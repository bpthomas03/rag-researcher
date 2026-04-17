from __future__ import annotations

import json
import math
from collections import deque
from pathlib import Path
from typing import Any, Literal

import httpx

from corpus_builder.models import CrawlConfig, CrawlSettings, WorkRecord
from corpus_builder.openalex import (
    OpenAlexClient,
    seed_identifiers_from_discovery_rows,
    work_from_openalex_payload,
)


def _title_gate_ok(
    rid: str,
    summaries: dict[str, dict[str, Any]],
    needles: list[str],
) -> bool:
    if not needles:
        return True
    title = (summaries.get(rid) or {}).get("title") or ""
    tl = title.lower()
    return any(n in tl for n in needles)


def _work_matches_expansion_gate(work: WorkRecord, keywords: list[str]) -> bool:
    needles = [k.strip().lower() for k in keywords if isinstance(k, str) and k.strip()]
    if not needles:
        return True
    hay = f"{(work.title or '').lower()} {(work.abstract or '').lower()}"
    return any(n in hay for n in needles)


def _score_reference(
    rid: str,
    summaries: dict[str, dict[str, Any]],
    search_scores: dict[str, float],
    settings: CrawlSettings,
) -> float:
    s = summaries.get(rid) or {}
    hay = f"{(s.get('title') or '').lower()} {(s.get('abstract') or '').lower()}"
    kw_hits = 0
    for k in settings.reference_keywords:
        kk = k.strip().lower()
        if kk and kk in hay:
            kw_hits += 1
    cites = int(s.get("cited_by_count") or 0)
    sr = float(search_scores.get(rid, 0.0))
    return (
        settings.reference_keyword_weight * kw_hits
        + settings.reference_citation_weight * math.log1p(cites)
        + settings.reference_openalex_search_weight * sr
    )


async def _work_record_from_openalex_raw(
    oa: OpenAlexClient,
    client: httpx.AsyncClient,
    raw: dict[str, Any],
    settings: CrawlSettings,
) -> WorkRecord:
    merged = await oa.referenced_work_ids_with_crossref_fallback(
        client,
        raw,
        enabled=settings.crossref_references_fallback,
        cap=settings.crossref_references_cap,
        resolve_batch_size=settings.reference_lookup_batch_size,
    )
    aug = dict(raw)
    aug["referenced_works"] = [f"https://openalex.org/{i}" for i in merged]
    return WorkRecord.model_validate(work_from_openalex_payload(aug))


async def _pick_ranked_work_ids(
    client: httpx.AsyncClient,
    oa: OpenAlexClient,
    candidate_ids: list[str],
    settings: CrawlSettings,
    *,
    max_pick: int,
    selection: Literal["scored", "top_cited", "openalex_order"] | None = None,
) -> list[str]:
    """Rank neighbor work ids (references or citing papers) using the same scoring knobs."""
    sel = selection or settings.reference_selection
    ref_batch = settings.reference_lookup_batch_size

    if sel == "openalex_order":
        return candidate_ids[:max_pick]

    cap = settings.reference_score_cap
    pool = candidate_ids[:cap] if len(candidate_ids) > cap else candidate_ids
    if not pool:
        return []

    if sel == "top_cited":
        if len(pool) <= max_pick:
            return pool
        counts = await oa.fetch_cited_by_counts(client, pool, ref_batch)
        return sorted(pool, key=lambda rid: (-counts.get(rid, 0), rid))[:max_pick]

    summaries = await oa.fetch_work_summaries_for_refs(client, pool, ref_batch)
    search_scores: dict[str, float] = {}
    if settings.reference_openalex_search_weight > 0 and (
        settings.reference_openalex_search and settings.reference_openalex_search.strip()
    ):
        search_scores = await oa.fetch_reference_openalex_search_scores(
            client,
            pool,
            settings.reference_openalex_search.strip(),
            ref_batch,
        )
    scored = [(rid, _score_reference(rid, summaries, search_scores, settings)) for rid in pool]
    scored.sort(key=lambda t: (-t[1], t[0]))
    needles = [
        x.strip().lower()
        for x in (settings.reference_title_gate_substrings or [])
        if isinstance(x, str) and x.strip()
    ]
    gated = [rid for rid, _ in scored if _title_gate_ok(rid, summaries, needles)]
    picked: list[str] = gated[:max_pick]
    if len(picked) < max_pick:
        for rid, _ in scored:
            if rid in picked:
                continue
            picked.append(rid)
            if len(picked) >= max_pick:
                break
    return picked


async def _pick_reference_ids(
    client: httpx.AsyncClient,
    oa: OpenAlexClient,
    all_refs: list[str],
    settings: CrawlSettings,
) -> list[str]:
    return await _pick_ranked_work_ids(
        client, oa, all_refs, settings, max_pick=settings.max_references_per_work
    )


def _forward_gate_keywords(settings: CrawlSettings) -> list[str]:
    if settings.forward_expansion_gate_keywords is not None:
        return settings.forward_expansion_gate_keywords
    return settings.reference_expansion_gate_keywords


async def crawl_forward(
    config: CrawlConfig,
    client: httpx.AsyncClient,
    oa: OpenAlexClient,
    records: dict[str, WorkRecord],
    edges: list[tuple[str, str]],
    *,
    seed_work_ids: set[str],
) -> dict[str, int]:
    """
    BFS on citing edges: for anchor work A, add works C with (C, A) meaning C cites A.
    Uses the same ranking as backward references; OpenAlex pool may be filtered by
    forward_title_and_abstract_phrase to reduce off-topic citers.
    """
    settings = config.crawl
    if not settings.forward_crawl:
        return {"new_works": 0, "new_edges": 0}

    n_before = len(records)
    edges_before = len(edges)
    seen_edges = set(edges)
    gate_kw = _forward_gate_keywords(settings)

    if settings.forward_from == "seeds":
        starters = {w for w in seed_work_ids if w in records}
    else:
        starters = {
            wid
            for wid, w in records.items()
            if not gate_kw or _work_matches_expansion_gate(w, gate_kw)
        }

    q: deque[tuple[str, int]] = deque((wid, 0) for wid in sorted(starters))
    forward_queued: set[str] = set(starters)

    while q and len(records) < settings.max_works:
        current_id, depth = q.popleft()
        if depth >= settings.forward_max_depth:
            continue
        current = records.get(current_id)
        if not current:
            continue
        if gate_kw and depth > settings.forward_expansion_bypass_depth:
            if not _work_matches_expansion_gate(current, gate_kw):
                continue

        try:
            pool = await oa.fetch_citing_work_ids(
                client,
                current_id,
                title_and_abstract_phrase=settings.forward_title_and_abstract_phrase,
                max_results=settings.forward_citing_pool_cap,
            )
        except ValueError:
            continue

        picked = await _pick_ranked_work_ids(
            client,
            oa,
            pool,
            settings,
            max_pick=settings.forward_max_per_work,
        )
        for citer_id in picked:
            if len(records) >= settings.max_works:
                break
            resolved = citer_id
            if citer_id not in records:
                try:
                    raw = await oa.fetch_work(client, citer_id)
                except httpx.HTTPStatusError as e:
                    if e.response.status_code == 404:
                        continue
                    raise
                oid = (raw.get("id") or "").rsplit("/", 1)[-1]
                if not oid:
                    continue
                if oid not in records:
                    records[oid] = await _work_record_from_openalex_raw(oa, client, raw, settings)
                resolved = oid
            edge = (resolved, current_id)
            if edge not in seen_edges:
                edges.append(edge)
                seen_edges.add(edge)
            if depth + 1 < settings.forward_max_depth and resolved not in forward_queued:
                forward_queued.add(resolved)
                q.append((resolved, depth + 1))

    return {
        "new_works": len(records) - n_before,
        "new_edges": len(edges) - edges_before,
    }


async def crawl_backward(
    config: CrawlConfig,
    client: httpx.AsyncClient,
    oa: OpenAlexClient,
    *,
    seeds: list[str],
) -> tuple[list[WorkRecord], list[tuple[str, str]], set[str]]:
    """
    BFS backward on references: edge (citing_work -> referenced_work).
    citing_work is the child in BFS (the paper whose bibliography we expand).
    """
    settings = config.crawl
    max_depth = settings.max_depth
    max_works = settings.max_works

    records: dict[str, WorkRecord] = {}
    edges: list[tuple[str, str]] = []

    q: deque[tuple[str, int]] = deque()
    seeds_enqueued: set[str] = set()

    for seed in seeds:
        raw = await oa.fetch_work(client, seed)
        wid = (raw.get("id") or "").rsplit("/", 1)[-1]
        if not wid:
            raise ValueError(f"Could not parse work id from seed {seed!r}")
        if wid not in records:
            records[wid] = await _work_record_from_openalex_raw(oa, client, raw, settings)
        if wid not in seeds_enqueued:
            seeds_enqueued.add(wid)
            q.append((wid, 0))

    while q and len(records) < max_works:
        current_id, depth = q.popleft()
        if depth >= max_depth:
            continue
        current = records.get(current_id)
        if not current:
            continue
        gate_kw = settings.reference_expansion_gate_keywords
        if (
            gate_kw
            and depth > settings.reference_expansion_bypass_depth
            and not _work_matches_expansion_gate(current, gate_kw)
        ):
            continue
        all_refs = list(dict.fromkeys(current.referenced_work_ids))
        ref_ids = await _pick_reference_ids(client, oa, all_refs, settings)
        for ref_id in ref_ids:
            edges.append((current_id, ref_id))
            if ref_id in records:
                continue
            if len(records) >= max_works:
                break
            try:
                raw = await oa.fetch_work(client, ref_id)
            except httpx.HTTPStatusError as e:
                if e.response.status_code == 404:
                    continue
                raise
            rid = (raw.get("id") or "").rsplit("/", 1)[-1]
            if not rid:
                continue
            if rid in records:
                continue
            records[rid] = await _work_record_from_openalex_raw(oa, client, raw, settings)
            q.append((rid, depth + 1))

    ordered = list(records.values())
    return ordered, edges, seeds_enqueued


def write_outputs(
    out_dir: Path,
    works: list[WorkRecord],
    edges: list[tuple[str, str]],
    *,
    meta: dict[str, Any] | None = None,
) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    graph: dict[str, Any] = {
        "nodes": [w.openalex_id for w in works],
        "edges": [{"from": a, "to": b, "kind": "references"} for a, b in edges],
    }
    if meta:
        graph["meta"] = meta
    (out_dir / "graph.json").write_text(json.dumps(graph, indent=2), encoding="utf-8")
    lines = [json.dumps(w.model_dump(), ensure_ascii=False) for w in works]
    (out_dir / "works.jsonl").write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")


async def run_crawl(config: CrawlConfig, out_dir: Path) -> dict[str, Any]:
    oa = OpenAlexClient(mailto=config.mailto, delay_ms=config.crawl.request_delay_ms)
    async with httpx.AsyncClient(timeout=60.0) as client:
        if config.seed_discovery is not None:
            discovered = await oa.discover_top_cited_works(client, config.seed_discovery)
            seeds = seed_identifiers_from_discovery_rows(discovered)
            meta = {
                "seed_discovery": config.seed_discovery.model_dump(mode="json"),
                "discovered_seed_works": discovered,
                "resolved_seed_identifiers": seeds,
            }
        else:
            seeds = list(config.seeds)
            meta = {"resolved_seed_identifiers": seeds}
        works, edges, seed_work_ids = await crawl_backward(config, client, oa, seeds=seeds)
        meta["seed_openalex_ids"] = sorted(seed_work_ids)
        if config.crawl.forward_crawl:
            records_map = {w.openalex_id: w for w in works}
            fw = await crawl_forward(
                config,
                client,
                oa,
                records_map,
                edges,
                seed_work_ids=seed_work_ids,
            )
            meta["forward_crawl"] = fw
            works = list(records_map.values())
    write_outputs(out_dir, works, edges, meta=meta)
    return {
        "works": len(works),
        "edges": len(edges),
        "out_dir": str(out_dir.resolve()),
        "seeds": len(seeds),
    }

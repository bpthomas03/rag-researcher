from __future__ import annotations

import asyncio
import math
import os
import re
import time
from datetime import date, timedelta
from typing import Any
from urllib.parse import quote

import httpx

from corpus_builder.models import SeedDiscovery

OPENALEX_BASE = "https://api.openalex.org"


def clean_doi(doi: str | None) -> str | None:
    """Strip publisher path junk OpenAlex sometimes attaches (e.g. …/pdf)."""
    if not doi:
        return None
    d = doi.replace("https://doi.org/", "").strip()
    for suffix in ("/pdf", "/abstract", "/full", "/meta"):
        if len(d) > len(suffix) and d.lower().endswith(suffix):
            d = d[: -len(suffix)]
    return d or None


def normalize_work_id(seed: str) -> str:
    """Return OpenAlex work id like W1234567890."""
    s = seed.strip()
    if m := re.search(r"(W\d{8,})", s, re.I):
        return m.group(1).upper()
    if s.lower().startswith("doi:"):
        s = "https://doi.org/" + s[4:].lstrip("/")
    if "doi.org/" in s:
        return s  # fetch by DOI URL
    if re.fullmatch(r"10\.\d{4,9}/\S+", s):
        c = clean_doi(s)
        if c:
            return f"https://doi.org/{c}"
    return s


def _invert_abstract(inv_index: dict[str, list[int]] | None) -> str | None:
    """OpenAlex stores inverted abstract; expand to plain text."""
    if not inv_index:
        return None
    positions: list[tuple[int, str]] = []
    for word, idxs in inv_index.items():
        for i in idxs:
            positions.append((i, word))
    positions.sort(key=lambda t: t[0])
    return " ".join(w for _, w in positions) if positions else None


def work_from_openalex_payload(data: dict[str, Any]) -> dict[str, Any]:
    oa_id = (data.get("id") or "").rsplit("/", 1)[-1]
    loc = data.get("primary_location") or {}
    pdf_url = (loc.get("pdf_url") or "").strip() or None
    landing = (loc.get("landing_page_url") or "").strip() or None
    doi = clean_doi((data.get("doi") or "").replace("https://doi.org/", ""))
    refs = data.get("referenced_works") or []
    ref_ids = [r.rsplit("/", 1)[-1] for r in refs if isinstance(r, str)]
    abstract = _invert_abstract(data.get("abstract_inverted_index"))
    return {
        "openalex_id": oa_id,
        "doi": doi,
        "title": data.get("display_name") or data.get("title"),
        "publication_year": data.get("publication_year"),
        "cited_by_count": data.get("cited_by_count"),
        "type": data.get("type"),
        "referenced_work_ids": ref_ids,
        "primary_location_pdf": pdf_url,
        "primary_location_landing": landing,
        "abstract": abstract,
    }


def _discovery_filter_parts(spec: SeedDiscovery) -> list[str]:
    start, end = _publication_window(spec)
    parts = [
        f"from_publication_date:{start.isoformat()}",
        f"to_publication_date:{end.isoformat()}",
    ]
    if spec.work_types:
        parts.append(f"type:{'|'.join(spec.work_types)}")
    phrase = spec.title_and_abstract_phrase.strip()
    if phrase:
        if "," in phrase:
            raise ValueError(
                "title_and_abstract_phrase must not contain commas (OpenAlex filter syntax)."
            )
        parts.append(f"title_and_abstract.search:{phrase}")
    return parts


def _dedupe_openalex_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[str] = set()
    out: list[dict[str, Any]] = []
    for row in rows:
        wid = (row.get("id") or "").rsplit("/", 1)[-1]
        if not wid or wid in seen:
            continue
        seen.add(wid)
        out.append(row)
    return out


def _filter_openalex_rows_by_title(
    rows: list[dict[str, Any]], substrings: list[str]
) -> list[dict[str, Any]]:
    if not substrings:
        return rows
    needles = [s.strip().lower() for s in substrings if s.strip()]
    if not needles:
        return rows
    kept: list[dict[str, Any]] = []
    for row in rows:
        title = (row.get("display_name") or row.get("title") or "").lower()
        if any(n in title for n in needles):
            kept.append(row)
    return kept


def _publication_window(spec: SeedDiscovery) -> tuple[date, date]:
    end = spec.to_publication_date or date.today()
    if spec.from_publication_date is not None:
        start = spec.from_publication_date
    else:
        days = max(1, int(round(spec.years_back * 365.25)))
        start = end - timedelta(days=days)
    if start > end:
        raise ValueError("from_publication_date must be on or before to_publication_date")
    return start, end


class OpenAlexClient:
    def __init__(self, mailto: str | None, delay_ms: int) -> None:
        mail = mailto or os.environ.get("OPENALEX_MAILTO", "")
        ua = "corpus-builder/0.1 (https://github.com/)"
        if mail:
            ua = f"{ua} mailto:{mail}"
        self._headers = {"User-Agent": ua}
        self._delay_ms = delay_ms
        self._last_request = 0.0

    async def _pace(self) -> None:
        if self._delay_ms <= 0:
            return
        now = time.monotonic()
        wait = self._delay_ms / 1000.0 - (now - self._last_request)
        if wait > 0:
            await asyncio.sleep(wait)
        self._last_request = time.monotonic()

    async def fetch_work(self, client: httpx.AsyncClient, seed: str) -> dict[str, Any]:
        await self._pace()
        wid = normalize_work_id(seed)
        url = f"{OPENALEX_BASE}/works/{wid}"
        r = await client.get(url, headers=self._headers, follow_redirects=True)
        if r.status_code == 404 and "doi.org/" in wid.lower():
            await self._pace()
            doi = wid.lower().split("doi.org/", 1)[-1].strip("/")
            r2 = await client.get(
                f"{OPENALEX_BASE}/works",
                params={"filter": f"doi:{doi}", "per-page": 1},
                headers=self._headers,
                follow_redirects=True,
            )
            r2.raise_for_status()
            payload = r2.json()
            count = int((payload.get("meta") or {}).get("count") or 0)
            if count == 0:
                raise ValueError(
                    f"No OpenAlex work for DOI {doi!r}. Try another DOI, an OpenAlex ID (W…), "
                    "or discover IDs via https://api.openalex.org/works?search=…"
                )
            results = payload.get("results") or []
            data = results[0]
        else:
            r.raise_for_status()
            data = r.json()
        if data.get("id") is None:
            raise ValueError(f"No OpenAlex work resolved for seed: {seed!r}")
        return data

    async def referenced_work_ids_with_crossref_fallback(
        self,
        client: httpx.AsyncClient,
        raw_work: dict[str, Any],
        *,
        enabled: bool,
        cap: int,
        resolve_batch_size: int,
    ) -> list[str]:
        """
        Return OpenAlex work ids for references. If OpenAlex lists none but the work has a DOI,
        optionally pull reference DOIs from Crossref and resolve them to OpenAlex ids.
        """
        refs = raw_work.get("referenced_works") or []
        ids = [r.rsplit("/", 1)[-1] for r in refs if isinstance(r, str)]
        ids = list(dict.fromkeys(ids))
        if ids or not enabled:
            return ids
        doi = clean_doi((raw_work.get("doi") or "").replace("https://doi.org/", ""))
        if not doi:
            return []
        ref_dois = await self._fetch_crossref_reference_dois(client, doi, cap)
        if not ref_dois:
            return []
        return await self._resolve_dois_to_openalex_work_ids(client, ref_dois, resolve_batch_size)

    async def _fetch_crossref_reference_dois(
        self, client: httpx.AsyncClient, doi: str, cap: int
    ) -> list[str]:
        await self._pace()
        url = f"https://api.crossref.org/works/{quote(doi, safe='')}"
        r = await client.get(url, headers=self._headers, follow_redirects=True)
        if r.status_code == 404:
            return []
        r.raise_for_status()
        msg = (r.json() or {}).get("message") or {}
        out: list[str] = []
        seen: set[str] = set()
        for ref in msg.get("reference") or []:
            if not isinstance(ref, dict):
                continue
            d = ref.get("DOI")
            if not isinstance(d, str) or not d.strip():
                continue
            key = d.strip().lower()
            if key in seen:
                continue
            seen.add(key)
            out.append(key)
            if len(out) >= cap:
                break
        return out

    async def _resolve_dois_to_openalex_work_ids(
        self,
        client: httpx.AsyncClient,
        dois: list[str],
        batch_size: int,
    ) -> list[str]:
        """Resolve Crossref DOIs to OpenAlex work ids; preserve order; skip unknown DOIs."""
        out: list[str] = []
        seen: set[str] = set()
        bs = max(1, min(batch_size, 25))
        for i in range(0, len(dois), bs):
            chunk = dois[i : i + bs]
            filt = "|".join(chunk)
            await self._pace()
            r = await client.get(
                f"{OPENALEX_BASE}/works",
                params={
                    "filter": f"doi:{filt}",
                    "per-page": min(200, max(len(chunk), 1)),
                    "select": "id,doi",
                },
                headers=self._headers,
                follow_redirects=True,
            )
            r.raise_for_status()
            by_doi: dict[str, str] = {}
            for row in r.json().get("results") or []:
                d = clean_doi((row.get("doi") or "").replace("https://doi.org/", ""))
                wid = (row.get("id") or "").rsplit("/", 1)[-1]
                if d and wid:
                    by_doi[d.lower()] = wid
            for d in chunk:
                wid = by_doi.get(d.lower())
                if wid and wid not in seen:
                    seen.add(wid)
                    out.append(wid)
        return out

    async def fetch_cited_by_counts(
        self,
        client: httpx.AsyncClient,
        work_ids: list[str],
        batch_size: int,
    ) -> dict[str, int]:
        """Map OpenAlex work id -> cited_by_count (missing ids -> 0)."""
        unique = list(dict.fromkeys(w for w in work_ids if w))
        out: dict[str, int] = dict.fromkeys(unique, 0)
        if not unique:
            return out

        for i in range(0, len(unique), batch_size):
            chunk = unique[i : i + batch_size]
            filt = "|".join(chunk)
            await self._pace()
            r = await client.get(
                f"{OPENALEX_BASE}/works",
                params={
                    "filter": f"openalex:{filt}",
                    "per-page": min(200, max(len(chunk), 1)),
                    "select": "id,cited_by_count",
                },
                headers=self._headers,
                follow_redirects=True,
            )
            r.raise_for_status()
            payload = r.json()
            for row in payload.get("results") or []:
                wid = (row.get("id") or "").rsplit("/", 1)[-1]
                if not wid:
                    continue
                try:
                    c = int(row.get("cited_by_count") or 0)
                except (TypeError, ValueError):
                    c = 0
                out[wid] = c
        return out

    async def fetch_work_summaries_for_refs(
        self,
        client: httpx.AsyncClient,
        work_ids: list[str],
        batch_size: int,
    ) -> dict[str, dict[str, Any]]:
        """Minimal fields for scoring: title, abstract text, cited_by_count."""
        unique = list(dict.fromkeys(w for w in work_ids if w))
        out: dict[str, dict[str, Any]] = {}
        for i in range(0, len(unique), batch_size):
            chunk = unique[i : i + batch_size]
            filt = "|".join(chunk)
            await self._pace()
            r = await client.get(
                f"{OPENALEX_BASE}/works",
                params={
                    "filter": f"openalex:{filt}",
                    "per-page": min(200, max(len(chunk), 1)),
                    "select": "id,display_name,cited_by_count,abstract_inverted_index",
                },
                headers=self._headers,
                follow_redirects=True,
            )
            r.raise_for_status()
            for row in r.json().get("results") or []:
                wid = (row.get("id") or "").rsplit("/", 1)[-1]
                if not wid:
                    continue
                try:
                    cites = int(row.get("cited_by_count") or 0)
                except (TypeError, ValueError):
                    cites = 0
                abstract = _invert_abstract(row.get("abstract_inverted_index")) or ""
                title = (row.get("display_name") or row.get("title") or "") or ""
                out[wid] = {"title": title, "abstract": abstract, "cited_by_count": cites}
        return out

    async def fetch_reference_openalex_search_scores(
        self,
        client: httpx.AsyncClient,
        work_ids: list[str],
        search_query: str,
        batch_size: int,
    ) -> dict[str, float]:
        """
        For each reference id, a score in (0, 1] from OpenAlex BM25 ranking within its batch;
        ids missing from a batch's hit list get 0.0. Best hit rank wins across overlapping chunks
        (should not overlap if work_ids are unique).
        """
        q = search_query.strip()
        if not q:
            return {}
        unique = list(dict.fromkeys(w for w in work_ids if w))
        scores: dict[str, float] = dict.fromkeys(unique, 0.0)
        for i in range(0, len(unique), batch_size):
            chunk = unique[i : i + batch_size]
            filt = "|".join(chunk)
            await self._pace()
            r = await client.get(
                f"{OPENALEX_BASE}/works",
                params={
                    "filter": f"openalex:{filt}",
                    "search": q,
                    "per-page": min(200, max(len(chunk), 1)),
                    "select": "id",
                },
                headers=self._headers,
                follow_redirects=True,
            )
            r.raise_for_status()
            results = r.json().get("results") or []
            n = len(results)
            if n == 0:
                continue
            for idx, row in enumerate(results):
                wid = (row.get("id") or "").rsplit("/", 1)[-1]
                if not wid:
                    continue
                rank_score = (n - idx) / float(n)
                scores[wid] = max(scores[wid], rank_score)
        return scores

    async def fetch_citing_work_ids(
        self,
        client: httpx.AsyncClient,
        cited_openalex_id: str,
        *,
        title_and_abstract_phrase: str | None,
        max_results: int,
    ) -> list[str]:
        """
        Works that cite ``cited_openalex_id`` (OpenAlex id like W123…), newest / highest-cited first.
        Optionally AND a title_and_abstract.search on the citing side to stay in-field.
        """
        wid = cited_openalex_id.strip().upper()
        if not wid.startswith("W"):
            raise ValueError(f"Expected OpenAlex work id W…, got {cited_openalex_id!r}")
        parts = [f"cites:{wid}"]
        if title_and_abstract_phrase and title_and_abstract_phrase.strip():
            phrase = title_and_abstract_phrase.strip()
            if "," in phrase:
                raise ValueError("title_and_abstract_phrase must not contain commas (OpenAlex filter syntax).")
            parts.append(f"title_and_abstract.search:{phrase}")
        filt = ",".join(parts)
        out: list[str] = []
        seen: set[str] = set()
        page = 1
        per = min(200, max(1, max_results))
        while len(out) < max_results:
            await self._pace()
            r = await client.get(
                f"{OPENALEX_BASE}/works",
                params={
                    "filter": filt,
                    "sort": "cited_by_count:desc",
                    "per-page": per,
                    "page": page,
                    "select": "id",
                },
                headers=self._headers,
                follow_redirects=True,
            )
            r.raise_for_status()
            payload = r.json()
            results = payload.get("results") or []
            if not results:
                break
            for row in results:
                oid = (row.get("id") or "").rsplit("/", 1)[-1]
                if oid and oid not in seen:
                    seen.add(oid)
                    out.append(oid)
                    if len(out) >= max_results:
                        break
            if len(results) < per:
                break
            meta = payload.get("meta") or {}
            total = int(meta.get("count") or 0)
            if page * per >= total:
                break
            page += 1
            if page > 500:
                break
        return out

    async def discover_top_cited_works(
        self, client: httpx.AsyncClient, spec: SeedDiscovery
    ) -> list[dict[str, Any]]:
        """
        Discover seed works: either pure top-cited in a filtered corpus, or a relevance pool
        re-ranked by citations with an optional title gate (see SeedDiscovery.discovery_mode).
        """
        filter_str = ",".join(_discovery_filter_parts(spec))

        if spec.discovery_mode == "top_cited_in_filters":
            params: dict[str, str | int] = {
                "filter": filter_str,
                "sort": "cited_by_count:desc",
                "per-page": spec.limit,
            }
            if spec.extra_search:
                params["search"] = spec.extra_search
        else:
            search_q = (spec.extra_search or spec.relevance_search).strip()
            if not search_q:
                raise ValueError(
                    "search_relevance_then_top_cited requires a non-empty relevance_search "
                    "(or extra_search)."
                )
            params = {
                "filter": filter_str,
                "search": search_q,
                "per-page": spec.candidate_pool,
            }

        await self._pace()
        r = await client.get(
            f"{OPENALEX_BASE}/works",
            params=params,
            headers=self._headers,
            follow_redirects=True,
        )
        r.raise_for_status()
        rows: list[dict[str, Any]] = _dedupe_openalex_rows(list(r.json().get("results") or []))

        rows = _filter_openalex_rows_by_title(rows, spec.require_title_substrings)

        if spec.discovery_mode == "search_relevance_then_top_cited":
            rows.sort(
                key=lambda row: (
                    -(int(row.get("cited_by_count") or 0)),
                    str(row.get("id") or ""),
                )
            )
            rows = rows[: spec.limit]
        else:
            rows = rows[: spec.limit]

        out: list[dict[str, Any]] = []
        for row in rows:
            oa_id = (row.get("id") or "").rsplit("/", 1)[-1]
            doi = clean_doi((row.get("doi") or "").replace("https://doi.org/", "").strip())
            out.append(
                {
                    "openalex_id": oa_id,
                    "doi": doi,
                    "title": row.get("display_name") or row.get("title"),
                    "publication_year": row.get("publication_year"),
                    "cited_by_count": row.get("cited_by_count"),
                    "type": row.get("type"),
                }
            )
        if not out:
            raise ValueError(
                "seed_discovery returned no works after filters. Try: widen the date window, "
                "relax title_and_abstract_phrase, set discovery_mode to top_cited_in_filters, "
                "use require_title_substrings: [], or adjust relevance_search."
            )
        return out


def seed_identifiers_from_discovery_rows(rows: list[dict[str, Any]]) -> list[str]:
    ids: list[str] = []
    for row in rows:
        doi = row.get("doi")
        if isinstance(doi, str) and doi.strip():
            c = clean_doi(doi.strip())
            if c:
                ids.append(c)
                continue
        wid = row.get("openalex_id")
        if isinstance(wid, str) and wid.strip():
            ids.append(wid.strip())
    if not ids:
        raise ValueError("Discovery rows contained no DOI or OpenAlex id.")
    return ids

from __future__ import annotations

import asyncio
import json
import re
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any
from urllib.parse import quote_plus

import httpx

from corpus_builder.models import PdfManifestEntry


def _safe_url(url: str | None) -> str | None:
    if not isinstance(url, str):
        return None
    u = url.strip()
    if not u.startswith(("http://", "https://")):
        return None
    return u


def _extract_arxiv_id_from_text(text: str) -> str | None:
    t = text.strip()
    # Modern arXiv id: 2103.05230 or 2103.05230v2
    m = re.search(r"(\d{4}\.\d{4,5}(?:v\d+)?)", t, flags=re.I)
    if m:
        return m.group(1)
    # Legacy format: astro-ph/9701234
    m2 = re.search(r"([a-z\-]+\/\d{7}(?:v\d+)?)", t, flags=re.I)
    if m2:
        return m2.group(1)
    return None


def _extract_arxiv_id(row: dict[str, Any]) -> str | None:
    doi = row.get("doi")
    if isinstance(doi, str):
        # Common pattern: 10.48550/arXiv.2103.05230
        m = re.search(r"arxiv\.([A-Za-z0-9.\-\/]+)$", doi.strip(), flags=re.I)
        if m:
            cand = _extract_arxiv_id_from_text(m.group(1))
            if cand:
                return cand
    for key in ("primary_location_pdf", "primary_location_landing"):
        u = row.get(key)
        if not isinstance(u, str):
            continue
        if "arxiv.org" not in u.lower():
            continue
        # Try abs/<id> first, then generic id extraction fallback.
        m = re.search(r"/abs/([^/?#]+)", u, flags=re.I)
        if m:
            cand = _extract_arxiv_id_from_text(m.group(1))
            if cand:
                return cand
        cand = _extract_arxiv_id_from_text(u)
        if cand:
            return cand
    return None


def _pdf_candidates(row: dict[str, Any]) -> list[str]:
    out: list[str] = []
    for key in ("primary_location_pdf",):
        if u := _safe_url(row.get(key)):
            out.append(u)
    doi = row.get("doi")
    if isinstance(doi, str) and doi.strip():
        d = doi.strip().replace("https://doi.org/", "").lstrip("/")
        out.append(f"https://doi.org/{d}")
    for key in ("primary_location_landing",):
        if u := _safe_url(row.get(key)):
            out.append(u)
    if arx_id := _extract_arxiv_id(row):
        out.append(f"https://arxiv.org/pdf/{arx_id}.pdf")
        out.append(f"https://arxiv.org/abs/{arx_id}")
    seen: set[str] = set()
    dedup: list[str] = []
    for u in out:
        if u not in seen:
            seen.add(u)
            dedup.append(u)
    return dedup


def _unpaywall_best_oa_url(payload: dict[str, Any]) -> str | None:
    best = payload.get("best_oa_location") or {}
    for k in ("url_for_pdf", "url"):
        u = best.get(k)
        if isinstance(u, str) and u.startswith(("http://", "https://")):
            return u
    for loc in payload.get("oa_locations") or []:
        if not isinstance(loc, dict):
            continue
        for k in ("url_for_pdf", "url"):
            u = loc.get(k)
            if isinstance(u, str) and u.startswith(("http://", "https://")):
                return u
    return None


async def _unpaywall_url_for_doi(
    client: httpx.AsyncClient, doi: str | None, mailto: str, timeout_s: float
) -> str | None:
    if not isinstance(doi, str) or not doi.strip():
        return None
    d = doi.strip().replace("https://doi.org/", "").lstrip("/")
    if not d:
        return None
    url = f"https://api.unpaywall.org/v2/{quote_plus(d)}?email={quote_plus(mailto)}"
    try:
        r = await client.get(url, follow_redirects=True, timeout=timeout_s)
        if r.status_code >= 400:
            return None
        return _unpaywall_best_oa_url(r.json() or {})
    except Exception:
        return None


def _semantic_scholar_pdf_url(payload: dict[str, Any]) -> str | None:
    oa = payload.get("openAccessPdf") or {}
    u = oa.get("url")
    if isinstance(u, str) and u.startswith(("http://", "https://")):
        return u
    return None


async def _semantic_scholar_url_for_doi(
    client: httpx.AsyncClient, doi: str | None, timeout_s: float
) -> str | None:
    if not isinstance(doi, str) or not doi.strip():
        return None
    d = doi.strip().replace("https://doi.org/", "").lstrip("/")
    if not d:
        return None
    url = f"https://api.semanticscholar.org/graph/v1/paper/DOI:{quote_plus(d)}"
    params = {"fields": "openAccessPdf,title,externalIds"}
    try:
        r = await client.get(url, params=params, follow_redirects=True, timeout=timeout_s)
        if r.status_code >= 400:
            return None
        return _semantic_scholar_pdf_url(r.json() or {})
    except Exception:
        return None


def _crossref_best_url(message: dict[str, Any]) -> str | None:
    # Prefer explicit full-text links first.
    for link in message.get("link") or []:
        if not isinstance(link, dict):
            continue
        ctype = str(link.get("content-type") or "").lower()
        intent = str(link.get("intended-application") or "").lower()
        u = link.get("URL") or link.get("url")
        if isinstance(u, str) and u.startswith(("http://", "https://")):
            if "pdf" in ctype:
                return u
            if "text-mining" in intent:
                return u
    # Then license URLs can sometimes redirect to accepted manuscripts.
    for lic in message.get("license") or []:
        if not isinstance(lic, dict):
            continue
        u = lic.get("URL") or lic.get("url")
        if isinstance(u, str) and u.startswith(("http://", "https://")):
            return u
    return None


async def _crossref_url_for_doi(
    client: httpx.AsyncClient, doi: str | None, timeout_s: float
) -> str | None:
    if not isinstance(doi, str) or not doi.strip():
        return None
    d = doi.strip().replace("https://doi.org/", "").lstrip("/")
    if not d:
        return None
    url = f"https://api.crossref.org/works/{quote_plus(d)}"
    try:
        r = await client.get(url, follow_redirects=True, timeout=timeout_s)
        if r.status_code >= 400:
            return None
        msg = (r.json() or {}).get("message") or {}
        if not isinstance(msg, dict):
            return None
        return _crossref_best_url(msg)
    except Exception:
        return None


async def _openalex_enrichment_for_work(
    client: httpx.AsyncClient, openalex_id: str, timeout_s: float
) -> dict[str, Any]:
    oid = openalex_id.strip().upper()
    if not oid.startswith("W"):
        return []
    url = f"https://api.openalex.org/works/{oid}"
    params = {
        "select": "id,ids,best_oa_location,primary_location,locations",
    }
    try:
        r = await client.get(url, params=params, follow_redirects=True, timeout=timeout_s)
        if r.status_code >= 400:
            return {"urls": [], "title": None, "arxiv_id": None}
        data = r.json() or {}
    except Exception:
        return {"urls": [], "title": None, "arxiv_id": None}
    out: list[str] = []
    title = data.get("display_name") if isinstance(data.get("display_name"), str) else None
    arxiv_id: str | None = None
    ids = data.get("ids") or {}
    arxiv = ids.get("arxiv")
    if isinstance(arxiv, str) and arxiv.strip():
        aid = _extract_arxiv_id_from_text(arxiv)
        if aid:
            arxiv_id = aid
            out.append(f"https://arxiv.org/pdf/{aid}.pdf")
            out.append(f"https://arxiv.org/abs/{aid}")

    def add_loc(loc: dict[str, Any]) -> None:
        for k in ("pdf_url", "landing_page_url", "url_for_pdf", "url"):
            u = loc.get(k)
            if isinstance(u, str) and u.startswith(("http://", "https://")):
                out.append(u)

    bol = data.get("best_oa_location") or {}
    if isinstance(bol, dict):
        add_loc(bol)
    pl = data.get("primary_location") or {}
    if isinstance(pl, dict):
        add_loc(pl)
    for loc in data.get("locations") or []:
        if isinstance(loc, dict):
            add_loc(loc)

    seen: set[str] = set()
    dedup: list[str] = []
    for u in out:
        if u not in seen:
            seen.add(u)
            dedup.append(u)
    return {"urls": dedup, "title": title, "arxiv_id": arxiv_id}


def _looks_like_pdf(url: str, ctype: str | None) -> bool:
    if url.lower().endswith(".pdf"):
        return True
    if isinstance(ctype, str) and "pdf" in ctype.lower():
        return True
    return False


def _pdf_bytes_ok(content: bytes) -> bool:
    head = content[:1024].lstrip()
    return head.startswith(b"%PDF-")


def _extract_pdf_href(html: str, base_url: str) -> str | None:
    m = re.search(r'href=[\'"]([^\'"]+\.pdf(?:\?[^\'"]*)?)[\'"]', html, flags=re.I)
    if not m:
        return None
    href = m.group(1)
    if href.startswith(("http://", "https://")):
        return href
    if href.startswith("/"):
        p = httpx.URL(base_url)
        return f"{p.scheme}://{p.host}{href}"
    p = httpx.URL(base_url)
    base = str(p.copy_with(path="/"))
    return base + href.lstrip("/")


def _norm_title(text: str) -> str:
    return re.sub(r"[^a-z0-9 ]+", " ", text.lower()).strip()


def _token_overlap(a: str, b: str) -> float:
    sa = {t for t in _norm_title(a).split() if len(t) > 2}
    sb = {t for t in _norm_title(b).split() if len(t) > 2}
    if not sa or not sb:
        return 0.0
    return len(sa & sb) / float(max(len(sa), len(sb)))


def _arxiv_pdf_from_atom(xml_text: str, wanted_title: str) -> str | None:
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return None
    ns = {"a": "http://www.w3.org/2005/Atom"}
    best: tuple[float, str] | None = None
    for e in root.findall("a:entry", ns):
        t = (e.findtext("a:title", default="", namespaces=ns) or "").strip()
        i = (e.findtext("a:id", default="", namespaces=ns) or "").strip()
        if not t or "arxiv.org/abs/" not in i:
            continue
        score = _token_overlap(wanted_title, t)
        if best is None or score > best[0]:
            aid = i.rsplit("/", 1)[-1]
            best = (score, f"https://arxiv.org/pdf/{aid}.pdf")
    if best and best[0] >= 0.45:
        return best[1]
    return None


async def _arxiv_title_fallback(
    client: httpx.AsyncClient, title: str, timeout_s: float
) -> str | None:
    if not title.strip():
        return None
    q = " ".join(_norm_title(title).split()[:14]).strip()
    if not q:
        return None
    url = (
        "https://export.arxiv.org/api/query"
        f"?search_query=ti:{quote_plus(q)}&start=0&max_results=5"
    )
    try:
        r = await client.get(url, follow_redirects=True, timeout=timeout_s)
        r.raise_for_status()
    except Exception:
        return None
    return _arxiv_pdf_from_atom(r.text, title)


async def _download_one(
    client: httpx.AsyncClient,
    row: dict[str, Any],
    pdf_dir: Path,
    timeout_s: float,
    unpaywall_email: str,
) -> PdfManifestEntry:
    oid = str(row.get("openalex_id") or "").strip()
    doi = row.get("doi") if isinstance(row.get("doi"), str) else None
    title = row.get("title") if isinstance(row.get("title"), str) else None
    if not oid:
        return PdfManifestEntry(
            openalex_id="unknown",
            doi=doi,
            title=title,
            status="failed",
            reason="missing_openalex_id",
        )

    out_path = pdf_dir / f"{oid}.pdf"
    if out_path.is_file() and out_path.stat().st_size > 0:
        return PdfManifestEntry(
            openalex_id=oid,
            doi=doi,
            title=title,
            local_path=str(out_path),
            status="skipped",
            reason="already_exists",
        )

    candidates = _pdf_candidates(row)
    if not candidates:
        return PdfManifestEntry(
            openalex_id=oid,
            doi=doi,
            title=title,
            status="failed",
            reason="no_candidate_urls",
        )

    async def try_url(u: str) -> PdfManifestEntry | None:
        browser_headers = {
            "User-Agent": (
                "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
            ),
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        }
        last_err = False
        for _ in range(3):
            try:
                r = await client.get(
                    u, follow_redirects=True, timeout=timeout_s, headers=browser_headers
                )
                last_err = False
            except Exception:
                last_err = True
                await asyncio.sleep(0.25)
                continue
            ctype = r.headers.get("content-type")
            final_url = str(r.url)
            if _looks_like_pdf(final_url, ctype):
                if _pdf_bytes_ok(r.content):
                    out_path.write_bytes(r.content)
                    return PdfManifestEntry(
                        openalex_id=oid,
                        doi=doi,
                        title=title,
                        source_url=final_url,
                        local_path=str(out_path),
                        status="downloaded",
                    )
            if "html" in (ctype or "").lower():
                pdf_link = _extract_pdf_href(r.text, final_url)
                if pdf_link:
                    r2 = await client.get(pdf_link, follow_redirects=True, timeout=timeout_s)
                    if _looks_like_pdf(str(r2.url), r2.headers.get("content-type")) and _pdf_bytes_ok(
                        r2.content
                    ):
                        out_path.write_bytes(r2.content)
                        return PdfManifestEntry(
                            openalex_id=oid,
                            doi=doi,
                            title=title,
                            source_url=str(r2.url),
                            local_path=str(out_path),
                            status="downloaded",
                        )
            await asyncio.sleep(0.1)
        if last_err:
            return None
        return None

    for u in candidates:
        if got := await try_url(u):
            return got

    oa_enriched = await _openalex_enrichment_for_work(client, oid, timeout_s)

    # DOI-based OA resolvers: Unpaywall then Semantic Scholar.
    upw_u = await _unpaywall_url_for_doi(client, doi, unpaywall_email, timeout_s)
    if upw_u:
        if got := await try_url(upw_u):
            got.reason = "unpaywall_fallback"
            return got
    s2_u = await _semantic_scholar_url_for_doi(client, doi, timeout_s)
    if s2_u:
        if got := await try_url(s2_u):
            got.reason = "semantic_scholar_fallback"
            return got
    cr_u = await _crossref_url_for_doi(client, doi, timeout_s)
    if cr_u:
        if got := await try_url(cr_u):
            got.reason = "crossref_fallback"
            return got

    # OpenAlex enrichment fallback: use full location set + ids.arxiv for this work id.
    for u in oa_enriched.get("urls") or []:
        if got := await try_url(u):
            got.reason = "openalex_enrichment_fallback"
            return got

    # Final fallback: search arXiv by best available title (row title, then enriched OpenAlex title).
    title_for_arxiv = title
    if (not isinstance(title_for_arxiv, str) or not title_for_arxiv.strip()) and isinstance(
        oa_enriched.get("title"), str
    ):
        title_for_arxiv = oa_enriched["title"]
    if isinstance(title_for_arxiv, str) and title_for_arxiv.strip():
        pdf_u = await _arxiv_title_fallback(client, title_for_arxiv, timeout_s)
        if pdf_u:
            try:
                r3 = await client.get(pdf_u, follow_redirects=True, timeout=timeout_s)
                if _looks_like_pdf(str(r3.url), r3.headers.get("content-type")) and _pdf_bytes_ok(
                    r3.content
                ):
                    out_path.write_bytes(r3.content)
                    return PdfManifestEntry(
                        openalex_id=oid,
                        doi=doi,
                        title=title_for_arxiv,
                        source_url=str(r3.url),
                        local_path=str(out_path),
                        status="downloaded",
                        reason="arxiv_title_fallback",
                    )
            except Exception:
                pass

    return PdfManifestEntry(
        openalex_id=oid,
        doi=doi,
        title=title,
        status="failed",
        reason="all_candidates_failed",
    )


def _load_works(works_path: Path) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for line in works_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(row, dict):
            out.append(row)
    return out


async def download_pdfs_from_works(
    works_path: Path,
    pdf_dir: Path,
    manifest_path: Path,
    *,
    concurrency: int = 6,
    timeout_s: float = 35.0,
    unpaywall_email: str = "slsn-rag@example.com",
) -> dict[str, int]:
    pdf_dir.mkdir(parents=True, exist_ok=True)
    works = _load_works(works_path)
    sem = asyncio.Semaphore(max(1, concurrency))

    async with httpx.AsyncClient(headers={"User-Agent": "rag-research/0.1"}) as client:
        async def one(row: dict[str, Any]) -> PdfManifestEntry:
            async with sem:
                return await _download_one(client, row, pdf_dir, timeout_s, unpaywall_email)

        entries = await asyncio.gather(*(one(w) for w in works))

    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    lines = [json.dumps(e.model_dump(mode="json"), ensure_ascii=False) for e in entries]
    manifest_path.write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")

    summary = {"total": len(entries), "downloaded": 0, "skipped": 0, "failed": 0}
    for e in entries:
        summary[e.status] = int(summary.get(e.status, 0)) + 1
    return summary

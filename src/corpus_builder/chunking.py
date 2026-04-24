from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from pypdf import PdfReader

from corpus_builder.models import ChunkRecord


def _clean_text(text: str) -> str:
    t = text.replace("\x00", " ")
    t = re.sub(r"[ \t]+", " ", t)
    t = re.sub(r"\n{3,}", "\n\n", t)
    return t.strip()


def _chunk_text(text: str, chunk_size: int, overlap: int) -> list[str]:
    if not text:
        return []
    step = max(1, chunk_size - overlap)
    out: list[str] = []
    i = 0
    n = len(text)
    while i < n:
        chunk = text[i : i + chunk_size].strip()
        if chunk:
            out.append(chunk)
        i += step
    return out


def _works_by_id(works_path: Path) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    if not works_path.is_file():
        return out
    for line in works_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        oid = row.get("openalex_id")
        if isinstance(oid, str) and oid:
            out[oid] = row
    return out


def _extract_pdf_text(pdf_path: Path) -> str:
    reader = PdfReader(str(pdf_path))
    pages: list[str] = []
    for p in reader.pages:
        pages.append(p.extract_text() or "")
    return _clean_text("\n\n".join(pages))


def build_chunks(
    pdf_dir: Path,
    works_path: Path,
    out_chunks_path: Path,
    *,
    chunk_size: int = 1100,
    overlap: int = 180,
) -> dict[str, int]:
    works = _works_by_id(works_path)
    out_chunks_path.parent.mkdir(parents=True, exist_ok=True)

    chunks: list[ChunkRecord] = []
    pdf_files = sorted(pdf_dir.glob("*.pdf"))
    failed = 0
    for pdf in pdf_files:
        oid = pdf.stem
        meta = works.get(oid) or {}
        try:
            text = _extract_pdf_text(pdf)
        except Exception:
            failed += 1
            continue
        for idx, chunk in enumerate(_chunk_text(text, chunk_size=chunk_size, overlap=overlap)):
            chunks.append(
                ChunkRecord(
                    chunk_id=f"{oid}:{idx}",
                    openalex_id=oid,
                    doi=meta.get("doi") if isinstance(meta.get("doi"), str) else None,
                    title=meta.get("title") if isinstance(meta.get("title"), str) else None,
                    source_path=str(pdf),
                    chunk_index=idx,
                    text=chunk,
                )
            )

    lines = [json.dumps(c.model_dump(mode="json"), ensure_ascii=False) for c in chunks]
    out_chunks_path.write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")
    return {"pdf_total": len(pdf_files), "pdf_failed": failed, "chunks": len(chunks)}

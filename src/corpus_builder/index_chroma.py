from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import chromadb
import httpx


def _load_chunks(chunks_path: Path) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for line in chunks_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(row, dict) and isinstance(row.get("chunk_id"), str):
            out.append(row)
    return out


async def _embed_one(client: httpx.AsyncClient, base_url: str, model: str, text: str) -> list[float]:
    # Try modern endpoint first.
    r = await client.post(f"{base_url}/api/embed", json={"model": model, "input": text})
    if r.status_code < 300:
        js = r.json()
        emb = js.get("embeddings") or []
        if emb and isinstance(emb[0], list):
            return emb[0]
    # Fallback endpoint.
    r2 = await client.post(f"{base_url}/api/embeddings", json={"model": model, "prompt": text})
    r2.raise_for_status()
    js2 = r2.json()
    return js2.get("embedding") or []


async def index_chunks_chroma(
    chunks_path: Path,
    chroma_dir: Path,
    *,
    collection_name: str = "slsn_chunks",
    ollama_base_url: str = "http://localhost:11434",
    embedding_model: str = "nomic-embed-text",
    batch_size: int = 32,
) -> dict[str, int]:
    chroma_dir.mkdir(parents=True, exist_ok=True)
    client = chromadb.PersistentClient(path=str(chroma_dir))
    col = client.get_or_create_collection(name=collection_name)

    chunks = _load_chunks(chunks_path)
    total = len(chunks)
    upserted = 0
    failed = 0

    async with httpx.AsyncClient(timeout=90.0) as h:
        for i in range(0, total, max(1, batch_size)):
            batch = chunks[i : i + batch_size]
            ids: list[str] = []
            docs: list[str] = []
            metas: list[dict[str, Any]] = []
            embs: list[list[float]] = []
            for row in batch:
                txt = row.get("text") or ""
                if not isinstance(txt, str) or not txt.strip():
                    failed += 1
                    continue
                try:
                    emb = await _embed_one(h, ollama_base_url, embedding_model, txt)
                except Exception:
                    failed += 1
                    continue
                ids.append(str(row["chunk_id"]))
                docs.append(txt)
                metas.append(
                    {
                        "openalex_id": row.get("openalex_id"),
                        "doi": row.get("doi"),
                        "title": row.get("title"),
                        "source_path": row.get("source_path"),
                        "chunk_index": int(row.get("chunk_index") or 0),
                    }
                )
                embs.append(emb)
            if ids:
                col.upsert(ids=ids, documents=docs, metadatas=metas, embeddings=embs)
                upserted += len(ids)

    return {"total_chunks": total, "upserted": upserted, "failed": failed}

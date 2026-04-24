from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

import chromadb
import httpx


@dataclass
class RagCitation:
    openalex_id: str | None
    title: str | None
    doi: str | None
    source_path: str | None
    snippet: str


@dataclass
class RagResult:
    answer: str
    citations: list[RagCitation]


async def _embed_query(client: httpx.AsyncClient, base_url: str, model: str, text: str) -> list[float]:
    r = await client.post(f"{base_url}/api/embed", json={"model": model, "input": text})
    if r.status_code < 300:
        js = r.json()
        emb = js.get("embeddings") or []
        if emb and isinstance(emb[0], list):
            return emb[0]
    r2 = await client.post(f"{base_url}/api/embeddings", json={"model": model, "prompt": text})
    r2.raise_for_status()
    return r2.json().get("embedding") or []


def _build_prompt(question: str, contexts: list[dict[str, Any]]) -> str:
    blocks: list[str] = []
    for i, c in enumerate(contexts, start=1):
        title = c.get("title") or "Unknown title"
        oid = c.get("openalex_id") or "unknown"
        txt = c.get("text") or ""
        blocks.append(f"[{i}] {title} ({oid})\n{txt}")
    context_text = "\n\n".join(blocks)
    return (
        "You are an SLSN domain expert assistant. Answer ONLY from the provided context. "
        "If context is insufficient, say so explicitly.\n\n"
        f"Question:\n{question}\n\n"
        f"Context:\n{context_text}\n\n"
        "Return a concise answer and mention citation numbers like [1], [2]."
    )


async def answer_question(
    question: str,
    *,
    chroma_dir: str,
    collection_name: str = "slsn_chunks",
    ollama_base_url: str = "http://localhost:11434",
    embedding_model: str = "nomic-embed-text",
    chat_model: str = "llama3.1:8b",
    top_k: int = 8,
) -> RagResult:
    chroma_client = chromadb.PersistentClient(path=chroma_dir)
    col = chroma_client.get_collection(collection_name)

    async with httpx.AsyncClient(timeout=120.0) as client:
        q_emb = await _embed_query(client, ollama_base_url, embedding_model, question)
        res = col.query(query_embeddings=[q_emb], n_results=max(1, top_k))
        docs = (res.get("documents") or [[]])[0]
        metas = (res.get("metadatas") or [[]])[0]
        contexts: list[dict[str, Any]] = []
        citations: list[RagCitation] = []
        for d, m in zip(docs, metas):
            md = m or {}
            contexts.append(
                {
                    "text": d,
                    "openalex_id": md.get("openalex_id"),
                    "title": md.get("title"),
                }
            )
            citations.append(
                RagCitation(
                    openalex_id=md.get("openalex_id"),
                    title=md.get("title"),
                    doi=md.get("doi"),
                    source_path=md.get("source_path"),
                    snippet=(d[:320] + "...") if isinstance(d, str) and len(d) > 320 else (d or ""),
                )
            )

        prompt = _build_prompt(question, contexts)
        chat_payload = {
            "model": chat_model,
            "stream": False,
            "messages": [{"role": "user", "content": prompt}],
        }
        r = await client.post(f"{ollama_base_url}/api/chat", json=chat_payload)
        r.raise_for_status()
        js = r.json()
        answer = ((js.get("message") or {}).get("content") or "").strip()
        if not answer:
            answer = "I could not generate an answer from the available context."
        return RagResult(answer=answer, citations=citations)


def rag_result_to_dict(result: RagResult) -> dict[str, Any]:
    return {
        "answer": result.answer,
        "citations": [
            {
                "openalex_id": c.openalex_id,
                "title": c.title,
                "doi": c.doi,
                "source_path": c.source_path,
                "snippet": c.snippet,
            }
            for c in result.citations
        ],
    }


def rag_result_to_json(result: RagResult) -> str:
    return json.dumps(rag_result_to_dict(result), ensure_ascii=False, indent=2)

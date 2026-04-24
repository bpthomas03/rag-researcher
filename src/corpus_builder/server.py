from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from corpus_builder.rag import answer_question, rag_result_to_dict


class AskPayload(BaseModel):
    question: str
    top_k: int = 8


def create_app(
    *,
    chroma_dir: str = "corpus/chroma",
    collection_name: str = "slsn_chunks",
    ollama_base_url: str = "http://localhost:11434",
    embedding_model: str = "nomic-embed-text",
    chat_model: str = "llama3.1:8b",
) -> FastAPI:
    app = FastAPI(title="SLSN RAG")
    web_dir = Path(__file__).resolve().parents[2] / "web"
    app.mount("/static", StaticFiles(directory=str(web_dir)), name="static")

    @app.get("/", response_class=HTMLResponse)
    async def index() -> str:
        return (web_dir / "index.html").read_text(encoding="utf-8")

    @app.post("/api/ask")
    async def ask(payload: AskPayload) -> dict:
        res = await answer_question(
            payload.question,
            chroma_dir=chroma_dir,
            collection_name=collection_name,
            ollama_base_url=ollama_base_url,
            embedding_model=embedding_model,
            chat_model=chat_model,
            top_k=payload.top_k,
        )
        return rag_result_to_dict(res)

    return app

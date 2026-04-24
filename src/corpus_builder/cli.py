from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

import yaml

from corpus_builder.crawl import run_crawl
from corpus_builder.models import CrawlConfig, SeedDiscovery


def load_config(path: Path) -> CrawlConfig:
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(data, dict):
        raise ValueError("Config must be a YAML mapping")
    mailto = data.get("mailto")
    seeds = list(data.get("seeds") or [])
    sd = data.get("seed_discovery")
    seed_discovery = SeedDiscovery.model_validate(sd) if sd is not None else None
    crawl = data.get("crawl") or {}
    return CrawlConfig(
        mailto=mailto,
        seeds=seeds,
        seed_discovery=seed_discovery,
        crawl=crawl,
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Citation corpus + local SLSN RAG toolkit")
    sub = parser.add_subparsers(dest="cmd")

    p_crawl = sub.add_parser("crawl", help="Run citation crawl")
    p_crawl.add_argument("-c", "--config", type=Path, default=Path("config/seeds.yaml"))
    p_crawl.add_argument("-o", "--out", type=Path, default=Path("out/corpus"))

    p_pdfs = sub.add_parser("pdfs", help="Download PDFs from works.jsonl")
    p_pdfs.add_argument("-w", "--works", type=Path, default=Path("out/corpus/works.jsonl"))
    p_pdfs.add_argument("-d", "--pdf-dir", type=Path, default=Path("corpus/pdfs"))
    p_pdfs.add_argument(
        "-m", "--manifest", type=Path, default=Path("corpus/manifests/pdf-manifest.jsonl")
    )
    p_pdfs.add_argument("--concurrency", type=int, default=6)
    p_pdfs.add_argument("--timeout", type=float, default=35.0)
    p_pdfs.add_argument("--unpaywall-email", default="slsn-rag@example.com")

    p_chunk = sub.add_parser("chunk", help="Parse PDFs and write chunks.jsonl")
    p_chunk.add_argument("-d", "--pdf-dir", type=Path, default=Path("corpus/pdfs"))
    p_chunk.add_argument("-w", "--works", type=Path, default=Path("out/corpus/works.jsonl"))
    p_chunk.add_argument("-o", "--out", type=Path, default=Path("corpus/chunks.jsonl"))
    p_chunk.add_argument("--chunk-size", type=int, default=1100)
    p_chunk.add_argument("--overlap", type=int, default=180)

    p_index = sub.add_parser("index", help="Embed chunks and index in Chroma")
    p_index.add_argument("-i", "--chunks", type=Path, default=Path("corpus/chunks.jsonl"))
    p_index.add_argument("--chroma-dir", type=Path, default=Path("corpus/chroma"))
    p_index.add_argument("--collection", default="slsn_chunks")
    p_index.add_argument("--ollama-url", default="http://localhost:11434")
    p_index.add_argument("--embedding-model", default="nomic-embed-text")
    p_index.add_argument("--batch-size", type=int, default=32)

    p_ask = sub.add_parser("ask", help="Ask a question against indexed corpus")
    p_ask.add_argument("question")
    p_ask.add_argument("--chroma-dir", default="corpus/chroma")
    p_ask.add_argument("--collection", default="slsn_chunks")
    p_ask.add_argument("--ollama-url", default="http://localhost:11434")
    p_ask.add_argument("--embedding-model", default="nomic-embed-text")
    p_ask.add_argument("--chat-model", default="llama3.1:8b")
    p_ask.add_argument("--top-k", type=int, default=8)

    p_serve = sub.add_parser("serve", help="Serve local web chat UI")
    p_serve.add_argument("--host", default="127.0.0.1")
    p_serve.add_argument("--port", type=int, default=8000)
    p_serve.add_argument("--chroma-dir", default="corpus/chroma")
    p_serve.add_argument("--collection", default="slsn_chunks")
    p_serve.add_argument("--ollama-url", default="http://localhost:11434")
    p_serve.add_argument("--embedding-model", default="nomic-embed-text")
    p_serve.add_argument("--chat-model", default="llama3.1:8b")

    p_all = sub.add_parser("build-all", help="Run crawl -> pdf -> chunk -> index")
    p_all.add_argument("-c", "--config", type=Path, default=Path("config/seeds.yaml"))
    p_all.add_argument("--crawl-out", type=Path, default=Path("out/corpus"))
    p_all.add_argument("--pdf-dir", type=Path, default=Path("corpus/pdfs"))
    p_all.add_argument("--manifest", type=Path, default=Path("corpus/manifests/pdf-manifest.jsonl"))
    p_all.add_argument("--chunks", type=Path, default=Path("corpus/chunks.jsonl"))
    p_all.add_argument("--chroma-dir", type=Path, default=Path("corpus/chroma"))
    p_all.add_argument("--collection", default="slsn_chunks")
    p_all.add_argument("--ollama-url", default="http://localhost:11434")
    p_all.add_argument("--embedding-model", default="nomic-embed-text")

    known_cmds = {"crawl", "pdfs", "chunk", "index", "ask", "serve", "build-all"}
    argv = sys.argv[1:]
    if argv and argv[0] not in known_cmds and argv[0].startswith("-") and argv[0] not in {
        "-h",
        "--help",
    }:
        argv = ["crawl", *argv]
    args = parser.parse_args(argv)
    cmd = args.cmd or "crawl"

    if cmd == "crawl":
        if not args.config.is_file():
            raise SystemExit(f"Config not found: {args.config}")
        cfg = load_config(args.config)
        summary = asyncio.run(run_crawl(cfg, args.out))
        print(
            f"Wrote {summary['works']} works, {summary['edges']} citation edges "
            f"({summary.get('seeds', 0)} seeds) to {summary['out_dir']}"
        )
        return

    if cmd == "pdfs":
        from corpus_builder.ingest_pdfs import download_pdfs_from_works

        summary = asyncio.run(
            download_pdfs_from_works(
                args.works,
                args.pdf_dir,
                args.manifest,
                concurrency=args.concurrency,
                timeout_s=args.timeout,
                unpaywall_email=args.unpaywall_email,
            )
        )
        print(json.dumps(summary, indent=2))
        return

    if cmd == "chunk":
        from corpus_builder.chunking import build_chunks

        summary = build_chunks(
            args.pdf_dir, args.works, args.out, chunk_size=args.chunk_size, overlap=args.overlap
        )
        print(json.dumps(summary, indent=2))
        return

    if cmd == "index":
        from corpus_builder.index_chroma import index_chunks_chroma

        summary = asyncio.run(
            index_chunks_chroma(
                args.chunks,
                args.chroma_dir,
                collection_name=args.collection,
                ollama_base_url=args.ollama_url,
                embedding_model=args.embedding_model,
                batch_size=args.batch_size,
            )
        )
        print(json.dumps(summary, indent=2))
        return

    if cmd == "ask":
        from corpus_builder.rag import answer_question, rag_result_to_json

        result = asyncio.run(
            answer_question(
                args.question,
                chroma_dir=args.chroma_dir,
                collection_name=args.collection,
                ollama_base_url=args.ollama_url,
                embedding_model=args.embedding_model,
                chat_model=args.chat_model,
                top_k=args.top_k,
            )
        )
        print(rag_result_to_json(result))
        return

    if cmd == "serve":
        import uvicorn

        from corpus_builder.server import create_app

        app = create_app(
            chroma_dir=args.chroma_dir,
            collection_name=args.collection,
            ollama_base_url=args.ollama_url,
            embedding_model=args.embedding_model,
            chat_model=args.chat_model,
        )
        uvicorn.run(app, host=args.host, port=args.port)
        return

    if cmd == "build-all":
        from corpus_builder.chunking import build_chunks
        from corpus_builder.index_chroma import index_chunks_chroma
        from corpus_builder.ingest_pdfs import download_pdfs_from_works

        if not args.config.is_file():
            raise SystemExit(f"Config not found: {args.config}")
        cfg = load_config(args.config)
        crawl_summary = asyncio.run(run_crawl(cfg, args.crawl_out))
        works_path = args.crawl_out / "works.jsonl"
        pdf_summary = asyncio.run(
            download_pdfs_from_works(
                works_path,
                args.pdf_dir,
                args.manifest,
                unpaywall_email="slsn-rag@example.com",
            )
        )
        chunk_summary = build_chunks(args.pdf_dir, works_path, args.chunks)
        index_summary = asyncio.run(
            index_chunks_chroma(
                args.chunks,
                args.chroma_dir,
                collection_name=args.collection,
                ollama_base_url=args.ollama_url,
                embedding_model=args.embedding_model,
            )
        )
        print(
            json.dumps(
                {
                    "crawl": crawl_summary,
                    "pdfs": pdf_summary,
                    "chunk": chunk_summary,
                    "index": index_summary,
                },
                indent=2,
            )
        )
        return

    parser.print_help()


if __name__ == "__main__":
    main()

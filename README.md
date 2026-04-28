# rag-research

End-to-end tooling for a **research RAG assistant** (framed here around **superluminous supernovae / SLSN**): build a **citation graph** from the literature, **inspect it visually**, **harvest PDFs**, **chunk and embed** text locally, then **chat** with grounded answers over your corpus—CLI or small **FastAPI** web UI.

The stack is **local-first**: [OpenAlex](https://openalex.org/) (+ optional [Crossref](https://www.crossref.org/) bibliography fallback) for metadata and graph expansion, [Ollama](https://ollama.com/) for embeddings and chat, [Chroma](https://www.trychroma.com/) for on-disk vector storage, [PyPDF](https://pypdf.readthedocs.io/) for text extraction.

---

## What this project does (big picture)

| Phase | What happens | Main outputs |
|--------|----------------|---------------|
| **1. Crawl** | Breadth-first expansion on **references** (backward), optional **forward** pass (papers that cite your graph), ranking and **node policy** to limit drift | `graph.json`, `works.jsonl` |
| **2. Visualize** | Static **vis-network** HTML: explore nodes and open papers in the browser | e.g. `viz/corpus-graph.html` |
| **3. PDFs** | Download PDFs per work (landing/DOI/arXiv, then **Unpaywall**, Semantic Scholar, Crossref, OpenAlex enrichment, arXiv title fallback) | `corpus/pdfs/*.pdf`, `pdf-manifest.jsonl` |
| **4. Chunk** | Extract text, split into overlapping segments, attach metadata from `works.jsonl` | `corpus/chunks.jsonl` |
| **5. Index** | Embed each chunk via Ollama, **upsert** into a persistent Chroma collection | `corpus/chroma/` (default) |
| **6. Query** | Retrieve top‑k chunks, build a **context-only** prompt, generate an answer + citations | `corpus-crawl ask` or `corpus-crawl serve` |

**Re-run chunking** when PDFs or chunking parameters change; **re-run indexing** when `chunks.jsonl` changes. **Querying** only reads Chroma—it does not re-embed the whole corpus.

---

## Features

### Citation graph

- **Manual seeds** (`DOI`, `https://doi.org/…`, or OpenAlex `W…`) or **seed discovery** (OpenAlex search + date window + filters).
- **Backward BFS** on references with ranked selection (`scored`, `top_cited`, `openalex_order`), keyword / citation / BM25-style scoring, and an **expansion gate** so survey or software hubs do not pull huge off-topic bibliographies.
- **Forward crawl** (optional): papers that **cite** your anchors, with the same ranking knobs and an optional OpenAlex `title_and_abstract.search` filter on the citing side.
- **Per-node policy**: `include_in_graph` vs `continue_traversal`, with weighted heuristics and optional **embedding similarity** rescue for borderline nodes.
- **Crossref bibliography fallback** when OpenAlex returns empty `referenced_works` for a work.

### Full-text & RAG

- **Resilient PDF acquisition** with a manifest for every work (downloaded / skipped / failed and reason).
- **Chunking** with configurable size and overlap; chunk IDs keyed by OpenAlex work id (PDF filename stem).
- **Chroma** persistent client: embeddings and metadata live on disk under `--chroma-dir`.
- **Retrieval + generation** in `rag.py`: embed the question, query Chroma, prompt the chat model as an **SLSN domain expert** answering **only from provided context** (with explicit “insufficient context” behavior).
- **FastAPI** app (`server.py`) serves `web/index.html` and `POST /api/ask` for a minimal browser UI.

---

## Requirements

- Python **3.11+**
- Network access for crawling and PDF resolution (OpenAlex, Crossref when enabled, publishers, Unpaywall, etc.).
- **`OPENALEX_MAILTO`** or `mailto:` in YAML set to a real address (OpenAlex [polite pool](https://docs.openalex.org/how-to-use-the-api/rate-limits-and-authentication#the-polite-pool)).
- For the RAG path: **Ollama** running locally, with embedding and chat models pulled (see below).

`corpus/` and `out/` are **gitignored** by default—PDFs, chunks, and Chroma stay on your machine.

---

## Install

```bash
cd rag-research
python -m venv .venv
source .venv/bin/activate   # Windows PowerShell: .\.venv\Scripts\Activate.ps1
pip install -e .
```

Entry points:

- **`corpus-crawl`** — `python -m corpus_builder` (subcommands below).
- **`corpus-viz`** — `python -m corpus_builder.viz` (graph HTML).

Use an explicit subcommand (e.g. `corpus-crawl crawl …`). For legacy convenience, flags-only invocations are rewritten to `crawl` (e.g. `corpus-crawl -c config/seeds.yaml -o out/corpus`).

---

## CLI quick reference

| Subcommand | Purpose |
|------------|--------|
| `crawl` | Run citation graph crawl from YAML config |
| `pdfs` | Download PDFs listed in `works.jsonl` |
| `chunk` | Read PDFs + `works.jsonl` → write `chunks.jsonl` |
| `index` | Embed chunks via Ollama → Chroma |
| `ask` | One-shot question; prints JSON (answer + citations) |
| `serve` | FastAPI + static UI on `http://127.0.0.1:8000` (default) |
| `build-all` | `crawl` → `pdfs` → `chunk` → `index` in one go |

Global defaults in the CLI point at `config/seeds.yaml`, `out/corpus/works.jsonl`, `corpus/pdfs`, `corpus/chunks.jsonl`, `corpus/chroma`, collection name `slsn_chunks`. Override with flags (`-h` on each subcommand).

---

## Configuration (YAML)

The crawl expects **exactly one** of:

1. **`seeds`** — list of DOIs / URLs / `W…` ids  
2. **`seed_discovery`** — OpenAlex-driven seeds (see `config/seeds.yaml`)

Top-level keys:

| Key | Purpose |
|-----|--------|
| `mailto` | Email for OpenAlex `User-Agent` (optional if `OPENALEX_MAILTO` is set). |
| `crawl` | `max_depth`, `max_works`, reference scoring, expansion gates, Crossref fallback, optional **forward** block, and `node_policy` thresholds/weights. |

Copy `config/seeds.example.yaml` to `config/seeds.yaml` and edit, or pass any path with `corpus-crawl crawl -c …`.

RAG defaults (Chroma path, Ollama URL, model names) are **CLI flags** for `chunk` / `index` / `ask` / `serve`; `seeds.example.yaml` includes an optional commented `rag:` block as documentation only—it is **not** parsed by the crawler.

---

## Citation graph: run a crawl

```bash
export OPENALEX_MAILTO='you@example.com'   # Windows PowerShell: $env:OPENALEX_MAILTO = 'you@example.com'
corpus-crawl crawl -c config/seeds.yaml -o out/corpus
# Shorthand: corpus-crawl -c config/seeds.yaml -o out/corpus
```

Outputs:

- `out/corpus/graph.json` — `nodes`, `edges` (`from` cites `to`), optional `meta`
- `out/corpus/works.jsonl` — one JSON object per line (`openalex_id`, `title`, `doi`, `primary_location_landing`, …)

`graph.json.meta.node_policy` includes settings + diagnostics (`evaluated`, `included`, `continued`, `rejected`, `top_rejection_reasons`, embedding rescue stats) for tuning.

### Include vs continue (node policy)

Node policy evaluates every newly seen paper and emits:

- **`include_in_graph`**: keep this node + edges in outputs.
- **`continue_traversal`**: whether BFS should expand neighbors from this node.

Key knobs (under `crawl.node_policy`):

- `include_threshold` / `continue_threshold`
- `depth_penalty_*`, `forward_direction_bonus`, `seed_anchor_bonus`
- `venue_*`, `keywords` / `keyword_weight`, `citation_weight`
- `embedding_*` for borderline rescue vs seed centroid

Practical defaults for balanced precision/recall:

1. Start with `include_threshold < continue_threshold` (e.g. `1.6` and `2.8`).
2. Keep `embedding_enabled: true` with modest `embedding_margin` (`0.4` to `0.7`).
3. Run a small smoke crawl (`max_works` 40–80), then inspect `meta.node_policy.stats`.
4. If drift is high: increase thresholds, lower `forward_direction_bonus`, tighten `keywords` and `forward_title_and_abstract_phrase`.
5. If recall is low: lower thresholds slightly and/or increase `embedding_alpha`.

---

## Graph HTML

```bash
corpus-viz -g out/corpus/graph.json -w out/corpus/works.jsonl -o viz/corpus-graph.html
# or: python -m corpus_builder.viz -g out/corpus/graph.json -w out/corpus/works.jsonl -o viz/corpus-graph.html
```

Open `viz/corpus-graph.html` in a browser. **Click a node** to open the paper in a new tab.

---

## Full-text pipeline & RAG (Ollama + Chroma)

### Prerequisites

- Install and run [Ollama](https://ollama.com/) locally (ensure `ollama` is on your `PATH`, or use the full path to `ollama.exe` on Windows after install).
- Pull models (example):

```bash
ollama pull nomic-embed-text
ollama pull llama3.1:8b
```

### Stages

**1) Crawl** (if you need fresh `works.jsonl`):

```bash
corpus-crawl crawl -c config/seeds.yaml -o out/corpus
```

**2) PDFs**

```bash
corpus-crawl pdfs -w out/corpus/works.jsonl -d corpus/pdfs -m corpus/manifests/pdf-manifest.jsonl
```

Not every work will yield a PDF (paywalls, missing OA links). RAG coverage is limited to **successfully downloaded** PDFs.

**3) Chunk**

```bash
corpus-crawl chunk -d corpus/pdfs -w out/corpus/works.jsonl -o corpus/chunks.jsonl
```

PyPDF may log warnings (e.g. duplicate `/Rotate`, odd XObject streams) on some publisher PDFs; check the printed summary (`pdf_failed`, `chunks`).

**4) Index** (embeddings stored under `--chroma-dir`; large corpora take a while—each chunk is embedded via Ollama):

```bash
corpus-crawl index -i corpus/chunks.jsonl --chroma-dir corpus/chroma --collection slsn_chunks --ollama-url http://localhost:11434 --embedding-model nomic-embed-text
```

**5) Ask (CLI)**

```bash
corpus-crawl ask "What are the leading SLSN powering mechanisms?" --chroma-dir corpus/chroma --collection slsn_chunks --ollama-url http://localhost:11434 --embedding-model nomic-embed-text --chat-model llama3.1:8b
```

**6) Serve (web UI)**

```bash
corpus-crawl serve --host 127.0.0.1 --port 8000 --chroma-dir corpus/chroma --collection slsn_chunks --ollama-url http://localhost:11434 --embedding-model nomic-embed-text --chat-model llama3.1:8b
```

Open `http://127.0.0.1:8000`.

### One-shot pipeline

```bash
corpus-crawl build-all -c config/seeds.yaml --crawl-out out/corpus --pdf-dir corpus/pdfs --chunks corpus/chunks.jsonl --chroma-dir corpus/chroma
```

---

## Optional reading in this repo

- **`advice.md`** — Design notes: why citation expansion, drift, paywalls, forward vs backward passes.
- **`evaluation-plan.md`** — Ideas for gold/anti-gold lists and parameter sweeps for crawl quality (scripts not yet in-tree).

---

## Project layout

```
config/           # seeds.example.yaml, seeds.yaml (local)
src/corpus_builder/
  __main__.py     # python -m corpus_builder
  cli.py          # crawl, pdfs, chunk, index, ask, serve, build-all
  crawl.py        # BFS, forward pass, graph + works outputs
  ingest_pdfs.py  # PDF downloader + manifest
  chunking.py     # PDF extraction + chunking
  index_chroma.py # Ollama embeddings + Chroma upsert
  rag.py          # retrieval + answer synthesis
  server.py       # FastAPI app
  models.py       # CrawlConfig / CrawlSettings (Pydantic)
  openalex.py     # OpenAlex + Crossref helpers
  viz.py          # HTML graph
web/
  index.html      # local chat UI (served by FastAPI)
advice.md
evaluation-plan.md
pyproject.toml
```

---

## Lint / format (optional)

[Ruff](https://docs.astral.sh/ruff/) is configured in `pyproject.toml` (`line-length = 100`). Install ruff separately if you want `ruff check src`.

---

## License

Specify your license here if you publish the repo.

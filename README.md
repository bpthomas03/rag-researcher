# rag-research

Python tooling to build a **citation-graph corpus** from [OpenAlex](https://openalex.org/) (with optional [Crossref](https://www.crossref.org/) bibliography fallback) for research RAG—backward references, optional forward “who cites this?” expansion, and a small **vis-network** HTML viewer.

## Features

- **Manual seeds** (`DOI`, `https://doi.org/…`, or OpenAlex `W…`) or **seed discovery** (OpenAlex search + date window + filters).
- **Backward BFS** on references with ranked selection (`scored`, `top_cited`, `openalex_order`), keyword / citation / BM25-style scoring, and an **expansion gate** so survey or software hubs do not pull huge off-topic bibliographies.
- **Forward crawl** (optional): papers that **cite** your anchors, using the same ranking knobs and an optional OpenAlex `title_and_abstract.search` filter on the citing side to limit drift.
- **Per-node policy engine**: each candidate gets two decisions, `include_in_graph` and `continue_traversal`, based on weighted heuristics plus optional embedding similarity for borderline nodes.
- **Outputs**: `graph.json` (nodes + edges) and `works.jsonl` (work metadata for labels and outbound links).
- **Viz**: static HTML graph; **click a node** to open the paper (landing page, DOI, or OpenAlex).
- **Local RAG pipeline**: download PDFs, chunk text, embed into Chroma, then ask questions via CLI or local web UI.

## Requirements

- Python **3.11+**
- Network access to OpenAlex (and Crossref when fallback is enabled).
- Set **`OPENALEX_MAILTO`** or `mailto:` in your YAML to a real address (OpenAlex [polite pool](https://docs.openalex.org/how-to-use-the-api/rate-limits-and-authentication#the-polite-pool)).

## Install

```bash
cd rag-research
python -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -e .
```

Entry points (after install):

- `corpus-crawl` — same as `python -m corpus_builder`
- `corpus-viz` — same as `python -m corpus_builder.viz`

## Configuration

YAML config supports **exactly one** of:

1. **`seeds`** — list of DOIs / URLs / `W…` ids  
2. **`seed_discovery`** — OpenAlex-driven seeds (see `config/seeds.yaml`)

Top-level keys:

| Key | Purpose |
|-----|--------|
| `mailto` | Email for OpenAlex `User-Agent` (optional if `OPENALEX_MAILTO` is set). |
| `crawl` | `max_depth`, `max_works`, reference scoring, expansion gates, Crossref fallback, optional **forward** block, and `node_policy` thresholds/weights (`include` vs `continue`). |

Copy `config/seeds.example.yaml` to `config/seeds.yaml` and edit, or pass any path with `-c`.

## Run a crawl

```bash
export OPENALEX_MAILTO='you@example.com'
corpus-crawl -c config/seeds.yaml -o out/corpus
# or: python -m corpus_builder -c config/seeds.yaml -o out/corpus
```

Outputs:

- `out/corpus/graph.json` — `nodes`, `edges` (`from` cites `to`), optional `meta`
- `out/corpus/works.jsonl` — one JSON object per line (`openalex_id`, `title`, `doi`, `primary_location_landing`, …)

`graph.json.meta.node_policy` includes settings + diagnostics (`evaluated`, `included`, `continued`, `rejected`, `top_rejection_reasons`, embedding rescue stats) for tuning.

## Include vs Continue Tuning

Node policy evaluates every newly seen paper and emits:

- `include_in_graph`: keep this node + edges in outputs.
- `continue_traversal`: whether BFS should expand neighbors from this node.

Key knobs (under `crawl.node_policy`):

- `include_threshold`: lower bound to keep a node.
- `continue_threshold`: higher bound to keep expanding through a node.
- `depth_penalty_*`: reduce score as graph distance grows.
- `forward_direction_bonus`: mild boost for citing-side traversal.
- `venue_*` + `keywords`/`keyword_weight`: fast relevance priors.
- `embedding_*`: borderline-only similarity rescue against seed centroid.

Practical defaults for balanced precision/recall:

1. Start with `include_threshold < continue_threshold` (e.g. `1.6` and `2.8`).
2. Keep `embedding_enabled: true` with modest `embedding_margin` (`0.4` to `0.7`).
3. Run a small smoke crawl (`max_works` 40-80), then inspect `meta.node_policy.stats`.
4. If drift is high: increase thresholds, lower `forward_direction_bonus`, tighten `keywords` and `forward_title_and_abstract_phrase`.
5. If recall is low: lower thresholds slightly and/or increase `embedding_alpha`.

## Build the graph HTML

```bash
corpus-viz -g out/corpus/graph.json -w out/corpus/works.jsonl -o viz/corpus-graph.html
# or: python -m corpus_builder.viz -g out/corpus/graph.json -w out/corpus/works.jsonl -o viz/corpus-graph.html
```

Open `viz/corpus-graph.html` in a browser. **Click a node** to open the paper in a new tab.

## Local-First RAG (Ollama + Chroma)

### Prerequisites

- Install and run [Ollama](https://ollama.com/) locally.
- Pull models (example):

```bash
ollama pull nomic-embed-text
ollama pull llama3.1:8b
```

### Stage commands

All commands below use the same CLI entrypoint with subcommands.

1) Crawl:

```bash
corpus-crawl crawl -c config/seeds.yaml -o out/corpus
```

2) Download PDFs from crawl output:

```bash
corpus-crawl pdfs -w out/corpus/works.jsonl -d corpus/pdfs -m corpus/manifests/pdf-manifest.jsonl
```

3) Chunk PDFs:

```bash
corpus-crawl chunk -d corpus/pdfs -w out/corpus/works.jsonl -o corpus/chunks.jsonl
```

4) Build Chroma index with local embeddings:

```bash
corpus-crawl index -i corpus/chunks.jsonl --chroma-dir corpus/chroma --collection slsn_chunks --ollama-url http://localhost:11434 --embedding-model nomic-embed-text
```

5) Ask a question (CLI):

```bash
corpus-crawl ask "What are the leading SLSN powering mechanisms?" --chroma-dir corpus/chroma --collection slsn_chunks --chat-model llama3.1:8b
```

6) Serve local web UI:

```bash
corpus-crawl serve --host 127.0.0.1 --port 8000 --chroma-dir corpus/chroma --collection slsn_chunks --chat-model llama3.1:8b
```

Then open `http://127.0.0.1:8000`.

### One-shot pipeline

```bash
corpus-crawl build-all -c config/seeds.yaml --crawl-out out/corpus --pdf-dir corpus/pdfs --chunks corpus/chunks.jsonl --chroma-dir corpus/chroma
```

## Project layout

```
config/           # example + local YAML (seeds.example.yaml, seeds.yaml)
src/corpus_builder/
  __main__.py     # python -m corpus_builder
  cli.py          # crawl + rag subcommands
  crawl.py        # BFS, forward pass, outputs
  ingest_pdfs.py  # PDF downloader + manifest
  chunking.py     # PDF extraction + chunking
  index_chroma.py # local embedding + vector indexing
  rag.py          # retrieval + answer synthesis
  server.py       # local FastAPI app
  models.py       # CrawlConfig / CrawlSettings (Pydantic)
  openalex.py     # OpenAlex + Crossref helpers
  viz.py          # HTML graph
web/
  index.html      # local chat UI
advice.md         # design notes (optional)
pyproject.toml
```

## Lint / format (optional)

[Ruff](https://docs.astral.sh/ruff/) is configured in `pyproject.toml` (`line-length = 100`). Install ruff separately if you want `ruff check src`.

## License

Specify your license here if you publish the repo.

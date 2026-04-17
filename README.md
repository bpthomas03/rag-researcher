# rag-research

Python tooling to build a **citation-graph corpus** from [OpenAlex](https://openalex.org/) (with optional [Crossref](https://www.crossref.org/) bibliography fallback) for research RAG—backward references, optional forward “who cites this?” expansion, and a small **vis-network** HTML viewer.

## Features

- **Manual seeds** (`DOI`, `https://doi.org/…`, or OpenAlex `W…`) or **seed discovery** (OpenAlex search + date window + filters).
- **Backward BFS** on references with ranked selection (`scored`, `top_cited`, `openalex_order`), keyword / citation / BM25-style scoring, and an **expansion gate** so survey or software hubs do not pull huge off-topic bibliographies.
- **Forward crawl** (optional): papers that **cite** your anchors, using the same ranking knobs and an optional OpenAlex `title_and_abstract.search` filter on the citing side to limit drift.
- **Outputs**: `graph.json` (nodes + edges) and `works.jsonl` (work metadata for labels and outbound links).
- **Viz**: static HTML graph; **click a node** to open the paper (landing page, DOI, or OpenAlex).

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
| `crawl` | `max_depth`, `max_works`, reference scoring, expansion gates, Crossref fallback, optional **forward** block (`forward_crawl`, `forward_max_depth`, `forward_title_and_abstract_phrase`, `forward_from`, …). |

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

## Build the graph HTML

```bash
corpus-viz -g out/corpus/graph.json -w out/corpus/works.jsonl -o viz/corpus-graph.html
# or: python -m corpus_builder.viz -g out/corpus/graph.json -w out/corpus/works.jsonl -o viz/corpus-graph.html
```

Open `viz/corpus-graph.html` in a browser. **Click a node** to open the paper in a new tab.

## Project layout

```
config/           # example + local YAML (seeds.example.yaml, seeds.yaml)
src/corpus_builder/
  __main__.py     # python -m corpus_builder
  cli.py          # CLI
  crawl.py        # BFS, forward pass, outputs
  models.py       # CrawlConfig / CrawlSettings (Pydantic)
  openalex.py     # OpenAlex + Crossref helpers
  viz.py          # HTML graph
advice.md         # design notes (optional)
pyproject.toml
```

## Lint / format (optional)

[Ruff](https://docs.astral.sh/ruff/) is configured in `pyproject.toml` (`line-length = 100`). Install ruff separately if you want `ruff check src`.

## License

Specify your license here if you publish the repo.

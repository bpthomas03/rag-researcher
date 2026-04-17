# Advice: building a citation-based research corpus (e.g. SLSN assistant)

This note captures design guidance for a **general RAG** system framed as a **superluminous supernova (SLSN) research assistant**, with emphasis on **growing the corpus by traversing a citation graph** from recent seeds.

## Why citation-graph expansion is a strong start

- **Relevance is partly baked in.** Papers that recent SLSN work cites tend to stay on-topic (physics, progenitors, light curves, host galaxies, etc.), rather than being arbitrary neighbors from a keyword search.
- **You get a graph, not a bag.** That helps later with “related work” style answers, clustering by subtopic, or weighting retrieval (for example, preferring paths closer to your seeds).
- **Seeds define the frontier.** A handful of strong recent papers (and reviews) anchor what “current” means; **backward citations** pull in foundations and methods those papers still rely on.

## Where the approach weakens (plan for it)

- **Recency bias.** Backward-only traversal underweights classic papers that everyone *assumes* and no longer cites often. Mitigations: add a few **canonical** seeds (reviews, milestone discovery papers), and optionally a **forward citation** pass (“who cites this?”) from those anchors so very new work still appears even when older hubs do not cite it yet.
- **Graph blow-up.** Citation graphs grow fast. Use **stopping rules**: max depth, max distinct works, max references expanded per paper, optional year window, **deduplication** by DOI / arXiv id / OpenAlex id, and caps so one huge collaboration or survey cannot dominate.
- **Full text vs metadata.** For RAG you eventually need **PDFs or publisher HTML**, not only bibliographic metadata. Plan around **ADS / arXiv / open access**, and a graceful path when paywalled (abstract-only tier can still help routing and summaries).
- **Corpus quality.** Filter noisy record types (errata, data-only stubs where inappropriate), watch for **retractions**, and be aware that **venue and citation count** are imperfect proxies for usefulness to a narrow expert task.

## A practical pipeline shape

1. **Seed set** — on the order of 5–15 papers you trust: reviews plus a few discovery or survey papers that cover the subfield you care about.
2. **Backward traversal with limits** — breadth- or depth-first expansion of references, with hard caps on depth, breadth, and total works.
3. **Optional forward pass** — from a small set of “pillar” works only, to catch very new papers that cite them but are not yet in your backward cone.
4. **Stable identifiers** — normalize and merge on **DOI**, **arXiv**, **ADS bibcode**, **OpenAlex work id**, so duplicates do not inflate the corpus or fragment the graph.
5. **Ingestion for RAG** — fetch text → chunk (section-aware when possible) → store with rich metadata (title, year, venue, citation edges, distance to seeds, PDF URL when known).

## Adjacent fields (host galaxies, GRBs, statistics, surveys)

An SLSN expert still needs **some** adjacent material, but not unbounded drift. Prefer a **controlled** mix: tighter traversal limits near the core, a **separate** small crawl or higher bar for “peripheral” topics, or explicit **allowlists / scoring** so instrumentation and cosmology papers do not dominate unless you want them to.

## How this maps to this repository

The current codebase focuses on **OpenAlex-backed backward expansion**, **seed discovery**, **reference ranking** to reduce drift, and **graph + JSONL exports** plus an optional **HTML graph view**. The steps above are the larger roadmap: **full-text acquisition**, **chunking and embeddings**, and **retrieval** remain to be wired on top of the corpus artifacts.

**Expansion gate (implemented):** highly cited “hub” papers (software, wide-field surveys, methods) can still enter via a single weak link; their bibliographies are enormous and mostly off-topic. The crawler can require **topic keywords in title or abstract** before a node is allowed to **expand its own references** (seeds bypass this by default via `reference_expansion_bypass_depth`). Tune keywords vs. how aggressively you want foundational non-SLSN-titled papers to branch.

**OpenAlex empty bibliographies:** some works (often very new) have **`referenced_works` empty in OpenAlex** while Crossref still lists hundreds of references. That makes a seed look “dead” in the graph (no outgoing edges). The crawler can **fall back to Crossref** for reference DOIs and resolve them to OpenAlex ids (`crossref_references_fallback` in crawl settings).

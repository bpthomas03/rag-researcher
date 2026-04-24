# SLSN Corpus Parameter Evaluation Plan

## Goal

Tune crawl and node-policy parameters to maximize SLSN coverage while enforcing hard topic boundaries (minimizing non-SLSN leakage).

## 1) Define an Evaluation Set

Create three reference lists:

- **Must-include set** (~50 papers): landmark + recent + edge-case SLSN papers.
- **Must-exclude set** (~50 papers): likely leakage papers (generic SN, instrumentation, broad astro, unrelated domains).
- **Boundary set** (~30 papers): ambiguous cases for manual review.

Use stable identifiers (OpenAlex IDs preferred; DOI fallback accepted).

## 2) Measure Every Crawl

For each run, compute:

- **Recall@gold**: fraction of must-include recovered.
- **Leakage rate**: fraction of must-exclude included.
- **Boundary precision**: manual or semi-automatic score over boundary set.
- **Graph sanity metrics**:
  - node count
  - edge count
  - giant component size %
  - depth distribution

Target behavior: high recall, low leakage, stable graph structure.

## 3) Parameter Sweep (Instead of Manual Guessing)

Sweep the most influential knobs:

- `node_policy.include_threshold`
- `node_policy.continue_threshold`
- `node_policy.embedding_margin`
- `node_policy.embedding_include_similarity`
- `node_policy.embedding_continue_similarity`
- `forward_title_and_abstract_phrase` (strict vs broad)
- `forward_from` (`seeds` vs `gated_graph`)
- `max_depth`, `forward_max_depth`

Rank runs with a weighted objective, e.g.:

`score = 0.7 * recall - 1.5 * leakage`

Keep top configurations on a Pareto frontier (precision vs recall tradeoff).

## 4) Use Staged Expansion

Prefer multi-pass over one giant loose pass:

1. **Strict pass**: high precision seeds + strict thresholds.
2. **Expansion pass**: expand from accepted nodes with slightly relaxed thresholds.
3. **Prune pass**: re-apply strict include policy before finalizing corpus.

This generally yields better boundaries with similar recall.

## 5) Add Hard Constraints

Add non-negotiable edge controls:

- venue/domain denylist
- title/abstract must-have constraints (at least in early passes)
- year/type constraints for forward expansion
- manual allowlist rescue for known exceptions

## 6) Recommended Next Implementation

When resuming this work, implement:

1. `evaluation/` folder for gold/anti-gold/boundary lists
2. `scripts/evaluate_corpus.py` to score a run (`works.jsonl`) against those lists
3. `scripts/sweep_params.py` to execute config sweeps and rank outputs

## 7) Suggested Workflow

1. Curate gold/anti-gold lists.
2. Run baseline config and record metrics.
3. Sweep 20-50 candidate configs.
4. Inspect top 5 qualitatively (false positives/negatives).
5. Freeze a default profile and one strict profile.

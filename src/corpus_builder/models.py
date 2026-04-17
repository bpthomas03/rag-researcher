from __future__ import annotations

from datetime import date
from typing import Literal

from pydantic import BaseModel, Field, model_validator


class CrawlSettings(BaseModel):
    max_depth: int = Field(ge=0, default=2)
    max_works: int = Field(ge=1, default=800)
    max_references_per_work: int = Field(
        ge=1,
        default=80,
        description="Max references followed from each paper after ranking (see reference_selection).",
    )
    reference_selection: Literal["scored", "top_cited", "openalex_order"] = Field(
        default="scored",
        description=(
            "scored: batch metadata + keyword hits + log citations + optional OpenAlex BM25 over ref IDs; "
            "top_cited: global citation count only; openalex_order: first N bibliography ids."
        ),
    )
    reference_score_cap: int = Field(
        default=120,
        ge=20,
        le=500,
        description="Max references considered for scored / top_cited ranking per paper (OpenAlex order).",
    )
    reference_keywords: list[str] = Field(
        default_factory=lambda: [
            "superluminous",
            "slsn",
            "slsne",
            "superluminous supernova",
            "type i superluminous",
            "magnetar",
            "pair-instability",
            "pulsational pair",
            "circumstellar",
        ],
        description="Counted in title+abstract (case-insensitive) when reference_selection=scored.",
    )
    reference_keyword_weight: float = Field(default=2.0, ge=0.0)
    reference_citation_weight: float = Field(default=1.0, ge=0.0)
    reference_openalex_search: str | None = Field(
        default="superluminous supernova SLSN",
        description="If non-empty and weight>0: OpenAlex BM25 within each reference-id batch.",
    )
    reference_openalex_search_weight: float = Field(default=1.5, ge=0.0)
    reference_title_gate_substrings: list[str] | None = Field(
        default=None,
        description=(
            "If non-empty: prefer references whose title contains any substring (case-insensitive); "
            "remaining slots filled by next-best scores. None = no title preference."
        ),
    )
    reference_lookup_batch_size: int = Field(
        default=45,
        ge=1,
        le=100,
        description="Batch size for OpenAlex /works requests (citation counts, summaries, ref search).",
    )
    reference_expansion_gate_keywords: list[str] = Field(
        default_factory=lambda: ["superluminous", "slsn", "slsne"],
        description=(
            "If non-empty, only works whose title OR abstract contains any keyword (case-insensitive) "
            "will have their references expanded in the BFS. Stops survey/software hubs (e.g. Astropy, ZTF) "
            "from pulling in huge unrelated bibliographies. Empty list disables this gate."
        ),
    )
    reference_expansion_bypass_depth: int = Field(
        default=0,
        ge=0,
        le=10,
        description=(
            "Works at BFS depth <= this value always expand references (gate skipped). "
            "0 = only seed papers bypass the gate; 1 = seeds and their direct references bypass."
        ),
    )
    crossref_references_fallback: bool = Field(
        default=True,
        description=(
            "If OpenAlex returns no referenced_works for a DOI work, fetch bibliography DOIs from "
            "Crossref and resolve them to OpenAlex work ids (common for new / thin OpenAlex records)."
        ),
    )
    crossref_references_cap: int = Field(
        default=350,
        ge=20,
        le=800,
        description="Max Crossref bibliography DOIs to consider per work when OpenAlex refs are empty.",
    )
    request_delay_ms: int = Field(ge=0, default=100)

    forward_crawl: bool = Field(
        default=False,
        description="After backward BFS, add works that cite selected anchors (OpenAlex cites: filter).",
    )
    forward_max_depth: int = Field(
        default=1,
        ge=0,
        le=10,
        description="BFS depth for citing edges: 1 = citers of anchors only; 2 = citers of those citers, etc.",
    )
    forward_max_per_work: int = Field(
        default=10,
        ge=1,
        le=100,
        description="Max citing papers kept per expanded work (after the same scoring as references).",
    )
    forward_citing_pool_cap: int = Field(
        default=150,
        ge=20,
        le=5000,
        description="Max citing works to pull from OpenAlex per anchor before ranking (pagination cap).",
    )
    forward_title_and_abstract_phrase: str | None = Field(
        default=None,
        description=(
            "If set, combined with cites:W in OpenAlex as title_and_abstract.search (comma not allowed). "
            "Strongly recommended to stay on-topic (e.g. superluminous)."
        ),
    )
    forward_from: Literal["seeds", "gated_graph"] = Field(
        default="seeds",
        description=(
            "seeds: only anchors are your resolved seed papers (conservative). "
            "gated_graph: every backward-graph node that passes the expansion keyword gate may attract citers."
        ),
    )
    forward_expansion_gate_keywords: list[str] | None = Field(
        default=None,
        description="If None, reuse reference_expansion_gate_keywords for when to expand citing-BFS from a node.",
    )
    forward_expansion_bypass_depth: int = Field(
        default=0,
        ge=0,
        le=10,
        description="Same idea as reference_expansion_bypass_depth: seeds/citers at depth <= N always expand.",
    )

    @model_validator(mode="after")
    def _forward_requires_depth(self) -> CrawlSettings:
        if self.forward_crawl and self.forward_max_depth < 1:
            raise ValueError("forward_crawl requires forward_max_depth >= 1.")
        return self

    @model_validator(mode="after")
    def _scored_has_signal(self) -> CrawlSettings:
        if self.reference_selection != "scored":
            return self
        has_q = bool(self.reference_openalex_search and self.reference_openalex_search.strip())
        if (
            self.reference_keyword_weight <= 0
            and self.reference_citation_weight <= 0
            and (self.reference_openalex_search_weight <= 0 or not has_q)
        ):
            raise ValueError(
                "reference_selection=scored needs keyword_weight, citation_weight, "
                "or (openalex_search_weight with a non-empty reference_openalex_search)."
            )
        return self


class SeedDiscovery(BaseModel):
    """Resolve crawl seeds from OpenAlex (e.g. top-cited SLSN papers in a date window)."""

    limit: int = Field(default=10, ge=1, le=200)
    discovery_mode: Literal["top_cited_in_filters", "search_relevance_then_top_cited"] = Field(
        default="search_relevance_then_top_cited",
        description=(
            "top_cited_in_filters: sort entire filtered corpus by citations (noisy for broad phrases). "
            "search_relevance_then_top_cited: take a relevance-ranked pool from `relevance_search`, "
            "optionally gate titles, then keep the most-cited (better SLSN focus)."
        ),
    )
    candidate_pool: int = Field(
        default=80,
        ge=20,
        le=200,
        description="For search_relevance_then_top_cited: how many OpenAlex hits to pull before re-ranking.",
    )
    relevance_search: str = Field(
        default="superluminous supernova SLSN",
        description="OpenAlex `search` string when using search_relevance_then_top_cited.",
    )
    title_and_abstract_phrase: str = Field(
        default="superluminous supernova",
        description="OpenAlex title_and_abstract.search filter (comma not allowed).",
    )
    years_back: float = Field(
        default=1.0,
        gt=0,
        le=20,
        description="If from_publication_date is omitted: window ends at to_publication_date (or today).",
    )
    from_publication_date: date | None = None
    to_publication_date: date | None = None
    work_types: list[str] = Field(
        default_factory=lambda: ["article", "review"],
        description="OpenAlex work type filter (e.g. article, review, book-chapter).",
    )
    require_title_substrings: list[str] = Field(
        default_factory=lambda: ["superluminous", "slsn", "slsne"],
        description=(
            "After fetching, keep works whose title contains any substring (case-insensitive). "
            "Use [] to disable."
        ),
    )
    extra_search: str | None = Field(
        default=None,
        description="If set, used as OpenAlex `search` instead of relevance_search (legacy override).",
    )


class CrawlConfig(BaseModel):
    mailto: str | None = None
    seeds: list[str] = Field(default_factory=list)
    seed_discovery: SeedDiscovery | None = None
    crawl: CrawlSettings = Field(default_factory=CrawlSettings)

    @model_validator(mode="after")
    def _seeds_or_discovery(self) -> CrawlConfig:
        has_seeds = bool(self.seeds)
        has_recent = self.seed_discovery is not None
        if has_seeds and has_recent:
            raise ValueError("Use either 'seeds' or 'seed_discovery', not both.")
        if not has_seeds and not has_recent:
            raise ValueError("Provide either 'seeds' or 'seed_discovery'.")
        return self


class WorkRecord(BaseModel):
    """Subset of OpenAlex work fields stored for RAG / provenance."""

    openalex_id: str
    doi: str | None
    title: str | None
    publication_year: int | None
    cited_by_count: int | None
    type: str | None
    referenced_work_ids: list[str] = Field(default_factory=list)
    primary_location_pdf: str | None = None
    primary_location_landing: str | None = None
    abstract: str | None = None

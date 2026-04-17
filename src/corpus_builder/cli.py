from __future__ import annotations

import argparse
import asyncio
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
    parser = argparse.ArgumentParser(description="Backward citation crawl via OpenAlex")
    parser.add_argument(
        "-c",
        "--config",
        type=Path,
        default=Path("config/seeds.yaml"),
        help="Path to seeds YAML (default: config/seeds.yaml)",
    )
    parser.add_argument(
        "-o",
        "--out",
        type=Path,
        default=Path("out/corpus"),
        help="Output directory for graph.json and works.jsonl",
    )
    args = parser.parse_args()
    if not args.config.is_file():
        raise SystemExit(f"Config not found: {args.config}")
    cfg = load_config(args.config)
    summary = asyncio.run(run_crawl(cfg, args.out))
    print(
        f"Wrote {summary['works']} works, {summary['edges']} citation edges "
        f"({summary.get('seeds', 0)} seeds) to {summary['out_dir']}"
    )


if __name__ == "__main__":
    main()

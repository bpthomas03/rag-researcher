from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


def _short_label(title: str, wid: str) -> str:
    if isinstance(title, str) and len(title) > 90:
        return title[:87] + "…"
    return title if isinstance(title, str) else wid


def _row_open_url(row: dict[str, Any]) -> str | None:
    for key in ("primary_location_landing", "primary_location_pdf"):
        u = row.get(key)
        if isinstance(u, str) and u.strip().startswith(("http://", "https://")):
            return u.strip()
    doi = row.get("doi")
    if isinstance(doi, str) and doi.strip():
        d = doi.strip().replace("https://doi.org/", "").lstrip("/")
        if d:
            return f"https://doi.org/{d}"
    return None


def _load_work_titles_and_urls(works_path: Path) -> tuple[dict[str, str], dict[str, str]]:
    titles: dict[str, str] = {}
    urls: dict[str, str] = {}
    if not works_path.is_file():
        return titles, urls
    for line in works_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        wid = row.get("openalex_id")
        if not isinstance(wid, str):
            continue
        title = row.get("title") or wid
        titles[wid] = _short_label(title if isinstance(title, str) else wid, wid)
        u = _row_open_url(row)
        if u:
            urls[wid] = u
    return titles, urls


def build_html(graph: dict[str, Any], titles: dict[str, str], open_urls: dict[str, str]) -> str:
    nodes_raw = graph.get("nodes") or []
    edges_raw = graph.get("edges") or []

    vis_nodes: list[dict[str, Any]] = []
    url_map: dict[str, str] = dict(open_urls)
    for i, nid in enumerate(nodes_raw):
        if not isinstance(nid, str):
            continue
        label = titles.get(nid, nid)
        if nid not in url_map and nid.startswith("W"):
            url_map[nid] = f"https://openalex.org/{nid}"
        hint = "Click to open in a new tab." if url_map.get(nid) else "No URL in corpus."
        vis_nodes.append(
            {
                "id": nid,
                "label": label,
                "title": f"<b>{nid}</b><br/>{titles.get(nid, '')}<br/><small>{hint}</small>",
                "value": 4 + (i % 5),
            }
        )

    vis_edges: list[dict[str, Any]] = []
    for e in edges_raw:
        if not isinstance(e, dict):
            continue
        a, b = e.get("from"), e.get("to")
        if not isinstance(a, str) or not isinstance(b, str):
            continue
        vis_edges.append({"from": a, "to": b, "arrows": "to"})

    nodes_json = json.dumps(vis_nodes, ensure_ascii=False)
    edges_json = json.dumps(vis_edges, ensure_ascii=False)
    open_urls_json = json.dumps(url_map, ensure_ascii=False)
    meta = graph.get("meta")
    meta_html = (
        f"<pre id='meta'>{json.dumps(meta, indent=2, ensure_ascii=False)}</pre>"
        if meta
        else ""
    )

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8"/>
  <meta name="viewport" content="width=device-width, initial-scale=1"/>
  <title>Citation graph</title>
  <script src="https://unpkg.com/vis-network@9.1.9/standalone/umd/vis-network.min.js"></script>
  <style>
    body {{ font-family: system-ui, sans-serif; margin: 0; background: #1a1a22; color: #e8e8ef; }}
    #toolbar {{
      padding: 10px 14px; background: #252530; border-bottom: 1px solid #3a3a48;
      font-size: 14px;
    }}
    #toolbar code {{ background: #1a1a22; padding: 2px 6px; border-radius: 4px; }}
    #mynetwork {{ width: 100%; height: calc(100vh - 52px); }}
    #meta {{ display: none; max-height: 200px; overflow: auto; font-size: 11px;
      margin: 0; padding: 10px 14px; background: #12121a; border-top: 1px solid #3a3a48; }}
    #meta.show {{ display: block; }}
    button {{ margin-right: 8px; padding: 6px 12px; cursor: pointer; border-radius: 6px;
      border: 1px solid #555; background: #3a3a55; color: #eee; }}
    button:hover {{ background: #4a4a68; }}
  </style>
</head>
<body>
  <div id="toolbar">
    <strong>Citation graph</strong> — {len(vis_nodes)} nodes, {len(vis_edges)} edges (arrows: citing → cited).
    <span style="opacity:0.85">Click a node to open the paper.</span>
    <button type="button" id="btnFit">Fit view</button>
    <button type="button" id="btnMeta">Toggle crawl meta</button>
  </div>
  <div id="mynetwork"></div>
  {meta_html}
  <script>
    const nodes = new vis.DataSet({nodes_json});
    const edges = new vis.DataSet({edges_json});
    const OPEN_URLS = {open_urls_json};
    const container = document.getElementById("mynetwork");
    const data = {{ nodes, edges }};
    const options = {{
      nodes: {{
        shape: "dot",
        font: {{ color: "#e8e8ef", size: 11 }},
        borderWidth: 1,
        color: {{ border: "#6b8cce", background: "#3d5a8a", highlight: {{ background: "#5a7ab8", border: "#9db7e8" }} }},
      }},
      edges: {{
        color: {{ color: "#6a6a80", highlight: "#9a9ab8" }},
        smooth: {{ type: "dynamic" }},
        width: 0.6,
      }},
      physics: {{
        enabled: true,
        barnesHut: {{
          gravitationalConstant: -3500,
          centralGravity: 0.25,
          springLength: 120,
          springConstant: 0.025,
        }},
        stabilization: {{ iterations: 200 }},
      }},
      interaction: {{ hover: true, tooltipDelay: 120 }},
    }};
    const network = new vis.Network(container, data, options);
    network.on("click", (params) => {{
      if (params.nodes.length !== 1) return;
      const id = params.nodes[0];
      const u = OPEN_URLS[id];
      if (u) window.open(u, "_blank", "noopener,noreferrer");
    }});
    setTimeout(() => {{
      network.setOptions({{ physics: false }});
      network.fit({{ animation: {{ duration: 400, easingFunction: "easeInOutQuad" }} }});
    }}, 30000);
    document.getElementById("btnFit").addEventListener("click", () => network.fit({{ animation: true }}));
    const metaEl = document.getElementById("meta");
    document.getElementById("btnMeta").addEventListener("click", () => {{
      if (metaEl) metaEl.classList.toggle("show");
    }});
  </script>
</body>
</html>
"""


def main() -> None:
    parser = argparse.ArgumentParser(description="Build interactive HTML for corpus graph.json")
    parser.add_argument(
        "-g",
        "--graph",
        type=Path,
        default=Path("out/corpus/graph.json"),
        help="Path to graph.json",
    )
    parser.add_argument(
        "-w",
        "--works",
        type=Path,
        default=None,
        help="Path to works.jsonl for node labels (default: graph dir / works.jsonl)",
    )
    parser.add_argument(
        "-o",
        "--out",
        type=Path,
        default=Path("viz/corpus-graph.html"),
        help="Output HTML (default: viz/corpus-graph.html — outside ignored out/)",
    )
    args = parser.parse_args()
    if not args.graph.is_file():
        raise SystemExit(f"Graph not found: {args.graph}")
    works = args.works or (args.graph.parent / "works.jsonl")
    out = args.out

    graph = json.loads(args.graph.read_text(encoding="utf-8"))
    titles, open_urls = _load_work_titles_and_urls(works)
    html = build_html(graph, titles, open_urls)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(html, encoding="utf-8")
    print(f"Wrote {out.resolve()}")


if __name__ == "__main__":
    main()

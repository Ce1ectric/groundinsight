"""Render ``graphify-out/GRAPH_REPORT.md`` from the JSON analysis dump.

`graphify` 0.7.x stopped emitting the human-readable Markdown report
that earlier versions produced alongside ``graph.json``. The
``graphify claude install`` hook and the auto-generated ``CLAUDE.md``
section, however, both reference ``graphify-out/GRAPH_REPORT.md`` as
the entry point that AI coding assistants should read before searching
raw files. This script reconstructs that file from the artefacts that
``graphify extract`` *does* still write:

- ``graphify-out/.graphify_analysis.json`` -- communities, cohesion,
  god nodes, surprises and token statistics, and
- ``graphify-out/graph.json`` -- the full node/edge dump used to
  resolve community-member labels and source files.

Usage
-----
Run from the repository root, after a successful
``graphify extract . --backend claude`` invocation::

    poetry run python scripts/generate_graphify_report.py

The script reads from and writes to ``./graphify-out/``. It does not
call ``graphify`` itself and does not need network or API access.

Notes
-----
The output mirrors the layout of the historic ``GRAPH_REPORT.md``:
high-level statistics first, then god nodes (highest-degree
symbols), then a per-community summary, followed by the analysis's
"surprises" (edges flagged as high-information). Token statistics
are appended verbatim from ``.graphify_analysis.json`` when present.

The script is a workaround. Once upstream ``graphify`` re-emits the
Markdown report directly, this script and the matching CHANGELOG
entry should be removed.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from collections import Counter
from pathlib import Path
from typing import Any

logger = logging.getLogger("groundinsight.graphify_report")

OUT_DIR_DEFAULT = Path("graphify-out")
ANALYSIS_NAME = ".graphify_analysis.json"
GRAPH_NAME = "graph.json"
REPORT_NAME = "GRAPH_REPORT.md"
REPO_PATH_MARKER = "/groundinsight/"


# --- helpers -----------------------------------------------------------------


def _load(out_dir: Path) -> tuple[dict[str, Any], dict[str, Any]]:
    """Load the analysis dump and the full graph from ``out_dir``.

    Parameters
    ----------
    out_dir
        Directory containing the graphify artefacts (typically
        ``./graphify-out``).

    Returns
    -------
    analysis, graph
        Parsed JSON contents of ``.graphify_analysis.json`` and
        ``graph.json``.
    """
    analysis_path = out_dir / ANALYSIS_NAME
    graph_path = out_dir / GRAPH_NAME
    if not analysis_path.exists():
        raise FileNotFoundError(
            f"{analysis_path} is missing - run `graphify extract .` first."
        )
    if not graph_path.exists():
        raise FileNotFoundError(
            f"{graph_path} is missing - run `graphify extract .` first."
        )
    return (
        json.loads(analysis_path.read_text()),
        json.loads(graph_path.read_text()),
    )


def _degree_index(links: list[dict[str, Any]]) -> Counter:
    """Compute the (undirected) degree of every node id in ``links``."""
    deg: Counter = Counter()
    for link in links:
        s, t = link.get("source"), link.get("target")
        if s is not None:
            deg[s] += 1
        if t is not None:
            deg[t] += 1
    return deg


def _short(text: str | None, n: int = 60) -> str:
    """Truncate ``text`` to ``n`` characters with an ellipsis."""
    if not text:
        return ""
    text = text.replace("\n", " ").strip()
    return text if len(text) <= n else text[: n - 1] + "…"


def _path_relative_to_repo(p: str | None) -> str:
    """Strip the absolute repository prefix from a graph source path."""
    if not p:
        return "?"
    idx = p.find(REPO_PATH_MARKER)
    return p[idx + len(REPO_PATH_MARKER) :] if idx >= 0 else p


# --- rendering ---------------------------------------------------------------


def _render_header(
    nodes: list[dict[str, Any]],
    links: list[dict[str, Any]],
    communities: dict[str, Any],
    commit: str | None,
) -> list[str]:
    out = [
        "# Graph report",
        "",
        (
            "Auto-generated from `graphify-out/.graphify_analysis.json` "
            "by `scripts/generate_graphify_report.py` "
            "(workaround for `graphify` 0.7.x not emitting the Markdown "
            "report itself). Regenerate after every `graphify extract` run."
        ),
        "",
        f"- **Nodes:** {len(nodes)}",
        f"- **Edges:** {len(links)}",
        f"- **Communities:** {len(communities)}",
    ]
    if commit:
        out.append(f"- **Built at commit:** `{commit}`")
    out.append("")
    return out


def _render_gods(
    gods: list[dict[str, Any]], id2node: dict[str, dict[str, Any]]
) -> list[str]:
    if not gods:
        return []
    out = [
        "## God nodes",
        "",
        (
            "Highest-degree symbols across the whole graph. Use these "
            "as entry points for structural questions before falling back "
            "to a raw file search."
        ),
        "",
        "| Degree | Symbol | File |",
        "| ---: | --- | --- |",
    ]
    for g in gods:
        node = id2node.get(g.get("id", ""), {})
        f = _path_relative_to_repo(node.get("source_file"))
        out.append(f"| {g.get('degree', '?')} | `{g.get('label', '?')}` | `{f}` |")
    out.append("")
    return out


def _render_communities(
    communities: dict[str, list[str]],
    cohesion: dict[str, Any],
    id2node: dict[str, dict[str, Any]],
    degree: Counter,
    top_members: int = 8,
) -> list[str]:
    if not communities:
        return []
    out = [
        "## Communities",
        "",
        (
            "Leiden clusters extracted by `graphify`. The members listed "
            "are the highest-degree nodes within each community; consult "
            "`graph.json` for the full membership."
        ),
        "",
    ]

    sorted_cids = sorted(
        communities.keys(),
        key=lambda c: -len(communities[c]),
    )
    for cid in sorted_cids:
        member_ids = communities[cid]
        coh = cohesion.get(cid)
        coh_str = (
            f" (cohesion {coh:.2f})"
            if isinstance(coh, (int, float))
            else ""
        )
        out.append(
            f"### Community {cid} — "
            f"{len(member_ids)} nodes{coh_str}"
        )
        out.append("")
        ranked = sorted(
            member_ids, key=lambda nid: -degree.get(nid, 0)
        )[:top_members]
        for nid in ranked:
            node = id2node.get(nid, {})
            label = _short(node.get("label", "?"), 70)
            f = _path_relative_to_repo(node.get("source_file"))
            d = degree.get(nid, 0)
            out.append(f"- `{label}` (degree {d}) -- `{f}`")
        out.append("")
    return out


def _render_surprises(surprises: list[dict[str, Any]]) -> list[str]:
    if not surprises:
        return []
    out = [
        "## Surprises",
        "",
        (
            "Edges flagged by the analysis as high-information: "
            "cross-community or cross-directory relations that are not "
            "explicit in the source. Often a hint at undocumented "
            "coupling between modules."
        ),
        "",
    ]
    for s in surprises:
        rel = s.get("relation", "?")
        src, tgt = s.get("source", "?"), s.get("target", "?")
        files = ", ".join(
            f"`{_path_relative_to_repo(p)}`" for p in s.get("source_files", [])
        )
        why = s.get("why", "")
        confidence = s.get("confidence", "")
        out.append(f"- `{src}` --[{rel}, {confidence}]--> `{tgt}`")
        if files:
            out.append(f"  - source files: {files}")
        if why:
            out.append(f"  - why: {why}")
    out.append("")
    return out


def _render_tokens(tokens: dict[str, Any] | None) -> list[str]:
    if not tokens:
        return []
    out = [
        "## Token statistics",
        "",
        (
            f"Build cost reported by `graphify`: "
            f"{tokens.get('input', '?')} input tokens, "
            f"{tokens.get('output', '?')} output tokens."
        ),
        "",
    ]
    return out


# --- entry point -------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    """Render ``GRAPH_REPORT.md`` from the graphify analysis dump."""
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument(
        "--out-dir",
        type=Path,
        default=OUT_DIR_DEFAULT,
        help=(
            "Directory containing graphify artefacts and where the "
            "Markdown report is written (default: ./graphify-out)"
        ),
    )
    parser.add_argument(
        "--top-members",
        type=int,
        default=8,
        help=(
            "Number of top-degree members listed per community "
            "(default: 8)"
        ),
    )
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(message)s")

    try:
        analysis, graph = _load(args.out_dir)
    except FileNotFoundError as exc:
        logger.error("%s", exc)
        return 1

    nodes: list[dict[str, Any]] = graph.get("nodes", [])
    links: list[dict[str, Any]] = graph.get("links", [])
    id2node = {n["id"]: n for n in nodes if "id" in n}
    degree = _degree_index(links)

    communities: dict[str, list[str]] = analysis.get("communities", {})
    cohesion: dict[str, Any] = analysis.get("cohesion", {})
    gods: list[dict[str, Any]] = analysis.get("gods", [])
    surprises: list[dict[str, Any]] = analysis.get("surprises", [])
    tokens: dict[str, Any] | None = analysis.get("tokens")
    commit: str | None = graph.get("built_at_commit")

    lines: list[str] = []
    lines += _render_header(nodes, links, communities, commit)
    lines += _render_gods(gods, id2node)
    lines += _render_communities(
        communities, cohesion, id2node, degree, top_members=args.top_members
    )
    lines += _render_surprises(surprises)
    lines += _render_tokens(tokens)

    report_path = args.out_dir / REPORT_NAME
    report_path.write_text("\n".join(lines))
    logger.info(
        "Wrote %s (%d communities, %d gods, %d surprises, %d nodes)",
        report_path,
        len(communities),
        len(gods),
        len(surprises),
        len(nodes),
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env bash
#
# Rebuild the graphify knowledge graph in graphify-out/ using DeepSeek.
#
# The AST pass is local and needs no API key. The key is for the semantic
# pass: extracting concepts from docs and naming the communities, without
# which GRAPH_REPORT.md lists "Community 0 … Community 55" instead of
# readable hub names.
#
# Usage
# -----
#   export DEEPSEEK_API_KEY=sk-...      # or let the script prompt for it
#   ./scripts/refresh_graphify.sh
#
#   ./scripts/refresh_graphify.sh --code-only    # AST + clustering, no API calls
#   ./scripts/refresh_graphify.sh --deep         # aggressive INFERRED edges, costs more
#
# The key is never written to disk by this script. If it is not already in
# the environment it is read interactively and stays in this process only;
# to keep it across runs, put it in your shell profile or a password
# manager, not in a file inside the repository.
#
# Model
# -----
# deepseek-v4-flash, pinned below rather than left to graphify's backend
# default, so an upstream change of that default does not silently move the
# model underneath a run. Override for one run with GRAPHIFY_DEEPSEEK_MODEL.
#
# v4-flash has thinking enabled by default and this script leaves it that
# way. GRAPHIFY_DISABLE_THINKING=1 would remove the rare case of a model
# returning reasoning prose instead of JSON -- which graphify already catches
# and retries -- at the cost of measurably lower extraction quality and file
# coverage. Not a good trade for a graph that gets read as reference.

set -euo pipefail

DEEPSEEK_MODEL="${GRAPHIFY_DEEPSEEK_MODEL:-deepseek-v4-flash}"

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

MODE="full"
EXTRA_ARGS=()
for arg in "$@"; do
    case "$arg" in
        --code-only) MODE="code-only" ;;
        --deep)      EXTRA_ARGS+=("--mode" "deep") ;;
        *)           EXTRA_ARGS+=("$arg") ;;
    esac
done

# --- graphify present and recent enough? ------------------------------------

if ! command -v graphify >/dev/null 2>&1; then
    echo "error: 'graphify' not on PATH. Install with:" >&2
    echo "         pipx install graphifyy      # or: pip install graphifyy" >&2
    exit 1
fi

VERSION="$(python3 -c 'import importlib.metadata as m; print(m.version("graphifyy"))' 2>/dev/null || echo "unknown")"
echo "graphify package version: ${VERSION}"

case "$VERSION" in
    0.[0-8].*|0.9.[0-3]|unknown)
        echo "note: 0.9.4+ writes GRAPH_REPORT.md itself; older versions need the"
        echo "      removed generate_graphify_report.py workaround. Upgrade with:"
        echo "        pipx upgrade graphifyy   # or: pip install --upgrade graphifyy"
        ;;
esac

# --- the key -----------------------------------------------------------------

if [ "$MODE" != "code-only" ]; then
    if [ -z "${DEEPSEEK_API_KEY:-}" ]; then
        echo
        echo "DEEPSEEK_API_KEY is not set. Paste it (input is hidden), or press"
        echo "Enter to fall back to a local-only run without semantic extraction."
        printf "DEEPSEEK_API_KEY: "
        read -rs DEEPSEEK_API_KEY
        echo
        if [ -z "$DEEPSEEK_API_KEY" ]; then
            echo "no key given - falling back to --code-only"
            MODE="code-only"
        else
            export DEEPSEEK_API_KEY
        fi
    fi
fi

# --- run ---------------------------------------------------------------------

echo
if [ "$MODE" = "code-only" ]; then
    echo "==> graphify extract . --code-only   (local AST, no API calls)"
    graphify extract . --code-only "${EXTRA_ARGS[@]}"
    echo
    echo "==> graphify cluster-only .  --no-label   (report with placeholder names)"
    graphify cluster-only . --no-label
else
    echo "==> graphify extract . --backend deepseek --model ${DEEPSEEK_MODEL}"
    graphify extract . --backend deepseek --model "$DEEPSEEK_MODEL" "${EXTRA_ARGS[@]}"
fi

echo
echo "done. Written to graphify-out/:"
ls -1 graphify-out/ 2>/dev/null | sed 's/^/  /'
echo
echo "The 'Token cost' line in graphify-out/GRAPH_REPORT.md reports what the"
echo "run actually consumed."

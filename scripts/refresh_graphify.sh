#!/usr/bin/env bash
#
# Rebuild the graphify knowledge graph in graphify-out/ using DeepSeek.
#
# The AST pass is local and needs no API key. The key is for the semantic
# pass: extracting concepts from docs and naming the communities, without
# which GRAPH_REPORT.md lists "Community 0 … Community 55" instead of
# readable hub names.
#
# Requires graphify to have the `openai` package available: DeepSeek has no
# extra of its own and is reached through the OpenAI-compatible client. The
# script checks for it up front and offers the right repair, because without
# that check the run fails only after the AST pass has completed.
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

# Portability
# -----------
# Must run under the bash 3.2 that ships with macOS, not just bash 5. Two
# consequences show up below: an empty array expands as an unbound variable
# under `set -u` in 3.2, so array expansions use the ${arr[@]+"${arr[@]}"}
# guard; and there is no `readarray`, `${var@Q}` or associative array here.

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

# `graphify --version` prints "graphify X.Y.Z" and works whatever the install
# method is. Asking the system python for the graphifyy metadata does not: a
# pipx install puts the package in its own virtualenv, invisible to python3.
VERSION="$(graphify --version 2>/dev/null | awk '{print $NF}')"
[ -n "$VERSION" ] || VERSION="unknown"
echo "graphify version: ${VERSION}"

case "$VERSION" in
    0.9.[4-9]|0.9.[1-9][0-9]*|0.[1-9][0-9].*|[1-9]*)
        ;;
    *)
        echo
        echo "note: this script expects 0.9.4+, which writes GRAPH_REPORT.md itself."
        echo "      Older versions need the generate_graphify_report.py workaround"
        echo "      that this script replaced. Upgrade with:"
        echo "        pipx upgrade graphifyy   # or: pip install --upgrade graphifyy"
        echo
        printf "continue anyway? [y/N] "
        read -r reply
        case "$reply" in
            [yY]*) ;;
            *) echo "aborted"; exit 1 ;;
        esac
        ;;
esac

# --- the openai client ---------------------------------------------------------
#
# DeepSeek has no extra of its own: graphify reaches it through the
# OpenAI-compatible client, so the semantic pass needs `openai` installed
# *inside graphify's own environment*. A plain `pip install graphifyy` does not
# pull it in, and the failure only surfaces after the AST pass has already run
# and the first chunk has been dispatched.

if [ "$MODE" != "code-only" ]; then
    GRAPHIFY_BIN="$(command -v graphify)"
    GRAPHIFY_PY="$(head -1 "$GRAPHIFY_BIN" 2>/dev/null | sed 's|^#!||; s|^ *||')"

    # A console script installed by pipx or a venv has the interpreter path
    # directly in its shebang. Some installs use `#!/usr/bin/env python3`
    # instead, which is not a path -- resolve it, or the check below would be
    # skipped silently and the run would fail later for the reason it exists
    # to prevent.
    case "$GRAPHIFY_PY" in
        */env\ *|env\ *)
            GRAPHIFY_PY="$(command -v "${GRAPHIFY_PY##* }" 2>/dev/null || true)" ;;
    esac

    # Only a working Python interpreter makes a failed `import openai`
    # meaningful. If the shebang points at something else, say nothing rather
    # than blocking the run on a false alarm.
    GRAPHIFY_PY_OK=0
    if [ -n "$GRAPHIFY_PY" ] && [ -x "$GRAPHIFY_PY" ] \
       && "$GRAPHIFY_PY" -c "import sys" >/dev/null 2>&1; then
        GRAPHIFY_PY_OK=1
    fi

    if [ "$GRAPHIFY_PY_OK" = "1" ] \
       && ! "$GRAPHIFY_PY" -c "import openai" >/dev/null 2>&1; then
        echo
        echo "graphify cannot reach DeepSeek: the 'openai' package is missing from"
        echo "its environment ($GRAPHIFY_PY)."
        echo

        # Pick a repair that actually works for this install. The obvious
        # `python -m pip` is not always available: environments created by
        # `uv venv` or `uv tool install` ship without pip.
        FIX=""
        case "$GRAPHIFY_BIN" in
            *uv/tools*|*uv/tool*)
                FIX='uv tool install "graphifyy[openai]" --force' ;;
            *pipx*)
                FIX='pipx inject graphifyy openai tiktoken' ;;
        esac
        if [ -z "$FIX" ]; then
            if "$GRAPHIFY_PY" -m pip --version >/dev/null 2>&1; then
                FIX="\"$GRAPHIFY_PY\" -m pip install openai tiktoken"
            elif command -v uv >/dev/null 2>&1; then
                FIX="uv pip install --python \"$GRAPHIFY_PY\" openai tiktoken"
            fi
        fi

        if [ -z "$FIX" ]; then
            echo "  That environment has no pip and uv is not on PATH, so this"
            echo "  script cannot repair it. Reinstall graphify with the extra:"
            echo "    pipx install --force \"graphifyy[openai]\""
            echo "    uv tool install \"graphifyy[openai]\" --force"
            echo
            echo "  Or run with --code-only for a local-only graph."
            exit 1
        fi

        echo "  $FIX"
        echo
        printf "run that now? [y/N] "
        read -r reply
        case "$reply" in
            [yY]*)
                if ! eval "$FIX"; then
                    echo >&2
                    echo "install command failed - see the error above. Reinstalling" >&2
                    echo "graphify with the extra also works:" >&2
                    echo "  pipx install --force \"graphifyy[openai]\"" >&2
                    echo "  uv tool install \"graphifyy[openai]\" --force" >&2
                    exit 1
                fi
                if ! "$GRAPHIFY_PY" -c "import openai" >/dev/null 2>&1; then
                    echo "install reported success but 'openai' is still not importable" >&2
                    echo "from $GRAPHIFY_PY - aborting rather than failing mid-run." >&2
                    exit 1
                fi
                echo "openai available now."
                ;;
            *)
                echo "aborted. Re-run with --code-only for a local-only graph." >&2
                exit 1
                ;;
        esac
    fi
fi

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
    graphify extract . --code-only ${EXTRA_ARGS[@]+"${EXTRA_ARGS[@]}"}
    echo
    echo "==> graphify cluster-only .  --no-label   (report with placeholder names)"
    graphify cluster-only . --no-label
else
    echo "==> graphify extract . --backend deepseek --model ${DEEPSEEK_MODEL}"
    graphify extract . --backend deepseek --model "$DEEPSEEK_MODEL" ${EXTRA_ARGS[@]+"${EXTRA_ARGS[@]}"}
    echo
    # extract writes graph.json and the analysis dump but NOT GRAPH_REPORT.md,
    # and leaves the communities as "Community N" placeholders -- it says so
    # itself in its closing line. Skipping this step is how a stale report ends
    # up sitting next to a fresh graph.
    echo "==> graphify cluster-only . --backend=deepseek --model=${DEEPSEEK_MODEL}"
    graphify cluster-only . --backend=deepseek --model="$DEEPSEEK_MODEL"
fi

echo
echo "done. Written to graphify-out/:"
ls -1 graphify-out/ 2>/dev/null | sed 's/^/  /'
echo
echo "The 'Token cost' line in graphify-out/GRAPH_REPORT.md reports what the"
echo "run actually consumed."

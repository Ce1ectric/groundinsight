# tests/_docs_codeblocks.py
"""
Extract and run the Python code blocks of the documentation (DOC13).

Why a separate runner
---------------------
A documentation page shows code that a reader copies. A block that only works
together with prose ("assume ``net`` is a solved network") is fine as long as
the reader is told so; an unmarked one is a promise that the code runs, and
nothing kept that promise before audit pass 19: of the 29 Python blocks on the
API pages, 7 ran. :mod:`tests.test_audit_pass19_docs_codeblocks` therefore runs
every block that is not marked as a fragment. The blocks are executed here, in
a subprocess, for three reasons:

* **isolation** -- documentation code may change process-wide state (warning
  filters, logging handlers, the default database, the working directory);
  none of that may leak into the test process;
* **timeouts** -- a block that hangs is stopped after a fixed time with
  :func:`faulthandler.dump_traceback_later`, which works on every platform the
  CI runs on, and the traceback names the block;
* **one interpreter for all pages** -- importing the package once instead of
  once per page keeps the gate cheap.

The convention the runner implements
------------------------------------
* A fenced block whose info string starts with ``python`` (or ``py``) is a
  Python block. ``pycon`` (``>>>`` sessions), ``text``, ``bash`` and the other
  languages are not executed.
* The Python blocks of one page run top to bottom in **one namespace**, like
  the cells of a notebook, in a fresh temporary working directory.
* A block whose first non-empty line is a comment starting with ``fragment``
  -- ``# fragment`` or ``# fragment: assumes a solved network net`` -- is
  shown, not run.
* A line ``--8<-- "docs/snippets/<file>.py:<section>"`` (the include syntax of
  ``pymdownx.snippets``) stands for the **whole snippet file**: the file runs
  once per page as ``__main__`` and its names join the page namespace, so later
  blocks of the page may use them. The section markers only decide which part
  the reader sees.

Usage (from the test)::

    python tests/_docs_codeblocks.py --root <repo> --out <file.json> \
        --timeout 120 docs/a.md docs/b.md ...

The result file maps every page to the outcome of each of its blocks.
"""

import argparse
import faulthandler
import json
import logging
import os
import re
import runpy
import sys
import tempfile
import time
import traceback
import warnings
from pathlib import Path
from typing import Dict, List, NamedTuple, Optional, Tuple

#: Opening or closing code fence, with its indentation and info string.
_FENCE = re.compile(r"^(?P<indent>[ \t]*)(?P<fence>`{3,}|~{3,})[ \t]*(?P<info>[^\n]*)$")

#: ``pymdownx.snippets`` single-line include, quoted or bare.
_INCLUDE = re.compile(r"""^\s*--8<--\s+(["']?)(?P<spec>[^"'\s]+)\1\s*$""")

#: The fragment marker: first non-empty line ``# fragment`` (any case).
_FRAGMENT = re.compile(r"^#\s*fragment\b", re.IGNORECASE)

#: Info-string languages that are executed.
PYTHON_LANGUAGES = ("python", "py", "python3")


class Block(NamedTuple):
    """
    One fenced code block of a Markdown page.

    Attributes
    ----------
    line : int
        1-based line number of the opening fence.
    language : str
        First word of the info string, lower case (``""`` without one).
    info : str
        The full info string.
    code : str
        The body, dedented by the indentation of the opening fence.
    """

    line: int
    language: str
    info: str
    code: str


def _language(info: str) -> str:
    """
    Language named by a fence info string.

    Parameters
    ----------
    info : str
        Text after the fence, e.g. ``python title="x.py"`` or ``{.python}``.

    Returns
    -------
    str
        The language in lower case, ``""`` when the fence names none.
    """
    text = info.strip().lstrip("{").strip()
    if not text:
        return ""
    word = re.split(r"[\s}]", text, maxsplit=1)[0]
    return word.lstrip(".").lower()


def code_blocks(text: str) -> List[Block]:
    """
    Every fenced code block of a Markdown text.

    Fences may be indented (inside list items, admonitions or tabs); the body
    is dedented by the opening fence's indentation. A closing fence uses the
    same character and at least as many of it, and nothing else.

    Parameters
    ----------
    text : str
        Markdown source.

    Returns
    -------
    list of Block
        In order of appearance.
    """
    lines = text.splitlines()
    blocks: List[Block] = []
    i = 0
    while i < len(lines):
        match = _FENCE.match(lines[i])
        if not match:
            i += 1
            continue
        indent = match.group("indent")
        fence = match.group("fence")
        info = match.group("info").strip()
        body: List[str] = []
        j = i + 1
        while j < len(lines):
            stripped = lines[j].strip()
            if (
                stripped
                and set(stripped) == {fence[0]}
                and len(stripped) >= len(fence)
            ):
                break
            line = lines[j]
            body.append(line[len(indent):] if line.startswith(indent) else line.lstrip())
            j += 1
        blocks.append(Block(i + 1, _language(info), info, "\n".join(body)))
        i = j + 1
    return blocks


def python_blocks(text: str) -> List[Block]:
    """
    The Python blocks of a Markdown text.

    Parameters
    ----------
    text : str
        Markdown source.

    Returns
    -------
    list of Block
        Blocks whose language is one of :data:`PYTHON_LANGUAGES`.
    """
    return [b for b in code_blocks(text) if b.language in PYTHON_LANGUAGES]


def is_fragment(code: str) -> bool:
    """
    Whether a block is marked as a fragment.

    Parameters
    ----------
    code : str
        Block body.

    Returns
    -------
    bool
        ``True`` when the first non-empty line is ``# fragment...``.
    """
    for line in code.splitlines():
        if line.strip():
            return bool(_FRAGMENT.match(line.strip()))
    return False


def includes(code: str) -> List[Tuple[str, Optional[str]]]:
    """
    The snippet includes of a block.

    Parameters
    ----------
    code : str
        Block body.

    Returns
    -------
    list of tuple of (str, str or None)
        ``(path, section)`` per include line, path relative to the repository
        root (the ``base_path`` of ``pymdownx.snippets``).
    """
    found = []
    for line in code.splitlines():
        match = _INCLUDE.match(line)
        if match:
            spec = match.group("spec")
            path, _, section = spec.partition(":")
            found.append((path, section or None))
    return found


def inline_code(code: str) -> str:
    """
    A block body without its include lines.

    Parameters
    ----------
    code : str
        Block body.

    Returns
    -------
    str
        The lines that are not ``--8<--`` includes.
    """
    return "\n".join(line for line in code.splitlines() if not _INCLUDE.match(line))


def classify_page(text: str) -> Dict[str, int]:
    """
    Count the Python blocks of a page by kind.

    Parameters
    ----------
    text : str
        Markdown source.

    Returns
    -------
    dict
        ``{"python": n, "runnable": n, "snippet": n, "fragment": n}`` --
        ``snippet`` counts blocks made only of includes, ``runnable`` the
        other unmarked ones.
    """
    counts = {"python": 0, "runnable": 0, "snippet": 0, "fragment": 0}
    for block in python_blocks(text):
        counts["python"] += 1
        if is_fragment(block.code):
            counts["fragment"] += 1
        elif includes(block.code) and not inline_code(block.code).strip():
            counts["snippet"] += 1
        else:
            counts["runnable"] += 1
    return counts


def _reset_process_state(gi_module) -> None:
    """
    Undo the process-wide state a page may have changed.

    Parameters
    ----------
    gi_module : module or None
        ``groundinsight`` if a page imported it.
    """
    try:
        import matplotlib.pyplot as plt

        plt.close("all")
    except Exception:  # noqa: BLE001 -- matplotlib may be missing or unused
        pass
    logging.captureWarnings(False)
    package_logger = logging.getLogger("groundinsight")
    for handler in list(package_logger.handlers):
        if not isinstance(handler, logging.NullHandler):
            package_logger.removeHandler(handler)
    package_logger.setLevel(logging.NOTSET)
    if gi_module is not None:
        try:
            gi_module.close_dbsession()
        except Exception:  # noqa: BLE001 -- no default database was open
            pass


def run_page(root: Path, page: str, timeout: float, progress: Path) -> List[Dict[str, object]]:
    """
    Run the Python blocks of one page in one namespace.

    Parameters
    ----------
    root : Path
        Repository root (snippet paths are relative to it).
    page : str
        Page path relative to ``root``.
    timeout : float
        Seconds one block (or one snippet file) may take.
    progress : Path
        File that names the block being run, read by the caller when the
        process had to be stopped.

    Returns
    -------
    list of dict
        Per block: ``line``, ``status`` (``"ok"``, ``"fragment"``,
        ``"error"``), ``kind`` (``"inline"`` or ``"snippet"``), ``seconds``
        and ``error`` (the formatted exception or ``None``).
    """
    text = (root / page).read_text(encoding="utf-8")
    namespace: Dict[str, object] = {"__name__": "__docs_page__"}
    ran_snippets: Dict[str, bool] = {}
    outcomes: List[Dict[str, object]] = []
    cwd = os.getcwd()
    workdir = tempfile.mkdtemp(prefix="docs-page-")
    os.chdir(workdir)
    failed = False
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            for block in python_blocks(text):
                outcome: Dict[str, object] = {
                    "line": block.line,
                    "status": "ok",
                    "kind": "snippet" if includes(block.code) else "inline",
                    "seconds": 0.0,
                    "error": None,
                }
                if is_fragment(block.code):
                    outcome["status"] = "fragment"
                    outcomes.append(outcome)
                    continue
                if failed:
                    outcome["status"] = "error"
                    outcome["error"] = "not run: an earlier block of the page failed"
                    outcomes.append(outcome)
                    continue
                progress.write_text(f"{page}:{block.line}", encoding="utf-8")
                start = time.perf_counter()
                faulthandler.dump_traceback_later(timeout, exit=True)
                try:
                    for path, _section in includes(block.code):
                        if path in ran_snippets:
                            continue
                        ran_snippets[path] = True
                        names = runpy.run_path(str(root / path), run_name="__main__")
                        namespace.update(
                            {k: v for k, v in names.items() if not k.startswith("__")}
                        )
                    code = inline_code(block.code)
                    if code.strip():
                        exec(compile(code, f"{page}:{block.line}", "exec"), namespace)
                except BaseException:  # noqa: BLE001 -- reported to the test
                    outcome["status"] = "error"
                    outcome["error"] = traceback.format_exc(limit=6)
                    failed = True
                finally:
                    faulthandler.cancel_dump_traceback_later()
                outcome["seconds"] = round(time.perf_counter() - start, 3)
                outcomes.append(outcome)
    finally:
        os.chdir(cwd)
        _reset_process_state(sys.modules.get("groundinsight"))
    return outcomes


def main(argv: Optional[List[str]] = None) -> int:
    """
    Command-line entry: run the given pages and write the outcomes as JSON.

    Parameters
    ----------
    argv : list of str, optional
        Arguments; defaults to ``sys.argv[1:]``.

    Returns
    -------
    int
        Always 0 when every page could be attempted (block failures are data).
    """
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    parser.add_argument("--root", required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--timeout", type=float, default=120.0)
    parser.add_argument("pages", nargs="*")
    args = parser.parse_args(argv)
    root = Path(args.root).resolve()
    out = Path(args.out)
    progress = out.with_suffix(".progress")
    results: Dict[str, List[Dict[str, object]]] = {}
    for page in args.pages:
        results[page] = run_page(root, page, args.timeout, progress)
        out.write_text(json.dumps(results, indent=1), encoding="utf-8")
    out.write_text(json.dumps(results, indent=1), encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())

# tests/test_audit_pass19_docs_codeblocks.py
"""
Audit pass 19 -- every Python block of the documentation runs or says it does not (DOC13).

Finding DOC13: of the 29 Python blocks on the API pages, 7 ran on their own; 15
stopped at a ``NameError`` (``net``, ``study``, ``result`` ... from prose), two
were pseudo-code with ``...``, one raised on purpose, and nothing marked any of
them. A reader cannot tell a complete example from an excerpt, and no test could
keep the complete ones complete.

The convention (implemented in :mod:`tests._docs_codeblocks`):

* the ``python`` blocks of one page run top to bottom in one namespace, like
  notebook cells, in a fresh temporary directory;
* a block whose first non-empty line is ``# fragment`` (optionally
  ``# fragment: <what it assumes>``) is an excerpt and is not run;
* ``>>>`` sessions are ``pycon`` blocks and pseudo-code is ``text``; neither is
  run;
* an include ``--8<-- "docs/snippets/<file>.py:<section>"`` stands for the
  whole snippet file, which runs once per page and lends its names to the
  blocks after it.

Each block has 120 s; a block that needs the optional ``pandapower`` extra is
skipped without it, except under ``GROUNDINSIGHT_CI=1`` (``pytest.importorskip``
turns into a failure there, see ``tests/conftest.py``).
"""

import importlib.util
import json
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

from tests import _docs_codeblocks as blocks

ROOT = Path(__file__).resolve().parent.parent
DOCS = ROOT / "docs"
RUNNER = Path(blocks.__file__).resolve()

#: Seconds one block may take before the runner stops it.
BLOCK_TIMEOUT_S = 120

#: Python modules of optional extras, mapped to the extra that installs them.
OPTIONAL_MODULES = {"pandapower": "pandapower"}

_IMPORT = re.compile(r"^\s*(?:import|from)\s+(\w+)", re.M)


def _pages_with_python():
    """Every documentation page with at least one Python block, as ``docs``-relative paths."""
    pages = []
    for path in sorted(DOCS.rglob("*.md")):
        if blocks.python_blocks(path.read_text(encoding="utf-8")):
            pages.append(path.relative_to(DOCS).as_posix())
    return pages


PAGES = _pages_with_python()


def _page_source_code(page):
    """All code a page runs -- inline blocks and the snippet files it includes."""
    text = (DOCS / page).read_text(encoding="utf-8")
    parts = []
    for block in blocks.python_blocks(text):
        if blocks.is_fragment(block.code):
            continue
        parts.append(blocks.inline_code(block.code))
        for path, _section in blocks.includes(block.code):
            parts.append((ROOT / path).read_text(encoding="utf-8"))
    return "\n".join(parts)


def _optional_modules(page):
    """Optional-extra modules a page's runnable code imports."""
    imported = set(_IMPORT.findall(_page_source_code(page)))
    return sorted(imported & set(OPTIONAL_MODULES))


def _module_available(name):
    """Whether a module can be imported in this environment."""
    return importlib.util.find_spec(name) is not None


def _run(pages, out, timeout=BLOCK_TIMEOUT_S, wall=None):
    """
    Run the runner in a subprocess with the source tree first on the path.

    Returns
    -------
    tuple of (subprocess.CompletedProcess or None, dict, str)
        The process (``None`` after a wall-clock timeout), the parsed result
        file and the content of the progress file.
    """
    env = dict(os.environ)
    env["PYTHONPATH"] = os.pathsep.join(
        [str(ROOT / "src")] + ([env["PYTHONPATH"]] if env.get("PYTHONPATH") else [])
    )
    env["MPLBACKEND"] = "Agg"
    env.setdefault("PYTHONIOENCODING", "utf-8")
    command = [
        sys.executable, str(RUNNER), "--root", str(ROOT), "--out", str(out),
        "--timeout", str(timeout), *pages,
    ]
    try:
        proc = subprocess.run(
            command, cwd=str(ROOT), env=env, capture_output=True, text=True,
            encoding="utf-8", errors="replace",
            timeout=wall or (timeout * max(1, len(pages)) * 4 + 60),
        )
    except subprocess.TimeoutExpired:
        proc = None
    results = json.loads(out.read_text(encoding="utf-8")) if out.exists() else {}
    progress = out.with_suffix(".progress")
    where = progress.read_text(encoding="utf-8") if progress.exists() else ""
    return proc, results, where


@pytest.fixture(scope="module")
def page_results(tmp_path_factory):
    """Outcomes of every page that can run here, from one runner process."""
    runnable = []
    for page in PAGES:
        if any(not _module_available(m) for m in _optional_modules(page)):
            continue
        runnable.append(page)
    out = tmp_path_factory.mktemp("docs-codeblocks") / "results.json"
    proc, results, where = _run([f"docs/{p}" for p in runnable], out)
    return {"results": results, "proc": proc, "where": where, "pages": runnable}


@pytest.mark.parametrize("page", PAGES)
def test_python_blocks_run_or_are_marked(page, page_results):
    """Every unmarked Python block of a page runs, in page order (DOC13)."""
    for module in _optional_modules(page):
        pytest.importorskip(module, reason=f"{page} needs the '{OPTIONAL_MODULES[module]}' extra")
    key = f"docs/{page}"
    outcomes = page_results["results"].get(key)
    if outcomes is None:
        proc = page_results["proc"]
        stderr = proc.stderr[-4000:] if proc is not None else "(wall-clock timeout)"
        pytest.fail(
            f"{page} was not completed by the runner; it stopped in "
            f"{page_results['where']!r}.\n{stderr}"
        )
    failures = [o for o in outcomes if o["status"] == "error"]
    assert not failures, (
        f"{page}: {len(failures)} Python block(s) do not run. Complete the block, "
        f"or start it with '# fragment' when it is an excerpt.\n\n"
        + "\n".join(f"line {o['line']}:\n{o['error']}" for o in failures)
    )


@pytest.mark.parametrize("page", PAGES)
def test_python_blocks_hold_no_console_sessions(page):
    """``>>>`` sessions belong in ``pycon`` blocks, which are not run (DOC13)."""
    text = (DOCS / page).read_text(encoding="utf-8")
    offending = [
        b.line for b in blocks.python_blocks(text)
        if any(line.lstrip().startswith(">>> ") for line in b.code.splitlines())
    ]
    assert not offending, f"{page}: '>>>' in python blocks at lines {offending}; use ```pycon"


@pytest.mark.parametrize("page", PAGES)
def test_python_snippet_includes_resolve(page):
    """Every include in a Python block names an existing file and section."""
    text = (DOCS / page).read_text(encoding="utf-8")
    for block in blocks.python_blocks(text):
        for path, section in blocks.includes(block.code):
            target = ROOT / path
            assert target.is_file(), f"{page}:{block.line} includes missing {path}"
            if section:
                source = target.read_text(encoding="utf-8")
                assert f"--8<-- [start:{section}]" in source, (page, block.line, section)
                assert f"--8<-- [end:{section}]" in source, (page, block.line, section)


def test_fragment_marker_is_the_first_line_only():
    """A marker further down does not make a block a fragment."""
    assert blocks.is_fragment("# fragment: assumes net\nprint(net)")
    assert blocks.is_fragment("\n  # Fragment\nx")
    assert not blocks.is_fragment("import math\n# fragment\n")
    assert not blocks.is_fragment("# fragments are ...\nx = 1")


def test_code_blocks_are_found_with_indentation_and_languages():
    """Indented fences (lists, admonitions) and info strings are parsed."""
    text = (
        "Intro\n\n"
        "```python\nx = 1\n```\n\n"
        "- item\n\n    ```python title=\"t.py\"\n    y = x + 1\n    ```\n\n"
        "```pycon\n>>> 1\n```\n\n"
        "~~~text\nnot code\n~~~\n"
    )
    found = blocks.code_blocks(text)
    assert [b.language for b in found] == ["python", "python", "pycon", "text"]
    assert found[1].code == 'y = x + 1'
    assert [b.code for b in blocks.python_blocks(text)] == ["x = 1", "y = x + 1"]


def test_runner_page_namespace_fragments_snippets_and_errors(tmp_path):
    """The runner shares names within a page, skips fragments, runs whole snippets
    and reports the first failing block with its line."""
    docs = tmp_path / "docs"
    (docs / "snippets").mkdir(parents=True)
    (docs / "snippets" / "demo.py").write_text(
        "import math\n# --8<-- [start:part]\nvalue = math.sqrt(16.0)\n# --8<-- [end:part]\n"
        "if __name__ == '__main__':\n    shown = True\n",
        encoding="utf-8",
    )
    (docs / "good.md").write_text(
        "```python\n--8<-- \"docs/snippets/demo.py:part\"\n```\n\n"
        "```python\nassert value == 4.0 and shown\ntotal = value + 1\n```\n\n"
        "```python\n# fragment: needs something from prose\nundefined_name\n```\n\n"
        "```python\nassert total == 5.0\n```\n",
        encoding="utf-8",
    )
    (docs / "bad.md").write_text(
        "```python\nx = 1\n```\n\n```python\nraise ValueError('boom')\n```\n\n```python\ny = 2\n```\n",
        encoding="utf-8",
    )
    out = tmp_path / "out.json"
    env = dict(os.environ, PYTHONPATH=str(ROOT / "src"))
    subprocess.run(
        [sys.executable, str(RUNNER), "--root", str(tmp_path), "--out", str(out),
         "docs/good.md", "docs/bad.md"],
        check=True, env=env, capture_output=True, text=True, encoding="utf-8",
        errors="replace",
    )
    results = json.loads(out.read_text(encoding="utf-8"))
    assert [o["status"] for o in results["docs/good.md"]] == ["ok", "ok", "fragment", "ok"]
    assert results["docs/good.md"][0]["kind"] == "snippet"
    bad = results["docs/bad.md"]
    assert [o["status"] for o in bad] == ["ok", "error", "error"]
    assert "ValueError: boom" in bad[1]["error"]
    assert "earlier block" in bad[2]["error"]


def test_runner_stops_a_block_that_hangs(tmp_path):
    """The per-block timeout ends the process and names the block."""
    docs = tmp_path / "docs"
    docs.mkdir()
    (docs / "slow.md").write_text("```python\nimport time\ntime.sleep(30)\n```\n", encoding="utf-8")
    out = tmp_path / "out.json"
    env = dict(os.environ, PYTHONPATH=str(ROOT / "src"))
    proc = subprocess.run(
        [sys.executable, str(RUNNER), "--root", str(tmp_path), "--out", str(out),
         "--timeout", "1", "docs/slow.md"],
        env=env, capture_output=True, text=True, encoding="utf-8", errors="replace",
        timeout=60,
    )
    assert proc.returncode != 0
    assert (tmp_path / "out.progress").read_text(encoding="utf-8") == "docs/slow.md:1"
    assert "Timeout" in proc.stderr

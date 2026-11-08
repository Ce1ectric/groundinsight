# tests/test_audit_pass19_supply_chain.py

"""
Supply chain and the supported Pythons (audit pass 19, F10, REL2, REL4, REL6).

F10 / REL6 -- the maintainer lowered the Python floor from ``^3.13`` to
``^3.12``. The suite is green on 3.12, and ``groundfield`` -- which requires
``python ^3.12`` -- was held at groundinsight 0.3.0 in its own lock because
every later release demanded a newer interpreter than groundfield accepts.

REL4 / DOC5 -- ``README.md`` and ``docs/installation.md`` said "Python 3.14 or
newer" while ``pyproject.toml`` said ``^3.13``, so a user on 3.13 read himself
out of a range the package actually supported. The documented floor is now
checked against the declaration.

REL2 -- ``poetry.lock`` had been resolved for ``^3.14`` and was not re-locked
when the floor moved to ``^3.13``. Poetry 2 refuses ``poetry install`` on such
a lock ("pyproject.toml changed significantly since poetry.lock was last
generated"), so every CI job would have stopped at its install step, while a
suite run from a hand-made virtual environment noticed nothing. The lock is
checked here, where the suite runs, and not only in CI.
"""

from __future__ import annotations

import re
import shutil
import subprocess
import tomllib
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]

#: The floor the maintainer decided on (F10). Pinned here once; every other
#: test derives the floor from ``pyproject.toml`` so the two cannot drift.
_FLOOR = "3.12"


def _pyproject() -> dict:
    """
    Parse ``pyproject.toml``.

    Returns
    -------
    dict
        The TOML document.
    """
    return tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))


def _lock() -> dict:
    """
    Parse ``poetry.lock``.

    Returns
    -------
    dict
        The TOML document.
    """
    return tomllib.loads((ROOT / "poetry.lock").read_text(encoding="utf-8"))


def _declared_floor() -> str:
    """
    The lowest supported Python as ``"3.N"``, read from the caret constraint.

    Returns
    -------
    str
        For example ``"3.12"`` for ``python = "^3.12"``.
    """
    constraint = _pyproject()["tool"]["poetry"]["dependencies"]["python"]
    match = re.fullmatch(r"\^(3\.\d+)", constraint)
    assert match is not None, f"unexpected python constraint {constraint!r}"
    return match.group(1)


# ---------------------------------------------------------------------------
# F10 / REL4 -- the floor, declared and documented
# ---------------------------------------------------------------------------


def test_python_floor_is_3_12():
    """F10: ``^3.12`` -- the version the suite and ``groundfield`` need."""
    assert _pyproject()["tool"]["poetry"]["dependencies"]["python"] == f"^{_FLOOR}"


_FLOOR_STATEMENT = re.compile(
    r"Python\s*\**\s*(?:(?P<a>3\.\d+)\s*\**\s*or\s+newer|(?:>=|≥)\s*(?P<b>3\.\d+))"
)


def _documented_floors():
    """
    Every "Python 3.N or newer" / "Python >= 3.N" statement in the user docs.

    Returns
    -------
    list of tuple
        ``(relative path, "3.N")`` per statement found.
    """
    pages = [ROOT / "README.md", *sorted((ROOT / "docs").rglob("*.md"))]
    found = []
    for page in pages:
        text = page.read_text(encoding="utf-8")
        for match in _FLOOR_STATEMENT.finditer(text):
            found.append(
                (page.relative_to(ROOT).as_posix(), match.group("a") or match.group("b"))
            )
    return found


def test_documented_python_floor_matches_pyproject():
    """REL4 / DOC5: README and installation page state the declared floor."""
    floor = _declared_floor()
    statements = _documented_floors()

    pages = {page for page, _ in statements}
    assert {"README.md", "docs/installation.md"} <= pages, statements
    wrong = [(page, version) for page, version in statements if version != floor]
    assert wrong == [], f"documented floor differs from pyproject ^{floor}: {wrong}"


def test_floor_statement_pattern_recognises_the_known_phrasings():
    """The scan above is only as good as its pattern -- check it on the forms
    the docs used before this pass, so a reworded sentence cannot slip past."""
    samples = {
        "`groundinsight` requires **Python 3.14 or newer**.": "3.14",
        "requires Python 3.13 or newer and": "3.13",
        "Requirements: Python ≥ 3.13 (from pyproject)": "3.13",
        "needs Python >= 3.12": "3.12",
    }
    for text, expected in samples.items():
        match = _FLOOR_STATEMENT.search(text)
        assert match is not None, text
        assert (match.group("a") or match.group("b")) == expected


# ---------------------------------------------------------------------------
# REL2 -- the lock belongs to the declaration
# ---------------------------------------------------------------------------


def test_lock_was_resolved_for_the_declared_python_range():
    """The lock records the range it was solved for; it must be the declared one.

    Independent of Poetry: the ``[metadata] python-versions`` entry is plain
    data, so this check also runs where Poetry is not installed.
    """
    declared = _pyproject()["tool"]["poetry"]["dependencies"]["python"]
    assert _lock()["metadata"]["python-versions"] == declared


def test_poetry_accepts_the_lock_file():
    """Poetry's own verdict on the lock (content hash over ``pyproject.toml``).

    This is the check ``poetry install`` performs in CI before installing
    anything; running it here catches a stale lock where the suite runs.
    """
    poetry = shutil.which("poetry")
    if poetry is None:
        pytest.skip("poetry is not installed in this environment")
    proc = subprocess.run(
        [poetry, "check", "--lock", "--no-interaction"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=300,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr

# tests/test_audit_pass19_standards.py
"""
Code standards as tests (audit pass 19, STD1, STD2, STD3, STD5, STD7, STD8,
STD10, STD11; documentation decision D4).

The house conventions -- module headers, NumPy docstrings, logging instead of
``print``, warnings with a package category, canonical parameter names -- were
written down but checked by nobody, so every review found a few more
violations. This module checks what can be checked statically and fast, so a
violation fails the ordinary test run instead of waiting for the next audit.

The linters themselves (``ruff check`` and ``numpydoc lint``) run in the
blocking ``lint`` job of ``.github/workflows/ci.yml``; here only their
configuration is pinned: lint, never format (STD8), the Python floor as the
target version, and the same exact tool versions in the dev group and in the
CI job.
"""

from __future__ import annotations

import re
import tomllib
from pathlib import Path
from typing import Any, Dict, List

ROOT = Path(__file__).resolve().parents[1]
PYPROJECT = ROOT / "pyproject.toml"
LOCK = ROOT / "poetry.lock"
CI = ROOT / ".github" / "workflows" / "ci.yml"

#: The linters of the lint gate, pinned exactly in the dev group.
LINTERS = ("ruff", "numpydoc")


def _pyproject() -> Dict[str, Any]:
    """
    The parsed ``pyproject.toml``.

    Returns
    -------
    dict
        The TOML document.
    """
    return tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))


def _python_floor() -> str:
    """
    The lowest supported Python as ``"3.X"``, from the Poetry constraint.

    Returns
    -------
    str
        For example ``"3.12"`` for ``python = "^3.12"``.
    """
    constraint = _pyproject()["tool"]["poetry"]["dependencies"]["python"]
    return re.fullmatch(r"\^(3\.\d+)", constraint).group(1)


def _locked_versions() -> Dict[str, List[str]]:
    """
    Every package in ``poetry.lock`` with its locked version(s).

    Returns
    -------
    dict of str to list of str
        Package name to the versions locked for it (more than one where the
        lock splits a package by environment marker).
    """
    lock = tomllib.loads(LOCK.read_text(encoding="utf-8"))
    versions: Dict[str, List[str]] = {}
    for package in lock["package"]:
        versions.setdefault(package["name"], []).append(package["version"])
    return versions


# ---------------------------------------------------------------------------
# STD8 -- the lint configuration
# ---------------------------------------------------------------------------


def test_ruff_targets_the_declared_python_floor():
    """ruff's target version is the floor of the Poetry constraint."""
    ruff = _pyproject()["tool"]["ruff"]
    assert ruff["target-version"] == "py" + _python_floor().replace(".", "")


def test_the_python_floor_agrees_with_the_lowest_ci_python():
    """Second method for the floor: the smallest version the CI matrix runs."""
    text = CI.read_text(encoding="utf-8")
    matrix = re.search(r"python-version:\s*\[([^\]]+)\]", text).group(1)
    versions = [v.strip().strip("\"'") for v in matrix.split(",")]
    lowest = min(versions, key=lambda v: tuple(int(p) for p in v.split(".")))
    assert lowest == _python_floor()


def test_ruff_lints_and_never_formats():
    """Lint, never format: no formatter section, no formatter in a dependency
    group, no formatter in CI."""
    config = _pyproject()
    ruff = config["tool"]["ruff"]
    assert "format" not in ruff
    assert "lint" in ruff and ruff["lint"]["select"]
    for group in config["tool"]["poetry"].get("group", {}).values():
        assert not set(group.get("dependencies", {})) & {"black", "autopep8", "yapf"}
    ci = CI.read_text(encoding="utf-8")
    assert not re.search(r"\bruff\s+format\b|\bblack\b|--fix\b|--add-noqa\b", ci)


def test_ruff_keeps_the_typing_house_style():
    """``typing.Optional/List/Dict`` stay: the rewrites to ``X | None`` and
    ``list[...]`` are switched off, everything else of pyupgrade is on."""
    lint = _pyproject()["tool"]["ruff"]["lint"]
    assert "UP" in lint["select"]
    assert {"UP006", "UP007", "UP035", "UP045"} <= set(lint["ignore"])


def test_numpydoc_lint_is_configured():
    """The numpydoc gate checks everything except the listed style codes."""
    config = _pyproject()["tool"]["numpydoc_validation"]
    assert config["checks"][0] == "all"
    # The section checks of the contract stay on.
    for code in ("PR01", "PR02", "PR04", "RT01", "RT03", "GL06", "GL07", "GL08"):
        assert code not in config["checks"], code
    assert config["exclude"]


def test_the_linters_are_pinned_exactly_in_the_dev_group():
    dev = _pyproject()["tool"]["poetry"]["group"]["dev"]["dependencies"]
    for tool in LINTERS:
        assert re.fullmatch(r"\d+\.\d+\.\d+", dev[tool]), (tool, dev.get(tool))


def test_the_lock_holds_the_pinned_linter_versions():
    """Second method for the pins: what ``poetry.lock`` resolved."""
    dev = _pyproject()["tool"]["poetry"]["group"]["dev"]["dependencies"]
    locked = _locked_versions()
    for tool in (*LINTERS, "radon"):
        assert locked.get(tool) == [dev[tool]], (tool, locked.get(tool), dev[tool])

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

import importlib
import inspect
import re
import tomllib
import warnings
from pathlib import Path
from typing import Any, Dict, Iterator, List, Set, Tuple

import pytest

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


# ---------------------------------------------------------------------------
# STD1 -- the fields of the public models are documented
# ---------------------------------------------------------------------------

#: Modules whose ``__all__`` is the public surface: the package and the
#: service layer for front ends.
PUBLIC_MODULES = ("groundinsight", "groundinsight.api")


def _public_classes() -> Iterator[Tuple[str, type]]:
    """
    Every class of the public surface, with its package base classes.

    Yields
    ------
    tuple of (str, type)
        Dotted name and class; each class once.
    """
    seen: Set[type] = set()
    for module_name in PUBLIC_MODULES:
        module = importlib.import_module(module_name)
        for name in module.__all__:
            obj = getattr(module, name)
            if not inspect.isclass(obj):
                continue
            for klass in obj.__mro__:
                if klass in seen or not klass.__module__.startswith("groundinsight"):
                    continue
                seen.add(klass)
                yield f"{klass.__module__}.{klass.__qualname__}", klass


def _model_fields(klass: type) -> Set[str]:
    """
    The pydantic or dataclass fields of ``klass``, inherited ones included.

    Parameters
    ----------
    klass : type
        Any class.

    Returns
    -------
    set of str
        Field names; empty for a class that is neither.
    """
    if hasattr(klass, "model_fields"):
        return set(klass.model_fields)
    if hasattr(klass, "__dataclass_fields__"):
        return set(klass.__dataclass_fields__)
    return set()


def _declared_fields(klass: type) -> Set[str]:
    """
    The fields ``klass`` adds to those of its bases.

    A field is documented on the class that introduces it; a subclass that
    only narrows an inherited field (a ``Literal`` discriminator, say) relies
    on the base's entry.

    Parameters
    ----------
    klass : type
        A pydantic model or dataclass.

    Returns
    -------
    set of str
        The new field names.
    """
    inherited: Set[str] = set()
    for base in klass.__bases__:
        inherited |= _model_fields(base)
    return _model_fields(klass) - inherited


def _documented_names(klass: type) -> Set[str]:
    """
    Names listed under ``Parameters``, ``Attributes`` or ``Other Parameters``.

    Parameters
    ----------
    klass : type
        The class whose docstring is read.

    Returns
    -------
    set of str
        Every documented name; ``a, b : str`` counts for both.
    """
    from numpydoc.docscrape import ClassDoc

    with warnings.catch_warnings():
        # Custom sections are allowed in prose; docscrape only warns.
        warnings.simplefilter("ignore", UserWarning)
        doc = ClassDoc(klass, config={"show_class_members": False})
    names: Set[str] = set()
    for section in ("Parameters", "Attributes", "Other Parameters"):
        for entry in doc[section]:
            for name in entry.name.split(":")[0].split(","):
                names.add(name.strip().lstrip("*"))
    return names


def _field_documentation_gaps() -> Dict[str, List[str]]:
    """
    Public model classes with fields their docstring does not name.

    Returns
    -------
    dict of str to list of str
        Dotted class name to the undocumented fields it declares.
    """
    pytest.importorskip("numpydoc")
    gaps: Dict[str, List[str]] = {}
    for name, klass in _public_classes():
        missing = sorted(_declared_fields(klass) - _documented_names(klass))
        if missing:
            gaps[name] = missing
    return gaps


def test_public_model_fields_are_documented():
    """Every pydantic or dataclass field of the public surface is listed
    under ``Attributes`` (or ``Parameters``) of the class that declares it."""
    gaps = _field_documentation_gaps()
    assert not gaps, gaps


def test_the_field_check_sees_the_models():
    """Second method: the check is not vacuous -- it walks the core models
    and the DTOs, and their declared fields add up to the model fields."""
    import groundinsight as gi
    from groundinsight.api import schemas

    names = dict(_public_classes())
    assert {"groundinsight.models.core_models.Bus", "groundinsight.api.schemas.BaseDTO"} <= set(names)
    assert _declared_fields(gi.Bus) == _model_fields(gi.Bus)
    assert "dto_schema_version" in _declared_fields(schemas.BaseDTO)
    assert "dto_schema_version" not in _declared_fields(schemas.KeyFigureRowDTO)

# tests/test_audit_pass19_workflows.py

"""
GitHub workflows: test matrix and hardening (audit pass 19, F10, REL6).

F10 / REL6 -- with the floor at ``^3.12`` the CI has to back the claim: the
suite runs on 3.12, 3.13 and 3.14. Before this pass the matrix was 3.13/3.14,
which matched the old ``^3.13`` floor.

The workflow files are read as text. PyYAML only comes with the optional
``docs`` group and is therefore missing from the CI test environment, so every
property has a dependency-free primary check. Where PyYAML is importable the
same property is checked a second time on the parsed document, which guards
the hand-written text checks against a layout they do not understand.
"""

from __future__ import annotations

import itertools
import re
import tomllib
from pathlib import Path
from typing import List, Set, Tuple

import pytest


ROOT = Path(__file__).resolve().parents[1]
WORKFLOWS = ROOT / ".github" / "workflows"


def _text(name: str) -> str:
    """
    Read one workflow file.

    Parameters
    ----------
    name : str
        File name below ``.github/workflows``.

    Returns
    -------
    str
        The file content.
    """
    return (WORKFLOWS / name).read_text(encoding="utf-8")


def _yaml(name: str) -> dict:
    """
    Parse one workflow file with PyYAML, or skip when PyYAML is missing.

    Parameters
    ----------
    name : str
        File name below ``.github/workflows``.

    Returns
    -------
    dict
        The parsed document. Note that YAML 1.1 reads the key ``on`` as
        ``True``; no test here needs the triggers from the parsed form.
    """
    yaml = pytest.importorskip("yaml")
    return yaml.safe_load(_text(name))


def _declared_pythons() -> List[str]:
    """
    The supported minor versions: from the declared floor up to 3.14.

    Returns
    -------
    list of str
        For example ``["3.12", "3.13", "3.14"]`` for ``python = "^3.12"``.
    """
    constraint = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))[
        "tool"
    ]["poetry"]["dependencies"]["python"]
    floor = int(re.fullmatch(r"\^3\.(\d+)", constraint).group(1))
    return [f"3.{minor}" for minor in range(floor, 15)]


def _flow_list(text: str, key: str) -> List[str]:
    """
    Items of the first ``key: [a, "b", ...]`` flow sequence in ``text``.

    Parameters
    ----------
    text : str
        Workflow source.
    key : str
        Mapping key whose value is a flow sequence.

    Returns
    -------
    list of str
        The items with surrounding quotes removed.
    """
    match = re.search(rf"^\s*{re.escape(key)}:\s*\[([^\]]*)\]", text, re.MULTILINE)
    assert match is not None, f"no flow sequence for {key!r}"
    return [item.strip().strip("'\"") for item in match.group(1).split(",") if item.strip()]


def _text_test_matrix(text: str) -> Set[Tuple[str, str]]:
    """
    Expand the ``test`` job matrix of ``ci.yml`` from the text alone.

    Supports exactly the layout used there: ``os`` and ``python-version`` as
    flow sequences, plus an optional ``include`` list of ``os`` /
    ``python-version`` pairs that add combinations.

    Parameters
    ----------
    text : str
        Source of ``ci.yml``.

    Returns
    -------
    set of tuple
        ``(os, python-version)`` combinations.
    """
    combos = set(itertools.product(_flow_list(text, "os"), _flow_list(text, "python-version")))
    for os_name, version in re.findall(
        r"^\s*-\s*os:\s*(\S+)\s*\n\s*python-version:\s*['\"]?([0-9.]+)['\"]?",
        text,
        re.MULTILINE,
    ):
        combos.add((os_name, version))
    return combos


def _yaml_test_matrix(document: dict) -> Set[Tuple[str, str]]:
    """
    Expand the ``test`` job matrix from the parsed document.

    Follows GitHub's rules for the keys used here: the product of the list
    axes, minus ``exclude`` entries, plus ``include`` entries that do not
    match an existing combination.

    Parameters
    ----------
    document : dict
        Parsed ``ci.yml``.

    Returns
    -------
    set of tuple
        ``(os, python-version)`` combinations.
    """
    matrix = document["jobs"]["test"]["strategy"]["matrix"]
    combos = {
        (str(os_name), str(version))
        for os_name, version in itertools.product(matrix["os"], matrix["python-version"])
    }
    for entry in matrix.get("exclude", []):
        combos.discard((str(entry["os"]), str(entry["python-version"])))
    for entry in matrix.get("include", []):
        combos.add((str(entry["os"]), str(entry["python-version"])))
    return combos


# ---------------------------------------------------------------------------
# F10 / REL6 -- every supported Python is tested
# ---------------------------------------------------------------------------


def test_ci_tests_every_supported_python_on_linux():
    """F10: ubuntu runs the full range 3.12 / 3.13 / 3.14."""
    expected = {("ubuntu-latest", version) for version in _declared_pythons()}
    assert _declared_pythons() == ["3.12", "3.13", "3.14"]
    assert expected <= _text_test_matrix(_text("ci.yml"))


def test_ci_matrix_text_and_yaml_expansion_agree():
    """Second method for the matrix: GitHub's expansion on the parsed file."""
    assert _yaml_test_matrix(_yaml("ci.yml")) == _text_test_matrix(_text("ci.yml"))

# tests/test_audit_pass19_warning_policy.py
"""
The warning policy of the suite (audit pass 19, test hygiene).

The suite printed about 1150 warnings per run -- deprecated calls of its
own, unclosed files, and package warnings that nobody asserted -- so a new
one could not be told from the noise. ``pyproject.toml`` now turns them
into errors, and a test either asserts the warning it provokes or
acknowledges it by name (``tests/_warning_policy.py``). This module checks
that the gate is configured, that it is live, and that the list of names
stays exactly as long as the suite needs.
"""

import re
import tomllib
import warnings
from pathlib import Path

import pytest

import groundinsight as gi
from tests._warning_policy import ACKNOWLEDGEABLE, acknowledge

ROOT = Path(__file__).resolve().parents[1]


def _filters():
    """
    The ``filterwarnings`` list of the pytest configuration.

    Returns
    -------
    list of str
        The filters in the order configured.
    """
    config = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    return config["tool"]["pytest"]["ini_options"]["filterwarnings"]


def test_the_gate_turns_the_relevant_warnings_into_errors():
    filters = _filters()
    for required in (
        "error::DeprecationWarning:groundinsight",
        "error::DeprecationWarning:tests",
        "error::ResourceWarning",
        "error::pytest.PytestUnraisableExceptionWarning",
        "error::groundinsight.GroundinsightWarning",
    ):
        assert required in filters


def test_every_ignore_of_the_configuration_says_why():
    """An ``ignore`` filter is preceded by a comment line in pyproject.toml."""
    lines = (ROOT / "pyproject.toml").read_text(encoding="utf-8").splitlines()
    unexplained = [
        line.strip()
        for number, line in enumerate(lines)
        if line.strip().startswith('"ignore:') and not lines[number - 1].strip().startswith("#")
    ]
    assert not unexplained, unexplained


def test_an_unacknowledged_package_warning_fails_the_test():
    """The gate is live: outside ``pytest.warns`` a package warning raises."""
    with pytest.raises(gi.InputWarning):
        warnings.warn(
            gi.InputWarning("Fault 'F' gives no scaling at 250 Hz", code="SCALINGS_INCOMPLETE"),
            stacklevel=1,
        )


@acknowledge("SCALINGS_INCOMPLETE")
def test_an_acknowledged_package_warning_passes():
    """Second method: the same warning, acknowledged by name, does not."""
    warnings.warn(
        gi.InputWarning("Fault 'F' gives no scaling at 250 Hz", code="SCALINGS_INCOMPLETE"),
        stacklevel=1,
    )


@acknowledge("SCALINGS_INCOMPLETE")
def test_an_acknowledgement_covers_only_its_own_message():
    """Acknowledging one warning does not let a different one through."""
    with pytest.raises(gi.InputWarning):
        warnings.warn(
            gi.InputWarning("Source 's' has no value at 250 Hz of the network frequencies"),
            stacklevel=1,
        )


def test_the_patterns_are_valid_filter_messages():
    """Each pattern compiles, and none contains the ``:`` that would end the
    message field of a filter."""
    for name, (category, pattern) in ACKNOWLEDGEABLE.items():
        re.compile(pattern)
        assert ":" not in pattern, name
        assert isinstance(getattr(gi, category), type), name


def test_every_acknowledgeable_name_is_in_use():
    """The list only holds what the suite needs: every name is used by a
    test module, and every name a module uses is in the list."""
    used = set()
    for path in sorted((ROOT / "tests").glob("test_*.py")):
        for call in re.finditer(r"acknowledge\(([^)]*)\)", path.read_text(encoding="utf-8")):
            used |= set(re.findall(r'"([A-Z_]+)"', call.group(1)))
    assert used == set(ACKNOWLEDGEABLE)

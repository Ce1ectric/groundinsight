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

SEC2 -- ``pillow = "^11.3.0"`` kept the dependency below every fixed release.
pip-audit (2026-11-08) lists 73 advisories for 11.3.0 and still 39 for 12.3.0;
all of them are fixed in 13.0.0, the first release without a known advisory.
groundinsight never imports PIL -- matplotlib is the only consumer and asks for
``pillow>=8`` -- so the direct declaration is a security floor and nothing
else, and it carries no upper bound: a cap is what kept every fix out of
reach, and it would do the same to the next one.
"""

from __future__ import annotations

import re
import shutil
import subprocess
import tomllib
from pathlib import Path

import pytest
from packaging.specifiers import SpecifierSet
from packaging.version import Version


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


# ---------------------------------------------------------------------------
# SEC2 -- a pillow floor without known advisories, and no cap
# ---------------------------------------------------------------------------

#: First pillow release without a known advisory (pip-audit, 2026-11-08).
_FIRST_CLEAN_PILLOW = Version("13.0.0")

#: Releases pip-audit reports as affected: 11.3.0 (the old lock, 73
#: advisories), the 12.x releases the old fix suggestion pointed at, and
#: 12.3.0 (still 39 advisories, every one fixed in 13.0.0).
_AFFECTED_PILLOW = ("11.3.0", "12.1.1", "12.2.0", "12.3.0")


def _pillow_specifier() -> SpecifierSet:
    """
    The pillow constraint of ``pyproject.toml`` as a PEP 440 specifier set.

    A Poetry caret (``^13.0``) is not PEP 440 and fails to parse here on
    purpose: the caret's implicit upper bound is the defect this pass removes.

    Returns
    -------
    packaging.specifiers.SpecifierSet
        The parsed constraint.
    """
    return SpecifierSet(_pyproject()["tool"]["poetry"]["dependencies"]["pillow"])


def test_pillow_floor_excludes_every_release_with_a_known_advisory():
    specifier = _pillow_specifier()
    for affected in _AFFECTED_PILLOW:
        assert affected not in specifier, affected
    assert str(_FIRST_CLEAN_PILLOW) in specifier


def test_pillow_constraint_has_no_upper_bound():
    """SEC2's root cause: ``^11.3.0`` meant ``<12``, and every fix was 12.1.1+."""
    operators = {spec.operator for spec in _pillow_specifier()}
    assert operators <= {">=", ">", "!="}, operators
    assert "99.0" in _pillow_specifier()


def test_locked_pillow_is_the_first_clean_release_or_newer():
    """Second route: the version CI actually installs, read from the lock."""
    locked = [p["version"] for p in _lock()["package"] if p["name"] == "pillow"]
    assert len(locked) == 1, locked
    assert Version(locked[0]) >= _FIRST_CLEAN_PILLOW
    assert locked[0] in _pillow_specifier()


def test_installation_page_names_every_runtime_dependency():
    """DOC5: the runtime list on the installation page left out pillow."""
    dependencies = _pyproject()["tool"]["poetry"]["dependencies"]
    runtime = [
        name
        for name, spec in dependencies.items()
        if name != "python" and not (isinstance(spec, dict) and spec.get("optional"))
    ]
    page = (ROOT / "docs" / "installation.md").read_text(encoding="utf-8")
    missing = [name for name in runtime if f"`{name}`" not in page]
    assert missing == []


# ---------------------------------------------------------------------------
# SEC2 -- the dependency audit (scripts/audit_dependencies.py)
# ---------------------------------------------------------------------------


def _audit_module():
    """
    Load ``scripts/audit_dependencies.py`` from its file path.

    ``scripts`` is maintainer tooling and deliberately not packaged, so it is
    loaded the way ``tests/test_release_calver.py`` loads the release script.

    Returns
    -------
    module
        The executed script module.
    """
    import importlib.util
    import sys

    spec = importlib.util.spec_from_file_location(
        "groundinsight_audit_script", ROOT / "scripts" / "audit_dependencies.py"
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _raw_lock_pins() -> list:
    """
    ``name==version`` of every lock entry, read with a regular expression.

    Independent of the script's TOML parsing: Poetry writes ``name`` and
    ``version`` as the first two lines of every ``[[package]]`` table.

    Returns
    -------
    list of str
        The pins in file order.
    """
    text = (ROOT / "poetry.lock").read_text(encoding="utf-8")
    return [
        f"{name}=={version}"
        for name, version in re.findall(
            r'^\[\[package\]\]\nname = "([^"]+)"\nversion = "([^"]+)"', text, re.MULTILINE
        )
    ]


def test_audit_covers_every_locked_package_exactly_once():
    audit = _audit_module()
    layers = audit.audit_layers(audit.locked_requirements(ROOT / "poetry.lock"))
    audited = [pin for layer in layers for pin in layer]
    assert sorted(audited) == sorted(_raw_lock_pins())
    assert len(audited) == len(set(audited))


def test_audit_layers_never_repeat_a_package():
    """pip-audit refuses two pins of one package in a requirements file; the
    lock has such pairs (networkx 3.6 / 3.7, split by a Python marker)."""
    audit = _audit_module()
    layers = audit.audit_layers(["a==1", "b==2", "a==3", "a==2"])
    assert layers == [["a==1", "b==2"], ["a==2"], ["a==3"]]
    for layer in audit.audit_layers(audit.locked_requirements(ROOT / "poetry.lock")):
        names = [pin.split("==")[0] for pin in layer]
        assert len(names) == len(set(names))


def test_the_shipped_ignore_list_is_well_formed():
    audit = _audit_module()
    entries = audit.read_ignore_list(ROOT / ".github" / "pip-audit-ignore.txt")
    assert all(entry.reason for entry in entries)


def test_ignore_entries_need_a_reason_and_an_end_date():
    audit = _audit_module()
    entries = audit.parse_ignore_list(
        "# comment\n\nGHSA-abcd-efgh-ijkl  # only reachable via the GUI; until 2027-01-31\n"
    )
    assert [(e.advisory, e.until.isoformat()) for e in entries] == [
        ("GHSA-abcd-efgh-ijkl", "2027-01-31")
    ]
    for bad in (
        "GHSA-abcd-efgh-ijkl\n",
        "GHSA-abcd-efgh-ijkl  # until 2027-01-31\n",
        "GHSA-abcd-efgh-ijkl  # a reason without an end date\n",
        "GHSA-abcd-efgh-ijkl  # a reason; until 2027-02-30\n",
    ):
        with pytest.raises(ValueError):
            audit.parse_ignore_list(bad)


def test_pip_audit_runs_offline_from_pins_and_strictly():
    audit = _audit_module()
    command = audit.pip_audit_command(Path("req.txt"), ["PYSEC-1", "GHSA-x"])
    assert command[1:3] == ["-m", "pip_audit"]
    for flag in ("--no-deps", "--disable-pip", "--strict"):
        assert flag in command
    pairs = [command[i + 1] for i, part in enumerate(command) if part == "--ignore-vuln"]
    assert pairs == ["PYSEC-1", "GHSA-x"]


def test_main_hands_every_layer_to_pip_audit_and_returns_its_status(monkeypatch, tmp_path):
    """Wiring without network: pip-audit is replaced by a recorder."""
    audit = _audit_module()
    seen = []

    class _Done:
        returncode = 1

    def _fake_run(command, **kwargs):
        requirements = Path(command[command.index("--requirement") + 1])
        seen.append(requirements.read_text(encoding="utf-8").split())
        return _Done()

    monkeypatch.setattr(audit.subprocess, "run", _fake_run)
    monkeypatch.setattr(audit, "_pip_audit_available", lambda: True)
    status = audit.main([])
    assert status == 1
    assert sorted(pin for layer in seen for pin in layer) == sorted(_raw_lock_pins())


def test_main_refuses_an_expired_ignore_entry(tmp_path, monkeypatch):
    audit = _audit_module()
    ignore = tmp_path / "ignore.txt"
    ignore.write_text("PYSEC-2026-1  # was harmless; until 2020-01-01\n", encoding="utf-8")
    monkeypatch.setattr(audit, "_pip_audit_available", lambda: True)
    assert audit.main(["--ignore-file", str(ignore)]) == 2


def test_main_reports_a_missing_pip_audit(monkeypatch):
    audit = _audit_module()
    monkeypatch.setattr(audit, "_pip_audit_available", lambda: False)
    assert audit.main([]) == 2


def test_print_mode_lists_the_pins_without_auditing(capsys):
    audit = _audit_module()
    assert audit.main(["--print"]) == 0
    printed = capsys.readouterr().out.split()
    assert sorted(printed) == sorted(_raw_lock_pins())

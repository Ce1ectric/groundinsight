# tests/test_release_calver.py
"""Calendar versioning in ``scripts/release.py`` (audit pass 19, VER1).

The release script is maintainer tooling and not part of the package, so it
is loaded from its file path rather than imported. The scheme under test is
``YYYY.M.MICRO``: calendar year, month *without* a leading zero, and a
counter that restarts at 0 in every month. The leading zero matters because
PEP 440 normalises ``2026.01.0`` to ``2026.1.0`` -- a zero-padded tag would
never match the version PyPI publishes.
"""

import datetime as dt
import importlib.util
import subprocess
import sys
import tomllib
from pathlib import Path
from typing import Optional

import pytest
from packaging.version import Version

ROOT = Path(__file__).resolve().parents[1]


def _load_release_module():
    spec = importlib.util.spec_from_file_location(
        "groundinsight_release_script", ROOT / "scripts" / "release.py"
    )
    module = importlib.util.module_from_spec(spec)
    # dataclasses resolve string annotations through sys.modules, so the
    # module has to be registered before it is executed.
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


release = _load_release_module()


# --- next version -------------------------------------------------------------


@pytest.mark.parametrize(
    "current, today, expected",
    [
        # first CalVer release after the SemVer era
        ("0.6.0", dt.date(2026, 11, 20), "2026.11.0"),
        # second release in the same month: the counter moves
        ("2026.11.0", dt.date(2026, 11, 28), "2026.11.1"),
        # a new month restarts the counter
        ("2026.11.3", dt.date(2026, 12, 2), "2026.12.0"),
        # January carries no leading zero
        ("2026.12.4", dt.date(2027, 1, 5), "2027.1.0"),
    ],
)
def test_next_calver(current, today, expected):
    assert release.next_calver(current, today) == expected


def test_next_calver_refuses_to_go_backwards():
    # A version set by hand for a later month must not be followed by an
    # older one just because the calendar has not caught up yet.
    with pytest.raises(ValueError, match="newer"):
        release.next_calver("2026.12.0", dt.date(2026, 11, 30))


# --- validation ---------------------------------------------------------------


@pytest.mark.parametrize(
    "bad",
    [
        "2026.01.0",  # leading zero: PEP 440 would publish 2026.1.0
        "2026.13.0",  # no 13th month
        "2026.0.0",  # no month zero
        "26.11.0",  # two-digit year
        "2026.11",  # micro missing
        "2026.11.01",  # leading zero in the counter
        "v2026.11.0",  # the tag, not the version
    ],
)
def test_validate_calver_rejects_malformed_versions(bad):
    with pytest.raises(ValueError):
        release.validate_calver(bad, dt.date(2026, 11, 7))


def test_validate_calver_requires_the_release_month():
    with pytest.raises(ValueError, match="month"):
        release.validate_calver("2026.10.0", dt.date(2026, 11, 7))


@pytest.mark.parametrize("version", ["2026.11.0", "2027.1.0", "2026.12.15"])
def test_calver_versions_are_already_pep440_normalised(version):
    release.validate_calver(version, dt.date(int(version[:4]), int(version.split(".")[1]), 1))
    assert str(Version(version)) == version


def test_calver_sorts_after_the_last_semver_release():
    assert Version("2026.11.0") > Version("0.6.0")


# --- file rewrites ------------------------------------------------------------


_CHANGELOG = """# Changelog

## [Unreleased]

### Added

- Something new.

---

## [0.6.0] — 2026-09-01

### Added

- Old things.

[Unreleased]: https://github.com/Ce1ectric/groundinsight/compare/v0.6.0...HEAD
[0.6.0]: https://github.com/Ce1ectric/groundinsight/releases/tag/v0.6.0
"""


def test_changelog_rollover_writes_the_calver_section_and_links(tmp_path):
    (tmp_path / "CHANGELOG.md").write_text(_CHANGELOG, encoding="utf-8")
    release._update_changelog(
        tmp_path,
        "2026.11.0",
        dt.date(2026, 11, 20),
        allow_empty=False,
        dry_run=False,
    )
    text = (tmp_path / "CHANGELOG.md").read_text(encoding="utf-8")
    assert "## [2026.11.0] — 2026-11-20" in text
    assert (
        "[Unreleased]: https://github.com/Ce1ectric/groundinsight/compare/"
        "v2026.11.0...HEAD" in text
    )
    assert (
        "[2026.11.0]: https://github.com/Ce1ectric/groundinsight/releases/tag/"
        "v2026.11.0" in text
    )


@pytest.mark.parametrize("has_date", [True, False])
def test_citation_date_released_is_written(tmp_path, has_date):
    lines = ['cff-version: 1.2.0', 'version: "0.6.0"']
    if has_date:
        lines.append('date-released: "2026-09-01"')
    path = tmp_path / "CITATION.cff"
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    release._write_release_date(path, dt.date(2026, 11, 20))
    text = path.read_text(encoding="utf-8")
    assert text.count("date-released:") == 1
    assert 'date-released: "2026-11-20"' in text


# --- end to end ---------------------------------------------------------------


def _git(*args, cwd):
    return subprocess.run(
        ["git", *args],
        cwd=cwd,
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    ).stdout


_GUI_PYPROJECT = """[project]
name = "groundinsight-gui"
version = "{version}"
dependencies = [
    "groundinsight=={pin}",
    "nicegui>=4.0,<4.1",
]
"""


def _fake_repository(root: Path, version: str = "0.6.0", gui_pin: Optional[str] = None) -> None:
    (root / "src" / "groundinsight").mkdir(parents=True)
    (root / "gui" / "groundinsight_gui").mkdir(parents=True)
    (root / "gui" / "pyproject.toml").write_text(
        _GUI_PYPROJECT.format(version=version, pin=gui_pin or version), encoding="utf-8"
    )
    (root / "gui" / "groundinsight_gui" / "__init__.py").write_text(
        f'__version__ = "{version}"\n', encoding="utf-8"
    )
    (root / "pyproject.toml").write_text(
        f'[tool.poetry]\nname = "groundinsight"\nversion = "{version}"\n',
        encoding="utf-8",
    )
    (root / "src" / "groundinsight" / "__init__.py").write_text(
        f'__version__ = "{version}"\n', encoding="utf-8"
    )
    (root / "CITATION.cff").write_text(
        f'cff-version: 1.2.0\nversion: "{version}"\n', encoding="utf-8"
    )
    (root / "CHANGELOG.md").write_text(_CHANGELOG, encoding="utf-8")
    _git("init", "-q", cwd=root)
    _git("config", "user.email", "test@example.org", cwd=root)
    _git("config", "user.name", "Test", cwd=root)
    _git("config", "commit.gpgsign", "false", cwd=root)
    _git("add", "-A", cwd=root)
    _git("commit", "-q", "-m", "initial", cwd=root)


def test_release_end_to_end_in_a_temporary_repository(tmp_path, monkeypatch):
    _fake_repository(tmp_path)
    monkeypatch.setattr(release, "_repo_root", lambda: tmp_path)

    assert release.main(["--no-push", "--date", "2026-11-20"]) == 0

    for relative, needle in [
        ("pyproject.toml", 'version = "2026.11.0"'),
        ("src/groundinsight/__init__.py", '__version__ = "2026.11.0"'),
        ("CITATION.cff", 'version: "2026.11.0"'),
        ("CITATION.cff", 'date-released: "2026-11-20"'),
        ("CHANGELOG.md", "## [2026.11.0] — 2026-11-20"),
        # The GUI distribution is released with the library: its version and
        # its pin of the library move together with the library's version.
        ("gui/pyproject.toml", 'version = "2026.11.0"'),
        ("gui/pyproject.toml", '"groundinsight==2026.11.0"'),
        ("gui/groundinsight_gui/__init__.py", '__version__ = "2026.11.0"'),
    ]:
        assert needle in (tmp_path / relative).read_text(encoding="utf-8")
    # Second way: the rewritten GUI metadata as a TOML parser reads it.
    gui = tomllib.loads((tmp_path / "gui" / "pyproject.toml").read_text(encoding="utf-8"))
    assert gui["project"]["version"] == "2026.11.0"
    assert gui["project"]["dependencies"] == ["groundinsight==2026.11.0", "nicegui>=4.0,<4.1"]
    assert _git("status", "--porcelain", cwd=tmp_path) == ""  # all of it committed
    assert _git("tag", "--list", "v2026.11.0", cwd=tmp_path).strip() == "v2026.11.0"
    assert "chore(release): v2026.11.0" in _git("log", "-1", "--format=%s", cwd=tmp_path)


def test_a_gui_pinned_to_another_library_version_stops_the_release(tmp_path, monkeypatch, caplog):
    """The GUI pins the library it was released with; a drifted pin would
    publish a GUI that installs a different library."""
    _fake_repository(tmp_path, gui_pin="0.5.0")
    monkeypatch.setattr(release, "_repo_root", lambda: tmp_path)
    assert release.main(["--no-push", "--date", "2026-11-20"]) == 2
    assert "version drift" in caplog.text
    assert "0.5.0" in caplog.text
    assert '"groundinsight==0.5.0"' in (tmp_path / "gui" / "pyproject.toml").read_text(encoding="utf-8")
    assert 'version = "0.6.0"' in (tmp_path / "pyproject.toml").read_text(encoding="utf-8")


def test_explicit_set_must_match_the_release_month(tmp_path, monkeypatch):
    _fake_repository(tmp_path)
    monkeypatch.setattr(release, "_repo_root", lambda: tmp_path)
    assert release.main(["set", "2026.10.0", "--no-push", "--date", "2026-11-20"]) == 2
    # nothing was rewritten
    assert 'version = "0.6.0"' in (tmp_path / "pyproject.toml").read_text(encoding="utf-8")


@pytest.mark.parametrize("kind", ["major", "minor", "patch"])
def test_semver_bump_kinds_are_retired_with_an_explanation(tmp_path, monkeypatch, kind, caplog):
    _fake_repository(tmp_path)
    monkeypatch.setattr(release, "_repo_root", lambda: tmp_path)
    assert release.main([kind, "--dry-run"]) == 2
    assert "calendar" in caplog.text.lower()


# --- the repository itself ----------------------------------------------------


def test_repository_version_sources_agree_and_follow_the_scheme():
    import re

    sources = {
        "pyproject.toml": r'^version\s*=\s*"([^"]+)"',
        "src/groundinsight/__init__.py": r'^__version__\s*=\s*"([^"]+)"',
        "CITATION.cff": r'^version:\s*"?([^"\s]+)"?',
        "gui/pyproject.toml": r'^version\s*=\s*"([^"]+)"',
        "gui/groundinsight_gui/__init__.py": r'^__version__\s*=\s*"([^"]+)"',
    }
    found = {
        path: re.search(pattern, (ROOT / path).read_text(encoding="utf-8"), re.M).group(1)
        for path, pattern in sources.items()
    }
    found["gui pin"] = re.search(
        r'"groundinsight==([^"]+)"', (ROOT / "gui" / "pyproject.toml").read_text(encoding="utf-8")
    ).group(1)
    assert len(set(found.values())) == 1, found
    # The script keeps exactly these in lockstep.
    locations = release._version_locations(ROOT)
    assert {release._read_current_version(loc) for loc in locations} == set(found.values())
    assert sorted(str(loc.path.relative_to(ROOT)) for loc in locations) == sorted(
        [*sources, "gui/pyproject.toml"]
    )
    version = next(iter(found.values()))
    # 0.6.0 is the last SemVer release; everything after it is CalVer.
    if version != "0.6.0":
        year, month, _ = (int(part) for part in version.split("."))
        release.validate_calver(version, dt.date(year, month, 1))

# tests/test_audit_pass19_gui_distribution.py
"""
The GUI distribution ``groundinsight-gui`` in ``gui/`` (GUI phase 0).

Maintainer decision: the GUI is a distribution of its own in the library's
repository -- the library stays free of UI dependencies -- released together
with the library under the same version and pinned to it. What keeps that
true without anyone remembering it:

* ``scripts/release.py`` rewrites the GUI's version and its pin of the
  library with the library's version (tested in ``test_release_calver.py``);
* the release workflow refuses a tag that one of them disagrees with -- its
  guard is executed here on copies of the version files, so the test checks
  what the workflow runs, not only what it says;
* a CI job installs the library and the GUI from the checkout and runs the
  GUI's tests (NiceGUI's simulated user, no browser) on the newest Python;
* Dependabot watches the GUI's NiceGUI range but leaves the library pin to
  the release script.
"""

from __future__ import annotations

import os
import pathlib
import re
import shutil
import subprocess
import sys
import tomllib
from typing import Any, Dict, List

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
GUI = ROOT / "gui"

#: The files whose version must equal the release tag.
VERSION_FILES = (
    "pyproject.toml",
    "src/groundinsight/__init__.py",
    "CITATION.cff",
    "gui/pyproject.toml",
    "gui/groundinsight_gui/__init__.py",
)


def _workflow(name: str) -> Dict[str, Any]:
    """A workflow file parsed with PyYAML (skip without it; CI strict mode fails)."""
    yaml = pytest.importorskip("yaml")
    return yaml.safe_load((ROOT / ".github" / "workflows" / name).read_text(encoding="utf-8"))


def _library_version() -> str:
    return tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["tool"]["poetry"]["version"]


def _gui_project() -> Dict[str, Any]:
    return tomllib.loads((GUI / "pyproject.toml").read_text(encoding="utf-8"))["project"]


# ---------------------------------------------------------------------------
# the distribution
# ---------------------------------------------------------------------------


def test_the_gui_carries_the_library_version_and_pins_the_library():
    project = _gui_project()
    version = _library_version()
    assert project["name"] == "groundinsight-gui"
    assert project["version"] == version
    assert f"groundinsight=={version}" in project["dependencies"]
    assert "nicegui>=4.0,<4.1" in project["dependencies"]
    # Second way: the version the GUI package reports at run time.
    source = (GUI / "groundinsight_gui" / "__init__.py").read_text(encoding="utf-8")
    assert re.search(r'^__version__ = "([^"]+)"', source, re.M).group(1) == version


def test_the_gui_supports_the_pythons_of_the_library():
    library = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    floor = library["tool"]["poetry"]["dependencies"]["python"].lstrip("^")
    assert _gui_project()["requires-python"] == f">={floor}"


def test_the_library_does_not_depend_on_a_ui_toolkit():
    library = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    groups = [library["tool"]["poetry"]["dependencies"]]
    groups += [g["dependencies"] for g in library["tool"]["poetry"].get("group", {}).values()]
    names = {name.lower() for group in groups for name in group}
    assert not names & {"nicegui", "pywebview", "fastapi", "pyside6"}


def test_the_gui_declares_its_test_tools_and_its_start_command():
    project = _gui_project()
    assert any(req.startswith("pytest") for req in project["optional-dependencies"]["test"])
    assert project["scripts"] == {"groundinsight-gui": "groundinsight_gui.__main__:main"}


# ---------------------------------------------------------------------------
# the release guard
# ---------------------------------------------------------------------------


def _release_guard() -> str:
    """The Python program of the release workflow's version guard."""
    steps = _workflow("release.yml")["jobs"]["build"]["steps"]
    step = next(s for s in steps if s.get("name") == "Verify tag matches every version source")
    run = step["run"]
    match = re.search(r"python - <<'PY'\n(?P<program>.*?)\nPY\s*$", run, re.S)
    assert match, run
    return match["program"]


def _run_guard(workdir: pathlib.Path, tag: str) -> subprocess.CompletedProcess:
    # PYTHONIOENCODING: on Windows a child's pipe uses the ANSI code page, and
    # the guard's non-ASCII message would not decode as UTF-8 (stdout None).
    env = dict(os.environ, RELEASE_TAG=tag, PYTHONIOENCODING="utf-8")
    return subprocess.run(
        [sys.executable, "-c", _release_guard()], cwd=workdir, env=env,
        capture_output=True, text=True, encoding="utf-8", timeout=120,
    )


def _copy_version_files(target: pathlib.Path) -> None:
    for relative in VERSION_FILES:
        destination = target / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ROOT / relative, destination)


def test_the_release_guard_names_the_gui_sources():
    program = _release_guard()
    for relative in ("gui/pyproject.toml", "gui/groundinsight_gui/__init__.py"):
        assert relative in program, relative
    assert "groundinsight==" in program


def test_the_release_guard_accepts_the_repository_and_refuses_a_drifted_gui(tmp_path):
    """Second way: the guard program itself, run on copies of the files."""
    _copy_version_files(tmp_path)
    version = _library_version()
    accepted = _run_guard(tmp_path, f"v{version}")
    assert accepted.returncode == 0, accepted.stdout + accepted.stderr
    assert f"matches all version sources ({version})" in accepted.stdout

    # A GUI pinned to another library release is refused, and named.
    gui = tmp_path / "gui" / "pyproject.toml"
    gui.write_text(gui.read_text(encoding="utf-8").replace(
        f'"groundinsight=={version}"', '"groundinsight==0.0.1"'), encoding="utf-8")
    refused = _run_guard(tmp_path, f"v{version}")
    assert refused.returncode == 1
    assert "::error::" in refused.stdout and "gui/pyproject.toml" in refused.stdout
    assert "0.0.1" in refused.stdout

    # So is a GUI package that reports another version.
    _copy_version_files(tmp_path)
    init = tmp_path / "gui" / "groundinsight_gui" / "__init__.py"
    init.write_text(f'__version__ = "{version}.post1"\n', encoding="utf-8")
    refused = _run_guard(tmp_path, f"v{version}")
    assert refused.returncode == 1 and "gui/groundinsight_gui/__init__.py" in refused.stdout


# ---------------------------------------------------------------------------
# the CI job
# ---------------------------------------------------------------------------


def _gui_job() -> Dict[str, Any]:
    return _workflow("ci.yml")["jobs"]["gui"]


def _runs(job: Dict[str, Any]) -> List[Dict[str, Any]]:
    return [step for step in job["steps"] if "run" in step]


def test_ci_runs_the_gui_tests_on_the_newest_python():
    job = _gui_job()
    assert job["runs-on"] == "ubuntu-latest"
    setup = next(s for s in job["steps"] if s.get("uses", "").startswith("actions/setup-python@"))
    assert setup["with"]["python-version"] == "3.14"
    commands = [" ".join(step["run"].split()) for step in _runs(job)]
    install = next(c for c in commands if "pip install" in c)
    # One call with both: pip satisfies the GUI's exact pin with the library
    # of this commit, not with the release on PyPI.
    assert re.search(r"pip install \./? .*\./gui\[test\]", install), install
    test = next(step for step in _runs(job) if "pytest" in step["run"])
    assert test.get("working-directory") == "gui"


def test_the_gui_job_reads_only():
    job = _gui_job()
    assert "permissions" not in job  # the workflow default: contents read
    checkout = next(s for s in job["steps"] if s.get("uses", "").startswith("actions/checkout@"))
    assert checkout["with"]["persist-credentials"] is False


def test_dependabot_watches_the_gui_but_not_its_library_pin():
    text = (ROOT / ".github" / "dependabot.yml").read_text(encoding="utf-8")
    yaml = pytest.importorskip("yaml")
    updates = yaml.safe_load(text)["updates"]
    gui = [u for u in updates if u["package-ecosystem"] == "pip" and u["directory"] == "/gui"]
    assert len(gui) == 1
    assert {"dependency-name": "groundinsight"} in gui[0].get("ignore", [])

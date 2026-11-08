# tests/test_audit_pass18_packaging.py

"""
Regression tests for audit pass 18, findings M11, M12, M13, M15, M16, M21,
M23 and M24.

M11 -- ``scripts/release.py`` ran ``git`` without ``cwd=root`` while handing
it paths relative to the repository root, so a release started from a
subdirectory left three rewritten version files, no commit and no tag. It
also tagged last, so an existing tag was only noticed after the commit had
been made.

M12 -- ``Path.segments`` is persisted in full and was then discarded by the
first ``run_fault``, because the private ``_paths_fingerprint`` is not
serialised.

M13 -- ``scripts`` was packaged into the wheel and installed executables
named ``release`` and ``generate-third-party-licenses`` into the user's PATH.

M15 -- the deprecation of ``auto_parallel_coefficients`` was only a
``logger.warning``, which the library's ``NullHandler`` default swallows.

M16 -- ``__all__`` listed none of the core models, and ``gi.Path`` was
``pathlib.Path``.

M21 -- a dead ``from sympy import lambdify, sympify, symbols`` in
``core_models``.

M23/M24 -- the declared Python floor and the third-party notices file.
"""

from __future__ import annotations

import ast
import pathlib
import tomllib
import warnings


import groundinsight as gi


_ROOT = pathlib.Path(__file__).resolve().parents[1]


def _pyproject() -> dict:
    return tomllib.loads((_ROOT / "pyproject.toml").read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
# M16 / M21 -- public surface
# ---------------------------------------------------------------------------


def test_core_models_are_re_exported():
    for name in (
        "Network",
        "Bus",
        "BusType",
        "Branch",
        "BranchType",
        "Fault",
        "Source",
        "Path",
        "Result",
        "ResultBus",
        "ResultBranch",
        "ComplexNumber",
    ):
        assert name in gi.__all__, name
        assert hasattr(gi, name), name


def test_gi_path_is_the_domain_model_not_pathlib():
    assert gi.Path is not pathlib.Path
    assert gi.Path.__module__ == "groundinsight.models.core_models"


def test_orm_mirrors_are_not_part_of_the_public_surface():
    for name in ("BusTypeDB", "BranchTypeDB", "NetworkDB", "BusDB", "BranchDB"):
        assert not hasattr(gi, name), name
        assert name not in gi.__all__


def test_everything_in_all_actually_exists():
    missing = [name for name in gi.__all__ if not hasattr(gi, name)]
    assert missing == []


def test_core_models_has_no_dead_sympy_import():
    """M21: nothing in ``core_models`` uses sympy any more."""
    source = (_ROOT / "src/groundinsight/models/core_models.py").read_text(
        encoding="utf-8"
    )
    tree = ast.parse(source)
    imported = {
        node.module
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module
    }
    assert not any(module.startswith("sympy") for module in imported)


# ---------------------------------------------------------------------------
# M15 -- DeprecationWarning
# ---------------------------------------------------------------------------


def test_the_deprecated_argument_raises_a_deprecation_warning(simple_network):
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        gi.run_fault(simple_network, "F", auto_parallel_coefficients=True)
    categories = [w.category for w in caught]
    assert DeprecationWarning in categories
    message = next(
        str(w.message) for w in caught if w.category is DeprecationWarning
    )
    assert "phase_current_mode" in message


def test_the_new_argument_warns_about_nothing(simple_network):
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always", DeprecationWarning)
        gi.run_fault(simple_network, "F", phase_current_mode="auto")
    assert [w for w in caught if w.category is DeprecationWarning] == []


# ---------------------------------------------------------------------------
# M12 -- persisted paths are used instead of thrown away
# ---------------------------------------------------------------------------


def test_restored_paths_survive_the_next_run_fault(simple_network):
    # Since K2 the default ``phase_current_mode="auto"`` no longer enumerates
    # paths, so a persisted path set only exists when it was asked for.
    gi.create_paths(simple_network)
    gi.run_fault(simple_network, "F")
    payload = simple_network.model_dump_json()

    restored = gi.Network.model_validate_json(payload)
    assert restored.paths
    assert restored.needs_path_rebuild() is False

    stored_names = {name: len(p.segments) for name, p in restored.paths.items()}
    gi.run_fault(restored, "F")
    assert {name: len(p.segments) for name, p in restored.paths.items()} == (
        stored_names
    )


def test_restored_paths_are_rebuilt_when_the_topology_no_longer_matches(
    simple_network,
):
    """A hand-edited file whose branches moved must not keep its paths."""
    gi.create_paths(simple_network)
    gi.run_fault(simple_network, "F")
    payload = simple_network.model_dump_json()

    restored = gi.Network.model_validate_json(payload)
    restored.branches["BC"].to_bus = "D"
    assert restored.needs_path_rebuild() is True


def test_a_network_without_paths_is_unaffected(simple_network):
    assert simple_network.paths == {}
    assert simple_network.needs_path_rebuild() is True


# ---------------------------------------------------------------------------
# M11 -- release script
# ---------------------------------------------------------------------------


def _load_script(module_name, filename):
    """Import a maintainer script by path.

    ``scripts/`` is no longer packaged (M13), so it cannot simply be
    imported. The module is registered in ``sys.modules`` before execution
    because ``dataclasses`` resolves string annotations through it.
    """
    import importlib.util
    import sys

    spec = importlib.util.spec_from_file_location(
        module_name, _ROOT / "scripts" / filename
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    try:
        spec.loader.exec_module(module)
    except Exception:
        sys.modules.pop(module_name, None)
        raise
    return module


def _release_module():
    return _load_script("_pass18_release", "release.py")


def test_run_git_runs_in_the_repository_root(monkeypatch):
    release = _release_module()
    calls = []

    def _fake_run(command, **kwargs):
        calls.append((command, kwargs))

        class _Result:
            returncode = 0

        return _Result()

    monkeypatch.setattr(release.subprocess, "run", _fake_run)
    release._run_git(["status"], root=pathlib.Path("/tmp"), dry_run=False)
    assert calls[0][1]["cwd"] == pathlib.Path("/tmp")


def test_git_tag_exists_reports_a_present_tag(tmp_path, monkeypatch):
    release = _release_module()

    def _fake_run(command, **kwargs):
        class _Result:
            stdout = "v9.9.9\n" if command[-1] == "v9.9.9" else ""

        return _Result()

    monkeypatch.setattr(release.subprocess, "run", _fake_run)
    assert release._git_tag_exists("v9.9.9", root=tmp_path) is True
    assert release._git_tag_exists("v0.0.1", root=tmp_path) is False


def test_the_tag_check_happens_before_any_file_is_rewritten():
    """Order matters: the old code tagged last and left a commit behind."""
    source = (_ROOT / "scripts" / "release.py").read_text(encoding="utf-8")
    main_body = source[source.index("def main("):]
    tag_check = main_body.index("_git_tag_exists(")
    first_write = main_body.index("_write_new_version(")
    assert tag_check < first_write


# ---------------------------------------------------------------------------
# M13 / M23 / M24 -- packaging metadata
# ---------------------------------------------------------------------------


def test_scripts_are_not_packaged_and_install_no_maintainer_commands():
    """M13. Audit pass 19 added the package's own command line, named after the
    package; no maintainer command (``release``, ...) may come back."""
    data = _pyproject()
    packages = data["tool"]["poetry"]["packages"]
    assert all(entry.get("include") != "scripts" for entry in packages)
    assert data["tool"]["poetry"].get("scripts", {}) == {
        "groundinsight": "groundinsight.cli:main"
    }


def test_python_floor_matches_the_ci_matrix():
    """M23: the declared floor is a version the CI actually runs.

    Audit pass 19 (F10) moved the floor from 3.13 to 3.12, so the floor is
    read from ``pyproject.toml`` instead of being repeated here; the number
    itself is pinned in ``test_audit_pass19_supply_chain.py``.
    """
    data = _pyproject()
    constraint = data["tool"]["poetry"]["dependencies"]["python"]
    assert constraint.startswith("^")
    floor = constraint[1:]

    workflow = (_ROOT / ".github/workflows/ci.yml").read_text(encoding="utf-8")
    assert f'"{floor}"' in workflow


def test_the_third_party_notices_file_is_not_ignored():
    ignore = (_ROOT / ".gitignore").read_text(encoding="utf-8")
    active = [
        line.strip()
        for line in ignore.splitlines()
        if line.strip() and not line.strip().startswith("#")
    ]
    assert "THIRD_PARTY_NOTICES.md" not in active


def test_optional_dependencies_do_not_count_as_runtime_for_the_notices():
    module = _load_script(
        "_pass18_licences", "generate_third_party_licenses.py"
    )

    names = module._runtime_dependency_names(_ROOT / "pyproject.toml")
    assert "pandapower" not in names
    assert "numpy" in names


def test_release_workflow_tests_before_publishing():
    workflow = (_ROOT / ".github/workflows/release.yml").read_text(encoding="utf-8")
    assert "poetry run pytest" in workflow
    assert "needs: test" in workflow


def test_ci_builds_the_docs_strictly_and_executes_the_notebooks():
    # Since audit pass 19 (DOC1 / DOC2) the strict docs build executes the
    # notebooks itself (mkdocs.yml: execute: true, allow_errors: false), so
    # the separate nbconvert job is gone; the build needs the pandapower
    # extra for examples/pandapower_import.ipynb.
    workflow = (_ROOT / ".github/workflows/ci.yml").read_text(encoding="utf-8")
    assert "mkdocs build --strict" in workflow
    assert "--extras pandapower" in workflow
    config = (_ROOT / "mkdocs.yml").read_text(encoding="utf-8")
    assert "execute: true" in config
    assert "allow_errors: false" in config

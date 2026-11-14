# tests/test_audit_pass18_doctests.py
"""
Audit pass 18 -- T1: the doctests are executed again.

``addopts`` was ``"-ra"`` and ``testpaths`` only ``["tests"]``, so pytest
never collected a single doctest. The docstring examples are the first thing
a reader copies, and three of them had stopped working without anyone
noticing: ``create_fault`` and ``create_source`` referenced a bus that was
never added to the example network, and the module example of
``simulation/outage.py`` used an undefined ``net``.

``--doctest-modules`` is now in ``addopts`` and ``src/groundinsight`` in
``testpaths``, so ``poetry run pytest`` -- and therefore CI, which calls
plain ``pytest`` -- runs them. This module pins both the configuration and
the three repaired examples, so a future edit cannot quietly break them
again.
"""

import doctest
import importlib
import tomllib
from pathlib import Path

import pytest

import groundinsight
from groundinsight import network_operations
from groundinsight.simulation import outage


_PYPROJECT = Path(__file__).resolve().parents[1] / "pyproject.toml"


def _pytest_config():
    with _PYPROJECT.open("rb") as handle:
        return tomllib.load(handle)["tool"]["pytest"]["ini_options"]


def test_pytest_is_configured_to_run_doctests():
    config = _pytest_config()
    assert "--doctest-modules" in config["addopts"]


def test_the_package_sources_are_on_the_collection_path():
    config = _pytest_config()
    assert any(path.endswith("groundinsight") for path in config["testpaths"])
    assert "tests" in config["testpaths"]


@pytest.mark.parametrize(
    "module",
    [network_operations, outage],
    ids=["network_operations", "simulation.outage"],
)
def test_the_repaired_doctests_run(module):
    """The three examples that had gone stale.

    ``doctest.testmod`` honours ``# doctest: +SKIP``, so this only executes
    the examples that are written to be executable.
    """
    result = doctest.testmod(module, verbose=False, report=False)
    assert result.failed == 0, f"{result.failed} doctest(s) failed in {module.__name__}"
    assert result.attempted > 0


#: Modules that may need an optional extra, with the extra. Only these may
#: be left out, and only where the extra is missing: the skip goes through
#: ``pytest.importorskip``, which CI strict mode (``GROUNDINSIGHT_CI=1``,
#: see ``conftest.py``) turns into a failure. Any other ImportError fails.
_OPTIONAL_IMPORTS = {
    "groundinsight.io.pandapower_import": "pandapower",
    "groundinsight.io.pandapower_sc": "pandapower",
}


def _package_modules():
    """
    The importable name of every module of the package, sorted.

    Returns
    -------
    list of str
        Dotted module names, ``__init__`` files as their package.
    """
    package_root = Path(groundinsight.__file__).resolve().parent
    names = []
    for path in sorted(package_root.rglob("*.py")):
        relative = path.relative_to(package_root).with_suffix("")
        parts = [part for part in relative.parts if part != "__init__"]
        names.append(".".join(["groundinsight", *parts]))
    return names


@pytest.mark.parametrize("module_name", _package_modules())
def test_every_module_of_the_package_has_running_doctests(module_name):
    """No module may carry an example that raises when it is run.

    The failure mode this guards against is not a wrong expected output --
    pytest reports that -- but a module nobody ever executed because it was
    outside the collection path. A module that cannot be imported is such a
    module, so its ImportError fails here instead of being passed over.
    """
    extra = _OPTIONAL_IMPORTS.get(module_name)
    if extra is not None:
        pytest.importorskip(extra)
    module = importlib.import_module(module_name)
    result = doctest.testmod(module, verbose=False, report=False)
    assert result.failed == 0, f"{module_name}: {result.failed} failed"


def test_the_optional_imports_name_existing_modules():
    """Second method: the allowlist names modules that exist, so a renamed
    module cannot keep a stale exemption."""
    assert set(_OPTIONAL_IMPORTS) <= set(_package_modules())

# tests/conftest.py

"""
Test-suite-wide fixtures.

Ensures the on-disk SQLite fixture used by ``test_database.py`` is removed
before the suite runs so schema changes (new columns, dropped columns, etc.)
take effect on the next ``Base.metadata.create_all`` call. SQLAlchemy's
``create_all`` only creates tables that do not yet exist; it never adds
columns to an existing table. Without this fixture, switching to a schema
with a new column on ``buses`` or ``branches`` would fail with
``OperationalError: no such column: ...`` against a stale fixture DB.

The DB file is in ``.gitignore``; it is regenerated on every test run.

CI strict mode (audit pass 19). Some tests need an optional import
(``pytest.importorskip``: PyYAML, bandit, hypothesis, pandapower) or a tool on
``PATH`` (``poetry``). Locally they skip when it is missing. In CI a skip of
that kind means the job installed less than the suite needs -- the workflow
checks and the bandit check were skipped there unnoticed -- so under
``GROUNDINSIGHT_CI=1`` (set by the workflows' test jobs) such a skip becomes
a failure. Skips for any other reason (platform, permissions, ``+SKIP``
doctests) are left alone.
"""

from __future__ import annotations

import os
import re
import shutil
from pathlib import Path
from typing import Optional

import pytest


_FIXTURE_DB = Path(__file__).parent / "test_grounding.db"

#: Environment variable that switches the suite into CI strict mode.
_STRICT_ENV = "GROUNDINSIGHT_CI"

#: How pytest words a skip raised by ``pytest.importorskip`` (missing module
#: or a version below ``minversion``).
_MISSING_IMPORT = re.compile(r"could not import |module '[^']+' has __version__ ")


def _strict_ci() -> bool:
    """
    Whether the suite runs in CI strict mode.

    Read at call time, so a test can switch it with ``monkeypatch.setenv``.

    Returns
    -------
    bool
        ``True`` when ``GROUNDINSIGHT_CI`` is set to anything other than an
        empty string, ``0``, ``false`` or ``no``.
    """
    value = os.environ.get(_STRICT_ENV, "").strip().lower()
    return value not in ("", "0", "false", "no")


def _missing_import_reason(report) -> Optional[str]:
    """
    The skip reason of ``report`` if ``pytest.importorskip`` caused the skip.

    Parameters
    ----------
    report : _pytest.reports.TestReport or _pytest.reports.CollectReport
        A test or collection report.

    Returns
    -------
    str or None
        The reason, or ``None`` for a pass, a failure, an expected failure or
        a skip for any other reason.
    """
    if not report.skipped or hasattr(report, "wasxfail"):
        return None
    longrepr = report.longrepr
    if isinstance(longrepr, tuple) and len(longrepr) == 3:
        reason = str(longrepr[2])
    else:
        reason = str(longrepr)
    return reason if _MISSING_IMPORT.search(reason) else None


def _fail_on_missing_import(report) -> None:
    """
    In CI strict mode, turn an ``importorskip`` skip into a failure.

    Parameters
    ----------
    report : _pytest.reports.TestReport or _pytest.reports.CollectReport
        Modified in place.
    """
    reason = _missing_import_reason(report)
    if reason is None or not _strict_ci():
        return
    report.outcome = "failed"
    report.longrepr = (
        f"{_STRICT_ENV}=1: a test dependency is missing, and CI must not skip "
        f"the tests that need it ({reason}). Install the dev group: "
        "poetry install --with dev"
    )


@pytest.hookimpl(wrapper=True)
def pytest_runtest_makereport(item, call):
    """
    Apply CI strict mode to skips inside a test or its fixtures.

    Parameters
    ----------
    item : pytest.Item
        The test.
    call : pytest.CallInfo
        The phase that produced the report.

    Returns
    -------
    _pytest.reports.TestReport
        The report, failed instead of skipped where strict mode demands it.
    """
    report = yield
    _fail_on_missing_import(report)
    return report


@pytest.hookimpl(wrapper=True)
def pytest_make_collect_report(collector):
    """
    Apply CI strict mode to a module-level ``importorskip``.

    Parameters
    ----------
    collector : pytest.Collector
        The collector, typically a test module.

    Returns
    -------
    _pytest.reports.CollectReport
        The report; in strict mode a collection error instead of a skip.
    """
    report = yield
    _fail_on_missing_import(report)
    return report


@pytest.fixture()
def require_tool():
    """
    Locate an executable a test needs, such as ``poetry``.

    Without the tool the test is skipped -- except in CI strict mode, where a
    missing tool fails the test: CI installs every tool the suite uses, so its
    absence there is a broken job, not an optional extra.

    Returns
    -------
    callable
        ``find(name) -> str``, the full path of the executable.
    """

    def find(name: str) -> str:
        """Return the path of ``name``; skip or, in strict mode, fail without it."""
        path = shutil.which(name)
        if path is not None:
            return path
        if _strict_ci():
            pytest.fail(f"{_STRICT_ENV}=1: the test tool {name!r} is not on PATH")
        pytest.skip(f"{name} is not installed in this environment")

    return find


@pytest.fixture(scope="session", autouse=True)
def _reset_test_grounding_db():
    """
    Delete ``tests/test_grounding.db`` once at the start of the test session.

    Yields control to the rest of the suite and does not clean up afterwards
    -- that lets developers inspect the produced DB after a failing run.
    """
    try:
        os.remove(_FIXTURE_DB)
    except FileNotFoundError:
        pass
    yield


@pytest.fixture()
def simple_network():
    """
    A four-bus radial grounding network with two faults, freshly built.

    Audit pass 18 (finding T3/T5): ``conftest.py`` held a single fixture and
    every test file rebuilt its own network from scratch -- roughly twenty
    copies of the same twenty lines, which is why a change to the constructor
    signature touched twenty files. This is the shared starting point for new
    tests. It is deliberately *not* retrofitted onto the existing ones: their
    networks encode the exact topology their finding needs, and rewriting them
    to fit one fixture would lose that.

    Topology::

        A --- B --- C --- D

    ``A`` carries the source, ``C`` the fault ``"F"``, ``D`` the second fault
    ``"F2"``. Two frequencies (50 Hz and 250 Hz) so per-frequency and RMS rows
    differ, and the branch type carries a ``phase_impedance_formula`` so the
    default ``phase_current_mode="auto"`` is exercised.

    Returns
    -------
    groundinsight.Network
        An unsolved network -- call :func:`groundinsight.run_fault` on it.
    """
    import groundinsight as gi

    bus_type = gi.BusType(
        name="fixture_bus",
        description="shared test bus type",
        system_type="Substation",
        voltage_level=110.0,
        impedance_formula="rho*0.01 + 1 + I*f*0.002",
    )
    branch_type = gi.BranchType(
        name="fixture_branch",
        description="shared test branch type",
        grounding_conductor=True,
        self_impedance_formula="(0.25 + I*f*0.012)*l",
        mutual_impedance_formula="(0.05 + I*f*0.010)*l",
        phase_impedance_formula="(0.10 + I*f*0.004)*l",
    )
    network = gi.create_network(
        name="simple_network", frequencies=[50.0, 250.0], description="shared fixture"
    )
    for label in ("A", "B", "C", "D"):
        gi.create_bus(
            name=label,
            type=bus_type,
            specific_earth_resistance=100.0,
            network=network,
        )
    for from_bus, to_bus in (("A", "B"), ("B", "C"), ("C", "D")):
        gi.create_branch(
            name=f"{from_bus}{to_bus}",
            type=branch_type,
            from_bus=from_bus,
            to_bus=to_bus,
            length=1.0,
            network=network,
        )
    gi.create_source(
        name="s",
        bus="A",
        values={50.0: 1000.0 + 0.0j, 250.0: 100.0 + 0.0j},
        network=network,
    )
    gi.create_fault(
        name="F", bus="C", scalings={50.0: 1.0, 250.0: 1.0}, network=network
    )
    gi.create_fault(
        name="F2", bus="D", scalings={50.0: 1.0, 250.0: 1.0}, network=network
    )
    return network

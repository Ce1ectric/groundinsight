# tests/test_audit_pass5_fixes.py

"""
Regression tests for the fifth audit-pass bug-fix batch.

This file collects pytest cases for every bug reported in
``applications/audit-report-changelogs-2026-05-13.md`` under the
*fifth 2026-05-13 review pass* heading. Each test pins one finding so
the suite reports clearly which fix has regressed if any.

The fifth pass focused on residual side-effects of the Pass-4
implementation block:

1. ``Network.invalidate_paths()`` now scopes the pathfinder cache
   clear to the calling network instance.
2. ``pathfinder._GRAPH_CACHE`` includes a structural fingerprint
   ``(name, n_buses, n_branches)`` to guard against CPython
   id-recycling.
3. ``groundinsight.__all__`` lists ``session`` *and* keeps the
   historic ``db_session`` alias importable.
4. ``close_dbsession`` tears down each global independently and
   never raises on a half-constructed state.
5. ``Network.frequencies`` rejects duplicate / NaN / negative
   values.
6. ``Network.set_active_fault(..., keep_results=True)`` preserves
   the cached ``Result`` for the activated fault.

Audit pass 19 (ARC2) replaced the session globals by ``gi.Database``.
Findings 3 and 4 are kept in their new form: ``db_session`` stays importable
as a deprecated alias but leaves ``__all__`` (a star import must not raise
the deprecation), and ``close_dbsession`` still never raises while tearing
the default database down. The old half-state tests assigned the globals
directly; that is no longer possible -- such an assignment would only shadow
the alias for the rest of the process.
"""

from __future__ import annotations

import gc
import logging

import numpy as np
import pytest
from pydantic import ValidationError

import groundinsight as gi
from groundinsight.models.core_models import (
    BranchType,
    BusType,
    Fault,
    Result,
    Source,
)
from groundinsight.pathfinder import (
    PathFinder,
    _FIND_PATHS_CACHE,
    _GRAPH_CACHE,
    clear_pathfinder_cache,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _two_bus_network(name: str = "net"):
    """A minimal 2-bus / 1-branch network used by the cache tests."""
    bt = BusType(
        name="BT",
        description="",
        system_type="Grounded",
        voltage_level=20.0,
        impedance_formula="rho * 0 + 1 + I * f * 1/50",
    )
    brt = BranchType(
        name="BRT",
        description="",
        grounding_conductor=True,
        self_impedance_formula="(rho * 0 + 0.25 + I * f * 0.012)*l",
        mutual_impedance_formula="(rho * 0 + 0.0 + I * f * 0.010)*l",
    )
    net = gi.create_network(name=name, frequencies=[50.0])
    gi.create_bus(name="B1", type=bt, specific_earth_resistance=100.0, network=net)
    gi.create_bus(name="B2", type=bt, specific_earth_resistance=100.0, network=net)
    gi.create_branch(
        name="L12",
        type=brt,
        from_bus="B1",
        to_bus="B2",
        length=1.0,
        network=net,
    )
    gi.create_source(
        name="S1",
        bus="B1",
        values={50.0: 1.0 + 0.0j},
        network=net,
    )
    gi.create_fault(
        name="F1",
        bus="B2",
        scalings={50.0: 1.0},
        network=net,
        active=True,
    )
    return net


@pytest.fixture(autouse=True)
def _clear_caches_between_tests():
    """Each test starts with a clean pathfinder cache."""
    clear_pathfinder_cache()
    yield
    clear_pathfinder_cache()


# ---------------------------------------------------------------------------
# Pass-5 finding 1 — ``Network.invalidate_paths`` is network-scoped.
# ---------------------------------------------------------------------------


def test_invalidate_paths_does_not_drop_other_networks_cache():
    """``net_a.invalidate_paths()`` must keep ``net_b``'s cache alive."""
    net_a = _two_bus_network(name="alpha")
    net_b = _two_bus_network(name="beta")

    pf_a = PathFinder(net_a)
    pf_b = PathFinder(net_b)
    # Populate both caches.
    pf_a.find_paths("B1", "B2")
    pf_b.find_paths("B1", "B2")

    # Sanity: both networks have an entry in the graph cache.
    keys_a = [k for k in _GRAPH_CACHE if k[1] == "alpha"]
    keys_b_before = [k for k in _GRAPH_CACHE if k[1] == "beta"]
    assert keys_a, "expected an alpha entry in _GRAPH_CACHE"
    assert keys_b_before, "expected a beta entry in _GRAPH_CACHE"

    net_a.invalidate_paths()

    keys_a_after = [k for k in _GRAPH_CACHE if k[1] == "alpha"]
    keys_b_after = [k for k in _GRAPH_CACHE if k[1] == "beta"]
    assert not keys_a_after, "alpha's cache should be cleared"
    assert keys_b_after, (
        "beta's cache must survive a network-scoped invalidation on alpha"
    )


def test_clear_pathfinder_cache_unscoped_drops_all_entries():
    """The unscoped form still clears every network (recovery path)."""
    net_a = _two_bus_network(name="alpha")
    net_b = _two_bus_network(name="beta")
    PathFinder(net_a).find_paths("B1", "B2")
    PathFinder(net_b).find_paths("B1", "B2")
    assert _GRAPH_CACHE, "precondition: cache populated"
    clear_pathfinder_cache()
    assert not _GRAPH_CACHE
    assert not _FIND_PATHS_CACHE


# ---------------------------------------------------------------------------
# Pass-5 finding 2 — ``pathfinder._GRAPH_CACHE`` includes structural fingerprint.
# ---------------------------------------------------------------------------


def test_topology_key_includes_structural_fingerprint():
    """``(name, n_buses, n_branches)`` participate in the cache key."""
    net = _two_bus_network(name="alpha")
    pf = PathFinder(net)
    key = pf._topology_key
    # (id, name, n_buses, n_branches, active_buses, active_branches)
    assert key[1] == "alpha"
    assert key[2] == len(net.buses) == 2
    assert key[3] == len(net.branches) == 1


def test_cache_does_not_falsely_hit_after_id_recycle():
    """Build a network, drop it, build another with the same id.

    Even if Python recycles the ``id`` for the new network, the
    structural fingerprint differs (different ``name``, different
    branch count) so the PathFinder must build a fresh graph rather
    than picking up the stale cached one.
    """
    net_a = _two_bus_network(name="alpha")
    pf_a = PathFinder(net_a)
    pf_a.find_paths("B1", "B2")
    stale_id = id(net_a)
    stale_keys = [k for k in _GRAPH_CACHE if k[0] == stale_id]
    assert stale_keys, "precondition: cache contains alpha"

    # Force garbage collection of net_a.
    del pf_a
    del net_a
    gc.collect()

    # Build a structurally different network (extra bus + branch). Even
    # if the new id collides with ``stale_id`` (unlikely on a modern
    # interpreter; but the structural fingerprint is the defence), the
    # cache lookup must miss because (name, n_buses, n_branches)
    # differs.
    bt = BusType(
        name="BT2",
        description="",
        system_type="Grounded",
        voltage_level=20.0,
        impedance_formula="rho * 0 + 1 + I * f * 1/50",
    )
    brt = BranchType(
        name="BRT2",
        description="",
        grounding_conductor=True,
        self_impedance_formula="(rho * 0 + 0.5 + I * f * 0.020)*l",
        mutual_impedance_formula="(rho * 0 + 0.0 + I * f * 0.010)*l",
    )
    net_c = gi.create_network(name="gamma", frequencies=[50.0])
    for bn in ("B1", "B2", "B3"):
        gi.create_bus(
            name=bn,
            type=bt,
            specific_earth_resistance=100.0,
            network=net_c,
        )
    gi.create_branch(
        name="L12", type=brt, from_bus="B1", to_bus="B2",
        length=1.0, network=net_c,
    )
    gi.create_branch(
        name="L23", type=brt, from_bus="B2", to_bus="B3",
        length=1.0, network=net_c,
    )

    pf_c = PathFinder(net_c)
    key_c = pf_c._topology_key
    # The structural part rules out any false hit on the stale entry.
    assert key_c[1] == "gamma"
    assert key_c[2] == 3
    assert key_c[3] == 2


# ---------------------------------------------------------------------------
# Pass-5 finding 3 — ``db_session`` is importable and is the same handle.
# ---------------------------------------------------------------------------


def _alias(name: str, module=gi):
    """Read a deprecated session alias and assert its ``DeprecationWarning``."""
    with pytest.deprecated_call(match=rf"groundinsight\.{name} is deprecated"):
        return getattr(module, name)


def test_db_session_importable_from_top_level():
    """``from groundinsight import db_session`` must not raise."""
    # Re-import path: by attribute first.
    with pytest.deprecated_call():
        assert hasattr(gi, "db_session")
    # And via from-import semantics.
    with pytest.deprecated_call():
        from groundinsight import db_session  # noqa: F401  -- import must succeed


def test_db_session_importable_but_no_longer_in_dunder_all():
    """Deprecated since pass 19: importable with a warning, not star-exported."""
    assert "db_session" not in gi.__all__
    assert "session" not in gi.__all__
    with pytest.deprecated_call():
        from groundinsight import db_session  # noqa: F401


def test_db_session_alias_tracks_session(tmp_path):
    """After ``start_dbsession`` both names point at the same scoped session."""
    db_path = tmp_path / "alias.db"
    gi.start_dbsession(str(db_path))
    try:
        # Re-import to pick up the rebound globals (Python imports the
        # module object once, so attribute access stays current — but
        # the symmetric assertion is the contract we ship).
        import groundinsight as gi_ref
        assert _alias("session", gi_ref) is _alias("db_session", gi_ref)
        assert _alias("session", gi_ref) is not None
    finally:
        gi.close_dbsession()


# ---------------------------------------------------------------------------
# Pass-5 finding 4 — ``close_dbsession`` never raises while tearing down.
# ---------------------------------------------------------------------------


def test_close_dbsession_survives_a_failing_engine_dispose(
    tmp_path, caplog, monkeypatch
):
    """A failing teardown step is logged; the default is released anyway."""
    gi.close_dbsession()
    database = gi.start_dbsession(str(tmp_path / "dispose.db"))

    def boom():
        raise RuntimeError("dispose failed")

    monkeypatch.setattr(database.engine, "dispose", boom)
    with caplog.at_level(logging.INFO, logger="groundinsight"):
        gi.close_dbsession()  # must not raise
    assert database.closed
    assert any("dispose failed" in r.getMessage() for r in caplog.records)
    assert any(
        "Database session closed" in r.getMessage() for r in caplog.records
    )
    with pytest.raises(RuntimeError, match="not started"):
        gi.load_bustypes_from_db()


def test_close_dbsession_removes_a_live_legacy_session(tmp_path):
    """The successor of the "session live, engine missing" half-state: a
    thread-local session handed out through ``gi.session`` is removed too."""
    gi.close_dbsession()
    database = gi.start_dbsession(str(tmp_path / "legacy.db"))
    legacy = _alias("session")
    legacy().execute(__import__("sqlalchemy").text("SELECT 1"))  # open it
    gi.close_dbsession()  # must not raise
    assert database.closed
    assert _alias("session") is None
    assert _alias("db_session") is None


def test_close_dbsession_with_no_state_just_warns(caplog):
    """Calling close on an idle module emits a WARNING, not an exception."""
    gi.close_dbsession()  # make sure nothing is open
    with caplog.at_level(logging.WARNING, logger="groundinsight"):
        gi.close_dbsession()
    assert any(
        "No database session to close" in r.getMessage() for r in caplog.records
    )


# ---------------------------------------------------------------------------
# Pass-5 finding 5 — ``Network.frequencies`` validation.
# ---------------------------------------------------------------------------


def test_network_rejects_duplicate_frequencies():
    with pytest.raises(ValidationError, match="duplicate"):
        gi.create_network(name="dup", frequencies=[50.0, 50.0])


def test_network_rejects_nan_frequency():
    with pytest.raises(ValidationError, match="non-finite"):
        gi.create_network(name="nan", frequencies=[50.0, float("nan")])


def test_network_rejects_inf_frequency():
    with pytest.raises(ValidationError, match="non-finite"):
        gi.create_network(name="inf", frequencies=[50.0, float("inf")])


def test_network_rejects_negative_frequency():
    with pytest.raises(ValidationError, match=">= 0"):
        gi.create_network(name="neg", frequencies=[-1.0, 50.0])


def test_network_rejects_empty_frequency_list():
    with pytest.raises(ValidationError, match="empty"):
        gi.create_network(name="empty", frequencies=[])


def test_network_accepts_zero_frequency_dc():
    """DC (``f = 0``) remains a valid solve frequency."""
    net = gi.create_network(name="dc", frequencies=[0.0, 50.0])
    assert net.frequencies == [0.0, 50.0]


# ---------------------------------------------------------------------------
# Pass-5 finding 6 — ``set_active_fault(..., keep_results=True)``.
# ---------------------------------------------------------------------------


def test_set_active_fault_clears_results_by_default():
    """Historic behaviour: previous result for the fault is dropped."""
    net = _two_bus_network(name="clr")
    # Stub a previous Result so we can observe the clear.
    net.results["F1"] = Result(fault="F1", buses=[], branches=[])
    assert "F1" in net.results
    net.set_active_fault("F1")
    assert "F1" not in net.results, (
        "default set_active_fault should clear the cached Result"
    )


def test_set_active_fault_keeps_results_when_requested():
    net = _two_bus_network(name="kp")
    stub = Result(fault="F1", buses=[], branches=[])
    net.results["F1"] = stub
    net.set_active_fault("F1", keep_results=True)
    assert net.results.get("F1") is stub, (
        "keep_results=True must preserve the previously cached Result"
    )


def test_set_active_fault_raises_for_unknown_fault():
    net = _two_bus_network(name="unknown")
    with pytest.raises(ValueError, match="does not exist"):
        net.set_active_fault("does-not-exist")


# ---------------------------------------------------------------------------
# Pass-5 cross-cutting — pathfinder cache still memoises within one network.
# ---------------------------------------------------------------------------


def test_pathfinder_reuses_graph_for_same_network():
    """The Pass-4 memoisation contract must survive the Pass-5 changes."""
    net = _two_bus_network(name="reuse")
    pf1 = PathFinder(net)
    pf2 = PathFinder(net)
    assert pf1.graph is pf2.graph, (
        "Repeat PathFinder(net) constructions on the same network "
        "must reuse the cached adjacency graph (Pass-4 contract)."
    )

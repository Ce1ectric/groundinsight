# tests/test_audit_pass18_path_enumeration.py
"""
Audit pass 18 -- K2: no full path enumeration in ``phase_current_mode="auto"``.

``run_fault`` used to call ``create_paths`` on every run, and ``define_paths``
enumerates *all* simple paths from every source to every fault by DFS. In the
default ``"auto"`` mode the only thing that path set was ever used for is the
question "does this source reach the fault at all?" -- the phase currents come
from a reduced phase-conductor solve.

The number of simple paths is exponential in the number of independent loops:
a 5x5 grid has 8 512, a 6x6 grid 1 262 816, and the enumeration takes about a
minute while the solver needs milliseconds. Exactly the meshed low-voltage
networks the package is meant for ran into that wall.

``"auto"`` now answers the reachability question with a breadth-first search
(``Network.sources_reaching``), and the enumeration only runs for the legacy
``"paths"`` mode or when the user asks for it with ``gi.create_paths``. A cap
in the ``PathFinder`` turns the remaining runaway case into an error message
instead of a hang.
"""

import time

import pytest

import groundinsight as gi
from groundinsight import pathfinder as pf_module
from groundinsight.pathfinder import PathFinder
from tests._warning_policy import acknowledge

#: The paths mode on parallel routes with default coefficients, on purpose.
PATHS_MODE = acknowledge("PARALLEL_COEFFICIENT_DEFAULT")


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _bus_type():
    return gi.BusType(
        name="bt",
        system_type="Grounded",
        voltage_level=20.0,
        impedance_formula="rho * 0 + 10 + I * f * 0",
    )


def _branch_type():
    return gi.BranchType(
        name="brt",
        grounding_conductor=True,
        self_impedance_formula="(0.25 + j*f*0.012)*l",
        mutual_impedance_formula="(0.0 + j*f*0.012)*l",
        phase_impedance_formula="(0.1 + j*f*0.004)*l",
    )


def _grid(n: int, name: str):
    """An ``n x n`` grid: ``n**2`` buses, ``2*n*(n-1)`` branches, many loops."""
    net = gi.create_network(name=name, frequencies=[50.0])
    bt, brt = _bus_type(), _branch_type()
    for i in range(n):
        for k in range(n):
            gi.create_bus(name=f"b{i}_{k}", type=bt, network=net)
    count = 0
    for i in range(n):
        for k in range(n):
            if i + 1 < n:
                gi.create_branch(
                    name=f"L{count}", type=brt, from_bus=f"b{i}_{k}",
                    to_bus=f"b{i+1}_{k}", length=1.0, network=net,
                )
                count += 1
            if k + 1 < n:
                gi.create_branch(
                    name=f"L{count}", type=brt, from_bus=f"b{i}_{k}",
                    to_bus=f"b{i}_{k+1}", length=1.0, network=net,
                )
                count += 1
    gi.create_source(name="S1", bus="b0_0", values={50.0: 1000.0}, network=net)
    gi.create_fault(
        name="F1", bus=f"b{n-1}_{n-1}", scalings={50.0: 1.0}, network=net
    )
    return net


def _epr(net, fault="F1"):
    return {
        bus.name: {
            freq: complex(value.real, value.imag)
            for freq, value in bus.uepr_freq.items()
        }
        for bus in net.results[fault].buses
    }


# ---------------------------------------------------------------------------
# K2a -- "auto" must not enumerate at all
# ---------------------------------------------------------------------------


def test_auto_mode_never_enumerates_paths(monkeypatch):
    """The deterministic form of the finding: ``find_paths`` is not called.

    Without the fix ``run_fault`` calls ``create_paths`` unconditionally and
    this fails on the very first call.
    """
    calls = []
    original = PathFinder.find_paths

    def spy(self, source_bus_name, fault_bus_name):
        calls.append((source_bus_name, fault_bus_name))
        return original(self, source_bus_name, fault_bus_name)

    monkeypatch.setattr(PathFinder, "find_paths", spy)

    net = _grid(4, "p18_no_enum")
    gi.run_fault(net, "F1")

    assert calls == [], (
        "phase_current_mode='auto' must not enumerate source-to-fault paths"
    )
    assert net.paths == {}


def test_auto_mode_on_a_meshed_grid_is_fast():
    """A 6x6 grid has 1 262 816 simple paths; the solve is milliseconds.

    Measured on the review container: 59.5 s before the fix, 0.14 s after.
    The bound is deliberately generous so a slow CI runner cannot make this
    flaky while still being two orders of magnitude below the old cost.
    """
    net = _grid(6, "p18_grid6")
    start = time.perf_counter()
    gi.run_fault(net, "F1")
    elapsed = time.perf_counter() - start
    assert elapsed < 10.0, f"run_fault on a 6x6 grid took {elapsed:.1f} s"
    assert net.results["F1"].buses


def test_auto_mode_result_is_unchanged_on_a_grid_where_enumeration_is_cheap():
    """Reachability and enumeration must select the same sources.

    On a 4x4 grid the enumeration is still affordable, so the two routes can
    be compared directly: the source set the solver uses in "auto" must be
    exactly the one the enumerated paths would have produced.
    """
    net = _grid(4, "p18_grid4")
    gi.run_fault(net, "F1")
    from_reachability = net.sources_reaching("F1")

    gi.create_paths(net)
    from_paths = [
        name
        for name in net.sources
        if any(
            path.fault == "F1" and path.source == name
            for path in net.paths.values()
        )
    ]
    assert from_reachability == from_paths == ["S1"]


# ---------------------------------------------------------------------------
# K2b -- Network.sources_reaching
# ---------------------------------------------------------------------------


def _two_island_network(name):
    """``s1`` reaches the fault over a line; ``s2`` sits on an island."""
    net = gi.create_network(name=name, frequencies=[50.0])
    bt, brt = _bus_type(), _branch_type()
    for bus in ("a1", "a2", "a3", "x1", "x2"):
        gi.create_bus(name=bus, type=bt, network=net)
    gi.create_branch(
        name="la1", type=brt, from_bus="a1", to_bus="a2", length=1.0, network=net
    )
    gi.create_branch(
        name="la2", type=brt, from_bus="a2", to_bus="a3", length=1.0, network=net
    )
    gi.create_branch(
        name="lx1", type=brt, from_bus="x1", to_bus="x2", length=1.0, network=net
    )
    gi.create_source(name="s1", bus="a1", values={50.0: 1000.0}, network=net)
    gi.create_source(name="s2", bus="x1", values={50.0: 1000.0}, network=net)
    gi.create_fault(name="F1", bus="a3", scalings={50.0: 1.0}, network=net)
    return net


def test_sources_reaching_excludes_a_galvanically_isolated_source():
    net = _two_island_network("p18_island")
    assert net.sources_reaching("F1") == ["s1"]


def test_sources_reaching_agrees_with_the_enumerated_paths():
    net = _two_island_network("p18_island_cmp")
    gi.create_paths(net)
    from_paths = [
        name
        for name in net.sources
        if any(
            path.fault == "F1" and path.source == name
            for path in net.paths.values()
        )
    ]
    assert net.sources_reaching("F1") == from_paths


def test_sources_reaching_follows_the_active_flags():
    net = _two_island_network("p18_island_outage")
    assert net.sources_reaching("F1") == ["s1"]
    net.branches["la2"].active = False
    net.invalidate_paths()
    assert net.sources_reaching("F1") == []


def test_sources_reaching_includes_a_source_on_the_fault_bus():
    net = _two_island_network("p18_island_at_fault")
    gi.create_source(name="s3", bus="a3", values={50.0: 500.0}, network=net)
    assert net.sources_reaching("F1") == ["s1", "s3"]


def test_sources_reaching_rejects_an_unknown_fault():
    net = _two_island_network("p18_island_unknown")
    with pytest.raises(ValueError, match="does not exist"):
        net.sources_reaching("nope")


# ---------------------------------------------------------------------------
# K2c -- the enumeration is still available, and now capped
# ---------------------------------------------------------------------------


def test_create_paths_still_enumerates_for_the_user():
    net = _grid(3, "p18_explicit")
    gi.create_paths(net)
    assert len(net.paths) > 1
    fingerprint_ok = not net.needs_path_rebuild()
    assert fingerprint_ok


@PATHS_MODE
def test_paths_mode_still_enumerates_and_solves():
    net = _grid(3, "p18_paths_mode")
    gi.run_fault(net, "F1", phase_current_mode="paths")
    assert net.paths, "the legacy mode needs the enumerated paths"
    assert net.results["F1"].buses


def test_path_enumeration_is_capped_with_an_actionable_error():
    """A runaway enumeration must be an error message, not a hang.

    The limit is set per network since audit pass 19 (ARC2); the message
    names that setting.
    """
    net = _grid(5, "p18_cap")
    net.pathfinder_settings = gi.PathfinderSettings(max_paths=50)
    with pytest.raises(ValueError, match="exceeded the limit of 50") as excinfo:
        gi.create_paths(net)
    assert "pathfinder_settings" in str(excinfo.value)


def test_path_cap_rejects_a_nonsense_limit():
    with pytest.raises(ValueError):
        gi.PathfinderSettings(max_paths=0)
    with pytest.deprecated_call(), pytest.raises(ValueError):
        pf_module.set_pathfinder_max_paths(0)


# ---------------------------------------------------------------------------
# K2d -- stale paths must not survive a solve
# ---------------------------------------------------------------------------


def test_stale_paths_are_dropped_by_run_fault_in_auto_mode():
    """Nothing downstream may read a path set that no longer matches.

    ``run_fault`` does not rebuild them in "auto" any more, so it must at
    least invalidate them -- persistence, the transient state-space solver
    and the short-circuit aggregation all read ``network.paths``.
    """
    net = _two_island_network("p18_stale")
    gi.create_paths(net)
    assert net.paths
    net.branches["la2"].active = False
    gi.run_fault(net, "F1")
    assert net.paths == {}


def test_fresh_paths_survive_run_fault_in_auto_mode():
    net = _two_island_network("p18_fresh")
    gi.create_paths(net)
    before = dict(net.paths)
    gi.run_fault(net, "F1")
    assert net.paths == before


def test_run_fault_still_rejects_a_network_without_sources():
    """The excitation guard used to live in ``create_paths``.

    ``run_fault`` no longer goes through ``create_paths`` in the default
    mode, so the guard had to move or it would have been lost.
    """
    net = gi.create_network(name="p18_unexcited", frequencies=[50.0])
    gi.create_bus(name="b1", type=_bus_type(), network=net)
    gi.create_fault(name="F1", bus="b1", scalings={50.0: 1.0}, network=net)
    with pytest.raises(ValueError, match="no sources"):
        gi.run_fault(net, "F1")


def test_run_fault_still_rejects_a_network_without_faults():
    net = gi.create_network(name="p18_unfaulted", frequencies=[50.0])
    gi.create_bus(name="b1", type=_bus_type(), network=net)
    gi.create_source(name="s1", bus="b1", values={50.0: 1.0}, network=net)
    net.active_fault = "F1"
    with pytest.raises(ValueError):
        gi.run_fault(net, "F1")


# ---------------------------------------------------------------------------
# K2e -- the physics is untouched
# ---------------------------------------------------------------------------


def test_islanded_source_contributes_nothing_in_auto_mode():
    """The reachability filter must have the same effect the path filter had."""
    net = _two_island_network("p18_island_physics")
    gi.run_fault(net, "F1")
    epr = _epr(net)
    assert abs(epr["x1"][50.0]) == pytest.approx(0.0, abs=1e-12)
    assert abs(epr["x2"][50.0]) == pytest.approx(0.0, abs=1e-12)
    assert abs(epr["a3"][50.0]) > 1.0


def test_shortcircuit_source_selection_still_excludes_an_isolated_source():
    """``sources_feeding`` used to fall back to *every* source on an active
    bus once ``network.paths`` was empty -- which is now the normal state in
    the default mode. It asks for reachability instead.
    """
    from groundinsight.analysis.shortcircuit import sources_feeding

    net = _two_island_network("p18_sc_sources")
    gi.run_fault(net, "F1")
    assert net.paths == {}
    assert sources_feeding(net, "F1") == ["s1"]

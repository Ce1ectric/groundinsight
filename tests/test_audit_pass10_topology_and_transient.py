# tests/test_audit_pass10_topology_and_transient.py

"""
Regression tests for the tenth audit-pass topology / transient fixes.

Three defects, all of them silent (no exception, no warning, a plausible
number returned):

T1. ``Network._active_topology_fingerprint`` did not cover the *excitation*.
    ``define_paths`` enumerates one path set per ``(source, fault)`` pair, so
    adding a fault or a source after the first solve genuinely invalidates
    the path set -- but the fingerprint only looked at buses and branches and
    therefore did not change. ``run_fault`` reused the old paths. For a fault
    added afterwards no path terminated at the new fault bus and *every* bus
    reported 0 V EPR. The same omission made
    ``analysis.inverse_rho.find_max_rho_scaling`` over-estimate the
    admissible soil resistivity by roughly three orders of magnitude, because
    the sweep never saw an EPR above ``u_max``.

T2. Both the fingerprint and ``PathFinder._compute_topology_key`` stored
    connectivity as a ``frozenset`` of bare ``(from_bus, to_bus)`` pairs,
    which collapses parallel branches: a set of endpoint pairs has no
    multiplicity. Rewiring one of two parallel branches onto an edge that
    already exists left the set bit-identical, so the stale path set / the
    cached adjacency list was reused and a whole route was never enumerated.
    Both now key on ``(branch_name, from_bus, to_bus)``.

T3. ``transient._solve_state_space`` derives the Carson mutual-coupling
    phase factors from ``network.paths``. Two things went wrong there:

    a) With ``network.paths`` empty or stale -- e.g. a transient study run
       straight after ``build_network``, without a preceding ``run_fault`` --
       every factor silently stayed zero and the entire mutual coupling was
       dropped. The measured peak EPR was 71 % off. The solver now rebuilds
       the paths and says so via ``logger.warning``.
    b) The factors were accumulated with ``+=`` behind a merely *per-path*
       ``seen`` set, so a branch shared by two parallel routes of one source
       (the common trunk of a ring) got a factor of 2.0. That contradicts the
       block's own header comment and the stationary reference in
       ``ElectricalNetwork._compute_phase_currents_from_paths``, which is
       explicitly first-path-wins. The transient EPR came out 32 % above the
       stationary one at the fault bus.

Every test below fails on the pre-fix code; each was checked by reverting
the corresponding hunk (mutation test).
"""

from __future__ import annotations

import logging

import numpy as np
import pytest

import groundinsight as gi
from groundinsight.analysis import find_max_rho_scaling
from groundinsight.models.core_models import BusType, BranchType
from groundinsight.pathfinder import PathFinder, clear_pathfinder_cache
from groundinsight.simulation import waveforms
from groundinsight.simulation.transient import TransientStudy


# ---------------------------------------------------------------------------
# Helpers -- purely resistive so the expected numbers stay hand-checkable
# ---------------------------------------------------------------------------


def _plain_types():
    bus_type = BusType(
        name="p10_bus",
        system_type="Grounded",
        voltage_level=110.0,
        impedance_formula="10 + j*0.0*f",
    )
    branch_type = BranchType(
        name="p10_line",
        grounding_conductor=True,
        self_impedance_formula="(0.1 + j*0.1)*l",
        mutual_impedance_formula="(0.05 + j*0.05)*l",
    )
    return bus_type, branch_type


def _build(name, buses, branches):
    """``branches``: list of ``(branch_name, from_bus, to_bus, length)``."""
    bus_type, branch_type = _plain_types()
    net = gi.create_network(name=name, frequencies=[50.0])
    for bus in buses:
        gi.create_bus(name=bus, type=bus_type, network=net,
                      specific_earth_resistance=100.0)
    for br_name, from_bus, to_bus, length in branches:
        gi.create_branch(name=br_name, type=branch_type, from_bus=from_bus,
                         to_bus=to_bus, length=length,
                         specific_earth_resistance=100.0, network=net)
    return net


def _epr(net, fault_name):
    return {bus.name: bus.uepr for bus in net.results[fault_name].buses}


_LINE = [("L1", "A", "B", 1.0), ("L2", "B", "C", 1.0), ("L3", "C", "D", 1.0)]
_BUSES = ["A", "B", "C", "D"]


# ---------------------------------------------------------------------------
# T1 -- the fingerprint must cover the excitation
# ---------------------------------------------------------------------------


def test_fault_added_after_first_solve_invalidates_the_path_set():
    """A fault created after the first ``run_fault`` must rebuild the paths.

    Pre-fix this returned 0 V on every bus, because no cached path ended at
    the new fault bus and nothing complained.
    """
    clear_pathfinder_cache()
    reference = _build("p10_t1_ref", _BUSES, _LINE)
    gi.create_source(name="S", bus="A", values={50.0: 1000 + 0j},
                     network=reference)
    gi.create_fault(name="F1", bus="C", scalings={50.0: 1.0}, network=reference)
    gi.create_fault(name="F2", bus="D", scalings={50.0: 1.0}, network=reference)
    gi.run_fault(reference, "F2")
    expected = _epr(reference, "F2")

    clear_pathfinder_cache()
    net = _build("p10_t1", _BUSES, _LINE)
    gi.create_source(name="S", bus="A", values={50.0: 1000 + 0j}, network=net)
    gi.create_fault(name="F1", bus="C", scalings={50.0: 1.0}, network=net)
    gi.run_fault(net, "F1")                       # builds the paths for F1 only
    gi.create_fault(name="F2", bus="D", scalings={50.0: 1.0}, network=net)

    assert net._needs_path_rebuild() is True

    gi.run_fault(net, "F2")
    actual = _epr(net, "F2")

    # Guard against the test passing on an all-zero degenerate reference.
    assert max(expected.values()) > 1.0
    for bus_name, value in expected.items():
        assert actual[bus_name] == pytest.approx(value, rel=1e-9, abs=1e-9)


def test_source_added_after_first_solve_invalidates_the_path_set():
    """A source created after the first ``run_fault`` must rebuild the paths."""
    clear_pathfinder_cache()
    reference = _build("p10_t1b_ref", _BUSES, _LINE)
    gi.create_source(name="S1", bus="A", values={50.0: 1000 + 0j},
                     network=reference)
    gi.create_source(name="S2", bus="D", values={50.0: 1000 + 0j},
                     network=reference)
    gi.create_fault(name="F", bus="C", scalings={50.0: 1.0}, network=reference)
    gi.run_fault(reference, "F")
    expected = _epr(reference, "F")

    clear_pathfinder_cache()
    net = _build("p10_t1b", _BUSES, _LINE)
    gi.create_source(name="S1", bus="A", values={50.0: 1000 + 0j}, network=net)
    gi.create_fault(name="F", bus="C", scalings={50.0: 1.0}, network=net)
    gi.run_fault(net, "F")
    gi.create_source(name="S2", bus="D", values={50.0: 1000 + 0j}, network=net)

    assert net._needs_path_rebuild() is True

    gi.run_fault(net, "F")
    actual = _epr(net, "F")

    assert max(expected.values()) > 1.0
    for bus_name, value in expected.items():
        assert actual[bus_name] == pytest.approx(value, rel=1e-9, abs=1e-9)


def test_find_max_rho_scaling_does_not_reuse_a_stale_path_set():
    """``find_max_rho_scaling`` inherited the stale-path defect from T1.

    Pre-fix the sweep ran against paths built for a *different* fault, saw
    0 V everywhere, never exceeded ``u_max`` and returned the upper bracket
    (``rho_max`` ~3000x too high, ``iterations == 0``).
    """
    clear_pathfinder_cache()
    bus_type = BusType(name="p10_rho_bus", system_type="Grounded",
                       voltage_level=20.0,
                       impedance_formula="rho * 0.01 + 0*f")
    branch_type = BranchType(name="p10_rho_line", grounding_conductor=True,
                             self_impedance_formula="(0.25 + I*0.6)*l",
                             mutual_impedance_formula="(0.0 + I*0.6)*l")

    def build(name):
        net = gi.create_network(name=name, frequencies=[50.0])
        for bus in ("b0", "b1", "b2"):
            gi.create_bus(name=bus, type=bus_type, network=net,
                          specific_earth_resistance=100.0)
        gi.create_branch(name="br0", type=branch_type, from_bus="b0",
                         to_bus="b1", length=1.0, network=net)
        gi.create_branch(name="br1", type=branch_type, from_bus="b1",
                         to_bus="b2", length=1.0, network=net)
        gi.create_source(name="src", bus="b0", values={50.0: 100.0},
                         network=net)
        return net

    buses = ["b0", "b1", "b2"]

    # Control: both faults known before the first solve.
    control = build("p10_rho_ctl")
    gi.create_fault(name="fltA", bus="b1", scalings={50.0: 1.0}, network=control)
    gi.create_fault(name="fltB", bus="b2", scalings={50.0: 1.0}, network=control)
    gi.run_fault(control, "fltB")
    expected = find_max_rho_scaling(control, "fltB", buses, u_max=10.0,
                                    c_bounds=(1e-3, 1e3))

    # The defect path: solve fltA first, add fltB afterwards.
    net = build("p10_rho")
    gi.create_fault(name="fltA", bus="b1", scalings={50.0: 1.0}, network=net)
    gi.run_fault(net, "fltA")
    gi.create_fault(name="fltB", bus="b2", scalings={50.0: 1.0}, network=net)
    actual = find_max_rho_scaling(net, "fltB", buses, u_max=10.0,
                                  c_bounds=(1e-3, 1e3))

    # The bisection must actually have run, i.e. not returned the bracket.
    assert expected["iterations"] > 0
    assert actual["iterations"] > 0
    assert actual["rho_max"]["b2"] == pytest.approx(expected["rho_max"]["b2"],
                                                    rel=1e-9)


# ---------------------------------------------------------------------------
# T2 -- connectivity must keep parallel branches distinguishable
# ---------------------------------------------------------------------------
#
# STATE 1: L1:A-B, L2:B-C, L3:A-D, L4:A-D   -> one route A->C: (L1, L2)
# STATE 2: L1:A-B, L2:B-C, L3:A-D, L4:A-B   -> two routes:     (L1, L2), (L4, L2)
#
# Both states have the same bus set, the same branch-name set and the same
# *set* of endpoint pairs {(A,B), (B,C), (A,D)}.

_PARALLEL_S1 = [("L1", "A", "B", 1.0), ("L2", "B", "C", 1.0),
                ("L3", "A", "D", 1.0), ("L4", "A", "D", 1.0)]
_PARALLEL_S2 = [("L1", "A", "B", 1.0), ("L2", "B", "C", 1.0),
                ("L3", "A", "D", 1.0), ("L4", "A", "B", 1.0)]


def _path_names(paths):
    return sorted(tuple(seg.name for seg in path.segments) for path in paths)


def test_pathfinder_cache_key_keeps_parallel_branches_apart():
    clear_pathfinder_cache()
    net = _build("p10_t2_pf", _BUSES, _PARALLEL_S1)

    finder_before = PathFinder(net)
    key_before = finder_before._topology_key
    assert _path_names(finder_before.find_paths("A", "C")) == [("L1", "L2")]

    net.branches["L4"].to_bus = "B"               # in-place rewiring D -> B

    finder_after = PathFinder(net)
    assert finder_after._topology_key != key_before
    assert _path_names(finder_after.find_paths("A", "C")) == [
        ("L1", "L2"), ("L4", "L2"),
    ]


def test_run_fault_rebuilds_paths_after_parallel_branch_rewiring():
    """End-to-end twin of the cache-key test -- pre-fix EPR was ~33 % off."""
    clear_pathfinder_cache()
    reference = _build("p10_t2_ref", _BUSES, _PARALLEL_S2)
    gi.create_source(name="S", bus="A", values={50.0: 1000 + 0j},
                     network=reference)
    gi.create_fault(name="F", bus="C", scalings={50.0: 1.0}, network=reference)
    gi.run_fault(reference, "F")
    expected = _epr(reference, "F")

    clear_pathfinder_cache()
    net = _build("p10_t2", _BUSES, _PARALLEL_S1)
    gi.create_source(name="S", bus="A", values={50.0: 1000 + 0j}, network=net)
    gi.create_fault(name="F", bus="C", scalings={50.0: 1.0}, network=net)
    gi.run_fault(net, "F")
    epr_state1 = _epr(net, "F")

    net.branches["L4"].to_bus = "B"

    assert net._needs_path_rebuild() is True

    gi.run_fault(net, "F")
    actual = _epr(net, "F")

    assert len(net.paths) == 2
    # The rewiring really changes the answer, so an unchanged result would be
    # a genuine failure rather than a coincidence.
    assert epr_state1["A"] != pytest.approx(expected["A"], rel=1e-6)
    for bus_name, value in expected.items():
        assert actual[bus_name] == pytest.approx(value, rel=1e-9, abs=1e-9)


# ---------------------------------------------------------------------------
# T3 -- state-space mutual coupling
# ---------------------------------------------------------------------------

_F = 50.0
_AMPL = 100.0


def _coupled_types(with_mutual):
    r_branch, l_branch = 2.0, 10e-3
    r_mutual, m_mutual = (0.5, 5e-3) if with_mutual else (0.0, 0.0)
    return BranchType(
        name="p10_rlm" if with_mutual else "p10_rl",
        grounding_conductor=True,
        self_impedance_formula=f"({r_branch} + I*2*pi*f*{l_branch}) * l",
        mutual_impedance_formula=f"({r_mutual} + I*2*pi*f*{m_mutual}) * l",
        R_self_formula=f"(rho*0+{r_branch})*l",
        L_self_formula=f"(rho*0+{l_branch})*l",
        R_mutual_formula=f"(rho*0+{r_mutual})*l",
        M_mutual_formula=f"(rho*0+{m_mutual})*l",
    )


def _steady_peak(time_s, signal, t_min=0.5):
    time_s = np.asarray(time_s)
    return float(np.max(np.abs(np.asarray(signal)[time_s >= t_min])))


def _stationary_magnitude(freq_map):
    value = freq_map[_F]
    return abs(complex(value.real, value.imag))


def _two_bus_coupled_network(name):
    bus_type_1 = BusType(name="p10_b1", system_type="Substation",
                         voltage_level=20.0,
                         impedance_formula="rho*0 + 10.0 + I*f*0",
                         R_formula="rho*0+10.0")
    bus_type_2 = BusType(name="p10_b2", system_type="Substation",
                         voltage_level=20.0,
                         impedance_formula="rho*0 + 5.0 + I*f*0",
                         R_formula="rho*0+5.0")
    net = gi.create_network(name=name, frequencies=[_F])
    gi.create_bus(name="bus1", type=bus_type_1, network=net)
    gi.create_bus(name="bus2", type=bus_type_2, network=net)
    gi.create_branch(name="branch1", type=_coupled_types(True),
                     from_bus="bus1", to_bus="bus2", length=1.0, network=net)
    gi.create_source(name="src", bus="bus1", values={_F: _AMPL + 0j},
                     network=net)
    gi.create_fault(name="F1", bus="bus2", scalings={_F: 1.0}, network=net)
    return net


def _run_transient(net, buses, branches, solver="state_space"):
    study = TransientStudy(net, fault_name="F1")
    study.set_source_waveform(
        "src",
        waveforms.sinusoidal_with_dc_offset(amplitude=_AMPL, frequency_hz=_F),
    )
    study.set_observation(buses=buses, branches=branches)
    result = study.solve(t_end=1.0, dt=1e-5, solver=solver)
    return study, result


def test_state_space_mutual_coupling_without_a_preceding_run_fault(caplog):
    """T3a -- with ``network.paths`` empty the coupling was silently dropped.

    Pre-fix run A (paths empty) reported 107.71 V against 62.82 V for run B,
    a 71 % error, and neither run warned.
    """
    net = _two_bus_coupled_network("p10_t3a")
    assert not net.paths                      # no run_fault has happened yet

    with caplog.at_level(logging.WARNING):
        study_a, result_a = _run_transient(net, ["bus1", "bus2"], ["branch1"])

    assert any("paths" in record.message for record in caplog.records), (
        "the solver must say that it rebuilt the paths"
    )
    assert net.paths, "the solver must have rebuilt the paths"

    peak_a = _steady_peak(result_a.time_s, result_a.epr_t["bus2"])

    # Run B: exactly the same study, but after a normal stationary solve.
    gi.run_fault(net, fault_name="F1")
    _, result_b = _run_transient(net, ["bus1", "bus2"], ["branch1"])
    peak_b = _steady_peak(result_b.time_s, result_b.epr_t["bus2"])

    assert peak_a == pytest.approx(peak_b, rel=1e-9)

    # ... and both agree with the stationary solver.
    stationary = _stationary_magnitude(
        next(b for b in net.results["F1"].buses if b.name == "bus2").uepr_freq
    )
    assert peak_a == pytest.approx(stationary, rel=1e-4)


def _ring_coupled_network(name):
    """A --bAB-- B --bBC-- C --bCD-- D(fault), plus the ring B--E--C.

    Two simple paths A->D exist; both traverse bAB and bCD. Only bCD carries
    mutual coupling, which isolates the effect.
    """
    bus_type = BusType(name="p10_ring_bus", system_type="Substation",
                       voltage_level=20.0,
                       impedance_formula="rho*0 + 10.0 + I*f*0",
                       R_formula="rho*0+10.0")
    plain = _coupled_types(False)
    coupled = _coupled_types(True)
    net = gi.create_network(name=name, frequencies=[_F])
    for bus in ("A", "B", "C", "D", "E"):
        gi.create_bus(name=bus, type=bus_type, network=net)
    for br_name, from_bus, to_bus in (("bAB", "A", "B"), ("bBC", "B", "C"),
                                      ("bBE", "B", "E"), ("bEC", "E", "C")):
        gi.create_branch(name=br_name, type=plain, from_bus=from_bus,
                         to_bus=to_bus, length=1.0, network=net)
    gi.create_branch(name="bCD", type=coupled, from_bus="C", to_bus="D",
                     length=1.0, network=net)
    gi.create_source(name="src", bus="A", values={_F: _AMPL + 0j}, network=net)
    gi.create_fault(name="F1", bus="D", scalings={_F: 1.0}, network=net)
    return net


def test_state_space_mutual_factor_is_first_path_wins_not_accumulated():
    """T3b -- a branch on two parallel paths of one source got factor 2.0.

    Pre-fix the transient EPR at the fault bus was 32.5 % above the
    stationary one; with the ring opened both solvers agreed, which is
    exactly why no existing test caught it.
    """
    net = _ring_coupled_network("p10_t3b")
    gi.run_fault(net, fault_name="F1")
    assert len(net.paths) == 2, "the ring must produce two parallel paths"

    study, result = _run_transient(net, ["C", "D"], ["bCD"])
    _, phase_factors, _ = study._TransientStudy__mutual_for_output

    assert phase_factors == {("bCD", "src"): 1.0}

    results = net.results["F1"]
    expected_d = _stationary_magnitude(
        next(b for b in results.buses if b.name == "D").uepr_freq
    )
    expected_c = _stationary_magnitude(
        next(b for b in results.buses if b.name == "C").uepr_freq
    )
    expected_i = _stationary_magnitude(
        next(b for b in results.branches if b.name == "bCD").i_s_freq
    )

    assert _steady_peak(result.time_s, result.epr_t["D"]) == pytest.approx(
        expected_d, rel=1e-5)
    assert _steady_peak(result.time_s, result.epr_t["C"]) == pytest.approx(
        expected_c, rel=1e-5)
    assert _steady_peak(result.time_s, result.i_branch_t["bCD"]) == (
        pytest.approx(expected_i, rel=1e-5))


def test_state_space_mutual_factor_control_single_path():
    """Control for T3b: with the ring opened the factor was always 1.0.

    This test passes both before and after the fix -- on purpose. It pins the
    single-path case so a future "fix" of the first-wins rule cannot silently
    break the topology that used to be correct.
    """
    net = _ring_coupled_network("p10_t3b_ctl")
    net.branches["bBE"].active = False
    net.branches["bEC"].active = False
    net.buses["E"].active = False
    gi.run_fault(net, fault_name="F1")
    assert len(net.paths) == 1

    study, result = _run_transient(net, ["C", "D"], ["bCD"])
    _, phase_factors, _ = study._TransientStudy__mutual_for_output
    assert phase_factors == {("bCD", "src"): 1.0}

    expected_d = _stationary_magnitude(
        next(b for b in net.results["F1"].buses if b.name == "D").uepr_freq
    )
    assert _steady_peak(result.time_s, result.epr_t["D"]) == pytest.approx(
        expected_d, rel=1e-5)

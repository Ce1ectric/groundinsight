# tests/test_audit_pass9_fixes.py

"""
Regression tests for the ninth audit-pass bug-fix batch (2026-07-19).

Covers the mid-severity findings B1–B5 and C1/C3/C4:

B1. ``run_fault`` reused stale paths after an in-place ``active`` flip or a
    rewiring (it only rebuilt when ``network.paths`` was empty). It now
    rebuilds whenever the active-topology fingerprint changed.
B2. The state-space transient solver applied ``fault.scalings`` to the
    mutual-coupling phase current but not to the injection, so the result
    moved with ``scalings`` even though the source waveform is unchanged.
    Transient solvers now treat the waveform as the literal injection.
B3. ``find_max_rho_scaling`` / ``evaluate_max_epr_under_k`` left the network
    mutated (dangling ``active_fault``, corrupted/leaked ``results``). They
    now restore that state in ``finally``.
B4. ``Network.results`` were dropped by the SQLite round-trip (JSON kept
    them). They are now persisted as a JSON column, matching the JSON path.
B5. Duplicate pandapower line names aborted ``from_pandapower`` while the
    preview reported both as ``keep``. Line names are now disambiguated.
C1. ``Fault.active`` was not restored on JSON load (DB path was fine).
C3. ``clear_pathfinder_cache`` over-cleared same-named live networks; the
    topology key now includes connectivity.
C4. A singular / floating network raised a raw ``RuntimeError`` from
    ``splu``; ``solve_network`` now raises a clear ``ValueError``.
"""

from __future__ import annotations

import numpy as np
import pytest

import groundinsight as gi
from groundinsight.analysis import find_max_rho_scaling, evaluate_max_epr_under_k
from groundinsight.models.core_models import Branch, BusType, BranchType
from groundinsight.simulation import waveforms
from groundinsight.simulation.transient import TransientStudy


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _rms_epr(net, fault, bus):
    df = net.res_buses(fault)
    row = df.filter((df["bus_name"] == bus) & (df["frequency_Hz"] == "RMS"))
    return row["EPR_V"][0]


def _ring_network():
    """b1-b2-b3 plus a direct b1-b3 branch (a triangle / ring)."""
    net = gi.create_network(name="pass9_ring", frequencies=[50.0])
    bt = gi.BusType(name="b", system_type="s", voltage_level=20.0,
                    impedance_formula="rho*0 + 2.0")
    ct = gi.BranchType(name="c", grounding_conductor=True,
                       self_impedance_formula="(rho*0 + 0.5 + j*f*0.01)*l",
                       mutual_impedance_formula="(rho*0 + 0.1 + j*f*0.01)*l")
    for b in ("b1", "b2", "b3"):
        gi.create_bus(name=b, type=bt, network=net)
    gi.create_branch(name="b12", type=ct, from_bus="b1", to_bus="b2", length=5.0, network=net)
    gi.create_branch(name="b23", type=ct, from_bus="b2", to_bus="b3", length=5.0, network=net)
    gi.create_branch(name="b13", type=ct, from_bus="b1", to_bus="b3", length=5.0, network=net)
    gi.create_source(name="src", bus="b1", values={50.0: 1000.0}, network=net)
    gi.create_fault(name="F", bus="b3", scalings={50.0: 1.0}, network=net)
    return net


# ---------------------------------------------------------------------------
# B1 — run_fault detects topology change and rebuilds paths
# ---------------------------------------------------------------------------


def test_run_fault_rebuilds_paths_after_manual_active_flip():
    net = _ring_network()
    gi.run_fault(net, "F")
    epr_full = _rms_epr(net, "F", "b3")

    # Deactivate the direct b1-b3 branch WITHOUT invalidating paths.
    net.branches["b13"].active = False
    gi.run_fault(net, "F")
    epr_reused = _rms_epr(net, "F", "b3")

    # Reference: the same reduced topology solved from a clean path cache.
    net.invalidate_paths()
    gi.run_fault(net, "F")
    epr_reference = _rms_epr(net, "F", "b3")

    assert epr_reused == pytest.approx(epr_reference)   # no stale-path error
    assert epr_reused != pytest.approx(epr_full)        # topology really changed


# ---------------------------------------------------------------------------
# B2 — transient solver ignores fault.scalings (waveform is the injection)
# ---------------------------------------------------------------------------


def _mutual_state_space_network(name):
    bus_type = BusType(
        name="bus_for_mut", system_type="Substation", voltage_level=20.0,
        impedance_formula="rho * 0 + 5", R_formula="rho * 0 + 5",
    )
    branch_type = BranchType(
        name="with_mut", grounding_conductor=True,
        self_impedance_formula="((rho * 0 + 0.5) + I * 2 * pi * f * 5e-3) * l",
        mutual_impedance_formula="((rho * 0 + 0.05) + I * 2 * pi * f * 2e-3) * l",
        R_self_formula="(rho * 0 + 0.5) * l", L_self_formula="(rho * 0 + 5e-3) * l",
        R_mutual_formula="(rho * 0 + 0.05) * l", M_mutual_formula="(rho * 0 + 2e-3) * l",
    )
    net = gi.create_network(name=name, frequencies=[50.0])
    gi.create_bus(name="b1", type=bus_type, network=net)
    gi.create_bus(name="b2", type=bus_type, network=net)
    gi.create_branch(name="link", type=branch_type, from_bus="b1", to_bus="b2",
                     length=1.0, network=net)
    gi.create_source(name="src", bus="b1", values={50.0: 100.0}, network=net)
    gi.create_fault(name="F1", bus="b2", scalings={50.0: 1.0}, network=net)
    gi.create_paths(network=net)
    return net


def test_state_space_transient_invariant_to_fault_scalings():
    net = _mutual_state_space_network("pass9_ss_scalings")
    study = TransientStudy(net, fault_name="F1")
    study.set_source_waveform(
        "src",
        waveforms.sinusoidal_with_dc_offset(amplitude=100.0, frequency_hz=50.0, t_on=0.0),
    )
    study.set_observation(buses=["b2"], branches=["link"])
    r1 = study.solve(t_end=0.2, dt=2e-4, solver="state_space")

    # Doubling the fault scaling must NOT move the transient: the waveform is
    # the literal injection. (Before the fix the mutual term scaled by 2.)
    net.faults["F1"].scalings = {50.0: 2.0}
    r2 = study.solve(t_end=0.2, dt=2e-4, solver="state_space")

    np.testing.assert_allclose(r1.i_branch_t["link"], r2.i_branch_t["link"], atol=1e-9)
    np.testing.assert_allclose(r1.epr_t["b2"], r2.epr_t["b2"], atol=1e-9)
    # sanity: the shield current is actually non-trivial (mutual path exercised)
    assert np.max(np.abs(r1.i_branch_t["link"])) > 1.0


# ---------------------------------------------------------------------------
# B3 — inverse routines restore network state
# ---------------------------------------------------------------------------


def _inverse_demo_network():
    net = gi.create_network(name="pass9_inv", frequencies=[50.0])
    bt = BusType(name="BT", system_type="Grounded", voltage_level=20.0,
                 impedance_formula="rho * 0.01 + I * f * 0")
    brt = BranchType(name="BRT", grounding_conductor=True,
                     self_impedance_formula="(0.25 + I*0.6)*l",
                     mutual_impedance_formula="(0.0 + I*0.6)*l")
    gi.create_bus(name="b0", type=bt, network=net)
    gi.create_bus(name="b1", type=bt, network=net)
    gi.create_branch(name="br", type=brt, from_bus="b0", to_bus="b1", length=1.0, network=net)
    gi.create_source(name="src", bus="b0", values={50.0: 100.0}, network=net)
    return net


def test_find_max_rho_scaling_restores_state_on_fresh_network():
    net = _inverse_demo_network()
    gi.create_fault(name="flt", bus="b1", scalings={50.0: 1.0}, network=net)
    assert net.active_fault is None and net.results == {}

    find_max_rho_scaling(net, "flt", ["b0", "b1"], u_max=200.0)

    assert net.active_fault is None            # not left dangling
    assert net.results == {}                   # no stale probe result
    assert net.buses["b0"].specific_earth_resistance == 100.0  # rho restored


def test_evaluate_max_epr_under_k_preserves_existing_fault_result():
    net = _inverse_demo_network()
    gi.create_fault(name="user_flt", bus="b1", scalings={50.0: 1.0}, network=net)
    gi.run_fault(net, "user_flt")
    true_epr = _rms_epr(net, "user_flt", "b1")

    # Sweep with a much larger k -> would overwrite results['user_flt'] before fix.
    evaluate_max_epr_under_k(net, ["b0", "b1"], k=(1.0, 0.0, 0.0, 0.0, 0.0))

    assert net.active_fault == "user_flt"                 # restored
    assert net.faults["user_flt"].active is True          # flag re-synced
    assert _rms_epr(net, "user_flt", "b1") == pytest.approx(true_epr)  # not corrupted
    assert not any(f.startswith("_inv_rhof_") for f in net.faults)     # no temp leak


def test_evaluate_max_epr_under_k_clears_active_fault_on_fresh_network():
    net = _inverse_demo_network()
    assert net.active_fault is None
    evaluate_max_epr_under_k(net, ["b0", "b1"], k=(0.01, 0.0, 0.0, 0.0, 0.0))
    assert net.active_fault is None
    assert net.faults == {}          # every temp fault removed
    assert net.results == {}


# ---------------------------------------------------------------------------
# B4 — SQLite round-trip keeps Network.results
# ---------------------------------------------------------------------------


def test_results_survive_sqlite_roundtrip(tmp_path):
    from groundinsight.database.crud import save_network, load_network

    net = _ring_network()
    gi.run_fault(net, "F")
    epr = _rms_epr(net, "F", "b3")

    db = str(tmp_path / "pass9.db")
    gi.start_dbsession(db)
    try:
        save_network(net, gi.session, overwrite=True)
        loaded = load_network(net.name, gi.session)
    finally:
        gi.close_dbsession()

    assert "F" in loaded.results
    reloaded_epr = next(rb.uepr for rb in loaded.results["F"].buses if rb.name == "b3")
    assert reloaded_epr == pytest.approx(epr)


# ---------------------------------------------------------------------------
# C1 — Fault.active restored on JSON load
# ---------------------------------------------------------------------------


def test_fault_active_flag_survives_json_roundtrip():
    net = _ring_network()
    net.set_active_fault("F")
    assert net.faults["F"].active is True

    reloaded = gi.Network.model_validate_json(net.model_dump_json())
    assert reloaded.active_fault == "F"
    assert reloaded.faults["F"].active is True      # was False before the fix


# ---------------------------------------------------------------------------
# C3 — pathfinder cache scoping + connectivity in the key
# ---------------------------------------------------------------------------


def test_clear_pathfinder_cache_does_not_evict_same_named_network():
    from groundinsight.pathfinder import clear_pathfinder_cache, _GRAPH_CACHE

    clear_pathfinder_cache()  # start clean
    net_a = _ring_network()
    net_b = _ring_network()   # same .name as net_a
    gi.run_fault(net_a, "F")
    gi.run_fault(net_b, "F")

    assert any(k[0] == id(net_b) for k in _GRAPH_CACHE)
    net_a.invalidate_paths()  # scoped clear of A only
    assert not any(k[0] == id(net_a) for k in _GRAPH_CACHE)
    assert any(k[0] == id(net_b) for k in _GRAPH_CACHE)   # B survives


def test_topology_key_reflects_rewiring():
    from groundinsight.pathfinder import PathFinder

    net = _ring_network()
    key_before = PathFinder(net)._topology_key
    # Rewire b13 from b1-b3 to b1-b2 in place (same name, same counts, same active set).
    net.add_branch(
        Branch(name="b13", type=net.branches["b13"].type, length=5.0,
               from_bus="b1", to_bus="b2", self_impedance={}, mutual_impedance={}),
        overwrite=True,
    )
    key_after = PathFinder(net)._topology_key
    assert key_before != key_after     # connectivity is part of the key now


# ---------------------------------------------------------------------------
# C4 — singular / floating network raises a clear ValueError
# ---------------------------------------------------------------------------


def test_floating_network_raises_clear_valueerror():
    net = gi.create_network(name="pass9_float", frequencies=[50.0])
    bt = gi.BusType(name="open", system_type="s", voltage_level=20.0,
                    impedance_formula="nan")     # inf -> zero ground admittance
    ct = gi.BranchType(name="c", grounding_conductor=True,
                       self_impedance_formula="(0.25 + j*f*0.012)*l",
                       mutual_impedance_formula="(0.0 + j*f*0.012)*l")
    gi.create_bus(name="b1", type=bt, network=net)
    gi.create_bus(name="b2", type=bt, network=net)
    gi.create_branch(name="c1", type=ct, from_bus="b1", to_bus="b2", length=5.0, network=net)
    gi.create_source(name="src", bus="b1", values={50.0: 1000.0}, network=net)
    gi.create_fault(name="F", bus="b2", scalings={50.0: 1.0}, network=net)

    with pytest.raises(ValueError, match="[Ss]ingular|reference earth"):
        gi.run_fault(net, "F")


def test_singular_solution_nonfinite_raises(monkeypatch):
    """Some scipy versions return a non-finite solution for a singular Y
    instead of raising ``RuntimeError`` (observed on scipy under Python 3.14).
    ``solve_network`` must surface a clear ``ValueError`` in that case too."""
    import groundinsight.electrical_network as en_mod

    net = gi.create_network(name="pass9_nf", frequencies=[50.0])
    bt = gi.BusType(name="b", system_type="s", voltage_level=20.0,
                    impedance_formula="rho*0 + 1.0")
    ct = gi.BranchType(name="c", grounding_conductor=True,
                       self_impedance_formula="(0.25 + j*f*0.012)*l",
                       mutual_impedance_formula="(0.0 + j*f*0.012)*l")
    gi.create_bus(name="b1", type=bt, network=net)
    gi.create_bus(name="b2", type=bt, network=net)
    gi.create_branch(name="c1", type=ct, from_bus="b1", to_bus="b2", length=5.0, network=net)
    gi.create_source(name="src", bus="b1", values={50.0: 1000.0}, network=net)
    gi.create_fault(name="F", bus="b2", scalings={50.0: 1.0}, network=net)

    class _FakeLU:
        def solve(self, b):
            out = np.asarray(b, dtype=complex).copy()
            out[:] = np.inf          # emulate a non-raising singular factorisation
            return out

    monkeypatch.setattr(en_mod, "splu", lambda *a, **k: _FakeLU())
    with pytest.raises(ValueError, match="[Ss]ingular|reference earth"):
        gi.run_fault(net, "F")


# ---------------------------------------------------------------------------
# B5 — duplicate pandapower line names are disambiguated (preview == commit)
# ---------------------------------------------------------------------------


def test_duplicate_pandapower_line_names_import_cleanly():
    pp = pytest.importorskip("pandapower")
    from groundinsight.io import from_pandapower, preview_pandapower_import

    net_pp = pp.create_empty_network(name="dup_lines")
    b1 = pp.create_bus(net_pp, vn_kv=20.0, name="b1")
    b2 = pp.create_bus(net_pp, vn_kv=20.0, name="b2")
    b3 = pp.create_bus(net_pp, vn_kv=20.0, name="b3")
    # Two lines with the SAME name.
    pp.create_line_from_parameters(net_pp, b1, b2, length_km=1.0, r_ohm_per_km=0.1,
                                   x_ohm_per_km=0.1, c_nf_per_km=0.0, max_i_ka=1.0, name="feeder")
    pp.create_line_from_parameters(net_pp, b2, b3, length_km=1.0, r_ohm_per_km=0.1,
                                   x_ohm_per_km=0.1, c_nf_per_km=0.0, max_i_ka=1.0, name="feeder")

    defaults = gi.ImportDefaults(
        rho=100.0, frequencies=[50.0],
        default_bus_type=BusType(name="B", system_type="Grounded", voltage_level=20.0,
                                 impedance_formula="rho * 0 + 1.0 + I * f * 0"),
        default_branch_type=BranchType(name="C", grounding_conductor=True,
                                       self_impedance_formula="(0.25 + I*0.6)*l",
                                       mutual_impedance_formula="(0.0 + I*0.6)*l"),
    )

    preview = preview_pandapower_import(net_pp, voltage_level_kV=20.0)
    kept_lines = preview.filter((preview["kind"] == "line") & (preview["status"] == "keep"))
    assert kept_lines.height == 2   # preview keeps both

    network = from_pandapower(net_pp, defaults=defaults, voltage_level_kV=20.0)
    assert len(network.branches) == 2                    # commit agrees, no crash
    assert len(set(network.branches.keys())) == 2        # names are unique

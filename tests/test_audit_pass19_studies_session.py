# tests/test_audit_pass19_studies_session.py
"""
Audit pass 19 -- study sessions: searches re-solve instead of rebuilding (P4).

``find_max_rho_scaling`` evaluates the EPR at the fault bus for twenty or so
trial factors, and every trial used to be a complete ``run_fault``: paths,
model tables, phase network, injection vectors, assembly, factorisation and
some hundred thousand result objects -- although between two trials only
the grounding impedances of the selected buses change. Those enter the
nodal matrix on its diagonal and nowhere else; the injection vectors, the
phase currents, the branch admittances and the position of every stamp are
the same for every trial. A session keeps all of that from the first solve
and re-solves a trial by re-assembling the matrix values and factorising
them -- the same values, the same summation order, the same factorisation,
so the EPR of a trial is bit for bit the one a full ``run_fault`` reports.

The tests here pin exactly that: a search with sessions returns the same
dictionary as the search through full solves (second way: the session
switched off), solves fully only where a session cannot answer (a 0 Hz
stand-in, a bus that gains or loses its electrode), raises the errors of
the full search, keeps the progress protocol (GUI5) and leaves the network
as found.
"""

from __future__ import annotations

import time
from typing import Dict, List

import numpy as np
import pytest

import groundinsight as gi
from groundinsight.analysis import _session
from tests import test_audit_pass19_performance as perf
from tests._warning_policy import acknowledge

#: The rho searches sweep trial faults along chains and rings whose electrode
#: currents are spread in angle; the tests compare the session with the full
#: search.
pytestmark = acknowledge("EARTH_GROUP_SPREAD")

#: The 0 Hz stand-in cases put a 0 Hz bin in the grid on purpose.
DC_STAND_IN = acknowledge("DC_LIMIT")

#: A voltage source drives the coupled branches on purpose: its loop closure
#: is what the session must not reuse.
THEVENIN = acknowledge("THEVENIN_COUPLING")

#: The paths mode on parallel routes, compared with the full search.
PATHS_MODE = acknowledge("PARALLEL_COEFFICIENT_DEFAULT")


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------


@pytest.fixture
def full_solves(monkeypatch):
    """Count the full ``run_fault`` calls of the searches."""
    import groundinsight.analysis.inverse_rho as inverse_rho
    import groundinsight.analysis.inverse_rho_f as inverse_rho_f

    original = inverse_rho.run_fault
    count = {"n": 0}

    def counting(*args, **kwargs):
        count["n"] += 1
        return original(*args, **kwargs)

    monkeypatch.setattr(inverse_rho, "run_fault", counting)
    monkeypatch.setattr(inverse_rho_f, "run_fault", counting)
    return count


def _without_session(monkeypatch, call):
    """Run ``call()`` with the sessions switched off: the full search."""
    with monkeypatch.context() as patch:
        patch.setattr(_session, "_ENABLED", False)
        return call()


def _outcome(call):
    """The result of ``call()``, or the type and text of what it raised --
    a search that refuses its input has to refuse it the same way."""
    try:
        return ("returned", call())
    except (ValueError, RuntimeError) as exc:
        return ("raised", type(exc).__name__, str(exc))


def _limit(net: gi.Network, fault: str = "F", factor: float = 0.8) -> float:
    """An EPR limit that the search can meet: ``factor`` times the EPR at
    the fault bus as the network stands."""
    bus = net.faults[fault].bus
    return factor * {b.name: b.uepr for b in gi.solve(net, fault).buses}[bus]


def _state(net: gi.Network) -> dict:
    """Serialised form plus the identities a search must restore."""
    return {
        "json": net.model_dump_json(),
        "results": [(name, id(result)) for name, result in net.results.items()],
        "active_fault": net.active_fault,
        "electrical_network": id(net.electrical_network),
        "rho": {name: bus.specific_earth_resistance for name, bus in net.buses.items()},
    }


def _rho_ring() -> gi.Network:
    """A ring whose bus type also declares R and L formulas (the transient
    parameters are derived alongside the impedance and must not change the
    outcome of a search)."""
    net = perf.ring(40, F=3, n_sources=2)
    bus_type = gi.BusType(
        name="rlc", system_type="MV", voltage_level=20.0,
        impedance_formula="0.01*rho + 0.2 + j*f*1e-4",
        R_formula="0.01*rho + 0.2", L_formula="1e-4/(2*pi)",
    )
    for name in list(net.buses)[::3]:
        net.update_bus(name, type=bus_type)
    return net


def _inactive_selected() -> gi.Network:
    net = perf.ring(30, F=3, n_sources=1)
    net.buses["B7"].active = False
    return net


RHO_CASES = {
    "ring60_all": (lambda: perf.ring(60, F=7), None),
    "ring60_five": (lambda: perf.ring(60, F=7), ["B28", "B29", "B30", "B31", "B32"]),
    "mesh8_four_sources": (lambda: perf.mesh(8, F=3, n_sources=4), 12),
    "radial120_three_sources": (lambda: perf.radial(120, F=3, n_sources=3), 20),
    "thevenin_ring": (lambda: perf.thevenin_ring(40, F=3), None),
    "rlc_bus_types": (_rho_ring, None),
    "inactive_bus_selected": (_inactive_selected, ["B5", "B6", "B7", "B8"]),
}


def _selection(net: gi.Network, spec) -> List[str]:
    names = list(net.buses)
    if spec is None:
        return names
    if isinstance(spec, int):
        return names[:: max(1, len(names) // spec)][:spec]
    return list(spec)


# --------------------------------------------------------------------------
# find_max_rho_scaling
# --------------------------------------------------------------------------


@THEVENIN
@pytest.mark.parametrize("case", sorted(RHO_CASES))
def test_rho_search_with_session_equals_the_full_search(monkeypatch, full_solves, case):
    """The session answers every trial with the EPR a full ``run_fault``
    reports for it, bit for bit, so the search takes the same steps and
    returns the same dictionary -- c_max, its EPR, rho_max, iterations,
    status and bracket -- or refuses the bracket with the same message
    (second way: the search with the sessions off). Only its first trial is
    a full solve."""
    builder, spec = RHO_CASES[case]
    net = builder()
    selected = _selection(net, spec)
    limit = _limit(net, factor=1.0)
    kwargs = dict(c_bounds=(0.05, 20.0), tol_rel=1e-4)
    full_solves["n"] = 0
    with_session = _outcome(
        lambda: gi.find_max_rho_scaling(net, "F", selected, limit, **kwargs)
    )
    assert full_solves["n"] == 1
    full = _without_session(
        monkeypatch,
        lambda: _outcome(
            lambda: gi.find_max_rho_scaling(builder(), "F", selected, limit, **kwargs)
        ),
    )
    assert with_session == full


def test_the_battery_above_exercises_the_bisection(monkeypatch):
    """The equivalence above is only worth something where the search
    actually bisects: on the ring with every bus scaled it does."""
    net = perf.ring(60, F=7)
    result = gi.find_max_rho_scaling(
        net, "F", list(net.buses), _limit(net, factor=1.0),
        c_bounds=(0.05, 20.0), tol_rel=1e-4,
    )
    assert result["status"] == "converged" and result["iterations"] > 5


@PATHS_MODE
def test_rho_search_with_paths_mode_equals_the_full_search(monkeypatch):
    """The legacy path-based phase currents enter the injection vectors the
    session keeps; they do not depend on the bus impedances either."""
    builder = lambda: perf.ring(30, F=3, n_sources=2)  # noqa: E731
    net = builder()
    limit = _limit(net)
    kwargs = dict(run_fault_kwargs={"phase_current_mode": "paths"})
    with_session = gi.find_max_rho_scaling(net, "F", list(net.buses), limit, **kwargs)
    full = _without_session(
        monkeypatch,
        lambda: gi.find_max_rho_scaling(builder(), "F", list(builder().buses),
                                        limit, **kwargs),
    )
    assert with_session == full


def test_rho_search_solves_once_and_re_solves_every_other_trial(full_solves):
    net = perf.ring(60, F=7)
    limit = _limit(net)
    full_solves["n"] = 0
    result = gi.find_max_rho_scaling(net, "F", list(net.buses), limit)
    assert result["status"] == "converged"
    assert full_solves["n"] == 1


def test_rho_search_without_session_solves_every_trial(monkeypatch, full_solves):
    """Second count for the one above: the same search through full solves
    makes one ``run_fault`` per trial (scan plus bisection steps)."""
    net = perf.ring(60, F=7)
    limit = _limit(net)
    full_solves["n"] = 0
    result = _without_session(
        monkeypatch,
        lambda: gi.find_max_rho_scaling(net, "F", list(net.buses), limit),
    )
    assert full_solves["n"] == 9 + result["iterations"]


@DC_STAND_IN
def test_rho_search_with_a_dc_stand_in_solves_fully(monkeypatch, full_solves):
    """At 0 Hz with a shorted shield the stand-in impedance is scaled to the
    smallest impedance of the network -- possibly a bus impedance, which the
    search changes. The session does not guess: every trial is a full solve,
    and the result is the full search's."""
    net = perf.dc_ring(30)
    limit = _limit(net)
    full_solves["n"] = 0
    with_session = gi.find_max_rho_scaling(net, "F", list(net.buses), limit)
    assert full_solves["n"] == 9 + with_session["iterations"]
    full = _without_session(
        monkeypatch,
        lambda: gi.find_max_rho_scaling(perf.dc_ring(30), "F",
                                        list(perf.dc_ring(30).buses), limit),
    )
    assert with_session == full


def test_a_session_does_not_answer_when_an_electrode_appears_or_vanishes():
    """A bus without electrode at some frequency, a shorted or a NaN value
    changes what the solver may treat as reference earth, or is an error the
    model reports itself: the session hands such a trial back (``None``) to
    the full solve. An ordinary change it answers."""
    net = perf.ring(20, F=3)
    gi.run_fault(net, "F")
    session = _session.BusImpedanceSession.attach(net, "F")
    width = len(net.frequencies)
    good = _session.bus_impedance_at(net.buses["B3"], 250.0, net.frequencies)
    assert isinstance(session.epr_rms({"B3": good}), float)
    for bad in (
        np.full(width, complex(np.inf, np.inf)),
        np.full(width, complex(np.nan, 0.0)),
        np.zeros(width, dtype=complex),
        np.full(width, complex(1e-320, 0.0)),  # 1/z overflows: a short
    ):
        changed = good.copy()
        changed[1] = bad[1]
        assert session.epr_rms({"B3": changed}) is None


def test_a_bus_without_electrode_before_the_search_is_not_re_solved():
    net = perf.ring(20, F=3)
    net.update_bus("B4", type=perf.BT_OPEN)
    gi.run_fault(net, "F")
    session = _session.BusImpedanceSession.attach(net, "F")
    value = _session.bus_impedance_at(net.buses["B3"], 120.0, net.frequencies)
    assert session.epr_rms({"B3": value}) is not None
    assert session.epr_rms({"B4": value}) is None


def test_a_trial_the_model_rejects_raises_as_in_the_full_search(monkeypatch):
    """A factor at which the bus impedance turns non-passive raises the
    model's own error -- the same type and text with and without session."""
    def build():
        net = perf.ring(20, F=3)
        # positive up to rho = 500, negative above: the first trial (c = 0.5,
        # a full solve) is fine, the second (c = 10, a session trial) is not
        falling = gi.BusType(name="falling", system_type="MV", voltage_level=20.0,
                             impedance_formula="1 - 0.002*rho + 0*f")
        for name in ("B2", "B3", "B4"):
            net.update_bus(name, type=falling)
        return net

    limit = 1e9
    kwargs = dict(c_bounds=(0.5, 10.0), monotonicity_grid=0)
    with pytest.raises(ValueError) as with_session:
        gi.find_max_rho_scaling(build(), "F", ["B2", "B3", "B4"], limit, **kwargs)
    with pytest.raises(ValueError) as full:
        _without_session(
            monkeypatch,
            lambda: gi.find_max_rho_scaling(build(), "F", ["B2", "B3", "B4"],
                                            limit, **kwargs),
        )
    assert str(with_session.value) == str(full.value)
    assert "negative" in str(with_session.value).lower()


def test_rho_search_with_session_leaves_the_network_as_found():
    net = perf.ring(40, F=3, n_sources=2)
    gi.run_fault(net, "F")
    before = _state(net)
    gi.find_max_rho_scaling(net, "F", list(net.buses), _limit(net))
    assert _state(net) == before


def test_a_session_trial_equals_run_fault_bit_for_bit():
    """The mechanism itself, against a second, independent computation: the
    EPR a session returns for new bus impedances equals the ``uepr`` of a
    full ``run_fault`` on a copy of the network that carries them."""
    net = perf.mesh(6, F=3, n_sources=2)
    gi.run_fault(net, "F")
    session = _session.BusImpedanceSession.attach(net, "F")
    assert session is not None
    other = net.model_copy(deep=True)
    changed: Dict[str, np.ndarray] = {}
    for name in ("M1_1", "M2_4", "M5_5"):
        net_bus = net.buses[name]
        rho = 3.7 * net_bus.specific_earth_resistance
        changed[name] = _session.bus_impedance_at(net_bus, rho, net.frequencies)
        other.buses[name].specific_earth_resistance = rho
    epr = session.epr_rms(changed)
    gi.run_fault(other, "F")
    fault_bus = other.faults["F"].bus
    expected = {b.name: b.uepr for b in other.results["F"].buses}[fault_bus]
    assert epr == expected


# --------------------------------------------------------------------------
# find_max_rho_f_scaling
# --------------------------------------------------------------------------

K_REF = (0.01, 0.0, 2e-5, 0.0, 1e-6)


def _with_user_fault() -> gi.Network:
    """A swept bus that already carries a fault of the user, with its own
    scalings: the search reuses that fault and its scalings."""
    net = perf.ring(30, F=3, n_sources=2)
    gi.create_fault(name="user", bus="B7", scalings={50.0: 0.5, 100.0: 1.0,
                                                     150.0: 2.0}, network=net)
    return net


RHO_F_CASES = {
    "ring40_five": (lambda: perf.ring(40, F=3), ["B3", "B9", "B20", "B21", "B33"], {}),
    "mesh6_four_sources": (lambda: perf.mesh(6, F=3, n_sources=4),
                           ["M0_1", "M2_2", "M3_3", "M5_4"], {}),
    "thevenin_ring": (lambda: perf.thevenin_ring(30, F=3), ["B4", "B15", "B22"], {}),
    "user_fault_reused": (_with_user_fault, ["B2", "B7", "B12"], {}),
    "fault_scalings": (lambda: perf.ring(30, F=3), ["B2", "B12"],
                       {"fault_scalings": {50.0: 1.0, 100.0: 0.3, 150.0: 0.1}}),
    "paths_mode": (lambda: perf.ring(24, F=3, n_sources=2), ["B2", "B12"],
                   {"run_fault_kwargs": {"phase_current_mode": "paths"}}),
}


def _rho_f_limit(builder, buses, kwargs) -> float:
    eprs = gi.evaluate_max_epr_under_k(builder(), buses, K_REF, **kwargs)
    return max(eprs.values())


@PATHS_MODE
@THEVENIN
@pytest.mark.parametrize("case", sorted(RHO_F_CASES))
def test_rho_f_search_with_session_equals_the_full_search(monkeypatch, full_solves, case):
    """Every fault location keeps its own session from the first full sweep;
    later trial factors are re-solved on them. Same dictionary as the search
    through full sweeps -- c_max, the per-bus EPRs, iterations, status --
    and only the first sweep (one solve per bus) is a full one."""
    builder, buses, extra = RHO_F_CASES[case]
    limit = _rho_f_limit(builder, buses, {
        key: value for key, value in extra.items() if key == "fault_scalings"
    })
    kwargs = dict(k_ref=K_REF, c_bounds=(0.1, 10.0), tol_rel=1e-4, **extra)
    full_solves["n"] = 0
    with_session = _outcome(
        lambda: gi.find_max_rho_f_scaling(builder(), buses, limit, **kwargs)
    )
    assert full_solves["n"] == len(buses)
    full = _without_session(
        monkeypatch,
        lambda: _outcome(
            lambda: gi.find_max_rho_f_scaling(builder(), buses, limit, **kwargs)
        ),
    )
    assert with_session == full
    assert with_session[0] == "returned" and with_session[1]["iterations"] > 5


@THEVENIN
def test_fault_locations_share_the_matrix_unless_a_voltage_source_loops():
    """Two fault locations of one network assemble the same nodal matrix --
    except for the loop closure a voltage source puts between its bus and
    the fault bus. Sessions that share it share each trial's factorisation;
    the per-location solves are then bit for bit the unshared ones."""
    def sessions(builder):
        net = builder()
        made = []
        for bus in ("B3", "B11"):
            gi.create_fault(name=f"at_{bus}", bus=bus, scalings={}, network=net)
            gi.run_fault(net, f"at_{bus}")
            made.append(_session.BusImpedanceSession.attach(net, f"at_{bus}"))
        return net, made

    net, (first, second) = sessions(lambda: perf.ring(20, F=3))
    assert first.same_matrix_as(second)
    changed = {"B5": _session.bus_impedance_at(net.buses["B5"], 333.0, net.frequencies)}
    factors = first.factorise(changed)
    assert second.epr_rms(changed, factors) == second.epr_rms(changed)
    _, (first, second) = sessions(lambda: perf.thevenin_ring(20, F=3))
    assert not first.same_matrix_as(second)


def test_rho_f_search_without_session_sweeps_every_trial(monkeypatch, full_solves):
    """Second count for the one above."""
    builder, buses, _ = RHO_F_CASES["ring40_five"]
    limit = _rho_f_limit(builder, buses, {})
    full_solves["n"] = 0
    result = _without_session(
        monkeypatch,
        lambda: gi.find_max_rho_f_scaling(builder(), buses, limit, K_REF,
                                          c_bounds=(0.1, 10.0), tol_rel=1e-4),
    )
    assert full_solves["n"] == len(buses) * (2 + result["iterations"])


@DC_STAND_IN
def test_rho_f_search_with_a_dc_stand_in_sweeps_fully(monkeypatch, full_solves):
    net = perf.dc_ring(24)
    buses = ["B3", "B12"]
    limit = max(gi.evaluate_max_epr_under_k(perf.dc_ring(24), buses, K_REF).values())
    full_solves["n"] = 0
    with_session = gi.find_max_rho_f_scaling(net, buses, limit, K_REF,
                                             c_bounds=(0.1, 10.0), tol_rel=1e-3)
    assert full_solves["n"] == len(buses) * (2 + with_session["iterations"])
    full = _without_session(
        monkeypatch,
        lambda: gi.find_max_rho_f_scaling(perf.dc_ring(24), buses, limit, K_REF,
                                          c_bounds=(0.1, 10.0), tol_rel=1e-3),
    )
    assert with_session == full


def test_rho_f_search_with_session_leaves_the_network_as_found():
    net = _with_user_fault()
    gi.run_fault(net, "F")
    before = _state(net)
    before["faults"] = sorted(net.faults)
    buses = ["B2", "B7", "B12"]
    gi.find_max_rho_f_scaling(net, buses, _rho_f_limit(_with_user_fault, buses, {}),
                              K_REF, c_bounds=(0.1, 10.0))
    after = _state(net)
    after["faults"] = sorted(net.faults)
    assert after == before


@pytest.mark.slow
def test_rho_f_search_on_a_300_bus_ring_takes_a_fraction_of_the_full_sweeps():
    """P4: five fault locations on a 300-bus ring at seven frequencies.
    Measured interleaved in one process: 3.9 s through full sweeps, 0.3 s
    with the sessions."""
    net = perf.ring(300, F=7)
    buses = ["B10", "B80", "B150", "B220", "B290"]
    limit = max(gi.evaluate_max_epr_under_k(net, buses, K_REF).values())
    start = time.process_time()
    result = gi.find_max_rho_f_scaling(net, buses, limit, K_REF,
                                       c_bounds=(0.1, 10.0))
    seconds = time.process_time() - start
    assert result["status"] == "converged"
    assert seconds < 1.5, f"{seconds:.2f} s"


@pytest.mark.slow
def test_rho_search_on_a_1000_bus_ring_takes_seconds_not_minutes():
    """P4: all 1000 buses scaled, seven frequencies. Measured interleaved in
    one process: 5.9 s through full solves on the sparse kernel (25 s on the
    dense kernel of 0.6.0, audit pass 19), 0.8 s with the session."""
    net = perf.ring(1000, F=7)
    limit = _limit(net)
    start = time.process_time()
    result = gi.find_max_rho_scaling(net, "F", list(net.buses), limit)
    seconds = time.process_time() - start
    assert result["status"] == "converged"
    assert seconds < 3.0, f"{seconds:.2f} s"

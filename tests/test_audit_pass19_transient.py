# tests/test_audit_pass19_transient.py

"""
Audit pass 19 -- transient solver: PH2 and PH-L1.

* **PH2** -- the state-space solver distributed the phase current over the
  routes of a ring with the enumerated source-to-fault paths and
  ``parallel_coefficient`` (default 1.0), i.e. the legacy
  ``phase_current_mode="paths"``. In a symmetric four-bus ring the steady-state
  EPR at the fault came out at 338.3 V -- exactly the ``"paths"`` result --
  against 166.6 V from the stationary default ``"auto"`` (+103 %), without a
  warning. The solver now takes the split from the same phase-network solve
  as ``"auto"`` and folds it into the coupling of each branch.
* **PH-L1** -- the warning the FFT solver raises for the mutual coupling it
  does not evaluate said "expect a *lower* EPR". Measured, the FFT EPR without
  coupling is *higher* (77.6 V against 49.2 V), at the fault bus and as the
  network maximum -- conservative there.

Second methods (house rule): the stationary phasor solver
(``run_fault(..., phase_current_mode="auto")``), the independent MNA
reference in :mod:`tests._transient_reference` (branch currents of shield
*and* phase conductor as unknowns, trapezoidal integration, phase network
integrated as states) and the closed form ``1/|1 - Z_m/Z_s|`` for a single
coupled branch.
"""

import logging
import math
import pathlib
import warnings

import numpy as np
import pytest

import groundinsight as gi
from groundinsight import diagnostics
from groundinsight.diagnostics import ModelAssumptionWarning
from groundinsight.models.core_models import TransientModelWarning
from groundinsight.simulation import waveforms
from groundinsight.simulation.transient import TransientStudy

from tests import _transient_reference as ref


W50 = 2.0 * math.pi * 50.0
DOCS = pathlib.Path(__file__).resolve().parents[1] / "docs"


# ---------------------------------------------------------------------------
# Networks: one description, two models (groundinsight and the reference)
# ---------------------------------------------------------------------------


def _build(rn, *, name, frequencies=(50.0,), declare_phase=True, values=None):
    """The ``groundinsight`` model of a :class:`ref.RefNetwork`.

    Formulas and lumped fields describe the same physics,
    ``Z = (R + j w L) * l`` with ``l = 1``, so the stationary solver, the
    state-space solver and the reference all see one network. With
    ``declare_phase=False`` the package falls back to its phase proxy
    ``1/Z_self`` (which equals the declared one when ``R_p = R_s``,
    ``L_p = L_s``).
    """
    model = gi.create_network(name=name, frequencies=list(frequencies))
    for bus, resistance in rn.bus_R.items():
        bus_type = gi.BusType(
            name=f"bt_{name}_{bus}", system_type="S", voltage_level=20.0,
            impedance_formula=f"{resistance!r}", R_formula=f"{resistance!r}",
        )
        gi.create_bus(name=bus, type=bus_type, network=model)
    for br in rn.branches:
        kwargs = dict(
            name=f"brt_{name}_{br.name}",
            grounding_conductor=br.shield,
            self_impedance_formula=f"({br.R_s!r} + j*2*pi*f*{br.L_s!r})*l",
            mutual_impedance_formula=f"({br.R_m!r} + j*2*pi*f*{br.M!r})*l",
            R_self_formula=f"{br.R_s!r}*l",
            L_self_formula=f"{br.L_s!r}*l",
            R_mutual_formula=f"{br.R_m!r}*l",
            M_mutual_formula=f"{br.M!r}*l",
        )
        if declare_phase:
            kwargs["phase_impedance_formula"] = (
                f"({br.R_p!r} + j*2*pi*f*{br.L_p!r})*l"
            )
        gi.create_branch(
            name=br.name, type=gi.BranchType(**kwargs), from_bus=br.from_bus,
            to_bus=br.to_bus, length=1.0, network=model,
        )
    if values is None:
        values = {float(f): 1000.0 + 0j for f in frequencies}
    gi.create_source(name="s", bus=rn.source_bus, values=values, network=model)
    gi.create_fault(
        name="F1", bus=rn.fault_bus,
        scalings={float(f): 1.0 for f in frequencies}, network=model,
    )
    return model


# Cable of the PH2 finding: per km, consistent R/L/M.
_CABLE = dict(R_s=0.29, L_s=0.625 / W50, R_m=0.049, M=0.55 / W50,
              R_p=0.29, L_p=0.625 / W50)


def _four_bus(ring: bool, lengths=(1.0, 1.0, 1.0, 1.0)):
    """S -- A -- F -- B (-- S): the network of the PH2 finding."""
    ends = [("L1", "S", "A"), ("L2", "A", "F"), ("L3", "F", "B")]
    if ring:
        ends.append(("L4", "B", "S"))
    branches = [
        ref.RefBranch(name, a, b, **{k: v * lengths[i] for k, v in _CABLE.items()})
        for i, (name, a, b) in enumerate(ends)
    ]
    return ref.RefNetwork(
        bus_R={"S": 0.3, "A": 2.0, "F": 2.0, "B": 2.0},
        branches=branches, source_bus="S", fault_bus="F",
    )


def _mixed_ring():
    """Cable route S-A-F and overhead-line route F-B-S (steel earth wire).

    The two routes have different R/L ratios, so the auto split is complex at
    50 Hz (quadrature part 0.044) and changes with frequency (0.49 at DC,
    0.77 at 50 Hz, 0.78 towards high frequency on the cable route).
    """
    cable = dict(R_s=0.29, L_s=0.625 / W50, R_m=0.049, M=0.55 / W50,
                 R_p=0.125, L_p=0.35 / W50)
    ohl = dict(R_s=2.5, L_s=0.9 / W50, R_m=0.049, M=0.35 / W50,
               R_p=0.12, L_p=1.25 / W50)
    return ref.RefNetwork(
        bus_R={"S": 0.3, "A": 2.0, "F": 2.0, "B": 15.0},
        branches=[
            ref.RefBranch("L1", "S", "A", **cable),
            ref.RefBranch("L2", "A", "F", **cable),
            ref.RefBranch("L3", "F", "B", **ohl),
            ref.RefBranch("L4", "B", "S", **ohl),
        ],
        source_bus="S", fault_bus="F",
    )


def _sine(rms=1000.0, phase=0.0):
    return lambda t: math.sqrt(2.0) * rms * np.cos(W50 * t + phase)


def _offset_fault_current(rms=1000.0, tau=0.04):
    """Fully offset fault current that starts at zero (consistent start)."""
    amplitude = math.sqrt(2.0) * rms
    return lambda t: amplitude * (np.sin(W50 * t - np.pi / 2) + np.exp(-t / tau))


def _state_space(model, *, waves=None, t_end=0.4, dt=1e-5, buses=None,
                 branches=(), fault="F1"):
    study = TransientStudy(model, fault_name=fault)
    for name, wave in (waves or {"s": _sine()}).items():
        study.set_source_waveform(name, wave)
    study.set_observation(buses=list(buses or model.buses), branches=list(branches))
    result = study.solve(t_end=t_end, dt=dt, solver="state_space")
    return study, result


def _stationary(model, fault="F1"):
    gi.run_fault(model, fault, phase_current_mode="auto")
    return {
        b.name: complex(b.uepr_freq[50.0].real, b.uepr_freq[50.0].imag)
        for b in model.results[fault].buses
    }


def _phasors(result, buses, periods=5):
    t = np.asarray(result.time_s)
    return {b: ref.steady_phasor(t, np.asarray(result.epr_t[b]), 50.0, periods)
            for b in buses}


def _max_rel(a, b):
    scale = max(abs(v) for v in b.values())
    return max(abs(a[k] - b[k]) for k in b) / scale


def _phase_factors(study):
    _, factors, _ = study._TransientStudy__mutual_for_output
    return factors


# ---------------------------------------------------------------------------
# PH2 -- the state-space solver uses the auto phase split
# ---------------------------------------------------------------------------


def test_reference_reproduces_the_stationary_solver():
    """The oracle itself: same model and sign convention as ``run_fault``.

    Measured 1e-15 on all four networks used below; without this the
    reference could agree with the transient for the wrong reason.
    """
    for label, rn, declare in (
        ("chain", _four_bus(False), False),
        ("ring", _four_bus(True), False),
        ("asym", _four_bus(True, lengths=(0.5, 1.0, 2.0, 1.5)), True),
        ("mixed", _mixed_ring(), True),
    ):
        model = _build(rn, name=f"oracle_{label}", declare_phase=declare)
        stationary = _stationary(model)
        reference = ref.phasor_solve(rn, 50.0, 1000.0)["epr"]
        assert _max_rel(reference, stationary) < 1e-12, label


def test_ring_state_space_matches_the_stationary_auto_solve():
    """PH2 acceptance: symmetric ring, steady-state RMS at the fault = ``auto``.

    Pre-fix 338.3 V (the ``"paths"`` answer) against 166.6 V.
    Second method: the stationary phasor solver; third: the reference.
    """
    rn = _four_bus(True)
    model = _build(rn, name="ph2_ring", declare_phase=False)
    stationary = _stationary(model)
    _, result = _state_space(model)
    t = np.asarray(result.time_s)
    u_f = np.asarray(result.epr_t["F"])
    last = t >= t[-1] - 5 * 0.02 + 0.5 * (t[1] - t[0])  # five whole periods
    rms = float(np.sqrt(np.mean(u_f[last] ** 2)))

    assert rms == pytest.approx(abs(stationary["F"]), rel=1e-3)
    assert abs(stationary["F"]) == pytest.approx(166.641, abs=1e-3)
    # Phasors, not only magnitudes, at every bus -- and against the reference.
    phasors = _phasors(result, rn.bus_R)
    assert _max_rel(phasors, stationary) < 1e-5       # measured 3.6e-7
    reference = ref.phasor_solve(rn, 50.0, 1000.0)["epr"]
    assert _max_rel(phasors, reference) < 1e-5


def test_chain_state_space_is_unchanged():
    """Control: in a chain ``auto`` and ``paths`` coincide (0.02 % before)."""
    rn = _four_bus(False)
    model = _build(rn, name="ph2_chain", declare_phase=False)
    stationary = _stationary(model)
    study, result = _state_space(model)
    phasors = _phasors(result, rn.bus_R)
    assert abs(phasors["F"]) == pytest.approx(abs(stationary["F"]), rel=2e-4)
    assert _max_rel(phasors, stationary) < 1e-5       # measured 6.9e-7
    factors = _phase_factors(study)
    assert factors[("L1", "s")] == pytest.approx(1.0, abs=1e-12)
    assert factors[("L2", "s")] == pytest.approx(1.0, abs=1e-12)
    assert abs(factors.get(("L3", "s"), 0.0)) < 1e-12


def test_ring_phase_factors_are_the_auto_split():
    """The symmetric ring halves the source current on each route (was 1.0)."""
    rn = _four_bus(True)
    model = _build(rn, name="ph2_factors", declare_phase=False)
    study, _ = _state_space(model, t_end=0.02, dt=1e-4)
    factors = _phase_factors(study)
    expected = {"L1": 0.5, "L2": 0.5, "L3": -0.5, "L4": -0.5}
    reference = ref.phase_split(rn, 50.0)
    for branch, value in expected.items():
        assert factors[(branch, "s")] == pytest.approx(value, abs=1e-12)
        assert reference[branch] == pytest.approx(value, abs=1e-12)


def test_asymmetric_homogeneous_ring_follows_the_reference_in_time():
    """One cable type, unequal lengths: the split is real and frequency-
    independent (0.7 / 0.3), so a fixed split is *exact* -- also for the DC
    offset of a fully offset fault current. Compared sample by sample against
    the reference with the phase conductors integrated as states."""
    rn = _four_bus(True, lengths=(0.5, 1.0, 2.0, 1.5))
    model = _build(rn, name="ph2_asym", declare_phase=True)
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        _, result = _state_space(
            model, waves={"s": _offset_fault_current()}, t_end=0.3, dt=2e-5,
        )
    assert not [w for w in caught if getattr(w.message, "code", "") ==
                "TRANSIENT_PHASE_SPLIT"]
    t = np.asarray(result.time_s)
    exact = ref.time_solve(rn, t, _offset_fault_current())["epr"]
    peak = max(np.max(np.abs(v)) for v in exact.values())
    worst = max(np.max(np.abs(np.asarray(result.epr_t[b]) - exact[b]))
                for b in exact)
    assert worst / peak < 1e-5                         # measured 5.8e-7
    # Pre-fix the path rule gave both routes the full current: the worst
    # deviation from the reference was 124 % of the peak.
    assert peak > 100.0


def test_mixed_ring_is_exact_at_f_eval_and_warns_about_the_dynamics():
    """Routes of different R/L: the split depends on frequency.

    * steady state at f_eval (50 Hz) equals ``run_fault`` at every bus, because
      the complex split is folded into the coupling of each branch (a fixed
      real split ``Re(alpha)`` would be 4.4 % low at the fault, unconservative);
    * the solver says that DC offset and harmonics see the 50 Hz split
      (``TRANSIENT_PHASE_SPLIT``);
    * against the reference with the phase network integrated exactly, the
      fully offset fault current deviates by about 1 % of the peak (measured
      0.99 %), and against the reference fed with the *same* folded split by
      integration error only (9.5e-7).
    """
    rn = _mixed_ring()
    model = _build(rn, name="ph2_mixed", declare_phase=True)
    stationary = _stationary(model)

    with pytest.warns(ModelAssumptionWarning) as record:
        _, steady = _state_space(model)
    split = [w.message for w in record
             if getattr(w.message, "code", "") == "TRANSIENT_PHASE_SPLIT"]
    assert len(split) == 1
    assert {("branch", n) for n in ("L1", "L2", "L3", "L4")} <= set(split[0].elements)
    assert "50 Hz" in str(split[0])

    assert _max_rel(_phasors(steady, rn.bus_R), stationary) < 1e-5   # 3.3e-7

    with warnings.catch_warnings():
        warnings.simplefilter("ignore", ModelAssumptionWarning)
        _, transient = _state_space(
            model, waves={"s": _offset_fault_current()}, t_end=0.3, dt=2e-5,
        )
    t = np.asarray(transient.time_s)
    exact = ref.time_solve(rn, t, _offset_fault_current())["epr"]
    peak = max(np.max(np.abs(v)) for v in exact.values())
    deviation = max(np.max(np.abs(np.asarray(transient.epr_t[b]) - exact[b]))
                    for b in exact)
    # Measured 0.99 %: a real, warned approximation -- not zero, not large
    # (a fixed Re(alpha) instead of the fold: 6.5 %).
    assert 0.005 < deviation / peak < 0.0125

    # Same folded split in the reference: R'_m + j w M' = (R_m + j w M) * alpha.
    alpha = ref.phase_split(rn, 50.0)
    folded = ref.RefNetwork(bus_R=dict(rn.bus_R), source_bus=rn.source_bus,
                            fault_bus=rn.fault_bus)
    for br in rn.branches:
        z_m = complex(br.R_m, W50 * br.M) * alpha[br.name]
        folded.branches.append(ref.RefBranch(
            br.name, br.from_bus, br.to_bus, br.R_s, br.L_s,
            z_m.real, z_m.imag / W50, br.R_p, br.L_p,
        ))
    same_model = ref.time_solve(
        folded, t, _offset_fault_current(),
        phase_factors={br.name: 1.0 for br in rn.branches},
    )["epr"]
    worst = max(np.max(np.abs(np.asarray(transient.epr_t[b]) - same_model[b]))
                for b in same_model)
    assert worst / peak < 1e-5                         # measured 9.5e-7


def test_state_space_leaves_paths_and_active_fault_alone(caplog):
    """The split needs no path enumeration and no active-fault switch.

    Pre-fix the solver rebuilt ``network.paths`` (with a log line advising a
    preceding ``run_fault`` "so the stationary and the transient result rest
    on the same path set" -- misleading, ``run_fault`` no longer builds paths).
    """
    rn = _four_bus(True)
    model = _build(rn, name="ph2_untouched", declare_phase=False)
    assert not model.paths and model.active_fault is None
    flags = {name: fault.active for name, fault in model.faults.items()}

    with caplog.at_level(logging.WARNING, logger="groundinsight"):
        _, before = _state_space(model, t_end=0.1, dt=1e-4)
    assert not model.paths
    assert model.active_fault is None
    assert {n: f.active for n, f in model.faults.items()} == flags
    assert not [r for r in caplog.records if "paths" in r.getMessage()]

    gi.run_fault(model, "F1")
    _, after = _state_space(model, t_end=0.1, dt=1e-4)
    for bus in model.buses:
        np.testing.assert_allclose(after.epr_t[bus], before.epr_t[bus],
                                   rtol=0, atol=1e-9)


def test_study_fault_that_is_not_the_active_fault():
    """The split is taken for the study's fault, whatever is active."""
    rn = _four_bus(True)
    model = _build(rn, name="ph2_two_faults", declare_phase=False)
    gi.create_fault(name="F2", bus="A", scalings={50.0: 1.0}, network=model)
    expected = _stationary(model, "F1")
    gi.run_fault(model, "F2")
    assert model.active_fault == "F2"
    _, result = _state_space(model, fault="F1")
    assert model.active_fault == "F2"
    assert _max_rel(_phasors(result, rn.bus_R), expected) < 1e-5


def test_two_current_sources_are_split_independently():
    """Superposition: each source gets the split of its own bus."""
    rn = _four_bus(True, lengths=(0.5, 1.0, 2.0, 1.5))
    model = _build(rn, name="ph2_two_sources", declare_phase=True)
    i_b = 400.0 * np.exp(1j * 0.6)
    gi.create_source(name="s2", bus="B", values={50.0: i_b}, network=model)
    stationary = _stationary(model)
    _, result = _state_space(
        model, waves={"s": _sine(), "s2": _sine(abs(i_b), float(np.angle(i_b)))},
    )
    assert _max_rel(_phasors(result, rn.bus_R), stationary) < 1e-5


# ---------------------------------------------------------------------------
# PH-L1 -- the FFT warning names the right direction
# ---------------------------------------------------------------------------


def _two_bus_coupled(name, *, coupled=True):
    """The PH-L1 case: one coupled cable link between two 5-Ohm buses."""
    bus_type = gi.BusType(name=f"bt_{name}", system_type="S", voltage_level=20.0,
                          impedance_formula="rho*0 + 5", R_formula="rho*0 + 5")
    r_m, m = (0.05, 2e-3) if coupled else (0.0, 0.0)
    branch_type = gi.BranchType(
        name=f"brt_{name}", grounding_conductor=True,
        self_impedance_formula="((rho*0+0.5) + I*2*pi*f*5e-3)*l",
        mutual_impedance_formula=f"((rho*0+{r_m}) + I*2*pi*f*{m})*l",
        R_self_formula="(rho*0+0.5)*l", L_self_formula="(rho*0+5e-3)*l",
        R_mutual_formula=f"(rho*0+{r_m})*l", M_mutual_formula=f"(rho*0+{m})*l",
    )
    net = gi.create_network(name=name, frequencies=[50.0])
    gi.create_bus(name="b1", type=bus_type, network=net)
    gi.create_bus(name="b2", type=bus_type, network=net)
    gi.create_branch(name="link", type=branch_type, from_bus="b1", to_bus="b2",
                     length=1.0, network=net)
    gi.create_source(name="src", bus="b1", values={50.0: 100.0}, network=net)
    gi.create_fault(name="F1", bus="b2", scalings={50.0: 1.0}, network=net)
    return net


def _steady_rms(result, bus, t_min=0.5):
    t = np.asarray(result.time_s)
    u = np.asarray(result.epr_t[bus])
    return float(np.sqrt(np.mean(u[t >= t_min] ** 2)))


def test_fft_without_coupling_is_higher_and_the_warning_says_so():
    """PH-L1 acceptance: number and text.

    FFT 77.6 V, state space 49.2 V, ``run_fault`` 49.2 V. Second methods: the
    stationary solver with the mutual impedance set to zero reproduces the FFT
    value, and for a single coupled branch the ratio is exactly
    ``1/|1 - Z_m/Z_s|`` (the coupling scales every injection by ``1 - k``).
    """
    net = _two_bus_coupled("pl1")
    study = TransientStudy(net, fault_name="F1")
    study.set_source_waveform("src", waveforms.sinusoidal_with_dc_offset(
        amplitude=100.0 * math.sqrt(2.0), frequency_hz=50.0, t_on=0.0))
    study.set_observation(buses=["b1", "b2"])
    with pytest.warns(TransientModelWarning) as record:
        res_fft = study.solve(t_end=1.0, dt=2e-4, solver="fft")
    res_ss = study.solve(t_end=1.0, dt=2e-4, solver="state_space")

    fft, ss = _steady_rms(res_fft, "b2"), _steady_rms(res_ss, "b2")
    assert fft > ss
    assert fft == pytest.approx(77.634, abs=2e-3)

    coupled = _stationary(net)
    uncoupled = _stationary(_two_bus_coupled("pl1_off", coupled=False))
    assert fft == pytest.approx(abs(uncoupled["b2"]), rel=1e-4)
    assert ss == pytest.approx(abs(coupled["b2"]), rel=2e-3)
    z_s = complex(0.5, W50 * 5e-3)
    z_m = complex(0.05, W50 * 2e-3)
    assert abs(uncoupled["b2"]) / abs(coupled["b2"]) == pytest.approx(
        1.0 / abs(1.0 - z_m / z_s), rel=1e-12)

    messages = [w.message for w in record if "mutual coupling" in str(w.message)]
    assert len(messages) == 1
    text = str(messages[0])
    assert "higher" in text and "conservative" in text
    assert "lower earth potential rise" not in text
    assert messages[0].code == "TRANSIENT_MODEL"
    assert messages[0].elements == (("branch", "link"),)


def test_fft_coupling_warning_is_collected_as_a_diagnostic():
    net = _two_bus_coupled("pl1_diag")
    study = TransientStudy(net, fault_name="F1")
    study.set_source_waveform("src", waveforms.step(amplitude=100.0))
    study.set_observation(buses=["b2"])
    with diagnostics.collecting() as sink, warnings.catch_warnings():
        warnings.simplefilter("ignore")
        study.solve(t_end=0.05, dt=1e-4, solver="fft")
    coupling = [d for d in sink if "mutual coupling" in d.message]
    assert len(coupling) == 1
    assert coupling[0].code == "TRANSIENT_MODEL"
    assert coupling[0].category == "TransientModelWarning"
    assert coupling[0].elements == [("branch", "link")]


def test_transient_chapter_states_the_direction_of_the_missing_coupling():
    """The chapter said the missing FFT coupling *under*-estimates the EPR."""
    text = (DOCS / "transient.md").read_text(encoding="utf-8")
    assert "under*-estimating" not in text
    notes = text.split("*Mutual coupling — not modelled.*", 1)[1].split("\n\n", 1)[0]
    assert "higher" in notes and "conservative" in notes

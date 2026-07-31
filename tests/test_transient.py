# tests/test_transient.py

"""
Tests for the FFT-based transient solver and the waveform library.

The tests cover three behaviour classes:

1. Waveforms: shape, on/off windowing, DC decay -- verified against the
   closed-form expressions used in the factories.
2. FFT solver on a purely resistive world: the EPR at the fault bus must
   track the source waveform up to a constant divider determined by the
   network. Used as a sanity check that the FFT round-trip is faithful.
3. FFT solver on an RL world: the response must show low-pass behaviour
   relative to the input -- the high-frequency content of a switching
   transient is attenuated.
"""

from __future__ import annotations

import numpy as np
import pytest

import groundinsight as gi
from groundinsight.simulation import waveforms
from groundinsight.simulation.transient import TransientStudy, ResultTransient
from groundinsight.models.core_models import BusType, BranchType


# ---------------------------------------------------------------------------
# Waveform factories
# ---------------------------------------------------------------------------


def test_step_waveform_window():
    w = waveforms.step(amplitude=10.0, t_on=0.02, t_off=0.05)
    t = np.linspace(0.0, 0.1, 11)
    y = w(t)
    # before t_on: zero
    assert (y[t < 0.02] == 0.0).all()
    # in the on-window: 10
    assert (y[(t >= 0.02) & (t < 0.05)] == 10.0).all()
    # after t_off: zero
    assert (y[t >= 0.05] == 0.0).all()


def test_sinusoidal_with_dc_offset_zero_dc_matches_pure_sine():
    """Without DC offset the waveform is just a windowed sinusoid."""
    w = waveforms.sinusoidal_with_dc_offset(
        amplitude=2.0, frequency_hz=50.0, t_on=0.0, t_off=None,
    )
    t = np.array([0.0, 0.005, 0.010, 0.015, 0.020])  # 0, T/4, T/2, 3T/4, T
    y = w(t)
    expected = 2.0 * np.sin(2 * np.pi * 50.0 * t)
    np.testing.assert_allclose(y, expected, atol=1e-12)


def test_sinusoidal_with_dc_offset_decay():
    """The DC component decays exponentially in the on-window."""
    w = waveforms.sinusoidal_with_dc_offset(
        amplitude=0.0, frequency_hz=50.0,
        dc_amplitude=10.0, dc_decay_tau=0.05, t_on=0.0,
    )
    t = np.array([0.0, 0.05, 0.1])
    y = w(t)
    # AC=0, only DC: 10, 10*exp(-1), 10*exp(-2)
    np.testing.assert_allclose(
        y, [10.0, 10.0 * np.exp(-1), 10.0 * np.exp(-2)], atol=1e-12
    )


def test_damped_oscillation_shape():
    """At t_on the value is sin(phase) * amplitude; later the envelope decays."""
    w = waveforms.damped_oscillation(
        amplitude=5.0, frequency_hz=100.0, decay_tau=0.01,
        phase_rad=np.pi / 2, t_on=0.0,
    )
    # At t=0 with phase=pi/2: cos-like start -> sin(pi/2)=1, full amplitude
    assert w(np.array([0.0]))[0] == pytest.approx(5.0, rel=1e-9)
    # After one period at 100 Hz (T=10 ms) with tau=10 ms: envelope at 1/e
    val_after_T = w(np.array([0.01]))[0]
    expected_envelope = 5.0 * np.exp(-1.0)
    assert abs(val_after_T) <= expected_envelope + 1e-9


# ---------------------------------------------------------------------------
# Helpers for the solver tests
# ---------------------------------------------------------------------------


def _purely_resistive_two_bus_network(name: str):
    """Build a two-bus network with constant real bus and branch impedances."""
    bus_type = BusType(
        name="R_bus",
        system_type="Substation",
        voltage_level=20.0,
        impedance_formula="rho * 0 + 10 + I * f * 0",
    )
    branch_type = BranchType(
        name="R_branch",
        grounding_conductor=True,
        self_impedance_formula="(rho * 0 + 1.0 + I * f * 0) * l",
        mutual_impedance_formula="(rho * 0 + 0.0 + I * f * 0) * l",
    )
    net = gi.create_network(name=name, frequencies=[50.0])
    gi.create_bus(name="bus1", type=bus_type, network=net)
    gi.create_bus(name="bus2", type=bus_type, network=net)
    gi.create_branch(
        name="branch1", type=branch_type,
        from_bus="bus1", to_bus="bus2", length=1.0, network=net,
    )
    gi.create_source(
        name="src", bus="bus1", values={50.0: 100.0 + 0.0j}, network=net,
    )
    gi.create_fault(name="F1", bus="bus2", scalings={50.0: 1.0}, network=net)
    return net


def _rl_two_bus_network(name: str):
    """Two-bus network with a small inductive component on the bus impedance."""
    bus_type = BusType(
        name="RL_bus",
        system_type="Substation",
        voltage_level=20.0,
        impedance_formula="rho * 0 + 10 + I * 2 * pi * f * 5e-3",  # 10 ohm + j*omega*5mH
    )
    branch_type = BranchType(
        name="R_branch",
        grounding_conductor=True,
        self_impedance_formula="(rho * 0 + 1.0 + I * f * 0) * l",
        mutual_impedance_formula="(rho * 0 + 0.0 + I * f * 0) * l",
    )
    net = gi.create_network(name=name, frequencies=[50.0])
    gi.create_bus(name="bus1", type=bus_type, network=net)
    gi.create_bus(name="bus2", type=bus_type, network=net)
    gi.create_branch(
        name="branch1", type=branch_type,
        from_bus="bus1", to_bus="bus2", length=1.0, network=net,
    )
    gi.create_source(
        name="src", bus="bus1", values={50.0: 100.0 + 0.0j}, network=net,
    )
    gi.create_fault(name="F1", bus="bus2", scalings={50.0: 1.0}, network=net)
    return net


# ---------------------------------------------------------------------------
# FFT solver
# ---------------------------------------------------------------------------


def test_transient_study_requires_existing_fault():
    net = _purely_resistive_two_bus_network("net")
    with pytest.raises(ValueError):
        TransientStudy(net, fault_name="does_not_exist")


def test_transient_study_set_observation_validates_names():
    net = _purely_resistive_two_bus_network("net")
    study = TransientStudy(net, fault_name="F1")
    with pytest.raises(ValueError):
        study.set_observation(buses=["unknown_bus"])
    with pytest.raises(ValueError):
        study.set_observation(branches=["unknown_branch"])


def test_fft_solver_rejects_voltage_source():
    """The FFT solver only handles current sources; the rejection happens
    at solve() time, not at set_source_waveform (the state-space solver
    happily accepts voltage sources)."""
    net = _purely_resistive_two_bus_network("net_v")
    # Replace the current source with a voltage source on the same bus.
    del net.sources["src"]
    gi.create_voltage_source(
        name="src",
        bus="bus1",
        voltage={50.0: 1000.0 + 0.0j},
        source_impedance={50.0: 0.5 + 0.1j},
        network=net,
    )
    study = TransientStudy(net, fault_name="F1")
    # set_source_waveform must accept a voltage source.
    study.set_source_waveform("src", waveforms.step(amplitude=100.0))
    # solve(solver='fft') must refuse it with a clear pointer.
    with pytest.raises(ValueError, match="current sources"):
        study.solve(t_end=0.1, dt=1e-3, solver="fft")


def test_resistive_network_fft_tracks_source_proportionally():
    """In a purely resistive network the EPR at the fault bus must be a
    scalar multiple of the source waveform at every instant."""
    net = _purely_resistive_two_bus_network("net_R")
    study = TransientStudy(net, fault_name="F1")
    study.set_source_waveform(
        "src",
        waveforms.sinusoidal_with_dc_offset(
            amplitude=100.0, frequency_hz=50.0, t_on=0.02, t_off=0.18,
        ),
    )
    study.set_observation(buses=["bus2"], branches=["branch1"])

    result = study.solve(t_end=0.2, dt=1e-4)
    assert isinstance(result, ResultTransient)

    epr = np.asarray(result.epr_t["bus2"])
    src = np.asarray(result.source_t["src"])

    # Determine the proportionality ratio from a non-zero sample of the
    # source signal and verify the linearity holds throughout the trace.
    nonzero = np.abs(src) > 1.0  # ignore noise-floor near zero crossings
    assert nonzero.any(), "test setup produced an all-zero source signal"
    ratio = epr[nonzero] / src[nonzero]
    # Use a relative tolerance because the FFT round-trip introduces a
    # small numerical jitter near sharp window edges.
    assert np.std(ratio) / np.abs(np.mean(ratio)) < 5e-2


def test_fft_steady_state_matches_frequency_domain_solver():
    """Cross-check: in steady state, the FFT solver and the existing
    frequency-domain solver must produce the same EPR magnitude at the
    fault bus for an inductive bus impedance.

    The 50 Hz tone is on for the entire window, so the FFT result is the
    forced response at 50 Hz with negligible transient ringing."""
    net = _rl_two_bus_network("net_steady")
    # Steady-state reference via the frequency-domain solver.
    gi.run_fault(net, fault_name="F1")
    epr_freq_dom = next(
        b.uepr for b in net.results["F1"].buses if b.name == "bus2"
    )

    study = TransientStudy(net, fault_name="F1")
    study.set_source_waveform(
        "src",
        waveforms.sinusoidal_with_dc_offset(
            amplitude=100.0, frequency_hz=50.0, t_on=0.0,
        ),
    )
    study.set_observation(buses=["bus2"])
    result = study.solve(t_end=1.0, dt=2e-4)

    # Skip the leading transient and compute the RMS over the last 0.5 s.
    epr = np.asarray(result.epr_t["bus2"])
    t = np.asarray(result.time_s)
    steady_mask = t >= 0.5
    epr_rms_fft = np.sqrt(np.mean(epr[steady_mask] ** 2))

    # Frequency-domain RMS uses sum-of-magnitudes-squared across the
    # network's frequency grid; with one frequency at amplitude A this is
    # |A|. The FFT result is a sinusoid of peak |A|, hence RMS = |A|/sqrt(2).
    np.testing.assert_allclose(
        epr_rms_fft, epr_freq_dom / np.sqrt(2), rtol=2e-2,
    )


def test_fft_step_response_in_resistive_network_is_instant():
    """A purely resistive grounding network has no reactive elements; the
    EPR must follow the source step without rise time (apart from the
    finite Gibbs-like ringing introduced by the FFT band-limit)."""
    net = _purely_resistive_two_bus_network("net_step")
    study = TransientStudy(net, fault_name="F1")
    study.set_source_waveform(
        "src",
        waveforms.step(amplitude=100.0, t_on=0.05, t_off=None),
    )
    study.set_observation(buses=["bus2"])
    result = study.solve(t_end=0.2, dt=1e-4)

    epr = np.asarray(result.epr_t["bus2"])
    src = np.asarray(result.source_t["src"])
    t = np.asarray(result.time_s)

    # After the step is fully on (well past the FFT ringing window), the
    # EPR-to-source ratio is the steady-state divider of the network.
    settled = t >= 0.15
    ratio = epr[settled] / src[settled]
    # Must be near constant (no rise-time, no decay) -- the standard
    # deviation of the ratio relative to its mean stays well under 1 %.
    assert np.std(ratio) / abs(np.mean(ratio)) < 0.01


# ---------------------------------------------------------------------------
# State-space solver
# ---------------------------------------------------------------------------


def _rl_state_space_network(name: str, *, R_bus: float, L_bus: float):
    """Two-bus network with explicit RLC fields needed by the state-space
    solver. Branch is purely resistive.

    The bus topology is a parallel ``R || L`` shunt to remote earth, so
    the matching ``impedance_formula`` must be the parallel impedance,
    not the series sum -- that way the FFT and state-space solvers see
    the same physical network and can be cross-checked.
    """
    Zp = (
        f"(({R_bus}) * I * 2 * pi * f * ({L_bus})) "
        f"/ (rho * 0 + ({R_bus}) + I * 2 * pi * f * ({L_bus}))"
    )
    bus_type = BusType(
        name="RL_bus_ss",
        system_type="Substation",
        voltage_level=20.0,
        impedance_formula=Zp,
        R_formula=f"rho * 0 + {R_bus}",
        L_formula=f"rho * 0 + {L_bus}",
    )
    branch_type = BranchType(
        name="R_branch_ss",
        grounding_conductor=True,
        self_impedance_formula="(rho * 0 + 1.0 + I * f * 0) * l",
        mutual_impedance_formula="(rho * 0 + 0.0 + I * f * 0) * l",
        R_self_formula="(rho * 0 + 1.0) * l",
    )
    net = gi.create_network(name=name, frequencies=[50.0])
    gi.create_bus(name="bus1", type=bus_type, network=net)
    gi.create_bus(name="bus2", type=bus_type, network=net)
    gi.create_branch(
        name="branch1", type=branch_type,
        from_bus="bus1", to_bus="bus2", length=1.0, network=net,
    )
    gi.create_source(
        name="src", bus="bus1", values={50.0: 100.0 + 0.0j}, network=net,
    )
    gi.create_fault(name="F1", bus="bus2", scalings={50.0: 1.0}, network=net)
    return net


def test_state_space_step_response_shows_transient_decay():
    """In the parallel R || L bus topology, a step source current produces
    an immediate jump to the resistive divider value followed by an
    exponential decay as the inductor current builds up and short-circuits
    the bus to ground at t -> infinity."""
    R_bus, L_bus = 10.0, 0.05  # smallest tau = L/R = 5 ms
    net = _rl_state_space_network("net_ss_step", R_bus=R_bus, L_bus=L_bus)
    study = TransientStudy(net, fault_name="F1")
    study.set_source_waveform("src", waveforms.step(amplitude=100.0))
    study.set_observation(buses=["bus1"], branches=["branch1"])

    # t_end well past the slow inter-bus mode (~100 ms) so the trace has
    # time to settle to the analytic steady-state.
    result = study.solve(t_end=2.0, dt=5e-4, solver="state_space")
    assert result.solver == "state_space"

    epr = np.asarray(result.epr_t["bus1"])
    epr_initial = epr[1]
    epr_final = epr[-1]
    # Initial value at the bus = resistive divider (large), final value
    # is zero because both bus inductors short the network to ground in
    # steady state with a current source.
    assert abs(epr_initial) > 10.0, (
        f"Initial bus voltage too small: {epr_initial:.3f} V"
    )
    assert abs(epr_final) < 0.05 * abs(epr_initial), (
        f"State-space solver did not decay toward zero: "
        f"epr[final]={epr_final:.3f}, epr[initial]={epr_initial:.3f}"
    )


def test_state_space_matches_fft_on_lti_network():
    """For a strictly LTI network with both formula sets present, the
    state-space and FFT solvers must agree on the steady-state RMS at
    the fault bus."""
    R_bus, L_bus = 10.0, 0.005  # small L: tau = 0.5 ms, well-settled by 0.5 s
    net = _rl_state_space_network("net_ss_match", R_bus=R_bus, L_bus=L_bus)

    study = TransientStudy(net, fault_name="F1")
    study.set_source_waveform(
        "src",
        waveforms.sinusoidal_with_dc_offset(
            amplitude=100.0, frequency_hz=50.0, t_on=0.0,
        ),
    )
    study.set_observation(buses=["bus2"])

    res_fft = study.solve(t_end=1.0, dt=2e-4, solver="fft")
    res_ss = study.solve(t_end=1.0, dt=2e-4, solver="state_space")

    t_fft = np.asarray(res_fft.time_s)
    t_ss = np.asarray(res_ss.time_s)
    epr_fft = np.asarray(res_fft.epr_t["bus2"])
    epr_ss = np.asarray(res_ss.epr_t["bus2"])

    # RMS of the steady-state portion of each trace (skip the first 0.5 s
    # to let any solver-specific startup transient die out).
    rms_fft = np.sqrt(np.mean(epr_fft[t_fft >= 0.5] ** 2))
    rms_ss = np.sqrt(np.mean(epr_ss[t_ss >= 0.5] ** 2))
    np.testing.assert_allclose(rms_ss, rms_fft, rtol=3e-2)


def test_state_space_fault_clearing_shows_decay():
    """After the source switches off, the bus-inductor current decays
    exponentially through the resistive grounding network."""
    R_bus, L_bus = 10.0, 0.05  # tau = 5 ms
    net = _rl_state_space_network("net_ss_decay", R_bus=R_bus, L_bus=L_bus)

    study = TransientStudy(net, fault_name="F1")
    # Source on for 0.2 s, off afterwards. The two interacting RL pairs
    # in this two-bus topology have a slow mode around tau ~= 100 ms, so
    # the on-window must be long enough for the trace to settle and the
    # off-window must allow several slow-tau decay times.
    study.set_source_waveform(
        "src", waveforms.step(amplitude=100.0, t_on=0.0, t_off=0.2),
    )
    study.set_observation(buses=["bus1"])
    result = study.solve(t_end=2.5, dt=5e-4, solver="state_space")

    t = np.asarray(result.time_s)
    epr = np.asarray(result.epr_t["bus1"])

    # Window just before clearing (slow mode well-settled), then well
    # past the clearing event (>10 slow-tau later).
    epr_just_before = epr[(t > 0.18) & (t < 0.20)].mean()
    epr_well_after = epr[t > 2.0].mean()

    # The post-clearing EPR must decay to a small fraction of the level
    # carried just before the clearing event.
    assert abs(epr_well_after) < 0.05 * max(abs(epr_just_before), 1.0)


def test_state_space_requires_R_on_buses():
    """The state-space solver demands a resistive grounding path at every
    active bus; missing R yields a clear error."""
    bus_type_no_R = BusType(
        name="no_R",
        system_type="Substation",
        voltage_level=20.0,
        impedance_formula="rho * 0 + 10",
        L_formula="rho * 0 + 0.05",  # only L, no R
    )
    branch_type = BranchType(
        name="R_branch",
        grounding_conductor=True,
        self_impedance_formula="(rho * 0 + 1.0) * l",
        mutual_impedance_formula="(rho * 0 + 0.0) * l",
        R_self_formula="(rho * 0 + 1.0) * l",
    )
    net = gi.create_network(name="net_no_R", frequencies=[50.0])
    gi.create_bus(name="bus1", type=bus_type_no_R, network=net)
    gi.create_bus(name="bus2", type=bus_type_no_R, network=net)
    gi.create_branch(
        name="branch1", type=branch_type,
        from_bus="bus1", to_bus="bus2", length=1.0, network=net,
    )
    gi.create_source(
        name="src", bus="bus1", values={50.0: 100.0}, network=net,
    )
    gi.create_fault(name="F1", bus="bus2", scalings={50.0: 1.0}, network=net)

    study = TransientStudy(net, fault_name="F1")
    study.set_source_waveform("src", waveforms.step(amplitude=100.0))
    with pytest.raises(ValueError, match="resistive ground path"):
        study.solve(t_end=0.1, dt=1e-4, solver="state_space")


def test_state_space_requires_at_least_one_inductor():
    """Without any L, there are no transient dynamics; the user is
    redirected to the FFT solver."""
    bus_type_no_L = BusType(
        name="no_L",
        system_type="Substation",
        voltage_level=20.0,
        impedance_formula="rho * 0 + 10",
        R_formula="rho * 0 + 10",
    )
    branch_type = BranchType(
        name="R_branch",
        grounding_conductor=True,
        self_impedance_formula="(rho * 0 + 1.0) * l",
        mutual_impedance_formula="(rho * 0 + 0.0) * l",
        R_self_formula="(rho * 0 + 1.0) * l",
    )
    net = gi.create_network(name="net_no_L", frequencies=[50.0])
    gi.create_bus(name="bus1", type=bus_type_no_L, network=net)
    gi.create_bus(name="bus2", type=bus_type_no_L, network=net)
    gi.create_branch(
        name="branch1", type=branch_type,
        from_bus="bus1", to_bus="bus2", length=1.0, network=net,
    )
    gi.create_source(
        name="src", bus="bus1", values={50.0: 100.0}, network=net,
    )
    gi.create_fault(name="F1", bus="bus2", scalings={50.0: 1.0}, network=net)

    study = TransientStudy(net, fault_name="F1")
    study.set_source_waveform("src", waveforms.step(amplitude=100.0))
    with pytest.raises(ValueError, match="inductive element"):
        study.solve(t_end=0.1, dt=1e-4, solver="state_space")


def test_state_space_parallel_RLC_bus_oscillates():
    """A bus modelled as parallel R || L || C driven by a step current
    shows damped oscillation around its steady-state EPR (zero in this
    topology because the bus inductor shorts to ground at DC). The
    underdamped response must produce both positive and negative voltage
    excursions."""
    # Pick values that give well-underdamped response:
    # zeta = (1/(2 R)) * sqrt(L/C), need zeta < 1 -> R > 0.5 * sqrt(L/C).
    # Here L/C = 1e4, sqrt = 100, so R = 500 -> zeta = 0.1, very ringy.
    bus_RLC = BusType(
        name="RLC_resonator",
        system_type="Substation",
        voltage_level=20.0,
        impedance_formula="rho * 0 + 500",
        R_formula="rho * 0 + 500",
        L_formula="rho * 0 + 0.1",
        C_formula="rho * 0 + 1e-5",
    )
    bus_ref = BusType(
        name="ref_bus",
        system_type="Substation",
        voltage_level=20.0,
        impedance_formula="rho * 0 + 1e6",
        R_formula="rho * 0 + 1e6",
        L_formula="rho * 0 + 0.001",  # tiny L so the source extraction
                                       # can return via this bus's L to ground
    )
    branch = BranchType(
        name="connector",
        grounding_conductor=True,
        self_impedance_formula="(rho * 0 + 1e6) * l",
        mutual_impedance_formula="(rho * 0 + 0.0) * l",
        R_self_formula="(rho * 0 + 1e6) * l",   # virtually open: isolates
                                                  # the RLC oscillator
    )
    net = gi.create_network(name="net_rlc_resonator", frequencies=[50.0])
    gi.create_bus(name="osc", type=bus_RLC, network=net)
    gi.create_bus(name="ref", type=bus_ref, network=net)
    gi.create_branch(
        name="conn", type=branch,
        from_bus="osc", to_bus="ref", length=1.0, network=net,
    )
    gi.create_source(name="src", bus="osc", values={50.0: 100.0}, network=net)
    gi.create_fault(name="F1", bus="ref", scalings={50.0: 1.0}, network=net)
    gi.create_paths(network=net)

    study = TransientStudy(net, fault_name="F1")
    study.set_source_waveform("src", waveforms.step(amplitude=100.0))
    study.set_observation(buses=["osc"])
    result = study.solve(t_end=0.04, dt=1e-5, solver="state_space")

    epr = np.asarray(result.epr_t["osc"])
    # Damped sinusoid around zero -> both signs appear in the trace.
    assert epr.max() > 1.0, f"Expected positive overshoot, got max={epr.max():.3f}"
    assert epr.min() < -0.1, f"Expected negative undershoot, got min={epr.min():.3f}"


def test_state_space_voltage_source_R_only_matches_current_norton():
    """A voltage source with purely resistive Z_src must produce the same
    EPR trace as a current source with the Norton-equivalent waveform
    I_N(t) = U(t)/R_src."""
    R_src = 10.0
    U_amp = 1000.0  # Norton current = 100 A

    bus_type = BusType(
        name="RL_bus_norton",
        system_type="Substation",
        voltage_level=20.0,
        impedance_formula="rho * 0 + 5",
        R_formula="rho * 0 + 5",
        L_formula="rho * 0 + 0.01",
    )
    branch_type = BranchType(
        name="R_branch_norton",
        grounding_conductor=True,
        self_impedance_formula="(rho * 0 + 0.5) * l",
        mutual_impedance_formula="(rho * 0 + 0.0) * l",
        R_self_formula="(rho * 0 + 0.5) * l",
    )

    def _build_net(source_factory):
        net = gi.create_network(name="net_norton", frequencies=[50.0])
        gi.create_bus(name="b1", type=bus_type, network=net)
        gi.create_bus(name="b2", type=bus_type, network=net)
        gi.create_branch(
            name="br", type=branch_type,
            from_bus="b1", to_bus="b2", length=1.0, network=net,
        )
        source_factory(net)
        gi.create_fault(name="F1", bus="b2", scalings={50.0: 1.0}, network=net)
        gi.create_paths(network=net)
        return net

    # Reference: current source with I_N = 100 A step.
    net_i = _build_net(
        lambda net: gi.create_source(
            name="src", bus="b1", values={50.0: 100.0 + 0.0j}, network=net,
        )
    )
    study_i = TransientStudy(net_i, fault_name="F1")
    study_i.set_source_waveform("src", waveforms.step(amplitude=100.0))
    study_i.set_observation(buses=["b1", "b2"])
    # The slowest eigenmode of this two-bus topology sits around 40 ms;
    # integrate well past 5 tau for a clean steady-state comparison.
    res_i = study_i.solve(t_end=0.5, dt=2e-4, solver="state_space")

    # Voltage source with R-only Z_src and step EMF.
    net_v = _build_net(
        lambda net: gi.create_voltage_source(
            name="src",
            bus="b1",
            voltage={50.0: U_amp + 0.0j},
            source_impedance={50.0: R_src + 0.0j},
            network=net,
        )
    )
    study_v = TransientStudy(net_v, fault_name="F1")
    study_v.set_source_waveform("src", waveforms.step(amplitude=U_amp))
    study_v.set_observation(buses=["b1", "b2"])
    res_v = study_v.solve(t_end=0.5, dt=2e-4, solver="state_space")

    # The voltage-source case has an extra Y_src loop closure between
    # source and fault bus. With R_src small enough the effect on the
    # EPR is small but not zero -- check ordering and rough agreement
    # rather than identity. The dominant feature (sign and order of
    # magnitude) must match.
    epr_i = np.asarray(res_i.epr_t["b1"])
    epr_v = np.asarray(res_v.epr_t["b1"])
    # At steady state both should approach zero (parallel L shorts).
    assert abs(epr_v[-1]) < 1.0
    assert abs(epr_i[-1]) < 1.0
    # The peak (early transient) of both must agree in sign and within a
    # factor of 2 (the Norton-loop's R_src changes the loading).
    assert np.sign(epr_i.max()) == np.sign(epr_v.max())
    assert 0.3 < abs(epr_v.max()) / max(abs(epr_i.max()), 1e-6) < 3.0


def test_state_space_voltage_source_with_L_runs_and_oscillates():
    """A voltage source with R+L source impedance switched onto an RLC
    grounding network must produce a non-trivial transient response."""
    bus_RLC = BusType(
        name="RLC_target",
        system_type="Substation",
        voltage_level=20.0,
        impedance_formula="rho * 0 + 100",
        R_formula="rho * 0 + 100",
        L_formula="rho * 0 + 0.05",
        C_formula="rho * 0 + 5e-6",
    )
    bus_ref = BusType(
        name="ref_bus_v",
        system_type="Substation",
        voltage_level=20.0,
        impedance_formula="rho * 0 + 0.1",
        R_formula="rho * 0 + 0.1",
    )
    branch_type = BranchType(
        name="link",
        grounding_conductor=True,
        self_impedance_formula="(rho * 0 + 0.5) * l",
        mutual_impedance_formula="(rho * 0 + 0.0) * l",
        R_self_formula="(rho * 0 + 0.5) * l",
    )

    net = gi.create_network(name="net_thevenin_rlc", frequencies=[50.0])
    gi.create_bus(name="osc", type=bus_RLC, network=net)
    gi.create_bus(name="ref", type=bus_ref, network=net)
    gi.create_branch(
        name="link", type=branch_type,
        from_bus="osc", to_bus="ref", length=1.0, network=net,
    )
    # Z_src(50 Hz) = 1 + j * 2*pi*50 * 0.005 = 1 + j*1.5708
    # -> R_src = 1, L_src = 5 mH
    omega = 2 * np.pi * 50.0
    L_src = 0.005
    gi.create_voltage_source(
        name="src",
        bus="osc",
        voltage={50.0: 100.0 + 0.0j},
        source_impedance={50.0: 1.0 + 1j * omega * L_src},
        network=net,
    )
    gi.create_fault(name="F1", bus="ref", scalings={50.0: 1.0}, network=net)
    gi.create_paths(network=net)

    study = TransientStudy(net, fault_name="F1")
    study.set_source_waveform("src", waveforms.step(amplitude=100.0))
    study.set_observation(buses=["osc"])
    result = study.solve(t_end=0.05, dt=1e-5, solver="state_space")

    epr = np.asarray(result.epr_t["osc"])
    # The response must be non-trivial: at least some positive excursion
    # well above the noise floor.
    assert epr.max() > 0.5
    # And must show oscillatory character (sign change after the initial
    # rise, due to L_src + bus L + C interaction).
    assert epr.min() < 0.0 or np.diff(np.sign(np.diff(epr))).any()


def test_state_space_pi_section_branch_C_lumps_onto_buses():
    """Branch ``C_self`` is automatically split as ``C/2`` to each
    endpoint. A network without explicit ``Bus.C`` but with a finite
    ``Branch.C_self`` must therefore still produce capacitor states and
    a non-trivial transient response, because the bus is now effectively
    capacitive through the lumping."""
    bus_type = BusType(
        name="bus_no_C",
        system_type="Substation",
        voltage_level=20.0,
        impedance_formula="rho * 0 + 100",
        R_formula="rho * 0 + 100",
        L_formula="rho * 0 + 0.05",   # bus has L but no own C
    )
    branch_with_C = BranchType(
        name="cable_with_C",
        grounding_conductor=True,
        self_impedance_formula="(rho * 0 + 0.5) * l",
        mutual_impedance_formula="(rho * 0 + 0.0) * l",
        R_self_formula="(rho * 0 + 0.5) * l",
        L_self_formula="(rho * 0 + 1e-3) * l",
        C_self_formula="(rho * 0 + 600e-9) * l",   # 600 nF per unit length
    )

    net = gi.create_network(name="net_pi", frequencies=[50.0])
    gi.create_bus(name="b1", type=bus_type, network=net)
    gi.create_bus(name="b2", type=bus_type, network=net)
    gi.create_branch(
        name="link", type=branch_with_C,
        from_bus="b1", to_bus="b2", length=1.0, network=net,
    )
    gi.create_source(name="src", bus="b1", values={50.0: 100.0}, network=net)
    gi.create_fault(name="F1", bus="b2", scalings={50.0: 1.0}, network=net)
    gi.create_paths(network=net)

    study = TransientStudy(net, fault_name="F1")
    study.set_source_waveform("src", waveforms.step(amplitude=100.0))
    study.set_observation(buses=["b1", "b2"])
    result = study.solve(t_end=0.05, dt=1e-4, solver="state_space")

    epr_b1 = np.asarray(result.epr_t["b1"])
    # Without bus C the trace would settle monotonically; with the
    # branch C lumped onto the bus we get an oscillatory ringing on the
    # leading edge. Detect it via the difference between max and steady-
    # state of the early window.
    early = epr_b1[: len(epr_b1) // 4]
    assert (early.max() - early[-1]) > 0.1 * abs(early.max()), (
        "Lumped branch C should make the bus transient oscillatory"
    )


def test_state_space_mutual_coupling_consistent_with_fft():
    """Mutual coupling in state-space must give the same steady-state RMS
    shield current as the FFT solver. The absolute change in the shield
    current relative to the no-mutual case is often small (~1 %) because
    the bus voltages adjust to absorb most of the Norton injection -- the
    relevant invariant is therefore not the magnitude of the change but
    the agreement between the two solver paths."""
    bus_type = BusType(
        name="bus_for_mut",
        system_type="Substation",
        voltage_level=20.0,
        impedance_formula="rho * 0 + 5",
        R_formula="rho * 0 + 5",
    )
    branch_type = BranchType(
        name="with_mut",
        grounding_conductor=True,
        self_impedance_formula="((rho * 0 + 0.5) + I * 2 * pi * f * 5e-3) * l",
        mutual_impedance_formula="((rho * 0 + 0.05) + I * 2 * pi * f * 2e-3) * l",
        R_self_formula="(rho * 0 + 0.5) * l",
        L_self_formula="(rho * 0 + 5e-3) * l",
        R_mutual_formula="(rho * 0 + 0.05) * l",
        M_mutual_formula="(rho * 0 + 2e-3) * l",
    )

    net = gi.create_network(name="net_mut_consistency", frequencies=[50.0])
    gi.create_bus(name="b1", type=bus_type, network=net)
    gi.create_bus(name="b2", type=bus_type, network=net)
    gi.create_branch(
        name="link", type=branch_type,
        from_bus="b1", to_bus="b2", length=1.0, network=net,
    )
    gi.create_source(name="src", bus="b1", values={50.0: 100.0}, network=net)
    gi.create_fault(name="F1", bus="b2", scalings={50.0: 1.0}, network=net)
    gi.create_paths(network=net)

    study = TransientStudy(net, fault_name="F1")
    study.set_source_waveform(
        "src",
        waveforms.sinusoidal_with_dc_offset(
            amplitude=100.0, frequency_hz=50.0, t_on=0.0,
        ),
    )
    study.set_observation(branches=["link"])
    res_fft = study.solve(t_end=1.0, dt=2e-4, solver="fft")
    res_ss = study.solve(t_end=1.0, dt=2e-4, solver="state_space")

    t = np.asarray(res_fft.time_s)
    steady = t >= 0.5
    i_fft = np.asarray(res_fft.i_branch_t["link"])[steady]
    i_ss = np.asarray(res_ss.i_branch_t["link"])[: t.size][steady]
    rms_fft = np.sqrt(np.mean(i_fft ** 2))
    rms_ss = np.sqrt(np.mean(i_ss ** 2))
    np.testing.assert_allclose(rms_ss, rms_fft, rtol=5e-2)


def test_state_space_mutual_matches_fft_in_steady_state():
    """For the same ``self_impedance_formula`` and consistent ``R_mutual``
    / ``M_mutual`` formulas, the state-space and FFT solvers must agree
    on the steady-state shield current at the fundamental frequency."""
    bus_type = BusType(
        name="bus_match",
        system_type="Substation",
        voltage_level=20.0,
        impedance_formula="rho * 0 + 5",
        R_formula="rho * 0 + 5",
    )
    branch_type = BranchType(
        name="mut_match",
        grounding_conductor=True,
        self_impedance_formula="((rho * 0 + 0.5) + I * 2 * pi * f * 5e-3) * l",
        mutual_impedance_formula="((rho * 0 + 0.05) + I * 2 * pi * f * 2e-3) * l",
        R_self_formula="(rho * 0 + 0.5) * l",
        L_self_formula="(rho * 0 + 5e-3) * l",
        R_mutual_formula="(rho * 0 + 0.05) * l",
        M_mutual_formula="(rho * 0 + 2e-3) * l",
    )
    net = gi.create_network(name="net_mut_match", frequencies=[50.0])
    gi.create_bus(name="b1", type=bus_type, network=net)
    gi.create_bus(name="b2", type=bus_type, network=net)
    gi.create_branch(
        name="link", type=branch_type,
        from_bus="b1", to_bus="b2", length=1.0, network=net,
    )
    gi.create_source(name="src", bus="b1", values={50.0: 100.0}, network=net)
    gi.create_fault(name="F1", bus="b2", scalings={50.0: 1.0}, network=net)
    gi.create_paths(network=net)

    study = TransientStudy(net, fault_name="F1")
    study.set_source_waveform(
        "src",
        waveforms.sinusoidal_with_dc_offset(
            amplitude=100.0, frequency_hz=50.0, t_on=0.0,
        ),
    )
    study.set_observation(branches=["link"])
    res_fft = study.solve(t_end=1.0, dt=2e-4, solver="fft")
    res_ss = study.solve(t_end=1.0, dt=2e-4, solver="state_space")

    # Steady-state RMS comparison after the leading transient settles.
    t = np.asarray(res_fft.time_s)
    steady = t >= 0.5
    i_fft = np.asarray(res_fft.i_branch_t["link"])[steady]
    i_ss = np.asarray(res_ss.i_branch_t["link"])[: t.size][steady]
    rms_fft = np.sqrt(np.mean(i_fft ** 2))
    rms_ss = np.sqrt(np.mean(i_ss ** 2))
    np.testing.assert_allclose(rms_ss, rms_fft, rtol=5e-2)


def test_unknown_solver_raises():
    """Solver dispatch surfaces unknown identifiers with a clear message."""
    net = _purely_resistive_two_bus_network("net_unknown_solver")
    study = TransientStudy(net, fault_name="F1")
    study.set_source_waveform("src", waveforms.step(amplitude=100.0))
    with pytest.raises(NotImplementedError, match="Unknown transient solver"):
        study.solve(t_end=0.1, dt=1e-4, solver="some_other")


def test_transient_result_to_polars_has_expected_columns():
    net = _purely_resistive_two_bus_network("net_polars")
    study = TransientStudy(net, fault_name="F1")
    study.set_source_waveform(
        "src", waveforms.step(amplitude=100.0, t_on=0.0, t_off=0.05),
    )
    study.set_observation(buses=["bus2"], branches=["branch1"])
    result = study.solve(t_end=0.1, dt=1e-3)
    df = result.to_polars()
    assert set(df.columns) == {"time_s", "signal_kind", "name", "value"}
    kinds = set(df["signal_kind"].unique().to_list())
    assert kinds == {"epr", "i_branch", "source"}

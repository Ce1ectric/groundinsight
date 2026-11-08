# tests/test_audit_pass18_transient.py

"""
Audit pass 18 -- the two transient solver paths against their own promises.

Eight findings, all of them cases where a transient result differed from what
the same network yields elsewhere, without the difference being visible:

* **S1** -- the FFT solver ignores mutual coupling (the stationary and the
  state-space solvers do not) and said nothing about it.
* **S3** -- the FFT solver injected sources with no galvanic connection to the
  fault; the stationary solver filters them out through the paths.
* **S4** -- the state-space solver read its lumped R/L/C at
  ``frequencies[0]``, so prepending a 0 Hz bin to the frequency list moved the
  EPR by a factor of 680 via ``L_src = Im(Z_src)/omega -> 0``.
* **S5** -- the FFT window's periodicity assumption was never checked, so a
  response that had not decayed wrapped around into the pre-fault interval.
* **S9** -- ``Fault.scalings`` is ignored by both transient solvers, recorded
  only in an inline comment.
* **S10** -- negative lumped R/L/C were dropped by the positivity guards, i.e.
  a sign error came back as a lossless network.
* **S11** -- the two solvers used different time grids for the same arguments.
* **S20** -- ``__vsrc__`` is an internal sentinel that a real branch name could
  collide with.

Each test fails on the pre-fix code and passes after it.
"""

from __future__ import annotations

import warnings

import numpy as np
import pytest

import groundinsight as gi
from groundinsight.models.core_models import (
    BranchType,
    BusType,
    TransientModelWarning,
)
from groundinsight.simulation import waveforms
from groundinsight.simulation.transient import TransientStudy, _time_grid
from tests._warning_policy import acknowledge

#: The cross-solver cases start the FFT grid at 0 Hz, end the window before
#: every ring-down has decayed and include a source without a path to the
#: fault; the tests compare the solvers, not those conditions.
pytestmark = acknowledge(
    "DC_LIMIT", "TRANSIENT_RINGING", "TRANSIENT_SOURCE_NOT_CONNECTED"
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _rl_network(name: str, *, R_bus: float = 10.0, L_bus: float = 0.05):
    """Two buses with a parallel ``R || L`` shunt and a resistive link.

    The ``impedance_formula`` is the parallel impedance of the same R and L,
    so the FFT and the state-space solver see one physical network.
    """
    Zp = (
        f"(({R_bus}) * I * 2 * pi * f * ({L_bus})) "
        f"/ (rho * 0 + ({R_bus}) + I * 2 * pi * f * ({L_bus}))"
    )
    bus_type = BusType(
        name="RL_bus",
        system_type="Substation",
        voltage_level=20.0,
        impedance_formula=Zp,
        R_formula=f"rho * 0 + {R_bus}",
        L_formula=f"rho * 0 + {L_bus}",
    )
    branch_type = BranchType(
        name="R_branch",
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


def _resistive_network(name: str, *, frequencies=(50.0,)):
    """Frequency-flat two-bus network; used where only topology matters."""
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
    net = gi.create_network(name=name, frequencies=list(frequencies))
    for bus in ("bus1", "bus2", "island"):
        gi.create_bus(name=bus, type=bus_type, network=net)
    gi.create_branch(
        name="branch1", type=branch_type,
        from_bus="bus1", to_bus="bus2", length=1.0, network=net,
    )
    values = {float(f): 100.0 + 0.0j for f in frequencies}
    gi.create_source(name="src", bus="bus1", values=values, network=net)
    gi.create_fault(
        name="F1",
        bus="bus2",
        scalings={float(f): 1.0 for f in frequencies},
        network=net,
    )
    return net


def _study(
    net,
    *,
    buses=("bus2",),
    branches=(),
    amplitude=100.0,
    t_on=0.0,
    t_off=None,
):
    study = TransientStudy(net, fault="F1")
    study.set_source_waveform(
        "src", waveforms.step(amplitude=amplitude, t_on=t_on, t_off=t_off)
    )
    study.set_observation(buses=list(buses), branches=list(branches))
    return study


# ---------------------------------------------------------------------------
# S1 -- the FFT solver says that it drops the mutual coupling
# ---------------------------------------------------------------------------


def _coupled_network(name: str):
    net = _rl_network(name)
    coupled = BranchType(
        name="coupled",
        grounding_conductor=True,
        self_impedance_formula="(rho * 0 + 1.0 + I * f * 0) * l",
        mutual_impedance_formula="(rho * 0 + 0.5 + I * f * 0) * l",
        R_self_formula="(rho * 0 + 1.0) * l",
        L_self_formula="(rho * 0 + 1e-3) * l",
        M_mutual_formula="(rho * 0 + 8e-4) * l",
    )
    net.branches["branch1"].type = coupled
    net.branches["branch1"].calculate_impedance(net.frequencies)
    return net


def test_fft_warns_when_it_drops_mutual_coupling():
    """S1: a coupled branch must not be dropped silently by the FFT solver."""
    net = _coupled_network("net_s1")
    study = _study(net)
    with pytest.warns(TransientModelWarning, match="mutual coupling"):
        study.solve(t_end=0.05, dt=1e-4, solver="fft")


def test_fft_is_silent_about_mutual_coupling_when_there_is_none():
    """The S1 warning must not fire on an uncoupled network."""
    net = _rl_network("net_s1_clean")
    study = _study(net, t_on=0.1, t_off=0.2)
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        study.solve(t_end=1.0, dt=1e-4, solver="fft")
    assert not [
        w for w in caught if "mutual coupling" in str(w.message)
    ]


# ---------------------------------------------------------------------------
# S3 -- only sources that can reach the fault inject
# ---------------------------------------------------------------------------


def test_fft_ignores_a_source_without_a_path_to_the_fault():
    """S3: an isolated source injected its full spectrum and corrupted every
    bus; the stationary solver excludes it, and now so does the FFT."""
    net = _resistive_network("net_s3")
    # Second source on the galvanically isolated bus.
    gi.create_source(
        name="ghost", bus="island", values={50.0: 100.0 + 0.0j}, network=net
    )

    study = TransientStudy(net, fault="F1")
    study.set_source_waveform("ghost", waveforms.step(amplitude=1000.0))
    study.set_observation(buses=["island", "bus2"])
    result = study.solve(t_end=0.05, dt=1e-4, solver="fft")

    # Stationary reference: the isolated source contributes nothing.
    gi.run_fault(net, fault="F1")
    stationary = {b.name: b.uepr for b in net.results["F1"].buses}
    assert stationary["island"] == pytest.approx(0.0, abs=1e-9)

    assert np.max(np.abs(result.epr_t["island"])) == pytest.approx(0.0, abs=1e-9)
    assert np.max(np.abs(result.epr_t["bus2"])) == pytest.approx(0.0, abs=1e-9)


def test_fft_still_injects_a_connected_source():
    """The S3 filter must not throw away the ordinary case."""
    net = _resistive_network("net_s3_ok")
    result = _study(net).solve(t_end=0.05, dt=1e-4, solver="fft")
    assert np.max(np.abs(result.epr_t["bus2"])) > 1.0


# ---------------------------------------------------------------------------
# S4 -- f_eval is a physical choice, not a list position
# ---------------------------------------------------------------------------


def _voltage_source_network(name: str, frequencies):
    net = _rl_network(name)
    net.frequencies = list(frequencies)
    for bus in net.buses.values():
        bus.calculate_impedance(net.frequencies)
    for branch in net.branches.values():
        branch.calculate_impedance(net.frequencies)
    del net.sources["src"]
    gi.create_voltage_source(
        name="src",
        bus="bus1",
        voltage={float(f): 1000.0 + 0.0j for f in frequencies},
        source_impedance={float(f): 0.5 + 3.0j for f in frequencies},
        network=net,
    )
    net.faults["F1"].scalings = {float(f): 1.0 for f in frequencies}
    return net


def _state_space_peak(frequencies, **kwargs):
    net = _voltage_source_network("net_s4", frequencies)
    study = _study(net, buses=("bus1",))
    result = study.solve(t_end=0.2, dt=1e-4, solver="state_space", **kwargs)
    return float(np.max(np.abs(result.epr_t["bus1"])))


def test_frequency_list_order_does_not_change_the_state_space_result():
    """S4: [50] and [0, 50] must give the same answer -- the second one used
    to read L_src at 0 Hz and silently produce a purely resistive source."""
    assert _state_space_peak([50.0]) == pytest.approx(
        _state_space_peak([0.0, 50.0]), rel=1e-12
    )


def test_f_eval_argument_selects_the_operating_point():
    """An explicit f_eval overrides the default (smallest positive)."""
    at_50 = _state_space_peak([50.0, 250.0])
    at_250 = _state_space_peak([50.0, 250.0], f_eval=250.0)
    # A different L_src is a different loop, so the peak has to move.
    assert at_50 != pytest.approx(at_250, rel=1e-6)


def test_f_eval_must_be_one_of_the_network_frequencies():
    net = _voltage_source_network("net_s4_bad", [50.0])
    with pytest.raises(ValueError, match="not one of the network's"):
        _study(net, buses=("bus1",)).solve(
            t_end=0.05, dt=1e-4, solver="state_space", f_eval=60.0
        )


def test_reactive_source_impedance_at_dc_is_rejected():
    """S4: Im(Z_src) != 0 at omega = 0 has no L_src; it must not be 0.0."""
    net = _voltage_source_network("net_s4_dc", [0.0])
    with pytest.raises(ValueError, match="not defined"):
        _study(net, buses=("bus1",)).solve(
            t_end=0.05, dt=1e-4, solver="state_space", f_eval=0.0
        )


# ---------------------------------------------------------------------------
# S5 -- the FFT periodicity assumption is checked, and can be worked around
# ---------------------------------------------------------------------------


def test_fft_warns_when_the_window_ends_while_the_response_rings():
    """S5: a 0.4 s window on a network with a ~100 ms mode is too short."""
    net = _rl_network("net_s5")
    study = _study(net, buses=("bus1",), t_on=0.1, t_off=0.2)
    with pytest.warns(TransientModelWarning, match="still ringing"):
        study.solve(t_end=0.4, dt=1e-4, solver="fft")


def _s5_traces():
    """One fault pulse, solved three ways on the same 0.4 s window."""
    kwargs = dict(buses=("bus1",), t_on=0.1, t_off=0.2)
    reference = _study(_rl_network("net_s5_ss"), **kwargs).solve(
        t_end=0.4, dt=1e-4, solver="state_space"
    )
    padded = _study(_rl_network("net_s5_pad"), **kwargs).solve(
        t_end=0.4, dt=1e-4, solver="fft", zero_pad=True
    )
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", TransientModelWarning)
        plain = _study(_rl_network("net_s5_plain"), **kwargs).solve(
            t_end=0.4, dt=1e-4, solver="fft"
        )
    return reference, padded, plain


def test_zero_pad_recovers_the_state_space_answer():
    """S5: with the window doubled, FFT and state-space agree to well under
    1 % after the fault; without it the wraparound costs several percent."""
    reference, padded, plain = _s5_traces()
    ref = np.asarray(reference.epr_t["bus1"])
    t = np.asarray(reference.time_s)
    after = t >= 0.1
    scale = np.max(np.abs(ref[after]))

    pad_err = np.max(np.abs(np.asarray(padded.epr_t["bus1"])[after] - ref[after]))
    plain_err = np.max(np.abs(np.asarray(plain.epr_t["bus1"])[after] - ref[after]))

    assert pad_err / scale < 0.01
    assert plain_err / scale > 0.02


def test_zero_pad_removes_the_phantom_pre_fault_potential():
    """S5: the wrapped tail shows up as an EPR *before* the fault -- the
    single most misleading artefact of a window that is too short."""
    reference, padded, plain = _s5_traces()
    t = np.asarray(reference.time_s)
    before = t < 0.1
    assert np.max(np.abs(np.asarray(reference.epr_t["bus1"])[before])) == 0.0
    assert np.max(np.abs(np.asarray(plain.epr_t["bus1"])[before])) > 1.0
    assert np.max(np.abs(np.asarray(padded.epr_t["bus1"])[before])) < 0.5


def test_zero_pad_silences_the_periodicity_warning_when_it_is_enough():
    """A window that really is long enough must not keep warning."""
    net = _rl_network("net_s5_quiet")
    study = _study(net, buses=("bus1",), t_on=0.1, t_off=0.2)
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        study.solve(t_end=0.4, dt=1e-4, solver="fft", zero_pad=True)
    assert not [w for w in caught if "still ringing" in str(w.message)]


# ---------------------------------------------------------------------------
# S9 -- fault scalings are ignored, loudly
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("solver", ["fft", "state_space"])
def test_both_solvers_warn_about_ignored_fault_scalings(solver):
    net = _rl_network(f"net_s9_{solver}")
    net.faults["F1"].scalings = {50.0: 3.0}
    study = _study(net, buses=("bus1",))
    with pytest.warns(TransientModelWarning, match="scalings"):
        study.solve(t_end=0.5, dt=1e-4, solver=solver)


# ---------------------------------------------------------------------------
# S10 / S14 -- passivity of the lumped RLC fields
# ---------------------------------------------------------------------------


def test_negative_branch_resistance_is_rejected():
    """S10: R_self = -0.5 used to be dropped, leaving a lossless branch."""
    net = _rl_network("net_s10")
    net.branches["branch1"].R_self = {50.0: -0.5}
    with pytest.raises(ValueError, match="R_self = -0.5 is negative"):
        _study(net, buses=("bus1",)).solve(
            t_end=0.05, dt=1e-4, solver="state_space"
        )


def test_negative_bus_inductance_is_rejected():
    net = _rl_network("net_s10_L")
    net.buses["bus1"].L = {50.0: -0.05}
    with pytest.raises(ValueError, match="L = -0.05 is negative"):
        _study(net, buses=("bus1",)).solve(
            t_end=0.05, dt=1e-4, solver="state_space"
        )


def test_mutual_inductance_above_self_inductance_warns():
    """S14: |M| > L_self is not realisable and is almost always a mH/H slip."""
    net = _rl_network("net_s14")
    net.branches["branch1"].L_self = {50.0: 1e-3}
    net.branches["branch1"].M_mutual = {50.0: 1.0}
    with pytest.warns(TransientModelWarning, match="larger"):
        _study(net, buses=("bus1",)).solve(
            t_end=0.05, dt=1e-4, solver="state_space"
        )


# ---------------------------------------------------------------------------
# S11 -- one time grid for both solvers
# ---------------------------------------------------------------------------


def test_both_solvers_return_the_same_time_grid():
    net = _rl_network("net_s11")
    fft = _study(net, buses=("bus1",)).solve(t_end=0.5, dt=1e-3, solver="fft")
    ss = _study(net, buses=("bus1",)).solve(
        t_end=0.5, dt=1e-3, solver="state_space"
    )
    assert fft.time_s == ss.time_s
    assert len(fft.epr_t["bus1"]) == len(ss.epr_t["bus1"])


def test_time_grid_is_even_and_covers_the_requested_window():
    for t_end, dt in ((0.02, 1e-3), (0.021, 1e-3), (0.4, 1e-4)):
        t, n = _time_grid(t_end, dt)
        assert n % 2 == 0
        assert n * dt >= t_end - 1e-12
        assert t[0] == 0.0
        np.testing.assert_allclose(np.diff(t), dt)


# ---------------------------------------------------------------------------
# S20 -- the __vsrc__ sentinel is reserved
# ---------------------------------------------------------------------------


def test_branch_named_like_the_voltage_source_sentinel_is_rejected():
    net = _rl_network("net_s20")
    branch = net.branches.pop("branch1")
    branch.name = "__vsrc__src"
    net.branches["__vsrc__src"] = branch
    with pytest.raises(ValueError, match="reserves"):
        _study(net, buses=("bus1",)).solve(
            t_end=0.05, dt=1e-4, solver="state_space"
        )

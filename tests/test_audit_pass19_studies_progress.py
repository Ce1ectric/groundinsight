# tests/test_audit_pass19_studies_progress.py
"""
Audit pass 19 -- progress reports and cooperative cancellation in studies (GUI5).

A GUI runs a study in a worker thread, shows a progress bar and offers a
cancel button. Python cannot stop a thread from outside, so cancellation has
to be cooperative, and before this pass none of the five long-running entry
points -- ``run_sweep``, ``run_outage_study``, ``find_max_rho_scaling``,
``find_max_rho_f_scaling`` and ``TransientStudy.solve`` -- had a hook.

The protocol pinned here: ``progress(done, total, label)`` after every unit
of work with ``done`` counting up by one, ``should_cancel()`` before every
unit, and ``gi.StudyCancelled`` (a ``RuntimeError``) on cancellation with the
network restored to the exact state it had when the study was called --
serialised form, stored results, active fault, paths and assembled system.
The number of progress calls is checked against a second, independent count
(the ``run_fault`` calls the study made, or the FFT bins of the result).

``run_sweep`` documents that it leaves the network "exactly as found,
whatever happens"; it left the result and the assembled system of its last
point on the network instead. That is pinned here as well (ARC1/GUI5).
"""

from __future__ import annotations

import math
import warnings

import numpy as np
import pytest

import groundinsight as gi

FREQ = [50.0, 250.0]


# --------------------------------------------------------------------------
# builders and helpers
# --------------------------------------------------------------------------


def _bus_type() -> gi.BusType:
    return gi.BusType(
        name="bt", system_type="MV", voltage_level=20.0,
        impedance_formula="0.1*rho + 0.5",
        R_formula="0.1*rho + 0.5", L_formula="1e-4",
    )


def _branch_type() -> gi.BranchType:
    return gi.BranchType(
        name="brt", grounding_conductor=True,
        self_impedance_formula="(0.1+0.2*j*f/50)*l",
        mutual_impedance_formula="(0.05+0.1*j*f/50)*l",
        phase_impedance_formula="(0.2+0.4*j*f/50)*l",
        R_self_formula="0.1*l", L_self_formula="0.2/(2*pi*50)*l",
    )


def _ring() -> gi.Network:
    """Four-bus ring, source at R0, faults at R2 (``F``) and R1 (``F1``)."""
    net = gi.create_network("ring", frequencies=FREQ)
    names = [f"R{i}" for i in range(4)]
    for name in names:
        gi.create_bus(name, _bus_type(), 100.0, network=net)
    for i in range(4):
        gi.create_branch(f"L{i}", _branch_type(), names[i], names[(i + 1) % 4],
                         1.0 + 0.5 * i, network=net)
    gi.create_fault("F", "R2", {50.0: 1.0, 250.0: 0.2}, network=net)
    gi.create_fault("F1", "R1", {50.0: 1.0, 250.0: 0.2}, network=net)
    gi.create_source("SRC", "R0", {50.0: 800.0, 250.0: 100.0}, network=net)
    return net


def _solved_ring() -> gi.Network:
    """The ring with results for both faults, ``F1`` active: a study that
    forgets to restore results, the active fault or the assembled system
    shows up against this state."""
    net = _ring()
    gi.run_fault(net, "F")
    gi.run_fault(net, "F1")
    return net


def _state(net: gi.Network):
    """Serialised form plus the identities and private state of a network."""
    return {
        "json": net.model_dump_json(),
        "results_id": id(net.results),
        "results": [(name, id(result)) for name, result in net.results.items()],
        "paths_id": id(net.paths),
        "fingerprint": net._paths_fingerprint,
        "active_fault": net.active_fault,
        "fault_flags": {name: fault.active for name, fault in net.faults.items()},
        "electrical_network": id(net.electrical_network),
        "fields_set": set(net.__pydantic_fields_set__),
    }


class _Recorder:
    """A progress callback that remembers every call."""

    def __init__(self):
        self.calls = []

    def __call__(self, done, total, label):
        self.calls.append((done, total, label))

    @property
    def done(self):
        return [call[0] for call in self.calls]

    def cancel_after(self, k):
        return lambda: len(self.calls) >= k

    def assert_well_formed(self):
        assert self.done == list(range(1, len(self.calls) + 1))
        totals = [call[1] for call in self.calls]
        assert totals == sorted(totals)
        assert all(done <= total for done, total, _ in self.calls)
        assert all(isinstance(label, str) and label for *_, label in self.calls)


@pytest.fixture
def run_fault_calls(monkeypatch):
    """Count every ``run_fault`` a study makes -- the independent count the
    progress calls are compared with."""
    import groundinsight.analysis.inverse_rho as inverse_rho
    import groundinsight.analysis.inverse_rho_f as inverse_rho_f
    import groundinsight.network_operations as network_operations
    import groundinsight.simulation.sweep as sweep

    original = network_operations.run_fault
    count = {"n": 0}

    def counting(*args, **kwargs):
        count["n"] += 1
        return original(*args, **kwargs)

    for module in (network_operations, sweep, inverse_rho, inverse_rho_f):
        monkeypatch.setattr(module, "run_fault", counting)
    return count


def _points():
    return [
        gi.SweepPoint(label=f"rho={rho:g}", bus_rho={"R2": rho})
        for rho in (20.0, 50.0, 100.0, 200.0, 500.0)
    ]


# --------------------------------------------------------------------------
# the exception
# --------------------------------------------------------------------------


def test_study_cancelled_is_an_exported_runtime_error():
    assert "StudyCancelled" in gi.__all__
    assert issubclass(gi.StudyCancelled, RuntimeError)
    error = gi.StudyCancelled("stop", done=3, total=10)
    assert (error.done, error.total) == (3, 10)


@pytest.mark.parametrize("hook", ["progress", "should_cancel"])
def test_a_hook_that_is_not_callable_is_refused(hook):
    with pytest.raises(TypeError, match=f"{hook} must be callable"):
        gi.run_sweep(_ring(), fault="F", points=_points(), **{hook: 42})


# --------------------------------------------------------------------------
# run_sweep
# --------------------------------------------------------------------------


def test_sweep_reports_one_call_per_point(run_fault_calls):
    recorder = _Recorder()
    study = gi.run_sweep(_ring(), fault="F", points=_points(), progress=recorder)
    recorder.assert_well_formed()
    assert recorder.calls == [
        (k + 1, 5, point.label) for k, point in enumerate(_points())
    ]
    assert run_fault_calls["n"] == len(recorder.calls) == len(study.labels)


def test_sweep_cancel_stops_before_the_next_point_and_restores_the_network(
    run_fault_calls,
):
    """GUI5: cancel after three points -- ``[1, 2, 3]``, then
    ``StudyCancelled`` before point four, network as found."""
    net = _solved_ring()
    before = _state(net)
    recorder = _Recorder()
    with pytest.raises(gi.StudyCancelled) as caught:
        gi.run_sweep(net, fault="F", points=_points(), progress=recorder,
                     should_cancel=recorder.cancel_after(3))
    assert recorder.done == [1, 2, 3]
    assert run_fault_calls["n"] == 3
    assert (caught.value.done, caught.value.total) == (3, 5)
    assert _state(net) == before


def test_sweep_cancel_before_the_first_point_does_nothing(run_fault_calls):
    net = _solved_ring()
    before = _state(net)
    recorder = _Recorder()
    with pytest.raises(gi.StudyCancelled):
        gi.run_sweep(net, fault="F", points=_points(), progress=recorder,
                     should_cancel=lambda: True)
    assert recorder.calls == [] and run_fault_calls["n"] == 0
    assert _state(net) == before


def test_sweep_results_do_not_depend_on_the_hooks():
    plain = gi.run_sweep(_ring(), fault="F", points=_points())
    hooked = gi.run_sweep(_ring(), fault="F", points=_points(),
                          progress=_Recorder(), should_cancel=lambda: False)
    assert plain.labels == hooked.labels
    assert plain.buses().equals(hooked.buses())
    assert plain.impedances().equals(hooked.impedances())


def test_a_failed_sweep_point_still_counts_as_done():
    points = _points()
    points.insert(2, gi.SweepPoint(label="bad", bus_rho={"nope": 1.0}))
    recorder = _Recorder()
    study = gi.run_sweep(_ring(), fault="F", points=points, progress=recorder)
    recorder.assert_well_formed()
    assert [label for *_, label in recorder.calls] == [p.label for p in points]
    assert "bad" in study.failures


def test_a_raising_progress_callback_is_not_recorded_as_a_failed_point():
    """A bug in the caller's callback must surface, not be filed under
    ``SweepResult.failures`` as if a parameter point had failed."""
    net = _solved_ring()
    before = _state(net)

    def broken(done, total, label):
        raise LookupError("ui gone")

    with pytest.raises(LookupError, match="ui gone"):
        gi.run_sweep(net, fault="F", points=_points(), progress=broken)
    assert _state(net) == before


def test_sweep_leaves_the_result_and_the_assembled_system_of_its_fault_alone():
    """"Left exactly as found, whatever happens" -- including the result the
    sweep's own fault had before, and the assembled system ``bus_response``
    reads. The last point's result used to be left in ``results["F"]``, next
    to bus impedances already restored to their original values."""
    net = _ring()
    gi.run_fault(net, "F")
    before = _state(net)
    result_before = net.results["F"]
    assembled_before = net.electrical_network

    gi.run_sweep(net, fault="F", points=_points())

    assert net.results["F"] is result_before
    assert net.electrical_network is assembled_before
    assert net.electrical_network.matches_network_state()
    assert _state(net) == before


# --------------------------------------------------------------------------
# run_outage_study
# --------------------------------------------------------------------------


def _scenarios():
    return [
        gi.Outage(name="L1_open", disabled_branches=["L1"]),
        gi.Outage(name="L3_open", disabled_branches=["L3"]),
    ]


def test_outage_study_reports_base_and_every_scenario(run_fault_calls):
    recorder = _Recorder()
    study = gi.run_outage_study(_ring(), fault="F", scenarios=_scenarios(),
                                progress=recorder)
    recorder.assert_well_formed()
    assert recorder.calls == [(1, 3, "base"), (2, 3, "L1_open"), (3, 3, "L3_open")]
    assert run_fault_calls["n"] == 3 == len(study.labels())


def test_outage_study_cancel_restores_the_network(run_fault_calls):
    net = _solved_ring()
    before = _state(net)
    recorder = _Recorder()
    with pytest.raises(gi.StudyCancelled):
        gi.run_outage_study(net, fault="F", scenarios=_scenarios(),
                            progress=recorder,
                            should_cancel=recorder.cancel_after(2))
    assert recorder.done == [1, 2] and run_fault_calls["n"] == 2
    assert _state(net) == before
    assert all(branch.active for branch in net.branches.values())


def test_outage_study_results_do_not_depend_on_the_hooks():
    plain = gi.run_outage_study(_ring(), fault="F", scenarios=_scenarios())
    hooked = gi.run_outage_study(_ring(), fault="F", scenarios=_scenarios(),
                                 progress=_Recorder(),
                                 should_cancel=lambda: False)
    assert plain.compare_buses().equals(hooked.compare_buses())


# --------------------------------------------------------------------------
# find_max_rho_scaling
# --------------------------------------------------------------------------


def _limit(net):
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        epr = {b.name: b.uepr for b in gi.solve(net, "F").buses}["R2"]
    return 1.5 * epr


def test_rho_search_reports_one_call_per_solve(run_fault_calls):
    net = _ring()
    limit = _limit(net)
    run_fault_calls["n"] = 0
    recorder = _Recorder()
    result = gi.find_max_rho_scaling(net, "F", ["R1", "R2", "R3"], limit,
                                     progress=recorder)
    recorder.assert_well_formed()
    assert len(recorder.calls) == run_fault_calls["n"]
    assert result["status"] == "converged"
    # A monotone search's evaluation count is fixed by its bracket and
    # tolerance, so the plan announced up front is exact.
    assert recorder.calls[-1][0] == recorder.calls[-1][1]
    assert {call[1] for call in recorder.calls} == {recorder.calls[-1][1]}


def test_rho_search_cancel_restores_the_network(run_fault_calls):
    net = _solved_ring()
    limit = _limit(net)
    before = _state(net)
    run_fault_calls["n"] = 0
    recorder = _Recorder()
    with pytest.raises(gi.StudyCancelled):
        gi.find_max_rho_scaling(net, "F", ["R1", "R2", "R3"], limit,
                                progress=recorder,
                                should_cancel=recorder.cancel_after(4))
    assert recorder.done == [1, 2, 3, 4] and run_fault_calls["n"] == 4
    assert _state(net) == before


def test_rho_search_result_does_not_depend_on_the_hooks():
    net = _ring()
    limit = _limit(net)
    plain = gi.find_max_rho_scaling(net, "F", ["R1", "R2", "R3"], limit)
    hooked = gi.find_max_rho_scaling(net, "F", ["R1", "R2", "R3"], limit,
                                     progress=_Recorder(),
                                     should_cancel=lambda: False)
    assert plain == hooked


# --------------------------------------------------------------------------
# find_max_rho_f_scaling
# --------------------------------------------------------------------------

K_REF = (0.1, 0.0, 1e-4, 0.0, 0.0)


def test_rho_f_search_reports_one_call_per_bus_solve(run_fault_calls):
    net = _ring()
    recorder = _Recorder()
    result = gi.find_max_rho_f_scaling(net, ["R1", "R2"], 400.0, K_REF,
                                       max_iter=8, progress=recorder)
    recorder.assert_well_formed()
    assert len(recorder.calls) == run_fault_calls["n"]
    assert len(recorder.calls) == 2 * (2 + result["iterations"])
    assert any("R1" in label for *_, label in recorder.calls)


def test_rho_f_search_cancel_in_the_middle_of_a_bus_sweep_restores_the_network(
    run_fault_calls,
):
    net = _solved_ring()
    before = _state(net)
    recorder = _Recorder()
    with pytest.raises(gi.StudyCancelled):
        gi.find_max_rho_f_scaling(net, ["R1", "R2"], 400.0, K_REF,
                                  max_iter=8, progress=recorder,
                                  should_cancel=recorder.cancel_after(3))
    assert recorder.done == [1, 2, 3] and run_fault_calls["n"] == 3
    assert _state(net) == before


def test_rho_f_search_result_does_not_depend_on_the_hooks():
    plain = gi.find_max_rho_f_scaling(_ring(), ["R1", "R2"], 400.0, K_REF,
                                      max_iter=8)
    hooked = gi.find_max_rho_f_scaling(_ring(), ["R1", "R2"], 400.0, K_REF,
                                       max_iter=8, progress=_Recorder(),
                                       should_cancel=lambda: False)
    assert plain == hooked


# --------------------------------------------------------------------------
# TransientStudy.solve
# --------------------------------------------------------------------------


def _transient(net):
    study = gi.TransientStudy(net, "F")
    study.set_source_waveform(
        "SRC",
        gi.waveforms.sinusoidal_with_dc_offset(
            amplitude=1000.0, frequency_hz=50.0, t_on=0.01, t_off=None,
            dc_amplitude=300.0, dc_decay_tau=0.03,
        ),
    )
    study.set_observation(buses=["R2"], branches=["L1"])
    return study


def test_fft_transient_reports_one_call_per_solve_block():
    recorder = _Recorder()
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        result = _transient(_ring()).solve(t_end=0.25, dt=1e-4, progress=recorder)
    recorder.assert_well_formed()
    # Independent count: the bins of the real FFT of the returned trace.
    n_bins = len(result.time_s) // 2 + 1
    assert len(recorder.calls) == math.ceil(n_bins / gi.TransientStudy._SOLVE_BLOCK)
    assert len(recorder.calls) >= 2
    assert recorder.calls[-1][0] == recorder.calls[-1][1]


def test_fft_transient_progress_follows_the_batch_of_the_byte_budget():
    """``fft_block_bytes`` shrinks the batch to ``budget // (16 n**2)`` bins,
    and the progress report follows it: one call per batch."""
    recorder = _Recorder()
    budget = 16 * 4 * 4 * 100  # four active buses: 100 bins per batch
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        result = _transient(_ring()).solve(
            t_end=0.25, dt=1e-4, progress=recorder, fft_block_bytes=budget
        )
    recorder.assert_well_formed()
    n_bins = len(result.time_s) // 2 + 1
    assert len(recorder.calls) == math.ceil(n_bins / 100)
    assert recorder.calls[0][2] == f"bins 0-99 of {n_bins}"
    last_start = 100 * (len(recorder.calls) - 1)
    assert recorder.calls[-1][2] == f"bins {last_start}-{n_bins - 1} of {n_bins}"


def test_fft_transient_cancel_restores_the_network():
    net = _solved_ring()
    before = _state(net)
    recorder = _Recorder()
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        with pytest.raises(gi.StudyCancelled):
            _transient(net).solve(t_end=0.25, dt=1e-4, progress=recorder,
                                  should_cancel=recorder.cancel_after(1))
    assert recorder.done == [1]
    assert _state(net) == before


def test_state_space_transient_reports_assembly_and_integration():
    recorder = _Recorder()
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        _transient(_ring()).solve(t_end=0.05, dt=1e-4, solver="state_space",
                                  progress=recorder)
    recorder.assert_well_formed()
    assert [label for *_, label in recorder.calls] == ["assembled", "integrated"]


def test_state_space_transient_can_be_cancelled_before_integrating():
    net = _solved_ring()
    before = _state(net)
    recorder = _Recorder()
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        with pytest.raises(gi.StudyCancelled):
            _transient(net).solve(t_end=0.05, dt=1e-4, solver="state_space",
                                  progress=recorder,
                                  should_cancel=recorder.cancel_after(1))
    assert recorder.done == [1]
    assert _state(net) == before


@pytest.mark.parametrize("solver", ["fft", "state_space"])
def test_transient_result_does_not_depend_on_the_hooks(solver):
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        plain = _transient(_ring()).solve(t_end=0.05, dt=1e-4, solver=solver)
        hooked = _transient(_ring()).solve(t_end=0.05, dt=1e-4, solver=solver,
                                           progress=_Recorder(),
                                           should_cancel=lambda: False)
    assert np.array_equal(plain.epr_t["R2"], hooked.epr_t["R2"])
    assert np.array_equal(plain.i_branch_t["L1"], hooked.i_branch_t["L1"])


def test_state_space_solve_leaves_the_network_as_found_cancelled_or_not():
    """The state-space solver takes its phase split from the stationary
    phase-network solve and writes nothing into the network -- in particular
    no path set, which it used to rebuild. Cancelled after its first stage it
    raises; finished it returns; the network is as found either way."""
    net = gi.create_network("coupled", frequencies=FREQ)
    branch_type = gi.BranchType(
        name="coupled", grounding_conductor=True,
        self_impedance_formula="(0.1+0.2*j*f/50)*l",
        mutual_impedance_formula="(0.05+0.1*j*f/50)*l",
        R_self_formula="0.1*l", L_self_formula="0.2/(2*pi*50)*l",
        M_mutual_formula="0.1/(2*pi*50)*l",
    )
    for name in ("S", "A", "F"):
        gi.create_bus(name, _bus_type(), 100.0, network=net)
    gi.create_branch("SA", branch_type, "S", "A", 1.0, network=net)
    gi.create_branch("AF", branch_type, "A", "F", 1.0, network=net)
    gi.create_fault("F", "F", {50.0: 1.0, 250.0: 1.0}, network=net)
    gi.create_source("SRC", "S", {50.0: 800.0, 250.0: 100.0}, network=net)
    assert net.paths == {}
    before = _state(net)

    study = gi.TransientStudy(net, "F")
    study.set_source_waveform(
        "SRC", gi.waveforms.sinusoidal_with_dc_offset(1000.0, 50.0)
    )
    study.set_observation(buses=["F"])
    recorder = _Recorder()
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        with pytest.raises(gi.StudyCancelled):
            study.solve(t_end=0.05, dt=1e-4, solver="state_space",
                        progress=recorder, should_cancel=recorder.cancel_after(1))
    assert recorder.done == [1]
    assert _state(net) == before

    finished = _Recorder()
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        study.solve(t_end=0.05, dt=1e-4, solver="state_space", progress=finished)
    finished.assert_well_formed()
    assert [label for _, _, label in finished.calls] == ["assembled", "integrated"]
    assert _state(net) == before
    assert net.paths == {}

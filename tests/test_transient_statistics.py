# tests/test_transient_statistics.py
"""
Statistics of transient signals over a chosen window: min, max, peak, RMS.

Checked a second way: a sampled sine over whole periods has the RMS
``A / sqrt(2)`` exactly (the mean of ``sin**2`` over uniformly spaced samples
of whole periods is one half), and the statistics of a solved transient
equal NumPy's on the same samples.
"""

import math

import numpy as np
import pytest

import groundinsight as gi
from groundinsight.simulation import waveforms


def test_a_sine_over_whole_periods_has_the_rms_of_its_amplitude():
    t = np.arange(0, 0.04, 1e-4)  # two periods of 50 Hz, 200 samples each
    result = gi.ResultTransient(time_s=t.tolist(), epr_t={"B": (325.0 * np.sin(2 * math.pi * 50 * t)).tolist()},
                                fault="F")
    row = result.statistics().row(0, named=True)
    assert row["rms"] == pytest.approx(325.0 / math.sqrt(2), rel=1e-12)
    assert row["peak_abs"] == pytest.approx(325.0, rel=1e-3)
    assert row["n_samples"] == t.size


def _pulse():
    net = gi.create_network(name="pulse", frequencies=[50.0])
    grid = gi.BusType(name="grid", system_type="Substation", voltage_level=20.0,
                      impedance_formula="rho * 0.01", R_formula="rho * 0.01")
    line = gi.BranchType(name="line", grounding_conductor=True, self_impedance_formula="0.25 * l",
                         mutual_impedance_formula="0 * l", R_self_formula="0.25 * l")
    for name in ("B1", "B2"):
        gi.create_bus(name=name, type=grid, network=net)
    gi.create_branch(name="L1", type=line, from_bus="B1", to_bus="B2", length=1.0, network=net)
    gi.create_source(name="S1", bus="B1", values={50.0: 100.0}, network=net)
    gi.create_fault(name="F1", bus="B2", scalings={50.0: 1.0}, network=net)
    return net


def test_the_window_statistics_of_a_solved_transient_are_numpy_s():
    study = gi.TransientStudy(_pulse(), "F1")
    study.set_source_waveform("S1", waveforms.step(100.0, t_off=0.02))
    study.set_observation(buses=["B1", "B2"], branches=["L1"])
    result = study.solve(t_end=0.04, dt=0.001)
    stats = result.statistics(0.005, 0.03)
    t = np.asarray(result.time_s)
    inside = (t >= 0.005) & (t <= 0.03)
    for row in stats.iter_rows(named=True):
        series = {"epr": result.epr_t, "i_branch": result.i_branch_t, "source": result.source_t}[
            row["signal_kind"]][row["name"]]
        x = np.asarray(series)[inside]
        assert (row["min"], row["max"]) == (x.min(), x.max())
        assert row["peak_abs"] == np.abs(x).max()
        assert row["rms"] == pytest.approx(math.sqrt(np.mean(x * x)), rel=1e-12)
    assert set(stats["signal_kind"]) == {"epr", "i_branch", "source"}


def test_an_empty_or_reversed_window_is_refused():
    result = gi.ResultTransient(time_s=[0.0, 1.0], epr_t={"B": [1.0, 2.0]}, fault="F")
    with pytest.raises(ValueError, match="before it starts"):
        result.statistics(1.0, 0.5)
    with pytest.raises(ValueError, match="no sample"):
        result.statistics(2.0, 3.0)

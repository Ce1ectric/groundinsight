# tests/test_audit_pass18_fft_performance.py

"""
Audit pass 18 -- the FFT solver's hot path, and the guards that keep it honest.

Three measures from section 5 of the review, all of them pure speed:

* ``compute_impedance_array`` / ``check_passive_impedance_array`` as the raw
  entry points of the impedance calculator, so a transient does not build and
  discard half a million ``ComplexNumber`` instances and does not call the
  scalar ``is_short_circuit`` once per (element, bin).
* a stacked ``(K, n, n)`` assembly of ``Y`` with a batched ``numpy.linalg.solve``
  over blocks of bins, instead of a Python double loop per bin.
* ``ResultTransient.to_polars()`` built column-wise instead of from one
  dictionary per sample.

Speed is not what these tests check -- a timing assertion is a flake on shared
CI. They check the part that matters: that the fast path computes *exactly*
what the slow one did. Each test therefore recomputes the reference the naive
way, inside the test, and compares.

Measured on 25 buses and 20 000 samples (see the report): solve 9.18 s ->
0.34 s, ``to_polars`` 1.02 s -> 0.07 s, with a maximum absolute difference of
9e-13 V on the EPR traces.
"""

from __future__ import annotations

import warnings

import numpy as np
import polars as pl
import pytest

import groundinsight as gi
from groundinsight.models.core_models import BranchType, BusType
from groundinsight.simulation import waveforms
from groundinsight.simulation.transient import ResultTransient, TransientStudy
from groundinsight.utils.impedance_calculator import (
    check_passive_impedance,
    check_passive_impedance_array,
    compute_impedance,
    compute_impedance_array,
)


FREQS = [0.0, 50.0, 250.0, 1000.0]


# --- the raw impedance entry point -------------------------------------------


@pytest.mark.parametrize(
    "formula, params",
    [
        ("0.25 + I*2*pi*f*1.2e-3", {}),
        ("(0.05*rho + I*2*pi*f*1e-3) * l", {"rho": 100.0, "l": 2.5}),
        ("1/(I*2*pi*f*1e-6)", {}),          # capacitive: inf at DC
        ("I*2*pi*f*1e-3", {}),              # inductive: exact short at DC
        ("nan", {}),                        # the open-end sentinel
    ],
)
def test_compute_impedance_array_matches_the_dict_entry_point(formula, params):
    """Same numbers, bit for bit -- only the container differs."""
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        as_dict = compute_impedance(formula, FREQS, dict(params))
        as_array = compute_impedance_array(formula, FREQS, dict(params))
    expected = np.array(
        [complex(as_dict[f].real, as_dict[f].imag) for f in FREQS]
    )
    assert as_array.dtype == complex
    np.testing.assert_array_equal(as_array, expected)


def test_compute_impedance_array_still_rejects_nan():
    with pytest.raises(ValueError, match="evaluated to NaN"):
        compute_impedance_array("(f - f) / (f - f)", [50.0], {})


def test_passivity_array_check_accepts_what_the_dict_check_accepts():
    values = compute_impedance_array("0.25 + I*2*pi*f*1.2e-3", FREQS, {})
    # A short circuit at 0 Hz is legitimate physics and must stay accepted.
    values[0] = 0.0
    check_passive_impedance_array(values, np.array(FREQS), element="bus 'b'")


@pytest.mark.parametrize(
    "values",
    [
        [0.5 + 0j, 0.0 + 0j, 1.0 + 0j, 2.0 + 0j],       # zero above DC
        [0.5 + 0j, 1e-320 + 0j, 1.0 + 0j, 2.0 + 0j],    # not invertible
        [0.5 + 0j, -1.0 + 0j, 1.0 + 0j, 2.0 + 0j],      # negative real part
        [-0.5 + 0j, 1.0 + 0j, 1.0 + 0j, 2.0 + 0j],      # negative at DC
    ],
)
def test_passivity_array_check_raises_the_same_message(values):
    """The array check delegates its message to the dictionary check, so the
    two must be indistinguishable from the caller's side."""
    array = np.array(values, dtype=complex)
    kwargs = dict(
        element="bus 'b1' (grounding impedance, transient FFT grid)",
        formula_str="0.05*rho",
        params={"rho": 100.0},
    )
    with pytest.raises(ValueError) as from_array:
        check_passive_impedance_array(array, np.array(FREQS), **kwargs)
    with pytest.raises(ValueError) as from_dict:
        check_passive_impedance(
            {f: v for f, v in zip(FREQS, array, strict=True)}, **kwargs
        )
    assert str(from_array.value) == str(from_dict.value)


# --- the batched solve -------------------------------------------------------


def _chain_network(name: str, n_bus: int = 6):
    bus_type = BusType(
        name="bt",
        system_type="MV",
        voltage_level=20.0,
        impedance_formula="0.05*rho + I*2*pi*f*1e-3",
    )
    branch_type = BranchType(
        name="brt",
        grounding_conductor=True,
        self_impedance_formula="(0.25 + I*2*pi*f*1.2e-3)*l",
        mutual_impedance_formula="(0.0 + I*f*0)*l",
    )
    net = gi.create_network(name, frequencies=[50.0])
    for k in range(n_bus):
        gi.create_bus(
            name=f"b{k}", type=bus_type, specific_earth_resistance=100.0,
            network=net,
        )
    for k in range(n_bus - 1):
        gi.create_branch(
            name=f"L{k}", type=branch_type, from_bus=f"b{k}", to_bus=f"b{k+1}",
            length=1.0, network=net,
        )
    gi.create_source(
        name="src", bus="b0", values={50.0: 1000.0 + 0j}, network=net
    )
    gi.create_fault(
        name="F1", bus=f"b{n_bus-1}", scalings={50.0: 1.0}, network=net
    )
    return net


def _solved_study(net, *, block: int):
    study = TransientStudy(net, fault_name="F1")
    study.set_source_waveform(
        "src",
        waveforms.sinusoidal_with_dc_offset(
            amplitude=1000.0, frequency_hz=50.0, t_on=0.02, t_off=0.12,
            dc_amplitude=500.0, dc_decay_tau=0.02,
        ),
    )
    study.set_observation(
        buses=[b for b in net.buses], branches=[b for b in net.branches]
    )
    study._SOLVE_BLOCK = block
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        return study.solve(t_end=0.4, dt=1e-3, solver="fft")


def test_block_size_does_not_change_the_result():
    """One bin per block is the old per-bin path; 512 is the shipped one."""
    per_bin = _solved_study(_chain_network("net_block_1"), block=1)
    blocked = _solved_study(_chain_network("net_block_512"), block=512)
    for bus in per_bin.epr_t:
        np.testing.assert_allclose(
            blocked.epr_t[bus], per_bin.epr_t[bus], rtol=0, atol=1e-10
        )
    for branch in per_bin.i_branch_t:
        np.testing.assert_allclose(
            blocked.i_branch_t[branch],
            per_bin.i_branch_t[branch],
            rtol=0,
            atol=1e-10,
        )


def test_a_block_containing_a_singular_bin_falls_back_per_bin():
    """A singular bin must only cost its own bin, not the whole block.

    A purely capacitive bus is an open circuit at 0 Hz, so the whole chain
    floats there and Y(0) is singular. The remaining bins of the same block
    have to come out exactly as they do when every bin is solved on its own.
    """
    bus_type = BusType(
        name="C_bus",
        system_type="MV",
        voltage_level=20.0,
        impedance_formula="rho*0 + 1/(I*2*pi*f*1e-6)",  # inf at DC
    )
    branch_type = BranchType(
        name="brt",
        grounding_conductor=True,
        self_impedance_formula="(0.25 + I*2*pi*f*1.2e-3)*l",
        mutual_impedance_formula="(0.0 + I*f*0)*l",
    )
    net = gi.create_network("net_singular", frequencies=[50.0])
    for k in range(3):
        gi.create_bus(name=f"b{k}", type=bus_type, network=net)
    for k in range(2):
        gi.create_branch(
            name=f"L{k}", type=branch_type, from_bus=f"b{k}", to_bus=f"b{k+1}",
            length=1.0, network=net,
        )
    gi.create_source(
        name="src", bus="b0", values={50.0: 1000.0 + 0j}, network=net
    )
    gi.create_fault(name="F1", bus="b2", scalings={50.0: 1.0}, network=net)

    blocked = _solved_study(net, block=512)
    per_bin = _solved_study(net, block=1)
    for bus in per_bin.epr_t:
        np.testing.assert_allclose(
            blocked.epr_t[bus], per_bin.epr_t[bus], rtol=0, atol=1e-10
        )
    # And the trace is finite: the singular bin was skipped, not propagated.
    assert np.isfinite(np.asarray(blocked.epr_t["b2"])).all()


# --- to_polars ---------------------------------------------------------------


def _naive_to_polars(result: ResultTransient) -> pl.DataFrame:
    """The pre-0.6.0 row-wise construction, kept here as the reference."""
    rows = []
    for kind, table in (
        ("epr", result.epr_t),
        ("i_branch", result.i_branch_t),
        ("source", result.source_t),
    ):
        for name, series in table.items():
            for t, v in zip(result.time_s, series, strict=True):
                rows.append(
                    {"time_s": t, "signal_kind": kind, "name": name, "value": v}
                )
    return pl.DataFrame(rows)


def test_to_polars_matches_the_row_wise_construction():
    result = _solved_study(_chain_network("net_polars"), block=512)
    fast = result.to_polars()
    naive = _naive_to_polars(result)
    assert fast.columns == naive.columns
    assert fast.schema == naive.schema
    assert fast.equals(naive)


def test_to_polars_of_an_empty_result_keeps_the_schema():
    """An observation-free study must still return the pinned four columns."""
    empty = ResultTransient(time_s=[0.0, 1e-3], fault="F1")
    frame = empty.to_polars()
    assert frame.height == 0
    assert frame.columns == ["time_s", "signal_kind", "name", "value"]
    assert frame.schema["value"] == pl.Float64

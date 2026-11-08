# tests/test_audit_pass19_results_columnwise.py
"""
Audit pass 19 -- result frames built column-wise (P11) and cheaper result
objects (P5).

``Network.res_buses`` and ``res_branches`` built one Python dict per row and
took magnitude and angle of every phasor one NumPy scalar at a time -- on a
4000-bus ring at seven frequencies the frame took half as long as the solve
that produced it. They now collect the phasors once and compute magnitude
and angle on whole columns.

The frames must not change by a bit: same rows in the same order, same
schema, same values, ``null`` where an entry is missing. The second way here
is the former row-wise construction, kept in this file as the reference,
compared with ``check_exact=True`` -- on solved networks and on hand-built
results with the irregularities the row-wise code tolerated (missing
entries, plain ``complex`` values, zeros, NaN). Magnitudes come from
``numpy.hypot``, which agrees bit for bit with Python's ``abs`` of a complex
number; ``numpy.abs`` of a complex array does not (its vectorised loop
differs in the last bit for about a third of all values), which the last
test pins so that nobody "simplifies" the columns into it.
"""

from __future__ import annotations

import time

import numpy as np
import polars as pl
import pytest
from polars.testing import assert_frame_equal

import groundinsight as gi
from groundinsight.models.core_models import (
    RES_BRANCHES_SCHEMA,
    RES_BUSES_SCHEMA,
    ComplexNumber,
    Result,
    ResultBranch,
    ResultBus,
    _magnitude_angle,
)
from tests import test_audit_pass19_performance as perf
from tests._warning_policy import acknowledge

#: The column-wise frames are compared with the row-wise construction on
#: networks that produce every kind of row: rings, 0 Hz bins, Thevenin
#: sources, spread electrode currents.
pytestmark = acknowledge(
    "DC_LIMIT", "EARTH_GROUP_SPREAD", "PHASE_PROXY", "THEVENIN_COUPLING"
)


# --------------------------------------------------------------------------
# the reference: the row-wise construction up to pass 19
# --------------------------------------------------------------------------


def _row_wise_buses(result: Result, fault: str) -> pl.DataFrame:
    data = []
    for result_bus in result.buses:
        for freq, voltage in result_bus.uepr_freq.items():
            current = result_bus.ia_freq.get(freq)
            injection = result_bus.i_inj_freq.get(freq)
            data.append({
                "bus_name": result_bus.name, "fault": fault,
                "frequency_Hz": float(freq), "is_rms": False,
                **_magnitude_angle(voltage, "EPR_V", "EPR_degree"),
                **_magnitude_angle(current, "I_bus_A", "I_bus_degree"),
                **_magnitude_angle(injection, "I_inj_A", "I_inj_degree"),
            })
        data.append({
            "bus_name": result_bus.name, "fault": fault, "frequency_Hz": None,
            "is_rms": True, "EPR_V": result_bus.uepr, "EPR_degree": None,
            "I_bus_A": result_bus.ia, "I_bus_degree": None,
            "I_inj_A": result_bus.i_inj, "I_inj_degree": None,
        })
    return pl.DataFrame(data, schema=RES_BUSES_SCHEMA)


def _row_wise_branches(result: Result, fault: str) -> pl.DataFrame:
    data = []
    for result_branch in result.branches:
        for freq, current in result_branch.i_s_freq.items():
            if current:
                data.append({
                    "branch_name": result_branch.name, "fault": fault,
                    "frequency_Hz": float(freq), "is_rms": False,
                    **_magnitude_angle(current, "I_branch_A", "I_branch_degree"),
                })
        data.append({
            "branch_name": result_branch.name, "fault": fault,
            "frequency_Hz": None, "is_rms": True,
            "I_branch_A": result_branch.i_s, "I_branch_degree": None,
        })
    return pl.DataFrame(data, schema=RES_BRANCHES_SCHEMA)


def _assert_both_frames(net: gi.Network, fault: str = "F") -> None:
    result = net.results[fault]
    assert_frame_equal(net.res_buses(fault=fault), _row_wise_buses(result, fault),
                       check_exact=True)
    assert_frame_equal(net.res_branches(fault=fault),
                       _row_wise_branches(result, fault), check_exact=True)


# --------------------------------------------------------------------------
# solved networks
# --------------------------------------------------------------------------

#: A random network of the equivalence battery that has a solution (seeds
#: with a floating group are refused by the solve).
RANDOM_SEED = 14


@pytest.mark.parametrize(
    "builder",
    [
        lambda: perf.ring(50, F=7, n_sources=2),
        lambda: perf.mesh(6, F=3, n_sources=4),
        lambda: perf.thevenin_ring(30, F=3),
        lambda: perf.dc_ring(20),
        lambda: perf.random_network(RANDOM_SEED),
    ],
    ids=["ring", "mesh", "thevenin", "dc", "random"],
)
def test_res_frames_equal_the_row_wise_construction(builder):
    net = builder()
    gi.run_fault(net, "F")
    _assert_both_frames(net)


# --------------------------------------------------------------------------
# hand-built results: what the row-wise code tolerated
# --------------------------------------------------------------------------


def _cn(value: complex) -> ComplexNumber:
    return ComplexNumber(real=value.real, imag=value.imag)


def _irregular_result() -> Result:
    """Missing entries, plain complex values, zeros, NaN, signed zeros and a
    bus with fewer frequencies than another."""
    buses = [
        ResultBus(
            name="a",
            uepr=10.0, ia=2.0, i_inj=0.0,
            uepr_freq={50.0: _cn(3 + 4j), 150.0: _cn(-1e-12 + 0j), 250.0: _cn(0j)},
            ia_freq={50.0: _cn(1 - 1j), 250.0: _cn(complex(-0.0, -0.0))},
            i_inj_freq={},
        ),
        ResultBus(
            name="b",
            uepr=float("nan"), ia=0.0, i_inj=1.5,
            uepr_freq={250.0: _cn(complex(float("nan"), 1.0)), 50.0: _cn(-7 + 0j)},
            ia_freq={50.0: _cn(complex(float("inf"), 2.0)), 250.0: _cn(2j)},
            i_inj_freq={50.0: _cn(1e300 + 1e300j), 250.0: _cn(-3e-320 + 0j)},
        ),
    ]
    branches = [
        ResultBranch(name="x", i_s=4.0,
                     i_s_freq={50.0: _cn(1 + 2j), 150.0: _cn(0j), 250.0: _cn(-2 - 0j)}),
        ResultBranch(name="y", i_s=0.0, i_s_freq={}),
    ]
    return Result(buses=buses, branches=branches, fault="F")


def test_res_frames_of_an_irregular_result_equal_the_row_wise_construction():
    net = perf.ring(6, F=3)
    net.results["F"] = _irregular_result()
    _assert_both_frames(net)


def test_plain_complex_values_and_falsy_currents_are_handled_as_before():
    """A hand-built branch mapping with plain ``complex`` values: a zero
    current is falsy and has never produced a row; ``None`` neither."""
    net = perf.ring(6, F=3)
    result = _irregular_result()
    result.branches[0].i_s_freq = {50.0: 1 + 1j, 150.0: 0j, 250.0: None}
    result.buses[0].uepr_freq = {50.0: 3 + 4j, 150.0: complex(-0.0, 0.0)}
    net.results["F"] = result
    _assert_both_frames(net)


def test_an_empty_result_gives_empty_frames_with_the_schema():
    net = perf.ring(6, F=3)
    net.results["F"] = Result(buses=[], branches=[], fault="F")
    _assert_both_frames(net)
    assert net.res_buses(fault="F").schema == pl.Schema(RES_BUSES_SCHEMA)


def test_numpy_abs_of_a_complex_array_is_not_python_abs():
    """Why the columns use ``hypot``: on this platform the vectorised complex
    absolute differs from Python's ``abs`` in the last bit for a large share
    of values, ``hypot`` does not. If NumPy ever changes either, this test
    says so rather than the frame tests above."""
    rng = np.random.default_rng(7)
    re = rng.normal(size=20000) * 10.0 ** rng.integers(-6, 6, size=20000)
    im = rng.normal(size=20000) * 10.0 ** rng.integers(-6, 6, size=20000)
    python = np.array([abs(complex(a, b)) for a, b in zip(re.tolist(), im.tolist(), strict=True)])
    assert np.array_equal(np.hypot(re, im), python)


# --------------------------------------------------------------------------
# size
# --------------------------------------------------------------------------


@pytest.mark.slow
def test_res_buses_of_a_4000_bus_ring_takes_milliseconds():
    """P11: 32 000 rows. Measured interleaved in one process: 0.16 s row-wise
    (more than a quarter of the solve that produced it), 0.03 s column-wise.

    The bound is relative to the solve on the same machine, because CI
    runners are two to four times slower than a workstation and an absolute
    bound measures the runner, not the code."""
    net = perf.ring(4000, F=7)
    start = time.perf_counter()
    gi.run_fault(net, "F")
    solve = time.perf_counter() - start
    start = time.perf_counter()
    frame = net.res_buses(fault="F")
    seconds = time.perf_counter() - start
    assert frame.height == 4000 * 8
    assert seconds < 0.2 * solve, f"{seconds:.3f} s against a {solve:.3f} s solve"

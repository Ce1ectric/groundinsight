"""
Audit pass 18 -- the cut decomposition against the system that was solved.

``analyze_cuts`` assembled its own nodal matrix from the stored impedances
instead of reusing the solver's. The two agree on a plain 50 Hz current-source
network and part company everywhere else:

- **A3** -- at 0 Hz the solver substitutes a small finite value for an element
  that is an ideal short there; the decomposition read the raw zero and treated
  the short as an **open circuit**. On the chain below that is a driving-point
  impedance of 10 Ohm where the solver has 2.5, with every ``Z_side`` infinite
  and ``identity_residual`` reporting ``0.0``.
- **A4** -- the Thevenin loop closure ``Y_src`` between a voltage source's bus
  and the fault bus exists only in the solver's matrix. It carries current out
  of the fault location, so leaving it out cost the current balance up to 23 %
  of the fault current.
- **A13** -- ``identity_residual`` was structurally zero: both sides of the
  comparison came from the same locally assembled matrix, so it followed from
  the KCL of that solve and could not disagree with anything. It is a real
  cross-check once the reference is the solved system.
- **A11** -- an incomplete stored result surfaced as a bare ``KeyError``.
- **A14** -- ``to_polars()`` inferred ``Null`` dtypes for the current columns
  when no result was available, so the frame refused to stack with a solved one.
"""

from __future__ import annotations

import numpy as np
import polars as pl
import pytest

import groundinsight as gi
from groundinsight.analysis.decomposition import SOURCE_LOOP_SIDE
from groundinsight.models.core_models import (
    BranchType,
    BusType,
    ComplexNumber,
    Source,
)


CHAIN = [("SA", "S", "A"), ("AF", "A", "F"), ("FR1", "F", "R1")]
BUSES = ["S", "A", "F", "R1"]


def _cuts():
    return [
        gi.Cut(name="left", branches=["AF"]),
        gi.Cut(name="right", branches=["FR1"]),
    ]


def _network(
    *,
    frequencies,
    self_formula="(0.1+0.2*j)*l",
    thevenin=None,
    solve=True,
):
    bus_type = BusType(
        name="bt", system_type="MV", voltage_level=20.0, impedance_formula="10"
    )
    branch_type = BranchType(
        name="brt", grounding_conductor=True,
        self_impedance_formula=self_formula,
        mutual_impedance_formula="(0.05+0.1*j)*l",
    )
    net = gi.create_network("net", frequencies=frequencies)
    for bus in BUSES:
        net.add_bus(gi.create_bus(bus, bus_type, 100.0))
    for name, from_bus, to_bus in CHAIN:
        net.add_branch(
            gi.create_branch(name, branch_type, from_bus, to_bus, 1.0, 100.0)
        )
    gi.create_fault(
        "F", "F", {f: 1.0 for f in frequencies}, active=True, network=net
    )
    if thevenin is None:
        gi.create_source(
            "SRC", "S", {f: 1000.0 for f in frequencies}, network=net
        )
    else:
        net.add_source(
            Source(
                name="SRC", bus="S", source_type="voltage",
                voltage={
                    f: ComplexNumber(real=10000.0, imag=0.0)
                    for f in frequencies
                },
                source_impedance={
                    f: ComplexNumber(
                        real=complex(thevenin).real, imag=complex(thevenin).imag
                    )
                    for f in frequencies
                },
            )
        )
    if solve:
        gi.run_fault(net, "F")
    return net


def _solver_driving_point(net, bus_name, freq):
    """Read the driving-point impedance off the *solved* system, so the check
    is against what was actually solved rather than a second assembly."""
    electrical = net.electrical_network
    index = electrical.bus_indices
    injection = np.zeros(len(index), dtype=complex)
    injection[index[bus_name]] = 1.0
    solved = np.linalg.solve(electrical.Y_matrices[freq], injection)
    return complex(solved[index[bus_name]])


# --- A3: 0 Hz ------------------------------------------------------------------


def test_a_dc_short_is_not_read_as_an_open_circuit():
    """
    ``(2*pi*f*L*j)*l`` is an ideal short at 0 Hz. The solver substitutes a small
    finite value there; reading the stored zero instead made every direction
    infinite and the fault bus's own electrode the whole answer.
    """
    net = _network(
        frequencies=[0.0, 50.0], self_formula="(2*pi*f*0.001*j)*l"
    )
    analysis = gi.analyze_cuts(net, fault="F", cuts=_cuts())

    expected = _solver_driving_point(net, "F", 0.0)
    assert analysis.z_driving_point[0.0] == pytest.approx(expected, rel=1e-9)
    # The bus's own electrode is 10 Ohm; a driving point *below* it is what a
    # parallel network gives and an open one cannot.
    assert abs(analysis.z_driving_point[0.0]) < 9.0
    for name in analysis.z_side:
        assert np.isfinite(analysis.z_side[name][0.0])
    assert analysis.identity_residual[0.0] < 1e-6


def test_the_50_hz_bin_of_the_same_network_is_unchanged():
    """The 0 Hz fix must not move any other frequency."""
    net = _network(
        frequencies=[0.0, 50.0], self_formula="(2*pi*f*0.001*j)*l"
    )
    analysis = gi.analyze_cuts(net, fault="F", cuts=_cuts())
    assert analysis.z_driving_point[50.0] == pytest.approx(
        _solver_driving_point(net, "F", 50.0), rel=1e-12
    )


# --- A4: the Thevenin loop closure --------------------------------------------


@pytest.mark.parametrize(
    "z_src", [complex(2.0, 8.0), complex(0.5, 2.0), complex(0.2, 0.5)]
)
def test_the_thevenin_loop_closure_is_a_direction_of_its_own(z_src):
    """
    ``Y_src`` carries current out of the fault bus. Counting it closes the
    current balance, which was short by 2.5 % to 23 % of the fault current.
    """
    net = _network(frequencies=[50.0], thevenin=z_src)
    analysis = gi.analyze_cuts(net, fault="F", cuts=_cuts())
    assert SOURCE_LOOP_SIDE in analysis.z_side
    assert analysis.kcl_residual[50.0] < 1e-9
    assert analysis.z_driving_point[50.0] == pytest.approx(
        _solver_driving_point(net, "F", 50.0), rel=1e-9
    )


def test_dropping_the_loop_closure_would_break_the_identity():
    """
    A13 in one assertion: the residual is meaningful now. Recomputing the
    parallel sum *without* the loop closure -- which is exactly what 0.6.0
    reported -- misses the driving point by percent, where the old residual
    stayed at 1e-16 and said nothing.
    """
    net = _network(frequencies=[50.0], thevenin=complex(0.2, 0.5))
    analysis = gi.analyze_cuts(net, fault="F", cuts=_cuts())

    without = 1.0 / analysis.z_local[50.0] + sum(
        1.0 / analysis.z_side[name][50.0]
        for name in analysis.z_side
        if name != SOURCE_LOOP_SIDE
    )
    z_dp = analysis.z_driving_point[50.0]
    drift = abs(1.0 / without - z_dp) / abs(z_dp)
    assert drift > 0.02
    assert analysis.identity_residual[50.0] < 1e-9


def test_a_current_source_network_has_no_loop_side():
    """The direction appears only where the solver actually adds one."""
    net = _network(frequencies=[50.0])
    analysis = gi.analyze_cuts(net, fault="F", cuts=_cuts())
    assert SOURCE_LOOP_SIDE not in analysis.z_side
    assert set(analysis.z_side) == {"left", "right"}


def test_the_loop_side_name_is_reserved():
    net = _network(frequencies=[50.0])
    with pytest.raises(ValueError, match="reserved"):
        gi.analyze_cuts(
            net,
            fault="F",
            cuts=[gi.Cut(name=SOURCE_LOOP_SIDE, branches=["AF"])],
        )


# --- A11 / A14: the output surface --------------------------------------------


def test_an_incomplete_result_is_explained_instead_of_raising_keyerror():
    net = _network(frequencies=[50.0])
    result = net.results["F"]
    result.buses = [b for b in result.buses if b.name != "R1"]
    with pytest.raises(ValueError, match="Incomplete results"):
        gi.analyze_cuts(net, fault="F", cuts=_cuts())


def test_the_frame_of_an_unsolved_analysis_stacks_with_a_solved_one():
    """A14: without a result the nine current columns are all null; an
    inferred schema makes them ``Null`` and ``pl.concat`` then fails."""
    unsolved = gi.analyze_cuts(
        _network(frequencies=[50.0], solve=False), fault="F", cuts=_cuts()
    ).to_polars()
    solved = gi.analyze_cuts(
        _network(frequencies=[50.0]), fault="F", cuts=_cuts()
    ).to_polars()
    assert unsolved.schema["I_shield_A"] == pl.Float64
    assert unsolved.schema["r_side"] == pl.Float64
    assert pl.concat([unsolved, solved]).height == unsolved.height + solved.height


# --- the ring stays exact ------------------------------------------------------


def test_the_ring_case_is_unchanged_and_exact():
    """The reuse of the solver's matrix must not disturb the case the module
    was built for."""
    net = _network(frequencies=[50.0])
    net.add_branch(
        gi.create_branch(
            "R1S", net.branches["AF"].type, "R1", "S", 1.0, 100.0
        )
    )
    gi.run_fault(net, "F")
    analysis = gi.analyze_cuts(net, fault="F", cuts=_cuts())
    assert analysis.identity_residual[50.0] < 1e-12
    assert analysis.sides_are_disjoint is False
    assert analysis.z_driving_point[50.0] == pytest.approx(
        _solver_driving_point(net, "F", 50.0), rel=1e-12
    )

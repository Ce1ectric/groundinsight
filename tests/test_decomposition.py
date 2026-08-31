"""
Splitting the network at the fault location, and the current-based reduction
factor that goes with it.

Two claims carry this module and both are checked numerically rather than
argued:

1. The parallel decomposition closes. ``Z_local`` in parallel with every
   ``Z_side`` reproduces the driving-point impedance of the whole network at the
   fault bus, to machine precision, in a radial network *and* in a ring.
2. The current-based reduction factor responds to the fault-bus characteristic
   while the EPR-based one does not. That is the whole reason both are kept: a
   rho-f sensitivity study plotted against ``value`` would be a flat line.
"""

from __future__ import annotations

import logging

import numpy as np
import polars as pl
import pytest

import groundinsight as gi
from groundinsight.models.core_models import BusType, BranchType, ComplexNumber


FREQ = [50.0]

# S -- A -- F -- R1 -- R2, source at S, fault at F. Two directions leave the
# fault bus: "left" towards the source, "right" into a passive spur.
CHAIN = [("SA", "S", "A"), ("AF", "A", "F"), ("FR1", "F", "R1"), ("R1R2", "R1", "R2")]
BUSES = ["S", "A", "F", "R1", "R2"]
RING_CLOSER = ("R2S", "R2", "S")


def _network(*, ring=False, z_fault=None, solve=True):
    bus_type = BusType(
        name="bt", system_type="MV", voltage_level=20.0, impedance_formula="10"
    )
    branch_type = BranchType(
        name="brt",
        grounding_conductor=True,
        self_impedance_formula="(0.1+0.2*j)*l",
        mutual_impedance_formula="(0.05+0.1*j)*l",
    )
    net = gi.create_network("net", frequencies=FREQ)
    for bus in BUSES:
        net.add_bus(gi.create_bus(bus, bus_type, 100.0))
    branches = list(CHAIN) + ([RING_CLOSER] if ring else [])
    for name, from_bus, to_bus in branches:
        net.add_branch(
            gi.create_branch(name, branch_type, from_bus, to_bus, 1.0, 100.0)
        )
    gi.create_fault("F", "F", {50.0: 1.0}, active=True, network=net)
    gi.create_source("SRC", "S", {50.0: 1000.0}, network=net)
    if z_fault is not None:
        net.buses["F"].impedance = {
            50.0: ComplexNumber(real=z_fault, imag=0.0)
        }
    if solve:
        gi.run_fault(net, "F")
    return net


def _cuts():
    return [
        gi.Cut(name="left", branches=["AF"], description="towards the source"),
        gi.Cut(name="right", branches=["FR1"], description="passive spur"),
    ]


def _independent_driving_point(net, bus_name, freq=50.0):
    """Assemble the nodal system by hand and inject 1 A -- an independent check
    that does not share code with the module under test."""
    names = sorted(net.buses)
    index = {n: i for i, n in enumerate(names)}
    Y = np.zeros((len(names), len(names)), dtype=complex)
    for n in names:
        Y[index[n], index[n]] += 1.0 / complex(net.buses[n].impedance[freq])
    for branch in net.branches.values():
        y = 1.0 / complex(branch.self_impedance[freq])
        i, j = index[branch.from_bus], index[branch.to_bus]
        Y[i, i] += y
        Y[j, j] += y
        Y[i, j] -= y
        Y[j, i] -= y
    injection = np.zeros(len(names), dtype=complex)
    injection[index[bus_name]] = 1.0
    return complex(np.linalg.solve(Y, injection)[index[bus_name]])


# --- the parallel identity ---------------------------------------------------


@pytest.mark.parametrize("ring", [False, True], ids=["chain", "ring"])
def test_the_parallel_decomposition_closes(ring):
    """``1/Z_local + sum(1/Z_side)`` is the driving-point admittance, exactly."""
    net = _network(ring=ring)
    analysis = gi.analyze_cuts(net, fault="F", cuts=_cuts())
    assert analysis.identity_residual[50.0] < 1e-12
    admittance = 1.0 / analysis.z_local[50.0] + sum(
        1.0 / analysis.z_side[name][50.0] for name in analysis.z_side
    )
    assert complex(1.0 / admittance) == pytest.approx(
        analysis.z_driving_point[50.0], rel=1e-12
    )


@pytest.mark.parametrize("ring", [False, True], ids=["chain", "ring"])
def test_the_driving_point_matches_a_hand_assembled_system(ring):
    """The module's own value is checked against a system assembled from
    scratch, so a shared mistake cannot hide inside the identity above."""
    net = _network(ring=ring)
    analysis = gi.analyze_cuts(net, fault="F", cuts=_cuts())
    assert analysis.z_driving_point[50.0] == pytest.approx(
        _independent_driving_point(net, "F"), rel=1e-12
    )


def test_a_ring_reports_overlapping_directions_instead_of_empty_ones():
    """
    Removing one branch of a ring separates nothing.

    An isolate-the-sub-network formulation would report an empty far side and an
    infinite impedance here. Current division gives a finite impedance for both
    directions and flags the overlap instead.
    """
    net = _network(ring=True)
    analysis = gi.analyze_cuts(net, fault="F", cuts=_cuts())
    assert analysis.sides_are_disjoint is False
    assert set(analysis.sides["left"]) == set(analysis.sides["right"])
    for name in ("left", "right"):
        assert np.isfinite(analysis.z_side[name][50.0])
    # Far-side quantities are not defined without a far side of one's own.
    assert analysis.r_side["left"][50.0] is None
    # ... but the metallic share out of the fault bus still is.
    assert analysis.current_share["left"][50.0] > 0.0


def test_a_chain_reports_disjoint_directions():
    net = _network(ring=False)
    analysis = gi.analyze_cuts(net, fault="F", cuts=_cuts())
    assert analysis.sides_are_disjoint is True
    assert analysis.sides["left"] == ["A", "S"]
    assert analysis.sides["right"] == ["R1", "R2"]
    assert analysis.r_side["left"][50.0] is not None


def test_the_current_split_accounts_for_the_whole_fault_current():
    """KCL at the fault bus: injection = local electrode + everything leaving
    metallically. Checked as a residual, not assumed."""
    net = _network()
    analysis = gi.analyze_cuts(net, fault="F", cuts=_cuts())
    assert analysis.kcl_residual[50.0] < 1e-12
    leaving = sum(analysis.i_shield[name][50.0] for name in analysis.i_shield)
    assert complex(analysis.i_fault[50.0]) == pytest.approx(
        analysis.i_local[50.0] + leaving, rel=1e-12
    )


def test_a_passive_spur_has_no_reduction_factor_but_has_an_impedance():
    """Nothing has to cross a cut whose far side carries no source, so ``r``
    would be a division by zero. The impedance is the meaningful quantity."""
    net = _network()
    analysis = gi.analyze_cuts(net, fault="F", cuts=_cuts())
    assert analysis.r_side["right"][50.0] is None
    assert abs(analysis.i_total["right"][50.0]) == pytest.approx(0.0, abs=1e-9)
    assert np.isfinite(analysis.z_side["right"][50.0])
    assert abs(analysis.z_side["right"][50.0]) > 0.0


# --- the sensitivity the whole feature exists for ----------------------------


def test_r_epr_is_blind_to_the_fault_bus_while_r_current_is_not():
    """
    The reason both definitions are kept.

    Over four decades of fault-bus impedance the EPR-based factor does not move
    at all -- it is a rank-1 update that cancels in its own quotient -- while the
    potential rise moves by a factor of fifty.
    """
    r_epr, r_current, eprs = [], [], []
    for z_fault in (0.05, 0.5, 5.0, 50.0):
        net = _network(z_fault=z_fault)
        factor = net.results["F"].reduction_factor
        r_epr.append(factor.value[50.0])
        r_current.append(factor.value_current[50.0])
        eprs.append(next(b.uepr for b in net.results["F"].buses if b.name == "F"))

    assert r_epr == pytest.approx([0.5] * 4, rel=1e-9)
    assert eprs[-1] / eprs[0] > 40.0
    # Strictly decreasing: a better electrode at the faulted station takes a
    # larger share of the current straight into the local soil, so more of the
    # fault current returns through earth.
    assert all(a > b for a, b in zip(r_current, r_current[1:]))
    assert r_current[0] / r_current[-1] > 1.5
    # I_E is a sum over every bus that feeds the soil, so it stays finite even
    # when the faulted station has no electrode at all -- the neighbours carry
    # it. A definition built on the fault bus alone would collapse here, and
    # that is exactly the error this range guards against.
    assert min(r_current) > 0.005


def test_the_side_impedances_are_a_property_of_the_network_alone():
    """Source-free by construction, so varying the fault-bus characteristic
    leaves them untouched -- which is what makes them a clean baseline for the
    sweep."""
    reference = None
    for z_fault in (0.05, 5.0, 500.0):
        net = _network(z_fault=z_fault)
        analysis = gi.analyze_cuts(net, fault="F", cuts=_cuts())
        values = [analysis.z_side[n][50.0] for n in ("left", "right")]
        if reference is None:
            reference = values
        else:
            for got, expected in zip(values, reference):
                assert got == pytest.approx(expected, rel=1e-12)


def test_the_side_reduction_factor_responds_to_the_fault_bus():
    net_low = _network(z_fault=0.05)
    net_high = _network(z_fault=500.0)
    r_low = gi.analyze_cuts(net_low, fault="F", cuts=_cuts()).r_side["left"][50.0]
    r_high = gi.analyze_cuts(net_high, fault="F", cuts=_cuts()).r_side["left"][50.0]
    assert r_low > r_high


# --- contract ----------------------------------------------------------------


def test_a_branch_away_from_the_fault_bus_is_rejected_with_the_reason():
    net = _network()
    with pytest.raises(ValueError, match="not an active grounding branch"):
        gi.analyze_cuts(net, fault="F", cuts=[gi.Cut(name="far", branches=["SA"])])


def test_two_cuts_may_not_claim_the_same_branch():
    net = _network()
    with pytest.raises(ValueError, match="claimed by both"):
        gi.analyze_cuts(
            net,
            fault="F",
            cuts=[
                gi.Cut(name="a", branches=["AF"]),
                gi.Cut(name="b", branches=["AF"]),
            ],
        )


def test_a_branch_listed_twice_in_one_cut_is_rejected():
    with pytest.raises(ValueError, match="more than once"):
        gi.Cut(name="a", branches=["AF", "AF"])


def test_the_reserved_name_is_refused():
    net = _network()
    with pytest.raises(ValueError, match="reserved"):
        gi.analyze_cuts(net, fault="F", cuts=[gi.Cut(name="rest", branches=["AF"])])


def test_an_unclaimed_branch_becomes_the_implicit_side():
    """The decomposition covers the whole network whether or not the user named
    every direction -- otherwise the identity would silently stop closing."""
    net = _network()
    analysis = gi.analyze_cuts(
        net, fault="F", cuts=[gi.Cut(name="left", branches=["AF"])]
    )
    assert set(analysis.branches) == {"left", "rest"}
    assert analysis.branches["rest"] == ["FR1"]
    assert analysis.identity_residual[50.0] < 1e-12


def test_an_unknown_fault_is_named():
    net = _network()
    with pytest.raises(ValueError, match="does not exist"):
        gi.analyze_cuts(net, fault="nope", cuts=_cuts())


# --- output surface ----------------------------------------------------------


def test_the_impedances_do_not_need_a_solved_result():
    """Half the analysis is a network property, so it must not require a
    ``run_fault`` the user has not asked for."""
    net = _network(solve=False)
    analysis = gi.analyze_cuts(net, fault="F", cuts=_cuts())
    assert analysis.has_currents is False
    assert analysis.identity_residual[50.0] < 1e-12
    frame = analysis.to_polars()
    assert frame["Z_side_Ohm"].null_count() == 0
    assert frame["I_shield_A"].null_count() == len(frame)


def test_the_frame_is_long_format_and_typed_for_grouping():
    net = _network()
    frame = gi.analyze_cuts(net, fault="F", cuts=_cuts()).to_polars()
    assert frame.height == 2 * len(FREQ)
    assert frame.schema["frequency_Hz"] == pl.Float64
    assert set(frame["cut"]) == {"left", "right"}
    assert frame.filter(pl.col("frequency_Hz") == 50.0).height == 2


def test_res_all_impedances_carries_both_reduction_factors():
    net = _network()
    frame = net.res_all_impedances()
    assert "reduction_factor" in frame.columns
    assert "reduction_factor_current" in frame.columns
    row = frame.filter(pl.col("frequency_Hz") == 50.0)
    assert row["reduction_factor"][0] == pytest.approx(0.5, rel=1e-9)
    assert 0.0 < row["reduction_factor_current"][0] < 1.0


def test_the_result_survives_a_json_round_trip():
    net = _network()
    restored = type(net).model_validate_json(net.model_dump_json())
    factor = restored.results["F"].reduction_factor
    assert factor.value[50.0] == pytest.approx(0.5, rel=1e-9)
    assert factor.value_current[50.0] == pytest.approx(
        net.results["F"].reduction_factor.value_current[50.0], rel=1e-12
    )


def test_the_overlap_notice_is_logged_once(caplog):
    net = _network(ring=True)
    with caplog.at_level(logging.INFO, logger="groundinsight"):
        gi.analyze_cuts(net, fault="F", cuts=_cuts())
    assert sum("overlap" in r.getMessage() for r in caplog.records) == 1

# tests/test_audit_pass18_bisection.py
"""
Audit pass 18 -- the monotonicity a bisection assumes (A6).

``find_max_rho_scaling`` bisects ``c`` against an EPR limit. A bisection finds
*the* crossing, so it presumes there is one: that ``EPR(c)`` rises with ``c``.
Scaling ``rho`` does not guarantee that. Only the terms of the impedance
formula that carry ``rho`` scale; a fixed reactance stays put, and

    |Y_net| = |G + 1/(R + jX)|

then has a maximum at ``R = (-1 + sqrt(1 + 4 G^2 X^2)) / (2G)`` -- so the EPR
has a *minimum* there, falls before it rises, and crosses any limit in that
band twice.

The consequence was not a slightly wrong answer but a wrong *category*: the
search reported "no scaling factor in the bracket satisfies the constraint"
for a limit that factors around the dip satisfy comfortably. The network below
is built to put the turning point inside the default bracket:

    bus S: 1 Ohm fixed          -- the conductance G
    bus F: 0.02*rho + 0.1*f*j   -- R scales with c, X = 5 Ohm stays
    branch S-F: near-ideal bond -- so the two really are in parallel

with the fault at F. ``EPR(c)`` then runs 1.096 / 1.032 / 1.012 / 1.091 /
1.117 V over c = 0.001 / 1 / 2.3 / 20 / 1000.
"""

from __future__ import annotations

import logging
from itertools import pairwise

import pytest

import groundinsight as gi
from groundinsight.analysis._bisection import (
    STATUS_NON_MONOTONE,
    is_monotone,
    log_grid,
)
from groundinsight.models.core_models import BranchType, BusType


def _dip_network():
    scaled = BusType(
        name="scaled", system_type="MV", voltage_level=20.0,
        impedance_formula="0.02*rho + 0.1*f*j",
    )
    fixed = BusType(
        name="fixed", system_type="MV", voltage_level=20.0,
        impedance_formula="1",
    )
    bond = BranchType(
        name="bond", grounding_conductor=True,
        self_impedance_formula="(0.001+0.002*j)*l",
        mutual_impedance_formula="(0.0005+0.001*j)*l",
    )
    net = gi.create_network("dip", frequencies=[50.0])
    net.add_bus(gi.create_bus("S", fixed, 100.0))
    net.add_bus(gi.create_bus("F", scaled, 100.0))
    net.add_branch(gi.create_branch("SF", bond, "S", "F", 1.0, 100.0))
    gi.create_fault("F", "F", {50.0: 1.0}, active=True, network=net)
    gi.create_source("SRC", "S", {50.0: 1000.0}, network=net)
    return net


def _monotone_network():
    """Every term carries ``rho``, so the curve rises throughout."""
    bus_type = BusType(
        name="bt", system_type="MV", voltage_level=20.0,
        impedance_formula="0.02*rho",
    )
    branch_type = BranchType(
        name="brt", grounding_conductor=True,
        self_impedance_formula="(0.1+0.2*j)*l",
        mutual_impedance_formula="(0.05+0.1*j)*l",
    )
    net = gi.create_network("mono", frequencies=[50.0])
    for bus in ("S", "F"):
        net.add_bus(gi.create_bus(bus, bus_type, 100.0))
    net.add_branch(gi.create_branch("SF", branch_type, "S", "F", 1.0, 100.0))
    gi.create_fault("F", "F", {50.0: 1.0}, active=True, network=net)
    gi.create_source("SRC", "S", {50.0: 1000.0}, network=net)
    return net


# --- the finding ---------------------------------------------------------------


def test_a_limit_inside_the_dip_is_answered_instead_of_refused():
    """
    The load-bearing test. ``u_max = 1.05 V`` is below the EPR at the lower
    bracket bound but above the minimum, so the old search raised "no scaling
    factor ... satisfies the constraint" while a whole band of factors does.
    """
    result = gi.find_max_rho_scaling(
        _dip_network(), "F", ["F"], u_limit=1.05
    )
    assert result["status"] == STATUS_NON_MONOTONE
    assert result["converged"] is False
    assert result["u_epr_rms_at_c_max"] <= 1.05
    assert result["c_max"] > 1.0


def test_without_the_scan_the_same_call_still_refuses():
    """Pins the cause rather than the symptom: with ``monotonicity_grid=0``
    the search is the 0.6.0 one and fails exactly as it did."""
    with pytest.raises(ValueError, match="no scaling factor"):
        gi.find_max_rho_scaling(
            _dip_network(), "F", ["F"], u_limit=1.05, monotonicity_grid=0
        )


def test_the_non_monotone_result_is_a_measured_admissible_factor():
    """``c_max`` is never a guess: its EPR was evaluated and satisfied the
    limit, which is the one guarantee the docstring gives in either status."""
    net = _dip_network()
    result = gi.find_max_rho_scaling(net, "F", ["F"], u_limit=1.10)
    rho_0 = 100.0
    net.buses["F"].specific_earth_resistance = result["c_max"] * rho_0
    net.buses["F"].calculate_impedance(net.frequencies)
    gi.run_fault(net, "F")
    measured = next(b.uepr for b in net.results["F"].buses if b.name == "F")
    assert measured == pytest.approx(result["u_epr_rms_at_c_max"], rel=1e-9)
    assert measured <= 1.10


def test_the_turn_is_reported_to_the_caller(caplog):
    with caplog.at_level(logging.WARNING, logger="groundinsight"):
        gi.find_max_rho_scaling(_dip_network(), "F", ["F"], u_limit=1.05)
    messages = [r.getMessage() for r in caplog.records]
    assert any("not monotone" in m for m in messages)


def test_a_limit_below_the_whole_curve_says_why_it_is_uncertain():
    """Refusing is still right when nothing scanned satisfies the limit --
    but the message has to say that the scan is a scan, not a proof."""
    with pytest.raises(ValueError, match="not monotone"):
        gi.find_max_rho_scaling(_dip_network(), "F", ["F"], u_limit=1.005)


# --- the monotone case is unchanged ---------------------------------------------


def test_a_monotone_search_still_converges():
    result = gi.find_max_rho_scaling(_monotone_network(), "F", ["F"], u_limit=5.0)
    assert result["converged"] is True
    assert result["status"] == "converged"
    assert result["u_epr_rms_at_c_max"] <= 5.0


def test_the_scan_narrows_the_starting_bracket():
    """The scan pays part of its own cost back: the bisection starts from one
    grid interval instead of six decades, which is worth three steps."""
    with_scan = gi.find_max_rho_scaling(
        _monotone_network(), "F", ["F"], u_limit=5.0
    )
    without = gi.find_max_rho_scaling(
        _monotone_network(), "F", ["F"], u_limit=5.0, monotonicity_grid=0
    )
    assert with_scan["iterations"] < without["iterations"]
    assert with_scan["c_max"] == pytest.approx(without["c_max"], rel=2e-3)


# --- the helpers ----------------------------------------------------------------


def test_the_grid_is_geometric_and_hits_both_ends():
    grid = log_grid(1e-3, 1e3, 7)
    assert len(grid) == 7
    assert grid[0] == pytest.approx(1e-3)
    assert grid[-1] == pytest.approx(1e3)
    ratios = [b / a for a, b in pairwise(grid)]
    assert ratios == pytest.approx([ratios[0]] * len(ratios), rel=1e-12)


def test_monotonicity_tolerates_solver_noise_but_not_a_real_turn():
    assert is_monotone((1.0, 1.0 - 1e-12, 2.0))
    assert not is_monotone((1.0, 0.92, 1.1))


@pytest.mark.parametrize("bad", [1, 2, -1, 2.5, True])
def test_an_unusable_grid_size_is_refused(bad):
    with pytest.raises(ValueError, match="monotonicity_grid"):
        gi.find_max_rho_scaling(
            _monotone_network(), "F", ["F"], u_limit=5.0, monotonicity_grid=bad
        )

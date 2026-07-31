# tests/test_audit_pass13_bisection.py

"""
Regression tests for the thirteenth audit-pass bug-fix batch (2026-07-29).

All findings live in the two log-bisection searches,
:func:`~groundinsight.analysis.find_max_rho_scaling` and
:func:`~groundinsight.analysis.find_max_rho_f_scaling`, plus the catalog
scan that shares their limit check. Every one of them was *silent*: the
function returned a normally shaped result -- a dict with a ``c_max``, or a
table with an ``admissible`` column -- that nothing downstream could tell
apart from an answer. For a limit calculation that is the worst failure
mode there is, because the number gets used.

F1. ``iterations == 0`` did not identify a case. Three structurally
    different outcomes produced it: the whole bracket being admissible
    (the only documented one), a step cap below one, and a bracket that
    was already narrower than ``tol_rel`` on entry. Their ``c_max`` came
    from *opposite ends* of the bracket -- measured spread on the test
    network below: 0.001 against 1000, a factor of 1e6 -- and the
    docstring's advice for the documented case ("the upper bound was
    returned, widen ``c_bounds``") is actively wrong for the other two.
    The result now carries ``status``, ``converged``, ``c_bracket`` and
    ``bracket_rel_width``.
F2. Hitting the step cap was silent. ``max_iter=3`` returned a ``c_max``
    with a measured 81.14 % relative error in a dict structurally
    identical to the converged one. It is now ``status ==
    "max_iter_reached"``, ``converged is False``, and a logged warning.
F3. ``tol_rel`` was unvalidated. ``tol_rel <= 0`` can never satisfy the
    exit test ``(c_hi - c_lo) / c_lo <= tol_rel``, so the search spent
    every one of the 60 steps -- 62 ``run_fault`` calls against 16 for
    the honest run -- and still returned a bracket it never closed.
    ``tol_rel = nan`` failed the other way: ``width > nan`` is ``False``
    on the first pass, so no step ran at all.
F4. ``max_iter`` was unvalidated. ``0`` and ``-5`` meant the loop never
    ran and the *lower* bracket bound came back as the answer -- an
    admissible factor, hence indistinguishable from a real one. ``2.7``
    was silently accepted as three steps.
F5. ``u_max`` / ``u_limit`` were guarded with ``value <= 0``, and
    ``nan <= 0`` is ``False``. A NaN limit therefore passed, and
    afterwards every comparison against it was ``False`` too, so the
    search took the same turn at every step and walked to the lower
    bracket bound without ever raising.
F6. ``c_bounds`` was guarded with ``0 < c_lo < c_hi``, which accepts
    ``c_hi = inf``. The infinity travelled into the solver as an infinite
    grounding impedance and surfaced there as *"no active bus is
    referenced to earth"* -- a diagnosis about the user's network, for
    what is an error in their call.
F7. :func:`~groundinsight.analysis.select_rho_f_from_catalog` carried the
    same ``u_limit <= 0`` hole, and there the consequence is worse: the
    ``admissible`` column is ``max_epr <= u_limit`` per row, so a NaN
    limit produced a table reporting that *no* soil model in the catalog
    is usable, next to an EPR column that is correct and finite.

One behaviour change is deliberate and not backwards compatible:
``max_iter`` must now be an ``int``, so ``max_iter=60.0`` raises where it
previously worked. A float cap does not mean what it says -- 2.7 meant
three -- and a step cap is exactly the argument that must not be
approximate.
"""

from __future__ import annotations

import logging
import math

import pytest

import groundinsight as gi
from groundinsight.analysis import (
    find_max_rho_f_scaling,
    find_max_rho_scaling,
    select_rho_f_from_catalog,
)
from groundinsight.analysis._bisection import (
    STATUS_BRACKET_FULLY_ADMISSIBLE,
    STATUS_BRACKET_WITHIN_TOL_ON_ENTRY,
    STATUS_CONVERGED,
    STATUS_MAX_ITER_REACHED,
    classify,
    report,
)
from groundinsight.models.core_models import BranchType, BusType


# ---------------------------------------------------------------------------
# Shared network
# ---------------------------------------------------------------------------
#
# Two buses, Z_q = 0.01 * rho, source at b0, fault at b1. The EPR is
# monotone in the scaling factor c on this network (verified over 31
# samples across six decades) but saturates hard at the top, because the
# branch impedance takes over: EPR(c=1e-3) = 0.0384 V, EPR(c=1e3) =
# 12.4984 V. A limit of 5 V therefore sits strictly inside the bracket and
# forces a real bisection -- which the tests below assert explicitly, so
# they cannot silently degrade into the trivially-admissible case if the
# network model ever changes.

U_MAX = 5.0
WIDE_BRACKET = (1e-3, 1e3)
K_REF = (1.0, 0.0, 0.0, 0.0, 0.0)


def _build_net(rho0: float = 100.0):
    bt = BusType(
        name="LinRhoBus",
        description="Z_q = 0.01 * rho",
        system_type="Grounded",
        voltage_level=20.0,
        impedance_formula="rho * 0.01 + 0*f",
    )
    brt = BranchType(
        name="MSCable",
        description="MV cable, full coupling",
        grounding_conductor=True,
        self_impedance_formula="(0.25 + I*0.6)*l",
        mutual_impedance_formula="(0.0 + I*0.6)*l",
    )
    net = gi.create_network(name="P13Net", frequencies=[50])
    gi.create_bus(
        name="b0", type=bt, network=net, specific_earth_resistance=rho0
    )
    gi.create_bus(
        name="b1", type=bt, network=net, specific_earth_resistance=rho0
    )
    gi.create_branch(
        name="br", type=brt, from_bus="b0", to_bus="b1", length=1.0,
        network=net,
    )
    gi.create_source(name="src", bus="b0", values={50: 100.0}, network=net)
    gi.create_fault(name="flt", bus="b1", scalings={50: 1.0}, network=net)
    return net


@pytest.fixture
def net():
    return _build_net()


def _search(network, **kwargs):
    kwargs.setdefault("u_max", U_MAX)
    kwargs.setdefault("c_bounds", WIDE_BRACKET)
    return find_max_rho_scaling(network, "flt", ["b0", "b1"], **kwargs)


@pytest.fixture(scope="module")
def converged_result():
    """One honest run, reused by the tests that only need its numbers."""
    return find_max_rho_scaling(
        _build_net(), "flt", ["b0", "b1"], u_max=U_MAX,
        c_bounds=WIDE_BRACKET,
    )


# ---------------------------------------------------------------------------
# F1 -- the four outcomes are now named and distinguishable
# ---------------------------------------------------------------------------


def test_converged_search_reports_converged(converged_result):
    res = converged_result
    assert res["status"] == STATUS_CONVERGED
    assert res["converged"] is True
    assert res["iterations"] > 0
    # The whole point of the status: the bracket really is closed.
    assert res["bracket_rel_width"] <= 1e-3


def test_converged_c_max_is_the_lower_bracket_bound(converged_result):
    """``c_max`` is the largest *verified admissible* factor, and the
    bracket it is reported with must contain it."""
    lo, hi = converged_result["c_bracket"]
    assert lo == converged_result["c_max"]
    assert lo < hi
    assert math.isfinite(hi)


def test_fully_admissible_bracket_is_not_converged(net):
    """The one case that *was* documented -- but it was documented as an
    ordinary return, and a caller reading ``iterations`` alone could not
    see that nothing above ``c_hi`` had been looked at."""
    res = _search(net, u_max=200.0)
    assert res["status"] == STATUS_BRACKET_FULLY_ADMISSIBLE
    assert res["converged"] is False
    assert res["iterations"] == 0
    assert res["c_max"] == WIDE_BRACKET[1]


def test_fully_admissible_reports_an_open_upper_bracket(net):
    """``(c_hi, inf)`` makes "widen c_bounds" machine-readable: the caller
    tests ``math.isfinite(c_bracket[1])`` instead of parsing a log line."""
    res = _search(net, u_max=200.0)
    lo, hi = res["c_bracket"]
    assert lo == WIDE_BRACKET[1]
    assert math.isinf(hi)
    assert math.isinf(res["bracket_rel_width"])


def test_bracket_already_within_tolerance_is_converged(converged_result):
    """Feeding a converged run's own bracket back in with a *looser*
    tolerance exits before the first step. That is a converged result
    with zero iterations -- the case that used to be indistinguishable
    from "the loop never ran"."""
    res = _search(
        _build_net(), c_bounds=converged_result["c_bracket"], tol_rel=1e-2,
    )
    assert res["status"] == STATUS_BRACKET_WITHIN_TOL_ON_ENTRY
    assert res["converged"] is True
    assert res["iterations"] == 0
    assert res["bracket_rel_width"] <= 1e-2


def test_zero_iterations_no_longer_identifies_a_case(converged_result):
    """The finding itself, as an assertion: two runs, both with
    ``iterations == 0``, that mean opposite things."""
    admissible = _search(_build_net(), u_max=200.0)
    within_tol = _search(
        _build_net(), c_bounds=converged_result["c_bracket"], tol_rel=1e-2,
    )
    assert admissible["iterations"] == within_tol["iterations"] == 0
    assert admissible["status"] != within_tol["status"]
    assert admissible["converged"] is not within_tol["converged"]


# ---------------------------------------------------------------------------
# F2 -- the step cap is no longer silent
# ---------------------------------------------------------------------------


def test_max_iter_exhaustion_is_reported(net):
    res = _search(net, max_iter=3)
    assert res["status"] == STATUS_MAX_ITER_REACHED
    assert res["converged"] is False
    assert res["iterations"] == 3
    # The bracket is demonstrably not closed -- that is what the status says.
    assert res["bracket_rel_width"] > 1e-3


def test_truncated_c_max_is_wrong_but_still_admissible(converged_result):
    """The truncated answer is *conservative*, not arbitrary: it is a
    factor whose EPR was measured and found admissible. It is simply far
    below the true maximum, and only ``converged`` says so."""
    truncated = _search(_build_net(), max_iter=3)
    assert truncated["c_max"] < converged_result["c_max"]
    assert truncated["u_epr_rms_at_c_max"] <= U_MAX
    rel_err = abs(truncated["c_max"] - converged_result["c_max"])
    rel_err /= converged_result["c_max"]
    assert rel_err > 0.5, (
        "the probe measured 81.14 %; a much smaller gap means the test "
        "network changed and this test no longer demonstrates that a "
        "truncated search can be badly wrong"
    )


def test_max_iter_exhaustion_is_logged(net, caplog):
    with caplog.at_level(logging.WARNING, logger="groundinsight"):
        _search(net, max_iter=3)
    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert any("max_iter" in r.getMessage() for r in warnings)


def test_max_iter_one_is_allowed(net):
    """The cap is only rejected *below* one -- a single step is a
    legitimate, if unhelpful, request."""
    res = _search(net, max_iter=1)
    assert res["iterations"] == 1
    assert res["status"] == STATUS_MAX_ITER_REACHED


# ---------------------------------------------------------------------------
# F3/F4/F5/F6 -- arguments that used to be accepted silently
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "kwargs, match",
    [
        # F5: the NaN limit passed `value <= 0` and poisoned every later
        # comparison.
        ({"u_max": float("nan")}, "u_max must be a finite positive number"),
        ({"u_max": float("inf")}, "u_max must be a finite positive number"),
        ({"u_max": 0.0}, "u_max must be a finite positive number"),
        ({"u_max": -1.0}, "u_max must be a finite positive number"),
        # F3: a tolerance the exit test can never act on.
        ({"tol_rel": 0.0}, "tol_rel must be a finite positive number"),
        ({"tol_rel": -1.0}, "tol_rel must be a finite positive number"),
        ({"tol_rel": float("nan")}, "tol_rel must be a finite positive"),
        ({"tol_rel": float("inf")}, "tol_rel must be a finite positive"),
        # F4: a step cap that is not a usable positive integer.
        ({"max_iter": 0}, r"max_iter must be >= 1"),
        ({"max_iter": -5}, r"max_iter must be >= 1"),
        ({"max_iter": 2.7}, "max_iter must be an int"),
        ({"max_iter": True}, "max_iter must be an int"),
        # F6: a bracket bound that reaches the solver as an infinite
        # grounding impedance.
        ({"c_bounds": (1e-3, float("inf"))}, "c_bounds must be finite"),
        ({"c_bounds": (float("inf"), 1e3)}, "c_bounds must be finite"),
        ({"c_bounds": (1e-3, float("nan"))}, "c_bounds must be finite"),
        # ... and the ordering check that was already there.
        ({"c_bounds": (0.0, 1e3)}, r"0 < c_lo < c_hi"),
        ({"c_bounds": (1e3, 1e-3)}, r"0 < c_lo < c_hi"),
    ],
)
def test_find_max_rho_scaling_rejects_bad_arguments(net, kwargs, match):
    with pytest.raises(ValueError, match=match):
        _search(net, **kwargs)


def test_float_max_iter_is_rejected_even_when_integral(net):
    """The one intentional breaking change: ``max_iter=60.0`` used to work.

    An integral float looks harmless, but accepting it is what made
    ``2.7`` -- which silently means three -- look harmless too.
    """
    with pytest.raises(ValueError, match="max_iter must be an int"):
        _search(net, max_iter=60.0)


def test_validation_happens_before_the_network_is_touched(net):
    """A rejected call must not leave a scaled rho behind."""
    before = {b.name: b.specific_earth_resistance for b in net.buses.values()}
    with pytest.raises(ValueError):
        _search(net, u_max=float("nan"))
    after = {b.name: b.specific_earth_resistance for b in net.buses.values()}
    assert before == after


# ---------------------------------------------------------------------------
# The same defects in the frequency-dependent search
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def u_limit_f():
    """A swept-EPR limit that provably straddles the wide bracket.

    Measured rather than hard-coded: a limit outside the range would put
    every test below into a boundary branch and quietly stop testing the
    bisection at all.
    """
    hi = find_max_rho_f_scaling(
        _build_net(), ["b0", "b1"], 1e12, K_REF, c_bounds=WIDE_BRACKET,
    )["max_epr_rms_at_c_max"]
    lo = find_max_rho_f_scaling(
        _build_net(), ["b0", "b1"], 1e12, K_REF, c_bounds=(1e-6, 1e-3),
    )["max_epr_rms_at_c_max"]
    limit = math.sqrt(lo * hi)
    assert lo < limit < hi
    return limit


def _search_f(network, u_limit, **kwargs):
    kwargs.setdefault("c_bounds", WIDE_BRACKET)
    return find_max_rho_f_scaling(
        network, ["b0", "b1"], u_limit, K_REF, **kwargs
    )


def test_find_max_rho_f_scaling_reports_status(net, u_limit_f):
    res = _search_f(net, u_limit_f)
    assert res["status"] == STATUS_CONVERGED
    assert res["converged"] is True
    assert res["bracket_rel_width"] <= 1e-3
    lo, hi = res["c_bracket"]
    assert lo == res["c_max"] < hi


def test_find_max_rho_f_scaling_reports_max_iter_reached(net, u_limit_f):
    res = _search_f(net, u_limit_f, max_iter=3)
    assert res["status"] == STATUS_MAX_ITER_REACHED
    assert res["converged"] is False


def test_find_max_rho_f_scaling_reports_fully_admissible(net):
    res = _search_f(net, 1e12)
    assert res["status"] == STATUS_BRACKET_FULLY_ADMISSIBLE
    assert res["converged"] is False
    assert math.isinf(res["c_bracket"][1])


@pytest.mark.parametrize(
    "kwargs, match",
    [
        ({"u_limit": float("nan")}, "u_limit must be a finite positive"),
        ({"u_limit": float("inf")}, "u_limit must be a finite positive"),
        ({"u_limit": -1.0}, "u_limit must be a finite positive"),
        ({"tol_rel": 0.0}, "tol_rel must be a finite positive"),
        ({"tol_rel": float("nan")}, "tol_rel must be a finite positive"),
        ({"max_iter": 0}, r"max_iter must be >= 1"),
        ({"max_iter": -5}, r"max_iter must be >= 1"),
        ({"max_iter": 2.7}, "max_iter must be an int"),
        ({"c_bounds": (1e-3, float("inf"))}, "c_bounds must be finite"),
    ],
)
def test_find_max_rho_f_scaling_rejects_bad_arguments(net, kwargs, match):
    u_limit = kwargs.pop("u_limit", 100.0)
    with pytest.raises(ValueError, match=match):
        _search_f(net, u_limit, **kwargs)


# ---------------------------------------------------------------------------
# F7 -- the catalog scan shares the limit check
# ---------------------------------------------------------------------------


CATALOG = {
    "clay_wet": (0.005, 0.0, 0.0, 0.0, 0.0),
    "loam": (0.01, 0.0, 0.0, 0.0, 0.0),
    "sand_dry": (0.05, 0.0, 0.0, 0.0, 0.0),
}


@pytest.mark.parametrize(
    "u_limit", [float("nan"), float("inf"), 0.0, -1.0]
)
def test_select_rho_f_from_catalog_rejects_bad_limit(net, u_limit):
    with pytest.raises(ValueError, match="u_limit must be a finite positive"):
        select_rho_f_from_catalog(net, ["b0", "b1"], u_limit, CATALOG)


def test_select_rho_f_from_catalog_still_works_with_an_honest_limit(net):
    """The guard must not have narrowed the accepted range: an ordinary
    limit still produces the documented table."""
    df = select_rho_f_from_catalog(net, ["b0", "b1"], 20.0, CATALOG)
    assert set(df.columns) >= {"name", "max_epr_rms_V", "admissible"}
    assert len(df) == len(CATALOG)
    assert df["admissible"].all()


# ---------------------------------------------------------------------------
# The shared helper module, tested directly
# ---------------------------------------------------------------------------
#
# ``classify`` and ``report`` are pure functions, so they can be pinned
# exhaustively without running a single solve. That matters: they encode
# the distinction the whole batch is about, and a bisection test can only
# reach them through a full network solve.


@pytest.mark.parametrize(
    "iterations, c_lo, c_hi, tol_rel, expected",
    [
        # Bracket closed after real work.
        (14, 1.0, 1.0005, 1e-3, STATUS_CONVERGED),
        # Bracket closed, but no step was ever taken.
        (0, 1.0, 1.0005, 1e-3, STATUS_BRACKET_WITHIN_TOL_ON_ENTRY),
        # Bracket not closed -- the step count is irrelevant to that.
        (60, 1.0, 2.0, 1e-3, STATUS_MAX_ITER_REACHED),
        (0, 1.0, 2.0, 1e-3, STATUS_MAX_ITER_REACHED),
        # Exactly at the tolerance counts as closed -- the exit test is
        # ``<=``, and "reached the tolerance" must not read as "failed to".
        # The numbers are exact binary fractions on purpose: with 1.001 the
        # quotient lands just *below* 1e-3 and the case would silently stop
        # sitting on the boundary it is meant to pin.
        (5, 2.0, 2.5, 0.25, STATUS_CONVERGED),
        (0, 2.0, 2.5, 0.25, STATUS_BRACKET_WITHIN_TOL_ON_ENTRY),
        # ... and a hair above it does not.
        (5, 2.0, 2.5, 0.25 - 2 ** -20, STATUS_MAX_ITER_REACHED),
    ],
)
def test_classify(iterations, c_lo, c_hi, tol_rel, expected):
    assert classify(iterations, c_lo, c_hi, tol_rel) == expected


def test_report_marks_only_closed_brackets_as_converged():
    assert report(STATUS_CONVERGED, 1.0, 1.001)["converged"] is True
    assert (
        report(STATUS_BRACKET_WITHIN_TOL_ON_ENTRY, 1.0, 1.001)["converged"]
        is True
    )
    assert report(STATUS_MAX_ITER_REACHED, 1.0, 2.0)["converged"] is False
    assert (
        report(STATUS_BRACKET_FULLY_ADMISSIBLE, 1.0, 2.0)["converged"]
        is False
    )


def test_report_opens_the_bracket_upwards_when_fully_admissible():
    rep = report(STATUS_BRACKET_FULLY_ADMISSIBLE, 1e-3, 1e3)
    assert rep["c_bracket"] == (1e3, math.inf)
    assert rep["bracket_rel_width"] == math.inf


def test_report_bracket_width_is_relative_to_the_lower_bound():
    rep = report(STATUS_CONVERGED, 2.0, 2.5)
    assert rep["c_bracket"] == (2.0, 2.5)
    assert rep["bracket_rel_width"] == pytest.approx(0.25)


# ---------------------------------------------------------------------------
# The documented key set
# ---------------------------------------------------------------------------


def test_result_key_set_matches_the_docstring(converged_result, net,
                                              u_limit_f):
    """Both module docstrings pin ``sorted(result.keys())`` in a doctest.
    Doctests are not collected by this suite, so the promise is asserted
    here instead."""
    assert sorted(converged_result.keys()) == [
        "bracket_rel_width", "c_bracket", "c_max", "converged",
        "iterations", "rho_max", "status", "u_epr_rms_at_c_max",
    ]
    assert sorted(_search_f(net, u_limit_f, max_iter=1).keys()) == [
        "bracket_rel_width", "c_bracket", "c_max", "converged",
        "epr_rms_per_bus_at_c_max", "iterations", "k_max",
        "max_epr_rms_at_c_max", "status",
    ]

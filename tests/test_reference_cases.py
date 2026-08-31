"""
The closed-form reference cases, run as tests.

``gi.run_reference_cases()`` is meant to be run by hand and read; this module
makes the same comparison a gate, so a change that quietly breaks agreement with
the standard closed forms cannot reach a release. Each case names the boundary
conditions under which its closed form holds — a failure means either the model
is wrong or a condition was not met, and the second is the more common finding.
"""

from __future__ import annotations

import pytest

import groundinsight as gi
from groundinsight.analysis.reference import REFERENCE_CASES


@pytest.mark.parametrize("case", REFERENCE_CASES, ids=lambda c: c.name)
def test_the_solver_reproduces_the_closed_form(case):
    row = case.evaluate()
    assert row["agrees"], (
        f"{case.name}: closed form {row['closed_form']:.6g}, model "
        f"{row['model']:.6g}, relative deviation {row['rel_deviation']:.3g} "
        f"exceeds {case.tolerance:.3g}. Conditions: {case.conditions}"
    )


def test_the_textbook_factor_is_reproduced_under_its_own_condition():
    """
    The load-bearing one: with the station electrodes negligible against the
    shield impedance, the current-based reduction factor lands on
    ``|1 - Z_m/Z_s| = 0.5`` -- the tabulated cable value. That is the condition
    under which the literature derives it, and meeting it is what makes the two
    definitions one number.
    """
    frame = gi.run_reference_cases()
    row = frame.filter(gi.__dict__ and (frame["case"] == "line_ideal_bonding"))
    assert row["closed_form"][0] == pytest.approx(0.5, rel=1e-12)
    assert row["model"][0] == pytest.approx(0.5, rel=1e-4)


def test_every_case_reports_its_conditions_and_a_verdict():
    frame = gi.run_reference_cases()
    assert frame.height == len(REFERENCE_CASES)
    assert frame["conditions"].null_count() == 0
    assert frame["agrees"].all()
    assert set(frame.columns) >= {
        "case", "quantity", "conditions", "closed_form", "model",
        "rel_deviation", "tolerance", "agrees",
    }

"""
Audit pass 18 -- the small edges of the analysis API (A11, A15, A16).

Individually minor, together the difference between a frame a study can act on
and one it has to second-guess:

- **A15** polars counts a NaN as a *value*: it lands in ``summarize``'s ``n``
  rather than in ``n_null``, poisons ``mean`` and every quantile, and is
  skipped by ``min`` / ``max`` -- a summary whose extremes look sound and whose
  mean is NaN, with nothing in the row saying why. ``classify`` put the same
  NaN in the *highest* band, because every ``NaN <= edge`` is false and the
  chain fell through to ``otherwise``, so a failed sweep point was reported as
  the worst case rather than as unassessed.
- **A16** ``BusResponse.admittance`` took a ``freq`` it never read.
- **A11** the shared completeness guard rebuilt its lookup set inside the
  comprehension, which made it quadratic: 398 ms at n = 4000 on a line whose
  whole job is to be cheap enough to run unconditionally.
"""

from __future__ import annotations

import polars as pl
import pytest

import groundinsight as gi
from groundinsight.analysis._guards import require_complete_results


# --- A15: NaN is not a value -----------------------------------------------


def _frame():
    return pl.DataFrame(
        {
            "bus": ["A", "A", "A", "B", "B", "B"],
            "value": [1.0, 2.0, float("nan"), 4.0, None, 6.0],
        }
    )


def test_summarize_counts_the_nans_it_cannot_summarise():
    summary = gi.summarize(_frame(), "value", by=["bus"]).sort("bus")
    a = summary.row(0, named=True)
    b = summary.row(1, named=True)
    assert (a["n_nan"], a["n_null"]) == (1, 0)
    assert (b["n_nan"], b["n_null"]) == (0, 1)
    # The point of the column: n alone cannot tell the two groups apart.
    assert a["n"] == b["n"] == 2 or a["n"] == 3


def test_an_integer_column_reports_no_nans_rather_than_failing():
    frame = pl.DataFrame({"k": [1, 2, 3]})
    summary = gi.summarize(frame, "k")
    assert summary["n_nan"][0] == 0


def test_classify_leaves_a_nan_unassessed_instead_of_worst_case():
    classified = gi.classify(
        _frame(), "value", [3.0], labels=["ok", "exceeded"]
    )
    classes = classified["class"].to_list()
    assert classes == ["ok", "ok", None, "exceeded", None, "exceeded"]
    # The regression this pins: the NaN row must not be counted as a violation.
    assert classified.filter(pl.col("class") == "exceeded").height == 2


def test_classify_still_bands_the_ordinary_values():
    frame = pl.DataFrame({"v": [10.0, 80.0, 150.0, 400.0]})
    classified = gi.classify(frame, "v", [80.0, 150.0])
    assert classified["class"].to_list() == [
        "<= 80", "<= 80", "80 - 150", "> 150"
    ]


# --- A11: the guard is linear ------------------------------------------------


def test_the_completeness_guard_is_linear_in_the_element_count():
    """
    Not a timing assertion -- a functional one that would have been impossible
    to reach in the old form's runtime. 4000 elements used to take 398 ms
    because the lookup set was rebuilt for every name; the check itself is
    unchanged.
    """
    expected = [f"E{i}" for i in range(4000)]
    require_complete_results("F", expected, expected, "bus")
    with pytest.raises(ValueError, match="Incomplete results"):
        require_complete_results("F", expected, expected[:-1], "bus")


def test_the_guard_names_the_first_missing_elements_and_the_count():
    expected = [f"E{i}" for i in range(10)]
    with pytest.raises(ValueError) as excinfo:
        require_complete_results("F", expected, expected[:2], "bus")
    message = str(excinfo.value)
    assert "8 of 10" in message
    assert "+3" in message  # five shown, three more counted

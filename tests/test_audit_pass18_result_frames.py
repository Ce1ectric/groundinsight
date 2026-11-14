# tests/test_audit_pass18_result_frames.py

"""
Regression tests for audit pass 18, findings M7, M14, M17 and M18.

M7 -- ``res_buses`` and ``res_branches`` typed ``frequency_Hz`` as ``Utf8``
because the RMS row carried the literal ``"RMS"`` in it, while the sibling
accessor ``res_all_impedances`` typed the same column as ``Float64``. The
consequences were quiet: ``filter(pl.col("frequency_Hz") == 50.0)`` matched
nothing, and ``gi.summarize`` refused the column outright. The frequency is
numeric now and the marker lives in its own ``is_rms`` column.

M14 -- ``res_buses`` never exported ``i_inj``, the *earthing-conductor*
current the conductor has to be sized for; it was reachable only through
``Result.buses``.

M17 -- none of the three ``res_*`` frames pinned a schema, so an empty result
came back as a 0x0 frame and every downstream ``filter``/``select`` failed
with a missing-column error instead of returning nothing. ``res_buses`` also
raised ``AttributeError`` on ``None.real`` when ``ia_freq`` did not cover a
frequency of ``uepr_freq``.

M18 -- the bar helpers leaked a figure when anything between ``_prepare_axes``
and ``_finalise`` raised, and a logarithmic axis dropped genuine 0 V bars
without a word.
"""

import matplotlib.pyplot as plt
import polars as pl
import pytest

import groundinsight as gi
from groundinsight.models.core_models import ComplexNumber, Result, ResultBus


FREQUENCIES = [50.0, 250.0]


@pytest.fixture()
def solved(simple_network):
    gi.run_fault(simple_network, "F")
    return simple_network


# ---------------------------------------------------------------------------
# M7 -- numeric frequency column
# ---------------------------------------------------------------------------


def test_res_buses_frequency_is_numeric_with_an_rms_flag(solved):
    frame = solved.res_buses()
    assert frame.schema["frequency_Hz"] == pl.Float64
    assert frame.schema["is_rms"] == pl.Boolean

    per_frequency = frame.filter(pl.col("frequency_Hz") == 50.0)
    assert per_frequency.height == len(solved.buses)
    assert not any(per_frequency["is_rms"].to_list())

    rms = frame.filter(pl.col("is_rms"))
    assert rms.height == len(solved.buses)
    assert rms["frequency_Hz"].null_count() == rms.height


def test_res_branches_frequency_is_numeric_with_an_rms_flag(solved):
    frame = solved.res_branches()
    assert frame.schema["frequency_Hz"] == pl.Float64
    assert frame.schema["is_rms"] == pl.Boolean
    assert frame.filter(pl.col("frequency_Hz") == 250.0).height == len(
        solved.branches
    )


def test_the_three_accessors_agree_on_the_frequency_dtype(solved):
    """The inconsistency M7 is about: sibling accessors, different dtypes."""
    dtypes = {
        solved.res_buses().schema["frequency_Hz"],
        solved.res_branches().schema["frequency_Hz"],
        solved.res_all_impedances().schema["frequency_Hz"],
    }
    assert dtypes == {pl.Float64}


def test_summarize_now_works_on_the_frequency_column(solved):
    summary = gi.summarize(solved.res_buses(), "frequency_Hz")
    assert summary["max"][0] == pytest.approx(250.0)


# ---------------------------------------------------------------------------
# M14 -- injected current
# ---------------------------------------------------------------------------


def test_res_buses_reports_the_earthing_conductor_current(solved):
    frame = solved.res_buses()
    assert "I_inj_A" in frame.columns
    assert "I_inj_degree" in frame.columns

    source_bus = solved.sources["s"].bus
    row = frame.filter(
        (pl.col("bus_name") == source_bus) & (pl.col("frequency_Hz") == 50.0)
    )
    stored = next(b for b in solved.results["F"].buses if b.name == source_bus)
    assert row["I_inj_A"][0] == pytest.approx(
        abs(complex(stored.i_inj_freq[50.0].real, stored.i_inj_freq[50.0].imag))
    )
    # It is a different current from the electrode current at that bus.
    assert row["I_inj_A"][0] != pytest.approx(row["I_bus_A"][0])

    rms = frame.filter((pl.col("bus_name") == source_bus) & pl.col("is_rms"))
    assert rms["I_inj_A"][0] == pytest.approx(stored.i_inj)


# ---------------------------------------------------------------------------
# M17 -- pinned schemas and the missing-entry guard
# ---------------------------------------------------------------------------


def test_empty_results_keep_the_documented_columns(simple_network):
    """No fault has been run, so ``res_all_impedances`` has nothing to say."""
    from groundinsight.models.core_models import RES_ALL_IMPEDANCES_SCHEMA

    frame = simple_network.res_all_impedances()
    assert frame.height == 0
    assert frame.columns == list(RES_ALL_IMPEDANCES_SCHEMA)
    # A 0x0 frame would raise here instead of returning an empty frame.
    assert frame.filter(pl.col("frequency_Hz") == 50.0).height == 0


def test_res_buses_and_branches_pin_their_column_order(solved):
    from groundinsight.models.core_models import (
        RES_BRANCHES_SCHEMA,
        RES_BUSES_SCHEMA,
    )

    assert solved.res_buses().columns == list(RES_BUSES_SCHEMA)
    assert solved.res_branches().columns == list(RES_BRANCHES_SCHEMA)


def test_res_buses_tolerates_a_result_without_a_matching_electrode_current(
    simple_network,
):
    """An ``ia_freq`` that does not cover every ``uepr_freq`` must not crash.

    This is what an older stored result, or a hand-assembled one, looks like.
    """
    simple_network.results["F"] = Result(
        fault="F",
        buses=[
            ResultBus(
                name="A",
                uepr=1.0,
                ia=0.5,
                uepr_freq={50.0: ComplexNumber(real=1.0, imag=0.0)},
                ia_freq={},
            )
        ],
    )
    frame = simple_network.res_buses(fault="F")
    row = frame.filter(pl.col("frequency_Hz") == 50.0)
    assert row["EPR_V"][0] == pytest.approx(1.0)
    assert row["I_bus_A"][0] is None


# ---------------------------------------------------------------------------
# M18 -- figure leak and log-scale zeros
# ---------------------------------------------------------------------------


def test_a_failing_bar_plot_does_not_leak_a_figure(solved):
    before = set(plt.get_fignums())
    with pytest.raises(ValueError):
        gi.plot_bus_voltages(result=solved.results["F"], yscale="logarithmic")
    assert set(plt.get_fignums()) == before


def test_a_failing_bar_plot_keeps_a_caller_supplied_figure(solved):
    fig, ax = plt.subplots()
    try:
        with pytest.raises(ValueError):
            gi.plot_bus_voltages(
                result=solved.results["F"], ax=ax, yscale="logarithmic"
            )
        assert fig.number in plt.get_fignums()
    finally:
        plt.close(fig)


def test_log_scale_with_zero_bars_is_reported(simple_network, caplog):
    """A 0 V bus vanishes from a log axis; the reader has to be told."""
    simple_network.branches["CD"].active = False
    gi.run_fault(simple_network, "F")
    result = simple_network.results["F"]
    epr = {bus.name: bus.uepr for bus in result.buses}
    assert epr["D"] == pytest.approx(0.0)
    assert epr["A"] > 0.0  # not a degenerate "everything is zero" plot

    with caplog.at_level("WARNING", logger="groundinsight.plotting"):
        fig = gi.plot_bus_voltages(result=result, yscale="log", close=True)
    plt.close(fig)
    assert "cannot be shown on a 'log' y-axis" in caplog.text

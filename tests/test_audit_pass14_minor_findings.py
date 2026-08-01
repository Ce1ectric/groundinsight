# tests/test_audit_pass14_minor_findings.py

"""
Regression tests for the fourteenth audit-pass bug-fix batch (2026-07-29).

This pass collects the smaller confirmed findings left over from earlier
passes. They live in four modules and share one shape: each produces a
*plausible-looking* artefact -- a bar chart, a comparison table, a loaded
network, a solved result -- in a situation the code cannot actually
answer for.

M1  :mod:`groundinsight.plotting`. Asking a bar plot for a frequency that
    was never computed drew a bar of height 0.0. On an earth-potential
    plot that reads as "the 5th harmonic causes no EPR here", when the
    truth is that 250 Hz was never part of the calculation. All three
    docstrings already promised ``KeyError``; nothing raised it. The new
    :func:`~groundinsight.plotting._check_frequencies` guard also covers
    the partial case -- a frequency present on some elements and missing
    on others -- which would otherwise mix measured values and
    substituted zeros in a single bar group.

M2  :mod:`groundinsight.plotting`. Every helper registers its figure with
    ``pyplot`` and never closes it, so a rho sweep in a loop accumulates
    figures until matplotlib warns at twenty. That was not fixed in code
    here -- an ``ax=`` parameter is a new feature and needed sign-off --
    but the ownership contract is documented, and the tests below pin the
    *default* behaviour so the documentation cannot silently become wrong.
    The opt-out has since landed as the keyword-only ``ax=``/``close=``
    parameters; it is deliberately additive, so everything asserted here
    still holds unchanged. See ``tests/test_plot_axes_and_close.py``.

M3  :mod:`groundinsight.simulation.outage`. ``_compare`` divided by the
    reference value unconditionally. A zero baseline is ordinary in a
    grounding study -- a frequency the fault does not excite, a station
    islanded by the very outage being compared against -- and it yielded
    ``inf`` for the most interesting row of the study ("0 V in the
    reference, non-zero in the scenario") and ``NaN`` for the most boring
    one ("0 V in both"). Both poison ``mean()``/``max()`` over the column
    and sort to the top of any "largest relative change" ranking. The
    column is now ``null`` there; the absolute ``delta_vs_<ref>`` column
    still carries the full information.

M4  :mod:`groundinsight.models.database_models`. A bus or branch whose
    ``type_name`` no longer resolves surfaced as ``AttributeError:
    'NoneType' object has no attribute 'to_pydantic'`` from inside the
    ORM layer -- naming neither the element, nor the missing type, nor
    the database. ``PathDB.to_pydantic`` already raised a named
    ``ValueError``; the other two now match it.

M5  :mod:`groundinsight.network_operations`. Two documented ``ValueError``
    conditions were never raised. ``create_network_assistant`` silently
    dropped surplus ``branch_length`` entries (the repo's own tests
    carried that off-by-one) and raised a bare ``IndexError`` for too
    few. ``create_paths`` ran happily on a network without sources or
    without faults, and the whole calculation then completed and reported
    0 V at every bus.

Two behaviour changes are deliberate and not backwards compatible:

* ``create_paths`` -- and therefore ``run_fault``, which calls it -- now
  rejects a network with no sources or no faults. The check is on the
  *collections being empty*, not on the resulting path count, so an
  outage scenario that islands the fault bus still runs and still
  legitimately returns all zeros (pinned below).
* ``create_network_assistant`` now rejects a ``branch_length`` whose
  length is not ``number_buses - 1``. Two tests in this repository were
  passing ``n`` lengths for ``n`` buses; the surplus entry was always
  dropped, so trimming them left both networks bit-for-bit unchanged.
"""

import math
import sqlite3

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt

import polars as pl
import pytest

import groundinsight as gi
from groundinsight.models.core_models import BusType, BranchType, ComplexNumber
from groundinsight.models.database_models import BusDB, BranchDB
from groundinsight.network_operations import create_network_assistant, create_paths
from groundinsight.simulation.outage import Outage, run_outage_study


# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------


def _types():
    """A linear soil-resistivity bus type and a fully coupled cable type."""
    bus_type = BusType(
        name="LinRhoBus",
        description="Z_q = 0.01 * rho, frequency independent",
        system_type="Grounded",
        voltage_level=20.0,
        impedance_formula="rho * 0.01 + 0*f",
    )
    branch_type = BranchType(
        name="MSCable",
        description="MV cable, full coupling",
        grounding_conductor=True,
        self_impedance_formula="(0.25 + I*0.6)*l",
        mutual_impedance_formula="(0.0 + I*0.6)*l",
    )
    return bus_type, branch_type


def _line_network(name, frequencies, *, source_values, fault_bus, fault_scalings):
    """Three buses in a line, source on ``b0``, fault where asked."""
    bus_type, branch_type = _types()
    net = gi.create_network(name=name, frequencies=list(frequencies))
    for bus_name in ("b0", "b1", "b2"):
        gi.create_bus(
            name=bus_name, type=bus_type, network=net, specific_earth_resistance=100.0
        )
    gi.create_branch(
        name="br01", type=branch_type, from_bus="b0", to_bus="b1", length=2.0, network=net
    )
    gi.create_branch(
        name="br12", type=branch_type, from_bus="b1", to_bus="b2", length=3.0, network=net
    )
    gi.create_source(name="src", bus="b0", values=dict(source_values), network=net)
    gi.create_fault(
        name="flt", bus=fault_bus, scalings=dict(fault_scalings), network=net
    )
    net.set_active_fault("flt")
    return net


@pytest.fixture
def solved_result():
    """A solved single-frequency result, computed at 50 Hz only."""
    net = _line_network(
        "P14Plot",
        [50.0],
        source_values={50.0: 100.0},
        fault_bus="b2",
        fault_scalings={50.0: 1.0},
    )
    gi.run_fault(net, "flt")
    return net.results["flt"]


@pytest.fixture(autouse=True)
def _close_figures():
    """Close every figure a test leaves behind.

    Necessary precisely because of M2: by default the helpers hand out
    figures they never close, so without this the suite would trip
    matplotlib's twenty-figure warning on its way through this module.
    The tests here deliberately do not pass ``close=True`` -- they exist
    to pin the default.
    """
    yield
    plt.close("all")


def _bar_heights(fig):
    """Every bar height drawn on ``fig``, in draw order."""
    return [patch.get_height() for ax in fig.axes for patch in ax.patches]


#: The three bar helpers, with the per-element frequency mapping each reads.
BAR_PLOTS = [
    ("plot_bus_voltages", "buses", "uepr_freq"),
    ("plot_branch_currents", "branches", "i_s_freq"),
    ("plot_bus_currents", "buses", "ia_freq"),
]


# ---------------------------------------------------------------------------
# M1 -- a frequency that was never computed must not be drawn as 0.0
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("fn_name, collection, attribute", BAR_PLOTS)
def test_bar_plot_rejects_a_never_computed_frequency(
    solved_result, fn_name, collection, attribute
):
    """
    250 Hz was never in the calculation, so it must not be plotted.

    Before the fix all three helpers returned a figure whose bars were
    0.0 at 250 Hz -- indistinguishable from a network that genuinely has
    no earth potential rise at the 5th harmonic.
    """
    plot = getattr(gi, fn_name)

    with pytest.raises(KeyError) as excinfo:
        plot(result=solved_result, frequencies=[250.0])

    message = str(excinfo.value)
    assert "250.0" in message
    assert attribute in message
    # The message must also say what *was* computed, so the caller can fix
    # the call without going back to the network definition. Asserted on the
    # bracketed list rather than the bare number: "50.0" is a substring of
    # "250.0", so the naive check passes on a message that never mentions the
    # available frequencies at all.
    assert "computed for" in message
    assert "[50.0]" in message
    # ... and it must be *this* diagnosis, not the partial-result one, which
    # would also fire here but tells the caller to go looking for a truncated
    # result object instead of a wrong frequency in their own call.
    assert "Incomplete result" not in message


@pytest.mark.parametrize("fn_name, collection, attribute", BAR_PLOTS)
def test_bar_plot_rejects_a_partially_present_frequency(
    solved_result, fn_name, collection, attribute
):
    """
    A frequency on some elements and missing on others is also rejected.

    This is the more insidious half of M1: the bar group would then mix
    measured values with substituted zeros, and nothing in the figure
    distinguishes the two. Such a result can only arise from a truncated
    or hand-edited result object, which is exactly when a loud failure is
    wanted.
    """
    elements = getattr(solved_result, collection)
    victim = elements[0]
    getattr(victim, attribute).pop(50.0)

    with pytest.raises(KeyError) as excinfo:
        getattr(gi, fn_name)(result=solved_result, frequencies=[50.0])

    message = str(excinfo.value)
    assert victim.name in message
    assert "50.0" in message


@pytest.mark.parametrize("fn_name, collection, _attribute", BAR_PLOTS)
def test_a_computed_frequency_still_plots(solved_result, fn_name, collection, _attribute):
    """
    The guard must not reject the frequency the result was computed for.

    One bar per element, all finite, at least one of them non-zero -- a
    guard that raised here, or one that dropped elements, would be caught.
    """
    fig = getattr(gi, fn_name)(result=solved_result, frequencies=[50.0])
    heights = _bar_heights(fig)

    assert len(heights) == len(getattr(solved_result, collection))
    assert all(math.isfinite(h) for h in heights)
    assert max(heights) > 0.0


@pytest.mark.parametrize("fn_name, _collection, _attribute", BAR_PLOTS)
def test_an_int_frequency_still_matches_a_float_result_key(
    solved_result, fn_name, _collection, _attribute
):
    """
    ``50`` and ``50.0`` are the same dict key, and must stay that way.

    The guard is a membership test, so a stricter implementation --
    comparing types, or the repr -- would start rejecting the integer
    frequencies that appear all over the existing notebooks.
    """
    plot = getattr(gi, fn_name)
    as_int = _bar_heights(plot(result=solved_result, frequencies=[50]))
    as_float = _bar_heights(plot(result=solved_result, frequencies=[50.0]))
    assert as_int == as_float


def test_omitting_frequencies_still_plots_the_rms_values(solved_result):
    """
    ``frequencies=None`` is the RMS path and must not go through the guard.

    An over-eager guard that raised on an empty request would break the
    default call in every notebook.
    """
    fig = gi.plot_bus_voltages(result=solved_result)
    heights = _bar_heights(fig)
    assert len(heights) == len(solved_result.buses)
    assert max(heights) > 0.0


def test_a_genuine_zero_volt_bus_still_plots_as_a_zero_bar(solved_result):
    """
    A measured 0 V is data, not a gap.

    The presence check inside the helpers was tightened from ``if value:``
    to ``if value is not None:``. Both spellings yield 0.0 for a zero
    :class:`ComplexNumber` today -- the two branches agree on that value --
    so this test does not distinguish them. What it does pin is the
    *outcome*: should :class:`ComplexNumber` ever gain a ``__bool__``, or
    should the two branches ever diverge, a genuine zero must still reach
    the chart as a zero bar and not disappear.
    """
    zero_bus = solved_result.buses[0]
    zero_bus.uepr_freq[50.0] = ComplexNumber(real=0.0, imag=0.0)

    heights = _bar_heights(gi.plot_bus_voltages(result=solved_result, frequencies=[50.0]))

    assert heights[0] == 0.0
    assert any(h > 0.0 for h in heights[1:]), "the other buses must be unaffected"


# ---------------------------------------------------------------------------
# M2 -- the figure-ownership contract, documented rather than changed
# ---------------------------------------------------------------------------


def test_the_returned_figure_is_registered_with_pyplot(solved_result):
    """
    The docstrings tell the caller to ``plt.close(fig)``; that advice only
    works while the figure really is a pyplot-managed one. This pins the
    contract in both directions: the figure is registered on return, and
    closing it deregisters it.
    """
    plt.close("all")
    fig = gi.plot_bus_voltages(result=solved_result, frequencies=[50.0])

    assert fig.number in plt.get_fignums()

    plt.close(fig)
    assert fig.number not in plt.get_fignums()


def test_repeated_plot_calls_accumulate_figures(solved_result):
    """
    Documents the default ownership, so the contract note stays honest.

    Five plain calls leave five open figures. The ``close=True`` opt-out
    added afterwards did not change this: it is a new keyword-only
    parameter defaulting to ``False``, so a call site written before it
    existed behaves exactly as it did. The counterpart -- the same sweep
    with ``close=True``, leaking nothing -- lives in
    ``tests/test_plot_axes_and_close.py``.
    """
    plt.close("all")
    for _ in range(5):
        gi.plot_bus_voltages(result=solved_result, frequencies=[50.0])
    assert len(plt.get_fignums()) == 5


# ---------------------------------------------------------------------------
# M3 -- a zero baseline yields null, not inf or NaN
# ---------------------------------------------------------------------------


def _finite_stats(column):
    """``(n_rows, n_inf, n_nan, n_null)`` of a float column."""
    values = column.to_list()
    present = [v for v in values if v is not None]
    return (
        len(values),
        sum(1 for v in present if math.isinf(v)),
        sum(1 for v in present if math.isnan(v)),
        len(values) - len(present),
    )


@pytest.fixture
def study_without_fifth_harmonic():
    """
    Route A: a fault whose 250 Hz scaling is 0.0.

    "This fault current contains a fundamental and no 5th harmonic" is an
    ordinary modelling statement. Every element is then exactly 0 at
    250 Hz in *every* scenario, so the whole 250 Hz block used to be
    0/0 -> NaN.
    """
    net = _line_network(
        "P14RouteA",
        [50.0, 250.0],
        source_values={50.0: 1000.0, 250.0: 200.0},
        fault_bus="b2",
        fault_scalings={50.0: 1.0, 250.0: 0.0},
    )
    return run_outage_study(
        net,
        fault="flt",
        scenarios=[
            Outage(name="br12_out", description="cable b1-b2 out", disabled_branches=["br12"])
        ],
    )


@pytest.fixture
def study_with_an_islanded_station():
    """
    Route B: comparing against a scenario that islands a station.

    ``b2`` is 0 V in ``br12_out`` and non-zero in ``base``, so the
    relative column used to read ``inf`` for the single most interesting
    row in the study.
    """
    net = _line_network(
        "P14RouteB",
        [50.0],
        source_values={50.0: 1000.0},
        fault_bus="b1",
        fault_scalings={50.0: 1.0},
    )
    return run_outage_study(
        net,
        fault="flt",
        scenarios=[
            Outage(name="br12_out", description="spur to b2 out", disabled_branches=["br12"]),
            Outage(name="b2_out", description="station b2 out", disabled_buses=["b2"]),
        ],
    )


@pytest.mark.parametrize("comparison", ["compare_buses", "compare_branches"])
def test_zero_in_both_scenarios_is_null_not_nan(study_without_fifth_harmonic, comparison):
    """0/0 is undefined, and ``null`` says so; ``NaN`` looks like a solver failure."""
    table = getattr(study_without_fifth_harmonic, comparison)()
    pct = next(c for c in table.columns if c.startswith("delta_pct_vs_"))

    n_rows, n_inf, n_nan, n_null = _finite_stats(table[pct])

    assert n_rows > 0
    assert n_inf == 0
    assert n_nan == 0
    assert n_null > 0, "the 250 Hz block has a zero baseline and must be null"

    # The 250 Hz rows are exactly the null ones, and nothing else is.
    nulls_at_250 = table.filter(pl.col(pct).is_null())["frequency_Hz"].to_list()
    assert set(nulls_at_250) == {"250"}


@pytest.mark.parametrize("comparison", ["compare_buses", "compare_branches"])
def test_a_zero_reference_with_a_nonzero_value_is_null_not_inf(
    study_without_fifth_harmonic, comparison
):
    """
    The row an engineer most wants to see must not be ``inf``.

    Comparing *against* the outage scenario puts a hard 0 in the
    denominator while the base case carries hundreds of amperes. Before
    the fix that produced ``+inf``, which sorts to the top of every
    "largest relative change" ranking and destroys any aggregation over
    the column.
    """
    table = getattr(study_without_fifth_harmonic, comparison)(against="br12_out")
    pct = next(c for c in table.columns if c.startswith("delta_pct_vs_"))
    delta = next(c for c in table.columns if c.startswith("delta_vs_"))

    _n_rows, n_inf, n_nan, n_null = _finite_stats(table[pct])
    assert n_inf == 0
    assert n_nan == 0
    assert n_null > 0

    # The absolute column keeps the full information: a null percentage is
    # not a missing measurement, it is an undefined ratio.
    nulls = table.filter(pl.col(pct).is_null())
    assert nulls[delta].null_count() == 0
    assert any(abs(v) > 0.0 for v in nulls[delta].to_list())


def test_an_islanded_station_yields_null_and_keeps_its_absolute_delta(
    study_with_an_islanded_station,
):
    """
    Route B, spelled out on the one row it is about.

    ``b2`` is 0 V once its spur is out. Relative to that, "how much higher
    is it in the base case" has no answer -- but "57.9 V higher" does, and
    that number must survive.
    """
    table = study_with_an_islanded_station.compare_buses(against="br12_out")
    pct = next(c for c in table.columns if c.startswith("delta_pct_vs_"))
    delta = next(c for c in table.columns if c.startswith("delta_vs_"))

    _n_rows, n_inf, n_nan, _n_null = _finite_stats(table[pct])
    assert n_inf == 0
    assert n_nan == 0

    b2_base = table.filter(
        (pl.col("bus_name") == "b2")
        & (pl.col("scenario") == "base")
        & (pl.col("frequency_Hz") == "RMS")
    )
    assert len(b2_base) == 1
    assert b2_base[pct][0] is None
    assert b2_base[delta][0] > 0.0


def test_a_nonzero_baseline_still_gets_a_percentage(study_with_an_islanded_station):
    """
    The guard must only fire on a zero denominator.

    ``b0`` carries the source and is far from zero in every scenario, so
    its relative column has to stay populated -- otherwise the "fix"
    would have thrown the feature away.
    """
    table = study_with_an_islanded_station.compare_buses()
    pct = next(c for c in table.columns if c.startswith("delta_pct_vs_"))

    b0 = table.filter((pl.col("bus_name") == "b0") & (pl.col("frequency_Hz") == "RMS"))
    assert len(b0) == len(study_with_an_islanded_station.labels())
    assert b0[pct].null_count() == 0

    # The reference row compares with itself: exactly 0 %, not null.
    base_row = b0.filter(pl.col("scenario") == "base")
    assert base_row[pct][0] == pytest.approx(0.0)


def test_null_percentages_do_not_poison_aggregations(study_without_fifth_harmonic):
    """
    The point of ``null`` over ``NaN``: Polars aggregations skip it.

    With ``NaN`` in the column, ``mean()`` and ``max()`` both returned
    ``NaN`` and the whole summary became unusable.
    """
    table = study_without_fifth_harmonic.compare_buses()
    pct = next(c for c in table.columns if c.startswith("delta_pct_vs_"))

    mean = table[pct].mean()
    largest = table[pct].max()

    assert mean is not None and math.isfinite(mean)
    assert largest is not None and math.isfinite(largest)


# ---------------------------------------------------------------------------
# M4 -- an unresolvable type name names itself
# ---------------------------------------------------------------------------


def test_busdb_with_an_unresolvable_type_raises_a_named_valueerror():
    """
    Was: ``AttributeError: 'NoneType' object has no attribute 'to_pydantic'``.

    That message names neither the bus, nor the type it wanted, nor the
    network -- and it arrives from inside the ORM layer, which reads like
    a groundinsight bug rather than an inconsistent database.
    """
    row = BusDB(network_name="StationA", name="bus7", type_name="DeletedBusType")

    with pytest.raises(ValueError) as excinfo:
        row.to_pydantic()

    message = str(excinfo.value)
    assert "bus7" in message
    assert "DeletedBusType" in message
    assert "StationA" in message


def test_branchdb_with_an_unresolvable_type_raises_a_named_valueerror():
    """The branch side of M4, identical in shape."""
    row = BranchDB(network_name="StationA", name="cable3", type_name="DeletedBranchType")

    with pytest.raises(ValueError) as excinfo:
        row.to_pydantic()

    message = str(excinfo.value)
    assert "cable3" in message
    assert "DeletedBranchType" in message
    assert "StationA" in message


def test_loading_a_network_whose_bus_type_was_deleted_reports_the_bus(tmp_path):
    """
    The realistic route: a shared database someone pruned.

    A bus type row is removed behind groundinsight's back -- the concrete
    scenario the check exists for -- and the load must say which bus and
    which type, not fail deep inside the ORM.
    """
    bus_type, branch_type = _types()
    net = gi.create_network(name="StationA", frequencies=[50.0])
    for bus_name in ("b0", "b1"):
        gi.create_bus(name=bus_name, type=bus_type, network=net)
    gi.create_branch(
        name="br01", type=branch_type, from_bus="b0", to_bus="b1", length=1.0, network=net
    )
    gi.create_source(name="src", bus="b0", values={50.0: 100.0}, network=net)
    gi.create_fault(name="flt", bus="b1", scalings={50.0: 1.0}, network=net)

    path = str(tmp_path / "pruned.db")
    gi.start_dbsession(path)
    try:
        gi.save_network_to_db(net)
    finally:
        # The session is module-global; leaving it bound leaks into every
        # later test that opens a database of its own.
        gi.close_dbsession()

    connection = sqlite3.connect(path)
    connection.execute("DELETE FROM bus_types WHERE name = ?", (bus_type.name,))
    connection.commit()
    connection.close()

    gi.start_dbsession(path)
    try:
        with pytest.raises(ValueError) as excinfo:
            gi.load_network_from_db("StationA")
    finally:
        gi.close_dbsession()

    message = str(excinfo.value)
    assert bus_type.name in message
    assert "b0" in message or "b1" in message


# ---------------------------------------------------------------------------
# M5a -- create_network_assistant validates its branch_length
# ---------------------------------------------------------------------------


def test_assistant_rejects_too_many_lengths():
    """
    ``n`` lengths for ``n`` buses silently dropped the last one.

    Two tests in this repository were written that way, and the networks
    they asserted against were built from ``n-1`` lengths all along.
    """
    bus_type, branch_type = _types()

    with pytest.raises(ValueError) as excinfo:
        create_network_assistant(
            name="TooMany",
            frequencies=[50.0],
            number_buses=5,
            bus_type=bus_type,
            branch_type=branch_type,
            branch_length=[1.0] * 5,
            specific_earth_resistance=100.0,
        )

    message = str(excinfo.value)
    assert "5 entries" in message
    assert "4 branches" in message


def test_assistant_rejects_too_few_lengths():
    """Was a bare ``IndexError: list index out of range`` from the loop body."""
    bus_type, branch_type = _types()

    with pytest.raises(ValueError) as excinfo:
        create_network_assistant(
            name="TooFew",
            frequencies=[50.0],
            number_buses=5,
            bus_type=bus_type,
            branch_type=branch_type,
            branch_length=[1.0] * 2,
            specific_earth_resistance=100.0,
        )

    assert "2 entries" in str(excinfo.value)


@pytest.mark.parametrize("bad", [0, -3])
def test_assistant_rejects_a_non_positive_bus_count(bad):
    """``number_buses=0`` built an empty network; ``-3`` built one too."""
    bus_type, branch_type = _types()

    with pytest.raises(ValueError, match=">= 1"):
        create_network_assistant(
            name="Empty",
            frequencies=[50.0],
            number_buses=bad,
            bus_type=bus_type,
            branch_type=branch_type,
            branch_length=[],
            specific_earth_resistance=100.0,
        )


@pytest.mark.parametrize("bad", [5.0, "5", True])
def test_assistant_rejects_a_non_int_bus_count(bad):
    """
    ``5.0`` reads as five buses but ``range(5.0)`` raises; ``True`` is an
    ``int`` to Python and would build a one-bus network.
    """
    bus_type, branch_type = _types()

    with pytest.raises(ValueError, match="number_buses"):
        create_network_assistant(
            name="NotAnInt",
            frequencies=[50.0],
            number_buses=bad,
            bus_type=bus_type,
            branch_type=branch_type,
            branch_length=[1.0] * 4,
            specific_earth_resistance=100.0,
        )


def test_assistant_rejects_a_scalar_branch_length():
    """
    ``branch_length=1.0`` -- "all branches are 1 km" -- is a natural thing
    to try and used to fail with ``TypeError: 'float' object is not
    subscriptable`` from inside the loop.
    """
    bus_type, branch_type = _types()

    with pytest.raises(ValueError) as excinfo:
        create_network_assistant(
            name="Scalar",
            frequencies=[50.0],
            number_buses=5,
            bus_type=bus_type,
            branch_type=branch_type,
            branch_length=1.0,
            specific_earth_resistance=100.0,
        )

    assert "sequence" in str(excinfo.value)


def test_assistant_still_builds_the_correct_line():
    """
    The guard must not change what a correct call produces.

    ``n`` buses, ``n-1`` branches, and every length landing on the branch
    it belongs to -- in order.
    """
    bus_type, branch_type = _types()
    lengths = [0.5, 1.5, 2.5, 3.5]

    net = create_network_assistant(
        name="Line5",
        frequencies=[50.0],
        number_buses=5,
        bus_type=bus_type,
        branch_type=branch_type,
        branch_length=lengths,
        specific_earth_resistance=100.0,
    )

    assert len(net.buses) == 5
    assert len(net.branches) == 4
    assert [net.branches[f"branch{i}"].length for i in range(1, 5)] == lengths


def test_a_single_bus_network_needs_no_lengths():
    """``number_buses=1`` is the boundary: one bus, zero branches."""
    bus_type, branch_type = _types()

    net = create_network_assistant(
        name="Single",
        frequencies=[50.0],
        number_buses=1,
        bus_type=bus_type,
        branch_type=branch_type,
        branch_length=[],
        specific_earth_resistance=100.0,
    )

    assert len(net.buses) == 1
    assert len(net.branches) == 0


# ---------------------------------------------------------------------------
# M5b -- create_paths refuses a network that cannot be excited
# ---------------------------------------------------------------------------


def _unexcited_network(*, with_source, with_fault):
    """Two buses and a branch, with the source and/or the fault omitted."""
    bus_type, branch_type = _types()
    net = gi.create_network(name="Unexcited", frequencies=[50.0])
    for bus_name in ("b0", "b1"):
        gi.create_bus(name=bus_name, type=bus_type, network=net)
    gi.create_branch(
        name="br01", type=branch_type, from_bus="b0", to_bus="b1", length=1.0, network=net
    )
    if with_source:
        gi.create_source(name="src", bus="b0", values={50.0: 100.0}, network=net)
    if with_fault:
        gi.create_fault(name="flt", bus="b1", scalings={50.0: 1.0}, network=net)
    return net


def test_create_paths_rejects_a_network_without_sources():
    """
    Without a source there is nothing to drive current, and the whole
    calculation used to run through and report 0 V at every bus -- a
    plausible answer to a question that was never posed.
    """
    net = _unexcited_network(with_source=False, with_fault=True)

    with pytest.raises(ValueError, match="no sources"):
        create_paths(network=net)


def test_create_paths_rejects_a_network_without_faults():
    """The mirror image; path enumeration runs over ``sources x faults``."""
    net = _unexcited_network(with_source=True, with_fault=False)

    with pytest.raises(ValueError, match="no faults"):
        create_paths(network=net)


def test_run_fault_inherits_the_guard():
    """
    The guard has to reach the entry point users actually call.

    ``run_fault`` rebuilds the paths itself, so a source-less network must
    fail there too rather than returning a table of zeros.
    """
    net = _unexcited_network(with_source=False, with_fault=True)
    net.set_active_fault("flt")

    with pytest.raises(ValueError, match="no sources"):
        gi.run_fault(net, "flt")


def test_create_paths_still_accepts_a_network_with_no_path_between_endpoints():
    """
    The distinction the guard rests on.

    A source and a fault that exist but are not connected is *not* an
    error -- it is exactly what an outage scenario islanding the fault bus
    produces, and 0 V is then the correct answer. The check is on the
    collections being empty, never on the resulting path count.
    """
    bus_type, branch_type = _types()
    net = gi.create_network(name="Islanded", frequencies=[50.0])
    for bus_name in ("b0", "b1", "b2"):
        gi.create_bus(name=bus_name, type=bus_type, network=net)
    # b2 hangs off b1; b0 carries the source, b2 the fault.
    gi.create_branch(
        name="br01", type=branch_type, from_bus="b0", to_bus="b1", length=1.0, network=net
    )
    gi.create_branch(
        name="br12", type=branch_type, from_bus="b1", to_bus="b2", length=1.0, network=net
    )
    gi.create_source(name="src", bus="b0", values={50.0: 100.0}, network=net)
    gi.create_fault(name="flt", bus="b2", scalings={50.0: 1.0}, network=net)
    net.set_active_fault("flt")

    # Island b2 by taking its only branch out of service.
    net.branches["br12"].active = False
    create_paths(network=net)  # must not raise

    gi.run_fault(net, "flt")
    epr = [bus.uepr for bus in net.results["flt"].buses]
    assert all(value == pytest.approx(0.0) for value in epr)


def test_an_outage_study_that_islands_the_fault_bus_still_runs():
    """
    The same distinction, through the public outage API.

    This is the test that would fail if the guard were ever tightened to
    "no paths found", and it is the reason it was not.
    """
    net = _line_network(
        "P14Island",
        [50.0],
        source_values={50.0: 1000.0},
        fault_bus="b2",
        fault_scalings={50.0: 1.0},
    )
    study = run_outage_study(
        net,
        fault="flt",
        scenarios=[
            Outage(name="br12_out", description="fault bus islanded", disabled_branches=["br12"])
        ],
    )

    assert set(study.labels()) == {"base", "br12_out"}
    islanded = study.bus_results["br12_out"]
    assert islanded["EPR_V"].max() == pytest.approx(0.0)

# tests/test_plot_axes_and_close.py

"""
Tests for the ``ax=`` / ``close=`` extension of :mod:`groundinsight.plotting`.

Background
----------
Audit pass 14 documented, but deliberately did not fix, a figure-ownership
problem: every plotting helper created its own figure, registered it with
``pyplot`` and never let go of it. Two consequences followed.

* A parameter sweep -- twenty soil resistivities, twenty outage scenarios --
  accumulated twenty figures and then triggered matplotlib's
  ``More than 20 figures have been opened`` warning. The caller had to know
  to call ``plt.close(fig)``, which is not discoverable from the call site.
* Comparing two cases side by side was impossible. Every helper insisted on
  a figure of its own, so "base case next to cable-out case, shared y-axis"
  could not be expressed at all.

Both are addressed by two optional, keyword-only parameters:

``ax``
    draw into a caller-supplied :class:`~matplotlib.axes.Axes`;
``close``
    close the created figure before returning it.

The compatibility requirement is absolute: **omitting both must reproduce
the previous behaviour exactly.** Implementing ``ax=`` meant rewriting all
five helpers from the ``pyplot`` state machine (``plt.bar``, ``plt.xticks``,
``plt.title``) to the object-oriented API (``ax.bar``, ``ax.set_xticks``,
``ax.set_title``), which touches every drawing call in the module. That
claim is therefore checked here in two independent ways: by asserting the
default figure properties directly, and -- more sharply -- by asserting that
an ``ax=`` call and a standalone call put *identical* artists on the axis.

What each group proves
----------------------
A. Nothing changed for callers who pass neither parameter.
B. ``ax=`` draws into the given axis, returns the caller's figure, creates
   no figure of its own and leaves the surrounding layout untouched.
C. ``close=`` removes the figure from pyplot's manager without damaging it,
   and a sweep longer than ``figure.max_open_warning`` stays silent.
D. The two combinations that cannot be honoured are rejected loudly, before
   anything is drawn, rather than silently ignored.
E. The absolute contract: what each helper must put on its axis, and that a
   helper-created figure really is laid out. Groups A-D compare one call
   against another, so a change that hits *both* sides is invisible to them;
   the values in group E are written out instead of derived.
"""

import io
import warnings

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt

import pytest

import groundinsight as gi
from groundinsight.models.core_models import BusType, BranchType
from groundinsight.plotting import (
    _DEFAULT_BAR_FIGSIZE,
    _DEFAULT_TRANSIENT_FIGSIZE,
)


# ---------------------------------------------------------------------------
# Fixtures
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


def _solved_network(name="AxNet", rho=100.0):
    """Three buses in a line, source on ``b0``, fault on ``b2``, solved."""
    bus_type, branch_type = _types()
    net = gi.create_network(name=name, frequencies=[50.0, 250.0])
    for bus_name in ("b0", "b1", "b2"):
        gi.create_bus(
            name=bus_name, type=bus_type, network=net, specific_earth_resistance=rho
        )
    gi.create_branch(
        name="br01", type=branch_type, from_bus="b0", to_bus="b1",
        length=2.0, network=net,
    )
    gi.create_branch(
        name="br12", type=branch_type, from_bus="b1", to_bus="b2",
        length=3.0, network=net,
    )
    gi.create_source(
        name="src", bus="b0", values={50.0: 100.0, 250.0: 20.0}, network=net
    )
    gi.create_fault(
        name="flt", bus="b2", scalings={50.0: 1.0, 250.0: 0.4}, network=net
    )
    net.set_active_fault("flt")
    gi.run_fault(net, "flt")
    return net


@pytest.fixture(scope="module")
def result():
    """A solved stationary result at 50 Hz and 250 Hz."""
    return _solved_network().results["flt"]


@pytest.fixture(scope="module")
def other_result():
    """A second result with a different soil resistivity, so EPR differs."""
    return _solved_network(name="AxNetHighRho", rho=500.0).results["flt"]


@pytest.fixture(scope="module")
def transient_result():
    """A short transient run with two observed buses and two branches."""
    net = _solved_network(name="AxNetTransient")
    study = gi.TransientStudy(network=net, fault_name="flt")
    study.set_source_waveform(
        "src",
        gi.waveforms.sinusoidal_with_dc_offset(
            amplitude=1e3,
            frequency_hz=50.0,
            t_on=0.0,
            t_off=0.06,
            dc_amplitude=200.0,
            dc_decay_tau=0.02,
        ),
    )
    study.set_observation(buses=["b1", "b2"], branches=["br01", "br12"])
    return study.solve(t_end=0.08, dt=1e-3, solver="fft")


@pytest.fixture(autouse=True)
def _clean_figures():
    """Start and end every test with an empty pyplot figure registry."""
    plt.close("all")
    yield
    plt.close("all")


# ---------------------------------------------------------------------------
# Call tables -- every helper, driven through one uniform interface
# ---------------------------------------------------------------------------

#: ``(id, callable(result, **kwargs), fixture_name, expected default figsize)``
BAR_HELPERS = [
    ("bus_voltages_rms", gi.plot_bus_voltages, {}),
    ("bus_voltages_freq", gi.plot_bus_voltages, {"frequencies": [50.0, 250.0]}),
    ("branch_currents_rms", gi.plot_branch_currents, {}),
    ("branch_currents_freq", gi.plot_branch_currents, {"frequencies": [50.0]}),
    ("bus_currents_rms", gi.plot_bus_currents, {}),
    ("bus_currents_freq", gi.plot_bus_currents, {"frequencies": [250.0]}),
]

TRANSIENT_HELPERS = [
    ("epr_transient", gi.plot_epr_transient, {}),
    ("branch_current_transient", gi.plot_branch_current_transient, {}),
]

ALL_HELPER_IDS = [entry[0] for entry in BAR_HELPERS + TRANSIENT_HELPERS]


def _call(entry, res, transient, **extra):
    """Invoke one helper from the tables above with ``extra`` kwargs."""
    _id, fn, kwargs = entry
    payload = transient if "transient" in _id else res
    return fn(result=payload, **kwargs, **extra)


def _entry(helper_id):
    for entry in BAR_HELPERS + TRANSIENT_HELPERS:
        if entry[0] == helper_id:
            return entry
    raise KeyError(helper_id)


def _axis_fingerprint(ax):
    """Everything this module draws on an axis, as a comparable value."""
    return {
        "bars": [
            (
                round(p.get_x(), 9),
                round(p.get_width(), 9),
                round(p.get_height(), 9),
            )
            for p in ax.patches
        ],
        "lines": [
            (
                line.get_label(),
                len(line.get_xdata()),
                round(float(sum(line.get_ydata())), 9),
            )
            for line in ax.get_lines()
        ],
        "xlabel": ax.get_xlabel(),
        "ylabel": ax.get_ylabel(),
        "title": ax.get_title(),
        "yscale": ax.get_yscale(),
        "xticks": [round(t, 9) for t in ax.get_xticks()],
        "xticklabels": [
            (t.get_text(), round(t.get_rotation(), 9), t.get_horizontalalignment())
            for t in ax.get_xticklabels()
        ],
        "legend": (
            None
            if ax.get_legend() is None
            else (
                ax.get_legend().get_title().get_text(),
                [t.get_text() for t in ax.get_legend().get_texts()],
            )
        ),
    }


def _positions(fig):
    return [tuple(round(v, 9) for v in ax.get_position().bounds) for ax in fig.axes]


# ---------------------------------------------------------------------------
# A -- the default call is untouched
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("helper_id", ALL_HELPER_IDS)
def test_default_call_creates_one_registered_open_figure(
    result, transient_result, helper_id
):
    """
    Neither parameter given: one new figure, one axis, still open.

    This is the compatibility floor. Every notebook in the repository calls
    the helpers this way and expects the figure to survive the call so the
    cell can display it.
    """
    fig = _call(_entry(helper_id), result, transient_result)

    assert len(fig.axes) == 1
    assert fig.number in plt.get_fignums()
    assert len(plt.get_fignums()) == 1


@pytest.mark.parametrize("helper_id", ALL_HELPER_IDS)
def test_default_figsize_is_the_documented_one(result, transient_result, helper_id):
    """
    ``figsize`` defaults to ``None`` now; ``None`` must still mean the
    helper's own default, not matplotlib's rcParam default.

    The parameter used to carry its literal default in the signature. It was
    replaced by ``None`` so that "the caller asked for a size" is
    distinguishable from "the caller said nothing" -- which is what makes
    the ``ax=``/``figsize=`` conflict detectable at all. That change must
    not leak into the produced figure.
    """
    expected = (
        _DEFAULT_TRANSIENT_FIGSIZE
        if "transient" in helper_id
        else _DEFAULT_BAR_FIGSIZE
    )
    fig = _call(_entry(helper_id), result, transient_result)
    assert tuple(fig.get_size_inches()) == expected

    plt.close("all")
    explicit_none = _call(_entry(helper_id), result, transient_result, figsize=None)
    assert tuple(explicit_none.get_size_inches()) == expected


def test_the_module_defaults_are_the_historical_sizes():
    """
    The two default sizes, as literals.

    The test above compares against the module constants, so it would still
    pass if both the constant and the figure changed together -- it proves
    consistency, not correctness. These are the sizes the helpers produced
    before the parameter existed, and every notebook in the repository was
    laid out around them.
    """
    assert _DEFAULT_BAR_FIGSIZE == (12, 6)
    assert _DEFAULT_TRANSIENT_FIGSIZE == (10, 5)


@pytest.mark.parametrize("helper_id", ALL_HELPER_IDS)
def test_an_explicit_figsize_is_still_honoured(result, transient_result, helper_id):
    """A size the caller *can* be given is still applied."""
    fig = _call(_entry(helper_id), result, transient_result, figsize=(7.5, 3.25))
    assert tuple(fig.get_size_inches()) == (7.5, 3.25)


@pytest.mark.parametrize("bad", [(0, 0), (0.0, 6.0), (-12.0, 6.0), (12.0, -6.0)])
def test_a_degenerate_figsize_is_rejected_rather_than_replaced(result, bad):
    """
    A non-positive size must fail at the call that made it.

    Matplotlib takes ``figsize=(0, 0)`` without complaint and only raises
    when the figure is finally drawn or saved, so the traceback lands in
    ``savefig`` -- far from the call responsible. Substituting the default
    instead would be worse still: the caller would get a plot at a size they
    did not ask for and no indication that their argument was discarded.
    """
    with pytest.raises(ValueError) as excinfo:
        gi.plot_bus_voltages(result=result, figsize=bad)

    assert "figsize" in str(excinfo.value)
    assert "positive" in str(excinfo.value)
    assert plt.get_fignums() == [], "the rejected call left a figure behind"


@pytest.mark.parametrize("bad", [(), (12.0,), (12.0, 6.0, 3.0), 12.0, "12x6"])
def test_a_malformed_figsize_is_named_in_the_error(result, bad):
    """
    A ``figsize`` that is not a pair at all must still name the parameter.

    ``()`` is the one falsy value ``figsize`` can take -- a tuple with
    anything in it is truthy, so ``(0, 0)`` does *not* exercise the
    difference between ``if figsize is None`` and ``if not figsize``. Only
    the empty tuple does, and under a truth test it would be silently
    replaced by the default size, which is the same quietly-wrong artefact
    the degenerate case above rejects.

    Left to itself the unpacking raises ``ValueError: not enough values to
    unpack (expected 2, got 0)``, which names neither ``figsize`` nor the
    helper that was called -- a hard message to act on several notebook
    cells below the mistake.
    """
    with pytest.raises(ValueError) as excinfo:
        gi.plot_bus_voltages(result=result, figsize=bad)

    message = str(excinfo.value)
    assert "figsize" in message
    assert "(width, height)" in message
    assert "unpack" not in message, "matplotlib's own message leaked through"
    assert plt.get_fignums() == [], "the rejected call left a figure behind"


def test_positional_arguments_still_work(result):
    """
    ``ax`` and ``close`` were appended behind a ``*``, so the historical
    positional signature ``(result, frequencies, figsize, title, yscale)``
    is unaffected.
    """
    fig = gi.plot_bus_voltages(result, [50.0], (7.0, 3.0), "positional", "log")
    ax = fig.axes[0]
    assert ax.get_title() == "positional"
    assert ax.get_yscale() == "log"
    assert tuple(fig.get_size_inches()) == (7.0, 3.0)


def test_ax_and_close_are_keyword_only(result):
    """
    Passing an axis positionally must fail rather than land in ``show``.

    Without the ``*`` in the signature, ``plot_bus_voltages(res, None,
    None, "t", "linear", False, ax)`` would bind ``ax`` to nothing at all
    on the transient helpers and to the seventh positional slot here --
    a silent difference between the five helpers. The marker removes the
    question.
    """
    fig, ax = plt.subplots()
    with pytest.raises(TypeError):
        gi.plot_bus_voltages(result, None, None, "t", "linear", False, ax)


# ---------------------------------------------------------------------------
# B -- ax= draws into the caller's axis
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("helper_id", ALL_HELPER_IDS)
def test_ax_returns_the_callers_figure_and_creates_no_other(
    result, transient_result, helper_id
):
    """
    The helper must hand back ``ax.figure`` itself -- identity, not a copy --
    and must not have created a figure of its own on the side.
    """
    fig, ax = plt.subplots()
    assert plt.get_fignums() == [fig.number]

    returned = _call(_entry(helper_id), result, transient_result, ax=ax)

    assert returned is fig
    assert plt.get_fignums() == [fig.number], "a second figure was created"
    assert len(fig.axes) == 1


@pytest.mark.parametrize("helper_id", ALL_HELPER_IDS)
def test_ax_draws_exactly_what_a_standalone_call_draws(
    result, transient_result, helper_id
):
    """
    The sharp form of the compatibility claim.

    Rewriting five helpers from ``plt.*`` to ``ax.*`` touched every drawing
    call in the module. Rather than trusting that, this compares the two
    axes artist by artist: bar geometry, line data, both axis labels, the
    title, the y-scale, the tick positions, the tick label text *and* their
    rotation and alignment, and the legend title plus its entries.

    The decoy is what makes a leftover ``plt.`` call visible. ``plt.bar``
    and friends draw on ``plt.gca()``, which -- with a single figure holding
    a single axis -- happens to *be* the axis under test, so the comparison
    alone would pass. Creating a second axis afterwards moves ``gca()``
    away, and a stray state-machine call then lands on the decoy: the target
    comes back empty and the decoy comes back holding artists nobody asked
    for. The decoy is a separate figure, not a second panel, so the axis
    under test keeps exactly the geometry of the standalone case and its
    automatic tick locations stay comparable.
    """
    standalone = _call(_entry(helper_id), result, transient_result)
    reference = _axis_fingerprint(standalone.axes[0])
    plt.close("all")

    fig, ax = plt.subplots(figsize=standalone.get_size_inches())
    decoy_fig, decoy_ax = plt.subplots()
    assert plt.gca() is decoy_ax, "the decoy must be the current axis"

    _call(_entry(helper_id), result, transient_result, ax=ax)

    assert _axis_fingerprint(ax) == reference
    assert len(decoy_ax.patches) == 0, "a plt.* call drew on the current axis"
    assert len(decoy_ax.get_lines()) == 0, "a plt.* call drew on the current axis"
    assert decoy_ax.get_title() == "" and decoy_ax.get_xlabel() == ""


def test_ax_leaves_the_surrounding_layout_untouched(result):
    """
    ``tight_layout`` must not run on a figure the helper did not create.

    A 2x2 comparison grid is usually laid out deliberately -- shared axes,
    a suptitle, a colour bar. Re-flowing it from inside a plotting helper
    would move panels the call never touched. The second half of the test
    is the positive control: ``tight_layout`` demonstrably *does* move
    these axes, so the first assertion is not vacuously true.
    """
    fig, axes = plt.subplots(2, 2, figsize=(12, 6))
    fig.canvas.draw()
    before = _positions(fig)

    gi.plot_bus_voltages(result=result, ax=axes[0][0], title="base")
    gi.plot_bus_currents(result=result, ax=axes[0][1], title="bus currents")
    fig.canvas.draw()

    assert _positions(fig) == before

    fig.tight_layout()
    fig.canvas.draw()
    assert _positions(fig) != before, "positive control: tight_layout must move them"


def test_a_comparison_grid_keeps_the_panels_apart(result, other_result):
    """
    Two results into two panels of one figure -- the use case the parameter
    exists for. Each panel must carry its own data, and only its own.
    """
    fig, (left, right) = plt.subplots(1, 2, figsize=(16, 5), sharey=True)

    gi.plot_bus_voltages(result=result, ax=left, title="rho = 100")
    assert len(right.patches) == 0, "the second panel must still be empty"

    gi.plot_bus_voltages(result=other_result, ax=right, title="rho = 500")

    left_heights = [p.get_height() for p in left.patches]
    right_heights = [p.get_height() for p in right.patches]

    assert len(left_heights) == len(result.buses)
    assert len(right_heights) == len(other_result.buses)
    assert left.get_title() == "rho = 100"
    assert right.get_title() == "rho = 500"
    # A five-fold soil resistivity must move the earth potential rise, so
    # the two panels cannot be the same drawing twice.
    assert left_heights != right_heights
    assert plt.get_fignums() == [fig.number]


def test_the_transient_helpers_stack_in_one_figure(transient_result):
    """EPR over time above, shield current below, sharing the time axis."""
    fig, (top, bottom) = plt.subplots(2, 1, figsize=(10, 8), sharex=True)

    gi.plot_epr_transient(result=transient_result, ax=top)
    gi.plot_branch_current_transient(result=transient_result, ax=bottom)

    assert len(top.get_lines()) == len(transient_result.epr_t)
    assert len(bottom.get_lines()) == len(transient_result.i_branch_t)
    assert top.get_ylabel() == "EPR / V"
    assert bottom.get_ylabel() == "current / A"
    assert plt.get_fignums() == [fig.number]


def test_ax_still_validates_the_requested_frequencies(result):
    """
    The pass-14 frequency guard is not bypassed by the new parameter.

    ``ax=`` changes where a plot goes, never what counts as plottable.
    """
    fig, ax = plt.subplots()
    with pytest.raises(KeyError) as excinfo:
        gi.plot_bus_voltages(result=result, frequencies=[1234.0], ax=ax)

    assert "1234.0" in str(excinfo.value)
    assert len(ax.patches) == 0, "nothing may be drawn on a rejected call"


def test_ax_still_validates_the_requested_transient_buses(transient_result):
    """The same for the transient helper's observation-point check."""
    fig, ax = plt.subplots()
    with pytest.raises(ValueError) as excinfo:
        gi.plot_epr_transient(result=transient_result, buses=["nope"], ax=ax)

    assert "nope" in str(excinfo.value)
    assert len(ax.get_lines()) == 0


# ---------------------------------------------------------------------------
# C -- close= releases the figure without damaging it
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("helper_id", ALL_HELPER_IDS)
def test_close_deregisters_the_figure(result, transient_result, helper_id):
    """``close=True`` empties the pyplot registry; ``close=False`` does not."""
    closed = _call(_entry(helper_id), result, transient_result, close=True)
    assert closed.number not in plt.get_fignums()
    assert plt.get_fignums() == []

    still_open = _call(_entry(helper_id), result, transient_result, close=False)
    assert still_open.number in plt.get_fignums()


@pytest.mark.parametrize("helper_id", ALL_HELPER_IDS)
def test_a_closed_figure_is_still_a_complete_figure(
    result, transient_result, helper_id
):
    """
    Closing must release the figure, not destroy it.

    The whole point of returning the figure from a sweep is to save or
    inspect it afterwards. ``plt.close`` only detaches the figure from
    pyplot's manager, so rendering it to a file still has to work -- this
    asserts it produces real PNG bytes, not an empty buffer.
    """
    fig = _call(_entry(helper_id), result, transient_result, close=True)

    assert len(fig.axes) == 1
    artists = fig.axes[0].patches or fig.axes[0].get_lines()
    assert len(artists) > 0

    buffer = io.BytesIO()
    fig.savefig(buffer, format="png")
    assert buffer.getvalue().startswith(b"\x89PNG")
    assert len(buffer.getvalue()) > 1000


@pytest.mark.parametrize("helper_id", ALL_HELPER_IDS)
def test_close_does_not_change_what_was_drawn(result, transient_result, helper_id):
    """A closed figure carries the same artists as an open one."""
    open_fig = _call(_entry(helper_id), result, transient_result)
    reference = _axis_fingerprint(open_fig.axes[0])
    plt.close("all")

    closed_fig = _call(_entry(helper_id), result, transient_result, close=True)
    assert _axis_fingerprint(closed_fig.axes[0]) == reference


def test_a_long_sweep_stays_silent_and_leaks_nothing(result):
    """
    The motivating case: a parameter sweep longer than
    ``figure.max_open_warning``.

    Without ``close=True`` matplotlib emits
    ``RuntimeWarning: More than 20 figures have been opened``; the sweep
    below runs five iterations past that threshold and must stay silent,
    with an empty figure registry throughout.
    """
    limit = int(matplotlib.rcParams["figure.max_open_warning"])
    figures = []

    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        for index in range(limit + 5):
            figures.append(
                gi.plot_bus_voltages(
                    result=result, title=f"sweep {index}", close=True
                )
            )
            assert plt.get_fignums() == []

    messages = [str(w.message) for w in caught]
    assert not any("figures have been opened" in m for m in messages), messages
    assert len(figures) == limit + 5
    # Every figure is still individually usable after the sweep.
    assert all(len(fig.axes) == 1 for fig in figures)


def test_the_same_sweep_without_close_still_accumulates(result):
    """
    Positive control for the test above: the leak is real, and ``close=``
    is what removes it. Without the parameter the registry grows one figure
    per iteration -- which is exactly the pass-14 behaviour, unchanged.
    """
    for index in range(5):
        gi.plot_bus_voltages(result=result, title=f"sweep {index}")
    assert len(plt.get_fignums()) == 5


def test_show_runs_before_close(result, monkeypatch):
    """
    Order matters: closing first would leave ``plt.show()`` nothing to
    display, and the caller would see an empty window instead of a plot.

    ``plt.show`` is replaced by a probe that records the figure registry at
    the moment it is called.
    """
    seen = {}

    def _probe():
        seen["fignums"] = list(plt.get_fignums())

    monkeypatch.setattr(plt, "show", _probe)

    fig = gi.plot_bus_voltages(result=result, show=True, close=True)

    assert seen["fignums"] == [fig.number], "the figure was closed before show()"
    assert plt.get_fignums() == [], "and closed afterwards"


# ---------------------------------------------------------------------------
# D -- the two combinations that cannot be honoured are rejected
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("helper_id", ALL_HELPER_IDS)
def test_ax_with_figsize_is_rejected(result, transient_result, helper_id):
    """
    A size cannot be applied to a figure the caller already owns.

    Silently ignoring ``figsize`` would return a figure at a size nobody
    asked for -- and in a comparison grid the caller would most likely
    blame their own ``plt.subplots`` call. The message therefore names the
    rejected value and the way to actually resize.
    """
    fig, ax = plt.subplots()
    with pytest.raises(ValueError) as excinfo:
        _call(
            _entry(helper_id), result, transient_result, ax=ax, figsize=(4.0, 4.0)
        )

    message = str(excinfo.value)
    assert "figsize" in message
    assert "(4.0, 4.0)" in message
    assert "set_size_inches" in message
    assert len(ax.patches) == 0 and len(ax.get_lines()) == 0
    assert plt.get_fignums() == [fig.number]


@pytest.mark.parametrize("helper_id", ALL_HELPER_IDS)
def test_ax_with_close_is_rejected(result, transient_result, helper_id):
    """
    ``close=True`` releases a figure this call created. With ``ax=`` the
    figure belongs to the caller and may hold other panels, so honouring
    the request would destroy work the helper never touched.
    """
    fig, ax = plt.subplots()
    with pytest.raises(ValueError) as excinfo:
        _call(_entry(helper_id), result, transient_result, ax=ax, close=True)

    message = str(excinfo.value)
    assert "close=True" in message
    assert "ax=" in message
    assert plt.get_fignums() == [fig.number], "the caller's figure survived"
    assert len(ax.patches) == 0 and len(ax.get_lines()) == 0


@pytest.mark.parametrize("bad", [(0.0, 0.0), ()])
def test_ax_with_an_unusable_figsize_still_reports_the_combination(result, bad):
    """
    The conflict is "a size was given at all", not "a usable size was given".

    The guard must test ``figsize is not None``. Under a truth test the empty
    tuple -- the only falsy value ``figsize`` can take -- would slip past and
    be silently ignored, and the caller would never learn that ``figsize=``
    does nothing next to ``ax=``. The combination message is also the more
    useful one here: with ``ax=`` no size can be applied at all, degenerate
    or not, so complaining about the *value* would send the caller off to
    fix the wrong thing.
    """
    fig, ax = plt.subplots()
    with pytest.raises(ValueError) as excinfo:
        gi.plot_bus_voltages(result=result, ax=ax, figsize=bad)

    assert "cannot be combined with ax=" in str(excinfo.value)
    assert len(ax.patches) == 0
    assert plt.get_fignums() == [fig.number]


def test_close_false_with_ax_is_fine(result):
    """
    Only ``close=True`` conflicts. The default value must not turn the
    ordinary ``ax=`` call into an error.
    """
    fig, ax = plt.subplots()
    returned = gi.plot_bus_voltages(result=result, ax=ax, close=False)
    assert returned is fig
    assert len(ax.patches) == len(result.buses)


def test_a_rejected_combination_leaks_no_figure(result):
    """
    The guard runs before any figure is created, so a rejected call leaves
    the registry exactly as it found it -- an exception in a loop must not
    be a second way to accumulate figures.
    """
    fig, ax = plt.subplots()
    for _ in range(10):
        with pytest.raises(ValueError):
            gi.plot_bus_voltages(result=result, ax=ax, figsize=(3.0, 3.0))
        with pytest.raises(ValueError):
            gi.plot_bus_voltages(result=result, ax=ax, close=True)

    assert plt.get_fignums() == [fig.number]


# ---------------------------------------------------------------------------
# E -- the absolute contract, not a comparison against another call
# ---------------------------------------------------------------------------

#: Everything each helper must put on its axis, written out rather than
#: derived from a second call.
#:
#: Groups A-D above always compare one call against another -- ``ax=`` against
#: standalone, ``close=True`` against ``close=False``. A change that hits both
#: sides is therefore invisible to them: dropping ``_rotate_xticklabels``,
#: giving the RMS legend the ``"Frequency"`` title it must not have, turning
#: ``grid(True, axis="y")`` into a full grid, or silently swallowing the
#: y-scale would leave both sides wrong in the same way and pass. The values
#: below were read off a fingerprint of the module as it stood *before* the
#: object-oriented rewrite, so they pin the historical behaviour from outside
#: the implementation.
#:
#: ``legend_title`` is deliberately inconsistent between helpers: the RMS
#: branches of the two current helpers call a bare ``ax.legend()`` and end up
#: with an empty title, while ``plot_bus_voltages`` passes
#: ``title="Frequency"`` in *both* branches -- so its RMS plot carries a
#: "Frequency" legend over a single "RMS" entry. That is pre-existing, it is
#: cosmetic, and changing it would be a silent visual break for every
#: notebook; it is recorded here so it cannot change by accident.
AXIS_CONTRACT = {
    "bus_voltages_rms": {
        "xlabel": "Bus Name",
        "ylabel": "UEPR (V)",
        "title": "UEPR vs Bus Name",
        "legend_title": "Frequency",
        "legend_entries": ["RMS"],
        "rotated": True,
        "grid": (False, True, 1.0),
    },
    "bus_voltages_freq": {
        "xlabel": "Bus Name",
        "ylabel": "UEPR (V)",
        "title": "UEPR vs Bus Name",
        "legend_title": "Frequency",
        "legend_entries": ["50.0 Hz", "250.0 Hz"],
        "rotated": True,
        "grid": (False, True, 1.0),
    },
    "branch_currents_rms": {
        "xlabel": "Branch Name",
        "ylabel": "Current RMS (A)",
        "title": "Branch Currents",
        "legend_title": "",
        "legend_entries": ["RMS"],
        "rotated": True,
        "grid": (False, True, 1.0),
    },
    "branch_currents_freq": {
        "xlabel": "Branch Name",
        "ylabel": "Current (A)",
        "title": "Branch Currents",
        "legend_title": "Frequency",
        "legend_entries": ["50.0 Hz"],
        "rotated": True,
        "grid": (False, True, 1.0),
    },
    "bus_currents_rms": {
        "xlabel": "Bus Name",
        "ylabel": "Current RMS (A)",
        "title": "Bus Currents",
        "legend_title": "",
        "legend_entries": ["RMS"],
        "rotated": True,
        "grid": (False, True, 1.0),
    },
    "bus_currents_freq": {
        "xlabel": "Bus Name",
        "ylabel": "Current (A)",
        "title": "Bus Currents",
        "legend_title": "Frequency",
        "legend_entries": ["250.0 Hz"],
        "rotated": True,
        "grid": (False, True, 1.0),
    },
    "epr_transient": {
        "xlabel": "time / s",
        "ylabel": "EPR / V",
        "title": "EPR over time",
        "legend_title": "Bus",
        "legend_entries": ["b1", "b2"],
        "rotated": False,
        "grid": (True, True, 0.3),
    },
    "branch_current_transient": {
        "xlabel": "time / s",
        "ylabel": "current / A",
        "title": "Branch current over time",
        "legend_title": "Branch",
        "legend_entries": ["br01", "br12"],
        "rotated": False,
        "grid": (True, True, 0.3),
    },
}


def _grid_state(ax):
    """``(x visible, y visible, y alpha)`` read from the public gridline API."""
    x_lines = ax.xaxis.get_gridlines()
    y_lines = ax.yaxis.get_gridlines()
    assert x_lines and y_lines, "no gridlines to inspect"
    x_visible = all(line.get_visible() for line in x_lines)
    y_visible = all(line.get_visible() for line in y_lines)
    alphas = {line.get_alpha() for line in y_lines}
    assert len(alphas) == 1, f"inconsistent grid alpha: {alphas}"
    return x_visible, y_visible, alphas.pop()


@pytest.mark.parametrize("helper_id", ALL_HELPER_IDS)
def test_the_axis_contract_is_the_documented_one(
    result, transient_result, helper_id
):
    """
    Every label, the title, the legend and the grid, against fixed values.

    This is the test that notices when the rewrite changed *both* the
    standalone and the ``ax=`` path in the same direction -- the one failure
    mode the comparison tests structurally cannot see.
    """
    contract = AXIS_CONTRACT[helper_id]
    fig = _call(_entry(helper_id), result, transient_result)
    ax = fig.axes[0]

    assert ax.get_xlabel() == contract["xlabel"]
    assert ax.get_ylabel() == contract["ylabel"]
    assert ax.get_title() == contract["title"]

    legend = ax.get_legend()
    assert legend is not None, "every helper draws a legend"
    assert legend.get_title().get_text() == contract["legend_title"]
    assert [t.get_text() for t in legend.get_texts()] == contract[
        "legend_entries"
    ]

    labels = ax.get_xticklabels()
    assert labels, "there must be tick labels to check"
    if contract["rotated"]:
        # ``_rotate_xticklabels`` replaces ``plt.xticks(rotation=45,
        # ha="right")``. Bus and branch names are long enough to overlap
        # without it, which is why the original code rotated them.
        assert all(round(t.get_rotation(), 9) == 45.0 for t in labels)
        assert all(t.get_horizontalalignment() == "right" for t in labels)
    else:
        # The transient helpers plot time on a numeric axis and never
        # rotated anything.
        assert all(round(t.get_rotation(), 9) == 0.0 for t in labels)
        assert all(t.get_horizontalalignment() == "center" for t in labels)

    assert _grid_state(ax) == contract["grid"]


@pytest.mark.parametrize("helper_id", ALL_HELPER_IDS)
def test_a_created_figure_is_laid_out_tightly(result, transient_result, helper_id):
    """
    ``tight_layout`` runs on figures the helper created.

    Its counterpart -- ``tight_layout`` must *not* run on a caller-supplied
    figure -- is asserted in group B. Without this test the implementation
    could satisfy that one by never laying anything out at all, and rotated
    tick labels would be clipped off the bottom of every default plot.

    Two assertions, because "the bounds differ from the default" alone would
    also pass if some unrelated call had nudged the axis: the second
    ``tight_layout`` is a fixed point, which is only true if the layout is
    already the tight one.
    """
    expected_size = (
        _DEFAULT_TRANSIENT_FIGSIZE
        if "transient" in helper_id
        else _DEFAULT_BAR_FIGSIZE
    )

    fig = _call(_entry(helper_id), result, transient_result)
    fig.canvas.draw()
    laid_out = _positions(fig)

    untouched, _ = plt.subplots(figsize=expected_size)
    untouched.canvas.draw()
    assert laid_out != _positions(untouched), "tight_layout did not run"

    fig.tight_layout()
    fig.canvas.draw()
    assert _positions(fig) == laid_out, "the layout was not already the tight one"


def test_close_only_closes_the_figure_it_created(result):
    """
    ``close=True`` must reach for ``plt.close(fig)``, never ``plt.close("all")``.

    The difference only shows when the caller has other figures open -- which
    is precisely the sweep-plus-summary-plot case the parameter is for. A
    helper that closed everything would silently discard the caller's own
    work, and every other test in this file would still pass because they all
    start from an empty registry.
    """
    keeper, keeper_ax = plt.subplots()
    keeper_ax.plot([0, 1], [0, 1], label="the caller's own plot")
    second, _ = plt.subplots()
    assert plt.get_fignums() == [keeper.number, second.number]

    closed = gi.plot_bus_voltages(result=result, close=True)

    assert closed.number not in plt.get_fignums()
    assert plt.get_fignums() == [keeper.number, second.number], (
        "close=True closed figures it did not create"
    )
    assert len(keeper_ax.get_lines()) == 1


def test_grouped_bar_geometry_is_unchanged(result):
    """
    The grouped-bar arithmetic, pinned to literal positions.

    ``bar_width = 0.8 / n``, one offset group per frequency, tick marks in the
    middle of each group -- this survived the move from ``plt.bar`` /
    ``plt.xticks(positions, names)`` to ``ax.bar`` / ``ax.set_xticks`` +
    ``ax.set_xticklabels``, which is the pair of calls where an off-by-one
    would be easiest to introduce and hardest to see. The numbers below are
    from a fingerprint of the pre-rewrite implementation.
    """
    names = [bus.name for bus in result.buses]
    assert names == ["b0", "b1", "b2"], "the fixture this test is pinned to"

    fig = gi.plot_bus_voltages(result=result, frequencies=[50.0, 250.0])
    ax = fig.axes[0]

    assert [round(p.get_width(), 9) for p in ax.patches] == [0.4] * 6
    assert [round(p.get_x(), 9) for p in ax.patches] == [
        -0.2, 0.8, 1.8,  # 50 Hz group, left of each tick
        0.2, 1.2, 2.2,   # 250 Hz group, right of each tick
    ]
    assert [round(t, 9) for t in ax.get_xticks()] == [0.2, 1.2, 2.2]
    assert [t.get_text() for t in ax.get_xticklabels()] == names

    # Bar heights are the magnitudes of the complex per-frequency EPR, in
    # frequency-major order. Checked against the result rather than against
    # literals so this stays a plotting test, not a physics regression.
    expected = [
        abs(complex(bus.uepr_freq[freq].real, bus.uepr_freq[freq].imag))
        for freq in (50.0, 250.0)
        for bus in result.buses
    ]
    assert [p.get_height() for p in ax.patches] == pytest.approx(expected)


def test_single_series_bar_geometry_is_unchanged(result):
    """
    The RMS branch draws one full-width bar per element at integer positions.

    ``ax.bar(names, values)`` with categorical labels is a different code path
    from the grouped case above -- matplotlib places the ticks itself -- so it
    gets its own pin.
    """
    fig = gi.plot_bus_voltages(result=result)
    ax = fig.axes[0]

    assert [round(p.get_width(), 9) for p in ax.patches] == [0.8] * 3
    assert [round(p.get_x(), 9) for p in ax.patches] == [-0.4, 0.6, 1.6]
    assert [round(t, 9) for t in ax.get_xticks()] == [0.0, 1.0, 2.0]
    assert [t.get_text() for t in ax.get_xticklabels()] == ["b0", "b1", "b2"]
    assert [p.get_height() for p in ax.patches] == pytest.approx(
        [bus.uepr for bus in result.buses]
    )


@pytest.mark.parametrize("yscale", ["linear", "log"])
def test_the_requested_yscale_reaches_the_axis(result, yscale):
    """
    ``plt.yscale`` became ``ax.set_yscale``; a dropped call would leave every
    logarithmic plot silently linear, which on an EPR bar chart spanning
    decades looks plausible and is wrong.
    """
    fig = gi.plot_bus_voltages(result=result, yscale=yscale)
    assert fig.axes[0].get_yscale() == yscale

    fig, ax = plt.subplots()
    gi.plot_bus_currents(result=result, yscale=yscale, ax=ax)
    assert ax.get_yscale() == yscale

# plotting.py

"""
Plotting Module.

This module provides functions for visualizing the results of electrical network calculations,
including UEPR (Earth Potential Rise) for buses and branch currents. It utilizes Matplotlib
to generate plots that can display both frequency-dependent and RMS (Root Mean Square)
values for various electrical parameters. These visualizations aid in analyzing the
performance and behavior of the electrical network under different fault conditions.

Every helper accepts two optional arguments that control *where* the plot is
drawn and *who owns* the resulting figure:

``ax``
    Draw into an existing :class:`matplotlib.axes.Axes` instead of creating a
    figure. This is what makes multi-panel comparisons possible -- one panel
    per outage scenario, per soil resistivity, per fault location -- and
    follows the same convention as ``pandas.DataFrame.plot(ax=...)``.

``close``
    Close the freshly created figure before returning it. The figure object
    remains fully usable (``fig.savefig(...)`` still works); it is merely no
    longer held by ``pyplot``'s figure manager, which is what a long
    parameter sweep needs in order not to accumulate figures until
    matplotlib warns at twenty.

Omitting both reproduces the historical behaviour exactly: a new figure of
the helper's default size, registered with ``pyplot`` and left open.
"""

import matplotlib.pyplot as plt
from typing import Iterable, List, Dict, Optional, Sequence, Tuple, TYPE_CHECKING
from .models.core_models import Result, ComplexNumber

if TYPE_CHECKING:
    from .simulation.transient import ResultTransient


#: Default figure size of the bar-plot helpers.
_DEFAULT_BAR_FIGSIZE = (12, 6)

#: Default figure size of the transient time-series helpers.
_DEFAULT_TRANSIENT_FIGSIZE = (10, 5)


def _prepare_axes(
    ax: Optional["plt.Axes"],
    figsize: Optional[Tuple[float, float]],
    close: bool,
    default_figsize: Tuple[float, float],
) -> Tuple["plt.Figure", "plt.Axes", bool]:
    """
    Resolve the ``(figure, axes)`` pair a plotting helper draws into.

    Parameters
    ----------
    ax : matplotlib.axes.Axes or None
        An existing axis supplied by the caller, or ``None`` to create a
        new figure.
    figsize : tuple of (float, float) or None
        Requested figure size. ``None`` means "use ``default_figsize``".
    close : bool
        Whether the caller asked for the figure to be closed on return.
    default_figsize : tuple of (float, float)
        Size used when ``figsize`` is ``None`` and a new figure is created.

    Returns
    -------
    fig : matplotlib.figure.Figure
        The figure to return to the caller.
    ax : matplotlib.axes.Axes
        The axis to draw into.
    owns_figure : bool
        ``True`` when this call created the figure and may therefore lay it
        out and close it; ``False`` when the figure belongs to the caller.

    Raises
    ------
    ValueError
        If ``ax`` is combined with ``figsize`` or with ``close=True``, or if
        ``figsize`` is not a pair of numbers that are positive in both
        dimensions.

    Notes
    -----
    Both combinations are rejected rather than silently ignored. ``figsize``
    with an existing axis cannot be honoured -- the figure already exists
    and may hold other panels -- and a plot that comes back at a size the
    caller did not ask for is exactly the kind of quietly wrong artefact
    this module tries not to produce. ``close=True`` with an existing axis
    is worse: it would close a figure the helper did not create, taking
    every sibling panel with it.

    The same reasoning applies to a degenerate size. Matplotlib accepts
    ``figsize=(0, 0)`` when the figure is created and only fails much later,
    when it is drawn or saved, so the traceback points at ``savefig`` rather
    than at the call that caused it. It is rejected here instead. A
    ``figsize`` that is not a pair of numbers at all -- ``()``, a scalar, a
    three-tuple -- is reported the same way, because the exception it would
    otherwise raise (``not enough values to unpack``) names neither the
    parameter nor the caller.
    """
    if ax is None:
        if figsize is None:
            figsize = default_figsize
        else:
            try:
                width, height = figsize
                degenerate = not (width > 0 and height > 0)
            except (TypeError, ValueError):
                raise ValueError(
                    f"figsize={figsize!r} must be a (width, height) pair of "
                    "positive numbers, in inches."
                ) from None
            if degenerate:
                raise ValueError(
                    f"figsize={figsize!r} must be positive in both "
                    "dimensions. Matplotlib accepts a zero or negative "
                    "size here and only fails later, when the figure is "
                    "drawn or saved."
                )
        fig, ax = plt.subplots(figsize=figsize)
        return fig, ax, True

    if figsize is not None:
        raise ValueError(
            f"figsize={figsize!r} cannot be combined with ax=: the figure "
            "already exists and its size belongs to the caller. Size it "
            "yourself with ax.figure.set_size_inches(...), or pass "
            "figsize= to plt.subplots(...) when creating the grid."
        )
    if close:
        raise ValueError(
            "close=True cannot be combined with ax=: close= releases the "
            "figure this call created, but with ax= the figure belongs to "
            "the caller and closing it would take every other panel with "
            "it. Close it yourself once the whole figure is finished."
        )
    return ax.figure, ax, False


def _rotate_xticklabels(
    ax: "plt.Axes", rotation: float = 45, ha: str = "right"
) -> None:
    """
    Rotate the x tick labels of ``ax`` in place.

    Parameters
    ----------
    ax : matplotlib.axes.Axes
        The axis whose tick labels are rotated.
    rotation : float, optional
        Rotation angle in degrees. Defaults to ``45``.
    ha : str, optional
        Horizontal alignment of the labels. Defaults to ``"right"``.

    Notes
    -----
    This is the object-oriented equivalent of
    ``plt.xticks(rotation=..., ha=...)``: with no tick positions given,
    ``pyplot`` also only updates the existing label artists.
    """
    for label in ax.get_xticklabels():
        label.set_rotation(rotation)
        label.set_horizontalalignment(ha)


def _finalise(
    fig: "plt.Figure", owns_figure: bool, show: bool, close: bool
) -> "plt.Figure":
    """
    Lay out, optionally display and optionally close a finished figure.

    Parameters
    ----------
    fig : matplotlib.figure.Figure
        The figure that was drawn.
    owns_figure : bool
        ``True`` when the helper created ``fig``. Only then is
        ``tight_layout`` applied and only then may the figure be closed --
        re-laying out a caller-supplied figure would disturb panels this
        call never touched.
    show : bool
        Call ``plt.show()`` before returning.
    close : bool
        Call ``plt.close(fig)`` before returning.

    Returns
    -------
    matplotlib.figure.Figure
        ``fig``, unchanged. A closed figure is still a complete figure:
        ``fig.savefig(...)`` and attribute access keep working, only the
        ``pyplot`` figure manager has let go of it.
    """
    if owns_figure:
        fig.tight_layout()
    if show:
        plt.show()
    if close:
        # Ordered after ``show`` on purpose: closing first would leave
        # nothing to display.
        plt.close(fig)
    return fig


def _check_frequencies(
    requested: Sequence[float],
    elements: Iterable,
    attribute: str,
    element_kind: str,
) -> None:
    """
    Verify that every requested frequency was actually computed.

    A bar of height zero and a frequency that was never part of the
    calculation are two entirely different statements, and a bar chart
    cannot tell them apart. This guard makes the second one impossible:
    the plotting helpers only draw frequencies that are present in the
    result, so a zero bar always means a measured zero.

    Parameters
    ----------
    requested : sequence of float
        The frequencies the caller asked for.
    elements : iterable
        The result elements to inspect (``result.buses`` or
        ``result.branches``).
    attribute : str
        Name of the per-element frequency mapping, e.g. ``"uepr_freq"``.
    element_kind : str
        Human-readable element name used in the message, e.g. ``"bus"``.

    Raises
    ------
    KeyError
        If a requested frequency is missing from the frequency mapping of
        at least one element. The message lists the offending frequencies,
        the elements they are missing from and the frequencies that *are*
        available.

    Notes
    -----
    ``50`` and ``50.0`` are the same dictionary key in Python, so an
    integer frequency continues to match a float result key.
    """
    elements = list(elements)
    if not requested:
        return

    available = set()
    for element in elements:
        available.update(getattr(element, attribute, {}) or {})

    missing_everywhere = [f for f in requested if f not in available]
    if missing_everywhere:
        raise KeyError(
            f"Frequenc{'y' if len(missing_everywhere) == 1 else 'ies'} "
            f"{missing_everywhere} not present in '{attribute}' of any "
            f"{element_kind}; the result was computed for "
            f"{sorted(available)} Hz. Plotting them would draw bars of "
            f"height 0, which reads as a measured zero."
        )

    partly_missing = {}
    for element in elements:
        mapping = getattr(element, attribute, {}) or {}
        gaps = [f for f in requested if f not in mapping]
        if gaps:
            partly_missing[getattr(element, "name", "?")] = gaps
    if partly_missing:
        raise KeyError(
            f"Incomplete result: '{attribute}' is missing frequencies "
            f"{partly_missing} while other {element_kind}s carry them. "
            "Plotting would silently substitute 0."
        )


def plot_bus_voltages(
    result: Result,
    frequencies: Optional[List[float]] = None,
    figsize: Optional[Tuple[float, float]] = None,
    title: str = "UEPR vs Bus Name",
    yscale: str = "linear",
    show: bool = False,
    *,
    ax: Optional["plt.Axes"] = None,
    close: bool = False,
) -> "plt.Figure":
    """
    Plot the UEPR (Earth Potential Rise) for each bus.

    Generates a bar plot of UEPR values for each bus in the network. It
    can plot either frequency-dependent UEPR magnitudes or RMS UEPR
    values based on the provided parameters. Additionally, the y-axis
    scale can be linear or logarithmic.

    Parameters
    ----------
    result : Result
        The :class:`Result` object containing the calculation results.
    frequencies : list of float, optional
        Frequencies (in Hz) to plot. If ``None`` or empty, RMS UEPR
        values are plotted. Defaults to ``None``.
    figsize : tuple of (float, float), optional
        Figure size in inches as a ``(width, height)`` tuple. ``None``
        selects the module default ``(12, 6)``. Must not be combined with
        ``ax``.
    title : str, optional
        Title of the plot. Defaults to ``"UEPR vs Bus Name"``.
    yscale : {'linear', 'log'}, optional
        Scale for the y-axis. Defaults to ``"linear"``.
    show : bool, optional
        Whether to display the plot immediately. If ``False``, the figure
        is returned for further manipulation. Defaults to ``False``.
    ax : matplotlib.axes.Axes, optional
        Draw into this axis instead of creating a new figure. The figure
        that owns ``ax`` is returned, so a multi-panel figure comes back
        unchanged from every call. Keyword-only.
    close : bool, optional
        Close the created figure with ``plt.close`` before returning it.
        The returned figure stays usable -- ``fig.savefig(...)`` works --
        it is simply no longer registered with ``pyplot``. Use this in
        parameter sweeps. Cannot be combined with ``ax``. Keyword-only.
        Defaults to ``False``.

    Returns
    -------
    matplotlib.figure.Figure
        The Matplotlib figure object containing the plot. With ``ax``
        given this is ``ax.figure``, i.e. the caller's own figure.

    Raises
    ------
    KeyError
        If a specified frequency is not present in ``uepr_freq`` of any
        bus, or is missing from some buses while present in others. The
        message lists the frequencies the result actually contains.
    ValueError
        If ``ax`` is combined with ``figsize`` or with ``close=True``.

    Notes
    -----
    Without ``ax`` and without ``close`` the returned figure is registered
    with ``pyplot`` and stays open until the caller closes it. In a loop --
    a parameter sweep over ``specific_earth_resistance``, for instance --
    pass ``close=True`` (or call ``plt.close(fig)`` yourself), otherwise
    matplotlib accumulates the figures and warns after 20.

    ``tight_layout`` is only applied to figures this call created; a
    caller-supplied ``ax`` leaves the surrounding layout alone.

    Examples
    --------
    >>> import groundinsight as gi  # doctest: +SKIP
    >>> fig = gi.plot_bus_voltages(  # doctest: +SKIP
    ...     result=result, frequencies=[50, 60], yscale="log",
    ... )

    Two scenarios side by side in one figure:

    >>> import matplotlib.pyplot as plt  # doctest: +SKIP
    >>> fig, axes = plt.subplots(1, 2, figsize=(16, 5), sharey=True)  # doctest: +SKIP
    >>> gi.plot_bus_voltages(result=base, ax=axes[0], title="base")  # doctest: +SKIP
    >>> gi.plot_bus_voltages(result=outage, ax=axes[1], title="cable out")  # doctest: +SKIP
    """
    # Extract bus names
    bus_names = [bus.name for bus in result.buses]

    # Initialize data structure for plotting
    uepr_data = {}

    if frequencies:
        _check_frequencies(frequencies, result.buses, "uepr_freq", "bus")
        # Plot frequency-dependent UEPR values
        for freq in frequencies:
            uepr_values = []
            for bus in result.buses:
                uepr_complex = bus.uepr_freq.get(freq)
                # ``is not None`` rather than a truth test: ComplexNumber is a
                # Pydantic model without ``__bool__``, so 0+0j is truthy today
                # -- but relying on that to mean "present" is an accident, and
                # a genuine 0 V at one frequency must not read as missing.
                if uepr_complex is not None:
                    # Calculate magnitude of the complex UEPR value
                    uepr_magnitude = abs(complex(uepr_complex.real, uepr_complex.imag))
                else:
                    uepr_magnitude = 0.0  # Handle missing data
                uepr_values.append(uepr_magnitude)
            uepr_data[freq] = uepr_values

        # Plotting
        fig, ax, owns_figure = _prepare_axes(
            ax, figsize, close, _DEFAULT_BAR_FIGSIZE
        )
        bar_width = 0.8 / len(frequencies)
        indices = range(len(bus_names))
        for i, (freq, uepr_values) in enumerate(uepr_data.items()):
            positions = [x + i * bar_width for x in indices]
            ax.bar(positions, uepr_values, width=bar_width, label=f"{freq} Hz")

        ax.set_xticks([x + bar_width * (len(frequencies) - 1) / 2 for x in indices])
        ax.set_xticklabels(bus_names, rotation=45, ha="right")
    else:
        # Plot RMS values of UEPR
        uepr_rms_values = [
            bus.uepr if bus.uepr is not None else 0.0 for bus in result.buses
        ]
        fig, ax, owns_figure = _prepare_axes(
            ax, figsize, close, _DEFAULT_BAR_FIGSIZE
        )
        ax.bar(bus_names, uepr_rms_values, label="RMS")

    # Configure plot
    ax.set_yscale(yscale)
    ax.set_xlabel("Bus Name")
    ax.set_ylabel("UEPR (V)")
    ax.set_title(title)
    _rotate_xticklabels(ax)
    ax.legend(title="Frequency")
    ax.grid(True, axis="y")

    return _finalise(fig, owns_figure, show, close)


def plot_branch_currents(
    result: Result,
    frequencies: Optional[List[float]] = None,
    figsize: Optional[Tuple[float, float]] = None,
    title: str = "Branch Currents",
    yscale: str = "linear",
    show: bool = False,
    *,
    ax: Optional["plt.Axes"] = None,
    close: bool = False,
) -> "plt.Figure":
    """
    Plot the branch currents for each branch.

    Generates a bar plot of branch currents in the network. It can plot
    either frequency-dependent current magnitudes or RMS current values
    based on the provided parameters.

    Parameters
    ----------
    result : Result
        The :class:`Result` object containing the calculation results.
    frequencies : list of float, optional
        Frequencies (in Hz) to plot. If ``None`` or empty, RMS current
        values are plotted. Defaults to ``None``.
    figsize : tuple of (float, float), optional
        Figure size in inches. ``None`` selects the module default
        ``(12, 6)``. Must not be combined with ``ax``.
    title : str, optional
        Title of the plot. Defaults to ``"Branch Currents"``.
    yscale : {'linear', 'log'}, optional
        Scale for the y-axis. Defaults to ``"linear"``.
    show : bool, optional
        Whether to display the plot immediately. Defaults to ``False``.
    ax : matplotlib.axes.Axes, optional
        Draw into this axis instead of creating a new figure. Keyword-only.
    close : bool, optional
        Close the created figure before returning it; the figure object
        stays usable. Cannot be combined with ``ax``. Keyword-only.
        Defaults to ``False``.

    Returns
    -------
    matplotlib.figure.Figure
        The Matplotlib figure object containing the plot. With ``ax``
        given this is ``ax.figure``.

    Raises
    ------
    KeyError
        If a specified frequency is not present in ``i_s_freq`` of any
        branch, or is missing from some branches while present in others.
        The message lists the frequencies the result actually contains.
    ValueError
        If ``ax`` is combined with ``figsize`` or with ``close=True``.

    Notes
    -----
    Without ``ax`` and without ``close`` the returned figure is registered
    with ``pyplot`` and stays open until the caller closes it; pass
    ``close=True`` when plotting in a loop.

    Examples
    --------
    >>> import groundinsight as gi  # doctest: +SKIP
    >>> fig = gi.plot_branch_currents(  # doctest: +SKIP
    ...     result=result, frequencies=[50, 60],
    ... )
    """
    # Extract branch names
    branch_names = [branch.name for branch in result.branches]

    # Initialize data structure for plotting
    current_data = {}

    if frequencies:
        _check_frequencies(frequencies, result.branches, "i_s_freq", "branch")
        # Plot frequency-dependent branch currents
        for freq in frequencies:
            current_values = []
            for branch in result.branches:
                current_complex = branch.i_s_freq.get(freq)
                # ``is not None``: see the note in plot_bus_voltages.
                if current_complex is not None:
                    # Calculate magnitude of the complex current
                    current_magnitude = abs(
                        complex(current_complex.real, current_complex.imag)
                    )
                else:
                    current_magnitude = 0.0  # Handle missing data
                current_values.append(current_magnitude)
            current_data[freq] = current_values

        # Plotting
        fig, ax, owns_figure = _prepare_axes(
            ax, figsize, close, _DEFAULT_BAR_FIGSIZE
        )
        bar_width = 0.8 / len(
            frequencies
        )  # Adjust bar width based on the number of frequencies
        indices = range(len(branch_names))
        for i, (freq, current_values) in enumerate(current_data.items()):
            positions = [x + i * bar_width for x in indices]
            ax.bar(positions, current_values, width=bar_width, label=f"{freq} Hz")
        ax.set_yscale(yscale)
        ax.set_xlabel("Branch Name")
        ax.set_ylabel("Current (A)")
        ax.set_title(title)
        ax.set_xticks(
            [x + bar_width * (len(frequencies) - 1) / 2 for x in indices]
        )
        ax.set_xticklabels(branch_names, rotation=45, ha="right")
        ax.legend(title="Frequency")
        ax.grid(True, axis="y")

    else:
        # Plot RMS values of branch currents
        current_rms_values = []
        for branch in result.branches:
            current_rms = branch.i_s  # RMS value of branch current
            if current_rms is not None:
                current_rms_values.append(current_rms)
            else:
                current_rms_values.append(0.0)  # Handle missing data

        # Plotting
        fig, ax, owns_figure = _prepare_axes(
            ax, figsize, close, _DEFAULT_BAR_FIGSIZE
        )
        ax.bar(branch_names, current_rms_values, label="RMS")
        ax.set_yscale(yscale)
        ax.set_xlabel("Branch Name")
        ax.set_ylabel("Current RMS (A)")
        ax.set_title(title)
        _rotate_xticklabels(ax)
        ax.legend()
        ax.grid(True, axis="y")

    return _finalise(fig, owns_figure, show, close)


def plot_bus_currents(
    result: Result,
    frequencies: Optional[List[float]] = None,
    figsize: Optional[Tuple[float, float]] = None,
    title: str = "Bus Currents",
    yscale: str = "linear",
    show: bool = False,
    *,
    ax: Optional["plt.Axes"] = None,
    close: bool = False,
) -> "plt.Figure":
    """
    Plot the bus currents for each bus.

    Generates a bar plot of bus currents in the network. It can plot
    either frequency-dependent current magnitudes or RMS current values
    based on the provided parameters.

    Parameters
    ----------
    result : Result
        The :class:`Result` object containing the calculation results.
    frequencies : list of float, optional
        Frequencies (in Hz) to plot. If ``None`` or empty, RMS current
        values are plotted. Defaults to ``None``.
    figsize : tuple of (float, float), optional
        Figure size in inches. ``None`` selects the module default
        ``(12, 6)``. Must not be combined with ``ax``.
    title : str, optional
        Title of the plot. Defaults to ``"Bus Currents"``.
    yscale : {'linear', 'log'}, optional
        Scale for the y-axis. Defaults to ``"linear"``.
    show : bool, optional
        Whether to display the plot immediately. Defaults to ``False``.
    ax : matplotlib.axes.Axes, optional
        Draw into this axis instead of creating a new figure. Keyword-only.
    close : bool, optional
        Close the created figure before returning it; the figure object
        stays usable. Cannot be combined with ``ax``. Keyword-only.
        Defaults to ``False``.

    Returns
    -------
    matplotlib.figure.Figure
        The Matplotlib figure object containing the plot. With ``ax``
        given this is ``ax.figure``.

    Raises
    ------
    KeyError
        If a specified frequency is not present in ``ia_freq`` of any bus,
        or is missing from some buses while present in others. The message
        lists the frequencies the result actually contains.
    ValueError
        If ``ax`` is combined with ``figsize`` or with ``close=True``.

    Notes
    -----
    Without ``ax`` and without ``close`` the returned figure is registered
    with ``pyplot`` and stays open until the caller closes it; pass
    ``close=True`` when plotting in a loop.

    Examples
    --------
    >>> import groundinsight as gi  # doctest: +SKIP
    >>> fig = gi.plot_bus_currents(  # doctest: +SKIP
    ...     result=result, frequencies=[50, 60],
    ... )
    """
    # Extract bus names
    bus_names = [bus.name for bus in result.buses]

    # Initialize data structure for plotting
    current_data = {}

    if frequencies:
        _check_frequencies(frequencies, result.buses, "ia_freq", "bus")
        # Plot frequency-dependent bus currents
        for freq in frequencies:
            current_values = []
            for bus in result.buses:
                current_complex = bus.ia_freq.get(freq)
                # ``is not None``: see the note in plot_bus_voltages.
                if current_complex is not None:
                    # Calculate magnitude of the complex current
                    current_magnitude = abs(
                        complex(current_complex.real, current_complex.imag)
                    )
                else:
                    current_magnitude = 0.0  # Handle missing data
                current_values.append(current_magnitude)
            current_data[freq] = current_values

        # Plotting
        fig, ax, owns_figure = _prepare_axes(
            ax, figsize, close, _DEFAULT_BAR_FIGSIZE
        )
        bar_width = 0.8 / len(
            frequencies
        )  # Adjust bar width based on the number of frequencies
        indices = range(len(bus_names))
        for i, (freq, current_values) in enumerate(current_data.items()):
            positions = [x + i * bar_width for x in indices]
            ax.bar(positions, current_values, width=bar_width, label=f"{freq} Hz")
        ax.set_yscale(yscale)
        ax.set_xlabel("Bus Name")
        ax.set_ylabel("Current (A)")
        ax.set_title(title)
        ax.set_xticks(
            [x + bar_width * (len(frequencies) - 1) / 2 for x in indices]
        )
        ax.set_xticklabels(bus_names, rotation=45, ha="right")
        ax.legend(title="Frequency")
        ax.grid(True, axis="y")

    else:
        # Plot RMS values of bus currents
        current_rms_values = []
        for bus in result.buses:
            current_rms = bus.ia  # RMS value of bus current
            if current_rms is not None:
                current_rms_values.append(current_rms)
            else:
                current_rms_values.append(0.0)  # Handle missing data

        # Plotting
        fig, ax, owns_figure = _prepare_axes(
            ax, figsize, close, _DEFAULT_BAR_FIGSIZE
        )
        ax.bar(bus_names, current_rms_values, label="RMS")
        ax.set_yscale(yscale)
        ax.set_xlabel("Bus Name")
        ax.set_ylabel("Current RMS (A)")
        ax.set_title(title)
        _rotate_xticklabels(ax)
        ax.legend()
        ax.grid(True, axis="y")

    return _finalise(fig, owns_figure, show, close)


def plot_epr_transient(
    result: "ResultTransient",
    *,
    buses: Optional[List[str]] = None,
    figsize: Optional[Tuple[float, float]] = None,
    title: str = "EPR over time",
    show: bool = False,
    ax: Optional["plt.Axes"] = None,
    close: bool = False,
) -> "plt.Figure":
    """
    Plot the time-domain EPR for one or more observed buses.

    Parameters
    ----------
    result : ResultTransient
        A result returned by
        :meth:`groundinsight.simulation.TransientStudy.solve`.
    buses : list of str, optional
        Restrict the plot to a subset of the observed buses. ``None``
        plots every bus that was set as an observation point.
    figsize : tuple of (float, float), optional
        Figure size in inches. ``None`` selects the module default
        ``(10, 5)``. Must not be combined with ``ax``.
    title : str, optional
        Plot title. Defaults to ``"EPR over time"``.
    show : bool, optional
        Call ``plt.show()`` immediately. Defaults to ``False``; the figure
        is always returned for further customisation.
    ax : matplotlib.axes.Axes, optional
        Draw into this axis instead of creating a new figure -- for
        example to stack EPR and shield current in one two-row figure.
    close : bool, optional
        Close the created figure before returning it; the figure object
        stays usable. Cannot be combined with ``ax``. Defaults to
        ``False``.

    Returns
    -------
    matplotlib.figure.Figure
        The generated figure, or ``ax.figure`` when ``ax`` is given.

    Raises
    ------
    ValueError
        If ``buses`` references a name that was not observed, or if ``ax``
        is combined with ``figsize`` or with ``close=True``.

    Notes
    -----
    Without ``ax`` and without ``close`` the returned figure is registered
    with ``pyplot`` and stays open until the caller closes it; pass
    ``close=True`` when plotting in a loop.

    Examples
    --------
    >>> import matplotlib.pyplot as plt  # doctest: +SKIP
    >>> fig, (top, bottom) = plt.subplots(2, 1, sharex=True)  # doctest: +SKIP
    >>> gi.plot_epr_transient(result=res, ax=top)  # doctest: +SKIP
    >>> gi.plot_branch_current_transient(result=res, ax=bottom)  # doctest: +SKIP
    """
    available = list(result.epr_t.keys())
    selection = buses or available
    missing = [b for b in selection if b not in result.epr_t]
    if missing:
        raise ValueError(
            f"Buses not present in result: {missing}. "
            f"Observed buses are: {available}."
        )

    fig, ax, owns_figure = _prepare_axes(
        ax, figsize, close, _DEFAULT_TRANSIENT_FIGSIZE
    )
    for bus_name in selection:
        ax.plot(result.time_s, result.epr_t[bus_name], label=bus_name)
    ax.set_xlabel("time / s")
    ax.set_ylabel("EPR / V")
    ax.set_title(title)
    ax.grid(True, alpha=0.3)
    ax.legend(title="Bus")
    return _finalise(fig, owns_figure, show, close)


def plot_branch_current_transient(
    result: "ResultTransient",
    *,
    branches: Optional[List[str]] = None,
    figsize: Optional[Tuple[float, float]] = None,
    title: str = "Branch current over time",
    show: bool = False,
    ax: Optional["plt.Axes"] = None,
    close: bool = False,
) -> "plt.Figure":
    """
    Plot the time-domain shield current for one or more observed branches.

    Parameters
    ----------
    result : ResultTransient
        A transient result.
    branches : list of str, optional
        Restrict the plot to a subset of the observed branches. ``None``
        plots every branch that was set as an observation point.
    figsize : tuple of (float, float), optional
        Figure size in inches. ``None`` selects the module default
        ``(10, 5)``. Must not be combined with ``ax``.
    title : str, optional
        Plot title.
    show : bool, optional
        Call ``plt.show()`` immediately. Defaults to ``False``.
    ax : matplotlib.axes.Axes, optional
        Draw into this axis instead of creating a new figure.
    close : bool, optional
        Close the created figure before returning it; the figure object
        stays usable. Cannot be combined with ``ax``. Defaults to
        ``False``.

    Returns
    -------
    matplotlib.figure.Figure
        The generated figure, or ``ax.figure`` when ``ax`` is given.

    Raises
    ------
    ValueError
        If ``branches`` references a name that was not observed, or if
        ``ax`` is combined with ``figsize`` or with ``close=True``.

    Notes
    -----
    Without ``ax`` and without ``close`` the returned figure is registered
    with ``pyplot`` and stays open until the caller closes it; pass
    ``close=True`` when plotting in a loop.
    """
    available = list(result.i_branch_t.keys())
    selection = branches or available
    missing = [b for b in selection if b not in result.i_branch_t]
    if missing:
        raise ValueError(
            f"Branches not present in result: {missing}. "
            f"Observed branches are: {available}."
        )

    fig, ax, owns_figure = _prepare_axes(
        ax, figsize, close, _DEFAULT_TRANSIENT_FIGSIZE
    )
    for branch_name in selection:
        ax.plot(result.time_s, result.i_branch_t[branch_name], label=branch_name)
    ax.set_xlabel("time / s")
    ax.set_ylabel("current / A")
    ax.set_title(title)
    ax.grid(True, alpha=0.3)
    ax.legend(title="Branch")
    return _finalise(fig, owns_figure, show, close)

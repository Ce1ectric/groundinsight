# analysis/statistics.py

"""
Summarise and classify a long-format result frame.

A parameter study produces hundreds of rows; a report needs a handful of
numbers and a verdict. Polars already does the arithmetic, so these two
functions add only the shape an engineering summary wants -- named quantiles
next to the extremes, and a class column that turns a continuous quantity into
the bands a study argues in.

Deliberately absent: any built-in table of admissible values. Touch-voltage
limits depend on the clearing time, the standard edition and the additional
resistances assumed, and a plausible-looking constant baked in here would be
carried into a result without ever being checked. :func:`classify` therefore
takes the edges from the caller, who can cite them.
"""

from __future__ import annotations

from typing import Optional, Sequence

import polars as pl

__all__ = [
    "summarize",
    "classify",
]

#: Quantiles reported by :func:`summarize` unless told otherwise.
DEFAULT_QUANTILES = (0.05, 0.5, 0.95)


def summarize(
    frame: pl.DataFrame,
    value: str,
    *,
    by: Optional[Sequence[str]] = None,
    quantiles: Sequence[float] = DEFAULT_QUANTILES,
) -> pl.DataFrame:
    """
    Reduce one column to count, spread, quantiles and extremes.

    Parameters
    ----------
    frame : pl.DataFrame
        Long-format results, e.g. from
        :meth:`~groundinsight.simulation.sweep.SweepResult.buses`.
    value : str
        Numeric column to summarise.
    by : sequence of str, optional
        Grouping columns. Without them the whole frame is one group.
    quantiles : sequence of float, optional
        Quantiles in ``[0, 1]``. Each becomes a column ``p05``, ``p50``, ...

    Returns
    -------
    pl.DataFrame
        One row per group: the grouping columns, then ``n``, ``n_null``,
        ``mean``, ``std``, ``min``, the quantiles, and ``max``. Sorted by the
        grouping columns so repeated runs produce identical output.

    Raises
    ------
    ValueError
        If a named column is missing, if the value column is not numeric, or if
        a quantile lies outside ``[0, 1]``.

    Examples
    --------
    >>> summarize(study.buses(), "EPR_V", by=["bus_name"])  # doctest: +SKIP
    """
    missing = [c for c in [value, *(by or [])] if c not in frame.columns]
    if missing:
        raise ValueError(
            f"Column(s) {missing} are not in the frame. Available: "
            f"{frame.columns}."
        )
    if not frame.schema[value].is_numeric():
        raise ValueError(
            f"Column '{value}' has dtype {frame.schema[value]}, which cannot be "
            f"summarised numerically. Note that res_buses() reports "
            f"'frequency_Hz' as a string because it carries the 'RMS' marker "
            f"row -- filter or cast it before grouping on it."
        )
    bad = [q for q in quantiles if not 0.0 <= q <= 1.0]
    if bad:
        raise ValueError(f"Quantile(s) {bad} lie outside [0, 1].")

    aggregations = [
        pl.col(value).count().alias("n"),
        pl.col(value).null_count().alias("n_null"),
        pl.col(value).mean().alias("mean"),
        pl.col(value).std().alias("std"),
        pl.col(value).min().alias("min"),
    ]
    aggregations += [
        pl.col(value).quantile(q, interpolation="linear").alias(_quantile_name(q))
        for q in quantiles
    ]
    aggregations.append(pl.col(value).max().alias("max"))

    if by:
        return frame.group_by(list(by)).agg(aggregations).sort(list(by))
    return frame.select(aggregations)


def _quantile_name(q: float) -> str:
    """``0.05`` -> ``p05``, ``0.5`` -> ``p50``, ``0.975`` -> ``p97_5``."""
    scaled = q * 100.0
    if abs(scaled - round(scaled)) < 1e-9:
        return f"p{int(round(scaled)):02d}"
    return "p" + f"{scaled:g}".replace(".", "_")


def classify(
    frame: pl.DataFrame,
    value: str,
    edges: Sequence[float],
    *,
    labels: Optional[Sequence[str]] = None,
    name: str = "class",
) -> pl.DataFrame:
    """
    Add a class column by binning a numeric column at the given edges.

    ``edges`` are the interior boundaries: ``n`` edges make ``n + 1`` classes.
    Bins are closed on the right, so a value exactly on an edge falls into the
    lower class -- the conservative reading when the edge is a limit.

    Parameters
    ----------
    frame : pl.DataFrame
        Frame to extend.
    value : str
        Numeric column to classify.
    edges : sequence of float
        Strictly increasing interior boundaries, e.g. ``[80, 150]`` for the
        three bands "below 80", "80 to 150" and "above 150".
    labels : sequence of str, optional
        Names for the classes, ``len(edges) + 1`` of them. Defaults to readable
        interval labels built from the edges.
    name : str, optional
        Name of the added column. Defaults to ``"class"``.

    Returns
    -------
    pl.DataFrame
        The input with one column added. Rows whose value is null get a null
        class rather than being forced into the lowest band.

    Raises
    ------
    ValueError
        If the column is missing or not numeric, if ``edges`` is empty or not
        strictly increasing, or if ``labels`` has the wrong length.

    Examples
    --------
    >>> classify(study.buses(), "EPR_V", [80.0, 150.0],  # doctest: +SKIP
    ...          labels=["ok", "check", "exceeded"])
    """
    if value not in frame.columns:
        raise ValueError(
            f"Column '{value}' is not in the frame. Available: {frame.columns}."
        )
    if not frame.schema[value].is_numeric():
        raise ValueError(
            f"Column '{value}' has dtype {frame.schema[value]} and cannot be "
            f"binned numerically."
        )
    edges = [float(e) for e in edges]
    if not edges:
        raise ValueError(
            "classify needs at least one edge; with none there is only one "
            "class and nothing to decide."
        )
    if any(b <= a for a, b in zip(edges, edges[1:])):
        raise ValueError(
            f"Edges must be strictly increasing, got {edges}. Overlapping or "
            f"repeated edges would make the class assignment ambiguous."
        )

    if labels is None:
        labels = [f"<= {edges[0]:g}"]
        labels += [f"{a:g} - {b:g}" for a, b in zip(edges, edges[1:])]
        labels.append(f"> {edges[-1]:g}")
    labels = list(labels)
    if len(labels) != len(edges) + 1:
        raise ValueError(
            f"{len(edges)} edge(s) define {len(edges) + 1} classes, but "
            f"{len(labels)} label(s) were given."
        )

    expression = pl.when(pl.col(value).is_null()).then(None)
    for edge, label in zip(edges, labels):
        expression = expression.when(pl.col(value) <= edge).then(pl.lit(label))
    expression = expression.otherwise(pl.lit(labels[-1]))
    return frame.with_columns(expression.alias(name))

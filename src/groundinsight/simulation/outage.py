# simulation/outage.py

"""
Outage / What-If Study Module.

Convenience layer on top of the ``active`` flag of
:class:`groundinsight.models.core_models.Bus` and
:class:`groundinsight.models.core_models.Branch`. An :class:`Outage` is a
named scenario that lists buses and/or branches to be deactivated; the
:func:`outage_context` context manager applies the scenario for the
duration of a ``with`` block and restores the previous state on exit, and
:func:`run_outage_study` runs ``run_fault`` for the base case and a list
of scenarios in one go and packages the results into an
:class:`OutageStudyResult` with ready-made comparison helpers.

Example:

    >>> import groundinsight as gi
    >>> # build a network, add fault "F1", then:
    >>> study = gi.run_outage_study(
    ...     network=net,
    ...     fault="F1",
    ...     scenarios=[
    ...         gi.Outage(name="branch_b12_open", disabled_branches=["B12"]),
    ...         gi.Outage(name="bus5_isolated", disabled_buses=["bus5"]),
    ...     ],
    ... )
    >>> df = study.compare_buses()
    >>> df_branch = study.compare_branches()

Inactive elements are physically modelled as open circuits / removed nodes
in the solver (see :mod:`groundinsight.electrical_network` and
:mod:`groundinsight.pathfinder`); the outage layer does not duplicate that
logic, it only flips the ``active`` flag and re-runs the existing
``run_fault`` pipeline.
"""

from __future__ import annotations

import logging
from contextlib import contextmanager
from typing import Dict, Iterator, List, Optional, Sequence, Tuple

import polars as pl
from pydantic import BaseModel, Field

from groundinsight.models.core_models import Network


logger = logging.getLogger(__name__)


# Per-network nesting stack for :func:`outage_context`. Keyed by
# ``id(network)``; each value is a list of per-level baselines pushed by
# nested ``with`` blocks. Each per-level baseline is a
# ``{"buses": {name: original_active}, "branches": {name: original_active}}``
# mapping captured *immediately before* the level flips its targets, so
# the level's exit can revert exactly its own changes (and nothing the
# inner level did to untouched-by-this-level elements). The stack
# bookkeeping lives at module scope rather than on the
# :class:`Network` instance so the public Pydantic surface stays clean.
_OUTAGE_BASELINE_STACK: Dict[int, List[Dict[str, Dict[str, bool]]]] = {}


class Outage(BaseModel):
    """
    Definition of a single what-if scenario.

    An outage is described by the names of buses and/or branches that should
    be deactivated for the duration of the scenario. The names must refer to
    elements that exist in the network when the scenario is applied;
    unknown names raise ``ValueError`` at apply time.

    Attributes
    ----------
    name : str
        Unique label used in result keys and DataFrame columns.
    disabled_buses : list of str
        Names of buses to deactivate.
    disabled_branches : list of str
        Names of branches to deactivate.
    description : str, optional
        Free-form description for documentation.
    """

    name: str
    disabled_buses: List[str] = Field(default_factory=list)
    disabled_branches: List[str] = Field(default_factory=list)
    description: Optional[str] = None

    def __str__(self) -> str:
        return (
            f"Outage(name={self.name}, "
            f"disabled_buses={self.disabled_buses}, "
            f"disabled_branches={self.disabled_branches})"
        )


class OutageStudyResult(BaseModel):
    """
    Result of an outage study.

    Holds bus and branch result DataFrames per scenario (and for the base
    case, if it was included). The ``compare_*`` methods produce
    long-format Polars DataFrames suitable for plotting or further
    aggregation.

    Attributes
    ----------
    fault : str
        Name of the fault that was solved.
    base_label : str
        Label of the base scenario (typically ``"base"``).
    scenarios : list of Outage
        The scenarios in the order they were run.
    bus_results : dict of str to polars.DataFrame
        ``label -> res_buses(fault)`` for the base case (if present) and
        every scenario.
    branch_results : dict of str to polars.DataFrame
        Same as ``bus_results`` but for branch results.
    """

    model_config = {"arbitrary_types_allowed": True}

    fault: str
    base_label: str
    scenarios: List[Outage]
    bus_results: Dict[str, pl.DataFrame] = Field(default_factory=dict)
    branch_results: Dict[str, pl.DataFrame] = Field(default_factory=dict)

    def labels(self) -> List[str]:
        """
        Return the labels of all stored scenarios.

        The base label is included first (if present), followed by the user
        scenarios in the order they were submitted to ``run_outage_study``.

        Returns
        -------
        list of str
            Scenario labels.
        """
        order = []
        if self.base_label in self.bus_results:
            order.append(self.base_label)
        for scenario in self.scenarios:
            if scenario.name in self.bus_results:
                order.append(scenario.name)
        return order

    def compare_buses(
        self,
        *,
        against: Optional[str] = None,
        columns: Sequence[str] = ("EPR_V",),
    ) -> pl.DataFrame:
        """
        Long-format bus comparison across scenarios.

        For every ``(bus_name, frequency_Hz)`` pair the table contains one
        row per scenario per metric, plus the ``delta_vs_<against>`` and
        ``delta_pct_vs_<against>`` columns relative to the reference scenario.

        Parameters
        ----------
        against : str, optional
            Reference scenario label. Defaults to ``base_label`` if it
            exists in the result, otherwise to the first available label.
        columns : sequence of str, optional
            Bus result metrics to compare. Defaults to ``("EPR_V",)``.
            Must be column names of the per-scenario ``res_buses``
            DataFrame (e.g. ``"EPR_V"``, ``"I_bus_A"``).

        Returns
        -------
        polars.DataFrame
            Long-format frame with columns ``["bus_name", "frequency_Hz",
            "scenario", "metric", "value", "delta_vs_<ref>",
            "delta_pct_vs_<ref>"]``. ``delta_pct_vs_<ref>`` is ``null``
            wherever the reference value is 0 -- see :meth:`_compare`.

        Raises
        ------
        ValueError
            If ``against`` is not present in the stored results or if a
            requested column is missing from a scenario frame.
        """
        return self._compare(
            tables=self.bus_results,
            id_columns=("bus_name", "frequency_Hz"),
            metrics=columns,
            against=against,
        )

    def compare_branches(
        self,
        *,
        against: Optional[str] = None,
        columns: Sequence[str] = ("I_branch_A",),
    ) -> pl.DataFrame:
        """
        Long-format branch comparison across scenarios.

        Same shape as :meth:`compare_buses` but keyed by
        ``(branch_name, frequency_Hz)``.

        Parameters
        ----------
        against : str, optional
            Reference scenario label.
        columns : sequence of str, optional
            Branch result metrics to compare. Defaults to
            ``("I_branch_A",)``.

        Returns
        -------
        polars.DataFrame
            Long-format comparison frame. ``delta_pct_vs_<ref>`` is
            ``null`` wherever the reference value is 0 -- see
            :meth:`_compare`.

        Raises
        ------
        ValueError
            If ``against`` is not present in the stored results or if a
            requested column is missing from a scenario frame.
        """
        return self._compare(
            tables=self.branch_results,
            id_columns=("branch_name", "frequency_Hz"),
            metrics=columns,
            against=against,
        )

    # ----- internal helpers ---------------------------------------------------

    def _resolve_reference(
        self, tables: Dict[str, pl.DataFrame], against: Optional[str]
    ) -> str:
        """Pick the reference scenario for a comparison."""
        if against is not None:
            if against not in tables:
                raise ValueError(
                    f"Reference scenario '{against}' is not in the study result. "
                    f"Available labels: {list(tables.keys())}."
                )
            return against
        if self.base_label in tables:
            return self.base_label
        labels = list(tables.keys())
        if not labels:
            raise ValueError("OutageStudyResult contains no scenarios.")
        return labels[0]

    def _compare(
        self,
        *,
        tables: Dict[str, pl.DataFrame],
        id_columns: Tuple[str, str],
        metrics: Sequence[str],
        against: Optional[str],
    ) -> pl.DataFrame:
        """
        Build a long-format comparison frame from per-scenario result tables.

        The reference scenario provides the baseline value; absolute and
        relative deltas are computed for every other scenario. ``frequency_Hz``
        is cast to ``Utf8`` so that the ``"RMS"`` row mixes cleanly with the
        per-frequency floats.

        A zero reference value yields ``null`` in the relative column, not
        ``inf`` or ``NaN``. Zero baselines are ordinary in a grounding
        study -- an islanded station, a frequency the fault does not
        excite -- and "x % of nothing" is undefined, not infinite. The
        absolute ``delta_vs_<ref>`` column still carries the full
        information for those rows.
        """
        ref_label = self._resolve_reference(tables, against)
        if not metrics:
            raise ValueError("`columns` must contain at least one metric.")

        long_frames = []
        id_col_a, id_col_b = id_columns
        for label, df in tables.items():
            missing = [m for m in metrics if m not in df.columns]
            if missing:
                raise ValueError(
                    f"Scenario '{label}' is missing column(s) {missing}; "
                    f"available columns: {df.columns}."
                )
            sub = df.select(
                [
                    pl.col(id_col_a),
                    pl.col(id_col_b).cast(pl.Utf8).alias(id_col_b),
                    *[pl.col(m) for m in metrics],
                ]
            ).unpivot(
                index=[id_col_a, id_col_b],
                on=list(metrics),
                variable_name="metric",
                value_name="value",
            )
            sub = sub.with_columns(pl.lit(label).alias("scenario"))
            long_frames.append(sub)

        long_df = pl.concat(long_frames, how="vertical_relaxed")

        ref_df = (
            long_df.filter(pl.col("scenario") == ref_label)
            .select([id_col_a, id_col_b, "metric", pl.col("value").alias("_ref_value")])
        )
        merged = long_df.join(ref_df, on=[id_col_a, id_col_b, "metric"], how="left")
        delta = pl.col("value") - pl.col("_ref_value")
        # The relative column is only defined where the baseline is non-zero.
        # Dividing anyway produced +/-inf for "0 V in the reference, non-zero
        # in the scenario" -- the single most interesting row of an outage
        # study -- and NaN for "0 V in both", the most boring one. Both then
        # poisoned every mean()/max() over the column and sorted to the top of
        # any "largest relative change" ranking. ``null`` is the honest
        # marker: Polars aggregations skip it and it plots as a gap.
        relative = (
            pl.when(pl.col("_ref_value") == 0.0)
            .then(pl.lit(None, dtype=pl.Float64))
            .otherwise(delta / pl.col("_ref_value") * 100.0)
        )
        merged = merged.with_columns(
            delta.alias(f"delta_vs_{ref_label}"),
            relative.alias(f"delta_pct_vs_{ref_label}"),
        ).drop("_ref_value")
        return merged

    def __str__(self) -> str:
        return (
            f"OutageStudyResult(fault={self.fault}, "
            f"scenarios={[s.name for s in self.scenarios]})"
        )


@contextmanager
def outage_context(network: Network, outage: Outage) -> Iterator[Network]:
    """
    Apply an :class:`Outage` to ``network`` for the duration of a ``with`` block.

    On entry, the ``active`` flag of the listed buses and branches is set
    to ``False``. On exit, the original ``active`` values are restored
    even if the body raises. Path information is invalidated on entry and
    on exit because both the outage and its rollback can change topology.

    Parameters
    ----------
    network : Network
        The network to mutate in place.
    outage : Outage
        Scenario describing what to deactivate.

    Yields
    ------
    Network
        The same ``network`` instance, mutated for the scenario.

    Raises
    ------
    ValueError
        If a name in ``disabled_buses`` or ``disabled_branches`` is not
        part of the network.

    Notes
    -----
    - The module-level pathfinder cache (see
      :mod:`groundinsight.pathfinder`) is cleared **on exit** as well
      as on entry. Without the exit-side eviction, a multi-scenario
      outage sweep was accumulating one cache entry per scenario
      indefinitely; together with the new LRU cap the cache footprint
      now matches the user-visible state of ``network``.
    - The function is now **nestable**. Nested ``with`` blocks store
      the *outer-block-baseline* on a per-(bus, branch) basis using
      ``setdefault`` so the inner block does not capture the
      already-modified outer state as its own baseline. On
      outer-block exit the originally-captured baseline is restored,
      so changes made by the inner block to buses/branches that are
      not part of the outer outage are correctly preserved through
      the unwind.

    Examples
    --------
    >>> with outage_context(net, Outage(name="b12", disabled_branches=["B12"])):  # doctest: +SKIP
    ...     gi.run_fault(net, "F1")
    ...     df = net.res_buses(fault="F1")
    """
    from groundinsight.pathfinder import clear_pathfinder_cache  # local import

    unknown_buses = [b for b in outage.disabled_buses if b not in network.buses]
    if unknown_buses:
        raise ValueError(
            f"Outage '{outage.name}' references unknown buses: {unknown_buses}"
        )
    unknown_branches = [
        b for b in outage.disabled_branches if b not in network.branches
    ]
    if unknown_branches:
        raise ValueError(
            f"Outage '{outage.name}' references unknown branches: {unknown_branches}"
        )

    # Nestable baseline tracking. Each ``outage_context`` level pushes a
    # per-level baseline onto the module-level
    # ``_OUTAGE_BASELINE_STACK`` (keyed by ``id(network)``) that records
    # the ``active`` flag for every bus / branch *this level* touches
    # *immediately before* it flips them. On exit, only that level's
    # changes are reverted; nothing the inner block has done to
    # untouched-by-this-level elements is overwritten. This makes
    # ``with outage_context(net, A): with outage_context(net, B): ...``
    # behave like a stack so nested contexts compose cleanly.
    # ``id(network)`` rather than a Pydantic ``PrivateAttr`` keeps the
    # bookkeeping out of the public ``Network`` surface.
    net_key = id(network)
    stack = _OUTAGE_BASELINE_STACK.setdefault(net_key, [])

    # Snapshot the pre-flip state for *this* level. We snapshot *before*
    # mutating so an outer level that has already flipped a flag to
    # ``False`` does not see the inner level "restore" it to ``False``
    # on exit (correct behaviour: inner level only restores what it
    # itself changed at the moment it changed it).
    level_baseline = {
        "buses": {
            name: network.buses[name].active for name in outage.disabled_buses
        },
        "branches": {
            name: network.branches[name].active
            for name in outage.disabled_branches
        },
    }
    stack.append(level_baseline)

    saved_paths = dict(network.paths)

    try:
        for name in outage.disabled_buses:
            network.buses[name].active = False
        for name in outage.disabled_branches:
            network.branches[name].active = False
        # The pre-existing path cache is no longer valid for the new
        # topology — drop both ``network.paths`` and the module-level
        # pathfinder cache for this network so the inner solve rebuilds
        # both. Atomic rebind keeps any external snapshot intact.
        network.paths = {}
        clear_pathfinder_cache(network)
        yield network
    finally:
        # Restore only what *this* level touched.
        for name, value in level_baseline["buses"].items():
            network.buses[name].active = value
        for name, value in level_baseline["branches"].items():
            network.branches[name].active = value
        stack.pop()
        if not stack:
            _OUTAGE_BASELINE_STACK.pop(net_key, None)
        network.paths = saved_paths
        # Drop the cache entries this context built up so the resident
        # footprint matches the externally-visible state of ``network``.
        clear_pathfinder_cache(network)


def run_outage_study(
    network: Network,
    *,
    fault: str,
    scenarios: List[Outage],
    include_base: bool = True,
    base_label: str = "base",
    auto_parallel_coefficients: Optional[bool] = None,
    phase_current_mode: str = "auto",
    redefine_paths: bool = True,
) -> OutageStudyResult:
    """
    Solve ``fault`` for the base case (optional) and a list of outage scenarios.

    Each scenario is applied via :func:`outage_context`, then ``run_fault``
    is called. Bus and branch result DataFrames are pulled out of the
    network and stored under the scenario label so that the network's own
    ``results`` dict is left in a single, reproducible state at the end of
    the study (the last scenario's solution).

    Parameters
    ----------
    network : Network
        The network to study. Must contain ``fault``.
    fault : str
        Name of the fault to solve in every scenario.
    scenarios : list of Outage
        Scenarios to run, in order.
    include_base : bool, optional
        If ``True`` (default) the base case (no outages) is solved first
        and stored under ``base_label``.
    base_label : str, optional
        Label used to identify the base case in the stored DataFrames.
        Defaults to ``"base"``.
    auto_parallel_coefficients : bool, optional
        Deprecated alias, forwarded to :func:`run_fault`. Defaults to
        ``None`` (not given).
    phase_current_mode : {"auto", "paths"}, optional
        Forwarded to :func:`run_fault`. Defaults to ``"auto"``, which is
        the only mode that divides the source current correctly over a
        ring or a mesh -- and an outage study is precisely where a ring
        turns into a chain and back, so a per-scenario topology change
        must not change the modelling assumption underneath it.
    redefine_paths : bool, optional
        If ``True`` (default), the pre-existing path cache on ``network``
        is dropped at the start of the study so that path definitions
        match the current topology. Each scenario then triggers its own
        path definition through :func:`run_fault`.

    Returns
    -------
    OutageStudyResult
        Aggregated result with comparison helpers.

    Raises
    ------
    ValueError
        If ``fault`` is not part of the network or if a scenario label
        collides with ``base_label``.

    Examples
    --------
    >>> study = run_outage_study(  # doctest: +SKIP
    ...     net, fault="F1",
    ...     scenarios=[
    ...         Outage(name="b12_open", disabled_branches=["B12"]),
    ...         Outage(name="bus5_isolated", disabled_buses=["bus5"]),
    ...     ],
    ... )
    >>> study.compare_buses()       # doctest: +SKIP
    >>> study.compare_branches()    # doctest: +SKIP
    """
    if fault not in network.faults:
        raise ValueError(f"Fault '{fault}' is not part of network '{network.name}'.")

    seen_labels = set()
    if include_base:
        seen_labels.add(base_label)
    for scenario in scenarios:
        if scenario.name == base_label and include_base:
            raise ValueError(
                f"Scenario name '{scenario.name}' collides with the base label."
            )
        if scenario.name in seen_labels:
            raise ValueError(f"Duplicate scenario label '{scenario.name}'.")
        seen_labels.add(scenario.name)

    bus_results: Dict[str, pl.DataFrame] = {}
    branch_results: Dict[str, pl.DataFrame] = {}

    if redefine_paths:
        network.paths = {}

    # Local import to avoid a circular dependency between simulation and
    # network_operations (network_operations imports core_models, which the
    # outage module also touches at module load time via Pydantic).
    from groundinsight.network_operations import run_fault

    if include_base:
        logger.info(
            "Outage study: solving base case for fault '%s' under label '%s'.",
            fault,
            base_label,
        )
        run_fault(
            network,
            fault_name=fault,
            auto_parallel_coefficients=auto_parallel_coefficients,
            phase_current_mode=phase_current_mode,
        )
        bus_results[base_label] = network.res_buses(fault=fault)
        branch_results[base_label] = network.res_branches(fault=fault)

    for scenario in scenarios:
        logger.info(
            "Outage study: running scenario '%s' (%s buses, %s branches).",
            scenario.name,
            len(scenario.disabled_buses),
            len(scenario.disabled_branches),
        )
        with outage_context(network, scenario):
            run_fault(
                network,
                fault_name=fault,
                auto_parallel_coefficients=auto_parallel_coefficients,
                phase_current_mode=phase_current_mode,
            )
            bus_results[scenario.name] = network.res_buses(fault=fault)
            branch_results[scenario.name] = network.res_branches(fault=fault)

    return OutageStudyResult(
        fault=fault,
        base_label=base_label,
        scenarios=list(scenarios),
        bus_results=bus_results,
        branch_results=branch_results,
    )

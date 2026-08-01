# analysis/inverse_rho_f.py

"""
Inverse determination of the standard rho-f bus model parameters.

The bus grounding impedance is parameterised in the canonical linear
rho-f form

.. math::

    Z(\\rho, f) = k_1 \\, \\rho + (k_2 + j k_3) \\, f + (k_4 + j k_5)
                  \\, \\rho \\, f

with five real parameters ``k = (k1, k2, k3, k4, k5)`` -- a common
form fitted to measured rho-f impedance points. This module solves
the *inverse* problem: given a
fully built network and a list of selected buses, find the parameter
combination that maximises the rho-f characteristic while still keeping
the RMS earth potential rise (EPR) below a user-supplied limit
``u_limit`` at every selected bus when each bus is swept as the active
fault.

This module ships the foundation layer plus a 1-D scaling variant:

* :func:`evaluate_max_epr_under_k` -- internal helper. For a given
  ``k`` the bus impedance of every selected bus is overwritten on the
  fly with the rho-f form (using each bus' own
  :attr:`Bus.specific_earth_resistance` for ``rho``), then each bus is
  set as the active fault and :func:`run_fault` is invoked. The maximum
  RMS EPR observed across the sweep is returned together with the
  per-bus values. The original network state (bus impedances, faults,
  ``active_fault``) is restored in a ``finally`` block.
* :func:`find_max_rho_f_scaling` -- log-bisects the largest scaling
  factor ``c`` such that ``k = c * k_ref`` keeps the swept maximum EPR
  at or below ``u_limit``. The reference vector ``k_ref`` is typically
  obtained by fitting the rho-f model above to measured impedance
  points; this function answers *"how much head-room does the network
  have around this characteristic?"*.

A full Pareto front in :math:`\\mathbb{R}^5` is *not* part of this
layer and will land as ``find_max_rho_f_pareto_front`` in a follow-up
release on top of the same helper.
"""

import logging
import math
from typing import Any, Dict, List, Literal, Optional, Tuple

import polars as pl

from ..models.core_models import ComplexNumber, Fault, Network
from ..network_operations import run_fault
from ._bisection import (
    STATUS_BRACKET_FULLY_ADMISSIBLE,
    STATUS_MAX_ITER_REACHED,
    classify,
    report,
    validate_c_bounds,
    validate_limit,
    validate_max_iter,
    validate_tol_rel,
)


logger = logging.getLogger(__name__)


KVector = Tuple[float, float, float, float, float]


def _z_rho_f(k: KVector, rho: float, f: float) -> complex:
    """Evaluate ``Z(rho, f) = k1*rho + (k2+jk3)*f + (k4+jk5)*rho*f``.

    Parameters
    ----------
    k : tuple of (float, float, float, float, float)
        Real model parameters ``(k1, k2, k3, k4, k5)``.
    rho : float
        Specific earth resistance at the bus, in Ohm * m.
    f : float
        Frequency in Hz.

    Returns
    -------
    complex
        Complex bus grounding impedance at the given ``rho`` and ``f``.
    """
    k1, k2, k3, k4, k5 = k
    return k1 * rho + (k2 + 1j * k3) * f + (k4 + 1j * k5) * rho * f


def evaluate_max_epr_under_k(
    network: Network,
    bus_names: List[str],
    k: KVector,
    *,
    fault_scalings: Optional[Dict[float, float]] = None,
    run_fault_kwargs: Optional[Dict[str, Any]] = None,
) -> Dict[str, float]:
    """Evaluate the RMS EPR at each selected bus for a given ``k`` vector.

    For every name in ``bus_names`` the bus grounding impedance is
    overwritten with the rho-f linear form
    ``Z(rho, f) = k1*rho + (k2+jk3)*f + (k4+jk5)*rho*f`` — using the
    bus' own :attr:`Bus.specific_earth_resistance` for ``rho`` — and
    the bus is swept as the active fault. An existing fault at that bus
    is reused; otherwise a temporary one is created with
    ``fault_scalings`` (or ``1.0`` at every simulation frequency by
    default) and removed at the end. The original bus impedances, the
    temporary faults and the previous ``active_fault`` are restored in a
    ``finally`` block, so the network is left exactly as it was on entry.

    Parameters
    ----------
    network : Network
        The simulation network. Sources, branches, paths and the buses to
        be swept must already be configured.
    bus_names : list of str
        Buses whose impedance is rewritten and which are swept as fault
        locations one by one. Must be non-empty and refer to buses in
        ``network``.
    k : tuple of (float, float, float, float, float)
        Real model parameters ``(k1, k2, k3, k4, k5)``.
    fault_scalings : dict of float to float, optional
        Frequency-resolved scalings used for any fault that has to be
        created on the fly (no pre-existing fault at the swept bus).
        Defaults to ``{f: 1.0}`` for every simulation frequency. Existing
        faults are reused unmodified.
    run_fault_kwargs : dict, optional
        Extra keyword arguments forwarded to :func:`run_fault` at every
        step.

    Returns
    -------
    dict of str to float
        Mapping of each ``bus_name`` to its RMS EPR in volts at that bus
        when it is the active fault.

    Raises
    ------
    ValueError
        If ``bus_names`` is empty, ``k`` does not have length 5, or any
        name is not in the network.
    """
    if not bus_names:
        raise ValueError("bus_names must not be empty.")
    if len(k) != 5:
        raise ValueError(f"k must be a 5-tuple, got length {len(k)}.")
    missing = [b for b in bus_names if b not in network.buses]
    if missing:
        raise ValueError(f"Unknown bus(es) in network: {missing!r}.")

    rfk: Dict[str, Any] = dict(run_fault_kwargs) if run_fault_kwargs else {}
    if fault_scalings is None:
        fault_scalings = {float(f): 1.0 for f in network.frequencies}

    # Snapshot current bus impedances so the network can be restored.
    impedance_backup: Dict[str, Dict[float, ComplexNumber]] = {
        b: dict(network.buses[b].impedance) for b in bus_names
    }

    # Look up an existing fault per swept bus (if any).
    existing_faults_by_bus: Dict[str, str] = {}
    for fname, fault in network.faults.items():
        if fault.bus in bus_names and fault.bus not in existing_faults_by_bus:
            existing_faults_by_bus[fault.bus] = fname

    temp_faults_created: List[str] = []
    active_fault_backup = network.active_fault
    # Snapshot results of any *pre-existing* fault we are about to reuse so we
    # can restore them (run_fault overwrites results[fault] with the k-form EPR).
    reused_fault_names = set(existing_faults_by_bus.values())
    results_backup = {fn: network.results.get(fn) for fn in reused_fault_names}
    had_result_backup = {fn: fn in network.results for fn in reused_fault_names}

    # Snapshot existing paths and clear them: ``run_fault`` only triggers
    # ``define_paths`` when ``network.paths`` is empty. We need fresh paths
    # for every (source, fault) combination -- including the temporary
    # faults we are about to create -- so we drop the cache for the
    # duration of the sweep and restore it at the end.
    paths_backup = dict(network.paths)
    network.paths.clear()

    def _temp_fault_name(b: str) -> str:
        base = f"_inv_rhof_{b}"
        candidate = base
        n = 0
        while candidate in network.faults:
            n += 1
            candidate = f"{base}_{n}"
        return candidate

    epr_per_bus: Dict[str, float] = {}

    try:
        # Overwrite every selected bus impedance with the k-form.
        for b in bus_names:
            bus = network.buses[b]
            rho = float(bus.specific_earth_resistance)
            new_imp: Dict[float, ComplexNumber] = {}
            for f in network.frequencies:
                z = _z_rho_f(k, rho, float(f))
                new_imp[float(f)] = ComplexNumber(real=z.real, imag=z.imag)
            bus.impedance = new_imp

        # Pre-create any temporary faults the sweep needs *before* the
        # first ``run_fault`` so ``define_paths`` enumerates paths for
        # all swept (source, fault) combinations in a single pass.
        sweep_fault_names: List[str] = []
        for b in bus_names:
            if b in existing_faults_by_bus:
                sweep_fault_names.append(existing_faults_by_bus[b])
            else:
                fname = _temp_fault_name(b)
                network.add_fault(
                    Fault(name=fname, bus=b, scalings=dict(fault_scalings))
                )
                temp_faults_created.append(fname)
                sweep_fault_names.append(fname)

        # Sweep: each fault once. The first call populates ``network.paths``
        # for every (source, fault) pair; subsequent calls reuse the cache.
        for b, fname in zip(bus_names, sweep_fault_names):
            run_fault(network, fault_name=fname, **rfk)
            try:
                result_bus = next(
                    rb for rb in network.results[fname].buses if rb.name == b
                )
            except StopIteration as exc:
                # ``run_fault_kwargs={"buses": [...]}`` may filter the
                # swept bus out of the result frame. Surface the situation
                # with a clear lookup error rather than a bare
                # ``StopIteration`` from the generator expression.
                available = [
                    rb.name for rb in network.results[fname].buses
                ]
                raise LookupError(
                    f"Bus {b!r} is not present in the result frame of "
                    f"fault {fname!r}. Available buses: {available!r}. "
                    "If you are passing run_fault_kwargs={'buses': [...]}, "
                    "make sure every swept bus appears in that list."
                ) from exc
            epr_per_bus[b] = float(result_bus.uepr)
    finally:
        # Restore bus impedances regardless of success/failure.
        for b in bus_names:
            network.buses[b].impedance = impedance_backup[b]
        # Drop temporary faults and any results they produced.
        for fname in temp_faults_created:
            network.faults.pop(fname, None)
            network.results.pop(fname, None)
        # Restore results for reused (pre-existing) faults; drop any result
        # run_fault created for a reused fault that had none before.
        for fname in reused_fault_names:
            if had_result_backup.get(fname):
                network.results[fname] = results_backup[fname]
            else:
                network.results.pop(fname, None)
        # Restore the path cache (cleared at the start of the sweep).
        # Use an atomic dict swap rather than ``clear()`` + ``update()`` so
        # that concurrent readers never see an empty ``network.paths``.
        # Pydantic's ``BaseModel`` exposes the field as a mutable dict; we
        # mutate it in place but in two steps that look atomic from a
        # snapshot perspective (rebuild a new dict, then reassign).
        rebuilt = dict(paths_backup)
        try:
            network.paths = rebuilt  # atomic rebind when allowed
        except Exception:
            network.paths.clear()
            network.paths.update(rebuilt)
        # Restore active_fault and the per-fault _active flags exactly. When
        # the network had no active fault on entry (the common fresh-network
        # case), clear the flag rather than leaving it on a deleted temp fault.
        if active_fault_backup is None:
            network.active_fault = None
            for _flt in network.faults.values():
                _flt._set_active(False)
        elif active_fault_backup in network.faults:
            network.set_active_fault(active_fault_backup, keep_results=True)
        else:  # pragma: no cover -- backup fault vanished
            network.active_fault = None
            for _flt in network.faults.values():
                _flt._set_active(False)

    return epr_per_bus


def find_max_rho_f_scaling(
    network: Network,
    bus_names: List[str],
    u_limit: float,
    k_ref: KVector,
    *,
    c_bounds: Tuple[float, float] = (1e-3, 1e3),
    tol_rel: float = 1e-3,
    max_iter: int = 60,
    fault_scalings: Optional[Dict[float, float]] = None,
    run_fault_kwargs: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Find the largest scaling factor ``c`` of a reference k-vector.

    Performs a log-scale bisection on ``c`` so that ``k = c * k_ref``
    yields a maximum RMS EPR (across all buses swept as faults) that is
    at or below ``u_limit``. ``k_ref`` is typically obtained by fitting
    the rho-f model above to measured impedance points; this function
    answers *how much head-room the network has around that
    characteristic*.

    Parameters
    ----------
    network : Network
        The simulation network. Buses, branches, sources and paths must
        already be configured.
    bus_names : list of str
        Buses whose impedance is parameterised by ``k`` *and* which are
        swept as fault locations.
    u_limit : float
        Upper bound on the RMS EPR (in volts) at any swept bus. Must be
        finite and strictly positive.
    k_ref : tuple of (float, float, float, float, float)
        Reference 5-tuple ``(k1_ref, ..., k5_ref)``. Must have length 5
        and not be the zero vector.
    c_bounds : tuple of (float, float), optional
        Search interval for ``c``. Finite, strictly positive,
        ``c_bounds[0] < c_bounds[1]``. Defaults to ``(1e-3, 1e3)``.
    tol_rel : float, optional
        Bisection tolerance on the relative bracket width
        ``(c_hi - c_lo) / c_lo``. Must be finite and strictly positive.
        Defaults to ``1e-3``.
    max_iter : int, optional
        Hard cap on bisection steps. Must be an ``int`` of at least 1.
        Defaults to ``60``.
    fault_scalings : dict of float to float, optional
        See :func:`evaluate_max_epr_under_k`.
    run_fault_kwargs : dict, optional
        Forwarded to :func:`run_fault`.

    Returns
    -------
    dict
        Mapping with keys

        - ``"c_max"`` (float): a scaling factor whose swept maximum EPR
          was evaluated and found compatible with ``u_limit``. It is the
          *largest* such factor only when ``"converged"`` is ``True``.
        - ``"k_max"`` (tuple of float): ``c_max * k_ref``.
        - ``"max_epr_rms_at_c_max"`` (float): maximum RMS EPR across all
          swept buses at ``c_max``, in volts.
        - ``"epr_rms_per_bus_at_c_max"`` (dict of str to float): RMS EPR
          per swept bus at ``c_max``.
        - ``"iterations"`` (int): number of bisection steps taken.
        - ``"converged"`` (bool): ``True`` iff the bracket was closed to
          within ``tol_rel``. Check this before using ``c_max`` or
          ``k_max`` as a design value.
        - ``"status"`` (str): ``"converged"``,
          ``"bracket_within_tol_on_entry"``, ``"max_iter_reached"`` or
          ``"bracket_fully_admissible"``.
        - ``"c_bracket"`` (tuple of float): the interval provably
          containing the true maximum; ``(c_hi, inf)`` when the whole
          bracket was admissible.
        - ``"bracket_rel_width"`` (float): ``(c_hi - c_lo) / c_lo`` of
          that interval, comparable against ``tol_rel``.

        As in :func:`~groundinsight.analysis.find_max_rho_scaling`,
        ``iterations == 0`` does not identify a case on its own; read
        ``"status"``.

    Raises
    ------
    ValueError
        For invalid input -- ``u_limit``, ``tol_rel``, ``max_iter`` and
        ``c_bounds`` are checked for finiteness as well as range -- or if
        ``c = c_bounds[0]`` already violates the EPR limit, or if the
        model returns a non-finite EPR at some trial factor.

    Examples
    --------
    >>> import groundinsight as gi  # doctest: +SKIP
    >>> res = gi.find_max_rho_f_scaling(  # doctest: +SKIP
    ...     network=net, bus_names=["b0", "b1"], u_limit=200.0,
    ...     k_ref=(0.01, 0.0, 0.0, 0.0, 0.0),
    ... )
    """
    validate_limit(u_limit, "u_limit")
    validate_tol_rel(tol_rel)
    validate_max_iter(max_iter)
    if not bus_names:
        raise ValueError("bus_names must not be empty.")
    if len(k_ref) != 5:
        raise ValueError(f"k_ref must be a 5-tuple, got length {len(k_ref)}.")
    if all(x == 0 for x in k_ref):
        raise ValueError("k_ref must not be the zero vector.")
    c_lo_init, c_hi_init = validate_c_bounds(c_bounds)

    last_epr_per_bus: Dict[str, Dict[str, float]] = {"value": {}}

    def _max_epr_at(c: float) -> float:
        eprs = evaluate_max_epr_under_k(
            network, bus_names,
            k=tuple(c * x for x in k_ref),
            fault_scalings=fault_scalings,
            run_fault_kwargs=run_fault_kwargs,
        )
        last_epr_per_bus["value"] = eprs
        epr = max(eprs.values()) if eprs else 0.0
        if not math.isfinite(epr):
            # Same reasoning as in ``inverse_rho``: a non-finite EPR makes
            # every comparison against u_limit False, so the bisection would
            # take the same turn at every step and reach the lower bracket
            # bound without ever raising.
            raise ValueError(
                f"The swept maximum EPR evaluated to {epr!r} for the scaling "
                f"factor c={c:g} (k = {tuple(c * x for x in k_ref)!r}). A "
                "non-finite EPR cannot be compared against u_limit -- every "
                "comparison would be False and the bisection would walk to "
                "the lower bracket bound without raising. Check the k-vector "
                "at that scale against the buses' specific_earth_resistance."
            )
        return epr

    epr_lo = _max_epr_at(c_lo_init)
    eprs_lo_snapshot: Dict[str, float] = dict(last_epr_per_bus["value"])
    if epr_lo > u_limit:
        raise ValueError(
            f"u_limit={u_limit:g} V is below the maximum EPR at "
            f"c={c_lo_init:g}: max EPR_RMS={epr_lo:g} V — no scaling "
            f"factor in the bracket {c_bounds!r} satisfies the constraint."
        )

    epr_hi = _max_epr_at(c_hi_init)
    iterations = 0
    if epr_hi <= u_limit:
        logger.info(
            "Bracket fully admissible: max EPR_RMS at c=%g is %g V <= "
            "u_limit=%g V. Consider widening c_bounds.",
            c_hi_init, epr_hi, u_limit,
        )
        c_max, epr_at = c_hi_init, epr_hi
        epr_per_bus_at = dict(last_epr_per_bus["value"])
        status = STATUS_BRACKET_FULLY_ADMISSIBLE
        c_lo, c_hi = c_lo_init, c_hi_init
    else:
        c_lo, c_hi = c_lo_init, c_hi_init
        while iterations < max_iter and (c_hi - c_lo) / c_lo > tol_rel:
            c_mid = math.sqrt(c_lo * c_hi)  # geometric mean -> log bisection
            epr_mid = _max_epr_at(c_mid)
            if epr_mid <= u_limit:
                c_lo, epr_lo = c_mid, epr_mid
                eprs_lo_snapshot = dict(last_epr_per_bus["value"])
            else:
                c_hi = c_mid
            iterations += 1
        c_max, epr_at = c_lo, epr_lo
        epr_per_bus_at = eprs_lo_snapshot
        status = classify(iterations, c_lo, c_hi, tol_rel)
        if status == STATUS_MAX_ITER_REACHED:
            logger.warning(
                "Bisection stopped at the step cap max_iter=%d without "
                "closing the bracket: c in [%g, %g], relative width %g > "
                "tol_rel=%g. c_max=%g is admissible but may be well below "
                "the true maximum.",
                max_iter, c_lo, c_hi, (c_hi - c_lo) / c_lo, tol_rel, c_lo,
            )

    # Defensive consistency check: the maximum of the per-bus EPRs at
    # c_max must equal the reported ``max_epr_rms_at_c_max``. A drift
    # here would mean the snapshot and the headline figure refer to
    # different iterations — historically a bug surface (the
    # ``eprs_lo_snapshot`` shadow variable was added in 0.4.0 to plug
    # exactly this gap). Re-evaluate once at ``c_max`` to lock the
    # invariant in place; the cost is one extra ``run_fault`` per sweep.
    epr_at = _max_epr_at(c_max)
    epr_per_bus_at = dict(last_epr_per_bus["value"])

    return {
        "c_max": c_max,
        "k_max": tuple(c_max * x for x in k_ref),
        "max_epr_rms_at_c_max": epr_at,
        "epr_rms_per_bus_at_c_max": epr_per_bus_at,
        "iterations": iterations,
        **report(status, c_lo, c_hi),
    }


def select_rho_f_from_catalog(
    network: Network,
    bus_names: List[str],
    u_limit: float,
    candidates: Dict[str, KVector],
    *,
    fault_scalings: Optional[Dict[float, float]] = None,
    run_fault_kwargs: Optional[Dict[str, Any]] = None,
    sort_by: Literal["max_epr_asc", "max_epr_desc", "name"] = "max_epr_asc",
) -> pl.DataFrame:
    """Pick admissible rho-f characteristics from a user-provided catalog.

    Evaluates every candidate ``k`` in the catalog with
    :func:`evaluate_max_epr_under_k` and reports, per candidate, the
    maximum RMS EPR observed across the bus sweep, the per-bus EPRs and
    whether the candidate satisfies the limit ``u_limit``. The catalog is
    typically a hand-curated list of soil scenarios (e.g. dry sand, wet
    clay, permafrost) or rho-f fits from previous measurements.

    Parameters
    ----------
    network
        The simulation network. Sources, branches and the buses
        to be swept must already be configured.
    bus_names
        Buses whose impedance is parameterised by every
        candidate ``k`` and which are swept as fault locations.
    u_limit
        Upper bound on the RMS EPR (in volts) at any swept bus.
        Must be strictly positive.
    candidates
        Mapping ``{name: (k1, k2, k3, k4, k5)}`` of the
        candidate rho-f characteristics. Names must be unique
        (Python dict guarantees that), tuples must each have length
        5. May be empty -- the result is then an empty DataFrame
        with the documented schema.
    fault_scalings
        Frequency-resolved scalings for any fault that
        has to be created on the fly. See
        :func:`evaluate_max_epr_under_k`.
    run_fault_kwargs
        Forwarded to :func:`run_fault`.
    sort_by
        How to sort the result rows.

        - ``"max_epr_asc"`` (default): admissible candidates first,
        tightest-EPR-margin candidates at the top.
        - ``"max_epr_desc"``: largest EPR first; useful to inspect
        the worst-case candidates.
        - ``"name"``: lexicographic by candidate name.

    Returns
    -------
    A Polars DataFrame with one row per candidate and the columns

    - ``"name"`` (str)
    - ``"k1", "k2", "k3", "k4", "k5"`` (float)
    - ``"max_epr_rms_V"`` (float)
        maximum RMS EPR across the bus
        sweep, in volts.
    - ``"admissible"`` (bool)
        ``True`` iff
        ``max_epr_rms_V <= u_limit``.
    - one ``"epr_<bus>_V"`` column per swept bus, holding the RMS
        EPR at that bus when it is the active fault, in volts.

    Raises
    ------
    ValueError
        If ``u_limit`` is not finite and strictly positive,
        ``bus_names`` is empty, or any candidate has a wrong-length
        ``k`` or refers to an unknown bus (the underlying helper
        validates this). A NaN ``u_limit`` is rejected explicitly:
        it would otherwise pass a plain positivity check and mark
        the whole catalog inadmissible.

    Examples
    --------
        >>> import groundinsight as gi
        >>> from groundinsight.models.core_models import BusType, BranchType
        >>> from groundinsight.analysis import select_rho_f_from_catalog
        >>> bt = BusType(name="BT", system_type="Grounded",
        ...              voltage_level=20.0,
        ...              impedance_formula="rho * 0.01 + 0*f")
        >>> brt = BranchType(name="BRT", grounding_conductor=True,
        ...                  self_impedance_formula="(0.25 + I*0.6)*l",
        ...                  mutual_impedance_formula="(0.0 + I*0.6)*l")
        >>> net = gi.create_network(name="N", frequencies=[50])
        >>> _ = gi.create_bus(name="b0", type=bt, network=net)
        >>> _ = gi.create_bus(name="b1", type=bt, network=net)
        >>> _ = gi.create_branch(name="br", type=brt, from_bus="b0",
        ...                      to_bus="b1", length=1.0, network=net)
        >>> _ = gi.create_source(name="src", bus="b0",
        ...                      values={50: 100.0}, network=net)
        >>> catalog = {
        ...     "low":   (0.005, 0.0, 0.0, 0.0, 0.0),
        ...     "med":   (0.01,  0.0, 0.0, 0.0, 0.0),
        ...     "high":  (0.05,  0.0, 0.0, 0.0, 0.0),
        ... }
        >>> df = select_rho_f_from_catalog(
        ...     net, ["b0", "b1"], u_limit=20.0, candidates=catalog,
        ... )
        >>> set(df.columns) >= {"name", "k1", "max_epr_rms_V", "admissible"}
        True
    """
    # Same check as in the two searches, and for the same reason -- but the
    # consequence here is worse. This function does not return a scalar a
    # reader might sanity-check, it returns a table with an ``admissible``
    # column, and every entry in it is ``max_epr <= u_limit``. Against NaN
    # every one of those comparisons is False, so the table would report
    # that *no* soil model in the catalog is usable, next to an EPR column
    # that is correct and finite. Nothing in the output would point at the
    # limit as the broken part.
    validate_limit(u_limit, "u_limit")
    if not bus_names:
        raise ValueError("bus_names must not be empty.")

    # Empty catalog -> return an empty DataFrame with the documented schema.
    if not candidates:
        schema = {
            "name": pl.Utf8,
            "k1": pl.Float64, "k2": pl.Float64, "k3": pl.Float64,
            "k4": pl.Float64, "k5": pl.Float64,
            "max_epr_rms_V": pl.Float64,
            "admissible": pl.Boolean,
        }
        for b in bus_names:
            schema[f"epr_{b}_V"] = pl.Float64
        return pl.DataFrame(schema=schema)

    rows: List[Dict[str, Any]] = []
    for name, k in candidates.items():
        if len(k) != 5:
            raise ValueError(
                f"Candidate {name!r}: k must be a 5-tuple, got length {len(k)}."
            )
        eprs = evaluate_max_epr_under_k(
            network, bus_names, k=tuple(k),
            fault_scalings=fault_scalings,
            run_fault_kwargs=run_fault_kwargs,
        )
        max_epr = max(eprs.values()) if eprs else 0.0
        row: Dict[str, Any] = {
            "name": name,
            "k1": float(k[0]),
            "k2": float(k[1]),
            "k3": float(k[2]),
            "k4": float(k[3]),
            "k5": float(k[4]),
            "max_epr_rms_V": float(max_epr),
            "admissible": bool(max_epr <= u_limit),
        }
        for b in bus_names:
            row[f"epr_{b}_V"] = float(eprs[b])
        rows.append(row)

    df = pl.DataFrame(rows)

    if sort_by == "max_epr_asc":
        # Admissible first (True -> 1 sorts after False -> 0 by default,
        # so descending on ``admissible`` puts True on top), then
        # ascending EPR within each block.
        df = df.sort(
            by=["admissible", "max_epr_rms_V"], descending=[True, False]
        )
    elif sort_by == "max_epr_desc":
        df = df.sort(by="max_epr_rms_V", descending=True)
    elif sort_by == "name":
        df = df.sort(by="name")
    else:  # pragma: no cover -- guarded by Literal type, defensive
        raise ValueError(
            f"sort_by must be one of "
            f"'max_epr_asc' | 'max_epr_desc' | 'name', got {sort_by!r}."
        )

    return df

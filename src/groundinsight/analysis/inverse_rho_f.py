# analysis/inverse_rho_f.py

"""
Inverse determination of the standard rho-f bus model parameters.

The bus grounding impedance is parameterised in the canonical linear
rho-f form

.. math::

    Z(\\rho, f) = k_1 \\, \\rho + (k_2 + j k_3) \\, f + (k_4 + j k_5)
                  \\, \\rho \\, f

with five real parameters ``k = (k1, k2, k3, k4, k5)`` -- the same form
that ``groundmeas.services.analytics.rho_f_model`` fits to measured
impedance points. This module solves the *inverse* problem: given a
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
  at or below ``u_limit``. The reference vector ``k_ref`` is typically a
  fit from ``groundmeas.rho_f_model`` over measured points; this
  function answers *"how much head-room does the network have around
  this characteristic?"*.

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


logger = logging.getLogger(__name__)


KVector = Tuple[float, float, float, float, float]


def _z_rho_f(k: KVector, rho: float, f: float) -> complex:
    """Evaluate ``Z(rho, f) = k1*rho + (k2+jk3)*f + (k4+jk5)*rho*f``.

    Args:
        k: 5-tuple of real model parameters ``(k1, k2, k3, k4, k5)``.
        rho: Specific earth resistance at the bus, in ohm-metres.
        f: Frequency in Hz.

    Returns:
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
    overwritten with the rho-f linear form ``Z(rho, f) = k1*rho +
    (k2+jk3)*f + (k4+jk5)*rho*f`` -- using the bus' own
    :attr:`Bus.specific_earth_resistance` for ``rho`` -- and the bus is
    swept as the active fault: an existing fault at that bus is reused,
    otherwise a temporary one is created with ``fault_scalings`` (or
    ``1.0`` at every simulation frequency by default) and removed at the
    end. The original bus impedances, the temporary faults and the
    previous ``active_fault`` are restored in a ``finally`` block, so the
    network is left exactly as it was on entry.

    Args:
        network: The simulation network. Sources, branches, paths and
            the buses to be swept must already be configured.
        bus_names: Buses whose impedance is rewritten and which are
            swept as fault locations one by one. Must be non-empty and
            refer to buses in ``network``.
        k: 5-tuple ``(k1, k2, k3, k4, k5)`` of real model parameters.
        fault_scalings: Frequency-resolved scalings used for any fault
            that has to be created on the fly (no pre-existing fault at
            the swept bus). Defaults to ``{f: 1.0}`` for every simulation
            frequency. Existing faults are reused unmodified.
        run_fault_kwargs: Extra keyword arguments forwarded to
            :func:`run_fault` at every step.

    Returns:
        Dict mapping each ``bus_name`` to its RMS EPR in volts at that
        bus when it is the active fault.

    Raises:
        ValueError: If ``bus_names`` is empty, ``k`` does not have
            length 5, or any name is not in the network.
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
            result_bus = next(
                rb for rb in network.results[fname].buses if rb.name == b
            )
            epr_per_bus[b] = float(result_bus.uepr)
    finally:
        # Restore bus impedances regardless of success/failure.
        for b in bus_names:
            network.buses[b].impedance = impedance_backup[b]
        # Drop temporary faults and any results they produced.
        for fname in temp_faults_created:
            network.faults.pop(fname, None)
            network.results.pop(fname, None)
        # Restore the path cache (cleared at the start of the sweep).
        network.paths.clear()
        network.paths.update(paths_backup)
        # Restore active_fault best-effort.
        if active_fault_backup is not None:
            try:
                network.active_fault = active_fault_backup
            except Exception:  # pragma: no cover -- defensive
                logger.warning(
                    "Could not restore active_fault to %r.", active_fault_backup
                )

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
    at or below ``u_limit``. ``k_ref`` is typically a fit produced by
    :func:`groundmeas.services.analytics.rho_f_model` from measured
    impedance points; this function answers *how much head-room the
    network has around that characteristic*.

    Args:
        network: The simulation network. Buses, branches, sources and
            paths must already be configured.
        bus_names: Buses whose impedance is parameterised by ``k`` *and*
            which are swept as fault locations.
        u_limit: Upper bound on the RMS EPR (in volts) at any swept bus.
            Must be strictly positive.
        k_ref: Reference 5-tuple ``(k1_ref, ..., k5_ref)``. Must have
            length 5 and not be the zero vector.
        c_bounds: Search interval for ``c``. Strictly positive,
            ``c_bounds[0] < c_bounds[1]``. Defaults to ``(1e-3, 1e3)``.
        tol_rel: Bisection tolerance on the relative bracket width
            ``(c_hi - c_lo) / c_lo``. Defaults to ``1e-3``.
        max_iter: Hard cap on bisection steps. Defaults to ``60``.
        fault_scalings: See :func:`evaluate_max_epr_under_k`.
        run_fault_kwargs: Forwarded to :func:`run_fault`.

    Returns:
        Dict with keys

        - ``"c_max"`` (float): Largest scaling factor compatible with
          ``u_limit`` within the bracket.
        - ``"k_max"`` (Tuple[float, ...]): ``c_max * k_ref``.
        - ``"max_epr_rms_at_c_max"`` (float): Maximum RMS EPR across all
          swept buses at ``c_max``, in volts.
        - ``"epr_rms_per_bus_at_c_max"`` (Dict[str, float]): RMS EPR
          per swept bus at ``c_max``.
        - ``"iterations"`` (int): Number of bisection steps taken
          (``0`` if the upper bracket bound is already admissible).

    Raises:
        ValueError: For invalid input or if ``c = c_bounds[0]`` already
            violates the EPR limit.

    Examples:
        >>> import groundinsight as gi
        >>> from groundinsight.models.core_models import BusType, BranchType
        >>> from groundinsight.analysis import find_max_rho_f_scaling
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
        >>> # k_ref reproducing the original Z = 0.01*rho on b0/b1.
        >>> res = find_max_rho_f_scaling(
        ...     net, ["b0", "b1"], u_limit=200.0,
        ...     k_ref=(0.01, 0.0, 0.0, 0.0, 0.0),
        ... )
        >>> isinstance(res["c_max"], float)
        True
    """
    if u_limit <= 0:
        raise ValueError(f"u_limit must be positive, got {u_limit!r}.")
    if not bus_names:
        raise ValueError("bus_names must not be empty.")
    if len(k_ref) != 5:
        raise ValueError(f"k_ref must be a 5-tuple, got length {len(k_ref)}.")
    if all(x == 0 for x in k_ref):
        raise ValueError("k_ref must not be the zero vector.")
    c_lo_init, c_hi_init = c_bounds
    if not (0 < c_lo_init < c_hi_init):
        raise ValueError(
            f"c_bounds must satisfy 0 < c_lo < c_hi, got {c_bounds!r}."
        )

    last_epr_per_bus: Dict[str, Dict[str, float]] = {"value": {}}

    def _max_epr_at(c: float) -> float:
        eprs = evaluate_max_epr_under_k(
            network, bus_names,
            k=tuple(c * x for x in k_ref),
            fault_scalings=fault_scalings,
            run_fault_kwargs=run_fault_kwargs,
        )
        last_epr_per_bus["value"] = eprs
        return max(eprs.values()) if eprs else 0.0

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

    return {
        "c_max": c_max,
        "k_max": tuple(c_max * x for x in k_ref),
        "max_epr_rms_at_c_max": epr_at,
        "epr_rms_per_bus_at_c_max": epr_per_bus_at,
        "iterations": iterations,
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

    Args:
        network: The simulation network. Sources, branches and the buses
            to be swept must already be configured.
        bus_names: Buses whose impedance is parameterised by every
            candidate ``k`` and which are swept as fault locations.
        u_limit: Upper bound on the RMS EPR (in volts) at any swept bus.
            Must be strictly positive.
        candidates: Mapping ``{name: (k1, k2, k3, k4, k5)}`` of the
            candidate rho-f characteristics. Names must be unique
            (Python dict guarantees that), tuples must each have length
            5. May be empty -- the result is then an empty DataFrame
            with the documented schema.
        fault_scalings: Frequency-resolved scalings for any fault that
            has to be created on the fly. See
            :func:`evaluate_max_epr_under_k`.
        run_fault_kwargs: Forwarded to :func:`run_fault`.
        sort_by: How to sort the result rows.

            - ``"max_epr_asc"`` (default): admissible candidates first,
              tightest-EPR-margin candidates at the top.
            - ``"max_epr_desc"``: largest EPR first; useful to inspect
              the worst-case candidates.
            - ``"name"``: lexicographic by candidate name.

    Returns:
        A Polars DataFrame with one row per candidate and the columns

        - ``"name"`` (str)
        - ``"k1", "k2", "k3", "k4", "k5"`` (float)
        - ``"max_epr_rms_V"`` (float): maximum RMS EPR across the bus
          sweep, in volts.
        - ``"admissible"`` (bool): ``True`` iff
          ``max_epr_rms_V <= u_limit``.
        - one ``"epr_<bus>_V"`` column per swept bus, holding the RMS
          EPR at that bus when it is the active fault, in volts.

    Raises:
        ValueError: If ``u_limit`` is non-positive, ``bus_names`` is
            empty, or any candidate has a wrong-length ``k`` or refers
            to an unknown bus (the underlying helper validates this).

    Examples:
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
    if u_limit <= 0:
        raise ValueError(f"u_limit must be positive, got {u_limit!r}.")
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

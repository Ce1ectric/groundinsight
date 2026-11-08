# analysis/inverse_rho.py

"""
The maximum admissible soil resistivity at selected buses, found inversely.

Given an existing network with a configured fault and sources, this
module computes the largest uniform scaling factor ``c`` of the selected
buses' specific earth resistance ``rho`` such that the RMS earth potential
rise (EPR) at the fault bus stays at or below a user-supplied limit
``u_limit``. The resulting ``rho_max[bus] = c * rho_0[bus]`` characterises
the worst-case soil condition the network can tolerate while still
satisfying the EPR limit.

The shape of the rho-f curve at each bus is controlled entirely by the
existing :attr:`BusType.impedance_formula`; this module only varies the
scalar ``rho`` per bus, so any user-defined parametric form (linear,
square root, frequency-dependent, ...) is supported transparently.

Algorithm:

1. Snapshot the original ``rho_0[bus]`` for every selected bus.
2. Bisect ``c`` on a logarithmic axis (geometric mean step) inside
   ``c_bounds``: at every trial value, set ``rho = c * rho_0`` for each
   selected bus, recompute its bus impedance, run the fault, and read the
   RMS EPR at the fault bus.
3. Stop when the relative width of the bracket is below ``tol_rel`` or
   ``max_iter`` is reached.
4. Restore the original ``rho_0`` values regardless of success or
   failure (via a ``finally`` block).

The result reports *which* of those stopping conditions applied. That
distinction is not cosmetic: ``c_max`` is always a scaling factor whose
EPR was measured and found admissible, but only a converged search has
also shown that nothing meaningfully larger is admissible. A search that
ran out of steps, and a search whose whole bracket turned out to be
admissible, both return an honest ``c_max`` that is *not* the answer to
the question the caller asked. Read ``"converged"`` before ``"c_max"``.

Examples
--------
>>> import groundinsight as gi
>>> from groundinsight.models.core_models import BusType, BranchType
>>> bt = BusType(name="BT", system_type="Grounded", voltage_level=20.0,
...              impedance_formula="rho * 0.01 + I * f * 0")
>>> brt = BranchType(name="BRT", grounding_conductor=True,
...                  self_impedance_formula="(0.25 + I*0.6)*l",
...                  mutual_impedance_formula="(0.0 + I*0.6)*l")
>>> net = gi.create_network(name="Demo", frequencies=[50])
>>> _ = gi.create_bus(name="b0", type=bt, network=net)
>>> _ = gi.create_bus(name="b1", type=bt, network=net)
>>> _ = gi.create_branch(name="br", type=brt, from_bus="b0", to_bus="b1",
...                      length=1.0, network=net)
>>> _ = gi.create_source(name="src", bus="b0", values={50: 100.0}, network=net)
>>> _ = gi.create_fault(name="flt", bus="b1", scalings={50: 1.0}, network=net)
>>> from groundinsight.analysis import find_max_rho_scaling
>>> result = find_max_rho_scaling(net, "flt", ["b0", "b1"], u_limit=200.0)
>>> sorted(result.keys())
['bracket_rel_width', 'c_bracket', 'c_max', 'converged', 'iterations', 'rho_max', 'status', 'u_epr_rms_at_c_max']
"""

import logging
import math
from typing import Any, Callable, Dict, List, Optional, Tuple

from .._state import NetworkState
from ..models.core_models import Network
from ..network_operations import run_fault
from ..progress import StudyHooks
from ..utils.compat import renamed_parameters
from ._session import BusImpedanceSession, bus_impedance_at
from ._bisection import (
    STATUS_BRACKET_FULLY_ADMISSIBLE,
    STATUS_MAX_ITER_REACHED,
    STATUS_NON_MONOTONE,
    classify,
    is_monotone,
    log_grid,
    planned_steps,
    report,
    validate_c_bounds,
    validate_grid_points,
    validate_limit,
    validate_max_iter,
    validate_tol_rel,
)


logger = logging.getLogger(__name__)


def _non_monotone_result(
    grid: Tuple[float, ...],
    grid_eprs: Tuple[float, ...],
    u_limit: float,
    tol_rel: float,
    max_iter: int,
    evaluate,
    rho_0: Dict[str, float],
    bus_names: List[str],
    c_bounds: Tuple[float, float],
) -> Dict[str, Any]:
    """
    Answer for a bracket in which ``EPR(c)`` turns.

    A bisection assumes one crossing and finds it by discarding half the
    bracket at every step; with two or more crossings the half it discards may
    be the one holding the answer. That is not a tolerance problem and cannot
    be fixed by more steps -- so the search stops claiming a maximum and
    reports what it actually measured: the largest grid point that satisfied
    the limit, refined upwards inside its own interval, where the crossing
    *is* bracketed and a bisection is valid again.

    Parameters
    ----------
    grid, grid_eprs : tuple of float
        The pre-scan and its EPR values, in order of increasing ``c``.
    u_limit, tol_rel, max_iter
        As in :func:`find_max_rho_scaling`.
    evaluate : callable
        ``c -> EPR``, the same closure the bisection uses.
    rho_0 : dict of str to float
        Original rho per bus, for ``rho_max``.
    bus_names : list of str
        The scaled buses.
    c_bounds : tuple of (float, float)
        The original bracket, for the message.

    Returns
    -------
    dict
        The same shape :func:`find_max_rho_scaling` returns, with
        ``status = "non_monotone"`` and ``converged = False``.

    Raises
    ------
    ValueError
        If no grid point satisfies the limit.
    """
    admissible = [i for i, epr in enumerate(grid_eprs) if epr <= u_limit]
    if not admissible:
        raise ValueError(
            f"u_limit={u_limit:g} V is below the EPR at every one of the "
            f"{len(grid)} scanned factors in {c_bounds!r} (EPR from "
            f"{min(grid_eprs):g} to {max(grid_eprs):g} V). EPR(c) is not "
            f"monotone over this bracket, so a factor between two scanned "
            f"points could still satisfy the limit -- raise "
            f"monotonicity_grid to look more closely before concluding that "
            f"none does."
        )

    index = admissible[-1]
    c_lo, epr_lo = grid[index], grid_eprs[index]
    if index + 1 < len(grid):
        c_hi = grid[index + 1]
        iterations = 0
        while iterations < max_iter and (c_hi - c_lo) / c_lo > tol_rel:
            c_mid = math.sqrt(c_lo * c_hi)
            epr_mid = evaluate(c_mid)
            if epr_mid <= u_limit:
                c_lo, epr_lo = c_mid, epr_mid
            else:
                c_hi = c_mid
            iterations += 1
        bracket = (c_lo, c_hi)
        width = (c_hi - c_lo) / c_lo
    else:
        # The top of the bracket is admissible, so nothing above it was seen.
        iterations = 0
        bracket = (c_lo, math.inf)
        width = math.inf

    logger.warning(
        "EPR(c) is not monotone over c_bounds=%r: the scan of %d factors "
        "found it falling and rising again (%g to %g V). A bisection assumes "
        "one crossing and would discard the half of the bracket that holds "
        "the answer, so it was not run. c_max=%g is the largest scanned "
        "factor that satisfied u_limit=%g V, refined inside its own grid "
        "interval; factors between two scanned points may satisfy it as "
        "well. Raise monotonicity_grid for a finer scan, or narrow c_bounds "
        "to a stretch where the curve is monotone.",
        c_bounds, len(grid), min(grid_eprs), max(grid_eprs), c_lo, u_limit,
    )
    return {
        "c_max": c_lo,
        "u_epr_rms_at_c_max": epr_lo,
        "rho_max": {b: c_lo * rho_0[b] for b in bus_names},
        "iterations": iterations,
        "status": STATUS_NON_MONOTONE,
        "converged": False,
        "c_bracket": bracket,
        "bracket_rel_width": width,
    }


@renamed_parameters(fault_name="fault", u_max="u_limit")
def find_max_rho_scaling(
    network: Network,
    fault: str,
    bus_names: List[str],
    u_limit: float,
    *,
    c_bounds: Tuple[float, float] = (1e-3, 1e3),
    tol_rel: float = 1e-3,
    max_iter: int = 60,
    monotonicity_grid: int = 9,
    run_fault_kwargs: Optional[Dict[str, Any]] = None,
    progress: Optional[Callable[[int, int, str], None]] = None,
    should_cancel: Optional[Callable[[], bool]] = None,
) -> Dict[str, Any]:
    """
    Find the largest uniform rho-scaling factor compatible with an EPR limit.

    Bisects the scalar ``c`` such that scaling every selected bus'
    specific earth resistance to ``c * rho_0`` yields an RMS earth
    potential rise at the fault bus that just satisfies
    ``|u_EPR|_rms <= u_limit``. The bus impedance formula is re-evaluated
    through the existing :meth:`Bus.calculate_impedance` machinery, so
    any user-defined rho-f characteristic is honoured.

    Parameters
    ----------
    network : Network
        The simulation network. Must already contain the named fault, the
        sources and the buses listed in ``bus_names``. Paths are not
        needed: the default ``phase_current_mode="auto"`` of
        :func:`run_fault` solves the phase-conductor network, and the legacy
        ``"paths"`` mode (through ``run_fault_kwargs``) enumerates the paths
        it needs itself.
    fault : str
        Name of the fault to evaluate. Used as ``active_fault`` during
        every bisection step. The former keyword ``fault_name`` is accepted
        as a deprecated alias.
    bus_names : list of str
        Names of the buses whose specific earth resistance is uniformly
        scaled by the same factor ``c``. Must be non-empty and refer to
        buses in ``network``.
    u_limit : float
        Upper bound on the RMS earth potential rise at the fault bus, in
        volts. Must be finite and strictly positive. The RMS is taken
        over all simulation frequencies, matching :attr:`ResultBus.uepr`.
        The former keyword ``u_max`` is accepted as a deprecated alias.
    c_bounds : tuple of (float, float), optional
        Search interval for the scaling factor ``c``. Both bounds must be
        finite and strictly positive and ``c_bounds[0] < c_bounds[1]``.
        Defaults to ``(1e-3, 1e3)``, i.e. six decades.
    tol_rel : float, optional
        Relative tolerance on the bracket width
        ``(c_hi - c_lo) / c_lo`` at which the bisection terminates. Must
        be finite and strictly positive. Defaults to ``1e-3``.
    max_iter : int, optional
        Hard cap on the number of bisection steps. Must be an ``int``
        of at least 1. Defaults to ``60``, which is roughly four times
        what the default bracket and tolerance need.
    monotonicity_grid : int, optional
        Number of factors evaluated on a logarithmic grid across
        ``c_bounds`` *before* the bisection, to test the monotonicity
        the bisection assumes. Must be ``0`` (no scan) or at least 3;
        defaults to ``9``. The scan is not free -- it costs one solve
        per point -- but it pays part of itself back: on
        a monotone curve the bisection starts from the one grid
        interval the threshold falls in rather than from the whole
        bracket, which is worth ``log2(monotonicity_grid - 1)`` steps.
        Set it to ``0`` only where monotonicity is known from the
        model (e.g. an impedance formula in which *every* term scales
        with ``rho``).
    run_fault_kwargs : dict, optional
        Extra keyword arguments forwarded to :func:`run_fault` (e.g.
        ``{"phase_current_mode": "paths"}``). Defaults to ``None``.
    progress : callable, optional
        ``progress(done, total, label)``, called after every solve (scan
        points and bisection steps alike) with the trial factor as label.
        ``total`` is the evaluation count the bracket and tolerance require;
        see :mod:`groundinsight.progress`.
    should_cancel : callable, optional
        Asked before every solve; a true answer raises
        :class:`~groundinsight.progress.StudyCancelled`.

    Returns
    -------
    dict
        Mapping with keys

        - ``"c_max"`` (float): a scaling factor whose EPR was evaluated
          and found to satisfy the constraint. **This is a guarantee in
          one direction only**: ``c_max`` is always admissible, but it is
          the *largest* admissible factor only when ``"converged"`` is
          ``True``.
        - ``"u_epr_rms_at_c_max"`` (float): the RMS EPR at the fault bus
          evaluated at ``c_max``, in volts.
        - ``"rho_max"`` (dict of str to float): ``c_max * rho_0[bus]`` for
          every selected bus.
        - ``"iterations"`` (int): number of bisection steps taken.
        - ``"converged"`` (bool): ``True`` iff the search closed the
          bracket to within ``tol_rel`` around the threshold. Check this
          before using ``c_max`` as a design value.
        - ``"status"`` (str): which stopping condition applied --
          ``"converged"``, ``"bracket_within_tol_on_entry"``,
          ``"max_iter_reached"``, ``"bracket_fully_admissible"`` or
          ``"non_monotone"``. See
          :mod:`groundinsight.analysis._bisection`.
        - ``"c_bracket"`` (tuple of float): the interval that provably
          contains the true maximum admissible factor. For
          ``"bracket_fully_admissible"`` this is ``(c_hi, inf)``: nothing
          above the bracket was ever evaluated, so widen ``c_bounds``.
        - ``"bracket_rel_width"`` (float): ``(c_hi - c_lo) / c_lo`` of
          that interval, directly comparable against ``tol_rel``, and
          ``inf`` for ``"bracket_fully_admissible"``.

        ``iterations == 0`` on its own does **not** identify a case: it is
        produced by a fully admissible bracket, by a bracket that was
        already narrower than ``tol_rel``, and (before the guards below
        existed) by a cap of zero steps -- outcomes whose ``c_max`` came
        from opposite ends of the bracket. Use ``"status"``.

    Raises
    ------
    ValueError
        If ``u_limit``, ``tol_rel``, ``max_iter``, ``monotonicity_grid`` or
        ``c_bounds`` is invalid (see the parameter descriptions --
        non-finite values are rejected, not just out-of-range ones),
        ``bus_names`` is empty, any name is not in the network, the EPR at
        the lower bound ``c_bounds[0]`` already exceeds ``u_limit``, or the
        model returns a non-finite EPR at some trial factor.
    TypeError
        If ``progress`` or ``should_cancel`` is not callable.
    StudyCancelled
        If ``should_cancel()`` returned true.

    Notes
    -----
    **The assumption, and what happens when it does not hold.** A bisection
    finds *the* crossing of ``EPR(c)`` with ``u_limit``, which presumes there
    is only one -- i.e. that ``EPR`` rises monotonically with ``c``. That is
    the usual case but not a theorem: this function scales the scalar ``rho``,
    and an impedance formula in which only part of the expression carries
    ``rho`` (``"0.02*rho + 0.1*f*j"``, say) leaves the reactance fixed. The
    network admittance ``|Y_net| = |G + 1/(R + jX)|`` then has a *maximum* at
    ``R = (-1 + sqrt(1 + 4 G^2 X^2)) / (2 G)``, so the EPR has a minimum
    there and falls before it rises. Around that dip a bisection can discard
    the half of the bracket that holds the answer, and it did: the review's
    case reported "no scaling factor satisfies the constraint" while ``c = 1``
    satisfied it.

    ``monotonicity_grid`` scans for exactly that before the bisection starts.
    Where the curve turns, the result comes back with
    ``status = "non_monotone"`` and ``converged = False``: ``c_max`` is then
    the largest *measured* admissible factor, refined inside its grid
    interval, and a lower bound on the true maximum rather than the maximum.
    Its resolution is the grid's.

    **Cost.** Only the first trial is a full :func:`run_fault`. A trial
    changes nothing but the grounding impedances of the selected buses, which
    enter the nodal matrix on its diagonal only, so every later trial is
    re-solved on what the first one assembled -- injection vectors, phase
    currents, stamp pattern -- by re-assembling the matrix values and
    factorising them (P4, :mod:`groundinsight.analysis._session`). The EPR of
    a trial is bit for bit the one a full ``run_fault`` reports, so the
    search takes the same steps; it is about seven times faster on a
    1000-bus ring with every bus scaled. A trial the re-solve cannot answer
    (a 0 Hz stand-in, a bus that gains or loses its electrode, a value the
    model rejects) is a full ``run_fault`` again, with its errors.

    Examples
    --------
    Two stations whose electrodes follow the soil (``rho * 0.01``): by which
    factor ``c`` may the resistivity around both change before the EPR at the
    fault exceeds 5 V?

    >>> import groundinsight as gi
    >>> net = gi.create_network(name="station", frequencies=[50.0])
    >>> grid = gi.BusType(name="grid", system_type="Substation", voltage_level=20.0,
    ...                   impedance_formula="rho * 0.01")
    >>> line = gi.BranchType(name="line", grounding_conductor=True,
    ...                      self_impedance_formula="0.25 * l",
    ...                      mutual_impedance_formula="0 * l")
    >>> for name in ("B1", "B2"):
    ...     _ = gi.create_bus(name=name, type=grid, network=net)
    >>> _ = gi.create_branch(name="L1", type=line, from_bus="B1", to_bus="B2",
    ...                      length=1.0, network=net)
    >>> _ = gi.create_source(name="S1", bus="B1", values={50.0: 100.0}, network=net)
    >>> _ = gi.create_fault(name="F1", bus="B2", network=net)
    >>> report = gi.find_max_rho_scaling(net, "F1", ["B1", "B2"], u_limit=5.0)
    >>> report["status"], round(report["c_max"], 3), report["u_epr_rms_at_c_max"] <= 5.0
    ('converged', 0.083, True)
    """
    validate_limit(u_limit, "u_limit")
    validate_tol_rel(tol_rel)
    validate_max_iter(max_iter)
    validate_grid_points(monotonicity_grid)
    if not bus_names:
        raise ValueError("bus_names must not be empty.")
    c_lo_init, c_hi_init = validate_c_bounds(c_bounds)
    missing = [b for b in bus_names if b not in network.buses]
    if missing:
        raise ValueError(f"Unknown bus(es) in network: {missing!r}.")
    if fault not in network.faults:
        raise ValueError(f"Unknown fault {fault!r} in network.")

    rfk: Dict[str, Any] = dict(run_fault_kwargs) if run_fault_kwargs else {}

    # The evaluation count is known before the first solve: the scan, plus the
    # halvings that close one scan interval (the whole bracket without a scan)
    # to tol_rel -- a log-bisection needs the same number wherever the
    # threshold lies.
    n_scan = len(log_grid(c_lo_init, c_hi_init, monotonicity_grid))
    interval = (c_hi_init / c_lo_init) ** (1.0 / max(n_scan - 1, 1))
    hooks = StudyHooks(
        "find_max_rho_scaling",
        n_scan + planned_steps(1.0, interval, tol_rel, max_iter),
        progress,
        should_cancel,
    )

    fault_bus_name = network.faults[fault].bus

    # Snapshot original rhos so the network can be restored at the end.
    rho_0: Dict[str, float] = {
        b: float(network.buses[b].specific_earth_resistance) for b in bus_names
    }
    # Snapshot the state run_fault will mutate so the search leaves the network
    # exactly as it found it (the returned figures are already final).
    active_fault_backup = network.active_fault
    result_backup = network.results.get(fault)
    had_result = fault in network.results
    # The assembled nodal system is *not* recomputed by anything the caller
    # does afterwards -- ``bus_response`` and ``analyze_cuts`` read it back as
    # it stands. Every ``run_fault`` below replaces it with the matrices of a
    # trial rho, so without this backup the network would be left carrying the
    # Y of the last bisection step next to the restored rho. That combination
    # is not merely stale, it is inconsistent: it gives a *negative*
    # driving-point impedance in the review case. The pre-search object was
    # built from exactly the state restored below, so putting it back is
    # correct rather than merely conservative.
    electrical_backup = network.electrical_network

    # A trial changes nothing but the grounding impedances of the selected
    # buses, i.e. the diagonal of Y. The first trial is a full run_fault; the
    # others are re-solved on what it assembled (P4, see analysis._session):
    # the same matrix values, factorisation and RMS, so the same EPR bit for
    # bit. A trial the session cannot answer is solved in full again, and the
    # session then continues from that solve.
    session_box: Dict[str, Optional[BusImpedanceSession]] = {"session": None}

    def _session_epr(c: float) -> Optional[float]:
        """The EPR of a trial from the session, or ``None`` for a full solve."""
        session = session_box["session"]
        if session is None:
            return None
        try:
            impedances = {
                b: bus_impedance_at(
                    network.buses[b], c * rho_0[b], network.frequencies
                )
                for b in bus_names
            }
        except Exception:  # noqa: BLE001 -- the full solve raises it in its own words
            return None
        return session.epr_rms(impedances)

    def _epr_rms_at(c: float) -> float:
        """Evaluate the RMS EPR at the fault bus for a given scaling factor."""
        hooks.checkpoint()
        epr = _session_epr(c)
        if epr is None:
            for b in bus_names:
                bus = network.buses[b]
                bus.specific_earth_resistance = c * rho_0[b]
                bus.calculate_impedance(network.frequencies)
            run_fault(network, fault=fault, **rfk)
            result_bus = next(
                rb
                for rb in network.results[fault].buses
                if rb.name == fault_bus_name
            )
            epr = float(result_bus.uepr)
            session_box["session"] = BusImpedanceSession.attach(network, fault)
        if not math.isfinite(epr):
            # A non-finite EPR cannot be compared against u_limit: every
            # comparison is False, so the bisection would take the same turn
            # at every step and walk silently to the lower bracket bound.
            # The impedance pipeline raises on NaN before a formula can get
            # this far today, so this is a second lock on the same door --
            # it defends the search below it, not the model above it.
            raise ValueError(
                f"The EPR at bus {fault_bus_name!r} evaluated to {epr!r} for "
                f"the scaling factor c={c:g}. A non-finite EPR cannot be "
                "compared against u_limit -- every comparison would be False "
                "and the bisection would walk to the lower bracket bound "
                "without ever raising. This is a model problem, not a search "
                "problem: check the bus impedance formula at the scaled rho."
            )
        hooks.advance(f"c = {c:.6g}")
        return epr

    # Everything above restores the network field by field; this records the
    # network-level state as a whole, so the restore in ``finally`` is exact
    # -- result order, path set and private state included (GUI5).
    state = NetworkState(network)
    iterations = 0
    try:
        c_lo, c_hi = c_lo_init, c_hi_init
        # Pre-scan. The bisection needs EPR(c) to rise with c, and the rho
        # sweep does not always: only the resistive part of the bus impedance
        # scales, so |Y_net| = |G + 1/(R + jX)| passes through a maximum at
        # R = (-1 + sqrt(1 + 4 G^2 X^2)) / (2G) and the EPR through a minimum
        # there. The scan measures whether that happens inside this bracket
        # instead of assuming it does not.
        grid = log_grid(c_lo, c_hi, monotonicity_grid)
        grid_eprs = tuple(_epr_rms_at(c) for c in grid)
        monotone = monotonicity_grid < 3 or is_monotone(grid_eprs)
        epr_lo, epr_hi = grid_eprs[0], grid_eprs[-1]

        if not monotone:
            return _non_monotone_result(
                grid, grid_eprs, u_limit, tol_rel, max_iter, _epr_rms_at,
                rho_0, bus_names, c_bounds,
            )

        # Monotone: the scan also narrows the bracket to the one grid interval
        # the threshold falls in, which pays back part of its own cost --
        # every interval skipped is a bisection step not taken.
        for value, epr in zip(grid, grid_eprs, strict=True):
            if epr <= u_limit:
                c_lo, epr_lo = value, epr
            else:
                c_hi, epr_hi = value, epr
                break

        if epr_lo > u_limit:
            raise ValueError(
                f"u_limit={u_limit:g} V is below the EPR at c_min={c_lo:g}: "
                f"|u_EPR|_rms(c_min)={epr_lo:g} V — no scaling factor in "
                f"the bracket {c_bounds!r} satisfies the constraint."
            )
        if epr_hi <= u_limit:
            # Whole bracket is admissible. c_hi is a real, measured answer,
            # but it is a lower bound on the true maximum, not the maximum:
            # nothing above c_hi was evaluated. The status says so, and
            # ``c_bracket`` comes back as (c_hi, inf) to make that
            # machine-readable.
            logger.info(
                "Bracket fully admissible: |u_EPR|_rms(c_hi=%g)=%g V <= "
                "u_limit=%g V. Consider widening c_bounds.",
                c_hi, epr_hi, u_limit,
            )
            c_max, epr_at = c_hi, epr_hi
            status = STATUS_BRACKET_FULLY_ADMISSIBLE
        else:
            # From here on epr(c_lo) <= u_limit < epr(c_hi) holds and is
            # preserved by every step, so c_lo is always a *verified*
            # admissible factor and the threshold stays bracketed.
            while iterations < max_iter and (c_hi - c_lo) / c_lo > tol_rel:
                c_mid = math.sqrt(c_lo * c_hi)  # geometric mean -> log bisection
                epr_mid = _epr_rms_at(c_mid)
                if epr_mid <= u_limit:
                    c_lo, epr_lo = c_mid, epr_mid
                else:
                    c_hi, epr_hi = c_mid, epr_mid
                iterations += 1
            c_max, epr_at = c_lo, epr_lo
            status = classify(iterations, c_lo, c_hi, tol_rel)
            # Ask the classifier rather than re-deriving the same condition:
            # two copies of "did it close?" is exactly how a status and its
            # log message drift apart.
            if status == STATUS_MAX_ITER_REACHED:
                logger.warning(
                    "Bisection stopped at the step cap max_iter=%d without "
                    "closing the bracket: c in [%g, %g], relative width %g > "
                    "tol_rel=%g. c_max=%g is admissible but may be well below "
                    "the true maximum.",
                    max_iter, c_lo, c_hi, (c_hi - c_lo) / c_lo, tol_rel, c_lo,
                )
    finally:
        # Restore original rhos and recompute their impedances no matter what.
        for b in bus_names:
            bus = network.buses[b]
            bus.specific_earth_resistance = rho_0[b]
            bus.calculate_impedance(network.frequencies)
        # Restore the result cache and active fault so no trace of the search
        # remains on the network.
        if had_result:
            network.results[fault] = result_backup
        else:
            network.results.pop(fault, None)
        if active_fault_backup is None:
            network.active_fault = None
            for _flt in network.faults.values():
                _flt.mark_active(False)
        elif active_fault_backup in network.faults:
            network.set_active_fault(active_fault_backup, keep_results=True)
        # Restore the assembled system last: it has to agree with the rho,
        # results and active fault put back above. If the network carried none
        # on entry it gets none back -- a stale matrix is worse than no matrix,
        # because the readers above it raise on the second and not on the first.
        network.electrical_network = electrical_backup
        # And the network-level state exactly as recorded before the search.
        state.restore()

    return {
        "c_max": c_max,
        "u_epr_rms_at_c_max": epr_at,
        "rho_max": {b: c_max * rho_0[b] for b in bus_names},
        "iterations": iterations,
        **report(status, c_lo, c_hi),
    }

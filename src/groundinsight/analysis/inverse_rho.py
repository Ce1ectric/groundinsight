# analysis/inverse_rho.py

"""
Inverse determination of the maximum admissible rho-f characteristic at
selected buses.

Given an existing network with a configured fault, sources and paths, this
module computes the largest uniform scaling factor ``c`` of the selected
buses' specific earth resistance ``rho`` such that the RMS earth potential
rise (EPR) at the fault bus stays at or below a user-supplied limit
``u_max``. The resulting ``rho_max[bus] = c * rho_0[bus]`` characterises
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
>>> result = find_max_rho_scaling(net, "flt", ["b0", "b1"], u_max=200.0)
>>> sorted(result.keys())
['bracket_rel_width', 'c_bracket', 'c_max', 'converged', 'iterations', 'rho_max', 'status', 'u_epr_rms_at_c_max']
"""

import logging
import math
from typing import Any, Dict, List, Tuple

from ..models.core_models import Network
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


def find_max_rho_scaling(
    network: Network,
    fault_name: str,
    bus_names: List[str],
    u_max: float,
    *,
    c_bounds: Tuple[float, float] = (1e-3, 1e3),
    tol_rel: float = 1e-3,
    max_iter: int = 60,
    run_fault_kwargs: Dict[str, Any] = None,
) -> Dict[str, Any]:
    """
    Find the largest uniform rho-scaling factor compatible with an EPR limit.

    Bisects the scalar ``c`` such that scaling every selected bus'
    specific earth resistance to ``c * rho_0`` yields an RMS earth
    potential rise at the fault bus that just satisfies
    ``|u_EPR|_rms <= u_max``. The bus impedance formula is re-evaluated
    through the existing :meth:`Bus.calculate_impedance` machinery, so
    any user-defined rho-f characteristic is honoured.

    Parameters
    ----------
    network : Network
        The simulation network. Must already contain the named fault, the
        sources, the buses listed in ``bus_names`` and consistent paths
        from sources to fault.
    fault_name : str
        Name of the fault to evaluate. Used as ``active_fault`` during
        every bisection step.
    bus_names : list of str
        Names of the buses whose specific earth resistance is uniformly
        scaled by the same factor ``c``. Must be non-empty and refer to
        buses in ``network``.
    u_max : float
        Upper bound on the RMS earth potential rise at the fault bus, in
        volts. Must be finite and strictly positive. The RMS is taken
        over all simulation frequencies, matching :attr:`ResultBus.uepr`.
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
    run_fault_kwargs : dict, optional
        Extra keyword arguments forwarded to :func:`run_fault` at every
        step (e.g. ``{"auto_parallel_coefficients": True}``). Defaults to
        ``None``.

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
          ``"max_iter_reached"`` or ``"bracket_fully_admissible"``. See
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
        If ``u_max``, ``tol_rel``, ``max_iter`` or ``c_bounds`` is
        invalid (see the parameter descriptions -- non-finite values are
        rejected, not just out-of-range ones), ``bus_names`` is empty,
        any name is not in the network, the EPR at the lower bound
        ``c_bounds[0]`` already exceeds ``u_max``, or the model returns a
        non-finite EPR at some trial factor.

    Examples
    --------
    >>> import groundinsight as gi  # doctest: +SKIP
    >>> res = gi.find_max_rho_scaling(  # doctest: +SKIP
    ...     network=net, fault_name="flt",
    ...     bus_names=["b0", "b1"], u_max=200.0,
    ... )
    >>> if not res["converged"]:  # doctest: +SKIP
    ...     print(res["status"], res["c_bracket"])
    """
    validate_limit(u_max, "u_max")
    validate_tol_rel(tol_rel)
    validate_max_iter(max_iter)
    if not bus_names:
        raise ValueError("bus_names must not be empty.")
    c_lo_init, c_hi_init = validate_c_bounds(c_bounds)
    missing = [b for b in bus_names if b not in network.buses]
    if missing:
        raise ValueError(f"Unknown bus(es) in network: {missing!r}.")
    if fault_name not in network.faults:
        raise ValueError(f"Unknown fault {fault_name!r} in network.")

    rfk: Dict[str, Any] = dict(run_fault_kwargs) if run_fault_kwargs else {}

    fault_bus_name = network.faults[fault_name].bus

    # Snapshot original rhos so the network can be restored at the end.
    rho_0: Dict[str, float] = {
        b: float(network.buses[b].specific_earth_resistance) for b in bus_names
    }
    # Snapshot the state run_fault will mutate so the search leaves the network
    # exactly as it found it (the returned figures are already final).
    active_fault_backup = network.active_fault
    result_backup = network.results.get(fault_name)
    had_result = fault_name in network.results

    def _epr_rms_at(c: float) -> float:
        """Evaluate the RMS EPR at the fault bus for a given scaling factor."""
        for b in bus_names:
            bus = network.buses[b]
            bus.specific_earth_resistance = c * rho_0[b]
            bus.calculate_impedance(network.frequencies)
        run_fault(network, fault_name=fault_name, **rfk)
        result_bus = next(
            rb
            for rb in network.results[fault_name].buses
            if rb.name == fault_bus_name
        )
        epr = float(result_bus.uepr)
        if not math.isfinite(epr):
            # A non-finite EPR cannot be compared against u_max: every
            # comparison is False, so the bisection would take the same turn
            # at every step and walk silently to the lower bracket bound.
            # The impedance pipeline raises on NaN before a formula can get
            # this far today, so this is a second lock on the same door --
            # it defends the search below it, not the model above it.
            raise ValueError(
                f"The EPR at bus {fault_bus_name!r} evaluated to {epr!r} for "
                f"the scaling factor c={c:g}. A non-finite EPR cannot be "
                "compared against u_max -- every comparison would be False "
                "and the bisection would walk to the lower bracket bound "
                "without ever raising. This is a model problem, not a search "
                "problem: check the bus impedance formula at the scaled rho."
            )
        return epr

    iterations = 0
    try:
        c_lo, c_hi = c_lo_init, c_hi_init
        epr_lo = _epr_rms_at(c_lo)
        if epr_lo > u_max:
            raise ValueError(
                f"u_max={u_max:g} V is below the EPR at c_min={c_lo:g}: "
                f"|u_EPR|_rms(c_min)={epr_lo:g} V — no scaling factor in "
                f"the bracket {c_bounds!r} satisfies the constraint."
            )
        epr_hi = _epr_rms_at(c_hi)
        if epr_hi <= u_max:
            # Whole bracket is admissible. c_hi is a real, measured answer,
            # but it is a lower bound on the true maximum, not the maximum:
            # nothing above c_hi was evaluated. The status says so, and
            # ``c_bracket`` comes back as (c_hi, inf) to make that
            # machine-readable.
            logger.info(
                "Bracket fully admissible: |u_EPR|_rms(c_hi=%g)=%g V <= "
                "u_max=%g V. Consider widening c_bounds.",
                c_hi, epr_hi, u_max,
            )
            c_max, epr_at = c_hi, epr_hi
            status = STATUS_BRACKET_FULLY_ADMISSIBLE
        else:
            # From here on epr(c_lo) <= u_max < epr(c_hi) holds and is
            # preserved by every step, so c_lo is always a *verified*
            # admissible factor and the threshold stays bracketed.
            while iterations < max_iter and (c_hi - c_lo) / c_lo > tol_rel:
                c_mid = math.sqrt(c_lo * c_hi)  # geometric mean -> log bisection
                epr_mid = _epr_rms_at(c_mid)
                if epr_mid <= u_max:
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
            network.results[fault_name] = result_backup
        else:
            network.results.pop(fault_name, None)
        if active_fault_backup is None:
            network.active_fault = None
            for _flt in network.faults.values():
                _flt._set_active(False)
        elif active_fault_backup in network.faults:
            network.set_active_fault(active_fault_backup, keep_results=True)

    return {
        "c_max": c_max,
        "u_epr_rms_at_c_max": epr_at,
        "rho_max": {b: c_max * rho_0[b] for b in bus_names},
        "iterations": iterations,
        **report(status, c_lo, c_hi),
    }

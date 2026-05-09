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

Examples:
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
    ['c_max', 'iterations', 'rho_max', 'u_epr_rms_at_c_max']
"""

import logging
import math
from typing import Any, Dict, List, Tuple

from ..models.core_models import Network
from ..network_operations import run_fault


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

    Bisects the scalar ``c`` such that scaling every selected bus' specific
    earth resistance to ``c * rho_0`` yields an RMS earth potential rise at
    the fault bus that just satisfies ``|u_EPR|_rms <= u_max``. The bus
    impedance formula is re-evaluated through the existing
    :meth:`Bus.calculate_impedance` machinery, so any user-defined rho-f
    characteristic is honoured.

    Args:
        network (Network): The simulation network. Must already contain the
            named fault, the sources, the buses listed in ``bus_names`` and
            consistent paths from sources to fault.
        fault_name (str): Name of the fault to evaluate. Used as
            ``active_fault`` during every bisection step.
        bus_names (List[str]): Names of the buses whose specific earth
            resistance is uniformly scaled by the same factor ``c``. Must
            be non-empty and refer to buses in ``network``.
        u_max (float): Upper bound on the RMS earth potential rise at the
            fault bus, in volts. Must be strictly positive. The RMS is
            taken over all simulation frequencies, matching
            :attr:`ResultBus.uepr`.
        c_bounds (Tuple[float, float], optional): Search interval for the
            scaling factor ``c``. Both bounds must be strictly positive,
            and ``c_bounds[0] < c_bounds[1]``. Defaults to
            ``(1e-3, 1e3)``, i.e. six decades.
        tol_rel (float, optional): Relative tolerance on the bracket width
            ``(c_hi - c_lo) / c_lo`` at which the bisection terminates.
            Defaults to ``1e-3``.
        max_iter (int, optional): Hard cap on the number of bisection
            steps. Defaults to ``60`` (largely sufficient given the log
            bracketing and ``tol_rel``).
        run_fault_kwargs (Dict[str, Any], optional): Extra keyword
            arguments forwarded to :func:`run_fault` at every step (e.g.
            ``{"auto_parallel_coefficients": True}``). Defaults to
            ``None``.

    Returns:
        Dict[str, Any]: A dictionary with keys

            - ``"c_max"`` (float): The largest scaling factor satisfying
              the EPR constraint within the bracket. If ``epr(c_hi) <=
              u_max`` already, the upper bound is returned and
              ``iterations`` is zero (the bracket should be widened).
            - ``"u_epr_rms_at_c_max"`` (float): The RMS EPR at the fault
              bus evaluated at ``c_max``, in volts.
            - ``"rho_max"`` (Dict[str, float]): ``c_max * rho_0[bus]``
              for every selected bus.
            - ``"iterations"`` (int): Number of bisection steps taken.

    Raises:
        ValueError: If ``u_max`` is not positive, ``bus_names`` is empty,
            ``c_bounds`` is invalid, any name is not in the network, or
            the EPR at the lower bound ``c_bounds[0]`` already exceeds
            ``u_max`` (constraint cannot be satisfied within the bracket).

    Examples:
        >>> import groundinsight as gi
        >>> from groundinsight.models.core_models import BusType, BranchType
        >>> from groundinsight.analysis import find_max_rho_scaling
        >>> bt = BusType(name="BT", system_type="Grounded",
        ...              voltage_level=20.0,
        ...              impedance_formula="rho * 0.01 + I * f * 0")
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
        >>> _ = gi.create_fault(name="flt", bus="b1",
        ...                     scalings={50: 1.0}, network=net)
        >>> res = find_max_rho_scaling(net, "flt", ["b0", "b1"],
        ...                            u_max=200.0)
        >>> isinstance(res["c_max"], float)
        True
    """
    if u_max <= 0:
        raise ValueError(f"u_max must be positive, got {u_max!r}.")
    if not bus_names:
        raise ValueError("bus_names must not be empty.")
    c_lo_init, c_hi_init = c_bounds
    if not (0 < c_lo_init < c_hi_init):
        raise ValueError(
            f"c_bounds must satisfy 0 < c_lo < c_hi, got {c_bounds!r}."
        )
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
        return float(result_bus.uepr)

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
            # Whole bracket is admissible. Return c_hi but flag with zero
            # iterations so the caller can recognise the boundary case.
            logger.info(
                "Bracket fully admissible: |u_EPR|_rms(c_hi=%g)=%g V <= "
                "u_max=%g V. Consider widening c_bounds.",
                c_hi, epr_hi, u_max,
            )
            c_max, epr_at = c_hi, epr_hi
        else:
            while iterations < max_iter and (c_hi - c_lo) / c_lo > tol_rel:
                c_mid = math.sqrt(c_lo * c_hi)  # geometric mean -> log bisection
                epr_mid = _epr_rms_at(c_mid)
                if epr_mid <= u_max:
                    c_lo, epr_lo = c_mid, epr_mid
                else:
                    c_hi, epr_hi = c_mid, epr_mid
                iterations += 1
            c_max, epr_at = c_lo, epr_lo
    finally:
        # Restore original rhos and recompute their impedances no matter what.
        for b in bus_names:
            bus = network.buses[b]
            bus.specific_earth_resistance = rho_0[b]
            bus.calculate_impedance(network.frequencies)

    return {
        "c_max": c_max,
        "u_epr_rms_at_c_max": epr_at,
        "rho_max": {b: c_max * rho_0[b] for b in bus_names},
        "iterations": iterations,
    }

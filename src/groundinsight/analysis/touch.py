# analysis/touch.py

r"""
The touch-voltage assessment of EN 50522 per bus.

EN 50522 accepts an installation when the touch voltage a person can bridge
stays below the permissible value for the fault duration. With an earthing
characteristic on a bus (:class:`~groundinsight.EarthingCharacteristic`) the
solve already gives the prospective touch voltage of every bus,
$U_\mathrm{vT} = k_\mathrm{touch} \cdot |U_E|$ (``ResultBus.u_touch``); this
module compares it with a limit and reports the utilisation
$U_\mathrm{vT} / U_\mathrm{limit}$.

Why the limit is an argument
----------------------------
The library carries no table of admissible values (see
:mod:`groundinsight.analysis.statistics`). The limit depends on the fault
duration, on the edition of the standard and on the additional resistances
assumed -- footwear, surface layer -- and a plausible constant baked in here
would end up in a report without ever being checked. The caller passes it and
cites it: one value in volts, or the curve ``U(t_F)`` as points
``[(t_F in s, U in V), ...]`` from the standard, interpolated between its
points on log-log axes (the axes the standard draws it on) and never beyond
them.

Which limit to pass
-------------------
$U_\mathrm{vT}$ here is the *prospective* touch voltage: the open-circuit
potential difference between the electrode and the earth surface 1 m away,
before any body or additional resistance. Comparing it with the permissible
touch voltage $U_\mathrm{Tp}$ itself is conservative; comparing it with the
prospective permissible value $U_\mathrm{vTp}$, which includes the additional
resistances of EN 50522 Annex B, is the refined check. Either is a valid
``u_limit`` -- the report should say which.

The RMS over all frequencies of the study is assessed, as for $U_E$ in
:meth:`Network.res_fault <groundinsight.Network.res_fault>`.
"""

import logging
import math
from typing import Any, Dict, List, Optional, Sequence, Tuple, Union

import polars as pl

from ..diagnostics import InputWarning, emit
from ..models.core_models import Network

logger = logging.getLogger(__name__)

__all__ = [
    "TOUCH_VOLTAGE_SCHEMA",
    "check_touch_voltages",
]

TOUCH_VOLTAGE_SCHEMA: Dict[str, Any] = {
    "bus_name": pl.Utf8,
    "fault": pl.Utf8,
    "EPR_V": pl.Float64,
    "U_vT_V": pl.Float64,
    "t_k_s": pl.Float64,
    "U_limit_V": pl.Float64,
    "utilisation": pl.Float64,
    "within_limit": pl.Boolean,
}
"""Column layout of :func:`check_touch_voltages`, in output order.

``EPR_V`` and ``U_vT_V`` are the RMS values of the stored result, ``t_k_s``
the fault duration the limit was taken at (null for a constant limit given
without one), ``U_limit_V`` the limit, ``utilisation`` = ``U_vT_V /
U_limit_V``, ``within_limit`` = ``utilisation <= 1``. A bus without a
tabulated touch factor has nulls from ``U_vT_V`` on."""

LimitCurve = Sequence[Tuple[float, float]]


def _curve_points(curve: LimitCurve) -> List[Tuple[float, float]]:
    """
    Validate a limit curve ``[(t_F, U), ...]``.

    Parameters
    ----------
    curve : sequence of (float, float)
        Durations in seconds and limits in volts.

    Returns
    -------
    list of tuple
        The points as floats.

    Raises
    ------
    ValueError
        If there are fewer than two points, a value is not finite and
        positive, or the durations are not strictly increasing.
    """
    points = [(float(t), float(u)) for t, u in curve]
    if len(points) < 2:
        raise ValueError("u_limit as a curve needs at least two points (t_F in s, U in V).")
    for t, u in points:
        if not (math.isfinite(t) and t > 0.0 and math.isfinite(u) and u > 0.0):
            raise ValueError(f"u_limit point ({t!r}, {u!r}) must be finite and positive.")
    if any(b[0] <= a[0] for a, b in zip(points[:-1], points[1:], strict=True)):
        raise ValueError("u_limit curve: the durations t_F must be strictly increasing.")
    return points


def limit_at(u_limit: Union[float, LimitCurve], t_k_s: Optional[float]) -> float:
    """
    The limit for a fault duration.

    Parameters
    ----------
    u_limit : float or sequence of (float, float)
        A constant in volts, or the curve ``[(t_F in s, U in V), ...]``.
    t_k_s : float, optional
        The fault duration in seconds; required for a curve.

    Returns
    -------
    float
        The limit in volts; for a curve, interpolated on log-log axes.

    Raises
    ------
    ValueError
        If the constant is not finite and positive, a curve is given without
        a duration, or the duration lies outside the curve.

    Examples
    --------
    >>> from groundinsight.analysis.touch import limit_at
    >>> limit_at(80.0, None)
    80.0
    >>> round(limit_at([(0.1, 600.0), (1.0, 100.0)], 0.1), 6)
    600.0
    """
    if isinstance(u_limit, (int, float)):
        value = float(u_limit)
        if not (math.isfinite(value) and value > 0.0):
            raise ValueError(f"u_limit must be finite and positive, got {u_limit!r}.")
        return value
    points = _curve_points(u_limit)
    if t_k_s is None:
        raise ValueError(
            "u_limit is a curve U(t_F); pass t_k_s or set Fault.t_k_s to choose the "
            "point of the curve."
        )
    t0, t1 = points[0][0], points[-1][0]
    if not t0 <= t_k_s <= t1:
        raise ValueError(
            f"t_k_s = {t_k_s:g} s lies outside the limit curve ({t0:g} s to {t1:g} s); "
            "a normative curve is not extrapolated -- extend it from the standard."
        )
    for (ta, ua), (tb, ub) in zip(points[:-1], points[1:], strict=True):
        if ta <= t_k_s <= tb:
            if t_k_s == ta:
                return ua
            if t_k_s == tb:
                return ub
            weight = math.log(t_k_s / ta) / math.log(tb / ta)
            return math.exp(math.log(ua) + weight * math.log(ub / ua))
    raise AssertionError("unreachable")  # pragma: no cover


def _stored_result(network: Network, fault: str) -> Any:
    """
    The stored result of a fault, with the errors of :meth:`Network.res_fault`.

    Parameters
    ----------
    network : Network
        The network.
    fault : str
        The fault.

    Returns
    -------
    Result
        ``network.results[fault]``.

    Raises
    ------
    ValueError
        If ``fault`` is not a fault of the network.
    RuntimeError
        If the network holds no result for it.
    """
    if fault not in network.faults:
        raise ValueError(
            f"Fault '{fault}' does not exist in network '{network.name}' "
            f"(faults: {sorted(network.faults)})."
        )
    result = network.results.get(fault)
    if result is None:
        raise RuntimeError(
            f"Network '{network.name}' holds no result for fault '{fault}'; "
            f"run gi.run_fault(network, '{fault}') first."
        )
    return result


def _touch_row(bus: Any, fault: str, duration: Optional[float], limit: float) -> Dict[str, Any]:
    """
    One row of :func:`check_touch_voltages`.

    Parameters
    ----------
    bus : ResultBus
        The bus result.
    fault : str
        The fault.
    duration : float, optional
        The fault duration the limit was taken at.
    limit : float
        The limit in volts.

    Returns
    -------
    dict
        The row; nulls from ``U_vT_V`` on for a bus without touch factor.
    """
    touch = bus.u_touch
    if touch is None:
        return {"bus_name": bus.name, "fault": fault, "EPR_V": bus.uepr, "U_vT_V": None,
                "t_k_s": None, "U_limit_V": None, "utilisation": None, "within_limit": None}
    utilisation = touch / limit
    return {"bus_name": bus.name, "fault": fault, "EPR_V": bus.uepr, "U_vT_V": touch,
            "t_k_s": duration, "U_limit_V": limit, "utilisation": utilisation,
            "within_limit": utilisation <= 1.0}


def check_touch_voltages(
    network: Network,
    fault: str,
    u_limit: Union[float, LimitCurve],
    t_k_s: Optional[float] = None,
) -> pl.DataFrame:
    r"""
    Compare the prospective touch voltage of every bus with a limit.

    Parameters
    ----------
    network : Network
        A network with a stored result for ``fault`` (:func:`groundinsight.run_fault`).
    fault : str
        The fault to assess.
    u_limit : float or sequence of (float, float)
        The limit in volts, or the curve ``[(t_F in s, U in V), ...]`` it is
        read from; see the module docstring for which limit to pass.
    t_k_s : float, optional
        The fault duration in seconds; defaults to ``Fault.t_k_s``. Required
        when ``u_limit`` is a curve.

    Returns
    -------
    polars.DataFrame
        One row per bus of the result, schema :data:`TOUCH_VOLTAGE_SCHEMA`.

    Raises
    ------
    ValueError
        If ``fault`` is not a fault of the network, or for an invalid limit
        (see :func:`limit_at`).
    RuntimeError
        If the network holds no result for ``fault``.

    Notes
    -----
    A bus without a tabulated touch factor gets nulls from ``U_vT_V`` on.
    When no bus of the result has one, the warning ``TOUCH_FACTORS_MISSING``
    says so -- an all-null frame is not a passed check. The assessment reads
    the stored result; :meth:`Network.stale_results` tells whether the
    network has changed since.

    Examples
    --------
    >>> import groundinsight as gi
    >>> station = gi.EarthingCharacteristic(rho_Ohm_m=(100.0,), frequency_Hz=(50.0,),
    ...     impedance_Ohm=[[1.0]], k_touch=[[0.3]])
    >>> bt = gi.BusType(name="S", system_type="MV/LV", voltage_level=20.0,
    ...                 impedance_formula="rho / 100", characteristic=station)
    >>> net = gi.create_network(name="n", frequencies=[50.0])
    >>> _ = gi.create_bus(name="A", type=bt, network=net)
    >>> _ = gi.create_bus(name="B", type=bt, network=net)
    >>> cable = gi.BranchType(name="K", grounding_conductor=True,
    ...     self_impedance_formula="(0.2 + j*0.6)*l", mutual_impedance_formula="j*0.5*l")
    >>> _ = gi.create_branch(name="K1", type=cable, from_bus="A", to_bus="B",
    ...                      length=1.0, network=net)
    >>> _ = gi.create_source(name="Q", bus="A", values={50.0: 100.0}, network=net)
    >>> _ = gi.create_fault(name="F", bus="B", network=net)
    >>> _ = gi.run_fault(net, "F")
    >>> table = gi.check_touch_voltages(net, "F", u_limit=[(0.1, 600.0), (1.0, 100.0)],
    ...                                 t_k_s=0.5)
    >>> table.columns[-2:], table["within_limit"].to_list()
    (['utilisation', 'within_limit'], [True, True])
    """
    result = _stored_result(network, fault)
    duration = t_k_s if t_k_s is not None else network.faults[fault].t_k_s
    limit = limit_at(u_limit, duration)
    rows = [_touch_row(bus, fault, duration, limit) for bus in result.buses]
    if not any(row["U_vT_V"] is not None for row in rows):
        emit(
            InputWarning,
            f"No bus of the result of fault '{fault}' has a tabulated touch factor, "
            "so no touch voltage can be assessed. Give the station buses an earthing "
            "characteristic with k_touch (BusType.characteristic or Bus.characteristic) "
            "and run the fault again.",
            code="TOUCH_FACTORS_MISSING",
            elements=[("fault", fault)],
        )
    logger.debug("touch voltages of fault %s against %.6g V", fault, limit)
    return pl.DataFrame(rows, schema=TOUCH_VOLTAGE_SCHEMA)

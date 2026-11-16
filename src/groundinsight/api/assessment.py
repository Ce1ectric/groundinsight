# api/assessment.py
"""
The results of a fault held to the project's assessment criteria.

A front end colours the network by how far each element is from its limit:
the earth potential rise against ``U_E``, the prospective touch voltage
against the limit of the standard, the thermally equivalent short-time
current against the admissible one. The numbers come from the library's own
checks on the stored result -- :func:`groundinsight.check_touch_voltages`,
:func:`groundinsight.check_conductor_limits` and
:func:`groundinsight.check_node_limits` -- and are put side by side here in
percent of the limit, nothing is solved and no limit is made up: a limit the
criteria do not give, or that the data cannot provide, leaves the column
empty and says why in ``notes``.

Why the thermal limit of a branch type can be given in the criteria: the
admissible current the library computes needs material and cross-section of
the type (IEC 60949), a data sheet often states the admissible short-circuit
current of the screen directly. Given for the fault duration, it replaces
the computed one for every branch of that type.
"""

from __future__ import annotations

import logging
import warnings
from typing import Any, Dict, List, Optional

import polars as pl

import groundinsight as gi

from .schemas import AssessmentDTO, CriteriaDTO
from .views import table_dto

logger = logging.getLogger(__name__)

__all__ = ["assess"]

_BUS_SCHEMA = {
    "bus_name": pl.Utf8,
    "EPR_V": pl.Float64,
    "U_E_limit_V": pl.Float64,
    "epr_utilisation_pct": pl.Float64,
    "U_vT_V": pl.Float64,
    "U_T_limit_V": pl.Float64,
    "touch_utilisation_pct": pl.Float64,
    "thermal_utilisation_pct": pl.Float64,
}

_BRANCH_SCHEMA = {
    "branch_name": pl.Utf8,
    "branch_type": pl.Utf8,
    "I_th_A": pl.Float64,
    "I_limit_A": pl.Float64,
    "limit_source": pl.Utf8,
    "thermal_utilisation_pct": pl.Float64,
}


def _percent(value: Optional[float], limit: Optional[float]) -> Optional[float]:
    """``100 * value / limit``, or ``None`` where either is missing."""
    if value is None or limit is None or limit <= 0:
        return None
    return 100.0 * value / limit


def _quietly(check, *args: Any, **kwargs: Any) -> pl.DataFrame:
    """Run a library check with its warnings silenced (the notes say what matters)."""
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", gi.GroundinsightWarning)
        return check(*args, **kwargs)


def _touch(network: gi.Network, fault: str, criteria: CriteriaDTO, t_k: Optional[float],
           notes: List[str]) -> Dict[str, Dict[str, Any]]:
    """Rows of the touch-voltage check per bus; empty with a note where it cannot run."""
    if criteria.u_touch_limit is None:
        notes.append("touch voltage: no limit given")
        return {}
    if isinstance(criteria.u_touch_limit, list) and t_k is None:
        notes.append("touch voltage: the limit is a curve and neither the fault nor the criteria give t_F")
        return {}
    frame = _quietly(gi.check_touch_voltages, network, fault, criteria.u_touch_limit, t_k_s=t_k)
    rows = {row["bus_name"]: row for row in frame.iter_rows(named=True)}
    if not any(row["U_vT_V"] is not None for row in rows.values()):
        notes.append("touch voltage: no bus has a tabulated touch factor")
    return rows


def _thermal(network: gi.Network, fault: str, criteria: CriteriaDTO, t_k: Optional[float],
             notes: List[str]):
    """Conductor and node checks; ``(None, None)`` with a note where they cannot run."""
    if t_k is None:
        notes.append("thermal: neither the fault nor the criteria give a fault duration")
        return None, None
    try:
        branches = _quietly(gi.check_conductor_limits, network, fault, t_k, r_to_x=criteria.r_to_x)
        nodes = _quietly(gi.check_node_limits, network, fault, t_k, r_to_x=criteria.r_to_x)
    except ValueError as exc:
        notes.append(f"thermal: {exc}")
        return None, None
    return branches, nodes


def _bus_rows(result: gi.Result, criteria: CriteriaDTO, touch: Dict[str, Dict[str, Any]],
              nodes: Optional[pl.DataFrame]) -> List[Dict[str, Any]]:
    """One row per bus of the result: EPR, touch voltage and thermal utilisation."""
    node_util: Dict[str, float] = {}
    for row in (nodes.iter_rows(named=True) if nodes is not None else []):
        if row["utilization"] is not None:
            node_util[row["bus_name"]] = max(node_util.get(row["bus_name"], 0.0), 100.0 * row["utilization"])
    rows = []
    for bus in result.buses:
        row = touch.get(bus.name, {})
        rows.append({
            "bus_name": bus.name,
            "EPR_V": bus.uepr,
            "U_E_limit_V": criteria.u_e_limit_V,
            "epr_utilisation_pct": _percent(bus.uepr, criteria.u_e_limit_V),
            "U_vT_V": row.get("U_vT_V"),
            "U_T_limit_V": row.get("U_limit_V"),
            "touch_utilisation_pct": _percent(row.get("U_vT_V"), row.get("U_limit_V")),
            "thermal_utilisation_pct": node_util.get(bus.name),
        })
    return rows


def _branch_rows(network: gi.Network, criteria: CriteriaDTO,
                 conductors: Optional[pl.DataFrame]) -> List[Dict[str, Any]]:
    """One row per checked branch: I_th against the given or the computed limit."""
    rows = []
    for row in (conductors.iter_rows(named=True) if conductors is not None else []):
        name = row["branch_name"]
        type_name = network.branches[name].type.name
        given = criteria.branch_current_limits_A.get(type_name)
        limit = given if given is not None else row["I_admissible_A"]
        rows.append({
            "branch_name": name,
            "branch_type": type_name,
            "I_th_A": row["I_th_A"],
            "I_limit_A": limit,
            "limit_source": None if limit is None else ("criteria" if given is not None else "thermal"),
            "thermal_utilisation_pct": _percent(row["I_th_A"], limit),
        })
    return rows


def assess(network: gi.Network, fault: str, criteria: CriteriaDTO) -> AssessmentDTO:
    """
    Hold the stored result of ``fault`` to ``criteria``.

    Parameters
    ----------
    network : Network
        The network with a stored result of ``fault``.
    fault : str
        The fault.
    criteria : CriteriaDTO
        The limits.

    Returns
    -------
    AssessmentDTO
        Per bus and per branch the value, the limit and the utilisation in
        percent; ``None`` where no limit applies.

    Raises
    ------
    ValueError
        If the network holds no result for ``fault``.

    Examples
    --------
    >>> from groundinsight import api
    >>> session = api.ProjectSession.from_example("minimal")
    >>> _ = session.solve("fault")
    >>> dto = assess(session.network, "fault", api.CriteriaDTO(u_e_limit_V=100.0))
    >>> round(dto.buses.rows[2]["epr_utilisation_pct"], 1)
    18.0
    """
    result = network.results.get(fault)
    if result is None:
        raise ValueError(f"network '{network.name}' holds no result for fault '{fault}'")
    notes: List[str] = []
    if criteria.u_e_limit_V is None:
        notes.append("earth potential rise: no limit given")
    t_k = network.faults[fault].t_k_s or criteria.t_f_s
    touch = _touch(network, fault, criteria, t_k, notes)
    conductors, nodes = _thermal(network, fault, criteria, t_k, notes)

    bus_rows = _bus_rows(result, criteria, touch, nodes)
    branch_rows = _branch_rows(network, criteria, conductors)
    if conductors is not None and not any(r["I_limit_A"] is not None for r in branch_rows):
        notes.append("thermal: no branch type gives material and cross-section or a current limit")
    return AssessmentDTO(
        fault=fault,
        buses=table_dto("assessment_buses", pl.DataFrame(bus_rows, schema=_BUS_SCHEMA)),
        branches=table_dto("assessment_branches", pl.DataFrame(branch_rows, schema=_BRANCH_SCHEMA)),
        notes=notes,
    )

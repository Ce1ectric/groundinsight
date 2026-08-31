# analysis/__init__.py

"""
Analysis subpackage.

Contains higher-level analysis routines on top of :mod:`network_operations`,
such as inverse problems for the bus-grounding rho-f characteristic. These
functions assume that the network is already fully built (buses, branches,
faults, sources, paths) and orchestrate repeated :func:`run_fault` calls.
"""

from .decomposition import Cut, CutAnalysis, analyze_cuts
from .reference import REFERENCE_CASES, ReferenceCase, run_reference_cases
from .response import BusResponse, bus_response
from .statistics import classify, summarize
from .inverse_rho import find_max_rho_scaling
from .inverse_rho_f import (
    evaluate_max_epr_under_k,
    find_max_rho_f_scaling,
    select_rho_f_from_catalog,
)
from .shortcircuit import (
    FaultShortCircuitData,
    iec60909_m,
    kappa_from_r_to_x,
    peak_short_circuit_current,
    resolve_fault_sc_characteristics,
    thermal_equivalent_current,
)
from .thermal import (
    admissible_short_circuit_current,
    check_conductor_limits,
    check_node_limits,
    final_temperature,
    iec60949_k,
    FINAL_TEMPERATURES,
    CABLE_INSULATION_LIMITS,
    IEC60949_MATERIALS,
)

__all__ = [
    "Cut",
    "CutAnalysis",
    "analyze_cuts",
    "REFERENCE_CASES",
    "ReferenceCase",
    "run_reference_cases",
    "BusResponse",
    "bus_response",
    "classify",
    "summarize",
    "find_max_rho_scaling",
    "evaluate_max_epr_under_k",
    "find_max_rho_f_scaling",
    "select_rho_f_from_catalog",
    "admissible_short_circuit_current",
    "check_conductor_limits",
    "check_node_limits",
    "final_temperature",
    "iec60949_k",
    "iec60909_m",
    "kappa_from_r_to_x",
    "peak_short_circuit_current",
    "thermal_equivalent_current",
    "resolve_fault_sc_characteristics",
    "FaultShortCircuitData",
    "FINAL_TEMPERATURES",
    "CABLE_INSULATION_LIMITS",
    "IEC60949_MATERIALS",
]

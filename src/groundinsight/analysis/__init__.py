# analysis/__init__.py

"""
Analysis subpackage.

Contains higher-level analysis routines on top of :mod:`network_operations`,
such as inverse problems for the bus-grounding rho-f characteristic. These
functions assume that the network is already fully built (buses, branches,
faults, sources, paths) and orchestrate repeated :func:`run_fault` calls.
"""

from .inverse_rho import find_max_rho_scaling
from .inverse_rho_f import (
    evaluate_max_epr_under_k,
    find_max_rho_f_scaling,
    select_rho_f_from_catalog,
)

__all__ = [
    "find_max_rho_scaling",
    "evaluate_max_epr_under_k",
    "find_max_rho_f_scaling",
    "select_rho_f_from_catalog",
]

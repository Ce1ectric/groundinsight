# analysis/_guards.py

"""
Guards the analysis layer shares, so the same situation gets the same answer.

Everything under :mod:`groundinsight.analysis` reads a *stored* result rather
than solving anything, and every one of them meets the same two failure modes:
a result that does not cover the whole network, and an assembled nodal system
that no longer describes it. Those are not module-specific accidents -- they
follow from the way ``run_fault`` caches its work -- so the check belongs in one
place rather than in whichever module happened to hit it first.

The rule the guards follow is the one the thermal checks established: a check
that silently reports *nothing* is worse than one that raises, because the
natural safety gates around it (iterating the rows, testing ``is_empty()``)
read an incomplete result as "no violations".
"""

from __future__ import annotations

import logging
from typing import Sequence

logger = logging.getLogger(__name__)

__all__ = [
    "require_complete_results",
]


def require_complete_results(
    fault_name: str, expected: Sequence[str], reported: Sequence[str], kind: str
) -> None:
    """
    Fail loudly when the stored result does not cover the whole network.

    A limit check that silently reports *nothing* is worse than one that
    raises: the natural safety gates (iterating the rows, or testing
    ``frame.is_empty()``) then read an incomplete result as "no violations",
    and the ``logger.warning`` channel of the check goes silent as well.

    This happens in practice: ``ElectricalNetwork.solve_network()`` resets
    ``network.results[fault]`` and only :meth:`~groundinsight.electrical_network.
    ElectricalNetwork.compute_branch_currents` refills the branch list, so
    solving a hand-built :class:`ElectricalNetwork` for inspection leaves the
    stored result without any branches. Adding a branch or activating a bus
    after :func:`groundinsight.run_fault` leaves the same gap.

    Parameters
    ----------
    fault_name : str
        Name of the fault whose result is being checked, for the message.
    expected : Sequence[str]
        Names that a complete result must contain.
    reported : Sequence[str]
        Names actually present in the stored result.
    kind : str
        ``"branch"`` or ``"bus"``, used in the message only.

    Raises
    ------
    ValueError
        If any expected name is missing from the stored result.
    """
    # The set is built once. Building it inside the comprehension made the
    # check quadratic in the number of elements -- 484 ms at n = 4000, on a
    # line whose whole job is to be cheap enough to run unconditionally.
    reported_names = set(reported)
    missing = [name for name in expected if name not in reported_names]
    if not missing:
        return
    shown = missing[:5]
    suffix = "" if len(missing) == len(shown) else f", ... (+{len(missing) - len(shown)})"
    raise ValueError(
        f"Incomplete results for fault {fault_name!r}: {len(missing)} of "
        f"{len(expected)} {kind}es are missing from the stored result "
        f"({shown}{suffix}). The check would silently report no violations "
        f"for them. Call run_fault(network, {fault_name!r}) to rebuild the "
        f"results; note that calling ElectricalNetwork.solve_network() on its "
        f"own resets them and must be followed by compute_branch_currents()."
    )

# groundinsight/analysis/contributions.py
r"""
The earth current of a fault, broken down by where its current returns to.

**The question.** The norm chain ``U_E = r * Z_E * I_F`` has one reduction
factor per fault. ``Z_E`` is what a measurement at the station sees: a test
current into the earthing of the station, returning through a remote
auxiliary electrode, the lead at 90° to cables and lines. A fault sends
current into the same station, but through a conductor that runs along the
screens, and takes it out of the earthing network again at the bus that feeds
it. Both differences reduce the potential, and how much depends on *where* the
current returns to. As long as one source feeds the fault, ``r`` of the fault
is the factor of that one place. With charging currents (taken at every cable
end), a coil (taken at the substation) and a resistor, the fault is fed from
many places at once.

**The formulation.** The network is linear. Let ``I_S`` be the current taken
at bus ``S`` and driven through the phase conductors to the fault bus ``F``,
and ``U_E(S)`` the potential it alone raises at ``F``. Then

$$
r_S = \frac{U_E(S)}{Z_E\,I_S}, \qquad
I_E = \sum_S r_S\,I_S, \qquad U_E = Z_E\,I_E, \qquad I_F = \sum_S I_S .
$$

``r_S`` belongs to the pair of buses and the lines between them, not to the
neutral treatment. The factor of the fault is the current-weighted mean
``r = |sum r_S I_S| / |sum I_S|``. In a resonant-earthed system ``sum I_S`` is
nearly zero while ``sum r_S I_S`` is not, because the coil current and the
charging currents return to different buses: ``r`` exceeds 1 there, and this
table shows where the earth current comes from.

**How it is computed.** Not by solving once per bus. The coupled system of
earth and phase nodes is reciprocal, so one solve with one ampere injected at
the earth node of the fault bus gives every factor: the potential of the
fault bus for a current driven from the earth node ``e_S`` into the phase
node ``p_S`` equals the voltage that one ampere at the fault bus leaves
between ``p_S`` and ``e_S``,

$$
r_S = \frac{x(p_S) - x(e_S)}{x(e_F)}, \qquad Z_E = x(e_F).
$$

A current from remote earth (the charging current of an overhead line) has
``x(p_S)`` alone. The alternative -- one full solve per source location -- was
rejected for its cost on feeders with hundreds of cable ends; a test compares
the two.

**Limits.** A Thevenin (voltage) source has no prescribed current: its loop
current depends on all the others, so a share "of that source" is not
defined. Networks with one are refused.
"""

import cmath
import logging
import math
from typing import Any, Dict, List

import polars as pl

from ..models.core_models import Network
from ..service import solved_overlay

logger = logging.getLogger(__name__)

__all__ = ["EARTH_CURRENT_SCHEMA", "earth_current_contributions"]

#: Column layout of :func:`earth_current_contributions`.
EARTH_CURRENT_SCHEMA: Dict[str, Any] = {
    "frequency_Hz": pl.Float64,
    "bus_name": pl.String,
    "remote_earth": pl.Boolean,
    "sources": pl.List(pl.String),
    "I_S_A": pl.Float64,
    "I_S_degree": pl.Float64,
    "r_S": pl.Float64,
    "r_S_degree": pl.Float64,
    "I_E_A": pl.Float64,
    "I_E_degree": pl.Float64,
}


def _polar(value: complex) -> List[float]:
    """Magnitude and angle in degrees; the angle of zero is 0."""
    magnitude = abs(value)
    return [magnitude, math.degrees(cmath.phase(value)) if magnitude else 0.0]


def earth_current_contributions(network: Network, fault: str) -> pl.DataFrame:
    r"""
    The earth current of a fault as the sum over the buses that take current back.

    Solves the fault on a copy (``network`` is not changed) and returns, per
    frequency and per bus at which a feeding element takes its current out of
    the earthing network, that current ``I_S``, the reduction factor ``r_S``
    of the way from that bus to the fault, and the contribution
    ``r_S * I_S`` to the earth current. The contributions of one frequency
    add up (as phasors) to ``I_E`` of :meth:`Network.res_fault`, the currents
    ``I_S`` to ``I_F``.

    Parameters
    ----------
    network : Network
        The network.
    fault : str
        Name of the fault.

    Returns
    -------
    polars.DataFrame
        One row per frequency and source location
        (:data:`EARTH_CURRENT_SCHEMA`):

        - ``frequency_Hz``;
        - ``bus_name`` -- the bus whose earthing the current is taken from,
          or, with ``remote_earth``, the bus at whose phase conductors a
          current from remote earth arrives;
        - ``remote_earth`` -- ``True`` for the charging current of
          capacitances to earth (``BranchType.c_earth_nf_per_km``);
        - ``sources`` -- names of the sources that take their current there;
          derived ones read ``"Charging current at <bus>"``,
          ``"Charging current to earth at <bus>"`` and ``"Neutral <name>"``;
        - ``I_S_A``, ``I_S_degree`` -- the current, after the scaling of the
          fault;
        - ``r_S``, ``r_S_degree`` -- the reduction factor of that location
          (``None`` where the earthing impedance of the fault bus is
          undefined);
        - ``I_E_A``, ``I_E_degree`` -- ``r_S * I_S``.

        Angles refer to the same reference as the source currents. Locations
        without current at a frequency are left out.

    Raises
    ------
    ValueError
        If the fault does not exist, if the network cannot be solved
        (:func:`groundinsight.run_fault`), or if a voltage source feeds the
        fault.

    Notes
    -----
    What is taken at the fault bus itself has ``r_S = 0``: it is put back
    where it is taken. A source that reaches the fault through the earthing
    network only (``SOURCE_WITHOUT_PHASE_PATH``) has the galvanic part of its
    factor alone.

    Examples
    --------
    >>> import groundinsight as gi
    >>> station = gi.BusType(name="station", system_type="MV", voltage_level=20.0, impedance_formula="2")
    >>> cable = gi.BranchType(name="cable", grounding_conductor=True, voltage_level=20.0,
    ...                       self_impedance_formula="(0.3 + j*f/50*0.7)*l",
    ...                       mutual_impedance_formula="(j*f/50*0.6)*l", c_shield_nf_per_km=250.0)
    >>> net = gi.create_network(name="feeder", frequencies=[50.0])
    >>> for bus in ("UW", "S1"):
    ...     _ = gi.create_bus(name=bus, type=station, network=net)
    >>> _ = gi.create_branch(name="K1", type=cable, from_bus="UW", to_bus="S1", length=10.0, network=net)
    >>> _ = gi.create_isolated_neutral(name="20 kV", bus="UW", voltage_level=20.0, network=net)
    >>> _ = gi.create_fault(name="F", bus="S1", network=net)
    >>> table = gi.earth_current_contributions(net, "F")
    >>> table["bus_name"].to_list(), [round(r, 3) for r in table["r_S"]]
    (['UW', 'S1'], [0.368, 0.0])
    """
    if fault not in network.faults:
        raise ValueError(f"Fault '{fault}' does not exist in the network '{network.name}'.")
    overlay = solved_overlay(network, fault)
    electrical = overlay.electrical_network
    try:
        rows: List[List[Any]] = []
        for frequency in overlay.frequencies:
            for bus, remote, names, current, factor in electrical.source_locations(float(frequency)):
                contribution = None if factor is None else factor * current
                rows.append([
                    float(frequency), bus, remote, names, *_polar(current),
                    *([None, None] if factor is None else _polar(factor)),
                    *([None, None] if contribution is None else _polar(contribution)),
                ])
    finally:
        overlay.electrical_network = None
    return pl.DataFrame(rows, schema=EARTH_CURRENT_SCHEMA, orient="row")

# utils/earth_current.py

"""
The earth-return current of a fault, summed over every bus that feeds the soil.

The reduction factor describes the effect of the inductive coupling between the
phase conductor and the earth conductor, and as a measured quantity it is the
ratio of the total earth-return current to the total fault current:

.. math::

    r = \\frac{|\\underline{I}_E|}{|\\underline{I}_F|}

The subtlety is in ``I_E``. Where the stations are bonded to one another --
which is what a cable network with continuous shields *is* -- the current does
not enter the soil at one place. It spreads along the shields and leaks into the
soil at every bonded station, so ``I_E`` is the **sum over all of them**, not the
electrode current at the faulted station. The stations that count are those from
the fault outwards, in *every* direction -- a ring or a mesh has more than one --
up to where the potential profile turns:

.. math::

    \\underline{I}_E = \\sum_i \\underline{I}_{a,i}
    \\quad\\text{over the buses that feed the soil}

Why the selection is needed
---------------------------
Summing over *all* buses gives exactly zero, always: whatever enters the soil
somewhere leaves it somewhere else, so Kirchhoff's law at the whole network
forces ``sum(I_a) = 0``. The physically meaningful quantity is the current
transferred **one way** through the soil, i.e. the sum over the buses that push
current into it. The buses that take it back out carry the same sum with the
opposite sign.

How the two groups are found
----------------------------
Not by looking for where the potential crosses zero. The crossing generally
falls *between* two stations rather than on one, and when the fault sits close
to the infeed the potential profile may never come near zero at all -- it passes
through a shallow minimum and rises again. Measured on a six-station feeder with
the fault one station from the infeed, ``|EPR|`` runs 91.4, 19.6, 18.7, 18.1,
17.7, 17.5 V: no crossing to find anywhere.

What *is* unambiguous per bus is the **direction** of its electrode current. The
split is therefore made in the complex plane: the group is the set of phasors
lying in one half-plane, and the half-plane is chosen as the one whose sum has
the largest magnitude. That is threshold-free -- no angle has to be nominated
and no tolerance tuned -- and it reduces to the obvious answer whenever the two
groups are cleanly opposed, which is the normal case.

``separation`` reports how clean the split was: the group sum divided by half the
sum of all magnitudes. It is ``1.0`` when every phasor in a group is in phase
with the others, and falls below that when the electrode currents are spread in
angle, which is the case where the notion of "one" earth-return current starts to
lose its sharpness. On the three verification feeders it is ``0.998`` or better.
"""

from __future__ import annotations

from typing import Dict, List, Optional, Tuple

import numpy as np

__all__ = [
    "EarthCurrentSplit",
    "earthing_voltage",
    "split_earth_currents",
]


class EarthCurrentSplit:
    """
    Result of :func:`split_earth_currents`.

    Attributes
    ----------
    i_earth : complex
        The earth-return current: the sum of the electrode currents of the buses
        that feed the soil.
    feeding_buses : list of str
        Those buses, sorted by name.
    returning_buses : list of str
        The buses that take the current back out of the soil.
    separation : float
        ``|i_earth| / (0.5 * sum of all magnitudes)``, in ``(0, 1]``. One means
        the phasors within each group are in phase; well below one means the
        electrode currents are spread in angle and a single scalar earth-return
        current is a coarser description than it looks.
    """

    __slots__ = ("i_earth", "feeding_buses", "returning_buses", "separation")

    def __init__(
        self,
        i_earth: complex,
        feeding_buses: List[str],
        returning_buses: List[str],
        separation: float,
    ):
        self.i_earth = i_earth
        self.feeding_buses = feeding_buses
        self.returning_buses = returning_buses
        self.separation = separation

    def __repr__(self):
        return (
            f"EarthCurrentSplit(i_earth={self.i_earth:.4g}, "
            f"feeding={self.feeding_buses}, separation={self.separation:.4f})"
        )


def earthing_voltage(
    potentials: Dict[str, complex],
    electrode_currents: Dict[str, complex],
    buses: List[str],
) -> Tuple[complex, float]:
    """
    The earthing voltage ``U_E`` of a bonded group, and how equipotential it is.

    EN 50522 writes ``U_E = 3*I_0 * Z_E * r`` for an installation that is one
    earthing system at one potential. A network model does not deliver a single
    potential -- the stations differ by the drop along the shields between them
    -- so ``U_E`` is taken as the mean over the group weighted by each station's
    electrode current. That makes the station passing the most current into the
    soil count the most, which is the one the norm's lumped picture is about.

    The second return value says whether that lumped picture applies at all:
    the weighted phasor sum over the weighted sum of magnitudes, in ``(0, 1]``.
    One means the group really is at one potential; well below one means the
    stations counted as one earthing system are not, and ``U_E`` is then a
    weighted average of genuinely different voltages.

    Parameters
    ----------
    potentials : dict of str to complex
        Bus potentials against reference earth.
    electrode_currents : dict of str to complex
        ``I_a`` per bus.
    buses : list of str
        The bonded group, normally :attr:`EarthCurrentSplit.feeding_buses`.

    Returns
    -------
    tuple of (complex, float)
        ``(U_E, equipotential)``. ``(0j, 0.0)`` when the group carries no
        current at all.
    """
    weights = {b: abs(electrode_currents.get(b, 0.0 + 0.0j)) for b in buses}
    total = sum(weights.values())
    if total <= 0:
        return 0.0 + 0.0j, 0.0
    u_e = sum(potentials[b] * weights[b] for b in buses) / total
    spread = sum(abs(potentials[b]) * weights[b] for b in buses) / total
    equipotential = float(abs(u_e) / spread) if spread > 0 else 0.0
    return complex(u_e), equipotential


def split_earth_currents(
    electrode_currents: Dict[str, complex],
    *,
    reference_bus: Optional[str] = None,
    feeding_buses: Optional[List[str]] = None,
) -> Optional[EarthCurrentSplit]:
    """
    Sum the electrode currents that flow one way through the soil.

    Parameters
    ----------
    electrode_currents : dict of str to complex
        ``I_a`` per bus -- the current each bus passes into the soil through its
        own electrode. Buses without an electrode may be omitted or passed as
        zero; either way they contribute nothing.
    reference_bus : str, optional
        Anchors which of the two groups is reported as ``feeding``. Both groups
        carry the same sum with opposite signs, so ``|I_E|`` does not depend on
        the choice -- but the reported membership does, and an unanchored maximum
        is indifferent between them. The group **containing** ``reference_bus``
        is the one named ``feeding``: pass the fault bus and the report is the
        set of stations from the fault outwards, in every direction, up to where
        the potential profile turns -- which is how the earthing current is read
        off a network. Without it the smaller group is named ``feeding`` and ties
        are broken by name, so the output stays reproducible either way.
    feeding_buses : list of str, optional
        Pin the group instead of deriving it. Use this when the split is known
        from the study design -- for instance when only the stations of one
        feeder are to be counted -- and the automatic choice would take a
        different set. Names that carry no current are ignored.

    Returns
    -------
    EarthCurrentSplit or None
        ``None`` when no bus carries any electrode current at all, which is the
        case of a network with no earthed bus rather than an earth-return
        current of zero.

    Examples
    --------
    >>> currents = {"A": 10 + 0j, "B": 5 + 0j, "C": -15 + 0j}
    >>> split = split_earth_currents(currents, reference_bus="C")
    >>> abs(split.i_earth)
    15.0
    >>> split.feeding_buses
    ['C']
    >>> split.returning_buses
    ['A', 'B']
    """
    names = [name for name, value in electrode_currents.items() if value != 0]
    if not names:
        return None

    values = np.array([complex(electrode_currents[n]) for n in names], dtype=complex)
    total_magnitude = float(np.abs(values).sum())

    if feeding_buses is not None:
        chosen = [n for n in names if n in set(feeding_buses)]
        i_earth = complex(
            sum((complex(electrode_currents[n]) for n in chosen), 0.0 + 0.0j)
        )
    else:
        chosen, i_earth = _best_half_plane(names, values)
        rest_names = [n for n in names if n not in set(chosen)]
        if _should_swap(chosen, rest_names, reference_bus):
            chosen, rest_names = rest_names, chosen
            i_earth = -i_earth

    rest = [n for n in names if n not in set(chosen)]
    separation = (
        float(abs(i_earth) / (0.5 * total_magnitude)) if total_magnitude > 0 else 0.0
    )
    return EarthCurrentSplit(
        i_earth=i_earth,
        feeding_buses=sorted(chosen),
        returning_buses=sorted(rest),
        separation=separation,
    )


def _should_swap(
    chosen: List[str], rest: List[str], reference_bus: Optional[str]
) -> bool:
    """
    Decide whether the two groups have to be exchanged.

    Both carry the same sum with opposite signs, so the half-plane maximum is
    indifferent between them -- which would make the reported membership depend
    on the order the buses happen to be stored in. This pins it.
    """
    if not rest:
        return False
    if reference_bus is not None:
        if reference_bus in chosen:
            return False
        if reference_bus in rest:
            return True
    if len(rest) != len(chosen):
        return len(rest) < len(chosen)
    return sorted(rest) < sorted(chosen)


def _best_half_plane(
    names: List[str], values: np.ndarray
) -> Tuple[List[str], complex]:
    """
    Return the half-plane of phasors whose sum has the largest magnitude.

    The optimum is always bounded by two of the phasors themselves, so it is
    enough to slide a half-turn window over the angle-sorted phasors. Prefix
    sums over the doubled array make that one pass: ``O(n log n)`` for the sort
    and ``O(n)`` after it, with no angle to nominate and no tolerance to tune.
    """
    n = len(values)
    if n == 1:
        return list(names), complex(values[0])

    angles = np.angle(values)
    order = np.argsort(angles)
    sorted_angles = angles[order]
    sorted_values = values[order]

    doubled = np.concatenate([sorted_values, sorted_values])
    doubled_angles = np.concatenate([sorted_angles, sorted_angles + 2.0 * np.pi])
    prefix = np.concatenate([[0.0 + 0.0j], np.cumsum(doubled)])

    best_sum = 0.0 + 0.0j
    best_span = (0, 0)
    j = 0
    for i in range(n):
        if j < i:
            j = i
        limit = sorted_angles[i] + np.pi
        while j < i + n and doubled_angles[j] < limit:
            j += 1
        candidate = complex(prefix[j] - prefix[i])
        if abs(candidate) > abs(best_sum):
            best_sum = candidate
            best_span = (i, j)

    start, stop = best_span
    chosen = [names[order[k % n]] for k in range(start, stop)]
    return chosen, best_sum

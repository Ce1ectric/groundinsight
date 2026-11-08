# utils/earth_current.py

r"""
The earth-return current of a fault, summed over every bus that feeds the soil.

The reduction factor describes the effect of the inductive coupling between the
phase conductor and the earth conductor, and as a measured quantity it is the
ratio of the total earth-return current to the total fault current:

.. math::

    r = \frac{|\underline{I}_E|}{|\underline{I}_F|}

The subtlety is in ``I_E``. Where the stations are bonded to one another --
which is what a cable network with continuous shields *is* -- the current does
not enter the soil at one place. It spreads along the shields and leaks into the
soil at every bonded station, so ``I_E`` is the **sum over all of them**, not the
electrode current at the faulted station. The stations that count are those from
the fault outwards, in *every* direction -- a ring or a mesh has more than one --
up to where the potential profile turns:

.. math::

    \underline{I}_E = \sum_i \underline{I}_{a,i}
    \quad\text{over the buses that feed the soil}

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
It cannot fall below ``2/pi``: for any set of phasors some half-plane collects at
least ``1/pi`` of their summed magnitudes.

Rounding noise
--------------
The half-plane is threshold-free, and that is exactly what made it fragile at
the edges (audit finding P9). A bus whose electrode current is zero in exact
arithmetic comes out of the solver as ``1e-17`` of the largest one, at an
arbitrary angle -- and an arbitrary angle lands in an arbitrary group. Two
solvers that agree to ``1e-15`` then reported different groups, and where the
group size decided which group was named feeding, a different ``I_E`` sign, a
different ``U_E`` and an earthing impedance that moved by a factor of 2.2. Three
rules close that:

* **Noise threshold.** A current with ``|I_a| <= NOISE_REL * max|I_a|``
  (``NOISE_REL = 1e-9``) counts as zero. Physically it is irrelevant to ``I_E``;
  numerically it is the only part of the input whose angle is not determined.
* **Anchor rule.** The group containing the fault bus is the feeding one. When
  the fault bus has no electrode (or only a noise-level current), the caller
  passes the fault-bus potential as ``anchor``: the feeding group is the one
  whose sum is in phase with it -- the group a resistive electrode at the fault
  bus would join. Only when neither decides do the group sizes, which noise can
  no longer tip, and the names. On 823 frequency points of a random battery in
  which the fault bus *does* carry an electrode, the anchor rule picked the
  same group as the fault-bus electrode in every case (smallest margin
  ``cos = 0.16``); the size rule it replaces named the source side feeding in 33
  of 90 electrode-less cases.
* **Vanishing reference.** :func:`earth_current_vanishes` tells the caller when
  every electrode current is noise against the fault current -- the fault
  current then returns entirely through the shields, ``I_E`` is zero to within
  rounding and no group can be formed. The caller reports ``None`` and says so
  rather than a group of noise.
"""

from __future__ import annotations

from typing import Dict, List, Optional, Tuple

import numpy as np

__all__ = [
    "EarthCurrentSplit",
    "NOISE_REL",
    "earth_current_vanishes",
    "earthing_voltage",
    "split_earth_currents",
]

#: Relative magnitude below which an electrode current (against the largest
#: one), the whole set of electrode currents (against the fault current) or the
#: fault-bus EPR (against the largest EPR) counts as rounding noise. Chosen far
#: above double-precision noise (``~1e-16``) and far below any physically
#: relevant share; see the module docstring and audit finding P9 / F14.
NOISE_REL = 1e-9

#: Smallest ``|cos|`` between the group sum and the anchor phasor at which the
#: anchor is trusted to orient the split. Below it the two are perpendicular to
#: within rounding and the size/name rule decides.
_ANCHOR_COS = 1e-9


class EarthCurrentSplit:
    """
    Result of :func:`split_earth_currents`.

    Parameters
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

    Attributes
    ----------
    i_earth, feeding_buses, returning_buses, separation
        The constructor arguments of the same name.
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
    The current-weighted mean potential of a bonded group, and how equipotential it is.

    A network model does not deliver a single potential for a group of bonded
    stations -- they differ by the drop along the shields between them -- so
    this mean weights each station by its electrode current: the station
    passing the most current into the soil counts the most.

    It is reported as ``ResultReductionFactor.u_earthing_group``, a
    diagnostic. Until audit pass 19 it was reported as the earthing voltage
    ``U_E`` of EN 50522; since decision F4 ``U_E`` is the EPR at the fault
    bus, because on a long bonded run the group reaches into the decaying
    tail of the potential profile and its mean lies 43 % below the fault-bus
    EPR (unconservative for a touch-voltage assessment).

    The second return value says whether the lumped picture applies at all:
    the weighted phasor sum over the weighted sum of magnitudes, in ``(0, 1]``.
    One means the group really is at one potential; well below one means the
    stations counted as one group are not, and the mean is then a weighted
    average of genuinely different voltages.

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
        ``(mean potential, equipotential)``. ``(0j, 0.0)`` when the group
        carries no current at all.
    """
    weights = {b: abs(electrode_currents.get(b, 0.0 + 0.0j)) for b in buses}
    total = sum(weights.values())
    if total <= 0:
        return 0.0 + 0.0j, 0.0
    u_e = sum(potentials[b] * weights[b] for b in buses) / total
    spread = sum(abs(potentials[b]) * weights[b] for b in buses) / total
    equipotential = float(abs(u_e) / spread) if spread > 0 else 0.0
    return complex(u_e), equipotential


def earth_current_vanishes(
    electrode_currents: Dict[str, complex],
    i_fault: complex,
    noise_rel: float = NOISE_REL,
) -> bool:
    """
    ``True`` when no electrode carries more than rounding noise of ``I_F``.

    The fault current then returns entirely through the shields and the other
    metallic conductors -- a fault at the far end of a cable whose end has no
    electrode is the typical case -- and ``I_E`` is zero to within rounding.
    The electrode currents that remain are of order ``1e-17 * |I_F|`` at
    arbitrary angles; splitting them would turn noise into a group, an
    earth-return current and a reduction factor (audit finding P9).

    Parameters
    ----------
    electrode_currents : dict of str to complex
        ``I_a`` per bus.
    i_fault : complex
        The fault current the group would be compared against.
    noise_rel : float, optional
        Relative threshold, :data:`NOISE_REL` by default.

    Returns
    -------
    bool
        ``True`` if ``max|I_a| <= noise_rel * |i_fault|`` (including the case
        of no electrode current at all).

    Examples
    --------
    >>> earth_current_vanishes({"A": 1e-14 + 0j, "B": -1e-14 + 0j}, 1000.0)
    True
    >>> earth_current_vanishes({"A": 10 + 0j, "B": -10 + 0j}, 1000.0)
    False
    """
    largest = max((abs(complex(v)) for v in electrode_currents.values()), default=0.0)
    return largest <= noise_rel * abs(complex(i_fault))


def split_earth_currents(
    electrode_currents: Dict[str, complex],
    *,
    reference_bus: Optional[str] = None,
    feeding_buses: Optional[List[str]] = None,
    anchor: Optional[complex] = None,
    noise_rel: float = NOISE_REL,
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
        off a network.
    feeding_buses : list of str, optional
        Pin the group instead of deriving it. Use this when the split is known
        from the study design -- for instance when only the stations of one
        feeder are to be counted -- and the automatic choice would take a
        different set. Names that carry no current are ignored.
    anchor : complex, optional
        Orientation used when ``reference_bus`` is in neither group -- it has
        no electrode, or only a noise-level current. The group whose sum has a
        positive projection on ``anchor`` is named ``feeding``; pass the
        fault-bus potential and that is the group a resistive electrode at the
        fault bus would join. Ignored when it is (to within ``1e-9``)
        perpendicular to the group sum. Without a deciding reference or anchor
        the smaller group is named ``feeding`` and ties are broken by name, so
        the output stays reproducible either way.
    noise_rel : float, optional
        Currents with ``|I_a| <= noise_rel * max|I_a|`` count as zero and join
        neither group. Defaults to :data:`NOISE_REL` (``1e-9``); ``0`` keeps
        every non-zero current.

    Returns
    -------
    EarthCurrentSplit or None
        ``None`` when no bus carries any electrode current at all, which is the
        case of a network with no earthed bus rather than an earth-return
        current of zero. Whether the currents that *are* there amount to more
        than rounding noise against the fault current is a separate question,
        answered by :func:`earth_current_vanishes`.

    Notes
    -----
    Inputs without noise-level currents and with a current at
    ``reference_bus`` give bit-for-bit the result of the implementation
    before the noise rules (audit finding P9); the test suite keeps that
    implementation as an oracle.

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

    A bus with a rounding-noise current joins neither group:

    >>> noisy = dict(currents, N=1e-14 + 1e-14j)
    >>> split_earth_currents(noisy, reference_bus="C").returning_buses
    ['A', 'B']
    """
    names = [name for name, value in electrode_currents.items() if value != 0]
    if not names:
        return None

    values = np.array([complex(electrode_currents[n]) for n in names], dtype=complex)
    if noise_rel > 0:
        # Noise threshold (P9): only the currents above it have a determined
        # angle. Applied before anything else so that the noise neither joins
        # a group nor shifts the group sizes the fallback rule looks at. When
        # nothing is dropped, ``names``/``values`` are untouched and the
        # result is bit-for-bit the unfiltered one.
        magnitudes = np.abs(values)
        keep = magnitudes > noise_rel * float(magnitudes.max())
        if not keep.all():
            names = [n for n, kept in zip(names, keep, strict=True) if kept]
            values = values[keep]
    total_magnitude = float(np.abs(values).sum())

    # Every membership test below goes against a set built *once*. Building
    # ``set(chosen)`` inside the comprehensions -- once per bus -- made the
    # split quadratic: 12 s per frequency at 20 000 buses (P3).
    if feeding_buses is not None:
        pinned = set(feeding_buses)
        chosen = [n for n in names if n in pinned]
        i_earth = complex(
            sum((complex(electrode_currents[n]) for n in chosen), 0.0 + 0.0j)
        )
    else:
        chosen, i_earth = _best_half_plane(names, values)
        chosen_set = set(chosen)
        rest_names = [n for n in names if n not in chosen_set]
        if _should_swap(chosen, rest_names, reference_bus, i_earth, anchor):
            chosen, rest_names = rest_names, chosen
            i_earth = -i_earth

    chosen_set = set(chosen)
    rest = [n for n in names if n not in chosen_set]
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
    chosen: List[str],
    rest: List[str],
    reference_bus: Optional[str],
    i_earth: complex = 0.0 + 0.0j,
    anchor: Optional[complex] = None,
) -> bool:
    """
    Decide whether the two groups have to be exchanged.

    Both carry the same sum with opposite signs, so the half-plane maximum is
    indifferent between them -- which would make the reported membership depend
    on the order the buses happen to be stored in. This pins it, in this
    order: the group containing ``reference_bus`` feeds; else the group whose
    sum is in phase with ``anchor`` feeds; else the smaller group, ties broken
    by name.

    Parameters
    ----------
    chosen : list of str
        Members of the half-plane found by :func:`_best_half_plane`.
    rest : list of str
        The other (non-noise) buses.
    reference_bus : str or None
        Bus whose group is named feeding when it carries a current.
    i_earth : complex
        Sum over ``chosen``.
    anchor : complex or None
        Orientation phasor for the case without a deciding reference bus.

    Returns
    -------
    bool
        ``True`` if ``rest`` is the feeding group.
    """
    if not rest:
        return False
    if reference_bus is not None:
        if reference_bus in chosen:
            return False
        if reference_bus in rest:
            return True
    if anchor is not None and anchor != 0 and i_earth != 0:
        anchor = complex(anchor)
        projection = (complex(i_earth) * anchor.conjugate()).real
        if abs(projection) > _ANCHOR_COS * abs(i_earth) * abs(anchor):
            return projection < 0
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

    Parameters
    ----------
    names : list of str
        Bus names, aligned with ``values``.
    values : numpy.ndarray
        Non-zero electrode currents.

    Returns
    -------
    tuple of (list of str, complex)
        The members of the best half-plane (in angle order, starting at the
        window's first phasor) and their sum.

    Notes
    -----
    The window end of every start ``i`` is found with one vectorised
    :func:`numpy.searchsorted` instead of a scalar ``while`` loop (P3). It is
    the *same* index the loop produced: the loop carried ``j`` over from the
    previous start, clamped it to at least ``i`` and advanced it while
    ``j < i + n`` and ``doubled_angles[j] < sorted_angles[i] + pi``. Because
    ``doubled_angles`` is sorted and the limits never decrease with ``i``,
    the first index whose angle reaches the limit is
    ``s_i = searchsorted(doubled_angles, limit_i, side="left")``, the carried
    ``j`` never exceeds ``min(max(s_i, i), i + n)``, and the loop therefore
    stopped exactly there. The candidate sums are the same IEEE subtractions
    of the same prefix sums, and :func:`numpy.argmax` returns the *first*
    maximum, which is the loop's strict ``>`` against a running best. The
    result is bit-for-bit what the loop returned; the test suite keeps the
    loop as an oracle.
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

    starts = np.arange(n)
    stops = np.searchsorted(doubled_angles, sorted_angles + np.pi, side="left")
    stops = np.minimum(np.maximum(stops, starts), starts + n)
    candidates = prefix[stops] - prefix[starts]
    magnitudes = np.abs(candidates)
    best = int(np.argmax(magnitudes))
    if not magnitudes[best] > 0.0:
        # No window beats an empty sum -- the loop then kept (0, 0).
        return [], 0.0 + 0.0j

    start, stop = int(starts[best]), int(stops[best])
    chosen = [names[order[k % n]] for k in range(start, stop)]
    return chosen, complex(candidates[best])

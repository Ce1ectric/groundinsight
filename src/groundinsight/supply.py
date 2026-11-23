# groundinsight/supply.py
r"""
Supply areas, charging currents and the currents of the neutral-point elements.

**What is modelled.** In an earth fault of a system with a neutral-point
element (:class:`~groundinsight.IsolatedNeutral`,
:class:`~groundinsight.ResonantNeutral`,
:class:`~groundinsight.LowImpedanceNeutral`) the healthy phases charge the
phase-to-earth capacitances of the system. A branch of length ``l`` with the
capacitance ``C'`` per phase carries

$$
I_C = j\,\sqrt{3}\,\omega\,C'\,l\,U_n
$$

(``U_n`` the voltage level of the element). The current leaves the phase
conductors along the branch; for a charging current that is uniform along the
branch the loop equations of screen and conductor depend on its mean only, so
the equivalent with **half the current at each end** is exact, not an
approximation. Each half is a current source in the sense of
:class:`~groundinsight.Source`: taken from the earthing network at the end of
the branch (``c_shield_nf_per_km``: the screen is bonded there) or from
remote earth (``c_earth_nf_per_km``: an overhead line charges the soil along
its route), carried by the phase conductors to the fault and put into the
earthing network there. What flows through the neutral -- the coil current,
the watt residual, the current of a resistor -- is one more source at the bus
of the element.

**Why sources and not shunt admittances.** A source needs nothing the solver
does not have: the capacitive part is the same superposition for every
neutral treatment, only the current at the bus of the element differs, and a
network without any of this is solved exactly as before. A shunt admittance
between phase and earth nodes would also give the voltage profile along long
lines and the resonance of harmonics, but needs a voltage-driven fault and
the zero-sequence data of transformer and coil; it is on the roadmap. Feeding
the charging current into the earthing nodes *without* the conductor current
was rejected: the screens would lose their reducing effect (a factor of
``1/|1 - Z_m/Z_s|`` on the potentials).

**Voltage levels.** Several levels share one earthing network (earth wire of
the 110 kV line, screens of the 20 kV cables, PEN of the LV cables). A fault
belongs to one level: only the branches of that level
(``BranchType.voltage_level``, ``None`` belongs to every level) carry its
phase current and only their capacitances are charged; the others are
earthing conductors. The **supply area** of a feeding element is the set of
branches of its level its phase conductors reach. Levels come into play only
when a feeding element carries one -- a neutral-point element always does, a
:class:`~groundinsight.Source` when ``voltage_level`` is set; without any, the
network is one system, as it always was.

**Harmonics.** ``harmonic_currents`` of an element is a current spectrum in
A. For an isolated or resonant neutral it is the fault current at that
frequency, which is capacitive: it is distributed over the branches like the
charging current at the nominal frequency, a coil compensating
``1/h^2`` of its share (``h`` the harmonic order). For a low-impedance
neutral it flows through the neutral.

:func:`supply_areas` is the public table; :func:`feeding` is what the solver
reads.
"""

import logging
import math
from dataclasses import dataclass, field
from typing import Dict, FrozenSet, List, Optional, Set, Tuple

import polars as pl

from .models.core_models import (
    Branch,
    IsolatedNeutral,
    LowImpedanceNeutral,
    Network,
    ResonantNeutral,
    Source,
)

logger = logging.getLogger(__name__)

__all__ = ["SUPPLY_AREAS_SCHEMA", "supply_areas"]

#: Column layout of :func:`supply_areas`.
SUPPLY_AREAS_SCHEMA: Dict[str, pl.DataType] = {
    "area": pl.Int64,
    "voltage_level_kV": pl.Float64,
    "feeding_elements": pl.List(pl.String),
    "neutral": pl.String,
    "buses": pl.List(pl.String),
    "branches": pl.List(pl.String),
    "length_km": pl.Float64,
    "I_C_branches_A": pl.Float64,
    "I_C_remaining_A": pl.Float64,
    "I_CE_A": pl.Float64,
    "I_coil_A": pl.Float64,
    "I_active_A": pl.Float64,
    "I_residual_A": pl.Float64,
    "detuning": pl.Float64,
}

#: Relative tolerance with which a network frequency counts as the nominal one.
_NOMINAL_TOLERANCE = 1e-9
#: Below this magnitude of ``1 - k/h**2`` a harmonic sits on the resonance of coil and capacitances.
_RESONANCE_MARGIN = 1e-9


@dataclass
class Feeding:
    """
    What feeds one fault, as the solver reads it.

    Internal API: built by :func:`feeding` for
    :class:`~groundinsight.electrical_network.ElectricalNetwork`; not
    exported from ``groundinsight`` and free to change in any release.

    Attributes
    ----------
    active : bool
        ``False`` for a network without neutral-point elements and without a
        source that carries a voltage level: ``sources`` is then the
        network's own dictionary and nothing else is set.
    level : float or None
        Voltage level of the fault in kV; ``None`` when no level applies.
    sources : dict of str to Source
        The sources of the solve: the network's sources of this level and
        the derived ones.
    derived : frozenset of str
        Names of the derived sources (they feed the fault by construction).
    unscaled : frozenset of str
        Sources the scaling of the fault does not apply to.
    remote : frozenset of str
        Sources that take their current from remote earth, not from the
        earthing node of their bus.
    blocked : frozenset of str
        Branches of another level: no phase conductor in this solve.
    charging : dict of str to dict of float to complex
        Charging current per branch and frequency.
    capacitive : dict of float to complex
        All capacitive current per frequency (branches and the part of the
        system that is not modelled).
    neutral : dict of float to complex
        Current through the neutral points per frequency, before the scaling
        of the fault.
    """

    active: bool
    level: Optional[float]
    sources: Dict[str, Source]
    derived: FrozenSet[str] = frozenset()
    unscaled: FrozenSet[str] = frozenset()
    remote: FrozenSet[str] = frozenset()
    blocked: FrozenSet[str] = frozenset()
    charging: Dict[str, Dict[float, complex]] = field(default_factory=dict)
    capacitive: Dict[float, complex] = field(default_factory=dict)
    neutral: Dict[float, complex] = field(default_factory=dict)


def _phase_conducts(branch: Branch) -> bool:
    """
    Whether the phase conductor of a branch is closed.

    Parameters
    ----------
    branch : Branch
        Any branch.

    Returns
    -------
    bool
        ``False`` for an inactive branch and for one whose phase impedance is
        declared open (infinite) at every frequency; a branch without a
        declared phase impedance conducts (the solver uses its proxy).
    """
    if not branch.active:
        return False
    if not branch.phase_impedance:
        return True
    return not all(
        math.isinf(z.real) or math.isinf(z.imag) or math.isnan(z.real) or math.isnan(z.imag)
        for z in branch.phase_impedance.values()
    )


def _of_level(branch: Branch, level: Optional[float]) -> bool:
    """Whether a branch belongs to ``level`` (``None`` on either side matches every level)."""
    own = branch.type.voltage_level
    return level is None or own is None or math.isclose(own, level, rel_tol=1e-9)


def _phase_adjacency(network: Network, level: Optional[float]) -> Dict[str, List[Tuple[str, str]]]:
    """Per bus the ``(neighbour, branch)`` pairs its closed phase conductors of ``level`` lead to."""
    adjacency: Dict[str, List[Tuple[str, str]]] = {}
    for name, branch in network.branches.items():
        ends = (branch.from_bus, branch.to_bus)
        usable = _phase_conducts(branch) and _of_level(branch, level) and all(
            end in network.buses and network.buses[end].active for end in ends)
        if usable:
            adjacency.setdefault(ends[0], []).append((ends[1], name))
            adjacency.setdefault(ends[1], []).append((ends[0], name))
    return adjacency


def _phase_component(network: Network, start: str, level: Optional[float]) -> Tuple[Set[str], List[str]]:
    """
    The buses and branches the phase conductors of one level reach from a bus.

    Parameters
    ----------
    network : Network
        The network.
    start : str
        Name of the bus to start at.
    level : float or None
        Voltage level in kV; ``None`` takes every branch.

    Returns
    -------
    buses : set of str
        The active buses reached, ``start`` included.
    branches : list of str
        The branches between them, in the order of the network.
    """
    adjacency = _phase_adjacency(network, level)
    reached, frontier, members = {start}, [start], set()
    while frontier:
        here = frontier.pop()
        for other, name in adjacency.get(here, ()):
            members.add(name)
            if other not in reached:
                reached.add(other)
                frontier.append(other)
    return reached, [name for name in network.branches if name in members]


def _levels_in_use(network: Network) -> List[float]:
    """The voltage levels of the feeding elements, ascending."""
    levels = {neutral.voltage_level for neutral in network.neutrals.values() if neutral.active}
    levels |= {source.voltage_level for source in network.sources.values() if source.voltage_level is not None}
    return sorted(levels)


def _elements_of(network: Network, level: float, buses: Set[str]) -> Tuple[List[str], List[str]]:
    """Names of the active neutrals and of the sources of ``level`` that stand at one of ``buses``."""
    neutrals = [name for name, neutral in network.neutrals.items()
                if neutral.active and neutral.bus in buses and math.isclose(neutral.voltage_level, level, rel_tol=1e-9)]
    sources = [name for name, source in network.sources.items()
               if source.voltage_level is not None and source.bus in buses
               and math.isclose(source.voltage_level, level, rel_tol=1e-9)]
    return neutrals, sources


def _charging_magnitudes(network: Network, branches: List[str], level: float, frequency: float
                         ) -> Dict[str, Tuple[float, float]]:
    """
    Charging current of every branch at one frequency, as magnitudes in A.

    Parameters
    ----------
    network : Network
        The network.
    branches : list of str
        The branches of the supply area.
    level : float
        Voltage level in kV.
    frequency : float
        Frequency in Hz.

    Returns
    -------
    dict of str to (float, float)
        Per branch with a capacitance: the part to the earthing conductor
        and the part to earth, ``sqrt(3) * omega * C' * l * U_n`` each.
    """
    factor = math.sqrt(3) * 2 * math.pi * frequency * level * 1e3 * 1e-9
    result: Dict[str, Tuple[float, float]] = {}
    for name in branches:
        branch = network.branches[name]
        to_conductor = (branch.type.c_shield_nf_per_km or 0.0) * branch.length * factor
        to_earth = (branch.type.c_earth_nf_per_km or 0.0) * branch.length * factor
        if to_conductor or to_earth:
            result[name] = (to_conductor, to_earth)
    return result


def _nominal_frequency(network: Network, neutrals: List[str]) -> float:
    """
    The nominal frequency the neutrals of one supply area agree on.

    Parameters
    ----------
    network : Network
        The network.
    neutrals : list of str
        Names of the neutrals of the area (at least one).

    Returns
    -------
    float
        The frequency in Hz.

    Raises
    ------
    ValueError
        If the elements name different frequencies.
    """
    values = {network.neutrals[name].nominal_frequency for name in neutrals}
    if len(values) > 1:
        raise ValueError(
            f"The neutral-point elements {', '.join(repr(n) for n in neutrals)} feed the same supply area "
            f"but name different nominal frequencies ({', '.join(f'{v:g} Hz' for v in sorted(values))})."
        )
    return values.pop()


def supply_areas(network: Network) -> pl.DataFrame:
    r"""
    The supply areas of a network: who feeds which branches, and with what.

    A supply area is one voltage level as far as the phase conductors of a
    feeding element reach: the branches of that level
    (``BranchType.voltage_level``; a branch without one belongs to every
    level) that are connected with the bus of a neutral-point element or of a
    source carrying that ``voltage_level``. Open phase conductors and
    inactive branches divide areas. Nothing is solved.

    Parameters
    ----------
    network : Network
        The network.

    Returns
    -------
    polars.DataFrame
        One row per area (:data:`SUPPLY_AREAS_SCHEMA`):

        - ``area`` -- running number, by voltage level and first bus;
        - ``voltage_level_kV``;
        - ``feeding_elements`` -- names of the neutral-point elements and
          sources of the area;
        - ``neutral`` -- ``"isolated"``, ``"resonant"``, ``"low_impedance"``,
          ``"source"`` (prescribed current only), several joined by ``+``;
        - ``buses``, ``branches``, ``length_km``;
        - ``I_C_branches_A`` -- capacitive earth-fault current of the
          branches at the nominal frequency,
          $\sqrt{3}\,\omega\,C_E\,U_n$; ``None`` for an area without a
          neutral-point element (its capacitances are not charged);
        - ``I_C_remaining_A`` -- the part of the system that is not modelled;
        - ``I_CE_A`` -- their sum;
        - ``I_coil_A`` -- sum of the coil currents;
        - ``I_active_A`` -- sum of the watt residual currents;
        - ``I_residual_A`` -- $|I_{CE} - I_{coil}|$ combined with the watt
          residual, the fault current at the nominal frequency of an
          isolated or resonant area;
        - ``detuning`` -- $v = (I_{CE} - I_{coil})/I_{CE}$, negative when
          over-compensated; ``None`` without a coil or without capacitive
          current.

    Raises
    ------
    ValueError
        If the neutral-point elements of one area name different nominal
        frequencies.

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
    >>> _ = gi.create_resonant_neutral(name="coil", bus="UW", voltage_level=20.0, coil_current=28.0, network=net)
    >>> areas = gi.supply_areas(net)
    >>> areas["neutral"][0], round(areas["I_CE_A"][0], 2), round(areas["detuning"][0], 3)
    ('resonant', 27.21, -0.029)
    """
    rows = []
    seen: Set[Tuple[float, str]] = set()
    for level in _levels_in_use(network):
        for start in network.buses:
            if (level, start) in seen or not network.buses[start].active:
                continue
            buses, branches = _phase_component(network, start, level)
            neutrals, sources = _elements_of(network, level, buses)
            seen.update((level, bus) for bus in buses)
            if not neutrals and not sources:
                continue
            rows.append(_area_row(network, level, buses, branches, neutrals, sources))
    for number, row in enumerate(rows, start=1):
        row["area"] = number
    return pl.DataFrame(rows, schema=SUPPLY_AREAS_SCHEMA)


def _area_row(network: Network, level: float, buses: Set[str], branches: List[str], neutrals: List[str],
              sources: List[str]) -> Dict[str, object]:
    """One row of :func:`supply_areas`."""
    kinds = list(dict.fromkeys(network.neutrals[name].kind for name in neutrals))
    row: Dict[str, object] = {
        "area": 0,
        "voltage_level_kV": level,
        "feeding_elements": neutrals + sources,
        "neutral": "+".join(kinds) if kinds else "source",
        "buses": [bus for bus in network.buses if bus in buses],
        "branches": branches,
        "length_km": sum(network.branches[name].length for name in branches),
        "I_C_branches_A": None, "I_C_remaining_A": None, "I_CE_A": None, "I_coil_A": None,
        "I_active_A": None, "I_residual_A": None, "detuning": None,
    }
    if neutrals:
        row.update(_area_currents(network, level, branches, neutrals))
    return row


def _area_currents(network: Network, level: float, branches: List[str], neutrals: List[str]) -> Dict[str, float]:
    """
    The current columns of one area with neutral-point elements.

    Parameters
    ----------
    network : Network
        The network.
    level : float
        Voltage level in kV.
    branches : list of str
        The branches of the area.
    neutrals : list of str
        Its neutral-point elements (at least one).

    Returns
    -------
    dict of str to float
        The columns of :func:`supply_areas` that have a value for this area.

    Raises
    ------
    ValueError
        If the elements name different nominal frequencies.
    """
    frequency = _nominal_frequency(network, neutrals)
    elements = [network.neutrals[name] for name in neutrals]
    charging = sum(a + b for a, b in _charging_magnitudes(network, branches, level, frequency).values())
    remaining = sum(element.remaining_capacitive_current for element in elements)
    coils = [element for element in elements if isinstance(element, ResonantNeutral)]
    coil = sum(element.coil_current for element in coils)
    active = sum(element.active_residual_current for element in coils)
    total = charging + remaining
    columns = {"I_C_branches_A": charging, "I_C_remaining_A": remaining, "I_CE_A": total}
    columns.update(_coil_columns(coils, coil, active, total))
    if all(isinstance(element, (IsolatedNeutral, ResonantNeutral)) for element in elements):
        columns["I_residual_A"] = math.hypot(total - coil, active)
    return columns


def _coil_columns(coils: List[ResonantNeutral], coil: float, active: float, total: float) -> Dict[str, float]:
    """The coil columns of :func:`supply_areas`: empty without a coil, the detuning only with capacitive current."""
    if not coils:
        return {}
    columns = {"I_coil_A": coil, "I_active_A": active}
    if total > 0:
        columns["detuning"] = (total - coil) / total
    return columns


def fault_level(network: Network, fault: str) -> Optional[float]:
    """
    The voltage level a fault belongs to.

    Internal API: used by the solver and the checks; not exported from
    ``groundinsight`` and free to change in any release.

    Parameters
    ----------
    network : Network
        The network.
    fault : str
        Name of a fault of the network.

    Returns
    -------
    float or None
        ``Fault.voltage_level`` if set; otherwise the one level whose
        feeding elements reach the fault bus over their phase conductors;
        ``None`` if no feeding element carries a level or none of them
        reaches the fault bus.

    Raises
    ------
    ValueError
        If the feeding elements of several levels reach the fault bus and
        the fault does not say which one it is in.
    """
    definition = network.faults[fault]
    if definition.voltage_level is not None:
        return definition.voltage_level
    candidates = []
    for level in _levels_in_use(network):
        buses, _ = _phase_component(network, definition.bus, level)
        neutrals, sources = _elements_of(network, level, buses)
        if neutrals or sources:
            candidates.append(level)
    if len(candidates) > 1:
        raise ValueError(
            f"Fault '{fault}' at bus '{definition.bus}' is reached by the feeding elements of several "
            f"voltage levels ({', '.join(f'{level:g} kV' for level in candidates)}). Say which system "
            "the fault is in: Fault.voltage_level."
        )
    return candidates[0] if candidates else None


class _Collector:
    """Currents per (kind, bus) and frequency, turned into derived sources at the end."""

    CHARGING = "Charging current at {bus}"
    TO_EARTH = "Charging current to earth at {bus}"
    NEUTRAL = "Neutral {name}"

    def __init__(self, frequencies: List[float]):
        self.frequencies = frequencies
        self.values: Dict[Tuple[str, str, str], Dict[float, complex]] = {}

    def add(self, pattern: str, bus: str, key: str, frequency: float, current: complex) -> None:
        """Add ``current`` at ``frequency`` to the source ``pattern`` of ``bus``."""
        if current == 0:
            return
        entry = self.values.setdefault((pattern, bus, key), {f: 0j for f in self.frequencies})
        entry[frequency] += current

    def sources(self, taken: Set[str]) -> Tuple[Dict[str, Source], Set[str], Set[str]]:
        """
        The collected currents as sources.

        Parameters
        ----------
        taken : set of str
            Names already in use.

        Returns
        -------
        sources : dict of str to Source
            The derived sources, with a value at every network frequency.
        unscaled : set of str
            Those the scaling of the fault does not apply to.
        remote : set of str
            Those that take their current from remote earth.
        """
        sources: Dict[str, Source] = {}
        unscaled: Set[str] = set()
        remote: Set[str] = set()
        for (pattern, bus, key), values in self.values.items():
            name = pattern.format(bus=bus, name=key.split("|", 1)[0])
            while name in taken or name in sources:
                name += " *"
            sources[name] = Source(name=name, bus=bus, values=dict(values))
            if not key.endswith("|scaled"):
                unscaled.add(name)
            if pattern == self.TO_EARTH:
                remote.add(name)
        return sources, unscaled, remote


def feeding(network: Network, fault: str) -> Feeding:
    """
    Everything that feeds a fault: the sources of its level and the derived ones.

    Internal API: called by
    :class:`~groundinsight.electrical_network.ElectricalNetwork`; not
    exported from ``groundinsight`` and free to change in any release.

    Parameters
    ----------
    network : Network
        The network.
    fault : str
        Name of the active fault.

    Returns
    -------
    Feeding
        See the class. For a network without neutral-point elements and
        without a source that carries a voltage level it holds the network's
        own source dictionary and nothing else.

    Raises
    ------
    ValueError
        If the level of the fault is ambiguous, if the elements of its
        supply area name different nominal frequencies, or if a harmonic of
        a resonant area sits on the resonance of coil and capacitances.
    """
    if not _levels_in_use(network):
        return Feeding(active=False, level=None, sources=network.sources)
    level = fault_level(network, fault)
    sources = {name: source for name, source in network.sources.items()
               if level is None or source.voltage_level is None
               or math.isclose(source.voltage_level, level, rel_tol=1e-9)}
    blocked = frozenset(name for name, branch in network.branches.items() if not _of_level(branch, level))
    result = Feeding(active=True, level=level, sources=sources, blocked=blocked)
    if level is not None:
        _derive_sources(network, fault, level, result)
    return result


def _derive_sources(network: Network, fault: str, level: float, result: Feeding) -> None:
    """
    Add the sources derived from the supply area of the fault to ``result``.

    Parameters
    ----------
    network : Network
        The network.
    fault : str
        Name of the fault.
    level : float
        Its voltage level in kV.
    result : Feeding
        Extended in place; unchanged if no neutral-point element stands in
        the supply area of the fault.

    Raises
    ------
    ValueError
        As documented for :func:`feeding`.
    """
    buses, branches = _phase_component(network, network.faults[fault].bus, level)
    neutrals, _ = _elements_of(network, level, buses)
    if not neutrals:
        return
    collector = _Collector([float(f) for f in network.frequencies])
    _AreaCurrents(network, level, branches, neutrals, collector, result).collect()
    derived, unscaled, remote = collector.sources(set(result.sources))
    result.sources = {**result.sources, **derived}
    result.derived = frozenset(derived)
    result.unscaled = frozenset(unscaled)
    result.remote = frozenset(remote)


class _AreaCurrents:
    """
    The currents of one supply area at every network frequency.

    Parameters
    ----------
    network : Network
        The network.
    level : float
        Voltage level of the area in kV.
    branches : list of str
        Its branches.
    neutrals : list of str
        Its neutral-point elements (at least one).
    collector : _Collector
        Receives the currents.
    result : Feeding
        Receives the charging current per branch and the sums per frequency.

    Raises
    ------
    ValueError
        If the elements name different nominal frequencies.
    """

    def __init__(self, network: Network, level: float, branches: List[str], neutrals: List[str],
                 collector: _Collector, result: Feeding):
        self.network = network
        self.collector = collector
        self.result = result
        self.nominal = _nominal_frequency(network, neutrals)
        self.elements = [network.neutrals[name] for name in neutrals]
        self.magnitudes = _charging_magnitudes(network, branches, level, self.nominal)
        self.remaining = [(element.bus, element.remaining_capacitive_current) for element in self.elements
                          if element.remaining_capacitive_current]
        self.coils = [element for element in self.elements if isinstance(element, ResonantNeutral)]
        self.total = sum(a + b for a, b in self.magnitudes.values()) + sum(value for _, value in self.remaining)
        self.coil_total = sum(element.coil_current for element in self.coils)

    def collect(self) -> None:
        """
        Fill the collector.

        Raises
        ------
        ValueError
            If a harmonic sits on the resonance of coils and capacitances.
        """
        for frequency in self.collector.frequencies:
            if math.isclose(frequency, self.nominal, rel_tol=_NOMINAL_TOLERANCE):
                self._nominal(frequency)
            else:
                self._harmonic(frequency)

    def _capacitive(self, frequency: float, unit: complex) -> None:
        """Put ``unit`` times the nominal charging pattern (in A) at ``frequency``."""
        add = self.collector.add
        for name, (to_conductor, to_earth) in self.magnitudes.items():
            branch = self.network.branches[name]
            self.result.charging.setdefault(name, {})[frequency] = unit * (to_conductor + to_earth)
            for end in (branch.from_bus, branch.to_bus):
                add(_Collector.CHARGING, end, "", frequency, unit * to_conductor / 2)
                add(_Collector.TO_EARTH, end, "", frequency, unit * to_earth / 2)
        for bus, value in self.remaining:
            add(_Collector.CHARGING, bus, "", frequency, unit * value)
        self.result.capacitive[frequency] = self.result.capacitive.get(frequency, 0j) + unit * self.total

    def _through_neutral(self, element, frequency: float, current: complex, scaled: bool = False) -> None:
        """Take ``current`` at the bus of ``element``; ``scaled`` if the fault scaling applies to it."""
        key = f"{element.name}|{'scaled' if scaled else ''}"
        self.collector.add(_Collector.NEUTRAL, element.bus, key, frequency, current)
        self.result.neutral[frequency] = self.result.neutral.get(frequency, 0j) + current

    def _nominal(self, frequency: float) -> None:
        """The nominal frequency: charging currents, coils, resistors."""
        self._capacitive(frequency, 1j)
        for element in self.elements:
            if isinstance(element, ResonantNeutral):
                current = complex(element.active_residual_current, -element.coil_current)
                self._through_neutral(element, frequency, current)
            elif isinstance(element, LowImpedanceNeutral):
                self._through_neutral(element, frequency, complex(element.current), scaled=True)

    def _entered(self, frequency: float, order: float) -> complex:
        """
        Place the entered harmonic currents that are not capacitive; return the capacitive ones.

        Parameters
        ----------
        frequency : float
            A frequency other than the nominal one.
        order : float
            Its harmonic order.

        Returns
        -------
        complex
            Sum of the entered currents of the isolated and resonant
            elements that is to be distributed over the capacitances.
        """
        nowhere = self.total <= 0 or (order == 0 and not self.coil_total)
        spectrum = 0j
        for element in self.elements:
            current = element.harmonic_currents.get(frequency)
            if current is None:
                continue
            if isinstance(element, LowImpedanceNeutral):
                self._through_neutral(element, frequency, complex(current), scaled=True)
            elif nowhere:
                # nothing to distribute it over: it stays at the bus of the element
                self._through_neutral(element, frequency, complex(current))
            else:
                spectrum += complex(current)
        return spectrum

    def _harmonic(self, frequency: float) -> None:
        """
        Another frequency: the entered spectrum, capacitive where the neutral is not earthed.

        Parameters
        ----------
        frequency : float
            A frequency other than the nominal one.

        Raises
        ------
        ValueError
            If the frequency sits on the resonance of coils and capacitances.
        """
        order = frequency / self.nominal
        spectrum = self._entered(frequency, order)
        if spectrum == 0:
            return
        if order == 0:
            # 0 Hz: no capacitance conducts, the coils carry it in proportion to their size
            for element in self.coils:
                self._through_neutral(element, frequency, spectrum * element.coil_current / self.coil_total)
            return
        compensation = (self.coil_total / self.total) / order**2
        if abs(1 - compensation) < _RESONANCE_MARGIN:
            raise ValueError(
                f"The harmonic current at {frequency:g} Hz sits on the resonance of the coils "
                f"({self.coil_total:g} A) and the capacitive current ({self.total:g} A) of its supply area: "
                "the capacitive current that gives this residual is unbounded."
            )
        unit = spectrum / (1 - compensation) / self.total
        self._capacitive(frequency, unit)
        for element in self.coils:
            self._through_neutral(element, frequency, -unit * element.coil_current / order**2)

# analysis/decomposition.py

"""
Split a grounding network at the fault location into parallel sides.

The question this module answers is the one a cable-network study starts from:
of everything that is bolted onto the faulted station, how much grounding does
each direction actually contribute, and how does the fault current divide
between them?

    parallel impedance left --- fault location --- parallel impedance right

A **cut** is a named set of branches, all of them incident to the fault bus.
Removing the cut separates a *far side* from the *near side* that holds the
fault bus. Two families of quantity are then computed for each cut, and they
answer different questions:

**Impedances -- a property of the network, not of the fault.**
``Z_side`` is obtained by current division, which keeps it exact in any
topology. One ampere is injected at the fault bus into the network with **all
sources and all mutual injections removed**; the share of it that leaves through
each cut is read off, and ``Z_side = u_fault / i_cut``. Because the shares and
the local electrode current add up to the injected ampere, the decomposition
closes by construction:

.. math::

    \\frac{1}{Z_\\text{driving point}} = \\frac{1}{Z_\\text{local}}
        + \\sum_\\text{sides} \\frac{1}{Z_\\text{side}}

and the residual of that identity is reported next to the values, so a study
never has to take it on trust. Because ``Z_side`` is source-free it does **not**
change when the fault bus's own rho-f characteristic is varied -- which is the
useful statement: the network's parallel contribution is a fixed property, the
local electrode is the variable, and the total is their parallel combination.

An earlier formulation isolated each side into its own sub-network instead. That
is equivalent wherever the sides are galvanically separate, and **wrong in a
ring**: removing one branch of a ring separates nothing, the far side comes out
empty and the impedance comes out infinite. Current division has no such blind
spot -- in a ring both directions simply see much of the same network, which is
reported through ``sides_are_disjoint``.

**Currents -- how the fault current actually divides.**
Taken from a solved fault. Across a cut, the current crossing from the far side
splits into a metallic part through the cable shields and a part travelling
through the soil:

.. math::

    \\underline{I}_\\text{total} = \\underline{I}_\\text{shield}
                                  + \\underline{I}_\\text{earth}

where ``I_earth`` is the sum of the electrode currents of every bus on the far
side and ``I_total`` is the sum of the source injections there. The reduction
factor of that side is ``r = |I_earth| / |I_total|`` -- the share of the current
crossing the cut that returns through the earth rather than through the shields.
Unlike the EPR-based reduction factor on ``Result``, this one **does** respond to
the impedance at the fault bus, because the split between electrode and shields
is decided by that impedance.

A cut whose far side carries no source has ``I_total = 0``: nothing *has* to
cross it, and whatever does is driven purely by the coupling. ``r`` is then
``None`` rather than a division by zero, and ``Z_side`` is the quantity that
carries the meaning for that side.

``I_earth``, ``I_total`` and ``r`` all need a far side that belongs to one cut
alone. Where the sides overlap -- a ring -- they are ``None`` and
``current_share = |I_shield| / |I_inj at the fault bus|`` is the quantity that
still holds: the fraction of the fault current that leaves the fault location
metallically in that direction. Together with the local share it always adds up
to the injected current.

Contract
--------
Every branch of a cut must be incident to the fault bus. That is what makes the
sides *parallel* elements at the fault bus and the identity above exact; a cut
placed further out is a legitimate thing to want, but it is not a parallel
element of the fault location and the sum would not close. Branches incident to
the fault bus that no cut claims are collected into an implicit side so the
decomposition always covers the whole network.
"""

from __future__ import annotations

import logging
from typing import Dict, List, Optional, Sequence, Set, Tuple

import numpy as np
import polars as pl
from pydantic import BaseModel, Field, field_validator

from groundinsight.models.core_models import Network

logger = logging.getLogger(__name__)

__all__ = [
    "Cut",
    "CutAnalysis",
    "analyze_cuts",
]

#: Name given to the side made of fault-bus branches that no cut claimed.
IMPLICIT_SIDE = "rest"


class Cut(BaseModel):
    """
    A named set of branches separating one side of the network from the fault.

    Attributes
    ----------
    name : str
        Label used in the result frame. Must not collide with the reserved
        name ``"rest"``, which is used for the implicit remainder side.
    branches : list of str
        Names of the branches forming the cut. All of them must exist, be
        active, carry a grounding conductor and be incident to the fault bus.
    description : str, optional
        Free text carried through to the result frame.
    """

    name: str
    branches: List[str] = Field(min_length=1)
    description: Optional[str] = None

    @field_validator("branches")
    def _no_duplicates(cls, value):
        """Reject a branch listed twice -- it would be counted twice."""
        seen = set()
        duplicates = {b for b in value if b in seen or seen.add(b)}
        if duplicates:
            raise ValueError(
                f"Branch(es) {sorted(duplicates)} appear more than once in the "
                f"same cut. Each branch crosses a cut exactly once."
            )
        return value

    def __str__(self):
        return f"Cut(name={self.name}, branches={self.branches})"


class CutAnalysis(BaseModel):
    """
    Result of :func:`analyze_cuts` for one fault.

    Attributes
    ----------
    fault : str
        Name of the analysed fault.
    fault_bus : str
        Bus the fault sits on -- the reference point of the decomposition.
    frequencies : list of float
        Frequencies the analysis was run at.
    sides : dict of str to list of str
        Buses visible from the fault bus when looking out through that cut,
        with the other cuts' branches removed. In a ring the entries overlap.
    sides_are_disjoint : bool
        ``True`` when no two directions reach the same bus. The far-side
        current quantities are only defined in that case.
    branches : dict of str to list of str
        The branches of each cut, including the implicit ``"rest"`` side.
    z_local : dict of float to complex
        Impedance of the fault bus's own electrode per frequency.
    z_side : dict of str to dict of float to complex
        Impedance of each direction, from source-free current division.
    z_parallel : dict of float to complex
        ``z_local`` in parallel with every ``z_side``.
    z_driving_point : dict of float to complex
        Driving-point impedance of the complete network at the fault bus,
        computed independently. Equal to ``z_parallel`` up to
        ``identity_residual``.
    identity_residual : dict of float to float
        ``|z_parallel - z_driving_point| / |z_driving_point|`` per frequency.
    i_shield : dict of str to dict of float to complex
        Shield current leaving the fault bus in each direction. Empty when no
        solved result was available.
    i_fault, i_local : dict of float to complex
        Injection at the fault bus and the part of it taken by the fault bus's
        own electrode.
    current_share : dict of str to dict of float to float or None
        ``|i_shield| / |i_fault|`` -- the share of the fault current leaving
        metallically in that direction. Always defined.
    i_earth, i_total : dict of str to dict of float to complex or None
        Soil and total current crossing the cut, summed over the far side.
        ``None`` per entry when the directions overlap.
    r_side : dict of str to dict of float to float or None
        ``|i_earth| / |i_total|`` per direction -- the earth-return share of
        what crosses that cut. ``None`` when the far side carries no injection
        or when the directions overlap.
    kcl_residual : dict of float to float
        ``|i_fault - i_local - sum(i_shield)| / |i_fault|``. Zero to machine
        precision confirms the split accounts for the whole fault current.
    """

    model_config = {"arbitrary_types_allowed": True}

    fault: str
    fault_bus: str
    frequencies: List[float]
    sides: Dict[str, List[str]]
    sides_are_disjoint: bool = True
    branches: Dict[str, List[str]]
    z_local: Dict[float, complex]
    z_side: Dict[str, Dict[float, complex]]
    z_parallel: Dict[float, complex]
    z_driving_point: Dict[float, complex]
    identity_residual: Dict[float, float]
    i_shield: Dict[str, Dict[float, complex]] = Field(default_factory=dict)
    i_earth: Dict[str, Dict[float, Optional[complex]]] = Field(default_factory=dict)
    i_total: Dict[str, Dict[float, Optional[complex]]] = Field(default_factory=dict)
    r_side: Dict[str, Dict[float, Optional[float]]] = Field(default_factory=dict)
    current_share: Dict[str, Dict[float, Optional[float]]] = Field(
        default_factory=dict
    )
    i_fault: Dict[float, complex] = Field(default_factory=dict)
    i_local: Dict[float, complex] = Field(default_factory=dict)
    kcl_residual: Dict[float, float] = Field(default_factory=dict)

    @property
    def has_currents(self) -> bool:
        """``True`` when a solved fault result was available."""
        return bool(self.i_shield)

    def to_polars(self) -> pl.DataFrame:
        """
        Return the analysis as one long-format frame, one row per direction and
        frequency.

        Columns
        -------
        fault, fault_bus, cut, side_buses, n_side_buses, branches,
        sides_are_disjoint, frequency_Hz, Z_side_Ohm, Z_side_deg, Z_local_Ohm,
        Z_parallel_Ohm, Z_driving_point_Ohm, identity_residual, I_fault_A,
        I_local_A, I_shield_A, I_shield_deg, current_share, I_earth_A,
        I_total_A, r_side, kcl_residual

        Returns
        -------
        pl.DataFrame
            One row per (cut, frequency).
        """

        def _mag(value):
            return None if value is None else float(abs(value))

        rows = []
        for cut_name in self.z_side:
            for freq in self.frequencies:
                z = self.z_side[cut_name][freq]
                row = {
                    "fault": self.fault,
                    "fault_bus": self.fault_bus,
                    "cut": cut_name,
                    "side_buses": ", ".join(self.sides[cut_name]),
                    "n_side_buses": len(self.sides[cut_name]),
                    "branches": ", ".join(self.branches[cut_name]),
                    "sides_are_disjoint": self.sides_are_disjoint,
                    "frequency_Hz": float(freq),
                    "Z_side_Ohm": float(abs(z)),
                    "Z_side_deg": float(np.degrees(np.angle(z))),
                    "Z_local_Ohm": float(abs(self.z_local[freq])),
                    "Z_parallel_Ohm": float(abs(self.z_parallel[freq])),
                    "Z_driving_point_Ohm": float(abs(self.z_driving_point[freq])),
                    "identity_residual": float(self.identity_residual[freq]),
                }
                if self.has_currents:
                    i_sh = self.i_shield[cut_name][freq]
                    row.update(
                        {
                            "I_fault_A": _mag(self.i_fault[freq]),
                            "I_local_A": _mag(self.i_local[freq]),
                            "I_shield_A": float(abs(i_sh)),
                            "I_shield_deg": float(np.degrees(np.angle(i_sh))),
                            "current_share": self.current_share[cut_name][freq],
                            "I_earth_A": _mag(self.i_earth[cut_name][freq]),
                            "I_total_A": _mag(self.i_total[cut_name][freq]),
                            "r_side": self.r_side[cut_name][freq],
                            "kcl_residual": float(self.kcl_residual[freq]),
                        }
                    )
                else:
                    row.update(
                        {
                            "I_fault_A": None,
                            "I_local_A": None,
                            "I_shield_A": None,
                            "I_shield_deg": None,
                            "current_share": None,
                            "I_earth_A": None,
                            "I_total_A": None,
                            "r_side": None,
                            "kcl_residual": None,
                        }
                    )
                rows.append(row)
        return pl.DataFrame(rows)


# --- internals ---------------------------------------------------------------


def _finite_impedance(value) -> Optional[complex]:
    """Return the impedance as ``complex``, or ``None`` if it carries no
    admittance (missing, open circuit, or not finite)."""
    if value is None:
        return None
    z = complex(value)
    if np.isinf(z.real) or np.isinf(z.imag):
        return None
    if np.isnan(z.real) or np.isnan(z.imag):
        return None
    if z == 0:
        return None
    return z


def _active_branches(network: Network):
    """Yield the branches that contribute an admittance to the grounding grid."""
    for branch in network.branches.values():
        if not branch.active:
            continue
        if not branch.type.grounding_conductor:
            continue
        from_bus = network.buses.get(branch.from_bus)
        to_bus = network.buses.get(branch.to_bus)
        if from_bus is None or to_bus is None:
            continue
        if not from_bus.active or not to_bus.active:
            continue
        yield branch


def _unit_injection(
    network: Network, fault_bus: str, freq: float
) -> Optional[Dict[str, complex]]:
    """
    Solve the source-free network with one ampere injected at the fault bus.

    The resulting nodal voltages are impedances: ``u[fault_bus]`` *is* the
    driving-point impedance, and the current leaving through any branch divided
    into ``u[fault_bus]`` is the impedance of that direction. No source and no
    mutual injection takes part, so the outcome is a property of the network
    alone.

    Parameters
    ----------
    network : Network
        The network to assemble.
    fault_bus : str
        Bus the ampere is injected at.
    freq : float
        Frequency to evaluate the impedances at.

    Returns
    -------
    dict of str to complex or None
        Nodal voltages by bus name, or ``None`` when the system has no finite
        path to reference earth at this frequency.
    """
    ordered = sorted(name for name, bus in network.buses.items() if bus.active)
    index = {name: i for i, name in enumerate(ordered)}
    n = len(ordered)
    Y = np.zeros((n, n), dtype=complex)

    for name in ordered:
        z = _finite_impedance(network.buses[name].impedance.get(freq))
        if z is not None:
            Y[index[name], index[name]] += 1.0 / z

    for branch in _active_branches(network):
        z = _finite_impedance(branch.self_impedance.get(freq))
        if z is None:
            continue
        y = 1.0 / z
        i, j = index[branch.from_bus], index[branch.to_bus]
        Y[i, i] += y
        Y[j, j] += y
        Y[i, j] -= y
        Y[j, i] -= y

    i_vector = np.zeros(n, dtype=complex)
    i_vector[index[fault_bus]] = 1.0
    try:
        u = np.linalg.solve(Y, i_vector)
    except np.linalg.LinAlgError:
        return None
    if not np.all(np.isfinite(u)):
        return None
    return {name: complex(u[index[name]]) for name in ordered}


def _branch_current_out(
    network: Network, branch_name: str, fault_bus: str, u: Dict[str, complex], freq: float
) -> complex:
    """Current leaving ``fault_bus`` through one incident branch, given nodal
    voltages ``u``. Zero for an open-circuited or unresolvable branch."""
    branch = network.branches[branch_name]
    z = _finite_impedance(branch.self_impedance.get(freq))
    if z is None:
        return 0.0 + 0.0j
    other = branch.to_bus if branch.from_bus == fault_bus else branch.from_bus
    return (u[fault_bus] - u[other]) / z


def _direction_buses(
    network: Network, cut_branches: Set[str], incident: Set[str], fault_bus: str
) -> List[str]:
    """
    Buses visible from the fault bus when looking out through ``cut_branches``.

    Every other branch at the fault bus is removed first, so what remains is
    what that direction reaches -- around a ring included, which is why two
    directions of a ring report overlapping sets.
    """
    blocked = incident - cut_branches
    adjacency: Dict[str, Set[str]] = {}
    for branch in _active_branches(network):
        if branch.name in blocked:
            continue
        adjacency.setdefault(branch.from_bus, set()).add(branch.to_bus)
        adjacency.setdefault(branch.to_bus, set()).add(branch.from_bus)

    seen = {fault_bus}
    stack = [fault_bus]
    while stack:
        for neighbour in adjacency.get(stack.pop(), ()):
            if neighbour not in seen:
                seen.add(neighbour)
                stack.append(neighbour)
    return sorted(seen - {fault_bus})


def _validate_cuts(
    network: Network, cuts: Sequence[Cut], fault_bus: str
) -> Tuple[Dict[str, List[str]], Set[str]]:
    """Check the contract and return ``({cut name: branches}, incident)``."""
    incident = {
        branch.name
        for branch in _active_branches(network)
        if fault_bus in (branch.from_bus, branch.to_bus)
    }
    if not incident:
        raise ValueError(
            f"The fault bus '{fault_bus}' has no active grounding branch, so "
            f"there is nothing to split. A parallel decomposition needs at "
            f"least one branch leaving the fault location."
        )

    claimed: Dict[str, str] = {}
    resolved: Dict[str, List[str]] = {}
    for cut in cuts:
        if cut.name == IMPLICIT_SIDE:
            raise ValueError(
                f"'{IMPLICIT_SIDE}' is reserved for the branches that no cut "
                f"claims; pick a different name for your cut."
            )
        if cut.name in resolved:
            raise ValueError(f"Two cuts are both named '{cut.name}'.")
        for branch_name in cut.branches:
            if branch_name not in network.branches:
                raise ValueError(
                    f"Cut '{cut.name}' names branch '{branch_name}', which does "
                    f"not exist in network '{network.name}'."
                )
            if branch_name not in incident:
                branch = network.branches[branch_name]
                raise ValueError(
                    f"Cut '{cut.name}' names branch '{branch_name}' "
                    f"({branch.from_bus} -> {branch.to_bus}), which is not an "
                    f"active grounding branch at the fault bus '{fault_bus}'. "
                    f"A parallel decomposition is defined at the fault "
                    f"location: every cut branch has to be one of "
                    f"{sorted(incident)}. A cut placed further out still "
                    f"separates the network, but its far side is no longer a "
                    f"parallel element of the fault bus and the impedances "
                    f"would not sum to the driving-point value."
                )
            if branch_name in claimed:
                raise ValueError(
                    f"Branch '{branch_name}' is claimed by both cut "
                    f"'{claimed[branch_name]}' and cut '{cut.name}'. Sides have "
                    f"to be disjoint or the current would be counted twice."
                )
            claimed[branch_name] = cut.name
        resolved[cut.name] = list(cut.branches)

    remainder = sorted(incident - set(claimed))
    if remainder:
        logger.info(
            "Branches %s leave the fault bus '%s' without belonging to a named "
            "cut; they are reported as the implicit side '%s' so the parallel "
            "decomposition covers the whole network.",
            ", ".join(remainder),
            fault_bus,
            IMPLICIT_SIDE,
        )
        resolved[IMPLICIT_SIDE] = remainder
    return resolved, incident


def analyze_cuts(
    network: Network,
    *,
    fault: str,
    cuts: Sequence[Cut],
    include_currents: bool = True,
) -> CutAnalysis:
    """
    Split the network at the fault bus and quantify each direction.

    Parameters
    ----------
    network : Network
        The network. Bus and branch impedances must have been evaluated at
        ``network.frequencies`` (they are, after ``add_bus`` / ``add_branch``).
    fault : str
        Name of the fault whose bus is the reference of the decomposition.
    cuts : sequence of Cut
        Named sets of branches, each incident to the fault bus and disjoint from
        the others. Incident branches that no cut claims form the implicit side
        ``"rest"``.
    include_currents : bool, optional
        If ``True`` (default) the current split is read from
        ``network.results[fault]``. When no such result exists the impedances
        are still returned and the current fields stay empty -- the impedance
        half of the analysis needs no solve.

    Returns
    -------
    CutAnalysis
        Impedances, currents and the residual of the parallel identity.

    Raises
    ------
    ValueError
        If the fault is unknown, if a cut names a branch that is not an active
        grounding branch at the fault bus, or if two cuts claim the same branch.

    Examples
    --------
    >>> import groundinsight as gi  # doctest: +SKIP
    >>> gi.run_fault(net, "F1")  # doctest: +SKIP
    >>> analysis = gi.analyze_cuts(  # doctest: +SKIP
    ...     net,
    ...     fault="F1",
    ...     cuts=[gi.Cut(name="left", branches=["L12"]),
    ...           gi.Cut(name="right", branches=["L23"])],
    ... )
    >>> analysis.to_polars()  # doctest: +SKIP
    """
    if fault not in network.faults:
        raise ValueError(
            f"Fault '{fault}' does not exist in network '{network.name}'. "
            f"Available: {sorted(network.faults)}."
        )
    fault_bus = network.faults[fault].bus
    resolved, incident = _validate_cuts(network, cuts, fault_bus)
    frequencies = [float(f) for f in network.frequencies]

    directions = {
        name: _direction_buses(network, set(branches), incident, fault_bus)
        for name, branches in resolved.items()
    }
    names = list(resolved)
    disjoint = all(
        not (set(directions[a]) & set(directions[b]))
        for i, a in enumerate(names)
        for b in names[i + 1 :]
    )

    z_local: Dict[float, complex] = {}
    z_side: Dict[str, Dict[float, complex]] = {name: {} for name in resolved}
    z_parallel: Dict[float, complex] = {}
    z_driving_point: Dict[float, complex] = {}
    identity_residual: Dict[float, float] = {}

    infinite = complex(np.inf, 0.0)
    for freq in frequencies:
        local = _finite_impedance(network.buses[fault_bus].impedance.get(freq))
        z_local[freq] = local if local is not None else infinite

        u = _unit_injection(network, fault_bus, freq)
        if u is None:
            z_driving_point[freq] = infinite
            for name in resolved:
                z_side[name][freq] = infinite
            z_parallel[freq] = infinite
            identity_residual[freq] = float("nan")
            continue

        u_fault = u[fault_bus]
        z_driving_point[freq] = u_fault

        admittance = 0.0 + 0.0j if local is None else 1.0 / local
        for name, branches in resolved.items():
            i_cut = sum(
                (
                    _branch_current_out(network, b, fault_bus, u, freq)
                    for b in branches
                ),
                0.0 + 0.0j,
            )
            if i_cut == 0 or u_fault == 0:
                z_side[name][freq] = infinite
            else:
                z = u_fault / i_cut
                z_side[name][freq] = z
                admittance += 1.0 / z

        z_parallel[freq] = 1.0 / admittance if admittance != 0 else infinite
        reference = abs(z_driving_point[freq])
        identity_residual[freq] = (
            abs(z_parallel[freq] - z_driving_point[freq]) / reference
            if reference > 0 and np.isfinite(reference)
            else float("nan")
        )

    analysis_kwargs = dict(
        fault=fault,
        fault_bus=fault_bus,
        frequencies=frequencies,
        sides=directions,
        sides_are_disjoint=disjoint,
        branches=resolved,
        z_local=z_local,
        z_side=z_side,
        z_parallel=z_parallel,
        z_driving_point=z_driving_point,
        identity_residual=identity_residual,
    )

    if include_currents and fault in network.results:
        result = network.results[fault]
        branch_results = {b.name: b for b in result.branches}
        bus_results = {b.name: b for b in result.buses}

        i_shield: Dict[str, Dict[float, complex]] = {n: {} for n in resolved}
        i_earth: Dict[str, Dict[float, complex]] = {n: {} for n in resolved}
        i_total: Dict[str, Dict[float, complex]] = {n: {} for n in resolved}
        r_side: Dict[str, Dict[float, Optional[float]]] = {n: {} for n in resolved}
        current_share: Dict[str, Dict[float, Optional[float]]] = {
            n: {} for n in resolved
        }
        i_fault: Dict[float, complex] = {}
        i_local: Dict[float, complex] = {}
        kcl_residual: Dict[float, float] = {}

        for freq in frequencies:
            injection = complex(bus_results[fault_bus].i_inj_freq[freq])
            local_current = complex(bus_results[fault_bus].ia_freq[freq])
            i_fault[freq] = injection
            i_local[freq] = local_current
            leaving = 0.0 + 0.0j

            for name, branches in resolved.items():
                total_out = 0.0 + 0.0j
                for branch_name in branches:
                    branch = network.branches[branch_name]
                    stored = complex(branch_results[branch_name].i_s_freq[freq])
                    # ``i_s`` is oriented to_bus -> from_bus, so it already is
                    # the current leaving the fault bus when the fault bus is
                    # the to_bus, and the negative of it otherwise.
                    sign = 1.0 if branch.to_bus == fault_bus else -1.0
                    total_out += sign * stored
                i_shield[name][freq] = total_out
                leaving += total_out
                current_share[name][freq] = (
                    abs(total_out) / abs(injection) if injection != 0 else None
                )

                if disjoint:
                    far = directions[name]
                    earth = sum(
                        (complex(bus_results[b].ia_freq[freq]) for b in far),
                        0.0 + 0.0j,
                    )
                    total = sum(
                        (complex(bus_results[b].i_inj_freq[freq]) for b in far),
                        0.0 + 0.0j,
                    )
                    i_earth[name][freq] = earth
                    i_total[name][freq] = total
                    r_side[name][freq] = (
                        abs(earth) / abs(total) if abs(total) > 0 else None
                    )
                else:
                    i_earth[name][freq] = None
                    i_total[name][freq] = None
                    r_side[name][freq] = None

            reference = abs(injection)
            kcl_residual[freq] = (
                abs(injection - local_current - leaving) / reference
                if reference > 0
                else 0.0
            )

        if not disjoint:
            logger.info(
                "The directions out of fault bus '%s' overlap -- %s reach each "
                "other around a ring -- so there is no far side belonging to "
                "one cut alone. The impedances are unaffected (they come from "
                "current division), but I_earth, I_total and r_side are left "
                "empty; use current_share instead.",
                fault_bus,
                " and ".join(f"'{n}'" for n in names),
            )

        analysis_kwargs.update(
            i_shield=i_shield,
            i_earth=i_earth,
            i_total=i_total,
            r_side=r_side,
            current_share=current_share,
            i_fault=i_fault,
            i_local=i_local,
            kcl_residual=kcl_residual,
        )
    elif include_currents:
        logger.info(
            "No solved result for fault '%s' on network '%s', so only the "
            "source-free impedances are reported. Call run_fault first if you "
            "also want the current split.",
            fault,
            network.name,
        )

    return CutAnalysis(**analysis_kwargs)

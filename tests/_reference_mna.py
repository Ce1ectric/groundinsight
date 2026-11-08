# tests/_reference_mna.py
"""
Independent reference solver for networked grounding systems (test oracle).

Why a second solver lives in the test suite
-------------------------------------------
Every physical result of ``groundinsight`` comes out of one nodal solve, and a
nodal solve returns a number for a wrong model just as readily as for a right
one. A test that compares the solver against itself -- or against a number it
produced last month -- cannot notice that. This module is the second way the
house rule asks for: it solves the *documented* model with a different
formulation, in a different sign frame, from the same input data, and the
tests compare the two.

What is deliberately different from the package
-----------------------------------------------
* **Modified nodal analysis with the branch currents as explicit unknowns.**
  The package eliminates the shield currents into a nodal admittance matrix
  and represents the inductive coupling as Norton injections. Here every
  shield branch carries its own unknown current and its own branch law, and
  the coupling appears as an EMF in that law.
* **Physical sign frame.** The fault current ``I_F`` of a source flows
  transformer -> phase conductor (source bus -> fault bus) -> fault ->
  earthing node of the fault bus -> soil / shields -> earthing node of the
  source bus. The earth-side injection is therefore ``+I_F`` at the fault bus
  and ``-I_F`` at the source bus, and a shield branch obeys the textbook law
  for co-directional currents with earth return::

      u_a - u_b = Z_ss * I_s + Z_ps * I_p          (currents a -> b)

  The package uses the opposite injection sign. The two frames are related by
  ``u_pkg = -u_phys`` and ``ia_pkg = -I_a,phys``, while the package's shield
  current ``i_s`` equals the physical shield current in from -> to direction.
  :func:`to_package_frame` applies that mapping.
* **Plain data.** The solver reads a dictionary, not a ``Network``.
  :func:`reference_network` fills it from the *public* model fields of a
  ``groundinsight`` network (stored impedance tables, flags, source values,
  fault scalings) and imports nothing else from the package. The input data
  is shared on purpose -- the oracle checks the solver, not the formula
  evaluator, which has its own tests.

Phase-current models
--------------------
:func:`solve_oneway`
    The package's documented one-way model: the phase currents are fixed first
    from a phase-only network (fault bus as reference, one ampere per source)
    and then imposed as EMFs ``Z_ps * I_p`` in the shield branch laws. For a
    Thevenin source the phase current is either the *actual* loop current
    (``thevenin_phase="actual"``, solved simultaneously -- physically
    consistent) or the Norton current ``E/Z_src`` (``"norton"``, what the
    package does while finding PH1 is open).
:func:`solve_coupled`
    The full multi-conductor model: per branch a 2x2 impedance matrix
    ``[[Z_pp, Z_ps], [Z_ps, Z_ss]]`` (both loops with earth return); phase
    conductors, shields and electrodes are solved simultaneously, the fault
    bonds the phase conductor to the earthing node of the fault bus.

Proxy rule for the one-way phase split
--------------------------------------
A branch without a declared phase impedance gets a proxy: its shield self
impedance ``Z_ss`` when it has a grounding conductor with a finite one, its
length (real, in km) otherwise. A broken shield (``Z_ss`` infinite) therefore
falls back to the length proxy -- the phase conductor of a cable with a broken
screen is still there (audit finding PH3).

Data model::

    net = {
      "buses":    {name: {"z": complex | INF, "active": bool}},
      "branches": {name: {"a": str, "b": str, "zs": complex | INF, "zm": complex,
                          "zp": complex | INF | None,   # declared phase impedance
                          "zpp": complex | None,        # coupled model only
                          "gc": bool, "active": bool, "length": float}},
      "sources":  {name: {"bus": str, "kind": "current", "I": complex}
                   | {"bus": str, "kind": "voltage", "E": complex, "Zsrc": complex}},
      "fault_bus": str,
      "scaling": float,
    }

All quantities are per frequency; callers loop over frequencies.
"""

from __future__ import annotations

import cmath
import math
from typing import Dict, List, Optional, Sequence, Tuple

import numpy as np

INF = complex(math.inf, 0.0)


# ---------------------------------------------------------------------------
# input
# ---------------------------------------------------------------------------


def _isinf(z) -> bool:
    """``True`` for an infinite impedance (the documented open-end sentinel).

    Parameters
    ----------
    z : complex or None
        Impedance to classify.

    Returns
    -------
    bool
        ``False`` for ``None``.
    """
    if z is None:
        return False
    z = complex(z)
    return math.isinf(z.real) or math.isinf(z.imag)


def _value(table, frequency: float) -> Optional[complex]:
    """Read one entry of a stored impedance or source table as ``complex``.

    Parameters
    ----------
    table : dict or None
        ``{frequency: ComplexNumber}`` as stored on a model.
    frequency : float
        Key to read.

    Returns
    -------
    complex or None
        ``None`` if the table or the entry is missing.
    """
    if not table:
        return None
    entry = table.get(frequency)
    if entry is None:
        return None
    return complex(entry.real, entry.imag)


def reference_network(network, frequency: float, fault: str) -> dict:
    """Build the plain-data input of the reference solver from a network.

    Only public model fields are read: ``Bus.impedance``, ``Bus.active``,
    ``Branch.self_impedance``, ``Branch.mutual_impedance``,
    ``Branch.phase_impedance``, ``Branch.type.grounding_conductor``,
    ``Branch.length``, ``Branch.active``, ``Source.values`` /
    ``Source.voltage`` / ``Source.source_impedance`` and
    ``Fault.scalings``. A missing scaling key means the documented default
    factor ``1.0``.

    Parameters
    ----------
    network : groundinsight.Network
        A network built through the public API.
    frequency : float
        The frequency to extract, in Hz.
    fault : str
        Name of the fault to solve.

    Returns
    -------
    dict
        The data model described in the module docstring. Sources without a
        value at ``frequency`` are left out (they contribute nothing there).
    """
    buses = {
        name: {"z": _value(bus.impedance, frequency), "active": bool(bus.active)}
        for name, bus in network.buses.items()
    }
    branches = {}
    for name, branch in network.branches.items():
        branches[name] = {
            "a": branch.from_bus,
            "b": branch.to_bus,
            "zs": _value(branch.self_impedance, frequency),
            "zm": _value(branch.mutual_impedance, frequency) or 0j,
            "zp": _value(branch.phase_impedance, frequency),
            "zpp": None,
            "gc": bool(branch.type.grounding_conductor),
            "active": bool(branch.active),
            "length": float(branch.length),
        }
    sources = {}
    for name, source in network.sources.items():
        if source.source_type == "current":
            current = _value(source.values, frequency)
            if current is None:
                continue
            sources[name] = {"bus": source.bus, "kind": "current", "I": current}
        else:
            emf = _value(source.voltage, frequency)
            z_src = _value(source.source_impedance, frequency)
            if emf is None or z_src is None:
                continue
            sources[name] = {
                "bus": source.bus, "kind": "voltage", "E": emf, "Zsrc": z_src,
            }
    fault_obj = network.faults[fault]
    scaling = float((fault_obj.scalings or {}).get(float(frequency), 1.0))
    return {
        "buses": buses,
        "branches": branches,
        "sources": sources,
        "fault_bus": fault_obj.bus,
        "scaling": scaling,
    }


def _components(nodes: Sequence[str], edges: Sequence[Tuple[str, str]], start: str) -> set:
    """Nodes reachable from ``start`` over undirected ``edges``.

    Parameters
    ----------
    nodes : sequence of str
        All node names.
    edges : sequence of (str, str)
        Undirected edges.
    start : str
        Start node.

    Returns
    -------
    set of str
        The connected component of ``start`` (including it).
    """
    adjacency = {n: set() for n in nodes}
    for a, b in edges:
        adjacency[a].add(b)
        adjacency[b].add(a)
    seen = {start}
    stack = [start]
    while stack:
        current = stack.pop()
        for neighbour in adjacency[current]:
            if neighbour not in seen:
                seen.add(neighbour)
                stack.append(neighbour)
    return seen


def _active_buses(net: dict) -> List[str]:
    """Names of the active buses, in declaration order."""
    return [b for b, d in net["buses"].items() if d.get("active", True)]


def _shield_branches(net: dict, bus_index: Dict[str, int]) -> List[str]:
    """Active branches with a finite shield between two active buses."""
    shields = []
    for name, d in net["branches"].items():
        if not d.get("active", True) or not d.get("gc", True):
            continue
        if d["a"] not in bus_index or d["b"] not in bus_index:
            continue
        if d["zs"] is None or _isinf(d["zs"]):
            continue
        shields.append(name)
    return shields


# ---------------------------------------------------------------------------
# phase-only network (one-way model)
# ---------------------------------------------------------------------------


def phase_impedance(branch: dict, phase_choice: str = "declared") -> complex:
    """Impedance a branch presents in the one-way phase split.

    Parameters
    ----------
    branch : dict
        One entry of ``net["branches"]``.
    phase_choice : {"declared", "proxy", "zpp", "zred"}
        ``"declared"`` uses ``zp`` and falls back to the proxy when it is
        ``None``; ``"proxy"`` always uses the proxy (``Z_ss`` for a branch
        with a finite shield, the length otherwise); ``"zpp"`` the phase loop
        impedance; ``"zred"`` the screened ``Z_pp - Z_ps**2/Z_ss``.

    Returns
    -------
    complex
        The impedance; ``INF`` means the phase conductor is open.
    """
    if phase_choice == "declared" and branch.get("zp") is not None:
        return complex(branch["zp"])
    if phase_choice == "zpp":
        return complex(branch["zpp"])
    if phase_choice == "zred":
        if branch.get("gc", True) and not _isinf(branch["zs"]):
            return complex(branch["zpp"]) - complex(branch["zm"]) ** 2 / complex(branch["zs"])
        return complex(branch["zpp"])
    # proxy -- a broken shield keeps its phase conductor (length proxy)
    zs = branch.get("zs")
    if branch.get("gc", True) and zs is not None and not _isinf(zs):
        return complex(zs)
    length = branch.get("length") or 1.0
    return complex(length if length > 0 else 1.0, 0.0)


def phase_split(net: dict, phase_choice: str = "declared") -> Dict[str, Dict[str, complex]]:
    """Per-unit phase current per branch and source (one-way model).

    Solved by MNA on the phase-only network with the branch currents as
    unknowns; the fault bus is the reference node and one ampere enters at
    the source bus.

    Parameters
    ----------
    net : dict
        Reference network.
    phase_choice : str
        See :func:`phase_impedance`.

    Returns
    -------
    dict
        ``alpha[source][branch]`` -- phase current (direction a -> b) per
        ampere of that source's fault current. Zero for sources on the fault
        bus or outside the phase component of the fault bus.
    """
    buses = _active_buses(net)
    fault_bus = net["fault_bus"]
    usable: List[str] = []
    z_phase: Dict[str, complex] = {}
    for name, d in net["branches"].items():
        if not d.get("active", True):
            continue
        if d["a"] not in buses or d["b"] not in buses:
            continue
        z = phase_impedance(d, phase_choice)
        if _isinf(z):
            continue
        usable.append(name)
        z_phase[name] = z
    component = _components(
        buses, [(net["branches"][n]["a"], net["branches"][n]["b"]) for n in usable], fault_bus
    )
    nodes = [b for b in buses if b in component]
    in_component = [n for n in usable if net["branches"][n]["a"] in component]
    v_index = {b: i for i, b in enumerate([b for b in nodes if b != fault_bus])}
    nv = len(v_index)
    nb = len(in_component)
    alpha: Dict[str, Dict[str, complex]] = {}
    for source_name, source in net["sources"].items():
        alpha[source_name] = {n: 0j for n in net["branches"]}
        source_bus = source["bus"]
        if source_bus == fault_bus or source_bus not in component:
            continue
        A = np.zeros((nv + nb, nv + nb), dtype=complex)
        rhs = np.zeros(nv + nb, dtype=complex)
        for k, n in enumerate(in_component):
            a, b = net["branches"][n]["a"], net["branches"][n]["b"]
            # KCL: branch current a -> b leaves a and enters b
            if a in v_index:
                A[v_index[a], nv + k] += 1.0
            if b in v_index:
                A[v_index[b], nv + k] -= 1.0
            # KVL: V_a - V_b - Z * I = 0
            row = nv + k
            if a in v_index:
                A[row, v_index[a]] += 1.0
            if b in v_index:
                A[row, v_index[b]] -= 1.0
            A[row, nv + k] -= z_phase[n]
        rhs[v_index[source_bus]] = 1.0
        x = np.linalg.solve(A, rhs)
        for k, n in enumerate(in_component):
            alpha[source_name][n] = complex(x[nv + k])
    return alpha


# ---------------------------------------------------------------------------
# earth network, one-way coupling
# ---------------------------------------------------------------------------


def solve_oneway(
    net: dict,
    phase_choice: str = "declared",
    thevenin_phase: str = "actual",
    with_mutual: bool = True,
) -> dict:
    """Earth-side solution with the phase currents imposed (documented model).

    Unknowns: the earth potential of every active bus, the current of every
    shield branch, and the loop current of every Thevenin source that is not
    on the fault bus.

    Parameters
    ----------
    net : dict
        Reference network.
    phase_choice : str
        See :func:`phase_impedance`.
    thevenin_phase : {"actual", "norton"}
        Which current of a Thevenin source drives the coupling.
    with_mutual : bool
        ``False`` drops the coupling EMFs (the reference solution of the
        EPR-based reduction factor).

    Returns
    -------
    dict
        Physical frame: ``u`` (potentials), ``I_s`` (shield currents a -> b),
        ``I_p`` (phase currents used), ``I_a`` (electrode currents into the
        soil), ``I_src`` (fault current per source) and ``I_F`` (their sum).
    """
    buses = _active_buses(net)
    bus_index = {b: i for i, b in enumerate(buses)}
    n_bus = len(buses)
    fault_bus = net["fault_bus"]
    scaling = net.get("scaling", 1.0)
    shields = _shield_branches(net, bus_index)
    alpha = phase_split(net, phase_choice)
    # A source contributes when any active branch connects it to the fault.
    edges = [
        (d["a"], d["b"])
        for d in net["branches"].values()
        if d.get("active", True) and d["a"] in bus_index and d["b"] in bus_index
    ]
    reach = _components(buses, edges, fault_bus) if fault_bus in bus_index else set()
    sources = [
        s for s, d in net["sources"].items() if d["bus"] in bus_index and d["bus"] in reach
    ]
    thevenin = [
        s for s in sources
        if net["sources"][s]["kind"] == "voltage" and net["sources"][s]["bus"] != fault_bus
    ]
    n_shield = len(shields)
    size = n_bus + n_shield + len(thevenin)
    A = np.zeros((size, size), dtype=complex)
    rhs = np.zeros(size, dtype=complex)
    for b in buses:
        z = net["buses"][b]["z"]
        if z is not None and not _isinf(z):
            A[bus_index[b], bus_index[b]] += 1.0 / complex(z)
    known: Dict[str, complex] = {}
    for s in sources:
        d = net["sources"][s]
        if d["kind"] == "current":
            known[s] = scaling * complex(d["I"])
        elif d["bus"] == fault_bus:
            known[s] = scaling * complex(d["E"]) / complex(d["Zsrc"])
    for s, current in known.items():
        source_bus = net["sources"][s]["bus"]
        rhs[bus_index[fault_bus]] += current
        rhs[bus_index[source_bus]] -= current
    for k, n in enumerate(shields):
        d = net["branches"][n]
        ia, ib = bus_index[d["a"]], bus_index[d["b"]]
        column = n_bus + k
        A[ia, column] += 1.0
        A[ib, column] -= 1.0
        row = n_bus + k
        A[row, ia] += 1.0
        A[row, ib] -= 1.0
        A[row, column] -= complex(d["zs"])
        if with_mutual:
            zm = complex(d["zm"])
            for s in sources:
                share = alpha[s].get(n, 0j)
                if share == 0:
                    continue
                d_s = net["sources"][s]
                if s in known:
                    rhs[row] += zm * share * known[s]
                elif thevenin_phase == "norton":
                    rhs[row] += zm * share * scaling * complex(d_s["E"]) / complex(d_s["Zsrc"])
                else:
                    A[row, n_bus + n_shield + thevenin.index(s)] -= zm * share
    for t, s in enumerate(thevenin):
        d = net["sources"][s]
        column = n_bus + n_shield + t
        # the loop current enters the fault earthing node, leaves the source one
        A[bus_index[fault_bus], column] -= 1.0
        A[bus_index[d["bus"]], column] += 1.0
        # E * scaling = Z_src * I + u_F - u_S
        A[column, column] = complex(d["Zsrc"])
        A[column, bus_index[fault_bus]] += 1.0
        A[column, bus_index[d["bus"]]] -= 1.0
        rhs[column] = scaling * complex(d["E"])
    x = np.linalg.solve(A, rhs)
    u = {b: complex(x[bus_index[b]]) for b in buses}
    shield_currents = {n: 0j for n in net["branches"]}
    for k, n in enumerate(shields):
        shield_currents[n] = complex(x[n_bus + k])
    source_currents = dict(known)
    for t, s in enumerate(thevenin):
        source_currents[s] = complex(x[n_bus + n_shield + t])
    phase_currents = {n: 0j for n in net["branches"]}
    for s in sources:
        d_s = net["sources"][s]
        if s in known:
            used = known[s]
        elif thevenin_phase == "norton":
            used = scaling * complex(d_s["E"]) / complex(d_s["Zsrc"])
        else:
            used = source_currents[s]
        for n in net["branches"]:
            phase_currents[n] += alpha[s].get(n, 0j) * used
    electrode_currents = {}
    for b in buses:
        z = net["buses"][b]["z"]
        if z is None or _isinf(z):
            electrode_currents[b] = 0j
        else:
            electrode_currents[b] = u[b] / complex(z)
    return {
        "u": u,
        "I_s": shield_currents,
        "I_p": phase_currents,
        "I_a": electrode_currents,
        "I_src": source_currents,
        "I_F": sum(source_currents.values(), 0j),
    }


def driving_point_impedance(net: dict, bus: str) -> complex:
    """Driving-point impedance of the earthing network at ``bus``.

    One ampere is injected into the earthing node of ``bus`` against remote
    earth, with no source, no coupling and no Thevenin loop: the unknowns are
    the potentials and the shield currents, exactly as in
    :func:`solve_oneway`, so the result shares no code with a nodal
    admittance matrix.

    Parameters
    ----------
    net : dict
        Reference network.
    bus : str
        Bus at which the impedance is seen.

    Returns
    -------
    complex
        ``u[bus]`` per ampere injected at ``bus``.
    """
    buses = _active_buses(net)
    bus_index = {b: i for i, b in enumerate(buses)}
    n_bus = len(buses)
    shields = _shield_branches(net, bus_index)
    size = n_bus + len(shields)
    A = np.zeros((size, size), dtype=complex)
    rhs = np.zeros(size, dtype=complex)
    for b in buses:
        z = net["buses"][b]["z"]
        if z is not None and not _isinf(z):
            A[bus_index[b], bus_index[b]] += 1.0 / complex(z)
    for k, n in enumerate(shields):
        d = net["branches"][n]
        ia, ib = bus_index[d["a"]], bus_index[d["b"]]
        column = n_bus + k
        A[ia, column] += 1.0
        A[ib, column] -= 1.0
        A[column, ia] += 1.0
        A[column, ib] -= 1.0
        A[column, column] -= complex(d["zs"])
    rhs[bus_index[bus]] = 1.0
    x = np.linalg.solve(A, rhs)
    return complex(x[bus_index[bus]])


# ---------------------------------------------------------------------------
# fully coupled multi-conductor model
# ---------------------------------------------------------------------------


def solve_coupled(net: dict, R_F: complex = 0.0) -> dict:
    """Phase conductors, shields and electrodes solved simultaneously.

    Unknowns: phase-node voltages ``V`` (buses of the phase component of the
    fault bus), earth-node voltages ``u`` (all active buses), the phase and
    shield current of every active branch, one current per source and the
    current through the fault bond ``V_F - u_F = R_F * I_F``. Every branch
    needs ``zpp``.

    Parameters
    ----------
    net : dict
        Reference network with ``zpp`` filled for every active branch.
    R_F : complex
        Fault resistance between phase conductor and earthing node.

    Returns
    -------
    dict
        Physical frame: ``u``, ``V``, ``I_s``, ``I_p``, ``I_a``, ``I_src``,
        ``I_F``.
    """
    buses = _active_buses(net)
    fault_bus = net["fault_bus"]
    scaling = net.get("scaling", 1.0)
    branches = [
        name for name, d in net["branches"].items()
        if d.get("active", True) and d["a"] in buses and d["b"] in buses
    ]
    has_shield = {
        n: (net["branches"][n].get("gc", True) and not _isinf(net["branches"][n]["zs"]))
        for n in branches
    }
    has_phase0 = {n: not _isinf(net["branches"][n]["zpp"]) for n in branches}
    phase_component = _components(
        buses,
        [(net["branches"][n]["a"], net["branches"][n]["b"]) for n in branches if has_phase0[n]],
        fault_bus,
    )
    has_phase = {n: has_phase0[n] and net["branches"][n]["a"] in phase_component for n in branches}
    phase_buses = [b for b in buses if b in phase_component]
    column: Dict[tuple, int] = {}
    c = 0
    for b in phase_buses:
        column[("V", b)] = c
        c += 1
    for b in buses:
        column[("u", b)] = c
        c += 1
    for n in branches:
        if has_phase[n]:
            column[("Ip", n)] = c
            c += 1
        if has_shield[n]:
            column[("Is", n)] = c
            c += 1
    sources = [s for s, d in net["sources"].items() if d["bus"] in phase_component]
    for s in sources:
        column[("I", s)] = c
        c += 1
    column[("IF",)] = c
    c += 1
    size = c
    A = np.zeros((size, size), dtype=complex)
    rhs = np.zeros(size, dtype=complex)
    r = 0
    for b in phase_buses:  # phase KCL
        for n in branches:
            if not has_phase[n]:
                continue
            d = net["branches"][n]
            if d["a"] == b:
                A[r, column[("Ip", n)]] += 1.0
            if d["b"] == b:
                A[r, column[("Ip", n)]] -= 1.0
        for s in sources:
            if net["sources"][s]["bus"] == b:
                A[r, column[("I", s)]] -= 1.0
        if b == fault_bus:
            A[r, column[("IF",)]] += 1.0
        r += 1
    for b in buses:  # earth KCL
        z = net["buses"][b]["z"]
        if z is not None and not _isinf(z):
            A[r, column[("u", b)]] += 1.0 / complex(z)
        for n in branches:
            if not has_shield[n]:
                continue
            d = net["branches"][n]
            if d["a"] == b:
                A[r, column[("Is", n)]] += 1.0
            if d["b"] == b:
                A[r, column[("Is", n)]] -= 1.0
        for s in sources:
            if net["sources"][s]["bus"] == b:
                A[r, column[("I", s)]] += 1.0
        if b == fault_bus:
            A[r, column[("IF",)]] -= 1.0
        r += 1
    for n in branches:  # branch laws
        d = net["branches"][n]
        a, b = d["a"], d["b"]
        zpp, zps = complex(d["zpp"]), complex(d["zm"])
        if has_phase[n]:
            A[r, column[("V", a)]] += 1.0
            A[r, column[("V", b)]] -= 1.0
            A[r, column[("Ip", n)]] -= zpp
            if has_shield[n]:
                A[r, column[("Is", n)]] -= zps
            r += 1
        if has_shield[n]:
            A[r, column[("u", a)]] += 1.0
            A[r, column[("u", b)]] -= 1.0
            A[r, column[("Is", n)]] -= complex(d["zs"])
            if has_phase[n]:
                A[r, column[("Ip", n)]] -= zps
            r += 1
    for s in sources:  # sources
        d = net["sources"][s]
        if d["kind"] == "current":
            A[r, column[("I", s)]] = 1.0
            rhs[r] = scaling * complex(d["I"])
        else:  # V_phase - u = E - Z_src * I
            A[r, column[("V", d["bus"])]] += 1.0
            A[r, column[("u", d["bus"])]] -= 1.0
            A[r, column[("I", s)]] += complex(d["Zsrc"])
            rhs[r] = scaling * complex(d["E"])
        r += 1
    A[r, column[("V", fault_bus)]] += 1.0  # fault bond
    A[r, column[("u", fault_bus)]] -= 1.0
    A[r, column[("IF",)]] -= R_F
    r += 1
    assert r == size, (r, size)
    x = np.linalg.solve(A, rhs)
    u = {b: complex(x[column[("u", b)]]) for b in buses}
    return {
        "u": u,
        "V": {b: complex(x[column[("V", b)]]) for b in phase_buses},
        "I_s": {n: (complex(x[column[("Is", n)]]) if n in branches and has_shield[n] else 0j)
                for n in net["branches"]},
        "I_p": {n: (complex(x[column[("Ip", n)]]) if n in branches and has_phase[n] else 0j)
                for n in net["branches"]},
        "I_a": {b: (0j if net["buses"][b]["z"] is None or _isinf(net["buses"][b]["z"])
                    else u[b] / complex(net["buses"][b]["z"])) for b in buses},
        "I_src": {s: complex(x[column[("I", s)]]) for s in sources},
        "I_F": complex(x[column[("IF",)]]),
    }


# ---------------------------------------------------------------------------
# frame mapping and derived quantities
# ---------------------------------------------------------------------------


def to_package_frame(solution: dict) -> dict:
    """Map a physical-frame solution to the package's sign frame.

    Parameters
    ----------
    solution : dict
        Output of :func:`solve_oneway` or :func:`solve_coupled`.

    Returns
    -------
    dict
        ``u`` and ``ia`` negated, ``i_s`` unchanged (the package reports the
        physical shield current in from -> to direction).
    """
    return {
        "u": {b: -v for b, v in solution["u"].items()},
        "ia": {b: -v for b, v in solution["I_a"].items()},
        "i_s": dict(solution["I_s"]),
    }


def half_plane_split(
    currents: Dict[str, complex],
    reference: Optional[str] = None,
    *,
    anchor: Optional[complex] = None,
    noise_rel: float = 0.0,
) -> Tuple[complex, List[str]]:
    """Brute-force earth-return split: the half-plane with the largest sum.

    Every half-plane boundary direction on a fine grid plus every direction
    defined by a phasor itself is tried; the half-plane ``Re(v * conj(d)) >
    0`` with the largest ``|sum|`` wins. Independent of the package's sorted
    sliding window.

    Orientation (which group is reported): the group containing
    ``reference``; if ``reference`` carries no (non-noise) current, the group
    whose sum has a positive projection on ``anchor``; otherwise the smaller
    group, ties broken by name.

    Parameters
    ----------
    currents : dict of str to complex
        Electrode currents (package frame).
    reference : str, optional
        Bus that anchors the group (the fault bus).
    anchor : complex, optional
        Fallback orientation phasor (the fault-bus EPR).
    noise_rel : float
        Currents with ``|I| <= noise_rel * max|I|`` count as zero.

    Returns
    -------
    tuple of (complex, list of str)
        The group sum and its sorted members; ``(0j, [])`` without currents.
    """
    names = [n for n, v in currents.items() if v != 0]
    if names and noise_rel > 0:
        peak = max(abs(currents[n]) for n in names)
        names = [n for n in names if abs(currents[n]) > noise_rel * peak]
    if not names:
        return 0j, []
    values = np.array([currents[n] for n in names])
    best = 0j
    best_set: List[str] = []
    candidates = set()
    for angle in np.angle(values):
        for eps in (-1e-9, 1e-9):
            candidates.add(float(angle) + math.pi / 2 + eps)
            candidates.add(float(angle) - math.pi / 2 + eps)
    for theta in np.linspace(-math.pi, math.pi, 721):
        candidates.add(float(theta))
    for theta in sorted(candidates):
        direction = cmath.exp(1j * theta)
        selected = [i for i, v in enumerate(values) if (v * direction.conjugate()).real > 0]
        total = values[selected].sum() if selected else 0j
        if abs(total) > abs(best) + 1e-15:
            best = complex(total)
            best_set = [names[i] for i in selected]
    rest = [n for n in names if n not in best_set]
    swap = False
    if reference is not None and reference in rest:
        swap = True
    elif reference is not None and reference in best_set:
        swap = False
    elif anchor is not None and anchor != 0 and (best * complex(anchor).conjugate()).real != 0:
        swap = (best * complex(anchor).conjugate()).real < 0
    elif rest and len(rest) != len(best_set):
        swap = len(rest) < len(best_set)
    elif rest:
        swap = sorted(rest) < sorted(best_set)
    if swap:
        best_set, best = rest, -best
    return complex(best), sorted(best_set)


def reduction_factor_epr(net: dict, **kwargs) -> float:
    """``|u_F with coupling| / |u_F without coupling|`` (one-way model).

    Parameters
    ----------
    net : dict
        Reference network.
    **kwargs
        Passed to :func:`solve_oneway`.

    Returns
    -------
    float
        The EPR-based reduction factor.
    """
    with_coupling = solve_oneway(net, with_mutual=True, **kwargs)
    without = solve_oneway(net, with_mutual=False, **kwargs)
    fault_bus = net["fault_bus"]
    return abs(with_coupling["u"][fault_bus]) / abs(without["u"][fault_bus])


def max_relative_deviation(
    a: Dict[str, complex], b: Dict[str, complex], scale: Optional[float] = None
) -> float:
    """Normwise relative deviation ``max|a - b| / max|b|`` over common keys.

    Parameters
    ----------
    a, b : dict of str to complex
        The two vectors.
    scale : float, optional
        Normalisation; defaults to ``max|b|``.

    Returns
    -------
    float
        ``0.0`` when both are empty.
    """
    keys = [k for k in a if k in b]
    if not keys:
        return 0.0
    if scale is None:
        scale = max(max(abs(b[k]) for k in keys), 1e-300)
    return max(abs(a[k] - b[k]) for k in keys) / scale

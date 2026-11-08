# api/examples.py
"""
The documented example networks, as project templates.

A user interface offers "new project from template", and the service-layer
tests need networks whose numbers are documented. Both use the networks of
the documentation, rebuilt here through the public API exactly as the pages
build them:

* ``"quickstart"`` -- the network of ``docs/getting-started/first-calculation.md``
  (two substations, one cable), at five frequencies and with a fault whose
  scalings cover 50 Hz only;
* ``"minimal"`` -- ``docs/examples/minimal.ipynb``: a three-bus cable line
  at 50 Hz whose reduction factor has a closed form;
* ``"mv_ring"`` -- ``docs/examples/mv_ring.ipynb``: a 20-station 20 kV cable
  ring, 1 kA at 50 Hz fed at ``b01``, fault ``F1`` at ``b10``.

Each builder returns a fresh, unsolved :class:`~groundinsight.Network` and
touches no global state.
"""

import logging
from typing import Callable, Dict

import groundinsight as gi

logger = logging.getLogger(__name__)

__all__ = ["EXAMPLES", "build_minimal", "build_mv_ring", "build_quickstart"]


def build_quickstart() -> gi.Network:
    """
    The network of the first tutorial, at five frequencies.

    Returns
    -------
    Network
        ``QuickstartNet``: buses ``bus_source`` and ``bus_fault``, cable
        ``cable_1`` (5 km), source ``substation_infeed`` (1000 A at 50 Hz plus
        harmonics), fault ``fault_at_remote_bus``.

    Examples
    --------
    >>> net = build_quickstart()
    >>> sorted(net.buses), list(net.faults)
    (['bus_fault', 'bus_source'], ['fault_at_remote_bus'])
    """
    net = gi.create_network(name="QuickstartNet", frequencies=[50, 250, 350, 450, 550])
    bus_type = gi.BusType(
        name="SubstationBus",
        description="Lumped substation grounding grid",
        system_type="Substation",
        voltage_level=20,
        impedance_formula="rho * 0.01 + j * f * 1/50 * 0.1",
    )
    cable_type = gi.BranchType(
        name="MSCable",
        description="20 kV single-core cable with shield",
        grounding_conductor=True,
        self_impedance_formula="(0.25 + j * f * 0.012) * l",
        mutual_impedance_formula="(0.0  + j * f * 0.012) * l",
    )
    for name in ("bus_source", "bus_fault"):
        gi.create_bus(name=name, type=bus_type, network=net, specific_earth_resistance=100.0)
    gi.create_branch(
        name="cable_1",
        type=cable_type,
        from_bus="bus_source",
        to_bus="bus_fault",
        length=5.0,
        specific_earth_resistance=100.0,
        network=net,
    )
    gi.create_source(
        name="substation_infeed",
        bus="bus_source",
        values={50: 1000.0, 250: 200.0, 350: 100.0, 450: 50.0, 550: 25.0},
        network=net,
    )
    gi.create_fault(
        name="fault_at_remote_bus",
        bus="bus_fault",
        description="Single-phase-to-ground fault at bus_fault",
        scalings={50: 1.0},
        network=net,
    )
    return net


def build_minimal() -> gi.Network:
    """
    The three-bus cable line of the minimal example.

    Returns
    -------
    Network
        ``SmokeTest``: ``bus1``-``bus2``-``bus3`` with 1 km cables ``b12`` and
        ``b23``, 100 A at 50 Hz fed at ``bus1``, fault ``fault`` at ``bus3``.

    Examples
    --------
    >>> net = build_minimal()
    >>> list(net.branches), net.frequencies
    (['b12', 'b23'], [50.0])
    """
    net = gi.create_network(
        name="SmokeTest", frequencies=[50], description="3-bus MV-cable line, single frequency"
    )
    bus_type = gi.BusType(
        name="BusUnit",
        description="Unit-like bus impedance for plausibility tests",
        system_type="Grounded",
        voltage_level=20.0,
        impedance_formula="rho * 0 + 1.0 + I * f * 0",
    )
    cable = gi.BranchType(
        name="MSCable",
        description="MV cable reference branch",
        grounding_conductor=True,
        self_impedance_formula="(rho * 0 + 0.25 + I * 0.6)*l",
        mutual_impedance_formula="(rho * 0 + 0.0 + I * 0.6)*l",
    )
    for i in range(1, 4):
        gi.create_bus(name=f"bus{i}", type=bus_type, network=net)
    gi.create_branch(name="b12", type=cable, from_bus="bus1", to_bus="bus2", length=1.0, network=net)
    gi.create_branch(name="b23", type=cable, from_bus="bus2", to_bus="bus3", length=1.0, network=net)
    gi.create_source(name="src", bus="bus1", values={50: 100.0}, network=net)
    gi.create_fault(name="fault", bus="bus3", scalings={50: 1.0}, network=net)
    return net


def build_mv_ring(n_buses: int = 20) -> gi.Network:
    """
    The 20 kV cable ring of the ``mv_ring`` example.

    Per section: NA2XS(F)2Y 1x300 RM/25, 0.5 km, ``R = 0.25 Ohm/km``,
    ``L = 1.9 mH/km``, Carson mutual ``0.05 Ohm/km + 1.85 mH/km``; every
    station 5 Ohm in parallel with the lumped cable capacitance (150 nF).

    Parameters
    ----------
    n_buses : int, optional
        Number of stations; the documented network has 20.

    Returns
    -------
    Network
        ``mv_ring_<n>`` at 50 Hz: buses ``b01`` ... , branches
        ``L_01_02`` ..., source ``src`` (1 kA at ``b01``), fault ``F1`` at
        the opposite station (``b10`` for 20 stations).

    Raises
    ------
    ValueError
        For fewer than three stations.

    Examples
    --------
    >>> net = build_mv_ring()
    >>> len(net.buses), net.faults["F1"].bus
    (20, 'b10')
    """
    if n_buses < 3:
        raise ValueError(f"a ring needs at least 3 stations, got {n_buses}")
    r_per_km, l_per_km, c_per_km = 0.25, 1.9e-3, 300e-9
    r_mut_per_km, m_per_km = 0.05, 1.85e-3
    section_km, r_bus = 0.5, 5.0
    c_bus = c_per_km * section_km
    bus_type = gi.BusType(
        name="MV_substation",
        description="20 kV substation grounding, 5 ohm; cable C lumped automatically",
        system_type="Substation",
        voltage_level=20.0,
        impedance_formula=f"((rho * 0 + {r_bus}) / (1 + I * 2 * pi * f * {r_bus} * {c_bus}))",
        R_formula=f"rho * 0 + {r_bus}",
    )
    branch_type = gi.BranchType(
        name="NA2XS_300_25",
        description="NA2XS(F)2Y 1x300 RM/25 with Carson mutual coupling",
        grounding_conductor=True,
        self_impedance_formula=f"((rho * 0 + {r_per_km}) + I * 2 * pi * f * {l_per_km}) * l",
        mutual_impedance_formula=f"((rho * 0 + {r_mut_per_km}) + I * 2 * pi * f * {m_per_km}) * l",
        R_self_formula=f"({r_per_km}) * l",
        L_self_formula=f"({l_per_km}) * l",
        C_self_formula=f"({c_per_km}) * l",
        R_mutual_formula=f"({r_mut_per_km}) * l",
        M_mutual_formula=f"({m_per_km}) * l",
    )
    net = gi.create_network(name=f"mv_ring_{n_buses}", frequencies=[50.0])
    for k in range(1, n_buses + 1):
        gi.create_bus(name=f"b{k:02d}", type=bus_type, network=net)
    for k in range(1, n_buses + 1):
        nxt = (k % n_buses) + 1
        gi.create_branch(
            name=f"L_{k:02d}_{nxt:02d}",
            type=branch_type,
            from_bus=f"b{k:02d}",
            to_bus=f"b{nxt:02d}",
            length=section_km,
            network=net,
        )
    gi.create_source(name="src", bus="b01", values={50.0: 1000.0 + 0.0j}, network=net)
    gi.create_fault(name="F1", bus=f"b{n_buses // 2:02d}", scalings={50.0: 1.0}, network=net)
    return net


#: Template name to builder, for "new project from template".
EXAMPLES: Dict[str, Callable[[], gi.Network]] = {
    "quickstart": build_quickstart,
    "minimal": build_minimal,
    "mv_ring": build_mv_ring,
}

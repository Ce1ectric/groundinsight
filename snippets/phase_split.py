# docs/snippets/phase_split.py
"""
How the phase current divides in a ring, and when the one-way coupling is exact.

The example of the explanation page "Phase currents in rings and meshes": a
ring ``S - A - F - B - S`` fed at ``S`` (1 kA, 50 Hz) with the earth fault at
``F``. Route 1 (``S - A - F``, 2 x 1.2 km) is a cable, route 2 (``S - B - F``,
2 x 1.5 km) is either the same cable (*homogeneous ring*) or an overhead line
with an earth wire (*mixed ring*). All per-km values are illustrative 50 Hz
values: for each construction the shield-earth loop ``Z_s'``, the coupling
``Z_m'`` and the phase-earth loop ``Z_pp'``; the shielded phase-earth loop
``Z_red' = Z_pp' - Z_m'^2 / Z_s'`` follows from them.

Every ring is solved three times, with ``phase_impedance_formula`` set to
``Z_red``, to ``Z_pp`` and left out (the proxy). The test suite solves the
same rings with a fully coupled multi-conductor model -- phase conductors,
shields and electrodes in one system -- and compares.

Run it as a script: ``python docs/snippets/phase_split.py``.
"""

import warnings

import groundinsight as gi

# --8<-- [start:constructions]
CABLE = {"zs": 0.29 + 0.625j, "zm": 0.049 + 0.625j, "zpp": 0.174 + 0.740j}
OVERHEAD_LINE = {"zs": 0.65 + 0.785j, "zm": 0.049 + 0.372j, "zpp": 0.355 + 0.759j}


def z_red(construction):
    """Phase-earth loop with the shield bonded at both ends: Z_pp - Z_m^2/Z_s."""
    return construction["zpp"] - construction["zm"] ** 2 / construction["zs"]


def per_km(z):
    """A per-km impedance at 50 Hz as a formula (reactance proportional to f)."""
    return f"({z.real!r} + {z.imag!r}*j*f/50)*l"
# --8<-- [end:constructions]


# --8<-- [start:ring]
def ring(route_2, phase):
    """The ring S-A-F-B-S; ``phase`` is "Z_red", "Z_pp" or "proxy"."""
    net = gi.create_network(name=f"ring_{phase}", frequencies=[50.0])
    electrodes = {"S": "0.2", "A": "2.0", "F": "2.0",
                  "B": "15.0" if route_2 is OVERHEAD_LINE else "2.0"}
    for bus, z_b in electrodes.items():
        bus_type = gi.BusType(name=f"electrode_{bus}", system_type="MV",
                              voltage_level=20.0, impedance_formula=z_b)
        gi.create_bus(name=bus, type=bus_type, network=net)

    def branch_type(name, construction):
        phase_formula = {"Z_red": per_km(z_red(construction)),
                         "Z_pp": per_km(construction["zpp"]),
                         "proxy": None}[phase]
        return gi.BranchType(name=name, grounding_conductor=True,
                             self_impedance_formula=per_km(construction["zs"]),
                             mutual_impedance_formula=per_km(construction["zm"]),
                             phase_impedance_formula=phase_formula)

    route_1_type = branch_type("route_1", CABLE)
    route_2_type = branch_type("route_2", route_2)
    for name, a, b, kind, length in (("SA", "S", "A", route_1_type, 1.2),
                                     ("AF", "A", "F", route_1_type, 1.2),
                                     ("SB", "S", "B", route_2_type, 1.5),
                                     ("BF", "B", "F", route_2_type, 1.5)):
        gi.create_branch(name=name, type=kind, from_bus=a, to_bus=b, length=length,
                         network=net)
    gi.create_source(name="infeed", bus="S", values={50.0: 1000.0}, network=net)
    gi.create_fault(name="F1", bus="F", network=net)
    return net


def split(route_2, phase):
    """Phase current on route 1 in A: the current divider of the two routes.

    The proxy of a branch with a grounding conductor is its shield impedance.
    """
    per_km = {"Z_red": z_red, "Z_pp": lambda c: c["zpp"], "proxy": lambda c: c["zs"]}[phase]
    z_route_1 = per_km(CABLE) * 2 * 1.2
    z_route_2 = per_km(route_2) * 2 * 1.5
    return abs(1000.0 * z_route_2 / (z_route_1 + z_route_2))


def epr_at_fault(net):
    """EPR at the fault bus F in V."""
    with warnings.catch_warnings():
        # the proxy variant is announced by a ModelAssumptionWarning PHASE_PROXY
        warnings.simplefilter("ignore", gi.ModelAssumptionWarning)
        result = gi.run_fault(net, "F1")
    return abs(complex(next(b.uepr_freq[50.0] for b in result.buses if b.name == "F")))


TABLE = {
    (label, phase): (split(route_2, phase), epr_at_fault(ring(route_2, phase)))
    for label, route_2 in (("homogeneous", CABLE), ("mixed", OVERHEAD_LINE))
    for phase in ("Z_red", "Z_pp", "proxy")
}
# --8<-- [end:ring]

if __name__ == "__main__":
    for (label, phase), (i_route_1, u_fault) in TABLE.items():
        print(f"{label:>11} ring, {phase:>5}: I_p(route 1) = {i_route_1:7.2f} A,"
              f" |EPR(F)| = {u_fault:8.3f} V")

# tests/test_capacitive_import.py
"""
The pandapower import takes over capacitance and voltage level on request.

``ImportDefaults.derive_capacitance`` gives every imported line the
capacitance of a phase to earth: ``c0_nf_per_km`` where the line has it; for
a cable the operating capacitance otherwise; nothing for an overhead line
without ``c0`` (its operating capacitance is not its capacitance to earth).
Second way: the capacitive earth-fault current of the imported network
against ``sqrt(3) * omega * C * U`` from the pandapower table.
"""

import math

import pytest

import groundinsight as gi

pp = pytest.importorskip("pandapower")

CABLE = "NA2XS2Y 1x150 RM/25 12/20 kV"
LINE = "48-AL1/8-ST1A 20.0"


def _defaults(**overrides) -> gi.ImportDefaults:
    station = gi.BusType(name="station", system_type="MV", voltage_level=20.0, impedance_formula="rho/50")
    branch = gi.BranchType(name="generic", grounding_conductor=True, self_impedance_formula="(0.3 + j*f/50*0.7)*l",
                           mutual_impedance_formula="(j*f/50*0.6)*l")
    return gi.ImportDefaults(rho=100.0, frequencies=[50.0], default_bus_type=station, default_branch_type=branch,
                             **overrides)


def _net(c0_overhead=None):
    net = pp.create_empty_network(name="mixed")
    buses = [pp.create_bus(net, vn_kv=20.0, name=name) for name in ("UW", "A", "B", "C")]
    pp.create_line(net, buses[0], buses[1], length_km=4.0, std_type=CABLE, name="cable 1")
    pp.create_line(net, buses[1], buses[2], length_km=6.0, std_type=CABLE, name="cable 2")
    pp.create_line(net, buses[2], buses[3], length_km=10.0, std_type=LINE, name="overhead")
    if c0_overhead is not None:
        net.line["c0_nf_per_km"] = [float("nan"), float("nan"), c0_overhead]
    return net


def _by_description(network: gi.Network):
    return {branch.description: branch for branch in network.branches.values()}


def test_without_the_switch_nothing_changes():
    plain = gi.from_pandapower(_net(), defaults=_defaults(), voltage_level_kV=20.0)
    for branch in plain.branches.values():
        assert (branch.type.name, branch.type.c_shield_nf_per_km, branch.type.voltage_level) == ("generic", None, None)
    assert _defaults().derive_capacitance is False


def test_a_cable_gets_its_operating_capacitance_an_overhead_line_only_its_c0():
    network = gi.from_pandapower(_net(), defaults=_defaults(derive_capacitance=True), voltage_level_kV=20.0)
    branches = _by_description(network)
    cable, overhead = branches["cable 1"].type, branches["overhead"].type
    assert (cable.c_shield_nf_per_km, cable.c_earth_nf_per_km, cable.voltage_level) == (250.0, None, 20.0)
    assert branches["cable 2"].type is cable                       # one type per kind of line
    assert (overhead.c_shield_nf_per_km, overhead.c_earth_nf_per_km, overhead.voltage_level) == (None, None, 20.0)
    assert len({branch.type.name for branch in network.branches.values()}) == 2

    network = gi.from_pandapower(_net(c0_overhead=5.5), defaults=_defaults(derive_capacitance=True),
                                 voltage_level_kV=20.0)
    assert _by_description(network)["overhead"].type.c_earth_nf_per_km == 5.5


def test_the_capacitive_current_of_the_imported_network_is_that_of_the_pandapower_table():
    net = _net(c0_overhead=5.5)
    network = gi.from_pandapower(net, defaults=_defaults(derive_capacitance=True), voltage_level_kV=20.0)
    gi.create_isolated_neutral(name="20 kV", bus="UW", voltage_level=20.0, network=network)
    area = gi.supply_areas(network).to_dicts()[0]
    capacitance = (250.0 * 4.0 + 250.0 * 6.0 + 5.5 * 10.0) * 1e-9
    assert area["I_CE_A"] == pytest.approx(math.sqrt(3) * 2 * math.pi * 50.0 * capacitance * 20e3, rel=1e-12)
    assert area["length_km"] == pytest.approx(20.0)


def test_with_the_phase_impedance_one_variant_carries_both():
    network = gi.from_pandapower(_net(), defaults=_defaults(derive_capacitance=True, derive_phase_impedance=True),
                                 voltage_level_kV=20.0)
    cable = _by_description(network)["cable 1"].type
    assert cable.phase_impedance_formula is not None and cable.c_shield_nf_per_km == 250.0
    assert cable.name == "generic_line1"
    only_phase = gi.from_pandapower(_net(), defaults=_defaults(derive_phase_impedance=True), voltage_level_kV=20.0)
    assert _by_description(only_phase)["cable 1"].type.name == "generic_phase1"     # as before

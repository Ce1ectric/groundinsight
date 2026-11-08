# docs/snippets/pandapower_topology.py
"""
Topology import from pandapower: the first example of the how-to "Import from
pandapower" and of the I/O page.

``pandapower.networks.example_simple()`` has a 110 kV and a 20 kV level joined
by a transformer. The import keeps the 20 kV buses and lines, bonds the
transformer's 20 kV bus to the main busbar through the closed bus-to-bus
switch, derives a phase-conductor impedance per line and opens the phase
conductor of the line whose switch is open. The earthing side -- station
electrodes and cable screens -- is not in pandapower; it comes from the types
passed in ``ImportDefaults`` (illustrative: the cable system of the data-sheet
how-to for every line).

Needs the optional extra: ``pip install "groundinsight[pandapower]"``.
Run it as a script: ``python docs/snippets/pandapower_topology.py``.
"""

import copy

import pandapower.networks as pn

import groundinsight as gi
from groundinsight.diagnostics import collecting

# --8<-- [start:defaults]
net_pp = pn.example_simple()

station = gi.BusType(name="MV station", system_type="Station", voltage_level=20.0,
                     impedance_formula="2*rho/(3*1.13*sqrt(100.0))")
cable_system = gi.BranchType(
    name="3x NA2XS(F)2Y 1x300RM/25 12/20 kV, trefoil",
    grounding_conductor=True,
    self_impedance_formula=(
        "(0.727/3 + pi**2*1e-4*f + j*4*pi*1e-4*f*log(658*sqrt(rho/f)/0.03151272))*l"
    ),
    mutual_impedance_formula=(
        "(pi**2*1e-4*f + j*4*pi*1e-4*f*log(658*sqrt(rho/f)/0.03151272))*l"
    ),
)
bond = gi.BranchType(name="busbar bond", grounding_conductor=True,
                     self_impedance_formula="(0.05 + j*2*pi*f*1e-3)*l",
                     mutual_impedance_formula="0")
defaults = gi.ImportDefaults(
    rho=100.0,
    frequencies=[50.0],
    default_bus_type=station,
    default_branch_type=cable_system,
    switch_branch_type=bond,          # closed bus-to-bus switches become 1 m branches
    derive_phase_impedance=True,      # phase_impedance_formula from r/x of each line
)
# --8<-- [end:defaults]

# --8<-- [start:preview]
preview = gi.preview_pandapower_import(net_pp, voltage_level_kV=20.0, defaults=defaults)
counts = preview.group_by("status", "kind").len().sort("status", "kind")
# --8<-- [end:preview]

# --8<-- [start:import]
with collecting() as records:
    net = gi.from_pandapower(net_pp, defaults=defaults, voltage_level_kV=20.0,
                             network_name="example_simple, 20 kV")
# --8<-- [end:import]

# --8<-- [start:without_switch_type]
with collecting() as records_without:
    isolated = gi.from_pandapower(
        net_pp, voltage_level_kV=20.0,
        defaults=defaults.model_copy(update={"switch_branch_type": None}))
# --8<-- [end:without_switch_type]

# --8<-- [start:open_line]
gi.create_source(name="infeed", bus="MV Transformer Bus", values={50.0: 1000.0}, network=net)
gi.create_fault(name="F", bus="MV Bus 2", network=net)
gi.run_fault(net, "F")
phase_currents_open = dict(net.electrical_network.phase_currents[50.0])

# For comparison: the same pandapower net with every switch closed.
net_pp_closed = copy.deepcopy(net_pp)
net_pp_closed.switch["closed"] = True
ring = gi.from_pandapower(net_pp_closed, defaults=defaults, voltage_level_kV=20.0)
gi.create_source(name="infeed", bus="MV Transformer Bus", values={50.0: 1000.0}, network=ring)
gi.create_fault(name="F", bus="MV Bus 2", network=ring)
gi.run_fault(ring, "F")
phase_currents_closed = dict(ring.electrical_network.phase_currents[50.0])
line_3 = ring.branches["Line 3"].type
# --8<-- [end:open_line]

if __name__ == "__main__":
    for row in counts.iter_rows(named=True):
        print(f"preview {row['status']:>4}: {row['len']} {row['kind']}")
    print(f"imported: {len(net.buses)} buses, branches {sorted(net.branches)}")
    print("import diagnostics:", [r.code for r in records])
    opened = next(r for r in records if r.code == "IMPORT_LINE_SWITCH_OPEN")
    print("Line 3 imported as:", net.branches["Line 3"].type.name, opened.elements)
    print("derived phase impedance of Line 3, switch closed:", line_3.phase_impedance_formula)
    print(f"without switch_branch_type: {len(isolated.branches)} branches,",
          [r.code for r in records_without])
    for label, currents in (("as imported (Line 3 open)", phase_currents_open),
                            ("every switch closed", phase_currents_closed)):
        shown = ", ".join(f"{b} {abs(currents[b]):.1f} A" for b in ("Line 2", "Line 3", "Line 4"))
        print(f"phase current, {label}: {shown}")

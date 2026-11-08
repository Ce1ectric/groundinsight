# docs/snippets/harmonics.py
"""
Harmonics, fault scalings and DC: the example of the how-to "Harmonics, fault
scalings and DC".

A substation ``UW`` (0.5 Ohm) feeds an earth fault at station ``S`` over 3 km
of the three-phase cable system of the data-sheet how-to (screens bonded at
both ends, rho = 100 Ohm*m). The infeed carries 1000 A at 50 Hz and the 5th
and 7th harmonics (RMS values; illustrative). The script prints the key
figures per frequency and as total RMS, then shows what the fault scalings and
the source values do when they are partial, and a DC study with an ideal bond.

Run it as a script: ``python docs/snippets/harmonics.py``.
"""

import math

import groundinsight as gi
from groundinsight.diagnostics import collecting

# --8<-- [start:network]
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
substation = gi.BusType(name="substation", system_type="Substation", voltage_level=20.0,
                        impedance_formula="0.5")
station = gi.BusType(name="station", system_type="Station", voltage_level=20.0,
                     impedance_formula="2*rho/(3*1.13*sqrt(100.0))")  # ring electrode

FREQUENCIES = [50.0, 250.0, 350.0]
SOURCE_VALUES = {50.0: 1000.0, 250.0: 60.0, 350.0: 40.0}  # A, RMS per frequency
LENGTH_KM = 3.0

net = gi.create_network(name="harmonics", frequencies=FREQUENCIES)
gi.create_bus(name="UW", type=substation, specific_earth_resistance=100.0, network=net)
gi.create_bus(name="S", type=station, specific_earth_resistance=100.0, network=net)
gi.create_branch(name="K", type=cable_system, from_bus="UW", to_bus="S", length=LENGTH_KM,
                 specific_earth_resistance=100.0, network=net)
gi.create_source(name="infeed", bus="UW", values=SOURCE_VALUES, network=net)
gi.create_fault(name="F", bus="S", scalings={50.0: 1.0, 250.0: 1.0, 350.0: 1.0}, network=net)
# --8<-- [end:network]

# --8<-- [start:solve]
gi.run_fault(net, "F")
figures = net.res_fault("F")  # one row per frequency and one RMS row
# --8<-- [end:solve]

# --8<-- [start:exclude]
net.faults["F"].scalings = {50.0: 1.0, 250.0: 0.0, 350.0: 0.0}  # this fault: 50 Hz only
gi.run_fault(net, "F")
u_e_rms_fundamental_only = net.res_fault("F").filter("is_rms")["U_E_V"][0]
# --8<-- [end:exclude]

# --8<-- [start:gaps]
net.faults["F"].scalings = {50.0: 1.0, 250.0: 1.0}  # 350 Hz missing: scaled with 1.0
missing_scaling = gi.run_fault(net, "F")

net.faults["F"].scalings = None  # 1.0 at every frequency, no warning
net.sources["infeed"].values = {50.0: 1000.0, 250.0: 60.0}  # 350 Hz missing: 0 A there
missing_value = gi.run_fault(net, "F")

net.sources["infeed"].values = {16.7: 1000.0}  # no network frequency at all
try:
    gi.run_fault(net, "F")
except ValueError as refused:
    no_value = str(refused).split(". ")[0]

with collecting() as outside:  # announced when the source is created
    gi.create_source(name="extra", bus="UW", values={50.0: 10.0, 150.0: 5.0}, network=net)
# --8<-- [end:gaps]

# --8<-- [start:dc]
dc = gi.create_network(name="dc_chain", frequencies=[0.0])
for name, z in (("A", "0.8"), ("B", "12.0"), ("C", "3.5")):
    gi.create_bus(name=name, network=dc, type=gi.BusType(
        name=f"electrode {name}", system_type="Station", voltage_level=20.0, impedance_formula=z))
bond = gi.BranchType(name="bond", grounding_conductor=True,  # purely inductive: 0 at DC
                     self_impedance_formula="j*2*pi*f*1e-6*l", mutual_impedance_formula="0")
conductor = gi.BranchType(name="conductor", grounding_conductor=True,
                          self_impedance_formula="0.35*l", mutual_impedance_formula="0")
gi.create_branch(name="AB", type=bond, from_bus="A", to_bus="B", length=1.0, network=dc)
gi.create_branch(name="BC", type=conductor, from_bus="B", to_bus="C", length=1.0, network=dc)
gi.create_source(name="injection", bus="A", values={0.0: 1000.0}, network=dc)  # DC, real
gi.create_fault(name="F", bus="C", network=dc)
dc_result = gi.run_fault(dc, "F")
dc_potentials = {bus.name: bus.uepr_freq[0.0].real for bus in dc_result.buses}
stand_in = next(d for d in dc_result.diagnostics if d.code == "DC_LIMIT")
dc_substitute_ohm = float(stand_in.message.split("modelled with ")[1].split(" Ohm")[0])
# --8<-- [end:dc]

# --8<-- [start:refused]
with collecting() as constant_reactance:  # '+ j*0.2' does not vanish at DC
    gi.create_branch(name="CA", network=dc, from_bus="C", to_bus="A", length=1.0,
                     type=gi.BranchType(name="fixed X", grounding_conductor=True,
                                        self_impedance_formula="0.35*l + j*0.2",
                                        mutual_impedance_formula="0"))
try:  # log(f) runs to minus infinity at DC: refused for an electrode
    gi.create_bus(name="D", network=dc, type=gi.BusType(
        name="log", system_type="Station", voltage_level=20.0, impedance_formula="1 + log(f)"))
except ValueError as refused:
    log_refused = str(refused).split(". ")[1]
# --8<-- [end:refused]

if __name__ == "__main__":
    print(f"{'f / Hz':>6} {'I_F / A':>8} {'U_E / V':>8} {'r':>7}")
    for row in figures.iter_rows(named=True):
        f = "RMS" if row["is_rms"] else f"{row['frequency_Hz']:.0f}"
        r = "-" if row["r_current"] is None else f"{row['r_current']:.4f}"
        print(f"{f:>6} {row['I_F_A']:8.1f} {row['U_E_V']:8.2f} {r:>7}")
    print(f"50 Hz only (scalings 1/0/0): U_E, total RMS = {u_e_rms_fundamental_only:.2f} V")
    print("scaling missing at 350 Hz:", [d.code for d in missing_scaling.diagnostics])
    print("source value missing at 350 Hz:", [d.code for d in missing_value.diagnostics])
    print("source without a network frequency: ValueError:", no_value)
    print("source value at 150 Hz:", [d.code for d in outside])
    print("DC chain: " + ", ".join(f"u_{b} = {u:.2f} V" for b, u in dc_potentials.items()))
    print(f"DC stand-in for the bond: {dc_substitute_ohm:.4g} Ohm ({stand_in.code})")
    print("constant reactance at 0 Hz:", [d.code for d in constant_reactance])
    print("log(f) electrode at 0 Hz: ValueError:", log_refused)

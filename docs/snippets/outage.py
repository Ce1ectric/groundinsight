# docs/snippets/outage.py
"""
Outage and what-if studies: the example of the how-to "Outage and what-if
studies".

A 20 kV ring -- substation ``UW`` and three stations ``S1`` to ``S3``, bonded
through the screens of a three-phase cable system -- with an earth fault at
``S2``. Three scenarios take elements out of service: the cable next to the
fault, the cable on the other side of the ring, and both cables of the faulted
station, which islands it. A fourth takes the faulted bus itself out, which
leaves nothing to solve; it is recorded instead of ending the study.

Run it as a script: ``python docs/snippets/outage.py``.
"""

import polars as pl

import groundinsight as gi

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
    phase_impedance_formula=(
        "((0.1 + pi**2*1e-4*f + j*4*pi*1e-4*f*log(658*sqrt(rho/f)/0.00806059))"
        " - (pi**2*1e-4*f + j*4*pi*1e-4*f*log(658*sqrt(rho/f)/0.03151272))**2"
        "/(0.727/3 + pi**2*1e-4*f + j*4*pi*1e-4*f*log(658*sqrt(rho/f)/0.03151272)))*l"
    ),
)
substation = gi.BusType(name="substation", system_type="Substation", voltage_level=20.0,
                        impedance_formula="0.5")
station = gi.BusType(name="station", system_type="Station", voltage_level=20.0,
                     impedance_formula="2*rho/(3*1.13*sqrt(100.0))")

FAULT_BUS = "S2"
net = gi.create_network(name="ring", frequencies=[50.0])
gi.create_bus(name="UW", type=substation, specific_earth_resistance=100.0, network=net)
for name in ("S1", "S2", "S3"):
    gi.create_bus(name=name, type=station, specific_earth_resistance=100.0, network=net)
for name, a, b in (("K1", "UW", "S1"), ("K2", "S1", "S2"), ("K3", "S2", "S3"), ("K4", "S3", "UW")):
    gi.create_branch(name=name, type=cable_system, from_bus=a, to_bus=b, length=1.2,
                     specific_earth_resistance=100.0, network=net)
gi.create_source(name="infeed", bus="UW", values={50.0: 1000.0}, network=net)
gi.create_fault(name="F", bus=FAULT_BUS, network=net)
# --8<-- [end:network]

# --8<-- [start:study]
SCENARIOS = [
    gi.Outage(name="K2 out", disabled_branches=["K2"]),
    gi.Outage(name="K4 out", disabled_branches=["K4"]),
    gi.Outage(name="S2 islanded", disabled_branches=["K2", "K3"]),
]
study = gi.run_outage_study(
    net, fault="F",
    scenarios=SCENARIOS + [gi.Outage(name="fault bus out", disabled_buses=[FAULT_BUS])],
    on_error="record",
)
compared = study.compare_buses()
at_fault_bus = compared.filter(
    (pl.col("bus_name") == FAULT_BUS) & (pl.col("frequency_Hz") == "RMS")
)
# --8<-- [end:study]

# --8<-- [start:against]
against_islanded = study.compare_buses(against="S2 islanded").filter(
    (pl.col("bus_name") == FAULT_BUS) & (pl.col("frequency_Hz") == "RMS")
    & (pl.col("scenario") == "K2 out")
).row(0, named=True)
# --8<-- [end:against]

# --8<-- [start:context]
with gi.outage_context(net, gi.Outage(name="K2 out", disabled_branches=["K2"])):
    one_off = gi.solve(net, "F")  # the network itself stays untouched by solve
k2_out_epr = next(b.uepr for b in one_off.buses if b.name == FAULT_BUS)
# --8<-- [end:context]

if __name__ == "__main__":
    print(f"{'scenario':>12} {'EPR S2 / V':>10} {'delta / V':>10} {'delta / %':>9}")
    for row in at_fault_bus.iter_rows(named=True):
        pct = "null" if row["delta_pct_vs_base"] is None else f"{row['delta_pct_vs_base']:.1f}"
        print(f"{row['scenario']:>12} {row['value']:10.1f} {row['delta_vs_base']:10.1f} {pct:>9}")
    print(f"K2 out against S2 islanded: delta = {against_islanded['delta_vs_S2 islanded']:.1f} V,"
          f" delta / % = {against_islanded['delta_pct_vs_S2 islanded']}")
    print("recorded failures:", study.failures)
    print(f"outage_context, K2 out: EPR S2 = {k2_out_epr:.1f} V")
    out_of_service = [n for n, e in {**net.buses, **net.branches}.items() if not e.active]
    print("out of service after the study:", out_of_service)

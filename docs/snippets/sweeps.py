# docs/snippets/sweeps.py
"""
Parameter sweeps and statistics: the example of the how-to "Parameter sweeps
and statistics".

The 20 kV feeder of the EN 50522 how-to -- substation ``UW`` and five stations
bonded through the screens of a three-phase cable system, earth fault at
``S3`` -- is solved once per parameter point: first for four soil
resistivities of the station electrodes, then for three rho-f characteristics
of the faulted station. The results are stacked into long-format frames,
summarised and classified against bands the caller chooses (illustrative
values, not limits of a standard).

Run it as a script: ``python docs/snippets/sweeps.py``.
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
)
substation = gi.BusType(name="substation", system_type="Substation", voltage_level=20.0,
                        impedance_formula="0.5")
station = gi.BusType(name="station", system_type="Station", voltage_level=20.0,
                     impedance_formula="2*rho/(3*1.13*sqrt(100.0))")  # ring electrode

STATIONS = ["S1", "S2", "S3", "S4", "S5"]
FAULT_BUS = "S3"
net = gi.create_network(name="feeder", frequencies=[50.0, 250.0])
gi.create_bus(name="UW", type=substation, specific_earth_resistance=100.0, network=net)
for name in STATIONS:
    gi.create_bus(name=name, type=station, specific_earth_resistance=100.0, network=net)
names = ["UW"] + STATIONS
for i, (a, b) in enumerate(zip(names[:-1], names[1:], strict=True), start=1):
    gi.create_branch(name=f"K{i}", type=cable_system, from_bus=a, to_bus=b, length=1.2,
                     specific_earth_resistance=100.0, network=net)
gi.create_source(name="infeed", bus="UW", values={50.0: 1000.0, 250.0: 50.0}, network=net)
gi.create_fault(name="F", bus=FAULT_BUS, scalings={50.0: 1.0, 250.0: 1.0}, network=net)
before = net.model_dump_json()
# --8<-- [end:network]

# --8<-- [start:soil]
RHO_VALUES = [30.0, 100.0, 300.0, 1000.0]  # Ohm*m at the station electrodes
soil_points = [
    gi.SweepPoint(label=f"rho={rho:g}", bus_rho={name: rho for name in STATIONS},
                  parameters={"rho_Ohm_m": rho})
    for rho in RHO_VALUES
]
soil = gi.run_sweep(net, fault="F", points=soil_points)
# --8<-- [end:soil]

# --8<-- [start:characteristic]
K_VECTORS = {  # Z = k1*rho + (k2 + j*k3)*f + (k4 + j*k5)*rho*f, illustrative fits
    "small electrode": (0.10, 0.0, 2e-4, 0.0, 1e-6),
    "ring electrode": (0.059, 0.0, 2e-4, 0.0, 1e-6),
    "meshed grid": (0.02, 0.0, 2e-4, 0.0, 1e-6),
}
characteristic = gi.run_sweep(net, fault="F", points=gi.rho_f_points(
    bus=FAULT_BUS, k_vectors=K_VECTORS, frequencies=net.frequencies, rho=100.0))
# --8<-- [end:characteristic]

# --8<-- [start:statistics]
stacked = pl.concat([soil.buses(), characteristic.buses()], how="diagonal")
fault_bus_rms = stacked.filter((pl.col("bus_name") == FAULT_BUS) & pl.col("is_rms"))
summary = gi.summarize(fault_bus_rms, "EPR_V")
EDGES_V = [300.0, 600.0]  # bands of your study -- not limits of a standard
LABELS = ["up to 300 V", "300 V to 600 V", "above 600 V"]
classes = gi.classify(fault_bus_rms, "EPR_V", EDGES_V, labels=LABELS)
# --8<-- [end:statistics]

if __name__ == "__main__":
    print(f"{'point':>16} {'U_E RMS / V':>12}  class")
    for row in classes.iter_rows(named=True):
        print(f"{row['point']:>16} {row['EPR_V']:12.1f}  {row['class']}")
    s = summary.row(0, named=True)
    print(f"n = {s['n']}, min = {s['min']:.1f} V, median = {s['p50']:.1f} V, "
          f"max = {s['max']:.1f} V")
    print("network unchanged:", net.model_dump_json() == before)

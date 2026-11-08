# docs/snippets/thermal.py
"""
Thermal limits of earthing conductors: the example of the how-to "Thermal
limits of earthing conductors".

The 20 kV feeder of the EN 50522 how-to (earth fault at ``S3``) with thermal
data on its types: the cable screens (copper, 69.7 mm2 for the three screens of
the cable system, XLPE-insulated) and, at every station, a copper earthing
conductor of 50 mm2 and a galvanised steel ring electrode of 90 mm2 that the
current reaches from two sides. The infeed has R/X = 0.1 and the protection
clears after 0.5 s (illustrative values). A second run adds a direct current of
150 A to show what the check does with a 0 Hz component.

Run it as a script: ``python docs/snippets/thermal.py``.
"""

import polars as pl

import groundinsight as gi

# --8<-- [start:types]
cable_system = gi.BranchType(
    name="3x NA2XS(F)2Y 1x300RM/25 12/20 kV, trefoil",
    grounding_conductor=True,
    self_impedance_formula=(
        "(0.727/3 + pi**2*1e-4*f + j*4*pi*1e-4*f*log(658*sqrt(rho/f)/0.03151272))*l"
    ),
    mutual_impedance_formula=(
        "(pi**2*1e-4*f + j*4*pi*1e-4*f*log(658*sqrt(rho/f)/0.03151272))*l"
    ),
    conductor_material="Cu",
    cross_section_mm2=69.7,  # three 25 mm2 screens, see the data-sheet how-to
    theta_final_C=gi.final_temperature("Cu", "XLPE"),  # 250 degC, insulation limit
)
substation = gi.BusType(name="substation", system_type="Substation", voltage_level=20.0,
                        impedance_formula="0.5")
station = gi.BusType(
    name="station", system_type="Station", voltage_level=20.0,
    impedance_formula="2*rho/(3*1.13*sqrt(100.0))",
    earthing_conductor_material="Cu",               # carries the full injected current
    earthing_conductor_cross_section_mm2=50.0,
    earthing_conductor_theta_final_C=gi.final_temperature("Cu", "bare"),
    electrode_material="Steel",                     # carries the share into the soil
    electrode_cross_section_mm2=90.0,
    electrode_theta_final_C=gi.final_temperature("Steel", "galvanized"),
    electrode_current_split=0.5,                    # ring electrode, reached from two sides
)
# --8<-- [end:types]

# --8<-- [start:network]
STATIONS = ["S1", "S2", "S3", "S4", "S5"]
FAULT_BUS = "S3"
T_K_S = 0.5
R_TO_X = 0.1
net = gi.create_network(name="feeder", frequencies=[50.0])
gi.create_bus(name="UW", type=substation, specific_earth_resistance=100.0, network=net)
for name in STATIONS:
    gi.create_bus(name=name, type=station, specific_earth_resistance=100.0, network=net)
names = ["UW"] + STATIONS
for i, (a, b) in enumerate(zip(names[:-1], names[1:]), start=1):
    gi.create_branch(name=f"K{i}", type=cable_system, from_bus=a, to_bus=b, length=1.2,
                     specific_earth_resistance=100.0, network=net)
gi.create_source(name="infeed", bus="UW", values={50.0: 1000.0}, r_to_x=R_TO_X, network=net)
gi.create_fault(name="F", bus=FAULT_BUS, t_k_s=T_K_S, network=net)
gi.run_fault(net, "F")
# --8<-- [end:network]

# --8<-- [start:check]
conductors = gi.check_conductor_limits(net, "F")  # t_k_s, kappa, n_factor from the model
nodes = gi.check_node_limits(net, "F")
# --8<-- [end:check]

# --8<-- [start:dc]
dc_net = net.model_copy(deep=True)
dc_net.frequencies = [0.0, 50.0]  # re-derives every element at 0 Hz as well
dc_net.sources["infeed"].values = {0.0: 150.0, 50.0: 1000.0}
gi.run_fault(dc_net, "F")
with gi.diagnostics.collecting() as dc_records:
    with_dc = gi.check_conductor_limits(dc_net, "F")
# --8<-- [end:dc]

if __name__ == "__main__":
    for row in conductors.iter_rows(named=True):
        print(f"{row['branch_name']}: I_th = {row['I_th_A']:.1f} A of I_adm = "
              f"{row['I_admissible_A']:.1f} A, utilization {row['utilization']:.3f}")
    print(f"kappa = {conductors['kappa'][0]:.4f}, m = {conductors['m'][0]:.5f}, "
          f"k = {conductors['k'][0]:.1f} A*s^0.5/mm2")
    for row in nodes.filter(pl.col("bus_name") == FAULT_BUS).iter_rows(named=True):
        print(f"{FAULT_BUS} {row['element']}: I = {row['I_conductor_A']:.1f} A "
              f"(split {row['current_split']:g}), I_adm = {row['I_admissible_A']:.1f} A, "
              f"within limit: {row['within_limit']}")
    unassessed = nodes.filter(pl.col("within_limit").is_null())["bus_name"].unique().to_list()
    print("not assessed (no thermal data):", unassessed)
    k1 = with_dc.filter(pl.col("branch_name") == "K1").row(0, named=True)
    print(f"with 150 A DC, K1: I_dc = {k1['I_dc_A']:.1f} A, I_ac = {k1['I_ac_rms_A']:.1f} A,"
          f" I_th = {k1['I_th_A']:.1f} A;", [r.code for r in dc_records])

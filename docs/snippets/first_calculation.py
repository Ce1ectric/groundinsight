# docs/snippets/first_calculation.py
"""
Your first fault calculation: the code of the tutorial page of the same name.

Two substations joined by a 5 km medium-voltage cable whose shield is earthed
at both ends; 1000 A (RMS, 50 Hz) flow from the infeed at ``bus_source`` to an
earth fault at ``bus_fault``. The script builds the network, solves it, prints
the earth potential rise, the earthing impedance and both reduction factors,
and then computes the same numbers by hand from the closed forms of a single
line.

The impedance formulas are illustrative round numbers, not the data of a real
cable or electrode.

Run it as a script: ``python docs/snippets/first_calculation.py``.
"""

# --8<-- [start:network]
import groundinsight as gi

net = gi.create_network(name="first_calculation", frequencies=[50.0])
# --8<-- [end:network]

# --8<-- [start:types]
station = gi.BusType(
    name="SubstationBus",
    system_type="Substation",
    voltage_level=20.0,
    impedance_formula="rho * 0.01 + j * f * 1/50 * 0.1",  # Z_B, Ohm
)
cable = gi.BranchType(
    name="MSCable",
    grounding_conductor=True,
    self_impedance_formula="(0.25 + j * f * 0.012) * l",   # Z_s: shield-earth loop
    mutual_impedance_formula="(0.0 + j * f * 0.012) * l",  # Z_m: phase-shield coupling
)
# --8<-- [end:types]

# --8<-- [start:elements]
gi.create_bus(name="bus_source", type=station, specific_earth_resistance=100.0,
              network=net)
gi.create_bus(name="bus_fault", type=station, specific_earth_resistance=100.0,
              network=net)
gi.create_branch(name="cable_1", type=cable, from_bus="bus_source",
                 to_bus="bus_fault", length=5.0, network=net)
# --8<-- [end:elements]

# --8<-- [start:source_fault]
gi.create_source(name="substation_infeed", bus="bus_source",
                 values={50.0: 1000.0}, network=net)          # A, RMS
gi.create_fault(name="fault_at_remote_bus", bus="bus_fault", network=net)
# --8<-- [end:source_fault]

# --8<-- [start:solve]
result = gi.run_fault(net, fault="fault_at_remote_bus")
print("diagnostics:", result.diagnostics)
# --8<-- [end:solve]

# --8<-- [start:results]
key = net.res_fault("fault_at_remote_bus").row(0, named=True)  # the 50 Hz row
print(f"U_E  = {key['U_E_V']:.2f} V        EPR at the fault bus")
print(f"Z_E  = {key['Z_E_Ohm']:.4f} Ohm at {key['Z_E_degree']:.2f} deg")
print(f"I_E  = {key['I_E_A']:.2f} A        earth current")
print(f"r    = {key['r_current']:.6f}       reduction factor, EN 50522")
print(f"EPR ratio = {key['r_epr']:.6f}  reduction factor of the cable")

for row in net.res_buses(fault="fault_at_remote_bus").iter_rows(named=True):
    if not row["is_rms"]:
        print(f"{row['bus_name']:<10} EPR {row['EPR_V']:.2f} V at {row['EPR_degree']:7.2f} deg,"
              f" electrode {row['I_bus_A']:.2f} A at {row['I_bus_degree']:7.2f} deg")
shield = net.res_branches(fault="fault_at_remote_bus").row(0, named=True)
print(f"cable_1    shield {shield['I_branch_A']:.2f} A at {shield['I_branch_degree']:.2f} deg")
# --8<-- [end:results]

# --8<-- [start:by_hand]
z_b = 1.0 + 0.1j                     # electrode, rho = 100 Ohm m, 50 Hz
z_s = (0.25 + 0.6j) * 5.0            # shield-earth loop of 5 km
z_m = 0.6j * 5.0                     # coupling over 5 km
current = 1000.0

ratio = abs(1 - z_m / z_s)                         # EPR ratio
i_earth = current * (z_s - z_m) / (z_s + 2 * z_b)  # current through the soil
u_e = z_b * i_earth                                # EPR at the fault bus
z_e = z_b * (z_s + z_b) / (z_b + z_s + z_b)        # Z_B parallel to (Z_s + Z_B)

print(f"by hand: EPR ratio = {ratio:.6f}, r = {abs(i_earth) / current:.6f}, "
      f"U_E = {abs(u_e):.2f} V, |Z_E| = {abs(z_e):.4f} Ohm")
# --8<-- [end:by_hand]

# --8<-- [start:plot_save]
figure = gi.plot_bus_voltages(result=result, title="EPR, first calculation")
gi.save_network_to_json(net, "first_calculation.json")
same_network = gi.load_network_from_json("first_calculation.json")
print(sorted(same_network.buses))
# --8<-- [end:plot_save]

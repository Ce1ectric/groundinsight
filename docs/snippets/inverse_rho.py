# docs/snippets/inverse_rho.py
"""
The example of the how-to "Admissible soil resistivity (inverse rho / rho-f)".

The 20 kV feeder of the EN 50522 how-to (substation ``UW``, stations ``S1`` to
``S5``, earth fault at ``S3``, 1000 A at 50 Hz and 50 A at 250 Hz). Two
questions: how far may the soil resistivity at the station electrodes rise
before the earth potential rise of the faulted station exceeds a limit, and how
far may a fitted rho-f characteristic of the electrodes be scaled before *any*
station, faulted itself, exceeds it. The limit is an illustrative number --
take yours from your EN 50522 assessment.

Run it as a script: ``python docs/snippets/inverse_rho.py``.
"""

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
                     impedance_formula="2*rho/(3*1.13*sqrt(100.0))")

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
# --8<-- [end:network]

# --8<-- [start:rho]
U_LIMIT_V = 500.0  # illustrative: the EPR limit your EN 50522 assessment sets

result = gi.find_max_rho_scaling(net, "F", bus_names=STATIONS, u_limit=U_LIMIT_V)
if not result["converged"]:
    raise RuntimeError(f"no maximum: {result['status']}, bracket {result['c_bracket']}")
# --8<-- [end:rho]

# --8<-- [start:rho_f]
K_REF = (0.059, 0.0, 2e-4, 0.0, 1e-6)  # fitted rho-f vector of the station electrodes

rho_f = gi.find_max_rho_f_scaling(net, bus_names=STATIONS, u_limit=U_LIMIT_V, k_ref=K_REF)
# --8<-- [end:rho_f]

if __name__ == "__main__":
    print(f"rho search: status = {result['status']}, converged = {result['converged']}")
    lo, hi = result["c_bracket"]
    print(f"c_max = {result['c_max']:.4f}, bracket = ({lo:.4f}, {hi:.4f}), "
          f"EPR at c_max = {result['u_epr_rms_at_c_max']:.1f} V")
    print(f"rho_max at {FAULT_BUS} = {result['rho_max'][FAULT_BUS]:.1f} Ohm*m")
    print(f"rho-f search: status = {rho_f['status']}, c_max = {rho_f['c_max']:.4f}, "
          f"k1_max = {rho_f['k_max'][0]:.5f} Ohm/(Ohm*m)")
    worst = max(rho_f["epr_rms_per_bus_at_c_max"], key=rho_f["epr_rms_per_bus_at_c_max"].get)
    print(f"largest EPR at c_max: {rho_f['max_epr_rms_at_c_max']:.1f} V, fault at {worst}")

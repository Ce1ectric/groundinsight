# docs/snippets/ring.py
"""
From a line to a ring: the code of the second tutorial.

The cable of the first tutorial, laid as a ring of four 2.5 km sections:
``bus_source - bus_a - bus_fault`` (route 1) and
``bus_source - bus_b - bus_fault`` (route 2), each route 5 km long, every
station with the same illustrative electrode. 1000 A (RMS, 50 Hz) flow from
the infeed at ``bus_source`` to an earth fault at ``bus_fault``, opposite the
infeed. The ring is solved without and with a phase impedance, in the legacy
path mode for comparison, and with a harmonic; the numbers are compared with
the closed forms of the symmetric ring.

Run it as a script: ``python docs/snippets/ring.py``.
"""

import cmath
import warnings

# --8<-- [start:build]
import groundinsight as gi


def build_ring(frequencies, source_values, phase_impedance_formula=None):
    """The ring of the tutorial, ready to solve."""
    net = gi.create_network(name="ring", frequencies=frequencies)
    station = gi.BusType(
        name="SubstationBus", system_type="Substation", voltage_level=20.0,
        impedance_formula="rho * 0.01 + j * f * 1/50 * 0.1",
    )
    cable = gi.BranchType(
        name="MSCable", grounding_conductor=True,
        self_impedance_formula="(0.25 + j * f * 0.012) * l",
        mutual_impedance_formula="(0.0 + j * f * 0.012) * l",
        phase_impedance_formula=phase_impedance_formula,
    )
    for bus in ("bus_source", "bus_a", "bus_fault", "bus_b"):
        gi.create_bus(name=bus, type=station, specific_earth_resistance=100.0,
                      network=net)
    for name, a, b in (("cable_1", "bus_source", "bus_a"),     # route 1
                       ("cable_2", "bus_a", "bus_fault"),
                       ("cable_3", "bus_source", "bus_b"),     # route 2
                       ("cable_4", "bus_b", "bus_fault")):
        gi.create_branch(name=name, type=cable, from_bus=a, to_bus=b,
                         length=2.5, network=net)
    gi.create_source(name="substation_infeed", bus="bus_source",
                     values=source_values, network=net)
    gi.create_fault(name="fault_at_remote_bus", bus="bus_fault", network=net)
    return net
# --8<-- [end:build]


# --8<-- [start:first_solve]
net = build_ring([50.0], {50.0: 1000.0})
with warnings.catch_warnings(record=True) as caught:
    warnings.simplefilter("always")
    result = gi.run_fault(net, fault="fault_at_remote_bus")
for warning in caught:
    print(warning.category.__name__, warning.message.code)
print([d.code for d in result.diagnostics])
# --8<-- [end:first_solve]

# --8<-- [start:phase_impedance]
Z_RED = "((0.15 + j*f*0.015) - (j*f*0.012)**2 / (0.25 + j*f*0.012)) * l"

net = build_ring([50.0], {50.0: 1000.0}, phase_impedance_formula=Z_RED)
result = gi.run_fault(net, fault="fault_at_remote_bus")
print([d.code for d in result.diagnostics])

key = net.res_fault("fault_at_remote_bus").row(0, named=True)  # the 50 Hz row
print(f"U_E  = {key['U_E_V']:.2f} V")
print(f"Z_E  = {key['Z_E_Ohm']:.4f} Ohm at {key['Z_E_degree']:.2f} deg")
print(f"I_E  = {key['I_E_A']:.2f} A")
print(f"r    = {key['r_current']:.6f}   EPR ratio = {key['r_epr']:.6f}")
# --8<-- [end:phase_impedance]

# --8<-- [start:electrodes]
buses = net.res_buses(fault="fault_at_remote_bus")
for row in buses.filter(buses["frequency_Hz"] == 50.0).iter_rows(named=True):
    print(f"{row['bus_name']:<11} EPR {row['EPR_V']:8.2f} V   electrode {row['I_bus_A']:8.2f} A")
# --8<-- [end:electrodes]

# --8<-- [start:by_hand]
z_b = 1.0 + 0.1j                 # electrode, rho = 100 Ohm m, 50 Hz
z_s = (0.25 + 0.6j) * 2.5        # shield-earth loop of one 2.5 km section
z_m = 0.6j * 2.5                 # coupling of one section
current = 1000.0


def parallel(a, b):
    """The parallel connection of two impedances."""
    return a * b / (a + b)


# By symmetry each route carries current / 2, and bus_a and bus_b stay at
# zero potential: the earthing network is bus_source and bus_fault joined
# by the two half-routes in parallel (z_s / 2 each), with a Norton current
# (1 - k) * current between them.
k = z_m / z_s
u_e = abs(1 - k) * current * abs(parallel(z_b, z_s / 2))
r = abs(z_s / 2 - z_m / 2) / abs(z_b + z_s / 2)
z_e = parallel(z_b, z_s / 2 + parallel(z_b / 2, z_s / 2 + z_b))
print(f"by hand: U_E = {u_e:.2f} V, r = {r:.6f}, EPR ratio = {abs(1 - k):.6f}, "
      f"Z_E = {abs(z_e):.4f} Ohm at {cmath.phase(z_e) * 180 / cmath.pi:.2f} deg")
# --8<-- [end:by_hand]

# --8<-- [start:legacy]
legacy = build_ring([50.0], {50.0: 1000.0}, phase_impedance_formula=Z_RED)
with warnings.catch_warnings():
    warnings.simplefilter("ignore", gi.ModelAssumptionWarning)
    gi.run_fault(legacy, fault="fault_at_remote_bus", phase_current_mode="paths")
old = legacy.res_fault("fault_at_remote_bus").row(0, named=True)
print(f"paths mode: U_E = {old['U_E_V']:.2f} V, EPR ratio = {old['r_epr']:.6f}")
# --8<-- [end:legacy]

# --8<-- [start:harmonics]
net = build_ring([50.0, 250.0], {50.0: 1000.0, 250.0: 200.0},
                 phase_impedance_formula=Z_RED)
gi.run_fault(net, fault="fault_at_remote_bus")
for row in net.res_fault("fault_at_remote_bus").iter_rows(named=True):
    label = "RMS" if row["is_rms"] else f"{row['frequency_Hz']:.0f} Hz"
    ratios = ("" if row["is_rms"] else
              f"   r = {row['r_current']:.6f}   EPR ratio = {row['r_epr']:.6f}")
    print(f"{label:>6}: I_F = {row['I_F_A']:7.2f} A   U_E = {row['U_E_V']:7.2f} V{ratios}")
# --8<-- [end:harmonics]

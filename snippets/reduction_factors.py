# docs/snippets/reduction_factors.py
"""
The EN 50522 chain ``U_E = r * 3I_0 * Z_E`` on a long bonded cable run.

The example of the explanation page "Reduction factors and earthing
impedance": a homogeneous chain of 64 cable sections of 0.5 km, every station
earthed with 2 Ohm, the infeed (0.2 Ohm) at one end and the earth fault at the
other -- the case the standard's lumped picture describes: a fault far from the
infeed on a long bonded run. The per-km values are illustrative (shield-earth
loop ``0.29 + j0.625`` Ohm/km, phase-shield coupling ``0.049 + j0.625``
Ohm/km at 50 Hz).

The script prints the package's ``U_E``, ``Z_E`` and both reduction factors
next to the closed forms of the ladder network: the Norton injections of a
homogeneous run telescope to ``(1 - k) * I`` at its two ends, so
``|U_E| = |1 - k| * I * |Z_inf|`` with the ladder input impedance
``Z_inf = -Z'/2 + sqrt(Z'^2/4 + Z_e * Z')``.

Run it as a script: ``python docs/snippets/reduction_factors.py``.
"""

import cmath

import groundinsight as gi

# --8<-- [start:chain]
N_SECTIONS = 64
SECTION_KM = 0.5
Z_STATION = 2.0          # Ohm, electrode of every station
I_FAULT = 1000.0         # A RMS, 3I_0 at 50 Hz

cable = gi.BranchType(
    name="cable", grounding_conductor=True,
    self_impedance_formula="(0.29 + 0.625*j*f/50)*l",     # Z_s' (illustrative)
    mutual_impedance_formula="(0.049 + 0.625*j*f/50)*l",  # Z_m' (illustrative)
)
station = gi.BusType(name="station", system_type="Station", voltage_level=20.0,
                     impedance_formula=repr(Z_STATION))
infeed = gi.BusType(name="infeed", system_type="Substation", voltage_level=110.0,
                    impedance_formula="0.2")

net = gi.create_network(name="long_run", frequencies=[50.0])
gi.create_bus(name="N0", type=infeed, network=net)
for i in range(1, N_SECTIONS + 1):
    gi.create_bus(name=f"N{i}", type=station, network=net)
for i in range(N_SECTIONS):
    gi.create_branch(name=f"L{i + 1}", type=cable, from_bus=f"N{i}",
                     to_bus=f"N{i + 1}", length=SECTION_KM, network=net)
gi.create_source(name="infeed", bus="N0", values={50.0: I_FAULT}, network=net)
gi.create_fault(name="F", bus=f"N{N_SECTIONS}", network=net)

rf = gi.run_fault(net, "F").reduction_factor
u_e = complex(rf.u_earthing[50.0])        # U_E: EPR of the fault bus
z_e = complex(rf.z_earthing[50.0])        # Z_E: driving-point impedance there
r_epr = rf.value[50.0]                    # EPR ratio, |1 - Z_m/Z_s| here
r_current = rf.value_current[50.0]        # |I_E| / |3I_0|
# --8<-- [end:chain]

# --8<-- [start:closed_form]
z_series = (0.29 + 0.625j) * SECTION_KM   # Z' of one section
z_mutual = (0.049 + 0.625j) * SECTION_KM
k = z_mutual / z_series
z_inf = -z_series / 2 + cmath.sqrt(z_series ** 2 / 4 + Z_STATION * z_series)
u_closed = abs(1 - k) * I_FAULT * abs(z_inf)
# --8<-- [end:closed_form]

if __name__ == "__main__":
    print(f"|1 - k|            = {abs(1 - k):.6f}    value         = {r_epr:.6f}")
    print(f"value_current      = {r_current:.6f}")
    print(f"Z_E                = {z_e.real:.4f}{z_e.imag:+.4f}j Ohm  "
          f"Z_inf = {z_inf.real:.4f}{z_inf.imag:+.4f}j Ohm")
    print(f"|U_E|              = {abs(u_e):.3f} V   |1-k|*I*|Z_inf| = {u_closed:.3f} V")
    print(f"value * I * |Z_E|  = {r_epr * I_FAULT * abs(z_e):.3f} V")
    print(f"value_current*I*|Z_E| = {r_current * I_FAULT * abs(z_e):.3f} V")
    print(f"group mean / group quotient = {abs(complex(rf.u_earthing_group[50.0])):.2f} V"
          f" / {abs(complex(rf.z_earthing_group[50.0])):.4f} Ohm")

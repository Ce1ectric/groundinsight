# docs/snippets/reduction_factors.py
"""
The reduction factor ``r = U_E / (Z_E * I_F)`` on a long run and on one cable.

The examples of the explanation page "Reduction factors and earthing
impedance".

The long run: a homogeneous chain of 64 cable sections of 0.5 km, every
station earthed with 2 Ohm, the infeed (0.2 Ohm) at one end and the earth
fault at the other -- the case the standard's lumped picture describes: a
fault far from the infeed on a long bonded run. The per-km values are
illustrative (shield-earth loop ``0.29 + j0.625`` Ohm/km, phase-shield
coupling ``0.049 + j0.625`` Ohm/km at 50 Hz). The script prints the
package's ``U_E``, ``Z_E`` and ``r`` next to the closed forms of the ladder
network: the Norton injections of a homogeneous run telescope to
``(1 - k) * I`` at its two ends, so ``|U_E| = |1 - k| * I * |Z_inf|`` with the
ladder input impedance ``Z_inf = -Z'/2 + sqrt(Z'^2/4 + Z_e * Z')``, and
``r = |1 - k|``. As a second way, the fault station is earthed ideally
(1e-9 Ohm) and the current into that electrode is read off.

One cable: a source station earthed with ``Z_A`` feeds a fault 1 km away;
``r = (Z_s - Z_m)/(Z_s + Z_A)`` over five decades of ``Z_A``.

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
infeed = gi.BusType(name="infeed", system_type="Substation", voltage_level=110.0,
                    impedance_formula="0.2")


def long_run(z_fault_station: float) -> gi.Network:
    """The chain; the electrode of the fault station is the argument."""
    station = gi.BusType(name="station", system_type="Station", voltage_level=20.0,
                         impedance_formula=repr(Z_STATION))
    fault_station = gi.BusType(name="fault station", system_type="Station",
                               voltage_level=20.0, impedance_formula=repr(z_fault_station))
    net = gi.create_network(name="long_run", frequencies=[50.0])
    gi.create_bus(name="N0", type=infeed, network=net)
    for i in range(1, N_SECTIONS + 1):
        kind = fault_station if i == N_SECTIONS else station
        gi.create_bus(name=f"N{i}", type=kind, network=net)
    for i in range(N_SECTIONS):
        gi.create_branch(name=f"L{i + 1}", type=cable, from_bus=f"N{i}",
                         to_bus=f"N{i + 1}", length=SECTION_KM, network=net)
    gi.create_source(name="infeed", bus="N0", values={50.0: I_FAULT}, network=net)
    gi.create_fault(name="F", bus=f"N{N_SECTIONS}", network=net)
    return net


net = long_run(Z_STATION)
rf = gi.run_fault(net, "F").reduction_factor
u_e = complex(rf.u_earthing[50.0])        # U_E: EPR of the fault bus
z_e = complex(rf.z_earthing[50.0])        # Z_E: driving-point impedance there
i_e = complex(rf.i_earth[50.0])           # I_E = U_E / Z_E
r = rf.value[50.0]                        # r = |I_E| / |I_F|
# --8<-- [end:chain]

# --8<-- [start:short_circuit]
shorted = long_run(1e-9)                  # the fault station earthed ideally
gi.run_fault(shorted, "F")
epr = shorted.res_buses("F").filter(shorted.res_buses("F")["bus_name"] == f"N{N_SECTIONS}")
i_short = epr["EPR_V"][0] / 1e-9          # the current into the ideal electrode
r_short = i_short / I_FAULT
# --8<-- [end:short_circuit]

# --8<-- [start:closed_form]
z_series = (0.29 + 0.625j) * SECTION_KM   # Z' of one section
z_mutual = (0.049 + 0.625j) * SECTION_KM
k = z_mutual / z_series
z_inf = -z_series / 2 + cmath.sqrt(z_series ** 2 / 4 + Z_STATION * z_series)
u_closed = abs(1 - k) * I_FAULT * abs(z_inf)
# --8<-- [end:closed_form]

# --8<-- [start:one_cable]
Z_S, Z_M = 0.2 + 0.4j, 0.1 + 0.2j         # Ohm, one cable of 1 km
line_type = gi.BranchType(name="line", grounding_conductor=True,
                          self_impedance_formula=f"({Z_S.real}+{Z_S.imag}*j)*l",
                          mutual_impedance_formula=f"({Z_M.real}+{Z_M.imag}*j)*l")
one_cable = []
for z_a in (10.0, 1.0, 0.1, 0.01, 1e-5):
    line = gi.create_network(name="line", frequencies=[50.0])
    for name, z in (("A", z_a), ("B", 1.0)):
        gi.create_bus(name=name, type=gi.BusType(name=name, system_type="Station",
                      voltage_level=20.0, impedance_formula=repr(z)), network=line)
    gi.create_branch(name="K", type=line_type, from_bus="A", to_bus="B", length=1.0,
                     network=line)
    gi.create_source(name="Q", bus="A", values={50.0: I_FAULT}, network=line)
    gi.create_fault(name="F", bus="B", network=line)
    model = gi.run_fault(line, "F").reduction_factor.value[50.0]
    one_cable.append((z_a, model, abs((Z_S - Z_M) / (Z_S + z_a))))
# --8<-- [end:one_cable]

if __name__ == "__main__":
    print(f"r                  = {r:.6f}    |1 - k| = {abs(1 - k):.6f}")
    print(f"r (ideal electrode) = {r_short:.6f}")
    print(f"Z_E                = {z_e.real:.4f}{z_e.imag:+.4f}j Ohm  "
          f"Z_inf = {z_inf.real:.4f}{z_inf.imag:+.4f}j Ohm")
    print(f"|U_E|              = {abs(u_e):.3f} V   |1-k|*I*|Z_inf| = {u_closed:.3f} V")
    print(f"|I_E|              = {abs(i_e):.2f} A")
    for z_a, model, closed in one_cable:
        print(f"Z_A = {z_a:g} Ohm: r = {model:.6f}, closed form {closed:.6f}")

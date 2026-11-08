# docs/snippets/en50522_check.py
"""
The EN 50522 quantities of one solved fault: the worked example of the how-to
"Compare results with EN 50522 limits".

A 20 kV cable feeder -- substation ``UW`` and five stations bonded through the
screens of a three-phase NA2XS(F)2Y 1x300 RM/25 system in trefoil (the type of
the how-to "Bus and branch types from data sheets") -- with an earth fault at
station ``S3``. The infeed carries 1000 A at 50 Hz and 50 A at 250 Hz (RMS;
illustrative values). The script prints, per frequency, the fault current
``3I_0``, the reduction factor ``r``, the earth current ``I_E``, the earthing
impedance ``Z_E`` and the earth potential rise ``U_E`` at the fault bus, the
chain ``r * 3I_0 * Z_E`` for comparison, and the total RMS of ``U_E``.

The permissible touch voltage is not part of the package: fill in
``U_TP_V`` from EN 50522:2022 for the fault duration of your protection.

Run it as a script: ``python docs/snippets/en50522_check.py``.
"""

import math

import groundinsight as gi

# --8<-- [start:network]
cable_system = gi.BranchType(
    name="3x NA2XS(F)2Y 1x300RM/25 12/20 kV, trefoil, screens bonded both ends",
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
substation = gi.BusType(
    name="substation", system_type="Substation", voltage_level=20.0,
    impedance_formula="0.5",
)
station = gi.BusType(  # ring earth electrode around 10 m x 10 m
    name="station", system_type="Station", voltage_level=20.0,
    impedance_formula="2*rho/(3*1.13*sqrt(100.0))",
)

FREQUENCIES = [50.0, 250.0]
SOURCE_VALUES = {50.0: 1000.0, 250.0: 50.0}  # A RMS, 3I_0 per frequency
FAULT_BUS = "S3"

net = gi.create_network(name="feeder", frequencies=FREQUENCIES)
names = ["UW", "S1", "S2", "S3", "S4", "S5"]
gi.create_bus(name="UW", type=substation, specific_earth_resistance=100.0, network=net)
for name in names[1:]:
    gi.create_bus(name=name, type=station, specific_earth_resistance=100.0, network=net)
for i, (a, b) in enumerate(zip(names[:-1], names[1:]), start=1):
    gi.create_branch(
        name=f"K{i}", type=cable_system, from_bus=a, to_bus=b, length=1.2,
        specific_earth_resistance=100.0, network=net,
    )
gi.create_source(name="infeed", bus="UW", values=SOURCE_VALUES, network=net)
gi.create_fault(name="F", bus=FAULT_BUS, scalings={50.0: 1.0, 250.0: 1.0}, network=net)
# --8<-- [end:network]

# --8<-- [start:quantities]
result = gi.run_fault(net, "F")  # the fault by name (positional)
rf = result.reduction_factor

ROWS = []
for f in FREQUENCIES:
    i_f = abs(complex(rf.i_fault[f].real, rf.i_fault[f].imag))  # 3I_0
    r = rf.value_current[f]  # EN 50522 reduction factor
    i_e = abs(complex(rf.i_earth[f].real, rf.i_earth[f].imag))  # I_E = r * 3I_0
    z_e = abs(complex(rf.z_earthing[f].real, rf.z_earthing[f].imag))  # Z_E
    u_e = abs(complex(rf.u_earthing[f].real, rf.u_earthing[f].imag))  # U_E
    ROWS.append({"f": f, "I_F": i_f, "r": r, "I_E": i_e, "Z_E": z_e, "U_E": u_e,
                 "chain": r * i_f * z_e})

U_E_RMS = math.sqrt(sum(row["U_E"] ** 2 for row in ROWS))  # total RMS over frequencies
# --8<-- [end:quantities]

# --8<-- [start:criterion]
U_TP_V = None  # U_Tp(t_F) from EN 50522:2022, 5.4.1 / Annex B, for your fault duration

if U_TP_V is None:
    verdict = "U_Tp not set: read it from EN 50522:2022 for the fault duration t_F"
else:
    verdict = "U_E <= U_Tp" if U_E_RMS <= U_TP_V else "U_E > U_Tp: follow the design procedure"
# --8<-- [end:criterion]

if __name__ == "__main__":
    print(f"{'f / Hz':>7} {'3I_0 / A':>9} {'r':>7} {'I_E / A':>8} {'|Z_E| / Ohm':>11}"
          f" {'|U_E| / V':>10} {'r*3I_0*|Z_E| / V':>17}")
    for row in ROWS:
        print(f"{row['f']:7.0f} {row['I_F']:9.1f} {row['r']:7.4f} {row['I_E']:8.1f}"
              f" {row['Z_E']:11.3f} {row['U_E']:10.2f} {row['chain']:17.2f}")
    print(f"U_E, total RMS: {U_E_RMS:.1f} V -- {verdict}")

# docs/snippets/dc_chain.py
"""
A direct-current study: the reference chain of the explanation page
"Direct current (f = 0)".

Three earthing systems in a chain, ``A - B - C`` (0.8, 12 and 3.5 Ohm), joined
by two 10 km spans of an earthing conductor of 0.25 Ohm/km. A direct current of
1 kA enters the earthing system at ``A`` and leaves it at ``C`` -- stray
current of a DC traction return, the electrode line of an HVDC link, a
cathodic-protection installation. ``f = 0`` is an ordinary network frequency;
nothing else is needed.

The conductor is written twice: with its reactance as ``j*2*pi*f*L`` (Carson's
earth-return term), which vanishes at DC by itself, and with a constant
``j*0.6`` Ohm/km, which has no meaning at DC. The script solves both at 0 Hz
and at the old work-around frequency 0.1 Hz.

Run it as a script: ``python docs/snippets/dc_chain.py``.
"""

import warnings

import groundinsight as gi

CARSON = "(0.25 + j*2*pi*f*2e-7*log(658*sqrt(rho/f)/0.01))*l"
CONSTANT_X = "(0.25 + j*0.6)*l"


# --8<-- [start:build]
def build(conductor_formula, frequency):
    """The chain A - B - C with 1 kA DC entering at A and leaving at C."""
    net = gi.create_network(name="dc_return", frequencies=[frequency])
    for name, z_electrode in (("A", 0.8), ("B", 12.0), ("C", 3.5)):
        electrode = gi.BusType(name=f"electrode_{name}", system_type="Substation",
                               voltage_level=110.0, impedance_formula=repr(z_electrode))
        gi.create_bus(name=name, type=electrode, specific_earth_resistance=100.0,
                      network=net)
    conductor = gi.BranchType(name="conductor", grounding_conductor=True,
                              self_impedance_formula=conductor_formula,
                              mutual_impedance_formula="0")
    gi.create_branch(name="A-B", type=conductor, from_bus="A", to_bus="B",
                     length=10.0, network=net)
    gi.create_branch(name="B-C", type=conductor, from_bus="B", to_bus="C",
                     length=10.0, network=net)
    gi.create_source(name="dc", bus="A", values={frequency: 1000.0}, network=net)
    gi.create_fault(name="F", bus="C", network=net)
    return net


net = build(CARSON, 0.0)
result = gi.run_fault(net, "F")
epr_a = abs(complex(next(b.uepr_freq[0.0] for b in result.buses if b.name == "A")))
z_e = abs(complex(result.reduction_factor.z_earthing[0.0]))
# --8<-- [end:build]

# --8<-- [start:workaround]
ROWS = []
for spelling, formula in (("j*2*pi*f*L", CARSON), ("constant j*X", CONSTANT_X)):
    for frequency in (0.1, 0.0):
        with warnings.catch_warnings():
            # the constant reactance at 0 Hz is announced by a DCLimitWarning
            warnings.simplefilter("ignore", gi.DCLimitWarning)
            res = gi.run_fault(build(formula, frequency), "F")
        bus_a = next(b for b in res.buses if b.name == "A")
        ROWS.append({
            "spelling": spelling,
            "f_Hz": frequency,
            "EPR_A_V": abs(complex(bus_a.uepr_freq[frequency])),
            "Z_E_Ohm": abs(complex(res.reduction_factor.z_earthing[frequency])),
        })
# --8<-- [end:workaround]

if __name__ == "__main__":
    print(f"0 Hz, Carson spelling: |EPR(A)| = {epr_a:.9f} V, |Z_E| = {z_e:.6f} Ohm")
    for row in ROWS:
        print(f"{row['spelling']:>13}  {row['f_Hz']:4.1f} Hz  |EPR(A)| = {row['EPR_A_V']:12.6f} V"
              f"  |Z_E| = {row['Z_E_Ohm']:.6f} Ohm")

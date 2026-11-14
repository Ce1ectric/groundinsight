# docs/snippets/transient.py
"""
Transient studies: the example of the how-to "Transient studies".

A substation ``UW`` (0.5 Ohm) feeds an earth fault at station ``S`` (2 Ohm)
over 3 km of cable whose screen is coupled to the phase conductor. The types
carry both descriptions the package needs: impedance formulas for the
stationary solve and the FFT solver, lumped R/L values for the state-space
solver. A 50 Hz fault current of 1000 A RMS flows from 20 ms to 120 ms. The
script solves it with both transient solvers and compares their steady state
with the stationary result.

Run it as a script: ``python docs/snippets/transient.py``.
"""

import math

import numpy as np

import groundinsight as gi
from groundinsight.diagnostics import collecting

# --8<-- [start:network]
L_SELF_H_PER_KM = 1.2e-3     # screen-earth loop inductance (illustrative)
L_MUTUAL_H_PER_KM = 1.0e-3   # screen-phase mutual inductance
LENGTH_KM = 3.0

substation = gi.BusType(name="substation", system_type="Substation", voltage_level=20.0,
                        impedance_formula="0.5", R_formula="0.5")
station = gi.BusType(name="station", system_type="Station", voltage_level=20.0,
                     impedance_formula="2.0", R_formula="2.0")
cable = gi.BranchType(
    name="cable", grounding_conductor=True,
    self_impedance_formula=f"(0.2 + j*2*pi*f*{L_SELF_H_PER_KM})*l",   # stationary, FFT
    mutual_impedance_formula=f"(j*2*pi*f*{L_MUTUAL_H_PER_KM})*l",
    R_self_formula="0.2*l", L_self_formula=f"{L_SELF_H_PER_KM}*l",    # state space
    R_mutual_formula="0", M_mutual_formula=f"{L_MUTUAL_H_PER_KM}*l",
)
net = gi.create_network(name="transient", frequencies=[50.0])
gi.create_bus(name="UW", type=substation, network=net)
gi.create_bus(name="S", type=station, network=net)
gi.create_branch(name="K", type=cable, from_bus="UW", to_bus="S", length=LENGTH_KM, network=net)
gi.create_source(name="infeed", bus="UW", values={50.0: 1000.0}, network=net)  # RMS
gi.create_fault(name="F", bus="S", network=net)
stationary = gi.run_fault(net, "F")
stationary_epr = next(b.uepr for b in stationary.buses if b.name == "S")
# --8<-- [end:network]

# --8<-- [start:waveform]
fault_current = gi.waveforms.sinusoidal_with_dc_offset(
    amplitude=math.sqrt(2) * 1000.0,  # peak of 1000 A RMS
    frequency_hz=50.0, t_on=0.02, t_off=0.12, dc_amplitude=0.0,
)
# --8<-- [end:waveform]

# --8<-- [start:solve]
results, diagnostics = {}, {}
for solver in ("fft", "state_space"):
    study = gi.TransientStudy(net, "F")
    study.set_source_waveform("infeed", fault_current)
    study.set_observation(buses=["S"], branches=["K"])
    with collecting() as records:
        results[solver] = study.solve(t_end=0.3, dt=1e-4, solver=solver)
    diagnostics[solver] = records
# --8<-- [end:solve]

# --8<-- [start:steady_state]
def steady_rms(result, bus, start=0.08, stop=0.10):
    """RMS of an EPR trace over one full period of the steady state."""
    t = np.asarray(result.time_s)
    u = np.asarray(result.epr_t[bus])
    window = (t >= start - 1e-9) & (t < stop - 1e-9)
    return float(np.sqrt(np.mean(u[window] ** 2)))

rms_fft = steady_rms(results["fft"], "S")
rms_state_space = steady_rms(results["state_space"], "S")
# --8<-- [end:steady_state]

if __name__ == "__main__":
    print(f"stationary EPR at S: {stationary_epr:.1f} V (RMS)")
    for solver, rms in (("fft", rms_fft), ("state_space", rms_state_space)):
        peak = float(np.max(np.abs(results[solver].epr_t["S"])))
        codes = sorted({r.code for r in diagnostics[solver]})
        print(f"{solver:>11}: steady-state RMS {rms:.1f} V, peak {peak:.1f} V, codes {codes}")

# Conventions and units

The rules on this page change numbers when they are missed: a length in metres
instead of kilometres, a peak value where an RMS value belongs, a phase angle
read in the wrong frame, a missing fault scaling. Each rule is stated with an
example that the test suite recomputes.

## Units

| Quantity | Unit | Where |
|---|---|---|
| branch length `l`, `Branch.length` | km | formulas, `create_branch(length=...)` |
| specific earth resistance `rho` | Ω·m | `specific_earth_resistance` of buses and branches, formulas |
| frequency `f` | Hz, as `float` keys | `Network.frequencies`, `Source.values`, `Fault.scalings`, result dictionaries; `0.0` is DC |
| currents | A, RMS | `Source.values`, result columns ending in `_A` |
| voltages, earth potential rise | V, RMS | `uepr`, `EPR_V`, `u_earthing` |
| impedances | Ω per element (per-km value × `l` for a branch) | formulas, `grounding_impedance_Ohm`, `z_earthing` |
| phase angles | degrees, relative to the phasor reference of the sources | result columns ending in `_degree` |
| time | s | `ResultTransient.time_s`, `Fault.t_k_s` |
| cross-sections, temperatures | mm², °C | thermal fields of the types |

## Phasors are RMS, waveforms are instantaneous

Every phasor in the stationary model is an **RMS** phasor: `Source.values`, the
Thévenin `voltage`, and every phasor in the results. The RMS row of a result
frame (`is_rms` true, `frequency_Hz` null) combines the frequencies as
$X_\text{RMS} = \sqrt{\sum_f |X(f)|^2}$, with the 0 Hz term entering with its
full magnitude.

The transient solvers work with **instantaneous** values. The waveform
factories in `gi.waveforms` take peak amplitudes: `amplitude` of
`sinusoidal_with_dc_offset` and of `damped_oscillation` is the peak of the
alternating part, `dc_amplitude` the initial value of the decaying offset, and
`step` holds its `amplitude` as an instantaneous level. `ResultTransient.epr_t`
and `i_branch_t` are instantaneous volts and amperes. To reproduce a stationary
run with `values={50.0: I}`, drive the transient with
`amplitude=math.sqrt(2) * I`:

```python
import math
import groundinsight as gi

station = gi.BusType(name="station", system_type="Station", voltage_level=20.0,
                     impedance_formula="2.0")
line = gi.BranchType(name="line", grounding_conductor=True,
                     self_impedance_formula="(0.2 + j*2*pi*f*1.2e-3)*l",
                     mutual_impedance_formula="0")
net = gi.create_network(name="rms", frequencies=[50.0])
gi.create_bus(name="S", type=station, network=net)
gi.create_bus(name="F", type=station, network=net)
gi.create_branch(name="K", type=line, from_bus="S", to_bus="F", length=2.0, network=net)
gi.create_source(name="Q", bus="S", values={50.0: 1000.0}, network=net)  # 1000 A RMS
gi.create_fault(name="F1", bus="F", scalings={50.0: 1.0}, network=net)
stationary = gi.run_fault(net, "F1")

study = gi.TransientStudy(net, "F1")
study.set_source_waveform("Q", gi.waveforms.sinusoidal_with_dc_offset(
    amplitude=math.sqrt(2) * 1000.0, frequency_hz=50.0))  # peak of 1000 A RMS
study.set_observation(buses=["F"])
transient = study.solve(t_end=0.2, dt=1e-5, solver="fft")
```

The stationary EPR at `F` is 382.39 V (RMS). The RMS of the transient EPR over
its last period is the same 382.39 V, and its peak is 540.8 V = √2 × 382.39 V.
(The FFT solver reminds you with a `TransientModelWarning` that the window ends
while the source is still on; for a steady-state comparison that is intended.)
If a data source gives peak amplitudes, divide the alternating components by
√2 before filling `values`; a DC component stays as it is.

## Signs and phase angles

Magnitudes, RMS values, the reduction factors and the impedances do not depend
on a sign convention. Phase angles and the signs of complex results do, and
`groundinsight` uses two frames:

- **Phase and shield currents** are reported for the physical fault: the phase
  current flows from the source to the fault, and `ResultBranch.i_s_freq`
  (`I_branch_A`, `I_branch_degree`) is the shield current counted positive from
  `from_bus` to `to_bus`.
- **Earth-side quantities** — the potentials `uepr_freq` (`EPR_V`,
  `EPR_degree`, `u_earthing`), the electrode currents `ia_freq` (`I_bus_A`,
  `I_bus_degree`) and the injections `i_inj_freq` (`I_inj_A`, `I_inj_degree`)
  — are reported for a current that enters the earthing network at the source
  bus and leaves it at the fault bus. That is the reverse of the physical
  direction, so each of these phasors is the **negative** of its physical
  value: add 180° to read the physical phase angle.

Two buses show it. `S` (electrode 0.5 Ω) feeds 1000 A at 50 Hz to a fault at
`F` (electrode 2 Ω) over a 5 km line with
`Z_s = (0.2 + 0.4j) Ω/km` and `Z_m = (0.05 + 0.35j) Ω/km`:

```python
import groundinsight as gi

s_type = gi.BusType(name="S", system_type="Substation", voltage_level=20.0,
                    impedance_formula="0.5")
f_type = gi.BusType(name="F", system_type="Station", voltage_level=20.0,
                    impedance_formula="2.0")
line = gi.BranchType(name="line", grounding_conductor=True,
                     self_impedance_formula="(0.2 + 0.4*j)*l",
                     mutual_impedance_formula="(0.05 + 0.35*j)*l")
net = gi.create_network(name="signs", frequencies=[50.0])
gi.create_bus(name="S", type=s_type, network=net)
gi.create_bus(name="F", type=f_type, network=net)
gi.create_branch(name="L", type=line, from_bus="S", to_bus="F", length=5.0, network=net)
gi.create_source(name="Q", bus="S", values={50.0: 1000.0}, network=net)
gi.create_fault(name="F1", bus="F", scalings={50.0: 1.0}, network=net)
result = gi.run_fault(net, "F1")
```

By hand, with the phase current I = 1000 A from S to F: the shield returns
I<sub>s</sub> = I·(Z<sub>S</sub> + Z<sub>F</sub> + Z<sub>m</sub>)/(Z<sub>S</sub> + Z<sub>F</sub> + Z<sub>s</sub>)
= 807.7 + 38.5j A from F to S, the soil carries
I<sub>E</sub> = I − I<sub>s</sub> = 192.3 − 38.5j A, and the electrodes rise to
u<sub>F</sub> = Z<sub>F</sub>·I<sub>E</sub> and u<sub>S</sub> = −Z<sub>S</sub>·I<sub>E</sub>.

| Quantity | Reported | Physical (by hand) | Relation |
|---|---|---|---|
| EPR at the fault bus `F` | −384.6 + 76.9j V (168.7°) | +384.6 − 76.9j V (−11.3°) | negated |
| EPR at the source bus `S` | 96.2 − 19.2j V | −96.2 + 19.2j V | negated |
| electrode current at `F` | −192.3 + 38.5j A | +192.3 − 38.5j A | negated |
| injection at `S` / `F` | +1000 A / −1000 A | the fault current enters the earthing system at `F` | negated |
| shield current of `L` (S → F) | −807.7 − 38.5j A | 807.7 + 38.5j A flowing from F to S | same, counted `from_bus` → `to_bus` |
| phase current of `L` | +1000 A | 1000 A from S to F | same |

The EPR at the fault bus is therefore reported at about 180° from the source
phasor when the earthing is mostly resistive. [Concepts](../concepts.md#branch-currents)
describes the shield current in the earth-side frame — positive from `to_bus`
to `from_bus` — which is the same physical current. The transient time series
follow the earth-side frame of the stationary result: with a source waveform
√2·I·sin(ωt) and mostly resistive earthing, the EPR at `F` is negative when the
source current peaks.

## Frequencies, source values and fault scalings

- **Network frequencies** are floats; integer keys are converted. `0.0` is DC.
- **Source values** are given per frequency. An active source that reaches the
  fault but has no value at any network frequency makes `run_fault` raise a
  `ValueError` that names the source, its keys and the network frequencies —
  it would contribute 0 A everywhere. A source that misses some network
  frequencies contributes 0 A at those and is announced once per source and run
  (`InputWarning`, code `SOURCE_FREQUENCY_GAP`). Keys outside the network
  frequencies are announced when the source is created
  (`SOURCE_FREQUENCY_OUTSIDE_NETWORK`) and ignored by the solve.
- **Fault scalings** multiply every source current that feeds the fault, per
  frequency. `scalings` is optional: left out, `None` or `{}` means 1.0 at every
  frequency. A frequency missing from a non-empty mapping is also scaled with
  1.0, and the run announces the gap once (`InputWarning`, code
  `SCALINGS_INCOMPLETE`). Give `0.0: 0.0` to keep a frequency, for example
  DC, out of a fault. `Fault.scaling_at(frequency)` returns the factor used.

## Which reduction factor is which

| Field | Column of `res_all_impedances()` | Definition | Use |
|---|---|---|---|
| `ResultReductionFactor.value` | `reduction_factor` | EPR at the fault bus with coupling over EPR without coupling | a property of the line: $\lvert 1 - Z_m/Z_s \rvert$ for one line, independent of the fault-bus electrode |
| `ResultReductionFactor.value_current` | `reduction_factor_current` | $\lvert I_E \rvert / \lvert 3I_0 \rvert$, earth current over fault current | the reduction factor `r` of EN 50522 |

The two coincide only for ideally bonded stations. See
[Concepts](../concepts.md#reduction-factor-on-a-current-basis).

## See also

- [Formula syntax](formula-syntax.md)
- [Glossary and symbols](glossary.md)

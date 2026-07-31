# Transient simulations

The `groundinsight.simulation.transient` module extends the package
beyond the steady-state phasor solve into the time domain. A
`TransientStudy` binds a network, an active fault and one or more
user-defined source waveforms together and produces a
`ResultTransient` with EPR and shield-current time series at the
declared observation points.

## Physical / modelling context

The frequency-domain solver answers *"given a sinusoidal injection at
frequency $f$, what is the EPR?"*. Real fault currents are
non-sinusoidal: they switch on at fault inception, may carry an
exponentially decaying DC offset, and switch off again at clearing.
Two complementary solver paths are implemented to capture that
behaviour:

- **FFT solver (`solver="fft"`)** — samples the user waveform on a
  regular time grid, transforms to the frequency domain via NumPy's
  real-valued FFT, evaluates the existing nodal-admittance solve at
  every FFT bin, and transforms the bus voltages back via IFFT. It
  reuses `BusType.impedance_formula` and
  `BranchType.self_impedance_formula` and is therefore consistent
  with the stationary results bin-by-bin. Only current sources are
  accepted, and mutual coupling is not evaluated by this path.
- **State-space solver (`solver="state_space"`)** — assembles a
  modified-nodal-analysis ODE system $\dot x = A x + B u$,
  $y = C x + D u$ from the lumped RLC fields on `BusType` and
  `BranchType` (`R_formula`, `L_formula`, `C_formula`,
  `R_self_formula`, `L_self_formula`, `C_self_formula`,
  `R_mutual_formula`, `M_mutual_formula`) and integrates with
  `scipy.signal.lsim`. Voltage sources, Carson-style mutual coupling
  and pi-section branch capacitance are supported.

Source waveforms are produced by the small library in
[`groundinsight.simulation.waveforms`](#waveforms): `step`,
`sinusoidal_with_dc_offset` (the textbook
single-line-to-ground fault current with DC asymmetry) and
`damped_oscillation`. Custom waveforms are any vectorised callable
`f(t) -> values`.

## Example

```python
import groundinsight as gi
from groundinsight import waveforms

# Assume `net` is a built network with one current source 'infeed'
# and a fault 'fault1'.

study = gi.TransientStudy(network=net, fault_name="fault1")
study.set_source_waveform(
    "infeed",
    waveforms.sinusoidal_with_dc_offset(
        amplitude=1e3, frequency_hz=50.0,
        t_on=0.02, t_off=0.12,
        dc_amplitude=500.0, dc_decay_tau=0.05,
    ),
)
study.set_observation(buses=["bus_fault"], branches=["cable_1"])

result = study.solve(t_end=0.2, dt=1e-4, solver="fft")

# Plot the time series
gi.plot_epr_transient(result=result, title="EPR transient")
gi.plot_branch_current_transient(result=result, title="Shield current")

# Long-format DataFrame for further post-processing
df = result.to_polars()
```

Switching to the state-space solver only requires changing the
`solver` argument and ensuring the network's bus and branch types
carry the lumped RLC formulas required by the ODE form.

## API reference

### Transient study and result

::: groundinsight.simulation.transient

### Waveforms

::: groundinsight.simulation.waveforms

The matching matplotlib helpers `plot_epr_transient` and
`plot_branch_current_transient` are documented on the
[Plotting](plotting.md) page.

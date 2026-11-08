# Transient studies

A real earth-fault current switches on, may carry a decaying DC offset and
switches off again. This guide computes the earth potential rise and the
screen currents over time with `gi.TransientStudy`, chooses between its two
solvers and reads the warnings that come with them. The script is
[`transient.py`](../snippets/transient.py); its output is at the
[end of the page](#output). The derivations of both solvers are in
[Transient simulation](../transient.md).

## Choose the solver

| | `solver="fft"` | `solver="state_space"` |
|---|---|---|
| model | the stationary nodal system at every FFT bin, from the impedance formulas | a state-space model from the lumped R/L/C fields, evaluated at one frequency `f_eval` |
| needs | the impedance formulas only | `R_formula` (and optionally `L_formula`, `C_formula`) on the bus types, `R_self_formula`, `L_self_formula` (`C_self_formula`, `R_mutual_formula`, `M_mutual_formula`) on the branch types |
| sources | current sources with a galvanic connection to the fault bus | current and voltage (Thévenin) sources |
| mutual coupling | **not modelled** — the earth potential rise comes out higher (conservative), and the solve says so | modelled for current sources |
| frequency dependence | exact per bin | fixed at `f_eval` (the smallest positive network frequency, or `solve(..., f_eval=...)`) |
| watch for | the window: the response must have decayed before `t_end` | branches without `R_self`/`L_self` are left out |

Use the FFT solver for a fast answer on a network without coupling, or as a
conservative bound; use the state-space solver when the coupling between
phase conductor and screens matters — in a cable network it usually does. On
a network without coupling, with a window long enough, the two agree to
numerical precision: that is the cheapest cross-check the package offers.

## Describe the network for both solvers

The impedance formulas serve the stationary solve and the FFT solver; the
lumped fields serve the state-space solver. Declare both when you want to
compare:

```python
--8<-- "docs/snippets/transient.py:network"
```

## The source waveform

The waveform factories of `gi.waveforms` take **peak** values of the
instantaneous current, whereas `Source.values` holds RMS phasors: a fault
current of 1000 A RMS has the amplitude $\sqrt 2 \cdot 1000$ A. `step`,
`sinusoidal_with_dc_offset` (switched sinusoid with a decaying DC offset,
`dc_amplitude`, `dc_decay_tau`) and `damped_oscillation` are provided; any
vectorised callable `f(t)` works. Both solvers use the waveform as the
literal injection and ignore `Fault.scalings` — scale the waveform itself; a
fault with scalings other than 1 is announced (`TRANSIENT_MODEL`).

```python
--8<-- "docs/snippets/transient.py:waveform"
```

## Solve and read the result

`study.set_observation(buses=..., branches=...)` names what is recorded;
`study.solve(t_end=..., dt=..., solver=...)` returns a `ResultTransient` with
`time_s`, `epr_t[bus]` and `i_branch_t[branch]` (instantaneous values, in the
earth-side sign frame of the stationary results) and `to_polars()` for a long
frame. `progress=` and `should_cancel=` report and stop a long solve.

```python
--8<-- "docs/snippets/transient.py:solve"
```

```python
--8<-- "docs/snippets/transient.py:steady_state"
```

## The warnings of a transient study

| Code | Solver | Meaning |
|---|---|---|
| `TRANSIENT_MODEL` | FFT | the network has mutual coupling, which the FFT solver ignores (the coupled branches are named); also: the window ends while the response still rings — lengthen `t_end` or pass `zero_pad=True` —, the fault carries scalings, or `M_mutual` exceeds `L_self` (a unit slip) |
| `TRANSIENT_SOURCE_NOT_CONNECTED` | FFT | a source with a waveform has no galvanic connection to the fault bus and injects nothing |
| `TRANSIENT_PHASE_SPLIT` | state space | meshed network: the phase current is split as at `f_eval`, and the split depends on frequency, so a DC offset and harmonics are split as at `f_eval` (below 1 % of the peak on a mixed cable/line ring) |
| `TRANSIENT_BRANCH_SKIPPED` | state space | branches without `R_self`/`L_self` are left out |
| `TRANSIENT_COUPLING_SKIPPED` | state space | the coupling is modelled for current sources only |
| `TRANSIENT_RESISTIVE_COUPLING_IGNORED` | state space | mutual data on branches without inductance is ignored |
| `DC_LIMIT` | FFT | every FFT grid contains a 0 Hz bin, so every formula is evaluated at DC; see [Harmonics, fault scalings and DC](harmonics.md#direct-current-f-0) |

They arrive as warnings; collect them with
`groundinsight.diagnostics.collecting()` around `solve`. Details:
[Warnings and diagnostics](../reference/warnings.md).

## Memory and time

The FFT solver assembles a dense $n \times n$ admittance matrix per frequency
bin — $16\,n^2$ bytes for $n$ active buses — in batches of at most 512 bins
that stay within the byte budget `fft_block_bytes` of `solve`, 256 MiB by default. The
budget binds from about 180 buses on; a 400-bus solve over 1001 bins peaks at
about 0.3 GB. A smaller budget saves memory and costs time on mid-size
networks (150 buses, 1001 bins: 0.7 s at the default, 4.5 s at 1 MiB); the
result does not change. The state-space solver's cost grows linearly with the
number of samples.

## Output

```text title="Output of transient.py"
stationary EPR at S: 381.2 V (RMS)
        fft: steady-state RMS 776.0 V, peak 1097.3 V, codes ['TRANSIENT_MODEL']
state_space: steady-state RMS 381.2 V, peak 539.3 V, codes []
```

The state-space solver reproduces the stationary result in the steady state;
its peak is √2 times the RMS value plus a small switching transient. The FFT
solver ignores the coupling and reports twice the earth potential rise — and
says so.

**Check it by hand.** For the two-bus line the earth current is
$I_E = I\,(Z_s - Z_m)/(Z_{UW} + Z_S + Z_s)$ with
$Z_s = (0.2 + j\,2\pi\,50 \cdot 1.2\cdot10^{-3}) \cdot 3 = 0.6 + 1.131j\ \Omega$ and
$Z_m = j\,2\pi\,50 \cdot 10^{-3} \cdot 3 = 0.942j\ \Omega$:
$U_E = 2 \cdot 1000 \cdot |0.6 + 0.188j| / |3.1 + 1.131j| = 381.2$ V. Without
the coupling ($Z_m = 0$): $2 \cdot 1000 \cdot |0.6 + 1.131j| / |3.1 + 1.131j| = 776.0$ V —
the FFT value. The test suite checks both steady states against these closed
forms.

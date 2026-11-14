# Harmonics, fault scalings and DC

This guide sets up a fault study with several frequencies — the fundamental,
harmonics, direct current — and shows what the solver does when the input
covers only part of them. It uses one script,
[`harmonics.py`](../snippets/harmonics.py); its output is at the
[end of the page](#output).

## Frequencies and source values

`Network.frequencies` lists the frequencies the network is solved at, in Hz,
as floats; `0.0` is DC. Every bus and branch evaluates its impedance formulas
at each of them, and each frequency is an independent solve of the same
nodal system. Keep the list strictly increasing — a shuffled list is kept
but announced (`FREQUENCY_ORDER`), because the FFT transient solver maps bins
by position.

`Source.values` holds the **RMS** phasor of the source current per frequency,
in A: the fundamental and the harmonic content of the earth-fault current at
the infeed, from a measurement or a harmonic study. A peak value from a
waveform record has to be divided by √2 first
([Conventions](../reference/conventions.md#phasors-are-rms-waveforms-are-instantaneous)).

The example: a substation `UW` (0.5 Ω) feeds an earth fault at station `S`
(ring electrode, 5.90 Ω at ρ = 100 Ω·m) over 3 km of the cable system of the
[data-sheet how-to](types-from-datasheets.md), with 1000 A at 50 Hz, 60 A at
250 Hz and 40 A at 350 Hz (illustrative values):

```python
--8<-- "docs/snippets/harmonics.py:network"
```

## Fault scalings

`Fault.scalings` multiplies the current of every source that reaches the
fault, per frequency. The source values describe the infeed; the scalings
describe the fault — which share of each frequency this fault draws. The
rules:

- `scalings` left out, `None` or `{}` means **1.0 at every frequency**, without
  a warning.
- A frequency missing from a non-empty mapping is scaled with 1.0 (full
  contribution), and the run announces it once (`SCALINGS_INCOMPLETE`). Write
  every frequency to make the choice explicit.
- `0.0` excludes a frequency from this fault: `{50.0: 1.0, 250.0: 0.0}`
  solves the fault at 50 Hz alone, whatever the source carries at 250 Hz.
- `Fault.scaling_at(frequency)` returns the factor the solve uses.

```python
--8<-- "docs/snippets/harmonics.py:solve"
```

```python
--8<-- "docs/snippets/harmonics.py:exclude"
```

## Per-frequency and RMS results

`res_fault`, `res_buses` and `res_branches` have one row per frequency and one
**RMS row** per element, which combines the frequencies as
$\sqrt{\sum_f |X(f)|^2}$ — the 0 Hz term with its full magnitude. The RMS row
has `is_rms` true and `frequency_Hz` null, and its ratios, impedances and
angles are null: a reduction factor has no RMS. Select a frequency with
`pl.col("frequency_Hz") == 250.0` and the RMS row with `pl.col("is_rms")`.
`res_all_impedances` has no RMS row; the comparison frames of an outage study
write `frequency_Hz` as text. All columns:
[Result tables](../reference/results.md).

For the comparison with the permissible touch voltage of EN 50522, use the
total RMS of `U_E` against the 50 Hz curve. That is conservative for the body
current, not shown for the touch voltage; see
[Compare results with EN 50522 limits](en50522-check.md#harmonics-and-dc).

**Check it by hand.** For one line between two stations the earth current
has a closed form: with the source at `UW` and the fault at `S`,
$I_E = I\,(Z_s - Z_m)/(Z_{UW} + Z_S + Z_s)$ and $U_E = Z_S\,I_E$. At 50 Hz the
cable gives $Z_s - Z_m = 3 \cdot 0.727/3 = 0.727\ \Omega$ (the screens'
resistance; the earth-return terms cancel) and
$Z_{UW} + Z_S + Z_s = 7.275 + 1.940j\ \Omega$, so
$I_E = 1000 \cdot 0.727 / 7.529 = 96.56$ A, $r = 0.0966$ and
$U_E = 5.900 \cdot 96.56 = 569.67$ V — the first row of the output. The test
suite recomputes every row this way.

## When the input does not cover every frequency

| Input | What the solve does | Announced as |
|---|---|---|
| a non-empty `scalings` without some network frequencies | scales those with 1.0 | `SCALINGS_INCOMPLETE` (`InputWarning`), once per run |
| a source that reaches the fault, without a value at some network frequencies | the source contributes 0 A there | `SOURCE_FREQUENCY_GAP` (`InputWarning`), once per source and run |
| a source that reaches the fault, without a value at **any** network frequency | refuses: it would contribute 0 A everywhere | `ValueError` naming the source, its keys and the network frequencies |
| a source value at a frequency the network does not solve | ignores the value | `SOURCE_FREQUENCY_OUTSIDE_NETWORK` (`InputWarning`), when the source is created |

The first two arrive in `Result.diagnostics`; the last one is raised by
`create_source` before any solve, so collect it with
`groundinsight.diagnostics.collecting()`. `gi.check_network(net)` reports all
three before a solve. Every code: [Warnings and diagnostics](../reference/warnings.md).

```python
--8<-- "docs/snippets/harmonics.py:gaps"
```

## Direct current (f = 0)

A DC study is an ordinary solve at `f = 0`; only the input needs care.

- **Network:** `frequencies=[0.0]`, or a mixed list such as `[0.0, 50.0]` —
  the 0 Hz bin is independent, and a study with both returns the same 50 Hz
  answer as one with 50 Hz alone.
- **Current source:** a real value at `0.0`, e.g. `values={0.0: 1000.0}`.
- **Voltage source:** `voltage` and `source_impedance` with a `0.0` entry; the
  source impedance needs a real part, a purely inductive one is zero at DC.
- **Fault:** no `0.0` key needed (1.0 by default); `0.0: 0.0` keeps DC out of
  a fault. The IEC 60909 characteristics (`i_k_a`, `r_to_x`, `kappa`) describe
  an alternating current and have no meaning in a DC study.

**How a formula is read at 0 Hz.** The value is the limit for $f \to 0$,
found by approaching on `1e-6`, `1e-7`, `1e-8` Hz:

- a reactance written as `j*2*pi*f*L` vanishes, and the Carson earth-return
  terms of a cable tend to their limit (zero for the mutual impedance) —
  nothing to announce;
- a **constant reactance** such as `+ j*0.2` has no reading at DC: the 0 Hz
  bin keeps the real part and drops the reactance, with a `DC_LIMIT` warning
  (`DCLimitWarning`) when the element is created;
- a **pole** — a series capacitance $1/(j\omega C)$ — is an open circuit;
- an **exact zero** — a purely inductive bond — is an ideal short circuit,
  which the nodal system cannot invert. The element gets a **stand-in** of
  $\sqrt{\varepsilon_\text{mach}}\,Z_\text{min} = 1.49\cdot10^{-8}\,Z_\text{min}$,
  where $Z_\text{min}$ is the smallest other impedance of the network at
  0 Hz, and the run says so (`DC_LIMIT` in `Result.diagnostics`). Only the
  0 Hz bin is affected.

```python
--8<-- "docs/snippets/harmonics.py:dc"
```

**Check it by hand.** In the chain above (electrodes 0.8 Ω, 12 Ω and 3.5 Ω,
bond A–B purely inductive, 0.35 Ω from B to C, 1000 A injected at A, fault at
C) the stand-in is $1.49\cdot10^{-8} \cdot 0.35\ \Omega = 5.2\cdot10^{-9}\ \Omega$.
The exact answer merges A and B into one node with $0.8 \parallel 12 = 0.75\ \Omega$:
$u_{AB}\,(1/0.75 + 1/0.35) - u_C/0.35 = 1000$ and
$u_C\,(1/3.5 + 1/0.35) - u_{AB}/0.35 = -1000$ (the package's earth-side
frame) give $u_A = u_B = 57.07$ V and $u_C = -266.30$ V — what the solve
reports. Prefer the merge when the potential *difference* across an ideal
bond is part of what you report, or give the bond a small honest resistance,
which also removes the warning.

**What is refused.** An impedance that is inverted — an electrode, a
conductor with `grounding_conductor=True`, a source impedance — may not have a
negative real part, nor a real part of $-\infty$ at 0 Hz (a `log(f)` term
without a factor `f`), nor be NaN; above 0 Hz it may not be zero either. Each
raises a `ValueError` that names the element, the frequency and the formula.
`+inf` (the formula `"nan"`) stays legal everywhere: it is the open end.

```python
--8<-- "docs/snippets/harmonics.py:refused"
```

The reasoning behind these rules — the three kinds of singularity at 0 Hz,
the measured accuracy of the stand-in — is in the explanation of direct
current; the formula side is summarised in
[Formula syntax](../reference/formula-syntax.md#direct-current-f-0). Every
transient study contains a 0 Hz bin as well:
[Transient simulation](../transient.md).

## Output

```text title="Output of harmonics.py"
f / Hz  I_F / A  U_E / V       r
    50   1000.0   569.67  0.0966
   250     60.0    21.61  0.0610
   350     40.0    11.62  0.0493
   RMS   1002.6   570.20       -
50 Hz only (scalings 1/0/0): U_E, total RMS = 569.67 V
scaling missing at 350 Hz: ['SCALINGS_INCOMPLETE']
source value missing at 350 Hz: ['SOURCE_FREQUENCY_GAP']
source without a network frequency: ValueError: Source 'infeed' at bus 'UW' reaches fault 'F' but carries values only at 16.7 Hz, none of the network frequencies (50 Hz, 250 Hz, 350 Hz)
source value at 150 Hz: ['SOURCE_FREQUENCY_OUTSIDE_NETWORK']
DC chain: u_A = 57.07 V, u_B = 57.07 V, u_C = -266.30 V
DC stand-in for the bond: 5.215e-09 Ohm (DC_LIMIT)
constant reactance at 0 Hz: ['DC_LIMIT']
log(f) electrode at 0 Hz: ValueError: Its real part is minus infinity at 0 Hz
```

The DC potentials are in the package's earth-side sign frame (see
[Signs and phase angles](../reference/conventions.md#signs-and-phase-angles)).

# Reduction factors and earthing impedance

`groundinsight` reports **one** reduction factor for every fault and frequency,
together with the quantities of the EN 50522 chain $U_E = r \cdot I_F \cdot
Z_E$ at the fault bus. This page defines it, shows why the definition needs no
balanced state far from the fault, and how it is checked. Where each number
appears in the results is in the
[glossary](../reference/glossary.md#quantities-at-the-fault); how they feed
the verification of the standard is in
[Compare results with EN 50522 limits](../howto/en50522-check.md).

| Field of `ResultReductionFactor` | Column of `res_fault()` | Meaning |
|---|---|---|
| `value` | `r` | the reduction factor $r = \lvert I_E\rvert / \lvert I_F\rvert$ |
| `i_earth` | `I_E_A` | $I_E = U_E / Z_E$, the earth current |
| `u_earthing` | `U_E_V` | $U_E$, the EPR of the fault bus |
| `z_earthing` | `Z_E_Ohm`, `Z_E_degree` | $Z_E$, the driving-point impedance of the earthing network at the fault bus |
| `i_fault` | `I_F_A`, `I_F_degree` | $I_F = 3I_0$, the scaled source currents that reach the fault |

## The definition { #reduction-factor }

At the fault bus, per frequency,

$$
I_E = \frac{U_E}{Z_E}, \qquad r = \frac{\lvert I_E\rvert}{\lvert I_F\rvert},
$$

with

- `u_earthing` = $U_E$, the **EPR at the fault bus** — the same complex number
  as the fault bus's `uepr_freq`;
- `z_earthing` = $Z_E$, the **driving-point impedance of the earthing network
  at the fault bus**: one ampere injected there against remote earth, every
  electrode and shield in place, no inductive coupling, no Thevenin loop
  closure. It is what an earthing-impedance measurement at the station sees,
  the stations bonded through cable shields included, and it is the reported
  grounding impedance.

**Why this is the earth current.** The network seen from the fault bus is
linear. By Norton's theorem its potential there is $U_E = Z_E \cdot I_N$, where
$I_N$ is the current that flows into the fault bus's earthing when that
earthing is ideal ($Z \to 0$, the fault bus held at remote earth). $I_N$ is the
part of the fault current that does not return to the sources through shields
and conductors — whether it is pushed back by induction or flows back
galvanically to a source station bonded through the shields. That is the earth
current of the standard, so $I_E = I_N = U_E / Z_E$, and the chain

$$
U_E = r \cdot I_F \cdot Z_E
$$

holds **exactly**, with the $Z_E$ a measurement at the station sees. No
balanced state far from the fault is needed — the classical way to define $r$
for long overhead lines, where some tower far away has $U_E = 0$ and the earth
wire carries the whole reduction current, and a state a short cable network
does not reach.

Three properties follow:

- $r$ **does not depend on the electrode of the fault bus**: the short circuit
  takes it out. The electrode enters $U_E$ through $Z_E$ only. On the
  six-station feeder of the
  [decomposition example](decomposition.md#splitting-the-network-at-the-fault),
  sweeping the electrode of the faulted station from 0.01 Ω to 100 Ω
  leaves $r$ at 0.279869 while $Z_E$ and $U_E$ grow by a factor of 80
  (0.0099 → 0.796 Ω, 8.3 → 668 V).
- On a **long line** with the infeed far away, $r$ tends to the cable value
  $\lvert 1 - Z_m/Z_s\rvert$.
- For **one cable** from a source station earthed with $Z_A$ to the fault,
  $r = \lvert (Z_s - Z_m)/(Z_s + Z_A)\rvert$: with $Z_A \to 0$ the cable value,
  with $Z_A \to \infty$ zero — the whole fault current returns through the
  shield to the transformer, none through the soil.

`r` and `I_E` are `None` at a frequency that carries no fault current and where
$Z_E$ is undefined. A fault on the bus of every source that feeds it injects
nothing into the earthing network: $r = 0$ there, said by the info diagnostic
`FAULT_AT_FEEDING_BUS`.

**The denominator is every source that feeds the fault.** `i_fault` is the sum
of the scaled currents of all sources that reach the fault, a source on the
fault bus itself included. Such a source injects and withdraws its current at
the same bus; it adds to $I_F$ but not to $I_E$. With 1000 A from a remote
infeed and 500 A from a source on the fault bus, $r$ is exactly two thirds of
its value without the local source, while `i_earth` does not change. The
reduction factor of a line
in the sense of the standard counts the remote infeed only; divide `i_earth` by
it where a local source is present.

**Where the short circuit is exact.** Norton's theorem holds for the network
seen from the fault bus with the phase currents as they are. In a mesh whose
routes are different cables, the coupled solve lets the shield currents act
back on the split of the phase current, so earthing the fault bus ideally
also moves the phase currents a little: there the current into an ideal
electrode and $U_E / Z_E$ differ slightly — by 4·10⁻⁴ relative on a 20 kV
ring with an Al 1×95 and a Cu 1×95 cable section (`tests/test_coupled_solve.py`).
`r` keeps the definition $U_E/(Z_E I_F)$, the one that reproduces $U_E$ with the
$Z_E$ a measurement sees. In radial networks and wherever every mesh has one
cable type the two are the same.

## Checked a second way

The identity $U_E = r \cdot I_F \cdot Z_E$ holds by construction and checks
nothing. What is checked is the short circuit: the fault station earthed with
1e-9 Ω instead of its electrode, and the current into that electrode read off.
The test suite does the same with an independent solver that holds the fault
bus at remote earth (`tests/test_reduction_factor.py`,
`tests/test_audit_pass19_physics.py`), and the reference case `en50522_chain`
of `gi.run_reference_cases()` with a nodal system assembled on its own.

The long run of the standard's lumped picture — 64 sections of 0.5 km,
stations 2 Ω, infeed 0.2 Ω, 1 kA, illustrative per-km values
([`reduction_factors.py`](../snippets/reduction_factors.py)):

```python
--8<-- "docs/snippets/reduction_factors.py:chain"
```

```python
--8<-- "docs/snippets/reduction_factors.py:short_circuit"
```

```python
--8<-- "docs/snippets/reduction_factors.py:closed_form"
```

| Quantity | Model | Second way |
|---|---|---|
| `value` ($r$) | 0.349781 | ideal fault electrode: 0.349781; $\lvert 1 - k\rvert$ = 0.349781 |
| $Z_E$ | 0.6249 + 0.3081j Ω | $Z_\infty$ = 0.6249 + 0.3081j Ω |
| $\lvert U_E\rvert$ | 243.708 V | $\lvert 1-k\rvert \cdot I \cdot \lvert Z_\infty\rvert$ = 243.708 V |
| $\lvert I_E\rvert$ | 349.78 A | |

One cable of 1 km ($Z_s$ = 0.2 + 0.4j Ω, $Z_m$ = 0.1 + 0.2j Ω), the fault
station earthed with 1 Ω, the source station with $Z_A$:

```python
--8<-- "docs/snippets/reduction_factors.py:one_cable"
```

| $Z_A$ | $r$ (model) | $\lvert (Z_s - Z_m)/(Z_s + Z_A)\rvert$ |
|---|---|---|
| 10 Ω | 0.021905 | 0.021905 |
| 1 Ω | 0.176777 | 0.176777 |
| 0.1 Ω | 0.447214 | 0.447214 |
| 0.01 Ω | 0.494952 | 0.494952 |
| 10 µΩ | 0.499995 | 0.499995 |

The cable value $1 - Z_m/Z_s$ = 0.5 is the limit of a source station earthed
ideally. With ordinary station electrodes the shield carries a large part of
the fault current back to the transformer galvanically, and $r$ is smaller —
in a meshed cable network whose infeeds are bonded through the shields
typically well below the cable value.

## Frequency dependence of the reduction factor

On a long line $r$ is the cable value $\lvert 1 - Z_m/Z_s\rvert$. For a shielded cable the impedances split into a resistive and an
inductive part,

$$
Z_{\text{self}}(f) = R + j\,\omega L,
\qquad
Z_{\text{mutual}}(f) = j\,\omega M,
\qquad \omega = 2\pi f.
$$

With full coupling between the faulted phase and the grounding conductor
(the geometric ideal $M = L$) the closed form simplifies to

$$
r(f) \;=\; \left| 1 - \frac{j\,\omega L}{R + j\,\omega L} \right|
       \;=\; \frac{R}{\sqrt{R^{2} + (\omega L)^{2}}}.
$$

Two limits follow directly:

- $f \to 0$: $\omega L \to 0$, so $Z_{\text{mutual}}/Z_{\text{self}} \to 0$
  and $r \to 1$. At DC the shield carries no induced current, the entire
  fault current flows through the local earth path and the reduction
  factor equals 1.
- $f \to \infty$: the imaginary parts dominate, so
  $Z_{\text{mutual}}/Z_{\text{self}} \to M/L = 1$ and $r \to 0$. At high
  frequency the shield short-circuits the inductive coupling, almost the
  entire fault current returns metallically and the EPR collapses.

In a real cable the resistive part is small compared with $\omega L$
already at power frequency, which is why MV cables typically reach
$r \approx 0.3 \ldots 0.4$ at 50 Hz and the reduction factor decays
quickly above one or two hundred Hz. This convergence is exercised
explicitly by
`tests/test_topology_and_reduction.py::test_reduction_factor_sweep_*`,
which sweeps a single MV cable section and a 20-bus symmetric ring
from 50 Hz to 5 kHz and asserts both the closed form above and the
monotonic decay towards zero.

## Grounding impedance

`ResultGroundingImpedance.value` is `Z_E` in the EN 50522 sense: the
driving-point impedance of the earthing network at the fault bus, identical to
`ResultReductionFactor.z_earthing`,

$$
Z_G(f) = Z_E(f) = Z_\text{dp}(F, f) = \left(Y_E(f)^{-1}\right)_{FF},
$$

with $Y_E$ the nodal matrix of electrodes and shields alone — without the
inductive coupling and without the Thevenin loop closures of voltage sources,
which belong to the fault loop rather than to the earthing system. It is the
impedance an earthing-impedance measurement at the station sees, the stations
bonded to it through cable shields included; `gi.bus_response(...)` (Möbius
form) and `gi.analyze_cuts(...)` (without voltage sources) reproduce it. Note
that it is not the station electrodes in parallel either — the shield sections
between them add to it.

`Z_E` is `None` (with a `ModelAssumptionWarning` `EARTHING_IMPEDANCE_UNDEFINED`)
where the fault side reaches remote earth only through the loop closure of a
voltage source: no bus connected to the fault bus through shields carries an
electrode. That is decided from the structure of the earthing network, not
from the solver — the matrix of such a group of buses is singular only up to
rounding and a solve returns a meaningless 1e15 Ω. In `bus_response`, where
the electrode at the studied bus is the fault side's only one, every ampere
leaves through it: `Z_E = Z_B + BusResponse.z_fault_ideal`.

It is exposed **per frequency** through `net.res_all_impedances()`, which
has one row per (fault, frequency) and no RMS row: a grounding impedance is
defined for a frequency, and there is no meaningful way to reduce a set of
complex impedances at different frequencies to one scalar. The RMS rows live
on `net.res_buses()` and `net.res_branches()`, where they aggregate
*quantities* ($\sqrt{\sum_f |x(f)|^2}$), and are marked there by the
`is_rms` column.

## Results stored with the earlier definitions

A result saved by an earlier release carries other reduction factors: the
ratio of the fault-bus EPR with and without the inductive coupling, and a sum
of electrode currents over a group of buses. It still loads. Where it holds
$U_E$, $Z_E$ and $I_F$ — every result of 2026.11 — `value` and `i_earth` are
recomputed from them on reading, exactly, and the result carries the info
diagnostic `LEGACY_REDUCTION_FACTOR`. A result of 0.6.0, whose stored $U_E$
and $Z_E$ were group means, keeps no earthing quantities and no reduction
factor until the fault is solved again (`LEGACY_EARTHING_DEFINITION`).

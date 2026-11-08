# Reduction factors and earthing impedance

`groundinsight` reports two reduction factors for every fault and frequency,
together with the quantities of the EN 50522 chain $U_E = r \cdot 3I_0 \cdot
Z_E$ at the fault bus. This page defines each of them, shows when they agree
and when they do not, and says which one the standard means. Where each
number appears in the results is in the
[glossary](../reference/glossary.md#quantities-at-the-fault); how they feed
the verification of the standard is in
[Compare results with EN 50522 limits](../howto/en50522-check.md).

| Field of `ResultReductionFactor` | Column of `res_all_impedances()` | Meaning |
|---|---|---|
| `value` | `reduction_factor` | EPR at the fault bus with coupling over EPR without coupling |
| `value_current` | `reduction_factor_current` | $\lvert I_E\rvert / \lvert 3I_0\rvert$, the reduction factor `r` of EN 50522 |
| `u_earthing` | — | $U_E$, the EPR of the fault bus |
| `z_earthing` | `grounding_impedance_Ohm`, `grounding_impedance_deg` | $Z_E$, the driving-point impedance of the earthing network at the fault bus |
| `i_earth`, `earth_buses` | — | $I_E$ and the group of buses whose electrode currents it sums |
| `i_fault` | — | $3I_0$, the scaled source currents that reach the fault |

## The EPR ratio { #reduction-factor }

The reduction factor $r$ at the fault bus is defined as

$$
r(f) = \frac{|u_{\text{fault}}^{\text{(with mutual)}}(f)|}
             {|u_{\text{fault}}^{\text{(without mutual)}}(f)|}.
$$

`groundinsight` obtains the denominator by re-solving the same network with
all mutual-coupling Norton sources removed. For a single shielded line
directly between source and fault with identical impedances the expression
collapses to the familiar analytical form

$$
r = \left| 1 - \frac{Z_{\text{mutual}}}{Z_{\text{self}}} \right|.
$$

The same closed form applies to a fully symmetric ring with the fault
diametrically opposite the source — the Norton injections in the two
ring halves are then perfectly anti-parallel and superpose to the same
expression as the single-line case. More generally it holds in any network
whose branches all have the same ratio $k = Z_m/Z_s$: the Norton injections
then telescope along every route to $(1-k)$ times the source injection, and
every potential with coupling is $(1-k)$ times the one without.

## Reduction factor on a current basis

The ratio above is structurally blind to the impedance *at* the fault bus. Both
solves use the same `Y`, so changing the diagonal entry `Y_ff` is a rank-1
update whose effect cancels in the quotient. On the six-station feeder of the
[decomposition example](decomposition.md#splitting-the-network-at-the-fault),
sweeping the electrode of the faulted station from 0.01 Ω to 100 Ω leaves
`value` at 0.310003, while $Z_E$ and the potential rise both grow by a factor
of 80 (0.0099 → 0.796 Ω, 8.3 → 668 V) and `value_current` falls from 0.278 to
0.168. That is not a numerical artefact — the closed form in
[Characterising a location](decomposition.md#characterising-a-location-without-its-electrode)
derives it.

`ResultReductionFactor.value_current` is the **measured** definition alongside
it: the total earth-return current over the total fault current,

$$
r_I(f) = \frac{|\underline{I}_E(f)|}{|\underline{I}_F(f)|}.
$$

`I_E` is the sum of the electrode currents of **every bus that feeds the soil**
— from the fault outwards in *every* direction, a ring or a mesh having more
than one, up to where the potential profile turns — not the electrode current at
the faulted station. In a cable network with
continuous shields the stations are bonded to one another, so the current spreads
along the shields and leaks into the soil at every one of them; the earthing
current is distributed over the whole bonded system by construction. Measured on
a six-station feeder, counting only the faulted station understates `r_I` by a
factor of 1.8 to 4.7 depending on where the fault sits.

Two things make the sum less obvious than it looks.

**Summing over every bus gives exactly zero.** Whatever enters the soil somewhere
leaves it somewhere else, so Kirchhoff at the whole network forces
`Σ I_a = 0`. A group has to be selected — and "every bus except the faulted one"
is not that group: by the same law it collapses back to the electrode current of
the faulted station alone.

**The group cannot be found from the potential profile.** The crossing between
the two groups generally falls *between* two stations rather than on one, and
when the fault sits close to the infeed the profile may never come near zero at
all — it passes through a shallow minimum and rises again. On the verification
feeder with the fault one station from the infeed, `|EPR|` runs 91.4, 19.6, 18.7,
18.1, 17.7, 17.5 V: there is no crossing to find.

What *is* unambiguous per bus is the **direction** of its electrode current, so
the split is made in the complex plane: the group is the set of phasors in one
half-plane, and the half-plane is the one whose sum has the largest magnitude.
That needs no angle nominated and no tolerance tuned, and it reduces to the
obvious answer whenever the two groups are cleanly opposed. Both groups carry the
same sum with opposite signs, so `|I_E|` does not depend on which is called
which; the fault bus anchors the naming.

`ResultReductionFactor` reports `i_earth` (the ampere value) and `earth_buses`
(the group) next to the ratio, because the split is a modelling statement and
should be inspectable. The separation of the two groups says how cleanly the
phasors separated; below 0.9 the electrode currents are spread in angle and a
single scalar earth-return current is a coarser description than it looks, and
`run_fault` says so (`ModelAssumptionWarning`, code `EARTH_GROUP_SPREAD`).
Where no electrode carries a noticeable share of the fault current — the fault
current returns entirely through shields and conductors — there is no group,
and $I_E$ and `value_current` are `None` (`EARTH_GROUP_UNDEFINED`).
`res_all_impedances()` carries both factors.

**The denominator is every source that feeds the fault.** `i_fault` is the sum
of the scaled currents of all sources that reach the fault, a source on the
fault bus itself included. Such a source injects and withdraws its current at
the same bus; it adds to $3I_0$ but not to $I_E$. With 1000 A from a remote
infeed and 500 A from a source on the fault bus, `value_current` is exactly two
thirds of its value without the local source, while `i_earth` and `value` do
not change. The reduction factor of a line in the sense of the standard counts
the remote infeed only; divide `i_earth` by it where a local source is present.

## Which one is the EN 50522 reduction factor

`value_current`, `r = |I_E|/|3I_0|`. The norm writes the earthing voltage as

$$
U_E = 3 I_0 \cdot Z_E \cdot r,
$$

with two quantities that `groundinsight` reports at the **fault bus**
(`ResultReductionFactor`):

- `u_earthing` is `U_E`, the **EPR at the fault bus** — the same complex number
  as the fault bus's `uepr_freq`;
- `z_earthing` is `Z_E`, the **driving-point impedance of the earthing network
  at the fault bus**: one ampere injected there against remote earth, every
  electrode and shield in place, no inductive coupling, no Thevenin loop
  closure. It is what an earthing-impedance measurement at the station sees,
  the stations bonded through cable shields included, and it is the reported
  grounding impedance.

The chain is a physical statement about these, not an identity: it holds where
the norm's assumptions hold. On a long homogeneous cable run with the fault far
from the infeed it closes with `r = |1 - Z_m/Z_s|` (the factor `value`) on the
ladder limit `Z_E = -Z'/2 + sqrt(Z'^2/4 + Z_e Z')` to better than `1e-5`
(`tests/test_audit_pass19_physics.py`, 64 sections). Away from that case — a
short feeder, the infeed close by, large electrodes — `|U_E|` and
`r \cdot 3I_0 \cdot |Z_E|` differ, and the difference measures how far the
installation is from the norm's lumped picture; nothing raises or warns.

The long run, built and compared with the closed form
([`reduction_factors.py`](../snippets/reduction_factors.py); 64 sections of
0.5 km, stations 2 Ω, infeed 0.2 Ω, 1 kA, illustrative per-km values):

```python
--8<-- "docs/snippets/reduction_factors.py:chain"
```

```python
--8<-- "docs/snippets/reduction_factors.py:closed_form"
```

| Quantity | Model | Closed form |
|---|---|---|
| `value` | 0.349781 | $\lvert 1 - k\rvert$ = 0.349781 |
| `value_current` | 0.359015 | — |
| $Z_E$ | 0.6249 + 0.3081j Ω | $Z_\infty$ = 0.6249 + 0.3081j Ω |
| $\lvert U_E\rvert$ | 243.708 V | $\lvert 1-k\rvert \cdot I \cdot \lvert Z_\infty\rvert$ = 243.708 V |
| `value` · 3I<sub>0</sub> · \|Z<sub>E</sub>\| | 243.708 V | |
| `value_current` · 3I<sub>0</sub> · \|Z<sub>E</sub>\| | 250.142 V | |

The chain closes with the coupling factor of the cable. `value_current` lies
2.6 % above it here: along the run the electrode currents decay and rotate
with the complex propagation constant of the ladder, the half-plane split
leaves out those that point backwards, and the sum of the rest exceeds
$\lvert 1-k\rvert \cdot I$.

The current-weighted mean potential of the earth group and that mean divided by
`I_E` — which release 0.6.0 reported as `u_earthing` / `z_earthing` —
are kept as `u_earthing_group` / `z_earthing_group`. On that pair the chain
closes to machine precision, but only because `z_earthing_group` is *defined* as
`u_earthing_group / I_E`; it checks nothing. On the long run above they are
138.83 V and 0.3867 Ω, 43 % and 44.5 % below the fault-bus EPR and the
driving-point impedance, which is why they are no longer reported as `U_E` and
`Z_E`: taken into a touch-voltage assessment they are unconservative.

The fault current the factor divides by is stored as `i_fault`
(`sum(scaling * I_src)` over the sources that reach the fault).

`value` uses a *different* reference: the mutual coupling removed, but the
cable shield still in place as a metallic return path. Most of the current
still comes back through the shield rather than through the soil, so that
reference voltage is far below `Z_E \cdot 3I_0` (the norm's `r = 1`, the whole
fault current through the earthing impedance). Both factors are correct
answers; they answer different questions.

## The two factors are not two computations of one number

They coincide in exactly one case, and it is worth knowing which. For a route
with shield impedance $Z_s$, mutual impedance $Z_m$ and station electrodes
summing to $Z_E$ along the earth-return path,

$$
r_\text{coupling} = \frac{Z_s - Z_m}{Z_s},
\qquad
r_I = \frac{Z_s - Z_m}{Z_s + Z_E},
$$

so their ratio is the current divider $Z_s/(Z_s + Z_E)$ and nothing else. The
first is the **ideally bonded limit** — the tabulated property of the cable,
$1 - Z_m/Z_s$, independent of the station earths by construction. The second is
what the earthing system of *this* network actually passes into the soil.

Verified against the closed form to machine precision over five decades of
electrode impedance on a two-section line with $Z_s = 0.2 + 0.4\mathrm{j}\ \Omega$:

| electrode per station | $r_\text{coupling}$ | $r_I$ | ratio | $Z_s/(Z_s+Z_E)$ |
|---|---|---|---|---|
| 10 Ω | 0.500000 | 0.011067 | 0.022135 | 0.022135 |
| 1 Ω | 0.500000 | 0.100000 | 0.200000 | 0.200000 |
| 0.1 Ω | 0.500000 | 0.395285 | 0.790569 | 0.790569 |
| 0.01 Ω | 0.500000 | 0.489820 | 0.979639 | 0.979639 |
| 10 µΩ | 0.500000 | 0.499990 | 0.999980 | 0.999980 |

A wide gap is therefore a statement about the network, not a defect: the
stations are not bonded well enough for the cable's tabulated reduction factor
to describe what the soil sees. `groundinsight` logs the divider at INFO where
it falls below 0.5, rather than as a warning — with ordinary station electrodes
it is the normal case, and a log that warns about the normal case stops being
read.

## Frequency dependence of the reduction factor

For a shielded cable the impedances split into a resistive and an
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

The quotient of the earth group's mean potential by `I_E`, which release
0.6.0 reported as the grounding impedance, is kept as
`z_earthing_group`; on a long homogeneous run it is 44.5 % below `Z_E`. The
definition before 0.6.0, $u_{\text{EPR}}/(r \cdot I_{\text{fault}})$, mixed the
coupling ratio into an impedance.

It is exposed **per frequency** through `net.res_all_impedances()`, which
has one row per (fault, frequency) and no RMS row: a grounding impedance is
defined for a frequency, and there is no meaningful way to reduce a set of
complex impedances at different frequencies to one scalar. The RMS rows live
on `net.res_buses()` and `net.res_branches()`, where they aggregate
*quantities* ($\sqrt{\sum_f |x(f)|^2}$), and are marked there by the
`is_rms` column.

## Results stored with the earlier definition

A result saved by a release that reported the group quantities as $U_E$ and
$Z_E$ — in a JSON project file or a database — still loads. Its stored values
are moved to `u_earthing_group` / `z_earthing_group`, `u_earthing`,
`z_earthing` and the grounding impedance stay empty, and the result carries
an `info` diagnostic, code `LEGACY_EARTHING_DEFINITION`, that says why. Run
the fault again with `run_fault` to obtain $U_E$ and $Z_E$ in the current
sense; nothing is recomputed silently on load.

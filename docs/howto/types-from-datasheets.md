# Bus and branch types from data sheets

A `BranchType` and a `BusType` carry their impedances as formula strings in
`f`, `rho` and `l` ([formula syntax](../reference/formula-syntax.md)). This
page shows how to turn a cable data sheet, an overhead-line conductor table and
an earth-electrode geometry into those strings, with a worked example for the
single-core cable NA2XS(F)2Y 1×300 RM/25 12/20 kV.

Every number on this page is computed by
[`types_from_datasheets.py`](../snippets/types_from_datasheets.py), which
evaluates each cable quantity three ways — the closed form, Pollaczek's
integral and a `groundinsight` network — and the test suite checks the
agreement stated below.

!!! warning "Check the inputs before you use the types"

    The data-sheet values are quoted with their source. Values that a data
    sheet does not give — the build-up between insulation and screen wires,
    the stranding factor of the conductor, the geometry of the overhead line —
    are estimates and are marked as such. Replace them by the values of your
    cable and line.

## What a branch type needs

| Field | Quantity | Symbol |
|---|---|---|
| `self_impedance_formula` | loop of the earthing conductor (cable screen, earth wire) with earth return | `Z_s` |
| `mutual_impedance_formula` | coupling between the loop of the faulted phase conductor and that loop, both with earth return | `Z_m` |
| `phase_impedance_formula` | loop of the faulted phase conductor with earth return, the screen bonded at both ends | `Z_red = Z_pp − Z_m²/Z_s` |
| `grounding_conductor=True` | the branch has a screen or earth wire bonded to the buses | – |

Each formula is the impedance of the whole branch: a per-km expression times
`l`. The types below also set `conductor_material` and `cross_section_mm2` of
the screen, which the thermal check of the earthing conductors reads.

## Inputs

| Input | Symbol | Where it comes from |
|---|---|---|
| DC resistance of the screen per km | R<sub>s</sub>′ | data sheet or test certificate of the cable; else the EN 60228 value of the screen cross-section |
| mean radius of the screen | r<sub>s</sub> | diameter over insulation plus the build-up to the middle of the screen wires |
| DC resistance of the conductor per km | R<sub>c</sub>′ | data sheet (EN 60228) |
| geometric mean radius of the conductor | GMR<sub>c</sub> | conductor radius × e<sup>−1/4</sup> for a solid round conductor |
| outer diameter | D | data sheet; the axis distance of touching cables |
| soil resistivity | ρ | measurement (EN 50522:2022, Annex L.1) |
| frequencies | f | the study: 50 Hz and the harmonics of interest |

## Screen–earth loop and coupling

Carson's formula [3] with the equivalent earth-return depth
$D_E = 658\,\sqrt{\rho/f}$ m gives, per km,

$$
Z_s' = R_s' + \pi^2\,10^{-4}\,f + j\,4\pi\,10^{-4}\,f\,\ln\frac{D_E}{r_s},
\qquad
Z_m' = \pi^2\,10^{-4}\,f + j\,4\pi\,10^{-4}\,f\,\ln\frac{D_E}{r_s}
\qquad [\Omega/\text{km}].
$$

- $\pi^2 10^{-4} f = \omega\mu_0/8$ is the resistance of the earth return,
  0.0493 Ω/km at 50 Hz; $4\pi 10^{-4} f = \omega\mu_0/(2\pi)$ is the
  reactance per unit of the logarithm, 0.0628 Ω/km at 50 Hz.
- The core and its concentric screen link the same flux outside $r_s$, so
  the mutual impedance has the same logarithm as the screen loop and
  $Z_s' - Z_m' = R_s'$. The screen is treated as thin: its internal
  reactance is neglected and its transfer impedance is its DC resistance,
  which holds while the wire diameter is small against the skin depth
  (1.3 mm in copper at 2.5 kHz, against 0.75 mm wires here).
- 658 is the rounded constant; the exact low-frequency limit of the
  earth-return integral is $2e^{1/2-\gamma}/\sqrt{\omega\mu_0/\rho} = 658.87\sqrt{\rho/f}$ m
  ($\gamma$ Euler's constant). The closed form assumes homogeneous soil,
  neglects displacement currents and holds while the conductor depth and
  radius are small against $1/\lvert m\rvert$, $m = \sqrt{j\omega\mu_0/\rho}$.

For one line between ideally bonded stations the reduction factor is

$$
r = \left\lvert 1 - \frac{Z_m}{Z_s} \right\rvert = \frac{R_s'}{\lvert Z_s' \rvert},
$$

which is what `ResultReductionFactor.value` reproduces for a single branch.
The reduction factor of EN 50522 in a network, `value_current`, depends on the
electrodes as well; see [Compare results with EN 50522 limits](en50522-check.md).

## Phase impedance for rings and meshes

In a ring or mesh the source current divides over several routes, and
`phase_current_mode="auto"` computes that split on a network of phase
conductors. The impedance it needs is the loop of the faulted phase conductor
with the screen bonded at both ends:

$$
Z_{pp}' = R_c' + \pi^2\,10^{-4}\,f + j\,4\pi\,10^{-4}\,f\,\ln\frac{D_E}{\mathrm{GMR}_c},
\qquad
Z_\text{red}' = Z_{pp}' - \frac{Z_m'^2}{Z_s'}.
$$

Eliminating the screen current of a branch from its two loop equations leaves
exactly $Z_\text{red}$ in front of the phase current. It is neither the
positive-sequence impedance $R_1 + jX_1$ nor the conductor resistance; for a
branch without an earthing conductor it is $Z_{pp}$. The pandapower importer
can derive the formula from a line's `r_ohm_per_km`/`x_ohm_per_km`
(`ImportDefaults.derive_phase_impedance`): that is the positive-sequence
impedance, a proxy that splits a ring exactly only when all its routes have the
same construction. In a radial network the split is fixed by the topology and
the formula does not change the result; declare it anyway, so the type stays
correct when the network gets a second infeed. How the split and the coupling
are modelled is explained in [Concepts](../concepts.md#path-finding).

## Worked example: NA2XS(F)2Y 1×300 RM/25 12/20 kV

Data of the cable, ρ = 100 Ω·m, 50 Hz and the 5th harmonic (250 Hz):

| Quantity | Value | Source |
|---|---|---|
| conductor | Al, 300 mm², R<sub>c</sub>′ = 0.100 Ω/km at 20 °C | Prysmian data sheet [1], EN 60228 [2] |
| screen | Cu wires 25 mm², R<sub>s</sub>′ = 0.727 Ω/km at 20 °C | EN 60228 value for 25 mm² [2] <!-- CHECK: screen DC resistance 0.727 Ohm/km assumed equal to the EN 60228 class-2 value for 25 mm2; take the value of the manufacturer or the routine test certificate --> |
| insulation | 5.5 mm, diameter over insulation 32.9 mm | Prysmian data sheet [1], row 1x300RM/25 <!-- CHECK: two of three SAP rows give 32.9 mm, one 32.2 mm --> |
| outer diameter | 41.9 mm | Prysmian data sheet [1] |
| semiconductive layers, swelling tape, wire diameter | 0.6 mm, 0.4 mm, 0.75 mm | estimates <!-- CHECK: build-up between insulation and screen wires estimated; it sets r_s --> |
| mean screen radius r<sub>s</sub> | 32.9/2 + 0.6 + 0.4 + 0.75/2 = 17.825 mm | from the rows above |
| conductor radius, GMR | 32.9/2 − 5.5 − 0.6 = 10.35 mm; GMR = 8.06 mm | estimate, solid-conductor factor e<sup>−1/4</sup> <!-- CHECK: conductor diameter not on the data sheet; stranding factor of a compacted conductor --> |
| laying depth h | 0.7 m | rating conditions of the data sheet [1]; only used for method (b) |

The screen radius enters only through a logarithm: ±5 % in r<sub>s</sub> moves
r at 50 Hz by ±0.2 %. The screen resistance enters directly; at 20 °C it is
the lower bound, and a warm screen gives a higher r (see
[the three-phase system](#three-phase-system-the-other-two-screens)).

The data and the closed form, as the snippet writes them:

```python
--8<-- "docs/snippets/types_from_datasheets.py:datasheet"
```

```python
--8<-- "docs/snippets/types_from_datasheets.py:closed_form"
```

```python
--8<-- "docs/snippets/types_from_datasheets.py:types"
```

The type this builds, written out:

```python
import groundinsight as gi

cable = gi.BranchType(
    name="NA2XS(F)2Y 1x300RM/25 12/20 kV, own screen",
    grounding_conductor=True,
    self_impedance_formula=(
        "(0.727 + pi**2*1e-4*f + j*4*pi*1e-4*f*log(658*sqrt(rho/f)/0.017825))*l"
    ),
    mutual_impedance_formula=(
        "(pi**2*1e-4*f + j*4*pi*1e-4*f*log(658*sqrt(rho/f)/0.017825))*l"
    ),
    phase_impedance_formula=(
        "((0.1 + pi**2*1e-4*f + j*4*pi*1e-4*f*log(658*sqrt(rho/f)/0.00806059))"
        " - (pi**2*1e-4*f + j*4*pi*1e-4*f*log(658*sqrt(rho/f)/0.017825))**2"
        "/(0.727 + pi**2*1e-4*f + j*4*pi*1e-4*f*log(658*sqrt(rho/f)/0.017825)))*l"
    ),
    conductor_material="Cu",
    cross_section_mm2=25.0,
)
```

### Three ways to the same numbers

**(a) Closed form** — the formulas above, evaluated in plain Python.

**(b) Pollaczek's integral** [5] for a conductor buried at depth $h$ in
homogeneous soil, evaluated with `scipy.integrate.quad`:

$$
Z_E' = \frac{j\omega\mu_0}{2\pi}\left[K_0(m r) - K_0(2mh)
 + 2\int_0^\infty \frac{e^{-2h\sqrt{\lambda^2+m^2}}}{\lambda + \sqrt{\lambda^2+m^2}}\,d\lambda\right],
\qquad Z_m' = Z_E'(r_s),\quad Z_s' = R_s' + Z_E'(r_s).
$$

```python
--8<-- "docs/snippets/types_from_datasheets.py:pollaczek"
```

**(c) A one-branch network** — the type above between two stations, solved
with `run_fault`; `ResultReductionFactor.value` of a single branch is
$\lvert 1 - Z_m/Z_s\rvert$:

```python
--8<-- "docs/snippets/types_from_datasheets.py:network"
```

Result, impedances in Ω/km:

| f | method | Z<sub>s</sub>′ | Z<sub>m</sub>′ | Z<sub>red</sub>′ | r |
|---|---|---|---|---|---|
| 50 Hz | (a) closed form | 0.77635 + 0.68254j | 0.04935 + 0.68254j | 0.44301 + 0.38746j | 0.703286 |
| 50 Hz | (b) Pollaczek, h = 0.7 m | 0.77643 + 0.68254j | 0.04943 + 0.68254j | 0.44302 + 0.38742j | 0.703244 |
| 50 Hz | (c) groundinsight | 0.77635 + 0.68254j | 0.04935 + 0.68254j | 0.44301 + 0.38746j | 0.703286 |
| 250 Hz | (a) closed form | 0.97374 + 3.15988j | 0.24674 + 3.15988j | 0.77993 + 0.40208j | 0.219869 |
| 250 Hz | (b) Pollaczek, h = 0.7 m | 0.97465 + 3.15938j | 0.24765 + 3.15938j | 0.77988 + 0.40207j | 0.219883 |
| 250 Hz | (c) groundinsight | 0.97374 + 3.15988j | 0.24674 + 3.15988j | 0.77993 + 0.40208j | 0.219869 |

- **(a) and (c)** agree to rounding (about 1e-16): the model evaluates the
  same formula and reproduces the closed form of the reduction factor.
- **(a) against (b)** shows how good the $D_E$ approximation is. At 50 Hz the
  earth-return resistance differs by 0.17 %, the reactance by about 1e-6, and
  r by 4.2·10⁻⁵ (1.4·10⁻⁵ at 250 Hz). At 50 Hz most of that is the rounded
  constant: with 658.87 instead of 658 the difference in r drops to 4.8·10⁻⁶.
  The rest is the burial depth; it shrinks in proportion to $h$.
- Over 16.7 Hz … 2.5 kHz and 10 … 10 000 Ω·m (depth 0.7 m) the closed form stays
  within 1.5·10⁻⁴ of the integral in r and within 0.5 % in $\lvert Z_s'\rvert$
  and $\lvert Z_m'\rvert$; the largest deviation in r on a 40 × 40 grid of that
  range is 1.3·10⁻⁴, near 400 Hz and 10 Ω·m. The integral itself is checked
  against an independent quadrature (mpmath) to 1e-9 and against its analytic
  low-frequency limit.

## Three-phase system: the other two screens

A three-phase cable system of single-core cables has three screens. With all
of them bonded at both ends, the faulted core drives current into all three,
and the three screens return it in parallel. `groundinsight` has no coupling
between the screens of one branch, so the system is one branch whose type
describes the **screen bundle**: the screens in parallel, seen from the
faulted core. Eliminating the individual screen currents gives

$$
Z_{s,\text{eq}} = \frac{1}{\mathbf 1^T Z_{ss}^{-1}\mathbf 1},\qquad
Z_{m,\text{eq}} = Z_{s,\text{eq}}\;\mathbf 1^T Z_{ss}^{-1} z_c ,
$$

with $Z_{ss}$ the 3×3 matrix of screen loop and screen–screen impedances and
$z_c$ the couplings of the faulted core to each screen. In **trefoil** every
row of $Z_{ss}$ has the same sum, and the result is exactly the bundle formula:
resistance $R_s'/3$, and $\mathrm{GMR} = (r_s s^2)^{1/3}$ in both logarithms
($s$ = axis distance = outer diameter for touching cables), so again
$Z_{s,\text{eq}} - Z_{m,\text{eq}} = R_s'/3$.

```python
--8<-- "docs/snippets/types_from_datasheets.py:trefoil"
```

```python
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
    conductor_material="Cu",
    cross_section_mm2=75.0,
)
```

| f | r, own screen only | r, three screens in trefoil (bundle formula = matrix reduction = groundinsight) |
|---|---|---|
| 50 Hz | 0.703286 | 0.341570 |
| 250 Hz | 0.219869 | 0.080223 |

The other two screens halve the reduction factor at 50 Hz. Which type is right
depends on how the screens are bonded: all three at both ends — the usual case —
is the bundle; a cable system whose screens are bonded at one end only, or
cross-bonded, needs its own derivation.

- **Flat formation.** The rows of $Z_{ss}$ differ, and the bundle formula with
  geometric mean distances is an approximation: for touching cables it is off
  by 0.1 … 0.2 % in r at 50 Hz and by 2 … 5 % at 250 Hz (outer and middle
  phase faulted). Compute the bundle with the matrix reduction
  (`bundle_equivalent` in the snippet) at the study frequencies and check the
  formula against it.
- **Screen temperature.** r grows with the screen resistance. With the
  screens at 70 °C instead of 20 °C (factor 304.5/254.5 for copper) r of the
  trefoil system at 50 Hz rises from 0.342 to 0.397. The 20 °C value is the
  lower bound and gives the lower earth potential rise.
  <!-- CHECK: which screen temperature to assume for an earth-fault study (20 °C value of the data sheet vs. operating temperature) -->

## Overhead line with earth wire

For a line whose earth wire is bonded to the towers and the stations, the
earth wire takes the place of the screen. With the earth wire's resistance
$R_{ew}'$ and geometric mean radius, and the distance $d$ between the faulted
phase conductor and the earth wire:

$$
Z_s' = R_{ew}' + \pi^2 10^{-4} f + j\,4\pi 10^{-4} f \ln\frac{D_E}{\mathrm{GMR}_{ew}},
\qquad
Z_m' = \pi^2 10^{-4} f + j\,4\pi 10^{-4} f \ln\frac{D_E}{d}.
$$

When the faulted phase is not known, or the line is transposed, use for $d$ the
geometric mean of the distances of the three phases to the earth wire; two
earth wires form a bundle like the screens above. Model each span as a branch
and each tower as a bus whose electrode is the tower footing.

Example: phase conductor 94-AL1/15-ST1A (Al/St 95/15, 0.3060 Ω/km, Ø 13.60 mm),
earth wire 47-AL1/8-ST1A (0.6129 Ω/km, Ø 9.45 mm) [4], earth wire 2 m above the
faulted phase at 10 m. <!-- CHECK: line geometry (heights 10 m / 12 m, earth wire directly above the faulted phase) and the solid-conductor GMR factor for stranded Al/St conductors are estimates -->

```python
--8<-- "docs/snippets/types_from_datasheets.py:overhead"
```

```python
overhead_line = gi.BranchType(
    name="20 kV OHL 94-AL1/15-ST1A with earth wire 47-AL1/8-ST1A",
    grounding_conductor=True,
    self_impedance_formula=(
        "(0.6129 + pi**2*1e-4*f + j*4*pi*1e-4*f*log(658*sqrt(rho/f)/0.00367983))*l"
    ),
    mutual_impedance_formula=(
        "(pi**2*1e-4*f + j*4*pi*1e-4*f*log(658*sqrt(rho/f)/2.0))*l"
    ),
    phase_impedance_formula=(
        "((0.306 + pi**2*1e-4*f + j*4*pi*1e-4*f*log(658*sqrt(rho/f)/0.00529585))"
        " - (pi**2*1e-4*f + j*4*pi*1e-4*f*log(658*sqrt(rho/f)/2.0))**2"
        "/(0.6129 + pi**2*1e-4*f + j*4*pi*1e-4*f*log(658*sqrt(rho/f)/0.00367983)))*l"
    ),
)
```

| f | r (a) closed form | r (b) Carson's integral | r (c) groundinsight |
|---|---|---|---|
| 50 Hz | 0.712107 | 0.711893 | 0.712107 |
| 250 Hz | 0.551584 | 0.550026 | 0.551584 |

Method (b) is Carson's integral for conductors above ground. Overhead
conductors sit much higher than a buried cable, so the closed form loses
accuracy sooner: 2·10⁻⁴ in r at 50 Hz, 1.6·10⁻³ at 250 Hz, and up to 2·10⁻²
at 2.5 kHz in 10 Ω·m soil.

## Electrode impedance of a station

A `BusType` needs the impedance of the station's electrode to remote earth,
`Z_B` — not the earthing impedance of the network at that station (see the
[glossary](../reference/glossary.md)). Two sources:

- **A measurement.** An earthing-impedance measurement at a station whose
  cable screens are connected measures the network's driving-point impedance,
  not `Z_B`. Measure with the screens and earth wires disconnected, or derive
  `Z_B` from the measured value and the network. [groundmeas](https://ce1ectric.github.io/groundmeas/)
  manages and evaluates such measurements, including impedance against
  frequency and soil resistivity.
- **A standard formula** for the electrode geometry. The common ones and two
  published variants each, at ρ = 100 Ω·m:

| Electrode | Formula in the `BusType` (source) | Value | Second formula (source) | Value |
|---|---|---|---|---|
| rod, L = 3 m, Ø 20 mm | ρ/(2πL)·(ln(4L/a) − 1), a = radius (Dwight [6]) | 32.31 Ω | ρ/L (rough rule [7]) | 33.33 Ω |
| ring around 10 m × 10 m, D = 1.13·√A | 2ρ/(3D) (rough rule [7], [8]) | 5.90 Ω | ρ/(2π²D)·(ln(8D/d) + ln(4D/s)), d = 10 mm, s = 2·0.5 m (Dwight [6]) | 5.79 Ω |
| mesh 40 m × 40 m, 400 m conductor, 0.8 m deep | ρ·(1/L<sub>T</sub> + (1 + 1/(1 + h·√(20/A)))/√(20A)) (Sverak, IEEE 80 [9]) | 1.32 Ω | ρ/4·√(π/A) + ρ/L<sub>T</sub> (Laurent–Niemann, IEEE 80 [9]) | 1.36 Ω |

<!-- CHECK: electrode formulas transcribed from Dwight (IEEE Std 142 Table 13), DEHN Table 5.5.1 and IEEE 80-2013 clause 14.2; confirm against EN 50522:2022 Annex J.2 and the editions you cite -->

```python
--8<-- "docs/snippets/types_from_datasheets.py:electrodes"
```

The two variants agree within 2 … 4 %; the soil model behind them — homogeneous
ρ — is the larger uncertainty. **Frequency dependence is a modelling choice.**
The formulas give a resistance, the same at every frequency. A measured
impedance with an inductive part is written so that the reactance scales with
frequency (`0.42 + j*0.08*f/50` above); that form also tends to the resistance
at 0 Hz by itself.

## At 0 Hz

DC studies and every FFT transient study evaluate the formulas at 0 Hz, where
`f*log(658*sqrt(rho/f)/r)` is 0·∞. The evaluator approaches 0 Hz numerically
([formula syntax](../reference/formula-syntax.md#direct-current-f-0)); for the
types on this page it returns:

| Formula | Value at 0 Hz | Message |
|---|---|---|
| `self_impedance_formula` | R<sub>s</sub>′·l, the DC resistance of the screen | none |
| `phase_impedance_formula` | R<sub>c</sub>′·l, the DC resistance of the conductor | none |
| `mutual_impedance_formula` | about π²·10⁻¹²·l Ω — zero for every practical purpose, r = 1 | a `DCLimitWarning` "converges only slowly towards 0 Hz" for branches longer than about 11 m |

The mutual impedance tends to zero, and its real part $\pi^2 10^{-4} f$ has no
constant term; the evaluator's convergence test compares the last step with an
absolute floor of 10⁻¹² Ω and reports it. For these formulas the warning is
expected and does not affect the result. The electrode formulas contain no `f`
and are unchanged at 0 Hz.

## Check a new type

- Run a one-branch network and compare `value` with $\lvert 1 - Z_m/Z_s\rvert$
  of the closed form, as method (c) does.
- Compare r at 50 Hz with the typical values that EN 50522:2022 lists for
  cables and lines (Annex I).
- Evaluate the formulas at the lowest ρ, the lowest frequency and the shortest
  length of the model: a negative real part or a NaN is refused
  ([formula syntax](../reference/formula-syntax.md#common-errors)).

## References

1. Prysmian, data sheet "NA2XS(F)2Y 12/20KV CPR F", document code
   RO00_NA2XS_F_2Y20KVCPRF_07/11/2026, table of constructions, rows 1x300RM/25.
   <https://datasheet.prysmiangroup.com/pdf/datasheet/en/322930>
2. EN 60228 / IEC 60228:2004, *Conductors of insulated cables*; values of the
   DC resistance at 20 °C as tabulated by Bayka, "Conductor resistance".
   <https://www.bayka.de/en/service/technical-information/conductor-resistance>
3. J. R. Carson, "Wave propagation in overhead wires with ground return",
   *Bell System Technical Journal*, vol. 5, no. 4, pp. 539–554, 1926.
4. EN 50182:2001, *Conductors for overhead lines — Round wire concentric lay
   stranded conductors*; values as tabulated by Lumino Industries, "EN 50182
   Type A1/S1A".
   <https://luminoindustries.com/wp-content/uploads/2024/04/EN-50182-2001-TYPE-A1-ST1A.pdf>
5. F. Pollaczek, "Über das Feld einer unendlich langen wechselstromdurchflossenen
   Einfachleitung", *Elektrische Nachrichten-Technik*, vol. 3, no. 9,
   pp. 339–359, 1926; A. Ametani, "A general formulation of impedance and
   admittance of cables", *IEEE Transactions on Power Apparatus and Systems*,
   vol. PAS-99, no. 3, pp. 902–910, 1980, doi:10.1109/TPAS.1980.319718.
6. H. B. Dwight, "Calculation of resistances to ground", *Transactions of the
   AIEE*, vol. 55, no. 12, pp. 1319–1328, 1936, doi:10.1109/T-AIEE.1936.5057209;
   reproduced in IEEE Std 142 (Green Book), table "Formulas for the calculation
   of resistances to ground".
7. DEHN, *Lightning Protection Guide*, 3rd ed., 2014, Table 5.5.1.
   <https://resources.ipd.com.au/Flipbook/35_DS702_E_2014_Lightning_Protection_Guide/files/basic-html/page124.html>
8. EN 50522:2022, *Earthing of power installations exceeding 1 kV a.c.*,
   Annex J (informative) "Basis for the design of earthing systems", J.2
   "Resistance to earth"; Annex I (informative) "Reduction factors related to
   earth wires of overhead lines and metal sheaths of underground cables";
   Annex L.1, soil resistivity measurement.
9. IEEE Std 80-2013, *IEEE Guide for Safety in AC Substation Grounding*,
   clause 14.2.

# Compare results with EN 50522 limits

EN 50522 requires that the touch voltages of an installation stay within
permissible values that depend on the fault duration. This page shows which
`groundinsight` result feeds which quantity of that verification, how the
procedure runs, and which parts of it the model does not cover. It refers to
the clauses of EN 50522:2022 by number and name and does not reproduce the
permissible values: take them from the edition you apply.

!!! warning "Scope of the results"

    groundinsight is a research and engineering tool. Its results support, but
    do not replace, an assessment according to EN 50522 / IEC 61936-1 by a
    qualified person, including the measurements the standards require. The
    software is provided under the MIT License, without warranty of any kind.

## What the model gives you

Per fault and per frequency, `groundinsight` computes the fault current, the
earth potential rise of every bus, the shield currents, the earth current and
the earthing impedance at the fault location, and the reduction factor of
EN 50522. The network model has no soil surface; touch voltages and
transferred potentials come from an earthing characteristic on the bus — a
table computed by a field solver such as groundfield, with the touch factor
`k_touch` and the transfer factor `k_transfer`
([how-to](groundfield-characteristics.md),
[explanation](../explanation/touch-and-transfer.md)). With it the result
carries, per bus, the prospective touch voltage `U_vT = k_touch · |U_E|` and
the largest transferred potential `U_transfer = k_transfer · |U_E|`; without
it, neither. The package does **not** compute:

- step voltages and surface potentials themselves — only the factors of the
  characteristic reach the network;
- the coupling of neighbouring electrodes through the soil, which raises
  their potentials and lowers their touch factors; characterise closely
  spaced electrodes as one system
  ([explanation](../explanation/touch-and-transfer.md#electrodes-that-see-each-other));
- additional resistances such as footwear or a surface layer, which the
  prospective touch voltage of the standard takes into account (Annex A);
- the permissible touch voltage itself;
- the dimensioning for corrosion and mechanical strength (5.2); the thermal
  check of earthing conductors is available separately
  (`check_conductor_limits`, `check_node_limits`, see [Analysis](../api/analysis.md)).

## Which result is which symbol

| EN 50522 | Meaning | groundinsight | In the tables |
|---|---|---|---|
| `3I_0` | earth-fault current at the fault location | `ResultReductionFactor.i_fault` — the source currents that reach the fault, times the fault scalings | `I_F_A` of `res_fault()` |
| `r` | reduction factor | `ResultReductionFactor.value` | `r` of `res_fault()`; `reduction_factor` of `res_all_impedances()` |
| `I_E` | earth current, `r·3I_0` = `U_E/Z_E` | `ResultReductionFactor.i_earth` | `I_E_A` of `res_fault()` |
| `Z_E` | earthing impedance | `ResultReductionFactor.z_earthing`, equal to `ResultGroundingImpedance.value` | `Z_E_Ohm`, `Z_E_degree` of `res_fault()`; `grounding_impedance_Ohm` of `res_all_impedances()` |
| `U_E` | earth potential rise | `ResultReductionFactor.u_earthing`, equal to the `uepr_freq` of the fault bus | `U_E_V` of `res_fault()`; `EPR_V` of the fault bus in `res_buses()` |
| `U_vT` | prospective touch voltage | `ResultBus.u_touch` (RMS) and `u_touch_freq`, for a bus whose earthing characteristic has `k_touch` | `U_vT_V` of `res_buses()`; `U_vT_max_V`, `U_vT_max_bus` of `res_fault()` |
| `U_T` | touch voltage, with body and additional resistances | not computed | – |
| – | transferred potential (Clause 6) | `ResultBus.u_transfer`, for a bus whose earthing characteristic has `k_transfer` | `U_transfer_V` of `res_buses()`; `U_transfer_max_V` of `res_fault()` |
| `U_Tp` | permissible touch voltage | not part of the package | EN 50522:2022, 5.4.1 and Annexes A, B |
| `t_F` | fault duration | not used by the solve | from the protection |

- `r` is the current an ideal electrode at the fault bus would take, over
  `3I_0` (Norton's theorem). With it the chain `U_E = r·3I_0·Z_E` holds
  exactly; `r` does not depend on the electrode of the fault station
  ([Reduction factors and earthing impedance](../explanation/reduction-factors.md#reduction-factor)).
- `Z_E` is the driving-point impedance of the whole earthing network at the
  fault bus, the stations bonded through screens and earth wires included —
  what an earthing-impedance measurement at the station sees. It is not the
  electrode of the station, `Z_B` ([glossary](../reference/glossary.md)).
- Complex values are in the package's sign frame
  ([conventions](../reference/conventions.md#signs-and-phase-angles)); the
  verification uses magnitudes.
- `Fault.t_k_s` is the short-circuit duration `T_k` of IEC 60909-0 for the
  thermal checks. The fault duration `t_F` of the touch-voltage verification
  comes from the same protection, but the solve does not use it.

## The procedure

1. **Fault current and fault duration.** Get `3I_0` at the fault location —
   from the network operator, or from a short-circuit calculation after
   IEC 60909-0 (a pandapower case can be imported, see [I/O](../api/io.md)) —
   per frequency as RMS values, and the fault duration `t_F` from the
   protection settings.
2. **Model.** Build the network with branch types from data sheets
   ([how-to](types-from-datasheets.md)) and the electrode impedances `Z_B` of
   the stations. Put the fault current at the infeed as `Source.values`, the
   fault at the station under study.
3. **Solve and read** `U_E`, `Z_E`, `I_E` and `r` per frequency, and `U_E` as
   the total RMS over the frequencies (see [Harmonics and DC](#harmonics-and-dc)):
   `net.res_fault(fault)` reports all of them in one frame
   ([below](#the-key-figures-in-one-frame)).
4. **Permissible touch voltage.** Read `U_Tp(t_F)` from EN 50522:2022, 5.4.1;
   Annexes A and B derive it and the prospective permissible touch voltage.
5. **Design procedure.** Apply the procedure of 5.4.3 (the flow chart of the
   earthing-system design, Figure 9): it compares `U_E` with `U_Tp`, taking the
   recognised measures `M` of Annex E into account, and tells you when the
   touch voltages themselves have to be determined — by measurement
   (Annex H), or by a calculation of surface potentials. With an earthing
   characteristic on the station bus, `gi.check_touch_voltages(net, fault,
   u_limit)` compares the prospective touch voltage `U_vT` with the
   permissible value you pass (a number, or the curve `U(t_F)` as points)
   and reports the utilisation per bus. An installation that is part of a
   global earthing system follows its own branch of the procedure (Annex N).
6. **Measure.** Clause 8 and Annex L describe the measurements of the earthing
   impedance (L.2), the earth potential rise (L.3) and touch voltages (L.4).
   Compare them with the model; the model does not replace them.
7. **Document** the assessment (Clause 10).

## Worked example

A 20 kV feeder: substation `UW` (0.5 Ω) and five stations with ring
electrodes, bonded through the screens of the three-phase cable system of the
[data-sheet how-to](types-from-datasheets.md#three-phase-system-the-other-two-screens)
in sections of 1.2 km, ρ = 100 Ω·m. Fault at station `S3`, with 1000 A at
50 Hz and 50 A at 250 Hz from the infeed (illustrative values).

```python
--8<-- "docs/snippets/en50522_check.py:network"
```

```python
--8<-- "docs/snippets/en50522_check.py:quantities"
```

```python
--8<-- "docs/snippets/en50522_check.py:criterion"
```

Output of [`en50522_check.py`](../snippets/en50522_check.py):

| f | 3I<sub>0</sub> | r | I<sub>E</sub> | \|Z<sub>E</sub>\| | \|U<sub>E</sub>\| | r·3I<sub>0</sub>·\|Z<sub>E</sub>\| |
|---|---|---|---|---|---|---|
| 50 Hz | 1000.0 A | 0.3234 | 323.4 A | 1.189 Ω | 384.4 V | 384.4 V |
| 250 Hz | 50.0 A | 0.0815 | 4.1 A | 2.232 Ω | 9.10 V | 9.10 V |

`U_E` as total RMS: 384.6 V — the value to compare with `U_Tp(t_F)`.

`|U_E|` is the earth potential rise of `S3` in `res_buses()`, and `|Z_E|` is
the driving-point impedance an independent nodal solve of the same electrodes
and screens gives; the test suite checks both.

### The key figures in one frame

The script reads the quantities from the result objects to show where each one
lives. `net.res_fault(fault)` puts them side by side in one frame — one row per
frequency and an RMS row, read from the stored result without solving again:
`I_F_A` is `3I_0`, `r` is `r`, `I_E_A` is `I_E`, `Z_E_Ohm` (with
`Z_E_degree`) is `Z_E` and `U_E_V` is `U_E`
([Result tables](../reference/results.md#networkres_faultfault)).

```python
--8<-- "docs/snippets/en50522_check.py:key_figures"
```

```text title="Output of en50522_check.py, last lines"
res_fault    I_F_A         r   I_E_A  Z_E_Ohm    U_E_V
    50 Hz   1000.0    0.3234   323.4    1.189   384.45
   250 Hz     50.0    0.0815     4.1    2.232     9.10
      RMS   1001.2         -   323.4        -   384.56
```

On the RMS row the ratio and the impedance are empty (`null`); `U_E_V` there is
the total RMS of `U_E` over the frequencies, the value to compare with
`U_Tp(t_F)`. The test suite recomputes every figure of the frame with an
independent nodal solver.

The chain $U_E = r \cdot 3I_0 \cdot Z_E$ of the standard holds exactly in the
last column, on this short feeder as well: $r$ is defined as the current into
an ideal electrode at the fault, and by Norton's theorem that current times
$Z_E$ is $U_E$. What the chain does not give is a shortcut: $r$ here, 0.3234,
is not the cable's value of a long run, because it contains the earthing of
the neighbouring stations and of the infeed. Compare `U_E` itself with the
permissible value.

## Harmonics and DC

The permissible touch voltages of EN 50522 are given for the power frequency;
the standard applies to installations with nominal frequencies up to 60 Hz.
For a fault current with harmonics or a DC component, compare the **total RMS
of `U_E` over all frequencies** — 384.6 V above — with the curve `U_Tp(t_F)`.
For the body current this is a conservative choice: IEC 60479-2:2019
describes the effect of a current at a higher frequency by a frequency factor,
the ratio of the threshold current at that frequency to the threshold at
50/60 Hz, and the thresholds of perception, let-go and ventricular
fibrillation rise with frequency. Counting every component with its full RMS
value against the 50/60 Hz curve therefore overstates the effect of the
harmonics on the body current. The impedance of the skin, however, falls with
frequency (DIN IEC/TS 60479-1 (VDE V 0140-479-1):2007-05, 4.2), which works
the other way; whether the total RMS is conservative in terms of the touch
voltage as well is not shown here.

A DC component enters the total RMS with its full magnitude. IEC 60479-1:2018
treats the effects of direct current in a clause of its own; for the fault
durations of interest its thresholds lie above the 50/60 Hz thresholds, so
counting a DC component against the 50/60 Hz curve overstates its effect on
the body current as well.

## EPR is an upper bound of the touch voltage

The touch voltage is the part of `U_E` that a person bridges between a touched
part and the point where the feet stand. For a person standing in the
potential funnel of the same earthing system, the surface potential there lies
between remote earth and `U_E`, so the touch voltage cannot exceed `U_E`. A
result `U_E ≤ U_Tp` therefore covers the touch voltage; `U_E > U_Tp` does not
mean that the touch voltage is too high, only that it has to be determined —
for instance from an earthing characteristic, whose `k_touch ≤ 1` gives
`U_vT ≤ U_E`. Potentials transferred from other installations (Clause 6) are
outside this bound.

## References

1. EN 50522:2022, *Earthing of power installations exceeding 1 kV a.c.*, with
   amendment EN 50522:2022/A1:2024 — clause names as listed in the national
   edition STN EN 50522:2022, <https://normy.normoff.gov.sk/norma/134904/nahlad>.
2. IEC 61936-1:2021, *Power installations exceeding 1 kV AC and 1,5 kV DC —
   Part 1: AC*, edition 3.0, Clause 10 "Earthing systems".
3. IEC 60909-0:2016, *Short-circuit currents in three-phase a.c. systems —
   Part 0: Calculation of currents*.
4. IEC 60479-1:2018, *Effects of current on human beings and livestock —
   Part 1: General aspects*.
5. IEC 60479-2:2019, *Effects of current on human beings and livestock —
   Part 2: Special aspects*, Clause 3.1 (frequency factor) and Clause 4
   (alternating currents above 100 Hz); preview:
   <https://cdn.standards.iteh.ai/samples/102313/18ea5a5451cf4f3bad4023ded332d0be/IEC-60479-2-2019.pdf>.
6. DIN IEC/TS 60479-1 (VDE V 0140-479-1):2007-05, *Effects of current on
   human beings and livestock — Part 1: General aspects* (IEC/TS 60479-1:2005
   + Corrigendum October 2006), 4.2 (impedance of the skin).

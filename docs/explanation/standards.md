# Standards and scope

`groundinsight` computes the quantities that an earthing study according to the
standards needs. It does not carry out the assessment a standard prescribes.
This page lists, standard by standard, what the package implements and what it
leaves to the engineer.

!!! warning "Scope of the results"

    groundinsight is a research and engineering tool. Its results support, but
    do not replace, an assessment according to EN 50522 / IEC 61936-1 by a
    qualified person, including the measurements the standards require. The
    software is provided under the MIT License, without warranty of any kind.

## EN 50522:2022 — earthing of installations above 1 kV AC

The European standard for the design and erection of earthing systems of
installations above 1 kV with nominal frequencies up to 60 Hz; the current
edition carries the amendment A1:2024.

**Implemented**

- The quantities of the touch-voltage verification at the fault location:
  earth-fault current `3I_0`, reduction factor `r`, earth current `I_E`,
  earthing impedance `Z_E` and earth potential rise `U_E`
  ([how-to](../howto/en50522-check.md)).
- Reduction factors of cable screens and earth wires from their impedances,
  for any topology — the standard's informative Annex I gives typical
  values at 50 Hz.
- Earth potential rise of every bus, for the transfer of potentials between
  bonded stations.
- The final temperatures of uninsulated earthing conductors and electrodes
  used by the thermal checks (`FINAL_TEMPERATURES`, EN 50522:2022 Table 2).

**Not implemented**

- The permissible touch voltage `U_Tp(t_F)` (5.4.1, Annexes A and B) and the
  decision of the design procedure (5.4.3) with the measures `M` (Annex E).
- Touch and step voltages, surface potentials, additional resistances.
- Transferred potentials to low-voltage and telecommunication systems
  (Clause 6), dimensioning for corrosion and mechanical strength (5.2), the
  construction rules (Clause 7).
- The measurements (Clause 8, 9.2, Annexes H and L) — the model needs them to
  be validated, it does not replace them.

## IEC 61936-1:2021 — power installations above 1 kV AC

The international standard for the design and erection of AC power
installations; its Clause 10 "Earthing systems" sets the earthing requirements
that EN 50522 details for Europe. The same split applies: `groundinsight`
supplies `U_E`, `Z_E`, `I_E` and `r`, the assessment is the engineer's.

## IEC 60909-0:2016 — short-circuit currents

**Implemented**: the factors that turn a symmetrical short-circuit current into
the peak and thermal quantities — the peak factor κ from R/X
(`kappa_from_r_to_x`), the peak current, the DC heat factor `m`
(`iec60909_m`) and the thermally equivalent short-time current
`I_th = I_k·√(m + n)`. They are applied to the current that the linear solve
superposes, not to the source currents one by one.

**Not implemented**: the short-circuit calculation of the network itself. The
fault current is an input — from the network operator, or from a pandapower
`calc_sc` run imported with `read_shortcircuit_results` /
`apply_shortcircuit_characteristics` ([I/O](../api/io.md)).

## IEC 60949:1988 — thermally permissible short-circuit currents

**Implemented**: the adiabatic rating `I_adm = k·S/√t_k` of an earthing
conductor or electrode, with `k = K·√ln((θ_f + β)/(θ_i + β))` and the material
constants of the standard (`IEC60949_MATERIALS`), in `check_conductor_limits`
and `check_node_limits` ([Analysis](../api/analysis.md)).

**Not implemented**: the non-adiabatic factor of IEC 60949 (heat given off
during the fault). Leaving it out underestimates the permissible current, which
is on the safe side.

## IEC 60479-1:2018 and IEC 60479-2:2019 — effects of current on the body

Not implemented. They are the physiological basis of the permissible touch
voltages of EN 50522, and the documentation uses IEC 60479-2 for one argument:
its frequency factors describe thresholds that rise with frequency, which is
why comparing the total RMS earth potential rise with the 50 Hz curve is
conservative for the body current. The impedance of the skin falls with
frequency (DIN IEC/TS 60479-1:2007-05, 4.2), so for the touch voltage this is
not shown ([how-to](../howto/en50522-check.md#harmonics-and-dc)).

## Model assumptions behind every result

The results rest on the model described in [Model and assumptions](theory.md) and
[Verification](verification.md): a quasi-stationary phasor solve per frequency
(transient solvers aside), lumped electrodes without resistive coupling
between them, homogeneous soil per element through `rho`, the coupling of the
faulted phase to the earthing conductors as Norton sources, and the formulas
the user supplies. An assessment has to check that these assumptions fit the
installation.

## References

1. EN 50522:2022, *Earthing of power installations exceeding 1 kV a.c.*, and
   EN 50522:2022/A1:2024; table of contents of the national edition STN EN
   50522:2022: <https://normy.normoff.gov.sk/norma/134904/nahlad>.
2. IEC 61936-1:2021, *Power installations exceeding 1 kV AC and 1,5 kV DC —
   Part 1: AC*, edition 3.0.
3. IEC 60909-0:2016, *Short-circuit currents in three-phase a.c. systems —
   Part 0: Calculation of currents*.
4. IEC 60949:1988 + A1:2008, *Calculation of thermally permissible
   short-circuit currents, taking into account non-adiabatic heating effects*.
5. IEC 60479-1:2018, *Effects of current on human beings and livestock — Part 1:
   General aspects*.
6. IEC 60479-2:2019, *Effects of current on human beings and livestock — Part 2:
   Special aspects*.
7. DIN IEC/TS 60479-1 (VDE V 0140-479-1):2007-05, *Effects of current on
   human beings and livestock — Part 1: General aspects* (IEC/TS 60479-1:2005
   + Corrigendum October 2006).

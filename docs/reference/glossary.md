# Glossary and symbols

One line per symbol or term, with the field or column where `groundinsight`
reports it. The symbols follow EN 50522 where the standard has one.

!!! note "`Z_B` and `Z_E` are two different impedances"

    The impedance of a single bus electrode to remote earth is `Z_B`, never
    `Z_E`. `Z_E` is the earthing impedance of EN 50522: the driving-point
    impedance of the whole earthing network at the fault location, the
    electrodes bonded through cable screens and earth wires included. In a
    cable network it is well below the electrode of the faulted station.

## Quantities at the fault

| Symbol or term | Meaning | In groundinsight |
|---|---|---|
| EPR | earth potential rise: the potential of an earthing system against remote earth | `ResultBus.uepr`, `uepr_freq`; column `EPR_V` of `res_buses()` |
| `U_E` | earth potential rise of the earthing system at the fault location (EN 50522) | `ResultReductionFactor.u_earthing`, equal to the `uepr_freq` of the fault bus |
| `Z_E` | earthing impedance of EN 50522: the driving-point impedance of the earthing network at the fault bus (electrodes and shields, without coupling and without Thévenin loops) | `ResultReductionFactor.z_earthing`, `ResultGroundingImpedance.value` |
| `Z_G` | the name of `Z_E` in the result tables (grounding impedance) | columns `grounding_impedance_Ohm`, `grounding_impedance_deg` of `res_all_impedances()` |
| `Z_dp` | driving-point impedance: the voltage at a bus per ampere injected there against remote earth | `Z_E` is `Z_dp` at the fault bus; `gi.bus_response`, `gi.analyze_cuts` |
| `I_F`, `3I_0` | fault current, the earth-fault current `3I_0` of EN 50522: the scaled source currents that reach the fault | `ResultReductionFactor.i_fault` |
| `I_E` | earth current: the part of the fault current that returns through the soil, summed over the electrodes of the earth group | `ResultReductionFactor.i_earth` |
| earth group | the buses whose electrodes feed the soil, from the fault outwards up to where the potential profile turns | `ResultReductionFactor.earth_buses` |
| `r` | reduction factor of EN 50522, the earth current over the fault current | `ResultReductionFactor.value_current`; column `reduction_factor_current` |
| `value` | EPR ratio at the fault bus with and without the inductive coupling; for one ideally bonded line the magnitude of `1 − k` | `ResultReductionFactor.value`; column `reduction_factor` |
| `U_T`, `U_vT` | touch voltage and prospective touch voltage | not computed; `U_E` is their upper bound |
| `U_Tp` | permissible touch voltage of EN 50522, a function of the fault duration | not part of the package |
| `t_F` | fault duration, set by the protection; the argument of `U_Tp` | not used by the stationary solve |
| `T_k` | short-circuit duration of IEC 60909-0, used by the thermal checks | `Fault.t_k_s` |

## Impedances of the model

| Symbol or term | Meaning | In groundinsight |
|---|---|---|
| `Z_B` | impedance of the electrode of one bus (*Erder*) to remote earth | `BusType.impedance_formula`, `Bus.impedance` |
| `Z_s` | self impedance of the shield–earth loop of a branch: cable screen or earth wire with earth return | `BranchType.self_impedance_formula`, `Branch.self_impedance` |
| `Z_m` | mutual impedance between the loop of the faulted phase and the shield–earth loop, both with earth return | `BranchType.mutual_impedance_formula`, `Branch.mutual_impedance` |
| `k` | coupling ratio `Z_m/Z_s`; the shield carries `k` times the phase current when both ends are ideally bonded | – |
| `Z_pp` | self impedance of the phase–earth loop | – |
| `Z_red` | phase–earth loop impedance with the shield bonded at both ends, `Z_pp − Z_m²/Z_s`; what the phase-current split needs | `BranchType.phase_impedance_formula`, `Branch.phase_impedance` |
| `D_E` | equivalent earth-return depth of Carson's formula, `658·√(ρ/f)` m | the Carson formulas of the how-to on data sheets |
| `GMR` | geometric mean radius of a conductor or bundle | the radius in a Carson formula |
| ρ (`rho`) | specific earth resistance of the soil | `specific_earth_resistance` of buses and branches; formula symbol `rho` |

## Conductors and terms

| Symbol or term | Meaning | In groundinsight |
|---|---|---|
| earthing conductor | the lumped connection (*Erdungsleiter*) that carries the current between the installation and the earthing system | `ResultBus.i_inj`; columns `I_inj_A`, `I_inj_degree` |
| earth electrode | the part in the soil (*Erder*); carries the current that leaves into the soil at this bus | `ResultBus.ia`; columns `I_bus_A`, `I_bus_degree` |
| shield, screen | metallic screen of a cable, the earthing conductor of a cable branch | a branch with `grounding_conductor=True` |
| earth wire | conductor of an overhead line that bonds the towers to the stations | a branch with `grounding_conductor=True` |
| RMS | root mean square; every phasor of the stationary model is an RMS phasor, and the RMS rows combine the frequencies as the root of the summed squares | column `is_rms` |
| grounding, earthing | the same thing; result names use "grounding", the EN 50522 terms "earthing" | – |

## See also

- [Conventions and units](conventions.md)
- [Bus and branch types from data sheets](../howto/types-from-datasheets.md)

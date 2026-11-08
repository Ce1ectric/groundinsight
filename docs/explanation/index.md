# Understanding the model

The pages of this section explain *why* `groundinsight` returns the numbers it
returns: the model, its assumptions and their limits, and the reasoning
behind each derived quantity. They are meant to be read, not looked up; the
[Reference](../reference/conventions.md) section holds the tables for looking
things up, and the [How-to guides](../howto/types-from-datasheets.md) the
procedures.

| Page | What it explains |
|---|---|
| [Model and assumptions](theory.md) | the network model, the nodal solve, the derived quantities, and the assumptions every result rests on |
| [Impedance formulas and where they come from](formulas.md) | what a formula means to the model, which values it may produce, how it is evaluated |
| [Phase currents in rings and meshes](phase-currents.md) | how the fault current divides between routes, what `phase_impedance_formula` must describe, when the one-way coupling is exact |
| [Reduction factors and earthing impedance](reduction-factors.md) | the EPR ratio and the reduction factor of EN 50522, $U_E$, $Z_E$ and $I_E$ at the fault bus |
| [Splitting the network, location response](decomposition.md) | what each direction contributes at the fault, and the response to any electrode at a bus |
| [Direct current (`f = 0`)](dc.md) | what a formula means at zero frequency, the stand-in for ideal bonds, what a DC study needs |
| [Transient simulation](../transient.md) | the FFT and state-space solvers for time-domain studies |
| [Verification and validation](verification.md) | what the results have been checked against, and what not |
| [Standards and scope](standards.md) | what the package implements of EN 50522 and the related standards |

## Where the sections of the former Concepts page went

These pages replace the single page *Concepts*. Its address forwards here,
and each of its section links lands on the matching row below.

| Section of *Concepts* | Now on |
|---|---|
| [Problem statement](theory.md#problem-statement){ #problem-statement } | Model and assumptions |
| [Objects](theory.md#objects){ #objects } | Model and assumptions |
| [Impedance formulas](formulas.md#impedance-formulas){ #impedance-formulas } | Impedance formulas |
| [Which values a formula may produce](formulas.md#which-values-a-formula-may-produce){ #which-values-a-formula-may-produce } | Impedance formulas |
| [Direct current (`f = 0`)](dc.md){ #direct-current-f-0 } | Direct current |
| [Three singularities, told apart numerically](dc.md#three-singularities-told-apart-numerically){ #three-singularities-told-apart-numerically } | Direct current |
| [A finite reactance has no reading at DC](dc.md#a-finite-reactance-has-no-reading-at-dc){ #a-finite-reactance-has-no-reading-at-dc } | Direct current |
| [An ideal bond is a short circuit](dc.md#an-ideal-bond-is-a-short-circuit-and-a-short-circuit-has-no-admittance){ #an-ideal-bond-is-a-short-circuit-and-a-short-circuit-has-no-admittance } | Direct current |
| [Every transient study contains DC](dc.md#every-transient-study-contains-dc){ #every-transient-study-contains-dc } | Direct current |
| [What a source and a fault need at DC](dc.md#what-a-source-and-a-fault-need-at-dc){ #what-a-source-and-a-fault-need-at-dc } | Direct current |
| [Nodal-admittance formulation](theory.md#nodal-admittance-formulation){ #nodal-admittance-formulation } | Model and assumptions |
| [Path finding](phase-currents.md#path-finding){ #path-finding } | Phase currents in rings and meshes |
| [Derived quantities](theory.md#derived-quantities){ #derived-quantities } | Model and assumptions |
| [Earth potential rise (EPR)](theory.md#earth-potential-rise-epr){ #earth-potential-rise-epr } | Model and assumptions |
| [Branch currents](theory.md#branch-currents){ #branch-currents } | Model and assumptions |
| [Reduction factor](reduction-factors.md#reduction-factor){ #reduction-factor } | Reduction factors and earthing impedance |
| [Reduction factor on a current basis](reduction-factors.md#reduction-factor-on-a-current-basis){ #reduction-factor-on-a-current-basis } | Reduction factors and earthing impedance |
| [Characterising a location without its electrode](decomposition.md#characterising-a-location-without-its-electrode){ #characterising-a-location-without-its-electrode } | Splitting the network, location response |
| [Which one is the EN 50522 reduction factor](reduction-factors.md#which-one-is-the-en-50522-reduction-factor){ #which-one-is-the-en-50522-reduction-factor } | Reduction factors and earthing impedance |
| [The two factors are not two computations of one number](reduction-factors.md#the-two-factors-are-not-two-computations-of-one-number){ #the-two-factors-are-not-two-computations-of-one-number } | Reduction factors and earthing impedance |
| [Splitting the network at the fault](decomposition.md#splitting-the-network-at-the-fault){ #splitting-the-network-at-the-fault } | Splitting the network, location response |
| [Frequency dependence of the reduction factor](reduction-factors.md#frequency-dependence-of-the-reduction-factor){ #frequency-dependence-of-the-reduction-factor } | Reduction factors and earthing impedance |
| [Grounding impedance](reduction-factors.md#grounding-impedance){ #grounding-impedance } | Reduction factors and earthing impedance |
| [Active flag and outage studies](theory.md#active-flag-and-outage-studies){ #active-flag-and-outage-studies } | Model and assumptions |
| [Inverse rho analysis](theory.md#inverse-rho-analysis){ #inverse-rho-analysis } | Model and assumptions |
| [Transient simulations](theory.md#transient-simulations){ #transient-simulations } | Model and assumptions |
| [Worked example](theory.md#worked-example){ #worked-example } | Model and assumptions |
| [Summary of the calculation pipeline](theory.md#summary-of-the-calculation-pipeline){ #summary-of-the-calculation-pipeline } | Model and assumptions |

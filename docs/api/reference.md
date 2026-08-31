# Closed-form reference cases

Every result in this package is a nodal solve, and a nodal solve will happily
return a number for a model that is wrong. These cases are the antidote:
configurations whose answer is known in closed form from the standard treatment
of grounding systems, run through the ordinary public API and compared.

The closed forms are **derived in each case's docstring** rather than quoted, so
they can be checked line by line against whichever text you cite. They are the
standard results of the German grounding literature (Oeding/Oswald, and the
TU Graz and Kücherler treatments); attaching the exact clause and equation
numbers of your editions is yours to do — the module does not claim a citation
it cannot verify.

Each case names the boundary conditions under which its closed form holds. A
deviation outside tolerance means either the model is wrong or a condition was
not met, and in practice the second is the more common finding.

| case | quantity | condition |
|---|---|---|
| `line_ideal_bonding` | `r = \|1 - Z_m/Z_s\|` | station electrodes negligible against the shield |
| `line_finite_earthing` | `r = \|(Z_s-Z_m)/(Z_s+Z_E)\|` | both ends earthed with a finite electrode |
| `en50522_chain` | `U_E = 3I_0 · Z_E · r` | read back from one solved fault |
| `ladder_input_impedance` | `Z_in = -Z'/2 + √(Z'²/4 + Z_e·Z')` | chain long enough to be semi-infinite |
| `ladder_potential_decay` | `u_n/u_0 = e^(-nγ)`, `γ = arccosh(1 + Z'/2Z_e)` | far from either end |
| `parallel_decomposition` | `1/Z_dp = 1/Z_local + Σ 1/Z_side` | cuts covering every branch at the station |

::: groundinsight.analysis.reference
    options:
      members:
        - ReferenceCase
        - run_reference_cases

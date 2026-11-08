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
<!-- CHECK: "Kücherler" (also in src/groundinsight/analysis/reference.py) is probably "Küchler"; confirm the author and edition. -->

The table below is written when this site is built, from `REFERENCE_CASES`
and one run of `gi.run_reference_cases()` — what each case compares, under
which condition, and how close the documented version comes:

<!-- reference-cases-table -->

The same call is a self-test of an installation; see
[Verification and validation](../explanation/verification.md) for the
independent checks behind the cases.

::: groundinsight.analysis.reference
    options:
      members:
        - ReferenceCase
        - REFERENCE_CASES
        - run_reference_cases

# Verification and validation

Two different questions decide whether a result can be trusted:

- **Verification** — does the code solve its model correctly? This page lists
  the evidence the package carries for that.
- **Validation** — does the model describe a real installation? That needs
  comparisons with measurements or with other programs, and for those the
  answer is plain: **groundinsight is not yet validated against field
  measurements or other programs.**

## Closed-form reference cases

`gi.run_reference_cases()` runs six configurations whose answer is known in
closed form through the ordinary public API and compares. Each case names the
boundary conditions under which its closed form holds, and its docstring
derives the closed form, so it can be checked line by line against the
literature (see [Reference cases](../api/reference.md)). A deviation beyond the
tolerance means that either the model is wrong or a condition was not met.

The table below is produced when this site is built, from the cases in
`REFERENCE_CASES` and one run of `gi.run_reference_cases()`:

<!-- reference-cases-table -->

The same call is a quick self-test of an installation (about 0.2 s):

```python
import groundinsight as gi

table = gi.run_reference_cases()
print(table.select("case", "rel_deviation", "tolerance", "agrees"))
assert table["agrees"].all()
```

## An independent solver in the test suite

The test suite carries a second solver for the stationary model,
`tests/_reference_mna.py`, written from the model description rather than from
the package code: modified nodal analysis with the branch currents as
unknowns, in the physical sign frame (the package eliminates the shield
currents and uses the reversed earth-side frame, see
[Conventions](../reference/conventions.md#signs-and-phase-angles)). Against it,
`run_fault` is checked to 1e-12 on 17 structural networks — chains, rings,
meshes, parallel cables, mixed construction, open ends, two sources, a source
on the fault bus, 0/50/150/250 Hz, inactive elements.

On seeded random meshes the suite checks properties that any correct solution
of the model must have, independent of a second implementation:

- Kirchhoff's current law at every bus and over the whole network;
- reciprocity without coupling: the voltage at one pair of buses per ampere
  injected at another pair equals the reverse;
- passivity and Tellegen's theorem without coupling: the real power the
  sources inject is positive and equals the losses in electrodes and shields.

On a long homogeneous cable run (64 sections) the fault-bus earth potential
rise and the earthing impedance meet the closed forms of the ladder network,
$\lvert u_F\rvert = \lvert 1 - k\rvert\, I\,\lvert Z_\infty\rvert$ and
$Z_\infty = -Z'/2 + \sqrt{Z'^2/4 + Z_e Z'}$, to 1e-5.

## Transient solvers

The FFT solver evaluates the stationary formulas per frequency bin, the
state-space solver integrates lumped R/L/C fields in time; the two are
cross-checked with each other and with the stationary solve in steady state. A
second transient solver in the test suite, `tests/_transient_reference.py`
(all branch currents as states, trapezoidal integration), checks the dynamics
of the state-space solver, including the phase-current split in a ring. The
FFT solver does not evaluate the inductive coupling of the branches and warns
when that matters ([Transient simulation](../transient.md)).

## The examples in this documentation

The worked examples of the how-to pages are scripts in `docs/snippets/` that
the test suite runs. The cable example of
[Bus and branch types from data sheets](../howto/types-from-datasheets.md) is
computed three ways — closed form, Pollaczek's integral, `run_fault` — and the
[EN 50522 example](../howto/en50522-check.md#worked-example) is checked against
an independent nodal solve.

## What is not validated

No comparison with measured earthing impedances, earth potential rises or
shield currents, and no comparison with another earthing program, is part of
the package or its documentation. The model assumptions that such a
comparison would test — and that bound what to expect from it — are:

- the coupling acts one way: the phase current is split first, then drives the
  shields; this is exact when all routes of a mesh have the same construction
  and an approximation otherwise ([Phase currents](phase-currents.md#one-way-coupling-when-the-split-is-exact));
- only the faulted phase conductor is modelled, not the healthy phases;
- electrodes are lumped impedances to remote earth, without resistive coupling
  between neighbouring electrodes;
- the soil is homogeneous per element (one `rho` per bus and per branch);
- the stationary solve is quasi-stationary: no travelling waves, no soil
  ionisation;
- the impedances are the formulas the user supplies; the package checks their
  syntax and passivity, not their physics.

## References

- Reference cases: [`groundinsight.analysis.reference`](../api/reference.md).
- Test oracles: `tests/_reference_mna.py`, `tests/_transient_reference.py`;
  physics regressions `tests/test_audit_pass19_physics.py`,
  `tests/test_audit_pass19_transient.py` in the
  [repository](https://github.com/Ce1ectric/groundinsight).

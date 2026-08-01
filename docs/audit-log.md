# Audit log

Working record of the code-audit passes run over `groundinsight` between
2026-05 and 2026-07. It exists because the measurement protocol behind a
fix — how the bug was reproduced, what the negative control was, and
whether a mutation of the fixed code is actually caught by the new test —
is worth keeping, but does not belong in user-facing release notes.

!!! note "What is authoritative for what"

    [`CHANGELOG.md`](https://github.com/Ce1ectric/groundinsight/blob/main/CHANGELOG.md)
    is authoritative for **what shipped in which release**. This page is
    authoritative for **how a finding was established**. Where the two
    disagree, the changelog wins.

    Part 3 lists findings that are confirmed but **not implemented**.
    Nothing in Part 3 is a description of shipped behaviour. Some Part 3
    entries were written before a later pass and have since been resolved
    without the entry being struck through; check the changelog before
    acting on one.

Each entry follows the same shape: what was observed, the test that
proves it is a defect rather than a matter of taste, and the change. The
project's rule is that a fix arrives with a test that fails without it;
for changes to drawing code and to matrix assembly — where a test can
pass for the wrong reason — the entry also records a mutation-testing
result.

## Part 1 — closed findings, by pass

Newest pass first. The pass numbering is chronological, not a severity
ranking.

### Internal (Audit pass 17 — release readiness)

> The 0.5.0 cut was checked end to end instead of assumed. The library is
> green — 874 tests pass and `mkdocs build --strict` is clean — but the
> *release path* was blocked in a way no test could have caught, because the
> blocker sits in the release tooling's own preconditions rather than in the
> package. Four items, all of them mechanical, none of them observable at
> runtime.

- **Version drift made `scripts/release.py` abort before it started.**
  `pyproject.toml` and `src/groundinsight/__init__.py` had been bumped to
  `0.5.0` in Pass 6 while `CITATION.cff` stayed at `0.4.0`. The script reads
  the current version from all three locations and exits with
  *"version drift detected"* when they disagree, and `release set 0.5.0` is
  refused as well because the target is not newer than the current value —
  so there was no way through the documented workflow at all. All three now
  read `0.4.0`, the last published version, which is what the working tree
  is supposed to carry: `poetry run release minor` performs the bump, rolls
  `[Unreleased]` into `## [0.5.0]`, commits, tags `v0.5.0` and pushes.
- **A test pinned the version literal and would have failed on every
  future release commit.** `test_version_is_0_5_0` asserted
  `gi.__version__ == "0.5.0"`. Since the release script rewrites that
  literal and *then* commits, the release commit itself would have failed
  the CI from the next minor bump onwards. Replaced by
  `test_version_matches_pyproject`, which reads the expectation from
  `tool.poetry.version`, plus `test_version_matches_citation_cff`, which
  turns the drift described above into a test failure at development time.
  Both skip cleanly when the package is installed without its source tree.
- **Deprecation notes were attributed to the wrong release.** Thirteen
  docstring and documentation passages described behaviour that *this*
  release changes as having been the case "up to v0.5.0" — an artefact of
  the premature bump, since the last published tag is `v0.4.0`. Corrected in
  `database/migration.py`, `electrical_network.py`,
  `simulation/transient.py`, `docs/concepts.md`, `docs/transient.md` and
  three audit test modules. One of them,
  `tests/test_audit_pass11_migration.py`, quoted
  `git show v0.5.0:src/groundinsight/models/database_models.py` as the
  provenance of its legacy-schema fixture — a command that cannot run,
  because that tag does not exist.
- **Sandbox paths removed from the changelog.** Five passages referenced
  `/tmp/probe_*.py` scratch files from the machine the measurements ran on.
  The file is rendered into the GitHub release notes, where such a path is
  noise.

### Fixed (Audit pass 16 — 0 Hz is a frequency, not a special case)

> `f = 0` has been an accepted entry in `Network.frequencies` and in
> `fault.scalings` since the validator was written, and the *solver* is exact
> there: `Y(f) · u(f) = i(f)` has no frequency dependence of its own. What
> broke at DC was everything around it, and it broke in three independent
> places, each of which had to be told apart from a different case that looks
> identical in floating point. Until now the way to get a DC answer out of
> `groundinsight` was to enter `f = 0.1 Hz` and accept the reactance that
> comes with it.
>
> The three failures are not variants of one bug and their fixes share no
> code: evaluating the *formula string* at zero, reading a *finite reactance*
> at zero, and inverting a *zero impedance*. The third one is unavoidable in
> transient studies — an FFT grid always contains a 0 Hz bin, so every
> transient run of every network with a purely inductive element hit it. Every
> figure below was measured against the real solver in a twelve-probe
> campaign, run before a line of code was changed, and
> `tests/test_audit_pass16_dc.py` adds 32 tests.
>
> This also closes the pass-15 roadmap item *"`Z = 0` at DC is modelled as an
> open circuit in the transient solver"*, which was left open pending exactly
> the decision the measurement below settles.

- **A removable singularity at 0 Hz raised instead of resolving.** Carson's
  earth-return term, `ω · ln(658·√(ρ/f)/GMR)`, is `0 · ∞` at `f = 0`. The
  limit is 0 — `ω` vanishes linearly while the logarithm diverges only
  logarithmically — so a Carson-type conductor tends to its DC resistance,
  which is the one number a DC study is about. Floating point renders the
  limit as `NaN`, and `NaN` was reported as a formula error, which made every
  Carson-type conductor unusable at DC **and in every transient study**. The
  limit is now determined numerically by approaching zero on the decade
  sequence `1e-6, 1e-7, 1e-8 Hz` and comparing two consecutive absolute
  differences. Measured on the reference cases: `d₁ = 1.41e-8`,
  `d₂ = 1.41e-9` for an inductance (shrinking, hence convergent) against
  `d₁ = 1.43e+12`, `d₂ = 1.43e+13` for a capacitance (growing, hence a
  pole). The comparison is deliberately *absolute*: a relative criterion
  cannot classify a formula whose limit is zero, because there the relative
  change stays at 90 % per decade forever.
- **A true pole is still infinite, and infinity is still the answer.** A
  series capacitance is an open circuit at DC and `1/(j·ω·C) → ∞` says so.
  The tie-break in the classifier is biased towards *convergent* on purpose:
  calling a pole convergent yields a large finite impedance, which behaves
  almost like the open circuit it should have been, whereas calling a
  convergent formula a pole would silently disconnect a real earthing
  conductor.
- **A genuine failure keeps failing.** `√ρ` with negative `ρ`, a `NaN`
  parameter, `0/0`: the approach sequence is `NaN` as well, the singularity
  cannot be classified, and the original error message — which names the
  formula, the frequency and the parameters — is the one that fires.
- **`Z = 0` at DC was modelled as an open circuit — in *both* solvers.** A
  purely inductive element is an *exact* zero at DC, which is correct
  physics, and zero has no reciprocal the nodal solve can use. Both solvers
  responded by dropping the element, i.e. by modelling a short circuit as a
  disconnection. Measured on a three-bus chain (`Z_A = 0.8 Ω`,
  `Z_B = 12 Ω`, `Z_C = 3.5 Ω`, bond A–B purely inductive, `R_BC = 0.35 Ω`,
  1 kA DC injected at A, fault at C): the correct EPR is
  `A = B = 57.07 V`, `C = −266.30 V`; what the solver returned was
  `A = 800.00 V`, `B = −2649.84 V`, `C = −2727.13 V` — wrong by factors of
  **14.0, 46.4 and 10.2**, with the sign of bus B reversed. Bus A had been
  cut off from the network entirely and reported its own electrode instead of
  the parallel combination. Across eight further realistic networks the
  open-circuit treatment was wrong by factors from **28 to 4.5 million**.
- **The replacement is a measured stand-in, not a guessed epsilon.** Such an
  element is now modelled with `√(machine epsilon) · Z_min = 1.4901e-8 · Z_min`
  ohm at the 0 Hz bin only, where `Z_min` is the smallest finite non-zero
  impedance the network carries at that frequency. The rule was chosen by
  sweeping four candidates over eight networks × two failure modes against
  the analytically node-merged reference: tying it to `Z_min` gives a worst
  case of `1.19e-5`, to `Z_median` `3.44e-2`, to `Z_max` `6.62e+1`, and the
  obvious `1e-9 · Z_min` `2.79e-4` — twenty times worse than the rule
  chosen. Both scales that matter are local to the shorted element and a
  solver cannot see them, which is why the network-wide rule costs about
  `1e-5` instead of the `3e-7` a per-element optimum would reach. Several
  simultaneous bonds do not compound (`1.25e-5` for one, `2.69e-5` for
  three) and complex neighbours behave identically (`4.4e-5 … 6.4e-5` at
  X/R = 0.3). On the three-bus chain above the substitution reproduces the
  merged reference to `1.58e-9` (stationary) and `8.33e-8` (transient DC
  bin), and it beats an explicitly modelled `1e-8 Ω` bond. Every use is
  announced with a `DCLimitWarning` that names the elements, the substitute,
  the reference it was scaled to, and the exact remedy: model the two buses
  of an ideal bond as a single bus.
- **Seven inversion sites, not one.** `1/complex(0, 0)` *raises
  `ZeroDivisionError`* for Python complex numbers rather than returning
  infinity, so each place that inverts an impedance was its own crash path at
  DC, not merely a place that returns a wrong number. Four of them sit
  outside the matrix assembly and are reached by no test that only checks the
  EPR: the source injection (`u_eff / Z_src`), the phase-current split, the
  mutual-current transfer (`Z_mutual / Z_self`) and the bus-current loop, plus
  `compute_branch_currents`. All seven now go through one method,
  `ElectricalNetwork._resolved_impedance`, so the substitution cannot be
  applied in one place and forgotten in another.
- **The transient path escaped the passivity check at 0 Hz entirely.** It
  dropped the 0 Hz bin before validating, which was the only way to let a
  legitimate short circuit through — but it also let a formula that turns
  *negative* at DC through, unchecked. The bin is validated like every other
  now, with the short circuit handled by substitution rather than by looking
  away.

### Changed (Audit pass 16 — what a formula may produce at 0 Hz)

- **A finite reactance at 0 Hz falls back to the real part, with a
  warning.** `(0.25 + j·0.6)·l` — the most common spelling in the wild —
  reports 0.6 Ω of reactance at *every* frequency including zero, and at DC
  that is a statement about nothing: a reactance either vanishes
  (`j·ω·L → 0`) or is infinite (`1/(j·ω·C) → ∞`). The 0 Hz bin now takes the
  real part and says so, naming X/R and the remedy (write the reactance as
  `j*2*pi*f*L` and it vanishes at DC by itself). All other frequencies are
  untouched. The warning quotes the *ratio* rather than the two values,
  because the ratio is length-invariant and therefore identical for every
  branch sharing a `BranchType` — which lets Python's default warning filter
  collapse a hundred-branch network into one line instead of a hundred.
- **The fallback is off for R/L/C parameters.** `compute_real_value` passes
  `dc_real_fallback=False`: an R, L or C field is real at every frequency by
  contract, so a complex value there is a mistake to report, not a value to
  repair. Silently dropping the imaginary part would have disabled the
  existing "produced a non-real value" check at 0 Hz and nowhere else. The DC
  *limit* still applies on that path.
- **`Z = 0` and a non-invertible `|Z|` are accepted at 0 Hz and rejected
  above it.** This is the one asymmetry the DC work introduces into the
  pass-15 rule, and it is asymmetric because the physics is: an inductance
  really is a short circuit at DC and really is not one at 50 Hz. The
  pass-15 message for the rejected case now says that 0 Hz would have been
  accepted, so a user who meets it at power frequency is not left wondering
  why the same value passes in a transient run. A **negative real part stays
  rejected at every frequency**, 0 Hz included: a passive element is passive
  at DC too.
- New public helpers in `groundinsight.utils.impedance_calculator`:
  `is_short_circuit(z)` asks the arithmetic (`is 1/z finite?`) rather than
  comparing against a hand-carried constant, so the boundary moves with the
  floating-point format instead of with a magic number; and
  `dc_substitute_impedance(magnitudes, shorted_elements, context=)` sizes the
  stand-in and emits the warning.

### Docs (Audit pass 16)

- `docs/concepts.md` gains **"Direct current (`f = 0`)"**: why the solver is
  exact at DC but the formula string is not, the three singularities and how
  they are told apart, the reactance fallback and how to write a formula that
  does not need it, what the short-circuit substitution costs in accuracy and
  when to merge two buses instead, and the DC entries a `Source` and a
  `Fault` need.
- `docs/transient.md`: the *DC bin* implementation note no longer claims the
  0 Hz bin is skipped. It now states that **every** transient run evaluates
  every formula at zero frequency, names the three cases and links to the
  concepts chapter.
- New notebook `notebooks/24_dc_studies.ipynb` (15 code cells, all executed
  end to end): a DC earth-current study run at `f = 0` next to the
  `f = 0.1 Hz` workaround it replaces, the reactance fallback made visible,
  Carson's limit and the `d₁`/`d₂` classifier reproduced by hand, the
  ideal-bond substitution checked against a hand-merged reference network and
  against explicitly modelled bond resistances from `1e-2` down to `1e-8 Ω`,
  the DC bin of the corresponding transient run, a capacitive bond staying an
  open circuit, and a mixed `0 / 50 Hz` study.
- **What the `f = 0.1 Hz` workaround cost, measured.** For a conductor whose
  reactance is written as `j*2*pi*f*L` the workaround was accurate to eleven
  digits (`456.621004575 V` at 0.1 Hz against `456.621004566 V` at 0 Hz) and
  existed only because `f = 0` used to raise. For the far more common
  `(0.25 + j*0.6)*l` spelling it reports **718.54 V instead of 456.62 V** and
  `Z_G = 2.87 Ω` instead of `1.82 Ω` — 57 % high, and the error does not
  shrink with the frequency because the reactance is constant.

### Fixed (Audit pass 15 — an impedance that cannot become an admittance)

> One subject: every impedance that reaches the diagonal of `Y` is used as
> its reciprocal `1/Z`, and three kinds of value have no reciprocal the
> nodal solve can use. All three were swallowed without a word — none of
> them raised, none of them warned, and two of them returned a number an
> engineer would accept.
>
> The proof for the first is a limit test, run on a two-bus reference
> network (`Z_A = 10 Ω`, branch `1 Ω`, 100 A injected at A, fault at B) in
> which a bus impedance going to zero has a non-zero limit to converge to.
> The sequence `Z_B → 0` converges cleanly to `EPR(A) = 1000/11 V`,
> `EPR(B) → 0`, matching the closed-form solution of the same 2×2 system to
> `rel = 1e-9` at every point from `0.1 Ω` down to `1e-12 Ω`. The answer the
> model returned *at* `Z_B = 0` was `EPR(A) = 0`, `EPR(B) = 100 V` —
> **bit-for-bit identical to `Z_B = ∞`**, and the mirror image of the limit.
> A perfect earth electrode and a missing one were the same object. Every
> figure quoted below was reproduced against the real solver with nothing
> changed but the new guard;
> `tests/test_audit_pass15_zero_impedance.py` adds 47 tests.

- **`Z = 0` was modelled as an open circuit — the exact opposite of the
  ideal earth it looks like.** `_is_open` treated a zero impedance the same
  way as an infinite one and dropped the element from the matrix, so a bus
  with a "perfect" grounding grid reported the *full* earth potential rise
  and no current into the soil. Zero is now rejected where the impedance is
  computed, with the element, the frequency and the formula in the message,
  and with the remedy: a near-ideal earth is a small finite value, not zero.
  The message quotes an accuracy for that remedy and the accuracy is
  measured — `1e-6 Ω` in a network whose impedances are of the order of
  `1 Ω` reproduces the ideal-earth limit to about seven digits (relative
  error `9.09e-8`), and every further decade buys another digit.
- **An impedance too small to invert put `inf` on the diagonal and `NaN` in
  the results.** `1/Z` overflows below `1/DBL_MAX ≈ 5.5626846e-309`, one
  representable step away from zero and not caught by an `== 0` test. At
  `Z_B = 5.5e-309` the `EPR_V` column still looked correct while `I_bus_A`
  came back as `NaN` for that bus, in both the 50 Hz row and the RMS row.
  The guard asks `np.isfinite(1/Z)` rather than comparing against a
  hand-carried constant, so the boundary sits exactly where the arithmetic
  stops: `5.6e-309` is accepted, `5.5e-309` is not.
- **A negative real part produced a plausible-looking number and then a
  misdiagnosis.** An earth electrode, an earthing conductor and a cable
  screen are passive, and a negative resistance walks the nodal determinant
  towards zero. Measured on the reference network, which is singular at
  `Z_B = −11 Ω`: `−1 Ω` gives 100 V, `−10 Ω` gives 1 kV, `−10.9 Ω` gives
  10 kV, `−10.99 Ω` gives 100 kV and `−10.999 Ω` gives **1.0999 MV** — all
  finite, all returned without comment. At exactly `−11 Ω` the solve fails,
  and the error it raised was the wrong one: *"the network has no path to
  reference earth. Ensure at least one bus has a finite grounding
  impedance"* — advice that cannot fix a network in which every bus already
  has one. This is not an exotic input: `0.05*rho - 2`, a plausible fit for
  a rod electrode, goes negative below `ρ_E = 40 Ω·m`, which is ordinary wet
  soil. A ρ sweep over that fit now raises at 40 and below, naming the
  formula and the ρ, instead of solving.
- **The check runs a second time immediately before the matrix is
  assembled, because impedances are not recomputed at solve time.**
  Rejecting the three cases only where a formula is evaluated would have
  been half a fix: a value assigned directly to `bus.impedance[freq]`, or
  restored from a `.db` or a JSON file written by an older version, reached
  the solver untouched. There are regression tests for all four routes.

### Changed (Audit pass 15 — three impedance values that used to be accepted)

- **Breaking: a zero, a sub-invertible or a negative-real-part impedance
  raises `ValueError`** on `BusType`/`BranchType` impedance formulas (bus
  grounding impedance, and branch self impedance where
  `grounding_conductor=True`), on `Source.source_impedance`, and again at
  the top of `_construct_Y_matrices`. If you have a model that deliberately
  writes `0.0` to mean "ideal earth", replace it with a small finite value —
  `1e-6 Ω` is seven digits of the ideal limit in a 1 Ω network, and unlike
  `0.0` it actually behaves like one.
- **The scope is deliberately narrow, and pinned by tests as carefully as
  the rule itself.** Mutual impedances are *not* checked — `Z_mutual = 0` is
  the ordinary way to say "no coupling" and stays legal. The self impedance
  of a branch with `grounding_conductor=False` is never inverted and is not
  checked. Inactive buses and branches are not checked, nor are frequencies
  outside `network.frequencies`. `inf` remains legal everywhere: it is the
  documented open-end sentinel (`impedance_formula="nan"`) and `1/inf = 0`
  is the correct contribution for a tower without an electrode. `NaN` is
  left to `compute_impedance`, which already reports it better.
- **`_is_open` was renamed to `_is_open_circuit` and no longer answers
  "yes" for zero.** It now tests infinity only, which is what its name and
  its every call site always meant.
- **A pass-12 regression test was superseded.** The cutoff formula
  `(0.30 + j*f*0.0025) * l * sqrt(1 - (f/500)**2)`, used there to show that
  a formula going complex above its cutoff no longer poisons the network,
  evaluates to `−4.33 + 0.52j Ω/km` at 1000 Hz and is now rejected outright
  for its negative real part. `test_cutoff_formula_no_longer_poisons_the_network`
  was split into three tests that keep the original claim about the
  imaginary part while asserting the new rejection; the file is green at 61
  tests.

### Docs (Audit pass 15)

- `docs/concepts.md` gains **"Which values a formula may produce"** under
  *Impedance formulas*: the three rejected results and why, how to model a
  near-ideal earth and what accuracy that buys, why a negative real part is
  usually a fit evaluated outside its range, the exact scope of the rule,
  why it runs twice, and the transient 0 Hz exception.

### Added (the plot helpers accept an axis and can release their figure)

> The five plotting helpers each created their own figure and left it
> registered with `pyplot`. That is right in a notebook and wrong
> everywhere else: two scenarios could not be put side by side in one
> figure, and a parameter sweep accumulated figures until matplotlib
> warned at twenty. Both are now caller-controlled.
>
> Implementing this meant rewriting every helper from the `pyplot` state
> machine (`plt.bar`, `plt.xticks`, `plt.title`) to the object-oriented API
> (`ax.bar`, `ax.set_xticks`, `ax.set_title`), which touches every drawing
> call in the module — so "it still looks right" was not accepted as
> evidence. A fingerprint of fifteen calls
> serialising bar positions and heights, tick locations, tick-label text,
> rotation and alignment, axis labels, titles, scales, limits, legend
> entries, grid state, line data and axis geometry is **byte-identical
> before and after** the rewrite. `tests/test_plot_axes_and_close.py` adds
> 125 tests, and all 59 mutations of the changed code are killed by their
> intended test; the five recorded accepted equivalents are documentation,
> typing, an arbitrary guard order and one provably redundant call, each
> with the reason written out in the harness.

- **`ax=` draws into an axis you already have.** The helper then treats the
  figure as the caller's: it applies no `tight_layout`, closes nothing, and
  returns the caller's figure rather than a new one. This is what makes a
  base case and an outage case comparable in one figure:
  `fig, axes = plt.subplots(1, 2, sharey=True)` followed by
  `gi.plot_bus_voltages(result=base, ax=axes[0])` and
  `gi.plot_bus_voltages(result=outage, ax=axes[1])`.
- **`close=True` releases the figure the helper created.** The returned
  figure is still complete — `savefig` works exactly as before — but it is
  unregistered from `pyplot`, so a sweep no longer accumulates figures.
  Equivalent to calling `plt.close(fig)` afterwards, and only ever applied
  to a figure the call itself created.
- Both parameters are keyword-only, appended behind a `*`, so the
  historical positional signature `(result, frequencies, figsize, title,
  yscale)` is unaffected.

### Changed (the plot helpers reject two argument combinations and a bad `figsize`)

- **`ax=` together with `figsize=` raises `ValueError`.** The figure
  already exists and may hold other panels, so the requested size cannot be
  honoured; silently ignoring it would hand back a plot at a size the
  caller did not ask for. The message says how to size the figure instead.
- **`ax=` together with `close=True` raises `ValueError`.** `close=`
  releases the figure *this call* created. With `ax=` the figure belongs to
  the caller, and closing it would take every sibling panel with it.
- **A `figsize` that cannot be used raises `ValueError` instead of being
  replaced by the default.** Matplotlib accepts `figsize=(0, 0)` when the
  figure is created and only fails much later, when the figure is drawn or
  saved, so the traceback pointed at `savefig` rather than at the call
  responsible. A `figsize` that is not a pair of numbers at all — `()`, a
  scalar, a three-tuple — is reported the same way, because the exception
  it would otherwise raise (`not enough values to unpack`) names neither
  the parameter nor the helper. Previously a falsy `figsize` was quietly
  swapped for the default size.
- `figsize` now defaults to `None` rather than to a literal size. It
  resolves to the same `(12, 6)` for the bar helpers and `(10, 5)` for the
  transient helpers, so no existing call changes; the sentinel is what
  makes "no size given" distinguishable from "this size given".

### Fixed (Audit pass 14 — results that look like answers)

> The smaller confirmed findings left over from earlier passes, collected
> into one batch. They live in four different modules and share one shape:
> each hands back a *plausible artefact* — a bar chart, a comparison table,
> a loaded network, a solved result — for a question the code could not
> actually answer. None of them fails; that is what makes them worth
> fixing. Every finding was reproduced on a running network before a line
> of code was changed on a running network. Regression tests in
> `tests/test_audit_pass14_minor_findings.py` (41 tests); all 29 mutations
> of the changed code are killed by their intended test, and the two
> recorded accepted equivalents are documentation and one provably
> equivalent spelling, each with the reason written out in the harness.

- **A frequency that was never computed was plotted as a bar of height
  zero.** `plot_bus_voltages`, `plot_branch_currents` and
  `plot_bus_currents` all read their per-frequency dict with `.get(freq)`
  and substituted `0.0` on a miss, although all three docstrings already
  promised a `KeyError`. On a 50 Hz result, `frequencies=[250.0]` produced
  a clean figure whose every bar sat at zero — which an engineer reads as
  *"the fifth harmonic causes no earth potential rise at this station"*,
  when the truth is that 250 Hz was never part of the calculation. The
  promised `KeyError` is now raised, and its message names the requested
  frequencies, the mapping they are missing from, and the frequencies that
  *are* available. The partial case — a frequency present on some elements
  and missing on others, which would mix measured values and substituted
  zeros inside a single bar group — is rejected separately and names the
  offending elements.
- **The relative delta of an outage study divided by the reference value
  without a guard.** `OutageStudyResult._compare` computed
  `(value - ref) / ref * 100` unconditionally, and a zero baseline is
  ordinary in a grounding study, not exotic. Measured on two realistic
  studies: a fault declared with `scalings={250.0: 0.0}` — *"this fault
  current has no fifth harmonic"* — made the entire 250 Hz block `0/0`,
  i.e. **6 `NaN` rows out of 18**; comparing `against=` a scenario that
  islands a station gave that station `0 V` in the denominator and a real
  voltage in the numerator, i.e. **2 `+inf` rows out of 16**. The `inf` is
  the single most interesting row of the study, and it sorts to the top of
  every "largest relative change" ranking while making `mean()` and
  `max()` over the column useless; the `NaN` marks the most boring row
  with the same symbol a solver divergence would produce. The column is
  now `null` wherever the reference is zero — Polars aggregations skip it
  and matplotlib plots it as a gap — while the absolute `delta_vs_<ref>`
  column keeps the full information. A non-zero baseline is unaffected,
  including the reference row's own `0.0 %`.
- **An unresolvable bus or branch type surfaced as an `AttributeError`
  from inside the ORM.** `BusDB.to_pydantic` and `BranchDB.to_pydantic`
  dereferenced `self.type.to_pydantic()` without checking the
  relationship, so a database whose type row had been deleted — a shared
  `.db` someone pruned, a hand-edited file — failed with
  `AttributeError: 'NoneType' object has no attribute 'to_pydantic'`. That
  message names neither the element, nor the type it wanted, nor the
  network, and reads like a groundinsight bug rather than an inconsistent
  database. Both now raise a `ValueError` naming all three and saying what
  to do, matching the `PathDB.to_pydantic` precedent that was already
  there.
- **Two documented `ValueError`s were never raised.** `create_paths`
  documented that it rejects a network without sources or faults and did
  not, so a network missing its `create_source(...)` ran the full
  pipeline and returned a four-row table of `0.0 V / 0.0 A` — measured,
  no warning, no log line. `create_network_assistant` documented its
  argument contract and enforced none of it.

### Changed (Audit pass 14 — two guards that reject calls that used to work)

- **Breaking: `create_paths` — and therefore `run_fault` — rejects a
  network with no sources or no faults.** Such a network previously
  produced a complete, structurally valid all-zero result. The check is on
  the *collections being empty*, never on the resulting path count, which
  is the distinction that matters: an outage scenario that islands the
  fault bus still runs and still legitimately returns all zeros, and there
  is a regression test pinning exactly that (a mutation tightening the
  guard to "no paths found" is killed by it). If you have a workflow that
  deliberately solves an unexcited network, this is the one item in this
  pass to object to.
- **Breaking: `create_network_assistant` validates `number_buses` and
  `branch_length`.** A line of `n` buses has `n − 1` branches; passing `n`
  lengths silently dropped the last one, and passing too few raised a bare
  `IndexError` from inside the loop. Both now raise a `ValueError` stating
  the two counts. `number_buses` must be an `int >= 1` (a `bool` is
  rejected although Python considers it an `int`), and a scalar
  `branch_length=1.0` — a natural thing to try — now gets a message
  instead of `TypeError: 'float' object is not subscriptable`.
  **Two tests in this repository carried exactly this off-by-one**:
  `test_network_assistant_creation` passed 10 lengths for 10 buses and
  `test_reduction_factor_for_bus7_fault7` passed 30 for 30. Both were
  trimmed to `n − 1` after verifying that the removed entries were the
  ones already being dropped, so both networks are bit-for-bit unchanged
  and their numeric reference assertions remain valid.

### Docs (Audit pass 14)

- The five plotting helpers now document the figure-ownership contract:
  the returned figure is registered with `pyplot` and stays open until the
  caller closes it, so a soil-resistivity sweep in a loop accumulates
  figures until matplotlib warns at twenty. This is **documented, not
  fixed** — the fix is an `ax=` parameter, which is a new feature and
  needs a decision first. Two tests pin the current behaviour so the note
  cannot silently become wrong.
- `docs/api/plotting.md` gains sections on the frequency guard and on
  figure ownership, `docs/api/outage.md` a table showing when
  `delta_pct_vs_<ref>` is `null` and why, `docs/api/network_operations.md`
  the excitation requirement and the n−1 rule, and `docs/quickstart.md`
  and `docs/concepts.md` short notes on both.
- `plotting.py` tightens three presence tests from `if value:` to
  `if value is not None:`. This is intent, not a behaviour change:
  `ComplexNumber` is a Pydantic model without `__bool__`, so every
  instance is truthy and both spellings take the same branch — and even if
  that changed, both branches assign `0.0` for a zero value. It is
  recorded as an accepted-equivalent mutation rather than claimed as a
  fix.

### Fixed (Audit pass 13 — what a bisection result does *not* say)

> Found by asking the inverse-rho searches the one question their return
> value could not answer: *how much of this number did you actually prove?*
> A log-bisection has four ways to stop and only one of them is a maximum,
> yet all four returned the same dict shape, and the only field that hinted
> at the difference — `iterations` — took the value `0` for three of them,
> whose `c_max` differed by a factor of **1e6** (0.001 against 1000) on the
> same network. Every finding below was reproduced numerically before a line
> of code was changed. Regression tests in
> `tests/test_audit_pass13_bisection.py` (57 tests). All 35 mutations of the
> changed code are killed by their intended test; the two mutations recorded
> as accepted equivalents are unreachable second locks on a door the pass-12
> formula guards already close, with the reason written out in the harness.

- **`iterations == 0` meant three different things**, and the docstring
  documented one of them. It could mean the whole bracket was admissible
  (`c_max` is the *upper* bound, the true maximum is above it and was never
  determined), the tolerance was already met on entry (`c_max` is correct),
  or the loop never ran at all because an argument was degenerate (`c_max`
  is the *lower* bound). The advice "if `iterations` is 0, widen
  `c_bounds`" was therefore actively wrong in two of the three cases: for a
  hit step cap it points at the wrong knob, and for a bracket already inside
  the tolerance it asks the caller to discard a converged answer. Results
  now carry `status`, `converged`, `c_bracket` and `bracket_rel_width`;
  `iterations` is retained but no longer identifies a case.
- **Exhausting `max_iter` was silent.** `max_iter=3` on the reference network
  returned `c_max` with a **81 %** relative error, in a dict indistinguishable
  from a converged one. It is now `status="max_iter_reached"`,
  `converged=False`, and a `logger.warning` naming the bracket that was
  never closed. `c_max` remains a *verified admissible* factor in this case —
  the search measured its EPR — it is simply not the largest one, and that is
  now the difference between the two keys rather than a difference the caller
  cannot see.
- **`tol_rel` was not validated.** A non-positive tolerance can never satisfy
  the exit test `(c_hi - c_lo) / c_lo <= tol_rel`, so the search spent every
  one of the 60 default steps — 62 `run_fault` calls against 16 for the same
  search with an honest tolerance — and returned a bracket it never closed.
  NaN fails the opposite way: `width > nan` is `False` on the first pass, so
  no step is taken and the lower bracket bound comes back as the answer.
- **`max_iter` was not validated.** `0` and `-5` skipped the loop entirely and
  returned `c_bounds[0]` — an admissible factor, hence indistinguishable from
  a real result. `2.7` silently meant three steps.
- **A NaN EPR limit passed the positivity guard.** `nan <= 0` is `False`, so
  `u_max=nan` (and `u_limit=nan`) was accepted; every later comparison against
  it is `False` as well, so the limit test carried no information and the
  search walked the bracket down to `c_min` and reported it. Both searches now
  require a finite positive limit, with the mechanism spelled out in the
  message.
- **The same NaN hole in `select_rho_f_from_catalog`, where it is worse.**
  That function does not return a scalar a reader might sanity-check but a
  table with an `admissible` column, and every entry in it is
  `max_epr <= u_limit`. With `u_limit=nan` the table reported that *no* soil
  model in the catalog is usable — measured: 0 of 3, where an honest limit
  gives 3 of 3 — next to an EPR column that is correct and finite. Nothing in
  the output pointed at the limit as the broken part.
- **`c_bounds=(1e-3, inf)` passed the ordering check** (`0 < c_lo < c_hi`
  holds for an infinite upper bound) and travelled into the solver as an
  infinite grounding impedance, surfacing several layers down as *"no active
  bus is referenced to earth"* — a diagnosis about the network for what is an
  argument error. Bounds are now checked for finiteness first, separately, so
  the message names the argument.

### Changed (Audit pass 13 — the inverse-rho result contract)

- **`find_max_rho_scaling` and `find_max_rho_f_scaling` return four new
  keys.** `status` is one of `"converged"`,
  `"bracket_within_tol_on_entry"`, `"bracket_fully_admissible"` or
  `"max_iter_reached"`; `converged` is `True` for the first two;
  `c_bracket` is the interval that provably contains the true threshold; and
  `bracket_rel_width` is its relative width. For a fully admissible bracket
  the interval is reported as `(c_hi, inf)`, which makes "widen `c_bounds`"
  machine-readable: `math.isfinite(result["c_bracket"][1])` is `False`. All
  previous keys keep their names and meanings, so existing code that reads
  `c_max` continues to work — it just now has a way to find out whether that
  number is a maximum.
- **Breaking: `max_iter` must be an `int >= 1`.** `max_iter=60.0` now raises
  a `ValueError` instead of being accepted. The float was never harmless:
  the loop condition rounds it up, so `2.7` meant three steps, and a cap
  that does not mean what it says is worse than no cap. Callers passing a
  float should pass the `int`.
- The shared checks and the report builder live in
  `groundinsight/analysis/_bisection.py` rather than being written twice, so
  the two searches cannot drift apart. `evaluate_max_epr_under_k` is
  deliberately left unvalidated: it is a pure evaluator with no limit, no
  tolerance and no loop, and adding guards it has no failure mode for would
  only obscure where the real ones are.

### Fixed (Audit pass 12 — the road from a formula string to a number in `Y`)

> Found by walking the single path every impedance takes — formula string →
> SymPy → `lambdify` → NumPy → `ComplexNumber` → admittance matrix — and
> asking at each hop which inputs leave it silently wrong rather than
> loudly broken. Five of the six defects below produced a *number*, not an
> exception, and surfaced (if at all) several layers later as "singular
> admittance matrix" — a message that describes a topology error which does
> not exist. Regression tests in `tests/test_audit_pass12_formula.py`
> (59 tests). All 48 mutations of the changed code are killed by their
> intended test; the one mutation that survives is recorded in the harness
> as an accepted equivalent with the reason written out.

- **A formula that merely *contained* the letters `nan` became an open
  circuit, without a word.** The open-end sentinel was tested with
  `"nan" in formula_str.lower()` — a substring test — so `resonance`,
  `resonanz`, `nanofarad`, `dominant` and `discriminant` all matched. The
  first two are ordinary vocabulary in a resonant-earthed (Petersen coil)
  network, which is exactly the kind of system this package is written for:
  a parameter named `resonanz_f` turned the element into an open end and the
  network was solved, with a plausible-looking answer. The sentinel is now
  matched against the whole stripped, case-folded string, so `"nan"`,
  `"NaN"` and `" nan "` still mean open end and nothing else does.
- **A parameter whose name collides with one of SymPy's ~680 exported names
  lost its value.** `sympify` was called without `locals`, so names were
  resolved out of the SymPy namespace first. Two of the collisions are
  silent: `E` evaluates to Euler's number 2.71828… and `oo` to infinity, and
  the value the caller passed is simply discarded. The rest are loud but
  unhelpful — `S` (the conductor cross-section of IEC 60949), `beta` (its
  material constant), `gamma` (the propagation constant), `N`, `Q`, `re`,
  `im` resolve to a SymPy class or function and the arithmetic then fails
  with `unsupported operand type(s)`, naming neither the parameter nor the
  reason. Declared parameters are now bound as plain symbols before parsing.
  Names that are *not* declared keep their SymPy meaning, so `sqrt`, `log`,
  `exp` and `pi` continue to work.
- **`params={"I": ...}` was silently overwritten with the imaginary unit.**
  `I` for a current is about as natural a name as exists in power
  engineering; the value was discarded and the formula evaluated with `1j`
  in its place. `params={"j": ...}` behaved the same way, and
  `params={"f": ...}` leaked `duplicate argument 'f' in function definition`
  out of generated code. The three names are now rejected with a message
  that leads with the offending key and suggests a rename.
- **Every formula whose argument goes negative collapsed to NaN.**
  `lambdify(..., modules=["numpy"])` picks the branch of `sqrt` and `log`
  from the *dtype*, not from the value: `np.sqrt(-0.5625)` is `nan` where
  SymPy says `0.75*I`. This is reachable in ordinary use — 0 Hz is a routine
  entry in `Network.frequencies` and in `fault.scalings`, and `sqrt(f - f0)`
  or `log(f/f0)` is a routine dispersion term. Evaluation now retries on the
  complex plane at exactly the positions that came back NaN; every value the
  real-axis pass produced is kept bit-for-bit, so existing formulas are
  unaffected.
- **NaN was passed on as if it were a number** — into `ComplexNumber`, into
  `compute_real_value`'s R/L/C fields (whose `not np.isfinite` guard was
  written for the `inf` sentinel and swallowed NaN with it), and from there
  into the admittance matrix. NaN now raises, naming the formula, the
  frequency and the parameters. `inf` still passes through unchanged,
  because an open end and a capacitor at DC are both legitimately infinite —
  the split is between NaN and `inf`, not between `inf` and finite.
  `1/(j*2*pi*f*C)` at 0 Hz returns `inf+nan*j` from IEEE 754 complex
  division; that NaN is bookkeeping, not a failed computation, and is
  normalised component-wise (rebuilding the value as `real + 1j*imag` would
  put it straight back, since `1j * inf` is `nan+inf*j`).
- **A single un-earthed tower failed the *whole* network.** With
  `impedance_formula="nan"` — the documented way to model a bus without an
  electrode — the diagonal entry `1 / complex(inf, inf)` is `nan+nan*j`, not
  `0`: IEEE 754 complex division does not give the mathematical answer for
  an infinite operand. One NaN on the diagonal makes the LU factorisation
  report a singular matrix, so a chain of three towers of which one has no
  electrode did not return a result with that tower at 0 A — it failed
  entirely, with a message about a missing path to reference earth, while
  the other two towers were properly earthed. Infinite impedances are now
  short-circuited to zero admittance at all eight reciprocal sites (bus
  diagonal, branch self-admittance, Thevenin loop closure, Norton source
  injection, phase-side admittance in the automatic split, mutual Norton
  injection, per-bus earth current, branch current).

### Changed (Audit pass 12 — the diagnosis for an unsolvable nodal system)

- **NaN in `Y` or `i` is now reported as a computation error before the
  solve**, naming the buses and branches whose stored impedance is NaN
  (`self` and `mutual` told apart) — instead of reaching scipy and coming
  back as "singular matrix", which sends the engineer looking for a topology
  error. The injection vector is checked as well as the matrix: a NaN in a
  *mutual* impedance is a Norton current and never touches `Y`.
- **"No path to reference earth" now says which buses were looked at and
  why each one failed to provide a reference**, split into `Z = 0`,
  infinite (open-end sentinel), NaN, and no impedance stored at that
  frequency. The `Z = 0` group carries the explanation that matters most:
  in this model a zero grounding impedance contributes *no* admittance to
  the diagonal and therefore no connection at all — the opposite of what an
  engineer writing 0 for an ideal earth intends. Use a small finite value
  (e.g. 1e-6). Long lists are truncated after five names with the total
  reported, so a systematic modelling error in a 60-bus network does not
  bury the explanation. The word `Singular` is kept at the front for callers
  matching on it.
- **`compute_real_value(..., name=...)` now reaches the caller.** The field
  name was documented as "used in error messages" but was only applied to
  the two checks in that function; every failure raised inside the shared
  impedance pipeline named the formula and not the field, so a `BranchType`
  using the same expression for `R_self_formula` and `R_mutual_formula`
  produced two byte-identical messages. All of them are now prefixed with
  the field name.
- NumPy's floating-point warnings are silenced inside the formula evaluator.
  They carry no information there: "invalid value encountered in sqrt" on
  the real axis is exactly the case answered by re-evaluating on the complex
  plane, and "divide by zero" is the capacitor-at-DC case normalised to a
  clean infinity. A NaN that survives both steps is raised as a `ValueError`
  naming the formula and the frequency, which is a better signal than a
  `RuntimeWarning` pointing at generated code.

### Added (2026-07-28 — databases written by an older release are converted, not rejected)

> Written because groundinsight is published: the author holds no production
> `.db` files, but users may. Detecting an old file and refusing it keeps
> their data safe and stops their work, which is not the same thing.
> Regression tests in `tests/test_audit_pass11_migration.py` (27 tests,
> built on two independent legacy fixtures — one produced by down-converting
> a file the current code wrote, one written from the released `CREATE TABLE`
> statements with no help from current code, so a shared mistake in the
> down-converter and the migration cannot cancel out). All 29 mutations of
> the migration code are killed by their intended test.

- **`gi.migrate_database(path)` converts a database written by the
  name-keyed schema to the current `(network_name, name)` one**, and
  `gi.needs_migration(path)` classifies a file without touching it.
  `Base.metadata.create_all` only ever creates *missing tables* — it never
  adds a column — so an old file used to open without complaint and fail
  later inside a query with a bare
  `OperationalError: no such column: buses.network_name`.
- **`gi.start_dbsession()` now migrates automatically**, before the engine is
  bound. Pass `migrate=False` for the previous behaviour, which now raises a
  `RuntimeError` naming `migrate_database` and your actual file.
- The conversion is written to a temporary sibling and moved into place with
  `os.replace`, so an interruption leaves either the old file or the new one,
  never a half-converted database. The unmodified original is copied to
  `<path>.bak` first; an existing backup is never overwritten (`.bak.1`,
  `.bak.2`, …).
- **`gi.MigrationReport` reports what could not be recovered** instead of
  quietly guessing, and `report.needs_attention` is a single flag for "read
  the warnings". Everything in it is also logged at `WARNING`:
  - *shared elements* — the legacy schema stored one row per element
    *name*, so an element two networks both referenced existed once. It is
    duplicated into each network and every such name is listed. If the two
    networks meant different things by that name, the older definition was
    already destroyed on the last save, before any migration ran.
  - *path segment order* — the legacy `path_segments` table had no
    `position` column, so order is reconstructed from SQLite's `rowid`. That
    is an assumption, so it is **verified**: each path is walked bus by bus,
    and any path whose segments do not form a connected chain is listed in
    `broken_paths` for rebuilding with `gi.create_paths`.
  - *orphaned elements* (belonging to no network — the current schema has
    nowhere to put them), *dangling memberships* (referenced but with no row
    of their own), and *defaulted cells*.
- **Every converted network is loaded back before the file is swapped in.** A
  structurally valid file is not necessarily a usable one: the database
  allows `NULL` where the Pydantic model requires a value, so a legacy row
  without `specific_earth_resistance`, or one naming a bus type the file
  never contained, converts cleanly and then raises on the user's first
  `load_network_from_db`. Such networks are named in `report.unloadable`
  together with the concrete error. They are reported, not raised — one bad
  network is no reason to withhold the rest of the file.
- **A missing measurement aborts the migration rather than being invented.**
  `active` and `source_type` declare defaults in the schema, so a `NULL`
  there is filled with the schema's own value and counted in
  `defaulted_cells`. `length` and `scalings` declare none: they are data, not
  schema conventions. Guessing `length = 0.0` would hand back a network that
  solves without complaint and reports a completely different earth potential
  rise, with nothing in the output to show a number was made up. The
  migration stops instead and names the row; the original file is untouched,
  so refusing is recoverable and a wrong number would not be.

### Changed (2026-07-28 — final temperatures split into an uninsulated and an insulated regime)

> The catalogue previously shipped placeholder values from mixed sources —
> the National Grid Earthing Technical Specification Table 5a and
> IEC 60364-5-54, in one table. It now has exactly two regimes with one
> source each: **EN 50522 Table 2**, as printed in the edition held by the
> author, for a conductor that is *not* insulated, and **IEC 60364-5-54
> Table 54.2** for one that is. Cross-checked against the melting points of
> the coatings: tin melts at 231.9 °C, which is why tinned copper is the one
> uninsulated entry below 300 °C; zinc melts at 419.5 °C, so 300 °C for
> galvanised steel is not coating-limited.

- **`IEC60949_MATERIALS["Steel"]["theta_final_default_C"]` lowered from
  400 °C to 300 °C.** `θ_f` enters the IEC 60949 material constant `k` under
  a logarithm, so the old, higher default produced a *larger* `k` and
  therefore permitted *more* current — the unsafe direction for a limit
  check. Studies that relied on it can be reproduced by passing
  `theta_final_C=400.0` explicitly.
- **Uninsulated conductors follow EN 50522 Table 2:** 300 °C for bare
  copper, aluminium and steel and for galvanised steel, 150 °C for tinned
  copper. The previous bare values (Cu 405 °C, Al 325 °C) came from the
  National Grid table and were more permissive.
- **Insulated conductors are capped by their insulation, not by Table 2.**
  `final_temperature(material, "PVC")` returns 160 °C, `"XLPE"` and `"EPR"`
  return 250 °C — for every conductor material, because it is the insulation
  that fails first and it does not care what the metal is. These are the
  values of the new `gi.CABLE_INSULATION_LIMITS`, which `FINAL_TEMPERATURES`
  splices in rather than copying, so a corrected cap cannot reach one
  material and miss another.
- **`"PE"` is deliberately not tabulated** and raises. Neither source states
  a value for it that this package can cite, and both neighbouring answers —
  300 °C from the uninsulated side, 160 °C from the PVC row — would be a
  number the docstring cannot point at a table for. Pass `theta_final_C`
  explicitly instead. Physically impossible pairings (`("Al", "tinned")`,
  `("Cu", "galvanized")`) raise for the same reason.

### Fixed (2026-07-28 — the path set went stale after adding a fault or a source)

> Found by asking, per input that `define_paths` reads, whether
> `_active_topology_fingerprint` actually covers it. Regression tests in
> `tests/test_audit_pass10_topology_and_transient.py` (T1); every fix in
> this batch was mutation-tested by reverting the hunk and checking that
> exactly the intended tests fail.

- **A fault created after the first `run_fault` was never given paths, and
  every bus then reported 0 V.** `define_paths` enumerates one path set per
  `(source, fault)` pair, so the excitation is part of the path set's input
  — but the fingerprint only looked at the active buses and branches.
  Adding a fault therefore left it unchanged, `_needs_path_rebuild()`
  returned `False`, and `run_fault` reused paths that all terminate at the
  *old* fault bus. No path reached the new one, and the result was 0 V at
  every bus — returned as a normal result, with no warning. The fingerprint
  now also covers the `(name, bus)` of every source and every fault.
- **The same omission made `find_max_rho_scaling` over-estimate the
  admissible soil resistivity by a factor of ~3000.** The bisection swept ρ
  against an EPR stuck at 0 V, never exceeded `u_max`, and returned the
  upper bracket (100000 Ω·m instead of 33.5 Ω·m) with `iterations: 0`. Read
  as a design result that is the non-conservative direction: it declares a
  site adequate that is not.
- **Adding a source after the first solve failed the same way**, but less
  visibly — the second source simply contributed nothing and the EPR came
  out plausible instead of zero (87.07 V against the correct 69.83 V at the
  source bus).

### Fixed (2026-07-28 — parallel branches collapsed in two topology keys)

> Found by constructing two topologies that differ only in the multiplicity
> of a parallel branch and checking whether the caches told them apart.
> Regression tests in `tests/test_audit_pass10_topology_and_transient.py`
> (T2).

- **`Network._active_topology_fingerprint` and
  `PathFinder._compute_topology_key` both stored connectivity as a
  `frozenset` of bare `(from_bus, to_bus)` pairs.** A set of endpoint pairs
  has no multiplicity, so parallel branches are indistinguishable from a
  single one. With `L3: A→D` and `L4: A→D` present, rewiring `L4` to `A→B`
  (where `L1: A→B` already exists) leaves the pair set
  `{(A,B), (B,C), (A,D)}` bit-identical. The stale path set and the cached
  adjacency list were reused, the second route `A→B→C` was never
  enumerated, and `run_fault` reported an EPR that was ~33 % off. Both keys
  now use `(branch_name, from_bus, to_bus)`.
- The branch-name term also supersedes the separate active-branch-name set
  in the fingerprint, which is now redundant.

### Fixed (2026-07-28 — the state-space transient dropped or doubled the mutual coupling)

> Found by running the transient solver against the stationary one on
> topologies where the two must agree in steady state. Regression tests in
> `tests/test_audit_pass10_topology_and_transient.py` (T3).

- **With `network.paths` empty or stale the entire Carson coupling was
  silently dropped.** The state-space solver reads its phase factors off
  `network.paths`; with no paths every factor stays zero, and the solve
  still succeeds. A transient study run straight after `build_network` —
  i.e. without a preceding `run_fault`, which is what populates the paths —
  returned a peak EPR 71 % away from the correct one (107.71 V against
  62.82 V on a two-bus reference case), with nothing in the log. The solver
  now rebuilds the paths when they are missing or stale and says so via
  `logger.warning`, pointing at `run_fault` / `create_paths`.
- **A branch shared by two parallel paths of one source received a factor
  of 2.0 instead of 1.0.** The factors were accumulated with `+=` behind a
  merely *per-path* `seen` set, which contradicts both the block's own
  header comment and the stationary reference in
  `ElectricalNetwork._compute_phase_currents_from_paths`, where the first
  path a branch appears on determines its direction and later appearances
  are ignored. On a five-bus feeder with a ring in the middle the transient
  EPR came out 32.5 % above the stationary one at the fault bus. The guard
  is now on the `(branch, source)` key, so it spans every path of that
  source. With the ring opened, so that a single path remains, both solvers
  always agreed — which is exactly why the existing tests did not catch it,
  and why the single-path case is now pinned by its own test.

### Changed (2026-07-28 — **breaking**: the SQLite schema now keys elements per network)

> Regression tests in `tests/test_audit_pass10_persistence.py` (18 tests).

- **Buses, branches, faults, sources and paths now carry a composite
  primary key `(network_name, name)`.** They were previously keyed by
  `name` alone and attached to their network through five `network_*`
  association tables. Two networks in one database that both contain a bus
  called `"A"` therefore shared a single row: saving the second silently
  overwrote the first one's impedance, and loading either returned the
  survivor. The association tables are gone; the relationships are
  ordinary one-to-many with `back_populates`. `path_segments` was promoted
  from a bare `Table` to a mapped `PathSegmentDB` with the primary key
  `(network_name, path_name, position)`.
- **This breaks existing `.db` files.** There is deliberately no
  auto-migration: `save_network` and `load_network` call
  `ensure_current_schema(session)` first, which raises a `RuntimeError`
  naming the legacy tables and pointing at the JSON round-trip
  (`save_network_json` on the old version, `load_network_json` on the new
  one) as the migration route. Silently rewriting a database whose rows may
  already have been merged across networks would destroy exactly the
  information needed to un-merge them.
- **Element order now survives the round-trip.** Buses, branches, faults,
  sources, paths and path segments carry a `position` column and every
  relationship declares `order_by`. Before, the load order was whatever
  SQLite happened to return, which reordered `Network.buses` and with it
  the Y-matrix assembly; the LU factorisation then differed in the last
  bits. With the order preserved, a save/load/re-solve is bit-identical.

### Fixed (2026-07-28 — `save_network` could leave a half-written network on disk)

> Regression tests in `tests/test_audit_pass10_persistence.py`.

- **The delete-then-insert in `save_network` was committed in two steps.**
  Overwriting an existing network first committed the deletion of the old
  rows and only then wrote the new ones, so any failure in between — a
  constraint violation, a `KeyboardInterrupt`, a full disk — left the
  database with the network gone and nothing in its place. The deletion is
  now only flushed; a single `commit()` covers the whole replacement, and
  the whole block is wrapped in `try/except` with `session.rollback()`
  before the exception is re-raised.
- **`save_network` wrote raw type rows instead of merging them.** Bus and
  branch types are a global catalogue shared by every network; they are now
  written with `session.merge(...)`, matching what `save_bustype` /
  `save_branchtype` already did, so re-saving a network with an edited type
  definition updates the catalogue rather than colliding with it.
- **Inconsistent path segments are now rejected before any database
  access.** `_validate_path_segments` raises a named `ValueError` naming
  the path and the offending segment, instead of letting a path that does
  not connect end to end reach the disk and fail on load.

### Fixed (2026-07-28 — pandapower import: schema inference, `tk_s`, line length)

> Regression tests in `tests/test_audit_pass10_pandapower.py` (19 tests, two
> of them honestly labelled as controls that survive mutation).

- **`calc_sc`'s `tk_s=1.0` signature default was read as the protection's
  clearing time.** pandapower copies `tk_s` into `net._options`
  unconditionally, so a solved net *always* reports one — even a run that
  asked for neither `ith=True` nor a duration. Since `I_adm = k·S/√t_k`
  (IEC 60949), adopting 1.0 s where the project uses 3.0 s inflates the
  admissible current by √3 ≈ 1.73 and makes an undersized conductor look
  adequate. `tk_s` is now adopted only when `options["ith"]` is truthy or
  the value differs from the 1.0 s placeholder; `n_factor` only when passed
  explicitly or differing from the neutral 1.0. Every overwrite of an
  existing fault value is logged naming old → new.
- **Three `pl.DataFrame(list_of_dicts)` sites relied on polars' schema
  inference.** Polars inspects only the first `infer_schema_length=100`
  entries, so on a net with more than 100 buses a column that is `None` for
  the first 100 rows and numeric afterwards (`kappa` where the
  zero-sequence data is incomplete, `vn_kv` for a bus missing from
  `net.bus`) aborted the frame construction with a `ComputeError`.
  `preview_pandapower_import`, `read_shortcircuit_results` and
  `apply_shortcircuit_characteristics` now declare their schemas
  explicitly, which also keeps a wholly absent column typed `Float64`
  instead of collapsing it to `pl.Null`.
- **A missing line length was silently replaced by 1.0 km.** A fabricated
  length propagates into every impedance (`Z ~ … · l`) with no trace in the
  result. The fallback is unchanged but now always accompanied by a
  `logger.warning` naming the line. A positive length is passed through
  verbatim, so the documented `length = length_km` contract holds exactly.
- **Zero and negative line lengths are now rejected.** They previously fell
  back to 1.0 km as well. A negative length flips the sign of the self and
  mutual impedance; a zero length is worse than it looks, because
  `electrical_network` skips a branch whose impedance is exactly zero and
  the branch then behaves as an *open circuit* rather than a short
  (measured: `length=1e-9` gives EPR A = B = 107.19 V, `length=0.0` gives
  A = 1000.0 V, B = 563.29 V — a discontinuity, not a limit). The preview
  reports `zero_length` and `negative_length` separately so the zero case
  can be relaxed later; `from_pandapower` raises a `ValueError`.

### Fixed (2026-07-28 — `str()` on two result objects raised `AttributeError`)

> Found by an AST sweep of every `__str__` / `__repr__` in the model modules
> for attribute names that no longer exist. Regression tests in
> `tests/test_thermal_optional.py` (group 6).

- **`ResultReductionFactor.__str__` and `ResultGroundingImpedance.__str__`
  referenced fields that do not exist.** Both read `self.reduction_factor`
  resp. `self.grounding_impedance`; the field on either class is called
  `value`. Any `print(result.reduction_factor)`, f-string or log line
  carrying one of these objects therefore raised
  `AttributeError: 'ResultReductionFactor' object has no attribute
  'reduction_factor'`.
- **Why it went unnoticed for so long.** pydantic generates its own
  `__repr__`, and `__repr__` does not fall back to a broken `__str__`. A
  bare `res.grounding_impedance` cell in a notebook — the usual way these
  objects are looked at — renders through `__repr__` and looked perfectly
  healthy. Only the explicit `str()` path was broken. The new tests assert
  that `str()` succeeds *and* that the printed text actually contains the
  value, so a `__str__` silently degraded to the pydantic default would
  fail too.
- Both messages now also carry `fault_bus`, which is what tells two results
  of the same network apart.

### Fixed (2026-07-28 — a half-declared conductor was silently left unassessed)

> Found by probing the optionality contract layer by layer; tests in
> `tests/test_thermal_optional.py` (group 3b, 9 tests).

- **An element carrying only one half of its thermal data was skipped
  without a word.** `check_conductor_limits` / `check_node_limits` assess a
  conductor only when *both* the material and the cross-section are
  present. Declaring nothing is a legitimate modelling choice and stays
  silent. Declaring a `conductor_material` **without** a
  `cross_section_mm2` — or an `earthing_conductor_cross_section_mm2`
  without an `earthing_conductor_material` — is different: the user has
  clearly begun to describe the conductor and believes it is being checked.
  The row was emitted with `within_limit = None`, which at a glance is
  indistinguishable from a pass. Verified silent in all four variants
  (branch × 2, node element × 2) before the fix.
- Such elements now raise a `logging.WARNING` naming the element **and the
  field that is missing**, once per check with all offenders in one
  message. Complete and fully undeclared elements stay silent, so the
  warning channel does not become noise.
- The assessment itself is unchanged: a half-declared element is still not
  judged. Guessing a cross-section would be the unsafe direction.

### Internal (2026-07-28 — the thermal assessment is opt-in, and now provably so)

> `tests/test_thermal_optional.py` (28 tests). Apart from the
> half-declared warning above, no behaviour change; the tests pin down a
> contract that was previously only implied.

- **Contract:** a grounding study is useful long before anybody has decided
  on conductor materials and cross-sections, so every thermal field on
  `BusType` and `BranchType` is optional and the whole electrical
  calculation must run without them. The tests fix six layers of that
  promise:
  1. `run_fault` on a network with *no* thermal and *no* IEC 60909 metadata
     produces the complete result — bus voltages, `ia`, `i_inj`, branch
     currents, `Z_G` and `r`.
  2. The thermal fields are **inert**: a network with every field declared
     and one with none give bit-identical results
     (`max |difference| = 0.0` over all bus and branch quantities).
  3. `check_conductor_limits` / `check_node_limits` degrade into a pure
     *current report*: every branch resp. every active bus is still listed
     with its currents, and only the judgement columns
     (`I_admissible_A`, `utilization`, `within_limit`) are `None`.
  4. Such an unassessable frame keeps its declared dtypes, so the usual
     `df.filter(pl.col("within_limit") == False)` gate still works and
     returns zero rows instead of raising.
  5. Nothing is logged when nothing is declared — an undeclared element is
     a modelling choice, not a defect. (Asserted as "no `WARNING` record at
     all", which is what makes the half-declared warning above
     distinguishable.)
  6. `BusType` / `BranchType` without thermal fields survive the JSON and
     SQLite round-trips unchanged.
- **Documented boundary:** the *excitation* is not optional. Once a check is
  requested, `t_k` and the DC characterisation (`kappa` or `r_to_x`) must
  come either from the arguments or from `Fault.t_k_s` / the sources, and
  their absence raises a `ValueError` naming the remedy. Tests assert the
  message, not just the exception type.

### Fixed (2026-07-28 — a thermal check on an incomplete result reported "no violations")

> Found while validating `notebooks/22_node_thermal_limits.ipynb`, which
> built an `ElectricalNetwork` to display `Y`, `i` and `u` and thereby
> silenced the branch check for the rest of the notebook. Regression tests
> in `tests/test_incomplete_results_guard.py` (11 tests).

- **`check_conductor_limits` / `check_node_limits` could silently report a
  violating network as clean.** Both build their frame by looping over the
  *stored* result (`result.branches` / `result.buses`), so a missing entry
  produced no row — and a missing row is indistinguishable from a passing
  one. Reproduced on a 16 mm^2 steel shield at **292 % utilisation**: after
  the result was emptied, two of the three natural safety gates reported
  clean (`[r for r in df.iter_rows(named=True) if r["within_limit"] is
  False]` -> `[]`, and `df.is_empty()` -> `True`), and the package's own
  `logger.warning("Thermal limit exceeded ...")` channel went silent. The
  third gate, `df.filter(pl.col("within_limit") == False)`, raised
  `ColumnNotFoundError` instead, because `pl.DataFrame([])` carries no
  schema. Both checks now raise `ValueError` naming the missing
  branches/buses and the remedy.
- **How the half-built result arises through the public API.**
  `ElectricalNetwork.solve_network()` replaces `network.results[fault]` with
  a fresh `Result` carrying bus rows only; the branch rows are filled in by
  `compute_branch_currents()`, which `run_fault` calls immediately
  afterwards. Solving a hand-built `ElectricalNetwork` on its own — the
  documented way to inspect the nodal system — therefore discarded the
  branch results of the preceding `run_fault`. Clearing them is kept
  (stale branch currents beside fresh bus voltages would be silently
  inconsistent) and is now documented as a `.. warning::` on
  `solve_network`, together with the non-mutating way to inspect `Y`, `i`
  and `u`.
- The guard also catches **staleness in everyday use**: a branch added or a
  bus activated after `run_fault` would previously have been skipped without
  a word. Expectations respect the modelling rules — every branch is
  reported (inactive and open ones with `i_s = 0`), only *active* buses are,
  since inactive buses leave the nodal system entirely.
- **Output schema is now declared explicitly** (`_BRANCH_SCHEMA`,
  `_NODE_SCHEMA`). A legitimately empty frame — a network without branches —
  keeps all 15 (resp. 18) columns and stays selectable and filterable
  instead of collapsing to shape `(0, 0)`. All-null columns keep their
  dtype too: a node whose `BusType` declares no element now yields
  `material: String` / `within_limit: Boolean` rather than polars' inferred
  `Null`.

### Added (2026-07-28 — node thermal limits: earthing conductor vs earth electrode, roadmap F4)

> New feature demonstrated in `notebooks/22_node_thermal_limits.ipynb`;
> tests in `tests/test_node_thermal_limits.py` (33 tests). Full suite
> green (392 passed). Third increment of the "conductor thermal-limit
> check" roadmap item: F1 sized the shield *between* buses, this one
> sizes the two grounding elements *at* a bus.

EN 50522 / IEC 61936-1 size the **earthing conductor** (*Erdungsleiter*)
and the **earth electrode** (*Erder*) for different currents, and the
solver did not expose the first of them at all. Three physically distinct
currents meet at a grounding bus, and mixing them up is the classic sizing
error — in the verification network the two differ by a factor of 41.

- **`ResultBus.i_inj` / `ResultBus.i_inj_freq`** report the source-only
  nodal injection: the current a lumped earthing conductor carries into
  the grounding system at a source bus, or out of it at the fault bus, and
  zero everywhere else. This is *not* `ResultBus.ia = u_EPR / Z_B`, which
  is only the share dissipated into the soil through the electrode.
  `i_inj` is snapshotted before `_add_mutual_currents` mutates the
  injection vector, so it deliberately **excludes** the mutual Norton
  equivalents — those model a distributed induced EMF along the line, not
  a current entering the node through a lumped conductor. Verified on a
  3-bus chain: source bus `+1000+0j` A, fault bus `-1000+0j` A, middle bus
  `0`, while `max|i_vector - source_injections| = 923.077 A`. The nodal
  balance including the mutual terms remains exact
  (`ia = i_vector + sum_br (u_other - u_self)*Y_self`, residual <= 1.045e-13).
  Both fields default to `0.0` / `{}`, so results stored before this
  release still load — re-run `run_fault` to populate them.
- **`BusType`** gains ten fields, five per element:
  `earthing_conductor_material`, `_cross_section_mm2`, `_theta_initial_C`
  (20 °C), `_theta_final_C`, `_current_split` (1.0), and the same five
  under `electrode_`. All round-trip through JSON and SQLite; the new
  columns are nullable and map `NULL` back onto the pydantic default, so
  rows written by an earlier version load unchanged. A cross-section
  <= 0 and a `current_split` outside `(0, 1]` are rejected at the model
  level — a factor above 1 is not a split but an error.
- **`gi.check_node_limits(network, fault, t_k=, *, kappa=/r_to_x=, n=, f=,
  aggregation=, elements=)`** applies
  `I_th = I_rms * current_split * sqrt(m + n)` (IEC 60909-0) against
  `I_adm = k*S/sqrt(t_k)` (IEC 60949) per bus and element, and returns a
  long-format Polars frame (`bus_name`, `element`, `I_rms_A`,
  `current_split`, `I_conductor_A`, `i_p_A`, `kappa`, `m`, `n`, `t_k_s`,
  `I_th_factor`, `I_th_A`, `material`, `cross_section_mm2`, `k`,
  `I_admissible_A`, `utilization`, `within_limit`). Every bus is reported;
  an element the `BusType` does not declare keeps its current columns
  filled and gets `within_limit = None`, so it can be sized by hand
  without re-running. Verified against an independent hand calculation of
  all 18 columns.
- **`current_split`** is a free factor in `(0, 1]`, not derived
  automatically: `1.0` for a single conductor, `1/N` for N parallel legs,
  `0.5` for a ring fed at one point, or an IEEE Std 80 division factor.
  The split depends on geometry the nodal model does not carry, so
  guessing it would be worse than asking for it.
- **`gi.final_temperature(material, covering)`** and
  **`gi.FINAL_TEMPERATURES`** provide theta_f with the source named inline
  per entry (National Grid ETS Table 5a for bare buried conductors,
  IEC 60364-5-54 Table 54.2 for PVC / XLPE). The catalog is deliberately
  **incomplete rather than filled with plausible-looking numbers**;
  `final_temperature` raises for anything missing and names EN 50522
  Table 2 as the source to consult.

The IEC 60909 excitation is resolved by a new shared helper
`_resolve_sc_inputs`, lifted verbatim out of `check_conductor_limits`, so
the branch and node views of one fault can no longer drift apart; a
regression test pins `kappa`, `m` and `sqrt(m+n)` equal across both.

Not changed, deliberately: the `IEC60949_MATERIALS["Steel"]` default of
400 °C is *higher* — i.e. more permissive, the unsafe direction for a
limit check — than the 300 °C the National Grid table gives for bare
buried steel. Moving a default silently would move every existing study,
so the discrepancy is documented as a warning in the source and in
`docs/api/analysis.md` instead, pending a check against EN 50522 Table 2.

Still open on this roadmap item: mechanical (electrodynamic `i_p`) limits,
and a dedicated `EarthElectrode` model for several electrodes per bus.

### Added (2026-07-19 — IEC 60909 short-circuit characteristics, roadmap F2/F3)

> New feature demonstrated in
> `notebooks/21_shortcircuit_characteristics.ipynb`; tests in
> `tests/test_shortcircuit_60909.py` (37 tests). Full suite green
> (359 passed). Second increment of the "conductor thermal-limit check"
> roadmap item: it removes the hand-entered `kappa` and `T_k` that F1 still
> required, by importing them from a solved pandapower short-circuit case.

- **`gi.read_shortcircuit_results(net_pp, *, t_k_s=, n_factor=1.0, f=50.0,
  buses=)`** reads a solved `pandapower.shortcircuit.calc_sc` case as IEC
  60909 quantities and returns a Polars DataFrame (`pp_bus_index`,
  `bus_name`, `vn_kv`, `fault_type`, `case`, `i_k_a`, `r1_ohm`, `x1_ohm`,
  `r0_ohm`, `x0_ohm`, `r_to_x`, `kappa`, `kappa_origin`, `t_k_s`,
  `n_factor`, `m`, `i_p_a`, `i_th_a`). Fault type, case and clearing time
  default to `net._options`, so a solved net is self-describing. Currents
  are reported in **amperes**, not kA.
- **`gi.apply_shortcircuit_characteristics(network, sc_results, fault, *,
  pp_bus=/bus_name=, sources=, set_source_values=False, frequency=,
  t_k_s=, n_factor=)`** writes those quantities onto a groundinsight model:
  each feeding source receives the loop's `r_to_x` / `kappa` and a share of
  `I_k''` proportional to its present injection, while `T_k` and `n` go onto
  the `Fault` (they describe the protection, not the infeed). Returns an
  audit frame including `i_k_previous_a`, so a review sees exactly what
  changed. `set_source_values=False` by default — an existing excitation is
  never silently overwritten.
- **`Source`** gains `i_k_a`, `r_to_x`, `kappa` and **`Fault`** gains
  `t_k_s`, `n_factor`, all validated (`kappa` in `(1, 2]`, `n` in `(0, 1]`,
  `r_to_x >= 0`) and round-tripping through JSON and SQLite;
  `gi.create_source` / `gi.create_fault` accept them directly. These are
  *metadata*: they never enter the linear solve.
- **`gi.resolve_fault_sc_characteristics(network, fault, *, frequency=,
  aggregation="weighted")`** resolves one effective `kappa` for a fault from
  all feeding sources and returns a `FaultShortCircuitData`. The default
  current-weighted mean `kappa_eff = Σ(kappa_i·I_i)/Σ(I_i)` reproduces the
  sum of the individual peaks exactly (verified to 1.6e-16);
  `aggregation="max"` is the strictly conservative variant.
- **`gi.check_conductor_limits`** now falls back to `Fault.t_k_s`,
  `Fault.n_factor` and the resolved `kappa` when they are not passed
  explicitly, and reports two new columns `i_p_A` and `t_k_s`. Explicit
  arguments still win, so sensitivity studies are unaffected.
- **`gi.peak_short_circuit_current`**, **`gi.thermal_equivalent_current`**
  exported; the 60909 primitives now live in
  `groundinsight.analysis.shortcircuit` and are re-exported from
  `analysis.thermal` for backwards compatibility.

Two deliberate deviations from pandapower, both pinned by tests:
`ip_ka` / `ith_ka` are entirely `NaN` for `fault="1ph"` — the case that
matters for grounding — so they are derived here; and `I_th` is always
recomputed with our `m`, because pandapower's `_calc_ith` sets `m = 0` for
`kappa > 1.99` where the analytic limit is `m = 2`, which under-estimates the
thermal stress. The `R/X` driving `kappa` is that of the earth-fault loop
`2·Z1 + Z0`, not `R1/X1`; where pandapower does publish `ip_ka` its
topology-aware `kappa` is preferred and recorded as `kappa_origin`.

Still open on this roadmap item: mechanical (electrodynamic `i_p`) limits and
node/bus-earth thermal limits.

### Docs (2026-07-19 — F1/F2/F3 pages)

- `docs/api/analysis.md` gained a **Conductor thermal limits (IEC 60949 /
  IEC 60909-0)** section, including the *what is superposed and what is not*
  rule with the weighted-`kappa` formula; the page intro no longer claims the
  subpackage covers only the inverse rho problem.
- `docs/api/io.md` gained a **Short-circuit characteristics (IEC 60909-0)**
  section covering `read_shortcircuit_results` /
  `apply_shortcircuit_characteristics` and the two deliberate deviations from
  pandapower; the intro now separates the topology import from the result
  import.
- `docs/api/index.md` and `docs/index.md` list the new entry points, so the
  overview pages no longer describe a pre-F1 feature set.

All four pages verified with a local `mkdocs build` — no new warnings, math
renders through arithmatex.

### Fixed (2026-07-19 — dead "Research notebooks" nav section in `mkdocs.yml`)

The `nav:` block advertised a **Research notebooks** section with 16
`../notebooks/*.ipynb` entries. None of them ever reached the published site.
MkDocs only collects files below `docs_dir`, so every one of the 16 was
reported as

```
WARNING - A reference to '../notebooks/01_smoke_test.ipynb' is included in
          the 'nav' configuration, which is not found in the documentation
          files.
```

and then dropped from the navigation. The section was removed and replaced by
a comment recording why the path shape cannot work.

Evidence, in the order it was collected:

- **Reproduction.** `mkdocs build` on the unmodified config emits exactly 16
  warnings, one per entry; after the removal it emits 0. Under `--strict` the
  old config exits 1, the new one exits 0.
- **Control test separating "file missing" from "path unresolvable".** A real
  notebook was copied to exactly `notebooks/01_smoke_test.ipynb` and the build
  repeated — the warning persisted verbatim. The cause is therefore structural
  (outside `docs_dir`), not a missing file, and no `mkdocs-jupyter` setting can
  repair it.
- **Why it stayed unnoticed.** `.github/workflows/docs.yml` runs
  `mkdocs gh-deploy --force --clean --verbose` *without* `--strict`, so the
  build stayed green while the section silently vanished.
- **Nothing is lost by the removal.** `/notebooks` is gitignored
  (`.gitignore:144`), so the files never reach a CI checkout in the first
  place; `git ls-files notebooks/` tracks only 01–08, which predate the ignore
  rule. The live site at <https://ce1ectric.github.io/groundinsight/> was
  checked and has no "Research notebooks" section.
- **Stale on top of broken.** The removed comment claimed "16 ipynb files
  (01..13 plus the four audit-pass demonstration notebooks)" while listing only
  14–16; the working tree meanwhile holds notebooks up to 21. The list had
  drifted out of sync in addition to never rendering.

Curated notebooks belong in `docs/examples/` (the Examples section, which does
render). The research notebooks stay a local working artefact.

### Added (2026-07-19 — conductor thermal-limit check, roadmap F1)

> New feature demonstrated in `notebooks/20_thermal_limits.ipynb`; tests in
> `tests/test_thermal_limits.py` (18 tests). Full suite green (322 passed).
> First increment of the "conductor thermal-limit check" roadmap item — the
> equipment-integrity counterpart to the planned EN 50522 touch-voltage
> (person-safety) assessment.

- **`gi.check_conductor_limits(network, fault, t_k, *, kappa=/r_to_x=, n=1.0, f=)`**
  compares every grounding branch's thermally equivalent short-time current
  against its adiabatic thermal limit and returns a Polars DataFrame
  (`I_s_rms_A`, `kappa`, `m`, `n`, `I_th_A`, `material`, `cross_section_mm2`,
  `k`, `I_admissible_A`, `utilization`, `within_limit`). `I_th = I_s_rms *
  sqrt(m + n)` (IEC 60909-0) is applied to the *superposed* AC RMS shield
  current — linear superposition first, the non-linear peak/thermal factor on
  the aggregate, so `I_th` is never superposed directly — and
  `I_adm = k * S / sqrt(t_k)` (IEC 60949). A branch is checked only when its
  `BranchType` defines both `conductor_material` and `cross_section_mm2`.
- **`BranchType`** gains `conductor_material` (`"Cu"` / `"Al"` / `"Steel"`),
  `cross_section_mm2`, `theta_initial_C` (default 20 °C) and `theta_final_C`
  (default per material — bare-earthing-conductor values, EN 50522). All four
  round-trip through JSON and SQLite.
- **IEC helpers** `gi.iec60949_k`, `gi.iec60909_m`, `gi.kappa_from_r_to_x` and
  the material catalog `gi.IEC60949_MATERIALS` (base constant `K` + `β` for
  Cu/Al/Steel, verified to reproduce the standard `k` tables — e.g. copper
  XLPE `k = 143`, PVC `k = 115`, aluminium XLPE `k = 94`).

Still open on this roadmap item: mechanical (electrodynamic `i_p`) limits and
node/bus-earth thermal limits. (The automatic `kappa` / `T_k` from a
pandapower `calc_sc` import, listed as open when this entry was written, has
since been delivered — see the F2/F3 entry above.)

### Fixed (Audit pass 9 — implemented 2026-07-19)

> Resolved on the current audit branch and demonstrated in
> `notebooks/19_audit_pass9_fixes.ipynb`. Regression coverage lives in
> `tests/test_audit_pass9_fixes.py` (11 tests, all green); the full suite
> stays green (303 passed).

- **`run_fault` rebuilds paths when the active topology changed.** It
  previously rebuilt paths only when `network.paths` was empty, so flipping
  `Bus.active` / `Branch.active` in place (or rewiring a branch) and calling
  `run_fault` again silently reused stale paths while the Y-matrix was rebuilt
  from the current flags — a wrong EPR with no warning. `Network` now carries an
  active-topology fingerprint (active buses, active branches, connectivity)
  recorded by `define_paths`; `run_fault` rebuilds via `invalidate_paths()`
  whenever it changed. The `outage_context` path was already safe.
- **Transient solvers treat the source waveform as the literal injection.** The
  state-space solver multiplied the mutual-coupling phase current by
  `fault.scalings` but left the shield injection unscaled, so the result moved
  with `scalings` (up to ~2x) even though the waveform was unchanged; the FFT
  solver ignored `scalings` entirely. `scalings` (a frequency-domain concept for
  the stationary solve) is now consistently **not** applied by either transient
  solver. The state-space solver additionally warns when a purely resistive
  branch carries `R_mutual`/`M_mutual` (its mutual term is only modelled for
  inductive branches).
- **The inverse rho / rho-f routines restore network state.**
  `find_max_rho_scaling`, `evaluate_max_epr_under_k` (and thus
  `find_max_rho_f_scaling` / `select_rho_f_from_catalog`) left the shared
  `Network` mutated: a dangling `active_fault` pointing at a deleted temporary
  fault on a fresh network, and a `results` entry for a reused pre-existing
  fault overwritten with the search's probe EPR. They now snapshot and restore
  `active_fault` (through `set_active_fault`, re-syncing the `_active` flags) and
  the affected `results` in their `finally` blocks. The returned figures were
  already correct.
- **`Network.results` survive the SQLite round-trip.** `NetworkDB` gained a
  `results` JSON column (serialised via `Result.model_dump`), so a solved
  network saved to and loaded from SQLite keeps its per-fault results — the JSON
  backend already did.
- **`Fault.active` survives the JSON round-trip.** `Network` gained an
  after-validator that re-syncs each fault's read-only `active` flag with
  `active_fault` on construction, so `model_validate_json` no longer resets it
  to `False` (the SQLite path was already correct).
- **Duplicate pandapower line names no longer abort the import.**
  `from_pandapower` now disambiguates line names by index (mirroring the bus
  handling), so two equally-named lines import as distinct branches instead of
  raising on `add_branch`; `preview_pandapower_import` and the commit agree.
  Non-positive `length_km` values fall back to the 1.0 km default.
- **Singular / floating networks raise a clear error.** `solve_network` now
  detects a floating network (no active bus referenced to earth) structurally
  before the solve — a tolerance-free check independent of scipy's
  version-dependent handling of a singular matrix (`splu` may raise a
  `RuntimeError`, return a non-finite solution, or return an arbitrary finite
  one) — and raises a `ValueError` naming the frequency instead of a raw
  traceback or a downstream divide warning. The sparse solve keeps
  `RuntimeError` / non-finite backstops for any residual singular case.

### Fixed (Audit pass 8 — implemented 2026-07-19)

> Resolved on the current audit branch and demonstrated in
> `notebooks/18_audit_pass8_fixes.ipynb`. Regression coverage lives in
> `tests/test_audit_pass8_fixes.py` (21 tests, all green); the full suite
> stays green (271 passed).

- **`Network.define_paths` no longer shadows `(source, fault)` pairs that
  share a branch route.** The dedup signature keyed only on the branch-name
  sequence, so two faults on the same bus — or two sources on the same bus —
  collided and one path was dropped. The shadowed fault then solved to
  `EPR = 0` and a shadowed source was silently ignored, i.e. the tool
  *underestimated* the earth-potential rise (safety-relevant). The signature
  now includes `source_name` and `fault_name`. Verified: two faults on one bus
  give identical non-zero EPR; two equal sources on one bus double the drive;
  the single-fault path count is unchanged.
- **Open-end (`inf`) values survive the JSON round-trip.** `ComplexNumber`
  (and `Bus` / `Branch` / `Network`) now set
  `model_config = ConfigDict(ser_json_inf_nan="constants")`. Pydantic's
  default serialised non-finite floats as JSON `null`, so an open-end
  impedance `inf` (from the documented `"nan"` formula) reloaded as `nan` and
  poisoned the solve, and an `inf` in a lumped RLC dict raised a
  `ValidationError` on reload. JSON now emits `Infinity`/`NaN` and reloads them
  intact, matching the SQLite backend. (Note: `Infinity`/`NaN` are a
  Python-`json` extension, not strict RFC-8259 — a deliberate trade-off to keep
  the two backends consistent and avoid silent data loss.)
- **Impedance/RLC formula strings are no longer an arbitrary-code-execution
  sink.** `utils.validations.assert_safe_formula` tokenises every formula and
  rejects dunder names, a denylist of dangerous builtins (`eval`, `open`, …),
  attribute access (`.`) and string literals *before* it reaches
  `sympy.sympify` (which evaluates its input as Python). Applied in both
  `validate_impedance_formula_value` and
  `impedance_calculator._compile_formula`, so `load_network_from_json` and the
  DB load path are covered. Ordinary free symbols (`rho`, `f`, `l`, the `roh`
  typo, `pi`, `NaN`, scientific notation, …) are unaffected.

### Fixed (Audit pass 7 — implemented 2026-05-24)

> The bullets in this sub-section have been **resolved** on branch
> `feature/audit-pass7-fixes` and are demonstrated end-to-end in
> `notebooks/17_audit_pass7_fixes.ipynb`. Regression coverage lives
> in `tests/test_audit_pass7_fixes.py` (13 tests, all green). The
> Pass-7-Backlog bullet *„db_session synonym carries cross-API
> maintenance debt"* and the Pass-7 *Roadmap*-bullet
> *„ADR-0013 — Cross-repo `show_versions` convention"* on the
> `groundinsight` side are now closed.

- **`groundinsight._set_session(new)`** is the new single source of
  truth for the module-level scoped session globals. ``gi.session``
  and the historic alias ``gi.db_session`` are pinned in lock-step:
  every code path that rebinds the session (``start_dbsession``,
  ``close_dbsession`` and any planned ``swap_dbsession`` context
  manager) routes through ``_set_session`` instead of assigning the
  globals directly. Removes the cross-API drift risk flagged in
  `audit-report-changelogs-2026-05-18-pass7.md` (item *„db_session
  synonym carries cross-API maintenance debt"*). The two helpers are
  now sole consumers of the global ``engine`` / ``SessionLocal``
  names; only ``_set_session`` touches ``session`` / ``db_session``.
- **`groundinsight.show_versions()`** is the cross-repo convention
  helper introduced for the Pass-7 *Roadmap* item *„ADR-0013 —
  Cross-repo `show_versions` convention"*. Returns a dict with at
  least ``{"groundinsight": __version__, "python": ..., "platform":
  ...}``; the peer packages ``groundfield`` and ``groundmeas`` are
  added when importable. Returns a fresh mapping on every call so it
  is safe to mutate. Listed in ``__all__``.
- **`docs/api/database.md`** has new sections "Session globals:
  `gi.session` and `gi.db_session`" (documents the lock-step
  contract enforced by ``_set_session``) and "Cross-repo version
  helper: `gi.show_versions()`" (documents the new API and shows
  the example output).

### Fixed (Audit pass 6 — implemented 2026-05-18)

> The bullets in this sub-section have been **resolved** on branch
> `feature/audit-pass6-fixes` and are demonstrated end-to-end in
> `notebooks/16_audit_pass6_fixes.ipynb`. Regression coverage lives
> in `tests/test_audit_pass6_fixes.py` (18 tests, all green). The
> matching backlog bullets in the *sixth 2026-05-14 review pass* and
> *seventh 2026-05-18 review pass* sub-sections below are left in
> place so the audit reports stay self-consistent until the next
> release is cut.

- **`pathfinder._GRAPH_CACHE` / `_FIND_PATHS_CACHE`** are now wrapped in
  `OrderedDict` with a configurable LRU cap (default `256`). The cap is
  surfaced via the new helpers `gi.set_pathfinder_cache_size(n)` /
  `gi.get_pathfinder_cache_size()`. A 100-scenario outage sweep that
  previously accumulated 100 cache entries indefinitely now stays
  bounded; dashboard authors can raise the cap for larger working sets
  or lower it for tests that want to pin the eviction policy.
- **`simulation/outage.outage_context`** clears the per-network
  pathfinder cache on **exit** as well as on entry. Combined with the
  new LRU cap this closes the multi-hundred-megabyte memory leak that
  appeared on long dashboard sessions over many outage scenarios.
- **`simulation/outage.outage_context` is nestable** ("re-entrant
  safe"). Each `with` block records a per-level baseline (captured
  *before* it flips its targets) on a module-level
  `_OUTAGE_BASELINE_STACK[id(network)]` list, and only reverts what
  *that* level touched on exit. Nested blocks no longer capture the
  already-modified outer state as their own baseline.
- **`Network._validate_frequencies` docstring** matches the
  implementation: DC (`f = 0`) is permitted, only strictly-negative
  values are rejected. The new error message reads
  ``Network.frequencies must be >= 0; got ... (``f == 0`` for the DC
  bin is permitted).``.
- **`Network.frequencies`** emits a new
  `NetworkFrequencyOrderWarning(UserWarning)` when the input is not
  strictly increasing. The FFT transient solver in
  `simulation/transient.TransientStudy` uses the *order* of
  `Network.frequencies` to map spectral bins, so `[100.0, 50.0]`
  silently produced a transient with the spectral bins reversed.
  Mirrors `groundfield.solver.engine.EngineFrequencyOrderWarning`
  introduced in the Pass-5 `groundfield 0.5.0` cut. The new category
  is re-exported as `gi.NetworkFrequencyOrderWarning`.
- **`Network.invalidate_paths()`** is now an **atomic rebind**:
  `self.paths = {}` instead of `self.paths.clear()`. External
  snapshots taken with `saved = dict(network.paths)` before the call
  now survive — mirrors the Pass-4 atomic-rebind fix in
  `analysis.inverse_rho_f.evaluate_max_epr_under_k`.
- **`gi.set_active_fault(network, fault_name, keep_results=False)`**
  is a new top-level factory that propagates the Pass-5
  `keep_results=` keyword to the bound method on `Network`. The
  documented public-API form now matches the bound method's
  capability.
- **`__version__`** bumped from `0.4.0` to `0.5.0` in
  `src/groundinsight/__init__.py` and `pyproject.toml`. The release
  cut was four passes overdue; the `0.5.0` minor release now bundles
  the Pass-4 + Pass-5 + Pass-6 implementation blocks (transient
  state-space solver, capacitance support, pandapower importer
  hardening, `Network.invalidate_paths`, `db_session` alias,
  frequency validator + order warning, pathfinder LRU cache,
  top-level `set_active_fault` factory).
- **`mkdocs.yml`** flipped to `docstring_style: numpy` so the
  rendered API pages keep their per-field type annotations after the
  2026-05-14 docstring sweep, and the Examples / Research-notebooks
  nav now lists all 16 on-disk notebooks (was 5 of 15).
- **`groundinsight.__all__`** gains `set_active_fault`,
  `clear_pathfinder_cache`, `get_pathfinder_cache_size`,
  `set_pathfinder_cache_size`, and `NetworkFrequencyOrderWarning` —
  the Pass-6 surface additions are reachable via
  `from groundinsight import *` and visible to type-checkers.

### Fixed (Audit pass 5 — implemented 2026-05-13)

> The bullets in this sub-section have been **resolved** on branch
> `feature/audit-pass5-fixes` and are demonstrated end-to-end in
> `notebooks/15_audit_pass5_fixes.ipynb`. Regression coverage lives
> in `tests/test_audit_pass5_fixes.py`. The corresponding bullets
> remain in the legacy backlog sub-section
> *fifth 2026-05-13 review pass* below so that the audit reports
> stay self-consistent until the next release is cut.

- **`Network.invalidate_paths()`** is now **scoped to the calling
  network instance**. Previously the helper called
  `clear_pathfinder_cache()` unconditionally and blew away the cache
  for every other network live in the same Python process; a
  notebook iterating over two networks therefore paid a full DFS on
  every flip. `clear_pathfinder_cache(network=...)` is the new
  scoped form; the unscoped call (``clear_pathfinder_cache()``)
  still drops everything as a recovery / test-fixture fallback.
- **`pathfinder._GRAPH_CACHE` / `_FIND_PATHS_CACHE`** include a
  structural fingerprint `(network.name, len(buses), len(branches))`
  in the cache key in addition to `id(network)`. CPython is allowed
  to recycle `id` values once an object has been garbage-collected;
  the structural component prevents the resulting false cache hit
  on a topologically different successor.
- **`groundinsight.__all__`** previously advertised `db_session` but
  the module-level symbol was named `session`, so the documented
  ``from groundinsight import db_session`` raised `ImportError`.
  `db_session` is now a real module-level alias for `session`,
  kept in lock-step by `start_dbsession` and `close_dbsession`,
  and both names are listed in `__all__` for symmetry.
- **`close_dbsession`** tears the three module globals
  (`session`, `engine`, `SessionLocal`) down independently and
  never raises on a half-constructed state (e.g. after a
  `start_dbsession(force=True)` was interrupted between
  `engine.dispose()` and the re-assignment of `session`). The
  helper still logs ``WARNING: No database session to close`` when
  every global is already ``None``.
- **`Network.frequencies`** is validated at construction time:
  empty lists, duplicate frequencies, non-finite (`nan`, `inf`) and
  negative values are rejected with a clear `ValueError`. DC
  (`f = 0`) remains a valid solve frequency. The bug used to
  silently double the work in `solve_network` *and* double the
  amplitude of the corresponding FFT spectral bin in the transient
  solver.
- **`Network.set_active_fault(fault_name, keep_results=False)`**
  accepts a new keyword argument: with `keep_results=True`, the
  previously cached `Result` for the activated fault is preserved
  so a notebook can re-plot the existing solve without recomputing.
  The default (`False`) preserves the historic clear behaviour.
- **`docs/api/core_models.md`** documents `Network.invalidate_paths`
  and the new active-subset semantics; the mkdocstrings dump now
  explicitly enumerates the public surface.
- **`docs/api/pathfinder.md`** documents the module-level caches,
  the new structural fingerprint and the scoped form of
  `clear_pathfinder_cache`.
- **`mkdocs.yml` Notebooks nav** lists all 15 notebooks (was: 3 of
  the 14 Pass-4 notebooks). The remaining Pass-5 follow-on items
  (mkdocs build --strict, notebook front-matter contract) are
  tracked in the Tests-backlog below.

### Fixed (Audit pass 4 — implemented 2026-05-13)

> The bullets in this sub-section have been **resolved** on branch
> `feature/audit-pass4-fixes` and are demonstrated end-to-end in
> `notebooks/14_audit_pass4_fixes.ipynb`. Regression coverage lives
> in `tests/test_audit_pass4_fixes.py` plus updated cases in
> `tests/test_logging.py`. The corresponding bullets have been **left**
> in the legacy backlog sub-sections below so that the four audit
> reports remain self-consistent until the next release is cut.

- **`mkdocs.yml`** no longer references `polyfill.io`. The original
  CDN entry was sold and later served malicious JavaScript; MathJax 3
  does not require a polyfill for modern browsers. The comment block
  documenting *why* the entry was removed remains in the YAML so the
  next audit does not flag it for a fifth time.
- **`__init__.set_log_level`** is now truly handler-idempotent across
  alternating levels. A private sentinel attribute
  (`_groundinsight_console_handler`) on the installed
  `StreamHandler` lets the helper dedupe both stale (pre-0.5) handlers
  and accidental duplicates from notebook reload cycles. The docstring
  documents the interaction with `logging.basicConfig` explicitly so
  users can opt into `propagate=False` when they hit the double-output
  case.
- **`groundinsight.__all__`** now lists every public top-level helper:
  `set_log_level`, the persistence factories (`start_dbsession`,
  `close_dbsession`, `save_*_to_db` / `load_*_from_db`,
  `save_network_to_json`, `load_network_from_json`). `from groundinsight
  import *` and the major type-checkers therefore see the full surface.
- **`__init__.start_dbsession`** is hardened. A second call with the
  *same* path warns and re-uses the existing engine (no-op). A second
  call with a *different* path raises `RuntimeError` unless `force=True`
  is given, in which case the old engine is properly disposed before
  the rebind — no more leaked `scoped_session` registries or
  silently-replaced engines.
- **`save_bustype_to_db` / `load_bustypes_from_db` /
  `save_branchtype_to_db` / `load_branchtypes_from_db` /
  `save_network_to_db` / `load_network_from_db`** now close their
  scoped session via `try/finally` so a failure inside the underlying
  CRUD helper no longer leaks the session in the scoped-session
  registry.
- **`models.core_models.Fault.scalings`** accepts integer keys but
  coerces them to `float` in a `field_validator(..., mode="before")`.
  Unparsable keys are rejected at validator time with a clear
  `ValueError`. The historic dead-int-key bug — where `{50: 1.0}`
  was silently ignored because the runtime lookup compared against
  `float(network.frequencies[i])` — can no longer happen.
- **`pathfinder.PathFinder`** caches both the adjacency graph and the
  `find_paths` results at module level, keyed on
  `(id(network), frozenset(active_buses), frozenset(active_branches))`.
  Multiple PathFinder constructions over the same logical topology
  (the inner loop of `analysis.inverse_rho_f.evaluate_max_epr_under_k`)
  now pay the DFS cost only on the first call. A new
  `clear_pathfinder_cache()` plus `Network.invalidate_paths()` give
  the user an explicit hook for the rare in-place-mutation case.
- **`io.pandapower_import._classify_buses`** logs a warning and skips
  the row with the new `vn_kv_unparsable` reason whenever `vn_kv` is
  missing / `None` / `NaN`. The previous behaviour silently coerced
  the value to `0` and re-classified the row as a voltage-level
  mismatch, hiding the data quality issue from the user.
- **`io.pandapower_import._classify_lines`** explicitly skips
  self-loop lines (`from_bus == to_bus`) with the new `self_loop`
  reason and a warning. Self-loops would have produced
  `Branch(from_bus=X, to_bus=X)` and tripped network validators only
  on some code paths.
- **`io.pandapower_import.from_pandapower`** promotes the
  zero-bus / zero-branch result to a `logger.warning`. A wrong
  `voltage_level_kV` no longer produces a silent empty `Network`.
- **`io.pandapower_import.preview_pandapower_import`** carries the
  same `include_trafos` parameter as `from_pandapower` (raising
  `NotImplementedError` for now). The preview-vs-commit asymmetry
  flagged in the third pass is resolved.
- **`simulation/waveforms.damped_oscillation`** rejects `decay_tau <= 0`
  and `t_off <= t_on` at factory time.
- **`simulation/waveforms.sinusoidal_with_dc_offset`** rejects
  non-positive `frequency_hz`, `dc_decay_tau <= 0` (when set), and
  inverted on/off windows. The classic ms-vs-s unit confusion now
  surfaces immediately.
- **`simulation/waveforms.step`** rejects inverted on/off windows for
  the same reason.
- **`analysis.inverse_rho_f.evaluate_max_epr_under_k`** raises a
  descriptive `LookupError` when the swept bus is missing from the
  result frame (typically due to a `run_fault_kwargs={"buses": [...]}`
  filter). The previous bare `StopIteration` from the generator
  expression is gone. The `network.paths` restore at the end of the
  sweep is now an atomic rebind (rather than `clear()` + `update()`),
  closing the transient-empty-dict window for concurrent readers.
- **`analysis.inverse_rho_f.find_max_rho_f_scaling`** re-evaluates
  the EPR breakdown at `c_max` after the bisection terminates so
  that `max(epr_rms_per_bus_at_c_max.values()) ==
  max_epr_rms_at_c_max` is structurally guaranteed regardless of how
  the loop exits.
- **`models.core_models.Network.invalidate_paths()`** is a new public
  helper that drops `network.paths` and the module-level PathFinder
  caches in one call.

## Part 2 — engineering detail on the 0.5.0 features

The reasoning behind the transient solver, the RLC parameterisation and
their persistence: state-vector layout, the Schur complement used to
eliminate the algebraic buses, the branch-current sign convention, the
pi-section lumping of branch shunt capacitance, the Carson substitution
at DC, and the database columns the new fields occupy. `CHANGELOG.md`
carries the user-facing summary of the same work.

### Added

- **Lumped RLC formulas on `BusType` and `BranchType`** — optional
  parallel parameterisation for the upcoming transient solvers. New
  fields are kept as `Optional[str]` and default to `None`, so existing
  networks and stationary studies are unaffected:
  - `BusType`: `R_formula`, `L_formula`, `C_formula`.
  - `BranchType`: `R_self_formula`, `L_self_formula`, `C_self_formula`,
    `R_mutual_formula`, `M_mutual_formula`.
  Each formula is validated with the same SymPy parser as the existing
  impedance formulas and may use the symbols `rho`, `f` (and `l` on
  branches). The frequency-domain solver continues to ignore the new
  fields entirely; the duplication is intentional so the stationary and
  transient parameterisations can be maintained independently (the
  decision recorded in the Phase 2 design discussion).
- **Evaluated RLC dicts on `Bus` and `Branch`** —
  `Bus.calculate_impedance` and `Branch.calculate_impedance` now also
  populate the corresponding `R`, `L`, `C` (bus) and `R_self`, `L_self`,
  `C_self`, `R_mutual`, `M_mutual` (branch) dictionaries when the type
  has the matching formulas. Values are real (`Dict[float, float]`) and
  are produced by the new helper `compute_real_value` in
  `groundinsight.utils.impedance_calculator`, which re-uses the same
  compilation cache as `compute_impedance` and rejects formulas that
  evaluate to a non-negligible imaginary value.
- **Notebook `notebooks/09_rlc_parameters.ipynb`** — demonstrates the
  parallel maintenance of `impedance_formula` and the lumped RLC
  formulas on a HF-aware substation grounding equivalent, verifies that
  the lumped RLC reconstruct `Z(f)` of the stationary formula across
  five decades of frequency, and shows the matching pattern on a
  branch type.
- **`groundinsight.simulation.transient` sub-module** — first transient
  solver path, FFT-based. New public types:
  - `gi.TransientStudy(network, fault_name)` — high-level study object
    with `set_source_waveform`, `set_observation(buses=..., branches=...)`
    and `solve(t_end, dt, solver='fft')`.
  - `gi.ResultTransient` — Pydantic container with `time_s`, `epr_t`,
    `i_branch_t`, `source_t` plus a `to_polars()` long-format export.
  Phase 3 limitations are documented in the module docstring: only
  `source_type='current'` sources are accepted, and mutual coupling is
  not evaluated by the FFT solver. Both restrictions go away with the
  state-space solver in Phase 4.
- **`groundinsight.simulation.waveforms` library** — `step`,
  `sinusoidal_with_dc_offset` and `damped_oscillation` factory
  functions returning vectorised time-domain callables. Exposed as
  `gi.waveforms`.
- **Plotting helpers `gi.plot_epr_transient` and
  `gi.plot_branch_current_transient`** — matplotlib time-domain plots
  for `ResultTransient`, mirroring the existing `plot_*` API and
  defaulting to all observed signals when no explicit selection is
  passed.
- **Notebook `notebooks/10_transient_fft.ipynb`** — fault-initiation /
  fault-clearing demo on a small two-bus inductive network: 50 Hz
  current with exponentially decaying DC offset, fault on at 20 ms,
  cleared at 120 ms, EPR and shield current shown over the entire
  switching cycle.
- **State-space ODE solver in `gi.TransientStudy`** — second transient
  solver path, accessible via `solve(t_end, dt, solver="state_space")`.
  Builds the modified-nodal-analysis form ``dx/dt = A*x + B*u``,
  ``y = C*x + D*u`` directly from the lumped RLC fields on
  `BusType` / `BranchType` (parallel ``R || L`` shunt at every active
  bus, series ``R_self + L_self`` along every grounding branch). State
  vector contains every bus-inductor current and every branch-inductor
  current; integration is done via ``scipy.signal.lsim``. The right
  tool for fault-initiation and fault-clearing transients dominated by
  L/R behaviour, where the FFT solver cannot resolve the true
  ring-down. Same `set_source_waveform` / `set_observation` /
  `ResultTransient` contract as the FFT solver — switching between
  solver paths is a one-line change. Phase 4 limitations documented in
  the module: bus capacitance and branch shunt capacitance reserved
  for a later release; mutual coupling reserved; only
  `source_type='current'` accepted.
- **Notebook `notebooks/11_transient_state_space.ipynb`** — same
  fault-on / fault-off scenario as Notebook 10, run through the
  state-space path. Compares FFT and state-space EPR traces side by
  side and zooms into the clearing transient to make the post-event
  exponential decay visible.
- **Bus capacitance support in the state-space solver** — `Bus.C`
  (populated from `BusType.C_formula`) is now part of the ODE: the
  bus voltage at every capacitive bus becomes a state variable
  ``v_C`` with ``C * dv_C/dt = i_C``. The state-vector layout is
  extended to ``[i_L_bus, i_L_branch, i_L_voltage_source, v_C_bus]``,
  and the non-capacitive bus voltages are eliminated algebraically
  via a Schur complement of the resistive conductance matrix.
- **Voltage-source support in the state-space solver** — sources with
  ``source_type='voltage'`` are now accepted by the state-space path.
  ``source_impedance`` is decomposed at ``network.frequencies[0]`` into
  a real ``R_src`` and an inductive part
  ``L_src = imag/(2*pi*f_eval)``; for ``L_src > 0`` a synthetic loop
  branch between source bus and active fault bus is added with its
  own inductor state and the EMF (the user-supplied waveform) enters
  the di/dt equation directly. For ``L_src == 0`` the loop is purely
  resistive and the source is reduced to its Norton equivalent
  (``Y_src`` loop closure plus injected ``U/R_src`` waveform). The FFT
  solver still rejects voltage sources at ``solve()`` time with a
  pointer to the state-space alternative.
- **Branch-current sign convention in the state-space solver** —
  inductive and resistive branch currents reported by the
  state-space solver now follow the project-wide convention used by
  ``compute_branch_currents`` and the FFT solver
  (``i_branch = (v_to - v_from) * Y_self``). The previous
  state-space output was inverted; users comparing FFT and
  state-space traces on the same network will now see them sit on
  top of each other for the forced response.
- **Notebook `notebooks/12_transient_thevenin_rlc.ipynb`** —
  Thevenin source switched onto a substation bus modelled as
  parallel R || L || C. Two LC modes (bus-internal tank and
  source-loop tank) are visible in the EPR trace and confirmed by
  the post-event spectrum.
- **Automatic pi-section lumping of branch shunt capacitance in the
  state-space solver** — every grounding branch with a populated
  ``C_self_formula`` now contributes ``C_self / 2`` to each of its
  endpoint buses. The user no longer needs to maintain a duplicate
  ``C_formula`` on the bus type when the cable's distributed
  capacitance is the dominant shunt. The frequency-domain solver path
  is unchanged: ``C_self`` is not part of the FFT branch admittance,
  so the user continues to model the lumped C in
  ``Bus.impedance_formula`` for consistency between the two solvers.
- **Carson-style mutual coupling in the state-space solver** —
  ``R_mutual`` and ``M_mutual`` are now part of the ODE assembly via
  the substitution ``z = i_shield + (M / L_self) * I_phase``. The
  substitution eliminates the ``M * dI_phase/dt`` term from the KVL
  of the shield branch and leaves a clean linear feedforward of the
  source waveform into both ``B_kcl`` and ``B_emf``. Restricted to
  current sources for now (voltage-source phase currents are
  state-dependent and require a more involved substitution); voltage
  sources skip the mutual contribution and emit a one-time warning.
- **Notebook `notebooks/13_mv_ring_transient.ipynb`** updated to
  exercise the two new features on a 20 kV / 20-bus medium-voltage
  ring with NA2XS(F)2Y cable (per-length values from datasheet).
  Compares FFT and state-space with mutual coupling on, and shows the
  reduction-factor effect of the cable shield.

### Internal

- Persistence: `BusTypeDB` / `BranchTypeDB` gained the new formula
  columns; `BusDB` / `BranchDB` gained nullable JSON columns for the
  evaluated `R/L/C` (bus) and `R_self/L_self/C_self/R_mutual/M_mutual`
  (branch) dicts. Two helpers `_real_dict_to_json` /
  `_real_dict_from_json` keep the (de)serialisation symmetric. JSON
  and SQLite roundtrip tests cover both the formula columns and the
  evaluated dicts.

## Part 3 — open findings, not yet scheduled

**Nothing below is implemented.** These are findings that were confirmed
during a pass but deferred, kept in the wording of the pass that raised
them. Entries written before a later pass may have been resolved since
without being struck through here — check
[`CHANGELOG.md`](https://github.com/Ce1ectric/groundinsight/blob/main/CHANGELOG.md)
first.

### Fixed (Backlog — eighth 2026-05-25 review pass)

> The eighth audit pass was run on 2026-05-25, one day after the
> Pass-7 *implementation* run on 2026-05-24 (which closed Pass-7
> *„db_session synonym carries cross-API maintenance debt"* and the
> Roadmap-bullet *„ADR-0013 — Cross-repo `show_versions` convention"*).
> The Pass-4 → Pass-7 implementation blocks **still sit uncommitted**
> on `feature/audit-pass5-fixes` (60 modified files + 14 untracked
> files including the new `simulation/transient.py`,
> `simulation/waveforms.py` and five new example notebooks). Only the
> CHANGELOG is edited in this pass; no program code is touched.

- **`__version__ = "0.5.0"` is set but the `0.5.0` release is *not*
  cut — `feature/audit-pass5-fixes` still holds the entire
  Pass-4 → Pass-7 implementation set as a single uncommitted blob
  (60 modified, 14 untracked).** `src/groundinsight/__init__.py` and
  `pyproject.toml` both carry `0.5.0` while `CITATION.cff` still carries
  `0.4.0` — the drift that makes `scripts/release.py` abort before it does
  anything (resolved in the Pass-17 block above). `git log` is at `04bd936  chore(release): v0.4.0`. Five
  audit passes have flagged the missing release cut. The recommended
  commit-sequence from `audit-implementation-report-groundinsight-2026-05-24-pass7.md`
  has *not* been executed; the eighth pass elevates this to a
  release-blocker — every additional audit run rebuilds the same
  Pass-4/5/6/7 ledger because the work is not yet history.
- **Transient subsystem has no dedicated `Added` block under
  `[Unreleased]`.** `src/groundinsight/simulation/transient.py`
  (untracked, 11 functions, `ResultTransient` + `TransientStudy`)
  and `src/groundinsight/simulation/waveforms.py` (untracked,
  `step`, `sinusoidal_with_dc_offset`, `damped_oscillation`) are
  the biggest single feature batch since the `0.4.0` cut, yet
  the only references in `[Unreleased]` are inline mentions in
  the Pass-6 `Fixed` block (the `NetworkFrequencyOrderWarning`
  motivation paragraph) and the `Tests (Backlog)` entries for
  individual bugs. Add an explicit
  `Added (Transient subsystem — implemented 2026-05-2x)` block
  enumerating: `TransientStudy.set_source_waveform`,
  `.set_observation`, `.solve(solver="fft" | "state_space")`;
  `ResultTransient.epr_per_bus_t`, `branch_current_per_branch_t`;
  the `waveforms` module surface; the two new plotting helpers
  `plot_epr_transient` and `plot_branch_current_transient`; and
  the new docs pages `docs/transient.md`, `docs/api/transient.md`.
- **Five new example notebooks are uncommitted with no
  `Added (Examples — …)` block under `[Unreleased]`.**
  `docs/examples/minimal.ipynb`, `docs/examples/mv_ring.ipynb`,
  `docs/examples/mv_ring_transient.ipynb`,
  `docs/examples/pandapower_import.ipynb`,
  `docs/examples/fault_sweep.ipynb` were added to replace the three
  removed `cired.ipynb` / `low_voltage.ipynb` / `simple.ipynb`
  entries (the `D ` markers in `git status`). The
  `examples/index.md` and `mkdocs.yml` `Examples:` nav have been
  updated in lock-step. Add a CHANGELOG bullet so the rename
  rationale (the old examples were domain-specific; the new
  five are progressively complex teaching examples) survives the
  release.
- **`plot_epr_transient`, `plot_branch_current_transient`** and
  the `waveforms` re-export at top level are in
  `src/groundinsight/__init__.py` `__all__` but the Pass-7
  `Fixed`-block lists `set_active_fault`,
  `clear_pathfinder_cache`, `get_pathfinder_cache_size`,
  `set_pathfinder_cache_size`, `NetworkFrequencyOrderWarning`
  only. The eight new Pass-8 public symbols miss the changelog.
  Add them under a dedicated `Added (Plotting / Waveforms public
  surface — …)` block before the release cut.
- **`docs/transient.md` does not appear in any `Added (Docs — …)` /
  `Docs (…)` block.** The page is the principal new user-facing
  document for the 0.5.0 release (FFT vs. state-space derivation,
  per-solver assumption table, side-by-side example), but the
  CHANGELOG has no entry that points to it. Add a
  `Docs (Transient page — …)` bullet.
- **`docs/api/transient.md` is untracked but the API-reference
  index does not mention it.** The mkdocstrings dump exists and
  `mkdocs.yml` has the `Transient simulations: api/transient.md`
  nav entry, but `docs/api/index.md` Overview prose still lists
  only the eight pre-Pass-7 modules. Add the entry under the API
  Reference table.
- **`gi.cross_repo` namespace + `docs/cross-repo.md` still open.**
  The Pass-7 *implementation* report explicitly deferred these
  pending ADR-0013 in `groundfield`. ADR-0013 itself is still
  unwritten (`docs/adr/` in `groundfield` lists 0001–0012 only),
  so the seventh-pass *Roadmap* bullet stays open on the
  `groundinsight` side. Track the dependency explicitly in the
  Pass-8 Roadmap section below.
- **Pass-4/5/6 `Fixed (Backlog — …)` sub-sections still
  duplicate the entries listed in the implementation blocks
  above.** The audit-implementation reports document that the
  duplication is intentional until the release is cut, but the
  three sub-sections together carry > 1100 lines of backlog
  text that is structurally identical to the implementation
  blocks above. Once the release-cut PR merges, the
  `Fixed (Backlog — …)` sub-sections for *implemented* passes
  must be deleted in a separate `chore(changelog): purge
  resolved backlog` commit. Eighth-pass forcing function:
  open that PR alongside the release tag.
- **`mkdocs build --strict` CI hook still missing — eighth pass
  in a row.** Same finding as in `groundfield` and `groundmeas`.
  The new `docs/transient.md`, `docs/api/transient.md` and the
  five new example notebooks would all be flagged by
  `--strict` immediately if a stale link survived the rename
  (the three removed example notebooks are still referenced in
  `notebooks/02_topologies.ipynb` markdown cells — flagged by
  the seventh pass as a Pass-7 `Tests (Backlog)` item, still
  open).
- **`db_session` retire path with `DeprecationWarning` still
  unscheduled.** The Pass-7 *implementation* run chose Option 1
  (central setter `_set_session`) and explicitly deferred
  Option 2 (retire alias with `DeprecationWarning`, drop in
  0.6.0). Add an `Added (Deprecations — …)` block once a date
  for the retire is pinned, so downstream code has a documented
  migration window.

### Docs (Backlog — eighth 2026-05-25 review pass)

- **`docs/quickstart.md` does not show the new transient
  workflow.** The page ends at the stationary
  `gi.run_fault(...)` call; an „Optional: transient analysis"
  appendix that drives `TransientStudy.set_source_waveform(...)`
  + `.solve("fft")` would close the gap. Mirrors the pattern
  the `groundfield` quickstart uses for optional features.
- **`docs/concepts.md` does not enumerate the new public
  `Network` topology methods.** `Network.invalidate_paths()`,
  `Network.set_active_fault(name, keep_results=True)`, the
  re-entrant `outage_context` and the `active` flag on
  `Bus` / `Branch` are referenced piecewise in API pages but
  not folded into the Concepts narrative. Add a
  „Network state-management contract" subsection.
- **`docs/api/index.md` still does not enumerate the new
  `transient` / plotting / waveforms surface.** Pass-7 Docs
  backlog re-emphasised the index drift; Pass-8 adds the
  transient subsystem and the two new plotting helpers.
- **README Roadmap drift (seventh-pass finding re-flagged).**
  The README's „Roadmap" section still references the bridge
  to `groundfield` as „Near term — target 0.4.0"; the bridge
  shipped in 0.4.0 and the next milestone (transient + pandapower
  importer + outage-context) ships in 0.5.0. Update the bullet
  before the release cut.

### Tests (Backlog — eighth 2026-05-25 review pass)

- **No `mkdocs build --strict` test (seventh-pass finding
  re-flagged — eighth pass elevates again).**
- **No regression test that the three removed example
  notebooks (`cired.ipynb`, `low_voltage.ipynb`,
  `simple.ipynb`) are not referenced in any docs page or
  notebook markdown cell.** A one-line grep over `docs/` and
  `notebooks/` per removed filename catches the most common
  stale-link regression.
- **No regression test against `graphify-out/manifest.json`
  staleness.** Same pattern as the `groundfield`-side Pass-8
  proposal: read the *„Built from commit"* line from
  `graphify-out/GRAPH_REPORT.md`, compare against `git rev-parse
  HEAD`.
- **No version-parity test for the new top-level symbols.**
  `tests/test_audit_pass6_fixes.py` and
  `tests/test_audit_pass7_fixes.py` pin per-fix symbols; an
  umbrella `test_public_api_surface.py` that asserts every
  symbol listed in `__all__` is importable and not `None`
  catches incomplete merges and stale `__all__` entries at
  PR time.
- **No test that `simulation/waveforms.py` defaults survive
  JSON round-trip via `ResultTransient.model_dump_json()`.**
  The transient subsystem is the only Pass-5/6/7 feature
  without a Pydantic round-trip test in the implementation
  blocks above.

### Fixed (Backlog — pending implementation)

> The following bugs were identified in the code-review pass on
> 2026-05-10. They are queued for the next maintenance release; the
> entries are recorded here so each one ships with a referenced fix
> commit. New entries from the **second 2026-05-10 review pass**
> (waveforms / inverse_rho_f / mkdocs) are appended at the end of the
> list below the original twelve items.

- **`network_operations.create_fault(name, ..., active=True, network=None)`**
  raises `AttributeError: 'NoneType' object has no attribute
  'set_active_fault'` instead of a clean `ValueError`. Guard the
  activation block under `if active:` and require a network.
- **`io.pandapower_import._bus_in_service` is reused for line rows**
  (`pandapower_import.py:343`). Functionally correct because
  pandapower exposes `in_service` on both tables, but the misleading
  name will trip up future contributors. Rename to a neutral
  `_in_service` helper or split per element class.
- **`simulation/transient.py:_solve_state_space` evaluates source
  impedance at `network.frequencies[0]`**, which silently drops the
  reactive part of `Z_src` when DC (`f=0`) is the first entry of the
  frequency list. Pick the first non-zero frequency or refuse the
  decomposition with a clear message.
- **Stale `TransientStudy._TransientStudy__mutual_for_output`**
  attribute survives across successive `solve()` calls. Pass the
  mutual-coupling tuple as a local argument or reset it at the top
  of every solve.
- **`simulation/outage.OutageStudyResult.compare_buses` /
  `compare_branches` divide by `_ref_value` without guarding against
  zero**, producing silent `inf` / `NaN` in `delta_pct_vs_<ref>`
  when the reference scenario carries an EPR or branch current of
  zero (typical at the source bus or on an open branch). Wrap with
  `pl.when(_ref_value == 0).then(None).otherwise(...)`.
- **`electrical_network.solve_network` does not assign
  `self.u_vectors[freq]` on solver failure** but its caller still
  dereferences `u_vectors[freq][idx]` further down, raising
  `KeyError`. Either store a zero vector on failure, or abort the
  whole solve.
- **`models.core_models.Source` voltage-mode validator accepts
  empty `voltage` / `source_impedance` dicts** because the
  `v_keys != z_keys` check passes for two empty sets. Require both
  dicts to be non-empty when `source_type='voltage'`.
- **`models.core_models.Fault.active` is a `computed_field`**
  rendered through the underscore-prefixed `_set_active`. Pydantic
  `model_validate` does not restore `_active` on JSON / SQLite
  round-trip, so `network.active_fault` is preserved but
  `fault._active` defaults to `False`. Either use a regular `active`
  field or set `_active` in `Network.model_post_init` based on
  `active_fault`.
- **`simulation/transient`: FFT and state-space grids differ by one
  sample for the same `(t_end, dt)`** (FFT forces even N; state
  space uses `int(round(t_end/dt)) + 1`). Side-by-side comparison
  notebooks exhibit a one-sample shift. Align both grid
  constructions or document precisely.
- **`simulation/transient._solve_state_space`** silently drops the
  mutual contribution of branches without an `L_self_formula`
  (they are not part of `branch_inductive` and therefore never
  enter `mutual_branches`). Emit a `logger.warning` listing the
  skipped mutual contributions so the user notices.
- **`__init__.save_*_to_db` / `load_*_from_db` close their session
  with a bare `db_session.close()`** — no `try/finally`. A failed
  underlying `_save_network` leaks the session in the scoped-session
  registry. Wrap each helper in `try/finally`.
- **`utils.validations.validate_impedance_formula_value` only
  registers `R, X, M, N, j` as known symbols.** A user typing
  `Z = R_E + j*omega*L_E` (typical EVU naming) gets the additional
  symbols accepted as free symbols and only fails much later inside
  `compute_impedance`. Validate against the canonical
  `{rho, f, l, rho1, rho2, h}` set instead.
- **`electrical_network.compute_branch_currents` and
  `_calculate_rms` use a `sqrt(sum(|X_k|^2))` definition**, while
  the documentation calls the result an "RMS" value. For sinusoidal
  phasors the conventional definition is
  `sqrt(sum(|X_k|^2 / 2))`. Either correct the formula or rename the
  field and clarify the docstring.

> Additional findings from the **second 2026-05-10 review pass**:

- **`simulation/waveforms.damped_oscillation` does not validate
  `decay_tau > 0`.** `decay_tau == 0` divides by zero in
  `np.exp(-tau_local / decay_tau)`; `decay_tau < 0` produces a
  silently exponentially *growing* waveform that the user will
  almost never want. Raise `ValueError` for `decay_tau <= 0` at
  factory time, before the user spends solver wall-clock on a
  meaningless input.
- **`simulation/waveforms.sinusoidal_with_dc_offset` accepts
  `dc_decay_tau == 0`** silently — same division-by-zero risk as
  above. Require `dc_decay_tau > 0` when a finite decay is
  intended; document that `dc_decay_tau is None` means "no
  decay".
- **`simulation/waveforms.{step, sinusoidal_with_dc_offset,
  damped_oscillation}` do not validate `t_off > t_on`.** A user
  passing `t_off < t_on` (typically a unit confusion: ms vs s)
  gets a permanently-zero waveform and a baffling "transient is
  flat" plot. Raise `ValueError` when `t_off is not None and
  t_off <= t_on`.
- **`simulation/waveforms.sinusoidal_with_dc_offset` raises no
  warning when `frequency_hz <= 0`.** Negative frequency is
  silently equivalent to flipping the phase; `frequency_hz == 0`
  collapses to a constant offset that masks user confusion with
  the DC term. Reject non-positive frequencies at factory time.
- **`analysis.inverse_rho_f.evaluate_max_epr_under_k` uses
  `network.paths.clear()` + `update(paths_backup)`** in the
  `finally` block instead of an atomic replacement
  (`network.paths = paths_backup`). If the user reads
  `network.paths` from another thread during the sweep the
  observable state transiently holds an empty dict. Either
  document the non-atomic restore or atomically reassign.
- **`analysis.inverse_rho_f.evaluate_max_epr_under_k` does not
  catch failures during `network.add_fault`** — if the second
  `add_fault` raises (e.g. because `Fault.scalings` violates a
  validator), `temp_faults_created` still lists faults that have
  already been added to the network and they are removed on the
  way out, but the *first* `add_fault` call has already mutated
  `network.faults` so the rollback works only because of the
  ordering. Wrap the `add_fault` loop in its own `try/except`
  that rolls back the partial list explicitly.
- **`simulation/outage.outage_context` keeps a *shallow* copy of
  `network.paths`** via `dict(network.paths)`. `run_fault` inside
  the `with` block mutates the same `Path` objects in place
  (e.g. populating `current_share`). After the `with` block exits
  the restored `network.paths` still references those mutated
  `Path` objects. Document the contract (the rollback restores
  *names* / *topology*, not internal solver state) or deep-copy.
- **`mkdocs.yml:90` still references `https://polyfill.io/...`**
  — the same security-relevant CDN entry that `groundmeas`
  already removed (the domain was sold and the CDN later served
  malicious JavaScript). MathJax 3 does not require a polyfill
  for modern browsers. Drop the line; mirror the comment block
  used in `groundmeas/mkdocs.yml`.
- **`simulation/transient.TransientStudy._solve_state_space`
  silently skips grounding branches that have neither `R_self`
  nor `L_self`** (logged as a warning at the end) but does not
  fail the solve. For a user who forgot to populate any RLC
  formula on the `BranchType`, all branches end up in
  ``skipped`` and the resulting state-space model is the bus-only
  parallel-RLC of the buses — likely not what was intended.
  Either fail loudly when *every* grounding branch was skipped or
  surface the count in the returned `ResultTransient.metadata`.
- **`__init__.set_log_level` only inspects `self.handlers`** for
  an existing `StreamHandler`, but does not consider inherited
  handlers from the root logger. Notebooks that configure
  `logging.basicConfig(level="INFO")` end up with two output
  streams: one through the root logger, one through the package
  handler. Either set `propagate=False` on the package logger or
  document the interaction with `basicConfig`.

> Additional findings from the **third 2026-05-12 review pass**
> (focus: `io/pandapower_import`, deep look at the new transient and
> inverse-rho-f modules; the previous two passes had only spot-checked
> the importer):

- **`io.pandapower_import._classify_buses` reads `vn_kv` with
  `float(row.get("vn_kv", 0.0) or 0.0)`** (`pandapower_import.py:157`).
  A bus row with a missing or `None` `vn_kv` is silently treated as
  `0 kV` and re-classified as "voltage_level_mismatch" — no warning
  reaches the user. Emit a `logger.warning(...)` listing rows with
  unparsable `vn_kv` so the importer reports unexpected data instead
  of silently dropping it.
- **`io.pandapower_import.from_pandapower` calls `_bus_in_service(row)`
  on the line rows** as well (`pandapower_import.py:343`). Functionally
  fine because pandapower exposes `in_service` on both tables, but the
  misleading name hides the intent. Already on the backlog from
  pass 1 — the third pass confirms the fix has not been merged.
- **`io.pandapower_import._classify_lines` does not reject self-loops**
  (`from_idx == to_idx`). A pandapower line with both endpoints on
  the same bus is mapped to a `Branch(from_bus=X, to_bus=X)` which
  later trips `Network.add_branch` validators only in some code paths.
  Add an explicit skip with `reason="self_loop"`.
- **`io.pandapower_import.from_pandapower` does not surface "no buses
  matched the requested voltage level"**. A user who passes the wrong
  `voltage_level_kV` gets an empty `Network` and no log entry beyond
  the `Imported pandapower net … 0 buses` info line. Promote the
  zero-bus / zero-branch case to a `logger.warning` so the user
  notices.
- **`io.pandapower_import.preview_pandapower_import` has no
  `include_trafos` parameter** but `from_pandapower` does. The two
  helpers are advertised as "preview → commit"; the asymmetry means
  a user previewing a 110 / 20 kV net cannot see which trafos would
  be picked up. Add the same parameter to the preview function and
  surface the reserved status (`NotImplementedError`) consistently.
- **`analysis.inverse_rho_f.evaluate_max_epr_under_k` collects the
  bus' RMS EPR via `next(rb for rb in network.results[fname].buses
  if rb.name == b)`** (`inverse_rho_f.py:194`). If the matching bus
  is absent from the result frame (e.g. because it was pruned by a
  custom `run_fault_kwargs={"buses": [...]}` filter) the generator
  raises `StopIteration` rather than a clear error. Wrap with a
  `KeyError` / `LookupError`.
- **`analysis.inverse_rho_f.find_max_rho_f_scaling` reports
  `c_max = c_lo_init` as the fall-back when the lower bracket is
  admissible but bisection then never updates it.** The result dict
  reports `"epr_rms_per_bus_at_c_max"` taken from the **last** loop
  iterate, not from the iterate that actually produced `c_max`. The
  `eprs_lo_snapshot` shadow variable closes most of the gap, but the
  branch that exits with `iterations < max_iter` due to the
  tolerance test still re-uses the most recent admissible snapshot
  rather than the snapshot at `c_max`. Add a test that prints the
  EPR breakdown at `c_max` and asserts the maximum equals
  `max_epr_rms_at_c_max`.
- **`simulation/transient.TransientStudy` constructor does not check
  that `fault_name` actually exists in `network.faults`.** A typo
  surfaces only later as a baffling `KeyError` from
  `_build_state_space`. Validate early in `__init__`.
- **`simulation/transient.py` and `simulation/outage.py` import
  `polars` unconditionally at module top-level** but the
  `pyproject.toml` extras already declare a `polars` core dep, so
  this is fine — *but* the future "lite" install path (e.g. for
  embedded use) is blocked by the import. Document the dependency
  in the module docstrings.
- **`network_operations.run_fault` rebuilds the path cache via
  `define_paths(network)`** every call when `network.paths` is
  empty, but the auto-parallel-coefficient pre-solve does *not*
  invalidate the cache on topology change (`active` toggles a bus
  but `network.paths` survives because the test
  `if not network.paths` is "non-empty → reuse"). The outage helper
  already works around this by `clear()`-ing the cache; document
  the invariant or add a `Network.invalidate_paths()` method.

> Additional findings from the **fourth 2026-05-12 review pass**
> (focus: re-export surface, set_log_level idempotency, notebook
> versus mkdocs nav coverage, repeated polyfill.io flag):

- **`mkdocs.yml:90` still references
  `https://polyfill.io/v3/polyfill.min.js?features=es6`** — verified
  again on the second 2026-05-12 audit run; this is now the fourth
  consecutive pass that flags it. The line still has to be removed
  (or pinned to a trusted mirror) before the next public release.
- **`groundinsight.__all__` does not include `set_log_level`** even
  though the helper is defined in `__init__.py:120` and the
  Changed-backlog entry below explicitly lists it as missing.
  Pass-1 noted the gap; pass 4 confirms the listing still ships
  in `0.4.0`. Type-checkers using `from groundinsight import *`
  silently miss the helper.
- **`__init__.set_log_level` adds a fresh `StreamHandler`** every
  time the level *changes*, but keeps the previous handler when the
  user merely *toggles back to the same level*. Repeated calls from
  notebooks ("`set_log_level("DEBUG")` → solve → `set_log_level
  ("INFO")` → solve → `set_log_level("DEBUG")`") therefore leave the
  package logger with multiple `StreamHandler`s, each writing the
  same record once. Either deduplicate handlers on every call or
  call `logger.removeHandler(...)` before re-attaching.
- **`__init__.start_dbsession` / `close_dbsession` use a
  module-global `db_session`**, mirroring the same anti-pattern
  flagged in `groundmeas.core.db._engine`. A second
  `start_dbsession("other.db")` silently replaces the first, leaking
  the previously-bound `scoped_session` and any open transactions.
  Either guard the re-attach with an explicit `close_dbsession()`
  precondition or dispose the old engine before swapping.
- **`pathfinder.PathFinder` constructs a fresh adjacency graph on
  every `find_paths` call.** For the multi-fault sweep loop in
  `analysis.inverse_rho_f.evaluate_max_epr_under_k` the same graph
  is rebuilt N_buses times even though the underlying topology
  (modulo `active` flips) is invariant within a single call. Cache
  the graph keyed on `(frozenset(active_buses),
  frozenset(active_branches))` to cut the inner-loop overhead.
- **`models/core_models.Fault.scalings` accepts a `Dict[float,
  ComplexNumber]`** but every value is silently coerced to
  `complex` during `compute_impedance`. A typo such as
  `scalings={50: 1.0+0j, 50.0: 0.5+0j}` (int vs float key) leaves
  *both* entries in the dict but the lookup uses
  `network.frequencies[i]` (always float), so the int-keyed value
  is dead. Validate the keys against `network.frequencies` at
  validator time.
- **`docs/api/io.md` does not cross-link `notebooks/06_pandapower_
  import.ipynb`**, even though the notebook is the only end-to-end
  example of the importer family. The `from_pandapower` rendered
  docstring shows an inline four-line snippet; for AP 1 readers the
  full notebook is the natural follow-up.
- **`notebooks/04_persistence.ipynb` is not part of the doc-site
  navigation** (`mkdocs.yml` examples nav only carries
  `simple.ipynb`, `cired.ipynb`, `low_voltage.ipynb`). The notebook
  is the canonical demonstration of `save_*_to_db` /
  `load_*_from_db`; without it the persistence API has no walkthrough
  on the docs site.

> Additional findings from the **fifth 2026-05-13 review pass**
> (focus: residual side-effects of the Pass-4 implementation block,
> `__all__` integrity, pathfinder cache scope, frequency-list
> validation, doc-site notebook coverage that was only partially
> closed in Pass 4):

- **`Network.invalidate_paths()` clears the *entire* module-level
  `pathfinder._GRAPH_CACHE`** via the unconditional
  `clear_pathfinder_cache()` call (`core_models.py:954`). Two
  notebooks that each build a separate `Network` and call
  `invalidate_paths()` on one of them blow away the cached graph
  for the *other* network too. The fix is to scope the clear to
  entries whose first key component is `id(self)`, or to use a
  per-Network sub-dict. Code-quality regression that bites the
  first time a user runs the dashboard sweep against two networks
  in the same process.
- **`pathfinder._GRAPH_CACHE` keys on `id(network)`** — Python
  re-uses ids once an object is garbage-collected. Two
  short-lived `Network` instances with different topologies but
  the same recycled id and the same `frozenset(active_*)` would
  silently share the cached graph. Either replace the integer id
  with a `WeakKeyDictionary` (keys must hash; this means
  upgrading `Network` to `frozen=True`-ish behaviour or wrapping
  it) or include `(network.name, len(buses), len(branches))` in
  the key as a defence-in-depth check.
- **`groundinsight.__all__` advertises ``db_session``** at line 76
  but no module-level `db_session` attribute is defined — the
  real handle is `session` (`__init__.py:217`).
  `from groundinsight import db_session` therefore raises
  `ImportError` while the docstring promises a public surface.
  Either drop `db_session` from `__all__` or alias `db_session =
  session` at module scope (matching the local variable name
  used in every persistence helper body).
- **`close_dbsession` assumes the `session is not None` guard
  implies `engine is not None`.** If a previous
  `start_dbsession(force=True)` was interrupted between
  `engine.dispose()` and re-assigning `session`, the function
  raises `AttributeError` on `engine.dispose()` (line 300). Null
  each global independently and only log "no session" when
  *all three* (session, engine, SessionLocal) are unset.
- **`models/core_models.Network.frequencies: List[float]`** is
  unvalidated. A duplicate frequency (`[50.0, 50.0]`) silently
  doubles the work in `solve_network` *and* doubles the
  amplitude of the corresponding spectral bin in the FFT
  transient. Reject duplicates / non-finite / non-positive
  frequencies in a `field_validator(..., mode="after")`.
- **`models/core_models.Bus.active` / `Branch.active`** are
  plain Pydantic fields. Flipping them in place leaves
  `network.paths` populated with stale topology; the user must
  remember to call `network.invalidate_paths()` (and the new
  Pass-4 helper exists exactly for this case). Either promote
  the field to a property + setter that emits a
  `UserWarning("path cache is stale; call invalidate_paths()")`
  on flip, or hook the validator to call `invalidate_paths()`
  automatically.
- **`Network.set_active_fault(fault_name)` silently deletes
  `self.results[fault_name]`** (`core_models.py:940`). The
  docstring mentions the clear, but a notebook user expecting a
  re-runnable scenario loses the previous solve. Expose a
  `keep_results: bool = False` kwarg and document the trade-off.
- **`docs/api/core_models.md` does not document
  `Network.invalidate_paths()`** even though the Pass-4
  implementation block lists it. The mkdocstrings auto-render
  picks it up only if the page enumerates the symbol; today the
  reader has to grep the source.
- **`mkdocs.yml` "Notebooks" nav added 3 entries
  (04, 06, 14) but the remaining 10 notebooks
  (01, 02, 03, 05, 07, 08, 09, 10, 11, 12, 13) are still
  invisible to the docs site.** Pass-2/3/4 finding only
  *partially* resolved; finishing the migration requires adding
  the rest under the same `mkdocs-jupyter` contract.
- **`docs/api/index.md`** still does not list the persistence
  helpers (`start_dbsession`, `close_dbsession`, `save_*_to_db`,
  `load_*_from_db`, JSON helpers). Pass-1 doc gap closed only in
  `__all__`; the rendered API index lags.
- **`scripts/release.py` does not move the `[Unreleased]` block**
  into a dated section on bump (Pass-2 finding). The maintainer
  has to do the cut by hand; port the helper from
  `groundmeas/scripts/_changelog.py`.

> Additional findings from the **sixth 2026-05-14 review pass**
> (focus: secondary side-effects of the Pass-5 implementation block,
> doc-builder convention drift, mkdocs-jupyter nav reality vs.
> claim, cache memory bookkeeping, model/docstring drift introduced
> by the pass-5 frequency validator):

- **`pathfinder._GRAPH_CACHE` / `_FIND_PATHS_CACHE` are unbounded
  module-level dicts.** Pass 5 fixed the *false-hit* problem by
  adding a structural fingerprint to the cache key; what remained
  open is that nothing ever evicts entries. A long-running notebook
  that calls `evaluate_max_epr_under_k` over a hundred-scenario
  outage sweep or that compares two networks across a hundred
  active-subset variations will accumulate one cache entry per
  visited topology and never reclaim memory. Add an `OrderedDict`-
  based LRU wrapper with a default `maxsize=256` and surface the
  cap as `gi.set_pathfinder_cache_size(n)` so dashboard authors
  can tune it.
- **`outage_context` does not invalidate the module-level
  pathfinder cache on exit** (`simulation/outage.py:309`). The
  cache key includes `frozenset(active_buses)` and
  `frozenset(active_branches)`, so correctness is preserved (a new
  active subset gets a new slot), but the entries built during the
  scenario sweep are never reclaimed when the context exits. The
  ``finally`` branch restores ``network.paths`` but leaves the
  module-level cache populated with stale active-subset slots.
  Coupled with the unbounded-growth finding above this is the
  fastest way to leak memory on a multi-scenario outage study.
  Fix: call ``clear_pathfinder_cache(network)`` from the
  ``finally`` branch after restoring ``network.paths`` so the
  cache footprint matches the user-visible state of ``network``.
- **`Network._validate_frequencies` docstring drift.** The
  docstring (`models/core_models.py:1022`) reads "Reject empty /
  duplicate / non-finite / non-positive frequency lists", but the
  implementation accepts `f == 0` (DC). The DC inclusion is the
  intended behaviour (FFT transient solvers use the zero-frequency
  bin); rephrase to "negative or non-finite" so users do not
  believe DC is rejected. Internal doc-vs-code drift; no behaviour
  change required.
- **`Network._validate_frequencies` does not warn on non-monotonic
  input.** `groundfield.solver.engine.Engine` introduced the
  `EngineFrequencyOrderWarning` family in Pass-5 to surface
  silently-sorted inputs; the symmetric `Network.frequencies`
  validator silently accepts a descending or shuffled list. This
  is unsafe for downstream FFT bin assignment in
  ``simulation/transient.TransientStudy``: the impedance dict
  uses the *order* from `Network.frequencies` to map to the FFT
  spectrum, so ``[100.0, 50.0]`` produces a transient with the
  spectral bins reversed. Add an analogous
  ``NetworkFrequencyOrderWarning(UserWarning)`` and emit it when
  the list is not strictly increasing.
- **`mkdocs.yml` `mkdocstrings.python.options.docstring_style` is
  set to `google`, but the 2026-05-14 docstring sweep migrated
  the public API to **numpy** style** (`mkdocs.yml:76`,
  `audit-readme-docs-2026-05-14.md`). Numpy-style Parameters /
  Returns sections render as plain text under the Google parser,
  dropping the per-field type annotations. The rendered docs
  site therefore degraded silently after the doc sweep even
  though the source-level docstrings improved. Switch to
  `docstring_style: numpy` and spot-check
  `docs/api/network_operations.md` /
  `docs/api/pathfinder.md` / `docs/api/core_models.md` in a
  local `mkdocs serve`.
- **`mkdocs.yml` Notebooks nav claim vs. reality.** Pass-5
  Fixed-block declares "lists all 15 notebooks (was: 3 of the 14
  Pass-4 notebooks)", but the on-disk `mkdocs.yml` Examples nav
  only lists five curated `.ipynb` files (`minimal`, `mv_ring`,
  `mv_ring_transient`, `pandapower_import`, `fault_sweep`) and
  does **not** list `notebooks/14_audit_pass4_fixes.ipynb` /
  `notebooks/15_audit_pass5_fixes.ipynb`. The 15-notebook claim
  did not materialise on `main`; either revert the Pass-5
  Fixed-block bullet or land the nav edit. Doc-vs-doc drift
  must be resolved before the next release tag.
- **`db_session` / `session` synonyms in `__all__`.** Pass 5 made
  `db_session` a real module-level alias for `session` and added
  *both* names to `__all__`. Two synonyms in the public surface
  multiply the maintenance cost: any future helper that re-binds
  one but not the other — e.g. `start_dbsession(force=True)` —
  produces a divergent `from groundinsight import db_session`
  vs. `from groundinsight import session` pair. Either keep
  `db_session` as a one-way alias documented as "legacy spelling
  of `session`" *or* remove `session` from `__all__` and the
  doc-site to retire the alternative spelling.
- **`outage_context` is not re-entrant safe.** Nested
  ``with outage_context(net, OutageA):`` / inside it
  ``with outage_context(net, OutageB):`` saves outer-baseline
  flags `True` in the outer block; the inner block then captures
  the *modified* state (`active=False` for OutageA targets) as
  *its own* baseline. On inner-exit the inner targets are
  restored correctly, **but** on outer-exit the outer baseline
  overrides every change the inner made to targets that are
  *not* part of OutageA. Either document the "outage contexts
  are not nestable" contract or deep-copy
  `network.buses[...].active` per saved entry on every nesting
  level. Bug surface: `simulation/outage.py:309-380`.
- **`Network.invalidate_paths()` mutates `self.paths` in place
  via `.clear()`.** Callers that snapshot
  `saved = dict(network.paths)` before the call will observe the
  snapshot lose its entries because `dict(...)` produces a
  shallow copy whose values are *shared* with `network.paths`
  only if the values were themselves mutable; safer is to rebind
  ``self.paths = {}`` (atomic rebind) instead of ``.clear()``.
  Same pattern as the Pass-4 atomic-rebind fix in
  `inverse_rho_f.evaluate_max_epr_under_k`.
- **`set_active_fault(fault_name, keep_results=True)` is not
  re-exported via `network_operations`.** The new `keep_results`
  keyword is reachable only via the bound method on `Network`
  instances; the top-level `gi.set_active_fault(network, fault,
  keep_results=...)` factory wrapper that the README and
  quickstart use does not propagate it. Add the keyword to the
  factory signature so the Pass-5 ergonomics improvement is
  reachable from the public-API surface.

> Additional findings from the **seventh 2026-05-18 review pass**
> (focus: status check four days after Pass-6 implementation block
> was deferred; release-cut overdue, doc-builder convention drift
> still open, validator docstring/code drift confirmed unfixed):

- **`__version__` still pinned at `0.4.0` — release cut overdue.**
  `pyproject.toml`, `src/groundinsight/__init__.py:__version__` and
  the audit-readme docstring sweep all still report `0.4.0` as of
  2026-05-18, even though the `[Unreleased]` block carries the
  Pass-4 + Pass-5 *implementation* blocks (transient state-space
  solver, capacitance support, pandapower importer hardening,
  `Network.invalidate_paths`, `db_session` alias, `Network`
  frequencies validator, `keep_results=` kwarg). The
  audit-readme-docs-2026-05-14 pass already flagged
  `docs/api/io.md` "New in 0.5" markers as a forward-looking
  inconsistency; the seventh pass confirms the bump is now
  *four passes* overdue. Cut `0.5.0` before the next audit so
  the backlog math returns to a normal cadence and the docs
  stop advertising features that the canonical ``__version__``
  does not yet ship.
- **`mkdocs.yml docstring_style: google` confirmed still set on
  2026-05-18.** Pass 6 flagged it (`audit-readme-docs-2026-05-14`
  migrated docstrings to numpy but the rendering parser stayed
  on Google); seventh-pass cross-check on the on-disk file shows
  the line is unchanged. Numpy-style ``Parameters`` /
  ``Returns`` sections continue to render as plain text under
  the Google parser. A single-line ``docstring_style: numpy``
  flip closes the regression; without it, every release of
  `0.5.x` will ship docs with the per-field type annotations
  dropped.
- **`mkdocs.yml` Examples nav still lists 5 of 15 notebooks.**
  On-disk `notebooks/` carries 15 ipynb files (01..13 plus
  `14_audit_pass4_fixes.ipynb` and `15_audit_pass5_fixes.ipynb`);
  the rendered nav exposes only `examples/minimal.ipynb`,
  `mv_ring.ipynb`, `mv_ring_transient.ipynb`,
  `pandapower_import.ipynb` and `fault_sweep.ipynb`. The
  `docs/examples/` directory only contains the five entries that
  the nav lists, so the remaining 10 notebooks are not even on
  the docs include path. Either bulk-promote
  `notebooks/01..13` into `docs/examples/` and wire them under
  a `Notebooks:` nav section, or document the convention "research
  notebooks live in `notebooks/`, curated examples in
  `docs/examples/`" in `docs/index.md`.
- **`_validate_frequencies` doc-vs-code drift still open.** The
  field-validator docstring on `Network.frequencies` reads
  "Reject empty / duplicate / non-finite / non-positive
  frequency lists" but the implementation explicitly accepts
  ``f == 0`` (DC) — the comparison is ``if f < 0`` rather than
  ``if f <= 0``. Pass 6 flagged the drift; on-disk file as of
  2026-05-18 still carries both the misleading docstring *and*
  the DC-friendly implementation. Rephrase the docstring to
  "Reject empty / duplicate / non-finite / negative frequency
  lists; ``f = 0`` (DC) is permitted for the FFT transient
  solver" — no behaviour change required.
- **`_validate_frequencies` still does not warn on non-monotonic
  input.** Symmetric to the `groundfield.solver.engine.Engine`
  Pass-5 fix (`EngineFrequencyOrderWarning`): the
  `Network.frequencies` validator on this side accepts a
  descending / shuffled list silently. The FFT transient solver
  uses the *order* of `Network.frequencies` to map to the FFT
  bins, so `[100.0, 50.0]` produces a transient with the spectral
  bins reversed. Add a `NetworkFrequencyOrderWarning(UserWarning)`
  category (and the symmetric
  `Network.with_frequencies(*freqs, preserve_order=True)`
  constructor) and emit on non-strict-monotone input. Seventh-pass
  re-emphasis on the Pass-6 cross-repo convention finding.
- **`Network.invalidate_paths()` still mutates via `.clear()`.**
  The body reads ``self.paths.clear()``; an external snapshot
  ``saved = dict(network.paths)`` captured before the call loses
  its values because the dict-keyed `Path` instances were the
  same objects that ``self.paths`` referred to (the snapshot is
  shallow). Rebind ``self.paths = {}`` instead so the snapshot
  survives — mirrors the Pass-4 atomic-rebind fix in
  `inverse_rho_f.evaluate_max_epr_under_k`. Six passes flagged
  the pattern; seven passes confirm it remains unfixed.
- **`pathfinder._GRAPH_CACHE` / `_FIND_PATHS_CACHE` remain
  unbounded.** Pass 5 added the structural fingerprint and Pass 6
  proposed an LRU cap (`gi.set_pathfinder_cache_size(n)`, default
  256). The on-disk pathfinder still uses two plain module-level
  dicts with no `OrderedDict` / `WeakKeyDictionary` /
  `functools.lru_cache` wrapper. A 100-scenario outage sweep
  therefore still leaks one cache entry per scenario; pass 7
  re-elevates this as the highest-leverage memory-bookkeeping
  finding on the package.
- **`outage_context` still does not clear the pathfinder cache
  on exit.** The Pass-6 Fixed-backlog entry recommended a
  ``clear_pathfinder_cache(network)`` call in the ``finally``
  branch; the on-disk `simulation/outage.py` does not yet make
  the call. Combined with the unbounded-cache finding above,
  this is the fastest path to a multi-hundred-megabyte resident
  memory footprint on a long dashboard session over a
  10-scenario sweep.
- **`set_active_fault(keep_results=)` still not on the
  top-level factory.** The bound method on `Network` accepts
  the kwarg (Pass-5 implementation), but
  `groundinsight.network_operations.set_active_fault(network,
  fault_name)` — the documented user-facing entry point — does
  not propagate it. Either add the kwarg to the factory
  wrapper or document the discrepancy in
  `docs/quickstart.md`. Seventh-pass re-emphasis.
- **`db_session` synonym carries cross-API maintenance debt.**
  Pass 5 added ``db_session = session`` and listed both names
  in ``__all__``; the seventh-pass review of `start_dbsession`
  / `close_dbsession` confirms both helpers rebind both names
  (lines 305, 348). The contract is currently kept by hand —
  any future helper that rebinds ``session`` without ``db_session``
  (e.g. a planned ``swap_dbsession()`` context manager) will
  silently drift. Either retire the alias (and add a
  ``DeprecationWarning`` for one release cycle) or introduce a
  module-level ``_SESSION_LOCK`` + a `_set_session(new)` helper
  that the persistence factories must call, so the alias is
  pinned by a single source of truth.

### Changed (Backlog — pending implementation)

- **`__init__.__all__` is incomplete.** The persistence helpers
  (`save_network_to_db`, `load_network_from_db`,
  `save_bustype_to_db`, `load_bustypes_from_db`,
  `save_branchtype_to_db`, `load_branchtypes_from_db`,
  `save_network_to_json`, `load_network_from_json`,
  `start_dbsession`, `close_dbsession`) and `set_log_level` are
  exposed at the top level via attribute access but missing from
  `__all__`. Add them so type-checkers and `from groundinsight
  import *` see them.
- **`network_operations.create_source` docstring does not point at
  `create_voltage_source`**. Add a `See also` paragraph so users
  reading only the current-source factory discover the Thevenin
  alternative.
- **Replace `lru_cache(maxsize=512)` on
  `utils.impedance_calculator._compile_formula` with an
  unbounded `functools.cache`** (or a `WeakValueDictionary`) so
  long Pareto sweeps do not silently miss the cache when the 513th
  unique formula appears. The current limit is silent.
- **`network_operations._warning_parallel_coeffcient` typo** —
  rename to `_warning_parallel_coefficient`. Internal but appears
  in stack traces.
- **`plotting.plot_bus_voltages`** sets a `legend(title="Frequency")`
  even in RMS-mode and re-runs `xticks(rotation=45, ha="right")`
  outside the mode branch, clobbering the carefully positioned
  ticks of the multi-frequency case. Move the duplicate `xticks`
  call into the RMS-mode branch and drop the misleading legend
  title in RMS mode.
- **`electrical_network` falls back to `scaling = 1`** for missing
  per-frequency entries in `Fault.scalings`. Log a warning so users
  notice that they are getting an unintentional full-strength
  injection at a harmonic.

### Docs (Backlog — pending implementation)

The package now exposes a long list of features (transient solver,
Thevenin source, pandapower importer, outage studies,
`inverse_rho_f`, RLC formula fields) that are documented only via
auto-generated docstrings. The doc site needs a structured pass
before the next release so the user-facing surface matches the
public API.

- **`docs/api/transient.md`** exists, but the example uses
  `from groundinsight import waveforms` whereas the canonical form
  is `gi.waveforms.step(...)`. Pick one form and use it consistently
  across the page, the `TransientStudy` docstring, and the
  notebook `10_transient_fft.ipynb`.
- **`docs/api/transient.md` still lacks a "Parameterising the
  lumped RLC formulas" section** showing how to author
  `R_self_formula`, `L_self_formula`, `C_self_formula`,
  `R_mutual_formula`, `M_mutual_formula` on a `BranchType` and the
  matching `R_formula`, `L_formula`, `C_formula` on a `BusType`.
  The values are referenced in prose but never demonstrated.
- **`docs/api/outage.md` example uses `include_base_case=True`** —
  the actual API parameter is `include_base` (`outage.py:349`).
  Fix the example.
- **`docs/api/analysis.md` uses reST `:func:` cross-references**
  that mkdocstrings does not render. Convert to mkdocs link
  syntax.
- **No `docs/api/persistence.md`** — `gi.start_dbsession`,
  `gi.close_dbsession`, the `save_*_to_db` / `load_*_from_db`
  family and the JSON helpers are all top-level user-facing
  functions but only documented indirectly via the autodoc of
  `groundinsight.database.crud`. Add a dedicated page.
- **`docs/api/index.md` does not list `gi.set_log_level`.** The
  helper has shipped in 0.3.2 and is in `__all__`-via-attribute but
  appears nowhere in the docs.
- **`network_operations.parallel_coefficient` docstring claims
  default `None`** — the actual default is `1.0`. Fix.
- **`simulation/transient.py` module docstring still claims
  "Mutual coupling is not yet evaluated by the FFT solver"**
  without the corresponding "but is evaluated by the state-space
  solver" addendum. Refresh the "Design choices recorded" block.
- **README** does not mention the new RLC formula fields, the
  state-space solver, or the inverse rho-f catalog selection
  helper. Bring it level with the [Unreleased] block before the
  next release tag.

> Additional doc gaps from the **second 2026-05-10 review pass**:

- **No `docs/api/waveforms.md`.** The three factory functions
  `gi.waveforms.step`, `gi.waveforms.sinusoidal_with_dc_offset`,
  `gi.waveforms.damped_oscillation` are top-level user-facing —
  the user *must* call one to feed `TransientStudy` — but
  mkdocstrings only auto-documents them indirectly via the
  `simulation.transient` page. Add a dedicated page that lists
  each waveform with its mathematical form, parameter table and
  a one-cell rendering example.
- **No `docs/api/analysis.md` section on the rho-f model
  inversion.** The page exists but only documents
  `find_max_rho_scaling` (single-parameter inversion).
  `evaluate_max_epr_under_k`, `find_max_rho_f_scaling` and
  `select_rho_f_from_catalog` are all part of `gi.analysis`'s
  public surface (`analysis/__init__.py` re-exports them) and
  re-exported on the package top level, but the docs page does
  not even mention them by name.
- **`docs/api/outage.md` Compare semantics.** The page documents
  `compare_buses(against=...)` but never explains how the long
  format works (`metric` column, `value`, `delta_vs_<ref>`,
  `delta_pct_vs_<ref>`), nor what happens when the reference
  scenario is missing from the study. A two-row sample frame
  would close the gap.
- **Notebook coverage in the docs site.** `mkdocs.yml` registers
  only three legacy notebooks under `examples/`
  (`simple.ipynb`, `cired.ipynb`, `low_voltage.ipynb`). The 13
  notebooks under `notebooks/01…13` are not part of the doc
  site, so the user reads `docs/transient.md` and is left to
  hunt the matching `notebooks/10_transient_fft.ipynb` in the
  repo. Either include the full notebook set under a new
  `Notebooks:` nav section or link to them by URL from
  `docs/transient.md`.
- **`docs/concepts.md` and `docs/transient.md` do not cross-link
  to the `analysis` rho-f helpers.** A reader of "concepts"
  reaches the rho-f model formula but no pointer to the helpers
  that invert it. Add a short cross-reference.
- **`docs/installation.md` does not list the `pandapower`
  optional extra in the same way the README does.** The previous
  audit added an "Optional extras" section to
  `installation.md`; verify after the next docs rebuild that the
  syntax matches `pyproject.toml`.

> Additional doc gaps from the **third 2026-05-12 review pass**:

- **No `docs/api/pandapower_import.md` sub-page**, even though the
  importer is now the recommended path for AP 1 case studies on real
  distribution-network data. The single `docs/api/io.md` page lumps
  every importer together and rendering one big page makes it hard
  to find the `voltage_level_kV` argument or the skip-reason
  vocabulary (`voltage_level_mismatch`,
  `endpoint_off_target_voltage_level`, `endpoint_bus_missing`).
  Split the page or add a top-level table of skip reasons.
- **`docs/api/transient.md` does not document the
  `branch-current sign convention` change shipped in
  `[Unreleased]`** — users still on the old (inverted) convention
  will see a silent sign flip after upgrade. Add a `Migration`
  callout that mirrors the CHANGELOG entry.
- **`docs/api/transient.md` Carson-mutual section missing.** The
  module ships `R_mutual` / `M_mutual` substitution and a
  one-time warning for the voltage-source case, but neither the
  trigger condition nor the substitution itself is documented in
  the user-facing page. The reader has to open the source to learn
  why the mutual contribution silently disappears with a voltage
  source.
- **`docs/concepts.md` does not mention the `0.4.0` `active` flag**
  on Bus / Branch and the resulting interaction with the
  pathfinder. New users importing from pandapower with
  `in_service=False` rows see the flag in the dataclass but no
  conceptual write-up.
- **`mkdocs.yml:90`** still references
  `https://polyfill.io/v3/polyfill.min.js?features=es6` — the
  pass-1 and pass-2 audits already noted the security-relevant
  CDN; verifying again on 2026-05-12 confirms the line is still
  there. Three-character delete; please ship before the next
  release.
- **Notebook front-matter consistency.** Notebooks 01..13 do not
  carry a uniform `Title` / `Released in` block, so the docs-site
  notebook index (currently three legacy notebooks) cannot pick
  them up automatically. Adopt the `mkdocs-jupyter` metadata
  contract (`# title:`, `# tags:` markdown cell at the top).

> Additional doc gaps from the **fifth 2026-05-13 review pass**:

- **`docs/api/pathfinder.md` does not document the new module
  caches** (`_GRAPH_CACHE`, `_FIND_PATHS_CACHE`, the helper
  `clear_pathfinder_cache()`). After the Pass-4 implementation
  shipped the caches as a public surface, the failure mode
  (stale cache after in-place mutation) needs an admonition.
- **`docs/api/core_models.md` does not enumerate
  `Network.invalidate_paths`** even though the helper is the
  intended escape hatch for the pathfinder cache. mkdocstrings
  picks the symbol up only when the page references it
  explicitly; add a one-line list entry.
- **`docs/concepts.md` does not mention the "active subset"
  topology rules**. Pass-4 implementation made
  `Bus.active=False` and `Branch.active=False` drop the row from
  the adjacency graph; the concepts page still describes the
  topology as the full bus+branch tree. Update the relevant
  paragraph.
- **`docs/api/transient.md` does not document the
  `BranchType.R_mutual_formula` / `M_mutual_formula` substitution
  warning** (Pass-3 doc gap). The module ships the substitution
  *and* the one-time warning for the voltage-source case; the
  reader has to open the source to learn why the mutual
  contribution disappears with a voltage source.
- **`docs/installation.md`** does not list the `pandapower`
  optional extra in the same way the README does (Pass-2 doc
  gap, verified open). Sync the prose with `pyproject.toml`.
- **`README.md` Roadmap section** still references items
  (`Network.res_touch_voltages`, `assess_touch_voltage`,
  PEN-aware `BranchType`) that have moved to the Roadmap
  section of this changelog. The README is the front door —
  it should either re-state the roadmap inline or link here.
- **No "Stable import surface" section in any docs page.**
  The package exposes nearly all helpers at top level
  (`gi.create_network(...)`) but the `__init__.py` `__all__`
  list now mixes a real symbol with the `db_session` typo
  (Fixed-backlog fifth-pass entry above). A short page that
  spells out the stable surface — and explicitly calls out
  which sub-modules (`gi.models`, `gi.simulation.transient`,
  `gi.io.pandapower_import`) are *not* part of the stable
  contract — would prevent the public-surface drift.

> Additional doc gaps from the **sixth 2026-05-14 review pass**:

- **`docs/api/pathfinder.md`** does not document the cache memory
  contract introduced by the Pass-5 structural fingerprint. A
  user-facing note that "the cache is unbounded by default; call
  `clear_pathfinder_cache()` between scenario sweeps or rely on
  `Network.invalidate_paths()` for per-network eviction" would
  give the reader a clear footgun guardrail. Coupled with the
  Fixed-backlog sixth-pass cache-cap proposal above.
- **`docs/api/core_models.md`** does not list the new
  `keep_results: bool = False` parameter on
  `Network.set_active_fault`. Pass 5 added the keyword and an
  Args block on the method docstring, but the curated
  Markdown page has its own table that omits it. The
  documentation rendered on the website therefore still
  describes the old single-arg form.
- **`docs/quickstart.md`** uses the example
  ``net.set_active_fault("f1")`` without the new
  ``keep_results=`` keyword. A short note "Pass
  ``keep_results=True`` if you want to replot the cached
  solve without recomputing" would surface the new ergonomics
  without complicating the quickstart.
- **`docs/concepts.md`** "Topology" section still claims
  "every bus is part of the admittance matrix"; the Pass-4
  `active=False` semantics dropped this years ago. Drift on
  the conceptual page that new users hit first.
- **`docs/api/io.md`** mentions "New in 0.5" features even
  though `__version__` is `0.4.0` (the audit-readme-docs-
  2026-05-14 pass flagged this as a forward-looking marker).
  Either pin the docs to the actual installable version (and
  mark unreleased helpers with an admonition box) or push the
  0.5 bump to `pyproject.toml`, `__init__.py.__version__` and
  `CITATION.cff`. Sixth-pass: still open.
- **`docs/transient.md`** does not yet explain the
  monotonic-frequencies expectation. The transient solver
  FFT-bin assignment relies on the order of
  `Network.frequencies`; without a docs warning the
  Fixed-backlog sixth-pass "non-monotonic frequencies"
  finding will keep reappearing in support questions.
- **`mkdocs.yml` Examples nav vs. notebooks contract.**
  Either the `notebooks/14_audit_pass4_fixes.ipynb` and
  `notebooks/15_audit_pass5_fixes.ipynb` should be promoted
  into `docs/examples/` and listed under the Examples nav
  (single source of truth: the docs site), or they should be
  documented as "research artifacts, not site content" in
  `docs/index.md`. The current state — they exist on disk,
  the Pass-5 changelog claims they are on the nav, the nav
  doesn't list them — is the worst of both worlds.

> Additional doc gaps from the **seventh 2026-05-18 review pass**:

- **`mkdocs.yml docstring_style` still `google` (verified on
  2026-05-18).** Seventh-pass re-confirmation of the
  sixth-pass finding. A two-character edit (`google` →
  `numpy`) restores the type annotations on every
  mkdocstrings-rendered page. Until then the rendered docs
  silently degrade after each docstring sweep.
- **`docs/api/io.md` still carries "New in 0.5" markers.**
  Two ``**New in 0.5.**`` admonitions (around lines 91 and
  94 — the `vn_kv_unparsable` and `self_loop` skip reasons)
  appear in the rendered docs even though the installable
  package reports ``__version__ = 0.4.0``. Either move the
  admonitions into a ``!!! warning "Forward-looking
  documentation"`` block or push the `0.5.0` bump through
  `pyproject.toml`, `__init__.__version__` and `CITATION.cff`
  before the next release tag.
- **`docs/api/core_models.md` still does not document the
  `keep_results: bool = False` parameter** on
  `Network.set_active_fault`. Pass 5 added the keyword,
  Pass 6 flagged the doc gap, Pass 7 confirms the rendered
  Markdown page (lines around the `set_active_fault` table)
  still omits it.
- **`docs/quickstart.md` does not mention `keep_results=`.**
  Pass-5 closed the kwarg on the bound method; the
  quickstart still demonstrates the single-arg form.
  Two-line edit.
- **No `docs/api/persistence.md` page.** Pass-1 finding,
  seventh-pass confirmation: the persistence helpers
  (`start_dbsession`, `close_dbsession`, `save_*_to_db`,
  `load_*_from_db`, JSON helpers) are top-level user-facing
  API but are only documented indirectly via `database.md`.
- **`docs/concepts.md` "Topology" paragraph still claims
  "every bus is part of the admittance matrix"** despite
  the Pass-4 active-subset semantics. Sixth-pass flagged
  this; seventh-pass confirms the on-disk page still
  carries the pre-active-flag wording.
- **README "Quickstart" snippet not yet aligned with the
  `0.4.0` features.** Sixth-pass entry: README is the front
  door, and it currently lists Roadmap items that have
  already shipped. Seventh-pass re-elevation given the
  release cut is now overdue.

### Tests (Backlog — pending implementation)

- **No dedicated unit test for `gi.create_voltage_source`
  validation paths** (mismatched frequency keys, zero
  `source_impedance`, missing fields, voltage-mode source with
  `values` set). Exercise each `ValueError` branch in
  `Source._validate_source_mode`.
- **No tests for `gi.set_log_level`** — `tests/test_logging.py`
  tests the package logger but does not exercise idempotence and
  level changes.
- **`tests/test_transient.py` only uses single-frequency
  networks.** Add a `[50, 250]` network state-space test
  cross-checking against the FFT solver to catch the multi-tone
  state-space mutual-coupling regression.
- **`tests/test_pandapower_import.py` has no coverage for
  silently-skipped trafos and switches.** Add
  `test_from_pandapower_silently_skips_trafos_and_switches`.
- **`tests/test_outage.py` does not assert dtype or finite-value
  semantics on `delta_pct_vs_base`** and would not catch the
  `_ref_value == 0` `inf`/`NaN` bug listed above. Add a regression
  test where the reference bus sits on the source bus.
- **No JSON / SQLite round-trip test for the new RLC formula
  fields** (`R_formula`, `L_formula`, `C_formula`,
  `R_self_formula`, `L_self_formula`, `C_self_formula`,
  `R_mutual_formula`, `M_mutual_formula`). Confirm the fields
  survive both serialisation paths.
- **`tests/test_inverse_rho_f.py` never exercises the
  `evaluate_max_epr_under_k` "best-effort restore active_fault"
  path.** Add a test that pre-sets `active_fault="some_other"`
  and asserts it is restored after the helper returns.

> Additional test gaps from the **third 2026-05-12 review pass**:

- **No regression test for the `vn_kv == None` pandapower bus row**
  (the `float(row.get("vn_kv", 0.0) or 0.0)` silent-zero bug listed
  above). Build a tiny synthetic `pp.bus` table with `vn_kv=None`
  and assert that the row is either skipped *with a warning* or
  raised on.
- **No regression test for `from_pandapower` returning an empty
  Network**. Pass a `voltage_level_kV` that matches nothing and
  assert that the result reports zero buses *plus a warning entry*.
- **No regression test for `find_max_rho_f_scaling` reporting the
  EPR breakdown at the actually-admissible `c_max`.** Verify that
  `max(epr_rms_per_bus_at_c_max.values()) == max_epr_rms_at_c_max`
  after the bisection terminates on `tol_rel`.

> Additional test gaps from the **fourth 2026-05-12 review pass**:

- **No test that asserts `set_log_level` is handler-idempotent.**
  Call the helper three times with alternating levels and assert
  `len([h for h in logger.handlers if isinstance(h,
  logging.StreamHandler)]) == 1` afterwards. Locks in the bug
  reported in the Fixed-backlog fourth pass above.
- **No test that asserts `start_dbsession` rejects a second call
  without a prior `close_dbsession`.** A two-step pytest fixture
  reusing the module-global `db_session` would silently leak the
  previous engine; pin the contract once it is hardened.
- **No test for the `Fault.scalings` int-vs-float key coercion.**
  Construct a fault with `scalings={50: 1.0+0j}` (int key) on a
  network with `frequencies=[50.0]` and assert that either the
  fault is rejected at validation time or that the scaling is
  applied to the matching float-keyed frequency.
- **No test that `pathfinder.PathFinder` is reused across calls
  on the same topology.** Once the caching helper lands, lock in
  the cache-hit count via a `mocker.spy` over the graph
  constructor.
- **No round-trip test for the docs-site notebook nav.** Add a
  `tests/test_docs.py::test_examples_nav_covers_notebooks` that
  loads `mkdocs.yml`, parses the `Examples` nav, and asserts every
  `notebooks/*.ipynb` is either listed or explicitly excluded —
  prevents future drift between notebook tree and rendered docs.

> Additional test gaps from the **fifth 2026-05-13 review pass**:

- **No test that `Network.invalidate_paths()` is scoped to the
  current network.** Build two networks, populate the
  `_GRAPH_CACHE` for both via `PathFinder(net_a)` /
  `PathFinder(net_b)`, then call `net_a.invalidate_paths()` and
  assert that `net_b`'s graph is still in the cache.
- **No test that `pathfinder._GRAPH_CACHE` does not falsely hit
  after `id` recycling.** Build a network, drop it, garbage-
  collect, build another network at the (likely-recycled) id
  with a different topology, and assert that the new network's
  PathFinder builds a fresh graph rather than picking up the
  stale one.
- **No regression test for `from groundinsight import db_session`
  ImportError.** Either remove `db_session` from `__all__` (and
  add a one-liner asserting the symbol is *not* importable) or
  alias it (and add the symmetric "imports the same scoped
  session" test).
- **No regression test for duplicate / negative / nan
  frequencies on `Network.frequencies`.** Build a network with
  `frequencies=[50.0, 50.0]` and assert the field validator
  rejects it (once it lands). Today the constructor accepts it
  silently and downstream solvers double-count.
- **No regression test that `set_active_fault` clears the
  previous `network.results[fault_name]`.** The docstring
  promises the side effect; pin it so it cannot regress after
  the planned `keep_results` flag.
- **No regression test for `close_dbsession` with a partially
  initialised state** (`session is None` but `engine is not
  None`). Construct the corrupted state manually and assert
  the helper either logs a warning *or* tears the engine down,
  rather than raising `AttributeError`.

> Additional test gaps from the **sixth 2026-05-14 review pass**:

- **No regression test for monotonic `Network.frequencies`.**
  Build a `Network(frequencies=[100.0, 50.0])`, run a
  `TransientStudy` and assert that either a
  `NetworkFrequencyOrderWarning` fires *or* the FFT bin mapping
  uses the user-supplied order — whichever behaviour is locked
  in by the sixth-pass implementation.
- **No regression test for unbounded pathfinder cache.** Run
  `Pa thFinder` over 1000 distinct active subsets in a loop
  and assert `len(_GRAPH_CACHE) <= maxsize` after the
  sixth-pass cache cap lands; today the same loop fills the
  dict indefinitely.
- **No regression test for `outage_context` cache eviction.**
  Run a 10-scenario outage sweep and assert
  `len(pathfinder._GRAPH_CACHE)` is back to the pre-sweep
  size after the contexts exit. Today the cache grows by 10
  per sweep.
- **No regression test for nested `outage_context`.** Stack
  two `outage_context` blocks over disjoint and overlapping
  scenarios and assert the post-exit state matches the
  pre-entry state for every bus / branch. Today nested
  contexts leak the inner's restore through the outer's
  baseline.
- **No regression test for `Network.invalidate_paths()` and
  external snapshot integrity.** Capture
  `saved = dict(network.paths)` before the call, then ensure
  `saved` survives the invalidation call. Pins the
  atomic-rebind behaviour proposed in the sixth-pass
  Fixed-backlog.
- **No `mkdocs build --strict` test** in `tests/test_docs.py`
  / CI. The Pass-3 / 4 / 5 doc backlog entries all assume the
  docs build is silent; without a strict-mode test the
  Pass-5 mkdocstrings-style mismatch (Google parser on
  numpy-style docstrings) goes undetected for at least one
  release cycle. Add a `subprocess`-based `mkdocs build
  --strict --site-dir tests/_site_build` invocation guarded
  by a `mkdocs` extra.
- **No regression test for `gi.set_active_fault` top-level
  factory `keep_results=` plumbing.** Once the keyword lands
  on the factory wrapper, pin it with a one-line test that
  calls `gi.set_active_fault(net, "F1", keep_results=True)`
  and asserts `net.results["F1"]` is preserved.

> Additional test gaps from the **seventh 2026-05-18 review pass**:

- **No regression test for `pathfinder._GRAPH_CACHE` eviction
  on `outage_context` exit.** Sixth-pass entry; seventh-pass
  re-emphasis. Once the ``finally`` branch gains the
  ``clear_pathfinder_cache(network)`` call, pin the contract
  with a 5-scenario sweep that asserts
  ``len(_GRAPH_CACHE)`` is back to the pre-context size on
  exit.
- **No regression test for the `_validate_frequencies` doc-
  vs-code drift.** Construct a `Network(frequencies=[0.0])`
  and assert the validator accepts it (DC), and a separate
  `Network(frequencies=[-1.0])` and assert it rejects it.
  Pins the docstring rewording proposed in the seventh-pass
  Fixed-backlog.
- **No regression test that `set_log_level` is handler-
  idempotent across `_groundinsight_console_handler` cycles.**
  Pass-4 added the sentinel-attribute strategy; pin it with a
  three-call alternating-level test that asserts
  ``sum(1 for h in pkg_logger.handlers if isinstance(h,
  logging.StreamHandler)) == 1``.
- **No CI-level `mkdocs build --strict` test.** Sixth-pass
  finding remains open. Seventh-pass re-elevation given the
  `docstring_style` drift would be caught on the very first
  build.
- **No regression test for the `pyproject.toml`-vs-
  `__version__` parity.** A two-line `test_release.py::
  test_version_matches_pyproject` would catch the
  "advertise 0.5 in docs while shipping 0.4" drift before
  the next release tag.

## Part 4 — roadmap proposals as recorded per pass

Superseded by the consolidated *Roadmap* section at the end of
`CHANGELOG.md`, which de-duplicates these blocks and drops the items that
have since shipped. Kept verbatim for provenance: this is what each pass
actually proposed, on the date it proposed it.

### Roadmap (additions from the 2026-07-19 review pass)

Confirmed with the maintainer; sequenced *after* the pass-8 bug-fixes.

- **Conductor thermal-limit check (equipment-integrity assessment, step 1).**
  Complements the planned EN 50522 touch-/step-voltage (person-safety) helper
  with a *conductor-integrity* check. Add cross-section `A` and material
  constant `k` (Cu / Al / steel, per IEC 60949 / EN 60865-1) to `BranchType` /
  `BusType`, plus `check_conductor_limits(network, fault, t_k)` returning, per
  branch/bus, the thermally-equivalent short-time current `I_th`, the
  admissible `k·A/√t_k`, and a pass/fail flag. Mechanical (electrodynamic,
  `F ∝ i_p²`, needs conductor geometry) is a deliberate second step.
- ~~**Short-circuit characteristic quantities on sources/faults (`i_p`,
  `I_th`).**~~ *Done (F3) — see the F2/F3 entry above.* Prerequisite for the
  thermal check. Extend `Source` / `Fault` with
  IEC 60909 quantities (`I_k''`, `kappa` or `R/X`, clearing time `T_k`).
  Superposition rule: superpose the linear AC RMS branch/shield currents as
  today, then apply the (non-linear) peak/thermal factors to the aggregate
  (`i_p = kappa·√2·I_s,RMS`, `I_th = I_s,RMS·√(m+n)`); the existing transient
  solver is the exact fall-back for mixed-`R/X` cases. Do **not** superpose
  `i_p`/`I_th` directly.
- ~~**Import IEC 60909 results from pandapower `calc_sc`.**~~ *Done (F2) — see
  the F2/F3 entry above.* Rather than
  re-implementing 60909, ingest pandapower's short-circuit result (`I_k''`,
  `i_p`, `I_th`, `R/X`) into groundinsight sources — docking onto the existing
  `auto_phase_currents` integration hook (the code already notes it as "the
  intended integration point for … pandapower single-phase short-circuit
  results"). Chosen over a native 60909 core to reuse the ecosystem.
  *Implementation note:* pandapower does **not** publish `i_p` / `I_th` for
  `fault="1ph"`, so those two are derived here rather than ingested; only
  `I_k''` and the sequence impedances come from pandapower.
- **Web GUI** remains long-term; the reserved `api/` package and the planned
  Plotly backend are the natural first building blocks (REST + Plotly before a
  thin front-end).

### Roadmap (additions from the eighth 2026-05-25 review pass)

- **Cut `0.5.0` immediately.** Five audit passes have flagged
  the missing release; the eighth pass treats this as the
  single most overdue maintenance item.
- **`gi.show_versions()` documentation polish.** Pass-7
  shipped the helper; add a `docs/api/database.md`
  „Cross-repo version helper" sub-section so the helper is
  reachable from the rendered docs (the existing reference
  in `tests/test_audit_pass7_fixes.py` is the only public
  call site).
- **`gi.cross_repo` namespace + `docs/cross-repo.md`** —
  blocked on ADR-0013 in `groundfield` (still unwritten).
  Track the cross-repo dependency in the
  `groundinsight` Roadmap.
- **`gi.audit_apply(report_path)` helper.** Take the
  cross-repo proposal seriously: a single CLI entry-point
  that reads a CHANGELOG-formatted audit report and writes
  the bullets into the matching `[Unreleased] → Fixed
  (Backlog)` sub-section. Avoids the manual copy-and-paste
  drift that has produced eight repetitive audit reports.

### Roadmap (additions from the 2026-05-10 audit)

The following items are *not yet scheduled* and should be triaged
into `[Unreleased]` once design questions are settled.

- **Touch- / step-voltage assessment helpers** —
  `Network.res_touch_voltages()` and a thin `assess_touch_voltage(
  t_clearing_ms, standard='EN50522'|'IEEE80')` returning the
  admissible limit and a pass/fail flag per bus. The single most
  useful safety-engineering deliverable on top of the current
  steady-state solver and the natural follow-on to the
  `ResultTouchVoltage` roadmap item.
- **PEN-conductor-aware `BranchType`** — current `BranchType`
  distinguishes only `grounding_conductor: bool`. For TN-Ortsnetze
  the PEN sits in parallel with the cable shield and the soil;
  modelling it explicitly (`pen_impedance_formula`) gives a
  cleaner reduction-factor split for low-voltage networks.
  Specifically relevant to AP 1 of the dissertation.
- **Time-series `Source.from_waveform(waveform, frequencies)`** —
  convenience factory that does the FFT once for a Thevenin
  source, instead of forcing the user to assemble a per-frequency
  `voltage` dict by hand. Closes the FFT-transient input loop.
- **`Network.verify_steady_state_match(transient_result)`** —
  promote the manual cross-check used in
  `test_state_space_matches_fft_on_lti_network` to a public
  diagnostic so a user can validate any new transient setup
  against the per-frequency phasor solve.
- **Parallel per-frequency solve** — `concurrent.futures.ThreadPoolExecutor`
  over the frequency loop in `solve_network`. SciPy `splu` releases
  the GIL, so a near-linear speed-up is realistic for harmonic
  studies (10–30 frequencies). Low effort, high payoff.

> Additional roadmap candidates from the **second 2026-05-10 review
> pass**:

- **`gi.waveforms.from_array(t_samples, values)`** — convenience
  factory that wraps a user-supplied numerical array (measured
  fault-current trace, e.g. from a digital fault recorder) into
  the `Callable[[np.ndarray], np.ndarray]` contract via
  `np.interp`. Closes the "BYO waveform" path without forcing the
  user to write the lambda.
- **`gi.assess_against_en50522(network, fault, t_clearing_ms)`**
  — companion to the planned touch-voltage assessment helper.
  Reads the prospective touch voltages from the steady-state
  solve, pulls the EN 50522 Table B.4 limit from the
  `groundfield.postprocess.safety` companion (once the
  `[groundfield]` extra is installed), returns a per-bus
  pass/fail DataFrame. Closes the safety-engineering loop on the
  `groundinsight` side and avoids re-implementing the limit
  curve.
- **`gi.cut_unreleased_to_release()`** in `scripts/release.py` —
  the `groundmeas` release script already moves the
  `[Unreleased]` block on bump (see `scripts/_changelog.py`).
  `groundinsight/scripts/release.py` does the version-string
  bump but not the changelog-section move; the maintainer has
  to do it by hand. Port the helper across repos.
- **Cut a `0.5.0` release.** The `[Unreleased]` block has grown
  by *transient state-space solver, capacitance support,
  voltage-source state-space path, Carson-style mutual coupling,
  branch shunt pi-section lumping, four new notebooks*. Cutting
  a release lets `groundfield/io/groundinsight.py` pin a
  groundinsight version that has the rho-f catalog helper, and
  unsticks the cross-repo bridge work.

> Additional roadmap candidates from the **third 2026-05-12 review
> pass**:

- **`Network.invalidate_paths()` public helper** — formalise the
  contract that `network.paths` must be re-derived after any
  `active` flip or topology mutation. Today the outage helper and
  the rho-f inverter both clear-and-update the dict by hand;
  exposing a method centralises the rule and lets future helpers
  (e.g. `apply_outage(net, outage)` outside of a `with` block)
  share it.
- **`gi.from_pandapower_multi_voltage(net, defaults_map)`** —
  multi-voltage-level importer that takes a `Dict[float,
  ImportDefaults]` and produces one `Network` per voltage level
  (or one combined Network with trafo branches once the
  `include_trafos` path lands). Closes the gap between the
  single-voltage-level importer and the AP 1 real-network case
  studies that span 110 / 20 / 0.4 kV.
- **`gi.TransientStudy.from_steady_state(network, fault_name)`** —
  convenience factory that pre-populates the transient study from
  the most recent `run_fault` result on the same network, copying
  the fault scalings and source observation set. Cuts notebook
  boilerplate roughly in half for the Phase 4 examples.
- **`gi.SoilModel(two_layer)` + symbolic recognition** — the
  long-promised two-layer `SoilModel` bridge to `groundfield` is
  still in the roadmap "Near term" block but has not gained any
  implementation since 0.4.0; it has become the longest-standing
  open item on the cross-package interface. Prioritise it now that
  the rho-f catalog work is complete.

> Additional roadmap candidates from the **fifth 2026-05-13 review
> pass**:

- **`gi.PathfinderConfig(cache_scope="per_network" | "global" |
  "none")`** — make the cache-scope decision explicit instead of
  silently sharing the module-level dict across notebooks.
  Closes the Fixed-backlog fifth-pass concern about
  `Network.invalidate_paths` over-clearing.
- **`gi.Network.frequencies` as a `tuple[float, ...]`** —
  switching the field type to a tuple (Pydantic accepts it) plus
  a `field_validator` that rejects duplicates, NaN, ±inf and
  non-positive values gives stricter contracts than the current
  `List[float]` and also makes the network hashable for the
  pathfinder cache key (closes the `id(network)` collision risk).
- **`gi.diagnose(network)`** — one-call health check that reports
  stale `network.paths`, duplicate frequencies, missing
  impedance formulas, untyped branches and the inverse-rho-f
  bus mismatch. Useful before each `run_fault` in long
  notebooks.
- **`scripts/release.py` cross-port** — copy
  `groundmeas/scripts/_changelog.py` (the stdlib-only
  `[Unreleased]` mover) into `groundinsight/scripts/` and wire
  the `release` command to call it. Closes the Pass-2 roadmap
  item that has now slipped four passes.
- **`gi.connectors.dashboard_state`** — explicit serialisable
  state object so a future Streamlit dashboard can resume a
  notebook session. Mirrors the `groundmeas` dashboard
  feedback loop and keeps the door open for a planned shared
  dashboard in the cross-repo toolchain.

> Additional roadmap candidates from the **sixth 2026-05-14 review
> pass**:

- **`gi.set_pathfinder_cache_size(n)`** — user-tunable LRU cap
  on the module-level pathfinder caches. Default 256, settable
  to `None` for the historic unbounded behaviour. Replaces the
  current "rely on `clear_pathfinder_cache()`" guidance with a
  predictable memory budget. Tied to the sixth-pass
  unbounded-cache Fixed-backlog entry.
- **`NetworkFrequencyOrderWarning(UserWarning)`** plus a
  symmetric `Network.with_frequencies(*freqs,
  preserve_order=True)` constructor — mirror the Pass-5
  `groundfield.solver.engine.EngineFrequencyOrderWarning`
  pattern for the network-side validator. Closes the
  sixth-pass FFT-bin-order finding.
- **`gi.docs.assert_api_pages_exist`** — small test-only helper
  that walks `__all__` and asserts every public symbol has at
  least one mkdocstrings `:::` directive somewhere under
  `docs/api/`. Companion to the proposed `mkdocs build
  --strict` test. Mirrors the planned
  `gf.docs.assert_api_pages_exist` in `groundfield`.
- **`gi.show_versions()`** — return a structured dict of the
  installed runtime versions for `groundinsight`, `numpy`,
  `scipy`, `sympy`, `pydantic`, `polars`, `sqlalchemy` and
  optionally `pandapower`. Mirrors the planned
  `gf.show_versions` in `groundfield`; would be reused by the
  cross-package `gm-cli doctor` proposal in `groundmeas`. The
  three packages together form one toolchain, so the
  diagnostic helper should be cross-repo-consistent.
- **`gi.audit_apply(report_path)`** — read a Markdown audit
  bullet list (`audit-report-changelogs-YYYY-MM-DD.md`) and
  insert the bullets verbatim into the appropriate
  `[Unreleased]` Backlog sub-section. Six passes in a row of
  hand-merging suggests this should be a one-line command.

> Additional roadmap candidates from the **seventh 2026-05-18
> review pass**:

- **Cut `0.5.0` release.** Pass 7 reiterates: the
  `[Unreleased]` block has now spanned four audit passes worth
  of *implemented* code (transient state-space solver,
  capacitance support, voltage-source state-space path,
  pandapower importer hardening, `Network.invalidate_paths`,
  frequencies validator, `keep_results=`); cutting `0.5.0`
  unsticks the cross-repo bridge work in
  `groundfield/io/groundinsight.py`. Higher priority than any
  further bug-finding pass.
- **ADR for cross-repo `show_versions` convention.** Tie to
  the proposed `ADR-0013` in `groundfield`; the return shape
  must be identical across `gi.show_versions()`,
  `gf.show_versions()` and the planned `gm-cli doctor` so a
  single dashboard / CI pipeline can consume all three. Pin
  the keys (``package``, ``python``, ``numpy``, ``scipy``,
  ``sympy``, ``pydantic``, ``polars``, ``sqlalchemy``,
  optionally ``pandapower``) and the ``_meta`` block before
  any of the three packages implements the helper.
- **`gi.cross_repo` namespace** — convenience re-exports of
  the ADR-bound cross-repo conventions
  (`gi.cross_repo.show_versions`, `gi.cross_repo.audit_apply`,
  `gi.cross_repo.docs_assert`). Stops the cross-cutting
  helpers from polluting the top-level namespace and keeps
  the discoverability hint in `gi.cross_repo.*` for future
  AP1 reviewers.
- **`docs/cross-repo.md`** — single page that lists the
  three-package toolchain (`groundfield` PDE/field,
  `groundinsight` reduced network, `groundmeas` measurement
  store) and their data-flow contracts (`rho-f` fit handoff,
  `multilayer_soil_model` bridge, planned
  `Measurement → ImpedanceTable` exporter). Currently spread
  across three CLAUDE.md files; a docs-site page is the
  forcing function for the contract pinning.

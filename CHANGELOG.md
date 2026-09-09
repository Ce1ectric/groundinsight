# Changelog

All notable changes to `groundinsight` are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and the project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

Change categories follow the Keep-a-Changelog vocabulary:

- **Added** — new features and public API.
- **Changed** — behaviour changes to existing public API.
- **Deprecated** — features that still work but will be removed.
- **Removed** — features taken out of the public API.
- **Fixed** — bug fixes.
- **Security** — vulnerability fixes.
- **Docs** — documentation-only changes.
- **Internal** — refactors, tests, packaging, CI; no observable behaviour change.

The backlog of ideas that are not yet scheduled is kept at the end of this
file under **Roadmap**. During regular work, add your entry under the
matching category in `[Unreleased]`; the release script
(`scripts/release.py`) moves the whole `[Unreleased]` block into a new
version section when a release is cut.

---

## [Unreleased]

### Security

- **The impedance formula guard is a whitelist and normalises Unicode
  (`utils/validations.py`).** It compared raw token strings against an ASCII
  denylist, but CPython NFKC-normalises identifiers when it compiles the
  source `sympy.lambdify` generates: a fullwidth `ｅval` passed the guard and
  reached the builtin `eval` through `create_bus`, i.e. through every JSON and
  database load. Names must now be ASCII and NFKC-stable, only a fixed set of
  SymPy functions may be *called* (free symbols stay unrestricted — a user
  may name a parameter whatever they like, and a bare name can never become
  a call), only `+ - * / ** ( ) ,` are accepted as operators, and a power
  whose exponent is itself a power or a literal above 1000 is refused (the
  `10**10**9` denial of service). Formulas longer than 4096 characters are
  rejected up front. Nothing in the repository's docs, tests or notebooks is
  affected; a formula calling a function outside the list (`factorial`,
  `besselj`, …) now fails with a message that names the allowed set.
- **`NetworkDB.frequencies` is a JSON column, no longer a `PickleType`.**
  Loading a database ran `pickle.loads` on the stored blob before any
  Pydantic validator saw a field, so a `.db` file from another hand was
  code execution — the very thing the formula guard exists to prevent. Files
  written before this release are converted by `gi.migrate_database(...)`
  (`start_dbsession` does it automatically) through a restrictive unpickler
  that resolves nothing but `builtins.list/float/int` and aborts before the
  first write when the blob names anything else.

### Fixed

- **The inverse searches no longer leave a stale nodal system behind
  (`analysis/inverse_rho.py`, `analysis/inverse_rho_f.py`).** Both `finally`
  blocks restored `rho`, impedances, results, paths and the active fault but
  not `network.electrical_network`, so the network was left carrying the `Y`
  of the last bisection step next to the restored `rho`. `bus_response` read
  a **negative** driving-point impedance out of that pair, without error or
  warning. The pre-search object is put back (or `None`), and
  `ElectricalNetwork` carries a fingerprint of the model values it was
  assembled from so `bus_response` refuses a stale system by name.
- **`BusResponse.evaluate` reports the current `Z_G` definition.** It computed
  `Z_G = u_b/(r*I_F)` — the definition 0.6.0 replaced — with the *varied*
  bus's voltage, a factor of 39 against `run_fault`. It is now
  `Z_E = U_E/I_E` of the bonded group, matching `run_fault` to 1e-9.
- **`BusResponse.evaluate` evaluates `r_epr` per electrode.** It was carried
  through as an invariant, which it is only at the fault bus; away from it the
  factor moves with the electrode by 18 %. A third right-hand side of the same
  factorisation (the mutual-free injection) makes it exact for every
  electrode.
- **`analyze_cuts` decomposes the system that was solved.** It assembled its
  own nodal matrix, which disagreed with the solver's in two places: a 0 Hz
  short, which the solver substitutes and the decomposition read as an open
  circuit (`Z_dp` 10 Ω against 2.5 Ω), and the Thévenin loop closure `Y_src`,
  which exists only in the solver's matrix and carried up to 23 % of the fault
  current out of the fault bus. The loop closure is reported as its own
  direction `"source_loop"`; `kcl_residual` falls from 22.5 % to 1e-16.
- **`find_max_rho_scaling` no longer assumes monotonicity.** Where only part
  of the impedance formula carries `rho`, `EPR(c)` falls before it rises, and
  the bisection reported "no scaling factor satisfies the constraint" for
  limits a whole band of factors satisfies. A logarithmic pre-scan
  (`monotonicity_grid`, default 9) measures the shape first.
- **`kappa` and `n` are validated in the thermal checks.** Both docstrings
  promised `(1, 2]` and `(0, 1]`; neither was enforced, and every out-of-range
  value failed in the unsafe direction.
- **The 0 Hz bin is no longer counted twice in `I_th`.** It entered both the
  RMS and `m`; on the audit case `[50 Hz]` gave 1028 A and `[0, 50 Hz]` with
  700 A of direct current gave 1255 A.
- **`classify` leaves a NaN unassessed** instead of putting it in the highest
  band, where a failed sweep point was counted among the violations.
- **`analyze_cuts` explains an incomplete result** instead of raising a bare
  `KeyError`, and `CutAnalysis.to_polars()` pins its schema so an unsolved
  analysis stacks with a solved one.
- **`sweep([])` and a non-positive network conductance in
  `worst_case_electrode`** raise `ValueError` instead of a polars error and a
  negative "worst case".

- **`run_fault` no longer enumerates every source-to-fault path in the
  default mode.** `phase_current_mode="auto"` only needs to know which
  sources reach the fault, yet `define_paths` ran the exhaustive DFS on every
  call; a 6×6 grid of 36 buses spent 55–68 s on 1 262 816 paths while the
  solve itself took milliseconds. `Network.sources_reaching(fault_name)`
  answers the question with a breadth-first search, and the enumeration runs
  only for `phase_current_mode="paths"`, the transient state-space solver and
  an explicit `gi.create_paths(network)`. Results are bit-identical on every
  network where the old path was affordable; the 6×6 grid now solves in
  0.09 s. `PathFinder` additionally caps the enumeration at
  `set_pathfinder_max_paths()` (default 100 000) with an error that names
  the bus pair and points at `"auto"`. `analysis/shortcircuit._sources_feeding`
  keeps its old behaviour through the same reachability helper.
- **A frequency an element does not cover is an error, not an open circuit.**
  `_construct_Y_matrices` skipped any bus or branch whose impedance dict
  lacked the frequency being solved, silently — extending
  `network.frequencies` in place and recomputing only the buses left the
  branches out of the 250 Hz bin and reported an EPR of 1000 V where 4.95 V
  was right. `ElectricalNetwork` now checks that every active element
  covers `network.frequencies` before assembling anything.
- **`value_current` with a source on the fault bus.** `I_fault` was read as
  the net injection at the fault bus, which with a source there is
  `I_src,F − ΣI_src` rather than the fault current; the current-based
  reduction factor came out as 0.005 instead of 0.5. It is now
  `Σ scaling·I_src` over the sources that reach the fault.
- **`run_sweep` leaves the network as it found it, in every case.** A point
  naming an unknown bus raised *after* the earlier buses had been
  overwritten, and because the return value of `_apply` was never bound, the
  `finally` block skipped the restore — with `on_error="record"` every later
  point was solved on the corrupted model. Names are validated before the
  first mutation, the snapshot is taken once per bus (`R`, `L`, `C` included,
  and a point overriding both `bus_impedance` and `bus_rho` on one bus can no
  longer save the overwritten table as the original), restore runs in
  reverse order, and the previously active fault keeps its cached result.
- **The FFT transient solver injects only sources that reach the fault**, as
  the stationary solver does; a source on a galvanically separate bus used to
  add 1000 V at that bus and shift every other trace through the fault-bus
  term.
- **The state-space solver no longer reads its operating frequency from
  `frequencies[0]`.** With 0 Hz first in the list the source inductance
  became `L_src = 0` (a factor 680 in the first time step). `solve()` takes
  `f_eval`, defaulting to the smallest frequency above 0 Hz, and refuses a
  reactive `Z_src` at `ω = 0`.
- **Negative `R_self`, `L_self`, `C_self`, bus `L` and `C` are rejected by the
  state-space solver** instead of being silently treated as absent, which
  made a branch with `R_self = −0.5` lossless.
- **`ComplexNumber == None` is `False`**, not a `TypeError`; the class is
  hashable; NumPy scalars (`np.int64`, `np.complex64`, …) work on either side
  of an operator; `repr` is a valid constructor call again.
- **`Network.frequencies` and every other model field validate on
  assignment** (`validate_assignment=True` on `Bus`, `Branch`, `Fault`,
  `Source`, `Network`). `apply_shortcircuit_characteristics` could write
  `kappa = 42` and a shortened `t_k_s` straight past the validators that
  promised to reject them, inflating `i_p` by 21 and `I_adm` by 7.7 — the
  unsafe direction — and left the network unserialisable. A rejected
  assignment is rolled back, because Pydantic writes the value before the
  model-level validators run.
- **`Fault.scalings` values are validated** (finite, non-negative) — `nan`
  and `inf` went through and broke the JSON round-trip of the whole network.
- **The `add_columns` database migration works.** The DDL it rendered for
  the eight NOT-NULL columns with Python-side defaults (`position`, `active`,
  `source_type`) had no `DEFAULT` clause, SQLite refused it, and because
  `start_dbsession` migrates before it binds, the file was unopenable. The
  column DDL is rendered with the declared default.
- **`save_network` no longer rewrites a shared type silently.** Saving
  network B replaced the formula of a `BusType` network A also uses; A was
  then internally inconsistent (stored 1 Ω, formula 999 Ω) and every
  analysis that re-evaluates the formula — `find_max_rho_scaling`,
  `run_sweep` — switched to the wrong value. A changed formula on a type
  other networks reference is refused unless `overwrite_types=True`.
- **`res_buses` handles a missing `ia_freq` entry** (`None` instead of an
  `AttributeError`), and the plot helpers release their figure when the
  drawing step raises (a bad `yscale` used to leak one figure per call).
- **The pandapower importer honours closed bus–bus switches** when
  `ImportDefaults.switch_branch_type` is set, and otherwise warns and lists
  them in the preview together with transformers and impedances, which were
  dropped without a trace before — a station bonded to its neighbour by a
  switch came out isolated at 0 V. Bus voltage levels are matched with
  `math.isclose` instead of exact equality, so rounding noise from a
  CSV/DGS import no longer empties the whole import.
- **`scripts/release.py` runs git in the repository root**, whatever the
  current directory, and refuses to touch the three version files when the
  target tag already exists.
- **Three doctests had gone stale** (`create_fault`, `create_source`, the
  `simulation/outage.py` module example); the suite now runs the doctests
  (`--doctest-modules`) so they cannot rot again.

### Changed

- **Two reference cases were replaced by ones that can fail.**
  `en50522_chain` checked `U_E = 3*I_0 * Z_E * r` with all three factors read
  out of one result — an identity the implementation closes by construction,
  so the case agreed to 1e-16 whatever the model did, and tripling every
  electrode current made the deviation *smaller*. It now re-derives `I_E` from
  an independently assembled nodal system (quantity and tolerance changed).
  `ladder_potential_decay` built its matrix from local constants rather than
  from the model and is now assembled from `bus.impedance` /
  `branch.self_impedance`.
- **`summarize` gains an `n_nan` column** and `check_conductor_limits` /
  `check_node_limits` gain `I_dc_A` and `I_ac_rms_A`. Code that asserts an
  exact column set has to be updated.
- **`BusResponse.admittance` lost its `freq` argument**, which it never read.

- **`res_buses()` / `res_branches()` report `frequency_Hz` as `Float64`**
  with the RMS row marked by a new `is_rms` column, instead of a string
  column mixing `"50"` and `"RMS"` that could not be compared with a number
  and disagreed with `res_all_impedances()`. Filter with
  `pl.col("is_rms")` and `pl.col("frequency_Hz") == 50.0`; both frames also
  gain `I_inj_A` / `I_inj_degree`, the earthing-conductor current the
  `ResultBus` docstring warns not to confuse with the electrode current.
  All three `res_*` frames carry a pinned schema, so an empty result keeps
  its columns.
- **`net.paths` is empty after a default `run_fault`** (see *Fixed*); paths
  that were persisted with the network are adopted on load and survive the
  next solve as long as the topology still matches.
- **The FFT transient solver warns** (`TransientModelWarning`, exported)
  when the network carries mutual coupling it does not model, when the
  active fault has `scalings` other than 1 (both solvers ignore them), and
  when the window ends while the response is still ringing — the periodic
  DFT then wraps the tail into the start of the trace (measured −37 % on the
  peak with a 0.4 s window). `solve(..., zero_pad=True)` computes on a window
  twice as long and reports the first half. `|M_mutual| > L_self` warns as the
  usual mH/H slip. Both solvers share one time grid; the state-space series
  is one sample shorter than before (`[0, t_end)`), a real branch named
  `__vsrc__…` is refused.
- **Non-finite floats are written as the strings `"inf"`, `"-inf"`, `"nan"`**
  in JSON and in the SQLite JSON columns, because the open-end sentinel of
  the `"nan"` formula used to be emitted as the literal `Infinity`, which is
  not RFC 8259 JSON (SQLite `json_valid()` = 0, `JSON.parse` throws). Old
  files still load.
- **`gi.Path` is the domain model**, not `pathlib.Path`; `Network`, `Bus`,
  `BusType`, `Branch`, `BranchType`, `Fault`, `Source`, `Result`,
  `ResultBus`, `ResultBranch`, `ComplexNumber` are exported and in
  `__all__`; the ORM classes `BusTypeDB` / `BranchTypeDB` / `NetworkDB` are
  no longer reachable from the top-level package.
- **`run_fault(auto_parallel_coefficients=…)` emits a `DeprecationWarning`**
  in addition to the log line, which without a logging setup nobody saw.
- **`scripts/` is not packaged and the `release` /
  `generate-third-party-licenses` entry points are gone**: they installed a
  top-level module `scripts` and an executable called `release` into every
  user's environment. Run `poetry run python scripts/release.py …` instead.
- **`python = "^3.13"`** (was `^3.14`); the suite passes unchanged on 3.13 and
  CI runs both.
- **`ImportDefaults` validates `rho`, `frequencies` and `switch_length_km`**
  on construction, so an empty frequency list fails at the defaults rather
  than deep in the importer.

### Added

- `run_outage_study(..., on_error="raise"|"record")` and
  `OutageStudyResult.failures`, matching `run_sweep`.
- `ImportDefaults.switch_branch_type`, `switch_length_km` and
  `derive_phase_impedance` (a `phase_impedance_formula` from
  `r_ohm_per_km + I*x_ohm_per_km`, deduplicated by formula) for the
  pandapower importer; `preview_pandapower_import(..., defaults=...)`.
- `utils.impedance_calculator.compute_impedance_array` and
  `check_passive_impedance_array` — the array path the transient solver
  uses; same results, same messages, without a `ComplexNumber` per bin.
- `Network.sources_reaching(fault_name)`, `PathFinder.reaches`,
  `set_/get_pathfinder_max_paths`, `ElectricalNetwork.state_fingerprint()`.
- A shared `simple_network` fixture in `tests/conftest.py`.
- CI: the release workflow runs the test suite before it builds; pull
  requests run `mkdocs build --strict` and execute `docs/examples/*.ipynb`.

### Docs

- `find_max_rho_f_scaling` states the monotonicity assumption and that it is
  proven at a single bus and unproven in the coupled multi-bus case.
- `resolve_fault_sc_characteristics` states exactly what it averages —
  `kappa` over the sources carrying 60909 data, `i_k_a` over all of them — and
  its warning names the share of the current the data does not cover.
- `kappa_from_r_to_x` documents its actual range, `[1.02, 2]`.
- `CutAnalysis.identity_residual` says what it can and cannot show.

- `Source.values` and `ElectricalNetwork._calculate_rms` state the
  convention: phasors are RMS amplitudes, the total RMS over all frequency
  components is `√Σ|X(f)|²` with the 0 Hz term entering as `|X_dc|`.
- `compute_grounding_impedance`, `CLAUDE.md`, `docs/concepts.md` and
  `docs/api/electrical_network.md` say `Z_G = Z_E = U_E / I_E` and name the
  pre-0.6.0 expression as such.
- `docs/transient.md` no longer claims the FFT solver injects mutual
  currents; the limitations list the Nyquist bin, the non-causal kernel of a
  frequency-independent reactance and the sign convention of the transient
  EPR. `docs/api/pathfinder.md` and `docs/concepts.md` state the real cost of
  the path enumeration. The transient example notebook runs again
  (`mv_ring_transient.ipynb`, cell 10), the README no longer recommends the
  deprecated `auto_parallel_coefficients`, and the notebooks import the
  model classes from `gi.*`.

### Internal

- Performance, core: `solve_network` keeps the per-frequency `splu`
  factorisation and `compute_reduction_factors` reuses it instead of a dense
  `np.linalg.solve` of the same matrix; `is_short_circuit`,
  `_is_open_circuit` and `check_passive_impedance` are array/`math` based
  (12–31× on the checks); `validate_impedance_formula_value` is memoised
  behind the guard (`model_validate_json` of a 120-bus network 246 ms → 5 ms).
  `run_fault` on a 200-bus chain × 50 frequencies: 1.10 s → 0.71 s.
- Performance, transient: the FFT path evaluates impedances as arrays,
  assembles `Y` as a stacked `(K, n, n)` array solved in blocks of 512 bins
  and builds `ResultTransient.to_polars()` column-wise — 9.3 s → 0.3 s on
  25 buses × 20 000 samples, results identical to 1e-13.
- `tests/test_audit_pass18_*.py` — twenty files, 394 tests, one per
  finding of the 2026-09-08 review; each fails without its change.

- `analysis/_guards.py` holds the shared completeness guard, previously
  private to `thermal.py`. Its lookup set is built once; it was quadratic
  (398 ms at n = 4000).
- Performance: `analyze_cuts` reuses the assembled `Y` (26.2 ms → 9.5 ms at
  150 buses × 9 f), `bus_response` solves three right-hand sides against one
  factorisation instead of two against two, `BusResponse.extremes()` is linear
  in the frequency count (90.7 ms → 22.9 ms at 9 f), and
  `find_max_rho_f_scaling` drops its closing re-evaluation
  (`(iter + 3)·N` → `(iter + 2)·N` `run_fault` calls).

- **`scripts/generate_graphify_report.py` removed, replaced by
  `scripts/refresh_graphify.sh`.** The Python script existed only because
  `graphify` 0.7.x stopped emitting `graphify-out/GRAPH_REPORT.md` while the
  `CLAUDE.md` hook still pointed at it. Verified against `graphifyy` 0.9.53:
  the report is written again — but by `cluster-only`, never by `extract`, in
  **either** variant. `extract` writes `graph.json` and leaves the communities
  as `Community N` placeholders; skipping the second pass leaves a stale
  report sitting next to a fresh graph without anything failing, which is the
  same silent drift the removed workaround was papering over. The shell script
  runs both passes in order, pins the DeepSeek backend to `deepseek-v4-flash` with an explicit
  `--model` rather than relying on the backend default, prompts for
  `DEEPSEEK_API_KEY` rather than storing it, and falls back to a local
  AST-only run when none is given. It also checks up front that graphify's
  environment has the `openai` package — DeepSeek has no extra of its own and
  is reached through the OpenAI-compatible client, so without it the AST pass
  completes and *then* every semantic chunk fails. Development tooling only —
  `graphify-out/` is gitignored and the package does not depend on any of it.
- **Dependabot** for GitHub Actions and Python dependencies
  (`.github/dependabot.yml`), monthly, grouped into one pull request per
  ecosystem. Added because the Node 20 runner deprecation reached the release
  workflow unnoticed: `v0.5.0` built with actions GitHub was already forcing
  onto Node 24, and the warning only surfaced in a release log. Grouping is
  deliberate — a single-maintainer repository reads a dozen separate bump PRs
  as noise, which defeats the purpose.
- **Audit pass 18 — full review of the 0.6.0 code base, documentation and
  project setup (2026-09-08).** The review itself changed nothing; the
  patch series that followed it is what the *Security*, *Fixed*,
  *Changed*, *Added* and *Docs* entries above record. The complete
  record — reproduction scripts, measurements, the proposed order of work
  and the GUI concept it was commissioned with — is kept with the
  maintainer's working notes outside this repository. The
  suite was green throughout (992 passed, `mkdocs build --strict` clean),
  and every finding listed here was reproduced twice — once by the reviewer
  who raised it and once independently — so the list is short on
  speculation. What the pass confirmed as *correct* is worth stating first:
  the coupling model and both reduction factors, the Thévenin ⇄ Norton
  equivalence in both solvers, the Sherman–Morrison/Möbius form
  (1.4e-14 at every bus), the ring current split, `iec60909_m` against
  numerical integration (1e-15), `iec60949_k`, and all four closed forms
  in `analysis/reference.py` re-derived by hand. The findings sit one layer
  above the core and at the edges.
  - *Two ways to execute code by loading foreign data.* The formula guard
    in `utils/validations.py` compares raw token strings against an ASCII
    denylist, but CPython NFKC-normalises identifiers when it compiles the
    source that `sympy.lambdify` generates — a fullwidth `ｅval` passes
    the guard and reaches the builtin `eval` (verified through
    `create_bus`). And `NetworkDB.frequencies` is a `PickleType`: a
    `.db` file from another hand runs `pickle.loads` before Pydantic sees
    a single field. Both contradict what `assert_safe_formula` promises.
  - *`run_fault` enumerates every simple source→fault path in every
    mode.* The default `phase_current_mode="auto"` only needs
    reachability, yet `define_paths` runs the full DFS: a 6×6 grid of 36
    buses takes 54–68 s for 1.26 million paths while the solve itself is
    milliseconds. `docs/concepts.md` and `docs/api/pathfinder.md` state
    the opposite scaling.
  - *The transient FFT solver ignores mutual coupling* (17 % on the
    verification case, no warning) while `docs/transient.md` says it
    injects mutual currents like the stationary solver; it also injects
    sources that have no path to the fault, and the state-space solver
    reads its evaluation frequency from `frequencies[0]`, so a leading
    0 Hz silently drops the source inductance.
  - *`run_sweep` does not restore the network after a point fails in
    `_apply`* (the return value is never bound, so `finally` skips the
    restore) and the following points are solved on the corrupted model;
    three smaller restore gaps (`R/L/C`, impedance-plus-rho on one bus,
    the previous fault's cached result) sit next to it.
  - *The 0.6.0 analysis layer against its own core:* `bus_response`
    passes `r_epr` through as invariant although it is only invariant at
    the fault bus, and computes `Z_G_Ohm` with the `u/(r·I)` expression
    that 0.6.0 replaced; the inverse-rho searches leave a stale
    `network.electrical_network` behind from which `bus_response` then
    reads a *negative* driving-point impedance; the bisection assumes a
    monotonicity that a rho-only sweep with a fixed reactive part does
    not have; `analyze_cuts` assembles its own `Y` and therefore treats a
    0 Hz short circuit as an open end and omits the Thévenin loop
    admittance, while its `identity_residual` is structurally zero and
    cannot show either.
  - *Persistence and models:* the `add_columns` migration cannot add the
    eight NOT-NULL columns with Python-side defaults it exists for and
    leaves the file unopenable; `save_network` merges bus/branch types
    globally and leaves the other network inconsistent; no model sets
    `validate_assignment`, so `apply_shortcircuit_characteristics` can
    write `kappa = 42` past the validator that promises to reject it.
  - *Documentation:* `docs/examples/mv_ring_transient.ipynb` does not run
    (`KeyError: 'F1'`, cell 10) while its stored output shows a result;
    the README still recommends the deprecated
    `auto_parallel_coefficients=True`; `concepts.md` claims an RMS row in
    `res_all_impedances` that does not exist; the migration API is absent
    from the site; and the four chapters an engineer without Python
    routine needs most — assumptions and limits of the stationary model,
    where the impedance formulas come from, how to read the result
    columns and carry a verification through to an EN 50522 limit, and a
    glossary — do not exist yet.
  - *Runtime, without touching the architecture:* reuse the `splu`
    factorisation in `compute_reduction_factors` instead of a dense
    re-factorisation (13× on the linear-algebra share), vectorise the
    scalar passivity/short-circuit checks (three hot loops at once), an
    `lru_cache` on `validate_impedance_formula_value` (8.4× on
    `model_validate_json`), and a batched `(K, n, n)` assembly in the FFT
    solver (≈10× end to end).
  The open items are listed under **Roadmap → From the 2026-09-08
  review**. Fixes follow the house rule: a test that is red without the
  change, in `tests/test_audit_pass18_*.py`, with the measurement protocol
  in `docs/audit-log.md`.

---

## [0.6.0] — 2026-09-01

### Added

- **Closed-form reference cases (`gi.run_reference_cases`).** Six
  configurations whose answer is known in closed form, run through the ordinary
  public API and compared, with the derivation in each case's docstring rather
  than a quoted equation number. Measured deviations: the textbook
  `r = |1 - Z_m/Z_s|` under its own condition of negligible station electrodes
  (2e-6), the same line with finite earthing (5e-16), the EN 50522 chain
  (1e-16), the ladder-network input impedance
  `Z_in = -Z'/2 + sqrt(Z'^2/4 + Z_e*Z')` over 80 sections (3e-9), the potential
  decay `u_n/u_0 = e^(-n*gamma)` with `gamma = arccosh(1 + Z'/(2*Z_e))` (3e-7),
  and the parallel decomposition at a station (1e-15). Each case states the
  boundary conditions its closed form needs, because a deviation more often
  means a condition was not met than that the model is wrong. Also run as tests,
  so a change that quietly breaks agreement cannot reach a release.

- **Characterising a location without knowing the electrode there
  (`gi.bus_response`).** Adding an electrode is a rank-one change to the nodal
  matrix, so by Sherman-Morrison every nodal voltage is a Möbius function of its
  admittance. Two solves with the electrode removed therefore determine the
  response for **every** electrode — measured against genuine solves at `Z_B`
  from 0.05 Ω to 500 Ω and at `4+9j` Ω, the closed form agrees to `3e-15`
  relative, on every bus and for the current-based reduction factor as well.
  `BusResponse.extremes()` gives the bracket: `open` (no electrode), `ideal`
  (`Z_B = 0`) and `worst_passive`. Both endpoints are exact limits rather than
  numerical stand-ins — the ideal electrode in particular, which the solver
  rejects outright because a zero impedance is not invertible. `.evaluate(z)`
  and `.sweep([...])` cost no solve at all.
  - `BusResponse.z_network` is the site-independent number the analysis is built
    around: the driving-point impedance of everything except the local
    electrode. Verified identical whether the response was built from a network
    carrying a 0.5 Ω electrode or a 200 Ω one.
  - `Z_dp = 1/(Y_B + 1/Z_net)` exactly, so it runs monotonically from `Z_net` to
    zero. The largest magnitude over all *passive* electrodes is not at the open
    end but at the reactive resonance `Y_B = -j·Im(1/Z_net)`, worth 0.1 % more
    in the verification network — small, but reported rather than assumed.
  - The closed form **derives** the invariance of the EPR-based reduction factor
    that the electrode sweep above measured: `u_b(Y_B) = u_0b/(1 + Y_B·Z_net)`
    holds with and without mutual coupling alike, so the quotient cancels.
    Algebra, not an accident.
  - The guard that the location is characterisable at all is structural, not a
    tolerance: at least one *other* bus must carry a finite grounding impedance
    once the electrode is removed. Leaving it to the linear solver is not enough
    — on the exactly singular case numpy returns a finite but meaningless
    `-2.25e15` instead of raising, the same inconsistency the DC work found in
    scipy's `splu`.

- **Splitting the network at the fault location (`gi.Cut`, `gi.analyze_cuts`).**
  A cut is a named set of branches, all incident to the fault bus; the analysis
  reports what each direction contributes.

      parallel impedance left --- fault location --- parallel impedance right

  `Z_side` comes from **source-free current division**: one ampere is injected
  at the fault bus with all sources and mutual injections removed, and the share
  leaving through each cut gives `Z_side = u_fault / i_cut`. The decomposition
  closes by construction — `1/Z_local + Σ 1/Z_side` reproduces the
  driving-point impedance of the whole network, measured residual `1.2e-15` on a
  five-bus chain and on the same network with the ring closed. Isolating each
  side into its own sub-network would have been equivalent for a radial network
  and **wrong in a ring**, where removing one branch separates nothing and the
  far side comes out empty; that formulation was discarded before it shipped.
  From a solved fault the analysis additionally reports `i_shield` and
  `current_share` per direction (KCL at the fault bus closes to `2.4e-16`), and
  where the directions are disjoint also `i_earth`, `i_total` and the side
  reduction factor. `sides_are_disjoint` says which case you are in; a passive
  spur gets `r = None` rather than a division by zero. Results as a typed
  `CutAnalysis` and as a long-format frame via `.to_polars()`.

- **A second reduction factor, on a current basis
  (`ResultReductionFactor.value_current`).** The existing EPR-based `value` is
  structurally blind to the impedance *at* the fault bus: both solves use the
  same `Y`, so changing `Y_ff` is a rank-1 update that cancels in the quotient.
  Measured: sweeping the fault-bus electrode over four decades leaves it at
  `0.500000` while `Z_G` moves by two orders of magnitude and the potential rise
  by a factor of fifty. A rho-f sensitivity study plotted against it would be a
  horizontal line. `value_current` is the share of the fault current that
  returned through earth rather than through the shields and does respond —
  `0.0414 → 0.00053` over the same sweep. **Both are kept**: `value` so earlier
  results stay reproducible and so the closed form `r = |1 - Z_m/Z_s|` keeps its
  meaning, `value_current` for sensitivity work. `res_all_impedances()` reports
  both.

- **Parameter sweeps (`gi.run_sweep`, `gi.SweepPoint`, `gi.rho_f_points`).**
  Solve one fault once per parameter combination and stack the results into
  long-format frames that carry the parameters as ordinary columns
  (`.buses()`, `.branches()`, `.impedances()`, `.cuts()`). A point can override
  bus impedance tables, soil resistivity, the fault location or the harmonic
  scalings. `rho_f_points` builds the points from a catalogue of five-parameter
  rho-f vectors — the form `groundfield` exports — and rejects a vector that
  gives `Re(Z) <= 0` where it is built rather than three layers down in the
  solver, because an unconstrained least-squares fit can land there. Overrides
  are undone in a `finally` block, so a failing point leaves neither the network
  nor the remaining points contaminated; failures are recorded per point and the
  sweep carries on (`on_error="raise"` for the strict variant).

- **Statistics and classification (`gi.summarize`, `gi.classify`).** Count,
  spread, named quantiles and extremes for any numeric column, grouped or not;
  and a class column that bins a quantity into user-supplied bands, closed on
  the right so a value exactly on a limit stays in the lower band. There is
  deliberately **no built-in table of admissible values** — touch-voltage limits
  depend on the clearing time, the standard edition and the assumed additional
  resistances, and a constant baked in here would be carried into a result
  without ever being checked. The edges come from the caller, who can cite them.
  `summarize` names the `frequency_Hz`-is-a-string trap in its error message
  rather than just refusing.

- **`BranchType.phase_impedance_formula`** (optional, symbols `f`, `rho`,
  `l`) and the evaluated `Branch.phase_impedance`, mirrored in SQLite and
  JSON. It describes the *phase* conductor -- the faulted conductor whose
  current induces the longitudinal EMF on the shield -- and is what the
  automatic phase-current distribution solves on. It never enters the nodal
  admittance matrix and is therefore not passivity-checked. Where it is
  absent **and** the phase network around the fault carries a cycle, the
  solve falls back to `1/Z_self` (with a grounding conductor) or `1/length`
  (without) and warns once per solve, naming the branches: those two
  quantities are not comparable, so the split between routes of different
  construction is a heuristic rather than a physical result. On a ring whose
  two legs differ by a factor of ten in phase impedance the declared formula
  divides the current 909.1 A / 90.9 A, as it should.

### Changed

- **Breaking: `ResultGroundingImpedance.value` is now `Z_E` in the EN 50522
  sense** — the earthing voltage of the bonded earthing system over the current
  it passes into the soil — instead of `u_EPR / (r_coupling * I_F)`. The old
  expression mixed the coupling ratio into an impedance: on the verification
  feeder it reported 0.219 Ω where the earthing system actually presents
  3.298 Ω, a factor of fifteen, the same one that separates the two reduction
  factors. With the new definition the norm's chain `U_E = 3*I_0 * Z_E * r`
  closes on the reported value itself. Every ingredient of the old expression is
  still in the result (`ResultBus.uepr`, `ResultReductionFactor.value`, the
  source currents), so an earlier number can be reconstructed where a
  comparison needs it.

- `run_outage_study` forwards `phase_current_mode` and defaults it to
  `"auto"` as well -- an outage study is precisely where a ring turns into a
  chain and back, so the modelling assumption must not change underneath it.

- **Breaking (behaviour, meshed networks only):** the default phase-current
  determination moved from the path-based scheme to the phase-network solve.
  A meshed network solved with the previous default returns different -- and
  now non-degenerate -- numbers. Radial networks are unchanged.
  `Branch.parallel_coefficient` is ignored in `"auto"` mode; it still applies
  in `"paths"`.

### Deprecated

- `run_fault(auto_parallel_coefficients=...)` is deprecated in favour of
  `phase_current_mode`. It still wins when passed explicitly, so existing
  call sites keep their exact behaviour, and passing it logs that it is
  deprecated.

### Fixed

- **The current-based reduction factor summed the wrong thing.** `I_E` is the
  *total earth-return current*, and in a cable network with continuous shields
  the stations are bonded to one another, so the current spreads along the
  shields and leaks into the soil at **every** bonded station — the earthing
  current is distributed by construction. The first implementation summed the
  electrode currents of every bus *except* the faulted one, which by Kirchhoff
  (`Σ I_a = 0` over the whole network) is identically the electrode current of
  the faulted station alone. Measured on a six-station feeder at 50 Hz:

  | fault position | as shipped first | correct | factor |
  |---|---|---|---|
  | mid-feeder | 0.010947 | 0.032385 | **2.96×** |
  | one station from the infeed | 0.001955 | 0.009138 | **4.67×** |
  | far end | 0.026939 | 0.048327 | **1.79×** |

  The group is *not* found from the potential profile: the crossing falls
  between stations rather than on one, and with the fault near the infeed
  `|EPR|` runs 91.4, 19.6, 18.7, 18.1, 17.7, 17.5 V — no crossing anywhere. What
  is unambiguous per bus is the *direction* of its electrode current, so the
  split is made in the complex plane over the half-plane whose sum is largest:
  threshold-free, no angle nominated, and it reduces to the obvious answer when
  the two groups are cleanly opposed. New `groundinsight.utils.earth_current`
  with `split_earth_currents`; `ResultReductionFactor` gains `i_earth` (the
  ampere value) and `earth_buses` (the group) so the split is inspectable, and a
  `separation` diagnostic is logged when the phasors are spread in angle. Both
  groups carry the same sum with opposite signs, so `|I_E|` is independent of
  which is named which; the fault bus anchors the naming. `BusResponse` uses the
  same definition, and there the electrode under test contributes its own current
  rather than the one the response happened to be built with.

- **The EN 50522 chain `U_E = 3*I_0 * Z_E * r` now closes in the result.**
  `ResultReductionFactor` gains `u_earthing` (the earthing voltage of the bonded
  group, the mean potential weighted by each station's electrode current) and
  `z_earthing` (`Z_E = U_E / I_E`). All three routes to `r` — the current ratio
  `|I_E|/|3I_0|`, the voltage route `U_E/(Z_E*3I_0)`, and the reported
  `value_current` — agree to machine precision across three decades of
  electrode impedance, which settles that **`value_current` is the norm's
  reduction factor**. Rearranging the norm to `U_E(r)/U_E(r=1) = r` is correct
  and its reference case is `r = 1`, meaning the whole fault current through
  `Z_E`: 3298 V on the verification feeder, against the 214 V that removing only
  the mutual coupling gives while the shield stays in place as a metallic
  return. The ratio of those two reference voltages is exactly the ratio of the
  two factors and nothing else. Two further things are worth knowing and are
  pinned by tests: `Z_E` is *not* the electrodes in parallel — the shield
  sections between the bonded stations add to it, 3.30 Ω against 2.50 Ω — and
  where the group is not actually equipotential the lumped `U_E` is a weighted
  average of different voltages, which is reported at INFO.

- **The two reduction factors are now documented as the different quantities
  they are, and the gap between them is reported.** They are not two
  computations of one number: for a route with shield impedance `Z_s`, mutual
  impedance `Z_m` and station electrodes summing to `Z_E`,
  `r_coupling = (Z_s-Z_m)/Z_s` against `r_current = (Z_s-Z_m)/(Z_s+Z_E)`, so
  their ratio is the current divider `Z_s/(Z_s+Z_E)` and nothing else.
  `ResultReductionFactor.value` is the **ideally bonded limit** — the tabulated
  cable property `1 - Z_m/Z_s`, blind to the station earths by construction —
  while `value_current` is what this earthing system actually passes into the
  soil. Verified against the closed form to machine precision over five decades
  of electrode impedance: exact convergence as `Z_E → 0` (ratio 0.999980 at
  10 µΩ) and a factor of 45 apart at 10 Ω electrodes against a 0.45 Ω shield.
  The divider is logged at INFO where it drops below 0.5 — a wide gap is a
  property of the network rather than a defect, and with ordinary station
  electrodes it is the normal case.

- **`BusResponse.extremes()` was documented as a bracket for more than it
  brackets.** The three cases are extremes of the *local* quantities — the
  potential rise at the bus itself and its driving-point impedance. Transfer
  quantities are not bounded by them: at the verification feeder a purely
  capacitive `Z_B = -0.5569j` at the faulted station — passive, so inside the
  same class the `worst_passive` case is drawn from — reaches
  **`EPR_S0 = 1032.5 V` against the `ideal` case's 928.0 V** (+11 %) and
  `r_current = 0.3429` against 0.2799 (+23 %), confirmed by a genuine solve and
  not only by the closed form. The module docstring said the current-based
  factor and the potential rise "are reported as brackets"; it now says which
  columns the bracket covers and points at `.sweep([...])`, which costs no
  solve, for the worst case of a transfer quantity. Behaviour unchanged — the
  numbers `extremes()` returns were always correct for what they are.
- **`EarthCurrentSplit.__repr__` was dead code.** It sat inside
  `earthing_voltage` after that function's `return`, so the class never had it
  and inspecting a split printed the default `<... object at 0x...>`. Moved
  into the class.
- **`earth_buses` now reports the fault side.** The two groups carry the same
  sum with opposite signs, so `|I_E|` never depended on the choice, but the
  reported membership did. It is now the set of stations from the fault
  outwards, in every direction — a ring or a mesh has more than one — up to
  where the potential profile turns, which is how the earthing current is read
  off a network.

- **`compute_reduction_factors` raised a bare `KeyError` instead of skipping.**
  Its `except LinAlgError: continue` never wrote `u_vectors_no_mutual[freq]`,
  and the loop straight after it dereferenced that key. A frequency whose
  no-mutual solve fails is now reported as `None`.

- **A ring, a mesh or a second parallel cable no longer collapses the
  result.** The phase current per branch used to be handed out by walking
  the enumerated source-to-fault paths and giving *every* branch on any
  path the **full** source current, scaled by
  `Branch.parallel_coefficient`. Without a cycle that is exact -- there is
  one route and it carries everything. With a cycle the same current was
  handed out more than once instead of being divided, and the mutual
  injections it drives were multiplied with it. Measured on a symmetric
  ring (S-A-F / S-B-F, `Z_self = (0.1+0.2j)*l`, `Z_mutual = (0.05+0.1j)*l`,
  1000 A) at the default coefficient of `1.0`, the two contributions
  cancelled the source exactly and the network solved to
  **`EPR = 0 V`, `r = 0`, `Z_G = None`** across every bus -- announced with
  nothing louder than a log line. Two identical cables between the same two
  buses did the same thing. In a mesh the outcome additionally depended on
  the order the branches had been declared in, because "the first path a
  branch appears on fixes its direction": the same topology gave
  `Z_G = -0.00272 + 0.11121j` or `+0.00272 - 0.11121j` depending on
  insertion order.

- **`run_fault` gained `phase_current_mode`, defaulting to `"auto"`.** The
  new default solves a reduced phase-conductor network per source -- fault
  bus as reference, source current injected at the source bus -- and reads
  the branch phase currents off that solution. On the ring above it lands
  on `EPR = 55.6208 V`, `r = 0.5`, `Z_G = 0.0507 + 0.0990j`, which is
  bit-for-bit the result a user previously had to reach by setting
  `parallel_coefficient = 0.5` on every branch by hand. **Radial networks
  are unaffected**: both modes agree to 1e-9 there, so existing studies keep
  their numbers. `phase_current_mode="paths"` restores the old behaviour
  verbatim, including the collapse, so pre-0.6 studies stay reproducible.

- **An island no longer discards a whole source contribution in silence.**
  The phase-network solve pinned the fault bus over the *whole* bus index
  range, which leaves any other island without a reference node and makes
  the reduced matrix singular; the resulting `LinAlgError` was caught and
  the source skipped, removing all mutual coupling -- `r` jumped from 0.5 to
  1.0 and the EPR doubled, with no warning. This hit
  `run_outage_study(auto_parallel_coefficients=True)` directly, since an
  outage disables named elements and leaves orphaned buses active. The solve
  is now restricted to the fault bus's own galvanic component, a source with
  no phase-conductor route to the fault is reported by name, and a genuinely
  singular restricted system raises with the branch types to check.

- **`_warning_parallel_coeffcient` no longer false-alarms.** It counted
  paths across *all* faults, so a strictly radial network that merely
  carried two fault definitions triggered the meshed-network warning. The
  check is now scoped to the active fault and to a single source reaching it
  over more than one path -- the only configuration in which the path-based
  mode multiplies the current.

### Docs

- `docs/concepts.md`: the path-finding section now describes the two modes,
  what the phase impedance is for and when the proxy fallback matters. The
  branch-current formula was corrected -- the page had
  `(u_from - u_to)/Z_self` while `compute_branch_currents` evaluates
  `(u_to - u_from)/Z_self`, so a positive `ResultBranch.i_s` means
  `to_bus` -> `from_bus`, the opposite of the phase-current convention. That
  sign is load-bearing for any side-resolved evaluation.

- New notebook `notebooks/25_mesh_phase_currents.ipynb` reproducing the
  collapse, the fix and the phase-impedance-driven split.

- New notebook `notebooks/26_cuts_and_rho_f_sweep.ipynb` walking the whole
  chain on a 20 kV feeder: cuts left and right of the fault, both reduction
  factors, a rho-f catalogue sweep and the statistics on top.

- `docs/concepts.md` gains "Reduction factor on a current basis",
  "Splitting the network at the fault" and "Characterising a location without
  its electrode"; new API pages `api/decomposition.md`, `api/sweep.md`,
  `api/statistics.md` and `api/response.md`, all registered in the nav.

- New notebook `notebooks/27_bus_response_extremes.ipynb`: the extremes, the
  closed form against genuine solves, and the whole curve at no solve cost. It
  also shows the counter-intuitive consequence the extremes exist to surface —
  an *ideal* electrode at the faulted station roughly doubles the potential rise
  at the source station.

- **New published example `docs/examples/fault_decomposition.ipynb`**, the
  curated counterpart to the three research notebooks above: a six-station
  20 kV feeder carried through both reduction factors, `analyze_cuts`,
  `bus_response` and a soil-resistivity sweep with `summarize`/`classify`,
  closing on `run_reference_cases`. Registered in the nav and in
  `docs/examples/index.md`. It also puts the `U_E`-versus-bus-EPR confusion
  on the page rather than leaving it to be rediscovered: the norm's chain
  closes on `u_earthing` (353.46 V here), **not** on the EPR at the fault bus
  (500.17 V), and the notebook prints both side by side.
- `README.md` and `docs/index.md`: the feature lists were still those of
  0.5.0 and mentioned none of the analysis layer. Both now carry the phase
  current solve, the two reduction factors, cuts, sweeps, `bus_response` and
  the reference cases. The README's model overview said the reduction factor
  *is* the EPR quotient; it now names the quotient's structural blindness to
  the fault-bus electrode and points at the current-based factor for
  sensitivity work.

---

## [0.5.0] — 2026-07-31

> This release makes direct current a real operating point, adds transient
> simulation with two independent solver paths, and adds the
> equipment-integrity side of a grounding study — thermal limits for the
> conductor between two buses and for the two grounding elements at a bus —
> next to the potential rise that was there before. It also carries fourteen
> audit passes worth of correctness work. The full measurement protocol for
> every finding (reproduction, negative control, and mutation testing where
> the change touched drawing or matrix-assembly code) is kept out of these
> notes and lives in [`docs/audit-log.md`](docs/audit-log.md).
>
> **Breaking changes are marked in _Changed_.** Databases written by an
> earlier release are converted automatically — see _Added_.

### Added

- **`f = 0 Hz` is a frequency, not a special case.** A study at zero
  frequency used to raise, and the documented workaround was to enter
  `f = 0.1 Hz` instead. Both solvers now evaluate the DC bin, and the three
  singularities a formula string can hit there are told apart rather than
  lumped together: Carson's removable `0·∞` resolves to its limit, the
  capacitive `1/(jωC)` stays an infinite open circuit, and a genuine failure
  (`√ρ` with negative `ρ`, a `NaN`) still raises. An impedance that becomes a
  true short circuit at DC is replaced by a substitute impedance sized from
  the network's own smallest impedance (`√eps · |Z|min`) and reported through
  the new `gi.DCLimitWarning`; `docs/concepts.md` explains when to merge the
  two buses instead. New public helpers `is_short_circuit(z)` and
  `dc_substitute_impedance(...)` in
  `groundinsight.utils.impedance_calculator`. What the old workaround cost,
  measured: for the common `(0.25 + j*0.6)*l` spelling it reported
  **718.54 V instead of 456.62 V** and `Z_G = 2.87 Ω` instead of `1.82 Ω`,
  and the error did not shrink with the frequency.
- **Transient simulation** — the new `groundinsight.simulation.transient`
  sub-module, with two solver paths behind one contract:
  `gi.TransientStudy(network, fault_name)` plus `set_source_waveform`,
  `set_observation(buses=, branches=)` and `solve(t_end, dt, solver=)`,
  returning `gi.ResultTransient` (`time_s`, `epr_t`, `i_branch_t`,
  `source_t`, `to_polars()`). Switching solver paths is a one-line change.
  - `solver="fft"` — frequency-domain superposition of a user waveform.
  - `solver="state_space"` — modified nodal analysis assembled as
    `dx/dt = A·x + B·u` and integrated with `scipy.signal.lsim`. Resolves
    the true ring-down that the FFT path cannot, and supports bus
    capacitance, automatic pi-section lumping of branch shunt capacitance,
    voltage sources (Norton reduction for a resistive loop, an explicit
    inductor state for `L_src > 0`) and Carson-style mutual coupling.
- **Lumped RLC parameterisation** — optional `R_formula`, `L_formula`,
  `C_formula` on `BusType` and `R_self_formula`, `L_self_formula`,
  `C_self_formula`, `R_mutual_formula`, `M_mutual_formula` on `BranchType`,
  evaluated into real `Dict[float, float]` fields on `Bus` and `Branch` by
  the new `compute_real_value` helper. All default to `None`, so the
  frequency-domain path and existing networks are unaffected; the
  duplication against `impedance_formula` is intentional so the stationary
  and transient parameterisations can be maintained independently.
- **`gi.waveforms`** — `step`, `sinusoidal_with_dc_offset` and
  `damped_oscillation` factory functions returning vectorised time-domain
  callables, plus the plotting helpers `gi.plot_epr_transient` and
  `gi.plot_branch_current_transient`.
- **Thermal limit check for conductors (roadmap F1).**
  `gi.check_conductor_limits(network, fault, t_k, *, kappa=/r_to_x=, n=, f=)`
  compares every grounding branch's thermally equivalent short-time current
  against its adiabatic limit and returns a long-format Polars frame.
  `I_th = I_rms·√(m + n)` (IEC 60909-0) is applied to the *superposed* AC RMS
  shield current — linear superposition first, the non-linear factor on the
  aggregate — against `I_adm = k·S/√t_k` (IEC 60949). `BranchType` gains
  `conductor_material`, `cross_section_mm2`, `theta_initial_C` and
  `theta_final_C`; new helpers `gi.iec60949_k`, `gi.iec60909_m`,
  `gi.kappa_from_r_to_x` and the catalog `gi.IEC60949_MATERIALS`, verified
  against the published `k` tables (Cu/XLPE 143, Cu/PVC 115, Al/XLPE 94).
- **Thermal limit check for nodes (roadmap F4).** EN 50522 and IEC 61936-1
  size the **earthing conductor** and the **earth electrode** for different
  currents, and the solver did not expose the first of them at all — in the
  verification network the two differ by a factor of 41.
  `gi.check_node_limits(...)` assesses both per bus and reports every bus,
  leaving `within_limit = None` where the `BusType` declares nothing.
  `ResultBus.i_inj` / `i_inj_freq` report the source-only nodal injection —
  the current a lumped earthing conductor carries into the system — which is
  *not* `ResultBus.ia = u_EPR / Z_B`, the share dissipated into the soil
  through the electrode. `BusType` gains five fields per element
  (`material`, `cross_section_mm2`, `theta_initial_C`, `theta_final_C`,
  `current_split`). `current_split ∈ (0, 1]` is a declared factor, not a
  derived one: the split depends on geometry the nodal model does not carry.
  `gi.final_temperature(material, covering)` and `gi.FINAL_TEMPERATURES`
  name their source per entry and raise rather than return a
  plausible-looking number for an entry they cannot cite.
- **IEC 60909 short-circuit characteristics from a solved pandapower case
  (roadmap F2/F3).** `gi.read_shortcircuit_results(net_pp, ...)` reads a
  `calc_sc` result as 60909 quantities in amperes,
  `gi.apply_shortcircuit_characteristics(...)` writes them onto the model
  and returns an audit frame including the previous values, and
  `gi.resolve_fault_sc_characteristics(...)` reduces several infeeds to one
  effective `kappa` (current-weighted by default, reproducing the sum of the
  individual peaks to 1.6e-16; `aggregation="max"` for the conservative
  variant). `Source` gains `i_k_a`, `r_to_x`, `kappa`, `Fault` gains
  `t_k_s`, `n_factor` — metadata that never enters the linear solve. Two
  deliberate deviations from pandapower, both pinned by tests: `ip_ka` and
  `ith_ka` are derived here because pandapower returns `NaN` for the `1ph`
  case that matters for grounding, and `I_th` is always recomputed because
  pandapower sets `m = 0` for `kappa > 1.99` where the analytic limit is
  `m = 2`, which under-estimates the thermal stress. The `R/X` driving
  `kappa` is that of the earth-fault loop `2·Z1 + Z0`, not `R1/X1`.
- **Databases written by an older release are converted, not rejected.**
  `gi.migrate_database(path)` converts a file written by the name-keyed
  schema to the current one and `gi.needs_migration(path)` classifies a file
  without touching it; `gi.start_dbsession()` migrates automatically
  (`migrate=False` restores the previous behaviour, which now raises a
  `RuntimeError` naming the tool and your file). The original is copied to
  `<path>.bak` first and an existing backup is never overwritten; the
  conversion is written to a temporary sibling and moved in with
  `os.replace`, so an interruption leaves either the old file or the new one.
  Every converted network is loaded back *before* the swap, because a
  structurally valid file is not necessarily a usable one.
  `gi.MigrationReport` names what could not be recovered — shared elements,
  paths whose segment order does not form a connected chain, orphans,
  dangling memberships, defaulted cells — instead of quietly guessing, and a
  missing `length` or `scalings` aborts the migration rather than being
  invented.
- **The plotting helpers accept `ax=` and `close=`.** `ax=` draws into an
  axis you already have, which is what makes a base case and an outage case
  comparable in one figure; `close=True` unregisters the figure the call
  created, so a parameter sweep no longer accumulates figures until
  matplotlib warns at twenty. Both are keyword-only and appended behind a
  `*`, so the historical positional signature is unaffected.

### Changed

- **Breaking: the SQLite schema keys elements per network.** Buses,
  branches, faults, sources and paths carry the composite primary key
  `(network_name, name)`; the five `network_*` association tables are gone,
  the relationships are ordinary one-to-many, and `path_segments` was
  promoted to a mapped `PathSegmentDB` keyed
  `(network_name, path_name, position)`. Two networks in one file that both
  contained a bus called `"A"` previously shared a single row, so saving the
  second silently overwrote the first one's impedance. Element order now
  survives the round-trip — before, the load order was whatever SQLite
  returned, which reordered the Y-matrix assembly and changed the LU
  factorisation in the last bits; a save/load/re-solve is now bit-identical.
- **Breaking: a zero, a sub-invertible or a negative-real-part impedance
  raises `ValueError`.** `Z = 0` was assembled as an *open circuit* — the
  exact opposite of the ideal earth it is written to mean — and an impedance
  too small to invert put `inf` on the diagonal and `NaN` in the result. The
  rule covers bus grounding impedances, branch self impedances where
  `grounding_conductor=True`, and `Source.source_impedance`, and it runs
  again immediately before the matrix is assembled. Deliberately out of
  scope: mutual impedances (`Z_mutual = 0` is the ordinary way to say "no
  coupling"), non-grounding branches, inactive elements, frequencies outside
  `network.frequencies`, and `inf`, which remains the documented open-end
  sentinel. Replace a deliberate `0.0` with a small finite value — `1e-6 Ω`
  is seven digits of the ideal limit in a 1 Ω network and, unlike `0.0`,
  actually behaves like one.
- **Breaking: `create_paths` — and therefore `run_fault` — rejects a network
  with no sources or no faults**, which previously produced a complete,
  structurally valid all-zero result. The check is on the *collections being
  empty*, never on the resulting path count: an outage scenario that islands
  the fault bus still runs and still legitimately returns all zeros.
- **Breaking: `create_network_assistant` validates `number_buses` and
  `branch_length`.** A line of `n` buses has `n − 1` branches; passing `n`
  lengths silently dropped the last one and passing too few raised a bare
  `IndexError`. A scalar `branch_length=1.0` now gets a message instead of
  `TypeError: 'float' object is not subscriptable`.
- **Breaking: `max_iter` must be an `int >= 1`** in `find_max_rho_scaling`
  and `find_max_rho_f_scaling`. `max_iter=60.0` was never harmless — the
  loop condition rounds up, so `2.7` meant three steps, and a cap that does
  not mean what it says is worse than no cap.
- **Breaking: the plotting helpers reject two argument combinations and an
  unusable `figsize`.** `ax=` with `figsize=` and `ax=` with `close=True`
  both raise `ValueError`, because the figure belongs to the caller in that
  case. A `figsize` that cannot be used raises instead of being swapped for
  the default: matplotlib accepts `figsize=(0, 0)` at creation and only
  fails when the figure is drawn, which pointed the traceback at `savefig`
  rather than at the call responsible.
- **Final temperatures now have two regimes with one source each.** The
  catalogue previously mixed the National Grid Earthing Technical
  Specification and IEC 60364-5-54 in one table. Uninsulated conductors
  follow **EN 50522 Table 2** — 300 °C for bare copper, aluminium, steel and
  galvanised steel, 150 °C for tinned copper, whose tin coating melts at
  231.9 °C. Insulated conductors are capped by their insulation, not by the
  metal: **IEC 60364-5-54 Table 54.2** gives 160 °C for PVC and 250 °C for
  XLPE and EPR, for every conductor material, because the insulation fails
  first. `IEC60949_MATERIALS["Steel"]["theta_final_default_C"]` is lowered
  from 400 °C to 300 °C: `θ_f` enters the IEC 60949 material constant `k`
  under a logarithm, so the old default produced a larger `k` and permitted
  *more* current — the unsafe direction for a limit check. Pass
  `theta_final_C=400.0` explicitly to reproduce earlier studies. `"PE"` is
  deliberately not tabulated and raises, as do physically impossible
  pairings such as `("Al", "tinned")`.
- **A finite reactance at 0 Hz falls back to the real part, with a warning.**
  `(0.25 + j·0.6)·l` reports 0.6 Ω of reactance at *every* frequency, and at
  DC that is a statement about nothing — a reactance either vanishes or is
  infinite. The warning quotes the length-invariant X/R ratio rather than the
  two values, so Python's default filter collapses a hundred-branch network
  into one line, and it names the remedy: write the reactance as
  `j*2*pi*f*L` and it vanishes at DC by itself. The fallback is off for
  R/L/C parameters, which are real at every frequency by contract.
  Correspondingly, `Z = 0` and a non-invertible `|Z|` are accepted at 0 Hz
  and rejected above it — an inductance really is a short circuit at DC and
  really is not one at 50 Hz — while a **negative real part stays rejected at
  every frequency**, because a passive element is passive at DC too.
- **`find_max_rho_scaling` and `find_max_rho_f_scaling` return four new
  keys.** `status` is one of `"converged"`,
  `"bracket_within_tol_on_entry"`, `"bracket_fully_admissible"` or
  `"max_iter_reached"`, `converged` is `True` for the first two, `c_bracket`
  is the interval that provably contains the threshold, and
  `bracket_rel_width` is its relative width. For a fully admissible bracket
  the interval is `(c_hi, inf)`, which makes "widen `c_bounds`"
  machine-readable. Every previous key keeps its name and meaning, so code
  reading `c_max` still works — it now has a way to find out whether that
  number is a maximum.
- **An unsolvable nodal system is diagnosed instead of being reported as a
  singular matrix.** A `NaN` in `Y` or in the injection vector is now a
  computation error naming the buses and branches whose stored impedance is
  `NaN`, with self and mutual told apart, rather than reaching SciPy and
  coming back as "singular matrix" — which sends the engineer looking for a
  topology error. "No path to reference earth" lists which buses were
  examined and why each failed, grouped into `Z = 0`, infinite, `NaN` and no
  value stored at that frequency, truncated after five names with the total
  reported. The word `Singular` is kept at the front for callers matching on
  it.

### Fixed

- **Security: an impedance or RLC formula string was an arbitrary-code
  execution vector**, and `mkdocs.yml` loaded `polyfill.io`, a domain that
  had changed hands and was serving malicious redirects. Both closed.
- **Formula evaluation.** A formula that merely *contained* the letters
  `nan` (`nan_factor`, `tanh`) became an open circuit. A parameter whose name
  collides with one of SymPy's ~680 exported names was shadowed, and
  `params={"I": ...}` was silently overwritten with the imaginary unit. Every
  formula whose argument goes negative — the common `√(1 − x)` fit outside
  its range — collapsed to `NaN`, which was then passed on as if it were a
  number, into `ComplexNumber`, into `Y`, and out the far side as a result.
  `compute_real_value(..., name=...)` now actually reaches the caller, so two
  `BranchType` fields sharing an expression no longer produce two
  byte-identical messages.
- **Paths and topology.** The path set went stale after a fault or a source
  was added following the first `run_fault`: the new element was never given
  paths, which made `find_max_rho_scaling` over-estimate the admissible soil
  resistivity. `run_fault` now rebuilds when the active topology changed, and
  `Network.invalidate_paths()` is an atomic rebind scoped to the calling
  network rather than a clear of the global cache. Two topology fingerprints
  collapsed parallel branches into one key, so two structurally different
  networks hashed alike. `Network.define_paths` no longer shadows
  `(source, fault)` pairs that share a source.
- **Transient solvers.** With `network.paths` empty or stale the state-space
  path dropped the entire Carson coupling silently, and a branch shared by
  two parallel paths of one source received the coupling twice. Branch
  currents from the state-space solver were inverted relative to
  `compute_branch_currents` and the FFT solver; both now follow
  `i_branch = (v_to − v_from) · Y_self`, so the two traces sit on top of each
  other for the forced response. The source waveform is treated as the
  literal injection instead of being rescaled.
- **Persistence.** `save_network` committed its delete-then-insert in two
  steps, so an interruption could leave a half-written network on disk, and
  it wrote raw type rows instead of merging them. `Network.results`,
  `Fault.active` and open-end `inf` values now survive the JSON and SQLite
  round-trips. Inconsistent path segments are rejected before anything is
  written.
- **pandapower import.** `calc_sc`'s `tk_s=1.0` signature default was read
  as the protection's actual clearing time. Three `pl.DataFrame(...)` sites
  relied on polars inferring a schema from the first row. A missing line
  length was silently replaced by 1.0 km — a fabricated length changes the
  earth potential rise with nothing in the output to show it was invented —
  and zero or negative lengths are now rejected. Duplicate line names no
  longer abort the import.
- **Results that looked like answers.** A frequency that was never computed
  was plotted as a bar of height zero, indistinguishable from a computed
  zero. The relative delta of an outage study divided by the reference value
  without guarding a zero reference. `check_conductor_limits` and
  `check_node_limits` reported "no violations" on a result that was only
  half built, and an element carrying just one half of its thermal data was
  skipped without a word. `str()` on `ResultReductionFactor` and
  `ResultGroundingImpedance` raised `AttributeError`. An unresolvable bus or
  branch type surfaced as an `AttributeError` from deep inside the solver,
  and two documented `ValueError`s were never raised at all.
- **The inverse-`ρ` searches.** `iterations == 0` meant three different
  things; exhausting `max_iter` was silent; `tol_rel` and `max_iter` were
  unvalidated; a `NaN` EPR limit passed the positivity guard because
  `nan <= 0` is `False`; and `c_bounds=(1e-3, inf)` passed the ordering
  check. The shared checks and the report builder now live in
  `analysis/_bisection.py` so the two searches cannot drift apart. The
  routines also restore network state instead of leaving the scaling they
  last tried in place.
- **A single un-earthed tower no longer fails the whole network**, and a
  genuinely singular or floating network raises a clear `ValueError` naming
  the cause instead of surfacing as a SciPy factorisation error.
- **Session and cache handling.** `_set_session` is the single source of
  truth for the module globals; `start_dbsession` is hardened against a
  second call and `close_dbsession` tears down all three globals;
  `set_log_level` is handler-idempotent across repeated calls; the
  pathfinder caches are bounded LRUs that no longer key on `id(network)`;
  `outage_context` clears them and is re-entrant. `Network.frequencies` is
  validated at construction and warns on a non-monotonic order.
  `gi.set_active_fault(network, fault_name, keep_results=False)` is
  available at the top level, and `__all__` lists every public helper.

### Docs

- `docs/concepts.md` gains **"Direct current (`f = 0`)"** — the three
  singularities and how they are told apart, the reactance fallback and how
  to write a formula that does not need it, what the short-circuit
  substitution costs and when to merge two buses instead — and **"Which
  values a formula may produce"** under *Impedance formulas*.
- `docs/transient.md` documents the state-space solver, the DC bin, the
  Carson-mutual substitution and the waveform contract.
- New API pages for the transient sub-module and the analysis helpers;
  `docs/api/analysis.md` carries the documented discrepancy between the
  `IEC60949_MATERIALS["Steel"]` default and EN 50522 Table 2.
- The dead **"Research notebooks"** nav section was removed from
  `mkdocs.yml`. mkdocs only collects files below `docs_dir`, so every
  `../notebooks/*.ipynb` entry was reported as unresolvable and silently
  dropped — the section never appeared on the site, and the build stayed
  green because `mkdocs gh-deploy` runs without `--strict`. Curated
  notebooks live in `docs/examples/`.
- `mkdocs.yml` uses `docstring_style: numpy`, matching the docstrings the
  project actually writes.

### Internal

- The audit narrative moved out of this file into
  [`docs/audit-log.md`](docs/audit-log.md): fourteen passes, each finding
  with its reproduction, its negative control, and — where the change
  touched drawing or matrix-assembly code — a mutation-testing result. The
  open findings not yet scheduled are in the same document; the roadmap is
  at the end of this file.
- **The thermal assessment is opt-in and provably so.** A grounding study is
  useful long before anybody has decided on a cross-section, so an element
  with no thermal data is reported with its current columns filled and
  `within_limit = None` rather than being dropped or defaulted. The
  *excitation* is not optional: once a check is requested, `t_k` and the
  peak factor must be resolvable, and a half-declared element is a warning
  rather than a silent skip.
- The release tooling's own preconditions are now tested rather than
  assumed. `pyproject.toml`, `src/groundinsight/__init__.py` and
  `CITATION.cff` are checked against each other by
  `test_version_matches_pyproject` and `test_version_matches_citation_cff`,
  which read the expectation from the project metadata instead of pinning a
  literal that `scripts/release.py` rewrites on every release commit.

---

## [0.4.0] — 2026-05-09

### Added

- **Thevenin (voltage) source mode for `Source`** — opt-in alternative to
  the legacy current-source injection, intended for transient simulations
  where the actual fault current is determined by the loop impedance
  rather than being prescribed. New fields on `Source`: `source_type`
  (`"current"` default, `"voltage"` for Thevenin), `voltage`, and
  `source_impedance` (both `Dict[float, ComplexNumber]`). A
  `model_validator` enforces that exactly the fields belonging to the
  declared mode are populated, that `voltage` and `source_impedance`
  share frequency keys, and that no `source_impedance` entry is zero.
- **`gi.create_voltage_source(name, bus, voltage, source_impedance, ...)`**
  — convenience factory that mirrors `gi.create_source` but constructs a
  Thevenin source and validates that the bus exists in the optional
  network argument.
- **Y-matrix loop closure for Thevenin sources** —
  `ElectricalNetwork._construct_Y_matrices` now adds an admittance
  `Y_src = 1/Z_src` between source bus and the active fault bus for every
  voltage-mode source. Together with the Norton-equivalent injection
  `I_N = scaling * U / Z_src` at the source bus and `-I_N` at the fault
  bus this is the exact Norton-Thevenin translation of the legacy
  current-source formulation. Default behaviour is unchanged: a network
  without any voltage-mode source produces bit-identical results to
  `0.3.x`.
- **Notebook `notebooks/08_thevenin_source.ipynb`** — demonstrates that a
  Thevenin source with very large `Z_src` reproduces the current-source
  EPR, that a finite `Z_src` reduces the effective fault loop current
  below the open-circuit Norton value, and visualises the transition
  between the two limits via a `Z_src` sweep.

### Internal

- Persistence: `SourceDB` gained `source_type`, `voltage` and
  `source_impedance` columns; the existing `values` column became
  nullable. `from_pydantic` / `to_pydantic` route the data through the
  new helpers `_freq_dict_to_pydantic` / `_freq_dict_to_json`. JSON and
  SQLite roundtrip tests cover both source modes.

- **`groundinsight.io` sub-package** — new home for external-network
  importers. First inhabitant: a pandapower importer.
- **`ImportDefaults` Pydantic model** (`groundinsight.io.defaults`) —
  shared shape for every importer: `rho`, `frequencies`,
  `default_bus_type`, `default_branch_type`. Future importers (PowerFactory
  `.dgs`, NEPLAN, PSS®E) reuse the same model so a single
  `ImportDefaults` instance can drive all of them.
- **Pandapower importer** (`groundinsight.io.pandapower_import`) with two
  entry points:
  - `from_pandapower(net, *, defaults, voltage_level_kV, network_name=None,
    include_trafos=False) -> Network` — maps `pp.bus` and `pp.line` rows on
    a single voltage level to groundinsight `Bus` and `Branch` objects,
    using `defaults.default_bus_type` / `default_branch_type` and
    `length=length_km`. Switches, ext_grids, sgens and loads are ignored;
    `include_trafos=True` is reserved for a future release. Pandapower
    `in_service=False` propagates to the new `Bus.active` / `Branch.active`
    flag so the imported network plugs straight into the outage / what-if
    layer.
  - `preview_pandapower_import(net, *, voltage_level_kV) -> pl.DataFrame`
    — pre-flight summary listing kept and skipped elements with
    explicit `reason` (`voltage_level_mismatch`,
    `endpoint_off_target_voltage_level`, `endpoint_bus_missing`).
- Public re-exports: `gi.ImportDefaults`, `gi.from_pandapower`,
  `gi.preview_pandapower_import`.
- **Optional dependency**: pandapower is now declared as an optional
  extra. Install with `pip install 'groundinsight[pandapower]'` (or
  `poetry install --extras pandapower`); the core install stays lean.
- **`active` flag on `Bus` and `Branch`** — both Pydantic models now carry
  a boolean `active` field (default `True`). An inactive `Bus` is removed
  from the nodal system entirely; an inactive `Branch` behaves like an
  open circuit (no contribution to the admittance matrix, no
  Norton-equivalent injection from the mutual coupling, branch current in
  the result is zero). The pathfinder skips inactive elements when
  building the adjacency graph, so `define_paths` no longer enumerates
  paths that cross them. Backwards compatible: existing JSON / SQLite
  payloads load with `active=True` for every element.
- **`groundinsight.simulation.outage`** — new sub-module for what-if
  studies built on top of the new `active` flag. Exposes:
  - `Outage` — Pydantic model describing a scenario (named, with
    `disabled_buses` and `disabled_branches` lists).
  - `outage_context(network, outage)` — context manager that flips the
    listed elements to `active=False` for the duration of a `with` block
    and restores them afterwards (including the previous path cache).
  - `run_outage_study(network, fault=..., scenarios=[...])` — runs
    `run_fault` for the base case (optional) and every scenario in one
    call and returns an `OutageStudyResult` aggregating per-scenario
    `res_buses` / `res_branches` DataFrames.
  - `OutageStudyResult.compare_buses(...)` /
    `compare_branches(...)` — long-format Polars DataFrames with absolute
    and relative deltas against a reference scenario (default: the base
    case). Same names are re-exported on the package: `gi.Outage`,
    `gi.run_outage_study`, …
- **`groundinsight.analysis` sub-package** — new home for higher-level
  analysis routines on top of `run_fault`. First inhabitant: an inverse
  determination of the maximum admissible bus rho-f characteristic.
- **`find_max_rho_scaling`** (`groundinsight.analysis.inverse_rho`) —
  given an existing network, an active fault and a set of selected
  buses, log-bisects the largest uniform scaling factor `c` of those
  buses' `specific_earth_resistance` such that the RMS earth potential
  rise at the fault bus stays at or below a user-supplied limit
  `u_max`. Returns `c_max`, the EPR at `c_max`, the per-bus
  `rho_max = c_max * rho_0`, and the iteration count. Honours every
  user-defined `BusType.impedance_formula`, restores the original
  `rho` values via a `finally` block (success or failure). Re-exported
  on the package as `gi.find_max_rho_scaling`.
- **rho-f model inversion** (`groundinsight.analysis.inverse_rho_f`) —
  foundation layer for inverting the canonical rho-f bus model
  `Z(rho, f) = k1*rho + (k2+jk3)*f + (k4+jk5)*rho*f` (the same form
  fitted by `groundmeas.services.analytics.rho_f_model`). Two public
  entry points, both re-exported on the package:
  - `evaluate_max_epr_under_k(network, bus_names, k, *,
    fault_scalings=None, run_fault_kwargs=None) -> Dict[str, float]` —
    overwrites every selected bus' impedance with the rho-f form for
    the given `k = (k1..k5)`, sweeps each bus once as the active fault
    (reusing pre-existing faults at swept buses, otherwise creating
    temporary ones with `fault_scalings`), runs `run_fault` and returns
    the per-bus RMS EPR. Restores bus impedances, drops temporary
    faults, and rebuilds the path cache in a `finally` block.
  - `find_max_rho_f_scaling(network, bus_names, u_limit, k_ref, *,
    c_bounds=(1e-3, 1e3), tol_rel=1e-3, max_iter=60, ...)` —
    log-bisects the largest scaling factor `c` such that
    `k = c * k_ref` keeps the maximum RMS EPR across the swept buses
    at or below `u_limit`. Returns `c_max`, `k_max = c_max * k_ref`,
    the achieved max EPR and the per-bus EPR breakdown at `c_max`.
  - `select_rho_f_from_catalog(network, bus_names, u_limit, candidates,
    *, fault_scalings=None, run_fault_kwargs=None,
    sort_by="max_epr_asc")` — evaluates every entry of a curated
    catalog `{name: (k1..k5)}` and returns a Polars DataFrame with
    columns `name`, `k1..k5`, `max_epr_rms_V`, `admissible` (bool) and
    one `epr_<bus>_V` per swept bus. Default sort puts admissible
    candidates first, ordered by ascending EPR margin. Useful for
    picking the feasible soil/curve from a hand-curated list (e.g.
    `groundmeas` fits per soil class).
  A full Pareto front in ℝ⁵ remains a roadmap item and will land as a
  separate `find_max_rho_f_pareto_front` on top of the same helper.
- **`notebooks/07_inverse_rho_f.ipynb`** — step-by-step demonstration
  of the three rho-f inversion entry points on a small three-bus MV
  line with frequency-dependent excitation.

### Changed

- **`BusDB` / `BranchDB`** schema gains a `active BOOLEAN NOT NULL DEFAULT 1`
  column to mirror the new Pydantic field. `from_pydantic` /
  `to_pydantic` round-trip the value; existing SQLite databases that
  predate this change still load (the column defaults to `True`).
- **Pathfinder graph and solver assembly** filter inactive elements
  (see *Added*). The behaviour is unchanged whenever every bus and
  branch keeps the default `active=True`.

---

## [0.3.2] — 2026-05-07

### Added

- **`groundinsight.set_log_level(level)` helper** — convenience entry
  point for interactive use. Attaches a single `StreamHandler` with a
  simple formatter to the package logger and sets the requested level.
  Idempotent: repeated calls only adjust the level. Default behaviour of
  the library is unchanged (a `NullHandler` is attached on import, no
  output without explicit configuration).

### Changed

- **Minimum Python version raised to 3.14.** `pyproject.toml` now
  declares `python = "^3.14"`; the CI matrix runs only against 3.14
  and the docs / release workflows pin the same version. This aligns
  the dissertation tool family (`groundinsight`, `groundmeas`,
  `groundfield`) on a single supported interpreter. **Breaking** for
  users still on 3.12 / 3.13 — pin to `groundinsight<0.3.2` if you
  cannot upgrade your Python yet.
- **User-facing messages migrated from `print()` to the standard
  `logging` module.** Every call previously printing status, warnings or
  solver errors in `__init__.py`, `network_operations.py`,
  `electrical_network.py` and `models/core_models.py` now goes through
  a per-module `logging.getLogger(__name__)`. Level mapping:
  - `INFO` for status (database session started/closed).
  - `WARNING` for recoverable issues that the user should notice
    (already-started/no-session-to-close, overwriting an existing
    `Bus`/`Branch`/`Fault`/`Source`, missing results when
    aggregating, parallel-coefficient guidance in
    `build_electrical_network`).
  - `ERROR` (with `exc_info=True`) for `numpy.linalg.LinAlgError` raised
    while solving the nodal equation per frequency.

  Callers can silence everything by doing nothing (the default), enable
  console output with `groundinsight.set_log_level("INFO")`, or wire the
  package logger into their own `logging` configuration. Doctests
  continue to use plain `print(...)` as before — they are not library
  output.

---

## [0.3.1] — 2026-05-06

### Changed

- **Impedance evaluation is now cached and vectorised.**
  `utils.impedance_calculator.compute_impedance` no longer calls
  `sympy.sympify` and `sympy.lambdify` on every invocation. Compiled
  functions are memoised with an `lru_cache` keyed on the formula
  string and parameter signature, so all buses sharing a `BusType`
  (and all branches sharing a `BranchType`) reuse the same compiled
  callable. Evaluation across all frequencies happens in a single
  vectorised numpy call instead of a Python loop. The public API of
  `compute_impedance` is unchanged. Expected speed-up for the
  impedance-build phase of `run_fault`: roughly one to two orders of
  magnitude on networks with many buses/branches per type.

### Internal
- Added `_compile_formula(formula_str, param_names)` helper in
  `utils/impedance_calculator.py` exposing the cached compilation
  step, plus a `clear_formula_cache()` function for tests and
  long-running processes that want to release compiled SymPy
  callables.
- (existing) `_compile_formula(...)` + `clear_formula_cache()` …
- Topology test bench extended in `tests/test_topology_and_reduction.py`
  with two frequency-sweep cases (single cable, 20-bus symmetric ring
  with the fault diametrically opposite to the source). Both assert the
  closed form `r(f) = R / sqrt(R² + (ωL)²)` and convergence to 0 for
  rising frequency. New helper
  `_ms_cable_branch_type_freq_dependent` derives constant
  `L_self = M ≈ 1.910 mH/km` from the existing 50 Hz reference values.

### Docs

- `notebooks/02_topologies.ipynb` extended with two new sections:
  a 20-bus symmetric ring (EPR profile assertions: maximum at the
  fault, mirror symmetry, source-side rise) and a frequency sweep
  showing the reduction factor decaying from ~0.385 at 50 Hz to
  ~0.004 at 5 kHz, with a side-by-side comparison against the
  closed-form analytical curve.

---

## [0.3.0] — 2026-04-23

Bug fix for mutual coupling in meshed topologies, new automatic
parallel-path distribution, first MkDocs documentation site, and a complete
CI/release pipeline.

### Added

- `run_fault(..., auto_parallel_coefficients=True)` runs a phase-only
  pre-solve to derive the per-path current share for ring and mesh
  topologies, instead of requiring hand-tuned `parallel_coefficient` values
  on each branch.
- Reference test suite
  (`tests/test_topology_and_reduction.py`) covering reduction factor on
  line, ring and mixed line/ring topologies.
- MkDocs Material documentation site with Installation, Quickstart,
  Concepts, Examples (three notebooks) and an `mkdocstrings`-driven API
  reference. Published automatically to
  <https://ce1ectric.github.io/groundinsight/>.
- `CITATION.cff` for scientific citation metadata.
- `scripts/release.py` — Poetry-invoked release script
  (`poetry run release {patch|minor|major|set X.Y.Z}`) that bumps the
  version in `pyproject.toml`, `src/groundinsight/__init__.py` and
  `CITATION.cff`, commits, tags and pushes.
- `scripts/generate_third_party_licenses.py` — regenerates the
  third-party license report on release.
- GitHub Actions workflows:
  - `ci.yml` — pytest matrix on Python 3.12 and 3.13, coverage report.
  - `docs.yml` — `mkdocs gh-deploy` on push to `main`.
  - `release.yml` — build → publish to PyPI via OIDC Trusted Publishing
    → create GitHub Release with auto-generated notes, on tag `v*`.

### Changed

- `README.md` rewritten: new badges, feature list including ring/mesh
  support, quickstart, model overview with math, mermaid workflow
  diagram, link to the docs site.
- `.gitignore` extended to cover AI-assistant context (`CLAUDE.md`,
  `.claude/`), regenerated notebook and test artefacts
  (`notebooks/grounding.db`, `notebooks/network.json`,
  `tests/test_grounding.db`) and the third-party license report.

### Fixed

- **Mutual-coupling direction in meshed topologies**: the Norton
  equivalent current injected along the path from source to fault now
  uses the path-derived direction (Variant A) instead of an
  index-based heuristic. The old behaviour produced wrong EPR and
  reduction factors on rings and meshes.
- Math rendering in the Jupyter notebooks on the docs site: replaced
  the Markdown-ambiguous `*` with `\cdot` inside the `$...$` blocks of
  the low-voltage example, so `mkdocs-jupyter`'s Markdown parser no
  longer eats the multiplication signs as emphasis markers.

### Internal

- Added MathJax 3 configuration (`docs/javascripts/mathjax.js`)
  supporting both `\(...\)` / `\[...\]` (arithmatex) and
  `$...$` / `$$...$$` (notebooks) delimiters, with re-typesetting on
  Material's instant-loading navigation.
- Consistency checks in the Y-matrix assembly
  (`_construct_Y_matrices`) to catch dimension and symmetry bugs
  earlier.
- `utils/impedance_calculator.py` hardened against missing symbols and
  case-insensitive `nan` strings (open-ended branches).

---

## [0.2.0] — 2024-12-07

First PyPI release with the full object model and solver.

### Added

- Public API: `create_network`, `create_bus`, `create_branch`,
  `create_source`, `create_fault`, `run_fault`.
- Pydantic v2 data model: `Bus`, `Branch`, `Fault`, `Source`,
  `Network`, `BusType`, `BranchType`, `Path`, `Result*`,
  `ComplexNumber`.
- Electrical network solver: nodal-admittance assembly per frequency,
  SciPy sparse LU (`splu`), mutual-coupling Norton equivalents,
  reduction factor and grounding impedance computation.
- DFS-based path finder between source and fault buses.
- SQLite persistence via SQLAlchemy (`save_/load_network`) and
  JSON import/export via Pydantic.
- Polars-DataFrame accessors `res_buses`, `res_branches`,
  `res_all_impedances`.
- Matplotlib helpers for EPR, branch-current and bus-current bar
  plots.
- Three example notebooks: `simple`, `CIRED`, `low_voltage_network`.

## [0.1.2] — 2024-12-05

Development release. See the git history for details.

## [0.1.1] — 2024-12-01

Development release. See the git history for details.

## [0.1.0] — 2024-12-01

Initial development release. Not installable via PyPI in the current
form — superseded by 0.2.0.

---

[Unreleased]: https://github.com/Ce1ectric/groundinsight/compare/v0.6.0...HEAD
[0.6.0]: https://github.com/Ce1ectric/groundinsight/releases/tag/v0.6.0
[0.5.0]: https://github.com/Ce1ectric/groundinsight/releases/tag/v0.5.0
[0.4.0]: https://github.com/Ce1ectric/groundinsight/releases/tag/v0.4.0
[0.3.2]: https://github.com/Ce1ectric/groundinsight/releases/tag/v0.3.2
[0.3.1]: https://github.com/Ce1ectric/groundinsight/releases/tag/v0.3.1
[0.3.0]: https://github.com/Ce1ectric/groundinsight/releases/tag/v0.3.0
[0.2.0]: https://github.com/Ce1ectric/groundinsight/releases/tag/v0.2.0
[0.1.2]: https://github.com/Ce1ectric/groundinsight/releases/tag/v0.1.2
[0.1.1]: https://github.com/Ce1ectric/groundinsight/releases/tag/v0.1.1
[0.1.0]: https://github.com/Ce1ectric/groundinsight/releases/tag/v0.1.0

---

## Roadmap

Feature ideas and scheduled work. Graduate an item from here into the
appropriate category of `[Unreleased]` once it is implemented and ready
for release.

### Scope boundary

`groundinsight` is a **reduced-model** solver for networked grounding
systems: nodal admittance assembly in the frequency domain, driven by
formula-based or tabulated impedance values. PDE/FEM field computation
is an **explicit non-goal** — it belongs in a companion package
`groundfield` that depends on `groundinsight` and produces reduced
models (impedance tables, multi-port Z matrices, `Network` objects)
which `groundinsight` consumes. The roadmap below is organised around
that split: the "bridge" items below define the data-exchange surface
between the two packages.

### Near term — bridge to `groundfield` (target: 0.4.0)

These items define the data-exchange surface between field-level
computations in `groundfield` and circuit-level solves in
`groundinsight`. The first work package (AP 1) of the dissertation
drives the priority.

- **Two-layer `SoilModel`** — dedicated Pydantic model with `rho_1`,
  `rho_2` and `h_12` fields, recognised as symbols by the formula
  evaluator (`rho1`, `rho2`, `h` in
  `utils/impedance_calculator.py` and `utils/validations.py`).
  Backwards compatible: single-layer behaviour when only `rho` is
  given. Shared type with `groundfield`.
- **`ImpedanceTable` as an alternative to formula strings** — accept
  `Dict[Tuple[rho, f], ComplexNumber]` or a user callback on
  `BusType` / `BranchType` alongside the existing SymPy formula path.
  Primary use case: ingest FEM-computed impedances from `groundfield`
  without pressing them into a closed-form formula.
- **Multi-port impedance matrix export (`Z_ON`)** — function
  `Network.impedance_matrix(f)` returning the full multi-port Z matrix
  of the grounding subsystem for a chosen set of terminal buses. This
  is the return channel: a field solution in `groundfield` can be
  reduced to a multi-port representation that `groundinsight`
  produces or consumes through the same interface.
- **Parametric TN-Ortsnetz builder** (new module
  `src/groundinsight/simulation/tn_builder.py`) — constructs a
  `Network` from structural parameters (number of family houses,
  small / medium commercial connections, cable-distributor quota,
  topology class). Directly supports the AP 1 parameter grid
  (5 / 10 / 30 / 80 / 200 × …).
- **Parameter-sweep runner** (`src/groundinsight/simulation/sweep.py`)
  — sweeps over `rho`, soil-layer thickness, network size, fault
  location and frequency; returns a long-format Polars DataFrame.
  Matches the AP 1 investigation programme.
- **Touch-voltage result type** — `ResultTouchVoltage` plus
  `Network.res_touch_voltages()`. Initial implementation uses a simple
  analytical surface-potential model in layered earth; higher-fidelity
  surface potentials come from `groundfield` via `ImpedanceTable`.

### Near term — external network import (target: 0.4.0)

Building grounding networks by hand is the single most time-consuming
step in any case study. Re-using existing distribution-network models
from established power-system tools eliminates that work and makes
`groundinsight` directly applicable to real network data. The two
priority importers below cover the dominant tools in the German
distribution-network space.

- **`pandapower` importer** — new module
  `src/groundinsight/io/pandapower_import.py` exposing
  `from_pandapower(net, *, defaults: ImportDefaults) -> Network`. Maps
  pandapower `bus`, `line`, `trafo` (and optionally `switch`,
  `ext_grid`) tables to `groundinsight` `Bus` and `Branch` objects.
  Required user-supplied defaults: `rho`, `frequencies`, default
  `BusType` and `BranchType` (impedance formulas), plus a
  per-`std_type` override map for cable shields / overhead PEN
  conductors. Length is taken from `line.length_km`; transformer
  branches default to a configurable star-point impedance formula.
  Round-trip example notebook under `notebooks/import_pandapower.ipynb`.
  *Shipped — `gi.from_pandapower`, `gi.ImportDefaults`,
  `gi.preview_pandapower_import`. The PowerFactory paths below are
  still open.*
- **PowerFactory importer** — new module
  `src/groundinsight/io/powerfactory_import.py` with two ingestion
  paths:
  - **`.dgs` export** (preferred, no PowerFactory installation needed)
    — `from_powerfactory_dgs(path, *, defaults)` parses the
    PowerFactory data-grid CSV/XML export and emits a `Network`.
  - **Live PowerFactory Python API** —
    `from_powerfactory(app, *, defaults)` for users running
    `groundinsight` next to a PowerFactory session
    (`powerfactory.GetApplication()`); kept behind a soft import so
    the dependency stays optional.
  Both paths share the same `ImportDefaults` schema as the pandapower
  importer.
- **`ImportDefaults` Pydantic model** — common schema for all
  importers: `rho`, `frequencies`, default `BusType` / `BranchType`
  references, per-element-type overrides, and a hook for
  user-supplied resolver callbacks (`resolve_bus_type(row) ->
  BusType`). Lives in `src/groundinsight/io/__init__.py` so future
  importers (DIgSILENT NEPLAN, PSS®E raw files) reuse it.
- **CLI / notebook helper** —
  `groundinsight.io.preview_import(net, defaults)` returns a Polars
  DataFrame summarising which buses and branches will be created and
  which elements were skipped (with reason), so users can validate
  the mapping before committing to a full network build.
  *Shipped as `gi.preview_pandapower_import`.*

### Near term — other

- **Codecov / Coveralls integration** — upload coverage from CI and
  show a PR diff badge; currently the XML report is only uploaded as
  a workflow artifact.
- **`CITATION.cff` validation in CI** — `cff-validator` step to catch
  broken metadata before a release is cut.
- **Release-notes template** — the GitHub Release body is currently
  auto-generated from PR titles, which reads rough. Either a manual
  `release_notes/vX.Y.Z.md` workflow or a `release-drafter` config
  would give cleaner user-facing notes. (Partially addressed by the
  new `CHANGELOG.md` flow.)

### Medium term

- **`MeasurementScenario` abstraction** — model the earthing
  measurement (auxiliary electrode at distance `d`, measurement-loop
  source) as a first-class object instead of an ad-hoc `Fault`. Lets
  the AP 1 reference-case studies (variation of auxiliary-electrode
  position) be expressed without notebook scaffolding.
  Add a measurement setup with current injection into the power system to the neighbour bus. Add a comparing function of real earthfault to, measurement with auxiliary electrode and current injection to the neighbour substation, to understand systematic errors. 
- **Optional bus geometry (`position_xy`)** — no hard dependency, but
  enables Carson distance calculations and map-style visualisations.
  Must stay optional so existing networks keep working.
- **Plot refactor**: the matplotlib helpers are one-shot bar plots.
  Extract an optional Plotly backend so results can be explored
  interactively in notebooks and embedded in the docs site.
- **REST/HTTP API surface** — the empty `src/groundinsight/api/`
  package was reserved for this. Likely FastAPI-based, exposing
  `run_fault` and the `res_*` accessors. Decide scope (thin RPC vs.
  job-oriented) before writing code.
- **Performance**: parallelise the per-frequency solve in
  `ElectricalNetwork.solve_network` with `concurrent.futures`
  (threads are enough — the work is in SciPy's native sparse LU,
  which releases the GIL). Roadmap item in the README.

### Long term

- **Kron reduction / multi-port model reduction** — eliminate
  internal buses and emit a reduced-port Z (or Y) matrix. This is the
  `groundinsight`-side counterpart of the dissertation's AP 2: once a
  large reference network exists, reduce it to a transferable model
  class.
- **Time-domain extension** — convert frequency-domain results into
  transient EPR / branch-current waveforms via inverse FFT, given a
  user-supplied fault-current time function. Needs design work on how
  to spec the input and what assumptions (linearity, bandwidth) to
  guard.
  *Shipped — `gi.TransientStudy` with `solver="fft"` for exactly this,
  and `solver="state_space"` beyond it.*
- **Typed impedance DSL** — replace SymPy formula strings with a
  small typed expression tree so units and argument domains can be
  checked at construction time. Would also let the docs site render a
  formula catalogue automatically.
- **Grey-box measurement identification** — given a measurement
  data set (e.g. from `groundmeas`), estimate bus grounding
  impedances and coupling parameters. Likely lives in a dedicated
  identification package or in `groundmeas` itself; the hook on the
  `groundinsight` side is the `ImpedanceTable` interface listed under
  the `groundfield` bridge.

### From the audit passes (2026-05 – 2026-07)

Open items raised by the audit passes, de-duplicated across passes.
Items the passes proposed and that have since shipped are not repeated
here — they are in `[Unreleased]` or in an earlier version section. The
verbatim per-pass roadmap blocks are preserved in
`docs/audit-log.md`, Part 4.

**Safety assessment — the missing half of a grounding study.** 0.5.0 added
the equipment-integrity side (thermal limits for conductors and nodes);
person safety is still unassessed.

- `Network.res_touch_voltages()` and `ResultTouchVoltage`, then a thin
  `assess_touch_voltage(t_clearing_ms, standard="EN50522"|"IEEE80")`
  returning the admissible limit and a pass/fail flag per bus. Initial
  implementation from a simple analytical surface-potential model in
  layered earth; higher-fidelity surface potentials come from
  `groundfield` through `ImpedanceTable`. This is the single most useful
  safety-engineering deliverable on top of the present steady-state
  solver.
- **Mechanical (electrodynamic) limits** — `F ∝ i_p²` between parallel
  conductors, the deliberate second step after the thermal check.
  Needs conductor geometry (spacing, support distance), which the nodal
  model does not carry today.

**Modelling.**

- **PEN-conductor-aware `BranchType`** — `BranchType` distinguishes only
  `grounding_conductor: bool`. In a TN-Ortsnetz the PEN sits in parallel
  with the cable shield and the soil; modelling it explicitly
  (`pen_impedance_formula`) gives a cleaner reduction-factor split for
  low-voltage networks. Directly relevant to AP 1 of the dissertation.
- **`Network.frequencies` as a `tuple[float, ...]`** — a tuple plus the
  existing validator makes the network hashable, which is what the
  pathfinder cache key wants; it currently keys on a network fingerprint
  because `List[float]` is unhashable.
- **`gi.PathfinderConfig(cache_scope="per_network"|"global"|"none")`** —
  make the cache-scope decision explicit rather than a property of the
  module-level dictionaries.

**Convenience factories** — each of these removes notebook boilerplate
that the example notebooks currently carry by hand.

- **`Source.from_waveform(waveform, frequencies)`** — do the FFT once for
  a Thevenin source instead of forcing the user to assemble a
  per-frequency `voltage` dict.
- **`gi.waveforms.from_array(t_samples, values)`** — wrap a measured
  fault-current trace (e.g. from a digital fault recorder) into the
  `Callable[[np.ndarray], np.ndarray]` contract via `np.interp`.
- **`gi.TransientStudy.from_steady_state(network, fault_name)`** —
  pre-populate a transient study from the most recent `run_fault` result
  on the same network, copying fault scalings and the observation set.
- **`gi.from_pandapower_multi_voltage(net, defaults_map)`** — take a
  `Dict[float, ImportDefaults]` and produce one `Network` per voltage
  level (or one combined network once transformer branches land). Closes
  the gap to the AP 1 case studies that span 110 / 20 / 0.4 kV.

**Diagnostics.**

- **`Network.verify_steady_state_match(transient_result)`** — promote the
  manual cross-check in `test_state_space_matches_fft_on_lti_network` to a
  public diagnostic, so a user can validate a new transient setup against
  the per-frequency phasor solve.
- **`gi.diagnose(network)`** — one-call health check: stale
  `network.paths`, duplicate frequencies, missing impedance formulas,
  untyped branches, the inverse-`ρ` bus mismatch. Useful before each
  `run_fault` in a long notebook.

**Performance.**

- **Parallel per-frequency solve** — a `ThreadPoolExecutor` over the
  frequency loop in `solve_network`. SciPy's `splu` releases the GIL, so a
  near-linear speed-up is realistic for a harmonic study over 10–30
  frequencies. Low effort, high payoff. (Also listed under *Medium term*.)

**Cross-repo toolchain** (`groundfield` field solver, `groundinsight`
reduced network, `groundmeas` measurement store).

- **ADR for the `show_versions` return shape** — `gi.show_versions()`
  shipped in 0.5.0, but the key set must be pinned across
  `gf.show_versions()` and the planned `gm-cli doctor` before the other
  two implement it, so one dashboard or CI pipeline can consume all
  three. Tie to `ADR-0013` in `groundfield`.
- **`gi.cross_repo` namespace and `docs/cross-repo.md`** — one page
  naming the three packages and their data-flow contracts (`ρ(f)` fit
  handoff, `multilayer_soil_model` bridge, planned
  `Measurement → ImpedanceTable` exporter), which currently live in three
  separate `CLAUDE.md` files. Blocked on `ADR-0013`.
- **`gi.audit_apply(report_path)`** — read a Markdown audit report and
  insert its bullets into the matching section of `[Unreleased]`.
  Seventeen passes of hand-merging is the argument for it; the
  `docs/audit-log.md` split in 0.5.0 is the argument against, since the
  destination is no longer a single file.
- **`gi.docs.assert_api_pages_exist`** — walk `__all__` and assert every
  public symbol has at least one mkdocstrings `:::` directive under
  `docs/api/`. Companion to the `mkdocs build --strict` check.
- **`gi.connectors.dashboard_state`** — a serialisable state object so a
  future dashboard can resume a notebook session.
- **`docs/api/database.md` sub-section for `gi.show_versions()`** — the
  helper is in `__all__` but has no rendered docs page entry.

### From the 2026-09-08 review (audit pass 18)

Open findings of the full review, ordered by the sequence the review
proposes; the IDs refer to that review record, where each item carries its
reproduction. Everything here is confirmed, nothing is implemented.

**Before the next release.**

- **K1 — formula guard: NFKC-normalise every NAME token, then switch to
  a whitelist** of symbols and SymPy functions; the fullwidth variants of
  `eval`, `exec`, `open`, `breakpoint` become the regression test.
- **M1 — `NetworkDB.frequencies` from `PickleType` to a JSON column**,
  with an `add_columns` migration and a read fallback that never
  unpickles foreign bytes without a whitelist; document that `.db` and
  network JSON files are code-equivalent input.
- **M2 — `_add_missing_columns` renders `DEFAULT <value>`** for the
  eight NOT-NULL columns with Python-side defaults, reusing
  `_declared_defaults` from the rebuild path.
- **K2 — reachability instead of enumeration in
  `phase_current_mode="auto"`**; full path enumeration only for
  `"paths"`, with a cap and a warning; `docs/concepts.md:300` and
  `docs/api/pathfinder.md:28` restated as "linear in buses for the solve,
  exponential in cycles for the enumeration" with the measured table.
- **S2/S6/S7/S8 — `run_sweep` restores in every case**: validate names
  before mutating, bind `saved` before `_apply`, save `R/L/C`, one
  snapshot per bus, `set_active_fault(previous, keep_results=True)`.
- **S1 — FFT solver warns on non-zero mutual impedance** and
  `docs/transient.md:104-106` states that the FFT path has no mutual
  term; **S3** the FFT path applies `_sources_with_path_to_active_fault`;
  **S4** `f_eval` becomes an argument defaulting to the first frequency
  above 0 Hz, and `ω = 0` with a reactive `Z_src` is refused.
- **A5 — the inverse-rho searches drop `network.electrical_network` in
  `finally`**, and `bus_response` refuses a `Y` whose diagonal does not
  match the current bus impedances.
- **A2 — `BusResponse.evaluate` reports `Z_E = U_E/I_E`** or drops the
  `Z_G_Ohm` column; **A1** `r_epr` is filled only for `bus == fault_bus`
  (or recomputed per electrode from a third right-hand side).
- **M4 — `validate_assignment=True`** on `Source`, `Fault`, `Bus`,
  `Branch`, `Network`; **A9** `kappa ∈ (1, 2]` and `n ∈ (0, 1]` enforced
  in `_resolve_sc_inputs`, `i_th` via `thermal_equivalent_current`.
- **Docs D-2/D-3/D-7 and K6** — the transient example notebook runs
  again (and a CI job executes `docs/examples/*.ipynb`), the README drops
  `auto_parallel_coefficients`, `concepts.md:681` loses the RMS-row claim,
  and the three places still quoting `Z_G = u_EPR/(r·I_F)` follow the
  0.6.0 definition.

**Runtime (no architecture change).**

- **K3** keep the per-frequency `splu` object and reuse it for the
  no-mutual right-hand side. **K7** array variants of
  `check_passive_impedance`, `is_short_circuit`, `_is_open_circuit`.
  **L1** `lru_cache` on `validate_impedance_formula_value`. **FFT** a
  `compute_impedance_array()` raw path, a stacked `(K, n, n)` assembly
  solved in blocks, and a column-wise `ResultTransient.to_polars()`.
  **M12** persist `Path.segments` as branch names (or serialise the
  fingerprint). **A** reuse the assembled `Y` in `analyze_cuts`, one
  solve with two right-hand sides in `bus_response`, linear `extremes()`,
  build the `set(reported)` once in `_require_complete_results`.

**Correctness, second wave.**

- **A3/A4/A13** `_unit_injection` on the solver's `Y` (fixes the 0 Hz
  short circuit and the missing `Y_src`, and makes `identity_residual`
  meaningful). **A6** a coarse log grid before the bisection with a
  status flag on non-monotone EPR, the assumption named in the docstring.
  **A7/A8** `en50522_chain` checked against the ladder closed form,
  `ladder_potential_decay` assembled from the network's own impedances.
  **A10** the 0 Hz bin excluded from the `I_th` RMS and reported
  separately. **A11/A14/A15/A16** the complete-results guard in
  `analyze_cuts`, pinned schema in `CutAnalysis.to_polars()`, an `n_nan`
  column in `summarize` and NaN treated like null in `classify`,
  `sweep([])` and `worst_case_electrode` with `Re(Y_net) ≤ 0` rejected.
  **K4** every active element must cover `network.frequencies`. **K5**
  `I_fault` as `Σ scaling·I_src`. **S5** wrap-around self-test or
  internal zero-padding in the FFT solver; **S9/S17/S18/S19** `scalings`,
  the Nyquist bin, the non-causal constant-reactance kernel and the sign
  of the transient EPR listed under *Limitations*. **S10** passivity
  check for `R/L/C/M` fields. **S11** one `_time_grid` helper for both
  solvers. **S12** `on_error` for `run_outage_study`. **S15** remove the
  dead `_OUTAGE_BASELINE_STACK` and its comments.
- **M3** type merge in `save_network` warns on a differing formula and
  the loader checks stored impedances against the formula. **M5/M6**
  `ComplexNumber.__eq__` returns `NotImplemented`, `__hash__`, NumPy
  scalars via `numbers.Complex`. **M7** numeric `frequency_Hz` with an
  `is_rms` flag. **M8/M9** `ser_json_inf_nan` on every model, validated
  `Fault.scalings` values, an RFC-8259-safe encoding of the open-end
  sentinel. **M10** closed bus–bus switches, transformers and impedances
  counted and previewed by the pandapower importer; a
  `phase_impedance_formula` derived from `r/x_ohm_per_km`. **M11**
  `cwd=root` in `_run_git`. **M13** `scripts/` out of the wheel, the
  `release` entry point renamed or moved to the dev group. **M14**
  `I_inj_A` in `res_buses`. **M15** a real `DeprecationWarning`. **M16**
  `BusType`, `BranchType`, `Network`, `Bus`, `Branch`, `Fault`, `Source`,
  `Result`, `ComplexNumber` exported, `gi.Path` no longer `pathlib.Path`.
  **M17–M24** pinned `res_*` schemas, `try/finally` around the plot
  helpers, `ImportDefaults` validation, the dead SymPy import, a
  re-readable `ComplexNumber.__repr__`, tests before publish in
  `release.yml`, `mkdocs build --strict` on pull requests, CI matrix
  `["3.13", "3.14"]` once `python = "^3.13"` is released (the suite
  passes on 3.13 unchanged).
- **T1/T2** `--doctest-modules src/groundinsight` in `addopts` and the
  three broken examples repaired; a parametrised operator matrix for
  `ComplexNumber` in both operand orders including NumPy scalars, `None`
  and `inf`/`nan`; a shared `simple_network` fixture in `conftest.py`.

**Documentation** — the nine chapters and the Diátaxis navigation the review
proposes: `theory.md`, `input-data.md`,
`results.md` (with the EN 50522 touch-voltage comparison and the
liability note), `verification.md`, `faq.md`, `persistence.md`,
`performance.md`, `glossary.md`, and the project pages (changelog,
contributing, citation with BibTeX, license); `audit-log.md` moved to the
end of the navigation and excluded from search; `api/toplevel.md` with
`::: groundinsight` so the 23 unrendered `__all__` symbols get a page;
`api/index.md` listing all API pages; the `scalings` default of 1.0
documented; a "Thermal and short-circuit checks" example notebook.

### Explicit non-goals

- **PDE / FEM field computation**. The 3-D field solve (potential
  function φ(x, y, z, f), surface potentials, current distribution
  in the soil) is out of scope for `groundinsight`. It will live in
  a dedicated companion package `groundfield`, developed separately,
  with a one-way dependency on `groundinsight` for shared data types
  (`SoilModel`, `ComplexNumber`, `Network`). The bridge in both
  directions is the `ImpedanceTable` / `Network.impedance_matrix`
  surface listed under *Near term — bridge to `groundfield`*.
- **Mesh generation and 3-D visualisation** (gmsh, meshio, pyvista,
  VTK). Same reasoning — these belong in `groundfield`, not here,
  because they pull in heavy native dependencies that the
  reduced-model user of `groundinsight` should not be forced to
  install.

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

_No changes yet._

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

[Unreleased]: https://github.com/Ce1ectric/groundinsight/compare/v0.4.0...HEAD
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

### Near term — other

- **Finish `auto_parallel_coefficients` path** — implement
  `ElectricalNetwork._auto_assign_parallel_coefficients` (currently a
  stub) so the phase-only pre-solve actually writes derived
  `parallel_coefficient` values back onto branches. Covered in part by
  the Variant B sketch added in 0.3.0.
- **Dependabot** for Python and GitHub Actions dependencies
  (`.github/dependabot.yml`).
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

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

[Unreleased]: https://github.com/Ce1ectric/groundinsight/compare/v0.3.0...HEAD
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

- **Logging migration**: the user-facing messages in
  `network_operations.py` and `electrical_network.py` currently use
  `print`. Moving to the standard `logging` module gives callers a
  way to silence output and makes the library usable inside notebooks
  without stdout noise. Becomes more pressing once parameter sweeps
  run at AP 1 scale.
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

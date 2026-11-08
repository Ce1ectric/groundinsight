# API reference

The API reference is generated directly from the docstrings in the
`groundinsight` source tree via
[`mkdocstrings`](https://mkdocstrings.github.io/). Each page covers one area of
the package: a short explanation, examples, and the rendered docstrings of
the public functions and classes. [Top-level package](toplevel.md) lists every
public name with its page.

## Modules

- [Top-level package](toplevel.md) — every public name of `groundinsight` and
  of the service layer `groundinsight.api`, with the page that documents it;
  `set_log_level`, `show_versions` and the warning classes.
- [Network operations](network_operations.md) — factory functions that build a
  network (`create_network`, `create_bus`, `create_branch`, `create_source`,
  `create_fault`, …) and `run_fault`.
- [Service functions](service.md) — `solve` (a fault solve that leaves the
  network untouched), `check_network` (every problem as structured
  diagnostics, never an exception), progress and cancellation, the canonical
  parameter names with their deprecated aliases; the service layer
  `groundinsight.api` for front ends (project sessions, undoable edit
  commands, jobs, JSON-safe DTOs) and the `groundinsight` command line.
- [Core models](core_models.md) — the Pydantic classes `Bus`, `Branch`,
  `Source`, `Fault`, `Network`, the types, the `Result*` containers and
  `ComplexNumber`; editing a network and stale results.
- [Electrical network](electrical_network.md) — the numerical core: nodal
  admittance matrices, sparse solve, reduction factors and earthing impedance.
- [Pathfinder](pathfinder.md) — enumeration of the simple paths between a
  source and the fault, used by the legacy phase-current mode, and its
  per-network settings.
- [Plotting](plotting.md) — Matplotlib helpers for earth potential rise,
  branch and bus currents, stationary and transient.
- [Database](database.md) — `gi.Database` and the module-level persistence
  helpers for SQLite, and the migration of files written by earlier releases.
- [I/O](io.md) — `ImportDefaults`, the pandapower topology import
  (`from_pandapower`, `preview_pandapower_import`), the short-circuit import
  (`read_shortcircuit_results`, `apply_shortcircuit_characteristics`),
  networks as tables (`network_from_tables`, `Network.to_tables`) and the JSON
  project file.
- [Outage studies](outage.md) — what-if studies on the `active` flag
  (`Outage`, `outage_context`, `run_outage_study`).
- [Parameter sweeps](sweep.md) — `run_sweep`, `SweepPoint`, `rho_f_points`.
- [Analysis](analysis.md) — the inverse rho and rho-f searches, the
  short-circuit characteristics and the thermal checks of conductors and
  nodes.
- [Decomposition](decomposition.md) — the network split at the fault location
  (`Cut`, `analyze_cuts`).
- [Location response](response.md) — a station characterised without its own
  electrode (`bus_response`).
- [Reference cases](reference.md) — the closed-form cases the solver is
  checked against (`run_reference_cases`).
- [Statistics](statistics.md) — `summarize` and `classify` on long-format
  frames.
- [Transient simulations](transient.md) — `TransientStudy`, `ResultTransient`
  and the waveform library `gi.waveforms`.

The columns of every result frame are listed in
[Result tables](../reference/results.md), every diagnostic code in
[Warnings and diagnostics](../reference/warnings.md), and the tasks these
functions serve in the [how-to guides](../howto/harmonics.md).

## Top-level package

`import groundinsight as gi` gives every public name; the list, grouped by
topic and linked to the page of each name, is on
[Top-level package](toplevel.md).

## Logging

`gi.set_log_level("INFO")` enables a console handler on the
`groundinsight` package logger. The helper is *handler-idempotent*:
repeated calls with alternating levels do not stack up duplicate
handlers. If you have separately called `logging.basicConfig()` at
the root logger and observe duplicate output, set
`logging.getLogger("groundinsight").propagate = False` after
`set_log_level`. What qualifies a result is not logged but raised as a
diagnostic; see [Diagnostics, logging and warnings](../howto/diagnostics.md).

## Internal API

Some functions and methods are shared between modules of the package but are
not meant for application code: the matrix helpers of `ElectricalNetwork`
that the response and decomposition analyses reuse (`resolved_impedance`,
`grounding_shunts`, `earthed_buses`, `remove_loop_closures`,
`compute_phase_currents`), the frequency rules that `gi.check_network`
applies again (`validate_frequencies`), the bookkeeping of a network
(`Fault.mark_active`, `Network.needs_path_rebuild`, `Network.input_digest`,
`Network.path_cache`) and a few helpers of the importers and of the
impedance checks. They carry no leading underscore, because another module of
the package uses them, and their docstring starts its second paragraph with
"Internal API". They are not exported from `groundinsight` and can change in
any release without a deprecation period; application code uses the public
functions they serve.

Underscore names stay inside the module that defines them. A test scans the
package and fails when one module uses an underscore name of another.

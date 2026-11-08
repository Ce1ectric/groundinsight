# API reference

The API reference is generated directly from the docstrings in the
`groundinsight` source tree via
[`mkdocstrings`](https://mkdocstrings.github.io/). Every page below corresponds
to one Python module; navigate through the pages to see the public functions,
classes and their signatures.

## Modules

- [Network operations](network_operations.md) — high-level factory functions
  for creating networks, buses, branches, sources, faults, paths, and for
  running fault calculations.
- [Service functions](service.md) — calls for code that holds a network on
  behalf of someone else: `solve` (a fault solve that leaves the network
  untouched), `check_network` (every problem as structured diagnostics,
  never an exception) and the canonical parameter names with their
  deprecated aliases.
- [Core models](core_models.md) — Pydantic data classes for `Bus`, `Branch`,
  `Source`, `Fault`, `Network`, the different `Result*` containers and the
  `ComplexNumber` helper.
- [Electrical network](electrical_network.md) — numerical core: assembly of
  the admittance matrices, sparse LU solve, reduction-factor and grounding-
  impedance computation.
- [Pathfinder](pathfinder.md) — DFS-based enumeration of every simple path
  from each source bus to the active fault bus.
- [Plotting](plotting.md) — Matplotlib bar-plot helpers for EPR, branch
  currents and bus currents (both stationary bar plots and transient time
  series).
- [Database](database.md) — SQLAlchemy CRUD helpers for persisting bus types,
  branch types and entire networks to SQLite.
- [I/O — network importers](io.md) — `ImportDefaults` schema, the
  pandapower topology importer (`from_pandapower`,
  `preview_pandapower_import`) and the short-circuit result import
  (`read_shortcircuit_results`,
  `apply_shortcircuit_characteristics`), and networks as tables
  (`network_from_tables`, `Network.to_tables`) for spreadsheets and CSV.
- [Outage studies](outage.md) — what-if API on top of the `active` flag
  (`Outage`, `outage_context`, `run_outage_study`).
- [Analysis](analysis.md) — higher-level routines on top of `run_fault`:
  the inverse rho / rho-f scaling helpers (`find_max_rho_scaling`,
  `find_max_rho_f_scaling`, `evaluate_max_epr_under_k`,
  `select_rho_f_from_catalog`) and the conductor thermal-limit check
  (`check_conductor_limits`, `resolve_fault_sc_characteristics` and the
  IEC 60909 / 60949 primitives behind them).
- [Transient simulations](transient.md) — time-domain solver paths
  (`TransientStudy`, `ResultTransient`) plus the
  `groundinsight.simulation.waveforms` library for source signals.

## Top-level package

The `groundinsight` top-level module re-exports the most commonly used
symbols so that everyday work only requires `import groundinsight as gi`:

```python
import groundinsight as gi

net = gi.create_network(name="...", frequencies=[50])
gi.create_bus(...)
gi.create_branch(...)
gi.create_fault(...)
gi.create_source(...)
gi.run_fault(network=net, fault="...")
```

Top-level helpers and persistence factories (`gi.set_log_level`,
`gi.Database`, `gi.start_dbsession`, `gi.close_dbsession`, `gi.save_*_to_db`,
`gi.load_*_from_db`, `gi.save_network_to_json`,
`gi.load_network_from_json`) are listed in `groundinsight.__all__` as
of 0.5 and are therefore visible to `from groundinsight import *`
and to type-checkers.

### Logging

`gi.set_log_level("INFO")` enables a console handler on the
`groundinsight` package logger. The helper is *handler-idempotent*:
repeated calls with alternating levels do not stack up duplicate
handlers. If you have separately called `logging.basicConfig()` at
the root logger and observe duplicate output, set
`logging.getLogger("groundinsight").propagate = False` after
`set_log_level`.

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

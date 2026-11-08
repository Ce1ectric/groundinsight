# Top-level package

`import groundinsight as gi` is all everyday work needs: the package re-exports
every public function, class and constant at the top level. This page lists
each of them — the names of `groundinsight.__all__` and those of the service
layer `groundinsight.api` — with the API page that documents it, and renders
the few that have no page of their own [below](#rendered-here).

```python
import groundinsight as gi

net = gi.create_network(name="example", frequencies=[50.0])
print(len(gi.__all__) > 100, gi.__version__ == gi.show_versions()["groundinsight"])  # True True
```

`from groundinsight import *` imports exactly these names. Everything else —
module internals, the ORM classes of the database, the helpers named in the
[internal API](index.md#internal-api) — can change in any release; the names
below follow the deprecation policy (a renamed or removed name keeps working
with a `DeprecationWarning` for at least two releases and six months).

## `groundinsight`

| Topic | Names | Page |
|---|---|---|
| Build and solve | `create_network`, `create_bus`, `create_branch`, `create_source`, `create_voltage_source`, `create_fault`, `create_network_assistant`, `set_active_fault`, `run_fault`, `build_electrical_network`, `create_paths` | [Network operations](network_operations.md) |
| Solve without side effects, check, diagnostics | `solve`, `check_network`, `Diagnostic`, `GroundinsightWarning`, `ModelAssumptionWarning`, `InputWarning`, `StudyCancelled` | [Service functions](service.md) |
| Model classes | `Network`, `Bus`, `BusType`, `Branch`, `BranchType`, `Source`, `Fault`, `Path`, `Result`, `ResultBus`, `ResultBranch`, `ComplexNumber`, `RES_FAULT_SCHEMA` | [Core models](core_models.md) |
| Rho searches, short-circuit and thermal checks | `find_max_rho_scaling`, `find_max_rho_f_scaling`, `evaluate_max_epr_under_k`, `select_rho_f_from_catalog`, `check_conductor_limits`, `check_node_limits`, `resolve_fault_sc_characteristics`, `FaultShortCircuitData`, `admissible_short_circuit_current`, `final_temperature`, `iec60909_m`, `iec60949_k`, `kappa_from_r_to_x`, `peak_short_circuit_current`, `thermal_equivalent_current`, `FINAL_TEMPERATURES`, `CABLE_INSULATION_LIMITS`, `IEC60949_MATERIALS` | [Analysis](analysis.md) |
| Decomposition at the fault | `Cut`, `CutAnalysis`, `analyze_cuts` | [Decomposition](decomposition.md) |
| Location response | `BusResponse`, `bus_response` | [Location response](response.md) |
| Reference cases | `ReferenceCase`, `REFERENCE_CASES`, `run_reference_cases` | [Reference cases](reference.md) |
| Parameter sweeps | `SweepPoint`, `SweepResult`, `rho_f_points`, `run_sweep` | [Parameter sweeps](sweep.md) |
| Statistics | `summarize`, `classify` | [Statistics](statistics.md) |
| Outage studies | `Outage`, `OutageStudyResult`, `outage_context`, `run_outage_study` | [Outage studies](outage.md) |
| Transient studies | `TransientStudy`, `ResultTransient`, `waveforms` | [Transient simulations](transient.md) |
| Plots | `plot_bus_voltages`, `plot_branch_currents`, `plot_bus_currents`, `plot_epr_transient`, `plot_branch_current_transient` | [Plotting](plotting.md) |
| Importers, tables, project files | `ImportDefaults`, `from_pandapower`, `preview_pandapower_import`, `read_shortcircuit_results`, `apply_shortcircuit_characteristics`, `network_from_tables`, `save_network_to_json`, `load_network_from_json` | [I/O](io.md) |
| Database | `Database`, `start_dbsession`, `close_dbsession`, `save_bustype_to_db`, `load_bustypes_from_db`, `save_branchtype_to_db`, `load_branchtypes_from_db`, `save_network_to_db`, `load_network_from_db`, `needs_migration`, `migrate_database`, `MigrationReport` | [Database](database.md) |
| Path search | `PathfinderSettings`, `clear_pathfinder_cache`, `get_pathfinder_cache_size`, `set_pathfinder_cache_size` | [Pathfinder](pathfinder.md) |
| Logging, versions, warning classes | `set_log_level`, `show_versions`, `NetworkFrequencyOrderWarning`, `TransientModelWarning`, `DCLimitWarning` | [below](#rendered-here) |

## `groundinsight.api`

The service layer for front ends is a namespace of its own and is not
re-exported at the top level: `from groundinsight.api import ProjectSession`.
How to use it: [Service layer and command line](../howto/service-layer.md).

| Topic | Names | Page |
|---|---|---|
| Sessions and stores | `ProjectSession`, `ProjectError`, `ProjectStore`, `MemoryStore`, `DatabaseStore`, `EXAMPLES` | [Service functions](service.md#groundinsightapi) |
| Commands | `Command`, `CommandError`, `COMMAND_OPS`, `parse_command`, `AddBus`, `UpdateBus`, `RemoveBus`, `AddBranch`, `UpdateBranch`, `RemoveBranch`, `AddSource`, `UpdateSource`, `RemoveSource`, `AddFault`, `UpdateFault`, `RemoveFault`, `AddBusType`, `UpdateBusType`, `RemoveBusType`, `AddBranchType`, `UpdateBranchType`, `RemoveBranchType`, `SetFrequencies` | [Service functions](service.md#groundinsightapi) |
| Jobs | `JobRunner`, `JobHandle`, `JobCancelled`, `JobTimeout`, `JobFailed`, `snapshot_network`, `parse_job_request` | [Service functions](service.md#groundinsightapi) |
| DTOs and schemas | `DTO_SCHEMA_VERSION`, `json_schemas`, `finite_or_none`, `ElementRefDTO`, `DiagnosticDTO`, `CheckReportDTO`, `ColumnDTO`, `TableDTO`, `NetworkTablesDTO`, `SessionLimitsDTO`, `NetworkSummaryDTO`, `CommandResultDTO`, `KeyFigureRowDTO`, `KeyFiguresDTO`, `FaultResultDTO`, `FaultResultsDTO`, `StudyResultDTO`, `ProgressDTO`, `JobErrorDTO`, `JobStatusDTO`, `ComplexDTO`, `SolveRequestDTO`, `SolveAllRequestDTO`, `SweepPointDTO`, `SweepRequestDTO`, `OutageScenarioDTO`, `OutageStudyRequestDTO`, `MaxRhoRequestDTO` | [Service functions](service.md#groundinsightapi) |

## Rendered here

::: groundinsight
    options:
      members:
        - set_log_level
        - show_versions
        - NetworkFrequencyOrderWarning
        - TransientModelWarning
        - DCLimitWarning
      show_root_heading: false
      show_root_toc_entry: false
      show_root_full_path: true
      heading_level: 3

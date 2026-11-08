# Getting started

Three pages take you from an empty Python environment to a solved ring.
Each states the numbers you should get, computes them a second way by hand,
and the test suite checks both on every change.

1. [Installation](installation.md) — a virtual environment, the package,
   Jupyter or VS Code, and a self-test that takes a second.
2. [Your first fault calculation](first-calculation.md) — two stations and a
   cable at 50 Hz: earth potential rise, earthing impedance, both reduction
   factors.
3. [From a line to a ring](ring.md) — the same cable as a ring: how the fault
   current divides, the phase impedance, a harmonic.

## Tasks after the tutorials

| Task | Described in |
|---|---|
| Derive bus and branch types from cable and electrode data | [Bus and branch types from data sheets](../howto/types-from-datasheets.md) |
| Compare the results with the limits of EN 50522 | [Compare results with EN 50522 limits](../howto/en50522-check.md) |
| Several frequencies, fault scalings, direct current | [Frequencies, source values and fault scalings](../reference/conventions.md#frequencies-source-values-and-fault-scalings), [Direct current](../explanation/dc.md) <!-- howto: howto/harmonics.md --> |
| Vary soil resistivity or electrodes over many cases | [Parameter sweeps](../api/sweep.md), [Statistics and classification](../api/statistics.md) <!-- howto: howto/sweeps.md --> |
| Take branches or stations out of service | [Outage studies](../api/outage.md#example) <!-- howto: howto/outage.md --> |
| Find the largest admissible soil resistivity | [Analysis](../api/analysis.md#example) <!-- howto: howto/inverse-rho.md --> |
| Import a network and its short-circuit results from pandapower | [I/O — network importers](../api/io.md#example) <!-- howto: howto/pandapower.md --> |
| Check earthing conductors and electrodes thermally | [Conductor thermal limits](../api/analysis.md#conductor-thermal-limits-iec-60949-iec-60909-0) <!-- howto: howto/thermal.md --> |
| Study the fault current in time: DC offset, switching | [Transient simulation](../transient.md), [API](../api/transient.md#example) <!-- howto: howto/transient.md --> |
| Save, load and migrate networks and type libraries | [Project files (JSON)](../api/io.md#project-files-json), [Database](../api/database.md#the-database-object) <!-- howto: howto/persistence.md --> |
| Large and meshed networks | [Enumerated paths and the legacy mode](../explanation/phase-currents.md#path-finding) <!-- howto: howto/performance.md --> |
| Warnings, diagnostics and logging | [Diagnostic codes](../api/service.md#diagnostic-codes), [Logging](../api/index.md#logging) <!-- howto: howto/diagnostics.md --> |

## Where the sections of the former Quickstart went

These pages replace the single page *Quickstart*. Its address forwards here,
and each of its section links lands on the matching row below.

| Section of *Quickstart* | Now |
|---|---|
| [1. Import and create a network](first-calculation.md#step-1-a-network-at-50-hz){ #1-import-and-create-a-network } | Your first fault calculation, step 1 |
| [2. Define bus and branch types](first-calculation.md#step-2-types){ #2-define-bus-and-branch-types } | Your first fault calculation, step 2 |
| [3. Add buses and branches](first-calculation.md#step-3-buses-and-the-cable){ #3-add-buses-and-branches } | Your first fault calculation, step 3 |
| [4. Add the source and the fault](first-calculation.md#step-4-the-source-and-the-fault){ #4-add-the-source-and-the-fault } | Your first fault calculation, step 4 |
| [5. Create paths and solve](first-calculation.md#step-5-solve){ #5-create-paths-and-solve } | Your first fault calculation, step 5 — no paths needed |
| [6. Inspect the results](first-calculation.md#step-6-read-the-results){ #6-inspect-the-results } | Your first fault calculation, step 6 |
| [7. Plot](first-calculation.md#step-8-plot-and-save){ #7-plot } | Your first fault calculation, step 8; [Figure ownership](../api/plotting.md#figure-ownership) |
| [8. Save and load](first-calculation.md#step-8-plot-and-save){ #8-save-and-load } | Your first fault calculation, step 8; [Project files (JSON)](../api/io.md#project-files-json) |
| [9. Optional: outage / what-if studies](../api/outage.md#example){ #9-optional-outage-what-if-studies } | Outage studies |
| [10. Optional: inverse rho analysis](../api/analysis.md#example){ #10-optional-inverse-rho-analysis } | Analysis |
| [11. Optional: import from pandapower](../api/io.md#example){ #11-optional-import-from-pandapower } | I/O — network importers |
| [12. Optional: transient simulation](../transient.md){ #12-optional-transient-simulation } | Transient simulation |
| [13. Logging and silencing output](../api/index.md#logging){ #13-logging-and-silencing-output } | API overview, logging |

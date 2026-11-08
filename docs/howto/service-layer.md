# Service layer and command line

A notebook works with a `Network` directly. A front end — a GUI, a server, a
batch tool — needs more: a project it can open and save, edits it can undo,
long calculations off its event loop, and data it can send as JSON. The
service layer `groundinsight.api` provides exactly that, without any physics
of its own, and the command line `groundinsight` is built on it. This guide
walks through a session, its commands and jobs, the JSON shapes and the
command line. The script is [`service_layer.py`](../snippets/service_layer.py);
its output is at the [end of the page](#output). The design rules of the
layer and its full API are on the [service page](../api/service.md#service-layer-for-front-ends-groundinsightapi).

## A session holds one project

`ProjectSession` is one open project: `ProjectSession.from_example(name)` (the
documented networks `quickstart`, `minimal`, `mv_ring`),
`ProjectSession.from_file(path)` for a project file, or
`ProjectSession.from_store(store, key)` for a server that keeps projects in a
`MemoryStore` or a `DatabaseStore`. The constructor takes the resource limits
a server sets per user — `max_buses`, `max_branches` (a larger file is refused
on opening), `max_job_runtime_s`, `max_undo` and `max_paths`.

A front end reads the project only through data-transfer objects (DTOs):
`summary()` (`NetworkSummaryDTO`), `tables()` (the element tables of
`Network.to_tables`), `check()` (the pre-flight check) and
`solve(fault)` / `result(fault)` (`FaultResultDTO`: the key figures of
`Network.res_fault`, the rows of `res_buses` and `res_branches`, the
diagnostics as `DiagnosticDTO`, the revision it was computed from and whether
it is stale).

```python
--8<-- "docs/snippets/service_layer.py:session"
```

## Every edit is a command

A front end changes the project only through `session.apply(command)`. The
commands — `AddBus`, `UpdateBus`, `RemoveBus` and their counterparts for
branches, sources, faults and the two type catalogues, plus `SetFrequencies` —
call the library's editing API, so the library validates the input and
derives the impedances. A command is atomic: a rejected one raises
`CommandError` and changes nothing, not even the revision. `session.undo()`
and `session.redo()` restore the network exactly; `revision` identifies an
input state, and a result knows whether it is stale for the current one — the
library's verdict (`Network.is_result_stale`), so undoing an edit makes the
result current again. `parse_command({"op": "update_bus", ...})` builds a
command from JSON.

```python
--8<-- "docs/snippets/service_layer.py:commands"
```

## Jobs

Anything that can take longer than a moment runs as a job:
`session.submit_solve(fault)` or `session.submit(request)` with a
`SolveRequestDTO`, `SolveAllRequestDTO`, `SweepRequestDTO`,
`OutageStudyRequestDTO` or `MaxRhoRequestDTO` returns a handle at once. The
job runs on a snapshot of the network taken at submission, so editing can go
on. `handle.status()` reports state and progress, `handle.cancel()` stops the
job at the next unit of work, and `handle.result(timeout=...)` returns the DTO
or raises `JobCancelled`, `JobTimeout` or `JobFailed`. The `JobRunner` of a
session runs jobs in a thread by default; `JobRunner(backend="process")` runs
them in a separate process, which costs the interpreter start but can stop a
job hard and runs truly in parallel.

```python
--8<-- "docs/snippets/service_layer.py:jobs"
```

## JSON in, JSON out

Every DTO is a pydantic model with `dto_schema_version`; `model_dump_json()`
is its wire format and never contains `NaN` or `Infinity` (a non-finite number
is `null`). `json_schemas()` returns the JSON schema of every DTO, of all
commands and of all job requests — the request and response schemas of a REST
API. `session.save(path)` writes the library's project file (UTF-8, atomic,
with `schema_version`) without stored results, so a file saved by a front end
opens in a notebook and the other way round.

```python
--8<-- "docs/snippets/service_layer.py:wire"
```

## The command line

Installing groundinsight installs the console script `groundinsight`
(`python -m groundinsight` is the same):

```bash
groundinsight run projekt.json                           # the only fault of the project
groundinsight run projekt.json --fault F1 --format json  # the FaultResultDTO
groundinsight run projekt.json --format csv              # the rows of res_fault
groundinsight check projekt.json                         # the pre-flight check
```

`groundinsight run` opens the file in a session, runs the pre-flight check for
the fault, solves it and prints the key figures per frequency and as RMS with
the diagnostics (`--format table`, the default), the `FaultResultDTO`
(`json`) or the key-figure rows with the columns of `RES_FAULT_SCHEMA`
(`csv`). `--phase-current-mode paths` selects the legacy phase-current mode.
`groundinsight check` prints every finding of `check_network`. The exit code
is 0 on success, 1 when the check or the result reports an error, and 2 for a
usage error (an unknown fault, a file that is not a project, `run` without
`--fault` on a project with several faults).

For the project saved by the script, `groundinsight run projekt.json` prints
after a two-line header (version, network, fault and input digest):

```text title="Output of groundinsight run projekt.json"
f [Hz]  I_F [A]  EPR fault [V]  EPR max [V]      at bus    r_epr  r_current  I_E [A]  U_E [V]  Z_E [Ohm]
------  -------  -------------  -----------  ----------  -------  ---------  -------  -------  ---------
    50     1000          275.4        275.4   bus_fault   0.3846     0.2741    274.1    275.4      0.844
   250      200          17.12        17.12   bus_fault  0.08305    0.07656    15.31    17.12      1.073
   350      100          6.741        6.741  bus_source  0.05942    0.05523    5.523    6.741      1.177
   450       50          2.901        2.901   bus_fault  0.04625    0.04313    2.156    2.901      1.299
   550       25          1.314        1.314   bus_fault  0.03785    0.03536    0.884    1.314      1.437
   RMS     1026          276.1        276.1   bus_fault        -          -    274.6    276.1          -
```

followed by the diagnostic `SCALINGS_INCOMPLETE`: the quickstart example gives
its fault a scaling at 50 Hz only, so the harmonics are scaled with 1.0
([Harmonics, fault scalings and DC](harmonics.md#fault-scalings)).
`r_epr` is the EPR-based reduction factor, `r_current` the reduction factor of
EN 50522 ([Result tables](../reference/results.md#networkres_faultfault)).

## The GUI

The separate distribution `groundinsight-gui` is a desktop and browser front
end built with NiceGUI on this service layer; it is released together with the
library and carries the same version. Its source is in the `gui/` directory of
the [repository](https://github.com/Ce1ectric/groundinsight).

```bash
pip install groundinsight-gui        # browser mode
python -m groundinsight_gui          # opens http://127.0.0.1:8080
```

## Output

```text title="Output of service_layer.py"
project 'QuickstartNet': 2 buses, 1 branch, faults ['fault_at_remote_bus'], frequencies [50.0, 250.0, 350.0, 450.0, 550.0]
RMS: I_F = 1026.2 A, EPR at the fault = 276.1 V, I_E = 274.6 A, U_E = 276.1 V
diagnostics: ['SCALINGS_INCOMPLETE']
after the edit: revision 1, stale True; after undo: revision 0, stale False
job: done, same figures: True
wire format: FaultResultDTO as JSON, 27 schemas
```

The test suite compares the key figures of the session with `res_fault` of a
plain `run_fault` on the same example network, and runs the command line on
the saved file.

# Service functions

Functions for code that holds a network on behalf of someone else — a GUI
session, a server answering requests, a study that has to hand the network
back unchanged. They answer the questions such a caller asks before and
during a calculation without the side effects of the interactive API:

| Question | Function |
|---|---|
| What would `run_fault` return — without touching my network? | [`gi.solve`](#solve-without-side-effects) |
| What is wrong with this network, all at once, as data? | [`gi.check_network`](#pre-flight-check) |
| How far is this study, and can I stop it? | [`progress` / `should_cancel`](#progress-and-cancellation) |
| How do I get a network from a spreadsheet, and back? | [`gi.network_from_tables` / `Network.to_tables`](io.md#networks-as-tables) |
| Which spelling of a parameter is the current one? | [canonical names](#canonical-parameter-names) |
| Which conditions qualify a result, by code? | [diagnostic codes](#diagnostic-codes) |

## Solve without side effects

`gi.run_fault(net, fault)` activates the fault, may drop or rebuild
`net.paths`, attaches the assembled electrical network and stores the
result in `net.results` — convenient in a notebook, where `net.res_buses()`
follows. `gi.solve(net, fault)` runs the same pipeline on an *overlay* that
shares the bus, branch and source objects of `net` (a solve only reads them)
and owns everything a solve writes. The numbers are therefore bit-identical
to `run_fault`, and `net` is the same object afterwards: same JSON, same
stored results, same active fault, same paths, same assembled system — also
when the solve raises.

| | `run_fault` | `solve` |
|---|---|---|
| returns the `Result` | yes | yes (a new object) |
| `net.results[fault]` | replaced | unchanged |
| `net.active_fault` | set to `fault` | unchanged |
| `net.paths` | may be dropped or rebuilt | unchanged |
| `net.electrical_network` | replaced (read by `bus_response`, `analyze_cuts`) | unchanged |
| `Result.diagnostics` | yes | yes, identical |
| `Result.input_digest` | stamped | stamped, identical |

```python
import groundinsight as gi

result = gi.solve(net, "F1")              # net is left as it was
epr = {bus.name: bus.uepr for bus in result.buses}

net.results["F1"] = result                # keep it after all, if wanted
net.res_buses(fault="F1")
net.stale_results()                       # [] -- current until the next edit
```

Do not modify the network while a solve runs, and do not solve the *same*
`Network` object from several threads at once: the solve shares the
module-level path caches of `groundinsight.pathfinder`, which are not
synchronised. Reading the network from another thread meanwhile is safe.

## Pre-flight check

`gi.check_network(net)` returns every problem of a network as a list of
[`Diagnostic`][groundinsight.diagnostics.Diagnostic] records — the same type
as `Result.diagnostics` — and never raises. Each record has a stable `code`,
the `elements` it is about as `(kind, name)` pairs, and a `severity`:

- **`error`** — `run_fault` would raise, or would return an answer that does
  not describe the network: a branch whose end names an unknown bus, or a
  source on such a bus, is dropped by the solver without a word and the
  result reads 0 V. The editing API refuses such a reference, but a network
  loaded from a hand-edited file or filled in by a script can carry one.
- **`warning`** — the network solves, but part of the input has no effect or
  a documented default stands in for a missing value.

```python
issues = gi.check_network(net)
blocking = [d for d in issues if d.severity == "error"]
for d in issues:
    print(d.severity, d.code, d.elements, d.message)

gi.check_network(net, fault="F1")         # fault checks for F1 only
```

The check is structural — no assembly, no solve — so an editor can run it
after every change. Where the solver already validates something, the check
calls that code and converts its exception or warning, so the two cannot
disagree. Conditions only a solve can detect (a heuristic phase-current
split, for example) arrive with the result in `Result.diagnostics`. Package
warnings raised during the check keep their own codes (`FREQUENCY_ORDER`,
`DC_LIMIT`).

| Code | Severity | Meaning |
|---|---|---|
| `NOT_A_NETWORK` | error | The argument is not a `Network`. |
| `CHECK_FAILED` | error | A check raised on an unforeseen malformation; the others still ran. |
| `ELEMENT_INVALID` | error | An entry of `buses`/`branches`/`sources`/`faults` is not an element of the expected type. |
| `FREQUENCIES_INVALID` | error | Empty frequency list, duplicates, non-finite or negative values (in-place edits bypass the validator). |
| `NO_ACTIVE_BUS` | error | No active bus. |
| `NO_SOURCE` | error | No source. |
| `NO_FAULT` | error | No fault. |
| `FAULT_UNKNOWN` | error | The `fault=` argument names no fault of the network. |
| `BRANCH_UNKNOWN_BUS` | error | A branch end names an unknown bus; the solver would drop the branch. |
| `SOURCE_UNKNOWN_BUS` | error | A source names an unknown bus; the solver would ignore it. |
| `FAULT_UNKNOWN_BUS` | error | A fault names an unknown bus. |
| `FAULT_BUS_INACTIVE` | error | A fault sits on an inactive bus. |
| `SOURCE_BUS_INACTIVE` | warning | A source sits on an inactive bus and injects nothing. |
| `IMPEDANCE_MISSING` | error | An element of the nodal system has no impedance at a network frequency. |
| `FORMULA_ERROR` | error | No stored impedance, and the type formula cannot be evaluated. |
| `IMPEDANCE_NOT_PASSIVE` | error | An inverted impedance is zero, too small to invert or has a negative real part. |
| `IMPEDANCE_NOT_FINITE` | error | An impedance is NaN at a network frequency. |
| `SOURCE_FREQUENCY_NONE` | error | A source has a value at none of the network frequencies. |
| `SOURCE_FREQUENCY_GAP` | warning | A source has no value at some network frequencies. |
| `SOURCE_FREQUENCY_OUTSIDE_NETWORK` | warning | A source has values at frequencies the network does not solve. |
| `SOURCE_VALUE_NOT_FINITE` | error | A source value is NaN or infinite. |
| `SCALINGS_INCOMPLETE` | warning | A fault's scalings miss network frequencies (scaled with 1.0). |
| `FAULT_NOT_REACHED` | warning | No source reaches the fault over active elements; every potential is 0 V. |
| `ISOLATED_BUS` | warning | An active bus has no active branch to any other bus. |
| `FLOATING_ISLAND` | error | Buses joined by grounding conductors with no reference to earth. |

## Progress and cancellation

The five long-running entry points — `run_sweep`, `run_outage_study`,
`find_max_rho_scaling`, `find_max_rho_f_scaling` and `TransientStudy.solve` —
take two optional keyword arguments:

- `progress(done, total, label)` is called after every unit of work: a sweep
  point, an outage scenario (the base case included), one solve of a
  bisection search (one bus solve of the rho-f search), a batch of FFT bins
  (at most 512, fewer under a tight `fft_block_bytes`), a stage of the
  state-space solver. `done` counts up by one, `label` names the unit,
  `done <= total` holds at every call.
- `should_cancel()` is asked before every unit. A true answer raises
  `gi.StudyCancelled` (a `RuntimeError` with `.done` and `.total`), and the
  network is back in the state it had when the study was called — JSON,
  stored results, active fault, paths and assembled system.

```python
import threading

stop = threading.Event()

def report(done, total, label):
    print(f"{done}/{total} {label}")      # a GUI forwards this to its event loop

try:
    study = gi.run_sweep(net, fault="F1", points=points,
                         progress=report, should_cancel=stop.is_set)
except gi.StudyCancelled as cancelled:
    print(f"stopped after {cancelled.done} of {cancelled.total} points")
```

Cancellation is cooperative: it takes effect at the next unit boundary. The
integration step of the state-space solver is a single library call and runs
to its end once started. Both callbacks run in the study's thread; an
exception raised by a callback propagates unchanged.

`total` is fixed for sweeps, outage studies and transients. A bisection
search announces the evaluations its bracket and tolerance require — a
log-bisection needs the same number of halvings wherever the threshold lies —
and ends below that when it stops early (a fully admissible bracket).

## Diagnostic codes

Every condition that changes how a result has to be read is raised as a
structured warning — a subclass of `gi.GroundinsightWarning` with a stable
`code`, a `severity` and the `elements` it is about — and not only logged:
the library's logging default is a `NullHandler`, so a log line reaches
nobody who has not configured logging. Inside `run_fault` (and `gi.solve`)
the records are collected into `Result.diagnostics`; around any other call
a program collects them with `groundinsight.diagnostics.collecting()`:

```python
from groundinsight.diagnostics import collecting

with collecting() as records:
    network = gi.from_pandapower(pp_net, defaults=defaults, voltage_level_kV=20.0)
for record in records:
    print(record.code, record.elements, record.message)
```

A program that reads warnings from its log can route them there with
`logging.captureWarnings(True)`: they arrive at the logger `py.warnings` as
formatted text, while code and elements stay with the records.

What stays a log line reports what the library does — sessions, migrations,
caches, costs — or summarises something the returned object already carries
(a `status` field, a `failures` map, a table column). The codes, with the
category and the function whose docstring describes the condition:

| Code | Category | Raised by |
|---|---|---|
| `DC_LIMIT` | `DCLimitWarning` | impedance evaluation at 0 Hz |
| `EARTH_GROUP_SPREAD` | `ModelAssumptionWarning` | `run_fault`: electrode currents spread in angle, `I_E` and `r_current` describe the dominant direction only |
| `EARTH_GROUP_UNDEFINED` | `ModelAssumptionWarning` | `run_fault`: no electrode carries a non-noise share of the fault current |
| `EARTHING_IMPEDANCE_UNDEFINED` | `ModelAssumptionWarning` | `run_fault`: `Z_E` undefined at a frequency |
| `FREQUENCY_ORDER` | `NetworkFrequencyOrderWarning` | `Network.frequencies` not strictly increasing |
| `IMPORT_BUS_SKIPPED` | `InputWarning` | `from_pandapower` / preview: a bus with an unparsable `vn_kv` |
| `IMPORT_ELEMENT_NOT_IMPORTED` | `InputWarning` | `from_pandapower`: transformers and impedance elements |
| `IMPORT_EMPTY` | `InputWarning` | `from_pandapower`: no bus or no branch imported |
| `IMPORT_LENGTH_DEFAULT` | `InputWarning` | `from_pandapower` / preview: a line without usable length got the fallback |
| `IMPORT_LINE_SKIPPED` | `InputWarning` | `from_pandapower` / preview: a self-loop, or a length that cannot be imported |
| `IMPORT_SWITCH_NOT_IMPORTED` | `InputWarning` | `from_pandapower`: closed bus-to-bus switches without `switch_branch_type` |
| `IMPORT_VOLTAGE_LEVEL_MISMATCH` | `InputWarning` | `from_pandapower`: the default bus type declares another voltage level |
| `PARALLEL_COEFFICIENT_DEFAULT` | `ModelAssumptionWarning` | `run_fault(phase_current_mode="paths")`: parallel routes with default coefficients |
| `PHASE_PROXY` | `ModelAssumptionWarning` | `run_fault`: a meshed phase network split with proxy impedances |
| `PROJECT_FILE_LEGACY_ENCODING` | `InputWarning` | `load_network_from_json`: a file that is not UTF-8 |
| `REDUCTION_FACTOR_UNDEFINED` | `ModelAssumptionWarning` | `run_fault`: `r_epr` undefined at a frequency |
| `SC_DATA_INCOMPLETE` | `InputWarning` | `resolve_fault_sc_characteristics`: sources without IEC 60909 data |
| `SC_KAPPA_MISSING` | `InputWarning` | `read_shortcircuit_results`: no `kappa` derivable for a bus |
| `SC_KAPPA_MIXED` | `ModelAssumptionWarning` | `resolve_fault_sc_characteristics`: sources of different R/X |
| `SC_KAPPA_OUT_OF_RANGE` | `InputWarning` | `read_shortcircuit_results`: pandapower's `ip`/`ikss` imply `kappa` outside (1, 2] |
| `SC_ROW_AMBIGUOUS` | `InputWarning` | `apply_shortcircuit_characteristics`: several matching rows |
| `SC_SPLIT_EQUAL` | `InputWarning` | `apply_shortcircuit_characteristics`: `I_k''` split equally |
| `SC_VALUE_REPLACED` | `InputWarning` | `apply_shortcircuit_characteristics`: a different `t_k_s` / `n_factor` replaced |
| `SC_VOLTAGE_SOURCE_SKIPPED` | `InputWarning` | `apply_shortcircuit_characteristics`: a voltage source keeps its values |
| `SCALINGS_INCOMPLETE` | `InputWarning` | `run_fault`: a fault's scalings miss network frequencies |
| `SOURCE_FREQUENCY_GAP` | `InputWarning` | `run_fault`: a source misses network frequencies |
| `SOURCE_FREQUENCY_OUTSIDE_NETWORK` | `InputWarning` | `Network.add_source`: values at frequencies the network does not solve |
| `SOURCE_WITHOUT_PHASE_PATH` | `ModelAssumptionWarning` | `run_fault`: a source with no phase-conductor path to the fault |
| `THERMAL_DATA_INCOMPLETE` | `InputWarning` | `check_conductor_limits` / `check_node_limits`: half-declared thermal data |
| `THERMAL_DC_EXCLUDED` | `ModelAssumptionWarning` | `check_conductor_limits` / `check_node_limits`: 0 Hz left out of the adiabatic check |
| `THEVENIN_COUPLING` | `ModelAssumptionWarning` | `run_fault`: a Thevenin source drives coupled branches |
| `TRANSIENT_BRANCH_SKIPPED` | `InputWarning` | `TransientStudy.solve` (state space): branches without `R_self`/`L_self` |
| `TRANSIENT_COUPLING_SKIPPED` | `TransientModelWarning` | `TransientStudy.solve` (state space): coupling of voltage sources |
| `TRANSIENT_MODEL` | `TransientModelWarning` | `TransientStudy.solve`: the model is narrower than the input |
| `TRANSIENT_PHASE_SPLIT` | `ModelAssumptionWarning` | `TransientStudy.solve` (state space): frequency-dependent phase split |
| `TRANSIENT_RESISTIVE_COUPLING_IGNORED` | `TransientModelWarning` | `TransientStudy.solve` (state space): mutual data on resistive branches |
| `TRANSIENT_SOURCE_NOT_CONNECTED` | `InputWarning` | `TransientStudy.solve` (FFT): a source without connection to the fault |

`gi.check_network` returns its own codes, listed [above](#pre-flight-check);
`SCALINGS_INCOMPLETE`, `SOURCE_FREQUENCY_GAP` and
`SOURCE_FREQUENCY_OUTSIDE_NETWORK` mean the same there.

## Canonical parameter names

Every concept of the public API has one parameter name. The former names
still work as keyword arguments and raise a `DeprecationWarning` that names
the replacement; positional calls are unaffected. Following the project's
deprecation policy, an old name keeps working for at least two releases and
six months after the release that deprecated it.

| Concept | Name | Former name(s) | Where |
|---|---|---|---|
| fault | `fault` | `fault_name` | `run_fault`, `set_active_fault`, `Network.set_active_fault`, `Network.sources_reaching`, `TransientStudy`, `find_max_rho_scaling`, `check_conductor_limits`, `check_node_limits`, `resolve_fault_sc_characteristics`, `apply_shortcircuit_characteristics` |
| EPR limit | `u_limit` | `u_max` | `find_max_rho_scaling` |
| fault duration | `t_k_s` | `t_k` | `check_conductor_limits`, `check_node_limits`, `admissible_short_circuit_current`, `iec60909_m` |
| AC heat factor | `n_factor` | `n` | `check_conductor_limits`, `check_node_limits`, `thermal_equivalent_current` |
| frequency | `frequency` | `f`, `freq` | `check_conductor_limits`, `check_node_limits`, `iec60909_m`, `read_shortcircuit_results`, `BusResponse.voltages`, `.voltages_no_mutual`, `.driving_point`, `.worst_case_electrode` |

```python
gi.run_fault(net, fault="F1")             # current spelling
gi.run_fault(net, "F1")                   # positional: unchanged
gi.run_fault(net, fault_name="F1")        # works, DeprecationWarning
```

## API reference

::: groundinsight.service

::: groundinsight.checks

::: groundinsight.progress

::: groundinsight.diagnostics

::: groundinsight.utils.compat

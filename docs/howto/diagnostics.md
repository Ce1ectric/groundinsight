# Diagnostics, logging and warnings

A result can be correct and still need a remark: the phase current of a ring
was split with a proxy, a frequency was scaled by default, a reactance had no
meaning at 0 Hz. `groundinsight` delivers such remarks as **diagnostics** —
warnings with a stable code, a severity and the elements they concern — so a
script, a GUI or a server can act on them. This guide reads them from a
result, checks a network before solving, collects them around other calls,
makes them errors in a test run and switches on logging. The script is
[`diagnostics.py`](../snippets/diagnostics.py); its output is at the
[end of the page](#output). Every code is listed in
[Warnings and diagnostics](../reference/warnings.md).

| Where the remark arises | How you get it |
|---|---|
| during a solve | `Result.diagnostics` of the result |
| before a solve | `gi.check_network` |
| around any other call | `collecting()` from `groundinsight.diagnostics` |
| as a Python warning | the warning filters, e.g. to make it an error |
| what the library does | logging, switched on with `set_log_level` |

## Read the diagnostics of a result

`gi.run_fault` and `gi.solve` collect every package warning raised during the
run into `Result.diagnostics`, a list of `gi.Diagnostic` records:

| Field | Meaning |
|---|---|
| `code` | stable identifier such as `PHASE_PROXY` — branch on it, never on the message text |
| `severity` | `info`, `warning` or `error` |
| `category` | the warning class, e.g. `ModelAssumptionWarning` (a modelling assumption) or `InputWarning` (incomplete input) |
| `message` | the text a person reads |
| `elements` | the elements concerned, as `(kind, name)` pairs |

The records are kept even when the warnings themselves are filtered or shown
only once, so a result always carries its complete set; `net.res_fault(fault)`
counts them in `n_diagnostics`. The records are pydantic models:
`model_dump()` gives a JSON-ready dict.

In the example the cable type declares no `phase_impedance_formula`, so the
phase current of the ring is split with proxy impedances, and the result says
which branches that concerns (`PHASE_PROXY`):

```python
--8<-- "docs/snippets/diagnostics.py:solve"
```

## Check a network before solving

`gi.check_network(net, fault=None)` returns every problem of a network at once
as `gi.Diagnostic` records and never raises: `error` where `run_fault` would
raise or describe another network than the one you built (a branch or source
on a bus that does not exist is otherwise dropped without a word), `warning`
where the network solves but part of the input has no effect (`ISOLATED_BUS`,
`SOURCE_FREQUENCY_GAP`, …). The check neither assembles nor solves, so it is
cheap enough to run after every edit. Its topology checks — isolated buses,
earthing islands, whether a source reaches the fault — run only on a network
whose references are consistent: fix the reference errors first, then check
again. In the example the first check finds the unconnected bus
(`ISOLATED_BUS`); after a source on a bus that does not exist is added, the
check reports that error (`SOURCE_UNKNOWN_BUS`, which would make the solver
ignore the source) and holds back the topology checks until it is fixed.

```python
--8<-- "docs/snippets/diagnostics.py:check"
```

## Collect warnings outside a solve

Creating elements, importing, the thermal checks and the transient solvers
raise their warnings directly. Collect them with
`groundinsight.diagnostics.collecting()`: inside the `with` block every
package warning is also recorded as a `gi.Diagnostic`. The collection belongs
to the current thread or asyncio task, so two runs in parallel never mix
their records.

```python
--8<-- "docs/snippets/diagnostics.py:collect"
```

## Filter warnings, or make them errors

Diagnostics are ordinary Python warnings of the classes
`gi.ModelAssumptionWarning`, `gi.InputWarning`, `gi.DCLimitWarning`,
`gi.TransientModelWarning` and `gi.NetworkFrequencyOrderWarning`, all
subclasses of `gi.GroundinsightWarning`. The usual tools apply:
`warnings.simplefilter("ignore", gi.InputWarning)` silences a category on the
screen (the result still records it), `warnings.simplefilter("error", ...)`
turns it into an exception that carries `code` and `elements`, and on the
command line `python -W error::groundinsight.ModelAssumptionWarning` does the
same — useful in a test suite that must not accept a heuristic result.

```python
--8<-- "docs/snippets/diagnostics.py:strict"
```

## Logging is opt-in

The library logs what it does — sessions, migrations, cache use, the summary
of a study — to the logger `groundinsight`, which has a `NullHandler`, so
nothing is printed unless you ask. Anything that qualifies a result is a
diagnostic, not a log line. `gi.set_log_level("INFO")` attaches a console
handler (repeated calls do not stack handlers); `logging.captureWarnings(True)`
routes the warnings into logging as well, as text — code and elements stay
with the records.

```python
import groundinsight as gi

gi.set_log_level("INFO")      # console output of the "groundinsight" logger
gi.set_log_level("WARNING")   # quieter again
```

For a bug report, `gi.show_versions()` returns the versions of
groundinsight, Python, the platform and the companion packages.

## In a GUI or a server

The service layer `groundinsight.api` hands the records to a front end as
`DiagnosticDTO` (code, severity, category, message and element references) in
every result DTO and in the report of the pre-flight check, unchanged. A
front end branches on `code` to choose an icon or a help text, highlights the
`elements` in its views and shows `message` as it is. See
[Service layer and command line](service-layer.md).

## Output

```text title="Output of diagnostics.py"
result: PHASE_PROXY (warning, ModelAssumptionWarning)
proxy split on: ['K1', 'K2', 'K3', 'K4']
as JSON for a front end: 1 record(s)
check: warning ISOLATED_BUS [('bus', 'X')]
check again: error SOURCE_UNKNOWN_BUS [('source', 'ghost'), ('bus', 'nowhere')]
blocking: ['SOURCE_UNKNOWN_BUS']
collected: [('SOURCE_FREQUENCY_OUTSIDE_NETWORK', [('source', 'extra')])]
turned into an error: PHASE_PROXY
```

`PHASE_PROXY` names exactly the four branches of the ring; the test suite
finds the ring branches from the topology and compares.
`SOURCE_FREQUENCY_OUTSIDE_NETWORK` is raised when the source is created, not
by a solve, which is why it has to be collected.

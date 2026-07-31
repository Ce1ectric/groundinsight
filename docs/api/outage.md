# Outage and what-if studies

The `groundinsight.simulation.outage` module turns the new `active`
flag on `Bus` and `Branch` into a first-class what-if API. Multiple
topology scenarios (e.g. "cable A out", "substation isolator open",
"shield broken") can be evaluated against one base case in a single
call, with comparison DataFrames produced automatically.

## Physical / modelling context

The nodal-admittance solver assembles
$Y(f)\,\underline{u}(f) = \underline{i}(f)$ over **active** elements
only. An inactive `Bus` is removed from the system entirely (its row
and column drop from $Y$); an inactive `Branch` contributes no
self-admittance, no mutual-coupling Norton injection, and reports a
zero current in the result — the open-circuit limit. The pathfinder
also skips inactive elements when enumerating source-to-fault paths,
which keeps the per-path Norton bookkeeping consistent.

That makes maintenance scenarios, planned outages, broken shields,
and N-1 contingencies expressible as **scenarios** without rebuilding
the network. The `outage_context` context manager flips the listed
elements to `active=False` for the duration of a `with` block and
restores the previous state — including the cached path list —
afterwards. `run_outage_study` orchestrates a base run plus one fault
calculation per scenario and returns long-format Polars DataFrames
with absolute and relative deltas against a reference scenario
(default: the base case).

## Example

```python
import groundinsight as gi

# Assume a pre-built network `net` with sources, buses, branches and
# at least one fault, e.g. from the quickstart or from
# gi.from_pandapower(...).

scenario_cable_out = gi.Outage(
    name="cable_1_oos",
    description="MS-cable cable_1 out of service",
    disabled_buses=[],
    disabled_branches=["cable_1"],
)

scenario_bus_islanded = gi.Outage(
    name="bus_x_islanded",
    description="Bus bus_x removed from the network",
    disabled_buses=["bus_x"],
    disabled_branches=[],
)

study = gi.run_outage_study(
    network=net,
    fault="fault1",
    scenarios=[scenario_cable_out, scenario_bus_islanded],
    include_base=True,
)

# Per-scenario result DataFrames
print(study.compare_buses())     # EPR per bus, with delta vs. base
print(study.compare_branches())  # branch currents, with delta vs. base
```

## Reading the two delta columns

Each comparison carries an absolute `delta_vs_<ref>` and a relative
`delta_pct_vs_<ref>`. The relative one is **`null` wherever the
reference value is zero**, and that is the ordinary case rather than
an exotic one:

| situation | reference | scenario | `delta_vs_base` | `delta_pct_vs_base` |
| --- | --- | --- | --- | --- |
| a frequency the fault does not excite | 0 V | 0 V | 0 V | `null` |
| a station islanded in the reference scenario | 0 V | 57.9 V | 57.9 V | `null` |
| ordinary change | 800 V | 720 V | −80 V | −10 % |

"Ten per cent of nothing" has no answer, so none is reported. A
zero baseline arises from a `scalings={250.0: 0.0}` fault, from
comparing `against=` a scenario that islands the bus in question,
and from any element that carries no current in the reference —
none of which is an error.

The absolute column keeps the full information in all three rows,
so it is the one to rank by when a zero baseline is possible. And
because the marker is `null` and not `NaN`, Polars aggregations
skip it:

```python
tbl = study.compare_buses()
tbl["delta_pct_vs_base"].mean()   # finite; the null rows are skipped
tbl.filter(pl.col("delta_pct_vs_base").is_null())   # the zero-baseline rows
```

Use `outage_context(network, outage)` directly when a single one-off
modification is needed:

```python
with gi.outage_context(net, scenario_cable_out):
    gi.run_fault(network=net, fault_name="fault1")
    print(net.res_all_impedances())
# net is fully restored on exit — including the cached paths.
```

## API reference

::: groundinsight.simulation.outage

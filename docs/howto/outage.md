# Outage and what-if studies

What happens to the earth potential rise when a cable is out of service, a
ring is opened or a station is cut off? This guide takes elements out of
service with `gi.Outage`, compares the scenarios with `gi.run_outage_study`
and reads the comparison frames. The script is
[`outage.py`](../snippets/outage.py); its output is at the
[end of the page](#output).

The example is a 20 kV ring: substation `UW` and three stations, bonded
through the screens of the cable system of the
[data-sheet how-to](types-from-datasheets.md) (with its phase impedance, so
the phase current divides over both halves of the ring), earth fault at `S2`.

```python
--8<-- "docs/snippets/outage.py:network"
```

## Scenarios

A `gi.Outage` names the buses and branches to take out of service. While a
scenario is solved their `active` flag is `False`: an inactive bus leaves the
nodal system, an inactive branch carries no current — neither screen nor phase
current — and induces no coupling. Nothing else changes, so scenarios need no
copy of the network.

`gi.run_outage_study(network, fault=..., scenarios=[...])` solves the base
case (`include_base=True`, labelled `base`) and every scenario, and puts the
elements back in service afterwards.

- With `on_error="raise"` (the default) a scenario whose solve fails ends the
  study. With `on_error="record"` it is noted in `OutageStudyResult.failures`
  and the study goes on — the scenarios that disconnect something are often
  the interesting ones. The base case always raises, because every comparison
  needs it.
- A scenario that islands the faulted station gives **0 V everywhere**: no
  source reaches the fault any more, so no current flows. That is the answer,
  not an error (`gi.check_network` calls it `FAULT_NOT_REACHED`). Taking the
  faulted bus itself out leaves nothing to solve and fails.
- `progress=` and `should_cancel=` report and stop a long study; a cancelled
  study restores the network to the state it had before.

```python
--8<-- "docs/snippets/outage.py:study"
```

## Reading the comparison

`OutageStudyResult.compare_buses()` and `.compare_branches()` return one row
per element, frequency, scenario and metric (`EPR_V` and `I_branch_A` unless
`columns=` names others), with `value`, the difference `delta_vs_base` to the
reference scenario and the relative difference `delta_pct_vs_base` in per
cent. Two things differ from the frames of a single solve:

- **`frequency_Hz` is text here** — `"50.0"` for a frequency and `"RMS"` for
  the RMS row — because it is the key the scenarios are joined on. Filter with
  `pl.col("frequency_Hz") == "RMS"`, not with `is_rms`, which these frames do
  not have.
- **`delta_pct_vs_base` is null where the reference value is 0** — a
  frequency the fault does not excite, a bus islanded in the reference
  scenario, an element without current. "Ten per cent of nothing" has no
  answer, and because the marker is null rather than NaN, Polars aggregations
  skip it. Rank by the absolute difference when a zero reference is possible.

`against=` compares with another scenario than the base case; the column
suffix follows its label:

```python
--8<-- "docs/snippets/outage.py:against"
```

Columns: [Result tables](../reference/results.md#outagestudyresultcompare_buses-and-compare_branches).

## One scenario by hand

`outage_context` — `gi.outage_context(network, outage)` — takes the elements out for the duration
of a `with` block and restores them — and the network's path memo — on exit,
also after an exception. Inside it, `gi.solve` returns the result without
storing it, or `gi.run_fault` stores it as usual.

```python
--8<-- "docs/snippets/outage.py:context"
```

## Output

```text title="Output of outage.py"
    scenario EPR S2 / V  delta / V delta / %
        base      231.9        0.0       0.0
      K2 out      447.7      215.7      93.0
      K4 out      369.4      137.5      59.3
 S2 islanded        0.0     -231.9    -100.0
K2 out against S2 islanded: delta = 447.7 V, delta / % = None
recorded failures: {'fault bus out': "Fault bus 'S2' is inactive or missing; cannot solve."}
outage_context, K2 out: EPR S2 = 447.7 V
out of service after the study: []
```

Opening the ring next to the fault (`K2 out`) almost doubles the earth
potential rise at `S2`: the screens of only one side of the ring carry the
current back. `None` is the null of the percentage column, because the
islanded reference is 0 V. The test suite recomputes every scenario with the
independent reference solver of the tests, with the same elements inactive.

# Parameter sweeps and statistics

A single solve answers one question; a study asks it over a range — the soil
resistivity, the characteristic of an electrode, the harmonic content, the
fault location. This guide runs one fault over such a grid with
`gi.run_sweep`, stacks the results into one frame and reduces it with
`gi.summarize` and `gi.classify`. The script is
[`sweeps.py`](../snippets/sweeps.py); its output is at the
[end of the page](#output).

The example is the 20 kV feeder of the
[EN 50522 how-to](en50522-check.md#worked-example): substation `UW` and five
stations bonded through the screens of a cable system, earth fault at `S3`,
1000 A at 50 Hz and 50 A at 250 Hz.

```python
--8<-- "docs/snippets/sweeps.py:network"
```

## One point per parameter combination

A `gi.SweepPoint` is a label plus the changes that define it:

| Field | Changes | Use it for |
|---|---|---|
| `bus_rho` | `specific_earth_resistance` of the named buses, with their bus-type formula evaluated again | the soil around the electrodes |
| `bus_impedance` | the impedance table `Bus.impedance` of the named buses, written directly — the formula is bypassed | a measured or fitted characteristic |
| `fault` | the fault solved at this point | the fault location |
| `fault_scalings` | the scalings of the fault for this point | the harmonic content |
| `parameters` | nothing — copied into every result row as columns | plotting against the physical parameter |

`bus_rho` changes the **buses** only: the `specific_earth_resistance` of the branches,
which enters the earth-return terms of a Carson-type cable formula, stays as
it is. To vary the soil along the cables as well, give the points a fault and
network of their own, or build one network per soil value.

```python
--8<-- "docs/snippets/sweeps.py:soil"
```

`gi.run_sweep(network, fault=..., points=...)` solves the fault once per
point, undoes the point's changes after the solve, and when it ends — finished,
failed or cancelled — the network is left as it was: same stored results,
same active fault, same paths (the script checks it with `model_dump_json()`).
`on_error` decides what a failing point does: with `"record"` (the default) it is noted in
`SweepResult.failures` with its message and the sweep goes on; with
`"raise"` the first failure ends it. `progress=` and
`should_cancel=` report and stop a long sweep.

## A catalogue of rho-f characteristics

`gi.rho_f_points` turns a catalogue of five-parameter rho-f vectors — the
form $Z = k_1\rho + (k_2 + jk_3)f + (k_4 + jk_5)\rho f$ that `groundfield`
exports — into points that set the impedance table of one bus. It refuses a
vector whose real part is not positive at some frequency, naming the label
and the frequency, because the solver would reject it anyway.

```python
--8<-- "docs/snippets/sweeps.py:characteristic"
```

## Reading the results

`SweepResult.buses()`, `.branches()` (only with `collect_branches=True`),
`.impedances()` and `.cuts()` (only with `cuts=`) stack the frames of the
points: the columns of `res_buses`, `res_branches`, `res_all_impedances` or
`CutAnalysis.to_polars`, preceded by `point` (the label) and one column per
key of `parameters`; `rho_f_points` adds `bus`, `rho_Ohm_m` and `k1` … `k5`.
The RMS rows have `is_rms` true, as in a single solve. Columns:
[Result tables](../reference/results.md#sweepresultbuses-branches-impedances-cuts).

## Summarise and classify

`summarize` — `gi.summarize(frame, value, by=...)` — reduces one column to `n`, `n_null`,
`n_nan`, `mean`, `std`, `min`, quantiles (`p05`, `p50`, `p95` by default) and
`max`, per group of `by=`. Read `n_nan` first: a failed point recorded as NaN
propagates into the mean and the quantiles. `classify` —
`gi.classify(frame, value, edges, labels=...)` — adds a column with the band of each row; a null or NaN value gets
no band rather than the highest one.

Neither function knows a limit. The permissible values of EN 50522 depend on
the fault duration, the edition and the measures taken, so the edges come from
your study — [Compare results with EN 50522 limits](en50522-check.md) shows
where they come from. The bands below are illustrative.

```python
--8<-- "docs/snippets/sweeps.py:statistics"
```

## Output

```text title="Output of sweeps.py"
           point  U_E RMS / V  class
          rho=30        200.7  up to 300 V
         rho=100        384.6  300 V to 600 V
         rho=300        622.8  above 600 V
        rho=1000        788.8  above 600 V
 small electrode        414.1  300 V to 600 V
  ring electrode        384.5  300 V to 600 V
     meshed grid        283.1  up to 300 V
n = 7, min = 200.7 V, median = 384.6 V, max = 788.8 V
network unchanged: True
```

`U_E RMS` is the total RMS of the earth potential rise of `S3` over 50 Hz and
250 Hz. The point `rho=100` reproduces the worked example of the EN 50522
how-to (384.6 V); `ring electrode` describes the same electrode by its rho-f
fit and differs only by the small reactance the fit carries. The test suite
recomputes every point with the independent reference solver of the tests,
with the station impedances evaluated from the formulas.

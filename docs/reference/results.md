# Result tables

Every solve stores one `Result` per fault on the network, and the package
hands results out as [Polars](https://pola.rs) data frames: the `res_*`
accessors of `Network`, the study results, the thermal checks and the
importers. This page lists every column of every frame with its type, unit
and meaning.

The column names and types in the tables below are **generated from the code**
when this site is built — from the schema constants that pin the frames
(`RES_BUSES_SCHEMA`, `gi.RES_FAULT_SCHEMA`, `CUT_FRAME_SCHEMA`, …) or, where a
frame has no constant, from a frame built on a small network at build time.
A test compares the constants with the frames of a real solve, so a table
cannot describe a column the code no longer has.

## Rules that hold for every frame

**Units.** Phasors are RMS values, currents in A, voltages in V, impedances
in Ω, angles in degrees; the column name ends with the unit (`_A`, `_V`,
`_Ohm`, `_degree` or `_deg`, `_Hz`, `_s`, `_mm2`). The time series of a
transient study are instantaneous values. See
[Conventions and units](conventions.md).

**Signs.** Magnitudes do not depend on a sign convention, angles do. Earth
potentials, electrode currents and injections are reported in the earth-side
frame (a current that enters the earthing network at the source and leaves it
at the fault), branch currents for the physical fault, counted from
`from_bus` to `to_bus` — see
[Signs and phase angles](conventions.md#signs-and-phase-angles).

**Frequencies and the RMS row.** `frequency_Hz` is `Float64` in every frame
of a solve. `res_buses`, `res_branches` and `res_fault` add **one RMS row**
per element that combines all frequencies as $\sqrt{\sum_f |X(f)|^2}$; that
row has `is_rms` true and `frequency_Hz` null, and its angles, ratios and
impedances are null, because they have no RMS. `res_all_impedances` and the
decomposition frame have no RMS row. The **exception** are the comparison
frames of an outage study: there `frequency_Hz` is text — `"50.0"`, or
`"RMS"` for the RMS row — because it is the key the scenarios are joined on.
Filter with `pl.col("is_rms")` in the frames of a solve and with
`pl.col("frequency_Hz") == "RMS"` in a comparison frame.

**Two reduction factors.** `reduction_factor` (in `res_fault`: `r_epr`) is
the EPR ratio with and without the inductive coupling — a property of the
line, blind to the electrodes, and **not** the reduction factor of EN 50522.
`reduction_factor_current` (in `res_fault`: `r_current`) is |I_E| / |I_F|,
the reduction factor *r* of EN 50522. They coincide only for ideally bonded
stations; see [Which reduction factor is which](conventions.md#which-reduction-factor-is-which).

**Missing values.** A quantity the result marks as undefined is `null`, never
a number: the current-based factor at a frequency without fault current, the
earthing impedance of a fault bus without an electrode in its earthing group
(diagnostic `EARTHING_IMPEDANCE_UNDEFINED`, see
[Warnings and diagnostics](warnings.md)). The schemas are pinned, so an empty
result is an empty frame with these columns, which can still be selected and
filtered.

```python
import polars as pl
import groundinsight as gi

station = gi.BusType(name="station", system_type="Station", voltage_level=20.0,
                     impedance_formula="2.0")
cable = gi.BranchType(name="cable", grounding_conductor=True,
                      self_impedance_formula="(0.2 + j*2*pi*f*1.2e-3)*l",
                      mutual_impedance_formula="(j*2*pi*f*1.0e-3)*l")
net = gi.create_network(name="tables", frequencies=[50.0, 250.0])
for name in ("UW", "S1", "S2"):
    gi.create_bus(name=name, type=station, network=net)
gi.create_branch(name="K1", type=cable, from_bus="UW", to_bus="S1", length=1.0, network=net)
gi.create_branch(name="K2", type=cable, from_bus="S1", to_bus="S2", length=1.0, network=net)
gi.create_source(name="infeed", bus="UW", values={50.0: 1000.0, 250.0: 50.0}, network=net)
gi.create_fault(name="F", bus="S2", network=net)
gi.run_fault(net, "F")

buses = net.res_buses(fault="F")
at_50_hz = buses.filter(pl.col("frequency_Hz") == 50.0)    # one row per bus
rms = buses.filter(pl.col("is_rms"))                       # one row per bus, frequency_Hz null
print(buses.shape, at_50_hz.shape, rms["frequency_Hz"].null_count())   # (9, 10) (3, 10) 3

study = gi.run_outage_study(net, fault="F", scenarios=[gi.Outage(name="K2 out", disabled_branches=["K2"])])
compared = study.compare_buses()
print(compared.schema["frequency_Hz"])                     # String
print(compared.filter(pl.col("frequency_Hz") == "RMS").height)   # 6: 3 buses x 2 scenarios
```

## Results of a solve

### `Network.res_buses(fault=None)`

One row per bus and frequency plus one RMS row per bus, for one fault or for
every stored result. `I_bus_A` is the current through the bus's own electrode
into the soil; `I_inj_A` is the current of the earthing conductor at a source
or fault bus. Sizing an earthing conductor with `I_bus_A` underestimates it —
the thermal check of the node uses the right one for each element.

<!-- result-table: res_buses -->

### `Network.res_branches(fault=None)`

One row per branch and frequency plus one RMS row per branch.

<!-- result-table: res_branches -->

### `Network.res_all_impedances()`

One row per stored fault and frequency, no RMS row: the earthing impedance and
**both** reduction factors at the fault location.

<!-- result-table: res_all_impedances -->

### `Network.res_fault(fault)`

The key figures of one fault, read from the stored result: one row per network
frequency plus one RMS row. The schema is `gi.RES_FAULT_SCHEMA`; this is the
frame the [EN 50522 how-to](../howto/en50522-check.md) and the command line
report.

<!-- result-table: res_fault -->

## Studies

### `SweepResult.buses()`, `.branches()`, `.impedances()`, `.cuts()`

A parameter sweep stacks the frames of its points: `buses()` the rows of
`res_buses`, `branches()` those of `res_branches` (collected only with
`run_sweep(..., collect_branches=True)`), `impedances()` those of
`res_all_impedances` for the swept fault, and `cuts()` those of
`CutAnalysis.to_polars()` (only with `cuts=`). Each frame starts with the
column `point` (`String`, the `SweepPoint.label`) followed by one column per key
of `SweepPoint.parameters` — `rho_f_points` adds `bus`, `rho_Ohm_m` and
`k1` … `k5` — and then the columns of the frame it stacks, with the types
listed above. A frame with nothing collected is an empty frame without
columns. See [Parameter sweeps](../api/sweep.md).

### `OutageStudyResult.compare_buses()` and `.compare_branches()`

Long format, one row per element, frequency, scenario and metric, with the
difference to a reference scenario (`against=`, by default the base case).
`frequency_Hz` is text here (see above).

<!-- result-table: compare_buses -->

<!-- result-table: compare_branches -->

### `CutAnalysis.to_polars()`

One row per direction (cut) and frequency of `gi.analyze_cuts`.

<!-- result-table: cut_analysis -->

### `select_rho_f_from_catalog(...)`

One row per catalogue entry, sorted by `sort_by`; one `epr_<bus>_V` column per
swept fault bus.

<!-- result-table: rho_f_catalog -->

### `summarize(frame, value, by=...)` and `classify(frame, value, edges)`

`summarize` returns one row per group: the grouping columns, then `n`,
`n_null`, `n_nan`, `mean`, `std`, `min`, one column per quantile (`p05`,
`p50`, `p95` by default) and `max` — read `n_nan` first, a NaN propagates into
the mean and the quantiles. `classify` returns its input with one `String`
column added (`class` unless `name=` says otherwise); a null or NaN value gets
a null class.

### `ResultTransient.to_polars()`

Long format, one row per sample and signal.

<!-- result-table: transient -->

### `run_reference_cases()`

One row per closed-form reference case; see
[Verification and validation](../explanation/verification.md).

<!-- result-table: reference_cases -->

## Thermal checks

### `check_conductor_limits(network, fault, ...)`

One row per branch. The thermal columns are null for a branch whose type
declares no material and cross-section; the currents are filled in anyway. See
[Analysis](../api/analysis.md#conductor-thermal-limits-iec-60949-iec-60909-0).

<!-- result-table: conductor_limits -->

### `check_node_limits(network, fault, ...)`

One row per bus and element — earthing conductor and electrode.

<!-- result-table: node_limits -->

## Importers

### `preview_pandapower_import(net, ...)`

One row per pandapower element that the import would keep or skip.

<!-- result-table: pandapower_preview -->

### `read_shortcircuit_results(net, ...)`

One row per bus of a case solved by `pandapower.shortcircuit.calc_sc`, in
amperes.

<!-- result-table: shortcircuit_results -->

### `apply_shortcircuit_characteristics(...)`

The audit trail: one row per source that feeds the fault.

<!-- result-table: shortcircuit_report -->

The ten input tables of `Network.to_tables()` and `gi.network_from_tables`
are listed on the [I/O page](../api/io.md#networks-as-tables).

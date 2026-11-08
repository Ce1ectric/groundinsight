# Network importers (I/O)

External-network importers convert existing power-system models from
third-party tools into a `groundinsight.Network`. They do not build
grounding networks from scratch — instead they project an existing
distribution-network topology onto the bus / branch primitives that
`groundinsight` solves for, while the user supplies the grounding-side
impedance formulas via an `ImportDefaults` object. The first
inhabitant is the pandapower importer; PowerFactory `.dgs` and the
live PowerFactory Python API are on the roadmap.

Two kinds of import are on this page. The **topology import**
(`preview_pandapower_import`, `from_pandapower`) answers *which buses
and branches exist*. The **short-circuit result import**
(`read_shortcircuit_results`, `apply_shortcircuit_characteristics`)
answers *how hard the fault drives them* — it takes a case already
solved by `pandapower.shortcircuit.calc_sc` and turns it into the
IEC 60909 excitation of the grounding model. The two are independent:
a hand-built `Network` can take the short-circuit import just as well,
as long as its bus names match.

The package's own file format — the JSON project file written by
`gi.save_network_to_json` — is described at the end of this page under
[Project files (JSON)](#project-files-json).

## Physical / modelling context

Distribution-network databases (pandapower, PowerFactory, NEPLAN,
PSS®E) carry the *electrical* topology: buses, lines, transformers,
their resistance and reactance per phase. They do *not* carry the
*grounding-side* model — neither the bus grounding impedance
$Z_{\text{B}}(\rho_E, f)$ nor the cable-shield self / mutual
impedance $Z_{\text{self}}, Z_{\text{mutual}}$ that
`groundinsight` needs. The importer therefore adopts the pandapower
*topology* (which buses exist, which lines connect them, how long the
lines are) and asks the user to supply the *grounding-side*
parameters via `ImportDefaults`. That separation keeps the importer
schema-stable across tools — every importer accepts the same
`ImportDefaults` shape — and lets the user attach the same set of
grounding formulas to networks coming from different sources.

The `active` flag (default `True`) is propagated from
`pandapower.in_service` so that out-of-service equipment is excluded
from the nodal solve straight away — see the
[outage-study reference](outage.md) for the runtime equivalent.

## Example

`pandapower.networks.example_simple()` has a 110 kV and a 20 kV level joined by
a transformer. The 20 kV level is imported; its `MV Transformer Bus` reaches the
`MV Main Bus` only through a closed bus-to-bus switch, which
`switch_branch_type` turns into a short branch — without it that bus would be
isolated and read 0 V (see below). The earthing types are illustrative; the
complete script and its output are in the how-to
[Import from pandapower](../howto/pandapower.md).

```python
--8<-- "docs/snippets/pandapower_topology.py:defaults"
```

```python
--8<-- "docs/snippets/pandapower_topology.py:preview"
```

```python
--8<-- "docs/snippets/pandapower_topology.py:import"
```

The import keeps 4 buses and 4 branches (`Line 2`, `Line 3`, `Line 4` and the
bond `switch_bus_1`) and skips the three 110 kV buses, the 110 kV line, the
other switches and the transformer.

The pandapower extra is optional; install with
`pip install 'groundinsight[pandapower]'` (or
`poetry install --extras pandapower`).

## Skip-reason vocabulary

`preview_pandapower_import` lists every pandapower row it keeps or skips; the
`reason` column of a skipped row is one of the following. `from_pandapower`
skips the same rows and announces them as `InputWarning`s with a code
(`IMPORT_BUS_SKIPPED`, `IMPORT_LINE_SKIPPED`, `IMPORT_SWITCH_NOT_IMPORTED`,
`IMPORT_ELEMENT_NOT_IMPORTED`; see [Warnings and diagnostics](../reference/warnings.md)).

| Reason | Element | Trigger |
|---|---|---|
| `voltage_level_mismatch` | bus | `vn_kv` is not `voltage_level_kV`: `math.isclose(vn_kv, voltage_level_kV, rel_tol=1e-9)` is false. |
| `vn_kv_unparsable` | bus | `vn_kv` is missing, `None` or `NaN`. |
| `endpoint_off_target_voltage_level` | line, switch | An end sits on another voltage level. |
| `endpoint_bus_missing` | line, switch | An end bus could not be resolved. |
| `self_loop` | line, switch | Both ends are the same bus. |
| `zero_length` | line | `length_km` is 0. `from_pandapower` refuses the import with a `ValueError`: a branch without series impedance would act as an open circuit, not as the short it represents. |
| `negative_length` | line | `length_km` is negative; refused like `zero_length`. |
| `no_switch_branch_type` | switch | A **closed** bus-to-bus switch, but no `ImportDefaults.switch_branch_type` to build it with. |
| `switch_open` | switch | The switch is open — genuinely not a connection. |
| `switch_not_bus_to_bus` | switch | `et != "b"`: the switching state of a line or transformer, not a bond. The line itself is imported with its screen and its phase conductor — see the how-to for a ring operated open at such a switch. |
| `trafos_are_not_modelled` | trafo | `net.trafo` / `net.trafo3w`. |
| `impedance_elements_are_not_modelled` | impedance | `net.impedance`. |

A line without a usable `length_km` is kept with a length of 1 km and
announced (`IMPORT_LENGTH_DEFAULT`). An import that produces no bus or no
branch is announced as well (`IMPORT_EMPTY`), so a wrong `voltage_level_kV`
fails loudly rather than silently.

### Closed switches bond grounding systems

!!! warning "Changed after 0.6.0"
    A **closed bus-to-bus switch** (`et="b"`, `closed=True`) bonds two
    grounding systems. Up to 0.6.0 it was discarded without a word, so a
    bus reachable only through a switch came out galvanically isolated
    and the solver reported **0 V** for it — which reads as a safe
    station. Set `ImportDefaults.switch_branch_type` (and optionally
    `switch_length_km`, default 1 m) to import such switches as short
    branches. Without it the behaviour is unchanged, but the omission is
    announced (`IMPORT_SWITCH_NOT_IMPORTED`) and appears in the preview.

### Deriving the phase-conductor impedance

`ImportDefaults.derive_phase_impedance=True` builds a per-line
`phase_impedance_formula` from the pandapower columns `r_ohm_per_km`
and `x_ohm_per_km`, i.e. `(r + I*x) * l`. That is what the default
`phase_current_mode="auto"` solve needs; without it every imported
mesh is split with proxy impedances (`PHASE_PROXY`). Lines that share `r`
and `x` share one derived branch type. Two approximations come with it:
the formula is the positive-sequence impedance R₁ + jX₁, while the phase
network wants the reduced impedance `Z_red` of the phase conductor with the
screen's return — exact only when all parallel routes are of the same
construction —, and the reactance is pandapower's value at its network
frequency, used at every frequency. See
[Import from pandapower](../howto/pandapower.md#the-phase-conductor-impedance).

The **earth-return** formulas (`self_impedance_formula`,
`mutual_impedance_formula`) are deliberately *not* derived. They depend
on the soil resistivity and the cable geometry, neither of which
pandapower carries, and inventing them would produce a network that
solves cleanly and is wrong. They stay exactly as the caller set them
on `default_branch_type`.

### Voltage level

`voltage_level_kV` selects which pandapower buses are imported;
`default_bus_type.voltage_level` states which level the type was
written for. The two are independent, but a mismatch is announced
(`IMPORT_VOLTAGE_LEVEL_MISMATCH`), because every imported bus carries the
*type's* value and that is what an assessment reads. Both comparisons use
`math.isclose` with a relative tolerance of 1e-9 rather than `==`, so
rounding noise from a CSV or DGS export no longer reports the whole target
level as a mismatch.

## Short-circuit characteristics (IEC 60909-0)

The grounding model is excited by a fault current, and a limit check
needs more than its RMS magnitude: the peak current $i_p$ and the
thermally equivalent current $I_{th}$ both depend on the fault-loop
$R/X$ and the clearing time $t_k$. Those follow from the *phase-side*
short-circuit calculation, which pandapower already performs. Rather
than re-implementing IEC 60909 next to it, `groundinsight` reads the
solved case.

`read_shortcircuit_results` converts `net.res_bus_sc` into a tidy,
unit-explicit Polars frame — one row per bus, currents in **amperes**
(not kA) to match the rest of the package. The fault type, case and
clearing time are taken from `net._options`, so a solved net is
self-describing and the call usually needs no arguments.
`apply_shortcircuit_characteristics` then writes the row belonging to
the fault bus onto the model: the loop $R/X$ and $\kappa$ go to every
source feeding the fault, together with that source's share of
$I_k''$; the clearing time $t_k$ and the AC heat factor $n$ go to the
`Fault`, where they belong — they describe the protection, not the
infeed. With `set_source_values=True` the source injections are
overwritten by those shares as well, which is off by default so a
hand-tuned excitation is never silently replaced. The returned frame
is an audit trail: it carries `i_k_previous_a` next to `i_k_a`, so a
review sees exactly what changed.

The example builds a 20 kV feeder in pandapower with the minimum data an
earth-fault calculation (`fault="1ph"`) needs — the short-circuit power
`s_sc_max_mva` and the ratios `rx_max`, `x0x_max`, `r0x0_max` of the external grid, and the
positive- and zero-sequence data `r_ohm_per_km`, `x_ohm_per_km`,
`c_nf_per_km`, `r0_ohm_per_km`, `x0_ohm_per_km`, `c0_nf_per_km` of the
lines — and solves it with `ip=True, ith=True` and a clearing time:

```python
--8<-- "docs/snippets/pandapower_shortcircuit.py:case"
```

Inspect the case first ...

```python
--8<-- "docs/snippets/pandapower_shortcircuit.py:read"
```

... then write it onto the grounding model and check the conductors:

```python
--8<-- "docs/snippets/pandapower_shortcircuit.py:apply"
```

The script is [`pandapower_shortcircuit.py`](../snippets/pandapower_shortcircuit.py);
its output and a hand check of $I_k''$, $R/X$ and $\kappa$ are in
[Import from pandapower](../howto/pandapower.md).

### Two deliberate deviations from pandapower

Both are documented in the module docstring of
`groundinsight.io.pandapower_sc` and are visible in the output, not
hidden:

`fault="1ph"` — the case relevant for grounding studies — leaves
pandapower's `ip_ka` and `ith_ka` entirely `NaN`; the quantities are
only published for the polyphase faults. `read_shortcircuit_results`
fills them in from the closed form
$\kappa = 1.02 + 0.98\,e^{-3R/X}$, evaluated on the **earth-fault
loop** $R/X = (2R_1 + R_0)/(2X_1 + X_0)$ rather than on the
positive-sequence ratio $R_1/X_1$ — the loop the earth-fault current
actually traverses. Where pandapower does publish `ip_ka` (the
polyphase cases), its topology-aware value is used unchanged. The
`kappa_origin` column records which of the two applied
(`"pandapower"`, `"iec_closed_form"` or `"unavailable"`); keep it in
reports, it is the difference between a reproducible study and a magic
number.

`I_th` is recomputed rather than taken over, because pandapower's
`_calc_ith` sets the DC heat factor $m$ to zero for $\kappa > 1.99$,
where the analytic limit is $m \to 2$. A vanishing $m$ makes $I_{th}$
too *small* — the unsafe direction for a limit check — and precisely
in the high-$\kappa$, DC-dominated cases where the thermal stress is
worst.

## Networks as tables

`gi.network_from_tables(...)` builds a network from ten tables — Polars
frames, CSV files or a directory of `<table>.csv` files — and
`Network.to_tables()` exports one in exactly that form. The schema is pinned
in `groundinsight.io.TABLE_SCHEMAS` (column order and dtype); required
columns are in bold, every other column may be absent or empty, which means
the model default (`None` where the field accepts it).

- `network`: **`name`**, `description`, `active_fault` — one row.
- `frequencies`: **`frequency_Hz`** — one row per frequency, in solve order.
- `bus_types`: **`name`**, `description`, **`system_type`**, **`voltage_level`**, **`impedance_formula`**, `R_formula`, `L_formula`, `C_formula`, `earthing_conductor_material`, `earthing_conductor_cross_section_mm2`, `earthing_conductor_theta_initial_C`, `earthing_conductor_theta_final_C`, `earthing_conductor_current_split`, `electrode_material`, `electrode_cross_section_mm2`, `electrode_theta_initial_C`, `electrode_theta_final_C`, `electrode_current_split`
- `branch_types`: **`name`**, `description`, **`grounding_conductor`**, **`self_impedance_formula`**, **`mutual_impedance_formula`**, `phase_impedance_formula`, `R_self_formula`, `L_self_formula`, `C_self_formula`, `R_mutual_formula`, `M_mutual_formula`, `conductor_material`, `cross_section_mm2`, `theta_initial_C`, `theta_final_C`
- `buses`: **`name`**, **`type`** (a `bus_types` name), `specific_earth_resistance` (Ω·m), `active`, `description`
- `branches`: **`name`**, **`type`** (a `branch_types` name), **`from_bus`**, **`to_bus`**, **`length`** (km), `specific_earth_resistance`, `parallel_coefficient`, `active`, `description`
- `sources`: **`name`**, **`bus`**, `source_type` (`current` or `voltage`), `i_k_a`, `r_to_x`, `kappa`, `description`
- `source_values`: **`source`**, **`quantity`**, **`frequency_Hz`**, **`real`**, `imag` — long format, one row per source, quantity and frequency; `quantity` is `current` for a current source and `voltage` / `impedance` for a Thevenin source; the values are RMS phasors in A, V or Ω.
- `faults`: **`name`**, **`bus`**, `t_k_s`, `n_factor`, `description`
- `fault_scalings`: **`fault`**, **`frequency_Hz`**, **`scaling`** — long format; a fault without rows scales every frequency with 1.0.

Only `frequencies`, `bus_types` and `buses` are required. The tables hold
*input* only: the impedance dictionaries are evaluated from the type
formulas on the way in — through the same `Network.add_*` path as
`create_bus` / `create_branch`, so every formula passes the formula guard —
and results and enumerated paths are not exported. A network whose
impedances follow from its types therefore survives the round trip
unchanged; one whose impedance dictionaries were overwritten by hand keeps
the formula, not the overwritten values.

```python
import groundinsight as gi
from pathlib import Path

folder = Path("ring_tables")
folder.mkdir(exist_ok=True)
for name, frame in net.to_tables().items():
    frame.write_csv(folder / f"{name}.csv")       # one CSV per table, UTF-8

same = gi.network_from_tables(folder)               # or a dict of frames / paths
```

Malformed tables raise **one** `ValueError` that lists every problem with
its table, row (counted from 1, header excluded) and column: missing tables
or columns, unknown columns (a typo such as `lenght` would otherwise drop
the value silently — pass `ignore_extra_columns=True` for sheets with
comment columns), cells that are not a number (a decimal comma is named as
such) or a boolean, duplicate names, references to buses, types, sources or
faults that do not exist, and every model validation error. Problems of a
well-formed network — a source that reaches no fault, partial scalings — are
what [`gi.check_network`](service.md#pre-flight-check) reports afterwards.

**Excel.** Polars reads `.xlsx` through the optional engine `fastexcel`,
which groundinsight does not depend on. With it installed,
`pl.read_excel("network.xlsx", sheet_id=0)` returns a dict of sheet name to
frame — one sheet per table — which `gi.network_from_tables` takes as it
is. Integer columns from a spreadsheet are accepted where floats are
expected.

## Walkthrough notebook

The end-to-end example for the pandapower importer is shipped as
[**Pandapower import**](../examples/pandapower_import.ipynb) in the
Examples section. It shows the full preview → commit flow on a real
MV ring net and is the recommended starting point for case-study
setups based on existing distribution-network models.

## API reference

::: groundinsight.io

## Project files (JSON)

`gi.save_network_to_json(network, path)` writes one network to a *project
file*, and `gi.load_network_from_json(path)` reads it back. The file is the
network's own `model_dump_json(indent=4)` with two header keys in front:

```json
{
    "schema_version": 1,
    "groundinsight_version": "2026.11.0",
    "name": "Umspannwerk Süd",
    "description": null,
    "frequencies": [50.0, 250.0],
    ...
}
```

- **UTF-8 on every platform.** Names such as `"Umspannwerk Süd Ω"` are
  stored as UTF-8 regardless of the locale; up to 0.6.0 the locale decided,
  which on Windows (cp1252) meant a `UnicodeEncodeError` for `Ω`/`ρ` and
  mojibake for a file written on macOS. A UTF-8 byte-order mark is accepted
  on reading. A file that is not UTF-8 can only come from 0.6.0 or earlier on
  a non-UTF-8 system; it is read with the platform's preferred encoding, as
  0.6.0 did, and an `InputWarning` with the code
  `PROJECT_FILE_LEGACY_ENCODING` says so. Saving it again writes UTF-8.
- **Atomic.** The network is serialised completely, written to a temporary
  file in the target directory, flushed and `fsync`-ed, and only then moved
  over the target with `os.replace`. A failure at any point leaves an
  existing project file byte-identical. The replaced file keeps its
  permission bits, a read-only file is refused, and a symbolic link keeps
  pointing at the saved file.
- **Versioned.** `schema_version` is the version of the file layout,
  `groundinsight_version` the release that wrote it (see the table below).

| `schema_version` | Written by | Content |
|---|---|---|
| *(absent)* | 0.6.0 and earlier | the model dump only — read unchanged |
| `1` | the first release after 0.6.0 and later | the two header keys plus the model dump |

A file with a newer `schema_version` than the running release supports is
refused with a `ValueError` that names both versions, instead of being read
with whatever the newer release added silently dropped. The version is
incremented whenever a release writes something an earlier release would
ignore or misread.

The header keys sit next to the model fields rather than in an envelope on
purpose: `Network` ignores unknown keys, so
`gi.Network.model_validate_json(text)` still reads a project file — and a
0.6.0 installation still opens a file written by a newer release of the same
schema. `load_network_from_json` removes the two keys before validation and
therefore does not depend on that. The names `schema_version` and
`groundinsight_version` are reserved; `Network` never gets fields of that
name.

::: groundinsight.io.project_file

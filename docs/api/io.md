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

```python
import pandapower.networks as pn
import groundinsight as gi

# Any pandapower MV / LV demo network — here a simple 4-bus MV ring.
net_pp = pn.example_simple()

defaults = gi.ImportDefaults(
    rho=100.0,
    frequencies=[50.0, 250.0],
    default_bus_type=gi.BusType(
        name="MVbus",
        description="Default substation grounding grid",
        system_type="Substation",
        voltage_level=20.0,
        impedance_formula="rho * 0.01 + j * f * 1/50 * 0.1",
    ),
    default_branch_type=gi.BranchType(
        name="MVcable",
        description="Default 20 kV cable",
        grounding_conductor=True,
        self_impedance_formula="(0.25 + j * f * 0.012) * l",
        mutual_impedance_formula="(0.0  + j * f * 0.012) * l",
    ),
)

# 1. Pre-flight summary: kept vs. skipped elements with a reason column.
#    Pass the same `defaults` so closed bus-to-bus switches are previewed
#    the way they will actually be imported.
preview = gi.preview_pandapower_import(
    net_pp, voltage_level_kV=20.0, defaults=defaults
)
print(preview)

# 2. Build the Network. Only buses / lines on the chosen voltage
#    level are imported; ext_grids, sgens and loads are ignored, and
#    trafos / impedance elements are listed as skipped.
net = gi.from_pandapower(
    net_pp,
    defaults=defaults,
    voltage_level_kV=20.0,
    network_name="MV ring (from pandapower)",
)

print(f"Imported {len(net.buses)} buses, {len(net.branches)} branches.")
```

The pandapower extra is optional; install with
`pip install 'groundinsight[pandapower]'` (or
`poetry install --extras pandapower`).

## Skip-reason vocabulary

Both `preview_pandapower_import` and `from_pandapower` produce
warning records on the `groundinsight.io.pandapower_import` logger
whenever they discard a row. The `reason` column of the preview frame
matches the warning text:

| Reason                            | Element | Trigger                                                                 |
|-----------------------------------|---------|-------------------------------------------------------------------------|
| `voltage_level_mismatch`          | bus     | `bus.vn_kv != voltage_level_kV`.                                        |
| `vn_kv_unparsable`                | bus     | `bus.vn_kv` is missing, `None`, or `NaN`. **New in 0.5.**               |
| `endpoint_off_target_voltage_level` | line  | One endpoint sits on a different voltage level.                         |
| `endpoint_bus_missing`            | line    | An endpoint bus could not be resolved.                                  |
| `self_loop`                       | line    | `from_bus == to_bus`. **New in 0.5.**                                   |
| `no_switch_branch_type`           | switch  | A **closed** bus-to-bus switch, but no `ImportDefaults.switch_branch_type` to build it with. **New in 0.6.1.** |
| `switch_open`                     | switch  | The switch is open — genuinely not a connection. **New in 0.6.1.**      |
| `switch_not_bus_to_bus`           | switch  | `et != "b"`: the switching state of a line or trafo, not a bond. **New in 0.6.1.** |
| `trafos_are_not_modelled`         | trafo   | `net.trafo` / `net.trafo3w`. **New in 0.6.1.**                          |
| `impedance_elements_are_not_modelled` | impedance | `net.impedance`. **New in 0.6.1.**                                |

If the importer produces a `Network` with zero buses or zero branches
it now also emits a `logger.warning` so a wrong `voltage_level_kV`
argument fails loudly rather than silently.

### Closed switches bond grounding systems

!!! warning "Changed in 0.6.1"
    A **closed bus-to-bus switch** (`et="b"`, `closed=True`) bonds two
    grounding systems. Up to 0.6.0 it was discarded without a word, so a
    bus reachable only through a switch came out galvanically isolated
    and the solver reported **0 V** for it — which reads as a safe
    station. Set `ImportDefaults.switch_branch_type` (and optionally
    `switch_length_km`, default 1 m) to import such switches as short
    branches. Without it the behaviour is unchanged, but the omission is
    now logged and appears in the preview.

### Deriving the phase-conductor impedance

`ImportDefaults.derive_phase_impedance=True` builds a per-line
`phase_impedance_formula` from the pandapower columns `r_ohm_per_km`
and `x_ohm_per_km`, i.e. `(r + I*x) * l`. That is what the default
`phase_current_mode="auto"` solve needs; without it every imported
mesh falls back to the older path-based proxy. Lines that share `r`
and `x` share one derived branch type.

The **earth-return** formulas (`self_impedance_formula`,
`mutual_impedance_formula`) are deliberately *not* derived. They depend
on the soil resistivity and the cable geometry, neither of which
pandapower carries, and inventing them would produce a network that
solves cleanly and is wrong. They stay exactly as the caller set them
on `default_branch_type`.

### Voltage level

`voltage_level_kV` selects which pandapower buses are imported;
`default_bus_type.voltage_level` states which level the type was
written for. The two are independent, but a mismatch is now reported at
`WARNING`, because every imported bus carries the *type's* value and
that is what a touch-voltage assessment reads. The bus filter compares
`vn_kv` with `math.isclose` rather than `==`, so rounding noise from a
CSV or DGS export no longer reports the whole target level as a
mismatch.

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

```python
import pandapower.shortcircuit as sc
import groundinsight as gi

sc.calc_sc(net_pp, fault="1ph", case="max", ip=True, ith=True, tk_s=0.5)

# Inspect first ...
gi.read_shortcircuit_results(net_pp).select(
    "bus_name", "i_k_a", "r_to_x", "kappa", "kappa_origin", "i_p_a", "i_th_a"
)

# ... then write onto the grounding model and check the conductors.
gi.apply_shortcircuit_characteristics(
    net_gi, net_pp, "fault1", set_source_values=True
)
gi.run_fault(net_gi, "fault1")
gi.check_conductor_limits(net_gi, "fault1")
```

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

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
preview = gi.preview_pandapower_import(net_pp, voltage_level_kV=20.0)
print(preview)

# 2. Build the Network. Only buses / lines on the chosen voltage
#    level are imported; switches, ext_grids, sgens, loads are ignored.
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

If the importer produces a `Network` with zero buses or zero branches
it now also emits a `logger.warning` so a wrong `voltage_level_kV`
argument fails loudly rather than silently.

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

# Network importers (I/O)

External-network importers convert existing power-system models from
third-party tools into a `groundinsight.Network`. They do not build
grounding networks from scratch — instead they project an existing
distribution-network topology onto the bus / branch primitives that
`groundinsight` solves for, while the user supplies the grounding-side
impedance formulas via an `ImportDefaults` object. The first
inhabitant is the pandapower importer; PowerFactory `.dgs` and the
live PowerFactory Python API are on the roadmap.

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

## API reference

::: groundinsight.io

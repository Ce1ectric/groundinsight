# Earthing characteristic from groundfield

A bus type describes its electrode by a formula `Z(rho, f)`. For a real
station a field calculation is better: it gives the earthing impedance over
the specific earth resistance and the frequency, and with it the touch factor
`k_touch = U_vT / U_E` and the transfer factor `k_transfer = U_transfer / U_E`.
[groundfield](https://github.com/Ce1ectric/groundfield) computes such fields;
this page shows how its result becomes an `EarthingCharacteristic` on a bus
and what to watch for. What the factors mean, and where they stop being
valid, is explained in
[Touch voltage and transferred potential](../explanation/touch-and-transfer.md).

groundfield is not a dependency of groundinsight. Reading its files needs
only groundinsight; computing a characteristic needs groundfield in the same
environment (`groundinsight.integrations.groundfield.available()` tells).

## In the GUI

Double-click a bus and open the tab **Earthing characteristic**:

- **Import groundfield BusType JSON** reads a file groundfield wrote and
  attaches it to the bus.
- **Compute with groundfield** runs the field calculation as a job: the kind
  of electrode (ring electrode with rods, foundation electrode, rod) and its
  main dimension, homogeneous or two-layer soil, the factors on ρ that span
  the table, the frequencies, optional distances of target points for the
  transferred potential, and the backend. The button is disabled, with the
  reason, where groundfield is not installed.

An imported characteristic goes to the bus; a computed one to the bus or to
its type, and then every bus of that type uses it. Either is one step that
**Undo** takes back, and **Remove own characteristic** returns the bus to its
type. The tab shows the table and where the bus impedance comes from;
**Colouring → Origin of the earthing impedance** shows it for the whole plan.

## From a groundfield BusType file

groundfield writes its result for groundinsight as a *BusType document*
(`save_bustype_json`). `read_bustype_json` turns it into a characteristic;
`Network.update_bus` attaches it to one bus, `BusType(..., characteristic=...)`
to a type. In the example a hemisphere of radius 2 m stands in for the file
groundfield would write: its impedance is $\rho/(2\pi r)$, and its touch
factor at 1 m is $1 - r/(r + 1) = 1/3$.

```python
import math

import polars as pl

import groundinsight as gi
from groundinsight.integrations.groundfield import read_bustype_json, write_bustype_json

r = 2.0
rho = (50.0, 100.0, 200.0, 400.0)
hemisphere = gi.EarthingCharacteristic(
    name="hemisphere r = 2 m",
    rho_Ohm_m=rho,
    frequency_Hz=(50.0, 250.0),
    impedance_Ohm=[[x / (2 * math.pi * r)] * 2 for x in rho],
    k_touch=[[1 / (r + 1)] * 2 for _ in rho],
    impedance_formula="rho / (4*pi)",
)
write_bustype_json("station.json", hemisphere, system_type="MV/LV", voltage_level=20.0)

station = read_bustype_json("station.json")
print(station.name, station == hemisphere)
print(station.to_frame().filter(pl.col("frequency_Hz") == 50.0).select("rho_Ohm_m", "Z_Ohm", "k_touch"))
```

```text title="Output"
hemisphere r = 2 m True
shape: (4, 3)
┌───────────┬───────────┬──────────┐
│ rho_Ohm_m ┆ Z_Ohm     ┆ k_touch  │
│ ---       ┆ ---       ┆ ---      │
│ f64       ┆ f64       ┆ f64      │
╞═══════════╪═══════════╪══════════╡
│ 50.0      ┆ 3.978874  ┆ 0.333333 │
│ 100.0     ┆ 7.957747  ┆ 0.333333 │
│ 200.0     ┆ 15.915494 ┆ 0.333333 │
│ 400.0     ┆ 31.830989 ┆ 0.333333 │
└───────────┴───────────┴──────────┘
```

A document groundfield wrote carries its samples (the table), its k0..k5
fit (the formula) and its provenance — groundfield version, date, the
resistivity preset of the ρ axis, electrode, soil and a SHA-256 of the
document. A document groundinsight wrote carries the touch and transfer
factors as well and reads back unchanged, as here.

Attached to the station `S` of a small network — an overhead line without an
earth wire, so the whole fault current of 100 A flows through the electrode of
`S` — the characteristic replaces the impedance of the bus type, and the
result gains the touch voltage:

```python
net = gi.create_network(name="station", frequencies=[50.0])
bus_type = gi.BusType(name="station", system_type="MV/LV", voltage_level=20.0,
                      impedance_formula="rho / 100")
line = gi.BranchType(name="overhead line", grounding_conductor=False,
                     self_impedance_formula="(0.2 + j*0.4) * l",
                     mutual_impedance_formula="0 * l")
gi.create_bus(name="UW", type=bus_type, specific_earth_resistance=50.0, network=net)
gi.create_bus(name="S", type=bus_type, specific_earth_resistance=100.0, network=net)
gi.create_branch(name="line", type=line, from_bus="UW", to_bus="S", length=5.0,
                 specific_earth_resistance=100.0, network=net)
gi.create_source(name="infeed", bus="UW", values={50.0: 100.0}, network=net)
gi.create_fault(name="F", bus="S", scalings={50.0: 1.0}, network=net)

net.update_bus("S", characteristic=station)
gi.run_fault(net, "F")
row = net.res_buses("F").filter(pl.col("bus_name") == "S", pl.col("frequency_Hz") == 50.0).row(0, named=True)
print(f"Z_B = {abs(net.buses['S'].impedance[50.0]):.3f} Ohm, EPR = {row['EPR_V']:.1f} V, "
      f"U_vT = {row['U_vT_V']:.1f} V")
```

```text title="Output"
Z_B = 7.958 Ohm, EPR = 795.8 V, U_vT = 265.3 V
```

By hand: $100/(4\pi)$ Ω = 7.958 Ω at ρ = 100 Ω·m, 100 A · 7.958 Ω = 795.8 V,
and a third of it is 265.3 V. `gi.check_touch_voltages(net, "F", u_limit=...)`
compares `U_vT` with the permissible value of EN 50522 you pass
([EN 50522 how-to](en50522-check.md)).

## Compute it with groundfield

`characterize` builds the world once per soil, solves it and fills one row of
the table per soil. `electrode_world` builds a typical electrode alone —
`"ring"` (a station ring with driven rods), `"foundation"` (a meshed house
foundation) or `"rod"`, with the dimensions of `ELECTRODES` as defaults — and
`add_electrode` puts the same electrode into a larger world of your own.
`scaled_soils` spans the ρ axis: every layer of a layered soil is scaled by
the same factor, the same soil wetter or drier.

```python
# fragment: needs groundfield installed
import groundfield as gf
from groundinsight.integrations import groundfield as gfi

soils = gfi.scaled_soils(gf.TwoLayerSoil(rho_1=100.0, rho_2=500.0, h_1=2.0), [0.5, 1.0, 2.0, 4.0])
engine = gf.create_engine(backend="mom", frequencies=[50.0, 150.0, 250.0, 550.0, 1000.0],
                          segment_length=0.5)
station = gfi.characterize(
    gfi.electrode_world("ring", name="ONS", radius_m=2.5, depth_m=0.6, rods=4, rod_length_m=2.5),
    soils, electrode="ONS", engine=engine, rho_preset="station",
    transfer_targets=[(25.0, 15.0, 0.0)], name="station ring 5 m with 4 rods",
)
gfi.write_bustype_json("station.json", station, system_type="MV/LV", voltage_level=20.0)
```

What the arguments decide:

- **The ρ of the table is an effective resistivity.** For layered soil the
  table is filled at `groundfield.effective_resistivity(soil, preset=...)`;
  `rho_preset` names the preset (`"station"`, `"lv_network"`, …) and is kept
  in the provenance. Give the bus the effective resistivity of the same
  preset, otherwise the table is read at another point than the one
  computed.
- **The grid has to cover the network.** ρ and f of every bus — in a sweep
  or an inverse ρ search, every value they reach — should lie inside the
  table. Outside, the edge cell is continued (a power law in ρ and f) and
  `run_fault` and `check_network` report `CHARACTERISTIC_EXTRAPOLATED` for
  the bus ([warnings](../reference/warnings.md)).
- **The backend.** `"mom"` gives touch voltages of field quality; the image
  backend is faster and fine for the impedance, but its touch voltages of
  meshed electrodes deviate by several per cent.
- **Touch voltage.** `touch_distance` (1 m by EN 50522) and `n_angles` set
  where the feet stand around the electrode; `touch_interior=True` also
  looks inside the meshes of an open-air grid. `k_touch` is the largest
  touch voltage found, over the electrode potential.
- **Transferred potential.** `transfer_targets` are points on the surface —
  house connections, a neighbouring installation — and `k_transfer` is the
  largest difference to the electrode potential there, over that potential.
  Without targets the characteristic has no `k_transfer`.
- **The formula.** With `fit=True` (default, at least two soils)
  groundfield's k0..k5 standard form is kept as `impedance_formula`;
  `impedance_source="formula"` makes the bus take its impedance from it
  instead of the table.

## Electrodes close together: one characteristic at the feed

A station and the foundations of the houses on its PEN, a few tens of metres
apart, raise each other's potential through the soil. Characteristics
computed one electrode at a time do not see this, and the network built from
them gives an earthing impedance that is too low — by 43 % at 25 m house
spacing in the example of the
[explanation](../explanation/touch-and-transfer.md#electrodes-that-see-each-other).
Characterise such a system as a whole and attach the result to the bus where
the fault current enters:

```python
# fragment: needs groundfield and a build_world of the whole system
system = gfi.characterize(build_whole_lv_network, soils, electrode="ONS", engine=engine,
                          rho_preset="station", quantity="input",
                          name="station with five houses, at the station")
```

`quantity="input"` takes the driving-point impedance seen at the source in
`ONS` instead of the cluster impedance of one electrode: the PEN conductors
have an impedance of their own and do not form one galvanic cluster. Separate
characteristics remain right for electrodes far apart; how far is far
depends on the soil (in the example the error is still 8 % at 200 m).

## Check what you attached

- `characteristic.to_frame()` shows the table; `characteristic.provenance`
  where it comes from.
- `bus.resolved_characteristic()` names the characteristic that applies
  (the bus's own wins over its type's); `bus.k_touch` and `bus.k_transfer`
  are the factors at the bus's ρ, per frequency.
- `gi.check_network(net)` reports an extrapolated characteristic before you
  solve.
- [Result tables](../reference/results.md) lists the columns `U_vT_V` and
  `U_transfer_V` of `res_buses`, their maxima in `res_fault` and the frame of
  `check_touch_voltages`.

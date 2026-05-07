# Quickstart

This page walks through a minimal end-to-end calculation: two substations
connected by a single medium-voltage cable, a fault at the remote bus and a
current source at the feeding substation. The example covers every stage of a
typical workflow — network construction, path generation, solve and result
access.

## 1. Import and create a network

Every object in `groundinsight` lives inside a `Network` container. Start by
importing the package and creating an empty network with the frequencies of
interest (the 50 Hz fundamental plus a few harmonics):

```python
import groundinsight as gi

net = gi.create_network(
    name="QuickstartNet",
    frequencies=[50, 250, 350, 450, 550],
)
```

## 2. Define bus and branch types

`BusType` and `BranchType` hold the formula strings for the grounding
impedance, self impedance and mutual impedance. The symbols `rho`, `f` and `l`
refer to the specific earth resistance $\rho_E$, frequency and line length;
`j` denotes the imaginary unit.

```python
bus_type = gi.BusType(
    name="SubstationBus",
    description="Lumped substation grounding grid",
    system_type="Substation",
    voltage_level=20,
    impedance_formula="rho * 0.01 + j * f * 1/50 * 0.1",
)

cable_type = gi.BranchType(
    name="MSCable",
    description="20 kV single-core cable with shield",
    grounding_conductor=True,
    self_impedance_formula="(0.25 + j * f * 0.012) * l",
    mutual_impedance_formula="(0.0  + j * f * 0.012) * l",
)
```

## 3. Add buses and branches

```python
gi.create_bus(
    name="bus_source",
    type=bus_type,
    network=net,
    specific_earth_resistance=100.0,
)
gi.create_bus(
    name="bus_fault",
    type=bus_type,
    network=net,
    specific_earth_resistance=100.0,
)

gi.create_branch(
    name="cable_1",
    type=cable_type,
    from_bus="bus_source",
    to_bus="bus_fault",
    length=5.0,
    specific_earth_resistance=100.0,
    network=net,
)
```

## 4. Add the source and the fault

```python
gi.create_source(
    name="substation_infeed",
    bus="bus_source",
    values={50: 1000.0, 250: 200.0, 350: 100.0, 450: 50.0, 550: 25.0},
    network=net,
)

gi.create_fault(
    name="fault_at_remote_bus",
    bus="bus_fault",
    description="Single-phase-to-ground fault at bus_fault",
    scalings={50: 1.0},
    network=net,
)
```

## 5. Create paths and solve

Path generation discovers every route from each source to the active fault and
is used to inject the mutual-coupling Norton currents with the correct sign.
If you skip `create_paths`, `run_fault` calls it implicitly.

```python
gi.create_paths(network=net)
gi.run_fault(network=net, fault_name="fault_at_remote_bus")
```

## 6. Inspect the results

The results are attached to the `Network` object and exposed as Polars
DataFrames through convenience methods:

```python
import polars as pl

buses    = net.res_buses(fault="fault_at_remote_bus")
branches = net.res_branches(fault="fault_at_remote_bus")

print(buses.filter(pl.col("bus_name") == "bus_fault"))
print(branches.filter(pl.col("branch_name") == "cable_1"))
```

The `res_all_impedances()` method summarises the grounding impedance $Z_G$ and
the reduction factor $r$ of every configured fault:

```python
print(net.res_all_impedances())
```

## 7. Plot

For a quick visual check use the bar-plot helpers:

```python
result = net.results["fault_at_remote_bus"]

gi.plot_bus_voltages(result=result, title="EPR — RMS values")
gi.plot_branch_currents(result=result, title="Branch currents — RMS values")
gi.plot_bus_currents(result=result, title="Bus currents — RMS values")
```

## 8. Save and load

Networks can be persisted either to a SQLite database or to a JSON file:

```python
# --- JSON ---
gi.save_network_to_json(network=net, path="quickstart.json")
loaded = gi.load_network_from_json(path="quickstart.json")

# --- SQLite ---
gi.start_dbsession(sqlite_path="quickstart.db")
gi.save_network_to_db(network=net, overwrite=True)
restored = gi.load_network_from_db(name="QuickstartNet")
gi.close_dbsession()
```

That is the full workflow. The [Concepts](concepts.md) page explains the model
behind the scenes; the [Examples](examples/index.md) section contains
runnable notebooks covering more realistic network topologies.

## 9. Logging and silencing output

`groundinsight` is a quiet library by default: it attaches a
`logging.NullHandler` to the package logger on import, so simply importing
and using it produces no console output. Status messages, overwrite
warnings and solver errors are all emitted through the standard
[`logging`](https://docs.python.org/3/library/logging.html) module.

To see the messages in a notebook or script, opt in with the convenience
helper:

```python
import groundinsight as gi

gi.set_log_level("INFO")  # or "WARNING", "ERROR", logging.DEBUG, ...
```

This attaches a single `StreamHandler` with a `LEVEL [logger] message`
formatter to the `groundinsight` logger and is safe to call repeatedly
(the handler is only added once). For full control, configure the
standard `logging` module directly — for example, route the
`groundinsight.electrical_network` logger to a file while keeping the
rest at `WARNING`.

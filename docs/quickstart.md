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

Both steps require the network to have at least one source **and** at least one
fault; without either there is nothing to enumerate, and the calculation would
otherwise run to completion and report 0 V everywhere. You get a `ValueError`
naming the missing side instead.

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

Passing `frequencies=` selects single frequencies from the result. Only
frequencies the network was actually computed for can be plotted — asking for
`250.0` on a 50 Hz result raises a `KeyError` rather than drawing a bar of
height zero, which would read as "no earth potential rise at 250 Hz".

The helpers return the figure and leave it open, which is what you want in a
notebook. Inside a loop, pass `close=True` so the figures do not accumulate —
the returned figure is still complete and `savefig` still works. Pass `ax=` to
draw into an axis you created yourself; that is how two scenarios end up side
by side in one figure:

```python
import matplotlib.pyplot as plt

fig, axes = plt.subplots(1, 2, figsize=(16, 5), sharey=True)
gi.plot_bus_voltages(result=base,   ax=axes[0], title="base case")
gi.plot_bus_voltages(result=outage, ax=axes[1], title="cable out")
```

See [Figure ownership](api/plotting.md#figure-ownership) for the details.

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

## 9. Optional: outage / what-if studies

Both `Bus` and `Branch` carry an `active` flag (default `True`) that
toggles the element in the nodal solve. To evaluate one or more
contingency scenarios in a single call, wrap them in `Outage`
descriptors and pass them to `run_outage_study`:

```python
scenario = gi.Outage(
    name="cable_1_oos",
    description="MV cable_1 out of service",
    disabled_buses=[],
    disabled_branches=["cable_1"],
)

study = gi.run_outage_study(
    network=net,
    fault="fault_at_remote_bus",
    scenarios=[scenario],
    include_base=True,
)

print(study.compare_buses())     # EPR per bus, with delta vs. base
print(study.compare_branches())  # branch currents, with delta vs. base
```

For a single ad-hoc modification, `gi.outage_context(net, scenario)`
flips the elements for the duration of a `with` block and restores
the previous state on exit. See the
[outage-study reference](api/outage.md) for the full API.

## 10. Optional: inverse rho analysis

Given an EPR limit $u_{\max}$ at the fault bus, find the largest
uniform scaling of `specific_earth_resistance` at selected buses
that still satisfies it:

```python
result = gi.find_max_rho_scaling(
    network=net,
    fault_name="fault_at_remote_bus",
    bus_names=["bus_source", "bus_fault"],
    u_max=200.0,            # touch-voltage limit in volts (RMS)
    c_bounds=(0.1, 100.0),
    tol_rel=1e-3,
)
# result is a dict: c_max, u_epr_rms_at_c_max, rho_max, iterations,
# status, converged, c_bracket, bracket_rel_width.
#
# Check `converged` before using `c_max`: c_max is always a factor whose
# EPR was measured and found admissible, but only a converged search has
# also shown that nothing meaningfully larger is. A non-converged result
# means either "the whole bracket was admissible — widen c_bounds" or
# "the step cap was hit"; `status` says which.
if not result["converged"]:
    print(f"not a maximum: {result['status']}, "
          f"bracket {result['c_bracket']}")

print(f"c_max = {result['c_max']:.3f}, "
      f"EPR = {result['u_epr_rms_at_c_max']:.1f} V")
```

The original `rho` values are restored automatically — see the
[analysis reference](api/analysis.md), which lists every `status`
value and what it says about `c_max`.

## 11. Optional: import from pandapower

If a distribution-network model already exists in pandapower, the
topology can be reused directly:

```python
import pandapower.networks as pn

defaults = gi.ImportDefaults(
    rho=100.0,
    frequencies=[50.0, 250.0],
    default_bus_type=bus_type,
    default_branch_type=cable_type,
)

net_pp = pn.example_simple()
net_imported = gi.from_pandapower(
    net_pp, defaults=defaults, voltage_level_kV=20.0,
)
```

`gi.preview_pandapower_import(net_pp, voltage_level_kV=20.0)`
returns a Polars DataFrame summarising kept and skipped elements
with an explicit `reason` column. Install with
`pip install 'groundinsight[pandapower]'`. See the
[I/O reference](api/io.md) for details.

## 12. Optional: transient simulation

A `TransientStudy` produces time-domain EPR and shield-current
trajectories for a user-defined source waveform. The FFT solver
re-uses the existing `impedance_formula` of every `BusType` and
`BranchType`; the state-space solver consumes the lumped RLC
fields instead.

```python
study = gi.TransientStudy(network=net, fault_name="fault_at_remote_bus")
study.set_source_waveform(
    "substation_infeed",
    gi.waveforms.sinusoidal_with_dc_offset(
        amplitude=1e3, frequency_hz=50.0,
        t_on=0.02, t_off=0.12,
        dc_amplitude=500.0, dc_decay_tau=0.05,
    ),
)
study.set_observation(buses=["bus_fault"], branches=["cable_1"])
result = study.solve(t_end=0.2, dt=1e-4, solver="fft")

gi.plot_epr_transient(result=result)
gi.plot_branch_current_transient(result=result)
```

See the [transient-simulations reference](api/transient.md) for the
full API and the differences between the FFT and state-space solvers.

## 13. Logging and silencing output

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

# Network operations

High-level factory functions to build a `Network` and run fault
calculations. These are the functions re-exported on the top-level
package as `groundinsight.create_network`,
`groundinsight.run_fault`, etc.

## Physical / modelling context

A `Network` is a graph: each `Bus` represents a grounding node with
an own earthing impedance $Z_E(f, \rho)$, every `Branch` a
metallic return path with self-impedance
$Z_\text{branch}(f, l)$ and an optional mutual coupling that
appears as a Norton injection along the source-to-fault path.
Building the network and triggering a fault therefore requires four
ingredients:

1. **Types** — `BusType`, `BranchType` carrying SymPy formula
   strings in the free symbols `rho`, `f` and (for branches) `l`.
2. **Instances** — `Bus`, `Branch` with concrete values for
   `specific_earth_resistance`, `length`, `parallel_coefficient`.
3. **Excitation** — at least one `Source` (current or voltage) and
   one `Fault` declaring which bus is the ground-fault location and
   which fault scaling per frequency applies.
4. **Frequency list** — the harmonics or per-fault spectrum at which
   the formula-based impedances are evaluated.

`run_fault` then assembles the nodal admittance matrix, runs the
sparse LU solve per frequency, and stores the result on the
`Network` (`net.results`).

## Example

```python
import groundinsight as gi

# 1. Frequency list and types
net = gi.create_network(name="demo", frequencies=[50.0, 250.0])
bus_type = gi.BusType(
    name="GroundRod",
    system_type="Substation",
    voltage_level=20.0,
    impedance_formula="rho/(2*3.14159*1.5)*(1 + j*0.01*f)",
)
branch_type = gi.BranchType(
    name="ShieldCable",
    grounding_conductor=True,
    self_impedance_formula="(0.2 + j*0.4*f/50)*l",
    mutual_impedance_formula="(0.0 + j*0.4*f/50)*l",
)

# 2. Instances
gi.create_bus(name="bus_substation", type=bus_type,
              specific_earth_resistance=100.0, network=net)
gi.create_bus(name="bus_fault", type=bus_type,
              specific_earth_resistance=100.0, network=net)
gi.create_branch(name="line_1", type=branch_type,
                 from_bus="bus_substation", to_bus="bus_fault",
                 length=2.0, network=net)

# 3. Source and fault
gi.create_source(name="src1", bus="bus_substation",
                 values={50.0: 1.0, 250.0: 0.05}, network=net)
gi.create_fault(name="f1", bus="bus_fault",
                scalings={50.0: 1.0, 250.0: 0.05}, network=net)

# 4. Run
gi.run_fault(network=net, fault_name="f1")
print(net.res_buses(fault="f1"))
```

The same workflow scales to multi-source ring or mesh topologies;
`auto_parallel_coefficients=True` activates an auxiliary phase-only
solve that derives per-path current shares automatically.

## A network without excitation is rejected

Step 3 is not optional. Path enumeration runs over
`sources × faults`, so a network missing either side yields no paths
at all — and the calculation used to run to completion anyway and
report **0 V at every bus**. That is a plausible-looking answer, and
the most common way to arrive at it is a forgotten
`gi.create_source(...)`, not a network that is genuinely unexcited.
`create_paths` — and therefore `run_fault`, which rebuilds the paths
itself — now raises a `ValueError` naming the missing side.

Finding no path *between* an existing source and an existing fault
is a different matter and stays permitted: that is exactly what an
outage scenario islanding the fault bus produces, and 0 V is then
the correct answer.

## `create_network_assistant` and the n−1 rule

A line of `n` buses has `n − 1` branches, so `branch_length` needs
`n − 1` entries:

```python
net = gi.create_network_assistant(
    name="Line30", frequencies=[50.0, 250.0], number_buses=30,
    bus_type=bus_type, branch_type=branch_type,
    branch_length=[1.0] * 29,          # 29, not 30
    specific_earth_resistance=100.0,
)
```

Passing `n` lengths used to drop the last one silently — the two
tests in this repository that did so had been asserting against a
shorter line than they thought for as long as they existed —
and passing too few raised a bare `IndexError` from inside the loop.
Both now raise a `ValueError` that states the two counts.

## API reference

::: groundinsight.network_operations

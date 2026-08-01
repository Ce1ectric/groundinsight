# Electrical network

Numerical core of `groundinsight`. `ElectricalNetwork` assembles
the nodal admittance matrix $Y(f)$ per frequency, fills the
right-hand side with source and mutual-coupling Norton currents,
solves the linear system via sparse LU and derives branch
currents, reduction factors and grounding impedances.

## Physical / modelling context

For each frequency $f$ in the network's frequency list the model
reduces to a sparse complex linear system

$$
Y(f)\,\underline{u}(f) \;=\; \underline{i}(f),
\qquad
\underline{u}(f) \;=\; Y(f)^{-1}\,\underline{i}(f),
$$

with

- $Y(f)$ the nodal admittance matrix. The diagonal entries are
  the bus grounding admittances $1/Z_E(f, \rho)$; the
  off-diagonal entries are the branch self-admittances
  $1/Z_b(f, l)$ when `grounding_conductor=True`.
- $\underline{u}(f)$ the bus-EPR vector.
- $\underline{i}(f)$ the source currents (scaled by the
  fault scaling at $f$) plus the Norton equivalents of the
  mutual coupling between phase and grounding conductor along
  the source-to-fault path.

After the per-frequency solve the network derives:

- **Branch currents** from $\Delta u$ and the impressed mutual
  current.
- **Reduction factors** as the ratio
  $|\underline{u}_\text{with}|/|\underline{u}_\text{without}|$ at
  the fault bus, where the second solve omits the mutual
  Norton sources.
- **Grounding impedance**
  $Z_G = u_\text{EPR}/(r \cdot I_\text{fault})$ per frequency.

The `ElectricalNetwork` is held as a `PrivateAttr` of `Network`,
not exposed in the JSON / DB serialisation; it carries the raw
NumPy / SciPy working arrays used by the LU solver.

## Example

```python
import groundinsight as gi

# Assume `net` was built via gi.create_* factories with a fault `f1`.
gi.run_fault(network=net, fault_name="f1")  # populates net._enet under the hood

# Inspect derived quantities through the public Network accessors:
df_bus = net.res_buses()           # EPR per bus
df_branch = net.res_branches()     # branch currents
df_zg = net.res_all_impedances()   # grounding impedance + reduction factor
```

Direct use of `ElectricalNetwork` is rarely needed; access is
mostly via `gi.run_fault` and the `Network.res_*` DataFrame
accessors.

## API reference

::: groundinsight.electrical_network

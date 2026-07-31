# Core models

Pydantic v2 data classes describing the physical network elements,
the configured faults and sources, the per-frequency results and
the `ComplexNumber` helper used throughout the package.

## Physical / modelling context

`groundinsight` represents a grounding network as a labelled,
undirected graph. The model layer owns four kinds of objects:

- **Types** — `BusType`, `BranchType` — carry the SymPy formula
  strings that are compiled to vectorised callables and
  evaluated per `(f, rho, l)` triple. Optional lumped RLC
  formulas (`R_formula`, `L_formula`, `C_formula`,
  `R_self_formula`, `L_self_formula`, `C_self_formula`,
  `R_mutual_formula`, `M_mutual_formula`) parameterise the
  state-space transient solver in
  [`groundinsight.simulation.transient`](transient.md).
- **Instances** — `Bus`, `Branch` — carry concrete numerical
  values (`specific_earth_resistance`, `length`,
  `parallel_coefficient`) plus the per-frequency impedance dict
  $Z(f) \in \mathbb{C}$ that is built from the formulas at
  network-build time.
- **Excitation** — `Source` (current or voltage source per bus)
  and `Fault` (which bus, which scaling per frequency).
- **Results** — `Result`, `ResultBus`, `ResultBranch`,
  `ResultReductionFactor`, `ResultGroundingImpedance` — the
  outcome of `run_fault`. Both per-frequency components and the
  RMS over all frequencies (computed as
  $\sqrt{\sum_f |X(f)|^2}$) are stored.

`ComplexNumber` is a small Pydantic wrapper over the native
`complex` type. It exists because `complex` is not natively
JSON-serialisable; the wrapper exposes overloaded arithmetic and
serialises as a `{"real": ..., "imag": ...}` dict.

## Example

```python
import groundinsight as gi
from groundinsight.models.core_models import (
    Bus, BusType, Branch, BranchType, Source, Fault,
    Network, ComplexNumber,
)

# Build a model directly (notebook style)
bt = BusType(name="GroundRod", system_type="Substation",
             voltage_level=20.0,
             impedance_formula="rho/(2*3.14159*1.5)*(1 + j*0.01*f)")
brt = BranchType(name="ShieldCable", grounding_conductor=True,
                 self_impedance_formula="(0.2 + j*0.4*f/50)*l",
                 mutual_impedance_formula="(0.0 + j*0.4*f/50)*l")

net = Network(name="demo", frequencies=[50.0, 150.0])
b1 = Bus(name="b1", type=bt, impedance={},
         specific_earth_resistance=100.0)
b2 = Bus(name="b2", type=bt, impedance={},
         specific_earth_resistance=100.0)
ln = Branch(name="ln", type=brt, from_bus="b1", to_bus="b2",
            length=2.0, self_impedance={}, mutual_impedance={})
src = Source(name="s1", bus="b1", values={50.0: 1.0, 150.0: 0.05})
flt = Fault(name="f1", bus="b2",
            scalings={50.0: 1.0, 150.0: 0.05})

# JSON round-trip — ComplexNumber serialises as {real, imag}
payload = net.model_dump_json(indent=2)
restored = Network.model_validate_json(payload)
```

Polars accessors `net.res_buses()`, `net.res_branches()` and
`net.res_all_impedances()` produce DataFrames suitable for
plotting and reporting.

## Active subset / cache invalidation

`Bus.active` and `Branch.active` are plain Pydantic fields that
flip an instance in or out of the topology used by
[`PathFinder`](pathfinder.md). Two callers therefore matter when a
flag is flipped in-place after `define_paths()` has already
populated `network.paths`:

- `network.paths` itself, which mirrors the *previously* active
  topology.
- The module-level `_GRAPH_CACHE` / `_FIND_PATHS_CACHE` in
  [`groundinsight.pathfinder`](pathfinder.md), which mirror the
  same topology fingerprint for fast re-use.

`Network.invalidate_paths()` is the explicit hook for that case:

```python
# Flip a branch out of service mid-notebook and rebuild paths.
net.branches["LN_main"].active = False
net.invalidate_paths()  # drops self.paths + this network's cache
gi.create_paths(net)    # rebuilds with the new topology
```

The invalidation is **scoped to the calling `Network` instance**:
cache entries belonging to other live networks in the same process
are preserved, so dashboards iterating over a set of feeders do
not pay a global cache eviction every time a single network
mutates.

Since `0.5.0` the invalidation is also an **atomic rebind**
(`self.paths = {}` instead of `self.paths.clear()`), so an external
snapshot `saved = dict(network.paths)` taken before the call keeps
its entries:

```python
saved = dict(network.paths)        # external snapshot
network.invalidate_paths()
# saved still holds the previously-enumerated paths.
```

## Frequency validation and order warning

`Network.frequencies` is validated at construction time:

- Empty / `nan` / `inf` / strictly-negative inputs are rejected with
  a clear `ValueError`. **DC (`f = 0`) is permitted** because the
  FFT transient solver in
  [`groundinsight.simulation.transient`](transient.md) uses the
  zero-frequency bin to carry the steady-state offset.
- Duplicate frequencies are rejected — the same `f` twice in the
  list silently doubled the work in `solve_network` and doubled the
  amplitude of the corresponding spectral bin in the FFT transient
  solver.
- Non-strictly-monotone-increasing inputs **accept** the order but
  emit a `NetworkFrequencyOrderWarning(UserWarning)` (added in
  `0.5.0`). The FFT transient solver maps spectral bins by *position*
  in `Network.frequencies`, so a shuffled or descending list is
  almost always a user error. Mirrors
  `groundfield.solver.engine.EngineFrequencyOrderWarning` so the
  three earthing-platform packages share one convention.

```python
import warnings
import groundinsight as gi

with warnings.catch_warnings():
    warnings.simplefilter("error", gi.NetworkFrequencyOrderWarning)
    # Raises instead of just warning — use during validation.
    gi.create_network(name="net", frequencies=[100.0, 50.0])
```

## Top-level `set_active_fault` factory

The `keep_results=` keyword on `Network.set_active_fault` is also
reachable via the top-level factory wrapper:

```python
import groundinsight as gi

# Re-plot the previously cached Result without recomputing it.
gi.set_active_fault(net, "F1", keep_results=True)
```

Field-level validation guards are documented inline (frequency
duplicate / NaN / negative rejection on `Network.frequencies`,
int-vs-float key coercion on `Fault.scalings`, …). See the
mkdocstrings dump below for the authoritative list.

## API reference

::: groundinsight.models.core_models
    options:
      members:
        - Bus
        - BusType
        - Branch
        - BranchType
        - Source
        - Fault
        - Network
        - Path
        - Result
        - ResultBus
        - ResultBranch
        - ResultReductionFactor
        - ResultGroundingImpedance
        - ComplexNumber

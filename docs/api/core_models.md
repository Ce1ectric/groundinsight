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
plotting and reporting. All three pin their column layout, so an
empty result is an empty frame *with the documented columns*
rather than a 0×0 one.

`frequency_Hz` is `Float64` in all three. `res_buses()` and
`res_branches()` add one **RMS row** per element, marked by the
boolean column `is_rms`; that row carries `null` in
`frequency_Hz`, because it belongs to no single frequency. Filter
per frequency with

```python
# fragment: assumes a solved network `net`
per_50_hz = net.res_buses().filter(pl.col("frequency_Hz") == 50.0)
rms       = net.res_buses().filter(pl.col("is_rms"))
```

!!! warning "Changed after 0.6.0"
    Up to 0.6.0 the RMS row carried the literal string `"RMS"` in
    `frequency_Hz`, which forced the whole column to `Utf8` —
    `filter(pl.col("frequency_Hz") == 50.0)` then matched nothing,
    and `gi.summarize` refused the column. Replace
    `pl.col("frequency_Hz") == "RMS"` with `pl.col("is_rms")` and
    `== "50"` with `== 50.0`.

`res_buses()` also reports `I_inj_A` / `I_inj_degree`, the current
carried by the **earthing conductor** at a source or fault bus.
That is a different current from `I_bus_A`, the share dissipated
into the soil through the electrode — see
[`ResultBus`](#groundinsight.models.core_models.ResultBus) for why
mixing the two is the classic sizing error.

## Key figures of a fault

`net.res_fault(fault)` puts the numbers an assessment starts from side by
side — read from the stored result, nothing is solved. One row per network
frequency and one RMS row (`is_rms` true, `frequency_Hz` null); the schema
is pinned in `gi.RES_FAULT_SCHEMA`:

| Column | Type | Meaning |
|---|---|---|
| `fault` | Utf8 | the fault |
| `fault_bus` | Utf8 | the bus the result was computed for |
| `frequency_Hz` | Float64 | the frequency; null on the RMS row |
| `is_rms` | Boolean | true on the RMS row |
| `I_F_A` | Float64 | magnitude of the fault current `I_F` (`ResultReductionFactor.i_fault`) |
| `I_F_degree` | Float64 | its angle |
| `EPR_fault_V` | Float64 | earth potential rise at the fault bus |
| `EPR_fault_degree` | Float64 | its angle |
| `EPR_max_V` | Float64 | the largest EPR in the network |
| `EPR_max_bus` | Utf8 | the bus it occurs at |
| `r_epr` | Float64 | EPR-based reduction factor (`value`) |
| `r_current` | Float64 | current-based reduction factor, magnitude of `I_E / I_F` (`value_current`) |
| `I_E_A` | Float64 | magnitude of the earth-return current `I_E` (`i_earth`) |
| `U_E_V` | Float64 | earthing voltage of EN 50522, the EPR at the fault bus (`u_earthing`) |
| `Z_E_Ohm` | Float64 | earthing impedance of EN 50522, the driving point at the fault bus (`z_earthing`) |
| `Z_E_degree` | Float64 | its angle |
| `n_diagnostics` | Int64 | records in `Result.diagnostics`, the same on every row |

A value the result marks as undefined is null — for example `r_current`
and `I_E_A` at a frequency without fault current. The RMS row carries the
stored RMS EPR of the fault bus and the largest one of any bus, and the root
sum of squares of `I_F`, `I_E` and `U_E` over the frequencies; ratios,
impedances and angles are null there.

```python
# fragment: assumes a network `net` with the fault "F1"
import polars as pl

gi.run_fault(net, "F1")                     # or keep a result from gi.solve
figures = net.res_fault("F1")
at_50_hz = figures.filter(pl.col("frequency_Hz") == 50.0).row(0, named=True)
print(at_50_hz["EPR_fault_V"], at_50_hz["r_current"], at_50_hz["Z_E_Ohm"])
if at_50_hz["n_diagnostics"]:
    print(net.results["F1"].diagnostics)    # what qualifies these numbers
```

`res_fault` raises a `ValueError` for a name that is not a fault of the
network and a `RuntimeError` for a fault without stored result. It reports
the stored numbers also when the network was edited since —
`net.stale_results()` says whether that is the case.

## Active subset / cache invalidation

`Bus.active` and `Branch.active` are plain Pydantic fields that
flip an instance in or out of the topology. `run_fault` reads them on
every solve: the default `phase_current_mode="auto"` needs no paths
and drops a path set that no longer matches the active topology; the
legacy mode `"paths"` enumerates the paths again. Enumerated paths are
kept in two places that mirror the *previous* topology when a flag is
flipped in-place after `define_paths()` has populated them:

- `network.paths` itself.
- The network's path-search memo (`network.path_cache()`, see
  [`groundinsight.pathfinder`](pathfinder.md)), which is keyed on the
  same topology fingerprint for fast re-use.

`Network.invalidate_paths()` is the explicit hook that drops both:

```python
# fragment: assumes a network `net` with the branch "LN_main" and the fault "F1"
net.branches["LN_main"].active = False   # take a branch out of service
net.invalidate_paths()                   # drop the paths of the old topology
gi.run_fault(net, "F1")                  # solves the new topology
```

Paths of the new topology, as data, come from the enumeration on the
[Pathfinder](pathfinder.md) page.

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
# fragment: assumes a network `network`
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
    try:
        gi.create_network(name="net", frequencies=[100.0, 50.0])
    except gi.NetworkFrequencyOrderWarning as refused:
        print(f"refused: {refused}")
```

## Top-level `set_active_fault` factory

The `keep_results=` keyword on `Network.set_active_fault` is also
reachable via the top-level factory wrapper:

```python
# fragment: assumes a solved network `net` with the fault "F1"
import groundinsight as gi

# Re-plot the previously cached Result without recomputing it.
gi.set_active_fault(net, "F1", keep_results=True)
```

## Editing a network and stale results

A `Bus` or `Branch` stores impedances *derived* from its type, its
`specific_earth_resistance` and — for a branch — its `length`, at the
network's frequencies. The network keeps them consistent while you
edit, and every stored result knows whether it still matches:

```python
import groundinsight as gi

net = gi.create_network(name="demo", frequencies=[50.0, 250.0])
rod = gi.BusType(name="rod", system_type="Tower", voltage_level=20.0,
                 impedance_formula="rho * 0.01 + j*f*1e-4")
cable = gi.BranchType(name="cable", grounding_conductor=True,
                      self_impedance_formula="(0.25 + j*f*0.012)*l",
                      mutual_impedance_formula="(j*f*0.012)*l")
for name in ("a", "b", "c"):
    gi.create_bus(name=name, type=rod, network=net)
gi.create_branch(name="ab", type=cable, from_bus="a", to_bus="b",
                 length=1.0, network=net)
gi.create_branch(name="bc", type=cable, from_bus="b", to_bus="c",
                 length=1.0, network=net)
gi.create_source(name="s", bus="a", values={50.0: 1000.0, 250.0: 100.0},
                 network=net)
gi.create_fault(name="F", bus="c", scalings={50.0: 1.0, 250.0: 1.0},
                network=net)
gi.run_fault(net, "F")

# An input assignment re-derives the element at once ...
net.buses["b"].specific_earth_resistance = 250.0
# ... and the stored result knows that it no longer matches.
print(net.stale_results())          # ['F']

# Several fields at once, all or nothing:
net.update_branch("bc", length=2.5, specific_earth_resistance=200.0)

gi.run_fault(net, "F")
print(net.is_result_stale("F"))     # False
```

- **Assignment re-derives.** `Bus.type`, `Bus.specific_earth_resistance`,
  `Branch.type`, `Branch.length` and `Branch.specific_earth_resistance`
  on an element that belongs to a network re-derive it through the same
  code path as `Network.add_*`; assigning `Network.frequencies`
  re-derives every element. A failing derivation raises and leaves the
  element (or the network) exactly as it was. On an element that belongs
  to no network an assignment only stores the value.
- **`update_bus` / `update_branch`** change several fields of one
  element: every value is validated, the bus references are checked and
  the impedances derived before anything is written. The derived fields
  (`impedance`, `self_impedance`, …) cannot be updated — assign them
  directly to override a value for a study; the next input change
  recomputes them.
- **`remove_bus`** refuses while a branch, source or fault refers to the
  bus and names all of them; `remove_branch`, `remove_source` and
  `remove_fault` (which also drops that fault's stored result) complete
  the set. Unknown names raise `ValueError`, as everywhere else.
- **Names are keys.** Renaming an element that belongs to a network is
  refused — remove it and add a renamed copy. Rewiring a branch, source or
  fault to a bus the network does not have is refused. An element
  belongs to one network at a time; add `element.model_copy(deep=True)`
  to another one.
- **Types are read-only.** A type has been evaluated into every element
  that uses it, so it cannot be changed in place. Create a changed type
  and assign it per element:

    ```python
    rod_2 = rod.model_copy(update={"impedance_formula": "rho * 0.02 + j*f*1e-4"})
    for name, bus in net.buses.items():
        if bus.type == rod:
            net.update_bus(name, type=rod_2)
    ```

**What makes a result stale.** `Network.stale_results()` lists the
faults whose stored result no longer matches the network:
`Network.frequencies`, any bus, branch or source as the solver reads it
(impedances, `active`, the wiring, `parallel_coefficient`, the source
data), or the definition of the result's own fault (`bus`, `scalings`).
Metadata — `description`, `Fault.t_k_s`/`n_factor`, the IEC 60909
characteristics of a source — and *other* faults do not count. The
check compares a digest of the inputs (`Result.input_digest`, written
by `run_fault`) with the network's current inputs, so it is a statement
about content: undoing an edit makes the result current again, and
`outage_context`, `run_sweep` and the rho searches, which restore the
network, leave the flags as they found them. The one result a study
leaves behind computed under *modified* inputs — the last scenario of
`run_outage_study`, the last point of `run_sweep` — is reported stale,
because it is. The digest is saved with the result to JSON and to the
database; results written by an older version carry none and are never
reported stale.

## Formula symbols and resource limits

A formula of a `BusType` is evaluated with `f` (frequency in Hz) and `rho`
(specific earth resistance in Ohm·m), a formula of a `BranchType` with
`f`, `rho` and `l` (length in km). `j` and `I` (imaginary unit), `pi` and
`E` are available in both, together with the functions `sqrt`, `exp`,
`log`/`ln`, `Abs`, `sign`, `conjugate`, `re`, `im`, `arg`, the
trigonometric and hyperbolic functions and their inverses, `Min`, `Max`,
`floor` and `ceiling`. Any other name is rejected when the type is
created, with the case-insensitive match as a suggestion (`L` → `l`).

SymPy computes with the numbers of a formula exactly while it parses it,
so a short string could cost unbounded CPU time. The formula guard
therefore bounds the work before SymPy runs:

| Limit | Value |
|---|---|
| digits of a number literal | 100 (decimal notation only) |
| exponent of a power, written out or computed | 12 |
| digits of an exact intermediate result | 10 000 |
| digits of the integer `floor`/`ceiling` builds | 100 |
| magnitude of an argument of `exp`, `sinh`, `cosh` | 1e100 |
| terms (numbers, names, operations, calls) | 512 |
| function calls / arguments per call | 8 / 3 |

The longest formula in this documentation, the reduced phase-conductor
impedance $Z_\text{red}$ of
[Bus and branch types from data sheets](../howto/types-from-datasheets.md),
has 99 terms and 6 calls, number literals of at most 9 digits and no
exponent above 2 — well inside the limits. Write very small or large
factors with an exponent part — `1e-15` rather than `10**-15`.

::: groundinsight.utils.validations.check_formula_symbols

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
        - RES_FAULT_SCHEMA

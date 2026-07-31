# groundinsight

**Simulation of grounding systems in electrical power grids.**

`groundinsight` is an open-source Python package for analysing networked grounding
systems during single-phase-to-ground faults. It computes the earth potential rise
(EPR), branch (shield) currents, reduction factors and the resulting grounding
impedance at the fault location for arbitrary bus/branch topologies including
line, ring and mesh networks.

[![PyPI version](https://img.shields.io/pypi/v/groundinsight.svg)](https://pypi.org/project/groundinsight/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Python](https://img.shields.io/pypi/pyversions/groundinsight.svg)](https://pypi.org/project/groundinsight/)

## What groundinsight does

Modern medium-voltage distribution networks are meshed through shared grounding
conductors (cable shields, overhead-line earth wires, substation grounding
grids). During a single-phase-to-ground fault, current returns to the source via
this grounding network. To assess touch-voltage safety and EMC effects, two
questions have to be answered for a given fault location:

- How much of the fault current actually flows through the local earth (as
  opposed to returning through the cable shield)? This is captured by the
  *reduction factor* $r$.
- What earth-potential rise does the grounding grid reach, and what is the
  effective grounding impedance $Z_G$ seen at the fault bus?

`groundinsight` answers both questions by assembling a nodal-admittance model
from user-defined frequency- and $\rho_E$-dependent impedance formulas and
solving it for every harmonic of interest.

## Governing equation

All computations happen per frequency $f$ in the phasor domain:

$$
Y(f)\,\underline{u}(f) = \underline{i}(f) \quad\Longrightarrow\quad
\underline{u}(f) = Y(f)^{-1}\,\underline{i}(f)
$$

where $Y$ is the nodal admittance matrix (bus grounding admittances on the
diagonal, branch self-admittances off-diagonal), $\underline{u}$ is the EPR
vector and $\underline{i}$ combines injected source currents and the Norton
equivalents of the mutual coupling between phase and shield conductors.

## Minimal example

```python
import groundinsight as gi

net = gi.create_network(name="Demo", frequencies=[50])

bus_type = gi.BusType(
    name="SubstationBus", system_type="Substation", voltage_level=20,
    impedance_formula="rho * 0.01 + j * f * 1/50 * 0.1",
)
cable_type = gi.BranchType(
    name="MSCable", grounding_conductor=True,
    self_impedance_formula="(0.25 + j * f * 0.012) * l",
    mutual_impedance_formula="(0.0  + j * f * 0.012) * l",
)

gi.create_bus(name="src", type=bus_type, network=net)
gi.create_bus(name="flt", type=bus_type, network=net)
gi.create_branch(
    name="c1", type=cable_type,
    from_bus="src", to_bus="flt", length=5.0, network=net,
)
gi.create_source(name="infeed", bus="src", values={50: 1000.0}, network=net)
gi.create_fault(name="f1", bus="flt", scalings={50: 1.0}, network=net)

gi.run_fault(network=net, fault_name="f1")
print(net.res_all_impedances())
```

See the [Quickstart](quickstart.md) for the full walkthrough.

## Feature overview

- Bus, branch, source and fault objects modelled as Pydantic v2 classes
- Symbolic impedance formulas in $\rho_E$, $f$ and line length $l$, evaluated
  through SymPy
- Sparse LU solver for every frequency (SciPy `splu`)
- Mutual coupling treated as Norton sources along the path from source to fault
- Reduction factors computed from the ratio of EPR with and without mutual
  coupling at the fault bus
- SQLite persistence and JSON export/import of networks and type libraries
- Polars DataFrames for result access; Matplotlib helpers for bar plots
- **Time-domain transient simulation** (FFT and modified-nodal-analysis
  state-space solvers) on top of the same Pydantic network, see
  [Transient simulation](transient.md)
- Import of external distribution networks from pandapower — the topology
  via `from_pandapower`, and a solved short-circuit case as IEC 60909
  quantities via `read_shortcircuit_results` /
  `apply_shortcircuit_characteristics`, see [I/O](api/io.md)
- **Conductor thermal-limit check** (`check_conductor_limits`): the
  IEC 60909 thermally equivalent current $I_{th}$ against the IEC 60949
  adiabatic limit $k\,S/\sqrt{t_k}$, per grounding branch, see
  [Analysis](api/analysis.md)

## Where to go next

- [Installation](installation.md) — how to get `groundinsight` onto your system.
- [Quickstart](quickstart.md) — end-to-end walkthrough of a minimal network.
- [Concepts](concepts.md) — the physical and numerical model behind the code.
- [Transient simulation](transient.md) — FFT and state-space solver paths for
  time-domain studies on top of the stationary network.
- [Examples](examples/index.md) — notebooks covering the minimal case,
  stationary and transient MV ring studies, a pandapower import and a
  fault sweep across an MV cable line.
- [API reference](api/index.md) — function-by-function documentation.

## Citation

If you use `groundinsight` for scientific work, please cite it using the
[`CITATION.cff`](https://github.com/Ce1ectric/groundinsight/blob/main/CITATION.cff)
metadata shipped with the repository.

## License

`groundinsight` is released under the [MIT License](https://github.com/Ce1ectric/groundinsight/blob/main/LICENSE).

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

## Where to go next

- [Installation](installation.md) — how to get `groundinsight` onto your system.
- [Quickstart](quickstart.md) — end-to-end walkthrough of a minimal network.
- [Concepts](concepts.md) — the physical and numerical model behind the code.
- [Examples](examples/index.md) — notebooks covering the simple case, a CIRED
  reference and a low-voltage network.
- [API reference](api/index.md) — function-by-function documentation.

## Citation

If you use `groundinsight` for scientific work, please cite it using the
[`CITATION.cff`](https://github.com/Ce1ectric/groundinsight/blob/main/CITATION.cff)
metadata shipped with the repository.

## License

`groundinsight` is released under the [MIT License](https://github.com/Ce1ectric/groundinsight/blob/main/LICENSE).

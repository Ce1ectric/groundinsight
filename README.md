# groundinsight

**Simulation of grounding systems in electrical power grids.**

[![PyPI version](https://img.shields.io/pypi/v/groundinsight.svg)](https://pypi.org/project/groundinsight/)
[![Python versions](https://img.shields.io/pypi/pyversions/groundinsight.svg)](https://pypi.org/project/groundinsight/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Documentation](https://img.shields.io/badge/docs-mkdocs--material-blue)](https://ce1ectric.github.io/groundinsight/)

`groundinsight` is an open-source Python package for networked grounding
systems during single-phase-to-ground faults. For arbitrary bus/branch
topologies — lines, rings, meshes — it computes the earth potential rise
(EPR) of every station, the currents in cable shields and earth wires, the
earthing impedance and both reduction factors at the fault location, per
frequency, from impedance formulas you define.

- **Documentation**: <https://ce1ectric.github.io/groundinsight/>
- **Source**: <https://github.com/Ce1ectric/groundinsight>
- **Issue tracker**: <https://github.com/Ce1ectric/groundinsight/issues>

> **Scope of the results.** groundinsight is a research and engineering tool.
> Its results support, but do not replace, an assessment according to
> EN 50522 / IEC 61936-1 by a qualified person, including the measurements
> the standards require. The software is provided under the MIT License,
> without warranty of any kind.

## Why groundinsight

Medium-voltage networks are meshed through their grounding conductors: cable
shields, overhead-line earth wires, the earthing grids of the substations.
During a single-phase-to-ground fault the return current divides between the
soil at the fault location and these metallic paths. An earthing study needs
to know how it divides:

- the **reduction factor** $r$ — the share of the fault current that returns
  through the soil, and
- the **earthing impedance** $Z_E$ and the **earth potential rise** $U_E$ at
  the fault, with the potentials they carry to the neighbouring stations.

`groundinsight` assembles a nodal-admittance model of the grounding system
from frequency- and soil-resistivity-dependent impedance formulas and solves
it for every frequency of interest.

## Features

- **Model.** Networks, bus and branch types, buses, branches, sources and
  faults as Pydantic v2 objects. Impedances are formulas in the soil
  resistivity `rho`, the frequency `f` and the length `l`, checked before
  they are parsed and evaluated through SymPy. Any list of frequencies,
  `f = 0` (direct current) included.
- **Solve and results.** `gi.run_fault` solves a fault and keeps the result
  on the network; `gi.solve` returns it and leaves the network untouched;
  `gi.check_network` reports every problem of a network as structured
  diagnostics instead of raising. `Network.res_fault` gives the key figures
  of a fault — EPR, $U_E$, $Z_E$, $I_E$, both reduction factors — one row per
  frequency and an RMS row, with the schema `gi.RES_FAULT_SCHEMA`;
  `res_buses`, `res_branches` and `res_all_impedances` give the rest, all as
  Polars DataFrames.
- **Diagnostics.** Every condition that changes how a result must be read —
  a heuristic phase split, a frequency the fault scalings do not cover, a
  reactance without meaning at 0 Hz — is a warning with a stable code and the
  elements it concerns, collected in `Result.diagnostics`. The library logs
  nothing unless asked (`gi.set_log_level`).
- **Rings and meshes.** The phase current divides over rings, meshes and
  parallel cables as a solve of the phase-conductor network says
  (`phase_current_mode="auto"`, the default);
  `BranchType.phase_impedance_formula` describes the faulted conductor.
- **Two reduction factors.** `value_current` (`r_current`) is the share of
  the fault current returning through the soil, the reduction factor of
  EN 50522; `value` (`r_epr`) is the EPR ratio with and without coupling,
  $|1 - Z_m/Z_s|$ for a homogeneous run.
- **A sparse kernel.** The nodal matrices are assembled and factorised
  sparse. Measured for the changelog (CPU time, seven frequencies): a
  2000-bus ring in 0.22 s instead of 4.04 s, a 4000-bus ring in 0.53 s where
  the dense kernel needed 2.3 GB of memory, an 8000-bus ring in 1.16 s with a
  peak of 203 MB.
- **Analysis.** Splitting the network at the fault (`gi.analyze_cuts`), the
  response of a location to every electrode from two solves
  (`gi.bus_response`), parameter sweeps with statistics and classes
  (`gi.run_sweep`, `gi.summarize`, `gi.classify`), outage studies
  (`gi.run_outage_study`), the admissible soil resistivity
  (`gi.find_max_rho_scaling`), the thermal check of earthing conductors after
  IEC 60949 / IEC 60909-0 (`gi.check_conductor_limits`), and closed-form
  reference cases the package checks itself against
  (`gi.run_reference_cases`).
- **Transient studies.** `gi.TransientStudy` with an FFT solver and a
  state-space solver that models the coupling.
- **Data in and out.** Types derived from cable and electrode data sheets
  (how-to guide with a 20 kV cable), the pandapower importer
  (`gi.from_pandapower`, extra `pandapower`), project files in JSON with a
  `schema_version`, and `gi.Database` for type libraries and networks in
  SQLite — older files load, newer ones are refused with a clear message.
- **Service layer, command line and GUI.** `groundinsight.api` offers project
  sessions, undoable edits, background jobs and JSON-safe results for front
  ends; the command line `groundinsight` checks and solves a project file
  from a shell; the separate distribution `groundinsight-gui` is a graphical
  front end at an early stage.

## Installation

`groundinsight` requires **Python 3.12 or newer** and is published on PyPI:

```bash
pip install groundinsight
pip install "groundinsight[pandapower]"    # with the pandapower importer
```

The [installation page](https://ce1ectric.github.io/groundinsight/getting-started/installation/)
covers virtual environments, Jupyter, the graphical front end and a
self-test that takes a second.

## Quickstart

Two substations, joined by a 5 km cable whose shield is earthed at both
ends; an earth fault at one draws 1000 A from the infeed at the other:

```python
import groundinsight as gi

net = gi.create_network(name="first_calculation", frequencies=[50.0])

station = gi.BusType(
    name="SubstationBus", system_type="Substation", voltage_level=20.0,
    impedance_formula="rho * 0.01 + j * f * 1/50 * 0.1",    # electrode Z_B
)
cable = gi.BranchType(
    name="MSCable", grounding_conductor=True,
    self_impedance_formula="(0.25 + j * f * 0.012) * l",    # shield-earth loop Z_s
    mutual_impedance_formula="(0.0 + j * f * 0.012) * l",   # phase-shield coupling Z_m
)

gi.create_bus(name="bus_source", type=station, network=net)
gi.create_bus(name="bus_fault", type=station, network=net)
gi.create_branch(name="cable_1", type=cable, from_bus="bus_source",
                 to_bus="bus_fault", length=5.0, network=net)
gi.create_source(name="infeed", bus="bus_source", values={50.0: 1000.0}, network=net)
gi.create_fault(name="earth_fault", bus="bus_fault", network=net)

result = gi.run_fault(net, fault="earth_fault")
key = net.res_fault("earth_fault").row(0, named=True)   # the 50 Hz row
print(f"U_E = {key['U_E_V']:.2f} V, Z_E = {key['Z_E_Ohm']:.4f} Ohm, "
      f"r = {key['r_current']:.6f}, EPR ratio = {key['r_epr']:.6f}")
print("diagnostics:", [d.code for d in result.diagnostics])
```

```text
U_E = 275.43 V, Z_E = 0.8440 Ohm, r = 0.274064, EPR ratio = 0.384615
diagnostics: []
```

The tutorial [Your first fault calculation](https://ce1ectric.github.io/groundinsight/getting-started/first-calculation/)
builds this network step by step and computes every number a second time by
hand; [From a line to a ring](https://ce1ectric.github.io/groundinsight/getting-started/ring/)
continues with the same cable as a ring.

## Model overview

Every frequency $f$ is solved on its own, in the phasor domain:

$$
Y(f)\,\underline{u}(f) = \underline{i}(f)
$$

$Y$ is the nodal admittance matrix of the grounding system — the electrodes
of the stations to remote earth, the cable shields and earth wires between
them — and $\underline{u}$ holds the earth potentials of the stations. The
fault current drives $\underline{i}$, and the current in each phase conductor
induces a voltage along the shield it runs in; those inductive couplings
enter as Norton current sources. At the fault bus the results follow the
EN 50522 quantities:

| Quantity | Definition in groundinsight |
|---|---|
| $U_E$ | the earth potential rise of the fault bus |
| $Z_E$ | the driving-point impedance of the grounding system at the fault bus: what a measurement there against remote earth would see, with everything bonded to the station |
| $I_E$ | the earth current: the part of the fault current that returns through the soil |
| $r$ | $\lvert I_E\rvert / \lvert 3I_0\rvert$ (`value_current`), the reduction factor of EN 50522 |
| EPR ratio | EPR at the fault with the coupling over EPR without (`value`), the reduction factor of a cable |

EN 50522 writes the chain $U_E = r \cdot 3I_0 \cdot Z_E$. `groundinsight`
computes the three quantities independently, and the chain holds where the
standard's picture of a long bonded run applies. The model, its assumptions
and their limits are described under
[Understanding the model](https://ce1ectric.github.io/groundinsight/explanation/).

## Documentation

- [Getting started](https://ce1ectric.github.io/groundinsight/getting-started/) —
  installation and two tutorials with every number checked by hand.
- [How-to guides](https://ce1ectric.github.io/groundinsight/howto/types-from-datasheets/) —
  types from data sheets, harmonics and DC, the EN 50522 check, sweeps,
  outages, inverse rho, pandapower, thermal and transient studies,
  persistence, large networks, diagnostics, the service layer.
- [Understanding the model](https://ce1ectric.github.io/groundinsight/explanation/) —
  the physics and numerics behind the results.
- [Examples](https://ce1ectric.github.io/groundinsight/examples/) — notebooks,
  executed when the site is built.
- [Reference](https://ce1ectric.github.io/groundinsight/reference/results/) —
  result tables, conventions and units, formula syntax, warnings, glossary
  and the API.

## Contributing

Contributions are welcome. [CONTRIBUTING.md](https://github.com/Ce1ectric/groundinsight/blob/main/CONTRIBUTING.md)
describes how to run the tests and build the documentation, and the
conventions a pull request is reviewed against. Changes are listed in the
[changelog](https://github.com/Ce1ectric/groundinsight/blob/main/CHANGELOG.md).

## Citation

If you use `groundinsight` in scientific work, please cite it with the
metadata in [CITATION.cff](https://github.com/Ce1ectric/groundinsight/blob/main/CITATION.cff);
the [citation page](https://ce1ectric.github.io/groundinsight/project/citation/)
of the documentation has it as BibTeX and APA.

## License

`groundinsight` is released under the
[MIT License](https://github.com/Ce1ectric/groundinsight/blob/main/LICENSE).

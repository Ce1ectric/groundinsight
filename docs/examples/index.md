# Examples

The examples are Jupyter notebooks. They are stored without outputs and
executed when this site is built, so every table and figure on these pages
is the output of the code next to it, run against the documented release —
a notebook that fails stops the build. Each one states the numbers it
expects and checks them a second way: against a closed form, a nodal solve
written in NumPy, or a second solver.

## First steps with the model

- **[Minimal example](minimal.ipynb)** — three stations on a cable line,
  one source, one fault: build, solve, read the results as tables, and check
  four of them against Kirchhoff's laws and the closed forms of a line. The
  tutorial [Your first fault calculation](../getting-started/first-calculation.md)
  takes the same steps more slowly.
- **[MV ring (stationary)](mv_ring.ipynb)** — a 20 kV ring with 20
  substations in the frequency domain: earth potential rise per bus, both
  reduction factors and the earthing impedance at the fault.
- **[MV ring (transient)](mv_ring_transient.ipynb)** — the same ring in the
  time domain: the FFT and the state-space solver, each against the
  stationary solve it must reproduce, a fault current with DC offset, and a
  Thévenin source switched onto the ring.
- **[Pandapower import](pandapower_import.ipynb)** — preview and convert a
  pandapower network with `preview_pandapower_import` and `from_pandapower`,
  add a fault, solve, and run an outage study on the result.
- **[Fault sweep](fault_sweep.ipynb)** — the fault moved over every bus of
  a 10-bus cable line at five frequencies: earthing impedance and both
  reduction factors per fault location, with `Network.res_all_impedances()`.

## Analysis

- **[Fault decomposition](fault_decomposition.ipynb)** — the analysis layer
  on top of a solve: the two reduction factors, `analyze_cuts`,
  `bus_response`, `run_sweep` with `summarize` and `classify`, and the
  closed-form reference cases.
- **[Electrode at the fault location](cuts_and_rho_f_sweep.ipynb)** — which
  electrode does the faulted station need? The network split at the fault
  into what belongs to the network and what to the electrode, a catalogue of
  electrodes in the $\rho$–$f$ form through `run_sweep`, statistics and
  classes on the stacked results.
- **[Location response bracket](bus_response_extremes.ipynb)** — a station
  characterised without knowing its electrode: the response to every
  electrode from two solves, the bracket of no electrode, an ideal one and
  the passive worst case, and what that bracket does not cover.
- **[Phase currents in meshes](mesh_phase_currents.ipynb)** — how the fault
  current divides in rings, meshes and parallel cables, what the legacy
  `paths` mode makes of it, and what `phase_impedance_formula` changes —
  checked against a fully coupled multi-conductor solve.
- **[Direct-current studies](dc_studies.ipynb)** — `f = 0` as an ordinary
  frequency: removable singularities, dropped reactances, ideal bonds and
  true poles, each against a reference that does not come from
  `groundinsight`.
- **[Thermal limits](thermal_limits.ipynb)** — whether an earthing conductor
  survives the fault current, after IEC 60949 and IEC 60909-0.

## Run them yourself

The button at the top of each notebook page downloads the notebook, without
outputs. To run it, install the package with the pandapower extra and
JupyterLab into a virtual environment
([Installation](../getting-started/installation.md)):

```bash
pip install "groundinsight[pandapower]" jupyterlab
jupyter lab
```

To run all of them from a clone of the repository instead:

```bash
git clone https://github.com/Ce1ectric/groundinsight.git
cd groundinsight
pip install -e ".[pandapower]" jupyterlab
jupyter lab docs/examples/
```

# Examples

The examples are Jupyter notebooks that walk through representative
`groundinsight` workflows. They are rendered into this site via
[`mkdocs-jupyter`](https://pypi.org/project/mkdocs-jupyter/), so all
figures and DataFrame outputs are visible inline.

## Notebooks in this section

- **[Minimal example](minimal.ipynb)** — three-bus MV cable line with a
  single source and one fault. The smallest possible working example;
  includes the analytical plausibility checks (Kirchhoff at the fault
  bus, reduction factor `r = |1 - Z_mutual / Z_self|`). Use this as a
  starting point for your own networks.
- **[MV ring (stationary)](mv_ring.ipynb)** — classical frequency-domain
  fault analysis of a 20 kV ring with 20 substations and a single-core
  shielded cable type. Shows EPR per bus, reduction factor and
  grounding impedance at the fault bus.
- **[MV ring (transient)](mv_ring_transient.ipynb)** — same 20 kV ring,
  full transient stack: FFT vs state-space comparison, the effect of
  the cable-shield mutual coupling on the fault-bus EPR, and a Thévenin
  voltage source switched onto the ring.
- **[Pandapower import](pandapower_import.ipynb)** — end-to-end use of
  `gi.preview_pandapower_import` and `gi.from_pandapower`: build a small
  pandapower MV network, convert it, inject a fault, and run a
  follow-up outage study on the imported network.
- **[Fault sweep](fault_sweep.ipynb)** — sweep the fault location across
  every bus of a 10-bus MV cable line and visualise the resulting
  grounding impedance and reduction factor per fault bus and frequency.
  Uses `Network.res_all_impedances()` for the bookkeeping.

Each notebook is self-contained: it builds the network in code, runs
the fault calculation, extracts the results as Polars DataFrames and
produces the plots inline. Clone the repository to execute them
locally:

```bash
git clone https://github.com/Ce1ectric/groundinsight.git
cd groundinsight
poetry install
poetry run jupyter lab docs/examples/
```

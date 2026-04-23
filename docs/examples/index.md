# Examples

The examples are Jupyter notebooks that reproduce representative fault cases
with `groundinsight`. They are executed offline and rendered into this site
via [`mkdocs-jupyter`](https://pypi.org/project/mkdocs-jupyter/), so all
figures are immediately visible. The raw `.ipynb` files ship with the
repository under `notebooks/`.

## Notebooks in this section

- **[Simple example](simple.ipynb)** — two buses connected by a single cable,
  fault at the remote bus, current source at the feeder. Use this notebook as
  the smallest possible working example and as a starting point for your own
  calculations.
- **[CIRED case](cired.ipynb)** — reproduction of the CIRED reference network:
  medium-voltage feeders with multiple line segments, cable shields and an
  overhead-line earth wire. Exercises reduction-factor computation across a
  mixed line.
- **[Low-voltage network](low_voltage.ipynb)** — a low-voltage distribution
  network with a meshed grounding grid, illustrating how `groundinsight`
  handles multiple parallel return paths through ring and mesh topologies.

Each notebook is self-contained: it creates the network in code, runs the
fault calculation, extracts the results as Polars DataFrames and produces the
plots inline. Clone the repository to execute them locally:

```bash
git clone https://github.com/Ce1ectric/groundinsight.git
cd groundinsight
poetry install
poetry run jupyter lab notebooks/
```

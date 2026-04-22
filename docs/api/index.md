# API reference

The API reference is generated directly from the docstrings in the
`groundinsight` source tree via
[`mkdocstrings`](https://mkdocstrings.github.io/). Every page below corresponds
to one Python module; navigate through the pages to see the public functions,
classes and their signatures.

## Modules

- [Network operations](network_operations.md) — high-level factory functions
  for creating networks, buses, branches, sources, faults, paths, and for
  running fault calculations.
- [Core models](core_models.md) — Pydantic data classes for `Bus`, `Branch`,
  `Source`, `Fault`, `Network`, the different `Result*` containers and the
  `ComplexNumber` helper.
- [Electrical network](electrical_network.md) — numerical core: assembly of
  the admittance matrices, sparse LU solve, reduction-factor and grounding-
  impedance computation.
- [Pathfinder](pathfinder.md) — DFS-based enumeration of every simple path
  from each source bus to the active fault bus.
- [Plotting](plotting.md) — Matplotlib bar-plot helpers for EPR, branch
  currents and bus currents.
- [Database](database.md) — SQLAlchemy CRUD helpers for persisting bus types,
  branch types and entire networks to SQLite.

## Top-level package

The `groundinsight` top-level module re-exports the most commonly used
symbols so that everyday work only requires `import groundinsight as gi`:

```python
import groundinsight as gi

net = gi.create_network(name="...", frequencies=[50])
gi.create_bus(...)
gi.create_branch(...)
gi.create_fault(...)
gi.create_source(...)
gi.run_fault(network=net, fault_name="...")
```

# Pathfinder

Depth-first search over the undirected bus/branch graph that
enumerates every simple path from a source bus to the active
fault bus. The resulting ordered branch lists are used by
`ElectricalNetwork` to inject the mutual-coupling Norton sources
with the correct sign.

## Physical / modelling context

In a meshed grounding network the mutual coupling between the
faulted phase and the grounding return appears only along the
**path that the phase current actually travels** between source
and fault. The phase conductor and its associated grounding
conductor are inductively coupled segment by segment; the
resulting per-segment voltage drop is modelled as a series-EMF
that can be Thevenin–Norton-converted into a current source on
the grounding-side admittance matrix. To assemble those Norton
sources correctly the algorithm has to:

1. enumerate every simple (loop-free) path from the source bus
   to the active fault bus over the undirected
   bus/branch graph,
2. preserve the **traversal direction** along each branch — the
   sign of the impressed Norton current depends on it,
3. take into account the optional `parallel_coefficient` on each
   branch so that mesh and ring topologies receive the correct
   per-path current share.

The DFS enumeration is exhaustive, and its cost is **exponential
in the number of independent loops** — not in the number of buses.
A radial network of 500 buses has two paths and enumerates in
milliseconds; a 5×5 grid of 25 buses already has 8 512 simple
paths, and a 6×6 grid 1 262 816, which takes about a minute.
Treat the enumeration as affordable only for radial and lightly
meshed topologies.

Since 0.6.1 the default `phase_current_mode="auto"` therefore does
**not** enumerate at all. It only needs to know which sources
reach the fault, and `Network.sources_reaching(fault_name)`
answers that with a breadth-first search in `O(V + E)`. The
enumeration runs for the legacy `phase_current_mode="paths"`, for
the transient state-space solver, and whenever you ask for it with
`gi.create_paths(network)`. `net.paths` is consequently empty
after a default `run_fault`.

`PathFinder.find_paths` refuses to enumerate more than
`pathfinder.get_pathfinder_max_paths()` paths (default `100_000`)
and raises a `ValueError` that names the bus pair instead of
running for hours. Adjust it with
`pathfinder.set_pathfinder_max_paths(...)` if you genuinely need
the full set on a meshed network.

## Example

```python
import groundinsight as gi
from groundinsight.pathfinder import PathFinder

# Build network and choose an active fault — see the quickstart
# for the create_* calls.
net.set_active_fault("f1")

# `create_paths` is NOT called by run_fault in the default
# phase_current_mode="auto". To enumerate the paths explicitly:
gi.create_paths(network=net)
for p in net.paths.values():
    print(p.source, "->", p.fault,
          "via", [b.name for b in p.segments])

# Direct use of the PathFinder helper:
pf = PathFinder(net)
all_paths = pf.find_paths(source_bus_name="bus_substation",
                          fault_bus_name="bus_fault")
```

## Module-level caches

`PathFinder` memoises two pieces of work at module scope:

- `pathfinder._GRAPH_CACHE` — the adjacency list built in
  `_build_graph`. Repeated `PathFinder(network)` constructions on
  the same logical topology (the inner loop of
  `analysis.inverse_rho_f.evaluate_max_epr_under_k`) pay the DFS
  cost only on the first call.
- `pathfinder._FIND_PATHS_CACHE` — the `(source, fault) → List[Path]`
  results returned by `find_paths`. `find_paths` returns *copies*
  of the cached `Path` instances so downstream callers (most
  prominently `network_operations.define_paths`) can safely mutate
  `Path.name` / `Path.source` / `Path.fault` without leaking the
  mutation into the cache.

Both caches key on the full topology fingerprint:

```text
(id(network), network.name,
 len(network.buses), len(network.branches),
 frozenset(active_buses), frozenset(active_branches))
```

The structural part (`name`, `len(buses)`, `len(branches)`) is a
defence-in-depth guard against the CPython id-recycling failure
mode: once a `Network` is garbage-collected, Python may legitimately
reuse its `id`; the structural component prevents a false cache hit
on the new, topologically different network.

!!! warning "Mutating a `Network` in place"

    Flipping `Bus.active` / `Branch.active` after `define_paths()`
    has already been called leaves the *previously cached* topology
    in `network.paths` and in the module-level caches. Use
    [`Network.invalidate_paths()`](core_models.md) — it drops the
    caller's own cache entries while preserving the cache for
    other networks live in the same process.

`clear_pathfinder_cache()` accepts an optional `network` argument
to support the same scoping at the function level:

```python
from groundinsight.pathfinder import clear_pathfinder_cache

# Scoped: only clear this network's entries.
clear_pathfinder_cache(net_a)

# Unscoped (test fixtures, recovery paths): drop everything.
clear_pathfinder_cache()
```

## Cache size and LRU eviction (added in 0.5.0)

Both module-level caches are `OrderedDict` instances with an LRU
eviction policy. The default cap is `256` entries per cache, applied
globally across all `Network` instances. A long-running dashboard or
a 100-scenario outage sweep therefore no longer accumulates one cache
entry per visited topology indefinitely.

```python
import groundinsight as gi

# Inspect or change the cap. Returns the previous value.
previous = gi.set_pathfinder_cache_size(64)
print(gi.get_pathfinder_cache_size())  # -> 64
```

The cap is read on every insertion, so a `set_pathfinder_cache_size`
call also evicts already-cached entries in LRU order until the new
cap is satisfied. Tests that want to pin the eviction policy to a
small value typically reset the cap from a pytest fixture.

!!! note "When `outage_context` exits"

    [`outage_context`](outage.md) clears the per-network pathfinder
    cache on **exit** as well as on entry (added in `0.5.0`). The
    resident footprint after a long outage sweep therefore matches the
    externally-visible state of the network rather than carrying one
    cache entry per visited scenario forward.

## API reference

::: groundinsight.pathfinder

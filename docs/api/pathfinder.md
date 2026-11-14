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
3. record the branch chain, which the **legacy** phase-current mode
   `run_fault(..., phase_current_mode="paths")` then weights with the
   optional `parallel_coefficient` on each branch.

!!! note "`parallel_coefficient` only applies to `phase_current_mode="paths"`"
    Since 0.6.0 the default mode is `"auto"`, which solves a reduced
    phase-conductor network from `BranchType.phase_impedance_formula`
    instead of splitting a prescribed current over enumerated paths.
    In that mode `parallel_coefficient` is not read at all — the split
    follows from the phase-conductor impedances. Set it only when you
    deliberately run the older `"paths"` mode.

The DFS enumeration is exhaustive, and its cost is **exponential
in the number of independent loops** — not in the number of buses.
A radial network of 500 buses has two paths and enumerates in
milliseconds; a 5×5 grid of 25 buses already has 8 512 simple
paths, and a 6×6 grid 1 262 816, which takes about a minute.
Treat the enumeration as affordable only for radial and lightly
meshed topologies.

Since the first release after 0.6.0 the default
`phase_current_mode="auto"` therefore does **not** enumerate at all. It only needs to know which sources
reach the fault, and `Network.sources_reaching(fault)`
answers that with a breadth-first search in `O(V + E)`. The
enumeration runs for the legacy `phase_current_mode="paths"`, for
the transient state-space solver, and whenever you ask for it with
`gi.create_paths(network)`. `net.paths` is consequently empty
after a default `run_fault`.

`PathFinder.find_paths` refuses to enumerate more than
`network.pathfinder_settings.max_paths` paths (default `100_000`)
and raises a `ValueError` that names the bus pair instead of
running for hours. Raise it for one network with
`network.pathfinder_settings = gi.PathfinderSettings(max_paths=...)`
if you genuinely need the full set on a meshed network.

## Example

```python
# fragment: assumes a network `net` with the fault "f1" and the buses named below
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

## Settings and memo per network

The path search keeps its settings and its memo **per network**
(since audit pass 19). Up to then both were module globals: a limit
set for one network applied to every network of the process —
also to one another thread was solving at that moment — and three
module-level dictionaries, shared by all networks and mutated without
a lock, kept the branches of networks long gone alive.

**Settings.** `gi.PathfinderSettings` holds the two knobs:

| Field | Default | Meaning |
|---|---|---|
| `max_paths` | `100_000` | most simple paths one `(source bus, fault bus)` enumeration may produce |
| `cache_size` | `256` | entries per table of the network's memo (LRU) |

```python
# fragment: assumes a network `net` with the fault "F"
import groundinsight as gi

net.pathfinder_settings = gi.PathfinderSettings(max_paths=1_000_000)
gi.run_fault(net, "F", phase_current_mode="paths")   # this network only
net.pathfinder_settings = None                        # back to the defaults
```

The settings are runtime configuration: they are not saved to a
project file or a database, but `copy.deepcopy`, `model_copy` and
`pickle` keep them. `PathFinder(network, settings=...)` overrides them
for one search.

**Memo.** `network.path_cache()` returns the network's
`PathfinderCache`: the adjacency graph built in `_build_graph`, the
`(source, fault) → List[Path]` results of `find_paths` and the
`reaches` answers, each table an LRU of `cache_size` entries keyed on
the active-topology fingerprint

```text
(id(network), network.name,
 len(network.buses), len(network.branches),
 frozenset(active_buses), frozenset(active_branches),
 frozenset((branch, from_bus, to_bus) for every active branch))
```

Repeated `PathFinder(network)` constructions on the same topology (the
inner loop of `analysis.inverse_rho_f.evaluate_max_epr_under_k`, an
outage study returning to its base case) pay the work only once.
`find_paths` returns *copies* of the cached `Path` instances, so callers
such as `define_paths` can set `Path.name` / `source` / `fault` without
touching the memo. Every operation holds the memo's lock. The memo dies
with its network, a copy or an unpickled network starts with an empty
one, and two networks never compare unequal because of what they have
cached.

!!! warning "Mutating a `Network` in place"

    Flipping `Bus.active` / `Branch.active` after `define_paths()`
    has already been called leaves the *previously enumerated* paths
    in `network.paths`. `run_fault` notices it and rebuilds them; for
    your own reads call
    [`Network.invalidate_paths()`](core_models.md), which also clears
    the network's memo — and only that network's.

`clear_pathfinder_cache(network)` clears one network's memo;
`clear_pathfinder_cache()` without an argument clears the memo of every
network alive in the process (test fixtures, recovery paths).

### Deprecated: the module-level switches

`set_pathfinder_max_paths` / `get_pathfinder_max_paths` and
`gi.set_pathfinder_cache_size` / `gi.get_pathfinder_cache_size` still
work and raise a `DeprecationWarning`. They change the process-wide
defaults — `pathfinder.default_pathfinder_settings()`, the settings of
every network without settings of its own — and
`set_pathfinder_cache_size` trims the memo of those networks at once.
They will be removed no earlier than two releases and six months after
the release that deprecated them.

| Before | Now |
|---|---|
| `pathfinder.set_pathfinder_max_paths(n)` | `net.pathfinder_settings = gi.PathfinderSettings(max_paths=n)` |
| `gi.set_pathfinder_cache_size(n)` | `net.pathfinder_settings = gi.PathfinderSettings(cache_size=n)` |
| `pathfinder.get_pathfinder_max_paths()` | `net.pathfinder_settings.max_paths` |

!!! note "When `outage_context` exits"

    [`outage_context`](outage.md) clears the per-network pathfinder
    cache on **exit** as well as on entry (added in `0.5.0`). The
    resident footprint after a long outage sweep therefore matches the
    externally-visible state of the network rather than carrying one
    cache entry per visited scenario forward.

## API reference

::: groundinsight.pathfinder

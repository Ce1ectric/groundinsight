# pathfinder.py

"""
PathFinder Module.

This module provides the `PathFinder` class, which is responsible for identifying all possible paths
between sources and faults within an electrical network. It utilizes Depth-First Search (DFS) to traverse
the network graph and determine the connectivity between different buses through branches.

The primary use cases include:
- Determining all paths from a specific source to a fault point.
- Analyzing the network's topology for fault impact assessment.
- Facilitating impedance and grounding calculations based on identified paths.
"""

from collections import OrderedDict
from typing import List, Dict, Set, Tuple, FrozenSet, Optional
from .models.core_models import Network, Bus, Branch, Path


# Module-level adjacency graph cache. The cache key is the full topology
# fingerprint of the (active subset of the) network plus a structural
# defence-in-depth component ``(name, n_buses, n_branches)`` that
# guards against the CPython id-recycling failure mode: ``id(obj)`` is
# only unique for the lifetime of ``obj``; once a ``Network`` is
# garbage-collected, a freshly-built ``Network`` may legitimately
# receive the same Python id. Including a structural fingerprint in
# the key avoids a false cache hit in that case.
#
# Repeated PathFinder constructions on the same logical topology — the
# common case inside the multi-fault sweep in
# ``analysis.inverse_rho_f.evaluate_max_epr_under_k`` — reuse the graph
# instead of rebuilding it. The cache also memoises ``find_paths``
# results per (source_bus, fault_bus).
#
# LRU eviction
# ------------
# The two module-level caches are wrapped in ``OrderedDict`` so the
# pathfinder cache can be evicted in LRU order. Long-running notebooks
# that sweep over many ``Outage`` scenarios or many ``active_subset``
# variations otherwise accumulated one cache entry per visited
# topology without ever reclaiming memory. The cap is global (across
# all networks); call :func:`set_pathfinder_cache_size` to tune it for
# dashboards that need a larger working set or for tests that want to
# pin the eviction policy to a small value.
TopologyKey = Tuple[
    int, str, int, int, FrozenSet[str], FrozenSet[str], FrozenSet[Tuple[str, str]]
]
_DEFAULT_CACHE_SIZE = 256
_CACHE_SIZE = _DEFAULT_CACHE_SIZE
_GRAPH_CACHE: "OrderedDict[TopologyKey, Dict[str, List[Branch]]]" = OrderedDict()
_FIND_PATHS_CACHE: "OrderedDict[Tuple[int, str, int, int, FrozenSet[str], FrozenSet[str], str, str], List[Path]]" = OrderedDict()


def _cache_set(cache: "OrderedDict", key, value) -> None:
    """Insert ``value`` at ``key`` and evict the LRU entry if over cap.

    The cap is read from the module-level :data:`_CACHE_SIZE` at every
    insertion so that :func:`set_pathfinder_cache_size` takes effect
    immediately without a manual eviction sweep.
    """
    if key in cache:
        cache.move_to_end(key)
    cache[key] = value
    while len(cache) > _CACHE_SIZE:
        cache.popitem(last=False)


def _cache_get(cache: "OrderedDict", key):
    """Look up ``key`` and bump LRU recency on a hit.

    Returns ``None`` if the key is missing so callers can distinguish
    the "no entry yet" case from a cached empty list.
    """
    value = cache.get(key)
    if value is not None and key in cache:
        cache.move_to_end(key)
    return value


def set_pathfinder_cache_size(maxsize: int) -> int:
    """Configure the maximum number of cached pathfinder entries.

    Parameters
    ----------
    maxsize : int
        Maximum number of entries kept in both ``_GRAPH_CACHE`` and
        ``_FIND_PATHS_CACHE``. Must be a positive integer. The cap
        applies independently to each cache.

    Returns
    -------
    int
        The previously configured cache size.

    Raises
    ------
    ValueError
        If ``maxsize`` is not a positive integer.

    Notes
    -----
    Reducing the cap also evicts already-present entries in LRU order
    so the new limit is immediately respected. The default value is
    ``256``; long-running dashboards iterating over many outage
    scenarios may want to raise it, tests that explicitly want to
    pin the eviction policy may want to lower it to ``8`` or ``16``.
    """
    global _CACHE_SIZE

    if not isinstance(maxsize, int) or maxsize <= 0:
        raise ValueError(
            f"pathfinder cache size must be a positive integer; got {maxsize!r}."
        )
    previous = _CACHE_SIZE
    _CACHE_SIZE = maxsize
    while len(_GRAPH_CACHE) > _CACHE_SIZE:
        _GRAPH_CACHE.popitem(last=False)
    while len(_FIND_PATHS_CACHE) > _CACHE_SIZE:
        _FIND_PATHS_CACHE.popitem(last=False)
    return previous


def get_pathfinder_cache_size() -> int:
    """Return the currently configured pathfinder cache cap."""
    return _CACHE_SIZE


def clear_pathfinder_cache(network: Optional[Network] = None) -> None:
    """Drop module-level PathFinder caches.

    Parameters
    ----------
    network:
        If ``None`` (default), every cache entry — across *all*
        ``Network`` instances seen by the current Python process — is
        dropped. This is the safe but coarse fallback used by
        e.g. test fixtures or "I lost track of which networks are
        live" recovery paths.

        If a :class:`Network` instance is given, only entries whose key
        is keyed on that exact instance are dropped. This is the
        network-scoped invalidation that :meth:`Network.invalidate_paths`
        invokes so that a second, unrelated network's cache survives.

    Notes
    -----
    Call the network-scoped form after mutating a single :class:`Network`
    in place if the same Python identity will be reused with a changed
    topology. The unscoped form is appropriate for global resets, e.g.
    inside a pytest ``conftest.py`` autouse fixture.
    """
    if network is None:
        _GRAPH_CACHE.clear()
        _FIND_PATHS_CACHE.clear()
        return

    net_id = id(network)
    # Network-scoped invalidation: drop every entry keyed on this exact
    # instance. Scoping by ``id`` only (not by name) means two distinct but
    # equally-named live networks no longer evict each other; the topology
    # key now carries connectivity, so id-recycling cannot cause a false hit.
    for key in [k for k in _GRAPH_CACHE if k[0] == net_id]:
        _GRAPH_CACHE.pop(key, None)
    for key in [k for k in _FIND_PATHS_CACHE if k[0] == net_id]:
        _FIND_PATHS_CACHE.pop(key, None)


class PathFinder:
    """
    Find all simple paths between sources and faults in a network.

    Constructs an adjacency list representation of the network graph and
    uses depth-first search (DFS) to identify all possible paths between
    a given source bus and fault bus. These paths are used to inject the
    mutual-coupling Norton sources with the correct sign in
    :mod:`groundinsight.electrical_network`.

    Parameters
    ----------
    network : Network
        The :class:`Network` instance containing buses and branches.

    Notes
    -----
    Both the adjacency graph and the per-``(source, fault)`` path
    results are cached at module level, keyed on a topology fingerprint
    ``(id(network), name, n_buses, n_branches, frozenset(active_buses),
    frozenset(active_branches))``. Repeated constructions over the same
    logical topology — for example the inner loop of
    :func:`groundinsight.analysis.evaluate_max_epr_under_k` — therefore
    pay the DFS cost only on the first call. Call
    :func:`clear_pathfinder_cache` if you mutate a :class:`Network` in
    place under the same Python identity with a changed topology.
    """

    def __init__(self, network: Network):
        """
        Build the adjacency-list representation of ``network``.

        Parameters
        ----------
        network : Network
            The Network instance containing buses and branches.
        """

        self.network = network
        self._topology_key = self._compute_topology_key()
        graph = _cache_get(_GRAPH_CACHE, self._topology_key)
        if graph is None:
            graph = self._build_graph()
            _cache_set(_GRAPH_CACHE, self._topology_key, graph)
        self.graph = graph

    def _compute_topology_key(self) -> TopologyKey:
        """Fingerprint the (active subset of the) network for the cache.

        Includes ``id(network)`` (cheap, fast path for repeated lookups
        on the same instance) *plus* a structural fingerprint
        ``(network.name, len(buses), len(branches))``. The structural
        component is required because CPython recycles ``id`` values
        once an object is garbage-collected; two networks with
        different topologies but the same recycled id would otherwise
        collide.
        """
        active_buses = frozenset(
            name
            for name, bus in self.network.buses.items()
            if getattr(bus, "active", True)
        )
        active_branches = frozenset(
            name
            for name, branch in self.network.branches.items()
            if getattr(branch, "active", True)
        )
        # Connectivity of every active branch as ``(name, from_bus, to_bus)``.
        # Including it makes the key robust to in-place rewiring AND makes an
        # id-recycled key collide only with a *structurally identical*
        # topology, where reusing the cached graph is correct.
        #
        # The branch *name* is part of the entry on purpose. An earlier
        # revision stored bare ``(from_bus, to_bus)`` pairs, which is a set of
        # endpoints and therefore loses the multiplicity of parallel branches:
        # with ``L3: A->D`` and ``L4: A->D`` present, rewiring ``L4`` to
        # ``A->B`` (where ``L1: A->B`` already exists) left the pair set
        # ``{(A,B), (B,C), (A,D)}`` bit-identical, so the stale adjacency list
        # was served from the cache and the second route ``A->B->C`` was never
        # enumerated. See the twin fingerprint in
        # ``Network._active_topology_fingerprint``.
        active_connectivity = frozenset(
            (name, branch.from_bus, branch.to_bus)
            for name, branch in self.network.branches.items()
            if getattr(branch, "active", True)
            and branch.from_bus in active_buses
            and branch.to_bus in active_buses
        )
        return (
            id(self.network),
            self.network.name,
            len(self.network.buses),
            len(self.network.branches),
            active_buses,
            active_branches,
            active_connectivity,
        )

    def _build_graph(self) -> Dict[str, List[Branch]]:
        """
        Build an adjacency list representation of the network graph.

        Each bus is mapped to a list of branches connected to it, facilitating efficient traversal.
        Inactive buses (``Bus.active=False``) are excluded from the graph entirely.
        Inactive branches (``Branch.active=False``) and branches that touch an inactive
        bus on either end are also dropped, so DFS will never traverse them.

        Returns
        -------
        Dict[str, List[Branch]]
            An adjacency list where keys are bus names and values are lists of connected branches.

        """
        graph = {
            bus_name: []
            for bus_name, bus in self.network.buses.items()
            if bus.active
        }

        for branch in self.network.branches.values():
            if not branch.active:
                continue
            if branch.from_bus not in graph or branch.to_bus not in graph:
                continue
            # Assuming undirected graph, add branch to both from_bus and to_bus entries
            graph[branch.from_bus].append(branch)
            graph[branch.to_bus].append(branch)
        return graph

    def find_paths(self, source_bus_name: str, fault_bus_name: str) -> List[Path]:
        """
        Find all simple paths between a source bus and a fault bus.

        Uses depth-first search (DFS) to enumerate every loop-free route
        from the source to the fault over the active sub-graph.

        Parameters
        ----------
        source_bus_name : str
            The name of the source bus.
        fault_bus_name : str
            The name of the fault bus.

        Returns
        -------
        list of Path
            All discovered paths, each as an ordered branch list.
        """
        cache_key = (
            *self._topology_key,
            source_bus_name,
            fault_bus_name,
        )
        cached = _cache_get(_FIND_PATHS_CACHE, cache_key)
        if cached is not None:
            # Return *copies* — callers (notably ``define_paths``) mutate
            # the returned ``Path`` instances (assigning ``name``,
            # ``source``, ``fault``) and that mutation must not bleed into
            # the cache.
            return [
                Path(
                    name="",
                    source="",
                    fault="",
                    segments=list(p.segments),
                )
                for p in cached
            ]

        all_paths = []
        visited_buses = set()
        path = []

        # Source or fault on an inactive (filtered) bus -> no path possible.
        if source_bus_name not in self.graph or fault_bus_name not in self.graph:
            _cache_set(_FIND_PATHS_CACHE, cache_key, [])
            return []

        self._dfs(source_bus_name, fault_bus_name, visited_buses, path, all_paths)

        # Convert each list of Branch objects to a Path object
        paths = []
        for branch_path in all_paths:
            path = Path(
                name="",  # Name will be assigned in define_paths()
                source="",  # Will be set in define_paths()
                fault="",  # Will be set in define_paths()
                segments=branch_path,
            )
            paths.append(path)
        _cache_set(_FIND_PATHS_CACHE, cache_key, paths)
        return [
            Path(
                name="",
                source="",
                fault="",
                segments=list(p.segments),
            )
            for p in paths
        ]

    def _dfs(
        self,
        current_bus: str,
        target_bus: str,
        visited_buses: Set[str],
        path: List[Branch],
        all_paths: List[List[Branch]],
    ):
        """
        Perform Depth-First Search (DFS) to find all paths from current_bus to target_bus.

        This recursive helper function explores all possible routes without revisiting buses.

        Parameters
        ----------
        current_bus : str
            The current bus being visited.
        target_bus : str
            The target fault bus.
        visited_buses : Set[str]
            A set of already visited buses to prevent cycles.
        path : List[Branch]
            The current path being explored.
        all_paths : List[List[Branch]]
            A list to store all discovered paths.

        """
        if current_bus == target_bus:
            # Found a path
            all_paths.append(list(path))
            return

        visited_buses.add(current_bus)

        for branch in self.graph[current_bus]:
            # Determine the neighbor bus (the other end of the branch)
            neighbor_bus = (
                branch.to_bus if branch.from_bus == current_bus else branch.from_bus
            )

            if neighbor_bus not in visited_buses:
                # Add branch to path
                path.append(branch)
                # Recursive call
                self._dfs(neighbor_bus, target_bus, visited_buses, path, all_paths)
                # Backtrack
                path.pop()

        visited_buses.remove(current_bus)

    def _bus_path_to_segments(self, bus_path: List[str]) -> List[Branch]:
        """
        Convert a list of bus names into a list of branches (segments).

        Translates a sequence of buses into the corresponding branches connecting them.

        Parameters
        ----------
        bus_path : List[str]
            A list of bus names representing the path.

        Returns
        -------
        List[Branch]
            A list of `Branch` instances that form the path.

        Raises
        ------
        ValueError
            If no branch is found between consecutive buses in the path.

        """
        segments = []
        for i in range(len(bus_path) - 1):
            from_bus = bus_path[i]
            to_bus = bus_path[i + 1]
            # Find the branch connecting from_bus and to_bus
            branch = self._find_branch_between_buses(from_bus, to_bus)
            if branch:
                segments.append(branch)
            else:
                raise ValueError(f"No branch found between {from_bus} and {to_bus}")
        return segments

    def _find_branch_between_buses(self, from_bus: str, to_bus: str) -> Branch:
        """
        Find the branch connecting two specified buses.

        Searches for a branch that connects the `from_bus` and `to_bus` directly.

        Parameters
        ----------
        from_bus : str
            The name of the originating bus.
        to_bus : str
            The name of the terminating bus.

        Returns
        -------
        Branch
            The `Branch` instance connecting the two buses.

        Raises
        ------
        ValueError
            If no branch connects the specified buses.

        """
        for branch in self.network.branches.values():
            if (branch.from_bus == from_bus and branch.to_bus == to_bus) or (
                branch.from_bus == to_bus and branch.to_bus == from_bus
            ):
                return branch
        return None

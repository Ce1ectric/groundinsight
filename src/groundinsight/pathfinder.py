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

Settings and memo belong to the network
---------------------------------------
Up to audit pass 19 the search kept its limits in module globals
(``set_pathfinder_max_paths``, ``set_pathfinder_cache_size``) and its memo in
three module-level dictionaries keyed by ``id(network)``. A limit set for one
network applied to every network of the process -- also to one that another
thread was solving at that moment -- the dictionaries were mutated from
several threads without a lock, and they kept the branches of networks long
gone alive until the LRU evicted them (finding ARC2).

Both now belong to the network:

* :class:`PathfinderSettings` (``max_paths``, ``cache_size``) is runtime
  configuration of one network, set with ``network.pathfinder_settings =
  PathfinderSettings(...)``. It is not saved with the network, but copies keep
  it. A network without settings uses the process-wide defaults, which only
  the deprecated module-level switches change.
* :class:`PathfinderCache` is the memo of one network -- adjacency graphs,
  enumerated paths and reachability answers per active topology, each table
  an LRU of ``cache_size`` entries guarded by a lock. ``Network.path_cache()``
  hands it out; it dies with its network, a copy or an unpickled network
  starts with an empty one, and it takes no part in comparing networks.

The deprecated switches still work, with a ``DeprecationWarning``: they change
the process-wide defaults and therefore every network without settings of
its own. A small registry of live caches (weak references) remains so that
``clear_pathfinder_cache()`` and the deprecated cache-size switch can reach
them; the search itself never reads it.
"""

import threading
import warnings
import weakref
from collections import OrderedDict, deque
from typing import Any, Dict, FrozenSet, Hashable, List, Optional, Set, Tuple

from pydantic import BaseModel, ConfigDict, Field

from .models.core_models import Network, Branch, Path

TopologyKey = Tuple[
    int, str, int, int, FrozenSet[str], FrozenSet[str], FrozenSet[Tuple[str, str]]
]

# Upper bound on the number of simple paths a single ``find_paths`` call may
# enumerate. The count is exponential in the number of independent loops: a
# 5x5 grid has 8 512 simple paths, a 6x6 grid 1 262 816 and the enumeration
# takes about a minute while the solver itself needs milliseconds. Without a
# cap a moderately meshed low-voltage network simply never returns, and the
# user has no way of telling a slow run from a hung one.
_DEFAULT_MAX_PATHS = 100_000
_DEFAULT_CACHE_SIZE = 256

#: The tables of a :class:`PathfinderCache`.
_TABLES = ("graph", "paths", "reaches")


class PathfinderSettings(BaseModel):
    """
    Settings of the source-to-fault path search of one network.

    Assign an instance to ``network.pathfinder_settings``; the settings then
    apply to every path search on that network -- :func:`groundinsight.create_paths`,
    ``run_fault(..., phase_current_mode="paths")``, the transient
    state-space solver -- and to nothing else. They are runtime configuration:
    not saved with the network, kept by copies.

    Attributes
    ----------
    max_paths : int
        Most simple paths one ``(source bus, fault bus)`` enumeration may
        produce before it stops with a ``ValueError``. The count grows
        exponentially with the number of loops (a 6x6 grid has 1 262 816);
        the default ``phase_current_mode="auto"`` does not enumerate at all.
        Defaults to ``100_000``.
    cache_size : int
        Entries per table of the network's path-search memo (adjacency graph,
        enumerated paths, reachability) before the least recently used one is
        dropped. One entry per active topology the network has been searched
        in -- an outage study visits one per scenario. Defaults to ``256``.

    Examples
    --------
    >>> import groundinsight as gi
    >>> settings = gi.PathfinderSettings(max_paths=1_000_000)
    >>> settings.max_paths, settings.cache_size
    (1000000, 256)
    >>> network.pathfinder_settings = settings  # doctest: +SKIP
    """

    model_config = ConfigDict(frozen=True)

    max_paths: int = Field(default=_DEFAULT_MAX_PATHS, gt=0, strict=True)
    cache_size: int = Field(default=_DEFAULT_CACHE_SIZE, gt=0, strict=True)


#: The defaults of every network without settings of its own. Changed only by
#: the deprecated module-level switches below.
_process_defaults = PathfinderSettings()
_process_defaults_lock = threading.Lock()


def default_pathfinder_settings() -> PathfinderSettings:
    """
    The settings of networks that have none of their own.

    Returns
    -------
    PathfinderSettings
        ``PathfinderSettings()`` unless a deprecated module-level switch
        changed it.
    """
    return _process_defaults


class PathfinderCache:
    """
    The path-search memo of one network.

    Internal API: a network hands it out through :meth:`Network.path_cache`;
    configure the search with :class:`PathfinderSettings` instead; not exported
    from ``groundinsight`` and free to change in any release.

    Three LRU tables keyed by the active topology of the network (see
    :meth:`PathFinder._compute_topology_key`): the adjacency graph, the
    enumerated paths per ``(source bus, fault bus)`` and the reachability
    answers per pair. Every operation holds the cache's lock, so concurrent
    readers of one network do not corrupt it.

    Obtain it with ``network.path_cache()``, which makes sure the cache
    belongs to that very network: a shallow copy (``model_copy``) shares the
    private attributes of its original, and the owner check gives it a cache
    of its own on first use. A deep copy and an unpickled network start empty.
    Any two caches compare equal, so the memo never decides whether two
    networks are equal.

    Parameters
    ----------
    owner : Network, optional
        The network the cache belongs to; held weakly.
    """

    __slots__ = ("_owner", "_lock", "_tables", "__weakref__")

    def __init__(self, owner: Optional[Network] = None) -> None:
        self._owner = weakref.ref(owner) if owner is not None else None
        self._lock = threading.RLock()
        self._tables: Dict[str, OrderedDict[Hashable, Any]] = {
            name: OrderedDict() for name in _TABLES
        }
        _register(self)

    def belongs_to(self, network: Network) -> bool:
        """
        Whether this cache is the memo of ``network`` itself.

        Parameters
        ----------
        network : Network
            The network asking.

        Returns
        -------
        bool
            ``True`` only for the network the cache was created for.
        """
        return self._owner is not None and self._owner() is network

    def owner(self) -> Optional[Network]:
        """
        The network this cache belongs to, if it is still alive.

        Returns
        -------
        Network or None
            The owner, or ``None`` for an unowned or orphaned cache.
        """
        return self._owner() if self._owner is not None else None

    def get(self, table: str, key: Hashable) -> Any:
        """
        Look ``key`` up in ``table`` and mark it as recently used.

        Parameters
        ----------
        table : {"graph", "paths", "reaches"}
            The table.
        key : hashable
            The entry's key.

        Returns
        -------
        object or None
            The stored value, or ``None`` when there is none.
        """
        with self._lock:
            entries = self._tables[table]
            value = entries.get(key)
            if value is not None:
                entries.move_to_end(key)
            return value

    def put(self, table: str, key: Hashable, value: Any, capacity: int) -> None:
        """
        Store ``value`` and drop the least recently used entries over capacity.

        Parameters
        ----------
        table : {"graph", "paths", "reaches"}
            The table.
        key : hashable
            The entry's key.
        value : object
            The value; ``None`` is not stored.
        capacity : int
            Most entries the table may hold afterwards.
        """
        if value is None:
            return
        with self._lock:
            entries = self._tables[table]
            entries[key] = value
            entries.move_to_end(key)
            while len(entries) > capacity:
                entries.popitem(last=False)

    def trim(self, capacity: int) -> None:
        """
        Drop least recently used entries until every table fits ``capacity``.

        Parameters
        ----------
        capacity : int
            Most entries per table.
        """
        with self._lock:
            for entries in self._tables.values():
                while len(entries) > capacity:
                    entries.popitem(last=False)

    def clear(self) -> None:
        """Drop every entry of every table."""
        with self._lock:
            for entries in self._tables.values():
                entries.clear()

    def keys(self, table: str) -> List[Hashable]:
        """
        The keys of ``table``, least recently used first.

        Parameters
        ----------
        table : {"graph", "paths", "reaches"}
            The table.

        Returns
        -------
        list
            A snapshot of the keys.
        """
        with self._lock:
            return list(self._tables[table])

    def sizes(self) -> Dict[str, int]:
        """
        Number of entries per table.

        Returns
        -------
        dict of str to int
            ``{"graph": ..., "paths": ..., "reaches": ...}``.
        """
        with self._lock:
            return {name: len(entries) for name, entries in self._tables.items()}

    def __eq__(self, other: object) -> bool:
        # A memo, not state: networks that differ only in what they have
        # cached are equal (pydantic compares private attributes in __eq__).
        return isinstance(other, PathfinderCache)

    def __hash__(self) -> int:
        return hash(PathfinderCache)

    def __copy__(self) -> "PathfinderCache":
        return PathfinderCache()

    def __deepcopy__(self, memo: Dict[int, Any]) -> "PathfinderCache":
        return PathfinderCache()

    def __reduce__(self):
        return (PathfinderCache, ())


#: Live caches by ``id``, held weakly. Only :func:`clear_pathfinder_cache` and
#: the deprecated cache-size switch read it; the search never does.
_LIVE_CACHES: "weakref.WeakValueDictionary[int, PathfinderCache]" = (
    weakref.WeakValueDictionary()
)
_LIVE_CACHES_LOCK = threading.Lock()


def _register(cache: PathfinderCache) -> None:
    """
    Record a new cache in the registry of live caches.

    Parameters
    ----------
    cache : PathfinderCache
        The cache.
    """
    with _LIVE_CACHES_LOCK:
        _LIVE_CACHES[id(cache)] = cache


def _live_caches() -> List[PathfinderCache]:
    """
    Every cache that is still alive.

    Returns
    -------
    list of PathfinderCache
        A snapshot; caches created meanwhile are not in it.
    """
    with _LIVE_CACHES_LOCK:
        return list(_LIVE_CACHES.values())


class _PathLimitExceededError(Exception):
    """Internal signal raised inside the DFS when the path cap is hit."""


def _deprecated_switch(name: str, replacement: str) -> None:
    """
    Warn that a module-level switch of the path search is deprecated.

    Parameters
    ----------
    name : str
        The deprecated function.
    replacement : str
        What to use instead.
    """
    warnings.warn(
        f"groundinsight.pathfinder.{name} is deprecated and will be removed no "
        "earlier than two releases and six months after the release that "
        "deprecated it. It changes the default of every network in the "
        "process, including one another thread is solving. Set it per network "
        f"instead: {replacement}.",
        DeprecationWarning,
        stacklevel=3,
    )


def _positive_int(value: Any, what: str) -> int:
    """
    Validate a positive integer argument of a deprecated switch.

    Parameters
    ----------
    value : object
        The argument.
    what : str
        Its description for the message.

    Returns
    -------
    int
        ``value``.

    Raises
    ------
    ValueError
        If ``value`` is not a positive integer (``bool`` included).
    """
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValueError(f"{what} must be a positive integer; got {value!r}.")
    return value


def set_pathfinder_max_paths(maxcount: int) -> int:
    """Set the process-wide default cap on enumerated simple paths (deprecated).

    .. deprecated::
        Use ``network.pathfinder_settings =
        PathfinderSettings(max_paths=...)``, which applies to that network
        only.

    Parameters
    ----------
    maxcount : int
        Maximum number of simple paths per ``(source_bus, fault_bus)`` pair.
        Must be a positive integer.

    Returns
    -------
    int
        The previous default.

    Raises
    ------
    ValueError
        If ``maxcount`` is not a positive integer.

    Warns
    -----
    DeprecationWarning
        Always.

    Notes
    -----
    Changes :func:`default_pathfinder_settings`, i.e. the limit of every
    network that has no :class:`PathfinderSettings` of its own.
    """
    global _process_defaults

    _deprecated_switch(
        "set_pathfinder_max_paths",
        "network.pathfinder_settings = gi.PathfinderSettings(max_paths=...)",
    )
    _positive_int(maxcount, "pathfinder max_paths")
    with _process_defaults_lock:
        previous = _process_defaults.max_paths
        _process_defaults = _process_defaults.model_copy(update={"max_paths": maxcount})
    return previous


def get_pathfinder_max_paths() -> int:
    """Return the process-wide default cap on enumerated paths (deprecated).

    .. deprecated::
        Read ``network.pathfinder_settings.max_paths``.

    Returns
    -------
    int
        The default of networks without settings of their own.

    Warns
    -----
    DeprecationWarning
        Always.
    """
    _deprecated_switch("get_pathfinder_max_paths", "network.pathfinder_settings.max_paths")
    return _process_defaults.max_paths


def set_pathfinder_cache_size(maxsize: int) -> int:
    """Set the process-wide default size of the path-search memo (deprecated).

    .. deprecated::
        Use ``network.pathfinder_settings =
        PathfinderSettings(cache_size=...)``, which applies to that network
        only.

    Parameters
    ----------
    maxsize : int
        Maximum number of entries per table of a network's memo. Must be a
        positive integer.

    Returns
    -------
    int
        The previous default.

    Raises
    ------
    ValueError
        If ``maxsize`` is not a positive integer.

    Warns
    -----
    DeprecationWarning
        Always.

    Notes
    -----
    Changes :func:`default_pathfinder_settings` and trims the memo of every
    live network to its own effective size at once, so a smaller default is
    respected immediately by the networks that follow it.
    """
    global _process_defaults

    _deprecated_switch(
        "set_pathfinder_cache_size",
        "network.pathfinder_settings = gi.PathfinderSettings(cache_size=...)",
    )
    _positive_int(maxsize, "pathfinder cache size")
    with _process_defaults_lock:
        previous = _process_defaults.cache_size
        _process_defaults = _process_defaults.model_copy(update={"cache_size": maxsize})
    for cache in _live_caches():
        owner = cache.owner()
        if owner is not None:
            cache.trim(owner.pathfinder_settings.cache_size)
    return previous


def get_pathfinder_cache_size() -> int:
    """Return the process-wide default size of the path-search memo (deprecated).

    .. deprecated::
        Read ``network.pathfinder_settings.cache_size``.

    Returns
    -------
    int
        The default of networks without settings of their own.

    Warns
    -----
    DeprecationWarning
        Always.
    """
    _deprecated_switch("get_pathfinder_cache_size", "network.pathfinder_settings.cache_size")
    return _process_defaults.cache_size


def clear_pathfinder_cache(network: Optional[Network] = None) -> None:
    """Drop the path-search memo of one network, or of every live network.

    Parameters
    ----------
    network : Network, optional
        The network whose memo to drop. If ``None`` (default), the memo of
        every network that is alive in the process is dropped -- the coarse
        reset for test fixtures and "I lost track" recovery paths.

    Notes
    -----
    The memo is keyed by the active topology, so an ``active`` flip or a
    rewiring never reads a stale entry; :meth:`Network.invalidate_paths` calls
    the scoped form anyway, to release the memory. Other networks keep their
    memo -- it is theirs.
    """
    if network is None:
        for cache in _live_caches():
            cache.clear()
        return
    network.path_cache().clear()


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
    settings : PathfinderSettings, optional
        Settings for this search only. Defaults to
        ``network.pathfinder_settings``.

    Notes
    -----
    The adjacency graph, the per-``(source, fault)`` paths and the
    reachability answers are memoised in the network's own
    :class:`PathfinderCache` (``network.path_cache()``), keyed on a topology
    fingerprint ``(id(network), name, n_buses, n_branches,
    frozenset(active_buses), frozenset(active_branches), connectivity)``.
    Repeated constructions over the same logical topology -- for example the
    inner loop of :func:`groundinsight.analysis.evaluate_max_epr_under_k` --
    therefore pay the DFS cost only on the first call, and nothing is shared
    with another network.
    """

    def __init__(self, network: Network, settings: Optional[PathfinderSettings] = None):
        """
        Build the adjacency-list representation of ``network``.

        Parameters
        ----------
        network : Network
            The Network instance containing buses and branches.
        settings : PathfinderSettings, optional
            Settings for this search; defaults to the network's.
        """
        self.network = network
        self.settings = settings if settings is not None else network.pathfinder_settings
        self._cache = network.path_cache()
        self._topology_key = self._compute_topology_key()
        graph = self._cache.get("graph", self._topology_key)
        if graph is None:
            graph = self._build_graph()
            self._cache.put("graph", self._topology_key, graph, self.settings.cache_size)
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
        cached = self._cache.get("paths", cache_key)
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
            self._cache.put("paths", cache_key, [], self.settings.cache_size)
            return []

        self._max_paths = self.settings.max_paths
        try:
            self._dfs(source_bus_name, fault_bus_name, visited_buses, path, all_paths)
        except _PathLimitExceededError:
            raise ValueError(
                f"Enumerating the simple paths from bus '{source_bus_name}' to "
                f"bus '{fault_bus_name}' exceeded the limit of {self._max_paths} "
                "paths. The number of simple paths grows exponentially with "
                "the number of loops in the network, so a moderately meshed "
                "network never finishes this enumeration while the solver "
                "itself needs milliseconds. The default "
                "phase_current_mode='auto' does not need the enumeration -- "
                "it solves a reduced phase-conductor network instead. Use it, "
                "or raise the limit for this network with "
                "network.pathfinder_settings = "
                "gi.PathfinderSettings(max_paths=...) if the enumerated paths "
                "are genuinely required."
            ) from None

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
        self._cache.put("paths", cache_key, paths, self.settings.cache_size)
        return [
            Path(
                name="",
                source="",
                fault="",
                segments=list(p.segments),
            )
            for p in paths
        ]

    def reaches(self, source_bus_name: str, fault_bus_name: str) -> bool:
        """
        Report whether *any* route connects a source bus to a fault bus.

        This is the question the stationary solver actually asks in the
        default ``phase_current_mode="auto"``: a source contributes to a
        fault if and only if at least one route exists. Answering it with
        :meth:`find_paths` means enumerating *all* simple paths, whose count
        is exponential in the number of independent loops -- 1 262 816 paths
        and roughly a minute on a 6x6 grid, against milliseconds in the
        solver. A breadth-first search over the same active sub-graph, with
        an early exit at the target, answers it in ``O(V + E)``.

        The result is exactly equivalent to ``len(find_paths(...)) > 0``:
        both run over the adjacency list built by :meth:`_build_graph`, so
        inactive buses and branches are excluded in the same way.

        Parameters
        ----------
        source_bus_name : str
            The name of the source bus.
        fault_bus_name : str
            The name of the fault bus.

        Returns
        -------
        bool
            ``True`` if a route over active buses and branches exists.
        """
        cache_key = (*self._topology_key, source_bus_name, fault_bus_name)
        cached = self._cache.get("reaches", cache_key)
        if cached is not None:
            return cached

        if source_bus_name not in self.graph or fault_bus_name not in self.graph:
            result = False
        elif source_bus_name == fault_bus_name:
            # A source sitting on the fault bus itself feeds the fault; the
            # DFS reports the empty path for this case, so ``find_paths``
            # returns one (zero-segment) path and the two agree.
            result = True
        else:
            visited = {source_bus_name}
            queue = deque([source_bus_name])
            result = False
            while queue:
                current = queue.popleft()
                for branch in self.graph[current]:
                    neighbor = (
                        branch.to_bus
                        if branch.from_bus == current
                        else branch.from_bus
                    )
                    if neighbor == fault_bus_name:
                        result = True
                        queue.clear()
                        break
                    if neighbor not in visited:
                        visited.add(neighbor)
                        queue.append(neighbor)

        self._cache.put("reaches", cache_key, result, self.settings.cache_size)
        return result

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
            if len(all_paths) > self._max_paths:
                raise _PathLimitExceededError
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

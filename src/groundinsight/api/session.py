# api/session.py
"""
:class:`ProjectSession` -- one open project, as a front end works with it.

A session owns exactly one :class:`~groundinsight.Network` together with
everything a user interface needs around it and a notebook does not:

* a **revision** counter and **undo/redo** stacks of
  :mod:`~groundinsight.api.commands`. Each new command gets a fresh revision
  number; undo and redo return to the number of the state they restore, so a
  revision identifies an input state (an ETag, in REST terms) and a job's
  result names the state it was computed from;
* a **location**: a project file (local use) or a key in a
  :class:`ProjectStore` -- an in-memory store or a :class:`~groundinsight.Database`
  (server use). Opening and saving go through the library's project-file
  functions, so a GUI project is a library project: UTF-8, atomic,
  schema-versioned. The file holds the network only -- no stored results
  (they are derived, go stale with the next edit and make a file useless in a
  diff) and no view state;
* the **type catalogue** by name (see :mod:`~groundinsight.api.commands`);
* **resource limits** (network size, job runtime, undo depth) given to the
  constructor, so a server can set them per user;
* **results**: kept on the network as the library keeps them
  (``network.results``), so their staleness is the library's
  (:meth:`Network.is_result_stale <groundinsight.Network.is_result_stale>`,
  input digests compared by content) and not a second bookkeeping that could
  disagree. Undoing an edit therefore makes a result current again.

Multi-user and REST-ready by construction: the module holds no state, a
session holds no reference to another session, every method returns a DTO,
and a session is safe to use from a UI thread while its jobs run in worker
threads (an internal lock guards every access to the network; a job works on
its own copy). Two sessions in two threads share nothing but the library's
immutable code. A session is *not* meant to be driven by two users at once --
a server keeps one session per user and project.

Jobs (:mod:`~groundinsight.api.jobs`) take a **snapshot** of the network when
they are submitted -- a deep copy without stored results and enumerated paths,
which is cheaper than the solve it protects -- and compute on it. The user can
keep editing meanwhile; the result arrives bound to the revision it was
computed from and is stored on the network, where the library reports it as
stale if the network has moved on. A result never replaces a stored result
that is current with one that is stale.
"""

import itertools
import logging
import threading
from typing import Any, Dict, Iterable, List, Optional, Union

import groundinsight as gi
from groundinsight.diagnostics import collecting, unique

from .commands import Command, CommandContext, parse_command, rejection_reason
from .examples import EXAMPLES
from .jobs import JobHandle, JobRunner, snapshot_network
from .tasks import TaskOutcome, run_task
from .schemas import (
    CheckReportDTO,
    CommandResultDTO,
    FaultResultDTO,
    FaultResultsDTO,
    NetworkSummaryDTO,
    NetworkTablesDTO,
    SessionLimitsDTO,
    SolveRequestDTO,
    TableDTO,
    parse_job_request,
)
from .views import check_report_dto, diagnostic_dto, element_tables_dto, fault_result_dto

logger = logging.getLogger(__name__)

__all__ = ["DatabaseStore", "MemoryStore", "ProjectError", "ProjectSession", "ProjectStore"]


class ProjectError(ValueError):
    """A project could not be opened or saved.

    The message names the location and the reason; the original exception
    (an ``OSError``, a validation error) is chained as ``__cause__``.
    """


# ---------------------------------------------------------------------------
# stores
# ---------------------------------------------------------------------------


class ProjectStore:
    """
    Where a server keeps projects: load and save networks by key.

    Subclasses implement :meth:`load`, :meth:`save` and :meth:`keys`. A store
    receives networks without stored results and returns fresh objects that
    share nothing with what was saved.

    Attributes
    ----------
    scheme : str
        Prefix of the session location, ``"<scheme>:<key>"``.
    """

    scheme: str = "store"

    def load(self, key: str) -> gi.Network:  # pragma: no cover - abstract
        """
        Load the network stored under ``key``.

        Parameters
        ----------
        key : str
            The project key.

        Returns
        -------
        Network
            A fresh network object.

        Raises
        ------
        KeyError
            If no project is stored under ``key``.
        """
        raise NotImplementedError

    def save(self, key: str, network: gi.Network) -> None:  # pragma: no cover - abstract
        """
        Store ``network`` under ``key``, replacing what was there.

        Parameters
        ----------
        key : str
            The project key.
        network : Network
            The network, without stored results.
        """
        raise NotImplementedError

    def keys(self) -> List[str]:  # pragma: no cover - abstract
        """
        The stored project keys.

        Returns
        -------
        list of str
            Sorted keys.
        """
        raise NotImplementedError


class MemoryStore(ProjectStore):
    """
    Projects in memory, as their JSON text -- for a demo server and for tests.

    Saving stores ``model_dump_json()`` of the network; loading validates it
    into a new object, so a loaded project shares nothing with the saved one,
    exactly as with a file.

    Examples
    --------
    >>> store = MemoryStore()
    >>> store.keys()
    []
    """

    scheme = "memory"

    def __init__(self) -> None:
        self._projects: Dict[str, str] = {}
        self._lock = threading.Lock()

    def load(self, key: str) -> gi.Network:
        """
        Load the network stored under ``key`` (see :meth:`ProjectStore.load`).

        Parameters
        ----------
        key : str
            The project key.

        Returns
        -------
        Network
            A fresh network object, parsed from the stored JSON.

        Raises
        ------
        KeyError
            If no project is stored under ``key``.
        """
        with self._lock:
            text = self._projects.get(key)
        if text is None:
            raise KeyError(f"no project '{key}' in the memory store")
        return gi.Network.model_validate_json(text)

    def save(self, key: str, network: gi.Network) -> None:
        """
        Store ``network`` under ``key`` (see :meth:`ProjectStore.save`).

        Parameters
        ----------
        key : str
            The project key; a project stored under it is replaced.
        network : Network
            The network, kept as its JSON form.
        """
        text = network.model_dump_json()
        with self._lock:
            self._projects[key] = text

    def keys(self) -> List[str]:
        """
        The stored project keys (see :meth:`ProjectStore.keys`).

        Returns
        -------
        list of str
            Sorted keys.
        """
        with self._lock:
            return sorted(self._projects)


class DatabaseStore(ProjectStore):
    """
    Projects in a :class:`groundinsight.Database` (SQLite), keyed by network name.

    The database keys networks by their name, so the store key must be the
    network's name. Saving replaces a stored network of that name; the
    database stores no results.

    Parameters
    ----------
    database : groundinsight.Database
        An open database.
    """

    scheme = "db"

    def __init__(self, database: "gi.Database") -> None:
        self.database = database

    def load(self, key: str) -> gi.Network:
        """
        Load the network named ``key`` (see :meth:`ProjectStore.load`).

        Parameters
        ----------
        key : str
            The network name.

        Returns
        -------
        Network
            A fresh network object, loaded from the database.

        Raises
        ------
        KeyError
            If the database holds no network of that name.
        """
        if key not in self.database.list_networks():
            raise KeyError(f"no network '{key}' in {self.database.path}")
        return self.database.load_network(key)

    def save(self, key: str, network: gi.Network) -> None:
        """
        Store ``network`` under its name (see :meth:`ProjectStore.save`).

        Parameters
        ----------
        key : str
            The network name; it has to equal ``network.name``.
        network : Network
            The network; a stored network of that name is replaced.

        Raises
        ------
        ValueError
            If ``key`` is not the network's name.
        """
        if key != network.name:
            raise ValueError(
                f"a database stores a network under its name; key '{key}' differs "
                f"from the network name '{network.name}'"
            )
        self.database.save_network(network, overwrite=True)

    def keys(self) -> List[str]:
        """
        The names of the stored networks (see :meth:`ProjectStore.keys`).

        Returns
        -------
        list of str
            The network names, in the database's order.
        """
        return list(self.database.list_networks())


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _without_results(network: gi.Network) -> gi.Network:
    """
    A shallow view of the network with no results, for saving.

    Parameters
    ----------
    network : Network
        The network.

    Returns
    -------
    Network
        A copy that shares the elements and holds ``results == {}``; the
        original keeps its results.
    """
    return network.model_copy(update={"results": {}})


def _check_size(network: gi.Network, limits: SessionLimitsDTO) -> None:
    """
    Refuse a network larger than the session's limits.

    Parameters
    ----------
    network : Network
        The network.
    limits : SessionLimitsDTO
        The limits.

    Raises
    ------
    ValueError
        Naming the limit (``max_buses`` or ``max_branches``) that is exceeded.
    """
    for collection, limit_name in (("buses", "max_buses"), ("branches", "max_branches")):
        limit = getattr(limits, limit_name)
        size = len(getattr(network, collection))
        if limit is not None and size > limit:
            raise ValueError(
                f"the network has {size} {collection}, this session allows at most "
                f"{limit} ({limit_name})"
            )


def _catalogue(elements: Iterable[Any]) -> Dict[str, Any]:
    """
    The types of ``elements`` by name, in order of first use.

    Parameters
    ----------
    elements : iterable of Bus or Branch
        The elements.

    Returns
    -------
    dict
        Type name to the first type object of that name.
    """
    catalogue: Dict[str, Any] = {}
    for element in elements:
        catalogue.setdefault(element.type.name, element.type)
    return catalogue


class _Entry:
    """One command in the history, with the revisions around it."""

    __slots__ = ("command", "before", "after", "paths")

    def __init__(self, command: Command, before: int, after: int, paths: Any) -> None:
        self.command = command
        self.before = before
        self.after = after
        self.paths = paths


# ---------------------------------------------------------------------------
# the session
# ---------------------------------------------------------------------------


class ProjectSession:
    """
    One open project: a network, its edit history, its results and its jobs.

    Parameters
    ----------
    network : Network
        The network; the session takes ownership (edit it only through
        :meth:`apply` from now on).
    path : str, optional
        Project file the network belongs to (used by :meth:`save`).
    store : ProjectStore, optional
        Store the network belongs to, with ``key``; mutually exclusive with
        ``path``.
    key : str, optional
        Key in ``store``.
    runner : JobRunner, optional
        Runner for :meth:`submit`. Default: a thread runner with one worker,
        created on first use and shut down by :meth:`close`. A server may
        share one runner between sessions; the session then does not shut it
        down.
    max_buses, max_branches : int, optional
        Size limits; a larger network is refused, and so is a command that
        would exceed them.
    max_job_runtime_s : float, optional
        Runtime limit of every job of this session.
    max_undo : int, optional
        Number of commands kept for undo (default 500).
    max_paths : int, optional
        Cap of the path enumeration on this session's network (the
        ``"paths"`` phase-current mode, the transient solver): it becomes the
        network's own :class:`groundinsight.PathfinderSettings`, so other
        sessions in the process keep theirs. ``None`` leaves the network's
        settings as they are.

    Raises
    ------
    ValueError
        If the network exceeds a limit, a limit is invalid, two different
        types share a name (the tables would lose one), or both ``path`` and
        ``store`` are given.

    Examples
    --------
    >>> from groundinsight.api import ProjectSession, UpdateBus
    >>> session = ProjectSession.from_example("quickstart")
    >>> result = session.solve("fault_at_remote_bus")
    >>> result.key_figures.rms().fault_bus
    'bus_fault'
    >>> _ = session.apply(UpdateBus(name="bus_fault",
    ...                             fields={"specific_earth_resistance": 250.0}))
    >>> session.is_stale("fault_at_remote_bus"), session.revision
    (True, 1)
    >>> _ = session.undo()
    >>> session.is_stale("fault_at_remote_bus"), session.revision
    (False, 0)
    >>> session.close()
    """

    def __init__(
        self,
        network: gi.Network,
        *,
        path: Optional[str] = None,
        store: Optional[ProjectStore] = None,
        key: Optional[str] = None,
        runner: Optional[JobRunner] = None,
        max_buses: Optional[int] = None,
        max_branches: Optional[int] = None,
        max_job_runtime_s: Optional[float] = None,
        max_undo: Optional[int] = 500,
        max_paths: Optional[int] = None,
    ) -> None:
        if not isinstance(network, gi.Network):
            raise TypeError(f"a session holds a groundinsight.Network, got {type(network).__name__}")
        if path is not None and store is not None:
            raise ValueError("a project lives in a file or in a store, not both")
        if (store is None) != (key is None):
            raise ValueError("a store location needs both store and key")
        self._limits = SessionLimitsDTO(
            max_buses=max_buses,
            max_branches=max_branches,
            max_job_runtime_s=max_job_runtime_s,
            max_undo=max_undo,
            max_paths=max_paths,
        )
        _check_size(network, self._limits)
        if self._limits.max_paths is not None:
            # Runtime settings of this network only: kept by the job snapshots
            # (copies and pickles keep them), not written to the project file.
            network.pathfinder_settings = gi.PathfinderSettings(
                max_paths=self._limits.max_paths,
                cache_size=network.pathfinder_settings.cache_size,
            )
        network.to_tables()  # refuses two different types of the same name
        self._network = network
        self._path = str(path) if path is not None else None
        self._store = store
        self._key = key
        self._bus_types = _catalogue(network.buses.values())
        self._branch_types = _catalogue(network.branches.values())
        self._lock = threading.RLock()
        self._revision = 0
        self._saved_revision = 0
        self._revisions = itertools.count(1)
        self._done: List[_Entry] = []
        self._undone: List[_Entry] = []
        self._runner = runner
        self._owns_runner = runner is None
        self._sequence = itertools.count(1)
        self._result_sequence: Dict[str, int] = {}
        self._result_revision: Dict[str, Optional[int]] = {}
        self._result_mode: Dict[str, Optional[str]] = {}
        self._handles: List[JobHandle] = []
        self._closed = False

    # -- construction ---------------------------------------------------------

    @classmethod
    def from_file(cls, path: str, **options: Any) -> "ProjectSession":
        """
        Open a project file (:func:`groundinsight.load_network_from_json`).

        Parameters
        ----------
        path : str
            The project file.
        **options
            Keyword arguments of the constructor (limits, runner).

        Returns
        -------
        ProjectSession
            A session located at ``path``. Results stored in the file are kept
            on the network (the library reports whether they are stale).

        Raises
        ------
        ProjectError
            If the file cannot be read, is not a project file, or exceeds the
            session's limits.
        """
        try:
            network = gi.load_network_from_json(path)
        except (OSError, ValueError) as exc:
            raise ProjectError(f"cannot open '{path}': {rejection_reason(exc)}") from exc
        try:
            return cls(network, path=path, **options)
        except ValueError as exc:
            raise ProjectError(f"cannot open '{path}': {rejection_reason(exc)}") from exc

    @classmethod
    def from_store(cls, store: ProjectStore, key: str, **options: Any) -> "ProjectSession":
        """
        Open a project from a store.

        Parameters
        ----------
        store : ProjectStore
            The store.
        key : str
            The project key.
        **options
            Keyword arguments of the constructor.

        Returns
        -------
        ProjectSession
            A session located at ``(store, key)``.

        Raises
        ------
        ProjectError
            If the store has no such project, it does not load, or it exceeds
            the session's limits.
        """
        location = f"{store.scheme}:{key}"
        try:
            network = store.load(key)
        except (KeyError, OSError, ValueError, RuntimeError) as exc:
            raise ProjectError(f"cannot open '{location}': {rejection_reason(exc)}") from exc
        try:
            return cls(network, store=store, key=key, **options)
        except ValueError as exc:
            raise ProjectError(f"cannot open '{location}': {rejection_reason(exc)}") from exc

    @classmethod
    def from_example(cls, name: str, **options: Any) -> "ProjectSession":
        """
        A new, unsaved project from one of the documented examples.

        Parameters
        ----------
        name : str
            A key of :data:`groundinsight.api.EXAMPLES`.
        **options
            Keyword arguments of the constructor.

        Returns
        -------
        ProjectSession
            A session without location.

        Raises
        ------
        ValueError
            If ``name`` is not an example.
        """
        if name not in EXAMPLES:
            raise ValueError(f"unknown example '{name}'; examples: {', '.join(EXAMPLES)}")
        return cls(EXAMPLES[name](), **options)

    def __enter__(self) -> "ProjectSession":
        return self

    def __exit__(self, *exc_info: Any) -> None:
        self.close()

    def close(self) -> None:
        """
        Cancel this session's jobs and shut down its own runner.

        A runner passed to the constructor is left running (it may serve
        other sessions); jobs of this session on it are cancelled.
        """
        with self._lock:
            if self._closed:
                return
            self._closed = True
            runner = self._runner
            handles = list(self._handles)
        if runner is None:
            return
        if self._owns_runner:
            runner.shutdown(wait=True)
            return
        for handle in handles:
            handle.cancel()

    # -- state ------------------------------------------------------------------

    @property
    def network(self) -> gi.Network:
        """
        The session's network, for reading (notebooks, tests).

        Front ends use the DTO methods; every change goes through
        :meth:`apply`.

        Returns
        -------
        Network
            The session's working network (not a copy).
        """
        return self._network

    @property
    def revision(self) -> int:
        """
        Identifier of the current input state (see the module docstring).

        Returns
        -------
        int
            The revision; it changes with every applied, undone or redone command.
        """
        return self._revision

    @property
    def dirty(self) -> bool:
        """
        ``True`` when the network differs from the last opened or saved state.

        Returns
        -------
        bool
            Whether there are unsaved changes.
        """
        return self._revision != self._saved_revision

    @property
    def limits(self) -> SessionLimitsDTO:
        """
        The session's resource limits.

        Returns
        -------
        SessionLimitsDTO
            The limits the session was opened with.
        """
        return self._limits

    @property
    def location(self) -> Optional[str]:
        """
        Where the project is stored.

        Returns
        -------
        str or None
            The project file path, ``"<scheme>:<key>"`` for a store, or
            ``None`` for a project that was never saved.
        """
        if self._store is not None:
            return f"{self._store.scheme}:{self._key}"
        return self._path

    @property
    def can_undo(self) -> bool:
        """
        Whether :meth:`undo` would do something.

        Returns
        -------
        bool
            ``True`` when there is a command to undo.
        """
        return bool(self._done)

    @property
    def can_redo(self) -> bool:
        """
        Whether :meth:`redo` would do something.

        Returns
        -------
        bool
            ``True`` when there is an undone command to redo.
        """
        return bool(self._undone)

    @property
    def runner(self) -> JobRunner:
        """
        The job runner (created on first use when none was given).

        Returns
        -------
        JobRunner
            The runner that executes the session's jobs.
        """
        with self._lock:
            if self._runner is None:
                self._runner = JobRunner(backend="thread", max_workers=1)
            return self._runner

    def history(self) -> List[str]:
        """
        Labels of the commands that :meth:`undo` can revert, oldest first.

        Returns
        -------
        list of str
            One label per command.
        """
        with self._lock:
            return [entry.command.label for entry in self._done]

    def fault_names(self) -> List[str]:
        """
        Names of the faults, in network order.

        Returns
        -------
        list of str
            The fault names.
        """
        with self._lock:
            return list(self._network.faults)

    # -- files and stores -------------------------------------------------------

    def save(self, path: Optional[str] = None) -> str:
        """
        Save the project -- to ``path`` ("save as") or to its location.

        Parameters
        ----------
        path : str, optional
            A project file. Without it, the session's file or store location.

        Returns
        -------
        str
            The location saved to (:attr:`location`).

        Raises
        ------
        ProjectError
            If there is no location, or writing fails; an existing file is
            then unchanged (the library writes atomically) and the session
            stays dirty.
        """
        with self._lock:
            self._require_open()
            if path is None and self._path is None and self._store is None:
                raise ProjectError("the project has no location yet; pass a path")
            view = _without_results(self._network)
            if path is not None or self._store is None:
                target = str(path) if path is not None else self._path
                try:
                    gi.save_network_to_json(view, target)
                except (OSError, ValueError) as exc:
                    raise ProjectError(f"cannot save '{target}': {rejection_reason(exc)}") from exc
                self._path, self._store, self._key = target, None, None
            else:
                self._save_to_store(self._store, self._key, view)
            self._saved_revision = self._revision
            return self.location

    def save_to(self, store: ProjectStore, key: str) -> str:
        """
        Save the project into a store ("save as"); the store becomes its location.

        Parameters
        ----------
        store : ProjectStore
            The store.
        key : str
            The project key.

        Returns
        -------
        str
            ``"<scheme>:<key>"``.

        Raises
        ------
        ProjectError
            If the store refuses the project.
        """
        with self._lock:
            self._require_open()
            self._save_to_store(store, key, _without_results(self._network))
            self._path, self._store, self._key = None, store, key
            self._saved_revision = self._revision
            return self.location

    @staticmethod
    def _save_to_store(store: ProjectStore, key: str, network: gi.Network) -> None:
        """Write to a store, turning its failures into :class:`ProjectError`."""
        try:
            store.save(key, network)
        except (OSError, ValueError, RuntimeError) as exc:
            raise ProjectError(f"cannot save '{store.scheme}:{key}': {rejection_reason(exc)}") from exc

    # -- edits ------------------------------------------------------------------

    def _context(self) -> CommandContext:
        """The objects commands act on."""
        return CommandContext(self._network, self._bus_types, self._branch_types, self._limits)

    def _require_open(self) -> None:
        """Refuse work on a closed session."""
        if self._closed:
            raise RuntimeError("the session is closed")

    def _command_result(self, command: Command, records: List[gi.Diagnostic]) -> CommandResultDTO:
        """The DTO describing the session after ``command``."""
        return CommandResultDTO(
            op=command.op,
            label=command.label,
            revision=self._revision,
            diagnostics=[diagnostic_dto(d) for d in unique(records)],
            stale_results=self._network.stale_results(),
            can_undo=bool(self._done),
            can_redo=bool(self._undone),
        )

    def apply(self, command: Union[Command, Dict[str, Any]]) -> CommandResultDTO:
        """
        Apply an edit command and record it for undo.

        Parameters
        ----------
        command : Command or dict
            The command, or its JSON form (see
            :func:`~groundinsight.api.commands.parse_command`). The session
            applies a copy, so one command object can be applied to several
            sessions.

        Returns
        -------
        CommandResultDTO
            The new revision, the library warnings the command raised (a
            frequency order, a source value outside the network frequencies),
            the stale results and the history state.

        Raises
        ------
        CommandError
            If the command is rejected; network, catalogue, revision and
            history are unchanged.
        """
        command = parse_command(command).model_copy(deep=True)
        with self._lock:
            self._require_open()
            paths_before = self._network.paths
            with collecting() as records:
                command.apply(self._context())
            entry = _Entry(command, self._revision, next(self._revisions), paths_before)
            self._done.append(entry)
            self._undone.clear()
            limit = self._limits.max_undo
            if limit is not None and len(self._done) > limit:
                del self._done[: len(self._done) - limit]
            self._revision = entry.after
            return self._command_result(command, records)

    def undo(self) -> Optional[CommandResultDTO]:
        """
        Revert the last applied command.

        Returns
        -------
        CommandResultDTO or None
            The reverted command and the session state afterwards; ``None``
            when there is nothing to undo.
        """
        with self._lock:
            self._require_open()
            if not self._done:
                return None
            entry = self._done[-1]
            try:
                with collecting() as records:
                    entry.command.revert(self._context())
                    if self._network.paths is not entry.paths:
                        self._network.paths = entry.paths
            except Exception:
                # Reverting restores a state the library accepted before, so
                # this is a defect, not a user error: keep the entry; the
                # exception reaches the caller.
                logger.debug("undo of '%s' failed", entry.command.label, exc_info=True)
                raise
            self._done.pop()
            self._undone.append(entry)
            self._revision = entry.before
            return self._command_result(entry.command, records)

    def redo(self) -> Optional[CommandResultDTO]:
        """
        Apply the last undone command again.

        Returns
        -------
        CommandResultDTO or None
            The command and the session state afterwards; ``None`` when there
            is nothing to redo.
        """
        with self._lock:
            self._require_open()
            if not self._undone:
                return None
            entry = self._undone.pop()
            entry.paths = self._network.paths
            with collecting() as records:
                entry.command.apply(self._context())
            self._done.append(entry)
            self._revision = entry.after
            return self._command_result(entry.command, records)

    # -- views ------------------------------------------------------------------

    def summary(self) -> NetworkSummaryDTO:
        """
        What a header bar shows: name, counts, history and save state.

        Returns
        -------
        NetworkSummaryDTO
            The summary.
        """
        with self._lock:
            network = self._network
            return NetworkSummaryDTO(
                name=network.name,
                description=network.description,
                frequencies=[float(f) for f in network.frequencies],
                n_buses=len(network.buses),
                n_branches=len(network.branches),
                n_sources=len(network.sources),
                n_faults=len(network.faults),
                bus_types=list(self._bus_types),
                branch_types=list(self._branch_types),
                faults=list(network.faults),
                results=list(network.results),
                stale_results=network.stale_results(),
                revision=self._revision,
                dirty=self.dirty,
                can_undo=bool(self._done),
                can_redo=bool(self._undone),
                undo_label=self._done[-1].command.label if self._done else None,
                redo_label=self._undone[-1].command.label if self._undone else None,
                location=self.location,
                limits=self._limits,
                groundinsight_version=gi.__version__,
            )

    def tables(self) -> NetworkTablesDTO:
        """
        The network's input as the ten tables of ``Network.to_tables``.

        Returns
        -------
        NetworkTablesDTO
            The tables; the type tables also list catalogue types without
            users.
        """
        with self._lock:
            used_bus = {b.type.name for b in self._network.buses.values()}
            used_branch = {b.type.name for b in self._network.branches.values()}
            return element_tables_dto(
                self._network,
                revision=self._revision,
                extra_bus_types=[t for n, t in self._bus_types.items() if n not in used_bus],
                extra_branch_types=[t for n, t in self._branch_types.items() if n not in used_branch],
            )

    def table(self, name: str) -> TableDTO:
        """
        One of the tables of :meth:`tables`.

        Parameters
        ----------
        name : str
            ``"buses"``, ``"branches"``, ...

        Returns
        -------
        TableDTO
            The table.

        Raises
        ------
        KeyError
            If ``name`` is not a table.
        """
        return self.tables().tables[name]

    def check(self, fault: Optional[str] = None) -> CheckReportDTO:
        """
        The pre-flight check (:func:`groundinsight.check_network`).

        Parameters
        ----------
        fault : str, optional
            Restrict the fault-specific checks to this fault.

        Returns
        -------
        CheckReportDTO
            Every finding, with counts; ``ok`` is false when one of them is
            an error.
        """
        with self._lock:
            records = gi.check_network(self._network, fault=fault)
            return check_report_dto(records, revision=self._revision, fault=fault)

    # -- results ----------------------------------------------------------------

    def is_stale(self, fault: str) -> bool:
        """
        Whether the stored result of ``fault`` no longer matches the network.

        Parameters
        ----------
        fault : str
            A fault with a stored result.

        Returns
        -------
        bool
            The library's answer (:meth:`Network.is_result_stale
            <groundinsight.Network.is_result_stale>`).

        Raises
        ------
        ValueError
            If no result is stored for ``fault``.
        """
        with self._lock:
            return self._network.is_result_stale(fault)

    def stale_results(self) -> List[str]:
        """
        Faults whose stored result no longer matches the network.

        Returns
        -------
        list of str
            The library's :meth:`Network.stale_results
            <groundinsight.Network.stale_results>`.
        """
        with self._lock:
            return self._network.stale_results()

    def result(self, fault: str) -> Optional[FaultResultDTO]:
        """
        The stored result of a fault, as a DTO.

        Parameters
        ----------
        fault : str
            The fault.

        Returns
        -------
        FaultResultDTO or None
            ``None`` when no result is stored. ``revision`` is the revision
            the result was computed from (``None`` for one that came with the
            project file); ``stale`` is the library's verdict now.
        """
        with self._lock:
            if fault not in self._network.results or fault not in self._network.faults:
                return None
            return fault_result_dto(
                self._network,
                fault,
                revision=self._result_revision.get(fault),
                phase_current_mode=self._result_mode.get(fault),
                stale=self._network.is_result_stale(fault),
            )

    def solve(self, fault: str, *, phase_current_mode: str = "auto") -> FaultResultDTO:
        """
        Solve one fault in the calling thread and store the result.

        For scripts and the command-line interface. A user interface uses
        :meth:`submit_solve`, which keeps its event loop free.

        Parameters
        ----------
        fault : str
            The fault.
        phase_current_mode : {"auto", "paths"}
            As in :func:`groundinsight.run_fault`.

        Returns
        -------
        FaultResultDTO
            The result, bound to the current revision.

        Raises
        ------
        ValueError
            Whatever :func:`groundinsight.solve` raises (unknown fault, no
            source, a network the solver rejects).
        """
        request = SolveRequestDTO(fault=fault, phase_current_mode=phase_current_mode)
        with self._lock:
            self._require_open()
            snapshot = snapshot_network(self._network)
            revision = self._revision
            sequence = next(self._sequence)
        outcome = run_task(request, snapshot, revision=revision)
        return self._bind(outcome, sequence, revision, request)

    # -- jobs -------------------------------------------------------------------

    def submit(self, request: Any) -> JobHandle:
        """
        Run a job request off the caller's thread.

        Parameters
        ----------
        request : job request DTO or dict
            A :class:`~groundinsight.api.schemas.SolveRequestDTO`,
            ``SolveAllRequestDTO``, ``SweepRequestDTO``,
            ``OutageStudyRequestDTO`` or ``MaxRhoRequestDTO``, or its JSON
            form.

        Returns
        -------
        JobHandle
            Poll :meth:`JobHandle.status`, wait with :meth:`JobHandle.result`,
            stop with :meth:`JobHandle.cancel`. Fault results are stored on
            the network when the job finishes (see the module docstring).

        Raises
        ------
        ValueError
            If the request is invalid.
        RuntimeError
            If the session is closed.
        """
        request = parse_job_request(request)
        with self._lock:
            self._require_open()
            snapshot = snapshot_network(self._network)
            revision = self._revision
            sequence = next(self._sequence)
        handle = self.runner.submit(
            request,
            snapshot,
            revision=revision,
            max_runtime_s=self._limits.max_job_runtime_s,
            on_success=lambda outcome: self._bind(outcome, sequence, revision, request),
            copy_network=False,
        )
        with self._lock:
            self._handles = [h for h in self._handles if not h.done]
            self._handles.append(handle)
        return handle

    def submit_solve(self, fault: str, *, phase_current_mode: str = "auto") -> JobHandle:
        """
        Solve one fault as a job; shorthand for :meth:`submit`.

        Parameters
        ----------
        fault : str
            The fault.
        phase_current_mode : {"auto", "paths"}
            As in :func:`groundinsight.run_fault`.

        Returns
        -------
        JobHandle
            The job; its result is a :class:`FaultResultDTO`.
        """
        return self.submit(SolveRequestDTO(fault=fault, phase_current_mode=phase_current_mode))

    def _bind(self, outcome: TaskOutcome, sequence: int, revision: int, request: Any):
        """
        Store a job's fault results on the network and finish its DTO.

        A result is stored unless it would replace a result that is current
        with one that is stale, or a stale result of a later job with one of
        an earlier job. Whether a result is stale is the library's verdict
        (the result is put in place and asked); a result that is not kept is
        taken out again.

        Parameters
        ----------
        outcome : TaskOutcome
            The job's DTO and its library results by fault.
        sequence : int
            Submission number of the job (later jobs have larger numbers).
        revision : int
            Revision the job computed from.
        request : job request DTO
            The request (for the phase-current mode).

        Returns
        -------
        BaseModel
            The job's DTO, with ``stale`` set from the library for every
            fault result in it.
        """
        mode = getattr(request, "phase_current_mode", None)
        with self._lock:
            network = self._network
            stale: Dict[str, bool] = {}
            for fault, result in outcome.results.items():
                if fault not in network.faults:
                    stale[fault] = True
                    continue
                previous = network.results.get(fault)
                network.results[fault] = result
                new_stale = network.is_result_stale(fault)
                keep = previous is None or not new_stale
                if not keep:
                    network.results[fault] = previous
                    previous_stale = network.is_result_stale(fault)
                    keep = previous_stale and sequence > self._result_sequence.get(fault, 0)
                    network.results[fault] = result if keep else previous
                if keep:
                    self._result_sequence[fault] = sequence
                    self._result_revision[fault] = revision
                    self._result_mode[fault] = mode
                stale[fault] = new_stale
            return _with_staleness(outcome.dto, stale)


def _with_staleness(dto: Any, stale: Dict[str, bool]) -> Any:
    """
    Set the ``stale`` flag of every fault result in a job's DTO.

    Parameters
    ----------
    dto : BaseModel
        A :class:`FaultResultDTO`, a :class:`FaultResultsDTO` or a study DTO.
    stale : dict of str to bool
        The library's verdict per fault.

    Returns
    -------
    BaseModel
        An updated copy (or ``dto`` itself when it holds no fault result).
    """
    if isinstance(dto, FaultResultDTO):
        return dto.model_copy(update={"stale": stale.get(dto.fault, dto.stale)})
    if isinstance(dto, FaultResultsDTO):
        return dto.model_copy(
            update={"results": [_with_staleness(r, stale) for r in dto.results]}
        )
    return dto

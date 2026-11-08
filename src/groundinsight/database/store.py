# database/store.py

"""
The SQLite store as an object: :class:`Database`.

Up to 0.6.0 the SQLite persistence lived in four module globals of the
package -- ``engine``, ``SessionLocal`` and the scoped session ``session``
with its alias ``db_session`` -- set by :func:`start_dbsession`. That allowed
exactly one database per process (a second ``start_dbsession`` for another
file raised), and every caller shared one thread-local session registry. A
desktop application with two open projects, a server that serves two users,
or a study that writes results while the GUI reads the type catalogue cannot
work like that (audit finding ARC2).

:class:`Database` owns its engine and its session factory. Every method opens
a *short-lived* session, does one operation and closes it again, so no
SQLAlchemy ``Session`` -- which is not thread-safe -- is ever shared between
threads. Two ``Database`` objects on two files are fully independent and can
be used from two threads at the same time.

One file, several threads
-------------------------
SQLite allows one writer at a time. Two threads that write the same file
through separate connections race: the second ``INSERT`` of a shared bus type
fails with an ``IntegrityError``, or a write waits for the busy timeout and
fails with ``database is locked``. Operations on the same file are therefore
serialised with a re-entrant lock that is shared by every ``Database`` object
of this process on that file (keyed by the resolved path), not only by one
object. Operations on different files never wait for each other. Separate
*processes* on one file rely on SQLite's own locking, as before.

The default instance
--------------------
The module-level functions of 0.6.0 -- :func:`start_dbsession`,
:func:`close_dbsession`, :func:`save_bustype_to_db`,
:func:`load_bustypes_from_db`, :func:`save_branchtype_to_db`,
:func:`load_branchtypes_from_db`, :func:`save_network_to_db`,
:func:`load_network_from_db` -- keep their names, signatures, messages and
errors. They delegate to a *default* ``Database`` that
:func:`start_dbsession` creates for the configured path (and now returns);
without one they raise ``RuntimeError`` exactly as before, rather than
silently creating ``grounding.db`` in the working directory.

The old globals ``groundinsight.session``, ``db_session``, ``engine`` and
``SessionLocal`` are deprecated aliases resolved through the package's
module-level ``__getattr__`` (PEP 562); see :func:`deprecated_attribute`.
"""

import contextlib
import logging
import os
import threading
from typing import Dict, Iterator, List, Optional, Union

from sqlalchemy import create_engine
from sqlalchemy.engine import URL, Engine
from sqlalchemy.orm import Session, scoped_session, sessionmaker
from sqlalchemy.pool import StaticPool

from ..models.core_models import BranchType, BusType, Network
from ..models.database_models import Base, BranchTypeDB, BusTypeDB, NetworkDB
from . import crud
from .migration import MigrationReport, migrate_database, needs_migration

logger = logging.getLogger(__name__)

_PathLike = Union[str, "os.PathLike[str]"]

#: Paths that select an in-memory database instead of a file.
_IN_MEMORY = ("", ":memory:")

#: One re-entrant lock per database file and process, shared by every
#: ``Database`` object on that file. Entries are never removed; there is one
#: per distinct file a process opens.
_FILE_LOCKS: Dict[str, "threading.RLock"] = {}
_FILE_LOCKS_GUARD = threading.Lock()


def _file_lock(path: str) -> "threading.RLock":
    """
    The process-wide lock that serialises operations on one database file.

    Parameters
    ----------
    path : str
        Path of the SQLite file as given by the caller.

    Returns
    -------
    threading.RLock
        The same lock for every spelling of the same file (resolved,
        case-normalised path).
    """
    key = os.path.normcase(os.path.realpath(path))
    with _FILE_LOCKS_GUARD:
        lock = _FILE_LOCKS.get(key)
        if lock is None:
            lock = _FILE_LOCKS[key] = threading.RLock()
        return lock


class Database:
    """
    A SQLite database of bus types, branch types and networks.

    The object owns its SQLAlchemy engine and session factory; each method
    runs in its own short-lived session. Several ``Database`` objects -- on
    the same or on different files -- can be open at the same time, and one
    object can be used from several threads.

    Parameters
    ----------
    path : str or os.PathLike
        The SQLite file; created if it does not exist. ``":memory:"`` gives a
        private in-memory database that lives as long as the object.
    migrate : bool, optional, keyword-only
        If ``True`` (the default), a file written by an earlier release is
        converted to the current schema before the engine is bound, after the
        unmodified file has been copied to ``<path>.bak`` (see
        :func:`groundinsight.migrate_database`). If ``False``, the file is
        left untouched and the first read or write of a legacy file raises a
        ``RuntimeError`` that names :meth:`migrate`.

    Raises
    ------
    ValueError
        If the automatic migration refuses the file (for example a pickled
        frequency list that is not a list of numbers).
    RuntimeError
        If the automatic migration cannot convert the file.
    sqlalchemy.exc.DatabaseError
        If the file is not a SQLite database.

    Notes
    -----
    Bus and branch types are a *global catalogue* inside one database: every
    stored network references its types by name. Saving a network whose type
    formulas differ from the stored ones, while another stored network uses
    that type, is refused unless ``overwrite_types=True``.

    Operations on the same file are serialised by a lock that every
    ``Database`` object of the process shares for that file; operations on
    different files run in parallel. Use the object as a context manager, or
    call :meth:`close`, to release the file.

    Examples
    --------
    >>> import groundinsight as gi
    >>> with gi.Database("library.db") as db:  # doctest: +SKIP
    ...     db.save_network(net)
    ...     db.list_networks()
    ['demo']
    >>> with gi.Database(":memory:") as db:
    ...     db.list_networks()
    []
    """

    def __init__(self, path: _PathLike, *, migrate: bool = True) -> None:
        self._path = os.fspath(path)
        self._in_memory = self._path in _IN_MEMORY
        self._lock = (
            threading.RLock() if self._in_memory else _file_lock(self._path)
        )
        self._closed = False
        self._scoped: Optional[scoped_session] = None
        with self._lock:
            if self._in_memory:
                # One shared connection: an in-memory database exists once per
                # connection, and the lock above serialises its use.
                engine = create_engine(
                    "sqlite://",
                    poolclass=StaticPool,
                    connect_args={"check_same_thread": False},
                )
            else:
                if migrate:
                    # Convert a file written by an earlier release *before*
                    # binding to it: ``create_all`` only ever creates missing
                    # tables and would leave a legacy file looking fine until
                    # the first query hit a missing column.
                    migrate_database(self._path)
                # ``URL.create`` instead of an f-string: a path containing
                # ``?`` or ``#`` would otherwise be parsed as URL syntax.
                engine = create_engine(URL.create("sqlite", database=self._path))
            try:
                Base.metadata.create_all(engine)
            except Exception:
                engine.dispose()
                raise
        self._engine: Engine = engine
        self._session_factory = sessionmaker(bind=engine)
        logger.debug("Opened database '%s'.", self._path)

    # -- object protocol -----------------------------------------------------

    def __repr__(self) -> str:
        """Show the path, and whether the database is closed."""
        state = ", closed" if self._closed else ""
        return f"Database({self._path!r}{state})"

    def __enter__(self) -> "Database":
        """Return the database itself; :meth:`close` runs on exit."""
        return self

    def __exit__(self, exc_type, exc_value, traceback) -> None:
        """Close the database; exceptions from the block propagate."""
        self.close()

    @property
    def path(self) -> str:
        """str : The database file as given to the constructor."""
        return self._path

    @property
    def closed(self) -> bool:
        """bool : ``True`` once :meth:`close` has run."""
        return self._closed

    @property
    def engine(self) -> Engine:
        """sqlalchemy.engine.Engine : The engine this database owns.

        For direct SQL access. Prefer :meth:`session` for ORM work; do not
        dispose the engine yourself -- :meth:`close` does that.
        """
        return self._engine

    def close(self) -> None:
        """
        Release the database: drop the connections and dispose the engine.

        Idempotent and never raises; problems while tearing down are logged at
        ``WARNING``. Every later operation raises ``RuntimeError``.
        """
        with self._lock:
            if self._closed:
                return
            self._closed = True
            if self._scoped is not None:
                try:
                    self._scoped.remove()
                except Exception as exc:  # pragma: no cover - defensive
                    logger.warning("Error while removing scoped session: %s", exc)
                self._scoped = None
            try:
                self._engine.dispose()
            except Exception as exc:  # pragma: no cover - defensive
                logger.warning("Error while disposing engine: %s", exc)
        logger.debug("Closed database '%s'.", self._path)

    @contextlib.contextmanager
    def session(self) -> Iterator[Session]:
        """
        A short-lived SQLAlchemy session for direct ORM access.

        The session is closed -- not committed -- when the block ends; commit
        explicitly if you write. Operations on this file wait until the block
        has finished.

        Yields
        ------
        sqlalchemy.orm.Session
            A new session bound to this database.

        Raises
        ------
        RuntimeError
            If the database is closed.

        Examples
        --------
        >>> from groundinsight.database.crud import load_network
        >>> with db.session() as session:  # doctest: +SKIP
        ...     net = load_network("demo", session)
        """
        with self._lock:
            self._require_open()
            session = self._session_factory()
            try:
                yield session
            finally:
                session.close()

    # -- types ---------------------------------------------------------------

    def save_bustype(self, bus_type: BusType, overwrite: bool = False) -> None:
        """
        Save a BusType to the catalogue.

        Parameters
        ----------
        bus_type : BusType
            The type to save.
        overwrite : bool, optional
            Replace a stored type of the same name. Defaults to ``False``.

        Raises
        ------
        ValueError
            If the type exists and ``overwrite`` is ``False``.
        RuntimeError
            If the database is closed.
        """
        with self.session() as session:
            if session.get(BusTypeDB, bus_type.name) is not None and not overwrite:
                raise ValueError(
                    f"BusType '{bus_type.name}' already exists. Use "
                    "overwrite=True to overwrite."
                )
            crud.save_bustype(bus_type, session)

    def load_bustypes(self) -> Dict[str, BusType]:
        """
        Load every stored BusType.

        Returns
        -------
        dict of str to BusType
            The types, keyed by name.

        Raises
        ------
        RuntimeError
            If the database is closed.
        """
        with self.session() as session:
            return crud.load_bustypes(session)

    def delete_bustype(self, name: str) -> None:
        """
        Delete a stored BusType that no stored network uses.

        Parameters
        ----------
        name : str
            Name of the type.

        Raises
        ------
        ValueError
            If the type is not stored, or a stored network still uses it.
        RuntimeError
            If the database is closed, or still uses the legacy schema.
        """
        with self.session() as session:
            crud.delete_bustype(name, session)

    def save_branchtype(self, branch_type: BranchType, overwrite: bool = False) -> None:
        """
        Save a BranchType to the catalogue.

        Parameters
        ----------
        branch_type : BranchType
            The type to save.
        overwrite : bool, optional
            Replace a stored type of the same name. Defaults to ``False``.

        Raises
        ------
        ValueError
            If the type exists and ``overwrite`` is ``False``.
        RuntimeError
            If the database is closed.
        """
        with self.session() as session:
            if (
                session.get(BranchTypeDB, branch_type.name) is not None
                and not overwrite
            ):
                raise ValueError(
                    f"BranchType '{branch_type.name}' already exists. Use "
                    "overwrite=True to overwrite."
                )
            crud.save_branchtype(branch_type, session)

    def load_branchtypes(self) -> Dict[str, BranchType]:
        """
        Load every stored BranchType.

        Returns
        -------
        dict of str to BranchType
            The types, keyed by name.

        Raises
        ------
        RuntimeError
            If the database is closed.
        """
        with self.session() as session:
            return crud.load_branchtypes(session)

    def delete_branchtype(self, name: str) -> None:
        """
        Delete a stored BranchType that no stored network uses.

        Parameters
        ----------
        name : str
            Name of the type.

        Raises
        ------
        ValueError
            If the type is not stored, or a stored network still uses it.
        RuntimeError
            If the database is closed, or still uses the legacy schema.
        """
        with self.session() as session:
            crud.delete_branchtype(name, session)

    # -- networks ------------------------------------------------------------

    def save_network(
        self,
        network: Network,
        overwrite: bool = False,
        overwrite_types: bool = False,
    ) -> None:
        """
        Save a network with all of its elements, in one transaction.

        Parameters
        ----------
        network : Network
            The network to save. Stored results are not part of the database
            schema; use a JSON project file to keep them.
        overwrite : bool, optional
            Replace a stored network of the same name. Defaults to ``False``.
        overwrite_types : bool, optional
            Allow this save to rewrite the formulas of bus/branch types that
            other stored networks also use. Defaults to ``False``.

        Raises
        ------
        ValueError
            If the network exists and ``overwrite`` is ``False``, if a path
            references a branch that is not in the network, or if a shared
            type would be rewritten without ``overwrite_types=True``.
        RuntimeError
            If the database is closed, or still uses the legacy schema.

        See Also
        --------
        groundinsight.database.crud.save_network : the transaction itself.
        """
        with self.session() as session:
            if session.get(NetworkDB, network.name) is not None and not overwrite:
                raise ValueError(
                    f"Network '{network.name}' already exists. Use "
                    "overwrite=True to overwrite."
                )
            crud.save_network(
                network, session, overwrite=overwrite, overwrite_types=overwrite_types
            )

    def load_network(self, name: str) -> Network:
        """
        Load a stored network by name.

        Parameters
        ----------
        name : str
            Name of the network.

        Returns
        -------
        Network
            The network, without results.

        Raises
        ------
        ValueError
            If no network of that name is stored.
        RuntimeError
            If the database is closed, or still uses the legacy schema.
        """
        with self.session() as session:
            return crud.load_network(name, session)

    def list_networks(self) -> List[str]:
        """
        Names of the stored networks.

        Returns
        -------
        list of str
            Sorted names.

        Raises
        ------
        RuntimeError
            If the database is closed, or still uses the legacy schema.
        """
        with self.session() as session:
            return crud.list_networks(session)

    def delete_network(self, name: str) -> None:
        """
        Delete a stored network and all of its elements; types stay.

        Parameters
        ----------
        name : str
            Name of the network.

        Raises
        ------
        ValueError
            If no network of that name is stored.
        RuntimeError
            If the database is closed, or still uses the legacy schema.
        """
        with self.session() as session:
            crud.delete_network(name, session)

    # -- schema --------------------------------------------------------------

    def needs_migration(self) -> str:
        """
        Classify the file against the current schema.

        Returns
        -------
        str
            ``"none"``, ``"legacy_scoped"``, ``"add_columns"`` or
            ``"pickle_frequencies"``; see
            :func:`groundinsight.needs_migration`.
        """
        if self._in_memory:
            return "none"
        with self._lock:
            return needs_migration(self._path)

    def migrate(self, backup: bool = True) -> MigrationReport:
        """
        Convert the file to the current schema (for ``migrate=False``).

        The engine's pooled connections are released first, because the
        conversion may replace the file.

        Parameters
        ----------
        backup : bool, optional
            Copy the unmodified file to ``<path>.bak`` first. Defaults to
            ``True``.

        Returns
        -------
        MigrationReport
            What was done, and what could not be recovered.

        Raises
        ------
        RuntimeError
            If the database is closed, or the file cannot be converted.
        ValueError
            If the file carries a payload the migration refuses.
        """
        with self._lock:
            self._require_open()
            if self._in_memory:
                return MigrationReport(path=self._path)
            self._engine.dispose()
            report = migrate_database(self._path, backup=backup)
            Base.metadata.create_all(self._engine)
            return report

    # -- internals -----------------------------------------------------------

    def _require_open(self) -> None:
        """
        Refuse to work on a closed database.

        Raises
        ------
        RuntimeError
            If :meth:`close` has run.
        """
        if self._closed:
            raise RuntimeError(f"Database '{self._path}' is closed.")

    def _legacy_scoped_session(self) -> scoped_session:
        """
        The thread-local session registry behind the deprecated aliases.

        Returns
        -------
        sqlalchemy.orm.scoped_session
            Created on first use and kept, so that ``gi.session`` and
            ``gi.db_session`` are the same object until :meth:`close`.
        """
        with self._lock:
            self._require_open()
            if self._scoped is None:
                self._scoped = scoped_session(self._session_factory)
            return self._scoped


# ---------------------------------------------------------------------------
# The default instance behind the module-level functions
# ---------------------------------------------------------------------------

_default: Optional[Database] = None
_default_guard = threading.RLock()

_NOT_STARTED = "Database session is not started. Call gi.start_dbsession() first."


def default_database() -> Optional[Database]:
    """
    The database the module-level functions use, if one is open.

    Returns
    -------
    Database or None
        The object :func:`start_dbsession` created, or ``None`` when no
        session is started (or the object was closed directly).
    """
    database = _default
    if database is None or database.closed:
        return None
    return database


def _require_default() -> Database:
    """
    The open default database, or the 0.6.0 error.

    Returns
    -------
    Database
        The default database.

    Raises
    ------
    RuntimeError
        If no session is started.
    """
    database = default_database()
    if database is None:
        raise RuntimeError(_NOT_STARTED)
    return database


def start_dbsession(
    sqlite_path: str = "grounding.db",
    force: bool = False,
    migrate: bool = True,
) -> Database:
    """
    Open the default database used by the module-level persistence functions.

    Idempotent in the safe direction: if a session is already active for the
    *same* ``sqlite_path``, it is kept and a ``WARNING`` is logged. Binding to
    a *different* file raises ``RuntimeError`` unless ``force=True``, in which
    case the previous database is closed first. To work with several files at
    once, use :class:`Database` objects instead.

    A file written by an earlier release is migrated to the current schema
    before it is opened, after the unmodified file has been copied to
    ``<sqlite_path>.bak``; see
    :func:`groundinsight.database.migration.migrate_database`.

    Parameters
    ----------
    sqlite_path : str, optional
        The SQLite file. Defaults to ``"grounding.db"``.
    force : bool, optional
        Close an active default database for another file and rebind.
        Defaults to ``False``.
    migrate : bool, optional
        Convert a database written by an earlier release (the default). Set
        to ``False`` to leave the file untouched and get an explanatory
        ``RuntimeError`` from the first read or write instead.

    Returns
    -------
    Database
        The default database. Its methods (``list_networks``,
        ``delete_network``, ``session`` ...) are available directly; the
        module-level functions use the same object.

    Raises
    ------
    RuntimeError
        If a session is already active for a different ``sqlite_path`` and
        ``force`` is not set.

    Examples
    --------
    >>> import groundinsight as gi
    >>> db = gi.start_dbsession("library.db")  # doctest: +SKIP
    >>> gi.save_network_to_db(net)  # doctest: +SKIP
    >>> db.list_networks()  # doctest: +SKIP
    ['demo']
    >>> gi.close_dbsession()  # doctest: +SKIP
    """
    global _default
    with _default_guard:
        current = default_database()
        if current is not None:
            requested = os.fspath(sqlite_path)
            if current.path == requested and not force:
                logger.warning(
                    "Database session already started for '%s' — re-using "
                    "existing engine.",
                    sqlite_path,
                )
                return current
            current_url = f"sqlite:///{current.path}"
            if not force:
                raise RuntimeError(
                    "A database session is already active for "
                    f"'{current_url}'. Call gi.close_dbsession() before "
                    f"binding to '{sqlite_path}', or pass force=True to "
                    "dispose the existing engine and rebind."
                )
            logger.warning(
                "Forcing re-bind of database session from '%s' to '%s'.",
                current_url,
                f"sqlite:///{requested}",
            )
            close_dbsession()
        _default = Database(sqlite_path, migrate=migrate)
        logger.info("Database session started with '%s'.", sqlite_path)
        return _default


def close_dbsession() -> None:
    """
    Close the default database opened by :func:`start_dbsession`.

    Never raises. Logs at ``WARNING`` if no session is active and at ``INFO``
    when one was closed. :class:`Database` objects created directly are not
    affected.
    """
    global _default
    with _default_guard:
        current, _default = _default, None
    if current is None:
        logger.warning("No database session to close.")
        return
    try:
        current.close()
    except Exception as exc:  # pragma: no cover - Database.close never raises
        logger.warning("Error while closing database '%s': %s", current.path, exc)
    logger.info("Database session closed.")


def save_bustype_to_db(bus_type: BusType, overwrite: bool = False) -> None:
    """
    Save a BusType to the default database.

    Parameters
    ----------
    bus_type : BusType
        The BusType instance to save.
    overwrite : bool, optional
        Whether to overwrite an existing BusType with the same name.
        Defaults to ``False``.

    Raises
    ------
    RuntimeError
        If the database session is not started.
    ValueError
        If the BusType already exists and ``overwrite`` is ``False``.
    """
    _require_default().save_bustype(bus_type, overwrite=overwrite)


def load_bustypes_from_db() -> Dict[str, BusType]:
    """
    Load all BusTypes from the default database.

    Returns
    -------
    dict of str to BusType
        A dictionary of BusType instances keyed by their names.

    Raises
    ------
    RuntimeError
        If the database session is not started.
    """
    return _require_default().load_bustypes()


def save_branchtype_to_db(branch_type: BranchType, overwrite: bool = False) -> None:
    """
    Save a BranchType to the default database.

    Parameters
    ----------
    branch_type : BranchType
        The BranchType instance to save.
    overwrite : bool, optional
        Whether to overwrite an existing BranchType with the same name.
        Defaults to ``False``.

    Raises
    ------
    RuntimeError
        If the database session is not started.
    ValueError
        If the BranchType already exists and ``overwrite`` is ``False``.
    """
    _require_default().save_branchtype(branch_type, overwrite=overwrite)


def load_branchtypes_from_db() -> Dict[str, BranchType]:
    """
    Load all BranchTypes from the default database.

    Returns
    -------
    dict of str to BranchType
        A dictionary of BranchType instances keyed by their names.

    Raises
    ------
    RuntimeError
        If the database session is not started.
    """
    return _require_default().load_branchtypes()


def save_network_to_db(
    network: Network, overwrite: bool = False, overwrite_types: bool = False
) -> None:
    """
    Save a Network to the default database.

    Parameters
    ----------
    network : Network
        The Network instance to save.
    overwrite : bool, optional
        Whether to overwrite an existing Network with the same name.
        Defaults to ``False``.
    overwrite_types : bool, optional
        Whether this save may rewrite the formulas of bus/branch types that
        other stored networks also reference. Bus and branch types are a
        *global* catalogue keyed by name, so such a change reaches those
        networks as well. Defaults to ``False``, which raises instead.

    Raises
    ------
    RuntimeError
        If the database session is not started.
    ValueError
        If the Network already exists and ``overwrite`` is ``False``, or if a
        shared type definition would be rewritten and ``overwrite_types`` is
        ``False``.
    """
    _require_default().save_network(
        network, overwrite=overwrite, overwrite_types=overwrite_types
    )


def load_network_from_db(name: str) -> Network:
    """
    Load a Network from the default database by name.

    Parameters
    ----------
    name : str
        The name of the Network to load.

    Returns
    -------
    Network
        The loaded Network instance.

    Raises
    ------
    RuntimeError
        If the database session is not started.
    ValueError
        If the Network with the specified name does not exist.
    """
    return _require_default().load_network(name)


# ---------------------------------------------------------------------------
# Deprecated package attributes
# ---------------------------------------------------------------------------

#: Former module globals of the package, now resolved on access.
DEPRECATED_ATTRIBUTES = ("session", "db_session", "engine", "SessionLocal")

_SESSION_REPLACEMENT = (
    "the methods of a gi.Database object, or `with db.session() as s:` for a "
    "short-lived SQLAlchemy session"
)
_REPLACEMENTS = {
    "session": _SESSION_REPLACEMENT,
    "db_session": _SESSION_REPLACEMENT,
    "engine": "Database.engine",
    "SessionLocal": "Database.session()",
}


def deprecation_message(name: str) -> str:
    """
    The ``DeprecationWarning`` text for a former package global.

    Parameters
    ----------
    name : str
        One of :data:`DEPRECATED_ATTRIBUTES`.

    Returns
    -------
    str
        Message naming the replacement and the removal policy.
    """
    return (
        f"groundinsight.{name} is deprecated and will be removed no earlier "
        "than two releases and six months after the release that deprecated "
        f"it. Use {_REPLACEMENTS[name]} instead: db = gi.Database(path), or "
        "the object returned by gi.start_dbsession(path)."
    )


def deprecated_attribute(name: str):
    """
    The value behind a deprecated package global.

    Parameters
    ----------
    name : str
        One of :data:`DEPRECATED_ATTRIBUTES`.

    Returns
    -------
    object or None
        ``None`` while no default database is open (as the globals were);
        otherwise the default database's thread-local scoped session for
        ``session`` / ``db_session`` (the same object for both), its engine
        for ``engine`` and its session factory for ``SessionLocal``.

    Raises
    ------
    AttributeError
        If ``name`` is not a deprecated attribute.
    """
    if name not in DEPRECATED_ATTRIBUTES:
        raise AttributeError(name)
    database = default_database()
    if database is None:
        return None
    if name in ("session", "db_session"):
        return database._legacy_scoped_session()
    if name == "engine":
        return database.engine
    return database._session_factory

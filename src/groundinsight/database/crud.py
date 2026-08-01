# database/crud.py


"""
CRUD Operations Module.

This module provides functions for creating, reading, updating, and deleting (CRUD) entities
in the GroundInsight database using SQLAlchemy sessions. It facilitates the management of
core electrical network components such as BusTypes, BranchTypes, Networks, Buses, Branches,
Faults, Sources, and Paths. The functions convert between Pydantic models and SQLAlchemy
database models to ensure seamless data manipulation and persistence.
"""

from groundinsight.models.core_models import Network, BusType, BranchType
from sqlalchemy import inspect
from sqlalchemy.orm import Session
from groundinsight.models.database_models import (
    BusTypeDB,
    BusDB,
    BranchTypeDB,
    BranchDB,
    FaultDB,
    SourceDB,
    PathDB,
    PathSegmentDB,
    NetworkDB,
)
from typing import Dict, Tuple

#: Association tables of the pre-network-scoped schema. Their presence is not
#: what makes a database unreadable -- the missing ``network_name`` column on
#: ``buses`` is -- but naming them makes the diagnostic concrete.
_LEGACY_ASSOCIATION_TABLES: Tuple[str, ...] = (
    "network_buses",
    "network_branches",
    "network_faults",
    "network_sources",
    "network_paths",
)


def ensure_current_schema(session: Session):
    """
    Reject databases written by the pre-network-scoped schema.

    Up to and including the association-table schema, ``buses``, ``branches``,
    ``faults``, ``sources`` and ``paths`` were keyed by element name alone and
    linked to their network through ``network_buses`` & co. Two networks
    containing an element of the same name therefore shared one row, so saving
    one network silently rewrote the other. Those tables are now keyed by
    ``(network_name, name)``.

    ``Base.metadata.create_all`` only ever creates *missing* tables -- it never
    adds a column to an existing one -- so an old database file opens without
    complaint and only fails deep inside a query with a bare
    ``OperationalError: no such column: buses.network_name``. This helper turns
    that into an actionable message before any row is read or written.

    This is the *last* line of defence, not the normal path:
    :func:`groundinsight.start_dbsession` converts such a file automatically
    (keeping a ``.bak`` copy) before the engine is bound. The error is reached
    when the caller passed ``migrate=False``, or built its own engine and
    session without going through ``start_dbsession``.

    Parameters
    ----------
    session : Session
        The SQLAlchemy session whose bind is inspected.

    Raises
    ------
    RuntimeError
        If the connected database still uses the legacy, globally-keyed
        element tables. The message names
        :func:`groundinsight.migrate_database`.

    See Also
    --------
    groundinsight.database.migration.migrate_database : performs the
        conversion this function refuses to do implicitly.
    """
    inspector = inspect(session.connection())
    table_names = set(inspector.get_table_names())
    if "buses" not in table_names:
        # Nothing written yet; ``create_all`` will lay out the current schema.
        return
    if any(column["name"] == "network_name" for column in inspector.get_columns("buses")):
        return

    legacy_tables = [name for name in _LEGACY_ASSOCIATION_TABLES if name in table_names]
    database = getattr(session.get_bind().url, "database", None) or "<your file>.db"
    raise RuntimeError(
        "This database uses the legacy groundinsight schema, in which buses, "
        "branches, faults, sources and paths were keyed by name alone and "
        "shared between networks"
        + (f" (found: {', '.join(legacy_tables)})" if legacy_tables else "")
        + ". Element tables are now keyed by (network_name, name). Convert the "
        f"file with gi.migrate_database('{database}') -- it copies the "
        "unmodified file to a .bak sibling first and reports anything it could "
        "not recover -- or let gi.start_dbsession() do it for you, which is "
        "the default."
    )


def _validate_path_segments(network: Network):
    """
    Check that every path segment refers to a branch of the network.

    ``Network.paths`` holds ``Branch`` objects, not names, so a user who prunes
    ``network.branches`` after :func:`groundinsight.create_paths` leaves paths
    pointing at branches that are no longer part of the network. Persisting
    such a path used to fail with ``AttributeError: 'NoneType' object has no
    attribute '_sa_instance_state'`` deep inside SQLAlchemy.

    Parameters
    ----------
    network : Network
        The network about to be persisted.

    Raises
    ------
    ValueError
        If a path segment names a branch that is not in ``network.branches``.
    """
    known_branches = {branch.name for branch in network.branches.values()}
    for path in network.paths.values():
        for segment in path.segments:
            if segment.name not in known_branches:
                raise ValueError(
                    f"Path '{path.name}' of network '{network.name}' references "
                    f"branch '{segment.name}', which is not part of "
                    "network.branches. Re-run gi.create_paths(network=...) after "
                    "changing the branches, or drop the stale path before saving."
                )


def save_bustype(bus_type: BusType, session: Session):
    """
    Save a BusType to the database.

    This function converts a Pydantic `BusType` model to its corresponding SQLAlchemy
    `BusTypeDB` model and saves it to the database. If a BusType with the same name
    already exists, it will be updated.

    Parameters
    ----------
    bus_type : BusType
        The BusType instance to be saved.
    session : Session
        The SQLAlchemy session used for database operations.

    Raises
    ------
    Exception
        If there is an error during the database commit.

    """
    bus_type_db = BusTypeDB.from_pydantic(bus_type)
    session.merge(bus_type_db)
    session.commit()


def load_bustypes(session: Session) -> Dict[str, BusType]:
    """
    Load all BusTypes from the database.

    This function retrieves all BusType entries from the database and converts them
    into a dictionary mapping BusType names to their corresponding Pydantic models.

    Parameters
    ----------
    session : Session
        The SQLAlchemy session used for database operations.

    Returns
    -------
    Dict[str, BusType]
        A dictionary where keys are BusType names and values are BusType instances.
    """
    bus_types = session.query(BusTypeDB).all()
    return {bt.name: bt.to_pydantic() for bt in bus_types}


def save_branchtype(branch_type: BranchType, session: Session):
    """
    Save a BranchType to the database.

    This function converts a Pydantic `BranchType` model to its corresponding SQLAlchemy
    `BranchTypeDB` model and saves it to the database. If a BranchType with the same name
    already exists, it will be updated.

    Parameters
    ----------
    branch_type : BranchType
        The BranchType instance to be saved.
    session : Session
        The SQLAlchemy session used for database operations.

    Raises
    ------
    Exception
        If there is an error during the database commit.
    """
    branch_type_db = BranchTypeDB.from_pydantic(branch_type)
    session.merge(branch_type_db)
    session.commit()


def load_branchtypes(session: Session) -> Dict[str, BranchType]:
    """
    Load all BranchTypes from the database.

    This function retrieves all BranchType entries from the database and converts them
    into a dictionary mapping BranchType names to their corresponding Pydantic models.

    Parameters
    ----------
    session : Session
        The SQLAlchemy session used for database operations.

    Returns
    -------
    Dict[str, BranchType]
        A dictionary where keys are BranchType names and values are BranchType instances.
    """
    branch_types = session.query(BranchTypeDB).all()
    return {bt.name: bt.to_pydantic() for bt in branch_types}


def save_network(network: Network, session: Session, overwrite: bool = False):
    """
    Save a Network to the database.

    This function saves a comprehensive `Network` instance to the database, including all
    associated BusTypes, BranchTypes, Buses, Branches, Faults, Sources, and Paths. It handles
    the creation or updating of related entities and ensures referential integrity. If `overwrite`
    is set to `True`, an existing network with the same name will be deleted and replaced.

    BusTypes and BranchTypes are a *global catalogue*: they are merged, exactly
    as :func:`save_bustype` and :func:`save_branchtype` do, so re-saving a
    network with an edited type definition updates the stored type instead of
    silently keeping the old one. Every other element is scoped to this network
    and is written under the composite key ``(network.name, element.name)``, so
    saving one network can never rewrite another's buses, branches, faults,
    sources or paths.

    The whole operation runs in a single transaction. On overwrite, the delete
    of the previous revision is *flushed but not committed* before the
    replacement rows are written, so a failure anywhere in the save rolls the
    delete back as well and leaves the stored network untouched.

    Parameters
    ----------
    network : Network
        The Network instance to be saved.
    session : Session
        The SQLAlchemy session used for database operations.
    overwrite : bool, optional
        If `True`, existing network data with the same name will be overwritten.
        Defaults to `False`.

    Raises
    ------
    ValueError
        If the network already exists and `overwrite` is set to `False`, or if a
        path references a branch that is not part of ``network.branches``.
    RuntimeError
        If the database still uses the legacy, globally-keyed element tables.
    Exception
        If there is an error during the database commit. The transaction is
        rolled back before the exception is re-raised.
    """
    ensure_current_schema(session)

    # Check for existing network
    existing_network = session.get(NetworkDB, network.name)
    if existing_network and not overwrite:
        raise ValueError(
            f"Network '{network.name}' already exists. Use overwrite=True to overwrite."
        )

    # Fail before touching the database rather than half-way through the write.
    _validate_path_segments(network)

    try:
        # Save BusTypes / BranchTypes -- merge, so an edited type definition
        # replaces the stored one instead of being ignored when the name exists.
        for bus in network.buses.values():
            session.merge(BusTypeDB.from_pydantic(bus.type))
        for branch in network.branches.values():
            session.merge(BranchTypeDB.from_pydantic(branch.type))

        if existing_network is not None:
            # Drop the previous revision including its child rows (the
            # relationships cascade), then flush so the primary keys are free
            # for the replacement rows. This stays inside the transaction
            # opened above -- no commit happens until the new rows are written.
            session.delete(existing_network)
            session.flush()

        network_db = NetworkDB.from_pydantic(network)
        network_db.active_fault_name = network.active_fault

        # ``Network`` element dictionaries are keyed by element name (see
        # ``Network.add_bus`` & co.), and ``to_pydantic`` rebuilds them from
        # the stored names, so the values -- not the dictionary keys -- are the
        # source of truth here. The enumeration index preserves the dictionary
        # order across the round-trip.
        network_db.buses = [
            BusDB.from_pydantic(bus, network.name, position)
            for position, bus in enumerate(network.buses.values())
        ]
        network_db.branches = [
            BranchDB.from_pydantic(branch, network.name, position)
            for position, branch in enumerate(network.branches.values())
        ]
        network_db.faults = [
            FaultDB.from_pydantic(fault, network.name, position)
            for position, fault in enumerate(network.faults.values())
        ]
        network_db.sources = [
            SourceDB.from_pydantic(source, network.name, position)
            for position, source in enumerate(network.sources.values())
        ]

        path_dbs = []
        for position, path in enumerate(network.paths.values()):
            path_db = PathDB.from_pydantic(path, network.name, position)
            # Segment order is semantically meaningful, so it is stored
            # explicitly instead of being left to the database.
            path_db.segments = [
                PathSegmentDB(
                    network_name=network.name,
                    path_name=path.name,
                    position=segment_position,
                    branch_name=segment.name,
                )
                for segment_position, segment in enumerate(path.segments)
            ]
            path_dbs.append(path_db)
        network_db.paths = path_dbs

        session.add(network_db)
        session.flush()

        # Commit the session only once every replacement row is on disk.
        session.commit()
    except Exception:
        # Roll the delete and the partial write back as one unit so a failed
        # overwrite cannot destroy the stored network.
        session.rollback()
        raise


def load_network(name: str, session: Session) -> Network:
    """
    Load a Network from the database.

    This function retrieves a `Network` instance by its name from the database and converts it
    into a Pydantic `Network` model. It ensures that all related entities such as Buses, Branches,
    Faults, Sources, and Paths are properly associated.

    Parameters
    ----------
    name : str
        The name of the network to load.
    session : Session
        The SQLAlchemy session used for database operations.

    Returns
    -------
    Network
        The loaded `Network` instance.

    Raises
    ------
    ValueError
        If the specified network does not exist in the database.
    RuntimeError
        If the database still uses the legacy, globally-keyed element tables.
    """
    ensure_current_schema(session)
    network_db = session.get(NetworkDB, name)
    if not network_db:
        raise ValueError(f"Network '{name}' not found.")
    network = network_db.to_pydantic()
    return network

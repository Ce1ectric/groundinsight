# database/migration.py


"""
Schema migration for existing SQLite databases.

groundinsight up to v0.4.0 keyed ``buses``, ``branches``, ``faults``,
``sources`` and ``paths`` by element *name alone* and linked them to their
network through the association tables ``network_buses``, ``network_branches``,
``network_faults``, ``network_sources`` and ``network_paths``. Two networks
that contained an element of the same name therefore shared one physical row,
so saving one network silently rewrote the other. The element tables are now
keyed by ``(network_name, name)``.

``Base.metadata.create_all`` only ever creates *missing* tables — it never adds
a column to an existing one — so an old database file opens without complaint
and then fails deep inside a query with a bare ``OperationalError: no such
column: buses.network_name``. This module converts such a file instead, so an
existing user keeps working:

* :func:`needs_migration` reports what, if anything, has to change.
* :func:`migrate_database` performs the conversion, after copying the file to
  a ``.bak`` sibling.

:func:`groundinsight.start_dbsession` calls :func:`migrate_database`
automatically; pass ``migrate=False`` to get the diagnostic error instead.

Two properties of the legacy schema cannot be recovered and are *reported*
rather than guessed:

Shared elements
    A bus that two networks both referenced existed once. Splitting the row
    per network can only duplicate the one surviving definition into each of
    them. If the networks meant different things by that name, the older
    definition is already gone — it was overwritten on the last save, before
    this migration ever ran. Every such name is listed in
    :attr:`MigrationReport.shared_elements` and logged at ``WARNING``.

Path segment order
    The legacy ``path_segments`` table had no ``position`` column; a path was
    an unordered set of ``(path_name, branch_name)`` rows. Order is therefore
    reconstructed from SQLite's ``rowid``, which reflects insertion order and
    hence the original segment sequence. The result is *verified* by walking
    each path bus-by-bus; paths whose segments do not form a chain are listed
    in :attr:`MigrationReport.broken_paths` and must be rebuilt with
    :func:`groundinsight.create_paths`.
"""

import logging
import os
import shutil
import sqlite3
from typing import Any, Dict, List, Optional, Set, Tuple

from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)


#: Element table -> association table of the pre-network-scoped schema. The
#: association tables are what the network membership has to be read from;
#: they are dropped afterwards because the new schema carries the network on
#: the element row itself.
_LEGACY_ASSOCIATIONS: Dict[str, str] = {
    "buses": "network_buses",
    "branches": "network_branches",
    "faults": "network_faults",
    "sources": "network_sources",
    "paths": "network_paths",
}

#: Element table -> the association column naming the element. The network is
#: always ``network_name``.
_LEGACY_ASSOCIATION_KEYS: Dict[str, str] = {
    "buses": "bus_name",
    "branches": "branch_name",
    "faults": "fault_name",
    "sources": "source_name",
    "paths": "path_name",
}

#: Tables copied across unchanged: they were never network-scoped.
_GLOBAL_TABLES: Tuple[str, ...] = ("bus_types", "branch_types", "complex_numbers")

#: Marker for "this column is mandatory and the schema offers nothing to put
#: there". Distinguishes a column whose declared default happens to be ``None``
#: from one that has no default at all.
_NO_DEFAULT = object()


class MigrationReport(BaseModel):
    """
    What :func:`migrate_database` did, and what a user has to check afterwards.

    Attributes
    ----------
    migrated : bool
        ``True`` if the file was rewritten. ``False`` means it already used
        the current schema and was left untouched.
    path : str
        The database file the report refers to.
    backup_path : Optional[str]
        Where the pre-migration file was copied to, or ``None`` if no backup
        was taken (``backup=False``, or nothing needed migrating).
    kind : str
        ``"none"``, ``"legacy_scoped"`` for the full rebuild from the
        name-keyed schema, or ``"add_columns"`` when only new nullable columns
        were missing.
    networks : List[str]
        Networks found in the file.
    rows : Dict[str, int]
        Rows written per table.
    shared_elements : Dict[str, List[str]]
        ``"table:name"`` -> the networks that shared that one row. Non-empty
        means the migration had to duplicate a definition; see the module
        docstring.
    orphaned : Dict[str, List[str]]
        ``table`` -> element names that no network referenced. They are *not*
        carried over — the new schema has nowhere to put them.
    missing_rows : Dict[str, List[str]]
        ``table`` -> element names a network referenced but which had no row
        in the element table. Skipped.
    broken_paths : List[str]
        ``"network:path"`` for paths whose reconstructed segment order does
        not form a connected chain.
    defaulted_cells : Dict[str, int]
        ``"table.column"`` -> how many ``NULL``\\ s were replaced by the
        column's own declared default (see :func:`_declared_defaults`). A
        non-empty dict means values were filled in, so it counts towards
        :attr:`needs_attention`.
    added_columns : Dict[str, List[str]]
        ``table`` -> columns added in place by the ``"add_columns"`` path.
    unloadable : Dict[str, str]
        ``network`` -> the error :func:`groundinsight.load_network_from_db`
        raised for it on the converted file. A network listed here was written
        successfully but cannot be turned back into a
        :class:`~groundinsight.models.core_models.Network`, almost always
        because a column the database allows to be ``NULL`` is mandatory in the
        Pydantic model. The other networks in the file are unaffected.
    """

    migrated: bool = False
    path: str = ""
    backup_path: Optional[str] = None
    kind: str = "none"
    networks: List[str] = Field(default_factory=list)
    rows: Dict[str, int] = Field(default_factory=dict)
    shared_elements: Dict[str, List[str]] = Field(default_factory=dict)
    orphaned: Dict[str, List[str]] = Field(default_factory=dict)
    missing_rows: Dict[str, List[str]] = Field(default_factory=dict)
    broken_paths: List[str] = Field(default_factory=list)
    defaulted_cells: Dict[str, int] = Field(default_factory=dict)
    added_columns: Dict[str, List[str]] = Field(default_factory=dict)
    unloadable: Dict[str, str] = Field(default_factory=dict)

    @property
    def needs_attention(self) -> bool:
        """``True`` if the migration had to guess or drop something."""
        return bool(
            self.shared_elements
            or self.orphaned
            or self.missing_rows
            or self.broken_paths
            or self.defaulted_cells
            or self.unloadable
        )


# ---------------------------------------------------------------------------
# Inspection
# ---------------------------------------------------------------------------


def _table_names(conn: sqlite3.Connection) -> Set[str]:
    return {
        row[0]
        for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table'"
        )
    }


def _column_names(conn: sqlite3.Connection, table: str) -> List[str]:
    return [row[1] for row in conn.execute(f'PRAGMA table_info("{table}")')]


def _current_metadata():
    """Import the current declarative metadata lazily (avoids a cycle)."""
    from ..models.database_models import Base

    return Base.metadata


def _declared_defaults(table_name: str) -> Dict[str, Any]:
    """
    Mandatory columns of the current schema, and what may be put in them.

    A ``NOT NULL`` column of the target schema that is ``NULL`` (or absent) in
    a legacy row has to be filled somehow. The only value this module is
    willing to invent is the one the schema itself declares -- ``active``
    defaults to ``True`` and ``source_type`` to ``"current"``, so writing those
    reproduces exactly what the current code would have written for a row that
    never mentioned them.

    Everything else maps to :data:`_NO_DEFAULT` and makes the migration stop.
    A ``length`` or a ``scalings`` mapping is *data*, not a schema convention;
    guessing ``0.0`` for a missing branch length would hand the user a network
    that solves cleanly and is physically wrong. Refusing is recoverable -- the
    original file is still there -- while a wrong number is not.

    Parameters
    ----------
    table_name : str
        Table of the current schema.

    Returns
    -------
    Dict[str, Any]
        ``column -> default value``, or ``column -> _NO_DEFAULT`` for a
        mandatory column the schema has no answer for. Nullable columns are
        not listed.
    """
    table = _current_metadata().tables.get(table_name)
    if table is None:
        return {}
    out: Dict[str, Any] = {}
    for column in table.columns:
        if column.nullable:
            continue
        default = column.default
        if default is None or default.is_callable or default.is_sequence:
            out[column.name] = _NO_DEFAULT
        else:
            out[column.name] = default.arg
    return out


def needs_migration(sqlite_path: str) -> str:
    """
    Classify an existing database file against the current schema.

    Parameters
    ----------
    sqlite_path : str
        Path to the SQLite file. A path that does not exist, or an empty
        file, counts as ``"none"`` — ``create_all`` will lay out the current
        schema.

    Returns
    -------
    str
        ``"none"`` if the file is already current, ``"legacy_scoped"`` if the
        element tables are still keyed by name alone and need the full
        rebuild, or ``"add_columns"`` if the keys are current but columns
        added by a later release are missing.

    Examples
    --------
    >>> needs_migration("grounding.db")  # doctest: +SKIP
    'legacy_scoped'
    """
    if not os.path.exists(sqlite_path) or os.path.getsize(sqlite_path) == 0:
        return "none"

    conn = sqlite3.connect(sqlite_path)
    try:
        tables = _table_names(conn)
        if "buses" not in tables:
            # Not a groundinsight database yet, or an empty one.
            return "none"
        if "network_name" not in _column_names(conn, "buses"):
            return "legacy_scoped"

        metadata = _current_metadata()
        for table in metadata.sorted_tables:
            if table.name not in tables:
                # ``create_all`` will add a whole missing table by itself.
                continue
            present = set(_column_names(conn, table.name))
            for column in table.columns:
                if column.name not in present:
                    return "add_columns"
        return "none"
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Backup
# ---------------------------------------------------------------------------


def _backup(sqlite_path: str) -> str:
    """Copy ``sqlite_path`` next to itself without ever clobbering a backup."""
    candidate = f"{sqlite_path}.bak"
    suffix = 0
    while os.path.exists(candidate):
        suffix += 1
        candidate = f"{sqlite_path}.bak.{suffix}"
    shutil.copy2(sqlite_path, candidate)
    return candidate


# ---------------------------------------------------------------------------
# Legacy read
# ---------------------------------------------------------------------------


def _read_memberships(
    conn: sqlite3.Connection, table: str, report: MigrationReport
) -> Dict[str, List[str]]:
    """
    Read ``network -> [element names]`` from a legacy association table.

    Order follows ``rowid``, i.e. the order the elements were originally added,
    which is what the new ``position`` column preserves.
    """
    association = _LEGACY_ASSOCIATIONS[table]
    key = _LEGACY_ASSOCIATION_KEYS[table]
    if association not in _table_names(conn):
        return {}

    memberships: Dict[str, List[str]] = {}
    owners: Dict[str, List[str]] = {}
    rows = conn.execute(
        f'SELECT network_name, "{key}" FROM "{association}" ORDER BY rowid'
    )
    for network_name, element_name in rows:
        if network_name is None or element_name is None:
            continue
        bucket = memberships.setdefault(network_name, [])
        if element_name not in bucket:
            bucket.append(element_name)
        owners.setdefault(element_name, []).append(network_name)

    for element_name, networks in owners.items():
        unique = sorted(set(networks))
        if len(unique) > 1:
            report.shared_elements[f"{table}:{element_name}"] = unique
    return memberships


def _read_rows(conn: sqlite3.Connection, table: str) -> Dict[str, Dict[str, Any]]:
    """Read a legacy element table into ``name -> {column: value}``."""
    columns = _column_names(conn, table)
    out: Dict[str, Dict[str, Any]] = {}
    for row in conn.execute(f'SELECT * FROM "{table}" ORDER BY rowid'):
        record = dict(zip(columns, row))
        out[record["name"]] = record
    return out


def _read_legacy_path_segments(conn: sqlite3.Connection) -> Dict[str, List[str]]:
    """
    Reconstruct ``path -> [branch names]`` from the unordered legacy table.

    The legacy ``path_segments`` table had only ``(path_name, branch_name)``.
    SQLAlchemy inserted the rows of a ``relationship(secondary=...)`` in list
    order, so ``rowid`` order *is* segment order for any file this package
    wrote. :func:`_verify_path_chains` checks the assumption afterwards rather
    than trusting it.
    """
    if "path_segments" not in _table_names(conn):
        return {}
    segments: Dict[str, List[str]] = {}
    for path_name, branch_name in conn.execute(
        "SELECT path_name, branch_name FROM path_segments ORDER BY rowid"
    ):
        if path_name is None or branch_name is None:
            continue
        segments.setdefault(path_name, []).append(branch_name)
    return segments


def _verify_path_chains(
    segments_per_network: Dict[Tuple[str, str], List[str]],
    branches_per_network: Dict[Tuple[str, str], Dict[str, Any]],
    report: MigrationReport,
) -> None:
    """
    Check that each reconstructed segment list forms a connected chain.

    A path is a walk through the network, so consecutive branches must share a
    bus. If they do not, ``rowid`` order was not segment order — the only way
    that assumption can be caught, and the reason it is checked at all.
    """
    for (network_name, path_name), branch_names in segments_per_network.items():
        if len(branch_names) < 2:
            continue
        previous: Optional[Set[str]] = None
        connected = True
        for branch_name in branch_names:
            branch = branches_per_network.get((network_name, branch_name))
            if branch is None:
                connected = False
                break
            ends = {branch.get("from_bus_name"), branch.get("to_bus_name")}
            if previous is not None and not (previous & ends):
                connected = False
                break
            previous = ends
        if not connected:
            report.broken_paths.append(f"{network_name}:{path_name}")


# ---------------------------------------------------------------------------
# Write
# ---------------------------------------------------------------------------


def _insert(
    conn: sqlite3.Connection,
    table: str,
    records: List[Dict[str, Any]],
    report: MigrationReport,
) -> None:
    """
    Insert ``records`` into ``table``, keeping only columns it has.

    A mandatory column that is ``NULL`` in the legacy row is filled from
    :func:`_declared_defaults` and counted in
    :attr:`MigrationReport.defaulted_cells`. A mandatory column with no
    declared default aborts the migration instead of being invented; see
    :func:`_declared_defaults` for why.

    Raises
    ------
    RuntimeError
        If a mandatory column of the current schema has no value in the legacy
        data and no default to fall back on. The message names the affected
        elements.
    """
    if not records:
        report.rows[table] = 0
        return
    available = _column_names(conn, table)
    columns = [name for name in available if any(name in r for r in records)]
    mandatory = _declared_defaults(table)

    # A mandatory column that no record mentions at all would be left out of
    # the INSERT entirely, so it has to be caught before the column list is
    # frozen -- otherwise SQLite reports it as a bare IntegrityError.
    unusable: List[str] = []
    for name, default in mandatory.items():
        if name in columns or name not in available:
            continue
        if default is _NO_DEFAULT:
            unusable.append(f"{table}.{name} (missing for every row)")
        else:
            columns.append(name)

    placeholders = ", ".join("?" for _ in columns)
    quoted = ", ".join(f'"{name}"' for name in columns)
    payload = []
    for record in records:
        values = []
        for name in columns:
            value = record.get(name)
            if value is None and name in mandatory:
                default = mandatory[name]
                if default is _NO_DEFAULT:
                    unusable.append(f"{table}.{name} of '{record.get('name')}'")
                else:
                    value = default
                    key = f"{table}.{name}"
                    report.defaulted_cells[key] = (
                        report.defaulted_cells.get(key, 0) + 1
                    )
            values.append(value)
        payload.append(tuple(values))

    if unusable:
        raise RuntimeError(
            "This database cannot be migrated: the current schema requires "
            f"values the file does not contain -- {', '.join(sorted(set(unusable)))}"
            ". These are measurements, not schema conventions, so the "
            "migration will not invent them; a guessed branch length or fault "
            "scaling would produce a network that solves cleanly and is "
            "physically wrong. Your file has not been modified. Fill the "
            "missing values in with the release that wrote it, then migrate "
            "again."
        )

    conn.executemany(
        f'INSERT INTO "{table}" ({quoted}) VALUES ({placeholders})', payload
    )
    report.rows[table] = len(payload)


def _rebuild_from_legacy(
    source_path: str, target_path: str, report: MigrationReport
) -> None:
    """Read the legacy file and write a fresh file in the current schema."""
    from sqlalchemy import create_engine

    metadata = _current_metadata()
    engine = create_engine(f"sqlite:///{target_path}")
    try:
        metadata.create_all(engine)
    finally:
        engine.dispose()

    source = sqlite3.connect(source_path)
    target = sqlite3.connect(target_path)
    try:
        legacy_tables = _table_names(source)

        # --- global tables: straight copy ---------------------------------
        for table in _GLOBAL_TABLES:
            if table not in legacy_tables:
                report.rows[table] = 0
                continue
            columns = _column_names(source, table)
            records = [
                dict(zip(columns, row))
                for row in source.execute(f'SELECT * FROM "{table}" ORDER BY rowid')
            ]
            _insert(target, table, records, report)

        # --- networks -----------------------------------------------------
        network_columns = _column_names(source, "networks")
        network_records = [
            dict(zip(network_columns, row))
            for row in source.execute("SELECT * FROM networks ORDER BY rowid")
        ]
        report.networks = [record["name"] for record in network_records]
        _insert(target, "networks", network_records, report)

        # --- network-scoped element tables --------------------------------
        branches_per_network: Dict[Tuple[str, str], Dict[str, Any]] = {}
        members_per_table: Dict[str, Dict[str, List[str]]] = {}
        for table in ("buses", "branches", "faults", "sources", "paths"):
            if table not in legacy_tables:
                report.rows[table] = 0
                members_per_table[table] = {}
                continue

            rows = _read_rows(source, table)
            memberships = _read_memberships(source, table, report)
            members_per_table[table] = memberships

            referenced: Set[str] = set()
            records: List[Dict[str, Any]] = []
            for network_name in report.networks:
                for position, element_name in enumerate(
                    memberships.get(network_name, [])
                ):
                    referenced.add(element_name)
                    record = rows.get(element_name)
                    if record is None:
                        report.missing_rows.setdefault(table, []).append(element_name)
                        continue
                    new_record = dict(record)
                    new_record["network_name"] = network_name
                    new_record["position"] = position
                    records.append(new_record)
                    if table == "branches":
                        branches_per_network[(network_name, element_name)] = new_record

            orphans = sorted(set(rows) - referenced)
            if orphans:
                report.orphaned[table] = orphans
            _insert(target, table, records, report)

        # --- path segments -------------------------------------------------
        legacy_segments = _read_legacy_path_segments(source)
        segment_records: List[Dict[str, Any]] = []
        segments_per_network: Dict[Tuple[str, str], List[str]] = {}
        for network_name in report.networks:
            for path_name in members_per_table.get("paths", {}).get(network_name, []):
                branch_names = legacy_segments.get(path_name, [])
                segments_per_network[(network_name, path_name)] = branch_names
                for position, branch_name in enumerate(branch_names):
                    segment_records.append(
                        {
                            "network_name": network_name,
                            "path_name": path_name,
                            "position": position,
                            "branch_name": branch_name,
                        }
                    )
        _insert(target, "path_segments", segment_records, report)
        _verify_path_chains(segments_per_network, branches_per_network, report)

        target.commit()
    finally:
        source.close()
        target.close()

    # Only now that the file is closed and complete can it be opened through
    # the ORM the way a user will.
    _verify_loadable(target_path, report)


def _verify_loadable(target_path: str, report: MigrationReport) -> None:
    """
    Load every migrated network back, and record the ones that will not come.

    A structurally correct file is not necessarily a usable one. The database
    lets a column be ``NULL`` that the Pydantic model requires -- a bus with no
    ``specific_earth_resistance``, a bus whose ``type_name`` names a bus type
    the file never contained -- so a migration can report success and still
    hand back something that raises on the user's first ``load_network_from_db``.
    Doing the load here turns that into a named network and a concrete error at
    migration time.

    Failures are *reported*, not raised: they are per network, and one
    unloadable network is no reason to withhold the other five. Anything
    recorded here also sets :attr:`MigrationReport.needs_attention`.

    Parameters
    ----------
    target_path : str
        The converted file, before it is moved over the original.
    report : MigrationReport
        Report to fill in; :attr:`MigrationReport.networks` drives the loop.
    """
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    from .crud import load_network

    engine = create_engine(f"sqlite:///{target_path}")
    try:
        session = sessionmaker(bind=engine)()
        try:
            for name in report.networks:
                try:
                    load_network(name, session)
                except Exception as exc:  # noqa: BLE001 - reported, not handled
                    detail = " ".join(str(exc).split())
                    if len(detail) > 300:
                        detail = detail[:297] + "..."
                    report.unloadable[name] = f"{type(exc).__name__}: {detail}"
        finally:
            session.close()
    finally:
        engine.dispose()


def _add_missing_columns(sqlite_path: str, report: MigrationReport) -> None:
    """Add nullable columns introduced by a later release, in place."""
    from sqlalchemy.schema import CreateColumn

    metadata = _current_metadata()
    conn = sqlite3.connect(sqlite_path)
    try:
        tables = _table_names(conn)
        for table in metadata.sorted_tables:
            if table.name not in tables:
                continue
            present = set(_column_names(conn, table.name))
            for column in table.columns:
                if column.name in present:
                    continue
                if not column.nullable and column.default is None:
                    raise RuntimeError(
                        f"Cannot add the NOT NULL column "
                        f"{table.name}.{column.name} to an existing database "
                        "in place. Export the networks to JSON with the "
                        "release that wrote this file and re-import them into "
                        "a fresh database."
                    )
                ddl = str(CreateColumn(column).compile(dialect=_sqlite_dialect()))
                conn.execute(f'ALTER TABLE "{table.name}" ADD COLUMN {ddl}')
                report.added_columns.setdefault(table.name, []).append(column.name)
        conn.commit()
    finally:
        conn.close()


def _sqlite_dialect():
    from sqlalchemy.dialects import sqlite

    return sqlite.dialect()


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def migrate_database(sqlite_path: str, backup: bool = True) -> MigrationReport:
    """
    Bring an existing database file up to the current schema.

    Safe to call on a file that is already current: it then does nothing, takes
    no backup, and returns a report with ``migrated=False``.

    The legacy conversion is written to a temporary sibling file and moved over
    the original with :func:`os.replace`, so an interruption leaves either the
    old file or the new one — never a half-converted database.

    Parameters
    ----------
    sqlite_path : str
        Path to the SQLite file.
    backup : bool, optional
        Copy the file to ``<path>.bak`` before touching it. An existing
        backup is never overwritten; ``.bak.1``, ``.bak.2`` … are used
        instead. Defaults to ``True``.

    Returns
    -------
    MigrationReport
        What was done, and what could not be recovered. Check
        :attr:`MigrationReport.needs_attention`.

    Raises
    ------
    RuntimeError
        If a ``NOT NULL`` column without a default is missing from an
        otherwise current file, which cannot be added in place; or if a legacy
        row has no value for a column the current schema requires and cannot
        default (see :func:`_declared_defaults`). In both cases the file on
        disk is left exactly as it was.

    Examples
    --------
    >>> report = migrate_database("grounding.db")  # doctest: +SKIP
    >>> report.shared_elements  # doctest: +SKIP
    {'buses': ['A']}
    """
    report = MigrationReport(path=sqlite_path)
    kind = needs_migration(sqlite_path)
    report.kind = kind
    if kind == "none":
        logger.debug("'%s' already uses the current schema.", sqlite_path)
        return report

    if backup:
        report.backup_path = _backup(sqlite_path)
        logger.warning(
            "Migrating the groundinsight database '%s' (%s). A copy of the "
            "unmodified file was written to '%s'.",
            sqlite_path,
            kind,
            report.backup_path,
        )
    else:
        logger.warning(
            "Migrating the groundinsight database '%s' (%s) without a backup.",
            sqlite_path,
            kind,
        )

    if kind == "add_columns":
        _add_missing_columns(sqlite_path, report)
        report.migrated = True
        logger.warning(
            "Added columns introduced by a later release: %s", report.added_columns
        )
        return report

    target_path = f"{sqlite_path}.migrating"
    if os.path.exists(target_path):
        os.remove(target_path)
    try:
        _rebuild_from_legacy(sqlite_path, target_path, report)
        os.replace(target_path, sqlite_path)
    except Exception:
        if os.path.exists(target_path):
            os.remove(target_path)
        raise
    report.migrated = True

    _log_report(report)
    return report


def _log_report(report: MigrationReport) -> None:
    """Emit the parts of a report a user has to act on, at ``WARNING``."""
    logger.info(
        "Migrated '%s': %d network(s), rows %s",
        report.path,
        len(report.networks),
        report.rows,
    )
    if report.shared_elements:
        logger.warning(
            "The legacy schema stored one row per element *name*, so these "
            "elements were shared between networks and had to be duplicated "
            "into each of them: %s. If those networks meant different things "
            "by the same name, only the definition written last survived — "
            "check them against the backup '%s'.",
            report.shared_elements,
            report.backup_path,
        )
    if report.orphaned:
        logger.warning(
            "These elements belonged to no network and were dropped, because "
            "the current schema has nowhere to store them: %s",
            report.orphaned,
        )
    if report.missing_rows:
        logger.warning(
            "These elements were referenced by a network but had no row of "
            "their own and were skipped: %s",
            report.missing_rows,
        )
    if report.broken_paths:
        logger.warning(
            "The legacy schema did not store the order of path segments; it "
            "was reconstructed from insertion order. For these paths the "
            "result is not a connected chain and the path must be rebuilt "
            "with gi.create_paths(network=...): %s",
            report.broken_paths,
        )
    if report.defaulted_cells:
        logger.warning(
            "NULL values in columns that are now mandatory were replaced by "
            "defaults: %s",
            report.defaulted_cells,
        )
    if report.unloadable:
        logger.warning(
            "These networks were converted but cannot be loaded, because the "
            "stored data does not satisfy the current model -- usually a "
            "column the database allows to be NULL that the model requires. "
            "Every other network in the file is usable. Fill the missing "
            "values in the backup '%s' with the release that wrote it, then "
            "migrate again: %s",
            report.backup_path,
            report.unloadable,
        )

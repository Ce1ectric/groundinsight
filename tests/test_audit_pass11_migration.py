# tests/test_audit_pass11_migration.py

"""
Regression tests for the legacy-database migration (audit pass 11).

Pass 10 re-keyed ``buses``, ``branches``, ``faults``, ``sources`` and ``paths``
from the element name alone to ``(network_name, name)``. That fixed a real data
corruption -- two networks sharing an element name shared one physical row --
but it made every ``.db`` file written by v0.4.0 or earlier unreadable, because
``Base.metadata.create_all`` never adds a column to an existing table. This
module pins the conversion path that keeps such a file working.

Two fixtures produce a legacy database, on purpose:

``_write_head_schema_db``
    Writes the *literal* DDL of the released schema (``git show
    v0.4.0:src/groundinsight/models/database_models.py``) with raw SQL. It
    depends on no current code at all, so it still represents a real user file
    even if the migration and the current models drift together. It is the
    fixture that proves the migration can read something groundinsight can no
    longer produce.

``_downgrade_to_legacy``
    Mechanically strips ``network_name`` / ``position`` off a database the
    *current* code just wrote, keeping every other column. Used for the
    round-trip test, where the point is that a real, fully populated network
    survives the conversion unchanged -- including columns the released schema
    never had.

Using only the first would leave the round-trip untested; using only the second
would let a shared mistake in the down-converter and the migration cancel out.
"""

import logging
import os
import shutil
import sqlite3

import pytest

import groundinsight as gi
from groundinsight.database.migration import (
    migrate_database,
    needs_migration,
)


FREQUENCIES = [50.0]


# ---------------------------------------------------------------------------
# The released schema, verbatim
# ---------------------------------------------------------------------------

#: DDL of the pre-network-scoped schema, transcribed from the released
#: ``database_models.py``. Note what is *absent*: no ``network_name`` and no
#: ``position`` anywhere, no ``results`` on ``networks``, no ``R`` / ``L`` /
#: ``C`` decomposition, no thermal or short-circuit columns -- and, critically,
#: no ``position`` on ``path_segments``, which is why segment order has to be
#: recovered from ``rowid``.
_HEAD_DDL = [
    """CREATE TABLE complex_numbers (
           id INTEGER NOT NULL PRIMARY KEY,
           value JSON NOT NULL)""",
    """CREATE TABLE bus_types (
           name VARCHAR NOT NULL PRIMARY KEY,
           description TEXT,
           system_type VARCHAR NOT NULL,
           voltage_level FLOAT NOT NULL,
           impedance_formula TEXT NOT NULL)""",
    """CREATE TABLE branch_types (
           name VARCHAR NOT NULL PRIMARY KEY,
           description TEXT,
           grounding_conductor BOOLEAN NOT NULL,
           self_impedance_formula TEXT NOT NULL,
           mutual_impedance_formula TEXT NOT NULL)""",
    """CREATE TABLE buses (
           name VARCHAR NOT NULL PRIMARY KEY,
           description TEXT,
           type_name VARCHAR REFERENCES bus_types (name),
           specific_earth_resistance FLOAT,
           impedance JSON,
           active BOOLEAN NOT NULL)""",
    """CREATE TABLE branches (
           name VARCHAR NOT NULL PRIMARY KEY,
           description TEXT,
           type_name VARCHAR REFERENCES branch_types (name),
           length FLOAT NOT NULL,
           from_bus_name VARCHAR REFERENCES buses (name),
           to_bus_name VARCHAR REFERENCES buses (name),
           self_impedance JSON,
           mutual_impedance JSON,
           specific_earth_resistance FLOAT,
           parallel_coefficient FLOAT,
           active BOOLEAN NOT NULL)""",
    """CREATE TABLE faults (
           name VARCHAR NOT NULL PRIMARY KEY,
           description TEXT,
           bus_name VARCHAR REFERENCES buses (name),
           scalings JSON NOT NULL,
           active BOOLEAN)""",
    """CREATE TABLE sources (
           name VARCHAR NOT NULL PRIMARY KEY,
           description TEXT,
           bus_name VARCHAR REFERENCES buses (name),
           source_type VARCHAR NOT NULL,
           "values" JSON,
           voltage JSON,
           source_impedance JSON)""",
    """CREATE TABLE paths (
           name VARCHAR NOT NULL PRIMARY KEY,
           description TEXT,
           source_name VARCHAR REFERENCES sources (name),
           fault_name VARCHAR REFERENCES faults (name))""",
    """CREATE TABLE path_segments (
           path_name VARCHAR REFERENCES paths (name),
           branch_name VARCHAR REFERENCES branches (name))""",
    """CREATE TABLE networks (
           name VARCHAR NOT NULL PRIMARY KEY,
           description TEXT,
           frequencies BLOB,
           active_fault_name VARCHAR REFERENCES faults (name))""",
    """CREATE TABLE network_buses (
           network_name VARCHAR REFERENCES networks (name),
           bus_name VARCHAR REFERENCES buses (name))""",
    """CREATE TABLE network_branches (
           network_name VARCHAR REFERENCES networks (name),
           branch_name VARCHAR REFERENCES branches (name))""",
    """CREATE TABLE network_faults (
           network_name VARCHAR REFERENCES networks (name),
           fault_name VARCHAR REFERENCES faults (name))""",
    """CREATE TABLE network_sources (
           network_name VARCHAR REFERENCES networks (name),
           source_name VARCHAR REFERENCES sources (name))""",
    """CREATE TABLE network_paths (
           network_name VARCHAR REFERENCES networks (name),
           path_name VARCHAR REFERENCES paths (name))""",
]

_LEGACY_ASSOCIATIONS = {
    "buses": ("network_buses", "bus_name"),
    "branches": ("network_branches", "branch_name"),
    "faults": ("network_faults", "fault_name"),
    "sources": ("network_sources", "source_name"),
    "paths": ("network_paths", "path_name"),
}


# ---------------------------------------------------------------------------
# Fixtures and helpers
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _no_leftover_session():
    """Never let a session from another module leak into these tests."""
    gi.close_dbsession()
    yield
    gi.close_dbsession()


def _make_types():
    """Build the one bus type and one branch type used throughout."""
    bus_type = gi.BusType(
        name="BT",
        system_type="Substation",
        voltage_level=110.0,
        impedance_formula="rho*0 + 1",
    )
    branch_type = gi.BranchType(
        name="BRT",
        grounding_conductor=True,
        self_impedance_formula="(rho*0 + 0.25 + I*f*0.012)*l",
        mutual_impedance_formula="(rho*0 + 0.0 + I*f*0.010)*l",
    )
    return bus_type, branch_type


def _build_network(name, bus_names, branch_specs, solve=True):
    """
    Assemble and optionally solve a small network through the public API.

    Parameters
    ----------
    name : str
        Network name.
    bus_names : list of str
        Buses, in order. The source goes on the first, the fault on the last.
    branch_specs : list of tuple
        ``(branch_name, from_bus, to_bus)`` triples, in order.
    solve : bool, optional
        Run the fault before returning. Defaults to ``True``.

    Returns
    -------
    Network
        The assembled network.
    """
    bus_type, branch_type = _make_types()
    net = gi.create_network(name=name, frequencies=FREQUENCIES)
    for bus_name in bus_names:
        gi.create_bus(name=bus_name, type=bus_type, network=net)
    for branch_name, from_bus, to_bus in branch_specs:
        gi.create_branch(
            name=branch_name,
            type=branch_type,
            from_bus=from_bus,
            to_bus=to_bus,
            length=1.0,
            network=net,
        )
    gi.create_source(name="src", bus=bus_names[0], values={50.0: 60}, network=net)
    gi.create_fault(name="flt", bus=bus_names[-1], scalings={50.0: 1.0}, network=net)
    gi.create_paths(network=net)
    if solve:
        gi.run_fault(network=net, fault_name="flt")
    return net


def _downgrade_to_legacy(path):
    """
    Strip the network scoping off a database the current code just wrote.

    Rebuilds each element table without ``network_name`` / ``position``, keyed
    by ``name`` alone, and re-creates the association tables. Every other
    column is kept, so a round trip through this function exercises columns the
    released schema never had.

    Where two networks hold an element of the same name, only one row survives
    -- ``INSERT OR REPLACE`` in network order, exactly the data loss the legacy
    schema produced on every save.

    Parameters
    ----------
    path : str
        SQLite file to convert in place.
    """
    conn = sqlite3.connect(path)
    try:
        for table, (association, key) in _LEGACY_ASSOCIATIONS.items():
            columns = [row[1] for row in conn.execute(f'PRAGMA table_info("{table}")')]
            kept = [c for c in columns if c not in ("network_name", "position")]
            quoted = ", ".join(f'"{c}"' for c in kept)

            conn.execute(
                f'CREATE TABLE "{association}" '
                "(network_name VARCHAR, "
                f'"{key}" VARCHAR)'
            )
            conn.execute(
                f'INSERT INTO "{association}" (network_name, "{key}") '
                f'SELECT network_name, name FROM "{table}" '
                "ORDER BY network_name, position"
            )

            conn.execute(f'ALTER TABLE "{table}" RENAME TO "{table}_scoped"')
            column_ddl = ", ".join(
                f'"{c}" VARCHAR' if c != "name" else '"name" VARCHAR PRIMARY KEY'
                for c in kept
            )
            conn.execute(f'CREATE TABLE "{table}" ({column_ddl})')
            conn.execute(
                f'INSERT OR REPLACE INTO "{table}" ({quoted}) '
                f'SELECT {quoted} FROM "{table}_scoped" ORDER BY network_name'
            )
            conn.execute(f'DROP TABLE "{table}_scoped"')

        conn.execute("ALTER TABLE path_segments RENAME TO path_segments_scoped")
        conn.execute(
            "CREATE TABLE path_segments (path_name VARCHAR, branch_name VARCHAR)"
        )
        conn.execute(
            "INSERT INTO path_segments (path_name, branch_name) "
            "SELECT path_name, branch_name FROM path_segments_scoped "
            "ORDER BY network_name, path_name, position"
        )
        conn.execute("DROP TABLE path_segments_scoped")
        conn.commit()
    finally:
        conn.close()


def _write_head_schema_db(path, networks):
    """
    Write a database in the released DDL, with no help from current code.

    Parameters
    ----------
    path : str
        File to create.
    networks : dict
        ``network name -> {"buses": [...], "branches": [(name, from, to)],
        "path": [branch names]}``. Elements listed under more than one network
        are stored once, as the released schema did.
    """
    import pickle

    conn = sqlite3.connect(path)
    try:
        for statement in _HEAD_DDL:
            conn.execute(statement)
        conn.execute(
            "INSERT INTO bus_types (name, system_type, voltage_level, "
            "impedance_formula) VALUES ('BT', 'Substation', 110.0, 'rho*0 + 1')"
        )
        conn.execute(
            "INSERT INTO branch_types (name, grounding_conductor, "
            "self_impedance_formula, mutual_impedance_formula) VALUES "
            "('BRT', 1, '(rho*0 + 0.25 + I*f*0.012)*l', "
            "'(rho*0 + 0.0 + I*f*0.010)*l')"
        )
        for network_name, spec in networks.items():
            conn.execute(
                "INSERT INTO networks (name, frequencies, active_fault_name) "
                "VALUES (?, ?, NULL)",
                (network_name, pickle.dumps(FREQUENCIES)),
            )
            for bus_name in spec["buses"]:
                conn.execute(
                    "INSERT OR REPLACE INTO buses (name, type_name, "
                    "specific_earth_resistance, active) VALUES (?, 'BT', 100.0, 1)",
                    (bus_name,),
                )
                conn.execute(
                    "INSERT INTO network_buses VALUES (?, ?)",
                    (network_name, bus_name),
                )
            for branch_name, from_bus, to_bus in spec["branches"]:
                conn.execute(
                    "INSERT OR REPLACE INTO branches (name, type_name, length, "
                    "from_bus_name, to_bus_name, specific_earth_resistance, "
                    "parallel_coefficient, active) "
                    "VALUES (?, 'BRT', 1.0, ?, ?, 100.0, 1.0, 1)",
                    (branch_name, from_bus, to_bus),
                )
                conn.execute(
                    "INSERT INTO network_branches VALUES (?, ?)",
                    (network_name, branch_name),
                )
            source_name = spec.get("source", "src")
            fault_name = spec.get("fault", "flt")
            conn.execute(
                "INSERT OR REPLACE INTO sources (name, bus_name, source_type, "
                '"values") VALUES (?, ?, \'current\', ?)',
                (source_name, spec["buses"][0], '{"50.0": {"real": 60.0, "imag": 0.0}}'),
            )
            conn.execute(
                "INSERT INTO network_sources VALUES (?, ?)",
                (network_name, source_name),
            )
            conn.execute(
                "INSERT OR REPLACE INTO faults (name, bus_name, scalings, active) "
                "VALUES (?, ?, ?, 1)",
                (fault_name, spec["buses"][-1], '{"50.0": 1.0}'),
            )
            conn.execute(
                "INSERT INTO network_faults VALUES (?, ?)",
                (network_name, fault_name),
            )
            if spec.get("path"):
                path_name = spec.get("path_name", "path_1")
                conn.execute(
                    "INSERT OR REPLACE INTO paths (name, source_name, fault_name) "
                    "VALUES (?, ?, ?)",
                    (path_name, source_name, fault_name),
                )
                conn.execute(
                    "INSERT INTO network_paths VALUES (?, ?)",
                    (network_name, path_name),
                )
                # Insertion order *is* segment order in the released schema.
                for branch_name in spec["path"]:
                    conn.execute(
                        "INSERT INTO path_segments VALUES (?, ?)",
                        (path_name, branch_name),
                    )
        conn.commit()
    finally:
        conn.close()


def _columns(path, table):
    conn = sqlite3.connect(path)
    try:
        return [row[1] for row in conn.execute(f'PRAGMA table_info("{table}")')]
    finally:
        conn.close()


def _legacy_file(tmp_path, name="legacy.db"):
    """Write a two-network database and downgrade it to the legacy schema."""
    path = str(tmp_path / name)
    gi.start_dbsession(path)
    net = _build_network("netA", ["A", "B", "C"], [("L1", "A", "B"), ("L2", "B", "C")])
    gi.save_network_to_db(net)
    gi.close_dbsession()
    _downgrade_to_legacy(path)
    return path, net


# ---------------------------------------------------------------------------
# Detection
# ---------------------------------------------------------------------------


def test_needs_migration_classifies_a_legacy_file(tmp_path):
    """The released schema must be recognised, a current one left alone."""
    current = str(tmp_path / "current.db")
    gi.start_dbsession(current)
    gi.save_network_to_db(_build_network("net", ["A", "B"], [("L1", "A", "B")]))
    gi.close_dbsession()
    assert needs_migration(current) == "none"

    legacy = str(tmp_path / "legacy.db")
    shutil.copy2(current, legacy)
    _downgrade_to_legacy(legacy)
    assert needs_migration(legacy) == "legacy_scoped"

    assert needs_migration(str(tmp_path / "does-not-exist.db")) == "none"

    empty = tmp_path / "empty.db"
    empty.write_bytes(b"")
    assert needs_migration(str(empty)) == "none"


def test_migrating_a_current_file_changes_nothing(tmp_path):
    """A no-op must stay a no-op: no rewrite, and above all no backup file."""
    path = str(tmp_path / "current.db")
    gi.start_dbsession(path)
    gi.save_network_to_db(_build_network("net", ["A", "B"], [("L1", "A", "B")]))
    gi.close_dbsession()
    before = open(path, "rb").read()

    report = migrate_database(path)

    assert report.migrated is False
    assert report.kind == "none"
    assert report.backup_path is None
    assert open(path, "rb").read() == before
    assert not os.path.exists(path + ".bak")


# ---------------------------------------------------------------------------
# Round trip
# ---------------------------------------------------------------------------


def test_round_trip_through_the_legacy_schema_preserves_the_network(tmp_path):
    """A real network must survive down-conversion and migration unchanged.

    This is the test that would fail on any lossy step in the migration --
    dropped elements, scrambled path segments, mangled impedance JSON.
    """
    path, original = _legacy_file(tmp_path)
    assert needs_migration(path) == "legacy_scoped"

    report = migrate_database(path)
    assert report.migrated is True
    assert report.networks == ["netA"]
    assert report.needs_attention is False

    gi.start_dbsession(path)
    restored = gi.load_network_from_db("netA")

    assert list(restored.buses) == list(original.buses)
    assert list(restored.branches) == list(original.branches)
    assert list(restored.sources) == list(original.sources)
    assert list(restored.faults) == list(original.faults)
    assert list(restored.paths) == list(original.paths)
    for name, bus in original.buses.items():
        assert restored.buses[name].type.name == bus.type.name
        assert restored.buses[name].specific_earth_resistance == pytest.approx(
            bus.specific_earth_resistance
        )
    for name, branch in original.branches.items():
        assert restored.branches[name].from_bus == branch.from_bus
        assert restored.branches[name].to_bus == branch.to_bus
        assert restored.branches[name].length == pytest.approx(branch.length)
    for name, path_obj in original.paths.items():
        assert [s.name for s in restored.paths[name].segments] == [
            s.name for s in path_obj.segments
        ]


def test_a_database_written_by_the_released_ddl_loads_after_migration(tmp_path):
    """The migration must read a file the current code could never produce.

    Built from raw released DDL, so it stays a valid regression test even if
    the down-converter and the migration were both to drift.
    """
    path = str(tmp_path / "head.db")
    _write_head_schema_db(
        path,
        {
            "netA": {
                "buses": ["A", "B", "C"],
                "branches": [("L1", "A", "B"), ("L2", "B", "C")],
                "path": ["L1", "L2"],
            }
        },
    )
    assert "network_name" not in _columns(path, "buses")
    assert needs_migration(path) == "legacy_scoped"

    report = migrate_database(path)
    assert report.migrated is True
    assert report.needs_attention is False

    gi.start_dbsession(path)
    net = gi.load_network_from_db("netA")
    assert list(net.buses) == ["A", "B", "C"]
    assert list(net.branches) == ["L1", "L2"]
    assert [s.name for s in net.paths["path_1"].segments] == ["L1", "L2"]
    # Columns the released schema never had must exist and be empty, not absent.
    assert "results" in _columns(path, "networks")
    assert "t_k_s" in _columns(path, "faults")
    assert net.faults["flt"].t_k_s is None


# ---------------------------------------------------------------------------
# Backup
# ---------------------------------------------------------------------------


def test_the_unmodified_file_is_copied_before_anything_is_written(tmp_path):
    """The backup must be byte-identical to the pre-migration file."""
    path, _ = _legacy_file(tmp_path)
    before = open(path, "rb").read()

    report = migrate_database(path)

    assert report.backup_path == path + ".bak"
    assert open(report.backup_path, "rb").read() == before
    assert open(path, "rb").read() != before  # it really was rewritten


def test_a_second_migration_never_overwrites_an_existing_backup(tmp_path):
    """Losing the only copy of the original to a re-run is not acceptable."""
    path, _ = _legacy_file(tmp_path)
    first = open(path, "rb").read()
    migrate_database(path)

    # Downgrade the migrated file again and migrate a second time.
    _downgrade_to_legacy(path)
    report = migrate_database(path)

    assert report.backup_path == path + ".bak.1"
    assert open(path + ".bak", "rb").read() == first
    assert os.path.exists(path + ".bak.1")


def test_backup_false_skips_the_copy(tmp_path):
    path, _ = _legacy_file(tmp_path)
    report = migrate_database(path, backup=False)
    assert report.migrated is True
    assert report.backup_path is None
    assert not os.path.exists(path + ".bak")


def test_a_failing_rebuild_leaves_the_original_file_untouched(tmp_path, monkeypatch):
    """An interrupted migration must not produce a half-converted database."""
    path, _ = _legacy_file(tmp_path)
    before = open(path, "rb").read()

    from groundinsight.database import migration as migration_module

    def boom(*args, **kwargs):
        raise RuntimeError("disk full")

    monkeypatch.setattr(migration_module, "_read_legacy_path_segments", boom)

    with pytest.raises(RuntimeError, match="disk full"):
        migrate_database(path)

    assert open(path, "rb").read() == before
    assert not os.path.exists(path + ".migrating")
    assert needs_migration(path) == "legacy_scoped"


# ---------------------------------------------------------------------------
# What cannot be recovered
# ---------------------------------------------------------------------------


def test_an_element_shared_by_two_networks_is_duplicated_and_reported(tmp_path):
    """The one shared row must land in *both* networks, loudly.

    In the legacy schema two networks referencing bus ``A`` shared one row.
    Splitting per network can only copy the surviving definition into each, so
    the migration has to say so rather than let the user believe both networks
    came back as they were saved.
    """
    path = str(tmp_path / "shared.db")
    _write_head_schema_db(
        path,
        {
            "netA": {
                "buses": ["A", "B"],
                "branches": [("L1", "A", "B")],
                "path": ["L1"],
                "source": "srcA",
                "fault": "fltA",
                "path_name": "pA",
            },
            "netB": {
                "buses": ["A", "C"],
                "branches": [("L2", "A", "C")],
                "path": ["L2"],
                "source": "srcB",
                "fault": "fltB",
                "path_name": "pB",
            },
        },
    )

    report = migrate_database(path)

    assert report.shared_elements == {"buses:A": ["netA", "netB"]}
    assert report.needs_attention is True

    gi.start_dbsession(path)
    net_a = gi.load_network_from_db("netA")
    net_b = gi.load_network_from_db("netB")
    assert list(net_a.buses) == ["A", "B"]
    assert list(net_b.buses) == ["A", "C"]
    # Two independent rows now, not one shared object.
    assert net_a.buses["A"] is not net_b.buses["A"]


def test_the_shared_element_warning_names_the_networks(tmp_path, caplog):
    """A report nobody reads is worthless; the loss must reach the log."""
    path = str(tmp_path / "shared.db")
    _write_head_schema_db(
        path,
        {
            "netA": {"buses": ["A", "B"], "branches": [("L1", "A", "B")],
                     "path": ["L1"], "source": "srcA", "fault": "fltA",
                     "path_name": "pA"},
            "netB": {"buses": ["A", "C"], "branches": [("L2", "A", "C")],
                     "path": ["L2"], "source": "srcB", "fault": "fltB",
                     "path_name": "pB"},
        },
    )
    with caplog.at_level("WARNING"):
        migrate_database(path)

    warnings = "\n".join(
        r.getMessage() for r in caplog.records if r.levelname == "WARNING"
    )
    assert "buses:A" in warnings
    assert "netA" in warnings and "netB" in warnings


def test_elements_belonging_to_no_network_are_dropped_and_reported(tmp_path):
    """An orphan has nowhere to go once the key carries the network."""
    path = str(tmp_path / "orphan.db")
    _write_head_schema_db(
        path,
        {"netA": {"buses": ["A", "B"], "branches": [("L1", "A", "B")], "path": ["L1"]}},
    )
    conn = sqlite3.connect(path)
    conn.execute(
        "INSERT INTO buses (name, type_name, specific_earth_resistance, active) "
        "VALUES ('ORPHAN', 'BT', 100.0, 1)"
    )
    conn.commit()
    conn.close()

    report = migrate_database(path)

    assert report.orphaned == {"buses": ["ORPHAN"]}
    assert report.needs_attention is True
    gi.start_dbsession(path)
    assert "ORPHAN" not in gi.load_network_from_db("netA").buses


def test_a_membership_without_an_element_row_is_reported_not_crashed(tmp_path):
    """A dangling association row used to be a hard failure on load."""
    path = str(tmp_path / "dangling.db")
    _write_head_schema_db(
        path,
        {"netA": {"buses": ["A", "B"], "branches": [("L1", "A", "B")], "path": ["L1"]}},
    )
    conn = sqlite3.connect(path)
    conn.execute("INSERT INTO network_buses VALUES ('netA', 'GHOST')")
    conn.commit()
    conn.close()

    report = migrate_database(path)

    assert report.missing_rows == {"buses": ["GHOST"]}
    assert report.needs_attention is True


# ---------------------------------------------------------------------------
# Path segment order
# ---------------------------------------------------------------------------


def test_path_segment_order_is_recovered_from_insertion_order(tmp_path):
    """The released schema stored segments unordered; order comes from rowid.

    A path is a walk, so getting this wrong silently reverses or shuffles the
    route a fault current takes -- and nothing downstream would raise.
    """
    path = str(tmp_path / "order.db")
    _write_head_schema_db(
        path,
        {
            "netA": {
                "buses": ["A", "B", "C", "D"],
                "branches": [("L1", "A", "B"), ("L2", "B", "C"), ("L3", "C", "D")],
                "path": ["L1", "L2", "L3"],
            }
        },
    )
    assert "position" not in _columns(path, "path_segments")

    report = migrate_database(path)
    assert report.broken_paths == []

    conn = sqlite3.connect(path)
    rows = list(
        conn.execute(
            "SELECT position, branch_name FROM path_segments "
            "WHERE network_name = 'netA' AND path_name = 'path_1' "
            "ORDER BY position"
        )
    )
    conn.close()
    assert rows == [(0, "L1"), (1, "L2"), (2, "L3")]

    gi.start_dbsession(path)
    net = gi.load_network_from_db("netA")
    assert [s.name for s in net.paths["path_1"].segments] == ["L1", "L2", "L3"]


def test_segments_that_do_not_form_a_chain_are_reported(tmp_path):
    """Insertion order is an assumption, so it has to be checked.

    Written by hand in a deliberately impossible order: consecutive branches
    share no bus, which cannot happen for a real path. Without the chain check
    the migration would accept it and the user would never learn.
    """
    path = str(tmp_path / "broken.db")
    _write_head_schema_db(
        path,
        {
            "netA": {
                "buses": ["A", "B", "C", "D"],
                "branches": [("L1", "A", "B"), ("L2", "B", "C"), ("L3", "C", "D")],
                "path": ["L1"],
            }
        },
    )
    conn = sqlite3.connect(path)
    conn.execute("DELETE FROM path_segments")
    # L1 (A-B) then L3 (C-D): no shared bus, so this is not a walk.
    conn.execute("INSERT INTO path_segments VALUES ('path_1', 'L1')")
    conn.execute("INSERT INTO path_segments VALUES ('path_1', 'L3')")
    conn.commit()
    conn.close()

    report = migrate_database(path)

    assert report.broken_paths == ["netA:path_1"]
    assert report.needs_attention is True


# ---------------------------------------------------------------------------
# Columns added by a later release
# ---------------------------------------------------------------------------


def test_a_missing_nullable_column_is_added_in_place(tmp_path):
    """``create_all`` never adds a column, so a dev-era file needs this path."""
    path = str(tmp_path / "missing_column.db")
    gi.start_dbsession(path)
    gi.save_network_to_db(_build_network("net", ["A", "B"], [("L1", "A", "B")]))
    gi.close_dbsession()

    conn = sqlite3.connect(path)
    conn.execute("ALTER TABLE networks DROP COLUMN results")
    conn.execute("ALTER TABLE faults DROP COLUMN t_k_s")
    conn.commit()
    conn.close()
    assert needs_migration(path) == "add_columns"

    report = migrate_database(path)

    assert report.kind == "add_columns"
    assert report.migrated is True
    assert sorted(report.added_columns["networks"]) == ["results"]
    assert sorted(report.added_columns["faults"]) == ["t_k_s"]
    assert needs_migration(path) == "none"

    gi.start_dbsession(path)
    net = gi.load_network_from_db("net")
    assert list(net.buses) == ["A", "B"]


# ---------------------------------------------------------------------------
# Wiring into start_dbsession
# ---------------------------------------------------------------------------


def test_start_dbsession_migrates_a_legacy_file_automatically(tmp_path):
    """The whole point: an existing user's script keeps running."""
    path, original = _legacy_file(tmp_path)

    gi.start_dbsession(path)  # no migrate_database call by the user
    restored = gi.load_network_from_db("netA")

    assert list(restored.buses) == list(original.buses)
    assert os.path.exists(path + ".bak")


def test_start_dbsession_with_migrate_false_gives_an_actionable_error(tmp_path):
    """Opting out must fail early and say exactly what to run."""
    path, _ = _legacy_file(tmp_path)
    before = open(path, "rb").read()

    gi.start_dbsession(path, migrate=False)
    with pytest.raises(RuntimeError, match="migrate_database"):
        gi.load_network_from_db("netA")

    assert open(path, "rb").read() == before
    assert not os.path.exists(path + ".bak")


# ---------------------------------------------------------------------------
# Order is stored, not merely stumbled into
# ---------------------------------------------------------------------------


def test_position_is_written_and_not_left_to_insertion_order(tmp_path):
    """``position`` must hold the real index, not a constant that happens to sort.

    The relationships in ``database_models`` order by ``position``. If every
    row carried the same value, SQLite would fall back to rowid order -- which
    for a freshly written file *is* the right order, so a load-and-compare test
    passes while the column is meaningless. Reading the column back is the only
    way to tell the two apart.
    """
    path, original = _legacy_file(tmp_path)
    migrate_database(path)

    conn = sqlite3.connect(path)
    try:
        for table, expected in (
            ("buses", list(original.buses)),
            ("branches", list(original.branches)),
        ):
            stored = conn.execute(
                f'SELECT name, position FROM "{table}" '
                "WHERE network_name = 'netA' ORDER BY position"
            ).fetchall()
            assert [name for name, _ in stored] == expected
            assert [pos for _, pos in stored] == list(range(len(expected)))

        segments = conn.execute(
            "SELECT branch_name, position FROM path_segments "
            "WHERE network_name = 'netA' AND path_name = 'path_1' "
            "ORDER BY position"
        ).fetchall()
        assert [pos for _, pos in segments] == list(range(len(segments)))
    finally:
        conn.close()


def test_membership_order_wins_over_element_table_order(tmp_path):
    """Order comes from the association table, not from the element rows.

    Both orders coincide in a file the package wrote, so they have to be pulled
    apart deliberately: the buses are stored C, A, B but the network lists them
    A, B, C.
    """
    path, _ = _legacy_file(tmp_path)
    conn = sqlite3.connect(path)
    try:
        # Rewrite the element table in a different physical order ...
        rows = conn.execute("SELECT * FROM buses").fetchall()
        columns = [row[1] for row in conn.execute('PRAGMA table_info("buses")')]
        by_name = {dict(zip(columns, row))["name"]: row for row in rows}
        conn.execute("DELETE FROM buses")
        placeholders = ", ".join("?" for _ in columns)
        for bus_name in ("C", "A", "B"):
            conn.execute(f"INSERT INTO buses VALUES ({placeholders})", by_name[bus_name])
        # ... while the membership keeps saying A, B, C.
        assert [
            row[0]
            for row in conn.execute(
                "SELECT bus_name FROM network_buses ORDER BY rowid"
            )
        ] == ["A", "B", "C"]
        conn.commit()
    finally:
        conn.close()

    migrate_database(path)

    gi.start_dbsession(path)
    restored = gi.load_network_from_db("netA")
    assert list(restored.buses) == ["A", "B", "C"]


# ---------------------------------------------------------------------------
# Mandatory columns with no value in the legacy row
# ---------------------------------------------------------------------------


def _null_out(path, table, column, name):
    """Set one cell of a legacy element row to NULL."""
    conn = sqlite3.connect(path)
    try:
        conn.execute(f'UPDATE "{table}" SET "{column}" = NULL WHERE name = ?', (name,))
        conn.commit()
    finally:
        conn.close()


def test_a_null_in_a_column_with_a_schema_default_is_filled_and_counted(tmp_path):
    """``active`` and ``source_type`` declare defaults, so they may be filled.

    Writing the schema's own default reproduces what the current code would
    have written for a row that never mentioned the column, so it is safe --
    but it is still a value the file did not contain, so it is reported.
    """
    path, _ = _legacy_file(tmp_path)
    _null_out(path, "buses", "active", "B")
    _null_out(path, "sources", "source_type", "src")

    report = migrate_database(path)

    assert report.migrated is True
    assert report.defaulted_cells == {"buses.active": 1, "sources.source_type": 1}
    # A filled-in value is a value the user did not supply -- say so.
    assert report.needs_attention is True

    gi.start_dbsession(path)
    restored = gi.load_network_from_db("netA")
    assert restored.buses["B"].active is True
    assert list(restored.buses) == ["A", "B", "C"]


def test_a_missing_branch_length_aborts_instead_of_being_guessed(tmp_path):
    """A length is a measurement. Inventing one is worse than refusing.

    ``length`` is ``NOT NULL`` in the current schema and declares no default.
    Substituting ``0.0`` would produce a network that solves without complaint
    and reports a completely different earth potential rise, with nothing in
    the output to show a number was made up.
    """
    path, _ = _legacy_file(tmp_path)
    _null_out(path, "branches", "length", "L2")
    before = open(path, "rb").read()

    with pytest.raises(RuntimeError, match=r"branches\.length of 'L2'"):
        migrate_database(path)

    # Refusing is only acceptable because it is recoverable.
    assert open(path, "rb").read() == before
    assert not os.path.exists(path + ".migrating")
    assert open(path + ".bak", "rb").read() == before


def test_a_missing_fault_scaling_aborts_instead_of_being_guessed(tmp_path):
    """Same rule for ``scalings``: an empty mapping is not a neutral default."""
    path, _ = _legacy_file(tmp_path)
    _null_out(path, "faults", "scalings", "flt")

    with pytest.raises(RuntimeError, match=r"faults\.scalings of 'flt'"):
        migrate_database(path)


def test_a_mandatory_column_absent_from_every_row_is_named(tmp_path):
    """A column dropped wholesale must not surface as a bare IntegrityError."""
    path, _ = _legacy_file(tmp_path)
    conn = sqlite3.connect(path)
    try:
        conn.execute("ALTER TABLE branches DROP COLUMN length")
        conn.commit()
    finally:
        conn.close()

    with pytest.raises(RuntimeError, match="missing for every row"):
        migrate_database(path)


# ---------------------------------------------------------------------------
# A converted file that cannot be loaded is not a successful migration
# ---------------------------------------------------------------------------


def test_a_network_that_cannot_be_loaded_is_named_in_the_report(tmp_path):
    """Structurally valid is not the same as usable.

    ``buses.specific_earth_resistance`` is nullable in the database and
    mandatory in the Pydantic model, so a legacy row without it converts
    cleanly and then raises on the user's first load. The migration has to
    find that itself rather than let the user discover it.
    """
    path, _ = _legacy_file(tmp_path)
    _null_out(path, "buses", "specific_earth_resistance", "B")

    report = migrate_database(path)

    assert report.migrated is True
    assert list(report.unloadable) == ["netA"]
    assert "specific_earth_resistance" in report.unloadable["netA"]
    assert report.needs_attention is True

    # The diagnosis has to be reproducible: the file really is unloadable.
    gi.start_dbsession(path)
    with pytest.raises(Exception):
        gi.load_network_from_db("netA")


def test_a_dangling_bus_type_reference_is_caught_at_migration_time(tmp_path):
    """A bus type the file never contained must not surface as an AttributeError."""
    path, _ = _legacy_file(tmp_path)
    conn = sqlite3.connect(path)
    try:
        conn.execute("DELETE FROM bus_types")
        conn.commit()
    finally:
        conn.close()

    report = migrate_database(path)

    assert list(report.unloadable) == ["netA"]
    assert report.needs_attention is True


def test_the_unloadable_warning_names_the_network_and_the_backup(tmp_path, caplog):
    """The warning must carry both halves of the recovery instruction."""
    path, _ = _legacy_file(tmp_path)
    _null_out(path, "buses", "specific_earth_resistance", "B")

    with caplog.at_level(logging.WARNING, logger="groundinsight.database.migration"):
        report = migrate_database(path)

    warnings = "\n".join(
        record.getMessage() for record in caplog.records if record.levelno >= logging.WARNING
    )
    assert "netA" in warnings
    assert report.backup_path in warnings


def test_a_loadable_network_is_never_flagged(tmp_path):
    """The check must not fire on a healthy file, or it is worthless."""
    path, _ = _legacy_file(tmp_path)

    report = migrate_database(path)

    assert report.unloadable == {}
    assert report.needs_attention is False

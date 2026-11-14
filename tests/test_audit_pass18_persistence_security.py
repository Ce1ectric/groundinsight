# tests/test_audit_pass18_persistence_security.py

"""
Regression tests for audit pass 18, findings M1, M2 and M3.

M1 -- ``NetworkDB.frequencies`` was a SQLAlchemy ``PickleType``. Reading a
database file therefore *executed* whatever the blob contained, before any
Pydantic validator ever saw the value: a ``.db`` handed over by someone else
was arbitrary code execution. The column is JSON now and the legacy blobs are
converted by a restrictive unpickler that only resolves ``builtins.list`` /
``float`` / ``int``.

M2 -- the in-place ``"add_columns"`` migration rendered its DDL with
SQLAlchemy's ``CreateColumn``, which emits no ``DEFAULT`` clause for a
Python-side ``default=``. SQLite refuses ``ADD COLUMN ... NOT NULL`` without
one, so a database missing ``buses.position`` / ``buses.active`` could not be
opened through the public API at all.

M3 -- ``save_network`` merged bus and branch types into the *global* catalogue
without asking, so saving network B silently rewrote the physics of network A.
"""

import json
import os
import pickle
import sqlite3

import pytest

import groundinsight as gi
from groundinsight.models.core_models import BusType
from groundinsight.database.migration import migrate_database, needs_migration


FREQUENCIES = [50.0, 250.0]


@pytest.fixture()
def db_path(tmp_path):
    """A closed-session database path, cleaned up afterwards."""
    path = str(tmp_path / "pass18.db")
    gi.close_dbsession()
    yield path
    gi.close_dbsession()


def _make_network(name="Net"):
    net = gi.create_network(name=name, frequencies=FREQUENCIES)
    gi.create_bus(
        name="b1",
        type=BusType(
            name="BT",
            system_type="Substation",
            voltage_level=110.0,
            impedance_formula="rho*0 + 1",
        ),
        network=net,
    )
    return net


# ---------------------------------------------------------------------------
# M1 -- pickled frequency column
# ---------------------------------------------------------------------------


def test_frequencies_column_is_not_a_pickle_column():
    """The stored value is JSON text, not a pickle blob."""
    from groundinsight.models.database_models import NetworkDB

    column = NetworkDB.__table__.columns["frequencies"]
    assert column.type.__class__.__name__ == "JSON"


def test_legacy_pickled_frequencies_are_migrated_and_load(db_path):
    """A file written with the pickle column migrates and still loads."""
    gi.start_dbsession(db_path)
    gi.save_network_to_db(_make_network())
    gi.close_dbsession()

    # Put the file back into the shape the pickle column produced.
    conn = sqlite3.connect(db_path)
    try:
        conn.execute(
            "UPDATE networks SET frequencies = ?", (pickle.dumps(FREQUENCIES),)
        )
        conn.commit()
    finally:
        conn.close()

    assert needs_migration(db_path) == "pickle_frequencies"
    report = migrate_database(db_path, backup=False)
    assert report.migrated is True
    assert report.converted_frequencies == ["Net"]

    conn = sqlite3.connect(db_path)
    try:
        stored = conn.execute("SELECT frequencies FROM networks").fetchone()[0]
    finally:
        conn.close()
    assert json.loads(stored) == FREQUENCIES

    gi.start_dbsession(db_path)
    assert gi.load_network_from_db("Net").frequencies == FREQUENCIES


def test_hostile_pickle_is_refused_without_running(db_path, tmp_path):
    """A ``__reduce__`` payload must not execute; the marker must not appear.

    The payload is deliberately harmless -- ``os.system('touch <marker>')``
    inside ``tmp_path``. Its only job is to be *observable*: if the marker file
    exists afterwards, the blob ran.
    """
    marker = tmp_path / "pickle_ran.marker"

    class _Payload:
        def __reduce__(self):
            return (os.system, (f"touch {marker}",))

    gi.start_dbsession(db_path)
    gi.save_network_to_db(_make_network())
    gi.close_dbsession()

    conn = sqlite3.connect(db_path)
    try:
        conn.execute(
            "UPDATE networks SET frequencies = ?", (pickle.dumps(_Payload()),)
        )
        conn.commit()
    finally:
        conn.close()

    assert needs_migration(db_path) == "pickle_frequencies"
    with pytest.raises(ValueError, match="Refusing to unpickle"):
        migrate_database(db_path, backup=False)
    assert not marker.exists()


def test_hostile_pickle_is_refused_by_start_dbsession(db_path, tmp_path):
    """The automatic migration in ``start_dbsession`` refuses it as well."""
    marker = tmp_path / "session_pickle_ran.marker"

    class _Payload:
        def __reduce__(self):
            return (os.system, (f"touch {marker}",))

    gi.start_dbsession(db_path)
    gi.save_network_to_db(_make_network())
    gi.close_dbsession()

    conn = sqlite3.connect(db_path)
    try:
        conn.execute(
            "UPDATE networks SET frequencies = ?", (pickle.dumps(_Payload()),)
        )
        conn.commit()
    finally:
        conn.close()

    with pytest.raises(ValueError, match="Refusing to unpickle"):
        gi.start_dbsession(db_path)
    assert not marker.exists()


def test_decoder_rejects_non_numeric_payloads():
    """A list that survives the class filter but is not a frequency list."""
    from groundinsight.database.migration import _decode_legacy_frequencies

    with pytest.raises(ValueError, match="not a frequency"):
        _decode_legacy_frequencies(pickle.dumps([50.0, "250"]))
    with pytest.raises(ValueError, match="did not contain a list"):
        _decode_legacy_frequencies(pickle.dumps({"f": 50.0}))


# ---------------------------------------------------------------------------
# M2 -- add_columns migration with declared defaults
# ---------------------------------------------------------------------------


def test_add_columns_migration_handles_not_null_defaults(db_path):
    """A file without ``buses.position`` / ``buses.active`` must reopen.

    Both columns are ``NOT NULL`` with a Python-side default, which is exactly
    the shape the old ``CreateColumn`` rendering could not add.
    """
    gi.start_dbsession(db_path)
    gi.save_network_to_db(_make_network())
    gi.close_dbsession()

    conn = sqlite3.connect(db_path)
    try:
        conn.execute("ALTER TABLE buses DROP COLUMN position")
        conn.execute("ALTER TABLE buses DROP COLUMN active")
        conn.commit()
    finally:
        conn.close()

    assert needs_migration(db_path) == "add_columns"
    report = migrate_database(db_path, backup=False)
    assert report.migrated is True
    assert sorted(report.added_columns["buses"]) == ["active", "position"]

    gi.start_dbsession(db_path)
    loaded = gi.load_network_from_db("Net")
    assert loaded.buses["b1"].active is True


def test_start_dbsession_opens_a_file_missing_the_new_columns(db_path):
    """The public entry point must survive it too -- that is the user path."""
    gi.start_dbsession(db_path)
    gi.save_network_to_db(_make_network())
    gi.close_dbsession()

    conn = sqlite3.connect(db_path)
    try:
        conn.execute("ALTER TABLE buses DROP COLUMN position")
        conn.commit()
    finally:
        conn.close()

    gi.start_dbsession(db_path)
    assert "b1" in gi.load_network_from_db("Net").buses


# ---------------------------------------------------------------------------
# M3 -- shared type catalogue
# ---------------------------------------------------------------------------


def _network_with_bustype(name, bus_name, formula):
    net = gi.create_network(name=name, frequencies=FREQUENCIES)
    gi.create_bus(
        name=bus_name,
        type=BusType(
            name="Shared",
            system_type="Substation",
            voltage_level=110.0,
            impedance_formula=formula,
        ),
        network=net,
    )
    return net


def test_save_network_refuses_to_rewrite_a_shared_type(db_path):
    """Network B may not silently redefine a type network A relies on."""
    gi.start_dbsession(db_path)
    gi.save_network_to_db(_network_with_bustype("NetA", "a1", "rho*0 + 1"))

    net_b = _network_with_bustype("NetB", "b1", "rho*0 + 999")
    with pytest.raises(ValueError) as excinfo:
        gi.save_network_to_db(net_b)
    message = str(excinfo.value)
    assert "'NetA'" in message
    assert "impedance_formula" in message
    assert "overwrite_types" in message

    # Nothing was written: NetA still computes with its own formula.
    loaded_a = gi.load_network_from_db("NetA")
    assert loaded_a.buses["a1"].type.impedance_formula == "rho*0 + 1"
    loaded_a.buses["a1"].calculate_impedance(FREQUENCIES)
    assert loaded_a.buses["a1"].impedance[50.0].real == pytest.approx(1.0)


def test_overwrite_types_allows_the_rewrite(db_path, caplog):
    """With consent the global catalogue behaves as documented."""
    gi.start_dbsession(db_path)
    gi.save_network_to_db(_network_with_bustype("NetA", "a1", "rho*0 + 1"))

    net_b = _network_with_bustype("NetB", "b1", "rho*0 + 999")
    with caplog.at_level("WARNING"):
        gi.save_network_to_db(net_b, overwrite_types=True)
    assert "NetA" in caplog.text

    assert (
        gi.load_bustypes_from_db()["Shared"].impedance_formula == "rho*0 + 999"
    )


def test_editing_a_type_only_this_network_uses_stays_silent(db_path):
    """The ordinary edit-and-resave workflow must not need a flag."""
    gi.start_dbsession(db_path)
    net = _network_with_bustype("NetA", "a1", "rho*0 + 1")
    gi.save_network_to_db(net)

    net.buses["a1"].type = BusType(
        name="Shared",
        system_type="Substation",
        voltage_level=110.0,
        impedance_formula="rho*0 + 7",
    )
    gi.save_network_to_db(net, overwrite=True)
    assert (
        gi.load_network_from_db("NetA").buses["a1"].type.impedance_formula
        == "rho*0 + 7"
    )

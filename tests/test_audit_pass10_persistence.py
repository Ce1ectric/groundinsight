# tests/test_audit_pass10_persistence.py

"""
Regression tests for the tenth audit pass of the SQLAlchemy persistence layer.

The pass confirmed five defects in ``groundinsight.database.crud`` and
``groundinsight.models.database_models``:

1. ``overwrite=True`` committed the DELETE of the stored network before the
   replacement rows were written, so a failing re-save destroyed the original.
   The underlying failure -- a path whose segments reference a branch the user
   removed from ``network.branches`` -- surfaced as
   ``AttributeError: 'NoneType' object has no attribute '_sa_instance_state'``.
2. ``save_network`` only *inserted* missing BusType / BranchType rows and never
   updated an existing one, so editing a type's physics and re-saving silently
   reverted to the stored definition on load.
3. Buses, branches, faults, sources and paths were keyed globally by name, so
   saving network B rewrote network A's identically named elements. Because
   ``create_paths`` names every path ``path_1``, ``path_2``, ..., path-name
   collisions between any two saved networks were the default.
4. Overwriting a shrunken network orphaned the dropped child rows.
5. ``Path.segments`` came back in an unspecified order, and the insertion order
   of ``Network.buses`` / ``Network.branches`` was scrambled by the round-trip.

Every test below fails on the pre-fix code.
"""

import os
import pickle
import sqlite3

import pytest
from sqlalchemy import event
from sqlalchemy.engine import Engine

import groundinsight as gi
from groundinsight.models.core_models import (
    Branch,
    BranchType,
    Bus,
    BusType,
    ComplexNumber,
    Fault,
    Network,
    Path,
    Source,
)

FREQUENCIES = [50.0]


# ---------------------------------------------------------------------------
# Fixtures and helpers
# ---------------------------------------------------------------------------


@pytest.fixture
def db_path(tmp_path):
    """
    Provide a private SQLite file and an active session for one test.

    Closes any session left open by a previous test module before binding, and
    closes ours again afterwards, so the module-level session globals of
    ``groundinsight`` are never carried between tests.

    Parameters
    ----------
    tmp_path : pathlib.Path
        pytest's per-test temporary directory.

    Yields
    ------
    str
        Filesystem path of the SQLite database backing the session.
    """
    gi.close_dbsession()
    path = str(tmp_path / "audit10.db")
    gi.start_dbsession(path)
    try:
        yield path
    finally:
        gi.close_dbsession()


@pytest.fixture
def emitted_sql():
    """
    Record every SQL statement any engine executes during the test.

    Yields
    ------
    list of str
        The statements, in execution order. The list fills up as the test runs.
    """
    statements = []

    def record(connection, cursor, statement, parameters, context, executemany):
        statements.append(statement)

    event.listen(Engine, "before_cursor_execute", record)
    try:
        yield statements
    finally:
        event.remove(Engine, "before_cursor_execute", record)


def make_bus_type(name="BT", voltage_level=110.0, impedance_formula="rho*0 + 1"):
    """Build a minimal :class:`BusType`.

    Parameters
    ----------
    name : str, optional
        Type name. Defaults to ``"BT"``.
    voltage_level : float, optional
        Nominal voltage in kV. Defaults to ``110.0``.
    impedance_formula : str, optional
        Grounding-impedance formula. Defaults to ``"rho*0 + 1"``.

    Returns
    -------
    BusType
        The constructed bus type.
    """
    return BusType(
        name=name,
        system_type="Substation",
        voltage_level=voltage_level,
        impedance_formula=impedance_formula,
    )


def make_branch_type(name="BRT", self_formula="(rho*0 + 0.25 + I*f*0.012)*l"):
    """Build a minimal :class:`BranchType`.

    Parameters
    ----------
    name : str, optional
        Type name. Defaults to ``"BRT"``.
    self_formula : str, optional
        Self-impedance formula. Defaults to a 0.25 Ohm/km overhead earth wire.

    Returns
    -------
    BranchType
        The constructed branch type.
    """
    return BranchType(
        name=name,
        grounding_conductor=True,
        self_impedance_formula=self_formula,
        mutual_impedance_formula="(rho*0 + 0.0 + I*f*0.010)*l",
    )


def build_network(name, bus_names, branch_specs, solve=True, bus_type=None,
                  branch_type=None):
    """
    Assemble a small solved network through the public API.

    Parameters
    ----------
    name : str
        Network name.
    bus_names : list of str
        Buses to create, in order. The source is attached to the first bus and
        the fault to the last.
    branch_specs : list of tuple
        ``(branch_name, from_bus, to_bus)`` triples, in order.
    solve : bool, optional
        Whether to run the fault before returning. Defaults to ``True``.
    bus_type : BusType, optional
        Bus type to use. Defaults to :func:`make_bus_type`.
    branch_type : BranchType, optional
        Branch type to use. Defaults to :func:`make_branch_type`.

    Returns
    -------
    Network
        The assembled network.
    """
    bus_type = bus_type if bus_type is not None else make_bus_type()
    branch_type = branch_type if branch_type is not None else make_branch_type()

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


def table_counts(path, tables):
    """Return ``{table: row count}`` for the given SQLite file.

    Parameters
    ----------
    path : str
        Filesystem path of the SQLite database.
    tables : list of str
        Table names to count.

    Returns
    -------
    dict of str to int
        Row count per table.
    """
    connection = sqlite3.connect(path)
    try:
        return {
            table: connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            for table in tables
        }
    finally:
        connection.close()


def results_by_name(result):
    """
    Re-key a ``Result.model_dump()`` by element name.

    The stored ``buses`` / ``branches`` lists are compared element-wise by
    default, which turns a pure reordering into a spurious numeric diff. Keying
    by name compares the physics rather than the list order.

    Parameters
    ----------
    result : dict
        A ``Result.model_dump()`` mapping.

    Returns
    -------
    dict
        The same data with ``buses`` and ``branches`` keyed by name.
    """
    return {
        "buses": {bus["name"]: bus for bus in result["buses"]},
        "branches": {branch["name"]: branch for branch in result["branches"]},
        "reduction_factor": result["reduction_factor"],
        "grounding_impedance": result["grounding_impedance"],
        "fault": result["fault"],
    }


# ---------------------------------------------------------------------------
# Bug 1 -- overwrite must be atomic, and stale path segments must be diagnosed
# ---------------------------------------------------------------------------


def test_failed_overwrite_keeps_the_stored_network_loadable(db_path):
    """A re-save that raises must leave the previously stored network intact."""
    good = build_network("Prod", ["b1", "b2"], [("br1", "b1", "b2")])
    gi.save_network_to_db(good)

    bad = build_network("Prod", ["b1", "b2"], [("br1", "b1", "b2")])
    ghost = Branch(
        name="ghost_branch",
        type=make_branch_type(),
        length=1.0,
        from_bus="b1",
        to_bus="b2",
        self_impedance={50.0: ComplexNumber(real=1, imag=0)},
        mutual_impedance={50.0: ComplexNumber(real=1, imag=0)},
    )
    list(bad.paths.values())[0].segments.append(ghost)

    with pytest.raises(ValueError):
        gi.save_network_to_db(bad, overwrite=True)

    # The original revision must still be there, complete and unchanged.
    restored = gi.load_network_from_db("Prod")
    assert sorted(restored.buses) == ["b1", "b2"]
    assert sorted(restored.branches) == ["br1"]
    assert sorted(restored.paths) == sorted(good.paths)
    assert restored.buses == good.buses
    assert restored.branches == good.branches


def test_failed_overwrite_rolls_back_even_without_the_upfront_validation(
    db_path, monkeypatch
):
    """
    The DELETE must roll back for *any* failure, not only the validated one.

    The upfront path validation catches the known trigger; this test injects an
    unrelated exception after the delete has already been flushed, which is the
    situation the old ``session.commit()`` right after the delete made
    unrecoverable.
    """
    from groundinsight.database import crud

    net = build_network("Prod", ["b1", "b2"], [("br1", "b1", "b2")])
    gi.save_network_to_db(net)

    boom = RuntimeError("injected write failure")

    def exploding_from_pydantic(*args, **kwargs):
        raise boom

    monkeypatch.setattr(crud.BusDB, "from_pydantic", exploding_from_pydantic)

    replacement = build_network("Prod", ["b1", "b2"], [("br1", "b1", "b2")])
    with pytest.raises(RuntimeError) as excinfo:
        gi.save_network_to_db(replacement, overwrite=True)
    assert excinfo.value is boom

    monkeypatch.undo()

    restored = gi.load_network_from_db("Prod")
    assert sorted(restored.buses) == ["b1", "b2"]
    assert sorted(restored.branches) == ["br1"]


def test_path_segment_without_matching_branch_raises_named_valueerror(db_path):
    """
    A stale path segment must be reported as a ValueError naming both elements.

    Pruning ``network.branches`` after ``create_paths`` used to blow up with
    ``AttributeError: 'NoneType' object has no attribute '_sa_instance_state'``.
    """
    net = build_network(
        "Study", ["b1", "b2", "b3"], [("br1", "b1", "b2"), ("br2", "b2", "b3")]
    )
    del net.branches["br2"]

    with pytest.raises(ValueError) as excinfo:
        gi.save_network_to_db(net)

    message = str(excinfo.value)
    assert "br2" in message
    assert "path_1" in message
    assert "Study" in message


def test_failed_first_save_does_not_leave_a_half_written_network(db_path):
    """A failing save of a *new* network must not leave partial rows behind."""
    net = build_network(
        "Study", ["b1", "b2", "b3"], [("br1", "b1", "b2"), ("br2", "b2", "b3")]
    )
    del net.branches["br2"]

    with pytest.raises(ValueError):
        gi.save_network_to_db(net)

    counts = table_counts(db_path, ["networks", "buses", "branches", "paths"])
    assert counts == {"networks": 0, "buses": 0, "branches": 0, "paths": 0}


# ---------------------------------------------------------------------------
# Bug 2 -- save_network must update existing type rows
# ---------------------------------------------------------------------------


def test_save_network_updates_an_existing_bustype(db_path):
    """Editing a BusType and saving a network must persist the new physics."""
    net_a = gi.create_network(name="NetA", frequencies=FREQUENCIES)
    gi.create_bus(name="busA", type=make_bus_type("SharedType", 110.0, "rho*0 + 1"),
                  network=net_a)
    gi.save_network_to_db(net_a)

    net_b = gi.create_network(name="NetB", frequencies=FREQUENCIES)
    gi.create_bus(name="busB", type=make_bus_type("SharedType", 380.0, "rho*0 + 99"),
                  network=net_b)
    gi.save_network_to_db(net_b)

    loaded_b = gi.load_network_from_db("NetB")
    assert loaded_b.buses["busB"].type.voltage_level == 380.0
    assert loaded_b.buses["busB"].type.impedance_formula == "rho*0 + 99"

    # A type is a global catalogue entry: the dedicated saver behaves the same
    # way, so both paths must agree on the stored definition.
    assert gi.load_bustypes_from_db()["SharedType"].voltage_level == 380.0


def test_overwriting_a_network_updates_its_bustype(db_path):
    """Re-saving the same network with an edited BusType must persist it."""
    net = gi.create_network(name="NetA", frequencies=FREQUENCIES)
    gi.create_bus(name="busA", type=make_bus_type("SharedType", 110.0, "rho*0 + 1"),
                  network=net)
    gi.save_network_to_db(net)

    net.buses["busA"].type = make_bus_type("SharedType", 400.0, "rho*0 + 7")
    gi.save_network_to_db(net, overwrite=True)

    loaded = gi.load_network_from_db("NetA")
    assert loaded.buses["busA"].type.voltage_level == 400.0
    assert loaded.buses["busA"].type.impedance_formula == "rho*0 + 7"


def test_save_network_updates_an_existing_branchtype(db_path):
    """Editing a BranchType and saving a network must persist the new physics."""
    first = make_branch_type("SharedBranchType", "(rho*0 + 0.25 + I*f*0.012)*l")
    net_a = build_network("NetA", ["b1", "b2"], [("br1", "b1", "b2")],
                          branch_type=first)
    gi.save_network_to_db(net_a)

    second = make_branch_type("SharedBranchType", "(rho*0 + 9.75 + I*f*0.012)*l")
    net_b = build_network("NetB", ["b1", "b2"], [("br1", "b1", "b2")],
                          branch_type=second)
    gi.save_network_to_db(net_b)

    loaded_b = gi.load_network_from_db("NetB")
    stored_formula = loaded_b.branches["br1"].type.self_impedance_formula
    assert stored_formula == "(rho*0 + 9.75 + I*f*0.012)*l"
    assert (
        gi.load_branchtypes_from_db()["SharedBranchType"].self_impedance_formula
        == "(rho*0 + 9.75 + I*f*0.012)*l"
    )


# ---------------------------------------------------------------------------
# Bug 3 -- child rows must be scoped to their network
# ---------------------------------------------------------------------------


def test_saving_a_second_network_does_not_rewrite_the_first(db_path):
    """Two networks may each own an element of the same name."""
    bus_type = make_bus_type("T")

    net_2020 = gi.create_network(name="Net2020", frequencies=FREQUENCIES)
    gi.create_bus(name="Station A", type=bus_type, specific_earth_resistance=10.0,
                  network=net_2020)
    gi.save_network_to_db(net_2020)

    net_2021 = gi.create_network(name="Net2021", frequencies=FREQUENCIES)
    gi.create_bus(name="Station A", type=bus_type, specific_earth_resistance=999.0,
                  network=net_2021)
    gi.save_network_to_db(net_2021)

    assert (
        gi.load_network_from_db("Net2020").buses["Station A"].specific_earth_resistance
        == 10.0
    )
    assert (
        gi.load_network_from_db("Net2021").buses["Station A"].specific_earth_resistance
        == 999.0
    )


def test_default_path_names_do_not_collide_between_networks(db_path):
    """
    ``create_paths`` names every path ``path_1``, so collisions are the default.

    Both networks below own a ``path_1``; each must keep its own segments.
    """
    net_a = build_network("NetA", ["a1", "a2"], [("a_br", "a1", "a2")])
    gi.save_network_to_db(net_a)
    net_b = build_network("NetB", ["b1", "b2"], [("b_br", "b1", "b2")])
    gi.save_network_to_db(net_b)

    loaded_a = gi.load_network_from_db("NetA")
    loaded_b = gi.load_network_from_db("NetB")
    assert "path_1" in loaded_a.paths and "path_1" in loaded_b.paths
    assert [s.name for s in loaded_a.paths["path_1"].segments] == ["a_br"]
    assert [s.name for s in loaded_b.paths["path_1"].segments] == ["b_br"]


def test_faults_and_sources_are_scoped_per_network(db_path):
    """Same-named faults and sources of two networks must stay independent."""
    net_a = build_network("NetA", ["a1", "a2"], [("a_br", "a1", "a2")], solve=False)
    gi.save_network_to_db(net_a)
    net_b = build_network("NetB", ["b1", "b2"], [("b_br", "b1", "b2")], solve=False)
    gi.save_network_to_db(net_b)

    loaded_a = gi.load_network_from_db("NetA")
    loaded_b = gi.load_network_from_db("NetB")
    assert loaded_a.faults["flt"].bus == "a2"
    assert loaded_b.faults["flt"].bus == "b2"
    assert loaded_a.sources["src"].bus == "a1"
    assert loaded_b.sources["src"].bus == "b1"


def test_element_tables_are_keyed_by_network_and_name(db_path):
    """The composite key must be part of the schema, not just the write path."""
    gi.save_network_to_db(build_network("NetA", ["b1", "b2"], [("br1", "b1", "b2")]))

    connection = sqlite3.connect(db_path)
    try:
        for table in ("buses", "branches", "faults", "sources", "paths"):
            key_columns = [
                row[1]
                for row in connection.execute(f"PRAGMA table_info({table})")
                if row[5]  # pk position, 0 when the column is not part of the key
            ]
            assert key_columns == ["network_name", "name"], table
    finally:
        connection.close()


def test_legacy_globally_keyed_database_is_migrated_or_clearly_refused(
    tmp_path,
):
    """
    An old database file must be converted, or fail loudly -- never mis-load.

    ``Base.metadata.create_all`` never adds a column to an existing table, so a
    file written by the association-table schema opens cleanly and would only
    fail deep inside a query. Since the migration module exists, the default is
    to convert the file; ``migrate=False`` keeps the original guard, which is
    what this test pinned before and still checks.
    """
    legacy = str(tmp_path / "legacy.db")
    connection = sqlite3.connect(legacy)
    try:
        # Minimal reproduction of the pre-fix layout: elements keyed by name
        # alone, network membership held in association tables.
        connection.executescript(
            """
            CREATE TABLE networks (
                name VARCHAR NOT NULL,
                description TEXT,
                frequencies BLOB,
                results JSON,
                active_fault_name VARCHAR,
                PRIMARY KEY (name)
            );
            CREATE TABLE buses (
                name VARCHAR NOT NULL,
                description TEXT,
                type_name VARCHAR,
                specific_earth_resistance FLOAT,
                impedance JSON,
                active BOOLEAN NOT NULL,
                PRIMARY KEY (name)
            );
            CREATE TABLE network_buses (
                network_name VARCHAR,
                bus_name VARCHAR
            );
            CREATE TABLE bus_types (
                name VARCHAR NOT NULL,
                description TEXT,
                system_type VARCHAR NOT NULL,
                voltage_level FLOAT NOT NULL,
                impedance_formula TEXT NOT NULL,
                PRIMARY KEY (name)
            );
            INSERT INTO networks (name) VALUES ('Legacy');
            INSERT INTO bus_types VALUES
                ('BT', NULL, 'Substation', 110.0, 'rho*0 + 1');
            INSERT INTO buses (name, type_name, specific_earth_resistance, active)
                VALUES ('Station A', 'BT', 100.0, 1);
            INSERT INTO network_buses VALUES ('Legacy', 'Station A');
            """
        )
        # ``frequencies`` is a PickleType BLOB, so it cannot go in the script.
        connection.execute(
            "UPDATE networks SET frequencies = ?", (pickle.dumps(FREQUENCIES),)
        )
        connection.commit()
    finally:
        connection.close()

    # ``migrate=False`` is the opt-out: no conversion, but a message naming the
    # fix instead of an ``OperationalError`` from deep inside a query.
    gi.close_dbsession()
    gi.start_dbsession(legacy, migrate=False)
    try:
        with pytest.raises(RuntimeError) as excinfo:
            gi.load_network_from_db("Legacy")
        message = str(excinfo.value)
        assert "legacy" in message.lower()
        assert "network_buses" in message
        assert "migrate_database" in message

        with pytest.raises(RuntimeError):
            gi.save_network_to_db(
                gi.create_network(name="Fresh", frequencies=FREQUENCIES)
            )
    finally:
        gi.close_dbsession()

    # Opting out must leave the file alone, so the default path below still has
    # the untouched original to work from.
    assert not os.path.exists(legacy + ".bak")

    # The default converts instead of refusing. This file is only partly
    # populated -- no branches, faults, sources or paths tables at all -- which
    # the migration has to tolerate rather than trip over.
    gi.start_dbsession(legacy)
    try:
        assert gi.needs_migration(legacy) == "none"
        assert os.path.exists(legacy + ".bak")
        loaded = gi.load_network_from_db("Legacy")
        assert list(loaded.buses) == ["Station A"]
    finally:
        gi.close_dbsession()


# ---------------------------------------------------------------------------
# Bug 4 -- overwrite must not orphan the dropped child rows
# ---------------------------------------------------------------------------


def test_overwriting_a_shrunken_network_drops_its_orphaned_rows(db_path):
    """Shrinking a network and re-saving must delete the removed elements."""
    large = build_network(
        "N", ["b1", "b2", "b3"], [("br1", "b1", "b2"), ("br2", "b2", "b3")]
    )
    gi.save_network_to_db(large)
    assert table_counts(db_path, ["buses", "branches"]) == {"buses": 3, "branches": 2}

    small = build_network("N", ["b1", "b2"], [("br1", "b1", "b2")])
    gi.save_network_to_db(small, overwrite=True)

    assert table_counts(db_path, ["buses", "branches", "path_segments"]) == {
        "buses": 2,
        "branches": 1,
        "path_segments": 1,
    }
    loaded = gi.load_network_from_db("N")
    assert sorted(loaded.buses) == ["b1", "b2"]
    assert sorted(loaded.branches) == ["br1"]


# ---------------------------------------------------------------------------
# Bug 5 -- collection order must survive the round-trip
# ---------------------------------------------------------------------------


def _ordered_segment_network(name, segment_order):
    """
    Build a network whose single path lists its branches in a chosen order.

    Parameters
    ----------
    name : str
        Network name.
    segment_order : list of str
        Branch names in the order the path traverses them.

    Returns
    -------
    Network
        A hand-assembled network; the path order is set explicitly rather than
        derived by the path finder.
    """
    bus_type = make_bus_type()
    branch_type = make_branch_type()
    buses = {
        bus_name: Bus(
            name=bus_name,
            type=bus_type,
            impedance={50.0: ComplexNumber(real=1, imag=0)},
        )
        for bus_name in ("x", "y")
    }
    branches = {
        branch_name: Branch(
            name=branch_name,
            type=branch_type,
            length=1.0,
            from_bus="x",
            to_bus="y",
            self_impedance={50.0: ComplexNumber(real=1, imag=0)},
            mutual_impedance={50.0: ComplexNumber(real=0, imag=0)},
        )
        for branch_name in ("s1", "s2", "s3", "s4")
    }
    return Network(
        name=name,
        frequencies=FREQUENCIES,
        buses=buses,
        branches=branches,
        sources={
            "src": Source(
                name="src", bus="x", values={50.0: ComplexNumber(real=1, imag=0)}
            )
        },
        faults={"flt": Fault(name="flt", bus="y", scalings={50.0: 1.0})},
        paths={
            "p": Path(
                name="p",
                source="src",
                fault="flt",
                segments=[branches[branch_name] for branch_name in segment_order],
            )
        },
    )


def test_path_segment_order_survives_the_roundtrip(db_path, emitted_sql):
    """
    Segments must come back in the order they were written.

    The round-trip assertion alone is not enough to pin the ``order_by`` down:
    ``position`` is part of the ``path_segments`` primary key, so SQLite's
    autoindex on ``(network_name, path_name, position)`` happens to hand the
    rows back in position order even without an ORDER BY. That is an artefact of
    one storage engine's query plan, not a guarantee, so the test also asserts
    that the ordering is actually requested from the database.
    """
    gi.save_network_to_db(_ordered_segment_network("P", ["s3", "s1", "s4", "s2"]))
    del emitted_sql[:]
    loaded = gi.load_network_from_db("P")
    assert [s.name for s in loaded.paths["p"].segments] == ["s3", "s1", "s4", "s2"]

    segment_selects = [
        statement
        for statement in emitted_sql
        if statement.lstrip().upper().startswith("SELECT")
        and "FROM path_segments" in statement
    ]
    assert segment_selects, "no segment query was captured"
    for statement in segment_selects:
        assert "ORDER BY path_segments.position" in statement, statement


def test_path_segment_order_survives_an_order_changing_overwrite(db_path):
    """Re-saving with a different segment order must replace the stored order."""
    gi.save_network_to_db(_ordered_segment_network("P", ["s1", "s2", "s3", "s4"]))
    gi.save_network_to_db(
        _ordered_segment_network("P", ["s4", "s3", "s2", "s1"]), overwrite=True
    )
    loaded = gi.load_network_from_db("P")
    assert [s.name for s in loaded.paths["p"].segments] == ["s4", "s3", "s2", "s1"]


def _multi_collection_network(name):
    """
    Build a network whose sources, faults and paths are not in name order.

    Each of the three collections is populated in the order ``z``, ``a``, ``m``
    so that any collection coming back sorted by name -- which is what the
    ``(network_name, name)`` primary-key index yields without an explicit
    ``order_by`` -- is immediately visible.

    Parameters
    ----------
    name : str
        Network name.

    Returns
    -------
    Network
        A hand-assembled network with three sources, three faults and three
        paths.
    """
    bus_type = make_bus_type()
    branch_type = make_branch_type()
    buses = {
        bus_name: Bus(
            name=bus_name,
            type=bus_type,
            impedance={50.0: ComplexNumber(real=1, imag=0)},
        )
        for bus_name in ("x", "y")
    }
    branch = Branch(
        name="br",
        type=branch_type,
        length=1.0,
        from_bus="x",
        to_bus="y",
        self_impedance={50.0: ComplexNumber(real=1, imag=0)},
        mutual_impedance={50.0: ComplexNumber(real=0, imag=0)},
    )
    prefixes = ("z", "a", "m")
    return Network(
        name=name,
        frequencies=FREQUENCIES,
        buses=buses,
        branches={"br": branch},
        sources={
            f"{prefix}_src": Source(
                name=f"{prefix}_src",
                bus="x",
                values={50.0: ComplexNumber(real=1, imag=0)},
            )
            for prefix in prefixes
        },
        faults={
            f"{prefix}_flt": Fault(name=f"{prefix}_flt", bus="y", scalings={50.0: 1.0})
            for prefix in prefixes
        },
        paths={
            f"{prefix}_path": Path(
                name=f"{prefix}_path",
                source=f"{prefix}_src",
                fault=f"{prefix}_flt",
                segments=[branch],
            )
            for prefix in prefixes
        },
    )


def test_source_fault_and_path_dict_order_survives_the_roundtrip(db_path):
    """
    ``Network.sources`` / ``faults`` / ``paths`` must keep their insertion order.

    Without the ``position`` ordering these come back in primary-key order,
    which for these tables means alphabetically by element name.
    """
    gi.save_network_to_db(_multi_collection_network("Ord2"))
    loaded = gi.load_network_from_db("Ord2")

    assert list(loaded.sources) == ["z_src", "a_src", "m_src"]
    assert list(loaded.faults) == ["z_flt", "a_flt", "m_flt"]
    assert list(loaded.paths) == ["z_path", "a_path", "m_path"]


def test_bus_and_branch_dict_order_survives_the_roundtrip(db_path):
    """
    ``Network.buses`` / ``branches`` must round-trip in insertion order.

    The names below are deliberately not in alphabetical order, which is what
    the primary-key index returned before the ``position`` columns existed.
    """
    net = build_network(
        "Ord", ["zb", "ab", "mb"], [("z_br", "zb", "ab"), ("a_br", "ab", "mb")]
    )
    gi.save_network_to_db(net)
    loaded = gi.load_network_from_db("Ord")

    assert list(loaded.buses) == ["zb", "ab", "mb"]
    assert list(loaded.branches) == ["z_br", "a_br"]


# ---------------------------------------------------------------------------
# Cross-check: the physics must be unaffected by the round-trip
# ---------------------------------------------------------------------------


def test_resolving_a_loaded_network_reproduces_the_original_results(db_path):
    """
    Re-solving after a round-trip must reproduce the per-bus-name numbers.

    The audit flagged apparent sign flips on ``uepr_freq`` / ``i_inj_freq``;
    those came from comparing the result lists positionally across a reordered
    collection. Keyed by name, original and reloaded network must agree, both
    for the stored results and after a fresh solve.
    """
    net = build_network(
        "Ord", ["zb", "ab", "mb"], [("z_br", "zb", "ab"), ("a_br", "ab", "mb")]
    )
    gi.save_network_to_db(net)
    loaded = gi.load_network_from_db("Ord")

    expected = results_by_name(net.model_dump()["results"]["flt"])
    assert results_by_name(loaded.model_dump()["results"]["flt"]) == expected

    gi.run_fault(network=loaded, fault_name="flt")
    resolved = results_by_name(loaded.model_dump()["results"]["flt"])

    for bus_name, bus_result in expected["buses"].items():
        for field in ("uepr", "ia", "i_inj"):
            assert resolved["buses"][bus_name][field] == pytest.approx(
                bus_result[field], rel=1e-9, abs=1e-12
            )
    for frequency, value in expected["reduction_factor"]["value"].items():
        assert resolved["reduction_factor"]["value"][frequency] == pytest.approx(
            value, rel=1e-9, abs=1e-12
        )

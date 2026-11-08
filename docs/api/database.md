# Database

SQLite persistence for bus types, branch types and entire networks.
`gi.Database(path)` is a database object: it owns its SQLAlchemy engine
and session factory and offers the persistence operations as methods.
The module-level helpers (`gi.start_dbsession`, `gi.save_network_to_db`,
...) work as before and delegate to a default `Database`. The ORM mirror
classes live next to the Pydantic models in
`groundinsight.models.database_models` and expose `from_pydantic` /
`to_pydantic` converters. For a single network in a file of its own, see
the [JSON project file](io.md#project-files-json).

## Physical / modelling context

The persistence layer is a *side-channel* to the in-memory
Pydantic model. Every Pydantic class has an ORM counterpart with
suffix `DB` (`BusDB`, `BranchDB`, `NetworkDB`, ...). Many-to-many
relationships (e.g. a `BranchType` shared by several `Network`
instances, or a `Path` composed of many `Branch` segments) are
modelled through association tables (`network_buses`,
`path_segments`, ...). Two design choices are worth noting:

- **Frequency lists** are stored as a JSON array. They are
  conceptually atomic — a network has *one* frequency list, never
  shared, never queried element-wise — so a child table is not
  justified. Up to 0.6.0 the column was a `PickleType`, which meant
  that *reading* a database file executed whatever the blob
  contained, before any Pydantic validator saw it. A `.db` file is a
  shared artefact — it gets mailed around and checked into
  repositories — so that is the same trust problem the formula guard
  in `utils.validations` exists for. Existing files are converted by
  `gi.migrate_database(...)` with an unpickler that resolves nothing
  but `list`, `float` and `int`; anything else aborts the migration
  and leaves the file untouched.
- **Impedance dicts** $Z(f) \in \mathbb{C}$ are stored as JSON
  with stringified frequency keys, because SQLite has no native
  complex type. The `ComplexNumber` Pydantic helper guarantees
  symmetric round-trips on read-back.

## The database object

```python
# fragment: assumes a bus type `bus_type` and a network `net` named "demo"
import groundinsight as gi

with gi.Database("library.db") as db:
    # 1. Persist a re-usable BusType
    db.save_bustype(bus_type, overwrite=True)

    # 2. Persist an entire fault-ready network
    db.save_network(net, overwrite=True)

    # 3. Read back, list, delete
    recovered_types = db.load_bustypes()          # dict[name -> BusType]
    recovered_net = db.load_network("demo")
    db.list_networks()                            # ['demo', ...]
    db.delete_network("old variant")              # its types stay
```

| Method | Purpose |
|---|---|
| `save_bustype(t, overwrite=False)`, `load_bustypes()`, `delete_bustype(name)` | bus-type catalogue |
| `save_branchtype(t, overwrite=False)`, `load_branchtypes()`, `delete_branchtype(name)` | branch-type catalogue |
| `save_network(net, overwrite=False, overwrite_types=False)`, `load_network(name)` | networks with all elements and their stored results |
| `list_networks()`, `delete_network(name)` | stored network names; delete a network and its elements |
| `needs_migration()`, `migrate(backup=True)` | schema check / conversion of a file opened with `migrate=False` |
| `session()` | context manager yielding a short-lived SQLAlchemy `Session` for direct ORM work |
| `close()`, `closed`, `path`, `engine` | lifetime and introspection; the object is also a context manager |

A type that a stored network still uses cannot be deleted; the error names
the networks. `gi.Database(path, migrate=True)` converts a file written by an
earlier release before opening it (keeping a `.bak` copy); with
`migrate=False` the file stays untouched and the first read or write of a
legacy file raises an error that names `migrate()`. `gi.Database(":memory:")`
gives a private in-memory database, handy for tests.

### Several files, several threads

Every method runs in its own short-lived session, so no SQLAlchemy `Session`
is ever shared between threads. Two `Database` objects on two files are
independent and can be used from two threads at the same time — a GUI with
two open projects, or a server that serves two users. One `Database` can be
used from several threads as well: operations on the *same file* are
serialised by a lock that every `Database` object of the process shares for
that file, because SQLite allows one writer at a time and two unserialised
writers race on shared rows (a second insert of the same bus type) or fail
with `database is locked`. Operations on different files never wait for each
other. Separate processes on one file rely on SQLite's own locking.

### The module-level helpers

```python
# fragment: assumes a network `net` named "demo"
db = gi.start_dbsession("library.db")   # returns the default Database
gi.save_network_to_db(network=net, overwrite=True)
recovered_net = gi.load_network_from_db(name="demo")
db.list_networks()                       # the same object, all methods
gi.close_dbsession()
```

`gi.start_dbsession(path)` opens the *default* database, and
`gi.save_bustype_to_db`, `gi.load_bustypes_from_db`,
`gi.save_branchtype_to_db`, `gi.load_branchtypes_from_db`,
`gi.save_network_to_db` and `gi.load_network_from_db` use it. Names,
arguments, messages and errors are those of 0.6.0: without a started session
they raise `RuntimeError`, and a second `start_dbsession` for another file
raises unless `force=True`. Since the default is a `Database`, the call now
returns it. The lower-level CRUD functions in `groundinsight.database.crud`
take an explicit SQLAlchemy `Session` argument; use them with
`with db.session() as session:`.

## Deprecated: `gi.session` and `gi.db_session`

Up to 0.6.0 the session lived in module globals: `gi.session`, its alias
`gi.db_session`, `gi.engine` and `gi.SessionLocal`. They still resolve —
to the default database's thread-local scoped session (the same object for
both names), its engine and its session factory, or `None` without a started
session — but every access raises a `DeprecationWarning`. They will be
removed no earlier than two releases and six months after the release that
deprecated them. `session` and `db_session` are no longer in `__all__`, so a
star import does not trigger the warning.

| Before | Now |
|---|---|
| `crud.save_network(net, gi.session)` | `db.save_network(net)`, or `with db.session() as s: crud.save_network(net, s)` |
| `gi.session()` (a thread-local `Session`) | `with db.session() as s: ...` (a short-lived `Session`) |
| `gi.engine` | `db.engine` |
| `gi.SessionLocal()` | `with db.session() as s: ...` |

`db` is a `gi.Database(path)`, or the object returned by
`gi.start_dbsession(path)`.

## Cross-repo version helper: `gi.show_versions()`

Returns a dictionary with the installed versions of ``groundinsight``,
the active Python and platform, and — if importable — the peer
packages ``groundfield`` and ``groundmeas``. Intended as the
ADR-0013-aligned cross-repo convention, so a bug report can include
the full earthing-platform stack with a single call:

```pycon
>>> import groundinsight as gi
>>> gi.show_versions()
{'groundinsight': '<installed version>',
 'python': '<running interpreter>',
 'platform': 'Linux-...-x86_64-...'}
```

## Database object and module-level helpers

::: groundinsight.database.store

## CRUD functions

::: groundinsight.database.crud

## ORM models

::: groundinsight.models.database_models

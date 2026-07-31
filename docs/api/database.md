# Database

SQLAlchemy-based CRUD helpers for persisting bus types, branch
types and entire networks to a SQLite database. The ORM mirror
classes live next to the Pydantic models in
`groundinsight.models.database_models` and expose `from_pydantic` /
`to_pydantic` converters.

## Physical / modelling context

The persistence layer is a *side-channel* to the in-memory
Pydantic model. Every Pydantic class has an ORM counterpart with
suffix `DB` (`BusDB`, `BranchDB`, `NetworkDB`, ...). Many-to-many
relationships (e.g. a `BranchType` shared by several `Network`
instances, or a `Path` composed of many `Branch` segments) are
modelled through association tables (`network_buses`,
`path_segments`, ...). Two design choices are worth noting:

- **Frequency lists** are stored as `PickleType` blobs. They are
  conceptually atomic — a network has *one* frequency list, never
  shared, never queried element-wise — so the round-trip price of
  storing them as JSON or as a child table is not justified.
- **Impedance dicts** $Z(f) \in \mathbb{C}$ are stored as JSON
  with stringified frequency keys, because SQLite has no native
  complex type. The `ComplexNumber` Pydantic helper guarantees
  symmetric round-trips on read-back.

A typical workflow opens a session via `gi.start_dbsession(path)`,
reads or writes types or networks via the CRUD helpers, and closes
the session at process exit. Sessions are not thread-safe; use one
session per process.

## Example

```python
import groundinsight as gi

gi.start_dbsession("library.db")

# 1. Persist a re-usable BusType
bus_type = ...  # gi.BusType instance
gi.save_bustype_to_db(bus_type=bus_type, overwrite=True)

# 2. Persist an entire fault-ready network
gi.save_network_to_db(network=net, overwrite=True)

# 3. Read back later
recovered_types = gi.load_bustypes_from_db()  # dict[name -> BusType]
recovered_net = gi.load_network_from_db(name="demo")

gi.close_dbsession()
```

The high-level helpers re-exported on the top-level package
(``gi.save_bustype_to_db``, ``gi.load_bustypes_from_db``,
``gi.save_branchtype_to_db``, ``gi.load_branchtypes_from_db``,
``gi.save_network_to_db``, ``gi.load_network_from_db``) wrap the
session lifecycle. The lower-level CRUD functions in
``groundinsight.database.crud`` take an explicit SQLAlchemy ``Session``
argument and are intended for callers that already manage their own
session scope.

## Session globals: `gi.session` and `gi.db_session`

The module exposes two equivalent names for the scoped session:

- ``gi.session`` — canonical name.
- ``gi.db_session`` — historic alias, kept to support
  ``from groundinsight import db_session`` written before 0.4.0.

Both names point at the **same** :class:`sqlalchemy.orm.scoped_session`
instance while a session is active, and both are reset to ``None``
when no session is active. The lock-step contract is pinned by a single
private helper, ``groundinsight._set_session(new)``. Every code path
that rebinds the session — ``start_dbsession``, ``close_dbsession``
and any future ``swap_dbsession`` context manager — must route
through ``_set_session`` instead of assigning the module global
directly. This removes the cross-API drift risk between the two names.

## Cross-repo version helper: `gi.show_versions()`

Returns a dictionary with the installed versions of ``groundinsight``,
the active Python and platform, and — if importable — the peer
packages ``groundfield`` and ``groundmeas``. Intended as the
ADR-0013-aligned cross-repo convention, so a bug report can include
the full earthing-platform stack with a single call:

```python
>>> import groundinsight as gi
>>> gi.show_versions()
{'groundinsight': '0.5.0',
 'python': '3.11.7',
 'platform': 'Linux-...-x86_64-...'}
```

## CRUD functions

::: groundinsight.database.crud

## ORM models

::: groundinsight.models.database_models

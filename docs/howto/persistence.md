# Save, load and migrate

A network can be kept in two ways: as a **project file** — one network in one
JSON file, to mail, to version or to open in the GUI — or in a **SQLite
database** that holds a catalogue of bus and branch types and many networks.
This guide uses both, migrates a database written by an earlier release and
shows what happens to files from an untrusted source. The script is
[`persistence.py`](../snippets/persistence.py); its output is at the
[end of the page](#output).

```python
--8<-- "docs/snippets/persistence.py:network"
```

## The project file

`gi.save_network_to_json(network, path)` writes the network — elements, types,
frequencies and the stored results — as JSON, and
`gi.load_network_from_json(path)` reads it back.

- **UTF-8 on every platform.** Names with `Ω`, `ρ` or `ü` are stored the same
  on Windows, macOS and Linux. A file that is not UTF-8 — only 0.6.0 and
  earlier on a non-UTF-8 system wrote such files — is read with the platform
  encoding and announced (`PROJECT_FILE_LEGACY_ENCODING`); saving it again
  writes UTF-8.
- **Atomic.** The file is written to a temporary file next to the target and
  then moved over it, so a failure leaves an existing project file intact.
- **Versioned.** The file starts with `schema_version` (the version of the file
  layout) and `groundinsight_version` (the release that wrote it).

```python
--8<-- "docs/snippets/persistence.py:json"
```

`Network.model_dump_json()` and `gi.Network.model_validate_json(text)` remain
available for a network embedded in your own file format; the project-file
functions add the header, the encoding rules and the atomic write.

## What is promised for old and new files

- **Files of every earlier release load.** A project file without
  `schema_version` (0.6.0 and earlier) is read unchanged. A database is
  migrated to the current schema when it is opened, after a copy of the
  original is kept as `<name>.db.bak`. Results stored before the earthing
  voltage and impedance were redefined at the fault bus load with those
  quantities empty and an `info` record `LEGACY_EARTHING_DEFINITION` in
  `Result.diagnostics`; solve the fault again.
- **Files of a newer schema are refused** with a `ValueError` that names both
  schema versions, instead of being read with whatever the newer release
  added silently dropped. Update groundinsight to open them.

```python
--8<-- "docs/snippets/persistence.py:newer"
```

## The database

`gi.Database` — `gi.Database(path)` — opens (or creates) a SQLite file and offers the
operations as methods: `save_bustype`, `load_bustypes`, `save_branchtype`,
`load_branchtypes`, `save_network` (with its types and stored results),
`load_network`, `list_networks`, `delete_network`, and their counterparts for
types. It is a context manager; every operation runs in a short-lived session
of its own, and two `Database` objects on the same file share a lock, so a
GUI or a server may use them from several threads. `gi.Database(":memory:")`
gives a private in-memory database. The module-level helpers
(`gi.start_dbsession`, `gi.save_network_to_db`, `gi.load_network_from_db`, …)
work as before on a default `Database`; the old globals `gi.session` and
`gi.db_session` still resolve but are deprecated. Details:
[Database API](../api/database.md).

```python
--8<-- "docs/snippets/persistence.py:database"
```

## Migrate a database

`needs_migration` — `gi.needs_migration(path)` — classifies a file: `"none"` (current),
`"add_columns"` (columns of a later release missing), `"legacy_scoped"`
(elements keyed by name alone, up to 0.4.0) or `"pickle_frequencies"`
(frequency lists stored with pickle, up to 0.6.0).
`migrate_database` — `gi.migrate_database(path, backup=True)` — converts the file in place after
copying it to `.bak`, and returns a `gi.MigrationReport` — `migrated`, `kind`,
`backup_path`, the rows written, the columns added, and in `needs_attention`
whether anything was filled in or left out that you should look at.
`gi.Database(path)` does the same on opening (`migrate=True`, the default);
with `migrate=False` the file stays untouched and its first use raises an
error that names `migrate()`.

```python
--8<-- "docs/snippets/persistence.py:migration"
```

The script simulates a file of an earlier release by removing a column that
a later release added; a real file from 0.5.0 lacks exactly such columns.

## Files from an untrusted source

A `.db` or `.json` file is shared like a document — mailed, checked into a
repository, downloaded. Two properties keep opening one from running code:

- **The formula guard.** Impedance formulas are parsed with SymPy, which
  evaluates Python. Every formula — typed, imported or loaded — passes the
  formula guard first: arithmetic over names and numbers, a fixed list of
  functions, and bounds on the size of numbers, exponents and terms; string
  literals, attribute access, other calls and dunder names are refused
  ([Formula syntax](../reference/formula-syntax.md#why-the-guard-exists)).
- **No pickle.** Databases store frequency lists as JSON. Files of 0.6.0 and
  earlier stored them with pickle; the migration converts them with an
  unpickler that resolves nothing but `list`, `float` and `int`, and aborts on
  anything else.

The guard bounds the work per formula, not the size of a file: a file with
many types and elements still takes time to load. A server that opens files
from strangers should do so in a worker process with a time limit; a session
of the [service layer](service-layer.md) also refuses files above its
`max_buses` and `max_branches` on opening.

```python
--8<-- "docs/snippets/persistence.py:untrusted"
```

## Output

```text title="Output of persistence.py"
project file header: ['schema_version', 'groundinsight_version']
JSON round trip identical: True
newer schema: ValueError: 'from_the_future.json' uses project-file schema version 99
database holds: ['Umspannwerk Süd']
database round trip identical: True
migration: add_columns -> none, backup library.db.bak, added {'bus_types': ['electrode_current_split']}
after migration identical: True
stored EPR (RMS): {'UW': 44.46, 'S': 524.57}
untrusted formula refused: Impedance formula must not contain string literals or other non-arithmetic tokens
```

"Identical" compares the complete JSON serialisation of the network — every
element, type, frequency and stored result — before saving and after
loading.

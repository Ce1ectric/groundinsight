# docs/snippets/persistence.py
"""
Save, load and migrate: the example of the how-to "Save, load and migrate".

A solved two-station network goes into a JSON project file and into a SQLite
database, and comes back out of both. Then the script shows the two
compatibility rules: a project file of a newer schema is refused with a clear
message, and a database written by an earlier release -- here simulated by
removing a column that a later release added -- is migrated on opening, with a
backup of the original file. Last, a project file from an untrusted source
with a formula that is not arithmetic is refused before anything evaluates it.

The files are written to the current directory.
Run it as a script: ``python docs/snippets/persistence.py``.
"""

import json
import re
import sqlite3
from pathlib import Path

import groundinsight as gi

# --8<-- [start:network]
station = gi.BusType(name="station", system_type="Station", voltage_level=20.0,
                     impedance_formula="2*rho/(3*1.13*sqrt(100.0))")
substation = gi.BusType(name="substation", system_type="Substation", voltage_level=20.0,
                        impedance_formula="0.5")
cable = gi.BranchType(name="cable", grounding_conductor=True,
                      self_impedance_formula="(0.2 + j*2*pi*f*1.2e-3)*l",
                      mutual_impedance_formula="(j*2*pi*f*1.0e-3)*l")
net = gi.create_network(name="Umspannwerk Süd", frequencies=[50.0, 250.0])
gi.create_bus(name="UW", type=substation, specific_earth_resistance=100.0, network=net)
gi.create_bus(name="S", type=station, specific_earth_resistance=100.0, network=net)
gi.create_branch(name="K", type=cable, from_bus="UW", to_bus="S", length=3.0, network=net)
gi.create_source(name="infeed", bus="UW", values={50.0: 1000.0, 250.0: 50.0}, network=net)
gi.create_fault(name="F", bus="S", scalings={50.0: 1.0, 250.0: 1.0}, network=net)
gi.run_fault(net, "F")
# --8<-- [end:network]

# --8<-- [start:json]
gi.save_network_to_json(net, "project.json")       # UTF-8, atomic, with schema_version
header = list(json.loads(Path("project.json").read_text(encoding="utf-8")))[:2]
from_json = gi.load_network_from_json("project.json")
# --8<-- [end:json]

# --8<-- [start:newer]
newer = json.loads(Path("project.json").read_text(encoding="utf-8"))
newer["schema_version"] = 99                          # as if a later release wrote it
Path("from_the_future.json").write_text(json.dumps(newer), encoding="utf-8")
try:
    gi.load_network_from_json("from_the_future.json")
except ValueError as refused:
    newer_refused = str(refused).split(" (written")[0]
# --8<-- [end:newer]

# --8<-- [start:database]
for old_file in ("library.db", "library.db.bak"):     # start from a fresh file
    Path(old_file).unlink(missing_ok=True)
with gi.Database("library.db") as db:
    db.save_bustype(station)                          # a reusable type catalogue
    db.save_branchtype(cable)
    db.save_network(net)                              # with its types and stored results
with gi.Database("library.db") as db:
    stored = db.list_networks()
    from_database = db.load_network("Umspannwerk Süd")
# --8<-- [end:database]

# --8<-- [start:migration]
connection = sqlite3.connect("library.db")            # simulate a file of an earlier release:
connection.execute("ALTER TABLE bus_types DROP COLUMN electrode_current_split")
connection.commit()
connection.close()

kind_before = gi.needs_migration("library.db")       # "add_columns"
report = gi.migrate_database("library.db")            # copies library.db to library.db.bak first
kind_after = gi.needs_migration("library.db")         # "none"
with gi.Database("library.db") as db:                 # Database(...) migrates on opening by default
    after_migration = db.load_network("Umspannwerk Süd")
# --8<-- [end:migration]

# --8<-- [start:untrusted]
untrusted = json.loads(Path("project.json").read_text(encoding="utf-8"))
untrusted["buses"]["S"]["type"]["impedance_formula"] = "__import__('os').getcwd()"
Path("untrusted.json").write_text(json.dumps(untrusted), encoding="utf-8")
try:
    gi.load_network_from_json("untrusted.json")
except ValueError as refused:  # a pydantic ValidationError is a ValueError
    untrusted_refused = re.search(r"Impedance formula [^(]*", str(refused)).group(0).strip()
# --8<-- [end:untrusted]

if __name__ == "__main__":
    epr = {b.name: round(b.uepr, 2) for b in net.results["F"].buses}
    print("project file header:", header)
    print("JSON round trip identical:", from_json.model_dump_json() == net.model_dump_json())
    print("newer schema: ValueError:", newer_refused)
    print("database holds:", stored)
    print("database round trip identical:", from_database.model_dump_json() == net.model_dump_json())
    print(f"migration: {kind_before} -> {kind_after}, backup {Path(report.backup_path).name},"
          f" added {report.added_columns}")
    print("after migration identical:", after_migration.model_dump_json() == net.model_dump_json())
    print("stored EPR (RMS):", epr)
    print("untrusted formula refused:", untrusted_refused)

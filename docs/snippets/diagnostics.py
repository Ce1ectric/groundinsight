# docs/snippets/diagnostics.py
"""
The example of the how-to "Diagnostics, logging and warnings".

A ring of four stations whose cable type declares no phase impedance: the
solve has to split the phase current with a proxy and says so in the result.
Then the same network gets a bus without connection and a source on a bus
that does not exist -- as a hand-edited project file could carry them -- and
the pre-flight check lists both. Last: a warning outside a solve, collected
explicitly, and a warning category turned into an error.

Run it as a script: ``python docs/snippets/diagnostics.py``.
"""

import json
import warnings

import groundinsight as gi
from groundinsight.diagnostics import collecting

# --8<-- [start:solve]
station = gi.BusType(name="station", system_type="Station", voltage_level=20.0,
                     impedance_formula="2.0")
cable = gi.BranchType(name="cable without phase impedance", grounding_conductor=True,
                      self_impedance_formula="(0.2 + j*2*pi*f*1.2e-3)*l",
                      mutual_impedance_formula="(j*2*pi*f*1.0e-3)*l")
ring = gi.create_network(name="ring", frequencies=[50.0])
for name in ("S0", "S1", "S2", "S3"):
    gi.create_bus(name=name, type=station, network=ring)
for name, a, b in (("K1", "S0", "S1"), ("K2", "S1", "S2"), ("K3", "S2", "S3"), ("K4", "S3", "S0")):
    gi.create_branch(name=name, type=cable, from_bus=a, to_bus=b, length=1.0, network=ring)
gi.create_source(name="infeed", bus="S0", values={50.0: 1000.0}, network=ring)
gi.create_fault(name="F", bus="S2", network=ring)

result = gi.run_fault(ring, "F")
for record in result.diagnostics:  # code, severity, category, message, elements
    if record.code == "PHASE_PROXY":
        proxy_branches = sorted(name for kind, name in record.elements if kind == "branch")
payload = json.dumps([record.model_dump() for record in result.diagnostics])  # for a front end
# --8<-- [end:solve]

# --8<-- [start:check]
gi.create_bus(name="X", type=station, network=ring)                 # no branch to it
first_check = gi.check_network(ring)

ring.sources["ghost"] = gi.Source(name="ghost", bus="nowhere",      # as a hand-edited
                                  values={50.0: 10.0})              # file could carry it
issues = gi.check_network(ring)
blocking = [d.code for d in issues if d.severity == "error"]
# --8<-- [end:check]

# --8<-- [start:collect]
with collecting() as records:  # warnings raised outside a solve
    gi.create_source(name="extra", bus="S1", values={50.0: 5.0, 150.0: 1.0}, network=ring)
# --8<-- [end:collect]

# --8<-- [start:strict]
del ring.sources["ghost"]
with warnings.catch_warnings():
    warnings.simplefilter("error", gi.ModelAssumptionWarning)  # e.g. in a CI job
    try:
        gi.run_fault(ring, "F")
    except gi.ModelAssumptionWarning as raised:
        strict_code = raised.code
# --8<-- [end:strict]

if __name__ == "__main__":
    for record in result.diagnostics:
        print(f"result: {record.code} ({record.severity}, {record.category})")
    print("proxy split on:", proxy_branches)
    print("as JSON for a front end:", len(json.loads(payload)), "record(s)")
    for label, found in (("check", first_check), ("check again", issues)):
        for record in found:
            print(f"{label}: {record.severity} {record.code} {record.elements}")
    print("blocking:", blocking)
    print("collected:", [(r.code, r.elements) for r in records])
    print("turned into an error:", strict_code)

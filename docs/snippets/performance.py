# docs/snippets/performance.py
"""
The example of the how-to "Large and meshed networks".

Three experiments with deterministic outcomes (the times are on the page,
taken from the change log, because they depend on the machine):

1. a ring of 500 stations is solved in the default phase-current mode, which
   never enumerates paths;
2. the number of simple paths between opposite corners of a k x k grid, which
   the legacy mode ``phase_current_mode="paths"`` and ``gi.create_paths``
   enumerate, grows exponentially with the number of meshes;
3. a per-network cap on that enumeration turns an hour-long search into an
   immediate error.

Run it as a script: ``python docs/snippets/performance.py``.
"""

import groundinsight as gi

# --8<-- [start:types]
station = gi.BusType(name="station", system_type="Station", voltage_level=20.0,
                     impedance_formula="2.0")
cable = gi.BranchType(name="cable", grounding_conductor=True,
                      self_impedance_formula="(0.2 + j*2*pi*f*1.2e-3)*l",
                      mutual_impedance_formula="(j*2*pi*f*1.0e-3)*l",
                      phase_impedance_formula="(0.1 + j*2*pi*f*0.4e-3)*l")
# --8<-- [end:types]

# --8<-- [start:ring]
ring = gi.create_network(name="ring 500", frequencies=[50.0])
names = [f"S{i}" for i in range(500)]
for name in names:
    gi.create_bus(name=name, type=station, network=ring)
for i, name in enumerate(names):
    gi.create_branch(name=f"K{i}", type=cable, from_bus=name, to_bus=names[(i + 1) % 500],
                     length=0.5, network=ring)
gi.create_source(name="infeed", bus="S0", values={50.0: 1000.0}, network=ring)
gi.create_fault(name="F", bus="S250", network=ring)
ring_result = gi.run_fault(ring, "F")  # sparse solve, no path enumeration
ring_epr = next(b.uepr for b in ring_result.buses if b.name == "S250")
# --8<-- [end:ring]

# --8<-- [start:grid]
def grid(k):
    """A k x k mesh of stations, source and fault at opposite corners."""
    net = gi.create_network(name=f"grid {k}x{k}", frequencies=[50.0])
    for r in range(k):
        for c in range(k):
            gi.create_bus(name=f"B{r}_{c}", type=station, network=net)
    for r in range(k):
        for c in range(k):
            if c + 1 < k:
                gi.create_branch(name=f"H{r}_{c}", type=cable, from_bus=f"B{r}_{c}",
                                 to_bus=f"B{r}_{c + 1}", length=0.5, network=net)
            if r + 1 < k:
                gi.create_branch(name=f"V{r}_{c}", type=cable, from_bus=f"B{r}_{c}",
                                 to_bus=f"B{r + 1}_{c}", length=0.5, network=net)
    gi.create_source(name="infeed", bus="B0_0", values={50.0: 1000.0}, network=net)
    gi.create_fault(name="F", bus=f"B{k - 1}_{k - 1}", network=net)
    return net

path_counts = {}
for k in (2, 3, 4, 5):
    mesh = grid(k)
    gi.run_fault(mesh, "F")                    # default mode: nothing enumerated
    assert not mesh.paths
    gi.create_paths(mesh)                      # paths as data: the exhaustive search
    path_counts[k] = len(mesh.paths)
# --8<-- [end:grid]

# --8<-- [start:cap]
capped = grid(5)
capped.pathfinder_settings = gi.PathfinderSettings(max_paths=1000)  # this network only
try:
    gi.run_fault(capped, "F", phase_current_mode="paths")
except ValueError as refused:
    cap_message = str(refused).split(". ")[0]
# --8<-- [end:cap]

if __name__ == "__main__":
    print(f"ring: {len(ring.buses)} buses, EPR at S250 = {ring_epr:.2f} V, "
          f"paths enumerated: {len(ring.paths)}")
    print("ring diagnostics:", [d.code for d in ring_result.diagnostics])
    for k, count in path_counts.items():
        print(f"{k}x{k} grid: {count} simple paths from corner to corner")
    print("capped at 1000 paths:", cap_message)

# docs/snippets/service_layer.py
"""
The service layer: the example of the how-to "Service layer and command line".

A front end -- a GUI, a server, a command-line tool -- holds a project in a
``ProjectSession`` of ``groundinsight.api``: it opens one of the documented
example networks, solves a fault into a JSON-safe result object, edits the
network with a command, undoes it, runs the solve as a job off the caller's
thread and saves the project file that ``groundinsight run`` reads.

The project file is written to the current directory.
Run it as a script: ``python docs/snippets/service_layer.py``.
"""

from groundinsight.api import ProjectSession, UpdateBus, json_schemas

# --8<-- [start:session]
session = ProjectSession.from_example("quickstart")   # or ProjectSession.from_file("projekt.json")
summary = session.summary()                            # NetworkSummaryDTO
result = session.solve("fault_at_remote_bus")          # FaultResultDTO
rms = result.key_figures.rms()                         # the RMS row of Network.res_fault
# --8<-- [end:session]

# --8<-- [start:commands]
report = session.apply(UpdateBus(name="bus_fault", fields={"specific_earth_resistance": 250.0}))
stale_after_edit = session.is_stale("fault_at_remote_bus")
session.undo()
stale_after_undo = session.is_stale("fault_at_remote_bus")
# --8<-- [end:commands]

# --8<-- [start:jobs]
handle = session.submit_solve("fault_at_remote_bus")  # returns at once
job_result = handle.result(timeout=60)                # FaultResultDTO, or raises
job_state = handle.status().state
# --8<-- [end:jobs]

# --8<-- [start:wire]
wire = result.model_dump_json()                       # never NaN or Infinity
schemas = json_schemas()                              # JSON schema of every DTO
session.save("projekt.json")                          # library project file, without results
session.close()
# --8<-- [end:wire]

if __name__ == "__main__":
    print(f"project {summary.name!r}: {summary.n_buses} buses, {summary.n_branches} branch, "
          f"faults {summary.faults}, frequencies {summary.frequencies}")
    print(f"RMS: I_F = {rms.I_F_A:.1f} A, EPR at the fault = {rms.EPR_fault_V:.1f} V, "
          f"I_E = {rms.I_E_A:.1f} A, U_E = {rms.U_E_V:.1f} V")
    print("diagnostics:", [d.code for d in result.diagnostics])
    print(f"after the edit: revision {report.revision}, stale {stale_after_edit}; "
          f"after undo: revision {session.revision}, stale {stale_after_undo}")
    print(f"job: {job_state}, same figures: {job_result.key_figures == result.key_figures}")
    print(f"wire format: {type(result).__name__} as JSON, {len(schemas)} schemas")

# tests/test_audit_pass19_service_layer.py
"""
The service layer ``groundinsight.api`` (audit pass 19, GUI phase 0; builds on
ARC1, GUI1-GUI9).

``groundinsight.api`` is what a front end imports: the NiceGUI app of the
``groundinsight-gui`` distribution, the command-line interface, a later REST
server. Its design rules, and the tests below that hold them:

* **No UI toolkit, no module state** (rule R8, multi-user): the package imports
  no UI or web framework, keeps no mutable module-level state, and two
  sessions in two threads share nothing.
* **DTOs are the only data shape towards a front end** (R3): pydantic models,
  strictly JSON-safe (a non-finite number becomes ``null``), each with a
  ``dto_schema_version`` and a JSON schema -- they are the request and response
  schemas of a future REST API.
* **Every number is the library's, checked two ways** (R9): the key figures of
  the documented examples equal the pinned ``res_fault``/``res_buses``/
  ``res_branches`` frames of an independently built twin solved with
  ``run_fault``, and agree with the independent reference solver of
  :mod:`tests._reference_mna`; the thread and the process backend give
  identical DTOs; tables round-trip through ``network_from_tables``.
* **Every edit is an undoable, atomic command that goes through the library**
  (R4): a rejected command leaves network, revision and history unchanged;
  undo restores the serialised network exactly; staleness of results comes
  from the library (``Network.is_result_stale``), so undo makes a result
  current again (R10).
* **The project file is the library's** (R7): UTF-8, atomic, schema-versioned,
  without results and without view state.
* **Long work runs off the caller's thread** (R5): the job runner forwards
  progress, cancels through ``should_cancel`` (``gi.StudyCancelled``), enforces
  the runtime limit and binds results to the revision they were computed from.
"""

from __future__ import annotations

import ast
import json
import math
import os
import pathlib
import threading
import time
import warnings
from typing import Any, Dict, List

import polars as pl
import pytest

import groundinsight as gi
from groundinsight.io.tables import TABLE_SCHEMAS
from groundinsight.models.core_models import (
    RES_BRANCHES_SCHEMA,
    RES_BUSES_SCHEMA,
    RES_FAULT_SCHEMA,
)
from tests import _reference_mna as ref

ROOT = pathlib.Path(__file__).resolve().parents[1]
API_DIR = ROOT / "src" / "groundinsight" / "api"

#: The documented examples and the fault each one solves.
EXAMPLE_FAULTS = {"quickstart": "fault_at_remote_bus", "minimal": "fault", "mv_ring": "F1"}


def _api():
    """Import the service layer (a missing package is the red state)."""
    import groundinsight.api as api

    return api


def _reject_constant(token: str):
    raise ValueError(f"non-RFC-8259 token {token}")


def _strict_json(text: str) -> Any:
    """``json.loads`` that rejects ``NaN``/``Infinity`` like a browser does."""
    return json.loads(text, parse_constant=_reject_constant)


def _finite(value: Any) -> Any:
    """A frame value as a DTO carries it: non-finite floats become ``None``."""
    if isinstance(value, float) and not math.isfinite(value):
        return None
    return value


def _frame_rows(frame: pl.DataFrame) -> List[Dict[str, Any]]:
    """The rows of a Polars frame with non-finite floats as ``None``."""
    return [{k: _finite(v) for k, v in row.items()} for row in frame.to_dicts()]


def _session(example: str = "quickstart", **options):
    """A session on a documented example network."""
    return _api().ProjectSession.from_example(example, **options)


def _input_json(network: gi.Network) -> str:
    """The serialised inputs of a network (no results, no derived paths)."""
    return network.model_dump_json(exclude={"results", "paths"})


@pytest.fixture
def quickstart():
    session = _session("quickstart")
    try:
        yield session
    finally:
        session.close()


# ===========================================================================
# R8 -- no UI toolkit, no module state; the public surface
# ===========================================================================

_UI_TOOLKITS = ("nicegui", "webview", "fastapi", "starlette", "PySide6", "flet", "panel")


def _imported_modules(path: pathlib.Path) -> List[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    names = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            names.append(node.module)
    return names


def test_service_layer_imports_no_ui_toolkit():
    modules = sorted(API_DIR.glob("*.py"))
    assert {p.name for p in modules} >= {
        "__init__.py", "schemas.py", "commands.py", "jobs.py", "session.py",
    }, "the service layer modules are missing"
    offenders = [
        f"{p.name}: {name}"
        for p in modules
        for name in _imported_modules(p)
        if name.split(".")[0] in _UI_TOOLKITS
    ]
    assert offenders == []


def test_service_layer_keeps_no_mutable_module_state():
    """Module level holds constants only: UPPER_CASE names, ``__all__``,
    ``logger``, type aliases -- and no ``global`` statement anywhere."""
    offenders = []
    for path in sorted(API_DIR.glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in tree.body:
            targets = []
            if isinstance(node, ast.Assign):
                targets = [t for t in node.targets if isinstance(t, ast.Name)]
            elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
                targets = [node.target]
            for target in targets:
                name = target.id
                if name.isupper() or name in ("__all__", "logger") or name[:1].isupper():
                    continue
                offenders.append(f"{path.name}: {name}")
        offenders.extend(
            f"{path.name}: global {', '.join(n.names)}"
            for n in ast.walk(tree)
            if isinstance(n, ast.Global)
        )
    assert offenders == []


def test_the_public_names_live_in_the_api_package_not_the_top_level():
    api = _api()
    for name in (
        "ProjectSession", "ProjectError", "MemoryStore", "DatabaseStore",
        "JobRunner", "JobHandle", "JobCancelled", "JobTimeout", "JobFailed",
        "Command", "CommandError", "parse_command", "parse_job_request",
        "UpdateBus", "SetFrequencies", "DTO_SCHEMA_VERSION", "json_schemas",
        "FaultResultDTO", "JobStatusDTO", "EXAMPLES",
    ):
        assert name in api.__all__, name
        assert hasattr(api, name), name
    assert all(hasattr(api, name) for name in api.__all__)
    assert "ProjectSession" not in gi.__all__ and not hasattr(gi, "ProjectSession")


#: What one module of the service layer shares with another without making it
#: public (the package's internal-API convention, ARC5).
SERVICE_INTERNAL_API = (
    ("groundinsight.api.schemas", "BaseDTO"),
    ("groundinsight.api.commands", "rejection_reason"),
)


@pytest.mark.parametrize("module, qualname", SERVICE_INTERNAL_API)
def test_the_internal_api_of_the_service_layer_is_marked_and_not_exported(module, qualname):
    import importlib
    import inspect

    api = _api()
    obj = importlib.import_module(module)
    for part in qualname.split("."):
        obj = getattr(obj, part)
    leaf = qualname.rsplit(".", 1)[-1]
    paragraphs = (inspect.getdoc(obj) or "").split("\n\n")
    assert paragraphs[1].startswith("Internal API: ")
    assert leaf not in api.__all__ and leaf not in gi.__all__ and not hasattr(gi, leaf)


def test_job_cancellation_is_the_library_exception():
    api = _api()
    assert issubclass(api.JobCancelled, gi.StudyCancelled)
    assert issubclass(api.JobTimeout, api.JobCancelled)


# ===========================================================================
# R3 -- DTOs: versioned, JSON-safe, with JSON schemas
# ===========================================================================


def test_every_dto_has_a_schema_version_and_a_json_schema():
    api = _api()
    schemas = api.json_schemas()
    assert isinstance(schemas, dict) and schemas
    dto_classes = [getattr(api, name) for name in api.__all__ if name.endswith("DTO")]
    assert len(dto_classes) >= 20
    for cls in dto_classes:
        assert cls.__name__ in schemas, cls.__name__
        field = cls.model_fields["dto_schema_version"]
        assert field.default == api.DTO_SCHEMA_VERSION
        assert "dto_schema_version" in schemas[cls.__name__]["properties"]
    # The request schemas of a future REST API: commands and job requests.
    assert {"Command", "JobRequest"} <= set(schemas)
    assert json.loads(json.dumps(schemas)) == schemas  # plain JSON documents


def _every_dto_instance(session) -> List[Any]:
    """One instance of (nearly) every DTO, produced by real operations."""
    api = _api()
    out: List[Any] = [session.summary(), session.tables(), session.check()]
    out.append(session.apply(api.UpdateBus(name="bus_fault", fields={"specific_earth_resistance": 150.0})))
    result = session.solve("fault_at_remote_bus")
    out += [result, result.key_figures, result.key_figures.rows[0], result.buses, result.buses.columns[0]]
    out += [d for d in result.diagnostics] + [e for d in result.diagnostics for e in d.elements]
    handle = session.submit(api.SweepRequestDTO(
        fault="fault_at_remote_bus",
        points=[api.SweepPointDTO(label="a", bus_rho={"bus_fault": 120.0}),
                api.SweepPointDTO(label="b", bus_rho={"bus_fault": 80.0})],
    ))
    out += [handle.result(timeout=60), handle.status(), handle.status().progress]
    out.append(session.limits)
    out.append(api.ComplexDTO(real=1.0, imag=-2.0))
    return out


def test_every_dto_round_trips_through_json(quickstart):
    api = _api()
    instances = _every_dto_instance(quickstart)
    seen = set()
    for dto in instances:
        text = dto.model_dump_json()
        _strict_json(text)
        assert type(dto).model_validate_json(text) == dto, type(dto).__name__
        seen.add(type(dto).__name__)
    assert len(seen) >= 15, sorted(seen)
    # The commands and job requests are request DTOs and round-trip too.
    for command in _command_catalogue():
        assert api.parse_command(_strict_json(command.model_dump_json())) == command
    request = api.SolveRequestDTO(fault="F1", phase_current_mode="paths")
    assert api.parse_job_request(_strict_json(request.model_dump_json())) == request


def test_non_finite_numbers_become_null():
    api = _api()
    row = api.KeyFigureRowDTO(
        fault="F", fault_bus="b", frequency_Hz=50.0, is_rms=False,
        I_F_A=float("inf"), EPR_fault_V=float("nan"), r_epr=-float("inf"),
        n_diagnostics=0,
    )
    assert row.I_F_A is None and row.EPR_fault_V is None and row.r_epr is None
    table = api.TableDTO(
        name="t",
        columns=[api.ColumnDTO(name="x", dtype="float")],
        rows=[{"x": float("inf")}, {"x": 1.5}],
    )
    assert [r["x"] for r in table.rows] == [None, 1.5]
    _strict_json(row.model_dump_json())
    _strict_json(table.model_dump_json())


def test_an_open_end_impedance_travels_as_null():
    """A ``"nan"`` formula is an infinite impedance (open end) in the library;
    the DTO carries ``null`` and the JSON stays strict."""
    api = _api()
    net = api.EXAMPLES["minimal"]()
    open_type = gi.BusType(name="open", system_type="MV", voltage_level=20.0, impedance_formula="nan")
    gi.create_bus(name="b_open", type=open_type, network=net)
    gi.create_branch(name="k_open", type=net.branches["b12"].type, from_bus="bus3",
                     to_bus="b_open", length=0.5, network=net)
    session = api.ProjectSession(net)
    try:
        result = session.solve("fault")
        _strict_json(result.model_dump_json())
        _strict_json(session.tables().model_dump_json())
        open_rows = [r for r in result.buses.rows if r["bus_name"] == "b_open"]
        assert open_rows and all(r["I_bus_A"] in (None, 0.0) for r in open_rows)
    finally:
        session.close()


def test_the_key_figure_row_mirrors_the_res_fault_schema():
    api = _api()
    fields = [n for n in api.KeyFigureRowDTO.model_fields if n != "dto_schema_version"]
    assert fields == list(RES_FAULT_SCHEMA)


# ===========================================================================
# Element tables: the schema of Network.to_tables
# ===========================================================================


@pytest.mark.parametrize("example", sorted(EXAMPLE_FAULTS))
def test_element_tables_have_the_to_tables_schema_and_rows(example):
    session = _session(example)
    try:
        dto = session.tables()
        frames = session.network.to_tables()
        assert list(dto.tables) == list(TABLE_SCHEMAS)
        for name, schema in TABLE_SCHEMAS.items():
            table = dto.tables[name]
            assert [c.name for c in table.columns] == list(schema)
            assert table.rows == _frame_rows(frames[name]), name
        # Second way: the model objects themselves.
        buses = {r["name"]: r for r in dto.tables["buses"].rows}
        for name, bus in session.network.buses.items():
            assert buses[name]["specific_earth_resistance"] == bus.specific_earth_resistance
            assert buses[name]["type"] == bus.type.name
            assert buses[name]["active"] is bus.active
    finally:
        session.close()


@pytest.mark.parametrize("example", sorted(EXAMPLE_FAULTS))
def test_element_tables_rebuild_the_network(example):
    """Tables out of the DTO, through JSON, back into ``network_from_tables``."""
    session = _session(example)
    try:
        wire = _strict_json(session.tables().model_dump_json())
        frames = {
            name: pl.DataFrame(table["rows"], schema=TABLE_SCHEMAS[name])
            if table["rows"] else pl.DataFrame(schema=TABLE_SCHEMAS[name])
            for name, table in wire["tables"].items()
        }
        rebuilt = gi.network_from_tables(frames)
        assert _input_json(rebuilt) == _input_json(session.network)
    finally:
        session.close()


def test_a_catalogue_type_without_users_is_listed_in_the_type_table(quickstart):
    api = _api()
    quickstart.apply(api.AddBusType(definition={
        "name": "spare_type", "system_type": "MV", "voltage_level": 20.0,
        "impedance_formula": "rho * 0.02",
    }))
    names = [r["name"] for r in quickstart.tables().tables["bus_types"].rows]
    assert names == ["SubstationBus", "spare_type"]


# ===========================================================================
# R9 -- golden key figures of the documented examples, two ways
# ===========================================================================


def _solved_twin(example: str) -> gi.Network:
    """The example built independently and solved with ``run_fault``."""
    api = _api()
    twin = api.EXAMPLES[example]()
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        gi.run_fault(twin, EXAMPLE_FAULTS[example])
    return twin


def _wire_result(example: str):
    """The fault result of a job, after a trip through JSON."""
    api = _api()
    session = _session(example)
    try:
        handle = session.submit_solve(EXAMPLE_FAULTS[example])
        dto = handle.result(timeout=120)
    finally:
        session.close()
    return api.FaultResultDTO.model_validate_json(dto.model_dump_json())


@pytest.mark.parametrize("example", sorted(EXAMPLE_FAULTS))
def test_key_figures_equal_the_library_frames(example):
    """Way 1: the pinned frames of a twin solved with ``run_fault`` -- exact."""
    fault = EXAMPLE_FAULTS[example]
    dto = _wire_result(example)
    twin = _solved_twin(example)
    rows = [{k: v for k, v in r.model_dump().items() if k != "dto_schema_version"}
            for r in dto.key_figures.rows]
    assert rows == _frame_rows(twin.res_fault(fault))
    assert dto.buses.rows == _frame_rows(twin.res_buses(fault))
    assert dto.branches.rows == _frame_rows(twin.res_branches(fault))
    assert [c.name for c in dto.buses.columns] == list(RES_BUSES_SCHEMA)
    assert [c.name for c in dto.branches.columns] == list(RES_BRANCHES_SCHEMA)
    assert dto.fault == fault and dto.fault_bus == twin.faults[fault].bus
    assert dto.input_digest == twin.results[fault].input_digest
    library = [(d.code, d.severity, [tuple(e) for e in d.elements])
               for d in twin.results[fault].diagnostics]
    assert [(d.code, d.severity, [(e.kind, e.name) for e in d.elements])
            for d in dto.diagnostics] == library


@pytest.mark.parametrize("example", sorted(EXAMPLE_FAULTS))
def test_key_figures_agree_with_the_reference_solver(example):
    """Way 2: the independent modified-nodal-analysis solver of the tests."""
    tol = 1e-10
    fault = EXAMPLE_FAULTS[example]
    dto = _wire_result(example)
    network = _api().EXAMPLES[example]()
    per_frequency = {r.frequency_Hz: r for r in dto.key_figures.rows if not r.is_rms}
    assert sorted(per_frequency) == sorted(network.frequencies)
    bus_epr = {(r["bus_name"], r["frequency_Hz"]): r["EPR_V"] for r in dto.buses.rows}
    for f, row in per_frequency.items():
        rd = ref.reference_network(network, f, fault)
        solution = ref.solve_oneway(rd, thevenin_phase="norton")
        mapped = ref.to_package_frame(solution)
        fault_bus = rd["fault_bus"]
        assert row.I_F_A == pytest.approx(abs(solution["I_F"]), rel=tol)
        assert row.EPR_fault_V == pytest.approx(abs(mapped["u"][fault_bus]), rel=tol)
        # The quickstart line is symmetric: both buses rise to the same EPR
        # (equal to the last bits), so the reported bus is checked to be *a*
        # maximiser rather than the one the reference happens to pick.
        top_value = max(abs(u) for u in mapped["u"].values())
        assert row.EPR_max_V == pytest.approx(top_value, rel=tol)
        assert abs(mapped["u"][row.EPR_max_bus]) == pytest.approx(top_value, rel=tol)
        assert row.r_epr == pytest.approx(ref.reduction_factor_epr(rd, thevenin_phase="norton"), rel=tol)
        i_earth, _ = ref.half_plane_split(mapped["ia"], fault_bus)
        assert row.I_E_A == pytest.approx(abs(i_earth), rel=tol)
        assert row.r_current == pytest.approx(abs(i_earth) / abs(solution["I_F"]), rel=tol)
        assert row.U_E_V == pytest.approx(abs(mapped["u"][fault_bus]), rel=tol)
        assert row.Z_E_Ohm == pytest.approx(abs(ref.driving_point_impedance(rd, fault_bus)), rel=tol)
        for bus, u in mapped["u"].items():
            assert bus_epr[(bus, f)] == pytest.approx(abs(u), rel=tol), (bus, f)


# ===========================================================================
# R7 -- the project file
# ===========================================================================


def test_save_writes_a_utf8_project_file_without_results(tmp_path, quickstart):
    api = _api()
    quickstart.apply(api.AddBus(name="Umspannwerk Süd Ω", type="SubstationBus"))
    quickstart.solve("fault_at_remote_bus")
    assert quickstart.network.results  # the session keeps its results ...
    path = tmp_path / "projekt.json"
    location = quickstart.save(str(path))
    raw = path.read_bytes()
    payload = _strict_json(raw.decode("utf-8"))
    assert "Süd Ω".encode("utf-8") in raw
    assert payload["results"] == {}  # ... the file does not
    assert payload["schema_version"] >= 1 and payload["groundinsight_version"] == gi.__version__
    assert quickstart.network.results  # saving did not drop them either
    assert location == str(path) and not quickstart.dirty
    # The library opens a GUI project unchanged; the session reopens it.
    assert "Umspannwerk Süd Ω" in gi.load_network_from_json(path).buses
    reopened = api.ProjectSession.from_file(str(path))
    try:
        assert _input_json(reopened.network) == _input_json(quickstart.network)
        assert reopened.tables() == quickstart.tables().model_copy(update={"revision": 0})
        assert not reopened.dirty and reopened.location == str(path)
    finally:
        reopened.close()


def test_a_failed_save_keeps_the_previous_file(tmp_path, monkeypatch, quickstart):
    api = _api()
    path = tmp_path / "p.json"
    quickstart.save(str(path))
    original = path.read_bytes()
    quickstart.apply(api.UpdateBranch(name="cable_1", fields={"length": 9.0}))

    def broken_replace(src, dst):
        raise OSError("disk full")

    monkeypatch.setattr(os, "replace", broken_replace)
    with pytest.raises(api.ProjectError, match="disk full"):
        quickstart.save()
    assert path.read_bytes() == original
    assert sorted(p.name for p in tmp_path.iterdir()) == ["p.json"]
    assert quickstart.dirty


def test_open_rejects_what_is_not_a_project(tmp_path):
    api = _api()
    bad = tmp_path / "bad.json"
    bad.write_text('{"name": "x"}', encoding="utf-8")
    with pytest.raises(api.ProjectError):
        api.ProjectSession.from_file(str(bad))
    with pytest.raises(api.ProjectError):
        api.ProjectSession.from_file(str(tmp_path / "missing.json"))


def test_open_enforces_the_size_limits(tmp_path):
    api = _api()
    path = tmp_path / "ring.json"
    gi.save_network_to_json(api.EXAMPLES["mv_ring"](), path)
    with pytest.raises(api.ProjectError, match="max_buses"):
        api.ProjectSession.from_file(str(path), max_buses=10)
    with pytest.raises(api.ProjectError, match="max_branches"):
        api.ProjectSession.from_file(str(path), max_branches=5)
    api.ProjectSession.from_file(str(path), max_buses=20, max_branches=20).close()


def test_memory_store_round_trip(quickstart):
    api = _api()
    store = api.MemoryStore()
    quickstart.solve("fault_at_remote_bus")
    quickstart.save_to(store, "team/project-1")
    assert store.keys() == ["team/project-1"]
    other = api.ProjectSession.from_store(store, "team/project-1")
    try:
        assert other.network.results == {}
        assert _input_json(other.network) == _input_json(quickstart.network)
        assert other.network is not quickstart.network
        assert other.location == "memory:team/project-1"
    finally:
        other.close()
    with pytest.raises(api.ProjectError):
        api.ProjectSession.from_store(store, "no-such-project")


def test_database_store_round_trip(tmp_path, quickstart):
    api = _api()
    with gi.Database(tmp_path / "projects.db") as database:
        store = api.DatabaseStore(database)
        quickstart.solve("fault_at_remote_bus")
        quickstart.save_to(store, quickstart.network.name)
        quickstart.apply(api.UpdateBus(name="bus_fault", fields={"specific_earth_resistance": 300.0}))
        quickstart.save()  # back into the same store, overwriting
        reopened = api.ProjectSession.from_store(store, "QuickstartNet")
        try:
            assert reopened.network.buses["bus_fault"].specific_earth_resistance == 300.0
            assert reopened.network.results == {}
        finally:
            reopened.close()
        with pytest.raises(api.ProjectError, match="name"):
            quickstart.save_to(store, "another-name")


# ===========================================================================
# R4 -- commands
# ===========================================================================


def _command_catalogue() -> List[Any]:
    """A valid instance of every command, applicable in this order to the
    quickstart network."""
    api = _api()
    return [
        api.AddBusType(definition={
            "name": "Mast", "system_type": "Tower", "voltage_level": 20.0,
            "impedance_formula": "rho * 0.05 + j * f * 0.0002",
        }),
        api.AddBus(name="tower_1", type="Mast", specific_earth_resistance=250.0),
        api.UpdateBus(name="tower_1", fields={"specific_earth_resistance": 300.0, "description": "T1"}),
        api.AddBranchType(definition={
            "name": "OHL", "grounding_conductor": True,
            "self_impedance_formula": "(0.4 + j * f * 0.016) * l",
            "mutual_impedance_formula": "(0.05 + j * f * 0.006) * l",
        }),
        api.AddBranch(name="span_1", type="OHL", from_bus="bus_fault", to_bus="tower_1", length=0.3),
        api.UpdateBranch(name="span_1", fields={"length": 0.35, "active": False}),
        api.AddSource(name="second_infeed", bus="tower_1", values={50.0: {"real": 100.0, "imag": -20.0}}),
        api.UpdateSource(name="second_infeed", fields={"values": {50.0: 120.0, 250.0: 10.0}}),
        api.AddFault(name="F_tower", bus="tower_1", scalings={50.0: 1.0}),
        api.UpdateFault(name="F_tower", fields={"t_k_s": 0.5, "scalings": {}}),
        api.UpdateBusType(name="Mast", fields={"impedance_formula": "rho * 0.04 + j * f * 0.0002"}),
        api.UpdateBranchType(name="OHL", fields={"description": "overhead line"}),
        api.SetFrequencies(frequencies=[50.0, 250.0, 350.0]),
        api.RemoveFault(name="F_tower"),
        api.RemoveSource(name="second_infeed"),
        api.RemoveBranch(name="span_1"),
        api.RemoveBranchType(name="OHL"),
        api.RemoveBus(name="tower_1"),
        api.RemoveBusType(name="Mast"),
    ]


def test_every_command_applies_and_undo_redo_restore_the_network_exactly():
    _api()  # the service layer exists
    session = _session("quickstart")
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            states = [_input_json(session.network)]
            tables = [session.tables().tables]
            for command in _command_catalogue():
                report = session.apply(command)
                assert report.op == command.op and report.revision == session.revision
                states.append(_input_json(session.network))
                tables.append(session.tables().tables)
                # Catalogue-only commands (a type nobody uses yet) change the
                # tables, not the network; every command changes the tables.
                assert tables[-1] != tables[-2], command.op
            assert session.history() == [c.label for c in _command_catalogue()]
            for k in range(len(_command_catalogue()), 0, -1):
                session.undo()
                assert _input_json(session.network) == states[k - 1], k
                assert session.tables().tables == tables[k - 1], k
            assert not session.can_undo and session.revision == 0
            for k in range(1, len(_command_catalogue()) + 1):
                session.redo()
                assert _input_json(session.network) == states[k], k
            assert not session.can_redo
    finally:
        session.close()


def test_update_bus_derives_like_a_fresh_build(quickstart):
    """Second way: the same network built from scratch with the new value."""
    api = _api()
    quickstart.apply(api.UpdateBus(name="bus_fault", fields={"specific_earth_resistance": 250.0}))
    fresh = api.EXAMPLES["quickstart"]()
    fresh_bus = gi.create_bus(name="x", type=fresh.buses["bus_fault"].type,
                              specific_earth_resistance=250.0, network=fresh)
    assert quickstart.network.buses["bus_fault"].impedance == fresh_bus.impedance
    assert quickstart.network.buses["bus_fault"].impedance[50.0].real == pytest.approx(2.5)


def _rejected_commands() -> List[Any]:
    api = _api()
    return [
        api.UpdateBus(name="bus_fault", fields={"specific_earth_resistance": -500.0}),  # Re(Z) < 0
        api.UpdateBus(name="bus_fault", fields={"impedance": {}}),  # derived field
        api.UpdateBus(name="nope", fields={"active": False}),
        api.UpdateBus(name="bus_fault", fields={"type": "NoSuchType"}),
        api.UpdateBranch(name="cable_1", fields={"to_bus": "nowhere"}),
        api.UpdateBranch(name="cable_1", fields={"length": 2.0, "lenght": 3.0}),  # all or nothing
        api.AddBus(name="bus_fault", type="SubstationBus"),  # duplicate
        api.AddBus(name="new", type="NoSuchType"),
        api.AddBranch(name="k2", type="MSCable", from_bus="bus_source", to_bus="ghost", length=1.0),
        api.AddSource(name="s2", bus="ghost", values={50.0: 1.0}),
        api.AddSource(name="s3", bus="bus_fault", source_type="voltage", values={50.0: 1.0}),
        api.UpdateSource(name="substation_infeed", fields={"source_type": "voltage"}),
        api.AddFault(name="F2", bus="ghost"),
        api.UpdateFault(name="fault_at_remote_bus", fields={"t_k_s": -1.0}),
        api.RemoveBus(name="bus_fault"),  # still referenced
        api.RemoveBranch(name="ghost"),
        api.RemoveBusType(name="SubstationBus"),  # still in use
        api.AddBusType(definition={"name": "SubstationBus", "system_type": "x",
                                   "voltage_level": 1.0, "impedance_formula": "1"}),
        api.AddBusType(definition={"name": "evil", "system_type": "x", "voltage_level": 1.0,
                                   "impedance_formula": "__import__('os')"}),
        api.UpdateBusType(name="SubstationBus", fields={"impedance_formula": "-rho * 0.01"}),
        api.UpdateBranchType(name="MSCable", fields={"name": "SubstationCable",
                                                     "self_impedance_formula": "x * l"}),
        api.SetFrequencies(frequencies=[50.0, 50.0]),
        api.SetFrequencies(frequencies=[]),
    ]


@pytest.mark.parametrize("index", range(23))
def test_a_rejected_command_leaves_everything_unchanged(index, quickstart):
    api = _api()
    commands = _rejected_commands()
    assert len(commands) == 23
    command = commands[index]
    quickstart.apply(api.UpdateBranch(name="cable_1", fields={"length": 4.0}))
    quickstart.solve("fault_at_remote_bus")
    before = (quickstart.network.model_dump_json(), quickstart.revision,
              quickstart.history(), quickstart.can_redo, quickstart.tables())
    with pytest.raises(api.CommandError):
        quickstart.apply(command)
    after = (quickstart.network.model_dump_json(), quickstart.revision,
             quickstart.history(), quickstart.can_redo, quickstart.tables())
    assert after == before


def test_a_rejected_command_says_why_in_the_words_of_the_library(quickstart):
    """A front end shows the reason verbatim, so it carries the library's
    message without pydantic's report frame ("1 validation error for ...",
    the input echo, the documentation link). Second way: the error the
    library raises for the same change on a twin."""
    api = _api()
    with pytest.raises(api.CommandError) as caught:
        quickstart.apply(api.UpdateBranch(name="cable_1", fields={"length": -1.0}))
    message = str(caught.value)
    twin = api.EXAMPLES["quickstart"]()
    with pytest.raises(ValueError) as direct:
        twin.update_branch("cable_1", length=-1.0)
    reasons = [error["msg"].removeprefix("Value error, ") for error in direct.value.errors()]
    assert reasons and all(reason in message for reason in reasons)
    assert message.startswith("update branch 'cable_1': length=-1.0: ")
    for frame in ("validation error for", "errors.pydantic.dev", "input_value", "Value error, "):
        assert frame not in message, frame
    # The same for every rejection of the catalogue and for a malformed command.
    for command in _rejected_commands():
        with pytest.raises(api.CommandError) as caught:
            quickstart.apply(command)
        assert "errors.pydantic.dev" not in str(caught.value), command.label
    with pytest.raises(api.CommandError) as caught:
        api.parse_command({"op": "update_bus", "name": "b", "fields": "not a mapping"})
    assert str(caught.value).startswith("invalid 'update_bus' command: ")
    assert "errors.pydantic.dev" not in str(caught.value)
    assert "fields" in str(caught.value)


def test_a_project_file_that_does_not_validate_says_where(tmp_path):
    """Opening reports the library's reason and the element it concerns.
    Second way: the library's own error for the same file."""
    api = _api()
    path = tmp_path / "broken.json"
    gi.save_network_to_json(api.EXAMPLES["quickstart"](), path)
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["branches"]["cable_1"]["length"] = -1.0
    path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(api.ProjectError) as caught:
        api.ProjectSession.from_file(str(path))
    message = str(caught.value)
    with pytest.raises(ValueError) as direct:
        gi.load_network_from_json(path)
    errors = direct.value.errors()
    assert errors
    for error in errors:
        assert error["msg"].removeprefix("Value error, ") in message
        assert ".".join(str(part) for part in error["loc"]) in message  # branches.cable_1.length
    assert message.startswith(f"cannot open '{path}': ")
    assert "errors.pydantic.dev" not in message and "validation error for" not in message


def test_a_type_update_swaps_every_user_atomically():
    api = _api()
    session = _session("mv_ring")
    try:
        old_types = {name: bus.type for name, bus in session.network.buses.items()}
        session.apply(api.UpdateBusType(name="MV_substation",
                                        fields={"impedance_formula": "rho * 0 + 3.0"}))
        assert {bus.impedance[50.0].real for bus in session.network.buses.values()} == {3.0}
        new_type = session.network.buses["b01"].type
        assert all(bus.type is new_type for bus in session.network.buses.values())
        session.undo()
        assert all(session.network.buses[n].type is t for n, t in old_types.items())
        # A formula that fails for one user fails for all: nothing changes.
        before = session.network.model_dump_json()
        session.apply(api.UpdateBus(name="b07", fields={"specific_earth_resistance": 5000.0}))
        with pytest.raises(api.CommandError):
            session.apply(api.UpdateBusType(name="MV_substation",
                                            fields={"impedance_formula": "1000 - rho"}))
        session.undo()
        assert session.network.model_dump_json() == before
    finally:
        session.close()


def test_renaming_a_type_moves_its_users(quickstart):
    api = _api()
    quickstart.apply(api.UpdateBusType(name="SubstationBus", fields={"name": "Station"}))
    assert {b.type.name for b in quickstart.network.buses.values()} == {"Station"}
    assert [r["name"] for r in quickstart.tables().tables["bus_types"].rows] == ["Station"]
    quickstart.undo()
    assert {b.type.name for b in quickstart.network.buses.values()} == {"SubstationBus"}


def test_set_frequencies_rederives_every_element(quickstart):
    api = _api()
    report = quickstart.apply(api.SetFrequencies(frequencies=[50.0, 150.0]))
    assert report.revision == 1
    for bus in quickstart.network.buses.values():
        assert sorted(bus.impedance) == [50.0, 150.0]
    fresh = gi.create_network(name="f", frequencies=[50.0, 150.0])
    bus = gi.create_bus(name="b", type=quickstart.network.buses["bus_fault"].type, network=fresh)
    assert quickstart.network.buses["bus_fault"].impedance == bus.impedance


def test_size_limits_reject_an_element_too_many():
    api = _api()
    session = _session("quickstart", max_buses=3, max_branches=1)
    try:
        session.apply(api.AddBus(name="third", type="SubstationBus"))
        before = session.network.model_dump_json()
        with pytest.raises(api.CommandError, match="max_buses"):
            session.apply(api.AddBus(name="fourth", type="SubstationBus"))
        with pytest.raises(api.CommandError, match="max_branches"):
            session.apply(api.AddBranch(name="k2", type="MSCable", from_bus="bus_fault",
                                        to_bus="third", length=1.0))
        assert session.network.model_dump_json() == before and session.revision == 1
        assert session.limits.max_buses == 3 and session.summary().limits.max_branches == 1
    finally:
        session.close()


def test_a_new_command_after_undo_drops_redo_and_gets_a_fresh_revision(quickstart):
    api = _api()
    quickstart.apply(api.UpdateBranch(name="cable_1", fields={"length": 6.0}))
    first = quickstart.revision
    quickstart.undo()
    assert quickstart.revision == 0 and quickstart.can_redo
    quickstart.apply(api.UpdateBranch(name="cable_1", fields={"length": 7.0}))
    assert not quickstart.can_redo and quickstart.revision not in (0, first)


def test_the_undo_history_is_bounded():
    api = _api()
    session = _session("quickstart", max_undo=3)
    try:
        for k in range(5):
            session.apply(api.UpdateBranch(name="cable_1", fields={"length": 1.0 + k}))
        assert len(session.history()) == 3
        while session.can_undo:
            session.undo()
        assert session.network.branches["cable_1"].length == 2.0
    finally:
        session.close()


def test_commands_parse_from_json_and_name_unknown_operations():
    api = _api()
    command = api.parse_command({"op": "update_bus", "name": "b1", "fields": {"active": False}})
    assert isinstance(command, api.UpdateBus) and command.fields == {"active": False}
    with pytest.raises(api.CommandError, match="op"):
        api.parse_command({"op": "format_disk"})
    assert {c.op for c in _command_catalogue()} == set(api.COMMAND_OPS)


def test_command_diagnostics_are_returned_with_the_command(quickstart):
    api = _api()
    report = quickstart.apply(api.SetFrequencies(frequencies=[250.0, 50.0, 350.0, 450.0, 550.0]))
    assert "FREQUENCY_ORDER" in {d.code for d in report.diagnostics}
    report = quickstart.apply(api.AddSource(name="s2", bus="bus_fault", values={60.0: 5.0, 50.0: 1.0}))
    outside = [d for d in report.diagnostics if d.code == "SOURCE_FREQUENCY_OUTSIDE_NETWORK"]
    assert outside and [(e.kind, e.name) for e in outside[0].elements] == [("source", "s2")]


def test_the_session_applies_a_copy_of_the_command():
    """One command object applied to two sessions: each keeps its own undo."""
    api = _api()
    command = api.UpdateBus(name="bus_fault", fields={"specific_earth_resistance": 222.0})
    first, second = _session("quickstart"), _session("quickstart")
    try:
        first.apply(command)
        second.apply(command)
        first.undo()
        assert first.network.buses["bus_fault"].specific_earth_resistance == 100.0
        assert second.network.buses["bus_fault"].specific_earth_resistance == 222.0
    finally:
        first.close()
        second.close()


# ===========================================================================
# R10 -- staleness from the library
# ===========================================================================


def test_staleness_comes_from_the_library_and_undo_makes_a_result_current(quickstart):
    api = _api()
    fault = "fault_at_remote_bus"
    assert quickstart.result(fault) is None
    result = quickstart.solve(fault)
    assert result.stale is False and quickstart.result(fault).stale is False
    quickstart.apply(api.UpdateBus(name="bus_fault", fields={"specific_earth_resistance": 300.0}))
    assert quickstart.is_stale(fault) is quickstart.network.is_result_stale(fault) is True
    assert quickstart.result(fault).stale and quickstart.summary().stale_results == [fault]
    quickstart.undo()
    assert quickstart.network.is_result_stale(fault) is False
    assert quickstart.result(fault).stale is False
    # An edit that does not touch the inputs of the result leaves it current.
    quickstart.apply(api.UpdateBus(name="bus_fault", fields={"description": "renamed in text"}))
    assert not quickstart.is_stale(fault)


def test_remove_fault_and_undo_restore_the_stored_result(quickstart):
    api = _api()
    fault = "fault_at_remote_bus"
    quickstart.solve(fault)
    stored = quickstart.network.results[fault]
    quickstart.apply(api.RemoveFault(name=fault))
    assert fault not in quickstart.network.results and quickstart.result(fault) is None
    quickstart.undo()
    assert quickstart.network.results[fault] is stored
    assert quickstart.result(fault).stale is False


def test_dirty_follows_the_saved_revision(tmp_path, quickstart):
    api = _api()
    assert not quickstart.dirty
    quickstart.save(str(tmp_path / "p.json"))
    quickstart.apply(api.UpdateBus(name="bus_fault", fields={"active": True, "description": "x"}))
    assert quickstart.dirty
    quickstart.undo()
    assert not quickstart.dirty
    quickstart.redo()
    assert quickstart.dirty


# ===========================================================================
# Pre-flight check
# ===========================================================================


def test_check_reports_every_problem_like_the_library(quickstart):
    api = _api()
    assert quickstart.check().n_errors == 0
    quickstart.apply(api.RemoveSource(name="substation_infeed"))
    quickstart.apply(api.AddBus(name="lonely", type="SubstationBus"))
    report = quickstart.check()
    library = gi.check_network(quickstart.network)
    assert [(d.code, d.severity, [(e.kind, e.name) for e in d.elements]) for d in report.diagnostics] == [
        (d.code, d.severity, [tuple(e) for e in d.elements]) for d in library
    ]
    codes = {d.code for d in report.diagnostics}
    assert {"NO_SOURCE", "ISOLATED_BUS"} <= codes
    assert report.n_errors == sum(d.severity == "error" for d in library) >= 1
    assert report.ok is False


# ===========================================================================
# R5 -- jobs
# ===========================================================================


def _wait_until(predicate, timeout: float = 60.0) -> None:
    deadline = time.monotonic() + timeout
    while not predicate():
        if time.monotonic() > deadline:
            raise AssertionError("condition not reached in time")
        time.sleep(0.01)


def _sweep_request(n_points: int, fault: str = "F1"):
    api = _api()
    return api.SweepRequestDTO(
        fault=fault,
        points=[api.SweepPointDTO(label=f"p{k}", bus_rho={"b05": 50.0 + k}) for k in range(n_points)],
    )


def test_a_solve_job_reports_progress_and_binds_its_result(quickstart):
    _api()  # the service layer exists
    handle = quickstart.submit_solve("fault_at_remote_bus")
    dto = handle.result(timeout=60)
    status = handle.status()
    assert status.state == "done" and status.task == "solve"
    assert (status.progress.done, status.progress.total) == (1, 1)
    assert dto.revision == 0 == status.revision and dto.stale is False
    assert quickstart.result("fault_at_remote_bus") == dto.model_copy(update={"duration_s": None})
    assert quickstart.network.results["fault_at_remote_bus"].input_digest == dto.input_digest


def test_thread_and_process_backends_give_identical_results():
    """R9 again: the same job in a worker thread and in a spawned process."""
    api = _api()
    results = {}
    for backend in ("thread", "process"):
        runner = api.JobRunner(backend=backend)
        session = _session("mv_ring", runner=runner)
        try:
            results[backend] = session.submit_solve("F1").result(timeout=180)
        finally:
            session.close()
            runner.shutdown()
    thread, process = (results[b].model_dump(exclude={"duration_s"}) for b in ("thread", "process"))
    assert thread == process


def test_cancel_stops_a_study_and_leaves_the_session_unchanged():
    api = _api()
    session = _session("mv_ring")
    try:
        before = session.network.model_dump_json()
        handle = session.submit(_sweep_request(400))
        _wait_until(lambda: handle.status().progress.done >= 2)
        assert handle.cancel() is True
        with pytest.raises(api.JobCancelled):
            handle.result(timeout=60)
        status = handle.status()
        assert status.state == "cancelled" and status.progress.done < 400
        assert session.network.model_dump_json() == before and session.revision == 0
    finally:
        session.close()


def test_a_queued_job_can_be_cancelled_before_it_starts():
    api = _api()
    session = _session("mv_ring")
    try:
        running = session.submit(_sweep_request(400))
        queued = session.submit_solve("F1")
        assert queued.status().state == "queued"
        assert queued.cancel() is True
        assert queued.status().state == "cancelled"
        running.cancel()
        with pytest.raises(api.JobCancelled):
            queued.result(timeout=60)
    finally:
        session.close()


def test_the_runtime_limit_stops_a_thread_job_at_a_unit_boundary():
    api = _api()
    session = _session("mv_ring", max_job_runtime_s=0.3)
    try:
        handle = session.submit(_sweep_request(1000))
        with pytest.raises(api.JobTimeout):
            handle.result(timeout=60)
        status = handle.status()
        assert status.state == "timeout" and status.progress.done < 1000
        assert status.elapsed_s < 10.0
    finally:
        session.close()


def test_the_runtime_limit_terminates_a_process_job():
    api = _api()
    runner = api.JobRunner(backend="process", cancel_grace_s=0.5)
    session = _session("mv_ring", runner=runner, max_job_runtime_s=1.0)
    try:
        start = time.monotonic()
        handle = session.submit(_sweep_request(5000))
        with pytest.raises(api.JobTimeout):
            handle.result(timeout=120)
        assert handle.status().state == "timeout"
        assert time.monotonic() - start < 30.0
    finally:
        session.close()
        runner.shutdown()


def test_a_failing_job_reports_its_error_as_data(quickstart):
    api = _api()
    handle = quickstart.submit_solve("no_such_fault")
    with pytest.raises(api.JobFailed) as caught:
        handle.result(timeout=60)
    status = handle.status()
    assert status.state == "failed"
    assert status.error.type == "ValueError" and "no_such_fault" in status.error.message
    assert isinstance(caught.value.__cause__, ValueError)
    _strict_json(status.model_dump_json())


def test_results_bind_to_the_revision_they_were_computed_from():
    """The edits change a cable length: the ring's station impedances do not
    depend on rho (``rho * 0`` in the documented formula), so a rho edit would
    leave every result current -- which the library would rightly report."""
    api = _api()
    session = _session("mv_ring")
    try:
        session.apply(api.UpdateBranch(name="L_05_06", fields={"length": 0.6}))
        blocker = session.submit(_sweep_request(30))  # occupies the single worker
        late = session.submit_solve("F1")  # snapshot taken now, at revision 1
        session.apply(api.UpdateBranch(name="L_05_06", fields={"length": 0.7}))
        blocker.result(timeout=120)
        late_dto = late.result(timeout=120)
        assert late_dto.revision == 1 and late_dto.stale is True
        assert session.is_stale("F1")  # bound, and the library calls it stale
        current = session.solve("F1")  # computed at revision 2
        assert current.revision == 2 and current.stale is False
        # A job computed on an older state that finishes later does not
        # replace a current result.
        session.undo()  # revision 1 again ...
        blocker = session.submit(_sweep_request(30))
        older = session.submit_solve("F1")  # ... snapshot at revision 1
        session.redo()  # ... while the network returns to revision 2
        blocker.result(timeout=120)
        assert older.result(timeout=120).stale is True
        assert session.result("F1").revision == 2 and not session.is_stale("F1")
    finally:
        session.close()


def test_study_jobs_return_what_the_library_computes():
    """Sweep, outage study and rho search through the runner, against the same
    library call made directly on a copy of the network."""
    api = _api()
    session = _session("mv_ring")
    network = api.EXAMPLES["mv_ring"]()
    try:
        sweep_dto = session.submit(_sweep_request(3)).result(timeout=120)
        direct = gi.run_sweep(
            network, fault="F1",
            points=[gi.SweepPoint(label=f"p{k}", bus_rho={"b05": 50.0 + k}) for k in range(3)],
        )
        assert sweep_dto.tables["buses"].rows == _frame_rows(direct.buses())
        assert sweep_dto.tables["impedances"].rows == _frame_rows(direct.impedances())

        outage_dto = session.submit(api.OutageStudyRequestDTO(
            fault="F1",
            scenarios=[api.OutageScenarioDTO(name="L05 out", disabled_branches=["L_05_06"])],
        )).result(timeout=120)
        study = gi.run_outage_study(
            network, fault="F1", scenarios=[gi.Outage(name="L05 out", disabled_branches=["L_05_06"])],
        )
        assert outage_dto.tables["buses"].rows == _frame_rows(study.compare_buses())

        for dto in (sweep_dto, outage_dto):
            _strict_json(dto.model_dump_json())
    finally:
        session.close()
    # The rho search needs a rho-dependent electrode: the quickstart network.
    quickstart = _session("quickstart")
    try:
        buses = ["bus_fault", "bus_source"]
        rho_dto = quickstart.submit(api.MaxRhoRequestDTO(
            fault="fault_at_remote_bus", bus_names=buses, u_limit=400.0,
        )).result(timeout=120)
        direct_rho = gi.find_max_rho_scaling(
            api.EXAMPLES["quickstart"](), "fault_at_remote_bus", buses, 400.0,
        )
        assert direct_rho["status"] == "converged" == rho_dto.values["status"]
        assert rho_dto.values["c_max"] == direct_rho["c_max"]  # same code, same input: exact
        assert rho_dto.values["iterations"] == direct_rho["iterations"]
        assert {r["bus_name"]: r["rho_max"] for r in rho_dto.tables["rho_max"].rows} == direct_rho["rho_max"]
        _strict_json(rho_dto.model_dump_json())
    finally:
        quickstart.close()


def test_two_sessions_in_two_threads_share_no_state():
    api = _api()
    sessions = {"a": _session("quickstart"), "b": _session("quickstart")}
    values = {"a": [100.0 + k for k in range(25)], "b": [500.0 + k for k in range(25)]}
    errors: List[str] = []

    def work(key: str) -> None:
        session = sessions[key]
        try:
            for value in values[key]:
                session.apply(api.UpdateBus(name="bus_fault", fields={"specific_earth_resistance": value}))
                session.undo()
                session.redo()
            session.submit_solve("fault_at_remote_bus").result(timeout=120)
            session.solve("fault_at_remote_bus")
        except Exception as exc:  # pragma: no cover - reported below
            errors.append(f"{key}: {type(exc).__name__}: {exc}")

    threads = [threading.Thread(target=work, args=(k,)) for k in sessions]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    try:
        assert errors == []
        for key, session in sessions.items():
            assert session.network.buses["bus_fault"].specific_earth_resistance == values[key][-1]
            assert session.history() == [
                api.UpdateBus(name="bus_fault", fields={"specific_earth_resistance": v}).label
                for v in values[key]
            ]
            assert session.revision == 25 and not session.is_stale("fault_at_remote_bus")
        a, b = (sessions[k].result("fault_at_remote_bus") for k in ("a", "b"))
        assert a.input_digest != b.input_digest
        assert sessions["a"].network is not sessions["b"].network
        assert sessions["a"].runner is not sessions["b"].runner
    finally:
        for session in sessions.values():
            session.close()


def test_job_status_is_json_and_reports_the_backend(quickstart):
    api = _api()
    handle = quickstart.submit_solve("fault_at_remote_bus")
    handle.wait(timeout=60)
    wire = _strict_json(handle.status().model_dump_json())
    assert wire["backend"] == "thread" and wire["state"] == "done"
    assert api.JobStatusDTO.model_validate(wire) == handle.status()


def test_closing_a_session_cancels_only_its_own_jobs_on_a_shared_runner():
    """A server shares one runner between sessions; closing one user's
    session must not touch another user's job."""
    api = _api()
    runner = api.JobRunner(max_workers=2)
    first, second = _session("mv_ring", runner=runner), _session("mv_ring", runner=runner)
    try:
        mine = first.submit(_sweep_request(400))
        theirs = second.submit(_sweep_request(5))
        _wait_until(lambda: mine.status().progress.done >= 1)
        first.close()
        with pytest.raises(api.JobCancelled):
            mine.result(timeout=60)
        assert theirs.result(timeout=60).values["points"] == 5
        assert runner.get(theirs.job_id).status().state == "done"
    finally:
        second.close()
        runner.shutdown()

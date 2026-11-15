# tests/test_api_diagram_editing.py
"""
The service layer edits network and diagram together.

Covered here: the automatic layout of the library (``Diagram.with_layout``),
the rename commands (network and diagram in one step), the diagram commands
(``PlaceBus``, ``MoveNodes``, ``RouteBranch``, ``UpdateDiagramNode``,
``AutoLayout``), the session's project file with diagram and views, ``~`` and
environment variables in paths, and DTO schema version 2.

Every command is checked the strict way of the service tests: after undo the
serialised network *and* the diagram equal the state before, redo gives the
state after, and a rejected command leaves network, diagram, revision and
history unchanged. A rename is checked a second way: the renamed network
solves to the same numbers as the original.
"""

import json
import math
import os
import warnings
from collections import deque
from typing import Any, Dict, List

import pytest

import groundinsight as gi
import groundinsight.api as api
from tests._characteristic_cases import FREQS, _network
from tests._warning_policy import acknowledge

#: The quickstart fault scales 50 Hz only.
pytestmark = acknowledge("SCALINGS_INCOMPLETE", "PHASE_PROXY")


def _session(network=None, **options) -> api.ProjectSession:
    return api.ProjectSession(network if network is not None else _network(), **options)


def _state(session: api.ProjectSession):
    """Inputs of network and diagram as JSON (no results, no paths)."""
    return (session.network.model_dump_json(exclude={"results", "paths"}),
            session.diagram_model.model_dump_json())


# --- the automatic layout ---------------------------------------------------


def _bfs_layers(network: gi.Network, root: str) -> Dict[str, int]:
    """Second way: graph distance from the root, independent of the layout code."""
    neighbours: Dict[str, List[str]] = {name: [] for name in network.buses}
    for branch in network.branches.values():
        neighbours[branch.from_bus].append(branch.to_bus)
        neighbours[branch.to_bus].append(branch.from_bus)
    layer = {root: 0}
    queue = deque([root])
    while queue:
        bus = queue.popleft()
        for other in neighbours[bus]:
            if other not in layer:
                layer[other] = layer[bus] + 1
                queue.append(other)
    return layer


@pytest.mark.parametrize("example", ["minimal", "mv_ring", "quickstart"])
def test_the_layout_places_every_element_by_graph_distance(example):
    net = api.EXAMPLES[example]()
    diagram = gi.Diagram().with_layout(net)
    assert diagram.check(net) == []
    assert list(diagram.nodes) == list(net.buses)
    assert list(diagram.edges) == list(net.branches)
    root = next(iter(net.sources.values())).bus
    layers = _bfs_layers(net, root)
    xs = {name: node.x for name, node in diagram.nodes.items()}
    dx = min(x for x in xs.values() if x > 0)
    assert {name: round(x / dx) for name, x in xs.items()} == layers
    positions = [(n.x, n.y) for n in diagram.nodes.values()]
    assert len(set(positions)) == len(positions)  # no two symbols on one spot
    grid = diagram.grid_size
    assert all(c % grid == 0 for p in positions for c in p)  # on the raster
    assert diagram.nodes[root].symbol == "source_bus"
    assert gi.Diagram().with_layout(net) == diagram  # deterministic


def test_the_layout_stacks_unconnected_parts():
    net = _network()
    gi.create_bus(name="island", type=net.buses["S2"].type, network=net)
    diagram = gi.Diagram().with_layout(net)
    main = [diagram.nodes[n].y for n in ("Q", "S1", "S2")]
    assert diagram.nodes["island"].y > max(main)


def test_only_unplaced_keeps_what_was_placed():
    net = _network()
    placed = gi.Diagram(nodes={"S1": gi.DiagramNode(x=500, y=500, symbol="busbar", length=80)},
                        edges={"K1": gi.DiagramEdge(waypoints=[(250, 500)])})
    diagram = placed.with_layout(net, only_unplaced=True)
    assert diagram.nodes["S1"] == placed.nodes["S1"]
    assert diagram.edges["K1"] == placed.edges["K1"]
    assert set(diagram.nodes) == set(net.buses) and set(diagram.edges) == set(net.branches)
    others = [(n.x, n.y) for name, n in diagram.nodes.items() if name != "S1"]
    assert (500, 500) not in others
    assert all(y > 500 for _, y in others)  # below the placed part
    # A full layout drops the routes and the stale entries.
    stale = placed.model_copy(update={"nodes": {**placed.nodes, "gone": gi.DiagramNode(x=0, y=0)}})
    full = stale.with_layout(net)
    assert "gone" not in full.nodes and full.edges["K1"].waypoints == []


# --- renaming ---------------------------------------------------------------


def _solved_epr(network: gi.Network, fault: str) -> Dict[str, float]:
    result = gi.solve(network, fault)
    return {bus.name: bus.uepr for bus in result.buses}


def test_rename_bus_rewires_every_reference_and_moves_the_symbol():
    session = _session()
    session.solve("F")
    before = _state(session)
    order = list(session.network.buses)
    report = session.apply(api.RenameBus(name="S1", new_name="Station 1"))
    net = session.network
    assert list(net.buses) == ["Q", "Station 1", "S2"] == [n if n != "S1" else "Station 1" for n in order]
    assert (net.branches["K1"].to_bus, net.branches["K2"].from_bus) == ("Station 1", "Station 1")
    assert net.faults["F"].bus == "Station 1"
    assert "Station 1" in session.diagram_model.nodes and "S1" not in session.diagram_model.nodes
    assert report.stale_results == ["F"]  # the result names the old bus
    after = _state(session)
    session.undo()
    assert _state(session) == before and session.stale_results() == []
    session.redo()
    assert _state(session) == after


def test_a_renamed_network_solves_to_the_same_numbers():
    original = _network()
    session = _session(_network())
    session.apply(api.RenameBus(name="S1", new_name="S1_new"))
    session.apply(api.RenameBranch(name="K2", new_name="cable S1-S2"))
    session.apply(api.RenameSource(name="I", new_name="infeed"))
    session.apply(api.RenameFault(name="F", new_name="F_station"))
    renamed = _solved_epr(session.network, "F_station")
    reference = _solved_epr(original, "F")
    mapping = {"S1_new": "S1"}
    assert {mapping.get(k, k): v for k, v in renamed.items()} == reference


def test_rename_keeps_an_overridden_impedance():
    session = _session()
    session.network.buses["S1"].impedance = {f: gi.ComplexNumber(real=9.0, imag=0.0) for f in FREQS}
    session.apply(api.RenameBus(name="S1", new_name="X"))
    assert session.network.buses["X"].impedance[50.0].real == 9.0


def test_rename_fault_moves_the_active_state_and_undo_restores_the_result():
    session = _session()
    session.solve("F")
    session.network.set_active_fault("F", keep_results=True)
    before = _state(session)
    session.apply(api.RenameFault(name="F", new_name="F2"))
    net = session.network
    assert list(net.faults) == ["F2"] and net.active_fault == "F2"
    assert "F" not in net.results  # the result described the fault under its old name
    session.undo()
    assert _state(session) == before
    assert session.network.active_fault == "F" and session.stale_results() == []


def test_rename_types_delegates_to_the_type_update():
    session = _session()
    session.apply(api.RenameBusType(name="station", new_name="Ortsnetzstation"))
    assert {b.type.name for b in session.network.buses.values()} == {"remote", "Ortsnetzstation"}
    session.apply(api.RenameBranchType(name="cable", new_name="NA2XS2Y"))
    assert session.summary().branch_types == ["NA2XS2Y"]
    session.undo()
    session.undo()
    assert session.summary().bus_types == ["remote", "station"]


# --- diagram commands -------------------------------------------------------


def test_place_bus_adds_bus_and_symbol_in_one_step():
    session = _session()
    before = _state(session)
    session.apply(api.PlaceBus(name="S3", type="station", x=600, y=40, symbol="busbar",
                               specific_earth_resistance=150.0))
    assert session.network.buses["S3"].specific_earth_resistance == 150.0
    assert session.diagram_model.nodes["S3"] == gi.DiagramNode(x=600, y=40, symbol="busbar")
    session.undo()
    assert _state(session) == before


def test_move_nodes_is_one_undo_step_and_does_not_touch_results():
    session = _session()
    session.solve("F")
    network_json = session.network.model_dump_json()
    before = _state(session)
    session.apply(api.MoveNodes(positions={"S1": (300, 20), "S2": (520, 20)}))
    nodes = session.diagram_model.nodes
    assert (nodes["S1"].x, nodes["S1"].y, nodes["S2"].x) == (300, 20, 520)
    assert session.network.model_dump_json() == network_json  # the network is untouched
    assert session.stale_results() == [] and session.dirty
    assert session.history()[-1] == "move 2 node(s)"
    session.undo()
    assert _state(session) == before


def test_route_branch_and_update_node():
    session = _session()
    session.apply(api.RouteBranch(name="K2", waypoints=[(300, 0), (300, 80)]))
    assert session.diagram_model.edges["K2"].waypoints == [(300.0, 0.0), (300.0, 80.0)]
    session.apply(api.UpdateDiagramNode(name="Q", fields={"symbol": "source_bus",
                                                          "label_offset": (0, -30)}))
    assert session.diagram_model.nodes["Q"].label_offset == (0.0, -30.0)
    session.undo()
    assert session.diagram_model.nodes["Q"].label_offset == (0.0, 0.0)


def test_auto_layout_computes_or_takes_the_positions_of_the_front_end():
    session = _session()
    session.apply(api.MoveNodes(positions={"S1": (900, 900)}))
    session.apply(api.AutoLayout())
    assert session.diagram_model == gi.Diagram().with_layout(session.network)
    # A front end that lays out itself (elkjs) sends the positions it computed.
    session.apply(api.AutoLayout(positions={"Q": (0, 0), "S1": (0, 100), "S2": (0, 200)},
                                 routes={"K2": [(40, 150)]}))
    nodes = session.diagram_model.nodes
    assert [(nodes[n].x, nodes[n].y) for n in ("Q", "S1", "S2")] == [(0, 0), (0, 100), (0, 200)]
    assert session.diagram_model.edges["K2"].waypoints == [(40.0, 150.0)]
    assert session.diagram_model.edges["K1"].waypoints == []


def test_removing_an_element_removes_its_entry_and_undo_puts_it_back():
    session = _session()
    session.apply(api.RouteBranch(name="K2", waypoints=[(300, 0)]))
    before = _state(session)
    session.apply(api.RemoveFault(name="F"))
    session.apply(api.RemoveBranch(name="K2"))
    session.apply(api.RemoveBus(name="S2"))
    diagram = session.diagram_model
    assert "S2" not in diagram.nodes and "K2" not in diagram.edges
    assert diagram.check(session.network) == []
    for _ in range(3):
        session.undo()
    assert _state(session) == before


def _rejected() -> List[Any]:
    return [
        api.RenameBus(name="S1", new_name="S2"),  # taken
        api.RenameBus(name="ghost", new_name="x"),
        api.RenameBus(name="S1", new_name=""),
        api.RenameBranch(name="K1", new_name="K2"),
        api.RenameSource(name="ghost", new_name="x"),
        api.RenameFault(name="F", new_name="F"),  # no change
        api.RenameBusType(name="station", new_name="remote"),
        api.PlaceBus(name="S1", type="station", x=0, y=0),  # duplicate bus
        api.PlaceBus(name="S9", type="station", x=math.inf, y=0),
        api.PlaceBus(name="S9", type="nope", x=0, y=0),
        api.MoveNodes(positions={"S1": (10, 10), "ghost": (0, 0)}),  # all or nothing
        api.MoveNodes(positions={}),
        api.RouteBranch(name="ghost", waypoints=[]),
        api.RouteBranch(name="K1", waypoints=[(0, math.nan)]),
        api.UpdateDiagramNode(name="S1", fields={"symbol": "triangle"}),
        api.UpdateDiagramNode(name="S1", fields={"colour": "red"}),
        api.UpdateDiagramNode(name="ghost", fields={"symbol": "bus"}),
        api.AutoLayout(positions={"ghost": (0, 0)}),
        api.AutoLayout(routes={"ghost": []}),
        api.AutoLayout(only_unplaced=True),  # everything is placed: nothing to do
    ]


@pytest.mark.parametrize("index", range(20))
def test_a_rejected_command_changes_neither_network_nor_diagram(index):
    commands = _rejected()
    assert len(commands) == 20
    session = _session()
    session.apply(api.MoveNodes(positions={"S2": (400, 40)}))
    session.solve("F")
    before = (_state(session), session.network.model_dump_json(), session.revision,
              session.history(), session.can_redo)
    with pytest.raises(api.CommandError):
        session.apply(commands[index])
    assert (_state(session), session.network.model_dump_json(), session.revision,
            session.history(), session.can_redo) == before


def _catalogue() -> List[Any]:
    return [
        api.PlaceBus(name="S3", type="station", x=600, y=0),
        api.MoveNodes(positions={"S3": (620, 20), "Q": (0, 20)}),
        api.RouteBranch(name="K2", waypoints=[(300, 0)]),
        api.UpdateDiagramNode(name="S2", fields={"symbol": "busbar", "length": 60}),
        api.RenameBus(name="S2", new_name="S2b"),
        api.RenameBranch(name="K2", new_name="K2b"),
        api.RenameSource(name="I", new_name="I2"),
        api.RenameFault(name="F", new_name="F2"),
        api.RenameBusType(name="remote", new_name="remote2"),
        api.RenameBranchType(name="cable", new_name="cable2"),
        api.AddBus(name="S4", type="station"),  # not placed: the next command places it
        api.AutoLayout(only_unplaced=True),
        api.AutoLayout(),
    ]


def test_every_new_command_undoes_and_redoes_exactly():
    session = _session()
    session.solve("F")
    states = [_state(session)]
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        for command in _catalogue():
            session.apply(command)
            states.append(_state(session))
            assert states[-1] != states[-2], command.op
        for k in range(len(_catalogue()), 0, -1):
            session.undo()
            assert _state(session) == states[k - 1], k
        for k in range(1, len(_catalogue()) + 1):
            session.redo()
            assert _state(session) == states[k], k
    for command in _catalogue():  # request bodies: through JSON and back
        assert api.parse_command(json.loads(command.model_dump_json())) == command


def test_the_catalogues_cover_every_command():
    from tests.test_audit_pass19_service_layer import _command_catalogue

    # The characteristic commands and Batch have their own tests.
    characteristic_ops = {"set_characteristic", "clear_characteristic", "import_characteristic",
                          "batch", "add_view", "update_view", "remove_view", "set_colour_scheme"}
    covered = {c.op for c in _command_catalogue()} | {c.op for c in _catalogue()} | characteristic_ops
    assert covered == set(api.COMMAND_OPS)


# --- the project file of a session ------------------------------------------


def test_a_session_saves_and_reopens_diagram_and_views(tmp_path):
    path = tmp_path / "projekt.json"
    views = [{"title": "EPR", "blocks": []}]
    with _session(views=views) as session:
        session.apply(api.MoveNodes(positions={"S2": (480, 60)}))
        session.save(str(path))
        saved = session.diagram_model
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["diagram"]["nodes"]["S2"]["x"] == 480 and payload["views"] == views
    with api.ProjectSession.from_file(str(path)) as reopened:
        assert reopened.diagram_model == saved and reopened.views == views
        assert not reopened.dirty


def test_a_network_without_diagram_is_laid_out_when_opened(tmp_path):
    path = tmp_path / "plain.json"
    gi.save_network_to_json(_network(), path)
    with api.ProjectSession.from_file(str(path)) as session:
        assert session.diagram_model == gi.Diagram().with_layout(session.network)
        assert not session.dirty and session.revision == 0


def test_the_diagram_dto_lists_every_element_with_its_place():
    with _session() as session:
        session.apply(api.AddBus(name="S3", type="station"))  # not placed yet
        dto = session.diagram()
        assert json.loads(dto.model_dump_json()) == json.loads(
            type(dto).model_validate_json(dto.model_dump_json()).model_dump_json())
        nodes = {n.name: n for n in dto.nodes}
        assert list(nodes) == list(session.network.buses)
        assert nodes["S3"].placed is False and nodes["S1"].placed is True
        assert nodes["Q"].symbol == "source_bus" and nodes["Q"].has_source
        assert nodes["S1"].has_fault and nodes["S1"].bus_type == "station"
        free = [(n.x, n.y) for n in dto.nodes]
        assert len(set(free)) == len(free)
        edges = {e.name: e for e in dto.edges}
        assert (edges["K2"].from_bus, edges["K2"].to_bus) == ("S1", "S2")
        assert {(m.kind, m.name, m.bus) for m in dto.markers} == {("fault", "F", "S1"),
                                                                 ("source", "I", "Q")}


def test_the_memory_store_keeps_the_diagram_and_the_database_lays_it_out(tmp_path):
    store = api.MemoryStore()
    with _session() as session:
        session.apply(api.MoveNodes(positions={"S2": (480, 60)}))
        session.save_to(store, "p")
        saved = session.diagram_model
    with api.ProjectSession.from_store(store, "p") as reopened:
        assert reopened.diagram_model == saved
    database = gi.Database(str(tmp_path / "p.db"))
    try:
        db_store = api.DatabaseStore(database)
        with _session() as session:
            session.save_to(db_store, "char")
        with api.ProjectSession.from_store(db_store, "char") as reopened:
            assert reopened.diagram_model == gi.Diagram().with_layout(reopened.network)
    finally:
        database.close()


# --- paths ------------------------------------------------------------------


def test_home_and_environment_variables_are_expanded(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))  # Windows
    monkeypatch.setenv("GI_PROJECTS", str(tmp_path / "projekte"))
    (tmp_path / "projekte").mkdir()
    with _session() as session:
        location = session.save("~/a.json")
        assert location == os.path.join(str(tmp_path), "a.json")
        assert (tmp_path / "a.json").exists()
        session.save(os.path.join("$GI_PROJECTS", "b.json"))
        assert (tmp_path / "projekte" / "b.json").exists()
    with api.ProjectSession.from_file("~/a.json") as reopened:
        assert reopened.location == os.path.join(str(tmp_path), "a.json")
    with pytest.raises(api.ProjectError, match="missing.json"):
        api.ProjectSession.from_file("~/missing.json")


# --- DTO schema version 2 ---------------------------------------------------


def test_dto_version_2_is_written_and_version_1_is_read():
    assert api.DTO_SCHEMA_VERSION == 2
    assert api.READABLE_DTO_SCHEMA_VERSIONS == (1, 2)
    old = {"dto_schema_version": 1, "op": "remove_bus", "name": "b7"}
    assert api.parse_command(old).name == "b7"
    row = api.ElementRefDTO.model_validate({"dto_schema_version": 1, "kind": "bus", "name": "b"})
    assert row.kind == "bus"
    with pytest.raises(ValueError):
        api.ElementRefDTO.model_validate({"dto_schema_version": 3, "kind": "bus", "name": "b"})
    assert api.ElementRefDTO(kind="bus", name="b").dto_schema_version == 2

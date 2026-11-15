# tests/test_project_file_v2.py
"""
Project file schema version 2 and the network diagram.

Version 2 is an envelope ``{schema_version, groundinsight_version, network,
diagram, views}``; versions 1 and none hold the network's fields at the top
level. Reading accepts every version, writing produces version 2 only. The
migration v1 -> v2 is checked against a file that release 2026.11.1 wrote
(``tests/data/project_written_by_2026_11_1.json``): the network read from it
equals the one the release saved, the stored result stays current, and a
second save writes version 2 with the same network section.
"""

import json
import math
import os
import pathlib

import pytest
from pydantic import ValidationError

import groundinsight as gi
from groundinsight.api import examples
from tests._characteristic_cases import _network, _table
from tests._warning_policy import acknowledge

DATA = pathlib.Path(__file__).resolve().parent / "data"
V1_FILE = DATA / "project_written_by_2026_11_1.json"


def _diagram():
    return gi.Diagram(
        nodes={
            "Q": gi.DiagramNode(x=0, y=0, symbol="source_bus", label_offset=(0, -24)),
            "S1": gi.DiagramNode(x=200, y=0),
            "S2": gi.DiagramNode(x=400, y=0, symbol="busbar", length=120),
        },
        edges={"K1": gi.DiagramEdge(), "K2": gi.DiagramEdge(waypoints=[(300, 0), (300, 60)])},
        faults={"F": gi.DiagramMarker(offset=(10, -20))},
        sources={"I": gi.DiagramMarker()},
        color_scheme="EPR",
    )


# --- the diagram ---------------------------------------------------------------


@pytest.mark.parametrize(
    ("factory", "message"),
    [
        (lambda: gi.DiagramNode(x=math.inf, y=0), "finite"),
        (lambda: gi.DiagramNode(x=0, y=0, symbol="triangle"), "symbol"),
        (lambda: gi.DiagramNode(x=0, y=0, length=-1), ">= 0"),
        (lambda: gi.DiagramNode(x=0, y=0, colour="red"), "colour"),
        (lambda: gi.DiagramEdge(waypoints=[(0, math.nan)]), "finite"),
        (lambda: gi.DiagramMarker(offset=(math.inf, 0)), "finite"),
        (lambda: gi.Diagram(grid_size=0), "grid_size"),
    ],
)
def test_invalid_diagram_entries_are_refused(factory, message):
    with pytest.raises(ValidationError, match=message):
        factory()


def test_renamed_and_without_return_changed_copies():
    diagram = _diagram()
    renamed = diagram.renamed("bus", "S1", "Station 1")
    assert list(renamed.nodes) == ["Q", "Station 1", "S2"]  # order kept
    assert renamed.nodes["Station 1"] == diagram.nodes["S1"]
    assert "S1" in diagram.nodes  # the original is untouched
    assert diagram.renamed("bus", "absent", "x") == diagram
    with pytest.raises(ValueError, match="already has an entry"):
        diagram.renamed("bus", "S1", "S2")
    with pytest.raises(ValueError, match="unknown element kind"):
        diagram.renamed("tower", "S1", "x")
    reduced = diagram.without("branch", "K2").without("fault", "F")
    assert list(reduced.edges) == ["K1"] and reduced.faults == {}
    assert "K2" in diagram.edges


def test_check_reports_unknown_and_unplaced_elements():
    net = _network()
    assert _diagram().check(net) == []
    diagram = _diagram().without("bus", "S2").without("branch", "K1").renamed("fault", "F", "F_old")
    records = {(d.code, d.severity, tuple(map(tuple, d.elements))) for d in diagram.check(net)}
    assert records == {
        ("DIAGRAM_ELEMENT_NOT_PLACED", "info", (("bus", "S2"),)),
        ("DIAGRAM_ELEMENT_NOT_PLACED", "info", (("branch", "K1"),)),
        ("DIAGRAM_UNKNOWN_ELEMENT", "warning", (("fault", "F_old"),)),
    }


# --- version 2 -------------------------------------------------------------


def test_save_and_load_a_project_with_diagram_and_views(tmp_path):
    net = _network(station_characteristic=_table(), rho=300.0)
    gi.run_fault(net, "F")
    views = [{"title": "Spannungen", "blocks": [{"kind": "chart", "quantity": "EPR_V",
                                                 "elements": ["S1", "S2"], "rms": True}]}]
    path = tmp_path / "Projekt Süd.json"
    gi.save_project(path, net, _diagram(), views)
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert list(payload) == ["schema_version", "groundinsight_version", "network", "diagram", "views"]
    assert payload["schema_version"] == gi.io.project_file.SCHEMA_VERSION == 2
    project = gi.load_project(path)
    assert project.network.model_dump_json() == net.model_dump_json()
    assert project.diagram == _diagram()
    assert project.views == views
    assert project.schema_version == 2 and project.groundinsight_version == gi.__version__
    assert project.network.stale_results() == []
    assert project.network.buses["S1"].type.characteristic == _table()


def test_save_network_to_json_writes_version_2_without_diagram(tmp_path):
    path = tmp_path / "plain.json"
    gi.save_network_to_json(_network(), path)
    project = gi.load_project(path)
    assert (project.schema_version, project.diagram, project.views) == (2, None, [])


def test_a_view_that_is_not_an_object_is_refused_before_the_disk_is_touched(tmp_path):
    path = tmp_path / "keep.json"
    gi.save_project(path, _network())
    before = path.read_bytes()
    with pytest.raises(TypeError, match="JSON object"):
        gi.save_project(path, _network(), views=["not an object"])
    with pytest.raises(ValueError):
        gi.save_project(path, _network(), views=[{"x": math.nan}])
    assert path.read_bytes() == before
    assert sorted(os.listdir(tmp_path)) == ["keep.json"]


@pytest.mark.parametrize(
    ("change", "message"),
    [
        ({"extra": 1}, "unknown top-level keys"),
        ({"network": None}, "no 'network' object"),
        ({"diagram": [1]}, "'diagram' must be an object"),
        ({"views": {"a": 1}}, "'views' must be a list"),
        ({"views": [1]}, "'views' must be a list"),
        ({"diagram": {"nodes": {"B": {"x": "left", "y": 0}}}}, "x"),
    ],
)
def test_malformed_sections_are_refused(tmp_path, change, message):
    path = tmp_path / "broken.json"
    gi.save_project(path, _network())
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload.update(change)
    path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ValueError, match=message):
        gi.load_project(path)


# --- migration v1 -> v2 ------------------------------------------------------


@acknowledge("SCALINGS_INCOMPLETE")  # the quickstart example scales 50 Hz only
def test_a_file_written_by_2026_11_1_loads_as_a_project_without_diagram():
    payload = json.loads(V1_FILE.read_text(encoding="utf-8"))
    assert payload["schema_version"] == 1 and payload["groundinsight_version"] == "2026.11.1"
    project = gi.load_project(V1_FILE)
    assert (project.schema_version, project.diagram, project.views) == (1, None, [])
    # Second way: the release's own dump of the same network, without the header.
    del payload["schema_version"], payload["groundinsight_version"]
    assert json.loads(project.network.model_dump_json()) == json.loads(
        gi.Network.model_validate(payload).model_dump_json())
    # The stored result is current, and the same as a fresh solve.
    assert project.network.stale_results() == []
    fresh = examples.build_quickstart()
    for fault in fresh.faults:
        gi.run_fault(fresh, fault)
        stored = project.network.res_buses(fault)
        assert stored.drop("U_vT_V", "U_transfer_V").equals(
            fresh.res_buses(fault).drop("U_vT_V", "U_transfer_V"))


def test_saving_a_version_1_project_writes_version_2(tmp_path):
    project = gi.load_project(V1_FILE)
    path = tmp_path / "migrated.json"
    gi.save_project(path, project.network)
    again = gi.load_project(path)
    assert again.schema_version == 2
    assert again.network.model_dump_json() == project.network.model_dump_json()
    assert again.network.stale_results() == []

# tests/test_type_catalogue.py
"""
The type catalogue of a project: kept in the project file, edited in the session.

A network knows only the types its elements use. A project also keeps the
types nobody uses yet -- created for drawing, or taken from the standard
library -- in the ``types`` section of the project file, so a new project can
start empty and a type survives deleting its last element. Checked a second
way: what the file holds is read back, what the session holds is reopened,
and a bus drawn with a library preset has the impedance its characteristic
gives directly.
"""

import json

import pytest

import groundinsight as gi
from groundinsight import api


def _network():
    net = gi.create_network(name="n", frequencies=[50.0])
    station = gi.BusType(name="station", system_type="MV/LV", voltage_level=20.0, impedance_formula="rho/50")
    gi.create_bus(name="A", type=station, specific_earth_resistance=100.0, network=net)
    return net


NAYY = "NAYY 4x150SM 0.6/1 kV"
RING = "Substation ring 2.5 m, 4 rods 2.5 m"


def test_unused_types_are_kept_in_the_types_section(tmp_path):
    net = _network()
    cable = gi.standard_branch_types()[NAYY]
    ring = gi.standard_bus_types()[RING]
    used = net.buses["A"].type
    path = tmp_path / "p.json"
    gi.save_project(path, net, bus_types=[used, ring], branch_types=[cable])
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert list(payload) == ["schema_version", "groundinsight_version", "network", "diagram", "views", "types"]
    # Only the types no element uses are written; the used one lives in the network section.
    assert [t["name"] for t in payload["types"]["bus_types"]] == [RING]
    assert [t["name"] for t in payload["types"]["branch_types"]] == [NAYY]
    project = gi.load_project(path)
    assert project.bus_types == [ring] and project.branch_types == [cable]
    assert gi.project_from_json(gi.project_to_json(net, bus_types=[ring])).bus_types == [ring]


def test_a_catalogue_type_that_contradicts_an_element_type_is_refused(tmp_path):
    net = _network()
    other = net.buses["A"].type.model_copy(update={"impedance_formula": "rho/10"})
    path = tmp_path / "p.json"
    with pytest.raises(ValueError, match="station"):
        gi.save_project(path, net, bus_types=[other])
    assert not path.exists()
    gi.save_project(path, net, bus_types=[gi.standard_bus_types()[RING]])
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["types"]["bus_types"].append(json.loads(other.model_dump_json()))
    path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ValueError, match="station"):
        gi.load_project(path)


def test_a_file_without_types_section_loads_with_an_empty_catalogue(tmp_path):
    path = tmp_path / "p.json"
    gi.save_project(path, _network())
    payload = json.loads(path.read_text(encoding="utf-8"))
    del payload["types"]
    path.write_text(json.dumps(payload), encoding="utf-8")
    project = gi.load_project(path)
    assert (project.bus_types, project.branch_types) == ([], [])


def test_a_session_keeps_types_without_users_across_save_and_open(tmp_path):
    session = api.ProjectSession(_network())
    cable = gi.standard_branch_types()[NAYY]
    session.apply(api.AddBranchType(definition=json.loads(cable.model_dump_json())))
    session.apply(api.AddBusType(definition=json.loads(gi.standard_bus_types()[RING].model_dump_json())))
    path = str(tmp_path / "p.json")
    session.save(path)
    again = api.ProjectSession.from_file(path)
    assert again.summary().branch_types == [NAYY]
    assert again.summary().bus_types == ["station", RING]
    # Second way: the reopened type is the library's, field by field.
    assert gi.load_project(path).branch_types == [cable]
    # And in a store, the same.
    store = api.MemoryStore()
    session.save_to(store, "k")
    assert api.ProjectSession.from_store(store, "k").summary().branch_types == [NAYY]


def test_an_empty_project_gets_its_first_types_from_the_library_and_draws_with_them():
    session = api.ProjectSession(gi.create_network(name="empty", frequencies=[50.0, 250.0]))
    ring = gi.standard_bus_types()[RING]
    session.apply(api.AddBusType(definition=json.loads(ring.model_dump_json())))
    session.apply(api.PlaceBus(name="ONS", type=RING, x=0.0, y=0.0))
    bus = session.network.buses["ONS"]
    expected = ring.characteristic.evaluate(bus.specific_earth_resistance, [50.0, 250.0]).impedance
    assert [complex(bus.impedance[f]) for f in (50.0, 250.0)] == pytest.approx(list(expected), rel=1e-12)
    # Removing the bus keeps the type in the catalogue; undo restores the bus.
    session.apply(api.RemoveBus(name="ONS"))
    assert session.summary().bus_types == [RING]
    session.undo()
    assert "ONS" in session.network.buses

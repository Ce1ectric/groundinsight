# gui/tests/test_plan.py
"""
The network plan, driven by NiceGUI's simulated ``User``: event -> command.

The canvas runs in a browser (``tests/test_browser.py`` drives it with a
mouse); here its events are fired directly, as the browser would send them,
and the test checks the command the page applied -- through the session's
history, network and diagram -- and that nothing changed when the library
rejected it. Numbers are compared with the library a second way: a bus
edited in the dialog derives the same impedance as a network built with
``groundinsight`` directly.
"""

import json
import os

import pytest
from nicegui.testing import User

import groundinsight as gi
from groundinsight.api import EXAMPLES

from groundinsight_gui.app import AppOptions, build_app
from groundinsight_gui.plan import DEFAULT_BRANCH_LENGTH_KM, DEFAULT_SOURCE_A, free_name

pytestmark = pytest.mark.anyio


async def _page(user: User, example: str = "minimal"):
    pages = build_app(AppOptions(start_example=example))
    await user.open("/")
    return pages[0]


def _fire(user: User, event: str, **args):
    user.find(marker="canvas").trigger(event, args)


def _state(session):
    return (session.network.model_dump_json(exclude={"results", "paths"}),
            session.diagram_model.model_dump_json())


async def test_the_plan_is_the_first_tab_and_shows_the_diagram(user: User):
    page = await _page(user, "mv_ring")
    await user.should_see(marker="canvas")
    canvas = next(iter(user.find(marker="canvas").elements))
    assert page.tab == "plan"
    assert [n["name"] for n in canvas.diagram["nodes"]] == list(page.session.network.buses)
    assert canvas.tool == "select"


async def test_placing_buses_and_a_source_bus(user: User):
    page = await _page(user)
    session = page.session
    before = _state(session)
    page.plan.set_tool("bus")
    _fire(user, "place", x=400, y=200, symbol="bus")
    assert session.history() == ["place bus 'B1'"]
    assert session.diagram_model.nodes["B1"].x == 400.0
    assert session.network.buses["B1"].type.name == page.plan.bus_type
    _fire(user, "place", x=400, y=320, symbol="source_bus")
    assert session.network.sources["I1"].bus == "B2"
    assert {f: complex(v) for f, v in session.network.sources["I1"].values.items()} == {
        f: DEFAULT_SOURCE_A for f in session.network.frequencies}
    assert session.diagram_model.nodes["B2"].symbol == "source_bus"
    assert len(session.history()) == 2  # bus and source: one step
    user.find(marker="undo").click()
    user.find(marker="undo").click()
    assert _state(session) == before


async def test_moving_drawing_a_branch_and_a_fault(user: User):
    page = await _page(user)
    session = page.session
    session.solve("fault")
    _fire(user, "move", positions={"bus2": [180, 120], "bus3": [360, 120]})
    assert (session.diagram_model.nodes["bus2"].y, session.history()[-1]) == (120.0, "move 2 node(s)")
    assert session.stale_results() == []  # moving a symbol is no input of a result
    _fire(user, "connect", from_bus="bus1", to_bus="bus3")
    branch = session.network.branches["L1"]
    assert (branch.from_bus, branch.to_bus, branch.length) == ("bus1", "bus3", DEFAULT_BRANCH_LENGTH_KM)
    assert branch.type.name == page.plan.branch_type and page.plan.selected == ("branch", "L1")
    _fire(user, "fault", bus="bus2")
    assert session.network.faults["F1"].bus == "bus2"
    assert session.network.faults["F1"].scalings == {f: 1.0 for f in session.network.frequencies}


async def test_a_rejected_gesture_changes_nothing(user: User):
    page = await _page(user)
    session = page.session
    before = (_state(session), session.history())
    _fire(user, "connect", from_bus="bus1", to_bus="ghost")
    _fire(user, "move", positions={"ghost": [0, 0]})
    assert (_state(session), session.history()) == before
    await user.should_see("Änderung abgelehnt")


async def test_deleting_a_connected_bus_is_one_step(user: User):
    page = await _page(user)
    session = page.session
    before = _state(session)
    page.plan.select("bus", "bus3")
    _fire(user, "delete", kind="bus", name="bus3")
    assert "bus3" not in session.network.buses and "fault" not in session.network.faults
    assert "bus3" not in session.diagram_model.nodes and len(session.history()) == 1
    session.undo()
    assert _state(session) == before


async def test_selection_is_shared_by_plan_table_and_properties(user: User):
    page = await _page(user)
    _fire(user, "select", kind="bus", name="bus2")
    assert page.plan.selected == ("bus", "bus2") and page.selected["buses"] == "bus2"
    canvas = next(iter(user.find(marker="canvas").elements))
    assert canvas.selected == {"kind": "bus", "name": "bus2"}
    await user.should_see(marker="properties-title")
    page._on_row_clicked("branches", {"data": {"name": "b23"}})
    assert canvas.selected == {"kind": "branch", "name": "b23"}
    _fire(user, "select", kind=None, name=None)
    assert canvas.selected is None


async def test_the_bus_dialog_applies_every_change_as_one_step(user: User):
    page = await _page(user)
    session = page.session
    _fire(user, "open", kind="bus", name="bus2")
    await user.should_see(marker="element-dialog")
    user.find(marker="dlg-rho").clear().type("250,5")
    user.find(marker="dlg-name").clear().type("Station 2")
    user.find(marker="dlg-fault").click()  # a fault at this bus
    user.find(marker="dialog-apply").click()
    assert "Station 2" in session.network.buses and len(session.history()) == 1
    bus = session.network.buses["Station 2"]
    assert bus.specific_earth_resistance == 250.5
    assert [f.bus for f in session.network.faults.values()].count("Station 2") == 1
    # Second way: the same bus built with the library directly.
    fresh = EXAMPLES["minimal"]()
    twin = gi.create_bus(name="x", type=fresh.buses["bus2"].type, specific_earth_resistance=250.5,
                         network=fresh)
    assert bus.impedance == twin.impedance
    assert page.plan.selected == ("bus", "Station 2")


async def test_the_bus_dialog_adds_a_source_with_its_values(user: User):
    page = await _page(user)
    session = page.session
    _fire(user, "open", kind="bus", name="bus3")
    await user.should_see(marker="dlg-source")
    user.find(marker="dlg-source").click()
    user.find(marker="dialog-apply").click()
    source = next(s for s in session.network.sources.values() if s.bus == "bus3")
    assert {f: complex(v) for f, v in source.values.items()} == {50.0: DEFAULT_SOURCE_A}


async def test_the_branch_dialog_opens_the_switch(user: User):
    page = await _page(user)
    session = page.session
    _fire(user, "open", kind="branch", name="b23")
    await user.should_see(marker="dlg-active")
    user.find(marker="dlg-active").click()
    user.find(marker="dlg-length").clear().type("2,5")
    user.find(marker="dialog-apply").click()
    branch = session.network.branches["b23"]
    assert (branch.active, branch.length) == (False, 2.5)
    edge = next(e for e in session.diagram().edges if e.name == "b23")
    assert edge.active is False


async def test_colouring_and_auto_layout_from_the_toolbar(user: User):
    page = await _page(user, "mv_ring")
    session = page.session
    user.find(marker="colour-infeed_fault").click()
    assert page.plan.mode == "infeed_fault"
    canvas = next(iter(user.find(marker="canvas").elements))
    assert canvas._props["colours"]["nodes"]["b10"] == "#D55E00"
    user.find(marker="colour-epr").click()
    await user.should_see(marker="legend-empty")  # no result yet
    _fire(user, "move", positions={"b05": [900, 900]})
    user.find(marker="layout-library").click()
    assert session.diagram_model == gi.Diagram().with_layout(session.network)
    _fire(user, "layout", positions={"b01": [0, 0], "b02": [100, 0]})
    assert session.history()[-1] == "auto layout (front end)"
    assert session.diagram_model.nodes["b02"].x == 100.0


async def test_opening_a_project_from_the_toolbar(user: User, tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    gi.save_network_to_json(EXAMPLES["mv_ring"](), tmp_path / "ring.json")
    page = await _page(user)
    user.find(marker="open").click()
    user.find(marker="open-dialog-path").type("~/ring.json")
    user.find(marker="open-dialog-confirm").click()
    await user.should_see(os.path.join(str(tmp_path), "ring.json"))  # the title, after the open
    assert page.session.location == os.path.join(str(tmp_path), "ring.json")
    assert len(page.session.network.buses) == 20


class _Upload:
    """What ui.upload hands its handler: a file whose text is a document."""

    def __init__(self, document):
        self.file = self
        self._text = json.dumps(document)

    async def text(self):
        return self._text


async def test_a_groundfield_document_is_imported_from_the_bus_dialog(user: User):
    from groundinsight_gui.plan import BusDialog

    page = await _page(user)
    rhos, freqs = (50.0, 100.0, 200.0), (50.0, 250.0)
    points = [(r, f) for r in rhos for f in freqs]
    document = {
        "schema": "groundfield.bustype", "schema_version": 1, "name": "ONS A", "system_type": "MV/LV",
        "voltage_level": 20.0, "impedance_formula": "0.01*rho + 0.0001*j*f",
        "samples": {"rho_Ohm_m": [r for r, _ in points], "frequency_Hz": [f for _, f in points],
                    "Z_real_Ohm": [0.01 * r for r, _ in points], "Z_imag_Ohm": [1e-4 * f for _, f in points]},
        "metadata": {"source": "groundfield 2026.11.0"},
    }
    dialog = BusDialog(page.plan, "bus2")
    dialog.open()
    await dialog._import(_Upload(document))
    bus = page.session.network.buses["bus2"]
    assert bus.characteristic is not None and bus.characteristic.name == "ONS A"
    assert complex(bus.impedance[50.0]) == pytest.approx(0.01 * bus.specific_earth_resistance + 0.005j)


def test_free_names():
    assert free_name("L", []) == "L1" and free_name("L", ["L1", "L2"]) == "L3"

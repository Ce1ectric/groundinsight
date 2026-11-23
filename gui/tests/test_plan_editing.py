# gui/tests/test_plan_editing.py
"""
Editing gestures of the network plan beyond a single element.

Several elements selected at once, moved and deleted as one undo step. The
canvas events are fired as the browser sends them (``tests/test_browser.py``
makes them with a real mouse and keyboard); the tests read the session to see
what the page did with them.
"""

import pytest
from nicegui.testing import User

from groundinsight_gui.app import AppOptions, build_app

pytestmark = pytest.mark.anyio


async def _page(user: User, example: str = "minimal"):
    pages = build_app(AppOptions(start_example=example))
    await user.open("/")
    return pages[0]


def _fire(user: User, event: str, **args):
    user.find(marker="canvas").trigger(event, args)


def _canvas(user: User):
    return next(iter(user.find(marker="canvas").elements))


def _state(session):
    return (session.network.model_dump_json(exclude={"results", "paths"}),
            session.diagram_model.model_dump_json())


async def test_a_click_with_a_modifier_adds_to_the_selection_and_takes_away_again(user: User):
    page = await _page(user)
    _fire(user, "select", kind="bus", name="bus2")
    _fire(user, "select", kind="bus", name="bus3", add=True)
    assert page.plan.selection == [("bus", "bus2"), ("bus", "bus3")]
    # No single element: nothing to show in a table row or to open.
    assert page.plan.selected is None
    assert _canvas(user).selection == [{"kind": "bus", "name": "bus2"}, {"kind": "bus", "name": "bus3"}]
    await user.should_see("2 Elemente ausgewählt")
    _fire(user, "select", kind="bus", name="bus2", add=True)
    assert page.plan.selection == [("bus", "bus3")] and page.plan.selected == ("bus", "bus3")
    _fire(user, "select", kind="bus", name="bus1")  # without the modifier: only this one
    assert page.plan.selection == [("bus", "bus1")]
    _fire(user, "select", kind=None, name=None)
    assert page.plan.selection == [] and _canvas(user).selection == []


async def test_a_frame_selects_what_it_holds_and_ignores_what_is_gone(user: User):
    page = await _page(user)
    _fire(user, "select_many", items=[{"kind": "bus", "name": "bus2"}, {"kind": "bus", "name": "bus3"},
                                      {"kind": "branch", "name": "b23"}, {"kind": "bus", "name": "ghost"}])
    assert page.plan.selection == [("bus", "bus2"), ("bus", "bus3"), ("branch", "b23")]
    await user.should_see("3 Elemente ausgewählt")
    await user.should_see("2 Busse, 1 Zweig")


async def test_several_elements_are_deleted_as_one_step(user: User):
    page = await _page(user)
    session = page.session
    before = _state(session)
    _fire(user, "select_many", items=[{"kind": "bus", "name": "bus2"}, {"kind": "bus", "name": "bus3"}])
    _fire(user, "delete", items=[{"kind": "bus", "name": "bus2"}, {"kind": "bus", "name": "bus3"}])
    # Both buses, the fault at bus3 and the two branches that end at them.
    assert list(session.network.buses) == ["bus1"]
    assert not session.network.branches and not session.network.faults
    assert len(session.history()) == 1 and page.plan.selection == []
    session.undo()
    assert _state(session) == before


async def test_the_delete_button_of_the_properties_deletes_the_selection(user: User):
    page = await _page(user)
    session = page.session
    _fire(user, "select_many", items=[{"kind": "branch", "name": "b12"}, {"kind": "branch", "name": "b23"}])
    await user.should_see(marker="properties-delete")
    user.find(marker="properties-delete").click()
    assert not session.network.branches and len(session.history()) == 1
    assert list(session.network.buses) == ["bus1", "bus2", "bus3"]


async def test_a_selected_group_moves_as_one_step(user: User):
    page = await _page(user)
    session = page.session
    _fire(user, "select_many", items=[{"kind": "bus", "name": "bus2"}, {"kind": "bus", "name": "bus3"}])
    _fire(user, "move", positions={"bus2": [200, 300], "bus3": [380, 300]})
    assert session.history() == ["move 2 node(s)"]
    # The group stays selected after the move.
    assert page.plan.selection == [("bus", "bus2"), ("bus", "bus3")]


async def test_escape_goes_back_to_the_selection_tool_and_clears_the_selection(user: User):
    page = await _page(user)
    page.plan.set_tool("bus")
    _fire(user, "select", kind="bus", name="bus2")
    _fire(user, "key", action="escape")
    assert page.plan.tool == "select" and _canvas(user).tool == "select"
    assert page.plan.selection == []
    # The palette shows the tool that is active.
    assert next(iter(user.find(marker="tools").elements)).value == "select"


async def test_select_all_takes_every_bus_and_branch(user: User):
    page = await _page(user)
    _fire(user, "key", action="select_all")
    assert page.plan.selection == [("bus", "bus1"), ("bus", "bus2"), ("bus", "bus3"),
                                   ("branch", "b12"), ("branch", "b23")]


async def test_the_undo_and_redo_shortcuts_are_the_buttons(user: User):
    page = await _page(user)
    session = page.session
    before = _state(session)
    _fire(user, "place", x=400, y=200, symbol="bus")
    after = _state(session)
    await page.shortcut({"action": "undo"})
    assert _state(session) == before
    await page.shortcut({"action": "redo"})
    assert _state(session) == after
    await page.shortcut({"action": "redo"})  # nothing left to redo: no error, no change
    assert _state(session) == after and session.history() == ["place bus 'B1'"]


async def test_copy_and_paste_duplicate_buses_with_their_fields_and_the_branch_between(user: User):
    import groundinsight as gi
    from groundinsight.api import EXAMPLES, UpdateBus

    page = await _page(user)
    session = page.session
    session.apply(UpdateBus(name="bus2", fields={"specific_earth_resistance": 250.0, "description": "Station"}))
    before = _state(session)
    _fire(user, "select_many", items=[{"kind": "bus", "name": "bus2"}, {"kind": "bus", "name": "bus3"},
                                      {"kind": "branch", "name": "b23"}])
    _fire(user, "key", action="copy")
    assert _state(session) == before  # copying changes nothing
    _fire(user, "key", action="paste")
    assert session.history()[-1] == "3 Elemente duplizieren" and len(session.history()) == 2
    copy2, copy3 = session.network.buses["bus2 Kopie"], session.network.buses["bus3 Kopie"]
    original = session.network.buses["bus2"]
    assert (copy2.type.name, copy2.specific_earth_resistance, copy2.description) == (
        original.type.name, 250.0, "Station")
    assert copy3.impedance == session.network.buses["bus3"].impedance
    branch = session.network.branches["b23 Kopie"]
    assert (branch.from_bus, branch.to_bus) == ("bus2 Kopie", "bus3 Kopie")
    assert (branch.type.name, branch.length) == (session.network.branches["b23"].type.name,
                                                 session.network.branches["b23"].length)
    # Beside the originals, by the same offset, on the raster.
    nodes, grid = session.diagram_model.nodes, session.diagram_model.grid_size
    offsets = {(nodes[f"{n} Kopie"].x - nodes[n].x, nodes[f"{n} Kopie"].y - nodes[n].y) for n in ("bus2", "bus3")}
    assert offsets == {(2 * grid, 2 * grid)}
    # The fault at bus3 is not copied: a fault location is set on purpose.
    assert [f.bus for f in session.network.faults.values()] == ["bus3"]
    # The copies are selected, ready to be dragged to their place.
    assert page.plan.selection == [("bus", "bus2 Kopie"), ("bus", "bus3 Kopie"), ("branch", "b23 Kopie")]
    # Second way: the copied bus derives the impedance of a bus built with the library.
    fresh = EXAMPLES["minimal"]()
    twin = gi.create_bus(name="x", type=fresh.buses["bus2"].type, specific_earth_resistance=250.0, network=fresh)
    assert copy2.impedance == twin.impedance
    session.undo()
    assert _state(session) == before


async def test_paste_at_the_pointer_and_a_second_paste_gets_new_names(user: User):
    page = await _page(user)
    session = page.session
    _fire(user, "select", kind="bus", name="bus1")
    _fire(user, "key", action="copy")
    _fire(user, "key", action="paste", x=500, y=260)
    node = session.diagram_model.nodes["bus1 Kopie"]
    assert (node.x, node.y) == (500.0, 260.0)
    # bus1 feeds the network: the copy brings its infeed along, with the same current.
    sources = {s.bus: s for s in session.network.sources.values()}
    assert sources["bus1 Kopie"].values == sources["bus1"].values
    _fire(user, "key", action="paste", x=600, y=260)
    assert "bus1 Kopie 2" in session.network.buses


async def test_a_copied_branch_alone_is_a_parallel_branch_and_duplicate_does_both_steps(user: User):
    page = await _page(user)
    session = page.session
    _fire(user, "select", kind="branch", name="b12")
    _fire(user, "key", action="duplicate")
    parallel = session.network.branches["b12 Kopie"]
    assert (parallel.from_bus, parallel.to_bus) == ("bus1", "bus2")
    assert len(session.history()) == 1 and page.plan.selection == [("branch", "b12 Kopie")]


async def test_paste_without_a_copy_does_nothing(user: User):
    page = await _page(user)
    before = _state(page.session)
    _fire(user, "key", action="paste")
    assert _state(page.session) == before and page.session.history() == []


async def test_the_context_menus_are_in_the_language_of_the_page(user: User):
    await _page(user)
    menus = _canvas(user)._props["menus"]
    assert set(menus) == {"bus", "branch", "group", "pane"}
    assert [entry["label"] for entry in menus["bus"]] == [
        "Bearbeiten …", "Duplizieren", "Fehler hier setzen", "Einspeisung hier setzen", "In Tabelle zeigen", "Löschen"]
    assert [entry["action"] for entry in menus["branch"]] == [
        "open", "duplicate", "open_switch", "close_switch", "show_row", "delete"]
    assert [entry["action"] for entry in menus["pane"]] == ["place_bus", "paste", "select_all", "fit"]
    assert all("_" not in entry["label"] for entries in menus.values() for entry in entries)


async def test_the_context_menu_of_a_bus(user: User):
    page = await _page(user)
    session = page.session
    _fire(user, "menu", action="fault", kind="bus", name="bus2")
    assert [f.bus for f in session.network.faults.values()] == ["bus3", "bus2"]
    _fire(user, "menu", action="source", kind="bus", name="bus2")
    source = next(s for s in session.network.sources.values() if s.bus == "bus2")
    assert {f: complex(v) for f, v in source.values.items()} == {50.0: 1000.0}
    steps = len(session.history())
    _fire(user, "menu", action="source", kind="bus", name="bus2")  # it has one already
    assert len(session.history()) == steps
    await user.should_see("hat schon eine Einspeisung")
    _fire(user, "menu", action="duplicate", kind="bus", name="bus2")
    assert "bus2 Kopie" in session.network.buses
    _fire(user, "menu", action="open", kind="bus", name="bus2")
    await user.should_see(marker="element-dialog")


async def test_the_context_menu_of_a_branch_switches_it_and_shows_its_row(user: User):
    page = await _page(user)
    session = page.session
    _fire(user, "menu", action="open_switch", kind="branch", name="b23")
    assert session.network.branches["b23"].active is False
    _fire(user, "menu", action="close_switch", kind="branch", name="b23")
    assert session.network.branches["b23"].active is True and len(session.history()) == 2
    _fire(user, "menu", action="show_row", kind="branch", name="b23")
    assert page.tab == "network" and page.selected["branches"] == "b23"


async def test_the_context_menu_of_the_pane_and_of_a_group(user: User):
    page = await _page(user)
    session = page.session
    _fire(user, "menu", action="place_bus", kind="pane", name=None, x=400, y=260)
    assert (session.diagram_model.nodes["B1"].x, session.diagram_model.nodes["B1"].y) == (400.0, 260.0)
    assert page.plan.tool == "select"  # the menu places one bus, the tool stays
    _fire(user, "select_many", items=[{"kind": "bus", "name": "bus2"}, {"kind": "bus", "name": "B1"}])
    _fire(user, "menu", action="delete", kind="group", name=None)
    assert "bus2" not in session.network.buses and "B1" not in session.network.buses
    _fire(user, "menu", action="select_all", kind="pane", name=None)
    assert ("bus", "bus1") in page.plan.selection and ("bus", "bus3") in page.plan.selection


async def test_the_plan_writes_the_result_beside_the_element_and_marks_it_stale(user: User):
    import warnings

    import groundinsight as gi
    from groundinsight.api import EXAMPLES, UpdateBranch

    page = await _page(user, "mv_ring")
    session = page.session
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        session.solve("F1")
    user.find(marker="colour-epr").click()
    notes = _canvas(user)._props["annotations"]
    # Second way: the RMS EPR of the library's own solve, four significant digits, decimal comma.
    twin = EXAMPLES["mv_ring"]()
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        gi.run_fault(twin, "F1")
    frame = twin.res_buses("F1").filter(twin.res_buses("F1")["frequency_Hz"].is_null())
    expected = {name: f"{value:.4g}".replace(".", ",") + " V"
                for name, value in zip(frame["bus_name"], frame["EPR_V"], strict=True)}
    assert {name: note["value"] for name, note in notes["nodes"].items()} == expected
    assert notes["stale"] is False
    await user.should_not_see(marker="legend-stale")
    # The fault and the infeed are named at their bus, and the tooltip says what the element is.
    assert notes["nodes"]["b10"]["faults"] == ["F1"] and notes["nodes"]["b01"]["sources"] == ["src"]
    assert notes["nodes"]["b10"]["tip"] == f"b10 · MV_substation · EPR {expected['b10']} · Fehler F1"
    # An input changes: the numbers stay on the plan, marked as stale.
    session.apply(UpdateBranch(name="L_03_04", fields={"length": 7.0}))
    page.plan.refresh()
    assert _canvas(user)._props["annotations"]["stale"] is True
    await user.should_see(marker="legend-stale")
    # Coloured by type there is no number to write.
    user.find(marker="colour-type").click()
    notes = _canvas(user)._props["annotations"]
    assert "value" not in notes["nodes"]["b10"] and notes["stale"] is False
    assert notes["edges"]["L_01_02"]["tip"].startswith("L_01_02 · NA2XS_300_25")


async def test_an_empty_project_says_how_to_begin(user: User):
    await _page(user, "empty")
    assert "Bus" in _canvas(user)._props["hint"]


async def test_a_plan_with_elements_has_no_hint(user: User):
    await _page(user)
    assert _canvas(user)._props["hint"] == ""


async def test_a_copy_never_lands_on_another_symbol(user: User):
    """Paste with the pointer still on the original, and paste again: each copy gets a free place."""
    page = await _page(user)
    session = page.session
    nodes, grid = session.diagram_model.nodes, session.diagram_model.grid_size
    x, y = nodes["bus2"].x, nodes["bus2"].y
    _fire(user, "select", kind="bus", name="bus2")
    _fire(user, "key", action="copy")
    _fire(user, "key", action="paste", x=x, y=y)
    _fire(user, "key", action="paste", x=x, y=y)
    _fire(user, "key", action="duplicate")  # of the selection, the second copy: beside it, not on the first
    nodes = session.diagram_model.nodes
    places = [(n.x, n.y) for n in nodes.values()]
    assert len(set(places)) == len(places) == 6
    assert (nodes["bus2 Kopie"].x, nodes["bus2 Kopie"].y) == (x + 2 * grid, y + 2 * grid)
    assert (nodes["bus2 Kopie 2"].x, nodes["bus2 Kopie 2"].y) == (x + 4 * grid, y + 4 * grid)

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

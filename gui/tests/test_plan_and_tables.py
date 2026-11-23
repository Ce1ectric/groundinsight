# gui/tests/test_plan_and_tables.py
"""
Plan and tables as two views of one network: finding an element in either.

A row is shown in the plan (the plan tab, the element selected and moved into
the middle), an element of the plan in its table; a search field filters the
tables, another one finds an element of the plan by its name.
"""

import pytest
from nicegui.testing import User

from groundinsight_gui.app import AppOptions, build_app

pytestmark = pytest.mark.anyio


async def _page(user: User, example: str = "mv_ring"):
    pages = build_app(AppOptions(start_example=example))
    await user.open("/")
    return pages[0]


def _element(user: User, marker: str):
    return next(iter(user.find(marker=marker).elements))


async def test_a_row_is_shown_in_the_plan(user: User):
    page = await _page(user)
    page.show_tab("network")
    page._on_row_clicked("branches", {"data": {"name": "L_07_08"}})
    user.find(marker="show-in-plan-branches").click()
    assert page.tab == "plan" and page.plan.selected == ("branch", "L_07_08")
    canvas = _element(user, "canvas")
    assert {k: canvas._props["focus"][k] for k in ("kind", "name")} == {"kind": "branch", "name": "L_07_08"}
    # A fault and a source are shown at their bus.
    page.show_tab("network")
    page._on_row_clicked("faults", {"data": {"name": "F1"}})
    user.find(marker="show-in-plan-faults").click()
    assert page.tab == "plan" and page.plan.selected == ("bus", "b10")
    first = canvas._props["focus"]["n"]
    # Asking again for the same element moves the plan again.
    page.show_in_plan("bus", "b10")
    assert canvas._props["focus"]["name"] == "b10" and canvas._props["focus"]["n"] != first


async def test_show_in_plan_without_a_row_says_so(user: User):
    page = await _page(user)
    page.show_tab("network")
    user.find(marker="show-in-plan-buses").click()
    await user.should_see("erst eine Zeile anklicken")
    assert page.tab == "network"


async def test_an_element_of_the_plan_is_shown_in_its_table(user: User):
    page = await _page(user)
    user.find(marker="canvas").trigger("select", {"kind": "bus", "name": "b17"})
    await user.should_see(marker="properties-show-row")
    user.find(marker="properties-show-row").click()
    assert page.tab == "network" and page.selected["buses"] == "b17"


async def test_the_search_field_filters_every_table(user: User):
    page = await _page(user)
    page.show_tab("network")
    await user.should_see(marker="table-filter")
    user.find(marker="table-filter").type("b17")
    assert all(grid.options["quickFilterText"] == "b17" for grid in page.grids.values())
    # The filter survives a change of the network (the grids are sent their rows again).
    from groundinsight.api import UpdateBus

    page.apply(UpdateBus(name="b17", fields={"description": "Station 17"}))
    assert all(grid.options["quickFilterText"] == "b17" for grid in page.grids.values())
    user.find(marker="table-filter").clear()
    assert all(grid.options["quickFilterText"] == "" for grid in page.grids.values())


async def test_the_search_of_the_plan_finds_buses_and_branches_by_name(user: User):
    page = await _page(user)
    search = _element(user, "plan-search")
    assert search.options["bus:b17"] == "b17" and search.options["branch:L_07_08"] == "L_07_08 (Zweig)"
    search.set_value("branch:L_07_08")
    assert page.plan.selected == ("branch", "L_07_08")
    assert _element(user, "canvas")._props["focus"]["name"] == "L_07_08"
    assert search.value is None  # ready for the next search
    # A new bus is found at once.
    user.find(marker="canvas").trigger("place", {"x": 0, "y": 600, "symbol": "bus"})
    assert "bus:B1" in _element(user, "plan-search").options

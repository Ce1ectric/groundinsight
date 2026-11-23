# gui/tests/test_results_view.py
"""
Results where they are needed: on the tiles, over the plan, in the evaluation tabs.

The tiles name their quantity as the standard writes it (a lower-case r stays
one); the fault is chosen and solved from the plan, which then shows the
result without a change of tab; an evaluation tab is renamed.
"""

import warnings

import pytest
from nicegui.testing import User

import groundinsight as gi
from groundinsight.api import EXAMPLES, AddFault, AddView

from groundinsight_gui.app import AppOptions, build_app

pytestmark = pytest.mark.anyio

JOB_RETRIES = 100


async def _page(user: User, example: str = "minimal"):
    pages = build_app(AppOptions(start_example=example))
    await user.open("/")
    return pages[0]


def _element(user: User, marker: str):
    return next(iter(user.find(marker=marker).elements))


async def test_the_tile_of_the_reduction_factor_keeps_its_lower_case_r(user: User):
    page = await _page(user)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        page.session.solve("fault")
    page.result_view.refresh()
    page.show_tab("results")
    await user.should_see(marker="tile-r-label")
    label = _element(user, "tile-r-label")
    assert label.text == "Reduktionsfaktor r = IE / IF"
    # Upper case by style would show "R = IE / IF", a resistance.
    assert "uppercase" not in label.classes


async def test_the_fault_is_chosen_and_solved_from_the_plan(user: User):
    page = await _page(user, "mv_ring")
    session = page.session
    session.apply(AddFault(name="F2", bus="b05", scalings={50.0: 1.0}))
    page._after_change()
    assert _element(user, "plan-fault").options == ["F1", "F2"] and _element(user, "plan-fault").value == "F1"
    _element(user, "plan-fault").set_value("F2")
    # One choice for the page: the results tab shows the same fault.
    assert page.fault == "F2" and page.fault_select.value == "F2"
    page.fault_select.set_value("F1")
    assert _element(user, "plan-fault").value == "F1"
    user.find(marker="plan-run").click()
    await user.should_see(marker="result-footer", retries=JOB_RETRIES)
    assert page.tab == "plan"  # the plan stays in front
    # The plan shows the result at once: coloured by EPR, the values at the buses.
    assert page.plan.mode == "epr"
    twin = EXAMPLES["mv_ring"]()
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        gi.run_fault(twin, "F1")
    frame = twin.res_buses("F1").filter(twin.res_buses("F1")["frequency_Hz"].is_null())
    notes = _element(user, "canvas")._props["annotations"]["nodes"]
    assert notes["b10"]["value"] == f"{dict(zip(frame['bus_name'], frame['EPR_V'], strict=True))['b10']:.4g} V".replace(
        ".", ",")


async def test_solving_from_the_plan_keeps_a_result_colouring_that_was_chosen(user: User):
    page = await _page(user, "mv_ring")
    user.find(marker="colour-i_branch").click()
    user.find(marker="plan-run").click()
    await user.should_see(marker="result-footer", retries=JOB_RETRIES)
    assert page.plan.mode == "i_branch"


async def test_without_a_fault_the_plan_says_what_is_missing(user: User):
    page = await _page(user, "empty")
    user.find(marker="plan-run").click()
    await user.should_see("Kein Fehlerort")
    assert page.handle is None


async def test_an_evaluation_tab_is_renamed(user: User):
    page = await _page(user)
    page.apply(AddView(view={"id": "v1", "title": "Auswertung", "blocks": []}))
    page.show_tab("v1")
    await user.should_see(marker="view-rename-v1")
    user.find(marker="view-rename-v1").click()
    await user.should_see(marker="view-title")
    user.find(marker="view-title").clear().type("EPR je Station")
    user.find(marker="view-create").click()
    assert page.session.views[0]["title"] == "EPR je Station" and page.session.views[0]["id"] == "v1"
    await user.should_see("EPR je Station")
    assert _element(user, "tab-v1").props["label"] == "EPR je Station"  # the tab itself, too
    assert page.tab == "v1"
    page.session.undo()
    assert page.session.views[0]["title"] == "Auswertung"


async def test_result_tables_show_whole_headers_and_the_sources_their_kind_in_words(user: User):
    page = await _page(user)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        page.session.solve("fault")
    page.result_view.refresh()
    page.show_tab("results")
    await user.should_see(marker="grid-bus_results")
    grid = _element(user, "grid-bus_results")
    assert grid.options["defaultColDef"]["wrapHeaderText"] and grid.options["defaultColDef"]["autoHeaderHeight"]
    kind = next(c for c in page.grids["sources"].options["columnDefs"] if c["field"] == "source_type")
    assert "Stromquelle" in kind[":valueFormatter"] and "Spannungsquelle" in kind[":valueFormatter"]

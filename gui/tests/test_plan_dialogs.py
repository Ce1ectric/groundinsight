# gui/tests/test_plan_dialogs.py
"""
The dialogs of the plan, as a person fills them in.

A wrong entry is reported at its field, in the language of the page, and
changes nothing; the dialog stays open until the entry is right. The fault and
the infeed of a bus are renamed where they are edited. Every dialog can be
left without doing anything.
"""

import pytest
from nicegui.testing import User

from groundinsight.api import groundfield_available

from groundinsight_gui.app import AppOptions, build_app

pytestmark = pytest.mark.anyio


async def _page(user: User, example: str = "minimal", **options):
    pages = build_app(AppOptions(start_example=example, **options))
    await user.open("/")
    return pages[0]


def _fire(user: User, event: str, **args):
    user.find(marker="canvas").trigger(event, args)


def _element(user: User, marker: str):
    return next(iter(user.find(marker=marker).elements))


async def test_a_wrong_number_is_reported_at_its_field_and_changes_nothing(user: User):
    page = await _page(user)
    session = page.session
    _fire(user, "open", kind="bus", name="bus2")
    await user.should_see(marker="element-dialog")
    assert "autofocus" in _element(user, "dlg-name").props  # typing starts in the name
    user.find(marker="dlg-rho").clear().type("abc")
    user.find(marker="dialog-apply").click()
    assert _element(user, "dlg-rho").error == "Keine Zahl: abc"
    assert session.history() == []
    await user.should_see(marker="element-dialog")  # still open
    await user.should_not_see("Änderung abgelehnt")  # said once, at the field
    user.find(marker="dlg-rho").clear().type("250,5")
    user.find(marker="dlg-rho").trigger("keydown.enter")  # Enter applies
    assert session.network.buses["bus2"].specific_earth_resistance == 250.5
    assert len(session.history()) == 1
    await user.should_not_see(marker="element-dialog")


async def test_a_name_that_exists_or_is_missing_is_reported_at_the_name(user: User):
    page = await _page(user, language="en")
    session = page.session
    _fire(user, "open", kind="branch", name="b12")
    await user.should_see(marker="element-dialog")
    user.find(marker="dlg-name").clear().type("b23")
    user.find(marker="dialog-apply").click()
    assert _element(user, "dlg-name").error == "This name is taken"
    user.find(marker="dlg-name").clear()
    user.find(marker="dialog-apply").click()
    assert _element(user, "dlg-name").error == "A name is needed"
    user.find(marker="dlg-length").clear().type("x")
    user.find(marker="dlg-name").clear().type("b12 new")
    user.find(marker="dialog-apply").click()
    # The error at the name is gone, the next one is shown at its field.
    assert _element(user, "dlg-name").error is None
    assert _element(user, "dlg-length").error == "Not a number: x"
    assert session.history() == [] and "b12" in session.network.branches


async def test_what_the_library_rejects_is_reported_and_the_dialog_stays(user: User):
    page = await _page(user)
    session = page.session
    _fire(user, "open", kind="branch", name="b12")
    await user.should_see(marker="element-dialog")
    user.find(marker="dlg-length").clear().type("-1")
    user.find(marker="dialog-apply").click()
    await user.should_see("Änderung abgelehnt")
    await user.should_see(marker="element-dialog")
    assert session.history() == [] and session.network.branches["b12"].length != -1.0


async def test_the_fault_and_the_infeed_are_renamed_in_the_bus_dialog(user: User):
    page = await _page(user)
    session = page.session
    _fire(user, "open", kind="bus", name="bus3")
    await user.should_see(marker="dlg-fault-name")
    assert _element(user, "dlg-fault-name").value == "fault"
    user.find(marker="dlg-fault-name").clear().type("fault 1")
    user.find(marker="dialog-apply").click()
    assert list(session.network.faults) == ["fault 1"] and len(session.history()) == 1
    assert page.fault == "fault 1"  # the page follows the renamed fault
    _fire(user, "open", kind="bus", name="bus1")
    await user.should_see(marker="dlg-source-name")
    name = _element(user, "dlg-source-name").value
    values = dict(session.network.sources[name].values)
    user.find(marker="dlg-source-name").clear().type("Einspeisung UW")
    user.find(marker="dialog-apply").click()
    assert dict(session.network.sources["Einspeisung UW"].values) == values and len(session.history()) == 2
    # A name another fault has is reported at the field.
    _fire(user, "fault", bus="bus2")
    _fire(user, "open", kind="bus", name="bus2")
    await user.should_see(marker="dlg-fault-name")
    user.find(marker="dlg-fault-name").clear().type("fault 1")
    user.find(marker="dialog-apply").click()
    assert _element(user, "dlg-fault-name").error == "Diesen Namen gibt es schon"


async def test_a_new_fault_and_a_new_infeed_get_the_name_typed(user: User):
    page = await _page(user)
    session = page.session
    _fire(user, "open", kind="bus", name="bus2")
    await user.should_see(marker="dlg-fault")
    user.find(marker="dlg-fault").click()
    user.find(marker="dlg-fault-name").clear().type("fault 2")
    user.find(marker="dlg-source").click()
    user.find(marker="dlg-source-name").clear().type("Notstrom")
    user.find(marker="dialog-apply").click()
    assert session.network.faults["fault 2"].bus == "bus2"
    assert session.network.sources["Notstrom"].bus == "bus2" and len(session.history()) == 1


async def test_properties_and_type_parameters_carry_units_and_words(user: User):
    await _page(user)
    _fire(user, "select", kind="branch", name="b12")
    await user.should_see("Länge [km]")
    await user.should_see("ρ [Ω·m]")
    _fire(user, "open", kind="branch", name="b12")
    await user.should_see(marker="type-grounding_conductor")
    assert _element(user, "type-grounding_conductor").text == "ja"  # not "True"


async def test_every_small_dialog_has_a_cancel_button(user: User):
    page = await _page(user)
    user.find(marker="view-add").click()
    await user.should_see(marker="view-cancel")
    user.find(marker="view-cancel").click()
    await user.should_not_see(marker="view-dialog")
    assert page.session.views == []
    user.find(marker="io-export_tables").click()
    await user.should_see(marker="io-cancel")
    user.find(marker="io-cancel").click()
    await user.should_not_see(marker="io-dialog")


async def test_the_block_dialog_of_an_evaluation_can_be_cancelled(user: User):
    from groundinsight.api import AddView

    page = await _page(user)
    page.apply(AddView(view={"id": "v1", "title": "Auswertung", "blocks": []}))
    page.show_tab("v1")
    await user.should_see(marker="block-add-v1")
    user.find(marker="block-add-v1").click()
    await user.should_see(marker="block-cancel")
    user.find(marker="block-cancel").click()
    await user.should_not_see(marker="block-dialog")
    assert page.session.views[0]["blocks"] == []


@pytest.mark.skipif(groundfield_available(), reason="groundfield is installed: the button is enabled")
async def test_without_groundfield_the_dialog_says_why_the_button_is_grey(user: User):
    await _page(user)
    _fire(user, "open", kind="bus", name="bus2")
    await user.should_see(marker="char-compute")
    assert not _element(user, "char-compute").enabled
    # In words beside the button: a disabled button shows no tooltip.
    await user.should_see(marker="char-compute-reason", content="Dafür muss groundfield installiert sein")

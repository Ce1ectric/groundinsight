# gui/tests/test_catalogue.py
"""
The type catalogue in the GUI: new, edit, copy, from the standard library, frequencies.

Driven by NiceGUI's simulated ``User``. Every action must be one command of
the session (one undo step), and every number is compared a second way: a
type taken from the library equals the library's, a bus drawn with an
electrode preset has the impedance the preset's characteristic gives, an
edited formula derives what the library derives from it directly.
"""

import json

import pytest
from nicegui.testing import User

import groundinsight as gi
from groundinsight.api import EXAMPLES

from groundinsight_gui.app import AppOptions, build_app

pytestmark = pytest.mark.anyio

RING = "Substation ring 2.5 m, 4 rods 2.5 m"
NAYY = "NAYY 4x150SM 0.6/1 kV"


async def _page(user: User, example: str = "empty"):
    pages = build_app(AppOptions(start_example=example))
    await user.open("/")
    return pages[0]


def _set(user: User, marker: str, value):
    element = next(iter(user.find(marker=marker).elements))
    element.set_value(value)
    return element


async def test_an_empty_project_draws_with_a_preset_from_the_palette(user: User):
    page = await _page(user)
    session = page.session
    assert session.summary().bus_types == [] and len(session.network.buses) == 0
    user.find(marker="palette-add-bus").click()
    await user.should_see(marker="library-dialog")
    _set(user, "library-pick", RING)
    user.find(marker="library-apply").click()
    assert session.summary().bus_types == [RING] and page.plan.bus_type == RING
    assert session.history() == [f"add bus type '{RING}'"]
    page.plan.set_tool("bus")
    user.find(marker="canvas").trigger("place", {"x": 0, "y": 0, "symbol": "bus"})
    bus = session.network.buses["B1"]
    # Second way: the preset's characteristic evaluated directly.
    expected = gi.standard_bus_types()[RING].characteristic.evaluate(bus.specific_earth_resistance, [50.0])
    assert complex(bus.impedance[50.0]) == pytest.approx(complex(expected.impedance[0]), rel=1e-12)


async def test_a_cable_from_the_library_is_the_librarys_type(user: User):
    page = await _page(user)
    user.find(marker="type-library-branch").click()
    await user.should_see(marker="library-dialog")
    _set(user, "library-pick", NAYY)
    assert [e.text for e in user.find(marker="library-description").elements][0].startswith(NAYY)
    user.find(marker="library-apply").click()
    added = gi.BranchType.model_validate(page.session.type_definition("branch", NAYY))
    assert added == gi.standard_branch_types()[NAYY]
    page.session.undo()
    assert page.session.summary().branch_types == []


async def test_a_new_bus_type_from_the_dialog(user: User):
    page = await _page(user)
    user.find(marker="type-new-bus").click()
    await user.should_see(marker="type-dialog")
    _set(user, "type-name", "Mast")
    _set(user, "type-voltage_level", "20")
    _set(user, "type-impedance_formula", "rho/40")
    user.find(marker="type-apply").click()
    mast = gi.BusType.model_validate(page.session.type_definition("bus", "Mast"))
    assert (mast.impedance_formula, mast.system_type, mast.voltage_level) == ("rho/40", "MV/LV", 20.0)
    assert page.session.history() == ["add bus type 'Mast'"]


async def test_a_broken_formula_is_refused_and_changes_nothing(user: User):
    page = await _page(user)
    user.find(marker="type-new-bus").click()
    await user.should_see(marker="type-dialog")
    _set(user, "type-name", "Mast")
    _set(user, "type-impedance_formula", "rho *")
    user.find(marker="type-apply").click()
    assert page.session.summary().bus_types == [] and page.session.history() == []
    await user.should_see(marker="type-dialog")  # stays open for a correction


async def test_editing_a_used_type_renames_it_and_its_users_follow(user: User):
    page = await _page(user, "quickstart")
    session = page.session
    page.selected["bus_types"] = "SubstationBus"
    user.find(marker="type-edit-bus").click()
    await user.should_see(marker="type-dialog")
    _set(user, "type-name", "UW Süd")
    _set(user, "type-impedance_formula", "rho * 0.02")
    user.find(marker="type-apply").click()
    assert len(session.history()) == 1
    bus = session.network.buses["bus_fault"]
    assert bus.type.name == "UW Süd"
    # Second way: the same formula on a bus built with the library.
    net = EXAMPLES["quickstart"]()
    twin = gi.create_bus(name="x", type=net.buses["bus_fault"].type.model_copy(
        update={"impedance_formula": "rho * 0.02"}), specific_earth_resistance=100.0, network=net)
    assert bus.impedance == twin.impedance
    session.undo()
    assert session.network.buses["bus_fault"].type.name == "SubstationBus"


async def test_a_copy_gets_a_free_name_and_the_same_fields(user: User):
    page = await _page(user, "quickstart")
    page.selected["branch_types"] = "MSCable"
    user.find(marker="type-copy-branch").click()
    user.find(marker="type-copy-branch").click()
    session = page.session
    assert session.summary().branch_types == ["MSCable", "MSCable Kopie", "MSCable Kopie 2"]
    original = session.type_definition("branch", "MSCable")
    assert session.type_definition("branch", "MSCable Kopie") == {**original, "name": "MSCable Kopie"}


async def test_the_frequencies_are_changed_in_one_step(user: User):
    page = await _page(user)
    user.find(marker="frequencies-edit").click()
    await user.should_see(marker="frequency-dialog")
    _set(user, "frequency-input", "50; 250,0")
    user.find(marker="frequency-apply").click()
    assert page.session.network.frequencies == [50.0, 250.0]
    assert len(page.session.history()) == 1
    user.find(marker="frequencies-edit").click()
    _set(user, "frequency-input", "fünfzig")
    user.find(marker="frequency-apply").click()
    assert page.session.network.frequencies == [50.0, 250.0]


async def test_types_without_users_are_saved_with_the_project(user: User, tmp_path):
    page = await _page(user)
    user.find(marker="type-library-branch").click()
    await user.should_see(marker="library-dialog")
    _set(user, "library-pick", NAYY)
    user.find(marker="library-apply").click()
    path = tmp_path / "leer.json"
    page.session.save(str(path))
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert [t["name"] for t in payload["types"]["branch_types"]] == [NAYY]

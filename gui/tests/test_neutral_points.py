# gui/tests/test_neutral_points.py
"""
Neutral-point elements, supply areas and the earth current by source location in the GUI.

Everything the page does here goes through the service layer: the dialog
applies ``AddNeutral``/``UpdateNeutral``/``RenameNeutral``/``RemoveNeutral``,
the tables are ``ProjectSession.supply_areas`` and
``ProjectSession.earth_current``. The numbers shown are compared with the
library's.
"""

import asyncio
import math

import pytest
from nicegui.testing import User

import groundinsight as gi

from groundinsight_gui.app import AppOptions, build_app
from groundinsight_gui.catalogue import FIELDS
from groundinsight_gui.colouring import MODES, NEUTRAL, colour

pytestmark = pytest.mark.anyio

F = 50.0
I_CE = math.sqrt(3) * 2 * math.pi * F * 250e-9 * 10.0 * 20e3


@pytest.fixture
def project(tmp_path):
    """A 20 kV feeder of the library's cable: substation and three stations, a fault at the end."""
    uw = gi.BusType(name="UW", system_type="Substation", voltage_level=20.0, impedance_formula="0.5")
    ons = gi.BusType(name="ONS", system_type="Substation", voltage_level=20.0, impedance_formula="rho/50")
    cable = gi.standard_branch_types()["NA2XS(F)2Y 1x150RM/25 12/20 kV"]
    net = gi.create_network(name="feeder", frequencies=[F, 250.0])
    gi.create_bus(name="UW", type=uw, network=net)
    for k, (a, b, km) in enumerate((("UW", "S1", 3.0), ("S1", "S2", 3.0), ("S2", "S3", 4.0)), start=1):
        gi.create_bus(name=b, type=ons, network=net)
        gi.create_branch(name=f"K{k}", type=cable, from_bus=a, to_bus=b, length=km, network=net)
    gi.create_fault(name="F", bus="S3", network=net)
    path = tmp_path / "feeder.json"
    gi.save_project(str(path), net)
    return str(path)


async def _page(user: User, project: str, **options):
    pages = build_app(AppOptions(start_file=project, **options))
    await user.open("/")
    return pages[0]


def _fire(user: User, event: str, **args):
    user.find(marker="canvas").trigger(event, args)


def _element(user: User, marker: str):
    return next(iter(user.find(marker=marker).elements))


async def _open_neutral_tab(user: User, bus: str = "UW"):
    _fire(user, "open", kind="bus", name=bus)
    await user.should_see(marker="dlg-neutral")


async def test_a_coil_is_entered_in_the_bus_dialog_and_the_detuning_is_shown(user: User, project):
    page = await _page(user, project)
    session = page.session
    await _open_neutral_tab(user)
    assert _element(user, "dlg-neutral-voltage").value == "20"          # from the bus type
    assert _element(user, "dlg-neutral-area").text.startswith("Noch kein Speisebereich")
    user.find(marker="dlg-neutral").click()
    _element(user, "dlg-neutral-kind").set_value("resonant")
    user.find(marker="dlg-neutral-name").clear().type("Spule UW")
    user.find(marker="dlg-neutral-coil").clear().type("28,567")
    user.find(marker="dlg-neutral-active").clear().type("0,816")
    user.find(marker="dialog-apply").click()
    neutral = session.network.neutrals["Spule UW"]
    assert isinstance(neutral, gi.ResonantNeutral)
    assert (neutral.bus, neutral.voltage_level, neutral.coil_current) == ("UW", 20.0, 28.567)
    assert len(session.history()) == 1                                   # one undo step
    # the properties of the bus show the neutral and its supply area, with the detuning
    page.plan.select("bus", "UW")
    areas = gi.supply_areas(session.network).to_dicts()[0]
    assert areas["detuning"] == pytest.approx((I_CE - 28.567) / I_CE)
    await user.should_see(marker="prop-supply-area")                     # the refresh runs in the event loop
    text = _element(user, "prop-supply-area").text
    assert text == ("Speisebereich 20 kV: kapazitiver Erdschlussstrom 27,21 A, Spulenstrom 28,57 A, "
                    "Verstimmung -4,999 %, Reststrom 1,586 A")
    assert _element(user, "prop-neutral").text == "Sternpunkt: 20 kV · gelöscht (Erdschlussspule)"
    # ... and the plan writes it at the bus
    notes = page.plan.annotations(page.plan.colouring())["nodes"]
    assert notes["UW"]["neutrals"] == ["20 kV · gelöscht (Erdschlussspule)"] and "neutrals" not in notes["S1"]
    session.undo()
    assert session.network.neutrals == {}


async def test_a_coil_without_its_current_is_reported_at_the_field(user: User, project):
    page = await _page(user, project)
    await _open_neutral_tab(user)
    user.find(marker="dlg-neutral").click()
    _element(user, "dlg-neutral-kind").set_value("resonant")
    user.find(marker="dialog-apply").click()
    assert _element(user, "dlg-neutral-coil").error == "Hier wird ein Wert gebraucht"
    assert page.session.history() == [] and page.session.network.neutrals == {}
    await user.should_see(marker="element-dialog")


async def test_kind_harmonics_name_and_removal_are_edited_in_the_dialog(user: User, project):
    page = await _page(user, project, language="en")
    session = page.session
    session.apply({"op": "add_neutral", "name": "N", "bus": "UW", "kind": "resonant", "voltage_level": 20.0,
                   "coil_current": 28.0})
    page._after_change()
    await _open_neutral_tab(user)
    assert _element(user, "dlg-neutral-kind").value == "resonant"
    assert _element(user, "dlg-neutral-coil").value == "28"
    assert _element(user, "dlg-neutral-area").text.startswith("Supply area 20 kV: capacitive earth-fault current")
    _element(user, "dlg-neutral-kind").set_value("isolated")
    user.find(marker="dlg-neutral-name").clear().type("20 kV")
    user.find(marker="dlg-neutral-remaining").clear().type("12.5")
    user.find(marker="dlg-neutral-harmonic-im-250").clear().type("4")
    user.find(marker="dialog-apply").click()
    neutral = session.network.neutrals["20 kV"]
    assert isinstance(neutral, gi.IsolatedNeutral) and neutral.remaining_capacitive_current == 12.5
    assert complex(neutral.harmonic_currents[250.0]) == 4j
    assert len(session.history()) == 2                                   # the dialog was one step
    await _open_neutral_tab(user)
    assert _element(user, "dlg-neutral-harmonic-im-250").value == "4"
    user.find(marker="dlg-neutral").click()                              # switch it off
    user.find(marker="dialog-apply").click()
    assert session.network.neutrals == {} and len(session.history()) == 3


async def test_a_low_impedance_neutral_and_the_levels_of_source_and_fault(user: User, project):
    page = await _page(user, project)
    session = page.session
    await _open_neutral_tab(user)
    user.find(marker="dlg-neutral").click()
    _element(user, "dlg-neutral-kind").set_value("low_impedance")
    user.find(marker="dlg-neutral-current-re").clear().type("1000")
    user.find(marker="dlg-source").click()
    user.find(marker="dlg-source-voltage").clear().type("110")
    user.find(marker="dialog-apply").click()
    neutral = next(iter(session.network.neutrals.values()))
    assert isinstance(neutral, gi.LowImpedanceNeutral) and complex(neutral.current) == 1000.0
    assert next(iter(session.network.sources.values())).voltage_level == 110.0
    _fire(user, "open", kind="bus", name="S3")
    await user.should_see(marker="dlg-fault-voltage")
    assert _element(user, "dlg-fault-voltage").value == ""
    user.find(marker="dlg-fault-voltage").clear().type("20")
    user.find(marker="dialog-apply").click()
    assert session.network.faults["F"].voltage_level == 20.0


async def test_the_tables_of_neutral_points_and_supply_areas(user: User, project):
    page = await _page(user, project)
    session = page.session
    session.apply({"op": "add_neutral", "name": "Spule", "bus": "UW", "kind": "resonant", "voltage_level": 20.0,
                   "coil_current": 1.05 * I_CE, "active_residual_current": 0.03 * I_CE})
    page._after_change()
    await user.should_see(marker="grid-neutrals")
    assert [row["name"] for row in _element(user, "grid-neutrals").options["rowData"]] == ["Spule"]
    table = page.supply_table()
    assert [column.name for column in table.columns] == [
        "area", "voltage_level_kV", "feeding_elements", "neutral", "branches", "length_km", "I_CE_A", "I_coil_A",
        "I_residual_A", "detuning"]
    row = table.rows[0]
    assert (row["neutral"], row["feeding_elements"], row["branches"]) == (
        "gelöscht (Erdschlussspule)", "Spule", "K1, K2, K3")
    assert row["detuning"] == pytest.approx(-5.0) and row["I_CE_A"] == pytest.approx(I_CE)
    await asyncio.sleep(0.2)                                             # the refresh runs in the event loop
    assert _element(user, "grid-supply_areas").options["rowData"] == table.rows
    # an edit in the table of the neutral points is a command like any other
    page._on_cell_edit("neutrals", {"colId": "remaining_capacitive_current", "newValue": "30", "oldValue": 0,
                                    "data": {"name": "Spule"}})
    assert session.network.neutrals["Spule"].remaining_capacitive_current == 30.0
    assert page.supply_table().rows[0]["I_CE_A"] == pytest.approx(I_CE + 30.0)


async def test_the_earth_current_by_source_location_is_shown_with_the_result(user: User, project):
    page = await _page(user, project)
    session = page.session
    session.apply({"op": "add_neutral", "name": "Spule", "bus": "UW", "kind": "resonant", "voltage_level": 20.0,
                   "coil_current": 1.05 * I_CE, "active_residual_current": 0.03 * I_CE})
    session.solve("F")
    page._after_change()
    await user.should_see(marker="earth-current")
    _element(user, "earth-current").set_value(True)                      # computed when opened
    await user.should_see(marker="grid-earth_current")
    rows = _element(user, "grid-earth_current").options["rowData"]
    at_50 = [row for row in rows if row["frequency_Hz"] == F]
    assert [row["bus_name"] for row in at_50] == ["UW", "S1", "S2", "S3"]
    assert at_50[0]["sources"] == "Charging current at UW, Neutral Spule"
    assert at_50[0]["r_S"] == pytest.approx(0.3401, abs=5e-5) and at_50[3]["r_S"] == pytest.approx(0.0, abs=1e-12)
    tiles = session.result("F").key_figures.rows[0]
    assert tiles.r == pytest.approx(2.2181, abs=5e-5)                    # the tile shows r above 1 ...
    codes = [d.code for d in session.result("F").diagnostics]
    assert "EARTH_CURRENT_EXCEEDS_FAULT_CURRENT" in codes                # ... and the result says where to look


def test_the_supply_area_colouring_and_the_voltage_level_of_branches(project):
    from groundinsight.api import ProjectSession

    session = ProjectSession.from_file(project)
    session.apply({"op": "add_neutral", "name": "N", "bus": "UW", "kind": "isolated", "voltage_level": 20.0})
    diagram = session.diagram()
    assert MODES[:2] == ("voltage_level", "supply_area")
    import json

    areas = [{**row, **{key: json.loads(row[key]) for key in ("feeding_elements", "buses", "branches")}}
             for row in session.supply_areas().rows]
    shown = colour("supply_area", diagram, areas=areas)
    assert len({shown.edges[name] for name in ("K1", "K2", "K3")}) == 1 and shown.edges["K1"] != NEUTRAL
    assert shown.nodes["S3"] == shown.edges["K1"]
    assert [(entry.label, entry.key) for entry in shown.entries] == [("20 kV · N", False), ("legend_unfed", True)]
    assert set(colour("supply_area", diagram, areas=[]).edges.values()) == {NEUTRAL}
    levels = {row["name"]: 20.0 for row in session.table("branches").rows}
    by_level = colour("voltage_level", diagram, levels=levels)
    assert by_level.edges["K1"] == by_level.nodes["UW"] != NEUTRAL
    assert [entry.label for entry in by_level.entries] == ["20 kV"]
    assert set(colour("voltage_level", diagram).edges.values()) == {NEUTRAL}     # without levels as before
    session.close()


def test_the_type_dialog_edits_capacitance_and_voltage_level():
    fields = dict(FIELDS["branch"])
    assert (fields["c_shield_nf_per_km"], fields["c_earth_nf_per_km"], fields["voltage_level"]) == (
        "number_or_none", "number_or_none", "number_or_none")

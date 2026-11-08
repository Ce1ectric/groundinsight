# gui/tests/test_app.py
"""
The NiceGUI app, driven by NiceGUI's simulated ``User`` (no browser).

Covers the phase-0 skeleton: start page and templates, opening and saving,
the network tables, running a fault as a job with progress and cancel, the
key-figure view, the diagnostics panel with element links, the stale marker,
undo/redo and the language switch.

Every number the GUI shows is checked against the library two independent
ways (rule R9):

1. the pinned frames ``res_fault``/``res_buses`` of a twin of the network,
   built independently and solved with ``groundinsight.run_fault`` -- the
   grid and chart values are compared exactly, the tile texts against the
   GUI's formatting of the frame value;
2. the independent reference solver of the library's test suite
   (``tests/_reference_mna.py``: modified nodal analysis with explicit branch
   currents, another sign frame), loaded from the repository by path.
"""

import importlib.util
import json
import math
import pathlib
import warnings

import pytest
from nicegui.testing import User

import groundinsight as gi
from groundinsight.api import EXAMPLES
from groundinsight.io import TABLE_SCHEMAS

from groundinsight_gui.app import AppOptions, build_app
from groundinsight_gui.formatting import format_number

pytestmark = pytest.mark.anyio

REPOSITORY = pathlib.Path(__file__).resolve().parents[2]

#: A result appears after a job: allow up to 30 s on a loaded machine.
JOB_RETRIES = 300


def _reference_solver():
    """The library suite's independent reference solver, loaded by path."""
    spec = importlib.util.spec_from_file_location(
        "groundinsight_reference_mna", REPOSITORY / "tests" / "_reference_mna.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _finite(value):
    return None if isinstance(value, float) and not math.isfinite(value) else value


def _frame_rows(frame):
    return [{k: _finite(v) for k, v in row.items()} for row in frame.to_dicts()]


def _twin(example: str, fault: str) -> gi.Network:
    """The example built independently and solved with ``run_fault``."""
    net = EXAMPLES[example]()
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        gi.run_fault(net, fault)
    return net


def _element(user: User, marker: str):
    elements = user.find(marker=marker).elements
    assert len(elements) == 1, (marker, len(elements))
    return next(iter(elements))


async def _start(user: User, **options) -> None:
    build_app(AppOptions(**options))
    await user.open("/")


async def _run_fault(user: User) -> None:
    user.find(marker="tab-results").click()
    user.find(marker="run").click()
    await user.should_see(marker="result-footer", retries=JOB_RETRIES)


def _ring_with_many_faults() -> gi.Network:
    """A 40-station ring with a fault at every station -- a slow fault sweep."""
    net = EXAMPLES["mv_ring"](40)
    for bus in list(net.buses)[1:]:
        gi.create_fault(name=f"F_{bus}", bus=bus, network=net)
    return net


# ---------------------------------------------------------------------------
# start page, templates, files
# ---------------------------------------------------------------------------


async def test_the_start_page_offers_the_templates_and_opening_a_file(user: User):
    await _start(user)
    for name in EXAMPLES:
        await user.should_see(marker=f"template-{name}")
    await user.should_see("Projekt öffnen")


async def test_a_template_opens_the_network_tables_of_the_library(user: User):
    await _start(user)
    user.find(marker="template-mv_ring").click()
    await user.should_see("mv_ring_20")
    frames = EXAMPLES["mv_ring"]().to_tables()
    for table in ("buses", "branches", "sources", "faults", "bus_types", "branch_types"):
        grid = _element(user, f"grid-{table}")
        assert [c["field"] for c in grid.options["columnDefs"]] == list(TABLE_SCHEMAS[table])
        assert grid.options["rowData"] == _frame_rows(frames[table]), table


async def test_a_file_opens_from_the_start_page(user: User, tmp_path):
    path = tmp_path / "minimal.json"
    gi.save_network_to_json(EXAMPLES["minimal"](), path)
    await _start(user)
    user.find(marker="open-path").type(str(tmp_path / "missing.json"))
    user.find(marker="open-button").click()
    await user.should_see("missing.json", retries=50)  # the error names the file
    user.find(marker="open-path").clear().type(str(path))
    user.find(marker="open-button").click()
    await user.should_see("SmokeTest", retries=50)


async def test_save_as_writes_the_project_file_without_results(user: User, tmp_path):
    await _start(user, start_example="quickstart")
    await _run_fault(user)
    user.find(marker="save-as").click()
    target = tmp_path / "projekt.json"
    user.find(marker="save-path").type(str(target))
    user.find(marker="save-confirm").click()
    # The notification comes after the write (the path field shows the name
    # from the start, so the name alone is no signal).
    await user.should_see("Gespeichert", retries=50)
    assert str(target) in _element(user, "project-title").text
    await user.should_not_see(marker="dirty")
    payload = json.loads(target.read_text(encoding="utf-8"))
    assert payload["results"] == {} and payload["schema_version"] >= 1
    assert payload["name"] == "QuickstartNet"


# ---------------------------------------------------------------------------
# running a fault: the numbers, two ways
# ---------------------------------------------------------------------------


async def test_running_a_fault_shows_the_numbers_of_the_library(user: User):
    await _start(user, start_example="mv_ring")
    await _run_fault(user)
    twin = _twin("mv_ring", "F1")
    rows = twin.res_fault("F1").to_dicts()
    rms = next(r for r in rows if r["is_rms"])
    at50 = next(r for r in rows if r["frequency_Hz"] == 50.0)

    # Way 1: the library frames of the independently solved twin.
    expected_tiles = {
        "tile-epr_fault": rms["EPR_fault_V"],
        "tile-epr_max": rms["EPR_max_V"],
        "tile-i_f": at50["I_F_A"],
        "tile-r_epr": at50["r_epr"],
        "tile-r_current": at50["r_current"],
        "tile-i_e": at50["I_E_A"],
        "tile-z_e": at50["Z_E_Ohm"],
    }
    for marker, value in expected_tiles.items():
        assert _element(user, marker).text == format_number(value, "de"), marker
    assert rms["EPR_max_bus"] in _element(user, "tile-epr_max-note").text
    key_grid = _element(user, "grid-key_figures")
    assert key_grid.options["rowData"] == _frame_rows(twin.res_fault("F1"))
    chart = _element(user, "epr-chart")
    shown = dict(zip(chart.options["xAxis"]["data"],
                     [point["value"] for point in chart.options["series"][0]["data"]]))
    buses = twin.res_buses("F1").to_dicts()
    assert shown == {r["bus_name"]: r["EPR_V"] for r in buses if r["is_rms"]}

    # Way 2: the independent reference solver.
    ref = _reference_solver()
    rd = ref.reference_network(EXAMPLES["mv_ring"](), 50.0, "F1")
    mapped = ref.to_package_frame(ref.solve_oneway(rd, thevenin_phase="norton"))
    for bus, value in shown.items():
        assert value == pytest.approx(abs(mapped["u"][bus]), rel=1e-10), bus
    assert _element(user, "tile-epr_fault").text == format_number(abs(mapped["u"]["b10"]), "de")
    z_e = abs(ref.driving_point_impedance(rd, "b10"))
    assert _element(user, "tile-z_e").text == format_number(z_e, "de")


async def test_cancel_stops_a_fault_sweep(user: User):
    await _start(user, start_example="many_faults", templates={"many_faults": _ring_with_many_faults})
    user.find(marker="tab-results").click()
    user.find(marker="run-all").click()
    await user.should_see(marker="cancel", retries=JOB_RETRIES)
    user.find(marker="cancel").click()
    await user.should_see("Abgebrochen", retries=JOB_RETRIES)
    await user.should_not_see(marker="result-footer")


# ---------------------------------------------------------------------------
# editing, stale marker, undo
# ---------------------------------------------------------------------------


async def test_an_edit_marks_the_result_stale_and_undo_makes_it_current(user: User):
    await _start(user, start_example="mv_ring")
    await _run_fault(user)
    await user.should_not_see(marker="stale-banner")
    user.find(marker="grid-branches").trigger(
        "cellValueChanged",
        {"colId": "length", "newValue": "0,6", "oldValue": 0.5, "data": {"name": "L_05_06"}},
    )
    await user.should_see(marker="stale-banner")
    await user.should_see(marker="dirty")
    grid = _element(user, "grid-branches")
    assert next(r for r in grid.options["rowData"] if r["name"] == "L_05_06")["length"] == 0.6
    user.find(marker="undo").click()
    await user.should_not_see(marker="stale-banner")
    grid = _element(user, "grid-branches")
    assert next(r for r in grid.options["rowData"] if r["name"] == "L_05_06")["length"] == 0.5
    user.find(marker="redo").click()
    await user.should_see(marker="stale-banner")


async def test_grid_events_send_only_the_fields_they_use(user: User):
    """AG Grid 36 puts its internal, circular context into row events; the
    browser cannot serialise such an event and drops it -- a click on a row
    never reached the server, so "delete selected row" did nothing (found
    driving the app in Chromium). The grids subscribe with the fields used."""
    await _start(user, start_example="quickstart")
    for table in ("buses", "branches", "sources", "faults", "bus_types", "branch_types"):
        grid = _element(user, f"grid-{table}")
        listeners = {listener.type: listener.args for listener in grid._event_listeners.values()}
        assert listeners["rowClicked"] == [["data", "rowId"]], table
        assert listeners["cellValueChanged"] == [["colId", "newValue", "oldValue", "data"]], table


async def test_a_rejected_edit_is_reported_and_changes_nothing(user: User):
    await _start(user, start_example="quickstart")
    user.find(marker="grid-buses").trigger(
        "cellValueChanged",
        {"colId": "specific_earth_resistance", "newValue": "-500", "data": {"name": "bus_fault"}},
    )
    await user.should_see("Änderung abgelehnt")
    grid = _element(user, "grid-buses")
    assert next(r for r in grid.options["rowData"] if r["name"] == "bus_fault")["specific_earth_resistance"] == 100.0
    assert not _element(user, "undo").enabled


# ---------------------------------------------------------------------------
# diagnostics, check, language
# ---------------------------------------------------------------------------


async def test_diagnostics_name_their_elements_and_link_to_them(user: User):
    await _start(user, start_example="quickstart")
    await _run_fault(user)
    await user.should_see("SCALINGS_INCOMPLETE")
    user.find(marker="goto-fault-fault_at_remote_bus").click()
    assert _element(user, "tabs").value == "network"


async def test_the_check_lists_the_findings_of_the_library(user: User):
    await _start(user, start_example="quickstart")
    user.find(marker="tab-check").click()
    await user.should_see(marker="check-ok")
    # Removing the only source breaks the network; the check shows it at once.
    user.find(marker="grid-sources").trigger("rowClicked", {"data": {"name": "substation_infeed"}})
    user.find(marker="remove-sources").click()
    await user.should_not_see(marker="check-ok")
    twin = EXAMPLES["quickstart"]()
    twin.remove_source("substation_infeed")
    findings = gi.check_network(twin)
    assert "NO_SOURCE" in {d.code for d in findings}
    for finding in findings:
        await user.should_see(finding.code)
    user.find(marker="undo").click()
    await user.should_see(marker="check-ok")


async def test_the_language_switch_translates_the_interface(user: User):
    await _start(user)
    await user.should_see("Projekt öffnen")
    toggle = _element(user, "lang")
    with user:
        toggle.set_value("en")
    await user.should_see("Open project")
    await user.should_not_see("Projekt öffnen")

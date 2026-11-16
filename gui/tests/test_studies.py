# gui/tests/test_studies.py
"""
The *Calculation* menu, the evaluation tabs and import/export, driven by ``User``.

Every study is started the way a user starts it -- menu, dialog, *Run* -- runs
as a job, and the table the GUI shows (the AG Grid's row data) is compared,
row by row, with the frame of the direct library call on an independently
built network: the same numbers, or the test fails (briefing WP5).
"""

import asyncio
import math
import warnings

import pytest
from nicegui.testing import User

import groundinsight as gi
from groundinsight.api import EXAMPLES

from groundinsight_gui.app import AppOptions, build_app
from groundinsight_gui.colouring import NEUTRAL, utilisation_colour

pytestmark = pytest.mark.anyio

JOB_RETRIES = 300


def _cell(value):
    if isinstance(value, float) and not math.isfinite(value):
        return None
    if isinstance(value, (list, tuple)):
        return str(value)
    return value


def _rows(frame):
    return [{k: _cell(v) for k, v in row.items()} for row in frame.to_dicts()]


def _quiet(function, *args, **kwargs):
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        return function(*args, **kwargs)


def _thermal_network():
    net = gi.create_network(name="thermal", frequencies=[50.0])
    bus_type = gi.BusType(name="b", system_type="s", voltage_level=20.0, impedance_formula="rho*0 + 0.5")
    cable = gi.BranchType(name="c", grounding_conductor=True,
                          self_impedance_formula="(rho*0 + 0.25 + j*f*0.012)*l",
                          mutual_impedance_formula="(rho*0 + 0.05 + j*f*0.012)*l",
                          conductor_material="Cu", cross_section_mm2=50.0,
                          theta_initial_C=20.0, theta_final_C=300.0)
    for name in ("b1", "b2"):
        gi.create_bus(name=name, type=bus_type, network=net)
    gi.create_branch(name="c1", type=cable, from_bus="b1", to_bus="b2", length=1.0, network=net)
    gi.create_source(name="src", bus="b1", values={50.0: 5000.0}, network=net)
    gi.create_fault(name="F", bus="b2", scalings={50.0: 1.0}, network=net)
    return net


def _station_network():
    """Two stations with a touch factor, fed from a remote substation."""
    rhos, freqs = (50.0, 100.0, 200.0), (50.0,)
    table = gi.EarthingCharacteristic(rho_Ohm_m=rhos, frequency_Hz=freqs,
                                      impedance_Ohm=[[r / 50.0] for r in rhos],
                                      k_touch=[[0.3], [0.35], [0.4]])
    net = gi.create_network(name="stations", frequencies=list(freqs))
    station = gi.BusType(name="station", system_type="MV/LV", voltage_level=20.0,
                         impedance_formula="rho / 50", characteristic=table)
    remote = gi.BusType(name="remote", system_type="HV/MV", voltage_level=110.0,
                        impedance_formula="0.2 + rho * 0")
    cable = gi.BranchType(name="cable", grounding_conductor=True, self_impedance_formula="(0.2 + j*0.6)*l",
                          mutual_impedance_formula="j*0.5*l", phase_impedance_formula="(0.1 + j*0.3)*l")
    gi.create_bus(name="Q", type=remote, network=net)
    gi.create_bus(name="S1", type=station, specific_earth_resistance=200.0, network=net)
    gi.create_bus(name="S2", type=station, specific_earth_resistance=100.0, network=net)
    gi.create_branch(name="K1", type=cable, from_bus="Q", to_bus="S1", length=2.0, network=net)
    gi.create_branch(name="K2", type=cable, from_bus="S1", to_bus="S2", length=1.0, network=net)
    gi.create_source(name="I", bus="Q", values={50.0: 1000.0}, network=net)
    gi.create_fault(name="F", bus="S1", scalings={50.0: 1.0}, network=net)
    return net


def _transient_network():
    net = gi.create_network(name="pulse", frequencies=[50.0])
    grid = gi.BusType(name="grid", system_type="Substation", voltage_level=20.0,
                      impedance_formula="rho * 0.01", R_formula="rho * 0.01")
    line = gi.BranchType(name="line", grounding_conductor=True, self_impedance_formula="0.25 * l",
                         mutual_impedance_formula="0 * l", R_self_formula="0.25 * l")
    for name in ("B1", "B2"):
        gi.create_bus(name=name, type=grid, network=net)
    gi.create_branch(name="L1", type=line, from_bus="B1", to_bus="B2", length=1.0, network=net)
    gi.create_source(name="S1", bus="B1", values={50.0: 100.0}, network=net)
    gi.create_fault(name="F1", bus="B2", scalings={50.0: 1.0}, network=net)
    return net


TEMPLATES = {**EXAMPLES, "thermal": _thermal_network, "stations": _station_network, "pulse": _transient_network}


async def _page(user: User, template: str):
    pages = build_app(AppOptions(start_example=template, templates=TEMPLATES))
    await user.open("/")
    return pages[0]


def _element(user: User, marker: str):
    return next(iter(user.find(marker=marker).elements))


async def _study(user: User, study: str, **fields):
    """Open the dialog of a study from the menu, fill it, run it, wait for the result."""
    user.find(marker=f"calc-{study}").click()
    await user.should_see(marker="study-dialog")
    for key, value in fields.items():
        element = _element(user, f"study-{key}")
        element.set_value(value)
    user.find(marker="study-run").click()
    await user.should_see(marker="study-result", retries=JOB_RETRIES)


async def _run_fault(user: User) -> None:
    user.find(marker="tab-results").click()
    user.find(marker="run").click()
    await user.should_see(marker="result-footer", retries=JOB_RETRIES)


def _shown(user: User, table: str):
    return _element(user, f"study-table-{table}").options["rowData"]


async def test_outage_study(user: User):
    await _page(user, "mv_ring")
    await _study(user, "outage", branches=["L_05_06", "L_15_16"])
    study = _quiet(gi.run_outage_study, EXAMPLES["mv_ring"](), fault="F1", scenarios=[
        gi.Outage(name="L_05_06 open", disabled_branches=["L_05_06"]),
        gi.Outage(name="L_15_16 open", disabled_branches=["L_15_16"])])
    assert _shown(user, "buses") == _rows(study.compare_buses())
    await user.should_see(marker="study-chart")


async def test_rho_f_sweep_with_statistics(user: User):
    await _page(user, "quickstart")
    await _study(user, "sweep", buses=["bus_fault"], rho="50; 100; 200")
    points = [gi.SweepPoint(label=f"rho {r:g}", bus_rho={"bus_fault": r}, parameters={"rho_Ohm_m": r})
              for r in (50.0, 100.0, 200.0)]
    study = _quiet(gi.run_sweep, EXAMPLES["quickstart"](), fault="fault_at_remote_bus", points=points)
    assert _shown(user, "summary") == _rows(gi.summarize(study.buses(), "EPR_V", by=["bus_name"]))
    assert _shown(user, "buses") == _rows(study.buses())


async def test_cuts(user: User):
    await _page(user, "mv_ring")
    await _study(user, "cuts", branches=["L_09_10", "L_10_11"])
    twin = EXAMPLES["mv_ring"]()
    _quiet(gi.run_fault, twin, "F1")
    frame = gi.analyze_cuts(twin, fault="F1", cuts=[gi.Cut(name="L_09_10", branches=["L_09_10"]),
                                                     gi.Cut(name="L_10_11", branches=["L_10_11"])]).to_polars()
    assert _shown(user, "cuts") == _rows(frame)


async def test_bus_response(user: User):
    await _page(user, "mv_ring")
    await _study(user, "bus_response", electrodes="1; 5")
    twin = EXAMPLES["mv_ring"]()
    _quiet(gi.run_fault, twin, "F1")
    response = gi.bus_response(twin, fault="F1")
    assert _shown(user, "extremes") == _rows(response.extremes())
    assert _shown(user, "sweep") == _rows(response.sweep([1.0, 5.0], labels=["1 Ohm", "5 Ohm"]))


async def test_max_rho_scaling(user: User):
    await _page(user, "minimal")
    await _study(user, "max_rho", u_limit="500")
    net = EXAMPLES["minimal"]()
    report = _quiet(gi.find_max_rho_scaling, net, "fault", list(net.buses), 500.0)
    shown = {r["bus_name"]: r["rho_max"] for r in _shown(user, "rho_max")}
    assert shown == {k: float(v) for k, v in report["rho_max"].items()}


async def test_thermal_check(user: User):
    await _page(user, "thermal")
    await _study(user, "thermal", t_k_s="1", r_to_x="0,1")
    twin = _thermal_network()
    _quiet(gi.run_fault, twin, "F")
    assert _shown(user, "limits") == _rows(gi.check_conductor_limits(twin, "F", t_k_s=1.0, r_to_x=0.1))


async def test_touch_check_and_the_utilisation_colouring(user: User):
    page = await _page(user, "stations")
    await _study(user, "touch", u_limit="0,1:600; 1:100", t_k_s="0,5")
    twin = _station_network()
    _quiet(gi.run_fault, twin, "F")
    frame = gi.check_touch_voltages(twin, "F", [(0.1, 600.0), (1.0, 100.0)], 0.5)
    assert _shown(user, "touch") == _rows(frame)

    # The limits of the project: the criteria dialog of the network tab.
    user.find(marker="tab-network").click()
    user.find(marker="criteria-edit").click()
    await user.should_see(marker="criteria-dialog")
    _element(user, "criteria-u_touch_limit").set_value("0,1:600; 1:100")
    _element(user, "criteria-t_f_s").set_value("0,5")
    _element(user, "criteria-u_e_limit_V").set_value("400")
    user.find(marker="criteria-apply").click()
    criteria = page.session.criteria()
    assert criteria.u_touch_limit == [(0.1, 600.0), (1.0, 100.0)] and criteria.u_e_limit_V == 400.0

    await _run_fault(user)
    user.find(marker="tab-plan").click()
    user.find(marker="colour-util_touch").click()
    canvas = _element(user, "canvas")
    # Second way: the library's check, in percent, on the ramp of the GUI.
    percent = {r["bus_name"]: None if r["utilisation"] is None else 100.0 * r["utilisation"]
               for r in frame.iter_rows(named=True)}
    top = max(150.0, max(v for v in percent.values() if v is not None))
    expected = {b: NEUTRAL if v is None else utilisation_colour(v, top) for b, v in percent.items()}
    assert canvas._props["colours"]["nodes"] == expected
    assert page.session.diagram_model.color_scheme == "util_touch"
    # EPR against U_E: the RMS EPR of the twin over 400 V.
    user.find(marker="colour-util_epr").click()
    epr = {r["bus_name"]: 100.0 * r["EPR_V"] / 400.0 for r in frame.iter_rows(named=True)}
    top = max(150.0, max(epr.values()))
    assert canvas._props["colours"]["nodes"] == {b: utilisation_colour(v, top) for b, v in epr.items()}


async def test_transient(user: User):
    await _page(user, "pulse")
    await _study(user, "transient", amplitude="100", t_off="0,02", t_end="0,04", dt="0,001",
                 observe_branches=["L1"])
    from groundinsight.simulation import waveforms

    study = gi.TransientStudy(_transient_network(), "F1")
    study.set_source_waveform("S1", waveforms.step(100.0, t_off=0.02))
    study.set_observation(buses=["B1", "B2"], branches=["L1"])
    result = study.solve(t_end=0.04, dt=0.001)
    assert _shown(user, "series") == _rows(result.to_polars())
    chart = _element(user, "study-chart")
    assert [s["name"] for s in chart.options["series"]] == ["B1 [V]", "B2 [V]", "L1 [A]"]
    # Only the chosen signals are drawn.
    _element(user, "transient-signals").set_value(["epr:B2"])
    assert [s["name"] for s in chart.options["series"]] == ["B2 [V]"]
    # Statistics over a window: the library's own, a second way.
    _element(user, "transient-t-start").set_value("0,005")
    _element(user, "transient-t-end").set_value("0,03")
    user.find(marker="transient-statistics-run").click()
    shown = _element(user, "transient-statistics").options["rowData"]
    assert shown == _rows(result.statistics(0.005, 0.03))


async def test_reference_cases(user: User):
    await _page(user, "minimal")
    await _study(user, "reference_cases")
    assert _shown(user, "cases") == _rows(gi.run_reference_cases())


async def test_single_fault_from_the_menu(user: User):
    page = await _page(user, "minimal")
    user.find(marker="calc-solve").click()
    await user.should_see(marker="result-footer", retries=JOB_RETRIES)
    assert page.tab == "results" and page.session.stale_results() == []


async def test_an_evaluation_tab_with_a_chart_of_the_result(user: User, tmp_path):
    page = await _page(user, "mv_ring")
    user.find(marker="view-add").click()
    await user.should_see(marker="view-dialog")
    user.find(marker="view-title").clear().type("Spannungen")
    user.find(marker="view-create").click()
    await user.should_see(marker="block-add-v1")
    assert page.session.views[0]["title"] == "Spannungen" and page.tab == "v1"
    user.find(marker="block-add-v1").click()
    await user.should_see(marker="block-dialog")
    _element(user, "block-elements").set_value(["b09", "b10", "b11"])
    user.find(marker="block-create").click()
    assert page.session.views[0]["blocks"][0]["quantity"] == "EPR_V"
    user.find(marker="calc-solve").click()
    await user.should_see(marker="block-chart-v1-0", retries=JOB_RETRIES)
    chart = _element(user, "block-chart-v1-0")
    twin = EXAMPLES["mv_ring"]()
    _quiet(gi.run_fault, twin, "F1")
    frame = twin.res_buses("F1")
    rms = {r["bus_name"]: r["EPR_V"] for r in frame.to_dicts() if r["frequency_Hz"] is None}
    assert chart.options["series"][0]["data"] == [rms[b] for b in ("b09", "b10", "b11")]
    page.session.save(str(tmp_path / "p.json"))
    assert gi.load_project(tmp_path / "p.json").views[0]["title"] == "Spannungen"
    user.find(marker="undo").click()  # the block
    user.find(marker="undo").click()  # the tab
    assert page.session.views == []


async def _new_session(page, old, user):
    """Wait until an import (off the event loop) has replaced the session."""
    for _ in range(200):
        if page.session is not old:
            return page.session
        await asyncio.sleep(0.05)
    raise AssertionError("the import did not replace the project")


async def test_tables_export_and_import(user: User, tmp_path):
    page = await _page(user, "mv_ring")
    user.find(marker="io-export_tables").click()
    await user.should_see(marker="io-dialog")
    user.find(marker="io-path").type(str(tmp_path / "t"))
    user.find(marker="io-apply").click()
    await user.should_see("Exportiert", retries=50)
    old = page.session
    user.find(marker="io-import_tables").click()
    await user.should_see(marker="io-dialog")
    user.find(marker="io-path").type(str(tmp_path / "t"))
    user.find(marker="io-apply").click()
    imported = (await _new_session(page, old, user)).network
    direct = gi.network_from_tables(str(tmp_path / "t"))
    assert imported.model_dump_json() == direct.model_dump_json()
    assert {n: b.impedance for n, b in imported.buses.items()} == {
        n: b.impedance for n, b in old.network.buses.items()}


async def test_pandapower_import(user: User, tmp_path):
    pp = pytest.importorskip("pandapower")
    import pandapower.networks as pn

    path = tmp_path / "net.json"
    pp.to_json(pn.example_simple(), str(path))
    page = await _page(user, "mv_ring")
    old = page.session
    user.find(marker="io-import_pandapower").click()
    await user.should_see(marker="io-dialog")
    user.find(marker="io-path").type(str(path))
    user.find(marker="io-rho").clear().type("150")
    user.find(marker="io-apply").click()
    imported = (await _new_session(page, old, user)).network
    defaults = gi.ImportDefaults(rho=150.0, frequencies=list(old.network.frequencies),
                                 default_bus_type=old.network.buses["b01"].type,
                                 default_branch_type=old.network.branches["L_01_02"].type)
    direct = _quiet(gi.from_pandapower, pp.from_json(str(path)), defaults=defaults, voltage_level_kV=20.0)
    assert imported.model_dump_json() == direct.model_dump_json()
    # The importer's reports (a transformer and a bus-to-bus switch are skipped) reach the user.
    codes = [d.code for d in page.session.import_diagnostics]
    assert codes and all(user.notify.contains(code) for code in codes)

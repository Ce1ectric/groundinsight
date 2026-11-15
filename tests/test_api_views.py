# tests/test_api_views.py
"""
Evaluation tabs (``views``) of a project, through the service layer.

A view is a titled list of blocks -- a chart or a table of one result
quantity for chosen elements, at one frequency or as RMS, for one fault.
``AddView``/``UpdateView``/``RemoveView`` edit them undoably, the project file
stores them, and ``ProjectSession.view_table`` returns a block's numbers,
compared here with ``res_buses``/``res_branches``/``res_fault`` of an
independently solved twin.
"""

import json
import warnings

import pytest

import groundinsight as gi
import groundinsight.api as api
from tests._warning_policy import acknowledge

pytestmark = acknowledge("PHASE_PROXY", "SCALINGS_INCOMPLETE")

VIEW = {"id": "v1", "title": "Spannungen", "blocks": [
    {"kind": "chart", "source": "buses", "quantity": "EPR_V", "elements": ["b09", "b10", "b11"]},
    {"kind": "table", "source": "branches", "quantity": "I_branch_A", "frequency_Hz": 50.0},
]}


def _twin():
    net = api.EXAMPLES["mv_ring"]()
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        gi.run_fault(net, "F1")
    return net


def test_views_are_added_changed_removed_and_undone():
    session = api.ProjectSession.from_example("mv_ring")
    session.apply(api.AddView(view=VIEW))
    assert [v["id"] for v in session.views] == ["v1"] and session.dirty
    changed = dict(VIEW, title="EPR")
    session.apply(api.UpdateView(view=changed))
    assert session.views[0]["title"] == "EPR"
    session.apply(api.RemoveView(id="v1"))
    assert session.views == []
    session.undo()
    session.undo()
    assert session.views[0]["title"] == "Spannungen"
    assert session.history() == ["add view 'Spannungen'"]
    with pytest.raises(api.CommandError, match="exists"):
        session.apply(api.AddView(view=VIEW))
    with pytest.raises(api.CommandError):
        session.apply(api.AddView(view={"id": "v2", "title": "x", "blocks": [{"kind": "pie"}]}))
    with pytest.raises(api.CommandError, match="no view"):
        session.apply(api.RemoveView(id="nope"))
    assert len(session.history()) == 1


def test_views_travel_in_the_project_file(tmp_path):
    session = api.ProjectSession.from_example("mv_ring")
    session.apply(api.AddView(view=VIEW))
    session.save(str(tmp_path / "p.json"))
    again = api.ProjectSession.from_file(str(tmp_path / "p.json"))
    assert again.views == session.views
    assert api.ViewDTO.model_validate(again.views[0]).blocks[0].quantity == "EPR_V"


def test_a_block_shows_the_numbers_of_the_result():
    session = api.ProjectSession.from_example("mv_ring")
    view = api.ViewDTO.model_validate(VIEW)
    empty = session.view_table(view.blocks[0])
    assert empty.rows == []  # no result yet
    session.solve("F1")
    twin = _twin()
    chart = session.view_table(view.blocks[0])
    frame = twin.res_buses("F1")
    rms = {r["bus_name"]: r["EPR_V"] for r in frame.to_dicts() if r["frequency_Hz"] is None}
    assert [(r["element"], r["value"]) for r in chart.rows] == [(b, rms[b]) for b in ("b09", "b10", "b11")]
    table = session.view_table(view.blocks[1])
    at50 = {r["branch_name"]: r["I_branch_A"] for r in twin.res_branches("F1").to_dicts()
            if r["frequency_Hz"] == 50.0}
    assert {r["element"]: r["value"] for r in table.rows} == at50
    key = session.view_table(api.ViewBlockDTO(kind="table", source="key_figures", quantity="U_E_V"))
    assert key.rows == [{"element": "F1", "value": twin.res_fault("F1").row(-1, named=True)["U_E_V"]}]
    with pytest.raises(ValueError, match="no column"):
        session.view_table(api.ViewBlockDTO(kind="chart", source="buses", quantity="EPR_X"))


def test_the_colour_scheme_is_kept_in_the_diagram():
    session = api.ProjectSession.from_example("minimal")
    session.apply(api.SetColourScheme(color_scheme="epr"))
    assert session.diagram_model.color_scheme == "epr" and session.diagram().color_scheme == "epr"
    session.undo()
    assert session.diagram_model.color_scheme == "default"
    for command in (api.AddView(view=VIEW), api.UpdateView(view=VIEW), api.RemoveView(id="v1"),
                    api.SetColourScheme(color_scheme="type")):
        assert api.parse_command(json.loads(command.model_dump_json())) == command


def test_tables_export_and_import_round_trip(tmp_path, monkeypatch):
    monkeypatch.setenv("GI_TABLES", str(tmp_path / "tables"))
    session = api.ProjectSession.from_example("mv_ring")
    written = session.export_tables("$GI_TABLES")
    assert sorted(p.split("/")[-1] for p in written) == sorted(f"{t}.csv" for t in session.network.to_tables())
    again = api.ProjectSession.from_tables("$GI_TABLES")
    # Second way: the library's own reader of the same directory.
    direct = gi.network_from_tables(str(tmp_path / "tables"))
    assert again.network.model_dump_json() == direct.model_dump_json()
    assert set(again.network.buses) == set(session.network.buses)
    for name, bus in again.network.buses.items():
        assert bus.impedance == session.network.buses[name].impedance


def test_a_pandapower_net_is_imported_with_the_catalogue_types(tmp_path):
    pp = pytest.importorskip("pandapower")
    import pandapower.networks as pn

    path = tmp_path / "net.json"
    pp.to_json(pn.example_simple(), str(path))
    session = api.ProjectSession.from_example("mv_ring")
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        imported = session.import_pandapower(str(path), voltage_level_kV=20.0, rho=150.0,
                                             bus_type="MV_substation", branch_type="NA2XS_300_25")
        defaults = gi.ImportDefaults(rho=150.0, frequencies=list(session.network.frequencies),
                                     default_bus_type=session.network.buses["b01"].type,
                                     default_branch_type=session.network.branches["L_01_02"].type)
        direct = gi.from_pandapower(pp.from_json(str(path)), defaults=defaults, voltage_level_kV=20.0)
    assert imported.network.model_dump_json() == direct.model_dump_json()
    assert imported.location is None and len(imported.network.buses) > 0
    with pytest.raises(api.ProjectError, match="bus type"):
        session.import_pandapower(str(path), voltage_level_kV=20.0, rho=150.0, bus_type="nope",
                                  branch_type="NA2XS_300_25")

# tests/test_api_characteristics.py
"""
Earthing characteristics through the service layer, and the groundfield JSON.

``SetCharacteristic``, ``ClearCharacteristic`` and ``ImportCharacteristic``
attach a characteristic to a bus or a bus type, undoably; the session shows
it as a ``CharacteristicDTO``. ``ImportCharacteristic`` reads the BusType
document groundfield writes (schema ``groundfield.bustype`` version 1) with
:func:`groundinsight.integrations.groundfield.characteristic_from_bustype_spec`,
which needs no groundfield installation.

The documents here are built the way groundfield's exporter builds them --
samples of the standard form ``k1 rho + (k2 + j k3) f + (k4 + j k5) f rho`` on
a rho x f grid, the formula as text, the metadata block -- so the imported
table can be checked a second way against the formula it was sampled from.
"""

import cmath
import hashlib
import json
import math

import pytest

import groundinsight as gi
import groundinsight.api as api
from groundinsight.integrations import groundfield as gf
from tests._characteristic_cases import _formula, _network, _table

K = {"k1": 0.004, "k2": 1.0e-5, "k3": 2.0e-4, "k4": 1.0e-8, "k5": 3.0e-7}
SPEC_RHO = (50.0, 100.0, 200.0, 400.0)
SPEC_F = (50.0, 150.0, 250.0, 1000.0)
SPEC_FORMULA = "0.004*rho + (1e-05 + 0.0002*j)*f + (1e-08 + 3e-07*j)*f*rho"


def _standard(rho, f):
    return K["k1"] * rho + complex(K["k2"], K["k3"]) * f + complex(K["k4"], K["k5"]) * f * rho


def _spec(**changes):
    """A BusType document as groundfield's ``to_bustype_dict`` writes it."""
    points = [(r, f) for r in SPEC_RHO for f in SPEC_F]
    document = {
        "schema": "groundfield.bustype",
        "schema_version": 1,
        "name": "ONS Typ A",
        "description": "ring earth electrode 5 m x 5 m",
        "system_type": "MV/LV",
        "voltage_level": 20.0,
        "impedance_formula": SPEC_FORMULA,
        "samples": {
            "frequency_Hz": [f for _, f in points],
            "rho_Ohm_m": [r for r, _ in points],
            "Z_real_Ohm": [_standard(r, f).real for r, f in points],
            "Z_imag_Ohm": [_standard(r, f).imag for r, f in points],
        },
        "metadata": {
            "fit_method": "rho_f_standard",
            "quantity": "cluster",
            "rho_definition": "effective_resistivity",
            "rho_preset": "station",
            "electrode_name": "ring 5x5",
            "soil_summary": "two layers, rho_1 = 100 Ohm m, h_1 = 2 m, rho_2 = 500 Ohm m",
            "source": "groundfield 2026.11.0",
            "created_at_utc": "2026-11-15T10:00:00Z",
        },
    }
    document.update(changes)
    return document


def _session():
    return api.ProjectSession(_network(rho=200.0))


def _inputs(session):
    return session.network.model_dump_json(exclude={"results", "paths"})


# --- the groundfield document -----------------------------------------------


def test_a_bustype_document_becomes_a_characteristic():
    characteristic = gf.characteristic_from_bustype_spec(_spec())
    assert characteristic.rho_Ohm_m == SPEC_RHO and characteristic.frequency_Hz == SPEC_F
    assert characteristic.impedance_source == "table"
    assert characteristic.impedance_formula == SPEC_FORMULA
    assert characteristic.name == "ONS Typ A"
    assert characteristic.k_touch is None and characteristic.k_transfer is None
    for i, r in enumerate(SPEC_RHO):
        for j, f in enumerate(SPEC_F):
            assert characteristic.impedance_Ohm[i][j] == _standard(r, f)  # exact
    provenance = characteristic.provenance
    assert provenance.source == "groundfield.bustype"
    assert provenance.groundfield_version == "2026.11.0"
    assert provenance.rho_preset == "station"
    assert provenance.created == "2026-11-15T10:00:00Z"
    assert provenance.geometry_id == "ring 5x5"
    assert provenance.soil == {"summary": _spec()["metadata"]["soil_summary"]}
    canonical = json.dumps(_spec(), sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    assert provenance.input_hash == "sha256:" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def test_samples_in_any_order_give_the_same_table():
    document = _spec()
    samples = document["samples"]
    order = list(reversed(range(len(samples["rho_Ohm_m"]))))
    shuffled = _spec(samples={key: [values[i] for i in order] for key, values in samples.items()})
    a = gf.characteristic_from_bustype_spec(document)
    b = gf.characteristic_from_bustype_spec(shuffled)
    assert a.impedance_Ohm == b.impedance_Ohm


def _bilinear_by_hand(rho, f):
    """Second way: the documented interpolation -- ln|Z| and the angle bilinear
    in (ln rho, ln f) -- written out for the cell around (rho, f)."""
    r0, r1 = max(r for r in SPEC_RHO if r <= rho), min(r for r in SPEC_RHO if r >= rho)
    f0, f1 = max(x for x in SPEC_F if x <= f), min(x for x in SPEC_F if x >= f)
    wr = math.log(rho / r0) / math.log(r1 / r0)
    wf = math.log(f / f0) / math.log(f1 / f0)

    def blend(g):
        return ((1 - wr) * (1 - wf) * g(r0, f0) + wr * (1 - wf) * g(r1, f0)
                + (1 - wr) * wf * g(r0, f1) + wr * wf * g(r1, f1))

    magnitude = math.exp(blend(lambda r, x: math.log(abs(_standard(r, x)))))
    return cmath.rect(magnitude, blend(lambda r, x: cmath.phase(_standard(r, x))))


def test_the_imported_table_interpolates_as_documented():
    characteristic = gf.characteristic_from_bustype_spec(_spec())
    values = characteristic.evaluate(150.0, [100.0, 500.0])
    for z, f in zip(values.impedance, [100.0, 500.0], strict=True):
        expected = _bilinear_by_hand(150.0, f)
        assert abs(z - expected) <= 1e-12 * abs(expected)
        # Against the fitted formula itself the table is off by the interpolation
        # error of the cell: the standard form is not a power law in f.
        assert abs(z - _standard(150.0, f)) / abs(_standard(150.0, f)) < 0.07
    at_grid = characteristic.evaluate(200.0, [250.0])
    assert at_grid.impedance[0] == _standard(200.0, 250.0)


def test_a_single_rho_document_of_a_vector_fit_is_a_one_row_table():
    points = [(100.0, f) for f in SPEC_F]
    document = _spec(impedance_formula="2 + j*f*0.001", samples={
        "frequency_Hz": [f for _, f in points], "rho_Ohm_m": [r for r, _ in points],
        "Z_real_Ohm": [2.0] * 4, "Z_imag_Ohm": [f * 0.001 for f in SPEC_F]})
    characteristic = gf.characteristic_from_bustype_spec(document)
    assert characteristic.rho_Ohm_m == (100.0,) and len(characteristic.impedance_Ohm) == 1


def test_a_document_without_samples_is_imported_as_its_formula():
    document = _spec(samples={})
    with pytest.raises(ValueError, match="no samples"):
        gf.characteristic_from_bustype_spec(document)
    characteristic = gf.characteristic_from_bustype_spec(document, impedance_source="formula",
                                                         rho_Ohm_m=SPEC_RHO, frequency_Hz=SPEC_F)
    assert characteristic.impedance_source == "formula" and characteristic.impedance_Ohm is None


@pytest.mark.parametrize(
    ("change", "message"),
    [
        ({"schema": "groundfield.other"}, "schema"),
        ({"schema_version": 2}, "schema_version"),
        ({"impedance_formula": "__import__('os')"}, "impedance_formula"),
        ({"samples": {"frequency_Hz": [50.0], "rho_Ohm_m": [100.0], "Z_real_Ohm": [None],
                      "Z_imag_Ohm": [0.0]}}, "not finite"),
        ({"samples": {"frequency_Hz": [50.0, 150.0, 50.0], "rho_Ohm_m": [100.0, 100.0, 200.0],
                      "Z_real_Ohm": [1.0, 1.0, 2.0], "Z_imag_Ohm": [0.0, 0.0, 0.0]}}, "full grid"),
        ({"samples": {"frequency_Hz": [50.0, 50.0], "rho_Ohm_m": [100.0, 100.0],
                      "Z_real_Ohm": [1.0, 1.0], "Z_imag_Ohm": [0.0, 0.0]}}, "twice"),
        ({"samples": {"frequency_Hz": [50.0], "rho_Ohm_m": [100.0, 200.0],
                      "Z_real_Ohm": [1.0], "Z_imag_Ohm": [0.0]}}, "equal length"),
        ({"samples": {"frequency_Hz": [50.0], "rho_Ohm_m": [100.0],
                      "Z_real_Ohm": [-1.0], "Z_imag_Ohm": [0.0]}}, "passive"),
    ],
)
def test_a_malformed_document_is_refused(change, message):
    with pytest.raises(ValueError, match=message):
        gf.characteristic_from_bustype_spec(_spec(**change))


def test_read_bustype_json_reads_a_file(tmp_path):
    path = tmp_path / "ons.json"
    path.write_text(json.dumps(_spec()), encoding="utf-8")
    assert gf.read_bustype_json(path) == gf.characteristic_from_bustype_spec(_spec())
    path.write_text("[1, 2]", encoding="utf-8")
    with pytest.raises(ValueError, match="JSON object"):
        gf.read_bustype_json(path)


# --- commands ---------------------------------------------------------------


def _as_json(characteristic):
    return json.loads(characteristic.model_dump_json())


def test_set_characteristic_on_a_bus_takes_the_table_and_undo_restores():
    session = _session()
    session.solve("F")
    before = _inputs(session)
    report = session.apply(api.SetCharacteristic(target="bus", name="S1",
                                                 characteristic=_as_json(_table())))
    bus = session.network.buses["S1"]
    assert complex(bus.impedance[50.0]) == _formula(200.0, 50.0)  # grid point: exact
    assert bus.k_touch is not None and report.stale_results == ["F"]
    dto = session.characteristic("bus", "S1")
    assert (dto.owner_kind, dto.owner, dto.origin) == ("bus", "S1", "bus")
    session.undo()
    assert _inputs(session) == before and session.stale_results() == []


def test_set_characteristic_on_a_type_reaches_every_user():
    session = _session()
    session.apply(api.SetCharacteristic(target="bus_type", name="station",
                                        characteristic=_as_json(_table())))
    for name in ("S1", "S2"):
        assert session.network.buses[name].type.characteristic == _table()
        assert session.characteristic("bus", name).origin == "bus_type"
    assert session.characteristic("bus", "Q") is None
    assert session.characteristic("bus_type", "station").owner == "station"


def test_clear_characteristic_gives_back_the_formula_like_a_fresh_build():
    session = api.ProjectSession(_network(bus_characteristic=_table(), rho=200.0))
    before = _inputs(session)
    session.apply(api.ClearCharacteristic(target="bus", name="S1"))
    after = _inputs(session)
    fresh = _network(rho=200.0)
    assert session.network.buses["S1"].impedance == fresh.buses["S1"].impedance
    assert session.network.buses["S1"].k_touch is None
    session.undo()
    assert _inputs(session) == before
    session.redo()
    assert _inputs(session) == after


def test_import_characteristic_from_a_document():
    session = _session()
    report = session.apply(api.ImportCharacteristic(target="bus", name="S1", document=_spec()))
    bus = session.network.buses["S1"]
    assert bus.characteristic == gf.characteristic_from_bustype_spec(_spec())
    assert complex(bus.impedance[50.0]) == _standard(200.0, 50.0)
    assert "ONS Typ A" in session.history()[-1]
    # Second way: the same document as a formula gives the same solve at a grid rho.
    table_epr = {b.name: b.uepr for b in gi.solve(session.network, "F").buses}
    session.undo()
    session.apply(api.ImportCharacteristic(target="bus", name="S1", document=_spec(),
                                           impedance_source="formula"))
    formula_epr = {b.name: b.uepr for b in gi.solve(session.network, "F").buses}
    assert formula_epr.keys() == table_epr.keys()
    for name, value in table_epr.items():
        assert formula_epr[name] == pytest.approx(value, rel=1e-12)
    assert report.diagnostics == []


def test_the_characteristic_dto_carries_the_table_of_to_frame():
    session = _session()
    session.apply(api.SetCharacteristic(target="bus", name="S1",
                                        characteristic=_as_json(_table())))
    dto = session.characteristic("bus", "S1")
    frame = _table().to_frame()
    assert [c.name for c in dto.table.columns] == frame.columns
    assert dto.table.rows == frame.to_dicts()
    assert dto.rho_Ohm_m == list(_table().rho_Ohm_m) and dto.frequency_Hz == list(_table().frequency_Hz)
    assert dto.extrapolated is False  # rho 200 and the network frequencies lie on the grid
    assert type(dto).model_validate_json(dto.model_dump_json()) == dto


@pytest.mark.parametrize(
    "command",
    [
        api.SetCharacteristic(target="bus", name="ghost", characteristic={}),
        api.SetCharacteristic(target="bus", name="S1",
                              characteristic={"rho_Ohm_m": [100.0], "frequency_Hz": [50.0],
                                              "impedance_Ohm": [[{"real": -1.0, "imag": 0.0}]]}),
        api.SetCharacteristic(target="bus_type", name="nope", characteristic={}),
        api.ClearCharacteristic(target="bus", name="S1"),  # has none
        api.ImportCharacteristic(target="bus", name="S1", document={"schema": "x"}),
    ],
)
def test_a_rejected_characteristic_command_changes_nothing(command):
    session = _session()
    session.solve("F")
    before = (session.network.model_dump_json(), session.revision, session.history())
    with pytest.raises(api.CommandError):
        session.apply(command)
    assert (session.network.model_dump_json(), session.revision, session.history()) == before


def test_characteristic_commands_round_trip_through_json():
    for command in (api.SetCharacteristic(target="bus", name="S1", characteristic=_as_json(_table())),
                    api.ClearCharacteristic(target="bus_type", name="station"),
                    api.ImportCharacteristic(target="bus", name="S1", document=_spec())):
        text = command.model_dump_json()
        assert api.parse_command(json.loads(text)) == command

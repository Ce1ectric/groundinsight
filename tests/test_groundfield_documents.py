# tests/test_groundfield_documents.py
"""
Writing groundfield BusType documents, and the coupling without groundfield.

Runs without groundfield: the document is plain JSON, and
:func:`groundinsight.integrations.groundfield.characterize` must say how to
get groundfield when it is missing. Whether groundfield itself reads the
documents is checked in ``tests/test_groundfield_characterize.py``.
"""

import importlib.util
import json

import pytest

from groundinsight.integrations import groundfield as gfi
from tests._characteristic_cases import FORMULA, _table


def test_a_characteristic_round_trips_through_a_document(tmp_path):
    original = _table(impedance_formula=FORMULA)
    document = gfi.bustype_document(original, system_type="MV/LV", voltage_level=20.0,
                                    description="station A")
    assert (document["schema"], document["schema_version"]) == ("groundfield.bustype", 1)
    assert document["name"] == "station A" and document["impedance_formula"] == FORMULA
    assert len(document["samples"]["rho_Ohm_m"]) == len(original.rho_Ohm_m) * len(original.frequency_Hz)
    json.dumps(document, allow_nan=False)  # strict JSON
    assert gfi.characteristic_from_bustype_spec(document) == original
    path = tmp_path / "a.json"
    gfi.write_bustype_json(path, original, system_type="MV/LV", voltage_level=20.0)
    assert gfi.read_bustype_json(path) == original


def test_a_document_needs_a_formula():
    with pytest.raises(ValueError, match="formula"):
        gfi.bustype_document(_table(), system_type="MV/LV", voltage_level=20.0)


def test_a_document_of_another_writer_keeps_its_own_provenance():
    document = gfi.bustype_document(_table(impedance_formula=FORMULA), system_type="x",
                                    voltage_level=1.0)
    del document["metadata"]["groundinsight_characteristic"]
    characteristic = gfi.characteristic_from_bustype_spec(document)
    assert characteristic.k_touch is None
    assert characteristic.provenance.source == "groundfield.bustype"


def test_inconsistent_factors_in_a_document_are_refused():
    document = gfi.bustype_document(_table(impedance_formula=FORMULA), system_type="x",
                                    voltage_level=1.0)
    document["metadata"]["groundinsight_characteristic"]["frequency_Hz"] = [50.0]
    with pytest.raises(ValueError, match="grid"):
        gfi.characteristic_from_bustype_spec(document)


@pytest.mark.skipif(importlib.util.find_spec("groundfield") is not None,
                    reason="groundfield is installed here")
def test_characterize_without_groundfield_says_how_to_get_it():
    with pytest.raises(ImportError, match="groundfield"):
        gfi.characterize(lambda soil: None, (100.0,), electrode="e", frequencies=(50.0,))

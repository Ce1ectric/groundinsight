# test_impedance_serialization.py

import pytest
from groundinsight.models.core_models import ComplexNumber
from groundinsight.utils.serialize import (
    serialize_impedance,
    deserialize_impedance,
)

def test_serialize_impedance_normal():
    impedance_dict = {
        50.0: ComplexNumber(real=10.5, imag=-3.2),
        60.0: ComplexNumber(real=15.0, imag=4.1),
        75.5: ComplexNumber(real=8.0, imag=0.0),
    }
    
    expected_json = {
        "50.0": {"real": 10.5, "imag": -3.2},
        "60.0": {"real": 15.0, "imag": 4.1},
        "75.5": {"real": 8.0, "imag": 0.0},
    }
    
    serialized = serialize_impedance(impedance_dict)
    assert serialized == expected_json

def test_serialize_impedance_empty():
    impedance_dict = {}
    expected_json = {}
    serialized = serialize_impedance(impedance_dict)
    assert serialized == expected_json

def test_deserialize_impedance_normal():
    impedance_json = {
        "50.0": {"real": 10.5, "imag": -3.2},
        "60.0": {"real": 15.0, "imag": 4.1},
        "75.5": {"real": 8.0, "imag": 0.0},
    }
    
    expected_impedance = {
        50.0: ComplexNumber(real=10.5, imag=-3.2),
        60.0: ComplexNumber(real=15.0, imag=4.1),
        75.5: ComplexNumber(real=8.0, imag=0.0),
    }
    
    deserialized = deserialize_impedance(impedance_json)
    assert deserialized == expected_impedance

def test_deserialize_impedance_empty():
    impedance_json = {}
    expected_impedance = {}
    deserialized = deserialize_impedance(impedance_json)
    assert deserialized == expected_impedance

def test_round_trip_serialization_deserialization():
    original_impedance = {
        50.0: ComplexNumber(real=10.5, imag=-3.2),
        60.0: ComplexNumber(real=15.0, imag=4.1),
        75.5: ComplexNumber(real=8.0, imag=0.0),
    }
    
    serialized = serialize_impedance(original_impedance)
    deserialized = deserialize_impedance(serialized)
    
    assert deserialized == original_impedance

def test_round_trip_deserialization_serialization():
    original_json = {
        "50.0": {"real": 10.5, "imag": -3.2},
        "60.0": {"real": 15.0, "imag": 4.1},
        "75.5": {"real": 8.0, "imag": 0.0},
    }
    
    deserialized = deserialize_impedance(original_json)
    serialized = serialize_impedance(deserialized)
    
    assert serialized == original_json

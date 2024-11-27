import pytest
from sympy import symbols, I
from sympy.core.sympify import SympifyError
from groundinsight.utils.validations import validate_impedance_formula_value, validate_numerics_dict

def test_validate_impedance_formula_valid():
    """
    Test valid impedance formulas.
    """
    assert validate_impedance_formula_value("R + j*X") == "R + j*X"
    assert validate_impedance_formula_value("M + N") == "M + N"
    assert validate_impedance_formula_value("R * (M + j*X)") == "R * (M + j*X)"
    assert validate_impedance_formula_value("(R + j*X) / (M - j*N)") == "(R + j*X) / (M - j*N)"

def test_validate_impedance_formula_invalid():
    """
    Test invalid impedance formulas that should raise ValueError.
    """
    with pytest.raises(ValueError, match="Sympify of expression"):
        validate_impedance_formula_value("R +")  # Incomplete formula
    with pytest.raises(ValueError, match="Sympify of expression"):
        validate_impedance_formula_value("")  # Empty formula


def test_numeric_dict_valid():
    """
    Test valid numerical values and dictionaries.
    """
    assert validate_numerics_dict(10) is True
    assert validate_numerics_dict(3.14) is True
    assert validate_numerics_dict("3.14") is True
    assert validate_numerics_dict("10") is True
    assert validate_numerics_dict("1 + 2j") is True
    assert validate_numerics_dict({"real": 1, "imag": 2}) is True
    assert validate_numerics_dict({"real": 1.5, "imag": -2.5}) is True

def test_numeric_dict_invalid():
    """
    Test invalid numerical values and dictionaries that should return False.
    """
    assert validate_numerics_dict("invalid") is False
    assert validate_numerics_dict("1 + j") is False
    assert validate_numerics_dict("1 + 2") is False
    assert validate_numerics_dict({"real": 1}) is False
    assert validate_numerics_dict({"imag": 2}) is False
    assert validate_numerics_dict({"real": 1, "imag": "2"}) is False
    assert validate_numerics_dict({"real": 1, "imag": 2, "extra": 3}) is False
    assert validate_numerics_dict(None) is False
    assert validate_numerics_dict([1, 2]) is False
    assert validate_numerics_dict((1, 2)) is False

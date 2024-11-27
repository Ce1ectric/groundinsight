import pytest
import numpy as np
from groundinsight.utils.impedance_calculator import compute_impedance


def test_compute_impedance():
    formula_str = "rho + j * f"
    frequencies = [50, 100, 200]
    params = {"rho": 1}

    result = compute_impedance(formula_str, frequencies, params)

    assert len(result) == 3
    assert result[50].real == 1
    assert result[50].imag == 50

def test_missing_frequency():
    formula_str = "rho + j * f"
    frequencies = None
    params = {"rho": 1}

    # Test for missing frequencies
    with pytest.raises(ValueError) as e:
        compute_impedance(formula_str, frequencies, params)

    
def test_missing_parameter():
    formula_str = "rho + j * f"
    frequencies = [50, 100, 200]
    params = {}

    # Test for missing parameters
    with pytest.raises(ValueError) as e:
        compute_impedance(formula_str, frequencies, params)
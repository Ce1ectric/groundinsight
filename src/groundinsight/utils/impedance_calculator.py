# impedance_calculator.py

import sympy as sp
import numpy as np
from typing import Dict, List, Any


def compute_impedance(
    formula_str: str, frequencies: List[float], params: Dict[str, float]
) -> Dict[float, Any]:
    """
    Compute impedance values for a list of frequencies based on a given formula and parameters.

    This function parses a SymPy-compatible impedance formula string, substitutes the provided
    parameters, and evaluates the impedance across the specified frequencies. If the formula
    contains "NaN" (case-insensitive), it returns infinite impedance values for all frequencies.

    Args:
        formula_str (str): A SymPy-compatible formula string for impedance.
                           Example: "1 + j * f / 50"
        frequencies (List[float]): A list of frequencies (in Hz) at which to compute the impedance.
        params (Dict[str, float]): A dictionary of additional parameters required by the formula.
                                   Example: {"rho": 100.0}

    Returns:
        Dict[float, Any]: A dictionary mapping each frequency to its calculated `ComplexNumber` impedance.

    Raises:
        ValueError: If there is an error in parsing or computing the impedance formula.
        TypeError: If the provided parameters do not match the formula's requirements.
    """
    from groundinsight.models.core_models import ComplexNumber

    # Check if "NaN" is present in the formula string (case-insensitive)
    if "nan" in formula_str.lower():
        # Create a dictionary with NaN values for all frequencies
        return {freq: ComplexNumber(real=np.inf, imag=np.inf) for freq in frequencies}

    try:
        # Define all necessary symbols
        # Start with frequency 'f' and include all keys from params
        symbols = ["f"] + list(params.keys())
        sympy_symbols = sp.symbols(' '.join(symbols))
        
        # Create a symbols dictionary for substitution if needed
        symbols_dict = {str(s): s for s in sympy_symbols}
        # Parse the formula string into a SymPy expression
        expr = sp.sympify(formula_str)

        # Substitute 'I' with the imaginary unit for numerical computation
        expr = expr.subs({"j": 1j})

        # Compile the function with parameters using lambdify
        # Use 'numpy' modules to ensure compatibility with numpy arrays
        compiled_func = sp.lambdify(
            sympy_symbols,
            expr,
            modules=["numpy"]
        )

        # Prepare parameter values in the order of symbols
        param_values = [1.0] * len(frequencies)  # Placeholder for 'f'
        param_values = frequencies  # 'f' is the first symbol

        # Extract additional parameter values
        additional_params = [params[symbol] for symbol in symbols[1:]]  # Exclude 'f'

        # Combine frequency array with additional parameters
        # Broadcasting additional_params to match frequencies
        # This ensures that parameters are constants across frequencies
        # Create a list where each element is (f_i, param1, param2, ...)
        impedance_dict = {}
        for freq in frequencies:
            try:
                # Evaluate the impedance for the current frequency
                impedance = compiled_func(freq, *[params[key] for key in symbols[1:]])

                # Check if the impedance is a number (constant) or an array
                if np.isscalar(impedance):
                    # If impedance is scalar, assign the constant value
                    complex_impedance = ComplexNumber(real=impedance.real, imag=impedance.imag)
                else:
                    # If impedance is array-like, handle accordingly
                    complex_impedance = ComplexNumber(real=impedance.real, imag=impedance.imag)

                impedance_dict[freq] = complex_impedance

            except Exception as e:
                raise ValueError(f"Error computing impedance at frequency {freq} Hz: {e}")

        return impedance_dict

    except Exception as e:
        raise ValueError(f"Error computing impedance: {e}")

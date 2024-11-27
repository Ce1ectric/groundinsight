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

    # Define symbols
    f = sp.symbols("f")
    additional_symbols = list(params.keys())
    symbols_dict = {symbol: sp.symbols(symbol) for symbol in additional_symbols}
    try:
        # Parse the formula string into a SymPy expression
        expr = sp.sympify(formula_str)

        # Substitute 'I' with the imaginary unit for numerical computation
        expr = expr.subs({"j": 1j})

        # Compile the function with parameters using lambdify
        # The variables are 'f' and any additional parameters
        compiled_func = sp.lambdify(
            [f] + [symbols_dict[symbol] for symbol in additional_symbols],
            expr,
            modules="numpy",
        )

        # Prepare parameter values in the order of symbols
        param_values = [params[symbol] for symbol in additional_symbols]

        # Convert frequencies to a NumPy array for vectorized computation
        freq_array = np.array(frequencies, dtype=float)

        # Evaluate the impedance function over all frequencies
        impedance_values = compiled_func(freq_array, *param_values)

        # Ensure impedance_values is a NumPy array for consistent iteration
        impedance_values = np.array(impedance_values, dtype=complex)

        # Store the results in a dictionary
        impedance_dict = {}
        for freq, imp in zip(frequencies, impedance_values):
            impedance_dict[freq] = ComplexNumber(real=imp.real, imag=imp.imag)

        return impedance_dict

    except Exception as e:
        raise ValueError(f"Error computing impedance: {e}")

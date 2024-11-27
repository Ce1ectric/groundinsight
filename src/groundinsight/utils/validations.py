from sympy import sympify, I, symbols
import ast


def validate_impedance_formula_value(value: str) -> str:
    """
    Validate an impedance formula by attempting to parse it using SymPy.

    This function checks whether the provided impedance formula string is valid by parsing
    it with SymPy. It ensures that all necessary symbols are defined and that the formula
    can be successfully interpreted.

    Args:
        value (str): The impedance formula as a string.

    Returns:
        str: The original impedance formula if it is valid.

    Raises:
        ValueError: If the formula is invalid or cannot be parsed.
    """
    try:
        # Define known symbols used in the formulas
        known_symbols = ["R", "X", "M", "N"]
        syms = symbols(" ".join(known_symbols))
        locals_dict = dict(zip(known_symbols, syms))
        # Map 'j' to the imaginary unit 'I'
        locals_dict["j"] = I
        # Sympify the formula with the local variables
        sympify(value, locals=locals_dict)
    except Exception as e:
        raise ValueError(f"Invalid impedance formula: {e}")
    return value


def validate_numerics_dict(value):
    """
    Check if the provided value is a number or a numerical string.

    This function validates whether a given value is a numerical type (int, float, complex)
    or a string that can be safely evaluated to a numerical type. It also checks if a dictionary
    represents a complex number with 'real' and 'imag' keys containing numerical values.

    Args:
        value (Any): The value to validate.

    Returns:
        bool: True if the value is a number, a numerical string, or a valid complex number dictionary.
              False otherwise.
    """
    if isinstance(value, (int, float, complex)):
        return True
    elif isinstance(value, str):
        try:
            # Safely evaluate the string to check if it's a number
            result = ast.literal_eval(value)
            return isinstance(result, (int, float, complex))
        except (ValueError, SyntaxError):
            return False
    elif isinstance(value, dict):
        # Check if dict represents a complex number with 'real' and 'imag' keys
        if set(value.keys()) == {"real", "imag"}:
            real = value["real"]
            imag = value["imag"]
            # Check if both 'real' and 'imag' are numbers
            return isinstance(real, (int, float)) and isinstance(imag, (int, float))
        else:
            return False
    else:
        return False

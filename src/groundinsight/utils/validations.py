from sympy import sympify, I, symbols
import ast
import io
import tokenize


# Names that must never appear in a formula string: the usual entry points
# for sandbox escapes when SymPy evaluates the expression as Python.
_FORBIDDEN_FORMULA_NAMES = frozenset({
    "eval", "exec", "compile", "open", "input", "breakpoint", "exit", "quit",
    "help", "globals", "locals", "vars", "dir", "getattr", "setattr",
    "delattr", "hasattr", "__import__", "memoryview", "bytearray", "bytes",
    "object", "super", "license", "credits", "copyright", "lambda",
})

# Operators with no place in an arithmetic impedance formula; blocking them
# closes attribute access and statement-level tricks.
_FORBIDDEN_FORMULA_OPS = frozenset({".", ";", ":=", "@", "->", "..."})


def assert_safe_formula(value: str) -> None:
    """
    Reject formula strings that could execute arbitrary code when parsed.

    ``sympy.sympify`` evaluates its input as Python, so an unvalidated
    formula such as ``"__import__(\'os\').system(\'...\')"`` is remote code
    execution as soon as a (possibly shared) network JSON file or database
    row is loaded. This guard tokenises ``value`` and rejects anything that
    is not plain arithmetic over identifiers and numbers: dunder names, a
    denylist of dangerous builtins, attribute access and other
    non-arithmetic operators, and string literals. Ordinary free symbols
    (``rho``, ``f``, ``l``, ``R`` ...) and numeric literals (including
    scientific notation and the ``NaN`` open-end sentinel) are still
    accepted, so no legitimate formula is affected.

    Parameters
    ----------
    value : str
        The formula string to check.

    Raises
    ------
    ValueError
        If the formula contains a construct that is not permitted.
    """
    if not isinstance(value, str):
        return
    try:
        tokens = list(tokenize.generate_tokens(io.StringIO(value).readline))
    except (tokenize.TokenError, IndentationError, SyntaxError):
        # A string that cannot be tokenised is handed on unchanged; the
        # subsequent SymPy parse raises the familiar formula error.
        return
    for tok in tokens:
        if tok.type == tokenize.NAME:
            name = tok.string
            if "__" in name or name in _FORBIDDEN_FORMULA_NAMES:
                raise ValueError(
                    f"Impedance formula contains a disallowed name {name!r}. "
                    "Formulas may only use arithmetic over symbols "
                    "(rho, f, l, R, X, ...) and numbers."
                )
        elif tok.type == tokenize.OP:
            if tok.string in _FORBIDDEN_FORMULA_OPS:
                raise ValueError(
                    "Impedance formula contains a disallowed operator "
                    f"{tok.string!r}."
                )
        elif tok.type == tokenize.STRING:
            raise ValueError(
                "Impedance formula must not contain string literals."
            )


def validate_impedance_formula_value(value: str) -> str:
    """
    Validate an impedance formula by attempting to parse it using SymPy.

    This function checks whether the provided impedance formula string is valid by parsing
    it with SymPy. It ensures that all necessary symbols are defined and that the formula
    can be successfully interpreted.

    Parameters
    ----------
    value : str
        The impedance formula as a string.

    Returns
    -------
    str
        The original impedance formula if it is valid.

    Raises
    ------
    ValueError
        If the formula is invalid or cannot be parsed.
    """
    assert_safe_formula(value)
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

    Parameters
    ----------
    value : Any
        The value to validate.

    Returns
    -------
    bool
        True if the value is a number, a numerical string, or a valid complex number dictionary.
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

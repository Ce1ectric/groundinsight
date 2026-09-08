from functools import lru_cache
from sympy import sympify, I, symbols
import ast
import io
import keyword
import tokenize
import unicodedata


# Names that must never appear in a formula string: the usual entry points
# for sandbox escapes when SymPy evaluates the expression as Python. The
# call-position whitelist below already blocks every one of them, but the
# denylist is kept as a second net and as documentation of the threat.
_FORBIDDEN_FORMULA_NAMES = frozenset({
    "eval", "exec", "compile", "open", "input", "breakpoint", "exit", "quit",
    "help", "globals", "locals", "vars", "dir", "getattr", "setattr",
    "delattr", "hasattr", "__import__", "memoryview", "bytearray", "bytes",
    "object", "super", "license", "credits", "copyright", "lambda",
})

# The only operators an arithmetic impedance formula needs. Everything else
# -- attribute access, subscripting, comparison, the walrus, the matrix
# product, statement separators -- is rejected.
_ALLOWED_FORMULA_OPS = frozenset({"+", "-", "*", "/", "**", "(", ")", ","})

# Functions a formula may *call*. This is the security-relevant whitelist:
# ``sympy.lambdify`` turns the parsed expression back into Python source and
# ``exec``s it, so a name in call position is the one construct that can
# reach a builtin. Everything here is a SymPy function that lambdify maps
# onto NumPy, and every one of them is closed over the reals/complexes --
# no ``factorial``, no ``gamma``, so no cheap way to build a huge number.
_ALLOWED_FORMULA_FUNCTIONS = frozenset({
    "sqrt", "exp", "log", "ln", "Abs", "abs", "sign", "conjugate",
    "re", "im", "arg",
    "sin", "cos", "tan", "asin", "acos", "atan", "atan2",
    "sinh", "cosh", "tanh", "asinh", "acosh", "atanh",
    "Min", "Max", "floor", "ceiling",
})

# Token categories that may appear at all. Anything else (string and f-string
# parts, error tokens) is rejected outright.
_ALLOWED_TOKEN_TYPES = frozenset({
    tokenize.NAME, tokenize.NUMBER, tokenize.OP,
    tokenize.NEWLINE, tokenize.NL, tokenize.INDENT, tokenize.DEDENT,
    tokenize.COMMENT, tokenize.ENDMARKER,
})

# Longest formula string the guard will look at. An impedance formula is a
# handful of terms; anything past this is not a formula but an attempt to
# make the checks themselves expensive. Bounding the input keeps every pass
# below linear-in-practice cost.
_MAX_FORMULA_LENGTH = 4096

# Largest literal exponent a formula may use. ``10**10**9`` is pure
# arithmetic over allowed tokens, but SymPy evaluates it eagerly and the
# result has a billion digits -- a denial of service with a ten-character
# formula. Physical formulas use exponents of single digits.
_MAX_LITERAL_EXPONENT = 1000

_SKIPPED_TOKEN_TYPES = frozenset({
    tokenize.NEWLINE, tokenize.NL, tokenize.INDENT, tokenize.DEDENT,
    tokenize.COMMENT, tokenize.ENDMARKER,
})


def _significant_tokens(tokens):
    """
    Drop the layout tokens so neighbours can be inspected in one pass.

    Newlines, indentation, comments and the end marker carry no meaning for
    an arithmetic expression, but they sit between the tokens whose
    *adjacency* the guard needs -- "is this name followed by an opening
    parenthesis?", "is this parenthesis preceded by a closing one?".
    Filtering once keeps that lookahead O(1) per token instead of scanning
    forward from every name.

    Parameters
    ----------
    tokens : List[tokenize.TokenInfo]
        The full token list of the formula.

    Returns
    -------
    List[tokenize.TokenInfo]
        The tokens that carry meaning, in order.
    """
    return [tok for tok in tokens if tok.type not in _SKIPPED_TOKEN_TYPES]


def _assert_no_runaway_power(value: str) -> None:
    """
    Reject exponentiations that would blow up before anything is plotted.

    ``sympy.sympify`` evaluates integer powers eagerly, so ``10**10**9``
    allocates a number with a billion digits and never returns. The check
    is structural rather than lexical: a power whose exponent is itself a
    power, or whose exponent is a literal above
    ``_MAX_LITERAL_EXPONENT``, is refused.

    Parameters
    ----------
    value : str
        The formula string, already tokenised successfully.

    Raises
    ------
    ValueError
        If the formula contains a runaway exponentiation.
    """
    try:
        tree = ast.parse(value, mode="eval")
    except SyntaxError:
        # Not parseable as an expression -- the SymPy parse downstream will
        # produce the familiar formula error.
        return

    def _literal_exponent(node):
        """Numeric value of ``node`` if it is a (signed) numeric literal."""
        if isinstance(node, ast.UnaryOp) and isinstance(
            node.op, (ast.UAdd, ast.USub)
        ):
            return _literal_exponent(node.operand)
        if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
            return abs(node.value)
        return None

    for node in ast.walk(tree):
        if not isinstance(node, ast.BinOp) or not isinstance(node.op, ast.Pow):
            continue
        if any(
            isinstance(sub, ast.BinOp) and isinstance(sub.op, ast.Pow)
            for sub in ast.walk(node.right)
        ):
            raise ValueError(
                "Impedance formula contains a nested exponentiation "
                "(a power in the exponent). SymPy evaluates integer powers "
                "eagerly, so such a formula can allocate an arbitrarily "
                "large number before any impedance is computed."
            )
        exponent = _literal_exponent(node.right)
        if exponent is not None and exponent > _MAX_LITERAL_EXPONENT:
            raise ValueError(
                f"Impedance formula uses the exponent {exponent!r}, which "
                f"exceeds the limit of {_MAX_LITERAL_EXPONENT}. Physical "
                "impedance formulas do not need exponents of that size."
            )


def assert_safe_formula(value: str) -> None:
    """
    Reject formula strings that could execute arbitrary code when parsed.

    ``sympy.sympify`` evaluates its input as Python and ``sympy.lambdify``
    turns the parsed expression back into Python source that is ``exec``ed,
    so an unvalidated formula is remote code execution as soon as a
    (possibly shared) network JSON file or database row is loaded.

    The guard tokenises ``value`` and applies three whitelists:

    * **Names.** Every ``NAME`` token must be pure ASCII and unchanged by
      NFKC normalisation. CPython normalises identifiers while compiling,
      so the fullwidth ``ｅval`` reaches the generated lambdify source as
      ``eval``; comparing the raw token against an ASCII denylist misses
      that entirely. Dunder names and Python keywords (``lambda`` among
      them) are rejected as well.
    * **Calls.** A name immediately followed by ``(`` must be one of
      ``_ALLOWED_FORMULA_FUNCTIONS``. This is the whitelist that matters:
      a bare name becomes a SymPy symbol and cannot execute anything,
      whereas a name in call position ends up as a real call in the
      generated source. Names that are *not* called stay unrestricted --
      an engineer may declare any parameter name (``S``, ``beta``,
      ``gamma`` ...), and the calculator binds declared names to plain
      symbols.
    * **Operators.** Only ``+ - * / ** ( ) ,`` are allowed, which closes
      attribute access, subscripting, slicing, comparisons, the walrus
      operator and statement separators in one rule. A ``(`` directly
      after ``)`` is rejected as well: that is a call on an expression.

    String literals and every other token category are refused, and
    :func:`_assert_no_runaway_power` blocks the ``10**10**9`` class of
    resource exhaustion. Ordinary formulas -- ``"(0.25 + j*f*0.012)*l"``,
    ``"sqrt(rho) + log(f)"``, the ``"NaN"`` open-end sentinel -- are
    unaffected.

    Parameters
    ----------
    value : str
        The formula string to check.

    Raises
    ------
    ValueError
        If the formula contains a construct that is not permitted.

    Examples
    --------
    >>> assert_safe_formula("(0.25 + j*f*0.012)*l")
    >>> assert_safe_formula("sqrt(rho / f)")
    """
    if not isinstance(value, str):
        return
    if len(value) > _MAX_FORMULA_LENGTH:
        raise ValueError(
            f"Impedance formula is {len(value)} characters long, above the "
            f"limit of {_MAX_FORMULA_LENGTH}. An impedance formula is a "
            "handful of terms; a string this size is not one."
        )
    try:
        tokens = list(tokenize.generate_tokens(io.StringIO(value).readline))
    except (tokenize.TokenError, IndentationError, SyntaxError):
        # A string that cannot be tokenised is handed on unchanged; the
        # subsequent SymPy parse raises the familiar formula error.
        return

    for tok in tokens:
        if tok.type not in _ALLOWED_TOKEN_TYPES:
            raise ValueError(
                "Impedance formula must not contain string literals or "
                f"other non-arithmetic tokens (found {tok.string!r})."
            )

    significant = _significant_tokens(tokens)
    for index, tok in enumerate(significant):
        if tok.type == tokenize.NAME:
            name = tok.string
            if not name.isascii() or unicodedata.normalize("NFKC", name) != name:
                raise ValueError(
                    f"Impedance formula contains the non-ASCII name {name!r}. "
                    "Python normalises identifiers (NFKC) while compiling, so "
                    "such a name can resolve to a builtin that the plain "
                    "spelling would not reach. Use ASCII symbol names."
                )
            if "__" in name or keyword.iskeyword(name):
                raise ValueError(
                    f"Impedance formula contains a disallowed name {name!r}. "
                    "Formulas may only use arithmetic over symbols "
                    "(rho, f, l, R, X, ...) and numbers."
                )
            following = (
                significant[index + 1] if index + 1 < len(significant) else None
            )
            if following is not None and following.string == "(":
                if name not in _ALLOWED_FORMULA_FUNCTIONS:
                    raise ValueError(
                        f"Impedance formula calls {name!r}, which is not an "
                        "allowed function. Permitted calls are: "
                        f"{', '.join(sorted(_ALLOWED_FORMULA_FUNCTIONS))}."
                    )
            elif name in _FORBIDDEN_FORMULA_NAMES:
                raise ValueError(
                    f"Impedance formula contains a disallowed name {name!r}. "
                    "Formulas may only use arithmetic over symbols "
                    "(rho, f, l, R, X, ...) and numbers."
                )
        elif tok.type == tokenize.OP:
            if tok.string not in _ALLOWED_FORMULA_OPS:
                raise ValueError(
                    "Impedance formula contains a disallowed operator "
                    f"{tok.string!r}. Permitted operators are "
                    "+ - * / ** ( ) and the argument comma."
                )
            if (
                tok.string == "("
                and index > 0
                and significant[index - 1].string == ")"
            ):
                raise ValueError(
                    "Impedance formula calls the result of an expression, "
                    "which is never arithmetic."
                )

    _assert_no_runaway_power(value)


def validate_impedance_formula_value(value: str) -> str:
    """
    Validate an impedance formula by attempting to parse it using SymPy.

    This function checks whether the provided impedance formula string is valid by parsing
    it with SymPy. It ensures that all necessary symbols are defined and that the formula
    can be successfully interpreted.

    The result is memoised per formula string (see
    :func:`_validate_impedance_formula_str`): loading a network validates the
    same handful of formulas hundreds of times, and every one of those calls
    used to run a full ``sympify``.

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
    if isinstance(value, str):
        return _validate_impedance_formula_str(value)
    return _validate_impedance_formula_uncached(value)


@lru_cache(maxsize=512)
def _validate_impedance_formula_str(value: str) -> str:
    """
    Cached validation of a formula *string*.

    Separated from :func:`validate_impedance_formula_value` so that the
    ``lru_cache`` only ever sees hashable input. The cache is safe with
    respect to the security guard: :func:`assert_safe_formula` is a pure
    function of the same string that keys the cache, so a cache hit is a
    string the guard has already accepted. ``lru_cache`` does not memoise
    exceptions, so a rejected formula is re-checked on every call.

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
    return _validate_impedance_formula_uncached(value)


def _validate_impedance_formula_uncached(value: str) -> str:
    """
    Run the security guard and the SymPy parse for one formula.

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

# utils/validations.py
"""
Validation of impedance formula strings and numeric inputs.

A formula string reaches ``sympy.sympify`` and ``sympy.lambdify``, which
evaluate Python. Everything that can arrive from a shared file -- a network
JSON, a database row, an import default -- therefore passes the guard in
:func:`assert_safe_formula` before SymPy sees it. The guard is a whitelist
over Python's own tokenizer: names, calls and operators are checked token by
token, and the magnitude of the numbers a formula can build is bounded before
SymPy computes any of them (see :func:`_assert_no_runaway_power`).

On top of the security guard sits a usability check,
:func:`check_formula_symbols`: a formula of a bus or branch type may only use
the symbols the evaluator binds for that kind of element. Anything else
(``L`` for ``l``, ``Rho`` for ``rho``) used to pass here and fail much later,
at ``create_branch``, with "Cannot convert expression to float".
"""

from decimal import Decimal
from functools import lru_cache
from sympy import sympify, I, symbols
import ast
import io
import keyword
import math
import tokenize
import unicodedata
from typing import Dict, Iterable, List, NamedTuple, Tuple


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

# --- Resource limits ---------------------------------------------------------
#
# SymPy evaluates arithmetic on numbers exactly and eagerly while it parses:
# ``9**999`` becomes a 954-digit integer before anything is plotted. A formula
# string from a shared file can therefore cost arbitrary CPU and memory
# without executing any code. The limits below bound that cost *before*
# SymPy sees the string. Each is set far above what a physical impedance
# formula needs (measured on every formula in the documentation, the curated
# notebooks and the test suite: at most 6 digits per number, exponents up to
# 2, 27 terms, 2 calls) and far below the point where the cost becomes
# noticeable (measured: worst admitted formula about 50 ms, against
# 1.9 s for one rejected review case and never-returning ones for others).

# Largest exponent a power may have, written out or computed. ``10**-12``
# (pico) is the smallest power of ten with a physical reading in this
# package; a constant expression in the exponent (``9**(4000*1000)``) counts
# with its value, because SymPy evaluates it first.
_MAX_LITERAL_EXPONENT = 12

# Most decimal digits a number literal may have. Together with the exponent
# limit this bounds a single power: ``<100 digits>**12`` has 1200 digits.
_MAX_LITERAL_DIGITS = 100

# Most digits an exact integer or rational built by the formula may have
# (bounded per sub-expression, e.g. across nested powers in the base).
# 10 000 digits are still microseconds of integer arithmetic.
_MAX_EXACT_DIGITS = 10_000

# Most digits of the integer ``floor()``/``ceiling()`` may produce. SymPy has
# to evaluate the argument to that many digits: 2 ms at 100 digits, 0.2 s at
# 1000, and ``floor(exp(10**12))`` never returns.
_MAX_INTEGER_PART_DIGITS = 100

# Largest magnitude, as a power of ten, of the argument of ``exp``, ``sinh``
# and ``cosh``. The cost of the evaluation grows with the argument's size:
# 0.4 ms at 1e100, 15 ms at 1e300.
_MAX_EXP_ARGUMENT_DIGITS = 100

# Most expression nodes (numbers, names, operations, calls) in one formula.
# Bounds the plain size of the expression SymPy builds; 512 leaves room for a
# rational fit with a few dozen poles.
_MAX_FORMULA_TERMS = 512

# Most function calls in one formula, and most arguments of one call. SymPy
# simplifies every call on construction; ``Max``/``Min`` compare their
# symbolic arguments pairwise (8 arguments 20 ms, 64 arguments 1.3 s). With
# these limits the most expensive admitted formula -- eight ``Max`` of three
# symbolic arguments -- costs about 50 ms with cold caches.
_MAX_FORMULA_CALLS = 8
_MAX_CALL_ARGUMENTS = 3

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


def _assert_number_literal(text: str) -> None:
    """
    Reject a number literal that is not a bounded decimal number.

    Parameters
    ----------
    text : str
        The ``NUMBER`` token as written, e.g. ``"1.5e-3"`` or ``"2j"``.

    Raises
    ------
    ValueError
        For a hexadecimal, octal or binary literal (no impedance value is
        written that way, and the digit count would not reflect its size),
        and for a literal with more than ``_MAX_LITERAL_DIGITS`` digits.
    """
    if text[:2].lower() in ("0x", "0o", "0b"):
        raise ValueError(
            f"Impedance formula contains the hexadecimal, octal or binary "
            f"literal {text[:20]!r}. Write numbers in decimal notation."
        )
    digits = sum(ch.isdigit() for ch in text)
    if digits > _MAX_LITERAL_DIGITS:
        raise ValueError(
            f"Impedance formula contains a number with {digits} digits, above "
            f"the limit of {_MAX_LITERAL_DIGITS}. SymPy computes with such a "
            "number exactly, so a single power of it can cost seconds; no "
            "impedance needs that many digits -- write 1.5e40 instead of the "
            "digits."
        )


class _Bound(NamedTuple):
    """
    Upper bounds for the value of one sub-expression of a formula.

    Attributes
    ----------
    mag : float
        Bound on ``log10(max(1, |value|))``.
    inv : float
        Bound on ``log10(max(1, |1/value|))`` for a non-zero value; division
        and negative exponents turn small values into large ones.
    size : float
        Bound on the decimal digits (numerator plus denominator) of the value
        if SymPy evaluates it to an exact integer or rational.
    """

    mag: float
    inv: float
    size: float


#: Bound used for a name: ``pi`` and ``E`` are below 4, and a symbol can only
#: turn into a number by cancelling (``f - f``, ``f/f``), which leaves the
#: literal coefficients around it.
_NAME_LOG10 = math.log10(4.0)
_LOG10_2 = math.log10(2.0)
_LOG10_E = math.log10(math.e)
#: Digits a cancellation or a vanishing real/imaginary part can cost on top of
#: the exact size of the operands (floating-point precision, generously).
_INEXACT_SLACK = 20.0
_EXP_LIKE = frozenset({"exp", "sinh", "cosh"})
_INTEGER_PART = frozenset({"floor", "ceiling"})
_LOG_LIKE = frozenset({"log", "ln"})


def _pow10(exponent: float) -> float:
    """``10**exponent`` as a float, ``inf`` instead of an OverflowError."""
    return math.inf if exponent > 300 else 10.0 ** exponent


def _literal_bound(text: str) -> _Bound:
    """
    Bounds of a decimal number literal, read from its source text.

    The text rather than the parsed value is used because Python turns
    ``1e400`` into ``inf`` and ``1e-400`` into ``0.0``, while SymPy parses
    both as finite numbers.

    Parameters
    ----------
    text : str
        The literal as written, already checked by
        :func:`_assert_number_literal`.

    Returns
    -------
    _Bound
        The bounds of the literal.
    """
    cleaned = text.replace("_", "").rstrip("jJ")
    digits = float(sum(ch.isdigit() for ch in cleaned))
    value = Decimal(cleaned)
    if value == 0:
        return _Bound(0.0, 0.0, max(digits, 1.0))
    adjusted = value.adjusted()
    if -300 < adjusted < 300:
        log10 = math.log10(abs(float(value)))
        return _Bound(max(0.0, log10), max(0.0, -log10), digits)
    return _Bound(max(0.0, adjusted + 1.0), max(0.0, -float(adjusted)), digits)


def _expression_children(node: ast.AST) -> List[ast.expr]:
    """
    Operands of an expression node, in evaluation order.

    Parameters
    ----------
    node : ast.AST
        An expression node of a formula.

    Returns
    -------
    List[ast.expr]
        The operands; the function name of a call is not an operand.

    Raises
    ------
    ValueError
        For a construct an arithmetic formula cannot contain. The token
        guard rejects all of them before this is reached; the check keeps
        the bound analysis closed rather than silently skipping a node.
    """
    if isinstance(node, (ast.Constant, ast.Name)):
        return []
    if isinstance(node, ast.BinOp):
        return [node.left, node.right]
    if isinstance(node, (ast.UnaryOp,)):
        return [node.operand]
    if isinstance(node, ast.Starred):
        return [node.value]
    if isinstance(node, ast.Call):
        return list(node.args)
    if isinstance(node, ast.Tuple):
        return list(node.elts)
    raise ValueError(
        f"Impedance formula contains the construct {type(node).__name__}, "
        "which is not arithmetic."
    )


def _combine(node: ast.expr, operands: List[Tuple[_Bound, bool]], source: str) -> Tuple[_Bound, bool]:
    """
    Bounds of one node from the bounds of its operands, enforcing the limits.

    Parameters
    ----------
    node : ast.expr
        The node.
    operands : List[Tuple[_Bound, bool]]
        Bounds of the operands, each with a flag telling whether the operand
        contains a power.
    source : str
        The parsed source, to quote sub-expressions in a message.

    Returns
    -------
    Tuple[_Bound, bool]
        Bounds of the node and whether it contains a power.

    Raises
    ------
    ValueError
        If the node exceeds one of the resource limits.
    """
    has_power = any(flag for _, flag in operands)
    bounds = [bound for bound, _ in operands]
    if isinstance(node, ast.Constant):
        if isinstance(node.value, bool) or not isinstance(
            node.value, (int, float, complex)
        ):
            raise ValueError("Impedance formula contains a non-numeric constant.")
        return _literal_bound(ast.get_source_segment(source, node) or "0"), False
    if isinstance(node, ast.Name):
        return _Bound(_NAME_LOG10, _NAME_LOG10, 1.0), False
    if isinstance(node, (ast.UnaryOp, ast.Starred)):
        return bounds[0], has_power
    if isinstance(node, ast.BinOp):
        a, b = bounds
        if isinstance(node.op, (ast.Add, ast.Sub)):
            bound = _Bound(
                max(a.mag, b.mag) + _LOG10_2,
                max(a.inv, b.inv) + a.size + b.size + _INEXACT_SLACK,
                a.size + b.size + 1.0,
            )
        elif isinstance(node.op, ast.Mult):
            bound = _Bound(a.mag + b.mag, a.inv + b.inv, a.size + b.size)
        elif isinstance(node.op, ast.Div):
            bound = _Bound(a.mag + b.inv, a.inv + b.mag, a.size + b.size)
        elif isinstance(node.op, ast.Pow):
            if operands[1][1]:
                raise ValueError(
                    "Impedance formula contains a nested exponentiation "
                    "(a power in the exponent). SymPy evaluates integer powers "
                    "eagerly, so such a formula can allocate an arbitrarily "
                    "large number before any impedance is computed."
                )
            exponent = _pow10(b.mag)
            if not exponent <= _MAX_LITERAL_EXPONENT * (1.0 + 1e-9):
                written = ast.get_source_segment(source, node.right) or "?"
                literal = node.right
                while isinstance(literal, ast.UnaryOp):
                    literal = literal.operand
                if isinstance(literal, ast.Constant):
                    message = f"uses the exponent {written}"
                else:
                    message = (
                        f"raises to the exponent {written!r}, which can "
                        f"evaluate to about {exponent:.3g}"
                    )
                raise ValueError(
                    f"Impedance formula {message} -- above the limit of "
                    f"{_MAX_LITERAL_EXPONENT}. SymPy evaluates powers of exact "
                    "numbers exactly, so the size of the exponent multiplies the "
                    "size of the result; physical impedance formulas use small "
                    "exponents (write 1e-15 rather than 10**-15)."
                )
            scale = max(a.mag, a.inv)
            bound = _Bound(scale * exponent, scale * exponent, a.size * max(1.0, exponent))
            has_power = True
        else:  # pragma: no cover - the token guard rejects every other operator
            raise ValueError("Impedance formula contains a non-arithmetic operator.")
    elif isinstance(node, ast.Call):
        name = node.func.id if isinstance(node.func, ast.Name) else ""
        mag = max((b.mag for b in bounds), default=0.0)
        inv = max((b.inv for b in bounds), default=0.0)
        size = sum(b.size for b in bounds)
        if name in _EXP_LIKE:
            if not mag <= _MAX_EXP_ARGUMENT_DIGITS:
                raise ValueError(
                    f"Impedance formula evaluates {name}() at an argument of up "
                    f"to about 1e{mag:.0f}, above the limit of "
                    f"1e{_MAX_EXP_ARGUMENT_DIGITS}. The cost of evaluating "
                    f"{name}() grows with the size of its argument."
                )
            grown = _LOG10_E * _pow10(mag)
            bound = _Bound(grown, grown, 1.0)
        elif name in _INTEGER_PART:
            if not mag <= _MAX_INTEGER_PART_DIGITS:
                raise ValueError(
                    f"Impedance formula takes {name}() of a value with up to "
                    f"about {mag:.3g} digits, above the limit of "
                    f"{_MAX_INTEGER_PART_DIGITS}. {name}() builds the exact "
                    "integer, and SymPy has to evaluate its argument to every "
                    "one of those digits first."
                )
            bound = _Bound(mag, 0.0, mag + 1.0)
        elif name == "sqrt":
            bound = _Bound(mag / 2.0, inv / 2.0, size)
        elif name in _LOG_LIKE:
            bound = _Bound(
                math.log10(1.0 + max(mag, inv) * math.log(10.0)) + 1.0,
                inv + size + _INEXACT_SLACK,
                size,
            )
        else:
            bound = _Bound(mag + 1.0, inv + size + _INEXACT_SLACK, size + 1.0)
    elif isinstance(node, ast.Tuple):
        bound = _Bound(
            max((b.mag for b in bounds), default=0.0),
            max((b.inv for b in bounds), default=0.0),
            sum(b.size for b in bounds),
        )
    else:  # pragma: no cover - _expression_children rejects everything else
        raise ValueError(
            f"Impedance formula contains the construct {type(node).__name__}, "
            "which is not arithmetic."
        )

    if not bound.size <= _MAX_EXACT_DIGITS:
        written = (ast.get_source_segment(source, node) or "?")[:60]
        raise ValueError(
            f"Impedance formula builds an exact number with up to about "
            f"{bound.size:.3g} digits in {written!r}, above the limit of "
            f"{_MAX_EXACT_DIGITS}. SymPy computes with exact numbers exactly; "
            "nested powers multiply their sizes."
        )
    return bound, has_power


def _assert_no_runaway_power(value: str) -> None:
    """
    Reject formulas whose evaluation by SymPy would blow up.

    ``sympy.sympify`` evaluates arithmetic on numbers exactly and eagerly, so
    ``10**10**9`` allocates a number with a billion digits and never returns.
    A limit on *literal* exponents is not enough: ``9**(4000*1000)`` computes
    its exponent first, ``(0*f + 9)**(f - f + 4000000)`` cancels its symbols,
    ``((b**12)**12)**12`` multiplies sizes, and ``floor(exp(10**12))`` asks
    for an integer with 4e11 digits. The check is therefore an analysis of
    the expression tree that bounds, for every sub-expression, the magnitude
    of its value, of its reciprocal and the digits of an exact result (see
    :class:`_Bound`), treating every name as a number that may cancel to a
    small constant. It enforces the module limits on exponents, exact sizes,
    ``floor``/``ceiling``, ``exp``-like arguments, and the number of terms,
    calls and call arguments. The bounds are conservative: a formula can be
    refused although SymPy would have kept the dangerous part symbolic, but
    never the other way round.

    The string is parsed in parentheses (``"(\\n" + value + "\\n)"``), which
    accepts exactly the whitespace and line breaks SymPy's own parser
    accepts. A string that still does not parse is rejected here instead of
    relying on SymPy to reject it (fail-closed, SEC5).

    Parameters
    ----------
    value : str
        The formula string, already tokenised successfully.

    Raises
    ------
    ValueError
        If the formula is not a valid expression or exceeds a resource limit.
        The message names the limit.
    """
    source = "(\n" + value + "\n)"
    try:
        tree = ast.parse(source, mode="eval")
    except SyntaxError as exc:
        raise ValueError(
            f"Impedance formula is not a valid arithmetic expression "
            f"({exc.msg})."
        ) from exc
    except ValueError as exc:  # e.g. a null byte in the source
        raise ValueError(
            f"Impedance formula is not a valid arithmetic expression ({exc})."
        ) from exc

    expressions = [node for node in ast.walk(tree) if isinstance(node, ast.expr)]
    if len(expressions) > _MAX_FORMULA_TERMS:
        raise ValueError(
            f"Impedance formula has {len(expressions)} terms (numbers, names, "
            f"operations and calls), above the limit of {_MAX_FORMULA_TERMS}. "
            "SymPy simplifies the whole expression when it parses it."
        )
    calls = [node for node in expressions if isinstance(node, ast.Call)]
    if len(calls) > _MAX_FORMULA_CALLS:
        raise ValueError(
            f"Impedance formula makes {len(calls)} function calls, above the "
            f"limit of {_MAX_FORMULA_CALLS}. SymPy simplifies every call when "
            "it parses the formula."
        )
    for call in calls:
        count = len(call.args) + len(call.keywords)
        if count > _MAX_CALL_ARGUMENTS:
            name = call.func.id if isinstance(call.func, ast.Name) else "a function"
            raise ValueError(
                f"Impedance formula calls {name}() with {count} arguments, above "
                f"the limit of {_MAX_CALL_ARGUMENTS}. Max and Min compare their "
                "arguments pairwise when SymPy parses them."
            )

    # Post-order walk with an explicit stack: a long sum is a left-deep tree
    # deeper than Python's recursion limit.
    results: Dict[int, Tuple[_Bound, bool]] = {}
    stack: List[Tuple[ast.expr, bool]] = [(tree.body, False)]
    while stack:
        node, expanded = stack.pop()
        children = _expression_children(node)
        if not expanded:
            stack.append((node, True))
            stack.extend((child, False) for child in children)
            continue
        operands = [results.pop(id(child)) for child in children]
        results[id(node)] = _combine(node, operands, source)


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

    String literals and every other token category are refused. A string
    the tokenizer cannot split is refused as well rather than handed on to
    SymPy (fail-closed). Number literals are limited to
    ``_MAX_LITERAL_DIGITS`` decimal digits, and :func:`_assert_no_runaway_power`
    bounds the resources SymPy's exact arithmetic can consume -- exponents,
    exact sizes, ``floor``/``ceiling``, ``exp``-like arguments, and the
    number of terms, calls and call arguments (see the limits at the top of
    this module). Ordinary formulas -- ``"(0.25 + j*f*0.012)*l"``,
    ``"sqrt(rho) + log(f)"``, the ``"NaN"`` open-end sentinel -- are
    unaffected.

    Parameters
    ----------
    value : str
        The formula string to check.

    Raises
    ------
    ValueError
        If the formula contains a construct that is not permitted, cannot be
        tokenized or parsed, or exceeds one of the resource limits; the
        message names the limit.

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
    except (tokenize.TokenError, IndentationError, SyntaxError) as exc:
        # Fail closed (SEC5). Handing the string on would make every check
        # below depend on SymPy's parser rejecting it -- it does today,
        # because it uses the same tokenizer, but a real impedance formula
        # always tokenises, so there is nothing to gain from that coupling.
        detail = exc.args[0] if exc.args else exc
        raise ValueError(
            f"Impedance formula could not be tokenized ({detail}). Check for "
            "an unclosed parenthesis or a stray quote."
        ) from exc

    for tok in tokens:
        if tok.type not in _ALLOWED_TOKEN_TYPES:
            raise ValueError(
                "Impedance formula must not contain string literals or "
                f"other non-arithmetic tokens (found {tok.string!r})."
            )
        if tok.type == tokenize.NUMBER:
            _assert_number_literal(tok.string)

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
        locals_dict = dict(zip(known_symbols, syms, strict=True))
        # Map 'j' to the imaginary unit 'I'
        locals_dict["j"] = I
        # Sympify the formula with the local variables
        sympify(value, locals=locals_dict)
    except Exception as e:
        raise ValueError(f"Invalid impedance formula: {e}")
    return value


#: Names every type formula may use besides its parameters: ``j`` and ``I``
#: are the imaginary unit (``j`` is substituted by the evaluator, ``I`` is
#: SymPy's), ``pi`` and ``E`` are SymPy's constants.
FORMULA_CONSTANTS: Tuple[str, ...] = ("j", "I", "pi", "E")

#: Parameters a *bus* formula (``BusType.impedance_formula``, ``R_formula``,
#: ``L_formula``, ``C_formula``) is evaluated with: the frequency and the
#: specific earth resistance of the bus. A bus has no length.
BUS_FORMULA_SYMBOLS: Tuple[str, ...] = ("f", "rho")

#: Parameters a *branch* formula (every formula field of ``BranchType``) is
#: evaluated with: the frequency, the specific earth resistance and the
#: length of the branch.
BRANCH_FORMULA_SYMBOLS: Tuple[str, ...] = ("f", "rho", "l")

_SYMBOL_MEANING = {
    "f": "frequency in Hz",
    "rho": "specific earth resistance in Ohm*m",
    "l": "branch length in km",
}


def _is_open_end_sentinel(formula: str) -> bool:
    """``True`` for the ``"nan"`` open-end sentinel (whole string, any case).

    Mirrors :func:`groundinsight.utils.impedance_calculator._is_open_end`,
    which cannot be imported here without a cycle.
    """
    return formula.strip().lower() == "nan"


@lru_cache(maxsize=1024)
def _value_names(formula: str) -> Tuple[str, ...]:
    """
    Names a formula uses as values, in order of first appearance.

    A name directly followed by ``(`` is a call and is checked by the
    function whitelist of :func:`assert_safe_formula`; every other name ends
    up as a SymPy symbol (or a SymPy constant) and has to be bound by the
    evaluator.

    Parameters
    ----------
    formula : str
        A formula that :func:`assert_safe_formula` has already accepted.

    Returns
    -------
    Tuple[str, ...]
        The distinct value names. Empty when the string cannot be tokenized
        (the guard has rejected such a string before this is reached).
    """
    try:
        tokens = list(tokenize.generate_tokens(io.StringIO(formula).readline))
    except (tokenize.TokenError, IndentationError, SyntaxError):
        return ()
    significant = _significant_tokens(tokens)
    names = []
    for index, tok in enumerate(significant):
        if tok.type != tokenize.NAME:
            continue
        following = significant[index + 1] if index + 1 < len(significant) else None
        if following is not None and following.string == "(":
            continue
        if tok.string not in names:
            names.append(tok.string)
    return tuple(names)


@lru_cache(maxsize=1024)
def _unknown_symbols(formula: str, parameters: Tuple[str, ...]) -> Tuple[str, ...]:
    """
    Value names of ``formula`` outside ``parameters`` and the constants.

    Cached, because every element derived from a type re-checks the type's
    formulas (see :func:`check_formula_symbols`).

    Parameters
    ----------
    formula : str
        The formula.
    parameters : Tuple[str, ...]
        The parameters the evaluator binds.

    Returns
    -------
    Tuple[str, ...]
        The unknown names in order of appearance; empty for the open-end
        sentinel and for a formula that only uses known names.
    """
    if _is_open_end_sentinel(formula):
        return ()
    permitted = set(parameters) | set(FORMULA_CONSTANTS)
    return tuple(name for name in _value_names(formula) if name not in permitted)


def check_formula_symbols(
    formula: str, allowed: Iterable[str], *, context: str = "formula"
) -> str:
    """
    Reject a formula that uses symbols the evaluator does not bind.

    A bus formula is evaluated with ``f`` and ``rho``, a branch formula with
    ``f``, ``rho`` and ``l`` (see :data:`BUS_FORMULA_SYMBOLS`,
    :data:`BRANCH_FORMULA_SYMBOLS`); :data:`FORMULA_CONSTANTS` are available
    in both. Any other name stays a free SymPy symbol, and the formula then
    fails only when an element is created -- with "Cannot convert expression
    to float", which names neither the symbol nor the field. The typical
    cause is the wrong case (``L``, ``F``, ``Rho``), so the message suggests
    the case-insensitive match when there is one.

    The open-end sentinel ``"nan"`` is not a formula and passes unchanged.

    Parameters
    ----------
    formula : str
        The formula, already accepted by :func:`assert_safe_formula`.
    allowed : Iterable[str]
        The parameters the evaluator binds for this formula; ``f`` must be
        among them. :data:`FORMULA_CONSTANTS` are added automatically.
    context : str, optional
        How to name the formula in the message, e.g.
        ``"BranchType 'cable': self_impedance_formula"``.

    Returns
    -------
    str
        ``formula``, unchanged.

    Raises
    ------
    ValueError
        If the formula uses a name outside the allowed set. The message names
        every unknown symbol, the allowed symbols with their meaning, and the
        case-insensitive match where one exists.

    Examples
    --------
    >>> check_formula_symbols("(0.25 + j*f*0.012)*l", BRANCH_FORMULA_SYMBOLS)
    '(0.25 + j*f*0.012)*l'
    >>> check_formula_symbols("rho*L", BRANCH_FORMULA_SYMBOLS)  # doctest: +ELLIPSIS
    Traceback (most recent call last):
    ...
    ValueError: formula uses the unknown symbol 'L' (did you mean 'l'?). ...
    """
    if not isinstance(formula, str):
        return formula
    parameters = tuple(allowed)
    unknown = _unknown_symbols(formula, parameters)
    if not unknown:
        return formula
    permitted = set(parameters) | set(FORMULA_CONSTANTS)

    hints = []
    for name in unknown:
        matches = sorted(a for a in permitted if a.lower() == name.lower())
        if matches:
            hints.append(f"'{name}' (did you mean '{matches[0]}'?)")
        elif name in BRANCH_FORMULA_SYMBOLS and name not in parameters:
            hints.append(
                f"'{name}' (the {_SYMBOL_MEANING[name]} is only defined in "
                "branch formulas)"
            )
        else:
            hints.append(f"'{name}'")
    noun = "symbol" if len(unknown) == 1 else "symbols"
    described = ", ".join(
        f"{name} ({_SYMBOL_MEANING.get(name, 'parameter')})" for name in parameters
    )
    raise ValueError(
        f"{context} uses the unknown {noun} {', '.join(hints)}. This formula "
        f"is evaluated with {described}; the constants "
        f"{', '.join(FORMULA_CONSTANTS[:2])} (imaginary unit), "
        f"{', '.join(FORMULA_CONSTANTS[2:])} are available as well. Names are "
        "case-sensitive."
    )


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

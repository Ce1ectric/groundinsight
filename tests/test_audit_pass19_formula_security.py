# tests/test_audit_pass19_formula_security.py
"""
Audit pass 19 -- denial of service through formula strings (SEC1) and the
fail-closed tokenizer guard (SEC5).

SEC1
    The guard capped the *literal* exponent at 1000 but nothing else, so
    ``<4080 digits>**999`` passed the guard in microseconds and then cost
    about two seconds in ``sympify`` -- per formula, linear over a JSON load.
    The same class has more doors: a constant expression in the exponent
    (``9**(99999*99999)`` never returns), symbols that cancel
    (``(0*f + 9)**(f - f + 4000000)``), nested powers in the base,
    ``floor(exp(10**12))`` (never returns), ``exp`` of a huge float, and
    plain size -- 120 symbolic arguments to ``Max`` cost 6.6 s, 300 calls
    of ``acosh`` 0.55 s. The guard now bounds all of them before SymPy runs.
SEC5
    A string the tokenizer cannot split was handed on to SymPy unchecked
    (fail-open). It is now rejected by the guard itself.

All payloads are harmless: they only cost CPU, and every one of them is
rejected before SymPy sees it. Timing assertions are generous (the minimum
of three runs against a limit an order of magnitude above the measured
cost), and the deterministic part of each check counts SymPy parses with a
spy instead of measuring time.
"""

import ast
import contextlib
import json
import time

import pytest
import sympy

import groundinsight as gi
import groundinsight.utils.validations as validations
from groundinsight.utils.validations import (
    assert_safe_formula,
    validate_impedance_formula_value,
)


@pytest.fixture
def sympify_spy(monkeypatch):
    """Count the SymPy parses made by the validation path."""
    calls = []
    original = validations.sympify

    def spy(*args, **kwargs):
        calls.append(args[0] if args else kwargs.get("a"))
        return original(*args, **kwargs)

    monkeypatch.setattr(validations, "sympify", spy)
    return calls


def _min_time(func, repeat=3):
    """Shortest of ``repeat`` runs of ``func`` in seconds (robust under load)."""
    best = float("inf")
    for _ in range(repeat):
        start = time.perf_counter()
        func()
        best = min(best, time.perf_counter() - start)
    return best


def _rejects(formula):
    """True when the full validation path rejects ``formula``."""
    try:
        validate_impedance_formula_value(formula)
    except ValueError:
        return True
    return False


# ---------------------------------------------------------------------------
# SEC1 -- the proof of concept of the security review
# ---------------------------------------------------------------------------

_REVIEW_POCS = {
    "base_4080_digits_pow_999": "9" * 4080 + "**999",
    "base_200_digits_pow_999": "9" * 200 + "**999",
    "literal_301_digits": "1" + "0" * 300,
    "base_3001_digits_times_zero": "9" * 3001 + "**999 * 0 + rho*0.01",
}


@pytest.mark.parametrize("name", sorted(_REVIEW_POCS))
def test_review_pocs_are_rejected_before_sympy(name, sympify_spy):
    formula = _REVIEW_POCS[name]
    with pytest.raises(ValueError, match="100"):
        assert_safe_formula(formula)
    assert _rejects(formula)
    assert sympify_spy == []


@pytest.mark.parametrize("name", sorted(_REVIEW_POCS))
def test_review_pocs_are_rejected_fast(name):
    """Measured before the fix: 0.07 s to 1.9 s each, accepted."""
    formula = _REVIEW_POCS[name]
    assert _min_time(lambda: _rejects(formula)) < 0.05


def _hostile_network_json(n_types):
    """A network JSON with ``n_types`` bus types, each carrying its own
    huge-base formula (distinct strings, so no cache can help)."""
    net = gi.create_network(name="poc", frequencies=[50.0])
    payload = json.loads(net.model_dump_json())
    for i in range(n_types):
        bus_type = {
            "name": f"T{i}", "system_type": "S", "voltage_level": 20.0,
            "impedance_formula": str(10 ** 3000 + i) + "**999 * 0 + rho*0.01",
        }
        payload["buses"][f"b{i}"] = {
            "name": f"b{i}", "type": bus_type, "impedance": {"50.0": {"real": 1.0, "imag": 0.0}},
        }
    return json.dumps(payload)


def test_hostile_network_json_is_rejected_fast(sympify_spy):
    """20 bus types with distinct ``<3001 digits>**999`` formulas: 17.9 s to
    load before the fix."""
    payload = _hostile_network_json(20)
    with pytest.raises(ValueError):
        gi.Network.model_validate_json(payload)
    assert sympify_spy == []

    def load():
        with contextlib.suppress(ValueError):
            gi.Network.model_validate_json(payload)

    assert _min_time(load) < 0.5


# ---------------------------------------------------------------------------
# SEC1 -- the same class through other doors
# ---------------------------------------------------------------------------

_OTHER_VECTORS = {
    # constant expressions in the exponent bypassed the literal cap
    "exponent_product": ("9**(4000*1000)", "12"),
    "exponent_product_huge": ("9**(99999*99999)", "12"),
    # symbols that cancel make the base and the exponent constant
    "cancelling_symbols": ("(0*f + 9)**(f - f + 4000000)", "12"),
    # every level of a nested base multiplies the size by the exponent
    "nested_base": ("((((99999999999**12)**12)**12)**12)", "10000"),
    # floor/ceiling build an exact integer of the full magnitude
    "floor_of_exp": ("floor(exp(10**12))", "100"),
    "ceiling_of_exp": ("ceiling(exp(10**7))", "100"),
    "floor_of_big_float": ("floor(1e400*f/f)", "100"),
    # exp of a huge float costs ~15 ms each in mpmath
    "exp_of_huge_float": ("exp(1e300)", "100"),
    # Max/Min compare symbolic arguments pairwise: 120 arguments, 6.6 s
    "max_many_arguments": ("Max(" + ",".join(f"{i}.{i}*rho" for i in range(1, 120)) + ")", "3"),
    # many calls: 300 acosh, 0.55 s -- caught by the term limit first ...
    "many_calls": ("+".join(f"acosh({1000 + i})" for i in range(300)), "512"),
    # ... and nine calls by the call limit
    "nine_calls": ("+".join(f"acosh({1000 + i})" for i in range(9)), "8"),
    # many terms: the size of the expression itself
    "many_terms": ("+".join(f"{i}.5*rho*f" for i in range(120)), "512"),
    # non-decimal literals are no impedance values and hide their size
    "hex_literal": ("0x" + "f" * 4000, "hexadecimal"),
}


@pytest.mark.parametrize("name", sorted(_OTHER_VECTORS))
def test_other_dos_vectors_are_rejected_before_sympy(name, sympify_spy):
    formula, limit = _OTHER_VECTORS[name]
    with pytest.raises(ValueError) as excinfo:
        assert_safe_formula(formula)
    assert limit in str(excinfo.value)
    assert _rejects(formula)
    assert sympify_spy == []


@pytest.mark.parametrize("name", sorted(_OTHER_VECTORS))
def test_other_dos_vectors_are_rejected_fast(name):
    formula, _ = _OTHER_VECTORS[name]
    assert _min_time(lambda: _rejects(formula)) < 0.05


# ---------------------------------------------------------------------------
# SEC1 -- limits are tight enough, but not tighter than real formulas need
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "formula",
    [
        "4*pi*10**-7*f",                   # mu_0
        "8.854e-12*f",
        "10**-12 + rho*0",                 # largest admissible exponent
        "(1 + f/50)**12",
        "(f/50)**0.5 + (f/50)**(1/3) + (f/50)**(3/2)",
        "sqrt(1 - (f/40)**2) + f**0.3333333333333333",
        "(0.05 + I*2*pi*f*2e-7*log(658*sqrt(rho/f)/0.4))*l",
        "floor(f/50) + ceiling(rho/10)",
        "exp(-f/1000)*rho + atan2(f, 50)",
        "Max(f, 1)*0 + Min(rho, 100, 1000)",  # three arguments: the limit
        "9" * 100 + " * 0 + rho",          # 100-digit literal: at the limit
        "((9**12)**12)**12 * 0 + rho",     # 1650 exact digits: cheap, admitted
        "  NaN  ",
        "(rho\n + 1)",
    ],
)
def test_ordinary_formulas_stay_accepted(formula):
    assert_safe_formula(formula)
    assert validate_impedance_formula_value(formula) == formula


@pytest.mark.parametrize(
    "formula",
    ["2**13", "2**-13", "f**(2*7)", "9" * 101 + "*rho", "10**(f*100)", "Max(f, rho, 1, 2)"],
)
def test_just_beyond_the_limits_is_rejected(formula):
    with pytest.raises(ValueError):
        assert_safe_formula(formula)


def test_worst_admitted_formulas_stay_cheap():
    """At the limits, validation plus compilation stays in the tens of
    milliseconds (measured with cold caches: 6-50 ms; the limit here leaves
    room for load)."""
    from groundinsight.utils.impedance_calculator import clear_formula_cache, compute_impedance

    worst = [
        "+".join(f"Max({i}.5*rho, f, {i}*f)" for i in range(1, 9)),
        "+".join(f"acosh({1000 + i}*rho)" for i in range(8)),
        "(" + "9" * 100 + ")**12 * 0 + rho",
        "+".join(f"{i}.25*rho*f" for i in range(85)),
    ]
    for formula in worst:
        assert_safe_formula(formula)

        def full(formula=formula):
            validations._validate_impedance_formula_str.cache_clear()
            clear_formula_cache()
            sympy.core.cache.clear_cache()
            validate_impedance_formula_value(formula)
            compute_impedance(formula, [50.0, 250.0], {"rho": 100.0})

        assert _min_time(full) < 0.25, formula


def test_documented_inventory_is_far_below_the_term_and_call_limits():
    """The largest formula in the docs and notebooks has a fraction of the
    admitted terms and calls (second way: the same AST measure the guard
    applies, computed here independently)."""
    from tests.test_audit_pass19_model_input import _documented_formulas

    terms, calls = [], []
    for _, formula, _ in _documented_formulas():
        if formula.strip().lower() == "nan":
            continue
        tree = ast.parse("(\n" + formula + "\n)", mode="eval")
        terms.append(sum(isinstance(n, ast.expr) for n in ast.walk(tree)))
        calls.append(sum(isinstance(n, ast.Call) for n in ast.walk(tree)))
    assert max(terms) * 4 <= validations._MAX_FORMULA_TERMS
    assert max(calls) * 4 <= validations._MAX_FORMULA_CALLS


# ---------------------------------------------------------------------------
# SEC5 -- the tokenizer guard fails closed
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "formula", ["exec(", "(((eval(", "1 + (2", "rho\n  + 1\n + 2", "'unterminated"]
)
def test_untokenizable_formula_is_rejected_by_the_guard(formula, sympify_spy):
    with pytest.raises(ValueError, match="could not be tokenized"):
        assert_safe_formula(formula)
    assert sympify_spy == []


def test_unparseable_formula_is_rejected_by_the_guard(sympify_spy):
    """A string that tokenizes but is no expression is refused by the guard
    too, instead of relying on SymPy's parser to refuse it."""
    with pytest.raises(ValueError, match="not a valid"):
        assert_safe_formula("rho +* 1")
    assert sympify_spy == []


@pytest.mark.parametrize(
    "formula",
    [
        "__import__('os').system('true')",
        "().__class__.__bases__[0]",
        "ｅval('1')",
        "(lambda: 1)()",
        "getattr(f, 'real')",
    ],
)
def test_code_execution_vectors_stay_rejected(formula, sympify_spy):
    with pytest.raises(ValueError):
        assert_safe_formula(formula)
    assert sympify_spy == []

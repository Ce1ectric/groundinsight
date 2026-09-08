# tests/test_audit_pass18_formula_guard.py
"""
Audit pass 18 -- K1: the impedance formula guard.

``sympy.sympify`` parses a formula as Python and ``sympy.lambdify`` renders
the parsed expression back into Python source that is ``exec``ed. The pass 8
guard compared raw token strings against an ASCII denylist, which CPython's
NFKC normalisation of identifiers walks straight past: the fullwidth
``ｅval`` passes the denylist and arrives in the generated source as the
builtin ``eval``. The reviewer reached the builtin with a harmless argument
and, with ``chr()`` concatenation for the string literal the guard forbids,
had arbitrary code execution from any network JSON or database file.

These tests pin the hardened guard: NFKC/ASCII on every name, a whitelist of
callable functions (the only construct that can reach a builtin), a whitelist
of operators, and a limit on runaway exponentiation. They also pin that no
legitimate formula from the docs, the test suite or ``ImportDefaults`` is
rejected -- a guard that blocks real formulas would be worked around.
"""

import pytest

import groundinsight as gi
from groundinsight.io.defaults import ImportDefaults
from groundinsight.utils.validations import (
    assert_safe_formula,
    validate_impedance_formula_value,
)


# ---------------------------------------------------------------------------
# K1a -- the Unicode bypass
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "formula",
    [
        "ｅval(chr(55)+chr(42)+chr(54))",   # fullwidth 'e' + "val"
        "ｅｘｅｃ(1)",           # fullwidth "exec"
        "ｏｐｅｎ(1)",           # fullwidth "open"
        "ｂｒｅａｋｐｏｉｎｔ()",  # "breakpoint"
        "ｅval",                             # not even called
        "Klelvin",                          # KELVIN SIGN normalises to 'K'
    ],
)
def test_nfkc_lookalike_names_are_rejected(formula):
    """A name that NFKC-normalises to something else never reaches SymPy.

    Without the fix these pass the guard, and ``ｅval(...)`` reaches the
    ``eval`` builtin inside the lambdify-generated module.
    """
    with pytest.raises(ValueError, match="non-ASCII name"):
        assert_safe_formula(formula)


def test_nfkc_bypass_is_rejected_through_the_public_api():
    """The guard must also fire on the path a shared network file takes."""
    with pytest.raises(ValueError):
        gi.BusType(
            name="attack",
            system_type="Grounded",
            voltage_level=20.0,
            impedance_formula="ｅval(chr(55))",
        )


# ---------------------------------------------------------------------------
# K1b -- calls are whitelisted
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "formula",
    [
        "chr(55) + chr(42)",     # string building for an eval payload
        "eval(1)",
        "exec(1)",
        "open(1)",
        "breakpoint()",
        "globals()",
        "getattr(rho, 1)",
        "print(1)",
        "__import__(1)",
        "factorial(999999)",     # not dangerous, but unbounded work
        "gamma(1e6)",
        "(rho)(f)",              # a call on an expression
    ],
)
def test_only_whitelisted_functions_may_be_called(formula):
    with pytest.raises(ValueError):
        assert_safe_formula(formula)


@pytest.mark.parametrize(
    "formula",
    ["sqrt(rho)", "log(f)", "exp(0*f)", "Abs(rho)", "atan(f)", "Max(f, 1)"],
)
def test_whitelisted_functions_are_still_callable(formula):
    assert_safe_formula(formula)


def test_a_forbidden_name_is_still_usable_as_a_plain_symbol():
    """Only *calls* can execute. A bare name becomes a SymPy symbol.

    ``open`` stays on the denylist as a second net, but an ordinary
    engineering symbol that merely shares a name with a builtin must not
    take the whole formula down.
    """
    assert_safe_formula("abs(rho) + 0*f")


# ---------------------------------------------------------------------------
# K1c -- operators, literals and other token categories
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "formula",
    [
        "x.__class__",
        "rho.real",
        "'string literal'",
        'f"{rho}"',
        "rho[0]",
        "{1: 2}",
        "rho; f",
        "rho @ f",
        "(rho := 2)",
        "rho if f else 1",
        "lambda f: f",
        "rho > f",
    ],
)
def test_non_arithmetic_constructs_are_rejected(formula):
    with pytest.raises(ValueError):
        assert_safe_formula(formula)


# ---------------------------------------------------------------------------
# K1d -- resource exhaustion through pure arithmetic
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("formula", ["10**10**9", "2**3**4**5", "10**99999"])
def test_runaway_exponentiation_is_rejected(formula):
    """``sympify`` evaluates integer powers eagerly.

    ``10**10**9`` is pure arithmetic over allowed tokens and would allocate a
    number with a billion digits before any impedance is computed. A power in
    the exponent, or a literal exponent above the limit, is refused.
    """
    with pytest.raises(ValueError):
        assert_safe_formula(formula)


@pytest.mark.parametrize(
    "formula", ["f**2", "(f/500)**2", "f**0.3333333333333333", "f**-2", "10**3"]
)
def test_ordinary_exponents_are_untouched(formula):
    assert_safe_formula(formula)


# ---------------------------------------------------------------------------
# K1e -- no legitimate formula is rejected
# ---------------------------------------------------------------------------


_LEGITIMATE_FORMULAS = [
    # docs/ and notebooks
    "(0.25 + j*f*0.012)*l",
    "(0.0 + j*f*0.012) * l",
    "rho * 0.01 + j * f * 1/50 * 0.1",
    "rho/(2*3.14159*1.5)*(1 + j*0.01*f)",
    # tests/
    "1+roh+f",                       # arbitrary free symbol (typo) still ok
    "rho*0 + 0.1 + I*f*1/50",
    "NaN",                           # open-end sentinel
    "nan",
    "1 + 2*j",
    "R + j*X",
    "M + N",
    "(R + j*X) / (M - j*N)",
    "rho*0 + 10 + I*2*pi*f*5e-3",
    "(rho*0 + 600e-9)*l",
    "rho*0 + 1e-320",
    "sqrt(rho) + log(f) + exp(0) + pi",
    "sqrt(1 - (f/40)**2) + f**0.3333333333333333",
    "(0.30 + j*f*0.0025) * l * sqrt(1 - (f/500)**2)",
    "(0.25 + I * 2 * pi * f * 2e-7 * log(658 * sqrt(rho / f) / 0.01)) * l",
    # parameter names an engineer may declare (see pass 12)
    "S + beta + gamma + zeta + lex + Q + O + E + re + im + oo + 0*f",
]


@pytest.mark.parametrize("formula", _LEGITIMATE_FORMULAS)
def test_legitimate_formulas_are_still_accepted(formula):
    assert_safe_formula(formula)


def test_import_defaults_formulas_pass_the_guard():
    """Every formula the documented ``ImportDefaults`` ships must survive."""
    defaults = ImportDefaults(
        rho=100.0,
        frequencies=[50.0],
        default_bus_type=gi.BusType(
            name="ImportedBus",
            system_type="Grounded",
            voltage_level=20.0,
            impedance_formula="rho * 0 + 1.0 + I * f * 0",
        ),
        default_branch_type=gi.BranchType(
            name="ImportedCable",
            grounding_conductor=True,
            self_impedance_formula="(0.25 + I * 0.6) * l",
            mutual_impedance_formula="(0.0 + I * 0.6) * l",
        ),
    )
    for formula in (
        defaults.default_bus_type.impedance_formula,
        defaults.default_branch_type.self_impedance_formula,
        defaults.default_branch_type.mutual_impedance_formula,
    ):
        assert_safe_formula(formula)
        assert validate_impedance_formula_value(formula) == formula


def test_guarded_formula_still_solves_end_to_end():
    """A whitelisted call must survive validation *and* evaluation."""
    net = gi.create_network(name="p18_guard", frequencies=[50.0])
    bus_type = gi.BusType(
        name="bt",
        system_type="Grounded",
        voltage_level=20.0,
        impedance_formula="sqrt(rho*0 + 100) + 0*f",
    )
    branch_type = gi.BranchType(
        name="brt",
        self_impedance_formula="(0.25 + j*f*0.012)*l",
        mutual_impedance_formula="(0.0 + j*f*0.012)*l",
        grounding_conductor=True,
    )
    for name in ("b1", "b2"):
        gi.create_bus(name=name, type=bus_type, network=net)
    gi.create_branch(
        name="l1",
        type=branch_type,
        from_bus="b1",
        to_bus="b2",
        length=1.0,
        network=net,
    )
    gi.create_fault(name="F1", bus="b2", scalings={50.0: 1.0}, network=net)
    gi.create_source(name="S1", bus="b1", values={50.0: 1000.0}, network=net)
    gi.run_fault(net, "F1")
    assert net.results["F1"].buses
    assert net.buses["b1"].impedance[50.0].real == pytest.approx(10.0)


# ---------------------------------------------------------------------------
# L1 -- the guard is not bypassed by the validation cache
# ---------------------------------------------------------------------------


def test_validation_cache_does_not_bypass_the_guard():
    """A rejected formula stays rejected however often it is offered."""
    for _ in range(3):
        with pytest.raises(ValueError):
            validate_impedance_formula_value("ｅval(chr(55))")
    for _ in range(3):
        assert validate_impedance_formula_value("R + j*X") == "R + j*X"


def test_an_absurdly_long_formula_is_rejected():
    """The checks themselves must not be a denial-of-service target."""
    with pytest.raises(ValueError, match="characters long"):
        assert_safe_formula("rho + " * 2000 + "f")

# tests/test_audit_pass12_formula.py

"""
Regression tests for the twelfth audit-pass bug-fix batch (2026-07-28).

All six findings live on the path from an impedance formula string to a number
in the admittance matrix, and five of the six were *silent*: they produced a
wrong number or a NaN instead of raising, and the failure surfaced -- if at all
-- several layers later as "singular admittance matrix", which describes a
topology error that does not exist.

F1. ``compute_impedance`` tested ``"nan" in formula_str.lower()`` -- a
    *substring* test -- so any formula merely containing those three letters
    became an open circuit without a word. ``resonance``, ``resonanz``,
    ``dominant``, ``nanofarad`` and ``discriminant`` all match, and the first
    two are ordinary vocabulary in a resonant-earthed (Petersen coil) network.
    The sentinel is now matched against the whole stripped, case-folded string.
F2. ``sympify`` was called without ``locals``, so a parameter name that
    collides with one of SymPy's ~680 exported names lost its value. ``E``
    became Euler's number and ``oo`` infinity *silently*; ``S`` (the conductor
    cross-section of IEC 60949), ``beta`` (its material constant), ``gamma``
    (the propagation constant), ``N``, ``Q``, ``re`` and ``im`` raised
    ``unsupported operand type(s)``, naming neither the parameter nor the
    reason. Declared parameters are now bound as plain symbols.
F3. ``params={"I": ...}`` and ``params={"j": ...}`` were silently overwritten
    with the imaginary unit, and ``params={"f": ...}`` leaked ``duplicate
    argument 'f' in function definition`` from generated code. ``I`` for a
    current is about as natural a name as exists in power engineering. The
    three reserved names are now rejected with an explanation.
F4. ``lambdify(..., modules=["numpy"])`` picks the branch of ``sqrt`` and
    ``log`` from the *dtype*: ``np.sqrt(-0.5625)`` is ``nan`` where SymPy says
    ``0.75*I``. Every formula whose argument goes negative therefore collapsed
    to NaN -- and it goes negative in ordinary use, because 0 Hz is a routine
    entry in ``scalings``. Evaluation now retries on the complex plane at
    exactly the frequencies that came back NaN.
F5. NaN was passed on as if it were a number: into ``ComplexNumber``, into
    ``compute_real_value``'s R/L/C fields (whose ``not np.isfinite`` guard was
    written for the ``inf`` sentinel and caught NaN as well), and into the
    admittance matrix. NaN is now raised; ``inf`` still passes through,
    because an open end and a capacitor at DC are both legitimately infinite.
F6. ``1/complex(inf, inf)`` is ``nan+nan*j``, not ``0`` -- IEEE 754 complex
    division does not give the mathematical answer for an infinite operand.
    A single un-earthed tower (``impedance_formula="nan"``, exactly as
    documented) therefore put NaN on the diagonal of ``Y`` and failed the
    *whole* network with "no path to reference earth", even with every other
    bus properly earthed. Infinite impedances are now short-circuited to zero
    admittance.
"""

from __future__ import annotations

import math
import warnings

import numpy as np
import pytest

import groundinsight as gi
from groundinsight.utils.impedance_calculator import (
    _compile_formula,
    compute_impedance,
    compute_real_value,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _z(formula, freqs, params=None, freq=None):
    """Evaluate a formula and return one frequency's value as a ``complex``."""
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        result = compute_impedance(formula, freqs, params or {})
    key = freqs[0] if freq is None else freq
    cn = result[key]
    return complex(cn.real, cn.imag)


def _chain_network(name, bus_types, self_formula="(0.30 + j*f*0.0025) * l",
                   frequencies=(50.0,)):
    """Three buses A-B-C in a chain, source at A, fault at C.

    ``bus_types`` maps a bus name to its :class:`BusType`; every bus gets a
    100 ohm-m soil so ``rho`` is bound.
    """
    ct = gi.BranchType(
        name=f"shield_{name}",
        grounding_conductor=True,
        self_impedance_formula=self_formula,
        mutual_impedance_formula="(0.05 + j*f*0.0020) * l",
    )
    net = gi.create_network(
        name=name, frequencies=list(frequencies), description=""
    )
    for bus_name in ("A", "B", "C"):
        gi.create_bus(
            name=bus_name,
            type=bus_types[bus_name],
            specific_earth_resistance=100.0,
            network=net,
        )
    gi.create_branch(name="A-B", type=ct, from_bus="A", to_bus="B",
                     length=0.3, network=net)
    gi.create_branch(name="B-C", type=ct, from_bus="B", to_bus="C",
                     length=0.3, network=net)
    gi.create_source(name="s", bus="A",
                     values={freq: 5000.0 for freq in frequencies},
                     r_to_x=0.1, network=net)
    gi.create_fault(name="F", bus="C",
                    scalings={freq: 1.0 for freq in frequencies},
                    t_k_s=0.5, n_factor=1.0, network=net)
    return net


def _grounded_type(name="grounded"):
    return gi.BusType(name=name, description="", system_type="Tower",
                      voltage_level=110.0, impedance_formula="rho * 0.15")


def _open_type(name="open_end"):
    return gi.BusType(name=name, description="", system_type="Tower",
                      voltage_level=110.0, impedance_formula="nan")


def _epr(net, freq="50"):
    return {
        row["bus_name"]: row["EPR_V"]
        for row in net.res_buses().to_dicts()
        if row["frequency_Hz"] == freq
    }


def _rms(frame, key_column, value_column):
    """Pick the RMS row of a result frame as ``{name: value}``."""
    return {
        row[key_column]: row[value_column]
        for row in frame.to_dicts()
        if row["frequency_Hz"] == "RMS"
    }


# ---------------------------------------------------------------------------
# F1 -- the open-end sentinel is matched exactly, not as a substring
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("spelling", ["nan", "NaN", "NAN", "  nan  ", "Nan"])
def test_sentinel_spellings_still_mean_open_end(spelling):
    """Backwards compatibility: every spelling used in the repo and the docs
    must keep returning an infinite impedance."""
    z = _z(spelling, [0.0, 50.0, 250.0], freq=50.0)
    assert math.isinf(z.real) and math.isinf(z.imag)


@pytest.mark.parametrize(
    "label, formula, params, expected",
    [
        ("resonance as a parameter", "f/resonance", {"resonance": 50.0}, 1.0),
        ("resonanz, German spelling", "f/resonanz", {"resonanz": 25.0}, 2.0),
        ("dominant (do-mi-NAN-t)", "dominant * f", {"dominant": 2.0}, 100.0),
        ("nanofarad in the name", "C_nano * f", {"C_nano": 3.0}, 150.0),
        ("discriminant", "discriminant + 0*f", {"discriminant": 7.0}, 7.0),
    ],
)
def test_formula_containing_nan_as_a_substring_is_evaluated(
    label, formula, params, expected
):
    """A formula that merely *contains* the letters n-a-n is an ordinary
    formula. Before the fix each of these silently became an open circuit."""
    z = _z(formula, [50.0], params)
    assert not math.isinf(z.real), f"{label} was turned into an open circuit"
    assert z == pytest.approx(complex(expected, 0.0))


def test_open_end_bus_type_still_works_end_to_end():
    """The documented modelling convention for a tower without an earth
    electrode must survive the whole pipeline."""
    types = {"A": _grounded_type(), "B": _open_type(), "C": _grounded_type()}
    net = _chain_network("p12_sentinel_e2e", types)
    gi.run_fault(net, "F")
    epr = _epr(net)
    assert all(math.isfinite(value) for value in epr.values())


# ---------------------------------------------------------------------------
# F2 -- a declared parameter is a parameter, whatever SymPy calls that name
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "name, value",
    [
        ("S", 2000.0),      # conductor cross-section, IEC 60949
        ("beta", 202.0),    # material constant, IEC 60949
        ("gamma", 6.0),     # propagation constant
        ("E", 7.0),         # field strength -- silently became 2.71828...
        ("N", 25.0),        # number of electrodes
        ("Q", 3.0),         # charge / reactive power
        ("re", 4.0),        # equivalent radius
        ("im", 5.0),
        ("O", 9.0),
        ("zeta", 2.0),
        ("lex", 8.0),
        ("oo", 11.0),       # silently became infinity
    ],
)
def test_sympy_names_used_as_parameters_keep_the_callers_value(name, value):
    z = _z(f"{name} + 0*f", [50.0], {name: value})
    assert z == pytest.approx(complex(value, 0.0)), (
        f"parameter {name!r} did not reach the formula"
    )


def test_undeclared_sympy_names_keep_their_sympy_meaning():
    """Only *declared* names are shadowed. ``sqrt``, ``log``, ``exp`` and
    ``pi`` must keep working as functions and constants."""
    z = _z("sqrt(rho) + log(f) + exp(0) + pi", [50.0], {"rho": 100.0})
    expected = math.sqrt(100.0) + math.log(50.0) + 1.0 + math.pi
    assert z.real == pytest.approx(expected)
    assert z.imag == pytest.approx(0.0)


def test_parameter_shadows_a_sympy_constant_it_shares_a_name_with():
    """The rule is "declared wins", with no blocklist -- a caller who declares
    ``pi`` as a parameter gets their own value, not 3.14159..."""
    assert _z("pi + 0*f", [50.0], {"pi": 42.0}) == pytest.approx(complex(42.0))


# ---------------------------------------------------------------------------
# F3 -- names this module binds itself are rejected, not silently overwritten
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("name", ["f", "I", "j"])
def test_reserved_parameter_names_are_rejected(name):
    with pytest.raises(ValueError, match="reserved"):
        compute_impedance(f"{name} * 2", [50.0], {name: 3.0})


def test_reserved_name_error_names_the_offending_parameter():
    """Asserted on the leading phrase, not on a bare ``'I'`` anywhere in the
    message: the explanation quotes ``'f'``, ``'I'`` and ``'j'`` by necessity,
    so a substring search would pass even with the echo of the caller's own
    key removed. Only the part before the first period is the caller's."""
    with pytest.raises(ValueError) as excinfo:
        compute_impedance("I * 3 + 0*f", [50.0], {"I": 5.0})
    assert "Reserved parameter name(s): 'I'." in str(excinfo.value)


def test_reserved_name_error_lists_every_offending_parameter():
    with pytest.raises(ValueError) as excinfo:
        compute_impedance("I * 3 + j * 2", [50.0], {"I": 5.0, "j": 2.0})
    assert "Reserved parameter name(s): 'I', 'j'." in str(excinfo.value)


def test_reserved_names_are_rejected_in_compute_real_value_too():
    with pytest.raises(ValueError) as excinfo:
        compute_real_value("I + 0*f", [50.0], {"I": 5.0}, name="R_self")
    message = str(excinfo.value)
    assert "reserved" in message
    # ``name`` is documented as "used in error messages" -- that has to hold
    # for the failures raised inside the shared pipeline as well, otherwise a
    # BranchType with the same expression in R_self_formula and
    # R_mutual_formula produces two identical messages.
    assert message.startswith("R_self: ")


def test_unreserved_current_name_works():
    """The suggested rename in the error message must actually work."""
    assert _z("I_k * 1e-3 + 0*f", [50.0], {"I_k": 5000.0}) == pytest.approx(
        complex(5.0, 0.0)
    )


# ---------------------------------------------------------------------------
# F4 -- the negative branch of sqrt/log follows SymPy, not the NumPy dtype
# ---------------------------------------------------------------------------


def test_sqrt_below_the_cutoff_frequency_returns_the_sympy_branch():
    """``sqrt(1-(f/f0)**2)`` at f > f0: SymPy says ``0.75*I``, NumPy on a real
    array said ``nan``."""
    z = _z("sqrt(1 - (f/f0)**2)", [50.0], {"f0": 40.0})
    assert z == pytest.approx(complex(0.0, 0.75))


def test_power_of_one_half_takes_the_same_branch_as_sqrt():
    z = _z("(1 - (f/f0)**2)**0.5", [50.0], {"f0": 40.0})
    assert z == pytest.approx(complex(0.0, 0.75))


def test_log_of_a_negative_argument_returns_the_principal_branch():
    z = _z("log(f - f0)", [50.0], {"f0": 100.0})
    assert z == pytest.approx(complex(math.log(50.0), math.pi))


def test_negative_parameter_under_a_square_root():
    z = _z("sqrt(rho - 200) + 0*f", [50.0], {"rho": 100.0})
    assert z == pytest.approx(complex(0.0, 10.0))


def test_the_complex_retry_fires_wherever_the_radicand_is_negative():
    """The retry is keyed on NaN, not on the frequency.

    ``sqrt(f - 50)`` is negative under the root for every f < 50 Hz, so NumPy
    on a real array returns NaN there and the complex retry has to supply the
    SymPy branch. This is the original coverage of what used to be
    ``test_zero_hertz_is_not_a_special_case``; only the probe frequency moved
    off 0 Hz, because 0 Hz is now a deliberate special case (next test).
    """
    z = _z("sqrt(f - 50)", [10.0, 50.0], freq=10.0)
    assert z == pytest.approx(complex(0.0, math.sqrt(40.0)))


def test_zero_hertz_is_a_deliberate_special_case():
    """0 Hz is a routine entry in ``Network.frequencies`` and in
    ``fault.scalings``, which is what makes this reachable -- and it is the one
    frequency at which a *reactance* is not a modelling statement anyone can
    read. At DC a reactance either vanishes or is infinite, so a formula that
    reports a finite one there has said something physically empty. The value
    falls back to the real part and the fallback is announced.

    The other frequencies of the same call are untouched: the fallback is a
    statement about DC, not a change of the formula.
    """
    with pytest.warns(gi.DCLimitWarning, match="reactance at 0 Hz"):
        result = compute_impedance("sqrt(f - 50)", [0.0, 50.0], {})

    dc = complex(result[0.0].real, result[0.0].imag)
    assert dc == 0.0
    assert not math.isnan(dc.real) and not math.isnan(dc.imag)

    ac = complex(result[50.0].real, result[50.0].imag)
    assert ac == pytest.approx(complex(0.0, 0.0), abs=1e-12)


def test_healthy_formulas_are_unchanged_bit_for_bit():
    """The complex retry must only fire where the real evaluation returned
    NaN, so every formula that worked before returns exactly what it did."""
    freqs = [0.0, 50.0, 250.0, 1000.0]
    # (formula, params, independent reference implementation)
    cases = [
        (
            "(0.30 + j*f*0.0025) * l",
            {"l": 0.3},
            lambda f, l: (0.30 + 1j * f * 0.0025) * l,
        ),
        (
            "rho * 0.15",
            {"rho": 100.0},
            lambda f, rho: complex(rho * 0.15, 0.0),
        ),
        (
            "1 + j * f / 50 + rho * l / 1000",
            {"rho": 100.0, "l": 2.0},
            lambda f, rho, l: 1 + 1j * f / 50 + rho * l / 1000,
        ),
        (
            "sqrt(rho / (2*pi*f + 1))",
            {"rho": 100.0},
            lambda f, rho: complex(math.sqrt(rho / (2 * math.pi * f + 1)), 0.0),
        ),
    ]
    for formula, params, reference in cases:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            got = compute_impedance(formula, freqs, params)
        for freq in freqs:
            value = complex(got[freq].real, got[freq].imag)
            expected = complex(reference(freq, **params))
            assert value == pytest.approx(expected, rel=1e-12, abs=1e-15), (
                f"{formula} moved at {freq} Hz"
            )


def test_a_healthy_frequency_keeps_its_real_axis_value_bit_for_bit():
    """The retry is applied per position, not to the whole array.

    ``test_healthy_formulas_are_unchanged_bit_for_bit`` cannot show this: no
    NaN occurs there, so the retry never runs and taking its result wholesale
    would be indistinguishable. The guarantee only becomes observable in a
    formula that is NaN at *one* frequency and healthy at another -- and only
    if the real and the complex NumPy kernel actually disagree at the healthy
    one. They do, but not for every function: measured over 200 000 random
    arguments, ``sqrt`` and ``sin`` never differ, ``exp`` differs for ~4.6 %
    and ``x**(1/3)`` for ~80 % of them. Hence the cube root below.

    The disagreeing frequency is located at runtime rather than hard-coded, so
    the test keeps its teeth across NumPy versions instead of silently turning
    into a tautology.
    """
    formula = "sqrt(1 - (f/40)**2) + f**0.3333333333333333"
    compiled = _compile_formula(formula, ())

    def evaluate(freq, dtype):
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            with np.errstate(invalid="ignore", divide="ignore"):
                return complex(
                    np.asarray(
                        compiled(np.array([freq], dtype=dtype)), dtype=complex
                    )[0]
                )

    healthy = None
    for candidate in range(1, 40):          # below the cutoff -> no NaN
        real_axis = evaluate(float(candidate), float)
        complex_plane = evaluate(float(candidate), complex)
        if real_axis != complex_plane:
            healthy = (float(candidate), real_axis, complex_plane)
            break
    if healthy is None:
        pytest.skip("this NumPy build has no real/complex kernel disagreement")

    freq, real_axis, complex_plane = healthy
    # 50 Hz is above the cutoff and comes back NaN on the real axis, which is
    # what makes the retry fire at all.
    got = compute_impedance(formula, [freq, 50.0], {})

    assert complex(got[freq].real, got[freq].imag) == real_axis
    assert complex(got[freq].real, got[freq].imag) != complex_plane
    assert not math.isnan(got[50.0].imag) and got[50.0].imag != 0.0


CUTOFF_FORMULA = "(0.30 + j*f*0.0025) * l * sqrt(1 - (f/500)**2)"


def test_cutoff_formula_evaluates_on_the_complex_plane_instead_of_nan():
    """A branch impedance with a validity cutoff -- an entirely ordinary way
    to write a dispersion term -- evaluated above the cutoff.

    This is the F4 finding proper: ``np.sqrt(-3.0)`` is ``nan`` where SymPy
    says ``I*sqrt(3)``, so the whole formula collapsed to NaN above 500 Hz.
    The retry on the complex plane has to give a finite complex number.
    """
    z = compute_impedance(CUTOFF_FORMULA, [50.0, 1000.0], {"rho": 100.0, "l": 1.0})
    for freq in (50.0, 1000.0):
        assert math.isfinite(z[freq].real) and math.isfinite(z[freq].imag)
    # Below the cutoff the term is real and positive; above it the sqrt turns
    # imaginary and rotates the impedance into the negative-resistance half
    # plane. Both are finite -- only the second one is unphysical.
    assert z[50.0].real > 0.0
    assert z[1000.0].real < 0.0


def test_cutoff_formula_solves_below_the_cutoff():
    types = {name: _grounded_type() for name in ("A", "B", "C")}
    net = _chain_network(
        "p12_cutoff", types, self_formula=CUTOFF_FORMULA, frequencies=(50.0,)
    )
    gi.run_fault(net, "F")
    epr = _epr(net, "50")
    assert epr
    assert all(math.isfinite(value) for value in epr.values())
    assert max(epr.values()) > 0.0


def test_cutoff_formula_above_the_cutoff_is_rejected_not_solved():
    """Superseded by audit pass 15.

    Pass 12 only required that this formula stop producing NaN. It still
    returned a *negative resistance* above the cutoff, and the network solved
    with it silently. A passive earthing conductor cannot have one, so pass 15
    rejects it where the impedance is computed. The message has to name the
    branch, the frequency and the formula, because the fix is to restrict the
    frequency list or the formula -- not to go looking at the topology.
    """
    types = {name: _grounded_type() for name in ("A", "B", "C")}
    with pytest.raises(ValueError) as excinfo:
        _chain_network(
            "p12_cutoff_hf",
            types,
            self_formula=CUTOFF_FORMULA,
            frequencies=(50.0, 1000.0),
        )
    message = str(excinfo.value)
    assert "negative" in message
    assert "1000 Hz" in message
    assert "sqrt(1 - (f/500)**2)" in message
    assert "Singular" not in message


# ---------------------------------------------------------------------------
# F5 -- NaN is an error, inf is a value
# ---------------------------------------------------------------------------


def test_nan_parameter_raises_instead_of_poisoning_the_result():
    with pytest.raises(ValueError, match="NaN"):
        compute_impedance("rho + 0*f", [50.0], {"rho": float("nan")})


def test_nan_error_names_the_formula_and_the_frequency():
    with pytest.raises(ValueError) as excinfo:
        compute_impedance("rho * f", [10.0, 50.0], {"rho": float("nan")})
    message = str(excinfo.value)
    assert "rho * f" in message
    assert "10 Hz" in message


def test_capacitive_branch_at_dc_is_infinite_and_not_an_error():
    """A capacitor at 0 Hz genuinely has infinite impedance. IEEE 754 complex
    division returns ``nan`` for one of the two components there, which must
    not be mistaken for a failed computation."""
    z = _z("1/(j*2*pi*f*C)", [0.0, 50.0], {"C": 1e-9}, freq=0.0)
    assert math.isinf(z.imag)
    assert not math.isnan(z.real)


def test_simple_pole_is_infinite_and_not_an_error():
    z = _z("1/(f - 50)", [50.0], {})
    assert math.isinf(z.real) and not math.isnan(z.imag)


def test_compute_real_value_raises_on_nan_instead_of_storing_it():
    with pytest.raises(ValueError) as excinfo:
        compute_real_value(
            "rho + 0*f", [50.0], {"rho": float("nan")}, name="R_self"
        )
    message = str(excinfo.value)
    assert "NaN" in message
    # The raise happens inside ``compute_impedance``, which knows the formula
    # but not the field. Eight R/L/C/M formulas share that pipeline, so the
    # field name has to be carried out with the error.
    assert message.startswith("R_self: ")


def test_compute_real_value_keeps_the_open_end_sentinel_as_inf():
    """``R_formula="nan"`` is used in the existing suite and must stay
    ``inf`` -- the split is between NaN and inf, not between inf and finite."""
    values = compute_real_value("nan", [50.0], {}, name="R_self")
    assert math.isinf(values[50.0])


def test_compute_real_value_still_rejects_a_complex_result():
    with pytest.raises(ValueError) as excinfo:
        compute_real_value("j * f", [50.0], {}, name="R_self")
    message = str(excinfo.value)
    assert "non-real" in message
    assert message.startswith("R_self: ")


# ---------------------------------------------------------------------------
# F6 -- an infinite impedance is zero admittance, not NaN admittance
# ---------------------------------------------------------------------------


def test_one_open_end_bus_does_not_poison_the_whole_network():
    """The regression this batch exists for: two properly earthed towers and
    one without an electrode. Before the fix ``1/complex(inf, inf)`` put NaN
    on the diagonal and the entire calculation failed with a message about a
    missing path to reference earth."""
    types = {"A": _grounded_type(), "B": _open_type(), "C": _grounded_type()}
    net = _chain_network("p12_open_bus", types)
    gi.run_fault(net, "F")
    epr = _epr(net)
    assert set(epr) == {"A", "B", "C"}
    assert all(math.isfinite(value) for value in epr.values())
    assert max(epr.values()) > 0.0


def test_open_end_bus_matches_a_network_without_that_electrode():
    """Physical cross-check: a bus whose grounding impedance is infinite must
    behave exactly like a bus that contributes no grounding admittance at
    all -- here approximated by a very large finite electrode resistance."""
    huge = gi.BusType(name="huge", description="", system_type="Tower",
                      voltage_level=110.0, impedance_formula="rho * 1e9")
    types_open = {"A": _grounded_type(), "B": _open_type(), "C": _grounded_type()}
    types_huge = {"A": _grounded_type("g2"), "B": huge, "C": _grounded_type("g2")}

    net_open = _chain_network("p12_cmp_open", types_open)
    net_huge = _chain_network("p12_cmp_huge", types_huge)
    gi.run_fault(net_open, "F")
    gi.run_fault(net_huge, "F")

    epr_open, epr_huge = _epr(net_open), _epr(net_huge)
    for bus in ("A", "B", "C"):
        assert epr_open[bus] == pytest.approx(epr_huge[bus], rel=1e-6)


def test_open_end_branch_does_not_poison_the_whole_network():
    """Same defect on the branch side: a grounding conductor written as an
    open end (a shield that is not bonded at this span)."""
    types = {name: _grounded_type() for name in ("A", "B", "C")}
    net = _chain_network("p12_open_branch", types)
    open_ct = gi.BranchType(
        name="unbonded", grounding_conductor=True,
        self_impedance_formula="nan", mutual_impedance_formula="nan",
    )
    net.branches["A-B"].type = open_ct
    net.branches["A-B"].calculate_impedance(net.frequencies)

    gi.run_fault(net, "F")
    epr = _epr(net)
    assert all(math.isfinite(value) for value in epr.values())


def test_open_end_bus_draws_no_earth_current():
    """The reciprocal appears a second time when the earth current per bus is
    computed. An unearthed tower must show exactly zero current into the soil,
    not a NaN in the ``I_bus_A`` column -- and a NaN there is worse than a
    crash, because it survives into the result table."""
    types = {"A": _grounded_type(), "B": _open_type(), "C": _grounded_type()}
    net = _chain_network("p12_open_bus_ia", types)
    gi.run_fault(net, "F")

    currents = _rms(net.res_buses(), "bus_name", "I_bus_A")
    assert all(math.isfinite(value) for value in currents.values())
    assert currents["B"] == 0.0
    # ... and the earthed towers do carry current, so the zero above is a
    # statement about bus B and not about a network that does nothing.
    assert currents["A"] > 0.0 and currents["C"] > 0.0


def test_open_end_branch_carries_no_shield_current():
    """Same reciprocal in ``compute_branch_currents``: an unbonded shield
    carries no current, it does not carry NaN amperes."""
    types = {name: _grounded_type() for name in ("A", "B", "C")}
    net = _chain_network("p12_open_branch_is", types)
    net.branches["A-B"].type = gi.BranchType(
        name="unbonded_is", grounding_conductor=True,
        self_impedance_formula="nan", mutual_impedance_formula="nan",
    )
    net.branches["A-B"].calculate_impedance(net.frequencies)
    gi.run_fault(net, "F")

    currents = _rms(net.res_branches(), "branch_name", "I_branch_A")
    assert all(math.isfinite(value) for value in currents.values())
    assert currents["A-B"] == 0.0
    assert currents["B-C"] > 0.0


def test_open_end_branch_in_the_automatic_phase_current_mode():
    """``auto_parallel_coefficients=True`` builds a second, phase-side
    admittance matrix from the same impedances -- with its own reciprocal, and
    therefore its own copy of the defect."""
    types = {name: _grounded_type() for name in ("A", "B", "C")}
    net = _chain_network("p12_open_branch_auto", types)
    net.branches["A-B"].type = gi.BranchType(
        name="unbonded_auto", grounding_conductor=True,
        self_impedance_formula="nan", mutual_impedance_formula="nan",
    )
    net.branches["A-B"].calculate_impedance(net.frequencies)
    gi.run_fault(net, "F", auto_parallel_coefficients=True)

    epr = _epr(net)
    assert all(math.isfinite(value) for value in epr.values())
    currents = _rms(net.res_branches(), "branch_name", "I_branch_A")
    assert all(math.isfinite(value) for value in currents.values())
    assert currents["A-B"] == 0.0


def test_voltage_source_with_an_infinite_internal_impedance_is_switched_out():
    """A Thevenin source with an infinite internal impedance is an infeed that
    is out of service. ``Z_src = 0`` has always meant "no source" here; the
    infinite case has to mean the same rather than injecting a NaN Norton
    current and a NaN loop admittance."""
    types = {name: _grounded_type() for name in ("A", "B", "C")}
    net = _chain_network("p12_open_vsource", types)
    net.sources.clear()
    inf = complex(float("inf"), float("inf"))
    gi.create_voltage_source(
        name="v", bus="A", voltage={50.0: 20000.0 + 0.0j},
        source_impedance={50.0: inf}, network=net,
    )
    net.invalidate_paths()
    gi.run_fault(net, "F")

    epr = _epr(net)
    assert all(math.isfinite(value) for value in epr.values())
    # No injection at all: an out-of-service infeed raises no earth potential.
    assert all(value == 0.0 for value in epr.values())


def test_nan_in_the_nodal_system_is_reported_as_a_computation_error():
    """NaN written straight into ``bus.impedance`` -- e.g. from an old
    database or an external import -- must not be reported as a topology
    error."""
    from groundinsight.models.core_models import ComplexNumber

    types = {name: _grounded_type() for name in ("A", "B", "C")}
    net = _chain_network("p12_nan_matrix", types)
    net.buses["B"].impedance = {
        50.0: ComplexNumber(real=float("nan"), imag=0.0)
    }

    with pytest.raises(ValueError) as excinfo:
        gi.run_fault(net, "F")
    message = str(excinfo.value)
    assert "NaN" in message
    # The exact phrase, not a bare ``"B"``: the word "Buses" in the heading
    # satisfies a substring search all by itself, so the loose assertion
    # passed even with the culprit list emptied out.
    assert "Buses with a NaN grounding impedance: B." in message
    assert "not a topology error" in message


def test_nan_entering_only_through_the_injection_vector_is_caught():
    """NaN in a *mutual* impedance never reaches ``Y``.

    The mutual term is a Norton injection, so a NaN there leaves the admittance
    matrix perfectly clean and poisons only ``i``. Checking ``Y`` alone lets it
    through, and the solve then reports the misleading "no path to reference
    earth" -- which is the message this whole diagnosis exists to replace."""
    from groundinsight.models.core_models import ComplexNumber

    types = {name: _grounded_type() for name in ("A", "B", "C")}
    net = _chain_network("p12_nan_ivector", types)
    branch = net.branches["A-B"]
    assert all(
        not math.isnan(cn.real) and not math.isnan(cn.imag)
        for cn in branch.self_impedance.values()
    ), "the self impedance must stay healthy or Y is poisoned too"
    branch.mutual_impedance = {
        50.0: ComplexNumber(real=float("nan"), imag=0.0)
    }

    with pytest.raises(ValueError) as excinfo:
        gi.run_fault(net, "F")
    message = str(excinfo.value)
    assert "not a topology error" in message
    assert "Branches with a NaN impedance: A-B (mutual)." in message


def test_all_zero_impedances_are_rejected_before_the_solve():
    """Superseded by audit pass 15.

    Pass 12 made the *diagnosis* of a Z = 0 network specific: the network was
    still built, still solved to "Singular", and the message then explained
    that Z = 0 means "not connected" in this model. Pass 15 removed that
    convention -- the limit of a grounding impedance going to zero is a perfect
    electrode, not a missing one -- so zero no longer reaches the solver at
    all. The rejection happens where the impedance is computed, which is the
    only place that can still name the formula.
    """
    zero = gi.BusType(name="zero_z", description="", system_type="Tower",
                      voltage_level=110.0, impedance_formula="rho * 0")
    types = {name: zero for name in ("A", "B", "C")}

    with pytest.raises(ValueError) as excinfo:
        _chain_network("p12_zero_z", types)
    message = str(excinfo.value)
    assert "exactly zero" in message
    assert "50 Hz" in message
    assert "rho * 0" in message
    assert "1e-6" in message               # the actionable replacement
    # Not a topology diagnosis any more: the old message sent the reader off
    # to look for a missing earth connection that was never the problem.
    assert "Singular" not in message


def test_all_open_ends_report_the_sentinel_case_specifically():
    types = {name: _open_type() for name in ("A", "B", "C")}
    net = _chain_network("p12_all_open", types)

    with pytest.raises(ValueError) as excinfo:
        gi.run_fault(net, "F")
    message = str(excinfo.value)
    assert "Singular" in message
    assert "open-end sentinel" in message


def test_diagnosis_message_does_not_list_every_bus_of_a_large_network():
    """A systematic modelling error in a 60-bus network must not bury the
    explanation under 60 names.

    The systematic error used here is the open-end sentinel on every bus --
    60 towers, none of them with an earth electrode. Pass 15 rejects Z = 0
    before the solve, so the sentinel is now the reachable way to make a whole
    network un-referenced.
    """
    zero = gi.BusType(name="open_big", description="", system_type="Tower",
                      voltage_level=110.0, impedance_formula="nan")
    ct = gi.BranchType(name="c_big", grounding_conductor=True,
                       self_impedance_formula="(0.30 + j*f*0.0025) * l",
                       mutual_impedance_formula="(0.05 + j*f*0.0020) * l")
    net = gi.create_network(name="p12_big", frequencies=[50.0], description="")
    for idx in range(60):
        gi.create_bus(name=f"b{idx}", type=zero,
                      specific_earth_resistance=100.0, network=net)
    for idx in range(59):
        gi.create_branch(name=f"l{idx}", type=ct, from_bus=f"b{idx}",
                         to_bus=f"b{idx+1}", length=0.3, network=net)
    gi.create_source(name="s", bus="b0", values={50.0: 5000.0}, r_to_x=0.1,
                     network=net)
    gi.create_fault(name="F", bus="b59", scalings={50.0: 1.0}, t_k_s=0.5,
                    n_factor=1.0, network=net)

    with pytest.raises(ValueError) as excinfo:
        gi.run_fault(net, "F")
    message = str(excinfo.value)
    assert "(60 in total)" in message
    assert "b59" not in message           # truncated, not dumped

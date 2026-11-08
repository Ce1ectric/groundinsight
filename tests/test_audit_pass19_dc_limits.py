# tests/test_audit_pass19_dc_limits.py
"""
Audit pass 19 -- two decisions at 0 Hz that the data-sheet how-to brought up.

Finding IDs: D7 (found while documenting what the evaluator returns for the
Carson formulas at 0 Hz). Both refine the DC treatment of pass 16: the
resolution test of the limit (D1 there) and the passivity rule, which holds at
0 Hz as at every other frequency.

**The resolution warning was not scale-aware.** After the approach to 0 Hz
(``1e-6, 1e-7, 1e-8 Hz``) the evaluator reported a limit as poorly resolved
when the real part still moved by more than ``1e-6 * |Re Z| + 1e-12 Ohm`` over
the last decade. For a real part without a constant term the relative half is
useless -- ``a*f`` moves by 90 % of its value per decade, however small -- and
the absolute floor does not scale with the formula. Every Carson mutual
impedance ``(pi**2*1e-4*f + j*4*pi*1e-4*f*log(...))*l`` therefore warned
"converges only slowly" from a length of 11.3 m on, in every network that
lists 0 Hz -- every DC study with cable types -- although its value at 0 Hz is
``pi**2*1e-12*l`` Ohm. (The FFT transient solver evaluates self impedances
only, and those carry the screen resistance as a constant term.) What
resolves a limit is the trend of the step, not its size: a step that shrinks
by a factor ``q`` per decade leaves at most ``q/(1-q)`` of itself between the
last probe and the limit. The test now also accepts ``q <= 0.2``. Anything
analytic in ``f`` gives 0.1, Carson's ``f*log(1/f)`` 0.12; ``sqrt(f)`` gives
0.32 and ``log(f)`` 1, and those stay reported -- for every length, because
``q`` does not depend on it.

**Minus infinity passed as an open circuit.** A bus formula with a ``log(f)``
term that no factor of ``f`` cancels is ``-inf`` at 0 Hz (``log(0)``). The
passivity checks inspected finite values only, so ``-inf`` passed, and the
solver read ``1/(-inf) = 0`` -- an open circuit, without a word, and only in the
0 Hz bin: the formula below, ``1 + ln f`` at 100 Ohm*m, is negative under
0.37 Hz, so a 0.1 Hz bin next to it was refused for its negative real part
all along. ``-inf`` is the limit of negative resistances, not an open circuit
(that is ``+inf``), so it is refused like a negative real part, in all three
places that apply the rules: the element check, the array check of the
transient FFT grid and the vectorised check of the solver. A network that
does not solve 0 Hz never evaluates the formula there and is not affected.

Every numerical assertion has a second, independent source: the step ratio is
recomputed with 50-digit mpmath arithmetic, the limits with ``sympy.limit``,
and the DC solution with a two-node system built by hand.
"""

import math
import warnings

import mpmath
import numpy as np
import pytest
import sympy

import groundinsight as gi
from groundinsight.models.core_models import ComplexNumber
from groundinsight.simulation import waveforms
from groundinsight.simulation.transient import TransientStudy
from groundinsight.utils.impedance_calculator import (
    DCLimitWarning,
    check_passive_impedance,
    check_passive_impedance_array,
    compute_impedance,
)

#: Carson's coupling between a cable screen and the earth return, as the
#: data-sheet how-to writes it (screen radius 16.5 mm). Its real part
#: ``pi**2*1e-4*f`` has no constant term: the limit at 0 Hz is zero.
CARSON_MUTUAL = "(pi**2*1e-4*f + j*4*pi*1e-4*f*log(658*sqrt(rho/f)/0.0165))*l"
#: The screen-earth loop of the same cable, with 0.727 Ohm/km screen resistance.
CARSON_SELF = (
    "(0.727 + pi**2*1e-4*f + j*4*pi*1e-4*f*log(658*sqrt(rho/f)/0.0165))*l"
)
#: A grounding impedance fitted with a logarithm of the frequency. Fine at
#: 50 Hz (1 + ln 50 = 4.91 Ohm at 100 Ohm*m), minus infinity at 0 Hz.
LOG_BUS = "rho*0.01 + log(f)"

#: The approach frequencies of the evaluator, restated as strings so that the
#: independent recomputation below reads them at 50 digits.
PROBES = ("1e-6", "1e-7", "1e-8")
#: The decay the evaluator accepts, restated.
DECAY = 0.2

#: Lengths from 1 m to 1000 km, including both sides of the former 11.3 m edge.
LENGTHS = (0.001, 0.0113, 0.012, 1.0, 37.5, 1000.0)

_F, _RHO, _L = sympy.symbols("f rho l", positive=True)


def _expr(formula, params):
    """The formula as a SymPy expression in ``f`` with the parameters bound."""
    expr = sympy.sympify(
        formula, locals={"f": _F, "rho": _RHO, "l": _L, "j": sympy.I}
    )
    return expr.subs({_RHO: params.get("rho", 100.0), _L: params.get("l", 1.0)})


def _real_steps(formula, params):
    """``(e1, e2)``: the two real-part steps of the approach, at 50 digits.

    Independent of the evaluator's double-precision arithmetic: the formula is
    re-parsed by SymPy and evaluated by mpmath at the three probe frequencies.
    """
    fn = sympy.lambdify(_F, _expr(formula, params), modules="mpmath")
    with mpmath.workdps(50):
        re = [mpmath.re(fn(mpmath.mpf(p))) for p in PROBES]
        return float(abs(re[0] - re[1])), float(abs(re[1] - re[2]))


def _limit(formula, params):
    """The analytic limit at ``f -> 0+``, as a Python complex number."""
    return complex(sympy.limit(_expr(formula, params), _F, 0, "+"))


def _dc(formula, params):
    """``Z(0)`` from the evaluator, as a Python complex number."""
    value = compute_impedance(formula, [0.0], params)[0.0]
    return complex(value.real, value.imag)


def _network(name, bus_formula, frequencies):
    """Two buses A-B joined by one Carson cable; source at A, fault at B."""
    freqs = list(frequencies)
    bus_type = gi.BusType(
        name=f"bt_{name}", system_type="Substation", voltage_level=20.0,
        impedance_formula=bus_formula,
    )
    cable = gi.BranchType(
        name=f"cab_{name}", grounding_conductor=True,
        self_impedance_formula=CARSON_SELF,
        mutual_impedance_formula=CARSON_MUTUAL,
    )
    net = gi.create_network(name=name, frequencies=freqs)
    for label in ("A", "B"):
        gi.create_bus(name=label, type=bus_type,
                      specific_earth_resistance=100.0, network=net)
    gi.create_branch(name="A-B", type=cable, from_bus="A", to_bus="B",
                     length=1.0, network=net)
    gi.create_source(name="s", bus="A",
                     values={f: 1000.0 + 0.0j for f in freqs}, network=net)
    gi.create_fault(name="F", bus="B", scalings={f: 1.0 for f in freqs},
                    network=net)
    return net


def _epr(net, freq):
    return {
        row["bus_name"]: float(row["EPR_V"])
        for row in net.res_buses().to_dicts()
        if row["frequency_Hz"] == float(freq)
    }


# ---------------------------------------------------------------------------
# The resolution test judges the trend of the step, not its size
# ---------------------------------------------------------------------------

#: Limits approached geometrically, all with a zero real part at 0 Hz -- the
#: case the relative test cannot judge.
QUIET = [
    pytest.param(CARSON_MUTUAL, id="carson-mutual"),
    pytest.param("(f + j*f*log(1/f))*l", id="linear"),
    pytest.param("(f*log(1/f) + j*f)*l", id="f-log-f"),
    pytest.param("(f**0.9 + j*f*log(1/f))*l", id="f^0.9"),
]

#: Slow or missing convergence. The ``j*f*log(1/f)`` term makes each of them
#: ``0*inf`` at 0 Hz, so the evaluator has to approach the limit.
LOUD = [
    pytest.param("(sqrt(f) + j*f*log(1/f))*l", id="sqrt"),
    pytest.param("(0.3 + sqrt(f) + j*f*log(1/f))*l", id="sqrt-with-dc-value"),
    pytest.param("(0.3 + 0.01*log(f) + j*f*log(1/f))*l", id="log"),
]


@pytest.mark.parametrize("formula", QUIET)
def test_a_limit_approached_geometrically_is_quiet_at_every_length(formula):
    """The Carson mutual impedance warned from 11.3 m on; no length warns now.

    The value is still the formula at the last probe -- nothing is
    extrapolated -- and it lies within the geometric tail ``e2*q/(1-q)`` of
    the analytic limit, which is what accepting ``q <= 0.2`` promises.
    """
    limit_per_km = _limit(formula, {"rho": 100.0, "l": 1.0})
    for length in LENGTHS:
        params = {"rho": 100.0, "l": length}
        with warnings.catch_warnings():
            warnings.simplefilter("error", DCLimitWarning)
            value = _dc(formula, params)
        e1, e2 = _real_steps(formula, params)
        q = e2 / e1
        assert q <= DECAY, (length, q)
        assert value.imag == 0.0
        tail = e2 * q / (1.0 - q)
        assert abs(value.real - limit_per_km.real * length) <= tail * (1 + 1e-9)


def test_the_carson_mutual_impedance_is_practically_zero_at_dc():
    """The number the how-to page quotes: about pi**2*1e-12*l Ohm."""
    for length in LENGTHS:
        with warnings.catch_warnings():
            warnings.simplefilter("error", DCLimitWarning)
            value = _dc(CARSON_MUTUAL, {"rho": 100.0, "l": length})
        assert value.real == pytest.approx(math.pi**2 * 1e-12 * length, rel=1e-9)
        assert _limit(CARSON_MUTUAL, {"rho": 100.0, "l": length}) == 0


@pytest.mark.parametrize("formula", LOUD)
def test_slow_or_missing_convergence_is_still_reported_at_every_length(formula):
    """``sqrt(f)`` and ``log(f)`` keep their warning, and it is one line per
    formula: the step ratio and the relative move do not depend on ``l``."""
    messages = set()
    for length in LENGTHS:
        with pytest.warns(DCLimitWarning, match="converges only slowly") as record:
            _dc(formula, {"l": length})
        messages |= {
            str(w.message) for w in record if issubclass(w.category, DCLimitWarning)
        }
        e1, e2 = _real_steps(formula, {"l": length})
        assert e2 / e1 > DECAY
    assert len(messages) == 1


@pytest.mark.parametrize(
    "formula, ratio",
    [
        pytest.param("(sqrt(f) + j*f*log(1/f))*l", "0.32", id="sqrt"),
        pytest.param("(0.3 + 0.01*log(f) + j*f*log(1/f))*l", "1", id="log"),
    ],
)
def test_the_warning_quotes_the_step_ratio_it_measured(formula, ratio):
    """The message says why: the step of the last decade against the one
    before, which is what separates slow convergence from a small value."""
    e1, e2 = _real_steps(formula, {"l": 1.0})
    assert f"{e2 / e1:.2g}" == ratio
    with pytest.warns(DCLimitWarning) as record:
        _dc(formula, {"l": 1.0})
    message = str(record[0].message)
    assert f"{ratio} times the step of the decade before" in message


def test_a_dc_study_with_carson_cables_is_quiet():
    """A network that lists 0 Hz evaluates the mutual impedance there when the
    branch is built, so the warning came with every cable longer than 11 m.
    The DC solution is checked against a two-node system built by hand:
    electrodes of ``rho*0.01 + 1 = 2 Ohm``, the screen's 0.727 Ohm, and no
    coupling, since ``Z_m(0)`` is about 1e-11 Ohm."""
    with warnings.catch_warnings():
        warnings.simplefilter("error", DCLimitWarning)
        net = _network("p19_dc_quiet", "rho*0.01 + 1.0 + j*f*1e-4", [0.0, 50.0])
        gi.run_fault(net, "F")
    epr = _epr(net, 0.0)
    g = 1.0 / 0.727
    y = np.array([[1.0 / 2.0 + g, -g], [-g, 1.0 / 2.0 + g]])
    u = np.linalg.solve(y, np.array([1000.0, -1000.0]))
    # ``EPR_V`` is a magnitude; the sign lives in ``EPR_degree``.
    assert epr["A"] == pytest.approx(abs(u[0]), rel=1e-9)
    assert epr["B"] == pytest.approx(abs(u[1]), rel=1e-9)


# ---------------------------------------------------------------------------
# Minus infinity is not an open circuit
# ---------------------------------------------------------------------------


def test_a_bus_formula_that_runs_to_minus_infinity_at_dc_is_refused():
    """Refused where the bus is built, naming the cause and the remedy. The
    second way: ``sympy.limit`` says the same about the formula -- this is
    the formula's limit, not a rounding artefact of ``log(0.0)``."""
    assert sympy.limit(_expr(LOG_BUS, {"rho": 100.0}), _F, 0, "+") == -sympy.oo
    bus_type = gi.BusType(
        name="bt_p19_log_dc", system_type="Substation", voltage_level=20.0,
        impedance_formula=LOG_BUS,
    )
    net = gi.create_network(name="p19_log_dc", frequencies=[0.0, 50.0])
    with pytest.raises(ValueError, match="minus infinity") as excinfo:
        gi.create_bus(name="A", type=bus_type,
                      specific_earth_resistance=100.0, network=net)
    message = str(excinfo.value)
    assert "at 0 Hz" in message
    assert "log(f)" in message
    assert "not an open circuit" in message
    assert "A" not in net.buses
    # The bins next to 0 Hz agree: below 1/e Hz the formula is a negative
    # resistance, refused before this pass as well.
    near = compute_impedance(LOG_BUS, [0.1], {"rho": 100.0})
    assert near[0.1].real == pytest.approx(1.0 + math.log(0.1), rel=1e-15)
    with pytest.raises(ValueError, match="negative"):
        check_passive_impedance(near, element="bus 'X'")


def test_the_same_formula_is_untouched_in_a_network_without_0_hz():
    """No 0 Hz, no evaluation at 0 Hz: the bus is built and the study solves
    exactly as with the same impedance written as a constant."""
    net = _network("p19_log_ac", LOG_BUS, [50.0])
    z = net.buses["A"].impedance[50.0]
    assert z.real == pytest.approx(1.0 + math.log(50.0), rel=1e-15)
    gi.run_fault(net, "F")
    reference = _network("p19_log_ac_ref", f"rho*0 + {1.0 + math.log(50.0)!r}", [50.0])
    gi.run_fault(reference, "F")
    got, ref = _epr(net, 50.0), _epr(reference, 50.0)
    for bus in ("A", "B"):
        assert got[bus] == pytest.approx(ref[bus], rel=1e-12)


@pytest.mark.filterwarnings("ignore::groundinsight.TransientModelWarning")
def test_a_transient_study_refuses_it_at_its_0_hz_bin():
    """The same network in a transient study: the FFT grid starts at 0 Hz,
    so the formula is evaluated there after all, and refused there."""
    net = _network("p19_log_tr", LOG_BUS, [50.0])
    study = TransientStudy(net, fault_name="F")
    study.set_source_waveform("s", waveforms.step(amplitude=1000.0, t_on=0.0))
    study.set_observation(buses=["A", "B"], branches=["A-B"])
    with pytest.raises(ValueError, match="minus infinity") as excinfo:
        study.solve(t_end=0.04, dt=1e-4, solver="fft")
    assert "transient FFT grid" in str(excinfo.value)


def test_the_three_passivity_checks_agree_on_minus_infinity():
    """Element check, array check and the solver's vectorised check apply
    one rule. The last one guards values that bypassed the first two, e.g. a
    value written into ``bus.impedance`` directly."""
    minus_inf = ComplexNumber(real=-math.inf, imag=0.0)
    with pytest.raises(ValueError, match="minus infinity"):
        check_passive_impedance({0.0: minus_inf}, element="bus 'X'")
    with pytest.raises(ValueError, match="minus infinity"):
        check_passive_impedance_array(
            np.array([complex(-math.inf, 0.0), complex(2.0, 0.3)]),
            np.array([0.0, 50.0]),
            element="bus 'X'",
        )
    net = _network("p19_minus_inf_stored", "rho*0.01 + 1.0 + j*f*1e-4", [0.0, 50.0])
    net.buses["A"].impedance[0.0] = minus_inf
    with pytest.raises(ValueError, match="minus infinity"):
        gi.run_fault(net, "F")


def test_minus_infinity_is_refused_at_any_frequency():
    """The rule is about the value, not about 0 Hz: ``log(f - 50)`` is minus
    infinity at 50 Hz and would have disconnected the bus there."""
    with pytest.raises(ValueError, match="minus infinity"):
        check_passive_impedance(
            {50.0: ComplexNumber(real=-math.inf, imag=1.0)}, element="bus 'X'"
        )
    values = compute_impedance("rho*0.01 + log(f - 50)", [50.0, 60.0], {"rho": 100.0})
    assert values[50.0].real == -math.inf
    with pytest.raises(ValueError, match="minus infinity"):
        check_passive_impedance(values, element="bus 'X'")


@pytest.mark.parametrize(
    "value",
    [
        pytest.param(complex(math.inf, 0.0), id="plus-inf-like-1-over-f"),
        pytest.param(complex(math.inf, math.inf), id="open-end-sentinel"),
        pytest.param(complex(0.0, -math.inf), id="capacitance-at-dc"),
        pytest.param(complex(0.0, math.inf), id="plus-j-inf"),
        pytest.param(complex(2.0, -math.inf), id="r-in-series-with-c-at-dc"),
    ],
)
def test_open_circuits_stay_accepted(value):
    """Only a real part of minus infinity is refused. Every form an open
    circuit takes -- including the capacitance, whose reactance runs to
    ``-j*inf`` -- passes all checks at 0 Hz and above."""
    check_passive_impedance(
        {0.0: ComplexNumber(real=value.real, imag=value.imag),
         50.0: ComplexNumber(real=value.real, imag=value.imag)},
        element="bus 'X'",
    )
    check_passive_impedance_array(
        np.array([value, value]), np.array([0.0, 50.0]), element="bus 'X'"
    )


def test_a_log_term_behind_a_carson_term_was_already_refused():
    """The other way the same formula reaches 0 Hz. With a ``f*log`` term
    beside it the value at 0 Hz is ``NaN``, the evaluator approaches the
    limit, reports that it does not settle, and the value it reaches is a
    finite negative resistance -- refused by the existing rule. With the new
    rule both spellings end in a refusal."""
    formula = "rho*0.01 + log(f) + j*4*pi*1e-4*f*log(658*sqrt(rho/f))"
    bus_type = gi.BusType(
        name="bt_p19_log_carson", system_type="Substation", voltage_level=20.0,
        impedance_formula=formula,
    )
    net = gi.create_network(name="p19_log_carson", frequencies=[0.0, 50.0])
    with (
        pytest.warns(DCLimitWarning, match="converges only slowly"),
        pytest.raises(ValueError, match="negative"),
    ):
        gi.create_bus(name="A", type=bus_type,
                      specific_earth_resistance=100.0, network=net)

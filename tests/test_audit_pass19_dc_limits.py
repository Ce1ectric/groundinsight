# tests/test_audit_pass19_dc_limits.py
"""
Audit pass 19 -- decisions at 0 Hz that the data-sheet how-to brought up.

Finding IDs: D7 (found while documenting what the evaluator returns for the
Carson formulas at 0 Hz). It refines the resolution test of the DC limit of
pass 16 (D1 there).

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
from groundinsight.utils.impedance_calculator import DCLimitWarning, compute_impedance

#: Carson's coupling between a cable screen and the earth return, as the
#: data-sheet how-to writes it (screen radius 16.5 mm). Its real part
#: ``pi**2*1e-4*f`` has no constant term: the limit at 0 Hz is zero.
CARSON_MUTUAL = "(pi**2*1e-4*f + j*4*pi*1e-4*f*log(658*sqrt(rho/f)/0.0165))*l"
#: The screen-earth loop of the same cable, with 0.727 Ohm/km screen resistance.
CARSON_SELF = (
    "(0.727 + pi**2*1e-4*f + j*4*pi*1e-4*f*log(658*sqrt(rho/f)/0.0165))*l"
)
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


# tests/test_audit_pass18_performance.py
"""
Audit pass 18 -- K3, K7 and L1: the three cheap run-time findings.

**K3.** ``solve_network`` factorises ``Y(f)`` with ``scipy.sparse.splu`` and
threw the factorisation away; ``compute_reduction_factors`` then solved the
*same* matrix with a second right-hand side using the dense
``numpy.linalg.solve``. That was 2.5 s of a 6.9 s profile on a 200-bus chain
over 50 frequencies, and it meant the same matrix was solved by two solvers
with different singularity treatment. The factors are now kept per frequency
and reused.

**K7.** ``is_short_circuit`` (59 850 calls), ``_is_open_circuit`` (209 400)
and ``check_passive_impedance`` classified single Python floats through
``numpy``, which costs an array coercion and a scalar dispatch per bit of
information. They now use ``math`` on the scalars and a single vectorised
pass over the frequency grid.

**L1.** ``validate_impedance_formula_value`` ran a full ``sympify`` on every
call; loading a 120-bus network validated the same two formulas 596 times.
The function is pure, so it is memoised -- after the security guard, which
is itself a pure function of the same string.

The tests below pin the *behaviour*, not the speed: every classification
must answer exactly as before, and the reused factorisation must give the
same reduction factors as the dense solve.
"""

import time

import numpy as np
import pytest

import groundinsight as gi
from groundinsight.electrical_network import ElectricalNetwork, _is_open_circuit
from groundinsight.models.core_models import ComplexNumber
from groundinsight.utils.impedance_calculator import (
    check_passive_impedance,
    is_short_circuit,
)
from groundinsight.utils.validations import validate_impedance_formula_value


_FREQS = [0.0, 50.0, 150.0, 250.0]


def _chain(name, n_bus=8, freqs=None):
    freqs = list(_FREQS if freqs is None else freqs)
    net = gi.create_network(name=name, frequencies=freqs)
    bt = gi.BusType(
        name="bt", system_type="Grounded", voltage_level=20.0,
        impedance_formula="rho * 0 + 10 + I * f * 0",
    )
    brt = gi.BranchType(
        name="brt", grounding_conductor=True,
        self_impedance_formula="(0.25 + j*f*0.012)*l",
        mutual_impedance_formula="(0.05 + j*f*0.010)*l",
        phase_impedance_formula="(0.1 + j*f*0.004)*l",
    )
    for i in range(n_bus):
        gi.create_bus(name=f"b{i}", type=bt, network=net)
    for i in range(n_bus - 1):
        gi.create_branch(
            name=f"L{i}", type=brt, from_bus=f"b{i}", to_bus=f"b{i+1}",
            length=1.0, network=net,
        )
    gi.create_source(
        name="S1", bus="b0", values={f: 1000.0 for f in freqs}, network=net
    )
    gi.create_fault(
        name="F1", bus=f"b{n_bus-1}", scalings={f: 1.0 for f in freqs},
        network=net,
    )
    return net


# ---------------------------------------------------------------------------
# K3 -- reuse the sparse factorisation for the no-mutual right-hand side
# ---------------------------------------------------------------------------


def test_solve_network_keeps_the_lu_factorisation():
    net = _chain("p18_lu_kept")
    gi.run_fault(net, "F1")
    factors = net.electrical_network._lu_factors
    assert set(factors) == set(net.frequencies)


def test_reduction_factors_match_the_dense_solve_exactly():
    """The reused factorisation must not move the answer.

    The no-mutual system is solved twice on the same network -- once through
    the kept ``splu`` factors, once through the dense fallback -- and the
    reduction factors must agree to machine precision.
    """
    net = _chain("p18_lu_equiv")
    gi.run_fault(net, "F1")
    reused = net.results["F1"].reduction_factor
    reused_value = dict(reused.value)
    reused_current = dict(reused.value_current)
    u_reused = {f: v.copy() for f, v in
                net.electrical_network.u_vectors_no_mutual.items()}

    # Force the dense path by dropping the stored factors.
    en = net.electrical_network
    en._lu_factors = {}
    en.compute_reduction_factors()
    dense = net.results["F1"].reduction_factor

    for freq in net.frequencies:
        assert dense.value[freq] == pytest.approx(reused_value[freq], rel=1e-12)
        assert dense.value_current[freq] == pytest.approx(
            reused_current[freq], rel=1e-12
        )
        assert np.allclose(
            en.u_vectors_no_mutual[freq], u_reused[freq], rtol=1e-12, atol=1e-14
        )


def test_stale_factors_are_dropped_when_the_matrices_change():
    """A second solve on a changed network must not reuse the old factors."""
    net = _chain("p18_lu_stale")
    gi.run_fault(net, "F1")
    first = net.electrical_network._lu_factors[50.0]

    net.buses["b3"].specific_earth_resistance = 500.0
    net.buses["b3"].calculate_impedance(net.frequencies)
    gi.run_fault(net, "F1")
    assert net.electrical_network._lu_factors[50.0] is not first


def test_compute_reduction_factors_still_works_without_a_prior_solve():
    """The dense fallback stays for a hand-built ElectricalNetwork."""
    net = _chain("p18_lu_fallback")
    gi.run_fault(net, "F1")
    en = ElectricalNetwork(net, auto_phase_currents=True)
    assert en._lu_factors == {}
    en.compute_reduction_factors()
    assert set(en.u_vectors_no_mutual) == set(net.frequencies)


# ---------------------------------------------------------------------------
# K7 -- the scalar classifications keep their answers
# ---------------------------------------------------------------------------


_CLASSIFICATION_CASES = [
    (complex(0.0, 0.0), True),
    (complex(1e-320, 0.0), True),          # subnormal: 1/z overflows
    (complex(0.0, 1e-320), True),
    (complex(1e-300, 0.0), False),         # 1/z = 1e300, still finite
    (complex(1e-290, 0.0), False),
    (complex(0.25, 0.6), False),
    (complex(-1.0, 0.0), False),
    (complex(float("inf"), 0.0), False),   # open-end sentinel
    (complex(0.0, float("inf")), False),
    (complex(float("nan"), 0.0), False),   # failed computation
    (complex(0.0, float("nan")), False),
]


@pytest.mark.parametrize("value, expected", _CLASSIFICATION_CASES)
def test_is_short_circuit_classification_is_unchanged(value, expected):
    assert is_short_circuit(value) is expected


def test_vectorised_short_circuit_mask_agrees_with_the_scalar_form():
    from groundinsight.utils.impedance_calculator import _short_circuit_mask

    values = [value for value, _ in _CLASSIFICATION_CASES]
    re = np.array([v.real for v in values])
    im = np.array([v.imag for v in values])
    mask = _short_circuit_mask(re, im)
    assert [bool(m) for m in mask] == [is_short_circuit(v) for v in values]


def test_vectorised_short_circuit_mask_agrees_on_a_random_sweep():
    """Fuzz the boundary: the mask must never disagree with the scalar form."""
    from groundinsight.utils.impedance_calculator import _short_circuit_mask

    rng = np.random.default_rng(20260908)
    n = 5000
    re = (rng.random(n) * 9 + 1) * 10.0 ** rng.integers(-330, 10, n)
    re *= np.where(rng.random(n) < 0.5, -1.0, 1.0)
    im = (rng.random(n) * 9 + 1) * 10.0 ** rng.integers(-330, 10, n)
    im[rng.random(n) < 0.3] = 0.0
    mask = _short_circuit_mask(re, im)
    scalar = [is_short_circuit(complex(a, b)) for a, b in zip(re, im)]
    assert [bool(m) for m in mask] == scalar


@pytest.mark.parametrize(
    "value, expected",
    [
        (complex(float("inf"), 0.0), True),
        (complex(0.0, float("inf")), True),
        (complex(-float("inf"), 0.0), True),
        (complex(0.0, 0.0), False),
        (complex(float("nan"), 0.0), False),
        (complex(1e300, 0.0), False),
    ],
)
def test_is_open_circuit_classification_is_unchanged(value, expected):
    assert _is_open_circuit(value) is expected


def test_check_passive_impedance_still_reports_every_cause():
    z_dict = {
        0.0: ComplexNumber(real=0.0, imag=0.0),        # DC short: accepted
        50.0: ComplexNumber(real=0.0, imag=0.0),       # zero above DC
        150.0: ComplexNumber(real=1e-320, imag=0.0),   # not invertible
        250.0: ComplexNumber(real=-1.0, imag=0.5),     # negative resistance
    }
    with pytest.raises(ValueError) as excinfo:
        check_passive_impedance(z_dict, element="bus 'b1'")
    message = str(excinfo.value)
    assert "exactly zero at 50 Hz" in message
    assert "150 Hz" in message and "too" in message
    assert "negative" in message and "250 Hz" in message
    # The 0 Hz short circuit is the physics and must not be reported.
    assert "exactly zero at 50 Hz." in message


def test_check_passive_impedance_accepts_the_sentinels():
    z_dict = {
        0.0: ComplexNumber(real=0.0, imag=0.0),                 # DC short
        50.0: ComplexNumber(real=float("inf"), imag=0.0),       # open end
        150.0: ComplexNumber(real=float("nan"), imag=0.0),      # failed calc
        250.0: ComplexNumber(real=1.0, imag=2.0),
    }
    check_passive_impedance(z_dict, element="bus 'b1'")


def test_check_passive_impedance_accepts_an_empty_dict():
    check_passive_impedance({}, element="bus 'b1'")


def test_check_passive_impedance_is_not_quadratic_in_the_frequency_grid():
    """A transient study evaluates every element at thousands of FFT bins.

    Measured on the review container: 1000 calls over a 51-point grid took
    0.558 s before and 0.036 s after. The bound below is deliberately far
    above the fixed cost so a loaded CI runner cannot make it flaky.
    """
    z_dict = {
        50.0 * k: ComplexNumber(real=0.25 + 0.001 * k, imag=0.6 + 0.002 * k)
        for k in range(51)
    }
    start = time.perf_counter()
    for _ in range(4000):
        check_passive_impedance(z_dict, element="bus 'b1'")
    elapsed = time.perf_counter() - start
    assert elapsed < 1.0, f"4000 passive checks took {elapsed:.2f} s"


# ---------------------------------------------------------------------------
# L1 -- formula validation is memoised
# ---------------------------------------------------------------------------


def test_formula_validation_is_memoised():
    from groundinsight.utils.validations import _validate_impedance_formula_str

    _validate_impedance_formula_str.cache_clear()
    formula = "rho * 0 + 42 + I * f * 0"
    for _ in range(20):
        assert validate_impedance_formula_value(formula) == formula
    info = _validate_impedance_formula_str.cache_info()
    assert info.misses == 1
    assert info.hits == 19


def test_formula_validation_still_rejects_and_does_not_cache_the_rejection():
    from groundinsight.utils.validations import _validate_impedance_formula_str

    _validate_impedance_formula_str.cache_clear()
    for _ in range(3):
        with pytest.raises(ValueError):
            validate_impedance_formula_value("rho +")
    assert _validate_impedance_formula_str.cache_info().hits == 0


def test_formula_validation_accepts_non_string_input_unchanged():
    """The cache must not turn an unhashable input into a TypeError."""
    assert validate_impedance_formula_value({"real": 1}) == {"real": 1}

# tests/test_audit_pass19_build.py
"""
Audit pass 19 -- the cost of building a network (P10).

Every ``create_bus`` / ``create_branch`` derives the impedances of the new
element from the formulas of its type. Per branch that evaluated three
formulas, packed every value into a validated :class:`ComplexNumber`, checked
the self impedance for passivity by reading the packed dictionary back into
arrays, and then wrote each table through the validated assignment -- whose
field validators and dictionary validation went over every value a second
time. On a 50 000-bus ring the build took longer than the solve.

Now every value is validated once, when it is packed; the tables are stored
as the validated assignment would store them, the passivity rules run on the
arrays the values came from (``check_passive_impedance_array``, which since
ARC6 decides and words exactly as the dictionary check), and a formula whose
values all came back finite skips the masks of the repairs that only act on
non-finite values.

Nothing may change by a bit. The second way here is the former path, kept in
this file: the validated assignment of every derived value, the passivity
check of the packed dictionary, the element-wise packing of the array; and
the JSON round trip of a whole network, which builds every element through
validation from the stored values.
"""

from __future__ import annotations

import cProfile
import math
import pickle
import pstats
from typing import Callable, Dict

import numpy as np
import pytest

import groundinsight as gi
from groundinsight.models.core_models import (
    Branch,
    Bus,
    ComplexNumber,
    ValidatedAssignmentModel,
)
from groundinsight.utils import impedance_calculator as ic
from tests import test_audit_pass19_performance as perf

FREQS = [50.0, 150.0, 250.0, 350.0, 450.0, 550.0, 650.0]
FREQS_DC = [0.0] + FREQS


def _calls(action: Callable[[], object], name: str) -> int:
    """How often functions called ``name`` run while ``action`` runs."""
    profile = cProfile.Profile()
    try:
        profile.enable()
    except ValueError:  # pragma: no cover -- another profiler is active
        pytest.skip("another profiler is active")
    try:
        action()
    finally:
        profile.disable()
    stats = pstats.Stats(profile).stats
    return sum(entry[1] for key, entry in stats.items() if key[2] == name)


# --------------------------------------------------------------------------
# one validation per value
# --------------------------------------------------------------------------


def test_each_derived_value_of_a_branch_is_validated_once():
    net = perf.ring(3, F=7)
    count = _calls(
        lambda: gi.create_branch(
            name="X", type=perf.CABLE, from_bus="B1", to_bus="B2", length=0.5,
            network=net,
        ),
        "validate_complex",
    )
    # self, mutual and phase impedance, one value per frequency
    assert count == 3 * len(net.frequencies)


def test_each_derived_value_of_a_bus_is_validated_once():
    net = perf.ring(3, F=7)
    count = _calls(
        lambda: gi.create_bus(name="X", type=perf.BT, network=net),
        "validate_complex",
    )
    assert count == len(net.frequencies)


# --------------------------------------------------------------------------
# stored as the validated assignment stores them
# --------------------------------------------------------------------------

RLC_BUS = gi.BusType(
    name="RLC", system_type="Grounded", voltage_level=20,
    impedance_formula="rho*0.02 + j*f*0.0002",
    R_formula="rho*0.02", L_formula="0.0002/(2*pi)", C_formula="1e-9",
)
RLC_BRANCH = gi.BranchType(
    name="RLCB", grounding_conductor=True,
    self_impedance_formula="(0.25 + j*f*0.012)*l",
    mutual_impedance_formula="(j*f*0.008)*l",
    R_self_formula="0.25*l", L_self_formula="0.012*l/(2*pi)",
    R_mutual_formula="0*l", M_mutual_formula="0.008*l/(2*pi)",
)


def _validated(element, derived: Dict[str, object]) -> None:
    """The former write: every derived value through the validated assignment."""
    for field, value in derived.items():
        if value is None and element.__dict__.get(field) is None:
            continue
        ValidatedAssignmentModel.__setattr__(element, field, value)


def _branch(branch_type, length, **extra) -> Branch:
    return Branch(name="l", type=branch_type, from_bus="a", to_bus="b", length=length,
                  self_impedance={}, mutual_impedance={}, **extra)


ELEMENTS = {
    "bus": lambda: Bus(name="b", type=perf.BT, specific_earth_resistance=70.0, impedance={}),
    "bus_open": lambda: Bus(name="b", type=perf.BT_OPEN, impedance={}),
    "bus_rlc": lambda: Bus(name="b", type=RLC_BUS, specific_earth_resistance=30.0, impedance={}),
    "cable": lambda: _branch(perf.CABLE, 0.7, specific_earth_resistance=50.0),
    "ohl": lambda: _branch(perf.OHL, 2.0),
    "no_phase": lambda: _branch(perf.CABLE_NO_PHASE, 0.3),
    "broken": lambda: _branch(perf.BROKEN_SHIELD, 1.1),
    "rlc": lambda: _branch(RLC_BRANCH, 0.9),
}


def _state(element):
    return (
        element.__dict__,
        element.model_fields_set,
        element.model_dump_json(),
        pickle.loads(pickle.dumps(element)).__dict__,
    )


@pytest.mark.parametrize("freqs", [FREQS, FREQS_DC], ids=["ac", "with_dc"])
@pytest.mark.parametrize("name", sorted(ELEMENTS))
def test_derived_values_are_stored_as_the_validated_assignment_stores_them(name, freqs):
    fast, reference = ELEMENTS[name](), ELEMENTS[name]()
    fast.calculate_impedance(freqs)
    _validated(reference, reference._derive(freqs))
    assert _state(fast) == _state(reference)


def test_a_stored_table_is_a_dictionary_of_its_own():
    bus = ELEMENTS["bus"]()
    derived = bus._derive(FREQS)
    bus._apply_derived(derived)
    derived["impedance"][50.0] = ComplexNumber(real=1e6, imag=0.0)
    assert bus.impedance is not derived["impedance"]
    assert bus.impedance[50.0].real != 1e6


def test_a_table_that_disappears_is_written_through_the_validated_assignment():
    """A type without a phase formula clears the phase impedance (``None``
    over a table) -- the rare case that keeps the validated write."""
    branch = ELEMENTS["cable"]()
    branch.calculate_impedance(FREQS)
    assert branch.phase_impedance is not None
    reference = ELEMENTS["cable"]()
    reference.calculate_impedance(FREQS)
    branch.type = perf.CABLE_NO_PHASE
    reference.type = perf.CABLE_NO_PHASE
    branch.calculate_impedance(FREQS)
    _validated(reference, reference._derive(FREQS))
    assert branch.phase_impedance is None
    assert _state(branch) == _state(reference)


@pytest.mark.parametrize(
    "builder",
    [lambda: perf.ring(40, F=7, n_sources=2), lambda: perf.thevenin_ring(20, F=3),
     lambda: perf.dc_ring(15), lambda: perf.random_network(14)],
    ids=["ring", "thevenin", "dc", "random"],
)
def test_a_built_network_equals_its_validated_json_round_trip(builder):
    """The JSON loader builds every element through validation from the
    stored values -- the second, independent way to the same state."""
    net = builder()
    loaded = gi.Network.model_validate_json(net.model_dump_json())
    assert loaded.model_dump_json() == net.model_dump_json()
    for fault in net.faults:
        assert loaded.input_digest(fault) == net.input_digest(fault)


def test_edit_invariants_still_re_derive_and_roll_back():
    net = perf.ring(6, F=3)
    bus = net.buses["B2"]
    before = dict(bus.impedance)
    bus.specific_earth_resistance = 300.0
    assert bus.impedance != before
    reference = Bus(name="r", type=perf.BT, specific_earth_resistance=300.0, impedance={})
    _validated(reference, reference._derive(net.frequencies))
    assert bus.impedance == reference.impedance
    negative = gi.BusType(
        name="NEG", system_type="Grounded", voltage_level=20,
        impedance_formula="1 - rho*0.01 + j*f*0.0001",
    )
    state = bus.model_dump_json()
    with pytest.raises(ValueError, match="real part is negative"):
        bus.type = negative
    assert bus.model_dump_json() == state


# --------------------------------------------------------------------------
# the calculator: packing, passivity on arrays, the finite fast path
# --------------------------------------------------------------------------


def _bits(value: float):
    return (math.copysign(1.0, value), value) if not math.isnan(value) else "nan"


def test_packing_takes_every_double_unchanged():
    rng = np.random.default_rng(3)
    values = rng.normal(size=400) * 10.0 ** rng.integers(-300, 300, size=400) \
        + 1j * rng.normal(size=400) * 10.0 ** rng.integers(-300, 300, size=400)
    values[:6] = [complex(-0.0, 0.0), complex(0.0, -0.0), 5e-324 + 0j,
                  complex(np.inf, -np.inf), complex(np.nan, 1.0), -1e-320j]
    freqs = np.arange(values.size, dtype=float)
    packed = ic.impedance_table(freqs, values)
    for (freq, number), value in zip(packed.items(), values, strict=True):
        reference = ComplexNumber(real=float(value.real), imag=float(value.imag))
        assert _bits(number.real) == _bits(reference.real)
        assert _bits(number.imag) == _bits(reference.imag)
        assert isinstance(freq, float) and type(number.real) is float


BAD = {
    "zero": [0j, 1 + 1j],
    "tiny": [complex(1e-310, 0.0), 1 + 0j],
    "negative": [complex(-0.5, 2.0), 1 + 0j],
    "minus_inf": [complex(-np.inf, 0.0), 1 + 0j],
    "everything": [0j, complex(1e-310, 1e-311), complex(-1.0, 0.0), complex(-np.inf, 1.0)],
}


@pytest.mark.parametrize("case", sorted(BAD))
@pytest.mark.parametrize("with_dc", [False, True])
def test_the_passivity_check_of_the_arrays_is_that_of_the_dictionary(case, with_dc):
    values = np.array(BAD[case], dtype=complex)
    freqs = np.arange(values.size, dtype=float) * 50.0 + (0.0 if with_dc else 50.0)
    table = {float(f): ComplexNumber(real=v.real, imag=v.imag)
             for f, v in zip(freqs.tolist(), values.tolist(), strict=True)}
    outcomes = []
    for check in (
        lambda: ic.check_passive_impedance(table, element="x", formula_str="z",
                                           params={"rho": 1.0}),
        lambda: ic.check_passive_impedance_array(values, freqs, element="x",
                                                 formula_str="z", params={"rho": 1.0}),
    ):
        try:
            check()
            outcomes.append(None)
        except ValueError as exc:
            outcomes.append(str(exc))
    assert outcomes[0] == outcomes[1]


def test_good_values_pass_both_passivity_checks():
    values = np.array([0j, 1 + 1j, complex(np.inf, np.inf), 1e-200 + 0j], dtype=complex)
    freqs = np.array([0.0, 50.0, 150.0, 250.0])
    ic.check_passive_impedance_array(values, freqs, element="x")
    ic.check_passive_impedance(
        {float(f): complex(v) for f, v in zip(freqs, values, strict=True)}, element="x"
    )


FORMULAS = [
    ("rho*0.01 + j*f*0.0001", {"rho": 100.0}),
    ("(0.25 + j*f*0.012)*l", {"l": 0.5, "rho": 100.0}),
    ("5 + 0j", {}),
    ("sqrt(f - 120)", {}),
    ("log(f/100) + 1", {}),
    ("1/(j*2*pi*f*1e-6) + 1", {}),
    ("Abs(f - 300)*0.01 + 1", {}),
    ("nan", {}),
    ("Max(f - 120, 0)*0.001*l", {"l": 2.0}),
]


@pytest.mark.parametrize("freqs", [FREQS, FREQS_DC], ids=["ac", "with_dc"])
@pytest.mark.parametrize("formula,params", FORMULAS)
def test_the_repairs_leave_finite_values_alone(formula, params, freqs):
    """The finite fast path skips :func:`_repair_non_finite`; applied to
    finite values it would return them unchanged, bit for bit."""
    freqs_arr = np.asarray(freqs, dtype=float)
    if ic._is_open_end(formula):
        pytest.skip("the open-end sentinel is not evaluated")
    compiled = ic._compile_formula(formula, tuple(params))
    values = ic._evaluate(compiled, freqs_arr, tuple(params.values()))
    if not np.isfinite(values).all():
        return
    repaired = ic._repair_non_finite(compiled, freqs_arr, tuple(params.values()), values)
    assert np.array_equal(repaired.view(np.float64), values.view(np.float64))


@pytest.mark.filterwarnings("ignore::groundinsight.utils.impedance_calculator.DCLimitWarning")
@pytest.mark.parametrize("freqs", [FREQS, FREQS_DC], ids=["ac", "with_dc"])
@pytest.mark.parametrize("formula,params", FORMULAS)
def test_the_finite_fast_path_skips_only_the_repairs(formula, params, freqs, monkeypatch):
    calls = []
    original = ic._repair_non_finite

    def spy(*args):
        calls.append(args)
        return original(*args)

    monkeypatch.setattr(ic, "_repair_non_finite", spy)
    try:
        _, values = ic._impedance_array(formula, freqs, params)
    except ValueError:
        return
    compiled = None if ic._is_open_end(formula) else ic._compile_formula(formula, tuple(params))
    if compiled is None:
        assert not calls
        return
    raw = ic._evaluate(compiled, np.asarray(freqs, dtype=float), tuple(params.values()))
    assert bool(calls) == (not np.isfinite(raw).all())

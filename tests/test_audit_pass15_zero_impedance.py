# tests/test_audit_pass15_zero_impedance.py

"""
Regression tests for the fifteenth audit-pass bug-fix batch (2026-07-29).

This pass has a single subject: an impedance that cannot become an
admittance. Every impedance checked here is destined for the diagonal or an
off-diagonal entry of ``Y`` as its reciprocal ``1/Z``, and three kinds of
value have no reciprocal the nodal solve can use. All three were swallowed
without a word.

Z1  ``Z == 0``. :func:`~groundinsight.electrical_network._is_open` -- now
    :func:`~groundinsight.electrical_network._is_open_circuit` -- treated a
    zero impedance as an *open circuit*, i.e. as a bus with no earth
    electrode at all. Zero is the exact opposite: the ideal electrode.
    The proof is a limit test. In the two-bus reference network below the
    sequence ``Z_B -> 0`` converges to ``EPR(A) = 1000/11 V`` and
    ``EPR(B) -> 0``; the answer the model returned *at* ``Z_B = 0`` was
    ``EPR(A) = 0``, ``EPR(B) = 100 V`` -- byte-identical to ``Z_B = inf``,
    and the mirror image of the limit. A perfect earth electrode and a
    missing one were the same object.

Z2  ``0 < |Z| < 1/DBL_MAX`` (about ``5.5626846e-309``). One representable
    step away from Z1 and not caught by an ``== 0`` test: the value is
    finite and positive, but ``1/Z`` overflows to infinity, the infinite
    admittance reaches ``Y``, and the bus current comes back as NaN. The
    guard asks ``np.isfinite(1/Z)`` rather than comparing against a
    hand-carried constant, so the boundary moves with the arithmetic
    instead of with a magic number.

Z3  ``Re(Z) < 0``. An earth electrode, an earthing conductor and a cable
    screen are passive; a negative resistance generates energy. It is not
    an exotic input -- it is what a fitted formula returns outside the
    range it was fitted on, and ``0.05*rho - 2`` goes negative below
    40 ohm-m, which is ordinary wet soil. The result is not a visible
    failure but a plausible-looking number: the nodal determinant walks
    towards zero and the reported earth potential rise grows without
    bound (see the determinant test below -- the reference network is
    singular at ``Z_B = -11`` and reports 1.1 MV one thousandth of an ohm
    before it).

Z4  Impedances are **not** recomputed at solve time. Rejecting the three
    cases where a formula is evaluated is therefore only half a fix: a
    value assigned directly to ``bus.impedance[freq]``, or restored from a
    database or a JSON file written by an older version, reaches the
    solver untouched. Hence the second pass,
    :meth:`~groundinsight.electrical_network.ElectricalNetwork._validate_passive_impedances`,
    at the top of ``_construct_Y_matrices``.

The scope of the rule is deliberately narrow and is pinned here as
carefully as the rule itself: only impedances that are actually inverted
are checked. Mutual impedances are not (zero coupling is the ordinary
case), the self impedance of a branch that is not a grounding conductor is
not (it never reaches a division), inactive elements are not, frequencies
outside ``network.frequencies`` are not, and at 0 Hz a short circuit is
accepted rather than rejected -- an ideal inductance really is a short circuit
at DC, so there the solvers substitute a small finite impedance and warn
(pass 16). Everything else about the rule, including the negative real part,
holds at 0 Hz as well.

``inf`` and ``NaN`` are both left alone: ``inf`` is the documented open-end
sentinel (``impedance_formula="nan"``) and ``1/inf == 0`` is the correct
contribution for a tower without an electrode, while ``NaN`` is a failed
computation that :func:`~groundinsight.utils.impedance_calculator.compute_impedance`
already reports with a better message than this guard could produce.
"""

from __future__ import annotations

import json
import math
import warnings

import numpy as np
import pytest

import groundinsight as gi
from groundinsight.electrical_network import _is_open_circuit
from groundinsight.models.core_models import ComplexNumber
from groundinsight.simulation import waveforms
from groundinsight.simulation.transient import TransientStudy
from groundinsight.utils.impedance_calculator import (
    DCLimitWarning,
    _render_frequencies,
    check_passive_impedance,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

#: Reference network constants. ``Z_A`` and ``Z_br`` are held fixed so that
#: every limit in this module has a closed form: with 100 A injected at A and
#: drawn at B, ``Z_B -> 0`` gives ``EPR(A) = 100 / (1/Z_A + 1/Z_br) / Z_br``
#: = ``100 / 1.1`` V.
Z_A = 10.0
Z_BRANCH = 1.0
I_INJECT = 100.0
IDEAL_EPR_A = I_INJECT / (1.0 / Z_A * Z_BRANCH + 1.0)  # 1000/11 V


def _two_bus(name, z_b_formula="rho*0 + 1.0", grounding_conductor=True,
             self_formula="(rho*0 + 1.0) * l",
             mutual_formula="(rho*0 + 0.0) * l"):
    """Two buses A-B, 100 A injected at A, fault at B.

    ``Z_A`` is fixed at 10 ohm and the branch at 1 ohm; only ``Z_B`` varies.
    That makes the network the smallest one in which a bus impedance going to
    zero has a *non-zero* limit to converge to, which is what a limit test
    needs -- in a network where every bus impedance shrinks together, every
    voltage goes to zero and the interesting behaviour is invisible.
    """
    bus_type_a = gi.BusType(
        name=f"a_{name}", description="", system_type="Substation",
        voltage_level=110.0, impedance_formula=f"rho*0 + {Z_A!r}",
    )
    bus_type_b = gi.BusType(
        name=f"b_{name}", description="", system_type="Substation",
        voltage_level=110.0, impedance_formula=z_b_formula,
    )
    branch_type = gi.BranchType(
        name=f"c_{name}", grounding_conductor=grounding_conductor,
        self_impedance_formula=self_formula,
        mutual_impedance_formula=mutual_formula,
    )
    net = gi.create_network(name=name, frequencies=[50.0], description="")
    gi.create_bus(name="A", type=bus_type_a, specific_earth_resistance=100.0,
                  network=net)
    gi.create_bus(name="B", type=bus_type_b, specific_earth_resistance=100.0,
                  network=net)
    gi.create_branch(name="A-B", type=branch_type, from_bus="A", to_bus="B",
                     length=1.0, network=net)
    gi.create_source(name="s", bus="A", values={50.0: I_INJECT + 0.0j},
                     r_to_x=0.1, network=net)
    gi.create_fault(name="F", bus="B", scalings={50.0: 1.0}, t_k_s=0.5,
                    n_factor=1.0, network=net)
    return net


def _epr(net):
    """Earth potential rise per bus at 50 Hz, as floats."""
    return {
        row["bus_name"]: float(row["EPR_V"])
        for row in net.res_buses().to_dicts()
        if row["frequency_Hz"] == "50"
    }


def _solved_epr(name, z_b_formula):
    net = _two_bus(name, z_b_formula=z_b_formula)
    gi.run_fault(net, "F")
    return _epr(net)


def _rho_chain(name, bus_formula, rho):
    """Two buses whose grounding impedance comes from a rho-dependent fit."""
    bus_type = gi.BusType(
        name=f"t_{name}", description="", system_type="Tower",
        voltage_level=110.0, impedance_formula=bus_formula,
    )
    branch_type = gi.BranchType(
        name=f"c_{name}", grounding_conductor=True,
        self_impedance_formula="(0.30 + j*f*0.0025) * l",
        mutual_impedance_formula="(0.05 + j*f*0.0020) * l",
    )
    net = gi.create_network(name=name, frequencies=[50.0], description="")
    for bus_name in ("A", "B"):
        gi.create_bus(name=bus_name, type=bus_type,
                      specific_earth_resistance=rho, network=net)
    gi.create_branch(name="A-B", type=branch_type, from_bus="A", to_bus="B",
                     length=0.3, network=net)
    gi.create_source(name="s", bus="A", values={50.0: 5000.0}, r_to_x=0.1,
                     network=net)
    gi.create_fault(name="F", bus="B", scalings={50.0: 1.0}, t_k_s=0.5,
                    n_factor=1.0, network=net)
    return net


def _transient_net(name, bus_formula="rho*0 + 10.0 + I*f*0",
                   self_formula="(rho*0 + 1.0 + I*f*0) * l"):
    bus_type = gi.BusType(
        name=f"t_{name}", description="", system_type="Substation",
        voltage_level=20.0, impedance_formula=bus_formula,
    )
    branch_type = gi.BranchType(
        name=f"c_{name}", grounding_conductor=True,
        self_impedance_formula=self_formula,
        mutual_impedance_formula="(rho*0 + 0.0 + I*f*0) * l",
    )
    net = gi.create_network(name=name, frequencies=[50.0])
    gi.create_bus(name="bus1", type=bus_type, network=net)
    gi.create_bus(name="bus2", type=bus_type, network=net)
    gi.create_branch(name="br", type=branch_type, from_bus="bus1",
                     to_bus="bus2", length=1.0, network=net)
    gi.create_source(name="src", bus="bus1", values={50.0: 100.0 + 0.0j},
                     network=net)
    gi.create_fault(name="F1", bus="bus2", scalings={50.0: 1.0}, network=net)
    return net


def _solve_transient(net):
    """Run the FFT solver on a 20 ms window; the bin spacing is 50 Hz."""
    study = TransientStudy(net, fault_name="F1")
    study.set_source_waveform("src", waveforms.step(amplitude=100.0, t_on=0.0))
    return study.solve(t_end=0.02, dt=1e-3, solver="fft")


@pytest.fixture
def db_path(tmp_path):
    """A private SQLite file with an active session for one test."""
    gi.close_dbsession()
    path = str(tmp_path / "audit15.db")
    gi.start_dbsession(path)
    try:
        yield path
    finally:
        gi.close_dbsession()


# ---------------------------------------------------------------------------
# Z1 -- zero is the ideal electrode, not a missing one
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "label, value, expected",
    [
        ("open end (the sentinel)", complex(math.inf, math.inf), True),
        ("real part infinite only", complex(math.inf, 0.0), True),
        ("perfect electrode", complex(0.0, 0.0), False),
        ("failed computation", complex(math.nan, math.nan), False),
        ("ordinary electrode", complex(10.0, 2.0), False),
    ],
)
def test_only_infinity_means_no_connection(label, value, expected):
    """``_is_open_circuit`` must separate three states that used to be two.

    Up to v0.4.0 the predicate answered ``True`` for zero as well, which is
    what made a perfect electrode and a missing one the same object. NaN was
    never open either -- it is a failed computation and has to stay visible.
    """
    assert _is_open_circuit(value) is expected, label


def test_zero_grounding_impedance_is_rejected_where_it_is_computed():
    """A formula that evaluates to zero must fail at the bus, not at the
    solver, so that the message can still name the formula."""
    with pytest.raises(ValueError) as excinfo:
        _two_bus("p15_zero_bus", z_b_formula="rho * 0")
    message = str(excinfo.value)
    assert "bus 'B'" in message
    assert "exactly zero" in message
    assert "50 Hz" in message
    assert "rho * 0" in message       # the formula that produced it
    assert "rho=100" in message       # and the substitution it was given
    assert "1e-6" in message          # the actionable replacement


def _closed_form(z_b):
    """Solve the reference network by hand at a given ``Z_B``.

    ``Y u = i`` with ``i = (I, -I)``. Returned as magnitudes, so it can be
    compared against the ``EPR_V`` column directly.
    """
    y = np.array(
        [[1.0 / Z_A + 1.0 / Z_BRANCH, -1.0 / Z_BRANCH],
         [-1.0 / Z_BRANCH, 1.0 / z_b + 1.0 / Z_BRANCH]],
        dtype=complex,
    )
    voltages = np.linalg.solve(y, np.array([I_INJECT, -I_INJECT],
                                           dtype=complex))
    return abs(voltages[0]), abs(voltages[1])


def test_the_sequence_towards_zero_converges_to_the_ideal_electrode():
    """The limit test that proves Z1 is a bug.

    ``EPR(A)`` must approach ``1000/11 V`` and ``EPR(B)`` must approach zero
    as ``Z_B`` shrinks -- the textbook behaviour of an electrode that becomes
    perfect. Every point on the way is additionally checked against the
    closed-form solution of the same 2x2 system, so the test states more than
    "it converges somewhere": it converges to the right place, all the way
    from a tenth of an ohm down to a picoohm, with no floor and no
    discontinuity.
    """
    errors = {}
    for exponent in (1, 3, 6, 9, 12):
        z_b = 10.0 ** -exponent
        epr = _solved_epr(f"p15_conv_{exponent}", f"rho*0 + {z_b!r}")
        expected_a, expected_b = _closed_form(z_b)
        assert epr["A"] == pytest.approx(expected_a, rel=1e-9)
        assert epr["B"] == pytest.approx(expected_b, rel=1e-9)
        errors[exponent] = abs(epr["A"] - IDEAL_EPR_A) / IDEAL_EPR_A

    assert errors[1] > errors[3] > errors[6] > errors[9]
    # First order in Z_B: three decades of Z buy three decades of accuracy.
    assert errors[3] / errors[1] == pytest.approx(1e-2, rel=0.1)
    assert errors[6] / errors[3] == pytest.approx(1e-3, rel=0.1)


def test_the_answer_at_zero_used_to_be_the_answer_at_infinity():
    """Why the endpoint had to be rejected rather than kept.

    With no electrode at B the whole injected current has to return through
    B itself: ``EPR(B) = 100 V`` and ``EPR(A) = 0``. That is the answer the
    model produced for ``Z_B = 0`` as well, because both went through the
    same "open circuit" branch -- while the limit of the sequence is the
    mirror image, ``EPR(A) = 1000/11 V`` and ``EPR(B) -> 0``. The two cases
    are as far apart as the network allows, so silently identifying them was
    never a rounding question.
    """
    open_end = _solved_epr("p15_open_end", "nan")
    assert open_end["A"] == pytest.approx(0.0, abs=1e-9)
    assert open_end["B"] == pytest.approx(I_INJECT, rel=1e-9)

    near_ideal = _solved_epr("p15_near_ideal", "rho*0 + 1e-12")
    assert near_ideal["A"] == pytest.approx(IDEAL_EPR_A, rel=1e-9)
    assert near_ideal["B"] == pytest.approx(0.0, abs=1e-9)

    # And the endpoint itself no longer answers at all.
    with pytest.raises(ValueError, match="exactly zero"):
        _two_bus("p15_endpoint", z_b_formula="rho*0 + 0.0")


def test_the_remedy_quoted_in_the_message_is_accurate():
    """The message promises "about seven digits" for 1e-6 ohm in a network
    whose impedances are of the order of 1 ohm. A shipped error message must
    not make a numerical claim nobody measured."""
    epr = _solved_epr("p15_remedy", "rho*0 + 1e-6")
    relative_error = abs(epr["A"] - IDEAL_EPR_A) / IDEAL_EPR_A
    assert 1e-8 < relative_error < 1e-6           # measured: 9.09e-8
    assert round(-math.log10(relative_error)) == 7


@pytest.mark.parametrize(
    "exponent, digits",
    [(3, 4), (6, 7), (9, 10), (12, 13)],
)
def test_every_further_decade_buys_another_digit(exponent, digits):
    """The second half of the same promise, in the docstring and in
    ``docs/concepts.md``: the convergence is first order, so the accuracy is
    a straight line in the exponent with no floor before double precision
    runs out. Measured relative errors: 9.09e-5, 9.09e-8, 9.09e-11,
    9.10e-14."""
    epr = _solved_epr(f"p15_decade_{exponent}", f"rho*0 + 1e-{exponent}")
    relative_error = abs(epr["A"] - IDEAL_EPR_A) / IDEAL_EPR_A
    assert round(-math.log10(relative_error)) == digits


# ---------------------------------------------------------------------------
# Z2 -- the boundary is where the arithmetic stops, not a magic number
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "magnitude, invertible",
    [
        (1e-300, True),
        (1e-308, True),
        (5.6e-309, True),      # just above 1/DBL_MAX
        (5.5e-309, False),     # just below -- 1/Z overflows here
        (1e-310, False),       # subnormal
        (5e-324, False),       # the smallest subnormal there is
    ],
)
def test_the_threshold_is_exactly_the_inversion_limit(magnitude, invertible):
    """``1/DBL_MAX`` is about ``5.5626846e-309``. The guard must sit exactly
    there, because that is the point at which the reciprocal stops being a
    number -- not at a round figure chosen for looks."""
    z_dict = {50.0: ComplexNumber(real=magnitude, imag=0.0)}
    if invertible:
        check_passive_impedance(z_dict, element="bus 'X'")
        assert np.isfinite(1.0 / magnitude)
    else:
        with pytest.raises(ValueError, match="too small to invert"):
            check_passive_impedance(z_dict, element="bus 'X'")


def test_a_subnormal_grounding_impedance_is_rejected_end_to_end():
    """Before the fix this produced ``inf`` on the diagonal of ``Y`` and NaN
    in the result columns, with no message anywhere."""
    with pytest.raises(ValueError) as excinfo:
        _two_bus("p15_subnormal", z_b_formula="rho*0 + 1e-320")
    message = str(excinfo.value)
    assert "too small to invert" in message
    assert "5.6e-309" in message      # the limit, quoted
    assert "1e-6" in message          # the way out


def test_probing_the_overflow_emits_no_runtime_warning():
    """The guard detects an overflow by provoking it. Doing that without
    suppression would make the library warn on the very path whose purpose is
    to replace the warning with an explanation, and would raise instead of
    explaining for anyone running under ``-W error::RuntimeWarning`` or
    ``np.errstate(over="raise")``. The filter below is that setting, applied
    to the guard alone."""
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        for magnitude in (5e-324, 1e-320, 1e-310, 1e-300):
            try:
                check_passive_impedance(
                    {50.0: ComplexNumber(real=magnitude, imag=0.0)},
                    element="bus 'X'",
                )
            except ValueError:
                pass


# ---------------------------------------------------------------------------
# Z3 -- a passive element cannot have a negative resistance
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "rho, outcome",
    [
        (60.0, "solves"),      # Z = 1.00 ohm
        (45.0, "solves"),      # Z = 0.25 ohm
        (41.0, "solves"),      # Z = 0.05 ohm
        (40.0, "zero"),        # Z = 0.00 ohm  -- the crossing
        (39.0, "negative"),    # Z = -0.05 ohm
        (20.0, "negative"),    # Z = -1.00 ohm
    ],
)
def test_a_fit_evaluated_below_its_range_is_rejected_not_solved(rho, outcome):
    """``0.05*rho - 2`` is a plausible fit for a rod electrode and it crosses
    zero at 40 ohm-m -- wet clay, not an exotic soil. Every value below that
    used to be solved and reported as an ordinary result."""
    name = f"p15_rho_{rho:g}".replace(".", "_")
    if outcome == "solves":
        net = _rho_chain(name, "0.05*rho - 2", rho)
        gi.run_fault(net, "F")
        assert all(math.isfinite(value) for value in _epr(net).values())
        return

    with pytest.raises(ValueError) as excinfo:
        _rho_chain(name, "0.05*rho - 2", rho)
    message = str(excinfo.value)
    assert ("exactly zero" if outcome == "zero" else "negative") in message
    assert "0.05*rho - 2" in message
    assert f"rho={rho:g}" in message
    # The old symptom must not be what the user sees any more.
    assert "Singular" not in message


def test_a_negative_impedance_drives_the_nodal_system_to_singularity():
    """The physics the rejection rests on, computed on the bare matrix.

    The guard makes this state unreachable through the public API, so the
    justification is demonstrated on the same 2x2 admittance matrix the
    model builds. ``det(Y) = (1/Z_A + 1/Z_br)(1/Z_B + 1/Z_br) - 1/Z_br^2``
    is ``1.1/Z_B + 0.1`` here and vanishes at ``Z_B = -11``: an impedance
    that is merely "somewhat negative" does not fail, it reports a number,
    and the number grows without bound as the fit wanders further out.
    """
    def solve(z_b):
        y = np.array(
            [[1.0 / Z_A + 1.0 / Z_BRANCH, -1.0 / Z_BRANCH],
             [-1.0 / Z_BRANCH, 1.0 / z_b + 1.0 / Z_BRANCH]],
            dtype=complex,
        )
        current = np.array([I_INJECT, -I_INJECT], dtype=complex)
        return np.linalg.det(y), np.linalg.solve(y, current)

    magnitudes = []
    for z_b in (-10.0, -10.99, -10.999):
        determinant, voltages = solve(z_b)
        assert determinant == pytest.approx(1.1 / z_b + 0.1, rel=1e-9)
        magnitudes.append(abs(voltages[1]))

    assert magnitudes[0] == pytest.approx(1000.0, rel=1e-6)
    assert magnitudes[1] > 1e5
    assert magnitudes[2] > 1e6
    # Nothing in the sequence looks like a failure -- that is the whole point.
    assert all(math.isfinite(value) for value in magnitudes)
    assert solve(-11.0 + 1e-13)[0] == pytest.approx(0.0, abs=1e-13)


def test_a_negative_branch_self_impedance_is_rejected():
    """The same rule on the other inverted quantity."""
    with pytest.raises(ValueError) as excinfo:
        _two_bus("p15_neg_branch", self_formula="(rho*0 - 0.5) * l")
    message = str(excinfo.value)
    assert "branch 'A-B'" in message
    assert "self impedance" in message
    assert "negative" in message


def test_a_negative_source_impedance_is_rejected():
    """``Source`` already refuses a zero source impedance at construction;
    a negative one passed and reached the Norton conversion."""
    net = _two_bus("p15_neg_source")
    del net.sources["s"]
    gi.create_voltage_source(
        name="v", bus="A", voltage={50.0: 5000.0 + 0.0j},
        source_impedance={50.0: -0.5 + 0.1j}, network=net,
    )
    with pytest.raises(ValueError) as excinfo:
        gi.run_fault(net, "F")
    message = str(excinfo.value)
    assert "source 'v'" in message
    assert "negative" in message


# ---------------------------------------------------------------------------
# Z4 -- the solver-side pass, for values that never met a formula
# ---------------------------------------------------------------------------


def test_a_direct_write_to_bus_impedance_is_caught_at_solve_time():
    """Impedances are not recomputed by ``run_fault``. Validating only where
    a formula is evaluated would leave this wide open, and assigning into
    ``bus.impedance`` is a documented way to model a measured electrode."""
    net = _two_bus("p15_direct_bus")
    net.buses["B"].impedance[50.0] = ComplexNumber(real=0.0, imag=0.0)
    with pytest.raises(ValueError) as excinfo:
        gi.run_fault(net, "F")
    assert "bus 'B'" in str(excinfo.value)
    assert "exactly zero" in str(excinfo.value)


def test_a_direct_write_to_branch_self_impedance_is_caught_at_solve_time():
    net = _two_bus("p15_direct_branch")
    net.branches["A-B"].self_impedance[50.0] = ComplexNumber(real=-1.0,
                                                             imag=0.0)
    with pytest.raises(ValueError) as excinfo:
        gi.run_fault(net, "F")
    assert "branch 'A-B'" in str(excinfo.value)
    assert "negative" in str(excinfo.value)


def test_a_source_impedance_written_after_construction_is_caught():
    net = _two_bus("p15_direct_source")
    del net.sources["s"]
    gi.create_voltage_source(
        name="v", bus="A", voltage={50.0: 5000.0 + 0.0j},
        source_impedance={50.0: 0.5 + 0.1j}, network=net,
    )
    net.sources["v"].source_impedance[50.0] = ComplexNumber(real=0.0,
                                                            imag=0.0)
    with pytest.raises(ValueError) as excinfo:
        gi.run_fault(net, "F")
    assert "source 'v'" in str(excinfo.value)
    assert "exactly zero" in str(excinfo.value)


def test_a_network_restored_from_json_is_validated(tmp_path):
    """A file written by an older version can carry any of the three values,
    and loading does not re-evaluate the formula."""
    net = _two_bus("p15_json")
    gi.run_fault(net, "F")
    path = tmp_path / "p15.json"
    gi.save_network_to_json(net, str(path))

    raw = json.loads(path.read_text())
    for key in raw["buses"]["B"]["impedance"]:
        raw["buses"]["B"]["impedance"][key] = {"real": 0.0, "imag": 0.0}
    path.write_text(json.dumps(raw))

    restored = gi.load_network_from_json(str(path))
    assert restored.buses["B"].impedance[50.0].real == 0.0
    with pytest.raises(ValueError) as excinfo:
        gi.run_fault(restored, "F")
    assert "bus 'B'" in str(excinfo.value)


def test_a_network_restored_from_the_database_is_validated(db_path):
    """Same for an existing production database -- the value is stored, not
    recomputed, so the guard has to sit on the read path too."""
    net = _two_bus("p15_db")
    gi.run_fault(net, "F")
    net.buses["B"].impedance[50.0] = ComplexNumber(real=0.0, imag=0.0)
    gi.save_network_to_db(net, overwrite=True)

    restored = gi.load_network_from_db("p15_db")
    assert restored.buses["B"].impedance[50.0].real == 0.0
    with pytest.raises(ValueError) as excinfo:
        gi.run_fault(restored, "F")
    assert "bus 'B'" in str(excinfo.value)
    assert "exactly zero" in str(excinfo.value)


# ---------------------------------------------------------------------------
# Scope -- what is deliberately *not* validated
# ---------------------------------------------------------------------------


def test_a_zero_mutual_impedance_is_normal_and_stays_allowed():
    """Mutual coupling is never inverted, its sign follows the chosen
    direction convention, and zero coupling is the ordinary case for an
    uncoupled branch. Applying the rule here would break every network in
    the repository."""
    net = _two_bus("p15_mutual", mutual_formula="(rho*0 + 0.0) * l")
    net.branches["A-B"].mutual_impedance[50.0] = ComplexNumber(real=0.0,
                                                               imag=0.0)
    gi.run_fault(net, "F")
    assert all(math.isfinite(value) for value in _epr(net).values())


def test_the_self_impedance_of_a_non_grounding_conductor_is_not_validated():
    """Every inversion of a self impedance is gated on
    ``branch.type.grounding_conductor``; without one the value never reaches
    a division and any value is harmless."""
    net = _two_bus("p15_non_gc", grounding_conductor=False,
                   self_formula="rho * 0")
    gi.run_fault(net, "F")
    assert all(math.isfinite(value) for value in _epr(net).values())


def test_inactive_elements_are_not_validated():
    """An inactive bus is not in ``bus_indices`` and contributes nothing to
    ``Y``; the branch that hangs off it is skipped with it. Refusing to solve
    because of a value that is not used would make deactivation useless as a
    what-if tool -- which is exactly what it is for (see the outage study).
    """
    net = _two_bus("p15_inactive")
    spur_type = gi.BusType(
        name="spur", description="", system_type="Tower",
        voltage_level=110.0, impedance_formula="rho*0 + 5.0",
    )
    gi.create_bus(name="C", type=spur_type, specific_earth_resistance=100.0,
                  network=net)
    gi.create_branch(name="B-C", type=net.branches["A-B"].type, from_bus="B",
                     to_bus="C", length=1.0, network=net)
    net.buses["C"].impedance[50.0] = ComplexNumber(real=0.0, imag=0.0)
    net.branches["B-C"].self_impedance[50.0] = ComplexNumber(real=-1.0,
                                                             imag=0.0)
    net.buses["C"].active = False
    net.branches["B-C"].active = False

    gi.run_fault(net, "F")
    epr = _epr(net)
    assert epr["A"] == pytest.approx(_closed_form(1.0)[0], rel=1e-9)


def test_frequencies_outside_the_network_list_are_not_validated():
    """A stored impedance dictionary may carry leftovers from an earlier
    frequency list. Those entries never reach a division, and a leftover
    must not make an otherwise sound network unusable."""
    net = _two_bus("p15_leftover")
    net.buses["B"].impedance[250.0] = ComplexNumber(real=0.0, imag=0.0)
    gi.run_fault(net, "F")
    assert all(math.isfinite(value) for value in _epr(net).values())


@pytest.mark.parametrize(
    "label, value",
    [
        ("open-end sentinel", ComplexNumber(real=math.inf, imag=math.inf)),
        ("failed computation", ComplexNumber(real=math.nan, imag=math.nan)),
    ],
)
def test_infinity_and_nan_are_left_to_their_own_handlers(label, value):
    """``inf`` is a value with a correct reciprocal (zero admittance) and
    ``NaN`` is reported by ``compute_impedance`` with a message about the
    formula. Neither belongs to this guard, and duplicating the NaN message
    here would only make the two compete."""
    check_passive_impedance({50.0: value}, element="bus 'X'")


# ---------------------------------------------------------------------------
# The transient FFT grid -- same rule, except at DC
# ---------------------------------------------------------------------------


def test_the_zero_hertz_bin_is_validated_like_every_other_bin():
    """Up to v0.4.0 the FFT grid dropped the 0 Hz bin before validating, which
    let a formula that is *negative* at DC through as well. Only the short
    circuit is legitimate there, and that one is now handled by substitution
    rather than by looking away, so the bin takes part in the check again.

    ``_dc_substitute_for`` answers ``None`` in both cases where there is
    nothing to substitute, and answering ``None`` is what keeps the warning
    silent for an ordinary network."""
    # No short circuit at DC -> nothing to substitute, no warning.
    assert (
        TransientStudy._dc_substitute_for(
            [0.0, 50.0],
            {"bus1": np.array([10.0 + 0.0j, 10.0 + 1.0j])},
            {},
        )
        is None
    )
    # No 0 Hz bin in the grid at all -> likewise nothing to do.
    assert (
        TransientStudy._dc_substitute_for(
            [50.0, 100.0],
            {"bus1": np.array([0.0 + 0.0j, 10.0 + 1.0j])},
            {},
        )
        is None
    )


def test_a_dc_short_circuit_is_sized_from_the_smallest_other_impedance():
    """The stand-in is ``sqrt(machine epsilon) * Z_min``. Tying it to the
    *smallest* impedance rather than to the median or the largest is what keeps
    the error near ``1e-5`` instead of several percent on a network whose
    impedances span decades; the measurement is recorded at
    ``impedance_calculator._DC_SUBSTITUTE_FACTOR``."""
    with pytest.warns(DCLimitWarning) as record:
        substitute = TransientStudy._dc_substitute_for(
            [0.0, 50.0],
            {
                "bus1": np.array([0.0 + 0.0j, 0.0 + 1.0j]),
                "bus2": np.array([12.5 + 0.0j, 12.5 + 1.0j]),
            },
            {"br": np.array([0.002 + 0.0j, 0.002 + 1.0j])},
        )
    expected = math.sqrt(np.finfo(float).eps) * 0.002
    assert substitute == pytest.approx(expected, rel=1e-12)
    assert "bus 'bus1'" in str(record[0].message)


def test_a_purely_reactive_impedance_may_be_zero_at_dc():
    """An ideal inductance is a short circuit at 0 Hz. The FFT grid always
    contains a 0 Hz bin, so applying the steady-state rule unchanged would
    reject the most ordinary transient model there is."""
    net = _transient_net("p15_tr_ind", bus_formula="rho*0 + I*2*pi*f*5e-3")
    assert net.buses["bus1"].impedance[50.0].imag > 0.0
    result = _solve_transient(net)
    assert result is not None


def test_the_transient_grid_rejects_a_bus_impedance_that_turns_negative():
    """``1 - f*0.005`` is positive at the network frequency and negative
    above 200 Hz. The steady-state build passes; the FFT grid must not.
    A formula is only ever validated over the frequencies it is used at."""
    net = _transient_net("p15_tr_bus", bus_formula="rho*0 + 1 - f*0.005")
    assert net.buses["bus1"].impedance[50.0].real == pytest.approx(0.75)
    with pytest.raises(ValueError) as excinfo:
        _solve_transient(net)
    message = str(excinfo.value)
    assert "transient FFT grid" in message
    assert "bus 'bus1'" in message
    assert "200 Hz" in message


def test_the_transient_grid_rejects_a_branch_impedance_that_turns_negative():
    net = _transient_net("p15_tr_branch",
                         self_formula="(rho*0 + 1 - f*0.005) * l")
    with pytest.raises(ValueError) as excinfo:
        _solve_transient(net)
    message = str(excinfo.value)
    assert "transient FFT grid" in message
    assert "branch 'br'" in message


# ---------------------------------------------------------------------------
# Message rendering
# ---------------------------------------------------------------------------


def test_the_frequency_list_is_truncated_instead_of_flooding_the_message():
    """A transient study evaluates the same formula at several thousand FFT
    bins. An unbounded list would bury the sentence that explains the
    problem."""
    rendered = _render_frequencies([float(i) for i in range(2048)])
    assert rendered.startswith("0 Hz, 1 Hz, 2 Hz, 3 Hz, 4 Hz")
    assert "2048 frequencies in total" in rendered
    assert "2047 Hz" not in rendered


def test_all_three_causes_are_reported_in_one_message():
    """A user who fixes one cause and re-runs only to meet the next has been
    told half the truth. The causes are collected, not raised on first
    sight."""
    with pytest.raises(ValueError) as excinfo:
        check_passive_impedance(
            {
                50.0: ComplexNumber(real=0.0, imag=0.0),
                150.0: ComplexNumber(real=1e-320, imag=0.0),
                250.0: ComplexNumber(real=-2.0, imag=1.0),
            },
            element="bus 'X' (grounding impedance)",
            formula_str="some_fit(rho, f)",
            params={"rho": 40.0},
        )
    message = str(excinfo.value)
    assert "exactly zero at 50 Hz" in message
    assert "too small to invert" in message and "150 Hz" in message
    assert "negative (-2 Ohm) at 250 Hz" in message
    assert "Formula: 'some_fit(rho, f)' with rho=40." in message

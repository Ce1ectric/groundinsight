# tests/test_audit_pass16_dc.py

"""
Regression tests for the sixteenth audit-pass batch (2026-07-30): direct
current as a first-class frequency.

0 Hz was an accepted entry in ``Network.frequencies`` and in
``fault.scalings`` from the beginning, and the *solver* is exact there -- the
nodal system ``Y(f) u(f) = i(f)`` has no frequency dependence of its own. What
broke at DC was everything around it, in three separate places, and each one
had to be told apart from a case that looks identical in floating point.

D1  **Evaluating the formula string at exactly zero.** Three situations render
    as NaN or as an exception and mean entirely different things:

    * a *removable singularity* -- Carson's earth-return term
      ``omega * ln(658*sqrt(rho/f)/GMR)`` is ``0 * inf`` at f = 0, but omega
      vanishes linearly while the logarithm diverges logarithmically, so the
      limit is 0 and the conductor tends to its DC resistance. Raising here
      makes every Carson-type conductor unusable at DC *and in every
      transient study*, because an FFT grid always contains a 0 Hz bin.
    * a *true pole* -- ``1/(j*omega*C)`` is infinite, and infinity is the
      physically correct answer: a capacitor is an open circuit at DC.
    * a *genuine failure* -- ``sqrt(rho)`` with negative rho, a NaN
      parameter. NaN is the truth there and must keep raising.

    They are separated by approaching zero on a decade sequence and watching
    the differences shrink or grow (``_resolve_dc``).

D2  **A finite reactance at 0 Hz has no physical reading.** At DC a reactance
    either vanishes (``j*omega*L -> 0``) or is infinite
    (``1/(j*omega*C) -> inf``). A formula such as ``(0.25 + I*0.6)*l`` --
    ordinary, and the most common spelling in the wild -- reports 0.6 ohm of
    reactance at every frequency including zero, which is a statement about
    nothing. The 0 Hz bin falls back to the real part and says so. This is
    what Christian was working around by entering 0.1 Hz instead of 0.

D3  **A short circuit at 0 Hz is physics, not a mistake.** A purely inductive
    element is an *exact* zero at DC, and zero has no reciprocal the nodal
    solve can use. Pass 15 rejected zero everywhere, which would have made
    the most ordinary transient model there is unusable. It is now accepted at
    0 Hz alone and replaced by ``sqrt(machine epsilon) * Z_min``, which
    reproduces the ideal bond to about five significant digits (measured;
    see ``_DC_SUBSTITUTE_FACTOR``). Up to v0.4.0 such an element was *skipped*
    instead -- modelled as an open circuit, the exact opposite of a short.
    On the reference network of this module that was wrong by a factor of 46,
    with the sign of one bus reversed.

D4  **Every inversion site, not just the matrix build.** ``1/complex(0, 0)``
    raises ``ZeroDivisionError`` for Python complex numbers rather than
    returning infinity, so each of the seven places that inverts an impedance
    in ``electrical_network.py`` was its own crash path at DC -- the source
    injection, the phase-current split, the mutual-current transfer, the bus
    currents and the branch currents, none of which is reached by a test that
    only checks the EPR.

The negative real part of pass 15 keeps being rejected at 0 Hz as well: a
passive element is passive at DC too, and that check is the one thing about
zero frequency that is *not* special.
"""

from __future__ import annotations

import math
import warnings

import numpy as np
import pytest

import groundinsight as gi
from groundinsight.models.core_models import ComplexNumber
from groundinsight.simulation import waveforms
from groundinsight.simulation.transient import TransientStudy
from groundinsight.utils.impedance_calculator import (
    DCLimitWarning,
    _DC_SUBSTITUTE_FACTOR,
    check_passive_impedance,
    compute_impedance,
    compute_real_value,
    dc_substitute_impedance,
    is_short_circuit,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

#: A Carson-style earth-return self impedance. ``2*pi*f`` times a logarithm
#: containing ``sqrt(rho/f)``: ``0 * inf`` at f = 0, finite limit 0.25*l.
CARSON_SELF = (
    "(0.25 + I * 2 * pi * f * 2e-7 * log(658 * sqrt(rho / f) / 0.01)) * l"
)
#: The same shape for the mutual term, without the DC resistance.
CARSON_MUTUAL = (
    "(0.05 + I * 2 * pi * f * 2e-7 * log(658 * sqrt(rho / f) / 0.4)) * l"
)


def _dc_network(
    name,
    *,
    frequencies=(0.0,),
    bus_formula="rho*0 + 10.0 + I*f*0",
    self_formula=CARSON_SELF,
    mutual_formula=CARSON_MUTUAL,
    source_values=None,
    rho=100.0,
):
    """Two buses A-B with a DC entry in ``frequencies``.

    Kept minimal on purpose: the point of every test below is the frequency,
    not the topology, and a two-bus network still has a closed form.
    """
    freqs = list(frequencies)
    values = source_values or {f: 1000.0 + 0.0j for f in freqs}
    bus_type = gi.BusType(
        name=f"bt_{name}", description="", system_type="Substation",
        voltage_level=110.0, impedance_formula=bus_formula,
    )
    branch_type = gi.BranchType(
        name=f"br_{name}", grounding_conductor=True,
        self_impedance_formula=self_formula,
        mutual_impedance_formula=mutual_formula,
    )
    net = gi.create_network(name=name, frequencies=freqs, description="")
    for label in ("A", "B"):
        gi.create_bus(name=label, type=bus_type,
                      specific_earth_resistance=rho, network=net)
    gi.create_branch(name="A-B", type=branch_type, from_bus="A", to_bus="B",
                     length=1.0, network=net)
    gi.create_source(name="s", bus="A", values=values, r_to_x=0.1, network=net)
    gi.create_fault(name="F", bus="B", scalings={f: 1.0 for f in freqs},
                    network=net)
    return net


def _epr_at(net, freq):
    """Earth potential rise per bus at one frequency, as floats."""
    key = str(int(freq)) if float(freq).is_integer() else str(freq)
    return {
        row["bus_name"]: float(row["EPR_V"])
        for row in net.res_buses().to_dicts()
        if row["frequency_Hz"] == key
    }


# Reference network for the DC substitution: three buses in a chain, with the
# purely inductive bond between the first two. The two-bus case cannot show
# this at all -- an ideal bond between the source bus and the fault bus makes
# the DC injection sum to zero at the single remaining node, so every voltage
# is zero and the treatment of the bond is invisible.
Z_A, Z_B, Z_C = 0.8, 12.0, 3.5
R_BC = 0.35
L_BOND = 1.2e-3
I_DC = 1000.0


def _chain(name, bond_formula, frequencies=(50.0,)):
    freqs = list(frequencies)
    types = {
        label: gi.BusType(
            name=f"bt_{name}_{label}", description="",
            system_type="Substation", voltage_level=110.0,
            impedance_formula=f"rho*0 + {z!r} + I*f*0",
        )
        for label, z in (("A", Z_A), ("B", Z_B), ("C", Z_C))
    }
    bond = gi.BranchType(
        name=f"bond_{name}", grounding_conductor=True,
        self_impedance_formula=bond_formula,
        mutual_impedance_formula="(rho*0 + 0.0 + I*f*0) * l",
    )
    ordinary = gi.BranchType(
        name=f"ord_{name}", grounding_conductor=True,
        self_impedance_formula=f"(rho*0 + {R_BC!r} + I*2*pi*f*4e-4) * l",
        mutual_impedance_formula="(rho*0 + 0.0 + I*f*0) * l",
    )
    net = gi.create_network(name=name, frequencies=freqs, description="")
    for label in ("A", "B", "C"):
        gi.create_bus(name=label, type=types[label],
                      specific_earth_resistance=100.0, network=net)
    gi.create_branch(name="A-B", type=bond, from_bus="A", to_bus="B",
                     length=1.0, network=net)
    gi.create_branch(name="B-C", type=ordinary, from_bus="B", to_bus="C",
                     length=1.0, network=net)
    gi.create_source(name="s", bus="A",
                     values={f: I_DC + 0.0j for f in freqs},
                     r_to_x=0.1, network=net)
    gi.create_fault(name="F", bus="C", scalings={f: 1.0 for f in freqs},
                    network=net)
    return net


def _chain_dc_reference():
    """DC solution of the chain with the bond merged into a single node."""
    g = 1.0 / R_BC
    Y = np.array([[1.0 / Z_A + 1.0 / Z_B + g, -g], [-g, 1.0 / Z_C + g]])
    u = np.linalg.solve(Y, np.array([I_DC, -I_DC]))
    return {"A": u[0], "B": u[0], "C": u[1]}


def _chain_open_reference():
    """DC solution of the chain as v0.4.0 modelled it: the bond dropped."""
    g = 1.0 / R_BC
    Y = np.array([
        [1.0 / Z_A, 0.0, 0.0],
        [0.0, 1.0 / Z_B + g, -g],
        [0.0, -g, 1.0 / Z_C + g],
    ])
    u = np.linalg.solve(Y, np.array([I_DC, 0.0, -I_DC]))
    return {"A": u[0], "B": u[1], "C": u[2]}


def _transient_dc(net, buses=("A", "B", "C"), branches=("A-B", "B-C")):
    """Mean of u(t) and i(t) over a full FFT window, i.e. the 0 Hz bin."""
    study = TransientStudy(net, fault_name="F")
    study.set_source_waveform("s", waveforms.step(amplitude=I_DC, t_on=0.0))
    study.set_observation(buses=list(buses), branches=list(branches))
    res = study.solve(t_end=0.04, dt=1e-4, solver="fft")
    return (
        {b: float(np.mean(np.asarray(v))) for b, v in res.epr_t.items()},
        {b: float(np.mean(np.asarray(v))) for b, v in res.i_branch_t.items()},
    )


# ---------------------------------------------------------------------------
# D1 -- the three singularities are told apart
# ---------------------------------------------------------------------------


def test_carsons_removable_singularity_resolves_to_its_limit():
    """``0 * inf`` at f = 0 with a finite limit. The closed form of the limit
    is the DC resistance alone, because ``omega * log(...)`` vanishes: omega
    is linear in f, the logarithm only logarithmic."""
    z = compute_impedance(CARSON_SELF, [0.0], {"rho": 100.0, "l": 1.0})
    dc = complex(z[0.0].real, z[0.0].imag)
    assert dc.real == pytest.approx(0.25, rel=1e-9)
    assert dc.imag == pytest.approx(0.0, abs=1e-9)


def test_carsons_limit_agrees_with_the_approach_from_above():
    """An independent statement of the same thing: the limit the resolver
    reports must be what the formula itself converges to. This is the check
    that a wrong sign or a dropped factor in the resolver cannot pass."""
    params = {"rho": 100.0, "l": 1.0}
    z = compute_impedance(CARSON_SELF, [0.0, 1e-5, 1e-4, 1e-3], params)
    dc = complex(z[0.0].real, z[0.0].imag)
    approach = [complex(z[f].real, z[f].imag) for f in (1e-3, 1e-4, 1e-5)]
    # Monotone approach, and the last probe is already within 1e-6 of the
    # reported limit.
    gaps = [abs(value - dc) for value in approach]
    assert gaps[0] > gaps[1] > gaps[2]
    assert gaps[-1] < 1e-6


def test_a_true_pole_stays_infinite_at_dc():
    """A series capacitance. ``inf`` is the answer, not a defect."""
    z = compute_impedance("1/(I*2*pi*f*1e-9)", [0.0], {})
    dc = complex(z[0.0].real, z[0.0].imag)
    assert math.isinf(dc.real) or math.isinf(dc.imag)


def test_a_genuine_failure_still_raises_at_dc():
    """A NaN parameter is a modelling error at every frequency, and 0 Hz must
    not turn it into a value. This is the documented ``None`` return of the
    resolver: the approach sequence is NaN as well, so the singularity cannot
    be classified and the original NaN keeps its own error message."""
    with pytest.raises(ValueError, match="NaN"):
        compute_impedance(
            "rho + I*2*pi*f*1e-3", [0.0], {"rho": float("nan")}
        )


# ---------------------------------------------------------------------------
# D2 -- a finite reactance at DC falls back to the real part
# ---------------------------------------------------------------------------


def test_a_constant_reactance_falls_back_to_the_real_part_and_warns():
    """``(0.25 + I*0.6)*l`` is the most common spelling in the wild. At 0 Hz
    the 0.6 ohm is a statement about nothing, so it is dropped -- loudly."""
    with pytest.warns(DCLimitWarning, match="reactance at 0 Hz"):
        z = compute_impedance("(0.25 + I*0.6)*l", [0.0, 50.0], {"l": 2.0})
    dc = complex(z[0.0].real, z[0.0].imag)
    assert dc == complex(0.5, 0.0)
    # The other frequencies are a different question and stay untouched.
    ac = complex(z[50.0].real, z[50.0].imag)
    assert ac == complex(0.5, 1.2)


def test_a_vanishing_reactance_does_not_warn():
    """``j*2*pi*f*L`` is zero at DC by itself. There is nothing to report:
    the formula and the physics agree."""
    with warnings.catch_warnings():
        warnings.simplefilter("error", DCLimitWarning)
        z = compute_impedance("0.25 + I*2*pi*f*5e-3", [0.0, 50.0], {})
    assert complex(z[0.0].real, z[0.0].imag) == complex(0.25, 0.0)


def test_the_fallback_can_be_switched_off_for_rlc_parameters():
    """``compute_real_value`` reads R/L/C fields, which are real at every
    frequency by contract. Repairing a complex value there would disable the
    "produced a non-real value" check at 0 Hz and nowhere else, so that path
    passes ``dc_real_fallback=False`` and the mistake keeps being reported."""
    with pytest.raises(ValueError, match="non-real|complex"):
        compute_real_value("0.25 + I*0.6", [0.0], {}, name="R_self_formula")
    # The DC *limit* still applies on that path: a removable singularity
    # resolves rather than raising.
    values = compute_real_value(
        CARSON_SELF.replace("I * ", ""), [0.0], {"rho": 100.0, "l": 1.0},
        name="R_self_formula",
    )
    assert values[0.0] == pytest.approx(0.25, rel=1e-9)


def test_the_dc_warning_names_the_ratio_and_not_the_element():
    """One warning per *formula*, not per element: the message quotes X/R,
    which is length-invariant, so Python's default filter collapses a
    hundred-branch network into a single line instead of a hundred."""
    with pytest.warns(DCLimitWarning) as record:
        compute_impedance("(0.25 + I*0.6)*l", [0.0], {"l": 1.0})
        compute_impedance("(0.25 + I*0.6)*l", [0.0], {"l": 7.0})
    messages = {str(w.message) for w in record}
    assert len(messages) == 1
    assert "X/R = 2.4" in messages.pop()


# ---------------------------------------------------------------------------
# D3 -- a short circuit at DC is substituted, not dropped
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "label, value, expected",
    [
        ("exact zero", complex(0.0, 0.0), True),
        ("purely imaginary zero", complex(0.0, 0.0), True),
        ("subnormal, 1/Z overflows", complex(1e-320, 0.0), True),
        ("smallest invertible", complex(1e-300, 0.0), False),
        ("ordinary electrode", complex(10.0, 2.0), False),
        ("open end sentinel", complex(math.inf, math.inf), False),
        ("failed computation", complex(math.nan, math.nan), False),
    ],
)
def test_is_short_circuit_asks_the_arithmetic_not_a_magic_number(
    label, value, expected
):
    """The boundary is "``1/Z`` overflows", which moves with the arithmetic
    rather than with a hand-carried constant. ``inf`` and ``NaN`` are not
    short circuits: the first is an open circuit, the second a failure."""
    assert is_short_circuit(value) is expected


def test_the_substitute_is_sqrt_epsilon_times_the_smallest_impedance():
    with pytest.warns(DCLimitWarning):
        value = dc_substitute_impedance(
            [12.5, 0.002, 480.0], ["bus 'X'"], context="unit test"
        )
    assert value == pytest.approx(_DC_SUBSTITUTE_FACTOR * 0.002, rel=1e-12)


def test_the_substitute_falls_back_to_one_ohm_when_there_is_no_scale():
    """Every impedance in the network is zero, so there is nothing to measure
    against. The fallback only has to be invertible -- such a network has no
    earth reference at all and the existing singular-matrix error is the
    correct answer."""
    with pytest.warns(DCLimitWarning, match="no other scale"):
        value = dc_substitute_impedance(
            [], ["bus 'X'", "bus 'Y'"], context="unit test"
        )
    assert value == pytest.approx(_DC_SUBSTITUTE_FACTOR, rel=1e-12)
    assert 1.0 / value < np.inf


def test_the_substitute_warning_does_not_list_every_element():
    with pytest.warns(DCLimitWarning) as record:
        dc_substitute_impedance(
            [1.0], [f"bus '{i}'" for i in range(40)], context="unit test"
        )
    message = str(record[0].message)
    assert "40 elements in total" in message
    assert "bus '39'" not in message


def test_a_stationary_dc_bond_matches_the_merged_network():
    """The measurement that decided the rule, as a test: a purely inductive
    bond is an ideal short at DC, and the substitution must reproduce the
    network in which its two buses are one node."""
    net = _chain("p16_st_bond", f"(rho*0 + 0.0 + I*2*pi*f*{L_BOND!r}) * l",
                 frequencies=(0.0,))
    with pytest.warns(DCLimitWarning):
        gi.run_fault(net, "F")
    got = _epr_at(net, 0.0)
    ref = _chain_dc_reference()
    # ``EPR_V`` is a magnitude; the sign lives in ``EPR_degree``.
    for bus in ("A", "B", "C"):
        assert got[bus] == pytest.approx(abs(ref[bus]), rel=1e-4)
    phases = {
        row["bus_name"]: float(row["EPR_degree"])
        for row in net.res_buses().to_dicts()
        if row["frequency_Hz"] == "0"
    }
    assert phases["A"] == pytest.approx(0.0, abs=1e-6)
    assert abs(phases["C"]) == pytest.approx(180.0, abs=1e-6)


def test_the_transient_dc_bin_matches_the_merged_network():
    """The FFT grid always contains 0 Hz, so this is not an exotic case: it is
    every transient run of every network with a purely inductive element."""
    net = _chain("p16_tr_bond", f"(rho*0 + 0.0 + I*2*pi*f*{L_BOND!r}) * l")
    with pytest.warns(DCLimitWarning):
        epr, _ = _transient_dc(net)
    ref = _chain_dc_reference()
    for bus in ("A", "B", "C"):
        assert epr[bus] == pytest.approx(ref[bus], rel=1e-6)


def test_the_substitution_beats_the_open_circuit_of_v050_by_orders():
    """The regression this pass exists for. Skipping the element modelled the
    short as an open circuit: bus A was cut off from the network entirely, its
    own electrode instead of the parallel combination, and bus B came back
    with the wrong sign."""
    ref = _chain_dc_reference()
    old = _chain_open_reference()
    net = _chain("p16_tr_cmp", f"(rho*0 + 0.0 + I*2*pi*f*{L_BOND!r}) * l")
    with pytest.warns(DCLimitWarning):
        epr, _ = _transient_dc(net)
    for bus in ("A", "B", "C"):
        new_err = abs(epr[bus] - ref[bus]) / abs(ref[bus])
        old_err = abs(old[bus] - ref[bus]) / abs(ref[bus])
        assert new_err < 1e-5
        assert old_err > 9.0
    # The sign reversal, pinned explicitly.
    assert old["B"] < 0 < ref["B"]


def test_an_explicit_small_resistance_converges_to_the_same_answer():
    """The substitution is not a special rule but the limit of an ordinary
    model: replacing the inductive bond by a shrinking real resistance walks
    towards the same numbers."""
    ref = _chain_dc_reference()
    errors = []
    for r_bond in (1e-2, 1e-4, 1e-6):
        net = _chain(f"p16_r{r_bond:g}", f"(rho*0 + {r_bond!r} + I*f*0) * l")
        epr, _ = _transient_dc(net)
        errors.append(abs(epr["A"] - ref["A"]) / abs(ref["A"]))
    assert errors[0] > errors[1] > errors[2]
    assert errors[-1] < 1e-4


def test_a_capacitance_still_reads_as_an_open_circuit_at_dc():
    """The substitution must not touch a true pole. A capacitive bond carries
    no DC, so the DC bin is the network *without* that branch -- which is
    exactly what v0.4.0 wrongly did to the inductive one."""
    net = _chain("p16_tr_cap", "(rho*0 + 1/(I*2*pi*f*1e-9)) * l")
    with warnings.catch_warnings():
        warnings.simplefilter("error", DCLimitWarning)
        epr, i_branch = _transient_dc(net)
    open_ref = _chain_open_reference()
    assert epr["A"] == pytest.approx(open_ref["A"], rel=1e-9)
    assert i_branch["A-B"] == pytest.approx(0.0, abs=1e-9)


def test_an_ordinary_network_at_dc_warns_about_nothing():
    """No short circuit, no finite reactance at DC, no pole: the DC study
    must be as quiet as a 50 Hz one. This is the test that keeps the three
    new warnings from becoming background noise."""
    net = _dc_network("p16_quiet", self_formula="(rho*0 + 0.25 + I*2*pi*f*5e-4) * l",
                      mutual_formula="(rho*0 + 0.05 + I*2*pi*f*2e-4) * l")
    with warnings.catch_warnings():
        warnings.simplefilter("error", DCLimitWarning)
        gi.run_fault(net, "F")
    assert all(math.isfinite(v) for v in _epr_at(net, 0.0).values())


# ---------------------------------------------------------------------------
# D4 -- every inversion site survives DC, not only the matrix build
# ---------------------------------------------------------------------------


def test_a_pure_dc_study_runs_end_to_end_with_carson_conductors():
    """``1/complex(0, 0)`` raises ``ZeroDivisionError`` rather than returning
    infinity, so each inversion site in the solver was its own crash path.
    A Carson formula reaches all of them at once: bus diagonal, branch
    self admittance, source injection, mutual transfer, bus currents and
    branch currents."""
    net = _dc_network("p16_dc_end_to_end")
    gi.run_fault(net, "F")
    epr = _epr_at(net, 0.0)
    assert set(epr) == {"A", "B"}
    assert all(math.isfinite(v) for v in epr.values())
    assert epr["A"] > 0.0
    branches = net.res_branches().to_dicts()
    assert branches
    assert all(
        math.isfinite(float(row["I_branch_A"])) for row in branches
        if row["frequency_Hz"] == "0"
    )


def test_a_dc_study_with_a_voltage_source_survives_the_thevenin_loop():
    """The Thevenin loop closure inverts the source impedance, which is a
    separate site from the three in the matrix build and is only reached by a
    ``source_type='voltage'`` source."""
    net = _dc_network("p16_dc_voltage")
    src = net.sources["s"]
    src.source_type = "voltage"
    src.source_impedance = {0.0: ComplexNumber(real=0.5, imag=0.0)}
    src.values = {0.0: 1000.0 + 0.0j}
    gi.run_fault(net, "F")
    epr = _epr_at(net, 0.0)
    assert all(math.isfinite(v) for v in epr.values())


def test_dc_and_power_frequency_in_one_study_do_not_disturb_each_other():
    """The DC handling is confined to the 0 Hz bin. A study that carries both
    must return the same 50 Hz answer as a study that carries 50 Hz alone --
    bit for bit, because nothing about the 50 Hz system changed."""
    both = _dc_network("p16_mixed_both", frequencies=(0.0, 50.0))
    only_ac = _dc_network("p16_mixed_ac", frequencies=(50.0,))
    gi.run_fault(both, "F")
    gi.run_fault(only_ac, "F")
    assert _epr_at(both, 50.0) == _epr_at(only_ac, 50.0)


def test_the_dc_component_of_a_short_circuit_current_reaches_the_result():
    """What Christian was after: a DC current source is an ordinary source and
    its earth potential rise is an ordinary result, with no 0.1 Hz detour.
    The closed form of the two-bus network is exact at DC."""
    net = _dc_network(
        "p16_dc_component",
        bus_formula="rho*0 + 10.0 + I*f*0",
        self_formula="(rho*0 + 1.0 + I*2*pi*f*3e-4) * l",
        mutual_formula="(rho*0 + 0.0 + I*f*0) * l",
        source_values={0.0: 1000.0 + 0.0j},
    )
    gi.run_fault(net, "F")
    epr = _epr_at(net, 0.0)
    # Y = [[1/10 + 1, -1], [-1, 1/10 + 1]] with i = [1000, -1000].
    y = np.array([[1.1, -1.0], [-1.0, 1.1]])
    u = np.linalg.solve(y, np.array([1000.0, -1000.0]))
    assert epr["A"] == pytest.approx(abs(u[0]), rel=1e-9)
    assert epr["B"] == pytest.approx(abs(u[1]), rel=1e-9)


# ---------------------------------------------------------------------------
# The pass-15 rule, restated for 0 Hz
# ---------------------------------------------------------------------------


def test_zero_is_accepted_at_dc_and_rejected_above_it():
    """The one asymmetry the DC work introduces into the pass-15 guard, in a
    single test so the boundary cannot drift unnoticed."""
    zero = ComplexNumber(real=0.0, imag=0.0)
    # 0 Hz: legitimate, the solvers substitute.
    check_passive_impedance({0.0: zero}, element="bus 'X'")
    # 50 Hz: an earth electrode of exactly zero ohm is a modelling error.
    with pytest.raises(ValueError, match="zero"):
        check_passive_impedance({50.0: zero}, element="bus 'X'")


def test_a_non_invertible_impedance_follows_the_same_asymmetry():
    subnormal = ComplexNumber(real=1e-320, imag=0.0)
    check_passive_impedance({0.0: subnormal}, element="bus 'X'")
    with pytest.raises(ValueError):
        check_passive_impedance({50.0: subnormal}, element="bus 'X'")


def test_a_negative_real_part_is_rejected_at_dc_too():
    """A passive element is passive at DC as well. This is the check the
    transient path escaped entirely while it dropped the 0 Hz bin before
    validating."""
    negative = ComplexNumber(real=-1.0, imag=0.0)
    with pytest.raises(ValueError, match="negative"):
        check_passive_impedance({0.0: negative}, element="bus 'X'")
    with pytest.raises(ValueError, match="negative"):
        check_passive_impedance({50.0: negative}, element="bus 'X'")


def test_the_zero_message_says_that_dc_would_have_been_accepted():
    """A user who meets this at 50 Hz has usually written a formula whose DC
    limit they were thinking of. The message says so rather than leaving them
    to guess why the same value is fine in a transient run."""
    with pytest.raises(ValueError) as excinfo:
        check_passive_impedance(
            {50.0: ComplexNumber(real=0.0, imag=0.0)}, element="bus 'X'"
        )
    assert "0 Hz this would be accepted" in str(excinfo.value)

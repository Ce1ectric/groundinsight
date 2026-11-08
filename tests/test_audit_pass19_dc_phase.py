# tests/test_audit_pass19_dc_phase.py
"""A purely inductive phase conductor at 0 Hz (audit pass 19).

Finding: follow-up to the DC rules of audit pass 16 (D3, D4). A declared
``phase_impedance_formula`` without a resistive part, such as
``j*2*pi*f*L*l``, is exactly zero at 0 Hz. The 0 Hz scan that sizes the DC
stand-in covered the buses, the shields and the voltage-source impedances,
but not the phase conductors, so without another DC short in the network the
phase-network solve inverted the zero and ``run_fault`` raised a bare
``ZeroDivisionError``. Above 0 Hz an exactly zero phase impedance raised the
same, where a bus or shield impedance gets a ``ValueError`` naming it.

The phase conductors now follow the documented rule of the other elements: at
0 Hz a short is physics and is replaced by the stand-in
``sqrt(eps) * smallest other impedance`` with a ``DCLimitWarning``; above
0 Hz zero, too small to invert or a negative real part is refused by name.

Second way: the phase split of a radial network does not depend on the phase
impedances at all, a network whose phase conductors all carry the same
stand-in splits like one with equal declared impedances, and the current
divider between an ideal short and a resistive route is written out.
"""

from __future__ import annotations

import math
import warnings
from typing import Dict, Optional

import numpy as np
import pytest

import groundinsight as gi
from groundinsight import DCLimitWarning
from tests import _reference_mna as ref


FREQS = [0.0, 50.0]
INDUCTIVE_PHASE = "j*2*pi*f*0.0004*l"
SHIELD = "(0.29 + j*2*pi*f*0.002)*l"
MUTUAL = "(0.049 + j*2*pi*f*0.0017)*l"


def _network(name: str, branches: Dict[str, tuple], buses=("S", "A", "F"),
             frequencies=FREQS) -> gi.Network:
    """Stations at every bus; ``branches``: name -> (from, to, length, phase formula)."""
    net = gi.create_network(name=name, frequencies=list(frequencies))
    station = gi.BusType(name=f"{name}_bt", system_type="S", voltage_level=20.0,
                         impedance_formula="2.0")
    for bus in buses:
        gi.create_bus(name=bus, type=station, network=net)
    types: Dict[Optional[str], gi.BranchType] = {}
    for branch, (a, b, length, phase) in branches.items():
        if phase not in types:
            kwargs = dict(name=f"{name}_brt{len(types)}", grounding_conductor=True,
                          self_impedance_formula=SHIELD, mutual_impedance_formula=MUTUAL)
            if phase is not None:
                kwargs["phase_impedance_formula"] = phase
            types[phase] = gi.BranchType(**kwargs)
        gi.create_branch(name=branch, type=types[phase], from_bus=a, to_bus=b,
                         length=length, network=net)
    gi.create_source(name="Q", bus="S", values={f: (100.0 if f == 0.0 else 1000.0)
                                                for f in frequencies}, network=net)
    gi.create_fault(name="F1", bus="F", network=net)
    return net


def _run(net: gi.Network, **kwargs):
    with warnings.catch_warnings(record=True) as record:
        warnings.simplefilter("always")
        result = gi.run_fault(net, "F1", **kwargs)
    return result, [w.message for w in record]


def _epr(result, bus: str, f: float) -> complex:
    return next(complex(b.uepr_freq[f]) for b in result.buses if b.name == bus)


def test_a_purely_inductive_phase_conductor_at_0_hz_is_a_short_not_a_crash():
    """Radial S - A - F, phase conductors j*2*pi*f*L: run_fault used to raise
    ZeroDivisionError. Now the stand-in replaces the short and a DCLimitWarning
    names both phase conductors. Second way: on a radial network the phase
    current is the source current whatever the phase impedance, so the result
    equals that of the same network with a resistive phase formula, and the
    reference solver on that network."""
    chain = {"L1": ("S", "A", 1.0, INDUCTIVE_PHASE), "L2": ("A", "F", 1.5, INDUCTIVE_PHASE)}
    net = _network("dcp_radial", chain)
    result, messages = _run(net)
    dc = [m for m in messages if isinstance(m, DCLimitWarning)]
    assert len(dc) == 1
    assert "L1" in str(dc[0]) and "L2" in str(dc[0]) and "phase" in str(dc[0])
    phase = net.electrical_network.phase_currents[0.0]
    assert phase["L1"] == pytest.approx(100.0, rel=1e-12)
    assert phase["L2"] == pytest.approx(100.0, rel=1e-12)

    resistive = {k: (a, b, length, "0.13*l + " + INDUCTIVE_PHASE)
                 for k, (a, b, length, _) in chain.items()}
    twin = _network("dcp_radial_resistive", resistive)
    twin_result, twin_messages = _run(twin)
    assert not [m for m in twin_messages if isinstance(m, DCLimitWarning)]
    rd = ref.reference_network(twin, 0.0, "F1")
    mapped = ref.to_package_frame(ref.solve_oneway(rd, thevenin_phase="norton"))
    for bus in ("S", "A", "F"):
        for f in FREQS:
            assert _epr(result, bus, f) == pytest.approx(_epr(twin_result, bus, f), rel=1e-12)
        assert _epr(result, bus, 0.0) == pytest.approx(mapped["u"][bus], rel=1e-12)


def test_a_ring_of_ideal_phase_conductors_splits_by_element_at_0_hz():
    """A ring whose phase conductors are all ideal shorts at 0 Hz has no DC
    split of its own: every conductor gets the same stand-in, so the ring
    splits as if all of them had one and the same impedance -- whatever
    their lengths. Second way: the same ring with the constant phase
    impedance 1 Ohm, split by the package and by the reference solver."""
    ring = {"L1": ("S", "A", 1.0, INDUCTIVE_PHASE), "L2": ("A", "F", 1.0, INDUCTIVE_PHASE),
            "L3": ("F", "B", 0.5, INDUCTIVE_PHASE), "L4": ("B", "S", 2.5, INDUCTIVE_PHASE),
            "L5": ("S", "F", 4.0, INDUCTIVE_PHASE)}
    buses = ("S", "A", "F", "B")
    net = _network("dcp_ring", ring, buses=buses)
    _, messages = _run(net)
    assert [m for m in messages if isinstance(m, DCLimitWarning)]
    equal = {k: (a, b, length, "1.0") for k, (a, b, length, _) in ring.items()}
    twin = _network("dcp_ring_equal", equal, buses=buses)
    _run(twin)
    split = net.electrical_network.phase_currents[0.0]
    twin_split = twin.electrical_network.phase_currents[0.0]
    rd = ref.reference_network(twin, 0.0, "F1")
    alpha = ref.phase_split(rd, "declared")["Q"]
    for name in ring:
        assert split[name] == pytest.approx(twin_split[name], rel=1e-9, abs=1e-9)
        assert split[name] == pytest.approx(100.0 * alpha[name], rel=1e-9, abs=1e-9)
    # S -> F directly is one element against two on either side route
    assert abs(split["L5"]) == pytest.approx(50.0, rel=1e-9)


def test_an_ideal_route_beside_a_resistive_one_takes_the_current():
    """Route S - A - F has ideal phase conductors at 0 Hz, route S - F a
    resistive one. The ideal route takes all but a stand-in-sized share:
    with the current divider written out, i_resistive / I = 2 Z_sub /
    (2 Z_sub + R) with Z_sub = sqrt(eps) * the smallest other impedance."""
    ring = {"L1": ("S", "A", 1.0, INDUCTIVE_PHASE), "L2": ("A", "F", 1.0, INDUCTIVE_PHASE),
            "L3": ("S", "F", 2.0, "0.13*l + " + INDUCTIVE_PHASE)}
    net = _network("dcp_mixed", ring)
    _run(net)
    split = net.electrical_network.phase_currents[0.0]
    # the stand-in is scaled to the earthing network: electrodes 2.0 Ohm,
    # shields 0.29 Ohm/km at DC -- the smallest is the 1 km shield, 0.29 Ohm
    smallest = min(2.0, 0.29 * 1.0, 0.29 * 2.0)
    z_sub = math.sqrt(np.finfo(float).eps) * smallest
    expected = 100.0 * 2 * z_sub / (2 * z_sub + 0.13 * 2.0)
    assert split["L3"] == pytest.approx(expected, rel=1e-6)
    assert split["L1"] == pytest.approx(100.0 - expected, rel=1e-12)


def test_a_zero_phase_impedance_above_0_hz_is_refused_by_name():
    """At 50 Hz a phase impedance of exactly zero is a modelling error, as for
    a bus or a shield: a ValueError that names the branch, not a bare
    ZeroDivisionError."""
    net = _network("dcp_zero_50", {"L1": ("S", "A", 1.0, "0"), "L2": ("A", "F", 1.0, "0")},
                   frequencies=[50.0])
    with pytest.raises(ValueError, match="phase impedance") as excinfo:
        _run(net)
    assert "L1" in str(excinfo.value)


def test_a_negative_resistance_in_a_phase_conductor_is_refused_like_any_other():
    """The rule of the other elements in full: a negative real part is not
    passive, at 0 Hz and above."""
    net = _network("dcp_negative", {"L1": ("S", "A", 1.0, "-0.1*l + " + INDUCTIVE_PHASE),
                                    "L2": ("A", "F", 1.0, INDUCTIVE_PHASE)})
    with pytest.raises(ValueError, match="real part is negative") as excinfo:
        _run(net)
    assert "branch 'L1' (phase impedance)" in str(excinfo.value)


def test_the_paths_mode_does_not_invert_phase_impedances_and_says_nothing():
    """phase_current_mode="paths" takes the phase current from the paths and
    never inverts a phase impedance; its 0 Hz bin needs no stand-in."""
    chain = {"L1": ("S", "A", 1.0, INDUCTIVE_PHASE), "L2": ("A", "F", 1.5, INDUCTIVE_PHASE)}
    net = _network("dcp_paths", chain)
    _, messages = _run(net, phase_current_mode="paths")
    assert not [m for m in messages if isinstance(m, DCLimitWarning)]


def test_a_phase_impedance_of_minus_infinity_at_0_hz_is_refused():
    """The two 0 Hz rules of this pass meet here: phase conductors are part of
    the passivity check, and a real part of minus infinity is the limit of a
    negative resistance, not an open circuit. ``0.1 + log(f)`` per km tends to
    minus infinity at 0 Hz; the ring makes the phase-network solve invert it.
    Before both rules the conductor was silently opened in the 0 Hz bin."""
    ring = {"R1": ("S", "A", 1.0, "(0.1 + log(f))*l"), "R2": ("A", "F", 1.0, INDUCTIVE_PHASE),
            "R3": ("F", "B", 1.0, INDUCTIVE_PHASE), "R4": ("B", "S", 1.0, INDUCTIVE_PHASE)}
    net = _network("dcp_neginf", ring, buses=("S", "A", "F", "B"))
    with pytest.raises(ValueError, match="minus infinity") as excinfo:
        _run(net)
    assert "branch 'R1' (phase impedance)" in str(excinfo.value)

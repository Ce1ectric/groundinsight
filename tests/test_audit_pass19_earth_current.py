# tests/test_audit_pass19_earth_current.py
"""The earth-return split: linear time (P3) and robustness against rounding (P9).

Finding IDs: P3 -- ``split_earth_currents`` rebuilt ``set(chosen)`` for every
bus and scanned the half-plane window with a scalar ``while`` loop, so a
20 000-bus network spent 12 s per frequency in the split alone. The fix must
not move a single bit of the result, so the previous implementation is kept
here verbatim as the oracle and compared with ``==``.

P9 / F14 -- the group, ``I_E`` and the factors built on it were decided by
rounding noise in named configurations; see the second half of the module.
"""

from __future__ import annotations

import time
from typing import Dict, List, Optional, Tuple

import numpy as np
import pytest

from groundinsight.utils.earth_current import EarthCurrentSplit, split_earth_currents


# ---------------------------------------------------------------------------
# Oracle: the implementation before P3, copied verbatim (do not "improve" it)
# ---------------------------------------------------------------------------


def _old_split_earth_currents(
    electrode_currents: Dict[str, complex],
    *,
    reference_bus: Optional[str] = None,
    feeding_buses: Optional[List[str]] = None,
) -> Optional[EarthCurrentSplit]:
    names = [name for name, value in electrode_currents.items() if value != 0]
    if not names:
        return None

    values = np.array([complex(electrode_currents[n]) for n in names], dtype=complex)
    total_magnitude = float(np.abs(values).sum())

    if feeding_buses is not None:
        chosen = [n for n in names if n in set(feeding_buses)]
        i_earth = complex(
            sum((complex(electrode_currents[n]) for n in chosen), 0.0 + 0.0j)
        )
    else:
        chosen, i_earth = _old_best_half_plane(names, values)
        rest_names = [n for n in names if n not in set(chosen)]
        if _old_should_swap(chosen, rest_names, reference_bus):
            chosen, rest_names = rest_names, chosen
            i_earth = -i_earth

    rest = [n for n in names if n not in set(chosen)]
    separation = (
        float(abs(i_earth) / (0.5 * total_magnitude)) if total_magnitude > 0 else 0.0
    )
    return EarthCurrentSplit(
        i_earth=i_earth,
        feeding_buses=sorted(chosen),
        returning_buses=sorted(rest),
        separation=separation,
    )


def _old_should_swap(
    chosen: List[str], rest: List[str], reference_bus: Optional[str]
) -> bool:
    if not rest:
        return False
    if reference_bus is not None:
        if reference_bus in chosen:
            return False
        if reference_bus in rest:
            return True
    if len(rest) != len(chosen):
        return len(rest) < len(chosen)
    return sorted(rest) < sorted(chosen)


def _old_best_half_plane(
    names: List[str], values: np.ndarray
) -> Tuple[List[str], complex]:
    n = len(values)
    if n == 1:
        return list(names), complex(values[0])

    angles = np.angle(values)
    order = np.argsort(angles)
    sorted_angles = angles[order]
    sorted_values = values[order]

    doubled = np.concatenate([sorted_values, sorted_values])
    doubled_angles = np.concatenate([sorted_angles, sorted_angles + 2.0 * np.pi])
    prefix = np.concatenate([[0.0 + 0.0j], np.cumsum(doubled)])

    best_sum = 0.0 + 0.0j
    best_span = (0, 0)
    j = 0
    for i in range(n):
        if j < i:
            j = i
        limit = sorted_angles[i] + np.pi
        while j < i + n and doubled_angles[j] < limit:
            j += 1
        candidate = complex(prefix[j] - prefix[i])
        if abs(candidate) > abs(best_sum):
            best_sum = candidate
            best_span = (i, j)

    start, stop = best_span
    chosen = [names[order[k % n]] for k in range(start, stop)]
    return chosen, best_sum


def _same(a: Optional[EarthCurrentSplit], b: Optional[EarthCurrentSplit]) -> bool:
    """Bit-level equality of two splits (``==`` on every field)."""
    if a is None or b is None:
        return a is None and b is None
    return (
        a.i_earth == b.i_earth
        and a.feeding_buses == b.feeding_buses
        and a.returning_buses == b.returning_buses
        and a.separation == b.separation
    )


def _random_sets(count: int, seed: int):
    """Random phasor sets with zeros, duplicate angles, anchors and pins."""
    rng = np.random.default_rng(seed)
    for _ in range(count):
        n = int(rng.integers(1, 200))
        values = rng.normal(size=n) + 1j * rng.normal(size=n)
        values[rng.random(n) < 0.1] = 0
        if n > 3 and rng.random() < 0.3:
            values[1] = values[0]                      # duplicate angle
        if n > 4 and rng.random() < 0.2:
            values[2] = -values[0]                     # exactly opposed pair
        names = [f"B{k}" for k in range(n)]
        mode = rng.random()
        if mode < 0.6:
            reference = names[int(rng.integers(n))]    # may carry zero current
        elif mode < 0.7:
            reference = "not-a-bus"
        else:
            reference = None
        pinned = None
        if rng.random() < 0.15:
            pinned = [nm for nm in names if rng.random() < 0.4]
        yield dict(zip(names, values, strict=True)), reference, pinned


# ---------------------------------------------------------------------------
# P3 -- linear time, bit for bit the same result
# ---------------------------------------------------------------------------


def test_the_split_is_bit_identical_to_the_previous_implementation():
    """2000 random phasor sets: every field equal with ``==``."""
    mismatches = []
    for k, (currents, reference, pinned) in enumerate(_random_sets(2000, seed=20261108)):
        old = _old_split_earth_currents(currents, reference_bus=reference, feeding_buses=pinned)
        new = split_earth_currents(currents, reference_bus=reference, feeding_buses=pinned)
        if not _same(old, new):
            mismatches.append(k)
    assert mismatches == []


def test_the_documented_examples_keep_their_answers():
    """The hand-made cases of the module docstring, against the oracle."""
    cases = [
        {"A": 10 + 0j, "B": 5 + 0j, "C": -15 + 0j},
        {"A": 10 + 0j, "B": 10j, "C": -10 - 10j},
        {"A": 3 + 4j},
        {"A": 1 + 0j, "B": -1 + 0j},
        {"A": 0j, "B": 2 + 0j, "C": -2 + 0j},
    ]
    for currents in cases:
        for reference in (None, "A", "C"):
            assert _same(
                _old_split_earth_currents(currents, reference_bus=reference),
                split_earth_currents(currents, reference_bus=reference),
            )


def _two_clusters(n: int) -> Dict[str, complex]:
    """A feeding and a returning cluster, the realistic shape of the input."""
    rng = np.random.default_rng(1)
    magnitudes = np.abs(rng.normal(size=n) + 1j * rng.normal(size=n))
    values = np.where(
        np.arange(n) < n // 3,
        magnitudes * np.exp(1j * 0.3),
        -magnitudes * np.exp(1j * 0.25),
    )
    return {f"B{k}": complex(v) for k, v in enumerate(values)}


def test_a_large_split_is_still_bit_identical():
    """n = 4000 is the largest size at which the quadratic oracle still runs
    in well under a second."""
    currents = _two_clusters(4000)
    assert _same(
        _old_split_earth_currents(currents, reference_bus="B0"),
        split_earth_currents(currents, reference_bus="B0"),
    )


@pytest.mark.slow
def test_the_split_is_not_quadratic():
    """n = 20 000 took 12 s per call before P3; the bound leaves a factor of
    about fifty to the measured time so that load on the machine cannot
    make the test flaky."""
    currents = _two_clusters(20_000)
    start = time.perf_counter()
    split = split_earth_currents(currents, reference_bus="B0")
    elapsed = time.perf_counter() - start
    assert split is not None
    assert elapsed < 0.5


# ---------------------------------------------------------------------------
# P9 / F14 -- the earth group must not be decided by rounding noise
# ---------------------------------------------------------------------------

import contextlib  # noqa: E402
import logging  # noqa: E402
import warnings  # noqa: E402

import groundinsight as gi  # noqa: E402
from groundinsight.diagnostics import ModelAssumptionWarning  # noqa: E402
from groundinsight.electrical_network import ElectricalNetwork  # noqa: E402

FREQ = 50.0


def test_noise_level_currents_are_left_out_of_both_groups():
    """Buses whose current is ``<= 1e-9 * max|I_a|`` carry no physical earth
    current; their angle is rounding noise. They must neither join a group
    nor tip it -- the split equals the split of the noise-free set, bit for
    bit (second way: the previous implementation on that subset)."""
    rng = np.random.default_rng(7)
    clean = {"F": -60.0 + 5j, "A": -30.0 + 2j, "S": 85.0 - 6j, "B": 5.0 + 0j}
    for trial in range(50):
        noisy = dict(clean)
        for k in range(4):
            angle = rng.uniform(-np.pi, np.pi)
            noisy[f"N{k}"] = 1e-15 * 85.0 * complex(np.cos(angle), np.sin(angle))
        split = split_earth_currents(noisy, reference_bus="F")
        oracle = _old_split_earth_currents(clean, reference_bus="F")
        assert _same(split, oracle), trial
        assert not any(n.startswith("N") for n in split.feeding_buses + split.returning_buses)


def test_currents_above_the_threshold_are_kept():
    """The filter is relative and narrow: 1e-6 of the largest current is a
    physical (if small) contribution and stays in its group."""
    currents = {"F": -60.0 + 0j, "S": 59.99994 + 0j, "W": -6e-5 + 0j}
    split = split_earth_currents(currents, reference_bus="F")
    assert "W" in split.feeding_buses


def test_an_anchor_phasor_orients_a_split_without_reference_current():
    """Without a current at the reference bus the old rule named the
    *smaller* group feeding -- a choice that noise buses could tip and that
    has no physical meaning. With the fault-bus EPR as anchor the feeding
    group is the one whose sum is in phase with it, whatever the sizes."""
    currents = {"A": -10 + 1j, "B": -12 + 0.5j, "C": 7 + 0j, "D": 8 - 1j, "E": 7 + 0.5j}
    # fault-side potential in the direction of A and B
    split = split_earth_currents(currents, reference_bus="F", anchor=-1.0 + 0.1j)
    assert split.feeding_buses == ["A", "B"]
    assert (split.i_earth * complex(-1.0, 0.1).conjugate()).real > 0
    # the opposite potential selects the other group
    flipped = split_earth_currents(currents, reference_bus="F", anchor=1.0 - 0.1j)
    assert flipped.feeding_buses == ["C", "D", "E"]
    # a reference bus with a real current still wins over the anchor
    anchored = split_earth_currents(currents, reference_bus="C", anchor=-1.0)
    assert anchored.feeding_buses == ["C", "D", "E"]


def test_without_anchor_and_reference_the_previous_rule_applies():
    currents = {"A": 10 + 0j, "B": 5 + 0j, "C": -15 + 0j}
    assert split_earth_currents(currents).feeding_buses == ["C"]


# --- network level ----------------------------------------------------------


def _bus_type(name: str, formula: str) -> gi.BusType:
    return gi.BusType(name=name, system_type="S", voltage_level=20.0, impedance_formula=formula)


CABLE = gi.BranchType(
    name="p9_cable", grounding_conductor=True,
    self_impedance_formula="(0.25 + j*f*0.012)*l",
    mutual_impedance_formula="(j*f*0.008)*l",
    phase_impedance_formula="(0.2 + j*f*0.010)*l",
)
OVERHEAD = gi.BranchType(
    name="p9_ohl", grounding_conductor=False,
    self_impedance_formula="(0.3 + j*f*0.015)*l",
    mutual_impedance_formula="(j*f*0.004)*l",
    phase_impedance_formula="(0.15 + j*f*0.012)*l",
)


def _anchorless_network_with_a_noise_island() -> gi.Network:
    """The mechanism of a random audit network, built on purpose.

    ``T`` is an electrode-less tower (formula ``"nan"``) carrying the fault,
    so the split has no anchor. Behind the source bus ``S`` a spur
    ``S - X - Y`` is reached through an overhead line without earth wire: the
    phase conductor continues, the earthing network does not. The spur
    carries no phase current physically, but the phase solve leaves rounding
    noise on it, and that noise excites the earth island ``{X, Y}`` through
    the coupling of the cable ``X - Y`` -- electrode currents of about 1e-15
    of the largest one, at random angles.
    """
    station = _bus_type("p9_station", "rho*0.01 + j*f*0.0001")
    tower = _bus_type("p9_tower", "nan")
    net = gi.create_network(name="p9_anchorless", frequencies=[FREQ, 150.0])
    for name in ("A", "B", "S", "X", "Y"):
        gi.create_bus(name=name, type=station, specific_earth_resistance=100.0, network=net)
    gi.create_bus(name="T", type=tower, network=net)
    gi.create_branch(name="L_TA", type=CABLE, from_bus="T", to_bus="A", length=0.5, network=net)
    gi.create_branch(name="L_AB", type=CABLE, from_bus="A", to_bus="B", length=0.5, network=net)
    gi.create_branch(name="L_BS", type=CABLE, from_bus="B", to_bus="S", length=1.2, network=net)
    gi.create_branch(name="O_SX", type=OVERHEAD, from_bus="S", to_bus="X", length=0.3, network=net)
    gi.create_branch(name="L_XY", type=CABLE, from_bus="X", to_bus="Y", length=0.5, network=net)
    gi.create_source(name="Q", bus="S", values={FREQ: 1000.0, 150.0: 80.0}, network=net)
    gi.create_fault(name="F", bus="T", scalings={FREQ: 1.0, 150.0: 1.0}, network=net)
    return net


def _solve(net: gi.Network, fault: str = "F") -> gi.Result:
    logging.getLogger("groundinsight").setLevel(logging.ERROR)
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            return gi.run_fault(net, fault)
    finally:
        logging.getLogger("groundinsight").setLevel(logging.NOTSET)


@contextlib.contextmanager
def _perturbed_solution(seed: int, ulps: float = 4.0):
    """Perturb the solved EPR vector by a few ulp (normwise) before the
    reduction factors are derived -- the second solver the robustness has to
    survive: same equations, different rounding."""
    original = ElectricalNetwork.compute_reduction_factors
    rng = np.random.default_rng(seed)

    def perturbed(self):
        for freq, u in list(self.u_vectors.items()):
            scale = float(np.abs(u).max())
            noise = rng.normal(size=u.size) + 1j * rng.normal(size=u.size)
            self.u_vectors[freq] = u + noise * (ulps * np.finfo(float).eps * scale)
        return original(self)

    ElectricalNetwork.compute_reduction_factors = perturbed
    try:
        yield
    finally:
        ElectricalNetwork.compute_reduction_factors = original


def _earth_quantities(result: gi.Result) -> dict:
    factor = result.reduction_factor
    return {
        f: (
            sorted(factor.earth_buses[f]),
            None if factor.i_earth[f] is None else complex(factor.i_earth[f]),
            factor.value_current[f],
        )
        for f in factor.value
    }


@pytest.mark.parametrize("seed", range(5))
def test_the_earth_group_survives_a_few_ulp_of_rounding(seed):
    """F14 acceptance: two solves that agree to 1e-15 report the same group,
    the same I_E and the same current-based reduction factor."""
    reference = _earth_quantities(_solve(_anchorless_network_with_a_noise_island()))
    with _perturbed_solution(seed):
        perturbed = _earth_quantities(_solve(_anchorless_network_with_a_noise_island()))
    for f, (group, i_earth, r_current) in reference.items():
        assert perturbed[f][0] == group, (f, perturbed[f][0], group)
        assert abs(perturbed[f][1] - i_earth) <= 1e-12 * abs(i_earth)
        assert perturbed[f][2] == pytest.approx(r_current, rel=1e-12)


def test_the_anchorless_group_is_the_fault_side_of_the_network():
    """Physics of the anchor rule: the electrode-less fault tower passes the
    fault current through the shield to ``A`` and ``B``, which feed the soil;
    the source station ``S`` takes it back. The old rule named the smaller
    group feeding, which here is the source side."""
    result = _solve(_anchorless_network_with_a_noise_island())
    factor = result.reduction_factor
    fault_epr = next(complex(b.uepr_freq[FREQ]) for b in result.buses if b.name == "T")
    assert set(factor.earth_buses[FREQ]) == {"A", "B"}
    assert (complex(factor.i_earth[FREQ]) * fault_epr.conjugate()).real > 0
    assert all(not n.startswith(("X", "Y")) for n in factor.earth_buses[FREQ])


def _dead_end_fault_network() -> gi.Network:
    """Fault at the far end of a cable whose end has no electrode: the whole
    fault current returns through the shield, no electrode carries any.
    Physically I_E = 0; numerically the electrode currents are rounding
    noise and the old split turned them into a group, I_E ~ 1e-14 A and a
    ratio-of-noise earthing impedance."""
    station = _bus_type("p9_dead_station", "0.3")
    tower = _bus_type("p9_dead_tower", "nan")
    net = gi.create_network(name="p9_dead_end", frequencies=[FREQ])
    gi.create_bus(name="S", type=station, network=net)
    gi.create_bus(name="T", type=tower, network=net)
    gi.create_branch(name="L", type=CABLE, from_bus="S", to_bus="T", length=2.0, network=net)
    gi.create_source(name="Q", bus="S", values={FREQ: 1000.0}, network=net)
    gi.create_fault(name="F", bus="T", scalings={FREQ: 1.0}, network=net)
    return net


def _source_behind_dead_end_network() -> gi.Network:
    """A configuration seen in a random audit network: the source sits on
    an electrode-less tower whose only connection is one cable to the fault
    bus. The fault bus potential is zero in exact arithmetic, with and
    without coupling, so the EPR-based reduction factor is a ratio of two
    rounding errors."""
    station = _bus_type("p9_src_station", "2.0")
    tower = _bus_type("p9_src_tower", "nan")
    net = gi.create_network(name="p9_src_dead_end", frequencies=[FREQ])
    gi.create_bus(name="T", type=tower, network=net)
    for name in ("F", "A", "B"):
        gi.create_bus(name=name, type=station, network=net)
    gi.create_branch(name="L_TF", type=CABLE, from_bus="T", to_bus="F", length=1.0, network=net)
    gi.create_branch(name="L_FA", type=CABLE, from_bus="F", to_bus="A", length=1.0, network=net)
    gi.create_branch(name="L_AB", type=CABLE, from_bus="A", to_bus="B", length=1.0, network=net)
    gi.create_source(name="Q", bus="T", values={FREQ: 1000.0}, network=net)
    gi.create_fault(name="F", bus="F", scalings={FREQ: 1.0}, network=net)
    return net


def _diagnostic_codes(result: gi.Result) -> List[str]:
    return [d.code for d in result.diagnostics]


def test_a_vanishing_earth_current_gives_none_and_says_why():
    net = _dead_end_fault_network()
    with pytest.warns(ModelAssumptionWarning, match="EARTH_GROUP_UNDEFINED|earth-return group"):
        result = gi.run_fault(net, "F")
    factor = result.reduction_factor
    assert factor.value_current[FREQ] is None
    assert factor.i_earth[FREQ] is None
    assert factor.earth_buses[FREQ] == []
    assert "EARTH_GROUP_UNDEFINED" in _diagnostic_codes(result)
    record = next(d for d in result.diagnostics if d.code == "EARTH_GROUP_UNDEFINED")
    assert ("fault", "F") in record.elements
    # Second way: every electrode current is rounding noise against I_F, and
    # the EPR-based factor (which needs no group) keeps its closed form
    # |1 - Z_m/Z_s| -- the shield is the only return path.
    largest = max(abs(complex(b.ia_freq[FREQ])) for b in result.buses)
    assert largest <= 1e-9 * 1000.0
    z_s, z_m = complex(0.25, 0.6) * 2.0, complex(0.0, 0.4) * 2.0
    assert factor.value[FREQ] == pytest.approx(abs(1 - z_m / z_s), rel=1e-9)


def test_a_vanishing_fault_epr_gives_none_and_says_why():
    net = _source_behind_dead_end_network()
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        result = gi.run_fault(net, "F")
    factor = result.reduction_factor
    assert factor.value[FREQ] is None
    codes = _diagnostic_codes(result)
    assert "REDUCTION_FACTOR_UNDEFINED" in codes
    assert "EARTH_GROUP_UNDEFINED" in codes
    # Second way: the reference solver puts the fault-bus potential at zero
    # to rounding, against hundreds of volts at the tower.
    from tests import _reference_mna as ref

    rd = ref.reference_network(net, FREQ, "F")
    with_coupling = ref.solve_oneway(rd, thevenin_phase="norton")
    without = ref.solve_oneway(rd, thevenin_phase="norton", with_mutual=False)
    assert abs(without["u"]["F"]) <= 1e-9 * max(abs(v) for v in without["u"].values())
    assert abs(with_coupling["u"]["F"]) <= 1e-9 * max(abs(v) for v in with_coupling["u"].values())


def test_vanishing_references_stay_vanished_under_rounding():
    for builder in (_dead_end_fault_network, _source_behind_dead_end_network):
        for seed in range(3):
            with _perturbed_solution(seed):
                result = _solve(builder())
            factor = result.reduction_factor
            assert factor.value_current[FREQ] is None
            assert factor.i_earth[FREQ] is None


def test_noise_free_networks_split_bit_for_bit_as_before():
    """Networks without noise-level electrode currents and with an earthed
    fault bus give the previous result exactly: the oracle split applied to
    the electrode currents reproduces ``I_E`` and the group with ``==``.

    The split is fed with exactly the electrode currents ``ResultBus.ia_freq``
    reports (P6: the kernel no longer recomputes them for the split with
    Python's complex division, which differed in the last bit), so those are
    the currents rebuilt here; buses without electrode are left out.
    """
    from tests.test_audit_pass19_physics import STRUCTURAL_CASES, build, solve

    for case, spec in STRUCTURAL_CASES.items():
        net = build(spec, case.replace(" ", "_"))
        result = solve(net)
        factor = result.reduction_factor
        fault_bus = spec["faults"]["F1"][0]
        for f in spec["freqs"]:
            currents = {}
            for b in result.buses:
                z = net.buses[b.name].impedance[f]
                z = complex(z.real, z.imag)
                if np.isinf(z.real) or np.isinf(z.imag):
                    continue
                currents[b.name] = complex(b.ia_freq[f])
            old = _old_split_earth_currents(currents, reference_bus=fault_bus)
            assert complex(factor.i_earth[f]) == old.i_earth, (case, f)
            assert factor.earth_buses[f] == old.feeding_buses, (case, f)


@pytest.mark.parametrize("z_fault", ["1.0", "1e-6", "1e-9", "1e-12"])
def test_a_near_ideal_fault_electrode_keeps_its_reduction_factor(z_fault):
    """A tiny fault-bus EPR is not automatically noise: with a near-ideal
    electrode it is small *and accurate*. The EPR-based factor at the fault
    bus is independent of the fault-bus electrode (both voltages scale with
    1/(1 + Y_B Z_net)), so every value must agree with the 1-ohm one -- and
    on a single line with the closed form |1 - Z_m/Z_s| (second way)."""
    station = _bus_type(f"p9_ideal_s_{z_fault}", "0.5")
    fault_type = _bus_type(f"p9_ideal_f_{z_fault}", f"rho*0 + {z_fault}")
    line = gi.BranchType(
        name=f"p9_ideal_line_{z_fault}", grounding_conductor=True,
        self_impedance_formula="(0.2+0.4*j)*l", mutual_impedance_formula="(0.05+0.35*j)*l",
    )
    net = gi.create_network(name="p9_near_ideal", frequencies=[FREQ])
    gi.create_bus(name="S", type=station, network=net)
    gi.create_bus(name="F", type=fault_type, network=net)
    gi.create_branch(name="L", type=line, from_bus="S", to_bus="F", length=5.0, network=net)
    gi.create_source(name="Q", bus="S", values={FREQ: 1000.0}, network=net)
    gi.create_fault(name="EF", bus="F", scalings={FREQ: 1.0}, network=net)
    result = gi.run_fault(net, "EF")
    assert "REDUCTION_FACTOR_UNDEFINED" not in _diagnostic_codes(result)
    closed_form = abs(1 - complex(0.05, 0.35) / complex(0.2, 0.4))
    assert result.reduction_factor.value[FREQ] == pytest.approx(closed_form, rel=1e-6)

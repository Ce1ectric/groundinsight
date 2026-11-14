# tests/test_audit_pass19_kernel_loops.py
"""
Audit pass 19 -- the last per-element Python loops of the kernel (P6).

Two loops over every bus or branch and frequency were left after the sparse
kernel: the electrode currents of the current-based reduction factor, one
``resolved_impedance`` and one Python complex division per bus and
frequency, and the check for coupled branches behind the
``THEVENIN_COUPLING`` diagnostic, one dictionary lookup and resolution per
branch and frequency. Both now read the arrays the assembly gathered.

The electrode currents are no longer recomputed at all: the split into the
earth-return group uses the very currents the result reports as
``ResultBus.ia``. Before, the two were computed with different divisions and
could differ in the last bit, so ``I_E`` was not exactly the sum of the
reported electrode currents of its group. The second way here is exactly
that sum, computed from the result; for the coupling check it is the former
scalar rule, written out in this file.
"""

from __future__ import annotations

import warnings
from typing import List

import pytest

import groundinsight as gi
from groundinsight.electrical_network import _is_open_circuit
from groundinsight.utils.earth_current import NOISE_REL, split_earth_currents
from tests import test_audit_pass19_performance as perf
from tests._warning_policy import acknowledge

#: Random networks: rings, Thevenin sources, 0 Hz bins and electrode currents
#: spread in angle occur by construction; the tests compare the kernel with
#: its reference.
pytestmark = acknowledge(
    "DC_LIMIT", "EARTH_GROUP_SPREAD", "PHASE_PROXY", "THEVENIN_COUPLING"
)


def _solvable_random(seeds) -> List[gi.Network]:
    nets = []
    for seed in seeds:
        net = perf.random_network(seed)
        try:
            gi.run_fault(net, "F")
        except (ValueError, RuntimeError, ZeroDivisionError):
            continue
        nets.append(net)
    return nets


NETWORKS = {
    "ring": lambda: perf.ring(80, F=7, n_sources=2),
    "mesh": lambda: perf.mesh(8, F=3, n_sources=4),
    "radial": lambda: perf.radial(150, F=3, n_sources=3),
    "thevenin": lambda: perf.thevenin_ring(40, F=3),
    "dc": lambda: perf.dc_ring(30),
    "open_ends": lambda: perf.STRUCTURED["open_ends_mesh8"](),
}


def _split_of_the_reported_currents(net: gi.Network, fault: str = "F"):
    """The earth-return split computed from ``ResultBus.ia`` and ``uepr``
    with the kernel's orientation rule (fault bus, else its potential)."""
    result = net.results[fault]
    fault_bus = net.faults[fault].bus
    out = {}
    for freq in net.frequencies:
        currents = {b.name: complex(b.ia_freq[freq]) for b in result.buses}
        potentials = {b.name: complex(b.uepr_freq[freq]) for b in result.buses}
        u_fault = potentials[fault_bus]
        u_scale = max(abs(u) for u in potentials.values())
        anchor = u_fault if abs(u_fault) > NOISE_REL * u_scale else None
        out[freq] = split_earth_currents(
            currents, reference_bus=fault_bus, anchor=anchor
        )
    return out


@pytest.mark.parametrize("name", sorted(NETWORKS))
def test_the_earth_return_current_is_the_split_of_the_reported_currents(name):
    """``I_E`` and the earth group are the split of exactly the electrode
    currents ``ResultBus.ia`` reports -- bit for bit."""
    net = NETWORKS[name]()
    gi.run_fault(net, "F")
    factor = net.results["F"].reduction_factor
    for freq, split in _split_of_the_reported_currents(net).items():
        if factor.i_earth.get(freq) is None:
            continue
        assert complex(factor.i_earth[freq]) == split.i_earth, (name, freq)
        assert sorted(factor.earth_buses[freq]) == sorted(split.feeding_buses)


def test_the_earth_return_current_is_exact_on_random_networks():
    nets = _solvable_random(range(40))
    assert len(nets) >= 25
    checked = 0
    for net in nets:
        factor = net.results["F"].reduction_factor
        for freq, split in _split_of_the_reported_currents(net).items():
            if factor.i_earth.get(freq) is None or split is None:
                continue
            assert complex(factor.i_earth[freq]) == split.i_earth
            checked += 1
    assert checked > 50


# --------------------------------------------------------------------------
# THEVENIN_COUPLING
# --------------------------------------------------------------------------


def _scalar_coupled(en) -> List[str]:
    """The former rule, branch by branch and frequency by frequency."""
    coupled = []
    for branch in en.network.branches.values():
        if not branch.active or not branch.type.grounding_conductor:
            continue
        if branch.from_bus not in en.bus_indices or branch.to_bus not in en.bus_indices:
            continue
        for freq in en.network.frequencies:
            z_mutual = (branch.mutual_impedance or {}).get(freq)
            z_self = en.resolved_impedance(
                (branch.self_impedance or {}).get(freq), freq
            )
            if z_mutual is None or z_self is None or _is_open_circuit(z_self):
                continue
            if complex(z_mutual.real, z_mutual.imag) != 0:
                coupled.append(branch.name)
                break
    return coupled


def _mixed_coupling() -> gi.Network:
    """A Thevenin ring whose branches cover every case of the rule: coupled,
    uncoupled (zero mutual impedance), coupled at one frequency only, a
    broken shield, no grounding conductor, an inactive branch and one with
    an inactive end."""
    net = perf.thevenin_ring(24, F=3)
    uncoupled = gi.BranchType(
        name="uncoupled", grounding_conductor=True,
        self_impedance_formula="(0.2 + j*f*0.01)*l", mutual_impedance_formula="0",
        phase_impedance_formula="(0.2 + j*f*0.01)*l",
    )
    one_frequency = gi.BranchType(
        name="one_f", grounding_conductor=True,
        self_impedance_formula="(0.2 + j*f*0.01)*l",
        mutual_impedance_formula="Max(f - 120, 0)*0.001*l",
        phase_impedance_formula="(0.2 + j*f*0.01)*l",
    )
    net.update_branch("L1", type=uncoupled)
    net.update_branch("L2", type=one_frequency)
    net.update_branch("L3", type=perf.BROKEN_SHIELD)
    net.update_branch("L4", type=perf.OHL)
    net.branches["L6"].active = False
    net.buses["B9"].active = False
    return net


@pytest.mark.parametrize(
    "builder",
    [_mixed_coupling, lambda: perf.thevenin_ring(30, F=3), lambda: perf.ring(20, F=3)],
    ids=["mixed", "thevenin", "current_sources"],
)
def test_the_coupled_branches_are_those_of_the_scalar_rule(builder):
    net = builder()
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        gi.run_fault(net, "F")
    en = net.electrical_network
    assert en._coupled_grounding_branches() == _scalar_coupled(en)


def test_the_mixed_case_exercises_both_outcomes():
    net = _mixed_coupling()
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        gi.run_fault(net, "F")
    coupled = _scalar_coupled(net.electrical_network)
    assert "L1" not in coupled and "L2" in coupled and "L7" in coupled
    assert "L6" not in coupled and "L4" not in coupled

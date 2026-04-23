# tests/test_topology_and_reduction.py

"""
Topology and reduction-factor reference tests.

These tests cover the four physical reference cases supplied by the author
(branches without shield, a single MS cable, a mixed line with an interruption,
and a fully symmetric ring), plus two structural tests that the calculation
core must satisfy independently of the bus-insertion order.

Analytical reference (see module docstring of ``electrical_network.py``):

    For a network where the mutual Norton injection is exactly anti-parallel
    to the source/fault injection (i.e. a single simple path or a symmetric
    ring), the reduction factor simplifies to

        r = |1 - Z_mutual / Z_self|.

    With Z_self = 0.25 + j*0.6 and Z_mutual = j*0.6 this evaluates to 0.3846,
    which matches the author's field reference band of 0.3 ... 0.4 for MV
    cables with PEN/shield.
"""

import math
import pytest

import groundinsight as gi
from groundinsight.models.core_models import BusType, BranchType


# ---------------------------------------------------------------------------
# Shared fixtures (not pytest fixtures to keep the tests self-contained and
# to match the style of tests/test_calculation_logic.py).
# ---------------------------------------------------------------------------


def _bus_type():
    """Small-impedance grounded bus so the shield path dominates."""
    return BusType(
        name="BusUnit",
        description="Unit-like bus impedance for reference tests",
        system_type="Grounded",
        voltage_level=20.0,
        impedance_formula="rho * 0 + 1.0 + I * f * 0",
    )


def _ms_cable_branch_type():
    """MV cable with Z_self = 0.25 + j*0.6 and Z_mutual = j*0.6 per unit length."""
    return BranchType(
        name="MSCable",
        description="MV cable reference from the author",
        grounding_conductor=True,
        self_impedance_formula="(rho * 0 + 0.25 + I * 0.6)*l",
        mutual_impedance_formula="(rho * 0 + 0.0 + I * 0.6)*l",
    )


def _ohl_branch_type():
    """Overhead line without shield; NaN impedances per project convention."""
    return BranchType(
        name="OHLine",
        description="Overhead line without shield",
        grounding_conductor=False,
        self_impedance_formula="NaN",
        mutual_impedance_formula="NaN",
    )


def _reduction_factor(net, fault_name, freq=50):
    """Extract the reduction factor for a given fault at a given frequency."""
    return net.results[fault_name].reduction_factor.value[freq]


# Analytical reference: r = |1 - Z_mutual / Z_self|
Z_SELF = complex(0.25, 0.6)
Z_MUTUAL = complex(0.0, 0.6)
ALPHA = Z_MUTUAL / Z_SELF
R_REF = abs(1.0 - ALPHA)  # ~= 0.3846


# ---------------------------------------------------------------------------
# Reference case 1: no shield -> r = 1
# ---------------------------------------------------------------------------


def test_reduction_factor_line_without_shield():
    """A two-bus line connected only by an overhead line without shield must
    produce r == 1 because no mutual coupling is injected at all."""
    net = gi.create_network(name="OHLOnly", frequencies=[50])
    bus_type = _bus_type()
    ohl = _ohl_branch_type()

    gi.create_bus(name="bus1", type=bus_type, network=net)
    gi.create_bus(name="bus2", type=bus_type, network=net)
    gi.create_branch(
        name="branch1",
        type=ohl,
        from_bus="bus1",
        to_bus="bus2",
        length=1.0,
        network=net,
    )

    gi.create_source(name="src", bus="bus1", values={50: 100.0}, network=net)
    gi.create_fault(name="fault", bus="bus2", scalings={50: 1.0}, network=net)

    gi.run_fault(net, fault_name="fault")

    r = _reduction_factor(net, "fault")
    assert r == pytest.approx(1.0, abs=1e-6)


# ---------------------------------------------------------------------------
# Reference case 2: single MS cable section -> r ~= 0.385
# ---------------------------------------------------------------------------


def test_reduction_factor_single_ms_cable():
    """A two-bus MV-cable link must yield the analytical r = |1 - Z_m/Z_s|."""
    net = gi.create_network(name="MSCableOnly", frequencies=[50])
    bus_type = _bus_type()
    cable = _ms_cable_branch_type()

    gi.create_bus(name="bus1", type=bus_type, network=net)
    gi.create_bus(name="bus2", type=bus_type, network=net)
    gi.create_branch(
        name="branch1",
        type=cable,
        from_bus="bus1",
        to_bus="bus2",
        length=1.0,
        network=net,
    )

    gi.create_source(name="src", bus="bus1", values={50: 100.0}, network=net)
    gi.create_fault(name="fault", bus="bus2", scalings={50: 1.0}, network=net)

    gi.run_fault(net, fault_name="fault")

    r = _reduction_factor(net, "fault")
    assert r == pytest.approx(R_REF, rel=1e-6)
    assert 0.30 <= r <= 0.40  # matches field reference band


# ---------------------------------------------------------------------------
# Reference case 3: mixed line with interruption -> r between the limits
# ---------------------------------------------------------------------------


def test_reduction_factor_mixed_line_with_interruption():
    """A cable-OHL-cable series link must yield a reduction factor between
    the OHL-only (r=1) and the cable-only (~0.385) limits."""
    net = gi.create_network(name="Mixed", frequencies=[50])
    bus_type = _bus_type()
    cable = _ms_cable_branch_type()
    ohl = _ohl_branch_type()

    for i in range(1, 5):
        gi.create_bus(name=f"bus{i}", type=bus_type, network=net)

    gi.create_branch(
        name="b12", type=cable, from_bus="bus1", to_bus="bus2", length=1.0, network=net
    )
    gi.create_branch(
        name="b23", type=ohl, from_bus="bus2", to_bus="bus3", length=1.0, network=net
    )
    gi.create_branch(
        name="b34", type=cable, from_bus="bus3", to_bus="bus4", length=1.0, network=net
    )

    gi.create_source(name="src", bus="bus1", values={50: 100.0}, network=net)
    gi.create_fault(name="fault", bus="bus4", scalings={50: 1.0}, network=net)

    gi.run_fault(net, fault_name="fault")

    r = _reduction_factor(net, "fault")
    assert R_REF < r < 1.0


# ---------------------------------------------------------------------------
# Structural test: direction must be derived from the path, not from the
# bus insertion index (this was the root cause of the ring-topology bug).
# ---------------------------------------------------------------------------


def _build_line(net_name, bus_order):
    """Create a 3-bus MS-cable line with buses inserted in the given order."""
    net = gi.create_network(name=net_name, frequencies=[50])
    bus_type = _bus_type()
    cable = _ms_cable_branch_type()

    for name in bus_order:
        gi.create_bus(name=name, type=bus_type, network=net)

    # branches always follow the electrical topology bus1 -> bus2 -> bus3
    gi.create_branch(
        name="b12", type=cable, from_bus="bus1", to_bus="bus2", length=1.0, network=net
    )
    gi.create_branch(
        name="b23", type=cable, from_bus="bus2", to_bus="bus3", length=1.0, network=net
    )

    gi.create_source(name="src", bus="bus1", values={50: 100.0}, network=net)
    gi.create_fault(name="fault", bus="bus3", scalings={50: 1.0}, network=net)
    return net


def test_reduction_factor_independent_of_bus_insertion_order():
    """Inserting buses in natural vs. reversed order must produce the same
    result. The old index-based sign heuristic failed this test; the new
    path-based derivation passes it."""
    net_natural = _build_line("Natural", ["bus1", "bus2", "bus3"])
    net_reversed = _build_line("Reversed", ["bus3", "bus1", "bus2"])

    gi.run_fault(net_natural, fault_name="fault")
    gi.run_fault(net_reversed, fault_name="fault")

    r_nat = _reduction_factor(net_natural, "fault")
    r_rev = _reduction_factor(net_reversed, "fault")
    assert r_nat == pytest.approx(r_rev, rel=1e-9)
    assert r_nat == pytest.approx(R_REF, rel=1e-6)


# ---------------------------------------------------------------------------
# Ring topology: this is the case Christian found broken. With a symmetric
# ring and a user-set parallel_coefficient=0.5 the reduction factor must
# collapse back to the analytical r = |1 - Z_m/Z_s|. The automatic variant
# (auto_parallel_coefficients=True) must reach the same answer without the
# user having to set the coefficient.
# ---------------------------------------------------------------------------


def _build_symmetric_ring(net_name):
    """4-bus ring bus1-bus2-bus3-bus4-bus1, source at bus1, fault at bus3."""
    net = gi.create_network(name=net_name, frequencies=[50])
    bus_type = _bus_type()
    cable = _ms_cable_branch_type()

    for i in range(1, 5):
        gi.create_bus(name=f"bus{i}", type=bus_type, network=net)

    # Two parallel paths of equal impedance
    gi.create_branch(
        name="b12",
        type=cable,
        from_bus="bus1",
        to_bus="bus2",
        length=1.0,
        network=net,
        parallel_coefficient=0.5,
    )
    gi.create_branch(
        name="b23",
        type=cable,
        from_bus="bus2",
        to_bus="bus3",
        length=1.0,
        network=net,
        parallel_coefficient=0.5,
    )
    gi.create_branch(
        name="b14",
        type=cable,
        from_bus="bus1",
        to_bus="bus4",
        length=1.0,
        network=net,
        parallel_coefficient=0.5,
    )
    gi.create_branch(
        name="b43",
        type=cable,
        from_bus="bus4",
        to_bus="bus3",
        length=1.0,
        network=net,
        parallel_coefficient=0.5,
    )

    gi.create_source(name="src", bus="bus1", values={50: 100.0}, network=net)
    gi.create_fault(name="fault", bus="bus3", scalings={50: 1.0}, network=net)
    return net


def test_ring_with_parallel_coefficient_half():
    """Symmetric ring with parallel_coefficient=0.5 on every branch must yield
    the analytical reduction factor. This is the case where the legacy
    implementation produced implausible results."""
    net = _build_symmetric_ring("RingManualCoeff")
    gi.run_fault(net, fault_name="fault")

    r = _reduction_factor(net, "fault")
    assert r == pytest.approx(R_REF, rel=1e-6)


def test_ring_auto_phase_currents():
    """Same ring, but let the topology solver split the phase current. The
    parallel_coefficient is ignored in this mode and the result must still
    match the analytical value."""
    net = _build_symmetric_ring("RingAuto")
    # Override the manually set coefficients to 1.0 to prove that the auto
    # mode does not rely on them.
    for branch in net.branches.values():
        branch.parallel_coefficient = 1.0

    gi.run_fault(net, fault_name="fault", auto_parallel_coefficients=True)

    r = _reduction_factor(net, "fault")
    assert r == pytest.approx(R_REF, rel=1e-3)


def test_ring_variants_agree():
    """The path-based variant with hand-picked coefficients and the automatic
    topology variant must agree for a symmetric ring."""
    net_a = _build_symmetric_ring("RingVariantA")
    net_b = _build_symmetric_ring("RingVariantB")

    gi.run_fault(net_a, fault_name="fault", auto_parallel_coefficients=False)
    gi.run_fault(net_b, fault_name="fault", auto_parallel_coefficients=True)

    r_a = _reduction_factor(net_a, "fault")
    r_b = _reduction_factor(net_b, "fault")
    assert r_a == pytest.approx(r_b, rel=1e-3)

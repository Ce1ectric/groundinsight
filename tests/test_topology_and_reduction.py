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

import cmath
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


def _ms_cable_branch_type_freq_dependent():
    """MV cable with frequency-dependent reactance and constant L_self / M.

    The 50-Hz reference values used by the rest of this module are
    ``Z_self = 0.25 + j*0.6 ohm/km`` and ``Z_mutual = j*0.6 ohm/km``. Treating
    the imaginary parts as ``omega * L`` and ``omega * M`` and solving at
    ``f = 50 Hz`` gives the constant inductances of the cable shield::

        omega_50 = 2 * pi * 50 = 100 * pi rad/s
        L_self   = 0.6 / omega_50  ~ 1.910 mH / km
        M        = 0.6 / omega_50  ~ 1.910 mH / km        (k = M / L = 1)

    These values are representative for a 20 kV XLPE cable with a 25 mm^2
    copper shield. The resistive part ``R = 0.25 ohm/km`` is kept identical to
    the 50-Hz reference so that the new sweep agrees exactly with the existing
    cable tests at 50 Hz.

    Writing the formula as ``j * 0.6 * f / 50`` keeps the analytical 50-Hz
    value explicit and avoids dragging ``pi`` through the SymPy formula
    string -- ``omega * L = (2 * pi * f) * (0.6 / (2 * pi * 50)) = 0.6 * f / 50``.
    """
    return BranchType(
        name="MSCableFreq",
        description="MV cable, frequency-dependent (constant L, M)",
        grounding_conductor=True,
        self_impedance_formula="(0.25 + I * 0.6 * f / 50) * l",
        mutual_impedance_formula="(0.0 + I * 0.6 * f / 50) * l",
    )


def _r_analytical_single_cable(f: float, R: float = 0.25, R_omegaL_50: float = 0.6) -> float:
    """Closed-form reduction factor for a single MS-cable section.

    With ``Z_self = R + j*omega*L`` and ``Z_mutual = j*omega*M`` and
    ``L == M`` (full coupling), the reduction factor reduces to::

        r(f) = |1 - Z_mutual/Z_self|
             = |R / (R + j * omega * L)|
             = R / sqrt(R**2 + (omega * L)**2)

    With the ``f/50`` parameterisation used by
    :func:`_ms_cable_branch_type_freq_dependent`, ``omega * L = 0.6 * f / 50``.

    Args:
        f: Frequency in Hz.
        R: Real part of ``Z_self`` (ohm/km), default ``0.25``.
        R_omegaL_50: ``omega * L`` at 50 Hz (ohm/km), default ``0.6``.

    Returns:
        The analytical reduction factor at frequency ``f``.
    """
    omega_L = R_omegaL_50 * f / 50.0
    return R / math.sqrt(R**2 + omega_L**2)


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


# ---------------------------------------------------------------------------
# Frequency sweep: with constant L_self == M and a purely resistive bus
# impedance, the reduction factor must converge towards 0 as f grows.
#
# The cable model is the frequency-dependent
# :func:`_ms_cable_branch_type_freq_dependent`. For a single cable section
# the closed form is
#
#     r(f) = R / sqrt(R**2 + (omega * L)**2)
#
# which is also the analytical reference for a fully symmetric ring with the
# fault diametrically opposite to the source (Norton injections perfectly
# anti-parallel along both halves).
# ---------------------------------------------------------------------------


_SWEEP_FREQS = [50, 100, 250, 500, 1000, 2500, 5000]


def test_reduction_factor_sweep_single_cable_converges_to_zero():
    """Single MS-cable section: r(f) follows the closed-form curve and
    decays monotonically towards 0 as the frequency grows."""
    net = gi.create_network(name="MSCableSweep", frequencies=_SWEEP_FREQS)
    bus_type = _bus_type()
    cable = _ms_cable_branch_type_freq_dependent()

    gi.create_bus(name="bus1", type=bus_type, network=net)
    gi.create_bus(name="bus2", type=bus_type, network=net)
    gi.create_branch(
        name="branch1", type=cable,
        from_bus="bus1", to_bus="bus2", length=1.0, network=net,
    )

    values = {f: 100.0 for f in _SWEEP_FREQS}
    scalings = {f: 1.0 for f in _SWEEP_FREQS}
    gi.create_source(name="src", bus="bus1", values=values, network=net)
    gi.create_fault(name="fault", bus="bus2", scalings=scalings, network=net)

    gi.run_fault(net, fault_name="fault")

    rf = net.results["fault"].reduction_factor.value
    rs = [rf[float(f)] for f in _SWEEP_FREQS]

    # 50 Hz reproduces the existing single-cable reference value (~0.385).
    assert rs[0] == pytest.approx(R_REF, rel=1e-6)

    # Monotonically decreasing across the entire sweep.
    for prev, curr in zip(rs, rs[1:]):
        assert curr < prev, f"r is not monotonically decreasing: {rs}"

    # Each value matches the closed-form expression r = R / sqrt(R^2+(wL)^2).
    for f, r in zip(_SWEEP_FREQS, rs):
        assert r == pytest.approx(_r_analytical_single_cable(f), rel=1e-3)

    # By 5 kHz the reduction factor is well below 5 % -- clear convergence
    # towards 0.
    assert rs[-1] < 0.05


def test_reduction_factor_sweep_20_bus_ring_converges_to_zero():
    """20-bus symmetric ring with the fault opposite the source: same
    closed-form reduction factor as a single cable, hence the same
    convergence towards 0 with rising frequency."""
    n_buses = 20
    fault_idx = n_buses // 2
    net = gi.create_network(name="Ring20Sweep", frequencies=_SWEEP_FREQS)
    bus_type = _bus_type()
    cable = _ms_cable_branch_type_freq_dependent()

    for i in range(n_buses):
        gi.create_bus(name=f"bus{i:02d}", type=bus_type, network=net)
    for i in range(n_buses):
        nxt = (i + 1) % n_buses
        gi.create_branch(
            name=f"b{i:02d}_{nxt:02d}", type=cable,
            from_bus=f"bus{i:02d}", to_bus=f"bus{nxt:02d}",
            length=1.0, network=net,
            parallel_coefficient=0.5,
        )

    values = {f: 100.0 for f in _SWEEP_FREQS}
    scalings = {f: 1.0 for f in _SWEEP_FREQS}
    gi.create_source(name="src", bus="bus00", values=values, network=net)
    gi.create_fault(
        name="fault", bus=f"bus{fault_idx:02d}",
        scalings=scalings, network=net,
    )

    gi.run_fault(net, fault_name="fault", auto_parallel_coefficients=True)

    rf = net.results["fault"].reduction_factor.value
    rs = [rf[float(f)] for f in _SWEEP_FREQS]

    # 50 Hz matches the analytical single-cable reference (the ring shares
    # the same closed form thanks to the symmetric anti-parallel injections).
    assert rs[0] == pytest.approx(R_REF, rel=1e-3)

    # Monotonically decreasing across the entire sweep.
    for prev, curr in zip(rs, rs[1:]):
        assert curr < prev, f"r on ring is not monotonically decreasing: {rs}"

    # Strict convergence towards 0 at high frequency.
    assert rs[-1] < 0.05


# ---------------------------------------------------------------------------
# Plausibility check: long homogeneous ring vs. asymptotic ladder formula.
#
# For a homogeneous ring (constant Z_l per branch, constant Z_q per bus) and
# zero mutual coupling, the input impedance seen at the fault bus -- defined
# as ``u_EPR / I_fault``, i.e. *without* dividing by the reduction factor
# ``r`` -- is given asymptotically (N -> infinity) by
#
#     Z_par = Z_l/2 + sqrt(Z_l**2 / 4 + Z_l * Z_q)
#     Z     = (Z_par / 2) || Z_q
#
# where ``Z_par`` is the input impedance of one half of the ring as seen
# from the fault bus, viewed as a semi-infinite ladder with the longitudinal
# step first, and the factor ``1/2`` accounts for the two halves of the
# ring meeting at the fault bus. The fault-bus grounding ``Z_q`` is in
# parallel with the combined ladder input.
#
# Adding a non-zero mutual impedance ``Z_m`` between the conductor and the
# return path must strictly reduce ``|u_EPR / I_fault|`` (induced mutual
# currents along the source-fault paths reduce the EPR). Note that the
# ``grounding_impedance`` reported by :func:`run_fault` is
# ``u_EPR / (r * I_fault)`` and is therefore invariant under ``Z_m`` for
# this fully symmetric configuration, so the check has to use the raw
# ``u_EPR / I_fault`` value.
# ---------------------------------------------------------------------------


def _build_homogeneous_ring(name, n, z_l, z_q, z_m=complex(0.0, 0.0)):
    """Build an N-bus homogeneous ring (source at ``b0``, fault at ``b{n//2}``).

    All branches share the same per-km self impedance ``z_l`` and per-km
    mutual impedance ``z_m``, all buses share the same grounding impedance
    ``z_q``. Branches have unit length and ``parallel_coefficient = 0.5``
    so that the two halves of the ring carry the symmetric phase current.
    """
    bus_type = BusType(
        name="HomBus",
        description="Homogeneous bus grounding impedance",
        system_type="Grounded",
        voltage_level=20.0,
        impedance_formula=f"({z_q.real} + I*{z_q.imag}) + 0*rho + 0*f",
    )
    branch_type = BranchType(
        name="HomBranch",
        description="Homogeneous branch with shield",
        grounding_conductor=True,
        self_impedance_formula=f"({z_l.real} + I*{z_l.imag})*l",
        mutual_impedance_formula=f"({z_m.real} + I*{z_m.imag})*l",
    )
    net = gi.create_network(name=name, frequencies=[50])
    for i in range(n):
        gi.create_bus(name=f"b{i}", type=bus_type, network=net)
    for i in range(n):
        gi.create_branch(
            name=f"br{i}",
            type=branch_type,
            from_bus=f"b{i}",
            to_bus=f"b{(i + 1) % n}",
            length=1.0,
            network=net,
            parallel_coefficient=0.5,
        )
    gi.create_source(name="src", bus="b0", values={50: 100.0}, network=net)
    gi.create_fault(
        name="fault", bus=f"b{n // 2}", scalings={50: 1.0}, network=net
    )
    return net


def _ladder_input_impedance(z_l: complex, z_q: complex) -> complex:
    """Closed-form ring input impedance for a homogeneous ladder.

    ``Z_par = Z_l/2 + sqrt(Z_l**2/4 + Z_l * Z_q)`` is the semi-infinite
    half-ring input impedance (longitudinal step first); ``Z = (Z_par/2)
    || Z_q`` puts the two halves and the fault-bus grounding in parallel.
    """
    z_par = z_l / 2 + cmath.sqrt(z_l ** 2 / 4 + z_l * z_q)
    half = z_par / 2
    return half * z_q / (half + z_q)


def _raw_input_impedance_magnitude(net, fault_name="fault", freq=50.0):
    """Return ``|u_EPR / I_fault|`` at the fault bus.

    This is the raw input impedance seen at the fault location, *without*
    the ``1/r`` scaling that :attr:`Result.grounding_impedance` applies. It
    is the quantity for which the closed-form ladder formula above holds
    and which decreases strictly when mutual coupling is switched on.
    """
    fault = net.faults[fault_name]
    bus_result = next(
        b for b in net.results[fault_name].buses if b.name == fault.bus
    )
    uepr = bus_result.uepr_freq[freq]
    uepr_complex = complex(uepr.real, uepr.imag)
    i_fault = sum(
        complex(s.values[freq]) * complex(fault.scalings[freq])
        for s in net.sources.values()
    )
    return abs(uepr_complex) / abs(i_fault)


@pytest.mark.parametrize(
    "z_l, z_q",
    [
        (complex(0.1, 0.0), complex(10.0, 0.0)),
        (complex(0.1, 0.5), complex(10.0, 5.0)),
        (complex(0.2, 0.6), complex(5.0, 2.0)),
        (complex(1.0, 0.0), complex(1.0, 0.0)),
    ],
)
def test_homogeneous_ring_matches_ladder_formula(z_l, z_q):
    """Long homogeneous ring without mutual coupling: ``|u_EPR / I_fault|``
    at the fault bus must match the closed-form ladder input impedance
    ``|(Z_par/2) || Z_q|`` with ``Z_par = Z_l/2 + sqrt(Z_l**2/4 + Z_l*Z_q)``.

    The ring has 200 buses (~100 sections per half ring), enough for the
    ladder to converge to its asymptotic input impedance for the parameter
    ranges chosen here.
    """
    net = _build_homogeneous_ring(
        f"HomRing_{abs(z_l):.2f}_{abs(z_q):.2f}",
        n=200,
        z_l=z_l,
        z_q=z_q,
    )
    gi.run_fault(net, fault_name="fault")

    z_meas = _raw_input_impedance_magnitude(net)
    z_theory = abs(_ladder_input_impedance(z_l, z_q))

    rel_err = abs(z_meas - z_theory) / z_theory
    assert rel_err < 1e-2, (
        f"|u_EPR/I| = {z_meas} vs. theory = {z_theory} "
        f"(rel.err = {rel_err:.2e})"
    )


def test_homogeneous_ring_mutual_coupling_strictly_reduces_z():
    """In the homogeneous ring, switching on a non-zero mutual impedance
    ``Z_m`` must strictly reduce ``|u_EPR / I_fault|`` at the fault bus.

    The ``grounding_impedance`` reported in the result is invariant under
    ``Z_m`` for this symmetric configuration (``r`` and ``u_EPR`` decrease
    proportionally), so the check uses the raw ``u_EPR / I_fault``.
    """
    z_l = complex(0.1, 0.5)
    z_q = complex(10.0, 5.0)

    net_no_m = _build_homogeneous_ring(
        "HomRingNoMutual", n=200, z_l=z_l, z_q=z_q,
        z_m=complex(0.0, 0.0),
    )
    gi.run_fault(net_no_m, fault_name="fault")
    z0 = _raw_input_impedance_magnitude(net_no_m)

    # Without mutual coupling the closed-form ladder formula must hold.
    z_theory = abs(_ladder_input_impedance(z_l, z_q))
    assert z0 == pytest.approx(z_theory, rel=1e-2)

    # Sweep increasing mutual coupling. Every step must strictly decrease
    # |u_EPR / I_fault| compared to the previous step.
    prev = z0
    for z_m_imag in (0.05, 0.1, 0.2, 0.3, 0.4, 0.5):
        net_m = _build_homogeneous_ring(
            f"HomRingM{z_m_imag}", n=200, z_l=z_l, z_q=z_q,
            z_m=complex(0.0, z_m_imag),
        )
        gi.run_fault(net_m, fault_name="fault")
        z = _raw_input_impedance_magnitude(net_m)
        assert z < prev, (
            f"|u_EPR/I| did not decrease at Z_m=j{z_m_imag}: {z} >= {prev}"
        )
        prev = z

    # The strongest mutual coupling must reduce |u_EPR / I_fault| well
    # below half of the no-coupling value.
    assert prev < 0.5 * z0

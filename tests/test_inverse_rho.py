# tests/test_inverse_rho.py

"""
Tests for :func:`groundinsight.analysis.find_max_rho_scaling`.

These tests cover the bisection logic, the boundary cases (constraint
fully admissible / unreachable), input validation, and the guarantee that
the original ``specific_earth_resistance`` of every selected bus is
restored after every call (success or failure).
"""

import pytest

import groundinsight as gi
from groundinsight.analysis import find_max_rho_scaling
from groundinsight.models.core_models import BusType, BranchType


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


def _bus_type_linear_rho():
    """Bus impedance Z_q = 0.01 * rho (frequency-independent).

    With rho = 100 the bus grounding impedance is 1 ohm, with rho = 200
    it is 2 ohm, etc. This makes the EPR strictly monotone in rho and
    keeps the analytical reasoning simple for the unit tests below.
    """
    return BusType(
        name="LinRhoBus",
        description="Z_q = 0.01 * rho",
        system_type="Grounded",
        voltage_level=20.0,
        impedance_formula="rho * 0.01 + 0*f",
    )


def _ms_cable_branch_type():
    return BranchType(
        name="MSCable",
        description="MV cable, full coupling",
        grounding_conductor=True,
        self_impedance_formula="(0.25 + I*0.6)*l",
        mutual_impedance_formula="(0.0 + I*0.6)*l",
    )


def _build_two_bus_net(rho0: float = 100.0):
    """Two-bus line, source at b0, fault at b1."""
    bt = _bus_type_linear_rho()
    brt = _ms_cable_branch_type()
    net = gi.create_network(name="InvNet", frequencies=[50])
    gi.create_bus(
        name="b0", type=bt, network=net, specific_earth_resistance=rho0
    )
    gi.create_bus(
        name="b1", type=bt, network=net, specific_earth_resistance=rho0
    )
    gi.create_branch(
        name="br", type=brt, from_bus="b0", to_bus="b1",
        length=1.0, network=net,
    )
    gi.create_source(name="src", bus="b0", values={50: 100.0}, network=net)
    gi.create_fault(name="flt", bus="b1", scalings={50: 1.0}, network=net)
    return net


def _baseline_epr(net, fault_name="flt", fault_bus="b1"):
    gi.run_fault(net, fault_name=fault_name)
    return next(
        rb for rb in net.results[fault_name].buses if rb.name == fault_bus
    ).uepr


# ---------------------------------------------------------------------------
# Bisection: c_max around the baseline u_max
# ---------------------------------------------------------------------------


def test_find_max_rho_scaling_anchored_at_unity():
    """Setting ``u_max`` equal to the EPR at ``c = 1`` must yield
    ``c_max ~= 1`` and identical baseline rhos in ``rho_max``."""
    net = _build_two_bus_net(rho0=100.0)
    epr0 = _baseline_epr(net)

    result = find_max_rho_scaling(
        net, "flt", ["b0", "b1"], u_max=epr0, tol_rel=1e-5,
    )

    assert result["c_max"] == pytest.approx(1.0, rel=2e-5)
    assert result["u_epr_rms_at_c_max"] == pytest.approx(epr0, rel=2e-5)
    assert result["rho_max"]["b0"] == pytest.approx(100.0, rel=2e-5)
    assert result["rho_max"]["b1"] == pytest.approx(100.0, rel=2e-5)
    assert 1 <= result["iterations"] <= 60


def test_find_max_rho_scaling_halved_u_max_gives_smaller_c():
    """Halving ``u_max`` must produce a strictly smaller scaling factor."""
    net = _build_two_bus_net(rho0=100.0)
    epr0 = _baseline_epr(net)

    result = find_max_rho_scaling(
        net, "flt", ["b0", "b1"], u_max=0.5 * epr0, tol_rel=1e-4,
    )

    assert 0 < result["c_max"] < 1.0
    # The returned EPR must obey the bound up to tolerance.
    assert result["u_epr_rms_at_c_max"] <= 0.5 * epr0 * (1 + 1e-3)


def test_find_max_rho_scaling_admissible_returns_upper_bound():
    """If the EPR at the upper bracket bound is still below ``u_max``,
    the function returns the upper bound and zero iterations."""
    net = _build_two_bus_net(rho0=100.0)
    epr0 = _baseline_epr(net)

    # Pick u_max far above the maximum reachable EPR within a tight bracket.
    result = find_max_rho_scaling(
        net, "flt", ["b0", "b1"], u_max=1e6 * epr0, c_bounds=(0.1, 10.0),
    )

    assert result["c_max"] == pytest.approx(10.0, rel=1e-12)
    assert result["iterations"] == 0


# ---------------------------------------------------------------------------
# Input validation
# ---------------------------------------------------------------------------


def test_find_max_rho_scaling_rejects_non_positive_u_max():
    net = _build_two_bus_net()
    with pytest.raises(ValueError, match="u_max"):
        find_max_rho_scaling(net, "flt", ["b0"], u_max=0.0)


def test_find_max_rho_scaling_rejects_empty_bus_list():
    net = _build_two_bus_net()
    with pytest.raises(ValueError, match="bus_names"):
        find_max_rho_scaling(net, "flt", [], u_max=10.0)


def test_find_max_rho_scaling_rejects_unknown_bus():
    net = _build_two_bus_net()
    with pytest.raises(ValueError, match="Unknown bus"):
        find_max_rho_scaling(net, "flt", ["does_not_exist"], u_max=10.0)


def test_find_max_rho_scaling_rejects_unknown_fault():
    net = _build_two_bus_net()
    with pytest.raises(ValueError, match="Unknown fault"):
        find_max_rho_scaling(net, "no_such_fault", ["b0"], u_max=10.0)


def test_find_max_rho_scaling_rejects_invalid_bracket():
    net = _build_two_bus_net()
    with pytest.raises(ValueError, match="c_bounds"):
        find_max_rho_scaling(
            net, "flt", ["b0"], u_max=10.0, c_bounds=(2.0, 1.0),
        )


def test_find_max_rho_scaling_unreachable_u_max():
    """If the EPR at the lower bracket bound already exceeds ``u_max``,
    the function must raise ``ValueError`` instead of returning a bogus
    scaling factor."""
    net = _build_two_bus_net(rho0=100.0)
    with pytest.raises(ValueError, match="below the EPR"):
        find_max_rho_scaling(
            net, "flt", ["b0", "b1"],
            u_max=1e-9, c_bounds=(0.1, 10.0),
        )


# ---------------------------------------------------------------------------
# Restoration of the original network state
# ---------------------------------------------------------------------------


def test_find_max_rho_scaling_restores_rho_after_success():
    """After a successful bisection the buses must carry their original
    ``specific_earth_resistance`` again."""
    net = _build_two_bus_net(rho0=137.0)
    rho0_b0 = net.buses["b0"].specific_earth_resistance
    rho0_b1 = net.buses["b1"].specific_earth_resistance

    epr0 = _baseline_epr(net)
    find_max_rho_scaling(net, "flt", ["b0", "b1"], u_max=0.5 * epr0)

    assert net.buses["b0"].specific_earth_resistance == pytest.approx(rho0_b0)
    assert net.buses["b1"].specific_earth_resistance == pytest.approx(rho0_b1)


def test_find_max_rho_scaling_restores_rho_after_failure():
    """Even when the bisection raises (e.g. constraint not satisfiable),
    the original ``rho`` of every selected bus must be restored."""
    net = _build_two_bus_net(rho0=100.0)

    with pytest.raises(ValueError):
        find_max_rho_scaling(
            net, "flt", ["b0", "b1"],
            u_max=1e-9, c_bounds=(0.1, 10.0),
        )

    assert net.buses["b0"].specific_earth_resistance == pytest.approx(100.0)
    assert net.buses["b1"].specific_earth_resistance == pytest.approx(100.0)

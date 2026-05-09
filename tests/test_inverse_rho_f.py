# tests/test_inverse_rho_f.py

"""
Tests for the rho-f model inversion routines in
:mod:`groundinsight.analysis.inverse_rho_f`.

The bus grounding impedance is parameterised in the canonical linear
form ``Z(rho, f) = k1*rho + (k2+jk3)*f + (k4+jk5)*rho*f``. These tests
cover the foundation helper :func:`evaluate_max_epr_under_k`, the 1-D
scaling solver :func:`find_max_rho_f_scaling`, the bus sweep that
treats each selected bus as the active fault one by one, and the
guarantee that the network state (bus impedances, faults, paths,
``active_fault``) is fully restored after every call.
"""

import polars as pl
import pytest

import groundinsight as gi
from groundinsight.analysis import (
    evaluate_max_epr_under_k,
    find_max_rho_f_scaling,
    select_rho_f_from_catalog,
)
from groundinsight.models.core_models import BusType, BranchType


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


def _bus_type():
    """Baseline bus impedance shape; will be overwritten by the helper."""
    return BusType(
        name="LinRhoBus",
        description="Z = 0.01 * rho baseline (overwritten in the test)",
        system_type="Grounded",
        voltage_level=20.0,
        impedance_formula="rho * 0.01 + 0*f",
    )


def _ms_cable_branch_type():
    return BranchType(
        name="MSCable",
        description="MV cable with full mutual coupling",
        grounding_conductor=True,
        self_impedance_formula="(0.25 + I*0.6)*l",
        mutual_impedance_formula="(0.0 + I*0.6)*l",
    )


def _build_three_bus_line(rho0: float = 100.0):
    """Three-bus line b0-b1-b2 with a source at b0."""
    bt = _bus_type()
    brt = _ms_cable_branch_type()
    net = gi.create_network(name="InvKNet", frequencies=[50])
    for name in ("b0", "b1", "b2"):
        gi.create_bus(
            name=name, type=bt, network=net, specific_earth_resistance=rho0
        )
    gi.create_branch(
        name="b01", type=brt, from_bus="b0", to_bus="b1",
        length=1.0, network=net,
    )
    gi.create_branch(
        name="b12", type=brt, from_bus="b1", to_bus="b2",
        length=1.0, network=net,
    )
    gi.create_source(name="src", bus="b0", values={50: 100.0}, network=net)
    return net


# ---------------------------------------------------------------------------
# evaluate_max_epr_under_k
# ---------------------------------------------------------------------------


def test_evaluate_max_epr_under_k_matches_baseline():
    """``k_ref = (0.01, 0, 0, 0, 0)`` reproduces the existing
    ``Z = 0.01 * rho`` shape of the bus impedance, so the helper must
    return the same EPR as a manually configured fault."""
    net = _build_three_bus_line()

    # Manual baseline at b2 with the original BusType formula.
    gi.create_fault(name="manual", bus="b2", scalings={50: 1.0}, network=net)
    gi.run_fault(net, fault_name="manual")
    rb = next(
        rb for rb in net.results["manual"].buses if rb.name == "b2"
    )
    epr_manual = rb.uepr

    # Drop the manual fault before the helper sweeps to avoid mixing.
    del net.faults["manual"]
    del net.results["manual"]
    net.paths.clear()

    eprs = evaluate_max_epr_under_k(
        net, ["b0", "b1", "b2"], k=(0.01, 0.0, 0.0, 0.0, 0.0),
    )

    assert eprs["b2"] == pytest.approx(epr_manual, rel=1e-9)
    # Source==Fault at b0 is degenerate -> EPR == 0.
    assert eprs["b0"] == pytest.approx(0.0, abs=1e-9)
    # The helper must have left the network in its original shape.
    assert net.faults == {}
    assert net.paths == {}


def test_evaluate_max_epr_under_k_restores_state_after_failure():
    """If ``run_fault`` raises mid-sweep, bus impedances, faults and
    paths must still be restored. We trigger an error by passing an
    unknown bus name."""
    net = _build_three_bus_line()
    impedance_b0 = dict(net.buses["b0"].impedance)

    with pytest.raises(ValueError, match="Unknown bus"):
        evaluate_max_epr_under_k(
            net, ["b0", "does_not_exist"], k=(0.01, 0.0, 0.0, 0.0, 0.0),
        )

    assert net.buses["b0"].impedance == impedance_b0
    assert net.faults == {}
    assert net.paths == {}


def test_evaluate_max_epr_under_k_validates_k_length():
    net = _build_three_bus_line()
    with pytest.raises(ValueError, match="5-tuple"):
        evaluate_max_epr_under_k(net, ["b0"], k=(0.01, 0.0, 0.0))


def test_evaluate_max_epr_under_k_rejects_empty_bus_list():
    net = _build_three_bus_line()
    with pytest.raises(ValueError, match="bus_names"):
        evaluate_max_epr_under_k(net, [], k=(0.01, 0.0, 0.0, 0.0, 0.0))


def test_evaluate_max_epr_under_k_reuses_existing_fault():
    """If a fault already exists at a swept bus, the helper must reuse it
    instead of creating a temporary one."""
    net = _build_three_bus_line()
    gi.create_fault(name="user_b2", bus="b2", scalings={50: 1.0}, network=net)

    eprs = evaluate_max_epr_under_k(
        net, ["b1", "b2"], k=(0.01, 0.0, 0.0, 0.0, 0.0),
    )

    # The user-defined fault at b2 must still exist after the call.
    assert "user_b2" in net.faults
    assert eprs["b2"] > 0.0
    # No leftover temporary faults.
    assert all(name == "user_b2" for name in net.faults)


# ---------------------------------------------------------------------------
# find_max_rho_f_scaling
# ---------------------------------------------------------------------------


def test_find_max_rho_f_scaling_anchored_at_unity():
    """Setting ``u_limit`` to the max EPR at ``c = 1`` must yield
    ``c_max ~= 1``. The k_ref vector reproduces the original
    ``Z = 0.01 * rho`` baseline."""
    net = _build_three_bus_line()
    k_ref = (0.01, 0.0, 0.0, 0.0, 0.0)

    epr_baseline = max(
        evaluate_max_epr_under_k(net, ["b0", "b1", "b2"], k_ref).values()
    )
    res = find_max_rho_f_scaling(
        net, ["b0", "b1", "b2"], u_limit=epr_baseline,
        k_ref=k_ref, tol_rel=1e-5,
    )

    assert res["c_max"] == pytest.approx(1.0, rel=2e-5)
    assert res["k_max"][0] == pytest.approx(0.01, rel=2e-5)
    assert res["max_epr_rms_at_c_max"] == pytest.approx(epr_baseline, rel=2e-5)
    assert res["epr_rms_per_bus_at_c_max"]["b2"] == pytest.approx(
        epr_baseline, rel=2e-5
    )
    assert 1 <= res["iterations"] <= 60


def test_find_max_rho_f_scaling_halved_u_limit_gives_smaller_c():
    net = _build_three_bus_line()
    k_ref = (0.01, 0.0, 0.0, 0.0, 0.0)
    epr_baseline = max(
        evaluate_max_epr_under_k(net, ["b0", "b1", "b2"], k_ref).values()
    )

    res = find_max_rho_f_scaling(
        net, ["b0", "b1", "b2"], u_limit=0.5 * epr_baseline,
        k_ref=k_ref, tol_rel=1e-4,
    )
    assert 0 < res["c_max"] < 1.0
    assert res["max_epr_rms_at_c_max"] <= 0.5 * epr_baseline * (1 + 1e-3)


def test_find_max_rho_f_scaling_rejects_zero_k_ref():
    net = _build_three_bus_line()
    with pytest.raises(ValueError, match="zero vector"):
        find_max_rho_f_scaling(
            net, ["b0", "b1", "b2"], u_limit=10.0,
            k_ref=(0.0, 0.0, 0.0, 0.0, 0.0),
        )


def test_find_max_rho_f_scaling_rejects_wrong_k_length():
    net = _build_three_bus_line()
    with pytest.raises(ValueError, match="5-tuple"):
        find_max_rho_f_scaling(
            net, ["b0"], u_limit=10.0, k_ref=(0.01, 0.0, 0.0),
        )


def test_find_max_rho_f_scaling_rejects_non_positive_u_limit():
    net = _build_three_bus_line()
    with pytest.raises(ValueError, match="u_limit"):
        find_max_rho_f_scaling(
            net, ["b0"], u_limit=0.0, k_ref=(0.01, 0.0, 0.0, 0.0, 0.0),
        )


def test_find_max_rho_f_scaling_unreachable_u_limit():
    net = _build_three_bus_line()
    k_ref = (0.01, 0.0, 0.0, 0.0, 0.0)
    with pytest.raises(ValueError, match="below the maximum EPR"):
        find_max_rho_f_scaling(
            net, ["b0", "b1", "b2"],
            u_limit=1e-9, k_ref=k_ref, c_bounds=(0.1, 10.0),
        )


def test_find_max_rho_f_scaling_admissible_returns_upper_bound():
    """If the EPR at the upper bracket bound is still below ``u_limit``,
    the function returns the upper bound and zero iterations."""
    net = _build_three_bus_line()
    k_ref = (0.01, 0.0, 0.0, 0.0, 0.0)
    epr_baseline = max(
        evaluate_max_epr_under_k(net, ["b0", "b1", "b2"], k_ref).values()
    )

    res = find_max_rho_f_scaling(
        net, ["b0", "b1", "b2"],
        u_limit=1e6 * epr_baseline,
        k_ref=k_ref, c_bounds=(0.1, 10.0),
    )

    assert res["c_max"] == pytest.approx(10.0, rel=1e-12)
    assert res["iterations"] == 0


def test_find_max_rho_f_scaling_restores_state_after_success():
    net = _build_three_bus_line(rho0=137.0)
    impedance_backup = {
        b: dict(net.buses[b].impedance) for b in ("b0", "b1", "b2")
    }
    k_ref = (0.01, 0.0, 0.0, 0.0, 0.0)
    epr_baseline = max(
        evaluate_max_epr_under_k(net, ["b0", "b1", "b2"], k_ref).values()
    )

    find_max_rho_f_scaling(
        net, ["b0", "b1", "b2"], u_limit=0.5 * epr_baseline, k_ref=k_ref,
    )

    for b in ("b0", "b1", "b2"):
        assert net.buses[b].impedance == impedance_backup[b]
    assert net.faults == {}


# ---------------------------------------------------------------------------
# select_rho_f_from_catalog
# ---------------------------------------------------------------------------


def test_select_rho_f_from_catalog_marks_admissible_correctly():
    """The ``admissible`` column must be ``True`` exactly when the
    candidate's max RMS EPR stays at or below ``u_limit``."""
    net = _build_three_bus_line()
    catalog = {
        "very_low": (0.001, 0.0, 0.0, 0.0, 0.0),
        "low":      (0.005, 0.0, 0.0, 0.0, 0.0),
        "medium":   (0.01,  0.0, 0.0, 0.0, 0.0),
        "high":     (0.05,  0.0, 0.0, 0.0, 0.0),
    }

    df = select_rho_f_from_catalog(
        net, ["b1", "b2"], u_limit=15.0, candidates=catalog,
    )

    assert df.shape[0] == 4
    expected_columns = {
        "name", "k1", "k2", "k3", "k4", "k5",
        "max_epr_rms_V", "admissible", "epr_b1_V", "epr_b2_V",
    }
    assert set(df.columns) == expected_columns

    # The admissible column must be a strict <= comparison against u_limit.
    for row in df.iter_rows(named=True):
        assert row["admissible"] == (row["max_epr_rms_V"] <= 15.0)


def test_select_rho_f_from_catalog_sort_max_epr_asc_puts_admissible_first():
    """Default sort: admissible candidates first, ascending EPR within."""
    net = _build_three_bus_line()
    catalog = {
        "very_low": (0.001, 0.0, 0.0, 0.0, 0.0),
        "high":     (0.05,  0.0, 0.0, 0.0, 0.0),
        "low":      (0.005, 0.0, 0.0, 0.0, 0.0),
    }

    df = select_rho_f_from_catalog(
        net, ["b1", "b2"], u_limit=10.0, candidates=catalog,
    )

    names = df["name"].to_list()
    admissibles = df["admissible"].to_list()
    # All True before any False.
    assert sorted(admissibles, reverse=True) == admissibles, names
    # Within the admissible block, EPR ascending.
    epr_admissible = [
        row["max_epr_rms_V"] for row in df.iter_rows(named=True)
        if row["admissible"]
    ]
    assert epr_admissible == sorted(epr_admissible)


def test_select_rho_f_from_catalog_sort_by_name():
    net = _build_three_bus_line()
    catalog = {
        "zebra":  (0.005, 0.0, 0.0, 0.0, 0.0),
        "alpha":  (0.005, 0.0, 0.0, 0.0, 0.0),
        "middle": (0.005, 0.0, 0.0, 0.0, 0.0),
    }
    df = select_rho_f_from_catalog(
        net, ["b1", "b2"], u_limit=1e6, candidates=catalog,
        sort_by="name",
    )
    assert df["name"].to_list() == ["alpha", "middle", "zebra"]


def test_select_rho_f_from_catalog_empty_catalog_returns_empty_df():
    net = _build_three_bus_line()
    df = select_rho_f_from_catalog(
        net, ["b1", "b2"], u_limit=10.0, candidates={},
    )
    assert df.shape[0] == 0
    expected_columns = {
        "name", "k1", "k2", "k3", "k4", "k5",
        "max_epr_rms_V", "admissible", "epr_b1_V", "epr_b2_V",
    }
    assert set(df.columns) == expected_columns


def test_select_rho_f_from_catalog_rejects_wrong_k_length():
    net = _build_three_bus_line()
    with pytest.raises(ValueError, match="5-tuple"):
        select_rho_f_from_catalog(
            net, ["b1", "b2"], u_limit=10.0,
            candidates={"bad": (0.01, 0.0, 0.0)},
        )


def test_select_rho_f_from_catalog_restores_state():
    """After a catalog scan, bus impedances and faults must be restored."""
    net = _build_three_bus_line()
    impedance_b1_before = dict(net.buses["b1"].impedance)

    catalog = {
        "a": (0.005, 0.0, 0.0, 0.0, 0.0),
        "b": (0.05, 0.0, 0.0, 0.0, 0.0),
    }
    select_rho_f_from_catalog(
        net, ["b1", "b2"], u_limit=10.0, candidates=catalog,
    )

    assert net.buses["b1"].impedance == impedance_b1_before
    assert net.faults == {}
    assert net.paths == {}


def test_find_max_rho_f_scaling_with_complex_k_ref():
    """A non-trivial k_ref with frequency- and rho-frequency-coupled terms
    must still yield a strictly positive c_max with the right monotonic
    behaviour against u_limit."""
    net = gi.create_network(name="MultiF", frequencies=[50, 250])
    bt = _bus_type()
    brt = _ms_cable_branch_type()
    for name in ("b0", "b1", "b2"):
        gi.create_bus(name=name, type=bt, network=net, specific_earth_resistance=100.0)
    gi.create_branch(name="b01", type=brt, from_bus="b0", to_bus="b1", length=1.0, network=net)
    gi.create_branch(name="b12", type=brt, from_bus="b1", to_bus="b2", length=1.0, network=net)
    gi.create_source(name="src", bus="b0", values={50: 100.0, 250: 50.0}, network=net)

    # k_ref with mixed real/imag and rho*f terms.
    k_ref = (0.005, 1e-4, 5e-4, 1e-6, 5e-6)
    bus_names = ["b1", "b2"]

    epr_at_unity = max(evaluate_max_epr_under_k(net, bus_names, k_ref).values())
    assert epr_at_unity > 0.0

    res_loose = find_max_rho_f_scaling(
        net, bus_names, u_limit=2.0 * epr_at_unity, k_ref=k_ref,
    )
    res_tight = find_max_rho_f_scaling(
        net, bus_names, u_limit=0.5 * epr_at_unity, k_ref=k_ref,
    )
    assert res_tight["c_max"] < res_loose["c_max"]

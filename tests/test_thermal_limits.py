# tests/test_thermal_limits.py

"""
Tests for the conductor thermal-limit check (F1).

Covers the IEC 60949 material constant ``k``, the IEC 60909-0 DC heat factor
``m`` and the ``check_conductor_limits`` post-processing that compares the
thermally equivalent short-time current ``I_th = I_s_rms * sqrt(m + n)`` of
each grounding branch against its admissible adiabatic current
``I_adm = k * S / sqrt(t_k)``. Also pins the round-trip of the new
``BranchType`` thermal fields through JSON and SQLite.
"""

from __future__ import annotations

import math

import pytest

import groundinsight as gi
from groundinsight.analysis.thermal import (
    admissible_short_circuit_current,
    iec60909_m,
    iec60949_k,
    kappa_from_r_to_x,
)


# ---------------------------------------------------------------------------
# IEC 60949 material constant k
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "material, theta_i, theta_f, expected",
    [
        ("Cu", 90.0, 250.0, 143.0),   # copper XLPE
        ("Cu", 70.0, 160.0, 115.0),   # copper PVC
        ("Al", 90.0, 250.0, 94.0),    # aluminium XLPE
    ],
)
def test_iec60949_k_reproduces_standard_tables(material, theta_i, theta_f, expected):
    assert iec60949_k(material, theta_i, theta_f) == pytest.approx(expected, abs=0.6)


def test_iec60949_k_rejects_unknown_material():
    with pytest.raises(ValueError):
        iec60949_k("Gold", 20.0, 300.0)


def test_iec60949_k_rejects_final_below_initial():
    with pytest.raises(ValueError):
        iec60949_k("Cu", theta_initial_C=300.0, theta_final_C=200.0)


def test_iec60949_k_uses_material_default_final_temperature():
    # Cu default theta_f = 300 C
    assert iec60949_k("Cu", 20.0) == pytest.approx(iec60949_k("Cu", 20.0, 300.0))


# ---------------------------------------------------------------------------
# kappa and IEC 60909-0 m factor
# ---------------------------------------------------------------------------


def test_kappa_from_r_to_x_bounds():
    assert kappa_from_r_to_x(0.0) == pytest.approx(2.0)
    assert kappa_from_r_to_x(10.0) == pytest.approx(1.02, abs=1e-3)
    with pytest.raises(ValueError):
        kappa_from_r_to_x(-0.1)


def test_iec60909_m_limits():
    assert iec60909_m(1.0, 50.0, 0.5) == 0.0            # no DC offset
    assert iec60909_m(2.0, 50.0, 0.5) == pytest.approx(2.0)  # non-decaying DC


def test_iec60909_m_sample_values():
    # short faults carry more DC heat than long ones
    m_long = iec60909_m(1.8, 50.0, 0.5)
    m_short = iec60909_m(1.8, 50.0, 0.05)
    assert m_long == pytest.approx(0.0896, abs=5e-3)
    assert m_short == pytest.approx(0.800, abs=1e-2)
    assert m_short > m_long


def test_iec60909_m_rejects_nonpositive_time():
    with pytest.raises(ValueError):
        iec60909_m(1.8, 50.0, 0.0)


def test_admissible_current_formula():
    k = iec60949_k("Cu", 20.0, 300.0)
    assert admissible_short_circuit_current(k, 50.0, 1.0) == pytest.approx(k * 50.0)
    assert admissible_short_circuit_current(k, 50.0, 4.0) == pytest.approx(k * 50.0 / 2.0)
    with pytest.raises(ValueError):
        admissible_short_circuit_current(k, -1.0, 1.0)


# ---------------------------------------------------------------------------
# End-to-end check_conductor_limits
# ---------------------------------------------------------------------------


def _thermal_network(cross_section_mm2):
    net = gi.create_network(name="thermal", frequencies=[50.0])
    bt = gi.BusType(name="b", system_type="s", voltage_level=20.0,
                    impedance_formula="rho*0 + 0.5")
    ct = gi.BranchType(
        name="c", grounding_conductor=True,
        self_impedance_formula="(rho*0 + 0.25 + j*f*0.012)*l",
        mutual_impedance_formula="(rho*0 + 0.05 + j*f*0.012)*l",
        conductor_material="Cu", cross_section_mm2=cross_section_mm2,
        theta_initial_C=20.0, theta_final_C=300.0,
    )
    for b in ("b1", "b2"):
        gi.create_bus(name=b, type=bt, network=net)
    gi.create_branch(name="c1", type=ct, from_bus="b1", to_bus="b2", length=1.0, network=net)
    gi.create_source(name="src", bus="b1", values={50.0: 5000.0}, network=net)
    gi.create_fault(name="F", bus="b2", scalings={50.0: 1.0}, network=net)
    gi.run_fault(net, "F")
    return net


def test_check_conductor_limits_applies_iec_formulas():
    net = _thermal_network(50.0)
    df = gi.check_conductor_limits(net, "F", t_k=1.0, r_to_x=0.1)
    row = df.filter(df["branch_name"] == "c1").to_dicts()[0]

    kappa = kappa_from_r_to_x(0.1)
    m = iec60909_m(kappa, 50.0, 1.0)
    factor = math.sqrt(m + 1.0)
    k = iec60949_k("Cu", 20.0, 300.0)

    assert row["k"] == pytest.approx(k)
    assert row["I_admissible_A"] == pytest.approx(k * 50.0 / math.sqrt(1.0))
    assert row["I_th_A"] == pytest.approx(row["I_s_rms_A"] * factor)
    assert row["within_limit"] is True


def test_check_conductor_limits_flags_undersized_and_passes_oversized():
    small = gi.check_conductor_limits(_thermal_network(1.0), "F", t_k=1.0, r_to_x=0.1)
    big = gi.check_conductor_limits(_thermal_network(500.0), "F", t_k=1.0, r_to_x=0.1)
    assert small.filter(small["branch_name"] == "c1")["within_limit"][0] is False
    assert big.filter(big["branch_name"] == "c1")["within_limit"][0] is True


def test_check_conductor_limits_branch_without_params_not_checked():
    net = gi.create_network(name="no_param", frequencies=[50.0])
    bt = gi.BusType(name="b", system_type="s", voltage_level=20.0,
                    impedance_formula="rho*0 + 0.5")
    ct = gi.BranchType(name="c", grounding_conductor=True,
                       self_impedance_formula="(rho*0 + 0.25 + j*f*0.012)*l",
                       mutual_impedance_formula="(rho*0 + 0.05 + j*f*0.012)*l")
    for b in ("b1", "b2"):
        gi.create_bus(name=b, type=bt, network=net)
    gi.create_branch(name="c1", type=ct, from_bus="b1", to_bus="b2", length=1.0, network=net)
    gi.create_source(name="src", bus="b1", values={50.0: 5000.0}, network=net)
    gi.create_fault(name="F", bus="b2", scalings={50.0: 1.0}, network=net)
    gi.run_fault(net, "F")

    df = gi.check_conductor_limits(net, "F", t_k=1.0, r_to_x=0.1)
    row = df.filter(df["branch_name"] == "c1").to_dicts()[0]
    assert row["within_limit"] is None       # not checked
    assert row["I_th_A"] > 0                  # I_th still reported


def test_check_conductor_limits_input_validation():
    net = _thermal_network(50.0)
    with pytest.raises(ValueError):
        gi.check_conductor_limits(net, "F", t_k=0.0, r_to_x=0.1)     # bad t_k
    with pytest.raises(ValueError):
        gi.check_conductor_limits(net, "F", t_k=1.0)                 # neither kappa nor r_to_x
    with pytest.raises(ValueError):
        gi.check_conductor_limits(net, "F", t_k=1.0, kappa=1.8, r_to_x=0.1)  # both
    with pytest.raises(ValueError):
        gi.check_conductor_limits(net, "nope", t_k=1.0, r_to_x=0.1)  # no results


# ---------------------------------------------------------------------------
# BranchType validation + persistence of the new fields
# ---------------------------------------------------------------------------


def test_branchtype_rejects_nonpositive_cross_section():
    with pytest.raises(ValueError):
        gi.BranchType(name="c", grounding_conductor=True,
                      self_impedance_formula="(0.25 + j*f*0.012)*l",
                      mutual_impedance_formula="(0.0 + j*f*0.012)*l",
                      cross_section_mm2=0.0)


def test_thermal_fields_survive_json_roundtrip():
    net = _thermal_network(50.0)
    reloaded = gi.Network.model_validate_json(net.model_dump_json())
    bt = reloaded.branches["c1"].type
    assert bt.conductor_material == "Cu"
    assert bt.cross_section_mm2 == 50.0
    assert bt.theta_initial_C == 20.0
    assert bt.theta_final_C == 300.0


def test_thermal_fields_survive_sqlite_roundtrip(tmp_path):
    from groundinsight.database.crud import save_network, load_network

    net = _thermal_network(50.0)
    gi.start_dbsession(str(tmp_path / "thermal.db"))
    try:
        save_network(net, gi.session, overwrite=True)
        loaded = load_network(net.name, gi.session)
    finally:
        gi.close_dbsession()

    bt = loaded.branches["c1"].type
    assert bt.conductor_material == "Cu"
    assert bt.cross_section_mm2 == 50.0
    assert bt.theta_final_C == 300.0

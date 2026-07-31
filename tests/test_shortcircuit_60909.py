# tests/test_shortcircuit_60909.py

"""
Tests for the IEC 60909 short-circuit characteristics (F2/F3).

Pins the five proofs that motivated the design, the current-weighted
aggregation of ``kappa`` across several feeding sources, the persistence of
the new ``Source`` / ``Fault`` fields, and the end-to-end path
pandapower ``calc_sc`` -> ``apply_shortcircuit_characteristics`` ->
``check_conductor_limits``.

The proofs, in short:

P1  ``calc_sc(fault="1ph")`` leaves ``ip_ka`` / ``ith_ka`` entirely ``NaN``,
    so groundinsight must derive them itself.
P2  the single line-to-earth loop is ``2*Z1 + Z0``, which fixes the ``R/X``
    that drives ``kappa``.
P3  our ``kappa`` / ``i_p`` / ``I_th`` reproduce pandapower's independent
    implementation on the 3ph case, where it does compute them.
P4  pandapower's DC heat factor is inverted near ``kappa = 2`` (it returns
    ``0`` where the analytic limit is ``2``), which under-estimates ``I_th``.
P5  superposing the linear RMS currents and then applying a
    current-weighted ``kappa`` reproduces the sum of the individual peaks
    exactly, whereas reusing a single source's ``kappa`` does not.
"""

from __future__ import annotations

import math

import pytest

import groundinsight as gi
from groundinsight.analysis.shortcircuit import (
    iec60909_m,
    kappa_from_r_to_x,
    peak_short_circuit_current,
    resolve_fault_sc_characteristics,
    thermal_equivalent_current,
)


C_MAX = 1.1


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _pp_net(rx=0.1, x0x=1.0, r0x0=0.1):
    """A 110/20 kV feeder with complete zero-sequence data."""
    pp = pytest.importorskip("pandapower")
    net = pp.create_empty_network(sn_mva=100.0)
    b1 = pp.create_bus(net, vn_kv=110.0, name="HV")
    b2 = pp.create_bus(net, vn_kv=20.0, name="MV1")
    b3 = pp.create_bus(net, vn_kv=20.0, name="MV2")
    pp.create_ext_grid(
        net, b1, s_sc_max_mva=1000.0, rx_max=rx, s_sc_min_mva=800.0, rx_min=rx,
        x0x_max=x0x, r0x0_max=r0x0, x0x_min=x0x, r0x0_min=r0x0,
    )
    pp.create_transformer_from_parameters(
        net, b1, b2, sn_mva=40, vn_hv_kv=110, vn_lv_kv=20, vkr_percent=0.5,
        vk_percent=12, pfe_kw=0, i0_percent=0, vector_group="Dyn",
        vk0_percent=12, vkr0_percent=0.5, mag0_percent=100, mag0_rx=0.0,
        si0_hv_partial=0.9,
    )
    pp.create_line_from_parameters(
        net, b2, b3, length_km=5.0, r_ohm_per_km=0.2, x_ohm_per_km=0.4,
        c_nf_per_km=0.0, max_i_ka=0.5, r0_ohm_per_km=0.6, x0_ohm_per_km=1.6,
        c0_nf_per_km=0.0,
    )
    return net


def _solved(fault="1ph", tk_s=0.5, **kwargs):
    sc = pytest.importorskip("pandapower.shortcircuit")
    net = _pp_net(**kwargs)
    sc.calc_sc(net, fault=fault, case="max", ip=True, ith=True, tk_s=tk_s,
               topology="radial", kappa_method="C")
    return net


def _gi_net(cross_section_mm2=50.0, buses=("HV", "MV1", "MV2")):
    """A minimal grounding chain whose bus names match the pandapower ones."""
    net = gi.create_network(name="sc", frequencies=[50.0])
    bt = gi.BusType(name="b", system_type="s", voltage_level=20.0,
                    impedance_formula="rho*0 + 0.5")
    ct = gi.BranchType(
        name="c", grounding_conductor=True,
        self_impedance_formula="(rho*0 + 0.25 + j*f*0.012)*l",
        mutual_impedance_formula="(rho*0 + 0.05 + j*f*0.012)*l",
        conductor_material="Cu", cross_section_mm2=cross_section_mm2,
        theta_initial_C=20.0, theta_final_C=300.0,
    )
    for name in buses:
        gi.create_bus(name=name, type=bt, network=net)
    for a, b in zip(buses, buses[1:]):
        gi.create_branch(name=f"{a}-{b}", type=ct, from_bus=a, to_bus=b,
                         length=1.0, network=net)
    gi.create_source(name="src", bus=buses[0], values={50.0: 1000.0}, network=net)
    gi.create_fault(name="F", bus=buses[-1], scalings={50.0: 1.0}, network=net)
    return net


# ---------------------------------------------------------------------------
# P1 / P2 — what pandapower does and does not deliver for earth faults
# ---------------------------------------------------------------------------


def test_p1_pandapower_leaves_ip_and_ith_nan_for_single_phase_faults():
    """The gap that makes this whole module necessary."""
    res = _solved(fault="1ph").res_bus_sc
    assert res["ip_ka"].isna().all()
    assert res["ith_ka"].isna().all()


def test_p1_read_shortcircuit_results_fills_the_gap():
    df = gi.read_shortcircuit_results(_solved(fault="1ph", tk_s=0.5))
    assert df.height == 3
    assert df["i_p_a"].null_count() == 0
    assert df["i_th_a"].null_count() == 0
    assert df["kappa"].null_count() == 0
    # No pandapower ip for 1ph, so every kappa comes from the closed form.
    assert set(df["kappa_origin"].to_list()) == {"iec_closed_form"}
    # I_k'' is reported in amperes, not kiloamperes.
    assert df["i_k_a"].min() > 1000.0


def test_p2_single_phase_loop_is_two_z1_plus_z0():
    """``I_k1'' = sqrt(3)*c*U_n/|2*Z1+Z0|`` reproduces pandapower exactly."""
    net = _solved(fault="1ph")
    worst = 0.0
    for idx, row in net.res_bus_sc.iterrows():
        u_n = net.bus.at[idx, "vn_kv"] * 1e3
        z1 = complex(row.rk_ohm, row.xk_ohm)
        z0 = complex(row.rk0_ohm, row.xk0_ohm)
        i_k = math.sqrt(3) * C_MAX * u_n / abs(2 * z1 + z0) / 1000.0
        worst = max(worst, abs(i_k - row.ikss_ka) / row.ikss_ka)
    assert worst < 1e-12


def test_p2_reported_r_to_x_uses_the_earth_fault_loop():
    net = _solved(fault="1ph")
    df = gi.read_shortcircuit_results(net)
    differs_somewhere = False
    for row in df.to_dicts():
        idx = row["pp_bus_index"]
        pp_row = net.res_bus_sc.loc[idx]
        expected = (2 * pp_row.rk_ohm + pp_row.rk0_ohm) / (
            2 * pp_row.xk_ohm + pp_row.xk0_ohm
        )
        assert row["r_to_x"] == pytest.approx(expected, rel=1e-12)
        naive = pp_row.rk_ohm / pp_row.xk_ohm
        # Where the zero sequence really differs from the positive one, the
        # loop ratio has to differ from the naive positive-sequence ratio.
        # Directly at the ext_grid the fixture gives Z0 == Z1 (x0x = 1,
        # r0x0 = rx), and there the two ratios coincide legitimately -- that
        # is physics, not a rounding artefact, so it must not be asserted
        # away.
        if pp_row.rk0_ohm != pytest.approx(pp_row.rk_ohm, rel=1e-9) or (
            pp_row.xk0_ohm != pytest.approx(pp_row.xk_ohm, rel=1e-9)
        ):
            assert row["r_to_x"] != pytest.approx(naive, rel=1e-6)
            differs_somewhere = True
        else:
            assert row["r_to_x"] == pytest.approx(naive, rel=1e-9)
    assert differs_somewhere, "fixture no longer exercises a differing zero sequence"


# ---------------------------------------------------------------------------
# P3 — agreement with pandapower where it does compute the quantities
# ---------------------------------------------------------------------------


def test_p3_matches_pandapower_on_three_phase_faults():
    net = _solved(fault="3ph", tk_s=0.5)
    for _, row in net.res_bus_sc.iterrows():
        kappa = kappa_from_r_to_x(row.rk_ohm / row.xk_ohm)
        i_k_a = row.ikss_ka * 1000.0
        i_p = peak_short_circuit_current(i_k_a, kappa)
        m = iec60909_m(kappa, 50.0, 0.5)
        i_th = thermal_equivalent_current(i_k_a, m, 1.0)
        assert i_p == pytest.approx(row.ip_ka * 1000.0, rel=1e-9)
        assert i_th == pytest.approx(row.ith_ka * 1000.0, rel=1e-9)


def test_p3_kappa_is_taken_from_pandapower_when_available():
    """For 3ph the topology-aware ip_ka is preferred over the closed form."""
    df = gi.read_shortcircuit_results(_solved(fault="3ph", tk_s=0.5))
    assert set(df["kappa_origin"].to_list()) == {"pandapower"}
    assert df["kappa"].min() > 1.0
    assert df["kappa"].max() <= 2.0


# ---------------------------------------------------------------------------
# P4 — the DC heat factor must not collapse to zero near kappa = 2
# ---------------------------------------------------------------------------


def test_p4_m_is_continuous_and_tends_to_two():
    """pandapower zeroes m for kappa > 1.99; the analytic limit is 2."""
    f, t_k = 50.0, 1.0
    m_values = [iec60909_m(k, f, t_k) for k in (1.99, 1.995, 1.999, 2.0)]
    assert all(b >= a for a, b in zip(m_values, m_values[1:])), m_values
    assert m_values[-1] == pytest.approx(2.0)
    # No collapse: the value just below the limit stays close to it.
    assert iec60909_m(1.999, f, t_k) > 1.0


def test_p4_our_i_th_is_the_conservative_one():
    """A near-zero-resistance fault must not lose its DC heat contribution."""
    i_k = 10_000.0
    ours = thermal_equivalent_current(i_k, iec60909_m(1.995, 50.0, 1.0), 1.0)
    pandapower_style = thermal_equivalent_current(i_k, 0.0, 1.0)  # m forced to 0
    assert ours > pandapower_style


# ---------------------------------------------------------------------------
# P5 — the superposition rule
# ---------------------------------------------------------------------------


def _two_source_network(rx_a=0.02, rx_b=0.60, i_a=800.0, i_b=400.0):
    net = gi.create_network(name="agg", frequencies=[50.0])
    bt = gi.BusType(name="b", system_type="s", voltage_level=20.0,
                    impedance_formula="rho*0 + 0.5")
    ct = gi.BranchType(name="c", grounding_conductor=True,
                       self_impedance_formula="(rho*0 + 0.25 + j*f*0.012)*l",
                       mutual_impedance_formula="(rho*0 + 0.05 + j*f*0.012)*l")
    for name in ("b1", "b2"):
        gi.create_bus(name=name, type=bt, network=net)
    gi.create_branch(name="c1", type=ct, from_bus="b1", to_bus="b2",
                     length=1.0, network=net)
    gi.create_source(name="a", bus="b1", values={50.0: i_a}, network=net, r_to_x=rx_a)
    gi.create_source(name="b", bus="b2", values={50.0: i_b}, network=net, r_to_x=rx_b)
    gi.create_fault(name="F", bus="b2", scalings={50.0: 1.0}, network=net, t_k_s=0.5)
    gi.run_fault(net, "F")
    return net


def test_p5_weighted_kappa_reproduces_the_sum_of_individual_peaks():
    i_a, rx_a = 800.0, 0.02
    i_b, rx_b = 400.0, 0.60
    net = _two_source_network(rx_a, rx_b, i_a, i_b)

    k_a, k_b = kappa_from_r_to_x(rx_a), kappa_from_r_to_x(rx_b)
    peak_sum = math.sqrt(2) * (k_a * i_a + k_b * i_b)

    data = resolve_fault_sc_characteristics(net, "F")
    assert data.i_k_a == pytest.approx(i_a + i_b)
    assert data.i_p_a == pytest.approx(peak_sum, rel=1e-12)
    assert data.homogeneous is False
    assert data.sources_without_kappa == []


def test_p5_max_aggregation_is_conservative():
    net = _two_source_network()
    weighted = resolve_fault_sc_characteristics(net, "F", aggregation="weighted")
    worst = resolve_fault_sc_characteristics(net, "F", aggregation="max")
    assert worst.kappa > weighted.kappa
    assert worst.i_p_a > weighted.i_p_a
    assert worst.kappa == pytest.approx(kappa_from_r_to_x(0.02))


def test_p5_homogeneous_sources_keep_the_common_kappa():
    net = _two_source_network(rx_a=0.25, rx_b=0.25)
    data = resolve_fault_sc_characteristics(net, "F")
    assert data.homogeneous is True
    assert data.kappa == pytest.approx(kappa_from_r_to_x(0.25))
    assert data.r_to_x == pytest.approx(0.25)


def test_resolve_reports_sources_without_characteristics():
    net = _two_source_network()
    net.sources["b"].r_to_x = None
    data = resolve_fault_sc_characteristics(net, "F")
    assert data.sources_without_kappa == ["b"]
    assert data.kappa == pytest.approx(kappa_from_r_to_x(0.02))


def test_resolve_without_any_characteristics_returns_none():
    net = _two_source_network()
    for source in net.sources.values():
        source.r_to_x = None
    data = resolve_fault_sc_characteristics(net, "F")
    assert data.kappa is None
    assert data.i_p_a is None


def test_resolve_validates_its_arguments():
    net = _two_source_network()
    with pytest.raises(ValueError):
        resolve_fault_sc_characteristics(net, "nope")
    with pytest.raises(ValueError):
        resolve_fault_sc_characteristics(net, "F", aggregation="mean")


# ---------------------------------------------------------------------------
# primitives
# ---------------------------------------------------------------------------


def test_peak_and_thermal_current_formulas():
    assert peak_short_circuit_current(1000.0, 1.8) == pytest.approx(1.8 * math.sqrt(2) * 1000.0)
    assert thermal_equivalent_current(1000.0, 0.0, 1.0) == pytest.approx(1000.0)
    assert thermal_equivalent_current(1000.0, 3.0, 1.0) == pytest.approx(2000.0)
    with pytest.raises(ValueError):
        peak_short_circuit_current(-1.0, 1.8)
    with pytest.raises(ValueError):
        thermal_equivalent_current(1000.0, -0.1, 1.0)


def test_explicit_kappa_beats_r_to_x_on_a_source():
    net = _two_source_network()
    net.sources["a"].kappa = 1.5
    data = resolve_fault_sc_characteristics(net, "F", aggregation="max")
    # 1.5 is now the largest kappa of the two (b has r_to_x=0.6 -> 1.18)
    assert data.kappa == pytest.approx(1.5)
    # ... and the R/X is no longer unambiguous, so it is not reported.
    assert data.r_to_x is None


# ---------------------------------------------------------------------------
# model fields: validation and persistence
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "kwargs",
    [
        {"i_k_a": 0.0},
        {"i_k_a": -1.0},
        {"r_to_x": -0.1},
        {"kappa": 1.0},
        {"kappa": 2.5},
    ],
)
def test_source_rejects_unphysical_characteristics(kwargs):
    with pytest.raises(ValueError):
        gi.create_source(name="s", bus="b1", values={50.0: 1.0}, **kwargs)


@pytest.mark.parametrize("kwargs", [{"t_k_s": 0.0}, {"t_k_s": -1.0},
                                    {"n_factor": 0.0}, {"n_factor": 1.5}])
def test_fault_rejects_unphysical_characteristics(kwargs):
    with pytest.raises(ValueError):
        gi.create_fault(name="F", bus="b1", scalings={50.0: 1.0}, **kwargs)


def test_source_accepts_the_physical_boundaries():
    source = gi.create_source(name="s", bus="b1", values={50.0: 1.0},
                              i_k_a=1.0, r_to_x=0.0, kappa=2.0)
    assert source.kappa == 2.0
    assert source.r_to_x == 0.0


def _characterised_network():
    net = _gi_net()
    net.sources["src"].i_k_a = 4321.0
    net.sources["src"].r_to_x = 0.12
    net.sources["src"].kappa = 1.75
    net.faults["F"].t_k_s = 0.35
    net.faults["F"].n_factor = 0.9
    return net


def test_characteristics_survive_json_roundtrip():
    reloaded = gi.Network.model_validate_json(_characterised_network().model_dump_json())
    assert reloaded.sources["src"].i_k_a == pytest.approx(4321.0)
    assert reloaded.sources["src"].r_to_x == pytest.approx(0.12)
    assert reloaded.sources["src"].kappa == pytest.approx(1.75)
    assert reloaded.faults["F"].t_k_s == pytest.approx(0.35)
    assert reloaded.faults["F"].n_factor == pytest.approx(0.9)


def test_characteristics_survive_sqlite_roundtrip(tmp_path):
    from groundinsight.database.crud import load_network, save_network

    net = _characterised_network()
    gi.start_dbsession(str(tmp_path / "sc.db"))
    try:
        save_network(net, gi.session, overwrite=True)
        loaded = load_network(net.name, gi.session)
    finally:
        gi.close_dbsession()

    assert loaded.sources["src"].i_k_a == pytest.approx(4321.0)
    assert loaded.sources["src"].kappa == pytest.approx(1.75)
    assert loaded.faults["F"].t_k_s == pytest.approx(0.35)
    assert loaded.faults["F"].n_factor == pytest.approx(0.9)


def test_sources_without_characteristics_still_roundtrip(tmp_path):
    """The new columns are optional; legacy models must keep loading."""
    from groundinsight.database.crud import load_network, save_network

    net = _gi_net()
    gi.start_dbsession(str(tmp_path / "plain.db"))
    try:
        save_network(net, gi.session, overwrite=True)
        loaded = load_network(net.name, gi.session)
    finally:
        gi.close_dbsession()

    assert loaded.sources["src"].i_k_a is None
    assert loaded.sources["src"].kappa is None
    assert loaded.faults["F"].t_k_s is None
    assert loaded.faults["F"].n_factor == pytest.approx(1.0)


# ---------------------------------------------------------------------------
# end-to-end: pandapower -> groundinsight -> thermal check
# ---------------------------------------------------------------------------


def test_apply_shortcircuit_characteristics_writes_sources_and_fault():
    pp_net = _solved(fault="1ph", tk_s=0.4)
    net = _gi_net()

    report = gi.apply_shortcircuit_characteristics(net, pp_net, "F")

    assert report.height == 1
    row = report.to_dicts()[0]
    assert row["source_name"] == "src"
    assert row["i_k_previous_a"] == pytest.approx(1000.0)
    assert row["values_updated"] is False

    df = gi.read_shortcircuit_results(pp_net)
    expected = df.filter(df["bus_name"] == "MV2").to_dicts()[0]
    assert row["i_k_a"] == pytest.approx(expected["i_k_a"])

    source = net.sources["src"]
    assert source.i_k_a == pytest.approx(expected["i_k_a"])
    assert source.kappa == pytest.approx(expected["kappa"])
    assert source.r_to_x == pytest.approx(expected["r_to_x"])
    # the injection itself is untouched unless explicitly requested
    assert abs(complex(source.values[50.0].real, source.values[50.0].imag)) == pytest.approx(1000.0)

    assert net.faults["F"].t_k_s == pytest.approx(0.4)
    assert net.faults["F"].n_factor == pytest.approx(1.0)


def test_apply_shortcircuit_characteristics_can_set_the_injection():
    pp_net = _solved(fault="1ph", tk_s=0.4)
    net = _gi_net()
    report = gi.apply_shortcircuit_characteristics(
        net, pp_net, "F", set_source_values=True
    )
    row = report.to_dicts()[0]
    assert row["values_updated"] is True
    injected = net.sources["src"].values[50.0]
    assert abs(complex(injected.real, injected.imag)) == pytest.approx(row["i_k_a"])


def test_apply_distributes_proportionally_over_several_sources():
    pp_net = _solved(fault="1ph", tk_s=0.4)
    net = _gi_net()
    gi.create_source(name="src2", bus="MV1", values={50.0: 3000.0}, network=net)

    report = gi.apply_shortcircuit_characteristics(net, pp_net, "F")
    shares = {r["source_name"]: r["share"] for r in report.to_dicts()}
    assert shares["src"] == pytest.approx(0.25)
    assert shares["src2"] == pytest.approx(0.75)

    total = sum(r["i_k_a"] for r in report.to_dicts())
    assert total == pytest.approx(report.to_dicts()[0]["i_k_total_a"])
    # every source inherits the same loop characteristics
    assert net.sources["src"].kappa == pytest.approx(net.sources["src2"].kappa)


def test_apply_validates_its_arguments():
    pp_net = _solved(fault="1ph")
    net = _gi_net()
    with pytest.raises(ValueError):
        gi.apply_shortcircuit_characteristics(net, pp_net, "missing_fault")
    with pytest.raises(ValueError):
        gi.apply_shortcircuit_characteristics(net, pp_net, "F", pp_bus=0, bus_name="HV")
    with pytest.raises(ValueError):
        gi.apply_shortcircuit_characteristics(net, pp_net, "F", sources=["nope"])


def test_apply_reports_a_clear_error_for_unmatched_bus_names():
    pp_net = _solved(fault="1ph")
    net = _gi_net(buses=("A", "B", "C"))  # names do not match pandapower
    with pytest.raises(ValueError, match="No short-circuit result"):
        gi.apply_shortcircuit_characteristics(net, pp_net, "F")
    # ... but selecting the row explicitly works
    report = gi.apply_shortcircuit_characteristics(net, pp_net, "F", bus_name="MV2")
    assert report.height == 1


def test_read_shortcircuit_results_requires_a_solved_net():
    net = _pp_net()
    with pytest.raises(ValueError, match="no short-circuit results"):
        gi.read_shortcircuit_results(net)


def test_end_to_end_pandapower_to_thermal_check():
    """The whole point: no magic numbers left in check_conductor_limits."""
    pp_net = _solved(fault="1ph", tk_s=0.4)
    net = _gi_net(cross_section_mm2=50.0)
    gi.apply_shortcircuit_characteristics(net, pp_net, "F", set_source_values=True)
    gi.run_fault(net, "F")

    df = gi.check_conductor_limits(net, "F")  # t_k, n and kappa all from the model
    row = df.to_dicts()[0]

    expected = gi.read_shortcircuit_results(pp_net)
    expected_row = expected.filter(expected["bus_name"] == "MV2").to_dicts()[0]

    assert row["t_k_s"] == pytest.approx(0.4)
    assert row["n"] == pytest.approx(1.0)
    assert row["kappa"] == pytest.approx(expected_row["kappa"])
    assert row["i_p_A"] == pytest.approx(
        row["I_s_rms_A"] * row["kappa"] * math.sqrt(2)
    )
    assert row["I_th_A"] == pytest.approx(
        row["I_s_rms_A"] * math.sqrt(row["m"] + row["n"])
    )
    assert row["within_limit"] in (True, False)


def test_explicit_arguments_override_the_stored_characteristics():
    pp_net = _solved(fault="1ph", tk_s=0.4)
    net = _gi_net()
    gi.apply_shortcircuit_characteristics(net, pp_net, "F")
    gi.run_fault(net, "F")

    stored = gi.check_conductor_limits(net, "F").to_dicts()[0]
    overridden = gi.check_conductor_limits(net, "F", t_k=1.0, kappa=1.2).to_dicts()[0]

    assert overridden["t_k_s"] == pytest.approx(1.0)
    assert overridden["kappa"] == pytest.approx(1.2)
    assert overridden["kappa"] != pytest.approx(stored["kappa"])
    # a longer fault duration lowers the admissible current
    assert overridden["I_admissible_A"] < stored["I_admissible_A"]

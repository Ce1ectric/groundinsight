# tests/test_audit_pass18_thermal.py
"""
Audit pass 18 -- the IEC 60909 inputs of the two thermal checks.

- **A9** ``kappa`` and ``n`` reached the checks unvalidated from three
  directions (argument, ``Fault``/``Source`` field, ``kappa_from_r_to_x``),
  although both docstrings promised ranges. Every out-of-range value fails in
  the *unsafe* direction: ``kappa = 0.5`` gives a peak below the AC amplitude,
  ``kappa = -1`` one that points the other way, ``n = 3`` raises ``I_th`` by
  70 % and ``n = -2`` arrives as a bare ``math domain error``. ``I_th`` is now
  also computed by :func:`thermal_equivalent_current` instead of by a second
  copy of its formula.
- **A10** the direct-current component was counted twice: once inside
  ``I_s_rms = sqrt(sum_f |I(f)|^2)``, which runs over 0 Hz too, and once
  through ``m``, which is exactly the DC heat-effect factor. On the audit case
  ``[50 Hz]`` gave ``I_th = 1028 A`` and ``[0, 50 Hz]`` with 700 A of direct
  current gave 1255 A.
- **A17** ``kappa_eff`` is averaged over the sources that carry 60909 data
  while ``i_k_a`` sums all of them, so ``i_p`` applies the factor of one
  subset to the current of the whole. That is a documented approximation now,
  and the warning says which share of the current it rests on.
"""

from __future__ import annotations

import math

import pytest

import groundinsight as gi
from groundinsight.diagnostics import collecting
from groundinsight.models.core_models import BranchType, BusType
from tests._warning_policy import acknowledge

#: The 0 Hz bin is left out of the adiabatic check on purpose; the tests check
#: how.
DC_EXCLUDED = acknowledge("THERMAL_DC_EXCLUDED")

#: A source without IEC 60909 data, on purpose; the test checks the share it
#: names.
UNCOVERED = acknowledge("SC_DATA_INCOMPLETE")


def _network(frequencies, source_values):
    bus_type = BusType(
        name="bt", system_type="MV", voltage_level=20.0,
        impedance_formula="10",
        earthing_conductor_material="Cu",
        earthing_conductor_cross_section_mm2=50.0,
        electrode_material="Cu",
        electrode_cross_section_mm2=95.0,
    )
    branch_type = BranchType(
        name="brt", grounding_conductor=True,
        self_impedance_formula="(0.1+0.002*f*j)*l",
        mutual_impedance_formula="(0.05+0.001*f*j)*l",
        conductor_material="Cu",
        cross_section_mm2=35.0,
    )
    net = gi.create_network("thermal", frequencies=frequencies)
    for bus in ("S", "A", "F"):
        net.add_bus(gi.create_bus(bus, bus_type, 100.0))
    net.add_branch(gi.create_branch("SA", branch_type, "S", "A", 1.0, 100.0))
    net.add_branch(gi.create_branch("AF", branch_type, "A", "F", 1.0, 100.0))
    gi.create_fault(
        "F", "F", {f: 1.0 for f in frequencies}, active=True, network=net,
        t_k_s=0.5,
    )
    gi.create_source("SRC", "S", source_values, network=net)
    gi.run_fault(net, "F")
    return net


# --- A9: the ranges the docstrings promise ------------------------------------


@pytest.mark.parametrize("kappa", [-1.0, 0.0, 0.5, 1.0, 2.5, float("nan")])
def test_an_out_of_range_kappa_is_refused(kappa):
    net = _network([50.0], {50.0: 1000.0})
    with pytest.raises(ValueError, match="kappa must lie in"):
        gi.check_conductor_limits(net, "F", t_k_s=0.5, kappa=kappa)
    with pytest.raises(ValueError, match="kappa must lie in"):
        gi.check_node_limits(net, "F", t_k_s=0.5, kappa=kappa)


@pytest.mark.parametrize("n", [-2.0, 0.0, 1.5, 3.0, float("nan")])
def test_an_out_of_range_n_is_refused(n):
    net = _network([50.0], {50.0: 1000.0})
    # Since audit pass 19 (USE8) the message names the canonical keyword
    # n_factor; the call keeps the deprecated n and so exercises the alias.
    with pytest.raises(ValueError, match=r"n_factor must lie in \(0, 1\]"):
        gi.check_conductor_limits(net, "F", t_k_s=0.5, r_to_x=0.1, n_factor=n)


def test_a_stored_out_of_range_kappa_is_refused_too():
    """The value does not have to come from the call: a hand-edited frame can
    put it on the source, which is where the pandapower importer writes it.

    Two layers now stand in the way. Assignment is validated (M4), so the
    ordinary write is refused on the spot; and a value that bypasses Pydantic
    altogether (``model_construct``, the only remaining route) is still caught
    by the thermal guard (A9) before it can inflate ``i_p``.
    """
    net = _network([50.0], {50.0: 1000.0})
    with pytest.raises(ValueError, match="kappa must lie in"):
        net.sources["SRC"].kappa = 42.0
    assert net.sources["SRC"].kappa is None

    original = net.sources["SRC"]
    tampered = gi.Source.model_construct(
        **{name: getattr(original, name) for name in type(original).model_fields},
    )
    tampered.__dict__["kappa"] = 42.0
    net.sources["SRC"] = tampered
    with pytest.raises(ValueError, match="kappa must lie in"):
        gi.check_conductor_limits(net, "F", t_k_s=0.5)


def test_every_kappa_the_r_to_x_conversion_can_produce_is_accepted():
    """The converted value is bounded by [1.02, 2], so the guard must never
    fire on it -- otherwise the ordinary path would be the one that breaks."""
    net = _network([50.0], {50.0: 1000.0})
    for r_to_x in (0.0, 0.02, 0.1, 1.0, 10.0, 1000.0):
        frame = gi.check_conductor_limits(net, "F", t_k_s=0.5, r_to_x=r_to_x)
        assert 1.0 < frame["kappa"][0] <= 2.0


def test_i_th_is_the_shared_formula_not_a_second_copy():
    from groundinsight.analysis.shortcircuit import thermal_equivalent_current

    net = _network([50.0], {50.0: 1000.0})
    frame = gi.check_conductor_limits(net, "F", t_k_s=0.5, r_to_x=0.1)
    row = frame.row(0, named=True)
    assert row["I_th_A"] == pytest.approx(
        thermal_equivalent_current(row["I_ac_rms_A"], row["m"], row["n"]),
        rel=1e-12,
    )


# --- A10: the DC bin ----------------------------------------------------------


@DC_EXCLUDED
def test_the_dc_bin_is_not_counted_in_the_short_time_current():
    """
    The same AC excitation with and without a DC bin has to give the same
    ``I_th``: ``sqrt(m + n)`` already carries the DC content. Before the fix
    the DC current entered the RMS as well and inflated ``I_th`` by 22 %.
    """
    ac_only = gi.check_conductor_limits(
        _network([50.0], {50.0: 1000.0}), "F", t_k_s=0.5, r_to_x=0.1
    )
    with_dc = gi.check_conductor_limits(
        _network([0.0, 50.0], {0.0: 700.0, 50.0: 1000.0}),
        "F", t_k_s=0.5, r_to_x=0.1,
    )
    for name in ac_only["branch_name"]:
        a = ac_only.filter(ac_only["branch_name"] == name).row(0, named=True)
        b = with_dc.filter(with_dc["branch_name"] == name).row(0, named=True)
        assert b["I_th_A"] == pytest.approx(a["I_th_A"], rel=1e-9)


@DC_EXCLUDED
def test_the_dc_bin_is_reported_in_its_own_column():
    frame = gi.check_conductor_limits(
        _network([0.0, 50.0], {0.0: 700.0, 50.0: 1000.0}),
        "F", t_k_s=0.5, r_to_x=0.1,
    )
    row = frame.row(0, named=True)
    assert row["I_dc_A"] is not None and row["I_dc_A"] > 0
    assert row["I_ac_rms_A"] == pytest.approx(
        math.sqrt(row["I_s_rms_A"] ** 2 - row["I_dc_A"] ** 2), rel=1e-12
    )
    assert row["I_ac_rms_A"] < row["I_s_rms_A"]


def test_without_a_dc_bin_the_column_is_null_rather_than_zero():
    """A study with no 0 Hz bin has nothing to report there, which is a
    different statement from a measured direct current of zero."""
    frame = gi.check_conductor_limits(
        _network([50.0], {50.0: 1000.0}), "F", t_k_s=0.5, r_to_x=0.1
    )
    assert frame["I_dc_A"].null_count() == frame.height
    assert frame["I_ac_rms_A"].to_list() == pytest.approx(
        frame["I_s_rms_A"].to_list(), rel=1e-12
    )


@DC_EXCLUDED
def test_the_node_check_splits_the_dc_bin_the_same_way():
    ac_only = gi.check_node_limits(
        _network([50.0], {50.0: 1000.0}), "F", t_k_s=0.5, r_to_x=0.1
    )
    with_dc = gi.check_node_limits(
        _network([0.0, 50.0], {0.0: 700.0, 50.0: 1000.0}),
        "F", t_k_s=0.5, r_to_x=0.1,
    )
    keys = ["bus_name", "element"]
    for row in ac_only.rows(named=True):
        other = with_dc.filter(
            (with_dc["bus_name"] == row["bus_name"])
            & (with_dc["element"] == row["element"])
        ).row(0, named=True)
        assert other["I_th_A"] == pytest.approx(row["I_th_A"], rel=1e-9)
        assert other["I_dc_A"] is not None
    assert set(keys) <= set(with_dc.columns)


# --- A17: what the aggregation actually averages ------------------------------


@UNCOVERED
def test_the_uncovered_current_share_is_named_in_the_warning():
    net = _network([50.0], {50.0: 1000.0})
    gi.create_source("SRC2", "A", {50.0: 3000.0}, network=net)
    net.sources["SRC"].r_to_x = 0.1
    gi.run_fault(net, "F")
    with collecting() as records:
        gi.resolve_fault_sc_characteristics(net, "F")
    # An InputWarning SC_DATA_INCOMPLETE since audit pass 19 (GUI4).
    messages = [r.message for r in records if r.code == "SC_DATA_INCOMPLETE"]
    uncovered = [m for m in messages if "no IEC 60909 data" in m]
    assert uncovered, messages
    assert "%" in uncovered[0]
    assert "75 %" in uncovered[0]
    assert "rest mostly on an assumption" in uncovered[0]

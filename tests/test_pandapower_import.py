# tests/test_pandapower_import.py

"""
Tests for the pandapower importer
(:mod:`groundinsight.io.pandapower_import`).

The whole module is gated by ``pytest.importorskip("pandapower")`` so the
core groundinsight test run keeps working even when pandapower is not
installed (the optional extra ``groundinsight[pandapower]`` opts in).
"""

from __future__ import annotations

import polars as pl
import pytest

import groundinsight as gi
from groundinsight.models.core_models import BranchType, BusType


pp = pytest.importorskip("pandapower")


# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------


def _bus_type() -> BusType:
    return BusType(
        name="ImportedBus",
        description="Default for pandapower import tests",
        system_type="Grounded",
        voltage_level=20.0,
        impedance_formula="rho * 0 + 1.0 + I * f * 0",
    )


def _branch_type() -> BranchType:
    return BranchType(
        name="ImportedCable",
        description="Default for pandapower import tests",
        grounding_conductor=True,
        self_impedance_formula="(0.25 + I * 0.6) * l",
        mutual_impedance_formula="(0.0 + I * 0.6) * l",
    )


def _defaults() -> gi.ImportDefaults:
    return gi.ImportDefaults(
        rho=100.0,
        frequencies=[50.0],
        default_bus_type=_bus_type(),
        default_branch_type=_branch_type(),
    )


def _build_three_bus_mv_with_lv_stub():
    """
    Build a tiny pandapower net with three 20-kV buses in a line plus one
    0.4-kV stub bus connected via a transformer-line surrogate. Used to
    exercise the voltage-level filter and the in_service propagation.
    """
    net = pp.create_empty_network(name="testnet_mv_lv")

    b1 = pp.create_bus(net, vn_kv=20.0, name="bus_mv_1")
    b2 = pp.create_bus(net, vn_kv=20.0, name="bus_mv_2")
    b3 = pp.create_bus(net, vn_kv=20.0, name="bus_mv_3", in_service=False)
    b_lv = pp.create_bus(net, vn_kv=0.4, name="bus_lv_1")

    pp.create_line_from_parameters(
        net,
        from_bus=b1,
        to_bus=b2,
        length_km=1.5,
        r_ohm_per_km=0.25,
        x_ohm_per_km=0.6,
        c_nf_per_km=0.0,
        max_i_ka=0.4,
        name="line_mv_12",
    )
    pp.create_line_from_parameters(
        net,
        from_bus=b2,
        to_bus=b3,
        length_km=2.0,
        r_ohm_per_km=0.25,
        x_ohm_per_km=0.6,
        c_nf_per_km=0.0,
        max_i_ka=0.4,
        name="line_mv_23",
        in_service=False,
    )
    # Cross-voltage-level "line" (non-physical but a clean filter test)
    pp.create_line_from_parameters(
        net,
        from_bus=b3,
        to_bus=b_lv,
        length_km=0.05,
        r_ohm_per_km=0.5,
        x_ohm_per_km=0.4,
        c_nf_per_km=0.0,
        max_i_ka=0.2,
        name="line_mv_lv_stub",
    )
    return net, {"b1": b1, "b2": b2, "b3": b3, "b_lv": b_lv}


# ---------------------------------------------------------------------------
# from_pandapower
# ---------------------------------------------------------------------------


def test_from_pandapower_imports_only_target_voltage_level():
    """20 kV buses end up in the Network, the 0.4 kV stub does not. The
    cross-voltage-level line is dropped along with the LV bus."""
    net, _ = _build_three_bus_mv_with_lv_stub()
    network = gi.from_pandapower(net, defaults=_defaults(), voltage_level_kV=20.0)

    assert network.frequencies == [50.0]
    assert set(network.buses.keys()) == {"bus_mv_1", "bus_mv_2", "bus_mv_3"}

    # Only the in-service MV-MV line lives on; line_mv_23 also lives on
    # but with active=False, while line_mv_lv_stub is filtered out.
    assert set(network.branches.keys()) == {"line_mv_12", "line_mv_23"}
    assert network.branches["line_mv_12"].length == pytest.approx(1.5)
    assert network.branches["line_mv_23"].length == pytest.approx(2.0)


def test_in_service_false_propagates_to_active_false():
    """Out-of-service pandapower elements turn into ``active=False`` on
    the groundinsight side -- they are still present in the model so the
    outage / what-if helpers can reason over them."""
    net, _ = _build_three_bus_mv_with_lv_stub()
    network = gi.from_pandapower(net, defaults=_defaults(), voltage_level_kV=20.0)

    assert network.buses["bus_mv_1"].active is True
    assert network.buses["bus_mv_2"].active is True
    assert network.buses["bus_mv_3"].active is False

    assert network.branches["line_mv_12"].active is True
    assert network.branches["line_mv_23"].active is False


def test_from_pandapower_uses_supplied_types_and_rho():
    """Every imported bus / branch wears the default types from
    ImportDefaults and the project-wide rho."""
    net, _ = _build_three_bus_mv_with_lv_stub()
    defaults = _defaults()
    network = gi.from_pandapower(net, defaults=defaults, voltage_level_kV=20.0)

    for bus in network.buses.values():
        assert bus.type.name == defaults.default_bus_type.name
        assert bus.specific_earth_resistance == pytest.approx(100.0)

    for branch in network.branches.values():
        assert branch.type.name == defaults.default_branch_type.name
        assert branch.specific_earth_resistance == pytest.approx(100.0)


def test_from_pandapower_rejects_empty_frequencies():
    net, _ = _build_three_bus_mv_with_lv_stub()
    bad = gi.ImportDefaults(
        rho=100.0,
        frequencies=[],
        default_bus_type=_bus_type(),
        default_branch_type=_branch_type(),
    )
    with pytest.raises(ValueError, match="frequencies"):
        gi.from_pandapower(net, defaults=bad, voltage_level_kV=20.0)


def test_from_pandapower_include_trafos_is_not_implemented():
    net, _ = _build_three_bus_mv_with_lv_stub()
    with pytest.raises(NotImplementedError):
        gi.from_pandapower(
            net,
            defaults=_defaults(),
            voltage_level_kV=20.0,
            include_trafos=True,
        )


def test_imported_network_solves_after_adding_source_and_fault():
    """Smoke-test: the imported Network is structurally complete enough
    to run a fault solve once the project-specific source / fault are
    added by the caller."""
    net, _ = _build_three_bus_mv_with_lv_stub()
    network = gi.from_pandapower(net, defaults=_defaults(), voltage_level_kV=20.0)

    # Re-activate bus_mv_3 so we have a connected sub-net for the test
    network.buses["bus_mv_3"].active = True
    network.branches["line_mv_23"].active = True

    gi.create_source(name="src", bus="bus_mv_1", values={50: 100.0}, network=network)
    gi.create_fault(name="F1", bus="bus_mv_3", scalings={50: 1.0}, network=network)
    gi.run_fault(network, fault_name="F1", auto_parallel_coefficients=True)

    epr = network.results["F1"].buses[0].uepr
    assert epr >= 0.0  # solver completed and produced a number


# ---------------------------------------------------------------------------
# preview_pandapower_import
# ---------------------------------------------------------------------------


def test_preview_lists_keep_and_skip_with_reasons():
    net, _ = _build_three_bus_mv_with_lv_stub()
    df = gi.preview_pandapower_import(net, voltage_level_kV=20.0)

    expected_columns = {
        "kind",
        "status",
        "pp_index",
        "name",
        "vn_kv",
        "from_bus",
        "to_bus",
        "length_km",
        "in_service",
        "reason",
    }
    assert expected_columns.issubset(set(df.columns))

    # Three MV buses kept, one LV bus skipped with the voltage-mismatch reason
    bus_kept = df.filter(
        (pl.col("kind") == "bus") & (pl.col("status") == "keep")
    )
    bus_skipped = df.filter(
        (pl.col("kind") == "bus") & (pl.col("status") == "skip")
    )
    assert bus_kept.height == 3
    assert bus_skipped.height == 1
    assert bus_skipped["reason"].to_list() == ["voltage_level_mismatch"]

    # MV-LV line is skipped with the off-target endpoint reason
    line_skipped = df.filter(
        (pl.col("kind") == "line") & (pl.col("status") == "skip")
    )
    assert "endpoint_off_target_voltage_level" in set(
        line_skipped["reason"].to_list()
    )


def test_preview_does_not_mutate_inputs_or_build_network():
    """Preview must be a pure inspection -- the original net is untouched
    and no Network is returned."""
    net, _ = _build_three_bus_mv_with_lv_stub()
    bus_count_before = len(net.bus)
    line_count_before = len(net.line)

    df = gi.preview_pandapower_import(net, voltage_level_kV=20.0)
    assert isinstance(df, pl.DataFrame)
    assert len(net.bus) == bus_count_before
    assert len(net.line) == line_count_before

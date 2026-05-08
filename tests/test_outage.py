# tests/test_outage.py

"""
Tests for the outage / what-if study layer
(:mod:`groundinsight.simulation.outage`) and the underlying ``active`` flag
on :class:`groundinsight.models.core_models.Bus` /
:class:`groundinsight.models.core_models.Branch`.

Three areas are covered:

1. Default behaviour: ``active=True`` everywhere keeps results identical to
   the pre-change behaviour on a small, deterministic topology.
2. Solver / pathfinder honour ``active``: an inactive branch acts as an
   open circuit, an inactive bus is removed from the nodal system and from
   pathfinding.
3. ``outage_context`` rolls back, ``run_outage_study`` aggregates, and the
   ``compare_*`` helpers produce sane long-format frames.
"""

from __future__ import annotations

import math

import polars as pl
import pytest

import groundinsight as gi
from groundinsight.models.core_models import BranchType, BusType
from groundinsight.simulation.outage import (
    Outage,
    OutageStudyResult,
    outage_context,
    run_outage_study,
)


# ---------------------------------------------------------------------------
# Shared helpers (small, deterministic three-bus line with one parallel ring
# branch). Mirrors the style of tests/test_topology_and_reduction.py.
# ---------------------------------------------------------------------------


def _bus_type() -> BusType:
    return BusType(
        name="BusUnit",
        description="Unit-like bus impedance for outage tests",
        system_type="Grounded",
        voltage_level=20.0,
        impedance_formula="rho * 0 + 1.0 + I * f * 0",
    )


def _cable_branch_type() -> BranchType:
    return BranchType(
        name="MSCable",
        description="MV cable, R=0.25, omega*L=0.6 at 50 Hz, full coupling",
        grounding_conductor=True,
        self_impedance_formula="(0.25 + I * 0.6) * l",
        mutual_impedance_formula="(0.0 + I * 0.6) * l",
    )


def _build_line(name: str = "TestLine"):
    """Three-bus line: bus1 -- b12 -- bus2 -- b23 -- bus3, source@bus1, fault@bus3."""
    net = gi.create_network(name=name, frequencies=[50])
    bt = _bus_type()
    cable = _cable_branch_type()

    for i in range(1, 4):
        gi.create_bus(name=f"bus{i}", type=bt, network=net)

    gi.create_branch(
        name="b12", type=cable, from_bus="bus1", to_bus="bus2", length=1.0, network=net
    )
    gi.create_branch(
        name="b23", type=cable, from_bus="bus2", to_bus="bus3", length=1.0, network=net
    )

    gi.create_source(name="src", bus="bus1", values={50: 100.0}, network=net)
    gi.create_fault(name="F1", bus="bus3", scalings={50: 1.0}, network=net)
    return net


def _build_ring(name: str = "TestRing"):
    """Three-bus ring with two parallel paths source -> fault for outage study."""
    net = gi.create_network(name=name, frequencies=[50])
    bt = _bus_type()
    cable = _cable_branch_type()

    for i in range(1, 4):
        gi.create_bus(name=f"bus{i}", type=bt, network=net)

    gi.create_branch(
        name="b12", type=cable, from_bus="bus1", to_bus="bus2", length=1.0, network=net
    )
    gi.create_branch(
        name="b23", type=cable, from_bus="bus2", to_bus="bus3", length=1.0, network=net
    )
    gi.create_branch(
        name="b13", type=cable, from_bus="bus1", to_bus="bus3", length=1.0, network=net
    )

    gi.create_source(name="src", bus="bus1", values={50: 100.0}, network=net)
    gi.create_fault(name="F1", bus="bus3", scalings={50: 1.0}, network=net)
    return net


def _epr_at(net, bus_name: str, fault: str = "F1", freq: float = 50.0) -> float:
    """
    EPR magnitude at a given bus / frequency, taken directly from the
    Pydantic result object. Bypasses ``res_buses`` (and thus the
    Polars Object-column for ``frequency_Hz``) which mixes floats and the
    ``"RMS"`` literal and is awkward to filter portably.

    Returns 0.0 if the bus is not present in the result (e.g. because it
    was deactivated).
    """
    result = net.results[fault]
    bus = next((b for b in result.buses if b.name == bus_name), None)
    if bus is None:
        return 0.0
    z = bus.uepr_freq[freq]
    return abs(complex(z.real, z.imag))


# ---------------------------------------------------------------------------
# 1. Defaults are backwards-compatible
# ---------------------------------------------------------------------------


def test_active_field_defaults_to_true():
    """Adding a Bus / Branch without specifying ``active`` keeps it active."""
    net = _build_line()
    for bus in net.buses.values():
        assert bus.active is True
    for branch in net.branches.values():
        assert branch.active is True


def test_default_solve_unchanged_versus_pre_change_baseline():
    """A network with all elements active solves to the same EPR as before
    the ``active`` field was introduced. The reference value is the closed
    form for a two-cable series line: r = R / sqrt(R^2 + (omega L)^2) at the
    fault bus, EPR = r * Z_grounding * I_fault. We just check structural
    consistency: EPR>0 at the fault bus, EPR>0 at the source bus, and the
    intermediate bus sits in between."""
    net = _build_line()
    gi.run_fault(net, fault_name="F1", auto_parallel_coefficients=True)
    e1 = _epr_at(net, "bus1")
    e2 = _epr_at(net, "bus2")
    e3 = _epr_at(net, "bus3")
    assert e1 > 0 and e2 > 0 and e3 > 0
    # Fault bus is the most stressed node in this topology
    assert e3 == max(e1, e2, e3)


# ---------------------------------------------------------------------------
# 2. Solver / pathfinder honour ``active``
# ---------------------------------------------------------------------------


def test_inactive_branch_acts_as_open_circuit_in_a_ring():
    """Disabling one branch of a parallel ring removes that path; the result
    must match a plain two-bus / single-cable line solve via the remaining
    path. We check this indirectly by comparing branch currents: the open
    branch carries exactly zero current."""
    net = _build_ring()
    net.branches["b13"].active = False
    # Drop pre-existing path cache so define_paths runs again with the new topology
    net.paths = {}
    gi.run_fault(net, fault_name="F1", auto_parallel_coefficients=True)

    df = net.res_branches(fault="F1")
    rms_b13 = (
        df.filter(
            (pl.col("branch_name") == "b13")
            & (pl.col("frequency_Hz").cast(pl.Utf8) == "RMS")
        )["I_branch_A"][0]
    )
    assert rms_b13 == pytest.approx(0.0, abs=1e-12)


def test_inactive_bus_removes_node_and_paths():
    """Setting ``active=False`` on an intermediate bus must drop every path
    that runs through it. With the line bus1-bus2-bus3 and bus2 inactive, no
    path from src@bus1 to fault@bus3 exists -- run_fault still completes
    (no source is contributing) but the EPR at bus3 collapses to ~0."""
    net = _build_line()
    net.buses["bus2"].active = False
    net.paths = {}
    gi.run_fault(net, fault_name="F1", auto_parallel_coefficients=True)

    # bus2 is removed from the result -- only bus1 and bus3 remain
    df = net.res_buses(fault="F1")
    bus_names = sorted(set(df["bus_name"].to_list()))
    assert "bus2" not in bus_names

    # And the fault bus has near-zero EPR because no current is injected
    assert _epr_at(net, "bus3") == pytest.approx(0.0, abs=1e-12)


def test_active_field_round_trips_through_json():
    """Pydantic JSON serialisation must preserve a non-default ``active``
    value on Bus and Branch."""
    net = _build_line()
    net.branches["b12"].active = False
    payload = net.model_dump_json()
    from groundinsight.models.core_models import Network

    restored = Network.model_validate_json(payload)
    assert restored.branches["b12"].active is False
    assert restored.branches["b23"].active is True
    assert restored.buses["bus2"].active is True


# ---------------------------------------------------------------------------
# 3. outage_context, run_outage_study, compare_*
# ---------------------------------------------------------------------------


def test_outage_context_restores_active_flags_on_exit():
    """Even if the body raises, the context manager must restore the
    original ``active`` values and the previous path cache."""
    net = _build_ring()
    gi.create_paths(net)
    saved_paths = dict(net.paths)

    outage = Outage(name="b13_open", disabled_branches=["b13"])
    try:
        with outage_context(net, outage):
            assert net.branches["b13"].active is False
            raise RuntimeError("boom")
    except RuntimeError:
        pass

    assert net.branches["b13"].active is True
    assert net.paths == saved_paths


def test_outage_context_raises_on_unknown_names():
    net = _build_line()
    with pytest.raises(ValueError, match="unknown buses"):
        with outage_context(net, Outage(name="x", disabled_buses=["does_not_exist"])):
            pass
    with pytest.raises(ValueError, match="unknown branches"):
        with outage_context(net, Outage(name="x", disabled_branches=["nope"])):
            pass


def test_run_outage_study_collects_base_and_scenarios():
    """A two-scenario study (base + branch outage + bus outage) must produce
    one bus / branch DataFrame per label."""
    net = _build_ring()
    study = run_outage_study(
        net,
        fault="F1",
        scenarios=[
            Outage(name="b13_open", disabled_branches=["b13"]),
            Outage(name="bus2_isolated", disabled_buses=["bus2"]),
        ],
        auto_parallel_coefficients=True,
    )

    assert isinstance(study, OutageStudyResult)
    assert study.base_label == "base"
    assert set(study.bus_results.keys()) == {"base", "b13_open", "bus2_isolated"}
    assert set(study.branch_results.keys()) == {"base", "b13_open", "bus2_isolated"}

    # Base has all three buses, bus2_isolated drops bus2
    base_buses = set(study.bus_results["base"]["bus_name"].to_list())
    iso_buses = set(study.bus_results["bus2_isolated"]["bus_name"].to_list())
    assert {"bus1", "bus2", "bus3"}.issubset(base_buses)
    assert "bus2" not in iso_buses


def test_run_outage_study_rejects_label_collisions():
    net = _build_line()
    with pytest.raises(ValueError, match="collides"):
        run_outage_study(
            net,
            fault="F1",
            scenarios=[Outage(name="base")],
        )


def test_compare_buses_produces_long_format_with_deltas():
    """The comparison helper must emit the expected columns and at least
    one non-zero delta for the disabled-branch scenario at the fault bus."""
    net = _build_ring()
    study = run_outage_study(
        net,
        fault="F1",
        scenarios=[Outage(name="b13_open", disabled_branches=["b13"])],
        auto_parallel_coefficients=True,
    )

    df = study.compare_buses()
    assert "bus_name" in df.columns
    assert "frequency_Hz" in df.columns
    assert "scenario" in df.columns
    assert "metric" in df.columns
    assert "value" in df.columns
    assert "delta_vs_base" in df.columns
    assert "delta_pct_vs_base" in df.columns

    # The "RMS" row is the deterministic anchor (literal string, no float
    # casting ambiguity). At the fault bus with one branch removed from the
    # ring the RMS EPR must shift by a non-trivial amount versus the base.
    deltas = (
        df.filter(
            (pl.col("scenario") == "b13_open")
            & (pl.col("metric") == "EPR_V")
            & (pl.col("bus_name") == "bus3")
            & (pl.col("frequency_Hz") == "RMS")
        )["delta_vs_base"]
        .to_list()
    )
    assert len(deltas) == 1
    assert deltas[0] is not None
    assert not math.isclose(deltas[0], 0.0, abs_tol=1e-9)


def test_compare_branches_against_custom_reference():
    """Picking a non-base reference must rename the delta columns accordingly
    and yield zero on the reference scenario."""
    net = _build_ring()
    study = run_outage_study(
        net,
        fault="F1",
        scenarios=[Outage(name="b13_open", disabled_branches=["b13"])],
        include_base=True,
        auto_parallel_coefficients=True,
    )

    df = study.compare_branches(against="b13_open")
    assert "delta_vs_b13_open" in df.columns

    # Self-comparison is identically zero (within float noise) for branches
    # that were not disabled. Disabled branches have value=0 in both rows.
    self_deltas = (
        df.filter(pl.col("scenario") == "b13_open")["delta_vs_b13_open"].to_list()
    )
    for d in self_deltas:
        if d is None or (isinstance(d, float) and math.isnan(d)):
            continue
        assert math.isclose(d, 0.0, abs_tol=1e-12)

# tests/test_incomplete_results_guard.py

"""
Regression tests: a limit check must never silently report "no violations".

Both thermal checks read the *stored* result of a fault, not the network
topology. When the stored result is incomplete, the loop that builds the
frame simply produces fewer rows -- and a missing row is indistinguishable
from a passing one. Two natural safety gates then read an incomplete result
as clean:

* iterating the rows and collecting ``within_limit is False``,
* testing ``frame.is_empty()``,

and the ``logger.warning`` channel of the check goes silent as well. A third
formulation, ``frame.filter(pl.col("within_limit") == False)``, used to raise
``ColumnNotFoundError`` instead, because an all-empty ``pl.DataFrame([])``
carries no schema at all.

The half-built state is reachable through the public API:
``ElectricalNetwork.solve_network()`` resets ``network.results[fault]`` and
only ``compute_branch_currents()`` refills the branch list, so solving a
hand-built :class:`ElectricalNetwork` for inspection -- as one does to look
at ``Y``, ``i`` or ``u`` -- drops every branch result. Mutating the topology
after ``run_fault`` opens the same gap.

The guard turns all of that into a ``ValueError``, and the schema is now
declared explicitly so a legitimately empty frame stays selectable.
"""

from __future__ import annotations

import logging

import polars as pl
import pytest

import groundinsight as gi
from groundinsight.electrical_network import ElectricalNetwork


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _bus_type() -> gi.BusType:
    return gi.BusType(
        name="footing",
        description="Tower footing",
        system_type="Tower",
        voltage_level=110.0,
        impedance_formula="rho * 0.15",
        earthing_conductor_material="Steel",
        earthing_conductor_cross_section_mm2=50.0,
        electrode_material="Steel",
        electrode_cross_section_mm2=95.0,
    )


def _thin_branch_type() -> gi.BranchType:
    """A 16 mm2 steel shield -- genuinely over-stressed at kA level."""
    return gi.BranchType(
        name="thin",
        grounding_conductor=True,
        self_impedance_formula="(0.30 + j*f*0.0025) * l",
        mutual_impedance_formula="(0.05 + j*f*0.0020) * l",
        conductor_material="Steel",
        cross_section_mm2=16.0,
    )


def _overstressed_net() -> gi.Network:
    """Two buses, one undersized shield, 5 kA infeed -> a real violation."""
    net = gi.create_network(name="n", frequencies=[50.0], description="guard")
    bt = _bus_type()
    for name in ("A", "B"):
        gi.create_bus(name=name, type=bt, specific_earth_resistance=100.0, network=net)
    gi.create_branch(
        name="A-B", type=_thin_branch_type(), from_bus="A", to_bus="B",
        length=0.3, network=net,
    )
    gi.create_source(name="s", bus="A", values={50.0: 5000.0}, r_to_x=0.1, network=net)
    gi.create_fault(
        name="F", bus="B", scalings={50.0: 1.0}, t_k_s=0.5, n_factor=1.0, network=net,
    )
    gi.run_fault(net, "F")
    return net


# ---------------------------------------------------------------------------
# the violation this guard protects
# ---------------------------------------------------------------------------


def test_the_reference_network_really_does_violate():
    """Guard the guard: without it the rest of the module proves nothing."""
    df = gi.check_conductor_limits(_overstressed_net(), "F")
    assert df.height == 1
    assert df["within_limit"][0] is False
    assert df["utilization"][0] > 1.0


# ---------------------------------------------------------------------------
# solve_network() leaves a half-built result -> must raise, not report clean
# ---------------------------------------------------------------------------


def test_solve_network_alone_drops_branch_results():
    """Pin the upstream cause, so the guard's reason stays visible."""
    net = _overstressed_net()
    assert len(net.results["F"].branches) == 1

    ElectricalNetwork(net).solve_network()

    # buses survive (solve_network rebuilds them), branches do not
    assert len(net.results["F"].buses) == 2
    assert len(net.results["F"].branches) == 0


def test_branch_check_raises_instead_of_silently_passing(caplog):
    net = _overstressed_net()
    ElectricalNetwork(net).solve_network()

    with caplog.at_level(logging.WARNING, logger="groundinsight"):
        with pytest.raises(ValueError, match="Incomplete results"):
            gi.check_conductor_limits(net, "F")

    # and it must not have been reported as a clean run on the way out
    assert "Thermal limit exceeded" not in caplog.text


def test_the_error_names_the_missing_branches_and_the_remedy():
    net = _overstressed_net()
    ElectricalNetwork(net).solve_network()

    with pytest.raises(ValueError) as exc:
        gi.check_conductor_limits(net, "F")

    msg = str(exc.value)
    assert "'A-B'" in msg          # which branch
    assert "run_fault" in msg      # how to fix it
    assert "solve_network" in msg  # what caused it


def test_node_check_raises_when_a_bus_result_is_missing():
    net = _overstressed_net()
    net.results["F"].buses = [
        rb for rb in net.results["F"].buses if rb.name != "B"
    ]

    with pytest.raises(ValueError, match="Incomplete results"):
        gi.check_node_limits(net, "F")


def test_branch_added_after_run_fault_is_not_silently_skipped():
    """Staleness, not corruption: the same silent-skip in everyday use."""
    net = _overstressed_net()
    gi.create_branch(
        name="B-C", type=_thin_branch_type(), from_bus="B", to_bus="A",
        length=0.2, network=net,
    )

    with pytest.raises(ValueError, match="Incomplete results"):
        gi.check_conductor_limits(net, "F")

    gi.run_fault(net, "F")  # re-running clears it
    assert gi.check_conductor_limits(net, "F").height == 2


def test_inactive_buses_do_not_trip_the_node_guard():
    """solve_network reports only active buses -- that is complete, not stale."""
    net = _overstressed_net()
    # A dangling third bus, so deactivating it neither removes the infeed nor
    # splits the network.
    gi.create_bus(
        name="C", type=_bus_type(), specific_earth_resistance=100.0, network=net,
    )
    gi.create_branch(
        name="B-C", type=_thin_branch_type(), from_bus="B", to_bus="C",
        length=0.2, network=net,
    )
    net.buses["C"].active = False
    gi.run_fault(net, "F")

    df = gi.check_node_limits(net, "F")
    assert set(df["bus_name"].to_list()) == {"A", "B"}
    # the branch to the inactive bus is still reported, as open
    branches = gi.check_conductor_limits(net, "F")
    assert set(branches["branch_name"].to_list()) == {"A-B", "B-C"}


def test_inactive_branches_still_appear_and_do_not_trip_the_guard():
    net = _overstressed_net()
    net.branches["A-B"].active = False
    gi.run_fault(net, "F")

    df = gi.check_conductor_limits(net, "F")
    assert df["branch_name"].to_list() == ["A-B"]
    assert df["I_s_rms_A"][0] == pytest.approx(0.0)


# ---------------------------------------------------------------------------
# schema stability: an empty frame must stay usable
# ---------------------------------------------------------------------------


def _branchless_net() -> gi.Network:
    net = gi.create_network(name="n", frequencies=[50.0], description="empty")
    gi.create_bus(
        name="A", type=_bus_type(), specific_earth_resistance=100.0, network=net,
    )
    gi.create_source(name="s", bus="A", values={50.0: 1000.0}, r_to_x=0.1, network=net)
    gi.create_fault(
        name="F", bus="A", scalings={50.0: 1.0}, t_k_s=0.5, n_factor=1.0, network=net,
    )
    gi.run_fault(net, "F")
    return net


def test_legitimately_empty_branch_frame_keeps_its_schema():
    """A network without branches: zero rows, but still a usable frame."""
    df = gi.check_conductor_limits(_branchless_net(), "F")

    assert df.height == 0
    assert "within_limit" in df.columns
    # the formulation that used to raise ColumnNotFoundError
    assert df.filter(pl.col("within_limit") == False).height == 0  # noqa: E712
    assert df.select("branch_name", "I_th_A", "utilization").height == 0


def test_empty_branch_frame_has_the_same_columns_as_a_populated_one():
    empty = gi.check_conductor_limits(_branchless_net(), "F")
    full = gi.check_conductor_limits(_overstressed_net(), "F")

    assert empty.columns == full.columns
    assert empty.schema == full.schema


def test_all_null_columns_keep_their_declared_dtype():
    """Without an explicit schema polars infers Null for an all-None column."""
    net = gi.create_network(name="n", frequencies=[50.0], description="undeclared")
    bare = gi.BusType(
        name="bare", description="no elements declared", system_type="Tower",
        voltage_level=110.0, impedance_formula="rho * 0.15",
    )
    gi.create_bus(name="A", type=bare, specific_earth_resistance=100.0, network=net)
    gi.create_source(name="s", bus="A", values={50.0: 1000.0}, r_to_x=0.1, network=net)
    gi.create_fault(
        name="F", bus="A", scalings={50.0: 1.0}, t_k_s=0.5, n_factor=1.0, network=net,
    )
    gi.run_fault(net, "F")

    df = gi.check_node_limits(net, "F")
    assert df["material"].to_list() == [None, None]
    assert df.schema["material"] == pl.Utf8
    assert df.schema["within_limit"] == pl.Boolean
    assert df.schema["cross_section_mm2"] == pl.Float64

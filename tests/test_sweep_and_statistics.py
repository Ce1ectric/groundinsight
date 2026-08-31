"""
Parameter sweeps over one fault, and the statistics that need their output.

The sweep exists because there was nothing to compute a statistic *on*: every
accessor on ``Network`` reports a single solve. Stacking many solves into one
long frame with the parameters as columns is the structural part; the summary
and the classification on top are thin by comparison, and that split is what
these tests check.

The invariant that matters most is the restore. A sweep writes onto the network
and must give it back untouched -- including after a point that raised, because
otherwise one bad parameter combination silently poisons every later point.
"""

from __future__ import annotations

import polars as pl
import pytest

import groundinsight as gi
from groundinsight.models.core_models import BusType, BranchType


FREQ = [50.0, 250.0]
CHAIN = [("SA", "S", "A"), ("AF", "A", "F"), ("FR1", "F", "R1")]
BUSES = ["S", "A", "F", "R1"]


def _network():
    bus_type = BusType(
        name="bt", system_type="MV", voltage_level=20.0, impedance_formula="10"
    )
    branch_type = BranchType(
        name="brt",
        grounding_conductor=True,
        self_impedance_formula="(0.1+0.2*j)*l",
        mutual_impedance_formula="(0.05+0.1*j)*l",
    )
    net = gi.create_network("net", frequencies=FREQ)
    for bus in BUSES:
        net.add_bus(gi.create_bus(bus, bus_type, 100.0))
    for name, from_bus, to_bus in CHAIN:
        net.add_branch(
            gi.create_branch(name, branch_type, from_bus, to_bus, 1.0, 100.0)
        )
    gi.create_fault("F", "F", {50.0: 1.0, 250.0: 0.2}, active=True, network=net)
    gi.create_source(
        "SRC", "S", {50.0: 1000.0, 250.0: 200.0}, network=net
    )
    return net


def _k_points(net, rho=100.0):
    return gi.rho_f_points(
        bus="F",
        k_vectors={
            "weak": (0.10, 1e-4, 3e-4, 0.0, 0.0),
            "medium": (0.05, 1e-4, 3e-4, 0.0, 0.0),
            "strong": (0.01, 1e-4, 3e-4, 0.0, 0.0),
        },
        frequencies=net.frequencies,
        rho=rho,
    )


# --- the sweep itself --------------------------------------------------------


def test_a_sweep_stacks_every_point_with_its_parameters():
    net = _network()
    study = gi.run_sweep(net, fault="F", points=_k_points(net))
    frame = study.buses()
    assert set(frame["point"]) == {"weak", "medium", "strong"}
    # The k-vector arrives as plottable columns, not just as a label.
    for column in ("k1", "k2", "k3", "k4", "k5", "rho_Ohm_m", "bus"):
        assert column in frame.columns
    assert sorted(frame["k1"].unique()) == pytest.approx([0.01, 0.05, 0.10])
    assert not study.failures


def test_the_rho_f_characteristic_actually_reaches_the_solver():
    """A lower ``k1`` is a better electrode, so the potential rise has to fall
    monotonically along the catalogue."""
    net = _network()
    study = gi.run_sweep(net, fault="F", points=_k_points(net))
    frame = (
        study.buses()
        .filter((pl.col("bus_name") == "F") & (pl.col("frequency_Hz") == "RMS"))
        .sort("k1")
    )
    eprs = frame["EPR_V"].to_list()
    assert all(a < b for a, b in zip(eprs, eprs[1:]))
    # k1 = 0.01 -> 1 Ohm, k1 = 0.10 -> 10 Ohm at rho = 100: a tenfold electrode
    # is damped by the parallel network to a threefold potential rise.
    assert eprs[-1] / eprs[0] > 3.0


def test_the_network_is_returned_exactly_as_it_was_found():
    net = _network()
    before = {name: dict(bus.impedance) for name, bus in net.buses.items()}
    before_rho = {n: b.specific_earth_resistance for n, b in net.buses.items()}
    gi.run_sweep(net, fault="F", points=_k_points(net))
    for name, bus in net.buses.items():
        assert dict(bus.impedance) == before[name]
        assert bus.specific_earth_resistance == before_rho[name]


def test_a_failing_point_neither_aborts_the_sweep_nor_leaks_its_override():
    """
    One unsolvable combination must not cost the rest of the grid, and above all
    must not leave its impedance behind on the network -- that would corrupt
    every point after it, silently.
    """
    net = _network()
    before = dict(net.buses["F"].impedance)
    points = list(_k_points(net))
    points.insert(
        1,
        gi.SweepPoint(
            label="broken", bus_impedance={"does_not_exist": {50.0: 1 + 0j}}
        ),
    )
    study = gi.run_sweep(net, fault="F", points=points)
    assert "broken" in study.failures
    assert set(study.labels) == {"weak", "medium", "strong"}
    assert dict(net.buses["F"].impedance) == before


def test_on_error_raise_propagates_instead():
    net = _network()
    points = [gi.SweepPoint(label="broken", bus_impedance={"nope": {50.0: 1 + 0j}})]
    with pytest.raises(ValueError, match="not in network"):
        gi.run_sweep(net, fault="F", points=points, on_error="raise")


def test_duplicate_labels_are_rejected_before_anything_is_solved():
    net = _network()
    points = [gi.SweepPoint(label="a"), gi.SweepPoint(label="a")]
    with pytest.raises(ValueError, match="more than once"):
        gi.run_sweep(net, fault="F", points=points)


def test_a_non_passive_k_vector_is_named_at_construction_time():
    """An unconstrained fit can produce ``Re(Z) <= 0``; saying so where the
    vector is built beats a solver error three layers down."""
    net = _network()
    with pytest.raises(ValueError, match="not a passive impedance"):
        gi.rho_f_points(
            bus="F",
            k_vectors={"bad": (-0.01, 1e-4, 3e-4, 0.0, 0.0)},
            frequencies=net.frequencies,
            rho=100.0,
        )


def test_soil_resistivity_can_be_swept_through_the_bus_type_formula():
    net = _network()
    bus_type = BusType(
        name="rho_bt",
        system_type="MV",
        voltage_level=20.0,
        impedance_formula="rho * 0.1",
    )
    net.buses["F"].type = bus_type
    net.buses["F"].calculate_impedance(net.frequencies)
    points = [
        gi.SweepPoint(label=f"rho{r:g}", bus_rho={"F": r}, parameters={"rho": r})
        for r in (50.0, 500.0)
    ]
    study = gi.run_sweep(net, fault="F", points=points)
    frame = (
        study.buses()
        .filter((pl.col("bus_name") == "F") & (pl.col("frequency_Hz") == "RMS"))
        .sort("rho")
    )
    assert frame["EPR_V"][0] < frame["EPR_V"][1]
    assert net.buses["F"].specific_earth_resistance == 100.0


def test_the_sweep_carries_the_cut_decomposition_along():
    net = _network()
    study = gi.run_sweep(
        net,
        fault="F",
        points=_k_points(net),
        cuts=[
            gi.Cut(name="left", branches=["AF"]),
            gi.Cut(name="right", branches=["FR1"]),
        ],
    )
    frame = study.cuts()
    assert set(frame["cut"]) == {"left", "right"}
    assert frame["identity_residual"].max() < 1e-12
    # The side impedances are a network property: identical at every point,
    # while the local electrode is what the sweep moved.
    for cut in ("left", "right"):
        values = frame.filter(
            (pl.col("cut") == cut) & (pl.col("frequency_Hz") == 50.0)
        )["Z_side_Ohm"].to_list()
        assert values == pytest.approx([values[0]] * len(values), rel=1e-9)
    local = sorted(
        frame.filter(pl.col("frequency_Hz") == 50.0)["Z_local_Ohm"].to_list()
    )
    # k1 spans a factor of ten; the small k2/k3 reactance terms keep the
    # magnitude ratio just under it.
    assert 9.5 < local[-1] / local[0] < 10.0


def test_both_reduction_factors_travel_in_the_impedance_frame():
    net = _network()
    study = gi.run_sweep(net, fault="F", points=_k_points(net))
    frame = study.impedances().filter(pl.col("frequency_Hz") == 50.0).sort("k1")
    r_epr = frame["reduction_factor"].to_list()
    r_current = frame["reduction_factor_current"].to_list()
    # Blind to the fault bus by construction ...
    assert r_epr == pytest.approx([r_epr[0]] * len(r_epr), rel=1e-9)
    # ... while the current-based one is strictly ordered by the electrode.
    assert all(a > b for a, b in zip(r_current, r_current[1:]))


# --- statistics --------------------------------------------------------------


def test_summarize_reports_count_spread_quantiles_and_extremes():
    net = _network()
    study = gi.run_sweep(net, fault="F", points=_k_points(net))
    frame = study.buses().filter(pl.col("frequency_Hz") == "RMS")
    summary = gi.summarize(frame, "EPR_V", by=["bus_name"])
    assert set(summary.columns) == {
        "bus_name", "n", "n_null", "mean", "std", "min", "p05", "p50", "p95", "max"
    }
    assert summary.height == len(BUSES)
    assert summary["bus_name"].to_list() == sorted(BUSES)
    row = summary.filter(pl.col("bus_name") == "F")
    assert row["n"][0] == 3
    assert row["min"][0] <= row["p50"][0] <= row["max"][0]


def test_summarize_without_grouping_reduces_to_one_row():
    frame = pl.DataFrame({"x": [1.0, 2.0, 3.0, 4.0]})
    summary = gi.summarize(frame, "x", quantiles=(0.5,))
    assert summary.height == 1
    assert summary["p50"][0] == pytest.approx(2.5)
    assert summary["max"][0] == pytest.approx(4.0)


def test_summarize_explains_the_string_frequency_column():
    """``res_buses`` types ``frequency_Hz`` as a string because of the RMS marker
    row -- the error says so rather than just refusing."""
    net = _network()
    study = gi.run_sweep(net, fault="F", points=_k_points(net))
    with pytest.raises(ValueError, match="RMS"):
        gi.summarize(study.buses(), "frequency_Hz")


def test_summarize_names_a_missing_column():
    frame = pl.DataFrame({"x": [1.0]})
    with pytest.raises(ValueError, match="not in the frame"):
        gi.summarize(frame, "y")


def test_classify_bins_on_the_conservative_side_of_an_edge():
    frame = pl.DataFrame({"EPR_V": [10.0, 80.0, 80.001, 200.0, None]})
    classified = gi.classify(
        frame, "EPR_V", [80.0, 150.0], labels=["ok", "check", "exceeded"]
    )
    assert classified["class"].to_list() == [
        "ok",
        "ok",  # exactly on the edge stays in the lower band
        "check",
        "exceeded",
        None,  # a null value is not forced into the lowest band
    ]


def test_classify_builds_readable_default_labels():
    frame = pl.DataFrame({"x": [1.0, 5.0, 20.0]})
    classified = gi.classify(frame, "x", [2.0, 10.0])
    assert classified["class"].to_list() == ["<= 2", "2 - 10", "> 10"]


def test_classify_rejects_an_ambiguous_edge_list():
    frame = pl.DataFrame({"x": [1.0]})
    with pytest.raises(ValueError, match="strictly increasing"):
        gi.classify(frame, "x", [10.0, 5.0])
    with pytest.raises(ValueError, match="at least one edge"):
        gi.classify(frame, "x", [])


def test_classify_checks_the_label_count():
    frame = pl.DataFrame({"x": [1.0]})
    with pytest.raises(ValueError, match="2 classes"):
        gi.classify(frame, "x", [5.0], labels=["only one"])


def test_sweep_then_classify_then_summarize_composes():
    """The three pieces are meant to chain: solve a grid, band the result, count
    how many points land in each band."""
    net = _network()
    study = gi.run_sweep(net, fault="F", points=_k_points(net))
    frame = study.buses().filter(
        (pl.col("bus_name") == "F") & (pl.col("frequency_Hz") == "RMS")
    )
    banded = gi.classify(frame, "EPR_V", [20.0, 60.0], labels=["low", "mid", "high"])
    counts = gi.summarize(banded, "EPR_V", by=["class"])
    assert counts["n"].sum() == 3
    assert set(counts["class"]).issubset({"low", "mid", "high"})

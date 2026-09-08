"""
Audit pass 18 -- ``run_sweep`` really does leave the network as it found it.

``run_sweep``'s docstring promises the network is "left exactly as found,
whatever happens". Four ways it was not:

* **S2** -- ``_apply`` mutated bus after bus and raised on the first unknown
  name; its return value never reached ``saved``, so the ``finally`` block
  skipped the restore entirely and every later point of the sweep was solved
  on a network that still carried the failed point's overrides.
* **S6** -- a ``bus_rho`` override re-evaluates the bus type's formulas, which
  also rewrites the lumped ``R`` / ``L`` / ``C`` fields. The restore put the
  impedance table back but not those, so the state-space transient solver saw
  the swept soil resistivity afterwards.
* **S7** -- ``bus_impedance`` and ``bus_rho`` on the *same* bus: the rho branch
  snapshotted the table the impedance branch had already written, so the
  override became the "original" and survived the sweep.
* **S8** -- restoring the previously active fault dropped its cached result.

Each test fails on the pre-fix code and passes after it.
"""

from __future__ import annotations

import pytest

import groundinsight as gi
from groundinsight.models.core_models import BranchType, BusType


FREQ = [50.0, 250.0]


def _bus_type():
    """A bus type that also carries the lumped RLC decomposition (S6)."""
    return BusType(
        name="bt",
        system_type="MV",
        voltage_level=20.0,
        impedance_formula="0.1*rho",
        R_formula="0.1*rho",
        L_formula="1e-6*rho",
        C_formula="1e-9*rho",
    )


def _branch_type():
    return BranchType(
        name="brt",
        grounding_conductor=True,
        self_impedance_formula="(0.1+0.2*j)*l",
        mutual_impedance_formula="(0.05+0.1*j)*l",
    )


def _network():
    """Three-bus chain S -- A -- F with one source and two faults."""
    bus_type = _bus_type()
    branch_type = _branch_type()
    net = gi.create_network("net", frequencies=FREQ)
    for bus in ("S", "A", "F"):
        net.add_bus(gi.create_bus(bus, bus_type, 100.0))
    net.add_branch(gi.create_branch("SA", branch_type, "S", "A", 1.0, 100.0))
    net.add_branch(gi.create_branch("AF", branch_type, "A", "F", 1.0, 100.0))
    gi.create_fault("F", "F", {50.0: 1.0, 250.0: 0.2}, active=True, network=net)
    gi.create_fault("FA", "A", {50.0: 1.0, 250.0: 0.2}, network=net)
    gi.create_source("SRC", "S", {50.0: 1000.0, 250.0: 200.0}, network=net)
    return net


def _table(value: complex):
    return {freq: value for freq in FREQ}


# --- S2 ----------------------------------------------------------------------


def test_sweep_restores_after_apply_raises_on_unknown_bus():
    """A point naming an unknown bus must not leave earlier overrides behind."""
    net = _network()
    before = dict(net.buses["A"].impedance)

    bad = gi.SweepPoint(
        label="bad",
        # ``A`` is applied first and is real; ``ghost`` is not in the network.
        bus_impedance={"A": _table(99.0 + 0j), "ghost": _table(1.0 + 0j)},
    )
    good = gi.SweepPoint(label="good", bus_impedance={"A": _table(2.0 + 0j)})

    study = gi.run_sweep(net, fault="F", points=[bad, good])

    assert "bad" in study.failures
    assert study.labels == ["good"]
    # The network is back to its pre-sweep state ...
    assert net.buses["A"].impedance == before
    # ... and the good point produced rows.
    assert study.buses().height > 0


def test_sweep_apply_validates_names_before_mutating():
    """No bus is touched at all when any name in the point is unknown (S2)."""
    from groundinsight.simulation.sweep import _apply, _new_saved

    net = _network()
    before = dict(net.buses["A"].impedance)
    point = gi.SweepPoint(
        label="bad",
        bus_impedance={"A": _table(99.0 + 0j), "ghost": _table(1.0 + 0j)},
    )
    saved = _new_saved()
    with pytest.raises(ValueError, match="ghost"):
        _apply(net, point, saved)
    assert net.buses["A"].impedance == before
    assert saved["order"] == []


def test_sweep_point_after_a_failing_point_sees_the_original_network():
    """The value solved for a later point must not depend on a failed one."""
    net = _network()

    # The good point varies bus S; bus A is only ever touched by the failing
    # point, so a missing restore shows up in the good point's result.
    good_point = gi.SweepPoint(label="good", bus_impedance={"S": _table(2.0 + 0j)})
    only_good = gi.run_sweep(net, fault="F", points=[good_point])
    reference = only_good.buses().filter(
        (only_good.buses()["bus_name"] == "F")
    )["EPR_V"].to_list()

    bad = gi.SweepPoint(
        label="bad",
        bus_impedance={"A": _table(99.0 + 0j), "ghost": _table(1.0 + 0j)},
    )
    good = gi.SweepPoint(label="good", bus_impedance={"S": _table(2.0 + 0j)})
    mixed = gi.run_sweep(net, fault="F", points=[bad, good])
    got = mixed.buses().filter((mixed.buses()["bus_name"] == "F"))["EPR_V"].to_list()

    assert got == pytest.approx(reference)


# --- S6 ----------------------------------------------------------------------


def test_bus_rho_override_restores_lumped_rlc_fields():
    """``bus_rho`` re-evaluates R/L/C; the restore has to put them back."""
    net = _network()
    before = {
        "R": dict(net.buses["A"].R),
        "L": dict(net.buses["A"].L),
        "C": dict(net.buses["A"].C),
        "rho": net.buses["A"].specific_earth_resistance,
    }
    assert before["R"]  # the fixture really does populate them

    gi.run_sweep(
        net,
        fault="F",
        points=[gi.SweepPoint(label="wet", bus_rho={"A": 500.0})],
    )

    assert net.buses["A"].specific_earth_resistance == before["rho"]
    assert net.buses["A"].R == before["R"]
    assert net.buses["A"].L == before["L"]
    assert net.buses["A"].C == before["C"]


# --- S7 ----------------------------------------------------------------------


def test_bus_impedance_and_bus_rho_on_the_same_bus_both_restore():
    """The rho branch must not snapshot the impedance branch's override."""
    net = _network()
    before = dict(net.buses["A"].impedance)

    gi.run_sweep(
        net,
        fault="F",
        points=[
            gi.SweepPoint(
                label="both",
                bus_impedance={"A": _table(99.0 + 0j)},
                bus_rho={"A": 500.0},
            )
        ],
    )

    assert net.buses["A"].impedance == before
    assert net.buses["A"].specific_earth_resistance == 100.0


# --- S8 ----------------------------------------------------------------------


def test_sweep_keeps_the_result_of_the_previously_active_fault():
    """Re-activating the previous fault must not delete its cached result."""
    net = _network()
    gi.run_fault(net, "FA")
    assert "FA" in net.results
    before = net.results["FA"]

    gi.run_sweep(
        net,
        fault="F",
        points=[gi.SweepPoint(label="p", bus_impedance={"A": _table(2.0 + 0j)})],
    )

    assert net.active_fault == "FA"
    assert "FA" in net.results
    assert net.results["FA"] is before


# --- S16 ---------------------------------------------------------------------


def test_module_docstring_example_of_rho_f_points_is_callable():
    """The documented call in ``sweep.py``'s module docstring must work."""
    net = _network()
    points = gi.rho_f_points(
        bus="F",
        k_vectors={
            f"k1={k1:g}": (k1, 1e-4, 3e-4, 0.0, 0.0)
            for k1 in (0.01, 0.02, 0.05, 0.1)
        },
        frequencies=net.frequencies,
        rho=100.0,
    )
    assert [p.label for p in points] == [
        "k1=0.01",
        "k1=0.02",
        "k1=0.05",
        "k1=0.1",
    ]

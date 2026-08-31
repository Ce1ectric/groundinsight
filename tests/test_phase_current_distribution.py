"""
Phase-current distribution over rings, meshes and parallel branches.

Background
----------
Until 0.5.0 the phase current per branch was derived by walking the enumerated
source-to-fault paths and giving *every* branch on any path the **full** source
current, scaled by ``Branch.parallel_coefficient``. In a network without cycles
that is exactly right -- there is only one route, so the full current takes it.
As soon as a ring, a mesh or a second parallel cable exists, the same current is
handed out more than once instead of being divided, and the mutual injections it
drives are multiplied with it. For a symmetric ring at the default coefficient of
1.0 the two contributions cancel the source exactly and the whole network solves
to ``EPR = 0 V``, ``r = 0`` and ``Z_G = None`` -- a degenerate result reported
with nothing louder than a log line.

``phase_current_mode="auto"`` -- the default since 0.6.0 -- instead solves a
reduced phase-conductor network per source with the fault bus as reference and
reads the branch currents off that solution, which divides the current the way
the topology and the conductor impedances say it divides.

The tests below pin, in order: that radial networks are unaffected by the change,
that the meshed cases now reproduce the hand-tuned reference, that the result no
longer depends on the order the branches were declared in, that
``phase_impedance_formula`` actually drives the split, and that the legacy mode
is still reachable and still says what it does.
"""

from __future__ import annotations

import logging

import pytest

import groundinsight as gi
from groundinsight.models.core_models import BusType, BranchType


FREQ = [50.0]

# Reference values, computed once on a radial two-branch chain with
# Z_bus = 10 Ohm, Z_self = (0.1 + 0.2j)*l, Z_mutual = (0.05 + 0.1j)*l, l = 1 km
# and I_source = 1000 A at 50 Hz. Z_mutual / Z_self = 0.5 everywhere, so the
# reduction factor is 0.5 for every topology built from this branch type.
EPR_RADIAL = 110.6747
EPR_TWO_ROUTES = 55.6208  # ring: the current divides over two routes
EPR_TWO_PARALLEL_CABLES = 27.8808  # plus a doubled shield admittance
REDUCTION_FACTOR = 0.5


def _bus_type() -> BusType:
    return BusType(
        name="bt",
        system_type="MV",
        voltage_level=20.0,
        impedance_formula="10",
    )


def _branch_type(name: str = "brt", phase_formula: str | None = None) -> BranchType:
    return BranchType(
        name=name,
        grounding_conductor=True,
        self_impedance_formula="(0.1+0.2*j)*l",
        mutual_impedance_formula="(0.05+0.1*j)*l",
        phase_impedance_formula=phase_formula,
    )


def _network(branches, buses, *, parallel_coefficient=1.0, branch_types=None):
    """
    Build a network from ``(name, from_bus, to_bus)`` triples.

    ``branch_types`` optionally maps a branch name to its own ``BranchType`` so a
    leg of a ring can be given a different conductor from the other.
    """
    bus_type = _bus_type()
    default_type = _branch_type()
    net = gi.create_network("net", frequencies=FREQ)
    for bus_name in buses:
        net.add_bus(gi.create_bus(bus_name, bus_type, 100.0))
    for branch_name, from_bus, to_bus in branches:
        net.add_branch(
            gi.create_branch(
                branch_name,
                (branch_types or {}).get(branch_name, default_type),
                from_bus,
                to_bus,
                1.0,
                100.0,
                parallel_coefficient=parallel_coefficient,
            )
        )
    return net


def _solve(net, *, fault_bus="F", source_bus="S", **run_kwargs):
    """Run the single fault and return ``(EPR at fault bus, r, Z_G)``."""
    gi.create_fault("F", fault_bus, {50.0: 1.0}, active=True, network=net)
    gi.create_source("SRC", source_bus, {50.0: 1000.0}, network=net)
    gi.run_fault(net, "F", **run_kwargs)
    result = net.results["F"]
    epr = next(b.uepr for b in result.buses if b.name == fault_bus)
    r = (
        list(result.reduction_factor.value.values())[0]
        if result.reduction_factor
        else None
    )
    z_g = (
        list(result.grounding_impedance.value.values())[0]
        if result.grounding_impedance
        else None
    )
    return epr, r, z_g


RADIAL = [("SA", "S", "A"), ("AF", "A", "F")]
RING = [("SA", "S", "A"), ("AF", "A", "F"), ("SB", "S", "B"), ("BF", "B", "F")]
PARALLEL_CABLES = [("C1", "S", "F"), ("C2", "S", "F")]
MESH = [
    ("SA", "S", "A"),
    ("AB", "A", "B"),
    ("BF", "B", "F"),
    ("SB", "S", "B"),
    ("AF", "A", "F"),
]


# --- radial networks are untouched by the change ----------------------------


@pytest.mark.parametrize("mode", ["auto", "paths"])
def test_radial_network_is_identical_in_both_modes(mode):
    """
    Without a cycle there is nothing to divide, so the two modes must agree.

    This is the backwards-compatibility pin: every existing study on a radial
    network keeps its numbers when the default flips to ``"auto"``.
    """
    epr, r, z_g = _solve(
        _network(RADIAL, ["S", "A", "F"]), phase_current_mode=mode
    )
    assert epr == pytest.approx(EPR_RADIAL, rel=1e-6)
    assert r == pytest.approx(REDUCTION_FACTOR, rel=1e-9)
    assert z_g is not None


def test_a_dead_end_branch_carries_no_phase_current():
    """A stub hanging off the route is a dead end, not a second route."""
    net = _network(
        RADIAL + [("A_stub", "A", "STUB")], ["S", "A", "F", "STUB"]
    )
    _solve(net)
    phase_currents = net.electrical_network.phase_currents[50.0]
    assert abs(phase_currents["A_stub"]) == pytest.approx(0.0, abs=1e-9)
    assert abs(phase_currents["AF"]) == pytest.approx(1000.0, rel=1e-9)


# --- the meshed cases the old scheme got wrong ------------------------------


def test_symmetric_ring_reproduces_the_hand_tuned_reference():
    """
    The ring is the case that used to collapse.

    ``auto`` must land on exactly the result a user previously had to reach by
    setting ``parallel_coefficient=0.5`` on every branch by hand.
    """
    auto = _solve(_network(RING, ["S", "A", "B", "F"]))
    hand_tuned = _solve(
        _network(RING, ["S", "A", "B", "F"], parallel_coefficient=0.5),
        phase_current_mode="paths",
    )
    assert auto[0] == pytest.approx(EPR_TWO_ROUTES, rel=1e-6)
    assert auto[0] == pytest.approx(hand_tuned[0], rel=1e-9)
    assert auto[1] == pytest.approx(REDUCTION_FACTOR, rel=1e-9)
    assert complex(auto[2]) == pytest.approx(complex(hand_tuned[2]), rel=1e-9)


def test_the_legacy_mode_still_collapses_the_ring():
    """
    The old behaviour is preserved verbatim under ``phase_current_mode="paths"``.

    Pinning the degenerate result is deliberate: studies produced before 0.6.0
    have to stay reproducible, and the collapse is the reason the default moved.
    """
    epr, r, z_g = _solve(
        _network(RING, ["S", "A", "B", "F"]), phase_current_mode="paths"
    )
    assert epr == pytest.approx(0.0, abs=1e-9)
    assert r == pytest.approx(0.0, abs=1e-12)
    assert z_g is None


def test_two_parallel_cables_halve_the_potential_rise():
    """
    Two identical cables between the same two buses: the phase current divides
    and the shield admittance doubles.
    """
    two = _solve(_network(PARALLEL_CABLES, ["S", "F"]))
    one = _solve(_network([("C1", "S", "F")], ["S", "F"]))
    assert two[0] == pytest.approx(EPR_TWO_PARALLEL_CABLES, rel=1e-6)
    assert one[0] == pytest.approx(EPR_TWO_ROUTES, rel=1e-6)
    # Close to half, but not exactly: the bus grounding impedance sits in the
    # loop and does not halve with the cable.
    assert two[0] == pytest.approx(one[0] / 2.0, rel=1e-2)
    assert two[0] < one[0]


def test_the_result_does_not_depend_on_the_branch_declaration_order():
    """
    In the path-based scheme the first path a branch appeared on fixed its
    direction, so a mesh gave a different answer depending on the order the
    branches were declared in -- including a flipped sign on ``Z_G``. The
    phase-network solve has no enumeration order to depend on.
    """
    forward = _solve(_network(MESH, ["S", "A", "B", "F"]))
    reversed_order = _solve(
        _network(list(reversed(MESH)), ["S", "A", "B", "F"])
    )
    assert forward[0] == pytest.approx(reversed_order[0], rel=1e-9)
    assert forward[1] == pytest.approx(reversed_order[1], rel=1e-9)
    assert complex(forward[2]) == pytest.approx(complex(reversed_order[2]), rel=1e-9)


# --- phase_impedance_formula ------------------------------------------------


def test_phase_impedance_formula_drives_the_split():
    """
    A ring whose two legs carry the same shield but different phase conductors
    must divide the current in inverse proportion to the phase impedance.
    """
    low = _branch_type("low_z_phase", phase_formula="(0.05+0.1*j)*l")
    high = _branch_type("high_z_phase", phase_formula="(0.5+1.0*j)*l")
    net = _network(
        RING,
        ["S", "A", "B", "F"],
        branch_types={"SA": low, "AF": low, "SB": high, "BF": high},
    )
    _solve(net)
    phase_currents = net.electrical_network.phase_currents[50.0]
    left = abs(phase_currents["SA"])
    right = abs(phase_currents["SB"])
    assert left + right == pytest.approx(1000.0, rel=1e-9)
    # Ten times the impedance on the right leg -> one tenth of the current.
    assert left / right == pytest.approx(10.0, rel=1e-6)


def test_the_declared_phase_impedance_is_persisted(tmp_path):
    """The new formula and the evaluated table survive a database round-trip."""
    db = tmp_path / "phase.db"
    gi.start_dbsession(str(db))
    try:
        net = _network(
            RADIAL,
            ["S", "A", "F"],
            branch_types={
                "SA": _branch_type("with_phase", phase_formula="(0.05+0.1*j)*l")
            },
        )
        gi.save_network_to_db(net, overwrite=True)
        loaded = gi.load_network_from_db("net")
    finally:
        gi.close_dbsession()

    branch = loaded.branches["SA"]
    assert branch.type.phase_impedance_formula == "(0.05+0.1*j)*l"
    assert branch.phase_impedance is not None
    assert complex(branch.phase_impedance[50.0]) == pytest.approx(
        complex(0.05, 0.1), rel=1e-12
    )
    # A branch whose type declares nothing keeps the field at None rather than
    # inventing an empty dict, so "declared" stays distinguishable from "zero".
    assert loaded.branches["AF"].type.phase_impedance_formula is None
    assert loaded.branches["AF"].phase_impedance is None


def test_the_declared_phase_impedance_survives_json():
    """The Pydantic JSON round-trip carries the new fields too."""
    net = _network(
        RADIAL,
        ["S", "A", "F"],
        branch_types={
            "SA": _branch_type("with_phase", phase_formula="(0.05+0.1*j)*l")
        },
    )
    restored = type(net).model_validate_json(net.model_dump_json())
    assert restored.branches["SA"].type.phase_impedance_formula == "(0.05+0.1*j)*l"
    assert complex(restored.branches["SA"].phase_impedance[50.0]) == pytest.approx(
        complex(0.05, 0.1), rel=1e-12
    )


# --- islands: the failure that used to be swallowed -------------------------


def test_an_island_no_longer_discards_the_whole_source_contribution():
    """
    Deactivating one leg of a ring orphans a bus. The reduced phase system over
    the *whole* index range is then singular, ``np.linalg.solve`` raised, and the
    ``except LinAlgError: continue`` dropped the entire source -- which removed
    all mutual coupling and reported ``r = 1`` with a doubled EPR. Restricting
    the solve to the fault bus's own galvanic component makes the case ordinary.
    """
    net = _network(RING, ["S", "A", "B", "F"])
    net.branches["SB"].active = False
    net.branches["BF"].active = False
    epr, r, _ = _solve(net)
    # What is left is the plain radial chain S-A-F.
    assert epr == pytest.approx(EPR_RADIAL, rel=1e-6)
    assert r == pytest.approx(REDUCTION_FACTOR, rel=1e-9)


def test_a_source_cut_off_from_the_fault_is_named_not_silently_dropped(caplog):
    """
    An intact shield with an interrupted phase conductor.

    The grounding network still connects source and fault, so the pathfinder
    offers the source to the solver, but no phase current can reach the fault
    and therefore no coupling is induced. That is the correct answer -- and it
    is exactly the configuration the old ``except LinAlgError: continue``
    swallowed, so it is now named.
    """
    open_phase = _branch_type("open_phase", phase_formula="nan")
    net = _network(
        RADIAL, ["S", "A", "F"], branch_types={"AF": open_phase}
    )
    with caplog.at_level(logging.WARNING, logger="groundinsight"):
        epr, r, _ = _solve(net)
    assert any(
        "no phase-conductor connection" in record.getMessage()
        for record in caplog.records
    )
    # No phase current means no mutual injection: r collapses to 1 because the
    # solution with and without coupling are the same solution.
    assert r == pytest.approx(1.0, rel=1e-9)
    assert epr > 0.0


# --- the proxy warning is scoped to the case where the proxy matters --------


def test_the_proxy_warning_stays_quiet_on_a_radial_network(caplog):
    """
    Without a cycle the proxy cannot influence anything, so warning about it
    would only train the user to ignore the log.
    """
    with caplog.at_level(logging.WARNING, logger="groundinsight"):
        _solve(_network(RADIAL, ["S", "A", "F"]))
    assert not [
        r for r in caplog.records if "phase_impedance_formula" in r.getMessage()
    ]


def test_the_proxy_warning_fires_once_on_a_meshed_network(caplog):
    """In a ring the split *is* decided by the impedances, so the proxy is a
    modelling assumption and is announced -- once, not once per frequency."""
    bus_type = _bus_type()
    branch_type = _branch_type()
    net = gi.create_network("net", frequencies=[50.0, 150.0, 250.0])
    for bus_name in ["S", "A", "B", "F"]:
        net.add_bus(gi.create_bus(bus_name, bus_type, 100.0))
    for branch_name, from_bus, to_bus in RING:
        net.add_branch(
            gi.create_branch(branch_name, branch_type, from_bus, to_bus, 1.0, 100.0)
        )
    gi.create_fault("F", "F", {50.0: 1.0, 150.0: 1.0, 250.0: 1.0}, active=True, network=net)
    gi.create_source("SRC", "S", {50.0: 1000.0, 150.0: 100.0, 250.0: 50.0}, network=net)
    with caplog.at_level(logging.WARNING, logger="groundinsight"):
        gi.run_fault(net, "F")
    matching = [
        r for r in caplog.records if "phase_impedance_formula" in r.getMessage()
    ]
    assert len(matching) == 1


# --- mode plumbing ----------------------------------------------------------


def test_an_unknown_mode_is_rejected_by_name():
    net = _network(RADIAL, ["S", "A", "F"])
    gi.create_fault("F", "F", {50.0: 1.0}, active=True, network=net)
    gi.create_source("SRC", "S", {50.0: 1000.0}, network=net)
    with pytest.raises(ValueError, match="phase_current_mode"):
        gi.run_fault(net, "F", phase_current_mode="topology")


@pytest.mark.parametrize(
    "legacy_flag, expected_epr",
    [(True, EPR_TWO_ROUTES), (False, 0.0)],
)
def test_the_deprecated_flag_still_wins_when_passed(legacy_flag, expected_epr, caplog):
    """
    ``auto_parallel_coefficients`` keeps its exact meaning so existing call
    sites -- including ``run_outage_study`` -- are unaffected by the new default,
    and passing it says that it is deprecated.
    """
    with caplog.at_level(logging.WARNING, logger="groundinsight"):
        epr, _, _ = _solve(
            _network(RING, ["S", "A", "B", "F"]),
            auto_parallel_coefficients=legacy_flag,
        )
    assert epr == pytest.approx(expected_epr, abs=1e-4)
    assert any("deprecated" in r.getMessage() for r in caplog.records)

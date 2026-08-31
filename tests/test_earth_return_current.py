"""
The earth-return current ``I_E`` of a fault, and the reduction factor built on it.

The reduction factor is a measured quantity: total earth-return current over
total fault current. What makes ``I_E`` awkward in a bonded cable network is that
the current does not enter the soil in one place -- it spreads along the shields
and leaks into the soil at every bonded station, so ``I_E`` is the sum over all
of them.

Two traps are pinned here, both of which a first implementation fell into:

1. **Summing over every bus gives exactly zero.** Whatever enters the soil leaves
   it again, so Kirchhoff at the whole network forces it. A group has to be
   selected, and the sum over "all buses except the faulted one" is not that
   group -- by the same law it collapses to the electrode current of the faulted
   station alone, understating ``r`` by a factor of 1.8 to 4.7 here.
2. **The group cannot be found by looking for where the potential crosses zero.**
   With the fault one station from the infeed the profile never comes near zero;
   it passes through a shallow minimum and rises again.
"""

from __future__ import annotations

import pytest

import groundinsight as gi
from groundinsight.models.core_models import BusType, BranchType
from groundinsight.utils.earth_current import split_earth_currents


FREQ = 50.0
FEEDER = ["UW", "S1", "S2", "S3", "S4", "S5"]
CABLES = [
    ("K1", "UW", "S1"), ("K2", "S1", "S2"), ("K3", "S2", "S3"),
    ("K4", "S3", "S4"), ("K5", "S4", "S5"),
]


def _feeder(fault_bus):
    bus_type = BusType(
        name="bt", system_type="MV", voltage_level=20.0, impedance_formula="10"
    )
    branch_type = BranchType(
        name="brt",
        grounding_conductor=True,
        self_impedance_formula="(0.1+0.2*j)*l",
        mutual_impedance_formula="(0.05+0.1*j)*l",
    )
    net = gi.create_network("feeder", frequencies=[FREQ])
    for bus in FEEDER:
        net.add_bus(gi.create_bus(bus, bus_type, 100.0))
    for name, from_bus, to_bus in CABLES:
        net.add_branch(
            gi.create_branch(name, branch_type, from_bus, to_bus, 1.0, 100.0)
        )
    gi.create_fault("EF", fault_bus, {FREQ: 1.0}, active=True, network=net)
    gi.create_source("Q", "UW", {FREQ: 1000.0}, network=net)
    gi.run_fault(net, "EF")
    return net


def _electrode_currents(net):
    return {
        b.name: complex(b.ia_freq[FREQ]) for b in net.results["EF"].buses
    }


# --- the two traps -----------------------------------------------------------


@pytest.mark.parametrize("fault_bus", ["S1", "S3", "S5"])
def test_the_electrode_currents_of_all_buses_sum_to_zero(fault_bus):
    """
    Kirchhoff at the whole network. This is why a group has to be selected at
    all, and why "sum over everything" is not an option.
    """
    total = sum(_electrode_currents(_feeder(fault_bus)).values())
    assert abs(total) < 1e-9


@pytest.mark.parametrize(
    "fault_bus, expected_r",
    [("S3", 0.032385), ("S1", 0.009138), ("S5", 0.048327)],
)
def test_the_reduction_factor_sums_every_bus_that_feeds_the_soil(
    fault_bus, expected_r
):
    """
    The reference values were computed by hand from the per-bus electrode
    currents of this feeder. They are between 1.8 and 4.7 times the value a
    fault-bus-only definition produces, which is the whole point of the fix.
    """
    net = _feeder(fault_bus)
    factor = net.results["EF"].reduction_factor
    assert factor.value_current[FREQ] == pytest.approx(expected_r, rel=1e-4)

    fault_bus_only = abs(_electrode_currents(net)[fault_bus]) / 1000.0
    assert factor.value_current[FREQ] > fault_bus_only * 1.5


def test_the_group_is_found_where_the_potential_never_crosses_zero():
    """
    Fault one station from the infeed: ``|EPR|`` runs 91.4, 19.6, 18.7, 18.1,
    17.7, 17.5 V -- a shallow minimum, no crossing. The split works anyway
    because it is made on the direction of each electrode current, not on the
    potential profile.
    """
    net = _feeder("S1")
    eprs = {b.name: b.uepr for b in net.results["EF"].buses}
    assert min(eprs.values()) > 15.0  # nothing anywhere near zero
    factor = net.results["EF"].reduction_factor
    assert factor.earth_buses[FREQ] == ["S1", "S2", "S3", "S4", "S5"]
    assert factor.value_current[FREQ] == pytest.approx(0.009138, rel=1e-4)


def test_the_selected_group_is_reported_and_matches_the_currents():
    """The split is a modelling statement, so it has to be inspectable."""
    net = _feeder("S3")
    factor = net.results["EF"].reduction_factor
    currents = _electrode_currents(net)
    feeding = factor.earth_buses[FREQ]
    # From the fault outwards in every direction, up to where the profile turns.
    assert feeding == ["S2", "S3", "S4", "S5"]
    assert complex(factor.i_earth[FREQ]) == pytest.approx(
        sum(currents[name] for name in feeding), rel=1e-9
    )
    # The rest carries the same sum with the opposite sign, by Kirchhoff.
    rest = sum(v for k, v in currents.items() if k not in feeding)
    assert rest == pytest.approx(-complex(factor.i_earth[FREQ]), rel=1e-9)


def test_i_earth_is_reported_in_amperes_not_only_as_a_ratio():
    net = _feeder("S3")
    factor = net.results["EF"].reduction_factor
    assert abs(complex(factor.i_earth[FREQ])) == pytest.approx(32.385, rel=1e-3)


def test_the_reduction_factor_stays_finite_without_an_electrode_at_the_fault():
    """
    A definition resting on the faulted station alone collapses when that
    station has no electrode. Summed over the whole earthing system it does not:
    the neighbours carry the current.
    """
    net = _feeder("S3")
    response = gi.bus_response(net, fault="EF", bus="S3")
    open_case = response.evaluate(None)
    assert open_case["r_current"][0] > 0.005


# --- the splitting helper ----------------------------------------------------


def test_a_clean_split_picks_the_feeding_side():
    """The reference bus lands in the returning group, the rest feeds."""
    currents = {"A": 10 + 0j, "B": 5 + 0j, "C": -15 + 0j}
    split = split_earth_currents(currents, reference_bus="C")
    assert split.feeding_buses == ["C"]
    assert split.returning_buses == ["A", "B"]
    assert abs(split.i_earth) == pytest.approx(15.0, rel=1e-12)
    assert split.separation == pytest.approx(1.0, rel=1e-12)


def test_the_magnitude_does_not_depend_on_which_group_is_named_feeding():
    """Both groups carry the same sum with opposite signs, so ``|I_E|`` -- the
    only thing the reduction factor uses -- is anchor-independent."""
    currents = {"A": 10 + 0j, "B": 5 + 0j, "C": -15 + 0j}
    from_c = split_earth_currents(currents, reference_bus="C")
    from_a = split_earth_currents(currents, reference_bus="A")
    assert abs(from_a.i_earth) == pytest.approx(abs(from_c.i_earth), rel=1e-12)
    assert from_a.feeding_buses == ["A", "B"]
    assert from_c.feeding_buses == ["C"]


def test_without_an_anchor_the_choice_is_still_reproducible():
    """An unanchored maximum is indifferent between the two groups, so the
    smaller one is named feeding and ties are broken by name -- otherwise the
    reported membership would depend on dictionary order."""
    currents = {"A": 10 + 0j, "B": 5 + 0j, "C": -15 + 0j}
    reversed_order = {k: currents[k] for k in reversed(list(currents))}
    assert (
        split_earth_currents(currents).feeding_buses
        == split_earth_currents(reversed_order).feeding_buses
        == ["C"]
    )


def test_the_split_is_independent_of_the_overall_phase():
    """Rotating every phasor by the same angle is a change of reference, not a
    change of the physics."""
    base = {"A": 10 + 0j, "B": 5 + 0j, "C": -15 + 0j}
    rotated = {k: v * complex(0.6, 0.8) for k, v in base.items()}
    assert abs(split_earth_currents(rotated, reference_bus="C").i_earth) == (
        pytest.approx(abs(split_earth_currents(base, reference_bus="C").i_earth), rel=1e-12)
    )
    assert (
        split_earth_currents(rotated, reference_bus="C").feeding_buses
        == split_earth_currents(base, reference_bus="C").feeding_buses
    )


def test_spread_phasors_lower_the_separation_diagnostic():
    """When the electrode currents are spread in angle, a single scalar
    earth-return current is a coarser description -- and says so."""
    spread = {"A": 10 + 0j, "B": 10j, "C": -10 - 10j}
    split = split_earth_currents(spread)
    assert split.separation < 0.95


def test_the_group_can_be_pinned_by_the_caller():
    split = split_earth_currents(
        {"A": 10 + 0j, "B": 5 + 0j, "C": -15 + 0j}, feeding_buses=["A"]
    )
    assert split.feeding_buses == ["A"]
    assert abs(split.i_earth) == pytest.approx(10.0, rel=1e-12)


def test_buses_without_an_electrode_contribute_nothing():
    with_zeros = split_earth_currents(
        {"A": 10 + 0j, "Z": 0j, "C": -10 + 0j}, reference_bus="C"
    )
    assert "Z" not in with_zeros.feeding_buses + with_zeros.returning_buses
    assert abs(with_zeros.i_earth) == pytest.approx(10.0, rel=1e-12)


def test_a_network_with_no_electrode_current_at_all_returns_none():
    """Distinguished from an earth-return current of zero on purpose: there is
    no earthing system here, which is a different statement."""
    assert split_earth_currents({"A": 0j, "B": 0j}) is None
    assert split_earth_currents({}) is None


def test_a_single_earthed_bus_is_its_own_group():
    split = split_earth_currents({"A": 3 + 4j})
    assert split.feeding_buses == ["A"]
    assert abs(split.i_earth) == pytest.approx(5.0, rel=1e-12)


# --- how the two definitions relate ------------------------------------------


def _two_node(z_electrode):
    """Three stations in a line, electrodes of the given impedance."""
    bus_type = BusType(
        name="bt",
        system_type="MV",
        voltage_level=20.0,
        impedance_formula=str(z_electrode),
    )
    branch_type = BranchType(
        name="brt",
        grounding_conductor=True,
        self_impedance_formula="(0.1+0.2*j)*l",
        mutual_impedance_formula="(0.05+0.1*j)*l",
    )
    net = gi.create_network("line", frequencies=[FREQ])
    for bus in ("A", "B", "C"):
        net.add_bus(gi.create_bus(bus, bus_type, 100.0))
    net.add_branch(gi.create_branch("K1", branch_type, "A", "B", 1.0, 100.0))
    net.add_branch(gi.create_branch("K2", branch_type, "B", "C", 1.0, 100.0))
    gi.create_fault("EF", "C", {FREQ: 1.0}, active=True, network=net)
    gi.create_source("Q", "A", {FREQ: 1000.0}, network=net)
    gi.run_fault(net, "EF")
    return net.results["EF"].reduction_factor


def test_the_two_reduction_factors_differ_by_exactly_the_current_divider():
    """
    They are not two computations of one number.

    ``r_coupling = (Z_s - Z_m)/Z_s`` is the ideally bonded limit -- the
    tabulated cable property, blind to the station earths by construction.
    ``r_current = (Z_s - Z_m)/(Z_s + Z_E)`` is what the earthing system of this
    network actually passes into the soil. Their ratio is the current divider
    ``Z_s/(Z_s + Z_E)`` and nothing else, which this pins to machine precision.
    """
    z_shield = complex(0.2, 0.4)  # two 1 km sections
    for z_electrode in (10.0, 1.0, 0.1, 0.01):
        factor = _two_node(z_electrode)
        divider = factor.value_current[FREQ] / factor.value[FREQ]
        expected = abs(z_shield / (z_shield + 2 * z_electrode))
        assert divider == pytest.approx(expected, rel=1e-9)


def test_they_converge_exactly_as_the_stations_approach_ideal_bonding():
    """The one condition under which the two definitions coincide."""
    assert _two_node(1e-5).value_current[FREQ] == pytest.approx(
        _two_node(1e-5).value[FREQ], rel=1e-4
    )
    # ... and are far apart when the electrodes dominate the shield.
    coarse = _two_node(10.0)
    assert coarse.value_current[FREQ] < coarse.value[FREQ] / 20.0


def test_the_coupling_factor_reproduces_the_textbook_closed_form():
    """``r = |1 - Z_m/Z_s| = 0.5`` here, whatever the stations are earthed with
    -- which is precisely why it cannot also be the earth-current share."""
    for z_electrode in (0.01, 1.0, 10.0):
        assert _two_node(z_electrode).value[FREQ] == pytest.approx(0.5, rel=1e-9)


# --- the EN 50522 chain U_E = 3*I_0 * Z_E * r --------------------------------


@pytest.mark.parametrize("z_electrode", [10.0, 2.0, 0.5])
def test_the_en50522_chain_closes_in_the_model(z_electrode):
    """
    ``U_E = 3*I_0 * Z_E * r`` with ``r = |I_E| / |3*I_0|``.

    All three routes to ``r`` -- the current ratio, the voltage over
    ``Z_E * 3I_0``, and the reported factor -- have to be the same number. This
    is the identity the norm asserts, checked against the model rather than
    assumed.
    """
    bus_type = BusType(
        name="bt",
        system_type="MV",
        voltage_level=20.0,
        impedance_formula=str(z_electrode),
    )
    branch_type = BranchType(
        name="brt",
        grounding_conductor=True,
        self_impedance_formula="(0.1+0.2*j)*l",
        mutual_impedance_formula="(0.05+0.1*j)*l",
    )
    net = gi.create_network("feeder", frequencies=[FREQ])
    for bus in FEEDER:
        net.add_bus(gi.create_bus(bus, bus_type, 100.0))
    for name, from_bus, to_bus in CABLES:
        net.add_branch(
            gi.create_branch(name, branch_type, from_bus, to_bus, 1.0, 100.0)
        )
    gi.create_fault("EF", "S3", {FREQ: 1.0}, active=True, network=net)
    gi.create_source("Q", "UW", {FREQ: 1000.0}, network=net)
    gi.run_fault(net, "EF")

    factor = net.results["EF"].reduction_factor
    i_fault = 1000.0
    u_e = abs(complex(factor.u_earthing[FREQ]))
    z_e = abs(complex(factor.z_earthing[FREQ]))
    i_e = abs(complex(factor.i_earth[FREQ]))

    r_reported = factor.value_current[FREQ]
    r_from_currents = i_e / i_fault
    r_from_voltage = u_e / (z_e * i_fault)

    assert r_from_currents == pytest.approx(r_reported, rel=1e-12)
    assert r_from_voltage == pytest.approx(r_reported, rel=1e-12)
    # ... and the chain itself, written the way the norm writes it.
    assert i_fault * z_e * r_reported == pytest.approx(u_e, rel=1e-12)


def test_the_earthing_impedance_is_not_just_the_electrodes_in_parallel():
    """
    The shield sections between the bonded stations add to ``Z_E``.

    Worth pinning because it is the quiet reason a hand calculation from the
    electrode values alone comes out low.
    """
    net = _feeder("S3")
    factor = net.results["EF"].reduction_factor
    z_e = abs(complex(factor.z_earthing[FREQ]))
    n_stations = len(factor.earth_buses[FREQ])
    pure_parallel = 10.0 / n_stations
    assert z_e > pure_parallel
    assert z_e == pytest.approx(3.2983, rel=1e-3)
    assert pure_parallel == pytest.approx(2.5, rel=1e-12)


def test_the_two_factors_use_different_reference_cases():
    """
    Where the factor of fifteen comes from.

    The norm's ``r = 1`` means the whole fault current flows through ``Z_E``:
    ``U_E(r=1) = Z_E * 3I_0``. The model's "no mutual coupling" leaves the
    shield in place as a metallic return path, so its reference voltage is a
    different -- and much smaller -- number. Both are correct answers to
    different questions.
    """
    net = _feeder("S3")
    factor = net.results["EF"].reduction_factor
    u_e = abs(complex(factor.u_earthing[FREQ]))
    z_e = abs(complex(factor.z_earthing[FREQ]))

    u_reference_norm = z_e * 1000.0            # r = 1: everything through Z_E
    u_reference_no_coupling = u_e / factor.value[FREQ]  # coupling removed only

    assert u_reference_norm == pytest.approx(3298.3, rel=1e-3)
    assert u_reference_no_coupling == pytest.approx(213.6, rel=1e-3)
    # The ratio of the two reference voltages is exactly the ratio of the two
    # factors -- which is the whole discrepancy, and nothing else.
    assert u_reference_norm / u_reference_no_coupling == pytest.approx(
        factor.value[FREQ] / factor.value_current[FREQ], rel=1e-9
    )


def test_the_grounding_impedance_is_now_the_normative_z_e():
    """
    ``ResultGroundingImpedance.value`` is ``Z_E`` in the EN 50522 sense.

    Until this change it was ``u_EPR / (r_coupling * I_F)``, which mixed the
    coupling ratio into an impedance and reported 0.219 Ohm where the earthing
    system of the bonded group actually presents 3.298 Ohm -- a factor of
    fifteen, the same one that separates the two reduction factors.
    """
    net = _feeder("S3")
    result = net.results["EF"]
    factor = result.reduction_factor
    z_g = abs(complex(result.grounding_impedance.value[FREQ]))

    assert z_g == pytest.approx(abs(complex(factor.z_earthing[FREQ])), rel=1e-12)
    assert z_g == pytest.approx(3.2983, rel=1e-3)
    # The norm's chain closes on the reported grounding impedance itself.
    assert 1000.0 * z_g * factor.value_current[FREQ] == pytest.approx(
        abs(complex(factor.u_earthing[FREQ])), rel=1e-12
    )

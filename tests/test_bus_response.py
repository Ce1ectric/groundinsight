"""
Describing a location in the network without knowing the electrode there.

The claim under test is strong and therefore checked against real solves rather
than argued: two solves with the local electrode removed determine the network's
response for **every** electrode, exactly. If that holds, the two extremes -- no
electrode and an ideal one -- are the endpoints of one curve rather than two
isolated samples, and everything in between follows in closed form.

The tests below pin, in order: that the closed form reproduces genuine solves to
machine precision including for a complex electrode, that both endpoints are
exact limits and not numerical stand-ins, that the site characterisation really
is independent of whatever electrode happened to be installed when it was built,
and that the EPR-based reduction factor is constant along the whole curve -- the
invariance the sensitivity study runs into, here derived from the algebra.
"""

from __future__ import annotations

import numpy as np
import polars as pl
import pytest

import groundinsight as gi
from groundinsight.models.core_models import BusType, BranchType, ComplexNumber


FREQ = [50.0, 250.0]
CHAIN = [("SA", "S", "A"), ("AF", "A", "F"), ("FR1", "F", "R1")]
BUSES = ["S", "A", "F", "R1"]


def _network(z_bus=None, *, bus_formula="10", solve=True):
    bus_type = BusType(
        name="bt", system_type="MV", voltage_level=20.0,
        impedance_formula=bus_formula,
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
    gi.create_fault("F", "F", {f: 1.0 for f in FREQ}, active=True, network=net)
    gi.create_source("SRC", "S", {50.0: 1000.0, 250.0: 200.0}, network=net)
    if z_bus is not None:
        net.buses["F"].impedance = {
            f: ComplexNumber(real=complex(z_bus).real, imag=complex(z_bus).imag)
            for f in FREQ
        }
    if solve:
        gi.run_fault(net, "F")
    return net


def _solved_values(z_bus, freq=50.0):
    """EPR and current-based reduction factor from a genuine solve."""
    net = _network(z_bus)
    result = net.results["F"]
    epr = abs(complex(next(b.uepr_freq[freq] for b in result.buses if b.name == "F")))
    return epr, result.reduction_factor.value_current[freq]


# --- the closed form against real solves -------------------------------------


@pytest.mark.parametrize(
    "z_bus", [0.05, 1.0, 10.0, 100.0, 500.0, complex(4.0, 9.0)]
)
def test_the_closed_form_reproduces_a_genuine_solve(z_bus):
    """
    The load-bearing test.

    A complex electrode is in the list on purpose: a formulation that only
    happened to work for real impedances would pass everything else.
    """
    response = gi.bus_response(_network(10.0), fault="F")
    predicted = response.evaluate(z_bus).filter(pl.col("frequency_Hz") == 50.0)
    epr, r_current = _solved_values(z_bus)
    assert predicted["EPR_V"][0] == pytest.approx(epr, rel=1e-12)
    assert predicted["r_current"][0] == pytest.approx(r_current, rel=1e-9)


def test_every_bus_is_reproduced_not_just_the_one_being_varied():
    """The response covers the whole network, so the potential rise at the other
    stations has to come out right too."""
    response = gi.bus_response(_network(10.0), fault="F")
    predicted = response.evaluate(2.5).filter(pl.col("frequency_Hz") == 50.0)
    solved = _network(2.5).results["F"]
    for name in BUSES:
        if name == "F":
            continue
        expected = abs(complex(next(b.uepr_freq[50.0] for b in solved.buses if b.name == name)))
        assert predicted[f"EPR_{name}_V"][0] == pytest.approx(expected, rel=1e-12)


def test_the_characterisation_does_not_depend_on_the_electrode_it_was_built_with():
    """
    The whole point: ``Z_network`` and the open-circuit response describe the
    *location*, so building the response from a network with a 0.5 Ohm electrode
    and from one with 200 Ohm must give the same object.
    """
    low = gi.bus_response(_network(0.5), fault="F")
    high = gi.bus_response(_network(200.0), fault="F")
    for freq in FREQ:
        assert low.z_network[freq] == pytest.approx(high.z_network[freq], rel=1e-12)
        for name in BUSES:
            assert low.u_open[freq][name] == pytest.approx(
                high.u_open[freq][name], rel=1e-12
            )
            assert low.z_column[freq][name] == pytest.approx(
                high.z_column[freq][name], rel=1e-12
            )


# --- the two extremes --------------------------------------------------------


def test_an_ideal_electrode_gives_exactly_zero_and_not_a_small_number():
    """
    ``Z_B = 0`` is evaluated as a limit, so it is available at all -- the solver
    itself rejects a zero impedance above 0 Hz because it cannot be inverted.
    """
    response = gi.bus_response(_network(10.0), fault="F")
    ideal = response.evaluate(0.0)
    assert ideal["EPR_V"].max() < 1e-9
    assert ideal["Z_driving_point_Ohm"].max() == pytest.approx(0.0, abs=1e-15)
    # The electrode current is the finite limit, not zero and not infinite.
    assert ideal["I_electrode_A"].min() > 0.0
    assert np.isfinite(ideal["I_electrode_A"].to_numpy()).all()


def test_no_electrode_gives_the_network_impedance_itself():
    response = gi.bus_response(_network(10.0), fault="F")
    for spelling in (None, float("inf")):
        frame = response.evaluate(spelling).filter(pl.col("frequency_Hz") == 50.0)
        assert frame["Z_driving_point_Ohm"][0] == pytest.approx(
            frame["Z_network_Ohm"][0], rel=1e-12
        )
        assert frame["I_electrode_A"][0] == pytest.approx(0.0, abs=1e-12)


def test_the_extremes_bracket_every_electrode_in_between():
    response = gi.bus_response(_network(10.0), fault="F")
    extremes = response.extremes().filter(pl.col("frequency_Hz") == 50.0)
    lower = extremes.filter(pl.col("case") == "ideal")["EPR_V"][0]
    upper = extremes.filter(pl.col("case") == "open")["EPR_V"][0]
    for z_bus in (0.01, 0.5, 3.0, 25.0, 1000.0):
        epr = response.evaluate(z_bus).filter(pl.col("frequency_Hz") == 50.0)["EPR_V"][0]
        assert lower <= epr <= upper * (1 + 1e-12)


def test_the_driving_point_follows_the_parallel_law_exactly():
    """``Z_dp = 1/(Y_B + 1/Z_net)`` -- the local electrode is a plain shunt."""
    response = gi.bus_response(_network(10.0), fault="F")
    z_net = response.z_network[50.0]
    for z_bus in (0.1, 4.0, 250.0, complex(2.0, -3.0)):
        expected = 1.0 / (1.0 / complex(z_bus) + 1.0 / z_net)
        assert response.driving_point(z_bus, 50.0) == pytest.approx(
            expected, rel=1e-12
        )


def test_the_worst_passive_case_is_the_reactive_resonance():
    """
    The largest driving-point magnitude over all passive electrodes is not at
    the open end but where a reactive electrode cancels the network's
    susceptance. It is barely above ``|Z_net|``, which is exactly why it is
    worth reporting rather than assuming.
    """
    response = gi.bus_response(_network(10.0), fault="F")
    worst = response.worst_case_electrode(50.0)
    z_net = response.z_network[50.0]
    y_net = 1.0 / z_net
    assert worst["z_driving_point"] == pytest.approx(1.0 / y_net.real, rel=1e-12)
    assert abs(worst["z_driving_point"]) >= abs(z_net)
    assert worst["z_bus"].real == pytest.approx(0.0, abs=1e-12)
    # No passive electrode beats it.
    for z_bus in (0.05, 1.0, 50.0, None, complex(1.0, -80.0), complex(0.0, -50.0)):
        assert abs(response.driving_point(z_bus, 50.0)) <= abs(
            worst["z_driving_point"]
        ) * (1 + 1e-9)


def test_the_extremes_frame_carries_all_three_cases():
    response = gi.bus_response(_network(10.0), fault="F")
    frame = response.extremes()
    assert set(frame["case"]) == {"open", "ideal", "worst_passive"}
    assert frame.height == 3 * len(FREQ)


# --- what is and is not invariant --------------------------------------------


def test_the_epr_reduction_factor_is_constant_along_the_whole_curve():
    """
    Derived, not just observed: ``u_b(Y_B) = u_0b / (1 + Y_B Z_net)`` holds with
    and without mutual coupling alike, so the quotient that defines ``r`` has
    the same denominator top and bottom and cancels exactly.
    """
    response = gi.bus_response(_network(10.0), fault="F")
    values = response.sweep([0.01, 1.0, 100.0, None, 0.0])["r_epr"].to_list()
    assert values == pytest.approx([values[0]] * len(values), rel=1e-12)


def test_the_current_reduction_factor_is_ordered_by_the_electrode():
    """A better electrode takes more of the fault current out of the earth-return
    path, so the current-based factor grows as the electrode improves."""
    response = gi.bus_response(_network(10.0), fault="F")
    frame = response.sweep([1000.0, 100.0, 10.0, 1.0, 0.1]).filter(
        pl.col("frequency_Hz") == 50.0
    )
    values = frame["r_current"].to_list()
    assert all(a < b for a, b in zip(values, values[1:]))


def test_an_ideal_electrode_at_the_fault_raises_the_potential_elsewhere():
    """
    Worth having as a test because it is counter-intuitive and is the kind of
    statement the extremes exist to produce: bonding the faulted station harder
    pushes potential onto its neighbours.
    """
    response = gi.bus_response(_network(10.0), fault="F")
    extremes = response.extremes().filter(pl.col("frequency_Hz") == 50.0)
    open_case = extremes.filter(pl.col("case") == "open")["EPR_S_V"][0]
    ideal_case = extremes.filter(pl.col("case") == "ideal")["EPR_S_V"][0]
    assert ideal_case > open_case * 1.5


# --- contract ----------------------------------------------------------------


def test_an_unsolved_fault_is_named():
    net = _network(solve=False)
    with pytest.raises(ValueError, match="run_fault first"):
        gi.bus_response(net, fault="F")


def test_an_unknown_bus_is_named_with_the_alternatives():
    net = _network(10.0)
    with pytest.raises(ValueError, match="Active buses"):
        gi.bus_response(net, fault="F", bus="nowhere")


def test_a_location_whose_earth_reference_is_its_own_electrode_is_refused():
    """
    Removing the electrode has to leave the network with a path to earth,
    otherwise there is nothing to characterise independently of it -- and the
    message says so rather than reporting a singular matrix.
    """
    net = _network(10.0, solve=False)
    for name in ("S", "A", "R1"):
        net.buses[name].impedance = {f: ComplexNumber(real=float("inf"), imag=0.0) for f in FREQ}
    gi.run_fault(net, "F")
    with pytest.raises(ValueError, match="no path to reference earth"):
        gi.bus_response(net, fault="F")


def test_the_response_can_be_built_for_a_bus_away_from_the_fault():
    """The characterisation is of a *location*, which need not be the faulted
    one -- a neighbouring station is just as legitimate a question."""
    net = _network(10.0)
    response = gi.bus_response(net, fault="F", bus="A")
    assert response.bus == "A"
    predicted = response.evaluate(10.0).filter(pl.col("frequency_Hz") == 50.0)
    solved = abs(
        complex(next(b.uepr_freq[50.0] for b in net.results["F"].buses if b.name == "A"))
    )
    assert predicted["EPR_V"][0] == pytest.approx(solved, rel=1e-12)


def test_sweep_checks_the_label_count():
    response = gi.bus_response(_network(10.0), fault="F")
    with pytest.raises(ValueError, match="one to one"):
        response.sweep([1.0, 2.0], labels=["only one"])


def test_sweep_stacks_without_solving_anything():
    response = gi.bus_response(_network(10.0), fault="F")
    values = [None, 0.0, 0.1, 1.0, 10.0, 100.0, 1000.0]
    frame = response.sweep(values)
    assert frame.height == len(values) * len(FREQ)
    assert frame["EPR_V"].null_count() == 0

# tests/test_audit_pass18_response_state.py
"""
Audit pass 18 -- the analysis layer against the state it reads from.

Three findings share one root: :func:`groundinsight.bus_response` reads the
*assembled* nodal system back out of the network, and nothing in 0.6.0 made
sure that system still described the network in hand, nor that the quantities
derived from it were the ones the solver itself reports.

- **A5** -- the inverse searches restored ``rho``, impedances, results, paths
  and the active fault in their ``finally`` blocks, but not
  ``network.electrical_network``. The network was left carrying the ``Y`` of
  the last bisection step next to the restored ``rho``, and ``bus_response``
  read a *negative* driving-point impedance out of that pair without any error
  or warning.
- **A2** -- ``BusResponse.evaluate`` computed ``Z_G = u_b / (r * I_F)``, the
  definition 0.6.0 replaced, and did it with the voltage of the *varied* bus
  rather than the fault bus. Against ``run_fault`` that is a factor of 39.
- **A1** -- ``r_epr`` was carried through as an invariant. It is one only when
  the varied bus is the fault bus; away from it the factor moves with the
  electrode by 18 %.

Each test compares against a genuine ``run_fault`` on the same network rather
than against a stored number, so the assertion is "the closed form and the
solver agree", not "the closed form still returns what it returned".
"""

from __future__ import annotations

import numpy as np
import polars as pl
import pytest

import groundinsight as gi
from groundinsight.analysis.response import BusResponse
from groundinsight.models.core_models import BusType, BranchType, ComplexNumber


FREQ = [50.0]
BUSES = ["B0", "B1", "B2", "B3"]


def _network(z_bus=None, *, bus="B1", rho=100.0, bus_formula="10"):
    """
    Four-bus chain, fault in the middle, two branch types with different
    ``Z_m/Z_s``. The asymmetry matters: with one branch type the EPR-based
    reduction factor comes out invariant by accident and A1 stays invisible.
    """
    bus_type = BusType(
        name="bt", system_type="MV", voltage_level=20.0,
        impedance_formula=bus_formula,
    )
    strong = BranchType(
        name="strong", grounding_conductor=True,
        self_impedance_formula="(0.1+0.2*j)*l",
        mutual_impedance_formula="(0.05+0.1*j)*l",
    )
    weak = BranchType(
        name="weak", grounding_conductor=True,
        self_impedance_formula="(0.2+0.5*j)*l",
        mutual_impedance_formula="(0.02+0.05*j)*l",
    )
    net = gi.create_network("net", frequencies=FREQ)
    for name in BUSES:
        net.add_bus(gi.create_bus(name, bus_type, rho))
    net.add_branch(gi.create_branch("b01", strong, "B0", "B1", 1.0, rho))
    net.add_branch(gi.create_branch("b12", weak, "B1", "B2", 1.0, rho))
    net.add_branch(gi.create_branch("b23", strong, "B2", "B3", 1.0, rho))
    gi.create_fault("F", "B2", {f: 1.0 for f in FREQ}, active=True, network=net)
    gi.create_source("SRC", "B0", {50.0: 1000.0}, network=net)
    if z_bus is not None:
        net.buses[bus].impedance = {
            f: ComplexNumber(real=complex(z_bus).real, imag=complex(z_bus).imag)
            for f in FREQ
        }
    gi.run_fault(net, "F")
    return net


def _row(response, z_bus, freq=50.0):
    return response.evaluate(z_bus).filter(pl.col("frequency_Hz") == freq)


# --- A5: the assembled system after a search ---------------------------------


def test_a_rho_search_leaves_the_assembled_system_matching_the_network():
    """
    The load-bearing A5 test. Every bisection step replaces the assembled ``Y``;
    the ``finally`` block restores ``rho`` but used to leave that ``Y`` behind.
    Reading a response off the pair produced a negative driving-point impedance
    -- a passive network cannot have one, so the sign alone convicts it.
    """
    net = _network(bus_formula="rho*0.1")
    before = gi.bus_response(net, fault="F", bus="B1").z_network[50.0]

    gi.find_max_rho_scaling(net, "F", BUSES, u_max=200.0)

    after = gi.bus_response(net, fault="F", bus="B1").z_network[50.0]
    assert after.real > 0, "driving-point impedance of a passive network"
    assert after == pytest.approx(before, rel=1e-12)


def test_a_rho_f_sweep_leaves_the_assembled_system_matching_the_network():
    """The same for ``evaluate_max_epr_under_k``, which drives the k-form
    sweep and the ``find_max_rho_f_scaling`` bisection above it."""
    net = _network()
    before = gi.bus_response(net, fault="F", bus="B1").z_network[50.0]

    gi.evaluate_max_epr_under_k(net, ["B0", "B1"], k=(0.05, 0.0, 0.0, 0.0, 0.0))

    after = gi.bus_response(net, fault="F", bus="B1").z_network[50.0]
    assert after.real > 0
    assert after == pytest.approx(before, rel=1e-12)


def test_the_search_restores_an_absent_assembled_system_as_absent():
    """A network that never ran a fault gets no matrices handed to it by the
    search either -- a stale ``Y`` is worse than none, because the readers
    above raise on the second and not on the first."""
    net = _network()
    net.electrical_network = None
    gi.find_max_rho_scaling(net, "F", BUSES, u_max=1e6)
    assert net.electrical_network is None


def test_a_stale_assembled_system_is_refused_by_name():
    """
    An impedance changed by hand after ``run_fault`` does not reach the
    assembled matrices. The response would silently mix the two states, so it
    is refused with a message that names the cause and the remedy.
    """
    net = _network()
    net.buses["B0"].impedance = {
        f: ComplexNumber(real=0.5, imag=0.0) for f in FREQ
    }
    with pytest.raises(ValueError, match="different model values"):
        gi.bus_response(net, fault="F", bus="B1")
    # ... and running the fault again makes it answerable.
    gi.run_fault(net, "F")
    assert gi.bus_response(net, fault="F", bus="B1").z_network[50.0].real > 0


def test_the_fingerprint_covers_active_flags_and_the_active_fault():
    net = _network()
    electrical = net.electrical_network
    assert electrical.matches_network_state()
    net.buses["B3"].active = False
    assert not electrical.matches_network_state()
    net.buses["B3"].active = True
    assert electrical.matches_network_state()


# --- A2: Z_G against the solver ----------------------------------------------


@pytest.mark.parametrize("z_bus", [0.1, 10.0, 1000.0])
@pytest.mark.parametrize("bus", ["B1", "B2", "B3"])
def test_the_grounding_impedance_is_the_en50522_one(bus, z_bus):
    """
    ``Z_G`` in the response frame has to be the same quantity ``run_fault``
    reports, ``Z_E = U_E / I_E``, for the electrode under test -- not the
    ``u_b / (r * I_F)`` form 0.6.0 replaced, which came out a factor of 39
    away and used the wrong bus's voltage on top.
    """
    response = gi.bus_response(_network(10.0, bus=bus), fault="F", bus=bus)
    solved = _network(z_bus, bus=bus)
    expected = abs(complex(solved.results["F"].grounding_impedance.value[50.0]))
    assert _row(response, z_bus)["Z_G_Ohm"][0] == pytest.approx(
        expected, rel=1e-9
    )


# --- A1: r_epr per electrode --------------------------------------------------


@pytest.mark.parametrize("z_bus", [0.1, 10.0, 1000.0])
def test_the_epr_reduction_factor_is_evaluated_for_the_electrode(z_bus):
    """
    Away from the fault bus the Möbius denominators do not cancel, so ``r``
    moves with the electrode. Checked against a genuine solve at each value
    rather than against the spread alone.
    """
    response = gi.bus_response(_network(10.0), fault="F", bus="B1")
    expected = _network(z_bus).results["F"].reduction_factor.value[50.0]
    assert _row(response, z_bus)["r_epr"][0] == pytest.approx(expected, rel=1e-9)


def test_the_epr_reduction_factor_actually_varies_away_from_the_fault_bus():
    """The regression this pins: reporting one constant across the curve."""
    response = gi.bus_response(_network(10.0), fault="F", bus="B1")
    values = response.sweep([0.1, 10.0, 1000.0])["r_epr"].to_list()
    assert max(values) - min(values) > 0.05


def test_the_epr_reduction_factor_stays_invariant_at_the_fault_bus():
    """The invariance is real where it was derived -- at the fault bus the
    denominator cancels exactly -- and the per-electrode evaluation must not
    disturb it."""
    response = gi.bus_response(_network(10.0, bus="B2"), fault="F", bus="B2")
    values = response.sweep([0.01, 1.0, 100.0, None, 0.0])["r_epr"].to_list()
    assert values == pytest.approx([values[0]] * len(values), rel=1e-12)


# --- A16: two guards on the same frame ---------------------------------------


def test_an_empty_sweep_is_refused_with_the_argument_named():
    response = gi.bus_response(_network(10.0), fault="F")
    with pytest.raises(ValueError, match="at least one electrode"):
        response.sweep([])


def test_the_passive_worst_case_needs_a_positive_conductance():
    """
    ``1/Re(Y_net)`` is the maximum only while the conductance is positive.
    With a non-positive one the same expression returns a negative or infinite
    "worst case" that reads like an answer -- exactly the shape A5 produced.
    """
    response = gi.bus_response(_network(10.0), fault="F")
    broken = BusResponse(
        **{
            **response.model_dump(),
            "z_network": {50.0: complex(-4.0, 1.0)},
        }
    )
    with pytest.raises(ValueError, match="non-positive conductance"):
        broken.worst_case_electrode(50.0)


def test_the_admittance_of_an_electrode_needs_no_frequency():
    """``admittance`` never read its ``freq`` argument; it is gone rather than
    left promising an influence it does not have."""
    response = gi.bus_response(_network(10.0), fault="F")
    assert response.admittance(4.0) == pytest.approx(0.25)
    assert response.admittance(None) == 0
    assert np.isinf(response.admittance(0.0).real)
    with pytest.raises(TypeError):
        response.admittance(4.0, 50.0)

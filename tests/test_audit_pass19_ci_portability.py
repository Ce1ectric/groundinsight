# tests/test_audit_pass19_ci_portability.py
"""What the first CI run on macOS and Windows exposed (audit pass 19).

* ``res_fault`` named the bus of the largest EPR by a strict ``>``. When two
  buses carry the same EPR magnitude -- both ends of a symmetric line do --
  the winner was decided by the last bit of the solution, which differs
  between the BLAS/LAPACK builds of Linux, macOS and Windows, so the same
  network printed ``bus_source`` on one machine and ``bus_fault`` on another.
  Magnitudes within ``1e-9`` of the largest now count as a tie, and a tie is
  resolved in favour of the fault bus, otherwise the first bus in result
  order.
"""

import warnings

import pytest

import groundinsight as gi


def _symmetric_line(name, *, bus_order=("source", "fault")):
    """Two equal stations on one cable section: |EPR| is the same at both ends."""
    bus_type = gi.BusType(
        name=f"BT_{name}", system_type="Substation", voltage_level=20,
        impedance_formula="rho * 0.01 + j * f * 0.0001",
    )
    cable = gi.BranchType(
        name=f"CAB_{name}", grounding_conductor=True,
        self_impedance_formula="(0.25 + j * f * 0.012) * l",
        mutual_impedance_formula="(j * f * 0.012) * l",
    )
    net = gi.create_network(name=name, frequencies=[50.0, 250.0])
    for bus in bus_order:
        gi.create_bus(name=bus, type=bus_type, network=net)
    gi.create_branch(name="line", type=cable, from_bus="source", to_bus="fault",
                     length=5.0, network=net)
    gi.create_source(name="s", bus="source", values={50.0: 1000.0, 250.0: 200.0}, network=net)
    gi.create_fault(name="F", bus="fault", network=net)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", gi.GroundinsightWarning)
        gi.run_fault(net, fault="F")
    return net


@pytest.mark.parametrize("bus_order", [("source", "fault"), ("fault", "source")])
def test_a_tie_of_the_largest_epr_names_the_fault_bus(bus_order):
    net = _symmetric_line("tie_" + "_".join(bus_order), bus_order=bus_order)
    key = net.res_fault("F")
    assert key["EPR_max_bus"].to_list() == ["fault", "fault", "fault"]
    # Second way: the per-bus EPR of res_buses shows the tie the rule resolves.
    buses = net.res_buses(fault="F")
    for frequency in (50.0, 250.0):
        epr = dict(
            buses.filter(buses["frequency_Hz"] == frequency)
            .select("bus_name", "EPR_V").rows()
        )
        assert epr["source"] == pytest.approx(epr["fault"], rel=1e-9)
        top = key.filter(key["frequency_Hz"] == frequency)["EPR_max_V"][0]
        assert top == pytest.approx(max(epr.values()), rel=1e-12)


def test_a_clear_maximum_elsewhere_still_wins():
    net = _symmetric_line("clear_max")
    # A larger electrode at the source bus raises its EPR above the fault bus.
    stronger = net.buses["source"].type.model_copy(
        update={"name": "BT_weak", "impedance_formula": "rho * 0.05 + j * f * 0.0001"}
    )
    net.update_bus("source", type=stronger)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", gi.GroundinsightWarning)
        gi.run_fault(net, fault="F")
    key = net.res_fault("F")
    buses = net.res_buses(fault="F")
    at_50 = dict(buses.filter(buses["frequency_Hz"] == 50.0).select("bus_name", "EPR_V").rows())
    expected = max(at_50, key=at_50.get)
    assert abs(at_50["source"] - at_50["fault"]) > 1e-6 * max(at_50.values())
    assert key.filter(key["frequency_Hz"] == 50.0)["EPR_max_bus"][0] == expected

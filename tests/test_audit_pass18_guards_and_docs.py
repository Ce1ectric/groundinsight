# tests/test_audit_pass18_guards_and_docs.py
"""
Audit pass 18 -- K4, K5, K6 and K8.

**K4.** Impedance dictionaries are filled when an element is added to the
network, never at solve time. A frequency present in ``network.frequencies``
but absent from an element's dictionary therefore reached the matrix
assembly as ``None`` and the element was skipped silently -- a bus with a
perfectly good electrode behaved like a bus with none. Extending
``network.frequencies`` in place and recomputing only the buses reported
1000 V instead of 4.95 V at 250 Hz with ``r = 1.0``, and nothing said why.

**K5.** The current-based reduction factor read the fault current off the
*net* nodal injection at the fault bus. That entry is
``I_src,F - Sum(I_src)`` as soon as a source sits on the fault bus, so with
100 A at a remote bus and 50 A on the fault bus the divisor was -100 A
instead of 150 A.

**K6 / K8.** Documentation findings, pinned here only where a statement can
be checked mechanically: the ``Z_G`` definition and the RMS convention must
be named where a reader looks for them.
"""

import math

import pytest

import groundinsight as gi
from groundinsight.electrical_network import ElectricalNetwork
from groundinsight.models.core_models import ComplexNumber, Source
from tests._warning_policy import acknowledge

#: A voltage source on coupled branches; the test is about its missing source
#: impedance.
THEVENIN = acknowledge("THEVENIN_COUPLING")


def _bus_type(name="bt"):
    return gi.BusType(
        name=name,
        system_type="Grounded",
        voltage_level=20.0,
        impedance_formula="rho * 0 + 10 + I * f * 0",
    )


def _branch_type(name="brt"):
    return gi.BranchType(
        name=name,
        grounding_conductor=True,
        self_impedance_formula="(0.25 + j*f*0.012)*l",
        mutual_impedance_formula="(0.05 + j*f*0.010)*l",
        phase_impedance_formula="(0.1 + j*f*0.004)*l",
    )


def _chain(name, freqs=(50.0,), n_bus=4):
    freqs = list(freqs)
    net = gi.create_network(name=name, frequencies=freqs)
    bt, brt = _bus_type(), _branch_type()
    for i in range(n_bus):
        gi.create_bus(name=f"b{i}", type=bt, network=net)
    for i in range(n_bus - 1):
        gi.create_branch(
            name=f"L{i}", type=brt, from_bus=f"b{i}", to_bus=f"b{i+1}",
            length=1.0, network=net,
        )
    gi.create_source(
        name="S1", bus="b0", values={f: 100.0 for f in freqs}, network=net
    )
    gi.create_fault(
        name="F1", bus=f"b{n_bus-1}", scalings={f: 1.0 for f in freqs},
        network=net,
    )
    return net


# ---------------------------------------------------------------------------
# K4 -- a frequency an element has no impedance for is an error, not a skip
# ---------------------------------------------------------------------------


def test_frequency_added_in_place_without_recomputation_is_rejected():
    """The exact repro from the review: extend frequencies, refresh buses only."""
    net = _chain("p18_k4_inplace", freqs=[50.0])
    gi.run_fault(net, "F1")

    net.frequencies.append(250.0)
    for bus in net.buses.values():
        bus.calculate_impedance(net.frequencies)
    # the branches are deliberately NOT recomputed

    with pytest.raises(ValueError, match="carry no impedance"):
        gi.run_fault(net, "F1")


def test_a_missing_bus_impedance_is_rejected():
    net = _chain("p18_k4_bus", freqs=[50.0, 250.0])
    del net.buses["b1"].impedance[250.0]
    with pytest.raises(ValueError) as excinfo:
        gi.run_fault(net, "F1")
    message = str(excinfo.value)
    assert "bus 'b1'" in message
    assert "250 Hz" in message


def test_a_missing_branch_impedance_is_rejected():
    net = _chain("p18_k4_branch", freqs=[50.0, 250.0])
    del net.branches["L1"].self_impedance[250.0]
    with pytest.raises(ValueError, match="branch 'L1'"):
        gi.run_fault(net, "F1")


@THEVENIN
def test_a_missing_source_impedance_is_rejected():
    net = gi.create_network(name="p18_k4_src", frequencies=[50.0, 250.0])
    bt, brt = _bus_type(), _branch_type()
    for name in ("b0", "b1"):
        gi.create_bus(name=name, type=bt, network=net)
    gi.create_branch(
        name="L0", type=brt, from_bus="b0", to_bus="b1", length=1.0, network=net
    )
    gi.create_voltage_source(
        name="V1", bus="b0",
        voltage={50.0: 1000.0, 250.0: 1000.0},
        source_impedance={50.0: complex(1.0, 1.0), 250.0: complex(1.0, 5.0)},
        network=net,
    )
    gi.create_fault(
        name="F1", bus="b1", scalings={50.0: 1.0, 250.0: 1.0}, network=net
    )
    gi.run_fault(net, "F1")

    del net.sources["V1"].source_impedance[250.0]
    with pytest.raises(ValueError, match="source 'V1'"):
        gi.run_fault(net, "F1")


def test_an_inactive_element_may_stay_incomplete():
    """The check only covers what actually enters the nodal system."""
    net = _chain("p18_k4_inactive", freqs=[50.0, 250.0])
    del net.buses["b1"].impedance[250.0]
    net.buses["b1"].active = False
    net.branches["L0"].active = False
    net.branches["L1"].active = False
    gi.run_fault(net, "F1")
    assert net.results["F1"].buses


def test_a_surplus_frequency_is_still_permitted():
    """A stored frequency the network is not solving at must not be an error."""
    net = _chain("p18_k4_surplus", freqs=[50.0])
    for bus in net.buses.values():
        bus.impedance[999.0] = bus.impedance[50.0]
    gi.run_fault(net, "F1")
    assert net.results["F1"].buses


# ---------------------------------------------------------------------------
# K5 -- a source on the fault bus
# ---------------------------------------------------------------------------


def _network_with_source_at_fault(name, i_remote, i_at_fault):
    net = _chain(name, freqs=[50.0])
    net.sources["S1"].values[50.0] = ComplexNumber(real=i_remote, imag=0.0)
    gi.create_source(
        name="S2", bus="b3", values={50.0: i_at_fault}, network=net
    )
    return net


def test_fault_current_is_the_sum_of_the_feeding_sources():
    """r = |I_E| / |Sum(scaling * I_src)|, not over the net nodal entry.

    With the net entry the divisor was ``I_src,F - Sum(I_src)`` = -100 A
    instead of 150 A, and the reported factor was 1.5x too large. The value
    is checked against the earth current the same run reports, which is the
    definition of the quantity.
    """
    net = _network_with_source_at_fault("p18_k5", 100.0, 50.0)
    gi.run_fault(net, "F1")
    reduction = net.results["F1"].reduction_factor
    i_earth = reduction.i_earth[50.0]
    assert i_earth is not None
    expected = abs(complex(i_earth.real, i_earth.imag)) / 150.0
    assert reduction.value_current[50.0] == pytest.approx(expected, rel=1e-12)


def test_the_factor_scales_with_the_total_infeed_not_the_net_entry():
    """Doubling the source that sits on the fault bus must not flip the sign
    of the divisor's growth: the fault current grows, so r falls."""
    small = _network_with_source_at_fault("p18_k5_small", 100.0, 50.0)
    large = _network_with_source_at_fault("p18_k5_large", 100.0, 200.0)
    gi.run_fault(small, "F1")
    gi.run_fault(large, "F1")
    r_small = small.results["F1"].reduction_factor.value_current[50.0]
    r_large = large.results["F1"].reduction_factor.value_current[50.0]
    assert r_large < r_small


def test_a_source_only_at_the_fault_bus_stays_the_degenerate_case():
    """Injecting at the fault bus and faulting there is a null case.

    Nothing flows anywhere, so there is no earth current and no factor. That
    was true before and stays true -- the fix must not invent a number here.
    """
    net = _chain("p18_k5_only", freqs=[50.0])
    del net.sources["S1"]
    gi.create_source(name="S2", bus="b3", values={50.0: 100.0}, network=net)
    net.invalidate_paths()
    gi.run_fault(net, "F1")
    assert net.results["F1"].reduction_factor.value_current[50.0] is None


def test_the_remote_source_case_is_unchanged():
    """No source on the fault bus: the two readings coincide, so the value
    must not have moved."""
    net = _chain("p18_k5_remote", freqs=[50.0])
    gi.run_fault(net, "F1")
    reduction = net.results["F1"].reduction_factor
    i_earth = reduction.i_earth[50.0]
    expected = abs(complex(i_earth.real, i_earth.imag)) / 100.0
    assert reduction.value_current[50.0] == pytest.approx(expected, rel=1e-12)


# ---------------------------------------------------------------------------
# K6 / K8 -- the statements that were wrong or missing
# ---------------------------------------------------------------------------


def test_grounding_impedance_docstring_names_the_current_definition():
    # Audit pass 19, decision F4: Z_G = Z_E is the driving-point impedance of
    # the earthing network at the fault bus; the group quotient U_E / I_E it
    # named before is kept as z_earthing_group (see PQ-PH4). The docstring
    # states the definition and why the group quotient is not it; which
    # release changed the definition is CHANGELOG material (decision D4).
    doc = ElectricalNetwork.compute_grounding_impedance.__doc__
    assert "Z_G = Z_E = Z_dp(F)" in doc
    assert "z_earthing_group" in doc and "45 %" in doc


def test_the_rms_convention_is_written_down_where_it_is_defined():
    assert "RMS convention" in Source.__doc__
    assert "RMS amplitude" in Source.__doc__
    rms_doc = ElectricalNetwork._calculate_rms.__doc__
    assert "RMS convention" in rms_doc
    assert "RMS amplitude" in rms_doc


def test_the_rms_column_follows_the_documented_convention():
    """sqrt(sum |X(f)|^2), with the 0 Hz term entering as |X_dc|."""
    net = _chain("p18_k8_rms", freqs=[0.0, 50.0, 150.0])
    gi.run_fault(net, "F1")
    for bus in net.results["F1"].buses:
        expected = math.sqrt(
            sum(
                abs(complex(v.real, v.imag)) ** 2
                for v in bus.uepr_freq.values()
                if v is not None
            )
        )
        assert bus.uepr == pytest.approx(expected, rel=1e-12)

# tests/test_audit_pass19_input_coverage.py
"""Which frequencies a source and a fault cover (audit pass 19).

Finding IDs: USE3 / decision F8 -- a source without a value at any of the
network frequencies contributed 0 A everywhere and the run reported 0 V at
every bus, indistinguishable from an ordinary result; partial coverage was
equally silent. PH6 / USE4 / decision F7 -- a missing ``Fault.scalings`` key
scales with 1.0 (the documentation said 0), ``scalings`` could not be left
out, and an incomplete mapping went unannounced.

Second way for every number: the reference solver of
:mod:`tests._reference_mna` with the documented semantics written out
explicitly (a source without a value is absent at that frequency; a missing
scaling is 1.0).
"""

from __future__ import annotations

import warnings
from typing import Dict, List, Optional

import pytest

import groundinsight as gi
from groundinsight.diagnostics import InputWarning
from tests import _reference_mna as ref


FREQS = [50.0, 150.0]


def _network(name: str, frequencies=FREQS) -> gi.Network:
    """A three-bus cable line S - A - F with an isolated island bus I."""
    station = gi.BusType(name=f"{name}_bt", system_type="S", voltage_level=20.0,
                         impedance_formula="1.5 + 0.01*j*f/50")
    cable = gi.BranchType(
        name=f"{name}_cab", grounding_conductor=True,
        self_impedance_formula="(0.29+0.625*j*f/50)*l",
        mutual_impedance_formula="(0.049+0.625*j*f/50)*l",
        phase_impedance_formula="(0.13+0.12*j*f/50)*l",
    )
    net = gi.create_network(name=name, frequencies=list(frequencies))
    for bus in ("S", "A", "F", "I"):
        gi.create_bus(name=bus, type=station, network=net)
    gi.create_branch(name="L1", type=cable, from_bus="S", to_bus="A", length=1.0, network=net)
    gi.create_branch(name="L2", type=cable, from_bus="A", to_bus="F", length=1.5, network=net)
    return net


def _quiet_source(net: gi.Network, name: str, bus: str, values: Dict[float, complex]) -> None:
    """Add a source without letting the creation-time check speak."""
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        gi.create_source(name=name, bus=bus, values=values, network=net)


def _codes(result: gi.Result) -> List[str]:
    return [d.code for d in result.diagnostics]


def _epr(result: gi.Result, bus: str, f: float) -> complex:
    return next(complex(b.uepr_freq[f]) for b in result.buses if b.name == bus)


# ---------------------------------------------------------------------------
# USE3 / F8 -- source frequency coverage
# ---------------------------------------------------------------------------


def test_a_source_covering_no_network_frequency_is_rejected_by_name():
    """Zero overlap: the source would contribute 0 A at every solved
    frequency. That used to come back as 0 V at every bus; now the run
    refuses and names the source, its keys and the network frequencies."""
    net = _network("use3_none")
    _quiet_source(net, "Q", "S", {60.0: 1000.0})
    gi.create_fault(name="F1", bus="F", scalings={50.0: 1.0, 150.0: 1.0}, network=net)
    with pytest.raises(ValueError) as excinfo:
        gi.run_fault(net, "F1")
    message = str(excinfo.value)
    assert "'Q'" in message and "60 Hz" in message
    assert "50 Hz" in message and "150 Hz" in message


def test_partial_coverage_is_announced_once_per_source_and_run():
    """A value at 50 Hz but not at 150 Hz: one SOURCE_FREQUENCY_GAP naming the
    source and the missing frequency. The numbers: the source is absent at
    150 Hz and present at 50 Hz (second way: the reference solver)."""
    net = _network("use3_gap")
    _quiet_source(net, "Q", "S", {50.0: 1000.0})
    _quiet_source(net, "R", "A", {50.0: 200.0, 150.0: 50.0})
    gi.create_fault(name="F1", bus="F", scalings={50.0: 1.0, 150.0: 1.0}, network=net)
    with pytest.warns(InputWarning) as record:
        result = gi.run_fault(net, "F1")
    gaps = [w.message for w in record if getattr(w.message, "code", None) == "SOURCE_FREQUENCY_GAP"]
    assert len(gaps) == 1
    assert gaps[0].elements == (("source", "Q"),)
    assert "150 Hz" in str(gaps[0])
    assert _codes(result).count("SOURCE_FREQUENCY_GAP") == 1
    for f in FREQS:
        rd = ref.reference_network(net, f, "F1")
        assert ("Q" in rd["sources"]) == (f == 50.0)
        mapped = ref.to_package_frame(ref.solve_oneway(rd, thevenin_phase="norton"))
        assert abs(_epr(result, "F", f) - mapped["u"]["F"]) <= 1e-12 * abs(mapped["u"]["F"])
    # a second run says it again -- it is a property of the input, per run
    with pytest.warns(InputWarning):
        again = gi.run_fault(net, "F1")
    assert _codes(again).count("SOURCE_FREQUENCY_GAP") == 1


def test_a_source_that_does_not_reach_the_fault_is_not_checked():
    """A source on an island contributes nothing to this fault whatever its
    values; its coverage is not this run's business."""
    net = _network("use3_island")
    _quiet_source(net, "Q", "S", {50.0: 1000.0, 150.0: 100.0})
    _quiet_source(net, "Z", "I", {60.0: 10.0})
    gi.create_fault(name="F1", bus="F", scalings={50.0: 1.0, 150.0: 1.0}, network=net)
    with warnings.catch_warnings():
        warnings.simplefilter("error", InputWarning)
        result = gi.run_fault(net, "F1")
    assert "SOURCE_FREQUENCY_GAP" not in _codes(result)


@pytest.mark.parametrize("factory", ["current", "voltage"])
def test_keys_outside_the_network_frequencies_are_announced_at_creation(factory):
    """SOURCE_FREQUENCY_OUTSIDE_NETWORK at create_source / add_source: the
    extra keys are ignored by the solve (second way: the result equals the
    one of a source without them, bit for bit)."""
    with_extra = _network(f"use3_extra_{factory}")
    without = _network(f"use3_plain_{factory}")
    for net, keys in ((with_extra, [50.0, 150.0, 250.0]), (without, [50.0, 150.0])):
        if factory == "current":
            kwargs = dict(values={k: 1000.0 / (1 + k / 50) for k in keys})
            create = gi.create_source
        else:
            kwargs = dict(voltage={k: 11547.0 for k in keys},
                          source_impedance={k: complex(1, 3 * k / 50) for k in keys})
            create = gi.create_voltage_source
        if net is with_extra:
            with pytest.warns(InputWarning, match="250 Hz") as record:
                create(name="Q", bus="S", network=net, **kwargs)
            codes = [getattr(w.message, "code", None) for w in record]
            assert codes.count("SOURCE_FREQUENCY_OUTSIDE_NETWORK") == 1
            outside = next(w.message for w in record if getattr(w.message, "code", None)
                           == "SOURCE_FREQUENCY_OUTSIDE_NETWORK")
            assert outside.elements == (("source", "Q"),)
        else:
            with warnings.catch_warnings():
                warnings.simplefilter("error", InputWarning)
                create(name="Q", bus="S", network=net, **kwargs)
        gi.create_fault(name="F1", bus="F", scalings={50.0: 1.0, 150.0: 1.0}, network=net)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        a = gi.run_fault(with_extra, "F1")
        b = gi.run_fault(without, "F1")
    for f in FREQS:
        assert _epr(a, "F", f) == _epr(b, "F", f)


def test_add_source_on_the_network_checks_as_well():
    net = _network("use3_add")
    source = gi.Source(name="Q", bus="S", values={50.0: 1.0, 60.0: 1.0})
    with pytest.warns(InputWarning, match="60 Hz"):
        net.add_source(source)

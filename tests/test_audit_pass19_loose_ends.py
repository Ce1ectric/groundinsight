# tests/test_audit_pass19_loose_ends.py
"""Three loose ends found while the documentation was rebuilt (audit pass 19).

* A fault on the bus of every source that feeds it leaves the earthing
  network unexcited: the source puts ``+I`` and the fault ``-I`` on the same
  row, so the fault current closes through that station's own earthing back
  to the source. The EPR and the earth-return current are exactly zero and
  both reduction factors are ``None`` -- correct, but the run said nothing,
  so a reader of ``res_fault`` saw empty ratios without a reason. The run now
  records the case on the result (``FAULT_AT_FEEDING_BUS``, severity
  ``info``) without raising a warning.
* ``run_outage_study(auto_parallel_coefficients=...)`` handed the deprecated
  keyword on to ``run_fault``, whose ``DeprecationWarning`` then pointed at a
  line inside the package, where the default filters hide it. The study now
  resolves the keyword itself and the warning points at the caller.
* Warning texts shown to users do not cite internal audit identifiers.
"""

import re
import warnings
from pathlib import Path

import pytest

import groundinsight as gi


def _line(name, *, fault_bus, extra_source_bus=None):
    """Three stations on two cable sections, one source at ``a``."""
    bus_type = gi.BusType(
        name=f"BT_{name}", system_type="Substation", voltage_level=20,
        impedance_formula="rho * 0.01 + j * f * 0.0001",
    )
    cable = gi.BranchType(
        name=f"CAB_{name}", grounding_conductor=True,
        self_impedance_formula="(0.25 + j * f * 0.012) * l",
        mutual_impedance_formula="(j * f * 0.008) * l",
    )
    net = gi.create_network(name=name, frequencies=[50.0])
    for bus in ("a", "b", "c"):
        gi.create_bus(name=bus, type=bus_type, network=net)
    gi.create_branch(name="ab", type=cable, from_bus="a", to_bus="b", length=1.0, network=net)
    gi.create_branch(name="bc", type=cable, from_bus="b", to_bus="c", length=1.0, network=net)
    gi.create_source(name="s", bus="a", values={50.0: 1000.0}, network=net)
    if extra_source_bus is not None:
        gi.create_source(name="s2", bus=extra_source_bus, values={50.0: 400.0}, network=net)
    gi.create_fault(name="F", bus=fault_bus, network=net)
    return net


def test_a_fault_on_the_bus_of_its_only_source_says_why_its_ratios_are_empty():
    net = _line("feeding_bus", fault_bus="a")
    # Recorded on the result, not raised: nothing about the result is in
    # doubt, and studies place trial faults on the source bus as well.
    with warnings.catch_warnings():
        warnings.simplefilter("error", gi.GroundinsightWarning)
        result = gi.run_fault(net, fault="F")
    rf = result.reduction_factor
    assert rf.value[50.0] is None
    assert rf.value_current[50.0] is None
    codes = {d.code: d for d in result.diagnostics}
    assert "FAULT_AT_FEEDING_BUS" in codes
    assert codes["FAULT_AT_FEEDING_BUS"].severity == "info"
    assert ("fault", "F") in codes["FAULT_AT_FEEDING_BUS"].elements
    assert codes["FAULT_AT_FEEDING_BUS"].category == "ModelAssumptionWarning"
    assert "sits on the bus of every source" in codes["FAULT_AT_FEEDING_BUS"].message
    # The key-figure table counts it.
    assert net.res_fault("F")["n_diagnostics"][0] == len(result.diagnostics)
    # Second way: the reason itself. Nothing is injected into the earthing
    # network, so every bus potential and every electrode current is zero.
    buses = net.res_buses(fault="F")
    assert buses["EPR_V"].abs().max() == 0.0
    # U_E is the fault-bus EPR, 0 V; Z_E stays the driving-point impedance.
    assert abs(complex(rf.u_earthing[50.0].real, rf.u_earthing[50.0].imag)) == 0.0
    assert rf.z_earthing[50.0] is not None


def test_a_second_source_elsewhere_excites_the_network_and_nothing_is_reported():
    net = _line("two_sources", fault_bus="a", extra_source_bus="c")
    with warnings.catch_warnings():
        warnings.simplefilter("error", gi.GroundinsightWarning)
        result = gi.run_fault(net, fault="F")
    assert "FAULT_AT_FEEDING_BUS" not in {d.code for d in result.diagnostics}
    assert result.reduction_factor.value_current[50.0] is not None


def test_a_fault_away_from_the_source_is_unchanged():
    net = _line("remote_fault", fault_bus="c")
    with warnings.catch_warnings():
        warnings.simplefilter("error", gi.GroundinsightWarning)
        result = gi.run_fault(net, fault="F")
    assert "FAULT_AT_FEEDING_BUS" not in {d.code for d in result.diagnostics}
    assert 0.0 < result.reduction_factor.value_current[50.0] < 1.0


def test_the_outage_study_points_its_deprecation_at_the_caller():
    net = _line("outage_alias", fault_bus="c")
    with pytest.warns(DeprecationWarning, match="auto_parallel_coefficients") as record:
        gi.run_outage_study(
            net, fault="F", scenarios=[], auto_parallel_coefficients=True,
        )
    deprecations = [w for w in record if issubclass(w.category, DeprecationWarning)]
    assert deprecations
    assert all(Path(w.filename).resolve() == Path(__file__).resolve() for w in deprecations)


def test_the_outage_study_alias_still_selects_the_mode():
    net_old = _line("outage_old", fault_bus="c")
    net_new = _line("outage_new", fault_bus="c")
    with pytest.warns(DeprecationWarning):
        old = gi.run_outage_study(net_old, fault="F", scenarios=[], auto_parallel_coefficients=False)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", gi.GroundinsightWarning)
        new = gi.run_outage_study(net_new, fault="F", scenarios=[], phase_current_mode="paths")
    assert old.bus_results["base"]["EPR_V"].to_list() == new.bus_results["base"]["EPR_V"].to_list()


def test_user_facing_warning_texts_cite_no_audit_identifiers():
    source = Path(gi.__file__).parent
    offenders = []
    for path in sorted(source.rglob("*.py")):
        text = path.read_text(encoding="utf-8")
        for match in re.finditer(r"f?\"[^\"\n]*audit finding[^\"\n]*\"", text):
            offenders.append(f"{path.name}: {match.group(0)[:60]}")
    assert offenders == []

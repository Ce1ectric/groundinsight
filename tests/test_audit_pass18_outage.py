# tests/test_audit_pass18_outage.py

"""
Audit pass 18 -- ``run_outage_study`` error handling and dead bookkeeping.

* **S12** -- an outage study had no ``on_error``. The scenarios worth running
  are exactly the ones that disconnect something, so one scenario that
  isolates the fault bus threw away the twenty that solved. ``run_sweep``
  already had the switch; the outage study now has the same one, with the
  historic ``"raise"`` as its default.
* **S15** -- ``_OUTAGE_BASELINE_STACK`` was filled and popped by every
  ``outage_context`` and never read; 40 lines of comment described a
  mechanism that was not the one in use. Removed, with the nesting
  behaviour it claimed to provide re-verified here.
"""

from __future__ import annotations

import pytest

import groundinsight as gi
from groundinsight.models.core_models import BranchType, BusType
from groundinsight.simulation import outage as outage_module


FREQ = [50.0]


def _network(name: str = "net"):
    """S -- A -- F chain: disabling A splits the source from the fault."""
    bus_type = BusType(
        name="bt", system_type="MV", voltage_level=20.0, impedance_formula="10"
    )
    branch_type = BranchType(
        name="brt",
        grounding_conductor=True,
        self_impedance_formula="(0.1+0.2*j)*l",
        mutual_impedance_formula="(0.05+0.1*j)*l",
    )
    net = gi.create_network(name, frequencies=FREQ)
    for bus in ("S", "A", "F"):
        net.add_bus(gi.create_bus(bus, bus_type, 100.0))
    net.add_branch(gi.create_branch("SA", branch_type, "S", "A", 1.0, 100.0))
    net.add_branch(gi.create_branch("AF", branch_type, "A", "F", 1.0, 100.0))
    gi.create_fault("F1", "F", {50.0: 1.0}, active=True, network=net)
    gi.create_source("SRC", "S", {50.0: 1000.0}, network=net)
    return net


# --- S12 ---------------------------------------------------------------------


def _failing_run_fault(monkeypatch, bad_scenario: str):
    """Make ``run_fault`` raise while exactly one scenario is applied."""
    import groundinsight.network_operations as ops

    original = ops.run_fault

    def patched(network, *args, **kwargs):
        if not network.branches["AF"].active and bad_scenario == "af_open":
            raise ValueError("synthetic solver failure for the AF outage")
        return original(network, *args, **kwargs)

    monkeypatch.setattr(ops, "run_fault", patched)


def test_outage_study_raises_by_default(monkeypatch):
    """The historic behaviour is unchanged unless on_error is given."""
    net = _network()
    _failing_run_fault(monkeypatch, "af_open")
    with pytest.raises(ValueError, match="synthetic solver failure"):
        gi.run_outage_study(
            net,
            fault="F1",
            scenarios=[gi.Outage(name="af_open", disabled_branches=["AF"])],
        )


def test_outage_study_records_a_failing_scenario_and_continues(monkeypatch):
    """S12: one bad scenario must not cost the whole study."""
    net = _network()
    _failing_run_fault(monkeypatch, "af_open")

    study = gi.run_outage_study(
        net,
        fault="F1",
        scenarios=[
            gi.Outage(name="af_open", disabled_branches=["AF"]),
            gi.Outage(name="sa_open", disabled_branches=["SA"]),
        ],
        on_error="record",
    )

    assert "af_open" in study.failures
    assert "synthetic solver failure" in study.failures["af_open"]
    assert "af_open" not in study.bus_results
    assert "af_open" not in study.branch_results
    assert "af_open" not in study.labels()
    # The scenario after the failing one was still solved.
    assert "sa_open" in study.bus_results
    assert study.labels() == ["base", "sa_open"]
    # And the comparison still works over what is left.
    assert study.compare_buses().height > 0


def test_outage_study_leaves_the_network_intact_after_a_failure(monkeypatch):
    """The context manager unwinds even for the recorded failure."""
    net = _network()
    _failing_run_fault(monkeypatch, "af_open")
    gi.run_outage_study(
        net,
        fault="F1",
        scenarios=[gi.Outage(name="af_open", disabled_branches=["AF"])],
        on_error="record",
    )
    assert net.branches["AF"].active is True
    assert net.branches["SA"].active is True


def test_outage_study_rejects_an_unknown_on_error():
    net = _network()
    with pytest.raises(ValueError, match="on_error"):
        gi.run_outage_study(net, fault="F1", scenarios=[], on_error="ignore")


def test_outage_study_without_failures_reports_an_empty_dict():
    net = _network()
    study = gi.run_outage_study(
        net,
        fault="F1",
        scenarios=[gi.Outage(name="sa_open", disabled_branches=["SA"])],
        on_error="record",
    )
    assert study.failures == {}


# --- S15 ---------------------------------------------------------------------


def test_the_dead_outage_baseline_stack_is_gone():
    """S15: the module-level stack was write-only bookkeeping."""
    assert not hasattr(outage_module, "_OUTAGE_BASELINE_STACK")


def test_nested_outage_contexts_still_unwind_correctly():
    """The behaviour the removed stack claimed to provide, re-verified."""
    net = _network()
    outer = gi.Outage(name="outer", disabled_branches=["SA"])
    inner = gi.Outage(name="inner", disabled_branches=["AF"])

    with outage_module.outage_context(net, outer):
        assert net.branches["SA"].active is False
        with outage_module.outage_context(net, inner):
            assert net.branches["SA"].active is False
            assert net.branches["AF"].active is False
        assert net.branches["SA"].active is False
        assert net.branches["AF"].active is True
    assert net.branches["SA"].active is True
    assert net.branches["AF"].active is True

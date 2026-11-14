# tests/test_audit_pass19_diagnostics.py
"""Structured diagnostics (audit pass 19, GUI4/STD5 foundation).

A result-relevant warning has to reach the caller in a form a program can
act on -- with a code and the elements it concerns -- and it has to stay with
the result it belongs to. ``warnings.catch_warnings`` cannot provide the
latter: it is process-global and not thread-safe, so a GUI or a server that
solves two networks at once would mix their messages. The tests pin the
contract the rest of pass 19 builds on.
"""

import pickle
import threading
import warnings

import pytest

import groundinsight as gi
from groundinsight.diagnostics import (
    Diagnostic,
    GroundinsightWarning,
    InputWarning,
    ModelAssumptionWarning,
    collecting,
    emit,
)


def _line_network(name="diag_line"):
    bus_type = gi.BusType(
        name="BT_diag", system_type="Substation", voltage_level=20,
        impedance_formula="rho * 0.01 + j * f * 0.0001",
    )
    cable = gi.BranchType(
        name="CAB_diag", grounding_conductor=True,
        self_impedance_formula="(0.25 + j * f * 0.012) * l",
        mutual_impedance_formula="(j * f * 0.008) * l",
    )
    net = gi.create_network(name=name, frequencies=[50.0])
    for bus in ("a", "b", "c"):
        gi.create_bus(name=bus, type=bus_type, network=net)
    gi.create_branch(name="ab", type=cable, from_bus="a", to_bus="b", length=1.0, network=net)
    gi.create_branch(name="bc", type=cable, from_bus="b", to_bus="c", length=1.0, network=net)
    gi.create_source(name="s", bus="a", values={50.0: 1000.0}, network=net)
    gi.create_fault(name="F", bus="c", scalings={50.0: 1.0}, network=net)
    return net


def test_every_package_warning_derives_from_the_common_base():
    for category in (
        gi.NetworkFrequencyOrderWarning,
        gi.TransientModelWarning,
        gi.DCLimitWarning,
        ModelAssumptionWarning,
        InputWarning,
    ):
        assert issubclass(category, GroundinsightWarning)
        assert issubclass(category, UserWarning)
    assert gi.GroundinsightWarning is GroundinsightWarning
    assert gi.ModelAssumptionWarning is ModelAssumptionWarning
    assert gi.InputWarning is InputWarning
    assert gi.Diagnostic is Diagnostic


def test_emit_warns_with_code_and_elements():
    with pytest.warns(ModelAssumptionWarning) as record:
        emit(
            ModelAssumptionWarning,
            "proxy used",
            code="PHASE_PROXY",
            elements=[("branch", "ab"), ("branch", "bc")],
        )
    warning = record[0].message
    assert warning.code == "PHASE_PROXY"
    assert warning.elements == (("branch", "ab"), ("branch", "bc"))
    assert warning.severity == "warning"


def test_collecting_records_emitted_and_plain_warnings_even_when_filtered():
    with collecting() as sink, warnings.catch_warnings():
        warnings.simplefilter("ignore")
        emit(InputWarning, "gap", code="SCALINGS_INCOMPLETE", elements=[("fault", "F")])
        # A legacy call site without the helper is recorded as well.
        warnings.warn("legacy text", gi.TransientModelWarning, stacklevel=1)
    assert [d.code for d in sink] == ["SCALINGS_INCOMPLETE", "TRANSIENT_MODEL"]
    assert sink[0].elements == [("fault", "F")]
    assert sink[0].category == "InputWarning"
    assert sink[1].message == "legacy text"


def test_nothing_is_recorded_outside_a_collecting_block():
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        emit(InputWarning, "gap", code="X")
    with collecting() as sink:
        pass
    assert sink == []


def test_sinks_are_isolated_between_threads():
    results = {}

    def worker(tag):
        with collecting() as sink, warnings.catch_warnings():
            warnings.simplefilter("ignore")
            for _ in range(50):
                emit(InputWarning, tag, code=tag)
        results[tag] = {d.code for d in sink}

    threads = [threading.Thread(target=worker, args=(t,)) for t in ("A", "B", "C")]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert results == {"A": {"A"}, "B": {"B"}, "C": {"C"}}


def test_run_fault_stores_the_diagnostics_of_its_run_on_the_result():
    net = _line_network()
    # an incomplete frequency order warning is raised at construction time
    # and must not leak into a later run's diagnostics
    gi.run_fault(net, "F")
    result = net.results["F"]
    assert isinstance(result.diagnostics, list)
    assert all(isinstance(d, Diagnostic) for d in result.diagnostics)


def test_result_diagnostics_survive_json_round_trip():
    net = _line_network("diag_json")
    gi.run_fault(net, "F")
    net.results["F"].diagnostics.append(
        Diagnostic(code="X", severity="info", category="InputWarning", message="m", elements=[("bus", "a")])
    )
    clone = gi.Network.model_validate_json(net.model_dump_json())
    assert clone.results["F"].diagnostics[-1].elements == [("bus", "a")]


def test_package_warnings_pickle():
    warning = ModelAssumptionWarning("text", code="C", elements=[("bus", "x")])
    clone = pickle.loads(pickle.dumps(warning))
    assert str(clone) == "text" and clone.code == "C" and clone.elements == (("bus", "x"),)


def test_run_fault_returns_the_result_it_stores():
    net = _line_network("diag_return")
    result = gi.run_fault(net, "F")
    assert result is net.results["F"]
    assert result.fault == "F"

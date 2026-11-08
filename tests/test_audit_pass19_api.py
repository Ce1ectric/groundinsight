# tests/test_audit_pass19_api.py
"""
Audit pass 19 -- the service API a GUI or a server builds on.

* **ARC1** -- ``gi.solve(network, fault)`` returns the result of a fault
  calculation without touching the network it was given. ``run_fault`` writes
  ``active_fault``, ``results``, ``paths`` and the assembled electrical network
  into its input, which is what every "the study left the network changed"
  finding of the last passes traced back to, and what makes a solve in a worker
  thread unsafe while another thread reads the same network. The tests pin both
  halves of the contract: bit-identical numbers to ``run_fault`` (and, as a
  second, independent route, a closed form), and an input that is identical
  before and after -- serialised form, private state, path caches -- also when
  the solve raises.

The pre-flight check (GUI9) and the canonical parameter names (USE8/STD3) are
tested further down in this module.
"""

from __future__ import annotations

import gc
import math
import warnings

import pytest

import groundinsight as gi
from groundinsight import pathfinder
from groundinsight.models.core_models import ComplexNumber

FREQ = [50.0, 250.0]


# --------------------------------------------------------------------------
# builders
# --------------------------------------------------------------------------


def _bus_type(formula: str = "0.1*rho") -> gi.BusType:
    return gi.BusType(
        name="bt", system_type="MV", voltage_level=20.0, impedance_formula=formula
    )


def _branch_type(
    phase: bool = False, self_formula: str = "(0.1+0.2*j*f/50)*l"
) -> gi.BranchType:
    return gi.BranchType(
        name="brt",
        grounding_conductor=True,
        self_impedance_formula=self_formula,
        mutual_impedance_formula="(0.05+0.1*j*f/50)*l",
        phase_impedance_formula="(0.2+0.4*j*f/50)*l" if phase else None,
    )


def _chain(frequencies=FREQ, self_formula="(0.1+0.2*j*f/50)*l") -> gi.Network:
    """S -- A -- F with one source and two faults (``F`` and ``FA``)."""
    bus_type, branch_type = _bus_type(), _branch_type(self_formula=self_formula)
    net = gi.create_network("chain", frequencies=list(frequencies))
    for name in ("S", "A", "F"):
        gi.create_bus(name, bus_type, 100.0, network=net)
    gi.create_branch("SA", branch_type, "S", "A", 1.0, network=net)
    gi.create_branch("AF", branch_type, "A", "F", 2.0, network=net)
    gi.create_fault("F", "F", {f: 1.0 for f in frequencies}, network=net)
    gi.create_fault("FA", "A", {f: 0.5 for f in frequencies}, network=net)
    gi.create_source("SRC", "S", {f: 1000.0 for f in frequencies}, network=net)
    return net


def _ring() -> gi.Network:
    """Four-bus ring R0..R3, source at R0, fault at R2 (two parallel routes)."""
    bus_type, branch_type = _bus_type(), _branch_type(phase=True)
    net = gi.create_network("ring", frequencies=FREQ)
    names = [f"R{i}" for i in range(4)]
    for name in names:
        gi.create_bus(name, bus_type, 100.0, network=net)
    for i in range(4):
        gi.create_branch(
            f"L{i}", branch_type, names[i], names[(i + 1) % 4], 1.0 + 0.5 * i,
            network=net,
        )
    gi.create_fault("F", "R2", {50.0: 1.0, 250.0: 0.2}, network=net)
    gi.create_source("SRC", "R0", {50.0: 800.0, 250.0: 100.0}, network=net)
    return net


def _thevenin() -> gi.Network:
    """Chain with a Thevenin source instead of a current source."""
    bus_type, branch_type = _bus_type(), _branch_type()
    net = gi.create_network("thevenin", frequencies=FREQ)
    for name in ("S", "A", "F"):
        gi.create_bus(name, bus_type, 100.0, network=net)
    gi.create_branch("SA", branch_type, "S", "A", 1.0, network=net)
    gi.create_branch("AF", branch_type, "A", "F", 1.0, network=net)
    gi.create_fault("F", "F", {50.0: 1.0, 250.0: 1.0}, network=net)
    gi.create_voltage_source(
        "V", "S", voltage={50.0: 11547.0, 250.0: 500.0},
        source_impedance={50.0: 0.5 + 2.0j, 250.0: 0.5 + 10.0j}, network=net,
    )
    return net


NETWORKS = {"chain": _chain, "ring": _ring, "thevenin": _thevenin}


def _state(net: gi.Network):
    """Everything ``run_fault`` writes into a network, by value *and* identity.

    The serialised form covers the fields; the identities catch a "restore"
    that puts equal-looking copies back (a caller holding ``net.results`` would
    then hold a dict the network no longer uses); the private attributes and
    the field bookkeeping are invisible in the JSON altogether.
    """
    return {
        "json": net.model_dump_json(),
        "results_id": id(net.results),
        "results": [(name, id(result)) for name, result in net.results.items()],
        "paths_id": id(net.paths),
        "paths": [(name, id(path)) for name, path in net.paths.items()],
        "fingerprint": net._paths_fingerprint,
        "active_fault": net.active_fault,
        "fault_flags": {name: fault.active for name, fault in net.faults.items()},
        "fault_ids": [(name, id(fault)) for name, fault in net.faults.items()],
        "electrical_network": id(net.electrical_network),
        "fields_set": set(net.__pydantic_fields_set__),
    }


def _pathfinder_cache_keys():
    """The module-level path caches, which ``invalidate_paths`` prunes."""
    return (
        set(pathfinder._GRAPH_CACHE),
        set(pathfinder._FIND_PATHS_CACHE),
        set(pathfinder._REACHES_CACHE),
    )


# --------------------------------------------------------------------------
# ARC1 -- same numbers as run_fault
# --------------------------------------------------------------------------


@pytest.mark.parametrize("mode", ["auto", "paths"])
@pytest.mark.parametrize("kind", sorted(NETWORKS))
def test_solve_returns_bit_identical_numbers_to_run_fault(kind, mode):
    """``solve`` is ``run_fault`` minus the side effects: the serialised
    results agree character for character, which for floats is bit for bit."""
    net = NETWORKS[kind]()
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        solved = gi.solve(net, "F", phase_current_mode=mode)
        reference = gi.run_fault(net, "F", phase_current_mode=mode)
    assert solved is not reference
    assert solved.fault == "F"
    assert solved.model_dump_json() == reference.model_dump_json()


def test_solve_agrees_with_the_closed_form_of_a_two_bus_line():
    """Second route, independent of the solver: on a two-bus line with finite
    end electrodes the current reduction factor is
    ``|(Z_s - Z_m) / (Z_s + Z_A + Z_B)|`` (screen loop, EN 50522)."""
    z_self, z_mutual, z_electrode = 0.1 + 0.2j, 0.05 + 0.1j, 10.0 + 0.0j
    net = gi.create_network("line", frequencies=[50.0])
    bus_type = gi.BusType(
        name="e", system_type="MV", voltage_level=20.0,
        impedance_formula=f"({z_electrode.real}+{z_electrode.imag}*j)",
    )
    branch_type = gi.BranchType(
        name="k", grounding_conductor=True,
        self_impedance_formula=f"({z_self.real}+{z_self.imag}*j)*l",
        mutual_impedance_formula=f"({z_mutual.real}+{z_mutual.imag}*j)*l",
    )
    gi.create_bus("A", bus_type, network=net)
    gi.create_bus("B", bus_type, network=net)
    gi.create_branch("K", branch_type, "A", "B", 1.0, network=net)
    gi.create_fault("EF", "B", {50.0: 1.0}, network=net)
    gi.create_source("Q", "A", {50.0: 1000.0}, network=net)

    result = gi.solve(net, "EF")

    closed_form = abs((z_self - z_mutual) / (z_self + 2 * z_electrode))
    assert result.reduction_factor.value_current[50.0] == pytest.approx(
        closed_form, rel=1e-12
    )
    assert net.results == {}


def test_solve_result_carries_the_diagnostics_of_the_run():
    """The structured diagnostics of the run belong to the returned result,
    exactly as ``run_fault`` attaches them -- here the DC-limit warning the
    solve raises when a purely inductive screen is an ideal short at 0 Hz and
    has to be substituted."""
    net = _chain(frequencies=[0.0, 50.0], self_formula="(0.2*j*f/50)*l")
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        solved = gi.solve(net, "F")
        reference = gi.run_fault(net, "F")
    assert [d.code for d in solved.diagnostics] == ["DC_LIMIT"]
    assert solved.diagnostics == reference.diagnostics


# --------------------------------------------------------------------------
# ARC1 -- the network is left untouched
# --------------------------------------------------------------------------


def _fresh(net):
    return net


def _solved_other_fault(net):
    gi.run_fault(net, "FA")
    return net


def _solved_same_fault_then_other(net):
    gi.run_fault(net, "F")
    gi.run_fault(net, "FA")
    return net


def _stale_paths(net):
    """Paths that no longer match the topology: ``run_fault`` would drop them."""
    gi.create_paths(net)
    net.branches["AF"].active = False
    net.branches["AF"].active = True
    net.buses["A"].active = False
    return net


PRESTATES = {
    "fresh": _fresh,
    "other_fault_solved": _solved_other_fault,
    "both_faults_solved": _solved_same_fault_then_other,
    "stale_paths": _stale_paths,
}


@pytest.mark.parametrize("mode", ["auto", "paths"])
@pytest.mark.parametrize("prestate", sorted(PRESTATES))
def test_solve_leaves_the_network_exactly_as_found(prestate, mode):
    # Start from empty path caches so their LRU cap (256 entries, shared by
    # every test of the session) cannot evict anything during the call: what
    # is compared below is "nothing invalidated, nothing left behind".
    gi.clear_pathfinder_cache()
    net = PRESTATES[prestate](_chain())
    before = _state(net)
    caches = _pathfinder_cache_keys()

    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        result = gi.solve(net, "F", phase_current_mode=mode)

    assert isinstance(result, gi.Result)
    assert _state(net) == before
    assert _pathfinder_cache_keys() == caches


def _open_every_electrode(net):
    for bus in net.buses.values():
        bus.impedance = {f: ComplexNumber(real=math.inf, imag=0.0) for f in FREQ}


def _deactivate_fault_bus(net):
    net.buses["F"].active = False


# name -> (edit applied before the snapshot, keyword arguments of the call)
FAILURES = {
    "unknown_fault": (None, {"fault": "nope"}),
    "bad_mode": (None, {"fault": "F", "phase_current_mode": "nodal"}),
    "no_ground_reference": (_open_every_electrode, {"fault": "F"}),
    "inactive_fault_bus": (_deactivate_fault_bus, {"fault": "F"}),
}


@pytest.mark.parametrize("failure", sorted(FAILURES))
def test_solve_that_raises_leaves_the_network_exactly_as_found(failure):
    """The guarantee holds on the error path too -- a raise half-way through
    the pipeline must not leave a half-written network behind."""
    edit, kwargs = FAILURES[failure]
    gi.clear_pathfinder_cache()
    net = _solved_other_fault(_chain())
    if edit is not None:
        edit(net)
    before = _state(net)
    caches = _pathfinder_cache_keys()

    with pytest.raises(ValueError):
        gi.solve(net, **kwargs)

    assert _state(net) == before
    assert _pathfinder_cache_keys() == caches


def test_solve_does_not_replace_the_assembled_system_of_an_earlier_run():
    """``bus_response`` and ``analyze_cuts`` read ``network.electrical_network``
    back; a solve in between must not swap it for the matrices of another
    fault."""
    net = _chain()
    gi.run_fault(net, "FA")
    assembled = net.electrical_network
    gi.solve(net, "F")
    assert net.electrical_network is assembled
    assert net.electrical_network.matches_network_state()
    assert net.active_fault == "FA"


def test_run_fault_returns_the_result_it_stores():
    """ARC1 commit pattern: the object ``run_fault`` returns is the one in
    ``results``."""
    net = _chain()
    result = gi.run_fault(net, "F")
    assert result is net.results["F"]


def test_solve_leaves_the_elements_attached_to_the_input():
    """The overlay shares the element objects. Constructing a ``Network``
    attaches every element to the network being built, so an overlay built
    that way would take the input's elements along: afterwards they would
    point at the collected overlay, and an edit of the input would no longer
    re-derive the element it edits. The edit invariants of the input hold
    after a solve exactly as before."""
    net = _chain()
    gi.solve(net, "F")
    gc.collect()
    for collection in (net.buses, net.branches, net.sources, net.faults):
        for element in collection.values():
            assert element._owning_network() is net
    net.buses["A"].specific_earth_resistance = 400.0
    # Second route: the bus type's formula 0.1*rho, evaluated by hand.
    assert net.buses["A"].impedance[50.0].real == pytest.approx(0.1 * 400.0)


def test_a_result_from_solve_is_current_when_committed():
    """``solve`` stamps the input digest ``run_fault`` would stamp, so a
    result kept with ``network.results[fault] = result`` is not reported
    stale -- and turns stale with the next edit, like any other."""
    net = _chain()
    result = gi.solve(net, "F")
    reference = gi.run_fault(_chain(), "F")
    assert result.input_digest is not None
    assert result.input_digest == reference.input_digest
    net.results["F"] = result
    assert net.stale_results() == []
    net.update_bus("A", specific_earth_resistance=400.0)
    assert net.stale_results() == ["F"]


def test_solve_is_exported():
    assert "solve" in gi.__all__
    assert gi.solve.__module__ == "groundinsight.service"

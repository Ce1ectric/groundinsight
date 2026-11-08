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
import json
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


# --------------------------------------------------------------------------
# USE8/STD3 -- one name per concept, old names as deprecated aliases
# --------------------------------------------------------------------------
#
# The fault is ``fault`` everywhere (``run_sweep``, ``bus_response`` and
# ``analyze_cuts`` already said so, ``run_fault`` and half the analysis layer
# said ``fault_name``), an EPR limit is ``u_limit``, a fault duration
# ``t_k_s``, the IEC 60909 AC heat factor ``n_factor`` and a frequency
# ``frequency`` -- the names of the model fields ``Fault.t_k_s`` /
# ``Fault.n_factor`` and of the newer functions.

#: Every public parameter that was renamed: (attribute path, old, new).
RENAMES = [
    ("run_fault", "fault_name", "fault"),
    ("set_active_fault", "fault_name", "fault"),
    ("Network.set_active_fault", "fault_name", "fault"),
    ("Network.sources_reaching", "fault_name", "fault"),
    ("TransientStudy.__init__", "fault_name", "fault"),
    ("find_max_rho_scaling", "fault_name", "fault"),
    ("find_max_rho_scaling", "u_max", "u_limit"),
    ("check_conductor_limits", "fault_name", "fault"),
    ("check_conductor_limits", "t_k", "t_k_s"),
    ("check_conductor_limits", "n", "n_factor"),
    ("check_conductor_limits", "f", "frequency"),
    ("check_node_limits", "fault_name", "fault"),
    ("check_node_limits", "t_k", "t_k_s"),
    ("check_node_limits", "n", "n_factor"),
    ("check_node_limits", "f", "frequency"),
    ("admissible_short_circuit_current", "t_k", "t_k_s"),
    ("iec60909_m", "f", "frequency"),
    ("iec60909_m", "t_k", "t_k_s"),
    ("thermal_equivalent_current", "n", "n_factor"),
    ("resolve_fault_sc_characteristics", "fault_name", "fault"),
    ("apply_shortcircuit_characteristics", "fault_name", "fault"),
    ("read_shortcircuit_results", "f", "frequency"),
    ("BusResponse.voltages", "freq", "frequency"),
    ("BusResponse.voltages_no_mutual", "freq", "frequency"),
    ("BusResponse.driving_point", "freq", "frequency"),
    ("BusResponse.worst_case_electrode", "freq", "frequency"),
]

#: Parameter names that must not survive anywhere in the public API.
OLD_NAMES = {"fault_name", "u_max", "t_k", "n", "f", "freq"}


def _resolve(path: str):
    obj = gi
    for part in path.split("."):
        obj = getattr(obj, part)
    return obj


def _public_callables():
    """Functions in ``gi.__all__`` plus the public methods of its classes."""
    import inspect

    seen = set()
    for name in gi.__all__:
        obj = getattr(gi, name)
        if inspect.isclass(obj):
            members = [
                (f"{name}.{member}", value)
                for member, value in vars(obj).items()
                if inspect.isfunction(value)
                and (not member.startswith("_") or member == "__init__")
            ]
        elif inspect.isfunction(obj):
            members = [(name, obj)]
        else:
            members = []
        for qualname, func in members:
            if not getattr(func, "__module__", "").startswith("groundinsight"):
                continue
            if id(func) in seen:
                continue
            seen.add(id(func))
            yield qualname, func


def _alias_warnings(record):
    """The rename warnings of this package, not third-party deprecations."""
    return [
        str(w.message)
        for w in record
        if issubclass(w.category, DeprecationWarning)
        and "is deprecated, use" in str(w.message)
    ]


def _call_old_and_new(call_old, call_new, olds):
    """Run both spellings; the old one must warn once per old name used, the
    new one not at all."""
    with warnings.catch_warnings(record=True) as record:
        warnings.simplefilter("always")
        old_value = call_old()
    messages = _alias_warnings(record)
    assert len(messages) == len(olds), messages
    for old in olds:
        assert any(f"'{old}'" in message for message in messages), messages
    with warnings.catch_warnings(record=True) as record:
        warnings.simplefilter("always")
        new_value = call_new()
    assert _alias_warnings(record) == []
    return old_value, new_value


@pytest.mark.parametrize("path, old, new", RENAMES)
def test_every_renamed_parameter_keeps_its_old_name_as_alias(path, old, new):
    import inspect

    func = _resolve(path)
    parameters = inspect.signature(func).parameters
    assert new in parameters
    assert old not in parameters
    assert getattr(func, "__deprecated_parameters__", {}).get(old) == new


def test_no_public_callable_keeps_a_pre_canonical_parameter_name():
    """USE8: a signature walk over ``__all__`` and the public methods."""
    import inspect

    offenders = {
        qualname: sorted(OLD_NAMES & set(inspect.signature(func).parameters))
        for qualname, func in _public_callables()
    }
    offenders = {k: v for k, v in offenders.items() if v}
    assert offenders == {}


def test_the_alias_warning_names_both_spellings_and_the_policy():
    from groundinsight.utils.compat import renamed_parameters

    @renamed_parameters(old="new")
    def demo(a, new=1):
        return a, new

    with pytest.warns(DeprecationWarning) as record:
        assert demo(1, old=5) == (1, 5)
    message = str(record[0].message)
    assert "demo()" in message and "'old'" in message and "'new'" in message
    assert "two releases" in message and "six months" in message
    # stacklevel: the warning points at the caller, not at the wrapper.
    assert record[0].filename == __file__


def test_the_alias_refuses_a_value_for_both_spellings():
    from groundinsight.utils.compat import renamed_parameters

    @renamed_parameters(old="new")
    def demo(a, new=1):
        return a, new

    with pytest.raises(TypeError, match="'new'.*'old'"):
        demo(1, new=2, old=3)
    with pytest.raises(TypeError, match="'new'.*'old'"):
        demo(1, 2, old=3)


def test_the_new_name_and_positional_calls_do_not_warn():
    import inspect

    from groundinsight.utils.compat import renamed_parameters

    @renamed_parameters(old="new")
    def demo(a, new=1):
        return a, new

    with warnings.catch_warnings():
        warnings.simplefilter("error")
        assert demo(1, new=2) == (1, 2)
        assert demo(1, 2) == (1, 2)
    assert list(inspect.signature(demo).parameters) == ["a", "new"]
    assert demo.__deprecated_parameters__ == {"old": "new"}


def test_the_decorator_rejects_an_inconsistent_declaration():
    from groundinsight.utils.compat import renamed_parameters

    def demo(a, new=1):
        return a, new

    with pytest.raises(TypeError, match="missing"):
        renamed_parameters(old="missing")(demo)
    with pytest.raises(TypeError, match="'a'"):
        renamed_parameters(a="new")(demo)


def test_the_alias_works_on_a_constructor():
    from groundinsight.utils.compat import renamed_parameters

    class Study:
        @renamed_parameters(fault_name="fault")
        def __init__(self, network, fault):
            self.fault = fault

    with pytest.warns(DeprecationWarning, match=r"Study\(\)"):
        assert Study(None, fault_name="F").fault == "F"


# --- behaviour: the old spelling reaches the same code as the new one -------


def test_run_fault_and_the_fault_activation_accept_the_old_name():
    old_net, new_net = _chain(), _chain()
    old, new = _call_old_and_new(
        lambda: gi.run_fault(old_net, fault_name="F"),
        lambda: gi.run_fault(new_net, fault="F"),
        ["fault_name"],
    )
    assert old.model_dump_json() == new.model_dump_json()

    _call_old_and_new(
        lambda: gi.set_active_fault(old_net, fault_name="FA"),
        lambda: gi.set_active_fault(new_net, fault="FA"),
        ["fault_name"],
    )
    assert old_net.active_fault == new_net.active_fault == "FA"
    _call_old_and_new(
        lambda: old_net.set_active_fault(fault_name="F", keep_results=True),
        lambda: new_net.set_active_fault(fault="F", keep_results=True),
        ["fault_name"],
    )
    assert old_net.active_fault == new_net.active_fault == "F"
    old, new = _call_old_and_new(
        lambda: old_net.sources_reaching(fault_name="F"),
        lambda: new_net.sources_reaching(fault="F"),
        ["fault_name"],
    )
    assert old == new == ["SRC"]


def test_transient_study_accepts_the_old_name():
    net = _chain()
    old, new = _call_old_and_new(
        lambda: gi.TransientStudy(net, fault_name="F"),
        lambda: gi.TransientStudy(net, fault="F"),
        ["fault_name"],
    )
    assert old.fault_name == new.fault_name == "F"


def test_find_max_rho_scaling_accepts_both_old_names():
    net = _chain()
    epr = gi.solve(net, "F").buses[2].uepr
    old, new = _call_old_and_new(
        lambda: gi.find_max_rho_scaling(
            net, fault_name="F", bus_names=["A", "F"], u_max=2.0 * epr,
            monotonicity_grid=0, max_iter=12,
        ),
        lambda: gi.find_max_rho_scaling(
            net, fault="F", bus_names=["A", "F"], u_limit=2.0 * epr,
            monotonicity_grid=0, max_iter=12,
        ),
        ["fault_name", "u_max"],
    )
    assert old == new
    assert old["u_epr_rms_at_c_max"] <= 2.0 * epr


def _thermal_network() -> gi.Network:
    bus_type = gi.BusType(
        name="bt", system_type="MV", voltage_level=20.0,
        impedance_formula="0.1*rho",
        earthing_conductor_material="Cu", earthing_conductor_cross_section_mm2=50.0,
        electrode_material="Cu", electrode_cross_section_mm2=50.0,
    )
    branch_type = gi.BranchType(
        name="brt", grounding_conductor=True,
        self_impedance_formula="(0.25 + j*f*0.012)*l",
        mutual_impedance_formula="(0.05 + j*f*0.012)*l",
        conductor_material="Cu", cross_section_mm2=50.0,
        theta_initial_C=20.0, theta_final_C=300.0,
    )
    net = gi.create_network("thermal", frequencies=[50.0])
    for name in ("S", "A", "F"):
        gi.create_bus(name, bus_type, 100.0, network=net)
    gi.create_branch("SA", branch_type, "S", "A", 1.0, network=net)
    gi.create_branch("AF", branch_type, "A", "F", 1.0, network=net)
    gi.create_source("SRC", "S", {50.0: 4000.0}, network=net, i_k_a=4000.0,
                     kappa=1.7)
    gi.create_fault("F", "F", {50.0: 1.0}, network=net)
    gi.run_fault(net, "F")
    return net


@pytest.mark.parametrize("check", ["check_conductor_limits", "check_node_limits"])
def test_thermal_checks_accept_all_four_old_names(check):
    net = _thermal_network()
    func = getattr(gi, check)
    old, new = _call_old_and_new(
        lambda: func(net, fault_name="F", t_k=0.5, kappa=1.8, n=0.9, f=50.0),
        lambda: func(net, fault="F", t_k_s=0.5, kappa=1.8, n_factor=0.9,
                     frequency=50.0),
        ["fault_name", "t_k", "n", "f"],
    )
    assert old.equals(new)
    # Second route for the one number all rows depend on: I_th / I_ac is
    # sqrt(m + n) with m from the IEC 60909-0 closed form.
    log_k = math.log(1.8 - 1.0)
    m = (math.exp(4 * 50.0 * 0.5 * log_k) - 1.0) / (2 * 50.0 * 0.5 * log_k)
    assert new["I_th_factor"].to_list()[0] == pytest.approx(
        math.sqrt(m + 0.9), rel=1e-12
    )


@pytest.mark.parametrize("check", ["check_conductor_limits", "check_node_limits"])
@pytest.mark.parametrize(
    "kwargs, message",
    [
        ({"t_k_s": -0.5}, r"^t_k_s must be strictly positive"),
        ({"t_k_s": 0.5, "n_factor": 3.0}, r"^n_factor must lie in \(0, 1\]"),
    ],
)
def test_thermal_range_errors_name_the_canonical_keyword(check, kwargs, message):
    """USE8: a range error names the keyword the caller passes, not the
    pre-canonical one."""
    net = _thermal_network()
    with pytest.raises(ValueError, match=message):
        getattr(gi, check)(net, "F", kappa=1.8, **kwargs)


def test_iec60909_primitives_accept_the_old_names():
    log_k = math.log(1.8 - 1.0)
    m_closed = (math.exp(4 * 50.0 * 0.5 * log_k) - 1.0) / (2 * 50.0 * 0.5 * log_k)
    old, new = _call_old_and_new(
        lambda: gi.iec60909_m(1.8, f=50.0, t_k=0.5),
        lambda: gi.iec60909_m(1.8, frequency=50.0, t_k_s=0.5),
        ["f", "t_k"],
    )
    assert old == new == pytest.approx(m_closed, rel=1e-12)

    old, new = _call_old_and_new(
        lambda: gi.thermal_equivalent_current(1000.0, 0.2, n=0.9),
        lambda: gi.thermal_equivalent_current(1000.0, 0.2, n_factor=0.9),
        ["n"],
    )
    assert old == new == pytest.approx(1000.0 * math.sqrt(1.1), rel=1e-12)

    old, new = _call_old_and_new(
        lambda: gi.admissible_short_circuit_current(143.0, 50.0, t_k=0.5),
        lambda: gi.admissible_short_circuit_current(143.0, 50.0, t_k_s=0.5),
        ["t_k"],
    )
    assert old == new == pytest.approx(143.0 * 50.0 / math.sqrt(0.5), rel=1e-12)


def test_resolve_fault_sc_characteristics_accepts_the_old_name():
    net = _thermal_network()
    old, new = _call_old_and_new(
        lambda: gi.resolve_fault_sc_characteristics(net, fault_name="F"),
        lambda: gi.resolve_fault_sc_characteristics(net, fault="F"),
        ["fault_name"],
    )
    assert old == new


def _pandapower_case():
    pp = pytest.importorskip("pandapower")
    sc = pytest.importorskip("pandapower.shortcircuit")
    net = pp.create_empty_network(sn_mva=100.0)
    b1 = pp.create_bus(net, vn_kv=110.0, name="HV")
    b2 = pp.create_bus(net, vn_kv=20.0, name="MV1")
    pp.create_ext_grid(
        net, b1, s_sc_max_mva=1000.0, rx_max=0.1, s_sc_min_mva=800.0,
        rx_min=0.1, x0x_max=1.0, r0x0_max=0.1, x0x_min=1.0, r0x0_min=0.1,
    )
    pp.create_transformer_from_parameters(
        net, b1, b2, sn_mva=40, vn_hv_kv=110, vn_lv_kv=20, vkr_percent=0.5,
        vk_percent=12, pfe_kw=0, i0_percent=0, vector_group="Dyn",
        vk0_percent=12, vkr0_percent=0.5, mag0_percent=100, mag0_rx=0.0,
        si0_hv_partial=0.9,
    )
    sc.calc_sc(net, fault="1ph", case="max", ip=True, ith=True, tk_s=0.5,
               topology="radial", kappa_method="C")
    return net


def _sc_grounding_network() -> gi.Network:
    net = gi.create_network("sc", frequencies=[50.0])
    bus_type = gi.BusType(name="b", system_type="s", voltage_level=20.0,
                          impedance_formula="rho*0 + 0.5")
    branch_type = gi.BranchType(
        name="c", grounding_conductor=True,
        self_impedance_formula="(0.25 + j*f*0.012)*l",
        mutual_impedance_formula="(0.05 + j*f*0.012)*l",
    )
    gi.create_bus("HV", bus_type, network=net)
    gi.create_bus("MV1", bus_type, network=net)
    gi.create_branch("HV-MV1", branch_type, "HV", "MV1", 1.0, network=net)
    gi.create_source("src", "HV", {50.0: 1000.0}, network=net)
    gi.create_fault("F", "MV1", {50.0: 1.0}, network=net)
    return net


def test_pandapower_short_circuit_helpers_accept_the_old_names():
    pp_net = _pandapower_case()
    old, new = _call_old_and_new(
        lambda: gi.read_shortcircuit_results(pp_net, f=50.0),
        lambda: gi.read_shortcircuit_results(pp_net, frequency=50.0),
        ["f"],
    )
    assert old.equals(new)

    old_net, new_net = _sc_grounding_network(), _sc_grounding_network()
    old, new = _call_old_and_new(
        lambda: gi.apply_shortcircuit_characteristics(old_net, pp_net, fault_name="F"),
        lambda: gi.apply_shortcircuit_characteristics(new_net, pp_net, fault="F"),
        ["fault_name"],
    )
    assert old.equals(new)
    assert old_net.model_dump_json() == new_net.model_dump_json()


def test_bus_response_methods_accept_the_old_frequency_name():
    net = _chain()
    gi.run_fault(net, "F")
    response = gi.bus_response(net, fault="F", bus="A")
    for method, args in (
        ("voltages", (10.0,)),
        ("voltages_no_mutual", (10.0,)),
        ("driving_point", (10.0,)),
        ("worst_case_electrode", ()),
    ):
        bound = getattr(response, method)
        old, new = _call_old_and_new(
            lambda: bound(*args, freq=50.0),
            lambda: bound(*args, frequency=50.0),
            ["freq"],
        )
        assert old == new


# --------------------------------------------------------------------------
# GUI9 -- check_network: every problem at once, as data, never an exception
# --------------------------------------------------------------------------
#
# A form-based editor needs all problems of a network before it solves, each
# with a stable code and the element it is about. ``run_fault`` and the
# ``create_*`` factories report the *first* problem as a ``ValueError`` -- or,
# for a branch whose end names no bus and for a source on an unknown bus,
# nothing at all: the solver drops the element and reports 0 V.


def _quickstart() -> gi.Network:
    """The network of ``docs/quickstart.md`` sections 1-4, verbatim."""
    net = gi.create_network(
        name="quickstart_demo", frequencies=[50, 250, 350, 450, 550]
    )
    bus_type = gi.BusType(
        name="SubstationBus",
        description="Lumped substation grounding grid",
        system_type="Substation",
        voltage_level=20,
        impedance_formula="rho * 0.01 + j * f * 1/50 * 0.1",
    )
    cable_type = gi.BranchType(
        name="MSCable",
        description="20 kV single-core cable with shield",
        grounding_conductor=True,
        self_impedance_formula="(0.25 + j * f * 0.012) * l",
        mutual_impedance_formula="(0.0  + j * f * 0.012) * l",
    )
    gi.create_bus(name="bus_source", type=bus_type, network=net,
                  specific_earth_resistance=100.0)
    gi.create_bus(name="bus_fault", type=bus_type, network=net,
                  specific_earth_resistance=100.0)
    gi.create_branch(name="cable_1", type=cable_type, from_bus="bus_source",
                     to_bus="bus_fault", length=5.0,
                     specific_earth_resistance=100.0, network=net)
    gi.create_source(name="substation_infeed", bus="bus_source",
                     values={50: 1000.0, 250: 200.0, 350: 100.0, 450: 50.0,
                             550: 25.0}, network=net)
    gi.create_fault(name="fault_at_remote_bus", bus="bus_fault",
                    description="Single-phase-to-ground fault at bus_fault",
                    scalings={50: 1.0}, network=net)
    return net


def _two_bus_docstring_network() -> gi.Network:
    """The network of the ``gi.solve`` docstring example."""
    bus_type = gi.BusType(name="BT", system_type="MV", voltage_level=20.0,
                          impedance_formula="0.01*rho")
    cable = gi.BranchType(name="K", grounding_conductor=True,
                          self_impedance_formula="(0.25+0.6*j)*l",
                          mutual_impedance_formula="0.6*j*l")
    net = gi.create_network(name="demo", frequencies=[50.0])
    gi.create_bus(name="b0", type=bus_type, network=net)
    gi.create_bus(name="b1", type=bus_type, network=net)
    gi.create_branch(name="k", type=cable, from_bus="b0", to_bus="b1",
                     length=1.0, network=net)
    gi.create_source(name="q", bus="b0", values={50.0: 100.0}, network=net)
    gi.create_fault(name="ef", bus="b1", scalings={50.0: 1.0}, network=net)
    return net


def _codes(diagnostics):
    return [(d.code, d.severity, tuple(d.elements)) for d in diagnostics]


@pytest.mark.parametrize(
    "build", [_chain, _ring, _thevenin, _two_bus_docstring_network],
    ids=["chain", "ring", "thevenin", "solve_docstring"],
)
def test_check_network_is_silent_on_a_sound_network(build):
    net = build()
    assert gi.check_network(net) == []
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        for fault in net.faults:
            gi.run_fault(net, fault)
    assert gi.check_network(net) == []


def test_check_network_is_silent_on_the_shared_fixture(simple_network):
    assert gi.check_network(simple_network) == []


def test_check_network_finds_no_source_and_an_isolated_bus_and_nothing_else():
    """GUI9: two problems, two records, no exception."""
    net = _chain()
    net.sources.clear()
    gi.create_bus("iso", _bus_type(), 100.0, network=net)
    assert sorted(_codes(gi.check_network(net))) == [
        ("ISOLATED_BUS", "warning", (("bus", "iso"),)),
        ("NO_SOURCE", "error", (("network", "chain"),)),
    ]


def test_check_network_reports_the_partial_scalings_of_the_quickstart():
    """The quickstart scales 50 Hz only and solves at five frequencies; with
    the missing ones taken as 1.0 (decision F7) that is reported, once."""
    diagnostics = gi.check_network(_quickstart())
    assert _codes(diagnostics) == [
        ("SCALINGS_INCOMPLETE", "warning", (("fault", "fault_at_remote_bus"),))
    ]
    assert "250" in diagnostics[0].message and "550" in diagnostics[0].message


# --- malformed networks: edit, expected record, what the solver does --------


def _edit_no_fault(net):
    net.faults.clear()


def _edit_frequencies_emptied(net):
    net.frequencies.clear()  # in place: bypasses the field validator


def _edit_frequency_duplicated(net):
    net.frequencies.append(50.0)


def _loaded_with(net, collection, name, field, value):
    """``net`` saved and loaded again, with one reference edited in the file.

    An element that belongs to a network refuses a reference to a bus the
    network does not have (the edit invariants of pass 19), so a dangling
    reference no longer comes out of the editing API. It still comes in from
    outside: a project file edited by hand, a database written by another
    tool, dictionaries filled by a script. Loading does not check references
    -- ``check_network`` is where they surface.
    """
    data = json.loads(net.model_dump_json())
    data[collection][name][field] = value
    return gi.Network.model_validate(data)


def _edit_dangling_branch(net):
    return _loaded_with(net, "branches", "AF", "to_bus", "ghost")


def _edit_source_on_unknown_bus(net):
    return _loaded_with(net, "sources", "SRC", "bus", "ghost")


def _edit_fault_on_unknown_bus(net):
    return _loaded_with(net, "faults", "F", "bus", "ghost")


def _edit_fault_bus_inactive(net):
    net.buses["F"].active = False


def _edit_source_bus_inactive(net):
    net.buses["S"].active = False


def _edit_impedance_missing(net):
    net.buses["A"].impedance = {50.0: ComplexNumber(real=10.0, imag=0.0)}


def _edit_formula_unevaluable(net):
    # Built without validation: a type formula with an unknown symbol is
    # rejected when the type is created (USE2), but a network loaded from an
    # older file or assembled by hand can still carry one. The bus has no
    # stored impedance, so the solver would need the formula.
    bad_type = gi.BusType.model_construct(
        name="bad", system_type="MV", voltage_level=20.0,
        impedance_formula="L*rho",
    )
    bus = gi.Bus(name="X", type=bad_type, impedance={},
                 specific_earth_resistance=100.0)
    net.buses["X"] = bus
    gi.create_branch("AX", _branch_type(), "A", "X", 1.0, network=net)


def _edit_impedance_not_passive(net):
    net.buses["A"].impedance = {f: ComplexNumber(real=-5.0, imag=1.0) for f in FREQ}


def _edit_impedance_nan(net):
    net.buses["A"].impedance = {
        50.0: ComplexNumber(real=math.nan, imag=0.0),
        250.0: ComplexNumber(real=10.0, imag=0.0),
    }


def _edit_source_frequency_none(net):
    net.sources["SRC"].values = {60.0: 1000.0}


def _edit_source_frequency_gap(net):
    net.sources["SRC"].values = {50.0: 1000.0}


def _edit_source_value_nan(net):
    net.sources["SRC"].values = {50.0: complex(math.nan, 0.0), 250.0: 100.0}


def _edit_scalings_incomplete(net):
    net.faults["F"].scalings = {50.0: 1.0}


def _edit_source_cut_off(net):
    net.branches["SA"].active = False


def _edit_floating_island(net):
    open_end = gi.BusType(name="open", system_type="MV", voltage_level=20.0,
                          impedance_formula="nan")
    gi.create_bus("X", open_end, network=net)
    gi.create_bus("Y", open_end, network=net)
    gi.create_branch("XY", _branch_type(), "X", "Y", 1.0, network=net)


def _edit_no_active_bus(net):
    for bus in net.buses.values():
        bus.active = False


def _edit_garbage_entry(net):
    net.buses["Z"] = "not a bus"


# (edit, expected (code, severity, elements) record, what the solver does:
#  "raises" -- run_fault raises; "zero" -- it returns 0 V everywhere for "F";
#  None -- it runs. A source that covers no solved frequency -- also because
#  the frequency list was emptied -- made run_fault return 0 V or a result
#  without a single frequency; since USE3 it raises.)
# An edit changes the network in place or returns the network to use instead.
MALFORMED = {
    "no_fault": (_edit_no_fault, ("NO_FAULT", "error", (("network", "chain"),)), "raises"),
    "frequencies_emptied": (
        _edit_frequencies_emptied,
        ("FREQUENCIES_INVALID", "error", (("network", "chain"),)), "raises",
    ),
    "frequency_duplicated": (
        _edit_frequency_duplicated,
        ("FREQUENCIES_INVALID", "error", (("network", "chain"),)), None,
    ),
    "dangling_branch": (
        _edit_dangling_branch,
        ("BRANCH_UNKNOWN_BUS", "error", (("branch", "AF"), ("bus", "ghost"))), "zero",
    ),
    "source_on_unknown_bus": (
        _edit_source_on_unknown_bus,
        ("SOURCE_UNKNOWN_BUS", "error", (("source", "SRC"), ("bus", "ghost"))), "zero",
    ),
    "fault_on_unknown_bus": (
        _edit_fault_on_unknown_bus,
        ("FAULT_UNKNOWN_BUS", "error", (("fault", "F"), ("bus", "ghost"))), "raises",
    ),
    "fault_bus_inactive": (
        _edit_fault_bus_inactive,
        ("FAULT_BUS_INACTIVE", "error", (("fault", "F"), ("bus", "F"))), "raises",
    ),
    "source_bus_inactive": (
        _edit_source_bus_inactive,
        ("SOURCE_BUS_INACTIVE", "warning", (("source", "SRC"), ("bus", "S"))), "zero",
    ),
    "impedance_missing": (
        _edit_impedance_missing,
        ("IMPEDANCE_MISSING", "error", (("bus", "A"),)), "raises",
    ),
    "formula_unevaluable": (
        _edit_formula_unevaluable,
        ("FORMULA_ERROR", "error", (("bus", "X"), ("bus_type", "bad"))), "raises",
    ),
    "impedance_not_passive": (
        _edit_impedance_not_passive,
        ("IMPEDANCE_NOT_PASSIVE", "error", (("bus", "A"),)), "raises",
    ),
    "impedance_nan": (
        _edit_impedance_nan,
        ("IMPEDANCE_NOT_FINITE", "error", (("bus", "A"),)), "raises",
    ),
    "source_frequency_none": (
        _edit_source_frequency_none,
        ("SOURCE_FREQUENCY_NONE", "error", (("source", "SRC"),)), "raises",
    ),
    "source_frequency_gap": (
        _edit_source_frequency_gap,
        ("SOURCE_FREQUENCY_GAP", "warning", (("source", "SRC"),)), None,
    ),
    "source_value_nan": (
        _edit_source_value_nan,
        ("SOURCE_VALUE_NOT_FINITE", "error", (("source", "SRC"),)), "raises",
    ),
    "scalings_incomplete": (
        _edit_scalings_incomplete,
        ("SCALINGS_INCOMPLETE", "warning", (("fault", "F"),)), None,
    ),
    "source_cut_off": (
        _edit_source_cut_off,
        ("FAULT_NOT_REACHED", "warning", (("fault", "F"),)), "zero",
    ),
    "floating_island": (
        _edit_floating_island,
        ("FLOATING_ISLAND", "error", (("bus", "X"), ("bus", "Y"))), "raises",
    ),
    "no_active_bus": (
        _edit_no_active_bus,
        ("NO_ACTIVE_BUS", "error", (("network", "chain"),)), "raises",
    ),
    "garbage_entry": (
        _edit_garbage_entry,
        ("ELEMENT_INVALID", "error", (("bus", "Z"),)), "raises",
    ),
}


@pytest.mark.parametrize("case", sorted(MALFORMED))
def test_check_network_names_the_problem_with_code_and_elements(case):
    edit, expected, _ = MALFORMED[case]
    net = _chain()
    net = edit(net) or net
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        diagnostics = gi.check_network(net)
    assert expected in _codes(diagnostics), _codes(diagnostics)
    for record in diagnostics:
        assert record.code in gi.checks.CHECK_CODES or record.code in (
            "FREQUENCY_ORDER", "DC_LIMIT",
        )
        assert record.message


@pytest.mark.parametrize("case", sorted(MALFORMED))
def test_check_network_severity_agrees_with_what_the_solver_does(case):
    """Second route: run the solver on the same network. Every "error" is a
    network on which ``run_fault`` raises, or returns an answer that does not
    describe the network (an element silently dropped: 0 V) -- and every
    "warning" alone still solves."""
    edit, (code, severity, _), solver = MALFORMED[case]
    net = _chain()
    net = edit(net) or net
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        if solver == "raises":
            with pytest.raises((ValueError, KeyError, AttributeError)):
                gi.run_fault(net, "F")
            return
        result = gi.run_fault(net, "F")
    if solver == "zero":
        assert max(bus.uepr for bus in result.buses) == 0.0
    else:
        assert severity == "warning" or code == "FREQUENCIES_INVALID"
        assert max(bus.uepr for bus in result.buses) > 0.0


def test_check_network_collects_several_problems_in_one_pass():
    net = _edit_dangling_branch(_chain())
    _edit_source_frequency_gap(net)
    _edit_scalings_incomplete(net)
    _edit_impedance_not_passive(net)
    codes = {d.code for d in gi.check_network(net)}
    assert {
        "BRANCH_UNKNOWN_BUS", "SOURCE_FREQUENCY_GAP", "SCALINGS_INCOMPLETE",
        "IMPEDANCE_NOT_PASSIVE",
    } <= codes


def test_check_network_restricts_fault_checks_to_the_named_fault():
    net = _chain()
    net.faults["FA"].scalings = {50.0: 1.0}
    assert gi.check_network(net, fault="F") == []
    assert [d.code for d in gi.check_network(net, fault="FA")] == [
        "SCALINGS_INCOMPLETE"
    ]
    assert _codes(gi.check_network(net, fault="nope")) == [
        ("FAULT_UNKNOWN", "error", (("fault", "nope"),))
    ]


def test_check_network_never_raises():
    assert _codes(gi.check_network(None)) == [("NOT_A_NETWORK", "error", ())]
    net = _chain()
    net.__dict__["buses"] = None  # beyond any validator
    diagnostics = gi.check_network(net)
    assert diagnostics and all(d.severity in ("error", "warning") for d in diagnostics)


def test_a_check_that_crashes_is_reported_and_the_others_still_run(monkeypatch):
    import groundinsight.checks as checks

    def explode(report):
        raise RuntimeError("boom")

    monkeypatch.setattr(checks, "_check_sources", explode)
    net = _chain()
    _edit_impedance_not_passive(net)
    diagnostics = gi.check_network(net)
    codes = {d.code: d for d in diagnostics}
    assert "IMPEDANCE_NOT_PASSIVE" in codes
    assert "boom" in codes["CHECK_FAILED"].message
    assert codes["CHECK_FAILED"].severity == "error"


def test_check_network_does_not_modify_the_network():
    net = _chain()
    _edit_formula_unevaluable(net)  # forces a formula evaluation on a copy
    _edit_impedance_missing(net)
    before = _state(net)
    gi.check_network(net)
    assert _state(net) == before
    assert net.buses["X"].impedance == {}


def test_every_check_code_is_upper_snake_and_has_a_severity():
    import re

    assert gi.checks.CHECK_CODES
    for code, (severity, meaning) in gi.checks.CHECK_CODES.items():
        assert re.fullmatch(r"[A-Z][A-Z0-9]*(_[A-Z0-9]+)*", code), code
        assert severity in ("error", "warning")
        assert meaning


def test_check_network_is_exported():
    assert "check_network" in gi.__all__


def test_the_documented_code_table_matches_the_registry():
    """``docs/api/service.md`` lists every check code with its severity; a
    code added to or dropped from the registry must not leave the page
    behind."""
    import pathlib
    import re

    page = pathlib.Path(__file__).resolve().parents[1] / "docs" / "api" / "service.md"
    text = page.read_text(encoding="utf-8")
    rows = dict(re.findall(r"^\| `([A-Z_]+)` \| (error|warning) \|", text, re.M))
    assert rows == {
        code: severity for code, (severity, _) in gi.checks.CHECK_CODES.items()
    }

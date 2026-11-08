# tests/test_audit_pass19_architecture.py

"""
Audit pass 19 -- architecture, phase 2 (ARC2 rest, ARC3, ARC5, ARC6).

ARC3: the package ``__init__`` carried implementation -- the logging setup,
``set_log_level`` and ``show_versions`` were the last pieces left after the
persistence moved out in phase 1. It now holds imports, ``__all__``,
``__version__`` and the module ``__getattr__`` for deprecations only, and every
name the package exported before stays importable from it.

ARC2 (rest): the path search kept its limits in module globals
(``set_pathfinder_max_paths``, ``set_pathfinder_cache_size``) and its memo in
three module-level dictionaries shared by every network of the process. A
limit set for one network applied to all of them -- also to a network another
thread was solving at that moment -- and the dictionaries were mutated from
several threads without a lock and kept the branches of dead networks alive.
Settings and memo now belong to the network; the old switches still work,
with a ``DeprecationWarning``, as the default of networks without settings.

ARC6: two copies of the reachability search (``PathFinder.reaches`` behind
``Network.sources_reaching``, and a second breadth-first search in the
transient solver) and three copies of the passivity classification (the
dictionary checker, the array checker of the transient solver and the
kernel's fast path) are reduced to one each. The tests compare the one
implementation with an independent oracle on random input -- connected
components from ``scipy.sparse.csgraph``, and the four rules applied value
by value with the scalar ``is_short_circuit`` -- and spy that every former
copy now goes through it.

ARC5: modules of the package used each other's underscore names
(``electrical._resolved_impedance``, ``fault._set_active``,
``network._needs_path_rebuild``, ...). What one module needs from another is
now internal API -- no leading underscore, marked "Internal API" in its
docstring, not exported. An AST scan of the package finds every remaining use
of another module's underscore name; the allow-list for exceptions is empty.
"""

import ast
import copy
import gc
import importlib
import inspect
import math
import pathlib
import pickle
import threading
import warnings
import weakref
from typing import Dict, List, Set, Tuple

import numpy as np
import pytest
from scipy.sparse import csr_matrix
from scipy.sparse.csgraph import connected_components

import groundinsight as gi

SRC = pathlib.Path(gi.__file__).resolve().parent

#: ``groundinsight.__all__`` of the base this phase started from. Every one of
#: these names must stay importable from the package (ARC3).
PUBLIC_NAMES_BEFORE = (
    'Network', 'Bus', 'BusType', 'Branch', 'BranchType', 'Fault', 'Source',
    'Path', 'Result', 'ResultBus', 'ResultBranch', 'RES_FAULT_SCHEMA',
    'ComplexNumber', 'create_network', 'create_bus', 'create_branch',
    'create_fault', 'create_source', 'create_voltage_source',
    'build_electrical_network', 'run_fault', 'solve', 'check_network',
    'StudyCancelled', 'plot_bus_voltages', 'plot_branch_currents',
    'plot_bus_currents', 'plot_epr_transient',
    'plot_branch_current_transient', 'create_network_assistant',
    'create_paths', 'set_active_fault', 'set_log_level',
    'clear_pathfinder_cache', 'get_pathfinder_cache_size',
    'set_pathfinder_cache_size', 'NetworkFrequencyOrderWarning',
    'TransientModelWarning', 'DCLimitWarning', 'GroundinsightWarning',
    'ModelAssumptionWarning', 'InputWarning', 'Diagnostic', 'Outage',
    'OutageStudyResult', 'outage_context', 'run_outage_study', 'SweepPoint',
    'SweepResult', 'rho_f_points', 'run_sweep', 'TransientStudy',
    'ResultTransient', 'waveforms', 'ImportDefaults', 'from_pandapower',
    'preview_pandapower_import', 'read_shortcircuit_results',
    'apply_shortcircuit_characteristics', 'network_from_tables', 'Cut',
    'CutAnalysis', 'analyze_cuts', 'BusResponse', 'bus_response',
    'REFERENCE_CASES', 'ReferenceCase', 'run_reference_cases', 'classify',
    'summarize', 'find_max_rho_scaling', 'find_max_rho_f_scaling',
    'evaluate_max_epr_under_k', 'select_rho_f_from_catalog',
    'admissible_short_circuit_current', 'check_conductor_limits',
    'check_node_limits', 'final_temperature', 'iec60949_k', 'iec60909_m',
    'kappa_from_r_to_x', 'peak_short_circuit_current',
    'thermal_equivalent_current', 'resolve_fault_sc_characteristics',
    'FaultShortCircuitData', 'FINAL_TEMPERATURES', 'CABLE_INSULATION_LIMITS',
    'IEC60949_MATERIALS', 'Database', 'start_dbsession', 'close_dbsession',
    'save_bustype_to_db', 'load_bustypes_from_db', 'save_branchtype_to_db',
    'load_branchtypes_from_db', 'save_network_to_db', 'load_network_from_db',
    'migrate_database', 'needs_migration', 'MigrationReport',
    'save_network_to_json', 'load_network_from_json', 'show_versions',
)


# ---------------------------------------------------------------------------
# ARC3 -- the package __init__ holds no implementation
# ---------------------------------------------------------------------------


def _allowed_top_level(node: ast.stmt) -> bool:
    """
    Whether a top-level statement of ``__init__.py`` is allowed by ARC3.

    Allowed are the module docstring, imports, the assignments of ``__all__``
    and ``__version__``, and the PEP 562 ``__getattr__`` that resolves
    deprecated names.

    Parameters
    ----------
    node : ast.stmt
        A statement of the module body.

    Returns
    -------
    bool
        ``True`` if the statement is one of the allowed kinds.
    """
    if isinstance(node, (ast.Import, ast.ImportFrom)):
        return True
    if isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant):
        return isinstance(node.value.value, str)  # the docstring
    if isinstance(node, (ast.Assign, ast.AnnAssign)):
        targets = node.targets if isinstance(node, ast.Assign) else [node.target]
        return all(
            isinstance(t, ast.Name) and t.id in ("__all__", "__version__")
            for t in targets
        )
    if isinstance(node, ast.FunctionDef):
        return node.name == "__getattr__"
    return False


def test_package_init_holds_only_imports_all_version_and_getattr():
    tree = ast.parse((SRC / "__init__.py").read_text(encoding="utf-8"))
    offending = [
        f"line {node.lineno}: {ast.unparse(node).splitlines()[0][:70]}"
        for node in tree.body
        if not _allowed_top_level(node)
    ]
    assert offending == []


def test_every_previously_public_name_is_still_importable():
    missing_from_all = [name for name in PUBLIC_NAMES_BEFORE if name not in gi.__all__]
    assert missing_from_all == []
    namespace = {}
    exec("from groundinsight import *", namespace)
    not_star_imported = [name for name in PUBLIC_NAMES_BEFORE if name not in namespace]
    assert not_star_imported == []
    for name in PUBLIC_NAMES_BEFORE:
        assert getattr(gi, name) is namespace[name], name


def test_library_logging_default_is_a_null_handler():
    """Moving the setup out of ``__init__`` must keep the import silent."""
    import logging

    handlers = logging.getLogger("groundinsight").handlers
    assert any(isinstance(h, logging.NullHandler) for h in handlers)


# ---------------------------------------------------------------------------
# ARC2 (rest) -- no module state in the path search
# ---------------------------------------------------------------------------

GRID = 4  # a 4x4 grid has 184 simple corner-to-corner paths


def _grid(name: str = "grid") -> gi.Network:
    """
    A ``GRID`` x ``GRID`` meshed network, source and fault in opposite corners.

    Parameters
    ----------
    name : str
        Network name.

    Returns
    -------
    groundinsight.Network
        An unsolved network with the fault ``"F"``.
    """
    bus_type = gi.BusType(
        name="earth",
        system_type="Substation",
        voltage_level=20.0,
        impedance_formula="rho*0.01 + 1 + I*f*0.002",
    )
    cable = gi.BranchType(
        name="cable",
        grounding_conductor=True,
        self_impedance_formula="(0.25 + I*f*0.012)*l",
        mutual_impedance_formula="(0.05 + I*f*0.010)*l",
        phase_impedance_formula="(0.10 + I*f*0.004)*l",
    )
    net = gi.create_network(name=name, frequencies=[50.0, 250.0])
    for row in range(GRID):
        for col in range(GRID):
            gi.create_bus(name=f"b{row}{col}", type=bus_type, network=net)
    for row in range(GRID):
        for col in range(GRID):
            if col + 1 < GRID:
                gi.create_branch(name=f"h{row}{col}", type=cable, from_bus=f"b{row}{col}",
                                 to_bus=f"b{row}{col + 1}", length=0.3, network=net)
            if row + 1 < GRID:
                gi.create_branch(name=f"v{row}{col}", type=cable, from_bus=f"b{row}{col}",
                                 to_bus=f"b{row + 1}{col}", length=0.4, network=net)
    corner = f"b{GRID - 1}{GRID - 1}"
    gi.create_source(name="s", bus="b00", values={50.0: 1000.0, 250.0: 100.0}, network=net)
    gi.create_fault(name="F", bus=corner, scalings={50.0: 1.0, 250.0: 1.0}, network=net)
    return net


def _outcome(net: gi.Network) -> str:
    """
    Solve ``net`` in the path-based mode and describe the outcome.

    Parameters
    ----------
    net : groundinsight.Network
        Network to solve (``phase_current_mode="paths"`` enumerates paths).

    Returns
    -------
    str
        The result's JSON, or ``"ValueError: <first line>"``.
    """
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        try:
            return gi.run_fault(net, "F", phase_current_mode="paths").model_dump_json()
        except ValueError as exc:
            return "ValueError: " + str(exc).splitlines()[0]


def _limited(name: str, max_paths: int) -> gi.Network:
    net = _grid(name)
    net.pathfinder_settings = gi.PathfinderSettings(max_paths=max_paths)
    return net


def test_two_networks_with_different_limits_in_two_threads_match_sequential_runs():
    """ARC-Q4: concurrent runs with different settings equal sequential ones."""
    sequential = {
        "tight": _outcome(_limited("tight", 100)),
        "loose": _outcome(_limited("loose", 1000)),
    }
    assert sequential["tight"].startswith("ValueError") and "100 paths" in sequential["tight"]
    assert not sequential["loose"].startswith("ValueError")

    rounds = 4
    observed = {"tight": [], "loose": []}
    errors = []
    barrier = threading.Barrier(2)

    def worker(kind: str, max_paths: int) -> None:
        try:
            for _ in range(rounds):
                net = _limited(kind, max_paths)
                barrier.wait(timeout=120)
                observed[kind].append(_outcome(net))
        except BaseException as exc:  # recorded for the main thread
            errors.append(repr(exc))

    threads = [
        threading.Thread(target=worker, args=("tight", 100)),
        threading.Thread(target=worker, args=("loose", 1000)),
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=600)
    assert errors == []
    for kind in ("tight", "loose"):
        assert observed[kind] == [sequential[kind]] * rounds


def test_a_network_without_settings_follows_the_process_default():
    net = _grid()
    assert net.pathfinder_settings == gi.PathfinderSettings()
    assert net.pathfinder_settings.max_paths == 100_000
    assert net.pathfinder_settings.cache_size == 256
    # Settings are runtime configuration, not part of the saved model.
    net.pathfinder_settings = gi.PathfinderSettings(max_paths=7)
    assert "pathfinder" not in net.model_dump_json()
    net.pathfinder_settings = None
    assert net.pathfinder_settings == gi.PathfinderSettings()


@pytest.mark.parametrize("bad", [0, -1, 1.5, True, "10"])
def test_settings_reject_nonsense(bad):
    with pytest.raises(ValueError):
        gi.PathfinderSettings(max_paths=bad)
    with pytest.raises(ValueError):
        gi.PathfinderSettings(cache_size=bad)


def test_network_settings_only_accept_settings_objects():
    with pytest.raises(TypeError):
        _grid().pathfinder_settings = 100


def test_the_deprecated_switches_warn_and_set_the_default_of_networks_without_settings():
    from groundinsight import pathfinder

    with warnings.catch_warnings():
        warnings.simplefilter("ignore", DeprecationWarning)
        previous = pathfinder.get_pathfinder_max_paths()
        previous_size = gi.get_pathfinder_cache_size()
    try:
        with pytest.deprecated_call(match="pathfinder_settings"):
            assert pathfinder.set_pathfinder_max_paths(100) == previous
        with pytest.deprecated_call():
            assert pathfinder.get_pathfinder_max_paths() == 100
        follows_default = _grid("default")
        own_settings = _limited("own", 1000)
        assert _outcome(follows_default).startswith("ValueError")
        assert not _outcome(own_settings).startswith("ValueError")

        with pytest.deprecated_call(match="pathfinder_settings"):
            assert gi.set_pathfinder_cache_size(8) == previous_size
        with pytest.deprecated_call():
            assert gi.get_pathfinder_cache_size() == 8
        assert _grid().pathfinder_settings.cache_size == 8
    finally:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", DeprecationWarning)
            pathfinder.set_pathfinder_max_paths(previous)
            gi.set_pathfinder_cache_size(previous_size)


def test_path_caches_belong_to_their_network_and_die_with_it():
    """The module-level caches kept every branch of a dead network alive."""
    from groundinsight.pathfinder import PathFinder

    net = _grid("transient")
    PathFinder(net).find_paths("b00", f"b{GRID - 1}{GRID - 1}")
    branch = weakref.ref(net.branches["h00"])
    other = _grid("other")
    assert net.path_cache() is not other.path_cache()
    del net
    gc.collect()
    assert branch() is None


def test_copies_get_their_own_cache_and_keep_the_settings():
    from groundinsight.pathfinder import PathFinder

    net = _limited("original", 1000)
    PathFinder(net).find_paths("b00", "b33")
    original_cache = net.path_cache()
    for copied in (
        net.model_copy(),
        copy.copy(net),
        copy.deepcopy(net),
        pickle.loads(pickle.dumps(net)),
    ):
        assert copied.path_cache() is not original_cache
        assert copied.pathfinder_settings == net.pathfinder_settings
        assert copied == net  # the cache takes no part in equality
    assert net.path_cache() is original_cache


def test_the_cache_is_bounded_per_network():
    from groundinsight.pathfinder import PathFinder

    net = _grid()
    net.pathfinder_settings = gi.PathfinderSettings(cache_size=2)
    for name in ("h00", "h01", "h02"):  # three different active topologies
        net.branches[name].active = False
        PathFinder(net)
        net.branches[name].active = True
    assert net.path_cache().sizes()["graph"] == 2


def test_clearing_one_network_leaves_the_other_and_clearing_all_clears_both():
    from groundinsight.pathfinder import PathFinder, clear_pathfinder_cache

    a, b = _grid("a"), _grid("b")
    for net in (a, b):
        PathFinder(net).find_paths("b00", "b33")
    clear_pathfinder_cache(a)
    assert a.path_cache().sizes() == {"graph": 0, "paths": 0, "reaches": 0}
    assert b.path_cache().sizes()["graph"] == 1
    clear_pathfinder_cache()
    assert b.path_cache().sizes() == {"graph": 0, "paths": 0, "reaches": 0}


# ---------------------------------------------------------------------------
# ARC6 -- one reachability search
# ---------------------------------------------------------------------------


def _random_network(seed: int) -> gi.Network:
    """
    A random, partly inactive network with several sources and one fault.

    Parameters
    ----------
    seed : int
        Seed of the generator.

    Returns
    -------
    groundinsight.Network
        Buses ``n0..``, random branches (possibly parallel, possibly leaving
        islands), random ``active`` flags, sources on random buses and the
        fault ``"F"`` on a random bus.
    """
    rng = np.random.default_rng(seed)
    bus_type = gi.BusType(name="earth", system_type="Substation", voltage_level=20.0,
                          impedance_formula="rho*0.01 + 1 + I*f*0.002")
    cable = gi.BranchType(name="cable", grounding_conductor=True,
                          self_impedance_formula="(0.25 + I*f*0.012)*l",
                          mutual_impedance_formula="(0.05 + I*f*0.010)*l",
                          phase_impedance_formula="(0.10 + I*f*0.004)*l")
    net = gi.create_network(name=f"random{seed}", frequencies=[50.0])
    count = int(rng.integers(4, 11))
    names = [f"n{i}" for i in range(count)]
    for name in names:
        gi.create_bus(name=name, type=bus_type, network=net)
    for k in range(int(rng.integers(count - 2, 2 * count))):
        a, b = rng.choice(count, size=2, replace=False)
        gi.create_branch(name=f"k{k}", type=cable, from_bus=names[a], to_bus=names[b],
                         length=0.5, network=net)
    for s in range(int(rng.integers(1, 4))):
        gi.create_source(name=f"s{s}", bus=names[int(rng.integers(count))],
                         values={50.0: 100.0}, network=net)
    gi.create_fault(name="F", bus=names[int(rng.integers(count))], scalings={50.0: 1.0},
                    network=net)
    for bus in net.buses.values():
        if rng.random() < 0.15:
            bus.active = False
    for branch in net.branches.values():
        if rng.random() < 0.2:
            branch.active = False
    return net


def _reaching_by_components(net: gi.Network, fault: str) -> list:
    """
    Independent oracle: sources in the connected component of the fault bus.

    Built from the active buses and the active branches between them with
    ``scipy.sparse.csgraph.connected_components`` -- no code of the package.

    Parameters
    ----------
    net : groundinsight.Network
        The network.
    fault : str
        The fault.

    Returns
    -------
    list of str
        Source names in network order.
    """
    active = [name for name, bus in net.buses.items() if bus.active]
    index = {name: i for i, name in enumerate(active)}
    fault_bus = net.faults[fault].bus
    if fault_bus not in index:
        return []
    rows, cols = [], []
    for branch in net.branches.values():
        if branch.active and branch.from_bus in index and branch.to_bus in index:
            rows.append(index[branch.from_bus])
            cols.append(index[branch.to_bus])
    graph = csr_matrix((np.ones(len(rows)), (rows, cols)), shape=(len(active),) * 2)
    _, labels = connected_components(graph, directed=False)
    target = labels[index[fault_bus]]
    return [
        name for name, source in net.sources.items()
        if source.bus in index and labels[index[source.bus]] == target
    ]


@pytest.mark.parametrize("seed", range(40))
def test_stationary_and_transient_ask_one_reachability_search(seed, monkeypatch):
    from groundinsight.pathfinder import PathFinder

    net = _random_network(seed)
    expected = _reaching_by_components(net, "F")
    assert net.sources_reaching("F") == expected

    calls = []
    original = PathFinder.reaches

    def spy(self, source_bus, fault_bus):
        calls.append((source_bus, fault_bus))
        return original(self, source_bus, fault_bus)

    monkeypatch.setattr(PathFinder, "reaches", spy)
    net.path_cache().clear()
    study = gi.TransientStudy(net, "F")
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        injecting = study._sources_reaching_fault(list(net.sources), net.faults["F"].bus)
    assert injecting == expected
    assert len(calls) == len(net.sources)  # the transient filter used the search


# ---------------------------------------------------------------------------
# ARC6 -- one passivity classification
# ---------------------------------------------------------------------------

EDGE_VALUES = [
    0j, complex(-0.0, 0.0), 1e-310 + 0j, -1e-310 + 0j, 1e-310j, 1e-320 + 1e-320j,
    5e-309 + 0j, 1e-300 + 0j, 1.0 + 0j, 1j, -1j, -1.0 + 0j, -1.0 + 1j, -1e-300 + 0j,
    complex(math.inf, 0.0), complex(math.inf, 1.0), complex(1.0, -math.inf),
    complex(-math.inf, 0.0), complex(-math.inf, 1.0), complex(math.nan, 0.0),
    complex(0.0, math.nan), complex(-math.inf, math.nan),
]


def _rule_by_rule(value: complex, frequency: float) -> bool:
    """
    Independent oracle: the four passivity rules for one value, in scalar code.

    Parameters
    ----------
    value : complex
        The impedance.
    frequency : float
        Its frequency in Hz.

    Returns
    -------
    bool
        ``True`` if the value violates a rule.
    """
    from groundinsight.utils.impedance_calculator import is_short_circuit

    finite = math.isfinite(value.real) and math.isfinite(value.imag)
    shorted = finite and is_short_circuit(value)
    if frequency != 0.0 and shorted:
        return True  # zero, or too small to invert, above 0 Hz
    if finite and not shorted and value.real < 0.0:
        return True  # negative resistance
    return value.real == -math.inf  # the limit of a negative resistance


def _sample(seed: int):
    """Edge values and random ones, each at 0 Hz or above."""
    rng = np.random.default_rng(seed)
    magnitudes = 10.0 ** rng.uniform(-320, 3, size=40)
    signs = rng.choice([-1.0, 1.0], size=(2, 40))
    random_values = signs[0] * magnitudes + 1j * signs[1] * magnitudes * rng.random(40)
    values = np.array(EDGE_VALUES + list(random_values), dtype=complex)
    frequencies = rng.choice([0.0, 50.0, 250.0], size=values.size)
    return values, frequencies


@pytest.mark.parametrize("seed", range(10))
def test_one_passivity_classification_matches_the_rules(seed):
    from groundinsight.utils.impedance_calculator import non_passive_mask

    values, frequencies = _sample(seed)
    expected = [_rule_by_rule(complex(v), float(f)) for v, f in zip(values, frequencies, strict=True)]
    assert non_passive_mask(values, frequencies).tolist() == expected
    # Broadcast form of the kernel: elements x frequencies.
    grid = non_passive_mask(np.vstack([values, values]), frequencies)
    assert grid.shape == (2, values.size)
    assert grid[1].tolist() == expected


@pytest.mark.parametrize("value", EDGE_VALUES + [-2.5 + 0j, 3e-311 + 0j])
@pytest.mark.parametrize("frequency", [0.0, 50.0])
def test_the_dictionary_and_array_checkers_decide_and_word_alike(value, frequency):
    from groundinsight.models.core_models import ComplexNumber
    from groundinsight.utils.impedance_calculator import (
        check_passive_impedance,
        check_passive_impedance_array,
    )

    def outcome(call):
        try:
            call()
            return None
        except ValueError as exc:
            return str(exc)

    as_dict = outcome(lambda: check_passive_impedance(
        {frequency: ComplexNumber(real=value.real, imag=value.imag)},
        element="bus 'X' (grounding impedance)", formula_str="f(rho)"))
    as_array = outcome(lambda: check_passive_impedance_array(
        np.array([value]), np.array([frequency]),
        element="bus 'X' (grounding impedance)", formula_str="f(rho)"))
    assert as_dict == as_array
    assert (as_dict is not None) == _rule_by_rule(value, frequency)


def test_every_former_copy_uses_the_one_classification(monkeypatch):
    from groundinsight.models.core_models import ComplexNumber
    from groundinsight.utils import impedance_calculator as ic

    calls = []
    original = ic.passivity_classes

    def spy(*args, **kwargs):
        calls.append(1)
        return original(*args, **kwargs)

    monkeypatch.setattr(ic, "passivity_classes", spy)

    ic.check_passive_impedance({50.0: ComplexNumber(real=1.0, imag=0.0)}, element="x")
    assert len(calls) == 1
    ic.check_passive_impedance_array(np.array([1.0 + 0j]), np.array([50.0]), element="x")
    assert len(calls) == 2
    before = len(calls)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        gi.solve(_grid("passive"), "F")  # the kernel's fast path
    assert len(calls) > before


# ---------------------------------------------------------------------------
# ARC5 -- no module reaches into another module's underscore names
# ---------------------------------------------------------------------------

#: Uses of another module's underscore name that stay allowed, as
#: ``"<file relative to the package>:<name>" -> reason``. Empty: whatever one
#: module of the package needs from another is internal API.
ALLOWED_PRIVATE_USES: Dict[str, str] = {}

#: The internal API: shared between modules of the package, without a leading
#: underscore, marked "Internal API" in its docstring and not exported from
#: ``groundinsight``.
INTERNAL_API = (
    ("groundinsight.electrical_network", "ElectricalNetwork.resolved_impedance"),
    ("groundinsight.electrical_network", "ElectricalNetwork.grounding_shunts"),
    ("groundinsight.electrical_network", "ElectricalNetwork.earthed_buses"),
    ("groundinsight.electrical_network", "ElectricalNetwork.remove_loop_closures"),
    ("groundinsight.electrical_network", "ElectricalNetwork.compute_phase_currents"),
    ("groundinsight.electrical_network", "component_labels"),
    ("groundinsight.electrical_network", "restricted_columns"),
    ("groundinsight.electrical_network", "ElectricalNetwork.nodal_parts"),
    ("groundinsight.electrical_network", "NodalParts"),
    ("groundinsight.electrical_network", "assemble_nodal_matrices"),
    ("groundinsight.electrical_network", "bus_admittances"),
    ("groundinsight.electrical_network", "rms_columns"),
    ("groundinsight.electrical_network", "stack_columns"),
    ("groundinsight.models.core_models", "Fault.mark_active"),
    ("groundinsight.models.core_models", "Network.needs_path_rebuild"),
    ("groundinsight.models.core_models", "Network.input_digest"),
    ("groundinsight.models.core_models", "Network.path_cache"),
    ("groundinsight.models.core_models", "validate_frequencies"),
    ("groundinsight.pathfinder", "PathfinderCache"),
    ("groundinsight.utils.impedance_calculator", "render_frequencies"),
    ("groundinsight.utils.impedance_calculator", "impedance_table"),
    ("groundinsight.utils.impedance_calculator", "short_circuit_mask"),
    ("groundinsight.utils.impedance_calculator", "PassivityClasses"),
    ("groundinsight.utils.impedance_calculator", "passivity_classes"),
    ("groundinsight.utils.impedance_calculator", "non_passive_mask"),
    ("groundinsight.analysis.shortcircuit", "sources_feeding"),
    ("groundinsight.io.pandapower_import", "bus_index_to_name"),
    ("groundinsight.io.pandapower_import", "require_pandapower"),
)


def _is_private(name: str) -> bool:
    """
    Whether ``name`` is an underscore name (a dunder name is not).

    Parameters
    ----------
    name : str
        An identifier.

    Returns
    -------
    bool
        ``True`` for ``_name``, ``False`` for ``name`` and ``__name__``.
    """
    return name.startswith("_") and not (name.startswith("__") and name.endswith("__"))


def _module_name(path: pathlib.Path, root: pathlib.Path) -> str:
    """
    Dotted name of the module in ``path``, for the package rooted at ``root``.

    Parameters
    ----------
    path : pathlib.Path
        A ``.py`` file below ``root``.
    root : pathlib.Path
        The package directory.

    Returns
    -------
    str
        For example ``groundinsight.analysis.response``; a package's
        ``__init__.py`` gives the package name.
    """
    parts = list(path.relative_to(root.parent).with_suffix("").parts)
    if parts[-1] == "__init__":
        parts.pop()
    return ".".join(parts)


def _defined_names(tree: ast.Module) -> Set[str]:
    """
    Names a module defines for others to reach.

    Functions, classes and assignment targets outside function bodies (at
    module level, also inside ``if``/``try``/``with``, and in class bodies --
    fields and private attributes included), and every attribute the module
    assigns on ``self`` or ``cls``.

    Parameters
    ----------
    tree : ast.Module
        The parsed module.

    Returns
    -------
    set of str
        The defined names.
    """
    names: Set[str] = set()

    def scope(statements: List[ast.stmt]) -> None:
        for node in statements:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                names.add(node.name)
                if isinstance(node, ast.ClassDef):
                    scope(node.body)
                continue
            if isinstance(node, (ast.Assign, ast.AnnAssign, ast.AugAssign)):
                targets = node.targets if isinstance(node, ast.Assign) else [node.target]
                for target in targets:
                    names.update(n.id for n in ast.walk(target) if isinstance(n, ast.Name))
            for field in ("body", "orelse", "finalbody"):
                scope(getattr(node, field, None) or [])
            for handler in getattr(node, "handlers", None) or []:
                scope(handler.body)

    scope(tree.body)
    names.update(
        node.attr
        for node in ast.walk(tree)
        if isinstance(node, ast.Attribute)
        and isinstance(node.ctx, ast.Store)
        and isinstance(node.value, ast.Name)
        and node.value.id in ("self", "cls")
    )
    return names


def _import_source(node: ast.ImportFrom, package: str) -> str:
    """
    Absolute name of the module a ``from ... import`` reads from.

    Parameters
    ----------
    node : ast.ImportFrom
        The import statement.
    package : str
        The package the importing module belongs to.

    Returns
    -------
    str
        The dotted module name.
    """
    if node.level == 0:
        return node.module or ""
    parts = package.split(".")
    base = parts[: len(parts) - (node.level - 1)]
    return ".".join(base + ([node.module] if node.module else []))


def _private_uses(root: pathlib.Path) -> List[Tuple[str, str, int, str]]:
    """
    Every use of an underscore name another module of the package defines.

    Three kinds of use count: ``from <module> import _name`` from another
    module of the package (a private *module* such as ``_state`` may be
    imported -- the underscore of a module means "internal to the package");
    ``obj._name`` on anything but ``self``/``cls``; and ``getattr``,
    ``setattr``, ``hasattr``, ``delattr`` and ``object.__setattr__`` with a
    constant underscore name. An attribute counts only if another module
    defines that name and the using module does not (see
    :func:`_defined_names`) -- attributes of third-party objects, such as
    pandapower's ``net._options``, are not the package's business.

    Parameters
    ----------
    root : pathlib.Path
        The package directory.

    Returns
    -------
    list of tuple
        ``(file relative to root, name, line, kind of use)``, sorted.
    """
    trees = {
        path: ast.parse(path.read_text(encoding="utf-8"))
        for path in sorted(root.rglob("*.py"))
    }
    modules = {_module_name(path, root): path for path in trees}
    defined = {path: _defined_names(tree) for path, tree in trees.items()}
    uses: List[Tuple[str, str, int, str]] = []
    for path, tree in trees.items():
        own = defined[path]
        elsewhere = set().union(*(names for p, names in defined.items() if p != path))
        relative = path.relative_to(root).as_posix()
        module = _module_name(path, root)
        package = module if path.name == "__init__.py" else module.rpartition(".")[0]

        def reaches(obj: ast.expr, name: str) -> bool:
            if not _is_private(name) or name in own or name not in elsewhere:
                return False
            return not (isinstance(obj, ast.Name) and obj.id in ("self", "cls"))

        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                source = _import_source(node, package)
                if source.split(".")[0] != root.name or source == module:
                    continue
                for alias in node.names:
                    if _is_private(alias.name) and f"{source}.{alias.name}" not in modules:
                        uses.append((relative, alias.name, node.lineno, f"import from {source}"))
            elif isinstance(node, ast.Attribute):
                if reaches(node.value, node.attr):
                    uses.append((relative, node.attr, node.lineno, "attribute"))
            elif isinstance(node, ast.Call) and len(node.args) >= 2:
                func = node.func
                by_name = isinstance(func, ast.Name) and func.id in (
                    "getattr", "setattr", "hasattr", "delattr"
                )
                by_object = isinstance(func, ast.Attribute) and func.attr in (
                    "__setattr__", "__getattribute__", "__delattr__"
                )
                name = node.args[1]
                if (by_name or by_object) and isinstance(name, ast.Constant) and isinstance(name.value, str):
                    if reaches(node.args[0], name.value):
                        uses.append((relative, name.value, node.lineno, ast.unparse(func)))
    return sorted(uses)


def test_the_scanner_finds_every_kind_of_use(tmp_path):
    package = tmp_path / "pkg"
    package.mkdir()
    (package / "__init__.py").write_text("", encoding="utf-8")
    (package / "_internal.py").write_text("X = 1\n", encoding="utf-8")
    (package / "a.py").write_text(
        "def _helper():\n"
        "    return 1\n"
        "\n"
        "\n"
        "class A:\n"
        "    _flag = False\n"
        "\n"
        "    def __init__(self):\n"
        "        self._state = 1\n"
        "\n"
        "    def _method(self):\n"
        "        return self._state\n",
        encoding="utf-8",
    )
    (package / "b.py").write_text(
        "from .a import _helper, A\n"
        "from . import _internal\n"
        "from pkg._internal import X\n"
        "\n"
        "\n"
        "def use(obj, other):\n"
        "    other._own = 1\n"
        "    obj._method()\n"
        "    getattr(obj, '_state')\n"
        "    object.__setattr__(obj, '_flag', True)\n"
        "    obj.__dict__\n"
        "    return obj._unknown\n"
        "\n"
        "\n"
        "class B:\n"
        "    def __init__(self):\n"
        "        self._own = 2\n"
        "\n"
        "    def run(self, a):\n"
        "        return self._own + a._flag\n",
        encoding="utf-8",
    )
    assert _private_uses(package) == [
        ("b.py", "_flag", 10, "object.__setattr__"),
        ("b.py", "_flag", 20, "attribute"),
        ("b.py", "_helper", 1, "import from pkg.a"),
        ("b.py", "_method", 8, "attribute"),
        ("b.py", "_state", 9, "getattr"),
    ]


def test_no_module_uses_another_modules_underscore_names():
    found = [
        f"{file}:{line} {name} ({kind})"
        for file, name, line, kind in _private_uses(SRC)
        if f"{file}:{name}" not in ALLOWED_PRIVATE_USES
    ]
    assert found == []


def test_every_allowed_use_is_still_needed():
    found = {f"{file}:{name}" for file, name, _, _ in _private_uses(SRC)}
    assert set(ALLOWED_PRIVATE_USES) <= found


@pytest.mark.parametrize("module, qualname", INTERNAL_API)
def test_internal_api_is_marked_and_not_exported(module, qualname):
    obj = importlib.import_module(module)
    for part in qualname.split("."):
        obj = getattr(obj, part)
    leaf = qualname.rsplit(".", 1)[-1]
    assert not leaf.startswith("_")
    paragraphs = (inspect.getdoc(obj) or "").split("\n\n")
    assert paragraphs[1].startswith("Internal API: ")  # after the summary
    assert leaf not in gi.__all__ and not hasattr(gi, leaf)


def test_the_frequency_order_warning_points_at_the_caller():
    with pytest.warns(gi.NetworkFrequencyOrderWarning) as caught:
        gi.Network(name="unordered", frequencies=[100.0, 50.0])
    assert [w.filename for w in caught] == [__file__]


def test_check_network_applies_the_same_frequency_rules():
    from groundinsight.models.core_models import validate_frequencies

    net = gi.create_network(name="edited", frequencies=[50.0, 150.0])
    net.frequencies.append(50.0)  # in place: bypasses the field validator
    with pytest.raises(ValueError) as rule:
        validate_frequencies(list(net.frequencies))
    report = gi.check_network(net)
    codes = {d.code: d.message for d in report}
    assert codes["FREQUENCIES_INVALID"] == str(rule.value)

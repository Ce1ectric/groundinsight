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
"""

import ast
import copy
import gc
import pathlib
import pickle
import threading
import warnings
import weakref

import pytest

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

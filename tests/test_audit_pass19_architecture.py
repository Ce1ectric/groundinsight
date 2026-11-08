# tests/test_audit_pass19_architecture.py

"""
Audit pass 19 -- architecture, phase 2 (ARC2 rest, ARC3, ARC5, ARC6).

ARC3: the package ``__init__`` carried implementation -- the logging setup,
``set_log_level`` and ``show_versions`` were the last pieces left after the
persistence moved out in phase 1. It now holds imports, ``__all__``,
``__version__`` and the module ``__getattr__`` for deprecations only, and every
name the package exported before stays importable from it.
"""

import ast
import pathlib

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

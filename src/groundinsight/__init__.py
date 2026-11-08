# groundinsight/__init__.py

"""
groundinsight package initialisation.

Re-exports the public API surface of ``groundinsight``: the SQLite
store (``Database`` and the module-level session helpers), the JSON project
file, the top-level network factory functions
(``create_network``, ``create_bus``, ``create_branch``,
``create_source``, ``create_fault``), the fault solver
(``run_fault``), the outage / what-if and inverse-rho analysis
helpers, the transient study workflow and the matplotlib plotting
helpers. All symbols re-exported here are available as
``groundinsight.<name>`` and are listed in :data:`__all__`.

This module holds no implementation (audit pass 19, ARC3): imports,
``__all__``, ``__version__`` and the PEP 562 ``__getattr__`` that resolves
deprecated names. Everything else lives in a module of its own -- the logging
default and :func:`set_log_level` in ``_logging``, :func:`show_versions` in
``_versions``, the persistence in ``database.store`` and ``io.project_file``.
"""

import warnings

# Imported first on purpose: it attaches the library's NullHandler to the
# package logger before any other module of the package can log.
from ._logging import set_log_level
from ._versions import show_versions
from .database.migration import (
    MigrationReport,
    migrate_database,
    needs_migration,
)

from .models.core_models import (
    Branch,
    BranchType,
    Bus,
    BusType,
    ComplexNumber,
    Fault,
    Network,
    NetworkFrequencyOrderWarning,
    Path,
    RES_FAULT_SCHEMA,
    Result,
    ResultBranch,
    ResultBus,
    Source,
    TransientModelWarning,
)

# The SQLite store. The ORM mirrors (``BusTypeDB`` & co.) stay an
# implementation detail of the persistence layer and are *not* re-exported;
# the public entry points are ``Database`` and the ``*_to_db`` / ``*_from_db``
# functions, which delegate to the default ``Database``.
from .database import store as _store
from .database.store import (
    Database,
    start_dbsession,
    close_dbsession,
    save_bustype_to_db,
    load_bustypes_from_db,
    save_branchtype_to_db,
    load_branchtypes_from_db,
    save_network_to_db,
    load_network_from_db,
)
from .utils.impedance_calculator import DCLimitWarning
from .diagnostics import (
    Diagnostic,
    GroundinsightWarning,
    InputWarning,
    ModelAssumptionWarning,
)
from .network_operations import (
    create_network,
    create_bus,
    create_branch,
    create_fault,
    create_source,
    create_voltage_source,
    build_electrical_network,
    run_fault,
    create_network_assistant,
    create_paths,
    set_active_fault,
)
from .service import solve
from .checks import check_network
from .progress import StudyCancelled
from .pathfinder import (
    PathfinderSettings,
    clear_pathfinder_cache,
    get_pathfinder_cache_size,
    set_pathfinder_cache_size,
)
from .plotting import (
    plot_bus_voltages,
    plot_branch_currents,
    plot_bus_currents,
    plot_epr_transient,
    plot_branch_current_transient,
)
from .simulation.outage import (
    Outage,
    OutageStudyResult,
    outage_context,
    run_outage_study,
)
from .simulation.sweep import (
    SweepPoint,
    SweepResult,
    rho_f_points,
    run_sweep,
)
from .simulation.transient import (
    ResultTransient,
    TransientStudy,
)
from .simulation import waveforms
from .io import (
    ImportDefaults,
    from_pandapower,
    preview_pandapower_import,
    read_shortcircuit_results,
    apply_shortcircuit_characteristics,
    network_from_tables,
)
from .io.project_file import (
    load_network_from_json,
    save_network_to_json,
)
from .analysis import (
    Cut,
    CutAnalysis,
    analyze_cuts,
    BusResponse,
    bus_response,
    REFERENCE_CASES,
    ReferenceCase,
    run_reference_cases,
    classify,
    summarize,
    find_max_rho_scaling,
    find_max_rho_f_scaling,
    evaluate_max_epr_under_k,
    select_rho_f_from_catalog,
    admissible_short_circuit_current,
    check_conductor_limits,
    check_node_limits,
    final_temperature,
    iec60949_k,
    iec60909_m,
    kappa_from_r_to_x,
    peak_short_circuit_current,
    thermal_equivalent_current,
    resolve_fault_sc_characteristics,
    FaultShortCircuitData,
    FINAL_TEMPERATURES,
    CABLE_INSULATION_LIMITS,
    IEC60949_MATERIALS,
)


__all__ = [
    # ``session`` and ``db_session`` are deliberately *not* listed: they are
    # deprecated aliases resolved by ``__getattr__`` at the end of this module,
    # and a star import must not raise their DeprecationWarning for code that
    # never touches them. ``from groundinsight import session`` still works.
    # Core models. Every documented example builds a network out of these,
    # and until 0.6.1 none of them was in ``__all__``: ``gi.BusType`` worked
    # by attribute access, ``from groundinsight import *`` did not import it,
    # type checkers flagged it, and the notebooks reached for a third spelling
    # (``from groundinsight.models.core_models import ...``).
    "Network",
    "Bus",
    "BusType",
    "Branch",
    "BranchType",
    "Fault",
    "Source",
    "Path",
    "Result",
    "ResultBus",
    "ResultBranch",
    # Column layout of Network.res_fault, the key figures of one fault.
    "RES_FAULT_SCHEMA",
    "ComplexNumber",
    "create_network",
    "create_bus",
    "create_branch",
    "create_fault",
    "create_source",
    "create_voltage_source",
    "build_electrical_network",
    "run_fault",
    # Side-effect-free solve: same numbers as run_fault, network untouched.
    "solve",
    # Pre-flight check: every problem as a Diagnostic, never an exception.
    "check_network",
    # Raised by a study whose should_cancel() callback returned true.
    "StudyCancelled",
    "plot_bus_voltages",
    "plot_branch_currents",
    "plot_bus_currents",
    "plot_epr_transient",
    "plot_branch_current_transient",
    "create_network_assistant",
    "create_paths",
    "set_active_fault",
    "set_log_level",
    # Path search: per-network settings (network.pathfinder_settings), the
    # memo reset, and the deprecated process-wide cache-size switch.
    "PathfinderSettings",
    "clear_pathfinder_cache",
    "get_pathfinder_cache_size",
    "set_pathfinder_cache_size",
    # Frequency-order warning.
    "NetworkFrequencyOrderWarning",
    # Transient model-scope warning.
    "TransientModelWarning",
    # DC (0 Hz) evaluation warning.
    "DCLimitWarning",
    # Structured diagnostics: common warning base, categories and the record
    # type stored in Result.diagnostics.
    "GroundinsightWarning",
    "ModelAssumptionWarning",
    "InputWarning",
    "Diagnostic",
    "Outage",
    "OutageStudyResult",
    "outage_context",
    "run_outage_study",
    "SweepPoint",
    "SweepResult",
    "rho_f_points",
    "run_sweep",
    "TransientStudy",
    "ResultTransient",
    "waveforms",
    "ImportDefaults",
    "from_pandapower",
    "preview_pandapower_import",
    "read_shortcircuit_results",
    "apply_shortcircuit_characteristics",
    # Networks as tables (Polars frames / CSV), pinned schema; the export is
    # Network.to_tables().
    "network_from_tables",
    "Cut",
    "CutAnalysis",
    "analyze_cuts",
    "BusResponse",
    "bus_response",
    "REFERENCE_CASES",
    "ReferenceCase",
    "run_reference_cases",
    "classify",
    "summarize",
    "find_max_rho_scaling",
    "find_max_rho_f_scaling",
    "evaluate_max_epr_under_k",
    "select_rho_f_from_catalog",
    "admissible_short_circuit_current",
    "check_conductor_limits",
    "check_node_limits",
    "final_temperature",
    "iec60949_k",
    "iec60909_m",
    "kappa_from_r_to_x",
    "peak_short_circuit_current",
    "thermal_equivalent_current",
    "resolve_fault_sc_characteristics",
    "FaultShortCircuitData",
    "FINAL_TEMPERATURES",
    "CABLE_INSULATION_LIMITS",
    "IEC60949_MATERIALS",
    # Persistence helpers — top-level surface, were previously reachable
    # only via attribute access so type checkers and ``from groundinsight
    # import *`` did not see them.
    "Database",
    "start_dbsession",
    "close_dbsession",
    "save_bustype_to_db",
    "load_bustypes_from_db",
    "save_branchtype_to_db",
    "load_branchtypes_from_db",
    "save_network_to_db",
    "load_network_from_db",
    "migrate_database",
    "needs_migration",
    "MigrationReport",
    "save_network_to_json",
    "load_network_from_json",
    # Cross-repo convention helper.
    "show_versions",
]

# Version. This literal mirrors the *released* version. It is rewritten by
# ``scripts/release.py`` together with ``pyproject.toml`` and ``CITATION.cff``
# in one step; the three must never drift, because the release script compares
# them and aborts on disagreement. Do not bump it by hand -- the [Unreleased]
# section in CHANGELOG.md is where work for the next cut accumulates (transient
# state-space solver, capacitance support, pandapower importer hardening,
# frequency-order warning, pathfinder LRU cache, atomic rebind in
# invalidate_paths, top-level set_active_fault factory, DC handling).
__version__ = "0.6.0"


def __getattr__(name: str):
    """
    Resolve the deprecated session globals (PEP 562).

    ``session``, ``db_session``, ``engine`` and ``SessionLocal`` were module
    globals up to 0.6.0. They now belong to the default
    :class:`~groundinsight.Database` and are looked up on access, with a
    ``DeprecationWarning`` that names the replacement. They are ``None`` while
    no session is started, as before.

    Parameters
    ----------
    name : str
        The attribute that normal lookup did not find.

    Returns
    -------
    object
        See :func:`groundinsight.database.store.deprecated_attribute`.

    Raises
    ------
    AttributeError
        For every other name.
    """
    if name in _store.DEPRECATED_ATTRIBUTES:
        warnings.warn(
            _store.deprecation_message(name), DeprecationWarning, stacklevel=2
        )
        return _store.deprecated_attribute(name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

# groundinsight/__init__.py

"""
groundinsight package initialisation.

Initialises the public API surface of ``groundinsight``: the SQLite
store (``Database`` and the module-level session helpers), the JSON project
file, the top-level network factory functions
(``create_network``, ``create_bus``, ``create_branch``,
``create_source``, ``create_fault``), the fault solver
(``run_fault``), the outage / what-if and inverse-rho analysis
helpers, the transient study workflow and the matplotlib plotting
helpers. All symbols re-exported here are available as
``groundinsight.<name>`` and are listed in :data:`__all__`.
"""

import logging
import warnings
from typing import Union

from .database.migration import (
    MigrationReport,
    migrate_database,
    needs_migration,
)
from typing import Dict

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
    # Pathfinder cache management.
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

# Library logging: attach a NullHandler so that importing groundinsight does
# not produce any output by default. Applications and notebooks opt in to
# log output either by configuring the standard ``logging`` module
# themselves or by calling :func:`set_log_level`.
logger = logging.getLogger(__name__)
logger.addHandler(logging.NullHandler())


# Sentinel attribute on a StreamHandler so we can recognise the one
# installed by :func:`set_log_level` and dedupe accidental duplicates.
_GI_HANDLER_MARK = "_groundinsight_console_handler"


def set_log_level(level: Union[int, str] = "INFO") -> logging.Logger:
    """
    Enable console logging for the ``groundinsight`` package.

    Convenience helper for interactive use (notebooks, scripts). Attaches a
    single :class:`logging.StreamHandler` with a simple formatter to the
    ``groundinsight`` package logger and sets the requested level.
    Subsequent calls only adjust the level on the *same* handler instance,
    so it is safe to call this helper repeatedly without accumulating
    duplicate console handlers — even across mixed
    ``set_log_level("DEBUG") -> set_log_level("INFO")`` toggles, and even
    when ``logging.basicConfig(...)`` has been called at the root logger.

    Parameters
    ----------
    level : int or str, optional
        Log level either as a numeric constant (e.g. ``logging.INFO``) or
        as a string name (``"DEBUG"``, ``"INFO"``, ``"WARNING"``,
        ``"ERROR"``, ``"CRITICAL"``). Defaults to ``"INFO"``.

    Returns
    -------
    logging.Logger
        The configured ``groundinsight`` package logger, for further
        adjustments by the caller if desired.

    Notes
    -----
    If you have also called :func:`logging.basicConfig` at the root logger
    level, every record will be emitted twice: once through the package
    handler installed here and once through the root handler installed by
    ``basicConfig``. To suppress the duplicate either call
    ``logging.getLogger("groundinsight").propagate = False`` explicitly,
    or rely solely on ``basicConfig`` and do not call
    :func:`set_log_level`.

    Examples
    --------
    >>> import groundinsight as gi
    >>> gi.set_log_level("INFO")  # doctest: +ELLIPSIS
    <Logger groundinsight (INFO)>
    """
    pkg_logger = logging.getLogger(__name__)
    pkg_logger.setLevel(level)

    # Collect every console-style handler installed previously, keep the
    # marked one, drop any stragglers that may have been attached by an
    # earlier (pre-0.5) version of this helper or by user code.
    marked = [
        h for h in pkg_logger.handlers if getattr(h, _GI_HANDLER_MARK, False)
    ]
    for h in list(pkg_logger.handlers):
        if (
            isinstance(h, logging.StreamHandler)
            and not isinstance(h, logging.NullHandler)
            and not getattr(h, _GI_HANDLER_MARK, False)
        ):
            pkg_logger.removeHandler(h)

    if marked:
        # Re-use the existing marked handler — just align its level so
        # filtering happens at both the logger and the handler level.
        handler = marked[0]
        # Defensive: if for some reason multiple marked handlers are
        # present (e.g. from a previous buggy release running in the same
        # interpreter), keep only the first.
        for extra in marked[1:]:
            pkg_logger.removeHandler(extra)
        handler.setLevel(level)
    else:
        handler = logging.StreamHandler()
        handler.setFormatter(
            logging.Formatter("%(levelname)s [%(name)s] %(message)s")
        )
        handler.setLevel(level)
        setattr(handler, _GI_HANDLER_MARK, True)
        pkg_logger.addHandler(handler)

    return pkg_logger


def show_versions() -> Dict[str, str]:
    """Return version information for ``groundinsight`` and its peers.

    Cross-repo convention helper introduced for the *seventh
    2026-05-18 review pass* roadmap entry "ADR-0013 — Cross-repo
    `show_versions` convention". The returned mapping always contains
    a ``"groundinsight"`` key with the value of :data:`__version__`;
    optional peers ``"groundfield"`` and ``"groundmeas"`` are added
    when the respective package is installed in the active
    interpreter. Python and platform information are reported under
    the ``"python"`` and ``"platform"`` keys so a bug report can
    include the full earthing-platform stack in a single call.

    Returns
    -------
    dict of str to str
        A mapping ``{"groundinsight": <__version__>, "python": "3.14.6",
        "platform": "Linux-...", ...}``. The first value is always
        :data:`__version__`; no version literal is repeated here so the
        docstring cannot drift away from the release. The dict is freshly
        built on every call and safe to mutate.

    Examples
    --------
    >>> import groundinsight as gi
    >>> info = gi.show_versions()
    >>> info["groundinsight"] == gi.__version__
    True
    """
    import platform
    import sys

    info: Dict[str, str] = {
        "groundinsight": __version__,
        "python": sys.version.split()[0],
        "platform": platform.platform(),
    }
    for peer in ("groundfield", "groundmeas"):
        try:
            mod = __import__(peer)
        except ImportError:
            continue
        peer_version = getattr(mod, "__version__", None)
        if peer_version is not None:
            info[peer] = str(peer_version)
    return info


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

# groundinsight/__init__.py

"""
groundinsight package initialisation.

Initialises the public API surface of ``groundinsight``: the SQLite
session helpers, the top-level network factory functions
(``create_network``, ``create_bus``, ``create_branch``,
``create_source``, ``create_fault``), the fault solver
(``run_fault``), the outage / what-if and inverse-rho analysis
helpers, the transient study workflow and the matplotlib plotting
helpers. All symbols re-exported here are available as
``groundinsight.<name>`` and are listed in :data:`__all__`.
"""

import logging
from typing import Union

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, scoped_session
from .database.crud import (
    save_bustype as _save_bustype,
    load_bustypes as _load_bustypes,
    save_branchtype as _save_branchtype,
    load_branchtypes as _load_branchtypes,
    save_network as _save_network,
    load_network as _load_network,
)
from .database.migration import (
    MigrationReport,
    migrate_database,
    needs_migration,
)
from typing import Optional
from typing import Dict
from pathlib import Path
from .models.core_models import (
    BusType,
    BranchType,
    Network,
    NetworkFrequencyOrderWarning,
)
from .models.database_models import BusTypeDB, BranchTypeDB, NetworkDB
from .utils.impedance_calculator import DCLimitWarning
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
from .analysis import (
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
    # NOTE: ``session`` (not ``db_session``) is the canonical name of
    # the module-level scoped session. The legacy ``db_session`` alias
    # in this list was a typo that raised ``ImportError`` for the
    # documented ``from groundinsight import db_session`` form; the
    # alias is now exposed at module scope further down (search for
    # ``db_session = session``) so the historic spelling keeps
    # working without any runtime cost.
    "session",
    "db_session",
    "create_network",
    "create_bus",
    "create_branch",
    "create_fault",
    "create_source",
    "create_voltage_source",
    "build_electrical_network",
    "run_fault",
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
    # DC (0 Hz) evaluation warning.
    "DCLimitWarning",
    "Outage",
    "OutageStudyResult",
    "outage_context",
    "run_outage_study",
    "TransientStudy",
    "ResultTransient",
    "waveforms",
    "ImportDefaults",
    "from_pandapower",
    "preview_pandapower_import",
    "read_shortcircuit_results",
    "apply_shortcircuit_characteristics",
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
__version__ = "0.4.0"

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


# These will be initialized by start_dbsession()
engine = None
SessionLocal = None
session: Optional[scoped_session] = None
# ``db_session`` is a historic public alias for ``session`` advertised
# in ``__all__``. Keeping the alias keeps ``from groundinsight import
# db_session`` working for users that copy/pasted the documented
# spelling. The alias points at ``None`` while no session is active;
# :func:`_set_session` is the *single source of truth* that updates
# both names in lock-step. ``start_dbsession`` / ``close_dbsession``
# (and any future helper such as a planned ``swap_dbsession`` context
# manager) MUST route through :func:`_set_session` instead of assigning
# the global directly. This keeps the two names from drifting apart
# across cross-API changes.
db_session: Optional[scoped_session] = None


def _set_session(new: Optional[scoped_session]) -> None:
    """Central setter for the module-level scoped session globals.

    Pins ``session`` and the historic alias ``db_session`` to the same
    object in a single assignment. Every code path that mutates the
    module-level scoped session (``start_dbsession``,
    ``close_dbsession``, future ``swap_dbsession`` helpers, tests that
    monkey-patch the session) MUST go through this helper so the two
    names cannot drift.

    Parameters
    ----------
    new : sqlalchemy.orm.scoped_session or None
        The new scoped session, or ``None`` to clear both names.

    Notes
    -----
    Earlier revisions rebound ``session`` and ``db_session`` independently
    in two separate helpers, so a future fourth helper that forgot one of
    the two names would silently drift them apart. The contract is now
    pinned by a single source of truth.
    """
    global session, db_session
    session = new
    db_session = new


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


def start_dbsession(
    sqlite_path: str = "grounding.db",
    force: bool = False,
    migrate: bool = True,
):
    """
    Initialise the database session.

    Sets up the SQLAlchemy engine and sessionmaker, creates a scoped
    session, and initialises the database tables based on the defined
    models. The helper is *idempotent* in the safe direction: if a session
    is already active and the caller asks for the *same* ``sqlite_path``,
    the previous binding is retained and a ``logger.warning`` is emitted.
    Re-binding to a *different* database is a likely user error and raises
    ``RuntimeError`` unless ``force=True`` is given, in which case the
    previous engine is properly disposed before the swap so no scoped
    session is leaked.

    A database file written by an earlier release is migrated to the current
    schema *before* the engine is bound, after the unmodified file has been
    copied to ``<sqlite_path>.bak``. Anything the migration could not recover
    is logged at ``WARNING``; see
    :func:`groundinsight.database.migration.migrate_database`.

    Parameters
    ----------
    sqlite_path : str, optional
        The file path for the SQLite database. Defaults to
        ``"grounding.db"``.
    force : bool, optional
        If ``True``, dispose any existing engine / scoped session before
        binding to ``sqlite_path``. Defaults to ``False``.
    migrate : bool, optional
        If ``True`` (the default), convert a database written by an earlier
        release, keeping a ``.bak`` copy. Set to ``False`` to leave the file
        untouched and get an explanatory ``RuntimeError`` from the first read
        or write instead.

    Raises
    ------
    RuntimeError
        If a database session is already active for a different
        ``sqlite_path`` and ``force`` is not set.
    """
    global engine, SessionLocal

    if engine is not None:
        current_url = str(engine.url)
        requested_url = f"sqlite:///{sqlite_path}"
        if current_url == requested_url and not force:
            logger.warning(
                "Database session already started for '%s' — re-using existing engine.",
                sqlite_path,
            )
            return
        if not force:
            raise RuntimeError(
                "A database session is already active for "
                f"'{current_url}'. Call gi.close_dbsession() before "
                f"binding to '{sqlite_path}', or pass force=True to "
                "dispose the existing engine and rebind."
            )
        # force=True path: tear the previous session down cleanly so we
        # do not leak the scoped session registry or open transactions.
        logger.warning(
            "Forcing re-bind of database session from '%s' to '%s'.",
            current_url,
            requested_url,
        )
        close_dbsession()

    # Convert a file written by an earlier release *before* binding to it.
    # ``create_all`` below only ever creates missing tables; it would leave a
    # legacy file looking fine until the first query hit a missing column.
    if migrate:
        migrate_database(sqlite_path)

    # Create an engine
    engine = create_engine(f"sqlite:///{sqlite_path}", echo=False)

    # Create a configured "Session" class
    SessionLocal = sessionmaker(bind=engine)

    # Create a thread-safe session via the single-source-of-truth setter
    # so ``session`` and the historic ``db_session`` alias cannot drift.
    _set_session(scoped_session(SessionLocal))

    # Import Base from your models and create tables
    from .models.database_models import Base

    Base.metadata.create_all(engine)
    logger.info("Database session started with '%s'.", sqlite_path)


def close_dbsession():
    """
    Close the database session.

    Removes the scoped session, disposes of the engine, and resets the
    session globals. Each of the three module-level handles
    (``session``, ``engine``, ``SessionLocal``) is nulled *independently*
    — if a previous ``start_dbsession(..., force=True)`` was interrupted
    between ``engine.dispose()`` and the re-assignment of ``session``,
    the asymmetric state will no longer trip an ``AttributeError`` here.
    The legacy ``db_session`` alias is kept in lock-step with ``session``.

    Notes
    -----
    Logs at ``WARNING`` if *no* state at all is set; logs at ``INFO`` if
    at least one component was torn down. Never raises.
    """
    global engine, SessionLocal

    if session is None and engine is None and SessionLocal is None:
        logger.warning("No database session to close.")
        return

    # Tear each piece down independently so a half-constructed state
    # ("engine present, session missing" or vice versa) cannot deadlock
    # the helper. ``session.remove()`` is idempotent.
    if session is not None:
        try:
            session.remove()
        except Exception as exc:  # pragma: no cover - defensive
            logger.warning("Error while removing scoped session: %s", exc)
        # Route through the central setter so the legacy ``db_session``
        # alias is cleared in the same assignment as ``session``.
        _set_session(None)
    if engine is not None:
        try:
            engine.dispose()
        except Exception as exc:  # pragma: no cover - defensive
            logger.warning("Error while disposing engine: %s", exc)
        engine = None
    SessionLocal = None
    logger.info("Database session closed.")


def save_bustype_to_db(bus_type: BusType, overwrite: bool = False):
    """
    Save a BusType to the database.

    If a BusType with the same name already exists and ``overwrite`` is
    ``False``, a ``ValueError`` is raised. If ``overwrite`` is ``True``,
    the existing BusType is updated.

    Parameters
    ----------
    bus_type : BusType
        The BusType instance to save.
    overwrite : bool, optional
        Whether to overwrite an existing BusType with the same name.
        Defaults to ``False``.

    Raises
    ------
    RuntimeError
        If the database session is not started.
    ValueError
        If the BusType already exists and ``overwrite`` is ``False``.
    """
    if session is None:
        raise RuntimeError(
            "Database session is not started. Call gi.start_dbsession() first."
        )
    db_session = session()
    try:
        existing = db_session.get(BusTypeDB, bus_type.name)
        if existing and not overwrite:
            raise ValueError(
                f"BusType '{bus_type.name}' already exists. Use overwrite=True to overwrite."
            )
        _save_bustype(bus_type, db_session)
    finally:
        db_session.close()


def load_bustypes_from_db() -> Dict[str, BusType]:
    """
    Load all BusTypes from the database.

    Returns
    -------
    dict of str to BusType
        A dictionary of BusType instances keyed by their names.

    Raises
    ------
    RuntimeError
        If the database session is not started.
    """
    if session is None:
        raise RuntimeError(
            "Database session is not started. Call gi.start_dbsession() first."
        )
    db_session = session()
    try:
        bus_types = _load_bustypes(db_session)
    finally:
        db_session.close()
    return bus_types


def save_branchtype_to_db(branch_type: BranchType, overwrite: bool = False):
    """
    Save a BranchType to the database.

    If a BranchType with the same name already exists and ``overwrite``
    is ``False``, a ``ValueError`` is raised. If ``overwrite`` is
    ``True``, the existing BranchType is updated.

    Parameters
    ----------
    branch_type : BranchType
        The BranchType instance to save.
    overwrite : bool, optional
        Whether to overwrite an existing BranchType with the same name.
        Defaults to ``False``.

    Raises
    ------
    RuntimeError
        If the database session is not started.
    ValueError
        If the BranchType already exists and ``overwrite`` is ``False``.
    """
    if session is None:
        raise RuntimeError(
            "Database session is not started. Call gi.start_dbsession() first."
        )
    db_session = session()
    try:
        existing = db_session.get(BranchTypeDB, branch_type.name)
        if existing and not overwrite:
            raise ValueError(
                f"BranchType '{branch_type.name}' already exists. Use overwrite=True to overwrite."
            )
        _save_branchtype(branch_type, db_session)
    finally:
        db_session.close()


def load_branchtypes_from_db() -> Dict[str, BranchType]:
    """
    Load all BranchTypes from the database.

    Returns
    -------
    dict of str to BranchType
        A dictionary of BranchType instances keyed by their names.

    Raises
    ------
    RuntimeError
        If the database session is not started.
    """
    if session is None:
        raise RuntimeError(
            "Database session is not started. Call gi.start_dbsession() first."
        )
    db_session = session()
    try:
        branch_types = _load_branchtypes(db_session)
    finally:
        db_session.close()
    return branch_types


def save_network_to_db(network: Network, overwrite: bool = False):
    """
    Save a Network to the database.

    If a Network with the same name already exists and ``overwrite`` is
    ``False``, a ``ValueError`` is raised. If ``overwrite`` is ``True``,
    the existing Network is replaced.

    Parameters
    ----------
    network : Network
        The Network instance to save.
    overwrite : bool, optional
        Whether to overwrite an existing Network with the same name.
        Defaults to ``False``.

    Raises
    ------
    RuntimeError
        If the database session is not started.
    ValueError
        If the Network already exists and ``overwrite`` is ``False``.
    """
    if session is None:
        raise RuntimeError(
            "Database session is not started. Call gi.start_dbsession() first."
        )
    db_session = session()
    try:
        existing = db_session.get(NetworkDB, network.name)
        if existing and not overwrite:
            raise ValueError(
                f"Network '{network.name}' already exists. Use overwrite=True to overwrite."
            )
        _save_network(network, db_session, overwrite=overwrite)
    finally:
        db_session.close()


def load_network_from_db(name: str) -> Network:
    """
    Load a Network from the database by name.

    Parameters
    ----------
    name : str
        The name of the Network to load.

    Returns
    -------
    Network
        The loaded Network instance.

    Raises
    ------
    RuntimeError
        If the database session is not started.
    ValueError
        If the Network with the specified name does not exist.
    """
    if session is None:
        raise RuntimeError(
            "Database session is not started. Call gi.start_dbsession() first."
        )
    db_session = session()
    try:
        network = _load_network(name, db_session)
    finally:
        db_session.close()
    return network


def save_network_to_json(network: Network, path: str):
    """
    Save a Network instance to a JSON file.

    Serialises the Network instance into JSON format and writes it to the
    specified file path.

    Parameters
    ----------
    network : Network
        The Network instance to serialise and save.
    path : str
        The file path where the JSON file will be saved.

    Raises
    ------
    IOError
        If there is an error writing to the file.
    """
    path = Path(path)
    with path.open("w") as f:
        f.write(network.model_dump_json(indent=4))


def load_network_from_json(path: str) -> Network:
    """
    Load a Network instance from a JSON file.

    Parameters
    ----------
    path : str
        The file path of the JSON file to load.

    Returns
    -------
    Network
        The deserialised Network instance.

    Raises
    ------
    IOError
        If there is an error reading the file.
    ValueError
        If the JSON content is invalid or does not conform to the Network
        model.
    """
    path = Path(path)
    with path.open("r") as f:
        json_string = f.read()
        model_instance = Network.model_validate_json(json_string)
    return model_instance

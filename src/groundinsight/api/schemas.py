# api/schemas.py
"""
Data transfer objects (DTOs) between the service layer and a front end.

They are the only data shape that crosses that boundary.

A front end -- the NiceGUI app, the command-line interface, a later REST
server and its web client -- must not reach into :class:`~groundinsight.Network`
or the ``Result`` models to draw something. Those are the library's working
objects: they carry ``ComplexNumber`` phasors, frequency-keyed dictionaries
and private solver state, and their shape follows the physics. A screen that
picks numbers out of them is where "physics in the GUI" starts (a wrong
field, a magnitude taken of the wrong phasor). The models in this module are
what a front end gets instead, and they are deliberately plain:

* **Strictly JSON-safe.** Every value is ``str``, ``bool``, ``int``, a
  *finite* ``float``, ``None``, or a list/dict of those. A non-finite number --
  the infinite impedance of an open end (formula ``"nan"``), an undefined
  ratio -- becomes ``None`` when the DTO is built, in Python as well as in
  JSON: a browser's ``JSON.parse`` rejects ``NaN`` and ``Infinity``, and a
  ``None`` cannot be mistaken for a number by a template either.
* **Versioned.** Every DTO carries ``dto_schema_version``
  (:data:`DTO_SCHEMA_VERSION`). A change that a reader of the previous
  version would misread increments it; a validator refuses a DTO of a
  version it does not know (:data:`READABLE_DTO_SCHEMA_VERSIONS`) instead of
  guessing. Version 2 added fields only -- the diagram and characteristic
  DTOs, the touch-voltage columns of the key figures, the rename, diagram
  and characteristic commands -- each with a default, so a DTO or a request
  of version 1 reads unchanged; a reader of version 1 would refuse the new
  fields, hence the new number.
* **Schemas of a REST API.** The DTOs are pydantic models, so they are the
  request and response schemas of a future HTTP layer as they stand:
  :func:`json_schemas` returns the JSON schema of every DTO, of the edit
  commands (:mod:`groundinsight.api.commands`, one discriminated union keyed
  by ``op``) and of the job requests (keyed by ``task``).
* **Tables are tables.** A table is a list of columns plus a list of row
  dictionaries keyed by column name -- what an AG Grid, a CSV writer and a
  test assertion all consume without an adapter. The element tables use the
  pinned schema of :meth:`Network.to_tables <groundinsight.Network.to_tables>`,
  the result tables those of ``res_buses``/``res_branches``, and the key
  figures the columns of :meth:`Network.res_fault
  <groundinsight.Network.res_fault>` -- the same names a notebook sees.

Rejected alternatives: handing out Polars frames (a frame is not a wire
format, and a front end holding one starts deriving numbers from it), and
exposing the pydantic result models (couples every screen to the library's
internal model and its ``ComplexNumber`` encoding).
"""

import logging
import math
from typing import Annotated, Any, Dict, List, Literal, Optional, Tuple, Union

from pydantic import (
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    TypeAdapter,
    field_validator,
    model_validator,
)

logger = logging.getLogger(__name__)

__all__ = [
    "DTO_SCHEMA_VERSION",
    "READABLE_DTO_SCHEMA_VERSIONS",
    "Cell",
    "CharacteristicDTO",
    "CharacterizeRequestDTO",
    "CharacterizeResultDTO",
    "BusResponseRequestDTO",
    "ColumnDTO",
    "CheckReportDTO",
    "CommandResultDTO",
    "ComplexDTO",
    "CriteriaDTO",
    "AssessmentDTO",
    "CutDTO",
    "CutsRequestDTO",
    "DiagnosticDTO",
    "DiagramDTO",
    "DiagramEdgeDTO",
    "DiagramMarkerDTO",
    "DiagramNodeDTO",
    "ElementRefDTO",
    "FaultResultDTO",
    "FaultResultsDTO",
    "JobErrorDTO",
    "JobRequest",
    "JobStatusDTO",
    "KeyFigureRowDTO",
    "KeyFiguresDTO",
    "MaxRhoRequestDTO",
    "NetworkSummaryDTO",
    "NetworkTablesDTO",
    "OutageScenarioDTO",
    "OutageStudyRequestDTO",
    "ProgressDTO",
    "ReferenceCasesRequestDTO",
    "SessionLimitsDTO",
    "SolveAllRequestDTO",
    "SolveRequestDTO",
    "StudyResultDTO",
    "SweepPointDTO",
    "SweepRequestDTO",
    "TableDTO",
    "ThermalRequestDTO",
    "TouchCheckRequestDTO",
    "TransientRequestDTO",
    "ViewBlockDTO",
    "ViewDTO",
    "finite_or_none",
    "json_schemas",
    "parse_job_request",
]

DTO_SCHEMA_VERSION = 2
"""Version of the DTO layout this release writes. Incremented when a field
changes its meaning or shape, or a field is added -- whenever a reader of the
previous version would misread or refuse a DTO of this one."""

READABLE_DTO_SCHEMA_VERSIONS = (1, 2)
"""Versions of the DTO layout this release reads. Version 1 lacks the fields
version 2 added, which take their defaults."""


def finite_or_none(value: Any) -> Optional[float]:
    """
    A number as a finite ``float``, or ``None`` when that is impossible.

    Parameters
    ----------
    value : Any
        A number (``int``, ``float``, NumPy scalar), ``None``, or anything
        else.

    Returns
    -------
    Optional[float]
        ``float(value)`` when it is finite; ``None`` for ``None``, for
        ``NaN``/``inf``, and for a value that is not a number.

    Examples
    --------
    >>> finite_or_none(1.5)
    1.5
    >>> finite_or_none(float("inf")) is None, finite_or_none(None) is None
    (True, True)
    """
    if value is None or isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _finite_float(value: Any) -> Any:
    """
    Field validator: map a non-finite number to ``None``, leave the rest.

    Parameters
    ----------
    value : Any
        The raw input of an optional float field.

    Returns
    -------
    Any
        ``None`` for ``NaN``/``inf``; otherwise ``value`` unchanged, so that
        pydantic still rejects a value that is not a number at all.
    """
    if isinstance(value, float) and not math.isfinite(value):
        return None
    return value


#: An optional float that is never ``NaN`` or infinite.
FiniteFloat = Annotated[Optional[float], BeforeValidator(_finite_float)]

#: One table cell: ``None``, ``bool``, ``int``, finite ``float`` or ``str``.
Cell = Optional[Union[bool, int, float, str]]


def _cell(value: Any) -> Any:
    """
    Normalise one table cell to a JSON-safe value.

    Parameters
    ----------
    value : Any
        A value out of a Polars frame or a model.

    Returns
    -------
    Any
        ``None`` for non-finite floats, the value itself for ``None``,
        ``bool``, ``int``, ``float`` and ``str``, and ``str(value)`` for
        anything else (a date, a list), so a table never carries a type the
        wire format cannot express.
    """
    if value is None or isinstance(value, (bool, str)):
        return value
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    number = finite_or_none(value) if hasattr(value, "__float__") else None
    if number is not None and not isinstance(value, complex):
        return number
    return str(value)


class BaseDTO(BaseModel):
    """
    Base of every DTO: no unknown keys, a schema version, JSON-safe numbers.

    Internal API: the command classes derive from it too; not exported, may
    change in any release.

    Attributes
    ----------
    dto_schema_version : int
        The version the DTO was written with: :data:`DTO_SCHEMA_VERSION` for
        a DTO built here, an earlier one of
        :data:`READABLE_DTO_SCHEMA_VERSIONS` for one read from an older
        writer; any other version is refused.
    """

    model_config = ConfigDict(extra="forbid", ser_json_inf_nan="null")

    dto_schema_version: Literal[READABLE_DTO_SCHEMA_VERSIONS] = DTO_SCHEMA_VERSION  # type: ignore[valid-type]


# ---------------------------------------------------------------------------
# diagnostics
# ---------------------------------------------------------------------------


class ElementRefDTO(BaseDTO):
    """
    A reference to one network element.

    Attributes
    ----------
    kind : str
        ``"bus"``, ``"branch"``, ``"source"``, ``"fault"``, ``"bus_type"``,
        ``"branch_type"`` or ``"network"``.
    name : str
        The element's name.
    """

    kind: str
    name: str


class DiagnosticDTO(BaseDTO):
    """
    One structured message of the library (:class:`groundinsight.Diagnostic`).

    Attributes
    ----------
    code : str
        Stable upper-case identifier; programs branch on it, not on the text.
    severity : {"info", "warning", "error"}
        How strongly it affects the reading of a result; ``"error"`` in a
        pre-flight check means the network cannot be solved as described.
    category : str
        Name of the warning class that carried the message.
    message : str
        The library's text, unchanged.
    elements : list of ElementRefDTO
        The elements the message is about -- what a GUI links to.
    """

    code: str
    severity: Literal["info", "warning", "error"] = "warning"
    category: str
    message: str
    elements: List[ElementRefDTO] = Field(default_factory=list)


class CheckReportDTO(BaseDTO):
    """
    The pre-flight check of a network (:func:`groundinsight.check_network`).

    Attributes
    ----------
    revision : int
        Session revision the check describes.
    fault : str, optional
        The fault the fault-specific checks were restricted to, if any.
    ok : bool
        ``True`` when no record has severity ``"error"``.
    n_errors, n_warnings : int
        Record counts by severity.
    diagnostics : list of DiagnosticDTO
        Every finding, in the library's order.
    """

    revision: int
    fault: Optional[str] = None
    ok: bool
    n_errors: int
    n_warnings: int
    diagnostics: List[DiagnosticDTO] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# tables
# ---------------------------------------------------------------------------


class ColumnDTO(BaseDTO):
    """
    One table column.

    Attributes
    ----------
    name : str
        Key of the column in every row dictionary -- the library's column
        name (``specific_earth_resistance``, ``EPR_V``, ...).
    dtype : {"str", "float", "int", "bool", "null"}
        The value type of the column.
    unit : str, optional
        Physical unit where the name does not carry one (``"km"`` for
        ``length``, ``"Ohm*m"`` for ``specific_earth_resistance``).
    """

    name: str
    dtype: Literal["str", "float", "int", "bool", "null"] = "str"
    unit: Optional[str] = None


class TableDTO(BaseDTO):
    """
    A table as columns plus row dictionaries.

    Attributes
    ----------
    name : str
        Identifier of the table (``"buses"``, ``"bus_results"``, ...).
    columns : list of ColumnDTO
        The columns, in order.
    rows : list of dict
        One dictionary per row, keyed by column name, with exactly the
        columns' keys. Non-finite numbers are stored as ``None``.

    Raises
    ------
    ValueError
        On construction, if a row's keys differ from the column names.
    """

    name: str
    columns: List[ColumnDTO]
    rows: List[Dict[str, Cell]] = Field(default_factory=list)

    @field_validator("rows", mode="before")
    @classmethod
    def _json_safe_rows(cls, rows: Any) -> Any:
        """Normalise every cell (see :func:`_cell`) before validation."""
        if not isinstance(rows, list):
            return rows
        return [
            {key: _cell(value) for key, value in row.items()} if isinstance(row, dict) else row
            for row in rows
        ]

    @model_validator(mode="after")
    def _rows_match_columns(self) -> "TableDTO":
        """Refuse a row whose keys are not exactly the column names."""
        names = [column.name for column in self.columns]
        expected = set(names)
        for index, row in enumerate(self.rows):
            if set(row) != expected:
                raise ValueError(
                    f"table '{self.name}': row {index} has the keys "
                    f"{sorted(row)}, the columns are {names}"
                )
        return self


class NetworkTablesDTO(BaseDTO):
    """
    The input of a network as ten tables.

    The tables are those of :meth:`Network.to_tables
    <groundinsight.Network.to_tables>`.

    Attributes
    ----------
    revision : int
        Session revision the tables describe.
    tables : dict of str to TableDTO
        ``network``, ``frequencies``, ``bus_types``, ``branch_types``,
        ``buses``, ``branches``, ``sources``, ``source_values``, ``faults``,
        ``fault_scalings`` -- in that order and with the pinned schema of
        :data:`groundinsight.io.TABLE_SCHEMAS`. The type tables also list the
        session's catalogue types that no element uses yet.
    """

    revision: int
    tables: Dict[str, TableDTO]


# ---------------------------------------------------------------------------
# session state
# ---------------------------------------------------------------------------


class SessionLimitsDTO(BaseDTO):
    """
    Resource limits of a session (a server sets them per user).

    Attributes
    ----------
    max_buses, max_branches : int, optional
        Largest network the session opens or edits into; ``None`` is no limit.
    max_job_runtime_s : float, optional
        Wall-clock limit of one job; ``None`` is no limit.
    max_undo : int, optional
        Number of commands kept for undo; older ones are dropped.
    max_paths : int, optional
        Cap of the path enumeration on the session's network (its
        ``PathfinderSettings.max_paths``); ``None`` keeps the network's
        settings.
    """

    max_buses: Optional[int] = Field(default=None, ge=1)
    max_branches: Optional[int] = Field(default=None, ge=0)
    max_job_runtime_s: Optional[float] = Field(default=None, gt=0, allow_inf_nan=False)
    max_undo: Optional[int] = Field(default=500, ge=1)
    max_paths: Optional[int] = Field(default=None, ge=1)


class NetworkSummaryDTO(BaseDTO):
    """
    What a header bar or a project overview shows.

    Attributes
    ----------
    name, description : str
        Of the network.
    frequencies : list of float
        The network frequencies in Hz.
    n_buses, n_branches, n_sources, n_faults : int
        Element counts.
    bus_types, branch_types : list of str
        Names in the session's type catalogue.
    faults : list of str
        Fault names, in network order.
    results : list of str
        Faults with a stored result.
    stale_results : list of str
        Faults whose stored result no longer matches the network
        (:meth:`Network.stale_results <groundinsight.Network.stale_results>`).
    revision : int
        Current session revision.
    dirty : bool
        ``True`` when the network differs from the last opened/saved state.
    can_undo, can_redo : bool
        Whether undo/redo would do something.
    undo_label, redo_label : str, optional
        Label of the command undo/redo would act on.
    location : str, optional
        Where the project is stored (a file path or ``"<store>:<key>"``).
    limits : SessionLimitsDTO
        The session's resource limits.
    groundinsight_version : str
        Version of the library computing the results.
    """

    name: str
    description: Optional[str] = None
    frequencies: List[float]
    n_buses: int
    n_branches: int
    n_sources: int
    n_faults: int
    bus_types: List[str]
    branch_types: List[str]
    faults: List[str]
    results: List[str]
    stale_results: List[str]
    revision: int
    dirty: bool
    can_undo: bool
    can_redo: bool
    undo_label: Optional[str] = None
    redo_label: Optional[str] = None
    location: Optional[str] = None
    limits: SessionLimitsDTO
    groundinsight_version: str


class CommandResultDTO(BaseDTO):
    """
    Outcome of an applied, undone or redone command.

    Attributes
    ----------
    op : str
        The command's operation (``"update_bus"``, ...).
    label : str
        Human-readable description, as listed in the history.
    revision : int
        Session revision afterwards.
    diagnostics : list of DiagnosticDTO
        Library warnings raised while the command ran (a frequency order, a
        source value at a frequency the network does not solve, a DC limit).
    stale_results : list of str
        Faults whose stored result is stale afterwards.
    can_undo, can_redo : bool
        History state afterwards.
    """

    op: str
    label: str
    revision: int
    diagnostics: List[DiagnosticDTO] = Field(default_factory=list)
    stale_results: List[str] = Field(default_factory=list)
    can_undo: bool
    can_redo: bool


# ---------------------------------------------------------------------------
# results
# ---------------------------------------------------------------------------


class KeyFigureRowDTO(BaseDTO):
    """
    One row of :meth:`Network.res_fault <groundinsight.Network.res_fault>`.

    The fields are the columns of :data:`groundinsight.RES_FAULT_SCHEMA`, with
    the same names, order and meaning; see that method for the definitions.
    ``frequency_Hz`` is ``None`` on the RMS row (``is_rms`` true), and every
    number the result marks as undefined is ``None``.

    Attributes
    ----------
    fault, fault_bus : str
        The fault and the bus the result was computed for.
    frequency_Hz : float or None
        The frequency of the row; ``None`` on the RMS row.
    is_rms : bool
        Whether this is the RMS row over all frequencies.
    I_F_A, I_F_degree : float or None
        The fault current ``I_F``: magnitude in A, angle in degrees.
    EPR_fault_V, EPR_fault_degree : float or None
        The EPR at the fault bus: magnitude in V, angle in degrees.
    EPR_max_V : float or None
        The largest EPR in the network, in V.
    EPR_max_bus : str or None
        The bus it occurs at.
    r : float or None
        The reduction factor ``|I_E| / |I_F|``.
    I_E_A : float or None
        The earth current ``|I_E| = |U_E / Z_E|``, in A.
    U_E_V : float or None
        The earthing voltage ``|U_E|`` of EN 50522 (the EPR at the fault
        bus), in V.
    Z_E_Ohm, Z_E_degree : float or None
        The earthing impedance ``Z_E``, the driving-point impedance of the
        earthing network at the fault bus: magnitude in ohms, angle in
        degrees.
    n_diagnostics : int
        The number of diagnostics of the result, the same on every row.
    U_vT_max_V : float or None
        The largest prospective touch voltage ``U_vT`` over the buses with
        an earthing characteristic, in V.
    U_vT_max_bus : str or None
        The bus it occurs at.
    U_transfer_max_V : float or None
        The largest transferred potential, in V.
    U_transfer_max_bus : str or None
        The bus it occurs at.
    """

    fault: str
    fault_bus: str
    frequency_Hz: FiniteFloat = None
    is_rms: bool
    I_F_A: FiniteFloat = None
    I_F_degree: FiniteFloat = None
    EPR_fault_V: FiniteFloat = None
    EPR_fault_degree: FiniteFloat = None
    EPR_max_V: FiniteFloat = None
    EPR_max_bus: Optional[str] = None
    r: FiniteFloat = None
    I_E_A: FiniteFloat = None
    U_E_V: FiniteFloat = None
    Z_E_Ohm: FiniteFloat = None
    Z_E_degree: FiniteFloat = None
    n_diagnostics: int = 0
    U_vT_max_V: FiniteFloat = None
    U_vT_max_bus: Optional[str] = None
    U_transfer_max_V: FiniteFloat = None
    U_transfer_max_bus: Optional[str] = None

    @model_validator(mode="before")
    @classmethod
    def _drop_earlier_factors(cls, data: Any) -> Any:
        """
        Read a row written before there was one reduction factor.

        Parameters
        ----------
        data : Any
            The raw row.

        Returns
        -------
        Any
            The row without ``r_epr`` and ``r_current``, whose definitions no
            longer exist; ``r`` stays empty, as the row carries no value of
            the current definition.
        """
        if isinstance(data, dict) and ("r_epr" in data or "r_current" in data):
            data = {k: v for k, v in data.items() if k not in ("r_epr", "r_current")}
        return data


class KeyFiguresDTO(BaseDTO):
    """
    The key figures of one fault: one row per frequency plus the RMS row.

    Attributes
    ----------
    fault, fault_bus : str
        The fault and its bus.
    rows : list of KeyFigureRowDTO
        In the order of ``res_fault``: the frequencies, then the RMS row.
    """

    fault: str
    fault_bus: str
    rows: List[KeyFigureRowDTO]

    def rms(self) -> Optional[KeyFigureRowDTO]:
        """
        The RMS row.

        Returns
        -------
        KeyFigureRowDTO or None
            The row with ``is_rms`` true, if any.
        """
        return next((row for row in self.rows if row.is_rms), None)

    def at(self, frequency: float) -> Optional[KeyFigureRowDTO]:
        """
        The row of one frequency.

        Parameters
        ----------
        frequency : float
            The frequency in Hz.

        Returns
        -------
        KeyFigureRowDTO or None
            The row of ``frequency``, if the result covers it.
        """
        return next(
            (row for row in self.rows if not row.is_rms and row.frequency_Hz == frequency),
            None,
        )


class FaultResultDTO(BaseDTO):
    """
    Everything a front end shows of one solved fault.

    Attributes
    ----------
    fault, fault_bus : str
        The fault and its bus.
    revision : int, optional
        Session revision of the inputs the result was computed from;
        ``None`` for a result that came with the project file.
    input_digest : str, optional
        :attr:`Result.input_digest <groundinsight.Result.input_digest>` --
        the reproducibility anchor a report prints.
    phase_current_mode : str, optional
        ``"auto"`` or ``"paths"``, as solved; ``None`` when not known.
    stale : bool
        ``True`` when the network has changed since (the library's
        :meth:`Network.is_result_stale <groundinsight.Network.is_result_stale>`).
    key_figures : KeyFiguresDTO
        The rows of ``res_fault``.
    buses, branches : TableDTO
        The rows of ``res_buses``/``res_branches`` (long format, one row per
        element and frequency plus one RMS row per element).
    diagnostics : list of DiagnosticDTO
        :attr:`Result.diagnostics <groundinsight.Result.diagnostics>`.
    groundinsight_version : str
        Library version that served the result.
    duration_s : float, optional
        Wall time of the solve; ``None`` when not measured.
    """

    fault: str
    fault_bus: str
    revision: Optional[int] = None
    input_digest: Optional[str] = None
    phase_current_mode: Optional[str] = None
    stale: bool = False
    key_figures: KeyFiguresDTO
    buses: TableDTO
    branches: TableDTO
    diagnostics: List[DiagnosticDTO] = Field(default_factory=list)
    groundinsight_version: str
    duration_s: FiniteFloat = None


class FaultResultsDTO(BaseDTO):
    """
    The results of several faults (a fault sweep).

    Attributes
    ----------
    results : list of FaultResultDTO
        One per fault, in the order solved.
    """

    results: List[FaultResultDTO]


class CriteriaDTO(BaseDTO):
    """
    The assessment criteria of a project: the limits its results are held to.

    Stored with the project (section ``criteria`` of the project file) and
    set with :class:`~groundinsight.api.SetCriteria`. Every limit is the
    user's, cited from the standard or the operator's rules; the package
    keeps no limit table of its own.

    Attributes
    ----------
    u_e_limit_V : float, optional
        Limit of the earth potential rise ``U_E`` in V (for every bus).
    u_touch_limit : float or list of (float, float), optional
        Limit of the prospective touch voltage in V, or the curve
        ``[(t_F in s, U in V), ...]`` of the standard
        (:func:`groundinsight.check_touch_voltages`).
    t_f_s : float, optional
        Fault duration for the curve where the fault has none
        (``Fault.t_k_s``).
    r_to_x : float, optional
        R/X of the fault loop for the thermal check (IEC 60909 ``kappa``)
        where the sources carry no short-circuit data.
    branch_current_limits_A : dict of str to float
        Admissible short-time current in A per branch type name, at the fault
        duration; it replaces the adiabatic limit computed from material and
        cross-section for the branches of that type.
    """

    u_e_limit_V: Optional[float] = Field(default=None, gt=0, allow_inf_nan=False)
    u_touch_limit: Optional[Union[float, List[Tuple[float, float]]]] = None
    t_f_s: Optional[float] = Field(default=None, gt=0, allow_inf_nan=False)
    r_to_x: Optional[float] = Field(default=None, gt=0, allow_inf_nan=False)
    branch_current_limits_A: Dict[str, float] = Field(default_factory=dict)

    @field_validator("branch_current_limits_A")
    @classmethod
    def _positive_limits(cls, limits: Dict[str, float]) -> Dict[str, float]:
        """Every current limit is a positive, finite number."""
        for name, value in limits.items():
            if not (math.isfinite(value) and value > 0):
                raise ValueError(f"the current limit of type '{name}' must be positive, got {value!r}")
        return limits


class AssessmentDTO(BaseDTO):
    """
    The results of one fault held to the project's criteria, in percent.

    Attributes
    ----------
    fault : str
        The fault.
    buses : TableDTO
        Per bus: ``bus_name``, ``EPR_V`` (RMS), ``U_E_limit_V``,
        ``epr_utilisation_pct``, ``U_vT_V``, ``U_T_limit_V``,
        ``touch_utilisation_pct``, ``thermal_utilisation_pct`` (the largest of
        the bus's earthing conductor and electrode).
    branches : TableDTO
        Per branch: ``branch_name``, ``branch_type``, ``I_th_A``,
        ``I_limit_A``, ``limit_source`` (``"criteria"`` or ``"thermal"``),
        ``thermal_utilisation_pct``.
    notes : list of str
        Why a part could not be assessed (no limit given, thermal data
        missing, the R/X needed for ``kappa``).
    """

    fault: str
    buses: TableDTO
    branches: TableDTO
    notes: List[str] = Field(default_factory=list)


class StudyResultDTO(BaseDTO):
    """
    The result of a study (sweep, outage study, rho search).

    Attributes
    ----------
    study : str
        The job task that produced it (``"sweep"``, ``"outage"``,
        ``"max_rho"``).
    fault : str, optional
        The fault studied.
    revision : int, optional
        Session revision of the inputs.
    tables : dict of str to TableDTO
        The study's result frames, unchanged (see the job request).
    values : dict of str to Cell
        Scalar results (``c_max``, ``status``, point counts).
    failures : dict of str to str
        Points or scenarios that raised, with the exception text.
    diagnostics : list of DiagnosticDTO
        Library warnings raised during the study, without repetitions.
    groundinsight_version : str
        Library version.
    duration_s : float, optional
        Wall time of the study.
    """

    study: str
    fault: Optional[str] = None
    revision: Optional[int] = None
    tables: Dict[str, TableDTO] = Field(default_factory=dict)
    values: Dict[str, Cell] = Field(default_factory=dict)
    failures: Dict[str, str] = Field(default_factory=dict)
    diagnostics: List[DiagnosticDTO] = Field(default_factory=list)
    groundinsight_version: str
    duration_s: FiniteFloat = None

    @field_validator("values", mode="before")
    @classmethod
    def _json_safe_values(cls, values: Any) -> Any:
        """Normalise every scalar (see :func:`_cell`) before validation."""
        if not isinstance(values, dict):
            return values
        return {key: _cell(value) for key, value in values.items()}


# ---------------------------------------------------------------------------
# diagram
# ---------------------------------------------------------------------------

#: A point of the drawing area, ``[x, y]`` in JSON.
PointDTO = Tuple[float, float]


class DiagramNodeDTO(BaseDTO):
    """
    The symbol of one bus, with what a front end needs to draw and colour it.

    Attributes
    ----------
    name : str
        The bus.
    x, y : float
        Centre of the symbol.
    symbol : {"bus", "source_bus", "busbar"}
        The symbol.
    label_offset : tuple of float
        Position of the name relative to the centre.
    length : float
        Length of a busbar; 0 for the default.
    placed : bool
        ``False`` for a bus without an entry in the diagram yet: the position
        is the automatic layout's, not stored (a :class:`MoveNodes
        <groundinsight.api.MoveNodes>` stores it).
    bus_type : str
        Name of the bus type.
    voltage_level : float or None
        Voltage level of the bus type, in kV.
    active : bool
        Whether the bus takes part in the solve.
    has_source, has_fault : bool
        Whether a source feeds the bus, a fault sits at it.
    characteristic : {"bus", "bus_type"} or None
        Where the bus's earthing characteristic comes from, if it has one.
    """

    name: str
    x: float
    y: float
    symbol: Literal["bus", "source_bus", "busbar"] = "bus"
    label_offset: PointDTO = (0.0, 0.0)
    length: float = 0.0
    placed: bool = True
    bus_type: str
    voltage_level: FiniteFloat = None
    active: bool = True
    has_source: bool = False
    has_fault: bool = False
    characteristic: Optional[Literal["bus", "bus_type"]] = None


class DiagramEdgeDTO(BaseDTO):
    """
    The line of one branch.

    Attributes
    ----------
    name : str
        The branch.
    from_bus, to_bus : str
        Its buses.
    waypoints : list of tuple of float
        Intermediate points; empty for a straight line.
    label_offset : tuple of float
        Position of the label relative to the middle of the line.
    placed : bool
        ``False`` for a branch without an entry in the diagram yet.
    branch_type : str
        Name of the branch type.
    active : bool
        Whether the branch takes part in the solve (a switch).
    grounding_conductor : bool
        Whether the branch connects the earthing systems of its buses.
    """

    name: str
    from_bus: str
    to_bus: str
    waypoints: List[PointDTO] = Field(default_factory=list)
    label_offset: PointDTO = (0.0, 0.0)
    placed: bool = True
    branch_type: str
    active: bool = True
    grounding_conductor: bool = True


class DiagramMarkerDTO(BaseDTO):
    """
    The symbol of a fault or a source at its bus.

    Attributes
    ----------
    kind : {"fault", "source"}
        What it marks.
    name : str
        The fault or source.
    bus : str
        The bus it is drawn at.
    offset : tuple of float
        Position relative to the bus symbol.
    """

    kind: Literal["fault", "source"]
    name: str
    bus: str
    offset: PointDTO = (0.0, 0.0)


class DiagramDTO(BaseDTO):
    """
    The diagram of the network as a front end draws it.

    Every bus and branch of the network is listed, in network order -- a
    bus without a stored place at the position the automatic layout gives it
    (``placed`` false) -- and nothing else: entries of elements the network
    no longer has are reported in ``diagnostics``, not drawn.

    Attributes
    ----------
    revision : int
        Session revision the diagram describes.
    nodes : list of DiagramNodeDTO
        One per bus.
    edges : list of DiagramEdgeDTO
        One per branch.
    markers : list of DiagramMarkerDTO
        One per fault and source.
    color_scheme : str
        The colouring the diagram was last shown with.
    grid_size : float
        The snapping raster.
    diagnostics : list of DiagnosticDTO
        :meth:`Diagram.check <groundinsight.Diagram.check>` of the stored
        diagram.
    """

    revision: int
    nodes: List[DiagramNodeDTO] = Field(default_factory=list)
    edges: List[DiagramEdgeDTO] = Field(default_factory=list)
    markers: List[DiagramMarkerDTO] = Field(default_factory=list)
    color_scheme: str = "default"
    grid_size: float = 20.0
    diagnostics: List[DiagnosticDTO] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# earthing characteristics
# ---------------------------------------------------------------------------


class CharacteristicDTO(BaseDTO):
    """
    An earthing characteristic, as the characteristic tab of a bus shows it.

    Attributes
    ----------
    owner_kind : {"bus", "bus_type"}
        What was asked for: a bus or a bus type.
    owner : str
        Its name.
    origin : {"bus", "bus_type"}
        Where the characteristic hangs: at the bus itself or at its type.
        For a bus type, always ``"bus_type"``.
    name : str, optional
        Name of the characteristic.
    impedance_source : {"table", "formula"}
        Where the bus impedance comes from.
    impedance_formula : str, optional
        The characteristic's own formula.
    rho_Ohm_m, frequency_Hz : list of float
        The grid.
    table : TableDTO
        :meth:`EarthingCharacteristic.to_frame
        <groundinsight.EarthingCharacteristic.to_frame>`, one row per grid
        point.
    extrapolated : bool or None
        For a bus: whether its ``rho`` or a network frequency lies outside
        the grid (the diagnostic ``CHARACTERISTIC_EXTRAPOLATED``); ``None``
        for a bus type.
    provenance : dict
        :class:`groundinsight.CharacteristicProvenance` as JSON; empty
        without one.
    """

    owner_kind: Literal["bus", "bus_type"]
    owner: str
    origin: Literal["bus", "bus_type"]
    name: Optional[str] = None
    impedance_source: Literal["table", "formula"] = "table"
    impedance_formula: Optional[str] = None
    rho_Ohm_m: List[float]
    frequency_Hz: List[float]
    table: TableDTO
    extrapolated: Optional[bool] = None
    provenance: Dict[str, Any] = Field(default_factory=dict)


# ---------------------------------------------------------------------------
# evaluation tabs (views)
# ---------------------------------------------------------------------------


class ViewBlockDTO(BaseDTO):
    """
    One block of an evaluation tab: a chart or a table of one result quantity.

    Attributes
    ----------
    kind : {"chart", "table"}
        How it is shown.
    source : {"buses", "branches", "key_figures"}
        The result table it reads: ``res_buses``, ``res_branches`` or
        ``res_fault``.
    quantity : str
        A column of that table (``EPR_V``, ``U_vT_V``, ``I_branch_A``,
        ``U_E_V``, ...).
    elements : list of str
        Buses or branches to show; empty for all.
    frequency_Hz : float, optional
        The frequency; ``None`` for the RMS row.
    fault : str, optional
        The fault whose stored result is read; ``None`` for the fault the
        front end has selected.
    """

    kind: Literal["chart", "table"] = "chart"
    source: Literal["buses", "branches", "key_figures"] = "buses"
    quantity: str
    elements: List[str] = Field(default_factory=list)
    frequency_Hz: Optional[float] = Field(default=None, allow_inf_nan=False)
    fault: Optional[str] = None


class ViewDTO(BaseDTO):
    """
    An evaluation tab of a project, stored in the project file's ``views``.

    Attributes
    ----------
    id : str
        Unique identifier within the project.
    title : str
        The tab's title.
    blocks : list of ViewBlockDTO
        Its blocks, top to bottom.
    """

    id: str = Field(min_length=1)
    title: str
    blocks: List[ViewBlockDTO] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# jobs
# ---------------------------------------------------------------------------


class ProgressDTO(BaseDTO):
    """
    Progress of a job, as the library reports it (``progress(done, total, label)``).

    Attributes
    ----------
    done, total : int
        Units finished and planned.
    label : str
        Name of the last finished unit.
    fraction : float, optional
        ``done / total`` in ``[0, 1]``; ``None`` while ``total`` is 0.
    """

    done: int = 0
    total: int = 0
    label: str = ""
    fraction: FiniteFloat = None


class JobErrorDTO(BaseDTO):
    """
    Why a job failed.

    Attributes
    ----------
    type : str
        Exception class name (``"ValueError"``).
    message : str
        The exception text, unchanged.
    """

    type: str
    message: str


class JobStatusDTO(BaseDTO):
    """
    Snapshot of a job, safe to poll from a UI timer or an HTTP client.

    Attributes
    ----------
    job_id : str
        Identifier, unique within the runner.
    task : str
        ``"solve"``, ``"solve_all"``, ``"sweep"``, ``"outage"`` or ``"max_rho"``.
    label : str
        Human-readable description.
    backend : {"thread", "process"}
        Where the job runs.
    state : {"queued", "running", "done", "failed", "cancelled", "timeout"}
        ``"timeout"`` means the runtime limit stopped the job.
    revision : int, optional
        Session revision of the inputs the job computes on.
    progress : ProgressDTO
        Last progress report.
    elapsed_s : float, optional
        Wall time since the job started running.
    max_runtime_s : float, optional
        The job's runtime limit.
    error : JobErrorDTO, optional
        Set for a failed job.
    """

    job_id: str
    task: str
    label: str = ""
    backend: Literal["thread", "process"]
    state: Literal["queued", "running", "done", "failed", "cancelled", "timeout"]
    revision: Optional[int] = None
    progress: ProgressDTO = Field(default_factory=ProgressDTO)
    elapsed_s: FiniteFloat = None
    max_runtime_s: FiniteFloat = None
    error: Optional[JobErrorDTO] = None


# ---------------------------------------------------------------------------
# job requests (the request bodies of a future ``POST .../jobs``)
# ---------------------------------------------------------------------------

#: Phase-current mode of :func:`groundinsight.run_fault`.
PhaseCurrentMode = Literal["auto", "paths"]


class ComplexDTO(BaseDTO):
    """
    A complex value (a phasor or an impedance) as two finite numbers.

    Attributes
    ----------
    real, imag : float
        Real and imaginary part; both must be finite.
    """

    real: float = Field(allow_inf_nan=False)
    imag: float = Field(default=0.0, allow_inf_nan=False)

    def to_complex(self) -> complex:
        """
        The value as a Python ``complex``.

        Returns
        -------
        complex
            ``real + 1j * imag``.
        """
        return complex(self.real, self.imag)


#: A phasor in a request: a plain real number or a :class:`ComplexDTO`.
Phasor = Union[ComplexDTO, float]


class SolveRequestDTO(BaseDTO):
    """
    Solve one fault (:func:`groundinsight.solve`).

    Attributes
    ----------
    task : "solve"
        Discriminator.
    fault : str
        The fault.
    phase_current_mode : {"auto", "paths"}
        As in :func:`groundinsight.run_fault`.
    """

    task: Literal["solve"] = "solve"
    fault: str
    phase_current_mode: PhaseCurrentMode = "auto"


class SolveAllRequestDTO(BaseDTO):
    """
    Solve several faults in turn, cancellable between faults.

    Attributes
    ----------
    task : "solve_all"
        Discriminator.
    faults : list of str, optional
        The faults; ``None`` solves every fault of the network.
    phase_current_mode : {"auto", "paths"}
        As in :func:`groundinsight.run_fault`.
    """

    task: Literal["solve_all"] = "solve_all"
    faults: Optional[List[str]] = None
    phase_current_mode: PhaseCurrentMode = "auto"


class SweepPointDTO(BaseDTO):
    """
    One point of a parameter sweep (:class:`groundinsight.SweepPoint`).

    Attributes
    ----------
    label : str
        Unique label of the point.
    bus_rho : dict of str to float
        New ``specific_earth_resistance`` per bus.
    bus_impedance : dict of str to dict of float to Phasor
        Impedance tables written onto ``Bus.impedance`` per frequency.
    fault : str, optional
        Another fault for this point.
    fault_scalings : dict of float to float, optional
        Replacement scalings of the fault.
    parameters : dict of str to Cell
        Free values copied into the result rows.
    """

    label: str
    bus_rho: Dict[str, float] = Field(default_factory=dict)
    bus_impedance: Dict[str, Dict[float, Phasor]] = Field(default_factory=dict)
    fault: Optional[str] = None
    fault_scalings: Optional[Dict[float, float]] = None
    parameters: Dict[str, Cell] = Field(default_factory=dict)


class SweepRequestDTO(BaseDTO):
    """
    A parameter sweep (:func:`groundinsight.run_sweep`).

    The result's tables are ``buses``, ``impedances`` and, with
    ``collect_branches``, ``branches`` -- the frames of
    :class:`groundinsight.SweepResult`.

    Attributes
    ----------
    task : "sweep"
        Discriminator.
    fault : str
        The fault solved at every point (unless a point names its own).
    points : list of SweepPointDTO
        The parameter points.
    phase_current_mode : {"auto", "paths"}
        As in :func:`groundinsight.run_fault`.
    collect_branches : bool
        Also collect the branch results.
    summary_value : str, optional
        A column of the ``buses`` table summarised per bus over the points
        (:func:`groundinsight.summarize`: count, mean, extremes, quantiles)
        into the table ``summary``; ``None`` for none.
    """

    task: Literal["sweep"] = "sweep"
    fault: str
    points: List[SweepPointDTO]
    phase_current_mode: PhaseCurrentMode = "auto"
    collect_branches: bool = False
    summary_value: Optional[str] = "EPR_V"


class OutageScenarioDTO(BaseDTO):
    """
    One what-if scenario (:class:`groundinsight.Outage`).

    Attributes
    ----------
    name : str
        Unique label.
    disabled_buses, disabled_branches : list of str
        Elements switched off in the scenario.
    description : str, optional
        Free text.
    """

    name: str
    disabled_buses: List[str] = Field(default_factory=list)
    disabled_branches: List[str] = Field(default_factory=list)
    description: Optional[str] = None


class OutageStudyRequestDTO(BaseDTO):
    """
    An outage study (:func:`groundinsight.run_outage_study`).

    The result's tables are ``buses`` and ``branches``, the long-format
    comparisons ``compare_buses()``/``compare_branches()`` of
    :class:`groundinsight.OutageStudyResult`.

    Attributes
    ----------
    task : "outage"
        Discriminator.
    fault : str
        The fault.
    scenarios : list of OutageScenarioDTO
        The scenarios.
    include_base : bool
        Also solve the unchanged network as the reference scenario.
    phase_current_mode : {"auto", "paths"}
        As in :func:`groundinsight.run_fault`.
    """

    task: Literal["outage"] = "outage"
    fault: str
    scenarios: List[OutageScenarioDTO]
    include_base: bool = True
    phase_current_mode: PhaseCurrentMode = "auto"


class MaxRhoRequestDTO(BaseDTO):
    """
    The largest admissible rho scaling (:func:`groundinsight.find_max_rho_scaling`).

    The result's ``values`` hold the scalar entries of the library's report
    (``c_max``, ``status``, ...).

    Attributes
    ----------
    task : "max_rho"
        Discriminator.
    fault : str
        The fault.
    bus_names : list of str
        Buses whose specific earth resistance is scaled.
    u_limit : float
        EPR limit in volts.
    c_bounds : tuple of float
        Search interval of the factor.
    tol_rel : float
        Relative bracket tolerance.
    max_iter : int
        Bisection step limit.
    monotonicity_grid : int
        Points of the monotonicity scan.
    phase_current_mode : {"auto", "paths"}
        As in :func:`groundinsight.run_fault`.
    """

    task: Literal["max_rho"] = "max_rho"
    fault: str
    bus_names: List[str]
    u_limit: float = Field(allow_inf_nan=False)
    c_bounds: Tuple[float, float] = (1e-3, 1e3)
    tol_rel: float = 1e-3
    max_iter: int = 60
    monotonicity_grid: int = 9
    phase_current_mode: PhaseCurrentMode = "auto"


class CutDTO(BaseDTO):
    """
    One cut of a cut analysis (:class:`groundinsight.Cut`).

    Attributes
    ----------
    name : str
        Unique label.
    branches : list of str
        The branches cut, at least one.
    description : str, optional
        Free text.
    """

    name: str
    branches: List[str] = Field(min_length=1)
    description: Optional[str] = None


class CutsRequestDTO(BaseDTO):
    """
    Split the fault location at cuts (:func:`groundinsight.analyze_cuts`).

    The fault is solved first; the result's table ``cuts`` is
    ``CutAnalysis.to_polars()``.

    Attributes
    ----------
    task : "cuts"
        Discriminator.
    fault : str
        The fault.
    cuts : list of CutDTO
        The cuts.
    phase_current_mode : {"auto", "paths"}
        As in :func:`groundinsight.run_fault`.
    """

    task: Literal["cuts"] = "cuts"
    fault: str
    cuts: List[CutDTO] = Field(min_length=1)
    phase_current_mode: PhaseCurrentMode = "auto"


class BusResponseRequestDTO(BaseDTO):
    """
    The response of the network at a bus (:func:`groundinsight.bus_response`).

    The fault is solved first. The result's tables are ``extremes``
    (``BusResponse.extremes()``) and, with ``electrodes``, ``sweep``
    (``BusResponse.sweep``).

    Attributes
    ----------
    task : "bus_response"
        Discriminator.
    fault : str
        The fault.
    bus : str, optional
        The bus; default the fault's bus.
    electrodes : dict of str to Phasor
        Label to an electrode impedance in ohms (the same at every
        frequency) to evaluate the bus with.
    phase_current_mode : {"auto", "paths"}
        As in :func:`groundinsight.run_fault`.
    """

    task: Literal["bus_response"] = "bus_response"
    fault: str
    bus: Optional[str] = None
    electrodes: Dict[str, Phasor] = Field(default_factory=dict)
    phase_current_mode: PhaseCurrentMode = "auto"


class ThermalRequestDTO(BaseDTO):
    """
    The thermal check of conductors or nodes.

    :func:`groundinsight.check_conductor_limits` or
    :func:`groundinsight.check_node_limits` after a solve of the fault; the
    result's table ``limits`` is the library's frame.

    Attributes
    ----------
    task : "thermal"
        Discriminator.
    fault : str
        The fault.
    scope : {"conductors", "nodes"}
        Branch conductors, or the earthing conductors and electrodes of the
        buses.
    t_k_s : float, optional
        Fault duration in s; default ``Fault.t_k_s``.
    kappa, r_to_x : float, optional
        Peak factor or R/X ratio of IEC 60909 (one of them, unless the
        sources carry them).
    phase_current_mode : {"auto", "paths"}
        As in :func:`groundinsight.run_fault`.
    """

    task: Literal["thermal"] = "thermal"
    fault: str
    scope: Literal["conductors", "nodes"] = "conductors"
    t_k_s: Optional[float] = Field(default=None, gt=0, allow_inf_nan=False)
    kappa: Optional[float] = Field(default=None, gt=0, allow_inf_nan=False)
    r_to_x: Optional[float] = Field(default=None, ge=0, allow_inf_nan=False)
    phase_current_mode: PhaseCurrentMode = "auto"


class TouchCheckRequestDTO(BaseDTO):
    """
    The EN 50522 touch-voltage check (:func:`groundinsight.check_touch_voltages`).

    The fault is solved first; the result's table ``touch`` is the
    library's frame.

    Attributes
    ----------
    task : "touch"
        Discriminator.
    fault : str
        The fault.
    u_limit : float or list of (float, float)
        The limit in volts, or the curve ``[(t_F in s, U in V), ...]``.
    t_k_s : float, optional
        Fault duration in s; default ``Fault.t_k_s``.
    phase_current_mode : {"auto", "paths"}
        As in :func:`groundinsight.run_fault`.
    """

    task: Literal["touch"] = "touch"
    fault: str
    u_limit: Union[float, List[Tuple[float, float]]]
    t_k_s: Optional[float] = Field(default=None, gt=0, allow_inf_nan=False)
    phase_current_mode: PhaseCurrentMode = "auto"


class TransientRequestDTO(BaseDTO):
    """
    A transient simulation (:class:`groundinsight.TransientStudy`).

    Every source of the network gets the same waveform; the result's table
    ``series`` is ``ResultTransient.to_polars()`` of the observed buses.

    Attributes
    ----------
    task : "transient"
        Discriminator.
    fault : str
        The fault.
    t_end, dt : float
        End time and step in s.
    solver : {"fft", "state_space"}
        As in :meth:`groundinsight.TransientStudy.solve`.
    waveform : {"step", "sine"}
        A current step, or a sine with a decaying DC offset
        (:mod:`groundinsight.simulation.waveforms`).
    amplitude : float
        Peak current in A.
    frequency_Hz : float
        Frequency of the sine.
    dc_decay_tau : float, optional
        Time constant of the DC offset of the sine in s; ``None`` for none.
    t_off : float, optional
        The source is switched off at ``t_off`` (a pulse); ``None`` keeps it
        on. The FFT solver needs a response that has decayed by ``t_end``.
    zero_pad : bool
        Solve the FFT on a window twice as long (see
        :meth:`groundinsight.TransientStudy.solve`).
    observe_buses : list of str
        Buses whose potential is returned; empty for every bus.
    observe_branches : list of str
        Branches whose shield current is returned; empty for none.
    """

    task: Literal["transient"] = "transient"
    fault: str
    t_end: float = Field(gt=0, allow_inf_nan=False)
    dt: float = Field(gt=0, allow_inf_nan=False)
    solver: Literal["fft", "state_space"] = "fft"
    waveform: Literal["step", "sine"] = "step"
    amplitude: float = Field(default=1000.0, allow_inf_nan=False)
    frequency_Hz: float = Field(default=50.0, gt=0, allow_inf_nan=False)
    dc_decay_tau: Optional[float] = Field(default=None, gt=0, allow_inf_nan=False)
    t_off: Optional[float] = Field(default=None, gt=0, allow_inf_nan=False)
    zero_pad: bool = False
    observe_buses: List[str] = Field(default_factory=list)
    observe_branches: List[str] = Field(default_factory=list)


class ReferenceCasesRequestDTO(BaseDTO):
    """
    The closed-form reference cases (:func:`groundinsight.run_reference_cases`).

    The result's table ``cases`` is the library's frame; the session's
    network is not used.

    Attributes
    ----------
    task : "reference_cases"
        Discriminator.
    """

    task: Literal["reference_cases"] = "reference_cases"


class CharacterizeRequestDTO(BaseDTO):
    """
    An earthing characteristic computed with groundfield.

    :func:`groundinsight.integrations.groundfield.characterize` of a typical
    electrode (:func:`~groundinsight.integrations.groundfield.add_electrode`)
    over scaled soils. The result is a :class:`CharacterizeResultDTO`; the
    front end attaches it with ``SetCharacteristic``. Needs groundfield.

    Attributes
    ----------
    task : "characterize"
        Discriminator.
    electrode : {"ring", "foundation", "rod"}
        The electrode kind.
    dimensions : dict of str to float
        Overrides of its dimensions (``radius_m``, ``size_m``, ...).
    soil : {"homogeneous", "two_layer"}
        The soil model.
    rho_1 : float
        Resistivity of the (upper) layer in Ohm*m.
    rho_2, h_1 : float, optional
        Lower half-space resistivity and upper layer thickness of a
        two-layer soil.
    factors : list of float
        Scale factors applied to every layer, one row of the table each.
    frequencies : list of float
        Positive frequencies in Hz.
    rho_preset : str, optional
        Preset of the effective resistivity of a layered soil.
    backend : str
        groundfield backend; ``"image"`` (fast, impedance-grade surface
        potential) or ``"mom"`` (field-grade touch voltages).
    segment_length : float
        Segment length in m.
    touch_distance : float, optional
        Reach of ``k_touch`` in m; ``None`` for none.
    transfer_distances : list of float
        Distances in m from the electrode centre (along +x, on the surface)
        of the target points of ``k_transfer``; empty for none.
    target : {"bus", "bus_type"}
        What the result is for (returned unchanged).
    owner : str
        The bus or bus type (returned unchanged).
    name : str, optional
        Name of the characteristic.
    """

    task: Literal["characterize"] = "characterize"
    electrode: Literal["ring", "foundation", "rod"] = "ring"
    dimensions: Dict[str, float] = Field(default_factory=dict)
    soil: Literal["homogeneous", "two_layer"] = "homogeneous"
    rho_1: float = Field(default=100.0, gt=0, allow_inf_nan=False)
    rho_2: Optional[float] = Field(default=None, gt=0, allow_inf_nan=False)
    h_1: Optional[float] = Field(default=None, gt=0, allow_inf_nan=False)
    factors: List[float] = Field(default_factory=lambda: [0.5, 1.0, 2.0, 4.0], min_length=1)
    frequencies: List[float] = Field(default_factory=lambda: [50.0], min_length=1)
    rho_preset: Optional[str] = None
    backend: str = "image"
    segment_length: float = Field(default=0.5, gt=0, allow_inf_nan=False)
    touch_distance: Optional[float] = Field(default=1.0, gt=0, allow_inf_nan=False)
    transfer_distances: List[float] = Field(default_factory=list)
    target: Literal["bus", "bus_type"] = "bus"
    owner: str = ""
    name: Optional[str] = None


class CharacterizeResultDTO(BaseDTO):
    """
    An earthing characteristic computed by a job.

    Attributes
    ----------
    characteristic : dict
        The :class:`groundinsight.EarthingCharacteristic` in its JSON form,
        ready for ``SetCharacteristic``.
    table : TableDTO
        Its ``to_frame()``.
    target : {"bus", "bus_type"}
        From the request.
    owner : str
        From the request.
    revision : int, optional
        Session revision when the job was submitted.
    diagnostics : list of DiagnosticDTO
        Warnings of the computation (groundfield's included, by class name).
    groundinsight_version : str
        Library version.
    duration_s : float, optional
        Wall time.
    """

    characteristic: Dict[str, Any]
    table: TableDTO
    target: Literal["bus", "bus_type"] = "bus"
    owner: str = ""
    revision: Optional[int] = None
    diagnostics: List[DiagnosticDTO] = Field(default_factory=list)
    groundinsight_version: str
    duration_s: FiniteFloat = None


#: Any job request, discriminated by ``task``.
JobRequest = Annotated[
    Union[
        SolveRequestDTO,
        SolveAllRequestDTO,
        SweepRequestDTO,
        OutageStudyRequestDTO,
        MaxRhoRequestDTO,
        CutsRequestDTO,
        BusResponseRequestDTO,
        ThermalRequestDTO,
        TouchCheckRequestDTO,
        TransientRequestDTO,
        ReferenceCasesRequestDTO,
        CharacterizeRequestDTO,
    ],
    Field(discriminator="task"),
]

_JOB_REQUEST_ADAPTER = TypeAdapter(JobRequest)


def parse_job_request(data: Any) -> BaseModel:
    """
    Build a job request from its JSON form.

    Parameters
    ----------
    data : dict or BaseModel
        A mapping with a ``task`` key (``"solve"``, ``"solve_all"``,
        ``"sweep"``, ``"outage"``, ``"max_rho"``, ``"cuts"``,
        ``"bus_response"``, ``"thermal"``, ``"touch"``, ``"transient"``,
        ``"reference_cases"``, ``"characterize"``), or a request DTO, which
        is returned unchanged.

    Returns
    -------
    BaseModel
        The request DTO.

    Raises
    ------
    ValueError
        If ``task`` is unknown or a field is invalid
        (``pydantic.ValidationError`` is a ``ValueError``).

    Examples
    --------
    >>> parse_job_request({"task": "solve", "fault": "F1"}).phase_current_mode
    'auto'
    """
    if isinstance(data, BaseModel) and getattr(data, "task", None) is not None:
        return data
    return _JOB_REQUEST_ADAPTER.validate_python(data)


def json_schemas() -> Dict[str, dict]:
    """
    The JSON schema of every DTO, of the commands and of the job requests.

    These are the request and response schemas of a future REST API; a test
    checks that every DTO round-trips through JSON against them.

    Returns
    -------
    dict of str to dict
        DTO class name to its JSON schema, plus ``"Command"`` (the union of
        every edit command, discriminated by ``op``) and ``"JobRequest"``
        (the union of the job requests, discriminated by ``task``).

    Examples
    --------
    >>> schemas = json_schemas()
    >>> "dto_schema_version" in schemas["FaultResultDTO"]["properties"]
    True
    """
    from .commands import COMMAND_ADAPTER  # local: commands builds on this module

    schemas: Dict[str, dict] = {}
    for name in __all__:
        cls = globals()[name]
        if isinstance(cls, type) and issubclass(cls, BaseDTO):
            schemas[name] = cls.model_json_schema()
    schemas["Command"] = COMMAND_ADAPTER.json_schema()
    schemas["JobRequest"] = _JOB_REQUEST_ADAPTER.json_schema()
    return schemas

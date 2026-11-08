# io/tables.py

"""
Networks as tables: build one from spreadsheet-style tables, export one.

The people who use groundinsight keep their networks in spreadsheets and in
exports of network-planning tools; one ``create_*`` call per element is no
way to bring such a network in. This module maps a
network onto ten tables with a pinned schema each -- :data:`TABLE_SCHEMAS`,
the same schema a GUI table editor works on -- and back:

* :func:`network_from_tables` builds a :class:`Network` from Polars frames,
  CSV files or a directory of CSV files;
* :func:`network_to_tables` (also :meth:`Network.to_tables`) exports one.

What the tables hold
--------------------
Only *input*: the network's name, description and frequencies, the bus and
branch types with their formulas, the buses and branches with their
parameters, the sources and faults, and -- in long format, one row per
frequency -- the source values and the fault scalings. Everything derived
from that is recomputed on the way in by the same code path as
``create_bus``/``create_branch`` (``Network.add_*``): the impedance
dictionaries and lumped RLC values from the type formulas, and nothing else.
Results and enumerated paths are not part of the tables. A network whose
impedances follow from its types therefore survives ``network -> tables ->
network`` unchanged (``model_dump_json`` without the results); one whose
impedance dictionaries were overwritten by hand does not, because the tables
carry the formula, not the overwritten values.

Formulas read from a table pass the same formula guard as every other formula:
they become a :class:`BusType`/:class:`BranchType` through the model's own
validators, so a table cannot smuggle in what a constructor call could not.

Errors
------
Malformed tables raise *one* :class:`ValueError` that lists every problem
found, each with its table, row (counted from 1, header excluded) and
column: missing tables or columns, unknown columns, cells that are not a
number or a boolean, duplicate names, references to buses, types, sources or
faults that do not exist, and every model validation error. A half-built
network never escapes. Problems of a *well-formed* network -- a source that
reaches no fault, partial scalings -- are what :func:`groundinsight.check_network`
reports afterwards.

Excel
-----
Polars reads ``.xlsx`` through an optional engine (``fastexcel``) that
groundinsight does not depend on. With the engine installed,
``pl.read_excel(path, sheet_id=0)`` returns a dict of sheet name to frame --
exactly the shape :func:`network_from_tables` takes, one sheet per table.
"""

import logging
import os
import typing
from pathlib import Path as _FilePath
from typing import Any, Dict, List, Mapping, Optional, Tuple, Union

import polars as pl
from pydantic import BaseModel, ValidationError

from ..models.core_models import Branch, BranchType, Bus, BusType, Fault, Network, Source

logger = logging.getLogger(__name__)

__all__ = [
    "REQUIRED_COLUMNS",
    "REQUIRED_TABLES",
    "TABLE_SCHEMAS",
    "network_from_tables",
    "network_to_tables",
]

TableSource = Union[pl.DataFrame, str, "os.PathLike[str]"]

_TEXT = pl.Utf8
_FLOAT = pl.Float64
_BOOL = pl.Boolean

TABLE_SCHEMAS: Dict[str, Dict[str, Any]] = {
    "network": {"name": _TEXT, "description": _TEXT, "active_fault": _TEXT},
    "frequencies": {"frequency_Hz": _FLOAT},
    "bus_types": {
        "name": _TEXT,
        "description": _TEXT,
        "system_type": _TEXT,
        "voltage_level": _FLOAT,
        "impedance_formula": _TEXT,
        "R_formula": _TEXT,
        "L_formula": _TEXT,
        "C_formula": _TEXT,
        "earthing_conductor_material": _TEXT,
        "earthing_conductor_cross_section_mm2": _FLOAT,
        "earthing_conductor_theta_initial_C": _FLOAT,
        "earthing_conductor_theta_final_C": _FLOAT,
        "earthing_conductor_current_split": _FLOAT,
        "electrode_material": _TEXT,
        "electrode_cross_section_mm2": _FLOAT,
        "electrode_theta_initial_C": _FLOAT,
        "electrode_theta_final_C": _FLOAT,
        "electrode_current_split": _FLOAT,
    },
    "branch_types": {
        "name": _TEXT,
        "description": _TEXT,
        "grounding_conductor": _BOOL,
        "self_impedance_formula": _TEXT,
        "mutual_impedance_formula": _TEXT,
        "phase_impedance_formula": _TEXT,
        "R_self_formula": _TEXT,
        "L_self_formula": _TEXT,
        "C_self_formula": _TEXT,
        "R_mutual_formula": _TEXT,
        "M_mutual_formula": _TEXT,
        "conductor_material": _TEXT,
        "cross_section_mm2": _FLOAT,
        "theta_initial_C": _FLOAT,
        "theta_final_C": _FLOAT,
    },
    "buses": {
        "name": _TEXT,
        "type": _TEXT,
        "specific_earth_resistance": _FLOAT,
        "active": _BOOL,
        "description": _TEXT,
    },
    "branches": {
        "name": _TEXT,
        "type": _TEXT,
        "from_bus": _TEXT,
        "to_bus": _TEXT,
        "length": _FLOAT,
        "specific_earth_resistance": _FLOAT,
        "parallel_coefficient": _FLOAT,
        "active": _BOOL,
        "description": _TEXT,
    },
    "sources": {
        "name": _TEXT,
        "bus": _TEXT,
        "source_type": _TEXT,
        "i_k_a": _FLOAT,
        "r_to_x": _FLOAT,
        "kappa": _FLOAT,
        "description": _TEXT,
    },
    "source_values": {
        "source": _TEXT,
        "quantity": _TEXT,
        "frequency_Hz": _FLOAT,
        "real": _FLOAT,
        "imag": _FLOAT,
    },
    "faults": {
        "name": _TEXT,
        "bus": _TEXT,
        "t_k_s": _FLOAT,
        "n_factor": _FLOAT,
        "description": _TEXT,
    },
    "fault_scalings": {"fault": _TEXT, "frequency_Hz": _FLOAT, "scaling": _FLOAT},
}
"""Column layout of every table, in output order, with its Polars dtype.

Pinned so that a table written by :func:`network_to_tables` -- or by hand, or
by a GUI -- has one documented shape: an empty table is an empty frame with
these columns, never a 0x0 frame. Element columns carry the names of the
model fields (``specific_earth_resistance``, ``length`` in km, ``t_k_s``);
frequency columns are ``frequency_Hz``. In ``source_values`` the column
``quantity`` is ``"current"`` for a current source and ``"voltage"`` or
``"impedance"`` for a Thevenin source; ``real``/``imag`` are the RMS phasor
(A, V or Ohm)."""

REQUIRED_COLUMNS: Dict[str, Tuple[str, ...]] = {
    "network": ("name",),
    "frequencies": ("frequency_Hz",),
    "bus_types": ("name", "system_type", "voltage_level", "impedance_formula"),
    "branch_types": (
        "name",
        "grounding_conductor",
        "self_impedance_formula",
        "mutual_impedance_formula",
    ),
    "buses": ("name", "type"),
    "branches": ("name", "type", "from_bus", "to_bus", "length"),
    "sources": ("name", "bus"),
    "source_values": ("source", "quantity", "frequency_Hz", "real"),
    "faults": ("name", "bus"),
    "fault_scalings": ("fault", "frequency_Hz", "scaling"),
}
"""Columns a table must have. A missing optional column, and an empty cell in
one, mean the model default -- or ``None`` where the model field accepts
``None`` (so ``parallel_coefficient`` round-trips as ``None``)."""

REQUIRED_TABLES: Tuple[str, ...] = ("frequencies", "bus_types", "buses")
"""Tables :func:`network_from_tables` cannot do without; the others default to
empty (and the network name to ``"network"``)."""

_QUANTITIES = {"current": ("current",), "voltage": ("voltage", "impedance")}
_TRUE = {"true", "1", "yes"}
_FALSE = {"false", "0", "no"}
_MAX_LISTED = 50


class _Problems:
    """Every problem found while reading the tables, with its location."""

    def __init__(self) -> None:
        self.lines: List[str] = []
        # Rows with a cell that could not be converted. They are reported
        # once, by the conversion, and then skipped: building them would only
        # add a second message about the value that never arrived.
        self.bad_rows: set = set()

    def add(
        self,
        table: str,
        message: str,
        row: Optional[int] = None,
        column: Optional[str] = None,
    ) -> None:
        """
        Record one problem.

        Parameters
        ----------
        table : str
            Table name.
        message : str
            What is wrong.
        row : int, optional
            1-based data row (header excluded).
        column : str, optional
            Column name.
        """
        where = table
        if row is not None:
            where += f", row {row}"
        if column is not None:
            where += f", column '{column}'"
        self.lines.append(f"{where}: {message}")

    def raise_if_any(self) -> None:
        """
        Raise one ``ValueError`` listing every problem, if there are any.

        Raises
        ------
        ValueError
            If at least one problem was recorded.
        """
        if not self.lines:
            return
        shown = self.lines[:_MAX_LISTED]
        more = len(self.lines) - len(shown)
        text = "\n".join(f"  - {line}" for line in shown)
        if more:
            text += f"\n  ... and {more} more"
        raise ValueError(
            f"The tables do not describe a valid network "
            f"({len(self.lines)} problem(s)):\n{text}"
        )


def _accepts_none(model: type, field: str) -> bool:
    """``True`` if ``model.field`` is annotated ``Optional[...]``."""
    annotation = model.model_fields[field].annotation
    return annotation is type(None) or type(None) in typing.get_args(annotation)


# --------------------------------------------------------------------------
# reading and coercing
# --------------------------------------------------------------------------


def _read(source: Any, table: str, problems: _Problems) -> Optional[pl.DataFrame]:
    """
    One table as a frame, from a frame or a CSV path.

    Parameters
    ----------
    source : polars.DataFrame or str or os.PathLike
        The table.
    table : str
        Its name, for messages.
    problems : _Problems
        Receives read errors.

    Returns
    -------
    polars.DataFrame or None
        ``None`` if the table could not be read. A CSV file is read with every
        column as text (UTF-8); :func:`_coerce` converts and reports per cell.
    """
    if isinstance(source, pl.DataFrame):
        return source
    if isinstance(source, (str, os.PathLike)):
        path = _FilePath(source)
        if path.suffix.lower() in (".xlsx", ".xlsm", ".xls"):
            problems.add(
                table,
                f"{path.name} is a spreadsheet; read it with "
                "pl.read_excel(path, sheet_id=0) (needs the optional "
                "'fastexcel' engine) and pass the resulting dict of frames.",
            )
            return None
        try:
            return pl.read_csv(path, infer_schema=False, encoding="utf8")
        except Exception as exc:  # noqa: BLE001 -- reported with the table name
            problems.add(table, f"cannot read {path}: {exc}")
            return None
    problems.add(
        table,
        f"expected a polars DataFrame or a CSV path, got {type(source).__name__}.",
    )
    return None


def _cast(
    series: pl.Series, dtype: Any, table: str, problems: _Problems
) -> pl.Series:
    """
    Convert one column to its pinned dtype, reporting every cell that fails.

    Parameters
    ----------
    series : polars.Series
        The column as read.
    dtype : polars.DataType
        Its pinned dtype.
    table : str
        Table name, for messages.
    problems : _Problems
        Receives one problem per cell that cannot be converted.

    Returns
    -------
    polars.Series
        The converted column; a cell that failed is ``null``. An empty string
        counts as an empty cell.
    """
    column = series.name
    if dtype == _TEXT:
        if series.dtype == pl.Utf8:
            return series
        return series.cast(pl.Utf8)
    values = series.to_list()
    out: List[Any] = []
    for index, value in enumerate(values, start=1):
        if value is None or (isinstance(value, str) and not value.strip()):
            out.append(None)
            continue
        if dtype == _BOOL:
            if isinstance(value, bool):
                out.append(value)
                continue
            text = str(value).strip().lower()
            if text in _TRUE:
                out.append(True)
            elif text in _FALSE:
                out.append(False)
            else:
                problems.add(
                    table,
                    f"{value!r} is not a boolean (true/false, 1/0, yes/no).",
                    index,
                    column,
                )
                problems.bad_rows.add((table, index))
                out.append(None)
            continue
        if isinstance(value, bool):
            problems.add(table, f"{value!r} is not a number.", index, column)
            problems.bad_rows.add((table, index))
            out.append(None)
            continue
        try:
            number = float(value.strip() if isinstance(value, str) else value)
        except (TypeError, ValueError):
            hint = " (use a decimal point)" if isinstance(value, str) and "," in value else ""
            problems.add(table, f"{value!r} is not a number{hint}.", index, column)
            problems.bad_rows.add((table, index))
            out.append(None)
            continue
        out.append(number)
    return pl.Series(column, out, dtype=dtype)


def _coerce(
    frame: pl.DataFrame, table: str, problems: _Problems, ignore_extra: bool
) -> Optional[pl.DataFrame]:
    """
    A table checked against its schema and converted to the pinned dtypes.

    Parameters
    ----------
    frame : polars.DataFrame
        The table as read.
    table : str
        Its name.
    problems : _Problems
        Receives missing, unknown and unconvertible columns and cells.
    ignore_extra : bool
        Accept columns that are not part of the schema (and drop them).

    Returns
    -------
    polars.DataFrame or None
        The table with every schema column (absent optional ones as nulls) in
        schema order, or ``None`` if a required column is missing.
    """
    schema = TABLE_SCHEMAS[table]
    unknown = [column for column in frame.columns if column not in schema]
    if unknown and not ignore_extra:
        problems.add(
            table,
            f"unknown column(s) {unknown}; the columns of '{table}' are "
            f"{list(schema)} (pass ignore_extra_columns=True to drop others).",
        )
    missing = [c for c in REQUIRED_COLUMNS[table] if c not in frame.columns]
    for column in missing:
        problems.add(table, f"required column '{column}' is missing.")
    if missing:
        return None
    columns = []
    for column, dtype in schema.items():
        if column in frame.columns:
            columns.append(_cast(frame[column], dtype, table, problems))
        else:
            columns.append(pl.Series(column, [None] * frame.height, dtype=dtype))
    return pl.DataFrame(columns)


def _rows(frame: Optional[pl.DataFrame]) -> List[Tuple[int, Dict[str, Any]]]:
    """``(1-based row, row dict)`` pairs of a coerced table (empty for ``None``)."""
    if frame is None:
        return []
    return list(enumerate(frame.iter_rows(named=True), start=1))


def _unique_names(
    frame: Optional[pl.DataFrame], table: str, key: str, problems: _Problems
) -> Dict[str, int]:
    """
    Map each name to its first row; report empty and duplicate names.

    Returns
    -------
    dict of str to int
        Name to 1-based row of its first occurrence. Empty names are left to
        :func:`_skip_row`.
    """
    first: Dict[str, int] = {}
    for row, record in _rows(frame):
        name = record[key]
        if name is None:
            continue  # reported by _skip_row
        if name in first:
            problems.add(
                table,
                f"{key} {name!r} appears in rows {first[name]} and {row}; "
                f"{key}s have to be unique.",
            )
            continue
        first[name] = row
    return first


def _skip_row(
    record: Dict[str, Any], table: str, row: int, problems: _Problems
) -> bool:
    """
    Whether a row cannot be built: a cell failed to convert or a required cell is empty.

    Parameters
    ----------
    record : dict
        The coerced row.
    table : str
        Table name.
    row : int
        1-based row.
    problems : _Problems
        Receives one problem per empty required cell; failed conversions are
        already in it.

    Returns
    -------
    bool
        ``True`` if the row must be skipped.
    """
    skip = (table, row) in problems.bad_rows
    for column in REQUIRED_COLUMNS[table]:
        if record.get(column) is None and (table, row) not in problems.bad_rows:
            problems.add(
                table, "the value is empty; this column is required.", row, column
            )
            skip = True
    return skip


def _model(
    model: type,
    record: Dict[str, Any],
    table: str,
    row: int,
    problems: _Problems,
    **fixed: Any,
) -> Optional[BaseModel]:
    """
    Build one model instance from a table row, reporting validation errors.

    Parameters
    ----------
    model : type
        The pydantic model class.
    record : dict
        The coerced row; keys that are fields of ``model`` are used. An empty
        cell becomes ``None`` where the field accepts it and is left to the
        model default otherwise.
    table : str
        Table name, for messages.
    row : int
        1-based row, for messages.
    problems : _Problems
        Receives the validation errors, each with its column where pydantic
        names one.
    **fixed
        Keyword arguments that override the row (resolved references, the
        frequency-dependent dictionaries).

    Returns
    -------
    pydantic.BaseModel or None
        The instance, or ``None`` if validation failed.
    """
    kwargs: Dict[str, Any] = {}
    for field in model.model_fields:
        if field in fixed or field not in record:
            continue
        value = record[field]
        if value is None and not _accepts_none(model, field):
            continue
        kwargs[field] = value
    kwargs.update(fixed)
    try:
        return model(**kwargs)
    except ValidationError as exc:
        for error in exc.errors():
            location = error.get("loc") or ()
            column = str(location[0]) if location and location[0] in TABLE_SCHEMAS[table] else None
            problems.add(table, error.get("msg", str(exc)), row, column)
    except (ValueError, TypeError) as exc:
        problems.add(table, str(exc), row)
    return None


# --------------------------------------------------------------------------
# public API
# --------------------------------------------------------------------------


def network_from_tables(
    tables: Optional[Union[Mapping[str, TableSource], str, "os.PathLike[str]"]] = None,
    *,
    name: Optional[str] = None,
    ignore_extra_columns: bool = False,
    **named_tables: TableSource,
) -> Network:
    """
    Build a network from tables.

    Parameters
    ----------
    tables : mapping or str or os.PathLike, optional
        Either a mapping of table name to table -- a Polars frame or the path
        of a CSV file, as returned by :func:`network_to_tables` or by
        ``pl.read_excel(path, sheet_id=0)`` -- or the path of a directory
        holding ``<table>.csv`` files. The table names and their columns are
        listed in :data:`TABLE_SCHEMAS`; ``frequencies``, ``bus_types`` and
        ``buses`` are required, the others optional.
    name : str, optional
        Name of the network. Overrides the ``network`` table; defaults to
        ``"network"`` without one.
    ignore_extra_columns : bool, optional
        Drop columns that are not part of the schema instead of reporting
        them. Defaults to ``False``: an unknown column is usually a typo
        (``lenght``), and silently ignoring it would silently drop the value.
    **named_tables
        Tables passed by name, e.g. ``buses=frame``. They override entries of
        ``tables``.

    Returns
    -------
    Network
        The network, with every impedance evaluated from its type formulas
        (exactly as ``create_bus``/``create_branch`` do) and the active fault
        of the ``network`` table activated. No results, no paths.

    Raises
    ------
    ValueError
        One error listing *every* problem found, each with table, row and
        column: missing tables or columns, unknown columns or tables, cells
        that are not a number or boolean, empty or duplicate names, references
        to buses, types, sources or faults that do not exist, a source value
        that does not fit the source type, and every model validation error --
        including a formula the formula guard rejects.

    Examples
    --------
    >>> import polars as pl
    >>> import groundinsight as gi
    >>> net = gi.network_from_tables(
    ...     name="demo",
    ...     frequencies=pl.DataFrame({"frequency_Hz": [50.0]}),
    ...     bus_types=pl.DataFrame({
    ...         "name": ["rod"], "system_type": ["MV"], "voltage_level": [20.0],
    ...         "impedance_formula": ["0.01*rho"]}),
    ...     buses=pl.DataFrame({"name": ["B1", "B2"], "type": ["rod", "rod"],
    ...                         "specific_earth_resistance": [100.0, 400.0]}),
    ... )
    >>> net.buses["B2"].impedance[50.0]
    ComplexNumber(real=4.0, imag=0.0)
    """
    problems = _Problems()
    sources: Dict[str, Any] = {}
    if tables is None:
        pass
    elif isinstance(tables, Mapping):
        sources.update(tables)
    elif isinstance(tables, (str, os.PathLike)) and _FilePath(tables).is_dir():
        for table in TABLE_SCHEMAS:
            path = _FilePath(tables) / f"{table}.csv"
            if path.exists():
                sources[table] = path
    else:
        problems.add(
            "tables",
            f"expected a mapping of table name to table or a directory of CSV "
            f"files, got {tables!r}.",
        )
    sources.update(named_tables)

    for table in sources:
        if table not in TABLE_SCHEMAS:
            problems.add(
                table,
                f"unknown table; the tables are {list(TABLE_SCHEMAS)}.",
            )
    for table in REQUIRED_TABLES:
        if table not in sources:
            problems.add(table, "required table is missing.")

    frames: Dict[str, Optional[pl.DataFrame]] = {}
    for table in TABLE_SCHEMAS:
        if table not in sources:
            frames[table] = None
            continue
        frame = _read(sources[table], table, problems)
        frames[table] = (
            None if frame is None else _coerce(frame, table, problems, ignore_extra_columns)
        )

    network = _build(frames, name, problems)
    problems.raise_if_any()
    return network


def _build(
    frames: Dict[str, Optional[pl.DataFrame]],
    name: Optional[str],
    problems: _Problems,
) -> Optional[Network]:
    """
    Turn the coerced tables into a network, collecting every problem.

    Parameters
    ----------
    frames : dict of str to polars.DataFrame or None
        The coerced tables (``None`` for absent or unreadable ones).
    name : str or None
        The ``name=`` argument of :func:`network_from_tables`.
    problems : _Problems
        Receives every problem; the caller raises once at the end.

    Returns
    -------
    Network or None
        The network, or ``None`` if it could not even be created (no valid
        frequency list). It is only handed out when ``problems`` stays empty.
    """
    # --- network row and frequencies ---------------------------------------
    meta: Dict[str, Any] = {"name": None, "description": None, "active_fault": None}
    meta_rows = _rows(frames["network"])
    if frames["network"] is not None and len(meta_rows) != 1:
        problems.add("network", f"needs exactly one row, has {len(meta_rows)}.")
    if meta_rows and not _skip_row(meta_rows[0][1], "network", 1, problems):
        meta.update(meta_rows[0][1])
    network_name = name if name is not None else (meta["name"] or "network")

    frequencies: List[float] = []
    for row, record in _rows(frames["frequencies"]):
        if _skip_row(record, "frequencies", row, problems):
            continue
        frequencies.append(record["frequency_Hz"])
    network: Optional[Network] = None
    try:
        network = Network(
            name=network_name, description=meta["description"], frequencies=frequencies
        )
    except ValidationError as exc:
        for error in exc.errors():
            problems.add("frequencies", error.get("msg", str(exc)))
    except ValueError as exc:
        problems.add("frequencies", str(exc))

    # --- types ---------------------------------------------------------------
    # One model instance per type row; every element of a type shares it, as
    # elements created with create_bus(type=...) share the object passed in.
    bus_type_names = _unique_names(frames["bus_types"], "bus_types", "name", problems)
    bus_types: Dict[str, BusType] = {}
    for row, record in _rows(frames["bus_types"]):
        if _skip_row(record, "bus_types", row, problems):
            continue
        if row != bus_type_names.get(record["name"]):
            continue
        instance = _model(BusType, record, "bus_types", row, problems)
        if instance is not None:
            bus_types[record["name"]] = instance
    branch_type_names = _unique_names(
        frames["branch_types"], "branch_types", "name", problems
    )
    branch_types: Dict[str, BranchType] = {}
    for row, record in _rows(frames["branch_types"]):
        if _skip_row(record, "branch_types", row, problems):
            continue
        if row != branch_type_names.get(record["name"]):
            continue
        instance = _model(BranchType, record, "branch_types", row, problems)
        if instance is not None:
            branch_types[record["name"]] = instance

    # --- buses -----------------------------------------------------------------
    bus_names = _unique_names(frames["buses"], "buses", "name", problems)
    seen: set = set()
    for row, record in _rows(frames["buses"]):
        bus_name = record["name"]
        if _skip_row(record, "buses", row, problems) or bus_name in seen:
            continue
        seen.add(bus_name)
        type_name = record["type"]
        if type_name not in bus_type_names:
            problems.add(
                "buses",
                f"bus type {type_name!r} is not in bus_types (bus {bus_name!r}).",
                row,
                "type",
            )
            continue
        if type_name not in bus_types:
            continue  # the type itself is reported
        bus = _model(Bus, record, "buses", row, problems,
                     type=bus_types[type_name], impedance={})
        if bus is not None and network is not None:
            try:
                network.add_bus(bus)
            except ValueError as exc:
                problems.add("buses", str(exc), row)

    # --- branches ---------------------------------------------------------------
    _unique_names(frames["branches"], "branches", "name", problems)
    seen = set()
    for row, record in _rows(frames["branches"]):
        branch_name = record["name"]
        if _skip_row(record, "branches", row, problems) or branch_name in seen:
            continue
        seen.add(branch_name)
        ok = True
        if record["type"] not in branch_type_names:
            problems.add(
                "branches",
                f"branch type {record['type']!r} is not in branch_types "
                f"(branch {branch_name!r}).",
                row,
                "type",
            )
            ok = False
        for end in ("from_bus", "to_bus"):
            if record[end] not in bus_names:
                problems.add(
                    "branches",
                    f"bus {record[end]!r} is not in buses (branch {branch_name!r}).",
                    row,
                    end,
                )
                ok = False
        if not ok or record["type"] not in branch_types:
            continue
        branch = _model(
            Branch, record, "branches", row, problems,
            type=branch_types[record["type"]], self_impedance={}, mutual_impedance={},
        )
        if branch is not None and network is not None:
            if record["from_bus"] not in network.buses or record["to_bus"] not in network.buses:
                continue  # an end bus failed and is reported
            try:
                network.add_branch(branch)
            except ValueError as exc:
                problems.add("branches", str(exc), row)

    # --- sources and their values ----------------------------------------------
    source_rows = _unique_names(frames["sources"], "sources", "name", problems)
    source_types: Dict[str, str] = {}
    for _row, record in _rows(frames["sources"]):
        if record["name"] is not None:
            source_types.setdefault(record["name"], record["source_type"] or "current")
    collected: Dict[str, Dict[str, Dict[float, complex]]] = {}
    for row, record in _rows(frames["source_values"]):
        if _skip_row(record, "source_values", row, problems):
            continue
        owner = record["source"]
        quantity = record["quantity"]
        frequency = record["frequency_Hz"]
        if owner not in source_rows:
            problems.add("source_values", f"source {owner!r} is not in sources.", row, "source")
            continue
        allowed = _QUANTITIES.get(source_types.get(owner, ""), ())
        if quantity not in allowed:
            problems.add(
                "source_values",
                f"quantity {quantity!r} does not belong to the "
                f"{source_types.get(owner)} source {owner!r}; a current source takes "
                "quantity 'current', a voltage source 'voltage' and 'impedance'.",
                row,
                "quantity",
            )
            continue
        bucket = collected.setdefault(owner, {}).setdefault(quantity, {})
        if frequency in bucket:
            problems.add(
                "source_values",
                f"source {owner!r} has two {quantity} values at {frequency:g} Hz.",
                row,
                "frequency_Hz",
            )
            continue
        bucket[frequency] = complex(record["real"], record["imag"] or 0.0)
    seen = set()
    for row, record in _rows(frames["sources"]):
        source_name = record["name"]
        if _skip_row(record, "sources", row, problems) or source_name in seen:
            continue
        seen.add(source_name)
        if record["bus"] not in bus_names:
            problems.add(
                "sources",
                f"bus {record['bus']!r} is not in buses (source {source_name!r}).",
                row,
                "bus",
            )
            continue
        values = collected.get(source_name, {})
        source = _model(
            Source, record, "sources", row, problems,
            values=values.get("current"),
            voltage=values.get("voltage"),
            source_impedance=values.get("impedance"),
        )
        if source is not None and network is not None and record["bus"] in network.buses:
            try:
                network.add_source(source)
            except ValueError as exc:
                problems.add("sources", str(exc), row)

    # --- faults and their scalings ----------------------------------------------
    fault_rows = _unique_names(frames["faults"], "faults", "name", problems)
    scalings: Dict[str, Dict[float, float]] = {}
    for row, record in _rows(frames["fault_scalings"]):
        if _skip_row(record, "fault_scalings", row, problems):
            continue
        owner = record["fault"]
        if owner not in fault_rows:
            problems.add("fault_scalings", f"fault {owner!r} is not in faults.", row, "fault")
            continue
        bucket = scalings.setdefault(owner, {})
        if record["frequency_Hz"] in bucket:
            problems.add(
                "fault_scalings",
                f"fault {owner!r} has two scalings at {record['frequency_Hz']:g} Hz.",
                row,
                "frequency_Hz",
            )
            continue
        bucket[record["frequency_Hz"]] = record["scaling"]
    seen = set()
    for row, record in _rows(frames["faults"]):
        fault_name = record["name"]
        if _skip_row(record, "faults", row, problems) or fault_name in seen:
            continue
        seen.add(fault_name)
        if record["bus"] not in bus_names:
            problems.add(
                "faults",
                f"bus {record['bus']!r} is not in buses (fault {fault_name!r}).",
                row,
                "bus",
            )
            continue
        fault = _model(
            Fault, record, "faults", row, problems,
            scalings=scalings.get(fault_name, {}),
        )
        if fault is not None and network is not None and record["bus"] in network.buses:
            try:
                network.add_fault(fault)
            except ValueError as exc:
                problems.add("faults", str(exc), row)

    # --- the active fault ---------------------------------------------------------
    active = meta["active_fault"]
    if active is not None:
        if active not in fault_rows:
            problems.add(
                "network",
                f"active fault {active!r} is not in faults.",
                1,
                "active_fault",
            )
        elif network is not None and active in network.faults:
            network.set_active_fault(active, keep_results=True)
    return network


def _type_rows(
    elements: Mapping[str, Any], kind: str, columns: Mapping[str, Any]
) -> List[Dict[str, Any]]:
    """
    One row per distinct type of ``elements``, in order of first use.

    Parameters
    ----------
    elements : mapping of str to Bus or Branch
        The elements whose ``type`` is collected.
    kind : str
        ``"bus type"`` or ``"branch type"``, for the error message.
    columns : mapping
        The schema of the type table.

    Returns
    -------
    list of dict
        The type rows.

    Raises
    ------
    ValueError
        If two different type definitions share one name: a table row is
        keyed by name, and one of them would be lost.
    """
    rows: Dict[str, Dict[str, Any]] = {}
    for element in elements.values():
        dumped = element.type.model_dump()
        row = {column: dumped.get(column) for column in columns}
        existing = rows.get(row["name"])
        if existing is None:
            rows[row["name"]] = row
        elif existing != row:
            raise ValueError(
                f"Two different {kind}s are both called {row['name']!r}; a type "
                "table row is keyed by name, so one definition would be lost. "
                "Give them distinct names."
            )
    return list(rows.values())


def network_to_tables(network: Network) -> Dict[str, pl.DataFrame]:
    """
    Export a network as the tables :func:`network_from_tables` reads.

    Parameters
    ----------
    network : Network
        The network to export.

    Returns
    -------
    dict of str to polars.DataFrame
        One frame per table of :data:`TABLE_SCHEMAS`, in that order and with
        that schema; a table without rows is an empty frame with the schema's
        columns. Write them with ``frame.write_csv(...)`` or a spreadsheet
        writer of your choice.

    Raises
    ------
    ValueError
        If two different bus types, or two different branch types, share a
        name.

    Notes
    -----
    Only input data is exported -- see the module docstring. The impedance
    dictionaries are left out because they are evaluated from the type
    formulas on the way back in; results and enumerated paths are left out
    because they are derived from everything else.

    Examples
    --------
    >>> import groundinsight as gi
    >>> net = gi.create_network(name="demo", frequencies=[50.0, 250.0])
    >>> tables = gi.network_from_tables(net.to_tables(), name="copy").to_tables()
    >>> tables["frequencies"]["frequency_Hz"].to_list()
    [50.0, 250.0]
    """
    rows: Dict[str, List[Dict[str, Any]]] = {table: [] for table in TABLE_SCHEMAS}
    rows["network"].append(
        {
            "name": network.name,
            "description": network.description,
            "active_fault": network.active_fault,
        }
    )
    rows["frequencies"] = [{"frequency_Hz": float(f)} for f in network.frequencies]
    rows["bus_types"] = _type_rows(network.buses, "bus type", TABLE_SCHEMAS["bus_types"])
    rows["branch_types"] = _type_rows(
        network.branches, "branch type", TABLE_SCHEMAS["branch_types"]
    )
    for name, bus in network.buses.items():
        rows["buses"].append(
            {
                "name": name,
                "type": bus.type.name,
                "specific_earth_resistance": bus.specific_earth_resistance,
                "active": bus.active,
                "description": bus.description,
            }
        )
    for name, branch in network.branches.items():
        rows["branches"].append(
            {
                "name": name,
                "type": branch.type.name,
                "from_bus": branch.from_bus,
                "to_bus": branch.to_bus,
                "length": branch.length,
                "specific_earth_resistance": branch.specific_earth_resistance,
                "parallel_coefficient": branch.parallel_coefficient,
                "active": branch.active,
                "description": branch.description,
            }
        )
    for name, source in network.sources.items():
        rows["sources"].append(
            {
                "name": name,
                "bus": source.bus,
                "source_type": source.source_type,
                "i_k_a": source.i_k_a,
                "r_to_x": source.r_to_x,
                "kappa": source.kappa,
                "description": source.description,
            }
        )
        for quantity, mapping in (
            ("current", source.values),
            ("voltage", source.voltage),
            ("impedance", source.source_impedance),
        ):
            for frequency, value in (mapping or {}).items():
                rows["source_values"].append(
                    {
                        "source": name,
                        "quantity": quantity,
                        "frequency_Hz": float(frequency),
                        "real": float(value.real),
                        "imag": float(value.imag),
                    }
                )
    for name, fault in network.faults.items():
        rows["faults"].append(
            {
                "name": name,
                "bus": fault.bus,
                "t_k_s": fault.t_k_s,
                "n_factor": fault.n_factor,
                "description": fault.description,
            }
        )
        for frequency, scaling in fault.scalings.items():
            rows["fault_scalings"].append(
                {"fault": name, "frequency_Hz": float(frequency), "scaling": float(scaling)}
            )
    return {
        table: pl.DataFrame(rows[table], schema=schema)
        if rows[table]
        else pl.DataFrame(schema=schema)
        for table, schema in TABLE_SCHEMAS.items()
    }

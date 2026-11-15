# api/views.py
"""
Builders that turn library objects into DTOs -- selection and conversion only.

Every number a front end shows passes through one of these functions, so they
follow one rule: **no physics here**. They pick values out of the library's
own public accessors -- :meth:`Network.to_tables
<groundinsight.Network.to_tables>`, :meth:`Network.res_fault
<groundinsight.Network.res_fault>`, ``res_buses``, ``res_branches``,
:attr:`Result.diagnostics <groundinsight.Result.diagnostics>`,
:func:`groundinsight.check_network` -- and convert them (a Polars frame into
columns and rows, a non-finite number into ``None``). Nothing is recomputed,
averaged or combined: the key figures *are* the rows of ``res_fault``, which
the library documents and tests against an independent solver. A selection
slip here would show up in the service-layer tests, which compare every DTO
with those frames and with a second, independent route.

The builders are module-level functions (not session methods) because a job
running in a separate process builds its DTOs there, from its own copy of the
network, and only the DTO travels back.
"""

import logging
from typing import Dict, Iterable, List, Optional, Sequence

import polars as pl

import groundinsight as gi
from groundinsight.io import TABLE_SCHEMAS

from .schemas import (
    CharacteristicDTO,
    CheckReportDTO,
    ColumnDTO,
    DiagnosticDTO,
    DiagramDTO,
    DiagramEdgeDTO,
    DiagramMarkerDTO,
    DiagramNodeDTO,
    ElementRefDTO,
    FaultResultDTO,
    KeyFigureRowDTO,
    KeyFiguresDTO,
    NetworkTablesDTO,
    TableDTO,
)

logger = logging.getLogger(__name__)

__all__ = [
    "COLUMN_UNITS",
    "characteristic_dto",
    "check_report_dto",
    "diagnostic_dto",
    "diagram_dto",
    "element_tables_dto",
    "fault_result_dto",
    "key_figures_dto",
    "table_dto",
]

#: Units of the element-table columns whose name does not carry one. The
#: result columns carry theirs in the name (``EPR_V``, ``I_branch_A``).
COLUMN_UNITS: Dict[str, str] = {
    "length": "km",
    "specific_earth_resistance": "Ohm*m",
    "voltage_level": "kV",
    "i_k_a": "A",
}


def diagnostic_dto(record: gi.Diagnostic) -> DiagnosticDTO:
    """
    Convert one library diagnostic.

    Parameters
    ----------
    record : groundinsight.Diagnostic
        A record of ``Result.diagnostics``, of :func:`groundinsight.check_network`
        or of :func:`groundinsight.diagnostics.collecting`.

    Returns
    -------
    DiagnosticDTO
        Code, severity, category, message and element references, unchanged.
    """
    return DiagnosticDTO(
        code=record.code,
        severity=record.severity,
        category=record.category,
        message=record.message,
        elements=[ElementRefDTO(kind=kind, name=name) for kind, name in record.elements],
    )


def _dtype(dtype: pl.DataType) -> str:
    """
    The DTO name of a Polars dtype.

    Parameters
    ----------
    dtype : polars.DataType
        A column dtype.

    Returns
    -------
    str
        ``"bool"``, ``"float"``, ``"int"``, ``"null"`` or, for every other
        dtype (text and anything the wire format renders as text), ``"str"``.
    """
    if dtype == pl.Boolean:
        return "bool"
    if dtype.is_float():
        return "float"
    if dtype.is_integer():
        return "int"
    if dtype == pl.Null:
        return "null"
    return "str"


def table_dto(name: str, frame: pl.DataFrame) -> TableDTO:
    """
    Convert a Polars frame into a table DTO, column for column.

    Parameters
    ----------
    name : str
        Identifier of the table.
    frame : polars.DataFrame
        The frame; its column names and order are kept.

    Returns
    -------
    TableDTO
        One column per frame column (with :data:`COLUMN_UNITS` where they
        apply) and one row dictionary per frame row; non-finite numbers
        become ``None``.
    """
    columns = [
        ColumnDTO(name=column, dtype=_dtype(dtype), unit=COLUMN_UNITS.get(column))
        for column, dtype in frame.schema.items()
    ]
    return TableDTO(name=name, columns=columns, rows=frame.to_dicts())


def _type_row(element_type, columns: Sequence[str]) -> Dict[str, object]:
    """
    The table row of one bus or branch type, as ``Network.to_tables`` writes it.

    Parameters
    ----------
    element_type : BusType or BranchType
        The type.
    columns : sequence of str
        The columns of the type table.

    Returns
    -------
    dict
        The type's fields restricted to ``columns``.
    """
    dumped = element_type.model_dump()
    return {column: dumped.get(column) for column in columns}


def element_tables_dto(
    network: gi.Network,
    *,
    revision: int,
    extra_bus_types: Iterable = (),
    extra_branch_types: Iterable = (),
) -> NetworkTablesDTO:
    """
    The input of a network as the ten tables of ``Network.to_tables``.

    Parameters
    ----------
    network : Network
        The network.
    revision : int
        Session revision the tables describe.
    extra_bus_types, extra_branch_types : iterable of BusType / BranchType
        Catalogue types that no element uses yet; appended to the type tables
        with the same columns (``to_tables`` lists the types in use only).

    Returns
    -------
    NetworkTablesDTO
        The tables in the order and with the schema of
        :data:`groundinsight.io.TABLE_SCHEMAS`.

    Raises
    ------
    ValueError
        As :meth:`Network.to_tables <groundinsight.Network.to_tables>`: two
        different types share a name.
    """
    frames = network.to_tables()
    tables: Dict[str, TableDTO] = {}
    for name in TABLE_SCHEMAS:
        frame = frames[name]
        extra = {"bus_types": extra_bus_types, "branch_types": extra_branch_types}.get(name)
        if extra:
            rows = [_type_row(t, TABLE_SCHEMAS[name]) for t in extra]
            if rows:
                frame = pl.concat(
                    [frame, pl.DataFrame(rows, schema=TABLE_SCHEMAS[name])], how="vertical"
                )
        tables[name] = table_dto(name, frame)
    return NetworkTablesDTO(revision=revision, tables=tables)


def key_figures_dto(network: gi.Network, fault: str) -> KeyFiguresDTO:
    """
    The key figures of a stored result: the rows of ``Network.res_fault``.

    Parameters
    ----------
    network : Network
        A network holding a result for ``fault``.
    fault : str
        The fault.

    Returns
    -------
    KeyFiguresDTO
        One :class:`KeyFigureRowDTO` per row of the frame, field for column.

    Raises
    ------
    ValueError
        If ``fault`` is not a fault of the network.
    RuntimeError
        If the network holds no result for ``fault``.
    """
    frame = network.res_fault(fault)
    rows = [KeyFigureRowDTO(**row) for row in frame.to_dicts()]
    return KeyFiguresDTO(fault=fault, fault_bus=network.faults[fault].bus, rows=rows)


def fault_result_dto(
    network: gi.Network,
    fault: str,
    *,
    revision: Optional[int],
    phase_current_mode: Optional[str],
    stale: bool,
    duration_s: Optional[float] = None,
) -> FaultResultDTO:
    """
    Everything a front end shows of one stored result.

    Parameters
    ----------
    network : Network
        A network holding a result for ``fault`` (``network.results[fault]``).
    fault : str
        The fault.
    revision : int, optional
        Session revision of the inputs the result was computed from.
    phase_current_mode : str, optional
        The mode it was solved with, if known.
    stale : bool
        Whether the network has changed since (from the library).
    duration_s : float, optional
        Wall time of the solve.

    Returns
    -------
    FaultResultDTO
        Key figures from ``res_fault``, bus and branch tables from
        ``res_buses``/``res_branches``, diagnostics and input digest from the
        stored :class:`~groundinsight.Result`.
    """
    result = network.results[fault]
    return FaultResultDTO(
        fault=fault,
        fault_bus=network.faults[fault].bus,
        revision=revision,
        input_digest=result.input_digest,
        phase_current_mode=phase_current_mode,
        stale=stale,
        key_figures=key_figures_dto(network, fault),
        buses=table_dto("bus_results", network.res_buses(fault=fault)),
        branches=table_dto("branch_results", network.res_branches(fault=fault)),
        diagnostics=[diagnostic_dto(d) for d in result.diagnostics],
        groundinsight_version=gi.__version__,
        duration_s=duration_s,
    )


def check_report_dto(
    records: List[gi.Diagnostic], *, revision: int, fault: Optional[str] = None
) -> CheckReportDTO:
    """
    The pre-flight check as a report.

    Parameters
    ----------
    records : list of groundinsight.Diagnostic
        The output of :func:`groundinsight.check_network`.
    revision : int
        Session revision that was checked.
    fault : str, optional
        The fault the check was restricted to.

    Returns
    -------
    CheckReportDTO
        The records, unchanged and in order, with counts by severity.
    """
    diagnostics = [diagnostic_dto(d) for d in records]
    n_errors = sum(d.severity == "error" for d in diagnostics)
    return CheckReportDTO(
        revision=revision,
        fault=fault,
        ok=n_errors == 0,
        n_errors=n_errors,
        n_warnings=sum(d.severity == "warning" for d in diagnostics),
        diagnostics=diagnostics,
    )


def _characteristic_origin(bus: gi.Bus) -> Optional[str]:
    """
    Where the characteristic of a bus hangs.

    Parameters
    ----------
    bus : Bus
        The bus.

    Returns
    -------
    str or None
        ``"bus"`` for its own, ``"bus_type"`` for its type's, ``None``
        without one.
    """
    if bus.characteristic is not None:
        return "bus"
    return "bus_type" if bus.type.characteristic is not None else None


def diagram_dto(network: gi.Network, diagram: gi.Diagram, *, revision: int) -> DiagramDTO:
    """
    The diagram of a network, every element listed.

    Parameters
    ----------
    network : Network
        The network.
    diagram : Diagram
        Its stored diagram.
    revision : int
        Session revision the diagram describes.

    Returns
    -------
    DiagramDTO
        One node per bus and one edge per branch in network order; elements
        without an entry at the position of
        :meth:`Diagram.with_layout <groundinsight.Diagram.with_layout>` with
        ``only_unplaced=True`` and ``placed`` false. The stored diagram is
        not changed.
    """
    shown = diagram.with_layout(network, only_unplaced=True)
    fed = {source.bus for source in network.sources.values()}
    faulted = {fault.bus for fault in network.faults.values()}
    nodes = [
        DiagramNodeDTO(
            name=name, x=node.x, y=node.y, symbol=node.symbol, label_offset=node.label_offset,
            length=node.length, placed=name in diagram.nodes, bus_type=bus.type.name,
            voltage_level=bus.type.voltage_level, active=bus.active, has_source=name in fed,
            has_fault=name in faulted, characteristic=_characteristic_origin(bus),
        )
        for name, bus in network.buses.items()
        for node in (shown.nodes[name],)
    ]
    edges = [
        DiagramEdgeDTO(
            name=name, from_bus=branch.from_bus, to_bus=branch.to_bus,
            waypoints=shown.edges[name].waypoints, label_offset=shown.edges[name].label_offset,
            placed=name in diagram.edges, branch_type=branch.type.name, active=branch.active,
            grounding_conductor=branch.type.grounding_conductor,
        )
        for name, branch in network.branches.items()
    ]
    markers = [
        DiagramMarkerDTO(kind=kind, name=name, bus=element.bus,
                         offset=entries[name].offset if name in entries else (0.0, 0.0))
        for kind, collection, entries in (("fault", network.faults, diagram.faults),
                                          ("source", network.sources, diagram.sources))
        for name, element in collection.items()
    ]
    return DiagramDTO(
        revision=revision, nodes=nodes, edges=edges, markers=markers,
        color_scheme=diagram.color_scheme, grid_size=diagram.grid_size,
        diagnostics=[diagnostic_dto(d) for d in diagram.check(network)],
    )


def characteristic_dto(
    network: gi.Network,
    kind: str,
    name: str,
    bus_types: Dict[str, gi.BusType],
) -> Optional[CharacteristicDTO]:
    """
    The earthing characteristic of a bus or a catalogue bus type.

    Parameters
    ----------
    network : Network
        The network.
    kind : {"bus", "bus_type"}
        What ``name`` is.
    name : str
        The bus or bus type.
    bus_types : dict of str to BusType
        The session's bus type catalogue.

    Returns
    -------
    CharacteristicDTO or None
        For a bus the characteristic it uses (its own, else its type's),
        with ``extrapolated`` from :meth:`EarthingCharacteristic.is_extrapolated
        <groundinsight.EarthingCharacteristic.is_extrapolated>` at its ``rho``
        and the network frequencies; ``None`` when it has none.

    Raises
    ------
    ValueError
        If ``kind`` is unknown or ``name`` is no bus / catalogue type.
    """
    extrapolated = None
    if kind == "bus":
        bus = network.buses.get(name)
        if bus is None:
            raise ValueError(f"bus '{name}' does not exist in network '{network.name}'")
        origin = "bus" if bus.characteristic is not None else "bus_type"
        characteristic = bus.resolved_characteristic()
        if characteristic is not None:
            extrapolated = characteristic.is_extrapolated(
                bus.specific_earth_resistance, network.frequencies)
    elif kind == "bus_type":
        if name not in bus_types:
            raise ValueError(f"bus type '{name}' is not in the catalogue")
        origin = "bus_type"
        characteristic = bus_types[name].characteristic
    else:
        raise ValueError(f"unknown owner kind {kind!r}; expected 'bus' or 'bus_type'")
    if characteristic is None:
        return None
    provenance = characteristic.provenance
    return CharacteristicDTO(
        owner_kind=kind, owner=name, origin=origin, name=characteristic.name,
        impedance_source=characteristic.impedance_source,
        impedance_formula=characteristic.impedance_formula,
        rho_Ohm_m=list(characteristic.rho_Ohm_m), frequency_Hz=list(characteristic.frequency_Hz),
        table=table_dto("characteristic", characteristic.to_frame()),
        extrapolated=extrapolated,
        provenance=provenance.model_dump(mode="json") if provenance is not None else {},
    )

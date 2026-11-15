# diagram.py

"""
The network diagram: where each element is drawn, kept apart from the network.

A one-line diagram places every bus at a point, routes every branch along
waypoints and attaches the symbols of faults and sources to their buses. None
of that changes a number: the solve reads impedances and topology, never a
coordinate. The :class:`Diagram` is therefore a model of its own, next to the
:class:`~groundinsight.Network` in the project file rather than inside it:

* the network stays the physical model, and its input digest -- which tells
  whether a stored result is current -- does not change when a bus is moved;
* a network built in a script, imported from pandapower or read from tables
  has no coordinates, and is complete without them; the diagram is filled in
  when it is first drawn;
* the diagram refers to elements by name, as faults and sources refer to
  their buses. Renaming an element renames it in both, in one step
  (:meth:`Diagram.renamed`); :meth:`Diagram.check` reports entries whose
  element is gone and elements that have no entry yet.

Coordinates are those of the drawing area at zoom 1 (screen pixels), with
``y`` growing downwards as on screen; ``grid_size`` is the snapping raster.
"""

import math
from typing import Any, Dict, List, Literal, Tuple

from pydantic import BaseModel, ConfigDict, field_validator

from .diagnostics import Diagnostic

__all__ = [
    "Diagram",
    "DiagramEdge",
    "DiagramMarker",
    "DiagramNode",
]

Point = Tuple[float, float]

#: The collections a diagram keeps entries for, with the element kind of a
#: diagnostic and the network collection they refer to.
_SECTIONS: Tuple[Tuple[str, str, str], ...] = (
    ("nodes", "bus", "buses"),
    ("edges", "branch", "branches"),
    ("faults", "fault", "faults"),
    ("sources", "source", "sources"),
)


def _finite_point(value: Point, what: str) -> Point:
    """
    Require a point with finite coordinates.

    Parameters
    ----------
    value : tuple of float
        ``(x, y)``.
    what : str
        How to name the point in the message.

    Returns
    -------
    tuple of float
        The point.

    Raises
    ------
    ValueError
        If a coordinate is not finite.
    """
    if not all(math.isfinite(c) for c in value):
        raise ValueError(f"{what} must have finite coordinates, got {value!r}.")
    return (float(value[0]), float(value[1]))


class DiagramNode(BaseModel):
    """
    The symbol of a bus.

    Attributes
    ----------
    x, y : float
        Position of the symbol's centre.
    symbol : {"bus", "source_bus", "busbar"}
        A circle (bus), a square (a bus with an infeed) or a bar (a busbar).
    label_offset : tuple of float
        Position of the name relative to the centre.
    length : float
        Length of a busbar, >= 0; 0 for the front end's default.

    Examples
    --------
    >>> import groundinsight as gi
    >>> gi.DiagramNode(x=100, y=40, symbol="source_bus").symbol
    'source_bus'
    """

    model_config = ConfigDict(extra="forbid")

    x: float
    y: float
    symbol: Literal["bus", "source_bus", "busbar"] = "bus"
    label_offset: Point = (0.0, 0.0)
    length: float = 0.0

    @field_validator("x", "y")
    def _finite(cls, value):
        """Require finite coordinates."""
        if not math.isfinite(value):
            raise ValueError(f"must be finite, got {value!r}.")
        return value

    @field_validator("length")
    def _length(cls, value):
        """Require a finite, non-negative busbar length."""
        if not (math.isfinite(value) and value >= 0.0):
            raise ValueError(f"length must be finite and >= 0, got {value!r}.")
        return value

    @field_validator("label_offset")
    def _finite_offset(cls, value):
        """Require a finite label offset."""
        return _finite_point(value, "label_offset")


class DiagramEdge(BaseModel):
    """
    The route of a branch between its two bus symbols.

    Attributes
    ----------
    waypoints : list of tuple of float
        Intermediate points of the line, in order from ``from_bus`` to
        ``to_bus``; empty for the line the canvas routes by itself.
    label_offset : tuple of float
        Position of the label relative to the middle of the line.

    Examples
    --------
    >>> import groundinsight as gi
    >>> gi.DiagramEdge(waypoints=[(100, 40), (100, 120)]).waypoints[1]
    (100.0, 120.0)
    """

    model_config = ConfigDict(extra="forbid")

    waypoints: List[Point] = []
    label_offset: Point = (0.0, 0.0)

    @field_validator("waypoints")
    def _finite_waypoints(cls, value):
        """Require finite waypoints."""
        return [_finite_point(point, "a waypoint") for point in value]

    @field_validator("label_offset")
    def _finite_offset(cls, value):
        """Require a finite label offset."""
        return _finite_point(value, "label_offset")


class DiagramMarker(BaseModel):
    """
    The symbol of a fault (a flash) or a source (an infeed arrow) at its bus.

    Attributes
    ----------
    offset : tuple of float
        Position relative to the bus symbol.

    Examples
    --------
    >>> import groundinsight as gi
    >>> gi.DiagramMarker(offset=(12, -18)).offset
    (12.0, -18.0)
    """

    model_config = ConfigDict(extra="forbid")

    offset: Point = (0.0, 0.0)

    @field_validator("offset")
    def _finite_offset(cls, value):
        """Require a finite offset."""
        return _finite_point(value, "offset")


class Diagram(BaseModel):
    """
    Positions and routes of the elements of one network, by element name.

    Attributes
    ----------
    nodes : dict of str to DiagramNode
        One entry per bus.
    edges : dict of str to DiagramEdge
        One entry per branch.
    faults : dict of str to DiagramMarker
        Symbols of the faults; a fault without entry is drawn at its bus.
    sources : dict of str to DiagramMarker
        Symbols of the sources; as ``faults``.
    color_scheme : str
        The colouring the diagram was last shown with, e.g.
        ``"voltage_level"`` or ``"EPR"``; the front end interprets it.
    grid_size : float
        The snapping raster, > 0.

    Examples
    --------
    >>> import groundinsight as gi
    >>> diagram = gi.Diagram(nodes={"B1": gi.DiagramNode(x=0, y=0),
    ...                             "B2": gi.DiagramNode(x=200, y=0)})
    >>> sorted(diagram.renamed("bus", "B1", "Station").nodes)
    ['B2', 'Station']
    """

    model_config = ConfigDict(extra="forbid")

    nodes: Dict[str, DiagramNode] = {}
    edges: Dict[str, DiagramEdge] = {}
    faults: Dict[str, DiagramMarker] = {}
    sources: Dict[str, DiagramMarker] = {}
    color_scheme: str = "default"
    grid_size: float = 20.0

    @field_validator("grid_size")
    def _positive_grid(cls, value):
        """Require a finite, positive raster."""
        if not (math.isfinite(value) and value > 0.0):
            raise ValueError(f"grid_size must be finite and > 0, got {value!r}.")
        return value

    def _section(self, kind: str) -> str:
        """
        The field that holds entries of an element kind.

        Parameters
        ----------
        kind : str
            ``"bus"``, ``"branch"``, ``"fault"`` or ``"source"``.

        Returns
        -------
        str
            ``"nodes"``, ``"edges"``, ``"faults"`` or ``"sources"``.

        Raises
        ------
        ValueError
            For another kind.
        """
        for section, section_kind, _ in _SECTIONS:
            if section_kind == kind:
                return section
        raise ValueError(f"unknown element kind {kind!r}; expected bus, branch, fault or source.")

    def renamed(self, kind: str, old: str, new: str) -> "Diagram":
        """
        A copy with one element's entry under a new name.

        Parameters
        ----------
        kind : str
            ``"bus"``, ``"branch"``, ``"fault"`` or ``"source"``.
        old, new : str
            The element's old and new name.

        Returns
        -------
        Diagram
            The changed copy; the order of the entries is kept. Without an
            entry for ``old`` the copy is unchanged.

        Raises
        ------
        ValueError
            For an unknown kind, or if ``new`` already has an entry.

        Examples
        --------
        >>> import groundinsight as gi
        >>> gi.Diagram(edges={"K1": gi.DiagramEdge()}).renamed("branch", "K1", "K2").edges
        {'K2': DiagramEdge(waypoints=[], label_offset=(0.0, 0.0))}
        """
        section = self._section(kind)
        entries = getattr(self, section)
        if old == new or old not in entries:
            return self.model_copy(deep=True)
        if new in entries:
            raise ValueError(f"the diagram already has an entry for {kind} {new!r}.")
        moved = {new if name == old else name: entry.model_copy() for name, entry in entries.items()}
        return self.model_copy(update={section: moved}, deep=True)

    def without(self, kind: str, name: str) -> "Diagram":
        """
        A copy without one element's entry.

        Parameters
        ----------
        kind : str
            ``"bus"``, ``"branch"``, ``"fault"`` or ``"source"``.
        name : str
            The element.

        Returns
        -------
        Diagram
            The copy; unchanged if there is no entry.

        Examples
        --------
        >>> import groundinsight as gi
        >>> gi.Diagram(nodes={"B1": gi.DiagramNode(x=0, y=0)}).without("bus", "B1").nodes
        {}
        """
        section = self._section(kind)
        entries = {key: value for key, value in getattr(self, section).items() if key != name}
        return self.model_copy(update={section: entries}, deep=True)

    def check(self, network: Any) -> List[Diagnostic]:
        """
        Compare the diagram with a network; never raises.

        Parameters
        ----------
        network : Network
            The network the diagram belongs to.

        Returns
        -------
        list of Diagnostic
            ``DIAGRAM_UNKNOWN_ELEMENT`` (warning) for each entry whose element
            the network does not have, ``DIAGRAM_ELEMENT_NOT_PLACED`` (info)
            for each bus or branch without an entry -- a front end places it
            automatically. Faults and sources without an entry are drawn at
            their bus and need none.

        Examples
        --------
        >>> import groundinsight as gi
        >>> net = gi.create_network(name="n", frequencies=[50.0])
        >>> diagram = gi.Diagram(nodes={"gone": gi.DiagramNode(x=0, y=0)})
        >>> [d.code for d in diagram.check(net)]
        ['DIAGRAM_UNKNOWN_ELEMENT']
        """
        records: List[Diagnostic] = []
        for section, kind, collection in _SECTIONS:
            present = getattr(network, collection, {}) or {}
            for name in getattr(self, section):
                if name not in present:
                    records.append(Diagnostic(
                        code="DIAGRAM_UNKNOWN_ELEMENT",
                        severity="warning",
                        category="InputWarning",
                        message=(
                            f"The diagram has an entry for {kind} '{name}', which "
                            f"network '{network.name}' does not have; it is not drawn."
                        ),
                        elements=[(kind, name)],
                    ))
            if section in ("nodes", "edges"):
                for name in present:
                    if name not in getattr(self, section):
                        records.append(Diagnostic(
                            code="DIAGRAM_ELEMENT_NOT_PLACED",
                            severity="info",
                            category="InputWarning",
                            message=(
                                f"{kind.capitalize()} '{name}' has no place in the "
                                "diagram yet; it is placed automatically."
                            ),
                            elements=[(kind, name)],
                        ))
        return records

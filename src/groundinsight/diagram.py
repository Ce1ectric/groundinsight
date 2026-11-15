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

The automatic layout
--------------------
:meth:`Diagram.with_layout` places a network that has no diagram yet, or
the elements that have none. It is a layered drawing read from the infeed:
the bus with the first source is the root of its connected part, every
other bus sits in the column of its graph distance from it (breadth-first
search over all branches, active or not), and within a column the buses
follow the mean row of their neighbours in the previous column, which keeps
a radial feeder straight and a ring from crossing itself. Connected parts
are stacked below each other. Everything is on the raster and deterministic
-- the same network gives the same drawing -- so a diagram laid out when a
file is opened looks the same the next time. A front end may lay out with a
library of its own (the GUI bundles elkjs) and send the positions it
computed; this layout is what works without one, in a script or on a
server.
"""

import math
from collections import deque
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


def _neighbours(network: Any) -> Dict[str, List[str]]:
    """
    The buses adjacent to each bus, over every branch.

    Parameters
    ----------
    network : Network
        The network.

    Returns
    -------
    dict of str to list of str
        Bus name to its neighbours, in branch order; a branch to a bus the
        network does not have is skipped.
    """
    neighbours: Dict[str, List[str]] = {name: [] for name in network.buses}
    for branch in network.branches.values():
        a, b = branch.from_bus, branch.to_bus
        if a in neighbours and b in neighbours:
            neighbours[a].append(b)
            neighbours[b].append(a)
    return neighbours


def _part_layers(root: str, neighbours: Dict[str, List[str]]) -> List[List[str]]:
    """
    The columns of one connected part: buses by graph distance from ``root``.

    Within a column the buses are ordered by the mean row of their
    neighbours in the previous column; ties keep the order of discovery.

    Parameters
    ----------
    root : str
        The bus of column 0.
    neighbours : dict of str to list of str
        The output of :func:`_neighbours`.

    Returns
    -------
    list of list of str
        One list of bus names per column.
    """
    distance = {root: 0}
    queue = deque([root])
    columns: List[List[str]] = [[root]]
    while queue:
        bus = queue.popleft()
        for other in neighbours[bus]:
            if other not in distance:
                distance[other] = distance[bus] + 1
                if distance[other] == len(columns):
                    columns.append([])
                columns[distance[other]].append(other)
                queue.append(other)
    for index in range(1, len(columns)):
        row = {name: position for position, name in enumerate(columns[index - 1])}

        def barycentre(name: str, row: Dict[str, int] = row) -> float:
            """
            Mean row of a bus's neighbours in the previous column.

            Parameters
            ----------
            name : str
                The bus.
            row : dict of str to int
                Row of each bus of the previous column.

            Returns
            -------
            float
                The mean row (every bus past column 0 has such a neighbour).
            """
            rows = [row[other] for other in neighbours[name] if other in row]
            return sum(rows) / len(rows)

        columns[index].sort(key=barycentre)
    return columns


def _layered_positions(network: Any, dx: float, dy: float) -> Dict[str, Point]:
    """
    The position of every bus in the layered layout of the module docstring.

    Parameters
    ----------
    network : Network
        The network.
    dx, dy : float
        Column and row spacing.

    Returns
    -------
    dict of str to tuple of float
        Bus name to ``(x, y)``, in network order of the parts.
    """
    neighbours = _neighbours(network)
    fed = [source.bus for source in network.sources.values() if source.bus in neighbours]
    positions: Dict[str, Point] = {}
    top = 0.0
    for start in network.buses:
        if start in positions:
            continue
        columns = _part_layers(_root(start, fed, neighbours), neighbours)
        for index, column in enumerate(columns):
            for row, name in enumerate(column):
                positions[name] = (index * dx, top + row * dy)
        top += (max(len(column) for column in columns) + 1) * dy
    return positions


def _root(start: str, fed: List[str], neighbours: Dict[str, List[str]]) -> str:
    """
    The root of the connected part of ``start``: its first fed bus, else ``start``.

    Parameters
    ----------
    start : str
        A bus of the part.
    fed : list of str
        The buses with a source, in source order.
    neighbours : dict of str to list of str
        The output of :func:`_neighbours`.

    Returns
    -------
    str
        The bus of column 0.
    """
    part = {name for column in _part_layers(start, neighbours) for name in column}
    return next((bus for bus in fed if bus in part), start)


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

    def with_entries(self, kind: str, entries: Dict[str, Any]) -> "Diagram":
        """
        A copy with entries of one kind set: replaced in place, or appended.

        Parameters
        ----------
        kind : str
            ``"bus"``, ``"branch"``, ``"fault"`` or ``"source"``.
        entries : dict
            Element name to its :class:`DiagramNode` (bus),
            :class:`DiagramEdge` (branch) or :class:`DiagramMarker` (fault,
            source).

        Returns
        -------
        Diagram
            The changed copy.

        Raises
        ------
        ValueError
            For an unknown kind.
        TypeError
            If an entry is not of the model the kind takes.

        Examples
        --------
        >>> import groundinsight as gi
        >>> diagram = gi.Diagram().with_entries("bus", {"B1": gi.DiagramNode(x=20, y=40)})
        >>> diagram.nodes["B1"].y
        40.0
        """
        section = self._section(kind)
        model = {"nodes": DiagramNode, "edges": DiagramEdge}.get(section, DiagramMarker)
        for name, entry in entries.items():
            if not isinstance(entry, model):
                raise TypeError(
                    f"the diagram entry of {kind} '{name}' must be a {model.__name__}, "
                    f"got {type(entry).__name__}"
                )
        merged = {**getattr(self, section), **entries}
        return self.model_copy(update={section: merged}, deep=True)

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

    def with_layout(self, network: Any, only_unplaced: bool = False) -> "Diagram":
        """
        A copy laid out automatically (see the module docstring).

        Parameters
        ----------
        network : Network
            The network to lay out.
        only_unplaced : bool
            ``False`` (default): place every bus anew and drop the routes and
            the entries of elements the network does not have; the symbol,
            label offset and length of a bus are kept. ``True``: keep every
            entry and place only the buses without one, below what is placed
            already; branches without an entry get a straight line.

        Returns
        -------
        Diagram
            The laid-out copy; columns ``8 * grid_size`` and rows
            ``6 * grid_size`` apart, on the raster. A bus with a source gets
            the ``source_bus`` symbol when it is placed.

        Examples
        --------
        >>> import groundinsight as gi
        >>> from groundinsight.api.examples import build_minimal
        >>> diagram = gi.Diagram().with_layout(build_minimal())
        >>> [(name, node.x, node.y) for name, node in diagram.nodes.items()]
        [('bus1', 0.0, 0.0), ('bus2', 160.0, 0.0), ('bus3', 320.0, 0.0)]
        """
        dx, dy = 8.0 * self.grid_size, 6.0 * self.grid_size
        layout = _layered_positions(network, dx, dy)
        if only_unplaced:
            return self._fill_unplaced(network, layout, dy)
        update: Dict[str, Any] = {
            "nodes": {name: self._node_at(network, name, *layout[name]) for name in network.buses},
            "edges": {
                name: DiagramEdge(label_offset=self.edges[name].label_offset)
                if name in self.edges else DiagramEdge()
                for name in network.branches
            },
            "faults": {k: v for k, v in self.faults.items() if k in network.faults},
            "sources": {k: v for k, v in self.sources.items() if k in network.sources},
        }
        return self.model_copy(update=update, deep=True)

    def _node_at(self, network: Any, name: str, x: float, y: float) -> DiagramNode:
        """
        The node of a bus at ``(x, y)``, styled as before if it had one.

        Parameters
        ----------
        network : Network
            The network (a bus with a source gets ``source_bus``).
        name : str
            The bus.
        x, y : float
            The position.

        Returns
        -------
        DiagramNode
            The node.
        """
        old = self.nodes.get(name)
        if old is not None:
            return old.model_copy(update={"x": x, "y": y})
        fed = any(source.bus == name for source in network.sources.values())
        return DiagramNode(x=x, y=y, symbol="source_bus" if fed else "bus")

    def _fill_unplaced(self, network: Any, layout: Dict[str, Point], dy: float) -> "Diagram":
        """
        Place the buses without an entry below the placed ones.

        Parameters
        ----------
        network : Network
            The network.
        layout : dict of str to tuple of float
            The layered positions of every bus.
        dy : float
            The row spacing.

        Returns
        -------
        Diagram
            The copy with an entry for every bus and branch.
        """
        missing = [name for name in network.buses if name not in self.nodes]
        placed = [node for name, node in self.nodes.items() if name in network.buses]
        shift_x, shift_y = self._shift_below(placed, [layout[name] for name in missing], dy)
        nodes = dict(self.nodes)
        for name in missing:
            nodes[name] = self._node_at(network, name, layout[name][0] + shift_x,
                                        layout[name][1] + shift_y)
        edges = dict(self.edges)
        for name in network.branches:
            edges.setdefault(name, DiagramEdge())
        return self.model_copy(update={"nodes": nodes, "edges": edges}, deep=True)

    def _shift_below(self, placed: List[DiagramNode], points: List[Point], dy: float) -> Point:
        """
        The raster shift that moves new points one row below the placed nodes.

        Parameters
        ----------
        placed : list of DiagramNode
            The nodes that keep their place.
        points : list of tuple of float
            The layout positions of the new nodes.
        dy : float
            The row spacing.

        Returns
        -------
        tuple of float
            ``(shift_x, shift_y)``, multiples of ``grid_size``; ``(0, 0)``
            when one of the lists is empty. The new points start at the left
            edge of the placed ones.
        """
        if not (placed and points):
            return (0.0, 0.0)
        grid = self.grid_size
        low_x = min(x for x, _ in points)
        low_y = min(y for _, y in points)
        shift_x = math.floor((min(n.x for n in placed) - low_x) / grid) * grid
        shift_y = math.ceil((max(n.y for n in placed) + dy - low_y) / grid) * grid
        return (shift_x, shift_y)

    def placed(self, network: Any) -> bool:
        """
        Whether every bus and branch of ``network`` has an entry.

        Parameters
        ----------
        network : Network
            The network.

        Returns
        -------
        bool
            ``True`` when :meth:`with_layout` with ``only_unplaced=True``
            would place nothing.

        Examples
        --------
        >>> import groundinsight as gi
        >>> from groundinsight.api.examples import build_minimal
        >>> gi.Diagram().placed(build_minimal())
        False
        """
        return all(name in self.nodes for name in network.buses) and all(
            name in self.edges for name in network.branches
        )

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

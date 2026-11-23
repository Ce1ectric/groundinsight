# groundinsight/api/editing.py
"""
Edits of several elements at once: delete a selection, copy a selection.

A front end deletes what the user selected and copies it to another place.
Both are sequences of the single commands of :mod:`groundinsight.api.commands`
-- a bus goes only after everything that refers to it, a copy needs free
names and a place of its own -- and both have to be one undo step. The
sequences are built here, in the service layer, so that a script and a
front end do the same thing:

* :func:`removal` -- the command that deletes buses and branches together
  with what refers to them;
* :func:`duplication` -- the command that copies buses and branches, and
  the names it creates.

Both return a command without applying it; ``ProjectSession.remove_elements``
and ``ProjectSession.duplicate_elements`` apply it. Why not one command class
each: the result depends on the state of the session (which names are free,
what refers to a bus), which a command in its JSON form must not -- a
``Batch`` of plain commands replays and undoes exactly.
"""

import json
import logging
from typing import TYPE_CHECKING, Any, Dict, List, Optional, Tuple

from .commands import (
    AddBranch,
    AddSource,
    Batch,
    Command,
    PlaceBus,
    RemoveBranch,
    RemoveBus,
    RemoveFault,
    RemoveNeutral,
    RemoveSource,
    RouteBranch,
    SetCharacteristic,
)

if TYPE_CHECKING:  # pragma: no cover - typing only
    from .session import ProjectSession

logger = logging.getLogger(__name__)

__all__ = ["copy_name", "duplication", "removal"]

Item = Tuple[str, str]


def copy_name(name: str, taken: List[str], suffix: str = "copy") -> str:
    """
    A free name for a copy: ``<name> <suffix>``, then ``<name> <suffix> 2`` ...

    Parameters
    ----------
    name : str
        The original.
    taken : list of str
        Names already in use.
    suffix : str
        The word for "copy".

    Returns
    -------
    str
        The first free name.

    Examples
    --------
    >>> copy_name("MSCable", ["MSCable", "MSCable copy"])
    'MSCable copy 2'
    """
    candidate = f"{name} {suffix}"
    n = 2
    while candidate in taken:
        candidate = f"{name} {suffix} {n}"
        n += 1
    return candidate


def _existing(session: "ProjectSession", items: List[Item]) -> List[Item]:
    """The items that are buses or branches of the session's network, without repeats, in the given order."""
    network = session.network
    collections = {"bus": network.buses, "branch": network.branches}
    seen: List[Item] = []
    for kind, name in items:
        if kind in collections and name in collections[kind] and (kind, name) not in seen:
            seen.append((kind, name))
    return seen


def removal(session: "ProjectSession", items: List[Item], title: Optional[str] = None) -> Optional[Command]:
    """
    The command that deletes buses and branches with everything that refers to them.

    Parameters
    ----------
    session : ProjectSession
        The session.
    items : list of (kind, name)
        ``("bus", name)`` and ``("branch", name)`` pairs; names that do not
        exist are passed over.
    title : str, optional
        Title of the undo step if it has several commands; default
        ``"remove <n> elements"``.

    Returns
    -------
    Command or None
        ``None`` for no element; a single ``RemoveBranch`` or ``RemoveBus``
        where nothing refers to the element; otherwise one ``Batch`` that
        removes the faults, sources and neutral-point elements at the buses,
        then every branch named or ending at one of the buses (once), then
        the buses.

    Examples
    --------
    >>> from groundinsight.api import ProjectSession, removal
    >>> session = ProjectSession.from_example("minimal")
    >>> command = removal(session, [("bus", "bus2")])
    >>> [step.op for step in command.commands]
    ['remove_branch', 'remove_branch', 'remove_bus']
    """
    items = _existing(session, items)
    if not items:
        return None
    network = session.network
    buses, branches = _of_kind(items, "bus"), _of_kind(items, "branch")
    branches += [name for name, branch in network.branches.items()
                 if name not in branches and (branch.from_bus in buses or branch.to_bus in buses)]
    commands: List[Command] = _removal_at(network.faults, buses, RemoveFault)
    commands += _removal_at(network.sources, buses, RemoveSource)
    commands += _removal_at(network.neutrals, buses, RemoveNeutral)
    commands += [RemoveBranch(name=name) for name in branches]
    commands += [RemoveBus(name=name) for name in buses]
    if len(commands) == 1:
        return commands[0]
    return Batch(title=title or f"remove {len(items)} elements", commands=commands)


def _of_kind(items: List[Item], kind: str) -> List[str]:
    """The names of the items of one kind, in their order."""
    return [name for item_kind, name in items if item_kind == kind]


class _Names:
    """
    Free names for copies, handed out one at a time.

    Parameters
    ----------
    taken : dict of str to list of str
        Per kind the names in use; extended by every name handed out.
    suffix : str
        The word for "copy".
    """

    def __init__(self, taken: Dict[str, List[str]], suffix: str):
        self.taken = taken
        self.suffix = suffix

    def __call__(self, kind: str, original: str) -> str:
        """
        A free name for a copy of ``original``, which is then taken.

        Parameters
        ----------
        kind : str
            ``"bus"``, ``"branch"`` or ``"source"``.
        original : str
            Name of the element that is copied.

        Returns
        -------
        str
            The name of the copy.
        """
        copy = copy_name(original, self.taken[kind], self.suffix)
        self.taken[kind].append(copy)
        return copy


def _removal_at(collection: Dict[str, Any], buses: List[str], command: Any) -> List[Command]:
    """One ``command`` per element of ``collection`` that stands at one of ``buses``."""
    return [command(name=name) for name, element in collection.items() if element.bus in buses]


def _offset(nodes: Dict[str, Any], buses: List[str], at: Optional[Tuple[float, float]], grid: float
            ) -> Tuple[float, float]:
    """
    How far the copies of ``buses`` are moved from their originals.

    Parameters
    ----------
    nodes : dict
        Diagram node per bus.
    buses : list of str
        The copied buses.
    at : tuple of float, optional
        Where their centre goes; default two raster steps right and down.
    grid : float
        Raster of the diagram.

    Returns
    -------
    tuple of float
        ``(dx, dy)`` on the raster, moved on to the lower right until no
        copy lies on another symbol (a copy on a symbol would hide it).
    """
    dx = dy = 2 * grid
    if at is not None and buses:
        dx = round((at[0] - sum(nodes[b].x for b in buses) / len(buses)) / grid) * grid
        dy = round((at[1] - sum(nodes[b].y for b in buses) / len(buses)) / grid) * grid
    occupied = {(node.x, node.y) for node in nodes.values()}
    while any((nodes[b].x + dx, nodes[b].y + dy) in occupied for b in buses):
        dx += 2 * grid
        dy += 2 * grid
    return dx, dy


def _source_copies(session: "ProjectSession", copies: Dict[str, str], name_of: Any) -> List[Command]:
    """The sources at copied buses, added again at the copies."""
    commands: List[Command] = []
    for name, source in session.network.sources.items():
        if source.bus not in copies:
            continue
        fields = json.loads(source.model_dump_json(exclude={"name", "bus"}))
        commands.append(AddSource(name=name_of("source", name), bus=copies[source.bus], **fields))
    return commands


def _bus_copies(network: Any, nodes: Dict[str, Any], buses: List[str], shift: Tuple[float, float],
                name_of: Any) -> Tuple[List[Command], Dict[str, str]]:
    """
    The commands that place the copies of buses.

    Parameters
    ----------
    network : Network
        The network.
    nodes : dict
        Diagram node per bus.
    buses : list of str
        The buses to copy.
    shift : tuple of float
        How far the copies are moved.
    name_of : callable
        ``name_of(kind, original)`` gives a free name and takes it.

    Returns
    -------
    commands : list of Command
        A ``PlaceBus`` per bus, and a ``SetCharacteristic`` where the bus
        carries its own earthing characteristic.
    copies : dict of str to str
        Original name to the name of its copy.
    """
    commands: List[Command] = []
    copies: Dict[str, str] = {}
    for name in buses:
        bus, node = network.buses[name], nodes[name]
        copies[name] = name_of("bus", name)
        commands.append(PlaceBus(
            name=copies[name], type=bus.type.name, specific_earth_resistance=bus.specific_earth_resistance,
            active=bus.active, description=bus.description, x=node.x + shift[0], y=node.y + shift[1],
            symbol=node.symbol, label_offset=node.label_offset, length=node.length))
        if bus.characteristic is not None:
            commands.append(SetCharacteristic(target="bus", name=copies[name],
                                              characteristic=json.loads(bus.characteristic.model_dump_json())))
    return commands, copies


def _branch_copies(network: Any, edges: Dict[str, Any], branches: List[str], copies: Dict[str, str],
                   shift: Tuple[float, float], name_of: Any) -> Tuple[List[Command], List[str]]:
    """
    The commands that add the copies of branches.

    Parameters
    ----------
    network : Network
        The network.
    edges : dict
        Diagram edge per branch.
    branches : list of str
        The branches to copy.
    copies : dict of str to str
        Copied buses: original name to the name of the copy.
    shift : tuple of float
        How far the copied buses are moved.
    name_of : callable
        ``name_of(kind, original)`` gives a free name and takes it.

    Returns
    -------
    commands : list of Command
        An ``AddBranch`` per branch -- each end at the copy of its bus if
        that bus is copied, else at the bus itself -- and a ``RouteBranch``
        where both ends are copied and the original has waypoints.
    names : list of str
        The names of the copies.
    """
    commands: List[Command] = []
    names: List[str] = []
    for name in branches:
        branch = network.branches[name]
        copy = name_of("branch", name)
        commands.append(AddBranch(
            name=copy, type=branch.type.name, from_bus=copies.get(branch.from_bus, branch.from_bus),
            to_bus=copies.get(branch.to_bus, branch.to_bus), length=branch.length,
            specific_earth_resistance=branch.specific_earth_resistance,
            parallel_coefficient=branch.parallel_coefficient, active=branch.active,
            description=branch.description))
        waypoints = edges[name].waypoints if name in edges else []
        if waypoints and branch.from_bus in copies and branch.to_bus in copies:
            commands.append(RouteBranch(name=copy, waypoints=[(x + shift[0], y + shift[1]) for x, y in waypoints]))
        names.append(copy)
    return commands, names


def duplication(session: "ProjectSession", items: List[Item], at: Optional[Tuple[float, float]] = None,
                suffix: str = "copy", title: Optional[str] = None) -> Tuple[Optional[Command], List[Item]]:
    """
    The command that copies buses and branches, and what it creates.

    A bus is copied with its fields, its symbol, its own earthing
    characteristic and its sources (a source bus without a source would feed
    nothing). Faults and neutral-point elements are not copied: a fault
    location and the treatment of a neutral are set on purpose. A branch is
    copied with its fields; each end goes to the copy of its bus if that bus
    is copied too, else it stays -- so a branch copied alone is a parallel
    branch. Copies are named ``<name> <suffix>``, ``<name> <suffix> 2``, ...

    Parameters
    ----------
    session : ProjectSession
        The session.
    items : list of (kind, name)
        ``("bus", name)`` and ``("branch", name)`` pairs; names that do not
        exist are passed over.
    at : tuple of float, optional
        Where the centre of the copied buses goes (diagram coordinates);
        default two raster steps right of and below the originals. Either
        way the copies move on to the lower right until none of them lies on
        another symbol.
    suffix : str
        The word for "copy" in the names.
    title : str, optional
        Title of the undo step if it has several commands; default
        ``"duplicate <n> elements"``.

    Returns
    -------
    command : Command or None
        One command (a ``Batch`` for more than one step); ``None`` for no
        element.
    created : list of (kind, name)
        The copies, buses first.

    Examples
    --------
    >>> from groundinsight.api import ProjectSession, duplication
    >>> session = ProjectSession.from_example("minimal")
    >>> command, created = duplication(session, [("bus", "bus2")])
    >>> created
    [('bus', 'bus2 copy')]
    """
    items = _existing(session, items)
    if not items:
        return None, []
    network = session.network
    diagram = session.diagram()
    nodes = {node.name: node for node in diagram.nodes}
    edges = {edge.name: edge for edge in diagram.edges}
    buses = _of_kind(items, "bus")
    shift = _offset(nodes, buses, at, float(diagram.grid_size or 20))
    name_of = _Names({"bus": list(network.buses), "branch": list(network.branches),
                      "source": list(network.sources)}, suffix)
    commands, copies = _bus_copies(network, nodes, buses, shift, name_of)
    commands += _source_copies(session, copies, name_of)
    branch_commands, branch_copies = _branch_copies(
        network, edges, _of_kind(items, "branch"), copies, shift, name_of)
    commands += branch_commands
    created: List[Item] = [("bus", copy) for copy in copies.values()]
    created += [("branch", copy) for copy in branch_copies]
    if len(commands) == 1:
        return commands[0], created
    return Batch(title=title or f"duplicate {len(items)} elements", commands=commands), created

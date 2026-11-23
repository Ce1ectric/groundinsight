# groundinsight_gui/colouring.py
"""
Colouring of the network diagram: a colour per element and a legend.

Pure functions of DTOs -- the diagram, a fault result, the pre-flight check --
so they are tested without a browser and contain no physics: a numerical mode
maps a number the result already carries (the RMS ``EPR_V`` of ``res_buses``,
``U_vT_V``, the shield current ``I_branch_A`` of ``res_branches`` -- the
current in a branch's earthing conductor or cable shield) onto a colour scale, a
categorical mode maps a property (voltage level, type, characteristic origin)
onto a palette. The only derived quantity is the *earthing group*: the
connected parts of the graph of active branches with a grounding conductor,
which is topology read off the diagram DTO, not a computation of the solver.

The categorical palette is Okabe and Ito's, distinguishable with the common
colour-vision deficiencies; the scale runs through five stops of viridis,
which is perceptually uniform and prints in greyscale.
"""

from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Optional, Tuple

__all__ = ["MODES", "NEUTRAL", "UTILISATION_MODES", "Colouring", "LegendEntry", "colour", "utilisation_colour"]

#: The colouring modes, in menu order.
MODES = ("voltage_level", "type", "group", "infeed_fault", "epr", "u_touch", "i_branch",
         "util_epr", "util_touch", "util_thermal", "characteristic", "diagnostics")

#: Modes that need a fault result.
RESULT_MODES = ("epr", "u_touch", "i_branch", "util_epr", "util_touch", "util_thermal")

#: Modes that read the assessment of the result against the project's
#: criteria (``ProjectSession.assessment``): the column of its bus and branch
#: tables they colour.
UTILISATION_MODES = {
    "util_epr": ("epr_utilisation_pct", None),
    "util_touch": ("touch_utilisation_pct", None),
    "util_thermal": ("thermal_utilisation_pct", "thermal_utilisation_pct"),
}

#: The utilisation ramp: green, amber at 100 % of the limit, red above.
UTILISATION_COLOURS = ("#009E73", "#E69F00", "#c62828")

#: Colour of an element a mode says nothing about.
NEUTRAL = "#9aa3ad"

#: Okabe-Ito, without the yellow that vanishes on white.
PALETTE = ("#0072B2", "#E69F00", "#009E73", "#CC79A7", "#56B4E9", "#D55E00", "#000000", "#7f6a3a")

#: Five stops of viridis, low to high.
SCALE = ("#440154", "#3b528b", "#21918c", "#5ec962", "#fde725")

SEVERITY_COLOURS = {"error": "#c62828", "warning": "#e69f00", "info": "#56B4E9", "ok": "#009E73"}


@dataclass(frozen=True)
class LegendEntry:
    """
    One line of a categorical legend.

    Attributes
    ----------
    colour : str
        CSS colour.
    label : str
        What it stands for; ``key`` is a text key of the GUI when ``True``.
    key : bool
        Whether ``label`` is translated by the GUI.
    """

    colour: str
    label: str
    key: bool = False


@dataclass
class Colouring:
    """
    Colours of nodes and edges, and their legend.

    Attributes
    ----------
    mode : str
        One of :data:`MODES`.
    nodes, edges : dict of str to str
        Element name to CSS colour.
    entries : list of LegendEntry
        A categorical legend; empty for a scale.
    scale : tuple, optional
        ``(minimum, maximum, unit)`` of a numerical mode; ``None`` when
        categorical or when there is no number to show.
    gradient : str
        CSS ``linear-gradient`` of the scale.
    node_values, edge_values : dict of str to float
        The number a numerical mode coloured each element by (elements
        without one are left out); empty for a categorical mode. The plan
        writes it beside the element.
    unit : str
        Unit of the values; empty without values.
    """

    mode: str
    nodes: Dict[str, str] = field(default_factory=dict)
    edges: Dict[str, str] = field(default_factory=dict)
    entries: List[LegendEntry] = field(default_factory=list)
    scale: Optional[Tuple[float, float, str]] = None
    node_values: Dict[str, float] = field(default_factory=dict)
    edge_values: Dict[str, float] = field(default_factory=dict)
    unit: str = ""
    gradient: str = "linear-gradient(to right, " + ", ".join(SCALE) + ")"


def _hex(colour: str) -> Tuple[int, int, int]:
    """``#rrggbb`` as integers."""
    return tuple(int(colour[i:i + 2], 16) for i in (1, 3, 5))  # type: ignore[return-value]


def scale_colour(fraction: float) -> str:
    """
    The colour of a position on the scale.

    Parameters
    ----------
    fraction : float
        0 (low) to 1 (high); clipped.

    Returns
    -------
    str
        ``#rrggbb``, linear between the two neighbouring stops.

    Examples
    --------
    >>> scale_colour(0.0), scale_colour(1.0)
    ('#440154', '#fde725')
    """
    t = min(max(fraction, 0.0), 1.0) * (len(SCALE) - 1)
    i = min(int(t), len(SCALE) - 2)
    w = t - i
    a, b = _hex(SCALE[i]), _hex(SCALE[i + 1])
    return "#" + "".join(f"{round(x + w * (y - x)):02x}" for x, y in zip(a, b, strict=True))


def _categorical(values: Dict[Any, Any], *, label=str) -> Tuple[Dict[Any, str], List[LegendEntry]]:
    """
    Colours for categories, in order of first appearance.

    Parameters
    ----------
    values : dict
        Element (a name, or a ``(kind, name)`` pair) to category; ``None``
        is neutral.
    label : callable
        Category to its legend text.

    Returns
    -------
    colours : dict
        Element to colour.
    entries : list of LegendEntry
        One per category.
    """
    order: List[Any] = []
    for value in values.values():
        if value is not None and value not in order:
            order.append(value)
    palette = {value: PALETTE[i % len(PALETTE)] for i, value in enumerate(order)}
    colours = {name: palette.get(value, NEUTRAL) for name, value in values.items()}
    return colours, [LegendEntry(palette[value], label(value)) for value in order]


def _numerical(values: Dict[str, Optional[float]], unit: str) -> Tuple[Dict[str, str], Optional[Tuple[float, float, str]]]:
    """
    Colours on the scale between the smallest and the largest value.

    Parameters
    ----------
    values : dict of str to float or None
        Element name to value; ``None`` is neutral.
    unit : str
        Unit of the values.

    Returns
    -------
    colours : dict of str to str
        Element name to colour; all at the middle when the values are equal.
    scale : tuple or None
        ``(minimum, maximum, unit)``; ``None`` without values.
    """
    present = [v for v in values.values() if v is not None]
    if not present:
        return {name: NEUTRAL for name in values}, None
    low, high = min(present), max(present)
    span = high - low
    colours = {
        name: NEUTRAL if v is None else scale_colour((v - low) / span if span > 0 else 0.5)
        for name, v in values.items()
    }
    return colours, (low, high, unit)


def _ramp(a: str, b: str, w: float) -> str:
    """The colour a fraction ``w`` of the way from ``a`` to ``b``."""
    x, y = _hex(a), _hex(b)
    return "#" + "".join(f"{round(p + w * (q - p)):02x}" for p, q in zip(x, y, strict=True))


def utilisation_colour(percent: float, top: float) -> str:
    """
    The colour of a utilisation in percent of the limit.

    Parameters
    ----------
    percent : float
        Utilisation in percent.
    top : float
        The top of the scale (at least 100): red.

    Returns
    -------
    str
        ``#rrggbb``: green at 0 %, amber at 100 %, red at ``top`` and above.

    Examples
    --------
    >>> utilisation_colour(0.0, 150.0), utilisation_colour(100.0, 150.0), utilisation_colour(200.0, 150.0)
    ('#009e73', '#e69f00', '#c62828')
    """
    green, amber, red = UTILISATION_COLOURS
    if percent <= 100.0:
        return _ramp(green, amber, max(percent, 0.0) / 100.0)
    return _ramp(amber, red, min((percent - 100.0) / (top - 100.0), 1.0) if top > 100.0 else 1.0)


def _utilisation(values: Dict[str, Optional[float]], top: float) -> Dict[str, str]:
    """Colours of a utilisation table; ``None`` (not assessed) is neutral."""
    return {name: NEUTRAL if v is None else utilisation_colour(v, top) for name, v in values.items()}


def _colour_utilisation(out: "Colouring", mode: str, diagram: Any, assessment: Any) -> None:
    """Fill ``out`` for a utilisation mode from the assessment (percent of the limit)."""
    bus_column, branch_column = UTILISATION_MODES[mode]
    buses = {r["bus_name"]: r[bus_column] for r in assessment.buses.rows} if bus_column else {}
    branches = ({r["branch_name"]: r[branch_column] for r in assessment.branches.rows}
                if branch_column else {})
    node_values = {n.name: buses.get(n.name) for n in diagram.nodes}
    edge_values = {e.name: branches.get(e.name) for e in diagram.edges}
    present = [v for v in [*node_values.values(), *edge_values.values()] if v is not None]
    if not present:
        return
    top = max(150.0, max(present))
    out.nodes = _utilisation(node_values, top)
    out.node_values = _present(node_values)
    if branch_column:
        out.edges = _utilisation(edge_values, top)
        out.edge_values = _present(edge_values)
    out.scale = (0.0, top, "%")
    out.unit = "%"
    stop = 100.0 * 100.0 / top
    out.gradient = (f"linear-gradient(to right, {UTILISATION_COLOURS[0]} 0%, "
                    f"{UTILISATION_COLOURS[1]} {stop:.1f}%, {UTILISATION_COLOURS[2]} 100%)")


def _present(values: Dict[str, Optional[float]]) -> Dict[str, float]:
    """The elements that have a value."""
    return {name: value for name, value in values.items() if value is not None}


def _groups(diagram: Any) -> Dict[str, Optional[int]]:
    """
    The earthing group of every bus: connected through active earth conductors.

    Parameters
    ----------
    diagram : DiagramDTO
        The diagram.

    Returns
    -------
    dict of str to int or None
        Bus name to the group's number (1, 2, ... in bus order); ``None`` for
        an inactive bus.
    """
    active = {node.name for node in diagram.nodes if node.active}
    parent = {name: name for name in active}

    def root(name: str) -> str:
        """
        The representative of a bus's group.

        Parameters
        ----------
        name : str
            The bus.

        Returns
        -------
        str
            The root of its union-find tree.
        """
        while parent[name] != name:
            parent[name] = parent[parent[name]]
            name = parent[name]
        return name

    for edge in diagram.edges:
        if edge.active and edge.grounding_conductor and edge.from_bus in active and edge.to_bus in active:
            parent[root(edge.from_bus)] = root(edge.to_bus)
    numbers: Dict[str, int] = {}
    groups: Dict[str, Optional[int]] = {}
    for node in diagram.nodes:
        if node.name not in active:
            groups[node.name] = None
            continue
        groups[node.name] = numbers.setdefault(root(node.name), len(numbers) + 1)
    return groups


def _rms(rows: Iterable[Dict[str, Any]], key: str, column: str) -> Dict[str, Optional[float]]:
    """
    The RMS row value of every element of a result table.

    Parameters
    ----------
    rows : iterable of dict
        Rows of ``res_buses`` / ``res_branches`` as a DTO carries them.
    key : str
        The name column (``bus_name``, ``branch_name``).
    column : str
        The value column.

    Returns
    -------
    dict of str to float or None
        Element name to the value of its RMS row.
    """
    return {row[key]: row.get(column) for row in rows if row.get("is_rms")}


def _severities(report: Any) -> Dict[Tuple[str, str], str]:
    """
    The most severe diagnostic per element.

    Parameters
    ----------
    report : CheckReportDTO
        The check.

    Returns
    -------
    dict
        ``(kind, name)`` to ``"error"``, ``"warning"`` or ``"info"``.
    """
    rank = {"info": 0, "warning": 1, "error": 2}
    worst: Dict[Tuple[str, str], str] = {}
    for record in report.diagnostics:
        for element in record.elements:
            ref = (element.kind, element.name)
            if rank[record.severity] >= rank.get(worst.get(ref, "info"), 0):
                worst[ref] = record.severity
    return worst


def colour(mode: str, diagram: Any, *, result: Any = None, check: Any = None,
           assessment: Any = None) -> Colouring:
    """
    Colour the diagram in one mode.

    Parameters
    ----------
    mode : str
        One of :data:`MODES`.
    diagram : DiagramDTO
        The diagram.
    result : FaultResultDTO, optional
        The fault result the numerical modes read.
    check : CheckReportDTO, optional
        The pre-flight check the ``diagnostics`` mode reads.
    assessment : AssessmentDTO, optional
        The result held to the project's criteria
        (``ProjectSession.assessment``), which the utilisation modes read:
        percent of the limit on a ramp from green through amber (100 %) to
        red; neutral where nothing was assessed.

    Returns
    -------
    Colouring
        Colours of every node and edge, and the legend. Without the result
        (or the check) a mode that needs it leaves everything neutral.

    Raises
    ------
    ValueError
        For an unknown mode.

    Examples
    --------
    >>> from groundinsight.api import ProjectSession
    >>> session = ProjectSession.from_example("minimal")
    >>> colour("infeed_fault", session.diagram()).nodes
    {'bus1': '#0072B2', 'bus2': '#9aa3ad', 'bus3': '#D55E00'}
    """
    if mode not in MODES:
        raise ValueError(f"unknown colouring {mode!r}; modes: {', '.join(MODES)}")
    nodes = {node.name: NEUTRAL for node in diagram.nodes}
    edges = {edge.name: NEUTRAL for edge in diagram.edges}
    out = Colouring(mode=mode, nodes=nodes, edges=edges)
    if mode == "voltage_level":
        out.nodes, out.entries = _categorical(
            {n.name: n.voltage_level for n in diagram.nodes}, label=lambda v: f"{v:g} kV")
    elif mode == "type":
        values: Dict[Any, Any] = {("bus", n.name): n.bus_type for n in diagram.nodes}
        values.update({("branch", e.name): e.branch_type for e in diagram.edges})
        colours, out.entries = _categorical(values)
        out.nodes = {name: c for (kind, name), c in colours.items() if kind == "bus"}
        out.edges = {name: c for (kind, name), c in colours.items() if kind == "branch"}
    elif mode == "group":
        out.nodes, out.entries = _categorical(_groups(diagram), label=lambda g: f"#{g}")
    elif mode == "infeed_fault":
        for node in diagram.nodes:
            if node.has_fault:
                out.nodes[node.name] = PALETTE[5]
            elif node.has_source:
                out.nodes[node.name] = PALETTE[0]
        out.entries = [LegendEntry(PALETTE[0], "legend_infeed", True),
                       LegendEntry(PALETTE[5], "legend_fault", True),
                       LegendEntry(NEUTRAL, "legend_other", True)]
    elif mode in UTILISATION_MODES:
        if assessment is not None:
            _colour_utilisation(out, mode, diagram, assessment)
    elif mode in RESULT_MODES:
        if result is not None:
            if mode == "i_branch":
                values = _rms(result.branches.rows, "branch_name", "I_branch_A")
                shown = {e.name: values.get(e.name) for e in diagram.edges}
                out.edges, out.scale = _numerical(shown, "A")
                out.edge_values, out.unit = _present(shown), "A"
            else:
                column = "EPR_V" if mode == "epr" else "U_vT_V"
                values = _rms(result.buses.rows, "bus_name", column)
                shown = {n.name: values.get(n.name) for n in diagram.nodes}
                out.nodes, out.scale = _numerical(shown, "V")
                out.node_values, out.unit = _present(shown), "V"
    elif mode == "characteristic":
        labels = {"bus": "legend_char_bus", "bus_type": "legend_char_type", None: "legend_char_formula"}
        colours = {"bus": PALETTE[2], "bus_type": PALETTE[4], None: NEUTRAL}
        out.nodes = {n.name: colours[n.characteristic] for n in diagram.nodes}
        out.entries = [LegendEntry(colours[k], labels[k], True) for k in ("bus", "bus_type", None)]
    elif mode == "diagnostics" and check is not None:
        worst = _severities(check)
        out.nodes = {n.name: SEVERITY_COLOURS[worst.get(("bus", n.name), "ok")] for n in diagram.nodes}
        out.edges = {e.name: SEVERITY_COLOURS[worst.get(("branch", e.name), "ok")] for e in diagram.edges}
        out.entries = [LegendEntry(SEVERITY_COLOURS[k], f"severity_{k}", True)
                       for k in ("error", "warning", "info", "ok")]
    return out

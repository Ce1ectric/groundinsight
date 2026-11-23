# groundinsight_gui/canvas.py
"""
The network canvas element: a Vue Flow drawing of the session's diagram.

The element only carries data across: the :class:`DiagramDTO
<groundinsight.api.DiagramDTO>` of the session, the colours of the chosen
colouring, the selected elements and the active tool go to the browser as
props; what the user does there comes back as events (see
``network_canvas.js``) that :mod:`groundinsight_gui.plan` turns into
commands. The browser decides nothing about the network -- a dragged symbol
moves for good only once the session has applied the ``MoveNodes`` command
and the new diagram is sent back.

Vue Flow, its minimap, controls and background, and elkjs are served from
the vendored bundle in ``static/vueflow`` (no CDN at run time).
"""

import logging
from typing import Any, Dict, List, Optional, Sequence, Tuple

from nicegui import ui

logger = logging.getLogger(__name__)

__all__ = ["CANVAS_CSS", "EVENTS", "TOOLS", "NetworkCanvas"]

#: The tools of the palette.
TOOLS = ("select", "bus", "source_bus", "branch", "fault")

#: Events the canvas emits, with the keys of their arguments.
EVENTS = {
    "place": ("x", "y", "symbol"),
    "move": ("positions",),
    "connect": ("from_bus", "to_bus"),
    "fault": ("bus",),
    "select": ("kind", "name", "add"),
    "select_many": ("items",),
    "open": ("kind", "name"),
    "delete": ("items",),
    "layout": ("positions",),
    "key": ("action", "x", "y"),
    "menu": ("action", "kind", "name", "x", "y"),
}

CANVAS_CSS = """
.gi-canvas { position: relative; width: 100%; height: 100%; outline: none; background: #fbfbfa; }
.gi-menu { position: absolute; z-index: 20; min-width: 13rem; padding: 4px 0; background: #fff;
           border: 1px solid #d6dbe1; border-radius: 6px; box-shadow: 0 6px 18px rgba(31, 42, 54, 0.18);
           font-size: 13px; color: #1f2a36; user-select: none; }
.gi-menu-item { display: flex; justify-content: space-between; gap: 1.5rem; padding: 5px 12px; cursor: pointer;
                white-space: nowrap; }
.gi-menu-item:hover { background: #e8f0fb; }
.gi-menu-item.gi-menu-danger { color: #b3261e; }
.gi-menu-item.gi-menu-rule { border-top: 1px solid #e3e6ea; margin-top: 4px; padding-top: 8px; }
.gi-menu-key { color: #6b7480; font-size: 12px; }
.gi-node { position: relative; border: 2px solid #1f2a36; box-sizing: border-box; }
.gi-node.gi-bus { border-radius: 50%; }
.gi-node.gi-source_bus { border-radius: 2px; }
.gi-node.gi-busbar { border-radius: 1px; border-width: 1px; }
.gi-node.gi-sel { box-shadow: 0 0 0 calc(3px * var(--gi-s, 1)) #ffb300; }
.gi-node.gi-off { opacity: 0.4; border-style: dashed; }
/* Texts of the plan. --gi-s is set by the canvas from the zoom: below 85 %
   the texts grow against it, so a name stays about 11 px high on screen
   while the plan is zoomed out. Each text is anchored at the corner next to
   its symbol and grows away from it. Zoomed out further the branch names go
   first (.gi-far), then the bus names and values (.gi-very-far); what is
   selected or under the pointer keeps its name. */
.gi-label, .gi-infeed { position: absolute; white-space: nowrap; pointer-events: none;
            line-height: 1.15; text-shadow: 0 0 2px #fbfbfa, 0 0 2px #fbfbfa, 0 0 3px #fbfbfa; }
.gi-label { display: flex; flex-direction: column; align-items: flex-start;
            font-size: calc(13px * var(--gi-s, 1)); color: #1f2a36; transform: translateY(-100%); }
.gi-value { margin-left: 0.45em; font-weight: 600; font-variant-numeric: tabular-nums; }
.gi-flash { color: #b84a00; font-weight: 600; }
.gi-neutral { display: block; color: #2e6b3a; font-weight: 500; white-space: nowrap; }
.gi-infeed { left: 50%; bottom: 100%; transform: translateX(-50%); font-size: calc(12px * var(--gi-s, 1));
             color: #0072B2; line-height: 1; }
.gi-stale .gi-value, .gi-stale .gi-edge-value { opacity: 0.6; font-style: italic; }
/* Hidden, not removed: the texts keep their place in the layout, so crossing a threshold while
   zooming a large plan repaints but does not lay a thousand texts out again. */
.gi-far .gi-edge-label { visibility: hidden; }
.gi-far .gi-edge.gi-sel .gi-edge-label, .gi-far .gi-edge:hover .gi-edge-label { visibility: visible; }
.gi-very-far .gi-label, .gi-very-far .gi-flash-name, .gi-very-far .gi-neutral { visibility: hidden; }
.gi-very-far .gi-flash { visibility: visible; }  /* the flash itself stays: where the faults are */
.gi-very-far .gi-node.gi-sel .gi-label, .gi-very-far .vue-flow__node:hover .gi-label,
.gi-very-far .gi-node.gi-sel .gi-flash-name, .gi-very-far .vue-flow__node:hover .gi-flash-name { visibility: visible; }
.gi-plan-search .q-field__control, .gi-plan-search .q-field__native { min-height: 26px; height: 26px; }
.gi-plan-search .q-field__native { padding: 0; }
.gi-plan-search .q-field__marginal { height: 26px; }
.gi-plan-run.q-btn { min-height: 26px; height: 26px; font-size: 13px; }
.gi-hint { position: absolute; inset: 0; display: flex; align-items: center; justify-content: center;
           padding: 0 15%; text-align: center; color: #6b7480; font-size: 14px; pointer-events: none; z-index: 4; }
.gi-handle { position: absolute !important; inset: 0 !important; width: 100% !important;
             height: 100% !important; transform: none !important; border: none !important;
             background: transparent !important; border-radius: inherit; opacity: 0;
             min-width: 0; min-height: 0; }
/* The handle covers the symbol: only the branch tool may start a line there,
   otherwise the symbol is dragged (Vue Flow's own rule makes it clickable). */
.gi-canvas .vue-flow__node .gi-handle { pointer-events: none !important; }
.gi-canvas.gi-tool-branch .vue-flow__node .gi-handle { pointer-events: all !important; cursor: crosshair; }
.gi-tool-bus .vue-flow__pane, .gi-tool-source_bus .vue-flow__pane { cursor: copy; }
.gi-tool-fault .vue-flow__node { cursor: pointer; }
.gi-edge-line { fill: none; stroke-width: 2.5; }
.gi-edge-hit { fill: none; stroke: transparent; stroke-width: 14; }
.gi-edge.gi-noearth .gi-edge-line { stroke-dasharray: 7 4; }
.gi-edge.gi-off .gi-edge-line { stroke-dasharray: 2 5; opacity: 0.5; }
.gi-edge.gi-sel .gi-edge-line { stroke-width: 4.5; filter: drop-shadow(0 0 2px #ffb300); }
.gi-edge-label { pointer-events: none; paint-order: stroke; stroke: #fbfbfa; stroke-width: 3px;
                 stroke-linejoin: round; font-size: calc(11px * var(--gi-s, 1)); fill: #3b4652; }
.gi-edge-value { fill: #1f2a36; font-weight: 600; }
"""


class NetworkCanvas(ui.element, component="network_canvas.js", esm={"groundinsight-vueflow": "static/vueflow"}):
    """
    Vue Flow canvas showing a diagram DTO.

    Parameters
    ----------
    diagram : dict
        ``DiagramDTO.model_dump()``.
    colours : dict, optional
        ``{"nodes": {name: css}, "edges": {name: css}}``.
    tool : str
        One of :data:`TOOLS`.
    menus : dict, optional
        The context menus: ``"bus"``, ``"branch"``, ``"group"`` (several
        selected elements) and ``"pane"`` to a list of entries ``{"action",
        "label"}``, optionally with ``"key"`` (a shortcut shown beside the
        label, ``Mod`` for Ctrl or Cmd), ``"danger"``, ``"rule"`` (a line
        above) and ``"when"`` (``"active"`` or ``"inactive"``: only for an
        element in that state). A chosen entry arrives as the event ``menu``.
    annotations : dict, optional
        What is written at the elements: ``{"nodes": {name: note}, "edges":
        {name: note}, "stale": bool}``. A note may carry ``"value"`` (the
        number of the colouring, with its unit), ``"tip"`` (the tooltip) and
        for a bus ``"faults"`` and ``"sources"`` (their names). ``stale``
        greys the values: they belong to a result older than the inputs.
    hint : str
        Shown in the middle of a plan without elements.
    """

    def __init__(self, diagram: Dict[str, Any], *, colours: Optional[Dict[str, Any]] = None,
                 tool: str = "select", menus: Optional[Dict[str, List[Dict[str, Any]]]] = None,
                 annotations: Optional[Dict[str, Any]] = None, hint: str = "") -> None:
        super().__init__()
        self._props["menus"] = menus or {}
        self._props["annotations"] = annotations or {"nodes": {}, "edges": {}, "stale": False}
        self._props["hint"] = hint
        self._props["focus"] = None
        self._focus_count = 0
        self._props["diagram"] = diagram
        self._props["colours"] = colours or {"nodes": {}, "edges": {}}
        self._props["tool"] = tool
        self._props["selected"] = []
        self._props["flow-id"] = f"gi-flow-{self.id}"

    @property
    def tool(self) -> str:
        """The active tool."""
        return self._props["tool"]

    @property
    def diagram(self) -> Dict[str, Any]:
        """The diagram shown, as a dict."""
        return self._props["diagram"]

    @property
    def selection(self) -> List[Dict[str, Any]]:
        """The selected elements, each ``{"kind", "name"}``."""
        return list(self._props["selected"])

    @property
    def selected(self) -> Optional[Dict[str, Any]]:
        """The selected element, ``{"kind", "name"}``; ``None`` for none or several."""
        items = self._props["selected"]
        return items[0] if len(items) == 1 else None

    def show(self, diagram: Dict[str, Any], colours: Dict[str, Any],
             annotations: Optional[Dict[str, Any]] = None, hint: str = "") -> None:
        """
        Show a new diagram, its colours and what is written at the elements.

        Parameters
        ----------
        diagram : dict
            ``DiagramDTO.model_dump()``.
        colours : dict
            ``{"nodes": ..., "edges": ...}``.
        annotations : dict, optional
            Values, tooltips and marker names (see the class).
        hint : str
            Shown in the middle of a plan without elements.
        """
        self._props["diagram"] = diagram
        self._props["colours"] = colours
        self._props["annotations"] = annotations or {"nodes": {}, "edges": {}, "stale": False}
        self._props["hint"] = hint
        self.update()

    def set_tool(self, tool: str) -> None:
        """
        Switch the palette tool.

        Parameters
        ----------
        tool : str
            One of :data:`TOOLS`.

        Raises
        ------
        ValueError
            For an unknown tool.
        """
        if tool not in TOOLS:
            raise ValueError(f"unknown tool {tool!r}")
        self._props["tool"] = tool
        self.update()

    def select(self, kind: Optional[str], name: Optional[str]) -> None:
        """
        Mark one element as selected (``None`` clears).

        Parameters
        ----------
        kind : {"bus", "branch"} or None
            The element kind.
        name : str or None
            The element.
        """
        self.select_items([(kind, name)] if kind and name else [])

    def select_items(self, items: Sequence[Tuple[str, str]]) -> None:
        """
        Mark several elements as selected.

        Parameters
        ----------
        items : sequence of (kind, name)
            The elements; empty clears the selection.
        """
        self._props["selected"] = [{"kind": kind, "name": name} for kind, name in items]
        self.update()

    def focus(self, kind: str, name: str) -> None:
        """
        Move an element into the middle of the canvas (zoomed in if its name would be hidden).

        Parameters
        ----------
        kind : {"bus", "branch"}
            The element kind.
        name : str
            The element.
        """
        # A prop, not a method call: it also reaches a canvas whose tab is
        # only just being shown. The counter makes a repeated request a new one.
        self._focus_count += 1
        self._props["focus"] = {"kind": kind, "name": name, "n": self._focus_count}
        self.update()

    def fit(self) -> None:
        """Zoom so that the whole diagram is visible."""
        self.run_method("fit")

    def layout_elk(self) -> None:
        """Lay the diagram out with elkjs in the browser; the result arrives as ``layout``."""
        self.run_method("layoutElk")

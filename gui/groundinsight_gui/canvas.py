# groundinsight_gui/canvas.py
"""
The network canvas element: a Vue Flow drawing of the session's diagram.

The element only carries data across: the :class:`DiagramDTO
<groundinsight.api.DiagramDTO>` of the session, the colours of the chosen
colouring, the selected element and the active tool go to the browser as
props; what the user does there comes back as events (see
``network_canvas.js``) that :mod:`groundinsight_gui.plan` turns into
commands. The browser decides nothing about the network -- a dragged symbol
moves for good only once the session has applied the ``MoveNodes`` command
and the new diagram is sent back.

Vue Flow, its minimap, controls and background, and elkjs are served from
the vendored bundle in ``static/vueflow`` (no CDN at run time).
"""

import logging
from typing import Any, Dict, Optional

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
    "select": ("kind", "name"),
    "open": ("kind", "name"),
    "delete": ("kind", "name"),
    "layout": ("positions",),
}

CANVAS_CSS = """
.gi-canvas { width: 100%; height: 100%; outline: none; background: #fbfbfa; }
.gi-node { position: relative; border: 2px solid #1f2a36; box-sizing: border-box; }
.gi-node.gi-bus { border-radius: 50%; }
.gi-node.gi-source_bus { border-radius: 2px; }
.gi-node.gi-busbar { border-radius: 1px; border-width: 1px; }
.gi-node.gi-sel { box-shadow: 0 0 0 3px #ffb300; }
.gi-node.gi-off { opacity: 0.4; border-style: dashed; }
.gi-label { position: absolute; white-space: nowrap; font-size: 12px; color: #1f2a36;
            pointer-events: none; }
.gi-flash { position: absolute; left: -16px; top: -18px; font-size: 14px; color: #D55E00; }
.gi-infeed { position: absolute; left: 3px; top: -20px; font-size: 13px; color: #0072B2; }
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
.gi-edge-label { font-size: 11px; fill: #3b4652; pointer-events: none; }
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
    """

    def __init__(self, diagram: Dict[str, Any], *, colours: Optional[Dict[str, Any]] = None,
                 tool: str = "select") -> None:
        super().__init__()
        self._props["diagram"] = diagram
        self._props["colours"] = colours or {"nodes": {}, "edges": {}}
        self._props["tool"] = tool
        self._props["selected"] = None
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
    def selected(self) -> Optional[Dict[str, Any]]:
        """The selected element, ``{"kind", "name"}``, or ``None``."""
        return self._props["selected"]

    def show(self, diagram: Dict[str, Any], colours: Dict[str, Any]) -> None:
        """
        Show a new diagram and its colours.

        Parameters
        ----------
        diagram : dict
            ``DiagramDTO.model_dump()``.
        colours : dict
            ``{"nodes": ..., "edges": ...}``.
        """
        self._props["diagram"] = diagram
        self._props["colours"] = colours
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
        Mark an element as selected (``None`` clears).

        Parameters
        ----------
        kind : {"bus", "branch"} or None
            The element kind.
        name : str or None
            The element.
        """
        self._props["selected"] = {"kind": kind, "name": name} if kind and name else None
        self.update()

    def fit(self) -> None:
        """Zoom so that the whole diagram is visible."""
        self.run_method("fit")

    def layout_elk(self) -> None:
        """Lay the diagram out with elkjs in the browser; the result arrives as ``layout``."""
        self.run_method("layoutElk")

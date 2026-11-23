# groundinsight_gui/plan.py
"""
The network plan: palette, canvas, properties, element dialogs.

Every gesture on the canvas becomes a command of ``groundinsight.api``:

==========================  ==================================================
gesture                     command
==========================  ==================================================
click with *Bus*            ``PlaceBus`` (a bus of the palette's bus type)
click with *Source bus*     ``Batch``: ``PlaceBus`` + ``AddSource``
drag bus to bus, *Branch*   ``AddBranch`` (the palette's branch type, 1 km)
click a bus with *Fault*    ``AddFault`` (scaling 1 at every frequency)
drag a symbol               ``MoveNodes``
*Delete*                    ``RemoveBranch``, or for a bus a ``Batch`` that
                            removes its faults, sources and branches first;
                            for several selected elements one ``Batch``
dialog *Apply*              one ``Batch`` of every change (rename last)
auto layout                 ``AutoLayout`` (library, or positions from elkjs)
Ctrl/Cmd+C, then +V         one ``Batch``: a ``PlaceBus`` per copied bus (with
(or Ctrl/Cmd+D)             its own characteristic and its infeed), an
                            ``AddBranch`` per copied branch
right click                 the context menu: the gestures above for the
                            element under the pointer, ``UpdateBranch`` for
                            its switch, ``AddSource`` for an infeed
*Esc*                       no command: the selection tool, nothing selected
Ctrl/Cmd+A                  no command: every bus and branch selected
==========================  ==================================================

so a rejected gesture changes nothing and every accepted one is one undo
step. Several elements are selected with Shift, Ctrl or Cmd and a click, or
with Shift and a dragged frame; a selected group is moved and deleted as one. New elements get the first free name of their kind (``B1``, ``L1``,
``I1``, ``F1``, ...), which the dialog renames.
"""

import json
import logging
from typing import Any, Callable, Dict, List, Optional, Tuple

from nicegui import events, ui

from groundinsight.api import (
    AddBranch,
    AddFault,
    AddSource,
    AutoLayout,
    Batch,
    ClearCharacteristic,
    Command,
    ImportCharacteristic,
    MoveNodes,
    PlaceBus,
    ProjectError,
    RemoveBranch,
    RemoveBus,
    RemoveFault,
    RemoveSource,
    RenameBranch,
    RenameBus,
    RouteBranch,
    SetCharacteristic,
    SetColourScheme,
    UpdateBranch,
    UpdateBus,
    UpdateFault,
    UpdateSource,
    groundfield_available,
)

from .canvas import CANVAS_CSS, TOOLS, NetworkCanvas
from .catalogue import copy_name
from .colouring import MODES, RESULT_MODES, UTILISATION_MODES, Colouring, colour
from .formatting import DASH, format_number, format_scale, parse_number
from .i18n import column_label
from .studies import grid_options

logger = logging.getLogger(__name__)

__all__ = ["DEFAULT_BRANCH_LENGTH_KM", "DEFAULT_SOURCE_A", "PlanView", "free_name"]

#: Length of a newly drawn branch, in km; the dialog changes it.
DEFAULT_BRANCH_LENGTH_KM = 1.0

#: Current of a newly drawn infeed at every network frequency, in A.
DEFAULT_SOURCE_A = 1000.0

#: The context menus of the canvas: per group the entries ``(action, text
#: key, options)``. ``Mod`` is Ctrl or Cmd; ``when`` shows an entry only for
#: an element that is in (``"active"``) or out of service (``"inactive"``).
CONTEXT_MENUS: Dict[str, List[Tuple[str, str, Dict[str, Any]]]] = {
    "bus": [("open", "edit", {}), ("duplicate", "menu_duplicate", {"key": "Mod+D"}),
            ("fault", "menu_fault", {"rule": True}), ("source", "menu_source", {}),
            ("show_row", "menu_show_row", {"rule": True}),
            ("delete", "delete", {"danger": True, "rule": True, "key": "Del"})],
    "branch": [("open", "edit", {}), ("duplicate", "menu_duplicate", {"key": "Mod+D"}),
               ("open_switch", "menu_open_switch", {"rule": True, "when": "active"}),
               ("close_switch", "menu_close_switch", {"rule": True, "when": "inactive"}),
               ("show_row", "menu_show_row", {"rule": True}),
               ("delete", "delete", {"danger": True, "rule": True, "key": "Del"})],
    "group": [("duplicate", "menu_duplicate", {"key": "Mod+D"}),
              ("delete", "delete", {"danger": True, "rule": True, "key": "Del"})],
    "pane": [("place_bus", "menu_place_bus", {}), ("paste", "menu_paste", {"key": "Mod+V"}),
             ("select_all", "menu_select_all", {"rule": True, "key": "Mod+A"}), ("fit", "menu_fit", {})],
}

TOOL_ICONS = {"select": "near_me", "bus": "radio_button_unchecked", "source_bus": "crop_square",
              "branch": "timeline", "fault": "flash_on"}


def free_name(prefix: str, taken: List[str]) -> str:
    """
    The first name ``<prefix><n>`` (n = 1, 2, ...) not in ``taken``.

    Parameters
    ----------
    prefix : str
        ``"B"``, ``"L"``, ``"I"``, ``"F"``, ...
    taken : list of str
        Names in use.

    Returns
    -------
    str
        The name.

    Examples
    --------
    >>> free_name("B", ["B1", "B3"])
    'B2'
    """
    used = set(taken)
    n = 1
    while f"{prefix}{n}" in used:
        n += 1
    return f"{prefix}{n}"


class PlanView:
    """
    The plan tab of a :class:`~groundinsight_gui.app.ProjectPage`.

    Parameters
    ----------
    page : ProjectPage
        The page; its session, texts, fault selection and refresh.
    """

    def __init__(self, page: Any) -> None:
        self.page = page
        self.tool = "select"
        self.mode = "type"
        self.selection: List[Tuple[str, str]] = []
        self.bus_type: Optional[str] = None
        self.branch_type: Optional[str] = None
        self.canvas: Optional[NetworkCanvas] = None
        self.dialog_host: Optional[Any] = None
        self.type_selects: Dict[str, Any] = {}
        self.tool_toggle: Optional[Any] = None
        # What Ctrl/Cmd+C took: names, not values -- a paste copies the
        # elements as they are then, and leaves out what was deleted since.
        self.clipboard: List[Tuple[str, str]] = []

    # -- helpers ------------------------------------------------------------------

    @property
    def session(self) -> Any:
        """The page's session."""
        return self.page.session

    def t(self, key: str, **values: Any) -> str:
        """The text of ``key`` in the page's language."""
        return self.page.t(key, **values)

    @property
    def selected(self) -> Optional[Tuple[str, str]]:
        """The selected element ``(kind, name)``; ``None`` for none or several."""
        return self.selection[0] if len(self.selection) == 1 else None

    def apply(self, command: Command) -> bool:
        """
        Apply a command and refresh the page; report a rejection.

        Parameters
        ----------
        command : Command
            The command.

        Returns
        -------
        bool
            Whether it was applied.
        """
        try:
            report = self.session.apply(command)
        except ValueError as exc:  # CommandError is a ValueError
            ui.notify(f"{self.t('edit_rejected')}: {exc}", type="negative", multi_line=True)
            self.refresh()  # a dragged symbol goes back to its place
            return False
        self.page._notify_diagnostics(report.diagnostics)
        self.page._after_change()
        return True

    def _names(self, table: str) -> List[str]:
        """The names in an element table."""
        return [row["name"] for row in self.session.table(table).rows]

    def colouring(self) -> Colouring:
        """The colours of the current mode for the current diagram."""
        diagram = self.session.diagram()
        result = None
        if self.mode in RESULT_MODES and self.page.fault:
            result = self.session.result(self.page.fault)
        check = self.session.check() if self.mode == "diagnostics" else None
        assessment = None
        if self.mode in UTILISATION_MODES and result is not None:
            try:
                assessment = self.session.assessment(self.page.fault)
            except ProjectError:
                assessment = None
        return colour(self.mode, diagram, result=result, check=check, assessment=assessment)

    def _mode_of_diagram(self) -> str:
        """The colouring the project keeps (``Diagram.color_scheme``), or by type."""
        scheme = self.session.diagram_model.color_scheme
        return scheme if scheme in MODES else "type"

    # -- layout -------------------------------------------------------------------

    def build(self) -> None:
        """Palette, canvas with legend, properties."""
        ui.add_css(CANVAS_CSS)
        self.mode = self._mode_of_diagram()
        summary = self.session.summary()
        if self.bus_type not in summary.bus_types:
            self.bus_type = summary.bus_types[0] if summary.bus_types else None
        if self.branch_type not in summary.branch_types:
            self.branch_type = summary.branch_types[0] if summary.branch_types else None
        with ui.row().classes("w-full no-wrap gap-2 items-stretch"):
            self._palette(summary)
            with ui.column().classes("grow gap-1 min-w-0"):
                self.legend()
                colours = self.colouring()
                self.canvas = NetworkCanvas(self.session.diagram().model_dump(mode="json"),
                                            colours={"nodes": colours.nodes, "edges": colours.edges},
                                            tool=self.tool, menus=self._menus()).classes(
                    "w-full rounded border bg-white").style("height: 68vh").mark("canvas")
                for name, handler in (("place", self._on_place), ("move", self._on_move),
                                      ("connect", self._on_connect), ("fault", self._on_fault),
                                      ("select", self._on_select), ("select_many", self._on_select_many),
                                      ("open", self._on_open),
                                      ("delete", self._on_delete), ("layout", self._on_layout),
                                      ("key", self._on_key), ("menu", self._on_menu)):
                    self.canvas.on(name, lambda e, h=handler: h(e.args or {}))
            with ui.column().classes("w-72 gap-1"):
                self.properties()
        # Dialogs are created here: an event handler runs in the slot of the
        # element that fired it, and the canvas renders no slot.
        self.dialog_host = ui.element("div").mark("dialog-host")
        self._sync_selection()

    def _menus(self) -> Dict[str, List[Dict[str, Any]]]:
        """The context menus with their texts in the page's language."""
        def entry(action: str, key: str, options: Dict[str, Any]) -> Dict[str, Any]:
            out = {"action": action, "label": self.t(key), **options}
            if out.get("key") == "Del":
                out["key"] = self.t("key_delete")
            return out

        return {group: [entry(*item) for item in entries] for group, entries in CONTEXT_MENUS.items()}

    def _palette(self, summary: Any) -> None:
        """Tools and the types new elements get."""
        with ui.column().classes("w-56 gap-1 bg-white rounded border p-2").mark("palette"):
            ui.label(self.t("palette")).classes("text-xs uppercase tracking-wide text-gray-500")
            self.tool_toggle = ui.toggle({tool: self.t(f"tool_{tool}") for tool in TOOLS}, value=self.tool,
                                         on_change=lambda e: self.set_tool(e.value)).props(
                "dense no-caps spread").classes("flex-col w-full").mark("tools")
            self.type_selects = {}
            for kind in ("bus", "branch"):
                options = summary.bus_types if kind == "bus" else summary.branch_types
                with ui.row().classes("w-full no-wrap items-center gap-0"):
                    self.type_selects[kind] = ui.select(
                        options, value=getattr(self, f"{kind}_type"), label=self.t(f"new_{kind}_type"),
                        on_change=lambda e, k=kind: setattr(self, f"{k}_type", e.value)).props(
                        "dense outlined").classes("grow min-w-0").mark(f"new-{kind}-type")
                    ui.button(icon="add", on_click=lambda _, k=kind: self.page.types.open_library(
                        k, for_palette=True)).props(
                        f'flat dense round aria-label="{self.t("type_add_" + kind)}"').tooltip(
                        self.t("type_add_" + kind)).mark(f"palette-add-{kind}")
            self.tool_hint = ui.label(self.t(f"tool_hint_{self.tool}")).classes("text-xs text-gray-500").mark(
                "tool-hint")

    def set_tool(self, tool: str) -> None:
        """
        Switch the palette tool.

        Parameters
        ----------
        tool : str
            One of :data:`groundinsight_gui.canvas.TOOLS`.
        """
        self.tool = tool
        if self.canvas is not None:
            self.canvas.set_tool(tool)
            self.tool_hint.set_text(self.t(f"tool_hint_{tool}"))
            if self.tool_toggle.value != tool:
                self.tool_toggle.set_value(tool)  # the tool was not picked in the palette

    @ui.refreshable_method
    def legend(self) -> None:
        """Colouring mode and its legend."""
        colours = self.colouring()
        with ui.row().classes("w-full items-center gap-3 no-wrap").mark("legend"):
            ui.icon("palette").classes("text-gray-500")
            ui.label(self.t(f"colour_{self.mode}")).classes("text-sm font-medium").mark("colour-mode")
            if colours.scale is not None:
                low, high, unit = colours.scale
                low_text, high_text = format_scale(low, high, self.page.language)
                ui.label(f"{low_text} {unit}").classes("text-xs").mark("legend-min")
                ui.element("div").style(f"width: 160px; height: 10px; border-radius: 3px; "
                                        f"background: {colours.gradient}")
                ui.label(f"{high_text} {unit}").classes("text-xs").mark("legend-max")
            elif self.mode in RESULT_MODES:
                ui.label(self.t("legend_no_result")).classes("text-xs text-gray-500").mark("legend-empty")
            for entry in colours.entries:
                with ui.row().classes("items-center gap-1 no-wrap"):
                    ui.element("span").style(f"display:inline-block;width:12px;height:12px;"
                                             f"border-radius:50%;background:{entry.colour}")
                    ui.label(self.t(entry.label) if entry.key else entry.label).classes("text-xs")

    def set_mode(self, mode: str) -> None:
        """
        Switch the colouring.

        Parameters
        ----------
        mode : str
            One of :data:`groundinsight_gui.colouring.MODES`.
        """
        if mode != self.session.diagram_model.color_scheme:
            # Kept in the project, like every other edit (and undone like one).
            self.page.apply(SetColourScheme(color_scheme=mode))
        self.mode = mode
        self.refresh()

    def refresh(self) -> None:
        """Send the current diagram and colours to the canvas; redraw legend and properties."""
        if self.canvas is None or self.session is None:
            return
        self.mode = self._mode_of_diagram()
        self._refresh_type_selects()
        colours = self.colouring()
        self.canvas.show(self.session.diagram().model_dump(mode="json"),
                         {"nodes": colours.nodes, "edges": colours.edges})
        self.selection = self._existing(self.selection)
        self._sync_selection()
        self.legend.refresh()
        self.properties.refresh()

    def _refresh_type_selects(self) -> None:
        """Offer the catalogue's current types; keep the chosen one while it exists."""
        summary = self.session.summary()
        for kind, options in (("bus", summary.bus_types), ("branch", summary.branch_types)):
            chosen = getattr(self, f"{kind}_type")
            if chosen not in options:
                chosen = options[0] if options else None
                setattr(self, f"{kind}_type", chosen)
            select = self.type_selects.get(kind)
            if select is not None:
                # set_options reports the interim value through on_change: restore the choice after it.
                select.set_options(list(options), value=chosen)
                setattr(self, f"{kind}_type", chosen)

    # -- selection and properties -------------------------------------------------

    def select(self, kind: Optional[str], name: Optional[str], *, from_table: bool = False) -> None:
        """
        Select one element in the plan (and, unless it came from there, in its table).

        Parameters
        ----------
        kind : {"bus", "branch"} or None
            The element kind; ``None`` clears.
        name : str or None
            The element.
        from_table : bool
            The selection came from a table row.
        """
        self.select_items([(kind, name)] if kind and name else [], from_table=from_table)

    def select_items(self, items: List[Tuple[str, str]], *, from_table: bool = False) -> None:
        """
        Select several elements in the plan.

        Parameters
        ----------
        items : list of (kind, name)
            The elements, in the order they were picked; elements the network
            does not have are left out.
        from_table : bool
            The selection came from a table row.
        """
        self.selection = self._existing(items)
        self._sync_selection()
        self.properties.refresh()
        if self.selected is not None and not from_table:
            kind, name = self.selected
            self.page.select_row({"bus": "buses", "branch": "branches"}[kind], name)

    def _existing(self, items: List[Tuple[str, str]]) -> List[Tuple[str, str]]:
        """The items that are buses or branches of the network, without repeats."""
        names = {"bus": set(self._names("buses")), "branch": set(self._names("branches"))}
        out: List[Tuple[str, str]] = []
        for kind, name in items:
            if name in names.get(kind, ()) and (kind, name) not in out:
                out.append((kind, name))
        return out

    def _sync_selection(self) -> None:
        """Show the selection on the canvas."""
        if self.canvas is not None:
            self.canvas.select_items(self.selection)

    @ui.refreshable_method
    def properties(self) -> None:
        """The fields of the selected element, with edit and delete."""
        with ui.card().classes("w-full p-3 gap-1").props("flat bordered").mark("properties"):
            if not self.selection:
                ui.label(self.t("properties")).classes("text-xs uppercase tracking-wide text-gray-500")
                ui.label(self.t("nothing_selected_plan")).classes("text-sm text-gray-500")
                return
            if len(self.selection) > 1:
                self._group_properties()
                return
            kind, name = self.selected
            table = "buses" if kind == "bus" else "branches"
            row = next((r for r in self.session.table(table).rows if r["name"] == name), None)
            ui.label(f"{self.t('kind_' + kind)} {name}").classes("text-base font-medium").mark("properties-title")
            for key, value in (row or {}).items():
                if key in ("name", "characteristic"):
                    continue
                if isinstance(value, bool):
                    text = self.t("yes") if value else self.t("no")
                elif isinstance(value, (int, float)):
                    text = format_number(value, self.page.language, 6)
                else:
                    text = DASH if value is None else value
                with ui.row().classes("w-full justify-between no-wrap gap-2"):
                    ui.label(column_label(self.page.language, key)).classes("text-xs text-gray-500")
                    ui.label(str(text)).classes("text-xs text-right truncate").mark(f"prop-{key}")
            with ui.row().classes("gap-1 mt-2"):
                ui.button(self.t("edit"), icon="edit", on_click=lambda: self.open_dialog(kind, name)).props(
                    "dense no-caps").mark("properties-edit")
                ui.button(self.t("delete"), icon="delete", on_click=lambda: self._on_delete(
                    {"kind": kind, "name": name})).props("dense flat no-caps color=negative").mark(
                    "properties-delete")

    def _group_properties(self) -> None:
        """What is selected when it is more than one element, and the group's delete."""
        buses = sum(1 for kind, _ in self.selection if kind == "bus")
        branches = len(self.selection) - buses
        ui.label(self.t("selection_count", n=len(self.selection))).classes("text-base font-medium").mark(
            "properties-count")
        parts = [f"1 {self.t('kind_bus')}" if buses == 1 else self.t("selection_buses", n=buses) if buses else "",
                 f"1 {self.t('kind_branch')}" if branches == 1 else self.t("selection_branches", n=branches)
                 if branches else ""]
        ui.label(", ".join(p for p in parts if p)).classes("text-xs text-gray-500")
        ui.label(self.t("selection_hint")).classes("text-xs text-gray-500")
        with ui.row().classes("gap-1 mt-2"):
            ui.button(self.t("delete"), icon="delete", on_click=lambda: self._on_delete({})).props(
                "dense flat no-caps color=negative").mark("properties-delete")

    # -- canvas events -----------------------------------------------------------

    def _on_place(self, args: Dict[str, Any]) -> None:
        """Place a bus (with an infeed for the source-bus tool)."""
        if self.bus_type is None:
            ui.notify(self.t("no_bus_type"), type="warning")
            return
        name = free_name("B", self._names("buses"))
        symbol = args.get("symbol") if args.get("symbol") in ("bus", "source_bus") else "bus"
        place = PlaceBus(name=name, type=self.bus_type, x=float(args["x"]), y=float(args["y"]), symbol=symbol)
        if symbol == "source_bus":
            frequencies = self.session.summary().frequencies
            source = AddSource(name=free_name("I", self._names("sources")), bus=name,
                               values={f: DEFAULT_SOURCE_A for f in frequencies})
            command: Command = Batch(title=self.t("draw_source_bus", name=name), commands=[place, source])
        else:
            command = place
        if self.apply(command):
            self.select("bus", name)

    def _on_move(self, args: Dict[str, Any]) -> None:
        """Store the new place of dragged symbols."""
        positions = {name: (float(p[0]), float(p[1])) for name, p in (args.get("positions") or {}).items()}
        if positions:
            self.apply(MoveNodes(positions=positions))

    def _on_connect(self, args: Dict[str, Any]) -> None:
        """Draw a branch between two buses."""
        if self.branch_type is None:
            ui.notify(self.t("no_branch_type"), type="warning")
            return
        name = free_name("L", self._names("branches"))
        if self.apply(AddBranch(name=name, type=self.branch_type, from_bus=args["from_bus"],
                                to_bus=args["to_bus"], length=DEFAULT_BRANCH_LENGTH_KM)):
            self.select("branch", name)

    def _on_fault(self, args: Dict[str, Any]) -> None:
        """Put a fault on a bus."""
        frequencies = self.session.summary().frequencies
        self.apply(AddFault(name=free_name("F", self._names("faults")), bus=args["bus"],
                            scalings={f: 1.0 for f in frequencies}))

    def _on_select(self, args: Dict[str, Any]) -> None:
        """Select what was clicked (or nothing); with a modifier key, add it or take it away."""
        kind, name = args.get("kind"), args.get("name")
        if not (kind and name):
            self.select_items([])
        elif not args.get("add"):
            self.select(kind, name)
        elif (kind, name) in self.selection:
            self.select_items([item for item in self.selection if item != (kind, name)])
        else:
            self.select_items([*self.selection, (kind, name)])

    def _on_select_many(self, args: Dict[str, Any]) -> None:
        """Select what a dragged frame holds."""
        self.select_items(self._items(args))

    @staticmethod
    def _items(args: Dict[str, Any]) -> List[Tuple[str, str]]:
        """The ``(kind, name)`` pairs an event names in its ``items``."""
        return [(str(item.get("kind")), str(item.get("name"))) for item in (args.get("items") or [])
                if isinstance(item, dict) and item.get("kind") and item.get("name")]

    def duplication(self, items: List[Tuple[str, str]],
                    at: Optional[Tuple[float, float]] = None) -> Tuple[Optional[Command], List[Tuple[str, str]]]:
        """
        The command that copies elements of the plan, and what it creates.

        A bus is copied with its fields, its symbol, its own earthing
        characteristic and its infeed (a source bus without a source would
        feed nothing); a fault is not copied, a fault location is set on
        purpose. A branch is copied with its fields; each end goes to the
        copy of its bus if that bus is copied too, else it stays -- so a
        branch copied alone is a parallel branch. Copies are named
        ``<name> Kopie``, ``<name> Kopie 2``, ... (``copy`` in English).

        Parameters
        ----------
        items : list of (kind, name)
            Buses and branches.
        at : tuple of float, optional
            Where the centre of the copied buses goes (diagram coordinates);
            default two raster steps right of and below the originals.

        Returns
        -------
        command : Command or None
            One command (a ``Batch`` for more than one step); ``None`` for no
            element.
        created : list of (kind, name)
            The copies, buses first.
        """
        items = self._existing(items)
        if not items:
            return None, []
        suffix = self.t("copy_suffix")
        diagram = self.session.diagram()
        nodes = {node.name: node for node in diagram.nodes}
        edges = {edge.name: edge for edge in diagram.edges}
        bus_rows = {row["name"]: row for row in self.session.table("buses").rows}
        branch_rows = {row["name"]: row for row in self.session.table("branches").rows}
        buses = [name for kind, name in items if kind == "bus"]
        grid = float(diagram.grid_size or 20)
        dx = dy = 2 * grid
        if at is not None and buses:
            dx = round((at[0] - sum(nodes[b].x for b in buses) / len(buses)) / grid) * grid
            dy = round((at[1] - sum(nodes[b].y for b in buses) / len(buses)) / grid) * grid
        taken = {"bus": list(bus_rows), "branch": list(branch_rows), "source": self._names("sources")}

        def name_of(kind: str, original: str) -> str:
            copy = copy_name(original, taken[kind], suffix)
            taken[kind].append(copy)
            return copy

        commands: List[Command] = []
        copies: Dict[str, str] = {}
        for name in buses:
            row, node = bus_rows[name], nodes[name]
            copies[name] = name_of("bus", name)
            commands.append(PlaceBus(
                name=copies[name], type=row["type"], specific_earth_resistance=row["specific_earth_resistance"],
                active=row["active"], description=row.get("description"), x=node.x + dx, y=node.y + dy,
                symbol=node.symbol, label_offset=node.label_offset, length=node.length))
            own = row.get("characteristic")
            if own:
                commands.append(SetCharacteristic(target="bus", name=copies[name],
                                                  characteristic=json.loads(own) if isinstance(own, str) else own))
        commands += self._source_copies(copies, name_of)
        created = [("bus", copy) for copy in copies.values()]
        for name in (n for kind, n in items if kind == "branch"):
            row = branch_rows[name]
            copy = name_of("branch", name)
            commands.append(AddBranch(
                name=copy, type=row["type"], from_bus=copies.get(row["from_bus"], row["from_bus"]),
                to_bus=copies.get(row["to_bus"], row["to_bus"]), length=row["length"],
                specific_earth_resistance=row["specific_earth_resistance"],
                parallel_coefficient=row["parallel_coefficient"], active=row["active"],
                description=row.get("description")))
            waypoints = edges[name].waypoints if name in edges else []
            if waypoints and row["from_bus"] in copies and row["to_bus"] in copies:
                commands.append(RouteBranch(name=copy, waypoints=[(x + dx, y + dy) for x, y in waypoints]))
            created.append(("branch", copy))
        if len(commands) == 1:
            return commands[0], created
        return Batch(title=self.t("duplicate_elements", n=len(items)), commands=commands), created

    def _source_copies(self, copies: Dict[str, str], name_of: Callable[[str, str], str]) -> List[Command]:
        """The sources at copied buses, added again at the copies."""
        quantities = {"current": "values", "voltage": "voltage", "impedance": "source_impedance"}
        commands: List[Command] = []
        for row in self.session.table("sources").rows:
            if row["bus"] not in copies:
                continue
            phasors: Dict[str, Dict[float, Dict[str, float]]] = {}
            for value in self.session.table("source_values").rows:
                if value["source"] == row["name"]:
                    phasors.setdefault(quantities[value["quantity"]], {})[value["frequency_Hz"]] = {
                        "real": value["real"], "imag": value["imag"]}
            commands.append(AddSource(
                name=name_of("source", row["name"]), bus=copies[row["bus"]], source_type=row["source_type"],
                i_k_a=row["i_k_a"], r_to_x=row["r_to_x"], kappa=row["kappa"], description=row.get("description"),
                **phasors))
        return commands

    def duplicate(self, items: List[Tuple[str, str]], at: Optional[Tuple[float, float]] = None) -> bool:
        """
        Copy elements of the plan and select the copies.

        Parameters
        ----------
        items : list of (kind, name)
            Buses and branches.
        at : tuple of float, optional
            Where the centre of the copied buses goes; see :meth:`duplication`.

        Returns
        -------
        bool
            Whether anything was copied.
        """
        command, created = self.duplication(items, at)
        if command is None or not self.apply(command):
            return False
        self.select_items(created)
        return True

    def _on_key(self, args: Dict[str, Any]) -> None:
        """A key of the canvas: Escape, select all, copy, paste, duplicate."""
        action = args.get("action")
        at = (float(args["x"]), float(args["y"])) if args.get("x") is not None and args.get("y") is not None \
            else None
        if action == "copy":
            self.clipboard = list(self.selection)
        elif action == "paste":
            self.duplicate(self.clipboard, at)
        elif action == "duplicate":
            self.duplicate(self.selection, at)
        elif action == "escape":
            self.set_tool("select")
            self.select_items([])
        elif action == "select_all":
            self.select_items([("bus", name) for name in self._names("buses")]
                              + [("branch", name) for name in self._names("branches")])

    def _on_menu(self, args: Dict[str, Any]) -> None:
        """An entry of a context menu was chosen."""
        action, kind, name = args.get("action"), args.get("kind"), args.get("name")
        at = (float(args["x"]), float(args["y"])) if args.get("x") is not None and args.get("y") is not None \
            else None
        items = [(kind, name)] if kind in ("bus", "branch") and name else list(self.selection)
        if action == "open" and len(items) == 1:
            self._on_open({"kind": kind, "name": name})
        elif action == "duplicate":
            self.duplicate(items)
        elif action == "delete":
            self._on_delete({"items": [{"kind": k, "name": n} for k, n in items]})
        elif action == "fault" and kind == "bus":
            self._on_fault({"bus": name})
        elif action == "source" and kind == "bus":
            self._add_source(name)
        elif action in ("open_switch", "close_switch") and kind == "branch":
            self.apply(UpdateBranch(name=name, fields={"active": action == "close_switch"}))
        elif action == "show_row" and len(items) == 1:
            self.page.show_row(kind, name)
        elif action == "place_bus" and at is not None:
            self._on_place({"x": at[0], "y": at[1], "symbol": "bus"})
        elif action == "paste":
            self.duplicate(self.clipboard, at)
        elif action == "select_all":
            self._on_key({"action": "select_all"})

    def _add_source(self, bus: str) -> None:
        """An infeed at a bus (1000 A at every frequency), unless it has one."""
        if any(row["bus"] == bus for row in self.session.table("sources").rows):
            ui.notify(self.t("bus_has_source", name=bus), type="info")
            return
        frequencies = self.session.summary().frequencies
        self.apply(AddSource(name=free_name("I", self._names("sources")), bus=bus,
                             values={f: DEFAULT_SOURCE_A for f in frequencies}))

    def _on_open(self, args: Dict[str, Any]) -> None:
        """Open the dialog of a double-clicked element."""
        self.select(args.get("kind"), args.get("name"))
        self.open_dialog(args["kind"], args["name"])

    def removal(self, kind: str, name: str) -> Command:
        """
        The command that deletes an element from the plan.

        Parameters
        ----------
        kind : {"bus", "branch"}
            The element kind.
        name : str
            The element.

        Returns
        -------
        Command
            ``RemoveBranch``; for a bus a ``Batch`` removing what refers to it
            first (faults, sources, branches), so the delete key works on a
            connected bus as one undo step.
        """
        if kind == "branch":
            return RemoveBranch(name=name)
        commands: List[Command] = [RemoveFault(name=r["name"]) for r in self.session.table("faults").rows
                                   if r["bus"] == name]
        commands += [RemoveSource(name=r["name"]) for r in self.session.table("sources").rows if r["bus"] == name]
        commands += [RemoveBranch(name=r["name"]) for r in self.session.table("branches").rows
                     if name in (r["from_bus"], r["to_bus"])]
        if not commands:
            return RemoveBus(name=name)
        return Batch(title=self.t("delete_bus_with", name=name), commands=commands + [RemoveBus(name=name)])

    def removal_of(self, items: List[Tuple[str, str]]) -> Optional[Command]:
        """
        The command that deletes several elements from the plan.

        Parameters
        ----------
        items : list of (kind, name)
            Buses and branches.

        Returns
        -------
        Command or None
            For one element :meth:`removal`; for several one ``Batch`` that
            removes the faults and sources at the buses, then every branch
            named or ending at one of the buses (once), then the buses.
            ``None`` for no element.
        """
        items = self._existing(items)
        if not items:
            return None
        if len(items) == 1:
            return self.removal(*items[0])
        buses = [name for kind, name in items if kind == "bus"]
        branches = [name for kind, name in items if kind == "branch"]
        branches += [r["name"] for r in self.session.table("branches").rows
                     if r["name"] not in branches and (r["from_bus"] in buses or r["to_bus"] in buses)]
        commands: List[Command] = [RemoveFault(name=r["name"]) for r in self.session.table("faults").rows
                                   if r["bus"] in buses]
        commands += [RemoveSource(name=r["name"]) for r in self.session.table("sources").rows if r["bus"] in buses]
        commands += [RemoveBranch(name=name) for name in branches]
        commands += [RemoveBus(name=name) for name in buses]
        return Batch(title=self.t("delete_elements", n=len(items)), commands=commands)

    def _on_delete(self, args: Dict[str, Any]) -> None:
        """Delete the elements the event names; without any, the selection."""
        items = self._items(args)
        if not items and args.get("kind") and args.get("name"):
            items = [(args["kind"], args["name"])]
        command = self.removal_of(items or self.selection)
        if command is not None and self.apply(command):
            self.select_items([])

    def _on_layout(self, args: Dict[str, Any]) -> None:
        """Take the layout elkjs computed in the browser."""
        positions = {name: (float(p[0]), float(p[1])) for name, p in (args.get("positions") or {}).items()}
        if self.apply(AutoLayout(positions=positions, routes={})):
            self.canvas.fit()

    def auto_layout(self, how: str) -> None:
        """
        Lay the plan out.

        Parameters
        ----------
        how : {"library", "unplaced", "elk"}
            groundinsight's layered layout of everything, of the unplaced
            elements only, or elkjs in the browser.
        """
        if how == "elk":
            self.canvas.layout_elk()
            return
        if self.apply(AutoLayout(only_unplaced=how == "unplaced")):
            self.canvas.fit()

    # -- dialogs ------------------------------------------------------------------

    def open_dialog(self, kind: str, name: str) -> None:
        """
        The element dialog with its tabs.

        Parameters
        ----------
        kind : {"bus", "branch"}
            The element kind.
        name : str
            The element.
        """
        if kind == "bus":
            BusDialog(self, name).open()
        elif kind == "branch":
            BranchDialog(self, name).open()


class _ElementDialog:
    """Base of the element dialogs: a dialog with tabs and an Apply button."""

    TABS: Tuple[str, ...] = ()
    KIND = ""

    def __init__(self, plan: PlanView, name: str) -> None:
        self.plan = plan
        self.name = name
        self.inputs: Dict[str, Any] = {}

    def t(self, key: str, **values: Any) -> str:
        """The text of ``key`` in the page's language."""
        return self.plan.t(key, **values)

    def row(self, table: str) -> Dict[str, Any]:
        """The element's row in a table."""
        return next(r for r in self.plan.session.table(table).rows if r["name"] == self.name)

    def open(self) -> None:
        """Build and open the dialog (in the plan's dialog host; deleted when closed)."""
        with self.plan.dialog_host, ui.dialog() as self.dialog, ui.card().classes(
                "w-[40rem] max-w-full").mark("element-dialog"):
            ui.label(self.title()).classes("text-base font-medium")
            with ui.tabs().classes("w-full") as tabs:
                for tab in self.TABS:
                    ui.tab(tab, label=self.t(f"dialog_{tab}")).mark(f"dialog-tab-{tab}")
            with ui.tab_panels(tabs, value=self.TABS[0]).classes("w-full"):
                for tab in self.TABS:
                    with ui.tab_panel(tab):
                        getattr(self, f"_tab_{tab}")()
            with ui.row().classes("w-full justify-end gap-2"):
                ui.button(self.t("cancel_dialog"), on_click=self.dialog.close).props("flat no-caps")
                ui.button(self.t("apply"), on_click=self.submit).props("no-caps").mark("dialog-apply")
        self.dialog.on_value_change(lambda e: None if e.value else self.dialog.delete())
        self.dialog.open()

    def title(self) -> str:
        """The dialog title."""
        raise NotImplementedError

    def commands(self) -> List[Command]:
        """The commands of the changed fields."""
        raise NotImplementedError

    def submit(self) -> None:
        """Apply the changes as one undo step and close."""
        try:
            commands = self.commands()
        except ValueError as exc:
            ui.notify(f"{self.t('edit_rejected')}: {exc}", type="negative", multi_line=True)
            return
        if commands:
            command = commands[0] if len(commands) == 1 else Batch(
                title=self.t("edit_element", name=self.name), commands=commands)
            renamed = next((c for c in commands if isinstance(c, (RenameBus, RenameBranch))), None)
            if not self.plan.apply(command):
                return
            if renamed is not None:
                self.plan.select(self.KIND, renamed.new_name)
        self.dialog.close()

    def changed(self, row: Dict[str, Any], fields: Dict[str, Callable[[Any], Any]]) -> Dict[str, Any]:
        """
        The fields whose input differs from the row.

        Parameters
        ----------
        row : dict
            The current values.
        fields : dict of str to callable
            Field name to the reader of its input value.

        Returns
        -------
        dict
            Field name to new value.
        """
        out = {}
        for field, read in fields.items():
            value = read(self.inputs[field].value)
            if value != row.get(field):
                out[field] = value
        return out


def _number(value: Any) -> Optional[float]:
    """A number input as float; empty is ``None``."""
    if value is None or (isinstance(value, str) and not value.strip()):
        return None
    return parse_number(value)


def _text(value: Any) -> Optional[str]:
    """A text input; empty is ``None``."""
    return str(value) if value is not None and str(value).strip() else None


class BusDialog(_ElementDialog):
    """The bus dialog: general, source, fault, earthing characteristic."""

    TABS = ("general", "source", "fault", "characteristic")
    KIND = "bus"

    def title(self) -> str:
        """``Bus <name>``."""
        return f"{self.t('kind_bus')} {self.name}"

    def _tab_general(self) -> None:
        row = self.row("buses")
        summary = self.plan.session.summary()
        self.inputs["name"] = ui.input(self.t("name"), value=self.name).mark("dlg-name")
        self.inputs["type"] = ui.select(summary.bus_types, value=row["type"], label=self.t("type")).mark("dlg-type")
        self.inputs["specific_earth_resistance"] = ui.input(
            "ρ / Ω·m", value=format_number(row["specific_earth_resistance"], self.plan.page.language, 12)).mark(
            "dlg-rho")
        self.inputs["active"] = ui.switch(self.t("active"), value=row["active"]).mark("dlg-active")
        self.inputs["description"] = ui.input(self.t("description"), value=row.get("description") or "")

    def _sources(self) -> List[Dict[str, Any]]:
        return [r for r in self.plan.session.table("sources").rows if r["bus"] == self.name]

    def _source_values(self, source: str) -> Dict[float, Dict[str, float]]:
        """The current phasors of a source, by frequency."""
        return {r["frequency_Hz"]: {"real": r["real"], "imag": r["imag"]}
                for r in self.plan.session.table("source_values").rows
                if r["source"] == source and r["quantity"] == "current"}

    def _tab_source(self) -> None:
        sources = self._sources()
        values = self._source_values(sources[0]["name"]) if sources else {}
        self.inputs["has_source"] = ui.switch(self.t("source_here"), value=bool(sources)).mark("dlg-source")
        ui.label(self.t("source_values_hint")).classes("text-xs text-gray-500")
        self.source_inputs: Dict[float, Tuple[Any, Any]] = {}
        for f in self.plan.session.summary().frequencies:
            row = values.get(f, {})
            with ui.row().classes("items-center gap-2 no-wrap"):
                ui.label(f"{format_number(f, self.plan.page.language, 6)} Hz").classes("w-20 text-sm")
                default = 0.0 if sources else DEFAULT_SOURCE_A
                real = ui.input("Re / A", value=format_number(row.get("real", default), self.plan.page.language, 12))
                imag = ui.input("Im / A", value=format_number(row.get("imag", 0.0), self.plan.page.language, 12))
                self.source_inputs[f] = (real, imag)

    def _faults(self) -> List[Dict[str, Any]]:
        return [r for r in self.plan.session.table("faults").rows if r["bus"] == self.name]

    def _scalings(self, fault: str) -> Dict[float, float]:
        """The scalings of a fault, by frequency (empty: 1.0 everywhere)."""
        return {r["frequency_Hz"]: r["scaling"] for r in self.plan.session.table("fault_scalings").rows
                if r["fault"] == fault}

    def _tab_fault(self) -> None:
        faults = self._faults()
        language = self.plan.page.language
        self.inputs["has_fault"] = ui.switch(self.t("fault_here"), value=bool(faults)).mark("dlg-fault")
        if faults:
            ui.label(", ".join(r["name"] for r in faults)).classes("text-sm text-gray-600")
        scalings = self._scalings(faults[0]["name"]) if faults else {}
        t_k_s = faults[0]["t_k_s"] if faults else None
        self.inputs["t_k_s"] = ui.input(column_label(language, "t_k_s"),
                                        value="" if t_k_s is None else format_number(t_k_s, language, 12)).mark(
            "dlg-t-k")
        ui.label(self.t("fault_scalings_hint")).classes("text-xs text-gray-500")
        self.scaling_inputs: Dict[float, Any] = {}
        for f in self.plan.session.summary().frequencies:
            with ui.row().classes("items-center gap-2 no-wrap"):
                ui.label(f"{format_number(f, language, 6)} Hz").classes("w-20 text-sm")
                self.scaling_inputs[f] = ui.input(column_label(language, "scaling"), value=format_number(
                    scalings.get(f, 1.0), language, 12)).mark(f"dlg-scaling-{format_number(f, 'en', 6)}")

    def _tab_characteristic(self) -> None:
        self.characteristic_view()

    @ui.refreshable_method
    def characteristic_view(self) -> None:
        """Where the characteristic comes from, its table, import and clear."""
        dto = self.plan.session.characteristic("bus", self.name)
        if dto is None:
            ui.label(self.t("char_none")).classes("text-sm").mark("char-origin")
        else:
            origin = self.t("char_own") if dto.origin == "bus" else self.t("char_type")
            source = self.t(f"char_source_{dto.impedance_source}")
            ui.label(f"{origin}: {dto.name or DASH} · {self.t('char_source')} {source}").classes(
                "text-sm").mark("char-origin")
            if dto.extrapolated:
                ui.label(self.t("char_extrapolated")).classes("text-xs text-amber-800")
            language = self.plan.page.language
            ui.aggrid(grid_options(dto.table, language, lambda lang: "de-DE" if lang == "de" else "en-US"),
                      theme="quartz").classes("h-48").mark("char-table")
        with ui.row().classes("gap-2 items-center"):
            ui.upload(label=self.t("char_import"), auto_upload=True, on_upload=self._import).props(
                "accept=.json flat dense").classes("w-64").mark("char-upload")
            compute = ui.button(self.t("char_compute"), icon="calculate", on_click=self._compute).props(
                "dense no-caps").mark("char-compute")
            if not groundfield_available():
                compute.disable()
                compute.tooltip(self.t("char_needs_groundfield"))
            if dto is not None and dto.origin == "bus":
                ui.button(self.t("char_clear"), on_click=self._clear).props("flat dense no-caps").mark("char-clear")

    async def _import(self, event: events.UploadEventArguments) -> None:
        """Attach the uploaded groundfield BusType document to this bus."""
        try:
            document = json.loads(await event.file.text())
        except ValueError as exc:
            ui.notify(f"{self.t('edit_rejected')}: {exc}", type="negative")
            return
        if self.plan.apply(ImportCharacteristic(target="bus", name=self.name, document=document)):
            self.characteristic_view.refresh()

    def _compute(self) -> None:
        """Compute the characteristic with groundfield as a job (attached when it ends)."""
        from .studies import StudyDialog

        self.dialog.close()
        StudyDialog(self.plan.page, "characterize", owner=self.name).open()

    def _clear(self) -> None:
        """Remove the bus's own characteristic."""
        if self.plan.apply(ClearCharacteristic(target="bus", name=self.name)):
            self.characteristic_view.refresh()

    def commands(self) -> List[Command]:
        """Field updates, source and fault, then the rename."""
        row = self.row("buses")
        commands: List[Command] = []
        fields = self.changed(row, {"type": lambda v: v, "specific_earth_resistance": _number,
                                    "active": bool, "description": _text})
        if fields:
            commands.append(UpdateBus(name=self.name, fields=fields))
        commands += self._source_commands()
        commands += self._fault_commands()
        new_name = (self.inputs["name"].value or "").strip()
        if new_name != self.name:
            commands.append(RenameBus(name=self.name, new_name=new_name))
        return commands

    def _source_commands(self) -> List[Command]:
        sources = self._sources()
        wanted = bool(self.inputs["has_source"].value)
        if not wanted:
            return [RemoveSource(name=r["name"]) for r in sources]
        values = {f: {"real": _number(re.value) or 0.0, "imag": _number(im.value) or 0.0}
                  for f, (re, im) in self.source_inputs.items()}
        if not sources:
            return [AddSource(name=free_name("I", self.plan._names("sources")), bus=self.name, values=values)]
        if self._source_values(sources[0]["name"]) == values:
            return []
        return [UpdateSource(name=sources[0]["name"], fields={"values": values})]

    def _fault_commands(self) -> List[Command]:
        faults = self._faults()
        wanted = bool(self.inputs["has_fault"].value)
        if not wanted:
            return [RemoveFault(name=r["name"]) for r in faults]
        scalings = {f: _number(box.value) for f, box in self.scaling_inputs.items()}
        scalings = {f: 1.0 if v is None else v for f, v in scalings.items()}
        t_k_s = _number(self.inputs["t_k_s"].value)
        if not faults:
            return [AddFault(name=free_name("F", self.plan._names("faults")), bus=self.name,
                             scalings=scalings, t_k_s=t_k_s)]
        fields: Dict[str, Any] = {}
        if self._scalings(faults[0]["name"]) != scalings:
            fields["scalings"] = scalings
        if t_k_s != faults[0]["t_k_s"]:
            fields["t_k_s"] = t_k_s
        return [UpdateFault(name=faults[0]["name"], fields=fields)] if fields else []


class BranchDialog(_ElementDialog):
    """The branch dialog: general, type parameters, switch state."""

    TABS = ("general", "type_parameters", "switch")
    KIND = "branch"

    def title(self) -> str:
        """``Branch <name>``."""
        return f"{self.t('kind_branch')} {self.name}"

    def _tab_general(self) -> None:
        row = self.row("branches")
        summary = self.plan.session.summary()
        buses = self.plan._names("buses")
        language = self.plan.page.language
        self.inputs["name"] = ui.input(self.t("name"), value=self.name).mark("dlg-name")
        self.inputs["type"] = ui.select(summary.branch_types, value=row["type"], label=self.t("type")).mark(
            "dlg-type")
        self.inputs["length"] = ui.input(self.t("length_km"), value=format_number(row["length"], language, 12)).mark(
            "dlg-length")
        self.inputs["from_bus"] = ui.select(buses, value=row["from_bus"], label=self.t("from_bus")).mark("dlg-from")
        self.inputs["to_bus"] = ui.select(buses, value=row["to_bus"], label=self.t("to_bus")).mark("dlg-to")
        self.inputs["description"] = ui.input(self.t("description"), value=row.get("description") or "")

    def _tab_type_parameters(self) -> None:
        row = self.row("branches")
        type_row = next(r for r in self.plan.session.table("branch_types").rows if r["name"] == row["type"])
        for key, value in type_row.items():
            if value is None or key == "name":
                continue
            with ui.row().classes("w-full justify-between no-wrap gap-3"):
                ui.label(column_label(self.plan.page.language, key)).classes("text-xs text-gray-500")
                ui.label(str(value)).classes("text-xs font-mono text-right break-all").mark(f"type-{key}")

    def _tab_switch(self) -> None:
        row = self.row("branches")
        self.inputs["active"] = ui.switch(self.t("switch_closed"), value=row["active"]).mark("dlg-active")
        ui.label(self.t("switch_hint")).classes("text-xs text-gray-500")

    def commands(self) -> List[Command]:
        """Field updates, then the rename."""
        row = self.row("branches")
        commands: List[Command] = []
        fields = self.changed(row, {"type": lambda v: v, "length": _number, "from_bus": lambda v: v,
                                    "to_bus": lambda v: v, "active": bool, "description": _text})
        if fields:
            commands.append(UpdateBranch(name=self.name, fields=fields))
        new_name = (self.inputs["name"].value or "").strip()
        if new_name != self.name:
            commands.append(RenameBranch(name=self.name, new_name=new_name))
        return commands

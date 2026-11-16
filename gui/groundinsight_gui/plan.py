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
                            removes its faults, sources and branches first
dialog *Apply*              one ``Batch`` of every change (rename last)
auto layout                 ``AutoLayout`` (library, or positions from elkjs)
==========================  ==================================================

so a rejected gesture changes nothing and every accepted one is one undo
step. New elements get the first free name of their kind (``B1``, ``L1``,
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
    RemoveBranch,
    RemoveBus,
    RemoveFault,
    RemoveSource,
    RenameBranch,
    RenameBus,
    SetColourScheme,
    UpdateBranch,
    UpdateBus,
    UpdateSource,
    groundfield_available,
)

from .canvas import CANVAS_CSS, TOOLS, NetworkCanvas
from .colouring import MODES, RESULT_MODES, Colouring, colour
from .formatting import DASH, format_number, format_scale, parse_number
from .i18n import column_label
from .studies import grid_options

logger = logging.getLogger(__name__)

__all__ = ["DEFAULT_BRANCH_LENGTH_KM", "DEFAULT_SOURCE_A", "PlanView", "free_name"]

#: Length of a newly drawn branch, in km; the dialog changes it.
DEFAULT_BRANCH_LENGTH_KM = 1.0

#: Current of a newly drawn infeed at every network frequency, in A.
DEFAULT_SOURCE_A = 1000.0

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
        self.selected: Optional[Tuple[str, str]] = None
        self.bus_type: Optional[str] = None
        self.branch_type: Optional[str] = None
        self.canvas: Optional[NetworkCanvas] = None
        self.dialog_host: Optional[Any] = None
        self.type_selects: Dict[str, Any] = {}

    # -- helpers ------------------------------------------------------------------

    @property
    def session(self) -> Any:
        """The page's session."""
        return self.page.session

    def t(self, key: str, **values: Any) -> str:
        """The text of ``key`` in the page's language."""
        return self.page.t(key, **values)

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
        study = self.page.study
        touch = study.tables.get("touch") if study is not None and study.study == "touch" else None
        return colour(self.mode, diagram, result=result, check=check, touch=touch)

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
                                            tool=self.tool).classes(
                    "w-full rounded border bg-white").style("height: 68vh").mark("canvas")
                for name, handler in (("place", self._on_place), ("move", self._on_move),
                                      ("connect", self._on_connect), ("fault", self._on_fault),
                                      ("select", self._on_select), ("open", self._on_open),
                                      ("delete", self._on_delete), ("layout", self._on_layout)):
                    self.canvas.on(name, lambda e, h=handler: h(e.args or {}))
            with ui.column().classes("w-72 gap-1"):
                self.properties()
        # Dialogs are created here: an event handler runs in the slot of the
        # element that fired it, and the canvas renders no slot.
        self.dialog_host = ui.element("div").mark("dialog-host")
        self._sync_selection()

    def _palette(self, summary: Any) -> None:
        """Tools and the types new elements get."""
        with ui.column().classes("w-48 gap-1 bg-white rounded border p-2").mark("palette"):
            ui.label(self.t("palette")).classes("text-xs uppercase tracking-wide text-gray-500")
            ui.toggle({tool: self.t(f"tool_{tool}") for tool in TOOLS}, value=self.tool,
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
        if self.selected is not None and self.selected[1] not in self._names(
                "buses" if self.selected[0] == "bus" else "branches"):
            self.selected = None
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
        Select an element in the plan (and, unless it came from there, in its table).

        Parameters
        ----------
        kind : {"bus", "branch"} or None
            The element kind; ``None`` clears.
        name : str or None
            The element.
        from_table : bool
            The selection came from a table row.
        """
        self.selected = (kind, name) if kind and name else None
        self._sync_selection()
        self.properties.refresh()
        if self.selected is not None and not from_table:
            self.page.select_row({"bus": "buses", "branch": "branches"}[kind], name)

    def _sync_selection(self) -> None:
        """Show the selection on the canvas."""
        if self.canvas is not None:
            kind, name = self.selected if self.selected else (None, None)
            self.canvas.select(kind, name)

    @ui.refreshable_method
    def properties(self) -> None:
        """The fields of the selected element, with edit and delete."""
        with ui.card().classes("w-full p-3 gap-1").props("flat bordered").mark("properties"):
            if self.selected is None:
                ui.label(self.t("properties")).classes("text-xs uppercase tracking-wide text-gray-500")
                ui.label(self.t("nothing_selected_plan")).classes("text-sm text-gray-500")
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
        """Select what was clicked (or nothing)."""
        self.select(args.get("kind"), args.get("name"))

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

    def _on_delete(self, args: Dict[str, Any]) -> None:
        """Delete the selected element."""
        kind, name = args.get("kind"), args.get("name")
        if kind in ("bus", "branch") and name and self.apply(self.removal(kind, name)):
            self.select(None, None)

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
            if not self.plan.apply(command):
                return
            renamed = next((c for c in commands if isinstance(c, (RenameBus, RenameBranch))), None)
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

    def _tab_fault(self) -> None:
        faults = [r for r in self.plan.session.table("faults").rows if r["bus"] == self.name]
        self.inputs["has_fault"] = ui.switch(self.t("fault_here"), value=bool(faults)).mark("dlg-fault")
        if faults:
            ui.label(", ".join(r["name"] for r in faults)).classes("text-sm text-gray-600")

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
        faults = [r for r in self.plan.session.table("faults").rows if r["bus"] == self.name]
        wanted = bool(self.inputs["has_fault"].value)
        if wanted and not faults:
            frequencies = self.plan.session.summary().frequencies
            return [AddFault(name=free_name("F", self.plan._names("faults")), bus=self.name,
                             scalings={f: 1.0 for f in frequencies})]
        if not wanted:
            return [RemoveFault(name=r["name"]) for r in faults]
        return []


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

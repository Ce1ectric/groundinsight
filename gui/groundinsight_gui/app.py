# groundinsight_gui/app.py
"""
The NiceGUI app: start page, network tables, fault jobs, key figures,
diagnostics, pre-flight check (GUI phase 0 skeleton).

Everything that touches the network goes through ``groundinsight.api``:

* a :class:`~groundinsight.api.ProjectSession` per browser tab holds the
  project (one session per user and project -- the same code serves a desktop
  window and, later, a team server);
* a cell edit becomes an update command, the delete button a remove command,
  the undo/redo buttons the session's undo and redo -- the library validates
  and re-derives, a rejected edit changes nothing and is reported;
* "Run" submits a job to the session's :class:`~groundinsight.api.JobRunner`
  and a 0.2 s timer polls its status, so the event loop never waits for a
  solve (rule R5); "Cancel" uses the library's cooperative cancellation;
* every number on screen is a field of a DTO, formatted by
  :mod:`groundinsight_gui.formatting` -- the tiles show the rows of
  ``res_fault``, the chart and the grids the rows of ``res_buses``; the only
  logic here is *which* row (the RMS row, the lowest non-zero frequency).

The stale marker is the library's verdict (``FaultResultDTO.stale``, from
``Network.is_result_stale``): it appears after an edit that changes an input of
the result and disappears again when the edit is undone.

Phase-0 limits, by design: no paste from spreadsheets, no formula editor
beyond a text cell, no network graph, no studies besides the fault sweep; a
page reload starts a new session (unsaved edits are lost).
"""

import logging
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional

from nicegui import app, run, ui

from groundinsight.api import (
    EXAMPLES,
    FaultResultDTO,
    FaultResultsDTO,
    JobRunner,
    KeyFigureRowDTO,
    ProjectError,
    ProjectSession,
    RemoveBranch,
    RemoveBranchType,
    RemoveBus,
    RemoveBusType,
    RemoveFault,
    RemoveSource,
    SolveAllRequestDTO,
    TableDTO,
    UpdateBranch,
    UpdateBranchType,
    UpdateBus,
    UpdateBusType,
    UpdateFault,
    UpdateSource,
)

from .formatting import DASH, format_number, format_unit, parse_number
from .i18n import STRINGS, column_label, text

logger = logging.getLogger(__name__)

__all__ = ["AppOptions", "ProjectPage", "build_app", "run_app"]

ACCENT = "#2a78d6"
MUTED = "#a9c4e8"

#: The element tables shown, in this order.
TABLES = ("buses", "branches", "sources", "faults", "bus_types", "branch_types")

#: Editable columns per table and how a typed value is read: ``number``
#: (decimal comma or point), ``bool``, ``text`` (empty means none), ``type``
#: (a catalogue type, picked from a list), ``bus`` (a bus, from a list),
#: ``formula`` (text, checked by the library's formula guard).
EDITABLE = {
    "buses": {
        "type": "type",
        "specific_earth_resistance": "number",
        "active": "bool",
        "description": "text",
    },
    "branches": {
        "type": "type",
        "from_bus": "bus",
        "to_bus": "bus",
        "length": "number",
        "specific_earth_resistance": "number",
        "parallel_coefficient": "number",
        "active": "bool",
        "description": "text",
    },
    "sources": {"bus": "bus", "i_k_a": "number", "r_to_x": "number", "kappa": "number", "description": "text"},
    "faults": {"bus": "bus", "t_k_s": "number", "n_factor": "number", "description": "text"},
    "bus_types": {"description": "text", "impedance_formula": "formula"},
    "branch_types": {
        "description": "text",
        "self_impedance_formula": "formula",
        "mutual_impedance_formula": "formula",
        "phase_impedance_formula": "formula",
    },
}

UPDATE_COMMANDS = {
    "buses": UpdateBus,
    "branches": UpdateBranch,
    "sources": UpdateSource,
    "faults": UpdateFault,
    "bus_types": UpdateBusType,
    "branch_types": UpdateBranchType,
}

REMOVE_COMMANDS = {
    "buses": RemoveBus,
    "branches": RemoveBranch,
    "sources": RemoveSource,
    "faults": RemoveFault,
    "bus_types": RemoveBusType,
    "branch_types": RemoveBranchType,
}

#: The table that lists an element kind of a diagnostic.
KIND_TABLES = {
    "bus": "buses",
    "branch": "branches",
    "source": "sources",
    "fault": "faults",
    "bus_type": "bus_types",
    "branch_type": "branch_types",
}

#: Columns of the per-frequency key-figure grid: the fields of the DTO row,
#: which are the columns of ``res_fault``.
KEY_FIGURE_COLUMNS = [name for name in KeyFigureRowDTO.model_fields if name != "dto_schema_version"]

#: Value kinds of the key-figure columns that are not floats.
_KEY_FIGURE_DTYPES = {"fault": "str", "fault_bus": "str", "EPR_max_bus": "str", "is_rms": "bool",
                      "n_diagnostics": "int"}

#: Buses up to which the chart labels every bar.
MAX_LABELLED_BARS = 40

#: Element buttons shown per diagnostic before the rest is summarised.
MAX_ELEMENT_LINKS = 6

_CSS = """
body { background: #f6f6f4; }
.gi-num { font-variant-numeric: tabular-nums; }
.gi-tile { min-width: 0; }
"""


@dataclass(frozen=True)
class AppOptions:
    """
    How the app starts.

    Attributes
    ----------
    language : {"de", "en"}
        Initial language.
    backend : {"thread", "process"}
        Job backend of the sessions.
    max_job_runtime_s : float, optional
        Runtime limit of every job.
    start_example : str, optional
        Open this template at once instead of the start page.
    start_file : str, optional
        Open this project file at once.
    templates : dict of str to callable, optional
        Template name to network builder; default
        :data:`groundinsight.api.EXAMPLES`.
    """

    language: str = "de"
    backend: str = "thread"
    max_job_runtime_s: Optional[float] = None
    start_example: Optional[str] = None
    start_file: Optional[str] = None
    templates: Optional[Dict[str, Callable[[], Any]]] = None

    def template_builders(self) -> Dict[str, Callable[[], Any]]:
        """
        The templates offered.

        Returns
        -------
        dict of str to callable
            Name to network builder.
        """
        return dict(self.templates) if self.templates else dict(EXAMPLES)


def build_app(options: AppOptions = AppOptions()) -> None:  # noqa: B008 -- frozen dataclass
    """
    Register the app's page with NiceGUI.

    Each browser tab gets its own :class:`ProjectPage` with its own session;
    a page closes its session when NiceGUI deletes the client, and every page
    is closed on shutdown.

    Parameters
    ----------
    options : AppOptions
        Start options.
    """
    pages: List[ProjectPage] = []

    @ui.page("/")
    def index() -> None:
        page = ProjectPage(options)
        pages.append(page)

        def forget() -> None:
            page.close()
            if page in pages:
                pages.remove(page)

        ui.context.client.on_delete(forget)
        page.build()

    def close_all() -> None:
        for page in list(pages):
            page.close()
        pages.clear()

    app.on_shutdown(close_all)


def run_app(options: AppOptions, *, native: bool = False, host: str = "127.0.0.1",
            port: Optional[int] = None, open_browser: bool = True) -> None:
    """
    Build the app and start NiceGUI.

    Parameters
    ----------
    options : AppOptions
        Start options.
    native : bool
        Open a desktop window (pywebview) instead of using a browser.
    host : str
        Interface to listen on; ``127.0.0.1`` keeps the app local.
    port : int, optional
        Port; a free one in native mode, 8080 otherwise.
    open_browser : bool
        Browser mode: open the page in the default browser.
    """
    build_app(options)
    if port is None and native:
        from nicegui import native as native_module

        port = native_module.find_open_port()
    ui.run(
        title="groundinsight",
        host=host,
        port=port or 8080,
        native=native,
        window_size=(1440, 900) if native else None,
        reload=False,
        show=open_browser and not native,
        language="de" if options.language == "de" else "en-US",
        favicon="⏚",
    )


class ProjectPage:
    """
    One browser tab: a session and the widgets that show it.

    Parameters
    ----------
    options : AppOptions
        Start options.
    """

    def __init__(self, options: AppOptions) -> None:
        self.options = options
        self.language = options.language
        self.templates = options.template_builders()
        self.runner = JobRunner(backend=options.backend, max_runtime_s=options.max_job_runtime_s)
        self.session: Optional[ProjectSession] = None
        self.handle = None
        self.fault: Optional[str] = None
        self.mode = "auto"
        self.tab = "network"
        self.durations: Dict[str, Optional[float]] = {}
        self.selected: Dict[str, Optional[str]] = {table: None for table in TABLES}
        self.grids: Dict[str, Any] = {}
        self.expansions: Dict[str, Any] = {}
        self.start_error: Optional[str] = None
        if options.start_file:
            try:
                self.session = self._session_from_file(options.start_file)
            except ProjectError as exc:
                self.start_error = str(exc)
        elif options.start_example:
            self.session = self._session_from_template(options.start_example)
        self._reset_view_state()

    # -- lifecycle --------------------------------------------------------------

    def t(self, key: str, **values: Any) -> str:
        """The text of ``key`` in the page's language."""
        return text(self.language, key, **values)

    def build(self) -> None:
        """Create the page content and the job poller."""
        ui.add_css(_CSS)
        ui.colors(primary=ACCENT)
        self.root()
        ui.timer(0.2, self._poll)
        if self.start_error:
            ui.notify(f"{self.t('open_failed')}: {self.start_error}", type="negative", multi_line=True)

    def close(self) -> None:
        """Close the session and stop the page's job runner."""
        if self.session is not None:
            self.session.close()
            self.session = None
        self.runner.shutdown(wait=False)

    def _session_from_template(self, name: str) -> ProjectSession:
        """A new session on a template network."""
        return ProjectSession(
            self.templates[name](),
            runner=self.runner,
            max_job_runtime_s=self.options.max_job_runtime_s,
        )

    def _session_from_file(self, path: str) -> ProjectSession:
        """A session on a project file (raises :class:`ProjectError`)."""
        return ProjectSession.from_file(
            path, runner=self.runner, max_job_runtime_s=self.options.max_job_runtime_s
        )

    def _reset_view_state(self) -> None:
        """Select the first fault and forget per-project view state."""
        faults = self.session.fault_names() if self.session is not None else []
        self.fault = faults[0] if faults else None
        self.handle = None
        self.durations = {}
        self.selected = {table: None for table in TABLES}
        self.tab = "network"

    def _replace_session(self, session: ProjectSession) -> None:
        """Show another project; the previous session is closed."""
        if self.session is not None:
            self.session.close()
        self.session = session
        self._reset_view_state()
        self.root.refresh()

    # -- layout -----------------------------------------------------------------

    @ui.refreshable_method
    def root(self) -> None:
        """Toolbar plus start page or workspace (rebuilt on a language switch)."""
        self.grids, self.expansions = {}, {}
        self._toolbar()
        if self.session is None:
            self._start_page()
        else:
            self._workspace()

    def _toolbar(self) -> None:
        """Title, file actions, undo/redo, language."""
        with ui.row().classes("w-full items-center gap-2 px-4 py-2 bg-white shadow-sm no-wrap"):
            ui.label(self.t("app_title")).classes("text-lg font-semibold tracking-tight")
            self.title_label = ui.label().classes("text-sm text-gray-600").mark("project-title")
            self.dirty_marker = ui.label("●").classes("text-amber-600").mark("dirty")
            self.dirty_marker.tooltip(self.t("unsaved"))
            ui.space()
            with ui.button(self.t("new"), icon="note_add").props("flat dense no-caps").mark("new"):
                with ui.menu():
                    for name in self.templates:
                        ui.menu_item(name, on_click=lambda _, n=name: self._open_template(n))
            if self.session is not None:
                ui.button(self.t("save"), icon="save", on_click=self._save).props("flat dense no-caps").mark("save")
                ui.button(self.t("save_as"), on_click=self._open_save_dialog).props("flat dense no-caps").mark("save-as")
                self.undo_button = ui.button(icon="undo", on_click=self._undo).props("flat dense").mark("undo")
                self.redo_button = ui.button(icon="redo", on_click=self._redo).props("flat dense").mark("redo")
                self.undo_button.props(f'aria-label="{self.t("undo")}"')
                self.redo_button.props(f'aria-label="{self.t("redo")}"')
            ui.toggle(
                {"de": "DE", "en": "EN"},
                value=self.language,
                on_change=lambda e: self._set_language(e.value),
            ).props(f'dense aria-label="{self.t("language")}"').mark("lang")
        if self.session is not None:
            with ui.dialog() as self.save_dialog, ui.card().classes("w-[34rem]"):
                ui.label(self.t("save_as_title")).classes("text-base font-medium")
                self.save_input = ui.input(self.t("path")).classes("w-full").mark("save-path")
                ui.button(self.t("save"), on_click=lambda: self._do_save(self.save_input.value)).mark("save-confirm")
        self._refresh_toolbar()

    def _refresh_toolbar(self) -> None:
        """Project title, dirty marker and the undo/redo state."""
        if self.session is None:
            self.title_label.set_text("")
            self.dirty_marker.set_visibility(False)
            return
        summary = self.session.summary()
        location = summary.location or self.t("not_saved_yet")
        self.title_label.set_text(f"{summary.name} · {location}")
        self.dirty_marker.set_visibility(summary.dirty)
        self.undo_button.set_enabled(summary.can_undo)
        self.redo_button.set_enabled(summary.can_redo)

    def _start_page(self) -> None:
        """Templates and the open-file field."""
        with ui.column().classes("w-full items-center gap-6 p-8"):
            ui.label(self.t("start_title")).classes("text-2xl font-semibold")
            ui.label(self.t("from_template")).classes("text-base text-gray-700")
            with ui.row().classes("gap-4 justify-center"):
                for name in self.templates:
                    with ui.card().classes("w-80 gap-2"):
                        ui.button(name, icon="hub", on_click=lambda _, n=name: self._open_template(n)).props(
                            "flat no-caps").classes("text-lg").mark(f"template-{name}")
                        key = f"template_{name}"
                        description = self.t(key) if key in STRINGS[self.language] else name
                        ui.label(description).classes("text-sm text-gray-600")
            ui.label(self.t("open_project")).classes("text-base text-gray-700 mt-4")
            with ui.row().classes("items-end gap-2"):
                self.open_input = ui.input(self.t("path"), placeholder=self.t("path_example")).classes(
                    "w-[32rem]").mark("open-path")
                ui.button(self.t("open"), icon="folder_open", on_click=self._open_file).mark("open-button")

    def _workspace(self) -> None:
        """The three tabs of an open project."""
        with ui.tabs(value=self.tab, on_change=lambda e: setattr(self, "tab", e.value)).classes(
                "w-full bg-white text-gray-700").mark("tabs") as self.tabs:
            ui.tab("network", label=self.t("tab_network"), icon="hub").mark("tab-network")
            ui.tab("results", label=self.t("tab_results"), icon="bar_chart").mark("tab-results")
            ui.tab("check", label=self.t("tab_check"), icon="fact_check").mark("tab-check")
        with ui.tab_panels(self.tabs, value=self.tab).classes("w-full bg-transparent"):
            with ui.tab_panel("network").classes("gap-2"):
                self._network_panel()
            with ui.tab_panel("results").classes("gap-3"):
                self._results_panel()
            with ui.tab_panel("check").classes("gap-2"):
                self.check_view()

    # -- network tables ---------------------------------------------------------

    def _network_panel(self) -> None:
        """One editable grid per element table."""
        tables = self.session.tables()
        ui.label(self.t("edit_hint")).classes("text-xs text-gray-500")
        self.frequency_label = ui.label().classes("text-sm text-gray-700").mark("frequencies")
        self._refresh_frequencies()
        for table in TABLES:
            with ui.expansion(self.t(f"table_{table}"), value=table in ("buses", "branches")).classes(
                    "w-full bg-white rounded") as expansion:
                grid = ui.aggrid(self._grid_options(table, tables.tables[table]), theme="quartz").classes(
                    "h-64").mark(f"grid-{table}")
                # Only the fields used are sent: the full event of AG Grid 36
                # carries the grid's internal context, which is circular and
                # makes the browser drop a rowClicked event entirely.
                grid.on("cellValueChanged", lambda e, tbl=table: self._on_cell_edit(tbl, e.args),
                        args=["colId", "newValue", "oldValue", "data"])
                grid.on("rowClicked", lambda e, tbl=table: self._on_row_clicked(tbl, e.args),
                        args=["data", "rowId"])
                ui.button(self.t("remove_selected"), icon="delete",
                          on_click=lambda _, tbl=table: self._remove_selected(tbl)).props(
                    "flat dense no-caps").mark(f"remove-{table}")
            self.grids[table] = grid
            self.expansions[table] = expansion

    def _refresh_frequencies(self) -> None:
        """The frequency line above the tables."""
        frequencies = self.session.summary().frequencies
        shown = ", ".join(format_number(f, self.language, 6) for f in frequencies)
        self.frequency_label.set_text(f"{self.t('frequencies')}: {shown} Hz")

    def _grid_options(self, table: str, dto: TableDTO) -> Dict[str, Any]:
        """
        AG Grid options for one table DTO.

        Parameters
        ----------
        table : str
            The table name.
        dto : TableDTO
            Its columns and rows.

        Returns
        -------
        dict
            Column definitions (headers, units, editors) and the row data,
            which are the DTO's rows unchanged.
        """
        locale = "de-DE" if self.language == "de" else "en-US"
        editable = EDITABLE.get(table, {})
        summary = self.session.summary()
        choices = {
            "type": summary.bus_types if table == "buses" else summary.branch_types,
            "bus": [r["name"] for r in self.session.table("buses").rows] if "bus" in editable.values() else [],
        }
        definitions = []
        for column in dto.columns:
            kind = editable.get(column.name)
            definition: Dict[str, Any] = {
                "field": column.name,
                # The pencil marks the columns a double-click edits.
                "headerName": column_label(self.language, column.name, format_unit(column.unit))
                + (" ✎" if kind is not None else ""),
                "editable": kind is not None,
                "sortable": True,
                "resizable": True,
            }
            if column.dtype in ("float", "int"):
                definition["type"] = "numericColumn"
                definition[":valueFormatter"] = (
                    f"p => p.value == null ? '{DASH}' : Number(p.value).toLocaleString('{locale}', "
                    "{maximumSignificantDigits: 6, useGrouping: false})"
                )
                if kind is not None:
                    definition["cellDataType"] = False  # a text editor: accepts a decimal comma
            elif column.dtype == "bool":
                definition["cellDataType"] = "boolean"
            if kind in ("type", "bus"):
                definition["cellEditor"] = "agSelectCellEditor"
                definition["cellEditorParams"] = {"values": list(choices[kind])}
            definitions.append(definition)
        return {
            # Flex widths follow the grid's size, also for a grid built while
            # its tab is hidden (a fit-to-width at creation would measure 0).
            "defaultColDef": {"flex": 1, "minWidth": 100, "wrapHeaderText": True, "autoHeaderHeight": True},
            "columnDefs": definitions,
            "rowData": dto.rows,
            ":getRowId": "p => String(p.data.name)",
            "rowSelection": {"mode": "singleRow", "checkboxes": False, "enableClickSelection": True},
            "stopEditingWhenCellsLoseFocus": True,
        }

    @staticmethod
    def _read_value(kind: str, raw: Any) -> Any:
        """
        A typed cell value as the command takes it.

        Parameters
        ----------
        kind : str
            The column's kind (see :data:`EDITABLE`).
        raw : Any
            What the grid sent.

        Returns
        -------
        Any
            The value.

        Raises
        ------
        ValueError
            If a number cannot be read.
        """
        if kind == "number":
            if raw is None or (isinstance(raw, str) and not raw.strip()):
                return None
            return parse_number(raw)
        if kind == "bool":
            if isinstance(raw, bool):
                return raw
            return str(raw).strip().lower() in ("true", "1", "ja", "yes")
        if kind == "text":
            return str(raw) if raw is not None and str(raw).strip() else None
        return raw

    def _on_cell_edit(self, table: str, args: Dict[str, Any]) -> None:
        """A cell was edited: apply it as an update command."""
        column = args.get("colId")
        name = (args.get("data") or {}).get("name")
        kind = EDITABLE.get(table, {}).get(column)
        try:
            if kind is None:
                raise ValueError(f"'{column}' cannot be edited here")
            value = self._read_value(kind, args.get("newValue"))
            report = self.session.apply(UPDATE_COMMANDS[table](name=name, fields={column: value}))
        except ValueError as exc:  # CommandError is a ValueError
            ui.notify(f"{self.t('edit_rejected')}: {exc}", type="negative", multi_line=True)
            self._push_grid(table, self.session.table(table))  # back to the session's values
            return
        self._notify_diagnostics(report.diagnostics)
        self._after_change()

    def _on_row_clicked(self, table: str, args: Dict[str, Any]) -> None:
        """Remember the clicked row for the delete button."""
        self.selected[table] = (args.get("data") or {}).get("name")

    def _remove_selected(self, table: str) -> None:
        """Delete the selected row through a remove command."""
        name = self.selected.get(table)
        if not name:
            ui.notify(self.t("nothing_selected"), type="info")
            return
        try:
            report = self.session.apply(REMOVE_COMMANDS[table](name=name))
        except ValueError as exc:
            ui.notify(f"{self.t('edit_rejected')}: {exc}", type="negative", multi_line=True)
            return
        self.selected[table] = None
        self._notify_diagnostics(report.diagnostics)
        self._after_change()

    def _notify_diagnostics(self, diagnostics: List[Any]) -> None:
        """Show the library warnings a command raised."""
        for record in diagnostics:
            ui.notify(f"{record.code}: {record.message}", type="warning", multi_line=True)

    def _push_grid(self, table: str, dto: TableDTO) -> None:
        """
        Show a table's current rows in its grid.

        Only the rows are sent when the columns are unchanged: the grid keeps
        its scroll position, sort order and selection (the rows have stable
        ids, their names). A change of the columns -- the choices of a type or
        bus column after a removal -- rebuilds the grid.
        """
        grid = self.grids[table]
        options = self._grid_options(table, dto)
        if grid.options.get("columnDefs") == options["columnDefs"]:
            grid.options["rowData"] = options["rowData"]
            grid.run_grid_method("setGridOption", "rowData", options["rowData"])
        else:
            grid.options.update(options)
            grid.update()

    def _after_change(self) -> None:
        """Refresh everything that shows the network after a command."""
        tables = self.session.tables()
        for table in self.grids:
            self._push_grid(table, tables.tables[table])
        faults = self.session.fault_names()
        if self.fault not in faults:
            self.fault = faults[0] if faults else None
        self.fault_select.set_options(faults, value=self.fault)
        self._refresh_frequencies()
        self._refresh_toolbar()
        self.result_view.refresh()
        self.check_view.refresh()

    def _undo(self) -> None:
        """Undo the last command."""
        if self.session.undo() is not None:
            self._after_change()

    def _redo(self) -> None:
        """Redo the last undone command."""
        if self.session.redo() is not None:
            self._after_change()

    def _goto(self, kind: str, name: str) -> None:
        """Show an element of a diagnostic in its table."""
        self.tab = "network"
        self.tabs.set_value("network")
        table = KIND_TABLES.get(kind)
        if table is None or table not in self.grids:
            return
        self.expansions[table].open()
        self.selected[table] = name
        names = [row["name"] for row in self.session.table(table).rows]
        grid = self.grids[table]
        # Fire and forget: selection and scrolling happen in the browser.
        grid.run_row_method(name, "setSelected", True)
        if name in names:
            grid.run_grid_method("ensureIndexVisible", names.index(name), "middle")
        ui.run_javascript(f"getHtmlElement({grid.id})?.scrollIntoView({{block: 'center'}})")

    # -- results ----------------------------------------------------------------

    def _results_panel(self) -> None:
        """Fault and mode selection, run/cancel, progress, the result view."""
        with ui.row().classes("items-end gap-3 w-full"):
            self.fault_select = ui.select(
                self.session.fault_names(), value=self.fault, label=self.t("fault"),
                on_change=self._on_fault_change,
            ).props("outlined dense").classes("w-56").mark("fault-select")
            ui.select(
                {"auto": self.t("mode_auto"), "paths": self.t("mode_paths")},
                value=self.mode,
                label=self.t("mode"),
                on_change=lambda e: setattr(self, "mode", e.value),
            ).props("outlined dense").classes("w-80").mark("mode-select")
            self.run_button = ui.button(self.t("run"), icon="play_arrow", on_click=self._run).mark("run")
            self.run_all_button = ui.button(self.t("run_all"), icon="playlist_play",
                                            on_click=self._run_all).props("outline").mark("run-all")
            self.cancel_button = ui.button(self.t("cancel"), icon="stop", on_click=self._cancel).props(
                "flat color=negative").mark("cancel")
        self.progress = ui.linear_progress(value=0, show_value=False).props("rounded size=6px").classes(
            "w-full").mark("progress")
        self.progress_label = ui.label().classes("text-xs text-gray-500").mark("progress-label")
        self._update_busy()
        self.result_view()

    def _on_fault_change(self, event: Any) -> None:
        """Show the stored result of the chosen fault, if any."""
        self.fault = event.value
        self.result_view.refresh()

    def _busy(self) -> bool:
        """Whether a job of this page is running."""
        return self.handle is not None and not self.handle.done

    def _update_busy(self) -> None:
        """Enable/disable the run buttons, show/hide cancel and progress."""
        busy = self._busy()
        self.run_button.set_enabled(not busy)
        self.run_all_button.set_enabled(not busy)
        self.cancel_button.set_visibility(busy)
        self.progress.set_visibility(busy)

    def _run(self) -> None:
        """Solve the chosen fault as a job."""
        if self._busy() or self.fault is None:
            return
        self.handle = self.session.submit_solve(self.fault, phase_current_mode=self.mode)
        self.progress_label.set_text(self.t("running"))
        self._update_busy()

    def _run_all(self) -> None:
        """Solve every fault as one job, cancellable between faults."""
        if self._busy():
            return
        self.handle = self.session.submit(SolveAllRequestDTO(phase_current_mode=self.mode))
        self.progress_label.set_text(self.t("running"))
        self._update_busy()

    def _cancel(self) -> None:
        """Ask the running job to stop."""
        if self.handle is not None:
            self.handle.cancel()

    def _poll(self) -> None:
        """Timer callback: progress of the running job, its end."""
        handle = self.handle
        if handle is None or self.session is None:
            return
        status = handle.status()
        if not handle.done:
            progress = status.progress
            self.progress.set_value(progress.fraction or 0.0)
            if progress.total:
                self.progress_label.set_text(f"{progress.done}/{progress.total} · {progress.label}")
            return
        self.handle = None
        if status.state == "done":
            result = handle.result(timeout=0)
            results = result.results if isinstance(result, FaultResultsDTO) else [result]
            for item in results:
                if isinstance(item, FaultResultDTO):
                    self.durations[item.fault] = item.duration_s
            self.progress_label.set_text("")
            self.result_view.refresh()
            self._refresh_toolbar()
        else:
            message = {
                "cancelled": self.t("cancelled"),
                "timeout": self.t("timed_out"),
            }.get(status.state)
            if message is None:
                error = status.error
                message = f"{self.t('job_failed')}: {error.type}: {error.message}" if error else self.t("job_failed")
                ui.notify(message, type="negative", multi_line=True)
            else:
                ui.notify(message, type="info")
            self.progress_label.set_text(message)
        self._update_busy()

    @ui.refreshable_method
    def result_view(self) -> None:
        """Stale marker, tiles, key figures, chart, bus results, diagnostics."""
        result = self.session.result(self.fault) if (self.session and self.fault) else None
        if result is None:
            ui.label(self.t("no_result")).classes("text-gray-500 italic py-6")
            return
        if result.stale:
            with ui.row().classes("items-center gap-2 text-amber-800 bg-amber-50 rounded px-3 py-1").mark(
                    "stale-banner"):
                ui.icon("warning_amber")
                ui.label(self.t("stale"))
        self._tiles(result)
        with ui.row().classes("w-full gap-3 no-wrap items-stretch"):
            with ui.card().classes("w-1/2 p-3"):
                ui.echart(self._chart(result)).classes("w-full h-80").mark("epr-chart")
            with ui.card().classes("w-1/2 p-3"):
                ui.label(self.t("bus_results")).classes("text-sm font-medium text-gray-700")
                rms_rows = [row for row in result.buses.rows if row["is_rms"]]
                columns = [c for c in result.buses.columns if c.name in ("bus_name", "EPR_V", "I_bus_A", "I_inj_A")]
                ui.aggrid(self._result_grid(columns, rms_rows, digits=4), theme="quartz").classes(
                    "h-72").mark("grid-bus_results")
        ui.label(self.t("key_figures")).classes("text-sm font-medium text-gray-700")
        rows = [row.model_dump(exclude={"dto_schema_version"}) for row in result.key_figures.rows]
        key_columns = [{"name": name, "dtype": _KEY_FIGURE_DTYPES.get(name, "float")} for name in KEY_FIGURE_COLUMNS]
        ui.aggrid(self._result_grid(key_columns, rows, digits=4, fit="content"), theme="quartz",
                  auto_size_columns=False).classes("h-48").mark("grid-key_figures")
        self._diagnostics_panel(result.diagnostics, title=self.t("diagnostics"))
        duration = self.durations.get(result.fault)
        parts = [
            f"{self.t('revision')} {result.revision if result.revision is not None else DASH}",
            f"{self.t('input_digest')} {(result.input_digest or DASH)[:20]}",
            f"{self.t('duration')} {format_number(duration * 1e3, self.language, 3) if duration else DASH} ms",
            f"{self.t('mode')}: {result.phase_current_mode or DASH}",
            f"groundinsight {result.groundinsight_version}",
        ]
        ui.label(" · ".join(parts)).classes("text-xs text-gray-500").mark("result-footer")

    def _tiles(self, result: FaultResultDTO) -> None:
        """The key-figure tiles: RMS row and the row of the fundamental."""
        rms = result.key_figures.rms()
        frequencies = [r.frequency_Hz for r in result.key_figures.rows if not r.is_rms]
        positive = [f for f in frequencies if f and f > 0]
        reference = min(positive) if positive else (frequencies[0] if frequencies else None)
        row = result.key_figures.at(reference) if reference is not None else None
        at = f"{self.t('at')} {format_number(reference, self.language, 6)} Hz" if reference is not None else ""
        tiles = [
            ("epr_fault", rms.EPR_fault_V if rms else None, "V", f"{result.fault_bus} · {self.t('rms')}"),
            ("epr_max", rms.EPR_max_V if rms else None, "V",
             f"{self.t('at_bus')} {rms.EPR_max_bus if rms else DASH} · {self.t('rms')}"),
            ("i_f", row.I_F_A if row else None, "A", at),
            ("r_epr", row.r_epr if row else None, "", at),
            ("r_current", row.r_current if row else None, "", at),
            ("i_e", row.I_E_A if row else None, "A", at),
            ("z_e", row.Z_E_Ohm if row else None, "Ω", at),
        ]
        with ui.element("div").classes("grid gap-3 w-full grid-cols-2 md:grid-cols-4 xl:grid-cols-7"):
            for key, value, unit, note in tiles:
                with ui.card().classes("gi-tile p-3 gap-0").props("flat bordered"):
                    ui.label(self.t(f"tile_{key}")).classes("text-xs uppercase tracking-wide text-gray-500")
                    with ui.row().classes("items-baseline gap-1"):
                        ui.label(format_number(value, self.language)).classes(
                            "text-2xl font-semibold gi-num text-gray-900").mark(f"tile-{key}")
                        ui.label(unit).classes("text-sm text-gray-600")
                    ui.label(note).classes("text-xs text-gray-500").mark(f"tile-{key}-note")

    def _chart(self, result: FaultResultDTO) -> Dict[str, Any]:
        """ECharts options: RMS EPR per bus, the fault bus highlighted."""
        rows = [row for row in result.buses.rows if row["is_rms"]]
        names = [row["bus_name"] for row in rows]
        data = [
            {"value": row["EPR_V"], "itemStyle": {"color": ACCENT if row["bus_name"] == result.fault_bus else MUTED}}
            for row in rows
        ]
        return {
            "title": {"text": self.t("chart_title"), "textStyle": {"fontSize": 13, "fontWeight": 500}},
            "aria": {"enabled": True},
            "grid": {"left": 56, "right": 16, "top": 48, "bottom": 48},
            "tooltip": {"trigger": "axis"},
            # Every bus is labelled while the labels fit; a long ring is thinned
            # by ECharts (the tooltip still names each bar).
            "xAxis": {"type": "category", "data": names,
                      "axisLabel": {"interval": 0 if len(names) <= MAX_LABELLED_BARS else "auto",
                                    "rotate": 45, "fontSize": 10}},
            "yAxis": {"type": "value", "name": "V"},
            "series": [{"type": "bar", "name": "EPR", "data": data, "barMaxWidth": 24,
                        "itemStyle": {"borderRadius": [4, 4, 0, 0]}}],
        }

    def _result_grid(self, columns: List[Any], rows: List[Dict[str, Any]], digits: int,
                     fit: str = "width") -> Dict[str, Any]:
        """
        AG Grid options for a read-only result table.

        Parameters
        ----------
        columns : list
            Column DTOs or ``{"name", "dtype"}`` dicts.
        rows : list of dict
            The rows, unchanged.
        digits : int
            Significant digits shown.
        fit : {"width", "content"}
            Share the grid's width (flex), or give each column the width of
            its header (wide tables, which then scroll sideways). Both are
            independent of the grid being visible when it is built.

        Returns
        -------
        dict
            The grid options.
        """
        locale = "de-DE" if self.language == "de" else "en-US"
        definitions = []
        for column in columns:
            name = column["name"] if isinstance(column, dict) else column.name
            dtype = column["dtype"] if isinstance(column, dict) else column.dtype
            header = column_label(self.language, name)
            definition: Dict[str, Any] = {"field": name, "headerName": header, "sortable": True, "resizable": True}
            if fit == "content":
                definition["width"] = 40 + 8 * max(len(header), 6)
            if dtype == "bool":
                definition["cellDataType"] = "boolean"
            elif dtype in ("float", "int"):
                definition["type"] = "numericColumn"
                definition[":valueFormatter"] = (
                    f"p => p.value == null ? '{DASH}' : Number(p.value).toLocaleString('{locale}', "
                    f"{{maximumSignificantDigits: {digits}, useGrouping: false}})"
                )
            definitions.append(definition)
        default = {"minWidth": 70} if fit == "content" else {"flex": 1, "minWidth": 80}
        return {"defaultColDef": default, "columnDefs": definitions, "rowData": rows}

    def _diagnostics_panel(self, diagnostics: List[Any], title: str) -> None:
        """Diagnostics with severity, code, element links and message."""
        count = len(diagnostics)
        with ui.expansion(f"{title} ({count})", icon="notes", value=count > 0).classes(
                "w-full bg-white rounded").mark("diagnostics"):
            ui.label(self.t("diagnostics_hint")).classes("text-xs text-gray-500")
            if not count:
                ui.label(self.t("no_diagnostics")).classes("text-sm text-gray-600")
            for record in diagnostics:
                icon = {"error": "error", "warning": "warning_amber"}.get(record.severity, "info")
                with ui.row().classes("w-full items-start gap-2 no-wrap py-1 border-t"):
                    ui.icon(icon).classes("text-amber-700 mt-1")
                    with ui.column().classes("gap-0 grow"):
                        with ui.row().classes("items-center gap-2"):
                            ui.badge(record.code).props("outline color=grey-8")
                            ui.label(self.t(f"severity_{record.severity}")).classes("text-xs text-gray-600")
                            for element in record.elements[:MAX_ELEMENT_LINKS]:
                                ui.button(
                                    f"{self._kind_label(element.kind)} {element.name} →",
                                    on_click=lambda _, e=element: self._goto(e.kind, e.name),
                                ).props("flat dense no-caps size=sm").mark(f"goto-{element.kind}-{element.name}")
                            if len(record.elements) > MAX_ELEMENT_LINKS:
                                ui.label(f"+{len(record.elements) - MAX_ELEMENT_LINKS}").classes("text-xs")
                        ui.label(record.message).classes("text-sm text-gray-800 whitespace-normal")

    def _kind_label(self, kind: str) -> str:
        """An element kind of a diagnostic in the page's language."""
        key = f"kind_{kind}"
        return self.t(key) if key in STRINGS[self.language] else kind

    # -- check ------------------------------------------------------------------

    @ui.refreshable_method
    def check_view(self) -> None:
        """The pre-flight check of the current network."""
        report = self.session.check()
        ui.label(self.t("check_title")).classes("text-base font-medium")
        if report.ok:
            ui.label(self.t("check_ok")).classes("text-green-800").mark("check-ok")
        else:
            ui.label(self.t("check_errors")).classes("text-red-800 font-medium").mark("check-errors")
        ui.label(self.t("check_counts", errors=report.n_errors, warnings=report.n_warnings)).classes(
            "text-sm text-gray-600")
        self._diagnostics_panel(report.diagnostics, title=self.t("tab_check"))

    # -- files and language -----------------------------------------------------

    def _open_template(self, name: str) -> None:
        """Start a new, unsaved project from a template."""
        self._replace_session(self._session_from_template(name))

    async def _open_file(self) -> None:
        """Open the project file named in the start page's field."""
        path = (self.open_input.value or "").strip()
        try:
            session = await run.io_bound(self._session_from_file, path)
        except ProjectError as exc:
            ui.notify(f"{self.t('open_failed')}: {exc}", type="negative", multi_line=True)
            return
        self._replace_session(session)

    def _open_save_dialog(self) -> None:
        """Ask for a file name."""
        self.save_input.set_value(self.session.location or "")
        self.save_dialog.open()

    async def _save(self) -> None:
        """Save to the project's location, or ask for one."""
        if self.session.location is None:
            self._open_save_dialog()
            return
        await self._do_save(None)

    async def _do_save(self, path: Optional[str]) -> None:
        """Save (to ``path`` if given) and report the location."""
        target = path.strip() if isinstance(path, str) and path.strip() else None
        try:
            location = await run.io_bound(self.session.save, target)
        except ProjectError as exc:
            ui.notify(f"{self.t('save_failed')}: {exc}", type="negative", multi_line=True)
            return
        self.save_dialog.close()
        ui.notify(f"{self.t('saved')}: {location}", type="positive")
        self._refresh_toolbar()

    def _set_language(self, language: str) -> None:
        """Switch the language without losing the session."""
        if language == self.language:
            return
        self.language = language
        self.root.refresh()


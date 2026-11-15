# groundinsight_gui/evaluations.py
"""
Evaluation tabs: user-defined charts and tables of the results.

A project keeps its evaluation tabs in the ``views`` of its project file (see
:class:`groundinsight.api.ViewDTO`): each tab is a title and a list of
blocks, a block one result quantity -- ``EPR_V``, ``U_vT_V``,
``I_branch_A``, a key figure -- of chosen buses or branches, at one frequency
or as RMS, for one fault or the selected one, shown as a bar chart or a table.
Adding, changing and removing a tab or a block are commands
(``AddView``/``UpdateView``/``RemoveView``), so they are undone like every
edit and saved with the project. The numbers come from
``ProjectSession.view_table``, which picks them out of the stored result;
this module only draws them.
"""

import logging
from typing import Any, Dict, List

from nicegui import ui

from groundinsight.api import AddView, KeyFigureRowDTO, RemoveView, UpdateView, ViewBlockDTO, ViewDTO

from .formatting import DASH, format_number
from .i18n import column_label

logger = logging.getLogger(__name__)

__all__ = ["QUANTITIES", "EvaluationTabs"]

#: The quantities a block can show, per source.
QUANTITIES: Dict[str, List[str]] = {
    "buses": ["EPR_V", "U_vT_V", "U_transfer_V", "I_bus_A", "I_inj_A"],
    "branches": ["I_branch_A"],
    "key_figures": [n for n, f in KeyFigureRowDTO.model_fields.items()
                    if n.endswith(("_V", "_A", "_Ohm")) or n.startswith("r_")],
}


class EvaluationTabs:
    """
    The evaluation tabs of a :class:`~groundinsight_gui.app.ProjectPage`.

    Parameters
    ----------
    page : ProjectPage
        The page: session, texts, selected fault, ``apply``.
    """

    def __init__(self, page: Any) -> None:
        self.page = page

    def t(self, key: str, **values: Any) -> str:
        """The text of ``key`` in the page's language."""
        return self.page.t(key, **values)

    def views(self) -> List[ViewDTO]:
        """The project's evaluation tabs."""
        return [ViewDTO.model_validate(v) for v in self.page.session.views]

    def ids(self) -> List[str]:
        """The ids of the tabs, in order."""
        return [v.id for v in self.views()]

    # -- tabs ---------------------------------------------------------------------

    def add_tab(self) -> None:
        """Ask for a title and add an empty tab."""
        with self.page.dialog_host, ui.dialog() as dialog, ui.card().classes("w-[28rem]").mark("view-dialog"):
            ui.label(self.t("view_new")).classes("text-base font-medium")
            title = ui.input(self.t("view_title"), value=self.t("view_default_title")).classes("w-full").mark(
                "view-title")

            def create() -> None:
                taken = set(self.ids())
                n = 1
                while f"v{n}" in taken:
                    n += 1
                if self.page.apply(AddView(view={"id": f"v{n}", "title": title.value or f"v{n}", "blocks": []})):
                    dialog.close()
                    self.page.show_tab(f"v{n}")

            ui.button(self.t("apply"), on_click=create).props("no-caps").mark("view-create")
        dialog.on_value_change(lambda e: None if e.value else dialog.delete())
        dialog.open()

    def panel(self, view: ViewDTO) -> None:
        """
        The content of one tab.

        Parameters
        ----------
        view : ViewDTO
            The tab.
        """
        with ui.row().classes("w-full items-center gap-2"):
            ui.label(view.title).classes("text-lg font-medium")
            ui.space()
            ui.button(self.t("block_add"), icon="add_chart",
                      on_click=lambda: self.block_dialog(view)).props("no-caps").mark(f"block-add-{view.id}")
            ui.button(self.t("view_remove"), icon="delete",
                      on_click=lambda: self.page.apply(RemoveView(id=view.id))).props(
                "flat no-caps color=negative").mark(f"view-remove-{view.id}")
        if not view.blocks:
            ui.label(self.t("view_empty")).classes("text-sm text-gray-500 italic")
        with ui.element("div").classes("grid gap-3 w-full grid-cols-1 xl:grid-cols-2"):
            for index, block in enumerate(view.blocks):
                self._block(view, index, block)

    def _block(self, view: ViewDTO, index: int, block: ViewBlockDTO) -> None:
        """One block: its title, the chart or table, and a remove button."""
        fault = block.fault or self.page.fault
        resolved = block.model_copy(update={"fault": fault}) if fault else block
        table = self.page.session.view_table(resolved)
        frequency = (self.t("rms") if block.frequency_Hz is None
                     else f"{format_number(block.frequency_Hz, self.page.language, 6)} Hz")
        title = f"{column_label(self.page.language, block.quantity)} · {frequency} · {fault or DASH}"
        with ui.card().classes("p-3 gap-1").props("flat bordered").mark(f"block-{view.id}-{index}"):
            with ui.row().classes("w-full items-center no-wrap"):
                ui.label(title).classes("text-sm font-medium grow")
                ui.button(icon="close", on_click=lambda: self._remove_block(view, index)).props(
                    "flat dense round size=sm").mark(f"block-remove-{view.id}-{index}")
            if not table.rows:
                ui.label(self.t("legend_no_result")).classes("text-xs text-gray-500")
            elif block.kind == "chart":
                ui.echart(self._chart(table, block)).classes("w-full h-64").mark(f"block-chart-{view.id}-{index}")
            else:
                ui.aggrid(self._grid(table, block), theme="quartz").classes("h-56").mark(
                    f"block-table-{view.id}-{index}")

    def _chart(self, table: Any, block: ViewBlockDTO) -> Dict[str, Any]:
        """Bars of the block's values per element."""
        return {"tooltip": {"trigger": "axis"}, "grid": {"left": 56, "right": 16, "top": 24, "bottom": 56},
                "xAxis": {"type": "category", "data": [r["element"] for r in table.rows],
                          "axisLabel": {"rotate": 45}},
                "yAxis": {"type": "value", "name": column_label(self.page.language, block.quantity)},
                "series": [{"type": "bar", "data": [r["value"] for r in table.rows], "barMaxWidth": 28}]}

    def _grid(self, table: Any, block: ViewBlockDTO) -> Dict[str, Any]:
        """A two-column table of the block's values."""
        locale = "de-DE" if self.page.language == "de" else "en-US"
        return {"columnDefs": [
            {"field": "element", "headerName": self.t("element")},
            {"field": "value", "headerName": column_label(self.page.language, block.quantity),
             "type": "numericColumn",
             ":valueFormatter": f"p => p.value == null ? '{DASH}' : Number(p.value).toLocaleString('{locale}', "
                                "{maximumSignificantDigits: 6, useGrouping: false})"}],
            "rowData": table.rows, "defaultColDef": {"flex": 1}}

    def _remove_block(self, view: ViewDTO, index: int) -> None:
        """Remove one block (an ``UpdateView``)."""
        blocks = [b for i, b in enumerate(view.blocks) if i != index]
        self.page.apply(UpdateView(view=view.model_copy(update={"blocks": blocks}).model_dump(mode="json")))

    def block_dialog(self, view: ViewDTO) -> None:
        """
        Ask for a block and add it to a tab.

        Parameters
        ----------
        view : ViewDTO
            The tab.
        """
        session = self.page.session
        names = {"buses": [r["name"] for r in session.table("buses").rows],
                 "branches": [r["name"] for r in session.table("branches").rows], "key_figures": []}
        frequencies = {None: self.t("rms")}
        frequencies.update({f: f"{format_number(f, self.page.language, 6)} Hz" for f in session.summary().frequencies})
        faults = {None: self.t("selected_fault")}
        faults.update({f: f for f in session.fault_names()})
        with self.page.dialog_host, ui.dialog() as dialog, ui.card().classes("w-[30rem]").mark("block-dialog"):
            ui.label(self.t("block_add")).classes("text-base font-medium")
            kind = ui.toggle({"chart": self.t("block_chart"), "table": self.t("block_table")}, value="chart").mark(
                "block-kind")
            source = ui.select({k: self.t(f"block_source_{k}") for k in QUANTITIES}, value="buses",
                               label=self.t("block_source")).classes("w-full").mark("block-source")
            quantity = ui.select({q: column_label(self.page.language, q) for q in QUANTITIES["buses"]},
                                 value="EPR_V", label=self.t("block_quantity")).classes("w-full").mark(
                "block-quantity")
            elements = ui.select(names["buses"], multiple=True, value=[], label=self.t("block_elements")).props(
                "use-chips").classes("w-full").mark("block-elements")
            frequency = ui.select(frequencies, value=None, label=self.t("frequencies")).classes("w-full").mark(
                "block-frequency")
            fault = ui.select(faults, value=None, label=self.t("fault")).classes("w-full").mark("block-fault")

            def on_source(event: Any) -> None:
                quantity.set_options({q: column_label(self.page.language, q) for q in QUANTITIES[event.value]},
                                     value=QUANTITIES[event.value][0])
                elements.set_options(names[event.value], value=[])

            source.on_value_change(on_source)

            def create() -> None:
                block = ViewBlockDTO(kind=kind.value, source=source.value, quantity=quantity.value,
                                     elements=list(elements.value or []), frequency_Hz=frequency.value,
                                     fault=fault.value)
                blocks = list(view.blocks) + [block]
                if self.page.apply(UpdateView(view=view.model_copy(update={"blocks": blocks}).model_dump(mode="json"))):
                    dialog.close()

            ui.button(self.t("apply"), on_click=create).props("no-caps").mark("block-create")
        dialog.on_value_change(lambda e: None if e.value else dialog.delete())
        dialog.open()

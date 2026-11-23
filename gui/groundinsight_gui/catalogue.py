# groundinsight_gui/catalogue.py
"""
The type catalogue of a project: new, edit, copy, from the standard library.

Every action becomes one command of ``groundinsight.api`` and so one undo
step: ``AddBusType``/``AddBranchType`` for a new type, a copy or a type from
the library, ``UpdateBusType``/``UpdateBranchType`` (all changed fields, a
rename included) for an edit. Deleting stays the table's "delete selected
row" (``RemoveBusType``/``RemoveBranchType``, refused while elements use the
type). The library's own check decides what a valid type is -- a broken
formula is rejected with its reason and nothing changes.

The dialogs are opened from the type tables of the network tab and from the
"+" buttons of the plan's palette, so an empty project gets its first types
without leaving the plan.
"""

import json
import logging
from typing import Any, Dict, List, Optional, Tuple

from nicegui import ui

import groundinsight as gi
from groundinsight.api import AddBranchType, AddBusType, UpdateBranchType, UpdateBusType, copy_name

from .formatting import format_number, parse_number
from .i18n import column_label

logger = logging.getLogger(__name__)

__all__ = ["TypeDialogs", "copy_name", "library_types"]

#: Fields of the dialog per kind: (field, how it is read). ``text`` empty is
#: ``None``, ``required`` text must not be empty, ``number`` takes a decimal
#: comma, ``bool`` is a switch, ``formula`` is text checked by the library.
FIELDS: Dict[str, List[Tuple[str, str]]] = {
    "bus": [("name", "required"), ("description", "text"), ("system_type", "required"),
            ("voltage_level", "number"), ("impedance_formula", "formula")],
    "branch": [("name", "required"), ("description", "text"), ("grounding_conductor", "bool"),
               ("self_impedance_formula", "formula"), ("mutual_impedance_formula", "formula"),
               ("phase_impedance_formula", "text"), ("conductor_material", "text"),
               ("cross_section_mm2", "number_or_none"), ("theta_initial_C", "number"),
               ("theta_final_C", "number_or_none"), ("voltage_level", "number_or_none"),
               ("c_shield_nf_per_km", "number_or_none"), ("c_earth_nf_per_km", "number_or_none")],
}

#: What a new type starts with.
NEW_TYPE: Dict[str, Dict[str, Any]] = {
    "bus": {"name": "", "description": None, "system_type": "MV/LV", "voltage_level": 20.0,
            "impedance_formula": "rho/50"},
    "branch": {"name": "", "description": None, "grounding_conductor": True, "self_impedance_formula": "",
               "mutual_impedance_formula": "", "phase_impedance_formula": None, "conductor_material": None,
               "cross_section_mm2": None, "theta_initial_C": 20.0, "theta_final_C": None,
               "voltage_level": None, "c_shield_nf_per_km": None, "c_earth_nf_per_km": None},
}

_ADD = {"bus": AddBusType, "branch": AddBranchType}
_UPDATE = {"bus": UpdateBusType, "branch": UpdateBranchType}


def library_types(kind: str) -> Dict[str, Any]:
    """
    The standard library's types of one kind, by name.

    Parameters
    ----------
    kind : {"bus", "branch"}
        Bus types (earth electrodes) or branch types (cables).

    Returns
    -------
    dict of str to BusType or BranchType
        :func:`groundinsight.standard_bus_types` or
        :func:`groundinsight.standard_branch_types`.
    """
    return gi.standard_bus_types() if kind == "bus" else gi.standard_branch_types()


class TypeDialogs:
    """
    The type dialogs of a :class:`~groundinsight_gui.app.ProjectPage`.

    Parameters
    ----------
    page : ProjectPage
        The page; its session, language, dialog host and plan.
    """

    def __init__(self, page: Any) -> None:
        self.page = page

    def t(self, key: str, **values: Any) -> str:
        """The text of ``key`` in the page's language."""
        return self.page.t(key, **values)

    def _names(self, kind: str) -> List[str]:
        """The catalogue's type names of one kind."""
        summary = self.page.session.summary()
        return list(summary.bus_types if kind == "bus" else summary.branch_types)

    def _apply(self, command: Any) -> bool:
        """Apply through the plan: rejection shown, everything refreshed."""
        return self.page.plan.apply(command)

    # -- copy -----------------------------------------------------------------------

    def copy(self, kind: str, name: str) -> Optional[str]:
        """
        Add a copy of a type under a free name.

        Parameters
        ----------
        kind : {"bus", "branch"}
            The catalogue.
        name : str
            The type copied.

        Returns
        -------
        str or None
            The copy's name, or ``None`` if the library refused it.
        """
        definition = self.page.session.type_definition(kind, name)
        new_name = copy_name(name, self._names(kind), self.t("copy_suffix"))
        return new_name if self._apply(_ADD[kind](definition={**definition, "name": new_name})) else None

    # -- new and edit -----------------------------------------------------------------

    def open_new(self, kind: str) -> None:
        """
        The dialog of a new type.

        Parameters
        ----------
        kind : {"bus", "branch"}
            The catalogue.
        """
        self._open_form(kind, None, dict(NEW_TYPE[kind]))

    def open_edit(self, kind: str, name: str) -> None:
        """
        The dialog of an existing type, all its fields editable.

        Parameters
        ----------
        kind : {"bus", "branch"}
            The catalogue.
        name : str
            The type.
        """
        self._open_form(kind, name, self.page.session.type_definition(kind, name))

    def _open_form(self, kind: str, name: Optional[str], values: Dict[str, Any]) -> None:
        """Build the form; ``name`` is ``None`` for a new type."""
        language = self.page.language
        title = self.t(f"type_new_{kind}") if name is None else self.t("type_edit_title", name=name)
        inputs: Dict[str, Any] = {}
        with self.page.dialog_host, ui.dialog() as dialog, ui.card().classes("w-[40rem] max-w-full").mark(
                "type-dialog"):
            ui.label(title).classes("text-base font-medium")
            for field, how in FIELDS[kind]:
                label = column_label(language, field)
                value = values.get(field)
                if how == "bool":
                    inputs[field] = ui.switch(label, value=bool(value)).mark(f"type-{field}")
                elif how.startswith("number"):
                    shown = "" if value is None else format_number(value, language, 12)
                    inputs[field] = ui.input(label, value=shown).classes("w-full").mark(f"type-{field}")
                else:
                    inputs[field] = ui.input(label, value=value or "").classes("w-full").mark(f"type-{field}")
            if kind == "branch":
                ui.label(self.t("type_formula_hint")).classes("text-xs text-gray-500")
            with ui.row().classes("w-full justify-end gap-2"):
                ui.button(self.t("cancel_dialog"), on_click=dialog.close).props("flat no-caps")
                ui.button(self.t("apply"), on_click=lambda: self._submit(kind, name, values, inputs, dialog)).props(
                    "no-caps").mark("type-apply")
        dialog.on_value_change(lambda e: None if e.value else dialog.delete())
        dialog.open()

    def _read(self, kind: str, inputs: Dict[str, Any]) -> Dict[str, Any]:
        """The typed values, read as the fields want them."""
        read: Dict[str, Any] = {}
        for field, how in FIELDS[kind]:
            raw = inputs[field].value
            if how == "bool":
                read[field] = bool(raw)
            elif how == "number":
                read[field] = parse_number(raw)
            elif how == "number_or_none":
                read[field] = parse_number(raw) if str(raw or "").strip() else None
            elif how == "text":
                read[field] = str(raw or "").strip() or None
            else:
                read[field] = str(raw or "").strip()
        return read

    def _submit(self, kind: str, name: Optional[str], values: Dict[str, Any], inputs: Dict[str, Any],
                dialog: Any) -> None:
        """Add or update; the dialog stays open on a rejection."""
        try:
            read = self._read(kind, inputs)
        except ValueError as exc:
            ui.notify(f"{self.t('edit_rejected')}: {exc}", type="negative", multi_line=True)
            return
        if name is None:
            command: Any = _ADD[kind](definition={**values, **read})
        else:
            changed = {k: v for k, v in read.items() if values.get(k) != v}
            if not changed:
                dialog.close()
                return
            command = _UPDATE[kind](name=name, fields=changed)
        if self._apply(command):
            dialog.close()

    # -- standard library -----------------------------------------------------------------

    def open_library(self, kind: str, *, for_palette: bool = False) -> None:
        """
        Pick a type of the standard library and add it to the catalogue.

        Parameters
        ----------
        kind : {"bus", "branch"}
            The catalogue.
        for_palette : bool
            Opened from the palette: the added type becomes the type of new
            elements.
        """
        types = library_types(kind)
        names = list(types)
        with self.page.dialog_host, ui.dialog() as dialog, ui.card().classes("w-[40rem] max-w-full").mark(
                "library-dialog"):
            ui.label(self.t(f"library_title_{kind}")).classes("text-base font-medium")
            pick = ui.select(names, value=names[0], label=self.t("type")).classes("w-full").mark("library-pick")
            description = ui.label(types[names[0]].description or "").classes("text-xs text-gray-600").mark(
                "library-description")
            name = ui.input(self.t("name"), value=names[0]).classes("w-full").mark("library-name")

            def picked(event: Any) -> None:
                description.set_text(types[event.value].description or "")
                name.set_value(event.value)

            pick.on_value_change(picked)
            with ui.row().classes("w-full justify-between gap-2"):
                ui.button(self.t("library_own"), icon="edit_note",
                          on_click=lambda: (dialog.close(), self.open_new(kind))).props("flat no-caps").mark(
                    "library-own")
                with ui.row().classes("gap-2"):
                    ui.button(self.t("cancel_dialog"), on_click=dialog.close).props("flat no-caps")
                    ui.button(self.t("apply"), on_click=lambda: self._add_from_library(
                        kind, types[pick.value], name.value, dialog, for_palette)).props("no-caps").mark(
                        "library-apply")
        dialog.on_value_change(lambda e: None if e.value else dialog.delete())
        dialog.open()

    def _add_from_library(self, kind: str, library_type: Any, name: str, dialog: Any, for_palette: bool) -> None:
        """Add the picked type under the given name."""
        definition = json.loads(library_type.model_dump_json())
        definition["name"] = (name or "").strip() or library_type.name
        if self._apply(_ADD[kind](definition=definition)):
            if for_palette:
                setattr(self.page.plan, f"{kind}_type", definition["name"])
                self.page.plan.refresh()
            dialog.close()

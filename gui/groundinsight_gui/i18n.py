# groundinsight_gui/i18n.py
"""
German and English texts of the GUI.

Deliberately a plain dictionary: NiceGUI ships no application i18n (its own
translations cover only its connection messages), and a dictionary is
trivially testable -- :func:`missing_keys` must be empty, a test enforces it.
A product with more languages moves to gettext catalogues; the keys stay.

Column headers are looked up by the library's column name
(:data:`COLUMN_LABELS`); a column without an entry shows its name, which for
the result columns (``EPR_V``, ``I_branch_A``) is the physics symbol anyway.
"""

from typing import Dict, List, Optional

__all__ = ["COLUMN_LABELS", "LANGUAGES", "STRINGS", "column_label", "missing_keys", "text"]

#: Supported languages, the default first.
LANGUAGES = ("de", "en")

STRINGS: Dict[str, Dict[str, str]] = {
    "de": {
        "app_title": "groundinsight",
        "new": "Neu",
        "open": "Öffnen",
        "save": "Speichern",
        "save_as": "Speichern unter …",
        "undo": "Rückgängig",
        "redo": "Wiederholen",
        "language": "Sprache",
        "unsaved": "ungespeicherte Änderungen",
        "not_saved_yet": "noch nicht gespeichert",
        "start_title": "Projekt beginnen",
        "from_template": "Aus einer Vorlage",
        "open_project": "Projekt öffnen",
        "path": "Dateipfad (.json)",
        "path_example": "/pfad/zum/projekt.json",
        "template_quickstart": "Zwei Stationen, ein Kabel, fünf Frequenzen (Quickstart der Doku)",
        "template_minimal": "Kabelstrang mit drei Knoten bei 50 Hz (Minimalbeispiel)",
        "template_mv_ring": "20-kV-Ring mit 20 Stationen, Fehler F1 an b10",
        "tab_network": "Netz",
        "tab_results": "Ergebnisse",
        "tab_check": "Prüfung",
        "table_buses": "Busse (Erder)",
        "table_branches": "Zweige (Kabel und Leitungen)",
        "table_sources": "Quellen",
        "table_faults": "Fehler",
        "table_bus_types": "Bustypen",
        "table_branch_types": "Zweigtypen",
        "frequencies": "Frequenzen",
        "remove_selected": "Ausgewählte Zeile löschen",
        "nothing_selected": "Keine Zeile ausgewählt – erst eine Zeile anklicken",
        "edit_hint": "Doppelklick bearbeitet eine Zelle, Dezimalkomma erlaubt. Jede Änderung lässt sich rückgängig machen.",
        "edit_rejected": "Änderung abgelehnt",
        "fault": "Fehlerort",
        "mode": "Phasenströme",
        "mode_auto": "Phasenleiter-Netz (empfohlen)",
        "mode_paths": "Pfadbasiert (Altverfahren)",
        "run": "Rechnen",
        "run_all": "Alle Fehlerorte",
        "cancel": "Abbrechen",
        "running": "Rechnet …",
        "cancelled": "Abgebrochen",
        "timed_out": "Zeitlimit überschritten",
        "job_failed": "Rechnung fehlgeschlagen",
        "no_result": "Noch keine Rechnung. Fehlerort wählen und „Rechnen“ drücken.",
        "stale": "Veraltet – die Eingaben wurden seit dieser Rechnung geändert. Neu rechnen.",
        "tile_epr_fault": "EPR am Fehlerort",
        "tile_epr_max": "Größte EPR im Netz",
        "tile_i_f": "Fehlerstrom I_F",
        "tile_r_epr": "Reduktionsfaktor r (EPR)",
        "tile_r_current": "r nach EN 50522 (I_E / I_F)",
        "tile_i_e": "Erdstrom I_E",
        "tile_z_e": "Erdungsimpedanz Z_E",
        "rms": "Effektivwert",
        "at": "bei",
        "at_bus": "an",
        "key_figures": "Kennzahlen je Frequenz",
        "chart_title": "Erdungsspannung (EPR) je Bus, Effektivwert",
        "bus_results": "Ergebnisse je Bus",
        "diagnostics": "Meldungen der Rechnung",
        "no_diagnostics": "Keine Meldungen.",
        "diagnostics_hint": "Meldungen gehören zum Ergebnis; ein Klick auf ein Element zeigt es in den Netztabellen.",
        "revision": "Revision",
        "input_digest": "Eingabe-Digest",
        "duration": "Rechenzeit",
        "check_title": "Vorab-Prüfung des Netzes",
        "check_ok": "Rechenbereit – keine Fehler",
        "check_errors": "Nicht rechenbar wie beschrieben",
        "check_counts": "{errors} Fehler, {warnings} Warnungen",
        "save_as_title": "Projekt speichern unter",
        "saved": "Gespeichert",
        "save_failed": "Speichern fehlgeschlagen",
        "open_failed": "Öffnen fehlgeschlagen",
        "severity_error": "Fehler",
        "severity_warning": "Warnung",
        "severity_info": "Hinweis",
        "kind_bus": "Bus",
        "kind_branch": "Zweig",
        "kind_source": "Quelle",
        "kind_fault": "Fehler",
        "kind_bus_type": "Bustyp",
        "kind_branch_type": "Zweigtyp",
        "kind_network": "Netz",
        "kind_frequency": "Frequenz",
    },
    "en": {
        "app_title": "groundinsight",
        "new": "New",
        "open": "Open",
        "save": "Save",
        "save_as": "Save as …",
        "undo": "Undo",
        "redo": "Redo",
        "language": "Language",
        "unsaved": "unsaved changes",
        "not_saved_yet": "not saved yet",
        "start_title": "Start a project",
        "from_template": "From a template",
        "open_project": "Open project",
        "path": "File path (.json)",
        "path_example": "/path/to/project.json",
        "template_quickstart": "Two substations, one cable, five frequencies (quickstart of the docs)",
        "template_minimal": "Three-bus cable line at 50 Hz (minimal example)",
        "template_mv_ring": "20 kV ring of 20 stations, fault F1 at b10",
        "tab_network": "Network",
        "tab_results": "Results",
        "tab_check": "Check",
        "table_buses": "Buses (electrodes)",
        "table_branches": "Branches (cables and lines)",
        "table_sources": "Sources",
        "table_faults": "Faults",
        "table_bus_types": "Bus types",
        "table_branch_types": "Branch types",
        "frequencies": "Frequencies",
        "remove_selected": "Delete selected row",
        "nothing_selected": "No row selected – click a row first",
        "edit_hint": "Double-click edits a cell. Every change can be undone.",
        "edit_rejected": "Edit rejected",
        "fault": "Fault",
        "mode": "Phase currents",
        "mode_auto": "Phase-conductor network (recommended)",
        "mode_paths": "Path-based (legacy)",
        "run": "Run",
        "run_all": "All faults",
        "cancel": "Cancel",
        "running": "Running …",
        "cancelled": "Cancelled",
        "timed_out": "Time limit exceeded",
        "job_failed": "Calculation failed",
        "no_result": "No calculation yet. Pick a fault and press “Run”.",
        "stale": "Stale – the inputs have changed since this result was computed. Run again.",
        "tile_epr_fault": "EPR at the fault",
        "tile_epr_max": "Largest EPR in the network",
        "tile_i_f": "Fault current I_F",
        "tile_r_epr": "Reduction factor r (EPR)",
        "tile_r_current": "r per EN 50522 (I_E / I_F)",
        "tile_i_e": "Earth current I_E",
        "tile_z_e": "Earthing impedance Z_E",
        "rms": "RMS",
        "at": "at",
        "at_bus": "at",
        "key_figures": "Key figures per frequency",
        "chart_title": "Earth potential rise (EPR) per bus, RMS",
        "bus_results": "Results per bus",
        "diagnostics": "Messages of the calculation",
        "no_diagnostics": "No messages.",
        "diagnostics_hint": "Messages belong to the result; click an element to see it in the network tables.",
        "revision": "Revision",
        "input_digest": "Input digest",
        "duration": "Solve time",
        "check_title": "Pre-flight check of the network",
        "check_ok": "Ready to solve – no errors",
        "check_errors": "Not solvable as described",
        "check_counts": "{errors} error(s), {warnings} warning(s)",
        "save_as_title": "Save project as",
        "saved": "Saved",
        "save_failed": "Saving failed",
        "open_failed": "Opening failed",
        "severity_error": "error",
        "severity_warning": "warning",
        "severity_info": "note",
        "kind_bus": "bus",
        "kind_branch": "branch",
        "kind_source": "source",
        "kind_fault": "fault",
        "kind_bus_type": "bus type",
        "kind_branch_type": "branch type",
        "kind_network": "network",
        "kind_frequency": "frequency",
    },
}

#: Column headers by the library's column name.
COLUMN_LABELS: Dict[str, Dict[str, str]] = {
    "de": {
        "name": "Name",
        "type": "Typ",
        "description": "Beschreibung",
        "specific_earth_resistance": "ρ",
        "active": "aktiv",
        "from_bus": "von",
        "to_bus": "nach",
        "length": "Länge",
        "parallel_coefficient": "Parallelfaktor",
        "bus": "Bus",
        "source_type": "Art",
        "t_k_s": "T_k [s]",
        "n_factor": "n",
        "system_type": "Anlage",
        "voltage_level": "Spannungsebene",
        "impedance_formula": "Impedanzformel Z(f, ρ)",
        "grounding_conductor": "Erdleiter",
        "self_impedance_formula": "Selbstimpedanz Z_s",
        "mutual_impedance_formula": "Koppelimpedanz Z_m",
        "phase_impedance_formula": "Phasenimpedanz Z_p",
        "frequency_Hz": "f [Hz]",
        "is_rms": "Effektivwert",
        "bus_name": "Bus",
        "branch_name": "Zweig",
    },
    "en": {
        "name": "Name",
        "type": "Type",
        "description": "Description",
        "specific_earth_resistance": "ρ",
        "active": "active",
        "from_bus": "from",
        "to_bus": "to",
        "length": "Length",
        "parallel_coefficient": "Parallel coefficient",
        "bus": "Bus",
        "source_type": "Kind",
        "t_k_s": "T_k [s]",
        "n_factor": "n",
        "system_type": "System",
        "voltage_level": "Voltage level",
        "impedance_formula": "Impedance formula Z(f, ρ)",
        "grounding_conductor": "Earthing conductor",
        "self_impedance_formula": "Self impedance Z_s",
        "mutual_impedance_formula": "Mutual impedance Z_m",
        "phase_impedance_formula": "Phase impedance Z_p",
        "frequency_Hz": "f [Hz]",
        "is_rms": "RMS",
        "bus_name": "Bus",
        "branch_name": "Branch",
    },
}


def text(language: str, key: str, **values: object) -> str:
    """
    The text of ``key`` in ``language``, with ``{placeholders}`` filled in.

    Parameters
    ----------
    language : {"de", "en"}
        The language.
    key : str
        A key of :data:`STRINGS`.
    **values
        Values for the placeholders.

    Returns
    -------
    str
        The text.

    Raises
    ------
    KeyError
        For an unknown key -- a missing text is a bug, not a fallback.
    """
    template = STRINGS[language][key]
    return template.format(**values) if values else template


def column_label(language: str, column: str, unit: Optional[str] = None) -> str:
    """
    The header of a table column.

    Parameters
    ----------
    language : {"de", "en"}
        The language.
    column : str
        The library's column name.
    unit : str, optional
        The display unit, appended in brackets.

    Returns
    -------
    str
        The header text.
    """
    label = COLUMN_LABELS[language].get(column, column)
    return f"{label} [{unit}]" if unit else label


def missing_keys() -> List[str]:
    """
    Keys that exist in one language but not in the other.

    Returns
    -------
    list of str
        ``"<language>:<key>"`` per gap; empty when the tables are complete.
    """
    missing: List[str] = []
    for tables in (STRINGS, COLUMN_LABELS):
        every_key = set().union(*(table.keys() for table in tables.values()))
        for language in LANGUAGES:
            missing += [f"{language}:{key}" for key in sorted(every_key - tables[language].keys())]
    return missing

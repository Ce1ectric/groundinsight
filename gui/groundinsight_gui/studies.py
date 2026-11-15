# groundinsight_gui/studies.py
"""
The *Calculation* menu: every study of groundinsight as a job.

Each menu entry opens a small dialog for its parameters and submits one job
request of ``groundinsight.api`` -- the same request a script or a server
would send. The job runs off the page's event loop with progress and cancel
(the page's poller), and its :class:`~groundinsight.api.StudyResultDTO` is
shown in the *Results* tab: the scalar values, every table of the study
(the library's frames, unchanged) and, where it helps, a chart of one of its
columns. Nothing is computed here; a parameter typed into a dialog is read
(numbers with a decimal comma, lists separated by commas) and handed over.

=================  =====================================================
menu entry         job request
=================  =====================================================
outage study       ``OutageStudyRequestDTO``: one scenario per branch
rho-f sweep        ``SweepRequestDTO``: rho values for chosen buses,
                   with the per-bus statistics (``summarize``)
cuts               ``CutsRequestDTO``: one cut per chosen branch
bus response       ``BusResponseRequestDTO``
max. rho scaling   ``MaxRhoRequestDTO``
thermal check      ``ThermalRequestDTO``
EN 50522 check     ``TouchCheckRequestDTO``
transient          ``TransientRequestDTO``
reference cases    ``ReferenceCasesRequestDTO``
=================  =====================================================
"""

import logging
from typing import Any, Callable, Dict, List, Optional, Tuple

from nicegui import ui

from groundinsight.api import (
    BusResponseRequestDTO,
    CharacterizeRequestDTO,
    CutDTO,
    CutsRequestDTO,
    MaxRhoRequestDTO,
    OutageScenarioDTO,
    OutageStudyRequestDTO,
    ReferenceCasesRequestDTO,
    StudyResultDTO,
    SweepPointDTO,
    SweepRequestDTO,
    ThermalRequestDTO,
    TouchCheckRequestDTO,
    TransientRequestDTO,
)

from .formatting import format_number, parse_number
from .i18n import column_label

logger = logging.getLogger(__name__)

__all__ = ["STUDIES", "StudyDialog", "parse_curve", "parse_numbers", "study_chart"]

#: The studies of the menu, in menu order.
STUDIES = ("outage", "sweep", "cuts", "bus_response", "max_rho", "thermal", "touch", "transient",
           "reference_cases")


def parse_numbers(text: str) -> List[float]:
    """
    Numbers separated by semicolons, or by commas when no decimal comma is used.

    Parameters
    ----------
    text : str
        ``"50; 100; 200"``, ``"50, 100, 200"`` or ``"0,5; 1,5"``.

    Returns
    -------
    list of float
        The numbers.

    Raises
    ------
    ValueError
        If an entry is not a number or there is none.

    Examples
    --------
    >>> parse_numbers("50, 100, 200"), parse_numbers("0,5; 1,5")
    ([50.0, 100.0, 200.0], [0.5, 1.5])
    """
    separator = ";" if ";" in text else ","
    values = [parse_number(part) for part in text.split(separator) if part.strip()]
    if not values:
        raise ValueError("no number given")
    return values


def parse_curve(text: str) -> Any:
    """
    A limit: one number, or a curve ``t:U; t:U; ...``.

    Parameters
    ----------
    text : str
        ``"80"`` or ``"0,1:600; 1:100"``.

    Returns
    -------
    float or list of (float, float)
        What ``TouchCheckRequestDTO.u_limit`` takes.

    Examples
    --------
    >>> parse_curve("80"), parse_curve("0,1:600; 1:100")
    (80.0, [(0.1, 600.0), (1.0, 100.0)])
    """
    if ":" not in text:
        return parse_number(text)
    return [(parse_number(t), parse_number(u)) for t, u in (p.split(":") for p in text.split(";") if p.strip())]


def study_chart(dto: StudyResultDTO, language: str) -> Optional[Dict[str, Any]]:
    """
    ECharts options for the result of a study, where a chart helps.

    Parameters
    ----------
    dto : StudyResultDTO
        The result.
    language : str
        For the axis names.

    Returns
    -------
    dict or None
        A bar chart of the touch-voltage utilisation per bus, a line chart of
        the transient potentials over time, a bar chart of the RMS EPR per
        bus and scenario of an outage study, or ``None``.
    """
    if dto.study == "touch":
        rows = [r for r in dto.tables["touch"].rows if r["utilisation"] is not None]
        return _bars([r["bus_name"] for r in rows], {"UvT/Ulimit": [r["utilisation"] for r in rows]},
                     column_label(language, "utilisation"))
    if dto.study == "transient":
        rows = dto.tables["series"].rows
        names = sorted({r["name"] for r in rows if r["signal_kind"] != "source"})
        series = [{"type": "line", "name": n, "showSymbol": False,
                   "data": [[r["time_s"], r["value"]] for r in rows if r["name"] == n]} for n in names]
        return {"tooltip": {"trigger": "axis"}, "legend": {"type": "scroll"},
                "xAxis": {"type": "value", "name": "t [s]"}, "yAxis": {"type": "value", "name": "V"},
                "series": series}
    if dto.study == "outage":
        # compare_buses() is long: one row per bus, frequency ("RMS" for the
        # RMS row), metric and scenario.
        rows = [r for r in dto.tables["buses"].rows if r["frequency_Hz"] == "RMS" and r["metric"] == "EPR_V"]
        buses = list(dict.fromkeys(r["bus_name"] for r in rows))
        scenarios = list(dict.fromkeys(r["scenario"] for r in rows))
        values = {s: [next((r["value"] for r in rows if r["scenario"] == s and r["bus_name"] == b), None)
                      for b in buses] for s in scenarios}
        return _bars(buses, values, "EPR [V]")
    return None


def _bars(categories: List[str], series: Dict[str, List[Any]], axis: str) -> Dict[str, Any]:
    """ECharts options of grouped bars."""
    return {"tooltip": {"trigger": "axis"}, "legend": {"type": "scroll"},
            "grid": {"left": 56, "right": 16, "top": 40, "bottom": 60},
            "xAxis": {"type": "category", "data": categories, "axisLabel": {"rotate": 45}},
            "yAxis": {"type": "value", "name": axis},
            "series": [{"type": "bar", "name": name, "data": data} for name, data in series.items()]}


class StudyDialog:
    """
    The parameter dialog of one study.

    Parameters
    ----------
    page : ProjectPage
        The page: session, texts, the selected fault, ``submit_study``.
    study : str
        One of :data:`STUDIES`.
    """

    def __init__(self, page: Any, study: str, owner: Optional[str] = None) -> None:
        self.page = page
        self.study = study
        self.owner = owner
        self.inputs: Dict[str, Any] = {}

    def t(self, key: str, **values: Any) -> str:
        """The text of ``key`` in the page's language."""
        return self.page.t(key, **values)

    def _names(self, table: str) -> List[str]:
        return [r["name"] for r in self.page.session.table(table).rows]

    def _fault(self) -> None:
        faults = self.page.session.fault_names()
        self.inputs["fault"] = ui.select(faults, value=self.page.fault or (faults[0] if faults else None),
                                         label=self.t("fault")).classes("w-full").mark("study-fault")

    def _multi(self, key: str, table: str, label: str) -> None:
        self.inputs[key] = ui.select(self._names(table), multiple=True, value=[], label=label).props(
            "use-chips").classes("w-full").mark(f"study-{key}")

    def _text(self, key: str, label: str, value: str) -> None:
        self.inputs[key] = ui.input(label, value=value).classes("w-full").mark(f"study-{key}")

    def open(self) -> None:
        """Build and open the dialog."""
        with self.page.dialog_host, ui.dialog() as self.dialog, ui.card().classes("w-[34rem]").mark(
                "study-dialog"):
            ui.label(self.t(f"study_{self.study}")).classes("text-base font-medium")
            ui.label(self.t(f"study_hint_{self.study}")).classes("text-xs text-gray-500")
            getattr(self, f"_fields_{self.study}")()
            with ui.row().classes("w-full justify-end gap-2"):
                ui.button(self.t("cancel_dialog"), on_click=self.dialog.close).props("flat no-caps")
                ui.button(self.t("run"), icon="play_arrow", on_click=self.submit).props("no-caps").mark(
                    "study-run")
        self.dialog.on_value_change(lambda e: None if e.value else self.dialog.delete())
        self.dialog.open()

    def submit(self) -> None:
        """Build the request and hand it to the page; report what cannot be read."""
        try:
            request = getattr(self, f"_request_{self.study}")()
        except ValueError as exc:
            ui.notify(f"{self.t('edit_rejected')}: {exc}", type="negative", multi_line=True)
            return
        if self.page.submit_study(request, self.t(f"study_{self.study}")):
            self.dialog.close()

    def value(self, key: str) -> Any:
        """The value of an input."""
        return self.inputs[key].value

    # -- the studies ----------------------------------------------------------------

    def _fields_outage(self) -> None:
        self._fault()
        self._multi("branches", "branches", self.t("study_outage_branches"))

    def _request_outage(self) -> OutageStudyRequestDTO:
        branches = list(self.value("branches") or [])
        if not branches:
            raise ValueError(self.t("study_choose_branches"))
        return OutageStudyRequestDTO(fault=self.value("fault"), scenarios=[
            OutageScenarioDTO(name=f"{b} open", disabled_branches=[b]) for b in branches])

    def _fields_sweep(self) -> None:
        self._fault()
        self._multi("buses", "buses", self.t("study_sweep_buses"))
        self._text("rho", self.t("study_rho_values"), "50; 100; 200; 400; 800")

    def _request_sweep(self) -> SweepRequestDTO:
        buses = list(self.value("buses") or []) or self._names("buses")
        points = [SweepPointDTO(label=f"rho {format_number(r, 'en', 6)}", bus_rho={b: r for b in buses},
                                parameters={"rho_Ohm_m": r})
                  for r in parse_numbers(self.value("rho"))]
        return SweepRequestDTO(fault=self.value("fault"), points=points)

    def _fields_cuts(self) -> None:
        self._fault()
        self._multi("branches", "branches", self.t("study_cut_branches"))

    def _request_cuts(self) -> CutsRequestDTO:
        branches = list(self.value("branches") or [])
        if not branches:
            raise ValueError(self.t("study_choose_branches"))
        return CutsRequestDTO(fault=self.value("fault"), cuts=[CutDTO(name=b, branches=[b]) for b in branches])

    def _fields_bus_response(self) -> None:
        self._fault()
        self.inputs["bus"] = ui.select([""] + self._names("buses"), value="", label=self.t("study_bus")).classes(
            "w-full").mark("study-bus")
        self._text("electrodes", self.t("study_electrodes"), "0,5; 1; 2; 5")

    def _request_bus_response(self) -> BusResponseRequestDTO:
        values = parse_numbers(self.value("electrodes")) if (self.value("electrodes") or "").strip() else []
        return BusResponseRequestDTO(fault=self.value("fault"), bus=self.value("bus") or None,
                                     electrodes={f"{format_number(z, 'en', 6)} Ohm": z for z in values})

    def _fields_max_rho(self) -> None:
        self._fault()
        self._multi("buses", "buses", self.t("study_scaled_buses"))
        self._text("u_limit", self.t("study_epr_limit"), "1000")

    def _request_max_rho(self) -> MaxRhoRequestDTO:
        buses = list(self.value("buses") or []) or self._names("buses")
        return MaxRhoRequestDTO(fault=self.value("fault"), bus_names=buses,
                                u_limit=parse_number(self.value("u_limit")))

    def _fields_thermal(self) -> None:
        self._fault()
        self.inputs["scope"] = ui.toggle({"conductors": self.t("study_conductors"), "nodes": self.t("study_nodes")},
                                         value="conductors").mark("study-scope")
        self._text("t_k_s", self.t("study_t_k"), "1")
        self._text("r_to_x", "R/X", "0,1")

    def _request_thermal(self) -> ThermalRequestDTO:
        r_to_x = (self.value("r_to_x") or "").strip()
        return ThermalRequestDTO(fault=self.value("fault"), scope=self.value("scope"),
                                 t_k_s=parse_number(self.value("t_k_s")),
                                 r_to_x=parse_number(r_to_x) if r_to_x else None)

    def _fields_touch(self) -> None:
        self._fault()
        self._text("u_limit", self.t("study_touch_limit"), "")
        self._text("t_k_s", self.t("study_t_f"), "0,5")

    def _request_touch(self) -> TouchCheckRequestDTO:
        if not (self.value("u_limit") or "").strip():
            raise ValueError(self.t("study_limit_needed"))
        return TouchCheckRequestDTO(fault=self.value("fault"), u_limit=parse_curve(self.value("u_limit")),
                                    t_k_s=parse_number(self.value("t_k_s")))

    def _fields_transient(self) -> None:
        self._fault()
        self.inputs["waveform"] = ui.toggle({"step": self.t("study_step"), "sine": self.t("study_sine")},
                                            value="step").mark("study-waveform")
        self._text("amplitude", self.t("study_amplitude"), "1000")
        self._text("t_off", self.t("study_t_off"), "0,02")
        self._text("t_end", self.t("study_t_end"), "0,1")
        self._text("dt", self.t("study_dt"), "0,0001")

    def _request_transient(self) -> TransientRequestDTO:
        t_off = (self.value("t_off") or "").strip()
        return TransientRequestDTO(fault=self.value("fault"), waveform=self.value("waveform"),
                                   amplitude=parse_number(self.value("amplitude")),
                                   t_off=parse_number(t_off) if t_off else None,
                                   t_end=parse_number(self.value("t_end")), dt=parse_number(self.value("dt")))

    def _fields_reference_cases(self) -> None:
        pass

    def _fields_characterize(self) -> None:
        bus = next(r for r in self.page.session.table("buses").rows if r["name"] == self.owner)
        self.inputs["target"] = ui.toggle({"bus": self.t("char_for_bus", name=self.owner),
                                           "bus_type": self.t("char_for_type", name=bus["type"])},
                                          value="bus").mark("study-target")
        self.inputs["electrode"] = ui.toggle({"ring": self.t("electrode_ring"),
                                              "foundation": self.t("electrode_foundation"),
                                              "rod": self.t("electrode_rod")}, value="ring").mark("study-electrode")
        self._text("dimension", self.t("electrode_dimension"), "")
        self.inputs["soil"] = ui.toggle({"homogeneous": self.t("soil_homogeneous"),
                                         "two_layer": self.t("soil_two_layer")}, value="two_layer").mark("study-soil")
        with ui.row().classes("w-full no-wrap gap-2"):
            self._text("rho_1", "ρ1 / Ω·m", "100")
            self._text("rho_2", "ρ2 / Ω·m", "500")
            self._text("h_1", "h1 / m", "2")
        self._text("factors", self.t("char_factors"), "0,5; 1; 2; 4")
        frequencies = "; ".join(format_number(f, self.page.language, 6)
                                for f in self.page.session.summary().frequencies)
        self._text("frequencies", self.t("frequencies") + " / Hz", frequencies)
        self._text("transfer", self.t("char_transfer"), "")
        self.inputs["backend"] = ui.toggle({"image": "image", "mom": "mom"}, value="image").mark("study-backend")

    def _request_characterize(self) -> CharacterizeRequestDTO:
        kind = self.value("electrode")
        dimension = (self.value("dimension") or "").strip()
        key = {"ring": "radius_m", "foundation": "size_m", "rod": "length_m"}[kind]
        transfer = (self.value("transfer") or "").strip()
        bus = next(r for r in self.page.session.table("buses").rows if r["name"] == self.owner)
        target = self.value("target")
        two_layer = self.value("soil") == "two_layer"
        return CharacterizeRequestDTO(
            electrode=kind, dimensions={key: parse_number(dimension)} if dimension else {},
            soil=self.value("soil"), rho_1=parse_number(self.value("rho_1")),
            rho_2=parse_number(self.value("rho_2")) if two_layer else None,
            h_1=parse_number(self.value("h_1")) if two_layer else None,
            factors=parse_numbers(self.value("factors")), frequencies=parse_numbers(self.value("frequencies")),
            rho_preset=("station" if kind == "ring" else "lv_network") if two_layer else None,
            backend=self.value("backend"), transfer_distances=parse_numbers(transfer) if transfer else [],
            target=target, owner=self.owner if target == "bus" else bus["type"],
            name=f"{self.t('electrode_' + kind)} ({'ρ1/ρ2' if two_layer else 'ρ'})")

    def _request_reference_cases(self) -> ReferenceCasesRequestDTO:
        return ReferenceCasesRequestDTO()


def study_tables(dto: StudyResultDTO) -> List[Tuple[str, Any]]:
    """
    The tables of a study result, in the order they are shown.

    Parameters
    ----------
    dto : StudyResultDTO
        The result.

    Returns
    -------
    list of (str, TableDTO)
        Name and table; the statistics of a sweep first.
    """
    order = ["summary", "touch", "limits", "cuts", "extremes", "sweep", "cases", "rho_max", "buses",
             "branches", "impedances", "series"]
    names = sorted(dto.tables, key=lambda n: order.index(n) if n in order else len(order))
    return [(name, dto.tables[name]) for name in names]


def grid_options(table: Any, language: str, format_cell: Callable[[str], str]) -> Dict[str, Any]:
    """
    AG Grid options of a read-only study table.

    Parameters
    ----------
    table : TableDTO
        The table.
    language : str
        For the headers.
    format_cell : callable
        Locale string for the number formatter (``"de-DE"``).

    Returns
    -------
    dict
        The options; rows unchanged.
    """
    locale = format_cell(language)
    columns = []
    for column in table.columns:
        definition = {"field": column.name, "headerName": column_label(language, column.name),
                      "sortable": True, "resizable": True}
        if column.dtype in ("float", "int"):
            definition["type"] = "numericColumn"
            definition[":valueFormatter"] = (
                f"p => p.value == null ? '–' : Number(p.value).toLocaleString('{locale}', "
                "{maximumSignificantDigits: 6, useGrouping: false})")
        columns.append(definition)
    return {"columnDefs": columns, "rowData": table.rows,
            "defaultColDef": {"minWidth": 90, "flex": 1, "wrapHeaderText": True, "autoHeaderHeight": True}}

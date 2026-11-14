# docs/hooks/result_tables.py
"""
MkDocs build hook: the column tables of the result-table reference.

``reference/results.md`` documents every result frame of the package -- the
``res_*`` accessors of ``Network``, the study results, the thermal checks, the
importer frames. A hand-written column list goes stale the first time a column
is added, renamed or retyped, and the frames are what reports and front ends
bind to. This hook therefore writes each table at build time where the page
carries a marker ``<!-- result-table: <key> -->``:

* the **columns and dtypes** come from the code -- from the schema constant
  that pins the frame (``RES_BUSES_SCHEMA``, ``RES_FAULT_SCHEMA``,
  ``CUT_FRAME_SCHEMA`` ...) or, for the frames that have none, from a frame
  built on a small probe network when the site is built;
* the **unit and meaning** of each column come from :data:`FRAMES` below.

A column without a description, a description of a column that does not
exist, or an unknown marker raises, so a strict build stops instead of
publishing an incomplete table. ``tests/test_audit_pass19_docs_reference.py``
runs the same checks in the test suite and compares the constants with the
frames of a real solve.

Registration: until ``mkdocs.yml`` lists this file under ``hooks:``, the
registered hook ``reference_cases.py`` calls :func:`render`; calling it twice
is harmless, because the second call finds no marker.
"""

import logging
import re
import sys
import warnings
from pathlib import Path
from typing import Callable, Dict, NamedTuple, Optional, Tuple

logger = logging.getLogger("mkdocs.hooks.result_tables")

_SRC = Path(__file__).resolve().parents[2] / "src"
_MARKER = re.compile(r"<!-- result-table: ([\w-]+) -->")

#: Unit marker for dimensionless columns and text.
NO_UNIT = "–"


class FrameSpec(NamedTuple):
    """
    How one result frame is documented.

    Attributes
    ----------
    source : tuple of (str, str) or callable
        ``(module, constant)`` of the schema constant, or a callable that
        takes the ``groundinsight`` module and returns a frame whose schema
        is documented.
    columns : dict of str to tuple of (str, str)
        Column -> (unit, meaning), in the order of the frame.
    """

    source: object
    columns: Dict[str, Tuple[str, str]]


def _import_groundinsight():
    """
    Import ``groundinsight`` from the source tree next to ``mkdocs.yml``.

    Returns
    -------
    module
        The ``groundinsight`` package.

    Raises
    ------
    RuntimeError
        If ``groundinsight`` was already imported from another location.
    """
    if str(_SRC) not in sys.path:
        sys.path.insert(0, str(_SRC))
    import groundinsight

    location = Path(groundinsight.__file__).resolve()
    if _SRC not in location.parents:
        raise RuntimeError(
            f"The result tables would be built from {location}, not from the "
            f"documented source tree {_SRC}."
        )
    return groundinsight


# --- probe frames for the results without a schema constant -----------------


def _probe_network(gi, bus_name: str = "B"):
    """A three-bus line with one fault, unsolved."""
    station = gi.BusType(name="probe", system_type="Station", voltage_level=20.0,
                         impedance_formula="2.0", R_formula="2.0")
    cable = gi.BranchType(
        name="probe", grounding_conductor=True,
        self_impedance_formula="(0.2 + j*2*pi*f*1e-3)*l", mutual_impedance_formula="0",
        R_self_formula="0.2*l", L_self_formula="1e-3*l",
    )
    net = gi.create_network(name="probe", frequencies=[50.0])
    for name in ("A", bus_name, "C"):
        gi.create_bus(name=name, type=station, network=net)
    gi.create_branch(name="AB", type=cable, from_bus="A", to_bus=bus_name, length=1.0, network=net)
    gi.create_branch(name="BC", type=cable, from_bus=bus_name, to_bus="C", length=1.0, network=net)
    gi.create_source(name="Q", bus="A", values={50.0: 100.0}, network=net)
    gi.create_fault(name="F", bus="C", network=net)
    return net


def _outage(gi):
    net = _probe_network(gi)
    return gi.run_outage_study(net, fault="F", scenarios=[gi.Outage(name="x", disabled_branches=["BC"])])


def _compare_buses(gi):
    return _outage(gi).compare_buses()


def _compare_branches(gi):
    return _outage(gi).compare_branches()


def _transient(gi):
    net = _probe_network(gi)
    study = gi.TransientStudy(net, "F")
    study.set_source_waveform("Q", gi.waveforms.step(amplitude=1.0))
    study.set_observation(buses=["C"], branches=["BC"])
    return study.solve(t_end=0.01, dt=1e-3, solver="fft").to_polars()


def _reference_cases(gi):
    return gi.run_reference_cases()


def _rho_f_catalog(gi):
    net = _probe_network(gi, bus_name="<bus>")
    return gi.select_rho_f_from_catalog(net, bus_names=["<bus>"], u_limit=1.0, candidates={})


# --- the descriptions ---------------------------------------------------------

_RMS_ROW = "null on the RMS row"

_RES_FREQUENCY = {
    "frequency_Hz": ("Hz", f"frequency of the row; {_RMS_ROW}"),
    "is_rms": (NO_UNIT, "true on the one RMS row per element, which combines all frequencies as √Σ|X(f)|²"),
}

FRAMES: Dict[str, FrameSpec] = {
    "res_buses": FrameSpec(("groundinsight.models.core_models", "RES_BUSES_SCHEMA"), {
        "bus_name": (NO_UNIT, "name of the bus"),
        "fault": (NO_UNIT, "name of the fault the result belongs to"),
        **_RES_FREQUENCY,
        "EPR_V": ("V", "earth potential rise of the bus, `ResultBus.uepr_freq`; RMS row: `ResultBus.uepr`"),
        "EPR_degree": ("°", f"angle of the EPR in the earth-side sign frame (see Conventions); {_RMS_ROW}"),
        "I_bus_A": ("A", "electrode current: what the bus's own electrode passes into the soil, `ResultBus.ia_freq`; summed over all buses it is zero"),
        "I_bus_degree": ("°", f"angle of `I_bus_A`; {_RMS_ROW}"),
        "I_inj_A": ("A", "current of the earthing conductor at a source or fault bus, `ResultBus.i_inj_freq` -- the source current at a source bus, the fault current at the fault bus, 0 elsewhere; the quantity a conductor is sized for"),
        "I_inj_degree": ("°", f"angle of `I_inj_A`; {_RMS_ROW}"),
    }),
    "res_branches": FrameSpec(("groundinsight.models.core_models", "RES_BRANCHES_SCHEMA"), {
        "branch_name": (NO_UNIT, "name of the branch"),
        "fault": (NO_UNIT, "name of the fault the result belongs to"),
        **_RES_FREQUENCY,
        "I_branch_A": ("A", "current of the earthing conductor (cable screen, earth wire) of the branch, `ResultBranch.i_s_freq`; counted positive from `from_bus` to `to_bus` for the physical fault"),
        "I_branch_degree": ("°", f"angle of `I_branch_A`; {_RMS_ROW}"),
    }),
    "res_all_impedances": FrameSpec(("groundinsight.models.core_models", "RES_ALL_IMPEDANCES_SCHEMA"), {
        "fault_name": (NO_UNIT, "name of the fault (this frame says `fault_name`, the others `fault`)"),
        "fault_bus": (NO_UNIT, "bus the fault sits on"),
        "frequency_Hz": ("Hz", "frequency of the row; the frame has no RMS row"),
        "grounding_impedance_Ohm": ("Ω", "earthing impedance Z_E of EN 50522 at the fault bus: the driving-point impedance of the earthing network seen from there (`ResultGroundingImpedance.value`, equal to `z_earthing`); null where it is undefined"),
        "grounding_impedance_deg": ("°", "angle of Z_E"),
        "reduction_factor": (NO_UNIT, "`value`: EPR at the fault bus with the inductive coupling over the EPR without it -- a property of the line, **not** the r of EN 50522"),
        "reduction_factor_current": (NO_UNIT, "`value_current`: |I_E| / |I_F|, the reduction factor r of EN 50522"),
    }),
    "res_fault": FrameSpec(("groundinsight.models.core_models", "RES_FAULT_SCHEMA"), {
        "fault": (NO_UNIT, "name of the fault"),
        "fault_bus": (NO_UNIT, "bus the result was computed for"),
        **_RES_FREQUENCY,
        "I_F_A": ("A", "fault current I_F (3I_0 of EN 50522): the currents of the sources that reach the fault, times the fault scalings (`ResultReductionFactor.i_fault`); RMS row √Σ|I_F(f)|²"),
        "I_F_degree": ("°", f"angle of I_F; {_RMS_ROW}"),
        "EPR_fault_V": ("V", "earth potential rise of the fault bus; RMS row: its stored RMS value"),
        "EPR_fault_degree": ("°", f"angle of that EPR; {_RMS_ROW}"),
        "EPR_max_V": ("V", "largest earth potential rise of any bus at that frequency (RMS row: largest RMS value)"),
        "EPR_max_bus": (NO_UNIT, "the bus where `EPR_max_V` occurs"),
        "r_epr": (NO_UNIT, f"EPR-based reduction factor `value` -- not the r of EN 50522; {_RMS_ROW}"),
        "r_current": (NO_UNIT, f"current-based reduction factor `value_current` = |I_E| / |I_F|, the r of EN 50522; {_RMS_ROW} and where it is undefined"),
        "I_E_A": ("A", "earth-return current I_E (`i_earth`); RMS row √Σ|I_E(f)|²"),
        "U_E_V": ("V", "earthing voltage U_E of EN 50522: the EPR at the fault bus (`u_earthing`); RMS row √Σ|U_E(f)|², the value compared with U_Tp"),
        "Z_E_Ohm": ("Ω", f"earthing impedance Z_E of EN 50522, the driving point of the earthing network at the fault bus (`z_earthing`); {_RMS_ROW}"),
        "Z_E_degree": ("°", f"angle of Z_E; {_RMS_ROW}"),
        "n_diagnostics": (NO_UNIT, "number of records in `Result.diagnostics`, the same on every row"),
    }),
    "compare_buses": FrameSpec(_compare_buses, {
        "bus_name": (NO_UNIT, "name of the bus"),
        "frequency_Hz": (NO_UNIT, 'frequency as **text** (`"50.0"`) or `"RMS"` -- the join key of the comparison, not a number'),
        "metric": (NO_UNIT, "the compared `res_buses` column, `EPR_V` unless `columns=` names others"),
        "value": ("unit of the metric", "the metric in this scenario"),
        "scenario": (NO_UNIT, "scenario label (`base` for the unmodified network)"),
        "delta_vs_base": ("unit of the metric", "value minus the reference scenario's value; the suffix is the reference label (`against=`)"),
        "delta_pct_vs_base": ("%", "100 · delta / reference value; null where the reference value is 0"),
    }),
    "compare_branches": FrameSpec(_compare_branches, {
        "branch_name": (NO_UNIT, "name of the branch"),
        "frequency_Hz": (NO_UNIT, 'frequency as **text** (`"50.0"`) or `"RMS"`'),
        "metric": (NO_UNIT, "the compared `res_branches` column, `I_branch_A` unless `columns=` names others"),
        "value": ("unit of the metric", "the metric in this scenario"),
        "scenario": (NO_UNIT, "scenario label"),
        "delta_vs_base": ("unit of the metric", "value minus the reference scenario's value"),
        "delta_pct_vs_base": ("%", "100 · delta / reference value; null where the reference value is 0"),
    }),
    "cut_analysis": FrameSpec(("groundinsight.analysis.decomposition", "CUT_FRAME_SCHEMA"), {
        "fault": (NO_UNIT, "the analysed fault"),
        "fault_bus": (NO_UNIT, "bus the fault sits on, the reference point of the decomposition"),
        "cut": (NO_UNIT, "name of the direction (a `Cut`, or the implicit `rest` / `source_loop` side)"),
        "side_buses": (NO_UNIT, "buses seen through that cut, comma-separated"),
        "n_side_buses": (NO_UNIT, "number of buses on that side"),
        "branches": (NO_UNIT, "branches of the cut, comma-separated"),
        "sides_are_disjoint": (NO_UNIT, "true when no two directions reach the same bus; the far-side currents are defined only then"),
        "frequency_Hz": ("Hz", "frequency of the row (no RMS row)"),
        "Z_side_Ohm": ("Ω", "parallel impedance of that direction, from source-free current division"),
        "Z_side_deg": ("°", "its angle"),
        "Z_local_Ohm": ("Ω", "impedance of the fault bus's own electrode"),
        "Z_parallel_Ohm": ("Ω", "Z_local in parallel with every Z_side"),
        "Z_driving_point_Ohm": ("Ω", "driving-point impedance of the solved nodal system at the fault bus"),
        "identity_residual": (NO_UNIT, "|Z_parallel − Z_driving_point| / |Z_driving_point|; grows when the decomposition misses a current path"),
        "I_fault_A": ("A", "injection at the fault bus"),
        "I_local_A": ("A", "part of it taken by the fault bus's own electrode"),
        "I_shield_A": ("A", "shield current leaving the fault bus in that direction"),
        "I_shield_deg": ("°", "its angle"),
        "current_share": (NO_UNIT, "|I_shield| / |I_fault|"),
        "I_earth_A": ("A", "soil current crossing the cut, summed over the far side; null when the directions overlap"),
        "I_total_A": ("A", "total current crossing the cut; null when the directions overlap"),
        "r_side": (NO_UNIT, "|I_earth| / |I_total|, the earth-return share of that direction"),
        "kcl_residual": (NO_UNIT, "|I_fault − I_local − Σ I_shield| / |I_fault|; zero confirms the split accounts for the whole fault current"),
    }),
    "conductor_limits": FrameSpec(("groundinsight.analysis.thermal", "_BRANCH_SCHEMA"), {
        "branch_name": (NO_UNIT, "name of the branch"),
        "I_s_rms_A": ("A", "RMS of the earthing-conductor current over all frequencies, 0 Hz included"),
        "I_dc_A": ("A", "the 0 Hz component, taken out of the adiabatic check (`THERMAL_DC_EXCLUDED`)"),
        "I_ac_rms_A": ("A", "RMS of the alternating components, the I_k of the check"),
        "i_p_A": ("A", "peak current κ·√2·I_ac_rms, input for an electrodynamic check"),
        "kappa": (NO_UNIT, "peak factor κ of IEC 60909-0"),
        "m": (NO_UNIT, "DC heat factor m of IEC 60909-0"),
        "n": (NO_UNIT, "AC heat factor n of IEC 60909-0"),
        "t_k_s": ("s", "short-circuit duration T_k"),
        "I_th_factor": (NO_UNIT, "√(m + n), the factor from I_ac_rms to I_th"),
        "I_th_A": ("A", "thermal equivalent short-time current I_ac_rms·√(m + n)"),
        "material": (NO_UNIT, "conductor material of the branch type, null when not declared"),
        "cross_section_mm2": ("mm²", "cross-section of the branch type, null when not declared"),
        "k": ("A·√s/mm²", "material constant k of IEC 60949 for the initial and final temperatures"),
        "I_admissible_A": ("A", "admissible short-time current k·S/√t_k"),
        "utilization": (NO_UNIT, "I_th / I_admissible"),
        "within_limit": (NO_UNIT, "utilization ≤ 1; null when the branch type declares no thermal data"),
    }),
    "node_limits": FrameSpec(("groundinsight.analysis.thermal", "_NODE_SCHEMA"), {
        "bus_name": (NO_UNIT, "name of the bus"),
        "element": (NO_UNIT, "`earthing_conductor` (carries `I_inj`) or `electrode` (carries `I_bus`)"),
        "I_rms_A": ("A", "RMS of the element's current over all frequencies, before the split"),
        "current_split": (NO_UNIT, "share of that current one physical conductor carries (`*_current_split` of the bus type)"),
        "I_conductor_A": ("A", "I_rms · current_split"),
        "I_dc_A": ("A", "the 0 Hz component, taken out of the adiabatic check"),
        "I_ac_rms_A": ("A", "RMS of the alternating components"),
        "i_p_A": ("A", "peak current κ·√2·I_ac_rms"),
        "kappa": (NO_UNIT, "peak factor κ"),
        "m": (NO_UNIT, "DC heat factor m"),
        "n": (NO_UNIT, "AC heat factor n"),
        "t_k_s": ("s", "short-circuit duration T_k"),
        "I_th_factor": (NO_UNIT, "√(m + n), the factor from I_ac_rms to I_th"),
        "I_th_A": ("A", "thermal equivalent short-time current"),
        "material": (NO_UNIT, "material declared for the element, null when not declared"),
        "cross_section_mm2": ("mm²", "cross-section declared for the element"),
        "k": ("A·√s/mm²", "material constant k of IEC 60949"),
        "I_admissible_A": ("A", "admissible short-time current k·S/√t_k"),
        "utilization": (NO_UNIT, "I_th / I_admissible"),
        "within_limit": (NO_UNIT, "utilization ≤ 1; null when the element is not declared"),
    }),
    "transient": FrameSpec(_transient, {
        "time_s": ("s", "time of the sample"),
        "signal_kind": (NO_UNIT, "`epr` for a bus potential, `i_branch` for a branch current, `source` for a source waveform"),
        "name": (NO_UNIT, "the observed bus, branch or source"),
        "value": ("V or A", "instantaneous value (not RMS), in the earth-side frame of the stationary results"),
    }),
    "reference_cases": FrameSpec(_reference_cases, {
        "case": (NO_UNIT, "name of the reference case"),
        "quantity": (NO_UNIT, "the compared quantity, with its unit"),
        "conditions": (NO_UNIT, "conditions under which the closed form holds"),
        "closed_form": ("unit of the quantity", "value of the closed form"),
        "model": ("unit of the quantity", "value the solver computes"),
        "rel_deviation": (NO_UNIT, "|model − closed form| / |closed form|"),
        "tolerance": (NO_UNIT, "largest admitted relative deviation"),
        "agrees": (NO_UNIT, "rel_deviation ≤ tolerance"),
    }),
    "rho_f_catalog": FrameSpec(_rho_f_catalog, {
        "name": (NO_UNIT, "name of the catalogue entry"),
        "k1": ("Ω/(Ω·m)", "coefficient of ρ in Z = k1·ρ + (k2 + j·k3)·f + (k4 + j·k5)·ρ·f"),
        "k2": ("Ω/Hz", "coefficient of f, real part"),
        "k3": ("Ω/Hz", "coefficient of f, imaginary part"),
        "k4": ("Ω/(Ω·m·Hz)", "coefficient of ρ·f, real part"),
        "k5": ("Ω/(Ω·m·Hz)", "coefficient of ρ·f, imaginary part"),
        "max_epr_rms_V": ("V", "largest RMS EPR over the swept fault buses"),
        "admissible": (NO_UNIT, "`max_epr_rms_V` ≤ `u_limit`"),
        "epr_<bus>_V": ("V", "RMS EPR with the fault at that bus, one column per entry of `bus_names`"),
    }),
    "pandapower_preview": FrameSpec(("groundinsight.io.pandapower_import", "_PREVIEW_SCHEMA"), {
        "kind": (NO_UNIT, "`bus`, `line`, `switch`, `trafo` or `impedance`"),
        "status": (NO_UNIT, "`keep` or `skip`"),
        "pp_index": (NO_UNIT, "row index in the pandapower table"),
        "name": (NO_UNIT, "name the element gets (or had)"),
        "vn_kv": ("kV", "nominal voltage of a bus, null for the other kinds"),
        "from_bus": (NO_UNIT, "first bus of a line or bond"),
        "to_bus": (NO_UNIT, "second bus of a line or bond"),
        "length_km": ("km", "length the branch gets"),
        "in_service": (NO_UNIT, "pandapower's in_service, becomes `active`"),
        "reason": (NO_UNIT, "why a row is skipped, see the reason table on the I/O page"),
    }),
    "shortcircuit_results": FrameSpec(("groundinsight.io.pandapower_sc", "_SC_SCHEMA"), {
        "pp_bus_index": (NO_UNIT, "pandapower bus index"),
        "bus_name": (NO_UNIT, "bus name, the key to the groundinsight network"),
        "vn_kv": ("kV", "nominal voltage"),
        "fault_type": (NO_UNIT, "fault type of the solved case, e.g. `1ph`"),
        "case": (NO_UNIT, "`max` or `min`"),
        "i_k_a": ("A", "initial short-circuit current I_k'' (pandapower's `ikss_ka` in A)"),
        "r1_ohm": ("Ω", "positive-sequence resistance at the bus"),
        "x1_ohm": ("Ω", "positive-sequence reactance"),
        "r0_ohm": ("Ω", "zero-sequence resistance (earth faults)"),
        "x0_ohm": ("Ω", "zero-sequence reactance"),
        "r_to_x": (NO_UNIT, "R/X of the fault loop; for `1ph` the earth-fault loop (2R1 + R0)/(2X1 + X0)"),
        "kappa": (NO_UNIT, "peak factor κ"),
        "kappa_origin": (NO_UNIT, "`pandapower`, `iec_closed_form` or `unavailable`"),
        "t_k_s": ("s", "short-circuit duration of the case"),
        "n_factor": (NO_UNIT, "AC heat factor n"),
        "m": (NO_UNIT, "DC heat factor m"),
        "i_p_a": ("A", "peak short-circuit current"),
        "i_th_a": ("A", "thermal equivalent short-time current"),
    }),
    "shortcircuit_report": FrameSpec(("groundinsight.io.pandapower_sc", "_REPORT_SCHEMA"), {
        "fault_name": (NO_UNIT, "the fault the characteristics were written for"),
        "source_name": (NO_UNIT, "a source that feeds it"),
        "bus": (NO_UNIT, "the source's bus"),
        "frequency": ("Hz", "frequency whose source value is meant"),
        "i_k_total_a": ("A", "I_k'' at the fault bus"),
        "share": (NO_UNIT, "this source's share of it"),
        "i_k_previous_a": ("A", "the source value before the call"),
        "i_k_a": ("A", "the source's share of I_k''"),
        "r_to_x": (NO_UNIT, "loop R/X written onto the source"),
        "kappa": (NO_UNIT, "κ written onto the source"),
        "kappa_origin": (NO_UNIT, "where κ came from"),
        "t_k_s": ("s", "short-circuit duration written onto the fault"),
        "n_factor": (NO_UNIT, "AC heat factor written onto the fault"),
        "values_updated": (NO_UNIT, "whether `Source.values` was overwritten (`set_source_values=True`)"),
    }),
}


def schema_of(key: str):
    """
    The schema of a documented frame, from the code.

    Parameters
    ----------
    key : str
        A key of :data:`FRAMES`.

    Returns
    -------
    dict
        Column name -> polars dtype, in frame order.

    Raises
    ------
    ValueError
        If ``key`` is unknown.
    """
    if key not in FRAMES:
        raise ValueError(f"unknown result table {key!r}; known: {', '.join(FRAMES)}")
    gi = _import_groundinsight()
    source = FRAMES[key].source
    if callable(source):
        # The probe solves only exist for their schema; what they warn about
        # (a short FFT window, say) is not the reader's business.
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            return dict(source(gi).schema)
    module_name, constant = source
    module = __import__(module_name, fromlist=[constant])
    return dict(getattr(module, constant))


def _cell(text: str) -> str:
    """Escape a string for a Markdown table cell."""
    return " ".join(str(text).split()).replace("|", r"\|")


def table_markdown(key: str) -> str:
    """
    The column table of one frame.

    Parameters
    ----------
    key : str
        A key of :data:`FRAMES`.

    Returns
    -------
    str
        A Markdown table: column, dtype, unit, meaning.

    Raises
    ------
    ValueError
        If the descriptions and the columns of the frame differ.
    """
    schema = schema_of(key)
    described = FRAMES[key].columns
    if list(schema) != list(described):
        raise ValueError(
            f"result table {key!r}: the frame has the columns {list(schema)}, the "
            f"descriptions cover {list(described)}; update docs/hooks/result_tables.py"
        )
    lines = ["| Column | Type | Unit | Meaning |", "|---|---|---|---|"]
    for column, dtype in schema.items():
        unit, meaning = described[column]
        lines.append(f"| `{column}` | {dtype} | {_cell(unit)} | {_cell(meaning)} |")
    return "\n".join(lines) + "\n"


def render(markdown: str) -> str:
    """
    Replace every result-table marker of a page with its table.

    Parameters
    ----------
    markdown : str
        Markdown source.

    Returns
    -------
    str
        The source with the markers replaced; unchanged without a marker.
    """
    return _MARKER.sub(lambda match: table_markdown(match.group(1)), markdown)


def on_page_markdown(markdown, *, page, config, files):
    """
    MkDocs event: write the tables into the page.

    Parameters
    ----------
    markdown : str
        Markdown source of the page.
    page, config, files
        Passed by MkDocs; not used.

    Returns
    -------
    str
        The source with the markers replaced.
    """
    if "<!-- result-table:" not in markdown:
        return markdown
    logger.debug("Generating result tables")
    return render(markdown)

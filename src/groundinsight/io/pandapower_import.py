# io/pandapower_import.py

"""
Pandapower importer.

Maps the ``bus`` and ``line`` tables of a pandapower ``net`` to a
:class:`~groundinsight.models.core_models.Network` instance, restricted
to a single voltage level. ``in_service=False`` flags propagate to
:attr:`Bus.active <groundinsight.models.core_models.Bus.active>` and
:attr:`Branch.active <groundinsight.models.core_models.Branch.active>`
so they integrate cleanly with the outage / what-if machinery in
:mod:`groundinsight.simulation.outage`.

The importer does **not** rebuild grounding semantics from pandapower
parameters. Cable shield impedances, cross-couplings and reduction
factors are not modelled in pandapower; they come from
:class:`ImportDefaults` (the user's project-level assumptions about the
typical bus and branch type at the selected voltage level). After the
import, individual buses or branches can be retyped manually if local
deviations are known.

Public API:

- :func:`from_pandapower`
- :func:`preview_pandapower_import`

The optional ``pandapower`` dependency is imported lazily so the core
``groundinsight`` install stays lean.
"""

from __future__ import annotations

import logging
import math
from typing import TYPE_CHECKING, Any, Dict, List, Optional, Tuple

import polars as pl

from groundinsight.diagnostics import InputWarning, emit
from groundinsight.models.core_models import Branch, Bus, Network

from .defaults import ImportDefaults


if TYPE_CHECKING:  # pragma: no cover - typing only
    import pandapower as pp  # noqa: F401


logger = logging.getLogger(__name__)


_PREVIEW_BUS_REASON_VL_MISMATCH = "voltage_level_mismatch"
_PREVIEW_BUS_REASON_VN_UNPARSABLE = "vn_kv_unparsable"
_PREVIEW_LINE_REASON_VL_MISMATCH = "endpoint_off_target_voltage_level"
_PREVIEW_LINE_REASON_BUS_MISSING = "endpoint_bus_missing"
_PREVIEW_LINE_REASON_SELF_LOOP = "self_loop"
_PREVIEW_LINE_REASON_ZERO_LENGTH = "zero_length"
_PREVIEW_LINE_REASON_NEGATIVE_LENGTH = "negative_length"

#: Reasons for the element kinds this importer does not model.
_PREVIEW_SWITCH_REASON_NOT_IMPORTED = "no_switch_branch_type"
_PREVIEW_SWITCH_REASON_OPEN = "switch_open"
_PREVIEW_SWITCH_REASON_NOT_BUS_BUS = "switch_not_bus_to_bus"
_PREVIEW_SWITCH_REASON_OFF_LEVEL = "endpoint_off_target_voltage_level"
_PREVIEW_TRAFO_REASON = "trafos_are_not_modelled"
_PREVIEW_IMPEDANCE_REASON = "impedance_elements_are_not_modelled"

#: Skip reasons that :func:`from_pandapower` refuses to import silently.
_FATAL_LINE_REASONS = (
    _PREVIEW_LINE_REASON_ZERO_LENGTH,
    _PREVIEW_LINE_REASON_NEGATIVE_LENGTH,
)

#: Fallback length in km for a line whose ``length_km`` is missing. Applied
#: only together with an ``InputWarning`` -- see :func:`_length_km`.
_MISSING_LENGTH_FALLBACK_KM = 1.0

#: Output schema of :func:`preview_pandapower_import`, in output order.
#:
#: Declaring it explicitly is not cosmetic. Polars infers the schema of a
#: ``list[dict]`` from its first ``infer_schema_length=100`` entries only, and
#: the preview emits *every* bus row before the first line row: on a net with
#: 100 or more buses the inference never sees a populated ``from_bus`` /
#: ``to_bus`` / ``length_km`` and the subsequent line rows fail to append with
#: a ``ComputeError``. Pinning the schema also keeps an all-``None`` column
#: (e.g. ``length_km`` on a net without lines) typed instead of collapsing it
#: to ``pl.Null``, and keeps an empty frame selectable with ``pl.col(...)``.
_PREVIEW_SCHEMA: Dict[str, Any] = {
    "kind": pl.Utf8,
    "status": pl.Utf8,
    "pp_index": pl.Int64,
    "name": pl.Utf8,
    "vn_kv": pl.Float64,
    "from_bus": pl.Utf8,
    "to_bus": pl.Utf8,
    "length_km": pl.Float64,
    "in_service": pl.Boolean,
    "reason": pl.Utf8,
}


def require_pandapower():
    """
    Import ``pandapower`` lazily and raise a clear error if it is missing.

    Internal API: also used by :mod:`groundinsight.io.pandapower_sc`; not
    exported from ``groundinsight`` and free to change in any release.

    Returns
    -------
    module
        The imported ``pandapower`` module.

    Raises
    ------
    ImportError
        If pandapower is not installed. The message points to
        the optional extra ``groundinsight[pandapower]``.
    """
    try:
        import pandapower as pp  # noqa: F401
    except ImportError as exc:  # pragma: no cover - exercised only without pp
        raise ImportError(
            "pandapower is required for groundinsight.io.pandapower_import. "
            "Install it via `pip install pandapower` or use the optional extra "
            "`pip install 'groundinsight[pandapower]'`."
        ) from exc
    return pp


def _bus_label(row_name: Any, idx: int) -> str:
    """Resolve a stable bus name from ``pp.bus.name`` with a fallback."""
    if row_name is None:
        return f"bus_{idx}"
    name = str(row_name).strip()
    if not name or name.lower() == "nan":
        return f"bus_{idx}"
    return name


def _line_label(row_name: Any, idx: int) -> str:
    """Resolve a stable line name from ``pp.line.name`` with a fallback."""
    if row_name is None:
        return f"line_{idx}"
    name = str(row_name).strip()
    if not name or name.lower() == "nan":
        return f"line_{idx}"
    return name


def bus_index_to_name(net) -> Dict[int, str]:
    """
    Build the ``pp.bus.index -> stable name`` map used to resolve
    ``pp.line.from_bus`` / ``to_bus`` references.

    Internal API: also used by :mod:`groundinsight.io.pandapower_sc`; not
    exported from ``groundinsight`` and free to change in any release.

    Parameters
    ----------
    net : pandapowerNet
        The pandapower network.

    Returns
    -------
    dict of int to str
        Bus name per ``net.bus`` index; a repeated name gets the index
        appended (``"<name>__<index>"``).
    """
    mapping: Dict[int, str] = {}
    seen: Dict[str, int] = {}
    for idx, row in net.bus.iterrows():
        candidate = _bus_label(row.get("name"), int(idx))
        # Disambiguate accidental duplicates by appending the index.
        if candidate in seen:
            candidate = f"{candidate}__{idx}"
        seen[candidate] = int(idx)
        mapping[int(idx)] = candidate
    return mapping


def _line_index_to_name(net) -> Dict[int, str]:
    """
    Build the ``pp.line.index -> stable name`` map. pandapower does not enforce
    unique line names (CIM/CSV/DGS imports routinely carry blank or repeated
    names), so duplicates are disambiguated by appending the index -- mirroring
    :func:`bus_index_to_name`. Without this, two equally-named lines collide on
    :meth:`Network.add_branch` and abort the whole import while the preview
    still reports both as ``keep``.
    """
    mapping: Dict[int, str] = {}
    seen: Dict[str, int] = {}
    for idx, row in net.line.iterrows():
        candidate = _line_label(row.get("name"), int(idx))
        if candidate in seen:
            candidate = f"{candidate}__{idx}"
        seen[candidate] = int(idx)
        mapping[int(idx)] = candidate
    return mapping


def _bus_in_service(row) -> bool:
    """Return ``True`` when the row is in service or the column is missing."""
    value = row.get("in_service")
    if value is None:
        return True
    try:
        return bool(value)
    except Exception:  # pragma: no cover - defensive
        return True


def _parse_length_km(value: Any) -> Optional[float]:
    """
    Return ``length_km`` as ``float`` or ``None`` if it carries no usable
    number.

    This is the side-effect-free counterpart of :func:`_length_km`,
    mirroring :func:`_parse_vn_kv`: it distinguishes "no value" (missing
    column, ``None``, ``NaN``, ``inf``, unparsable string) from a value
    that is present but unusable (``0.0``, negative), which the callers
    must handle differently. Non-finite values are folded into ``None``
    because an infinite length is as meaningless as a missing one.

    Parameters
    ----------
    value : Any
        Raw ``length_km`` cell of a pandapower line row.

    Returns
    -------
    float or None
        The finite length in km, or ``None`` if there is none.
    """
    if value is None:
        return None
    try:
        length = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(length):  # NaN / +-inf
        return None
    return length


def _length_km(row, *, name: str, pp_index: int) -> float:
    """
    Pull ``length_km`` from a pandapower line row for the import.

    A usable length is returned **unchanged**, so the ``length =
    length_km`` contract documented on :func:`from_pandapower` holds
    exactly. Only a *missing* length (absent column, ``None``, ``NaN``,
    ``inf`` or an unparsable value) falls back to
    ``_MISSING_LENGTH_FALLBACK_KM``, and that fallback is announced with an
    ``InputWarning`` ``IMPORT_LENGTH_DEFAULT`` rather than applied silently: a fabricated
    kilometre of conductor rescales every self and mutual impedance of the
    branch (``Z ~ ... * l``) and there is nothing in the resulting model
    that would betray it.

    Zero and negative lengths never reach this function -- they are
    classified as unusable by :func:`_classify_lines` and rejected by
    :func:`from_pandapower`.

    Parameters
    ----------
    row : pandas.Series
        A row of ``net.line``.
    name : str
        Resolved groundinsight name of the line, for the warning.
    pp_index : int
        pandapower index of the line, for the warning.

    Returns
    -------
    float
        The line length in km, or ``_MISSING_LENGTH_FALLBACK_KM`` if the
        row carries no usable one.
    """
    length = _parse_length_km(row.get("length_km"))
    if length is None:
        emit(
            InputWarning,
            f"Line row #{pp_index} ({name}) has no usable length_km "
            f"({row.get('length_km')!r}) — falling back to "
            f"{_MISSING_LENGTH_FALLBACK_KM:.3f} km. Every self and mutual "
            f"impedance of this branch scales with the length, so set an "
            f"explicit length_km in pandapower or retype the branch after the "
            f"import.",
            code="IMPORT_LENGTH_DEFAULT",
            elements=[("branch", name)],
            stacklevel=3,
        )
        return _MISSING_LENGTH_FALLBACK_KM
    return length


def _parse_vn_kv(value: Any) -> Optional[float]:
    """Return ``vn_kv`` as ``float`` or ``None`` if unparsable / missing.

    Pandapower allows ``vn_kv`` to be missing, ``None`` or ``NaN`` in
    user-built nets. The historic implementation silently coerced those
    to ``0`` (and then re-classified the row as
    ``voltage_level_mismatch`` without telling the user). Returning
    ``None`` lets the caller distinguish "no value" from "value but
    wrong level" and emit a warning.
    """
    if value is None:
        return None
    try:
        f = float(value)
    except (TypeError, ValueError):
        return None
    if f != f:  # NaN
        return None
    return f


def _classify_buses(
    net, voltage_level_kV: float
) -> Tuple[List[int], List[Dict[str, Any]]]:
    """
    Split ``net.bus`` into kept indices and skipped rows.

    Buses whose ``vn_kv`` value is missing, ``None`` or ``NaN`` are
    skipped with the dedicated ``vn_kv_unparsable`` reason and an
    ``InputWarning`` ``IMPORT_BUS_SKIPPED`` so the user notices unexpected data instead
    of having it silently re-classified as a voltage-level mismatch.

    Returns
    -------
    Tuple[List[int], List[Dict]]
        A list of kept bus indices on the
    target voltage level, and a list of skip-record dicts (one per
    skipped bus) ready for the preview DataFrame.
    """
    kept: List[int] = []
    skipped: List[Dict[str, Any]] = []
    for idx, row in net.bus.iterrows():
        name = _bus_label(row.get("name"), int(idx))
        raw_vn = row.get("vn_kv")
        vn = _parse_vn_kv(raw_vn)
        if vn is None:
            emit(
                InputWarning,
                f"Bus row #{int(idx)} ({name}) has unparsable vn_kv={raw_vn!r} "
                f"— skipped.",
                code="IMPORT_BUS_SKIPPED",
                elements=[("bus", name)],
                stacklevel=3,
            )
            skipped.append(
                {
                    "kind": "bus",
                    "pp_index": int(idx),
                    "name": name,
                    "vn_kv": None,
                    "reason": _PREVIEW_BUS_REASON_VN_UNPARSABLE,
                }
            )
            continue
        # ``isclose`` rather than ``!=``: a vn_kv that arrived through a CSV
        # or a DGS export carries rounding noise (20.000000000000004), and an
        # exact comparison then reports every bus of the target level as a
        # ``voltage_level_mismatch`` -- an empty import with no explanation.
        if not math.isclose(vn, float(voltage_level_kV), rel_tol=1e-9, abs_tol=0.0):
            skipped.append(
                {
                    "kind": "bus",
                    "pp_index": int(idx),
                    "name": name,
                    "vn_kv": vn,
                    "reason": _PREVIEW_BUS_REASON_VL_MISMATCH,
                }
            )
        else:
            kept.append(int(idx))
    return kept, skipped


def _classify_lines(
    net,
    kept_bus_indices: set,
    bus_name_by_index: Dict[int, str],
    line_name_by_index: Dict[int, str],
) -> Tuple[List[int], List[Dict[str, Any]]]:
    """
    Split ``net.line`` into kept indices and skipped rows. A line is kept
    iff both endpoints are on the target voltage level (i.e. their pp
    indices are in ``kept_bus_indices``) and it carries a usable length.

    The ``length_km`` checks come **last**, after every topological check,
    so that a zero-length line on an off-target voltage level is reported
    as a voltage-level mismatch (which it is) instead of poisoning an
    import that would never have touched it.

    Returns
    -------
    Tuple[List[int], List[Dict]]
        Kept pandapower line indices and skip-record dicts. The
        ``length_km`` of a skip record is the *raw* parsed value
        (``None`` when unusable) -- the missing-length fallback of
        :func:`_length_km` is deliberately not applied to a row that is
        not imported.

    Warns
    -----
    InputWarning
        ``IMPORT_LINE_SKIPPED`` (elements: the line) for a self-loop and for
        a line whose length cannot be imported. Log lines until audit pass 19
        (GUI4).
    """
    kept: List[int] = []
    skipped: List[Dict[str, Any]] = []
    for idx, row in net.line.iterrows():
        raw_length = row.get("length_km")
        length = _parse_length_km(raw_length)
        try:
            from_idx = int(row["from_bus"])
            to_idx = int(row["to_bus"])
        except (KeyError, TypeError, ValueError):
            skipped.append(
                {
                    "kind": "line",
                    "pp_index": int(idx),
                    "name": line_name_by_index[int(idx)],
                    "from_bus": None,
                    "to_bus": None,
                    "length_km": length,
                    "reason": _PREVIEW_LINE_REASON_BUS_MISSING,
                }
            )
            continue

        if from_idx not in bus_name_by_index or to_idx not in bus_name_by_index:
            skipped.append(
                {
                    "kind": "line",
                    "pp_index": int(idx),
                    "name": line_name_by_index[int(idx)],
                    "from_bus": bus_name_by_index.get(from_idx),
                    "to_bus": bus_name_by_index.get(to_idx),
                    "length_km": length,
                    "reason": _PREVIEW_LINE_REASON_BUS_MISSING,
                }
            )
            continue

        if from_idx not in kept_bus_indices or to_idx not in kept_bus_indices:
            skipped.append(
                {
                    "kind": "line",
                    "pp_index": int(idx),
                    "name": line_name_by_index[int(idx)],
                    "from_bus": bus_name_by_index[from_idx],
                    "to_bus": bus_name_by_index[to_idx],
                    "length_km": length,
                    "reason": _PREVIEW_LINE_REASON_VL_MISMATCH,
                }
            )
            continue

        if from_idx == to_idx:
            # Self-loops would map to a Branch(from_bus=X, to_bus=X) and
            # trip Network.add_branch validators only on some code
            # paths; skip them explicitly with a clear reason.
            emit(
                InputWarning,
                f"Line row #{int(idx)} ({line_name_by_index[int(idx)]}) is a "
                f"self-loop on bus {bus_name_by_index[from_idx]!r} — skipped.",
                code="IMPORT_LINE_SKIPPED",
                elements=[("branch", line_name_by_index[int(idx)])],
                stacklevel=3,
            )
            skipped.append(
                {
                    "kind": "line",
                    "pp_index": int(idx),
                    "name": line_name_by_index[int(idx)],
                    "from_bus": bus_name_by_index[from_idx],
                    "to_bus": bus_name_by_index[to_idx],
                    "length_km": length,
                    "reason": _PREVIEW_LINE_REASON_SELF_LOOP,
                }
            )
            continue

        if length is not None and length <= 0.0:
            # Neither value can be turned into a branch: a negative length
            # flips the sign of ``Z ~ ... * l`` (a conductor that *supplies*
            # energy), and a zero length yields ``Z_self = 0``, which
            # ``ElectricalNetwork._build_admittance_matrices`` drops from the
            # admittance matrix -- so a perfect short would silently become
            # an open circuit. Both are reported here and rejected by
            # :func:`from_pandapower`; see :func:`_reject_unusable_lengths`.
            reason = (
                _PREVIEW_LINE_REASON_ZERO_LENGTH
                if length == 0.0
                else _PREVIEW_LINE_REASON_NEGATIVE_LENGTH
            )
            emit(
                InputWarning,
                f"Line row #{int(idx)} ({line_name_by_index[int(idx)]}) has "
                f"length_km={raw_length!r}, which cannot be imported as a "
                f"branch ({reason}).",
                code="IMPORT_LINE_SKIPPED",
                elements=[("branch", line_name_by_index[int(idx)])],
                stacklevel=3,
            )
            skipped.append(
                {
                    "kind": "line",
                    "pp_index": int(idx),
                    "name": line_name_by_index[int(idx)],
                    "from_bus": bus_name_by_index[from_idx],
                    "to_bus": bus_name_by_index[to_idx],
                    "length_km": length,
                    "reason": reason,
                }
            )
            continue

        kept.append(int(idx))
    return kept, skipped


def _classify_switches(
    net, kept_bus_indices: set, bus_name_by_index: Dict[int, str], defaults
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """
    Split ``net.switch`` into importable bus-bus bonds and skipped rows.

    A **closed bus-to-bus switch** (``et == "b"``, ``closed``) bonds two
    grounding systems. Dropping it is not a conservative simplification: the
    bus behind the switch then has no galvanic connection at all and the
    solver reports 0 V for it, which reads as "this station is safe" -- the
    one answer a grounding study must never produce by accident. It was
    dropped without a word up to 0.6.0.

    An *open* switch is genuinely no connection and is reported as skipped.
    A switch to a line, trafo or transformer3w terminal (``et`` other than
    ``"b"``) describes the switching state of that element, not a bond
    between two grounding systems, and is out of scope here.

    Parameters
    ----------
    net : pandapower.auxiliary.pandapowerNet
        The source net.
    kept_bus_indices : set
        Bus indices on the target voltage level.
    bus_name_by_index : Dict[int, str]
        Bus index -> groundinsight bus name.
    defaults : ImportDefaults
        Supplies ``switch_branch_type``; a bond can only be built when the
        caller has said what a bonded connection looks like electrically.

    Returns
    -------
    Tuple[List[Dict], List[Dict]]
        Records for the switches to import, and skip records for the rest.
    """
    switch_table = getattr(net, "switch", None)
    if switch_table is None or len(switch_table) == 0:
        return [], []

    kept: List[Dict[str, Any]] = []
    skipped: List[Dict[str, Any]] = []
    for idx, row in switch_table.iterrows():
        name = _bus_label(row.get("name"), int(idx))
        name = f"switch_{name}" if not str(name).startswith("switch") else str(name)
        from_idx = row.get("bus")
        to_idx = row.get("element")
        element_type = str(row.get("et", "") or "")
        closed = bool(row.get("closed", True))

        record = {
            "kind": "switch",
            "pp_index": int(idx),
            "name": name,
            "from_bus": None,
            "to_bus": None,
        }
        if element_type != "b":
            skipped.append({**record, "reason": _PREVIEW_SWITCH_REASON_NOT_BUS_BUS})
            continue
        try:
            from_idx = int(from_idx)
            to_idx = int(to_idx)
        except (TypeError, ValueError):
            skipped.append({**record, "reason": _PREVIEW_LINE_REASON_BUS_MISSING})
            continue
        if from_idx not in kept_bus_indices or to_idx not in kept_bus_indices:
            skipped.append({**record, "reason": _PREVIEW_SWITCH_REASON_OFF_LEVEL})
            continue

        record["from_bus"] = bus_name_by_index[from_idx]
        record["to_bus"] = bus_name_by_index[to_idx]
        if from_idx == to_idx:
            skipped.append({**record, "reason": _PREVIEW_LINE_REASON_SELF_LOOP})
            continue
        if not closed:
            skipped.append({**record, "reason": _PREVIEW_SWITCH_REASON_OPEN})
            continue
        if defaults is None or defaults.switch_branch_type is None:
            skipped.append(
                {**record, "reason": _PREVIEW_SWITCH_REASON_NOT_IMPORTED}
            )
            continue
        kept.append(record)
    return kept, skipped


def _unmodelled_elements(net) -> List[Dict[str, Any]]:
    """
    Skip records for the element tables this importer does not model.

    ``net.trafo``, ``net.trafo3w`` and ``net.impedance`` all carry a
    connection between two buses that the grounding model has no
    representation for. They were discarded without a count, a log line or a
    preview row, so a user comparing bus counts had no way of telling that
    anything had been left out.

    Parameters
    ----------
    net : pandapower.auxiliary.pandapowerNet
        The source net.

    Returns
    -------
    List[Dict]
        Skip records ready for the preview frame.
    """
    records: List[Dict[str, Any]] = []
    for table_name, kind, reason in (
        ("trafo", "trafo", _PREVIEW_TRAFO_REASON),
        ("trafo3w", "trafo", _PREVIEW_TRAFO_REASON),
        ("impedance", "impedance", _PREVIEW_IMPEDANCE_REASON),
    ):
        table = getattr(net, table_name, None)
        if table is None or len(table) == 0:
            continue
        for idx, row in table.iterrows():
            records.append(
                {
                    "kind": kind,
                    "pp_index": int(idx),
                    "name": _bus_label(row.get("name"), int(idx)),
                    "from_bus": None,
                    "to_bus": None,
                    "reason": reason,
                }
            )
    return records


def _warn_about_unimported(
    switch_skips: List[Dict[str, Any]], unmodelled: List[Dict[str, Any]]
) -> None:
    """
    Say out loud what was left out, grouped by reason.

    Parameters
    ----------
    switch_skips : list of dict
        The skipped switch records of the import.
    unmodelled : list of dict
        The transformer and impedance records the grounding model has no
        representation for.

    Warns
    -----
    InputWarning
        ``IMPORT_SWITCH_NOT_IMPORTED`` (elements: the buses the closed
        switches would bond) and ``IMPORT_ELEMENT_NOT_IMPORTED`` (transformer
        and impedance elements). Log lines until audit pass 19 (GUI4).
    """
    bonds = [e for e in switch_skips
             if e["reason"] == _PREVIEW_SWITCH_REASON_NOT_IMPORTED]
    if bonds:
        bonded = sorted({e[end] for e in bonds for end in ("from_bus", "to_bus")
                         if e.get(end)})
        emit(
            InputWarning,
            f"{len(bonds)} closed bus-to-bus switch(es) were not imported: "
            f"{', '.join(sorted(e['name'] for e in bonds))}. A closed switch "
            f"bonds two grounding systems; without it the bus behind it has no "
            f"galvanic connection and the solver reports 0 V for it, which "
            f"reads as a safe station. Set ImportDefaults.switch_branch_type to "
            f"import them as short branches.",
            code="IMPORT_SWITCH_NOT_IMPORTED",
            elements=[("bus", name) for name in bonded],
            stacklevel=3,
        )
    for kind in ("trafo", "impedance"):
        rows = [e for e in unmodelled if e["kind"] == kind]
        if rows:
            emit(
                InputWarning,
                f"{len(rows)} {kind} element(s) were not imported "
                f"({rows[0]['reason']}). They connect buses that the grounding "
                f"model has no representation for; check "
                f"preview_pandapower_import() for the full list.",
                code="IMPORT_ELEMENT_NOT_IMPORTED",
                stacklevel=3,
            )


def _phase_impedance_formula(row) -> Optional[str]:
    """
    Per-line phase-conductor impedance from the pandapower line data.

    ``phase_current_mode="auto"`` -- the default of
    :func:`~groundinsight.run_fault` -- solves a reduced *phase-conductor*
    network, and it needs a ``phase_impedance_formula`` to do so. Without one
    every imported mesh falls back to the path-based proxy, which is exactly
    the case the automatic mode exists for. ``r_ohm_per_km`` and
    ``x_ohm_per_km`` are the positive-sequence values pandapower carries for
    every line, and ``Branch.length`` is in km, so ``(r + I*x)*l`` is the
    series impedance of the phase conductor of that line.

    The *earth-return* formulas are deliberately not derived: they depend on
    the soil resistivity and the cable geometry, neither of which pandapower
    carries, and inventing them would produce a network that solves cleanly
    and is wrong.

    Parameters
    ----------
    row : pandas.Series
        A row of ``net.line``.

    Returns
    -------
    Optional[str]
        The formula, or ``None`` when the line carries no usable r/x.
    """
    try:
        r = float(row.get("r_ohm_per_km"))
        x = float(row.get("x_ohm_per_km"))
    except (TypeError, ValueError):
        return None
    if not (math.isfinite(r) and math.isfinite(x)):
        return None
    if r < 0 or x < 0:
        return None
    if r == 0.0 and x == 0.0:
        return None
    return f"({r!r} + I * {x!r}) * l"


def _branch_type_for_line(defaults, row, cache: Dict[str, Any]):
    """
    The :class:`BranchType` an imported line gets.

    Without ``defaults.derive_phase_impedance`` this is
    ``defaults.default_branch_type``, unchanged. With it, a variant carrying a
    per-line ``phase_impedance_formula`` derived from the pandapower line data
    is used; variants are cached by formula, so a net with three cable types
    produces three branch types rather than one per line.

    Parameters
    ----------
    defaults : ImportDefaults
        The import defaults.
    row : pandas.Series
        A row of ``net.line``.
    cache : Dict[str, Any]
        Formula -> derived ``BranchType``, shared across one import.

    Returns
    -------
    BranchType
        The type to assign.
    """
    if not getattr(defaults, "derive_phase_impedance", False):
        return defaults.default_branch_type
    formula = _phase_impedance_formula(row)
    if formula is None:
        return defaults.default_branch_type
    if formula not in cache:
        base = defaults.default_branch_type
        cache[formula] = base.model_copy(
            update={
                "name": f"{base.name}_phase{len(cache) + 1}",
                "phase_impedance_formula": formula,
            }
        )
    return cache[formula]


def _check_voltage_level_consistency(defaults, voltage_level_kV: float) -> None:
    """
    Warn when the default bus type describes a different voltage level.

    ``voltage_level_kV`` selects which pandapower buses are imported;
    ``defaults.default_bus_type.voltage_level`` states which level the type
    was written for. Nothing connected the two, so importing the 0.4 kV level
    with a 20 kV bus type went through silently and produced a network whose
    every bus claims the wrong nominal voltage -- which then shows up in a
    touch-voltage assessment, several steps away from the import.

    A warning, not an error: the two are genuinely independent, and a user may
    have a good reason (a type shared across levels). But it must be visible.

    Parameters
    ----------
    defaults : ImportDefaults
        The import defaults.
    voltage_level_kV : float
        The level being imported.

    Warns
    -----
    InputWarning
        ``IMPORT_VOLTAGE_LEVEL_MISMATCH`` (elements: the default bus type).
        A log line until audit pass 19 (GUI4).
    """
    declared = getattr(defaults.default_bus_type, "voltage_level", None)
    if declared is None:
        return
    if not math.isclose(float(declared), float(voltage_level_kV), rel_tol=1e-9):
        emit(
            InputWarning,
            f"ImportDefaults.default_bus_type '{defaults.default_bus_type.name}' "
            f"declares voltage_level={float(declared):g} kV but the import "
            f"selects {float(voltage_level_kV):g} kV. Every imported bus will "
            f"carry the type's value, which is what a touch-voltage assessment "
            f"reads. Give the type the level you are importing, or confirm that "
            f"it is meant to be shared across levels.",
            code="IMPORT_VOLTAGE_LEVEL_MISMATCH",
            elements=[("bus_type", defaults.default_bus_type.name)],
            stacklevel=3,
        )


def _reject_unusable_lengths(skipped_lines: List[Dict[str, Any]]) -> None:
    """
    Abort the import if a line that is otherwise importable carries a
    non-positive ``length_km``.

    Rejecting is the deliberate choice over silently importing the value.
    A zero-length line is a jumper or bus coupler and is perfectly normal
    in a pandapower model, but groundinsight cannot represent it: its
    ``Z_self`` evaluates to ``0``, and
    ``ElectricalNetwork._build_admittance_matrices`` skips a branch whose
    impedance is zero, so the intended perfect short between the two buses
    turns into an *open circuit* -- the result does not converge to the
    ``length -> 0`` limit. Importing it as ``1.0 km`` (the historic
    behaviour) is no better: it invents a series impedance that is not in
    the source model. Both alternatives are silently wrong, so the import
    stops and names the rows instead.

    Parameters
    ----------
    skipped_lines : List[Dict]
        The skip records produced by :func:`_classify_lines`.

    Raises
    ------
    ValueError
        If at least one skip record carries a ``zero_length`` or
        ``negative_length`` reason.
    """
    offenders = [e for e in skipped_lines if e["reason"] in _FATAL_LINE_REASONS]
    if not offenders:
        return
    detail = ", ".join(
        f"{entry['name']!r} (pp index {entry['pp_index']}, "
        f"length_km={entry['length_km']!r})"
        for entry in offenders
    )
    raise ValueError(
        f"{len(offenders)} pandapower line(s) cannot be imported because their "
        f"length_km is not strictly positive: {detail}. A zero-length branch "
        "has no series impedance and is dropped from the admittance matrix "
        "(it would act as an open circuit, not as the short it represents); a "
        "negative length would flip the sign of the branch impedance. Give the "
        "line an explicit positive length_km, fuse its two buses, or drop it "
        "in pandapower before importing. preview_pandapower_import() lists "
        "every affected row with reason='zero_length' / 'negative_length'."
    )


def from_pandapower(
    net,
    *,
    defaults: ImportDefaults,
    voltage_level_kV: float,
    network_name: Optional[str] = None,
    include_trafos: bool = False,
) -> Network:
    """
    Build a :class:`~groundinsight.models.core_models.Network` from a
    pandapower ``net``, restricted to a single voltage level.

    The mapping is intentionally narrow:

    - ``pp.bus`` rows whose ``vn_kv`` matches ``voltage_level_kV`` become
      :class:`~groundinsight.models.core_models.Bus` instances using
      ``defaults.default_bus_type``. ``in_service=False`` propagates to
      ``Bus.active=False`` so the outage helpers can reason over them.
    - ``pp.line`` rows whose endpoints both lie on the target voltage
      level become :class:`~groundinsight.models.core_models.Branch`
      instances using ``defaults.default_branch_type`` with
      ``length=length_km``. ``in_service=False`` propagates to
      ``Branch.active=False``. With
      ``defaults.derive_phase_impedance=True`` each line additionally gets
      a ``phase_impedance_formula`` derived from its ``r_ohm_per_km`` /
      ``x_ohm_per_km``, which is what ``phase_current_mode="auto"`` needs;
      the earth-return formulas stay as the caller set them.
    - **Closed bus-to-bus switches** become short branches when
      ``defaults.switch_branch_type`` is set. Without it they are not
      imported -- as before -- but the omission is now logged and listed by
      :func:`preview_pandapower_import`, because a bus reachable only
      through a switch otherwise appears galvanically isolated and reports
      0 V.
    - ext_grids, sgens, loads etc. are ignored. Trafos, three-winding
      trafos and ``net.impedance`` elements are skipped (and reported);
      ``include_trafos=True`` is reserved for a future release and raises
      ``NotImplementedError`` for now.

    No fault, source or path is created — those are left to the caller.

    ``length = length_km`` holds literally: a length is never rescaled.
    A line whose ``length_km`` is missing (``None`` / ``NaN`` / ``inf`` /
    unparsable) is imported with a fallback of 1.0 km **and** an
    ``InputWarning`` ``IMPORT_LENGTH_DEFAULT`` naming the row, because that fallback silently
    rescales every impedance of the branch. A line whose ``length_km`` is
    zero or negative is not importable at all and raises ``ValueError``;
    see :func:`_reject_unusable_lengths` for the reasoning and
    :func:`preview_pandapower_import` for listing the affected rows
    beforehand.

    Parameters
    ----------
    net : pandapowerNet
        A pandapower network object (the type is not imported eagerly to
        keep pandapower optional).
    defaults : ImportDefaults
        Project-level defaults; see :class:`ImportDefaults`.
    voltage_level_kV : float
        Voltage level (``vn_kv``) of the buses to keep. Lines that bridge
        to a different level are skipped.
    network_name : str, optional
        Name for the resulting Network. Defaults to ``net.name`` if set,
        otherwise ``"pandapower_import"``.
    include_trafos : bool, optional
        Reserved for a future release. Setting this to ``True`` raises
        :class:`NotImplementedError`.

    Returns
    -------
    Network
        A new groundinsight Network containing the imported buses and
        branches, with ``frequencies = defaults.frequencies``.

    Raises
    ------
    ImportError
        If pandapower is not installed.
    ValueError
        If ``defaults.frequencies`` is empty, or if a line that would be
        imported carries a zero or negative ``length_km``.
    NotImplementedError
        If ``include_trafos=True``.

    Warns
    -----
    InputWarning
        Whatever the import had to decide on the user's behalf, each with the
        elements concerned: ``IMPORT_LENGTH_DEFAULT`` (a line without usable
        length got the fallback length), ``IMPORT_BUS_SKIPPED`` (a bus with
        an unparsable ``vn_kv``), ``IMPORT_LINE_SKIPPED`` (a self-loop, or a
        line whose length cannot be imported), ``IMPORT_SWITCH_NOT_IMPORTED``
        (closed bus-to-bus switches without ``switch_branch_type``),
        ``IMPORT_ELEMENT_NOT_IMPORTED`` (transformers and impedance
        elements), ``IMPORT_VOLTAGE_LEVEL_MISMATCH`` (the default bus type
        declares another voltage level) and ``IMPORT_EMPTY`` (no bus or no
        branch was imported). Log lines until audit pass 19 (GUI4); a GUI
        collects them with :func:`groundinsight.diagnostics.collecting`.

    Examples
    --------
    >>> import pandapower as pp                                      # doctest: +SKIP
    >>> from groundinsight.io import ImportDefaults, from_pandapower  # doctest: +SKIP
    >>> net = pp.networks.create_kerber_landnetz_freileitung_1()      # doctest: +SKIP
    >>> network = from_pandapower(                                    # doctest: +SKIP
    ...     net,
    ...     defaults=defaults,
    ...     voltage_level_kV=0.4,
    ...     network_name="kerber_lv",
    ... )
    """
    require_pandapower()

    if include_trafos:
        raise NotImplementedError(
            "include_trafos=True is reserved for a future release; trafos "
            "are not imported yet."
        )
    if not defaults.frequencies:
        raise ValueError("ImportDefaults.frequencies must not be empty.")

    _check_voltage_level_consistency(defaults, voltage_level_kV)

    name = network_name or getattr(net, "name", None) or "pandapower_import"
    network = Network(name=str(name), frequencies=list(defaults.frequencies))

    bus_name_by_index = bus_index_to_name(net)
    line_name_by_index = _line_index_to_name(net)
    kept_bus_indices, _ = _classify_buses(net, voltage_level_kV)
    kept_bus_set = set(kept_bus_indices)

    # Buses
    for idx in kept_bus_indices:
        row = net.bus.loc[idx]
        bus = Bus(
            name=bus_name_by_index[idx],
            description=str(row.get("name") or "") or None,
            type=defaults.default_bus_type,
            impedance={},
            specific_earth_resistance=float(defaults.rho),
            active=_bus_in_service(row),
        )
        network.add_bus(bus)

    # Branches
    kept_lines, skipped_lines = _classify_lines(
        net, kept_bus_set, bus_name_by_index, line_name_by_index
    )
    _reject_unusable_lengths(skipped_lines)
    derived_types: Dict[str, Any] = {}
    for idx in kept_lines:
        row = net.line.loc[idx]
        from_idx = int(row["from_bus"])
        to_idx = int(row["to_bus"])
        branch = Branch(
            name=line_name_by_index[int(idx)],
            description=str(row.get("name") or "") or None,
            type=_branch_type_for_line(defaults, row, derived_types),
            length=_length_km(
                row, name=line_name_by_index[int(idx)], pp_index=int(idx)
            ),
            from_bus=bus_name_by_index[from_idx],
            to_bus=bus_name_by_index[to_idx],
            self_impedance={},
            mutual_impedance={},
            specific_earth_resistance=float(defaults.rho),
            active=_bus_in_service(row),
        )
        network.add_branch(branch)

    # Closed bus-to-bus switches: a bond, not a line -- see _classify_switches.
    kept_switches, skipped_switches = _classify_switches(
        net, kept_bus_set, bus_name_by_index, defaults
    )
    for record in kept_switches:
        network.add_branch(
            Branch(
                name=record["name"],
                description="closed bus-to-bus switch",
                type=defaults.switch_branch_type,
                length=float(defaults.switch_length_km),
                from_bus=record["from_bus"],
                to_bus=record["to_bus"],
                self_impedance={},
                mutual_impedance={},
                specific_earth_resistance=float(defaults.rho),
                active=True,
            )
        )
    _warn_about_unimported(skipped_switches, _unmodelled_elements(net))

    if derived_types:
        logger.info(
            "Derived %d phase-impedance branch type(s) from r_ohm_per_km / "
            "x_ohm_per_km; the earth-return formulas stay at '%s'.",
            len(derived_types),
            defaults.default_branch_type.name,
        )

    if not network.buses or not network.branches:
        # Promote the zero-bus / zero-branch case to a warning so the
        # user notices that the import produced an unusable result
        # (typically because of a wrong ``voltage_level_kV`` argument).
        emit(
            InputWarning,
            f"Imported pandapower net into '{network.name}': "
            f"{len(network.buses)} buses, {len(network.branches)} branches at "
            f"{float(voltage_level_kV):.3f} kV — result is empty or "
            f"branch-less; verify the voltage_level_kV argument.",
            code="IMPORT_EMPTY",
            elements=[("network", network.name)],
            stacklevel=2,
        )
    else:
        logger.info(
            "Imported pandapower net into '%s': %d buses, %d branches at %.3f kV.",
            network.name,
            len(network.buses),
            len(network.branches),
            float(voltage_level_kV),
        )
    return network


def preview_pandapower_import(
    net,
    *,
    voltage_level_kV: float,
    include_trafos: bool = False,
    defaults: Optional[ImportDefaults] = None,
) -> pl.DataFrame:
    """
    Return a Polars DataFrame describing what :func:`from_pandapower`
    would do on this ``net`` at this voltage level, without building a
    Network.

    The frame has the following columns:

    - ``kind``           -- ``"bus"``, ``"line"``, ``"switch"``,
      ``"trafo"`` or ``"impedance"``. The last three are always
      ``status="skip"``; a ``"switch"`` row is ``"keep"`` only when a
      ``defaults`` with a ``switch_branch_type`` is passed.
    - ``status``         -- ``"keep"`` or ``"skip"``.
    - ``pp_index``       -- The pandapower index of the row.
    - ``name``           -- Resolved groundinsight name (with fallback).
    - ``vn_kv``          -- Bus voltage level (``None`` for lines).
    - ``from_bus``       -- Resolved from-bus name (lines only).
    - ``to_bus``         -- Resolved to-bus name (lines only).
    - ``length_km``      -- Line length in km (lines only). For a kept
      line this is the length :func:`from_pandapower` would assign, so
      preview and commit never disagree — including the
      ``_MISSING_LENGTH_FALLBACK_KM`` substitution (with the same
      warning) when pandapower carries no usable number. For a skipped
      line it is the raw parsed value, ``None`` when unusable.
    - ``in_service``     -- Boolean flag from pandapower (best effort).
    - ``reason``         -- Skip reason if ``status == "skip"``,
      ``None`` otherwise.

    The dtypes are pinned by ``_PREVIEW_SCHEMA`` rather than inferred, so
    the frame is identical in shape for a two-bus net and for a
    thousand-bus one, and an all-``None`` column keeps a usable dtype.

    Use this before committing to a full import to validate the mapping
    or diagnose unexpectedly skipped elements. Unlike
    :func:`from_pandapower`, this function never raises on bad *data*: a
    line whose ``length_km`` is zero or negative — which aborts the
    commit — is reported here as ``status="skip"`` with
    ``reason="zero_length"`` / ``"negative_length"``, so every affected
    row can be enumerated in one go before the import is attempted.

    Parameters
    ----------
    net : pandapowerNet
        The pandapower network to inspect.
    voltage_level_kV : float
        Voltage level to keep (``pp.bus.vn_kv``).
    include_trafos : bool, optional
        Mirrors the parameter of :func:`from_pandapower`. Reserved for a
        future release — setting this to ``True`` raises
        :class:`NotImplementedError` so that ``preview ↔ commit`` cannot
        disagree silently about trafo handling.
    defaults : ImportDefaults, optional
        The same defaults that will be passed to :func:`from_pandapower`.
        Only the switch handling depends on them: without a
        ``switch_branch_type`` no switch can be imported, so passing the
        defaults is what lets the preview show a closed bus-to-bus switch as
        ``status="keep"`` rather than as skipped. Everything else is
        independent of the defaults, so ``None`` still gives a correct
        bus/line preview.

    Returns
    -------
    polars.DataFrame
        One row per bus and one per line, with the dtypes of
        ``_PREVIEW_SCHEMA``.

    Raises
    ------
    NotImplementedError
        If ``include_trafos=True``.
    """
    require_pandapower()

    if include_trafos:
        raise NotImplementedError(
            "include_trafos=True is reserved for a future release; "
            "trafos are not previewed yet — matches the commit-side "
            "behaviour of from_pandapower()."
        )

    bus_name_by_index = bus_index_to_name(net)
    line_name_by_index = _line_index_to_name(net)
    kept_bus_indices, skipped_buses = _classify_buses(net, voltage_level_kV)
    kept_bus_set = set(kept_bus_indices)
    kept_lines, skipped_lines = _classify_lines(
        net, kept_bus_set, bus_name_by_index, line_name_by_index
    )
    kept_switches, skipped_switches = _classify_switches(
        net, kept_bus_set, bus_name_by_index, defaults
    )

    rows: List[Dict[str, Any]] = []

    # Kept buses
    for idx in kept_bus_indices:
        row = net.bus.loc[idx]
        rows.append(
            {
                "kind": "bus",
                "status": "keep",
                "pp_index": int(idx),
                "name": bus_name_by_index[int(idx)],
                "vn_kv": float(row.get("vn_kv", 0.0) or 0.0),
                "from_bus": None,
                "to_bus": None,
                "length_km": None,
                "in_service": _bus_in_service(row),
                "reason": None,
            }
        )

    # Skipped buses
    for entry in skipped_buses:
        rows.append(
            {
                "kind": "bus",
                "status": "skip",
                "pp_index": entry["pp_index"],
                "name": entry["name"],
                "vn_kv": entry.get("vn_kv"),
                "from_bus": None,
                "to_bus": None,
                "length_km": None,
                "in_service": None,
                "reason": entry["reason"],
            }
        )

    # Kept lines
    for idx in kept_lines:
        row = net.line.loc[idx]
        rows.append(
            {
                "kind": "line",
                "status": "keep",
                "pp_index": int(idx),
                "name": line_name_by_index[int(idx)],
                "vn_kv": None,
                "from_bus": bus_name_by_index[int(row["from_bus"])],
                "to_bus": bus_name_by_index[int(row["to_bus"])],
                "length_km": _length_km(
                    row, name=line_name_by_index[int(idx)], pp_index=int(idx)
                ),
                "in_service": _bus_in_service(row),
                "reason": None,
            }
        )

    # Skipped lines
    for entry in skipped_lines:
        rows.append(
            {
                "kind": "line",
                "status": "skip",
                "pp_index": entry["pp_index"],
                "name": entry["name"],
                "vn_kv": None,
                "from_bus": entry.get("from_bus"),
                "to_bus": entry.get("to_bus"),
                "length_km": entry.get("length_km"),
                "in_service": None,
                "reason": entry["reason"],
            }
        )

    # Switches: kept only when a switch_branch_type was supplied.
    for record in kept_switches:
        rows.append(
            {
                "kind": "switch",
                "status": "keep",
                "pp_index": record["pp_index"],
                "name": record["name"],
                "vn_kv": None,
                "from_bus": record["from_bus"],
                "to_bus": record["to_bus"],
                "length_km": float(defaults.switch_length_km)
                if defaults is not None
                else None,
                "in_service": True,
                "reason": None,
            }
        )
    for record in skipped_switches:
        rows.append(
            {
                "kind": "switch",
                "status": "skip",
                "pp_index": record["pp_index"],
                "name": record["name"],
                "vn_kv": None,
                "from_bus": record.get("from_bus"),
                "to_bus": record.get("to_bus"),
                "length_km": None,
                "in_service": None,
                "reason": record["reason"],
            }
        )

    # Trafos and impedance elements: never imported, but now counted and named
    # instead of vanishing between the bus and the line tables.
    for record in _unmodelled_elements(net):
        rows.append(
            {
                "kind": record["kind"],
                "status": "skip",
                "pp_index": record["pp_index"],
                "name": record["name"],
                "vn_kv": None,
                "from_bus": None,
                "to_bus": None,
                "length_km": None,
                "in_service": None,
                "reason": record["reason"],
            }
        )

    # The schema is pinned, never inferred: polars would only look at the
    # first 100 dicts, which on a net with >= 100 buses are all bus rows
    # with a ``None`` from_bus / to_bus / length_km, and the first line row
    # would then fail to append with a ComputeError.
    return pl.DataFrame(rows, schema=_PREVIEW_SCHEMA)

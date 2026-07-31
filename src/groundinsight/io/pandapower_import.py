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

#: Skip reasons that :func:`from_pandapower` refuses to import silently.
_FATAL_LINE_REASONS = (
    _PREVIEW_LINE_REASON_ZERO_LENGTH,
    _PREVIEW_LINE_REASON_NEGATIVE_LENGTH,
)

#: Fallback length in km for a line whose ``length_km`` is missing. Applied
#: only together with a ``logger.warning`` -- see :func:`_length_km`.
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


def _require_pandapower():
    """
    Import ``pandapower`` lazily and raise a clear error if it is missing.

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


def _bus_index_to_name(net) -> Dict[int, str]:
    """
    Build the ``pp.bus.index -> stable name`` map used to resolve
    ``pp.line.from_bus`` / ``to_bus`` references.
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
    :func:`_bus_index_to_name`. Without this, two equally-named lines collide on
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
    ``_MISSING_LENGTH_FALLBACK_KM``, and that fallback is announced with a
    ``logger.warning`` rather than applied silently: a fabricated
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
        logger.warning(
            "Line row #%d (%s) has no usable length_km (%r) — falling back to "
            "%.3f km. Every self and mutual impedance of this branch scales "
            "with the length, so set an explicit length_km in pandapower or "
            "retype the branch after the import.",
            pp_index,
            name,
            row.get("length_km"),
            _MISSING_LENGTH_FALLBACK_KM,
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
    skipped with the dedicated ``vn_kv_unparsable`` reason and a
    ``logger.warning(...)`` so the user notices unexpected data instead
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
            logger.warning(
                "Bus row #%d (%s) has unparsable vn_kv=%r — skipped.",
                int(idx),
                name,
                raw_vn,
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
        if vn != float(voltage_level_kV):
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
            logger.warning(
                "Line row #%d (%s) is a self-loop on bus %r — skipped.",
                int(idx),
                line_name_by_index[int(idx)],
                bus_name_by_index[from_idx],
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
            logger.warning(
                "Line row #%d (%s) has length_km=%r, which cannot be imported "
                "as a branch (%s).",
                int(idx),
                line_name_by_index[int(idx)],
                raw_length,
                reason,
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
      ``Branch.active=False``.
    - Switches, ext_grids, sgens, loads etc. are ignored. Trafos are
      skipped unless ``include_trafos=True``, which is reserved for a
      future release (raises ``NotImplementedError`` for now).

    No fault, source or path is created — those are left to the caller.

    ``length = length_km`` holds literally: a length is never rescaled.
    A line whose ``length_km`` is missing (``None`` / ``NaN`` / ``inf`` /
    unparsable) is imported with a fallback of 1.0 km **and** a
    ``logger.warning`` naming the row, because that fallback silently
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
    _require_pandapower()

    if include_trafos:
        raise NotImplementedError(
            "include_trafos=True is reserved for a future release; trafos "
            "are not imported yet."
        )
    if not defaults.frequencies:
        raise ValueError("ImportDefaults.frequencies must not be empty.")

    name = network_name or getattr(net, "name", None) or "pandapower_import"
    network = Network(name=str(name), frequencies=list(defaults.frequencies))

    bus_name_by_index = _bus_index_to_name(net)
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
    for idx in kept_lines:
        row = net.line.loc[idx]
        from_idx = int(row["from_bus"])
        to_idx = int(row["to_bus"])
        branch = Branch(
            name=line_name_by_index[int(idx)],
            description=str(row.get("name") or "") or None,
            type=defaults.default_branch_type,
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

    if not network.buses or not network.branches:
        # Promote the zero-bus / zero-branch case to a warning so the
        # user notices that the import produced an unusable result
        # (typically because of a wrong ``voltage_level_kV`` argument).
        logger.warning(
            "Imported pandapower net into '%s': %d buses, %d branches at %.3f kV — "
            "result is empty or branch-less; verify the voltage_level_kV argument.",
            network.name,
            len(network.buses),
            len(network.branches),
            float(voltage_level_kV),
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
) -> pl.DataFrame:
    """
    Return a Polars DataFrame describing what :func:`from_pandapower`
    would do on this ``net`` at this voltage level, without building a
    Network.

    The frame has the following columns:

    - ``kind``           -- ``"bus"``, ``"line"`` or ``"trafo"`` (the
      trafo kind only appears when ``include_trafos=True``).
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
    _require_pandapower()

    if include_trafos:
        raise NotImplementedError(
            "include_trafos=True is reserved for a future release; "
            "trafos are not previewed yet — matches the commit-side "
            "behaviour of from_pandapower()."
        )

    bus_name_by_index = _bus_index_to_name(net)
    line_name_by_index = _line_index_to_name(net)
    kept_bus_indices, skipped_buses = _classify_buses(net, voltage_level_kV)
    kept_bus_set = set(kept_bus_indices)
    kept_lines, skipped_lines = _classify_lines(
        net, kept_bus_set, bus_name_by_index, line_name_by_index
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

    # The schema is pinned, never inferred: polars would only look at the
    # first 100 dicts, which on a net with >= 100 buses are all bus rows
    # with a ``None`` from_bus / to_bus / length_km, and the first line row
    # would then fail to append with a ComputeError.
    return pl.DataFrame(rows, schema=_PREVIEW_SCHEMA)

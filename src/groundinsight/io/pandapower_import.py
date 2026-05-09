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
from typing import TYPE_CHECKING, Any, Dict, List, Optional, Tuple

import polars as pl

from groundinsight.models.core_models import Branch, Bus, Network

from .defaults import ImportDefaults


if TYPE_CHECKING:  # pragma: no cover - typing only
    import pandapower as pp  # noqa: F401


logger = logging.getLogger(__name__)


_PREVIEW_BUS_REASON_VL_MISMATCH = "voltage_level_mismatch"
_PREVIEW_LINE_REASON_VL_MISMATCH = "endpoint_off_target_voltage_level"
_PREVIEW_LINE_REASON_BUS_MISSING = "endpoint_bus_missing"


def _require_pandapower():
    """
    Import ``pandapower`` lazily and raise a clear error if it is missing.

    Returns:
        module: The imported ``pandapower`` module.

    Raises:
        ImportError: If pandapower is not installed. The message points to
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


def _bus_in_service(row) -> bool:
    """Return ``True`` when the row is in service or the column is missing."""
    value = row.get("in_service")
    if value is None:
        return True
    try:
        return bool(value)
    except Exception:  # pragma: no cover - defensive
        return True


def _length_km(row) -> float:
    """
    Pull ``length_km`` from a pandapower line row, default to 1.0 for
    missing / NaN entries (the same fallback used elsewhere in
    groundinsight when no length is available).
    """
    length = row.get("length_km")
    if length is None:
        return 1.0
    try:
        length_f = float(length)
    except (TypeError, ValueError):
        return 1.0
    if length_f != length_f:  # NaN
        return 1.0
    return length_f


def _classify_buses(
    net, voltage_level_kV: float
) -> Tuple[List[int], List[Dict[str, Any]]]:
    """
    Split ``net.bus`` into kept indices and skipped rows.

    Returns:
        Tuple[List[int], List[Dict]]: A list of kept bus indices on the
        target voltage level, and a list of skip-record dicts (one per
        skipped bus) ready for the preview DataFrame.
    """
    kept: List[int] = []
    skipped: List[Dict[str, Any]] = []
    for idx, row in net.bus.iterrows():
        vn = float(row.get("vn_kv", 0.0) or 0.0)
        name = _bus_label(row.get("name"), int(idx))
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
) -> Tuple[List[int], List[Dict[str, Any]]]:
    """
    Split ``net.line`` into kept indices and skipped rows. A line is kept
    iff both endpoints are on the target voltage level (i.e. their pp
    indices are in ``kept_bus_indices``).
    """
    kept: List[int] = []
    skipped: List[Dict[str, Any]] = []
    for idx, row in net.line.iterrows():
        try:
            from_idx = int(row["from_bus"])
            to_idx = int(row["to_bus"])
        except (KeyError, TypeError, ValueError):
            skipped.append(
                {
                    "kind": "line",
                    "pp_index": int(idx),
                    "name": _line_label(row.get("name"), int(idx)),
                    "from_bus": None,
                    "to_bus": None,
                    "length_km": _length_km(row),
                    "reason": _PREVIEW_LINE_REASON_BUS_MISSING,
                }
            )
            continue

        if from_idx not in bus_name_by_index or to_idx not in bus_name_by_index:
            skipped.append(
                {
                    "kind": "line",
                    "pp_index": int(idx),
                    "name": _line_label(row.get("name"), int(idx)),
                    "from_bus": bus_name_by_index.get(from_idx),
                    "to_bus": bus_name_by_index.get(to_idx),
                    "length_km": _length_km(row),
                    "reason": _PREVIEW_LINE_REASON_BUS_MISSING,
                }
            )
            continue

        if from_idx not in kept_bus_indices or to_idx not in kept_bus_indices:
            skipped.append(
                {
                    "kind": "line",
                    "pp_index": int(idx),
                    "name": _line_label(row.get("name"), int(idx)),
                    "from_bus": bus_name_by_index[from_idx],
                    "to_bus": bus_name_by_index[to_idx],
                    "length_km": _length_km(row),
                    "reason": _PREVIEW_LINE_REASON_VL_MISMATCH,
                }
            )
            continue

        kept.append(int(idx))
    return kept, skipped


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

    No fault, source or path is created -- those are project-specific
    additions left to the caller.

    Args:
        net: A pandapower ``pandapowerNet`` object (the type is not
            imported eagerly to keep pandapower optional).
        defaults: Project-level defaults; see :class:`ImportDefaults`.
        voltage_level_kV: Voltage level (``vn_kv``) of the buses to
            keep. Lines that bridge to a different level are skipped.
        network_name: Optional name for the resulting Network. Defaults
            to ``net.name`` if set, otherwise ``"pandapower_import"``.
        include_trafos: Reserved for a future release. Setting this to
            ``True`` raises ``NotImplementedError``.

    Returns:
        Network: A new groundinsight Network containing the imported
        buses and branches, with frequencies = ``defaults.frequencies``.

    Raises:
        ImportError: If pandapower is not installed.
        ValueError: If ``defaults.frequencies`` is empty.
        NotImplementedError: If ``include_trafos=True``.

    Examples:
        >>> import pandapower as pp                                  # doctest: +SKIP
        >>> from groundinsight.io import ImportDefaults, from_pandapower  # doctest: +SKIP
        >>> net = pp.networks.create_kerber_landnetz_freileitung_1()       # doctest: +SKIP
        >>> network = from_pandapower(                                     # doctest: +SKIP
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
    kept_lines, _ = _classify_lines(net, kept_bus_set, bus_name_by_index)
    for idx in kept_lines:
        row = net.line.loc[idx]
        from_idx = int(row["from_bus"])
        to_idx = int(row["to_bus"])
        branch = Branch(
            name=_line_label(row.get("name"), int(idx)),
            description=str(row.get("name") or "") or None,
            type=defaults.default_branch_type,
            length=_length_km(row),
            from_bus=bus_name_by_index[from_idx],
            to_bus=bus_name_by_index[to_idx],
            self_impedance={},
            mutual_impedance={},
            specific_earth_resistance=float(defaults.rho),
            active=_bus_in_service(row),
        )
        network.add_branch(branch)

    logger.info(
        "Imported pandapower net into '%s': %d buses, %d branches at %.3f kV.",
        network.name,
        len(network.buses),
        len(network.branches),
        float(voltage_level_kV),
    )
    return network


def preview_pandapower_import(
    net, *, voltage_level_kV: float
) -> pl.DataFrame:
    """
    Return a Polars DataFrame describing what :func:`from_pandapower`
    would do on this ``net`` at this voltage level, without building a
    Network.

    The frame has the following columns:

    - ``kind``           -- ``"bus"`` or ``"line"``.
    - ``status``         -- ``"keep"`` or ``"skip"``.
    - ``pp_index``       -- The pandapower index of the row.
    - ``name``           -- Resolved groundinsight name (with fallback).
    - ``vn_kv``          -- Bus voltage level (``None`` for lines).
    - ``from_bus``       -- Resolved from-bus name (lines only).
    - ``to_bus``         -- Resolved to-bus name (lines only).
    - ``length_km``      -- Line length in km (lines only).
    - ``in_service``     -- Boolean flag from pandapower (best effort).
    - ``reason``         -- Skip reason if ``status == "skip"``,
      ``None`` otherwise.

    Use this before committing to a full import to validate the mapping
    or diagnose unexpectedly skipped elements.

    Args:
        net: The pandapower ``pandapowerNet`` to inspect.
        voltage_level_kV: Voltage level to keep (``pp.bus.vn_kv``).

    Returns:
        pl.DataFrame: One row per bus and one per line.
    """
    _require_pandapower()

    bus_name_by_index = _bus_index_to_name(net)
    kept_bus_indices, skipped_buses = _classify_buses(net, voltage_level_kV)
    kept_bus_set = set(kept_bus_indices)
    kept_lines, skipped_lines = _classify_lines(
        net, kept_bus_set, bus_name_by_index
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
                "name": _line_label(row.get("name"), int(idx)),
                "vn_kv": None,
                "from_bus": bus_name_by_index[int(row["from_bus"])],
                "to_bus": bus_name_by_index[int(row["to_bus"])],
                "length_km": _length_km(row),
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

    return pl.DataFrame(rows)

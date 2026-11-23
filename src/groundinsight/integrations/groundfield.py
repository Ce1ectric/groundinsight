# integrations/groundfield.py

"""
Earthing characteristics from groundfield.

groundfield computes the field of an earthing arrangement and exports the
result for groundinsight as a *BusType document*: a JSON object with the
schema name ``"groundfield.bustype"`` and ``schema_version`` 1, written by
groundfield's ``to_bustype_dict`` / ``save_bustype_json``::

    {
        "schema": "groundfield.bustype", "schema_version": 1,
        "name": ..., "description": ..., "system_type": ..., "voltage_level": ...,
        "impedance_formula": "<Z(rho, f) as text>",
        "samples": {"frequency_Hz": [...], "rho_Ohm_m": [...],
                    "Z_real_Ohm": [...], "Z_imag_Ohm": [...]},
        "metadata": {"fit_method": ..., "quantity": ..., "rho_preset": ...,
                     "electrode_name": ..., "soil_summary": ...,
                     "source": "groundfield <version>", "created_at_utc": ...}
    }

:func:`characteristic_from_bustype_spec` turns such a document into an
:class:`~groundinsight.EarthingCharacteristic`; it reads plain JSON and needs
no groundfield installation, so a computation made in a groundfield notebook
can be taken over on a machine that has only groundinsight.

How the document maps onto a characteristic
-------------------------------------------
* The samples are the solver's own numbers, so they become the table: the
  distinct ``rho_Ohm_m`` and ``frequency_Hz`` values, sorted, are the grid,
  and every grid point must occur exactly once -- a standard-form fit
  samples a full ``rho x f`` grid, a vector fit one ``rho`` at several
  frequencies (a table with one row, constant in ``rho``). Samples that do
  not form a full grid are refused rather than filled in: a gap would be
  interpolated over without anyone having computed it.
* The formula is kept as ``impedance_formula``, so the same characteristic
  can take its impedance from the fit instead (``impedance_source=
  "formula"``); a document without samples can only be imported that way.
* Version 1 of the document has no touch or transfer factors; the
  characteristic carries none. groundinsight's own characterisation adds
  them.
* ``name``, ``system_type`` and ``voltage_level`` describe a bus type of
  the document's own; the characteristic is attached to an existing bus or
  bus type instead and keeps only the name.
* Provenance: ``metadata["source"]`` gives the groundfield version,
  ``created_at_utc`` the date, ``rho_preset`` the resistivity preset of the
  ``rho`` axis, ``electrode_name`` the geometry and ``soil_summary`` the
  soil; ``input_hash`` is the SHA-256 of the document in canonical JSON
  (sorted keys, no whitespace), so the same document is recognised however
  it was formatted.

The formula is untrusted text like any formula of a file and goes through
the formula guard of :class:`~groundinsight.EarthingCharacteristic`.
"""

import hashlib
import json
import logging
import math
import os
import re
from datetime import UTC, datetime
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple, Union

import numpy as np

from ..models.characteristic import CharacteristicProvenance, EarthingCharacteristic

logger = logging.getLogger(__name__)

__all__ = [
    "BUSTYPE_SCHEMA",
    "BUSTYPE_SCHEMA_VERSIONS",
    "ELECTRODES",
    "EXTENSION_KEY",
    "add_electrode",
    "available",
    "bustype_document",
    "characteristic_from_bustype_spec",
    "characterize",
    "electrode_world",
    "read_bustype_json",
    "scaled_soils",
    "write_bustype_json",
]

BUSTYPE_SCHEMA: str = "groundfield.bustype"
"""Schema name of groundfield's BusType document."""

BUSTYPE_SCHEMA_VERSIONS: Tuple[int, ...] = (1,)
"""Versions of the BusType document this module reads."""

_SAMPLE_KEYS = ("rho_Ohm_m", "frequency_Hz", "Z_real_Ohm", "Z_imag_Ohm")

EXTENSION_KEY: str = "groundinsight_characteristic"
"""Metadata entry of a document written by :func:`bustype_document`: the grid,
the touch and transfer factors, the impedance source, the name and the
provenance of the characteristic -- what version 1 of the document has no
field for. Other readers ignore it (the metadata is free-form)."""


def _check_header(document: Any) -> None:
    """
    Require a BusType document of a version this module reads.

    Parameters
    ----------
    document : Any
        The parsed JSON.

    Raises
    ------
    ValueError
        If it is not an object, has another schema name, or a version
        outside :data:`BUSTYPE_SCHEMA_VERSIONS`.
    """
    if not isinstance(document, dict):
        raise ValueError(
            f"a groundfield BusType document is a JSON object, got {type(document).__name__}"
        )
    if document.get("schema") != BUSTYPE_SCHEMA:
        raise ValueError(
            f"unexpected schema {document.get('schema')!r}; a groundfield BusType "
            f"document has schema {BUSTYPE_SCHEMA!r}"
        )
    version = document.get("schema_version")
    if isinstance(version, bool) or version not in BUSTYPE_SCHEMA_VERSIONS:
        raise ValueError(
            f"unsupported schema_version {version!r} of the groundfield BusType document; "
            f"this release reads version {', '.join(map(str, BUSTYPE_SCHEMA_VERSIONS))}"
        )


def _finite_number(value: Any) -> bool:
    """
    Whether a JSON value is a finite number (``null`` and booleans are not).

    Parameters
    ----------
    value : Any
        The value.

    Returns
    -------
    bool
        ``True`` for a finite ``int`` or ``float``.
    """
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def _sample_columns(samples: Any) -> List[List[Any]]:
    """
    The four sample lists of a document, checked for shape.

    Parameters
    ----------
    samples : Any
        The ``samples`` object of the document.

    Returns
    -------
    list of list
        ``rho_Ohm_m``, ``frequency_Hz``, ``Z_real_Ohm``, ``Z_imag_Ohm``;
        empty lists without samples.

    Raises
    ------
    ValueError
        If ``samples`` is not an object, or its lists are not lists of equal
        length.
    """
    if samples is None:
        samples = {}
    if not isinstance(samples, dict):
        raise ValueError("samples must be a JSON object of four lists")
    columns = [samples.get(key, []) for key in _SAMPLE_KEYS]
    if not all(isinstance(column, list) for column in columns):
        raise ValueError(f"samples {', '.join(_SAMPLE_KEYS)} must be lists")
    lengths = [len(column) for column in columns]
    if len(set(lengths)) > 1:
        raise ValueError(
            "the sample lists must have equal length, got "
            + ", ".join(f"{key}: {n}" for key, n in zip(_SAMPLE_KEYS, lengths, strict=True))
        )
    return columns


def _sample_points(samples: Any) -> List[Tuple[float, float, complex]]:
    """
    The samples as ``(rho, f, Z)`` triples.

    Parameters
    ----------
    samples : Any
        The ``samples`` object of the document.

    Returns
    -------
    list of tuple
        One triple per sample, in document order; empty without samples.

    Raises
    ------
    ValueError
        As :func:`_sample_columns`, and if a value is missing or not a
        finite number.
    """
    points = []
    for index, values in enumerate(zip(*_sample_columns(samples), strict=True)):
        if not all(map(_finite_number, values)):
            raise ValueError(
                f"sample {index} is not finite ({dict(zip(_SAMPLE_KEYS, values, strict=True))}); "
                "a table needs a value at every grid point"
            )
        rho, f, real, imag = (float(v) for v in values)
        points.append((rho, f, complex(real, imag)))
    return points


def _by_point(points: List[Tuple[float, float, complex]]) -> Dict[Tuple[float, float], complex]:
    """
    The samples keyed by grid point.

    Parameters
    ----------
    points : list of tuple
        ``(rho, f, Z)`` triples.

    Returns
    -------
    dict
        ``(rho, f)`` to ``Z``.

    Raises
    ------
    ValueError
        If a grid point occurs twice.
    """
    values: Dict[Tuple[float, float], complex] = {}
    for rho, f, z in points:
        if (rho, f) in values:
            raise ValueError(f"the sample at rho = {rho:g} Ohm*m, f = {f:g} Hz occurs twice")
        values[(rho, f)] = z
    return values


def _grid(points: List[Tuple[float, float, complex]]) -> Tuple[Tuple[float, ...], Tuple[float, ...], List[List[complex]]]:
    """
    Arrange samples on their ``rho x f`` grid.

    Parameters
    ----------
    points : list of tuple
        ``(rho, f, Z)`` triples.

    Returns
    -------
    rho, frequency : tuple of float
        The sorted distinct values.
    table : list of list of complex
        ``table[i][j]`` at ``rho[i]``, ``frequency[j]``.

    Raises
    ------
    ValueError
        If a grid point occurs twice or one is missing.
    """
    values = _by_point(points)
    rhos = tuple(sorted({rho for rho, _ in values}))
    freqs = tuple(sorted({f for _, f in values}))
    if len(values) != len(rhos) * len(freqs):
        missing = [(r, f) for r in rhos for f in freqs if (r, f) not in values]
        raise ValueError(
            f"the samples do not form a full grid of {len(rhos)} rho x {len(freqs)} f "
            f"values; missing (rho, f): {_points_text(missing)}"
        )
    return rhos, freqs, [[values[(r, f)] for f in freqs] for r in rhos]


def _points_text(points: List[Tuple[float, float]], shown: int = 5) -> str:
    """
    The first grid points of a list, for a message.

    Parameters
    ----------
    points : list of tuple of float
        ``(rho, f)`` pairs.
    shown : int
        How many to write out.

    Returns
    -------
    str
        ``"(100, 50), (100, 150) ..."``.
    """
    text = ", ".join(f"({r:g}, {f:g})" for r, f in points[:shown])
    return text + (" ..." if len(points) > shown else "")


def _provenance(document: Dict[str, Any]) -> CharacteristicProvenance:
    """
    The provenance of a characteristic read from a document.

    Parameters
    ----------
    document : dict
        The BusType document.

    Returns
    -------
    CharacteristicProvenance
        See the module docstring for the mapping.
    """
    metadata = document.get("metadata")
    metadata = metadata if isinstance(metadata, dict) else {}

    def text(key: str) -> Optional[str]:
        """
        A metadata entry if it is a non-empty string.

        Parameters
        ----------
        key : str
            The entry.

        Returns
        -------
        str or None
            The entry, or ``None``.
        """
        value = metadata.get(key)
        return value if isinstance(value, str) and value.strip() else None

    source = text("source") or ""
    version = source.split(" ", 1)[1].strip() if source.startswith("groundfield ") else None
    details = [f"{key}={metadata[key]}" for key in ("fit_method", "quantity", "rho_definition")
               if text(key)]
    canonical = json.dumps(document, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    soil = text("soil_summary")
    return CharacteristicProvenance(
        source=BUSTYPE_SCHEMA,
        groundfield_version=version,
        soil={"summary": soil} if soil else None,
        geometry_id=text("electrode_name"),
        rho_preset=text("rho_preset"),
        created=text("created_at_utc"),
        input_hash="sha256:" + hashlib.sha256(canonical.encode("utf-8")).hexdigest(),
        note="; ".join(details) or None,
    )


def _extension(
    document: Dict[str, Any], rhos: Tuple[float, ...], freqs: Tuple[float, ...]
) -> Optional[Dict[str, Any]]:
    """
    What groundinsight wrote into the metadata of a document, checked against its grid.

    Parameters
    ----------
    document : dict
        The BusType document.
    rhos, freqs : tuple of float
        The grid of its samples.

    Returns
    -------
    dict or None
        ``metadata["groundinsight_characteristic"]``, or ``None`` for a
        document of another writer.

    Raises
    ------
    ValueError
        If the entry is not an object or names another grid than the
        samples.
    """
    metadata = document.get("metadata")
    entry = metadata.get(EXTENSION_KEY) if isinstance(metadata, dict) else None
    if entry is None:
        return None
    if not isinstance(entry, dict):
        raise ValueError(f"metadata[{EXTENSION_KEY!r}] must be a JSON object")
    grid = (tuple(entry.get("rho_Ohm_m") or ()), tuple(entry.get("frequency_Hz") or ()))
    if grid != (rhos, freqs):
        raise ValueError(
            f"metadata[{EXTENSION_KEY!r}] holds touch factors on another grid than the "
            "samples; the document has been edited inconsistently"
        )
    return entry


def _formula_grid(
    impedance_source: str,
    rho_Ohm_m: Optional[Sequence[float]],
    frequency_Hz: Optional[Sequence[float]],
) -> Dict[str, Any]:
    """
    The grid of a document without samples, which only its formula can serve.

    Parameters
    ----------
    impedance_source : str
        Where the impedance is to come from.
    rho_Ohm_m, frequency_Hz : sequence of float, optional
        The grid the caller gives.

    Returns
    -------
    dict
        ``rho_Ohm_m`` and ``frequency_Hz``.

    Raises
    ------
    ValueError
        If the impedance is to come from the table, or the grid is missing.
    """
    if impedance_source == "table" or rho_Ohm_m is None or frequency_Hz is None:
        raise ValueError(
            "the groundfield BusType document has no samples, so there is no table to "
            "import; import its formula with impedance_source='formula' and give the "
            "grid (rho_Ohm_m, frequency_Hz) the characteristic is defined on"
        )
    return {"rho_Ohm_m": tuple(rho_Ohm_m), "frequency_Hz": tuple(frequency_Hz)}


def _table_fields(
    document: Dict[str, Any],
    points: List[Tuple[float, float, complex]],
    impedance_source: Optional[str],
) -> Dict[str, Any]:
    """
    The fields of a characteristic that come from the samples of a document.

    Parameters
    ----------
    document : dict
        The BusType document.
    points : list of tuple
        Its samples, ``(rho, f, Z)``.
    impedance_source : str, optional
        The caller's choice, which wins over a recorded one.

    Returns
    -------
    dict
        The grid and the impedance table; for a document of
        :func:`bustype_document` also the factors, name, provenance and
        impedance source it records.
    """
    rhos, freqs, table = _grid(points)
    fields: Dict[str, Any] = {"rho_Ohm_m": rhos, "frequency_Hz": freqs, "impedance_Ohm": table}
    entry = _extension(document, rhos, freqs)
    if entry is not None:
        fields.update(
            k_touch=entry.get("k_touch"), k_transfer=entry.get("k_transfer"),
            name=entry.get("name"), provenance=entry.get("provenance"),
            impedance_source=impedance_source or entry.get("impedance_source", "table"),
        )
    return fields


def characteristic_from_bustype_spec(
    document: Dict[str, Any],
    *,
    impedance_source: Optional[str] = None,
    rho_Ohm_m: Optional[Sequence[float]] = None,
    frequency_Hz: Optional[Sequence[float]] = None,
    name: Optional[str] = None,
) -> EarthingCharacteristic:
    """
    An earthing characteristic from a groundfield BusType document.

    Parameters
    ----------
    document : dict
        The parsed JSON document (schema ``"groundfield.bustype"``, version
        1).
    impedance_source : {"table", "formula"}, optional
        Where the characteristic takes the impedance from: the samples or
        the document's formula. Default: what a document written by
        :func:`bustype_document` records, else ``"table"``.
    rho_Ohm_m, frequency_Hz : sequence of float, optional
        The grid of a document without samples; only with
        ``impedance_source="formula"``. Ignored when there are samples.
    name : str, optional
        Name of the characteristic; default the document's.

    Returns
    -------
    EarthingCharacteristic
        The table of the samples, the formula and the provenance (see the
        module docstring). A document written by :func:`bustype_document`
        gives back the characteristic it was written from, touch factors
        and provenance included.

    Raises
    ------
    ValueError
        If the document is not a version-1 BusType document, its samples are
        not finite or do not form a full grid, a sampled impedance is not
        passive, the formula does not pass the formula guard, groundinsight's
        metadata entry names another grid, or there are no samples and the
        impedance is to come from the table.

    Examples
    --------
    >>> from groundinsight.integrations.groundfield import characteristic_from_bustype_spec
    >>> document = {"schema": "groundfield.bustype", "schema_version": 1,
    ...     "name": "ring", "system_type": "MV/LV", "voltage_level": 20.0,
    ...     "impedance_formula": "0.01*rho + 0.0002*j*f",
    ...     "samples": {"rho_Ohm_m": [100.0, 100.0], "frequency_Hz": [50.0, 150.0],
    ...                 "Z_real_Ohm": [1.0, 1.0], "Z_imag_Ohm": [0.01, 0.03]},
    ...     "metadata": {"source": "groundfield 2026.11.0"}}
    >>> characteristic = characteristic_from_bustype_spec(document)
    >>> characteristic.frequency_Hz, characteristic.provenance.groundfield_version
    ((50.0, 150.0), '2026.11.0')
    """
    _check_header(document)
    points = _sample_points(document.get("samples"))
    formula = document.get("impedance_formula")
    if formula is not None and not isinstance(formula, str):
        raise ValueError("impedance_formula of the groundfield BusType document must be a string")
    fields: Dict[str, Any] = {
        "impedance_formula": formula,
        "impedance_source": impedance_source or "table",
        "name": str(document.get("name") or "") or None,
        "provenance": _provenance(document),
    }
    if points:
        fields.update(_table_fields(document, points, impedance_source))
    else:
        fields.update(_formula_grid(fields["impedance_source"], rho_Ohm_m, frequency_Hz))
    if name is not None:
        fields["name"] = name
    characteristic = EarthingCharacteristic(**fields)
    logger.debug(
        "read groundfield BusType document '%s': %d x %d grid",
        document.get("name"), len(characteristic.rho_Ohm_m), len(characteristic.frequency_Hz),
    )
    return characteristic


def read_bustype_json(
    path: Union[str, "os.PathLike[str]"], **options: Any
) -> EarthingCharacteristic:
    """
    Read a groundfield BusType JSON file as an earthing characteristic.

    Parameters
    ----------
    path : str or os.PathLike
        The file, UTF-8.
    **options
        Keyword arguments of :func:`characteristic_from_bustype_spec`.

    Returns
    -------
    EarthingCharacteristic
        The characteristic.

    Raises
    ------
    OSError
        If the file cannot be read.
    ValueError
        If it is not valid JSON, or as for
        :func:`characteristic_from_bustype_spec`.

    Examples
    --------
    >>> from groundinsight.integrations.groundfield import read_bustype_json
    >>> read_bustype_json("ons_typ_a.json").name  # doctest: +SKIP
    'ONS Typ A'
    """
    with open(os.fspath(path), encoding="utf-8-sig") as handle:
        text = handle.read()
    try:
        document = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ValueError(f"'{os.fspath(path)}' is not valid JSON: {exc}") from exc
    return characteristic_from_bustype_spec(document, **options)


# ---------------------------------------------------------------------------
# writing documents
# ---------------------------------------------------------------------------


def bustype_document(
    characteristic: EarthingCharacteristic,
    *,
    system_type: str,
    voltage_level: float,
    name: Optional[str] = None,
    description: Optional[str] = None,
) -> Dict[str, Any]:
    """
    The groundfield BusType document of a characteristic.

    groundfield reads it with ``load_bustype_json`` as a bus type of its
    own; :func:`characteristic_from_bustype_spec` reads it back as the same
    characteristic.

    Parameters
    ----------
    characteristic : EarthingCharacteristic
        The characteristic; it needs an ``impedance_formula``, which the
        document requires.
    system_type : str
        ``system_type`` of the document's bus type.
    voltage_level : float
        ``voltage_level`` of the document's bus type, in kV.
    name : str, optional
        Name of the bus type; default the characteristic's name, else
        ``"characteristic"``.
    description : str, optional
        Description of the bus type.

    Returns
    -------
    dict
        A strict-JSON document of schema ``"groundfield.bustype"``, version
        1: the table as samples (rho-major), the formula, and the metadata
        groundfield writes (``source``, ``created_at_utc``, ``rho_preset``,
        ``electrode_name``, ``soil_summary``, ``rho_definition``), plus
        :data:`EXTENSION_KEY` with what version 1 has no field for.

    Raises
    ------
    ValueError
        If the characteristic has no formula or no table.

    Examples
    --------
    >>> import groundinsight as gi
    >>> from groundinsight.integrations.groundfield import bustype_document
    >>> table = gi.EarthingCharacteristic(rho_Ohm_m=(100.0,), frequency_Hz=(50.0,),
    ...     impedance_Ohm=[[1.0]], impedance_formula="rho / 100")
    >>> bustype_document(table, system_type="MV/LV", voltage_level=20.0)["schema"]
    'groundfield.bustype'
    """
    if characteristic.impedance_formula is None:
        raise ValueError(
            "a groundfield BusType document needs an impedance formula; give the "
            "characteristic one (characterize with fit=True fits it)"
        )
    if characteristic.impedance_Ohm is None:
        raise ValueError("a groundfield BusType document needs the table of the impedance")
    return {
        "schema": BUSTYPE_SCHEMA,
        "schema_version": BUSTYPE_SCHEMA_VERSIONS[-1],
        "name": name or characteristic.name or "characteristic",
        "description": description,
        "system_type": str(system_type),
        "voltage_level": float(voltage_level),
        "impedance_formula": characteristic.impedance_formula,
        "samples": _document_samples(characteristic),
        "metadata": json.loads(json.dumps(_document_metadata(characteristic), allow_nan=False)),
    }


def _document_samples(characteristic: EarthingCharacteristic) -> Dict[str, List[float]]:
    """
    The table of a characteristic as the four sample lists of a document.

    Parameters
    ----------
    characteristic : EarthingCharacteristic
        The characteristic, with a table.

    Returns
    -------
    dict of str to list of float
        ``frequency_Hz``, ``rho_Ohm_m``, ``Z_real_Ohm``, ``Z_imag_Ohm``,
        rho-major.
    """
    points = [(r, f, characteristic.impedance_Ohm[i][j])
              for i, r in enumerate(characteristic.rho_Ohm_m)
              for j, f in enumerate(characteristic.frequency_Hz)]
    return {
        "frequency_Hz": [f for _, f, _ in points],
        "rho_Ohm_m": [r for r, _, _ in points],
        "Z_real_Ohm": [z.real for _, _, z in points],
        "Z_imag_Ohm": [z.imag for _, _, z in points],
    }


def _document_metadata(characteristic: EarthingCharacteristic) -> Dict[str, Any]:
    """
    The metadata block of :func:`bustype_document`.

    Parameters
    ----------
    characteristic : EarthingCharacteristic
        The characteristic.

    Returns
    -------
    dict
        groundfield's metadata entries, filled from the provenance, and
        :data:`EXTENSION_KEY`.
    """
    stated = characteristic.provenance
    provenance = stated or CharacteristicProvenance()
    return {
        "fit_method": None,
        "quantity": "cluster",
        "rho_definition": "effective_resistivity",
        "rho_preset": provenance.rho_preset,
        "electrode_name": provenance.geometry_id,
        "soil_summary": json.dumps(provenance.soil, sort_keys=True) if provenance.soil else None,
        "source": "groundinsight",
        "created_at_utc": provenance.created,
        EXTENSION_KEY: {
            "rho_Ohm_m": list(characteristic.rho_Ohm_m),
            "frequency_Hz": list(characteristic.frequency_Hz),
            "k_touch": characteristic.k_touch,
            "k_transfer": characteristic.k_transfer,
            "impedance_source": characteristic.impedance_source,
            "name": characteristic.name,
            "provenance": stated.model_dump(mode="json") if stated is not None else None,
        },
    }


def write_bustype_json(
    path: Union[str, "os.PathLike[str]"], characteristic: EarthingCharacteristic, **options: Any
) -> None:
    """
    Write a characteristic as a groundfield BusType JSON file (UTF-8).

    Parameters
    ----------
    path : str or os.PathLike
        The file; replaced if it exists.
    characteristic : EarthingCharacteristic
        The characteristic.
    **options
        Keyword arguments of :func:`bustype_document` (``system_type`` and
        ``voltage_level`` are required).

    Raises
    ------
    ValueError
        As for :func:`bustype_document`.
    OSError
        If the file cannot be written.

    Examples
    --------
    >>> from groundinsight.integrations.groundfield import write_bustype_json
    >>> write_bustype_json("station.json", characteristic,
    ...                    system_type="MV/LV", voltage_level=20.0)  # doctest: +SKIP
    """
    document = bustype_document(characteristic, **options)
    text = json.dumps(document, indent=2, ensure_ascii=False, allow_nan=False)
    with open(os.fspath(path), "w", encoding="utf-8") as handle:
        handle.write(text + "\n")


# ---------------------------------------------------------------------------
# characterising an electrode with groundfield
# ---------------------------------------------------------------------------

_RHO_KEY = re.compile(r"^rho(_\d+)?$")


def require_groundfield() -> Any:
    """
    Import groundfield, or say how to get it.

    Internal API: the service layer's characteristic job uses it; not in
    ``__all__``, may change in any release.

    Returns
    -------
    module
        ``groundfield``.

    Raises
    ------
    ImportError
        If groundfield is not installed.
    """
    try:
        import groundfield
    except ImportError as exc:
        raise ImportError(
            "characterize needs groundfield, the field solver: install it with "
            "'pip install groundfield' (until its release 2026.11.0 is on PyPI: "
            "'pip install -e <path to the groundfield repository>'). Reading and "
            "writing BusType documents needs no groundfield."
        ) from exc
    return groundfield


def _scaled(value: Any, factor: float) -> Any:
    """
    A copy of a dumped soil model with every resistivity multiplied by ``factor``.

    Parameters
    ----------
    value : Any
        The ``model_dump()`` of a soil, or a part of it.
    factor : float
        The factor.

    Returns
    -------
    Any
        The scaled copy; keys ``rho``, ``rho_1``, ``rho_2``, ... are scaled,
        thicknesses and permittivities are kept.
    """
    if isinstance(value, dict):
        return {key: item * factor if _RHO_KEY.match(key) and isinstance(item, (int, float))
                else _scaled(item, factor) for key, item in value.items()}
    if isinstance(value, list):
        return [_scaled(item, factor) for item in value]
    return value


def scaled_soils(soil: Any, factors: Sequence[float]) -> List[Any]:
    """
    Copies of a groundfield soil with every layer's resistivity scaled.

    Scaling every layer by the same factor keeps the shape of the field and
    scales the effective resistivity by that factor, so a characteristic
    over scaled soils is the electrode in "the same soil, wetter or drier".

    Parameters
    ----------
    soil : groundfield soil model
        ``HomogeneousSoil``, ``TwoLayerSoil`` or ``MultiLayerSoil``.
    factors : sequence of float
        Positive, finite factors.

    Returns
    -------
    list
        One soil of the same class per factor; thicknesses unchanged.

    Raises
    ------
    ValueError
        If a factor is not finite and positive.

    Examples
    --------
    >>> import groundfield as gf  # doctest: +SKIP
    >>> [s.rho_2 for s in scaled_soils(gf.TwoLayerSoil(rho_1=100, rho_2=500, h_1=2),
    ...                                [0.5, 2.0])]  # doctest: +SKIP
    [250.0, 1000.0]
    """
    for factor in factors:
        if not (math.isfinite(factor) and factor > 0.0):
            raise ValueError(f"a soil scale factor must be finite and positive, got {factor!r}")
    data = soil.model_dump()
    return [type(soil).model_validate(_scaled(data, float(factor))) for factor in factors]


def _engine_for(gf: Any, engine: Any, frequencies: Optional[Sequence[float]],
                segment_length: float) -> Any:
    """
    The groundfield engine of a characterisation, with positive frequencies.

    Parameters
    ----------
    gf : module
        groundfield.
    engine : groundfield.Engine, optional
        The caller's engine; its frequencies are the table's.
    frequencies : sequence of float, optional
        The frequencies of a default engine (``create_engine`` with
        ``segment_length``).
    segment_length : float
        Segment length of a default engine, in m.

    Returns
    -------
    groundfield.Engine
        The engine.

    Raises
    ------
    ValueError
        Without engine and frequencies, or for a frequency that is not
        positive (the table's axis is logarithmic).
    """
    if engine is None:
        if frequencies is None:
            raise ValueError("characterize needs frequencies, or an engine that has them")
        engine = gf.create_engine(frequencies=list(frequencies), segment_length=segment_length)
    if not engine.frequencies or not all(f > 0.0 for f in engine.frequencies):
        raise ValueError(
            f"the frequencies of a characteristic must be positive, got {list(engine.frequencies)}"
        )
    return engine


def _solve_all(gf: Any, build_world: Callable[[Any], Any], soils: List[Any], engine: Any,
               electrode: str, rho_preset: Optional[str]) -> List[Tuple[float, Any, Any]]:
    """
    Build and solve the world of every soil, sorted by effective resistivity.

    Parameters
    ----------
    gf : module
        groundfield.
    build_world : callable
        ``build_world(soil) -> World``.
    soils : list
        groundfield soil models.
    engine : groundfield.Engine
        The engine.
    electrode : str
        The electrode characterised; a world without a source gets 1 A into
        it.
    rho_preset : str, optional
        The preset of ``effective_resistivity``.

    Returns
    -------
    list of tuple
        ``(rho_eff, world, result)``, by increasing ``rho_eff``.

    Raises
    ------
    ValueError
        If a world has no electrode ``electrode`` or two soils have the same
        effective resistivity.
    """
    runs = []
    for soil in soils:
        world = build_world(soil)
        names = [e.name for e in world.electrodes]
        if electrode not in names:
            raise ValueError(f"the world has no electrode '{electrode}' (electrodes: {names})")
        if not world.sources:
            gf.create_source(world, attached_to=electrode, magnitude=1.0)
        options = {"preset": rho_preset} if rho_preset is not None else {}
        rho = float(gf.effective_resistivity(soil, **options))
        runs.append((rho, world, engine.solve(world)))
    runs.sort(key=lambda run: run[0])
    for (a, _, _), (b, _, _) in zip(runs[:-1], runs[1:], strict=True):
        if a == b:
            raise ValueError(f"the effective resistivity {a:g} Ohm*m occurs twice")
    return runs


def _factors(gf: Any, world: Any, result: Any, electrode: str, frequency_index: int,
             touch: Dict[str, Any], targets: Optional[Any]) -> Tuple[Optional[float], Optional[float]]:
    """
    ``k_touch`` and ``k_transfer`` of one solved world at one frequency.

    Parameters
    ----------
    gf : module
        groundfield.
    world, result : groundfield.World, groundfield.FieldResult
        The solved world.
    electrode : str
        The electrode.
    frequency_index : int
        The frequency.
    touch : dict
        Keyword arguments of ``touch_voltage_envelope`` (``distance``,
        ``n_angles``, ``interior``), or empty for no ``k_touch``.
    targets : numpy.ndarray, optional
        Target points, shape ``(n, 3)``.

    Returns
    -------
    tuple of float or None
        The two factors; ``None`` where not asked for.

    Raises
    ------
    ValueError
        If the electrode potential is zero (the factors are undefined).
    """
    u_e = complex(result.electrode_potentials[electrode][frequency_index])
    if u_e == 0:
        raise ValueError(f"the potential of '{electrode}' is zero; the factors are undefined")
    k_touch = k_transfer = None
    if touch:
        _, voltages = gf.touch_voltage_envelope(result, world, electrode=electrode,
                                                frequency_index=frequency_index, **touch)
        k_touch = float(max(abs(v) for v in voltages) / abs(u_e))
    if targets is not None:
        phi = result.potential(targets, frequency_index=frequency_index)
        k_transfer = float(max(abs(u_e - p) for p in phi) / abs(u_e))
    return k_touch, k_transfer


def _evaluation_options(
    touch_distance: Optional[float],
    n_angles: int,
    touch_interior: Union[bool, str],
    transfer_targets: Optional[Sequence[Sequence[float]]],
) -> Tuple[Dict[str, Any], Optional[Any]]:
    """
    The envelope settings and target points of :func:`characterize`, checked.

    Parameters
    ----------
    touch_distance : float or None
        Reach of the touch voltage; ``None`` for no ``k_touch``.
    n_angles : int
        Directions of the envelope.
    touch_interior : bool or str
        ``interior`` of the envelope.
    transfer_targets : sequence of (x, y, z), optional
        Target points.

    Returns
    -------
    touch : dict
        Keyword arguments of ``touch_voltage_envelope``; empty for no
        ``k_touch``.
    targets : numpy.ndarray or None
        The points, shape ``(n, 3)``.

    Raises
    ------
    ValueError
        If ``touch_distance`` is not positive.
    """
    touch: Dict[str, Any] = {}
    if touch_distance is not None:
        if not (math.isfinite(touch_distance) and touch_distance > 0):
            raise ValueError(f"touch_distance must be positive or None, got {touch_distance!r}")
        touch = {"distance": touch_distance, "n_angles": n_angles, "interior": touch_interior}
    if transfer_targets is None:
        return touch, None
    return touch, np.asarray(transfer_targets, dtype=float).reshape(-1, 3)


def _tables(gf: Any, runs: List[Tuple[float, Any, Any]], electrode: str,
            touch: Dict[str, Any], targets: Optional[Any], quantity: str = "cluster") -> Tuple[Any, Any, Any]:
    """
    The impedance and factor tables of the solved worlds.

    Parameters
    ----------
    gf : module
        groundfield.
    runs : list of tuple
        The output of :func:`_solve_all`.
    electrode : str
        The electrode.
    touch : dict
        Envelope settings; empty for no ``k_touch``.
    targets : numpy.ndarray, optional
        Target points; ``None`` for no ``k_transfer``.
    quantity : {"cluster", "input"}
        Cluster impedance of the electrode, or input impedance of the source.

    Returns
    -------
    tuple
        ``(impedance, k_touch, k_transfer)``, rows by resistivity; a factor
        table is ``None`` when it was not asked for.
    """
    impedance, k_touch, k_transfer = [], [], []
    for _, world, result in runs:
        values = result.cluster_impedance(electrode) if quantity == "cluster" else result.input_impedance()
        impedance.append([complex(z) for z in values])
        factors = [_factors(gf, world, result, electrode, j, touch, targets)
                   for j in range(len(result.frequencies))]
        k_touch.append([k for k, _ in factors])
        k_transfer.append([k for _, k in factors])
    return impedance, (k_touch if touch else None), (k_transfer if targets is not None else None)


def _note(electrode: str, touch: Dict[str, Any], targets: Optional[Any], quantity: str = "cluster") -> str:
    """
    The provenance note of :func:`characterize`: how the table was evaluated.

    Parameters
    ----------
    electrode : str
        The electrode.
    touch : dict
        Envelope settings; empty for no ``k_touch``.
    targets : numpy.ndarray, optional
        Target points.
    quantity : {"cluster", "input"}
        Which impedance the table holds.

    Returns
    -------
    str
        One line.
    """
    parts = [f"cluster impedance of '{electrode}'" if quantity == "cluster"
             else f"input impedance of the source at '{electrode}'"]
    if touch:
        parts.append(f"k_touch: envelope at {touch['distance']} m, {touch['n_angles']} "
                     f"directions, interior={touch['interior']!r}")
    if targets is not None:
        parts.append(f"k_transfer: {len(targets)} target point(s)")
    return "; ".join(parts)


def _fitted_formula(gf: Any, runs: List[Tuple[float, Any, Any]], electrode: str,
                    rho_preset: Optional[str], quantity: str = "cluster") -> Optional[str]:
    """
    The k0..k5 standard form fitted by groundfield, as formula text.

    Parameters
    ----------
    gf : module
        groundfield.
    runs : list of tuple
        The output of :func:`_solve_all`.
    electrode : str
        The electrode whose cluster impedance is fitted.
    rho_preset : str, optional
        Recorded with the fit.
    quantity : {"cluster", "input"}
        The impedance fitted, as in :func:`characterize`.

    Returns
    -------
    str or None
        The formula as groundfield's exporter prints it; ``None`` for fewer
        than two resistivities (the standard form needs a spread in rho) or
        fewer than four samples.
    """
    if len(runs) < 2 or len(runs) * len(runs[0][2].frequencies) < 4:
        return None  # the standard form has more coefficients than samples
    fit = gf.rho_f_standard_from_results([result for _, _, result in runs],
                                         [rho for rho, _, _ in runs],
                                         electrode if quantity == "cluster" else None,
                                         quantity=quantity)
    document = gf.to_bustype_dict(fit, name=electrode, system_type="characteristic",
                                  voltage_level=0.0, rho_preset=rho_preset)
    return document["impedance_formula"]


def _jsonable(value: Any) -> Any:
    """
    A JSON form of what ``json.dumps`` cannot store (``json.dumps`` default).

    Parameters
    ----------
    value : Any
        A NumPy array or scalar, or another object.

    Returns
    -------
    Any
        ``value.tolist()`` where it has one, else ``str(value)``.
    """
    return value.tolist() if hasattr(value, "tolist") else str(value)


def _input_hash(runs: List[Tuple[float, Any, Any]], engine: Any, settings: Dict[str, Any]) -> str:
    """
    SHA-256 of what a characterisation computed from.

    Parameters
    ----------
    runs : list of tuple
        The output of :func:`_solve_all` (the worlds enter).
    engine : groundfield.Engine
        The engine (its settings enter).
    settings : dict
        The other inputs (electrode, preset, touch and transfer settings).

    Returns
    -------
    str
        ``"sha256:<hex>"`` of the canonical JSON of all of it.
    """
    payload = {
        "worlds": [world.model_dump(mode="json") for _, world, _ in runs],
        "engine": engine.model_dump(mode="json"),
        "settings": settings,
    }
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=_jsonable)
    return "sha256:" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def characterize(
    build_world: Callable[[Any], Any],
    soils: Sequence[Any],
    *,
    electrode: str,
    frequencies: Optional[Sequence[float]] = None,
    engine: Any = None,
    segment_length: float = 0.5,
    rho_preset: Optional[str] = None,
    touch_distance: Optional[float] = 1.0,
    touch_interior: Union[bool, str] = False,
    n_angles: int = 24,
    transfer_targets: Optional[Sequence[Sequence[float]]] = None,
    fit: bool = True,
    impedance_source: str = "table",
    name: Optional[str] = None,
    geometry_id: Optional[str] = None,
    quantity: str = "cluster",
) -> EarthingCharacteristic:
    r"""
    Compute the earthing characteristic of an electrode with groundfield.

    For every soil the world is built and solved, and the table is filled
    at the soil's effective resistivity (see the module docstring):

    * ``impedance_Ohm`` -- the cluster impedance of ``electrode``,
      $Z = \varphi_c / \sum I$ (``FieldResult.cluster_impedance``);
    * ``k_touch`` -- the largest prospective touch voltage around the
      electrode over its potential,
      $\max |U_T| / |U_E|$ with $U_T$ from ``touch_voltage_envelope``
      (feet ``touch_distance`` from the wires, ``n_angles`` directions,
      inside the loops with ``touch_interior``);
    * ``k_transfer`` -- the largest difference between the electrode
      potential and the earth-surface potential at the target points,
      $\max_P |U_E - \varphi(P)| / |U_E|$ (``FieldResult.potential``).

    Parameters
    ----------
    build_world : callable
        ``build_world(soil) -> groundfield.World`` with the electrode. A
        world without a source gets 1 A into ``electrode`` (the field is
        linear, so the current does not matter).
    soils : sequence of float or groundfield soil models
        One entry per row of the table: a number is homogeneous soil of that
        resistivity in Ohm*m; for layered soil see :func:`scaled_soils`.
    electrode : str
        The electrode characterised.
    frequencies : sequence of float, optional
        Positive frequencies in Hz, for an engine created here
        (``groundfield.create_engine`` with ``segment_length``).
    engine : groundfield.Engine, optional
        An engine of the caller instead; its frequencies are the table's.
    segment_length : float
        Segment length of the engine created here, in m.
    rho_preset : str, optional
        The preset of ``groundfield.effective_resistivity`` that turns a
        layered soil into the ``rho`` of the table; recorded in the
        provenance. Default groundfield's default preset.
    touch_distance : float or None
        Reach of the touch voltage, 1 m by EN 50522; ``None`` computes no
        ``k_touch``.
    touch_interior : bool or "meshes"
        ``interior`` of ``touch_voltage_envelope``: also inside the meshes of
        an open-air grid.
    n_angles : int
        Directions of the envelope.
    transfer_targets : sequence of (x, y, z), optional
        Target points of the transferred potential in m (house
        connections); without them no ``k_transfer``.
    fit : bool
        Also fit groundfield's k0..k5 standard form
        (``rho_f_standard_from_results``) and keep it as
        ``impedance_formula``; needs at least two soils.
    impedance_source : {"table", "formula"}
        Where a bus takes the impedance from.
    name : str, optional
        Name of the characteristic.
    geometry_id : str, optional
        An identifier of the geometry for the provenance; default
        ``electrode``.
    quantity : {"cluster", "input"}
        The impedance of the table: the cluster impedance of ``electrode``
        (default; an electrode alone, with what is bonded to it), or the
        driving-point impedance of the world's source
        (``FieldResult.input_impedance``) -- for a whole system fed at
        ``electrode``, such as a station with the houses on its PEN, whose
        conductors have an impedance and are no galvanic cluster.

    Returns
    -------
    EarthingCharacteristic
        The table, the factors, the formula and the provenance (groundfield
        version, backend, soils, preset, date, SHA-256 of the input).

    Raises
    ------
    ImportError
        If groundfield is not installed.
    ValueError
        For no soils, two soils of the same effective resistivity, a
        frequency that is not positive, an electrode the world does not
        have, or a ``touch_distance`` that is not positive.

    Examples
    --------
    >>> import groundfield as gf  # doctest: +SKIP
    >>> def station(soil):
    ...     world = gf.create_world(soil=soil)
    ...     gf.create_electrode(world, "ring", name="ring", center=(0, 0, 0.8), radius=3.0)
    ...     return world  # doctest: +SKIP
    >>> characteristic = characterize(station, [50.0, 100.0, 400.0], electrode="ring",
    ...                               frequencies=[50.0, 250.0])  # doctest: +SKIP
    """
    gf = require_groundfield()
    if not soils:
        raise ValueError("characterize needs at least one soil")
    touch, targets = _evaluation_options(touch_distance, n_angles, touch_interior, transfer_targets)
    engine = _engine_for(gf, engine, frequencies, segment_length)
    models = [gf.HomogeneousSoil(rho=float(s)) if isinstance(s, (int, float)) else s for s in soils]
    runs = _solve_all(gf, build_world, models, engine, electrode, rho_preset)
    if quantity not in ("cluster", "input"):
        raise ValueError(f"quantity must be 'cluster' or 'input', got {quantity!r}")
    impedance, k_touch, k_transfer = _tables(gf, runs, electrode, touch, targets, quantity)
    settings = {"electrode": electrode, "rho_preset": rho_preset, "touch": touch,
                "targets": targets, "fit": fit, "quantity": quantity}
    provenance = CharacteristicProvenance(
        source="groundfield",
        groundfield_version=str(getattr(gf, "__version__", "unknown")),
        solver=runs[0][2].backend,
        soil={"soils": [model.model_dump(mode="json") for model in models],
              "rho_definition": "effective_resistivity"},
        geometry_id=geometry_id or electrode,
        rho_preset=rho_preset,
        created=datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        input_hash=_input_hash(runs, engine, settings),
        note=_note(electrode, touch, targets, quantity),
    )
    logger.debug("characterised '%s' over %d soils", electrode, len(runs))
    return EarthingCharacteristic(
        rho_Ohm_m=tuple(rho for rho, _, _ in runs),
        frequency_Hz=tuple(float(f) for f in engine.frequencies),
        impedance_Ohm=impedance, k_touch=k_touch, k_transfer=k_transfer,
        impedance_formula=_fitted_formula(gf, runs, electrode, rho_preset, quantity) if fit else None,
        impedance_source=impedance_source, name=name, provenance=provenance,
    )



# ---------------------------------------------------------------------------
# electrode templates
# ---------------------------------------------------------------------------

#: The electrode kinds of :func:`add_electrode`, with their dimensions and defaults.
ELECTRODES: Dict[str, Dict[str, float]] = {
    "ring": {"radius_m": 2.5, "depth_m": 0.6, "rods": 4, "rod_length_m": 2.5, "rod_circle_m": 1.5},
    "foundation": {"size_m": 10.0, "depth_m": 0.8, "meshes": 2},
    "rod": {"length_m": 2.5, "depth_m": 0.0},
    "mesh": {"length_m": 50.0, "width_m": 100.0, "depth_m": 0.8, "mesh_m": 10.0,
             "rods": 0, "rod_length_m": 6.0, "rod_distance_m": 0.0},
}


def add_electrode(world: Any, kind: str, *, name: str, position: Tuple[float, float] = (0.0, 0.0),
                  **dimensions: float) -> str:
    """
    Add a typical earth electrode to a groundfield world.

    The same function builds the electrode alone, for its characteristic,
    and inside a larger world, so that both describe the same geometry.

    Parameters
    ----------
    world : groundfield.World
        The world.
    kind : {"ring", "foundation", "rod", "mesh"}
        A ring electrode (station) with driven rods inside, bonded to it; a
        square foundation electrode with a mesh (house); a single rod; a
        rectangular mesh electrode of ``length_m`` x ``width_m`` with meshes
        of about ``mesh_m`` (one mesh is a rectangular ring), optionally with
        rods bonded to it.
    name : str
        Name of the main electrode; rods are ``<name>_rod<k>``, the bare
        strips that join outlying rods to a mesh ``<name>_strip<k>``.
    position : tuple of float
        Centre ``(x, y)`` in m.
    **dimensions : float
        Overrides of :data:`ELECTRODES`: ``radius_m``, ``depth_m``,
        ``rods``, ``rod_length_m``, ``rod_circle_m``; ``size_m``,
        ``meshes``; ``length_m``; for a mesh ``length_m``, ``width_m``,
        ``mesh_m``, ``rods``, ``rod_length_m`` and ``rod_distance_m`` --
        ``0`` puts the rods at the corners, a distance puts them outside the
        mesh on its axes, each joined to it by a bare strip at the depth of
        the mesh.

    Returns
    -------
    str
        ``name``, the electrode to characterise or feed.

    Raises
    ------
    ValueError
        For an unknown kind or dimension.

    Examples
    --------
    >>> import groundfield as gf  # doctest: +SKIP
    >>> world = gf.create_world(soil=gf.HomogeneousSoil(rho=100.0))  # doctest: +SKIP
    >>> add_electrode(world, "foundation", name="H1", position=(25.0, 15.0))  # doctest: +SKIP
    'H1'
    """
    gf = require_groundfield()
    if kind not in ELECTRODES:
        raise ValueError(f"unknown electrode kind {kind!r}; kinds: {', '.join(ELECTRODES)}")
    unknown = sorted(set(dimensions) - set(ELECTRODES[kind]))
    if unknown:
        raise ValueError(f"electrode '{kind}' has no dimension {', '.join(unknown)}")
    d = {**ELECTRODES[kind], **dimensions}
    x, y = float(position[0]), float(position[1])
    if kind == "foundation":
        half = d["size_m"] / 2.0
        n = int(d["meshes"])
        gf.create_electrode(world, "grid_mesh", name=name, corner=(x - half, y - half, d["depth_m"]),
                            size=(d["size_m"], d["size_m"]), n_x=n, n_y=n)
    elif kind == "rod":
        gf.create_electrode(world, "rod", name=name, position=(x, y, d["depth_m"]), length=d["length_m"])
    elif kind == "mesh":
        _add_mesh(gf, world, name, x, y, d)
    else:
        gf.create_electrode(world, "ring", name=name, center=(x, y, d["depth_m"]), radius=d["radius_m"])
        for k in range(int(d["rods"])):
            angle = math.pi / 4 + 2 * math.pi * k / max(int(d["rods"]), 1)
            rod = f"{name}_rod{k}"
            gf.create_electrode(world, "rod", name=rod, length=d["rod_length_m"],
                                position=(x + d["rod_circle_m"] * math.cos(angle),
                                          y + d["rod_circle_m"] * math.sin(angle), d["depth_m"]))
            gf.create_conductor(world, name=f"{name}_bond{k}", start=name, end=rod)
    return name


def _mesh_rod_places(d: Dict[str, float]) -> List[Tuple[float, float]]:
    """
    Where the rods of a mesh electrode stand, relative to its centre.

    Parameters
    ----------
    d : dict
        The dimensions of the mesh.

    Returns
    -------
    list of (float, float)
        ``rods`` places: the corners (first one, then the opposite one, then
        the other two) for ``rod_distance_m == 0``, otherwise the points
        ``rod_distance_m`` outside the mesh on its axes (x first, then y).
    """
    half_x, half_y = d["length_m"] / 2.0, d["width_m"] / 2.0
    distance = d["rod_distance_m"]
    if distance > 0:
        places = [(half_x + distance, 0.0), (-half_x - distance, 0.0),
                  (0.0, half_y + distance), (0.0, -half_y - distance)]
    else:
        places = [(half_x, half_y), (-half_x, -half_y), (half_x, -half_y), (-half_x, half_y)]
    count = int(d["rods"])
    if count > len(places):
        raise ValueError(f"a mesh electrode takes at most {len(places)} rods, got {count}")
    return places[:count]


def _add_mesh(gf: Any, world: Any, name: str, x: float, y: float, d: Dict[str, float]) -> None:
    """
    Add a rectangular mesh electrode with its rods to a world.

    Parameters
    ----------
    gf : module
        groundfield.
    world : groundfield.World
        The world.
    name : str
        Name of the mesh.
    x, y : float
        Its centre in m.
    d : dict
        Its dimensions (:data:`ELECTRODES` ``"mesh"`` with the overrides).

    Raises
    ------
    ValueError
        For more rods than places.
    """
    half_x, half_y, depth = d["length_m"] / 2.0, d["width_m"] / 2.0, d["depth_m"]
    gf.create_electrode(world, "grid_mesh", name=name, corner=(x - half_x, y - half_y, depth),
                        size=(d["length_m"], d["width_m"]),
                        n_x=max(1, round(d["length_m"] / d["mesh_m"])),
                        n_y=max(1, round(d["width_m"] / d["mesh_m"])))
    for k, (dx, dy) in enumerate(_mesh_rod_places(d)):
        rod = f"{name}_rod{k}"
        gf.create_electrode(world, "rod", name=rod, length=d["rod_length_m"], position=(x + dx, y + dy, depth))
        gf.create_conductor(world, name=f"{name}_bond{k}", start=name, end=rod)
        if d["rod_distance_m"] > 0:
            # a bare earth conductor from the edge of the mesh to the rod: part of the electrode
            edge = (x + math.copysign(half_x, dx) if dx else x, y + math.copysign(half_y, dy) if dy else y)
            strip = f"{name}_strip{k}"
            gf.create_electrode(world, "strip", name=strip, start=(edge[0], edge[1], depth),
                                end=(x + dx, y + dy, depth))
            gf.create_conductor(world, name=f"{name}_strip_bond{k}", start=name, end=strip)


def electrode_world(kind: str, *, name: str = "E", **dimensions: float) -> Callable[[Any], Any]:
    """
    A ``build_world`` for :func:`characterize`: one electrode alone, at the origin.

    Parameters
    ----------
    kind : {"ring", "foundation", "rod", "mesh"}
        As in :func:`add_electrode`.
    name : str
        Name of the electrode.
    **dimensions : float
        As in :func:`add_electrode`.

    Returns
    -------
    callable
        ``build_world(soil) -> groundfield.World``.

    Examples
    --------
    >>> build = electrode_world("ring", radius_m=3.0)
    >>> callable(build)
    True
    """
    def build(soil: Any) -> Any:
        """
        The world of the electrode in ``soil``.

        Parameters
        ----------
        soil : groundfield soil model
            The soil.

        Returns
        -------
        groundfield.World
            The world.
        """
        gf = require_groundfield()
        world = gf.create_world(soil=soil)
        add_electrode(world, kind, name=name, **dimensions)
        return world

    return build


def available() -> bool:
    """
    Whether groundfield can be imported here.

    Returns
    -------
    bool
        ``True`` when :func:`characterize` can run.

    Examples
    --------
    >>> isinstance(available(), bool)
    True
    """
    import importlib.util

    return importlib.util.find_spec("groundfield") is not None

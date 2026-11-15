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
from typing import Any, Dict, List, Optional, Sequence, Tuple, Union

from ..models.characteristic import CharacteristicProvenance, EarthingCharacteristic

logger = logging.getLogger(__name__)

__all__ = [
    "BUSTYPE_SCHEMA",
    "BUSTYPE_SCHEMA_VERSIONS",
    "characteristic_from_bustype_spec",
    "read_bustype_json",
]

BUSTYPE_SCHEMA: str = "groundfield.bustype"
"""Schema name of groundfield's BusType document."""

BUSTYPE_SCHEMA_VERSIONS: Tuple[int, ...] = (1,)
"""Versions of the BusType document this module reads."""

_SAMPLE_KEYS = ("rho_Ohm_m", "frequency_Hz", "Z_real_Ohm", "Z_imag_Ohm")


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


def characteristic_from_bustype_spec(
    document: Dict[str, Any],
    *,
    impedance_source: str = "table",
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
    impedance_source : {"table", "formula"}
        Where the characteristic takes the impedance from: the samples
        (default) or the document's formula.
    rho_Ohm_m, frequency_Hz : sequence of float, optional
        The grid of a document without samples; only with
        ``impedance_source="formula"``. Ignored when there are samples.
    name : str, optional
        Name of the characteristic; default the document's ``name``.

    Returns
    -------
    EarthingCharacteristic
        The table of the samples, the formula, and the provenance (see the
        module docstring); no touch or transfer factors.

    Raises
    ------
    ValueError
        If the document is not a version-1 BusType document, its samples are
        not finite or do not form a full grid, a sampled impedance is not
        passive, the formula does not pass the formula guard, or there are no
        samples and ``impedance_source`` is ``"table"``.

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
        "impedance_source": impedance_source,
        "name": name if name is not None else str(document.get("name") or "") or None,
        "provenance": _provenance(document),
    }
    if points:
        rhos, freqs, table = _grid(points)
        fields.update(rho_Ohm_m=rhos, frequency_Hz=freqs, impedance_Ohm=table)
    elif impedance_source == "table" or rho_Ohm_m is None or frequency_Hz is None:
        raise ValueError(
            "the groundfield BusType document has no samples, so there is no table to "
            "import; import its formula with impedance_source='formula' and give the "
            "grid (rho_Ohm_m, frequency_Hz) the characteristic is defined on"
        )
    else:
        fields.update(rho_Ohm_m=tuple(rho_Ohm_m), frequency_Hz=tuple(frequency_Hz))
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

# models/characteristic.py
r"""
Earthing characteristics: a tabulated earthing impedance and touch factors.

A bus type describes its electrode by an impedance formula ``Z(rho, f)``.
For a station whose earthing has been computed with a field solver
(groundfield), a formula is a fit; the solver's own numbers are a table over
the specific earth resistance and the frequency. An
:class:`EarthingCharacteristic` carries that table, and with it two
dimensionless factors a formula has no place for:

``k_touch``
    the prospective touch voltage relative to the electrode potential,
    $k_\mathrm{touch} = U_\mathrm{vT} / U_E$ -- the maximum of the touch
    voltage envelope around the electrode (1 m reach) divided by its
    potential rise;
``k_transfer``
    the largest transferred potential relative to the electrode potential,
    $k_\mathrm{transfer} = \max_P |U_E - \varphi(P)| / |U_E|$ over the target
    locations $P$ of conductors that leave the station (house connections,
    pipes) -- what a person standing at $P$ touches.

How a characteristic enters a network
-------------------------------------
It hangs at :attr:`BusType.characteristic <groundinsight.BusType>` (shared by
all stations of one design) or at :attr:`Bus.characteristic
<groundinsight.Bus>` (one station computed on its own); the bus's own
characteristic wins over its type's, and without either the bus uses its
type's formula exactly as before. ``impedance_source`` decides where the
impedance comes from: ``"table"`` interpolates :attr:`impedance_Ohm`,
``"formula"`` evaluates :attr:`impedance_formula` (or, when that is unset, the
type's formula) -- so a characteristic can contribute only its touch factors.
The factors themselves always come from the table.

Interpolation, and why in these coordinates
-------------------------------------------
Between the grid points the table is interpolated bilinearly in
$x = \ln \rho$ and $y = \ln f$:

* $\ln |Z|$ -- the earthing resistance of an electrode is proportional to
  $\rho$ in homogeneous soil and close to a power law in $\rho$ and $f$
  otherwise, so $\ln|Z|$ is nearly linear in $(\ln\rho, \ln f)$ and a power
  law is reproduced exactly; interpolating $|Z|$ linearly would bend it;
* the angle of $Z$ -- a passive earthing impedance has $\Re Z \ge 0$, so the
  angle lies in $[-90°, 90°]$ and needs no unwrapping;
* $k_\mathrm{touch}$ and $k_\mathrm{transfer}$ -- ratios of potentials,
  which change slowly with $\ln\rho$ and $\ln f$.

At a grid point the stored value is returned unchanged (not
``exp(log(|Z|))``), so a network whose specific earth resistances and
frequencies are grid points reproduces the solver's numbers exactly.

Outside the grid the same bilinear form is continued from the edge cell
(maintainer decision): a power law in $\rho$ above the largest grid value is
what an electrode in homogeneous soil does, while clamping would
underestimate $|Z|$ there. A frequency at or below 0 Hz has no logarithm and
takes the value of the lowest grid frequency. Continued touch factors are
clipped at 0. Every value outside the grid is reported: :func:`groundinsight.run_fault`
and :func:`groundinsight.check_network` record the diagnostic
``CHARACTERISTIC_EXTRAPOLATED`` for each bus concerned. A grid with a single
value along an axis is constant along it.

Why the impedance is stored as ``complex``
------------------------------------------
Persisted impedances are :class:`~groundinsight.ComplexNumber` elsewhere.
This module is imported by :mod:`groundinsight.models.core_models`, which
defines ``ComplexNumber``, so it cannot use it without an import cycle. The
grid therefore holds Python ``complex`` values with the same JSON form as
``ComplexNumber`` (``{"real": ..., "imag": ...}``); a ``ComplexNumber``, a
number, such a dictionary or a ``[real, imag]`` pair is accepted on input.
"""

import cmath
import math
from typing import Annotated, Any, Dict, List, Literal, NamedTuple, Optional, Sequence, Tuple

import numpy as np
import polars as pl
from pydantic import (
    BaseModel,
    BeforeValidator,
    ConfigDict,
    PlainSerializer,
    ValidationInfo,
    field_validator,
    model_validator,
)

from groundinsight.diagnostics import ModelAssumptionWarning, emit
from groundinsight.utils.validations import (
    BUS_FORMULA_SYMBOLS,
    check_formula_symbols,
    validate_impedance_formula_value,
)

__all__ = [
    "CharacteristicProvenance",
    "CharacteristicValues",
    "EarthingCharacteristic",
]


def _to_complex(value: Any) -> complex:
    """
    Accept the spellings of a complex impedance a characteristic is given in.

    Parameters
    ----------
    value : Any
        A number, a ``ComplexNumber`` (anything with ``__complex__``), a
        ``{"real": ..., "imag": ...}`` dictionary or a ``[real, imag]`` pair.

    Returns
    -------
    complex
        The value.

    Raises
    ------
    TypeError
        If the value has none of these forms.
    ValueError
        If a part is not finite.
    """
    if isinstance(value, dict):
        z = complex(float(value["real"]), float(value["imag"]))
    elif isinstance(value, (list, tuple)) and len(value) == 2:
        z = complex(float(value[0]), float(value[1]))
    elif isinstance(value, (bool, str)):
        raise TypeError(f"not an impedance: {value!r}")
    elif hasattr(value, "__complex__") or isinstance(value, (int, float)):
        z = complex(value)
    else:
        raise TypeError(f"not an impedance: {value!r}")
    if not (math.isfinite(z.real) and math.isfinite(z.imag)):
        raise ValueError(f"impedance {z!r} is not finite")
    return z


#: A complex value that serialises like :class:`~groundinsight.ComplexNumber`.
_Complex = Annotated[
    complex,
    BeforeValidator(_to_complex),
    PlainSerializer(lambda z: {"real": z.real, "imag": z.imag}, return_type=dict),
]


class CharacteristicProvenance(BaseModel):
    """
    Where an earthing characteristic comes from.

    Every field is optional; a hand-made table may name only a ``note``. A
    characteristic computed by groundfield carries enough to repeat the
    computation and to tell two computations apart.

    Attributes
    ----------
    source : str, optional
        What produced the table, e.g. ``"groundfield"``, ``"measurement"``,
        ``"manual"``.
    groundfield_version : str, optional
        The groundfield release that computed it.
    solver : str, optional
        The solver or engine used.
    soil : dict, optional
        The soil specification (layers, resistivities, thicknesses) as the
        producer describes it.
    geometry_id : str, optional
        An identifier of the electrode geometry.
    rho_preset : str, optional
        The name of the resistivity scaling the table's ``rho`` axis was
        generated with.
    created : str, optional
        ISO 8601 date or timestamp of the computation.
    input_hash : str, optional
        A hash of the producer's input, to recognise a repeated computation.
    note : str, optional
        Free text.

    Examples
    --------
    >>> import groundinsight as gi
    >>> gi.CharacteristicProvenance(source="manual", note="from report 2026-07").source
    'manual'
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    source: Optional[str] = None
    groundfield_version: Optional[str] = None
    solver: Optional[str] = None
    soil: Optional[Dict[str, Any]] = None
    geometry_id: Optional[str] = None
    rho_preset: Optional[str] = None
    created: Optional[str] = None
    input_hash: Optional[str] = None
    note: Optional[str] = None


class CharacteristicValues(NamedTuple):
    """
    A characteristic evaluated at one specific earth resistance.

    Attributes
    ----------
    impedance : numpy.ndarray or None
        Complex impedance per frequency, in ohms; ``None`` when the
        characteristic has no impedance table.
    k_touch : numpy.ndarray or None
        Touch factor per frequency; ``None`` when not tabulated.
    k_transfer : numpy.ndarray or None
        Transfer factor per frequency; ``None`` when not tabulated.
    extrapolated : bool
        Whether ``rho`` or any frequency lies outside the grid.

    Examples
    --------
    >>> import groundinsight as gi
    >>> table = gi.EarthingCharacteristic(rho_Ohm_m=(100.0,), frequency_Hz=(50.0,),
    ...     impedance_Ohm=[[2.0]], k_touch=[[0.3]])
    >>> values = table.evaluate(100.0, [50.0])
    >>> values.k_touch.tolist(), values.k_transfer, values.extrapolated
    ([0.3], None, False)
    """

    impedance: Optional[np.ndarray]
    k_touch: Optional[np.ndarray]
    k_transfer: Optional[np.ndarray]
    extrapolated: bool


def _axis_weights(grid: np.ndarray, query: np.ndarray) -> Tuple[np.ndarray, np.ndarray, np.ndarray]:
    """
    Cell indices and weights of a (possibly extrapolating) linear interpolation.

    Parameters
    ----------
    grid : numpy.ndarray
        Strictly increasing grid coordinates.
    query : numpy.ndarray
        Query coordinates.

    Returns
    -------
    tuple of numpy.ndarray
        ``(i0, i1, t)``: the value is ``(1 - t) * v[i0] + t * v[i1]``;
        ``t`` lies outside ``[0, 1]`` outside the grid. A single-point grid
        gives ``i0 = i1 = 0``, ``t = 0``.
    """
    n = grid.size
    if n == 1:
        zeros = np.zeros(query.shape, dtype=int)
        return zeros, zeros, np.zeros(query.shape)
    i0 = np.clip(np.searchsorted(grid, query, side="right") - 1, 0, n - 2)
    i1 = i0 + 1
    t = (query - grid[i0]) / (grid[i1] - grid[i0])
    return i0, i1, t


def _bilinear(values: np.ndarray, ix: Tuple, iy: Tuple) -> np.ndarray:
    """
    Evaluate a bilinear interpolation of a grid.

    Parameters
    ----------
    values : numpy.ndarray
        Grid values, shape ``(n_rho, n_f)``.
    ix : tuple
        ``(i0, i1, t)`` along the first axis (one query).
    iy : tuple
        ``(j0, j1, u)`` along the second axis (one per frequency).

    Returns
    -------
    numpy.ndarray
        One value per frequency.
    """
    i0, i1, t = ix
    j0, j1, u = iy
    return (
        (1.0 - t) * (1.0 - u) * values[i0, j0]
        + t * (1.0 - u) * values[i1, j0]
        + (1.0 - t) * u * values[i0, j1]
        + t * u * values[i1, j1]
    )


class EarthingCharacteristic(BaseModel):
    r"""
    Tabulated earthing impedance and touch factors of an electrode over ``rho`` and ``f``.

    See the module docstring for how the table is interpolated, where it is
    attached and what happens outside the grid.

    Attributes
    ----------
    rho_Ohm_m : tuple of float
        Specific earth resistances of the grid, strictly increasing, > 0.
    frequency_Hz : tuple of float
        Frequencies of the grid, strictly increasing, > 0.
    impedance_Ohm : tuple of tuple of complex, optional
        ``impedance_Ohm[i][j]`` is the earthing impedance at
        ``rho_Ohm_m[i]`` and ``frequency_Hz[j]``; ``Re Z >= 0``, ``Z != 0``.
        Required when ``impedance_source`` is ``"table"``.
    k_touch : tuple of tuple of float, optional
        Touch factor $U_\mathrm{vT}/U_E$ on the same grid, ``>= 0``.
    k_transfer : tuple of tuple of float, optional
        Transfer factor $U_\mathrm{transfer}/U_E$ on the same grid, ``>= 0``.
    impedance_formula : str, optional
        The impedance as a formula in ``rho`` and ``f`` -- typically the
        k1..k5 fit of the same table. Used when ``impedance_source`` is
        ``"formula"``.
    impedance_source : {"table", "formula"}
        Where the bus impedance comes from. ``"formula"`` without an
        ``impedance_formula`` means the formula of the bus type.
    name : str, optional
        A name to show, e.g. ``"Ortsnetzstation Typ A"``.
    provenance : CharacteristicProvenance, optional
        Where the table comes from.

    Examples
    --------
    A hemisphere of radius 1 m, $Z = \rho / (2\pi r)$, tabulated at three
    resistivities and evaluated between them:

    >>> import math
    >>> import groundinsight as gi
    >>> rho = (50.0, 100.0, 400.0)
    >>> table = gi.EarthingCharacteristic(
    ...     rho_Ohm_m=rho, frequency_Hz=(50.0,),
    ...     impedance_Ohm=[[r / (2 * math.pi)] for r in rho],
    ...     k_touch=[[0.3], [0.3], [0.3]])
    >>> values = table.evaluate(200.0, [50.0])
    >>> round(float(values.impedance[0].real), 6), values.extrapolated
    (31.830989, False)
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    rho_Ohm_m: Tuple[float, ...]
    frequency_Hz: Tuple[float, ...]
    impedance_Ohm: Optional[Tuple[Tuple[_Complex, ...], ...]] = None
    k_touch: Optional[Tuple[Tuple[float, ...], ...]] = None
    k_transfer: Optional[Tuple[Tuple[float, ...], ...]] = None
    impedance_formula: Optional[str] = None
    impedance_source: Literal["table", "formula"] = "table"
    name: Optional[str] = None
    provenance: Optional[CharacteristicProvenance] = None

    @field_validator("rho_Ohm_m", "frequency_Hz")
    def _validate_axis(cls, value, info: ValidationInfo):
        """Require a non-empty, finite, strictly increasing, positive axis."""
        if len(value) == 0:
            raise ValueError(f"{info.field_name} must hold at least one value.")
        if not all(math.isfinite(v) and v > 0.0 for v in value):
            raise ValueError(f"{info.field_name} must be finite and > 0, got {value!r}.")
        if any(b <= a for a, b in zip(value[:-1], value[1:], strict=True)):
            raise ValueError(f"{info.field_name} must be strictly increasing, got {value!r}.")
        return tuple(float(v) for v in value)

    @field_validator("k_touch", "k_transfer")
    def _validate_factor(cls, value, info: ValidationInfo):
        """Require finite, non-negative factors."""
        if value is None:
            return value
        for row in value:
            for v in row:
                if not (math.isfinite(v) and v >= 0.0):
                    raise ValueError(f"{info.field_name} must be finite and >= 0, got {v!r}.")
        return value

    @field_validator("impedance_Ohm")
    def _validate_impedance(cls, value):
        """Require passive, non-zero impedances (``Re Z >= 0``, ``Z != 0``)."""
        if value is None:
            return value
        for row in value:
            for z in row:
                if z.real < 0.0 or z == 0:
                    raise ValueError(
                        f"impedance_Ohm must be passive and non-zero (Re Z >= 0, Z != 0), got {z!r}."
                    )
        return value

    @field_validator("impedance_formula")
    def _validate_formula(cls, value):
        """Run the formula guard and the symbol check of a bus formula."""
        if value is None:
            return value
        value = validate_impedance_formula_value(value)
        return check_formula_symbols(
            value, BUS_FORMULA_SYMBOLS, context="EarthingCharacteristic: impedance_formula"
        )

    @model_validator(mode="after")
    def _validate_shape(self):
        """Every table matches the grid; a table source has a table."""
        shape = (len(self.rho_Ohm_m), len(self.frequency_Hz))
        for field in ("impedance_Ohm", "k_touch", "k_transfer"):
            table = getattr(self, field)
            if table is None:
                continue
            if len(table) != shape[0] or any(len(row) != shape[1] for row in table):
                raise ValueError(
                    f"{field} must have {shape[0]} rows (one per rho_Ohm_m) of "
                    f"{shape[1]} values (one per frequency_Hz)."
                )
        if self.impedance_source == "table" and self.impedance_Ohm is None:
            raise ValueError(
                "impedance_source='table' needs impedance_Ohm; set "
                "impedance_source='formula' to take the impedance from a formula."
            )
        return self

    # -- evaluation -------------------------------------------------------

    def is_extrapolated(self, rho: float, frequencies: Sequence[float]) -> bool:
        """
        Whether ``rho`` or a frequency lies outside the grid.

        Parameters
        ----------
        rho : float
            Specific earth resistance in ohm metres.
        frequencies : sequence of float
            Frequencies in hertz.

        Returns
        -------
        bool
            True if any value lies outside ``[min, max]`` of its axis.

        Examples
        --------
        >>> import groundinsight as gi
        >>> table = gi.EarthingCharacteristic(rho_Ohm_m=(100.0, 200.0),
        ...     frequency_Hz=(50.0,), impedance_Ohm=[[1.0], [2.0]])
        >>> table.is_extrapolated(150.0, [50.0]), table.is_extrapolated(300.0, [50.0])
        (False, True)
        """
        r0, r1 = self.rho_Ohm_m[0], self.rho_Ohm_m[-1]
        f0, f1 = self.frequency_Hz[0], self.frequency_Hz[-1]
        return not (r0 <= rho <= r1) or any(not (f0 <= f <= f1) for f in frequencies)

    def evaluate(self, rho: float, frequencies: Sequence[float]) -> CharacteristicValues:
        """
        Interpolate the tables at one specific earth resistance.

        Parameters
        ----------
        rho : float
            Specific earth resistance in ohm metres, > 0.
        frequencies : sequence of float
            Frequencies in hertz; values ``<= 0`` take the lowest grid
            frequency.

        Returns
        -------
        CharacteristicValues
            The interpolated tables (``None`` where the characteristic has
            none) and whether any value was extrapolated.

        Raises
        ------
        ValueError
            If ``rho`` is not finite and positive.

        Examples
        --------
        >>> import groundinsight as gi
        >>> table = gi.EarthingCharacteristic(rho_Ohm_m=(100.0, 400.0),
        ...     frequency_Hz=(50.0,), impedance_Ohm=[[1.0], [4.0]])
        >>> table.evaluate(200.0, [50.0]).impedance  # |Z| ~ rho: exact
        array([2.+0.j])
        """
        if not (math.isfinite(rho) and rho > 0.0):
            raise ValueError(f"rho must be finite and > 0, got {rho!r}.")
        freqs = np.asarray(list(frequencies), dtype=float)
        rho_grid = np.asarray(self.rho_Ohm_m)
        f_grid = np.asarray(self.frequency_Hz)
        # No logarithm at or below 0 Hz: such a frequency takes the lowest
        # grid frequency (and counts as extrapolated).
        f_query = np.where(freqs > 0.0, freqs, f_grid[0])
        ix = _axis_weights(np.log(rho_grid), np.log(np.asarray([rho])))
        ix = (ix[0][0], ix[1][0], ix[2][0])
        iy = _axis_weights(np.log(f_grid), np.log(f_query))
        # Grid points hit exactly return the stored value (see module docstring).
        rho_hit = np.flatnonzero(rho_grid == rho)
        f_index = np.minimum(np.searchsorted(f_grid, f_query), f_grid.size - 1)
        f_hit = np.where(f_grid[f_index] == f_query, f_index, -1)

        def exact(table: np.ndarray, interpolated: np.ndarray) -> np.ndarray:
            """Replace the values at grid points by the stored ones.

            Parameters
            ----------
            table : numpy.ndarray
                The grid, shape ``(n_rho, n_f)``.
            interpolated : numpy.ndarray
                The interpolated values, one per frequency; changed in place.

            Returns
            -------
            numpy.ndarray
                ``interpolated``.
            """
            if rho_hit.size == 0:
                return interpolated
            on_node = f_hit >= 0
            interpolated[on_node] = table[rho_hit[0], f_hit[on_node]]
            return interpolated

        impedance = None
        if self.impedance_Ohm is not None:
            z = np.asarray(self.impedance_Ohm, dtype=complex)
            magnitude = np.exp(_bilinear(np.log(np.abs(z)), ix, iy))
            angle = _bilinear(np.angle(z), ix, iy)
            impedance = exact(z, magnitude * np.exp(1j * angle))
        factors = []
        for table in (self.k_touch, self.k_transfer):
            if table is None:
                factors.append(None)
                continue
            k = np.asarray(table, dtype=float)
            factors.append(exact(k, np.maximum(_bilinear(k, ix, iy), 0.0)))
        return CharacteristicValues(
            impedance=impedance,
            k_touch=factors[0],
            k_transfer=factors[1],
            extrapolated=self.is_extrapolated(rho, frequencies),
        )

    def to_frame(self) -> pl.DataFrame:
        """
        The grid as a long table, one row per ``(rho, f)``.

        Returns
        -------
        polars.DataFrame
            Columns ``rho_Ohm_m``, ``frequency_Hz``, ``Z_Ohm``, ``Z_degree``,
            ``R_Ohm``, ``X_Ohm``, ``k_touch``, ``k_transfer`` (null where the
            characteristic has no table).

        Examples
        --------
        >>> import groundinsight as gi
        >>> table = gi.EarthingCharacteristic(rho_Ohm_m=(100.0,),
        ...     frequency_Hz=(50.0, 1000.0), impedance_Ohm=[[1.0, 1 + 1j]])
        >>> table.to_frame().select("frequency_Hz", "Z_degree").rows()
        [(50.0, 0.0), (1000.0, 45.0)]
        """
        rows: List[Dict[str, Optional[float]]] = []
        for i, rho in enumerate(self.rho_Ohm_m):
            for j, f in enumerate(self.frequency_Hz):
                z = self.impedance_Ohm[i][j] if self.impedance_Ohm is not None else None
                rows.append({
                    "rho_Ohm_m": rho,
                    "frequency_Hz": f,
                    "Z_Ohm": None if z is None else abs(z),
                    "Z_degree": None if z is None else math.degrees(cmath.phase(z)),
                    "R_Ohm": None if z is None else z.real,
                    "X_Ohm": None if z is None else z.imag,
                    "k_touch": None if self.k_touch is None else self.k_touch[i][j],
                    "k_transfer": None if self.k_transfer is None else self.k_transfer[i][j],
                })
        return pl.DataFrame(rows, schema={
            "rho_Ohm_m": pl.Float64, "frequency_Hz": pl.Float64, "Z_Ohm": pl.Float64,
            "Z_degree": pl.Float64, "R_Ohm": pl.Float64, "X_Ohm": pl.Float64,
            "k_touch": pl.Float64, "k_transfer": pl.Float64,
        })


def extrapolation_findings(
    buses: Dict[str, Any], frequencies: Sequence[float]
) -> List[Tuple[str, List[Tuple[str, str]]]]:
    """
    The buses whose earthing characteristic is evaluated outside its grid.

    Internal API: shared by :func:`report_extrapolation` and
    :func:`groundinsight.check_network`; not exported from ``groundinsight``
    and free to change in any release.

    Parameters
    ----------
    buses : dict of str to Bus
        The buses of a network; inactive ones are skipped.
    frequencies : sequence of float
        The network frequencies.

    Returns
    -------
    list of tuple
        ``(message, elements)`` per bus concerned, in name order.
    """
    findings = []
    for name in sorted(buses):
        bus = buses[name]
        if not getattr(bus, "active", True):
            continue
        characteristic = bus.resolved_characteristic()
        if characteristic is None:
            continue
        rho = bus.specific_earth_resistance
        if not characteristic.is_extrapolated(rho, frequencies):
            continue
        r0, r1 = characteristic.rho_Ohm_m[0], characteristic.rho_Ohm_m[-1]
        f0, f1 = characteristic.frequency_Hz[0], characteristic.frequency_Hz[-1]
        outside = [f"{f:g} Hz" for f in frequencies if not f0 <= f <= f1]
        parts = []
        if not r0 <= rho <= r1:
            parts.append(f"rho = {rho:g} Ohm*m (table {r0:g}-{r1:g} Ohm*m)")
        if outside:
            parts.append(f"{', '.join(outside)} (table {f0:g}-{f1:g} Hz)")
        label = f" '{characteristic.name}'" if characteristic.name else ""
        findings.append((
            f"Bus '{name}' is evaluated outside its earthing characteristic"
            f"{label}: {'; '.join(parts)}. Impedance and touch factors are "
            "continued from the edge of the table (a power law in rho and f); "
            "extend the table to cover the network.",
            [("bus", name)],
        ))
    return findings


def report_extrapolation(buses: Dict[str, Any], frequencies: Sequence[float]) -> None:
    """
    Emit ``CHARACTERISTIC_EXTRAPOLATED`` for each bus evaluated outside its grid.

    Internal API: called by :func:`groundinsight.run_fault`, so the
    diagnostic is part of ``Result.diagnostics``; not exported from
    ``groundinsight`` and free to change in any release.

    Parameters
    ----------
    buses : dict of str to Bus
        The buses of a network; inactive ones are skipped.
    frequencies : sequence of float
        The network frequencies.
    """
    for message, elements in extrapolation_findings(buses, frequencies):
        emit(ModelAssumptionWarning, message, code="CHARACTERISTIC_EXTRAPOLATED",
             elements=elements)

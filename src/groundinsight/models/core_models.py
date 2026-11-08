# models/core_models.py
"""
Pydantic models of a grounding network and the :class:`Network` container.

Types, elements, faults, sources and results are models of their own.

Types (:class:`BusType`, :class:`BranchType`) carry formula strings; elements
(:class:`Bus`, :class:`Branch`) carry the impedance dictionaries *derived*
from those formulas at the network's frequencies. Derived data is only worth
having while it matches its inputs, so the models keep three invariants:

* **Derived data follows its inputs.** Assigning an input of an element that
  belongs to a network (``specific_earth_resistance``, ``length``, ``type``)
  re-derives the element through the same code path as ``Network.add_*``;
  assigning ``Network.frequencies`` re-derives every element. A failing
  derivation leaves everything as it was. Types are read-only, because a
  type is evaluated into every element that uses it -- a changed type is a
  new type, assigned per element.
* **Stored results know whether they are current.** Every result carries a
  digest of the inputs it was computed from (:attr:`Result.input_digest`);
  :meth:`Network.stale_results` compares it with the inputs the network has
  now. The comparison is by content, so undoing an edit -- or a study that
  restores the network -- makes a result current again, and the digest
  survives JSON and the database.
* **Names are keys.** An element belongs to one network and is found by its
  name; renaming an attached element, rewiring it to a bus the network does
  not have, or removing a bus that is still referenced is refused.
  :meth:`Network.update_bus` / :meth:`Network.update_branch` and the
  ``remove_*`` methods change a network consistently.

Elements know their network through a weak back-reference that compares
equal to every other (:class:`_NetworkLink`), so it changes neither equality
nor serialisation, and copies or pickles of a network point at the copy.
"""

import hashlib
import logging
import math
import numbers
import sys
import warnings
import weakref
from array import array

import numpy as np
from pydantic import (
    BaseModel,
    ConfigDict,
    PrivateAttr,
    ValidationInfo,
    computed_field,
    field_serializer,
    field_validator,
    model_validator,
)
from typing import (
    TYPE_CHECKING, Any, ClassVar, Dict, FrozenSet, List, Literal, Optional, Tuple, Union,
)
from groundinsight.diagnostics import (
    Diagnostic,
    GroundinsightWarning,
    InputWarning,
    emit,
)
from groundinsight.utils.compat import renamed_parameters
from groundinsight.utils.validations import (
    BRANCH_FORMULA_SYMBOLS,
    BUS_FORMULA_SYMBOLS,
    check_formula_symbols,
    validate_impedance_formula_value,
)
from groundinsight.utils.impedance_calculator import (
    check_passive_impedance_array,
    compute_impedance,
    compute_impedance_array,
    compute_real_value,
    impedance_table,
    render_frequencies,
)
import polars as pl

if TYPE_CHECKING:  # both modules import this one
    from groundinsight.electrical_network import ElectricalNetwork
    from groundinsight.pathfinder import PathfinderCache, PathfinderSettings


logger = logging.getLogger(__name__)


class NetworkFrequencyOrderWarning(GroundinsightWarning):
    """Warning category for non-strictly-monotone ``Network.frequencies``.

    Emitted by :class:`Network` when ``frequencies`` is supplied in a
    non-strictly-increasing order. The FFT transient solver
    (:mod:`groundinsight.simulation.transient`) uses the order of
    ``Network.frequencies`` to map spectral bins to the impedance
    dictionary; a descending or shuffled list produces a transient with
    the spectral bins reversed and is almost always a user error.

    Mirrors :class:`groundfield.solver.engine.EngineFrequencyOrderWarning`
    so users get consistent diagnostics across the three packages of the
    earthing-platform stack.

    Notes
    -----
    Suppress with ``warnings.simplefilter('ignore',
    NetworkFrequencyOrderWarning)``; surface only once per Python
    process by combining it with ``simplefilter('once', ...)``.

    Examples
    --------
    >>> import warnings
    >>> import groundinsight as gi
    >>> with warnings.catch_warnings(record=True) as caught:
    ...     warnings.simplefilter("always")
    ...     net = gi.create_network(name="n", frequencies=[250.0, 50.0])
    >>> [w.category.__name__ for w in caught]
    ['NetworkFrequencyOrderWarning']
    """

    default_code = "FREQUENCY_ORDER"



class TransientModelWarning(GroundinsightWarning):
    """Warning category for a transient run whose model is narrower than its input.

    Emitted by :mod:`groundinsight.simulation.transient` when the network
    carries something a solver path cannot represent, and the result is
    therefore quantitatively different from what the same network yields
    elsewhere. It is deliberately *not* an error: the remaining physics is
    still solved correctly and the answer is often the one the user wants,
    but the difference has to be visible rather than inferred from a
    documentation chapter.

    Current cases:

    * the FFT solver meeting a branch with mutual coupling, which it does
      not evaluate (the stationary solver and the state-space solver do),
    * either transient solver meeting an active fault with per-frequency
      ``scalings`` other than 1, which is a frequency-domain concept the
      time-domain source waveform replaces,
    * a mutual inductance larger than the branch's own ``L_self``, which is
      not physically realisable for two coupled conductors and is almost
      always a mH/H unit slip,
    * the state-space solver meeting a voltage source that reaches the fault,
      whose coupling it does not model (code ``TRANSIENT_COUPLING_SKIPPED``),
      or a purely resistive branch carrying mutual data
      (``TRANSIENT_RESISTIVE_COUPLING_IGNORED``).

    Notes
    -----
    Suppress with ``warnings.simplefilter('ignore', TransientModelWarning)``;
    turn into an error with ``simplefilter('error', TransientModelWarning)``.

    Examples
    --------
    >>> import groundinsight as gi
    >>> issubclass(gi.TransientModelWarning, gi.GroundinsightWarning)
    True
    """

    default_code = "TRANSIENT_MODEL"



def json_safe_float(value: float) -> Union[float, str]:
    """
    Render a float so that ``json.dumps`` produces RFC 8259 JSON.

    Python writes ``Infinity`` / ``-Infinity`` / ``NaN`` for the non-finite
    floats, which no other JSON reader accepts: SQLite's ``json_valid()``
    returns 0 and JavaScript's ``JSON.parse`` raises. ``inf`` is not an
    accident in this package -- it is the open-end sentinel produced by the
    ``"nan"`` impedance formula -- so it cannot simply be dropped to ``null``
    either. The compromise is a string, which both survives the round-trip
    (``float("inf")`` reads it back) and keeps the document valid.

    Parameters
    ----------
    value : float
        The value to render.

    Returns
    -------
    Union[float, str]
        ``value`` itself when finite, otherwise ``"inf"``, ``"-inf"`` or
        ``"nan"``.
    """
    if math.isnan(value):
        return "nan"
    if math.isinf(value):
        return "inf" if value > 0 else "-inf"
    return value


#: Column layout of :meth:`Network.res_buses`, in output order.
#:
#: Pinned so an empty result is an empty frame *with these columns* instead of
#: a 0x0 frame: every consumer downstream selects or filters on the names, and
#: a 0x0 frame turns "no rows" into a missing-column error several calls away
#: from the cause. The importers in :mod:`groundinsight.io` already pin their
#: schemas for the same reason.
RES_BUSES_SCHEMA: Dict[str, Any] = {
    "bus_name": pl.Utf8,
    "fault": pl.Utf8,
    "frequency_Hz": pl.Float64,
    "is_rms": pl.Boolean,
    "EPR_V": pl.Float64,
    "EPR_degree": pl.Float64,
    "I_bus_A": pl.Float64,
    "I_bus_degree": pl.Float64,
    "I_inj_A": pl.Float64,
    "I_inj_degree": pl.Float64,
}

#: Column layout of :meth:`Network.res_branches`, in output order.
RES_BRANCHES_SCHEMA: Dict[str, Any] = {
    "branch_name": pl.Utf8,
    "fault": pl.Utf8,
    "frequency_Hz": pl.Float64,
    "is_rms": pl.Boolean,
    "I_branch_A": pl.Float64,
    "I_branch_degree": pl.Float64,
}

#: Column layout of :meth:`Network.res_all_impedances`, in output order.
RES_ALL_IMPEDANCES_SCHEMA: Dict[str, Any] = {
    "fault_name": pl.Utf8,
    "fault_bus": pl.Utf8,
    "frequency_Hz": pl.Float64,
    "grounding_impedance_Ohm": pl.Float64,
    "grounding_impedance_deg": pl.Float64,
    "reduction_factor": pl.Float64,
    "reduction_factor_current": pl.Float64,
}


RES_FAULT_SCHEMA: Dict[str, Any] = {
    "fault": pl.Utf8,
    "fault_bus": pl.Utf8,
    "frequency_Hz": pl.Float64,
    "is_rms": pl.Boolean,
    "I_F_A": pl.Float64,
    "I_F_degree": pl.Float64,
    "EPR_fault_V": pl.Float64,
    "EPR_fault_degree": pl.Float64,
    "EPR_max_V": pl.Float64,
    "EPR_max_bus": pl.Utf8,
    "r_epr": pl.Float64,
    "r_current": pl.Float64,
    "I_E_A": pl.Float64,
    "U_E_V": pl.Float64,
    "Z_E_Ohm": pl.Float64,
    "Z_E_degree": pl.Float64,
    "n_diagnostics": pl.Int64,
}
"""Column layout of :meth:`Network.res_fault`, in output order.

The key figures of one fault at the fault location, one row per network
frequency plus one RMS row (``is_rms`` true, ``frequency_Hz`` null). Pinned
for the same reason as the other result tables, and because a GUI or a report
template binds to the names: ``I_F`` the fault current, ``EPR_fault`` the
earth potential rise at the fault bus, ``EPR_max`` the largest one in the
network and its bus, ``r_epr`` and ``r_current`` the EPR-based and the
current-based reduction factor, ``I_E`` the earth-return current, ``U_E`` and
``Z_E`` the earthing voltage and impedance of EN 50522, ``n_diagnostics`` the
number of records in ``Result.diagnostics``.
"""

def _magnitude_angle(
    value: Optional["ComplexNumber"], magnitude_key: str, angle_key: str
) -> Dict[str, Optional[float]]:
    """
    Magnitude and angle (degrees) of a phasor, as two frame columns.

    Parameters
    ----------
    value : Optional[ComplexNumber]
        The phasor (a ``ComplexNumber`` or a plain ``complex``), or ``None``
        when the result carries no entry for this frequency.
    magnitude_key, angle_key : str
        Column names to produce.

    Returns
    -------
    Dict[str, Optional[float]]
        ``{magnitude_key: |value|, angle_key: arg(value) in degrees}``, both
        ``None`` when ``value`` is ``None``.

    Notes
    -----
    A missing entry yields ``None`` rather than an ``AttributeError``. A
    ``ResultBus`` whose ``ia_freq`` does not cover every frequency of
    ``uepr_freq`` -- a stored result of an earlier format, a hand-built one --
    would otherwise make :meth:`Network.res_buses` crash on ``None.real``
    while reporting nothing about which bus or frequency was at fault.
    """
    if value is None:
        return {magnitude_key: None, angle_key: None}
    as_complex = complex(value.real, value.imag)
    return {
        magnitude_key: abs(as_complex),
        angle_key: float(np.angle(as_complex) * 180 / np.pi),
    }


class _PhasorColumn:
    """
    The phasors of one result quantity, collected row by row for a frame.

    Building one dict per row for :meth:`Network.res_buses` and
    :meth:`Network.res_branches` and taking magnitude and angle of every
    phasor through :func:`_magnitude_angle`, one NumPy scalar at a time,
    takes half as long as the solve that produced the result on a 4000-bus
    ring at seven frequencies. This collects real and imaginary parts once
    and computes both columns on whole arrays, with the same arithmetic:
    ``numpy.hypot`` agrees bit for bit with Python's ``abs`` of a complex
    number (``numpy.abs`` of a complex array does not), and
    ``numpy.angle(z) * 180 / numpy.pi`` is the expression the scalar path
    evaluates.

    A row is one of three kinds: a phasor, a missing phasor (``None``, both
    columns ``null``), or an RMS row (magnitude given as a number or
    ``None``, angle ``null``).
    """

    def __init__(self) -> None:
        self.real: List[float] = []
        self.imag: List[float] = []
        self.missing: List[int] = []
        self.rms_rows: List[int] = []
        self.rms_values: List[Optional[float]] = []

    def phasor(self, value: Any) -> None:
        """
        Append a phasor row.

        Parameters
        ----------
        value : ComplexNumber, complex or None
            The phasor; ``None`` for a missing entry.
        """
        if value is None:
            self.missing.append(len(self.real))
            self.real.append(0.0)
            self.imag.append(0.0)
        else:
            self.real.append(value.real)
            self.imag.append(value.imag)

    def rms(self, value: Optional[float]) -> None:
        """
        Append an RMS row.

        Parameters
        ----------
        value : float or None
            The RMS magnitude; the angle of an RMS row is always ``null``.
        """
        self.rms_rows.append(len(self.real))
        self.rms_values.append(value)
        self.real.append(0.0)
        self.imag.append(0.0)

    def series(self, magnitude_key: str, angle_key: str) -> Tuple[pl.Series, pl.Series]:
        """
        The magnitude and angle columns.

        Parameters
        ----------
        magnitude_key, angle_key : str
            Column names.

        Returns
        -------
        tuple of polars.Series
            ``Float64`` magnitude and angle in degrees, ``null`` where the
            row kind says so.
        """
        real = np.asarray(self.real, dtype=float)
        imag = np.asarray(self.imag, dtype=float)
        phasors = np.empty(real.shape, dtype=complex)
        phasors.real = real
        phasors.imag = imag
        magnitude = np.hypot(real, imag)
        angle = np.angle(phasors) * 180 / np.pi
        rms_rows = np.asarray(self.rms_rows, dtype=np.int64)
        rms_null: List[int] = []
        if rms_rows.size:
            values = [np.nan if v is None else v for v in self.rms_values]
            magnitude[rms_rows] = np.asarray(values, dtype=float)
            rms_null = [
                row for row, v in zip(self.rms_rows, self.rms_values, strict=True) if v is None
            ]
        magnitude_series = pl.Series(magnitude_key, magnitude, dtype=pl.Float64)
        angle_series = pl.Series(angle_key, angle, dtype=pl.Float64)
        null_magnitude = self.missing + rms_null
        if null_magnitude:
            magnitude_series.scatter(np.asarray(null_magnitude, dtype=np.int64), None)
        null_angle = self.missing + self.rms_rows
        if null_angle:
            angle_series.scatter(np.asarray(null_angle, dtype=np.int64), None)
        return magnitude_series, angle_series


def _frequency_series(frequencies: List[float], rms_rows: List[int]) -> pl.Series:
    """
    The ``frequency_Hz`` column of a result frame: ``null`` on RMS rows.

    Parameters
    ----------
    frequencies : list of float
        One frequency per row (any placeholder on RMS rows).
    rms_rows : list of int
        The RMS rows.

    Returns
    -------
    polars.Series
        ``Float64`` column.
    """
    series = pl.Series("frequency_Hz", np.asarray(frequencies, dtype=float),
                       dtype=pl.Float64)
    if rms_rows:
        series.scatter(np.asarray(rms_rows, dtype=np.int64), None)
    return series


def _phasor(mapping: Optional[Dict[float, Any]], frequency: float) -> Optional[complex]:
    """
    One entry of a per-frequency result mapping, as a complex number.

    Parameters
    ----------
    mapping : dict of float to ComplexNumber or complex, optional
        A result field such as ``ResultReductionFactor.i_fault``; ``None`` for
        a result that has no such field.
    frequency : float
        The frequency in Hz.

    Returns
    -------
    complex or None
        The value, or ``None`` where the mapping has no entry or the entry is
        ``None`` (undefined at this frequency).
    """
    if not mapping:
        return None
    value = mapping.get(float(frequency))
    if value is None:
        return None
    if isinstance(value, complex):
        return value
    return complex(value.real, value.imag)


def _root_sum_of_squares(values: List[Optional[complex]]) -> Optional[float]:
    """
    RMS magnitude over frequencies, ``sqrt(sum |x(f)|**2)``.

    Parameters
    ----------
    values : list of complex or None
        One phasor per frequency; ``None`` where the quantity is undefined.

    Returns
    -------
    float or None
        The root sum of squares of the defined values, ``None`` when no
        frequency has one. An undefined entry adds nothing: the per-frequency
        quantities summed here (fault current, earth current, earthing
        voltage) are undefined only where they are zero or vanish in rounding
        noise.
    """
    defined = [abs(value) ** 2 for value in values if value is not None]
    if not defined:
        return None
    return math.sqrt(sum(defined))


def _fault_key_figures(network: "Network", fault: str, result: "Result") -> pl.DataFrame:
    """
    The rows of :meth:`Network.res_fault` for a stored result.

    Parameters
    ----------
    network : Network
        The network; its frequencies give the rows.
    fault : str
        Name of the fault.
    result : Result
        The stored result of ``fault``.

    Returns
    -------
    polars.DataFrame
        The table with the schema :data:`RES_FAULT_SCHEMA`.
    """
    factor = result.reduction_factor
    fault_bus = factor.fault_bus if factor is not None else network.faults[fault].bus
    buses = list(result.buses)
    fault_result = next((bus for bus in buses if bus.name == fault_bus), None)
    n_diagnostics = len(result.diagnostics)

    def from_factor(field: str, frequency: float) -> Optional[complex]:
        return _phasor(getattr(factor, field), frequency) if factor is not None else None

    def ratio(field: str, frequency: float) -> Optional[float]:
        if factor is None:
            return None
        value = getattr(factor, field).get(float(frequency))
        return None if value is None else float(value)

    rows: List[Dict[str, Any]] = []
    fault_currents: List[Optional[complex]] = []
    earth_currents: List[Optional[complex]] = []
    earthing_voltages: List[Optional[complex]] = []
    for frequency in network.frequencies:
        frequency = float(frequency)
        i_fault = from_factor("i_fault", frequency)
        i_earth = from_factor("i_earth", frequency)
        u_earthing = from_factor("u_earthing", frequency)
        u_fault = (
            _phasor(fault_result.uepr_freq, frequency) if fault_result is not None else None
        )
        # The largest EPR and its bus; the first bus in result order on a tie.
        top_bus, top = None, None
        for bus in buses:
            potential = _phasor(bus.uepr_freq, frequency)
            if potential is not None and (top is None or abs(potential) > top):
                top_bus, top = bus.name, abs(potential)
        fault_currents.append(i_fault)
        earth_currents.append(i_earth)
        earthing_voltages.append(u_earthing)
        rows.append(
            {
                "fault": fault,
                "fault_bus": fault_bus,
                "frequency_Hz": frequency,
                "is_rms": False,
                **_magnitude_angle(i_fault, "I_F_A", "I_F_degree"),
                **_magnitude_angle(u_fault, "EPR_fault_V", "EPR_fault_degree"),
                "EPR_max_V": top,
                "EPR_max_bus": top_bus,
                "r_epr": ratio("value", frequency),
                "r_current": ratio("value_current", frequency),
                "I_E_A": None if i_earth is None else abs(i_earth),
                "U_E_V": None if u_earthing is None else abs(u_earthing),
                **_magnitude_angle(from_factor("z_earthing", frequency), "Z_E_Ohm",
                                   "Z_E_degree"),
                "n_diagnostics": n_diagnostics,
            }
        )
    # The RMS row: the stored RMS EPR per bus, root sums of squares otherwise;
    # ratios, impedances and angles have no RMS value.
    top_rms = max(buses, key=lambda bus: bus.uepr, default=None)
    rows.append(
        {
            "fault": fault,
            "fault_bus": fault_bus,
            "frequency_Hz": None,
            "is_rms": True,
            "I_F_A": _root_sum_of_squares(fault_currents),
            "I_F_degree": None,
            "EPR_fault_V": fault_result.uepr if fault_result is not None else None,
            "EPR_fault_degree": None,
            "EPR_max_V": top_rms.uepr if top_rms is not None else None,
            "EPR_max_bus": top_rms.name if top_rms is not None else None,
            "r_epr": None,
            "r_current": None,
            "I_E_A": _root_sum_of_squares(earth_currents),
            "U_E_V": _root_sum_of_squares(earthing_voltages),
            "Z_E_Ohm": None,
            "Z_E_degree": None,
            "n_diagnostics": n_diagnostics,
        }
    )
    return pl.DataFrame(rows, schema=RES_FAULT_SCHEMA)

class _NetworkLink:
    """
    Weak back-reference from an element to the :class:`Network` holding it.

    An element has to know its network to re-derive itself when one of its
    inputs is assigned, and to refuse a rename or a rewiring the network
    cannot follow. A plain reference would not do: pydantic compares private
    attributes in ``__eq__`` (comparing two buses would compare their whole
    networks, and recurse back into the buses), and it would keep a network
    alive through any element still referenced elsewhere. So the link is
    weak, compares equal to every other link, and is never serialised:

    * ``copy.deepcopy`` resolves it through the copy memo -- when the whole
      network is copied the link points at the copy, when only the element
      is copied it is detached;
    * ``pickle`` stores a detached link; :meth:`Network.__setstate__`
      re-attaches the elements of an unpickled network;
    * a shallow copy shares the link, but :meth:`_NetworkElement._owning_network`
      also checks that the network still holds *this* object under its name,
      so a copy, a replaced element or a removed one counts as detached.
    """

    __slots__ = ("_ref",)

    def __init__(self, network: Optional["Network"] = None) -> None:
        self._ref = weakref.ref(network) if network is not None else None

    def get(self) -> Optional["Network"]:
        """
        The linked network, or ``None`` when detached or collected.

        Returns
        -------
        Optional[Network]
            The network, if it is still alive.
        """
        return self._ref() if self._ref is not None else None

    def __eq__(self, other: object) -> bool:
        return isinstance(other, _NetworkLink)

    def __hash__(self) -> int:
        return hash(_NetworkLink)

    def __copy__(self) -> "_NetworkLink":
        return self

    def __deepcopy__(self, memo: Dict[int, Any]) -> "_NetworkLink":
        network = self.get()
        copied = memo.get(id(network)) if network is not None else None
        return _NetworkLink(copied if isinstance(copied, BaseModel) else None)

    def __reduce__(self):
        return (_NetworkLink, ())


class ValidatedAssignmentModel(BaseModel):
    """
    Base class whose rejected assignments leave the object unchanged.

    ``model_config = ConfigDict(validate_assignment=True)`` makes Pydantic
    re-validate a field on ``setattr``, which is what the models below want:
    a value that is refused at construction has to be refused later, too.
    Pydantic applies the new value to ``__dict__`` *before* it runs the
    model-level ``mode="after"`` validators, though, and does not undo it when
    one of them raises. A caller that catches the ``ValidationError`` -- which
    is the normal thing to do around a bulk import -- would then be holding an
    object in exactly the state the validator just rejected, and the point of
    validating on assignment is that such an object never exists.

    Field-level validators are unaffected (they run before the write); this
    only matters for the cross-field checks such as
    :meth:`Source._validate_source_mode`. The previous value, and the
    ``__pydantic_fields_set__`` bookkeeping that goes with it, are restored
    before the exception is re-raised.

    Subclasses hook into an assignment through
    :meth:`_before_field_assignment` (may refuse it before anything is
    written) and :meth:`_after_field_assignment` (runs after the validated
    write; if it raises, the field is rolled back the same way).
    """

    def __setattr__(self, name: str, value: Any) -> None:
        fields = type(self).model_fields
        if name not in fields:
            super().__setattr__(name, value)
            return
        self._before_field_assignment(name, value)
        had_value = name in self.__dict__
        previous = self.__dict__.get(name)
        was_set = name in self.__pydantic_fields_set__
        try:
            super().__setattr__(name, value)
            self._after_field_assignment(name)
        except Exception:
            if had_value:
                self.__dict__[name] = previous
            else:
                self.__dict__.pop(name, None)
            if was_set:
                self.__pydantic_fields_set__.add(name)
            else:
                self.__pydantic_fields_set__.discard(name)
            raise

    def _before_field_assignment(self, name: str, value: Any) -> None:
        """
        Refuse an assignment before anything is written. No-op by default.

        Parameters
        ----------
        name : str
            The field being assigned.
        value : Any
            The value as passed, not yet validated.
        """

    def _after_field_assignment(self, name: str) -> None:
        """
        React to a validated assignment; raising rolls the field back.

        No-op by default.

        Parameters
        ----------
        name : str
            The field that was assigned.
        """


class _NetworkElement(ValidatedAssignmentModel):
    """
    Base of the elements a :class:`Network` keeps by name.

    Implements the edit invariants of an element that belongs to a network
    (see the module docstring): a rename is refused, a bus reference must
    name a bus of the network, and an input of the derived impedances
    re-derives them -- atomically, through :meth:`calculate_impedance`, the
    code path of ``Network.add_*``. On an element that belongs to no network
    none of this happens.
    """

    #: Name of the :class:`Network` field that holds this kind of element.
    _COLLECTION: ClassVar[str] = ""
    #: How this kind of element is named in messages.
    _KIND: ClassVar[str] = "element"
    #: Fields the derived impedance dictionaries are computed from.
    _DERIVATION_INPUTS: ClassVar[FrozenSet[str]] = frozenset()
    #: Fields that name a bus of the network.
    _BUS_REFERENCES: ClassVar[Tuple[str, ...]] = ()

    _network_link: _NetworkLink = PrivateAttr(default_factory=_NetworkLink)
    #: Derived fields that hold a ``{frequency: ComplexNumber}`` table.
    _DERIVED_TABLES: ClassVar[FrozenSet[str]] = frozenset()

    def _store_derived(self, derived: Dict[str, Any]) -> None:
        """
        Write values computed by ``_derive``.

        A derived impedance table is stored as the validated assignment would
        store it -- a dictionary of its own holding the very
        :class:`ComplexNumber` instances -- without validating every value a
        second time: each one was validated when the calculator packed it
        (P10). The field validators of the tables only convert the keys to
        ``float`` and pass instances through, and the assignment hooks do
        nothing for a derived field, so the stored state is the same. Every
        other value, and a table that becomes ``None``, goes through the
        validated assignment.

        Parameters
        ----------
        derived : Dict[str, Any]
            Field name to value, as returned by ``_derive``.
        """
        for field, value in derived.items():
            # ``None`` over ``None`` is the common case for the optional
            # fields; skipping it saves a validated assignment per field.
            if value is None and self.__dict__.get(field) is None:
                continue
            if value is not None and field in self._DERIVED_TABLES:
                self.__dict__[field] = dict(value)
                self.__pydantic_fields_set__.add(field)
                continue
            setattr(self, field, value)

    def _owning_network(self) -> Optional["Network"]:
        """
        The network this element belongs to, or ``None``.

        An element belongs to a network when it is linked to it *and* the
        network holds this very object under the element's name -- so a
        copy, a replaced or a removed element counts as detached.

        Returns
        -------
        Optional[Network]
            The owning network.
        """
        network = self._network_link.get()
        if network is None:
            return None
        if getattr(network, self._COLLECTION).get(self.name) is not self:
            return None
        return network

    def _before_field_assignment(self, name: str, value: Any) -> None:
        """
        Refuse renaming an element that belongs to a network.

        Parameters
        ----------
        name : str
            The field being assigned.
        value : Any
            The new value.

        Raises
        ------
        ValueError
            If ``name == "name"``, the value differs from the current name
            and the element belongs to a network.
        """
        if name != "name" or value == self.name:
            return
        network = self._owning_network()
        if network is not None:
            raise ValueError(
                f"Cannot rename {self._KIND} '{self.name}' to {value!r}: it "
                f"belongs to network '{network.name}', which keeps it under "
                "its name (branches, sources, faults and results refer to it "
                "by that name). Remove it and add a renamed copy instead."
            )

    def _after_field_assignment(self, name: str) -> None:
        """
        Keep an element that belongs to a network consistent with it.

        Parameters
        ----------
        name : str
            The field that was assigned.

        Raises
        ------
        ValueError
            If a bus reference names a bus the network does not have, or if
            the impedances cannot be derived from the new input. The field is
            then rolled back by :meth:`ValidatedAssignmentModel.__setattr__`.
        """
        if name not in self._BUS_REFERENCES and name not in self._DERIVATION_INPUTS:
            return
        network = self._owning_network()
        if network is None:
            return
        if name in self._BUS_REFERENCES:
            bus = getattr(self, name)
            if bus not in network.buses:
                raise ValueError(
                    f"{self._KIND.capitalize()} '{self.name}': {name} '{bus}' is "
                    f"not a bus of network '{network.name}'."
                )
        if name in self._DERIVATION_INPUTS:
            self.calculate_impedance(network.frequencies)


# data types
class ComplexNumber(BaseModel):
    """
    Pydantic-compatible complex number with real and imaginary parts.

    Wraps the native :class:`complex` type so that Pydantic models can
    serialise and deserialise complex numbers through JSON.

    Attributes
    ----------
    real : float
        The real part of the complex number.
    imag : float
        The imaginary part of the complex number.

    Notes
    -----
    Non-finite parts are serialised as the **strings** ``"inf"``,
    ``"-inf"`` and ``"nan"`` rather than as the bare JavaScript-style
    ``Infinity`` / ``NaN`` literals Python's ``json`` module emits.
    ``inf`` is a load-bearing value here -- it is the open-end sentinel
    produced by the ``"nan"`` impedance formula -- so it must survive a
    round-trip, and the file must stay RFC 8259 JSON: SQLite's
    ``json_valid()`` returns 0 for ``Infinity``, and a browser's
    ``JSON.parse`` refuses it outright, which would make every stored
    network with an open end unreadable to anything but Python. The
    validator already accepts those strings, so the round-trip is
    symmetric.

    Examples
    --------
    >>> from groundinsight import ComplexNumber
    >>> z = ComplexNumber(real=3.0, imag=4.0)
    >>> abs(z), complex(z)
    (5.0, (3+4j))
    >>> z * 2
    ComplexNumber(real=6.0, imag=8.0)
    >>> ComplexNumber.model_validate("1+2j")
    ComplexNumber(real=1.0, imag=2.0)
    """

    model_config = ConfigDict(ser_json_inf_nan="constants")

    real: float
    imag: float

    @field_serializer("real", "imag", when_used="json")
    def _serialise_part(self, value: float) -> Union[float, str]:
        """Write ``inf`` / ``-inf`` / ``nan`` as RFC-8259-safe strings."""
        return json_safe_float(value)

    @field_validator("real", "imag", mode="before")
    def convert_to_float(cls, value: Any) -> float:
        """Coerce ``real`` / ``imag`` inputs to ``float``; ``None`` becomes ``NaN``.

        Parameters
        ----------
        value : Any
            Numeric input to coerce. ``None`` is interpreted
            as ``numpy.nan``.

        Returns
        -------
        float
            The coerced value.
        """
        if value is None:
            return np.nan
        return float(value)

    @model_validator(mode="before")
    @classmethod
    def validate_complex(cls, value: Any) -> Union["ComplexNumber", dict]:
        """
        Validates and converts the input value to a ComplexNumber instance.

        Parameters
        ----------
        value : Any
            The value to validate and convert. Can be a
            ``ComplexNumber``, ``complex``, ``float``, ``int``,
            ``dict`` or ``str``.

        Returns
        -------
        Union[ComplexNumber, dict]
            Either the original
        ``ComplexNumber`` instance (passed through) or a dictionary
        with ``real`` and ``imag`` keys ready for Pydantic to
        instantiate ``ComplexNumber``.

        Raises
        ------
        ValueError
            If the input string cannot be parsed as a complex number.
        TypeError
            If the input type is unsupported.
        """
        if isinstance(value, cls):
            return value
        elif isinstance(value, complex):
            return {"real": value.real, "imag": value.imag}
        elif isinstance(value, (float, int)):
            return {"real": float(value), "imag": 0.0}
        elif isinstance(value, dict):
            return value
        elif isinstance(value, str):
            try:
                c = complex(value.replace(" ", "").replace("i", "j"))
                return {"real": c.real, "imag": c.imag}
            except ValueError as error:
                raise ValueError(f"Invalid complex number string: {value}") from error
        elif isinstance(value, (np.generic, numbers.Complex)):
            # NumPy scalars reach the models straight from the solver arrays;
            # ``np.complex128`` happens to subclass ``complex`` but
            # ``np.complex64`` and the integer types do not, and a bare
            # TypeError for them would name neither the value nor the model.
            try:
                c = complex(value)
            except (TypeError, ValueError) as exc:
                raise TypeError(
                    f"Cannot parse ComplexNumber from type {type(value)}"
                ) from exc
            return {"real": c.real, "imag": c.imag}
        else:
            raise TypeError(f"Cannot parse ComplexNumber from type {type(value)}")

    def __complex__(self):
        return complex(self.real, self.imag)

    def __repr__(self):
        # Round-trippable: ``eval(repr(z))`` reconstructs the value with
        # ``ComplexNumber`` in scope. The previous form ``(-1.0+-2.0j)``
        # produced ``+-`` for a negative imaginary part and was neither valid
        # Python nor a valid complex literal.
        return f"ComplexNumber(real={self.real!r}, imag={self.imag!r})"

    # Implementing arithmetic operations
    def __add__(self, other):
        other = self._convert_to_complex_number(other)
        return ComplexNumber(real=self.real + other.real, imag=self.imag + other.imag)

    def __radd__(self, other):
        return self.__add__(other)

    def __sub__(self, other):
        other = self._convert_to_complex_number(other)
        return ComplexNumber(real=self.real - other.real, imag=self.imag - other.imag)

    def __rsub__(self, other):
        other = self._convert_to_complex_number(other)
        return ComplexNumber(real=other.real - self.real, imag=other.imag - self.imag)

    def __mul__(self, other):
        other = self._convert_to_complex_number(other)
        c1 = complex(self.real, self.imag)
        c2 = complex(other.real, other.imag)
        result = c1 * c2
        return ComplexNumber(real=result.real, imag=result.imag)

    def __rmul__(self, other):
        return self.__mul__(other)

    def __truediv__(self, other):
        other = self._convert_to_complex_number(other)
        c1 = complex(self.real, self.imag)
        c2 = complex(other.real, other.imag)
        result = c1 / c2
        return ComplexNumber(real=result.real, imag=result.imag)

    def __rtruediv__(self, other):
        other = self._convert_to_complex_number(other)
        c1 = complex(other.real, other.imag)
        c2 = complex(self.real, self.imag)
        result = c1 / c2
        return ComplexNumber(real=result.real, imag=result.imag)

    def __neg__(self):
        return ComplexNumber(real=-self.real, imag=-self.imag)

    def __abs__(self):
        return abs(complex(self.real, self.imag))

    def __eq__(self, other):
        """Compare against anything convertible; defer otherwise.

        Returning ``NotImplemented`` instead of raising is what the data
        model asks for: Python then tries the reflected operation and finally
        falls back to identity, so ``z == None`` is ``False`` rather than a
        ``TypeError``. Every ``Optional[ComplexNumber]`` field in this package
        is compared against ``None`` somewhere, and the raising version turned
        that ordinary check into a crash.
        """
        try:
            other = self._convert_to_complex_number(other)
        except TypeError:
            return NotImplemented
        return self.real == other.real and self.imag == other.imag

    def __hash__(self):
        """Hash as the equivalent :class:`complex`.

        Defining ``__eq__`` sets ``__hash__`` to ``None``, which made
        ``ComplexNumber`` unusable as a dict key or set member. Hashing the
        equivalent ``complex`` keeps the hash/eq contract across the types
        this class compares equal to: ``hash(ComplexNumber(real=1, imag=0))``
        equals ``hash(1.0)``.
        """
        return hash(complex(self.real, self.imag))

    # Implementing exponentiation
    def __pow__(self, power, modulo=None):
        if modulo is not None:
            raise ValueError("Modulo operation is not supported for complex numbers.")
        try:
            power = complex(self._convert_to_complex_number(power))
        except TypeError as error:
            raise TypeError(f"Unsupported type for exponentiation: {type(power)}") from error
        base = complex(self.real, self.imag)
        result = base**power
        return ComplexNumber(real=result.real, imag=result.imag)

    def __rpow__(self, other):
        try:
            other = complex(self._convert_to_complex_number(other))
        except TypeError as error:
            raise TypeError(f"Unsupported type for exponentiation: {type(other)}") from error
        exponent = complex(self.real, self.imag)
        result = other**exponent
        return ComplexNumber(real=result.real, imag=result.imag)

    def _convert_to_complex_number(self, value) -> "ComplexNumber":
        """
        Converts a value to a ComplexNumber instance.

        Parameters
        ----------
        value : ComplexNumber, complex, float, int, numpy scalar
            The value to convert. NumPy scalars (``np.int64``,
            ``np.float32``, ``np.complex64``, ...) and anything else
            registered as :class:`numbers.Complex` are accepted, because the
            solver hands NumPy scalars back out of the linear algebra, and
            ``z * np.complex64(2)`` has to work as ``np.complex64(2) * z``
            does -- an asymmetry there stays invisible until it hits a user.

        Returns
        -------
        ComplexNumber
            The converted complex number.

        Raises
        ------
        TypeError
            If the value cannot be converted to ComplexNumber.
        """
        if isinstance(value, ComplexNumber):
            return value
        if isinstance(value, (complex, int, float)):
            converted = complex(value)
        elif isinstance(value, (np.generic, numbers.Complex)):
            # ``np.generic`` also catches NumPy scalars that are not numbers
            # (``np.str_``, ``np.bool_``); ``complex()`` rejects those and the
            # failure is reported as a TypeError like any other bad operand.
            try:
                converted = complex(value)
            except (TypeError, ValueError) as exc:
                raise TypeError(
                    f"Cannot convert {type(value)} to ComplexNumber"
                ) from exc
        else:
            raise TypeError(f"Cannot convert {type(value)} to ComplexNumber")
        return ComplexNumber(real=converted.real, imag=converted.imag)


class _ReadOnlyType(BaseModel):
    """
    Base of :class:`BusType` and :class:`BranchType`: read-only after construction.

    A type is evaluated into every element that uses it; the elements keep
    the result, not the formula. Changing a type in place would therefore
    leave the impedances of all those elements describing the *old* type,
    with nothing to tell which ones -- and after a JSON or database round
    trip every element holds its own copy of the type anyway, so "all
    elements of this type" is not even well defined. A changed type is a new
    type: create it with ``model_copy(update=...)`` and assign it to the
    elements that should use it (``network.update_bus(name, type=new)``),
    which re-derives them.
    """

    def __setattr__(self, name: str, value: Any) -> None:
        if name in type(self).model_fields:
            kind = type(self).__name__
            element = "bus" if kind == "BusType" else "branch"
            raise ValueError(
                f"{kind} {getattr(self, 'name', '')!r} is read-only: it has been "
                f"evaluated into the impedances of every {element} that uses it, "
                "and changing it in place would leave those impedances stale. "
                f"Create a changed type with new_type = this_type.model_copy("
                f"update={{{name!r}: ...}}) and assign it with "
                f"network.update_{element}(name, type=new_type)."
            )
        super().__setattr__(name, value)


def _check_type_symbols(
    element_type: BaseModel, kind: str, allowed: Tuple[str, ...], element: str
) -> None:
    """
    Re-check the formula symbols of a type when an element is derived from it.

    The type validators already reject unknown symbols, but
    ``model_copy(update=...)`` builds a type without running them. Checking
    again here -- the token scan is cached per formula -- keeps the clear
    message on that path too, instead of SymPy's "Cannot convert expression
    to float".

    Parameters
    ----------
    element_type : BaseModel
        The ``BusType`` or ``BranchType`` of the element.
    kind : str
        ``"BusType"`` or ``"BranchType"``, for the message.
    allowed : Tuple[str, ...]
        The parameters the element binds.
    element : str
        How to name the element in the message, e.g. ``"bus 'a'"``.

    Raises
    ------
    ValueError
        As documented for
        :func:`~groundinsight.utils.validations.check_formula_symbols`.
    """
    for field in type(element_type).model_fields:
        if not field.endswith("_formula"):
            continue
        formula = getattr(element_type, field)
        if formula is not None:
            check_formula_symbols(
                formula,
                allowed,
                context=f"{kind} {element_type.name!r} ({element}): {field}",
            )


def _formula_context(kind: str, info: ValidationInfo) -> str:
    """
    Name a formula field of a type for an error message.

    Parameters
    ----------
    kind : str
        ``"BusType"`` or ``"BranchType"``.
    info : ValidationInfo
        Pydantic's validation context of the field being validated. ``name``
        is declared first on both types, so it is available here.

    Returns
    -------
    str
        For example ``"BranchType 'cable': self_impedance_formula"``.
    """
    name = info.data.get("name") if info.data else None
    owner = f"{kind} {name!r}" if name is not None else kind
    return f"{owner}: {info.field_name}"


# user interface
class BusType(_ReadOnlyType):
    """
    Represents the type of a bus, including its default impedance formula.

    The mandatory ``impedance_formula`` is used by the frequency-domain
    solver (``Y(f) u = i``) and is the only required parameter for
    stationary studies.

    For transient simulations the type can additionally carry an explicit
    lumped-element decomposition ``R_formula`` / ``L_formula`` /
    ``C_formula``. These are parallel to ``impedance_formula``: the
    frequency-domain solver ignores them, the FFT- and state-space-based
    transient solvers consume them. The duplication is intentional so
    that the stationary model and the transient equivalent can be
    parameterised independently — a substation grounding, for example,
    may be modelled as a constant ``R`` in the stationary formula while
    the transient model uses the full ``R + j*omega*L`` plus an HF
    capacitance to remote earth.

    Attributes
    ----------
    name : str
        The name of the bus type.
    description : str, optional
        A brief description of the bus type.
    system_type : str
        The system type associated with the bus, e.g. ``'Tower'`` or
        ``'Substation'``.
    voltage_level : float
        The voltage level of the bus, in kV.
    impedance_formula : str
        SymPy formula for the frequency-domain grounding impedance
        ``Z(f, rho)``. Mandatory.
    R_formula : str, optional
        SymPy formula for the lumped resistance ``R(rho, f)`` in Ohm.
        Used only by the transient solvers.
    L_formula : str, optional
        SymPy formula for the lumped inductance ``L(rho, f)`` in Henry.
        Used only by the transient solvers.
    C_formula : str, optional
        SymPy formula for the lumped capacitance to remote earth
        ``C(rho, f)`` in Farad. Used only by the transient solvers
        (typically only relevant for HF studies).
    earthing_conductor_material : {'Cu', 'Al', 'Steel'}, optional
        Material of the **earthing conductor** (*Erdungsleiter*) — the
        lumped connection that carries the earth-fault current from the
        installation into the grounding system. Consumed only by
        :func:`groundinsight.check_node_limits`.
    earthing_conductor_cross_section_mm2 : float, optional
        Cross-section of the earthing conductor in mm². Must be strictly
        positive when given.
    earthing_conductor_theta_initial_C : float
        Initial earthing-conductor temperature in °C. Defaults to
        ``20.0`` (ambient).
    earthing_conductor_theta_final_C : float, optional
        Maximum permissible earthing-conductor temperature in °C.
        Defaults to the material value in
        :data:`groundinsight.analysis.thermal.IEC60949_MATERIALS`.
    earthing_conductor_current_split : float
        Share of the bus injection this conductor carries, in ``(0, 1]``.
        ``1.0`` (default) is a single conductor carrying everything;
        ``1/N`` splits the current across ``N`` equal parallel
        conductors. See :func:`groundinsight.check_node_limits`.
    electrode_material : {'Cu', 'Al', 'Steel'}, optional
        Material of the **earth electrode** (*Erder*) — the part buried
        in the soil, which only carries the share of the current that is
        actually dissipated to earth at this bus.
    electrode_cross_section_mm2 : float, optional
        Cross-section of the earth electrode in mm². Must be strictly
        positive when given.
    electrode_theta_initial_C : float
        Initial electrode temperature in °C. Defaults to ``20.0``.
    electrode_theta_final_C : float, optional
        Maximum permissible electrode temperature in °C. Buried
        electrodes are usually limited well below a free-air conductor
        to protect the surrounding soil and any coating; EN 50522
        Table 2 is the reference.
    electrode_current_split : float
        Share of the dissipated current a single electrode carries, in
        ``(0, 1]``. Use ``1/N`` for ``N`` equal parallel electrodes at
        the same bus.

    Notes
    -----
    The thermal fields are optional throughout. A bus is only assessed by
    :func:`groundinsight.check_node_limits` once both the material and
    the cross-section of the respective element are set; the two elements
    are independent, so a bus may declare only its electrode or only its
    earthing conductor.

    **Formula symbols.** Every formula of a bus type is evaluated with ``f``
    (frequency in Hz) and ``rho`` (specific earth resistance in Ohm*m); the
    constants ``j``/``I`` (imaginary unit), ``pi`` and ``E`` are available
    as well. Any other name -- ``F``, ``Rho``, or the branch length ``l`` --
    is rejected when the type is created, with the case-insensitive match as
    a suggestion (see
    :func:`~groundinsight.utils.validations.check_formula_symbols`).

    **Read-only.** A bus type cannot be changed after construction: it has
    been evaluated into the impedances of the buses that use it. Create a
    changed type with ``model_copy(update=...)`` and assign it per bus with
    :meth:`Network.update_bus`, which re-derives the bus.

    Examples
    --------
    >>> import groundinsight as gi
    >>> station = gi.BusType(name="station", system_type="Substation",
    ...                      voltage_level=20.0,
    ...                      impedance_formula="rho * 0.01 + j * f * 0.002")
    >>> station.impedance_formula
    'rho * 0.01 + j * f * 0.002'
    """

    model_config = ConfigDict(ser_json_inf_nan="constants")

    name: str
    description: Optional[str] = None
    system_type: str
    voltage_level: float
    impedance_formula: str
    R_formula: Optional[str] = None
    L_formula: Optional[str] = None
    C_formula: Optional[str] = None
    earthing_conductor_material: Optional[Literal["Cu", "Al", "Steel"]] = None
    earthing_conductor_cross_section_mm2: Optional[float] = None
    earthing_conductor_theta_initial_C: float = 20.0
    earthing_conductor_theta_final_C: Optional[float] = None
    earthing_conductor_current_split: float = 1.0
    electrode_material: Optional[Literal["Cu", "Al", "Steel"]] = None
    electrode_cross_section_mm2: Optional[float] = None
    electrode_theta_initial_C: float = 20.0
    electrode_theta_final_C: Optional[float] = None
    electrode_current_split: float = 1.0

    @field_validator("impedance_formula")
    def validate_impedance_formula(cls, value, info: ValidationInfo):
        """Validate the SymPy ``impedance_formula`` string.

        Runs the security guard and the SymPy parse, then checks that the
        formula only uses the symbols a bus formula is evaluated with
        (:data:`~groundinsight.utils.validations.BUS_FORMULA_SYMBOLS`).
        """
        value = validate_impedance_formula_value(value)
        return check_formula_symbols(
            value, BUS_FORMULA_SYMBOLS, context=_formula_context("BusType", info)
        )

    @field_validator("R_formula", "L_formula", "C_formula")
    def validate_rlc_formula(cls, value, info: ValidationInfo):
        """Validate the optional lumped RLC formula strings; ``None`` is allowed."""
        if value is None:
            return value
        value = validate_impedance_formula_value(value)
        return check_formula_symbols(
            value, BUS_FORMULA_SYMBOLS, context=_formula_context("BusType", info)
        )

    @field_validator(
        "earthing_conductor_cross_section_mm2", "electrode_cross_section_mm2"
    )
    def _validate_cross_section(cls, value):
        """Reject a non-positive conductor / electrode cross-section."""
        if value is not None and value <= 0:
            raise ValueError("cross_section_mm2 must be strictly positive.")
        return value

    @field_validator("earthing_conductor_current_split", "electrode_current_split")
    def _validate_current_split(cls, value):
        """Reject a current-split factor outside ``(0, 1]``.

        A factor above 1 would mean the element carries more than the bus
        current, which is not a split but an error; a factor of 0 or less
        is unphysical. Distributing the current over more parallel paths
        than modelled is expressed as ``1/N``, never as ``> 1``.
        """
        if value is None:
            raise ValueError("current_split must not be None.")
        if not 0.0 < float(value) <= 1.0:
            raise ValueError(
                f"current_split must lie in (0, 1], got {value!r}."
            )
        return float(value)

    def __str__(self):
        return f"BusType(name={self.name}, system_type={self.system_type}, voltage_level={self.voltage_level})"


class Bus(_NetworkElement):
    """
    Represents a grounding bus within the network.

    Attributes
    ----------
    name : str
        The name of the bus.
    description : str, optional
        A brief description of the bus.
    type : BusType
        The type of the bus.
    impedance : dict of float to ComplexNumber
        Mapping of frequency to grounding impedance values.
    specific_earth_resistance : float
        The specific earth resistance associated with the bus (Ohm * m).
    active : bool
        Whether the bus participates in the solve. Inactive buses are
        removed from the admittance matrix; paths traversing them are
        dropped. Defaults to ``True``. Used by the outage / what-if
        machinery in :mod:`groundinsight.simulation.outage`.
    R : dict of float to float, optional
        Evaluated lumped resistance per frequency in Ohm. Populated only
        if ``type.R_formula`` is set. Consumed by the transient solvers.
    L : dict of float to float, optional
        Evaluated lumped inductance per frequency in Henry. Populated
        only if ``type.L_formula`` is set.
    C : dict of float to float, optional
        Evaluated lumped capacitance to remote earth per frequency in
        Farad. Populated only if ``type.C_formula`` is set.

    Examples
    --------
    >>> import groundinsight as gi
    >>> station = gi.BusType(name="station", system_type="Substation",
    ...                      voltage_level=20.0, impedance_formula="rho * 0.01")
    >>> net = gi.create_network(name="n", frequencies=[50.0])
    >>> bus = gi.create_bus(name="B1", type=station, network=net,
    ...                     specific_earth_resistance=200.0)
    >>> bus.impedance
    {50.0: ComplexNumber(real=2.0, imag=0.0)}
    """

    model_config = ConfigDict(
        ser_json_inf_nan="constants", validate_assignment=True
    )

    _COLLECTION: ClassVar[str] = "buses"
    _KIND: ClassVar[str] = "bus"
    _DERIVATION_INPUTS: ClassVar[FrozenSet[str]] = frozenset(
        {"type", "specific_earth_resistance"}
    )

    name: str
    description: Optional[str] = None
    type: BusType
    impedance: Dict[float, ComplexNumber]
    specific_earth_resistance: float = 100.0
    active: bool = True
    R: Optional[Dict[float, float]] = None
    L: Optional[Dict[float, float]] = None
    C: Optional[Dict[float, float]] = None

    #: Fields that :meth:`calculate_impedance` computes from ``type``,
    #: ``specific_earth_resistance`` and the network frequencies.
    DERIVED_FIELDS: ClassVar[Tuple[str, ...]] = ("impedance", "R", "L", "C")
    _DERIVED_TABLES: ClassVar[FrozenSet[str]] = frozenset({"impedance"})

    def calculate_impedance(self, frequencies: List[float]):
        """
        Calculate the impedance and, where the type asks for them, R, L and C.

        Both are evaluated at each frequency; R, L and C are the lumped
        parameters of the type's optional formulas.

        ``impedance`` is always recomputed from ``type.impedance_formula``.
        Each of ``R``, ``L`` and ``C`` is computed from the matching formula
        of the type, and set to ``None`` when the type has none. Utilizes the
        external ``impedance_calculator`` to avoid storing non-pickleable
        functions.

        The update is atomic: every value is computed first and only then
        written, so a formula that fails to evaluate leaves the bus exactly as
        it was.

        Parameters
        ----------
        frequencies : List[float]
            A list of frequencies at which to
            evaluate the formulas.

        Raises
        ------
        ValueError
            If a formula cannot be evaluated or yields a grounding impedance
            that cannot be inverted into an admittance.
        """
        self._apply_derived(self._derive(frequencies))

    def _derive(self, frequencies: List[float]) -> Dict[str, Any]:
        """
        Compute every derived field without writing any of them.

        Parameters
        ----------
        frequencies : List[float]
            Frequencies at which to evaluate the formulas.

        Returns
        -------
        Dict[str, Any]
            One entry per name in :attr:`DERIVED_FIELDS`. ``R``, ``L`` and
            ``C`` are ``None`` when the type declares no formula for them.

        Raises
        ------
        ValueError
            As documented for :meth:`calculate_impedance`.
        """
        params = self._params()
        _check_type_symbols(self.type, "BusType", BUS_FORMULA_SYMBOLS, f"bus {self.name!r}")

        # Mandatory frequency-domain impedance.
        values = compute_impedance_array(
            self.type.impedance_formula, frequencies, params
        )
        freqs_arr = np.asarray(frequencies, dtype=float)
        # The grounding impedance is inverted into a diagonal admittance, so a
        # value that cannot become an admittance has to be rejected here rather
        # than silently dropped by the solver. See check_passive_impedance;
        # the same rules and message run on the evaluated array, before it is
        # packed (P10).
        check_passive_impedance_array(
            values,
            freqs_arr,
            element=f"bus '{self.name}' (grounding impedance)",
            formula_str=self.type.impedance_formula,
            params=params,
        )
        derived: Dict[str, Any] = {"impedance": impedance_table(freqs_arr, values)}

        # Optional lumped RLC -- None when the type declares no formula.
        for field, formula in (
            ("R", self.type.R_formula),
            ("L", self.type.L_formula),
            ("C", self.type.C_formula),
        ):
            derived[field] = (
                None
                if formula is None
                else compute_real_value(
                    formula, frequencies, params, name=f"{self.name}.{field}"
                )
            )
        return derived

    def _apply_derived(self, derived: Dict[str, Any]) -> None:
        """
        Write values computed by :meth:`_derive`.

        Parameters
        ----------
        derived : Dict[str, Any]
            Field name to value, as returned by :meth:`_derive`.
        """
        self._store_derived(derived)

    def _params(self) -> Dict[str, float]:
        """Formula parameters of this bus: the specific earth resistance ``rho``."""
        return {"rho": self.specific_earth_resistance}

    @field_validator("impedance", mode="before")
    def validate_impedance(cls, value):
        """Coerce the keys to ``float`` and every value to a ``ComplexNumber``."""
        if not isinstance(value, dict):
            raise TypeError(
                "Impedance must be a dictionary of frequency to impedance values."
            )
        new_value = {}
        for freq, imp in value.items():
            freq = float(freq)
            new_value[freq] = ComplexNumber.validate_complex(imp)
        return new_value

    def __str__(self):
        return (
            f"Bus(name={self.name}, type={self.type.name}, impedance={self.impedance})"
        )


class BranchType(_ReadOnlyType):
    """
    Represents the type of a branch, including its impedance formulas.

    The mandatory ``self_impedance_formula`` and
    ``mutual_impedance_formula`` drive the frequency-domain solver.

    The optional ``phase_impedance_formula`` describes the *phase*
    conductor -- the faulted conductor whose current induces the
    longitudinal EMF on the shield -- and is what the automatic
    phase-current distribution solves on. It is only needed when the
    network contains rings, meshes or parallel branches, because only
    then does the source current have more than one way to reach the
    fault. Without it the distribution falls back to a documented proxy
    and warns; see :meth:`~groundinsight.electrical_network.
    ElectricalNetwork._compute_phase_currents_auto`.

    For transient simulations the type can additionally carry a lumped
    RLCM decomposition: ``R_self_formula``, ``L_self_formula``,
    ``C_self_formula`` (shunt-to-ground capacitance per branch, only
    relevant for HF studies), ``R_mutual_formula`` (Carson earth-return
    resistance term) and ``M_mutual_formula`` (mutual inductance to the
    parallel phase conductor). These are parallel to the impedance
    formulas: the frequency-domain solver ignores them, the state-space
    and FFT-based transient solvers consume them. The duplication is
    intentional so the stationary and transient parameterisations can be
    maintained independently.

    Attributes
    ----------
    name : str
        The name of the branch type.
    description : str, optional
        A brief description of the branch type.
    grounding_conductor : bool
        Indicates whether the branch has a grounding wire or cable
        shield.
    self_impedance_formula : str
        SymPy formula used to calculate self-impedance per branch.
    mutual_impedance_formula : str
        SymPy formula used to calculate mutual impedance.
    phase_impedance_formula : str, optional
        SymPy formula for the series impedance of the *phase* conductor
        of this branch, in the same symbols ``f``, ``rho``, ``l`` as the
        other formulas. Consumed only by the automatic phase-current
        distribution; the admittance matrix never sees it.
    R_self_formula : str, optional
        Per-branch series resistance in Ohm. Used only by the transient
        solvers.
    L_self_formula : str, optional
        Per-branch series inductance in Henry. Used only by the transient
        solvers.
    C_self_formula : str, optional
        Per-branch shunt capacitance to remote earth in Farad. Used only
        by the transient solvers.
    R_mutual_formula : str, optional
        Per-branch mutual resistance (Carson earth-return) in Ohm. Used
        only by the transient solvers.
    M_mutual_formula : str, optional
        Per-branch mutual inductance in Henry. Used only by the transient
        solvers.
    conductor_material : {'Cu', 'Al', 'Steel'}, optional
        Material of the shield or earth wire, for the adiabatic thermal check
        of :func:`groundinsight.check_conductor_limits`.
    cross_section_mm2 : float, optional
        Cross-section of that conductor in mm². A branch is assessed only
        when both the material and the cross-section are given.
    theta_initial_C : float
        Initial conductor temperature in °C. Defaults to ``20.0``.
    theta_final_C : float, optional
        Maximum permissible conductor temperature in °C. Defaults to the
        material value in
        :data:`groundinsight.analysis.thermal.IEC60949_MATERIALS`.

    Notes
    -----
    **Formula symbols.** Every formula of a branch type is evaluated with
    ``f`` (frequency in Hz), ``rho`` (specific earth resistance in Ohm*m) and
    ``l`` (branch length in km); the constants ``j``/``I`` (imaginary unit),
    ``pi`` and ``E`` are available as well. Any other name -- ``L``, ``F``,
    ``Rho`` -- is rejected when the type is created, with the
    case-insensitive match as a suggestion (see
    :func:`~groundinsight.utils.validations.check_formula_symbols`).

    **Read-only.** A branch type cannot be changed after construction: it has
    been evaluated into the impedances of the branches that use it. Create a
    changed type with ``model_copy(update=...)`` and assign it per branch
    with :meth:`Network.update_branch`, which re-derives the branch.

    Examples
    --------
    >>> import groundinsight as gi
    >>> cable = gi.BranchType(name="cable", grounding_conductor=True,
    ...                       self_impedance_formula="(0.25 + j * f * 0.012) * l",
    ...                       mutual_impedance_formula="j * f * 0.012 * l")
    >>> cable.grounding_conductor, cable.phase_impedance_formula is None
    (True, True)
    """

    model_config = ConfigDict(ser_json_inf_nan="constants")

    name: str
    description: Optional[str] = None
    grounding_conductor: bool
    self_impedance_formula: str
    mutual_impedance_formula: str
    phase_impedance_formula: Optional[str] = None
    R_self_formula: Optional[str] = None
    L_self_formula: Optional[str] = None
    C_self_formula: Optional[str] = None
    R_mutual_formula: Optional[str] = None
    M_mutual_formula: Optional[str] = None
    conductor_material: Optional[Literal["Cu", "Al", "Steel"]] = None
    cross_section_mm2: Optional[float] = None
    theta_initial_C: float = 20.0
    theta_final_C: Optional[float] = None

    @field_validator("self_impedance_formula", "mutual_impedance_formula")
    def validate_impedance_formula(cls, value, info: ValidationInfo):
        """Validate the SymPy self / mutual impedance formula strings.

        Runs the security guard and the SymPy parse, then checks that the
        formula only uses the symbols a branch formula is evaluated with
        (:data:`~groundinsight.utils.validations.BRANCH_FORMULA_SYMBOLS`).
        """
        value = validate_impedance_formula_value(value)
        return check_formula_symbols(
            value, BRANCH_FORMULA_SYMBOLS, context=_formula_context("BranchType", info)
        )

    @field_validator(
        "phase_impedance_formula",
        "R_self_formula",
        "L_self_formula",
        "C_self_formula",
        "R_mutual_formula",
        "M_mutual_formula",
    )
    def validate_rlc_formula(cls, value, info: ValidationInfo):
        """Validate the optional phase / lumped RLCM formula strings; ``None`` is allowed."""
        if value is None:
            return value
        value = validate_impedance_formula_value(value)
        return check_formula_symbols(
            value, BRANCH_FORMULA_SYMBOLS, context=_formula_context("BranchType", info)
        )

    @field_validator("cross_section_mm2")
    def _validate_cross_section(cls, value):
        """Reject a non-positive conductor cross-section."""
        if value is not None and value <= 0:
            raise ValueError("cross_section_mm2 must be strictly positive.")
        return value

    def __str__(self):
        return f"BranchType(name={self.name}, grounding_conductor={self.grounding_conductor})"


class Branch(_NetworkElement):
    """
    Represents a branch (conductor) connecting two buses in the network.

    Attributes
    ----------
    name : str
        The name of the branch.
    description : str, optional
        A brief description of the branch.
    type : BranchType
        The type of the branch.
    length : float
        The length of the branch in km. Must be finite and strictly
        positive; it is checked on construction and on assignment.
    from_bus : str
        The name of the originating bus.
    to_bus : str
        The name of the destination bus.
    self_impedance : dict of float to ComplexNumber
        Self-impedance values mapped by frequency.
    mutual_impedance : dict of float to ComplexNumber
        Mutual-impedance values mapped by frequency.
    specific_earth_resistance : float
        The specific earth resistance associated with the branch
        (Ohm * m).
    parallel_coefficient : float, optional
        The parallel coefficient between 0 and 1, if any. Defaults to
        ``1.0``.
    active : bool
        Whether the branch participates in the solve. An inactive branch
        behaves like an open circuit: it contributes neither to the
        admittance matrix nor to the mutual-coupling injection, paths
        traversing it are dropped, and its branch current in the result
        is forced to zero. Defaults to ``True``. Used by the outage /
        what-if machinery in :mod:`groundinsight.simulation.outage`.
    phase_impedance : dict of float to ComplexNumber, optional
        Phase-conductor impedance per frequency, evaluated from
        ``BranchType.phase_impedance_formula``; ``None`` without that
        formula. Read by the phase-current solve (``phase_current_mode=
        "auto"``).
    R_self, L_self, C_self : dict of float to float, optional
        Series resistance (Ohm), series inductance (H) and shunt capacitance
        to remote earth (F) per frequency, from the matching ``*_formula``
        of the type; ``None`` without it. Read only by the state-space
        transient solver.
    R_mutual, M_mutual : dict of float to float, optional
        Mutual resistance (Ohm) and mutual inductance (H) per frequency, from
        the matching ``*_formula`` of the type; ``None`` without it. Read
        only by the state-space transient solver.

    Examples
    --------
    >>> from groundinsight.api.examples import build_minimal
    >>> branch = build_minimal().branches["b12"]
    >>> branch.from_bus, branch.to_bus, branch.length
    ('bus1', 'bus2', 1.0)
    >>> branch.self_impedance[50.0], branch.mutual_impedance[50.0]
    (ComplexNumber(real=0.25, imag=0.6), ComplexNumber(real=0.0, imag=0.6))
    """

    model_config = ConfigDict(
        ser_json_inf_nan="constants", validate_assignment=True
    )

    _COLLECTION: ClassVar[str] = "branches"
    _KIND: ClassVar[str] = "branch"
    _DERIVATION_INPUTS: ClassVar[FrozenSet[str]] = frozenset(
        {"type", "length", "specific_earth_resistance"}
    )
    _BUS_REFERENCES: ClassVar[Tuple[str, ...]] = ("from_bus", "to_bus")

    name: str
    description: Optional[str] = None
    type: BranchType
    length: float
    from_bus: str
    to_bus: str
    self_impedance: Dict[float, ComplexNumber]
    mutual_impedance: Dict[float, ComplexNumber]
    specific_earth_resistance: float = 100.0
    parallel_coefficient: Optional[float] = 1.0  # Default to 1
    active: bool = True
    phase_impedance: Optional[Dict[float, ComplexNumber]] = None
    R_self: Optional[Dict[float, float]] = None
    L_self: Optional[Dict[float, float]] = None
    C_self: Optional[Dict[float, float]] = None
    R_mutual: Optional[Dict[float, float]] = None
    M_mutual: Optional[Dict[float, float]] = None

    #: Fields that :meth:`calculate_impedance` computes from ``type``,
    #: ``length``, ``specific_earth_resistance`` and the network frequencies.
    DERIVED_FIELDS: ClassVar[Tuple[str, ...]] = (
        "self_impedance",
        "mutual_impedance",
        "phase_impedance",
        "R_self",
        "L_self",
        "C_self",
        "R_mutual",
        "M_mutual",
    )
    _DERIVED_TABLES: ClassVar[FrozenSet[str]] = frozenset(
        {"self_impedance", "mutual_impedance", "phase_impedance"}
    )

    def calculate_impedance(self, frequencies: List[float]):
        """
        Calculate the self and mutual impedance and the optional quantities.

        The phase impedance and the lumped RLCM parameters are evaluated at
        each frequency where the type specifies them.

        ``self_impedance`` and ``mutual_impedance`` are always recomputed.
        Each of ``phase_impedance``, ``R_self``, ``L_self``, ``C_self``,
        ``R_mutual``, ``M_mutual`` is computed from the matching formula of
        the branch type, and set to ``None`` when the type has none.

        The update is atomic: every value is computed first and only then
        written, so a formula that fails to evaluate -- in any of the steps --
        leaves the branch exactly as it was.

        Parameters
        ----------
        frequencies : List[float]
            A list of frequencies at which to
            evaluate the formulas.

        Raises
        ------
        ValueError
            If a formula cannot be evaluated, if the self impedance of a
            grounding conductor cannot be inverted into an admittance, or if
            an RLCM formula yields a non-real value.
        """
        self._apply_derived(self._derive(frequencies))

    def _params(self) -> Dict[str, float]:
        """Formula parameters of this branch: ``rho`` and the length ``l``."""
        return {"rho": self.specific_earth_resistance, "l": self.length}

    def _derive(self, frequencies: List[float]) -> Dict[str, Any]:
        """
        Compute every derived field without writing any of them.

        Parameters
        ----------
        frequencies : List[float]
            Frequencies at which to evaluate the formulas.

        Returns
        -------
        Dict[str, Any]
            One entry per name in :attr:`DERIVED_FIELDS`; the optional ones
            are ``None`` when the type declares no formula for them.

        Raises
        ------
        ValueError
            As documented for :meth:`calculate_impedance`.
        """
        _check_type_symbols(
            self.type, "BranchType", BRANCH_FORMULA_SYMBOLS, f"branch {self.name!r}"
        )
        derived: Dict[str, Any] = {
            "self_impedance": self._calculate_self_impedance(frequencies),
            "mutual_impedance": self._calculate_mutual_impedance(frequencies),
            "phase_impedance": self._calculate_phase_impedance(frequencies),
        }
        derived.update(self._calculate_rlc_parameters(frequencies))
        return derived

    def _apply_derived(self, derived: Dict[str, Any]) -> None:
        """
        Write values computed by :meth:`_derive`.

        Parameters
        ----------
        derived : Dict[str, Any]
            Field name to value, as returned by :meth:`_derive`.
        """
        self._store_derived(derived)

    def _calculate_phase_impedance(
        self, frequencies: List[float]
    ) -> Optional[Dict[float, "ComplexNumber"]]:
        """
        Evaluate the optional phase-conductor impedance formula.

        ``None`` when the branch type declares no
        ``phase_impedance_formula``. The value is consumed only by the
        automatic phase-current distribution and never enters the nodal
        admittance matrix, so it is *not* checked for passivity -- a
        phase conductor is allowed to be modelled with whatever the user
        got from a short-circuit study.

        Parameters
        ----------
        frequencies : List[float]
            Frequencies at which to evaluate.

        Returns
        -------
        Optional[Dict[float, ComplexNumber]]
            The phase impedance per frequency, or ``None``.
        """
        formula = self.type.phase_impedance_formula
        if formula is None:
            return None
        return compute_impedance(
            formula_str=formula, frequencies=frequencies, params=self._params()
        )

    def _calculate_rlc_parameters(
        self, frequencies: List[float]
    ) -> Dict[str, Optional[Dict[float, float]]]:
        """
        Evaluate the optional lumped RLCM formulas of the branch type.

        The same ``rho`` / ``l`` parameter substitution as in the impedance
        formulas is used so the units stay consistent.

        Parameters
        ----------
        frequencies : List[float]
            Frequencies at which to evaluate.

        Returns
        -------
        Dict[str, Optional[Dict[float, float]]]
            ``R_self``, ``L_self``, ``C_self``, ``R_mutual`` and ``M_mutual``,
            each ``None`` when the type declares no formula for it.
        """
        params = self._params()
        values: Dict[str, Optional[Dict[float, float]]] = {}
        for field in ("R_self", "L_self", "C_self", "R_mutual", "M_mutual"):
            formula = getattr(self.type, f"{field}_formula")
            values[field] = (
                None
                if formula is None
                else compute_real_value(
                    formula, frequencies, params, name=f"{self.name}.{field}"
                )
            )
        return values

    def _calculate_self_impedance(
        self, frequencies: List[float]
    ) -> Dict[float, "ComplexNumber"]:
        """
        Calculates self impedance for each frequency using the self impedance formula.

        Utilizes the external `impedance_calculator` to perform the computation.

        Parameters
        ----------
        frequencies : List[float]
            A list of frequencies at which to calculate self impedance.

        Returns
        -------
        Dict[float, ComplexNumber]
            The self impedance per frequency.

        Raises
        ------
        ValueError
            If the formula fails, or if the branch has a grounding conductor
            and the value cannot be inverted into an admittance.
        """
        formula = self.type.self_impedance_formula
        params = self._params()
        values = compute_impedance_array(formula, frequencies, params)
        freqs_arr = np.asarray(frequencies, dtype=float)
        # Only a grounding conductor has its self impedance inverted into an
        # admittance; for a branch without one the self impedance never reaches
        # a division and any value is harmless. The rules run on the evaluated
        # array, before it is packed (P10).
        if self.type.grounding_conductor:
            check_passive_impedance_array(
                values,
                freqs_arr,
                element=f"branch '{self.name}' (self impedance)",
                formula_str=formula,
                params=params,
            )
        return impedance_table(freqs_arr, values)

    def _calculate_mutual_impedance(
        self, frequencies: List[float]
    ) -> Dict[float, "ComplexNumber"]:
        """
        Calculates mutual impedance for each frequency using the mutual impedance formula.

        Utilizes the external `impedance_calculator` to perform the computation.

        Parameters
        ----------
        frequencies : List[float]
            A list of frequencies at which to calculate mutual impedance.

        Returns
        -------
        Dict[float, ComplexNumber]
            The mutual impedance per frequency.
        """
        return compute_impedance(
            formula_str=self.type.mutual_impedance_formula,
            frequencies=frequencies,
            params=self._params(),
        )

    @field_validator("length")
    @classmethod
    def _validate_length(cls, value):
        """Reject a length that is not a finite positive number of kilometres.

        Every formula of a branch type scales with ``l``. ``l = 0`` makes the
        branch an ideal short circuit, which the nodal solver cannot invert,
        and ``l < 0`` gives it a negative resistance. Accepted here, both would
        only surface when the impedance is derived -- as "exactly zero
        impedance" or as a negative real part, neither of which names the
        length -- and a detached branch would not be checked at all.

        Parameters
        ----------
        value : float
            The length in km.

        Returns
        -------
        float
            ``value``, unchanged.

        Raises
        ------
        ValueError
            If ``value`` is zero, negative, NaN or infinite.
        """
        value = float(value)
        if not math.isfinite(value) or value <= 0.0:
            raise ValueError(
                f"Branch.length must be a finite length > 0 in km, got "
                f"{value!r}. Every branch formula scales with l: l = 0 makes "
                "the branch an ideal short circuit the nodal solver cannot "
                "invert, l < 0 gives it a negative resistance. Model a bus "
                "coupler or jumper with a short positive length (e.g. 0.001 km)."
            )
        return value

    @field_validator("self_impedance", mode="before")
    def validate_self_impedance(cls, value):
        """Coerce the keys to ``float`` and every value to a ``ComplexNumber``."""
        if not isinstance(value, dict):
            raise TypeError(
                "Impedance must be a dictionary of frequency to impedance values."
            )
        new_value = {}
        for freq, imp in value.items():
            freq = float(freq)
            new_value[freq] = ComplexNumber.validate_complex(imp)
        return new_value

    @field_validator("mutual_impedance", mode="before")
    def validate_mutual_impedance(cls, value):
        """Coerce the keys to ``float`` and every value to a ``ComplexNumber``."""
        if not isinstance(value, dict):
            raise TypeError(
                "Impedance must be a dictionary of frequency to impedance values."
            )
        new_value = {}
        for freq, imp in value.items():
            freq = float(freq)
            new_value[freq] = ComplexNumber.validate_complex(imp)
        return new_value

    @field_validator("phase_impedance", mode="before")
    def validate_phase_impedance(cls, value):
        """Accept ``None`` or the same shapes as the other impedance dicts."""
        if value is None:
            return None
        if not isinstance(value, dict):
            raise TypeError(
                "Impedance must be a dictionary of frequency to impedance values."
            )
        new_value = {}
        for freq, imp in value.items():
            freq = float(freq)
            new_value[freq] = ComplexNumber.validate_complex(imp)
        return new_value

    def __str__(self):
        return f"Branch(name={self.name}, from={self.from_bus}, to={self.to_bus})"


class Fault(_NetworkElement):
    """
    Represents a fault within the network.

    Attributes
    ----------
    name : str
        The name of the fault.
    description : str, optional
        A brief description of the fault.
    bus : str
        The name of the bus where the fault occurs.
    scalings : dict of float to float, optional
        Factor applied to every source current, per frequency. **A frequency
        without a key is scaled with 1.0** -- full contribution -- and an
        empty or omitted mapping (``None`` is accepted and stored as ``{}``)
        means 1.0 at every frequency. Give ``0.0`` to exclude a frequency
        bin from this fault. A non-empty mapping that leaves out some network
        frequencies is announced once per run with an ``InputWarning``
        ``SCALINGS_INCOMPLETE``. :meth:`scaling_at` is the one
        place that applies the default.
    t_k_s : float, optional
        Short-circuit duration (clearing time) ``T_k`` in seconds, as
        defined in IEC 60909-0. Drives both the DC heat factor ``m`` of
        the thermally equivalent short-circuit current ``I_th`` and the
        adiabatic conductor rating ``I_adm = k * S / sqrt(t_k)``.
        ``None`` means "not specified"; the thermal check then requires
        an explicit ``t_k`` argument.
    n_factor : float, default 1.0
        AC heat factor ``n`` of IEC 60909-0. ``1.0`` is the
        far-from-generator case (no AC decay), which is the normal
        situation for grounding studies. Values below ``1.0`` apply when
        the fault is near a generator and the AC component decays during
        ``T_k``.

    Notes
    -----
    The private ``_active`` attribute indicates whether the fault is the
    currently active one in the network and is exposed read-only through
    the :attr:`active` computed property.

    ``t_k_s`` and ``n_factor`` live on the *fault*, not on the sources,
    because the clearing time is a property of the protection scheme
    reacting to that fault. The IEC 60909 quantities that describe the
    *feeding* side (``I_k''``, ``R/X``, ``kappa``) live on
    :class:`Source` instead.

    Examples
    --------
    >>> import groundinsight as gi
    >>> fault = gi.Fault(name="F1", bus="B3", scalings={50.0: 1.0}, t_k_s=0.5)
    >>> fault.scaling_at(50.0), fault.t_k_s, fault.active
    (1.0, 0.5, False)
    """

    model_config = ConfigDict(
        ser_json_inf_nan="constants", validate_assignment=True
    )

    _COLLECTION: ClassVar[str] = "faults"
    _KIND: ClassVar[str] = "fault"
    _BUS_REFERENCES: ClassVar[Tuple[str, ...]] = ("bus",)

    name: str
    description: Optional[str] = None
    bus: str  # Location of the fault
    scalings: Dict[float, float] = {}  # Scaling factors for sources
    t_k_s: Optional[float] = None  # IEC 60909-0 short-circuit duration T_k [s]
    n_factor: float = 1.0  # IEC 60909-0 AC heat factor n
    _active: bool = PrivateAttr(default=False)

    @field_validator("t_k_s")
    @classmethod
    def _validate_t_k(cls, value):
        """Reject non-positive or non-finite clearing times.

        ``t_k = 0`` would make the adiabatic rating ``k * S / sqrt(t_k)``
        infinite and the DC heat factor ``m`` undefined, so it is rejected
        here rather than producing ``inf``/``nan`` deep inside the
        thermal check.
        """
        if value is None:
            return value
        value = float(value)
        if not math.isfinite(value) or value <= 0:
            raise ValueError(
                f"Fault.t_k_s must be a finite positive duration in seconds, got {value}."
            )
        return value

    @field_validator("n_factor")
    @classmethod
    def _validate_n_factor(cls, value):
        """Restrict the AC heat factor ``n`` to the physical range ``(0, 1]``.

        IEC 60909-0 defines ``n = 1`` for far-from-generator faults and
        ``n < 1`` when the AC component decays. ``n > 1`` has no physical
        meaning and would silently inflate ``I_th``.
        """
        value = float(value)
        if not math.isfinite(value) or not (0.0 < value <= 1.0):
            raise ValueError(
                f"Fault.n_factor must lie in (0, 1] per IEC 60909-0, got {value}."
            )
        return value

    @field_validator("scalings", mode="before")
    @classmethod
    def _coerce_scalings_keys(cls, value):
        """
        Normalise ``scalings`` keys to ``float``.

        ``Fault.scalings`` keys are looked up against
        ``network.frequencies`` (always ``float``). Without coercion a
        scaling supplied with an ``int`` key — e.g. ``{50: 1.0}`` — would
        be stored but never consulted. Worse, mixing ``int`` and
        ``float`` keys (``{50: 1.0+0j, 50.0: 0.5+0j}``) leaves both
        entries in the dict but only the ``float``-keyed one is read,
        silently masking the ``int``-keyed override. This validator
        coerces every key to ``float`` and raises ``ValueError`` on
        duplicate frequencies that would otherwise collide silently.

        The *values* are checked as well. A scaling multiplies the source
        injection, so ``nan`` propagates into every voltage of that frequency
        bin without raising anywhere, ``inf`` turns the whole result into
        ``inf``, and a negative factor reverses the fault current -- which is
        not a scaling but a different fault. Accepted, all three would only
        show up as an unserialisable network afterwards.

        ``None`` is stored as ``{}``: both mean factor 1.0 at every frequency.
        """
        if value is None:
            return {}
        if not isinstance(value, dict):
            return value
        normalised: Dict[float, float] = {}
        for key, scaling in value.items():
            try:
                f_key = float(key)
            except (TypeError, ValueError) as exc:
                raise ValueError(
                    f"Fault.scalings: key {key!r} is not convertible to float."
                ) from exc
            if f_key in normalised:
                raise ValueError(
                    f"Fault.scalings: duplicate frequency key {f_key} "
                    "after int → float coercion. Drop the duplicate "
                    "entry to make the intended scaling unambiguous."
                )
            try:
                f_scaling = float(scaling)
            except (TypeError, ValueError) as exc:
                raise ValueError(
                    f"Fault.scalings: value {scaling!r} at {f_key} Hz is not "
                    "convertible to float."
                ) from exc
            if not math.isfinite(f_scaling):
                raise ValueError(
                    f"Fault.scalings: value at {f_key} Hz must be finite, got "
                    f"{f_scaling}. A non-finite scaling propagates into every "
                    "voltage of that frequency bin without raising, and makes "
                    "the network unserialisable afterwards."
                )
            if f_scaling < 0:
                raise ValueError(
                    f"Fault.scalings: value at {f_key} Hz must be >= 0, got "
                    f"{f_scaling}. A negative factor reverses the fault "
                    "current; express that through the source phasor, not "
                    "through the scaling."
                )
            normalised[f_key] = f_scaling
        return normalised

    def scaling_at(self, frequency: float) -> float:
        """
        The factor this fault applies to the source currents at a frequency.

        Parameters
        ----------
        frequency : float
            Frequency in Hz (an ``int`` key is looked up as ``float``, the way
            the validator stores it).

        Returns
        -------
        float
            ``scalings[frequency]``, or ``1.0`` -- full contribution -- when
            the frequency has no key.

        Examples
        --------
        >>> import groundinsight as gi
        >>> fault = gi.Fault(name="F1", bus="B", scalings={50.0: 0.5})
        >>> fault.scaling_at(50.0), fault.scaling_at(250.0)
        (0.5, 1.0)
        """
        return float(self.scalings.get(float(frequency), 1.0))

    @computed_field()
    @property
    def active(self) -> bool:
        """
        Whether this fault is currently the active one in the network.

        Returns
        -------
        bool
            ``True`` while ``Network.active_fault`` names this fault.
        """
        return self._active

    def mark_active(self, value: bool) -> None:
        """
        Set the flag behind :attr:`active`.

        Internal API: used by :class:`Network`, the database loader and the
        inverse-rho searches to keep the flag in step with
        :attr:`Network.active_fault`; not exported from ``groundinsight`` and
        free to change in any release. Application code activates a fault with
        :meth:`Network.set_active_fault`.

        Parameters
        ----------
        value : bool
            Whether this fault is the active fault of its network.
        """
        self._active = value

    def __str__(self):
        return f"Fault(name={self.name}, bus={self.bus})"


class Source(_NetworkElement):
    r"""
    Represents a current or Thevenin (voltage) source within the network.

    For stationary grounding analyses the default is a current source
    with a fixed injected current per frequency
    (``source_type="current"``). This is equivalent to a Norton source
    with infinite parallel impedance and matches the conventional
    planning practice of grounding engineering, where the prospective
    fault current is treated as a constant input.

    For transient simulations the source can alternatively be expressed
    as a Thevenin equivalent (``source_type="voltage"``) with a
    frequency-dependent EMF ``voltage`` and a finite
    ``source_impedance``. In that case the grounding network sees the
    loop impedance ``Z_src + Z_loop`` and the effective fault current
    results from the solution rather than being prescribed.

    Attributes
    ----------
    name : str
        The name of the source.
    description : str, optional
        A brief description of the source.
    bus : str
        The name of the bus where the source is located.
    source_type : {'current', 'voltage'}
        ``"current"`` (default) for a classic current-source injection;
        ``"voltage"`` for a Thevenin equivalent (EMF in series with
        ``source_impedance``).
    values : dict of float to ComplexNumber, optional
        Frequency-dependent current injection, one phasor per frequency.
        Required when ``source_type == "current"`` and must be ``None``
        otherwise. **The phasors are RMS amplitudes**, not peak values --
        see the RMS convention in the Notes.
    voltage : dict of float to ComplexNumber, optional
        Frequency-dependent Thevenin EMF, one phasor per frequency, again
        as RMS amplitudes. Required when ``source_type == "voltage"`` and
        must be ``None`` otherwise.
    source_impedance : dict of float to ComplexNumber, optional
        Frequency-dependent internal impedance of the Thevenin source.
        Required when ``source_type == "voltage"`` and must be ``None``
        otherwise. Must be non-zero at every frequency in order to be
        invertible into a Norton equivalent.
    i_k_a : float, optional
        Initial symmetrical short-circuit current ``I_k''`` at the fault
        location as seen from this source, in amperes. Provenance
        metadata for the IEC 60909 characteristics — the *solve* always
        uses ``values`` / ``voltage``. Typically filled by
        :func:`groundinsight.io.apply_shortcircuit_characteristics` from
        a pandapower ``calc_sc`` run.
    r_to_x : float, optional
        Ratio ``R/X`` of the short-circuit loop feeding this source, used
        to derive ``kappa`` when the latter is not given explicitly. For a
        single line-to-earth fault the relevant loop is ``2*Z1 + Z0``, so
        ``r_to_x = (2*R1 + R0) / (2*X1 + X0)``.
    kappa : float, optional
        IEC 60909-0 peak factor ``kappa`` of this source. Takes precedence
        over ``r_to_x`` when both are set, which lets a topology-aware
        value (e.g. pandapower's method C) override the closed-form
        ``1.02 + 0.98 * exp(-3 * R/X)``. Physically bounded to ``(1, 2]``.

    Notes
    -----
    ``i_k_a``, ``r_to_x`` and ``kappa`` are *characteristic* quantities.
    They do not enter the linear solve at all: the network equations
    superpose the RMS injections in ``values`` as before. The non-linear
    IEC 60909 factors are applied afterwards, to the aggregated branch
    current, by
    :func:`groundinsight.analysis.shortcircuit.resolve_fault_sc_characteristics`.
    Superposing ``i_p`` or ``I_th`` of individual sources directly would
    be wrong; see that module's docstring for the derivation.

    **RMS convention.** Every phasor in ``values``, ``voltage`` and every
    phasor the solver derives from them is an **RMS amplitude**, not a
    peak amplitude. The convention is package-wide and it is what makes
    the RMS columns of the results correct: the total RMS of a quantity
    with several frequency components is

    .. math::

        X_{\mathrm{RMS}} = \sqrt{\sum_f |X(f)|^2}

    with the 0 Hz term entering as ``|X_dc|`` -- a DC component
    contributes its own magnitude, not ``|X_dc|/sqrt(2)``. Peak-valued
    phasors would make that sum too large by ``sqrt(2)`` for every AC
    component while leaving the DC term right, so the two conventions
    cannot be mixed. If a data source gives peak amplitudes, divide the
    AC components by ``sqrt(2)`` before filling ``values``. See
    :meth:`groundinsight.electrical_network.ElectricalNetwork._calculate_rms`.

    Examples
    --------
    >>> import groundinsight as gi
    >>> source = gi.Source(name="S1", bus="B1", values={50.0: 1000.0, 250.0: 50.0})
    >>> source.source_type, source.values[250.0]
    ('current', ComplexNumber(real=50.0, imag=0.0))
    """

    model_config = ConfigDict(
        ser_json_inf_nan="constants", validate_assignment=True
    )

    _COLLECTION: ClassVar[str] = "sources"
    _KIND: ClassVar[str] = "source"
    _BUS_REFERENCES: ClassVar[Tuple[str, ...]] = ("bus",)

    name: str
    description: Optional[str] = None
    bus: str  # Location of the source
    source_type: Literal["current", "voltage"] = "current"
    values: Optional[Dict[float, ComplexNumber]] = (
        None  # {frequency: current value} (current source)
    )
    voltage: Optional[Dict[float, ComplexNumber]] = (
        None  # {frequency: EMF value} (voltage source)
    )
    source_impedance: Optional[Dict[float, ComplexNumber]] = (
        None  # {frequency: Z_src} (voltage source)
    )
    # --- IEC 60909 characteristic quantities (metadata, not solved) ------
    i_k_a: Optional[float] = None  # initial symmetrical SC current I_k'' [A]
    r_to_x: Optional[float] = None  # R/X of the short-circuit loop [-]
    kappa: Optional[float] = None  # IEC 60909-0 peak factor [-]

    @field_validator("i_k_a")
    @classmethod
    def _validate_i_k(cls, value):
        """Reject non-positive or non-finite short-circuit currents."""
        if value is None:
            return value
        value = float(value)
        if not math.isfinite(value) or value <= 0:
            raise ValueError(
                f"Source.i_k_a must be a finite positive current in amperes, got {value}."
            )
        return value

    @field_validator("r_to_x")
    @classmethod
    def _validate_r_to_x(cls, value):
        """Reject negative or non-finite ``R/X`` ratios.

        ``R/X = 0`` is admissible (purely inductive loop, ``kappa = 2``),
        but a negative ratio would push ``kappa`` above the physical
        limit of 2.
        """
        if value is None:
            return value
        value = float(value)
        if not math.isfinite(value) or value < 0:
            raise ValueError(
                f"Source.r_to_x must be a finite non-negative ratio, got {value}."
            )
        return value

    @field_validator("kappa")
    @classmethod
    def _validate_kappa(cls, value):
        """Restrict ``kappa`` to the physical range ``(1, 2]``.

        ``kappa = 1`` means no DC offset at all (only reachable as a
        limit) and ``kappa = 2`` a non-decaying DC component. Values
        outside this band indicate a unit error or a bad import and are
        rejected early so they cannot silently inflate ``i_p``.
        """
        if value is None:
            return value
        value = float(value)
        if not math.isfinite(value) or not (1.0 < value <= 2.0):
            raise ValueError(
                f"Source.kappa must lie in (1, 2] per IEC 60909-0, got {value}."
            )
        return value

    @model_validator(mode="after")
    def _validate_source_mode(self):
        """
        Validate that the populated fields match the declared ``source_type``.

        Returns
        -------
        Source
            The validated source instance (self).

        Raises
        ------
        ValueError
            If required fields for the chosen mode are missing,
            if fields belonging to the other mode are populated, if the
            frequency keys of ``voltage`` and ``source_impedance``
            disagree, or if any ``source_impedance`` entry is zero.
        """
        if self.source_type == "current":
            if self.values is None:
                raise ValueError(
                    "Source.values must be provided when source_type='current'."
                )
            if self.voltage is not None or self.source_impedance is not None:
                raise ValueError(
                    "Source: 'voltage' and 'source_impedance' must not be set "
                    "when source_type='current'."
                )
        else:  # source_type == "voltage"
            if self.voltage is None or self.source_impedance is None:
                raise ValueError(
                    "Source.voltage and Source.source_impedance must both be "
                    "provided when source_type='voltage'."
                )
            if self.values is not None:
                raise ValueError(
                    "Source.values must not be set when source_type='voltage'."
                )
            v_keys = set(self.voltage.keys())
            z_keys = set(self.source_impedance.keys())
            if v_keys != z_keys:
                raise ValueError(
                    "Source: 'voltage' and 'source_impedance' must share the "
                    "same set of frequency keys."
                )
            for f, z in self.source_impedance.items():
                if abs(complex(z.real, z.imag)) == 0:
                    raise ValueError(
                        f"Source.source_impedance at f={f} Hz must be non-zero."
                    )
        return self

    def __str__(self):
        return f"Source(name={self.name}, bus={self.bus}, type={self.source_type})"


class ResultBus(BaseModel):
    """
    Result data for a bus after running a fault calculation.

    Three physically distinct currents meet at a grounding bus, and mixing
    them up is the classic sizing error EN 50522 / IEC 61936-1 guard
    against:

    * ``i_inj`` — the current injected into the grounding system at this
      bus by the sources (at a source bus) or drawn out of it by the fault
      (at the fault bus). It flows through a *lumped* connection, the
      **earthing conductor** (*Erdungsleiter*), which therefore has to be
      sized for the full earth-fault current. Zero at every other bus.
    * ``ia`` — the share of that current which is actually dissipated into
      the soil at this bus, ``u_EPR / Z_B``. It flows through the **earth
      electrode** (*Erder*) and is generally much smaller.
    * the branch shield currents, reported per branch on
      :class:`ResultBranch`.

    ``i_inj`` deliberately excludes the mutual Norton-equivalent injections
    of the inductively coupled branches: those model a *distributed*
    induced EMF along the line, not a current entering the node through a
    lumped conductor. The full nodal balance including them is
    ``ia = i_vector + sum_branches (u_other - u_self) * Y_self``.

    Attributes
    ----------
    name : str
        The name of the bus.
    uepr : float
        RMS earth potential rise at the bus, in volts.
    ia : float
        RMS bus current dissipated into the soil through the earth
        electrode, in amperes.
    i_inj : float
        RMS source-side injection at the bus, in amperes — the current
        carried by the earthing conductor. Defaults to ``0.0`` so that
        results stored before this field existed still validate.
    uepr_freq : dict of float to ComplexNumber
        Mapping of frequency to complex voltage values.
    ia_freq : dict of float to ComplexNumber
        Mapping of frequency to complex electrode current values.
    i_inj_freq : dict of float to ComplexNumber
        Mapping of frequency to complex injection values. Defaults to an
        empty mapping for backwards compatibility.

    Examples
    --------
    >>> import groundinsight as gi
    >>> from groundinsight.api.examples import build_minimal
    >>> bus = gi.run_fault(build_minimal(), "fault").buses[2]
    >>> bus.name, round(bus.uepr, 2), round(abs(bus.uepr_freq[50.0]), 2)
    ('bus3', 18.03, 18.03)
    """

    model_config = ConfigDict(ser_json_inf_nan="constants")

    name: str  # name of the bus
    uepr: float  # Earth potential rise
    ia: float  # Current dissipated into the soil (earth electrode)
    i_inj: float = 0.0  # Source-side injection (earthing conductor)
    uepr_freq: Dict[float, ComplexNumber]  # {frequency: voltage}
    ia_freq: Dict[float, ComplexNumber]  # {frequency: current}
    i_inj_freq: Dict[float, ComplexNumber] = {}  # {frequency: injection}

    def __str__(self):
        return f"ResultBus(name={self.name}, uepr={self.uepr})"


class ResultBranch(BaseModel):
    """
    Result data for a branch after running a fault calculation.

    Attributes
    ----------
    name : str
        The name of the branch.
    i_s : float
        RMS shield (grounding-conductor) current in the branch, in
        amperes.
    i_s_freq : dict of float to ComplexNumber
        Mapping of frequency to complex shield current values.

    Examples
    --------
    >>> import groundinsight as gi
    >>> from groundinsight.api.examples import build_minimal
    >>> branch = gi.run_fault(build_minimal(), "fault").branches[0]
    >>> branch.name, round(branch.i_s, 2)
    ('b12', 84.11)
    """

    model_config = ConfigDict(ser_json_inf_nan="constants")

    name: str  # name of the branch
    i_s: float  # Shield current
    i_s_freq: Dict[float, ComplexNumber]  # {frequency: current}

    def __str__(self):
        return f"ResultBranch(name={self.name})"


class ResultReductionFactor(BaseModel):
    """
    Reduction-factor result at the fault bus.

    Attributes
    ----------
    name : str, optional
        The name of the reduction factor result.
    fault_bus : str
        The bus where the fault occurred.
    value : dict of float to float, optional
        Mapping from frequency to the **EPR-based** reduction factor
        ``r(f) = |u_fault with mutual| / |u_fault without mutual|``. This is
        the quantity that reproduces the familiar closed form
        ``r = |1 - Z_mutual / Z_self|`` for a single shielded line. It is
        structurally independent of the impedance *at* the fault bus. ``None``
        where the denominator is rounding noise -- below ``1e-9`` of the EPR
        the fault current alone would cause at the fault bus,
        ``|I_F| * |Z_FF|`` -- with a ``ModelAssumptionWarning``
        ``REDUCTION_FACTOR_UNDEFINED`` (P9).
    value_current : dict of float to float, optional
        Mapping from frequency to the **current-based** reduction factor
        ``r_I(f) = |I_E| / |I_F|`` -- the measured definition. ``I_E`` is the
        **sum of the electrode currents of every bus that feeds the soil**, not
        the electrode current at the faulted station: where the stations are
        bonded through continuous cable shields the current spreads along them
        and leaks into the soil at every one of them. Taking only the faulted
        station understates it by a factor of 1.8 to 4.7 on the verification
        feeder, depending on where the fault sits. Unlike ``value`` this factor
        responds to the fault-bus characteristic, which is what makes it usable
        as the ordinate of a rho-f sensitivity study. ``None`` per frequency
        where the frequency carries no fault current, and where no electrode
        carries more than ``1e-9`` of the fault current -- the current then
        returns through the shields and ``I_E`` is zero to within rounding;
        the latter comes with a ``ModelAssumptionWarning``
        ``EARTH_GROUP_UNDEFINED`` (P9).
    i_earth : dict of float to complex, optional
        The earth-return current ``I_E`` itself, per frequency, so a study can
        read the ampere value rather than only the ratio. Electrode currents
        at or below ``1e-9`` of the largest one are rounding noise and join
        neither group; ``None`` where ``value_current`` is ``None``.
    earth_buses : dict of float to list of str, optional
        The buses counted as feeding the soil at each frequency -- from the
        fault outwards, in every direction, up to where the potential profile
        turns. Reported because the split is a modelling statement and should
        be inspectable; see :mod:`groundinsight.utils.earth_current`.
    i_fault : dict of float to complex, optional
        The fault current ``I_F = 3*I_0`` the current-based factor divides by:
        ``sum(scaling * I_src)`` over the sources that reach the fault, a
        source on the fault bus included. For a Thevenin source this is its
        Norton current ``E / Z_src``. ``None`` where no
        source has a value.
    u_earthing : dict of float to complex, optional
        The earthing voltage ``U_E`` of EN 50522: the **EPR at the fault bus**,
        in the package's sign convention -- the same complex number as
        ``ResultBus.uepr_freq`` of the fault bus. This is the voltage the
        touch-voltage assessment starts from; in the norm's ideal case (a
        fault far from the infeed on a long bonded run) it equals
        ``r * |I_F| * |Z_E|`` with ``r = |1 - Z_m/Z_s|`` (``value``).
    z_earthing : dict of float to complex, optional
        The earthing impedance ``Z_E`` of EN 50522: the **driving-point
        impedance of the earthing network at the fault bus** -- the voltage one
        ampere injected at the fault bus raises it to against remote earth,
        with every electrode and shield in place, without the inductive
        coupling and without the Thevenin loop closures of voltage sources
        (those belong to the fault loop, not to the earthing system). It is
        what an earthing-impedance measurement at the station would see,
        including the stations bonded to it through cable shields, and on a
        long homogeneous run it tends to the ladder limit
        ``-Z'/2 + sqrt(Z'^2/4 + Z_e*Z')``. Equal to
        ``ResultGroundingImpedance.value``. ``None`` only when the fault bus
        reaches remote earth through nothing but a Thevenin loop -- no bus
        connected to it through shields carries an electrode -- which is
        decided by that structure, not by the solver
        (``EARTHING_IMPEDANCE_UNDEFINED``).
    u_earthing_group : dict of float to complex, optional
        Diagnostic: the current-weighted mean potential of the earth group
        ``earth_buses``. On a long bonded run it lies 43 % below the fault-bus
        EPR, because the group reaches into the decaying tail of the potential
        profile.
    z_earthing_group : dict of float to complex, optional
        Diagnostic: ``u_earthing_group / i_earth``. The chain
        ``u_earthing_group = I_F * z_earthing_group * value_current`` closes
        on it by construction, which is why it is no check of anything.

    Notes
    -----
    **Fault-bus quantities, not group means.** ``u_earthing`` and
    ``z_earthing`` are taken at the fault bus. The group means come out
    43 % / 45 % below the EPR and the driving-point impedance at the fault in
    the norm's ideal case -- unconservative if taken as ``U_E`` for a
    touch-voltage assessment -- and are kept as the ``*_group`` diagnostics
    only.

    **Stored results in the group format.** A record without ``i_fault``,
    ``u_earthing_group`` and ``z_earthing_group`` holds the group means in
    ``u_earthing`` / ``z_earthing``. It still loads: its values are moved to
    the ``*_group`` fields and ``u_earthing`` / ``z_earthing`` stay empty
    until the fault is solved again, so no stored number claims a meaning it
    was not computed with.
    """

    model_config = ConfigDict(ser_json_inf_nan="constants")

    name: Optional[str] = None  # Make name optional with a default value
    fault_bus: str
    value: Dict[float, Optional[float]]  # Mapping from frequency to reduction factor
    value_current: Dict[float, Optional[float]] = {}
    i_fault: Dict[float, Optional[ComplexNumber]] = {}
    i_earth: Dict[float, Optional[ComplexNumber]] = {}
    earth_buses: Dict[float, List[str]] = {}
    u_earthing: Dict[float, Optional[ComplexNumber]] = {}
    z_earthing: Dict[float, Optional[ComplexNumber]] = {}
    u_earthing_group: Dict[float, Optional[ComplexNumber]] = {}
    z_earthing_group: Dict[float, Optional[ComplexNumber]] = {}

    @model_validator(mode="before")
    @classmethod
    def _relocate_pre_f4_earthing(cls, data):
        """
        Move the group quantities of a group-format record to the ``*_group`` fields.

        Parameters
        ----------
        data : Any
            The raw input. Only a mapping without any of ``i_fault``,
            ``u_earthing_group`` and ``z_earthing_group`` is touched: the group
            format, in which ``u_earthing`` / ``z_earthing`` held the group
            mean. A model dump of this version always carries all three.

        Returns
        -------
        Any
            The input, with ``u_earthing`` / ``z_earthing`` renamed to
            ``u_earthing_group`` / ``z_earthing_group`` for a group-format
            record.
        """
        if isinstance(data, dict) and _is_pre_f4_reduction_factor(data):
            data = dict(data)
            data["u_earthing_group"] = data.pop("u_earthing", None) or {}
            data["z_earthing_group"] = data.pop("z_earthing", None) or {}
        return data

    def __str__(self):
        # The field is called ``value``. A wrong attribute name here would
        # raise AttributeError from str() / print() / f-strings only:
        # pydantic's __repr__ keeps working, so a bare cell in a notebook
        # would look fine.
        return (
            f"ResultReductionFactor(name={self.name}, "
            f"fault_bus={self.fault_bus}, value={self.value}, "
            f"value_current={self.value_current})"
        )


class ResultGroundingImpedance(BaseModel):
    """
    Grounding impedance result at the fault bus.

    Attributes
    ----------
    name : str, optional
        The name of the grounding impedance result.
    fault_bus : str
        The bus where the fault occurred.
    value : dict of float to ComplexNumber, optional
        Mapping from frequency to the grounding impedance
        ``Z_G(f) = Z_E(f) = Z_dp(F)``: the driving-point impedance of the
        earthing network at the fault bus, identical to
        ``ResultReductionFactor.z_earthing`` (see there).
    """

    model_config = ConfigDict(ser_json_inf_nan="constants")

    name: Optional[str] = None  # Make name optional with a default value
    fault_bus: str
    value: Dict[
        float, Optional[ComplexNumber]
    ]  # Mapping from frequency to grounding impedance

    def __str__(self):
        # See ResultReductionFactor.__str__ -- same defect, same cause: the
        # field is called ``value``, not ``grounding_impedance``.
        return (
            f"ResultGroundingImpedance(name={self.name}, "
            f"fault_bus={self.fault_bus}, value={self.value})"
        )


class Result(BaseModel):
    """
    Overall result of a single fault calculation.

    Attributes
    ----------
    buses : list of ResultBus
        Per-bus results.
    branches : list of ResultBranch
        Per-branch results.
    reduction_factor : ResultReductionFactor, optional
        The reduction factor result, if available.
    grounding_impedance : ResultGroundingImpedance, optional
        The grounding impedance result, if available.
    fault : str
        The name of the fault that was active during the calculation.
    diagnostics : list of Diagnostic
        Every package warning raised while this result was computed, with
        its code and the elements it concerns, in the order raised and
        without exact repetitions. Filled by :func:`groundinsight.run_fault`
        independently of the caller's warning filters, so a program (or a
        GUI) can read the conditions that qualify the result from the result
        itself. See :mod:`groundinsight.diagnostics`.
    input_digest : str, optional
        Digest of the inputs this result was computed from -- the network
        frequencies, every bus, branch and source as the solver reads them,
        and the definition of this result's fault. Written by
        :func:`groundinsight.run_fault`; :meth:`Network.stale_results`
        compares it with the network's current inputs. ``None`` for results
        without a digest -- stored in an earlier file format, or assembled by
        hand -- which are never reported stale (their provenance is unknown).

    Examples
    --------
    >>> import groundinsight as gi
    >>> from groundinsight.api.examples import build_minimal
    >>> result = gi.run_fault(build_minimal(), "fault")
    >>> [bus.name for bus in result.buses], result.diagnostics
    (['bus1', 'bus2', 'bus3'], [])
    >>> round(result.reduction_factor.value_current[50.0], 4)
    0.1803
    """

    model_config = ConfigDict(ser_json_inf_nan="constants")

    buses: List[ResultBus] = []
    branches: List[ResultBranch] = []
    reduction_factor: Optional[ResultReductionFactor] = None
    grounding_impedance: Optional[ResultGroundingImpedance] = None
    fault: str = ""  # name of the fault that was active
    diagnostics: List[Diagnostic] = []
    input_digest: Optional[str] = None

    @model_validator(mode="before")
    @classmethod
    def _flag_pre_f4_result(cls, data):
        """
        Keep a result in the group format from claiming the fault-bus meaning.

        In that format the grounding impedance is the group quantity that
        :class:`ResultReductionFactor` reports as ``z_earthing_group``. For
        such a record --
        recognised on its stored reduction factor, see
        :meth:`ResultReductionFactor._relocate_pre_f4_earthing` -- the stored
        grounding impedance is dropped (its value survives in
        ``z_earthing_group``) and an ``info`` diagnostic
        ``LEGACY_EARTHING_DEFINITION`` says why ``U_E``, ``Z_E`` and ``Z_G``
        are empty until the fault is solved again.

        Parameters
        ----------
        data : Any
            The raw input; only a mapping is touched.

        Returns
        -------
        Any
            The input, adjusted for a group-format record.
        """
        if not isinstance(data, dict):
            return data
        factor = data.get("reduction_factor")
        if not (isinstance(factor, dict) and _is_pre_f4_reduction_factor(factor)):
            return data
        grounding = data.get("grounding_impedance")
        stored_group = bool(factor.get("u_earthing") or factor.get("z_earthing"))
        stored_impedance = isinstance(grounding, dict) and bool(grounding.get("value"))
        if not (stored_group or stored_impedance):
            return data
        data = dict(data)
        if stored_impedance:
            grounding = dict(grounding)
            grounding["value"] = {}
            data["grounding_impedance"] = grounding
        diagnostics = list(data.get("diagnostics") or [])
        diagnostics.append(
            {
                "code": "LEGACY_EARTHING_DEFINITION",
                "severity": "info",
                "category": "InputWarning",
                "message": (
                    "This result was stored before U_E and Z_E were redefined "
                    "as the EN 50522 quantities at the fault bus (U_E = EPR of "
                    "the fault bus, Z_E = driving-point impedance of the "
                    "earthing network there). Its stored group quantities were "
                    "moved to u_earthing_group / z_earthing_group; u_earthing, "
                    "z_earthing and the grounding impedance stay empty until "
                    "the fault is solved again with run_fault."
                ),
                "elements": [("fault", str(data.get("fault") or ""))],
            }
        )
        data["diagnostics"] = diagnostics
        return data

    def __str__(self):
        return f"Result(buses={len(self.buses)}, branches={len(self.branches)})"


def _is_pre_f4_reduction_factor(data: dict) -> bool:
    """
    Recognise a stored reduction-factor record in the group format.

    Parameters
    ----------
    data : dict
        Raw input of :class:`ResultReductionFactor`.

    Returns
    -------
    bool
        ``True`` if none of ``i_fault``, ``u_earthing_group`` and
        ``z_earthing_group`` is present -- a model dump of this version always
        carries all three.
    """
    return not any(
        key in data for key in ("i_fault", "u_earthing_group", "z_earthing_group")
    )


class Path(BaseModel):
    """
    Ordered branch list connecting a source bus to a fault bus.

    Attributes
    ----------
    name : str
        The name of the path.
    description : str, optional
        A brief description of the path.
    source : str
        The name of the source at the start of the path.
    fault : str
        The name of the fault at the end of the path.
    segments : list of Branch
        Ordered list of branches that make up the path, traversed from
        ``source`` to ``fault``.

    Examples
    --------
    >>> import groundinsight as gi
    >>> from groundinsight.api.examples import build_minimal
    >>> net = build_minimal()
    >>> gi.create_paths(net)
    >>> path = next(iter(net.paths.values()))
    >>> path.source, path.fault, [segment.name for segment in path.segments]
    ('src', 'fault', ['b12', 'b23'])
    """

    model_config = ConfigDict(ser_json_inf_nan="constants")

    name: str
    description: Optional[str] = None
    source: str
    fault: str
    segments: List[Branch] = []

    def __str__(self):
        return f"Path(name={self.name}, source={self.source}, fault={self.fault})"


#: Version tag of :attr:`Result.input_digest`. A digest with another tag was
#: computed over a different set of inputs and is treated like no digest.
_DIGEST_SCHEME = "gi1"

#: The fields a stored result depends on, per model: what
#: :meth:`Network._input_digest_common` and :meth:`Network.input_digest`
#: read. ``type`` of a branch stands for ``type.grounding_conductor``, the only
#: type attribute the solver reads directly.
_DIGEST_FIELDS: Dict[str, Tuple[str, ...]] = {
    "Bus": ("name", "active", "impedance"),
    "Branch": (
        "name", "active", "from_bus", "to_bus", "type", "parallel_coefficient",
        "self_impedance", "mutual_impedance", "phase_impedance",
    ),
    "Source": ("name", "bus", "source_type", "values", "voltage", "source_impedance"),
    "Fault": ("name", "bus", "scalings"),
    "Network": ("frequencies", "buses", "branches", "sources", "faults"),
}

#: The fields a stored result does *not* read, with the reason. A test keeps
#: the two tables complete, so a new field has to be classified before it
#: can be added -- a field missing from the digest would let a result stay
#: "current" after a change that alters it.
_DIGEST_EXEMPT_FIELDS: Dict[str, Dict[str, str]] = {
    "Bus": {
        "description": "metadata",
        "type": "enters through the derived impedance",
        "specific_earth_resistance": "enters through the derived impedance",
        "R": "transient solvers only",
        "L": "transient solvers only",
        "C": "transient solvers only",
    },
    "Branch": {
        "description": "metadata",
        "length": "enters through the derived impedances",
        "specific_earth_resistance": "enters through the derived impedances",
        "R_self": "transient solvers only",
        "L_self": "transient solvers only",
        "C_self": "transient solvers only",
        "R_mutual": "transient solvers only",
        "M_mutual": "transient solvers only",
    },
    "Source": {
        "description": "metadata",
        "i_k_a": "IEC 60909 characteristic, applied after the solve",
        "r_to_x": "IEC 60909 characteristic, applied after the solve",
        "kappa": "IEC 60909 characteristic, applied after the solve",
    },
    "Fault": {
        "description": "metadata",
        "t_k_s": "thermal checks only, applied after the solve",
        "n_factor": "thermal checks only, applied after the solve",
    },
    "Network": {
        "name": "metadata",
        "description": "metadata",
        "results": "the outputs themselves",
        "paths": "derived from the topology, rebuilt when it changes",
        "active_fault": "set by run_fault to the fault it solves",
    },
}


def _impedance_record(
    mapping: Optional[Dict[float, Any]], strings: List[str], floats: List[float]
) -> None:
    """
    Append one frequency-keyed mapping to the digest buffers.

    Parameters
    ----------
    mapping : Optional[Dict[float, Any]]
        ``{frequency: ComplexNumber}`` (or ``{frequency: float}``), or
        ``None``.
    strings : List[str]
        Receives the structure (``"~"`` for ``None``, else the entry count).
    floats : List[float]
        Receives frequency, real and imaginary part per entry, sorted by
        frequency; ``+ 0.0`` folds ``-0.0`` into ``0.0``.
    """
    if mapping is None:
        strings.append("~")
        return
    strings.append(str(len(mapping)))
    for freq in sorted(mapping):
        value = mapping[freq]
        if isinstance(value, ComplexNumber):
            floats.extend((freq + 0.0, value.real + 0.0, value.imag + 0.0))
        else:
            floats.extend((freq + 0.0, float(value) + 0.0, 0.0))


def _digest_update(digest: "hashlib._Hash", strings: List[str], floats: List[float]) -> None:
    """
    Feed the digest buffers into ``digest`` in a platform-independent form.

    Parameters
    ----------
    digest : hashlib._Hash
        The running digest.
    strings : List[str]
        Names and structure markers, joined by a unit separator.
    floats : List[float]
        Packed as little-endian IEEE 754 doubles.
    """
    digest.update("\x1f".join(strings).encode("utf-8", "surrogatepass"))
    packed = array("d", floats)
    if sys.byteorder == "big":  # pragma: no cover - no big-endian CI
        packed.byteswap()
    digest.update(packed.tobytes())


def validate_frequencies(value: List[float], *, stacklevel: int = 1) -> List[float]:
    """Reject empty / duplicate / non-finite / negative frequency lists.

    Internal API: the rules of :attr:`Network.frequencies`, used by its field
    validator and by :func:`groundinsight.check_network`, which re-checks a
    list edited in place; not exported from ``groundinsight`` and free to
    change in any release.

    ``f = 0`` (DC) is **permitted** because the FFT transient solver
    in :mod:`groundinsight.simulation.transient` uses the
    zero-frequency bin to carry the steady-state offset.

    Non-strictly-monotone-increasing inputs are accepted but trigger
    a :class:`NetworkFrequencyOrderWarning` -- the transient solver
    relies on the *order* of ``Network.frequencies`` to map to the
    FFT spectral bins, so a shuffled or descending list almost
    always indicates a user error.

    Parameters
    ----------
    value : list of float
        The frequencies in Hz.
    stacklevel : int, default 1
        Where the order warning points, counted from the caller of this
        function: ``1`` is the caller itself, ``2`` its caller, and so on.

    Returns
    -------
    list of float
        ``value``, unchanged.

    Raises
    ------
    ValueError
        If the list is empty or holds a non-finite, negative or repeated
        frequency.

    Warns
    -----
    NetworkFrequencyOrderWarning
        If the list is not strictly increasing.

    Notes
    -----
    A duplicate frequency (e.g. ``[50.0, 50.0]``) silently doubles
    the work in :func:`solve_network` *and* doubles the amplitude
    of the corresponding spectral bin in the FFT transient solver.
    ``nan`` / ``inf`` / ``< 0`` would propagate into the
    impedance evaluation and surface as opaque
    ``ValueError: domain error`` deep inside
    :func:`utils.impedance_calculator.compute_impedance`.
    Raising eagerly at construction time turns those silent
    wrong-result bugs into a clear ``ValueError`` at the API
    boundary; the order-warning makes the order-dependent FFT
    pitfall observable.

    Mirrors :class:`groundfield.solver.engine.EngineFrequencyOrderWarning`
    in the sister ``groundfield`` package so the three
    earthing-platform packages share one convention.
    """
    if not value:
        raise ValueError(
            "Network.frequencies must not be empty — at least one "
            "frequency is required for the steady-state solve."
        )
    for f in value:
        if not np.isfinite(f):
            raise ValueError(
                f"Network.frequencies contains a non-finite value: {f!r}."
            )
        if f < 0:
            raise ValueError(
                f"Network.frequencies must be >= 0; got {f!r} "
                "(``f == 0`` for the DC bin is permitted)."
            )
    if len(set(value)) != len(value):
        raise ValueError(
            f"Network.frequencies contains duplicates: {value!r}. "
            "Each frequency may appear at most once."
        )
    # Non-strictly-monotone-increasing input: warn but keep the
    # user's order so a deliberate descending sweep is still
    # possible. The FFT transient solver maps spectral bins by
    # *position* in this list — a shuffled list almost always
    # indicates a user error.
    if any(value[i] >= value[i + 1] for i in range(len(value) - 1)):
        warnings.warn(
            "Network.frequencies is not strictly increasing "
            f"({value!r}). The FFT transient solver maps spectral "
            "bins by position; consider sorting the list with "
            "``sorted(frequencies)`` unless a non-monotone order "
            "is intentional.",
            NetworkFrequencyOrderWarning,
            stacklevel=stacklevel + 1,
        )
    return value


class Network(ValidatedAssignmentModel):
    """
    Top-level container for an entire grounding network.

    Holds every physical element (buses, branches, sources, faults), the
    enumerated source-to-fault paths, the per-fault result objects and a
    private :class:`ElectricalNetwork` helper that owns the numerical
    working arrays.

    Attributes
    ----------
    name : str
        The name of the network.
    description : str, optional
        A brief description of the network.
    frequencies : list of float
        Frequencies (in Hz) used in calculations.
    buses : dict of str to Bus
        Buses keyed by name.
    branches : dict of str to Branch
        Branches keyed by name.
    faults : dict of str to Fault
        Faults keyed by name.
    sources : dict of str to Source
        Sources keyed by name.
    results : dict of str to Result
        Per-fault calculation results keyed by fault name.
    paths : dict of str to Path
        Source-to-fault paths keyed by path name.
    active_fault : str, optional
        Name of the currently active fault.

    Notes
    -----
    **Editing.** Assigning an input of an element that belongs to the
    network -- ``Bus.specific_earth_resistance``/``type``,
    ``Branch.length``/``specific_earth_resistance``/``type`` -- re-derives
    its impedances at once, and assigning :attr:`frequencies` re-derives
    every element; a failing derivation leaves everything unchanged and
    raises. :meth:`update_bus` and :meth:`update_branch` change several
    fields at once (all or nothing); the ``remove_*`` methods refuse to
    leave a dangling reference. Renaming an element that belongs to the
    network is refused -- remove it and add a renamed copy. An element
    belongs to one network at a time.

    **Stale results.** :meth:`stale_results` lists the faults whose stored
    result no longer matches the network's inputs; :meth:`is_result_stale`
    asks for one fault. A result is stale when the frequencies, a bus, a
    branch or a source changed as the solver sees them, or the definition of
    its own fault -- adding or editing *another* fault leaves it current,
    and so do metadata such as ``description`` or ``Fault.t_k_s``. The
    comparison is by content (see :attr:`Result.input_digest`): restoring
    an input restores the result's status, which is how ``outage_context``
    and the studies leave the flags as they found them.

    Examples
    --------
    >>> import groundinsight as gi
    >>> import polars as pl
    >>> from groundinsight.api.examples import build_minimal
    >>> net = build_minimal()
    >>> sorted(net.buses), list(net.faults)
    (['bus1', 'bus2', 'bus3'], ['fault'])
    >>> _ = gi.run_fault(net, "fault")
    >>> net.stale_results()
    []
    >>> rms = net.res_fault("fault").filter(pl.col("is_rms"))
    >>> round(rms["EPR_fault_V"].item(), 2)
    18.03
    """

    model_config = ConfigDict(
        ser_json_inf_nan="constants", validate_assignment=True
    )

    name: str
    description: Optional[str] = None
    frequencies: List[float]
    buses: Dict[str, Bus] = {}
    branches: Dict[str, Branch] = {}
    faults: Dict[str, Fault] = {}
    sources: Dict[str, Source] = {}
    results: Dict[str, Result] = {}  # Stores results per fault
    paths: Dict[str, Path] = {}
    active_fault: Optional[str] = None  # Name of the active fault
    _electrical_network: Optional["ElectricalNetwork"] = PrivateAttr(default=None)
    _paths_fingerprint: Optional[tuple] = PrivateAttr(default=None)
    # Runtime configuration and memo of the path search (see
    # groundinsight.pathfinder): not fields, so never saved; the memo is
    # rebuilt by every copy and compares equal to any other.
    _pathfinder_settings: Optional[Any] = PrivateAttr(default=None)
    _pathfinder_cache: Optional[Any] = PrivateAttr(default=None)

    @field_validator("frequencies", mode="after")
    @classmethod
    def _validate_frequencies(cls, value: List[float]) -> List[float]:
        """Apply :func:`validate_frequencies` on construction and assignment."""
        # Counted from this validator: pydantic's __init__ is the next frame,
        # the code that builds the network the one after.
        return validate_frequencies(value, stacklevel=3)

    @model_validator(mode="after")
    def _sync_active_fault_flag(self):
        """Re-sync each fault's ``_active`` flag with ``active_fault``.

        ``Fault.active`` is a read-only computed field, so a JSON round-trip
        (``model_validate_json``) restores ``active_fault`` but leaves every
        ``Fault._active`` at ``False``. This after-validator re-establishes the
        invariant on construction so the JSON and SQLite load paths agree.
        """
        if self.active_fault is not None and self.active_fault in self.faults:
            for _name, _fault in self.faults.items():
                _fault.mark_active(_name == self.active_fault)
        self._adopt_loaded_paths()
        return self

    def _adopt_loaded_paths(self) -> None:
        """Trust restored paths when they still match the restored topology.

        ``Path.segments`` is persisted in full -- 37 % of the JSON of a solved
        network -- and was then thrown away by the first
        :func:`~groundinsight.run_fault`: ``_paths_fingerprint`` is a
        ``PrivateAttr`` and is not serialised, so
        :meth:`needs_path_rebuild` saw ``None`` and re-enumerated the paths
        from scratch. The stored data was pure overhead, twice over -- it cost
        a second ``Branch`` validation per segment on load and bought nothing.

        Recomputing the fingerprint here closes that. It is not a guess: the
        fingerprint is a function of the network's *own* stored state, so if
        the paths were built from this topology the value is exactly the one
        :meth:`define_paths` recorded when they were. The paths are only
        adopted after checking that every segment still names a branch of this
        network with the same endpoints -- a hand-edited file that dropped a
        branch keeps rebuilding, as it must.

        The alternative -- persisting segments as *names* instead of full
        ``Branch`` copies -- would shrink the file further but changes the
        stored format of every existing ``.json`` and ``.db``, and
        ``Path.segments`` is public API (the solver walks the ``Branch``
        objects). That is a separate, breaking change; this one is not.
        """
        if not self.paths or self._paths_fingerprint is not None:
            return
        for path in self.paths.values():
            for segment in path.segments:
                current = self.branches.get(segment.name)
                if current is None:
                    return
                if (current.from_bus, current.to_bus) != (
                    segment.from_bus,
                    segment.to_bus,
                ):
                    return
        self._paths_fingerprint = self._active_topology_fingerprint()

    def model_post_init(self, __context: Any) -> None:
        """
        Attach every element to this network after construction.

        Runs for every way a network is built -- the constructor, JSON and
        the database -- so that an element knows which network re-derives
        it (see :class:`_NetworkLink`).

        Parameters
        ----------
        __context : Any
            Pydantic's validation context (unused).
        """
        self._link_elements()

    def __setstate__(self, state: Dict[Any, Any]) -> None:
        """
        Restore a pickled network and re-attach its elements.

        The element links are not pickled (they hold a weak reference), so
        the elements of an unpickled network are attached here.

        Parameters
        ----------
        state : Dict[Any, Any]
            The state produced by ``BaseModel.__getstate__``.
        """
        super().__setstate__(state)
        self._link_elements()

    def _link_elements(self) -> None:
        """Point every element held by this network at it."""
        link = _NetworkLink(self)
        for collection in (self.buses, self.branches, self.sources, self.faults):
            for element in collection.values():
                private = getattr(element, "__pydantic_private__", None)
                if private is not None:
                    private["_network_link"] = link

    def _claim(self, element: _NetworkElement) -> None:
        """
        Refuse an element that already belongs to another network.

        Parameters
        ----------
        element : _NetworkElement
            The bus, branch, source or fault about to be added.

        Raises
        ------
        ValueError
            If the element is held by another network under its name.
        """
        owner = element._owning_network()
        if owner is not None and owner is not self:
            raise ValueError(
                f"{element._KIND.capitalize()} '{element.name}' already belongs "
                f"to network '{owner.name}'. An element belongs to one network "
                "at a time -- its impedances are derived for that network's "
                "frequencies and its edits re-derive it there. Add a copy: "
                "element.model_copy(deep=True), or remove it from the other "
                "network first."
            )

    def _adopt(self, element: _NetworkElement) -> None:
        """Link an element that has just been inserted into this network."""
        element.__pydantic_private__["_network_link"] = _NetworkLink(self)

    def _after_field_assignment(self, name: str) -> None:
        """
        Re-derive on a new frequency list; re-attach on a new element dict.

        Parameters
        ----------
        name : str
            The field that was assigned.

        Raises
        ------
        ValueError
            If an element cannot be derived at the new frequencies. Nothing
            has been re-derived then, and the base class restores the old
            frequency list.
        """
        if name == "frequencies":
            self._rederive_all()
        elif name in ("buses", "branches", "sources", "faults"):
            self._link_elements()

    def _rederive_all(self) -> None:
        """
        Re-derive every bus and branch at :attr:`frequencies`, all or nothing.

        Raises
        ------
        ValueError
            If any element cannot be derived; no element has been changed.
        """
        pending = [
            (element, element._derive(self.frequencies))
            for collection in (self.buses, self.branches)
            for element in collection.values()
        ]
        for element, derived in pending:
            element._apply_derived(derived)

    @property
    def electrical_network(self):
        """
        The solver workspace of the last build, or ``None``.

        Returns
        -------
        ElectricalNetwork or None
            The :class:`~groundinsight.electrical_network.ElectricalNetwork`
            that :func:`~groundinsight.build_electrical_network` (and so
            :func:`~groundinsight.run_fault`) attached; never saved with the
            network.
        """
        return self._electrical_network

    @electrical_network.setter
    def electrical_network(self, value):
        """
        Attach a solver workspace (the getter's docstring is the public one).

        Parameters
        ----------
        value : ElectricalNetwork or None
            The workspace, or ``None`` to drop it.
        """
        self._electrical_network = value

    @property
    def pathfinder_settings(self) -> "PathfinderSettings":
        """
        Settings of the path search on this network.

        Runtime configuration, not part of the saved model: a copy keeps it,
        a file or a database does not. Without settings of its own the
        network uses :func:`groundinsight.pathfinder.default_pathfinder_settings`,
        which only the deprecated module-level switches change. Assign
        ``None`` to return to the defaults.

        Returns
        -------
        PathfinderSettings
            The network's settings, or the process-wide defaults.

        Raises
        ------
        TypeError
            On assignment of anything but a ``PathfinderSettings`` or ``None``.

        Examples
        --------
        >>> import groundinsight as gi
        >>> net = gi.create_network(name="grid", frequencies=[50.0])
        >>> net.pathfinder_settings = gi.PathfinderSettings(max_paths=10**6)
        >>> net.pathfinder_settings.max_paths
        1000000
        """
        from groundinsight.pathfinder import default_pathfinder_settings

        settings = self._pathfinder_settings
        return settings if settings is not None else default_pathfinder_settings()

    @pathfinder_settings.setter
    def pathfinder_settings(self, value: Optional["PathfinderSettings"]) -> None:
        """
        Set the path-search settings (the getter's docstring is the public one).

        Parameters
        ----------
        value : PathfinderSettings or None
            The settings; ``None`` returns to the process-wide default.

        Raises
        ------
        TypeError
            If ``value`` is neither a ``PathfinderSettings`` nor ``None``.
        """
        from groundinsight.pathfinder import PathfinderSettings

        if value is not None and not isinstance(value, PathfinderSettings):
            raise TypeError(
                "Network.pathfinder_settings takes a "
                "groundinsight.PathfinderSettings or None, got "
                f"{type(value).__name__}."
            )
        self._pathfinder_settings = value

    def path_cache(self) -> "PathfinderCache":
        """
        The path-search memo of this network.

        Internal API: used by :class:`groundinsight.pathfinder.PathFinder`, so
        that no other module needs this network's private attributes; not
        exported from ``groundinsight`` and free to change in any release.

        The memo is created on first use. A shallow copy shares its original's
        private attributes, so the cache is checked to belong to *this*
        network and replaced by a fresh one otherwise.

        Returns
        -------
        PathfinderCache
            The memo, owned by this network.
        """
        from groundinsight.pathfinder import PathfinderCache

        cache = self._pathfinder_cache
        if cache is None or not cache.belongs_to(self):
            cache = PathfinderCache(self)
            self._pathfinder_cache = cache
        return cache

    @renamed_parameters(fault_name="fault")
    def set_active_fault(self, fault: str, keep_results: bool = False):
        """Set the specified fault as active and deactivate all other faults.

        Parameters
        ----------
        fault : str
            The name of the fault to activate. The former keyword
            ``fault_name`` is accepted as a deprecated alias.
        keep_results : bool, default ``False``
            If ``False`` (the default), any cached
            :class:`Result` for ``fault`` is dropped so the next
            solve starts from a clean slate. If ``True``, the cached
            result is preserved — useful when re-using the same
            network to plot a previous solve without recomputing it.

        Raises
        ------
        ValueError
            If the specified fault does not exist in the network.
        """
        if fault not in self.faults:
            raise ValueError(f"Fault '{fault}' does not exist in the network.")

        # Deactivate all faults
        for other in self.faults.values():
            other.mark_active(False)

        # Activate the specified fault
        self.faults[fault].mark_active(True)
        self.active_fault = fault

        # Clear previous results for the fault unless the caller
        # explicitly asks us to keep them.
        if not keep_results and fault in self.results:
            del self.results[fault]

    def invalidate_paths(self) -> None:
        """Drop the cached pathfinder results *for this network*.

        Rebinds ``self.paths`` to a fresh empty dictionary (atomic) and
        clears this network's path-search memo (:meth:`path_cache`).
        **Other networks keep theirs** -- every network has its own memo.

        The rebind is atomic (``self.paths = {}``, not an in-place
        ``self.paths.clear()``): a mapping a caller saved with
        ``saved = dict(network.paths)`` before the call keeps its entries.
        :func:`groundinsight.analysis.inverse_rho_f.evaluate_max_epr_under_k`
        follows the same pattern.

        Call this whenever the user has flipped ``Bus.active`` /
        ``Branch.active`` flags or added / removed branches outside of
        a context manager that performs its own rollback.
        """
        # Atomic rebind so external snapshots survive.
        self.paths = {}
        self.path_cache().clear()

    def define_paths(self):
        """
        Identifies all paths from all sources to all faults in the network and adds them to the network's paths.

        This method utilizes the `PathFinder` to locate paths and ensures that each path is unique
        before adding it to the network.
        """
        from groundinsight.pathfinder import PathFinder  # Import locally

        pathfinder = PathFinder(self)
        path_counter = 1  # To create unique path names
        seen_paths = set()  # To track unique paths

        for source_name, source in self.sources.items():
            source_bus_name = source.bus
            for fault_name, fault in self.faults.items():
                fault_bus_name = fault.bus
                # Find all paths between this source and fault
                paths = pathfinder.find_paths(source_bus_name, fault_bus_name)
                for path in paths:
                    # Create a hashable representation of the path to check for duplicates
                    path_signature = (
                        source_name,
                        fault_name,
                        tuple(branch.name for branch in path.segments),
                    )
                    if path_signature not in seen_paths:
                        seen_paths.add(path_signature)
                        # Assign a unique name to each path
                        path.name = f"path_{path_counter}"
                        path.description = f"Path from {source_name} to {fault_name}"
                        path.source = source_name
                        path.fault = fault_name
                        path_counter += 1
                        # Add the path to the network
                        self.add_path(path)

        # Record the active-topology fingerprint the paths were built for so
        # ``run_fault`` can detect stale paths after an in-place ``active``
        # flip or a rewiring and rebuild them instead of silently reusing them.
        self._paths_fingerprint = self._active_topology_fingerprint()

    @renamed_parameters(fault_name="fault")
    def sources_reaching(self, fault: str) -> List[str]:
        """
        Names of the sources that are connected to a fault over active elements.

        The stationary solver in the default ``phase_current_mode="auto"``
        needs to know *which* sources feed a fault, not *how* they get there.
        This method answers that with one breadth-first search per source
        over the active bus/branch sub-graph, in ``O(V + E)``, instead of
        enumerating every simple path -- a count that is exponential in the
        number of loops (1 262 816 paths and about a minute on a 6x6 grid,
        against milliseconds in the solver itself).

        The result is identical to the set of sources that appear in
        ``self.paths`` for this fault after a full :meth:`define_paths`,
        because both traverse the same active sub-graph.

        Parameters
        ----------
        fault : str
            Name of the fault to test reachability against. The former
            keyword ``fault_name`` is accepted as a deprecated alias.

        Returns
        -------
        List[str]
            Source names in the insertion order of ``self.sources``.

        Raises
        ------
        ValueError
            If ``fault`` is not a fault of this network.

        Examples
        --------
        >>> network.sources_reaching("Fault1")  # doctest: +SKIP
        ['Source1']
        """
        from groundinsight.pathfinder import PathFinder  # local import

        if fault not in self.faults:
            raise ValueError(
                f"Fault '{fault}' does not exist in the network "
                f"'{self.name}'."
            )
        fault_bus = self.faults[fault].bus
        pathfinder = PathFinder(self)
        return [
            name
            for name, source in self.sources.items()
            if pathfinder.reaches(source.bus, fault_bus)
        ]

    def _active_topology_fingerprint(self) -> tuple:
        """Fingerprint everything :meth:`define_paths` reads.

        A path is enumerated per ``(source, fault)`` pair over the active
        bus/branch subgraph, so the fingerprint has to cover *three* things,
        not just the wiring:

        1. **The active bus/branch subset** -- catches an outage flip.
        2. **The connectivity, per branch and with multiplicity** -- catches
           in-place rewiring. The entry is the triple
           ``(branch_name, from_bus, to_bus)``. An earlier revision used a
           ``frozenset`` of bare ``(from_bus, to_bus)`` pairs, which silently
           collapses parallel branches: with ``L3: A->D`` and ``L4: A->D``,
           rewiring ``L4`` to ``A->B`` (where ``L1: A->B`` already exists)
           leaves the pair set ``{(A,B), (B,C), (A,D)}`` unchanged, so the
           second route ``A->B->C`` was never found and ``run_fault``
           reported an EPR that was ~33 % off.
        3. **The excitation** -- the ``(name, bus)`` of every source and every
           fault. ``define_paths`` iterates over both mappings, so adding a
           fault or a source after the first solve genuinely invalidates the
           path set even though the wiring did not move. Without this term
           ``run_fault`` would reuse the stale paths, no path would terminate
           at the new fault bus, and every bus would report **0 V EPR** with
           no warning;
           :func:`groundinsight.analysis.inverse_rho.find_max_rho_scaling`
           would over-estimate the admissible soil resistivity by ~3000x.

        Two networks (or two states of one network) that differ in any of
        these produce different fingerprints, so :meth:`needs_path_rebuild`
        catches all three cases.
        """
        active_buses = frozenset(
            name for name, bus in self.buses.items() if bus.active
        )
        # (name, from_bus, to_bus) -- the name keeps parallel branches
        # distinguishable, the endpoints catch in-place rewiring. This single
        # term supersedes the separate active-branch-name set.
        connectivity = frozenset(
            (name, branch.from_bus, branch.to_bus)
            for name, branch in self.branches.items()
            if branch.active
            and branch.from_bus in active_buses
            and branch.to_bus in active_buses
        )
        # The excitation: define_paths enumerates source x fault.
        sources = frozenset(
            (name, source.bus) for name, source in self.sources.items()
        )
        faults = frozenset(
            (name, fault.bus) for name, fault in self.faults.items()
        )
        return (active_buses, connectivity, sources, faults)

    def needs_path_rebuild(self) -> bool:
        """
        Whether the stored paths have to be enumerated again.

        Internal API: used by :func:`groundinsight.run_fault` and the
        electrical network; not exported from ``groundinsight`` and free to
        change in any release.

        Returns
        -------
        bool
            ``True`` if there are no paths, or if the active topology changed
            since the paths were last built (see
            :meth:`_active_topology_fingerprint`).
        """
        if not self.paths:
            return True
        return self._paths_fingerprint != self._active_topology_fingerprint()

    def add_bus(self, bus: Bus, overwrite: bool = False):
        """
        Adds a bus to the network.

        Parameters
        ----------
        bus : Bus
            The bus instance to add.
        overwrite : bool, optional
            If True, overwrites an existing bus with the same name. Defaults to False.

        Raises
        ------
        ValueError
            If a bus with the same name already exists and overwrite is False,
            or if the impedances cannot be derived from the bus type. In both
            cases neither the network nor ``bus`` is changed.
        """
        replacing = bus.name in self.buses
        if replacing and not overwrite:
            raise ValueError(
                f"Bus with name '{bus.name}' already exists in the network '{self.name}'. If you want to overwrite, set overwrite=True."
            )
        self._claim(bus)

        # Derive first, insert second: a formula that fails to evaluate must
        # leave the network as it was (ARC9). Inserting before deriving would
        # leave a bus without impedances behind, which the next run_fault
        # would report as "bus 'x' carries no impedance".
        derived = bus._derive(self.frequencies)
        bus._apply_derived(derived)
        self.buses[bus.name] = bus
        self._adopt(bus)
        if replacing:
            logger.warning(
                "Bus '%s' already exists in the network. Overwriting.",
                bus.name,
            )

    def add_branch(self, branch: Branch, overwrite: bool = False):
        """
        Adds a branch to the network.

        Parameters
        ----------
        branch : Branch
            The branch instance to add.
        overwrite : bool, optional
            If True, overwrites an existing branch with the same name. Defaults to False.

        Raises
        ------
        ValueError
            If a branch with the same name already exists, if the connected
            buses are not in the network, or if the impedances cannot be
            derived from the branch type. In every case neither the network
            nor ``branch`` is changed.
        """
        replacing = branch.name in self.branches
        if replacing and not overwrite:
            raise ValueError(
                f"Branch with name '{branch.name}' already exists in the network '{self.name}'. If you want to overwrite, set overwrite=True."
            )
        self._claim(branch)

        # Validate that the from_bus and to_bus are in the network
        if branch.from_bus not in self.buses:
            raise ValueError(
                f"from_bus '{branch.from_bus}' is not in the network '{self.name}'"
            )
        if branch.to_bus not in self.buses:
            raise ValueError(
                f"to_bus '{branch.to_bus}' is not in the network '{self.name}'"
            )
        # Derive first, insert second -- see add_bus (ARC9).
        derived = branch._derive(self.frequencies)
        branch._apply_derived(derived)
        self.branches[branch.name] = branch
        self._adopt(branch)
        if replacing:
            logger.warning(
                "Branch '%s' already exists in the network. Overwriting.",
                branch.name,
            )

    def add_fault(self, fault: Fault, overwrite: bool = False):
        """
        Adds a fault to the network.

        Parameters
        ----------
        fault : Fault
            The fault instance to add.
        overwrite : bool, optional
            If True, overwrites an existing fault with the same name. Defaults to False.

        Raises
        ------
        ValueError
            If a fault with the same name already exists, or if the associated bus is not in the network.
        """
        if fault.bus not in self.buses:
            raise ValueError(f"bus '{fault.bus}' is not in the network '{self.name}'")

        replacing = fault.name in self.faults
        if replacing and not overwrite:
            raise ValueError(
                f"Fault with name '{fault.name}' already exists in the network '{self.name}'. If you want to overwrite, set overwrite=True."
            )
        self._claim(fault)

        self.faults[fault.name] = fault
        self._adopt(fault)
        if replacing:
            logger.warning(
                "Fault '%s' already exists in the network. Overwriting.",
                fault.name,
            )

    def add_source(self, source: Source, overwrite: bool = False):
        """
        Adds a source to the network.

        Parameters
        ----------
        source : Source
            The source instance to add.
        overwrite : bool, optional
            If True, overwrites an existing source with the same name. Defaults to False.

        Raises
        ------
        ValueError
            If a source with the same name already exists, or if the associated bus is not in the network.

        Warns
        -----
        InputWarning
            ``SOURCE_FREQUENCY_OUTSIDE_NETWORK`` if the source carries values
            at frequencies the network is not solved at; the solve ignores
            them.
        """
        if source.bus not in self.buses:
            raise ValueError(f"bus '{source.bus}' is not in the network '{self.name}'")

        replacing = source.name in self.sources
        if replacing and not overwrite:
            raise ValueError(
                f"Source with name '{source.name}' already exists in the network '{self.name}'. If you want to overwrite, set overwrite=True."
            )
        self._claim(source)
        # Said before the source is inserted, so that the warning turned into
        # an error (``-W error``) leaves the network unchanged (ARC9).
        self._warn_source_frequencies_outside(source)
        self.sources[source.name] = source
        self._adopt(source)
        if replacing:
            logger.warning(
                "Source '%s' already exists in the network. Overwriting.",
                source.name,
            )

    def _warn_source_frequencies_outside(self, source: "Source") -> None:
        """
        Announce source values at frequencies the network is not solved at.

        They are not an error -- a source table may cover more harmonics than
        a given study solves -- but they are ignored by the solve, which a
        mistyped key (``60`` in a 50 Hz network) makes worth saying.

        Parameters
        ----------
        source : Source
            The source about to be added.
        """
        table = source.values if source.source_type == "current" else source.voltage
        solved = {float(f) for f in self.frequencies}
        outside = sorted(float(k) for k in (table or {}) if float(k) not in solved)
        if not outside:
            return
        emit(
            InputWarning,
            f"Source '{source.name}' carries values at "
            f"{render_frequencies(outside)}, which are not among the network "
            f"frequencies of '{self.name}' "
            f"({render_frequencies(sorted(solved))}); the solve ignores them.",
            code="SOURCE_FREQUENCY_OUTSIDE_NETWORK",
            elements=[("source", source.name)],
            stacklevel=4,
        )

    def add_path(self, path: Path):
        """
        Adds a path to the network.

        Parameters
        ----------
        path : Path
            The path instance to add.
        """
        self.paths[path.name] = path

    # -- stale results ---------------------------------------------------------

    def _input_digest_common(self) -> bytes:
        """
        Digest of the inputs every stored result depends on.

        Covers the frequencies and every bus, branch and source as the solver
        reads them (see ``_DIGEST_FIELDS``): names, ``active``, the wiring,
        ``type.grounding_conductor``, ``parallel_coefficient``, the derived or
        assigned impedance dictionaries and the source data. Elements are
        taken in name order and dictionaries in frequency order, so the
        digest does not depend on insertion order and survives JSON and the
        database.

        Returns
        -------
        bytes
            A 16-byte BLAKE2b digest.
        """
        strings: List[str] = [str(len(self.frequencies))]
        floats: List[float] = [float(f) + 0.0 for f in self.frequencies]
        for name in sorted(self.buses):
            bus = self.buses[name]
            strings.extend((name, "1" if bus.active else "0"))
            _impedance_record(bus.impedance, strings, floats)
        strings.append("|branches")
        for name in sorted(self.branches):
            branch = self.branches[name]
            strings.extend((
                name, branch.from_bus, branch.to_bus,
                "1" if branch.active else "0",
                "1" if branch.type.grounding_conductor else "0",
                repr(branch.parallel_coefficient),
            ))
            _impedance_record(branch.self_impedance, strings, floats)
            _impedance_record(branch.mutual_impedance, strings, floats)
            _impedance_record(branch.phase_impedance, strings, floats)
        strings.append("|sources")
        for name in sorted(self.sources):
            source = self.sources[name]
            strings.extend((name, source.bus, source.source_type))
            _impedance_record(source.values, strings, floats)
            _impedance_record(source.voltage, strings, floats)
            _impedance_record(source.source_impedance, strings, floats)
        digest = hashlib.blake2b(digest_size=16)
        _digest_update(digest, strings, floats)
        return digest.digest()

    def input_digest(self, fault: str, common: Optional[bytes] = None) -> Optional[str]:
        """
        Digest of everything the result of ``fault`` depends on.

        Internal API: used by :func:`groundinsight.run_fault` to stamp
        :attr:`Result.input_digest`; not exported from ``groundinsight`` and
        free to change in any release.

        Parameters
        ----------
        fault : str
            Name of the fault.
        common : bytes, optional
            :meth:`_input_digest_common`, when the caller already has it.

        Returns
        -------
        Optional[str]
            ``"<scheme>:<hex>"``, or ``None`` if ``fault`` is not a fault of
            this network.
        """
        definition = self.faults.get(fault)
        if definition is None:
            return None
        if common is None:
            common = self._input_digest_common()
        strings = [definition.name, definition.bus]
        floats: List[float] = []
        _impedance_record(definition.scalings, strings, floats)
        digest = hashlib.blake2b(common, digest_size=16)
        _digest_update(digest, strings, floats)
        return f"{_DIGEST_SCHEME}:{digest.hexdigest()}"

    def _stale(self, fault: str, common: Optional[bytes]) -> Tuple[bool, Optional[bytes]]:
        """
        Whether the stored result of ``fault`` is stale.

        Parameters
        ----------
        fault : str
            A key of :attr:`results`.
        common : bytes, optional
            The common digest, if already computed.

        Returns
        -------
        Tuple[bool, Optional[bytes]]
            The answer, and the common digest for the next call.
        """
        stamp = self.results[fault].input_digest
        if not isinstance(stamp, str) or not stamp.startswith(_DIGEST_SCHEME + ":"):
            return False, common
        if fault not in self.faults:
            return True, common
        if common is None:
            common = self._input_digest_common()
        return stamp != self.input_digest(fault, common), common

    def stale_results(self) -> List[str]:
        """
        Faults whose stored result no longer matches the network.

        A result is stale when anything it was computed from has changed
        since: the frequencies, a bus, branch or source as the solver reads
        it (impedances, ``active``, the wiring, the source data), or the
        definition of its own fault (bus, scalings). Metadata such as
        ``description``, ``Fault.t_k_s`` or the IEC 60909 characteristics of a
        source do not count, and neither does another fault. The comparison
        is by content: an edit that is undone -- by hand, or by a study that
        restores the network -- leaves the result current.

        Results without a digest -- stored in an earlier file format, or
        assembled by hand -- are never reported (their provenance is unknown).
        :func:`groundinsight.run_fault` makes the result of its fault current.

        Returns
        -------
        List[str]
            Fault names, in the order of :attr:`results`.

        Examples
        --------
        >>> gi.run_fault(net, "F")  # doctest: +SKIP
        >>> net.update_bus("b1", specific_earth_resistance=250.0)  # doctest: +SKIP
        >>> net.stale_results()  # doctest: +SKIP
        ['F']
        """
        stale: List[str] = []
        common: Optional[bytes] = None
        for fault in self.results:
            is_stale, common = self._stale(fault, common)
            if is_stale:
                stale.append(fault)
        return stale

    def is_result_stale(self, fault: str) -> bool:
        """
        Whether the stored result of ``fault`` no longer matches the network.

        See :meth:`stale_results` for what counts as a change.

        Parameters
        ----------
        fault : str
            Name of a fault with a stored result.

        Returns
        -------
        bool
            ``True`` if an input of the result has changed since it was
            computed; ``False`` if not, or if the result carries no digest.

        Raises
        ------
        ValueError
            If no result is stored for ``fault``.

        Examples
        --------
        >>> net.is_result_stale("F")  # doctest: +SKIP
        False
        """
        if fault not in self.results:
            raise ValueError(f"No results available for fault '{fault}'.")
        return self._stale(fault, None)[0]

    # -- editing ---------------------------------------------------------------

    def _lookup(self, collection: str, name: str) -> _NetworkElement:
        """
        The element ``name`` of ``collection``, or a ``ValueError``.

        Parameters
        ----------
        collection : str
            ``"buses"``, ``"branches"``, ``"sources"`` or ``"faults"``.
        name : str
            The element name.

        Returns
        -------
        _NetworkElement
            The element.

        Raises
        ------
        ValueError
            If there is no such element -- the same error class
            :func:`groundinsight.run_fault` raises for an unknown fault.
        """
        elements = getattr(self, collection)
        if name not in elements:
            kind = {"buses": "Bus", "branches": "Branch", "sources": "Source", "faults": "Fault"}[collection]
            raise ValueError(
                f"{kind} '{name}' does not exist in the network '{self.name}'."
            )
        return elements[name]

    def _update_element(self, collection: str, name: str, fields: Dict[str, Any]) -> _NetworkElement:
        """
        Change several fields of one element, all or nothing.

        Every value is validated on a detached copy first; the references
        are checked against the network and the impedances derived from the
        new inputs. Only when all of that succeeded is anything written.

        Parameters
        ----------
        collection : str
            ``"buses"`` or ``"branches"``.
        name : str
            The element name.
        fields : Dict[str, Any]
            Field name to new value.

        Returns
        -------
        _NetworkElement
            The updated element (the same object as before).

        Raises
        ------
        ValueError
            As documented for :meth:`update_bus`.
        """
        element = self._lookup(collection, name)
        cls = type(element)
        updatable = sorted(
            set(cls.model_fields) - set(cls.DERIVED_FIELDS) - {"name"}
        )
        unknown = sorted(set(fields) - set(cls.model_fields))
        if unknown:
            raise ValueError(
                f"{cls.__name__} has no field {', '.join(map(repr, unknown))}. "
                f"Fields that can be updated: {', '.join(updatable)}."
            )
        derived = sorted(set(fields) & set(cls.DERIVED_FIELDS))
        if derived:
            raise ValueError(
                f"{', '.join(map(repr, derived))} of {cls.__name__} "
                f"'{name}' {'is' if len(derived) == 1 else 'are'} derived from "
                f"the type and the inputs ({', '.join(sorted(cls._DERIVATION_INPUTS))}) "
                "and cannot be updated. To override a derived value for a study, "
                "assign it directly; it is recomputed on the next input change."
            )
        if "name" in fields and fields["name"] != name:
            raise ValueError(
                f"Cannot rename {cls._KIND} '{name}' through update: elements are "
                "kept under their name. Remove it and add a renamed copy instead."
            )

        scratch = element.model_copy()  # detached: no hooks fire on it
        for field, value in fields.items():
            setattr(scratch, field, value)
        for field in cls._BUS_REFERENCES:
            if field in fields and getattr(scratch, field) not in self.buses:
                raise ValueError(
                    f"{cls.__name__} '{name}': {field} '{getattr(scratch, field)}' "
                    f"is not a bus of network '{self.name}'."
                )
        derived_values = (
            scratch._derive(self.frequencies)
            if set(fields) & set(cls._DERIVATION_INPUTS)
            else None
        )

        for field in fields:
            element.__dict__[field] = scratch.__dict__[field]
        element.__pydantic_fields_set__.update(fields)
        if derived_values is not None:
            element._apply_derived(derived_values)
        return element

    def update_bus(self, name: str, /, **fields: Any) -> "Bus":
        """
        Change several fields of a bus at once, all or nothing.

        Every value is validated first, then the impedances are derived from
        the new inputs; only when all of that succeeded are the fields
        written. Assigning a single field (``bus.specific_earth_resistance =
        250``) has the same effect for that field.

        Parameters
        ----------
        name : str
            Name of the bus.
        **fields : Any
            Fields to change: ``type``, ``specific_earth_resistance``,
            ``active``, ``description``. The derived fields (``impedance``,
            ``R``, ``L``, ``C``) and ``name`` cannot be updated.

        Returns
        -------
        Bus
            The updated bus (the same object).

        Raises
        ------
        ValueError
            If the bus does not exist, a field is unknown, derived or
            ``name``, a value is invalid, or the impedances cannot be derived
            from the new inputs. The bus is unchanged then.

        Examples
        --------
        >>> net.update_bus("b1", specific_earth_resistance=250.0)  # doctest: +SKIP
        """
        return self._update_element("buses", name, fields)

    def update_branch(self, name: str, /, **fields: Any) -> "Branch":
        """
        Change several fields of a branch at once, all or nothing.

        Every value is validated first, ``from_bus``/``to_bus`` are checked
        against the network, then the impedances are derived from the new
        inputs; only when all of that succeeded are the fields written.

        Parameters
        ----------
        name : str
            Name of the branch.
        **fields : Any
            Fields to change: ``type``, ``length``,
            ``specific_earth_resistance``, ``from_bus``, ``to_bus``,
            ``parallel_coefficient``, ``active``, ``description``. The derived
            impedance and RLCM fields and ``name`` cannot be updated.

        Returns
        -------
        Branch
            The updated branch (the same object).

        Raises
        ------
        ValueError
            If the branch does not exist, a field is unknown, derived or
            ``name``, a value is invalid, a bus reference names no bus of
            the network, or the impedances cannot be derived. The branch is
            unchanged then.

        Examples
        --------
        >>> net.update_branch("cable_1", length=2.5, to_bus="b3")  # doctest: +SKIP
        """
        return self._update_element("branches", name, fields)

    def remove_bus(self, name: str) -> "Bus":
        """
        Remove a bus that nothing refers to any more.

        Parameters
        ----------
        name : str
            Name of the bus.

        Returns
        -------
        Bus
            The removed bus; it no longer belongs to the network.

        Raises
        ------
        ValueError
            If the bus does not exist, or if a branch, source or fault still
            refers to it -- the message names every one of them.

        Examples
        --------
        >>> net.remove_bus("spare")  # doctest: +SKIP
        """
        bus = self._lookup("buses", name)
        users = (
            [f"branch '{n}'" for n, b in self.branches.items() if name in (b.from_bus, b.to_bus)]
            + [f"source '{n}'" for n, src in self.sources.items() if src.bus == name]
            + [f"fault '{n}'" for n, flt in self.faults.items() if flt.bus == name]
        )
        if users:
            raise ValueError(
                f"Bus '{name}' cannot be removed: {', '.join(users)} still "
                f"refer{'s' if len(users) == 1 else ''} to it. Remove them first "
                "(remove_branch, remove_source, remove_fault) or reconnect them "
                "(update_branch(..., from_bus=..., to_bus=...))."
            )
        del self.buses[name]
        return bus

    def remove_branch(self, name: str) -> "Branch":
        """
        Remove a branch.

        Enumerated paths that run through the branch are dropped (see
        :meth:`invalidate_paths`); stored results become stale.

        Parameters
        ----------
        name : str
            Name of the branch.

        Returns
        -------
        Branch
            The removed branch; it no longer belongs to the network.

        Raises
        ------
        ValueError
            If the branch does not exist.

        Examples
        --------
        >>> net.remove_branch("cable_3")  # doctest: +SKIP
        """
        branch = self._lookup("branches", name)
        del self.branches[name]
        if any(seg.name == name for path in self.paths.values() for seg in path.segments):
            self.invalidate_paths()
        return branch

    def remove_source(self, name: str) -> "Source":
        """
        Remove a source.

        Parameters
        ----------
        name : str
            Name of the source.

        Returns
        -------
        Source
            The removed source; it no longer belongs to the network.

        Raises
        ------
        ValueError
            If the source does not exist.

        Examples
        --------
        >>> net.remove_source("S2")  # doctest: +SKIP
        """
        source = self._lookup("sources", name)
        del self.sources[name]
        if any(path.source == name for path in self.paths.values()):
            self.invalidate_paths()
        return source

    def remove_fault(self, name: str) -> "Fault":
        """
        Remove a fault together with its stored result.

        If it was the active fault, no fault is active afterwards. The
        results of the other faults are unaffected: they do not depend on
        this fault's definition.

        Parameters
        ----------
        name : str
            Name of the fault.

        Returns
        -------
        Fault
            The removed fault; it no longer belongs to the network.

        Raises
        ------
        ValueError
            If the fault does not exist.

        Examples
        --------
        >>> net.remove_fault("F_old")  # doctest: +SKIP
        """
        fault = self._lookup("faults", name)
        del self.faults[name]
        self.results.pop(name, None)
        if self.active_fault == name:
            fault.mark_active(False)
            self.active_fault = None
        if any(path.fault == name for path in self.paths.values()):
            self.invalidate_paths()
        return fault

    def res_buses(self, fault: Optional[str] = None) -> pl.DataFrame:
        """
        Returns a Polars DataFrame with bus results for the specified fault.

        If no fault is specified, returns results for the active fault.

        Parameters
        ----------
        fault : Optional[str], optional
            The name of the fault. Defaults to None.

        Returns
        -------
        pl.DataFrame
            One row per (bus, frequency) plus one RMS row per bus, with the
            pinned schema :data:`RES_BUSES_SCHEMA`:

            ``bus_name``, ``fault``, ``frequency_Hz`` (``Float64``, ``null``
            on the RMS row), ``is_rms`` (``Boolean``), ``EPR_V``,
            ``EPR_degree``, ``I_bus_A``, ``I_bus_degree``, ``I_inj_A``,
            ``I_inj_degree``.

        Raises
        ------
        ValueError
            If no active fault is set or if results for the specified fault are unavailable.

        Notes
        -----
        ``frequency_Hz`` is numeric. The RMS row is marked in its own boolean
        column, ``is_rms``, and carries ``null`` for the frequency, which is
        what it is: no single frequency. A literal ``"RMS"`` in the frequency
        column would type it ``Utf8`` here while the sibling accessor
        :meth:`res_all_impedances` types it ``Float64``, make
        ``filter(pl.col("frequency_Hz") == 50.0)`` silently match nothing, and
        make :func:`groundinsight.summarize` refuse the column outright.

        ``I_inj_A`` / ``I_inj_degree`` report the *earthing-conductor* current
        ``i_inj`` -- the lumped injection at a source or fault bus, which the
        earthing conductor has to be sized for. It is a different current from
        ``I_bus_A`` (the share dissipated into the soil through the electrode);
        see :class:`ResultBus`.
        """
        if fault is None:
            fault = self.active_fault
            if fault is None:
                raise ValueError("No active fault set in the network.")

        if fault not in self.results:
            raise ValueError(f"No results available for fault '{fault}'.")

        # Column-wise (P11): one pass over the result objects collects the
        # phasors; magnitudes and angles are then computed on whole arrays
        # (see _PhasorColumn). Rows, order, schema and values are those of
        # the former row-by-row construction.
        result = self.results[fault]
        names: List[str] = []
        frequencies: List[float] = []
        rms_rows: List[int] = []
        epr, current, injection = _PhasorColumn(), _PhasorColumn(), _PhasorColumn()
        for result_bus in result.buses:
            ia_freq = result_bus.ia_freq
            inj_freq = result_bus.i_inj_freq
            for freq, voltage in result_bus.uepr_freq.items():
                names.append(result_bus.name)
                frequencies.append(float(freq))
                epr.phasor(voltage)
                current.phasor(ia_freq.get(freq))
                injection.phasor(inj_freq.get(freq))
            rms_rows.append(len(names))
            names.append(result_bus.name)
            frequencies.append(0.0)
            epr.rms(result_bus.uepr)
            current.rms(result_bus.ia)
            injection.rms(result_bus.i_inj)

        is_rms = np.zeros(len(names), dtype=bool)
        is_rms[rms_rows] = True
        columns = [
            pl.Series("bus_name", names, dtype=pl.Utf8),
            pl.Series("fault", [fault] * len(names), dtype=pl.Utf8),
            _frequency_series(frequencies, rms_rows),
            pl.Series("is_rms", is_rms, dtype=pl.Boolean),
            *epr.series("EPR_V", "EPR_degree"),
            *current.series("I_bus_A", "I_bus_degree"),
            *injection.series("I_inj_A", "I_inj_degree"),
        ]
        return pl.DataFrame(columns, schema=RES_BUSES_SCHEMA)

    def res_branches(self, fault: Optional[str] = None) -> pl.DataFrame:
        """
        Returns a Polars DataFrame with branch results for the specified fault.

        If no fault is specified, returns results for the active fault.

        Parameters
        ----------
        fault : Optional[str], optional
            The name of the fault. Defaults to None.

        Returns
        -------
        pl.DataFrame
            One row per (branch, frequency) plus one RMS row per branch, with
            the pinned schema :data:`RES_BRANCHES_SCHEMA`: ``branch_name``,
            ``fault``, ``frequency_Hz`` (``Float64``, ``null`` on the RMS
            row), ``is_rms`` (``Boolean``), ``I_branch_A``,
            ``I_branch_degree``.

        Raises
        ------
        ValueError
            If no active fault is set or if results for the specified fault are unavailable.

        Notes
        -----
        See :meth:`res_buses` for why ``frequency_Hz`` is numeric and the RMS
        marker lives in ``is_rms``.
        """
        if fault is None:
            fault = self.active_fault
            if fault is None:
                raise ValueError("No active fault set in the network.")

        if fault not in self.results:
            raise ValueError(f"No results available for fault '{fault}'.")

        # Column-wise (P11), as in res_buses. A falsy entry (``None``, or a
        # plain complex zero in a hand-built result) has never produced a row.
        result = self.results[fault]
        names: List[str] = []
        frequencies: List[float] = []
        rms_rows: List[int] = []
        shield = _PhasorColumn()
        for result_branch in result.branches:
            for freq, current in result_branch.i_s_freq.items():
                if current:
                    names.append(result_branch.name)
                    frequencies.append(float(freq))
                    shield.phasor(current)
            rms_rows.append(len(names))
            names.append(result_branch.name)
            frequencies.append(0.0)
            shield.rms(result_branch.i_s)

        is_rms = np.zeros(len(names), dtype=bool)
        is_rms[rms_rows] = True
        columns = [
            pl.Series("branch_name", names, dtype=pl.Utf8),
            pl.Series("fault", [fault] * len(names), dtype=pl.Utf8),
            _frequency_series(frequencies, rms_rows),
            pl.Series("is_rms", is_rms, dtype=pl.Boolean),
            *shield.series("I_branch_A", "I_branch_degree"),
        ]
        return pl.DataFrame(columns, schema=RES_BRANCHES_SCHEMA)

    def res_fault(self, fault: str) -> pl.DataFrame:
        """
        The key figures of one fault at the fault location, as one table.

        Reads the stored result of ``fault`` -- nothing is solved -- and puts
        the numbers an assessment starts from side by side: the fault current,
        the earth potential rise at the fault bus and the largest one in the
        network, both reduction factors, the earth-return current and the
        earthing voltage and impedance of EN 50522. Without it they are spread
        over ``ResultBus``, ``ResultReductionFactor``,
        ``ResultGroundingImpedance`` and ``Network.faults``.

        Parameters
        ----------
        fault : str
            Name of a fault of this network with a stored result.

        Returns
        -------
        polars.DataFrame
            One row per network frequency and one RMS row (``is_rms`` true,
            ``frequency_Hz`` null), with the pinned schema
            :data:`RES_FAULT_SCHEMA`:

            ``fault``, ``fault_bus``
                The fault and the bus the result was computed for.
            ``I_F_A``, ``I_F_degree``
                The fault current ``I_F`` (``ResultReductionFactor.i_fault``).
            ``EPR_fault_V``, ``EPR_fault_degree``
                The EPR at the fault bus (``ResultBus.uepr_freq``).
            ``EPR_max_V``, ``EPR_max_bus``
                The largest EPR in the network and its bus (the first one in
                result order on a tie).
            ``r_epr``, ``r_current``
                The EPR-based reduction factor ``value`` and the
                current-based one ``value_current`` (``|I_E| / |I_F|``).
            ``I_E_A``
                The earth-return current ``|I_E|`` (``i_earth``).
            ``U_E_V``
                The earthing voltage ``|U_E|`` of EN 50522, the EPR at the
                fault bus (``u_earthing``).
            ``Z_E_Ohm``, ``Z_E_degree``
                The earthing impedance ``Z_E``, the driving-point impedance
                of the earthing network at the fault bus (``z_earthing``,
                equal to ``ResultGroundingImpedance.value``).
            ``n_diagnostics``
                The number of records in ``Result.diagnostics``, the same on
                every row: anything above zero qualifies how the row is read.

            A value the result marks as undefined (``None``) is null. The RMS
            row holds the stored RMS EPR of the fault bus and the largest one
            of any bus, and the root sum of squares over the frequencies of
            ``I_F``, ``I_E`` and ``U_E``; ratios, impedances and angles have
            no RMS value and are null there.

        Raises
        ------
        ValueError
            If ``fault`` is not a fault of this network.
        RuntimeError
            If the network holds no result for ``fault``.

        Notes
        -----
        The table reports the *stored* result. If the network was edited
        after it was computed, :meth:`stale_results` lists the fault; the
        numbers are still those of the result, including the frequencies it
        was computed at (a network frequency the result does not cover gives
        a row of nulls). A result in the group format (see
        :class:`ResultReductionFactor`) carries no EN 50522 earthing
        quantities and no fault current; those columns stay null rather than
        being filled from its group values.

        Examples
        --------
        >>> import groundinsight as gi
        >>> net = gi.create_network(name="demo", frequencies=[50.0, 250.0])
        >>> bus_type = gi.BusType(name="BT", system_type="MV", voltage_level=20.0,
        ...                       impedance_formula="0.01*rho")
        >>> cable = gi.BranchType(name="K", grounding_conductor=True,
        ...                       self_impedance_formula="(0.25+0.6*j*f/50)*l",
        ...                       mutual_impedance_formula="0.6*j*f/50*l")
        >>> _ = gi.create_bus(name="b0", type=bus_type, network=net)
        >>> _ = gi.create_bus(name="b1", type=bus_type, network=net)
        >>> _ = gi.create_branch(name="k", type=cable, from_bus="b0", to_bus="b1",
        ...                      length=1.0, network=net)
        >>> _ = gi.create_source(name="q", bus="b0", values={50.0: 100.0, 250.0: 10.0},
        ...                      network=net)
        >>> _ = gi.create_fault(name="ef", bus="b1", network=net)
        >>> _ = gi.run_fault(net, "ef")
        >>> table = net.res_fault("ef")
        >>> table.shape, table["is_rms"].to_list()
        ((3, 17), [False, False, True])
        """
        if fault not in self.faults:
            raise ValueError(
                f"Fault '{fault}' does not exist in network '{self.name}' "
                f"(faults: {sorted(self.faults)})."
            )
        result = self.results.get(fault)
        if result is None:
            raise RuntimeError(
                f"Network '{self.name}' holds no result for fault '{fault}'. "
                f"gi.run_fault(network, '{fault}') solves and stores one; "
                f"gi.solve(network, '{fault}') returns one without touching the "
                f"network -- keep it with network.results['{fault}'] = result."
            )
        return _fault_key_figures(self, fault, result)

    def res_all_impedances(self) -> pl.DataFrame:
        """
        Return the grounding impedance and the reduction factor of every fault.

        One row per fault, bus and frequency, with the magnitude and angle of
        the grounding impedance and the reduction factor.

        Returns
        -------
        pl.DataFrame
            A DataFrame with the pinned schema
            :data:`RES_ALL_IMPEDANCES_SCHEMA`. There is no RMS row here --
            a grounding impedance is defined per frequency.
            ``grounding_impedance_Ohm`` / ``_deg`` are ``Z_E``, the
            driving-point impedance of the earthing network at the fault bus
            (``ResultReductionFactor.z_earthing``); ``reduction_factor`` is
            ``value`` and ``reduction_factor_current`` is ``value_current``.

        Notes
        -----
            - Faults without results are skipped.
            - Missing grounding impedance or reduction factor results are noted.
            - The schema is pinned so an empty result is an empty frame with
              the documented columns rather than a 0x0 one, which would make
              every downstream ``filter``/``join`` fail with a missing-column
              error instead of returning nothing.
        """
        data = []
        for fault_name, fault in self.faults.items():
            if fault_name not in self.results:
                logger.warning(
                    "No results available for fault '%s'. Skipping.",
                    fault_name,
                )
                continue
            result = self.results[fault_name]
            fault_bus = fault.bus

            # Grounding Impedance
            grounding_impedance = result.grounding_impedance
            if not grounding_impedance:
                logger.warning(
                    "No grounding impedance results for fault '%s'.",
                    fault_name,
                )
                continue

            # Reduction Factor
            reduction_factor = result.reduction_factor
            if not reduction_factor:
                logger.warning(
                    "No reduction factor results for fault '%s'.",
                    fault_name,
                )
                continue

            for freq in self.frequencies:
                gi = grounding_impedance.value.get(freq)
                rf = reduction_factor.value.get(freq)
                rf_current = reduction_factor.value_current.get(freq)
                if gi:
                    gi_magnitude = abs(complex(gi.real, gi.imag))
                    gi_angle = np.degrees(np.angle(complex(gi.real, gi.imag)))
                else:
                    gi_magnitude = None
                    gi_angle = None

                data.append(
                    {
                        "fault_name": fault_name,
                        "fault_bus": fault_bus,
                        "frequency_Hz": float(freq),
                        "grounding_impedance_Ohm": gi_magnitude,
                        "grounding_impedance_deg": gi_angle,
                        "reduction_factor": rf,
                        "reduction_factor_current": rf_current,
                    }
                )
        return pl.DataFrame(data, schema=RES_ALL_IMPEDANCES_SCHEMA)

    def to_tables(self) -> Dict[str, pl.DataFrame]:
        """
        Export the network as the tables :func:`groundinsight.network_from_tables` reads.

        Returns
        -------
        dict of str to polars.DataFrame
            One frame per table of
            :data:`groundinsight.io.tables.TABLE_SCHEMAS`, with that schema.

        Raises
        ------
        ValueError
            If two different bus types, or branch types, share a name.

        Notes
        -----
        Thin wrapper around :func:`groundinsight.io.tables.network_to_tables`;
        see there for what the tables hold and what is recomputed.

        Examples
        --------
        >>> import groundinsight as gi
        >>> net = gi.create_network(name="demo", frequencies=[50.0])
        >>> list(net.to_tables())[:3]
        ['network', 'frequencies', 'bus_types']
        """
        from groundinsight.io.tables import network_to_tables  # avoids a cycle

        return network_to_tables(self)

    def __str__(self):
        return f"Network(name={self.name})"

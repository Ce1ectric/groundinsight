# models/core_models.py

import logging
import math
import warnings

import numpy as np
from pydantic import (
    BaseModel,
    ConfigDict,
    PrivateAttr,
    computed_field,
    field_validator,
    model_validator,
)
from typing import Any, Optional, List, Dict, Literal, Union
from sympy import lambdify, sympify, symbols
from groundinsight.utils.validations import validate_impedance_formula_value
from groundinsight.utils.impedance_calculator import (
    check_passive_impedance,
    compute_impedance,
    compute_real_value,
)
import polars as pl


logger = logging.getLogger(__name__)


class NetworkFrequencyOrderWarning(UserWarning):
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
    """


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
    """

    model_config = ConfigDict(ser_json_inf_nan="constants")

    real: float
    imag: float

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
            except ValueError:
                raise ValueError(f"Invalid complex number string: {value}")
        else:
            raise TypeError(f"Cannot parse ComplexNumber from type {type(value)}")

    def __complex__(self):
        return complex(self.real, self.imag)

    def __repr__(self):
        return f"({self.real}+{self.imag}j)"

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
        other = self._convert_to_complex_number(other)
        return self.real == other.real and self.imag == other.imag

    # Implementing exponentiation
    def __pow__(self, power, modulo=None):
        if modulo is not None:
            raise ValueError("Modulo operation is not supported for complex numbers.")
        if isinstance(power, ComplexNumber):
            power = complex(power.real, power.imag)
        elif isinstance(power, (int, float)):
            power = float(power)
        else:
            raise TypeError(f"Unsupported type for exponentiation: {type(power)}")
        base = complex(self.real, self.imag)
        result = base**power
        return ComplexNumber(real=result.real, imag=result.imag)

    def __rpow__(self, other):
        if isinstance(other, ComplexNumber):
            other = complex(other.real, other.imag)
        elif isinstance(other, (int, float)):
            other = float(other)
        else:
            raise TypeError(f"Unsupported type for exponentiation: {type(other)}")
        exponent = complex(self.real, self.imag)
        result = other**exponent
        return ComplexNumber(real=result.real, imag=result.imag)

    def _convert_to_complex_number(self, value) -> "ComplexNumber":
        """
        Converts a value to a ComplexNumber instance.

        Parameters
        ----------
        value : ComplexNumber, complex, float, int, dict, str
            The value to convert.

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
        elif isinstance(value, complex):
            return ComplexNumber(real=value.real, imag=value.imag)
        elif isinstance(value, (int, float)):
            return ComplexNumber(real=float(value), imag=0.0)
        else:
            raise TypeError(f"Cannot convert {type(value)} to ComplexNumber")


# user interface
class BusType(BaseModel):
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
    """

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
    def validate_impedance_formula(cls, value):
        """Validate the SymPy ``impedance_formula`` string."""
        return validate_impedance_formula_value(value)

    @field_validator("R_formula", "L_formula", "C_formula")
    def validate_rlc_formula(cls, value):
        """Validate the optional lumped RLC formula strings; ``None`` is allowed."""
        if value is None:
            return value
        return validate_impedance_formula_value(value)

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


class Bus(BaseModel):
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
    """

    model_config = ConfigDict(ser_json_inf_nan="constants")

    name: str
    description: Optional[str] = None
    type: BusType
    impedance: Dict[float, ComplexNumber]
    specific_earth_resistance: float = 100.0
    active: bool = True
    R: Optional[Dict[float, float]] = None
    L: Optional[Dict[float, float]] = None
    C: Optional[Dict[float, float]] = None

    def calculate_impedance(self, frequencies: List[float]):
        """
        Calculates impedance and -- if specified by the type -- the lumped
        RLC parameters for each frequency.

        ``impedance`` is always recomputed from ``type.impedance_formula``.
        Each of ``R``, ``L`` and ``C`` is recomputed only if the matching
        formula is set on the type; otherwise the attribute is left as
        ``None``. Utilizes the external ``impedance_calculator`` to avoid
        storing non-pickleable functions.

        Parameters
        ----------
        frequencies : List[float]
            A list of frequencies at which to
            evaluate the formulas.
        """
        rho = self.specific_earth_resistance
        params = {"rho": rho}

        # Mandatory frequency-domain impedance.
        impedance = compute_impedance(
            formula_str=self.type.impedance_formula,
            frequencies=frequencies,
            params=params,
        )
        # The grounding impedance is inverted into a diagonal admittance, so a
        # value that cannot become an admittance has to be rejected here rather
        # than silently dropped by the solver. See check_passive_impedance.
        check_passive_impedance(
            impedance,
            element=f"bus '{self.name}' (grounding impedance)",
            formula_str=self.type.impedance_formula,
            params=params,
        )
        self.impedance = impedance

        # Optional lumped RLC -- skipped silently when no formula is set.
        if self.type.R_formula is not None:
            self.R = compute_real_value(
                self.type.R_formula, frequencies, params, name=f"{self.name}.R"
            )
        if self.type.L_formula is not None:
            self.L = compute_real_value(
                self.type.L_formula, frequencies, params, name=f"{self.name}.L"
            )
        if self.type.C_formula is not None:
            self.C = compute_real_value(
                self.type.C_formula, frequencies, params, name=f"{self.name}.C"
            )

    @field_validator("impedance", mode="before")
    def validate_impedance(cls, value):
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


class BranchType(BaseModel):
    """
    Represents the type of a branch, including its impedance formulas.

    The mandatory ``self_impedance_formula`` and
    ``mutual_impedance_formula`` drive the frequency-domain solver.

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
    """

    name: str
    description: Optional[str] = None
    grounding_conductor: bool
    self_impedance_formula: str
    mutual_impedance_formula: str
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
    def validate_impedance_formula(cls, value):
        """Validate the SymPy self / mutual impedance formula strings."""
        return validate_impedance_formula_value(value)

    @field_validator(
        "R_self_formula",
        "L_self_formula",
        "C_self_formula",
        "R_mutual_formula",
        "M_mutual_formula",
    )
    def validate_rlc_formula(cls, value):
        """Validate the optional lumped RLC formula strings; ``None`` is allowed."""
        if value is None:
            return value
        return validate_impedance_formula_value(value)

    @field_validator("cross_section_mm2")
    def _validate_cross_section(cls, value):
        """Reject a non-positive conductor cross-section."""
        if value is not None and value <= 0:
            raise ValueError("cross_section_mm2 must be strictly positive.")
        return value

    def __str__(self):
        return f"BranchType(name={self.name}, grounding_conductor={self.grounding_conductor})"


class Branch(BaseModel):
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
        The length of the branch (km).
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
    """

    model_config = ConfigDict(ser_json_inf_nan="constants")

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
    R_self: Optional[Dict[float, float]] = None
    L_self: Optional[Dict[float, float]] = None
    C_self: Optional[Dict[float, float]] = None
    R_mutual: Optional[Dict[float, float]] = None
    M_mutual: Optional[Dict[float, float]] = None

    def calculate_impedance(self, frequencies: List[float]):
        """
        Calculate self/mutual impedance and -- if specified by the type --
        the lumped RLCM parameters for each frequency.

        ``self_impedance`` and ``mutual_impedance`` are always recomputed.
        Each of ``R_self``, ``L_self``, ``C_self``, ``R_mutual``,
        ``M_mutual`` is recomputed only if the matching formula is set on
        the branch type; otherwise the attribute is left as ``None``.

        Parameters
        ----------
        frequencies : List[float]
            A list of frequencies at which to
            evaluate the formulas.
        """
        self._calculate_self_impedance(frequencies)
        self._calculate_mutual_impedance(frequencies)
        self._calculate_rlc_parameters(frequencies)

    def _calculate_rlc_parameters(self, frequencies: List[float]):
        """
        Evaluate the optional lumped RLCM formulas of the branch type.

        The same ``rho`` / ``l`` parameter substitution as in the impedance
        formulas is used so the units stay consistent.

        Parameters
        ----------
        frequencies : List[float]
            Frequencies at which to evaluate.
        """
        rho = self.specific_earth_resistance
        l = self.length
        params = {"rho": rho, "l": l}

        if self.type.R_self_formula is not None:
            self.R_self = compute_real_value(
                self.type.R_self_formula, frequencies, params,
                name=f"{self.name}.R_self",
            )
        if self.type.L_self_formula is not None:
            self.L_self = compute_real_value(
                self.type.L_self_formula, frequencies, params,
                name=f"{self.name}.L_self",
            )
        if self.type.C_self_formula is not None:
            self.C_self = compute_real_value(
                self.type.C_self_formula, frequencies, params,
                name=f"{self.name}.C_self",
            )
        if self.type.R_mutual_formula is not None:
            self.R_mutual = compute_real_value(
                self.type.R_mutual_formula, frequencies, params,
                name=f"{self.name}.R_mutual",
            )
        if self.type.M_mutual_formula is not None:
            self.M_mutual = compute_real_value(
                self.type.M_mutual_formula, frequencies, params,
                name=f"{self.name}.M_mutual",
            )

    def _calculate_self_impedance(self, frequencies: List[float]):
        """
        Calculates self impedance for each frequency using the self impedance formula.

        Utilizes the external `impedance_calculator` to perform the computation.
        The results are stored in the `self_impedance` attribute.

        Parameters
        ----------
        frequencies : List[float]
            A list of frequencies at which to calculate self impedance.
        """
        formula = self.type.self_impedance_formula
        rho = self.specific_earth_resistance
        l = self.length

        # Prepare parameters dictionary
        params = {"rho": rho, "l": l}

        # Compute self impedance
        self_impedance = compute_impedance(
            formula_str=formula, frequencies=frequencies, params=params
        )
        # Only a grounding conductor has its self impedance inverted into an
        # admittance; for a branch without one the self impedance never reaches
        # a division and any value is harmless.
        if self.type.grounding_conductor:
            check_passive_impedance(
                self_impedance,
                element=f"branch '{self.name}' (self impedance)",
                formula_str=formula,
                params=params,
            )
        self.self_impedance = self_impedance

    def _calculate_mutual_impedance(self, frequencies: List[float]):
        """
        Calculates mutual impedance for each frequency using the mutual impedance formula.

        Utilizes the external `impedance_calculator` to perform the computation.
        The results are stored in the `mutual_impedance` attribute.

        Parameters
        ----------
        frequencies : List[float]
            A list of frequencies at which to calculate mutual impedance.
        """
        formula = self.type.mutual_impedance_formula
        rho = self.specific_earth_resistance
        l = self.length

        # Prepare parameters dictionary
        params = {"rho": rho, "l": l}

        # Compute mutual impedance
        self.mutual_impedance = compute_impedance(
            formula_str=formula, frequencies=frequencies, params=params
        )

    @field_validator("self_impedance", mode="before")
    def validate_self_impedance(cls, value):
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


class Fault(BaseModel):
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
    scalings : dict of float to float
        Scaling factors for sources at different frequencies.
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
    """

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
        """
        if value is None:
            return value
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
            normalised[f_key] = scaling
        return normalised

    @computed_field()
    @property
    def active(self) -> bool:
        """Whether this fault is currently the active one in the network."""
        return self._active

    def _set_active(self, value: bool):
        """Set the active flag; called internally by ``Network.set_active_fault``."""
        self._active = value

    def __str__(self):
        return f"Fault(name={self.name}, bus={self.bus})"


class Source(BaseModel):
    """
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
        Frequency-dependent current injection. Required when
        ``source_type == "current"`` and must be ``None`` otherwise.
    voltage : dict of float to ComplexNumber, optional
        Frequency-dependent Thevenin EMF. Required when
        ``source_type == "voltage"`` and must be ``None`` otherwise.
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
    """

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
        Validate that the fields populated on the source match the declared
        ``source_type``.

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
    """

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
    """

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
        Mapping from frequency to reduction factor ``r(f)``.
    """

    name: Optional[str] = None  # Make name optional with a default value
    fault_bus: str
    value: Dict[float, Optional[float]]  # Mapping from frequency to reduction factor

    def __str__(self):
        # The field is called ``value``; the old ``self.reduction_factor``
        # raised AttributeError. That stayed invisible because pydantic's
        # __repr__ keeps working and only str() / print() / f-strings go
        # through __str__ -- so a bare cell in a notebook looked fine.
        return (
            f"ResultReductionFactor(name={self.name}, "
            f"fault_bus={self.fault_bus}, value={self.value})"
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
        Mapping from frequency to grounding impedance ``Z_G(f)``.
    """

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
    """

    buses: List[ResultBus] = []
    branches: List[ResultBranch] = []
    reduction_factor: Optional[ResultReductionFactor] = None
    grounding_impedance: Optional[ResultGroundingImpedance] = None
    fault: str = ""  # name of the fault that was active

    def __str__(self):
        return f"Result(buses={len(self.buses)}, branches={len(self.branches)})"


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
    """

    name: str
    description: Optional[str] = None
    source: str
    fault: str
    segments: List[Branch] = []

    def __str__(self):
        return f"Path(name={self.name}, source={self.source}, fault={self.fault})"


class Network(BaseModel):
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
    """

    model_config = ConfigDict(ser_json_inf_nan="constants")

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

    @field_validator("frequencies", mode="after")
    @classmethod
    def _validate_frequencies(cls, value: List[float]) -> List[float]:
        """Reject empty / duplicate / non-finite / negative frequency lists.

        ``f = 0`` (DC) is **permitted** because the FFT transient solver
        in :mod:`groundinsight.simulation.transient` uses the
        zero-frequency bin to carry the steady-state offset.

        Non-strictly-monotone-increasing inputs are accepted but trigger
        a :class:`NetworkFrequencyOrderWarning` — the transient solver
        relies on the *order* of ``Network.frequencies`` to map to the
        FFT spectral bins, so a shuffled or descending list almost
        always indicates a user error.

        Rationale
        ---------
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

        Notes
        -----
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
                stacklevel=3,
            )
        return value

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
                _fault._set_active(_name == self.active_fault)
        return self

    @property
    def electrical_network(self):
        return self._electrical_network

    @electrical_network.setter
    def electrical_network(self, value):
        self._electrical_network = value

    def set_active_fault(self, fault_name: str, keep_results: bool = False):
        """Set the specified fault as active and deactivate all other faults.

        Parameters
        ----------
        fault_name : str
            The name of the fault to activate.
        keep_results : bool, default ``False``
            If ``False`` (historic behaviour), any previously cached
            :class:`Result` for ``fault_name`` is dropped so the next
            solve starts from a clean slate. If ``True``, the cached
            result is preserved — useful when re-using the same
            network to plot a previous solve without recomputing it.

        Raises
        ------
        ValueError
            If the specified fault does not exist in the network.
        """
        if fault_name not in self.faults:
            raise ValueError(f"Fault '{fault_name}' does not exist in the network.")

        # Deactivate all faults
        for fault in self.faults.values():
            fault._set_active(False)

        # Activate the specified fault
        fault = self.faults[fault_name]
        fault._set_active(True)
        self.active_fault = fault_name

        # Clear previous results for the fault unless the caller
        # explicitly asks us to keep them.
        if not keep_results and fault_name in self.results:
            del self.results[fault_name]

    def invalidate_paths(self) -> None:
        """Drop the cached pathfinder results *for this network*.

        Rebinds ``self.paths`` to a fresh empty dictionary (atomic) and
        drops the module-level :mod:`groundinsight.pathfinder` cache
        entries whose key is scoped to this :class:`Network` instance.
        **Other networks' cache entries are preserved.** This matters
        as soon as the user runs more than one network in the same
        Python process (notebooks that compare two scenarios,
        dashboards iterating over a set of feeders, …).

        Earlier revisions called ``self.paths.clear()`` in place.
        Callers that had snapshot the mapping with
        ``saved = dict(network.paths)`` before the call observed the
        snapshot lose its entries because the snapshot dictionary
        shared its ``Path`` *values* with ``self.paths`` until the
        snapshot was deep-copied. The current atomic-rebind form
        — ``self.paths = {}`` — leaves the snapshot mapping intact and
        mirrors the atomic-rebind pattern in
        :func:`groundinsight.analysis.inverse_rho_f.evaluate_max_epr_under_k`.

        Call this whenever the user has flipped ``Bus.active`` /
        ``Branch.active`` flags or added / removed branches outside of
        a context manager that performs its own rollback.
        """
        from groundinsight.pathfinder import clear_pathfinder_cache  # local import

        # Atomic rebind so external snapshots survive.
        self.paths = {}
        clear_pathfinder_cache(self)

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
           ``run_fault`` reused the old paths, no path terminated at the new
           fault bus, and every bus reported **0 V EPR** with no warning.
           The same omission made
           :func:`groundinsight.analysis.inverse_rho.find_max_rho_scaling`
           over-estimate the admissible soil resistivity by ~3000x.

        Two networks (or two states of one network) that differ in any of
        these produce different fingerprints, so :meth:`_needs_path_rebuild`
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

    def _needs_path_rebuild(self) -> bool:
        """True if paths are missing or the active topology changed since the
        paths were last built (see :meth:`_active_topology_fingerprint`)."""
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
            If a bus with the same name already exists and overwrite is False.
        """
        if bus.name in self.buses:
            if overwrite:
                logger.warning(
                    "Bus '%s' already exists in the network. Overwriting.",
                    bus.name,
                )
            else:
                raise ValueError(
                    f"Bus with name '{bus.name}' already exists in the network '{self.name}'. If you want to overwrite, set overwrite=True."
                )

        self.buses[bus.name] = bus
        # Trigger impedance calculation when a bus is added
        bus.calculate_impedance(self.frequencies)

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
            If a branch with the same name already exists, or if the connected buses are not in the network.
        """
        if branch.name in self.branches:
            if overwrite:
                logger.warning(
                    "Branch '%s' already exists in the network. Overwriting.",
                    branch.name,
                )
            else:
                raise ValueError(
                    f"Branch with name '{branch.name}' already exists in the network '{self.name}'. If you want to overwrite, set overwrite=True."
                )

        # Validate that the from_bus and to_bus are in the network
        if branch.from_bus not in self.buses:
            raise ValueError(
                f"from_bus '{branch.from_bus}' is not in the network '{self.name}'"
            )
        if branch.to_bus not in self.buses:
            raise ValueError(
                f"to_bus '{branch.to_bus}' is not in the network '{self.name}'"
            )
        self.branches[branch.name] = branch
        # Trigger impedance calculation when a branch is added
        branch.calculate_impedance(self.frequencies)

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

        if fault.name in self.faults:
            if overwrite:
                logger.warning(
                    "Fault '%s' already exists in the network. Overwriting.",
                    fault.name,
                )
            else:
                raise ValueError(
                    f"Fault with name '{fault.name}' already exists in the network '{self.name}'. If you want to overwrite, set overwrite=True."
                )

        self.faults[fault.name] = fault

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
        """
        if source.bus not in self.buses:
            raise ValueError(f"bus '{source.bus}' is not in the network '{self.name}'")

        if source.name in self.sources:
            if overwrite:
                logger.warning(
                    "Source '%s' already exists in the network. Overwriting.",
                    source.name,
                )
            else:
                raise ValueError(
                    f"Source with name '{source.name}' already exists in the network '{self.name}'. If you want to overwrite, set overwrite=True."
                )
        self.sources[source.name] = source

    def add_path(self, path: Path):
        """
        Adds a path to the network.

        Parameters
        ----------
        path : Path
            The path instance to add.
        """
        self.paths[path.name] = path

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
            A DataFrame containing bus results.

        Raises
        ------
        ValueError
            If no active fault is set or if results for the specified fault are unavailable.
        """
        if fault is None:
            fault = self.active_fault
            if fault is None:
                raise ValueError("No active fault set in the network.")

        if fault not in self.results:
            raise ValueError(f"No results available for fault '{fault}'.")

        result = self.results[fault]
        data = []
        for result_bus in result.buses:
            # Add frequency-specific data
            for freq, voltage in result_bus.uepr_freq.items():
                current = result_bus.ia_freq.get(freq)
                voltage_abs = abs(complex(voltage.real, voltage.imag))
                current_abs = abs(complex(current.real, current.imag))
                voltage_ang = (
                    np.angle(complex(voltage.real, voltage.imag)) * 180 / np.pi
                )
                current_ang = (
                    np.angle(complex(current.real, current.imag)) * 180 / np.pi
                )
                data.append(
                    {
                        "bus_name": result_bus.name,
                        "fault": fault,
                        "frequency_Hz": freq,
                        "EPR_V": voltage_abs,
                        "EPR_degree": voltage_ang,
                        "I_bus_A": current_abs,
                        "I_bus_degree": current_ang,
                    }
                )
            # Add RMS values
            data.append(
                {
                    "bus_name": result_bus.name,
                    "fault": fault,
                    "frequency_Hz": "RMS",
                    "EPR_V": result_bus.uepr,
                    "EPR_degree": None,
                    "I_bus_A": result_bus.ia,
                    "I_bus_degree": None,
                }
            )

        df = pl.DataFrame(data)
        return df

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
            A DataFrame containing branch results.

        Raises
        ------
        ValueError
            If no active fault is set or if results for the specified fault are unavailable.
        """
        if fault is None:
            fault = self.active_fault
            if fault is None:
                raise ValueError("No active fault set in the network.")

        if fault not in self.results:
            raise ValueError(f"No results available for fault '{fault}'.")

        result = self.results[fault]
        data = []
        for result_branch in result.branches:
            # Add frequency-specific data
            for freq, current in result_branch.i_s_freq.items():
                if current:
                    current_abs = abs(complex(current.real, current.imag))
                    current_ang = (
                        np.angle(complex(current.real, current.imag)) * 180 / np.pi
                    )
                    data.append(
                        {
                            "branch_name": result_branch.name,
                            "fault": fault,
                            "frequency_Hz": freq,
                            "I_branch_A": current_abs,
                            "I_branch_degree": current_ang,
                        }
                    )
            # Add RMS current
            data.append(
                {
                    "branch_name": result_branch.name,
                    "fault": fault,
                    "frequency_Hz": "RMS",
                    "I_branch_A": result_branch.i_s,
                    "I_branch_degree": None,
                }
            )

        df = pl.DataFrame(data)
        return df

    def res_all_impedances(self) -> pl.DataFrame:
        """
        Returns a Polars DataFrame containing the grounding impedance and reduction factor
        for each fault, bus, and frequency.

        The DataFrame includes grounding impedance magnitude and angle, as well as the reduction factor.

        Returns
        -------
        pl.DataFrame
            A DataFrame containing grounding impedance and reduction factors.

        Notes
        -----
            - Faults without results are skipped.
            - Missing grounding impedance or reduction factor results are noted.
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
                if gi:
                    gi_real = gi.real
                    gi_imag = gi.imag
                    gi_magnitude = abs(complex(gi.real, gi.imag))
                    gi_angle = np.degrees(np.angle(complex(gi.real, gi.imag)))
                else:
                    gi_real = None
                    gi_imag = None
                    gi_magnitude = None
                    gi_angle = None

                data.append(
                    {
                        "fault_name": fault_name,
                        "fault_bus": fault_bus,
                        "frequency_Hz": freq,
                        "grounding_impedance_Ohm": gi_magnitude,
                        "grounding_impedance_deg": gi_angle,
                        "reduction_factor": rf,
                    }
                )
        df = pl.DataFrame(data)
        return df

    def __str__(self):
        return f"Network(name={self.name})"

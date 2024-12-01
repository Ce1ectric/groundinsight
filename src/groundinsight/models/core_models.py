# models/core_models.py

import numpy as np
from pydantic import (
    BaseModel,
    PrivateAttr,
    computed_field,
    field_validator,
    model_validator,
)
from typing import Optional, List, Dict
from sympy import lambdify, sympify, symbols
from groundinsight.utils.validations import validate_impedance_formula_value
from groundinsight.utils.impedance_calculator import compute_impedance
import polars as pl


# data types
class ComplexNumber(BaseModel):
    """
    Represents a complex number with real and imaginary components to creat Pydantic models with complex Numbers.

    Attributes:
        real (float): The real part of the complex number.
        imag (float): The imaginary part of the complex number.
    """

    real: float
    imag: float

    @field_validator("real", "imag", mode="before")
    def convert_to_float(cls, value):
        if value is None:
            return np.nan
        return float(value)

    @model_validator(mode="before")
    @classmethod
    def validate_complex(cls, value):
        """
        Validates and converts the input value to a ComplexNumber instance.

        Args:
            value: The value to validate and convert. Can be a ComplexNumber, complex, float, int, dict, or str.

        Returns:
            dict: A dictionary with 'real' and 'imag' keys for ComplexNumber initialization.

        Raises:
            ValueError: If the input string cannot be parsed as a complex number.
            TypeError: If the input type is unsupported.
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

        Args:
            value (ComplexNumber, complex, float, int, dict, str): The value to convert.

        Returns:
            ComplexNumber: The converted complex number.

        Raises:
            TypeError: If the value cannot be converted to ComplexNumber.
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

    Attributes:
        name (str): The name of the bus type.
        description (Optional[str]): A brief description of the bus type.
        system_type (str): The system type associated with the bus. e.g. 'Tower' or 'Substation'
        voltage_level (float): The voltage level of the bus.
        impedance_formula (str): The formula used to calculate groundingimpedance.
    """

    name: str
    description: Optional[str] = None
    system_type: str
    voltage_level: float
    impedance_formula: str

    @field_validator("impedance_formula")
    def validate_impedance_formula(cls, value):
        return validate_impedance_formula_value(value)

    def __str__(self):
        return f"BusType(name={self.name}, system_type={self.system_type}, voltage_level={self.voltage_level})"


class Bus(BaseModel):
    """
    Represents a grounding bus within the network.

    Attributes:
        name (str): The name of the bus.
        description (Optional[str]): A brief description of the bus.
        type (BusType): The type of the bus.
        impedance (Dict[float, ComplexNumber]): A mapping of frequency to impedance values.
        specific_earth_resistance (float): The specific earth resistance associated with the bus.
    """

    name: str
    description: Optional[str] = None
    type: BusType
    impedance: Dict[float, ComplexNumber]
    specific_earth_resistance: float = 100.0

    def calculate_impedance(self, frequencies: List[float]):
        """
        Calculates impedance for each frequency using the impedance formula.

        Utilizes the external `impedance_calculator` to avoid storing non-pickleable functions.
        The calculated impedance values are stored in the `impedance` attribute.

        Args:
            frequencies (List[float]): A list of frequencies at which to calculate impedance.
        """
        # Extract the formula and parameters
        formula = self.type.impedance_formula
        rho = self.specific_earth_resistance

        # Prepare parameters dictionary
        params = {"rho": rho}

        # Compute impedance using the external utility function
        self.impedance = compute_impedance(
            formula_str=formula, frequencies=frequencies, params=params
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

    Attributes:
        name (str): The name of the branch type.
        description (Optional[str]): A brief description of the branch type.
        grounding_conductor (bool): Indicates whether the branch has a grounding wire or cable shield.
        self_impedance_formula (str): The formula used to calculate self-impedance.
        mutual_impedance_formula (str): The formula used to calculate mutual impedance.
    """

    name: str
    description: Optional[str] = None
    grounding_conductor: bool
    self_impedance_formula: str
    mutual_impedance_formula: str

    @field_validator("self_impedance_formula", "mutual_impedance_formula")
    def validate_impedance_formula(cls, value):
        return validate_impedance_formula_value(value)

    def __str__(self):
        return f"BranchType(name={self.name}, grounding_conductor={self.grounding_conductor})"


class Branch(BaseModel):
    """
    Represents a branch (conductor) connecting two buses within the network.

    Attributes:
        name (str): The name of the branch.
        description (Optional[str]): A brief description of the branch.
        type (BranchType): The type of the branch.
        length (float): The length of the branch.
        from_bus (str): The name of the originating bus.
        to_bus (str): The name of the destination bus.
        self_impedance (Dict[float, ComplexNumber]): Self-impedance values mapped by frequency.
        mutual_impedance (Dict[float, ComplexNumber]): Mutual-impedance values mapped by frequency.
        specific_earth_resistance (float): The specific earth resistance associated with the branch.
        parallel_coefficient (Optional[float]): The parallel coefficient between 0..1, if any.
    """

    name: str
    description: Optional[str] = None
    type: BranchType
    length: float
    from_bus: str
    to_bus: str
    self_impedance: Dict[float, ComplexNumber]
    mutual_impedance: Dict[float, ComplexNumber]
    specific_earth_resistance: float = 100.0
    parallel_coefficient: Optional[float] = None  # Default to None

    def calculate_impedance(self, frequencies: List[float]):
        """
        Calculates self and mutual impedance for each frequency using the impedance formula.

        Utilizes the external `impedance_calculator` to avoid storing non-pickleable functions.
        The calculated impedance values are stored in the `self_impedance` and `mutual_impedance` attributes.

        Args:
            frequencies (List[float]): A list of frequencies at which to calculate impedance.
        """
        self._calculate_self_impedance(frequencies)
        self._calculate_mutual_impedance(frequencies)

    def _calculate_self_impedance(self, frequencies: List[float]):
        """
        Calculates self impedance for each frequency using the self impedance formula.

        Utilizes the external `impedance_calculator` to perform the computation.
        The results are stored in the `self_impedance` attribute.

        Args:
            frequencies (List[float]): A list of frequencies at which to calculate self impedance.
        """
        formula = self.type.self_impedance_formula
        rho = self.specific_earth_resistance
        l = self.length

        # Prepare parameters dictionary
        params = {"rho": rho, "l": l}

        # Compute self impedance
        self.self_impedance = compute_impedance(
            formula_str=formula, frequencies=frequencies, params=params
        )

    def _calculate_mutual_impedance(self, frequencies: List[float]):
        """
        Calculates mutual impedance for each frequency using the mutual impedance formula.

        Utilizes the external `impedance_calculator` to perform the computation.
        The results are stored in the `mutual_impedance` attribute.

        Args:
            frequencies (List[float]): A list of frequencies at which to calculate mutual impedance.
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

    Attributes:
        name (str): The name of the fault.
        description (Optional[str]): A brief description of the fault.
        bus (str): The name of the bus where the fault occurs.
        scalings (Dict[float, float]): Scaling factors for sources at different frequencies.
        _active (bool): Indicates whether the fault is active.
    """

    name: str
    description: Optional[str] = None
    bus: str  # Location of the fault
    scalings: Dict[float, float] = {}  # Scaling factors for sources
    _active: bool = PrivateAttr(default=False)

    @computed_field()
    @property
    def active(self) -> bool:
        return self._active

    def _set_active(self, value: bool):
        self._active = value

    def __str__(self):
        return f"Fault(name={self.name}, bus={self.bus})"


class Source(BaseModel):
    """
    Represents a current source within the network.

    Attributes:
        name (str): The name of the source.
        description (Optional[str]): A brief description of the source.
        bus (str): The name of the bus where the source is located.
        values (Dict[float, ComplexNumber]): A mapping of frequency to current values.
    """

    name: str
    description: Optional[str] = None
    bus: str  # Location of the source
    values: Dict[float, ComplexNumber]  # {frequency: current value}

    def __str__(self):
        return f"Source(name={self.name}, bus={self.bus})"


class ResultBus(BaseModel):
    """
    Represents the result data for a bus after running a fault calculation.

    Attributes:
        name (str): The name of the bus.
        uepr (float): Earth potential rise at the bus.
        ia (float): Current at the bus.
        uepr_freq (Dict[float, ComplexNumber]): Mapping of frequency to voltage values.
        ia_freq (Dict[float, ComplexNumber]): Mapping of frequency to current values.
    """

    name: str  # name of the bus
    uepr: float  # Earth potential rise
    ia: float  # Current
    uepr_freq: Dict[float, ComplexNumber]  # {frequency: voltage}
    ia_freq: Dict[float, ComplexNumber]  # {frequency: current}

    def __str__(self):
        return f"ResultBus(name={self.name}, uepr={self.uepr})"


class ResultBranch(BaseModel):
    """
    Represents the result data for a branch after computations.

    Attributes:
        name (str): The name of the branch.
        i_s (float): Shield current in the branch.
        i_s_freq (Dict[float, ComplexNumber]): Mapping of frequency to current values.
    """

    name: str  # name of the branch
    i_s: float  # Shield current
    i_s_freq: Dict[float, ComplexNumber]  # {frequency: current}

    def __str__(self):
        return f"ResultBranch(name={self.name})"


class ResultReductionFactor(BaseModel):
    """
    Represents the reduction factor results for faults.

    Attributes:
        name (Optional[str]): The name of the reduction factor result.
        fault_bus (str): The bus where the fault occurred.
        value (Dict[float, Optional[float]]): Mapping from frequency to reduction factor.
    """

    name: Optional[str] = None  # Make name optional with a default value
    fault_bus: str
    value: Dict[float, Optional[float]]  # Mapping from frequency to reduction factor

    def __str__(self):
        return f"ResultReductionFactor(name={self.name}, reduction_factor={self.reduction_factor})"


class ResultGroundingImpedance(BaseModel):
    """
    Represents the grounding impedance results for a specific bus.

    Attributes:
        name (Optional[str]): The name of the grounding impedance result.
        fault_bus (str): The bus where the fault occurred.
        value (Dict[float, Optional[ComplexNumber]]): Mapping from frequency to grounding impedance.
    """

    name: Optional[str] = None  # Make name optional with a default value
    fault_bus: str
    value: Dict[
        float, Optional[ComplexNumber]
    ]  # Mapping from frequency to grounding impedance

    def __str__(self):
        return f"ResultGroundingImpedance(name={self.name}, grounding_impedance={self.grounding_impedance})"


class Result(BaseModel):
    """
    Represents the overall results of the network calculations.

    Attributes:
        buses (List[ResultBus]): A list of bus results.
        branches (List[ResultBranch]): A list of branch results.
        reduction_factor (Optional[ResultReductionFactor]): The reduction factor result, if any.
        grounding_impedance (Optional[ResultGroundingImpedance]): The grounding impedance result, if any.
        fault (str): The name of the active fault.
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
    Represents the path between a source and a fault bus within the network.

    Attributes:
        name (str): The name of the path.
        description (Optional[str]): A brief description of the path.
        source (str): The name of the source at the start of the path.
        fault (str): The name of the fault at the end of the path.
        segments (List[Branch]): A list of branches that make up the path.
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
    Represents the entire electrical network.

    Attributes:
        name (str): The name of the network.
        description (Optional[str]): A brief description of the network.
        frequencies (List[float]): A list of frequencies used in calculations.
        buses (Dict[str, Bus]): A dictionary of buses in the network.
        branches (Dict[str, Branch]): A dictionary of branches in the network.
        faults (Dict[str, Fault]): A dictionary of faults in the network.
        sources (Dict[str, Source]): A dictionary of sources in the network.
        results (Dict[str, Result]): A dictionary of results per fault.
        paths (Dict[str, Path]): A dictionary of paths within the network.
        active_fault (Optional[str]): The name of the currently active fault.
        _electrical_network (Optional["ElectricalNetwork"]): A private attribute for the electrical network.
    """

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

    @property
    def electrical_network(self):
        return self._electrical_network

    @electrical_network.setter
    def electrical_network(self, value):
        self._electrical_network = value

    def set_active_fault(self, fault_name: str):
        """
        Sets the specified fault as active and deactivates all other faults.

        Args:
            fault_name (str): The name of the fault to activate.

        Raises:
            ValueError: If the specified fault does not exist in the network.
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

        # Clear previous results for the fault
        if fault_name in self.results:
            del self.results[fault_name]

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
                    path_signature = tuple(branch.name for branch in path.segments)
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

    def add_bus(self, bus: Bus, overwrite: bool = False):
        """
        Adds a bus to the network.

        Args:
            bus (Bus): The bus instance to add.
            overwrite (bool, optional): If True, overwrites an existing bus with the same name. Defaults to False.

        Raises:
            ValueError: If a bus with the same name already exists and overwrite is False.
        """
        if bus.name in self.buses:
            if overwrite:
                print(f"Bus '{bus.name}' already exists in the network. Overwriting.")
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

        Args:
            branch (Branch): The branch instance to add.
            overwrite (bool, optional): If True, overwrites an existing branch with the same name. Defaults to False.

        Raises:
            ValueError: If a branch with the same name already exists, or if the connected buses are not in the network.
        """
        if branch.name in self.branches:
            if overwrite:
                print(
                    f"Branch '{branch.name}' already exists in the network. Overwriting."
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

        Args:
            fault (Fault): The fault instance to add.
            overwrite (bool, optional): If True, overwrites an existing fault with the same name. Defaults to False.

        Raises:
            ValueError: If a fault with the same name already exists, or if the associated bus is not in the network.
        """
        if fault.bus not in self.buses:
            raise ValueError(f"bus '{fault.bus}' is not in the network '{self.name}'")

        if fault.name in self.faults:
            if overwrite:
                print(
                    f"Fault '{fault.name}' already exists in the network. Overwriting."
                )
            else:
                raise ValueError(
                    f"Fault with name '{fault.name}' already exists in the network '{self.name}'. If you want to overwrite, set overwrite=True."
                )

        self.faults[fault.name] = fault

    def add_source(self, source: Source, overwrite: bool = False):
        """
        Adds a source to the network.

        Args:
            source (Source): The source instance to add.
            overwrite (bool, optional): If True, overwrites an existing source with the same name. Defaults to False.

        Raises:
            ValueError: If a source with the same name already exists, or if the associated bus is not in the network.
        """
        if source.bus not in self.buses:
            raise ValueError(f"bus '{source.bus}' is not in the network '{self.name}'")

        if source.name in self.sources:
            if overwrite:
                print(
                    f"Source '{source.name}' already exists in the network. Overwriting."
                )
            else:
                raise ValueError(
                    f"Source with name '{source.name}' already exists in the network '{self.name}'. If you want to overwrite, set overwrite=True."
                )
        self.sources[source.name] = source

    def add_path(self, path: Path):
        """
        Adds a path to the network.

        Args:
            path (Path): The path instance to add.
        """
        self.paths[path.name] = path

    def res_buses(self, fault: Optional[str] = None) -> pl.DataFrame:
        """
        Returns a Polars DataFrame with bus results for the specified fault.

        If no fault is specified, returns results for the active fault.

        Args:
            fault (Optional[str], optional): The name of the fault. Defaults to None.

        Returns:
            pl.DataFrame: A DataFrame containing bus results.

        Raises:
            ValueError: If no active fault is set or if results for the specified fault are unavailable.
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

        Args:
            fault (Optional[str], optional): The name of the fault. Defaults to None.

        Returns:
            pl.DataFrame: A DataFrame containing branch results.

        Raises:
            ValueError: If no active fault is set or if results for the specified fault are unavailable.
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

        Returns:
            pl.DataFrame: A DataFrame containing grounding impedance and reduction factors.

        Notes:
            - Faults without results are skipped.
            - Missing grounding impedance or reduction factor results are noted.
        """
        data = []
        for fault_name, fault in self.faults.items():
            if fault_name not in self.results:
                print(f"No results available for fault '{fault_name}'. Skipping.")
                continue
            result = self.results[fault_name]
            fault_bus = fault.bus

            # Grounding Impedance
            grounding_impedance = result.grounding_impedance
            if not grounding_impedance:
                print(f"No grounding impedance results for fault '{fault_name}'.")
                continue

            # Reduction Factor
            reduction_factor = result.reduction_factor
            if not reduction_factor:
                print(f"No reduction factor results for fault '{fault_name}'.")
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

# tests/test_core_models.py

from pydantic import ValidationError
import pytest
from groundinsight.models.core_models import (
    ComplexNumber,
    BusType,
    Bus,
    BranchType,
    Branch,
    Fault,
    Source,
    ResultBus,
    ResultBranch,
    ResultReductionFactor,
    ResultGroundingImpedance,
    Result,
    Path,
    Network,
)
from groundinsight.utils.validations import validate_impedance_formula_value
import numpy as np
from sympy import sympify, symbols, I, pi  # Import pi from sympy



# Test ComplexNumber class
def test_complex_number_initialization():
    cn1 = ComplexNumber(real=3.0, imag=4.0)
    assert cn1.real == 3.0
    assert cn1.imag == 4.0

    cn2 = ComplexNumber(real=5, imag=-2)
    assert cn2.real == 5.0
    assert cn2.imag == -2.0

    cn3 = ComplexNumber(real=3, imag=4)
    assert cn3.real == 3.0
    assert cn3.imag == 4.0

    cn4 = ComplexNumber(real=3.5, imag=0)
    assert cn4.real == 3.5
    assert cn4.imag == 0.0

    cn5 = ComplexNumber(real="3", imag="4")
    assert cn5.real == 3.0
    assert cn5.imag == 4.0

    cn6 = ComplexNumber(real=3.0, imag=4.0)
    assert cn6.real == 3.0
    assert cn6.imag == 4.0

    cn7 = ComplexNumber(real=3, imag=4)
    assert cn7.real == 3.0
    assert cn7.imag == 4.0


def test_complex_number_operations():
    cn1 = ComplexNumber(real=1, imag=2)
    cn2 = ComplexNumber(real=3, imag=4)

    # Addition
    cn_add = cn1 + cn2
    assert cn_add.real == 4.0
    assert cn_add.imag == 6.0

    # Subtraction
    cn_sub = cn1 - cn2
    assert cn_sub.real == -2.0
    assert cn_sub.imag == -2.0

    # Multiplication
    cn_mul = cn1 * cn2
    assert cn_mul.real == -5.0
    assert cn_mul.imag == 10.0

    # Division
    cn_div = cn1 / cn2
    assert round(cn_div.real, 5) == 0.44
    assert round(cn_div.imag, 5) == 0.08

    # Negative
    cn_neg = -cn1
    assert cn_neg.real == -1.0
    assert cn_neg.imag == -2.0

    # Absolute value
    assert abs(cn1) == np.sqrt(1**2 + 2**2)

    # Exponentiation
    cn_pow = cn1 ** 2
    assert cn_pow.real == -3.0
    assert cn_pow.imag == 4.0

    # Equality
    cn3 = ComplexNumber(real=1, imag=2)
    assert cn1 == cn3
    assert cn1 != cn2


def test_complex_number_type_conversion():
    cn1 = ComplexNumber(real=3, imag=4)
    c = complex(cn1)
    assert c.real == 3.0
    assert c.imag == 4.0

    cn2 = cn1 + 5  # Adding an int
    assert cn2.real == 8.0
    assert cn2.imag == 4.0

    cn3 = cn1 * 2  # Multiplying by an int
    assert cn3.real == 6.0
    assert cn3.imag == 8.0

    cn4 = cn1 / 2  # Dividing by an int
    assert cn4.real == 1.5
    assert cn4.imag == 2.0

    with pytest.raises(TypeError):
        cn1 + "invalid"

    with pytest.raises(TypeError):
        cn1 * "invalid"

    with pytest.raises(TypeError):
        cn1 / "invalid"


def test_bustype_initialization_valid():
    """
    Test creating a BusType object with valid data.
    """
    bus_type = BusType(
        name="Type A",
        description="A high voltage bus type",
        system_type="AC",
        voltage_level=230.0,
        impedance_formula="1+roh+f",
    )
    assert bus_type.name == "Type A"
    assert bus_type.description == "A high voltage bus type"
    assert bus_type.system_type == "AC"
    assert bus_type.voltage_level == 230.0
    assert bus_type.impedance_formula == "1+roh+f"

def test_bustype_initialization_missing_optional():
    """
    Test creating a BusType object without the optional description.
    """
    bus_type = BusType(
        name="Type B",
        system_type="DC",
        voltage_level=110.0,
        impedance_formula="1+2",
    )
    assert bus_type.description is None
    assert bus_type.system_type == "DC"
    assert bus_type.voltage_level == 110.0
    assert bus_type.impedance_formula == "1+2"

def test_bustype_initialization_invalid():
    """
    Test creating a BusType object with invalid data.
    """
    #invalid formula
    with pytest.raises(ValueError):
        BusType(
            name="Type D",
            description="A low voltage bus type",
            system_type="AC",
            voltage_level=230.0,
            impedance_formula="1+2+",
        )

    #invalid name
    with pytest.raises(ValueError):
        BusType(
            name=1,
            description="A low voltage bus type",
            system_type="AC",
            voltage_level=230.0,
            impedance_formula="1+2",
        )
    
    #invalid voltage level
    with pytest.raises(ValueError):
        BusType(
            name="Type D",
            description="A low voltage bus type",
            system_type="AC",
            voltage_level="ABC",
            impedance_formula="1+2",
        )

def test_branchtype_initialization_valid():
    """
    Test creating a BranchType object with valid data.
    """
    branch_type = BranchType(
        name="Type A",
        description="A high voltage branch type",
        grounding_conductor=True,
        self_impedance_formula="1+roh+f",
        mutual_impedance_formula="1+roh+f",
    )
    assert branch_type.name == "Type A"
    assert branch_type.description == "A high voltage branch type"
    assert branch_type.self_impedance_formula == "1+roh+f"
    assert branch_type.mutual_impedance_formula == "1+roh+f"
    assert branch_type.grounding_conductor == True

def test_branchtype_initialization_missing_optional():
    """
    Test creating a BranchType object without the optional description.
    """
    branch_type = BranchType(
        name="Type B",
        grounding_conductor=False,
        self_impedance_formula="1+2",
        mutual_impedance_formula="1+2",
    )
    assert branch_type.description is None
    assert branch_type.self_impedance_formula == "1+2"
    assert branch_type.mutual_impedance_formula == "1+2"
    assert branch_type.grounding_conductor == False

def test_branchtype_initialization_invalid():
    """
    Test creating a BranchType object with invalid data.
    """
    #invalid formula
    with pytest.raises(ValueError):
        BranchType(
            name="Type D",
            description="A low voltage branch type",
            grounding_conductor=True,
            self_impedance_formula="1+2+",
            mutual_impedance_formula="1+2",
        )

    #invalid name
    with pytest.raises(ValueError):
        BranchType(
            name=1,
            description="A low voltage branch type",
            grounding_conductor=True,
            self_impedance_formula="1+2",
            mutual_impedance_formula="1+2",
        )

    #invalid grounding_conductor
    with pytest.raises(ValueError):
        BranchType(
            name="Type D",
            description="A low voltage branch type",
            grounding_conductor="ABC",
            self_impedance_formula="1+2",
            mutual_impedance_formula="1+2",
        )

def test_bus_initialization_valid():
    """
    Test creating a Bus object with valid data.
    """
    bus_type = BusType(
        name="Type A",
        description="A high voltage bus type",
        system_type="AC",
        voltage_level=230.0,
        impedance_formula="1+roh+f",
    )
    bus = Bus(
        name="Bus A",
        type=bus_type,
        description="A high voltage bus",
        specific_earth_resistance=200,
        impedance={50: 1+200+50},
    )

    assert bus.name == "Bus A"
    assert bus.type == bus_type
    assert bus.description == "A high voltage bus"
    assert bus.specific_earth_resistance == 200
    assert bus.impedance == {50: 1+200+50}

def test_bus_initialization_missing_optional():
    """
    Test creating a Bus object without the optional description.
    """
    bus_type = BusType(
        name="Type A",
        description="A high voltage bus type",
        system_type="AC",
        voltage_level=230.0,
        impedance_formula="1+roh+f",
    )
    bus = Bus(
        name="Bus A",
        type=bus_type,
        specific_earth_resistance=200,
        impedance={50: 1+200+50},
    )

    assert bus.description is None
    assert bus.name == "Bus A"
    assert bus.type == bus_type
    assert bus.specific_earth_resistance == 200
    assert bus.impedance == {50: 1+200+50}

def test_bus_initialization_invalid():
    """
    Test creating a Bus object with invalid data.
    """
    #invalid impedance
    with pytest.raises(ValueError):
        bus_type = BusType(
            name="Type A",
            description="A high voltage bus type",
            system_type="AC",
            voltage_level=230.0,
            impedance_formula="1+roh+f",
        )
        bus = Bus(
            name="Bus A",
            type=bus_type,
            specific_earth_resistance=200,
            impedance={50: 1+200+50, 60: "1+200+60"},
        )

    #invalid name
    with pytest.raises(ValueError):
        bus_type = BusType(
            name="Type A",
            description="A high voltage bus type",
            system_type="AC",
            voltage_level=230.0,
            impedance_formula="1+roh+f",
        )
        bus = Bus(
            name=1,
            type=bus_type,
            specific_earth_resistance=200,
            impedance={50: 1+200+50},
        )

    #invalid specific_earth_resistance
    with pytest.raises(ValueError):
        bus_type = BusType(
            name="Type A",
            description="A high voltage bus type",
            system_type="AC",
            voltage_level=230.0,
            impedance_formula="1+roh+f",
        )
        bus = Bus(
            name="Bus A",
            type=bus_type,
            specific_earth_resistance="ABC",
            impedance={50: 1+200+50},
        )

def test_branch_initialization_valid():
    """
    Test creating a Branch object with valid data.
    """
    branch_type = BranchType(
        name="Type A",
        description="A high voltage branch type",
        grounding_conductor=True,
        self_impedance_formula="1+roh+f",
        mutual_impedance_formula="1+roh+f",
    )
    branch = Branch(
        name="Branch A",
        description="Description",
        type=branch_type,
        length=100,
        from_bus="Bus A",
        to_bus="Bus B",
        self_impedance={50: 1+200+50},
        mutual_impedance={50: 1+200+50},
        specific_earth_resistance = 200.0,
        parallel_coefficient= 0.1
    )

    assert branch.name == "Branch A"
    assert branch.type == branch_type
    assert branch.from_bus == "Bus A"
    assert branch.to_bus == "Bus B"
    assert branch.length == 100
    assert branch.specific_earth_resistance == 200
    assert branch.self_impedance == {50: 1+200+50}
    assert branch.mutual_impedance == {50: 1+200+50}
    assert branch.parallel_coefficient == 0.1

def test_branch_initialization_missing_optional():
    """
    Test creating a Branch object without the optional description.
    """
    branch_type = BranchType(
        name="Type A",
        description="A high voltage branch type",
        grounding_conductor=True,
        self_impedance_formula="1+roh+f",
        mutual_impedance_formula="1+roh+f",
    )
    branch = Branch(
        name="Branch A",
        type=branch_type,
        length=100,
        from_bus="Bus A",
        to_bus="Bus B",
        self_impedance={50: 1+200+50},
        mutual_impedance={50: 1+200+50},
        specific_earth_resistance = 200.0,
        parallel_coefficient= 0.1
    )

    assert branch.description is None
    assert branch.name == "Branch A"
    assert branch.type == branch_type
    assert branch.from_bus == "Bus A"
    assert branch.to_bus == "Bus B"
    assert branch.length == 100
    assert branch.specific_earth_resistance == 200
    assert branch.self_impedance == {50: 1+200+50}
    assert branch.mutual_impedance == {50: 1+200+50}
    assert branch.parallel_coefficient == 0.1

def test_branch_initialization_invalid():
    """
    Test creating a Branch object with invalid data.
    """
    #invalid impedance
    with pytest.raises(ValueError):
        branch_type = BranchType(
            name="Type A",
            description="A high voltage branch type",
            grounding_conductor=True,
            self_impedance_formula="1+roh+f",
            mutual_impedance_formula="1+roh+f",
        )
        branch = Branch(
            name="Branch A",
            type=branch_type,
            length=100,
            from_bus="Bus A",
            to_bus="Bus B",
            self_impedance={50: 1+200+50, 60: "1+200+60"},
            mutual_impedance={50: 1+200+50},
            specific_earth_resistance = 200.0,
            parallel_coefficient= 0.1
        )

    #invalid name
    with pytest.raises(ValueError):
        branch_type = BranchType(
            name="Type A",
            description="A high voltage branch type",
            grounding_conductor=True,
            self_impedance_formula="1+roh+f",
            mutual_impedance_formula="1+roh+f",
        )
        branch = Branch(
            name=1,
            type=branch_type,
            length=100,
            from_bus="Bus A",
            to_bus="Bus B",
            self_impedance={50: 1+200+50},
            mutual_impedance={50: 1+200+50},
            specific_earth_resistance = 200.0,
            parallel_coefficient= 0.1
        )

    #invalid specific_earth_resistance
    with pytest.raises(ValueError):
        branch_type = BranchType(
            name="Type A",
            description="A high voltage branch type",
            grounding_conductor=True,
            self_impedance_formula="1+roh+f",
            mutual_impedance_formula="1+roh+f",
        )
        branch = Branch(
            name="Branch A",
            type=branch_type,
            length=100,
            from_bus="Bus A",
            to_bus="Bus B",
            self_impedance={50: 1+200+50},
            mutual_impedance={50: 1+200+50},
            specific_earth_resistance = "ABC",
            parallel_coefficient= 0.1
        )

    #invalid parallel_coefficient
    with pytest.raises(ValueError):
        branch_type = BranchType(
            name="Type A",
            description="A high voltage branch type",
            grounding_conductor=True,
            self_impedance_formula="1+roh+f",
            mutual_impedance_formula="1+roh+f",
        )
        branch = Branch(
            name="Branch A",
            type=branch_type,
            length=100,
            from_bus="Bus A",
            to_bus="Bus B",
            self_impedance={50: 1+200+50},
            mutual_impedance={50: 1+200+50},
            specific_earth_resistance = 200.0,
            parallel_coefficient= "ABC"
        )

def test_fault_initialization_valid():
    """
    Test creating a Fault object with valid data.

    name: str
    description: Optional[str] = None
    bus: str  # Location of the fault
    scalings: Dict[float, float] = {}  # Scaling factors for sources
    _active: bool = PrivateAttr(default=False)

    """
    fault = Fault(
        name="Fault A",
        description="Description",
        bus="Bus A",
        scalings={50: 1.0},
    )

    assert fault.name == "Fault A"
    assert fault.description == "Description"
    assert fault.bus == "Bus A"
    assert fault.scalings == {50: 1.0}
    assert fault.active == False

def test_fault_initialization_missing_optional():
    """
    Test creating a Fault object without the optional description.
    """
    fault = Fault(
        name="Fault A",
        bus="Bus A",
        scalings={50: 1.0},
    )

    assert fault.description is None
    assert fault.name == "Fault A"
    assert fault.bus == "Bus A"
    assert fault.scalings == {50: 1.0}
    assert fault.active == False

def test_fault_initialization_invalid():
    """
    Test creating a Fault object with invalid data.
    """
    #invalid scalings
    with pytest.raises(ValueError):
        fault = Fault(
            name="Fault A",
            description="Description",
            bus="Bus A",
            scalings={50: 1.0, 60: "ABC"},
        )

    #invalid name
    with pytest.raises(ValueError):
        fault = Fault(
            name=1,
            description="Description",
            bus="Bus A",
            scalings={50: 1.0},
        )

    #invalid bus
    with pytest.raises(ValueError):
        fault = Fault(
            name="Fault A",
            description="Description",
            bus=1,
            scalings={50: 1.0},
        )

def test_source_initialization_valid():
    """
    Test creating a Source object with valid data.

    name: str
    description: Optional[str] = None
    bus: str  # Location of the source
    values: Dict[float, ComplexNumber]  # {frequency: current value}
    """
    source = Source(
        name="Source A",
        description="Description",
        bus="Bus A",
        values={50: 1.0+2.0j},
    )

    assert source.name == "Source A"
    assert source.description == "Description"
    assert source.bus == "Bus A"
    assert source.values == {50: 1.0+2.0j}

def test_source_initialization_missing_optional():
    """
    Test creating a Source object without the optional description.
    """
    source = Source(
        name="Source A",
        bus="Bus A",
        values={50: 1.0+2.0j},
    )

    assert source.description is None
    assert source.name == "Source A"
    assert source.bus == "Bus A"
    assert source.values == {50: 1.0+2.0j}

def test_source_initialization_invalid():
    """
    Test creating a Source object with invalid data.
    """
    #invalid values
    with pytest.raises(ValueError):
        source = Source(
            name="Source A",
            description="Description",
            bus="Bus A",
            values={50: 1.0+2.0j, 60: "ABC"},
        )

    #invalid name
    with pytest.raises(ValueError):
        source = Source(
            name=1,
            description="Description",
            bus="Bus A",
            values={50: 1.0+2.0j},
        )

    #invalid bus
    with pytest.raises(ValueError):
        source = Source(
            name="Source A",
            description="Description",
            bus=1,
            values={50: 1.0+2.0j},
        )

def test_resultbus_initialization_valid():
    """
    name: str  # name of the bus
    uepr: float  # Earth potential rise
    ia: float  # Current
    uepr_freq: Dict[float, ComplexNumber]  # {frequency: voltage}
    ia_freq: Dict[float, ComplexNumber]  # {frequency: current}
    """
    resultbus = ResultBus(
        name="Bus A",
        uepr=1.0,
        ia=2.0,
        uepr_freq={50: 1.0+2.0j},
        ia_freq={50: 1.0+2.0j},
    )

    assert resultbus.name == "Bus A"
    assert resultbus.uepr == 1.0
    assert resultbus.ia == 2.0
    assert resultbus.uepr_freq == {50: 1.0+2.0j}
    assert resultbus.ia_freq == {50: 1.0+2.0j}

def test_resultbus_initialization_invalid():
    """
    Test creating a ResultBus object with invalid data.
    """
    #invalid uepr_freq
    with pytest.raises(ValueError):
        resultbus = ResultBus(
            name="Bus A",
            uepr=1.0,
            ia=2.0,
            uepr_freq={50: 1.0+2.0j, 60: "ABC"},
            ia_freq={50: 1.0+2.0j},
        )

    #invalid name
    with pytest.raises(ValueError):
        resultbus = ResultBus(
            name=1,
            uepr=1.0,
            ia=2.0,
            uepr_freq={50: 1.0+2.0j},
            ia_freq={50: 1.0+2.0j},
        )

    #invalid uepr
    with pytest.raises(ValueError):
        resultbus = ResultBus(
            name="Bus A",
            uepr="ABC",
            ia=2.0,
            uepr_freq={50: 1.0+2.0j},
            ia_freq={50: 1.0+2.0j},
        )

    #invalid ia
    with pytest.raises(ValueError):
        resultbus = ResultBus(
            name="Bus A",
            uepr=1.0,
            ia="ABC",
            uepr_freq={50: 1.0+2.0j},
            ia_freq={50: 1.0+2.0j},
        )

def test_resultbranch_initialization_valid():
    """
    name: str  # name of the branch
    i_s: float  # Shield current
    i_s_freq: Dict[float, ComplexNumber]  # {frequency: current}
    """
    resultbranch = ResultBranch(
        name="Branch A",
        i_s=1.0,
        i_s_freq={50: 1.0+2.0j},
    )

    assert resultbranch.name == "Branch A"
    assert resultbranch.i_s == 1.0
    assert resultbranch.i_s_freq == {50: 1.0+2.0j}

def test_resultbranch_initialization_invalid():
    """
    Test creating a ResultBranch object with invalid data.
    """
    #invalid i_s_freq
    with pytest.raises(ValueError):
        resultbranch = ResultBranch(
            name="Branch A",
            i_s=1.0,
            i_s_freq={50: 1.0+2.0j, 60: "ABC"},
        )

    #invalid name
    with pytest.raises(ValueError):
        resultbranch = ResultBranch(
            name=1,
            i_s=1.0,
            i_s_freq={50: 1.0+2.0j},
        )

    #invalid i_s
    with pytest.raises(ValueError):
        resultbranch = ResultBranch(
            name="Branch A",
            i_s="ABC",
            i_s_freq={50: 1.0+2.0j},
        )

def test_resultreductionfactor_initialization_valid():
    """
    name: Optional[str] = None  # Make name optional with a default value
    fault_bus: str
    value: Dict[float, Optional[float]]  # Mapping from frequency to reduction factor
    """
    resultreductionfactor = ResultReductionFactor(
        fault_bus="Bus A",
        value={50: 1.0},
    )

    assert resultreductionfactor.name is None
    assert resultreductionfactor.fault_bus == "Bus A"
    assert resultreductionfactor.value == {50: 1.0}

def test_resultreductionfactor_initialization_invalid():
    """
    Test creating a ResultReductionFactor object with invalid data.
    """
    #invalid value
    with pytest.raises(ValueError):
        resultreductionfactor = ResultReductionFactor(
            fault_bus="Bus A",
            value={50: "ABC"},
        )

    #invalid fault_bus
    with pytest.raises(ValueError):
        resultreductionfactor = ResultReductionFactor(
            fault_bus=1,
            value={50: 1.0},
        )

def test_resultgroundingimpedance_initialization_valid():
    """
    name: Optional[str] = None  # Make name optional with a default value
    fault_bus: str
    value: Dict[
        float, Optional[ComplexNumber]
    ]  # Mapping from frequency to grounding impedance
    """
    resultgroundingimpedance = ResultGroundingImpedance(
        fault_bus="Bus A",
        value={50: 1.0+2.0j},
    )

    assert resultgroundingimpedance.name is None
    assert resultgroundingimpedance.fault_bus == "Bus A"
    assert resultgroundingimpedance.value == {50: 1.0+2.0j}

def test_resultgroundingimpedance_initialization_invalid():
    """
    Test creating a ResultGroundingImpedance object with invalid data.
    """
    #invalid value
    with pytest.raises(ValueError):
        resultgroundingimpedance = ResultGroundingImpedance(
            fault_bus="Bus A",
            value={50: "ABC"},
        )

    #invalid fault_bus
    with pytest.raises(ValueError):
        resultgroundingimpedance = ResultGroundingImpedance(
            fault_bus=1,
            value={50: 1.0+2.0j},
        )

def test_result_initialization_valid():
    """
    buses: List[ResultBus] = []
    branches: List[ResultBranch] = []
    reduction_factor: Optional[ResultReductionFactor] = None
    grounding_impedance: Optional[ResultGroundingImpedance] = None
    fault: str = ""  # name of the fault that was active
    """
    result = Result(
        buses=[ResultBus(
            name="Bus A",
            uepr=1.0,
            ia=2.0,
            uepr_freq={50: 1.0+2.0j},
            ia_freq={50: 1.0+2.0j},
        )],
        branches=[ResultBranch(
            name="Branch A",
            i_s=1.0,
            i_s_freq={50: 1.0+2.0j},
        )],
        reduction_factor=ResultReductionFactor(
            fault_bus="Bus A",
            value={50: 1.0},
        ),
        grounding_impedance=ResultGroundingImpedance(
            fault_bus="Bus A",
            value={50: 1.0+2.0j},
        ),
        fault="Fault A"
    )

    assert result.buses[0].name == "Bus A"
    assert result.buses[0].uepr == 1.0
    assert result.buses[0].ia == 2.0
    assert result.buses[0].uepr_freq == {50: 1.0+2.0j}
    assert result.buses[0].ia_freq == {50: 1.0+2.0j}

    assert result.branches[0].name == "Branch A"
    assert result.branches[0].i_s == 1.0
    assert result.branches[0].i_s_freq == {50: 1.0+2.0j}

    assert result.reduction_factor.fault_bus == "Bus A"
    assert result.reduction_factor.value == {50: 1.0}

    assert result.grounding_impedance.fault_bus == "Bus A"
    assert result.grounding_impedance.value == {50: 1.0+2.0j}

    assert result.fault == "Fault A"

def test_result_initialization_invalid():
    """
    Test creating a Result object with invalid data.
    """
    #invalid buses
    with pytest.raises(ValueError):
        result = Result(
            buses=[ResultBus(
                name="Bus A",
                uepr=1.0,
                ia=2.0,
                uepr_freq={50: 1.0+2.0j},
                ia_freq={50: 1.0+2.0j},
            ), ResultBus(
                name=1,
                uepr=1.0,
                ia=2.0,
                uepr_freq={50: 1.0+2.0j},
                ia_freq={50: 1.0+2.0j},
            )],
            branches=[ResultBranch(
                name="Branch A",
                i_s=1.0,
                i_s_freq={50: 1.0+2.0j},
            )],
            reduction_factor=ResultReductionFactor(
                fault_bus="Bus A",
                value={50: 1.0},
            ),
            grounding_impedance=ResultGroundingImpedance(
                fault_bus="Bus A",
                value={50: 1.0+2.0j},
            ),
            fault="Fault A"
        )

    #invalid branches
    with pytest.raises(ValueError):
        result = Result(
            buses=[ResultBus(
                name="Bus A",
                uepr=1.0,
                ia=2.0,
                uepr_freq={50: 1.0+2.0j},
                ia_freq={50: 1.0+2.0j},
            )],
            branches=[ResultBranch(
                name="Branch A",
                i_s=1.0,
                i_s_freq={50: 1.0+2.0j},
            ), ResultBranch(
                name=1,
                i_s=1.0,
                i_s_freq={50: 1.0+2.0j},
            )],
            reduction_factor=ResultReductionFactor(
                fault_bus="Bus A",
                value={50: 1.0},
            ),
            grounding_impedance=ResultGroundingImpedance(
                fault_bus="Bus A",
                value={50: 1.0+2.0j},
            ),
            fault="Fault A"
        )

    #invalid reduction_factor
    with pytest.raises(ValueError):
        result = Result(
            buses=[ResultBus (
                name="Bus A",
                uepr=1.0,
                ia=2.0,
                uepr_freq={50: 1.0+2.0j},
                ia_freq={50: 1.0+2.0j},
            )],
            branches=[ResultBranch(
                name="Branch A",
                i_s=1.0,
                i_s_freq={50: 1.0+2.0j},
            )],
            reduction_factor=ResultReductionFactor(
                fault_bus=1,
                value={50: 1.0},
            ),
            grounding_impedance=ResultGroundingImpedance(
                fault_bus="Bus A",
                value={50: 1.0+2.0j},
            ),
            fault="Fault A"
        )

    #invalid grounding_impedance
    with pytest.raises(ValueError):
        result = Result(
            buses=[ResultBus (
                name="Bus A",
                uepr=1.0,
                ia=2.0,
                uepr_freq={50: 1.0+2.0j},
                ia_freq={50: 1.0+2.0j},
            )],
            branches=[ResultBranch(
                name="Branch A",
                i_s=1.0,
                i_s_freq={50: 1.0+2.0j},
            )],
            reduction_factor=ResultReductionFactor(
                fault_bus="Bus A",
                value={50: 1.0},
            ),
            grounding_impedance=ResultGroundingImpedance(
                fault_bus=1,
                value={50: 1.0+2.0j},
            ),
            fault="Fault A"
        )

    #invalid fault
    with pytest.raises(ValueError):
        result = Result(
            buses=[ResultBus (
                name="Bus A",
                uepr=1.0,
                ia=2.0,
                uepr_freq={50: 1.0+2.0j},
                ia_freq={50: 1.0+2.0j},
            )],
            branches=[ResultBranch(
                name="Branch A",
                i_s=1.0,
                i_s_freq={50: 1.0+2.0j},
            )],
            reduction_factor=ResultReductionFactor(
                fault_bus="Bus A",
                value={50: 1.0},
            ),
            grounding_impedance=ResultGroundingImpedance(
                fault_bus="Bus A",
                value={50: 1.0+2.0j},
            ),
            fault=1
        )

def test_path_initialization_valid():
    """
    name: str
    description: Optional[str] = None
    source: str
    fault: str
    segments: List[Branch] = []
    """
    path = Path(
        name="Path A",
        description="Description",
        source="Source A",
        fault="Fault A",
        segments=[Branch(
            name="Branch A",
            description="Description",
            type=BranchType(
                name="Type A",
                description="A high voltage branch type",
                grounding_conductor=True,
                self_impedance_formula="1+roh+f",
                mutual_impedance_formula="1+roh+f",
            ),
            length=100,
            from_bus="Bus A",
            to_bus="Bus B",
            self_impedance={50: 1+200+50},
            mutual_impedance={50: 1+200+50},
            specific_earth_resistance = 200.0,
            parallel_coefficient= 0.1
        )]
    )

    assert path.name == "Path A"
    assert path.description == "Description"
    assert path.source == "Source A"
    assert path.fault == "Fault A"
    assert path.segments[0].name == "Branch A"
    assert path.segments[0].description == "Description"
    assert path.segments[0].type.name == "Type A"
    assert path.segments[0].length == 100
    assert path.segments[0].from_bus == "Bus A"
    assert path.segments[0].to_bus == "Bus B"
    assert path.segments[0].self_impedance == {50: 1+200+50}
    assert path.segments[0].mutual_impedance == {50: 1+200+50}
    assert path.segments[0].specific_earth_resistance == 200.0
    assert path.segments[0].parallel_coefficient == 0.1

def test_path_initialization_missing_optional():
    """
    Test creating a Path object without the optional description.
    """
    path = Path(
        name="Path A",
        source="Source A",
        fault="Fault A",
        segments=[Branch(
            name="Branch A",
            type=BranchType(
                name="Type A",
                description="A high voltage branch type",
                grounding_conductor=True,
                self_impedance_formula="1+roh+f",
                mutual_impedance_formula="1+roh+f",
            ),
            length=100,
            from_bus="Bus A",
            to_bus="Bus B",
            self_impedance={50: 1+200+50},
            mutual_impedance={50: 1+200+50},
            specific_earth_resistance = 200.0,
            parallel_coefficient= 0.1
        )]
    )

    assert path.description is None
    assert path.name == "Path A"
    assert path.source == "Source A"
    assert path.fault == "Fault A"
    assert path.segments[0].name == "Branch A"
    assert path.segments[0].type.name == "Type A"
    assert path.segments[0].length == 100
    assert path.segments[0].from_bus == "Bus A"
    assert path.segments[0].to_bus == "Bus B"
    assert path.segments[0].self_impedance == {50: 1+200+50}
    assert path.segments[0].mutual_impedance == {50: 1+200+50}
    assert path.segments[0].specific_earth_resistance == 200.0
    assert path.segments[0].parallel_coefficient == 0.1

def test_path_initialization_invalid():
    """
    Test creating a Path object with invalid data.
    """
    #invalid segments
    with pytest.raises(ValueError):
        path = Path(
            name="Path A",
            description="Description",
            source="Source A",
            fault="Fault A",
            segments=[Branch(
                name="Branch A",
                description="Description",
                type=BranchType(
                    name="Type A",
                    description="A high voltage branch type",
                    grounding_conductor=True,
                    self_impedance_formula="1+roh+f",
                    mutual_impedance_formula="1+roh+f",
                ),
                length=100,
                from_bus="Bus A",
                to_bus="Bus B",
                self_impedance={50: 1+200+50},
                mutual_impedance={50: 1+200+50},
                specific_earth_resistance = 200.0,
                parallel_coefficient= 0.1
            ), Branch(
                name=1,
                description="Description",
                type=BranchType(
                    name="Type A",
                    description="A high voltage branch type",
                    grounding_conductor=True,
                    self_impedance_formula="1+roh+f",
                    mutual_impedance_formula="1+roh+f",
                ),
                length=100,
                from_bus="Bus A",
                to_bus="Bus B",
                self_impedance={50: 1+200+50},
                mutual_impedance={50: 1+200+50},
                specific_earth_resistance = 200.0,
                parallel_coefficient= 0.1
            )]
        )

    #invalid name
    with pytest.raises(ValueError):
        path = Path(
            name=1,
            description="Description",
            source="Source A",
            fault="Fault A",
            segments=[Branch(
                name="Branch A",
                description="Description",
                type=BranchType(
                    name="Type A",
                    description="A high voltage branch type",
                    grounding_conductor=True,
                    self_impedance_formula="1+roh+f",
                    mutual_impedance_formula="1+roh+f",
                ),
                length=100,
                from_bus="Bus A",
                to_bus="Bus B",
                self_impedance={50: 1+200+50},
                mutual_impedance={50: 1+200+50},
                specific_earth_resistance = 200.0,
                parallel_coefficient= 0.1
            )]
        )

    #invalid source
    with pytest.raises(ValueError):
        path = Path(
            name="Path A",
            description="Description",
            source=1,
            fault="Fault A",
            segments=[Branch(
                name="Branch A",
                description="Description",
                type=BranchType(
                    name="Type A",
                    description="A high voltage branch type",
                    grounding_conductor=True,
                    self_impedance_formula="1+roh+f",
                    mutual_impedance_formula="1+roh+f",
                ),
                length=100,
                from_bus="Bus A",
                to_bus="Bus B",
                self_impedance={50: 1+200+50},
                mutual_impedance={50: 1+200+50},
                specific_earth_resistance = 200.0,
                parallel_coefficient= 0.1
            )]
        )

    #invalid fault
    with pytest.raises(ValueError):
        path = Path(
            name="Path A",
            description="Description",
            source="Source A",
            fault=1,
            segments=[Branch(
                name="Branch A",
                description="Description",
                type=BranchType(
                    name="Type A",
                    description="A high voltage branch type",
                    grounding_conductor=True,
                    self_impedance_formula="1+roh+f",
                    mutual_impedance_formula="1+roh+f",
                ),
                length=100,
                from_bus="Bus A",
                to_bus="Bus B",
                self_impedance={50: 1+200+50},
                mutual_impedance={50: 1+200+50},
                specific_earth_resistance = 200.0,
                parallel_coefficient= 0.1
            )]
        )

def test_network_initialization_valid():
    """
    Test creating a Network object with valid data.
    """
    network = Network(
        name="Test Network",
        description="This is a test network",
        frequencies=[50.0, 60.0]
    )
    assert network.name == "Test Network"
    assert network.description == "This is a test network"
    assert network.frequencies == [50.0, 60.0]
    assert network.buses == {}
    assert network.branches == {}
    assert network.faults == {}
    assert network.sources == {}
    assert network.results == {}
    assert network.paths == {}
    assert network.active_fault is None
    assert network.electrical_network is None

def test_network_electrical_network_property():
    """
    Test getter and setter for electrical_network property.
    """
    network = Network(
        name="Test Network",
        frequencies=[50.0]
    )
    assert network.electrical_network is None

    mock_network = "Mock Electrical Network"
    network.electrical_network = mock_network
    assert network.electrical_network == mock_network

def test_network_set_active_fault():
    """
    Test setting an active fault in the network.
    """
    mock_fault = Fault(name="Fault A", bus="Bus 1")
    network = Network(name="Test Network", frequencies=[50.0], faults={"Fault A": mock_fault})

    network.set_active_fault("Fault A")
    assert network.active_fault == "Fault A"
    assert mock_fault.active == True

    with pytest.raises(ValueError, match="Fault 'Nonexistent Fault' does not exist"):
        network.set_active_fault("Nonexistent Fault")



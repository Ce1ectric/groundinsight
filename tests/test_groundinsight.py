import groundinsight as gi
from groundinsight.models.core_models import BusType, BranchType


def test_network_initialization():
    net = gi.create_network(name="MyTestNetwork", frequencies=[50])
    net.description = "That's my first test network"

    assert net.name == "MyTestNetwork"
    assert net.frequencies == [50]
    assert net.description == "That's my first test network"
    assert len(net.buses) == 0
    assert len(net.branches) == 0

def test_network_assistant_creation():
    bus_type = BusType(
        name="BusTypeFormulaTest",
        description="Example bus type with parameters",
        system_type="Grounded",
        voltage_level=230.0,
        impedance_formula="rho * 0 + 1 + I * f * 1/50",
    )

    branch_type = BranchType(
        name="TestBranchType",
        description="A test branch type",
        carry_current=True,
        self_impedance_formula="(rho * 0 + 0.25 + I * f * 0.012)*l",
        mutual_impedance_formula="(rho * 0 + 0.0 + I * f * 0.010)*l"
    )

    net = gi.create_network_assistant(name="MyTestNetwork", frequencies=[50], number_buses=10,
                                      bus_type=bus_type, branch_type=branch_type, branch_length=[1]*10, specific_earth_resistance=100)

    assert net.name == "MyTestNetwork"
    assert net.frequencies == [50]
    assert len(net.buses) == 10
    assert len(net.branches) == 9

def test_bus_and_branch_creation():
    net = gi.create_network(name="TestNet", frequencies=[50])

    bus_type = BusType(
        name="BusTypeFormulaTest",
        system_type="Grounded",
        voltage_level=230.0,
        impedance_formula="rho * 0 + 1 + I * f * 1/50",
    )

    branch_type = BranchType(
        name="TestBranchType",
        carry_current=True,
        self_impedance_formula="(rho * 0 + 0.25 + I * f * 0.012)*l",
        mutual_impedance_formula="(rho * 0 + 0.0 + I * f * 0.010)*l",
    )

    # Create and add buses
    gi.create_bus(name="bus1", type=bus_type, network=net, specific_earth_resistance=100.0)
    gi.create_bus(name="bus2", type=bus_type, network=net, specific_earth_resistance=150.0)


    assert len(net.buses) == 2
    assert "bus1" in net.buses
    assert "bus2" in net.buses

    # Create and add a branch
    gi.create_branch(name="branch1", type=branch_type, from_bus="bus1", to_bus="bus2", length=1, network=net)

    assert len(net.branches) == 1
    assert "branch1" in net.branches

def test_fault_and_source_setup():
    net = gi.create_network(name="TestNet", frequencies=[50])

    bus_type = BusType(
        name="BusTypeFormulaTest",
        system_type="Grounded",
        voltage_level=230.0,
        impedance_formula="rho * 0 + 1 + I * f * 1/50",
    )

    # Create a bus and source
    gi.create_bus(name="bus1", type=bus_type, network=net)
    gi.create_source(name="source1", bus="bus1", values={50: 60}, network=net)

    # Create a fault
    gi.create_fault(name="fault1", bus="bus1", description="Fault at bus1", scalings={50: 1.0}, network=net)

    assert len(net.sources) == 1
    assert "source1" in net.sources
    assert len(net.faults) == 1
    assert "fault1" in net.faults

def test_path_definition():
    net = gi.create_network(name="TestNet", frequencies=[50])

    bus_type = BusType(
        name="BusTypeFormulaTest",
        system_type="Grounded",
        voltage_level=230.0,
        impedance_formula="rho * 0 + 1 + I * f * 1/50",
    )

    branch_type = BranchType(
        name="TestBranchType",
        carry_current=True,
        self_impedance_formula="(rho * 0 + 0.25 + I * f * 0.012)*l",
        mutual_impedance_formula="(rho * 0 + 0.0 + I * f * 0.010)*l",
    )

    # Create and add buses
    gi.create_bus(name="bus1", type=bus_type, network=net)
    gi.create_bus(name="bus2", type=bus_type, network=net)
    gi.create_bus(name="bus3", type=bus_type, network=net)

    # Create and add branches
    gi.create_branch(name="branch1", type=branch_type, from_bus="bus1", to_bus="bus2", length=1, network=net)
    gi.create_branch(name="branch2", type=branch_type, from_bus="bus2", to_bus="bus3", length=1, network=net)

    #create a source
    gi.create_source(name="source1", bus="bus1", values={50: 60}, network=net)

    #create a fault
    gi.create_fault(name="fault1", bus="bus3", description="Fault at bus3", scalings={50: 1.0}, network=net)

    # Create a path
    gi.create_paths(network=net)

    assert len(net.paths) > 0

def test_fault_activation_and_simulation():
    net = gi.create_network(name="TestNet", frequencies=[50])

    bus_type = BusType(
        name="BusTypeFormulaTest",
        system_type="Grounded",
        voltage_level=230.0,
        impedance_formula="rho * 0 + 1 + I * f * 1/50",
    )

    branch_type = BranchType(
        name="TestBranchType",
        carry_current=True,
        self_impedance_formula="(rho * 0 + 0.25 + I * f * 0.012)*l",
        mutual_impedance_formula="(rho * 0 + 0.0 + I * f * 0.010)*l",
    )

    # Create and add buses
    gi.create_bus(name="bus1", type=bus_type, network=net)
    gi.create_bus(name="bus2", type=bus_type, network=net)
    gi.create_bus(name="bus3", type=bus_type, network=net)

    # Create and add branches
    gi.create_branch(name="branch1", type=branch_type, from_bus="bus1", to_bus="bus2", length=1, network=net)
    gi.create_branch(name="branch2", type=branch_type, from_bus="bus2", to_bus="bus3", length=1, network=net)

    #create a source
    gi.create_source(name="source1", bus="bus1", values={50: 60}, network=net)

    #create a fault
    gi.create_fault(name="fault1", bus="bus3", description="Fault at bus3", scalings={50: 1.0}, network=net)
    net.set_active_fault("fault1")

    # Create a path
    gi.create_paths(network=net)

    assert len(net.results) == 0

    # Run the fault
    gi.run_fault(network=net, fault_name="fault1")

    assert net.faults["fault1"].active == True
    assert len(net.results) > 0

def test_reduction_factor_between_0_1():
    net = gi.create_network(name="TestNet", frequencies=[50])

    bus_type = BusType(
        name="BusTypeFormulaTest",
        system_type="Grounded",
        voltage_level=230.0,
        impedance_formula="rho * 0 + 1 + I * f * 1/50",
    )

    branch_type = BranchType(
        name="TestBranchType",
        carry_current=True,
        self_impedance_formula="(rho * 0 + 0.25 + I * f * 0.012)*l",
        mutual_impedance_formula="(rho * 0 + 0.0 + I * f * 0.010)*l",
    )

    # Create and add buses
    gi.create_bus(name="bus1", type=bus_type, network=net)
    gi.create_bus(name="bus2", type=bus_type, network=net)
    gi.create_bus(name="bus3", type=bus_type, network=net)

    # Create and add branches
    gi.create_branch(name="branch1", type=branch_type, from_bus="bus1", to_bus="bus2", length=1, network=net)
    gi.create_branch(name="branch2", type=branch_type, from_bus="bus2", to_bus="bus3", length=1, network=net)

    #create a source
    gi.create_source(name="source1", bus="bus1", values={50: 60}, network=net)

    #create a fault
    gi.create_fault(name="fault1", bus="bus3", description="Fault at bus3", scalings={50: 1.0}, network=net)

    # Create a path
    gi.create_paths(network=net)

    assert len(net.results) == 0

    # Run the fault
    gi.run_fault(network=net, fault_name="fault1")

    assert net.faults["fault1"].active == True
    assert len(net.results) > 0

    # get the result of reduction factor
    results = net.results["fault1"]
    reduction_factors = results.reduction_factor.value

    # check if the reduction factor is between 0 and 1 for every value of the dict
    for key in reduction_factors:
        assert 0 <= reduction_factors[key] <= 1
 
def test_plot_bus_voltages_all_parameter():
    """
    Test of bus voltage plot was all possible parameter
    """
    net = gi.create_network(name="TestNet", frequencies=[50])

    bus_type = BusType(
        name="BusTypeFormulaTest",
        system_type="Grounded",
        voltage_level=230.0,
        impedance_formula="rho * 0 + 1 + I * f * 1/50",
    )

    branch_type = BranchType(
        name="TestBranchType",
        carry_current=True,
        self_impedance_formula="(rho * 0 + 0.25 + I * f * 0.012)*l",
        mutual_impedance_formula="(rho * 0 + 0.0 + I * f * 0.010)*l",
    )

    # Create and add buses
    gi.create_bus(name="bus1", type=bus_type, network=net)
    gi.create_bus(name="bus2", type=bus_type, network=net)
    gi.create_bus(name="bus3", type=bus_type, network=net)

    # Create and add branches
    gi.create_branch(name="branch1", type=branch_type, from_bus="bus1", to_bus="bus2", length=1, network=net)
    gi.create_branch(name="branch2", type=branch_type, from_bus="bus2", to_bus="bus3", length=1, network=net)

    #create a source
    gi.create_source(name="source1", bus="bus1", values={50: 60}, network=net)

    #create a fault
    gi.create_fault(name="fault1", bus="bus3", description="Fault at bus3", scalings={50: 1.0}, network=net)

    # Create a path
    gi.create_paths(network=net)

    # Run the fault
    gi.run_fault(network=net, fault_name="fault1")

    # Plot the bus voltages
    fig = gi.plot_bus_voltages(result=net.results["fault1"], frequencies=[50],
                               figsize=(10,5), title="Earth Potential Rise Plot", show=False)

    assert fig is not None

def test_plot_bus_voltages_default_parameter():
    """
    Test of bus voltage plot with default parameters
    """
    net = gi.create_network(name="TestNet", frequencies=[50])

    bus_type = BusType(
        name="BusTypeFormulaTest",
        system_type="Grounded",
        voltage_level=230.0,
        impedance_formula="rho * 0 + 1 + I * f * 1/50",
    )

    branch_type = BranchType(
        name="TestBranchType",
        carry_current=True,
        self_impedance_formula="(rho * 0 + 0.25 + I * f * 0.012)*l",
        mutual_impedance_formula="(rho * 0 + 0.0 + I * f * 0.010)*l",
    )

    # Create and add buses
    gi.create_bus(name="bus1", type=bus_type, network=net)
    gi.create_bus(name="bus2", type=bus_type, network=net)
    gi.create_bus(name="bus3", type=bus_type, network=net)

    # Create and add branches
    gi.create_branch(name="branch1", type=branch_type, from_bus="bus1", to_bus="bus2", length=1, network=net)
    gi.create_branch(name="branch2", type=branch_type, from_bus="bus2", to_bus="bus3", length=1, network=net)

    #create a source
    gi.create_source(name="source1", bus="bus1", values={50: 60}, network=net)

    #create a fault
    gi.create_fault(name="fault1", bus="bus3", description="Fault at bus3", scalings={50: 1.0}, network=net)

    # Create a path
    gi.create_paths(network=net)

    # Run the fault
    gi.run_fault(network=net, fault_name="fault1")

    # Plot the bus voltages
    fig = gi.plot_bus_voltages(result=net.results["fault1"], show=False)

    assert fig is not None

def test_plot_branch_currents_default_parameter():
    net = gi.create_network(name="TestNet", frequencies=[50])

    bus_type = BusType(
        name="BusTypeFormulaTest",
        system_type="Grounded",
        voltage_level=230.0,
        impedance_formula="rho * 0 + 1 + I * f * 1/50",
    )

    branch_type = BranchType(
        name="TestBranchType",
        carry_current=True,
        self_impedance_formula="(rho * 0 + 0.25 + I * f * 0.012)*l",
        mutual_impedance_formula="(rho * 0 + 0.0 + I * f * 0.010)*l",
    )

    # Create and add buses
    gi.create_bus(name="bus1", type=bus_type, network=net)
    gi.create_bus(name="bus2", type=bus_type, network=net)
    gi.create_bus(name="bus3", type=bus_type, network=net)

    # Create and add branches
    gi.create_branch(name="branch1", type=branch_type, from_bus="bus1", to_bus="bus2", length=1, network=net)
    gi.create_branch(name="branch2", type=branch_type, from_bus="bus2", to_bus="bus3", length=1, network=net)

    #create a source
    gi.create_source(name="source1", bus="bus1", values={50: 60}, network=net)

    #create a fault
    gi.create_fault(name="fault1", bus="bus3", description="Fault at bus3", scalings={50: 1.0}, network=net)

    # Create a path
    gi.create_paths(network=net)

    # Run the fault
    gi.run_fault(network=net, fault_name="fault1")

    # Plot the branch currents
    fig = gi.plot_branch_currents(result=net.results["fault1"], show=False)

    assert fig is not None

def test_plot_branch_currents_all_parameter():
    net = gi.create_network(name="TestNet", frequencies=[50])

    bus_type = BusType(
        name="BusTypeFormulaTest",
        system_type="Grounded",
        voltage_level=230.0,
        impedance_formula="rho * 0 + 1 + I * f * 1/50",
    )

    branch_type = BranchType(
        name="TestBranchType",
        carry_current=True,
        self_impedance_formula="(rho * 0 + 0.25 + I * f * 0.012)*l",
        mutual_impedance_formula="(rho * 0 + 0.0 + I * f * 0.010)*l",
    )

    # Create and add buses
    gi.create_bus(name="bus1", type=bus_type, network=net)
    gi.create_bus(name="bus2", type=bus_type, network=net)
    gi.create_bus(name="bus3", type=bus_type, network=net)

    # Create and add branches
    gi.create_branch(name="branch1", type=branch_type, from_bus="bus1", to_bus="bus2", length=1, network=net)
    gi.create_branch(name="branch2", type=branch_type, from_bus="bus2", to_bus="bus3", length=1, network=net)

    #create a source
    gi.create_source(name="source1", bus="bus1", values={50: 60}, network=net)

    #create a fault
    gi.create_fault(name="fault1", bus="bus3", description="Fault at bus3", scalings={50: 1.0}, network=net)

    # Create a path
    gi.create_paths(network=net)

    # Run the fault
    gi.run_fault(network=net, fault_name="fault1")

    # Plot the branch currents
    fig = gi.plot_branch_currents(result=net.results["fault1"], frequencies=[50],
                                  figsize=(10,5), title="Branch Current Plot", show=False)

    assert fig is not None

def test_plot_bus_currents_default_parameter():
    net = gi.create_network(name="TestNet", frequencies=[50])

    bus_type = BusType(
        name="BusTypeFormulaTest",
        system_type="Grounded",
        voltage_level=230.0,
        impedance_formula="rho * 0 + 1 + I * f * 1/50",
    )

    branch_type = BranchType(
        name="TestBranchType",
        carry_current=True,
        self_impedance_formula="(rho * 0 + 0.25 + I * f * 0.012)*l",
        mutual_impedance_formula="(rho * 0 + 0.0 + I * f * 0.010)*l",
    )

    # Create and add buses
    gi.create_bus(name="bus1", type=bus_type, network=net)
    gi.create_bus(name="bus2", type=bus_type, network=net)
    gi.create_bus(name="bus3", type=bus_type, network=net)

    # Create and add branches
    gi.create_branch(name="branch1", type=branch_type, from_bus="bus1", to_bus="bus2", length=1, network=net)
    gi.create_branch(name="branch2", type=branch_type, from_bus="bus2", to_bus="bus3", length=1, network=net)

    #create a source
    gi.create_source(name="source1", bus="bus1", values={50: 60}, network=net)

    #create a fault
    gi.create_fault(name="fault1", bus="bus3", description="Fault at bus3", scalings={50: 1.0}, network=net)

    # Create a path
    gi.create_paths(network=net)

    # Run the fault
    gi.run_fault(network=net, fault_name="fault1")

    # Plot the bus currents
    fig = gi.plot_bus_currents(result=net.results["fault1"], show=False)

    assert fig is not None

def test_plot_bus_currents_all_parameter():
    net = gi.create_network(name="TestNet", frequencies=[50])

    bus_type = BusType(
        name="BusTypeFormulaTest",
        system_type="Grounded",
        voltage_level=230.0,
        impedance_formula="rho * 0 + 1 + I * f * 1/50",
    )

    branch_type = BranchType(
        name="TestBranchType",
        carry_current=True,
        self_impedance_formula="(rho * 0 + 0.25 + I * f * 0.012)*l",
        mutual_impedance_formula="(rho * 0 + 0.0 + I * f * 0.010)*l",
    )

    # Create and add buses
    gi.create_bus(name="bus1", type=bus_type, network=net)
    gi.create_bus(name="bus2", type=bus_type, network=net)
    gi.create_bus(name="bus3", type=bus_type, network=net)

    # Create and add branches
    gi.create_branch(name="branch1", type=branch_type, from_bus="bus1", to_bus="bus2", length=1, network=net)
    gi.create_branch(name="branch2", type=branch_type, from_bus="bus2", to_bus="bus3", length=1, network=net)

    #create a source
    gi.create_source(name="source1", bus="bus1", values={50: 60}, network=net)

    #create a fault
    gi.create_fault(name="fault1", bus="bus3", description="Fault at bus3", scalings={50: 1.0}, network=net)

    # Create a path
    gi.create_paths(network=net)

    # Run the fault
    gi.run_fault(network=net, fault_name="fault1")

    # Plot the bus currents
    fig = gi.plot_bus_currents(result=net.results["fault1"], frequencies=[50],
                               figsize=(10,5), title="Bus Current Plot", show=False)

    assert fig is not None
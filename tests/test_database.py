import groundinsight as gi
from groundinsight.models.core_models import BusType, BranchType
import pytest

def test_session_start():
    #create a network with buses, branches, soruces, faults, paths and results from run_fault
    net = gi.create_network(name="TestNet", frequencies=[50])

    bus_type = BusType(
        name="BusTypeFormulaTest",
        system_type="Grounded",
        voltage_level=230.0,
        impedance_formula="rho * 0 + 1 + I * f * 1/50",
    )

    branch_type = BranchType(
        name="TestBranchType",
        grounding_conductor=True,
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

    # try to save it to the database without starting the session
    with pytest.raises(RuntimeError) as e:
        gi.save_network_to_db(network=net, overwrite=True)
    
    # start the session
    gi.start_dbsession(sqlite_path="tests/test_grounding.db")
    gi.save_network_to_db(network=net, overwrite=True)

def test_bus_type_save_load():
    #create a network with buses, branches, soruces, faults, paths and results from run_fault

    bus_type = BusType(
        name="BusTypeFormulaTest",
        system_type="Grounded",
        voltage_level=230.0,
        impedance_formula="rho * 0 + 1 + I * f * 1/50",
    )

    branch_type = BranchType(
        name="TestBranchType",
        grounding_conductor=True,
        self_impedance_formula="(rho * 0 + 0.25 + I * f * 0.012)*l",
        mutual_impedance_formula="(rho * 0 + 0.0 + I * f * 0.010)*l",
    )

    gi.close_dbsession()
    # save the bus type to the database error will be raised if the session is not started
    with pytest.raises(RuntimeError) as e:
        gi.save_bustype_to_db(bus_type)
    
    # start the session
    gi.start_dbsession("tests/test_grounding.db")

    gi.save_bustype_to_db(bus_type, overwrite=True)

    # load the bus type from the database
    bus_types = gi.load_bustypes_from_db()
    assert bus_types["BusTypeFormulaTest"] == bus_type

def test_branch_type_save_load():
    #create a network with buses, branches, soruces, faults, paths and results from run_fault

    bus_type = BusType(
        name="BusTypeFormulaTest",
        system_type="Grounded",
        voltage_level=230.0,
        impedance_formula="rho * 0 + 1 + I * f * 1/50",
    )

    branch_type = BranchType(
        name="TestBranchType",
        grounding_conductor=True,
        self_impedance_formula="(rho * 0 + 0.25 + I * f * 0.012)*l",
        mutual_impedance_formula="(rho * 0 + 0.0 + I * f * 0.010)*l",
    )

    gi.close_dbsession()
    # save the branch type to the database error will be raised if the session is not started
    with pytest.raises(RuntimeError) as e:
        gi.save_branchtype_to_db(branch_type)
    
    # start the session
    gi.start_dbsession("tests/test_grounding.db")

    gi.save_branchtype_to_db(branch_type, overwrite=True)

    # load the branch type from the database
    branch_types = gi.load_branchtypes_from_db()
    assert branch_types["TestBranchType"] == branch_type

def test_network_save_load():
    #create a network with buses, branches, soruces, faults, paths and results from run_fault
    net = gi.create_network(name="TestNet", frequencies=[50])

    bus_type = BusType(
        name="BusTypeFormulaTest",
        system_type="Grounded",
        voltage_level=230.0,
        impedance_formula="rho * 0 + 1 + I * f * 1/50",
    )

    branch_type = BranchType(
        name="TestBranchType",
        grounding_conductor=True,
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

    gi.close_dbsession()
    # save the network to the database error will be raised if the session is not started
    with pytest.raises(RuntimeError) as e:
        gi.save_network_to_db(network=net)
    
    # start the session
    gi.start_dbsession("tests/test_grounding.db")

    gi.save_network_to_db(network=net, overwrite=True)

    # load the network from the database
    loaded_net = gi.load_network_from_db("TestNet")
    gi.run_fault(network=loaded_net, fault_name="fault1")
    
    assert loaded_net.name == net.name
    assert loaded_net.frequencies == net.frequencies
    assert loaded_net.buses == net.buses
    assert loaded_net.branches == net.branches
    assert loaded_net.sources == net.sources
    assert loaded_net.faults == net.faults 
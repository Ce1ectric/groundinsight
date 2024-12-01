# tests/test_json.py

"""
Testfile for JSON serialization and deserialization functions of the Network class from core_models.py
Based on the example from the groundinsight package
"""

import pytest
import groundinsight as gi

def test_save_network_successful(tmp_path):
    """
    Test that a network can be saved successfully to the database
    """
    # Create a network with buses, branches, sources, faults, paths, and results from run_fault
    net = gi.create_network(name="TestNet", frequencies=[50])

    bus_type = gi.BusType(
        name="BusTypeFormulaTest",
        system_type="Grounded",
        voltage_level=230,
        impedance_formula="rho * 0 + 1 + I * f * 1/50",
    )

    branch_type = gi.BranchType(
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

    # Create a source
    gi.create_source(name="source1", bus="bus1", values={50: 60}, network=net)

    # Create a fault
    gi.create_fault(name="fault1", bus="bus3", description="Fault at bus3", scalings={50: 1.0}, network=net)

    # Create a path
    gi.create_paths(network=net)

    # Run the fault
    gi.run_fault(network=net, fault_name="fault1")

    # Save the network as JSON to a temporary file
    json_file = tmp_path / "test_network.json"
    gi.save_network_to_json(network=net, path=str(json_file))

    # Load the network from JSON
    net2 = gi.load_network_from_json(path=str(json_file))
    gi.run_fault(network=net2, fault_name="fault1")

    # Check that the loaded network is the same as the original network
    assert net.name == net2.name
    assert net.description == net2.description
    assert net.frequencies == net2.frequencies
    assert net.buses == net2.buses
    assert net.branches == net2.branches
    assert net.sources == net2.sources
    assert net.faults == net2.faults
    assert net.paths == net2.paths
    assert net.results == net2.results

# network_operations.py
"""
Network Operations Module.

This module provides functions for managing the electrical network, including creating networks,
buses, branches, faults, and sources. It also includes functions to build the electrical network,
define paths, and run fault calculations. These operations utilize the core models defined in
`groundinsight.models.core_models` and interact with the `Network` instance to perform necessary
calculations and updates.
"""

from .models.core_models import Network, Bus, BusType, Branch, BranchType, Fault, Source
from typing import Optional, List, Dict


def create_network(name: str, frequencies: List, description: str = None) -> Network:
    """
    Create a new network with the given name and description.

    Initializes a `Network` instance with the specified name, frequencies, and an optional description.

    Args:
        name (str): The name of the network.
        frequencies (List[float]): A list of frequencies (in Hz) to be used in network calculations.
        description (Optional[str], optional): A brief description of the network. Defaults to None.

    Returns:
        Network: A newly created `Network` instance.

    Examples:
        >>> from groundinsight.models.core_models import BusType, BranchType
        >>> import groundinsight as gi
        >>> bus_type = BusType(name="StandardBus", description="A standard bus type", system_type="Grounded", voltage_level=110.0, impedance_formula="1 + j * f / 50")
        >>> branch_type = BranchType(name="StandardBranch", description="A standard branch type", grounding_conductor=True, self_impedance_formula="(1 + j * f / 50)*l", mutual_impedance_formula="(0.5 + j * f / 100)*l")
        >>> network = gi.create_network(name="TestNetwork", frequencies=[50, 60], description="A test electrical network")
    """
    return Network(name=name, description=description, frequencies=frequencies)


def create_bus(
    name: str,
    type: BusType,
    specific_earth_resistance: Optional[float] = 100,
    description: str = None,
    network: Optional[Network] = None,
) -> Bus:
    """
    Create a new Bus instance and optionally add it to the network.

    This function initializes a `Bus` with the provided parameters. If a `Network` instance is
    provided, the bus is added to the network, triggering impedance calculations.

    Args:
        name (str): The name of the bus.
        type (BusType): The type of the bus.
        specific_earth_resistance (Optional[float], optional): The specific earth resistance for the bus.
                                                              Defaults to 100.0.
        description (Optional[str], optional): A brief description of the bus. Defaults to None.
        network (Optional[Network], optional): The network to which the bus should be added. Defaults to None.

    Returns:
        Bus: A newly created `Bus` instance.

    Raises:
        ValueError: If the bus cannot be added to the provided network.

    Examples:
        >>> from groundinsight.models.core_models import BusType
        >>> import groundinsight as gi
        >>> bus_type = BusType(name="StandardBus", description="A standard bus type", system_type="Grounded", voltage_level=110, impedance_formula="1 + j * f / 50")
        >>> network = gi.create_network(name="TestNetwork", frequencies=[50, 60], description="A test electrical network")
        >>> bus = gi.create_bus(name="Bus1", type=bus_type, specific_earth_resistance=100.0, network=network)
        >>> print(bus.name)
        Bus1
    """
    bus = Bus(
        name=name,
        type=type,
        impedance={},
        specific_earth_resistance=specific_earth_resistance,
        description=description,
    )
    if network:
        network.add_bus(bus)
        # Impedance calculation is triggered within network.add_bus()
    return bus


def create_branch(
    name: str,
    type: BranchType,
    from_bus: str,
    to_bus: str,
    length: float,
    specific_earth_resistance: Optional[float] = 100,
    description: str = None,
    network: Optional[Network] = None,
    parallel_coefficient: Optional[float] = None,
) -> Branch:
    """
    Create a new Branch instance and optionally add it to the network.

    Initializes a `Branch` with the provided parameters. If a `Network` instance is provided,
    the branch is added to the network, triggering impedance calculations.

    Args:
        name (str): The name of the branch.
        type (BranchType): The type of the branch.
        from_bus (str): The name of the originating bus.
        to_bus (str): The name of the terminating bus.
        length (float): The length of the branch.
        specific_earth_resistance (Optional[float], optional): The specific earth resistance for the branch.
                                                              Defaults to 100.0.
        description (Optional[str], optional): A brief description of the branch. Defaults to None.
        network (Optional[Network], optional): The network to which the branch should be added. Defaults to None.
        parallel_coefficient (Optional[float], optional): The parallel coefficient for the branch.
                                                          Defaults to None.

    Returns:
        Branch: A newly created `Branch` instance.

    Raises:
        ValueError: If the specified `from_bus` or `to_bus` does not exist in the provided network.

    Examples:
        >>> from groundinsight.models.core_models import BranchType
        >>> import groundinsight as gi
        >>> branch_type = BranchType(name="StandardBranch", description="A standard branch type", grounding_conductor=True, self_impedance_formula="(1 + j * f / 50)*l", mutual_impedance_formula="(0.5 + j * f / 100)*l")
        >>> network = gi.create_network(name="TestNetwork", frequencies=[50, 60], description="A test electrical network")
        >>> bus1 = gi.create_bus(name="Bus1", type=bus_type, specific_earth_resistance=100.0, network=network)
        >>> bus2 = gi.create_bus(name="Bus2", type=bus_type, specific_earth_resistance=100.0, network=network)
        >>> branch = gi.create_branch(name="Branch1", type=branch_type, from_bus="Bus1", to_bus="Bus2", length=1.0, network=network)
        >>> print(branch.name)
        Branch1
    """
    # Validate buses if network is provided
    if network:
        if from_bus not in network.buses:
            raise ValueError(
                f"from_bus '{from_bus}' is not in the network '{network.name}'"
            )
        if to_bus not in network.buses:
            raise ValueError(
                f"to_bus '{to_bus}' is not in the network '{network.name}'"
            )

    branch = Branch(
        name=name,
        type=type,
        length=length,
        from_bus=from_bus,
        to_bus=to_bus,
        specific_earth_resistance=specific_earth_resistance,
        self_impedance={},  # Will be calculated
        mutual_impedance={},  # Will be calculated
        description=description,
        parallel_coefficient=parallel_coefficient,
    )
    if network:
        network.add_branch(branch)
        # Impedance calculations are triggered within network.add_branch()
    return branch


def create_fault(
    name: str,
    bus: str,
    scalings: Dict,
    active: bool = False,
    description: str = None,
    network: Optional[Network] = None,
) -> Fault:
    """
    Create a new Fault instance and optionally add it to the network.

    Initializes a `Fault` with the provided parameters. If a `Network` instance is provided,
    the fault is added to the network. If the fault is marked as active, it becomes the
    currently active fault in the network.

    Args:
        name (str): The name of the fault.
        bus (str): The name of the bus where the fault occurs.
        scalings (Dict[float, float]): Scaling factors for sources at different frequencies.
        active (bool, optional): Whether to activate the fault immediately upon creation.
                                  Defaults to False.
        description (Optional[str], optional): A brief description of the fault. Defaults to None.
        network (Optional[Network], optional): The network to which the fault should be added. Defaults to None.

    Returns:
        Fault: A newly created `Fault` instance.

    Raises:
        ValueError: If the specified bus does not exist in the provided network.

    Examples:
        >>> import groundinsight as gi
        >>> network = gi.create_network(name="TestNetwork", frequencies=[50, 60], description="A test electrical network")
        >>> fault_scalings = {50: 1.0, 60: 0.8}
        >>> fault = gi.create_fault(name="Fault1", bus="Bus1", scalings=fault_scalings, active=True, network=network)
        >>> print(fault.name)
        Fault1
    """
    if network:
        if bus not in network.buses:
            raise ValueError(f"bus '{bus}' is not in the network '{network.name}'")

    fault = Fault(
        name=name, description=description, bus=bus, scalings=scalings, active=False
    )

    if network:
        network.add_fault(fault)

    if active:
        network.set_active_fault(name)

    return fault


def create_source(
    name: str,
    bus: str,
    values: Dict,
    description: str = None,
    network: Optional[Network] = None,
) -> Source:
    """
    Create a new Source instance and optionally add it to the network.

    Initializes a `Source` with the provided parameters. If a `Network` instance is provided,
    the source is added to the network.

    Args:
        name (str): The name of the source.
        bus (str): The name of the bus where the source is located.
        values (Dict[float, ComplexNumber]): A dictionary mapping frequencies to current values.
        description (Optional[str], optional): A brief description of the source. Defaults to None.
        network (Optional[Network], optional): The network to which the source should be added. Defaults to None.

    Returns:
        Source: A newly created `Source` instance.

    Raises:
        ValueError: If the specified bus does not exist in the provided network.

    Examples:
        >>> import groundinsight as gi
        >>> network = gi.create_network(name="TestNetwork", frequencies=[50, 60], description="A test electrical network")
        >>> source_values = {50: ComplexNumber(real=10, imag=5), 60: ComplexNumber(real=15, imag=7)}
        >>> source = gi.create_source(name="Source1", bus="Bus1", values=source_values, network=network)
        >>> print(source.name)
        Source1
    """
    if network:
        if bus not in network.buses:
            raise ValueError(f"bus '{bus}' is not in the network '{network.name}'")

    source = Source(name=name, description=description, bus=bus, values=values)
    if network:
        network.add_source(source)
    return source


def create_paths(network: Network):
    """
    Create all possible paths between sources and the active fault in the network.

    Identifies and maps each source to the set of branches involved in its paths to the fault.
    The identified paths are added to the network's paths collection.

    Args:
        network (Network): The network instance for which paths are to be defined.

    Raises:
        ValueError: If there are no sources or faults defined in the network.

    Examples:
        >>> import groundinsight as gi
        >>> network = gi.create_network(name="TestNetwork", frequencies=[50, 60], description="A test electrical network")
        >>> gi.create_bus(name="Bus1", type=bus_type, specific_earth_resistance=100.0, network=network)
        >>> gi.create_bus(name="Bus2", type=bus_type, specific_earth_resistance=100.0, network=network)
        >>> gi.create_branch(name="Branch1", type=branch_type, from_bus="Bus1", to_bus="Bus2", length=1.0, network=network)
        >>> gi.create_fault(name="Fault1", bus="Bus2", scalings={50:1.0, 60:0.8}, active=True, network=network)
        >>> gi.create_paths(network=network)
    """
    network.define_paths()


def build_electrical_network(network: Network):
    """
    Build the electrical network from the physical network and attach it to the Network object.

    Initializes an `ElectricalNetwork` instance based on the physical network's configuration and
    assigns it to the `electrical_network` attribute of the provided `Network` instance.

    Args:
        network (Network): The network instance for which the electrical network is to be built.

    Raises:
        ImportError: If the `ElectricalNetwork` class cannot be imported.
        Exception: If there is an error during the construction of the electrical network.

    Examples:
        >>> import groundinsight as gi
        >>> network = gi.create_network(name="TestNetwork", frequencies=[50, 60], description="A test electrical network")
        >>> gi.build_electrical_network(network)
        >>> print(network.electrical_network)
        ElectricalNetwork: TestNetwork
    """
    from groundinsight.electrical_network import ElectricalNetwork

    network.electrical_network = ElectricalNetwork(network)


def run_fault(network: Network, fault_name: str):
    """
    Execute fault calculations, including solving the network and computing branch currents.

    This function sets the specified fault as active, builds the electrical network, solves the network equations,
    computes branch currents, reduction factors, and grounding impedance. The results are stored within the
    network's results object.

    Args:
        network (Network): The network instance on which the fault calculations are to be performed.
        fault_name (str): The name of the fault to activate and run calculations for.

    Raises:
        ValueError: If the specified fault does not exist in the network.
        RuntimeError: If there is an error during network calculations.

    Examples:
        >>> import groundinsight as gi
        >>> network = gi.create_network(name="TestNetwork", frequencies=[50, 60], description="A test electrical network")
        >>> gi.create_bus(name="Bus1", type=bus_type, specific_earth_resistance=100.0, network=network)
        >>> gi.create_bus(name="Bus2", type=bus_type, specific_earth_resistance=100.0, network=network)
        >>> gi.create_branch(name="Branch1", type=branch_type, from_bus="Bus1", to_bus="Bus2", length=1.0, network=network)
        >>> fault_scalings = {50: 1.0, 60: 0.8}
        >>> gi.create_fault(name="Fault1", bus="Bus2", scalings=fault_scalings, active=True, network=network)
        >>> gi.run_fault(network, fault_name="Fault1")
    """

    # Set the active fault
    network.set_active_fault(fault_name)

    # Check if there are paths in the network if not run create_path
    if network.paths == {}:
        create_paths(network)

    # build the electrical network from the physical network
    build_electrical_network(network)

    # Solve the network
    network.electrical_network.solve_network()

    # Compute branch currents
    network.electrical_network.compute_branch_currents()

    # Compute reduction factors
    network.electrical_network.compute_reduction_factors()

    # Compute grounding impedance
    network.electrical_network.compute_grounding_impedance()

    # Results are stored in net.results within the ElectricalNetwork methods


def create_network_assistant(
    name: str,
    frequencies: List,
    number_buses: int,
    bus_type: BusType,
    branch_type: BranchType,
    branch_length: List,
    specific_earth_resistance: float,
    description: str = None,
) -> Network:
    """
    Create a new network with a uniform bus and branch type with a given number of buses.

    This function initializes a `Network` instance and populates it with a specified number of buses
    and branches. Each bus is connected sequentially to form a linear network. Impedance calculations
    are triggered upon adding buses and branches to the network.

    Args:
        name (str): The name of the network.
        frequencies (List[float]): A list of frequencies (in Hz) to be used in network calculations.
        number_buses (int): The total number of buses to create in the network.
        bus_type (BusType): The type to assign to each bus.
        branch_type (BranchType): The type to assign to each branch.
        branch_length (List[float]): A list of lengths for each branch connecting the buses.
                                     The list should have `number_buses - 1` elements.
        specific_earth_resistance (float): The specific earth resistance for all buses and branches.
        description (Optional[str], optional): A brief description of the network. Defaults to None.

    Returns:
        Network: A fully initialized `Network` instance with the specified configuration.

    Raises:
        ValueError: If the length of `branch_length` does not match `number_buses - 1`.

    Examples:
        >>> from groundinsight.models.core_models import BusType, BranchType
        >>> import groundinsight as gi
        >>> bus_type = BusType(name="StandardBus", description="A standard bus type", system_type="Grounded", voltage_level=230.0, impedance_formula="1 + j * f / 50")
        >>> branch_type = BranchType(name="StandardBranch", description="A standard branch type", grounding_conductor=True, self_impedance_formula="(1 + j * f / 50)*l", mutual_impedance_formula="(0.5 + j * f / 100)*l")
        >>> branch_lengths = [1.0 for _ in range(29)]
        >>> network = gi.create_network_assistant(name="Network2", frequencies=[50, 250], number_buses=30, bus_type=bus_type, branch_type=branch_type, branch_length=branch_lengths, specific_earth_resistance=100.0, description="A large test network")
        >>> print(network.name)
        Network2
    """

    net = create_network(name, frequencies, description)

    for i in range(number_buses):
        # create a bus with the name "bus"+i+1
        bus_name = f"bus{i+1}"
        bus = create_bus(bus_name, bus_type, specific_earth_resistance, network=net)
        if i > 0:
            # create a branch with the name "branch"+i which connectes the buses from idx i-1 and i
            branch_name = f"branch{i}"
            create_branch(
                branch_name,
                branch_type,
                f"bus{i}",
                bus_name,
                branch_length[i - 1],
                specific_earth_resistance,
                network=net,
            )

    return net

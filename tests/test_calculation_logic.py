import polars as pl
import groundinsight as gi
from groundinsight.models.core_models import BusType, BranchType

def test_reduction_factor_for_bus7_fault7():
    # Create a network
    net = gi.create_network(name="MyTestNetwork", frequencies=[50])
    net.description = "That's my first test network"

    bus_type = BusType(
        name="BusTypeFormulaTest",
        description="Example bus type with parameters",
        system_type="Grounded",
        voltage_level=230.0,
        impedance_formula="rho * 0 + 1 + I * f * 1/50",
    )

    bus_type_uw = BusType(
        name="BusTypeFormulaTestUW",
        description="Example bus type with parameters",
        system_type="Grounded",
        voltage_level=230.0,
        impedance_formula="rho * 0 + 0.1 + I * f * 1/50",
    )

    branch_type = BranchType(
        name="TestBranchType",
        description="A test branch type",
        carry_current=True,
        self_impedance_formula="(rho * 0 + 0.25 + I * f * 0.012)*l",
        mutual_impedance_formula="(rho * 0 + 0.0 + I * f * 0.010)*l"
    )

    branch_ohl = BranchType(
        name="OHLine",
        description="An overhead line",
        carry_current=False,
        self_impedance_formula="NaN",
        mutual_impedance_formula="NaN"
    )

    net2 = gi.create_network_assistant(name="Network2", frequencies=[50,250], number_buses=30, bus_type=bus_type, branch_type=branch_type,
                                    branch_length=[(x+1)/10 for x in range(30)], specific_earth_resistance=100)


    """
    current net structure
    bus1 (S1) - branch1 -> bus2 - branch3 (F1) -> bus3 - branch4 -> bus4 - branch5(F2) -> bus5
    bus1 (S1) - branch2 -> bus2 - branch3 (F1 -> bus3 - branch4 -> bus4 - branch5 (F2) -> bus5
    """

    # Create buses with specific earth resistivity
    gi.create_bus(name="bus1", type=bus_type_uw, network=net, specific_earth_resistance=100.0)
    bus2 = gi.create_bus(name="bus2", type=bus_type, specific_earth_resistance=150.0)
    net.add_bus(bus2)
    gi.create_bus(name="bus3", type=bus_type, network=net, specific_earth_resistance=100.0)
    gi.create_bus(name="bus4", type=bus_type, network=net, specific_earth_resistance=100.0)
    gi.create_bus(name="bus5", type=bus_type, network=net, specific_earth_resistance=100.0)
    gi.create_bus(name="bus6", type=bus_type, network=net, specific_earth_resistance=100.0)
    gi.create_bus(name="bus7", type=bus_type, network=net, specific_earth_resistance=100.0)
    gi.create_bus(name="bus8", type=bus_type, network=net, specific_earth_resistance=100.0)
    gi.create_bus(name="bus9", type=bus_type, network=net, specific_earth_resistance=100.0)

    #create branch 
    #defining a line length of each branch
    line_length = 1

    branch1 = gi.create_branch(name="branch1", type=branch_type, from_bus="bus1", to_bus="bus2", length=line_length, specific_earth_resistance=200.0)
    # Add the branch to the network
    net.add_branch(branch1)

    gi.create_branch(name="branch2", type=branch_type, from_bus="bus1", to_bus="bus2", length=line_length, specific_earth_resistance=200.0, network=net)
    gi.create_branch(name="branch3", type=branch_type, from_bus="bus2", to_bus="bus3", length=line_length, specific_earth_resistance=100.0, network=net)
    gi.create_branch(name="branch4", type=branch_type, from_bus="bus3", to_bus="bus4", length=line_length, specific_earth_resistance=100.0, network=net)
    gi.create_branch(name="branch5", type=branch_type, from_bus="bus4", to_bus="bus5", length=line_length, specific_earth_resistance=100.0, network=net, parallel_coefficient=0.8)
    gi.create_branch(name="branch6", type=branch_type, from_bus="bus4", to_bus="bus5", length=line_length, specific_earth_resistance=100.0, network=net, parallel_coefficient=0.2)
    gi.create_branch(name="branch7", type=branch_type, from_bus="bus5", to_bus="bus6", length=line_length, specific_earth_resistance=100.0, network=net)
    gi.create_branch(name="branch8", type=branch_ohl, from_bus="bus6", to_bus="bus7", length=line_length, specific_earth_resistance=100.0, network=net)
    gi.create_branch(name="branch9", type=branch_type, from_bus="bus7", to_bus="bus8", length=line_length, specific_earth_resistance=100.0, network=net)
    gi.create_branch(name="branch10", type=branch_type, from_bus="bus8", to_bus="bus9", length=line_length, specific_earth_resistance=100.0, network=net)
    gi.create_branch(name="branch11", type=branch_type, from_bus="bus8", to_bus="bus9", length=line_length, specific_earth_resistance=100.0, network=net)


    #net.add_bus(bus2, overwrite=True)

    #create a fault at bus2
    fault_scaling = {0:1, 50: 1.0, 250: 1}
    fault = gi.create_fault(name="fault1", bus="bus1", description="A fault at bus", scalings=fault_scaling, network=net)
    fault2 = gi.create_fault(name="fault2", bus="bus2", description="A fault at bus", scalings=fault_scaling, network=net)
    fault3 = gi.create_fault(name="fault3", bus="bus3", description="A fault at bus", scalings=fault_scaling, network=net)
    fault4 = gi.create_fault(name="fault4", bus="bus4", description="A fault at bus", scalings=fault_scaling, network=net)
    fault5 = gi.create_fault(name="fault5", bus="bus5", description="A fault at bus", scalings=fault_scaling, network=net)
    fault6 = gi.create_fault(name="fault6", bus="bus6", description="A fault at bus", scalings=fault_scaling, network=net)
    fault7 = gi.create_fault(name="fault7", bus="bus7", description="A fault at bus", scalings=fault_scaling, network=net)
    fault8 = gi.create_fault(name="fault8", bus="bus8", description="A fault at bus", scalings=fault_scaling, network=net)
    fault9 = gi.create_fault(name="fault9", bus="bus9", description="A fault at bus", scalings=fault_scaling, network=net)



    #add a source at bus1
    source = gi.create_source(name="source1", bus="bus1", values={0:10, 50:60, 250:60, 350:60}, network=net)
    #source2 = gi.create_source(name="source2", bus="bus6", values={0:0, 50:30, 250:30, 350:30}, network=net)

    #define the paths of the network
    net.define_paths()

    #for loop over each fault
    for i in range(9):

        # Run fault calculations
        gi.run_fault(net, fault_name=f"fault{i+1}")

    # Access the Polars DataFrame of impedances
    res_impedances_df = net.res_all_impedances()

    # Filter the results for bus7 and fault7
    res_bus7_fault7 = res_impedances_df.filter((pl.col("fault_name") == "fault7") & (pl.col("fault_bus") == "bus7"))

    # Assert that the reduction factor is equal to 1 for the frequency of 50 Hz
    assert res_bus7_fault7.filter(pl.col("frequency_Hz") == 50)["reduction_factor"][0] > 0.99 
    assert res_bus7_fault7.filter(pl.col("frequency_Hz") == 50)["reduction_factor"][0] < 1.01

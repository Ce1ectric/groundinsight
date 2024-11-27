import sys
import os
import polars as pl
import numpy as np
# Add the project root directory to PYTHONPATH
project_root = os.path.abspath(os.path.join(os.getcwd(), '..', 'src'))
sys.path.insert(0, project_root)

# Import necessary modules
import groundinsight as gi
from groundinsight.models.core_models import BusType, BranchType


#test the execution time 

# Define common types
bus_type = gi.BusType(
    name="BusTypeFormulaTest",
    description="Example bus type with parameters",
    system_type="Grounded",
    voltage_level=230.0,
    impedance_formula="rho * 0 + 1 + I * f * 1/50",
)

branch_type = gi.BranchType(
    name="TestBranchType",
    description="A test branch type",
    carry_current=True,
    self_impedance_formula="(rho * 0 + 0.25 + I * f * 0.012)*l",
    mutual_impedance_formula="(rho * 0 + 0.0 + I * f * 0.010)*l"
)

# Create a network
net = gi.create_network(name="LargeTestNetwork", frequencies=[50, 250, 500])
net.description = "Test network with 100 buses and corresponding branches"

# Create buses
num_buses = 300
for i in range(1, num_buses + 1):
    gi.create_bus(
        name=f"bus{i}",
        type=bus_type,
        network=net,
        specific_earth_resistance=100 + i,  # Example resistance variation
    )

# Create branches
for i in range(1, num_buses):
    gi.create_branch(
        name=f"branch{i}",
        type=branch_type,
        from_bus=f"bus{i}",
        to_bus=f"bus{i+1}",
        length=1 + i * 0.01,  # Example length variation
        specific_earth_resistance=150 + i * 10,  # Example resistance variation
        network=net,
    )

# Add a fault at each bus
fault_scaling = {0: 1, 50: 1.0, 250: 1, 500: 1}
for i in range(1, num_buses + 1):
    gi.create_fault(
        name=f"fault{i}",
        bus=f"bus{i}",
        description=f"Fault at bus{i}",
        scalings=fault_scaling,
        network=net,
    )

# Add a source at the first bus
gi.create_source(
    name="source1",
    bus="bus1",
    values={50: 60, 250: 40, 500: 20},
    network=net,
)

# Define paths
gi.create_paths(network=net)

# Time the network calculations
import time
start_time = time.time()

# Loop through all faults and run fault calculations
for i in range(1, num_buses + 1):
    net.set_active_fault(f"fault{i}")
    gi.run_fault(net)

# Measure elapsed time
elapsed_time = time.time() - start_time
print(f"Elapsed time for fault calculations: {elapsed_time:.2f} seconds")

# Access and print results for a specific fault
fault_name = "fault50"  # Example fault to analyze
res_bus_df = net.res_buses(fault=fault_name)
res_branch_df = net.res_branches(fault=fault_name)

print(f"Results for {fault_name}:")
print("Bus Results:")
print(res_bus_df)
print("Branch Results:")
print(res_branch_df)

# Access reduction factors and grounding impedances
result = net.results[fault_name]
reduction_factors = result.reduction_factor.value
grounding_impedances = result.grounding_impedance.value

# Print reduction factors
print("\nReduction Factors:")
for freq, rf in reduction_factors.items():
    print(f"Frequency: {freq} Hz, Reduction Factor: {rf}")

# Print grounding impedances
print("\nGrounding Impedances:")
for freq, z in grounding_impedances.items():
    try:
        print(f"Frequency: {freq} Hz, Grounding Impedance: {np.abs(z)}")
    except:
        print(f"Frequency: {freq} Hz, Exception in computing grounding impedance")

# Summarize impedance results
print("\nAll Impedances:")
print(net.res_all_impedances())

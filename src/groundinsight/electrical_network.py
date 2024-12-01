# electrical_network.py

"""
module for creating an electrical network based on the core models
The network is used to perform calculations based on the matrix form of the network:

Y * v = i
v = Y^-1 * i

where:

Y - admittance matrix, branches and buses are used to build this matrix
v - vector of bus voltages
i - vector of current sources and the mutual copplings between current sources and branches
"""

import numpy as np
from typing import Dict, Optional
from scipy.sparse import csc_matrix
from scipy.sparse.linalg import splu
from groundinsight.models.core_models import (
    Network,
    Bus,
    Branch,
    ResultGroundingImpedance,
    Source,
    Fault,
    ComplexNumber,
    Result,
    ResultBus,
    ResultBranch,
    ResultReductionFactor,
)


class ElectricalNetwork:
    """
    Represents the electrical properties of a network, enabling calculations.

    This class handles the construction of admittance matrices, voltage and current vectors,
    and performs network analysis to compute results such as bus voltages, branch currents,
    reduction factors, and grounding impedances.
    """

    def __init__(self, network: Network):
        """
        Initialize the ElectricalNetwork with a given Network model.

        Sets up necessary data structures and initializes the network calculations.

        Args:
            network (Network): The Network instance containing buses, branches, sources, and faults.
        """
        self.network = network
        self.bus_indices = {}
        self.Y_matrices = {}  # Admittance matrices for each frequency
        self.u_vectors = {}  # Voltage vectors for each frequency
        self.u_vectors_no_mutual = {}  # Voltage vectors without mutual currents
        self.i_vectors_no_mutual = {}  # Current vectors without mutual currents
        self.i_vectors = {}  # Current vectors for each frequency
        self.results: Result = Result()  # Stores the calculation results
        self.i_mutuals = {}  # Store mutual currents per frequency per branch
        self.total_source_currents = {}  # Store total source currents per frequency

        self._initialize()

    def _initialize(self):
        """
        Initialize bus indices and other necessary data structures.

        This method assigns indices to buses, handles parallel branch coefficients,
        constructs admittance matrices, and builds voltage and current vectors.
        """
        self._assign_bus_indices()
        self._assign_parallel_coefficients()
        self._construct_Y_matrices()
        self._construct_vectors()

    def _assign_bus_indices(self):
        """
        Assign an index to each bus for matrix representation.

        Creates a mapping from bus names to their corresponding indices in the admittance matrix.
        """
        bus_names = list(self.network.buses.keys())
        self.bus_indices = {bus_name: idx for idx, bus_name in enumerate(bus_names)}
        self.num_buses = len(bus_names)

    def _detect_parallel_branches(self):
        """
        Detect parallel branches between buses and group them.

        Identifies branches that connect the same pair of buses and groups them together.

        Returns:
            Dict[tuple, list]: A dictionary with keys as tuples of (from_bus, to_bus) and
                               values as lists of branch names that are parallel between those buses.
        """
        parallel_branches = {}
        for branch in self.network.branches.values():
            key = (branch.from_bus, branch.to_bus)
            if key in parallel_branches:
                parallel_branches[key].append(branch.name)
            else:
                parallel_branches[key] = [branch.name]
        return parallel_branches

    def _assign_parallel_coefficients(self):
        """
        Validate and assign parallel coefficients to branches.

        Ensures that the parallel coefficients of branches connecting the same pair of buses
        sum up to approximately 1.0. If coefficients are undefined or inconsistent, assigns
        equal splitting to each parallel branch.
        """
        parallel_branches = self._detect_parallel_branches()
        for (from_bus, to_bus), branch_names in parallel_branches.items():
            total_coefficient = 0.0
            undefined_coefficients = []
            for branch_name in branch_names:
                branch = self.network.branches[branch_name]
                if branch.parallel_coefficient is not None:
                    total_coefficient += branch.parallel_coefficient
                else:
                    undefined_coefficients.append(branch_name)
            # Check if total_coefficient is approximately 1.0
            if abs(total_coefficient - 1.0) > 1e-6 or undefined_coefficients:
                # Coefficients are not consistent or not fully defined
                # Assign equal splitting
                num_branches = len(branch_names)
                equal_coefficient = 1.0 / num_branches
                for branch_name in branch_names:
                    branch = self.network.branches[branch_name]
                    branch.parallel_coefficient = equal_coefficient
            else:
                # Coefficients sum to 1.0 and are defined
                continue

    def _construct_Y_matrices(self):
        """
        Construct the admittance matrices Y for each frequency in the network.

        Builds the admittance matrix by adding bus admittances to the diagonal and branch
        admittances to the off-diagonal elements. Mutual admittances are also accounted for.
        """
        frequencies = self.network.frequencies
        for freq in frequencies:
            Y_matrix = np.zeros((self.num_buses, self.num_buses), dtype=complex)
            # add the bus admittances to the diagonal of the matrix
            for bus_name, bus in self.network.buses.items():
                idx = self.bus_indices[bus_name]
                impedance = bus.impedance.get(freq)
                if impedance:
                    admittance = 1 / complex(impedance.real, impedance.imag)
                    Y_matrix[idx, idx] += admittance

            # Add branch admittances to Y_matrix
            for branch in self.network.branches.values():
                from_idx = self.bus_indices[branch.from_bus]
                to_idx = self.bus_indices[branch.to_bus]
                impedance = branch.self_impedance.get(freq)
                if impedance and branch.type.grounding_conductor:
                    admittance = 1 / complex(impedance.real, impedance.imag)
                    # Off-diagonal elements
                    Y_matrix[from_idx, to_idx] -= admittance
                    Y_matrix[to_idx, from_idx] -= admittance
                    # Diagonal elements
                    Y_matrix[from_idx, from_idx] += admittance
                    Y_matrix[to_idx, to_idx] += admittance

            self.Y_matrices[freq] = Y_matrix

    def _construct_vectors(self):
        """
        Construct the voltage and current vectors for each frequency in the network.

        Initializes voltage vectors and current vectors based on active faults and source
        currents, including mutual currents between branches and sources.
        """
        frequencies = self.network.frequencies
        active_fault = self.network.active_fault

        if not active_fault:
            raise ValueError("No active fault in the network")

        fault = self.network.faults[active_fault]
        fault_bus_idx = self.bus_indices[fault.bus]

        # Paths from sources to fault
        paths_from_sources = self._get_paths_from_sources_to_fault()

        for freq in frequencies:
            u_vector = np.zeros(self.num_buses, dtype=complex)
            i_vector = np.zeros(self.num_buses, dtype=complex)
            total_source_current = 0
            source_currents = {}
            # Initialize mutual currents dictionary for this frequency
            self.i_mutuals[freq] = {}

            # Iterate over the sources and add the source values to the current vector
            for source_name, source in self.network.sources.items():
                # Check if the source has a path to the fault
                branches_in_paths = paths_from_sources.get(source_name)
                if branches_in_paths:
                    bus_idx = self.bus_indices[source.bus]
                    scaling = fault.scalings.get(freq, 1)
                    current = source.values.get(freq, 0)
                    if current:
                        current_complex = scaling * complex(current.real, current.imag)
                        total_source_current += current_complex
                        # Store the source current for mutual coupling calculations
                        source_currents[source_name] = current_complex
                        # Source injection into their buses
                        i_vector[bus_idx] += current_complex
                else:
                    # Source does not have a path to the fault
                    continue  # Do not include this source

            # Inject the fault current into the fault bus
            i_vector[fault_bus_idx] -= total_source_current
            self.total_source_currents[freq] = total_source_current

            # Include mutual currents
            self._add_mutual_currents(
                i_vector, freq, source_currents, paths_from_sources
            )

            self.i_vectors[freq] = i_vector
            self.u_vectors[freq] = u_vector

    def _construct_vectors_no_mutual(self):
        """
        Construct the current vectors for each frequency without mutual currents.

        This method builds current vectors excluding the effects of mutual currents between
        branches and sources, allowing for separate analysis.
        """
        frequencies = self.network.frequencies
        active_fault = self.network.active_fault

        if not active_fault:
            raise ValueError("No active fault in the network")

        fault = self.network.faults[active_fault]
        fault_bus_idx = self.bus_indices[fault.bus]

        # Paths from sources to fault
        paths_from_sources = self._get_paths_from_sources_to_fault()

        self.i_vectors_no_mutual = {}

        for freq in frequencies:
            i_vector = np.zeros(self.num_buses, dtype=complex)
            total_source_current = 0
            source_currents = {}

            # Iterate over the sources and add the source values to the current vector
            for source_name, source in self.network.sources.items():
                # Check if the source has a path to the fault
                branches_in_paths = paths_from_sources.get(source_name)
                if branches_in_paths:
                    bus_idx = self.bus_indices[source.bus]
                    scaling = fault.scalings.get(freq, 1)
                    current = source.values.get(freq, 0)
                    if current:
                        current_complex = scaling * complex(current.real, current.imag)
                        total_source_current += current_complex
                        # Store the source current for mutual coupling calculations
                        source_currents[source_name] = current_complex
                        # Source injection into their buses
                        i_vector[bus_idx] += current_complex
                else:
                    # Source does not have a path to the fault
                    continue  # Do not include this source

            # Inject the fault current into the fault bus
            i_vector[fault_bus_idx] -= total_source_current

            # Do NOT include mutual currents
            # i_vector remains unchanged

            self.i_vectors_no_mutual[freq] = i_vector

    def _get_paths_from_sources_to_fault(self):
        """
        Retrieve all paths from sources to the active fault.

        Identifies and maps each source to the set of branches involved in its paths to the fault.

        Returns:
            Dict[str, set]: A dictionary mapping source names to sets of branch names in their paths.
        """
        paths_from_sources = {}
        fault_name = self.network.active_fault
        fault = self.network.faults[fault_name]
        fault_bus = fault.bus

        for source_name, source in self.network.sources.items():

            # Key: source_name, Value: set of branch names in paths
            paths = []
            for path in self.network.paths.values():
                if path.source == source_name and path.fault == fault_name:
                    paths.append(path)
            branches_in_paths = set()
            for path in paths:
                for branch in path.segments:
                    branches_in_paths.add(branch.name)

            paths_from_sources[source_name] = branches_in_paths

        return paths_from_sources

    def _add_mutual_currents(self, i_vector, freq, source_currents, paths_from_sources):
        """
        Add mutual currents to the current vector for each branch in paths from sources.

        Calculates and injects mutual currents based on source currents and branch impedances.

        Args:
            i_vector (np.ndarray): The current vector to update.
            freq (float): The frequency at which to calculate mutual currents.
            source_currents (Dict[str, complex]): A dictionary mapping source names to their currents.
            paths_from_sources (Dict[str, set]): A dictionary mapping source names to sets of branch names in their paths.
        """
        for branch in self.network.branches.values():
            from_idx = self.bus_indices[branch.from_bus]
            to_idx = self.bus_indices[branch.to_bus]

            # Check if branch is in any path from sources to fault
            for source_name, branches_in_paths in paths_from_sources.items():
                if branch.name in branches_in_paths:
                    source_bus_idx = self.bus_indices[
                        self.network.sources[source_name].bus
                    ]

                    # Determine sign based on indices
                    if source_bus_idx > min(from_idx, to_idx):
                        sign = 1
                    else:
                        sign = -1
                    # Get source current
                    i_source = source_currents.get(source_name, 0)
                    # Get branch impedances
                    Z_self = branch.self_impedance.get(freq)
                    Z_mutual = branch.mutual_impedance.get(freq)
                    if Z_self and Z_mutual:
                        Z_self_complex = complex(Z_self.real, Z_self.imag)
                        Z_mutual_complex = complex(Z_mutual.real, Z_mutual.imag)
                        # Use the parallel_coefficient
                        coefficient = branch.parallel_coefficient
                        # check if the branch can carry current
                        if branch.type.grounding_conductor:
                            # Calculate mutual current
                            i_mut = (
                                sign
                                * coefficient
                                * i_source
                                * (Z_mutual_complex / Z_self_complex)
                            )
                        else:
                            i_mut = 0
                        # Update i_vector with sign convention
                        i_vector[from_idx] += i_mut  # Negative at from_bus
                        i_vector[to_idx] -= i_mut  # Positive at to_bus

                        # Store the mutual current for this branch and frequency
                        # Accumulate if multiple sources contribute to mutual current
                        if branch.name in self.i_mutuals[freq]:
                            self.i_mutuals[freq][branch.name] += i_mut
                        else:
                            self.i_mutuals[freq][branch.name] = i_mut

    def solve_network(self):
        """
        Solve the network equations Y * u = i for each frequency.

        This method computes the bus voltages by solving the admittance matrix equations for each frequency.
        The results are stored in the network's results object.
        It uses the csc_matrix and splu functions from scipy, assuming the Y-Matrix is a sparse matrix.
        """
        fault_name = self.network.active_fault
        if fault_name is None:
            raise ValueError("No active fault set in the network.")

        result = Result(buses=[], branches=[], fault=fault_name)
        for freq in self.network.frequencies:
            Y_matrix = self.Y_matrices[freq]
            Y_matrix_sparse = csc_matrix(Y_matrix)
            lu = splu(Y_matrix_sparse)
            i_vector = self.i_vectors[freq]
            try:
                # Solve for u_vector
                u_vector = lu.solve(i_vector)
                self.u_vectors[freq] = u_vector
            except np.linalg.LinAlgError as e:
                print(f"Error solving network equations at frequency {freq}: {e}")
                continue

        # Create ResultBus instances
        for bus_name, idx in self.bus_indices.items():
            uepr_freq = {}
            ia_freq = {}
            for freq in self.network.frequencies:
                voltage = self.u_vectors[freq][idx]
                bus = self.network.buses.get(bus_name)
                impedance = bus.impedance.get(freq)
                try:
                    Z_self_complex = complex(impedance.real, impedance.imag)
                    current = voltage / Z_self_complex
                except ZeroDivisionError:
                    current = 0

                uepr_freq[freq] = ComplexNumber(real=voltage.real, imag=voltage.imag)
                ia_freq[freq] = ComplexNumber(real=current.real, imag=current.imag)

            # Calculate RMS values
            rms_voltage = self._calculate_rms(uepr_freq)
            rms_current = self._calculate_rms(ia_freq)

            result_bus = ResultBus(
                name=bus_name,
                uepr=rms_voltage,
                ia=rms_current,
                uepr_freq=uepr_freq,
                ia_freq=ia_freq,
            )
            result.buses.append(result_bus)

        # Store the result in the network's results dictionary
        self.network.results[fault_name] = result
        self.results = result  # Also keep a reference in self.results

    def compute_branch_currents(self):
        """
        Compute branch currents for each frequency and store them in the Result object.

        This method calculates the current flowing through each branch based on the bus voltages
        and branch impedances. The results are stored as `ResultBranch` instances within the
        network's results object.
        """
        fault_name = self.network.active_fault
        if fault_name is None:
            raise ValueError("No active fault set in the network.")

        if fault_name not in self.network.results:
            raise ValueError(f"No results available for fault '{fault_name}'.")

        result = self.network.results[fault_name]

        for branch in self.network.branches.values():
            from_idx = self.bus_indices[branch.from_bus]
            to_idx = self.bus_indices[branch.to_bus]
            i_s_freq = {}
            for freq in self.network.frequencies:
                from_voltage = self.u_vectors[freq][from_idx]
                to_voltage = self.u_vectors[freq][to_idx]
                impedance = branch.self_impedance.get(freq)
                if impedance and branch.type.grounding_conductor:
                    Z_self_complex = complex(impedance.real, impedance.imag)
                    Y_self_complex = 1 / Z_self_complex
                    delta_voltage = to_voltage - from_voltage

                    # Retrieve mutual current for this branch and frequency
                    i_mutual = self.i_mutuals.get(freq, {}).get(branch.name, 0)

                    # Calculate branch current using the new equation
                    current = delta_voltage * Y_self_complex + i_mutual

                    i_s_freq[freq] = ComplexNumber(real=current.real, imag=current.imag)
                else:
                    i_s_freq[freq] = 0

            # Calculate RMS current
            rms_current = self._calculate_rms(i_s_freq)

            result_branch = ResultBranch(
                name=branch.name, i_s=rms_current, i_s_freq=i_s_freq
            )
            result.branches.append(result_branch)

        # Update the result in the network's results dictionary
        self.network.results[fault_name] = result
        self.results = result  # Update self.results

    def _calculate_rms(
        self, freq_values: Dict[float, Optional[ComplexNumber]]
    ) -> float:
        """
        Calculate the RMS value from a dictionary of frequency to ComplexNumber.

        This method computes the root mean square (RMS) of the magnitudes of complex numbers
        across all specified frequencies.

        Args:
            freq_values (Dict[float, Optional[ComplexNumber]]): A dictionary mapping frequencies
                                                               to their corresponding ComplexNumber values.

        Returns:
            float: The calculated RMS value.
        """
        rms_squared = 0.0
        for value in freq_values.values():
            if value:
                magnitude = abs(complex(value.real, value.imag))
                rms_squared += magnitude**2
        rms_value = (rms_squared) ** 0.5
        return rms_value

    def compute_reduction_factors(self):
        """
        Compute the reduction factors by solving the network with and without mutual currents.

        This method calculates how much the presence of mutual currents affects the Earth Potential Rise (EPR).
        The reduction factors are stored in the network's results object.
        """
        fault_name = self.network.active_fault
        if fault_name is None:
            raise ValueError("No active fault set in the network.")

        fault_bus = self.network.faults[fault_name].bus
        fault_bus_idx = self.bus_indices[fault_bus]

        reduction_factors = {}
        uepr_with_mutual = {}
        uepr_without_mutual = {}

        frequencies = self.network.frequencies

        # Step 1: Solve network with mutual currents (already done)
        # Voltages are stored in self.u_vectors

        # Store uepr with mutual currents
        for freq in frequencies:
            voltage = self.u_vectors[freq][fault_bus_idx]
            uepr_with_mutual[freq] = voltage
        # Step 2: Create i_vectors without mutual currents
        self._construct_vectors_no_mutual()
        # Step 3: Solve network without mutual currents
        self.u_vectors_no_mutual = {}
        for freq in frequencies:
            Y_matrix = self.Y_matrices[freq]
            i_vector = self.i_vectors_no_mutual[freq]
            try:
                # Solve for u_vector without mutual currents
                u_vector = np.linalg.solve(Y_matrix, i_vector)
                self.u_vectors_no_mutual[freq] = u_vector

            except np.linalg.LinAlgError as e:
                print(
                    f"Error solving network equations at frequency {freq} without mutual currents: {e}"
                )
                continue

        # Store uepr without mutual currents
        for freq in frequencies:
            voltage = self.u_vectors_no_mutual[freq][fault_bus_idx]
            uepr_without_mutual[freq] = voltage

        # Step 4: Compute reduction factors
        for freq in frequencies:
            v_with = uepr_with_mutual[freq]
            v_without = uepr_without_mutual[freq]
            # Compute magnitudes
            mag_with = abs(v_with)
            mag_without = abs(v_without)

            if mag_without != 0:
                reduction_factor = mag_with / mag_without
            else:
                reduction_factor = None  # Handle division by zero
            reduction_factors[freq] = reduction_factor

        # Store the reduction factors in the result
        result = self.network.results[fault_name]
        result_reduction_factor = ResultReductionFactor(
            fault_bus=fault_bus, value=reduction_factors
        )
        result.reduction_factor = result_reduction_factor

        # Update the result in the network's results dictionary
        self.network.results[fault_name] = result
        self.results = result  # Update self.results

    def compute_grounding_impedance(self):
        """
        Compute the grounding impedance for the fault bus.

        This method calculates the grounding impedance using the formula:
            grounding_impedance = uepr / (reduction_factor * sum of all fault currents at the fault bus)

        The results are stored in the network's results object.
        """
        fault_name = self.network.active_fault
        if fault_name is None:
            raise ValueError("No active fault set in the network.")

        fault_bus = self.network.faults[fault_name].bus
        fault_bus_idx = self.bus_indices[fault_bus]

        grounding_impedances = (
            {}
        )  # Dictionary to store grounding impedance per frequency

        frequencies = self.network.frequencies

        result = self.network.results[fault_name]

        # Ensure that reduction factors are computed
        if not result.reduction_factor:
            raise ValueError(
                "Reduction factors not computed. Please compute reduction factors before grounding impedance."
            )

        for freq in frequencies:
            # Get uepr at the fault bus
            voltage = self.u_vectors[freq][fault_bus_idx]
            uepr = voltage  # Complex voltage at the fault bus

            # Get reduction factor at this frequency
            reduction_factor = result.reduction_factor.value.get(freq)
            if reduction_factor is None or reduction_factor == 0:
                grounding_impedances[freq] = None  # Cannot compute
                continue

            # Get total source current at this frequency
            total_source_current = self.total_source_currents.get(freq)
            if total_source_current is None or total_source_current == 0:
                grounding_impedances[freq] = None
                continue

            # The fault current is negative of total_source_current
            I_fault = -total_source_current  # Current flowing into the fault bus

            # Compute grounding impedance
            try:
                grounding_impedance = uepr / (reduction_factor * I_fault)
                grounding_impedances[freq] = ComplexNumber(
                    real=grounding_impedance.real, imag=grounding_impedance.imag
                )
            except ZeroDivisionError:
                grounding_impedances[freq] = None  # Handle division by zero

        # Store the grounding impedance in the result
        result_grounding_impedance = ResultGroundingImpedance(
            fault_bus=fault_bus, value=grounding_impedances
        )
        result.grounding_impedance = result_grounding_impedance

        # Update the result in the network's results dictionary
        self.network.results[fault_name] = result
        self.results = result  # Update self.results

    def __str__(self):
        # returns the name of the network
        return f"ElectricalNetwork: {self.network.name}"

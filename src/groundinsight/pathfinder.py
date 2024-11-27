# pathfinder.py

"""
PathFinder Module.

This module provides the `PathFinder` class, which is responsible for identifying all possible paths
between sources and faults within an electrical network. It utilizes Depth-First Search (DFS) to traverse
the network graph and determine the connectivity between different buses through branches.

The primary use cases include:
- Determining all paths from a specific source to a fault point.
- Analyzing the network's topology for fault impact assessment.
- Facilitating impedance and grounding calculations based on identified paths.
"""

from typing import List, Dict, Set
from .models.core_models import Network, Bus, Branch, Path


class PathFinder:
    """
    A class to find all paths between sources and faults in a network.

    The `PathFinder` class constructs an adjacency list representation of the network graph and uses
    Depth-First Search (DFS) to identify all possible paths between a given source bus and fault bus.
    These paths are essential for performing various electrical calculations, including impedance analysis
    and grounding assessments.
    """

    def __init__(self, network: Network):
        """
        Initialize the PathFinder with a given Network.

        Constructs the adjacency list representation of the network graph based on buses and branches.

        Args:
            network (Network): The Network instance containing buses and branches.

        """

        self.network = network
        self.graph = self._build_graph()

    def _build_graph(self) -> Dict[str, List[Branch]]:
        """
        Build an adjacency list representation of the network graph.

        Each bus is mapped to a list of branches connected to it, facilitating efficient traversal.

        Returns:
            Dict[str, List[Branch]]: An adjacency list where keys are bus names and values are lists of connected branches.

        """
        graph = {bus_name: [] for bus_name in self.network.buses.keys()}

        for branch in self.network.branches.values():
            from_bus = branch.from_bus
            to_bus = branch.to_bus
            # Assuming undirected graph, add branch to both from_bus and to_bus entries
            graph[from_bus].append(branch)
            graph[to_bus].append(branch)
        return graph

    def find_paths(self, source_bus_name: str, fault_bus_name: str) -> List[Path]:
        """
        Find all paths between the source bus and fault bus.

        Utilizes Depth-First Search (DFS) to explore all possible routes from the source to the fault.

        Args:
            source_bus_name (str): The name of the source bus.
            fault_bus_name (str): The name of the fault bus.

        Returns:
            List[Path]: A list of `Path` instances representing all discovered paths.

        """
        all_paths = []
        visited_buses = set()
        path = []

        self._dfs(source_bus_name, fault_bus_name, visited_buses, path, all_paths)

        # Convert each list of Branch objects to a Path object
        paths = []
        for branch_path in all_paths:
            path = Path(
                name="",  # Name will be assigned in define_paths()
                source="",  # Will be set in define_paths()
                fault="",  # Will be set in define_paths()
                segments=branch_path,
            )
            paths.append(path)
        return paths

    def _dfs(
        self,
        current_bus: str,
        target_bus: str,
        visited_buses: Set[str],
        path: List[Branch],
        all_paths: List[List[Branch]],
    ):
        """
        Perform Depth-First Search (DFS) to find all paths from current_bus to target_bus.

        This recursive helper function explores all possible routes without revisiting buses.

        Args:
            current_bus (str): The current bus being visited.
            target_bus (str): The target fault bus.
            visited_buses (Set[str]): A set of already visited buses to prevent cycles.
            path (List[Branch]): The current path being explored.
            all_paths (List[List[Branch]]): A list to store all discovered paths.

        """
        if current_bus == target_bus:
            # Found a path
            all_paths.append(list(path))
            return

        visited_buses.add(current_bus)

        for branch in self.graph[current_bus]:
            # Determine the neighbor bus (the other end of the branch)
            neighbor_bus = (
                branch.to_bus if branch.from_bus == current_bus else branch.from_bus
            )

            if neighbor_bus not in visited_buses:
                # Add branch to path
                path.append(branch)
                # Recursive call
                self._dfs(neighbor_bus, target_bus, visited_buses, path, all_paths)
                # Backtrack
                path.pop()

        visited_buses.remove(current_bus)

    def _bus_path_to_segments(self, bus_path: List[str]) -> List[Branch]:
        """
        Convert a list of bus names into a list of branches (segments).

        Translates a sequence of buses into the corresponding branches connecting them.

        Args:
            bus_path (List[str]): A list of bus names representing the path.

        Returns:
            List[Branch]: A list of `Branch` instances that form the path.

        Raises:
            ValueError: If no branch is found between consecutive buses in the path.

        """
        segments = []
        for i in range(len(bus_path) - 1):
            from_bus = bus_path[i]
            to_bus = bus_path[i + 1]
            # Find the branch connecting from_bus and to_bus
            branch = self._find_branch_between_buses(from_bus, to_bus)
            if branch:
                segments.append(branch)
            else:
                raise ValueError(f"No branch found between {from_bus} and {to_bus}")
        return segments

    def _find_branch_between_buses(self, from_bus: str, to_bus: str) -> Branch:
        """
        Find the branch connecting two specified buses.

        Searches for a branch that connects the `from_bus` and `to_bus` directly.

        Args:
            from_bus (str): The name of the originating bus.
            to_bus (str): The name of the terminating bus.

        Returns:
            Branch: The `Branch` instance connecting the two buses.

        Raises:
            ValueError: If no branch connects the specified buses.

        """
        for branch in self.network.branches.values():
            if (branch.from_bus == from_bus and branch.to_bus == to_bus) or (
                branch.from_bus == to_bus and branch.to_bus == from_bus
            ):
                return branch
        return None

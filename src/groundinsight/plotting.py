# plotting.py

"""
Plotting Module.

This module provides functions for visualizing the results of electrical network calculations,
including UEPR (Earth Potential Rise) for buses and branch currents. It utilizes Matplotlib
to generate plots that can display both frequency-dependent and RMS (Root Mean Square)
values for various electrical parameters. These visualizations aid in analyzing the
performance and behavior of the electrical network under different fault conditions.
"""

import matplotlib.pyplot as plt
from typing import List, Dict, Optional
from .models.core_models import Result, ComplexNumber


def plot_bus_voltages(
    result: Result,
    frequencies: Optional[List[float]] = None,
    figsize: tuple = (12, 6),
    title: str = "UEPR vs Bus Name",
    show=False,
):
    """
    Plot the UEPR (Earth Potential Rise) for each bus.

    This function generates a plot of UEPR values for each bus in the network. It can plot
    either frequency-dependent UEPR magnitudes or RMS UEPR values based on the provided parameters.

    Args:
        result (Result): The `Result` object containing the calculation results.
        frequencies (Optional[List[float]], optional):
            A list of frequencies (in Hz) to plot. If `None` or empty, RMS UEPR values are plotted.
            Defaults to `None`.
        figsize (tuple, optional):
            The size of the figure in inches as a (width, height) tuple. Defaults to `(12, 6)`.
        title (str, optional):
            The title of the plot. Defaults to `"UEPR vs Bus Name"`.
        show (bool, optional):
            Whether to display the plot immediately. If `False`, the plot is returned for further manipulation.
            Defaults to `False`.

    Returns:
        matplotlib.figure.Figure: The Matplotlib figure object containing the plot.

    Raises:
        KeyError: If a specified frequency is not present in the `uepr_freq` of any bus.

    Examples:
        >>> import groundinsight as gi
        >>> # Assuming 'result' is a Result object obtained from network calculations
        >>> fig = gi.plot_bus_voltages(result=result, frequencies=[50, 60], title="UEPR at 50Hz and 60Hz")
        >>> fig.savefig("uepr_plot.png")

        >>> # Plotting RMS UEPR values
        >>> fig = gi.plot_bus_voltages(result=result, title="RMS UEPR across Buses")
        >>> fig.show()
    """
    # Extract bus names
    bus_names = [bus.name for bus in result.buses]

    # Initialize data structure for plotting
    uepr_data = {}

    if frequencies:
        # Plot frequency-dependent UEPR values
        for freq in frequencies:
            uepr_values = []
            for bus in result.buses:
                uepr_complex = bus.uepr_freq.get(freq)
                if uepr_complex:
                    # Calculate magnitude of the complex UEPR value
                    uepr_magnitude = abs(complex(uepr_complex.real, uepr_complex.imag))
                else:
                    uepr_magnitude = 0.0  # Handle missing data
                uepr_values.append(uepr_magnitude)
            uepr_data[freq] = uepr_values

        # Plotting
        fig = plt.figure(figsize=figsize)
        for freq, uepr_values in uepr_data.items():
            plt.plot(bus_names, uepr_values, marker="o", label=f"{freq} Hz")

        plt.xlabel("Bus Name")
        plt.ylabel("UEPR (V)")
        plt.title(title)
        plt.xticks(rotation=45, ha="right")
        plt.legend(title="Frequency")
        plt.grid(True)
        plt.tight_layout()
        if show:
            plt.show()

    else:
        # Plot RMS values of UEPR
        uepr_rms_values = []
        for bus in result.buses:
            uepr_rms = bus.uepr  # RMS value of UEPR
            if uepr_rms is not None:
                uepr_rms_values.append(uepr_rms)
            else:
                uepr_rms_values.append(0.0)  # Handle missing data

        # Plotting
        fig = plt.figure(figsize=figsize)
        plt.plot(bus_names, uepr_rms_values, marker="o", linestyle="-", label="RMS")

        plt.xlabel("Bus Name")
        plt.ylabel("UEPR RMS (V)")
        plt.title(title)
        plt.xticks(rotation=45, ha="right")
        plt.legend()
        plt.grid(True)
        plt.tight_layout()
        if show:
            plt.show()

    return fig


def plot_branch_currents(
    result: Result,
    frequencies: Optional[List[float]] = None,
    figsize: tuple = (12, 6),
    title: str = "Branch Currents",
    show=False,
):
    """
    Plot the branch currents for each branch.

    This function generates a plot of branch currents in the network. It can plot either
    frequency-dependent current magnitudes or RMS current values based on the provided parameters.

    Args:
        result (Result): The `Result` object containing the calculation results.
        frequencies (Optional[List[float]], optional):
            A list of frequencies (in Hz) to plot. If `None` or empty, RMS current values are plotted.
            Defaults to `None`.
        figsize (tuple, optional):
            The size of the figure in inches as a (width, height) tuple. Defaults to `(12, 6)`.
        title (str, optional):
            The title of the plot. Defaults to `"Branch Currents"`.
        show (bool, optional):
            Whether to display the plot immediately. If `False`, the plot is returned for further manipulation.
            Defaults to `False`.

    Returns:
        matplotlib.figure.Figure: The Matplotlib figure object containing the plot.

    Raises:
        KeyError: If a specified frequency is not present in the `i_s_freq` of any branch.

    Examples:
        >>> import groundinsight as gi
        >>> # Assuming 'result' is a Result object obtained from network calculations
        >>> fig = gi.plot_branch_currents(result=result, frequencies=[50, 60], title="Branch Currents at 50Hz and 60Hz")
        >>> fig.savefig("branch_currents_plot.png")

        >>> # Plotting RMS branch currents
        >>> fig = gi.plot_branch_currents(result=result, title="RMS Branch Currents")
        >>> fig.show()
    """
    # Extract branch names
    branch_names = [branch.name for branch in result.branches]

    # Initialize data structure for plotting
    current_data = {}

    if frequencies:
        # Plot frequency-dependent branch currents
        for freq in frequencies:
            current_values = []
            for branch in result.branches:
                current_complex = branch.i_s_freq.get(freq)
                if current_complex:
                    # Calculate magnitude of the complex current
                    current_magnitude = abs(
                        complex(current_complex.real, current_complex.imag)
                    )
                else:
                    current_magnitude = 0.0  # Handle missing data
                current_values.append(current_magnitude)
            current_data[freq] = current_values

        # Plotting
        fig = plt.figure(figsize=figsize)
        bar_width = 0.8 / len(
            frequencies
        )  # Adjust bar width based on the number of frequencies
        indices = range(len(branch_names))
        for i, (freq, current_values) in enumerate(current_data.items()):
            positions = [x + i * bar_width for x in indices]
            plt.bar(positions, current_values, width=bar_width, label=f"{freq} Hz")

        plt.xlabel("Branch Name")
        plt.ylabel("Current (A)")
        plt.title(title)
        plt.xticks(
            [x + bar_width * (len(frequencies) - 1) / 2 for x in indices],
            branch_names,
            rotation=45,
            ha="right",
        )
        plt.legend(title="Frequency")
        plt.grid(True, axis="y")
        plt.tight_layout()
        if show:
            plt.show()

    else:
        # Plot RMS values of branch currents
        current_rms_values = []
        for branch in result.branches:
            current_rms = branch.i_s  # RMS value of branch current
            if current_rms is not None:
                current_rms_values.append(current_rms)
            else:
                current_rms_values.append(0.0)  # Handle missing data

        # Plotting
        fig = plt.figure(figsize=figsize)
        plt.bar(branch_names, current_rms_values, label="RMS")

        plt.xlabel("Branch Name")
        plt.ylabel("Current RMS (A)")
        plt.title(title)
        plt.xticks(rotation=45, ha="right")
        plt.legend()
        plt.grid(True, axis="y")
        plt.tight_layout()
        if show:
            plt.show()

    return fig


def plot_bus_currents(
    result: Result,
    frequencies: Optional[List[float]] = None,
    figsize: tuple = (12, 6),
    title: str = "Bus Currents",
    show=False,
):
    """
    Plot the bus currents for each bus.

    This function generates a plot of bus currents in the network. It can plot either
    frequency-dependent current magnitudes or RMS current values based on the provided parameters.

    Args:
        result (Result): The `Result` object containing the calculation results.
        frequencies (Optional[List[float]], optional):
            A list of frequencies (in Hz) to plot. If `None` or empty, RMS current values are plotted.
            Defaults to `None`.
        figsize (tuple, optional):
            The size of the figure in inches as a (width, height) tuple. Defaults to `(12, 6)`.
        title (str, optional):
            The title of the plot. Defaults to `"Bus Currents"`.
        show (bool, optional):
            Whether to display the plot immediately. If `False`, the plot is returned for further manipulation.
            Defaults to `False`.

    Returns:
        matplotlib.figure.Figure: The Matplotlib figure object containing the plot.

    Raises:
        KeyError: If a specified frequency is not present in the `ia_freq` of any bus.

    Examples:
        >>> import groundinsight as gi
        >>> # Assuming 'result' is a Result object obtained from network calculations
        >>> fig = gi.plot_bus_currents(result=result, frequencies=[50, 60], title="Bus Currents at 50Hz and 60Hz")
        >>> fig.savefig("bus_currents_plot.png")

        >>> # Plotting RMS bus currents
        >>> fig = gi.plot_bus_currents(result=result, title="RMS Bus Currents")
        >>> fig.show()
    """
    # Extract bus names
    bus_names = [bus.name for bus in result.buses]

    # Initialize data structure for plotting
    current_data = {}

    if frequencies:
        # Plot frequency-dependent bus currents
        for freq in frequencies:
            current_values = []
            for bus in result.buses:
                current_complex = bus.ia_freq.get(freq)
                if current_complex:
                    # Calculate magnitude of the complex current
                    current_magnitude = abs(
                        complex(current_complex.real, current_complex.imag)
                    )
                else:
                    current_magnitude = 0.0  # Handle missing data
                current_values.append(current_magnitude)
            current_data[freq] = current_values

        # Plotting
        fig = plt.figure(figsize=figsize)
        bar_width = 0.8 / len(
            frequencies
        )  # Adjust bar width based on the number of frequencies
        indices = range(len(bus_names))
        for i, (freq, current_values) in enumerate(current_data.items()):
            positions = [x + i * bar_width for x in indices]
            plt.bar(positions, current_values, width=bar_width, label=f"{freq} Hz")

        plt.xlabel("Bus Name")
        plt.ylabel("Current (A)")
        plt.title(title)
        plt.xticks(
            [x + bar_width * (len(frequencies) - 1) / 2 for x in indices],
            bus_names,
            rotation=45,
            ha="right",
        )
        plt.legend(title="Frequency")
        plt.grid(True, axis="y")
        plt.tight_layout()
        if show:
            plt.show()

    else:
        # Plot RMS values of bus currents
        current_rms_values = []
        for bus in result.buses:
            current_rms = bus.ia  # RMS value of bus current
            if current_rms is not None:
                current_rms_values.append(current_rms)
            else:
                current_rms_values.append(0.0)  # Handle missing data

        # Plotting
        fig = plt.figure(figsize=figsize)
        plt.bar(bus_names, current_rms_values, label="RMS")

        plt.xlabel("Bus Name")
        plt.ylabel("Current RMS (A)")
        plt.title(title)
        plt.xticks(rotation=45, ha="right")
        plt.legend()
        plt.grid(True, axis="y")
        plt.tight_layout()
        if show:
            plt.show()
    return fig

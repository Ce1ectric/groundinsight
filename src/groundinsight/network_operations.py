# network_operations.py
"""
Network Operations Module.

This module provides functions for managing the electrical network, including creating networks,
buses, branches, faults, and sources. It also includes functions to build the electrical network,
define paths, and run fault calculations. These operations utilize the core models defined in
`groundinsight.models.core_models` and interact with the `Network` instance to perform necessary
calculations and updates.
"""

import logging

from .models.core_models import Network, Bus, BusType, Branch, BranchType, Fault, Source
from typing import Optional, List, Dict


logger = logging.getLogger(__name__)


def create_network(name: str, frequencies: List, description: str = None) -> Network:
    """
    Create a new network with the given name and description.

    Initialises a :class:`Network` instance with the specified name,
    frequency list and an optional description.

    Parameters
    ----------
    name : str
        The name of the network.
    frequencies : list of float
        Frequencies (in Hz) at which network calculations are performed.
    description : str, optional
        A brief description of the network. Defaults to ``None``.

    Returns
    -------
    Network
        A newly created :class:`Network` instance.

    Examples
    --------
    >>> import groundinsight as gi
    >>> network = gi.create_network(
    ...     name="TestNetwork",
    ...     frequencies=[50, 60],
    ...     description="A test electrical network",
    ... )
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
    Create a new :class:`Bus` instance and optionally add it to a network.

    If a :class:`Network` instance is provided, the bus is added to the
    network, which also triggers the impedance calculation against the
    network's frequency list.

    Parameters
    ----------
    name : str
        The name of the bus.
    type : BusType
        The type of the bus.
    specific_earth_resistance : float, optional
        The specific earth resistance for the bus (Ohm * m). Defaults to
        ``100``.
    description : str, optional
        A brief description of the bus. Defaults to ``None``.
    network : Network, optional
        The network to which the bus should be added. Defaults to ``None``.

    Returns
    -------
    Bus
        A newly created :class:`Bus` instance.

    Raises
    ------
    ValueError
        If the bus cannot be added to the provided network.

    Examples
    --------
    >>> import groundinsight as gi
    >>> bus_type = gi.BusType(
    ...     name="StandardBus", system_type="Grounded", voltage_level=110,
    ...     impedance_formula="1 + j * f / 50",
    ... )
    >>> network = gi.create_network(name="TestNetwork", frequencies=[50, 60])
    >>> bus = gi.create_bus(
    ...     name="Bus1", type=bus_type,
    ...     specific_earth_resistance=100.0, network=network,
    ... )
    >>> bus.name
    'Bus1'
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
    parallel_coefficient: Optional[float] = 1.0,
) -> Branch:
    """
    Create a new :class:`Branch` instance and optionally add it to a network.

    If a :class:`Network` instance is provided, the branch is added to the
    network, which also triggers the self- and mutual-impedance
    calculations against the network's frequency list.

    Parameters
    ----------
    name : str
        The name of the branch.
    type : BranchType
        The type of the branch.
    from_bus : str
        The name of the originating bus.
    to_bus : str
        The name of the terminating bus.
    length : float
        The length of the branch (km).
    specific_earth_resistance : float, optional
        The specific earth resistance for the branch (Ohm * m). Defaults
        to ``100``.
    description : str, optional
        A brief description of the branch. Defaults to ``None``.
    network : Network, optional
        The network to which the branch should be added. Defaults to
        ``None``.
    parallel_coefficient : float, optional
        Per-branch share of the source-to-fault phase current; used by
        the path-based mutual-coupling injection. Defaults to ``1.0``.

    Returns
    -------
    Branch
        A newly created :class:`Branch` instance.

    Raises
    ------
    ValueError
        If the specified ``from_bus`` or ``to_bus`` does not exist in the
        provided network.

    Examples
    --------
    >>> import groundinsight as gi
    >>> branch_type = gi.BranchType(
    ...     name="StandardBranch", grounding_conductor=True,
    ...     self_impedance_formula="(1 + j * f / 50)*l",
    ...     mutual_impedance_formula="(0.5 + j * f / 100)*l",
    ... )
    >>> branch = gi.create_branch(
    ...     name="Branch1", type=branch_type,
    ...     from_bus="Bus1", to_bus="Bus2", length=1.0,
    ... )
    >>> branch.name
    'Branch1'
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
    t_k_s: Optional[float] = None,
    n_factor: float = 1.0,
) -> Fault:
    """
    Create a new :class:`Fault` instance and optionally add it to a network.

    If a :class:`Network` instance is provided, the fault is added to the
    network. If ``active=True``, the fault becomes the currently active
    fault in the network.

    Parameters
    ----------
    name : str
        The name of the fault.
    bus : str
        The name of the bus where the fault occurs.
    scalings : dict of float to float
        Scaling factors applied to the source currents at each frequency.
    active : bool, optional
        Whether to activate the fault immediately upon creation. Defaults
        to ``False``.
    description : str, optional
        A brief description of the fault. Defaults to ``None``.
    network : Network, optional
        The network to which the fault should be added. Defaults to
        ``None``.
    t_k_s : float, optional
        IEC 60909-0 short-circuit duration ``T_k`` in seconds. When set,
        :func:`groundinsight.check_conductor_limits` can derive the
        thermal rating without an explicit ``t_k`` argument.
    n_factor : float, default 1.0
        IEC 60909-0 AC heat factor ``n``. Keep the default ``1.0`` for
        far-from-generator faults, which is the normal case in grounding
        studies.

    Returns
    -------
    Fault
        A newly created :class:`Fault` instance.

    Raises
    ------
    ValueError
        If the specified bus does not exist in the provided network, if
        ``t_k_s`` is not a positive duration, or if ``n_factor`` lies
        outside ``(0, 1]``.

    Examples
    --------
    >>> import groundinsight as gi
    >>> network = gi.create_network(name="TestNetwork", frequencies=[50, 60])
    >>> fault = gi.create_fault(
    ...     name="Fault1", bus="Bus1",
    ...     scalings={50: 1.0, 60: 0.8},
    ...     active=True, network=network,
    ... )
    >>> fault.name
    'Fault1'
    """
    if network:
        if bus not in network.buses:
            raise ValueError(f"bus '{bus}' is not in the network '{network.name}'")

    fault = Fault(
        name=name,
        description=description,
        bus=bus,
        scalings=scalings,
        active=False,
        t_k_s=t_k_s,
        n_factor=n_factor,
    )

    if network:
        network.add_fault(fault)

    if active:
        network.set_active_fault(name)

    return fault


def set_active_fault(
    network: Network, fault_name: str, keep_results: bool = False
) -> None:
    """Activate ``fault_name`` on ``network`` and deactivate the others.

    Thin wrapper around :meth:`Network.set_active_fault` so the
    ``keep_results=`` keyword is reachable from the public top-level
    API surface (``gi.set_active_fault(net, "F1", keep_results=True)``)
    rather than only via the bound method on the :class:`Network`
    instance.

    Parameters
    ----------
    network : Network
        The network to operate on.
    fault_name : str
        The name of the fault to activate.
    keep_results : bool, default ``False``
        Forwarded to :meth:`Network.set_active_fault`. If ``True``,
        any previously cached :class:`Result` for ``fault_name`` is
        preserved so a notebook can re-plot the existing solve
        without recomputing it.

    Raises
    ------
    ValueError
        If the specified fault does not exist in ``network``.

    Notes
    -----
    Exposes the ``keep_results`` keyword at the top-level API surface
    where it would otherwise only be reachable as a bound method.

    Examples
    --------
    >>> import groundinsight as gi  # doctest: +SKIP
    >>> gi.set_active_fault(net, "F1", keep_results=True)  # doctest: +SKIP
    """
    network.set_active_fault(fault_name, keep_results=keep_results)


def create_source(
    name: str,
    bus: str,
    values: Dict,
    description: str = None,
    network: Optional[Network] = None,
    i_k_a: Optional[float] = None,
    r_to_x: Optional[float] = None,
    kappa: Optional[float] = None,
) -> Source:
    """
    Create a new current :class:`Source` and optionally add it to a network.

    Parameters
    ----------
    name : str
        The name of the source.
    bus : str
        The name of the bus where the source is located.
    values : dict of float to ComplexNumber or complex or float
        Frequency-resolved injected current. Real numbers are auto-promoted
        to :class:`ComplexNumber`.
    description : str, optional
        A brief description of the source. Defaults to ``None``.
    network : Network, optional
        The network to which the source should be added. Defaults to
        ``None``.
    i_k_a : float, optional
        Initial symmetrical short-circuit current ``I_k''`` in amperes
        contributed by this source. Metadata for the IEC 60909
        characteristics; the solve keeps using ``values``.
    r_to_x : float, optional
        ``R/X`` ratio of the short-circuit loop, used to derive ``kappa``
        when the latter is not given.
    kappa : float, optional
        IEC 60909-0 peak factor. Takes precedence over ``r_to_x``.

    Returns
    -------
    Source
        A newly created :class:`Source` instance with
        ``source_type='current'``.

    Raises
    ------
    ValueError
        If the specified bus does not exist in the provided network, or if
        any of the IEC 60909 quantities is outside its physical range.

    Examples
    --------
    >>> import groundinsight as gi
    >>> network = gi.create_network(name="TestNetwork", frequencies=[50, 60])
    >>> source = gi.create_source(
    ...     name="Source1", bus="Bus1",
    ...     values={50: 10 + 5j, 60: 15 + 7j},
    ...     network=network,
    ... )
    >>> source.name
    'Source1'
    """
    if network:
        if bus not in network.buses:
            raise ValueError(f"bus '{bus}' is not in the network '{network.name}'")

    source = Source(
        name=name,
        description=description,
        bus=bus,
        values=values,
        i_k_a=i_k_a,
        r_to_x=r_to_x,
        kappa=kappa,
    )
    if network:
        network.add_source(source)
    return source


def create_voltage_source(
    name: str,
    bus: str,
    voltage: Dict,
    source_impedance: Dict,
    description: str = None,
    network: Optional[Network] = None,
) -> Source:
    """
    Create a Thevenin (voltage) source and optionally add it to a network.

    The Thevenin source models a frequency-dependent EMF ``voltage`` in
    series with a finite ``source_impedance``. In contrast to
    :func:`create_source`, which creates an ideal current source for
    stationary studies, this factory is intended for transient analyses
    where the fault current is determined by the loop impedance
    ``Z_src + Z_loop`` rather than being prescribed.

    Parameters
    ----------
    name : str
        The name of the source.
    bus : str
        The name of the bus where the source is located.
    voltage : dict of float to ComplexNumber or complex
        Frequency-dependent EMF.
    source_impedance : dict of float to ComplexNumber or complex
        Frequency-dependent internal impedance. Must use the same
        frequency keys as ``voltage`` and must be non-zero.
    description : str, optional
        A brief description of the source. Defaults to ``None``.
    network : Network, optional
        The network to which the source should be added. Defaults to
        ``None``.

    Returns
    -------
    Source
        A newly created Thevenin source instance with
        ``source_type='voltage'``.

    Raises
    ------
    ValueError
        If the specified bus does not exist in the provided network, or
        if the input dictionaries do not satisfy the voltage-mode
        constraints.

    Examples
    --------
    >>> import groundinsight as gi
    >>> network = gi.create_network(name="TestNetwork", frequencies=[50])
    >>> source = gi.create_voltage_source(
    ...     name="VSrc1", bus="Bus1",
    ...     voltage={50: 20000.0 + 0.0j},
    ...     source_impedance={50: 0.5 + 0.1j},
    ...     network=network,
    ... )  # doctest: +SKIP
    """
    if network:
        if bus not in network.buses:
            raise ValueError(f"bus '{bus}' is not in the network '{network.name}'")

    source = Source(
        name=name,
        description=description,
        bus=bus,
        source_type="voltage",
        voltage=voltage,
        source_impedance=source_impedance,
    )
    if network:
        network.add_source(source)
    return source


def create_paths(network: Network):
    """
    Create all paths between the sources and the faults of the network.

    Identifies and maps each source to the ordered branch list of every
    simple path to each fault. The identified paths are added to the
    network's ``paths`` collection.

    Parameters
    ----------
    network : Network
        The network instance for which paths are to be defined.

    Raises
    ------
    ValueError
        If the network defines no sources or no faults. Path enumeration
        runs over ``sources x faults``, so an empty side yields no paths
        at all; every downstream step then succeeds on an unexcited
        system and reports 0 V at every bus. That is a plausible-looking
        result, not an error message, so it is rejected here instead.

    Notes
    -----
    The check is on *all* faults, not on ``active_fault``:
    :meth:`~groundinsight.models.core_models.Network.define_paths`
    enumerates every ``(source, fault)`` pair, and ``run_fault`` sets the
    active fault before it calls this function.

    Finding no path between an existing source and an existing fault is a
    different matter and stays permitted -- that is exactly what an
    outage scenario which islands the fault bus produces, and the
    all-zero result is then the correct answer.

    Examples
    --------
    >>> import groundinsight as gi  # doctest: +SKIP
    >>> gi.create_paths(network=network)  # doctest: +SKIP
    """
    if not network.sources:
        raise ValueError(
            f"Network '{network.name}' defines no sources, so no source-to-fault "
            "path exists and the calculation would return 0 V at every bus. "
            "Add a source with gi.create_source(...) or gi.create_voltage_source(...)."
        )
    if not network.faults:
        raise ValueError(
            f"Network '{network.name}' defines no faults, so no source-to-fault "
            "path exists and the calculation would return 0 V at every bus. "
            "Add a fault with gi.create_fault(...)."
        )
    network.define_paths()


def build_electrical_network(network: Network, auto_phase_currents: bool = False):
    """
    Build the electrical network and attach it to the :class:`Network` object.

    Initialises an :class:`ElectricalNetwork` helper based on the physical
    network's configuration and assigns it to the
    ``electrical_network`` attribute of the provided :class:`Network`
    instance. This step is invoked automatically by :func:`run_fault`.

    Parameters
    ----------
    network : Network
        The network instance for which the electrical network is to be
        built.
    auto_phase_currents : bool, optional
        If ``True``, the phase current through each branch is determined
        by solving a reduced phase-conductor network (topology-based
        split over parallel paths). If ``False``, the phase current is
        derived from the enumerated source-to-fault paths using each
        branch's ``parallel_coefficient``. Defaults to ``False`` here;
        :func:`run_fault` passes ``True`` unless asked otherwise.

    Raises
    ------
    ImportError
        If the :class:`ElectricalNetwork` class cannot be imported.

    Examples
    --------
    >>> import groundinsight as gi  # doctest: +SKIP
    >>> gi.build_electrical_network(network)  # doctest: +SKIP
    """
    from groundinsight.electrical_network import ElectricalNetwork

    network.electrical_network = ElectricalNetwork(
        network, auto_phase_currents=auto_phase_currents
    )


def _resolve_phase_current_mode(
    phase_current_mode: str, auto_parallel_coefficients: Optional[bool]
) -> bool:
    """
    Translate the mode arguments of :func:`run_fault` into one boolean.

    The deprecated ``auto_parallel_coefficients`` wins when it was passed
    explicitly, so existing call sites keep their exact behaviour even though
    the default of ``phase_current_mode`` is ``"auto"``.

    Parameters
    ----------
    phase_current_mode : str
        Either ``"auto"`` or ``"paths"``.
    auto_parallel_coefficients : bool or None
        Deprecated alias; ``None`` means it was not passed.

    Returns
    -------
    bool
        The value for ``ElectricalNetwork.auto_phase_currents``.

    Raises
    ------
    ValueError
        If ``phase_current_mode`` is neither ``"auto"`` nor ``"paths"``.
    """
    if phase_current_mode not in ("auto", "paths"):
        raise ValueError(
            f"phase_current_mode must be 'auto' or 'paths', got "
            f"{phase_current_mode!r}. 'auto' solves the phase-conductor network "
            f"and is the only mode that handles rings and meshes; 'paths' is the "
            f"pre-0.6 behaviour driven by Branch.parallel_coefficient."
        )
    if auto_parallel_coefficients is not None:
        logger.warning(
            "run_fault(auto_parallel_coefficients=%s) is deprecated; pass "
            "phase_current_mode=%r instead. The old argument still wins when "
            "given, so this call is unaffected.",
            auto_parallel_coefficients,
            "auto" if auto_parallel_coefficients else "paths",
        )
        return bool(auto_parallel_coefficients)
    return phase_current_mode == "auto"


def _warning_parallel_coeffcient(network: Network, parallel_coefficients: bool):
    """
    Warn when the legacy path-based mode meets a network with parallel routes.

    The check is scoped to the **active fault** and to a **single source**: only
    when one source reaches the active fault over two or more paths does the
    path-based scheme multiply the source current instead of splitting it. The
    earlier version counted paths across all faults, which raised a false alarm
    on a strictly radial network that merely carried two fault definitions.

    Parameters
    ----------
    network : Network
        The network instance for which the warning is to be raised.
    parallel_coefficients : bool
        ``True`` when the automatic phase-network solve is active, in which case
        nothing is warned about.

    Examples
    --------
        >>> import groundinsight as gi
        >>> network = gi.create_network(name="TestNetwork", frequencies=[50, 60], description="A test electrical network")
        >>> _warning_parallel_coeffcient(network=network, parallel_coefficients=False)
    """
    if parallel_coefficients:
        return

    active_fault = network.active_fault
    paths_per_source: Dict[str, int] = {}
    for path in network.paths.values():
        if active_fault is not None and path.fault != active_fault:
            continue
        paths_per_source[path.source] = paths_per_source.get(path.source, 0) + 1

    parallel_sources = [s for s, count in paths_per_source.items() if count > 1]
    if not parallel_sources:
        return

    # Only complain when the coefficients are still at their default, because a
    # user who set them deliberately has already made the call.
    default_coefficients = any(
        branch.parallel_coefficient is None or branch.parallel_coefficient == 1.0
        for path in network.paths.values()
        if active_fault is None or path.fault == active_fault
        for branch in path.segments
    )
    if not default_coefficients:
        return

    logger.warning(
        "Source(s) %s reach fault '%s' over more than one path while "
        "parallel_coefficient is still 1.0 or None. In phase_current_mode="
        "'paths' every one of those paths carries the FULL source current, so "
        "the current is multiplied instead of split -- a symmetric ring "
        "collapses to EPR = 0, r = 0 and Z_G = None. Use the default "
        "phase_current_mode='auto', or set parallel_coefficient per branch.",
        ", ".join(f"'{s}'" for s in sorted(parallel_sources)),
        active_fault,
    )


def run_fault(
    network: Network,
    fault_name: str,
    auto_parallel_coefficients: Optional[bool] = None,
    *,
    phase_current_mode: str = "auto",
):
    """
    Execute the fault calculation pipeline for a single fault.

    Sets the named fault as the active fault, builds the electrical
    network, solves the per-frequency nodal system, computes branch
    currents, reduction factors and grounding impedance. The results are
    stored on ``network.results[fault_name]``.

    Parameters
    ----------
    network : Network
        The network instance on which the fault calculations are to be
        performed.
    fault_name : str
        The name of the fault to activate and run calculations for.
    auto_parallel_coefficients : bool, optional
        Deprecated alias for ``phase_current_mode``. ``True`` maps to
        ``"auto"``, ``False`` to ``"paths"``. When given it wins over
        ``phase_current_mode`` so existing call sites keep their exact
        behaviour. Defaults to ``None`` (not given).
    phase_current_mode : {"auto", "paths"}, optional
        How the phase current per branch is determined.

        ``"auto"`` (default) solves a reduced phase-conductor network per
        source with the fault bus as reference and reads the branch
        currents off that solution. This is the only mode that splits the
        source current correctly over rings, meshes and parallel
        branches, and it ignores ``Branch.parallel_coefficient``.

        ``"paths"`` is the pre-0.6 behaviour: every branch on any
        enumerated source-to-fault path receives the *full* source
        current scaled by its ``parallel_coefficient``. In a meshed
        network that multiplies the current instead of splitting it --
        with the default coefficient of 1.0 a symmetric ring collapses to
        ``EPR = 0``. Kept for reproducing older studies and for networks
        where the split is known from measurement and set by hand.

    Raises
    ------
    ValueError
        If the specified fault does not exist in the network, or if
        ``phase_current_mode`` is not one of the two accepted values.
    RuntimeError
        If there is an error during the network calculations.

    Examples
    --------
    >>> import groundinsight as gi  # doctest: +SKIP
    >>> gi.run_fault(network, fault_name="Fault1")  # doctest: +SKIP
    """
    auto_phase_currents = _resolve_phase_current_mode(
        phase_current_mode, auto_parallel_coefficients
    )

    # Set the active fault
    network.set_active_fault(fault_name)

    # (Re)build paths when they are missing or the active topology changed
    # since they were last built (e.g. a manual Bus.active / Branch.active
    # flip or an in-place rewiring). Without this check run_fault would
    # silently reuse stale paths while the Y-matrix is rebuilt from the
    # current active flags, yielding a wrong EPR.
    if network._needs_path_rebuild():
        network.invalidate_paths()
        create_paths(network)

    # Warn only in the legacy mode, and only when the network around this fault
    # actually has parallel routes -- see _warning_parallel_coeffcient.
    if not auto_phase_currents:
        _warning_parallel_coeffcient(network, auto_phase_currents)

    # Build the electrical network from the physical network. auto_phase_currents
    # switches the phase-current determination from the path-based scheme
    # (parallel_coefficient per branch) to the phase-network solve.
    build_electrical_network(network, auto_phase_currents=auto_phase_currents)

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
    Create a linear network with a uniform bus and branch type.

    Initialises a :class:`Network` instance and populates it with
    ``number_buses`` buses and ``number_buses - 1`` branches connected
    sequentially to form a line topology. Impedance calculations are
    triggered upon adding buses and branches to the network.

    Parameters
    ----------
    name : str
        The name of the network.
    frequencies : list of float
        Frequencies (in Hz) at which network calculations are performed.
    number_buses : int
        The total number of buses to create.
    bus_type : BusType
        The type to assign to each bus.
    branch_type : BranchType
        The type to assign to each branch.
    branch_length : list of float
        Lengths of each branch connecting the buses. Must have
        ``number_buses - 1`` elements.
    specific_earth_resistance : float
        The specific earth resistance for all buses and branches
        (Ohm * m).
    description : str, optional
        A brief description of the network. Defaults to ``None``.

    Returns
    -------
    Network
        A fully initialised :class:`Network` instance with the specified
        configuration.

    Raises
    ------
    ValueError
        If ``number_buses`` is not an integer ``>= 1``, or if the length
        of ``branch_length`` does not match ``number_buses - 1``.

    Notes
    -----
    A line of ``n`` buses has ``n - 1`` branches, so ``branch_length``
    has one entry *fewer* than ``number_buses``. Passing ``n`` lengths --
    ``[1.0] * 30`` for ``number_buses=30``, read as "a 30 km line" -- used
    to be accepted silently: the surplus entry was dropped and the
    returned network was one span shorter than the one asked for, with no
    warning anywhere. Too few entries raised a bare ``IndexError`` from
    inside the loop.

    Examples
    --------
    >>> import groundinsight as gi  # doctest: +SKIP
    >>> network = gi.create_network_assistant(  # doctest: +SKIP
    ...     name="Linear30", frequencies=[50, 250], number_buses=30,
    ...     bus_type=bus_type, branch_type=branch_type,
    ...     branch_length=[1.0] * 29, specific_earth_resistance=100.0,
    ... )
    """
    if isinstance(number_buses, bool) or not isinstance(number_buses, int):
        raise ValueError(
            f"number_buses must be an int >= 1, got "
            f"{number_buses!r} ({type(number_buses).__name__})."
        )
    if number_buses < 1:
        raise ValueError(f"number_buses must be >= 1, got {number_buses}.")

    try:
        n_lengths = len(branch_length)
    except TypeError:
        raise ValueError(
            f"branch_length must be a sequence of {number_buses - 1} lengths "
            f"(one per branch), got {branch_length!r} "
            f"({type(branch_length).__name__})."
        ) from None
    if n_lengths != number_buses - 1:
        raise ValueError(
            f"branch_length has {n_lengths} entries but a line of "
            f"{number_buses} buses has {number_buses - 1} branches. "
            "A line of n buses needs n-1 lengths."
        )

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

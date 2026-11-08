# service.py

"""
Service functions: results without side effects on the input network.

:func:`groundinsight.run_fault` is a *stateful* convenience. It activates the
fault, drops or rebuilds ``network.paths``, attaches the assembled
:class:`~groundinsight.electrical_network.ElectricalNetwork` and stores the
:class:`~groundinsight.models.core_models.Result` in ``network.results``. That
is what a notebook wants -- ``net.res_buses()`` right after the call -- and it
is the wrong default for every caller that holds on to the network while
something else computes on it: a GUI that redraws its tables while a worker
thread solves, a server that answers several requests from one project, a
study that has to hand the network back unchanged. The audit history of the
studies is a list of exactly those restores going wrong (finding ARC1).

:func:`solve` is the side-effect-free twin. It runs the unchanged
``run_fault`` pipeline on an *overlay*: a second :class:`Network` object that
shares the element objects of the input -- buses, branches and sources, which
a solve reads but never writes -- and owns every container a solve does write
to: ``results``, ``paths``, the fault objects whose ``active`` flag is
switched, the private path fingerprint and the assembled system. Same code,
same element objects in the same order, hence the same floating-point
operations: the numbers are bit-identical to ``run_fault`` by construction,
not within a tolerance.

Two alternatives were rejected:

* **Mutate and restore** -- run ``run_fault`` on the input and put everything
  back in a ``finally`` block. The input would visibly change while the solve
  runs (a GUI thread reading it would see the fault's stored result vanish),
  and every private attribute a later release adds to ``Network`` would need a
  restore line of its own -- the failure mode the studies kept running into.
* **Deep copy** -- copying every ``ComplexNumber`` of every element costs
  ``elements x frequencies`` model validations per solve, which on a large
  network is more than the solve itself, to protect objects the solve never
  writes to.
"""

import copy
import logging

from .models.core_models import Network, Result
from .network_operations import run_fault
from .pathfinder import clear_pathfinder_cache

logger = logging.getLogger(__name__)

__all__ = ["solve"]


def _overlay(network: Network) -> Network:
    """
    A network object that shares the elements of ``network`` but not its state.

    The element objects (``Bus``, ``Branch``, ``Source``) are shared: the solve
    pipeline only reads them, and sharing them is what keeps the overlay cheap
    (``O(elements)`` references instead of ``O(elements x frequencies)``
    copies). Everything the pipeline writes to is the overlay's own:

    * every container field -- ``results``, ``paths``, the element
      dictionaries, ``frequencies`` -- as a shallow copy, so stored results and
      paths are reused exactly as ``run_fault`` would reuse them,
    * the ``Fault`` objects, copied because ``set_active_fault`` switches the
      private ``_active`` flag *on the fault object*,
    * the private attributes, copied with their containers; the path
      fingerprint is carried over so that the overlay decides "are the paths
      still valid?" exactly as the input would, and the assembled system
      starts empty.

    Parameters
    ----------
    network : Network
        The network to mirror.

    Returns
    -------
    Network
        A shallow copy of ``network`` made as described above. No validator
        runs (and none can warn a second time).

    Notes
    -----
    The overlay is a *copy* (``model_copy``), not a construction
    (``model_construct``): constructing a :class:`Network` runs its
    ``model_post_init``, which attaches every element to the network being
    built. The shared elements of the input would then belong to the
    short-lived overlay -- after the solve their back-reference would point at
    a collected object, and an edit of the input would no longer re-derive the
    element it edits. A copy runs no post-init, so the elements stay attached
    to the input throughout. The copied faults keep the input's link object
    too, but the input does not hold them under their names, so they count as
    detached and nothing reaches the input through them.

    The field values are taken from ``network.__dict__`` rather than from a
    fixed list, so a field or private attribute added to ``Network`` later is
    carried over without a change here.
    """
    overlay = network.model_copy()
    for name, value in list(overlay.__dict__.items()):
        if isinstance(value, (dict, list, set)):
            overlay.__dict__[name] = copy.copy(value)
    overlay.__dict__["faults"] = {
        name: fault.model_copy() for name, fault in network.faults.items()
    }
    private = overlay.__pydantic_private__
    if private is not None:
        for name, value in list(private.items()):
            if isinstance(value, (dict, list, set)):
                private[name] = copy.copy(value)
        private["_electrical_network"] = None
    return overlay


def solve(
    network: Network, fault: str, *, phase_current_mode: str = "auto"
) -> Result:
    """
    Solve one fault and return its result without modifying ``network``.

    Runs the same pipeline as :func:`groundinsight.run_fault` -- fault
    activation, path handling, assembly, nodal solve, branch currents,
    reduction factors, grounding impedance, diagnostics -- but on an overlay
    of ``network`` (see the module docstring), so nothing of the input is
    written: not its fields, not its stored results, not its active fault, not
    its path set or assembled system. The module-level path-cache entries keyed
    on it are not invalidated either (their own LRU limit may evict them, as
    after any solve), and the overlay's entries are removed again.

    Parameters
    ----------
    network : Network
        The network to solve. It is the same, unchanged object afterwards --
        also when the solve raises.
    fault : str
        Name of the fault to solve.
    phase_current_mode : {"auto", "paths"}, optional
        How the phase current per branch is determined, exactly as in
        :func:`groundinsight.run_fault`. Defaults to ``"auto"``.

    Returns
    -------
    Result
        A new result object with the same numbers -- bit for bit --, the same
        ``diagnostics`` and the same ``input_digest`` as the one ``run_fault``
        would store under ``network.results[fault]``. It is *not* stored on
        ``network``.

    Raises
    ------
    ValueError
        Whatever :func:`groundinsight.run_fault` raises for this network and
        fault: an unknown fault, a network with no source or no fault at all,
        an invalid ``phase_current_mode``, or a network the solver rejects
        (missing impedances, no reference to earth, ...).

    Notes
    -----
    **Threads.** ``solve`` never writes to ``network``, but it reads the
    network's buses, branches and sources while it runs. Do not modify the
    network during a solve, and do not solve the *same* ``Network`` object
    from several threads at once: the solve also goes through the
    module-level path caches of :mod:`groundinsight.pathfinder`, which every
    network in the process shares and which are not synchronised. Reading the
    network from another thread while a solve runs is safe.

    **solve or run_fault.** ``run_fault`` stays the convenient call for
    interactive work: afterwards ``network.res_buses()``,
    :func:`~groundinsight.bus_response` and :func:`~groundinsight.analyze_cuts`
    read the solved state straight off the network. ``solve`` is for code that
    holds the network on behalf of someone else -- a GUI session, a server, a
    study -- and must hand it back as it was. To keep a result from ``solve``
    on the network after all, store it explicitly
    (``network.results[fault] = result``); the ``res_*`` accessors tabulate it
    when called with ``fault=``. It carries the digest of the inputs it was
    computed from, so :meth:`Network.stale_results
    <groundinsight.models.core_models.Network.stale_results>` reports it as
    current until the network is edited.

    Examples
    --------
    >>> import groundinsight as gi
    >>> bus_type = gi.BusType(name="BT", system_type="MV", voltage_level=20.0,
    ...                       impedance_formula="0.01*rho")
    >>> cable = gi.BranchType(name="K", grounding_conductor=True,
    ...                       self_impedance_formula="(0.25+0.6*j)*l",
    ...                       mutual_impedance_formula="0.6*j*l")
    >>> net = gi.create_network(name="demo", frequencies=[50.0])
    >>> _ = gi.create_bus(name="b0", type=bus_type, network=net)
    >>> _ = gi.create_bus(name="b1", type=bus_type, network=net)
    >>> _ = gi.create_branch(name="k", type=cable, from_bus="b0", to_bus="b1",
    ...                      length=1.0, network=net)
    >>> _ = gi.create_source(name="q", bus="b0", values={50.0: 100.0}, network=net)
    >>> _ = gi.create_fault(name="ef", bus="b1", scalings={50.0: 1.0}, network=net)
    >>> result = gi.solve(net, "ef")
    >>> result.fault, net.results, net.active_fault
    ('ef', {}, None)
    """
    overlay = _overlay(network)
    try:
        return run_fault(overlay, fault, phase_current_mode=phase_current_mode)
    finally:
        # The overlay dies with this frame, but its path-cache entries would
        # not: they are keyed on ``id(overlay)``, and CPython hands that id to
        # the next object allocated at the same address. Dropping them here
        # keeps the caches exactly as the caller left them.
        clear_pathfinder_cache(overlay)
        overlay.electrical_network = None

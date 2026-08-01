# electrical_network.py

"""
module for creating an electrical network based on the core models
The network is used to perform calculations based on the matrix form of the network:

Y * u = i
u = Y^-1 * i

where:

Y - admittance matrix, branches and buses are used to build this matrix
u - vector of bus voltages (earth potential rise per bus)
i - vector of source injections plus the Norton-equivalent injections that
    represent the inductive coupling between phase conductors and shield
    (grounding) conductors along each branch.

Sign convention for the mutual coupling (see _add_mutual_currents):
    U_from - U_to = Z_self * I_s  -  Z_mutual * I_p
where I_p is the phase current through the branch in from->to direction and
I_s is the shield current in the same direction. The induced EMF enters the
nodal form as a Norton current i_mut = (Z_mutual / Z_self) * I_p which is
injected as +i_mut out of the from bus (nodal: i_vector[from] -= i_mut) and
+i_mut into the to bus (nodal: i_vector[to] += i_mut).

Two strategies are available to determine the phase current I_p per branch:
1) Path-based (default). For every simple path from source to fault, the
   branch direction is derived from the actual path traversal (no index
   heuristic). The user may scale the contribution per branch via
   Branch.parallel_coefficient, which is the legacy knob for splitting
   current between parallel paths.
2) Automatic distribution (auto_phase_currents=True). A reduced phase-only
   network is solved per source with +I at the source bus and the fault bus
   as reference. The resulting branch currents are used directly. In this
   mode parallel_coefficient is ignored. This mode is topology-agnostic and
   is the intended integration point for an external phase-current source
   (e.g. pandapower single-phase short-circuit results).
"""

import logging

import numpy as np
from typing import Dict, List, Optional
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
from groundinsight.utils.impedance_calculator import (
    check_passive_impedance,
    dc_substitute_impedance,
    is_short_circuit,
)


logger = logging.getLogger(__name__)


def _shortlist(names, limit: int = 5) -> str:
    """Render a list of names for an error message without flooding it.

    A 400-bus network with a systematic modelling error would otherwise put
    400 names into a single exception string, which hides the sentence that
    explains the problem. The count is always reported, so nothing is lost
    silently.

    Parameters
    ----------
    names : list of str
        Names to render, in the order they were collected.
    limit : int
        Maximum number of names to spell out. Defaults to 5.

    Returns
    -------
    str
        Comma-separated names, truncated with a total count if needed.
    """
    names = list(names)
    if len(names) <= limit:
        return ", ".join(names)
    shown = ", ".join(names[:limit])
    return f"{shown}, ... ({len(names)} in total)"


def _is_open_circuit(Z_complex: complex) -> bool:
    """Return ``True`` if an impedance means *no connection at all*.

    An infinite impedance is the documented open-end sentinel (formula
    ``"nan"``) -- a tower without an earth electrode, an overhead line without
    an earth wire. It is the only value that means "not connected".

    The reciprocal has to be short-circuited rather than computed, because
    IEEE 754 complex division does not give the mathematical answer for an
    infinite operand: ``1/complex(inf, inf)`` is ``nan+nan*j``, not ``0``.
    Letting that through puts NaN on the diagonal of ``Y`` and destroys the
    factorisation of the *whole* network -- so a single un-earthed tower,
    modelled exactly as documented, would fail the entire calculation with a
    "no path to reference earth" message even when every other bus is
    properly earthed.

    ``Z == 0`` is deliberately *not* treated as open any more. Up to v0.4.0 it
    was, which made a perfect earth electrode indistinguishable from a missing
    one: sweeping a grounding impedance towards zero converges to EPR = 0, but
    the value *at* zero returned the no-electrode EPR. Above 0 Hz zero is now
    rejected where it is computed
    (:func:`utils.impedance_calculator.check_passive_impedance`) and again in
    :meth:`ElectricalNetwork._validate_passive_impedances` for values that
    never passed through a formula. At 0 Hz it is legitimate -- an inductance
    is a short circuit at DC -- and
    :meth:`ElectricalNetwork._dc_substitute_at` replaces it with a small finite
    value before the matrix is assembled.

    ``NaN`` is deliberately *not* treated as open either. A NaN impedance is a
    failed computation, and it must stay visible so that
    :meth:`ElectricalNetwork._assert_finite_system` can report it.

    Parameters
    ----------
    Z_complex : complex
        The impedance to classify.

    Returns
    -------
    bool
        ``True`` for infinite impedances, ``False`` otherwise.
    """
    return bool(np.isinf(Z_complex.real) or np.isinf(Z_complex.imag))


class ElectricalNetwork:
    """
    Represents the electrical properties of a network, enabling calculations.

    This class handles the construction of admittance matrices, voltage and current vectors,
    and performs network analysis to compute results such as bus voltages, branch currents,
    reduction factors, and grounding impedances.
    """

    def __init__(self, network: Network, auto_phase_currents: bool = False):
        """
        Initialize the ElectricalNetwork with a given Network model.

        Sets up necessary data structures and initializes the network calculations.

        Parameters
        ----------
        network : Network
            The Network instance containing buses, branches, sources, and faults.
        auto_phase_currents : bool, optional
            If True the phase current through each
            branch is computed by solving a reduced phase-only network (Variant B).
            If False (default) the phase current is derived from the enumerated
            source-to-fault paths using each branch's ``parallel_coefficient``
            (Variant A). Defaults to False.
        """
        self.network = network
        self.auto_phase_currents = auto_phase_currents
        self.bus_indices = {}
        self.Y_matrices = {}  # Admittance matrices for each frequency
        self.u_vectors = {}  # Voltage vectors for each frequency
        self.u_vectors_no_mutual = {}  # Voltage vectors without mutual currents
        self.i_vectors_no_mutual = {}  # Current vectors without mutual currents
        self.i_vectors = {}  # Current vectors for each frequency
        self.results: Result = Result()  # Stores the calculation results
        self.i_mutuals = {}  # Store mutual currents per frequency per branch
        self.total_source_currents = {}  # Store total source currents per frequency
        # Source-only nodal injection per frequency, i.e. the ``i`` vector *before*
        # the mutual Norton equivalents are added on top. This is the current that
        # physically enters the grounding system through a lumped connection at the
        # bus -- the earthing conductor (EN 50522 "Erdungsleiter") -- as opposed to
        # ``ResultBus.ia``, which is the share dissipated into the soil through the
        # earth electrode ("Erder"). The mutual terms are a distributed modelling
        # artefact of the line/shield coupling and are deliberately excluded here:
        # no lumped conductor carries them into the node.
        self.source_injections = {}
        self.phase_currents = {}  # Signed phase current per branch per frequency
        # Substitute impedance per frequency for elements that are a short
        # circuit there. Only 0 Hz can have an entry, and only when the network
        # actually contains such an element. See _dc_substitute_at.
        self._dc_substitutes: Dict[float, float] = {}

        self._initialize()

    def _initialize(self):
        """
        Initialize bus indices and other necessary data structures.

        This method assigns indices to buses, constructs admittance matrices,
        and builds voltage and current vectors for each frequency.
        """
        self._assign_bus_indices()
        self._construct_Y_matrices()
        self._construct_vectors()

    def _assign_bus_indices(self):
        """
        Assign an index to each bus for matrix representation.

        Creates a mapping from bus names to their corresponding indices in the admittance matrix.
        Inactive buses (``Bus.active=False``) are excluded; they are removed from the
        nodal system entirely and any path traversing them is filtered out by the
        pathfinder.
        """
        bus_names = [
            name for name, bus in self.network.buses.items() if bus.active
        ]
        self.bus_indices = {bus_name: idx for idx, bus_name in enumerate(bus_names)}
        self.num_buses = len(bus_names)

    def _relevant(self, z_dict):
        """Restrict an impedance dictionary to the frequencies actually solved.

        A stored impedance dictionary may carry frequencies the network is not
        being solved at -- a leftover from an earlier frequency list, or an
        entry a user added by hand. Those values never reach a division, so
        they must not make the network unusable.

        Parameters
        ----------
        z_dict : dict or None
            Mapping of frequency to :class:`ComplexNumber`.

        Returns
        -------
        dict
            The subset of ``z_dict`` whose keys are in ``network.frequencies``.
        """
        if not z_dict:
            return {}
        return {
            freq: z_dict[freq]
            for freq in self.network.frequencies
            if freq in z_dict
        }

    def _validate_passive_impedances(self):
        """Reject stored impedances that cannot become an admittance.

        :meth:`Bus.calculate_impedance` and
        :meth:`Branch._calculate_self_impedance` already apply this rule when
        they evaluate a formula, but impedances are *not* recomputed at solve
        time. A value assigned directly to ``bus.impedance[freq]``, or restored
        from a database or a JSON file written by an older version, therefore
        reaches the solver untouched. This is the pass that catches those.

        Only the impedances that are actually inverted are checked: active
        buses, the self impedance of active grounding-conductor branches
        between two active buses, and the source impedance of voltage sources.
        Mutual impedances are never inverted and are deliberately left alone --
        zero mutual coupling is the normal case.

        Raises
        ------
        ValueError
            If any checked impedance is zero, too small to invert in double
            precision, or has a negative real part. The message names the
            element and every offending frequency.
        """
        for bus_name, bus in self.network.buses.items():
            if not bus.active:
                continue
            check_passive_impedance(
                self._relevant(bus.impedance),
                element=f"bus '{bus_name}' (grounding impedance)",
                formula_str=bus.type.impedance_formula,
            )

        for branch in self.network.branches.values():
            if not branch.active:
                continue
            if not branch.type.grounding_conductor:
                continue
            if branch.from_bus not in self.bus_indices:
                continue
            if branch.to_bus not in self.bus_indices:
                continue
            check_passive_impedance(
                self._relevant(branch.self_impedance),
                element=f"branch '{branch.name}' (self impedance)",
                formula_str=branch.type.self_impedance_formula,
            )

        for source in self.network.sources.values():
            if source.source_type != "voltage":
                continue
            if source.bus not in self.bus_indices:
                continue
            check_passive_impedance(
                self._relevant(source.source_impedance),
                element=f"source '{source.name}' (source impedance)",
            )

    def _dc_substitute_at(self, freq: float) -> Optional[float]:
        """Size the stand-in for elements that are a short circuit at 0 Hz.

        Only 0 Hz can need one: every other frequency has already been rejected
        by :meth:`_validate_passive_impedances`. At 0 Hz a purely inductive
        element legitimately has zero impedance -- an ideal short -- which the
        nodal formulation cannot invert. This pass finds those elements, and
        collects the magnitudes of the impedances that *are* usable so
        :func:`~groundinsight.utils.impedance_calculator.dc_substitute_impedance`
        can scale the substitute to the network.

        The scan covers exactly the impedances that are inverted in
        :meth:`_construct_Y_matrices`: active buses, the self impedance of
        active grounding-conductor branches between two active buses, and the
        impedance of voltage sources. Mutual impedances are never inverted.

        Parameters
        ----------
        freq : float
            The frequency being assembled, in Hz.

        Returns
        -------
        float or None
            The substitute impedance in Ohm, or ``None`` when ``freq`` is not
            0 Hz or no element is shorted -- in which case nothing changes.
        """
        if freq != 0.0:
            return None

        shorted: List[str] = []
        magnitudes: List[float] = []

        def classify(impedance, label: str) -> None:
            if impedance is None:
                return
            value = complex(impedance.real, impedance.imag)
            if is_short_circuit(value):
                shorted.append(label)
            else:
                magnitudes.append(abs(value))

        for bus_name, bus in self.network.buses.items():
            if not bus.active:
                continue
            classify(bus.impedance.get(freq), f"bus '{bus_name}'")

        for branch in self.network.branches.values():
            if not branch.active:
                continue
            if not branch.type.grounding_conductor:
                continue
            if branch.from_bus not in self.bus_indices:
                continue
            if branch.to_bus not in self.bus_indices:
                continue
            classify(
                branch.self_impedance.get(freq), f"branch '{branch.name}'"
            )

        for source in self.network.sources.values():
            if source.source_type != "voltage":
                continue
            if source.bus not in self.bus_indices:
                continue
            classify(
                source.source_impedance.get(freq), f"source '{source.name}'"
            )

        if not shorted:
            return None
        return dc_substitute_impedance(
            magnitudes, shorted, context="steady-state solve"
        )

    def _resolved_impedance(self, impedance, freq: float) -> Optional[complex]:
        """Complex value of a stored impedance, with a 0 Hz short substituted.

        Every place that inverts an impedance has to see the *same* value,
        otherwise the admittance matrix and the currents derived from it would
        describe two different networks. This is that single place. Away from
        0 Hz, and for every value that is not a short circuit, it is a plain
        conversion to :class:`complex`.

        Parameters
        ----------
        impedance : ComplexNumber or None
            The stored value, or ``None`` when the frequency is absent.
        freq : float
            The frequency the value belongs to, in Hz.

        Returns
        -------
        complex or None
            The value to invert, or ``None`` if ``impedance`` was ``None``.
        """
        if impedance is None:
            return None
        value = complex(impedance.real, impedance.imag)
        substitute = self._dc_substitutes.get(freq)
        if substitute is not None and is_short_circuit(value):
            return complex(substitute, 0.0)
        return value

    def _construct_Y_matrices(self):
        """
        Construct the admittance matrices Y for each frequency in the network.

        Builds the admittance matrix by adding bus grounding admittances to the diagonal
        and branch self-admittances to the off-diagonal elements. Branches without a
        grounding conductor (``grounding_conductor=False``) contribute no admittance to Y
        because their shield path is absent; they only propagate the phase current for the
        mutual coupling term.

        Thevenin sources (``source_type="voltage"``) contribute an additional
        loop-closing admittance ``Y_src = 1/Z_src`` between the source bus and
        the active fault bus. This represents the non-grounding part of the
        fault loop (phase conductor, transformer, source-side ground return)
        that is not modelled explicitly in the grounding network. Current-mode
        sources contribute nothing to ``Y`` -- they appear only in the current
        vector (Norton convention with infinite parallel impedance).
        """
        self._validate_passive_impedances()

        frequencies = self.network.frequencies
        # Active fault bus index is needed for the Thevenin loop closure. If
        # no fault is active yet (e.g. the network is being inspected before
        # ``run_fault``) we silently skip the voltage-source contribution --
        # ``_construct_vectors`` will reject the situation later with a
        # clear error.
        active_fault = self.network.active_fault
        if active_fault is not None:
            fault_bus = self.network.faults[active_fault].bus
            fault_bus_idx = self.bus_indices.get(fault_bus)
        else:
            fault_bus_idx = None

        # At 0 Hz a short circuit is physics rather than a mistake, so the
        # elements that have one get a small finite stand-in instead of being
        # dropped. Every frequency is asked, and only 0 Hz can answer.
        for freq in frequencies:
            substitute = self._dc_substitute_at(freq)
            if substitute is not None:
                self._dc_substitutes[freq] = substitute

        for freq in frequencies:
            Y_matrix = np.zeros((self.num_buses, self.num_buses), dtype=complex)
            # Bus grounding admittances on the diagonal (active buses only)
            for bus_name, bus in self.network.buses.items():
                if not bus.active:
                    continue
                idx = self.bus_indices[bus_name]
                Z_complex = self._resolved_impedance(
                    bus.impedance.get(freq), freq
                )
                if Z_complex is None:
                    continue
                if _is_open_circuit(Z_complex):
                    continue
                Y_matrix[idx, idx] += 1 / Z_complex

            # Branch self-admittances (inactive branches behave like an open
            # circuit: no contribution; inactive endpoints likewise drop the
            # branch entirely).
            for branch in self.network.branches.values():
                if not branch.active:
                    continue
                if not branch.type.grounding_conductor:
                    continue
                if branch.from_bus not in self.bus_indices:
                    continue
                if branch.to_bus not in self.bus_indices:
                    continue
                Z_complex = self._resolved_impedance(
                    branch.self_impedance.get(freq), freq
                )
                if Z_complex is None:
                    continue
                if _is_open_circuit(Z_complex):
                    continue
                admittance = 1 / Z_complex
                from_idx = self.bus_indices[branch.from_bus]
                to_idx = self.bus_indices[branch.to_bus]
                Y_matrix[from_idx, to_idx] -= admittance
                Y_matrix[to_idx, from_idx] -= admittance
                Y_matrix[from_idx, from_idx] += admittance
                Y_matrix[to_idx, to_idx] += admittance

            # Thevenin (voltage-source) loop closures between source bus and
            # active fault bus. Skipped if there is no active fault yet, or if
            # either endpoint is inactive.
            if fault_bus_idx is not None:
                for source in self.network.sources.values():
                    if source.source_type != "voltage":
                        continue
                    src_idx = self.bus_indices.get(source.bus)
                    if src_idx is None or src_idx == fault_bus_idx:
                        continue
                    Z_src_c = self._resolved_impedance(
                        source.source_impedance.get(freq), freq
                    )
                    if Z_src_c is None:
                        continue
                    if _is_open_circuit(Z_src_c):
                        continue
                    Y_src = 1 / Z_src_c
                    Y_matrix[src_idx, src_idx] += Y_src
                    Y_matrix[fault_bus_idx, fault_bus_idx] += Y_src
                    Y_matrix[src_idx, fault_bus_idx] -= Y_src
                    Y_matrix[fault_bus_idx, src_idx] -= Y_src

            self.Y_matrices[freq] = Y_matrix

    def _construct_vectors(self):
        """
        Construct the voltage and current vectors for each frequency in the network.

        For each frequency the source injections are placed at the source buses, the
        combined fault current is placed at the fault bus, the phase current per branch
        is computed (see :meth:`_compute_phase_currents`) and the Norton-equivalent
        mutual currents are added on top.

        Raises
        ------
        ValueError
            If no active fault is set on the network.
        """
        frequencies = self.network.frequencies
        active_fault = self.network.active_fault

        if not active_fault:
            raise ValueError("No active fault in the network")

        fault = self.network.faults[active_fault]
        if fault.bus not in self.bus_indices:
            raise ValueError(
                f"Fault bus '{fault.bus}' is inactive or missing; cannot solve."
            )
        fault_bus_idx = self.bus_indices[fault.bus]

        # Set of source names that actually have at least one path to the active
        # fault; sources without a path do not contribute to the injection.
        sources_with_path = self._sources_with_path_to_active_fault()

        for freq in frequencies:
            u_vector = np.zeros(self.num_buses, dtype=complex)
            i_vector = np.zeros(self.num_buses, dtype=complex)
            total_source_current = 0j
            self.i_mutuals[freq] = {}

            # Source injections at the source buses; combined fault current at the fault bus.
            # Sources whose bus is inactive are silently skipped here -- pathfinding
            # has already excluded them via the active-only graph, so no path leads
            # back to such a source either. The injection convention is the same
            # for current and voltage sources: +I at source bus, -I at fault bus.
            # For voltage sources I is the Norton-equivalent ``I_N = scaling*U/Z_src``;
            # the corresponding ``Y_src`` loop closure has already been added to Y.
            for source_name in sources_with_path:
                source = self.network.sources[source_name]
                if source.bus not in self.bus_indices:
                    continue
                bus_idx = self.bus_indices[source.bus]
                scaling = fault.scalings.get(freq, 1)
                injection = self._source_injection(source, freq, scaling)
                if injection is None:
                    continue
                total_source_current += injection
                i_vector[bus_idx] += injection

            i_vector[fault_bus_idx] -= total_source_current
            self.total_source_currents[freq] = total_source_current

            # Snapshot of the source-only injection, taken *before*
            # ``_add_mutual_currents`` mutates ``i_vector`` in place. This is the
            # current a lumped earthing conductor carries into (source buses) or
            # out of (fault bus) the grounding system; see the attribute comment
            # in ``__init__`` for why the mutual terms must not be included.
            self.source_injections[freq] = i_vector.copy()

            # Phase currents per branch at this frequency
            phase_currents = self._compute_phase_currents(freq, sources_with_path)
            self.phase_currents[freq] = phase_currents

            # Mutual Norton injections on top
            self._add_mutual_currents(i_vector, freq, phase_currents)

            self.i_vectors[freq] = i_vector
            self.u_vectors[freq] = u_vector

    def _source_injection(
        self, source: Source, freq: float, scaling: float
    ) -> Optional[complex]:
        """
        Return the (Norton-equivalent) current injection of a source at a frequency.

        For ``source_type="current"`` this is the legacy ``scaling * I_src``.
        For ``source_type="voltage"`` it is the Norton equivalent
        ``scaling * U_emf / Z_src`` -- the corresponding ``Y_src`` admittance is
        contributed to the Y-matrix in :meth:`_construct_Y_matrices`. Together
        the two model a Thevenin loop between the source bus and the fault bus.

        Parameters
        ----------
        source : Source
            The source whose injection should be evaluated.
        freq : float
            Frequency in Hz at which to evaluate the injection.
        scaling : float
            ``Fault.scalings[freq]`` for the active fault.

        Returns
        -------
        Optional[complex]
            The complex injection, or ``None`` if the
        source has no value at the requested frequency.
        """
        if source.source_type == "current":
            current = source.values.get(freq) if source.values is not None else None
            if current is None:
                return None
            return scaling * complex(current.real, current.imag)

        # source_type == "voltage"
        if source.voltage is None or source.source_impedance is None:
            return None
        u = source.voltage.get(freq)
        Z_src_c = self._resolved_impedance(
            source.source_impedance.get(freq), freq
        )
        if u is None or Z_src_c is None:
            return None
        if _is_open_circuit(Z_src_c):
            return None
        u_eff = scaling * complex(u.real, u.imag)
        return u_eff / Z_src_c

    def _construct_vectors_no_mutual(self):
        """
        Construct the current vectors for each frequency without mutual currents.

        This method builds current vectors excluding the inductive coupling between
        phase and shield conductors. It is used for the reference solution from which
        the reduction factor is computed.
        """
        frequencies = self.network.frequencies
        active_fault = self.network.active_fault

        if not active_fault:
            raise ValueError("No active fault in the network")

        fault = self.network.faults[active_fault]
        if fault.bus not in self.bus_indices:
            raise ValueError(
                f"Fault bus '{fault.bus}' is inactive or missing; cannot solve."
            )
        fault_bus_idx = self.bus_indices[fault.bus]

        sources_with_path = self._sources_with_path_to_active_fault()

        self.i_vectors_no_mutual = {}

        for freq in frequencies:
            i_vector = np.zeros(self.num_buses, dtype=complex)
            total_source_current = 0j

            for source_name in sources_with_path:
                source = self.network.sources[source_name]
                if source.bus not in self.bus_indices:
                    continue
                bus_idx = self.bus_indices[source.bus]
                scaling = fault.scalings.get(freq, 1)
                injection = self._source_injection(source, freq, scaling)
                if injection is None:
                    continue
                total_source_current += injection
                i_vector[bus_idx] += injection

            i_vector[fault_bus_idx] -= total_source_current

            self.i_vectors_no_mutual[freq] = i_vector

    def _sources_with_path_to_active_fault(self):
        """
        Return the list of source names that have at least one path to the active fault.

        Paths are taken from ``self.network.paths`` which must have been populated
        before the electrical network is solved (typically via ``network.define_paths()``).

        Returns
        -------
        List[str]
            Source names that contribute to the active fault.
        """
        fault_name = self.network.active_fault
        sources = set()
        for path in self.network.paths.values():
            if path.fault == fault_name and path.source in self.network.sources:
                sources.add(path.source)
        # Preserve insertion order of self.network.sources for determinism
        return [s for s in self.network.sources.keys() if s in sources]

    def _compute_phase_currents(
        self, freq: float, sources_with_path
    ) -> Dict[str, complex]:
        """
        Compute the signed phase current per branch at a given frequency.

        In the path-based mode (``auto_phase_currents=False``) the contributions are
        accumulated from each source's paths to the active fault. Direction comes from
        the actual path traversal (from-bus vs to-bus ordering) and the magnitude is
        scaled by ``Branch.parallel_coefficient``. A branch that lies on any path
        receives exactly one contribution per source (not one per path) to match the
        semantics of the legacy implementation that used a set of path branches.

        In the automatic mode (``auto_phase_currents=True``) a reduced phase-only
        network is solved per source using the branch self-impedance as the phase
        impedance proxy and the fault bus as the reference node. The resulting branch
        current is used directly and ``parallel_coefficient`` is ignored.

        Parameters
        ----------
        freq : float
            The frequency at which to compute the phase currents.
        sources_with_path : Iterable[str]
            Names of sources that have a path to the
            active fault.

        Returns
        -------
        Dict[str, complex]
            Mapping from branch name to signed phase current
        (positive when the current flows from ``from_bus`` to ``to_bus``).
        """
        if self.auto_phase_currents:
            return self._compute_phase_currents_auto(freq, sources_with_path)
        return self._compute_phase_currents_from_paths(freq, sources_with_path)

    def _compute_phase_currents_from_paths(
        self, freq: float, sources_with_path
    ) -> Dict[str, complex]:
        """
        Variant A: derive branch phase currents by traversing each source-to-fault path.

        For each source, all branches that appear on any of its paths to the active
        fault receive a single contribution ``sign * parallel_coefficient * I_source``
        where ``sign`` is +1 when the phase current direction coincides with the
        branch's from->to orientation and -1 otherwise.

        Parameters
        ----------
        freq : float
            Frequency to evaluate scalings and source values at.
        sources_with_path : Iterable[str]
            Sources that contribute at this fault.

        Returns
        -------
        Dict[str, complex]
            Signed phase current per branch.

        Raises
        ------
        RuntimeError
            If a path segment does not connect to the expected bus,
        indicating that the stored path is inconsistent with the branches.
        """
        fault_name = self.network.active_fault
        fault = self.network.faults[fault_name]
        phase_currents: Dict[str, complex] = {
            name: 0.0 + 0.0j for name in self.network.branches
        }

        for source_name in sources_with_path:
            source = self.network.sources[source_name]
            scaling = fault.scalings.get(freq, 1)
            i_src_complex = self._source_injection(source, freq, scaling)
            if i_src_complex is None:
                continue

            # Collect per-source branch directions. The first path in which a branch
            # appears determines its direction; any subsequent appearances are
            # ignored to avoid double-counting across parallel paths.
            branch_signs: Dict[str, int] = {}
            for path in self.network.paths.values():
                if path.source != source_name or path.fault != fault_name:
                    continue
                current_bus = source.bus
                for branch in path.segments:
                    if branch.from_bus == current_bus:
                        sign = +1
                        next_bus = branch.to_bus
                    elif branch.to_bus == current_bus:
                        sign = -1
                        next_bus = branch.from_bus
                    else:
                        raise RuntimeError(
                            f"Path '{path.name}' segment '{branch.name}' does not "
                            f"connect to bus '{current_bus}'"
                        )
                    if branch.name not in branch_signs:
                        branch_signs[branch.name] = sign
                    current_bus = next_bus

            for branch_name, sign in branch_signs.items():
                branch = self.network.branches[branch_name]
                coeff = branch.parallel_coefficient
                if coeff is None:
                    coeff = 1.0
                phase_currents[branch_name] += sign * coeff * i_src_complex

        return phase_currents

    def _compute_phase_currents_auto(
        self, freq: float, sources_with_path
    ) -> Dict[str, complex]:
        """
        Variant B: solve a reduced phase-only network to split source currents over parallel paths.

        The phase-side admittance of each branch is approximated by ``1 / Z_self`` when
        a grounding conductor is present and by ``1 / length`` otherwise (purely
        resistive proxy). The fault bus is taken as the reference (``u_fault = 0``) and
        each source injects ``scaling * source.values[freq]`` at its bus. Branch phase
        currents are then ``(u_from - u_to) * y_phase`` per branch. Contributions from
        all sources are superposed.

        This mode ignores ``Branch.parallel_coefficient`` because the split is derived
        from the topology. It is also the natural integration point for an external
        phase-current provider (e.g. pandapower's single-phase short-circuit) — such a
        provider would bypass the solve and write directly into the returned dict.

        Parameters
        ----------
        freq : float
            Frequency at which to solve.
        sources_with_path : Iterable[str]
            Sources contributing to the active fault.

        Returns
        -------
        Dict[str, complex]
            Signed phase current per branch.
        """
        fault_name = self.network.active_fault
        fault = self.network.faults[fault_name]
        fault_bus_idx = self.bus_indices[fault.bus]
        n = self.num_buses

        phase_currents: Dict[str, complex] = {
            name: 0.0 + 0.0j for name in self.network.branches
        }

        # Assemble phase admittance matrix (one entry per branch, regardless of
        # grounding_conductor: the phase conductor exists in both cases). Inactive
        # branches and branches with at least one inactive endpoint are skipped --
        # they cannot carry a phase current in the linearised model.
        Y_phase = np.zeros((n, n), dtype=complex)
        branch_y_phase: Dict[str, complex] = {}
        for branch in self.network.branches.values():
            if not branch.active:
                continue
            if branch.from_bus not in self.bus_indices:
                continue
            if branch.to_bus not in self.bus_indices:
                continue
            Z_complex = self._resolved_impedance(
                branch.self_impedance.get(freq), freq
            )
            if branch.type.grounding_conductor and Z_complex is not None:
                if _is_open_circuit(Z_complex):
                    y = 0.0 + 0.0j
                else:
                    y = 1.0 / Z_complex
            else:
                length = branch.length if branch.length and branch.length > 0 else 1.0
                y = complex(1.0 / length, 0.0)
            branch_y_phase[branch.name] = y
            fi = self.bus_indices[branch.from_bus]
            ti = self.bus_indices[branch.to_bus]
            Y_phase[fi, fi] += y
            Y_phase[ti, ti] += y
            Y_phase[fi, ti] -= y
            Y_phase[ti, fi] -= y

        # Reduce the system by pinning the fault bus to zero
        keep = [i for i in range(n) if i != fault_bus_idx]
        if not keep:
            return phase_currents
        Y_red = Y_phase[np.ix_(keep, keep)]

        scaling = fault.scalings.get(freq, 1)

        for source_name in sources_with_path:
            source = self.network.sources[source_name]
            src_idx = self.bus_indices.get(source.bus)
            if src_idx is None or src_idx == fault_bus_idx:
                continue
            I = self._source_injection(source, freq, scaling)
            if I is None:
                continue

            src_red_idx = keep.index(src_idx)
            i_red = np.zeros(len(keep), dtype=complex)
            i_red[src_red_idx] = I

            try:
                u_red = np.linalg.solve(Y_red, i_red)
            except np.linalg.LinAlgError:
                # Disconnected sub-network for this source; skip contribution
                continue

            u_full = np.zeros(n, dtype=complex)
            for j, bus_idx in enumerate(keep):
                u_full[bus_idx] = u_red[j]
            # u_full[fault_bus_idx] = 0 by construction

            for branch in self.network.branches.values():
                if branch.name not in branch_y_phase:
                    # Inactive branch or branch with inactive endpoint -- carries
                    # no phase current and was excluded from Y_phase above.
                    continue
                y = branch_y_phase[branch.name]
                fi = self.bus_indices[branch.from_bus]
                ti = self.bus_indices[branch.to_bus]
                i_branch = (u_full[fi] - u_full[ti]) * y
                phase_currents[branch.name] += i_branch

        return phase_currents

    def _add_mutual_currents(self, i_vector, freq, phase_currents):
        """
        Inject mutual-coupling Norton equivalents into the current vector.

        For each branch with a grounding conductor the phase current ``I_p`` through
        the parallel phase conductor induces an EMF across the shield which, combined
        with the shield self-impedance, is equivalent to a current source
        ``i_mut = (Z_mutual / Z_self) * I_p`` flowing from ``from_bus`` to ``to_bus``.
        In nodal form this appears as ``-i_mut`` at ``from_bus`` and ``+i_mut`` at
        ``to_bus``. For the downstream ``compute_branch_currents`` we store the
        *negated* value so that it combines with the legacy branch-current formula
        ``current = (u_to - u_from) * Y_self + i_mutual_stored``.

        Parameters
        ----------
        i_vector : np.ndarray
            Current vector to update in place.
        freq : float
            Frequency at which to evaluate the branch impedances.
        phase_currents : Dict[str, complex]
            Signed phase current per branch.
        """
        for branch in self.network.branches.values():
            if not branch.active:
                continue
            if not branch.type.grounding_conductor:
                continue
            if branch.from_bus not in self.bus_indices:
                continue
            if branch.to_bus not in self.bus_indices:
                continue
            Z_self_c = self._resolved_impedance(
                branch.self_impedance.get(freq), freq
            )
            Z_mutual = branch.mutual_impedance.get(freq)
            if Z_self_c is None or Z_mutual is None:
                continue
            Z_mutual_c = complex(Z_mutual.real, Z_mutual.imag)
            if _is_open_circuit(Z_self_c):
                continue

            i_phase = phase_currents.get(branch.name, 0.0 + 0.0j)
            i_mut = i_phase * (Z_mutual_c / Z_self_c)

            from_idx = self.bus_indices[branch.from_bus]
            to_idx = self.bus_indices[branch.to_bus]
            # Norton current flows from_bus -> to_bus (same direction as I_p)
            i_vector[from_idx] -= i_mut
            i_vector[to_idx] += i_mut

            # Stored with flipped sign so that compute_branch_currents keeps using
            # the formula current = (u_to - u_from) * Y_self + i_mutual_stored.
            stored = -i_mut
            if branch.name in self.i_mutuals[freq]:
                self.i_mutuals[freq][branch.name] += stored
            else:
                self.i_mutuals[freq][branch.name] = stored

    def _assert_finite_system(self, freq, Y_matrix, i_vector):
        """Reject a nodal system that contains NaN, naming the source.

        ``NaN`` in ``Y`` or ``i`` makes the LU factorisation fail or return
        garbage, and scipy's report for that is "singular matrix". That message
        describes a *topology* problem -- a network with no path to reference
        earth -- so an engineer reading it goes looking for a missing earth
        connection. The actual cause is arithmetic: an impedance formula that
        produced NaN, or a NaN written directly into ``bus.impedance`` /
        ``branch.self_impedance``. Say so, and say where.

        ``inf`` is deliberately not checked: it is the documented open-end
        sentinel and contributes ``1/inf == 0`` to the matrix, which is exactly
        right.

        Parameters
        ----------
        freq : float
            Frequency (Hz) of the system being checked, used in the message.
        Y_matrix : numpy.ndarray
            The nodal admittance matrix at ``freq``.
        i_vector : numpy.ndarray
            The injection vector at ``freq``.

        Raises
        ------
        ValueError
            If ``Y_matrix`` or ``i_vector`` contains NaN.
        """
        if not (np.isnan(Y_matrix).any() or np.isnan(i_vector).any()):
            return

        index_to_bus = {idx: name for name, idx in self.bus_indices.items()}

        culprit_buses = []
        for bus_name, bus in self.network.buses.items():
            if not bus.active or bus_name not in self.bus_indices:
                continue
            imp = bus.impedance.get(freq)
            if imp is not None and (np.isnan(imp.real) or np.isnan(imp.imag)):
                culprit_buses.append(bus_name)

        culprit_branches = []
        for branch in self.network.branches.values():
            if not branch.active:
                continue
            for label, store in (
                ("self", branch.self_impedance),
                ("mutual", branch.mutual_impedance),
            ):
                imp = store.get(freq) if store else None
                if imp is not None and (np.isnan(imp.real) or np.isnan(imp.imag)):
                    culprit_branches.append(f"{branch.name} ({label})")

        affected_rows = sorted(
            {int(row) for row in np.argwhere(np.isnan(Y_matrix))[:, 0]}
            | {int(row) for row in np.argwhere(np.isnan(i_vector)).ravel()}
        )
        affected_names = [
            index_to_bus.get(row, f"index {row}") for row in affected_rows
        ]

        parts = [
            f"NaN in the nodal system at f={freq} Hz -- the network cannot be "
            f"solved. This is a computation error, not a topology error: NaN "
            f"would surface from the LU factorisation as a misleading "
            f"'singular matrix'."
        ]
        if culprit_buses:
            parts.append(
                "Buses with a NaN grounding impedance: "
                + _shortlist(culprit_buses)
                + "."
            )
        if culprit_branches:
            parts.append(
                "Branches with a NaN impedance: " + _shortlist(culprit_branches) + "."
            )
        if not culprit_buses and not culprit_branches:
            parts.append(
                "No single bus or branch impedance is NaN, so the NaN entered "
                "through an injected current or a mutual coupling term. "
                "Affected matrix rows: " + _shortlist(affected_names) + "."
            )
        parts.append(
            "Check the impedance formula of the affected type(s) for a "
            "division by zero or a domain error (e.g. log(0) at f=0 Hz)."
        )
        raise ValueError(" ".join(parts))

    def _classify_bus_grounding(self, freq):
        """Sort the active buses by what their grounding impedance actually is.

        The four non-referencing outcomes are physically distinct and each has
        its own remedy, so they are kept apart instead of being collapsed into
        one "no path to reference earth".

        Parameters
        ----------
        freq : float
            Frequency (Hz) at which to read ``bus.impedance``.

        Returns
        -------
        dict
            Keys ``referenced`` (finite, non-zero -- these are the only buses
            that tie the network to earth), ``zero`` (``Z == 0``, only
            reachable by assigning to ``bus.impedance`` after the network was
            built, since :meth:`_validate_passive_impedances` rejects it
            otherwise), ``infinite`` (open end), ``nan`` (failed computation)
            and ``missing`` (no entry stored for this frequency); each maps to
            a list of bus names.
        """
        grouped = {
            "referenced": [],
            "zero": [],
            "infinite": [],
            "nan": [],
            "missing": [],
        }
        for bus_name, bus in self.network.buses.items():
            if not bus.active or bus_name not in self.bus_indices:
                continue
            imp = bus.impedance.get(freq)
            if imp is None:
                grouped["missing"].append(bus_name)
                continue
            zc = complex(imp.real, imp.imag)
            if np.isnan(zc.real) or np.isnan(zc.imag):
                grouped["nan"].append(bus_name)
            elif zc == 0:
                grouped["zero"].append(bus_name)
            elif not np.isfinite(zc):
                grouped["infinite"].append(bus_name)
            else:
                grouped["referenced"].append(bus_name)
        return grouped

    def _no_ground_reference_message(self, freq, grouped):
        """Build the diagnosis for a network with no path to reference earth.

        The word "Singular" is kept at the front because that is what the
        solver would have said, and because callers (and tests) match on it.
        Everything after it names which buses were looked at and why each one
        failed to provide a reference -- a bus written ``Z = 0`` needs a
        different fix from a bus left at the open-end sentinel.

        Parameters
        ----------
        freq : float
            Frequency (Hz) of the singular system.
        grouped : dict
            Output of :meth:`_classify_bus_grounding`.

        Returns
        -------
        str
            The full message for the :class:`ValueError`.
        """
        parts = [
            f"Singular admittance matrix at f={freq} Hz: no active bus is "
            f"referenced to earth, so the network has no path to reference "
            f"earth and its potential is undefined."
        ]
        if grouped["zero"]:
            parts.append(
                "Buses with Z = 0: "
                + _shortlist(grouped["zero"])
                + ". A zero grounding impedance is rejected when the network is "
                "built, so these values were assigned after construction and "
                "never reached the admittance matrix -- the diagonal still "
                "holds whatever was there before. Use a small finite value "
                "(e.g. 1e-6) for a near-ideal earth electrode and rebuild the "
                "network."
            )
        if grouped["infinite"]:
            parts.append(
                "Buses with an infinite Z (open-end sentinel, formula 'nan'): "
                + _shortlist(grouped["infinite"])
                + "."
            )
        if grouped["nan"]:
            parts.append(
                "Buses with a NaN Z (failed formula evaluation): "
                + _shortlist(grouped["nan"])
                + "."
            )
        if grouped["missing"]:
            parts.append(
                f"Buses with no impedance stored at {freq} Hz: "
                + _shortlist(grouped["missing"])
                + "."
            )
        if not any(
            grouped[key] for key in ("zero", "infinite", "nan", "missing")
        ):
            parts.append("No active bus is present in the nodal system at all.")
        parts.append(
            "Give at least one bus a finite, non-zero grounding impedance and "
            "make sure the fault bus is galvanically connected to it."
        )
        return " ".join(parts)

    def solve_network(self):
        """
        Solve the network equations Y * u = i for each frequency.

        This method computes the bus voltages by solving the admittance matrix equations for each frequency.
        The results are stored in the network's results object.
        It uses the csc_matrix and splu functions from scipy, assuming the Y-Matrix is a sparse matrix.

        .. warning::

            This **replaces** ``network.results[fault]`` with a fresh
            :class:`Result` that carries bus rows only. The branch rows are
            filled in by :meth:`compute_branch_currents`, which must be
            called afterwards -- :func:`groundinsight.run_fault` does exactly
            that. Calling ``solve_network`` on its own, e.g. on a hand-built
            :class:`ElectricalNetwork` used to inspect ``Y``, ``i`` or ``u``,
            therefore discards the branch results of a previous ``run_fault``.
            Clearing them is deliberate -- stale branch currents next to fresh
            bus voltages would be silently inconsistent -- and the thermal
            checks raise on the resulting gap rather than reporting an
            incomplete result as free of violations.

            To inspect the nodal system without touching the stored results,
            build the :class:`ElectricalNetwork` (its constructor has no side
            effects on ``network.results``) and read ``u`` back from
            ``network.results[fault].buses`` instead of re-solving.
        """
        fault_name = self.network.active_fault
        if fault_name is None:
            raise ValueError("No active fault set in the network.")

        result = Result(buses=[], branches=[], fault=fault_name)
        for freq in self.network.frequencies:
            Y_matrix = self.Y_matrices[freq]
            i_vector = self.i_vectors[freq]
            singular_msg = (
                f"Singular admittance matrix at f={freq} Hz: the network has no "
                "path to reference earth. Ensure at least one bus has a finite "
                "grounding impedance and that the fault bus is connected to it."
            )
            # A NaN anywhere in the nodal system poisons the factorisation, and
            # scipy reports that as a singular matrix -- which reads like a
            # topology error and sends the engineer looking for a missing
            # earth connection that is not missing. Catch it first and name
            # what is actually NaN.
            self._assert_finite_system(freq, Y_matrix, i_vector)

            # Exact, solver-independent floating-network guard. If no active bus
            # is referenced to earth at this frequency (every grounding impedance
            # is zero, infinite or missing) the admittance matrix is singular.
            # scipy's sparse ``splu`` handles a singular Y inconsistently across
            # versions -- it may raise ``RuntimeError``, return a non-finite
            # solution, or return an arbitrary finite one (a floating network has
            # no unique EPR) -- so the common floating case is caught structurally
            # here, before the solve, with no numerical tolerance.
            grouped = self._classify_bus_grounding(freq)
            if not grouped["referenced"]:
                raise ValueError(self._no_ground_reference_message(freq, grouped))
            try:
                # Backstops for any remaining singular case (e.g. a disconnected
                # ungrounded island): scipy may raise, or return a non-finite
                # solution.
                Y_matrix_sparse = csc_matrix(Y_matrix)
                lu = splu(Y_matrix_sparse)
                u_vector = lu.solve(i_vector)
            except (np.linalg.LinAlgError, RuntimeError) as e:
                raise ValueError(f"{singular_msg} Original solver error: {e}") from e
            if not np.all(np.isfinite(u_vector)):
                raise ValueError(singular_msg)
            self.u_vectors[freq] = u_vector

        # Create ResultBus instances. Inactive buses are not in ``bus_indices``
        # and therefore do not appear in the result -- they are physically
        # disconnected from the nodal system.
        for bus_name, idx in self.bus_indices.items():
            uepr_freq = {}
            ia_freq = {}
            i_inj_freq = {}
            for freq in self.network.frequencies:
                voltage = self.u_vectors[freq][idx]
                bus = self.network.buses.get(bus_name)
                Z_self_complex = self._resolved_impedance(
                    bus.impedance.get(freq), freq
                )
                if Z_self_complex is None:
                    current = 0
                else:
                    if _is_open_circuit(Z_self_complex):
                        # Open end (Z = inf): no electrode, so no current leaves
                        # into the soil here. Computing the quotient would give
                        # NaN and store it in the I_a column.
                        current = 0
                    else:
                        current = voltage / Z_self_complex

                uepr_freq[freq] = ComplexNumber(real=voltage.real, imag=voltage.imag)
                ia_freq[freq] = ComplexNumber(
                    real=complex(current).real, imag=complex(current).imag
                )
                # Source-only injection at this bus (0 at every bus that is
                # neither a source bus nor the fault bus).
                inj_vector = self.source_injections.get(freq)
                injection = 0j if inj_vector is None else complex(inj_vector[idx])
                i_inj_freq[freq] = ComplexNumber(
                    real=injection.real, imag=injection.imag
                )

            # Calculate RMS values
            rms_voltage = self._calculate_rms(uepr_freq)
            rms_current = self._calculate_rms(ia_freq)
            rms_injection = self._calculate_rms(i_inj_freq)

            result_bus = ResultBus(
                name=bus_name,
                uepr=rms_voltage,
                ia=rms_current,
                i_inj=rms_injection,
                uepr_freq=uepr_freq,
                ia_freq=ia_freq,
                i_inj_freq=i_inj_freq,
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
            i_s_freq = {}
            # Inactive branches and branches with at least one inactive endpoint
            # are open-circuited: their shield current is zero by construction.
            is_open = (
                not branch.active
                or branch.from_bus not in self.bus_indices
                or branch.to_bus not in self.bus_indices
            )
            if is_open:
                for freq in self.network.frequencies:
                    i_s_freq[freq] = ComplexNumber(real=0.0, imag=0.0)
                rms_current = 0.0
                result_branch = ResultBranch(
                    name=branch.name, i_s=rms_current, i_s_freq=i_s_freq
                )
                result.branches.append(result_branch)
                continue

            from_idx = self.bus_indices[branch.from_bus]
            to_idx = self.bus_indices[branch.to_bus]
            for freq in self.network.frequencies:
                from_voltage = self.u_vectors[freq][from_idx]
                to_voltage = self.u_vectors[freq][to_idx]
                Z_self_complex = self._resolved_impedance(
                    branch.self_impedance.get(freq), freq
                )
                if (
                    Z_self_complex is not None
                    and branch.type.grounding_conductor
                    and not _is_open_circuit(Z_self_complex)
                ):
                    Y_self_complex = 1 / Z_self_complex
                    delta_voltage = to_voltage - from_voltage

                    # Stored mutual term already carries the sign to combine with
                    # delta_voltage = u_to - u_from. See _add_mutual_currents.
                    i_mutual = self.i_mutuals.get(freq, {}).get(branch.name, 0)

                    current = delta_voltage * Y_self_complex + i_mutual

                    i_s_freq[freq] = ComplexNumber(real=current.real, imag=current.imag)
                else:
                    i_s_freq[freq] = ComplexNumber(real=0.0, imag=0.0)

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

        Parameters
        ----------
        freq_values : Dict[float, Optional[ComplexNumber]]
            A dictionary mapping frequencies
            to their corresponding ComplexNumber values.

        Returns
        -------
        float
            The calculated RMS value.
        """
        rms_squared = 0.0
        for value in freq_values.values():
            if value is None:
                continue
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
                logger.error(
                    "Error solving network equations at frequency %s without mutual currents: %s",
                    freq,
                    e,
                    exc_info=True,
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

        This method calculates the grounding impedance using the formula::

            Z_G = u_EPR / (reduction_factor * I_fault)

        where ``I_fault`` is the (signed) sum of source injections at the
        active fault. For current-mode sources this is ``Σ scaling * I_src``
        as before; for Thevenin (voltage-mode) sources it is the corresponding
        Norton injection ``Σ scaling * U_emf / Z_src``. In Thevenin mode the
        resulting ``Z_G`` is therefore the EPR per Norton ampere, which
        depends on both the grounding network and ``Z_src``; it recovers the
        classic grounding impedance in the limit ``Z_src -> ∞`` with
        ``U_emf = I_src * Z_src`` held constant.

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

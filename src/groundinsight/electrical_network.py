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
   network is solved with +I at each source bus and the fault bus as
   reference. The resulting branch currents are used directly. In this
   mode parallel_coefficient is ignored. This mode is topology-agnostic and
   is the intended integration point for an external phase-current source
   (e.g. pandapower single-phase short-circuit results).

Sparse kernel (audit pass 19)
-----------------------------
Both linear systems of a frequency are sparse -- a grounding network has a
few entries per row -- and the kernel treats them that way end to end.

*Model values as arrays.* The impedance dictionaries of all buses, branches
and voltage sources are read once per assembly into ``(elements, F)`` arrays
(:class:`_NetworkTables`), with the 0 Hz stand-in applied to them as a
whole. Every later stage indexes into those arrays instead of resolving one
impedance per element and frequency in Python.

*One factorisation, many right-hand sides.* The phase-conductor network is
the same matrix for every source, so it is factorised once per frequency
(:func:`scipy.sparse.linalg.splu`) and solved for all sources as the columns
of one right-hand side (finding P1). It used to be a dense ``n x n`` solve
per source and frequency -- the dominant cost of a run, growing faster than
``n**2``.

*Program order, kept on purpose.* Matrices are assembled from stamps whose
contributions are summed entry by entry in the order the former dense loops
added them (:func:`_assemble_csc`). What remains different is rounding: the
admittances come from NumPy's complex division, which differs from Python's
in the last bit for about a quarter of all values, and the factorisation is a
sparse LU instead of LAPACK's dense one. On the badly conditioned phase
network of a long radial feeder the sparse LU is the *more* accurate of the
two (finding P13).
"""

import gc
import hashlib
import logging
import math
import threading
from collections.abc import Mapping
from contextlib import contextmanager

import numpy as np
from typing import Dict, Iterator, List, Optional, Tuple
from scipy.sparse import csc_matrix, csr_matrix, issparse
from scipy.sparse.csgraph import connected_components
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
    _render_frequencies,
    _short_circuit_mask,
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


from groundinsight.diagnostics import InputWarning, ModelAssumptionWarning, emit
from groundinsight.utils.earth_current import (
    NOISE_REL,
    earth_current_vanishes,
    earthing_voltage,
    split_earth_currents,
)


def _render_points(points: List[Tuple[float, float, float]], unit: str) -> str:
    """Render ``(frequency, value, reference)`` triples for a diagnostic.

    Parameters
    ----------
    points : list of tuple of float
        One ``(frequency in Hz, magnitude, reference magnitude)`` per entry.
    unit : str
        Unit of both magnitudes, e.g. ``"A"`` or ``"V"``.

    Returns
    -------
    str
        ``"50 Hz (1.2e-14 A against 884 A), ..."``, shortened like
        :func:`_shortlist` beyond five entries.
    """
    rendered = [
        f"{freq:g} Hz ({value:.3g} {unit} against {reference:.4g} {unit})"
        for freq, value, reference in points
    ]
    return _shortlist(rendered)


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

    Notes
    -----
    ``math.isinf`` rather than ``numpy.isinf``: this is called once per
    (element, frequency) while the matrices are assembled -- 209 400 times
    in a 200-bus / 50-frequency profile -- and each ``numpy.isinf`` on a
    Python float goes through array coercion and scalar dispatch for a
    single bit of information. ``math.isinf`` answers the same question on
    the same C double roughly an order of magnitude faster.
    """
    return math.isinf(Z_complex.real) or math.isinf(Z_complex.imag)


_GC_PAUSE_LOCK = threading.Lock()
_GC_PAUSE_DEPTH = 0
_GC_PAUSE_RESTORE = False


@contextmanager
def _gc_paused() -> Iterator[None]:
    """Pause the cyclic garbage collector while result objects are built.

    A solve allocates one :class:`ComplexNumber` per element, quantity and
    frequency -- a quarter of a million objects for a 4000-bus ring at seven
    frequencies -- and every few hundred allocations trigger a collection
    that traverses the *whole* object graph, the network model included.
    None of these objects forms a reference cycle: they are freed by
    reference counting the moment they are dropped, so the traversals find
    nothing and only cost time (finding P5: half of the result-building time
    on large networks).

    The collector is disabled only if it was enabled when the first pause
    began, and enabled again when the last pause ends -- also when the
    building raises. Pauses may nest and may overlap across threads; a
    counter under a lock makes the collector come back exactly once. A
    collector the caller switched off stays off.

    Yields
    ------
    None
        Control, with the collector paused.
    """
    global _GC_PAUSE_DEPTH, _GC_PAUSE_RESTORE
    with _GC_PAUSE_LOCK:
        if _GC_PAUSE_DEPTH == 0:
            _GC_PAUSE_RESTORE = gc.isenabled()
            gc.disable()
        _GC_PAUSE_DEPTH += 1
    try:
        yield
    finally:
        with _GC_PAUSE_LOCK:
            _GC_PAUSE_DEPTH -= 1
            if _GC_PAUSE_DEPTH == 0 and _GC_PAUSE_RESTORE:
                gc.enable()


def _gather_impedances(
    dicts: List[Optional[Dict[float, ComplexNumber]]], frequencies: List[float]
) -> Tuple[np.ndarray, np.ndarray]:
    """Stack impedance dictionaries into one ``(elements, frequencies)`` array.

    Every vectorised step of the kernel starts here: the per-element
    dictionaries are read once per assembly, and from then on the kernel
    works on arrays instead of calling :meth:`ElectricalNetwork._resolved_impedance`
    once per element and frequency.

    Parameters
    ----------
    dicts : list of dict or None
        One impedance dictionary per element, in element order. ``None`` or an
        empty dictionary means "no value at any frequency".
    frequencies : list of float
        Column order.

    Returns
    -------
    values : numpy.ndarray
        Complex array of shape ``(len(dicts), len(frequencies))``. Missing
        entries are ``nan``.
    present : numpy.ndarray
        Boolean array of the same shape, ``True`` where the dictionary has an
        entry for that frequency.

    Notes
    -----
    The real and imaginary parts are written separately. Building the
    complex value as ``re + 1j*im`` would turn the open-end sentinel
    ``inf + inf*j`` into ``nan + inf*j``, because ``inf * 1j`` multiplies the
    zero real part of ``1j`` by infinity.
    """
    count, width = len(dicts), len(frequencies)
    nan = math.nan
    real: List[float] = []
    imag: List[float] = []
    found: List[bool] = []
    blank_re = [nan] * width
    blank_ok = [False] * width
    for mapping in dicts:
        if not mapping:
            real.extend(blank_re)
            imag.extend(blank_re)
            found.extend(blank_ok)
            continue
        get = mapping.get
        for freq in frequencies:
            value = get(freq)
            if value is None:
                real.append(nan)
                imag.append(nan)
                found.append(False)
            else:
                real.append(value.real)
                imag.append(value.imag)
                found.append(True)
    values = np.empty((count, width), dtype=complex)
    values.real = np.asarray(real, dtype=float).reshape(count, width)
    values.imag = np.asarray(imag, dtype=float).reshape(count, width)
    present = np.asarray(found, dtype=bool).reshape(count, width)
    return values, present


def _open_mask(values: np.ndarray) -> np.ndarray:
    """Vectorised :func:`_is_open_circuit`: ``True`` where a part is infinite."""
    return np.isinf(values.real) | np.isinf(values.imag)


def _reciprocal(values: np.ndarray, usable: np.ndarray) -> np.ndarray:
    """``1/Z`` where ``usable``, zero elsewhere -- without a NumPy warning.

    A NaN impedance gives a NaN admittance here exactly as Python's complex
    division did in the scalar loops, so a failed computation still reaches
    :meth:`ElectricalNetwork._assert_finite_system`.

    Parameters
    ----------
    values : numpy.ndarray
        Complex impedances.
    usable : numpy.ndarray
        Boolean mask of the entries to invert.

    Returns
    -------
    numpy.ndarray
        Complex admittances, ``0`` where ``usable`` is ``False``.
    """
    out = np.zeros(values.shape, dtype=complex)
    with np.errstate(over="ignore", invalid="ignore", divide="ignore"):
        np.divide(1.0, values, out=out, where=usable)
    return out


class _StampPattern:
    """The sparsity structure of a matrix assembled from a fixed list of stamps.

    The position of every stamp -- which bus, which branch end -- does not
    depend on the frequency; only its value does. The structure (CSC
    ``indices``/``indptr`` and the map from each stamp to its entry) is
    therefore computed once per network and reused for every frequency, which
    then costs one :func:`numpy.bincount` per real and imaginary part (finding
    P2: the dense assembly allocated and filled one ``n x n`` matrix per
    frequency in a Python loop, and converted each to CSC by scanning all
    ``n**2`` entries).

    :func:`numpy.bincount` adds the contributions of each entry starting from
    zero in the order they were stamped -- the same additions in the same
    order as the dense loop ``Y[r, c] += v``, hence the same rounding of the
    sums.

    Parameters
    ----------
    rows, cols : numpy.ndarray
        Integer row and column of every stamp, in stamp order.
    size : int
        Order of the square matrix.
    """

    def __init__(self, rows: np.ndarray, cols: np.ndarray, size: int):
        self.size = size
        keys = np.asarray(cols, dtype=np.int64) * size + np.asarray(
            rows, dtype=np.int64
        )
        unique, self.inverse = np.unique(keys, return_inverse=True)
        self.nnz = unique.size
        self.indices = (unique % size).astype(np.int32)
        indptr = np.zeros(size + 1, dtype=np.int64)
        if unique.size:
            np.cumsum(np.bincount(unique // size, minlength=size), out=indptr[1:])
        self.indptr = indptr.astype(np.int32)

    def matrix(self, values: np.ndarray) -> csc_matrix:
        """The matrix for one set of stamp values.

        Entries that sum to exactly zero are dropped, as ``csc_matrix(dense)``
        did; the structure arrays are then copied first, because they are
        shared by the matrices of all frequencies.

        Parameters
        ----------
        values : numpy.ndarray
            Complex value of every stamp, in stamp order.

        Returns
        -------
        scipy.sparse.csc_matrix
            The assembled matrix with sorted indices.
        """
        if self.nnz == 0:
            return csc_matrix((self.size, self.size), dtype=complex)
        data = np.empty(self.nnz, dtype=complex)
        data.real = np.bincount(self.inverse, weights=values.real, minlength=self.nnz)
        data.imag = np.bincount(self.inverse, weights=values.imag, minlength=self.nnz)
        if np.all(data != 0):
            return csc_matrix(
                (data, self.indices, self.indptr), shape=(self.size, self.size)
            )
        matrix = csc_matrix(
            (data, self.indices.copy(), self.indptr.copy()),
            shape=(self.size, self.size),
        )
        matrix.eliminate_zeros()
        return matrix


def _assemble_csc(
    rows: np.ndarray, cols: np.ndarray, values: np.ndarray, size: int
) -> csc_matrix:
    """Sum matrix stamps into a CSC matrix, every entry in stamp order.

    One-off use of :class:`_StampPattern`, for a matrix whose structure is
    not reused (the reduced phase network, whose rows depend on the galvanic
    component of the fault bus at each frequency).

    Parameters
    ----------
    rows, cols : numpy.ndarray
        Integer row and column of every stamp.
    values : numpy.ndarray
        Complex value of every stamp.
    size : int
        Order of the square matrix.

    Returns
    -------
    scipy.sparse.csc_matrix
        The assembled matrix with sorted indices.
    """
    return _StampPattern(rows, cols, size).matrix(values)


class _DenseYView(Mapping):
    """``Y_matrices`` as it used to be: frequency to dense ``numpy`` array.

    The solver works on :attr:`ElectricalNetwork.Y_sparse`. This read-only
    mapping keeps the historic attribute usable for code that reads a dense
    matrix -- each access materialises a fresh dense copy of the sparse
    matrix, so the ``n x n`` cost is paid only by whoever asks for it, and
    writing into the returned array does not reach the solve.

    Parameters
    ----------
    sparse : dict of float to scipy.sparse.csc_matrix
        The solver's matrices (held by reference, so a matrix replaced there
        is seen here).
    """

    def __init__(self, sparse: Dict[float, csc_matrix]):
        self._sparse = sparse

    def __getitem__(self, freq: float) -> np.ndarray:
        return self._sparse[freq].toarray()

    def __iter__(self) -> Iterator[float]:
        return iter(self._sparse)

    def __len__(self) -> int:
        return len(self._sparse)

    def __contains__(self, freq) -> bool:
        return freq in self._sparse

    def __repr__(self) -> str:
        return f"_DenseYView(frequencies={list(self._sparse)})"


def _extra_entries(
    dicts: List[Optional[Dict[float, ComplexNumber]]],
    frequencies: List[float],
    present: np.ndarray,
) -> Dict[int, tuple]:
    """Stored entries at frequencies the network is *not* solved at.

    The fingerprint has always covered every stored value of an element, not
    only the solved frequencies; :func:`_gather_impedances` reads only the
    latter. This collects the rest, for the rare element that has any.

    Parameters
    ----------
    dicts : list of dict or None
        The impedance dictionaries that were gathered.
    frequencies : list of float
        The gathered frequencies.
    present : numpy.ndarray
        The presence mask returned by :func:`_gather_impedances`.

    Returns
    -------
    dict of int to tuple
        Element index to the sorted ``(f, real, imag)`` entries outside
        ``frequencies``; elements without such entries are omitted.
    """
    found = present.sum(axis=1)
    solved = set(frequencies)
    extras: Dict[int, tuple] = {}
    for i, mapping in enumerate(dicts):
        if mapping and len(mapping) > found[i]:
            extras[i] = tuple(
                (float(f), float(z.real), float(z.imag))
                for f, z in sorted(mapping.items())
                if f not in solved
            )
    return extras


class _NetworkTables:
    """Everything one assembly reads from the model, gathered into arrays.

    A ``run_fault`` used to touch every bus and branch once per frequency and
    stage -- assembly, phase network, coupling terms, result rows -- each time
    through a Python-level dictionary lookup and :meth:`ElectricalNetwork._resolved_impedance`.
    On a 4000-bus ring at seven frequencies that was several hundred thousand
    calls before any linear algebra happened (finding P6). The tables are
    filled once per :class:`ElectricalNetwork` and every later stage indexes
    into them; the state fingerprint is a digest of the same arrays.

    Rows follow the model: ``bus_*`` arrays have one row per *active* bus in
    :attr:`ElectricalNetwork.bus_indices` order (``all_bus_*`` one per bus,
    active or not, in ``network.buses`` order), ``branch_*`` arrays one row
    per branch in ``network.branches`` order, ``vs_*`` arrays one row per
    voltage source in ``network.sources`` order.

    Attributes
    ----------
    frequencies : list of float
        Column order of every impedance array.
    column : dict of float to int
        Frequency to column.
    all_bus_names : list of str
        Every bus, in model order.
    all_bus_active : numpy.ndarray
        The ``active`` flag of every bus.
    all_bus_z_raw, all_bus_present : numpy.ndarray
        Stored grounding impedances of every bus, ``(n_all, F)``.
    bus_z_raw, bus_present : numpy.ndarray
        The rows of the active buses, ``(n, F)``.
    branch_names : list of str
        All branches, in model order.
    branch_from_name, branch_to_name : list of str
        The stored end buses of each branch.
    branch_from, branch_to : numpy.ndarray
        Bus index of each end, ``-1`` where that bus is inactive or missing.
    branch_active, branch_grounding : numpy.ndarray
        The ``active`` flag and ``type.grounding_conductor``.
    branch_in_net : numpy.ndarray
        Active, with both ends active: the branches that exist in the solve.
    self_raw, self_present : numpy.ndarray
        Stored self impedances, ``(m, F)``.
    mutual, mutual_present : numpy.ndarray
        Stored mutual impedances (never resolved: they are not inverted).
    phase_raw, phase_present : numpy.ndarray
        Stored phase impedances; ``phase_declared`` marks branches whose
        ``phase_impedance`` is not ``None`` at all.
    proxy_length : numpy.ndarray
        ``length`` where positive, else ``1.0`` -- the resistive proxy.
    vs_names, vs_bus_name : list of str
        Voltage sources in model order and the bus each one sits on.
    vs_bus : numpy.ndarray
        Bus index of each voltage source, ``-1`` where inactive or missing.
    vs_z_raw, vs_present : numpy.ndarray
        Stored source impedances, ``(s, F)``.
    bus_extras, self_extras, vs_extras : dict of int to tuple
        Stored entries at frequencies outside the solved ones, see
        :func:`_extra_entries`. Only the fingerprint reads them.
    bus_z, self_z, phase_z, vs_z : numpy.ndarray
        The same values with the 0 Hz stand-in applied
        (:meth:`ElectricalNetwork._resolved_impedance`, vectorised); filled
        by :meth:`resolve`.

    Parameters
    ----------
    electrical : ElectricalNetwork
        The network being assembled; its :attr:`~ElectricalNetwork.bus_indices`
        must be assigned.
    fingerprint_only : bool, optional
        Skip the mutual and phase impedances, which only the solve reads.
    """

    def __init__(self, electrical: "ElectricalNetwork", fingerprint_only: bool = False):
        network = electrical.network
        index = electrical.bus_indices
        self.frequencies = [float(f) for f in network.frequencies]
        self.column = {f: j for j, f in enumerate(self.frequencies)}
        freqs = self.frequencies

        self.all_bus_names = list(network.buses)
        all_buses = list(network.buses.values())
        self.all_bus_active = np.fromiter(
            (bool(bus.active) for bus in all_buses), dtype=bool,
            count=len(all_buses),
        )
        impedances = [bus.impedance for bus in all_buses]
        self.all_bus_z_raw, self.all_bus_present = _gather_impedances(
            impedances, freqs
        )
        self.bus_extras = _extra_entries(impedances, freqs, self.all_bus_present)
        # bus_indices enumerates the active buses in model order
        active_rows = np.flatnonzero(self.all_bus_active)
        self.bus_z_raw = self.all_bus_z_raw[active_rows]
        self.bus_present = self.all_bus_present[active_rows]

        branches = list(network.branches.values())
        count = len(branches)
        self.branch_names = [branch.name for branch in branches]
        self.branch_from_name = [branch.from_bus for branch in branches]
        self.branch_to_name = [branch.to_bus for branch in branches]
        self.branch_from = np.fromiter(
            (index.get(name, -1) for name in self.branch_from_name),
            dtype=np.int64, count=count,
        )
        self.branch_to = np.fromiter(
            (index.get(name, -1) for name in self.branch_to_name),
            dtype=np.int64, count=count,
        )
        self.branch_active = np.fromiter(
            (bool(b.active) for b in branches), dtype=bool, count=count
        )
        self.branch_grounding = np.fromiter(
            (bool(b.type.grounding_conductor) for b in branches), dtype=bool,
            count=count,
        )
        self.branch_in_net = (
            self.branch_active & (self.branch_from >= 0) & (self.branch_to >= 0)
        )
        self_impedances = [b.self_impedance for b in branches]
        self.self_raw, self.self_present = _gather_impedances(self_impedances, freqs)
        self.self_extras = _extra_entries(self_impedances, freqs, self.self_present)
        # The fingerprint does not read the mutual and phase impedances --
        # they never enter Y -- so a fingerprint-only gather skips them.
        coupling = [] if fingerprint_only else branches
        self.mutual, self.mutual_present = _gather_impedances(
            [b.mutual_impedance for b in coupling], freqs
        )
        self.phase_declared = np.fromiter(
            (b.phase_impedance is not None for b in coupling), dtype=bool,
            count=len(coupling),
        )
        self.phase_raw, self.phase_present = _gather_impedances(
            [b.phase_impedance for b in coupling], freqs
        )
        self.proxy_length = np.fromiter(
            (b.length if b.length and b.length > 0 else 1.0 for b in branches),
            dtype=float, count=count,
        )

        vsources = [
            s for s in network.sources.values() if s.source_type == "voltage"
        ]
        self.vs_names = [s.name for s in vsources]
        self.vs_bus_name = [s.bus for s in vsources]
        self.vs_bus = np.fromiter(
            (index.get(name, -1) for name in self.vs_bus_name), dtype=np.int64,
            count=len(vsources),
        )
        source_impedances = [s.source_impedance for s in vsources]
        self.vs_z_raw, self.vs_present = _gather_impedances(source_impedances, freqs)
        self.vs_extras = _extra_entries(source_impedances, freqs, self.vs_present)
        self.resolve({})

    def resolve(self, substitutes: Dict[float, float]) -> None:
        """Apply the 0 Hz stand-in to every impedance that is inverted.

        Vectorised :meth:`ElectricalNetwork._resolved_impedance`: where a
        frequency has a substitute, every stored value that is a short circuit
        in the sense of :func:`~groundinsight.utils.impedance_calculator.is_short_circuit`
        is replaced by it. Mutual impedances are never inverted and stay raw.

        Parameters
        ----------
        substitutes : dict of float to float
            :attr:`ElectricalNetwork._dc_substitutes`.
        """
        self.bus_z = self._substituted(self.bus_z_raw, self.bus_present, substitutes)
        self.self_z = self._substituted(
            self.self_raw, self.self_present, substitutes
        )
        self.phase_z = self._substituted(
            self.phase_raw, self.phase_present, substitutes
        )
        self.vs_z = self._substituted(self.vs_z_raw, self.vs_present, substitutes)

    def _substituted(
        self,
        values: np.ndarray,
        present: np.ndarray,
        substitutes: Dict[float, float],
    ) -> np.ndarray:
        """Copy of ``values`` with the shorted entries of substituted columns
        replaced; ``values`` itself when nothing is substituted."""
        columns = [
            (self.column[f], s) for f, s in substitutes.items() if f in self.column
        ]
        if not columns or values.size == 0:
            return values
        out = values.copy()
        for j, substitute in columns:
            col = out[:, j]
            shorted = _short_circuit_mask(col.real, col.imag) & present[:, j]
            col[shorted] = complex(substitute, 0.0)
        return out

    def thevenin_loops(
        self, fault_bus_idx: Optional[int]
    ) -> Tuple[np.ndarray, np.ndarray, np.ndarray]:
        """The loop closures of the voltage sources, for every frequency.

        A voltage source on an active bus other than the fault bus closes its
        loop with ``1/Z_src`` between its bus and the fault bus, at every
        frequency with a stored, not open, source impedance (0 Hz stand-in
        applied). This is the one rule behind both the assembly of ``Y`` and
        :meth:`ElectricalNetwork.thevenin_loop_admittances`.

        Parameters
        ----------
        fault_bus_idx : int or None
            Row of the active fault bus; ``None`` without an active fault.

        Returns
        -------
        loops : numpy.ndarray
            Indices into the voltage-source tables (``vs_*``) of the sources
            that close a loop, in network order.
        admittance : numpy.ndarray
            ``(len(loops), F)`` complex ``1/Z_src``; zero where unusable.
        usable : numpy.ndarray
            ``(len(loops), F)`` boolean: a finite source impedance is stored.

        Notes
        -----
        Requires :meth:`resolve` to have run.
        """
        width = len(self.frequencies)
        if fault_bus_idx is None:
            return (
                np.zeros(0, dtype=np.int64),
                np.zeros((0, width), dtype=complex),
                np.zeros((0, width), dtype=bool),
            )
        loops = np.flatnonzero((self.vs_bus >= 0) & (self.vs_bus != fault_bus_idx))
        z_src = self.vs_z[loops]
        usable = self.vs_present[loops] & ~_open_mask(z_src)
        return loops, _reciprocal(z_src, usable), usable

    def fingerprint(self, active_fault: Optional[str]) -> tuple:
        """Digest of every model value the nodal matrices are assembled from.

        Covers what :meth:`ElectricalNetwork.state_fingerprint` has always
        covered -- frequencies, active fault, every bus (name, ``active``,
        grounding impedance), every branch (name, ``active``,
        ``grounding_conductor``, end buses, self impedance) and every voltage
        source (name, bus, source impedance), including stored values at
        frequencies outside the solved ones -- with elements ordered by name,
        so the insertion order of the model does not matter. The values enter
        a BLAKE2 digest as raw bytes instead of a tuple of Python floats with
        one entry per element and frequency. Built from tables the assembly
        has gathered anyway, the digest costs 5 ms on a 4000-bus ring at
        seven frequencies, where the separate pass over the model that built
        the tuple took 27 ms on every build (finding P6). Negative zeros are
        folded into zeros first, so that ``0.0`` and ``-0.0`` compare equal as
        they did in the tuple.

        Parameters
        ----------
        active_fault : str or None
            The active fault of the network.

        Returns
        -------
        tuple
            ``(frequencies, active fault, digest)``; hashable and only
            meaningful for equality.
        """
        digest = hashlib.blake2b(digest_size=20)

        def strings(values: List[str]) -> None:
            encoded = [v.encode("utf-8", "surrogatepass") for v in values]
            digest.update(
                np.fromiter((len(e) for e in encoded), dtype=np.int64,
                            count=len(encoded)).tobytes()
            )
            digest.update(b"".join(encoded))

        def numbers(values: np.ndarray) -> None:
            digest.update(np.ascontiguousarray(values).tobytes())

        def block(tag, names, order, flags, values, present, extras, ends=()):
            digest.update(tag)
            rows = np.asarray(order, dtype=np.int64)
            strings([names[i] for i in order])
            for flag in flags:
                numbers(flag[rows])
            for end in ends:
                strings([end[i] for i in order])
            numbers(values[rows] + 0j)
            numbers(present[rows])
            strings([repr((names[i], extras[i])) for i in order if i in extras])

        def by_name(names: List[str]) -> List[int]:
            return sorted(range(len(names)), key=names.__getitem__)

        block(
            b"buses", self.all_bus_names, by_name(self.all_bus_names),
            (self.all_bus_active,), self.all_bus_z_raw, self.all_bus_present,
            self.bus_extras,
        )
        block(
            b"branches", self.branch_names, by_name(self.branch_names),
            (self.branch_active, self.branch_grounding), self.self_raw,
            self.self_present, self.self_extras,
            ends=(self.branch_from_name, self.branch_to_name),
        )
        block(
            b"voltage sources", self.vs_names, by_name(self.vs_names), (),
            self.vs_z_raw, self.vs_present, self.vs_extras,
            ends=(self.vs_bus_name,),
        )
        return (tuple(self.frequencies), active_fault, digest.digest())


class ElectricalNetwork:
    """
    Represents the electrical properties of a network, enabling calculations.

    This class handles the construction of admittance matrices, voltage and current vectors,
    and performs network analysis to compute results such as bus voltages, branch currents,
    reduction factors, and grounding impedances.

    Notes
    -----
    An instance is copied and pickled together with the :class:`Network` it
    is attached to (``copy.deepcopy(network)``, ``network.model_copy(deep=True)``,
    ``pickle.dumps(network)``). Every attribute travels with it except the
    entries named in :attr:`_UNCOPYABLE_CACHES`: those hold SciPy ``SuperLU``
    factorisations, which can be neither pickled nor deep-copied. They are
    pure caches -- a copy starts without them and the solver refactorises
    when it needs a factor again -- so dropping them loses no information.
    The back-reference :attr:`network` is copied through the copy memo and
    therefore points at the *copied* network, which is what
    :meth:`matches_network_state` needs.
    """

    #: Attributes that hold solver factorisations rather than model data.
    #: They are emptied in every copy and pickle (see the class notes). A new
    #: cache of SciPy factor objects has to be listed here, or a solved
    #: network stops being copyable again.
    _UNCOPYABLE_CACHES: Tuple[str, ...] = ("_lu_factors",)

    def __getstate__(self) -> Dict[str, object]:
        """
        State for ``pickle`` and ``copy.deepcopy``, without the factor caches.

        ``copy.deepcopy`` and ``pickle`` both go through this method, so one
        override covers both. The caches are replaced by empty dictionaries,
        not removed, so that every method can keep reading them as before.

        Returns
        -------
        Dict[str, object]
            A shallow copy of the instance dictionary with every entry of
            :attr:`_UNCOPYABLE_CACHES` replaced by an empty dict.
        """
        state = self.__dict__.copy()
        for name in self._UNCOPYABLE_CACHES:
            if name in state:
                state[name] = {}
        return state

    def __init__(
        self,
        network: Network,
        auto_phase_currents: bool = False,
        *,
        input_checks: bool = False,
    ):
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
        input_checks : bool, optional
            Run the input checks of :func:`~groundinsight.run_fault` while the
            vectors are assembled: a source that reaches the fault with a
            value at none of the network frequencies raises ``ValueError``
            (USE3/F8); partial source coverage, an incomplete
            ``Fault.scalings`` (F7) and a Thevenin source driving coupled
            branches (F5) are announced. :func:`~groundinsight.build_electrical_network`,
            the step ``run_fault`` uses, turns them on. Other users of the
            assembly leave them off -- the phase-split probe of the transient
            solver injects 1 A per source whatever its stationary values, and
            a source that only carries a transient waveform is legitimate
            there. Defaults to False.
        """
        self.network = network
        self.auto_phase_currents = auto_phase_currents
        self._input_checks = input_checks
        self.bus_indices = {}
        # Nodal admittance matrix per frequency, CSC (the solver's matrices).
        self.Y_sparse: Dict[float, csc_matrix] = {}
        # Dense view of the same matrices, materialised on access only; see
        # :class:`_DenseYView`. Kept for code that reads ``Y_matrices``.
        self.Y_matrices = {}
        self.u_vectors = {}  # Voltage vectors for each frequency
        self.u_vectors_no_mutual = {}  # Voltage vectors without mutual currents
        self.i_vectors_no_mutual = {}  # Current vectors without mutual currents
        self.i_vectors = {}  # Current vectors for each frequency
        # Sparse LU factorisation of Y(f), kept per frequency by
        # ``solve_network`` so that ``compute_reduction_factors`` can reuse it
        # for the second right-hand side instead of factorising the same
        # matrix a second time with a dense solver. Cleared whenever the
        # matrices are rebuilt.
        self._lu_factors = {}
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
        # One-shot latch so the phase-impedance proxy warning is emitted once
        # per network, not once per frequency of the sweep.
        self._phase_proxy_warned = False
        # Sources that reach the fault through the earthing network but not
        # through the phase network, with the frequencies concerned; reported
        # once per source after the frequency loop (PH9).
        self._sources_without_phase_path: Dict[str, List[float]] = {}
        # Substitute impedance per frequency for elements that are a short
        # circuit there. Only 0 Hz can have an entry, and only when the network
        # actually contains such an element. See _dc_substitute_at.
        self._dc_substitutes: Dict[float, float] = {}
        # Digest of the model values the matrices below were assembled from.
        # Filled once the assembly is done; see :meth:`state_fingerprint`.
        self._build_fingerprint: Optional[tuple] = None
        # The model values of this assembly as arrays, gathered once (P6);
        # see :class:`_NetworkTables` and :meth:`_network_tables`.
        self._tables: Optional[_NetworkTables] = None
        # The grounding admittance stamped on the diagonal per active bus and
        # frequency, ``(n, F)``; see :meth:`_grounding_shunts`.
        self._bus_admittance: Optional[np.ndarray] = None

        self._initialize()
        # The tables were gathered from this very state a moment ago; digest
        # them instead of reading the model a second time.
        self._build_fingerprint = self._network_tables().fingerprint(
            self.network.active_fault
        )

    def state_fingerprint(self) -> tuple:
        """
        Digest of every model value the assembled matrices depend on.

        The assembled system is a *snapshot*: once ``Y`` is built, later
        changes to a bus impedance, to an ``active`` flag or to the active
        fault do not reach it. Anything that reads ``Y_sparse`` back out
        afterwards -- :func:`~groundinsight.analysis.response.bus_response`
        above all -- therefore has to be able to tell whether the snapshot
        still describes the network in hand. This fingerprint is that test:
        it covers exactly the inputs :meth:`_construct_Y_matrices` reads, in
        their stored form, so an equal fingerprint means an equal matrix.

        Returns
        -------
        tuple
            Hashable digest. Compare it against :attr:`_build_fingerprint`
            (or use :meth:`matches_network_state`); the value itself carries
            no meaning beyond equality.

        Notes
        -----
        The model is read afresh into arrays (:class:`_NetworkTables`) and
        digested with BLAKE2 (:meth:`_NetworkTables.fingerprint`). Up to pass
        18 this was a nested tuple with one Python float triple per element
        and frequency, built in a separate pass over the model on every
        assembly (finding P6). The coverage is the same, including values
        stored at frequencies the network is not solved at. A call here reads
        the model afresh and costs about what the tuple did (38 ms against
        27 ms on a 4000-bus ring at seven frequencies); the fingerprint of a
        build reuses the tables of the assembly and costs 5 ms.
        """
        return _NetworkTables(self, fingerprint_only=True).fingerprint(
            self.network.active_fault
        )

    def matches_network_state(self) -> bool:
        """
        ``True`` while the assembled matrices still describe ``self.network``.

        See :meth:`state_fingerprint` for what "still describe" covers.
        """
        if self._build_fingerprint is None:  # pragma: no cover -- mid-assembly
            return True
        return self.state_fingerprint() == self._build_fingerprint

    def _initialize(self):
        """
        Initialize bus indices and other necessary data structures.

        This method assigns indices to buses, constructs admittance matrices,
        and builds voltage and current vectors for each frequency.
        """
        self._assign_bus_indices()
        self._validate_frequency_coverage()
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

    def _validate_frequency_coverage(self):
        """Reject an active element that has no impedance at a solved frequency.

        Impedance dictionaries are filled once, when the element is added to
        the network or when its type is re-evaluated -- never at solve time.
        A frequency that is in ``network.frequencies`` but not in the
        element's dictionary therefore reaches ``_construct_Y_matrices`` as
        ``None``, and the element is skipped **without a word**: the bus
        contributes no admittance, the branch no coupling. The result looks
        like an ordinary solve.

        The way into that state is ordinary too. Extending
        ``network.frequencies`` in place and recomputing only the buses --
        or loading a network written before a frequency was added -- was
        measured to report 1000 V instead of 4.95 V at 250 Hz, with a
        reduction factor of 1.0, and nothing in the output said why.

        Only the elements that actually enter the nodal system are checked:
        active buses, active grounding-conductor branches between two active
        buses, and the source impedance of voltage sources on an active bus.
        A stored frequency that the network is *not* solving at stays
        permitted -- see :meth:`_relevant`.

        Raises
        ------
        ValueError
            If an active element is missing an entry for a frequency the
            network is being solved at. The message names the element, the
            missing frequencies and how to refill them.
        """
        frequencies = list(self.network.frequencies)
        if not frequencies:
            return

        missing: List[Tuple[str, List[float]]] = []

        def _check(z_dict, label: str) -> None:
            absent = [
                freq
                for freq in frequencies
                if not z_dict or freq not in z_dict
            ]
            if absent:
                missing.append((label, absent))

        for bus_name, bus in self.network.buses.items():
            if not bus.active:
                continue
            _check(bus.impedance, f"bus '{bus_name}' (grounding impedance)")

        for branch in self.network.branches.values():
            if not branch.active:
                continue
            if branch.from_bus not in self.bus_indices:
                continue
            if branch.to_bus not in self.bus_indices:
                continue
            if branch.type.grounding_conductor:
                _check(
                    branch.self_impedance,
                    f"branch '{branch.name}' (self impedance)",
                )
            _check(
                branch.mutual_impedance,
                f"branch '{branch.name}' (mutual impedance)",
            )

        for source in self.network.sources.values():
            if source.source_type != "voltage":
                continue
            if source.bus not in self.bus_indices:
                continue
            _check(
                source.source_impedance,
                f"source '{source.name}' (source impedance)",
            )

        if not missing:
            return

        details = "; ".join(
            f"{label} at {_render_frequencies(freqs)}" for label, freqs in missing
        )
        raise ValueError(
            "The network is being solved at "
            f"{_render_frequencies(frequencies)}, but the following active "
            f"elements carry no impedance there: {details}. Impedances are "
            "evaluated when an element is added to the network, not at solve "
            "time, so a frequency added to network.frequencies afterwards -- "
            "or a network stored before that frequency existed -- leaves the "
            "element without a value. It would then be dropped from the "
            "admittance matrix without a word, which is indistinguishable "
            "from an element with no electrode at all. Recompute the "
            "impedances for the current frequency list -- "
            "element.calculate_impedance(network.frequencies) for every "
            "affected bus and branch, or re-add the elements -- before "
            "solving."
        )

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

    def _passive_impedances_ok(self, tables: _NetworkTables) -> bool:
        """Vectorised pass/fail of :meth:`_validate_passive_impedances`.

        The same three rules as
        :func:`~groundinsight.utils.impedance_calculator.check_passive_impedance`
        -- exactly zero above 0 Hz, too small to invert above 0 Hz, negative
        real part at any frequency -- over the same elements (active buses,
        grounding-conductor branches between active buses, voltage sources on
        an active bus) and the same frequencies, evaluated on whole arrays.
        Calling the element-wise check for every element cost 0.19 s on a
        4000-bus ring (finding P6) although it almost never finds anything;
        when this check does fail, the element-wise one runs and raises with
        its full message, so there is still only one copy of that message.

        Parameters
        ----------
        tables : _NetworkTables
            The gathered model arrays (stored values, before the 0 Hz
            stand-in).

        Returns
        -------
        bool
            ``True`` when no checked impedance violates a rule.
        """
        above_dc = np.asarray(tables.frequencies, dtype=float)[None, :] != 0.0

        def passes(values: np.ndarray, present: np.ndarray) -> bool:
            if values.size == 0:
                return True
            re, im = values.real, values.imag
            finite = np.isfinite(re) & np.isfinite(im)
            short = _short_circuit_mask(re, im)
            exact_zero = (re == 0.0) & (im == 0.0) & finite
            bad = (
                (exact_zero & above_dc)
                | (short & ~exact_zero & above_dc)
                | (finite & ~short & (re < 0.0))
            )
            return not np.any(bad & present)

        grid = tables.branch_in_net & tables.branch_grounding
        on_bus = tables.vs_bus >= 0
        return (
            passes(tables.bus_z_raw, tables.bus_present)
            and passes(tables.self_raw[grid], tables.self_present[grid])
            and passes(tables.vs_z_raw[on_bus], tables.vs_present[on_bus])
        )

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

        Notes
        -----
        **Index tables instead of dense matrices** (audit pass 19, P2/P6). The
        matrices are kept as CSC in :attr:`Y_sparse`. The position of every
        stamp -- bus diagonal, the four entries of a grounding branch, the four
        entries of a Thevenin loop closure -- is fixed by the topology, so
        those positions are laid out once (:class:`_StampPattern`) and each
        frequency only computes the admittances and sums them per entry,
        in the order the former dense loops added them. Until pass 18 every
        frequency allocated a dense ``n x n`` matrix and *all* of them were
        kept: 2.3 GB on a 4000-bus ring at seven frequencies, with a hard limit
        near 5000 buses on an 8 GB machine, plus a scan of all ``n**2`` entries
        to convert each one to CSC. :attr:`Y_matrices` remains as a dense view
        that materialises a matrix only when it is read.

        Validation is unchanged in effect: the passivity rules run on the
        gathered arrays (:meth:`_passive_impedances_ok`), and only when they
        find something does the element-wise
        :meth:`_validate_passive_impedances` run to raise its message.
        """
        tables = _NetworkTables(self)
        self._tables = tables
        if not self._passive_impedances_ok(tables):
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
        tables.resolve(self._dc_substitutes)

        # Stamp positions, in the order of the former dense loops: bus
        # diagonals (active buses), then per grounding branch (from, to),
        # (to, from), (from, from), (to, to), then per Thevenin source
        # (src, src), (fault, fault), (src, fault), (fault, src). Inactive
        # branches and branches with an inactive end are not stamped at all;
        # a value missing or open at one frequency stamps zero there.
        n = self.num_buses
        diagonal = np.arange(n, dtype=np.int64)
        grid = np.flatnonzero(tables.branch_in_net & tables.branch_grounding)
        g_from, g_to = tables.branch_from[grid], tables.branch_to[grid]
        # The loop closures come from the same rule as
        # :meth:`thevenin_loop_admittances` (one place, see there).
        loops, y_loop, _ = tables.thevenin_loops(fault_bus_idx)
        l_src = tables.vs_bus[loops]
        l_fault = np.full(loops.size, fault_bus_idx or 0, dtype=np.int64)
        rows = np.concatenate([
            diagonal,
            np.stack([g_from, g_to, g_from, g_to], axis=1).ravel(),
            np.stack([l_src, l_fault, l_src, l_fault], axis=1).ravel(),
        ])
        cols = np.concatenate([
            diagonal,
            np.stack([g_to, g_from, g_from, g_to], axis=1).ravel(),
            np.stack([l_src, l_fault, l_fault, l_src], axis=1).ravel(),
        ])
        pattern = _StampPattern(rows, cols, n)

        y_bus = _reciprocal(
            tables.bus_z, tables.bus_present & ~_open_mask(tables.bus_z)
        )
        self._bus_admittance = y_bus
        z_grid = tables.self_z[grid]
        y_grid = _reciprocal(
            z_grid, tables.self_present[grid] & ~_open_mask(z_grid)
        )
        self.Y_sparse = {}
        for j, freq in enumerate(frequencies):
            y_g, y_l = y_grid[:, j], y_loop[:, j]
            values = np.concatenate([
                y_bus[:, j],
                np.stack([-y_g, -y_g, y_g, y_g], axis=1).ravel(),
                np.stack([y_l, y_l, -y_l, -y_l], axis=1).ravel(),
            ])
            self.Y_sparse[freq] = pattern.matrix(values)
        self.Y_matrices = _DenseYView(self.Y_sparse)

    def thevenin_loop_admittances(self, freq: float) -> List[Tuple[int, int, complex]]:
        """
        The loop-closure stamps the Thevenin sources put into ``Y(freq)``.

        A voltage source closes its loop with ``Y_src = 1/Z_src`` between its
        own bus and the active fault bus: the non-grounding part of the fault
        loop (transformer, phase conductor) that the earthing network does not
        model. :meth:`_construct_Y_matrices` stamps them and
        :meth:`earthing_admittance_matrix` takes them back out, both from
        :meth:`_NetworkTables.thevenin_loops` -- one rule in one place, so
        the two cannot drift apart, and the values here are bit for bit the
        ones in :attr:`Y_sparse`.

        Parameters
        ----------
        freq : float
            Frequency in Hz, one of the network frequencies.

        Returns
        -------
        list of tuple of (int, int, complex)
            ``(source bus row, fault bus row, Y_src)`` per voltage source on
            an active bus other than the fault bus, with a finite source
            impedance at ``freq``. Empty without an active fault, and for a
            frequency the network is not solved at (``Y(freq)`` does not
            exist there).
        """
        active_fault = self.network.active_fault
        if active_fault is None:
            return []
        fault_bus_idx = self.bus_indices.get(self.network.faults[active_fault].bus)
        if fault_bus_idx is None:
            return []
        tables = self._network_tables()
        column = tables.column.get(float(freq))
        if column is None:
            return []
        loops, admittance, usable = tables.thevenin_loops(fault_bus_idx)
        stamped = usable[:, column]
        return [
            (src_idx, fault_bus_idx, y_src)
            for src_idx, y_src in zip(
                tables.vs_bus[loops][stamped].tolist(),
                admittance[stamped, column].tolist(),
            )
        ]

    def earthing_admittance_matrix(self, freq: float) -> np.ndarray:
        """
        ``Y(freq)`` of the earthing network alone, Thevenin loops removed.

        The nodal matrix that is solved also carries the loop closures of the
        voltage sources (:meth:`thevenin_loop_admittances`). They belong to
        the fault loop, not to the earthing system, so a property *of the
        earthing system* -- its driving-point impedance at the fault bus,
        ``Z_E`` of EN 50522 -- is taken from this matrix instead. Without a
        voltage source it is a copy of the solved one.

        Parameters
        ----------
        freq : float
            Frequency in Hz; ``Y_sparse[freq]`` must exist.

        Returns
        -------
        numpy.ndarray
            A new dense array; the assembled matrix is not modified.

        Notes
        -----
        Like :attr:`Y_matrices` this is a dense view, materialised on
        request: ``Z_E`` itself is computed from the sparse form
        (:meth:`_earthing_admittance_sparse`), so ``run_fault`` never builds
        an ``n x n`` array for it.
        """
        return self._earthing_admittance_sparse(freq).toarray()

    def _earthing_admittance_sparse(self, freq: float) -> csc_matrix:
        """
        :meth:`earthing_admittance_matrix` in CSC: ``Y_sparse[freq]`` minus
        the Thevenin loop closures (see :meth:`_remove_loop_closures`).

        Parameters
        ----------
        freq : float
            Frequency in Hz; ``Y_sparse[freq]`` must exist.

        Returns
        -------
        scipy.sparse.csc_matrix
            A new matrix; ``Y_sparse[freq]`` is not modified.
        """
        return self._remove_loop_closures(self.Y_sparse[freq], freq)

    def _remove_loop_closures(self, matrix: csc_matrix, freq: float) -> csc_matrix:
        """
        ``matrix`` with the Thevenin loop-closure stamps of ``freq`` taken out.

        The stamps of :meth:`thevenin_loop_admittances` are subtracted source
        by source, exactly as the dense definition of the earthing matrix
        does it -- every entry is bit for bit the one the dense
        ``Y - loops`` gives for the same ``Y`` -- and entries that cancel to
        zero leave the sparsity pattern. Used for the earthing matrix of the
        solved system and, in :func:`~groundinsight.analysis.response.bus_response`,
        for the solved system with one electrode removed.

        Parameters
        ----------
        matrix : scipy.sparse.csc_matrix
            A nodal matrix of this network at ``freq`` that carries the loop
            closures.
        freq : float
            Frequency in Hz.

        Returns
        -------
        scipy.sparse.csc_matrix
            A new matrix (a copy when there is no loop closure); ``matrix``
            is not modified.
        """
        size = matrix.shape[0]
        earthing = matrix.copy()
        for src_idx, loop_idx, y_src in self.thevenin_loop_admittances(freq):
            loop = _assemble_csc(
                np.array([src_idx, loop_idx, src_idx, loop_idx], dtype=np.int64),
                np.array([src_idx, loop_idx, loop_idx, src_idx], dtype=np.int64),
                np.array([y_src, y_src, -y_src, -y_src], dtype=complex),
                size,
            )
            earthing = csc_matrix(earthing - loop)
        return earthing

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
        if self._input_checks:
            # The run_fault checks of the input (see ``input_checks``).
            self._check_source_coverage(sources_with_path, fault)
            self._warn_incomplete_scalings(fault)
            self._warn_thevenin_coupling(sources_with_path, fault)

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
                scaling = fault.scaling_at(freq)
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

        self._report_sources_without_phase_path(fault)

    def _report_sources_without_phase_path(self, fault: Fault) -> None:
        """
        Say what a source without phase-conductor path to the fault still does.

        Such a source reaches the fault through the earthing network -- which
        is why it was considered at all -- but the phase conductor between
        them is interrupted (an inactive branch, a phase conductor declared
        open). No phase current flows from it, so it induces no coupling, yet
        its current is still injected into the earthing system at its bus and
        drawn out at the fault bus: it is treated as if it returned entirely
        through soil and shields, reduction factor 1 for its share. That is the
        conservative reading of its share -- no coupling relief -- and is kept;
        the message makes it explicit (PH9). Physically such a source cannot
        feed the fault at all.

        Parameters
        ----------
        fault : Fault
            The active fault.
        """
        for name, freqs in self._sources_without_phase_path.items():
            source = self.network.sources[name]
            emit(
                ModelAssumptionWarning,
                f"Source '{name}' at bus '{source.bus}' reaches fault "
                f"'{fault.name}' through the earthing network but has no "
                f"phase-conductor connection to the fault bus '{fault.bus}' at "
                f"{_render_frequencies(freqs)}, so it contributes no phase "
                f"current and induces no coupling. Its current is still injected "
                f"into the earthing system, as if it returned entirely through "
                f"soil and shields (reduction factor 1 for its share, no "
                f"coupling relief) -- conservative for its share, although "
                f"physically the source cannot feed this fault at all. Check "
                f"whether a branch that should "
                f"carry the phase conductor is inactive or declared open "
                f"(phase_impedance_formula 'nan').",
                code="SOURCE_WITHOUT_PHASE_PATH",
                elements=[("source", name), ("fault", fault.name)],
                stacklevel=5,
            )
        self._sources_without_phase_path = {}

    def _check_source_coverage(self, sources_with_path, fault: Fault) -> None:
        """
        Refuse a source that covers no solved frequency, announce a partial one.

        A source contributes at a frequency only if it carries a value there
        (``values`` for a current source, ``voltage`` for a Thevenin source).
        One whose keys match *none* of the network frequencies therefore
        contributed 0 A everywhere, and the run came back with 0 V at every
        bus -- a plausible-looking result rather than an error (USE3). Partial
        coverage is legitimate (a harmonic present only in some sources) but
        changes the result at the uncovered frequencies, so it is said once
        per source and run (decision F8).

        Only sources that matter for this fault are checked: they reach the
        fault and sit on an active bus.

        Parameters
        ----------
        sources_with_path : iterable of str
            Sources that reach the active fault.
        fault : Fault
            The active fault.

        Raises
        ------
        ValueError
            If such a source has a value at none of the network frequencies.
        """
        frequencies = [float(f) for f in self.network.frequencies]
        solved = set(frequencies)
        for name in sources_with_path:
            source = self.network.sources[name]
            if source.bus not in self.bus_indices:
                continue
            table = source.values if source.source_type == "current" else source.voltage
            keys = sorted(float(k) for k in (table or {}))
            key_set = set(keys)
            covered = [f for f in frequencies if f in key_set]
            if not covered:
                raise ValueError(
                    f"Source '{name}' at bus '{source.bus}' reaches fault "
                    f"'{fault.name}' but carries values only at "
                    f"{_render_frequencies(keys) if keys else 'no frequency at all'}"
                    f", none of the network frequencies "
                    f"({_render_frequencies(frequencies)}). It would contribute "
                    f"0 A at every solved frequency, and the run would report "
                    f"0 V at every bus as if that were a result. Give the source "
                    f"values at the network frequencies, or remove it."
                )
            missing = [f for f in frequencies if f not in key_set]
            if missing:
                emit(
                    InputWarning,
                    f"Source '{name}' at bus '{source.bus}' has no value at "
                    f"{_render_frequencies(missing)} of the network frequencies "
                    f"and contributes 0 A there (fault '{fault.name}'). Covered: "
                    f"{_render_frequencies([f for f in keys if f in solved])}.",
                    code="SOURCE_FREQUENCY_GAP",
                    elements=[("source", name)],
                    stacklevel=5,
                )

    def _warn_incomplete_scalings(self, fault: Fault) -> None:
        """
        Announce a scalings mapping that leaves out some network frequencies.

        A missing frequency is scaled with 1.0 -- full contribution, see
        :meth:`~groundinsight.models.core_models.Fault.scaling_at`. An empty
        mapping says exactly that for every frequency and stays silent; a
        mapping that names *some* frequencies but not others is more likely a
        forgotten entry (the documentation used to promise that a missing key
        meant zero), so it is said once per run (decision F7).

        Parameters
        ----------
        fault : Fault
            The active fault.
        """
        if not fault.scalings:
            return
        missing = [
            float(f) for f in self.network.frequencies
            if float(f) not in fault.scalings
        ]
        if not missing:
            return
        emit(
            InputWarning,
            f"Fault '{fault.name}' gives no scaling at "
            f"{_render_frequencies(missing)}; those frequencies are scaled with "
            f"1.0 (full contribution). Add the key with 0.0 to exclude a "
            f"frequency from this fault, or with 1.0 to make the choice explicit.",
            code="SCALINGS_INCOMPLETE",
            elements=[("fault", fault.name)],
            stacklevel=5,
        )

    def _warn_thevenin_coupling(self, sources_with_path, fault: Fault) -> None:
        """
        Announce a Thevenin source that drives coupled branches (PH1, F5).

        A voltage source's loop current is ``(E - (u_S - u_F)) / Z_src``, but
        the stationary solver uses the Norton current ``E / Z_src`` as the
        phase current that drives the shield coupling, as the fault current of
        ``value_current`` and as ``i_inj``. The EPR depends on the difference
        ``I - k * I_p`` of two nearly equal currents, so a few percent of
        current error become tens of percent of EPR -- too low, i.e.
        unconservative. Until the loop current is solved (planned), every run
        in which that can happen says so once.

        It can happen when a voltage source on an active bus other than the
        fault bus feeds the fault and at least one active branch with a
        grounding conductor has a non-zero mutual impedance at a solved
        frequency. A Thevenin source *on* the fault bus is exact (its loop
        current is ``E / Z_src``), and without coupling the phase current does
        not enter the earth side at all.

        Parameters
        ----------
        sources_with_path : iterable of str
            Sources that reach the active fault.
        fault : Fault
            The active fault.
        """
        voltage_sources = [
            name
            for name in sources_with_path
            if self.network.sources[name].source_type == "voltage"
            and self.network.sources[name].bus in self.bus_indices
            and self.network.sources[name].bus != fault.bus
        ]
        if not voltage_sources:
            return
        coupled: List[str] = []
        for branch in self.network.branches.values():
            if not branch.active or not branch.type.grounding_conductor:
                continue
            if branch.from_bus not in self.bus_indices:
                continue
            if branch.to_bus not in self.bus_indices:
                continue
            for freq in self.network.frequencies:
                z_mutual = (branch.mutual_impedance or {}).get(freq)
                z_self = self._resolved_impedance(
                    (branch.self_impedance or {}).get(freq), freq
                )
                if z_mutual is None or z_self is None or _is_open_circuit(z_self):
                    continue
                if complex(z_mutual.real, z_mutual.imag) != 0:
                    coupled.append(branch.name)
                    break
        if not coupled:
            return
        emit(
            ModelAssumptionWarning,
            f"Fault '{fault.name}': the Thevenin source(s) "
            f"{', '.join(repr(s) for s in voltage_sources)} feed the fault while "
            f"branches with mutual coupling carry phase current "
            f"({_shortlist(coupled)}). The stationary solver couples them with "
            f"the Norton current E/Z_src instead of the actual loop current and "
            f"uses that current as the fault current of value_current and as "
            f"i_inj (audit finding PH1, not yet corrected). Against a fully "
            f"coupled multi-conductor reference the EPR at the fault came out 6 "
            f"to 38 % too low on cable feeders with Z_src between 5+15j and "
            f"0.3+1j Ohm -- unconservative, and growing as Z_src falls. For a "
            f"stationary study, model the infeed as a current source carrying "
            f"the prospective fault current (gi.create_source).",
            code="THEVENIN_COUPLING",
            elements=[("source", name) for name in voltage_sources],
            stacklevel=5,
        )

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
            ``Fault.scaling_at(freq)`` for the active fault (1.0 for a
            frequency without a key).

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
                scaling = fault.scaling_at(freq)
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

        In the default ``phase_current_mode="auto"`` the answer comes from
        :meth:`groundinsight.models.core_models.Network.sources_reaching`,
        a breadth-first reachability test over the active sub-graph. That
        mode never reads ``Network.paths`` anywhere else, so enumerating
        every simple path just to ask "is there one?" was pure waste --
        exponential in the number of loops, 54-68 s on a 6x6 grid against
        milliseconds in the solver.

        In the legacy ``"paths"`` mode the enumerated paths carry the phase
        currents themselves, so the source list is derived from
        ``self.network.paths``, which must have been populated before the
        electrical network is solved (via ``network.define_paths()``).
        Both routes traverse the same active sub-graph and therefore
        return the same source list.

        Returns
        -------
        List[str]
            Source names that contribute to the active fault.
        """
        fault_name = self.network.active_fault
        if self.auto_phase_currents:
            return self.network.sources_reaching(fault_name)

        # Legacy mode: the enumerated paths are the data, not just an
        # existence proof. ``run_fault`` no longer builds them in the
        # default mode, so a caller that switches to ``"paths"`` may find
        # them missing or stale. Silently reading an empty path set would
        # inject nothing at all and report 0 V at every bus, so rebuild --
        # the same choice the transient state-space solver makes.
        if self.network._needs_path_rebuild():
            logger.warning(
                "phase_current_mode='paths' derives the branch phase currents "
                "from the enumerated source-to-fault paths, but the network's "
                "paths are missing or stale -- rebuilding them now. On a "
                "meshed network this enumeration is expensive; the default "
                "phase_current_mode='auto' does not need it."
            )
            self.network.invalidate_paths()
            self.network.define_paths()

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
            scaling = fault.scaling_at(freq)
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

    def _network_tables(self) -> _NetworkTables:
        """The model arrays of this assembly, resolved for the 0 Hz stand-in.

        Gathered on first use and kept for the lifetime of the
        :class:`ElectricalNetwork`, which -- like the matrices -- describes
        the network as it was when the object was built.

        Returns
        -------
        _NetworkTables
            The gathered tables.
        """
        if self._tables is None:
            self._tables = _NetworkTables(self)
            self._tables.resolve(self._dc_substitutes)
        return self._tables

    def _grounding_shunts(self, freq: float) -> Tuple[np.ndarray, np.ndarray]:
        """Grounding impedance of every active bus, and the admittance stamped.

        The interface :func:`~groundinsight.analysis.response.bus_response`
        uses to take one bus's electrode back out of the assembled matrix.
        Subtracting exactly the value that was added -- rather than a
        reciprocal recomputed with a different rounding -- leaves an exact
        zero where the electrode was a bus's only connection, so such a
        system is still recognised as singular.

        Parameters
        ----------
        freq : float
            A frequency of the network.

        Returns
        -------
        impedance : numpy.ndarray
            Resolved grounding impedance per bus in :attr:`bus_indices` order
            (0 Hz stand-in applied; ``nan`` where none is stored).
        admittance : numpy.ndarray
            The admittance on the diagonal of :attr:`Y_sparse` for that
            impedance; zero for a missing or open electrode.
        """
        tables = self._network_tables()
        column = tables.column[float(freq)]
        if self._bus_admittance is None:  # pragma: no cover -- set by the assembly
            usable = tables.bus_present & ~_open_mask(tables.bus_z)
            self._bus_admittance = _reciprocal(tables.bus_z, usable)
        return tables.bus_z[:, column], self._bus_admittance[:, column]

    def _phase_admittances(
        self, tables: _NetworkTables, column: int
    ) -> Tuple[np.ndarray, np.ndarray, np.ndarray]:
        """Phase-side admittance of every branch that exists in the solve.

        The rules of the scalar implementation, applied to whole arrays: a
        declared phase impedance at this frequency gives ``1/Z_phase`` (zero
        for an open conductor); without one the branch falls back to the
        documented proxy, ``1/Z_self`` when it has an intact grounding
        conductor with a value at this frequency and ``1/length`` otherwise.
        A broken shield (infinite ``Z_self``) takes the length proxy as well:
        it opens the shield, not the phase conductor next to it (PH3).

        Parameters
        ----------
        tables : _NetworkTables
            The gathered model arrays.
        column : int
            Frequency column.

        Returns
        -------
        admittance : numpy.ndarray
            Complex admittance per branch of ``tables.branch_in_net``, in
            branch order.
        proxied : numpy.ndarray
            Boolean mask over the same branches: ``True`` where the proxy was
            used.
        broken : numpy.ndarray
            Boolean mask over the same branches: proxied branches with a
            grounding conductor whose ``Z_self`` is infinite at this frequency
            (they took the length proxy).

        Raises
        ------
        ZeroDivisionError
            If a declared phase impedance is exactly zero. The scalar
            implementation inverted it with Python's complex division and
            raised; that behaviour is kept rather than silently turned into an
            infinite admittance.
        """
        rows = np.flatnonzero(tables.branch_in_net)
        z_phase = tables.phase_z[rows, column]
        declared = tables.phase_declared[rows] & tables.phase_present[rows, column]
        admittance = np.zeros(rows.size, dtype=complex)

        invert = declared & ~_open_mask(z_phase)
        zero = invert & (z_phase.real == 0.0) & (z_phase.imag == 0.0)
        if zero.any():
            # Same exception, same message as the scalar ``1.0 / Z_phase``.
            1.0 / complex(z_phase[zero][0])
        admittance[invert] = _reciprocal(z_phase[invert], np.ones(invert.sum(), bool))

        proxied = ~declared
        z_self = tables.self_z[rows, column]
        with_conductor = (
            tables.branch_grounding[rows] & tables.self_present[rows, column]
        )
        shield_open = _open_mask(z_self)
        invert = proxied & with_conductor & ~shield_open
        admittance[invert] = _reciprocal(z_self[invert], np.ones(invert.sum(), bool))
        broken = proxied & with_conductor & shield_open
        by_length = proxied & ~invert
        admittance[by_length] = 1.0 / tables.proxy_length[rows][by_length]
        return admittance, proxied, broken

    def _compute_phase_currents_auto(
        self, freq: float, sources_with_path
    ) -> Dict[str, complex]:
        """
        Variant B: solve a reduced phase-only network to split source currents over parallel paths.

        The phase-side admittance of each branch is the declared phase
        impedance, or the documented proxy ``1 / Z_self`` when a grounding
        conductor is present and ``1 / length`` otherwise (see
        :meth:`_phase_admittances`). The fault bus is taken as the reference
        (``u_fault = 0``) and each source injects ``scaling * source.values[freq]``
        at its bus. Branch phase currents are then ``(u_from - u_to) * y_phase``
        per branch. Contributions from all sources are superposed.

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

        Raises
        ------
        RuntimeError
            If the phase-conductor network around the fault bus is singular.

        Notes
        -----
        **One factorisation, all sources at once** (audit pass 19, P1). The
        reduced phase network is a complex Laplacian with the fault bus as
        reference: sparse, with about ``2m + n`` entries, and the *same*
        matrix for every source. It used to be assembled as a dense ``n x n``
        array and solved with :func:`numpy.linalg.solve` once per source and
        frequency, which made it the dominant cost of a run and the one that
        grew fastest with the network: measured on a 1000-bus ring at seven
        frequencies it took 1.19 s of 2.24 s, on a 1000-bus radial feeder with
        three sources 2.95 s of 3.92 s (now 0.035 s and 0.050 s). It is now assembled
        straight into CSC, factorised once per frequency with
        :func:`scipy.sparse.linalg.splu` and solved for a right-hand side with
        one column per source; the branch currents are one vectorised gather.
        Superposition is kept in the original order (sources in turn), and
        the stamps are accumulated in the original program order, so the
        reduced matrix differs from the one the dense loop built only by the
        last-bit rounding of NumPy's complex division.

        The galvanic component of the fault bus is found with
        :func:`scipy.sparse.csgraph.connected_components` instead of a Python
        depth-first search. Everything observable is unchanged: the proxy
        warning, the record of a source without phase-conductor connection
        (reported once per run, PH9), the :class:`RuntimeError` for a
        singular phase network, and :attr:`phase_currents` as a dictionary
        over all branches. A phase network with a non-finite admittance is
        solved densely as before, so a NaN propagates instead of being
        reported as a singular factor.
        """
        fault_name = self.network.active_fault
        fault = self.network.faults[fault_name]
        fault_bus_idx = self.bus_indices[fault.bus]
        n = self.num_buses
        tables = self._network_tables()
        column = tables.column[float(freq)]

        rows = np.flatnonzero(tables.branch_in_net)
        y, proxied_mask, broken_mask = self._phase_admittances(tables, column)
        from_idx = tables.branch_from[rows]
        to_idx = tables.branch_to[rows]
        names = tables.branch_names

        # Restrict the solve to the galvanic component of the phase network that
        # contains the fault bus. Pinning the fault bus to zero over the *whole*
        # index range leaves any other island without a reference node, which
        # makes the reduced matrix singular -- previously that raised
        # LinAlgError and the entire source contribution was dropped in silence.
        connected = y != 0
        graph = csr_matrix(
            (
                np.ones(int(connected.sum())),
                (from_idx[connected], to_idx[connected]),
            ),
            shape=(n, n),
        )
        _, labels = connected_components(graph, directed=False)
        in_component = labels == labels[fault_bus_idx]

        # The proxy only ever influences the result where there is something to
        # split: in a network without cycles every branch carries the full
        # source current regardless of its impedance. Warning about the proxy on
        # a radial network would be noise, so the warning is scoped to the
        # meshed case and emitted once per solve rather than once per frequency.
        # (The method returns at once when it has warned or nothing is proxied,
        # so building its arguments is skipped in exactly those cases.)
        if proxied_mask.any() and not self._phase_proxy_warned:
            self._warn_phase_impedance_proxy(
                [names[b] for b in rows[proxied_mask]],
                dict(zip((names[b] for b in rows), y.tolist())),
                set(np.flatnonzero(in_component).tolist()),
                [names[b] for b in rows[broken_mask]],
            )

        keep_mask = in_component.copy()
        keep_mask[fault_bus_idx] = False
        keep = np.flatnonzero(keep_mask)
        position = np.full(n, -1, dtype=np.int64)
        position[keep] = np.arange(keep.size)

        scaling = fault.scaling_at(freq)
        columns: List[Tuple[int, complex]] = []
        factor = None

        # An empty ``keep`` means the fault bus is alone in the phase network.
        # That is not an early exit: every source then fails the membership test
        # below and gets told why, instead of the function returning zeros.
        for source_name in sources_with_path:
            source = self.network.sources[source_name]
            src_idx = self.bus_indices.get(source.bus)
            if src_idx is None or src_idx == fault_bus_idx:
                continue
            I = self._source_injection(source, freq, scaling)
            if I is None:
                continue

            if position[src_idx] < 0:
                # The source bus is not galvanically connected to the fault bus
                # through the phase network, so no phase current can flow between
                # them. That is a legitimate result -- but it is also the exact
                # situation that used to be swallowed, so it is named out loud,
                # once per source and run (PH9, see _construct_vectors).
                self._sources_without_phase_path.setdefault(source_name, []).append(
                    freq
                )
                continue

            if factor is None:
                # Factorised when the first source needs it -- the point where
                # the per-source dense solve used to raise for a singular network.
                factor = self._factorise_phase_network(
                    position, from_idx, to_idx, y, keep.size, fault.bus, freq
                )
            columns.append((int(position[src_idx]), I))

        phase_currents: Dict[str, complex] = {
            name: 0.0 + 0.0j for name in self.network.branches
        }
        if not columns:
            return phase_currents

        rhs = np.zeros((keep.size, len(columns)), dtype=complex)
        for col, (pos, current) in enumerate(columns):
            rhs[pos, col] = current
        reduced = factor(rhs)
        potentials = np.zeros((n, len(columns)), dtype=complex)
        potentials[keep] = reduced
        # u_full[fault_bus_idx] = 0 by construction

        # Superposition source by source, as the per-source loop added it.
        total = np.zeros(rows.size, dtype=complex)
        for col in range(len(columns)):
            total += (potentials[from_idx, col] - potentials[to_idx, col]) * y
        for name, value in zip((names[b] for b in rows), total.tolist()):
            phase_currents[name] = value
        return phase_currents

    def _factorise_phase_network(
        self,
        position: np.ndarray,
        from_idx: np.ndarray,
        to_idx: np.ndarray,
        y: np.ndarray,
        size: int,
        fault_bus: str,
        freq: float,
    ):
        """Assemble the reduced phase network and return a solver for it.

        The stamps of every branch -- ``+y`` on both diagonals, ``-y`` on the
        two off-diagonals -- are accumulated in branch order and mapped
        through ``position``; rows and columns of buses outside the reduced
        system (the fault bus, other islands) are dropped -- the sparse
        counterpart of ``Y_phase[np.ix_(keep, keep)]`` of the former dense
        assembly, with the same additions in the same order.

        Parameters
        ----------
        position : numpy.ndarray
            Row of each bus in the reduced system, ``-1`` if it is not part of
            it.
        from_idx, to_idx : numpy.ndarray
            Bus indices of the branch ends.
        y : numpy.ndarray
            Phase admittance per branch.
        size : int
            Number of rows of the reduced system.
        fault_bus : str
            Name of the fault bus, for the error message.
        freq : float
            Frequency, for the error message.

        Returns
        -------
        callable
            ``solve(rhs) -> potentials`` for a ``(size,)`` or ``(size, k)``
            right-hand side.

        Raises
        ------
        RuntimeError
            If the reduced system is singular.
        """
        pf = position[from_idx]
        pt = position[to_idx]
        stamp_rows = np.stack([pf, pt, pf, pt], axis=1).ravel()
        stamp_cols = np.stack([pf, pt, pt, pf], axis=1).ravel()
        values = np.stack([y, y, -y, -y], axis=1).ravel()
        keep = (stamp_rows >= 0) & (stamp_cols >= 0)
        matrix = _assemble_csc(
            stamp_rows[keep], stamp_cols[keep], values[keep], size
        )
        message = (
            f"The phase-conductor network around fault bus "
            f"'{fault_bus}' is singular at {freq} Hz even after "
            f"restricting the solve to the galvanic component of the "
            f"fault bus ({size + 1} buses). This should not happen "
            f"for a passive connected network; it usually means a branch "
            f"carries a zero or non-finite phase impedance. Check the "
            f"'phase_impedance_formula' of the branch types involved, or "
            f"switch to phase_current_mode='paths' and set "
            f"Branch.parallel_coefficient by hand."
        )
        if not np.all(np.isfinite(matrix.data)):
            # A NaN or infinite admittance: SuperLU would call that "exactly
            # singular", LAPACK propagates it. Keep the historic behaviour.
            dense = matrix.toarray()

            def solve_dense(rhs):
                try:
                    return np.linalg.solve(dense, rhs)
                except np.linalg.LinAlgError as exc:
                    raise RuntimeError(message) from exc

            # The dense solve raised per source, i.e. at the first solve.
            solve_dense(np.zeros((size, 1), dtype=complex))
            return solve_dense
        try:
            lu = splu(matrix)
        except RuntimeError as exc:
            raise RuntimeError(message) from exc
        return lu.solve

    def _warn_phase_impedance_proxy(
        self,
        proxied: List[str],
        branch_y_phase: Dict[str, complex],
        component: set,
        broken_shields: Optional[List[str]] = None,
    ) -> None:
        """
        Warn once per solve when a *meshed* network relies on the phase proxy.

        In a network without cycles the phase-current distribution is fixed by
        the topology alone -- every branch on the single route carries the full
        source current whatever its impedance -- so the proxy cannot change the
        result and saying so would only add noise. As soon as the fault bus sits
        in a component that carries a cycle, the split *is* decided by the
        impedance values, and a proxy there is a modelling assumption the user
        needs to know about. It is emitted as a ``ModelAssumptionWarning`` with
        code ``PHASE_PROXY`` and the proxied branches of that component as
        elements, so a program can find it in ``Result.diagnostics``.

        The cycle test counts every branch with a non-zero phase admittance.
        Since the proxy no longer opens the phase conductor of a broken shield
        (PH3), only a *declared* open phase conductor (``phase_impedance_formula
        = "nan"``) has zero admittance -- and that one genuinely breaks the
        cycle.

        Parameters
        ----------
        proxied : List[str]
            Branch names whose type declares no ``phase_impedance_formula``.
        branch_y_phase : Dict[str, complex]
            Phase admittance per branch name for the current frequency.
        component : set
            Bus indices in the galvanic component of the fault bus.
        broken_shields : List[str], optional
            Proxied branches with a grounding conductor whose ``Z_self`` is
            infinite, which fell back to the length proxy.
        """
        if self._phase_proxy_warned or not proxied:
            return

        connecting = [
            name
            for name, y in branch_y_phase.items()
            if y != 0
            and self.bus_indices[self.network.branches[name].from_bus] in component
            and self.bus_indices[self.network.branches[name].to_bus] in component
        ]
        # A connected component is a tree exactly when it has one fewer edge
        # than it has nodes; anything above that means at least one cycle.
        if len(connecting) < len(component):
            return

        relevant = sorted(set(proxied) & set(connecting))
        if not relevant:
            return

        self._phase_proxy_warned = True
        broken = sorted(set(broken_shields or ()) & set(relevant))
        broken_note = (
            f" The shield of {', '.join(broken)} is broken (self impedance "
            f"'nan'); its phase conductor is kept in the phase network with the "
            f"length proxy, since a broken shield does not open the phase "
            f"conductor next to it."
            if broken
            else ""
        )
        fault_name = self.network.active_fault
        emit(
            ModelAssumptionWarning,
            f"The phase network around fault '{fault_name}' carries at least one "
            f"cycle, so the source current genuinely divides between routes -- "
            f"and {len(relevant)} of the branches in it declare no "
            f"'phase_impedance_formula': {', '.join(relevant)}. Their phase "
            f"admittance falls back to 1/Z_self for a branch with an intact "
            f"grounding conductor and to 1/length (purely real) for one without, "
            f"two quantities that are not comparable. The split between routes "
            f"of different construction is therefore a heuristic, not a physical "
            f"result.{broken_note} Set BranchType.phase_impedance_formula to make "
            f"it physical.",
            code="PHASE_PROXY",
            elements=[("branch", name) for name in relevant]
            + [("fault", str(fault_name))],
            stacklevel=5,
        )

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

        Notes
        -----
        Evaluated on the gathered arrays (finding P6): the coupled branches --
        active, grounding conductor, both ends active, self and mutual
        impedance stored at this frequency, shield not open -- are selected
        with one mask, and the injections are added with
        :func:`numpy.add.at` in branch order, which performs the same
        additions in the same order as the former loop.
        """
        tables = self._network_tables()
        column = tables.column[float(freq)]
        z_self = tables.self_z[:, column]
        coupled = np.flatnonzero(
            tables.branch_in_net
            & tables.branch_grounding
            & tables.self_present[:, column]
            & tables.mutual_present[:, column]
            & ~_open_mask(z_self)
        )
        if coupled.size == 0:
            return
        names = tables.branch_names
        i_phase = np.fromiter(
            (phase_currents.get(names[b], 0.0 + 0.0j) for b in coupled),
            dtype=complex,
            count=coupled.size,
        )
        with np.errstate(over="ignore", invalid="ignore", divide="ignore"):
            i_mut = i_phase * (tables.mutual[coupled, column] / z_self[coupled])

        # Norton current flows from_bus -> to_bus (same direction as I_p)
        ends = np.stack(
            [tables.branch_from[coupled], tables.branch_to[coupled]], axis=1
        ).ravel()
        np.add.at(i_vector, ends, np.stack([-i_mut, i_mut], axis=1).ravel())

        # Stored with flipped sign so that compute_branch_currents keeps using
        # the formula current = (u_to - u_from) * Y_self + i_mutual_stored.
        stored = self.i_mutuals[freq]
        values = (-i_mut).tolist()
        if not stored:
            stored.update(zip((names[b] for b in coupled), values))
        else:
            for b, value in zip(coupled, values):
                name = names[b]
                if name in stored:
                    stored[name] += value
                else:
                    stored[name] = value

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
        Y_matrix : numpy.ndarray or scipy.sparse matrix
            The nodal admittance matrix at ``freq``. A sparse matrix is checked
            on its stored entries, without densifying it.
        i_vector : numpy.ndarray
            The injection vector at ``freq``.

        Raises
        ------
        ValueError
            If ``Y_matrix`` or ``i_vector`` contains NaN.
        """
        if issparse(Y_matrix):
            entries = Y_matrix.tocoo()
            nan_rows = entries.row[np.isnan(entries.data)]
        else:
            nan_rows = np.argwhere(np.isnan(Y_matrix))[:, 0]
        if not (nan_rows.size or np.isnan(i_vector).any()):
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
            {int(row) for row in nan_rows}
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
        # Drop factorisations of a previous solve: the Y matrices may have
        # been rebuilt since (a changed rho, a flipped active flag), and a
        # stale factor would silently answer with the old topology.
        self._lu_factors = {}
        tables = self._network_tables()
        for freq in self.network.frequencies:
            Y_matrix = self.Y_sparse[freq]
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
            # here, before the solve, with no numerical tolerance. The test runs
            # on the stored values as a whole; the per-bus classification is
            # only needed for the message.
            stored = tables.bus_z_raw[:, tables.column[float(freq)]]
            present = tables.bus_present[:, tables.column[float(freq)]]
            if not np.any(present & np.isfinite(stored) & (stored != 0)):
                grouped = self._classify_bus_grounding(freq)
                raise ValueError(self._no_ground_reference_message(freq, grouped))
            try:
                # Backstops for any remaining singular case (e.g. a disconnected
                # ungrounded island): scipy may raise, or return a non-finite
                # solution. The matrix is assembled as CSC, so it is factorised
                # as it is -- no dense-to-sparse scan.
                lu = splu(Y_matrix)
                u_vector = lu.solve(i_vector)
            except (np.linalg.LinAlgError, RuntimeError) as e:
                raise ValueError(f"{singular_msg} Original solver error: {e}") from e
            if not np.all(np.isfinite(u_vector)):
                raise ValueError(singular_msg)
            self.u_vectors[freq] = u_vector
            # Keep the factorisation: ``compute_reduction_factors`` solves the
            # *same* Y with a second right-hand side (the injection without the
            # mutual Norton terms). Re-using the factor turns that second solve
            # into a triangular back-substitution.
            self._lu_factors[freq] = lu

        # Create ResultBus instances. Inactive buses are not in ``bus_indices``
        # and therefore do not appear in the result -- they are physically
        # disconnected from the nodal system. The electrode current is
        # ``u / Z`` with the 0 Hz stand-in applied, zero for a missing or open
        # electrode; the injection is the source-only vector.
        frequencies = self.network.frequencies
        potentials = self._stacked(self.u_vectors)
        usable = tables.bus_present & ~_open_mask(tables.bus_z)
        electrode = np.zeros(potentials.shape, dtype=complex)
        with np.errstate(over="ignore", invalid="ignore", divide="ignore"):
            np.divide(potentials, tables.bus_z, out=electrode, where=usable)
        injection = self._stacked(self.source_injections)
        result.buses.extend(
            self._result_rows(
                ResultBus,
                list(self.bus_indices),
                frequencies,
                {"uepr": potentials, "ia": electrode, "i_inj": injection},
            )
        )

        # Store the result in the network's results dictionary
        self.network.results[fault_name] = result
        self.results = result  # Also keep a reference in self.results

    def _stacked(self, vectors: Dict[float, np.ndarray]) -> np.ndarray:
        """Per-frequency bus vectors as one ``(n, F)`` array (zeros if absent).

        Parameters
        ----------
        vectors : dict of float to numpy.ndarray
            E.g. :attr:`u_vectors` or :attr:`source_injections`.

        Returns
        -------
        numpy.ndarray
            Column ``j`` is the vector of ``network.frequencies[j]``.
        """
        frequencies = self.network.frequencies
        out = np.zeros((self.num_buses, len(frequencies)), dtype=complex)
        for j, freq in enumerate(frequencies):
            vector = vectors.get(freq)
            if vector is not None:
                out[:, j] = vector
        return out

    def _result_rows(
        self,
        model,
        names: List[str],
        frequencies: List[float],
        columns: Dict[str, np.ndarray],
    ) -> list:
        """Build one result model per element from ``(element, F)`` arrays.

        Every field ``key`` of ``columns`` becomes ``key`` (the RMS over all
        frequencies, see :meth:`_calculate_rms`) and ``key + "_freq"`` (one
        :class:`ComplexNumber` per frequency) on the model.

        Parameters
        ----------
        model : type
            :class:`ResultBus` or :class:`ResultBranch`.
        names : list of str
            Element names, one per row.
        frequencies : list of float
            Frequencies, one per column.
        columns : dict of str to numpy.ndarray
            Field name to complex array of shape ``(len(names), F)``.

        Returns
        -------
        list
            The result models, in row order.

        Notes
        -----
        The values are taken out of the arrays with ``tolist()`` once rather
        than indexed per element and frequency. The RMS is accumulated
        frequency by frequency as ``sqrt(sum |x|**2)``, the convention of
        :meth:`_calculate_rms`. The objects are built with the cyclic garbage
        collector paused (:func:`_gc_paused`, finding P5a).
        """
        prepared = []
        for key, values in columns.items():
            square = np.zeros(values.shape[0])
            for j in range(values.shape[1]):
                magnitude = np.abs(values[:, j])
                square += magnitude * magnitude
            prepared.append(
                (key, np.sqrt(square).tolist(), values.real.tolist(),
                 values.imag.tolist())
            )
        rows = []
        indexed = list(enumerate(frequencies))
        with _gc_paused():
            for k, name in enumerate(names):
                fields = {"name": name}
                for key, rms, real, imag in prepared:
                    re_k, im_k = real[k], imag[k]
                    fields[key] = rms[k]
                    fields[key + "_freq"] = {
                        freq: ComplexNumber(real=re_k[j], imag=im_k[j])
                        for j, freq in indexed
                    }
                rows.append(model(**fields))
        return rows

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
        tables = self._network_tables()
        frequencies = self.network.frequencies

        # Inactive branches and branches with at least one inactive endpoint
        # are open-circuited: their shield current is zero by construction, as
        # is that of a branch without grounding conductor and of a frequency
        # whose self impedance is missing or open. Everything else carries
        # (u_to - u_from) * Y_self plus the stored mutual term, whose sign
        # already combines with that difference (see _add_mutual_currents).
        conducting = (
            (tables.branch_in_net & tables.branch_grounding)[:, None]
            & tables.self_present
            & ~_open_mask(tables.self_z)
        )
        admittance = _reciprocal(tables.self_z, conducting)
        potentials = self._stacked(self.u_vectors)
        from_idx = np.where(tables.branch_from >= 0, tables.branch_from, 0)
        to_idx = np.where(tables.branch_to >= 0, tables.branch_to, 0)
        names = tables.branch_names
        mutual = np.zeros(admittance.shape, dtype=complex)
        for j, freq in enumerate(frequencies):
            stored = self.i_mutuals.get(freq, {})
            if stored:
                mutual[:, j] = np.fromiter(
                    (stored.get(name, 0) for name in names), dtype=complex,
                    count=len(names),
                )
        current = np.zeros(admittance.shape, dtype=complex)
        with np.errstate(over="ignore", invalid="ignore"):
            delta = potentials[to_idx] - potentials[from_idx]
            current[conducting] = (delta * admittance + mutual)[conducting]
        result.branches.extend(
            self._result_rows(ResultBranch, names, frequencies, {"i_s": current})
        )

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

        Notes
        -----
        **RMS convention.** Every phasor in this package -- from
        :attr:`groundinsight.models.core_models.Source.values` through the
        solution vectors to these result columns -- is an **RMS amplitude**,
        not a peak amplitude. Under that convention the total RMS over all
        frequency components is

        .. math::

            X_{\\mathrm{RMS}} = \\sqrt{\\sum_f |X(f)|^2}

        which is exactly what this method computes, with the 0 Hz term
        entering as ``|X_dc|``: a DC component contributes its own
        magnitude, an AC component its RMS amplitude, and the two add in
        quadrature. Peak-valued phasors would make every AC term too large
        by ``sqrt(2)`` while leaving the DC term right, so the two
        conventions must not be mixed in one model.

        A ``None`` entry -- a frequency at which the quantity is not
        defined, e.g. no electrode current at an open end -- contributes
        nothing rather than propagating NaN through the whole column.
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

        Notes
        -----
        The no-mutual system uses the *same* ``Y(f)`` that
        :meth:`solve_network` has already factorised, only with a different
        right-hand side. The sparse LU factors kept in ``self._lu_factors``
        are therefore reused and the second solve costs one triangular
        back-substitution. The earlier ``numpy.linalg.solve`` refactorised
        the matrix densely -- 2.5 s of a 6.9 s profile on a 200-bus chain
        over 50 frequencies -- and did so with a *different* singularity
        treatment than the first solve, so the two paths could disagree
        about whether the same matrix was solvable. Frequencies without a
        stored factor (``compute_reduction_factors`` called on its own)
        factorise :attr:`Y_sparse` afresh with the same sparse LU.
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
            i_vector = self.i_vectors_no_mutual[freq]
            lu = self._lu_factors.get(freq)
            try:
                # Solve for u_vector without mutual currents. The LU factors
                # of Y(f) come from ``solve_network``; only a caller that
                # skipped it has to pay for a fresh factorisation.
                if lu is None:
                    lu = splu(self.Y_sparse[freq])
                u_vector = lu.solve(i_vector)
                self.u_vectors_no_mutual[freq] = u_vector

            except (np.linalg.LinAlgError, RuntimeError) as e:
                logger.error(
                    "Error solving network equations at frequency %s without mutual currents: %s",
                    freq,
                    e,
                    exc_info=True,
                )
                continue

        # Store uepr without mutual currents. A frequency whose no-mutual solve
        # failed above is absent from ``u_vectors_no_mutual``; it is skipped here
        # and reported as ``None`` rather than raising a bare KeyError out of the
        # solver core.
        for freq in frequencies:
            if freq not in self.u_vectors_no_mutual:
                continue
            voltage = self.u_vectors_no_mutual[freq][fault_bus_idx]
            uepr_without_mutual[freq] = voltage

        # Z_E of EN 50522 (F4): the driving-point impedance of the earthing
        # network at the fault bus. Computed first, because it is also the
        # yardstick of the noise rule for the EPR-based factor below.
        z_driving_point, z_isolated = self._earthing_driving_points(fault_bus_idx)

        # Step 4: Compute reduction factors
        epr_undefined: List[Tuple[float, float, float]] = []
        for freq in frequencies:
            if freq not in uepr_without_mutual:
                reduction_factors[freq] = None
                continue
            v_with = uepr_with_mutual[freq]
            v_without = uepr_without_mutual[freq]
            # Compute magnitudes
            mag_with = abs(v_with)
            mag_without = abs(v_without)

            # The quotient means something only while its denominator stands
            # above rounding noise (P9). A fault bus that is at zero potential
            # in exact arithmetic -- every source on the fault bus, or a source
            # behind a dead end that returns all its current through one
            # shield -- comes out at ~1e-14 V, and the ratio of two such
            # numbers was reported as a reduction factor (0.449 =
            # 5.1e-14 V / 1.1e-13 V for a source behind a dead end, next to
            # 320 V at the source tower). The yardstick is the EPR the fault
            # current would raise the fault bus to on its own,
            # |I_F| * |Z_FF|: an exact zero sits at ~1e-16 of it, while a
            # near-ideal fault-bus electrode -- a small but accurate EPR --
            # stays at the order of |1 - k| of it.
            # (The largest EPR of the solution is no yardstick: it calls the
            # near-ideal electrode noise.)
            i_fault = complex(self.total_source_currents.get(freq) or 0)
            reference = 0.0
            if i_fault != 0:
                z_ff = z_driving_point.get(freq)
                if z_ff is None:
                    z_ff = self._solved_driving_point(freq, fault_bus_idx)
                reference = abs(i_fault) * (abs(z_ff) if z_ff is not None else 0.0)
            if mag_without > NOISE_REL * reference:
                reduction_factor = mag_with / mag_without
            else:
                reduction_factor = None
                # Said out loud only where the earthing network is excited at
                # all. Every source on the fault bus injects nothing anywhere:
                # that null case has no earth current and no factor by
                # definition, as it always had, and a study that scans every
                # bus as a fault location would otherwise repeat it per bus.
                if i_fault != 0 and self._is_excited(freq):
                    epr_undefined.append((freq, mag_without, reference))
            reduction_factors[freq] = reduction_factor

        # Step 5: the current-based reduction factor -- see the method docstring.
        (
            reduction_factors_current,
            earth_currents,
            earth_buses,
            earthing_voltages,
            earthing_impedances,
            group_undefined,
        ) = self._compute_current_reduction_factors()

        self._report_undefined_reduction_quantities(
            fault_name, fault_bus, epr_undefined, group_undefined, z_isolated
        )

        # The two definitions answer different questions and only coincide in
        # the limit of ideally bonded stations. Saying so where they part
        # company beats leaving a factor of forty to be discovered later.
        self._warn_on_reduction_factor_divergence(
            reduction_factors, reduction_factors_current
        )

        # The EN 50522 quantities (F4): U_E is the EPR at the fault bus -- the
        # very number ResultBus reports there -- and Z_E the driving-point
        # impedance of the earthing network at the fault bus. The group mean
        # and its quotient by I_E, which carried these names before, stay
        # available as diagnostics.
        fault_currents = {
            freq: (
                None
                if self.total_source_currents.get(freq) is None
                else complex(self.total_source_currents[freq])
            )
            for freq in frequencies
        }
        fault_potentials = {
            freq: complex(self.u_vectors[freq][fault_bus_idx]) for freq in frequencies
        }

        # Store the reduction factors in the result
        result = self.network.results[fault_name]
        result_reduction_factor = ResultReductionFactor(
            fault_bus=fault_bus,
            value=reduction_factors,
            value_current=reduction_factors_current,
            i_fault=fault_currents,
            i_earth=earth_currents,
            earth_buses=earth_buses,
            u_earthing=fault_potentials,
            z_earthing=z_driving_point,
            u_earthing_group=earthing_voltages,
            z_earthing_group=earthing_impedances,
        )
        result.reduction_factor = result_reduction_factor

        # Update the result in the network's results dictionary
        self.network.results[fault_name] = result
        self.results = result  # Update self.results

    def _is_excited(self, freq: float) -> bool:
        """
        ``True`` if anything is injected into the earthing network at ``freq``.

        Every source on the fault bus puts ``+I`` and ``-I`` on the same row
        and no phase current anywhere, so the injection vector is exactly
        zero: the null case, in which there is no earth current and no
        reduction factor by definition.

        Parameters
        ----------
        freq : float
            Frequency in Hz.

        Returns
        -------
        bool
            ``False`` for an all-zero (or missing) injection vector.
        """
        i_vector = self.i_vectors.get(freq)
        return i_vector is not None and bool(np.any(i_vector != 0))

    def _solved_driving_point(self, freq: float, bus_idx: int) -> Optional[complex]:
        """
        ``(Y^-1)_bb`` of the system that was solved at ``freq``.

        The voltage one ampere injected at the bus raises the bus itself to,
        with every element of the solved matrix in place. Costs one
        triangular solve against the factorisation ``solve_network`` kept;
        a frequency without a stored factor factorises ``Y_sparse`` on its own.

        Parameters
        ----------
        freq : float
            Frequency of the solved system, in Hz.
        bus_idx : int
            Row of the bus in the nodal system.

        Returns
        -------
        complex or None
            ``None`` if the solve did not give a finite value.
        """
        unit = np.zeros(self.num_buses, dtype=complex)
        unit[bus_idx] = 1.0
        lu = self._lu_factors.get(freq)
        try:
            if lu is None:
                lu = splu(self.Y_sparse[freq])
            column = lu.solve(unit)
        except (np.linalg.LinAlgError, RuntimeError):
            return None
        value = complex(column[bus_idx])
        if not (math.isfinite(value.real) and math.isfinite(value.imag)):
            return None
        return value

    def _earthing_driving_points(
        self, bus_idx: int
    ) -> Tuple[Dict[float, Optional[complex]], List[float]]:
        """
        Driving-point impedance of the earthing network at one bus, per frequency.

        ``Z_dp = (Y_E^-1)_bb`` with ``Y_E`` the earthing network alone: every
        electrode and shield as assembled (0 Hz substitutes included), no
        coupling, no Thevenin loop closure. Without a voltage source ``Y_E``
        *is* the solved matrix and the value costs one triangular solve on
        the factorisation ``solve_network`` kept; with one, the sparse ``Y_E``
        (:meth:`_earthing_admittance_sparse`) is factorised on its own.

        Parameters
        ----------
        bus_idx : int
            Row of the bus (the fault bus) in the nodal system.

        Returns
        -------
        tuple of (dict of float to complex or None, list of float)
            The impedance per frequency, and the frequencies at which the bus
            has no path to remote earth through the earthing network alone
            (only through a Thevenin loop) -- ``None`` there.
        """
        values: Dict[float, Optional[complex]] = {}
        isolated: List[float] = []
        for freq in self.network.frequencies:
            if freq not in self.Y_sparse:
                values[freq] = None
                continue
            if not self.thevenin_loop_admittances(freq):
                values[freq] = self._solved_driving_point(freq, bus_idx)
                continue
            unit = np.zeros(self.num_buses, dtype=complex)
            unit[bus_idx] = 1.0
            try:
                lu = splu(self._earthing_admittance_sparse(freq))
                value = complex(lu.solve(unit)[bus_idx])
            except (np.linalg.LinAlgError, RuntimeError):
                value = None
            if value is None or not (
                math.isfinite(value.real) and math.isfinite(value.imag)
            ):
                values[freq] = None
                isolated.append(freq)
                continue
            values[freq] = value
        return values, isolated

    def _report_undefined_reduction_quantities(
        self,
        fault_name: str,
        fault_bus: str,
        epr_undefined: List[Tuple[float, float, float]],
        group_undefined: List[Tuple[float, float, float]],
        z_isolated: Optional[List[float]] = None,
    ) -> None:
        """
        Say once per run where a reduction quantity was reported as ``None``.

        The first two conditions are a vanishing reference (audit finding
        P9): the quotient or the group would otherwise be built from rounding
        noise. The third is a fault bus that reaches remote earth only through
        the loop closure of a voltage source, so the earthing network alone
        presents an infinite impedance there (F4). Collected over all
        frequencies so that a sweep over fifty harmonics produces one message
        per condition, not fifty.

        Parameters
        ----------
        fault_name : str
            The active fault.
        fault_bus : str
            Its bus.
        epr_undefined : list of tuple of float
            ``(frequency, |u_F,0|, |I_F| * |Z_FF|)`` where the EPR-based factor
            is undefined.
        group_undefined : list of tuple of float
            ``(frequency, max|I_a|, |I_F|)`` where no earth-return group exists.
        z_isolated : list of float, optional
            Frequencies at which ``Z_E`` is undefined.
        """
        elements = [("fault", fault_name), ("bus", fault_bus)]
        if z_isolated:
            emit(
                ModelAssumptionWarning,
                f"Fault '{fault_name}': at "
                f"{_render_frequencies(z_isolated)} the fault bus "
                f"'{fault_bus}' reaches remote earth only through the loop "
                f"closure of a voltage source, not through any electrode or "
                f"shield. The earthing network alone presents an infinite "
                f"impedance there, so the earthing impedance Z_E (the "
                f"driving-point impedance of the earthing network at the fault "
                f"bus) is reported as None.",
                code="EARTHING_IMPEDANCE_UNDEFINED",
                elements=elements,
                stacklevel=4,
            )
        if epr_undefined:
            emit(
                ModelAssumptionWarning,
                f"Fault '{fault_name}': the EPR-based reduction factor "
                f"(ResultReductionFactor.value) is undefined at "
                f"{_render_points(epr_undefined, 'V')} -- the EPR of the fault "
                f"bus '{fault_bus}' in the solution without coupling is at "
                f"rounding-noise level against the EPR the fault current would "
                f"raise it to on its own, |I_F| * |Z_FF| (ratio below "
                f"{NOISE_REL:g}). The fault current returns to its source "
                f"without raising the fault bus at all, e.g. because the source "
                f"sits behind a dead end without electrode or on the fault bus "
                f"itself, so |u_F| / |u_F without coupling| would be a ratio of "
                f"two rounding errors. Reported as None.",
                code="REDUCTION_FACTOR_UNDEFINED",
                elements=elements,
                stacklevel=4,
            )
        if group_undefined:
            emit(
                ModelAssumptionWarning,
                f"Fault '{fault_name}': no earth-return group exists at "
                f"{_render_points(group_undefined, 'A')} -- no electrode carries "
                f"more than {NOISE_REL:g} of the fault current, so the fault "
                f"current returns entirely through shields and conductors and "
                f"the earth-return current I_E is zero to within rounding (for "
                f"example a fault at the far end of a cable whose end has no "
                f"electrode). The remaining electrode currents are rounding noise "
                f"at arbitrary angles; I_E, the current-based reduction factor "
                f"and the earth group are reported as None instead of being "
                f"built from them.",
                code="EARTH_GROUP_UNDEFINED",
                elements=elements,
                stacklevel=4,
            )

    def _compute_current_reduction_factors(
        self,
    ) -> Tuple[
        Dict[float, Optional[float]],
        Dict[float, Optional[complex]],
        Dict[float, List[str]],
        Dict[float, Optional[complex]],
        Dict[float, Optional[complex]],
        List[Tuple[float, float, float]],
    ]:
        """
        Reduction factor on a current basis: earth-return current over fault current.

        The reduction factor describes the effect of the inductive coupling
        between the phase conductor and the earth conductor, and as a measured
        quantity it is

        .. math::

            r = \\frac{|\\underline{I}_E|}{|\\underline{I}_F|}

        with ``I_E`` the **sum of the electrode currents of every bus that feeds
        the soil** -- not the electrode current at the faulted station. Where the
        stations are bonded through continuous cable shields the current spreads
        along them and leaks into the soil at every one of them, which is exactly
        what makes the quantity worth reporting. Measured on a six-station
        feeder, taking only the faulted station understates ``r`` by a factor of
        1.8 to 4.7 depending on where the fault sits.

        Summing over *all* buses would give zero identically -- whatever enters
        the soil leaves it again -- so the buses are split into the group that
        feeds the soil and the group that takes the current back out. The split
        is made in the complex plane and is threshold-free; see
        :mod:`groundinsight.utils.earth_current` for why looking for a
        zero crossing of the potential does not work.

        ``ResultReductionFactor.value``, the EPR-based factor, is left untouched
        alongside it: that one keeps the closed form ``r = |1 - Z_m/Z_s|`` for a
        single shielded line.

        Rounding noise (audit finding P9, decision F14): electrode currents at
        or below ``1e-9`` of the largest one join no group; the group is
        oriented by the fault bus, or -- when the fault bus has no electrode --
        by the fault-bus potential; and when no electrode carries more than
        ``1e-9`` of the fault current there is no group at all. See
        :mod:`groundinsight.utils.earth_current`.

        Returns
        -------
        tuple
            ``(r, I_E, feeding buses, U_E of the group, Z_E of the group,
            undefined points)``, the first five per frequency. ``None`` (or an
            empty list) where the frequency carries no fault current or no
            earth-return group exists; the last element lists those
            frequencies of the second kind as ``(frequency, max|I_a|, |I_F|)``.
        """
        factors: Dict[float, Optional[float]] = {}
        earth_currents: Dict[float, Optional[complex]] = {}
        feeding: Dict[float, List[str]] = {}
        earthing_voltages: Dict[float, Optional[complex]] = {}
        earthing_impedances: Dict[float, Optional[complex]] = {}
        undefined: List[Tuple[float, float, float]] = []
        fault_bus = self.network.faults[self.network.active_fault].bus

        for freq in self.network.frequencies:
            factors[freq] = None
            earth_currents[freq] = None
            feeding[freq] = []
            earthing_voltages[freq] = None
            earthing_impedances[freq] = None

            u_vector = self.u_vectors.get(freq)
            if u_vector is None:
                continue

            # The fault current is the sum of the source injections that reach
            # this fault, ``Sum(scaling * I_src)``, and it is recorded as such
            # by ``_construct_vectors``. Reading it off the injection vector at
            # the fault bus instead gives the *net* nodal injection there, and
            # those two differ as soon as a source sits on the fault bus:
            # the entry is then ``I_src,F - Sum(I_src)``. With S1 = 100 A at a
            # remote bus and S2 = 50 A on the fault bus, the net entry is
            # -100 A instead of 150 A and the current-based reduction factor
            # came out 100x too small.
            total = self.total_source_currents.get(freq)
            if total is None:
                continue
            i_fault = complex(total)
            if i_fault == 0:
                continue

            electrode_currents: Dict[str, complex] = {}
            for bus_name, idx in self.bus_indices.items():
                Z_complex = self._resolved_impedance(
                    self.network.buses[bus_name].impedance.get(freq), freq
                )
                if Z_complex is None or _is_open_circuit(Z_complex):
                    continue
                electrode_currents[bus_name] = complex(u_vector[idx]) / Z_complex

            # No group exists when every electrode current is rounding noise
            # against the fault current: the current returns through the
            # shields and I_E is zero to within rounding (P9).
            if earth_current_vanishes(electrode_currents, i_fault):
                largest = max(
                    (abs(v) for v in electrode_currents.values()), default=0.0
                )
                if self._is_excited(freq):
                    undefined.append((freq, largest, abs(i_fault)))
                continue

            # Anchor for a fault bus without (non-noise) electrode current:
            # the group in phase with the fault-bus potential feeds the soil.
            # A fault-bus potential that is itself noise orients nothing.
            u_fault = complex(u_vector[self.bus_indices[fault_bus]])
            u_scale = float(np.abs(u_vector).max())
            anchor = u_fault if abs(u_fault) > NOISE_REL * u_scale else None
            split = split_earth_currents(
                electrode_currents,
                reference_bus=fault_bus,
                anchor=anchor,
            )
            if split is None:
                continue

            factors[freq] = abs(split.i_earth) / abs(i_fault)
            earth_currents[freq] = split.i_earth
            feeding[freq] = split.feeding_buses

            # Diagnostics of the group (u_earthing_group / z_earthing_group):
            # its current-weighted mean potential and that mean over I_E. They
            # were reported as U_E / Z_E before decision F4; the EN 50522
            # quantities are now the fault-bus EPR and the driving-point
            # impedance (see compute_reduction_factors). The chain
            # mean = I_F * (mean / I_E) * |I_E| / |I_F| closes on these by
            # construction, which is why it checks nothing.
            potentials = {
                name: complex(u_vector[idx])
                for name, idx in self.bus_indices.items()
            }
            u_e, equipotential = earthing_voltage(
                potentials, electrode_currents, split.feeding_buses
            )
            earthing_voltages[freq] = u_e
            if split.i_earth != 0:
                earthing_impedances[freq] = u_e / split.i_earth
            if equipotential < 0.9 and split.feeding_buses:
                logger.info(
                    "The stations counted as one earth group at %s Hz are "
                    "not at one potential (coherence %.3f), so the group mean "
                    "u_earthing_group = %.4g V averages genuinely different "
                    "voltages and z_earthing_group follows it. U_E of EN 50522 "
                    "is the EPR at the fault bus (u_earthing). Group: "
                    "%s.",
                    freq,
                    equipotential,
                    abs(u_e),
                    ", ".join(split.feeding_buses),
                )
            if split.separation < 0.9:
                logger.warning(
                    "The electrode currents at %s Hz are spread in angle "
                    "(separation %.3f), so splitting them into one group that "
                    "feeds the soil and one that returns it is less sharp than "
                    "usual. The earth-return current %.4g A and the reduction "
                    "factor %.4f built from it describe the dominant direction "
                    "only. Buses counted as feeding: %s.",
                    freq,
                    split.separation,
                    abs(split.i_earth),
                    factors[freq],
                    ", ".join(split.feeding_buses),
                )
        return (
            factors,
            earth_currents,
            feeding,
            earthing_voltages,
            earthing_impedances,
            undefined,
        )

    def _warn_on_reduction_factor_divergence(
        self,
        coupling: Dict[float, Optional[float]],
        current: Dict[float, Optional[float]],
    ) -> None:
        """
        Point out where the two reduction factors part company, and why.

        They are not two computations of one number. For a route with shield
        impedance ``Z_s``, mutual impedance ``Z_m`` and station electrodes
        summing to ``Z_E`` along the earth-return path,

        .. math::

            r_\\text{coupling} = \\frac{Z_s - Z_m}{Z_s}
            \\qquad
            r_I = \\frac{Z_s - Z_m}{Z_s + Z_E}

        so their ratio is the current divider ``Z_s / (Z_s + Z_E)`` and nothing
        else. ``value`` is the **ideally bonded** limit -- the tabulated property
        of the cable, ``1 - Z_m/Z_s``, independent of the station earths by
        construction. ``value_current`` is what the earthing system of *this*
        network actually passes into the soil. Verified against the closed form
        to machine precision over five decades of electrode impedance: they
        converge exactly as ``Z_E -> 0`` and differ by a factor of 45 at 10 Ohm
        electrodes against a 0.45 Ohm shield.

        A large gap is therefore a statement about the network, not a defect:
        the stations are not bonded well enough for the cable's tabulated
        reduction factor to describe what the soil sees.

        Parameters
        ----------
        coupling : Dict[float, Optional[float]]
            ``ResultReductionFactor.value`` per frequency.
        current : Dict[float, Optional[float]]
            ``ResultReductionFactor.value_current`` per frequency.
        """
        for freq in self.network.frequencies:
            r_c = coupling.get(freq)
            r_i = current.get(freq)
            if r_c in (None, 0) or r_i is None:
                continue
            divider = r_i / r_c
            if divider >= 0.5:
                continue
            # INFO, not WARNING: a wide gap is a property of the network, not a
            # defect, and it is the normal case for stations with ordinary
            # electrodes. Raising it to a warning on every such network is how
            # a log gets trained out of being read.
            logger.info(
                "At %s Hz the coupling-based reduction factor is %.4f while the "
                "current-based one is %.4f -- a current divider of %.3f between "
                "the shield path and the earth path. The two are the same "
                "quantity only when the station electrodes vanish against the "
                "shield impedance: r_coupling = (Z_s-Z_m)/Z_s is the ideally "
                "bonded limit and the tabulated cable property, r_current = "
                "(Z_s-Z_m)/(Z_s+Z_E) is what this earthing system passes into "
                "the soil. A divider this small says the stations are not bonded "
                "well enough for the cable value to describe the soil current; "
                "it is a property of the network, not a numerical artefact.",
                freq,
                r_c,
                r_i,
                divider,
            )

    def compute_grounding_impedance(self):
        """
        Compute the grounding impedance for the fault bus.

        The grounding impedance is the earthing impedance of EN 50522 at the
        fault bus::

            Z_G = Z_E = Z_dp(F)

        the driving-point impedance of the earthing network at the fault bus:
        the voltage one ampere injected there raises the bus to against remote
        earth, with every electrode and shield in place, without the inductive
        coupling and without the Thevenin loop closures of voltage sources. It
        is a property of the earthing system -- what an earthing-impedance
        measurement at the station sees, the stations bonded to it through
        cable shields included -- and it is computed alongside the reduction
        factors as ``ResultReductionFactor.z_earthing``, which this method
        copies. With ``U_E`` the EPR at the fault bus and
        ``r = |1 - Z_m/Z_s|`` the norm's chain ``U_E = r * 3*I_0 * Z_E`` then
        holds where the norm's assumptions hold (a fault far from the infeed
        on a long bonded run), as a physical statement rather than by
        construction.

        This is decision F4 of audit pass 19. It replaced ``Z_E = U_E / I_E``
        over the earth group (kept as ``z_earthing_group``), which on a long
        homogeneous chain came out 45 % below the driving-point impedance, and
        which in turn had replaced the pre-0.6.0 ``u_EPR / (r * I_fault)``.
        ``docs/concepts.md`` and ``docs/api/electrical_network.md`` name the
        current definition.

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
            # Z_E = Z_dp(F), computed alongside the reduction factors (F4).
            z_earthing = result.reduction_factor.z_earthing.get(freq)
            if z_earthing is not None:
                grounding_impedances[freq] = ComplexNumber(
                    real=complex(z_earthing).real, imag=complex(z_earthing).imag
                )
                continue

            # The fault bus reaches remote earth only through a Thevenin loop:
            # the earthing network alone presents no finite impedance there.
            grounding_impedances[freq] = None

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

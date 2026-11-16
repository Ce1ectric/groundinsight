# electrical_network.py

"""
The numerical kernel of the fault solve, built from the core models.

:class:`ElectricalNetwork` computes with the matrix form of the network:

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
1) Path-based (``auto_phase_currents=False``, the default of this class;
   ``run_fault(phase_current_mode="paths")``, deprecated). For every simple path from
   source to fault, the branch direction is derived from the actual path
   traversal (no index heuristic). The user may scale the contribution per
   branch via Branch.parallel_coefficient, the knob for splitting current
   between parallel paths in this mode.
2) The coupled solve (``auto_phase_currents=True``; the default of
   :func:`~groundinsight.run_fault`, ``phase_current_mode="auto"``). Phase
   conductors, shields and electrodes are one linear system per frequency
   (:meth:`ElectricalNetwork._compute_phase_currents_auto`); the phase
   currents it gives drive the Norton injections above, and the earth
   potentials are its own. Where the phase network around the fault has no
   mesh with coupling and no Thevenin source, the phase currents are the
   source currents and the phase-only network gives them exactly. In this
   mode parallel_coefficient is ignored.

Sparse kernel
-------------
Both linear systems of a frequency are sparse -- a grounding network has a
few entries per row -- and the kernel treats them that way end to end.

*Model values as arrays.* The impedance dictionaries of all buses, branches
and voltage sources are read once per assembly into ``(elements, F)`` arrays
(:class:`_NetworkTables`), with the 0 Hz stand-in applied to them as a
whole. Every later stage indexes into those arrays instead of resolving one
impedance per element and frequency in Python.

*Index tables instead of dense matrices.* Where a stamp lands -- a bus
diagonal, the four entries of a grounding branch or of a Thevenin loop
closure -- depends on the topology only. The positions are laid out once
per network (:class:`_StampPattern`: CSC ``indices``/``indptr`` and the
entry of every stamp); a frequency then costs one vector of admittances and
one summation per entry, straight into the CSC matrix the LU factorises
(:attr:`ElectricalNetwork.Y_sparse`). A dense ``n x n`` matrix kept per
frequency would make the memory grow as ``n**2 * F``; ``Y_matrices`` is
therefore only a dense view that materialises a matrix when someone reads
it.

*One factorisation, many right-hand sides.* The phase-conductor network is
the same matrix for every source, so it is factorised once per frequency
(:func:`scipy.sparse.linalg.splu`) and solved for all sources as the columns
of one right-hand side. A dense ``n x n`` solve per source and frequency
would be the dominant cost of a run, growing faster than ``n**2``.

*Result objects with the collector paused.* A solve creates one
:class:`ComplexNumber` per element, quantity and frequency. Those objects
form no reference cycles, but their sheer number keeps triggering the cyclic
garbage collector, which then walks the whole network model each time. The
result rows are therefore built inside :func:`_gc_paused`, which restores
the collector afterwards.

*Program order, kept on purpose.* Matrices are assembled from stamps whose
contributions are summed entry by entry in the order an element-by-element
dense assembly adds them (:func:`_assemble_csc`), so the sums are those of
that reference. What differs from it is rounding: the admittances come from
NumPy's complex division, which differs from Python's in the last bit for
about a quarter of all values, and the factorisation is a sparse LU instead
of LAPACK's dense one. On the badly conditioned phase network of a long
radial feeder the sparse LU is the *more* accurate of the two.
"""

import gc
import hashlib
import logging
import math
import threading
import warnings
from collections.abc import Mapping
from contextlib import contextmanager

import numpy as np
from typing import Dict, Iterator, List, NamedTuple, Optional, Tuple
from scipy.sparse import coo_matrix, csc_matrix, csr_matrix, issparse
from scipy.sparse.csgraph import connected_components
from scipy.sparse.linalg import MatrixRankWarning, splu
from groundinsight.models.core_models import (
    Network,
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
    non_passive_mask,
    check_passive_impedance,
    dc_substitute_impedance,
    is_short_circuit,
    render_frequencies,
    short_circuit_mask,
)
from groundinsight.diagnostics import InputWarning, ModelAssumptionWarning, emit, note


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

    ``Z == 0`` is deliberately *not* treated as open: that would make a perfect
    earth electrode indistinguishable from a missing one -- sweeping a
    grounding impedance towards zero converges to EPR = 0, while the value
    *at* zero would return the no-electrode EPR. Above 0 Hz zero is rejected
    where it is computed
    (:func:`utils.impedance_calculator.check_passive_impedance`) and again in
    :meth:`ElectricalNetwork._validate_passive_impedances` for values that
    never passed through a formula. At 0 Hz it is legitimate -- an inductance
    is a short circuit at DC -- and
    :meth:`ElectricalNetwork._dc_substitute_at` replaces it with a small finite
    value before the matrix is assembled.

    ``NaN`` is deliberately *not* treated as open either. A NaN impedance is a
    failed computation, and it must stay visible so that
    :meth:`ElectricalNetwork._assert_finite_system` can report it.

    A real part of *minus* infinity never reaches this function for an
    impedance that is inverted: it is the limit of a negative resistance, not
    an open circuit, and the passivity checks refuse it beforehand.

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
    nothing and only cost time -- measured at half of the result-building
    time on large networks.

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
    works on arrays instead of calling :meth:`ElectricalNetwork.resolved_impedance`
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
    """Vectorised :func:`_is_open_circuit`: ``True`` where a part is infinite.

    Parameters
    ----------
    values : numpy.ndarray
        Complex impedances of any shape.

    Returns
    -------
    numpy.ndarray
        Boolean mask of the same shape; ``NaN`` counts as *not* open, as in
        the scalar function.
    """
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


def bus_admittances(
    bus_z: np.ndarray, present: np.ndarray
) -> np.ndarray:
    """The grounding admittance each bus stamps on the diagonal of ``Y``.

    Internal API: also used by :mod:`groundinsight.analysis._session`; not
    exported from ``groundinsight`` and free to change in any release.

    The one rule for it, shared by the assembly and by the study sessions
    that re-assemble ``Y`` for changed bus impedances (P4): ``1/Z`` where a
    value is stored and not open, zero elsewhere.

    Parameters
    ----------
    bus_z : numpy.ndarray
        Resolved grounding impedances, ``(buses, F)``.
    present : numpy.ndarray
        Where a value is stored, same shape.

    Returns
    -------
    numpy.ndarray
        Complex admittances, same shape.
    """
    return _reciprocal(bus_z, present & ~_open_mask(bus_z))


def rms_columns(values: np.ndarray) -> np.ndarray:
    """RMS over the frequency columns, the convention of the result rows.

    Internal API: also used by :mod:`groundinsight.analysis._session`; not
    exported from ``groundinsight`` and free to change in any release.

    ``sqrt(sum |x|**2)``, accumulated frequency by frequency (see
    :meth:`ElectricalNetwork._calculate_rms`). The result rows and the study
    sessions (P4) both take their RMS from here, so that a re-solved trial
    reports bit for bit the value a full ``run_fault`` stores.

    Parameters
    ----------
    values : numpy.ndarray
        Complex array ``(elements, F)``.

    Returns
    -------
    numpy.ndarray
        Float array ``(elements,)``.
    """
    square = np.zeros(values.shape[0])
    for j in range(values.shape[1]):
        magnitude = np.abs(values[:, j])
        square += magnitude * magnitude
    return np.sqrt(square)


def stack_columns(vectors: List[Optional[np.ndarray]], rows: int) -> np.ndarray:
    """Per-frequency vectors as the columns of one ``(rows, F)`` array.

    Internal API: also used by :mod:`groundinsight.analysis._session`; not
    exported from ``groundinsight`` and free to change in any release.

    Parameters
    ----------
    vectors : list of numpy.ndarray or None
        One vector per frequency, in frequency order; ``None`` leaves the
        column at zero.
    rows : int
        Length of every vector.

    Returns
    -------
    numpy.ndarray
        Complex array; column ``j`` is ``vectors[j]``.
    """
    out = np.zeros((rows, len(vectors)), dtype=complex)
    for j, vector in enumerate(vectors):
        if vector is not None:
            out[:, j] = vector
    return out


def assemble_nodal_matrices(
    pattern: "_StampPattern",
    y_bus: np.ndarray,
    y_grid: np.ndarray,
    y_loop: np.ndarray,
    frequencies: List[float],
) -> Dict[float, csc_matrix]:
    """The nodal matrices from the stamp pattern and the stamp admittances.

    Internal API: also used by :mod:`groundinsight.analysis._session`; not
    exported from ``groundinsight`` and free to change in any release.

    The bus admittances are the first ``n`` stamps of the pattern; the four
    stamps of every grounding branch and of every Thevenin loop closure
    follow, in the order an element-by-element dense assembly adds them, and
    every entry is summed in that order (:meth:`_StampPattern.matrix`). The
    assembly and the study sessions that re-assemble ``Y`` for changed bus
    impedances both go through here, so the same admittances give bit for
    bit the same matrices.

    Parameters
    ----------
    pattern : _StampPattern
        Where every stamp lands.
    y_bus : numpy.ndarray
        Grounding admittance per active bus and frequency, ``(n, F)``.
    y_grid : numpy.ndarray
        Admittance per grounding branch and frequency.
    y_loop : numpy.ndarray
        Admittance per Thevenin loop closure and frequency.
    frequencies : list of float
        The network frequencies, one per column.

    Returns
    -------
    dict of float to scipy.sparse.csc_matrix
        One matrix per frequency.
    """
    matrices: Dict[float, csc_matrix] = {}
    for j, freq in enumerate(frequencies):
        y_g, y_l = y_grid[:, j], y_loop[:, j]
        values = np.concatenate([
            y_bus[:, j],
            np.stack([-y_g, -y_g, y_g, y_g], axis=1).ravel(),
            np.stack([y_l, y_l, -y_l, -y_l], axis=1).ravel(),
        ])
        matrices[freq] = pattern.matrix(values)
    return matrices


def component_labels(matrix) -> np.ndarray:
    """
    Connected groups of buses of a nodal matrix, from its off-diagonal entries.

    Internal API: also used by :mod:`groundinsight.analysis.response`; not
    exported from ``groundinsight`` and free to change in any release.

    Two buses belong together when the matrix couples them: a shield with a
    non-zero admittance at that frequency, or -- in the solved matrix -- the
    loop closure of a voltage source. An electrode sits on the diagonal and
    couples nothing. Whether a group reaches remote earth is then a question
    of structure, answered without a tolerance: it does if and only if one of
    its buses has a finite, non-zero electrode admittance. The linear solver
    cannot be asked instead. The matrix of a floating group of two or more
    buses is singular only up to rounding -- every row of a shield stamp sums
    to zero in exact arithmetic, not in floating point -- and ``splu`` then
    returns potentials of 1e15 Ohm per ampere and more instead of raising.

    Parameters
    ----------
    matrix : scipy.sparse.spmatrix
        A square nodal matrix.

    Returns
    -------
    numpy.ndarray
        Integer group label per row.
    """
    coo = matrix.tocoo()
    link = (coo.row != coo.col) & (coo.data != 0)
    graph = csr_matrix(
        (np.ones(int(link.sum())), (coo.row[link], coo.col[link])),
        shape=matrix.shape,
    )
    _, labels = connected_components(graph, directed=False)
    return labels


def restricted_columns(
    matrix, rows: np.ndarray, units: List[int]
) -> Optional[np.ndarray]:
    """
    Unit-injection columns of ``matrix``, solved on one group of buses only.

    Internal API: also used by :mod:`groundinsight.analysis.response`; not
    exported from ``groundinsight`` and free to change in any release.

    The buses outside ``rows`` are decoupled from it by construction (see
    :func:`component_labels`), so the columns inside the group do not
    depend on them -- and a floating group elsewhere in the matrix cannot
    make this solve singular.

    Parameters
    ----------
    matrix : scipy.sparse.spmatrix
        Square nodal matrix.
    rows : numpy.ndarray
        Boolean mask of the group.
    units : list of int
        Rows (inside the group) at which one ampere is injected, one column
        each.

    Returns
    -------
    numpy.ndarray or None
        ``(n, len(units))`` voltages, zero outside the group; ``None`` if the
        group's matrix is numerically singular or the solve is not finite.
    """
    index = np.flatnonzero(rows)
    sub = csc_matrix(matrix.tocsr()[index][:, index])
    rhs = np.zeros((index.size, len(units)), dtype=complex)
    rhs[np.searchsorted(index, units), np.arange(len(units))] = 1.0
    try:
        solved = splu(sub).solve(rhs)
    except (np.linalg.LinAlgError, RuntimeError):
        return None
    if not np.all(np.isfinite(solved)):
        return None
    columns = np.zeros((matrix.shape[0], len(units)), dtype=complex)
    columns[index] = solved
    return columns


class _StampPattern:
    """The sparsity structure of a matrix assembled from a fixed list of stamps.

    The position of every stamp -- which bus, which branch end -- does not
    depend on the frequency; only its value does. The structure (CSC
    ``indices``/``indptr`` and the map from each stamp to its entry) is
    therefore computed once per network and reused for every frequency, which
    then costs one :func:`numpy.bincount` per real and imaginary part (P2: a
    dense assembly would allocate and fill one ``n x n`` matrix per frequency
    in a Python loop, and convert each to CSC by scanning all ``n**2``
    entries).

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


class NodalParts(NamedTuple):
    """
    What a re-assembly of ``Y`` for other bus impedances needs from a solve.

    Internal API: handed to :mod:`groundinsight.analysis._session` by
    :meth:`ElectricalNetwork.nodal_parts`; not exported from
    ``groundinsight`` and free to change in any release.

    Attributes
    ----------
    pattern : _StampPattern
        Where every stamp of ``Y`` lands.
    bus_z : numpy.ndarray
        Resolved grounding impedance per active bus and frequency, ``(n, F)``.
    bus_present : numpy.ndarray
        Where a bus impedance is stored, same shape.
    grid_admittance : numpy.ndarray
        Admittance per grounding branch and frequency.
    loop_admittance : numpy.ndarray
        Admittance per Thevenin loop closure and frequency.
    i_vectors : list of numpy.ndarray
        The injection vector per network frequency, in frequency order.
    dc_substitute : bool
        Whether a 0 Hz stand-in is in use (its size may follow a bus
        impedance, so the parts do not describe ``Y`` for other ones).
    """

    pattern: "_StampPattern"
    bus_z: np.ndarray
    bus_present: np.ndarray
    grid_admittance: np.ndarray
    loop_admittance: np.ndarray
    i_vectors: List[np.ndarray]
    dc_substitute: bool


class _DenseYView(Mapping):
    """``Y_matrices``: frequency to dense ``numpy`` array, materialised on access.

    The solver works on :attr:`ElectricalNetwork.Y_sparse`. This read-only
    mapping keeps the attribute usable for code that reads a dense
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


class _CoupledStamps:
    """Stamps of the coupled phase/earth system, summed into one CSC matrix.

    Parameters
    ----------
    n : int
        Number of earth nodes (the active buses); the phase nodes follow.
    """

    def __init__(self, n: int):
        self.n = n
        self.rows: List[np.ndarray] = []
        self.cols: List[np.ndarray] = []
        self.values: List[np.ndarray] = []

    def add(self, rows: np.ndarray, cols: np.ndarray, values: np.ndarray) -> None:
        """Add ``values`` at ``(rows, cols)``."""
        self.rows.append(np.asarray(rows, dtype=np.int64))
        self.cols.append(np.asarray(cols, dtype=np.int64))
        self.values.append(np.asarray(values, dtype=complex))

    def pair(self, i_a, i_b, j_a, j_b, y) -> None:
        """Stamp the admittance ``y`` between two conductors.

        Conductor ``i`` runs from node ``i_a`` to ``i_b``, conductor ``j``
        from ``j_a`` to ``j_b``: ``+y`` at (i_a, j_a) and (i_b, j_b), ``-y`` at
        (i_a, j_b) and (i_b, j_a). Zero entries drop out.
        """
        y = np.asarray(y, dtype=complex)
        keep = y != 0
        i_a, i_b, j_a, j_b, y = (np.asarray(v)[keep] for v in (i_a, i_b, j_a, j_b, y))
        self.add(np.concatenate([i_a, i_b, i_a, i_b]), np.concatenate([j_a, j_b, j_b, j_a]),
                 np.concatenate([y, y, -y, -y]))

    def matrix(self, size: int, diagonal: np.ndarray) -> csc_matrix:
        """The summed matrix, ``size x size``, with ``diagonal`` on the earth nodes.

        Parameters
        ----------
        size : int
            Number of nodes.
        diagonal : numpy.ndarray
            The grounding admittance of every earth node, stamped first.

        Returns
        -------
        scipy.sparse.csc_matrix
            The matrix; the same stamps in the same order give the same bits.
        """
        earth = np.arange(self.n, dtype=np.int64)
        rows = np.concatenate([earth] + self.rows)
        cols = np.concatenate([earth] + self.cols)
        values = np.concatenate([np.asarray(diagonal, dtype=complex)] + self.values)
        return coo_matrix((values, (rows, cols)), shape=(size, size)).tocsc()


class _NetworkTables:
    """Everything one assembly reads from the model, gathered into arrays.

    Touching every bus and branch once per frequency and stage -- assembly,
    phase network, coupling terms, result rows -- through a Python-level
    dictionary lookup and :meth:`ElectricalNetwork.resolved_impedance` would
    cost several hundred thousand calls on a 4000-bus ring at seven
    frequencies before any linear algebra happens. The tables are filled
    once per :class:`ElectricalNetwork` and every later stage indexes into
    them; the state fingerprint is a digest of the same arrays.

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
        (:meth:`ElectricalNetwork.resolved_impedance`, vectorised); filled
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

        Vectorised :meth:`ElectricalNetwork.resolved_impedance`: where a
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
        """Apply the 0 Hz stand-in to one impedance table.

        Parameters
        ----------
        values : numpy.ndarray
            Stored impedances, ``(elements, F)``.
        present : numpy.ndarray
            Where a value is stored at all.
        substitutes : dict of float to float
            Frequency to stand-in impedance.

        Returns
        -------
        numpy.ndarray
            A copy in which every stored short circuit of a substituted
            column (zero, or too small to invert; see ``short_circuit_mask``)
            is replaced by the stand-in, or ``values`` itself when nothing is
            substituted.
        """
        columns = [
            (self.column[f], s) for f, s in substitutes.items() if f in self.column
        ]
        if not columns or values.size == 0:
            return values
        out = values.copy()
        for j, substitute in columns:
            col = out[:, j]
            shorted = short_circuit_mask(col.real, col.imag) & present[:, j]
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
        has gathered anyway, the digest costs about 5 ms on a 4000-bus ring at
        seven frequencies, where a separate pass over the model building a
        tuple of floats takes about 26 ms on every build. Negative zeros are
        folded into zeros first, so that ``0.0`` and ``-0.0`` compare equal.

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

    Parameters
    ----------
    network : Network
        The Network instance containing buses, branches, sources, and faults.
    auto_phase_currents : bool, optional
        If True the phase current through each branch comes from the
        coupled solve of phase conductors, shields and electrodes (Variant B).
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

        Sets up necessary data structures and initializes the network
        calculations; the parameters are documented on the class.
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
        # Per Thevenin source and frequency, from the coupled solve
        # (phase_current_mode="auto"): its loop current and the earth-side
        # injection that carries it; see _compute_phase_currents_auto.
        self.thevenin_currents: Dict[float, Dict[str, Tuple[complex, complex]]] = {}
        # False once a frequency needed the coupled solve: its phase currents
        # then depend on the bus impedances (analysis/_session.py re-solves
        # the coupled system for such a frequency).
        self.phase_split_fixed = True
        # The coupled systems of the frequencies that needed one (stamps
        # without the electrodes, right-hand side, size), and the earth
        # potentials they give in the package frame: solve_network takes them,
        # so a study session that re-solves the system with other electrodes
        # (coupled_solution) gets the same numbers as run_fault.
        self._coupled_systems: Dict[float, Tuple[_CoupledStamps, np.ndarray, int]] = {}
        self.coupled_potentials: Dict[float, np.ndarray] = {}
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
        # frequency, ``(n, F)``; see :meth:`grounding_shunts`.
        self._bus_admittance: Optional[np.ndarray] = None
        # Where every stamp of Y lands, and the admittances of the stamps
        # that are not bus electrodes (grounding branches, Thevenin loop
        # closures), ``(stamps, F)`` each. Kept so that Y can be re-assembled
        # for other bus admittances without touching the model (P4); see
        # :meth:`_assemble_nodal`.
        self._stamp_pattern: Optional[_StampPattern] = None
        self._grid_admittance: Optional[np.ndarray] = None
        self._loop_admittance: Optional[np.ndarray] = None
        # The electrode currents ``u / Z`` per active bus and frequency as
        # ``solve_network`` reports them in ``ResultBus.ia``, and where a bus
        # has an electrode at all; the earth-return split reads these (P6).
        self._electrode_currents: Optional[np.ndarray] = None
        self._electrode_usable: Optional[np.ndarray] = None

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
        digested with BLAKE2 (:meth:`_NetworkTables.fingerprint`); the
        digest covers every stored value, including values at frequencies the
        network is not solved at. A call here reads the model afresh (about
        25 ms on a 4000-bus ring at seven frequencies); the fingerprint of a
        build reuses the tables of the assembly and costs about 5 ms.
        """
        return _NetworkTables(self, fingerprint_only=True).fingerprint(
            self.network.active_fault
        )

    def matches_network_state(self) -> bool:
        """
        ``True`` while the assembled matrices still describe ``self.network``.

        See :meth:`state_fingerprint` for what "still describe" covers.

        Returns
        -------
        bool
            Whether the network's state fingerprint equals the one recorded
            when the matrices were built.
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
        or loading a network written before a frequency was added -- would,
        unchecked, report 1000 V instead of 4.95 V at 250 Hz in a measured
        case, with a reduction factor of 1.0, and nothing in the output
        saying why.

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
            f"{label} at {render_frequencies(freqs)}" for label, freqs in missing
        )
        raise ValueError(
            "The network is being solved at "
            f"{render_frequencies(frequencies)}, but the following active "
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
        from a database or a JSON file that was never re-evaluated, therefore
        reaches the solver untouched. This is the pass that catches those.

        Only the impedances that are actually inverted are checked: active
        buses, the self impedance of active grounding-conductor branches
        between two active buses, the source impedance of voltage sources and,
        when the phase network is solved (``auto_phase_currents``), the
        declared phase impedance of active branches between two active buses.
        Mutual impedances are never inverted and are deliberately left alone --
        zero mutual coupling is the normal case. A phase impedance that is zero
        *at 0 Hz* is accepted like any other element's and replaced by the DC
        stand-in (:meth:`_dc_substitute_at`); above 0 Hz it is refused here,
        before the phase-network solve would invert it and fail with a bare
        ``ZeroDivisionError``.

        Raises
        ------
        ValueError
            If any checked impedance is zero, too small to invert in double
            precision, or has a negative real part or a real part of minus
            infinity. The message names the element and every offending
            frequency.
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

        for branch in self._branches_with_inverted_phase_impedance():
            check_passive_impedance(
                self._relevant(branch.phase_impedance),
                element=f"branch '{branch.name}' (phase impedance)",
                formula_str=branch.type.phase_impedance_formula,
            )

    def _branches_with_inverted_phase_impedance(self) -> List[Branch]:
        """
        Branches whose declared phase impedance the phase-network solve inverts.

        Active branches between two active buses with a declared
        ``phase_impedance``, and only when the phase network is solved
        (``auto_phase_currents``); in the path mode the phase current comes
        from the enumerated paths and no phase impedance is inverted.

        Returns
        -------
        list of Branch
            In network order.
        """
        if not self.auto_phase_currents:
            return []
        return [
            branch
            for branch in self.network.branches.values()
            if branch.active
            and branch.phase_impedance is not None
            and branch.from_bus in self.bus_indices
            and branch.to_bus in self.bus_indices
        ]

    def _dc_substitute_at(self, freq: float) -> Optional[float]:
        """Size the stand-in for elements that are a short circuit at 0 Hz.

        Only 0 Hz can need one: every other frequency has already been rejected
        by :meth:`_validate_passive_impedances`. At 0 Hz a purely inductive
        element legitimately has zero impedance -- an ideal short -- which the
        nodal formulation cannot invert. This pass finds those elements, and
        collects the magnitudes of the impedances that *are* usable so
        :func:`~groundinsight.utils.impedance_calculator.dc_substitute_impedance`
        can scale the substitute to the network.

        The scan covers exactly the impedances that are inverted: in
        :meth:`_construct_Y_matrices` active buses, the self impedance of
        active grounding-conductor branches between two active buses and the
        impedance of voltage sources; in the phase-network solve the declared
        phase impedances (:meth:`_branches_with_inverted_phase_impedance`).
        A phase conductor modelled as ``j*2*pi*f*L*l`` is an ideal short at
        DC like an inductive shield, and without it in the scan a network
        whose only DC short was a phase conductor got no stand-in and
        ``run_fault`` raised ``ZeroDivisionError``. A shorted phase conductor
        triggers the stand-in and receives it; the stand-in itself stays
        scaled to the smallest usable impedance of the earthing network, so
        the earthing results of a network with a DC short are unchanged.
        Mutual impedances are never inverted.

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

        def classify(impedance, label: str, scale: bool = True) -> None:
            if impedance is None:
                return
            value = complex(impedance.real, impedance.imag)
            if is_short_circuit(value):
                shorted.append(label)
            elif scale:
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

        # A shorted phase conductor needs the stand-in as well, but the
        # stand-in stays scaled to the earthing network's elements: it is the
        # one value every shorted element of either system gets, and the
        # earthing results of a network with a DC short do not move because
        # its phase conductors are declared.
        for branch in self._branches_with_inverted_phase_impedance():
            classify(
                branch.phase_impedance.get(freq),
                f"branch '{branch.name}' (phase conductor)",
                scale=False,
            )

        if not shorted:
            return None
        return dc_substitute_impedance(
            magnitudes, shorted, context="steady-state solve"
        )

    def resolved_impedance(self, impedance, freq: float) -> Optional[complex]:
        """Complex value of a stored impedance, with a 0 Hz short substituted.

        Internal API: also used by :mod:`groundinsight.analysis.decomposition`;
        not exported from ``groundinsight`` and free to change in any release.

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

        The same four rules as
        :func:`~groundinsight.utils.impedance_calculator.check_passive_impedance`
        -- exactly zero above 0 Hz, too small to invert above 0 Hz, negative
        real part and real part of minus infinity at any frequency -- over the
        same elements (active buses, grounding-conductor branches between
        active buses, voltage sources on an active bus, and the declared phase
        impedances the phase-network solve inverts) and the same frequencies,
        evaluated on whole arrays.
        Calling the element-wise check for every element costs 0.19 s on a
        4000-bus ring although it almost never finds anything;
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
        frequencies = np.asarray(tables.frequencies, dtype=float)[None, :]

        def passes(values: np.ndarray, present: np.ndarray) -> bool:
            # The rules themselves live in one place (ARC6):
            # utils.impedance_calculator.passivity_classes.
            if values.size == 0:
                return True
            return not np.any(non_passive_mask(values, frequencies) & present)

        grid = tables.branch_in_net & tables.branch_grounding
        on_bus = tables.vs_bus >= 0
        # The declared phase impedances the phase-network solve inverts
        # (see _branches_with_inverted_phase_impedance).
        phase = tables.branch_in_net & tables.phase_declared
        if not self.auto_phase_currents:
            phase = np.zeros_like(phase)
        return (
            passes(tables.bus_z_raw, tables.bus_present)
            and passes(tables.self_raw[grid], tables.self_present[grid])
            and passes(tables.vs_z_raw[on_bus], tables.vs_present[on_bus])
            and passes(tables.phase_raw[phase], tables.phase_present[phase])
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
        **Index tables instead of dense matrices.** The matrices are kept as
        CSC in :attr:`Y_sparse`. The position of every stamp -- bus diagonal,
        the four entries of a grounding branch, the four entries of a Thevenin
        loop closure -- is fixed by the topology, so those positions are laid
        out once (:class:`_StampPattern`) and each frequency only computes the
        admittances and sums them per entry, in the order an element-by-element
        dense assembly adds them. Dense matrices kept for every frequency would
        take 2.3 GB on a 4000-bus ring at seven frequencies, with a hard limit
        near 5000 buses on an 8 GB machine, plus a scan of all ``n**2`` entries
        to convert each one to CSC. :attr:`Y_matrices` is a dense view that
        materialises a matrix only when it is read.

        Validation follows the same rules as the element-wise check: the
        passivity rules run on the gathered arrays (:meth:`_passive_impedances_ok`), and only when they
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

        # Stamp positions, in the order of an element-by-element dense
        # assembly: bus diagonals (active buses), then per grounding branch (from, to),
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
        self._stamp_pattern = _StampPattern(rows, cols, n)

        y_bus = bus_admittances(tables.bus_z, tables.bus_present)
        self._bus_admittance = y_bus
        z_grid = tables.self_z[grid]
        self._grid_admittance = _reciprocal(
            z_grid, tables.self_present[grid] & ~_open_mask(z_grid)
        )
        self._loop_admittance = y_loop
        self.Y_sparse = self._assemble_nodal(y_bus)
        self.Y_matrices = _DenseYView(self.Y_sparse)

    def _assemble_nodal(self, y_bus: np.ndarray) -> Dict[float, csc_matrix]:
        """
        The nodal matrices for given bus admittances, all other stamps as built.

        See :func:`assemble_nodal_matrices`; the grounding branches and
        Thevenin loop closures keep the values computed when the network was
        assembled.

        Parameters
        ----------
        y_bus : numpy.ndarray
            Grounding admittance per active bus and frequency, ``(n, F)``, as
            :func:`bus_admittances` computes it.

        Returns
        -------
        dict of float to scipy.sparse.csc_matrix
            One matrix per network frequency.
        """
        return assemble_nodal_matrices(
            self._stamp_pattern,
            y_bus,
            self._grid_admittance,
            self._loop_admittance,
            self.network.frequencies,
        )

    def nodal_parts(self) -> Optional[NodalParts]:
        """
        The parts of the assembled system a re-assembly of ``Y`` reads.

        Internal API: used by the study sessions of
        :mod:`groundinsight.analysis._session` (P4); not exported from
        ``groundinsight`` and free to change in any release.

        Returns
        -------
        NodalParts or None
            ``None`` until the matrices are assembled. The arrays are those
            of this assembly, not copies; a reader must not write to them.
        """
        if self._stamp_pattern is None:
            return None
        tables = self._network_tables()
        return NodalParts(
            pattern=self._stamp_pattern,
            bus_z=tables.bus_z,
            bus_present=tables.bus_present,
            grid_admittance=self._grid_admittance,
            loop_admittance=self._loop_admittance,
            i_vectors=[self.i_vectors[freq] for freq in self.network.frequencies],
            dc_substitute=bool(self._dc_substitutes),
        )

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
                strict=True,
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
        :meth:`earthing_admittance_matrix` as a CSC matrix.

        ``Y_sparse[freq]`` minus the Thevenin loop closures (see
        :meth:`remove_loop_closures`).

        Parameters
        ----------
        freq : float
            Frequency in Hz; ``Y_sparse[freq]`` must exist.

        Returns
        -------
        scipy.sparse.csc_matrix
            A new matrix; ``Y_sparse[freq]`` is not modified.
        """
        return self.remove_loop_closures(self.Y_sparse[freq], freq)

    def remove_loop_closures(self, matrix: csc_matrix, freq: float) -> csc_matrix:
        """
        ``matrix`` with the Thevenin loop-closure stamps of ``freq`` taken out.

        Internal API: also used by :mod:`groundinsight.analysis.response`; not
        exported from ``groundinsight`` and free to change in any release.

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
        is computed (see :meth:`compute_phase_currents`) and the Norton-equivalent
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
            if not self.auto_phase_currents:
                # The coupled solve of "auto" carries the loop current.
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
            # Phase currents per branch at this frequency. First, because the
            # coupled solve also gives the loop current of a Thevenin source.
            phase_currents = self.compute_phase_currents(freq, sources_with_path)
            self.phase_currents[freq] = phase_currents
            loops = self.thevenin_currents.get(freq, {})

            # ``effective`` is what each source drives into the earthing
            # system: its current, or the loop current of a Thevenin source
            # from the coupled solve. ``i_vector`` carries the injection that
            # gives it, with the loop closure of the Thevenin source in Y.
            effective = np.zeros(self.num_buses, dtype=complex)
            total_injection = 0j
            for source_name in sources_with_path:
                source = self.network.sources[source_name]
                if source.bus not in self.bus_indices:
                    continue
                bus_idx = self.bus_indices[source.bus]
                scaling = fault.scaling_at(freq)
                injection = self._source_injection(source, freq, scaling)
                if injection is None:
                    continue
                current = injection
                if source_name in loops:
                    current, injection = loops[source_name]
                total_source_current += current
                total_injection += injection
                i_vector[bus_idx] += injection
                effective[bus_idx] += current

            i_vector[fault_bus_idx] -= total_injection
            effective[fault_bus_idx] -= total_source_current
            self.total_source_currents[freq] = total_source_current

            # The source-only injection, before ``_add_mutual_currents``
            # mutates ``i_vector`` in place: the current a lumped earthing
            # conductor carries into (source buses) or out of (fault bus) the
            # grounding system; see the attribute comment in ``__init__`` for
            # why the mutual terms must not be included.
            self.source_injections[freq] = effective

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
                f"{render_frequencies(freqs)}, so it contributes no phase "
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
        One whose keys match *none* of the network frequencies would therefore
        contribute 0 A everywhere, and the run would come back with 0 V at every
        bus -- a plausible-looking result rather than an error -- so it raises.
        Partial coverage is legitimate (a harmonic present only in some
        sources) but changes the result at the uncovered frequencies, so it is
        said once per source and run.

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
                    f"{render_frequencies(keys) if keys else 'no frequency at all'}"
                    f", none of the network frequencies "
                    f"({render_frequencies(frequencies)}). It would contribute "
                    f"0 A at every solved frequency, and the run would report "
                    f"0 V at every bus as if that were a result. Give the source "
                    f"values at the network frequencies, or remove it."
                )
            missing = [f for f in frequencies if f not in key_set]
            if missing:
                emit(
                    InputWarning,
                    f"Source '{name}' at bus '{source.bus}' has no value at "
                    f"{render_frequencies(missing)} of the network frequencies "
                    f"and contributes 0 A there (fault '{fault.name}'). Covered: "
                    f"{render_frequencies([f for f in keys if f in solved])}.",
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
        forgotten entry -- or written in the belief that a missing key means
        zero -- so it is said once per run.

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
            f"{render_frequencies(missing)}; those frequencies are scaled with "
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
        phase current that drives the shield coupling, as the fault current
        ``I_F`` of the reduction factor and as ``i_inj``. The EPR depends on the difference
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
        coupled = self._coupled_grounding_branches()
        if not coupled:
            return
        emit(
            ModelAssumptionWarning,
            f"Fault '{fault.name}': the Thevenin source(s) "
            f"{', '.join(repr(s) for s in voltage_sources)} feed the fault while "
            f"branches with mutual coupling carry phase current "
            f"({_shortlist(coupled)}). The stationary solver couples them with "
            f"the Norton current E/Z_src instead of the actual loop current and "
            f"uses that current as the fault current I_F of the reduction factor and as "
            f"i_inj (a known limitation, not yet corrected). Against a fully "
            f"coupled multi-conductor reference the EPR at the fault came out 6 "
            f"to 38 % too low on cable feeders with Z_src between 5+15j and "
            f"0.3+1j Ohm -- unconservative, and growing as Z_src falls. For a "
            f"stationary study, model the infeed as a current source carrying "
            f"the prospective fault current (gi.create_source).",
            code="THEVENIN_COUPLING",
            elements=[("source", name) for name in voltage_sources],
            stacklevel=5,
        )

    def _electrode_arrays(
        self, potentials: Optional[np.ndarray] = None
    ) -> Tuple[np.ndarray, np.ndarray]:
        """
        The electrode currents ``u / Z`` per active bus and frequency.

        Computed once per solve and kept: ``ResultBus.ia`` reports them and
        the earth-return split reads the very same values (P6).

        Parameters
        ----------
        potentials : numpy.ndarray, optional
            The bus potentials ``(n, F)``; stacked from :attr:`u_vectors`
            when not given.

        Returns
        -------
        currents : numpy.ndarray
            Complex ``(n, F)``; zero where a bus has no electrode.
        usable : numpy.ndarray
            Boolean ``(n, F)``: a stored, not open impedance (an electrode).
        """
        if self._electrode_currents is None:
            tables = self._network_tables()
            if potentials is None:
                potentials = self._stacked(self.u_vectors)
            usable = tables.bus_present & ~_open_mask(tables.bus_z)
            currents = np.zeros(potentials.shape, dtype=complex)
            with np.errstate(over="ignore", invalid="ignore", divide="ignore"):
                np.divide(potentials, tables.bus_z, out=currents, where=usable)
            self._electrode_currents = currents
            self._electrode_usable = usable
        return self._electrode_currents, self._electrode_usable

    def _coupled_grounding_branches(self) -> List[str]:
        """
        The grounding-conductor branches that couple to their phase conductor.

        A branch counts when it is active between two active buses, has a
        grounding conductor, and at some network frequency carries a non-zero
        mutual impedance next to a stored, not open self impedance (0 Hz
        stand-in applied). Evaluated on the gathered arrays (P6), not by one
        lookup and resolution per branch and frequency.

        Returns
        -------
        list of str
            Branch names, in network order.
        """
        tables = self._network_tables()
        in_grid = tables.branch_in_net & tables.branch_grounding
        if tables.mutual.shape[0] == 0 or not in_grid.any():
            return []
        self_usable = tables.self_present & ~_open_mask(tables.self_z)
        coupled = (
            tables.mutual_present & self_usable & (tables.mutual != 0)
        ).any(axis=1) & in_grid
        return [tables.branch_names[row] for row in np.flatnonzero(coupled).tolist()]

    def _source_admittance(self, source: Source, freq: float) -> Optional[complex]:
        """
        ``1/Z_src`` of a Thevenin source at ``freq``, as the loop closure stamps it.

        Parameters
        ----------
        source : Source
            Any source.
        freq : float
            Frequency in Hz.

        Returns
        -------
        complex or None
            ``None`` for a current source and where no finite source
            impedance is stored.
        """
        if source.source_type != "voltage" or source.source_impedance is None:
            return None
        z_src = self.resolved_impedance(source.source_impedance.get(freq), freq)
        if z_src is None or _is_open_circuit(z_src):
            return None
        return 1.0 / z_src

    def _source_injection(
        self, source: Source, freq: float, scaling: float
    ) -> Optional[complex]:
        """
        Return the (Norton-equivalent) current injection of a source at a frequency.

        For ``source_type="current"`` this is ``scaling * I_src``.
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
        Z_src_c = self.resolved_impedance(
            source.source_impedance.get(freq), freq
        )
        if u is None or Z_src_c is None:
            return None
        if _is_open_circuit(Z_src_c):
            return None
        u_eff = scaling * complex(u.real, u.imag)
        return u_eff / Z_src_c

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

        # Path mode: the enumerated paths are the data, not just an
        # existence proof. ``run_fault`` does not build them in the
        # default mode, so a caller that switches to ``"paths"`` may find
        # them missing or stale. Silently reading an empty path set would
        # inject nothing at all and report 0 V at every bus, so rebuild --
        # the same choice the transient state-space solver makes.
        if self.network.needs_path_rebuild():
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
        return [s for s in self.network.sources if s in sources]

    def compute_phase_currents(
        self, freq: float, sources_with_path
    ) -> Dict[str, complex]:
        """
        Compute the signed phase current per branch at a given frequency.

        Internal API: also used by :mod:`groundinsight.simulation.transient`;
        not exported from ``groundinsight`` and free to change in any release.

        In the path-based mode (``auto_phase_currents=False``) the contributions are
        accumulated from each source's paths to the active fault. Direction comes from
        the actual path traversal (from-bus vs to-bus ordering) and the magnitude is
        scaled by ``Branch.parallel_coefficient``. A branch that lies on any path
        receives exactly one contribution per source (not one per path): the paths
        of a source are taken as one set of branches.

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
            Mapping from branch name to signed phase current (positive when
            the current flows from ``from_bus`` to ``to_bus``).
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

    def grounding_shunts(self, freq: float) -> Tuple[np.ndarray, np.ndarray]:
        """Grounding impedance of every active bus, and the admittance stamped.

        Internal API: also used by :mod:`groundinsight.analysis.response`; not
        exported from ``groundinsight`` and free to change in any release.

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

    def earthed_buses(self, freq: float) -> np.ndarray:
        """
        Buses with a path to remote earth of their own at ``freq``.

        Internal API: also used by :mod:`groundinsight.analysis.response`; not
        exported from ``groundinsight`` and free to change in any release.

        The admittance is the one the assembly stamped (0 Hz stand-in
        applied), so a missing, open (``"nan"``) or non-finite electrode does
        not count.

        Parameters
        ----------
        freq : float
            A frequency of the network.

        Returns
        -------
        numpy.ndarray
            Boolean mask in :attr:`bus_indices` order.
        """
        _, admittance = self.grounding_shunts(freq)
        return np.isfinite(admittance) & (admittance != 0)

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
        ValueError
            If a declared phase impedance is still exactly zero here. It
            cannot be: the passivity check refuses it above 0 Hz and the 0 Hz
            scan replaces it with the DC stand-in. The guard stays so that an
            unforeseen path ends in a message naming the branch rather than in
            an infinite admittance or a bare ``ZeroDivisionError``.
        """
        rows = np.flatnonzero(tables.branch_in_net)
        z_phase = tables.phase_z[rows, column]
        declared = tables.phase_declared[rows] & tables.phase_present[rows, column]
        admittance = np.zeros(rows.size, dtype=complex)

        invert = declared & ~_open_mask(z_phase)
        zero = invert & (z_phase.real == 0.0) & (z_phase.imag == 0.0)
        if zero.any():
            branch = tables.branch_names[rows[np.flatnonzero(zero)[0]]]
            raise ValueError(
                f"Branch '{branch}' has a phase impedance of exactly zero at "
                f"{tables.frequencies[column]} Hz, which the phase-network "
                f"solve cannot invert."
            )
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
        r"""
        The phase currents of the coupled multi-conductor model.

        Phase conductors, shields and electrodes are solved together in one
        linear system per frequency: every bus has an earth node (its
        electrode to remote earth) and a phase node, the fault bonds the phase
        node of the fault bus to its earth node, a current source drives its
        current from the earth node of its bus into the phase node, and a
        Thevenin source is its EMF in series with ``Z_src`` between the two.
        A branch with shield and phase conductor is the 2x2 impedance matrix

        $$
        \begin{pmatrix} Z_s & Z_m \\ Z_m & Z_{pp} \end{pmatrix},
        \qquad Z_{pp} = Z_{red} + \frac{Z_m^2}{Z_s},
        $$

        both loops with earth return; its admittance matrix is
        ``[[1/Z_s + k^2/Z_red, -k/Z_red], [-k/Z_red, 1/Z_red]]`` with
        ``k = Z_m/Z_s``. ``Z_red`` is the type's phase impedance (or the
        documented proxy, :meth:`_phase_admittances`), so the types stay as
        they are. A branch without grounding conductor or with an open shield
        is its phase conductor alone, a branch with an open phase conductor
        its shield alone.

        Why: the phase-only network with ``Z_red`` assumes that every shield
        carries the induced current ``k I_p`` and nothing else. The galvanic
        shield currents of a mesh with different cables -- and the earthing
        of the stations -- shift the phase split; on a 20 kV ring with two
        cable types the EPR moved by about 1 %. With one cable type per mesh
        and in every radial network the two agree exactly. The phase currents
        returned here are the coupled ones; :meth:`_add_mutual_currents` and
        the earth-side solve then reproduce the coupled earth potentials
        exactly, because given the phase currents the earth equations are the
        same. The loop current of a Thevenin source is part of the solution;
        it and the earth-side injection that carries it are kept in
        :attr:`thevenin_currents`.

        Where the coupled system is singular -- a group of buses whose only
        path to remote earth is missing -- the split of the phase network
        alone (:meth:`_compute_phase_currents_one_way`) is used, and the
        earth-side solve reports the group as it always has.

        Parameters
        ----------
        freq : float
            Frequency at which to solve.
        sources_with_path : Iterable[str]
            Sources contributing to the active fault.

        Returns
        -------
        Dict[str, complex]
            Signed phase current per branch (from -> to positive).
        """
        one_way = self._compute_phase_currents_one_way(freq, sources_with_path)
        if not self._coupling_matters(freq):
            return one_way
        coupled = self._solve_coupled(freq, sources_with_path)
        if coupled is None:
            return one_way
        phase_currents, loop_currents = coupled
        self.thevenin_currents[freq] = loop_currents
        self.phase_split_fixed = False
        return phase_currents

    def _coupling_matters(self, freq: float) -> bool:
        """
        ``False`` where the coupled phase split equals the one-way one exactly.

        Without a mesh in the phase network around the fault bus -- or without
        any phase-shield coupling in it, as at 0 Hz -- the phase currents do
        not depend on the shields and electrodes -- unless a Thevenin source
        sits in that network,
        whose loop current depends on the earth potentials. There the split of
        the phase network is returned as it is, bit for bit, and the phase
        currents do not depend on the bus impedances
        (:attr:`phase_split_fixed`).

        Parameters
        ----------
        freq : float
            Frequency in Hz.

        Returns
        -------
        bool
            ``True`` if the coupled system has to be solved.
        """
        fault = self.network.faults[self.network.active_fault]
        fault_idx = self.bus_indices[fault.bus]
        n = self.num_buses
        tables = self._network_tables()
        column = tables.column[float(freq)]
        rows = np.flatnonzero(tables.branch_in_net)
        y_red, _, _ = self._phase_admittances(tables, column)
        phase = y_red != 0
        ends_a, ends_b = tables.branch_from[rows][phase], tables.branch_to[rows][phase]
        graph = csr_matrix((np.ones(ends_a.size), (ends_a, ends_b)), shape=(n, n))
        _, labels = connected_components(graph, directed=False)
        in_component = labels == labels[fault_idx]
        # A mesh matters only where a phase conductor in it couples to its
        # shield (at 0 Hz Z_m is zero: the phase network is then on its own).
        z_mutual = tables.mutual[rows, column][phase]
        coupling = (
            tables.branch_grounding[rows][phase]
            & tables.mutual_present[rows, column][phase]
            & (z_mutual != 0)
            & in_component[ends_a]
        )
        meshed = coupling.any() and int(in_component[ends_a].sum()) > int(in_component.sum()) - 1
        thevenin = any(
            source.source_type == "voltage"
            and self.bus_indices.get(source.bus) is not None
            and self.bus_indices[source.bus] != fault_idx
            and in_component[self.bus_indices[source.bus]]
            for source in self.network.sources.values()
        )
        return meshed or thevenin

    def coupled_solution(self, freq: float, y_bus: np.ndarray) -> Optional[np.ndarray]:
        """
        Solve the coupled system of ``freq`` for given grounding admittances.

        Internal API: also used by :mod:`groundinsight.analysis._session`,
        whose trials change nothing but the electrodes -- the diagonal of the
        earth nodes. Not exported and free to change in any release.

        Parameters
        ----------
        freq : float
            A frequency at which :meth:`_solve_coupled` assembled a system.
        y_bus : numpy.ndarray
            Grounding admittance per active bus.

        Returns
        -------
        numpy.ndarray or None
            Earth node potentials (physical frame) followed by the phase
            nodes; ``None`` if the system is singular or the solution not
            finite.
        """
        stamps, rhs, size = self._coupled_systems[freq]
        matrix = stamps.matrix(size, y_bus)
        try:
            with warnings.catch_warnings():
                warnings.simplefilter("error", MatrixRankWarning)
                factor = splu(matrix)
                x = factor.solve(rhs)
                # One step of iterative refinement: the phase and earth nodes
                # of a long feeder make the system a Laplacian with condition
                # of order n**2, and the step brings the phase currents back to
                # the accuracy the phase-only network had.
                x = x + factor.solve(rhs - matrix @ x)
        except (RuntimeError, MatrixRankWarning):
            return None
        if not np.all(np.isfinite(x)):
            return None
        return x

    def _solve_coupled(
        self, freq: float, sources_with_path
    ) -> Optional[Tuple[Dict[str, complex], Dict[str, Tuple[complex, complex]]]]:
        """
        Assemble and solve the coupled system of one frequency.

        See :meth:`_compute_phase_currents_auto` for the model. The system is
        assembled in the physical frame (``+I_F`` into the earth node of the
        fault bus); the phase currents it returns are signed from -> to like
        those of the phase network.

        Parameters
        ----------
        freq : float
            Frequency in Hz.
        sources_with_path : Iterable[str]
            Sources contributing to the active fault.

        Returns
        -------
        tuple of (dict, dict) or None
            Phase current per branch, and per Thevenin source in the phase
            network its loop current and the injection of the earth-side
            system that carries it; ``None`` when nothing feeds the phase
            network or the system is singular.
        """
        fault = self.network.faults[self.network.active_fault]
        fault_idx = self.bus_indices[fault.bus]
        n = self.num_buses
        tables = self._network_tables()
        column = tables.column[float(freq)]
        rows = np.flatnonzero(tables.branch_in_net)
        y_red, _, _ = self._phase_admittances(tables, column)
        ends_a, ends_b = tables.branch_from[rows], tables.branch_to[rows]
        z_self = tables.self_z[rows, column]
        shield = (
            tables.branch_grounding[rows]
            & tables.self_present[rows, column]
            & ~_open_mask(z_self)
        )
        y_shield = _reciprocal(z_self, shield)
        z_mutual = np.where(
            shield & tables.mutual_present[rows, column], tables.mutual[rows, column], 0.0
        )

        # The phase nodes of the component around the fault bus; the phase
        # node of the fault bus is its earth node (the fault bond).
        phase = y_red != 0
        graph = csr_matrix(
            (np.ones(int(phase.sum())), (ends_a[phase], ends_b[phase])), shape=(n, n)
        )
        _, labels = connected_components(graph, directed=False)
        in_component = labels == labels[fault_idx]
        phase &= in_component[ends_a]
        node = np.full(n, -1, dtype=np.int64)
        others = np.flatnonzero(in_component & (np.arange(n) != fault_idx))
        node[others] = n + np.arange(others.size)
        node[fault_idx] = fault_idx
        size = n + others.size

        k = np.where(phase, z_mutual * y_shield, 0.0)
        y_pp = np.where(phase, y_red, 0.0)
        y_sp = -k * y_pp
        y_ss = y_shield + k * k * y_pp
        stamps = _CoupledStamps(n)
        stamps.pair(ends_a, ends_b, ends_a, ends_b, y_ss)
        p_a, p_b = np.where(phase, node[ends_a], 0), np.where(phase, node[ends_b], 0)
        stamps.pair(p_a, p_b, p_a, p_b, y_pp)
        stamps.pair(ends_a, ends_b, p_a, p_b, y_sp)
        stamps.pair(p_a, p_b, ends_a, ends_b, y_sp)

        rhs = np.zeros(size, dtype=complex)
        thevenin = self._coupled_sources(
            freq, set(sources_with_path), stamps, rhs, node, in_component, fault_idx
        )
        if not rhs.any():
            return None
        self._coupled_systems[freq] = (stamps, rhs, size)
        x = self.coupled_solution(freq, bus_admittances(tables.bus_z, tables.bus_present)[:, column])
        if x is None:
            del self._coupled_systems[freq]
            return None
        self.coupled_potentials[freq] = -x[:n]

        phase_potential = np.zeros(n, dtype=complex)
        phase_potential[in_component] = x[node[in_component]]
        i_phase = y_sp * (x[ends_a] - x[ends_b]) + np.where(
            phase, y_pp * (phase_potential[ends_a] - phase_potential[ends_b]), 0.0
        )
        currents = {name: 0.0 + 0.0j for name in self.network.branches}
        for b, value in zip(rows, i_phase.tolist(), strict=True):
            currents[tables.branch_names[b]] = value
        # The earth-side system carries the loop closure 1/Z_src between the
        # source bus and the fault bus (package frame u = -x). The injection
        # that makes its effective source current the coupled loop current
        # is I_loop + Y_src (u_S - u_F).
        loops = {}
        for name, (p, e, norton, y_src) in thevenin.items():
            loop = complex(norton - y_src * (x[p] - x[e]))
            loops[name] = (loop, loop + y_src * complex(x[fault_idx] - x[e]))
        return currents, loops

    def _coupled_sources(
        self, freq: float, feeding: set, stamps: _CoupledStamps, rhs: np.ndarray,
        node: np.ndarray, in_component: np.ndarray, fault_idx: int,
    ) -> Dict[str, Tuple[int, int, complex, complex]]:
        """
        Stamp the sources into the coupled system of :meth:`_solve_coupled`.

        A current source drives its current from its earth node into its
        phase node; a Thevenin source is ``E/Z_src`` with ``1/Z_src`` between
        the two. A source outside the phase component feeds no phase
        current: its current is injected between its earth node and the
        fault's, as in the earth-side system (``SOURCE_WITHOUT_PHASE_PATH``).
        Every Thevenin source closes its loop, as in the earth-side matrix
        (:meth:`thevenin_loop_admittances`), fed or not.

        Parameters
        ----------
        freq : float
            Frequency in Hz.
        feeding : set of str
            Sources that reach the active fault.
        stamps : _CoupledStamps
            The system, extended in place.
        rhs : numpy.ndarray
            Its right-hand side, extended in place.
        node : numpy.ndarray
            Phase node per bus (the fault's is its earth node; ``-1`` outside
            the component).
        in_component : numpy.ndarray
            Buses of the phase component of the fault bus.
        fault_idx : int
            Row of the fault bus.

        Returns
        -------
        dict
            Per Thevenin source in the component: ``(phase node, earth node,
            Norton current, 1/Z_src)``.
        """
        scaling = self.network.faults[self.network.active_fault].scaling_at(freq)
        thevenin: Dict[str, Tuple[int, int, complex, complex]] = {}
        for name, source in self.network.sources.items():
            bus = self.bus_indices.get(source.bus)
            if bus is None:
                continue
            phase_node = int(node[bus]) if in_component[bus] else fault_idx
            y_src = self._source_admittance(source, freq) if bus != fault_idx else None
            if y_src is not None:
                stamps.pair(np.array([phase_node]), np.array([bus]),
                            np.array([phase_node]), np.array([bus]), np.array([y_src]))
            norton = self._source_injection(source, freq, scaling) if name in feeding else None
            if norton is None:
                continue
            rhs[phase_node] += norton
            rhs[bus] -= norton
            if y_src is not None and in_component[bus]:
                thevenin[name] = (phase_node, int(bus), norton, y_src)
        return thevenin

    def _compute_phase_currents_one_way(
        self, freq: float, sources_with_path
    ) -> Dict[str, complex]:
        """
        The phase split of the phase-only network with ``Z_red`` (one-way coupling).

        The fallback of :meth:`_compute_phase_currents_auto` where the coupled
        system is singular, and the place where the proxy and the sources
        without phase path are reported.

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
        **One factorisation, all sources at once.** The reduced phase network
        is a complex Laplacian with the fault bus as reference: sparse, with
        about ``2m + n`` entries, and the *same* matrix for every source. A
        dense ``n x n`` array solved with :func:`numpy.linalg.solve` once per
        source and frequency would be the dominant cost of a run and the one
        that grows fastest with the network (measured: 0.60 s of 1.06 s on a
        1000-bus ring at seven frequencies, 1.29 s of 1.66 s on a 1000-bus
        radial feeder with three sources, against 0.011 s and 0.010 s for the
        sparse form). It is therefore assembled straight into CSC, factorised
        once per frequency with :func:`scipy.sparse.linalg.splu` and solved
        for a right-hand side with one column per source; the branch currents
        are one vectorised gather. Superposition runs source by source, and
        the stamps are accumulated in the program order of an
        element-by-element dense assembly, so the reduced matrix differs from
        the dense one only by the last-bit rounding of NumPy's complex
        division.

        The galvanic component of the fault bus is found with
        :func:`scipy.sparse.csgraph.connected_components`. A source without a
        phase-conductor connection is recorded and reported once per run, a
        singular phase network raises :class:`RuntimeError`, and
        :attr:`phase_currents` is a dictionary over all branches. A phase
        network with a non-finite admittance is solved densely, so a NaN
        propagates instead of being reported as a singular factor.
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
        # makes the reduced matrix singular: the solve would raise and the
        # entire source contribution would be lost.
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
                dict(zip((names[b] for b in rows), y.tolist(), strict=True)),
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
                # them. That is a legitimate result -- but it is easily mistaken
                # for a solved contribution, so it is named out loud, once per
                # source and run (see _construct_vectors).
                self._sources_without_phase_path.setdefault(source_name, []).append(
                    freq
                )
                continue

            if factor is None:
                # Factorised when the first source needs it, so a singular
                # network raises at the first source that would use it.
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
        for name, value in zip((names[b] for b in rows), total.tolist(), strict=True):
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
        counterpart of ``Y_phase[np.ix_(keep, keep)]`` of a dense assembly,
        with the same additions in the same order.

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
        The proxy does not open the phase conductor of a broken shield, so
        only a *declared* open phase conductor (``phase_impedance_formula
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
        *negated* value so that it combines with the branch-current formula
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
        Evaluated on the gathered arrays: the coupled branches -- active,
        grounding conductor, both ends active, self and mutual impedance
        stored at this frequency, shield not open -- are selected with one
        mask, and the injections are added with :func:`numpy.add.at` in
        branch order, which performs the same additions in the same order as
        an element-by-element loop.
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
            stored.update(zip((names[b] for b in coupled), values, strict=True))
        else:
            for b, value in zip(coupled, values, strict=True):
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

    def _floating_buses(self, freq: float, matrix) -> List[str]:
        """
        Buses whose group in the solved matrix has no path to remote earth.

        A group is the set of buses coupled by the off-diagonal entries of
        ``matrix`` -- shields with a non-zero admittance and the loop closures
        of voltage sources (:func:`component_labels`); it reaches remote
        earth if one of its buses has a finite, non-zero electrode admittance
        (:meth:`earthed_buses`).

        Parameters
        ----------
        freq : float
            Frequency in Hz.
        matrix : scipy.sparse.spmatrix
            The nodal matrix about to be solved.

        Returns
        -------
        list of str
            The buses of every group without an electrode, in
            :attr:`bus_indices` order; empty when every group is earthed.
        """
        labels = component_labels(matrix)
        earthed_groups = np.zeros(int(labels.max()) + 1, dtype=bool)
        earthed_groups[labels[self.earthed_buses(freq)]] = True
        names = list(self.bus_indices)
        return [names[i] for i in np.flatnonzero(~earthed_groups[labels])]

    def _floating_group_message(self, freq: float, floating: List[str]) -> str:
        """
        The diagnosis for buses connected to one another but not to earth.

        Parameters
        ----------
        freq : float
            Frequency (Hz) of the singular system.
        floating : list of str
            Output of :meth:`_floating_buses`.

        Returns
        -------
        str
            The full message for the :class:`ValueError`; it starts with
            "Singular" like the other messages of :meth:`solve_network`.
        """
        if len(floating) == 1:
            subject = (
                f"bus {floating[0]!r} has no finite grounding impedance and no "
                f"branch with a grounding conductor (or loop closure of a "
                f"voltage source) to an earthed bus, so it has"
            )
        else:
            subject = (
                f"buses {_shortlist([repr(name) for name in floating])} are "
                f"connected to one another -- through shields or the loop "
                f"closure of a voltage source -- but none of them has a finite "
                f"grounding impedance, so they have"
            )
        return (
            f"Singular admittance matrix at f={freq} Hz: the {subject} no path "
            f"to reference earth and an undefined potential. A linear solver "
            f"still returns numbers for a group of such buses (1e15 V and more, "
            f"from rounding), which is why it is refused here. Give one of them "
            f"an electrode, connect them to an earthed bus through a branch with "
            f"a grounding conductor, or deactivate them."
        )

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
            # The same question for every group of buses the matrix couples:
            # one group without an electrode is enough to make Y singular, and
            # once it holds two or more buses only up to rounding -- splu then
            # returns 1e18 V for it instead of raising (see component_labels).
            floating = self._floating_buses(freq, Y_matrix)
            if floating:
                raise ValueError(self._floating_group_message(freq, floating))
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
            # Where the coupled system was solved, its earth potentials are the
            # solution (the nodal system with the coupled phase currents gives
            # the same to rounding; the coupled one is what a study session
            # re-solves, so both report the same bits).
            self.u_vectors[freq] = self.coupled_potentials.get(freq, u_vector)
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
        self._electrode_currents = None
        electrode, usable = self._electrode_arrays(potentials)
        injection = self._stacked(self.source_injections)
        result.buses.extend(
            self._result_rows(
                ResultBus,
                list(self.bus_indices),
                frequencies,
                {"uepr": potentials, "ia": electrode, "i_inj": injection},
            )
        )
        self._touch_voltages(result.buses)

        # Store the result in the network's results dictionary
        self.network.results[fault_name] = result
        self.results = result  # Also keep a reference in self.results

    def _touch_voltages(self, result_buses: List[ResultBus]) -> None:
        """Scale the touch factors of each bus with its EPR.

        ``U_vT(f) = k_touch(f) |u(f)|`` and ``U_transfer(f) = k_transfer(f)
        |u(f)|``, with the RMS over the frequencies as for the EPR. Only
        buses whose earthing characteristic tabulates a factor get a value;
        every other bus keeps the defaults (``None`` and an empty mapping),
        so a network without characteristics produces the results it did
        before.

        Parameters
        ----------
        result_buses : list of ResultBus
            The bus results of this solve; changed in place.
        """
        buses = self.network.buses
        for result_bus in result_buses:
            bus = buses[result_bus.name]
            for field, factors in (("u_touch", bus.k_touch), ("u_transfer", bus.k_transfer)):
                if factors is None:
                    continue
                per_frequency = {
                    freq: factors[freq] * abs(complex(potential))
                    for freq, potential in result_bus.uepr_freq.items()
                }
                setattr(result_bus, field + "_freq", per_frequency)
                setattr(result_bus, field, math.sqrt(sum(v * v for v in per_frequency.values())))

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
        return stack_columns(
            [vectors.get(freq) for freq in self.network.frequencies],
            self.num_buses,
        )

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
            prepared.append(
                (key, rms_columns(values).tolist(), values.real.tolist(),
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
        r"""
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

        $$
        X_{\mathrm{RMS}} = \sqrt{\sum_f |X(f)|^2}
        $$

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
        r"""
        The reduction factor, the earth current and the earthing quantities of EN 50522.

        At the fault bus ``F``, per frequency:

        $$
        U_E = u_F, \qquad Z_E = Z_{dp}(F), \qquad
        I_E = \frac{U_E}{Z_E}, \qquad r = \frac{|I_E|}{|I_F|}
        $$

        with ``Z_dp`` the driving-point impedance of the earthing network at
        the fault bus (:meth:`_earthing_driving_points`) and ``I_F`` the
        fault current of the sources that reach the fault.

        Why this definition: the network seen from the fault bus is linear,
        so by Norton's theorem its potential is ``U_E = Z_E * I_N`` with
        ``I_N`` the current that flows into the fault bus's earthing when
        that earthing is ideal (``Z -> 0``). ``I_E = I_N`` is the earth
        current of the norm: the part of the fault current that does not
        return through shields and conductors -- induced or galvanic -- to
        the sources. The chain of the norm ``U_E = r * Z_E * I_F`` then holds
        exactly, with the ``Z_E`` a measurement at the station sees, and
        ``r`` needs no balanced state far from the fault. It does not depend
        on the fault-bus electrode, which the short circuit takes out; for a
        long line it tends to ``|1 - Z_m/Z_s|``, and for one cable from a
        source earthed with ``Z_A`` it is ``(Z_s - Z_m)/(Z_s + Z_A)``.

        Rejected: the sum of the electrode currents over a group of buses
        that "feed the soil" (it needs a split of the network that is a
        modelling choice and changes with the fault location), and the ratio
        of the fault-bus EPR with and without coupling (it treats the
        galvanic return of the fault current to a source inside the earthing
        network as earth current).

        ``r`` and ``I_E`` are ``None`` where the frequency carries no fault
        current and where ``Z_E`` is undefined (``EARTHING_IMPEDANCE_UNDEFINED``).
        A fault on the bus of every source that feeds it injects nothing:
        ``r = 0`` there, said by the info diagnostic ``FAULT_AT_FEEDING_BUS``.

        Notes
        -----
        Costs nothing beyond ``Z_E``: ``U_E`` is the solved potential, and
        ``Z_E`` is one triangular solve on the factorisation
        :meth:`solve_network` kept (with a voltage source, a factorisation of
        the earthing group alone).
        """
        fault_name = self.network.active_fault
        if fault_name is None:
            raise ValueError("No active fault set in the network.")

        fault_bus = self.network.faults[fault_name].bus
        fault_bus_idx = self.bus_indices[fault_bus]
        frequencies = self.network.frequencies

        z_driving_point, z_undefined = self._earthing_driving_points(fault_bus_idx)
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
        factors: Dict[float, Optional[float]] = {}
        earth_currents: Dict[float, Optional[complex]] = {}
        unexcited: List[float] = []
        for freq in frequencies:
            i_fault = fault_currents[freq]
            z_earthing = z_driving_point.get(freq)
            if z_earthing is None or i_fault is None or i_fault == 0:
                factors[freq] = None
                earth_currents[freq] = None
                continue
            i_earth = fault_potentials[freq] / z_earthing
            earth_currents[freq] = i_earth
            factors[freq] = abs(i_earth) / abs(i_fault)
            if not self._is_excited(freq):
                unexcited.append(freq)

        self._report_undefined_reduction_quantities(fault_name, fault_bus, z_undefined)
        if unexcited:
            note(
                ModelAssumptionWarning,
                f"Fault '{fault_name}' sits on the bus of every source that "
                f"feeds it at {render_frequencies(unexcited)}: the fault current "
                f"closes through that station back to the source, nothing is "
                f"injected into the earthing network, and U_E, the earth current "
                f"I_E and the reduction factor r are 0 there; Z_E is still the "
                f"driving-point impedance of the fault bus.",
                code="FAULT_AT_FEEDING_BUS",
                elements=[("fault", fault_name), ("bus", fault_bus)],
            )

        result = self.network.results[fault_name]
        result.reduction_factor = ResultReductionFactor(
            fault_bus=fault_bus,
            value=factors,
            i_fault=fault_currents,
            i_earth=earth_currents,
            u_earthing=fault_potentials,
            z_earthing=z_driving_point,
        )
        self.network.results[fault_name] = result
        self.results = result

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
    ) -> Tuple[Dict[float, Optional[complex]], Dict[float, List[str]]]:
        """
        Driving-point impedance of the earthing network at one bus, per frequency.

        ``Z_dp = (Y_E^-1)_bb`` with ``Y_E`` the earthing network alone: every
        electrode and shield as assembled (0 Hz substitutes included), no
        coupling, no Thevenin loop closure. Without a voltage source ``Y_E``
        *is* the solved matrix and the value costs one triangular solve on
        the factorisation ``solve_network`` kept. With one, the loop closures
        are taken out (:meth:`_earthing_admittance_sparse`), and whether the
        bus still reaches remote earth is decided by structure: the group of
        buses connected to it through shields must hold an electrode
        (:func:`component_labels`). If it does, only that group is
        factorised, so a floating group elsewhere -- the bus of a voltage
        source without electrode, say -- does not make the value undefined.
        If it does not, the value is ``None``: the matrix of such a group is
        singular only up to rounding, and a direct solve returns about
        4.5e15 Ohm for it.

        Parameters
        ----------
        bus_idx : int
            Row of the bus (the fault bus) in the nodal system.

        Returns
        -------
        tuple of (dict of float to complex or None, dict of float to list of str)
            The impedance per frequency, and for every frequency at which it
            is undefined the buses of the group around ``bus_idx`` -- empty
            when the group holds an electrode but its matrix is numerically
            singular.
        """
        values: Dict[float, Optional[complex]] = {}
        undefined: Dict[float, List[str]] = {}
        names = list(self.bus_indices)
        for freq in self.network.frequencies:
            if freq not in self.Y_sparse:
                values[freq] = None
                continue
            if not self.thevenin_loop_admittances(freq):
                # Y_E is the solved matrix, and solve_network has accepted it.
                values[freq] = self._solved_driving_point(freq, bus_idx)
                continue
            earthing = self._earthing_admittance_sparse(freq)
            labels = component_labels(earthing)
            group = labels == labels[bus_idx]
            if not np.any(group & self.earthed_buses(freq)):
                values[freq] = None
                undefined[freq] = [names[i] for i in np.flatnonzero(group)]
                continue
            columns = restricted_columns(earthing, group, [bus_idx])
            if columns is None:
                values[freq] = None
                undefined[freq] = []
                continue
            values[freq] = complex(columns[bus_idx, 0])
        return values, undefined

    def _report_undefined_reduction_quantities(
        self,
        fault_name: str,
        fault_bus: str,
        z_undefined: Dict[float, List[str]],
    ) -> None:
        """
        Say once per run where ``Z_E`` -- and with it ``r`` and ``I_E`` -- is ``None``.

        Either the fault bus's group in the earthing network -- the buses
        connected to it through shields -- holds no electrode, so it reaches
        remote earth only through the loop closure of a voltage source and the
        earthing network alone presents an infinite impedance there (decided
        by structure, see :meth:`_earthing_driving_points`), or the group's
        matrix is numerically singular. Collected over all frequencies so
        that a sweep over fifty harmonics produces one message per condition,
        not fifty.

        Parameters
        ----------
        fault_name : str
            The active fault.
        fault_bus : str
            Its bus.
        z_undefined : dict of float to list of str
            Frequencies at which ``Z_E`` is undefined, with the buses of the
            electrode-less group around the fault bus (an empty list where the
            group holds an electrode but its matrix is numerically singular).
        """
        elements = [("fault", fault_name), ("bus", fault_bus)]
        floating = {f: buses for f, buses in z_undefined.items() if buses}
        singular = sorted(f for f, buses in z_undefined.items() if not buses)
        if floating:
            group: List[str] = []
            for buses in floating.values():
                group.extend(b for b in buses if b not in group)
            others = [b for b in group if b != fault_bus]
            connected = (
                f" and the buses connected to it through shields "
                f"({_shortlist(others)})" if others else ""
            )
            emit(
                ModelAssumptionWarning,
                f"Fault '{fault_name}': at "
                f"{render_frequencies(sorted(floating))} the fault bus "
                f"'{fault_bus}'{connected} carry no electrode, so the fault "
                f"side reaches remote earth only through the loop closure of a "
                f"voltage source. The earthing network alone presents an "
                f"infinite impedance there, so the earthing impedance Z_E (the "
                f"driving-point impedance of the earthing network at the fault "
                f"bus), the earth current and the reduction factor are reported "
                f"as None.",
                code="EARTHING_IMPEDANCE_UNDEFINED",
                elements=elements + [("bus", b) for b in others],
                stacklevel=4,
            )
        if singular:
            emit(
                ModelAssumptionWarning,
                f"Fault '{fault_name}': at {render_frequencies(singular)} the "
                f"earthing network around the fault bus '{fault_bus}' is "
                f"numerically singular although it holds an electrode, so the "
                f"earthing impedance Z_E, the earth current and the reduction "
                f"factor are reported as None.",
                code="EARTHING_IMPEDANCE_UNDEFINED",
                elements=elements,
                stacklevel=4,
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
        copies. With ``U_E`` the EPR at the fault bus the norm's chain
        ``U_E = r * I_F * Z_E`` holds exactly, because ``r`` is defined through
        it (:meth:`compute_reduction_factors`); the check of ``r`` is the
        short circuit of the fault bus, not this identity.
        ``docs/explanation/reduction-factors.md`` and
        ``docs/api/electrical_network.md`` state the same definition.

        The results are stored in the network's results object.
        """
        fault_name = self.network.active_fault
        if fault_name is None:
            raise ValueError("No active fault set in the network.")

        fault_bus = self.network.faults[fault_name].bus

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

            # The fault bus reaches remote earth only through a Thevenin loop
            # (no bus connected to it through shields carries an electrode):
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

# tests/_dense_oracle.py

"""
Independent dense reference for the stationary solver (audit pass 19).

The sparse kernel of pass 19 (findings P1, P2/P6, P7) rewrites how the nodal
system and the phase-conductor network are assembled and solved. A rewrite
like that can only be accepted against a second implementation that shares
none of its machinery, so this module rebuilds the stationary model from the
*public* data of a :class:`~groundinsight.Network` -- the impedance
dictionaries, the ``active`` flags, the sources and the fault -- with plain
Python loops, assembles every matrix densely and solves it with
:func:`numpy.linalg.solve`. It does not call a single method of
``ElectricalNetwork``.

What it shares with the package is deliberately limited to model *rules*
that are not part of the kernel and are tested on their own:
:meth:`Network.sources_reaching` (which sources feed the fault),
:func:`is_short_circuit` / :func:`dc_substitute_impedance` (how a 0 Hz short is
replaced) and, for the earth-group quantities only,
:func:`split_earth_currents` / :func:`earthing_voltage` (how the electrode
currents are split into the group that feeds the soil). Everything the kernel
computes -- admittances, stamps, the phase-network solve, the Norton coupling
terms, the nodal solves, electrode and shield currents -- is recomputed here.

Two switches serve finding P13, the accuracy question for large networks:

``refine``
    Steps of iterative refinement with the residual evaluated in
    ``numpy.clongdouble`` for every linear solve (phase network and nodal
    system). The phase network of a long ring is a Laplacian with condition
    number of order ``n**2``, so for a 2000-bus ring *any* float64 kernel is
    only good to roughly 1e-11; a refined solution is the exact solution of
    the stored float64 system to working precision and is the honest
    reference for size checks, not another float64 kernel.
``division``
    ``"python"`` (default) computes admittances with Python's complex
    division, which rounds differently from NumPy's in about a quarter of all
    values -- the battery then compares two genuinely different roundings.
    ``"numpy"`` uses array division like the kernel and accumulates the stamps
    in the same program order, so the stored system is bit-for-bit the
    kernel's. A refined reference must be built that way: a one-ulp change in
    the data moves the exact solution of a badly conditioned system by as much
    as the rounding error being measured.
``sparse``
    Assemble and factorise with SciPy instead of NumPy (the stamps and their
    order are the same), for the large networks where a dense ``n x n``
    matrix per frequency is too expensive. Combined with ``refine`` the result
    is still the refined exact solution; the factorisation only drives the
    correction steps.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

import numpy as np
import scipy.sparse as sp
from scipy.sparse.linalg import splu

from groundinsight.utils.earth_current import earthing_voltage, split_earth_currents
from groundinsight.utils.impedance_calculator import (
    dc_substitute_impedance,
    is_short_circuit,
)


@dataclass
class OracleSolution:
    """Every physical quantity of one stationary solve, per frequency.

    Attributes
    ----------
    frequencies : list of float
        Network frequencies, in network order.
    bus_names : list of str
        Active buses in network insertion order -- the row order of the
        package's results.
    branch_names : list of str
        All branches in network insertion order.
    u, u_no_mutual : numpy.ndarray
        Bus potentials (EPR) with and without the mutual Norton injections,
        shape ``(n_bus, F)``.
    ia : numpy.ndarray
        Electrode current per bus (``u / Z_bus``, zero for an open end).
    i_inj : numpy.ndarray
        Source-only nodal injection per bus.
    i_vector : numpy.ndarray
        Full nodal injection per bus: sources plus the mutual Norton terms.
    i_shield : numpy.ndarray
        Shield current per branch, oriented like ``ResultBranch.i_s_freq``.
    i_phase : numpy.ndarray
        Phase current per branch (``from -> to`` positive).
    i_fault : numpy.ndarray
        ``Sum(scaling * I_src)`` over the sources reaching the fault, ``(F,)``.
    z_dp : numpy.ndarray
        Driving-point impedance of the earthing network at the fault bus
        (unit injection, no sources, no mutual terms, no Thevenin loop
        closures), ``(F,)``.
    Y : dict of float to numpy.ndarray
        The dense nodal admittance matrix per frequency (only for
        ``sparse=False``).
    electrode : list of dict
        Per frequency, ``{bus: I_a}`` for every bus with a finite, non-open
        grounding impedance -- the input of the earth-current split.
    dc_substitute : float or None
        The 0 Hz stand-in impedance, when the network has a short there. Its
        bin is deliberately ill-conditioned (the stand-in is ``sqrt(eps)``
        times the smallest other impedance), see finding P13.
    """

    frequencies: List[float]
    bus_names: List[str]
    branch_names: List[str]
    u: np.ndarray
    u_no_mutual: np.ndarray
    ia: np.ndarray
    i_inj: np.ndarray
    i_vector: np.ndarray
    i_shield: np.ndarray
    i_phase: np.ndarray
    i_fault: np.ndarray
    z_dp: np.ndarray
    Y: Dict[float, np.ndarray] = field(default_factory=dict)
    electrode: List[Dict[str, complex]] = field(default_factory=list)
    dc_substitute: Optional[float] = None


def _is_open(z: complex) -> bool:
    """``True`` for the open-end sentinel (an infinite component).

    Parameters
    ----------
    z : complex
        An impedance.

    Returns
    -------
    bool
        ``True`` when either part is infinite; ``NaN`` is not open.
    """
    return math.isinf(z.real) or math.isinf(z.imag)


def _dc_substitute(network, index: Dict[str, int]) -> Optional[float]:
    """The 0 Hz stand-in for shorted elements, sized by the package rule.

    Scans exactly what is inverted in the nodal matrix: active buses, the
    self impedance of active grounding-conductor branches between active
    buses, and the source impedance of voltage sources on an active bus.

    Parameters
    ----------
    network : Network
        The network.
    index : dict
        Active bus name to row.

    Returns
    -------
    float or None
        The substitute impedance, or ``None`` when nothing is shorted.
    """
    shorted: List[str] = []
    magnitudes: List[float] = []

    def classify(z, label):
        if z is None:
            return
        value = complex(z.real, z.imag)
        if is_short_circuit(value):
            shorted.append(label)
        else:
            magnitudes.append(abs(value))

    for name, bus in network.buses.items():
        if bus.active:
            classify(bus.impedance.get(0.0), name)
    for branch in network.branches.values():
        if (
            branch.active
            and branch.type.grounding_conductor
            and branch.from_bus in index
            and branch.to_bus in index
        ):
            classify(branch.self_impedance.get(0.0), branch.name)
    for source in network.sources.values():
        if source.source_type == "voltage" and source.bus in index:
            classify((source.source_impedance or {}).get(0.0), source.name)
    if not shorted:
        return None
    return dc_substitute_impedance(magnitudes, shorted, context="oracle")


class _Assembler:
    """Collect stamps in program order and turn them into one matrix.

    The order matters only for bit-for-bit reproduction (``division="numpy"``):
    both paths below add the contributions to each entry in exactly the order
    they were stamped, starting from zero, as the historic dense loops did.
    """

    def __init__(self, size: int):
        self.size = size
        self.rows: List[int] = []
        self.cols: List[int] = []
        self.vals: List[complex] = []

    def stamp(self, r: int, c: int, v: complex) -> None:
        """Record one contribution ``Y[r, c] += v``.

        Parameters
        ----------
        r, c : int
            Row and column.
        v : complex
            The contribution.
        """
        self.rows.append(r)
        self.cols.append(c)
        self.vals.append(v)

    def branch(self, a: int, b: int, y: complex) -> None:
        """The four stamps of an admittance between ``a`` and ``b``.

        Parameters
        ----------
        a, b : int
            The two nodes, in the order of the former dense loop
            (``(a, b)``, ``(b, a)``, ``(a, a)``, ``(b, b)``).
        y : complex
            The admittance.
        """
        self.stamp(a, b, -y)
        self.stamp(b, a, -y)
        self.stamp(a, a, y)
        self.stamp(b, b, y)

    def dense(self) -> np.ndarray:
        """The dense matrix, each entry summed in stamp order.

        Returns
        -------
        numpy.ndarray
            Complex ``(size, size)`` array.
        """
        matrix = np.zeros((self.size, self.size), dtype=complex)
        if self.vals:
            np.add.at(
                matrix,
                (np.asarray(self.rows), np.asarray(self.cols)),
                np.asarray(self.vals, dtype=complex),
            )
        return matrix

    def sparse(self) -> sp.csc_matrix:
        """The same matrix as CSC, entries summed in stamp order, zeros dropped.

        Returns
        -------
        scipy.sparse.csc_matrix
            Complex ``(size, size)`` matrix.
        """
        n = self.size
        if not self.vals:
            return sp.csc_matrix((n, n), dtype=complex)
        rows = np.asarray(self.rows, dtype=np.int64)
        cols = np.asarray(self.cols, dtype=np.int64)
        vals = np.asarray(self.vals, dtype=complex)
        keys, inverse = np.unique(cols * n + rows, return_inverse=True)
        data = np.empty(keys.size, dtype=complex)
        data.real = np.bincount(inverse, weights=vals.real, minlength=keys.size)
        data.imag = np.bincount(inverse, weights=vals.imag, minlength=keys.size)
        matrix = sp.csc_matrix((data, (keys % n, keys // n)), shape=(n, n))
        matrix.eliminate_zeros()
        return matrix


def _solve(matrix, rhs: np.ndarray, refine: int):
    """Solve ``matrix @ x = rhs``, optionally refined in extended precision.

    The base solve runs in float64 (``rhs`` is rounded to ``complex`` for it);
    each refinement step evaluates the residual against the *unrounded*
    ``rhs`` in ``numpy.clongdouble`` and solves for the correction in float64.
    Returns ``numpy.clongdouble`` values when ``refine > 0`` so that the
    quantities derived from the potentials (differences across a branch) do
    not lose the digits the refinement bought back.

    Parameters
    ----------
    matrix : numpy.ndarray or scipy.sparse matrix
        The system matrix (float64 complex).
    rhs : numpy.ndarray
        Right-hand side, ``(n,)`` or ``(n, k)``; may be ``clongdouble``.
    refine : int
        Number of refinement steps (0: plain float64 solve).

    Returns
    -------
    numpy.ndarray
        The solution, ``complex`` or ``clongdouble``.

    Raises
    ------
    numpy.linalg.LinAlgError or RuntimeError
        If the matrix is singular (dense or sparse factorisation).
    """
    if sp.issparse(matrix):
        lu = splu(sp.csc_matrix(matrix))
        base = lu.solve
    else:
        base = lambda b: np.linalg.solve(matrix, b)  # noqa: E731
    x = base(np.asarray(rhs).astype(complex))
    if not refine:
        return x
    a_l = (
        matrix.astype(np.clongdouble)
        if sp.issparse(matrix)
        else np.asarray(matrix, dtype=np.clongdouble)
    )
    b_l = np.asarray(rhs, dtype=np.clongdouble)
    x_l = np.asarray(x, dtype=np.clongdouble)
    for _ in range(refine):
        residual = b_l - a_l @ x_l
        x_l = x_l + np.asarray(base(residual.astype(complex)), dtype=np.clongdouble)
    return x_l


def _reciprocals(values: List[complex], division: str) -> List[complex]:
    """``1/z`` for every value, with the requested rounding.

    Parameters
    ----------
    values : list of complex
        Impedances.
    division : {"python", "numpy"}
        Python's complex division or NumPy's array division.

    Returns
    -------
    list of complex
        The admittances, in the same order.

    Raises
    ------
    ZeroDivisionError
        For an exact zero with ``division="python"``.
    """
    if division == "numpy":
        if not values:
            return []
        return (1.0 / np.asarray(values, dtype=complex)).tolist()
    return [1.0 / z for z in values]


def _quotient(a: complex, b: complex, division: str) -> complex:
    """``a/b`` with the requested rounding (see the module docstring).

    Parameters
    ----------
    a, b : complex
        Numerator and denominator.
    division : {"python", "numpy"}
        Python's complex division or NumPy's array division.

    Returns
    -------
    complex
        The quotient.
    """
    if division == "numpy":
        quotient = np.asarray([a], dtype=complex) / np.asarray([b], dtype=complex)
        return complex(quotient[0])
    return a / b


class _Model:
    """Topology of one fault, and the per-frequency nodal assembly.

    Shared by :func:`solve` and :func:`assemble`, so that the matrices the
    assembly test compares are the ones the oracle solves.

    Parameters
    ----------
    network : Network
        The network.
    fault : str
        The fault; its bus is the reference of the phase network and the end
        of every Thevenin loop closure.
    """

    def __init__(self, network, fault: str):
        self.network = network
        self.frequencies = [float(f) for f in network.frequencies]
        self.names = [name for name, bus in network.buses.items() if bus.active]
        self.index = {name: k for k, name in enumerate(self.names)}
        self.n = len(self.names)
        self.k_fault = self.index[network.faults[fault].bus]
        self.branches = list(network.branches.values())
        self.in_net = [
            b.active and b.from_bus in self.index and b.to_bus in self.index
            for b in self.branches
        ]
        self.vsources = [
            s for s in network.sources.values() if s.source_type == "voltage"
        ]
        self.dc_sub = (
            _dc_substitute(network, self.index) if 0.0 in self.frequencies else None
        )

    def resolver(self, f: float):
        """``value -> complex`` with the 0 Hz stand-in applied (or ``None``).

        Parameters
        ----------
        f : float
            The frequency the values belong to.

        Returns
        -------
        callable
            Maps a stored value to ``complex`` -- at 0 Hz a short circuit to
            the stand-in -- and ``None`` to ``None``.
        """
        sub = self.dc_sub if f == 0.0 else None

        def resolved(z) -> Optional[complex]:
            if z is None:
                return None
            value = complex(z.real, z.imag)
            if sub is not None and is_short_circuit(value):
                return complex(sub, 0.0)
            return value

        return resolved

    def nodal(self, f: float, division: str):
        """Stamps of the nodal matrix at ``f``.

        Parameters
        ----------
        f : float
            The frequency.
        division : {"python", "numpy"}
            Rounding of the admittances.

        Returns
        -------
        tuple
            ``(bus_z, grid, y_grid, grid_only, full)``: the resolved bus
            impedances, the grounding branches ``(branch, from, to, Z)`` that
            carry an admittance, their admittances, and two assemblers -- the
            earthing network alone and with the Thevenin loop closures.
        """
        resolved = self.resolver(f)
        index, k_fault = self.index, self.k_fault
        bus_z = [
            resolved(self.network.buses[name].impedance.get(f)) for name in self.names
        ]
        grid: List[Tuple[int, int, int, complex]] = []
        for b, branch in enumerate(self.branches):
            if not (self.in_net[b] and branch.type.grounding_conductor):
                continue
            z = resolved(branch.self_impedance.get(f))
            if z is None or _is_open(z):
                continue
            grid.append((b, index[branch.from_bus], index[branch.to_bus], z))
        loops: List[Tuple[int, complex]] = []
        for source in self.vsources:
            s_idx = index.get(source.bus)
            if s_idx is None or s_idx == k_fault:
                continue
            z = resolved((source.source_impedance or {}).get(f))
            if z is None or _is_open(z):
                continue
            loops.append((s_idx, z))

        usable_bus = [
            (k, z) for k, z in enumerate(bus_z) if z is not None and not _is_open(z)
        ]
        y_bus = _reciprocals([z for _, z in usable_bus], division)
        y_grid = _reciprocals([g[3] for g in grid], division)
        y_loop = _reciprocals([z for _, z in loops], division)

        grid_only = _Assembler(self.n)
        for (k, _), y in zip(usable_bus, y_bus, strict=True):
            grid_only.stamp(k, k, y)
        for (_, a, c, _), y in zip(grid, y_grid, strict=True):
            grid_only.branch(a, c, y)
        full = _Assembler(self.n)
        full.rows, full.cols, full.vals = (
            list(grid_only.rows), list(grid_only.cols), list(grid_only.vals)
        )
        for (s_idx, _), y in zip(loops, y_loop, strict=True):
            full.stamp(s_idx, s_idx, y)
            full.stamp(k_fault, k_fault, y)
            full.stamp(s_idx, k_fault, -y)
            full.stamp(k_fault, s_idx, -y)
        return bus_z, grid, y_grid, grid_only, full


def assemble(network, fault: str, *, division: str = "python") -> Dict[float, np.ndarray]:
    """The dense nodal matrix per frequency, without solving anything.

    Parameters
    ----------
    network : Network
        The network.
    fault : str
        The fault whose bus closes the Thevenin loops.
    division : {"python", "numpy"}, optional
        Rounding of the admittances, see the module docstring.

    Returns
    -------
    dict of float to numpy.ndarray
        Frequency to dense ``Y``; also for a network whose system is singular.
    """
    model = _Model(network, fault)
    return {f: model.nodal(f, division)[4].dense() for f in model.frequencies}


def solve(
    network,
    fault: str,
    *,
    refine: int = 0,
    division: str = "python",
    sparse: bool = False,
) -> OracleSolution:
    """Solve one fault from the public model data alone.

    Parameters
    ----------
    network : Network
        The network; its impedance dictionaries must be evaluated.
    fault : str
        Name of the fault to solve.
    refine : int, optional
        Iterative-refinement steps with a ``clongdouble`` residual.
    division : {"python", "numpy"}, optional
        Rounding of the admittances, see the module docstring.
    sparse : bool, optional
        Assemble and factorise with SciPy instead of dense NumPy.

    Returns
    -------
    OracleSolution
        All physical quantities of the solve.

    Notes
    -----
    Phase-current rules (``phase_current_mode="auto"``): a branch that is
    active between two active buses carries ``1/Z_phase`` when its type
    declares a phase impedance at that frequency, otherwise the proxy
    ``1/Z_self`` (intact grounding conductor) or ``1/length`` (none, or a
    broken one with an infinite ``Z_self``). The fault bus
    is the reference, only its galvanic component (branches with non-zero
    admittance) is solved, and a source outside it contributes no phase
    current.
    """
    model = _Model(network, fault)
    freqs = model.frequencies
    fault_obj = network.faults[fault]
    names, index, n = model.names, model.index, model.n
    k_fault = model.k_fault
    branches, in_net = model.branches, model.in_net
    m = len(branches)
    reaching = network.sources_reaching(fault)
    dc_sub = model.dc_sub

    nF = len(freqs)
    dtype = np.clongdouble if refine else complex
    U = np.zeros((n, nF), dtype=dtype)
    U_nm = np.zeros((n, nF), dtype=dtype)
    IA = np.zeros((n, nF), dtype=dtype)
    INJ = np.zeros((n, nF), dtype=complex)
    IVEC = np.zeros((n, nF), dtype=dtype)
    IS = np.zeros((m, nF), dtype=dtype)
    IP = np.zeros((m, nF), dtype=dtype)
    IF = np.zeros(nF, dtype=complex)
    ZDP = np.zeros(nF, dtype=complex)
    Ys: Dict[float, np.ndarray] = {}
    electrode: List[Dict[str, complex]] = []

    for j, f in enumerate(freqs):
        resolved = model.resolver(f)
        scaling = fault_obj.scaling_at(f)

        def injection(source, f=f, scaling=scaling, resolved=resolved) -> Optional[complex]:
            if source.source_type == "current":
                value = (source.values or {}).get(f)
                if value is None:
                    return None
                return scaling * complex(value.real, value.imag)
            if source.voltage is None or source.source_impedance is None:
                return None
            u_emf = source.voltage.get(f)
            z_src = resolved(source.source_impedance.get(f))
            if u_emf is None or z_src is None or _is_open(z_src):
                return None
            return scaling * complex(u_emf.real, u_emf.imag) / z_src

        # --- nodal admittance: buses, grounding branches, Thevenin closures --
        bus_z, grid, y_grid, grid_only, full = model.nodal(f, division)
        Y = full.sparse() if sparse else full.dense()
        if not sparse:
            Ys[f] = Y

        # --- source injections -------------------------------------------
        i_src = np.zeros(n, dtype=complex)
        total = 0j
        for name in reaching:
            source = network.sources[name]
            if source.bus not in index:
                continue
            inj = injection(source)
            if inj is None:
                continue
            total += inj
            i_src[index[source.bus]] += inj
        i_src[k_fault] -= total
        INJ[:, j] = i_src
        IF[j] = total

        # --- phase-conductor network -------------------------------------
        y_phase: Dict[int, complex] = {}
        raw: List[Tuple[int, complex]] = []
        for b, branch in enumerate(branches):
            if not in_net[b]:
                continue
            z_phase = None
            if branch.phase_impedance is not None:
                z_phase = resolved(branch.phase_impedance.get(f))
            if z_phase is not None:
                if _is_open(z_phase):
                    y_phase[b] = 0j
                else:
                    raw.append((b, z_phase))
            else:
                z_self = resolved(branch.self_impedance.get(f))
                if (
                    branch.type.grounding_conductor
                    and z_self is not None
                    and not _is_open(z_self)
                ):
                    raw.append((b, z_self))
                else:
                    # No grounding conductor, or a broken one: an infinite
                    # Z_self opens the shield, not the phase conductor (PH3).
                    length = (
                        branch.length if branch.length and branch.length > 0 else 1.0
                    )
                    y_phase[b] = complex(1.0 / length, 0.0)
        for (b, _), y in zip(raw, _reciprocals([z for _, z in raw], division), strict=True):
            y_phase[b] = y
        order = sorted(y_phase)  # network order of the branches

        adjacency: Dict[int, List[int]] = {}
        for b in order:
            if y_phase[b] != 0:
                a = index[branches[b].from_bus]
                c = index[branches[b].to_bus]
                adjacency.setdefault(a, []).append(c)
                adjacency.setdefault(c, []).append(a)
        component = {k_fault}
        stack = [k_fault]
        while stack:
            for nb in adjacency.get(stack.pop(), ()):
                if nb not in component:
                    component.add(nb)
                    stack.append(nb)
        keep = sorted(component - {k_fault})
        pos = {bus: p for p, bus in enumerate(keep)}

        columns: List[Tuple[int, complex]] = []
        for name in reaching:
            source = network.sources[name]
            s_idx = index.get(source.bus)
            if s_idx is None or s_idx == k_fault:
                continue
            inj = injection(source)
            if inj is None or s_idx not in pos:
                continue
            columns.append((pos[s_idx], inj))

        ip = np.zeros(m, dtype=dtype)
        if columns:
            phase = _Assembler(len(keep))
            for b in order:
                a = pos.get(index[branches[b].from_bus])
                c = pos.get(index[branches[b].to_bus])
                y = y_phase[b]
                # The fault bus row and column are dropped (reference node).
                if a is not None and c is not None:
                    phase.branch(a, c, y)
                elif a is not None:
                    phase.stamp(a, a, y)
                elif c is not None:
                    phase.stamp(c, c, y)
            matrix = phase.sparse() if sparse else phase.dense()
            rhs = np.zeros((len(keep), len(columns)), dtype=complex)
            for col, (p, inj) in enumerate(columns):
                rhs[p, col] = inj
            potentials = _solve(matrix, rhs, refine)
            full_u = np.zeros((n, len(columns)), dtype=dtype)
            full_u[keep] = potentials
            for col in range(len(columns)):
                for b in order:
                    a = index[branches[b].from_bus]
                    c = index[branches[b].to_bus]
                    ip[b] += (full_u[a, col] - full_u[c, col]) * np.asarray(
                        y_phase[b], dtype=dtype
                    )
        IP[:, j] = ip

        # --- mutual Norton injections ------------------------------------
        i_vec = i_src.astype(dtype)
        stored = np.zeros(m, dtype=dtype)
        for b, a, c, z_self in grid:
            z_mut = branches[b].mutual_impedance.get(f)
            if z_mut is None:
                continue
            ratio = _quotient(complex(z_mut.real, z_mut.imag), z_self, division)
            i_mut = ip[b] * np.asarray(ratio, dtype=dtype)
            i_vec[a] -= i_mut
            i_vec[c] += i_mut
            stored[b] = -i_mut

        IVEC[:, j] = i_vec

        # --- nodal solves --------------------------------------------------
        u = _solve(Y, i_vec, refine)
        u_nm = _solve(Y, i_src, refine)
        U[:, j] = u
        U_nm[:, j] = u_nm

        ia = np.zeros(n, dtype=dtype)
        currents: Dict[str, complex] = {}
        for k, z in enumerate(bus_z):
            if z is None or _is_open(z):
                continue
            ia[k] = u[k] / np.asarray(z, dtype=dtype)
            currents[names[k]] = complex(ia[k])
        IA[:, j] = ia
        electrode.append(currents)

        i_shield = np.zeros(m, dtype=dtype)
        for (b, a, c, _), y in zip(grid, y_grid, strict=True):
            i_shield[b] = (u[c] - u[a]) * np.asarray(y, dtype=dtype) + stored[b]
        IS[:, j] = i_shield

        # --- driving point of the earthing network (no loop closures) ------
        unit = np.zeros(n, dtype=complex)
        unit[k_fault] = 1.0
        Yg = grid_only.sparse() if sparse else grid_only.dense()
        try:
            ZDP[j] = complex(np.asarray(_solve(Yg, unit, 0))[k_fault])
        except (np.linalg.LinAlgError, RuntimeError):
            ZDP[j] = complex(np.nan, np.nan)

    def _c(a):
        return np.asarray(a, dtype=complex)

    return OracleSolution(
        frequencies=freqs,
        bus_names=names,
        branch_names=[b.name for b in branches],
        u=_c(U),
        u_no_mutual=_c(U_nm),
        ia=_c(IA),
        i_inj=INJ,
        i_vector=_c(IVEC),
        i_shield=_c(IS),
        i_phase=_c(IP),
        i_fault=IF,
        z_dp=ZDP,
        Y=Ys,
        electrode=electrode,
        dc_substitute=dc_sub,
    )


# ---------------------------------------------------------------------------
# comparison with a solved network
# ---------------------------------------------------------------------------


def _cn(value) -> complex:
    """A ``ComplexNumber`` (or ``None``) as ``complex`` (NaN for ``None``).

    Parameters
    ----------
    value : ComplexNumber or None
        A stored result value.

    Returns
    -------
    complex
        The value, or ``nan + nan*j``.
    """
    if value is None:
        return complex(np.nan, np.nan)
    return complex(value.real, value.imag)


def package_arrays(network, fault: str) -> Dict[str, np.ndarray]:
    """Read the stored result of ``fault`` into arrays aligned with the oracle.

    Parameters
    ----------
    network : Network
        A network solved by ``run_fault``.
    fault : str
        The solved fault.

    Returns
    -------
    dict
        ``u``, ``ia``, ``i_inj``, ``i_shield``, ``i_phase`` as ``(elements, F)``
        arrays; the RMS values ``u_rms``, ``ia_rms``, ``i_shield_rms`` per
        element; and the lists ``bus_names`` / ``branch_names`` for the
        alignment check.
    """
    result = network.results[fault]
    freqs = [float(f) for f in network.frequencies]
    out = {
        "u": np.array([[_cn(b.uepr_freq[f]) for f in freqs] for b in result.buses]),
        "ia": np.array([[_cn(b.ia_freq[f]) for f in freqs] for b in result.buses]),
        "i_inj": np.array(
            [[_cn(b.i_inj_freq[f]) for f in freqs] for b in result.buses]
        ),
        "u_rms": np.array([b.uepr for b in result.buses]),
        "ia_rms": np.array([b.ia for b in result.buses]),
        "i_shield": np.array(
            [[_cn(b.i_s_freq[f]) for f in freqs] for b in result.branches]
        ),
        "i_shield_rms": np.array([b.i_s for b in result.branches]),
        "bus_names": [b.name for b in result.buses],
        "branch_names": [b.name for b in result.branches],
    }
    en = network.electrical_network
    out["i_phase"] = np.array(
        [[complex(en.phase_currents[f].get(name, 0j)) for f in freqs]
         for name in network.branches]
    )
    return out


def _normwise(a: np.ndarray, b: np.ndarray, scale: float) -> float:
    """``max|a - b| / scale``; a NaN must sit at the same place in both.

    Parameters
    ----------
    a, b : numpy.ndarray
        Arrays of the same shape.
    scale : float
        The physical scale to divide by.

    Returns
    -------
    float
        The normwise deviation; ``inf`` for a shape or NaN-pattern mismatch
        and for a nonzero difference at a zero scale.
    """
    a = np.asarray(a)
    b = np.asarray(b)
    if a.shape != b.shape:
        return float("inf")
    if np.any(np.isnan(a) != np.isnan(b)):
        return float("inf")
    diff = np.abs(np.where(np.isnan(a), 0, a) - np.where(np.isnan(b), 0, b))
    worst = float(diff.max()) if diff.size else 0.0
    if worst == 0.0:
        return 0.0
    return worst / scale if scale > 0 else float("inf")


def compare(
    network,
    fault: str,
    oracle: OracleSolution,
    *,
    frequencies: Optional[List[float]] = None,
) -> Dict[str, float]:
    """Deviation of the stored result from the oracle, quantity by quantity.

    Scales are physical: potentials against
    ``max|U|``, currents against the largest injection (the fault current),
    the dimensionless reduction factors absolutely, impedances against their
    own maximum. RMS columns are checked as well.

    The earth-group quantities (``I_E``, ``r_current``, feeding buses) are
    compared only where they are numerically defined (finding P9), with the
    group oriented as the kernel orients it -- by the fault bus, or by the
    fault-bus potential when the fault bus carries no electrode current of
    its own; buses whose electrode current is rounding noise
    (``|I_a| <= 1e-9 max``) or lies on the half-plane boundary are excluded
    from the membership check. ``Z_G`` is the driving point ``Z_dp(F)`` of
    the earthing network (decision F4) and compared at every bin where the
    oracle has a finite one; before F4 it was the group impedance
    ``U_E / I_E`` and was compared with the group. ``value`` is compared
    where ``|u_F,no mutual| > 1e-9 max|U|``.

    Parameters
    ----------
    network : Network
        The solved network.
    fault : str
        The solved fault.
    oracle : OracleSolution
        The reference.
    frequencies : list of float, optional
        Compare only these frequencies (default: all). The RMS columns, which
        mix all frequencies, are compared only when all are selected.

    Returns
    -------
    dict
        Deviation per quantity; ``"earth_buses"`` is 0.0 when the memberships
        agree and ``inf`` otherwise.
    """
    pkg = package_arrays(network, fault)
    result = network.results[fault]
    assert pkg["bus_names"] == oracle.bus_names
    assert pkg["branch_names"] == oracle.branch_names
    every = oracle.frequencies
    freqs = every if frequencies is None else [f for f in every if f in frequencies]
    cols = [every.index(f) for f in freqs]
    if not cols:
        return {}

    def sel(a):
        return np.asarray(a)[:, cols]

    i_scale = float(np.abs(sel(oracle.i_inj)).max()) if oracle.i_inj.size else 0.0
    u_scale = float(np.abs(sel(oracle.u)).max()) if oracle.u.size else 0.0
    dev: Dict[str, float] = {}
    dev["u"] = _normwise(sel(pkg["u"]), sel(oracle.u), u_scale)
    dev["ia"] = _normwise(sel(pkg["ia"]), sel(oracle.ia), i_scale)
    dev["i_inj"] = _normwise(sel(pkg["i_inj"]), sel(oracle.i_inj), i_scale)
    dev["i_shield"] = _normwise(sel(pkg["i_shield"]), sel(oracle.i_shield), i_scale)
    dev["i_phase"] = _normwise(sel(pkg["i_phase"]), sel(oracle.i_phase), i_scale)
    if len(cols) == len(every):
        dev["u_rms"] = _normwise(
            pkg["u_rms"], np.sqrt((np.abs(oracle.u) ** 2).sum(axis=1)), u_scale
        )
        dev["ia_rms"] = _normwise(
            pkg["ia_rms"], np.sqrt((np.abs(oracle.ia) ** 2).sum(axis=1)), i_scale
        )
        dev["i_shield_rms"] = _normwise(
            pkg["i_shield_rms"],
            np.sqrt((np.abs(oracle.i_shield) ** 2).sum(axis=1)),
            i_scale,
        )

    degenerate = u_scale < 1e-9 * max(1.0, i_scale)
    rf = result.reduction_factor
    k_fault = oracle.bus_names.index(network.faults[fault].bus)

    # EPR-based reduction factor, where its denominator is not noise.
    r_dev = 0.0
    for j, f in zip(cols, freqs, strict=True):
        u_max = np.abs(oracle.u[:, j]).max() if oracle.u.size else 0.0
        denom = abs(oracle.u_no_mutual[k_fault, j])
        if degenerate or not denom > 1e-9 * u_max:
            continue
        expected = abs(oracle.u[k_fault, j]) / denom
        got = rf.value.get(f)
        r_dev = max(r_dev, float("inf") if got is None else abs(got - expected))
    dev["r_epr"] = r_dev

    has_group_fields = "z_earthing_group" in type(rf).model_fields
    ie_dev = ri_dev = zg_dev = 0.0
    membership_ok = True
    z_scale = 0.0
    z_pairs: List[Tuple[complex, complex]] = []
    for j, f in zip(cols, freqs, strict=True):
        if degenerate:
            continue
        currents = oracle.electrode[j]
        values = np.array(list(currents.values()), dtype=complex)
        if values.size == 0:
            continue
        mx = float(np.abs(values).max())
        i_f = abs(oracle.i_fault[j])
        if not (mx > 1e-9 * max(i_scale, 1e-300)) or i_f == 0:
            continue
        # Orientation as in the kernel (P9): the fault-bus potential decides
        # when the fault bus has no electrode current of its own, unless it
        # is itself rounding noise.
        u_fault = complex(oracle.u[k_fault, j])
        u_max = float(np.abs(oracle.u[:, j]).max())
        anchor = u_fault if abs(u_fault) > 1e-9 * u_max else None
        split = split_earth_currents(
            currents, reference_bus=network.faults[fault].bus, anchor=anchor
        )
        if split is None:
            continue
        half = 0.5 * float(np.abs(values).sum())
        if not abs(split.i_earth) > 1e-6 * half:
            continue  # I_E is cancellation noise (P9)
        got_ie = rf.i_earth.get(f)
        if got_ie is None:
            ie_dev = float("inf")
            continue
        got_ie = complex(got_ie)
        # membership, without noise and boundary buses
        direction = split.i_earth / abs(split.i_earth)
        excluded = {
            name
            for name, v in currents.items()
            if abs(v) <= 1e-9 * mx
            or abs((v * direction.conjugate()).real) <= 1e-9 * mx
        }
        want = set(split.feeding_buses) - excluded
        have = set(rf.earth_buses.get(f, [])) - excluded
        if want != have:
            membership_ok = False
        ie_dev = max(ie_dev, abs(got_ie - split.i_earth) / i_scale)
        expected_r = abs(split.i_earth) / i_f
        got_r = rf.value_current.get(f)
        ri_dev = max(ri_dev, float("inf") if got_r is None else abs(got_r - expected_r))
        if not has_group_fields:
            # before decision F4: the current-weighted group impedance
            potentials = {
                name: complex(oracle.u[k, j]) for k, name in enumerate(oracle.bus_names)
            }
            u_e, _ = earthing_voltage(potentials, currents, split.feeding_buses)
            expected_z = u_e / split.i_earth
            z_pairs.append(
                (_cn(result.grounding_impedance.value.get(f)), complex(expected_z))
            )
            z_scale = max(z_scale, abs(expected_z))
    if has_group_fields:
        # Z_E = Z_dp(F) (decision F4) is a property of the earthing network
        # and defined whether or not an earth group exists: every bin.
        for j, f in zip(cols, freqs, strict=True):
            expected_z = complex(oracle.z_dp[j])
            if not (math.isfinite(expected_z.real) and math.isfinite(expected_z.imag)):
                continue
            z_pairs.append((_cn(result.grounding_impedance.value.get(f)), expected_z))
            z_scale = max(z_scale, abs(expected_z))
    for got, want in z_pairs:
        zg_dev = max(zg_dev, abs(got - want) / z_scale if z_scale > 0 else 0.0)
    dev["i_earth"] = ie_dev
    dev["r_current"] = ri_dev
    dev["z_g"] = zg_dev
    dev["earth_buses"] = 0.0 if membership_ok else float("inf")
    return dev

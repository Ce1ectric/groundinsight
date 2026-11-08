# tests/_transient_reference.py

"""
Independent reference solver for the transient tests of audit pass 19.

Why a second solver
-------------------
The state-space solver of :mod:`groundinsight.simulation.transient` eliminates
the bus voltages with a Schur complement, substitutes
``z = i_s + (M/L_self) * I_p`` to get rid of the derivative of the phase
current and integrates with the matrix exponential of ``scipy.signal.lsim``.
Testing it against the stationary solver only checks the steady state. This
module solves the same physics a different way, so that the dynamics are
checked as well:

* **Formulation.** Modified nodal analysis with *every* branch current as an
  unknown -- the shield current ``j`` and the phase-conductor current
  ``i_p`` of each branch -- plus the bus EPRs ``v`` and the phase-node
  potentials ``phi``. Nothing is eliminated and nothing is substituted.
* **Phase conductor.** The phase conductors form a network of their own with
  ``R_p + L_p d/dt`` per branch, the fault bus as reference and the source
  current entering at the source bus. Its branch currents are *states*, so a
  split that depends on frequency (routes with different ``R_p/L_p``) is
  integrated exactly instead of being frozen to one factor.
* **Integration.** The trapezoidal rule (or backward Euler) on the
  descriptor system ``E x' = F x + g(t)`` with one LU factorisation.

The model is the documented one-way coupling of the package: the phase
current induces ``R_m I_p + M dI_p/dt`` along the shield, the shield does
not act back on the phase conductor. Sign convention as in
:mod:`groundinsight.electrical_network`: ``U_from - U_to = Z_s I_s - Z_m I_p``
with both currents counted from ``from_bus`` to ``to_bus``, and the earthing
system receives ``+I`` at the source bus and ``-I`` at the fault bus.

Only NumPy is imported; nothing from ``groundinsight``.
"""

from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional

import numpy as np


@dataclass
class RefBranch:
    """One branch: shield conductor coupled to a phase conductor.

    Attributes
    ----------
    name, from_bus, to_bus : str
        Identification and orientation (currents count ``from -> to``).
    R_s, L_s : float
        Series resistance and inductance of the shield loop (Ohm, H).
    R_m, M : float
        Mutual resistance and inductance between phase and shield loop.
    R_p, L_p : float
        Series resistance and inductance of the phase conductor.
    shield : bool
        ``False`` for a branch without a grounding conductor: it carries
        phase current only.
    """

    name: str
    from_bus: str
    to_bus: str
    R_s: float
    L_s: float
    R_m: float = 0.0
    M: float = 0.0
    R_p: float = 0.0
    L_p: float = 0.0
    shield: bool = True


@dataclass
class RefNetwork:
    """Buses with a resistance to remote earth, branches, one source, one fault.

    Attributes
    ----------
    bus_R : dict of str to float
        Earthing resistance per bus in Ohm.
    branches : list of RefBranch
        The branches.
    source_bus, fault_bus : str
        Where the fault current enters and leaves the phase network.
    """

    bus_R: Dict[str, float]
    branches: List[RefBranch] = field(default_factory=list)
    source_bus: str = ""
    fault_bus: str = ""


class _Layout:
    """Index bookkeeping for ``x = [v, j, phi', i_p]``.

    ``v`` has one entry per bus, ``j`` one per shield branch, ``phi'`` one per
    bus except the fault bus (the reference of the phase network) and ``i_p``
    one per branch.
    """

    def __init__(self, net: RefNetwork):
        self.buses = list(net.bus_R)
        self.bus_idx = {name: k for k, name in enumerate(self.buses)}
        self.shield = [b for b in net.branches if b.shield]
        self.branches = list(net.branches)
        self.phase_nodes = [b for b in self.buses if b != net.fault_bus]
        self.phase_idx = {name: k for k, name in enumerate(self.phase_nodes)}
        self.nv = len(self.buses)
        self.nj = len(self.shield)
        self.nphi = len(self.phase_nodes)
        self.nip = len(self.branches)
        self.o_j = self.nv
        self.o_phi = self.o_j + self.nj
        self.o_ip = self.o_phi + self.nphi
        self.size = self.o_ip + self.nip
        self.ip_of = {b.name: k for k, b in enumerate(self.branches)}


def _assemble(net: RefNetwork, phase_factors: Optional[Dict[str, float]] = None):
    """Return ``(E, F, g_unit, layout)`` of the descriptor system.

    ``E x' = F x + g_unit * I(t)``; the rows are shield KCL, shield branch
    law, phase KCL (fault bus excluded) and phase branch law.

    With ``phase_factors`` the phase network is not solved: every phase
    current is prescribed as ``i_p = factor * I(t)`` (zero for a branch that
    is not listed) and the phase-node potentials are pinned to zero. That is
    the frozen-split model the state-space solver integrates, written in this
    module's own formulation.
    """
    lay = _Layout(net)
    n = lay.size
    E = np.zeros((n, n))
    F = np.zeros((n, n))
    g = np.zeros(n)

    # Rows 0..nv-1: shield KCL  G v + A j = I (e_S - e_F)
    for name, R in net.bus_R.items():
        k = lay.bus_idx[name]
        F[k, k] -= 1.0 / R
    for q, br in enumerate(lay.shield):
        a, b = lay.bus_idx[br.from_bus], lay.bus_idx[br.to_bus]
        F[a, lay.o_j + q] -= 1.0
        F[b, lay.o_j + q] += 1.0
    g[lay.bus_idx[net.source_bus]] += 1.0
    g[lay.bus_idx[net.fault_bus]] -= 1.0
    # Written as 0 = F x + g: the KCL residual G v + A j - inj = 0 becomes
    # -(G v + A j) + inj = 0.

    # Rows nv..nv+nj-1: shield branch  L_s j' - M i_p' = v_a - v_b - R_s j + R_m i_p
    for q, br in enumerate(lay.shield):
        row = lay.o_j + q
        a, b = lay.bus_idx[br.from_bus], lay.bus_idx[br.to_bus]
        p = lay.o_ip + lay.ip_of[br.name]
        E[row, lay.o_j + q] = br.L_s
        E[row, p] = -br.M
        F[row, a] += 1.0
        F[row, b] -= 1.0
        F[row, lay.o_j + q] -= br.R_s
        F[row, p] += br.R_m

    if phase_factors is not None:
        for k in range(lay.nphi):
            F[lay.o_phi + k, lay.o_phi + k] = -1.0
        for br in lay.branches:
            row = lay.o_ip + lay.ip_of[br.name]
            F[row, row] = -1.0
            g[row] = float(phase_factors.get(br.name, 0.0))
        return E, F, g, lay

    # Rows: phase KCL at every bus except the fault bus  A_p' i_p = I e_S'
    for name, k in lay.phase_idx.items():
        row = lay.o_phi + k
        for br in lay.branches:
            p = lay.o_ip + lay.ip_of[br.name]
            if br.from_bus == name:
                F[row, p] -= 1.0
            if br.to_bus == name:
                F[row, p] += 1.0
        if name == net.source_bus:
            g[row] += 1.0

    # Rows: phase branch  L_p i_p' = phi_a - phi_b - R_p i_p   (phi_fault = 0)
    for br in lay.branches:
        row = lay.o_ip + lay.ip_of[br.name]
        E[row, row] = br.L_p
        if br.from_bus in lay.phase_idx:
            F[row, lay.o_phi + lay.phase_idx[br.from_bus]] += 1.0
        if br.to_bus in lay.phase_idx:
            F[row, lay.o_phi + lay.phase_idx[br.to_bus]] -= 1.0
        F[row, row] -= br.R_p
    return E, F, g, lay


def phasor_solve(
    net: RefNetwork,
    frequency: float,
    current: complex = 1.0,
    phase_factors: Optional[Dict[str, float]] = None,
):
    """Steady state at one frequency.

    Parameters
    ----------
    net : RefNetwork
        The network.
    frequency : float
        Frequency in Hz.
    current : complex
        Source current phasor.
    phase_factors : dict of str to float, optional
        Prescribed per-unit phase current per branch instead of the solved
        phase network (see :func:`_assemble`).

    Returns
    -------
    dict
        ``"epr"``: bus EPR phasors, ``"i_phase"``: phase current per branch,
        ``"i_shield"``: shield current per shield branch (``from -> to``).
    """
    E, F, g, lay = _assemble(net, phase_factors)
    s = 2j * np.pi * frequency
    x = np.linalg.solve(s * E - F, g * current)
    return {
        "epr": {b: x[lay.bus_idx[b]] for b in lay.buses},
        "i_phase": {b.name: x[lay.o_ip + k] for k, b in enumerate(lay.branches)},
        "i_shield": {b.name: x[lay.o_j + q] for q, b in enumerate(lay.shield)},
    }


def time_solve(
    net: RefNetwork,
    t: np.ndarray,
    current: Callable[[np.ndarray], np.ndarray],
    method: str = "trapezoidal",
    phase_factors: Optional[Dict[str, float]] = None,
):
    """Integrate from rest on the uniform grid ``t``.

    Parameters
    ----------
    net : RefNetwork
        The network.
    t : numpy.ndarray
        Uniform time grid starting at 0.
    current : callable
        Source current ``I(t)``; should start at 0 for a consistent start.
    method : {"trapezoidal", "backward_euler"}
        Integration rule.
    phase_factors : dict of str to float, optional
        Prescribed per-unit phase current per branch instead of the solved
        phase network (see :func:`_assemble`).

    Returns
    -------
    dict
        ``"epr"``: bus EPR time series, ``"i_phase"``: phase current per
        branch, ``"i_shield"``: shield current per shield branch.
    """
    E, F, g, lay = _assemble(net, phase_factors)
    h = float(t[1] - t[0])
    u = np.asarray(current(t), dtype=float)
    x = np.zeros((t.size, lay.size))
    if method == "trapezoidal":
        lhs = E - 0.5 * h * F
        rhs_mat = E + 0.5 * h * F
    elif method == "backward_euler":
        lhs = E - h * F
        rhs_mat = E
    else:  # pragma: no cover - test helper misuse
        raise ValueError(method)
    lu_inv = np.linalg.inv(lhs)
    for k in range(t.size - 1):
        if method == "trapezoidal":
            rhs = rhs_mat @ x[k] + 0.5 * h * g * (u[k] + u[k + 1])
        else:
            rhs = rhs_mat @ x[k] + h * g * u[k + 1]
        x[k + 1] = lu_inv @ rhs
    return {
        "epr": {b: x[:, lay.bus_idx[b]] for b in lay.buses},
        "i_phase": {b.name: x[:, lay.o_ip + k] for k, b in enumerate(lay.branches)},
        "i_shield": {b.name: x[:, lay.o_j + q] for q, b in enumerate(lay.shield)},
    }


def phase_split(net: RefNetwork, frequency: float) -> Dict[str, complex]:
    """Per-unit phase current per branch at one frequency (``from -> to``)."""
    return phasor_solve(net, frequency, 1.0)["i_phase"]


def steady_phasor(
    t: np.ndarray, signal: np.ndarray, frequency: float, periods: int = 5
) -> complex:
    """RMS phasor of ``signal`` at ``frequency`` over its last whole periods.

    Projects the last ``periods`` full periods onto ``exp(j w t)``; with an
    integer number of samples per period this is exact for a pure sinusoid,
    unlike an RMS over a window that is one sample short of a period.

    Parameters
    ----------
    t, signal : numpy.ndarray
        Uniform time grid and samples.
    frequency : float
        Frequency in Hz.
    periods : int
        Number of trailing periods to use.

    Returns
    -------
    complex
        ``U`` such that ``signal ~ Re(sqrt(2) U exp(j w t))``.
    """
    dt = float(t[1] - t[0])
    per = int(round(1.0 / (frequency * dt)))
    if abs(per * dt * frequency - 1.0) > 1e-9:
        raise ValueError("the grid must hold an integer number of samples per period")
    m = per * periods
    tt = t[-m:]
    ss = np.asarray(signal)[-m:]
    w = 2.0 * np.pi * frequency
    return complex(np.sqrt(2.0) * np.mean(ss * np.exp(-1j * w * tt)))

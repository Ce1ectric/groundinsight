# analysis/_session.py
"""
Study sessions: re-solve a solved fault after its bus impedances changed (P4).

Why this module exists
----------------------
The inverse searches evaluate the same fault for twenty or more trial values
of the bus grounding impedances, and every trial used to be a complete
:func:`~groundinsight.network_operations.run_fault`: path check, model tables,
phase-conductor network, injection vectors, assembly, factorisation and the
result objects of every bus and branch -- although a trial changes nothing
but the grounding impedances of the selected buses.

Those enter the nodal system in exactly one place, the diagonal of ``Y``.
Everything else a solve needs is the same for every trial:

* the position of every stamp of ``Y`` (the sparsity pattern) and the
  admittances of the grounding branches and Thevenin loop closures;
* the injection vectors -- source currents, the phase currents of the
  phase-conductor network and the mutual Norton terms derived from them,
  none of which reads a bus impedance.

A session keeps those from one full solve and answers a trial by
re-assembling the values of ``Y`` (the kernel's own
:func:`~groundinsight.electrical_network.assemble_nodal_matrices`, so the
entries are summed in the same order), factorising them and solving
the kept injection vector. The matrix, the factorisation and the solution are
those of a full ``run_fault`` with the same impedances, bit for bit, and so is
the RMS at the fault bus (:func:`~groundinsight.electrical_network.rms_columns`)
-- the search takes the same steps and returns the same numbers.

When a session cannot answer, it says so (``None``) and the caller solves in
full. It does not try where the answer would need more than the diagonal:

* a 0 Hz stand-in is in use -- its size follows the smallest impedance of the
  network, possibly a bus impedance the search changes;
* a bus gains or loses its electrode, or a value is shorted, open, NaN or
  not passive -- that changes what the solver treats as reference earth, or
  is an error the model reports in its own words;
* the factorisation or the solution fails.

Errors are therefore always raised by the full solve, with its message.
"""

from __future__ import annotations

import logging
from typing import Dict, List, Optional

import numpy as np
from scipy.sparse.linalg import splu

from ..electrical_network import (
    ElectricalNetwork,
    NodalParts,
    assemble_nodal_matrices,
    bus_admittances,
    rms_columns,
    stack_columns,
)
from ..models.core_models import Bus, Network
from ..utils.impedance_calculator import (
    compute_impedance_array,
    compute_real_value,
    short_circuit_mask,
)

logger = logging.getLogger(__name__)

#: Switch for the tests: with ``False`` every trial is a full solve, which is
#: the reference the sessions are checked against.
_ENABLED = True


def bus_impedance_at(bus: Bus, rho: float, frequencies: List[float]) -> np.ndarray:
    """
    The grounding impedance a bus would derive for another ``rho``.

    Evaluates what :meth:`Bus.calculate_impedance` evaluates for the specific
    earth resistance ``rho`` -- the impedance formula through the same
    compiled function, and the R, L, C formulas of the type, whose values a
    steady-state solve does not need but whose failure the model reports --
    without writing anything to the bus. The passivity rules are not applied
    here: a session accepts only finite, invertible values with a
    non-negative real part (:func:`_usable_electrodes`), which pass them,
    and hands every other value to the full solve, whose check reports it.

    Parameters
    ----------
    bus : Bus
        The bus; its type supplies the formulas.
    rho : float
        Specific earth resistance in Ohm*m.
    frequencies : list of float
        The network frequencies.

    Returns
    -------
    numpy.ndarray
        Complex impedances, one per frequency, bit for bit the values
        ``calculate_impedance`` would store.

    Raises
    ------
    ValueError
        If a formula cannot be evaluated, as :meth:`Bus.calculate_impedance`
        raises it.
    """
    params = {"rho": rho}
    values = compute_impedance_array(bus.type.impedance_formula, frequencies, params)
    for field, extra in (
        ("R", bus.type.R_formula),
        ("L", bus.type.L_formula),
        ("C", bus.type.C_formula),
    ):
        if extra is not None:
            compute_real_value(extra, frequencies, params, name=f"{bus.name}.{field}")
    return values


def _usable_electrodes(values: np.ndarray) -> np.ndarray:
    """Per bus: whether every value is a finite, invertible, passive impedance.

    Such values pass every rule of the solver's passivity check and keep the
    bus an electrode at every frequency.

    Parameters
    ----------
    values : numpy.ndarray
        Complex impedances, ``(buses, F)``.

    Returns
    -------
    numpy.ndarray
        Boolean per bus; ``False`` for an open, NaN, zero or too-small value
        at any frequency, and for a negative real part.
    """
    finite = np.isfinite(values.real) & np.isfinite(values.imag)
    short = short_circuit_mask(values.real, values.imag)
    return (finite & ~short & (values.real >= 0.0)).all(axis=1)


class BusImpedanceSession:
    """
    Re-solves one solved fault for other bus grounding impedances.

    Created from the assembled system a full ``run_fault`` left on the
    network (:meth:`attach`). It keeps only what a re-solve reads -- the
    stamp pattern and admittances, the bus impedances as assembled, the
    injection vectors -- and nothing of the model, so later edits of the
    network do not reach it, and a search that holds one session per fault
    location does not keep their result objects and factorisations alive.

    Parameters
    ----------
    electrical : ElectricalNetwork
        The solved system of the fault.
    parts : NodalParts
        Its :meth:`~groundinsight.electrical_network.ElectricalNetwork.nodal_parts`.
    fault_row : int
        Row of the fault bus in the nodal system.

    Notes
    -----
    Use :meth:`attach` rather than the constructor: it checks that the system
    qualifies.
    """

    def __init__(self, electrical: ElectricalNetwork, parts: NodalParts, fault_row: int):
        self._fault_row = fault_row
        self._rows = dict(electrical.bus_indices)
        self._frequencies = list(electrical.network.frequencies)
        self._bus_z = parts.bus_z
        self._present = parts.bus_present
        # The buses that are an electrode at every frequency as assembled --
        # the only ones whose value a session may change.
        self._usable = self._present.all(axis=1) & _usable_electrodes(self._bus_z)
        self._pattern = parts.pattern
        self._grid = parts.grid_admittance
        self._loop = parts.loop_admittance
        self._i_vectors = list(parts.i_vectors)

    @classmethod
    def attach(cls, network: Network, fault: str) -> Optional[BusImpedanceSession]:
        """
        A session for the system ``run_fault`` just solved, or ``None``.

        Parameters
        ----------
        network : Network
            The network ``run_fault`` was called on.
        fault : str
            The fault it solved.

        Returns
        -------
        BusImpedanceSession or None
            ``None`` when sessions are switched off, the network carries no
            solved system of this fault, or the system uses a 0 Hz stand-in
            (whose size may follow the bus impedances).
        """
        if not _ENABLED:
            return None
        electrical = network.electrical_network
        if not isinstance(electrical, ElectricalNetwork):
            return None
        if network.active_fault != fault or not electrical.u_vectors:
            return None
        parts = electrical.nodal_parts()
        if parts is None or parts.dc_substitute:
            return None
        fault_row = electrical.bus_indices.get(network.faults[fault].bus)
        if fault_row is None:
            return None
        return cls(electrical, parts, fault_row)

    def same_matrix_as(self, other: BusImpedanceSession) -> bool:
        """``True`` if both sessions assemble the same ``Y`` for the same impedances.

        Two fault locations of one network share their nodal matrix unless a
        voltage source closes its loop between its bus and the fault bus --
        that loop is the only fault-dependent stamp. Sessions that share it
        can share each trial's factorisation (:meth:`factorise`).

        Parameters
        ----------
        other : BusImpedanceSession
            Another session.

        Returns
        -------
        bool
            ``True`` when neither has a loop closure and pattern, branch
            admittances and assembled bus impedances are identical.
        """
        if self._loop.size or other._loop.size:
            return False
        return (
            self._frequencies == other._frequencies
            and self._rows == other._rows
            and self._pattern.size == other._pattern.size
            and np.array_equal(self._pattern.indices, other._pattern.indices)
            and np.array_equal(self._pattern.indptr, other._pattern.indptr)
            and np.array_equal(self._pattern.inverse, other._pattern.inverse)
            and np.array_equal(self._grid, other._grid)
            and np.array_equal(self._bus_z, other._bus_z, equal_nan=True)
            and np.array_equal(self._present, other._present)
        )

    def factorise(self, impedances: Dict[str, np.ndarray]) -> Optional[list]:
        """
        The factorisation of ``Y`` per frequency with ``impedances`` in place.

        Parameters
        ----------
        impedances : dict of str to numpy.ndarray
            New grounding impedance per bus name, one value per network
            frequency (as :func:`bus_impedance_at` returns them). Buses that
            are not part of the solve (inactive) are ignored -- they are not
            in ``Y`` either.

        Returns
        -------
        list of scipy.sparse.linalg.SuperLU or None
            One factorisation per network frequency, or ``None`` when only a
            full solve can answer (see the module docstring).
        """
        bus_z = self._bus_z.copy()
        rows: List[int] = []
        values: List[np.ndarray] = []
        for name, value in impedances.items():
            row = self._rows.get(name)
            if row is not None:
                rows.append(row)
                values.append(value)
        if rows:
            # The electrode has to be there before and after: anything else
            # changes the structure the solver's guards decide on, or is an
            # error the full solve reports.
            index = np.asarray(rows, dtype=np.int64)
            new = np.vstack(values)
            if not (self._usable[index].all() and _usable_electrodes(new).all()):
                return None
            bus_z[index] = new
        matrices = assemble_nodal_matrices(
            self._pattern,
            bus_admittances(bus_z, self._present),
            self._grid,
            self._loop,
            self._frequencies,
        )
        factors = []
        for freq in self._frequencies:
            try:
                factors.append(splu(matrices[freq]))
            except (np.linalg.LinAlgError, RuntimeError):
                return None
        return factors

    def potentials(
        self, impedances: Dict[str, np.ndarray], factors: Optional[list] = None
    ) -> Optional[Dict[float, np.ndarray]]:
        """
        Bus potentials with ``impedances`` in place of the assembled ones.

        Parameters
        ----------
        impedances : dict of str to numpy.ndarray
            As for :meth:`factorise`.
        factors : list, optional
            The factorisation of these impedances from a session with the
            same matrix (:meth:`same_matrix_as`); computed here otherwise.

        Returns
        -------
        dict of float to numpy.ndarray or None
            ``u`` per frequency, or ``None`` when only a full solve can answer
            (see the module docstring).
        """
        if factors is None:
            factors = self.factorise(impedances)
            if factors is None:
                return None
        solved: Dict[float, np.ndarray] = {}
        for freq, i_vector, lu in zip(self._frequencies, self._i_vectors, factors, strict=True):
            u_vector = lu.solve(i_vector)
            if not np.all(np.isfinite(u_vector)):
                return None
            solved[freq] = u_vector
        return solved

    def epr_rms(
        self, impedances: Dict[str, np.ndarray], factors: Optional[list] = None
    ) -> Optional[float]:
        """
        RMS earth potential rise at the fault bus for new bus impedances.

        Parameters
        ----------
        impedances : dict of str to numpy.ndarray
            As for :meth:`factorise`.
        factors : list, optional
            As for :meth:`potentials`.

        Returns
        -------
        float or None
            The value ``ResultBus.uepr`` of the fault bus would carry after a
            full ``run_fault`` with these impedances, or ``None`` when only
            that full solve can answer.
        """
        solved = self.potentials(impedances, factors)
        if solved is None:
            return None
        stacked = stack_columns(
            [solved[freq] for freq in self._frequencies], self._bus_z.shape[0]
        )
        return float(rms_columns(stacked)[self._fault_row])

# simulation/transient.py

"""
Transient Simulation Layer.

This module hosts the high-level :class:`TransientStudy` workflow and the
matching :class:`ResultTransient` Pydantic model. The first solver path
implemented here is FFT-based: a user-defined source waveform is sampled
on a regular time grid, transformed to a frequency spectrum via NumPy's
real-valued FFT, fed into the existing per-frequency network solve, and
transformed back via IFFT. The state-space solver path is reserved for
the next release; it will reuse the same study object and observation
contract.

Design choices recorded in the Phase 3 discussion:

* Default and currently only supported source mode for the FFT solver is
  the legacy current source (``Source.source_type='current'``). A current
  source's frequency-dependent ``values`` are *replaced* by the FFT
  spectrum of the user-supplied waveform; the rest of the network remains
  untouched. Voltage-mode sources will be supported by the state-space
  solver in Phase 4 (where the loop closure is naturally part of the ODE
  system).
* Observation points (buses for EPR, branches for shield current) are
  passed *explicitly* to ``set_observation`` -- there is no "all" default,
  to keep memory and post-processing tractable on large networks.
* Mutual coupling is not yet evaluated by the FFT solver; the phase
  current along each path would itself be time-dependent and is more
  natural to handle in the state-space formulation. The FFT solver
  ignores mutual impedance and emits no warning -- it is documented as a
  known limitation and the demo notebook shows how to interpret the
  resulting EPR.
"""

import logging
from typing import Callable, Dict, List, Optional, TYPE_CHECKING

import numpy as np
from pydantic import BaseModel, ConfigDict, Field

from groundinsight.models.core_models import Network

if TYPE_CHECKING:
    import polars as pl  # forward reference for type annotations only
from groundinsight.utils.impedance_calculator import (
    check_passive_impedance,
    compute_impedance,
    dc_substitute_impedance,
    is_short_circuit,
)


logger = logging.getLogger(__name__)


WaveformFunc = Callable[[np.ndarray], np.ndarray]


class ResultTransient(BaseModel):
    """
    Container for the time-domain results of a transient simulation.

    Attributes
    ----------
    time_s : list of float
        Time samples in seconds, equally spaced.
    epr_t : dict of str to list of float
        Mapping of observed bus name to its EPR time series in volts.
    i_branch_t : dict of str to list of float
        Mapping of observed branch name to its branch current time series
        in amperes.
    source_t : dict of str to list of float
        The sampled source waveforms keyed by source name, in the natural
        unit of the source (amperes for current sources, volts for
        voltage sources).
    fault : str
        Name of the fault that was active during the simulation.
    solver : str
        Identifier of the solver that produced this result
        (``"fft"`` or ``"state_space"``).

    Notes
    -----
    The list-typed fields use plain Python lists for JSON
    serialisability. Convert to ``numpy.ndarray`` at the call site if you
    need vectorised post-processing.
    """

    model_config = ConfigDict(arbitrary_types_allowed=True)

    time_s: List[float]
    epr_t: Dict[str, List[float]] = Field(default_factory=dict)
    i_branch_t: Dict[str, List[float]] = Field(default_factory=dict)
    source_t: Dict[str, List[float]] = Field(default_factory=dict)
    fault: str
    solver: str = "fft"

    def to_polars(self) -> "pl.DataFrame":  # noqa: F821 -- forward ref
        """
        Convert the result to a Polars DataFrame in long form.

        Returns
        -------
        polars.DataFrame
            A DataFrame with columns ``time_s``, ``signal_kind``
            (``"epr"`` / ``"i_branch"`` / ``"source"``), ``name`` and
            ``value``.
        """
        import polars as pl

        rows = []
        for bus_name, series in self.epr_t.items():
            for t, v in zip(self.time_s, series):
                rows.append(
                    {
                        "time_s": t,
                        "signal_kind": "epr",
                        "name": bus_name,
                        "value": v,
                    }
                )
        for branch_name, series in self.i_branch_t.items():
            for t, v in zip(self.time_s, series):
                rows.append(
                    {
                        "time_s": t,
                        "signal_kind": "i_branch",
                        "name": branch_name,
                        "value": v,
                    }
                )
        for src_name, series in self.source_t.items():
            for t, v in zip(self.time_s, series):
                rows.append(
                    {
                        "time_s": t,
                        "signal_kind": "source",
                        "name": src_name,
                        "value": v,
                    }
                )
        return pl.DataFrame(rows)


class TransientStudy:
    """
    High-level entry point for transient simulations.

    A study binds together a :class:`Network`, the active fault to study,
    a set of source waveforms (one per source contributing to the fault),
    and the list of observation points (buses and branches) that should be
    returned as time series. Calling :meth:`solve` produces a
    :class:`ResultTransient`.

    Parameters
    ----------
    network : Network
        The network model. Buses, branches, sources and the active fault
        are taken from this object. The network is not mutated.
    fault_name : str
        Name of the fault to activate for this study.

    Examples
    --------
    >>> import groundinsight as gi  # doctest: +SKIP
    >>> from groundinsight.simulation import waveforms  # doctest: +SKIP
    >>> study = gi.TransientStudy(network, fault_name='F1')  # doctest: +SKIP
    >>> study.set_source_waveform(  # doctest: +SKIP
    ...     'src',
    ...     waveforms.sinusoidal_with_dc_offset(
    ...         amplitude=1e3, frequency_hz=50.0,
    ...         t_on=0.02, t_off=0.12,
    ...         dc_amplitude=500.0, dc_decay_tau=0.05,
    ...     ),
    ... )
    >>> study.set_observation(buses=['bus_fault'], branches=['line1'])  # doctest: +SKIP
    >>> result = study.solve(t_end=0.2, dt=1e-4)  # doctest: +SKIP
    """

    def __init__(self, network: Network, fault_name: str):
        if fault_name not in network.faults:
            raise ValueError(
                f"Fault '{fault_name}' does not exist in the network."
            )
        self.network = network
        self.fault_name = fault_name
        self._source_waveforms: Dict[str, WaveformFunc] = {}
        self._obs_buses: List[str] = []
        self._obs_branches: List[str] = []
        # Stand-in impedance for elements that are an ideal short circuit at
        # the 0 Hz bin, and the index of that bin. Both stay ``None`` unless
        # the FFT solver actually meets such an element. See
        # :meth:`_dc_substitute_for` and :meth:`_resolved_z`.
        self._dc_substitute: Optional[float] = None
        self._dc_bin: Optional[int] = None

    def set_source_waveform(self, source_name: str, waveform: WaveformFunc):
        """
        Bind a time-domain waveform to a network source.

        For ``source_type='current'`` the waveform is the injected current
        in amperes. For ``source_type='voltage'`` the waveform is the
        Thevenin EMF in volts (the ``source_impedance`` is taken from the
        :class:`Source` definition). Voltage sources are accepted by the
        state-space solver but rejected by the FFT solver, which still
        only supports current sources — the relevant solver checks the
        type at ``solve()`` time.

        Parameters
        ----------
        source_name : str
            Name of a source defined on the network.
        waveform : callable
            Vectorised function ``f(t) -> values`` mapping a 1-D time
            array to source values of the same shape.

        Raises
        ------
        ValueError
            If the source does not exist on the network.
        """
        src = self.network.sources.get(source_name)
        if src is None:
            raise ValueError(
                f"Source '{source_name}' does not exist in the network."
            )
        if src.source_type not in ("current", "voltage"):
            raise ValueError(
                f"Source '{source_name}' has unknown source_type "
                f"'{src.source_type}'."
            )
        self._source_waveforms[source_name] = waveform

    def set_observation(
        self,
        *,
        buses: Optional[List[str]] = None,
        branches: Optional[List[str]] = None,
    ):
        """
        Declare which buses and branches should be returned as time series.

        Parameters
        ----------
        buses : list of str, optional
            Names of buses whose EPR ``u(t)`` should be returned. Defaults
            to no buses.
        branches : list of str, optional
            Names of branches whose shield current ``i(t)`` should be
            returned. Defaults to no branches.

        Raises
        ------
        ValueError
            If a name is not present in the network.
        """
        for name in buses or []:
            if name not in self.network.buses:
                raise ValueError(f"Bus '{name}' not in network.")
        for name in branches or []:
            if name not in self.network.branches:
                raise ValueError(f"Branch '{name}' not in network.")
        self._obs_buses = list(buses or [])
        self._obs_branches = list(branches or [])

    def solve(
        self,
        *,
        t_end: float,
        dt: float,
        solver: str = "fft",
    ) -> ResultTransient:
        """
        Run the transient solve and return the resulting time series.

        Parameters
        ----------
        t_end : float
            End time of the simulation in seconds. ``t=0`` is always the
            start.
        dt : float
            Time-step in seconds. For the FFT solver this determines the
            Nyquist frequency ``f_max = 1/(2*dt)`` and therefore the
            highest harmonic resolved. For the state-space solver ``dt``
            is the integration time-step (first-order hold on the source
            signal between samples).
        solver : {'fft', 'state_space'}, optional
            Solver to use. ``"fft"`` uses ``BusType.impedance_formula``
            and ``BranchType.self_impedance_formula`` (frequency-domain
            impedances); ``"state_space"`` uses the lumped RLC fields on
            ``BusType`` / ``BranchType`` and integrates with
            :func:`scipy.signal.lsim`. Defaults to ``"fft"``.

        Returns
        -------
        ResultTransient
            Time-domain results at the observation points.

        Raises
        ------
        ValueError
            If no source waveform is set, if the time parameters are
            invalid, or if the network is missing the lumped RLC fields
            required by the state-space solver.
        NotImplementedError
            If ``solver`` is not one of the supported identifiers.
        """
        if solver == "fft":
            return self._solve_fft(t_end=t_end, dt=dt)
        if solver == "state_space":
            return self._solve_state_space(t_end=t_end, dt=dt)
        raise NotImplementedError(
            f"Unknown transient solver: '{solver}'. "
            "Supported: 'fft', 'state_space'."
        )

    # ------------------------------------------------------------------
    # internals
    # ------------------------------------------------------------------

    def _solve_fft(self, *, t_end: float, dt: float) -> ResultTransient:
        if t_end <= 0 or dt <= 0:
            raise ValueError("t_end and dt must be strictly positive.")
        if not self._source_waveforms:
            raise ValueError(
                "No source waveform set. Call set_source_waveform(...) first."
            )
        for src_name in self._source_waveforms:
            if self.network.sources[src_name].source_type != "current":
                raise ValueError(
                    f"Source '{src_name}' is a voltage source; the FFT "
                    "transient solver only supports current sources. Use "
                    "solver='state_space' for Thevenin sources."
                )

        # --- time and frequency grids --------------------------------------
        n_samples = int(round(t_end / dt))
        # Even N so rfft / irfft round-trip cleanly with default normalisation.
        if n_samples % 2 == 1:
            n_samples += 1
        if n_samples < 4:
            raise ValueError(
                "t_end / dt yields fewer than four samples; choose a finer dt."
            )
        t = np.arange(n_samples) * dt
        freqs = np.fft.rfftfreq(n_samples, d=dt)
        n_freqs = freqs.size

        # --- sample and FFT the source waveforms ---------------------------
        source_signals_t: Dict[str, np.ndarray] = {}
        source_spectra: Dict[str, np.ndarray] = {}
        for src_name, wave in self._source_waveforms.items():
            sig = np.asarray(wave(t), dtype=float)
            if sig.shape != t.shape:
                raise ValueError(
                    f"Waveform for source '{src_name}' returned shape "
                    f"{sig.shape}, expected {t.shape}."
                )
            source_signals_t[src_name] = sig
            source_spectra[src_name] = np.fft.rfft(sig)

        # --- bus index map (active buses only) -----------------------------
        bus_index: Dict[str, int] = {}
        for name, bus in self.network.buses.items():
            if bus.active:
                bus_index[name] = len(bus_index)
        n_buses = len(bus_index)
        if n_buses == 0:
            raise ValueError("Network has no active buses.")

        fault_bus = self.network.faults[self.fault_name].bus
        if fault_bus not in bus_index:
            raise ValueError(
                f"Fault bus '{fault_bus}' is inactive or missing; cannot "
                "solve the transient case."
            )
        fault_bus_idx = bus_index[fault_bus]

        # --- pre-evaluate impedances at all FFT frequencies ----------------
        # Reuses the impedance-calculator's compile-cache, so each unique
        # type-formula is parsed by SymPy exactly once even though we hit a
        # large number of frequency bins.
        freq_list = freqs.tolist()
        bus_Z = self._eval_bus_impedances(bus_index, freq_list)
        branch_Z = self._eval_branch_self_impedances(bus_index, freq_list)

        # --- DC short circuits ----------------------------------------------
        # The 0 Hz bin is always part of an FFT grid, and a purely inductive
        # element is an exact short circuit there. That is the physics, not a
        # modelling mistake, but a short has no reciprocal, so those elements
        # get a small finite stand-in for that one bin.
        self._dc_substitute = self._dc_substitute_for(
            freq_list, bus_Z, branch_Z
        )
        self._dc_bin = (
            freq_list.index(0.0)
            if self._dc_substitute is not None
            else None
        )

        # --- per-frequency solve -------------------------------------------
        u_spectrum = np.zeros((n_buses, n_freqs), dtype=complex)
        for k in range(n_freqs):
            Y = self._build_y_at(bus_index, bus_Z, branch_Z, k)
            i_vec = self._build_i_at(
                bus_index, source_spectra, fault_bus_idx, k
            )
            try:
                u_spectrum[:, k] = np.linalg.solve(Y, i_vec)
            except np.linalg.LinAlgError:
                # Singular at this bin (commonly f=0 with no DC return path).
                # We leave u as zero; for a real-valued input signal the
                # resulting time-domain response is just missing the DC
                # component, which is what the user would expect.
                logger.debug(
                    "Y(f=%g) is singular for the active fault; skipping.",
                    freqs[k],
                )

        # --- IFFT for each observation point -------------------------------
        epr_t: Dict[str, List[float]] = {}
        for bus_name in self._obs_buses:
            if bus_name not in bus_index:
                # Inactive bus -> all-zero response, but stay in the result
                # to make the contract explicit.
                epr_t[bus_name] = [0.0] * n_samples
                continue
            spec = u_spectrum[bus_index[bus_name], :]
            epr_t[bus_name] = np.fft.irfft(spec, n=n_samples).tolist()

        i_branch_t: Dict[str, List[float]] = {}
        for branch_name in self._obs_branches:
            branch = self.network.branches.get(branch_name)
            if branch is None or branch_name not in branch_Z:
                i_branch_t[branch_name] = [0.0] * n_samples
                continue
            from_idx = bus_index[branch.from_bus]
            to_idx = bus_index[branch.to_bus]
            i_spec = np.zeros(n_freqs, dtype=complex)
            for k in range(n_freqs):
                Z = self._resolved_z(branch_Z[branch_name][k], k)
                if Z == 0 or not np.isfinite(Z):
                    continue
                i_spec[k] = (u_spectrum[to_idx, k] - u_spectrum[from_idx, k]) / Z
            i_branch_t[branch_name] = np.fft.irfft(i_spec, n=n_samples).tolist()

        return ResultTransient(
            time_s=t.tolist(),
            epr_t=epr_t,
            i_branch_t=i_branch_t,
            source_t={
                name: sig.tolist() for name, sig in source_signals_t.items()
            },
            fault=self.fault_name,
            solver="fft",
        )

    @staticmethod
    def _dc_substitute_for(
        freqs: List[float],
        bus_Z: Dict[str, np.ndarray],
        branch_Z: Dict[str, np.ndarray],
    ) -> Optional[float]:
        """Size the stand-in for elements that are a short circuit at 0 Hz.

        The FFT grid always contains a 0 Hz bin, so *every* transient run
        evaluates every formula at DC -- there is no way to sidestep it by
        choosing the frequency list, as there is in a steady-state study. An
        ordinary earthing-conductor formula such as ``(0.25 + I*0.6)*l`` is
        non-zero at DC, but a purely inductive one is exactly zero there, and
        that value is correct rather than mistaken.

        Up to v0.4.0 such an element was skipped, i.e. modelled as an open
        circuit -- the exact opposite of a short. Now it is modelled with a
        small finite impedance scaled to the network; see
        :func:`~groundinsight.utils.impedance_calculator.dc_substitute_impedance`.

        Parameters
        ----------
        freqs : list of float
            The FFT frequency grid, in Hz.
        bus_Z : dict
            Complex impedance array per bus over ``freqs``.
        branch_Z : dict
            Complex self-impedance array per branch over ``freqs``.

        Returns
        -------
        float or None
            The substitute impedance in Ohm, or ``None`` when the grid has no
            0 Hz bin or no element is shorted there.
        """
        try:
            k_dc = freqs.index(0.0)
        except ValueError:
            return None

        shorted: List[str] = []
        magnitudes: List[float] = []
        for kind, table in (("bus", bus_Z), ("branch", branch_Z)):
            for name, values in table.items():
                value = complex(values[k_dc])
                if is_short_circuit(value):
                    shorted.append(f"{kind} '{name}'")
                else:
                    magnitudes.append(abs(value))

        if not shorted:
            return None
        return dc_substitute_impedance(
            magnitudes, shorted, context="transient FFT grid"
        )

    def _resolved_z(self, value: complex, k: int) -> complex:
        """Return the impedance to invert for bin ``k``.

        This is the single point where the DC stand-in of
        :meth:`_dc_substitute_for` is applied. Outside the 0 Hz bin, and
        whenever the network has no DC short circuit at all, the value is
        handed back untouched -- in particular a true pole (``inf``, e.g. a
        capacitance at DC) stays ``inf`` and is still treated as an open
        circuit by the callers, which is the correct physics.

        Parameters
        ----------
        value : complex
            The impedance as evaluated from the formula, in Ohm.
        k : int
            Index of the FFT frequency bin.

        Returns
        -------
        complex
            Either ``value`` or the real-valued substitute impedance.
        """
        if (
            self._dc_substitute is not None
            and k == self._dc_bin
            and is_short_circuit(value)
        ):
            return complex(self._dc_substitute, 0.0)
        return complex(value)

    def _eval_bus_impedances(
        self, bus_index: Dict[str, int], freqs: List[float]
    ) -> Dict[str, np.ndarray]:
        """Return a complex impedance array per active bus over ``freqs``."""
        out: Dict[str, np.ndarray] = {}
        for name in bus_index:
            bus = self.network.buses[name]
            z_dict = compute_impedance(
                bus.type.impedance_formula,
                freqs,
                {"rho": bus.specific_earth_resistance},
            )
            check_passive_impedance(
                z_dict,
                element=f"bus '{name}' (grounding impedance, transient FFT grid)",
                formula_str=bus.type.impedance_formula,
                params={"rho": bus.specific_earth_resistance},
            )
            out[name] = np.array(
                [complex(z_dict[f].real, z_dict[f].imag) for f in freqs]
            )
        return out

    def _eval_branch_self_impedances(
        self, bus_index: Dict[str, int], freqs: List[float]
    ) -> Dict[str, np.ndarray]:
        """Return a complex self-impedance array per active branch over ``freqs``.

        Only branches with a grounding conductor and both endpoints active
        contribute admittance; the others would behave as open circuits and
        are simply omitted from the dict.
        """
        out: Dict[str, np.ndarray] = {}
        for name, branch in self.network.branches.items():
            if not branch.active:
                continue
            if not branch.type.grounding_conductor:
                continue
            if branch.from_bus not in bus_index or branch.to_bus not in bus_index:
                continue
            params = {
                "rho": branch.specific_earth_resistance,
                "l": branch.length,
            }
            z_dict = compute_impedance(
                branch.type.self_impedance_formula, freqs, params
            )
            check_passive_impedance(
                z_dict,
                element=f"branch '{name}' (self impedance, transient FFT grid)",
                formula_str=branch.type.self_impedance_formula,
                params=params,
            )
            out[name] = np.array(
                [complex(z_dict[f].real, z_dict[f].imag) for f in freqs]
            )
        return out

    def _build_y_at(
        self,
        bus_index: Dict[str, int],
        bus_Z: Dict[str, np.ndarray],
        branch_Z: Dict[str, np.ndarray],
        k: int,
    ) -> np.ndarray:
        """Construct ``Y`` at FFT frequency bin index ``k``.

        A short circuit (``Z == 0``, or a value so small that ``1 / Z``
        overflows) is only reachable at the 0 Hz bin -- above DC it is rejected
        by :meth:`_eval_bus_impedances` / :meth:`_eval_branch_self_impedances`.
        At 0 Hz it is legitimate physics: an ideal inductance really is a short
        at DC. Such elements are replaced by the small finite stand-in of
        :meth:`_dc_substitute_for` via :meth:`_resolved_z`, which reproduces the
        ideal bond to roughly five significant digits. Up to v0.4.0 they were
        skipped instead, i.e. modelled as an open circuit -- the exact opposite
        of the physics -- which made the DC component of such a transient
        useless. An exact treatment requires merging the two buses into one,
        which is a modelling decision and is left to the user.

        ``inf`` (a true pole, e.g. a capacitance at DC, or the documented
        ``"nan"`` open-end sentinel) is still skipped, which *is* the correct
        physics: no current flows.
        """
        n = len(bus_index)
        Y = np.zeros((n, n), dtype=complex)
        for name, idx in bus_index.items():
            Z = self._resolved_z(bus_Z[name][k], k)
            if Z != 0 and np.isfinite(Z):
                Y[idx, idx] += 1.0 / Z
        for name, branch in self.network.branches.items():
            if name not in branch_Z:
                continue
            Z = self._resolved_z(branch_Z[name][k], k)
            if Z == 0 or not np.isfinite(Z):
                continue
            y = 1.0 / Z
            fi = bus_index[branch.from_bus]
            ti = bus_index[branch.to_bus]
            Y[fi, fi] += y
            Y[ti, ti] += y
            Y[fi, ti] -= y
            Y[ti, fi] -= y
        return Y

    def _build_i_at(
        self,
        bus_index: Dict[str, int],
        source_spectra: Dict[str, np.ndarray],
        fault_bus_idx: int,
        k: int,
    ) -> np.ndarray:
        """Construct ``i`` at FFT frequency bin index ``k`` (current sources only)."""
        n = len(bus_index)
        i_vec = np.zeros(n, dtype=complex)
        total = 0.0 + 0.0j
        for src_name, spectrum in source_spectra.items():
            src = self.network.sources[src_name]
            if src.bus not in bus_index:
                continue
            inj = spectrum[k]
            i_vec[bus_index[src.bus]] += inj
            total += inj
        i_vec[fault_bus_idx] -= total
        return i_vec

    # ------------------------------------------------------------------
    # state-space solver
    # ------------------------------------------------------------------

    def _solve_state_space(self, *, t_end: float, dt: float) -> ResultTransient:
        """
        Solve the transient via modified-nodal-analysis state-space form.

        The network is converted to ``dx/dt = A x + B u``, ``y = C x + D u``
        using each bus's lumped ``R`` / ``L`` / ``C`` (three parallel
        paths to remote earth) and each grounding branch's series
        ``R_self`` / ``L_self``. Voltage sources are added as
        synthetic loop branches between source bus and active fault bus
        with their ``Z_src`` decomposed at ``network.frequencies[0]`` into
        a real ``R_src`` and an inductive part ``L_src = imag/(2*pi*f)``;
        the corresponding EMF (the source waveform itself) enters the
        di/dt equation of the loop inductor.

        State vector layout: ``[i_L_bus, i_L_branch, i_L_voltage_source,
        v_C_bus]``. The non-capacitive bus voltages are eliminated
        algebraically via the Schur complement of the resistive
        conductance matrix and never appear as states. Source waveforms
        are sampled on the regular time grid and integrated with
        ``scipy.signal.lsim``.
        """
        if t_end <= 0 or dt <= 0:
            raise ValueError("t_end and dt must be strictly positive.")
        if not self._source_waveforms:
            raise ValueError(
                "No source waveform set. Call set_source_waveform(...) first."
            )

        # Frequency at which the lumped RLC values are sampled. The
        # state-space model treats RLC as frequency-independent.
        f_eval = self.network.frequencies[0]
        omega_eval = 2.0 * np.pi * f_eval

        # --- bus index map (active buses only) ----------------------------
        bus_index: Dict[str, int] = {}
        bus_R: Dict[str, float] = {}
        bus_L: Dict[str, Optional[float]] = {}
        # Effective bus capacitance: own ``Bus.C`` plus ``C_self / 2`` from
        # every adjacent grounding branch with a defined ``C_self_formula``
        # (pi-section lumping). Stays at 0 for buses with neither.
        bus_C: Dict[str, float] = {}
        for name, bus in self.network.buses.items():
            if not bus.active:
                continue
            if bus.R is None or f_eval not in bus.R:
                raise ValueError(
                    f"Bus '{name}' is missing a resistive ground path "
                    "(R_formula). The state-space solver requires every "
                    "active bus to have a finite R value."
                )
            R_val = float(bus.R[f_eval])
            if R_val <= 0:
                raise ValueError(
                    f"Bus '{name}' has non-positive R = {R_val}; the "
                    "state-space solver requires R > 0."
                )
            bus_index[name] = len(bus_index)
            bus_R[name] = R_val
            bus_L[name] = (
                float(bus.L[f_eval])
                if bus.L is not None and f_eval in bus.L and float(bus.L[f_eval]) > 0
                else None
            )
            # Bus's own grounding capacitance; per-branch shunt
            # capacitance is added below via the pi-section lumping.
            bus_C[name] = (
                float(bus.C[f_eval])
                if bus.C is not None and f_eval in bus.C and float(bus.C[f_eval]) > 0
                else 0.0
            )
        n_bus = len(bus_index)
        if n_bus == 0:
            raise ValueError("Network has no active buses.")

        fault_bus = self.network.faults[self.fault_name].bus
        if fault_bus not in bus_index:
            raise ValueError(
                f"Fault bus '{fault_bus}' is inactive or missing R."
            )
        fault_bus_idx = bus_index[fault_bus]

        # --- branch decomposition ----------------------------------------
        # Three buckets per grounding branch (others are silently skipped):
        #   - "resistive": only R_self -> contributes to G_a, no state.
        #   - "inductive" / "RL": L_self > 0 -> state with optional series R.
        branch_resistive: List[tuple] = []  # (name, from_idx, to_idx, G)
        branch_inductive: List[tuple] = []  # (name, from_idx, to_idx, R_or_None, L)
        skipped: List[str] = []
        mutual_dropped: List[str] = []
        for name, branch in self.network.branches.items():
            if not branch.active:
                continue
            if not branch.type.grounding_conductor:
                continue
            if branch.from_bus not in bus_index or branch.to_bus not in bus_index:
                continue
            R_val = (
                float(branch.R_self[f_eval])
                if branch.R_self and f_eval in branch.R_self
                else None
            )
            L_val = (
                float(branch.L_self[f_eval])
                if branch.L_self and f_eval in branch.L_self
                else None
            )
            from_idx = bus_index[branch.from_bus]
            to_idx = bus_index[branch.to_bus]
            if L_val is not None and L_val > 0:
                branch_inductive.append((name, from_idx, to_idx, R_val, L_val))
            elif R_val is not None and R_val > 0:
                branch_resistive.append((name, from_idx, to_idx, 1.0 / R_val))
                has_mutual = (
                    (branch.R_mutual and f_eval in branch.R_mutual
                     and branch.R_mutual[f_eval])
                    or (branch.M_mutual and f_eval in branch.M_mutual
                        and branch.M_mutual[f_eval])
                )
                if has_mutual:
                    mutual_dropped.append(name)
            else:
                skipped.append(name)
        if skipped:
            logger.warning(
                "State-space solver skipped %d branches without R_self/L_self: %s",
                len(skipped), skipped,
            )
        if mutual_dropped:
            logger.warning(
                "State-space solver ignored mutual coupling on %d purely "
                "resistive branch(es) (R_mutual/M_mutual set but no L_self > 0): "
                "%s. The Carson-style mutual term is currently modelled only for "
                "inductive branches.",
                len(mutual_dropped), mutual_dropped,
            )

        # --- pi-section lumping of branch shunt capacitance --------------
        # Every grounding branch with ``C_self_formula`` populated
        # contributes ``C_self / 2`` to each of its endpoints (the standard
        # pi-section approximation of a distributed-parameter cable). The
        # buses with non-zero effective capacitance then become capacitor
        # states in the partitioning step below.
        for name, branch in self.network.branches.items():
            if not branch.active:
                continue
            if not branch.type.grounding_conductor:
                continue
            if branch.from_bus not in bus_index or branch.to_bus not in bus_index:
                continue
            if branch.C_self is None or f_eval not in branch.C_self:
                continue
            C_branch = float(branch.C_self[f_eval])
            if C_branch <= 0:
                continue
            half_C = 0.5 * C_branch
            bus_C[branch.from_bus] += half_C
            bus_C[branch.to_bus] += half_C

        # --- source decomposition ----------------------------------------
        # Each source maps to one input channel. Voltage sources are
        # decomposed into a synthetic loop (R_src + optional L_src + EMF)
        # between source bus and active fault bus; current sources stay as
        # plain bus injections.
        src_names = list(self._source_waveforms.keys())
        n_src = len(src_names)
        # For each source we keep a record of how it contributes to:
        #   - bus injections (B_kcl column entries, for current sources and
        #     for voltage sources with L_src == 0 via Norton equivalence),
        #   - the conductance matrix (Y_src loop closure for L_src == 0
        #     voltage sources),
        #   - synthetic inductor states (voltage sources with L_src > 0,
        #     contributing to branch_inductive plus an EMF input).
        # The actual matrices are filled below once n_L is known.
        src_records: List[dict] = []
        for src_name in src_names:
            src = self.network.sources[src_name]
            if src.bus not in bus_index:
                raise ValueError(
                    f"Source '{src_name}' sits on inactive or missing bus "
                    f"'{src.bus}'; cannot include in state-space solve."
                )
            src_bus_idx = bus_index[src.bus]
            if src.source_type == "current":
                src_records.append({
                    "kind": "current",
                    "src_idx": src_bus_idx,
                })
                continue
            # voltage source: decompose Z_src at f_eval
            if src.source_impedance is None or f_eval not in src.source_impedance:
                raise ValueError(
                    f"Voltage source '{src_name}' has no source_impedance "
                    f"defined at f={f_eval} Hz."
                )
            z = src.source_impedance[f_eval]
            R_src = float(z.real)
            L_src = float(z.imag) / omega_eval if omega_eval > 0 else 0.0
            if R_src < 0:
                raise ValueError(
                    f"Voltage source '{src_name}' has negative R_src "
                    f"({R_src}); cannot synthesise an RLC equivalent."
                )
            if L_src > 0:
                # The source loop becomes an additional branch inductor.
                # We register it now and let the branch loop pick it up.
                src_records.append({
                    "kind": "voltage_RL",
                    "src_idx": src_bus_idx,
                    "R_src": R_src,
                    "L_src": L_src,
                    # Position in branch_inductive will be filled in below.
                })
                # Synthetic loop branch: from = source bus, to = fault bus.
                # ``R_src`` may legitimately be zero here (pure inductive
                # source impedance), in which case the loop has only L.
                branch_inductive.append((
                    f"__vsrc__{src_name}",
                    src_bus_idx,
                    fault_bus_idx,
                    R_src if R_src > 0 else None,
                    L_src,
                ))
                src_records[-1]["loop_branch_idx"] = len(branch_inductive) - 1
            else:
                if R_src <= 0:
                    raise ValueError(
                        f"Voltage source '{src_name}' has Z_src = 0; cannot "
                        "build a Norton equivalent in state-space."
                    )
                src_records.append({
                    "kind": "voltage_R",
                    "src_idx": src_bus_idx,
                    "R_src": R_src,
                })

        # --- state vector layout -----------------------------------------
        # Order: bus inductor currents, then branch inductor currents
        # (including voltage-source loop inductors), then bus capacitor
        # voltages.
        bus_L_states: List[str] = [
            name for name in bus_index if bus_L[name] is not None
        ]
        n_bus_L = len(bus_L_states)
        n_br_L = len(branch_inductive)
        n_L = n_bus_L + n_br_L

        bus_C_states: List[str] = [
            name for name in bus_index if bus_C[name] > 0
        ]
        n_C = len(bus_C_states)
        n_state = n_L + n_C

        if n_state == 0:
            raise ValueError(
                "State-space solver requires at least one inductive element "
                "(bus L or branch L_self) or one capacitive element. "
                "Otherwise the network has no transient dynamics and the "
                "FFT solver is the right tool."
            )

        # Useful index arrays.
        bus_C_full_idx = np.array(
            [bus_index[name] for name in bus_C_states], dtype=int
        )
        bus_R_states_full_idx = np.array(
            [idx for name, idx in bus_index.items() if bus_C[name] <= 0],
            dtype=int,
        )
        n_R = bus_R_states_full_idx.size  # number of non-capacitive buses

        # --- conductance matrix G_a (resistive shunts + resistive branches
        # + voltage-source Norton conductance) ----------------------------
        G_a = np.zeros((n_bus, n_bus))
        for name, idx in bus_index.items():
            G_a[idx, idx] += 1.0 / bus_R[name]
        for _, fi, ti, G in branch_resistive:
            G_a[fi, fi] += G
            G_a[ti, ti] += G
            G_a[fi, ti] -= G
            G_a[ti, fi] -= G
        for rec in src_records:
            if rec["kind"] == "voltage_R":
                G_src = 1.0 / rec["R_src"]
                a, b = rec["src_idx"], fault_bus_idx
                G_a[a, a] += G_src
                G_a[b, b] += G_src
                G_a[a, b] -= G_src
                G_a[b, a] -= G_src

        # --- B_L: inductor currents -> KCL contribution ------------------
        B_L = np.zeros((n_bus, n_L))
        for col, name in enumerate(bus_L_states):
            B_L[bus_index[name], col] = 1.0
        for k, (_, fi, ti, _, _) in enumerate(branch_inductive):
            col = n_bus_L + k
            B_L[ti, col] = 1.0
            B_L[fi, col] = -1.0

        # --- M: bus voltages -> inductor current derivative --------------
        M = np.zeros((n_L, n_bus))
        for col, name in enumerate(bus_L_states):
            M[col, bus_index[name]] = 1.0 / bus_L[name]
        for k, (_, fi, ti, _, L_val) in enumerate(branch_inductive):
            row = n_bus_L + k
            M[row, ti] = 1.0 / L_val
            M[row, fi] = -1.0 / L_val

        # --- N: -R/L diagonal for inductors with series R ----------------
        N_mat = np.zeros((n_L, n_L))
        for k, (_, _, _, R_val, L_val) in enumerate(branch_inductive):
            row = n_bus_L + k
            if R_val is not None and R_val > 0:
                N_mat[row, row] = -R_val / L_val

        # --- B_kcl (n_bus x n_src) and B_emf (n_L x n_src) ---------------
        # B_kcl maps the source waveform onto KCL injections at buses
        # (current sources directly, voltage sources via Norton when
        # L_src == 0). B_emf maps it onto the EMF term in the di/dt of the
        # corresponding loop inductor (voltage sources with L_src > 0).
        B_kcl = np.zeros((n_bus, n_src))
        B_emf = np.zeros((n_L, n_src))
        for col, rec in enumerate(src_records):
            if rec["kind"] == "current":
                B_kcl[rec["src_idx"], col] += 1.0
                B_kcl[fault_bus_idx, col] -= 1.0
            elif rec["kind"] == "voltage_R":
                G_src = 1.0 / rec["R_src"]
                B_kcl[rec["src_idx"], col] += G_src
                B_kcl[fault_bus_idx, col] -= G_src
            elif rec["kind"] == "voltage_RL":
                row = n_bus_L + rec["loop_branch_idx"]
                # EMF appears in di/dt = (v_to - v_from + U - R*i) / L.
                # See module docstring: U > 0 with (+) at source bus drives
                # current externally from source toward fault, which in our
                # convention is a positive loop-state derivative.
                B_emf[row, col] = 1.0 / rec["L_src"]

        # --- mutual coupling contribution to B_kcl and B_emf -------------
        # For every inductive grounding branch with non-zero R_mutual or
        # M_mutual we add the Carson-style coupling between phase
        # conductor and shield. The phase current along each branch is
        # derived from the source-to-fault path topology (mirror of the
        # FFT solver's _compute_phase_currents_from_paths).
        #
        # Substitution trick: define z = i_shield + (M / L_self) * I_phase.
        # The state we actually integrate is z, which eliminates the
        # ``M * dI_phase/dt`` term in the KVL of the shield branch and
        # turns the mutual coupling into a clean linear feedforward of
        # the source waveform.
        #
        # Restrictions: only current sources contribute -- voltage
        # sources have a state-dependent loop current that would require
        # a more involved substitution and is left out for now.
        mutual_branches: Dict[str, Dict[str, float]] = {}
        for k, (br_name, fi, ti, R_self_val, L_self_val) in enumerate(
            branch_inductive
        ):
            if br_name.startswith("__vsrc__"):
                continue
            branch = self.network.branches[br_name]
            R_mut = (
                float(branch.R_mutual[f_eval])
                if branch.R_mutual and f_eval in branch.R_mutual
                else 0.0
            )
            M_mut = (
                float(branch.M_mutual[f_eval])
                if branch.M_mutual and f_eval in branch.M_mutual
                else 0.0
            )
            if R_mut == 0.0 and M_mut == 0.0:
                continue
            mutual_branches[br_name] = {
                "R_mut": R_mut,
                "M_mut": M_mut,
                "L_self": L_self_val,
                "R_self": R_self_val if R_self_val is not None else 0.0,
                "state_idx": n_bus_L + k,
                "from_idx": fi,
                "to_idx": ti,
            }

        # Path-walking to build the phase factor per (mutual branch,
        # current source) pair. The factor is ``sign * parallel_coefficient``
        # where ``sign = +1`` if the path traverses the branch in
        # ``from_bus -> to_bus`` direction, ``-1`` otherwise. First
        # appearance of a branch on any path from a given source wins,
        # matching the legacy FFT-solver semantics.
        phase_factors: Dict[tuple, float] = {}
        if mutual_branches:
            # The phase factors are read off ``network.paths``. When the paths
            # are missing or stale every factor silently stays zero and the
            # entire Carson coupling is dropped -- *without* an error. The
            # solve still succeeds and returns a peak EPR that was measured
            # 71 % low on a two-branch reference case. A transient study run
            # straight after ``build_network`` (i.e. without a preceding
            # ``run_fault``) hit exactly that. Rebuild rather than guess.
            if self.network._needs_path_rebuild():
                logger.warning(
                    "The transient mutual coupling is derived from the "
                    "source-to-fault paths, but the network's paths are "
                    "missing or stale -- rebuilding them now. Call "
                    "gi.run_fault(...) or gi.create_paths(...) before the "
                    "transient simulation so the stationary and the "
                    "transient result rest on the same path set."
                )
                self.network.invalidate_paths()
                self.network.define_paths()

            # The transient solvers treat the source *waveform* as the literal
            # injection; ``fault.scalings`` (a frequency-domain concept for the
            # stationary solve) is intentionally NOT applied here so the
            # mutual-coupling phase current stays consistent with the unscaled
            # shield injection. The FFT solver behaves the same way.
            voltage_src_skipped: List[str] = []
            for path in self.network.paths.values():
                if path.fault != self.fault_name:
                    continue
                src_name = path.source
                if src_name not in self._source_waveforms:
                    continue
                src_obj = self.network.sources[src_name]
                if src_obj.source_type != "current":
                    if src_name not in voltage_src_skipped:
                        voltage_src_skipped.append(src_name)
                    continue
                source_bus = src_obj.bus
                current_bus = source_bus
                for branch in path.segments:
                    br_name = branch.name
                    if branch.from_bus == current_bus:
                        sign = +1
                        next_bus = branch.to_bus
                    elif branch.to_bus == current_bus:
                        sign = -1
                        next_bus = branch.from_bus
                    else:
                        raise RuntimeError(
                            f"Path '{path.name}' segment '{br_name}' does "
                            f"not connect to bus '{current_bus}'"
                        )
                    if br_name in mutual_branches:
                        key = (br_name, src_name)
                        # First appearance wins -- deliberately *not* ``+=``.
                        # ``key`` carries the source, so the guard spans every
                        # path of that source, exactly like ``branch_signs`` in
                        # :meth:`ElectricalNetwork._compute_phase_currents_from_paths`.
                        # An earlier revision accumulated with a merely
                        # per-path ``seen`` set, so a branch shared by two
                        # parallel routes of one source (e.g. the common
                        # trunk of a ring) received a factor of 2.0 instead
                        # of 1.0. That double-counted the Carson coupling and
                        # put the transient EPR 32 % above the stationary
                        # solver at the fault bus on a five-bus ring; with
                        # the ring opened, so that only one path remained,
                        # the two solvers agreed to 1e-6 -- which is what
                        # made the defect invisible in the existing tests.
                        if key not in phase_factors:
                            coeff = branch.parallel_coefficient
                            if coeff is None:
                                coeff = 1.0
                            phase_factors[key] = sign * coeff
                    current_bus = next_bus
            if voltage_src_skipped:
                logger.warning(
                    "State-space mutual coupling skipped for voltage "
                    "sources: %s. The state-space solver currently models "
                    "the Carson-style coupling only for current-source "
                    "phase currents.",
                    voltage_src_skipped,
                )

            for (br_name, src_name), factor in phase_factors.items():
                info = mutual_branches[br_name]
                src_col = src_names.index(src_name)
                M_per_L = info["M_mut"] / info["L_self"]
                K_mut = (
                    info["R_self"] * info["M_mut"]
                    - info["R_mut"] * info["L_self"]
                ) / (info["L_self"] ** 2)

                # KCL feedforward: substitution moves -B_L * (M/L_self) * I_p
                # to the input side. Since the branch state convention is
                # to->from (+1 at to, -1 at from in B_L), the contribution
                # at the to-bus is +M_per_L*factor and at the from-bus is
                # -M_per_L*factor.
                B_kcl[info["to_idx"], src_col] += M_per_L * factor
                B_kcl[info["from_idx"], src_col] -= M_per_L * factor
                # EMF in dz/dt
                B_emf[info["state_idx"], src_col] += K_mut * factor

        # Stored for the observation-time correction below.
        self.__mutual_for_output = (mutual_branches, phase_factors, src_names)

        # --- partition for capacitor elimination -------------------------
        # If there are no capacitor states the partitioning collapses to
        # the simpler n_C = 0 form -- the assembled matrices below remain
        # valid in either case.
        G_RR = G_a[np.ix_(bus_R_states_full_idx, bus_R_states_full_idx)]
        G_RC = G_a[np.ix_(bus_R_states_full_idx, bus_C_full_idx)]
        G_CR = G_a[np.ix_(bus_C_full_idx, bus_R_states_full_idx)]
        G_CC = G_a[np.ix_(bus_C_full_idx, bus_C_full_idx)]

        B_L_R = B_L[bus_R_states_full_idx, :]
        B_L_C = B_L[bus_C_full_idx, :]
        M_R = M[:, bus_R_states_full_idx]
        M_C = M[:, bus_C_full_idx]
        B_kcl_R = B_kcl[bus_R_states_full_idx, :]
        B_kcl_C = B_kcl[bus_C_full_idx, :]

        if n_R > 0:
            try:
                G_RR_inv = np.linalg.inv(G_RR)
            except np.linalg.LinAlgError as exc:
                raise ValueError(
                    "Resistive sub-conductance G_RR is singular; check "
                    "that every non-capacitive bus has a finite resistive "
                    "grounding path."
                ) from exc
        else:
            G_RR_inv = np.zeros((0, 0))

        # Helper products used below.
        M_R_GRR = M_R @ G_RR_inv if n_R > 0 else np.zeros((n_L, 0))
        G_CR_GRR = G_CR @ G_RR_inv if n_R > 0 else np.zeros((n_C, 0))

        # --- assemble A and B for x = [x_L; v_C], input = source waveforms
        A_LL = N_mat - M_R_GRR @ B_L_R
        A_LC = M_C - M_R_GRR @ G_RC
        if n_C > 0:
            C_diag_inv = np.diag([1.0 / bus_C[name] for name in bus_C_states])
            A_CL = C_diag_inv @ (G_CR_GRR @ B_L_R - B_L_C)
            A_CC = C_diag_inv @ (G_CR_GRR @ G_RC - G_CC)
            B_C_total = C_diag_inv @ (B_kcl_C - G_CR_GRR @ B_kcl_R)
        else:
            A_CL = np.zeros((0, n_L))
            A_CC = np.zeros((0, 0))
            B_C_total = np.zeros((0, n_src))

        B_L_total = M_R_GRR @ B_kcl_R + B_emf

        A = np.block([[A_LL, A_LC], [A_CL, A_CC]])
        B = np.block([[B_L_total], [B_C_total]])

        # --- output: bus voltages at observed buses -----------------------
        # For non-capacitive observed bus k:
        #   v_k = G_RR_inv[k_R,:] @ (B_kcl_R @ u - G_RC @ v_C - B_L_R @ x_L)
        # For capacitive observed bus k: v_k = v_C state directly.
        full_to_R = {full_idx: i for i, full_idx in enumerate(bus_R_states_full_idx)}
        full_to_C = {full_idx: i for i, full_idx in enumerate(bus_C_full_idx)}

        obs_bus_rows: Dict[str, int] = {}
        C_rows: List[np.ndarray] = []
        D_rows: List[np.ndarray] = []
        for bus_name in self._obs_buses:
            if bus_name not in bus_index:
                continue
            full_idx = bus_index[bus_name]
            obs_bus_rows[bus_name] = len(C_rows)
            if full_idx in full_to_C:
                # State-direct readout from v_C.
                row_xL = np.zeros(n_L)
                row_vC = np.zeros(n_C)
                row_vC[full_to_C[full_idx]] = 1.0
                C_rows.append(np.concatenate([row_xL, row_vC]))
                D_rows.append(np.zeros(n_src))
            else:
                # Algebraic: v_k as a function of state and input.
                k_R = full_to_R[full_idx]
                gri = G_RR_inv[k_R, :]
                row_xL = -gri @ B_L_R
                row_vC = -gri @ G_RC if n_C > 0 else np.zeros(n_C)
                C_rows.append(np.concatenate([row_xL, row_vC]))
                D_rows.append(gri @ B_kcl_R)

        # --- time grid and source samples ---------------------------------
        n_samples = int(round(t_end / dt)) + 1
        t = np.arange(n_samples) * dt
        u = np.zeros((n_samples, n_src))
        signals_t: Dict[str, np.ndarray] = {}
        for col, src_name in enumerate(src_names):
            sig = np.asarray(self._source_waveforms[src_name](t), dtype=float)
            if sig.shape != t.shape:
                raise ValueError(
                    f"Waveform for source '{src_name}' returned shape "
                    f"{sig.shape}, expected {t.shape}."
                )
            u[:, col] = sig
            signals_t[src_name] = sig

        # --- integrate ----------------------------------------------------
        from scipy.signal import lsim

        # Integrate with identity output to recover the full state x(t),
        # then derive observation outputs by applying the C/D rows.
        sys_x = (
            np.asarray(A),
            np.asarray(B),
            np.eye(n_state),
            np.zeros((n_state, n_src)),
        )
        _, _, x_out = lsim(sys_x, U=u, T=t)
        if x_out.ndim == 1:
            x_out = x_out.reshape(-1, 1)

        # --- pack EPR observations ----------------------------------------
        epr_t: Dict[str, List[float]] = {}
        for bus_name in self._obs_buses:
            if bus_name not in obs_bus_rows:
                epr_t[bus_name] = [0.0] * n_samples
                continue
            row = C_rows[obs_bus_rows[bus_name]]
            d = D_rows[obs_bus_rows[bus_name]]
            epr_t[bus_name] = (x_out @ row + u @ d).tolist()

        # --- pack branch current observations -----------------------------
        # Compute the full bus voltage trajectory once; needed for
        # resistive observed branches and to ignore inductive ones cleanly.
        full_v_xL = np.zeros((n_bus, n_L))
        full_v_vC = np.zeros((n_bus, n_C))
        full_v_u = np.zeros((n_bus, n_src))
        # Capacitive buses: v_k = v_C state directly.
        for k_C, full_idx in enumerate(bus_C_full_idx):
            full_v_vC[full_idx, k_C] = 1.0
        # Non-capacitive buses: v_R = G_RR_inv (B_kcl_R u - G_RC v_C - B_L_R x_L).
        if n_R > 0:
            full_v_xL[bus_R_states_full_idx, :] = -G_RR_inv @ B_L_R
            if n_C > 0:
                full_v_vC[bus_R_states_full_idx, :] = -G_RR_inv @ G_RC
            full_v_u[bus_R_states_full_idx, :] = G_RR_inv @ B_kcl_R

        # v_full[t, bus] = x_L_part + v_C_part + u_part
        x_L_part = x_out[:, :n_L]
        v_C_part = x_out[:, n_L:]
        v_full = (
            x_L_part @ full_v_xL.T + v_C_part @ full_v_vC.T + u @ full_v_u.T
        )

        i_branch_t: Dict[str, List[float]] = {}
        # State index in x_out for each "real" branch inductor (skip the
        # synthetic voltage-source loop branches).
        ind_state_offset = {}
        for idx, entry in enumerate(branch_inductive):
            br_name = entry[0]
            if br_name.startswith("__vsrc__"):
                continue
            ind_state_offset[br_name] = n_bus_L + idx
        res_branch_lookup = {
            name: (fi, ti, G) for (name, fi, ti, G) in branch_resistive
        }
        for branch_name in self._obs_branches:
            if branch_name in ind_state_offset:
                # Inductive branch state. With the mutual substitution
                # ``z = i_shield + (M/L_self) * I_phase`` the observable
                # shield current is ``i_shield = z - (M/L_self) * I_phase``.
                i_observed = x_out[:, ind_state_offset[branch_name]].copy()
                mut_branches, phase_facts, _ = self.__mutual_for_output
                if branch_name in mut_branches:
                    info = mut_branches[branch_name]
                    M_per_L = info["M_mut"] / info["L_self"]
                    for src_col, src_name in enumerate(src_names):
                        factor = phase_facts.get((branch_name, src_name), 0.0)
                        if factor != 0.0:
                            i_observed -= M_per_L * factor * u[:, src_col]
                i_branch_t[branch_name] = i_observed.tolist()
            elif branch_name in res_branch_lookup:
                fi, ti, G = res_branch_lookup[branch_name]
                i_branch_t[branch_name] = (
                    (v_full[:, ti] - v_full[:, fi]) * G
                ).tolist()
            else:
                i_branch_t[branch_name] = [0.0] * n_samples

        return ResultTransient(
            time_s=t.tolist(),
            epr_t=epr_t,
            i_branch_t=i_branch_t,
            source_t={name: sig.tolist() for name, sig in signals_t.items()},
            fault=self.fault_name,
            solver="state_space",
        )

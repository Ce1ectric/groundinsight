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
  ignores mutual impedance and says so: meeting a coupled branch it
  raises a :class:`~groundinsight.models.core_models.TransientModelWarning`,
  because the same network solved stationarily or with the state-space
  solver returns a lower EPR at the fault (49.2 V against 77.6 V on a
  coupled two-bus cable link) and a silent difference of that size is
  indistinguishable from a modelling result. The FFT answer is the
  conservative one at the fault bus.
* Only sources that can actually reach the fault inject. The stationary
  solver filters its sources through the source-to-fault paths; the FFT
  solver reproduces that with a breadth-first reachability search over
  the active branches, so a galvanically isolated source contributes
  0 V in both solvers instead of its full spectrum in one of them.
"""

import logging
import warnings
from typing import Callable, Dict, List, Optional, Sequence, Tuple, TYPE_CHECKING

import numpy as np
from pydantic import BaseModel, ConfigDict, Field

from groundinsight.diagnostics import ModelAssumptionWarning, emit
from groundinsight.electrical_network import ElectricalNetwork
from groundinsight.models.core_models import Network, TransientModelWarning
from groundinsight.utils.compat import renamed_parameters

if TYPE_CHECKING:
    import polars as pl  # forward reference for type annotations only
from groundinsight.utils.impedance_calculator import (
    check_passive_impedance_array,
    compute_impedance_array,
    dc_substitute_impedance,
    is_short_circuit,
)


logger = logging.getLogger(__name__)


WaveformFunc = Callable[[np.ndarray], np.ndarray]


class _ActiveFaultView:
    """Read-only stand-in for a :class:`Network` with another active fault.

    The phase-network solve of the stationary solver reads the fault bus --
    the reference node of the phase network -- from ``network.active_fault``.
    A transient study names its own fault, which need not be the active one.
    Assigning ``network.active_fault`` for the duration of the solve is not
    side-effect free: ``Network`` validates assignments, and its
    after-validator re-synchronises every ``Fault``'s active flag, which a
    restore to ``None`` does not undo. Handing the stationary assembly this
    view instead leaves the user's network untouched, also when the solve
    raises half-way.

    Parameters
    ----------
    network : Network
        The network to read from.
    fault_name : str
        The fault to report as active.

    Raises
    ------
    AttributeError
        On any attempt to set an attribute through the view.
    """

    __slots__ = ("_network", "active_fault")

    def __init__(self, network: Network, fault_name: str):
        object.__setattr__(self, "_network", network)
        object.__setattr__(self, "active_fault", fault_name)

    def __getattr__(self, name: str):
        return getattr(self._network, name)

    def __setattr__(self, name: str, value) -> None:
        raise AttributeError(
            f"Cannot set '{name}': the transient phase-split probe reads the "
            f"network through a read-only view."
        )


class _UnitInjectionProbe(ElectricalNetwork):
    """The stationary ``"auto"`` phase-network solve with 1 A per source.

    The state-space solver needs the *per-unit* phase split -- the phase
    current of each branch per ampere of each source -- because the source
    waveform, not ``Source.values``, is the injection of a transient. The
    split is a property of the phase network alone, so it is taken from the
    very method ``run_fault(..., phase_current_mode="auto")`` uses,
    :meth:`ElectricalNetwork._compute_phase_currents`, with every source
    injecting exactly 1 A. Dividing the stationary result by the stationary
    source value would do the same, but fails for a source whose
    ``values`` miss ``f_eval`` or are zero there -- legitimate for a source
    that only exists to carry a transient waveform.

    Parameters
    ----------
    network : Network
        The network; it is read through an :class:`_ActiveFaultView` and not
        modified.
    fault_name : str
        The fault whose bus is the reference node of the phase network.

    Notes
    -----
    Construction runs the full stationary assembly at every network
    frequency, so the proxy warning for a meshed phase network without
    ``phase_impedance_formula`` is raised here exactly as in ``run_fault``
    -- the transient split rests on the same proxy.
    """

    def __init__(self, network: Network, fault_name: str):
        super().__init__(
            _ActiveFaultView(network, fault_name), auto_phase_currents=True
        )

    def _source_injection(self, source, freq: float, scaling: float) -> complex:
        """Inject exactly 1 A regardless of source type, value and scaling.

        Parameters
        ----------
        source : Source
            Ignored.
        freq : float
            Ignored.
        scaling : float
            Ignored.

        Returns
        -------
        complex
            ``1 + 0j``.
        """
        return complex(1.0, 0.0)


def _passivity_message(
    element: str, field: str, value: float, quantity: str
) -> str:
    """Compose the rejection message for a negative lumped RLC value.

    The state-space solver inverts or integrates every one of these values.
    A negative resistance generates energy, a negative inductance or
    capacitance has no physical realisation at all, and the pre-0.6.0 code
    silently *dropped* such an element via its ``> 0`` guards -- so a sign
    error in a fitted formula came back as a lossless network rather than
    as a message.

    Parameters
    ----------
    element : str
        How to name the element, e.g. ``"branch 'L12'"``.
    field : str
        The model field, e.g. ``"R_self"``.
    value : float
        The offending value.
    quantity : str
        The physical quantity, e.g. ``"resistance"``.

    Returns
    -------
    str
        The full message for the ``ValueError``.
    """
    return (
        f"The state-space solver rejects {element}: {field} = {value:g} is "
        f"negative. A passive earthing element cannot have a negative "
        f"{quantity}. Up to 0.6.0 such a value was skipped by the "
        f"positivity guards and the element simply vanished from the model, "
        f"which reads as a lossless -- and therefore never decaying -- "
        f"network rather than as the formula error it is. Check the "
        f"*_formula on the type at the lowest rho and the shortest length "
        f"in the model."
    )


def _time_grid(t_end: float, dt: float) -> Tuple[np.ndarray, int]:
    """Build the time grid both transient solvers run on.

    Until 0.6.0 the two solvers derived their own grid: the FFT solver used
    ``round(t_end/dt)`` samples rounded *up* to an even count, the state-space
    solver ``round(t_end/dt) + 1``. The two therefore differed in length by one
    or two samples for the same ``(t_end, dt)``, so the cheapest cross-check
    the package has -- comparing the two results element-wise -- failed on the
    array shape, and the extra state-space sample sat one ``dt`` beyond the
    window the FFT had solved.

    One grid for both removes that. ``N = round(t_end/dt)``, rounded *up* to an
    even count, over ``t = 0 ... (N-1)*dt``. Two properties are deliberate:

    * ``N*dt >= t_end``: the requested duration is the *period* of the grid,
      never less than asked for. It is the period rather than the last sample
      because the DFT treats the trace as periodic -- a source that repeats
      with ``t_end`` stays exactly periodic in the window, which is what keeps
      a 50 Hz excitation on a bin instead of spreading it over two.
    * ``N`` even, because ``rfft``/``irfft`` round-trip cleanly with the
      default normalisation only then. The adjustment moves the end of the
      window by one ``dt`` and is logged rather than applied silently.

    Parameters
    ----------
    t_end : float
        Length of the simulated window in seconds.
    dt : float
        Sample spacing in seconds.

    Returns
    -------
    tuple of (numpy.ndarray, int)
        The time samples ``0, dt, ..., (N-1)*dt`` and their count ``N``.

    Raises
    ------
    ValueError
        If ``t_end`` or ``dt`` is not strictly positive.
    """
    if t_end <= 0 or dt <= 0:
        raise ValueError("t_end and dt must be strictly positive.")
    n_samples = int(round(t_end / dt))
    if n_samples % 2 == 1:
        n_samples += 1
        logger.debug(
            "Transient time grid extended from %d to %d samples so N is even; "
            "the window is %g s instead of the requested %g s, dt unchanged "
            "at %g s.",
            n_samples - 1,
            n_samples,
            n_samples * dt,
            t_end,
            dt,
        )
    return np.arange(n_samples) * dt, n_samples


def _validated_block_bytes(value) -> Optional[int]:
    """Check the ``fft_block_bytes`` argument of :meth:`TransientStudy.solve`.

    Parameters
    ----------
    value : int or None
        The caller's byte budget for one batch of FFT admittance matrices.

    Returns
    -------
    int or None
        ``value`` as a plain ``int``, or ``None`` for the default.

    Raises
    ------
    TypeError
        If ``value`` is not an integer (``bool`` included, which would
        silently mean a budget of one byte).
    ValueError
        If ``value`` is not positive.
    """
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, np.integer)):
        raise TypeError(
            f"fft_block_bytes must be a positive integer number of bytes or "
            f"None, got {value!r} ({type(value).__name__})."
        )
    if value <= 0:
        raise ValueError(
            f"fft_block_bytes must be a positive number of bytes, got {value}."
        )
    return int(value)


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
            ``value``. Rows are grouped by signal kind and then by name,
            in the insertion order of the result dictionaries, with the
            time axis running inside each group.

        Notes
        -----
        Assembled one *series* at a time -- the time axis and the values as
        NumPy columns, the two label columns as Polars literals -- and
        stacked with ``pl.concat``. A transient of 20 000 samples over
        25 buses and 24 branches is a million rows, and materialising a
        million single-row Python dictionaries to hand them to Polars took
        1.0 s and several hundred MB of temporary objects. Expanding the
        labels inside Polars instead keeps the whole conversion in Arrow:
        the same million-row frame is built in ~0.02 s.
        """
        import polars as pl

        schema = {
            "time_s": pl.Float64,
            "signal_kind": pl.Utf8,
            "name": pl.Utf8,
            "value": pl.Float64,
        }
        time = np.asarray(self.time_s, dtype=float)

        frames: List["pl.DataFrame"] = []
        for kind, table in (
            ("epr", self.epr_t),
            ("i_branch", self.i_branch_t),
            ("source", self.source_t),
        ):
            for name, series in table.items():
                frames.append(
                    pl.DataFrame(
                        {
                            "time_s": time,
                            "value": np.asarray(series, dtype=float),
                        }
                    )
                    .with_columns(
                        pl.lit(kind, dtype=pl.Utf8).alias("signal_kind"),
                        pl.lit(name, dtype=pl.Utf8).alias("name"),
                    )
                    .select(list(schema))
                )

        if not frames:
            return pl.DataFrame(schema=schema)
        return pl.concat(frames)


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
        The network model. Buses, branches, sources and faults are taken
        from this object. Nothing is written back -- no impedance, no
        ``active`` flag, no stored result, no ``network.paths`` and not
        ``network.active_fault``. Up to audit pass 18 the state-space solver
        read (and, when they were missing, rebuilt) the source-to-fault
        paths for its mutual coupling; since pass 19 it takes the phase
        split from the stationary phase-network solve instead, which needs
        no path set.
    fault : str
        Name of the fault this study simulates. It does not have to be the
        network's active fault. It is kept as the attribute ``fault_name``.
        The former keyword ``fault_name`` is accepted as a deprecated alias.

    Examples
    --------
    >>> import groundinsight as gi  # doctest: +SKIP
    >>> from groundinsight.simulation import waveforms  # doctest: +SKIP
    >>> study = gi.TransientStudy(network, fault='F1')  # doctest: +SKIP
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

    @renamed_parameters(fault_name="fault")
    def __init__(self, network: Network, fault: str):
        if fault not in network.faults:
            raise ValueError(
                f"Fault '{fault}' does not exist in the network."
            )
        self.network = network
        self.fault_name = fault
        self._source_waveforms: Dict[str, WaveformFunc] = {}
        self._obs_buses: List[str] = []
        self._obs_branches: List[str] = []
        # Stand-in impedance for elements that are an ideal short circuit at
        # the 0 Hz bin, and the index of that bin. Both stay ``None`` unless
        # the FFT solver actually meets such an element. See
        # :meth:`_dc_substitute_for` and :meth:`_resolved_z_stack`.
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
        f_eval: Optional[float] = None,
        zero_pad: bool = False,
        fft_block_bytes: Optional[int] = None,
    ) -> ResultTransient:
        """
        Run the transient solve and return the resulting time series.

        Parameters
        ----------
        t_end : float
            End time of the simulation in seconds. ``t=0`` is always the
            start. Both solvers use the same grid (see
            :func:`_time_grid`), which may extend by one sample so the
            sample count is even.
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
        f_eval : float, optional
            *State-space solver only.* The frequency in Hz at which the
            lumped ``R`` / ``L`` / ``C`` values and the Thevenin source
            impedance are read. The state-space model treats them as
            frequency-independent, so this picks the operating point.
            Must be one of ``network.frequencies``. Defaults to the
            **smallest strictly positive** entry of ``network.frequencies``
            rather than to its first element: with ``[0, 50]`` the first
            element is 0 Hz, where ``L_src = Im(Z_src)/omega`` is not
            defined and used to silently come out as zero -- a factor of
            680 on the EPR of the reference case, decided by nothing but
            the order of the frequency list.
        zero_pad : bool, optional
            *FFT solver only.* Solve on a window twice as long and return
            only its first half. The DFT is periodic, so a response that
            has not decayed by ``t_end`` wraps around into the start of
            the trace; doubling the window pushes the wrap out of the
            reported interval. Costs twice the solve time. Defaults to
            ``False``.
        fft_block_bytes : int, optional
            *FFT solver only.* Upper bound in bytes for one batch of dense
            complex admittance matrices (``16 * n**2`` bytes per frequency
            bin for ``n`` active buses). The solver assembles and solves at
            most 512 bins per batch; above about 181 buses this budget
            decides instead, so memory stays bounded for any network size --
            the fixed 512-bin batch took 1.3 GB at 400 buses and 8.2 GB at
            1000. The result does not depend on it (every bin is solved on
            its own). A very small budget costs time on mid-size networks,
            because every batch is one pass of the element-wise assembly:
            150 buses and 1001 bins took 4.5 s at 1 MiB (two bins per batch)
            against 0.7 s at the default. A single matrix larger than the
            budget is solved one bin at a time and logged. Defaults to
            256 MiB.

        Returns
        -------
        ResultTransient
            Time-domain results at the observation points.

        Raises
        ------
        ValueError
            If no source waveform is set, if the time parameters are
            invalid, if ``f_eval`` is not one of ``network.frequencies``,
            if ``fft_block_bytes`` is not positive, or if the network is
            missing the lumped RLC fields required by the state-space
            solver.
        TypeError
            If ``fft_block_bytes`` is not an integer.
        NotImplementedError
            If ``solver`` is not one of the supported identifiers.

        Warns
        -----
        TransientModelWarning
            If the network carries mutual coupling the FFT solver cannot
            evaluate, if the active fault carries per-frequency
            ``scalings`` other than 1 (both solvers apply the source
            waveform as given and ignore them), or if the FFT window ends
            while the response is still ringing.
        ModelAssumptionWarning
            Code ``TRANSIENT_PHASE_SPLIT``, state-space solver only: the
            phase current of a meshed network divides between routes of
            different R/L ratio, so the split the coupling uses -- exact at
            ``f_eval`` -- differs at the other frequencies in the waveform.
        """
        block_bytes = _validated_block_bytes(fft_block_bytes)
        if solver == "fft":
            return self._solve_fft(
                t_end=t_end, dt=dt, zero_pad=zero_pad, block_bytes=block_bytes
            )
        if solver == "state_space":
            return self._solve_state_space(t_end=t_end, dt=dt, f_eval=f_eval)
        raise NotImplementedError(
            f"Unknown transient solver: '{solver}'. "
            "Supported: 'fft', 'state_space'."
        )

    # ------------------------------------------------------------------
    # internals
    # ------------------------------------------------------------------

    def _solve_fft(
        self,
        *,
        t_end: float,
        dt: float,
        zero_pad: bool = False,
        block_bytes: Optional[int] = None,
    ) -> ResultTransient:
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
        t_out, n_out = _time_grid(t_end, dt)
        if n_out < 4:
            raise ValueError(
                "t_end / dt yields fewer than four samples; choose a finer dt."
            )
        # ``zero_pad`` doubles the *solved* window and reports only its first
        # half, which moves the periodic wraparound of the DFT out of the
        # interval the user looks at. The waveform is sampled over the whole
        # doubled window rather than zeroed on its second half: a step that is
        # still on at ``t_end`` is still on afterwards, and forcing it to zero
        # would inject a switching edge that is not in the model.
        n_samples = 2 * n_out if zero_pad else n_out
        t = np.arange(n_samples) * dt
        freqs = np.fft.rfftfreq(n_samples, d=dt)
        n_freqs = freqs.size

        self._warn_about_ignored_mutual_coupling()
        self._warn_about_fault_scalings()

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

        # --- sources that can reach the fault ------------------------------
        # Same rule as the stationary solver, which injects only sources with
        # a path to the active fault. Without it a source on a galvanically
        # isolated bus injected its full spectrum there *and* subtracted it
        # again at the fault bus, so every bus in the network came out wrong.
        injecting = self._sources_reaching_fault(
            list(source_spectra), bus_index, fault_bus
        )
        source_spectra = {
            name: spectrum
            for name, spectrum in source_spectra.items()
            if name in injecting
        }

        # --- per-frequency solve -------------------------------------------
        # Assembled and solved in blocks of bins: LAPACK is called once per
        # block on a stacked ``(K, n, n)`` array instead of once per bin. K is
        # bounded by the bin cap and by the byte budget (P8): a fixed 512-bin
        # block grew as 512 * n**2 * 16 B -- 1.3 GB at 400 buses, 8.2 GB at
        # 1000 -- and ran out of memory before it ran out of time.
        i_spectrum = self._build_i_stack(
            bus_index, source_spectra, fault_bus_idx, n_freqs
        )
        block = self._fft_block_bins(n_buses, block_bytes)
        budget = self._FFT_BLOCK_BYTES if block_bytes is None else block_bytes
        if 16 * n_buses * n_buses > budget:
            logger.warning(
                "One dense %d x %d admittance matrix needs %.1f MiB, more than "
                "fft_block_bytes = %.1f MiB; the FFT solver solves one bin at "
                "a time and needs about twice that matrix size.",
                n_buses,
                n_buses,
                16 * n_buses * n_buses / 2**20,
                budget / 2**20,
            )
        u_spectrum = self._solve_spectrum(
            bus_index, bus_Z, branch_Z, i_spectrum, freqs, block=block
        )

        # --- IFFT for each observation point -------------------------------
        # ``[:n_out]`` is the identity unless ``zero_pad`` doubled the window.
        epr_t: Dict[str, List[float]] = {}
        epr_full: Dict[str, np.ndarray] = {}
        for bus_name in self._obs_buses:
            if bus_name not in bus_index:
                # Inactive bus -> all-zero response, but stay in the result
                # to make the contract explicit.
                epr_t[bus_name] = [0.0] * n_out
                continue
            spec = u_spectrum[bus_index[bus_name], :]
            signal = np.fft.irfft(spec, n=n_samples)
            epr_full[bus_name] = signal
            epr_t[bus_name] = signal[:n_out].tolist()

        i_branch_t: Dict[str, List[float]] = {}
        for branch_name in self._obs_branches:
            branch = self.network.branches.get(branch_name)
            if branch is None or branch_name not in branch_Z:
                i_branch_t[branch_name] = [0.0] * n_out
                continue
            from_idx = bus_index[branch.from_bus]
            to_idx = bus_index[branch.to_bus]
            all_bins = np.arange(n_freqs)
            y = self._admittance_stack(branch_Z[branch_name], all_bins)
            i_spec = (u_spectrum[to_idx, :] - u_spectrum[from_idx, :]) * y
            i_branch_t[branch_name] = np.fft.irfft(i_spec, n=n_samples)[
                :n_out
            ].tolist()

        self._check_fft_periodicity(epr_full, zero_pad=zero_pad)

        return ResultTransient(
            time_s=t_out.tolist(),
            epr_t=epr_t,
            i_branch_t=i_branch_t,
            source_t={
                name: sig[:n_out].tolist()
                for name, sig in source_signals_t.items()
            },
            fault=self.fault_name,
            solver="fft",
        )

    # ------------------------------------------------------------------
    # FFT solver guards
    # ------------------------------------------------------------------

    _PERIODICITY_TOLERANCE: float = 0.01

    def _check_fft_periodicity(
        self, signals: Dict[str, np.ndarray], *, zero_pad: bool
    ) -> None:
        """Warn when the FFT window ends while the response is still ringing.

        The DFT treats the trace as one period of a periodic signal. If the
        impulse response has not decayed by the end of the solved window, the
        tail wraps around into the beginning -- which is where the user reads
        the pre-fault state. Measured on an R-parallel-L bus with a step at
        0.1 s and a 0.4 s window: 45.9 V at ``t = 0``, i.e. before the fault,
        and a post-fault peak 37 % below the state-space answer. With the
        window at 4 s the two solvers agree to 0.01 %.

        The test is deliberately crude and cheap: the last sample of the
        solved window, compared against the peak of the same signal. A
        decayed response ends near zero; a ringing one does not. It is
        evaluated on the *solved* window, so it still fires when
        ``zero_pad`` doubled the window and the response has not decayed
        even there.

        Parameters
        ----------
        signals : dict of str to numpy.ndarray
            The reconstructed EPR of every observed active bus, over the
            full solved window.
        zero_pad : bool
            Whether the window was already doubled, which changes the advice.
        """
        offenders: List[str] = []
        for name, signal in signals.items():
            peak = float(np.max(np.abs(signal))) if signal.size else 0.0
            if peak <= 0.0:
                continue
            if abs(float(signal[-1])) > self._PERIODICITY_TOLERANCE * peak:
                offenders.append(name)
        if not offenders:
            return
        shown = ", ".join(sorted(offenders)[:3])
        if len(offenders) > 3:
            shown += f", ... ({len(offenders)} buses in total)"
        remedy = (
            "Increase t_end further"
            if zero_pad
            else "Increase t_end, or pass zero_pad=True to solve on a window "
            "twice as long and report only its first half"
        )
        warnings.warn(
            f"The FFT window ends while the response is still ringing at "
            f"{shown}: the last sample is more than "
            f"{self._PERIODICITY_TOLERANCE:.0%} of the peak. The DFT is "
            f"periodic, so that tail wraps around into the start of the "
            f"trace and shows up as a potential rise before the fault and as "
            f"a peak that is too small after it (measured -37 % on an R||L "
            f"bus with a 0.4 s window). {remedy}, until t_end is well beyond "
            f"the slowest time constant of the network.",
            TransientModelWarning,
            stacklevel=3,
        )

    def _warn_about_ignored_mutual_coupling(self) -> None:
        """Warn when the FFT solver meets coupling it does not evaluate.

        The FFT solver builds ``Y`` from the bus grounding impedances and the
        branch *self* impedances only; ``BranchType.mutual_impedance_formula``
        and the lumped ``R_mutual`` / ``M_mutual`` never enter it. The
        stationary solver and the state-space solver both do evaluate them, so
        the same network answers differently depending on which one is asked.
        That difference has to be visible, and its direction has to be right.

        Without the coupling no induced share of the fault current returns
        through the shields, so the EPR at the fault bus and the maximum over
        the network come out *higher* -- the FFT answer is conservative there.
        Measured: 77.6 V against 49.2 V in the steady state of a coupled
        two-bus cable link, exactly ``1/|1 - Z_m/Z_s|`` for one coupled
        branch; +44 % to +101 % on the post-inception peak of a fully offset
        fault current in four networks (two-bus link, chain, two rings);
        higher at the fault bus and as network maximum in all of 600 random
        chains and meshes with Carson-like parameters, while 6 % of the other
        buses came out lower. Up to audit pass 18 the message said "lower"
        and was not tied to any element (PH-L1).

        Warns
        -----
        TransientModelWarning
            Code ``TRANSIENT_MODEL``; elements: every coupled branch.
        """
        coupled: List[str] = []
        for name, branch in self.network.branches.items():
            if not branch.active or not branch.type.grounding_conductor:
                continue
            if branch.from_bus not in self.network.buses:
                continue
            has_mutual = any(
                complex(value.real, value.imag) != 0
                for value in (branch.mutual_impedance or {}).values()
            )
            if not has_mutual:
                has_mutual = any(
                    float(value) != 0.0
                    for table in (branch.M_mutual, branch.R_mutual)
                    if table
                    for value in table.values()
                )
            if has_mutual:
                coupled.append(name)
        if not coupled:
            return
        coupled = sorted(coupled)
        shown = ", ".join(coupled[:3])
        if len(coupled) > 3:
            shown += f", ... ({len(coupled)} branches in total)"
        emit(
            TransientModelWarning,
            f"The FFT transient solver does not evaluate mutual coupling, but "
            f"the network carries it on: {shown}. Its admittance matrices "
            f"hold the self impedances only, so the Norton-equivalent mutual "
            f"currents of the stationary solver are missing from this result. "
            f"Expect a higher earth potential rise at the fault bus, and a "
            f"higher maximum over the network, than gi.run_fault(...) and the "
            f"state-space solver report for the same network: without the "
            f"coupling no induced share of the fault current returns through "
            f"the shields, so the result is conservative there (+58 % in the "
            f"steady state of a coupled two-bus cable link, i.e. "
            f"1/|1 - Z_m/Z_s| for one coupled branch; +44 % on the peak of a "
            f"fully offset fault current). A bus away from the fault can still "
            f"come out lower. Use solver='state_space', which does model the "
            f"coupling for current sources.",
            code="TRANSIENT_MODEL",
            elements=[("branch", name) for name in coupled],
            stacklevel=4,
        )

    def _warn_about_fault_scalings(self) -> None:
        """Warn when the active fault carries per-frequency scalings.

        ``Fault.scalings`` weights the source phasors per frequency in the
        stationary solve. Both transient solvers take the source *waveform*
        as the literal injection instead -- the waveform already carries the
        spectral content, and applying a per-frequency factor on top of an
        FFT bin grid that has nothing to do with ``network.frequencies``
        would be meaningless. That is a deliberate decision, but it makes a
        factor-of-3 difference against the stationary result and was only
        ever recorded in an inline comment.
        """
        fault = self.network.faults[self.fault_name]
        scaled = {
            freq: value
            for freq, value in (fault.scalings or {}).items()
            if float(value) != 1.0
        }
        if not scaled:
            return
        shown = ", ".join(
            f"{freq:g} Hz: {value:g}" for freq, value in sorted(scaled.items())
        )
        warnings.warn(
            f"Fault '{self.fault_name}' carries scalings other than 1 "
            f"({shown}), and the transient solvers ignore them. The source "
            f"waveform passed to set_source_waveform(...) is used as the "
            f"literal injection, because the FFT bin grid is set by t_end and "
            f"dt and has no relation to network.frequencies. Scale the "
            f"waveform itself if the transient is meant to carry the same "
            f"weighting as the stationary solve.",
            TransientModelWarning,
            stacklevel=3,
        )

    def _sources_reaching_fault(
        self,
        source_names: Sequence[str],
        bus_index: Dict[str, int],
        fault_bus: str,
    ) -> List[str]:
        """Return the sources that are galvanically connected to the fault bus.

        Mirrors
        :meth:`~groundinsight.electrical_network.ElectricalNetwork._sources_with_path_to_active_fault`,
        which filters the stationary injections through ``network.paths``.
        The reachability is recomputed here with a breadth-first search over
        the *active* branches rather than read off the paths, so a transient
        study does not depend on a path set having been built first and does
        not trigger the path enumeration itself.

        The search walks every active branch whose two endpoints are both
        active -- exactly the graph :class:`~groundinsight.pathfinder.PathFinder`
        builds -- and *not* only the branches carrying a grounding conductor.
        A line without an earth wire still carries the phase current that
        drives the fault, which is why the stationary solver counts its
        source as feeding the fault too.

        Parameters
        ----------
        source_names : sequence of str
            Candidate source names.
        bus_index : dict of str to int
            The active buses of this solve.
        fault_bus : str
            Bus the active fault sits on.

        Returns
        -------
        list of str
            The subset of ``source_names`` that can reach ``fault_bus``,
            in the order of ``network.sources``.
        """
        adjacency: Dict[str, List[str]] = {name: [] for name in bus_index}
        for branch in self.network.branches.values():
            if not branch.active:
                continue
            if branch.from_bus not in bus_index or branch.to_bus not in bus_index:
                continue
            adjacency[branch.from_bus].append(branch.to_bus)
            adjacency[branch.to_bus].append(branch.from_bus)

        reachable = {fault_bus}
        queue = [fault_bus]
        while queue:
            current = queue.pop()
            for neighbour in adjacency.get(current, ()):
                if neighbour not in reachable:
                    reachable.add(neighbour)
                    queue.append(neighbour)

        candidates = set(source_names)
        keeping: List[str] = []
        dropped: List[str] = []
        for name in self.network.sources:
            if name not in candidates:
                continue
            bus = self.network.sources[name].bus
            if bus in reachable:
                keeping.append(name)
            else:
                dropped.append(name)
        if dropped:
            logger.warning(
                "Transient FFT solve: source(s) %s have no galvanic "
                "connection to fault bus '%s' and do not inject, matching "
                "the stationary solver's path filter.",
                dropped,
                fault_bus,
            )
        return keeping

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

    def _eval_bus_impedances(
        self, bus_index: Dict[str, int], freqs: List[float]
    ) -> Dict[str, np.ndarray]:
        """Return a complex impedance array per active bus over ``freqs``.

        Uses the raw array entry points of the impedance calculator rather
        than the ``{frequency: ComplexNumber}`` dictionary. The values go
        straight into ``Y``, so the dictionary was built and taken apart
        again one bin later: 490 050 model instances and 2.9 s of a 9.2 s
        solve on a 25-bus, 20 000-sample run.
        """
        out: Dict[str, np.ndarray] = {}
        freqs_arr = np.asarray(freqs, dtype=float)
        for name in bus_index:
            bus = self.network.buses[name]
            params = {"rho": bus.specific_earth_resistance}
            values = compute_impedance_array(
                bus.type.impedance_formula, freqs, params
            )
            check_passive_impedance_array(
                values,
                freqs_arr,
                element=f"bus '{name}' (grounding impedance, transient FFT grid)",
                formula_str=bus.type.impedance_formula,
                params=params,
            )
            out[name] = values
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
        freqs_arr = np.asarray(freqs, dtype=float)
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
            values = compute_impedance_array(
                branch.type.self_impedance_formula, freqs, params
            )
            check_passive_impedance_array(
                values,
                freqs_arr,
                element=f"branch '{name}' (self impedance, transient FFT grid)",
                formula_str=branch.type.self_impedance_formula,
                params=params,
            )
            out[name] = values
        return out

    #: Upper bound on the FFT bins assembled and solved in one batch. Large
    #: enough that the per-call overhead of LAPACK and of the Python assembly
    #: loop is amortised, small enough that the ``(K, n, n)`` block stays in
    #: cache for small networks (512 bins of a 25-bus network are 5 MB).
    _SOLVE_BLOCK: int = 512

    #: Default byte budget for one such batch (``fft_block_bytes``). Binds
    #: from 182 buses on; below that the 512-bin cap is the smaller bound.
    _FFT_BLOCK_BYTES: int = 256 * 2**20

    def _fft_block_bins(
        self, n_buses: int, budget_bytes: Optional[int] = None
    ) -> int:
        """Number of FFT bins per batch for ``n_buses`` and a byte budget.

        ``K = max(1, min(_SOLVE_BLOCK, budget // (16 * n**2)))``: one complex
        ``n x n`` matrix per bin, at most :attr:`_SOLVE_BLOCK` bins, at least
        one. The stacked solve copies one matrix at a time into LAPACK's
        work buffer, so the batch itself is the dominant allocation.

        Parameters
        ----------
        n_buses : int
            Number of active buses, i.e. the matrix dimension.
        budget_bytes : int, optional
            Byte budget; ``None`` uses :attr:`_FFT_BLOCK_BYTES`.

        Returns
        -------
        int
            Bins per batch, between 1 and :attr:`_SOLVE_BLOCK`.

        Examples
        --------
        >>> study._fft_block_bins(400)  # doctest: +SKIP
        104
        """
        budget = self._FFT_BLOCK_BYTES if budget_bytes is None else budget_bytes
        per_bin = max(16 * n_buses * n_buses, 1)
        return int(max(1, min(self._SOLVE_BLOCK, budget // per_bin)))

    def _solve_spectrum(
        self,
        bus_index: Dict[str, int],
        bus_Z: Dict[str, np.ndarray],
        branch_Z: Dict[str, np.ndarray],
        i_spectrum: np.ndarray,
        freqs: np.ndarray,
        *,
        block: int,
    ) -> np.ndarray:
        """Solve ``Y(f) u(f) = i(f)`` for every FFT bin, ``block`` bins at a time.

        Each block is assembled by :meth:`_build_y_stack` and handed to
        LAPACK as one stacked ``(K, n, n)`` array. Every bin is still an
        independent LU solve, so the block size changes the memory and the
        number of calls, not a single digit of the result. A block that
        contains a singular bin is redone bin by bin, so the "skip this bin"
        rule applies to exactly that bin and not to its neighbours.

        Parameters
        ----------
        bus_index : dict of str to int
            Active bus name to matrix index.
        bus_Z, branch_Z : dict of str to numpy.ndarray
            Impedance per element over all bins.
        i_spectrum : numpy.ndarray
            Injection per bin, shape ``(n_freqs, n)``.
        freqs : numpy.ndarray
            The bin frequencies in Hz (for the log message only).
        block : int
            Bins per batch, see :meth:`_fft_block_bins`.

        Returns
        -------
        numpy.ndarray
            Bus voltages, shape ``(n, n_freqs)``; zero in a singular bin.
        """
        n_freqs = freqs.size
        u_spectrum = np.zeros((len(bus_index), n_freqs), dtype=complex)
        for start in range(0, n_freqs, block):
            stop = min(start + block, n_freqs)
            bins = np.arange(start, stop)
            Y_block = self._build_y_stack(bus_index, bus_Z, branch_Z, bins)
            rhs = i_spectrum[start:stop, :, np.newaxis]
            try:
                u_spectrum[:, start:stop] = np.linalg.solve(
                    Y_block, rhs
                )[:, :, 0].T
            except np.linalg.LinAlgError:
                for offset, k in enumerate(bins):
                    try:
                        u_spectrum[:, k] = np.linalg.solve(
                            Y_block[offset], i_spectrum[k]
                        )
                    except np.linalg.LinAlgError:
                        # Singular at this bin (commonly f=0 with no DC
                        # return path). We leave u as zero; for a
                        # real-valued input signal the resulting
                        # time-domain response is just missing the DC
                        # component, which is what the user would expect.
                        logger.debug(
                            "Y(f=%g) is singular for the active fault; "
                            "skipping.",
                            freqs[k],
                        )
            del Y_block
        return u_spectrum

    def _admittance_stack(
        self, values: np.ndarray, bins: np.ndarray
    ) -> np.ndarray:
        """Turn a block of impedances into admittances, zero where unusable.

        Vectorised form of the rule :meth:`_build_y_at` applied per bin: a
        finite non-zero impedance contributes ``1/Z``; ``inf`` (a true pole,
        e.g. a capacitance at DC, or the documented ``"nan"`` open-end
        sentinel) contributes nothing, which *is* the correct physics -- no
        current flows.

        A short circuit (``Z == 0``, or a value so small that ``1/Z``
        overflows) is only reachable at the 0 Hz bin; above DC it is rejected
        by :meth:`_eval_bus_impedances` / :meth:`_eval_branch_self_impedances`.
        At 0 Hz it is legitimate physics -- an ideal inductance really is a
        short at DC -- and :meth:`_resolved_z_stack` substitutes the small
        finite stand-in of :meth:`_dc_substitute_for` for it, which reproduces
        the ideal bond to roughly five significant digits. Up to v0.4.0 such
        an element was skipped instead, i.e. a short modelled as an open
        circuit, which made the DC component of such a transient useless. An
        exact treatment requires merging the two buses into one, which is a
        modelling decision and is left to the user.

        Parameters
        ----------
        values : numpy.ndarray
            The element's impedance over *all* bins.
        bins : numpy.ndarray
            The bin indices of this block.

        Returns
        -------
        numpy.ndarray
            Complex admittances, one per bin of the block.
        """
        z = self._resolved_z_stack(values[bins], bins)
        usable = (z != 0) & np.isfinite(z)
        y = np.zeros_like(z)
        np.divide(1.0, z, out=y, where=usable)
        return y

    def _resolved_z_stack(
        self, values: np.ndarray, bins: np.ndarray
    ) -> np.ndarray:
        """Apply the 0 Hz stand-in over a block of bins.

        The single point where the DC substitute of
        :meth:`_dc_substitute_for` is applied. Outside the 0 Hz bin, and
        whenever the network has no DC short circuit at all, the values are
        handed back untouched -- in particular a true pole (``inf``, e.g. a
        capacitance at DC) stays ``inf`` and is treated as an open circuit
        by the callers, which is the correct physics.
        """
        z = np.array(values, dtype=complex, copy=True)
        if self._dc_substitute is None or self._dc_bin is None:
            return z
        at_dc = bins == self._dc_bin
        if not at_dc.any():
            return z
        finite = np.isfinite(z.real) & np.isfinite(z.imag)
        with np.errstate(over="ignore", invalid="ignore", divide="ignore"):
            admittance = np.divide(
                1.0, z, out=np.full_like(z, np.inf), where=z != 0
            )
        shorted = finite & ~(
            np.isfinite(admittance.real) & np.isfinite(admittance.imag)
        )
        z[at_dc & shorted] = complex(self._dc_substitute, 0.0)
        return z

    def _build_y_stack(
        self,
        bus_index: Dict[str, int],
        bus_Z: Dict[str, np.ndarray],
        branch_Z: Dict[str, np.ndarray],
        bins: np.ndarray,
    ) -> np.ndarray:
        """Assemble ``Y`` for a block of FFT bins as a ``(K, n, n)`` array.

        Same matrix, entry for entry, as the per-bin assembly it replaces --
        only the loop order is inverted. Before, the outer loop ran over the
        20 000 bins of a transient and the inner one over the elements, so
        the whole assembly ran in Python; now the outer loop runs over the
        elements and each contributes to all ``K`` bins in one NumPy
        operation. Measured on 25 buses and 20 000 samples: 1.9 s to 0.05 s.
        """
        n = len(bus_index)
        Y = np.zeros((bins.size, n, n), dtype=complex)
        for name, idx in bus_index.items():
            Y[:, idx, idx] += self._admittance_stack(bus_Z[name], bins)
        for name, branch in self.network.branches.items():
            if name not in branch_Z:
                continue
            y = self._admittance_stack(branch_Z[name], bins)
            fi = bus_index[branch.from_bus]
            ti = bus_index[branch.to_bus]
            Y[:, fi, fi] += y
            Y[:, ti, ti] += y
            Y[:, fi, ti] -= y
            Y[:, ti, fi] -= y
        return Y

    def _build_i_stack(
        self,
        bus_index: Dict[str, int],
        source_spectra: Dict[str, np.ndarray],
        fault_bus_idx: int,
        n_freqs: int,
    ) -> np.ndarray:
        """Build the injection vectors of every bin at once, as ``(n_freqs, n)``.

        Current sources only: ``+I`` at the source bus and ``-sum(I)`` at the
        fault bus, which is the same convention the stationary solver uses.
        """
        n = len(bus_index)
        i_stack = np.zeros((n_freqs, n), dtype=complex)
        total = np.zeros(n_freqs, dtype=complex)
        for src_name, spectrum in source_spectra.items():
            src = self.network.sources[src_name]
            if src.bus not in bus_index:
                continue
            i_stack[:, bus_index[src.bus]] += spectrum
            total += spectrum
        i_stack[:, fault_bus_idx] -= total
        return i_stack

    # ------------------------------------------------------------------
    # state-space solver
    # ------------------------------------------------------------------

    #: Prefix of the synthetic loop branches the state-space solver creates
    #: for Thevenin voltage sources. A real branch of the same name would be
    #: mistaken for one of them, so the prefix is reserved (see
    #: :meth:`_reject_reserved_branch_names`).
    _VSRC_PREFIX = "__vsrc__"

    def _reject_reserved_branch_names(self) -> None:
        """Reject real branches whose name starts with the ``__vsrc__`` sentinel.

        The state-space solver appends synthetic loop branches named
        ``__vsrc__<source>`` to ``branch_inductive`` and later tells them
        apart from real branches by that prefix alone. A real branch called
        ``__vsrc__x`` would be skipped where the synthetic ones are skipped
        (mutual coupling, branch-current output) and looked up in
        ``network.branches`` where they are not, which fails with a
        ``KeyError`` far from the cause. Rejecting the name up front is the
        cheap half of the fix; the expensive half -- carrying an explicit
        "synthetic" flag through the tuples -- buys nothing a user would see.

        Raises
        ------
        ValueError
            If any branch name starts with the reserved prefix.
        """
        clashing = sorted(
            name
            for name in self.network.branches
            if name.startswith(self._VSRC_PREFIX)
        )
        if clashing:
            raise ValueError(
                f"Branch name(s) {clashing} start with '{self._VSRC_PREFIX}', "
                f"which the state-space solver reserves for the synthetic loop "
                f"branches it builds for Thevenin voltage sources. Rename the "
                f"branch; the prefix is an internal sentinel and a real branch "
                f"carrying it would be silently treated as one of them."
            )

    def _resolve_f_eval(self, f_eval: Optional[float]) -> float:
        """Pick the frequency the lumped RLC values are read at.

        The state-space model has one set of ``R`` / ``L`` / ``C`` values, so
        one frequency of ``network.frequencies`` has to be chosen as the
        operating point. Until 0.6.0 that was ``frequencies[0]`` --
        the *list position*, not a physical choice. With ``[0, 50]`` the
        chosen frequency is 0 Hz, where ``L_src = Im(Z_src) / omega`` divides
        by zero and came out as a plain 0.0; the reference case's EPR moved
        from 0.24 V to 161 V purely by prepending a DC bin to the frequency
        list.

        The default is therefore the smallest strictly positive frequency,
        which is the lowest AC operating point the model was parameterised
        for. A network whose frequency list is exactly ``[0.0]`` still
        resolves to 0 Hz -- there is nothing else to choose -- and the
        divide-by-zero is caught where it happens.

        Parameters
        ----------
        f_eval : float or None
            The caller's choice, or ``None`` for the default.

        Returns
        -------
        float
            The frequency in Hz.

        Raises
        ------
        ValueError
            If the network has no frequencies, or if ``f_eval`` is not one
            of them.
        """
        frequencies = list(self.network.frequencies or [])
        if not frequencies:
            raise ValueError(
                "The network has no frequencies; the state-space solver needs "
                "at least one to read the lumped R/L/C values at."
            )
        if f_eval is None:
            positive = [freq for freq in frequencies if freq > 0.0]
            return float(min(positive)) if positive else float(min(frequencies))
        chosen = float(f_eval)
        if chosen not in frequencies:
            raise ValueError(
                f"f_eval = {chosen} Hz is not one of the network's frequencies "
                f"{frequencies}. The lumped R/L/C tables are evaluated at "
                f"exactly those frequencies, so a value in between has no "
                f"stored counterpart."
            )
        return chosen

    #: Per-unit share of a source current below which a branch's phase
    #: current is taken as zero. The phase-network solve leaves rounding of
    #: about 1e-16 on branches that carry nothing, e.g. a stub beyond the fault.
    _SPLIT_ZERO: float = 1e-12

    #: Per-unit difference above which the phase split counts as
    #: frequency-dependent: its quadrature part at ``f_eval``, or its change
    #: between two network frequencies. Far above rounding, far below
    #: anything an EPR would show.
    _SPLIT_TOLERANCE: float = 1e-6

    def _phase_split(
        self,
        f_eval: float,
        src_names: Sequence[str],
        coupled: Sequence[str],
    ) -> Dict[Tuple[str, str], complex]:
        """Per-unit phase current of every coupled branch, per current source.

        The state-space solver drives the Carson coupling of a shield with the
        phase current of its branch. Which share of a source's current a
        branch carries is decided by the phase-conductor network, and the
        stationary solver answers exactly that question in its default
        ``phase_current_mode="auto"``: one nodal solve of the phase network
        per source, the fault bus as reference. This method asks the same
        code (:class:`_UnitInjectionProbe`) for 1 A per source at ``f_eval``,
        so stationary and transient results rest on one split -- including
        the declared ``phase_impedance_formula``, its proxy, open phase
        conductors and sources that are isolated in the phase network.

        Up to audit pass 18 the factor was read off the enumerated
        source-to-fault paths as ``sign * parallel_coefficient`` instead, i.e.
        the legacy ``"paths"`` mode: every route of a ring carried the full
        source current, +103 % EPR at the fault of a symmetric four-bus ring
        (PH2). ``Branch.parallel_coefficient`` is no longer read here, just as
        the stationary ``"auto"`` mode does not read it.

        Parameters
        ----------
        f_eval : float
            Frequency the lumped values are read at, in Hz.
        src_names : sequence of str
            Sources with a waveform in this study.
        coupled : sequence of str
            Branches that carry a mutual coupling in the state-space model.

        Returns
        -------
        dict of (str, str) to complex
            ``{(branch, source): alpha}`` with ``alpha`` the phase current of
            ``branch`` (counted ``from_bus -> to_bus``) per ampere of
            ``source``. Pairs whose share is zero are omitted, as are voltage
            sources (the coupling is modelled for current sources only, as
            before) and sources without a connection to the fault.

        Warns
        -----
        ModelAssumptionWarning
            ``TRANSIENT_PHASE_SPLIT`` when the split depends on frequency;
            see :meth:`_warn_about_frequency_dependent_split`.
        """
        network = self.network
        reaching = set(network.sources_reaching(self.fault_name))
        current_sources: List[str] = []
        voltage_skipped: List[str] = []
        for name in src_names:
            if name not in reaching:
                continue
            if network.sources[name].source_type == "current":
                current_sources.append(name)
            else:
                voltage_skipped.append(name)
        if voltage_skipped:
            logger.warning(
                "State-space mutual coupling skipped for voltage "
                "sources: %s. The state-space solver currently models "
                "the Carson-style coupling only for current-source "
                "phase currents.",
                voltage_skipped,
            )
        if not current_sources or not coupled:
            return {}

        probe = _UnitInjectionProbe(network, self.fault_name)
        split: Dict[Tuple[str, str], complex] = {}
        for src_name in current_sources:
            currents = probe._compute_phase_currents(f_eval, [src_name])
            for br_name in coupled:
                value = complex(currents.get(br_name, 0.0))
                if abs(value) > self._SPLIT_ZERO:
                    split[(br_name, src_name)] = value
        self._warn_about_frequency_dependent_split(
            probe, f_eval, current_sources, coupled, split
        )
        return split

    def _warn_about_frequency_dependent_split(
        self,
        probe: ElectricalNetwork,
        f_eval: float,
        sources: Sequence[str],
        coupled: Sequence[str],
        split: Dict[Tuple[str, str], complex],
    ) -> None:
        """Say so when one fixed phase split cannot describe every frequency.

        The state-space model is one linear ODE with frequency-independent
        coefficients, and the phase split enters it as one fixed factor per
        branch and source. That is exact when the split does not depend on
        frequency -- a chain, a ring of one cable type with any lengths, any
        symmetric arrangement. Routes of *different* R/L ratio (cable and
        overhead line in one ring) split a DC offset by their resistances,
        the 50 Hz component by their impedances and fast edges by their
        inductances. The folded coupling of :meth:`_folded_coupling` is exact
        at ``f_eval``; everything else in the waveform is split as at
        ``f_eval``.

        Two symptoms are checked, each against :attr:`_SPLIT_TOLERANCE`: a
        quadrature part of the split at ``f_eval`` (a split that does not
        change with frequency is real, because it is real at DC), and a
        change of the split between ``f_eval`` and the other positive
        network frequencies. The second only catches what the network's own
        frequency list can show.

        Measured on a cable/overhead-line ring (quadrature part 0.044 at
        50 Hz, split 0.49 at DC and 0.77 at 50 Hz on the cable route) with a
        fully offset fault current, against a reference that integrates the
        phase conductors as states: the folded split deviates by at most
        0.99 % of the peak EPR, a fixed ``Re(alpha)`` by 6.5 %.

        Parameters
        ----------
        probe : ElectricalNetwork
            The unit-injection phase-network solver of :meth:`_phase_split`.
        f_eval : float
            Frequency the split was taken at, in Hz.
        sources : sequence of str
            Current sources that received a split.
        coupled : sequence of str
            Branches with mutual coupling.
        split : dict of (str, str) to complex
            The split at ``f_eval``.

        Warns
        -----
        ModelAssumptionWarning
            Code ``TRANSIENT_PHASE_SPLIT``, elements: the coupled branches
            whose split depends on frequency.
        """
        tolerance = self._SPLIT_TOLERANCE
        quadrature: Dict[str, float] = {}
        for (br_name, _), alpha in split.items():
            quadrature[br_name] = max(quadrature.get(br_name, 0.0), abs(alpha.imag))

        drift: Dict[str, Tuple[float, float]] = {}
        for freq in self.network.frequencies:
            if freq <= 0.0 or freq == f_eval:
                continue
            for src_name in sources:
                currents = probe._compute_phase_currents(freq, [src_name])
                for br_name in coupled:
                    delta = abs(
                        complex(currents.get(br_name, 0.0))
                        - split.get((br_name, src_name), 0j)
                    )
                    if delta > drift.get(br_name, (0.0, freq))[0]:
                        drift[br_name] = (delta, float(freq))

        affected = sorted(
            name
            for name in coupled
            if quadrature.get(name, 0.0) > tolerance
            or drift.get(name, (0.0, 0.0))[0] > tolerance
        )
        if not affected:
            return

        symptoms: List[str] = []
        worst_quadrature = max(quadrature.get(name, 0.0) for name in affected)
        if worst_quadrature > tolerance:
            symptoms.append(
                f"at {f_eval:g} Hz the phase current carries a part of up to "
                f"{worst_quadrature:.3g} per unit of the source current in "
                f"quadrature with it"
            )
        drifting = [name for name in affected if drift.get(name, (0.0, 0.0))[0] > tolerance]
        if drifting:
            name = max(drifting, key=lambda b: drift[b][0])
            delta, freq = drift[name]
            symptoms.append(
                f"between {f_eval:g} Hz and {freq:g} Hz the share of branch "
                f"'{name}' changes by {delta:.3g} per unit"
            )
        shown = ", ".join(affected[:3])
        if len(affected) > 3:
            shown += f", ... ({len(affected)} branches in total)"
        emit(
            ModelAssumptionWarning,
            f"The state-space solver distributes the phase current of this "
            f"meshed network with the per-unit split of the phase-network "
            f"solve at f_eval = {f_eval:g} Hz -- the split "
            f"gi.run_fault(..., phase_current_mode='auto') uses -- folded into "
            f"the coupling of each branch, which reproduces the {f_eval:g} Hz "
            f"steady state exactly. Here the split depends on frequency, "
            f"because the parallel routes have different R/L ratios: "
            f"{'; '.join(symptoms)} (coupled branches: {shown}). A DC offset "
            f"and harmonics are therefore distributed as at {f_eval:g} Hz "
            f"rather than as at their own frequency. On a cable/overhead-line "
            f"ring with a fully offset fault current that moved the EPR by "
            f"less than 1 % of its peak; a split that changes more over the "
            f"spectrum of the waveform moves it more.",
            code="TRANSIENT_PHASE_SPLIT",
            elements=[("branch", name) for name in affected],
            stacklevel=5,
        )

    @staticmethod
    def _folded_coupling(
        R_mut: float, M_mut: float, alpha: complex, omega: float
    ) -> Tuple[float, float]:
        """Fold a complex phase split into an effective mutual R and M.

        The state-space model drives the shield of a branch with
        ``R_mut * I_p + M_mut * dI_p/dt`` and ``I_p = alpha * I_src``. The
        model is real-valued, so a complex ``alpha`` -- a phase shift between
        routes of different R/L ratio -- cannot be a factor on the source
        waveform. Folded into the coupling instead,
        ``R' + j*omega*M' = (R_mut + j*omega*M_mut) * alpha``, it reproduces
        the coupling EMF of the stationary solver exactly at ``omega``. For a
        real ``alpha`` the fold is the plain product
        ``(alpha * R_mut, alpha * M_mut)``, which is what the solver used
        before, and is exact at every frequency.

        Parameters
        ----------
        R_mut, M_mut : float
            Lumped mutual resistance (Ohm) and inductance (H) of the branch.
        alpha : complex
            Per-unit phase current of the branch for this source.
        omega : float
            Angular frequency the split was taken at, rad/s. At ``0`` the
            split is real anyway (DC divides by resistance) and only its
            real part is used.

        Returns
        -------
        tuple of float
            ``(R', M')``.
        """
        alpha = complex(alpha)
        if omega > 0.0:
            z = complex(R_mut, omega * M_mut) * alpha
            return z.real, z.imag / omega
        return R_mut * alpha.real, M_mut * alpha.real

    def _solve_state_space(
        self, *, t_end: float, dt: float, f_eval: Optional[float] = None
    ) -> ResultTransient:
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

        Mutual coupling (``R_mutual`` / ``M_mutual``) is driven by the phase
        current of each branch, which is the per-unit split of the
        stationary phase-network solve (``phase_current_mode="auto"``) at
        ``f_eval`` times the current-source waveform; see
        :meth:`_phase_split` and :meth:`_folded_coupling`.
        """
        if not self._source_waveforms:
            raise ValueError(
                "No source waveform set. Call set_source_waveform(...) first."
            )
        self._reject_reserved_branch_names()
        self._warn_about_fault_scalings()

        # Frequency at which the lumped RLC values are sampled. The
        # state-space model treats RLC as frequency-independent; see
        # :meth:`_resolve_f_eval` for why this is not ``frequencies[0]``.
        f_eval = self._resolve_f_eval(f_eval)
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
            L_val = (
                float(bus.L[f_eval])
                if bus.L is not None and f_eval in bus.L
                else None
            )
            if L_val is not None and L_val < 0:
                raise ValueError(
                    _passivity_message(
                        f"bus '{name}'", "L", L_val, "inductance"
                    )
                )
            bus_L[name] = L_val if L_val is not None and L_val > 0 else None
            # Bus's own grounding capacitance; per-branch shunt
            # capacitance is added below via the pi-section lumping.
            C_val = (
                float(bus.C[f_eval])
                if bus.C is not None and f_eval in bus.C
                else None
            )
            if C_val is not None and C_val < 0:
                raise ValueError(
                    _passivity_message(
                        f"bus '{name}'", "C", C_val, "capacitance"
                    )
                )
            bus_C[name] = C_val if C_val is not None and C_val > 0 else 0.0
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
            # Passivity of the lumped fields. A negative R_self used to be
            # dropped silently by the ``> 0`` tests below, which turned a
            # sign error into a lossless branch: the ring-down of a
            # reference case went from 21.9 V to exactly 0 V at t_end with
            # no message at all. Reject it where the value is read.
            if R_val is not None and R_val < 0:
                raise ValueError(
                    _passivity_message(
                        f"branch '{name}'", "R_self", R_val, "resistance"
                    )
                )
            if L_val is not None and L_val < 0:
                raise ValueError(
                    _passivity_message(
                        f"branch '{name}'", "L_self", L_val, "inductance"
                    )
                )
            M_val = (
                float(branch.M_mutual[f_eval])
                if branch.M_mutual and f_eval in branch.M_mutual
                else None
            )
            if (
                M_val is not None
                and L_val is not None
                and L_val > 0
                and abs(M_val) > L_val
            ):
                warnings.warn(
                    f"Branch '{name}' has |M_mutual| = {abs(M_val):g} H larger "
                    f"than its own L_self = {L_val:g} H at {f_eval:g} Hz. Two "
                    f"coupled conductors cannot have a mutual inductance above "
                    f"the smaller self-inductance (k = M/sqrt(L1*L2) <= 1), so "
                    f"this is almost always a unit slip -- mH written where H "
                    f"is expected scales the coupled EPR by a factor of 1000. "
                    f"The solve continues with the values as given.",
                    TransientModelWarning,
                    stacklevel=3,
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
            if C_branch < 0:
                raise ValueError(
                    _passivity_message(
                        f"branch '{name}'", "C_self", C_branch, "capacitance"
                    )
                )
            if C_branch == 0:
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
            if omega_eval == 0.0 and float(z.imag) != 0.0:
                raise ValueError(
                    f"Voltage source '{src_name}' has a reactive source "
                    f"impedance (Im(Z_src) = {float(z.imag):g} Ohm) and the "
                    f"lumped values are being read at f_eval = 0 Hz, where "
                    f"L_src = Im(Z_src) / omega is not defined. Up to 0.6.0 "
                    f"this silently produced L_src = 0, i.e. a purely "
                    f"resistive source, and the answer then depended on "
                    f"nothing but the position of 0 Hz in "
                    f"network.frequencies. Pass f_eval=<power frequency> to "
                    f"solve(), or give the network an AC frequency to "
                    f"parameterise the source loop at."
                )
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
                    f"{self._VSRC_PREFIX}{src_name}",
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
        # conductor and shield. The phase current along each branch is the
        # per-unit split of the stationary phase-network solve
        # (``phase_current_mode="auto"``) at f_eval times the source
        # waveform; see :meth:`_phase_split`.
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
        mutual_branches: Dict[str, dict] = {}
        for k, (br_name, fi, ti, R_self_val, L_self_val) in enumerate(
            branch_inductive
        ):
            if br_name.startswith(self._VSRC_PREFIX):
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
                "folded": {},
            }

        # Phase split per (coupled branch, current source): the per-unit
        # phase current of the branch, complex, from the same phase-network
        # solve as ``run_fault(..., phase_current_mode="auto")``. Up to audit
        # pass 18 the factor was read off the enumerated source-to-fault
        # paths as ``sign * parallel_coefficient`` (default 1.0) -- the legacy
        # ``"paths"`` mode, which in a ring gives *each* route the full source
        # current: 338.3 V instead of 166.6 V at the fault of a symmetric
        # four-bus ring (+103 %), against a stationary default that had
        # moved to ``"auto"`` in 0.6 (PH2).
        #
        # The transient solvers treat the source *waveform* as the literal
        # injection; ``fault.scalings`` (a frequency-domain concept for the
        # stationary solve) is intentionally NOT applied, so the phase
        # current stays consistent with the unscaled shield injection. The
        # FFT solver behaves the same way.
        phase_factors: Dict[Tuple[str, str], complex] = {}
        if mutual_branches:
            phase_factors = self._phase_split(
                f_eval, src_names, list(mutual_branches)
            )

        # The split enters as an effective coupling per (branch, source):
        # ``R' + j w M' = (R_mut + j w M_mut) * alpha`` at ``f_eval``. A complex
        # split -- routes of different R/L ratio -- is thereby reproduced
        # exactly in the ``f_eval`` steady state; freezing only ``Re(alpha)``
        # put the fault EPR of a cable/overhead-line ring 4.4 % low. For a
        # real split the fold is the plain product ``alpha * (R_mut, M_mut)``.
        for (br_name, src_name), alpha in phase_factors.items():
            info = mutual_branches[br_name]
            src_col = src_names.index(src_name)
            R_eff, M_eff = self._folded_coupling(
                info["R_mut"], info["M_mut"], alpha, omega_eval
            )
            info["folded"][src_name] = (R_eff, M_eff)
            M_per_L = M_eff / info["L_self"]
            K_mut = (
                info["R_self"] * M_eff - R_eff * info["L_self"]
            ) / (info["L_self"] ** 2)

            # KCL feedforward: substitution moves -B_L * (M/L_self) * I_p
            # to the input side. Since the branch state convention is
            # to->from (+1 at to, -1 at from in B_L), the contribution
            # at the to-bus is +M_per_L and at the from-bus is -M_per_L.
            B_kcl[info["to_idx"], src_col] += M_per_L
            B_kcl[info["from_idx"], src_col] -= M_per_L
            # EMF in dz/dt
            B_emf[info["state_idx"], src_col] += K_mut

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
        # Same grid as the FFT solver, so the two results can be compared
        # element-wise without resampling (see :func:`_time_grid`).
        t, n_samples = _time_grid(t_end, dt)
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
            if br_name.startswith(self._VSRC_PREFIX):
                continue
            ind_state_offset[br_name] = n_bus_L + idx
        res_branch_lookup = {
            name: (fi, ti, G) for (name, fi, ti, G) in branch_resistive
        }
        for branch_name in self._obs_branches:
            if branch_name in ind_state_offset:
                # Inductive branch state. With the mutual substitution
                # ``z = i_shield + (M'/L_self) * I_src`` the observable
                # shield current is ``i_shield = z - (M'/L_self) * I_src``,
                # ``M'`` the folded mutual inductance of each source.
                i_observed = x_out[:, ind_state_offset[branch_name]].copy()
                mut_branches, _, _ = self.__mutual_for_output
                if branch_name in mut_branches:
                    info = mut_branches[branch_name]
                    for src_col, src_name in enumerate(src_names):
                        folded = info["folded"].get(src_name)
                        if folded is not None:
                            M_eff = folded[1]
                            i_observed -= (
                                M_eff / info["L_self"]
                            ) * u[:, src_col]
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

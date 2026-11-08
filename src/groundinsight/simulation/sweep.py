# simulation/sweep.py

"""
Run one fault over a grid of parameter variations and collect it into one frame.

``run_outage_study`` already answers "what changes when an element drops out".
This module answers the other half: what changes when a *parameter* moves --
the rho-f characteristic of the faulted station, the soil resistivity, the
harmonic content of the source, the fault location itself.

The unit of work is a :class:`SweepPoint`: a label plus the overrides that
define it. Every point is applied, solved and restored, and the results are
stacked into long-format Polars frames that carry the point's label and its
parameters as ordinary columns. That last part is the point of the module --
until a frame like this exists there is nothing for a statistic to operate on,
which is why :mod:`groundinsight.analysis.statistics` starts here.

Overrides are applied by writing directly onto ``Bus.impedance`` and friends,
which reaches the solver untouched because ``run_fault`` does not recompute
impedances (see :class:`~groundinsight.electrical_network.ElectricalNetwork`).
Every point's overrides are undone in a ``finally`` block, and the
network-level state the solves write -- stored results, active fault, path
set, assembled system -- is restored exactly when the sweep ends, however it
ends: a finished sweep, a failing point with ``on_error="raise"``, a
cancellation through ``should_cancel``.

Example
-------
Vary the rho-f characteristic of the faulted station over a grid and watch what
it does to the potential rise and to the parallel impedances of the two feeder
directions::

    points = gi.rho_f_points(
        bus="Station_7",
        k_vectors={f"k1={k1:g}": (k1, 1e-4, 3e-4, 0.0, 0.0)
                   for k1 in (0.01, 0.02, 0.05, 0.1)},
        frequencies=net.frequencies,
        rho=100.0,
    )
    study = gi.run_sweep(
        net, fault="F_Station_7", points=points,
        cuts=[gi.Cut(name="left", branches=["C6_7"]),
              gi.Cut(name="right", branches=["C7_8"])],
    )
    study.impedances()   # Z_G and both reduction factors per point
    study.cuts()         # Z_left / Z_right / r_left / r_right per point
"""

from __future__ import annotations

import logging
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

import polars as pl
from pydantic import BaseModel, Field

from groundinsight._state import restore_always
from groundinsight.analysis.decomposition import Cut, analyze_cuts
from groundinsight.models.core_models import ComplexNumber, Network
from groundinsight.network_operations import run_fault
from groundinsight.progress import StudyHooks

logger = logging.getLogger(__name__)

__all__ = [
    "SweepPoint",
    "SweepResult",
    "run_sweep",
    "rho_f_points",
]

#: A five-parameter rho-f vector ``(k1, k2, k3, k4, k5)``.
KVector = Tuple[float, float, float, float, float]


def _z_rho_f(k: KVector, rho: float, f: float) -> complex:
    """The standard rho-f form ``Z = k1*rho + (k2+j*k3)*f + (k4+j*k5)*rho*f``."""
    k1, k2, k3, k4, k5 = k
    return k1 * rho + (k2 + 1j * k3) * f + (k4 + 1j * k5) * rho * f


class SweepPoint(BaseModel):
    """
    One parameter combination to solve.

    Attributes
    ----------
    label : str
        Identifies the point in every result frame. Must be unique in a sweep.
    bus_impedance : dict of str to dict of float to complex, optional
        Impedance tables written straight onto ``Bus.impedance``. Bypasses the
        bus type's formula entirely -- this is how a rho-f characteristic
        measured or fitted elsewhere enters the study.
    bus_rho : dict of str to float, optional
        New ``specific_earth_resistance`` per bus, with the bus type's formula
        re-evaluated afterwards. Use this to vary the soil rather than the
        characteristic.
    fault : str, optional
        Solve a different fault at this point. Defaults to the sweep's fault.
    fault_scalings : dict of float to float, optional
        Replace the active fault's per-frequency scalings -- the harmonic
        content of the source.
    parameters : dict of str to object, optional
        Free-form values copied into every result row as columns, so a plot can
        be made against the physical parameter rather than against the label.
    """

    model_config = {"arbitrary_types_allowed": True}

    label: str
    bus_impedance: Dict[str, Dict[float, complex]] = Field(default_factory=dict)
    bus_rho: Dict[str, float] = Field(default_factory=dict)
    fault: Optional[str] = None
    fault_scalings: Optional[Dict[float, float]] = None
    parameters: Dict[str, Any] = Field(default_factory=dict)

    def __str__(self):
        return f"SweepPoint(label={self.label}, parameters={self.parameters})"


class SweepResult(BaseModel):
    """
    Everything a sweep collected, as long-format frames.

    Attributes
    ----------
    fault : str
        The fault the sweep was run for (points may override it individually).
    labels : list of str
        Point labels in the order they were solved.
    failures : dict of str to str
        Points that raised, mapped to the exception text. They are absent from
        the frames; the sweep does not abort on one bad point.
    """

    model_config = {"arbitrary_types_allowed": True}

    fault: str
    labels: List[str] = Field(default_factory=list)
    failures: Dict[str, str] = Field(default_factory=dict)
    _buses: Optional[pl.DataFrame] = None
    _branches: Optional[pl.DataFrame] = None
    _impedances: Optional[pl.DataFrame] = None
    _cuts: Optional[pl.DataFrame] = None

    def buses(self) -> pl.DataFrame:
        """Per-bus results of every point, stacked."""
        return _or_empty(self._buses)

    def branches(self) -> pl.DataFrame:
        """Per-branch results of every point, stacked."""
        return _or_empty(self._branches)

    def impedances(self) -> pl.DataFrame:
        """``Z_G`` and both reduction factors of every point, stacked."""
        return _or_empty(self._impedances)

    def cuts(self) -> pl.DataFrame:
        """Side impedances and side reduction factors, stacked. Empty when the
        sweep was run without cuts."""
        return _or_empty(self._cuts)

    def __str__(self):
        return (
            f"SweepResult(fault={self.fault}, points={len(self.labels)}, "
            f"failures={len(self.failures)})"
        )


def _or_empty(frame: Optional[pl.DataFrame]) -> pl.DataFrame:
    return frame if frame is not None else pl.DataFrame()


def rho_f_points(
    *,
    bus: str,
    k_vectors: Dict[str, KVector],
    frequencies: Sequence[float],
    rho: float,
) -> List[SweepPoint]:
    """
    Build sweep points from a catalogue of rho-f parameter vectors.

    Each vector is evaluated into an impedance table for ``bus`` over
    ``frequencies`` at the given ``rho``, so the study varies the *fitted
    characteristic* of one station while the rest of the network stays put.
    ``k1`` ... ``k5`` and ``rho`` are copied into ``parameters``, which puts
    them in the result frames as plottable columns.

    Parameters
    ----------
    bus : str
        Name of the bus whose characteristic is varied.
    k_vectors : dict of str to tuple
        Label to ``(k1, k2, k3, k4, k5)``.
    frequencies : sequence of float
        Frequencies to evaluate the form at -- normally ``network.frequencies``.
    rho : float
        Soil resistivity the form is evaluated at.

    Returns
    -------
    list of SweepPoint

    Raises
    ------
    ValueError
        If a vector produces a non-positive real part at any frequency, which
        the solver rejects as non-passive. The offending label and frequency are
        named, because an unconstrained least-squares fit can land there and the
        failure is otherwise reported far from its cause.
    """
    points: List[SweepPoint] = []
    for label, k in k_vectors.items():
        if len(k) != 5:
            raise ValueError(
                f"rho-f vector '{label}' has {len(k)} entries; the standard "
                f"form takes exactly five, (k1, k2, k3, k4, k5)."
            )
        table: Dict[float, complex] = {}
        for freq in frequencies:
            z = _z_rho_f(tuple(k), rho, float(freq))
            if z.real <= 0.0:
                raise ValueError(
                    f"rho-f vector '{label}' gives Re(Z) = {z.real:.6g} Ohm at "
                    f"{freq} Hz with rho = {rho} Ohm*m, which is not a passive "
                    f"impedance and the solver will reject it. An unconstrained "
                    f"least-squares fit can produce this below the frequency "
                    f"range it was fitted on -- check the fit's validity range."
                )
            table[float(freq)] = z
        points.append(
            SweepPoint(
                label=label,
                bus_impedance={bus: table},
                parameters={
                    "bus": bus,
                    "rho_Ohm_m": rho,
                    "k1": k[0],
                    "k2": k[1],
                    "k3": k[2],
                    "k4": k[3],
                    "k5": k[4],
                },
            )
        )
    return points


def _new_saved() -> Dict[str, Any]:
    """Build the empty undo record :func:`_apply` fills in.

    The record is created by the caller and handed *into* :func:`_apply`
    rather than returned from it. That is the whole point: ``run_sweep``
    restores in a ``finally`` block, and a record that only exists as a
    return value never reaches that block when ``_apply`` raises half-way
    through -- every mutation made up to that point then stayed on the
    network and every later point of the sweep was solved on it.

    Returns
    -------
    dict
        ``{"buses": {}, "order": [], "scalings": None, "scalings_fault": None}``.
        ``order`` lists the bus names in the order they were first touched,
        so :func:`_restore` can undo them in reverse.
    """
    return {
        "buses": {},
        "order": [],
        "scalings": None,
        "scalings_fault": None,
    }


def _snapshot_bus(saved: Dict[str, Any], bus_name: str, bus) -> None:
    """Record a bus's pre-override state exactly once.

    ``setdefault`` is essential rather than convenient: a point may override
    both ``bus_impedance`` and ``bus_rho`` of the same bus, and the rho branch
    re-evaluates the type formula *after* the impedance branch has already
    written its table. A second snapshot would therefore capture the override
    as if it were the original and the bus would keep it for the rest of the
    sweep.

    All five fields the overrides can reach are captured together --
    ``specific_earth_resistance``, ``impedance`` and the lumped ``R``/``L``/``C``
    that ``Bus.calculate_impedance`` recomputes alongside the impedance table.
    """
    if bus_name in saved["buses"]:
        return
    saved["buses"][bus_name] = {
        "specific_earth_resistance": bus.specific_earth_resistance,
        "impedance": dict(bus.impedance),
        "R": dict(bus.R) if bus.R is not None else None,
        "L": dict(bus.L) if bus.L is not None else None,
        "C": dict(bus.C) if bus.C is not None else None,
    }
    saved["order"].append(bus_name)


def _apply(network: Network, point: SweepPoint, saved: Dict[str, Any]) -> None:
    """Apply a point's overrides, recording the undo information in ``saved``.

    Every referenced bus name is validated *before* the first mutation, so a
    point that names a bus the network does not have leaves the network
    untouched instead of half-overridden. Whatever is mutated after that is
    snapshotted into ``saved`` first, and ``saved`` belongs to the caller, so
    :func:`_restore` can undo the work of a call that raised in the middle.

    Parameters
    ----------
    network : Network
        The network to mutate in place.
    point : SweepPoint
        The overrides to apply.
    saved : dict
        Undo record from :func:`_new_saved`, filled in as the overrides are
        applied.

    Raises
    ------
    ValueError
        If the point references a bus that is not part of ``network``.
    """
    unknown = sorted(
        {name for name in point.bus_impedance if name not in network.buses}
        | {name for name in point.bus_rho if name not in network.buses}
    )
    if unknown:
        raise ValueError(
            f"Sweep point '{point.label}' overrides bus(es) {unknown}, which "
            f"are not in network '{network.name}'. Available: "
            f"{sorted(network.buses)}."
        )

    for bus_name, table in point.bus_impedance.items():
        bus = network.buses[bus_name]
        _snapshot_bus(saved, bus_name, bus)
        bus.impedance = {
            float(f): ComplexNumber(real=complex(z).real, imag=complex(z).imag)
            for f, z in table.items()
        }

    for bus_name, rho in point.bus_rho.items():
        bus = network.buses[bus_name]
        _snapshot_bus(saved, bus_name, bus)
        bus.specific_earth_resistance = rho
        bus.calculate_impedance(network.frequencies)


def _restore(network: Network, saved: Dict[str, Any]) -> None:
    """Undo :func:`_apply`. Never raises on a missing element -- a restore that
    fails would leave the network in a state no later point can trust.

    Buses are restored in reverse order of application, which is the order
    that makes a restore correct when two overrides of one point interact
    (an ``impedance`` table written first and then overwritten by the rho
    branch's re-evaluation of the type formula).
    """
    for bus_name in reversed(saved["order"]):
        bus = network.buses.get(bus_name)
        if bus is None:
            continue
        state = saved["buses"][bus_name]
        bus.specific_earth_resistance = state["specific_earth_resistance"]
        bus.impedance = state["impedance"]
        bus.R = state["R"]
        bus.L = state["L"]
        bus.C = state["C"]
    if saved["scalings_fault"] is not None:
        fault = network.faults.get(saved["scalings_fault"])
        if fault is not None:
            fault.scalings = saved["scalings"]


def _tag(frame: pl.DataFrame, point: SweepPoint) -> pl.DataFrame:
    """Prepend the label and the point's free parameters as columns."""
    if frame.is_empty():
        return frame
    frame = frame.with_columns(pl.lit(point.label).alias("point"))
    for key, value in point.parameters.items():
        if key in frame.columns:
            continue
        frame = frame.with_columns(pl.lit(value).alias(key))
    ordered = ["point"] + [c for c in frame.columns if c != "point"]
    return frame.select(ordered)


def run_sweep(
    network: Network,
    *,
    fault: str,
    points: Sequence[SweepPoint],
    cuts: Optional[Sequence[Cut]] = None,
    phase_current_mode: str = "auto",
    collect_branches: bool = False,
    on_error: str = "record",
    progress: Optional[Callable[[int, int, str], None]] = None,
    should_cancel: Optional[Callable[[], bool]] = None,
) -> SweepResult:
    """
    Solve one fault once per parameter point and stack the results.

    Parameters
    ----------
    network : Network
        The network. Left exactly as found, whatever happens: the point
        overrides are undone after every point, and the stored results,
        active fault, path set and assembled system are the ones it had
        before the sweep.
    fault : str
        Fault to solve, unless a point names its own.
    points : sequence of SweepPoint
        The parameter grid. Labels must be unique.
    cuts : sequence of Cut, optional
        When given, :func:`~groundinsight.analysis.analyze_cuts` runs at every
        point and its frame is stacked into ``SweepResult.cuts()``.
    phase_current_mode : {"auto", "paths"}, optional
        Forwarded to :func:`run_fault`.
    collect_branches : bool, optional
        Branch results multiply the row count by the number of branches and are
        rarely what a parameter study plots, so they are off by default.
    on_error : {"record", "raise"}, optional
        ``"record"`` (default) notes a failing point in ``SweepResult.failures``
        and carries on -- a single non-passive parameter combination should not
        throw away the rest of a long grid. ``"raise"`` propagates instead.
    progress : callable, optional
        ``progress(done, total, label)``, called after every point (a failed
        one included) with ``total = len(points)`` and the point's label.
        See :mod:`groundinsight.progress`.
    should_cancel : callable, optional
        Asked before every point; a true answer raises
        :class:`~groundinsight.progress.StudyCancelled`.

    Returns
    -------
    SweepResult

    Raises
    ------
    ValueError
        If ``points`` is empty, if two points share a label, if the fault is
        unknown, or if ``on_error`` is not one of the two accepted values.
    TypeError
        If ``progress`` or ``should_cancel`` is not callable.
    StudyCancelled
        If ``should_cancel()`` returned true; the network is as found.
    """
    if on_error not in ("record", "raise"):
        raise ValueError(
            f"on_error must be 'record' or 'raise', got {on_error!r}."
        )
    if not points:
        raise ValueError("A sweep needs at least one point.")
    labels = [p.label for p in points]
    duplicates = {label for label in labels if labels.count(label) > 1}
    if duplicates:
        raise ValueError(
            f"Sweep point label(s) {sorted(duplicates)} appear more than once. "
            f"Labels identify the rows of every result frame and have to be "
            f"unique."
        )
    if fault not in network.faults:
        raise ValueError(
            f"Fault '{fault}' does not exist in network '{network.name}'. "
            f"Available: {sorted(network.faults)}."
        )
    hooks = StudyHooks("run_sweep", len(points), progress, should_cancel)

    bus_frames: List[pl.DataFrame] = []
    branch_frames: List[pl.DataFrame] = []
    impedance_frames: List[pl.DataFrame] = []
    cut_frames: List[pl.DataFrame] = []
    solved: List[str] = []
    failures: Dict[str, str] = {}

    # Every solve below replaces the stored result, the active fault and the
    # assembled system, and may drop the path set; restoring them exactly is
    # what "left exactly as found" means -- also for the previously active
    # fault and its cached result (S8). Bus overrides are undone per point.
    with restore_always(network):
        for point in points:
            hooks.checkpoint()
            target = point.fault or fault
            if target not in network.faults:
                message = (
                    f"point '{point.label}' names fault '{target}', which does not "
                    f"exist in network '{network.name}'"
                )
                if on_error == "raise":
                    raise ValueError(message)
                failures[point.label] = message
                hooks.advance(point.label)
                continue

            # Bound before the call, not from its return value: ``_apply`` may
            # raise after it has already mutated the network, and ``finally``
            # has to see everything it did up to that point (S2).
            saved = _new_saved()
            try:
                _apply(network, point, saved)
                if point.fault_scalings is not None:
                    fault_obj = network.faults[target]
                    saved["scalings_fault"] = target
                    saved["scalings"] = dict(fault_obj.scalings)
                    fault_obj.scalings = dict(point.fault_scalings)

                run_fault(network, target, phase_current_mode=phase_current_mode)

                bus_frames.append(_tag(network.res_buses(fault=target), point))
                if collect_branches:
                    branch_frames.append(
                        _tag(network.res_branches(fault=target), point)
                    )
                impedance_frames.append(
                    _tag(
                        network.res_all_impedances().filter(
                            pl.col("fault_name") == target
                        ),
                        point,
                    )
                )
                if cuts:
                    analysis = analyze_cuts(network, fault=target, cuts=cuts)
                    cut_frames.append(_tag(analysis.to_polars(), point))
                solved.append(point.label)
            except Exception as exc:  # noqa: BLE001 -- recorded, then re-raised or not
                if on_error == "raise":
                    raise
                logger.warning(
                    "Sweep point '%s' failed and was skipped: %s", point.label, exc
                )
                failures[point.label] = str(exc)
            finally:
                _restore(network, saved)
            hooks.advance(point.label)

    result = SweepResult(fault=fault, labels=solved, failures=failures)
    result._buses = pl.concat(bus_frames, how="diagonal") if bus_frames else None
    result._branches = (
        pl.concat(branch_frames, how="diagonal") if branch_frames else None
    )
    result._impedances = (
        pl.concat(impedance_frames, how="diagonal") if impedance_frames else None
    )
    result._cuts = pl.concat(cut_frames, how="diagonal") if cut_frames else None
    if failures:
        logger.warning(
            "%d of %d sweep points failed: %s",
            len(failures),
            len(points),
            ", ".join(sorted(failures)),
        )
    return result

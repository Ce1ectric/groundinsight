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
Everything is restored in a ``finally`` block, so an exception in the middle of
a sweep leaves the network exactly as it was found -- including the path cache
and the active fault.

Example
-------
Vary the rho-f characteristic of the faulted station over a grid and watch what
it does to the potential rise and to the parallel impedances of the two feeder
directions::

    points = gi.rho_f_points(
        bus="Station_7",
        k_vectors={f"k1={k1:g}": (k1, 1e-4, 3e-4, 0.0, 0.0)
                   for k1 in (0.01, 0.02, 0.05, 0.1)},
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
from typing import Any, Dict, List, Optional, Sequence, Tuple

import polars as pl
from pydantic import BaseModel, Field

from groundinsight.analysis.decomposition import Cut, analyze_cuts
from groundinsight.models.core_models import ComplexNumber, Network
from groundinsight.network_operations import run_fault

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


def _apply(network: Network, point: SweepPoint) -> Dict[str, Any]:
    """Apply a point's overrides and return what is needed to undo them."""
    saved: Dict[str, Any] = {
        "impedance": {},
        "rho": {},
        "scalings": None,
        "scalings_fault": None,
    }

    for bus_name, table in point.bus_impedance.items():
        if bus_name not in network.buses:
            raise ValueError(
                f"Sweep point '{point.label}' overrides bus '{bus_name}', which "
                f"is not in network '{network.name}'."
            )
        bus = network.buses[bus_name]
        saved["impedance"][bus_name] = dict(bus.impedance)
        bus.impedance = {
            float(f): ComplexNumber(real=complex(z).real, imag=complex(z).imag)
            for f, z in table.items()
        }

    for bus_name, rho in point.bus_rho.items():
        if bus_name not in network.buses:
            raise ValueError(
                f"Sweep point '{point.label}' overrides rho of bus "
                f"'{bus_name}', which is not in network '{network.name}'."
            )
        bus = network.buses[bus_name]
        saved["rho"][bus_name] = (bus.specific_earth_resistance, dict(bus.impedance))
        bus.specific_earth_resistance = rho
        bus.calculate_impedance(network.frequencies)

    return saved


def _restore(network: Network, saved: Dict[str, Any]) -> None:
    """Undo :func:`_apply`. Never raises on a missing element -- a restore that
    fails would leave the network in a state no later point can trust."""
    for bus_name, table in saved["impedance"].items():
        bus = network.buses.get(bus_name)
        if bus is not None:
            bus.impedance = table
    for bus_name, (rho, table) in saved["rho"].items():
        bus = network.buses.get(bus_name)
        if bus is not None:
            bus.specific_earth_resistance = rho
            bus.impedance = table
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
) -> SweepResult:
    """
    Solve one fault once per parameter point and stack the results.

    Parameters
    ----------
    network : Network
        The network. Left exactly as found, whatever happens.
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

    Returns
    -------
    SweepResult

    Raises
    ------
    ValueError
        If ``points`` is empty, if two points share a label, if the fault is
        unknown, or if ``on_error`` is not one of the two accepted values.
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

    previous_active = network.active_fault
    bus_frames: List[pl.DataFrame] = []
    branch_frames: List[pl.DataFrame] = []
    impedance_frames: List[pl.DataFrame] = []
    cut_frames: List[pl.DataFrame] = []
    solved: List[str] = []
    failures: Dict[str, str] = {}

    for point in points:
        target = point.fault or fault
        if target not in network.faults:
            message = (
                f"point '{point.label}' names fault '{target}', which does not "
                f"exist in network '{network.name}'"
            )
            if on_error == "raise":
                raise ValueError(message)
            failures[point.label] = message
            continue

        saved = None
        try:
            saved = _apply(network, point)
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
            if saved is not None:
                _restore(network, saved)

    if previous_active is not None and previous_active in network.faults:
        network.set_active_fault(previous_active)

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

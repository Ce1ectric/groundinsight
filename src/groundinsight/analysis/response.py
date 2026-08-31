# analysis/response.py

"""
Characterise a location in the network independently of the electrode there.

The idea this module implements: to describe what the network does *at* a given
station, remove the station's own electrode and ask what is left. Whatever
electrode is eventually installed there only ever appears as one shunt
admittance, so the network's contribution can be stated once and the electrode
varied afterwards -- including to its two extremes, an ideal electrode and none
at all.

That intuition turns out to be exact rather than approximate. Adding a shunt
``Y_B`` at bus ``b`` is a rank-one change to the nodal matrix, so by the
Sherman-Morrison identity every nodal voltage is a **Möbius function** of it:

.. math::

    \\underline{u}(Y_B) = \\underline{u}_0
        - \\frac{Y_B\\, \\underline{z}\\, u_{0,b}}{1 + Y_B Z_\\text{net}}

with three site-independent objects, all obtained without knowing the electrode:

``u_0``
    the nodal voltages of the fault solve with the electrode removed -- the
    **open-circuit response** of the location;
``z``
    the column ``Y_0^{-1} e_b``, i.e. the voltage everywhere per ampere injected
    at ``b``, source-free;
``Z_net = z_b``
    the driving-point impedance of everything *except* the local electrode --
    the parallel impedance the network offers at that point.

Two solves therefore determine the response for **every** electrode, exactly and
at no further cost. Measured against real solves at ``Z_B`` from 0.05 Ω to 500 Ω
and at a complex value, the closed form agrees to ``2e-15`` relative.

The extremes
------------
``Z_B → ∞`` (no electrode) and ``Z_B → 0`` (ideal electrode) are the endpoints of
that curve and both are exact limits, not numerical stand-ins -- the ideal
electrode in particular, which the solver itself rejects because a zero impedance
is not a passive value it can invert.

The driving-point impedance is exactly ``Z_dp(Y_B) = 1/(Y_B + 1/Z_net)``, so it
runs monotonically from ``Z_net`` down to zero as the electrode improves. Over
*all* passive electrodes the largest attainable magnitude is not quite at the
open end: a purely reactive ``Y_B = -j\\,Im(1/Z_net)`` cancels the network's
susceptance and gives ``|Z_dp| = 1/Re(1/Z_net)``. It exceeds ``|Z_net|`` by very
little in a typical cable network -- 0.1 % in the verification case -- but it is
the honest bound and is reported next to the two endpoints.

What the extremes do *not* bracket
----------------------------------
``ResultReductionFactor.value``, the EPR-based reduction factor, is **constant**
along the whole curve. The closed form shows why: the voltage at the bus is
``u_b(Y_B) = u_{0,b} / (1 + Y_B Z_\\text{net})`` with and without mutual coupling
alike, so the factor cancels out of the quotient exactly. That is the same
invariance the sensitivity study runs into, here derived rather than measured.
The current-based factor and the potential rise are not invariant and are
reported as brackets.
"""

from __future__ import annotations

import logging
from typing import Dict, List, Optional, Sequence, Union

import numpy as np
import polars as pl
from pydantic import BaseModel, Field

from groundinsight.models.core_models import Network
from groundinsight.utils.earth_current import split_earth_currents

logger = logging.getLogger(__name__)

__all__ = [
    "BusResponse",
    "bus_response",
]

#: Accepted spellings of an electrode in :meth:`BusResponse.evaluate`.
ElectrodeSpec = Union[complex, float, None]


class BusResponse(BaseModel):
    """
    Closed-form response of the network to the electrode at one bus.

    Built by :func:`bus_response`. Evaluating it costs no solve.

    Attributes
    ----------
    fault : str
        Fault the response was built for.
    bus : str
        Bus whose electrode is the free parameter.
    fault_bus : str
        Bus the fault sits on. Used to anchor which group of buses is named as
        feeding the soil when the earth-return current is split.
    frequencies : list of float
        Frequencies covered.
    z_network : dict of float to complex
        Driving-point impedance at ``bus`` with the local electrode removed --
        the parallel impedance the rest of the network offers at that location.
        Independent of anything installed at ``bus``.
    u_open : dict of float to dict of str to complex
        Nodal voltages with the local electrode removed.
    z_column : dict of float to dict of str to complex
        Voltage at every bus per ampere injected at ``bus``, source-free.
    r_epr : dict of float to float or None
        The EPR-based reduction factor, carried through because it is constant
        along the whole curve.
    i_fault : dict of float to complex
        Fault current, the negated sum of source injections. Constant.
    """

    model_config = {"arbitrary_types_allowed": True}

    fault: str
    bus: str
    fault_bus: str
    frequencies: List[float]
    bus_names: List[str]
    z_network: Dict[float, complex]
    u_open: Dict[float, Dict[str, complex]]
    z_column: Dict[float, Dict[str, complex]]
    other_impedance: Dict[float, Dict[str, complex]] = Field(default_factory=dict)
    r_epr: Dict[float, Optional[float]] = Field(default_factory=dict)
    i_fault: Dict[float, complex] = Field(default_factory=dict)

    # -- the curve ------------------------------------------------------------

    def admittance(self, z_bus: ElectrodeSpec, freq: float) -> complex:
        """
        Translate an electrode spelling into a shunt admittance.

        ``None`` and an infinite impedance both mean *no electrode*
        (``Y_B = 0``); a zero impedance means an *ideal* one and is returned as
        ``inf``, which :meth:`voltages` handles as a limit rather than a
        division.
        """
        if z_bus is None:
            return 0.0 + 0.0j
        z = complex(z_bus)
        if np.isinf(z.real) or np.isinf(z.imag):
            return 0.0 + 0.0j
        if z == 0:
            return complex(np.inf, 0.0)
        return 1.0 / z

    def voltages(self, z_bus: ElectrodeSpec, freq: float) -> Dict[str, complex]:
        """
        Nodal voltages for one electrode at one frequency.

        Both endpoints are evaluated as limits, so an ideal electrode gives
        exactly zero at ``bus`` instead of a very small number.
        """
        u0 = self.u_open[freq]
        z_col = self.z_column[freq]
        u0_b = u0[self.bus]
        z_net = self.z_network[freq]
        y = self.admittance(z_bus, freq)

        if np.isinf(y.real):
            # Ideal electrode: the Y_B -> inf limit of the Möbius form.
            if z_net == 0:
                return dict(u0)
            return {
                name: u0[name] - z_col[name] * u0_b / z_net
                for name in self.bus_names
            }
        if y == 0:
            return dict(u0)
        denominator = 1.0 + y * z_net
        if denominator == 0:
            # A reactive electrode exactly at the network's own pole. Physically
            # unreachable with a passive electrode (it needs Re(Y_B) < 0), but
            # named rather than returned as inf.
            raise ValueError(
                f"The electrode {z_bus!r} at bus '{self.bus}' cancels the "
                f"network admittance exactly at {freq} Hz (1 + Y_B*Z_net = 0), "
                f"so the response has a pole there. That requires a negative "
                f"conductance, which no passive electrode has."
            )
        factor = y * u0_b / denominator
        return {
            name: u0[name] - z_col[name] * factor for name in self.bus_names
        }

    def driving_point(self, z_bus: ElectrodeSpec, freq: float) -> complex:
        """``Z_dp = 1 / (Y_B + 1/Z_net)`` -- the impedance seen at ``bus``."""
        y = self.admittance(z_bus, freq)
        if np.isinf(y.real):
            return 0.0 + 0.0j
        z_net = self.z_network[freq]
        if z_net == 0:
            return 0.0 + 0.0j
        total = y + 1.0 / z_net
        if total == 0:
            return complex(np.inf, 0.0)
        return 1.0 / total

    def worst_case_electrode(self, freq: float) -> Dict[str, complex]:
        """
        The passive electrode that maximises ``|Z_dp|``, and the value it gives.

        Over the closed right half-plane of ``Y_B`` the magnitude of
        ``1/(Y_B + Y_net)`` is largest where the imaginary parts cancel and the
        real part is as small as it can be, i.e. at ``Y_B = -j*Im(Y_net)``. The
        result exceeds ``|Z_net|`` only slightly in a cable network, but it is
        the true bound rather than an assumed one.

        Returns
        -------
        dict
            ``z_bus`` (the electrode, purely reactive) and ``z_driving_point``.
        """
        z_net = self.z_network[freq]
        if z_net == 0 or not np.isfinite(z_net):
            return {"z_bus": complex(np.inf, 0.0), "z_driving_point": z_net}
        y_net = 1.0 / z_net
        y_b = complex(0.0, -y_net.imag)
        if y_b == 0:
            return {"z_bus": complex(np.inf, 0.0), "z_driving_point": z_net}
        return {"z_bus": 1.0 / y_b, "z_driving_point": 1.0 / y_net.real}

    # -- derived engineering quantities ---------------------------------------

    def _electrode_current(
        self, z_bus: ElectrodeSpec, freq: float, u: Dict[str, complex]
    ) -> complex:
        """Current into the soil through the electrode at ``bus``."""
        y = self.admittance(z_bus, freq)
        if np.isinf(y.real):
            # Ideal electrode: u_b is zero but the product is not. The limit of
            # Y_B * u_b / (1 + Y_B Z_net) is u_0b / Z_net.
            z_net = self.z_network[freq]
            if z_net == 0:
                return complex(np.nan, np.nan)
            return self.u_open[freq][self.bus] / z_net
        return y * u[self.bus]

    def _earth_return_factor(
        self, z_bus: ElectrodeSpec, freq: float, u: Dict[str, complex]
    ) -> Optional[float]:
        """
        ``r_I = |I_E| / |I_F|`` with ``I_E`` summed over every bus that feeds the
        soil, matching ``ResultReductionFactor.value_current``.

        The bus being varied is included like any other -- its electrode is part
        of the earthing system, and leaving it out would reduce the whole sum to
        the electrode current of one station.
        """
        i_fault = self.i_fault.get(freq)
        if i_fault is None or i_fault == 0:
            return None
        impedances = self.other_impedance.get(freq, {})
        electrode_currents = {
            name: u[name] / z for name, z in impedances.items() if name != self.bus
        }
        # The varied bus carries the electrode being evaluated, not the one the
        # response happened to be built with, so its current comes from the
        # electrode under test -- including as a limit at the two extremes.
        own = self._electrode_current(z_bus, freq, u)
        if own != 0 and np.isfinite(own):
            electrode_currents[self.bus] = own
        split = split_earth_currents(
            electrode_currents, reference_bus=self.fault_bus
        )
        if split is None:
            return None
        return abs(split.i_earth) / abs(i_fault)

    def evaluate(
        self, z_bus: ElectrodeSpec, *, label: Optional[str] = None
    ) -> pl.DataFrame:
        """
        Everything the location does for one electrode, as one frame.

        Parameters
        ----------
        z_bus : complex, float or None
            The electrode. ``None`` or an infinite value means *none installed*,
            ``0`` means *ideal*. Both are evaluated as exact limits.
        label : str, optional
            Value of the ``case`` column. Defaults to a readable rendering of
            ``z_bus``.

        Returns
        -------
        pl.DataFrame
            One row per frequency: ``case``, ``bus``, ``fault``,
            ``frequency_Hz``, ``Z_bus_Ohm``, ``Z_network_Ohm``,
            ``Z_driving_point_Ohm``, ``EPR_V``, ``EPR_deg``, ``I_electrode_A``,
            ``r_epr``, ``r_current``, ``Z_G_Ohm``, plus ``EPR_<bus>_V`` for
            every other bus.
        """
        rows = []
        for freq in self.frequencies:
            u = self.voltages(z_bus, freq)
            z_dp = self.driving_point(z_bus, freq)
            u_b = u[self.bus]
            r_epr = self.r_epr.get(freq)
            i_fault = self.i_fault.get(freq)
            z_g = (
                u_b / (r_epr * i_fault)
                if r_epr not in (None, 0) and i_fault not in (None, 0)
                else None
            )
            row = {
                "case": label if label is not None else _render(z_bus),
                "fault": self.fault,
                "bus": self.bus,
                "frequency_Hz": float(freq),
                "Z_bus_Ohm": _magnitude(z_bus),
                "Z_network_Ohm": float(abs(self.z_network[freq])),
                "Z_driving_point_Ohm": float(abs(z_dp)),
                "EPR_V": float(abs(u_b)),
                "EPR_deg": float(np.degrees(np.angle(u_b))),
                "I_electrode_A": float(
                    abs(self._electrode_current(z_bus, freq, u))
                ),
                "r_epr": r_epr,
                "r_current": self._earth_return_factor(z_bus, freq, u),
                "Z_G_Ohm": None if z_g is None else float(abs(z_g)),
            }
            for name in self.bus_names:
                if name != self.bus:
                    row[f"EPR_{name}_V"] = float(abs(u[name]))
            rows.append(row)
        # Columns that are legitimately all-null for one case ("open" has no
        # finite electrode impedance) would otherwise come out as Null dtype and
        # refuse to stack with the other cases.
        nullable = ["Z_bus_Ohm", "r_epr", "r_current", "Z_G_Ohm"]
        return pl.DataFrame(rows).with_columns(
            [pl.col(name).cast(pl.Float64) for name in nullable]
        )

    def extremes(self) -> pl.DataFrame:
        """
        The bracket: no electrode, ideal electrode, and the passive worst case.

        Three rows per frequency, labelled ``"open"``, ``"ideal"`` and
        ``"worst_passive"``. The first two are the endpoints of the curve; the
        third is the reactive electrode that maximises the driving-point
        magnitude (see :meth:`worst_case_electrode`).
        """
        frames = [
            self.evaluate(None, label="open"),
            self.evaluate(0.0, label="ideal"),
        ]
        worst_rows = []
        for freq in self.frequencies:
            worst = self.worst_case_electrode(freq)
            frame = self.evaluate(worst["z_bus"], label="worst_passive")
            worst_rows.append(frame.filter(pl.col("frequency_Hz") == float(freq)))
        frames.append(pl.concat(worst_rows, how="diagonal"))
        return pl.concat(frames, how="diagonal")

    def sweep(
        self,
        z_values: Sequence[ElectrodeSpec],
        *,
        labels: Optional[Sequence[str]] = None,
    ) -> pl.DataFrame:
        """
        Evaluate many electrodes at once. No solve, whatever the length.

        Parameters
        ----------
        z_values : sequence
            Electrodes, in the spellings :meth:`evaluate` accepts.
        labels : sequence of str, optional
            One label per value. Defaults to a rendering of each value.
        """
        if labels is not None and len(labels) != len(z_values):
            raise ValueError(
                f"{len(z_values)} electrode(s) were given but {len(labels)} "
                f"label(s); they have to correspond one to one."
            )
        frames = [
            self.evaluate(z, label=None if labels is None else labels[i])
            for i, z in enumerate(z_values)
        ]
        return pl.concat(frames, how="diagonal")

    def __str__(self):
        return (
            f"BusResponse(bus={self.bus}, fault={self.fault}, "
            f"frequencies={self.frequencies})"
        )


def _render(z_bus: ElectrodeSpec) -> str:
    """Readable label for an electrode."""
    if z_bus is None:
        return "open"
    z = complex(z_bus)
    if np.isinf(z.real) or np.isinf(z.imag):
        return "open"
    if z == 0:
        return "ideal"
    if z.imag == 0:
        return f"{z.real:g} Ohm"
    return f"{z.real:g}{z.imag:+g}j Ohm"


def _magnitude(z_bus: ElectrodeSpec) -> Optional[float]:
    """Magnitude of an electrode for the frame, ``None`` where it is infinite."""
    if z_bus is None:
        return None
    z = complex(z_bus)
    if np.isinf(z.real) or np.isinf(z.imag):
        return None
    return float(abs(z))


def bus_response(
    network: Network,
    *,
    fault: str,
    bus: Optional[str] = None,
) -> BusResponse:
    """
    Build the closed-form response of the network to the electrode at one bus.

    Requires a solved fault: the assembled nodal system is reused, the bus's own
    shunt is taken back out of it, and two systems are solved once each -- the
    fault with the electrode removed, and a unit injection at the bus. Neither
    depends on the electrode, which is why the result covers every electrode.

    Parameters
    ----------
    network : Network
        A network with ``network.results[fault]`` present, i.e. after
        :func:`~groundinsight.network_operations.run_fault`.
    fault : str
        The solved fault.
    bus : str, optional
        Bus whose electrode is the free parameter. Defaults to the fault bus.

    Returns
    -------
    BusResponse

    Raises
    ------
    ValueError
        If the fault has not been solved, if the bus is unknown or inactive, or
        if the network without that electrode has no path to reference earth --
        in which case there is nothing to characterise, because the location's
        behaviour *is* its own electrode.

    Examples
    --------
    >>> gi.run_fault(net, "F1")  # doctest: +SKIP
    >>> response = gi.bus_response(net, fault="F1")  # doctest: +SKIP
    >>> response.extremes()  # doctest: +SKIP
    >>> response.evaluate(7.5)   # any electrode, no solve  # doctest: +SKIP
    """
    if fault not in network.results:
        raise ValueError(
            f"Fault '{fault}' has no result on network '{network.name}'. The "
            f"response is read off the assembled nodal system, so call "
            f"run_fault first."
        )
    electrical = network.electrical_network
    if electrical is None or not electrical.Y_matrices:
        raise ValueError(
            f"Network '{network.name}' carries no assembled electrical network. "
            f"Call run_fault before asking for a bus response."
        )

    target = bus if bus is not None else network.faults[fault].bus
    if target not in electrical.bus_indices:
        known = sorted(electrical.bus_indices)
        raise ValueError(
            f"Bus '{target}' is not part of the solved system of network "
            f"'{network.name}'. Active buses: {known}."
        )

    index = electrical.bus_indices
    order = sorted(index, key=lambda name: index[name])
    k = index[target]
    frequencies = [float(f) for f in network.frequencies]

    result = network.results[fault]
    reduction = result.reduction_factor
    r_epr = dict(reduction.value) if reduction is not None else {}

    z_network: Dict[float, complex] = {}
    u_open: Dict[float, Dict[str, complex]] = {}
    z_column: Dict[float, Dict[str, complex]] = {}
    other_impedance: Dict[float, Dict[str, complex]] = {}
    i_fault: Dict[float, complex] = {}

    for freq in frequencies:
        Y = electrical.Y_matrices.get(freq)
        i_vector = electrical.i_vectors.get(freq)
        if Y is None or i_vector is None:
            continue
        Y0 = np.array(Y, dtype=complex, copy=True)

        # Take the bus's own shunt back out. The same rules as the assembly:
        # a missing or infinite impedance never contributed one in the first
        # place.
        z_bus_now = electrical._resolved_impedance(
            network.buses[target].impedance.get(freq), freq
        )
        if z_bus_now is not None and np.isfinite(z_bus_now) and z_bus_now != 0:
            Y0[k, k] -= 1.0 / z_bus_now

        # The check is structural, not a tolerance: at least one *other* bus has
        # to carry a finite, non-zero grounding impedance once the target's
        # shunt is gone. Leaving it to the linear solver is not enough --
        # measured on the exactly singular case, numpy returns a finite but
        # meaningless -2.25e15 instead of raising, the same inconsistency the
        # DC work found in scipy's splu.
        earthed_elsewhere = any(
            (
                z := electrical._resolved_impedance(
                    network.buses[name].impedance.get(freq), freq
                )
            )
            is not None
            and np.isfinite(z)
            and z != 0
            for name in order
            if name != target
        )
        if not earthed_elsewhere:
            raise ValueError(
                f"With the electrode at bus '{target}' removed, no other bus of "
                f"network '{network.name}' has a finite grounding impedance at "
                f"{freq} Hz, so there is no path to reference earth. There is "
                f"nothing to characterise independently of that electrode: at "
                f"this location the network's behaviour *is* the electrode. "
                f"Pick a bus that has at least one other earthed bus behind it."
            )

        unit = np.zeros(len(order), dtype=complex)
        unit[k] = 1.0
        try:
            u0 = np.linalg.solve(Y0, i_vector)
            z_col = np.linalg.solve(Y0, unit)
        except np.linalg.LinAlgError as exc:
            raise ValueError(
                f"The nodal system of network '{network.name}' is singular at "
                f"{freq} Hz once the electrode at bus '{target}' is removed, "
                f"even though other buses are earthed. Check for a subnetwork "
                f"that reaches earth only through '{target}'."
            ) from exc
        if not (np.all(np.isfinite(u0)) and np.all(np.isfinite(z_col))):
            raise ValueError(
                f"The response of bus '{target}' at {freq} Hz came out "
                f"non-finite. That points at an infinite or NaN impedance "
                f"somewhere in network '{network.name}' rather than at the bus "
                f"itself."
            )

        z_network[freq] = complex(z_col[k])
        u_open[freq] = {name: complex(u0[index[name]]) for name in order}
        z_column[freq] = {name: complex(z_col[index[name]]) for name in order}

        impedances: Dict[str, complex] = {}
        for name in order:
            z = electrical._resolved_impedance(
                network.buses[name].impedance.get(freq), freq
            )
            if z is not None and np.isfinite(z) and z != 0:
                impedances[name] = z
        other_impedance[freq] = impedances

        total_source = electrical.total_source_currents.get(freq)
        if total_source is not None:
            i_fault[freq] = -complex(total_source)

    if not z_network:
        raise ValueError(
            f"No frequency of fault '{fault}' carried an assembled system, so "
            f"there is no response to build."
        )

    return BusResponse(
        fault=fault,
        bus=target,
        fault_bus=network.faults[fault].bus,
        frequencies=[f for f in frequencies if f in z_network],
        bus_names=order,
        z_network=z_network,
        u_open=u_open,
        z_column=z_column,
        other_impedance=other_impedance,
        r_epr=r_epr,
        i_fault=i_fault,
    )

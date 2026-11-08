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

The EPR-based reduction factor
------------------------------
``ResultReductionFactor.value`` is ``|u_F|/|u_F^{\\,\\text{no mutual}}|`` at the
**fault** bus. When the bus being varied *is* the fault bus, the closed form
makes it constant along the whole curve: the voltage there is
``u_b(Y_B) = u_{0,b} / (1 + Y_B Z_\\text{net})`` with and without mutual coupling
alike, so the denominator cancels out of the quotient exactly. That is the same
invariance the sensitivity study runs into, here derived rather than measured.

Away from the fault bus it does **not** cancel: the fault-bus voltage is
``u_F(Y_B) = u_{0,F} - Y_B z_F u_{0,b} / (1 + Y_B Z_\\text{net})``, a difference
whose two terms scale differently, and the quotient moves with the electrode
(1.147 / 1.056 / 0.973 over ``Z_B`` = 0.1 / 10 / 1000 Ω on the four-bus chain of
the review). It is therefore evaluated per electrode, from a **third** right-hand
side of the same factorisation -- the mutual-free injection vector -- rather than
carried through as an invariant.

The three cases are extremes of the **local** quantities only -- the potential
rise at the bus itself and its driving-point impedance. Quantities transferred
to *other* buses are not bounded by them: the EPR at a remote bus and the
current-based reduction factor can both exceed the ``ideal`` value for a
passive, purely capacitive ``Z_B``, because the phase of ``Y_B`` enters those
through a different combination than through ``1 + Y_B Z_net`` alone. Use
:meth:`BusResponse.sweep` -- which costs no solve -- when the worst case of a
transfer quantity is what a study needs.
"""

from __future__ import annotations

import logging
from typing import Dict, List, Optional, Sequence, Union

import numpy as np
import polars as pl
from pydantic import BaseModel, Field

from groundinsight.models.core_models import Network
from groundinsight.utils.earth_current import (
    NOISE_REL,
    earth_current_vanishes,
    split_earth_currents,
)

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
    u_open_no_mutual : dict of float to dict of str to complex
        The same, for the injection vector *without* the mutual Norton
        equivalents -- the third right-hand side of the same factorisation.
        It is what makes the EPR-based reduction factor evaluable per electrode
        instead of assumed invariant. Empty when the solve carried no mutual-free
        vector, in which case :meth:`evaluate` falls back to :attr:`r_epr`.
    z_column : dict of float to dict of str to complex
        Voltage at every bus per ampere injected at ``bus``, source-free.
    r_epr : dict of float to float or None
        The EPR-based reduction factor of the **solved** fault, i.e. for the
        electrode that was installed at ``bus`` when :func:`bus_response` ran.
        It is constant along the curve only for ``bus == fault_bus``; elsewhere
        use the ``r_epr`` column of :meth:`evaluate`, which is evaluated for the
        electrode in hand.
    i_fault : dict of float to complex
        Fault current, the negated sum of source injections. Constant.
    z_fault_open : dict of float to complex
        Driving-point impedance of the earthing network at the **fault** bus
        with the electrode at ``bus`` removed (Thevenin loop closures
        excluded) -- ``Z0_FF``.
    z_fault_transfer : dict of float to complex
        Transfer impedance between ``bus`` and the fault bus in that network
        -- ``Z0_Fb``.
    z_bus_open : dict of float to complex
        Driving-point impedance at ``bus`` in that network -- ``Z0_bb``; equal
        to :attr:`z_network` when the network has no voltage source.
    """

    model_config = {"arbitrary_types_allowed": True}

    fault: str
    bus: str
    fault_bus: str
    frequencies: List[float]
    bus_names: List[str]
    z_network: Dict[float, complex]
    u_open: Dict[float, Dict[str, complex]]
    u_open_no_mutual: Dict[float, Dict[str, complex]] = Field(default_factory=dict)
    z_column: Dict[float, Dict[str, complex]]
    other_impedance: Dict[float, Dict[str, complex]] = Field(default_factory=dict)
    r_epr: Dict[float, Optional[float]] = Field(default_factory=dict)
    i_fault: Dict[float, complex] = Field(default_factory=dict)
    z_fault_open: Dict[float, complex] = Field(default_factory=dict)
    z_fault_transfer: Dict[float, complex] = Field(default_factory=dict)
    z_bus_open: Dict[float, complex] = Field(default_factory=dict)

    # -- the curve ------------------------------------------------------------

    def admittance(self, z_bus: ElectrodeSpec) -> complex:
        """
        Translate an electrode spelling into a shunt admittance.

        ``None`` and an infinite impedance both mean *no electrode*
        (``Y_B = 0``); a zero impedance means an *ideal* one and is returned as
        ``inf``, which :meth:`voltages` handles as a limit rather than a
        division.

        Notes
        -----
        The electrode is a value, not a frequency-dependent model: the
        frequency dependence lives in :attr:`z_network` and in whatever the
        caller passes per frequency. The method used to take a ``freq``
        argument that it never read; it was removed in 0.6.1 rather than left
        as a parameter that promises an influence it does not have.
        """
        if z_bus is None:
            return 0.0 + 0.0j
        z = complex(z_bus)
        if np.isinf(z.real) or np.isinf(z.imag):
            return 0.0 + 0.0j
        if z == 0:
            return complex(np.inf, 0.0)
        return 1.0 / z

    def _with_electrode(
        self, u0: Dict[str, complex], z_bus: ElectrodeSpec, freq: float
    ) -> Dict[str, complex]:
        """
        Apply the Möbius form to one open-circuit voltage vector.

        Shared by :meth:`voltages` and :meth:`voltages_no_mutual`: the electrode
        enters both the same way -- it is a shunt in the same matrix -- and only
        the right-hand side behind ``u0`` differs.
        """
        z_col = self.z_column[freq]
        u0_b = u0[self.bus]
        z_net = self.z_network[freq]
        y = self.admittance(z_bus)

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

    def voltages(self, z_bus: ElectrodeSpec, freq: float) -> Dict[str, complex]:
        """
        Nodal voltages for one electrode at one frequency.

        Both endpoints are evaluated as limits, so an ideal electrode gives
        exactly zero at ``bus`` instead of a very small number.
        """
        return self._with_electrode(self.u_open[freq], z_bus, freq)

    def voltages_no_mutual(
        self, z_bus: ElectrodeSpec, freq: float
    ) -> Optional[Dict[str, complex]]:
        """
        The same, for the injection vector without the mutual Norton sources.

        This is the denominator of the EPR-based reduction factor. ``None`` when
        the response was built without that right-hand side.
        """
        u0 = self.u_open_no_mutual.get(freq)
        if u0 is None:
            return None
        return self._with_electrode(u0, z_bus, freq)

    def driving_point(self, z_bus: ElectrodeSpec, freq: float) -> complex:
        """``Z_dp = 1 / (Y_B + 1/Z_net)`` -- the impedance seen at ``bus``."""
        y = self.admittance(z_bus)
        if np.isinf(y.real):
            return 0.0 + 0.0j
        z_net = self.z_network[freq]
        if z_net == 0:
            return 0.0 + 0.0j
        total = y + 1.0 / z_net
        if total == 0:
            return complex(np.inf, 0.0)
        return 1.0 / total

    def fault_driving_point(
        self, z_bus: ElectrodeSpec, freq: float
    ) -> Optional[complex]:
        """
        ``Z_E`` -- the driving-point impedance at the **fault** bus -- for one
        electrode at ``bus``.

        The shunt ``Y_B`` at ``bus`` is a rank-one change of the earthing
        network, so the fault-bus driving point is Möbius in it as well:

        ``Z_FF(Y_B) = Z0_FF - Y_B * Z0_Fb**2 / (1 + Y_B * Z0_bb)``

        with both endpoints as exact limits. It is the quantity
        ``run_fault`` reports as ``ResultReductionFactor.z_earthing`` and as
        the grounding impedance (decision F4); for ``bus == fault_bus`` it
        reduces to :meth:`driving_point` of a network without voltage source.

        Parameters
        ----------
        z_bus : complex, float or None
            The electrode at ``bus``, as for :meth:`evaluate`.
        freq : float
            Frequency in Hz.

        Returns
        -------
        complex or None
            ``None`` when the response carries no earthing-network columns for
            ``freq`` (the fault bus reaches remote earth only through a
            Thevenin loop).
        """
        z_ff = self.z_fault_open.get(freq)
        z_fb = self.z_fault_transfer.get(freq)
        z_bb = self.z_bus_open.get(freq)
        if z_ff is None or z_fb is None or z_bb is None:
            return None
        y = self.admittance(z_bus)
        if np.isinf(y.real):
            if z_bb == 0:
                return z_ff
            return z_ff - z_fb * z_fb / z_bb
        if y == 0:
            return z_ff
        denominator = 1.0 + y * z_bb
        if denominator == 0:
            return None
        return z_ff - y * z_fb * z_fb / denominator

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

        Raises
        ------
        ValueError
            If ``Re(1/Z_net) <= 0``. The bound ``1/Re(Y_net)`` is only the
            maximum while the network's conductance is positive; a
            non-positive one turns the same expression into a negative or
            infinite "worst case" that reads like an answer.
        """
        z_net = self.z_network[freq]
        if z_net == 0 or not np.isfinite(z_net):
            return {"z_bus": complex(np.inf, 0.0), "z_driving_point": z_net}
        y_net = 1.0 / z_net
        if y_net.real <= 0:
            raise ValueError(
                f"The network at bus '{self.bus}' has a non-positive "
                f"conductance at {freq} Hz (Re(1/Z_net) = {y_net.real:g}), so "
                f"the passive worst case 1/Re(Y_net) is not a maximum -- it "
                f"would come back negative or infinite. A passive network "
                f"cannot do this: check the grounding impedances of network "
                f"'{self.fault}' behind this bus, and whether the assembled "
                f"system still matches them (see run_fault)."
            )
        y_b = complex(0.0, -y_net.imag)
        if y_b == 0:
            return {"z_bus": complex(np.inf, 0.0), "z_driving_point": z_net}
        return {"z_bus": 1.0 / y_b, "z_driving_point": 1.0 / y_net.real}

    # -- derived engineering quantities ---------------------------------------

    def _electrode_current(
        self, z_bus: ElectrodeSpec, freq: float, u: Dict[str, complex]
    ) -> complex:
        """Current into the soil through the electrode at ``bus``."""
        y = self.admittance(z_bus)
        if np.isinf(y.real):
            # Ideal electrode: u_b is zero but the product is not. The limit of
            # Y_B * u_b / (1 + Y_B Z_net) is u_0b / Z_net.
            z_net = self.z_network[freq]
            if z_net == 0:
                return complex(np.nan, np.nan)
            return self.u_open[freq][self.bus] / z_net
        return y * u[self.bus]

    def _epr_reduction_factor(
        self, z_bus: ElectrodeSpec, freq: float, u: Dict[str, complex]
    ) -> Optional[float]:
        """
        ``r = |u_F| / |u_F^{no mutual}|`` at the **fault** bus, for one electrode.

        Both voltages follow the same Möbius form in ``Y_B``; what differs is
        the right-hand side behind them, which is why the third solve is needed
        and why the factor does not cancel unless ``bus`` is the fault bus.

        Falls back to the value of the solved fault (:attr:`r_epr`) when the
        mutual-free vector is absent -- and that value is exact in the one case
        where it is invariant.
        """
        if self.bus == self.fault_bus or not self.u_open_no_mutual:
            return self.r_epr.get(freq)
        u_nm = self.voltages_no_mutual(z_bus, freq)
        if u_nm is None or self.fault_bus not in u_nm:
            return self.r_epr.get(freq)
        denominator = abs(u_nm[self.fault_bus])
        # The noise rule of run_fault (P9): the denominator must stand above
        # 1e-9 of the EPR the fault current alone raises the fault bus to.
        z_ff = self.fault_driving_point(z_bus, freq)
        i_fault = self.i_fault.get(freq)
        reference = 0.0
        if z_ff is not None and i_fault is not None:
            reference = abs(i_fault) * abs(z_ff)
        if not denominator > NOISE_REL * reference:
            return None
        return float(abs(u[self.fault_bus]) / denominator)

    def _earth_quantities(
        self, z_bus: ElectrodeSpec, freq: float, u: Dict[str, complex]
    ) -> Dict[str, Optional[complex]]:
        """
        The current-based reduction factor of the bonded earthing system.

        ``r_I = |I_E| / |I_F|`` with ``I_E`` summed over every bus that feeds the
        soil, matching ``ResultReductionFactor.value_current``, under the same
        noise rules (P9). The earthing impedance is no longer derived from the
        group: it is the fault-bus driving point,
        :meth:`fault_driving_point` (decision F4).

        The bus being varied is included like any other -- its electrode is part
        of the earthing system, and leaving it out would reduce the whole sum to
        the electrode current of one station.

        Parameters
        ----------
        z_bus : complex, float or None
            The electrode under test.
        freq : float
            Frequency in Hz.
        u : dict of str to complex
            Nodal voltages for that electrode.

        Returns
        -------
        dict
            ``r_current`` (float or None).
        """
        empty: Dict[str, Optional[complex]] = {"r_current": None}
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
        i_fault = self.i_fault.get(freq)
        # The noise rules of run_fault (P9): no group when every electrode
        # current is rounding noise against the fault current, and the
        # fault-bus potential orients a split without a fault-bus electrode.
        if i_fault is not None and earth_current_vanishes(electrode_currents, i_fault):
            return empty
        u_fault = u.get(self.fault_bus, 0j)
        u_scale = max((abs(v) for v in u.values()), default=0.0)
        anchor = u_fault if abs(u_fault) > NOISE_REL * u_scale else None
        split = split_earth_currents(
            electrode_currents, reference_bus=self.fault_bus, anchor=anchor
        )
        if split is None:
            return empty

        r_current = (
            abs(split.i_earth) / abs(i_fault)
            if i_fault is not None and i_fault != 0
            else None
        )
        return {"r_current": r_current}

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

        Notes
        -----
        ``r_epr`` is evaluated **for this electrode** at the fault bus (see the
        module docstring); it is constant across electrodes only when ``bus`` is
        the fault bus. ``Z_G_Ohm`` is the EN 50522 earthing impedance
        ``|Z_E|``: the driving-point impedance of the earthing network at the
        **fault** bus for this electrode (:meth:`fault_driving_point`), the same
        definition :func:`~groundinsight.network_operations.run_fault` reports
        (decision F4). For ``bus == fault_bus`` it equals
        ``Z_driving_point_Ohm`` when the network has no voltage source.
        """
        return _frame(self._rows(z_bus, label, self.frequencies))

    def _rows(
        self,
        z_bus: ElectrodeSpec,
        label: Optional[str],
        frequencies: Sequence[float],
    ) -> List[Dict[str, object]]:
        """
        The rows :meth:`evaluate` builds, for a chosen subset of frequencies.

        Split out so :meth:`extremes` can ask for the one frequency whose worst
        case it is evaluating instead of building every frequency and throwing
        all but one row away -- which made that method quadratic in the number
        of frequencies (126 ms at 20 frequencies against 3 ms at one).
        """
        rows: List[Dict[str, object]] = []
        for freq in frequencies:
            u = self.voltages(z_bus, freq)
            z_dp = self.driving_point(z_bus, freq)
            u_b = u[self.bus]
            r_epr = self._epr_reduction_factor(z_bus, freq, u)
            earth = self._earth_quantities(z_bus, freq, u)
            z_g = self.fault_driving_point(z_bus, freq)
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
                "r_current": earth["r_current"],
                "Z_G_Ohm": None if z_g is None else float(abs(z_g)),
            }
            for name in self.bus_names:
                if name != self.bus:
                    row[f"EPR_{name}_V"] = float(abs(u[name]))
            rows.append(row)
        return rows

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
        worst_rows: List[Dict[str, object]] = []
        for freq in self.frequencies:
            worst = self.worst_case_electrode(freq)
            worst_rows.extend(self._rows(worst["z_bus"], "worst_passive", [freq]))
        frames.append(_frame(worst_rows))
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

        Raises
        ------
        ValueError
            If ``z_values`` is empty, or if ``labels`` has a different length.
            An empty sweep used to reach ``pl.concat([])`` and come back as a
            polars error about an empty iterable, which names neither the
            argument nor the caller's mistake.
        """
        if not len(z_values):
            raise ValueError(
                "sweep() needs at least one electrode; an empty z_values has "
                "no frame to build. Pass the electrodes to evaluate, e.g. "
                "sweep([1.0, 10.0, None])."
            )
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


def _is_electrode(z: Optional[complex]) -> bool:
    """``True`` for a resolved impedance that contributes a shunt to ``Y``."""
    return z is not None and np.isfinite(z) and z != 0


def _frame(rows: List[Dict[str, object]]) -> pl.DataFrame:
    """
    Build the evaluation frame with the nullable columns pinned to ``Float64``.

    Columns that are legitimately all-null for one case ("open" has no finite
    electrode impedance) would otherwise come out as ``Null`` dtype and refuse
    to stack with the other cases.
    """
    nullable = ["Z_bus_Ohm", "r_epr", "r_current", "Z_G_Ohm"]
    return pl.DataFrame(rows).with_columns(
        [pl.col(name).cast(pl.Float64) for name in nullable]
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
    shunt is taken back out of it, and three right-hand sides are solved against
    the one factorisation -- the fault with the electrode removed, a unit
    injection at the bus, and the same fault without the mutual Norton
    equivalents. None of them depends on the electrode, which is why the result
    covers every electrode.

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
    # The response is read off the assembled matrices, and those are a
    # snapshot: an impedance changed after ``run_fault`` -- by hand, or by a
    # study that scaled rho and put it back -- does not reach them. Reading a
    # stale Y next to the current impedances does not fail loudly; it produced
    # a *negative* driving-point impedance in the review case, because the
    # shunt taken out below was never the shunt that went in. So refuse rather
    # than answer.
    if not electrical.matches_network_state():
        raise ValueError(
            f"The assembled nodal system of network '{network.name}' was built "
            f"from different model values than the ones the network carries "
            f"now (impedances, active flags or the active fault changed since "
            f"the last run_fault). A bus response read off it would mix the two "
            f"states and can come out non-physical -- a negative driving-point "
            f"impedance, for instance. Call run_fault again for fault "
            f"'{fault}' before asking for the response."
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
    u_open_no_mutual: Dict[float, Dict[str, complex]] = {}
    z_column: Dict[float, Dict[str, complex]] = {}
    other_impedance: Dict[float, Dict[str, complex]] = {}
    i_fault: Dict[float, complex] = {}
    z_fault_open: Dict[float, complex] = {}
    z_fault_transfer: Dict[float, complex] = {}
    z_bus_open: Dict[float, complex] = {}
    fault_bus = network.faults[fault].bus

    for freq in frequencies:
        Y = electrical.Y_matrices.get(freq)
        i_vector = electrical.i_vectors.get(freq)
        if Y is None or i_vector is None:
            continue
        Y0 = np.array(Y, dtype=complex, copy=True)

        # Resolve every bus impedance once for this frequency. The same values
        # answer three questions below -- the shunt to remove, whether any other
        # bus reaches earth, and the electrode currents -- and resolving them
        # once per bus instead of three times per bus is where the frequency
        # loop was spending its time.
        resolved: Dict[str, Optional[complex]] = {
            name: electrical._resolved_impedance(
                network.buses[name].impedance.get(freq), freq
            )
            for name in order
        }

        # Take the bus's own shunt back out. The same rules as the assembly:
        # a missing or infinite impedance never contributed one in the first
        # place.
        z_bus_now = resolved[target]
        if _is_electrode(z_bus_now):
            Y0[k, k] -= 1.0 / z_bus_now

        # The check is structural, not a tolerance: at least one *other* bus has
        # to carry a finite, non-zero grounding impedance once the target's
        # shunt is gone. Leaving it to the linear solver is not enough --
        # measured on the exactly singular case, numpy returns a finite but
        # meaningless -2.25e15 instead of raising, the same inconsistency the
        # DC work found in scipy's splu.
        earthed_elsewhere = any(
            _is_electrode(z) for name, z in resolved.items() if name != target
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
        # Three right-hand sides, one factorisation: the fault injection, the
        # unit injection at the bus, and -- when the solve carried it -- the
        # same fault injection without the mutual Norton equivalents. The third
        # is what makes ``r_epr`` evaluable per electrode (it is the denominator
        # of the reduction factor); asking numpy for all of them at once costs
        # one triangular solve each on top of the single LU.
        i_no_mutual = electrical.i_vectors_no_mutual.get(freq)
        rhs = [np.asarray(i_vector, dtype=complex), unit]
        if i_no_mutual is not None:
            rhs.append(np.asarray(i_no_mutual, dtype=complex))
        try:
            solved = np.linalg.solve(Y0, np.column_stack(rhs))
            u0 = solved[:, 0]
            z_col = solved[:, 1]
            u0_nm = solved[:, 2] if i_no_mutual is not None else None
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
        if u0_nm is not None:
            u_open_no_mutual[freq] = {
                name: complex(u0_nm[index[name]]) for name in order
            }

        other_impedance[freq] = {
            name: z for name, z in resolved.items() if _is_electrode(z)
        }

        # The earthing network without the electrode at ``target`` and without
        # the Thevenin loop closures: three numbers make the fault-bus driving
        # point Z_E available for every electrode (fault_driving_point, F4).
        earth_columns = _earthing_columns(electrical, Y0, k, index[fault_bus], freq)
        if earth_columns is not None:
            z_fault_open[freq], z_fault_transfer[freq], z_bus_open[freq] = earth_columns

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
        u_open_no_mutual=u_open_no_mutual,
        z_column=z_column,
        other_impedance=other_impedance,
        r_epr=r_epr,
        i_fault=i_fault,
        z_fault_open=z_fault_open,
        z_fault_transfer=z_fault_transfer,
        z_bus_open=z_bus_open,
    )


def _earthing_columns(electrical, Y0: np.ndarray, bus_idx: int, fault_idx: int,
                      freq: float):
    """
    ``(Z0_FF, Z0_Fb, Z0_bb)`` of the earthing network with one electrode removed.

    ``Y0`` is the solved matrix with the shunt at ``bus_idx`` taken out; the
    Thevenin loop closures of voltage sources are taken out as well, because
    they belong to the fault loop and not to the earthing network whose
    driving point ``Z_E`` is (decision F4).

    Parameters
    ----------
    electrical : ElectricalNetwork
        The assembled system, for its Thevenin stamps.
    Y0 : numpy.ndarray
        Solved matrix without the electrode at ``bus_idx``.
    bus_idx, fault_idx : int
        Rows of the varied bus and of the fault bus.
    freq : float
        Frequency in Hz.

    Returns
    -------
    tuple of complex or None
        ``None`` if the fault bus reaches remote earth through nothing but a
        Thevenin loop once the electrode is gone.
    """
    Y_earth = np.array(Y0, dtype=complex, copy=True)
    for src_idx, loop_idx, y_src in electrical.thevenin_loop_admittances(freq):
        Y_earth[src_idx, src_idx] -= y_src
        Y_earth[loop_idx, loop_idx] -= y_src
        Y_earth[src_idx, loop_idx] += y_src
        Y_earth[loop_idx, src_idx] += y_src
    units = np.zeros((Y_earth.shape[0], 2), dtype=complex)
    units[bus_idx, 0] = 1.0
    units[fault_idx, 1] = 1.0
    try:
        columns = np.linalg.solve(Y_earth, units)
    except np.linalg.LinAlgError:
        return None
    if not np.all(np.isfinite(columns)):
        return None
    return (
        complex(columns[fault_idx, 1]),
        complex(columns[fault_idx, 0]),
        complex(columns[bus_idx, 0]),
    )

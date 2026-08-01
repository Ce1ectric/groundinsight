# simulation/waveforms.py

"""
Waveform Library for Transient Simulations.

This module provides a small library of factory functions that return
``Callable[[np.ndarray], np.ndarray]`` waveforms suitable for the
:class:`groundinsight.simulation.transient.TransientStudy` solver. Each
factory captures its own parameters and returns a vectorised function
``f(t) -> values`` that can be called with an array of time samples.

The included waveforms cover the typical fault-current scenarios for low-
and medium-voltage grounding studies:

* :func:`step` -- a Heaviside-style on/off pulse, the simplest fault model
  (the source comes on at ``t_on`` and off again at ``t_off``).
* :func:`sinusoidal_with_dc_offset` -- a power-frequency current with an
  exponentially decaying DC component, the textbook representation of a
  single-line-to-ground fault current including the asymmetry caused by
  the inductive loop.
* :func:`damped_oscillation` -- a damped sinusoid useful for switching
  transients and ringing studies.

Custom waveforms can be defined as any user function that accepts a 1-D
``np.ndarray`` of time samples and returns an array of the same shape.
"""

import numpy as np
from typing import Callable, Optional


def _validate_window(t_on: float, t_off: Optional[float], factory: str) -> None:
    """Raise ``ValueError`` for inverted on/off windows.

    A common user error — typically a unit confusion between ms and s —
    is to pass ``t_off < t_on``. The resulting waveform is identically
    zero and produces a baffling "transient is flat" plot. Reject the
    inversion at factory time so the user gets a clear message instead.
    """
    if t_off is None:
        return
    if not np.isfinite(t_off) or not np.isfinite(t_on):
        raise ValueError(
            f"{factory}: t_on and t_off must be finite numbers "
            f"(got t_on={t_on!r}, t_off={t_off!r})."
        )
    if t_off <= t_on:
        raise ValueError(
            f"{factory}: t_off must be strictly greater than t_on "
            f"(got t_on={t_on!r}, t_off={t_off!r}). The waveform would "
            "be identically zero — check for a units confusion "
            "(ms vs s)."
        )


def step(
    amplitude: float,
    *,
    t_on: float = 0.0,
    t_off: Optional[float] = None,
) -> Callable[[np.ndarray], np.ndarray]:
    """
    Construct a rectangular pulse waveform.

    The output is zero before ``t_on``, equals ``amplitude`` between
    ``t_on`` and ``t_off`` and is zero again after ``t_off``. With
    ``t_off=None`` the pulse extends to the end of the time grid
    (a classic Heaviside step).

    Parameters
    ----------
    amplitude : float
        Plateau value of the pulse.
    t_on : float, optional
        Time at which the pulse switches on, in seconds. Defaults to
        ``0.0``.
    t_off : float, optional
        Time at which the pulse switches off, in seconds. ``None`` means
        "stays on indefinitely". Defaults to ``None``.

    Returns
    -------
    callable
        A vectorised waveform function ``f(t) -> values``.

    Examples
    --------
    >>> import numpy as np
    >>> w = step(amplitude=100.0, t_on=0.02, t_off=0.12)
    >>> y = w(np.linspace(0.0, 0.2, 5))
    """
    _validate_window(t_on, t_off, "step")

    def _wave(t: np.ndarray) -> np.ndarray:
        t = np.asarray(t, dtype=float)
        on = t >= t_on
        if t_off is not None:
            on = on & (t < t_off)
        return np.where(on, amplitude, 0.0)

    return _wave


def sinusoidal_with_dc_offset(
    amplitude: float,
    frequency_hz: float,
    *,
    phase_rad: float = 0.0,
    t_on: float = 0.0,
    t_off: Optional[float] = None,
    dc_amplitude: float = 0.0,
    dc_decay_tau: Optional[float] = None,
) -> Callable[[np.ndarray], np.ndarray]:
    """
    Construct a windowed sinusoid with optional exponentially decaying DC.

    Models the typical single-line-to-ground fault current::

        i(t) = A * sin(omega*(t - t_on) + phi) + I_dc * exp(-(t - t_on)/tau)

    multiplied by a rectangular window between ``t_on`` and ``t_off``.
    The DC component captures the asymmetric peak that arises when the
    fault occurs at a non-zero-crossing instant of the source voltage
    and the loop has finite inductance.

    Parameters
    ----------
    amplitude : float
        Peak value of the AC component.
    frequency_hz : float
        Power frequency in Hz (e.g. 50 or 60).
    phase_rad : float, optional
        Initial phase of the AC component, in radians. Defaults to ``0.0``.
    t_on : float, optional
        Fault initiation time, in seconds. Defaults to ``0.0``.
    t_off : float, optional
        Fault clearing time, in seconds. ``None`` keeps the fault on
        until the end of the time grid.
    dc_amplitude : float, optional
        Initial value of the DC component. Defaults to ``0.0`` (no DC
        offset).
    dc_decay_tau : float, optional
        Time constant ``L/R`` of the loop governing the DC decay, in
        seconds. ``None`` disables the decay (DC stays constant during
        the on-window). Required when ``dc_amplitude != 0`` and a finite
        decay is desired.

    Returns
    -------
    callable
        A vectorised waveform ``f(t) -> values``.

    Examples
    --------
    >>> w = sinusoidal_with_dc_offset(
    ...     amplitude=20e3, frequency_hz=50.0,
    ...     t_on=0.02, t_off=0.12,
    ...     dc_amplitude=10e3, dc_decay_tau=0.05,
    ... )
    """
    if not np.isfinite(frequency_hz) or frequency_hz <= 0:
        raise ValueError(
            "sinusoidal_with_dc_offset: frequency_hz must be a finite "
            f"positive number (got {frequency_hz!r}). Negative frequency "
            "is silently equivalent to flipping the phase and "
            "frequency_hz == 0 collapses to a constant offset that "
            "masks user confusion with the dc_amplitude term."
        )
    if dc_decay_tau is not None and (
        not np.isfinite(dc_decay_tau) or dc_decay_tau <= 0
    ):
        raise ValueError(
            "sinusoidal_with_dc_offset: dc_decay_tau must be a finite "
            f"positive number when set (got {dc_decay_tau!r}). Pass "
            "dc_decay_tau=None to disable the decay altogether."
        )
    _validate_window(t_on, t_off, "sinusoidal_with_dc_offset")
    omega = 2.0 * np.pi * frequency_hz

    def _wave(t: np.ndarray) -> np.ndarray:
        t = np.asarray(t, dtype=float)
        on = t >= t_on
        if t_off is not None:
            on = on & (t < t_off)

        tau_local = t - t_on
        ac = amplitude * np.sin(omega * tau_local + phase_rad)
        if dc_amplitude == 0.0:
            dc = np.zeros_like(t)
        elif dc_decay_tau is None:
            dc = np.full_like(t, dc_amplitude)
        else:
            # Exponential decay starting at t_on, only meaningful in the
            # on-window. Outside the window the rectangular factor below
            # forces it to zero anyway.
            dc = dc_amplitude * np.exp(
                -np.maximum(tau_local, 0.0) / dc_decay_tau
            )

        return np.where(on, ac + dc, 0.0)

    return _wave


def damped_oscillation(
    amplitude: float,
    frequency_hz: float,
    decay_tau: float,
    *,
    phase_rad: float = 0.0,
    t_on: float = 0.0,
    t_off: Optional[float] = None,
) -> Callable[[np.ndarray], np.ndarray]:
    """
    Construct a damped sinusoid, useful for switching-transient studies.

    The waveform is::

        x(t) = A * exp(-(t - t_on)/tau) * sin(omega*(t - t_on) + phi)

    inside the on-window ``[t_on, t_off)``, and zero outside.

    Parameters
    ----------
    amplitude : float
        Initial peak amplitude.
    frequency_hz : float
        Oscillation frequency in Hz.
    decay_tau : float
        Decay time constant in seconds.
    phase_rad : float, optional
        Phase offset of the sinusoid, in radians. Defaults to ``0.0``.
    t_on : float, optional
        Onset time, in seconds. Defaults to ``0.0``.
    t_off : float, optional
        Cut-off time, in seconds. ``None`` lets the oscillation decay
        naturally to the end of the time grid.

    Returns
    -------
    callable
        A vectorised waveform.

    Examples
    --------
    >>> w = damped_oscillation(
    ...     amplitude=1e3, frequency_hz=500.0, decay_tau=2e-3,
    ...     t_on=0.01,
    ... )
    """
    if not np.isfinite(frequency_hz) or frequency_hz <= 0:
        raise ValueError(
            "damped_oscillation: frequency_hz must be a finite positive "
            f"number (got {frequency_hz!r})."
        )
    if not np.isfinite(decay_tau) or decay_tau <= 0:
        raise ValueError(
            "damped_oscillation: decay_tau must be a finite positive "
            f"number (got {decay_tau!r}). decay_tau == 0 divides by zero "
            "and decay_tau < 0 produces an exponentially growing "
            "waveform — almost certainly not what was intended."
        )
    _validate_window(t_on, t_off, "damped_oscillation")
    omega = 2.0 * np.pi * frequency_hz

    def _wave(t: np.ndarray) -> np.ndarray:
        t = np.asarray(t, dtype=float)
        on = t >= t_on
        if t_off is not None:
            on = on & (t < t_off)
        tau_local = np.maximum(t - t_on, 0.0)
        envelope = amplitude * np.exp(-tau_local / decay_tau)
        signal = envelope * np.sin(omega * tau_local + phase_rad)
        return np.where(on, signal, 0.0)

    return _wave

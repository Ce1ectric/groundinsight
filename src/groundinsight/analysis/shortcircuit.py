# analysis/shortcircuit.py

"""
IEC 60909-0 short-circuit characteristic quantities and their superposition.

``groundinsight`` solves the *linear* grounding problem: at every frequency
the nodal system ``Y(f) u(f) = i(f)`` is solved and the contributions of all
sources add up by superposition. The IEC 60909-0 characteristic quantities

* the **peak short-circuit current** :math:`i_p = \\kappa \\sqrt{2} I_k''`, and
* the **thermally equivalent short-time current**
  :math:`I_{th} = I_k'' \\sqrt{m + n}`

are however *non-linear* functions of the fault-loop ``R/X`` ratio, so they
must **not** be superposed. This module implements the project rule agreed
for that:

1. The frequency-domain solve superposes the linear AC RMS currents exactly
   as before -- nothing changes there.
2. A single effective peak factor ``kappa`` is resolved for the fault from
   the participating sources (see
   :func:`resolve_fault_sc_characteristics`).
3. The non-linear factors are applied **once**, to the aggregated RMS
   current of each branch.

Why a current-weighted mean is the right aggregation
----------------------------------------------------
With several infeeds the DC components add, so the largest possible peak of
the total current is the sum of the individual peaks,

.. math::

    i_{p,\\Sigma} = \\sqrt{2} \\sum_i \\kappa_i I_i .

Writing that as one factor applied to the aggregated current
:math:`\\sum_i I_i` gives

.. math::

    \\kappa_{eff} = \\frac{\\sum_i \\kappa_i I_i}{\\sum_i I_i},

i.e. exactly the current-weighted mean -- it reproduces the sum of the
individual peaks identically, and degenerates to the common ``kappa`` when
all sources share one ``R/X``. Taking the largest ``kappa`` instead
(``aggregation="max"``) is the strictly conservative variant; taking one
source's ``kappa`` for all of them is simply wrong and can overestimate
``i_p`` by double-digit percentages.

For mixed-``R/X`` infeeds where the simultaneous-peak assumption is too
crude, the transient solver
(:mod:`groundinsight.simulation.transient`) remains the exact fallback: it
integrates the actual waveforms instead of applying a standard factor.

References
----------
IEC 60909-0:2016, clauses 4.3 (``kappa``, ``i_p``) and 4.8 (``m``, ``n``,
``I_th``).
"""

import logging
import math
from typing import Dict, List, Optional

from pydantic import BaseModel, Field

from ..models.core_models import Network


logger = logging.getLogger(__name__)


__all__ = [
    "kappa_from_r_to_x",
    "iec60909_m",
    "peak_short_circuit_current",
    "thermal_equivalent_current",
    "FaultShortCircuitData",
    "resolve_fault_sc_characteristics",
]


# ---------------------------------------------------------------------------
# Primitives
# ---------------------------------------------------------------------------


def kappa_from_r_to_x(r_to_x: float) -> float:
    """
    Peak factor ``kappa`` from the ``R/X`` ratio (IEC 60909-0).

    ``kappa = 1.02 + 0.98 * exp(-3 * R/X)``, bounded to ``(1, 2]``.

    The ``R/X`` to insert is the ratio of the **fault loop**, not of the
    positive-sequence system alone. For a three-phase fault the two
    coincide; for the line-to-earth fault that dominates grounding studies
    the loop impedance is ``2*Z1 + Z0``, hence
    ``R/X = (2*R1 + R0) / (2*X1 + X0)``
    (see :func:`groundinsight.io.pandapower_sc.read_shortcircuit_results`).

    Parameters
    ----------
    r_to_x : float
        The ``R/X`` ratio of the fault loop. Must be non-negative.

    Returns
    -------
    float
        The peak factor ``kappa``.

    Raises
    ------
    ValueError
        If ``r_to_x`` is negative.

    Examples
    --------
    >>> round(kappa_from_r_to_x(0.0), 3)
    2.0
    >>> round(kappa_from_r_to_x(0.1), 4)
    1.746
    """
    if r_to_x < 0:
        raise ValueError(f"r_to_x must be non-negative, got {r_to_x!r}.")
    return 1.02 + 0.98 * math.exp(-3.0 * r_to_x)


def iec60909_m(kappa: float, f: float, t_k: float) -> float:
    """
    Heat-effect factor ``m`` of the aperiodic (DC) short-circuit component.

    ``m = (exp(4*f*Tk*ln(kappa-1)) - 1) / (2*f*Tk*ln(kappa-1))`` per
    IEC 60909-0. The limits are handled explicitly: ``kappa -> 1`` (no DC
    offset) gives ``m -> 0``; ``kappa -> 2`` (non-decaying DC, ``X/R -> inf``)
    gives ``m -> 2``.

    Notes
    -----
    The ``kappa -> 2`` limit is worth spelling out because it is easy to get
    backwards. Substituting ``a = ln(kappa - 1) -> 0`` and expanding,
    ``(exp(4 f Tk a) - 1) / (2 f Tk a) -> 4 f Tk a / (2 f Tk a) = 2``. A
    vanishing-resistance fault therefore carries the *maximum* DC heat, not
    none. pandapower's ``_calc_ith`` sets ``m = 0`` for ``kappa > 1.99``,
    which is why :func:`groundinsight.io.pandapower_sc.read_shortcircuit_results`
    recomputes ``I_th`` with this function instead of copying pandapower's
    ``ith_ka``.

    Parameters
    ----------
    kappa : float
        Peak factor ``kappa`` in ``(1, 2]`` (see :func:`kappa_from_r_to_x`).
    f : float
        System frequency in Hz (50 or 60).
    t_k : float
        Fault duration ``T_k`` in seconds. Must be strictly positive.

    Returns
    -------
    float
        The dimensionless factor ``m`` (``>= 0``).

    Raises
    ------
    ValueError
        If ``t_k`` or ``f`` is not strictly positive.

    Examples
    --------
    >>> iec60909_m(1.0, 50.0, 0.5)
    0.0
    >>> iec60909_m(2.0, 50.0, 0.5)
    2.0
    """
    if t_k <= 0:
        raise ValueError(f"t_k must be strictly positive, got {t_k!r}.")
    if f <= 0:
        raise ValueError(f"f must be strictly positive, got {f!r}.")
    if kappa <= 1.0:
        return 0.0
    if kappa >= 2.0:
        return 2.0
    a = math.log(kappa - 1.0)  # negative for 1 < kappa < 2
    denom = 2.0 * f * t_k * a
    if abs(denom) < 1e-12:  # kappa extremely close to 2 -> non-decaying DC
        return 2.0
    return (math.exp(4.0 * f * t_k * a) - 1.0) / denom


def peak_short_circuit_current(i_k: float, kappa: float) -> float:
    """
    Peak short-circuit current ``i_p = kappa * sqrt(2) * I_k''`` (IEC 60909-0).

    ``i_p`` drives the *electrodynamic* (mechanical) stress on conductors and
    supports; the thermal counterpart is
    :func:`thermal_equivalent_current`.

    Parameters
    ----------
    i_k : float
        RMS short-circuit current ``I_k''`` in amperes. Must be
        non-negative.
    kappa : float
        Peak factor from :func:`kappa_from_r_to_x`.

    Returns
    -------
    float
        The peak current in amperes.

    Raises
    ------
    ValueError
        If ``i_k`` is negative.

    Examples
    --------
    >>> round(peak_short_circuit_current(1000.0, 1.8), 2)
    2545.58
    """
    if i_k < 0:
        raise ValueError(f"i_k must be non-negative, got {i_k!r}.")
    return kappa * math.sqrt(2.0) * i_k


def thermal_equivalent_current(i_k: float, m: float, n: float = 1.0) -> float:
    """
    Thermally equivalent short-time current ``I_th = I_k'' * sqrt(m + n)``.

    Parameters
    ----------
    i_k : float
        RMS short-circuit current ``I_k''`` in amperes. Must be
        non-negative.
    m : float
        DC heat-effect factor from :func:`iec60909_m`. Must be
        non-negative.
    n : float
        AC-decay heat factor in ``(0, 1]``. ``1.0`` for the
        far-from-generator faults typical of distribution grounding
        studies.

    Returns
    -------
    float
        The thermally equivalent current in amperes.

    Raises
    ------
    ValueError
        If ``i_k`` or ``m`` is negative, or ``n`` is not in ``(0, 1]``.

    Notes
    -----
    The bounds are enforced rather than clipped because violating them
    fails *silently downwards*: a negative ``m``, or an ``n`` above one
    supplied in place of a correct smaller value, changes ``sqrt(m + n)``
    without any visible symptom, and a too-small result under-estimates the
    thermal stress -- the unsafe direction for a limit check. A caller that
    hands in such a value has a bug upstream and should hear about it.

    Examples
    --------
    >>> round(thermal_equivalent_current(1000.0, 0.0, 1.0), 6)
    1000.0
    """
    if i_k < 0:
        raise ValueError(f"i_k must be non-negative, got {i_k!r}.")
    if m < 0:
        raise ValueError(f"m must be non-negative, got {m!r}.")
    if not 0.0 < n <= 1.0:
        raise ValueError(f"n must lie in (0, 1], got {n!r}.")
    return i_k * math.sqrt(m + n)


# ---------------------------------------------------------------------------
# Fault-level resolution / aggregation
# ---------------------------------------------------------------------------


class FaultShortCircuitData(BaseModel):
    """
    Resolved IEC 60909 characteristics of one fault.

    The result of aggregating the per-source data
    (:attr:`Source.i_k_a <groundinsight.models.core_models.Source.i_k_a>`,
    ``r_to_x``, ``kappa``) with the fault-level data
    (:attr:`Fault.t_k_s <groundinsight.models.core_models.Fault.t_k_s>`,
    ``n_factor``) into the single set of numbers the non-linear 60909
    factors are evaluated with.

    Attributes
    ----------
    fault_name : str
        Name of the fault these characteristics belong to.
    frequency : float
        Frequency in Hz at which the source currents were weighted.
    kappa : float, optional
        Effective peak factor, or ``None`` when no participating source
        carries ``kappa`` / ``r_to_x``.
    r_to_x : float, optional
        Effective ``R/X``, reported only when it is unambiguous (all
        contributing sources share one value).
    t_k_s : float, optional
        Fault duration ``T_k`` in seconds, taken from the fault.
    n_factor : float
        AC-decay heat factor ``n`` taken from the fault.
    m : float, optional
        DC heat-effect factor at ``kappa``, ``frequency`` and ``t_k_s``.
        ``None`` when either ``kappa`` or ``t_k_s`` is unknown.
    i_k_a : float
        Arithmetic sum of the participating source injection magnitudes at
        ``frequency`` (in amperes), i.e. the weight base of the
        aggregation. This is *not* the branch current -- that comes from
        the solve.
    i_p_a : float, optional
        ``kappa * sqrt(2) * i_k_a``, reported for reference.
    aggregation : str
        ``"weighted"`` or ``"max"``.
    homogeneous : bool
        ``True`` when every contributing source shares the same ``kappa``
        (within 1e-9), so the aggregation is exact rather than an
        interpolation.
    sources : list of str
        Names of the sources that contributed a ``kappa``.
    sources_without_kappa : list of str
        Names of sources that inject current at ``frequency`` but carry no
        60909 data; they are part of the linear solve but excluded from
        the ``kappa`` aggregation.
    """

    fault_name: str
    frequency: float
    kappa: Optional[float] = None
    r_to_x: Optional[float] = None
    t_k_s: Optional[float] = None
    n_factor: float = 1.0
    m: Optional[float] = None
    i_k_a: float = 0.0
    i_p_a: Optional[float] = None
    aggregation: str = "weighted"
    homogeneous: bool = True
    sources: List[str] = Field(default_factory=list)
    sources_without_kappa: List[str] = Field(default_factory=list)

    def __str__(self) -> str:
        return (
            f"FaultShortCircuitData(fault={self.fault_name}, "
            f"kappa={self.kappa}, t_k_s={self.t_k_s}, m={self.m})"
        )


def _source_kappa(source) -> Optional[float]:
    """
    Return the peak factor of a source: explicit ``kappa`` wins over
    ``r_to_x``; ``None`` when the source carries neither.
    """
    kappa = getattr(source, "kappa", None)
    if kappa is not None:
        return float(kappa)
    r_to_x = getattr(source, "r_to_x", None)
    if r_to_x is not None:
        return kappa_from_r_to_x(float(r_to_x))
    return None


def _source_injection_magnitude(source, freq: float, scaling: float) -> float:
    """
    Magnitude of the source injection at ``freq``, scaled by the fault
    scaling -- the same quantity the solver injects, used here as the
    aggregation weight. Returns ``0.0`` when the source has no value at
    that frequency.
    """
    if source.source_type == "current":
        values = source.values or {}
        current = values.get(freq)
        if current is None:
            return 0.0
        return abs(scaling * complex(current.real, current.imag))

    voltage = source.voltage or {}
    impedance = source.source_impedance or {}
    u = voltage.get(freq)
    z = impedance.get(freq)
    if u is None or z is None:
        return 0.0
    z_c = complex(z.real, z.imag)
    if z_c == 0:
        return 0.0
    return abs(scaling * complex(u.real, u.imag) / z_c)


def _sources_feeding(network: Network, fault_name: str) -> List[str]:
    """
    Names of the sources that feed ``fault_name``.

    Uses the enumerated paths when they are available (matching what the
    solver actually superposes); otherwise falls back to every source
    sitting on an active bus.
    """
    from_paths = {
        path.source
        for path in network.paths.values()
        if path.fault == fault_name and path.source in network.sources
    }
    if from_paths:
        return [name for name in network.sources if name in from_paths]

    result = []
    for name, source in network.sources.items():
        bus = network.buses.get(source.bus)
        if bus is not None and not bus.active:
            continue
        result.append(name)
    return result


def resolve_fault_sc_characteristics(
    network: Network,
    fault_name: str,
    *,
    frequency: Optional[float] = None,
    aggregation: str = "weighted",
) -> FaultShortCircuitData:
    """
    Aggregate the IEC 60909 data of a fault and its sources into one set of
    characteristics.

    This is the executable form of the project's superposition rule (see the
    module docstring): the *linear* RMS currents stay superposed by the
    solve, and one effective ``kappa`` is resolved here so that the
    *non-linear* 60909 factors are applied exactly once, to the aggregate.

    Parameters
    ----------
    network : Network
        The network holding the fault and its sources.
    fault_name : str
        Name of the fault to resolve.
    frequency : float, optional
        Frequency in Hz at which the source injections are weighted.
        Defaults to the lowest positive frequency of ``network`` (the power
        frequency in a harmonic study), or ``50.0``.
    aggregation : {'weighted', 'max'}
        ``"weighted"`` (default) takes the current-weighted mean of the
        source ``kappa`` values, which reproduces the sum of the individual
        peak currents exactly. ``"max"`` takes the largest ``kappa``, a
        strictly conservative bound.

    Returns
    -------
    FaultShortCircuitData
        The resolved characteristics. ``kappa`` is ``None`` when no
        contributing source carries 60909 data -- callers must then fall
        back to an explicit argument.

    Raises
    ------
    ValueError
        If ``fault_name`` is unknown or ``aggregation`` is not one of the
        supported modes.

    Examples
    --------
    >>> import groundinsight as gi
    >>> net = gi.create_network(name="demo", frequencies=[50.0])
    >>> bt = gi.BusType(name="b", system_type="s", voltage_level=20.0,
    ...                 impedance_formula="rho*0 + 1.0")
    >>> _ = gi.create_bus(name="B1", type=bt, network=net)
    >>> _ = gi.create_source(name="S1", bus="B1", values={50.0: 1000.0},
    ...                      network=net, r_to_x=0.1)
    >>> _ = gi.create_fault(name="F1", bus="B1", scalings={50.0: 1.0},
    ...                     network=net, t_k_s=0.5)
    >>> data = gi.resolve_fault_sc_characteristics(net, "F1")
    >>> round(data.kappa, 4)
    1.746
    """
    if aggregation not in ("weighted", "max"):
        raise ValueError(
            f"aggregation must be 'weighted' or 'max', got {aggregation!r}."
        )
    fault = network.faults.get(fault_name)
    if fault is None:
        raise ValueError(
            f"Unknown fault {fault_name!r}. Known faults: "
            f"{sorted(network.faults)}."
        )

    if frequency is None:
        frequency = next((float(x) for x in network.frequencies if x > 0), 50.0)
    frequency = float(frequency)
    scaling = fault.scalings.get(frequency, 1)

    weights: Dict[str, float] = {}
    kappas: Dict[str, float] = {}
    r_values: List[float] = []
    without_kappa: List[str] = []
    i_k_total = 0.0

    for source_name in _sources_feeding(network, fault_name):
        source = network.sources[source_name]
        weight = _source_injection_magnitude(source, frequency, scaling)
        kappa_i = _source_kappa(source)
        if kappa_i is None:
            if weight > 0:
                without_kappa.append(source_name)
            i_k_total += weight
            continue
        # A source that carries 60909 data but injects nothing at this
        # frequency would silently vanish from a purely current-weighted
        # mean; give it a nominal weight so its kappa still counts.
        if weight <= 0:
            weight = float(getattr(source, "i_k_a", None) or 0.0)
        weights[source_name] = weight
        kappas[source_name] = kappa_i
        r_to_x_i = getattr(source, "r_to_x", None)
        if r_to_x_i is not None:
            r_values.append(float(r_to_x_i))
        i_k_total += weight

    n_factor = float(getattr(fault, "n_factor", 1.0) or 1.0)
    t_k_s = getattr(fault, "t_k_s", None)
    t_k_s = None if t_k_s is None else float(t_k_s)

    if not kappas:
        if without_kappa:
            logger.debug(
                "Fault %r: none of the feeding sources (%s) carries IEC 60909 "
                "data; kappa stays unresolved.",
                fault_name,
                ", ".join(without_kappa),
            )
        return FaultShortCircuitData(
            fault_name=fault_name,
            frequency=frequency,
            t_k_s=t_k_s,
            n_factor=n_factor,
            i_k_a=i_k_total,
            aggregation=aggregation,
            sources_without_kappa=without_kappa,
        )

    kappa_list = list(kappas.values())
    homogeneous = (max(kappa_list) - min(kappa_list)) <= 1e-9

    if aggregation == "max" or homogeneous:
        kappa_eff = max(kappa_list)
    else:
        weight_sum = sum(weights.values())
        if weight_sum > 0:
            kappa_eff = (
                sum(kappas[name] * weights[name] for name in kappas) / weight_sum
            )
        else:  # no current information at all -> plain mean
            kappa_eff = sum(kappa_list) / len(kappa_list)

    if not homogeneous:
        logger.warning(
            "Fault %r is fed by sources with different R/X (kappa between "
            "%.4f and %.4f). The non-linear IEC 60909 factors are applied "
            "once, with the %s kappa = %.4f. Use the transient solver for an "
            "exact mixed-R/X result.",
            fault_name,
            min(kappa_list),
            max(kappa_list),
            aggregation,
            kappa_eff,
        )
    if without_kappa:
        logger.warning(
            "Fault %r: source(s) %s inject current at %.1f Hz but carry no "
            "IEC 60909 data; they are excluded from the kappa aggregation.",
            fault_name,
            ", ".join(without_kappa),
            frequency,
        )

    r_to_x_eff = None
    if r_values and (max(r_values) - min(r_values)) <= 1e-12:
        r_to_x_eff = r_values[0]

    m = None
    if t_k_s is not None and t_k_s > 0:
        m = iec60909_m(kappa_eff, frequency, t_k_s)

    return FaultShortCircuitData(
        fault_name=fault_name,
        frequency=frequency,
        kappa=kappa_eff,
        r_to_x=r_to_x_eff,
        t_k_s=t_k_s,
        n_factor=n_factor,
        m=m,
        i_k_a=i_k_total,
        i_p_a=peak_short_circuit_current(i_k_total, kappa_eff),
        aggregation=aggregation,
        homogeneous=homogeneous,
        sources=sorted(kappas),
        sources_without_kappa=without_kappa,
    )

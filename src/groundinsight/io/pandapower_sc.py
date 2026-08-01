# io/pandapower_sc.py

"""
IEC 60909 short-circuit characteristics from a pandapower ``calc_sc`` run.

This module is the bridge between the *fault-current* world (pandapower's
symmetrical-component short-circuit calculation) and the *grounding* world
(``groundinsight``). It lets a user reuse an existing, validated network
model instead of re-entering ``I_k''``, ``R/X`` and the clearing time by
hand:

.. code-block:: python

    import pandapower.shortcircuit as sc
    import groundinsight as gi

    sc.calc_sc(net_pp, fault="1ph", case="max", ip=True, ith=True, tk_s=0.5)

    df = gi.read_shortcircuit_results(net_pp)                     # tidy frame
    rep = gi.apply_shortcircuit_characteristics(net_gi, net_pp, "F1")
    gi.check_conductor_limits(net_gi, "F1")                       # no more magic numbers

Why not simply copy ``res_bus_sc``?
-----------------------------------

Two findings from the verification of pandapower 3.5 motivate the extra
layer; both are pinned by ``tests/test_shortcircuit_60909.py``.

1. **The 1ph path has no peak/thermal current.** For ``fault="1ph"`` —
   precisely the fault type that matters for grounding — pandapower fills
   ``res_bus_sc.ip_ka`` and ``ith_ka`` with ``NaN``: its ``_calc_sc_1ph``
   adds ``kappa`` to the internal ppc but never calls ``_calc_ip`` /
   ``_calc_ith``. The quantities therefore have to be derived here, from
   the sequence impedances pandapower *does* report.

2. **The DC heat factor is inverted near ``kappa = 2``.** pandapower's
   ``_calc_ith`` zeroes ``m`` for ``kappa > 1.99``, whereas the analytic
   limit of

   .. math::

       m = \\frac{e^{4 f T_k \\ln(\\kappa-1)} - 1}{2 f T_k \\ln(\\kappa-1)}

   as ``kappa -> 2`` is ``m -> 2`` (a non-decaying DC component carries the
   *maximum* additional heat, not none). Copying ``ith_ka`` would therefore
   *under*-estimate the thermal stress of near-zero-resistance faults, which
   is the unsafe direction. ``I_th`` is recomputed here with
   :func:`~groundinsight.analysis.shortcircuit.iec60909_m`, which handles
   both limits explicitly.

What *is* taken from pandapower is the part it does better than a closed
form: the topology-aware peak factor. Where ``ip_ka`` is available (the 3ph
path with ``kappa_method="B"``/``"C"``) the effective ``kappa`` is recovered
as ``ip / (sqrt(2) * I_k'')`` and reported with
``kappa_origin = "pandapower"``. Otherwise the IEC closed form
``1.02 + 0.98 * exp(-3 * R/X)`` is used and flagged as
``kappa_origin = "iec_closed_form"``.

The single line-to-earth loop
-----------------------------

For ``fault="1ph"`` the driving loop is ``2*Z1 + Z0``, so the ratio that
governs the DC decay is

.. math::

    \\frac{R}{X} = \\frac{2 R_1 + R_0}{2 X_1 + X_0}

and **not** the positive-sequence ratio ``R_1/X_1``. That this is the loop
pandapower itself uses is verified numerically (``I_k1'' = sqrt(3)*c*U_n /
|2*Z1 + Z0|`` reproduces ``ikss_ka`` to machine precision); using
``R_1/X_1`` instead shifts ``kappa`` measurably.

Public API:

- :func:`read_shortcircuit_results`
- :func:`apply_shortcircuit_characteristics`

The optional ``pandapower`` dependency is imported lazily.

References
----------
IEC 60909-0:2016, sections 4.3 (``kappa``, ``i_p``) and 4.5 (``I_th``).
"""

from __future__ import annotations

import logging
import math
from typing import TYPE_CHECKING, Any, Dict, List, Optional, Sequence

import polars as pl

from ..analysis.shortcircuit import (
    _sources_feeding,
    iec60909_m,
    kappa_from_r_to_x,
    peak_short_circuit_current,
    thermal_equivalent_current,
)
from ..models.core_models import ComplexNumber, Network
from .pandapower_import import _bus_index_to_name, _require_pandapower


if TYPE_CHECKING:  # pragma: no cover - typing only
    import pandapower as pp  # noqa: F401


logger = logging.getLogger(__name__)


#: Fault types whose driving loop contains the zero-sequence impedance and
#: for which ``R/X`` is therefore taken from ``2*Z1 + Z0``.
_EARTH_FAULT_TYPES = ("1ph",)

#: ``tk_s`` default of ``pandapower.shortcircuit.calc_sc``. pandapower always
#: writes a ``tk_s`` into ``net._options`` -- even for a run that never asked
#: for a thermal-equivalent current -- so this particular value is a
#: placeholder, not a statement about the protection. See
#: :func:`_tk_s_from_options`.
_PP_TK_S_PLACEHOLDER = 1.0

#: Neutral IEC 60909-0 AC heat factor (the far-from-generator case) and the
#: default of ``read_shortcircuit_results(n_factor=...)``. ``n`` is not a
#: pandapower quantity at all, so this value carries no information and must
#: never displace a user-set one. See :func:`apply_shortcircuit_characteristics`.
_NEUTRAL_N_FACTOR = 1.0

#: Output schema of :func:`read_shortcircuit_results`, in output order.
#: Declared explicitly rather than inferred: polars only inspects the first
#: ``infer_schema_length=100`` dicts of a ``list[dict]``, so on a net with
#: more than 100 buses a column that is ``None`` for the first 100 rows and
#: numeric afterwards (``kappa`` where the zero-sequence data is incomplete,
#: ``vn_kv`` for buses missing from ``net.bus``) would abort the frame
#: construction with a ``ComputeError``. It also keeps a wholly absent column
#: (``r0_ohm`` / ``x0_ohm`` on a 3ph run) typed as ``Float64`` instead of
#: collapsing it to ``pl.Null``.
_SC_SCHEMA: Dict[str, Any] = {
    "pp_bus_index": pl.Int64,
    "bus_name": pl.Utf8,
    "vn_kv": pl.Float64,
    "fault_type": pl.Utf8,
    "case": pl.Utf8,
    "i_k_a": pl.Float64,
    "r1_ohm": pl.Float64,
    "x1_ohm": pl.Float64,
    "r0_ohm": pl.Float64,
    "x0_ohm": pl.Float64,
    "r_to_x": pl.Float64,
    "kappa": pl.Float64,
    "kappa_origin": pl.Utf8,
    "t_k_s": pl.Float64,
    "n_factor": pl.Float64,
    "m": pl.Float64,
    "i_p_a": pl.Float64,
    "i_th_a": pl.Float64,
}

#: Column layout of :func:`read_shortcircuit_results`, in order.
_SC_COLUMNS = tuple(_SC_SCHEMA)

#: Output schema of the audit trail returned by
#: :func:`apply_shortcircuit_characteristics`, in output order. Same
#: reasoning as ``_SC_SCHEMA``; ``r_to_x``, ``kappa``, ``t_k_s`` and
#: ``n_factor`` are routinely ``None`` for a whole frame.
_REPORT_SCHEMA: Dict[str, Any] = {
    "fault_name": pl.Utf8,
    "source_name": pl.Utf8,
    "bus": pl.Utf8,
    "frequency": pl.Float64,
    "i_k_total_a": pl.Float64,
    "share": pl.Float64,
    "i_k_previous_a": pl.Float64,
    "i_k_a": pl.Float64,
    "r_to_x": pl.Float64,
    "kappa": pl.Float64,
    "kappa_origin": pl.Utf8,
    "t_k_s": pl.Float64,
    "n_factor": pl.Float64,
    "values_updated": pl.Boolean,
}


def _finite(value: Any) -> Optional[float]:
    """Return ``value`` as ``float`` if it is finite, else ``None``.

    pandapower reports missing results as ``NaN`` rather than ``None``;
    converting them to ``None`` here keeps the Polars frame free of silent
    ``NaN`` propagation.
    """
    if value is None:
        return None
    try:
        as_float = float(value)
    except (TypeError, ValueError):
        return None
    return as_float if math.isfinite(as_float) else None


def _tk_s_from_options(options: Dict[str, Any]) -> Optional[float]:
    """
    Return the clearing time of a ``calc_sc`` run, or ``None`` if the run
    never asked for one.

    ``calc_sc`` has the signature default ``tk_s=1.0`` and copies it into
    ``net._options`` unconditionally, so a solved net *always* reports a
    ``tk_s`` — even one computed with neither ``ith=True`` nor an explicit
    duration. Treating that placeholder as the protection's clearing time
    is not a rounding issue: ``I_adm = k*S/sqrt(t_k)`` (IEC 60949), so
    reading 1.0 s where the project uses 3.0 s inflates the admissible
    current by ``sqrt(3) ~ 1.73`` and makes an undersized conductor look
    adequate — the non-conservative direction.

    The run is therefore accepted as thermal only if

    * ``options["ith"]`` is true — the caller explicitly requested a
      thermal-equivalent current, so ``tk_s`` is the duration it was
      computed for; or
    * ``tk_s`` differs from ``_PP_TK_S_PLACEHOLDER`` — the caller must
      have passed it, because pandapower would not have produced it.

    An explicit ``tk_s=1.0`` without ``ith=True`` is indistinguishable
    from the placeholder and is conservatively treated as absent; pass
    ``t_k_s=1.0`` to :func:`read_shortcircuit_results` to state it.

    Parameters
    ----------
    options : Dict[str, Any]
        A copy of ``net._options`` as written by ``calc_sc``.

    Returns
    -------
    float or None
        The clearing time in seconds, or ``None`` if the run carries none.
    """
    tk_s = _finite(options.get("tk_s"))
    if tk_s is None:
        return None
    if bool(options.get("ith", False)):
        return tk_s
    if tk_s != _PP_TK_S_PLACEHOLDER:
        return tk_s
    return None


def _adopt_fault_value(
    fault,
    attribute: str,
    new_value: Optional[float],
    fault_name: str,
) -> None:
    """
    Write a protection-side quantity onto a :class:`Fault`, audibly.

    Nothing is written for ``new_value=None`` — that is how the callers
    express "the short-circuit run says nothing about this", and the value
    already on the fault (typically the project's real clearing time)
    survives. When a value *is* replaced by a different one, the change is
    announced with a ``logger.warning`` naming both, so that a protection
    setting can never be swapped out silently.

    Parameters
    ----------
    fault : Fault
        The fault to update, in place.
    attribute : str
        Name of the attribute to set, ``"t_k_s"`` or ``"n_factor"``.
    new_value : float, optional
        The value to adopt, or ``None`` to keep the current one.
    fault_name : str
        Name of the fault, for the log message.

    Returns
    -------
    None
    """
    if new_value is None:
        return
    new_value = float(new_value)
    old_value = getattr(fault, attribute, None)
    if old_value is not None and not math.isclose(
        float(old_value), new_value, rel_tol=1e-12, abs_tol=0.0
    ):
        logger.warning(
            "Fault '%s': %s replaced by the short-circuit result, %r -> %r. "
            "Pass %s=... explicitly if the previous value was the intended one.",
            fault_name, attribute, float(old_value), new_value, attribute,
        )
    setattr(fault, attribute, new_value)


def _loop_r_to_x(
    r1: Optional[float],
    x1: Optional[float],
    r0: Optional[float],
    x0: Optional[float],
    earth_fault: bool,
) -> Optional[float]:
    """
    ``R/X`` of the short-circuit loop that drives the DC decay.

    Parameters
    ----------
    r1, x1 : float, optional
        Positive-sequence resistance / reactance at the bus, in ohm.
    r0, x0 : float, optional
        Zero-sequence resistance / reactance at the bus, in ohm. Only
        needed for earth faults.
    earth_fault : bool
        ``True`` for a single line-to-earth fault, where the loop is
        ``2*Z1 + Z0``; ``False`` for a three-phase fault, where it is
        ``Z1``.

    Returns
    -------
    float or None
        The ratio ``R/X``, or ``None`` if the required impedances are
        missing or the reactance is zero.
    """
    if r1 is None or x1 is None:
        return None
    if earth_fault:
        if r0 is None or x0 is None:
            return None
        r_loop = 2.0 * r1 + r0
        x_loop = 2.0 * x1 + x0
    else:
        r_loop, x_loop = r1, x1
    if x_loop == 0:
        return None
    ratio = r_loop / x_loop
    return ratio if math.isfinite(ratio) and ratio >= 0 else None


def _kappa_for_row(
    i_k_a: Optional[float],
    ip_ka: Optional[float],
    r_to_x: Optional[float],
    bus_label: str,
) -> tuple:
    """
    Resolve ``(kappa, origin)`` for one bus row.

    pandapower's ``ip_ka`` embeds the topology-aware peak factor (method B
    or C), which is more accurate than the closed form whenever the fault
    is fed through several branches. It is therefore preferred, and only
    the missing case falls back to ``1.02 + 0.98 * exp(-3 * R/X)``.
    """
    if ip_ka is not None and i_k_a not in (None, 0.0):
        kappa = (ip_ka * 1000.0) / (math.sqrt(2.0) * i_k_a)
        if 1.0 < kappa <= 2.0 + 1e-9:
            return min(kappa, 2.0), "pandapower"
        logger.warning(
            "pandapower ip/ikss at bus '%s' implies kappa=%.4f outside (1, 2]; "
            "falling back to the IEC closed form.",
            bus_label, kappa,
        )
    if r_to_x is None:
        return None, "unavailable"
    return kappa_from_r_to_x(r_to_x), "iec_closed_form"


def read_shortcircuit_results(
    net,
    *,
    t_k_s: Optional[float] = None,
    n_factor: float = 1.0,
    f: float = 50.0,
    buses: Optional[Sequence[int]] = None,
) -> pl.DataFrame:
    """
    Read a solved pandapower short-circuit case as IEC 60909 quantities.

    Converts ``net.res_bus_sc`` into a tidy, unit-explicit
    :class:`polars.DataFrame` in which the quantities missing from
    pandapower's single line-to-earth path (``kappa``, ``i_p``, ``I_th``)
    are filled in, and ``I_th`` is recomputed with a DC heat factor that is
    correct in the ``kappa -> 2`` limit. See the module docstring for the
    reasoning.

    The fault type, case and clearing time are taken from ``net._options``
    (written by ``calc_sc``) so that a solved net is self-describing; the
    ``t_k_s`` and ``n_factor`` arguments override them. The clearing time
    is adopted only when the ``calc_sc`` run genuinely carried one --
    pandapower stores its signature default ``tk_s=1.0`` even for a run
    that asked for no thermal current at all, and passing that placeholder
    off as a protection setting is unsafe; see :func:`_tk_s_from_options`.

    Parameters
    ----------
    net : pandapower.auxiliary.pandapowerNet
        A pandapower network on which ``pandapower.shortcircuit.calc_sc``
        has already been run.
    t_k_s : float, optional
        Short-circuit duration ``T_k`` in seconds. Defaults to the
        ``tk_s`` of the ``calc_sc`` run, but only if that run requested a
        thermal-equivalent current (``ith=True``) or passed a ``tk_s``
        other than pandapower's default of 1.0 s. Without it ``m`` and
        ``I_th`` cannot be computed and are reported as ``None``.
    n_factor : float, default 1.0
        IEC 60909-0 AC heat factor ``n``. ``1.0`` is the
        far-from-generator case. pandapower does not model ``n``, so this
        argument is the only source of a non-neutral value.
    f : float, default 50.0
        System frequency in Hz used in the ``m`` factor.
    buses : sequence of int, optional
        Restrict the output to these pandapower bus indices. Defaults to
        every bus in ``res_bus_sc``.

    Returns
    -------
    polars.DataFrame
        One row per bus with the columns ``pp_bus_index``, ``bus_name``,
        ``vn_kv``, ``fault_type``, ``case``, ``i_k_a`` (in **amperes**,
        not kA), ``r1_ohm``, ``x1_ohm``, ``r0_ohm``, ``x0_ohm``,
        ``r_to_x``, ``kappa``, ``kappa_origin``, ``t_k_s``, ``n_factor``,
        ``m``, ``i_p_a`` and ``i_th_a``.

    Raises
    ------
    ImportError
        If pandapower is not installed.
    ValueError
        If ``net`` carries no short-circuit results, or if ``buses``
        references indices absent from them.

    Notes
    -----
    ``kappa_origin`` documents where each peak factor came from:
    ``"pandapower"`` (recovered from ``ip_ka``, topology-aware),
    ``"iec_closed_form"`` (``1.02 + 0.98*exp(-3*R/X)``) or
    ``"unavailable"``. Keep it in reports — it is the difference between a
    reproducible study and a magic number.

    Examples
    --------
    >>> import pandapower.shortcircuit as sc  # doctest: +SKIP
    >>> sc.calc_sc(net, fault="1ph", case="max", tk_s=0.5)  # doctest: +SKIP
    >>> gi.read_shortcircuit_results(net).select("bus_name", "i_k_a", "kappa")  # doctest: +SKIP
    """
    _require_pandapower()

    res = getattr(net, "res_bus_sc", None)
    if res is None or len(res) == 0:
        raise ValueError(
            "The pandapower net carries no short-circuit results. Run "
            "pandapower.shortcircuit.calc_sc(net, fault='1ph', case='max', "
            "ip=True, ith=True, tk_s=...) before calling "
            "read_shortcircuit_results()."
        )

    options: Dict[str, Any] = dict(getattr(net, "_options", None) or {})
    fault_type = str(options.get("fault", "unknown"))
    case = str(options.get("case", "unknown"))
    if t_k_s is None:
        t_k_s = _tk_s_from_options(options)
    if t_k_s is not None:
        t_k_s = float(t_k_s)
        if t_k_s <= 0:
            raise ValueError(f"t_k_s must be strictly positive, got {t_k_s!r}.")
    if not (0.0 < float(n_factor) <= 1.0):
        raise ValueError(f"n_factor must lie in (0, 1], got {n_factor!r}.")
    n_factor = float(n_factor)

    # ``fault="1ph"`` is the earth-fault case; anything reporting a
    # zero-sequence impedance is treated the same way.
    earth_fault = fault_type in _EARTH_FAULT_TYPES or "rk0_ohm" in res.columns

    index_to_name = _bus_index_to_name(net)

    if buses is not None:
        wanted = [int(b) for b in buses]
        missing = [b for b in wanted if b not in res.index]
        if missing:
            raise ValueError(
                f"Bus indices {missing} have no short-circuit results. "
                f"Available: {sorted(int(i) for i in res.index)}."
            )
        selection = wanted
    else:
        selection = [int(i) for i in res.index]

    rows: List[Dict[str, Any]] = []
    for idx in selection:
        row = res.loc[idx]
        bus_name = index_to_name.get(idx, f"bus_{idx}")

        ikss_ka = _finite(row.get("ikss_ka"))
        i_k_a = None if ikss_ka is None else ikss_ka * 1000.0
        r1 = _finite(row.get("rk_ohm"))
        x1 = _finite(row.get("xk_ohm"))
        r0 = _finite(row.get("rk0_ohm"))
        x0 = _finite(row.get("xk0_ohm"))

        r_to_x = _loop_r_to_x(r1, x1, r0, x0, earth_fault)
        kappa, kappa_origin = _kappa_for_row(
            i_k_a, _finite(row.get("ip_ka")), r_to_x, bus_name
        )

        m = None
        i_p_a = None
        i_th_a = None
        if kappa is not None:
            if i_k_a is not None:
                i_p_a = peak_short_circuit_current(i_k_a, kappa)
            if t_k_s is not None:
                m = iec60909_m(kappa, f, t_k_s)
                if i_k_a is not None:
                    i_th_a = thermal_equivalent_current(i_k_a, m, n_factor)

        rows.append(
            {
                "pp_bus_index": int(idx),
                "bus_name": bus_name,
                "vn_kv": _finite(net.bus.at[idx, "vn_kv"]) if idx in net.bus.index else None,
                "fault_type": fault_type,
                "case": case,
                "i_k_a": i_k_a,
                "r1_ohm": r1,
                "x1_ohm": x1,
                "r0_ohm": r0,
                "x0_ohm": x0,
                "r_to_x": r_to_x,
                "kappa": kappa,
                "kappa_origin": kappa_origin,
                "t_k_s": t_k_s,
                "n_factor": n_factor,
                "m": m,
                "i_p_a": i_p_a,
                "i_th_a": i_th_a,
            }
        )

    unavailable = [r["bus_name"] for r in rows if r["kappa_origin"] == "unavailable"]
    if unavailable:
        logger.warning(
            "No kappa could be derived for %d bus(es) (%s): neither ip_ka nor a "
            "usable R/X is available. For fault='1ph' make sure the zero-sequence "
            "data (x0x_max, r0x0_max, vk0_percent, r0_ohm_per_km, ...) is complete.",
            len(unavailable), unavailable,
        )

    return pl.DataFrame(rows, schema=_SC_SCHEMA)


def apply_shortcircuit_characteristics(
    network: Network,
    sc_results,
    fault_name: str,
    *,
    pp_bus: Optional[int] = None,
    bus_name: Optional[str] = None,
    sources: Optional[Sequence[str]] = None,
    set_source_values: bool = False,
    frequency: Optional[float] = None,
    t_k_s: Optional[float] = None,
    n_factor: Optional[float] = None,
) -> pl.DataFrame:
    """
    Write pandapower short-circuit characteristics onto a groundinsight model.

    Takes the IEC 60909 quantities computed at the **fault bus** and
    distributes them over the sources that feed that fault: every source
    receives the loop's ``r_to_x`` and ``kappa``, and a share of ``I_k''``
    proportional to its present injection. The clearing time ``T_k`` and
    the AC heat factor ``n`` are written onto the :class:`Fault`, where
    they belong — they describe the protection, not the infeed.

    With ``set_source_values=True`` the source injections themselves are
    overwritten with those shares, turning a pandapower earth-fault result
    directly into the excitation of the grounding model. This is off by
    default so an existing, hand-tuned excitation is never silently
    replaced.

    Parameters
    ----------
    network : Network
        The groundinsight network to update, in place.
    sc_results : pandapower.auxiliary.pandapowerNet or polars.DataFrame
        Either a solved pandapower net (passed through
        :func:`read_shortcircuit_results`) or a frame previously returned
        by it.
    fault_name : str
        Name of the :class:`Fault` in ``network`` the results belong to.
    pp_bus : int, optional
        pandapower bus index to read the characteristics from. Defaults to
        the row whose ``bus_name`` equals the fault's bus, which is the
        natural match for a network built by
        :func:`~groundinsight.io.from_pandapower`.
    bus_name : str, optional
        Alternative to ``pp_bus``: select the row by name. Mutually
        exclusive with ``pp_bus``.
    sources : sequence of str, optional
        Restrict the update to these sources. Defaults to every source
        with a path to the fault, or — if no paths are built — every
        active source in the network.
    set_source_values : bool, default False
        If ``True``, also overwrite ``Source.values`` at ``frequency``
        with each source's share of ``I_k''``.
    frequency : float, optional
        Frequency at which the shares are computed and, if requested,
        written. Defaults to the lowest positive network frequency.
    t_k_s : float, optional
        Overrides the clearing time from ``sc_results``, and is the way to
        set one when the short-circuit run carries none.
    n_factor : float, optional
        Overrides the AC heat factor from ``sc_results``, and is the way
        to set one at all — ``n`` is not a pandapower quantity.

    Returns
    -------
    polars.DataFrame
        An audit trail with one row per updated source: ``fault_name``,
        ``source_name``, ``bus``, ``frequency``, ``i_k_total_a``,
        ``share``, ``i_k_previous_a``, ``i_k_a``, ``r_to_x``, ``kappa``,
        ``kappa_origin``, ``t_k_s``, ``n_factor`` and ``values_updated``.
        ``i_k_previous_a`` is the magnitude the source injected *before*
        the call, so a review can see exactly what changed. ``t_k_s`` and
        ``n_factor`` report the values *in effect on the fault after* the
        call, which for an untouched quantity is the one that was already
        there.

    Raises
    ------
    ValueError
        If the fault does not exist, if both ``pp_bus`` and ``bus_name``
        are given, if no matching row is found in ``sc_results``, or if
        no source is eligible for the update.

    Notes
    -----
    Only the *linear* quantities are distributed. ``i_p`` and ``I_th`` are
    deliberately **not** written per source: they are non-linear in the
    current and must be evaluated on the aggregate, which is what
    :func:`~groundinsight.analysis.shortcircuit.resolve_fault_sc_characteristics`
    does.

    ``Fault.t_k_s`` and ``Fault.n_factor`` describe the *protection*, and a
    short-circuit run only occasionally knows anything about it. They are
    therefore updated conservatively: a clearing time is written only if
    the ``calc_sc`` run genuinely carried one (see
    :func:`_tk_s_from_options`) or ``t_k_s=...`` is passed here, and an AC
    heat factor only if it differs from the neutral 1.0 or ``n_factor=...``
    is passed here. Anything else leaves the value on the fault untouched
    instead of resetting it to a pandapower placeholder — a shortened
    ``T_k`` raises the admissible current by ``1/sqrt(t_k)`` and would make
    an undersized conductor pass. Whenever a value *is* replaced by a
    different one, a ``logger.warning`` names both.

    Examples
    --------
    >>> rep = gi.apply_shortcircuit_characteristics(net_gi, net_pp, "F1")  # doctest: +SKIP
    >>> rep.select("source_name", "i_k_previous_a", "i_k_a", "kappa")  # doctest: +SKIP
    """
    if pp_bus is not None and bus_name is not None:
        raise ValueError("Provide at most one of 'pp_bus' or 'bus_name'.")

    fault = network.faults.get(fault_name)
    if fault is None:
        raise ValueError(
            f"Fault {fault_name!r} is not in network {network.name!r}. "
            f"Available: {sorted(network.faults)}."
        )

    frame = (
        sc_results
        if isinstance(sc_results, pl.DataFrame)
        else read_shortcircuit_results(sc_results)
    )
    if frame.height == 0:
        raise ValueError("The short-circuit result frame is empty.")

    # --- pick the row describing the fault location --------------------
    target = bus_name if bus_name is not None else fault.bus
    if pp_bus is not None:
        matches = frame.filter(pl.col("pp_bus_index") == int(pp_bus))
        selector = f"pp_bus={pp_bus}"
    else:
        matches = frame.filter(pl.col("bus_name") == target)
        selector = f"bus_name={target!r}"
    if matches.height == 0:
        raise ValueError(
            f"No short-circuit result for {selector}. Available buses: "
            f"{frame['bus_name'].to_list()}. Pass pp_bus=... or bus_name=... "
            "if the groundinsight bus names differ from the pandapower ones."
        )
    if matches.height > 1:
        logger.warning(
            "%d short-circuit rows match %s; using the first one.",
            matches.height, selector,
        )
    row = matches.to_dicts()[0]

    i_k_total = row.get("i_k_a")
    if i_k_total is None:
        raise ValueError(
            f"The short-circuit result for {selector} has no I_k''; nothing to apply."
        )
    kappa = row.get("kappa")
    r_to_x = row.get("r_to_x")
    kappa_origin = row.get("kappa_origin")

    # --- the protection-side quantities belong to the fault ------------
    # An explicit argument always wins. Otherwise the frame is only
    # believed where it can actually have learnt something: a ``t_k_s``
    # that survived _tk_s_from_options, and an ``n_factor`` that is not the
    # neutral default (pandapower does not model ``n``, so a 1.0 in the
    # frame is the argument default of read_shortcircuit_results and says
    # nothing about this fault).
    if t_k_s is not None:
        t_k = float(t_k_s)
    else:
        t_k = row.get("t_k_s")
    if n_factor is not None:
        n_val = float(n_factor)
    else:
        frame_n = row.get("n_factor")
        n_val = (
            frame_n
            if frame_n is not None and frame_n != _NEUTRAL_N_FACTOR
            else None
        )
    _adopt_fault_value(fault, "t_k_s", t_k, fault_name)
    _adopt_fault_value(fault, "n_factor", n_val, fault_name)
    # Report what is in effect afterwards, not what was offered.
    effective_t_k = fault.t_k_s
    effective_n = fault.n_factor

    if frequency is None:
        frequency = next((float(x) for x in network.frequencies if x > 0), 50.0)
    frequency = float(frequency)

    # --- pick the sources to update ------------------------------------
    if sources is not None:
        names = list(sources)
        unknown = [s for s in names if s not in network.sources]
        if unknown:
            raise ValueError(
                f"Unknown source(s) {unknown} in network {network.name!r}."
            )
    else:
        names = _sources_feeding(network, fault_name)
    if not names:
        raise ValueError(
            f"No sources are eligible for fault {fault_name!r}. Build the paths "
            "(run_fault / build_paths) or pass sources=[...] explicitly."
        )

    # --- distribute I_k'' proportionally to the present injection ------
    previous: Dict[str, float] = {}
    for name in names:
        src = network.sources[name]
        values = src.values or {}
        entry = values.get(frequency)
        if entry is None:
            previous[name] = 0.0
        else:
            previous[name] = abs(complex(entry.real, entry.imag))

    total_previous = sum(previous.values())
    if total_previous > 0:
        shares = {name: previous[name] / total_previous for name in names}
    else:
        shares = {name: 1.0 / len(names) for name in names}
        if len(names) > 1:
            logger.warning(
                "Sources %s inject nothing at f=%.4g Hz; splitting I_k'' equally. "
                "Set the relative infeed shares first if the split is not uniform.",
                names, frequency,
            )

    rows: List[Dict[str, Any]] = []
    for name in names:
        src = network.sources[name]
        share = shares[name]
        i_k_share = i_k_total * share

        src.i_k_a = i_k_share if i_k_share > 0 else None
        src.r_to_x = r_to_x
        src.kappa = kappa

        updated = False
        if set_source_values and src.source_type == "current":
            values = dict(src.values or {})
            values[frequency] = ComplexNumber(real=i_k_share, imag=0.0)
            src.values = values
            updated = True
        elif set_source_values:
            logger.warning(
                "Source '%s' is a voltage source; its injection was not "
                "overwritten (set_source_values only applies to current sources).",
                name,
            )

        rows.append(
            {
                "fault_name": fault_name,
                "source_name": name,
                "bus": src.bus,
                "frequency": frequency,
                "i_k_total_a": i_k_total,
                "share": share,
                "i_k_previous_a": previous[name],
                "i_k_a": i_k_share,
                "r_to_x": r_to_x,
                "kappa": kappa,
                "kappa_origin": kappa_origin,
                "t_k_s": effective_t_k,
                "n_factor": effective_n,
                "values_updated": updated,
            }
        )

    logger.info(
        "Applied IEC 60909 characteristics of bus '%s' to fault '%s': "
        "I_k''=%.1f A, kappa=%s (%s), T_k=%s s over %d source(s).",
        row.get("bus_name"), fault_name, i_k_total,
        "n/a" if kappa is None else f"{kappa:.4f}", kappa_origin,
        effective_t_k, len(names),
    )

    return pl.DataFrame(rows, schema=_REPORT_SCHEMA)

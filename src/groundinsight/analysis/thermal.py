# analysis/thermal.py

"""
Thermal short-circuit rating of grounding conductors (IEC 60949 / IEC 60909-0).

This module adds a conductor-integrity check on top of the stationary
grounding solve. It answers the safety-engineering question *"does the
shield / grounding conductor survive the fault current thermally?"* — the
counterpart to the person-safety touch-voltage assessment.

Two standards are combined:

* **IEC 60909-0** — the fault current the conductor actually has to carry
  thermally is the *thermally equivalent short-time current*

  .. math::

      I_{th} = I_k \\cdot \\sqrt{m + n}

  where :math:`I_k` is the RMS short-circuit current (here the RMS shield
  current from :func:`groundinsight.run_fault`), ``m`` accounts for the
  heat effect of the aperiodic (DC) component and ``n`` for the decay of
  the AC component. ``m`` follows the closed form

  .. math::

      m = \\frac{1}{2 f T_k \\ln(\\kappa - 1)}
          \\left(e^{4 f T_k \\ln(\\kappa - 1)} - 1\\right)

  with the peak factor :math:`\\kappa` (from the network ``R/X`` ratio),
  the system frequency ``f`` and the fault duration ``T_k``. ``n = 1`` for
  the far-from-generator faults typical of distribution grounding studies.

* **IEC 60949 / IEC 60364-5-54** — the admissible adiabatic short-circuit
  current of a conductor of cross-section ``S`` (mm²) over the duration
  ``T_k`` (s) is

  .. math::

      I_{adm} = \\frac{k \\, S}{\\sqrt{T_k}}, \\qquad
      k = K \\, \\sqrt{\\ln\\!\\frac{\\theta_f + \\beta}{\\theta_i + \\beta}}

  with the material base constant :math:`K = \\sqrt{Q_c(\\beta+20)/\\rho_{20}}`,
  the reciprocal temperature coefficient ``β``, and the initial / final
  conductor temperatures ``θ_i`` / ``θ_f``. The tabulated ``K`` and ``β``
  below reproduce the standard ``k`` tables (e.g. copper XLPE
  ``k = 226·√(ln((250+234.5)/(90+234.5))) = 143``).

The superposition rule follows the project decision: the *linear* AC RMS
shield currents are superposed by the frequency-domain solve as usual, and
the *non-linear* IEC 60909 peak/thermal factors are applied to that
aggregate — ``I_th`` and ``i_p`` are never superposed directly.

The peak factor ``kappa`` itself can either be passed explicitly or be
resolved from the IEC 60909 quantities stored on the network's sources and
faults by
:func:`groundinsight.analysis.shortcircuit.resolve_fault_sc_characteristics`,
which implements the current-weighted aggregation across several feeding
sources. The 60909 primitives ``kappa_from_r_to_x`` and ``iec60909_m`` live
in that module and are re-exported here for backwards compatibility.

References
----------
IEC 60909-0:2016, IEC 60949:1988, IEC 60364-5-54, EN 50522.
"""

import logging
import math
from typing import Any, Dict, List, NamedTuple, Optional, Sequence, Tuple

import polars as pl

from ..models.core_models import Network
from .shortcircuit import (
    iec60909_m,
    kappa_from_r_to_x,
    peak_short_circuit_current,
    resolve_fault_sc_characteristics,
)


logger = logging.getLogger(__name__)


# IEC 60949 / IEC 60364-5-54 material data for the adiabatic ``k`` factor.
# ``K`` is the base material constant sqrt(Qc*(beta+20)/rho20) in A*s^0.5/mm^2,
# ``beta`` is the reciprocal temperature coefficient in K, and
# ``theta_final_default_C`` is a conservative bare-earthing-conductor final
# temperature. Override ``theta_final_C`` per BranchType / BusType for any
# other covering; :data:`FINAL_TEMPERATURES` collects the tabulated values.
#
# ``theta_final_default_C`` is 300 C for every material, which is the value
# EN 50522 Table 2 gives for a bare or galvanised earthing conductor or earth
# electrode. Earlier releases defaulted ``Steel`` to 400 C; that was *higher*,
# i.e. more permissive -- the unsafe direction for a limit check -- than both
# EN 50522 and the National Grid Earthing Technical Specification (Table 5a:
# steel 300 C), and was lowered to 300 C. Studies that relied on the old
# default get a smaller ``k`` and therefore a smaller admissible current; pass
# ``theta_final_C=400.0`` explicitly to reproduce them.
IEC60949_MATERIALS: Dict[str, Dict[str, float]] = {
    "Cu": {"K": 226.0, "beta": 234.5, "theta_final_default_C": 300.0},
    "Al": {"K": 148.0, "beta": 228.0, "theta_final_default_C": 300.0},
    "Steel": {"K": 78.0, "beta": 202.0, "theta_final_default_C": 300.0},
}


#: Maximum permissible conductor temperature ``theta_f`` in C for an
#: *insulated* conductor -- a core of an insulated cable, or an earthing
#: conductor run in one -- from IEC 60364-5-54 Table 54.2 / IEC 60949, the
#: caps reproduced by the worked example in :func:`iec60949_k`. Here it is the
#: insulation that fails first, not the metal and not a surface coating, so
#: these caps are markedly lower than the bare-conductor values, and they do
#: not depend on the conductor material.
#:
#: :data:`FINAL_TEMPERATURES` splices this mapping in for every material
#: instead of repeating the numbers, so an insulation cap cannot drift between
#: the two names.
CABLE_INSULATION_LIMITS: Dict[str, float] = {
    "PVC": 160.0,   # IEC 60364-5-54 Table 54.2, S <= 300 mm2
    "XLPE": 250.0,  # IEC 60364-5-54 Table 54.2
    "EPR": 250.0,   # IEC 60364-5-54 Table 54.2
}


# Maximum permissible final temperature ``theta_f`` in C for an *earthing
# conductor or earth electrode*, per conductor material and covering. Such a
# conductor is limited not by the metal alone but by whatever surrounds it,
# and the two regimes have different sources and very different numbers:
#
# * **Uninsulated** -- bare, tinned or galvanised. EN 50522 Table 2 applies
#   and gives 300 C throughout, with one exception: a *tinned* copper
#   conductor is capped at 150 C. That exception is physically the obvious
#   one -- tin melts at 231.9 C, so 150 C keeps a clear margin below the
#   solder joint failing, whereas the zinc of a galvanised steel conductor
#   melts only at 419.5 C and is therefore not the binding constraint at
#   300 C.
# * **Insulated** -- PVC, XLPE, EPR. The insulation degrades long before any
#   of that, so the much lower caps of :data:`CABLE_INSULATION_LIMITS` apply
#   and are spliced in below. They are the same numbers for every material,
#   because it is the insulation that sets them.
#
# Only combinations that occur in practice are tabulated. Aluminium is neither
# tinned nor galvanised and copper is not galvanised; ``PE`` is deliberately
# absent, because neither source states a value for it that this package can
# cite. Asking for any of these raises a ``ValueError`` from
# :func:`final_temperature` rather than returning an invented number, because
# a too-high ``theta_f`` raises ``k`` and therefore permits *more* current --
# the unsafe direction.
#
# Source
# ------
# EN 50522 Table 2 for the uninsulated rows, as printed in the edition held by
# the author of this package; IEC 60364-5-54 Table 54.2 for the insulated ones
# (see :data:`CABLE_INSULATION_LIMITS`). Earlier releases of groundinsight
# carried placeholder values taken from the National Grid Earthing Technical
# Specification, Table 5a (bare Cu 405 C, bare Al 325 C, bare steel 300 C);
# those have been replaced.
FINAL_TEMPERATURES: Dict[str, Dict[str, float]] = {
    "Cu": {
        "bare": 300.0,              # EN 50522 Table 2
        "tinned": 150.0,            # EN 50522 Table 2 -- tin melts at 231.9 C
        **CABLE_INSULATION_LIMITS,  # IEC 60364-5-54 Table 54.2
    },
    "Al": {
        "bare": 300.0,              # EN 50522 Table 2
        **CABLE_INSULATION_LIMITS,  # IEC 60364-5-54 Table 54.2
    },
    "Steel": {
        "bare": 300.0,              # EN 50522 Table 2
        "galvanized": 300.0,        # EN 50522 Table 2 -- zinc melts at 419.5 C
        **CABLE_INSULATION_LIMITS,  # IEC 60364-5-54 Table 54.2
    },
}


def final_temperature(material: str, covering: str) -> float:
    """
    Look up a tabulated maximum final temperature ``theta_f``.

    Convenience accessor for :data:`FINAL_TEMPERATURES`, meant to be used when
    *building* a ``BranchType`` or ``BusType`` so that the source of the
    number stays visible in the model::

        gi.BusType(..., electrode_theta_final_C=gi.final_temperature("Steel", "bare"))

    Which standard the answer comes from depends on the covering: EN 50522
    Table 2 for an uninsulated conductor, IEC 60364-5-54 Table 54.2 for an
    insulated one. The insulated caps are much lower, so the two must not be
    confused — hence one accessor over one catalogue rather than a per-source
    lookup the caller has to choose between.

    Parameters
    ----------
    material : str
        One of the keys of :data:`IEC60949_MATERIALS` (``"Cu"``, ``"Al"``,
        ``"Steel"``).
    covering : str
        Covering / surface treatment key. Uninsulated: ``"bare"``,
        ``"tinned"`` (copper only) or ``"galvanized"`` (steel only).
        Insulated: ``"PVC"``, ``"XLPE"`` or ``"EPR"``. Only combinations that
        occur in practice are tabulated — there is no tinned aluminium and no
        galvanised copper — and ``"PE"`` is not tabulated at all.

    Returns
    -------
    float
        Maximum permissible final temperature in °C.

    Raises
    ------
    ValueError
        If the material or the material / covering combination is not
        tabulated. The message lists what *is* available — the table is
        intentionally incomplete rather than populated with unsourced
        values, since an over-estimated ``theta_f`` permits too much
        current.

    See Also
    --------
    CABLE_INSULATION_LIMITS : the insulated subset, on its own, when the
        conductor material is not known or not relevant.

    Examples
    --------
    >>> final_temperature("Cu", "bare")
    300.0
    >>> final_temperature("Cu", "tinned")
    150.0
    >>> final_temperature("Cu", "PVC")
    160.0
    """
    if material not in FINAL_TEMPERATURES:
        raise ValueError(
            f"No tabulated final temperatures for material {material!r}. "
            f"Known materials: {sorted(FINAL_TEMPERATURES)}."
        )
    coverings = FINAL_TEMPERATURES[material]
    if covering not in coverings:
        raise ValueError(
            f"No tabulated final temperature for covering {covering!r} on "
            f"{material!r}. Tabulated coverings: {sorted(coverings)}. Add the "
            "value from EN 50522 Table 2 (uninsulated) or IEC 60364-5-54 "
            "Table 54.2 (insulated) to "
            "groundinsight.analysis.thermal.FINAL_TEMPERATURES, or pass "
            "theta_final_C explicitly."
        )
    return float(coverings[covering])


class _SCInputs(NamedTuple):
    """Resolved IEC 60909-0 inputs shared by the branch and node checks."""

    t_k: float
    kappa: float
    m: float
    n: float
    f: float
    i_th_factor: float


#: Columns common to the branch and the node check, in output order. Declaring
#: the schema explicitly keeps the dtypes stable when a whole column is
#: ``None`` (e.g. no conductor declared anywhere) and, above all, keeps an
#: empty frame *selectable* instead of degenerating into a schema-less
#: ``pl.DataFrame([])`` on which ``pl.col(...)`` raises.
_COMMON_SCHEMA: Dict[str, Any] = {
    "i_p_A": pl.Float64,
    "kappa": pl.Float64,
    "m": pl.Float64,
    "n": pl.Float64,
    "t_k_s": pl.Float64,
    "I_th_factor": pl.Float64,
    "I_th_A": pl.Float64,
    "material": pl.Utf8,
    "cross_section_mm2": pl.Float64,
    "k": pl.Float64,
    "I_admissible_A": pl.Float64,
    "utilization": pl.Float64,
    "within_limit": pl.Boolean,
}

#: Output schema of :func:`check_conductor_limits`.
_BRANCH_SCHEMA: Dict[str, Any] = {
    "branch_name": pl.Utf8,
    "I_s_rms_A": pl.Float64,
    **_COMMON_SCHEMA,
}

#: Output schema of :func:`check_node_limits`.
_NODE_SCHEMA: Dict[str, Any] = {
    "bus_name": pl.Utf8,
    "element": pl.Utf8,
    "I_rms_A": pl.Float64,
    "current_split": pl.Float64,
    "I_conductor_A": pl.Float64,
    **_COMMON_SCHEMA,
}


def _warn_half_declared(
    incomplete: Sequence[Tuple[str, str]], fault_name: str, kind: str
) -> None:
    """
    Warn about elements that carry *one* half of their thermal data.

    Declaring nothing is a modelling choice and stays silent -- see the
    optionality contract in :func:`check_conductor_limits`. Declaring a
    material *without* a cross-section (or the reverse) is different: the
    user has clearly started to describe the conductor, so a row reported
    with ``within_limit = None`` reads like "passed" rather than "never
    assessed". This is the one case where the omission is announced.

    Parameters
    ----------
    incomplete : sequence of (str, str)
        Pairs of ``(element_label, missing_field)``.
    fault_name : str
        Name of the fault being checked, for the message.
    kind : str
        Already-pluralised noun for the message, e.g. ``"branch(es)"``.
    """
    if not incomplete:
        return
    detail = ", ".join(f"{label} (missing {field})" for label, field in incomplete)
    logger.warning(
        "%d %s carry only half of their thermal data and were NOT assessed "
        "for fault '%s': %s. Declare both the material and the cross-section, "
        "or neither -- rows with 'within_limit = None' are unassessed, not "
        "passed.",
        len(incomplete), kind, fault_name, detail,
    )


def _require_complete_results(
    fault_name: str, expected: Sequence[str], reported: Sequence[str], kind: str
) -> None:
    """
    Fail loudly when the stored result does not cover the whole network.

    A limit check that silently reports *nothing* is worse than one that
    raises: the natural safety gates (iterating the rows, or testing
    ``frame.is_empty()``) then read an incomplete result as "no violations",
    and the ``logger.warning`` channel of the check goes silent as well.

    This happens in practice: ``ElectricalNetwork.solve_network()`` resets
    ``network.results[fault]`` and only :meth:`~groundinsight.electrical_network.
    ElectricalNetwork.compute_branch_currents` refills the branch list, so
    solving a hand-built :class:`ElectricalNetwork` for inspection leaves the
    stored result without any branches. Adding a branch or activating a bus
    after :func:`groundinsight.run_fault` leaves the same gap.

    Parameters
    ----------
    fault_name : str
        Name of the fault whose result is being checked, for the message.
    expected : Sequence[str]
        Names that a complete result must contain.
    reported : Sequence[str]
        Names actually present in the stored result.
    kind : str
        ``"branch"`` or ``"bus"``, used in the message only.

    Raises
    ------
    ValueError
        If any expected name is missing from the stored result.
    """
    missing = [name for name in expected if name not in set(reported)]
    if not missing:
        return
    shown = missing[:5]
    suffix = "" if len(missing) == len(shown) else f", ... (+{len(missing) - len(shown)})"
    raise ValueError(
        f"Incomplete results for fault {fault_name!r}: {len(missing)} of "
        f"{len(expected)} {kind}es are missing from the stored result "
        f"({shown}{suffix}). The check would silently report no violations "
        f"for them. Call run_fault(network, {fault_name!r}) to rebuild the "
        f"results; note that calling ElectricalNetwork.solve_network() on its "
        f"own resets them and must be followed by compute_branch_currents()."
    )


def _resolve_sc_inputs(
    network: Network,
    fault_name: str,
    t_k: Optional[float],
    kappa: Optional[float],
    r_to_x: Optional[float],
    n: Optional[float],
    f: Optional[float],
    aggregation: str,
) -> _SCInputs:
    """
    Resolve ``t_k``, ``kappa``, ``m``, ``n``, ``f`` and ``sqrt(m + n)``.

    Shared by :func:`check_conductor_limits` and :func:`check_node_limits`
    so both derive the IEC 60909-0 excitation identically: an explicit
    argument always wins, otherwise the value stored on the ``Fault`` /
    the feeding ``Source`` objects is used. See the two public functions
    for the parameter semantics.

    Raises
    ------
    ValueError
        If ``fault_name`` has no results, if both ``kappa`` and ``r_to_x``
        are given, if ``t_k`` is neither given nor stored and positive, or
        if ``kappa`` can be resolved from neither arguments nor sources.
    """
    if kappa is not None and r_to_x is not None:
        raise ValueError(
            "Provide at most one of 'kappa' or 'r_to_x' to characterise the "
            "DC content of the fault current."
        )
    if fault_name not in network.results:
        raise ValueError(
            f"No results for fault {fault_name!r}. Call run_fault(...) first."
        )

    fault = network.faults.get(fault_name)

    # --- fault duration T_k -------------------------------------------
    if t_k is None:
        t_k = getattr(fault, "t_k_s", None)
        if t_k is None:
            raise ValueError(
                f"No fault duration available for fault {fault_name!r}. Pass "
                "t_k=... explicitly or set Fault.t_k_s on the fault."
            )
    t_k = float(t_k)
    if t_k <= 0:
        raise ValueError(f"t_k must be strictly positive, got {t_k!r}.")

    # --- AC heat factor n ----------------------------------------------
    if n is None:
        n = float(getattr(fault, "n_factor", 1.0) or 1.0)
    n = float(n)

    if f is None:
        f = next((float(x) for x in network.frequencies if x > 0), 50.0)

    # --- peak factor kappa ---------------------------------------------
    if kappa is None and r_to_x is not None:
        kappa = kappa_from_r_to_x(float(r_to_x))
    if kappa is None:
        # Fall back to the IEC 60909 data stored on the feeding sources.
        sc_data = resolve_fault_sc_characteristics(
            network, fault_name, frequency=f, aggregation=aggregation
        )
        kappa = sc_data.kappa
        if kappa is None:
            raise ValueError(
                "Provide 'kappa' or 'r_to_x' to characterise the DC content of "
                f"the fault current, or store 'kappa'/'r_to_x' on the sources "
                f"feeding fault {fault_name!r} (e.g. via "
                "gi.apply_shortcircuit_characteristics from a pandapower run)."
            )
    kappa = float(kappa)

    m = iec60909_m(kappa, f, t_k)
    return _SCInputs(
        t_k=t_k,
        kappa=kappa,
        m=float(m),
        n=n,
        f=float(f),
        i_th_factor=math.sqrt(m + n),
    )


def iec60949_k(
    material: str,
    theta_initial_C: float = 20.0,
    theta_final_C: Optional[float] = None,
) -> float:
    """
    Material constant ``k`` of the adiabatic short-circuit equation.

    ``k = K * sqrt(ln((theta_f + beta) / (theta_i + beta)))`` per IEC 60949 /
    IEC 60364-5-54, with the base constant ``K`` and ``beta`` taken from
    :data:`IEC60949_MATERIALS`.

    Parameters
    ----------
    material : str
        One of the keys of :data:`IEC60949_MATERIALS` (``"Cu"``, ``"Al"``,
        ``"Steel"``).
    theta_initial_C : float
        Initial conductor temperature ``theta_i`` in °C. Defaults to
        ``20.0`` (ambient). For a conductor that also carries load current
        use its maximum continuous operating temperature.
    theta_final_C : float, optional
        Final (maximum permissible) conductor temperature ``theta_f`` in °C.
        Defaults to the material's bare-earthing-conductor value from
        :data:`IEC60949_MATERIALS`.

    Returns
    -------
    float
        The material constant ``k`` in A·s^0.5/mm².

    Raises
    ------
    ValueError
        If ``material`` is unknown or ``theta_final_C <= theta_initial_C``.

    Examples
    --------
    >>> round(iec60949_k("Cu", theta_initial_C=90.0, theta_final_C=250.0))
    143
    """
    if material not in IEC60949_MATERIALS:
        raise ValueError(
            f"Unknown conductor material {material!r}. Known materials: "
            f"{sorted(IEC60949_MATERIALS)}."
        )
    data = IEC60949_MATERIALS[material]
    theta_f = (
        data["theta_final_default_C"] if theta_final_C is None else float(theta_final_C)
    )
    beta = data["beta"]
    if theta_f <= theta_initial_C:
        raise ValueError(
            f"theta_final_C ({theta_f}) must exceed theta_initial_C "
            f"({theta_initial_C}) for material {material!r}."
        )
    return data["K"] * math.sqrt(
        math.log((theta_f + beta) / (theta_initial_C + beta))
    )


def admissible_short_circuit_current(
    k: float, cross_section_mm2: float, t_k: float
) -> float:
    """
    Adiabatic admissible short-circuit current ``I_adm = k*S/sqrt(t_k)``.

    Parameters
    ----------
    k : float
        Material constant from :func:`iec60949_k`, in A·s^0.5/mm².
    cross_section_mm2 : float
        Conductor cross-section ``S`` in mm². Must be strictly positive.
    t_k : float
        Fault duration in seconds. Must be strictly positive.

    Returns
    -------
    float
        The admissible short-circuit current in amperes.

    Raises
    ------
    ValueError
        If ``cross_section_mm2`` or ``t_k`` is not strictly positive.
    """
    if cross_section_mm2 <= 0:
        raise ValueError(
            f"cross_section_mm2 must be strictly positive, got {cross_section_mm2!r}."
        )
    if t_k <= 0:
        raise ValueError(f"t_k must be strictly positive, got {t_k!r}.")
    return k * cross_section_mm2 / math.sqrt(t_k)


def check_conductor_limits(
    network: Network,
    fault_name: str,
    t_k: Optional[float] = None,
    *,
    kappa: Optional[float] = None,
    r_to_x: Optional[float] = None,
    n: Optional[float] = None,
    f: Optional[float] = None,
    aggregation: str = "weighted",
) -> pl.DataFrame:
    """
    Check every grounding branch against its adiabatic thermal limit.

    For the given fault the RMS shield current of every branch (from the
    most recent :func:`groundinsight.run_fault`) is converted into the
    thermally equivalent short-time current ``I_th = I_s_rms * sqrt(m + n)``
    (IEC 60909-0) and compared against the admissible adiabatic current
    ``I_adm = k * S / sqrt(t_k)`` (IEC 60949) of the branch conductor. A
    branch is checked only if its :class:`BranchType` carries both
    ``conductor_material`` and ``cross_section_mm2``; the others are
    reported with ``within_limit = None``.

    .. note::
       The thermal data is **opt-in**. A network that declares none of it
       still solves and is still reported here — every branch appears with
       its currents (``I_s_rms_A``, ``i_p_A``, ``I_th_A``) and only the
       judgement columns are ``None``. Declaring *half* of it
       (a material without a cross-section or the reverse) is the one case
       that is announced with a ``logging.WARNING``, because such a row is
       indistinguishable from a passing one at a glance.

    All three IEC 60909 inputs — ``t_k``, ``kappa`` and ``n`` — can either
    be passed explicitly or be left to the network data:

    * ``t_k`` falls back to ``Fault.t_k_s``,
    * ``n`` falls back to ``Fault.n_factor`` (default ``1.0``),
    * ``kappa`` falls back to the current-weighted aggregation over the
      feeding sources' ``kappa`` / ``r_to_x``, see
      :func:`~groundinsight.analysis.shortcircuit.resolve_fault_sc_characteristics`.

    An explicit argument always wins over the stored value, so a study can
    override a single scenario without editing the network.

    Parameters
    ----------
    network : Network
        A network that already has results for ``fault_name`` (call
        :func:`run_fault` first).
    fault_name : str
        Name of the solved fault whose branch currents are checked.
    t_k : float, optional
        Fault duration ``T_k`` in seconds. Must be strictly positive.
        Defaults to ``Fault.t_k_s`` of the named fault.
    kappa : float, optional
        Peak factor ``kappa`` in ``(1, 2]``. Mutually exclusive with
        ``r_to_x``. If neither is given, ``kappa`` is resolved from the
        sources' IEC 60909 data.
    r_to_x : float, optional
        ``R/X`` ratio at the fault, converted to ``kappa`` internally.
        Mutually exclusive with ``kappa``.
    n : float, optional
        AC-decay heat factor ``n`` (IEC 60909-0). Defaults to
        ``Fault.n_factor``, i.e. ``1.0`` (far-from-generator;
        ``I_k'' = I_k``).
    f : float, optional
        System frequency in Hz used in the ``m`` factor. Defaults to the
        lowest positive frequency of ``network`` (usually 50), or ``50.0``.
    aggregation : {'weighted', 'max'}, default 'weighted'
        Only relevant when ``kappa`` is resolved from the sources.
        ``"weighted"`` reproduces the sum of the individual source peaks
        exactly; ``"max"`` takes the largest source ``kappa`` and is
        conservative. Ignored when ``kappa`` or ``r_to_x`` is given.

    Returns
    -------
    polars.DataFrame
        One row per branch with the columns ``branch_name``,
        ``I_s_rms_A``, ``i_p_A``, ``kappa``, ``m``, ``n``, ``t_k_s``,
        ``I_th_factor``, ``I_th_A``, ``material``, ``cross_section_mm2``,
        ``k``, ``I_admissible_A``, ``utilization`` (``I_th / I_adm``) and
        ``within_limit`` (``bool`` or ``None`` when the branch has no
        thermal parameters). ``i_p_A = kappa * sqrt(2) * I_s_rms`` is the
        peak current of that branch and is the input for a later
        electrodynamic-force check.

    Raises
    ------
    ValueError
        If ``t_k`` is neither given nor stored and positive, if both
        ``kappa`` and ``r_to_x`` are given, if ``kappa`` can be resolved
        from neither the arguments nor the sources, or if ``network`` has
        no results for ``fault_name``. Also if the stored result does not
        cover every branch of the network -- an incomplete result would
        silently report the missing branches as free of violations. Re-run
        :func:`groundinsight.run_fault` in that case.

    Examples
    --------
    >>> gi.check_conductor_limits(net, "F1", t_k=0.5, r_to_x=0.1)  # doctest: +SKIP
    >>> gi.check_conductor_limits(net, "F1")  # all inputs from the model  # doctest: +SKIP
    """
    sc = _resolve_sc_inputs(
        network, fault_name, t_k, kappa, r_to_x, n, f, aggregation
    )
    t_k, kappa, m, n, i_th_factor = (
        sc.t_k, sc.kappa, sc.m, sc.n, sc.i_th_factor
    )

    result = network.results[fault_name]
    # ``compute_branch_currents`` emits a row for *every* branch, including
    # inactive and open ones, so any missing name means the stored result is
    # stale or half-built. See _require_complete_results.
    _require_complete_results(
        fault_name,
        list(network.branches),
        [rb.name for rb in result.branches],
        "branch",
    )
    rows: List[Dict[str, Any]] = []
    incomplete: List[Tuple[str, str]] = []
    for result_branch in result.branches:
        name = result_branch.name
        i_s_rms = float(result_branch.i_s)
        i_th = i_s_rms * i_th_factor

        branch = network.branches.get(name)
        material = None
        cross_section = None
        k_val = None
        i_adm = None
        utilization = None
        within = None
        if branch is not None:
            btype = branch.type
            material = getattr(btype, "conductor_material", None)
            cross_section = getattr(btype, "cross_section_mm2", None)
            if material is not None and cross_section is not None:
                k_val = iec60949_k(
                    material,
                    theta_initial_C=getattr(btype, "theta_initial_C", 20.0),
                    theta_final_C=getattr(btype, "theta_final_C", None),
                )
                i_adm = admissible_short_circuit_current(k_val, cross_section, t_k)
                utilization = i_th / i_adm if i_adm > 0 else None
                within = bool(i_th <= i_adm)
            elif material is not None:
                incomplete.append((name, "cross_section_mm2"))
            elif cross_section is not None:
                incomplete.append((name, "conductor_material"))

        rows.append(
            {
                "branch_name": name,
                "I_s_rms_A": i_s_rms,
                "i_p_A": peak_short_circuit_current(i_s_rms, kappa),
                "kappa": float(kappa),
                "m": float(m),
                "n": float(n),
                "t_k_s": float(t_k),
                "I_th_factor": float(i_th_factor),
                "I_th_A": float(i_th),
                "material": material,
                "cross_section_mm2": cross_section,
                "k": k_val,
                "I_admissible_A": i_adm,
                "utilization": utilization,
                "within_limit": within,
            }
        )

    _warn_half_declared(incomplete, fault_name, "branch(es)")

    violations = [r["branch_name"] for r in rows if r["within_limit"] is False]
    if violations:
        logger.warning(
            "Thermal limit exceeded on %d branch(es) for fault '%s' "
            "(t_k=%.3g s, kappa=%.3f): %s",
            len(violations), fault_name, t_k, kappa, violations,
        )

    return pl.DataFrame(rows, schema=_BRANCH_SCHEMA)


# Per-element field prefixes on ``BusType``. The two elements are assessed
# against *different* currents -- see :func:`check_node_limits`.
_NODE_ELEMENTS: Dict[str, Dict[str, str]] = {
    "earthing_conductor": {
        "prefix": "earthing_conductor_",
        "current_attr": "i_inj",
    },
    "electrode": {
        "prefix": "electrode_",
        "current_attr": "ia",
    },
}


def check_node_limits(
    network: Network,
    fault_name: str,
    t_k: Optional[float] = None,
    *,
    kappa: Optional[float] = None,
    r_to_x: Optional[float] = None,
    n: Optional[float] = None,
    f: Optional[float] = None,
    aggregation: str = "weighted",
    elements: Sequence[str] = ("earthing_conductor", "electrode"),
) -> pl.DataFrame:
    """
    Check every bus against the adiabatic thermal limits of its grounding.

    The node-side counterpart of :func:`check_conductor_limits`. Where the
    branch check assesses the shield / earth wire *between* buses, this one
    assesses the two elements *at* a bus, which EN 50522 / IEC 61936-1 keep
    strictly apart because they carry different currents:

    ``earthing_conductor``
        The **earthing conductor** (*Erdungsleiter*): the lumped connection
        that brings the earth-fault current from the installation into the
        grounding system. It carries the full injected current
        ``ResultBus.i_inj`` — at a source bus the source infeed, at the
        fault bus the total fault current — and is therefore the more
        heavily stressed of the two.

    ``electrode``
        The **earth electrode** (*Erder*): the buried part, which only
        carries the share actually dissipated into the soil at this bus,
        ``ResultBus.ia = u_EPR / Z_B``. In a well-meshed system that is a
        small fraction of the injection, so sizing the electrode for the
        full fault current is wasteful — and sizing the earthing conductor
        for the electrode current is dangerous.

    Both are reported separately, one row each, in a long-format frame.

    An element is assessed only if its :class:`BusType` carries both the
    ``*_material`` and the ``*_cross_section_mm2`` field of that prefix;
    otherwise the row is reported with ``within_limit = None``.

    .. note::
       The thermal data is **opt-in**. A network that declares none of it
       still solves and is still reported here — every active bus appears
       with its currents and only the judgement columns are ``None``.
       Declaring *half* of it (a material without a cross-section or the
       reverse) is the one case that is announced with a
       ``logging.WARNING``, because such a row is indistinguishable from a
       passing one at a glance.

    Current split
    -------------
    Each element carries a free factor ``current_split`` in ``(0, 1]``
    (``BusType.earthing_conductor_current_split`` /
    ``BusType.electrode_current_split``), applied as
    ``I_conductor = I_rms * current_split``. It expresses how the bus
    current divides between physically parallel paths that the nodal model
    lumps into one:

    * ``1.0`` (default) — one conductor / one electrode carries everything.
    * ``1/N`` — ``N`` equal parallel paths, e.g. ``0.25`` for four
      down-conductors from a substation steelwork to the grid.
    * ``0.5`` — a ring electrode fed at a single point: the current splits
      into the two halves of the ring.
    * any IEEE Std 80 style split / division factor, entered directly.

    The factor is deliberately *not* derived automatically: the split
    depends on the physical arrangement inside the substation, which the
    nodal grounding model does not resolve. Values above ``1`` are rejected
    at the model level — more current than the bus carries is an error, not
    a split.

    Superposition
    -------------
    Identical to the branch check: the frequency-domain solve superposes
    the *linear* AC RMS currents, and the *non-linear* IEC 60909 factors
    ``kappa`` / ``m`` are applied once to the aggregate. ``i_p`` and
    ``I_th`` are never superposed directly.

    Parameters
    ----------
    network : Network
        A network that already has results for ``fault_name`` (call
        :func:`run_fault` first).
    fault_name : str
        Name of the solved fault whose bus currents are checked.
    t_k : float, optional
        Fault duration ``T_k`` in seconds. Defaults to ``Fault.t_k_s``.
    kappa : float, optional
        Peak factor ``kappa`` in ``(1, 2]``. Mutually exclusive with
        ``r_to_x``. Resolved from the sources when neither is given.
    r_to_x : float, optional
        ``R/X`` ratio at the fault, converted to ``kappa`` internally.
        Mutually exclusive with ``kappa``.
    n : float, optional
        AC-decay heat factor ``n`` (IEC 60909-0). Defaults to
        ``Fault.n_factor``, i.e. ``1.0``.
    f : float, optional
        System frequency in Hz used in the ``m`` factor. Defaults to the
        lowest positive frequency of ``network``, or ``50.0``.
    aggregation : {'weighted', 'max'}, default 'weighted'
        Only relevant when ``kappa`` is resolved from the sources; see
        :func:`check_conductor_limits`.
    elements : sequence of str, default ``('earthing_conductor', 'electrode')``
        Which of the two elements to report. Pass a single-element
        sequence to restrict the frame.

    Returns
    -------
    polars.DataFrame
        Long format, one row per bus and requested element, with the
        columns ``bus_name``, ``element``, ``I_rms_A`` (the bus current
        before the split), ``current_split``, ``I_conductor_A``,
        ``i_p_A``, ``kappa``, ``m``, ``n``, ``t_k_s``, ``I_th_factor``,
        ``I_th_A``, ``material``, ``cross_section_mm2``, ``k``,
        ``I_admissible_A``, ``utilization`` and ``within_limit``.
        ``within_limit`` is ``None`` where the ``BusType`` declares no
        material / cross-section for that element — the current columns
        are still filled, so an undimensioned bus shows what it would have
        to carry.

    Raises
    ------
    ValueError
        For an unknown entry in ``elements``, and for the same conditions
        as :func:`check_conductor_limits` (no results, missing ``t_k``,
        unresolvable ``kappa``, both ``kappa`` and ``r_to_x`` given, or a
        stored result that does not cover every *active* bus).

    Notes
    -----
    A bus that is *both* the fault bus and the only source bus gets
    ``i_inj = 0``: the current never enters the grounding system, it
    circulates in the phase conductor. That is physically correct and not
    a missing value.

    Results computed before ``ResultBus.i_inj`` existed default to ``0``
    for the injection. Re-run :func:`run_fault` if an earthing-conductor
    row reads ``I_rms_A = 0`` at a bus that should be carrying current.

    Examples
    --------
    >>> gi.run_fault(net, "F1")                       # doctest: +SKIP
    >>> gi.check_node_limits(net, "F1").filter(       # doctest: +SKIP
    ...     pl.col("element") == "electrode"
    ... )
    """
    unknown = [e for e in elements if e not in _NODE_ELEMENTS]
    if unknown:
        raise ValueError(
            f"Unknown node element(s) {unknown}. Known elements: "
            f"{sorted(_NODE_ELEMENTS)}."
        )

    sc = _resolve_sc_inputs(
        network, fault_name, t_k, kappa, r_to_x, n, f, aggregation
    )

    result = network.results[fault_name]
    # ``solve_network`` emits a row for every *active* bus (inactive ones are
    # removed from the nodal system and carry no EPR), so the expectation is
    # the set of active buses. See _require_complete_results.
    _require_complete_results(
        fault_name,
        [name for name, bus in network.buses.items() if bus.active],
        [rb.name for rb in result.buses],
        "bus",
    )
    rows: List[Dict[str, Any]] = []
    incomplete: List[Tuple[str, str]] = []
    for result_bus in result.buses:
        name = result_bus.name
        bus = network.buses.get(name)
        btype = getattr(bus, "type", None)

        for element in elements:
            spec = _NODE_ELEMENTS[element]
            prefix = spec["prefix"]

            i_rms = float(getattr(result_bus, spec["current_attr"], 0.0) or 0.0)

            split = 1.0
            material = None
            cross_section = None
            k_val = None
            i_adm = None
            utilization = None
            within = None
            if btype is not None:
                split = float(
                    getattr(btype, f"{prefix}current_split", 1.0) or 1.0
                )
                material = getattr(btype, f"{prefix}material", None)
                cross_section = getattr(btype, f"{prefix}cross_section_mm2", None)

            i_conductor = i_rms * split
            i_th = i_conductor * sc.i_th_factor

            if material is not None and cross_section is not None:
                k_val = iec60949_k(
                    material,
                    theta_initial_C=getattr(btype, f"{prefix}theta_initial_C", 20.0),
                    theta_final_C=getattr(btype, f"{prefix}theta_final_C", None),
                )
                i_adm = admissible_short_circuit_current(
                    k_val, cross_section, sc.t_k
                )
                utilization = i_th / i_adm if i_adm > 0 else None
                within = bool(i_th <= i_adm)
            elif material is not None:
                incomplete.append((f"{name}/{element}", f"{prefix}cross_section_mm2"))
            elif cross_section is not None:
                incomplete.append((f"{name}/{element}", f"{prefix}material"))

            rows.append(
                {
                    "bus_name": name,
                    "element": element,
                    "I_rms_A": i_rms,
                    "current_split": split,
                    "I_conductor_A": i_conductor,
                    "i_p_A": peak_short_circuit_current(i_conductor, sc.kappa),
                    "kappa": sc.kappa,
                    "m": sc.m,
                    "n": sc.n,
                    "t_k_s": sc.t_k,
                    "I_th_factor": sc.i_th_factor,
                    "I_th_A": float(i_th),
                    "material": material,
                    "cross_section_mm2": cross_section,
                    "k": k_val,
                    "I_admissible_A": i_adm,
                    "utilization": utilization,
                    "within_limit": within,
                }
            )

    _warn_half_declared(incomplete, fault_name, "node element(s)")

    violations = [
        f"{r['bus_name']}/{r['element']}" for r in rows if r["within_limit"] is False
    ]
    if violations:
        logger.warning(
            "Thermal limit exceeded on %d node element(s) for fault '%s' "
            "(t_k=%.3g s, kappa=%.3f): %s",
            len(violations), fault_name, sc.t_k, sc.kappa, violations,
        )

    return pl.DataFrame(rows, schema=_NODE_SCHEMA)

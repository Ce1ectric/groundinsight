# analysis/_bisection.py

"""
Shared input validation and outcome reporting for the log-bisection searches.

Both :mod:`~groundinsight.analysis.inverse_rho` and
:mod:`~groundinsight.analysis.inverse_rho_f` run the same search: a
log-scale bisection of a scalar ``c`` against an EPR limit, bounded by a
relative tolerance ``tol_rel`` and a hard step cap ``max_iter``. They
therefore share the same failure modes, and this module holds the checks
and the report builder in one place so the two cannot drift apart.

Why the validation is stricter than it looks it needs to be
-----------------------------------------------------------

Every check here exists because the unguarded value produced a
*result-shaped* return value rather than an error -- the worst outcome
for a limit calculation, because nothing downstream can tell it apart
from an answer:

``limit`` (``u_max`` / ``u_limit``) as NaN
    ``nan <= 0`` is ``False``, so a plain positivity guard lets NaN
    through. Afterwards every comparison against the limit is also
    ``False``: the search neither raises at the lower bound nor accepts
    the upper one, walks the bracket down to ``c_min`` and reports it.

``tol_rel <= 0``
    The exit test ``(c_hi - c_lo) / c_lo <= tol_rel`` can never be
    satisfied, so the search burns every one of the ``max_iter`` steps --
    each one a full ``run_fault`` -- and still returns a bracket it never
    closed.

``tol_rel`` as NaN
    The mirror image: ``width > nan`` is ``False`` on the first pass, so
    the loop body never executes and the *lower* bracket bound is
    returned after zero iterations.

``max_iter < 1``
    Same endpoint by a different road -- the loop never runs and
    ``c_min`` comes back labelled as a result.

non-integral ``max_iter``
    ``max_iter=2.7`` silently means three steps. A cap that does not
    mean what it says is worse than no cap.

non-finite ``c_bounds``
    ``0 < c_lo < c_hi`` accepts ``c_hi = inf``. The infinity reaches the
    solver as an infinite grounding impedance and surfaces several layers
    down as *"no active bus is referenced to earth"* -- a topology
    diagnosis for what is really an argument error, pointing the caller
    at their network instead of at their call.
"""

import math
from typing import Any, Dict, Tuple


#: The bisection closed the bracket to within ``tol_rel``. ``c_max`` is the
#: largest *verified admissible* scaling factor and the true threshold lies
#: inside ``c_bracket``.
STATUS_CONVERGED = "converged"

#: ``epr(c_hi) <= limit``: every factor in the bracket satisfies the limit,
#: so the true maximum lies somewhere *above* ``c_hi`` and was never
#: determined. ``c_max`` is the upper bracket bound. Widen ``c_bounds``.
STATUS_BRACKET_FULLY_ADMISSIBLE = "bracket_fully_admissible"

#: The step cap was hit before the bracket closed. ``c_max`` is still a
#: verified admissible factor, but it can be far below the true threshold.
STATUS_MAX_ITER_REACHED = "max_iter_reached"

#: The supplied bracket was already narrower than ``tol_rel`` on entry, so
#: no step was taken. The threshold *is* bracketed and the tolerance *is*
#: met -- this is a converged result that happens to have run zero steps.
STATUS_BRACKET_WITHIN_TOL_ON_ENTRY = "bracket_within_tol_on_entry"

#: Statuses for which ``c_max`` is the answer to within ``tol_rel``.
#: ``STATUS_BRACKET_FULLY_ADMISSIBLE`` and ``STATUS_MAX_ITER_REACHED`` are
#: deliberately absent: in both the true threshold is outside the closed
#: interval the search actually proved anything about.
CONVERGED_STATUSES = frozenset(
    {STATUS_CONVERGED, STATUS_BRACKET_WITHIN_TOL_ON_ENTRY}
)


def validate_limit(value: float, name: str) -> None:
    """Reject a non-finite or non-positive EPR limit.

    Parameters
    ----------
    value : float
        The limit to check (``u_max`` or ``u_limit``), in volts.
    name : str
        Parameter name, echoed in the message so the caller sees which
        argument tripped the check.

    Raises
    ------
    ValueError
        If ``value`` is NaN, infinite or not strictly positive.
    """
    if not math.isfinite(value) or value <= 0:
        # The message stays call-site neutral on purpose: this validator
        # guards a bisection *and* a catalog scan, and NaN breaks them in
        # opposite directions (the search walks to c_min, the scan marks
        # every candidate inadmissible). What they share is the cause.
        raise ValueError(
            f"{name} must be a finite positive number, got {value!r}. "
            "NaN is the dangerous one: it passes a plain positivity check "
            "(nan <= 0 is False), and every later comparison against it is "
            "False as well -- so the limit test carries no information and "
            "the outcome is decided by whichever branch 'False' happens to "
            "select. Nothing downstream can tell such a result apart from "
            "an answer."
        )


def validate_tol_rel(tol_rel: float) -> None:
    """Reject a tolerance the bisection can never act on.

    Raises
    ------
    ValueError
        If ``tol_rel`` is NaN, infinite or not strictly positive.
    """
    if not math.isfinite(tol_rel) or tol_rel <= 0:
        raise ValueError(
            f"tol_rel must be a finite positive number, got {tol_rel!r}. "
            "The bisection stops on (c_hi - c_lo) / c_lo <= tol_rel. A "
            "non-positive tolerance can never be met, so the search would "
            "spend every max_iter step -- one run_fault each -- and still "
            "return a bracket it never closed. A NaN tolerance fails the "
            "other way: the comparison is False immediately, so no step is "
            "taken at all and the lower bracket bound comes back as the "
            "answer."
        )


def validate_max_iter(max_iter: int) -> None:
    """Reject a step cap that is not a usable positive integer.

    Raises
    ------
    ValueError
        If ``max_iter`` is a bool, not an ``int``, or below 1.
    """
    if isinstance(max_iter, bool) or not isinstance(max_iter, int):
        raise ValueError(
            f"max_iter must be an int, got {max_iter!r} of type "
            f"{type(max_iter).__name__}. A float cap is silently rounded up "
            "by the loop condition (max_iter=2.7 means three steps), and a "
            "cap that does not mean what it says is worse than no cap."
        )
    if max_iter < 1:
        raise ValueError(
            f"max_iter must be >= 1, got {max_iter!r}. With a cap below one "
            "the bisection loop never runs and the *lower* bracket bound is "
            "returned -- which is a valid, admissible scaling factor and is "
            "therefore indistinguishable from a real answer."
        )


def validate_c_bounds(c_bounds: Tuple[float, float]) -> Tuple[float, float]:
    """Check the search bracket and return it unpacked.

    Returns
    -------
    tuple of (float, float)
        ``(c_lo, c_hi)``.

    Raises
    ------
    ValueError
        If either bound is non-finite, or the ordering
        ``0 < c_lo < c_hi`` does not hold.
    """
    c_lo, c_hi = c_bounds
    # Finiteness is checked first and separately: ``0 < c_lo < c_hi`` happily
    # accepts an infinite upper bound, and the infinity then travels all the
    # way into the solver before surfacing as a topology complaint about the
    # network. The caller needs to be told about their argument.
    if not (math.isfinite(c_lo) and math.isfinite(c_hi)):
        raise ValueError(
            f"c_bounds must be finite, got {c_bounds!r}. An infinite bound "
            "reaches the solver as an infinite grounding impedance and is "
            "reported there as 'no active bus is referenced to earth' -- a "
            "diagnosis about the network, for what is an argument error."
        )
    if not (0 < c_lo < c_hi):
        raise ValueError(
            f"c_bounds must satisfy 0 < c_lo < c_hi, got {c_bounds!r}."
        )
    return c_lo, c_hi


def classify(iterations: int, c_lo: float, c_hi: float, tol_rel: float) -> str:
    """Name the outcome of a bisection that had a bracketed threshold.

    Only for the branch where ``epr(c_lo) <= limit < epr(c_hi)`` held on
    entry; the fully-admissible case never enters the loop and is labelled
    by the caller with :data:`STATUS_BRACKET_FULLY_ADMISSIBLE`.

    Parameters
    ----------
    iterations : int
        Number of bisection steps actually taken.
    c_lo, c_hi : float
        The final bracket.
    tol_rel : float
        The tolerance the search was asked to reach.

    Returns
    -------
    str
        One of :data:`STATUS_CONVERGED`,
        :data:`STATUS_BRACKET_WITHIN_TOL_ON_ENTRY` or
        :data:`STATUS_MAX_ITER_REACHED`.
    """
    if (c_hi - c_lo) / c_lo > tol_rel:
        return STATUS_MAX_ITER_REACHED
    if iterations == 0:
        return STATUS_BRACKET_WITHIN_TOL_ON_ENTRY
    return STATUS_CONVERGED


def report(status: str, c_lo: float, c_hi: float) -> Dict[str, Any]:
    """Build the diagnostic half of a search result.

    The four keys answer the question ``iterations`` cannot: *how much of
    this number did the search actually prove?*

    Parameters
    ----------
    status : str
        One of the ``STATUS_*`` constants in this module.
    c_lo, c_hi : float
        The final bracket. For
        :data:`STATUS_BRACKET_FULLY_ADMISSIBLE` pass the *initial* bracket:
        the reported interval is then ``(c_hi, inf)``, because nothing was
        established about any factor above the upper bound.

    Returns
    -------
    dict
        ``status``, ``converged``, ``c_bracket`` and ``bracket_rel_width``.
    """
    if status == STATUS_BRACKET_FULLY_ADMISSIBLE:
        # The search proved every factor *inside* the bracket admissible and
        # nothing at all above it, so the interval that provably contains the
        # true maximum is (c_hi, inf). Reporting it that way makes "widen
        # c_bounds" machine-readable: math.isfinite(c_bracket[1]) is False.
        bracket = (c_hi, math.inf)
        width = math.inf
    else:
        bracket = (c_lo, c_hi)
        width = (c_hi - c_lo) / c_lo
    return {
        "status": status,
        "converged": status in CONVERGED_STATUSES,
        "c_bracket": bracket,
        "bracket_rel_width": width,
    }

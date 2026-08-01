# impedance_calculator.py

"""
Frequency-domain evaluation of impedance formula strings.

The public entry point :func:`compute_impedance` parses a SymPy-compatible
formula (e.g. ``"1 + j * f / 50 + rho * l / 1000"``), substitutes the
imaginary unit ``j``/``I`` and evaluates the resulting expression for a list
of frequencies and a dictionary of additional parameters (``rho``, ``l``,
...).

Two performance optimisations sit on top of the SymPy machinery and keep the
public API unchanged:

1.  **Compilation cache.** ``sympy.sympify`` and ``sympy.lambdify`` are
    expensive (each call typically costs a few milliseconds plus an ``exec``
    of the generated source). They are now memoised in
    :func:`_compile_formula` with an ``lru_cache`` keyed on the formula
    string and the tuple of parameter names. All buses sharing a
    :class:`BusType` and all branches sharing a :class:`BranchType` therefore
    reuse the same compiled callable. The first call for a given formula
    pays the SymPy cost; every subsequent call is a dictionary lookup.

2.  **Vectorised evaluation.** ``lambdify(..., modules="numpy")`` is called
    once with the full frequency array, not once per frequency, so the
    Python-level loop is replaced by a single NumPy expression. For typical
    impedance formulas this is another order of magnitude faster than the
    previous per-frequency loop.

The cache lives at module level. Long-running processes (e.g. parameter
sweeps that build many ad-hoc formulas) can release the compiled callables
via :func:`clear_formula_cache`.
"""

import warnings

import sympy as sp
import numpy as np
from functools import lru_cache
from typing import Any, Dict, List, Optional, Tuple

from groundinsight.utils.validations import assert_safe_formula


# Symbols used as the imaginary unit in user-supplied formulas. SymPy treats
# ``I`` as the imaginary unit by default; ``j`` is accepted as a free symbol
# and substituted to ``1j`` for engineering compatibility.
_IMAGINARY_UNIT_SUBS: Dict[str, complex] = {"j": 1j, "I": 1j}

#: Formula string that means *open end*: no galvanic connection at all, hence
#: infinite impedance at every frequency. An overhead line without an earth
#: wire is the canonical case, written ``"NaN"`` in the type definition.
#:
#: The comparison is against the whole stripped, case-folded string, and that
#: is the point of naming it here. An earlier revision tested
#: ``"nan" in formula_str.lower()`` -- a *substring* test, which also fired on
#: every formula that merely contained those three letters somewhere and
#: turned the element into an open circuit without a word. ``resonance``,
#: ``resonanz``, ``dominant``, ``nanofarad`` and ``discriminant`` all contain
#: them, and the first two are ordinary vocabulary in a resonant-earthed
#: (Petersen coil) network.
_OPEN_END_SENTINEL = "nan"

#: Names bound by :func:`compute_impedance` itself, which therefore must not
#: appear as keys in ``params``. ``f`` is the frequency and is always the first
#: argument of the compiled callable; ``I`` and ``j`` are both spellings of the
#: imaginary unit and are substituted before evaluation.
#:
#: Passing one of them used to lose the caller's value without a word --
#: ``params={"I": 5.0}`` evaluated ``I`` as ``1j`` and dropped the 5.0, and
#: ``params={"f": 7.0}`` failed with ``duplicate argument 'f' in function
#: definition``, a leaked Python error from generated code. ``I`` for current
#: is about as natural a name as exists in power engineering, which is exactly
#: why the collision has to be loud rather than resolved by a coin toss.
_RESERVED_PARAM_NAMES: Tuple[str, ...] = ("f", "I", "j")


# ---------------------------------------------------------------------------
# DC (0 Hz)
# ---------------------------------------------------------------------------
#
# A DC study is an ordinary requirement in earthing work -- HVDC earth
# electrodes, stray current from DC traction, cathodic protection, the DC
# component of an asymmetric short-circuit current -- and ``f = 0`` is an
# accepted entry in ``Network.frequencies``. What breaks at 0 Hz is not the
# solver, which is exact there, but the *evaluation of the formula string*.
#
# Three cases have to be told apart, and floating point renders two of them
# identically as NaN:
#
# removable singularity
#     The limit ``f -> 0+`` exists and is finite; only the evaluation at
#     exactly zero fails. Carson's earth-return term is the textbook case:
#     ``omega * ln(658*sqrt(rho/f)/GMR)`` is ``0 * inf`` at f = 0, but omega
#     vanishes linearly while the logarithm diverges only logarithmically, so
#     the limit is 0 and the whole expression tends to the DC resistance.
#     Raising here would make every Carson-type conductor unusable at DC --
#     and unusable in *every transient study*, because the FFT grid always
#     contains a 0 Hz bin.
# true pole
#     The limit is infinite. ``1/(j*omega*C)`` is the textbook case, and
#     infinity is the physically correct answer: a capacitor is an open
#     circuit at DC. This one usually survives evaluation as ``inf`` already.
# genuine failure
#     ``sqrt(rho)`` with a negative ``rho``, a NaN parameter, ``0/0``. Here
#     NaN is the truth and must keep raising.
#
# The three are separated by *approaching* zero numerically and watching the
# differences. See :func:`_resolve_dc`.

#: Frequencies used to approach 0 Hz, in decreasing order. Three points are
#: the minimum that yields two differences, and two differences are the
#: minimum that yields a trend. The values are chosen so that each step is a
#: full decade: for a formula whose limit is approached linearly in ``f`` --
#: which covers every ordinary impedance -- the difference then shrinks by a
#: factor of ten per step, and the value at the last probe carries the limit
#: to about twelve digits.
#:
#: The lower end is not pushed further than 1e-8 Hz on purpose. Carson's
#: logarithm contains ``sqrt(rho/f)``, which is 1e5 * sqrt(rho) at that point;
#: another eight decades and the argument of the logarithm approaches the
#: range where the *evaluation itself* loses accuracy, so the sequence would
#: get noisier rather than better.
_DC_PROBE_FREQUENCIES: Tuple[float, ...] = (1e-6, 1e-7, 1e-8)

#: Relative level below which the second difference counts as machine noise
#: rather than as growth. Without it, a formula that has already converged to
#: full double precision at the first probe would be classified by the
#: comparison of two numbers that are both rounding error.
_DC_CONVERGENCE_NOISE: float = 1e-12

#: How much the *real* part may still move over the last decade before the
#: limit counts as poorly resolved and is reported. A formula that approaches
#: DC linearly moves by about 1e-13 relative; ``sqrt(f)`` moves by 7e-4 and
#: ``log(1/f)`` does not converge at all. Both of the latter are reported.
_DC_RESOLUTION_RTOL: float = 1e-6
_DC_RESOLUTION_ATOL: float = 1e-12

#: Factor by which ``|Im(Z)|`` must fall over the last decade to count as a
#: vanishing reactance rather than a constant one. A genuine reactance falls
#: by about a factor of ten per decade; a constant imaginary offset does not
#: fall at all. Anything in between is reported rather than guessed at.
_DC_IMAG_DECAY: float = 0.5

#: Size below which an imaginary part at 0 Hz is numerical dust rather than a
#: modelling statement, relative to the real part and in absolute terms.
_DC_IMAG_RTOL: float = 1e-9
_DC_IMAG_ATOL: float = 1e-12

#: Factor between the smallest impedance in the network and the substitute that
#: stands in for a zero impedance at 0 Hz -- ``sqrt(machine epsilon)``.
#:
#: A zero impedance at DC is the correct limit of a purely inductive element,
#: but ``1/0`` is not a number, so the nodal formulation needs a small finite
#: stand-in. Two errors decide how small: the stand-in still drops ``I*eps``
#: volts (modelling error, linear in ``eps``), and the diagonal entry ``1/eps``
#: swamps the physical admittances beside it, so recovering an admittance of
#: size ``1/Z`` out of a sum of size ``1/eps`` costs ``u*Z/eps`` relative
#: (cancellation error, inverse in ``eps``). Setting the two equal gives the
#: geometric mean, i.e. ``sqrt(u)`` times an impedance of the network's own
#: scale.
#:
#: Which impedance? Measured, not assumed. Over eight networks spanning a
#: meshed substation (0.5 Ohm electrodes, 1e-4 Ohm conductors), overhead line
#: on rock (~1 kOhm), railway earth (~0.05 Ohm) and a deliberately mixed
#: worst case, the *smallest* impedance in the network keeps the error below
#: 1.2e-5 in the ideal-bond case and below 1e-9 in the ideal-earth case. Using
#: the median or the largest impedance instead fails badly exactly where the
#: network spans many decades -- 3.4e-2 and 6.6e+1 relative error respectively,
#: i.e. the answer stops being an answer. The plain constant ``1e-9 * Z_min``
#: is a factor of 20 worse than ``sqrt(u) * Z_min`` in the same battery.
#:
#: The true optimum is set by the impedances *local to the shorted element*
#: (``sqrt(u * Z_local * Z_path)``), which a solver cannot separate out, so
#: this network-wide rule sits about one to one and a half decades away from
#: it. That costs about 1e-5 relative instead of 3e-7 -- a hundredth of a volt
#: per kilovolt of earth potential rise, far below the uncertainty of the soil
#: resistivity that produced the impedances in the first place.
_DC_SUBSTITUTE_FACTOR: float = float(np.sqrt(np.finfo(float).eps))

#: Reference impedance used when the network offers none, i.e. when *every*
#: impedance at 0 Hz is a short. Such a network has no path to earth at all and
#: its admittance matrix is singular whatever the substitute is, so this value
#: only has to be invertible; the existing "singular admittance matrix" error
#: is the correct answer there and this keeps it reachable.
_DC_SUBSTITUTE_FALLBACK_OHM: float = 1.0


class DCLimitWarning(UserWarning):
    """Something about a formula at 0 Hz needed a decision rather than a value.

    Raised in four situations, all of them at ``f = 0`` only:

    * the imaginary part at DC is dropped and is not small enough to be
      numerical dust -- a reactance cannot survive at DC, so the value is a
      modelling statement that the real-part fallback silently overrides;
    * the limit is not well resolved, i.e. the last decade of the approach
      sequence still moved the real part appreciably;
    * the approach sequence diverges and the element is treated as an open
      circuit;
    * an impedance is zero (or too small to invert) at DC and is replaced by
      the small finite substitute of :func:`dc_substitute_impedance`.

    It is a warning and not an error on purpose: in every one of the three
    cases there is a defensible value to continue with, and a DC study that
    stops on the first unusual formula is of no use. Promote it with
    ``warnings.simplefilter("error", DCLimitWarning)`` to make a study strict.
    """


def _is_open_end(formula_str: str) -> bool:
    """Return ``True`` for the open-end sentinel, and only for it."""
    return formula_str.strip().lower() == _OPEN_END_SENTINEL


def _reject_reserved_params(param_names: Tuple[str, ...]) -> None:
    """Raise if a parameter name collides with a name this module binds."""
    clashes = [name for name in param_names if name in _RESERVED_PARAM_NAMES]
    if not clashes:
        return
    # The offending names lead the message and are the only thing before the
    # colon. The explanation that follows necessarily quotes 'f', 'I' and 'j'
    # again, so a test that merely searched the whole message for "'I'" would
    # pass even if the echo of the caller's name were dropped entirely -- which
    # is precisely the information the caller needs when ``params`` was built
    # in a loop and they cannot see which key tripped the check.
    raise ValueError(
        "Reserved parameter name(s): "
        + ", ".join(repr(name) for name in clashes)
        + ". Rename the parameter (e.g. 'I' -> 'I_k' for a current) -- the "
        "value passed under a reserved name would otherwise be discarded, "
        "because 'f' is bound to the frequency and 'I' and 'j' are both "
        "spellings of the imaginary unit."
    )


def _as_complex(value: Any) -> Any:
    """Widen a scalar parameter to ``complex`` for the complex-plane retry.

    Anything that is not a plain scalar (an array-valued parameter, say) is
    handed back untouched -- the retry is a best effort, and a parameter that
    cannot be widened simply keeps the branch behaviour it had.
    """
    try:
        return complex(value)
    except (TypeError, ValueError):
        return value


def _evaluate(compiled_func, freqs_arr: np.ndarray, param_values: Tuple[Any, ...]):
    """Call a compiled formula over a frequency array and normalise the shape.

    Constant-in-frequency formulas (e.g. ``"5 + 0j"``) return a scalar from
    lambdify regardless of the input shape; broadcast to the requested
    frequency grid so the output has one entry per frequency, matching the
    legacy behaviour. A formula with an unbound symbol (e.g. ``rho`` not
    present in ``params``) makes lambdify return a SymPy expression that
    ``np.asarray(..., dtype=complex)`` rejects; that is the path which turns
    "missing parameter" into a ``ValueError`` in the caller.

    NumPy's floating-point warnings are silenced for the duration of the call.
    They carry no information here: an "invalid value encountered in sqrt" on
    the real axis is exactly the case the caller answers by re-evaluating on
    the complex plane, and "divide by zero" is the capacitor-at-DC case the
    caller normalises to a clean infinity. What the warnings cannot tell apart,
    the caller can -- and a NaN that really does survive both steps is raised
    as a ``ValueError`` naming the formula and the frequency, which is a far
    better signal than a ``RuntimeWarning`` pointing at generated code.
    """
    with np.errstate(invalid="ignore", divide="ignore"):
        result_arr = np.asarray(
            compiled_func(freqs_arr, *param_values), dtype=complex
        )
    if result_arr.ndim == 0:
        return np.full(freqs_arr.shape, result_arr.item(), dtype=complex)
    if result_arr.shape != freqs_arr.shape:
        # Defensive: lambdify with modules="numpy" should always return a
        # matching-shape array, but guard against pathological formulas.
        return np.broadcast_to(result_arr, freqs_arr.shape)
    return result_arr


@lru_cache(maxsize=512)
def _compile_formula(formula_str: str, param_names: Tuple[str, ...]):
    """
    Parse and compile an impedance formula into a fast NumPy callable.

    The result is cached so that all callers passing the same
    ``formula_str`` and ``param_names`` reuse a single compiled function.
    The cache key intentionally includes the tuple of parameter names
    because the resulting callable depends on the argument order
    ``(f, *param_names)``.

    Parameters
    ----------
    formula_str : str
        SymPy-compatible expression, e.g.
        ``"1 + j * f / 50 + rho * l / 1000"``. Recognised free symbols
        are ``f`` and the entries of ``param_names``. ``j`` and ``I``
        are substituted with the imaginary unit.
    param_names : Tuple[str, ...]
        Names of the additional parameters
        in the same order they will be passed at evaluation time.

    Returns
    -------
    Callable
        A NumPy-friendly function with signature
    ``f_arr, *param_values -> np.ndarray | complex`` that returns the
    impedance for every frequency in ``f_arr``.

    Raises
    ------
    ValueError
        If ``formula_str`` cannot be parsed by SymPy.
    """
    assert_safe_formula(formula_str)
    _reject_reserved_params(param_names)
    try:
        sym_f = sp.Symbol("f")
        sym_params = sp.symbols(param_names) if param_names else ()
        # ``sp.symbols`` returns a single Symbol for a 1-tuple, normalise
        # to an iterable so the lambdify signature is built consistently.
        if param_names and not isinstance(sym_params, tuple):
            sym_params = (sym_params,)

        # Bind every declared name to a plain symbol before parsing. Without
        # ``locals``, ``sympify`` resolves names out of the SymPy namespace,
        # and roughly 680 of those names are ones an engineer would reasonably
        # type as a parameter. Two of them are silent: ``E`` evaluates to
        # Euler's number 2.71828... and ``oo`` to infinity, so the value the
        # caller passed is simply discarded. The rest are loud but unhelpful --
        # ``S`` (the conductor cross-section of IEC 60949), ``beta`` (its
        # material constant), ``gamma`` (the propagation constant), ``N``,
        # ``Q``, ``re``, ``im`` resolve to a SymPy class or function and the
        # arithmetic then fails with ``unsupported operand type(s)``, which
        # names neither the parameter nor the reason.
        #
        # A name the caller declared as a parameter *is* a parameter. Names
        # that are not declared keep their SymPy meaning, so ``sqrt``, ``log``,
        # ``exp`` and ``pi`` continue to work as functions and constants.
        local_symbols: Dict[str, sp.Symbol] = dict(zip(param_names, sym_params))
        local_symbols["f"] = sym_f

        expr = sp.sympify(formula_str, locals=local_symbols)
        expr = expr.subs(_IMAGINARY_UNIT_SUBS)

        return sp.lambdify((sym_f, *sym_params), expr, modules=["numpy"])
    except ValueError:
        raise
    except Exception as e:  # pragma: no cover - re-raised as ValueError below
        raise ValueError(
            f"Error compiling impedance formula '{formula_str}': {e}"
        ) from e


def _describe_dc_formula(formula_str: str) -> str:
    """Name a formula in a DC warning message.

    The parameter substitutions are deliberately left out. A DC warning is a
    statement about the *formula*, not about one element: every branch of one
    :class:`~groundinsight.core.core_models.BranchType` shares the formula and
    differs only in ``l``. Naming the substitutions would make each element's
    message unique, and Python's default warning filter -- which suppresses
    repeats of an identical message from an identical location -- would stop
    collapsing them. A hundred-branch network would then emit a hundred lines
    saying the same thing. The numbers quoted alongside this description are
    chosen scale-invariant for the same reason.
    """
    return f"'{formula_str}'"


def _resolve_dc(
    compiled_func,
    param_values: Tuple[Any, ...],
    formula_str: str,
) -> Optional[Tuple[complex, bool]]:
    """Determine ``Z(0)`` for a formula that cannot be evaluated *at* 0 Hz.

    The formula is evaluated on the decreasing sequence
    :data:`_DC_PROBE_FREQUENCIES` and the two consecutive differences
    ``d1 = |Z(e1) - Z(e2)|`` and ``d2 = |Z(e2) - Z(e3)|`` are compared.

    The comparison is deliberately **absolute**, not relative. A relative
    criterion ("has the value stopped changing by more than a part in 1e-9?")
    cannot classify a formula whose limit is zero -- a pure inductance,
    ``j*omega*L`` -- because there the *relative* change stays at 90 % per
    decade forever while the absolute change collapses by a factor of ten per
    decade. Measured on the reference cases: ``d1 = 1.41e-8``,
    ``d2 = 1.41e-9`` for the inductance (shrinking, hence convergent) against
    ``d1 = 1.43e+12``, ``d2 = 1.43e+13`` for a capacitance (growing, hence a
    pole).

    The tie-break on ``d2 <= d1`` is biased towards *convergent* on purpose.
    The two errors are not symmetric: calling a pole convergent returns a
    large finite impedance, which behaves almost like the open circuit it
    should have been, whereas calling a convergent formula a pole would
    silently disconnect a real earthing conductor -- the exact class of silent
    wrong answer this module exists to prevent. A sequence that satisfies
    ``d2 <= d1`` but is still moving (``log(1/f)`` moves by a constant amount
    per decade and so passes the test) is not silently accepted either: it
    trips the resolution warning below, which names the residual.

    Parameters
    ----------
    compiled_func : Callable
        The compiled formula, as returned by :func:`_compile_formula`.
    param_values : tuple
        Parameter values in the compiled signature's order.
    formula_str : str
        Only used to build warning messages.

    Returns
    -------
    tuple of (complex, bool), or None
        The DC impedance and a flag saying whether its imaginary part is a
        vanishing finite-step residue (``True``) rather than a reactance the
        formula genuinely carries at DC (``False``). ``None`` means the
        approach sequence is itself NaN, so the singularity cannot be
        classified -- the caller then leaves the original NaN in place and its
        error message stays the one that fires.
    """
    probes = np.asarray(_DC_PROBE_FREQUENCIES, dtype=float)
    values = _evaluate(compiled_func, probes, param_values)

    # Same real-axis/complex-plane story as in ``compute_impedance``: NumPy
    # picks the branch of sqrt and log from the dtype, so a formula that is
    # perfectly well behaved on the complex plane can come back NaN here.
    nan_mask = np.isnan(values.real) | np.isnan(values.imag)
    if nan_mask.any():
        retry = _evaluate(
            compiled_func,
            probes.astype(complex),
            tuple(_as_complex(value) for value in param_values),
        )
        values = np.where(nan_mask, retry, values)

    seq = [complex(value) for value in values]

    # NaN on the approach as well: this is not a singularity at zero, it is a
    # formula that does not evaluate at all in that neighbourhood (a negative
    # radicand from a bad parameter, a NaN parameter). Hand back None so the
    # caller's NaN message -- which names the formula and the parameters --
    # stays the one the user sees.
    if any(np.isnan(z.real) or np.isnan(z.imag) for z in seq):
        return None

    # Overflow on the approach is a pole that arrived early.
    if any(np.isinf(z.real) or np.isinf(z.imag) for z in seq):
        return complex(np.inf, np.inf), True

    z1, z2, z3 = seq
    d1 = abs(z1 - z2)
    d2 = abs(z2 - z3)
    scale = max(abs(z1), abs(z2), abs(z3))

    if not (d2 <= d1 or d2 <= _DC_CONVERGENCE_NOISE * scale):
        # The growth factor rather than the magnitudes themselves: a formula
        # that is linear in a parameter (``... * l``) has a different magnitude
        # per branch but the same growth factor, so one BranchType collapses
        # into one warning line instead of one per element.
        growth = abs(z3) / abs(z1) if abs(z1) else float("inf")
        warnings.warn(
            f"Impedance formula {_describe_dc_formula(formula_str)} "
            f"has a pole at 0 Hz: approaching DC over "
            f"{_DC_PROBE_FREQUENCIES[0]:g}, {_DC_PROBE_FREQUENCIES[1]:g} and "
            f"{_DC_PROBE_FREQUENCIES[2]:g} Hz the magnitude grows by a factor "
            f"of {growth:.3g} instead of settling. The element is modelled as "
            f"an open circuit at 0 Hz, which is the correct answer for a "
            f"series capacitance (1/(j*omega*C) at DC) and is very probably "
            f"wrong for anything else. Only the 0 Hz bin is affected; all "
            f"other frequencies are evaluated normally.",
            DCLimitWarning,
            stacklevel=3,
        )
        return complex(np.inf, np.inf), True

    # Converged. Report if the last decade still moved the real part -- that
    # is the part which survives the real-part fallback, so it is the part
    # whose resolution matters.
    real_scale = max(abs(z1.real), abs(z2.real), abs(z3.real))
    real_move = abs(z2.real - z3.real)
    if real_move > _DC_RESOLUTION_RTOL * real_scale + _DC_RESOLUTION_ATOL:
        # Relative, for the same reason the pole message is: the residual of
        # ``(0.30 + sqrt(f)) * l`` scales with ``l``, its share of the value
        # does not.
        share = real_move / real_scale if real_scale else float("inf")
        warnings.warn(
            f"Impedance formula {_describe_dc_formula(formula_str)} "
            f"converges only slowly towards 0 Hz: over the last decade of the "
            f"approach ({_DC_PROBE_FREQUENCIES[1]:g} -> "
            f"{_DC_PROBE_FREQUENCIES[2]:g} Hz) the real part still moved by "
            f"{share:.2%} of its value. The value reached is used for the "
            f"0 Hz bin, but it is an approximation of the limit rather than "
            f"the limit. A formula that behaves like sqrt(f) or log(f) near "
            f"DC does this; if the DC value is known analytically, state it "
            f"in the formula (e.g. as a term without f) instead of leaving it "
            f"to the limit.",
            DCLimitWarning,
            stacklevel=3,
        )

    # Is the imaginary part a vanishing residue of the finite step size, or a
    # reactance the formula really carries at DC? A vanishing reactance falls
    # by roughly a factor of ten per decade; a constant offset does not fall.
    im2, im3 = abs(z2.imag), abs(z3.imag)
    residue_vanishes = im3 == 0.0 or im3 <= _DC_IMAG_DECAY * im2

    return z3, residue_vanishes


def clear_formula_cache() -> None:
    """
    Drop all cached compiled formulas.

    Useful for tests that want to measure cold-cache performance and for
    long-running processes that build a large number of ad-hoc formulas
    and want to release the associated SymPy/code-generation memory.
    """
    _compile_formula.cache_clear()


def compute_impedance(
    formula_str: str,
    frequencies: List[float],
    params: Dict[str, float],
    *,
    dc_real_fallback: bool = True,
) -> Dict[float, Any]:
    """
    Compute impedance values for a list of frequencies based on a given formula and parameters.

    This function parses a SymPy-compatible impedance formula string, substitutes the provided
    parameters, and evaluates the impedance across the specified frequencies.

    A formula that is exactly ``"nan"`` (case-insensitive, surrounding
    whitespace ignored) is the *open-end sentinel* and returns infinite
    impedance at every frequency. The match is against the whole string: a
    formula that merely contains those three letters somewhere -- ``resonance``
    or ``nanofarad``, say -- is an ordinary formula and is evaluated.

    Formulas are evaluated on the real frequency axis first and re-evaluated on
    the complex plane at exactly those frequencies where the real evaluation
    returned NaN, because NumPy chooses the branch of ``sqrt`` and ``log`` from
    the dtype rather than the value. NaN that survives that retry is raised as
    a :class:`ValueError` rather than passed on; ``inf`` is a legitimate result
    and is passed through.

    **0 Hz is treated as DC, not as "a very small frequency".** Two things
    happen there and nowhere else:

    1.  A formula that comes back NaN *at* 0 Hz is re-examined by approaching
        zero numerically (see :func:`_resolve_dc`). A removable singularity --
        Carson's earth-return term is the standard case -- resolves to its
        limit; a true pole resolves to infinity, which is the correct open
        circuit for a series capacitance. Only a formula that is NaN on the
        approach as well still raises.
    2.  The imaginary part of a finite DC impedance is dropped
        (``dc_real_fallback``). At DC a reactance either vanishes
        (``j*omega*L -> 0``) or is infinite (``1/(j*omega*C) -> inf``); a
        finite non-zero reactance at 0 Hz has no physical reading, so the
        solvers fall back to the real part. Dropping a value that is not
        numerical dust emits a :class:`DCLimitWarning`.

    The compiled SymPy callable is cached across calls (see
    :func:`_compile_formula`) and the evaluation across all frequencies is
    vectorised through NumPy.

    Parameters
    ----------
    formula_str : str
        A SymPy-compatible formula string for impedance.
        Example: "1 + j * f / 50"
    frequencies : List[float]
        A list of frequencies (in Hz) at which to compute the impedance.
    params : Dict[str, float]
        A dictionary of additional parameters required by the formula.
        Example: {"rho": 100.0}
    dc_real_fallback : bool, keyword-only, default True
        Drop the imaginary part of a finite impedance at ``f = 0``. Set to
        ``False`` by callers for which a complex value at DC is an error to be
        reported rather than a value to be repaired -- :func:`compute_real_value`
        does this, because an RLC parameter is real by contract and its
        "non-real value" check must keep firing at 0 Hz as it does everywhere
        else. The DC *limit* of point 1 above is applied either way.

    Returns
    -------
    Dict[float, Any]
        A dictionary mapping each frequency to its calculated `ComplexNumber` impedance.

    Raises
    ------
    ValueError
        If there is an error in parsing or computing the impedance formula, if
        a parameter name collides with a name this module binds (``f``, ``I``,
        ``j``), or if the formula evaluates to NaN at any frequency.
    TypeError
        If the provided parameters do not match the formula's requirements.

    Warns
    -----
    DCLimitWarning
        If the value at 0 Hz required a decision: a dropped reactance, a
        poorly resolved limit, or a divergent approach treated as an open
        circuit.
    """
    from groundinsight.models.core_models import ComplexNumber

    # Open-ended element: infinite impedance at every frequency. Matched
    # against the whole string, not as a substring -- see
    # :data:`_OPEN_END_SENTINEL`.
    if _is_open_end(formula_str):
        return {
            float(freq): ComplexNumber(real=np.inf, imag=np.inf)
            for freq in frequencies
        }

    # Stable parameter ordering — used both as the lambdify signature and the
    # cache key. ``tuple(params)`` preserves insertion order in Python 3.7+,
    # which is what callers in core_models rely on.
    # The reserved-name check lives in ``_compile_formula``, which is the single
    # choke point every evaluation passes through. Repeating it here would be
    # dead code: a second call can only ever agree with the first.
    param_names: Tuple[str, ...] = tuple(params.keys())
    param_values = tuple(params[name] for name in param_names)

    try:
        compiled_func = _compile_formula(formula_str, param_names)
    except ValueError:
        raise  # propagate with the helpful message from _compile_formula
    except Exception as e:
        raise ValueError(f"Error computing impedance: {e}") from e

    # Frequency conversion, evaluation, and result coercion share the same
    # error surface: any failure (None as frequencies, missing parameters,
    # formula producing a non-numeric value, ...) is reported as ValueError,
    # matching the contract documented in the public API.
    try:
        freqs_arr = np.asarray(frequencies, dtype=float)
        if freqs_arr.ndim == 0:
            raise ValueError(
                "frequencies must be a non-empty iterable of numbers"
            )
        result_arr = _evaluate(compiled_func, freqs_arr, param_values)

        # NumPy picks the branch of ``sqrt`` and ``log`` from the *dtype* of
        # its argument, not from the value: ``np.sqrt(-0.5625)`` is ``nan``
        # while ``np.sqrt(-0.5625+0j)`` is ``0.75j``. With a real frequency
        # array every formula whose argument goes negative therefore collapses
        # to NaN without a word -- and it goes negative in ordinary use,
        # because 0 Hz is a routine entry in ``scalings`` and ``sqrt(f - f0)``
        # or ``log(f/f0)`` is a routine dispersion term.
        #
        # Only SymPy's own answer (``sqrt(1-(50/40)**2) == 0.75*I``) is both
        # the documented semantics and physically meaningful for an impedance,
        # so re-evaluate on the complex plane -- but only at the positions that
        # came back NaN. Every value the real evaluation produced is kept
        # bit-for-bit, which is what keeps ``Abs``, ``Max`` and comparisons on
        # existing formulas working and costs nothing in the common case.
        nan_mask = np.isnan(result_arr.real) | np.isnan(result_arr.imag)
        if nan_mask.any():
            retry_arr = _evaluate(
                compiled_func,
                freqs_arr.astype(complex),
                tuple(_as_complex(value) for value in param_values),
            )
            result_arr = np.where(nan_mask, retry_arr, result_arr)

        # IEEE 754 complex division by zero returns ``inf+nan*j``, not a clean
        # infinity: ``1/(j*2*pi*f*C)`` at 0 Hz -- a capacitive branch at DC,
        # which is an ordinary thing to model -- comes back as ``inf+nan*j``.
        # The NaN there is an artefact of the division algorithm's bookkeeping,
        # not a failed computation: the magnitude is unambiguously infinite,
        # and that is the only part that carries downstream (the admittance
        # 1/Z is zero either way). IEEE 754 cannot represent the *direction* of
        # a complex infinity, so the artefact is cleared to zero rather than
        # guessed at.
        half_infinite = (
            np.isinf(result_arr.real) | np.isinf(result_arr.imag)
        ) & (np.isnan(result_arr.real) | np.isnan(result_arr.imag))
        if half_infinite.any():
            # Written component-wise on purpose. Rebuilding the value as
            # ``real + 1j*imag`` would put the NaN straight back, because
            # ``1j * inf`` is ``nan+inf*j`` -- the real part comes out of
            # ``0 * inf``.
            cleaned = result_arr.copy()
            cleaned.real[half_infinite & np.isnan(cleaned.real)] = 0.0
            cleaned.imag[half_infinite & np.isnan(cleaned.imag)] = 0.0
            result_arr = cleaned
    except ValueError:
        raise
    except Exception as e:
        raise ValueError(f"Error computing impedance: {e}") from e

    # --- 0 Hz is DC, and DC is its own case ----------------------------------
    #
    # Everything above treats 0 Hz as just another entry in the array. It is
    # not: it is the one frequency at which a reactance has no finite non-zero
    # value, and the one frequency at which the common earth-return terms are
    # undefined as written while their limit is perfectly ordinary. Both are
    # handled here, and only here, so no other frequency changes by a bit.
    #
    # This sits outside the block above on purpose. The DC path *warns*, and a
    # caller who turned :class:`DCLimitWarning` into an error with
    # ``warnings.simplefilter("error", ...)`` must get that warning back --
    # ``Warning`` derives from ``Exception``, so a blanket ``except Exception``
    # would repackage their strictness setting as a formula parse failure.
    try:
        dc_positions = np.flatnonzero(freqs_arr == 0.0)
        if dc_positions.size:
            # ``_evaluate`` may hand back a read-only broadcast view for a
            # constant-in-frequency formula. Copy before writing into it.
            result_arr = np.array(result_arr, dtype=complex, copy=True)
        for pos in dc_positions:
            value = complex(result_arr[pos])
            # ``True`` means: an imaginary part, if any, is a residue of the
            # finite step size rather than a reactance the formula asserts.
            residue_vanishes = False

            if np.isnan(value.real) or np.isnan(value.imag):
                resolved = _resolve_dc(
                    compiled_func, param_values, formula_str
                )
                if resolved is None:
                    # Not a singularity at zero -- the formula does not
                    # evaluate in that neighbourhood either. Leave the NaN so
                    # the message below, which names formula and parameters,
                    # is the one the user sees.
                    continue
                value, residue_vanishes = resolved

            # A pole is infinite, and infinity is the answer, not a defect:
            # a series capacitance is an open circuit at DC. Never strip an
            # infinite imaginary part -- that would turn an open circuit into
            # a short.
            if not (np.isfinite(value.real) and np.isfinite(value.imag)):
                result_arr[pos] = value
                continue

            if dc_real_fallback and value.imag != 0.0:
                significant = abs(value.imag) > (
                    _DC_IMAG_RTOL * abs(value.real) + _DC_IMAG_ATOL
                )
                if significant and not residue_vanishes:
                    # The message quotes the X/R *ratio*, not the two values.
                    # Both scale with the element length, so the ratio is the
                    # same for every branch sharing a BranchType -- which makes
                    # the warning identical for all of them and lets Python's
                    # default filter collapse them into one line. Quoting
                    # "0.5+1.2j Ohm" and "0.75+1.8j Ohm" instead would print
                    # one warning per branch, i.e. hundreds for a real network,
                    # for a defect that lives in a single formula. The ratio is
                    # also the more useful number: it is what tells the user
                    # how much of the element they are about to lose.
                    ratio = (
                        f"X/R = {value.imag / value.real:.3g}"
                        if value.real != 0.0
                        else "purely reactive, R = 0"
                    )
                    warnings.warn(
                        f"Impedance formula "
                        f"{_describe_dc_formula(formula_str)} carries a "
                        f"finite reactance at 0 Hz ({ratio}). At DC a "
                        f"reactance either vanishes (j*omega*L -> 0) or is "
                        f"infinite (1/(j*omega*C) -> inf); a finite non-zero "
                        f"one has no physical reading, so the 0 Hz bin falls "
                        f"back to the real part and the reactance is dropped. "
                        f"This is usually a constant term such as '+ j*X' that "
                        f"was meant to apply at power frequency; writing the "
                        f"reactance as 'j*2*pi*f*L' makes it vanish at DC by "
                        f"itself. All other frequencies are unaffected.",
                        DCLimitWarning,
                        stacklevel=2,
                    )
                value = complex(value.real, 0.0)

            result_arr[pos] = value
    except (ValueError, Warning):
        raise
    except Exception as e:
        raise ValueError(f"Error computing impedance at 0 Hz: {e}") from e

    # NaN that survives the complex retry is a genuine computation failure and
    # never a physical result. It must not be handed on: downstream it lands in
    # the admittance matrix, where the LU factorisation reports "singular
    # matrix" and sends the engineer hunting for a topology error that does not
    # exist. ``inf`` on the other hand is legitimate -- it is the open-end
    # sentinel, and it is also what ``1/(j*2*pi*f*C)`` correctly returns at
    # 0 Hz -- so the test below is deliberately NaN-only.
    still_nan = np.isnan(result_arr.real) | np.isnan(result_arr.imag)
    if still_nan.any():
        bad_freqs = freqs_arr[still_nan].tolist()
        shown = ", ".join(f"{freq:g} Hz" for freq in bad_freqs[:5])
        if len(bad_freqs) > 5:
            shown += f", ... ({len(bad_freqs)} frequencies in total)"
        raise ValueError(
            f"Impedance formula '{formula_str}' evaluated to NaN at {shown} "
            f"with parameters {params}. NaN is not a valid impedance -- it "
            f"propagates into the admittance matrix and surfaces much later as "
            f"a singular-matrix error. Check for a division by zero, a domain "
            f"error (e.g. log(0) at f=0 Hz), or a parameter that is itself NaN."
        )

    impedance_dict: Dict[float, ComplexNumber] = {}
    for freq, value in zip(freqs_arr.tolist(), result_arr):
        impedance_dict[float(freq)] = ComplexNumber(
            real=float(value.real), imag=float(value.imag)
        )

    return impedance_dict


def compute_real_value(
    formula_str: str,
    frequencies: List[float],
    params: Dict[str, float],
    *,
    name: str = "value",
    imag_tolerance: float = 1e-9,
) -> Dict[float, float]:
    """
    Evaluate a SymPy formula and return a real-valued dict per frequency.

    Used for the lumped RLC parameters introduced by the transient-simulation
    layer (``BusType.R_formula`` / ``L_formula`` / ``C_formula``,
    ``BranchType.R_self_formula`` / ``L_self_formula`` / ``C_self_formula`` /
    ``R_mutual_formula`` / ``M_mutual_formula``). Internally delegates to the
    same compiled-callable cache that powers :func:`compute_impedance`, so
    formulas shared across types pay the SymPy compile cost only once.

    Parameters
    ----------
    formula_str : str
        SymPy-compatible formula string. Same symbol set
        as :func:`compute_impedance` (``rho``, ``f``, ``l`` etc.). The
        formula must evaluate to a real number at every frequency.
    frequencies : List[float]
        Frequencies (Hz) to evaluate at.
    params : Dict[str, float]
        Additional parameters substituted into
        the formula.
    name : str
        Human-readable parameter name used in error messages
        (e.g. ``"R_self"``). Defaults to ``"value"``.
    imag_tolerance : float
        Maximum absolute imaginary part allowed in
        the evaluated result before a :class:`ValueError` is raised.
        Defaults to ``1e-9``.

    Returns
    -------
    Dict[float, float]
        Mapping of frequency to the real value of the
    formula at that frequency.

    Raises
    ------
    ValueError
        If ``formula_str`` cannot be parsed, if a parameter is
        missing, if the formula evaluates to NaN, or if the result has a
        non-negligible imaginary part (which would indicate a complex formula
        was supplied for a quantity that is meant to be real).
    """
    # Re-use the impedance pipeline -- it already covers compilation
    # caching, vectorised evaluation and NaN handling. We only need to
    # post-process the result and collapse it onto floats while making
    # sure the formula did not accidentally produce a complex value.
    #
    # ``name`` is prefixed onto whatever the pipeline reports. Without it the
    # docstring's promise ("used in error messages") holds only for the two
    # checks below, while every failure raised *inside* the pipeline -- a NaN
    # parameter, a reserved name, an unparseable expression -- names the
    # formula but not the field that carried it. A BranchType that uses the
    # same expression for ``R_self_formula`` and ``R_mutual_formula`` then
    # produces two byte-identical messages.
    #
    # ``dc_real_fallback=False``: at 0 Hz an *impedance* falls back to its real
    # part, because a finite reactance has no reading at DC. An R/L/C parameter
    # is a different quantity -- it is real at every frequency by contract, and
    # a complex value in that field is a mistake to report, not a value to
    # repair. Silently dropping the imaginary part here would disable the
    # "produced a non-real value" check below at 0 Hz and nowhere else. The DC
    # *limit* still applies, so an R formula with a removable singularity at
    # zero resolves instead of raising, exactly as an impedance formula does.
    try:
        z_dict = compute_impedance(
            formula_str, frequencies, params, dc_real_fallback=False
        )
    except ValueError as exc:
        raise ValueError(f"{name}: {exc}") from exc

    real_dict: Dict[float, float] = {}
    for freq, cn in z_dict.items():
        # NaN and inf are not two shades of the same thing and must not share a
        # branch. ``inf`` is a value: it is the open-end sentinel and also the
        # correct answer for ``1/(2*pi*f*C)`` at 0 Hz. ``NaN`` is a failed
        # computation, and storing it into R/L/C without a word is how a typo
        # in a formula ends up being reported, several layers later, as a
        # singular admittance matrix. :func:`compute_impedance` already raises
        # on NaN, so this branch is unreachable through the public path today
        # -- it is kept deliberately, because the ``np.isfinite`` test on the
        # next line cannot tell NaN from inf. Drop this guard and a NaN would
        # not raise but be *stored* as the field value, which is exactly the
        # bug this pass fixed. It is a second lock on the same door, not a
        # duplicate of it.
        if np.isnan(cn.real) or np.isnan(cn.imag):
            raise ValueError(
                f"{name}: formula '{formula_str}' evaluated to NaN at "
                f"f={freq} Hz. Check for a division by zero or a domain error "
                f"(e.g. log(0) at f=0 Hz)."
            )
        # Open end: ``inf`` (from the "nan" sentinel, which returns
        # ``inf+inf*j``). For a real-valued field we drop the imaginary side
        # and keep ``inf``.
        if not np.isfinite(cn.real):
            real_dict[float(freq)] = float(cn.real)
            continue
        # A finite real part with an infinite imaginary part is not an open
        # end, it is a complex value in a field declared real -- that falls
        # through to the non-real check below.
        if abs(cn.imag) > imag_tolerance:
            raise ValueError(
                f"{name}: formula '{formula_str}' produced a non-real "
                f"value at f={freq} Hz: ({cn.real}+{cn.imag}j). RLC "
                f"parameters must evaluate to real numbers."
            )
        real_dict[float(freq)] = float(cn.real)

    return real_dict


# ---------------------------------------------------------------------------
# Plausibility of an impedance that becomes an admittance
# ---------------------------------------------------------------------------

def _describe_source(formula_str: Optional[str], params: Optional[Dict[str, float]]) -> str:
    """Render the origin of an impedance for an error message, if known.

    Parameters
    ----------
    formula_str : str or None
        The formula the value came from, or ``None`` when the value was read
        back from storage or written directly into the model.
    params : dict or None
        The substitutions used, e.g. ``{"rho": 100.0}``.

    Returns
    -------
    str
        A trailing sentence, or the empty string when nothing is known.
    """
    if formula_str is None:
        return ""
    if params:
        shown = ", ".join(f"{key}={value:g}" for key, value in params.items())
        return f" Formula: '{formula_str}' with {shown}."
    return f" Formula: '{formula_str}'."


def is_short_circuit(z: complex) -> bool:
    """Is ``z`` a zero impedance that ``1/z`` cannot express?

    Two values answer yes: exact zero, and a magnitude so small that ``1/z``
    overflows to infinity in double precision (below about 5.6e-309 Ohm). They
    are the same modelling statement one representable step apart, and both
    need the same treatment -- rejection above 0 Hz, the substitute of
    :func:`dc_substitute_impedance` at 0 Hz.

    Infinity answers no: it is the documented open-end sentinel (formula
    ``"nan"``) and ``1/inf == 0`` is exactly right. NaN answers no as well; a
    failed computation is reported by whoever produced it, not here.

    Parameters
    ----------
    z : complex
        The impedance value.

    Returns
    -------
    bool
        True if ``z`` is a short circuit in the sense above.
    """
    if np.isnan(z.real) or np.isnan(z.imag):
        return False
    if np.isinf(z.real) or np.isinf(z.imag):
        return False
    if z == 0:
        return True
    # Ask the question that actually matters -- "can this be inverted?" --
    # instead of comparing against a threshold that would have to be
    # re-derived whenever the complex division algorithm changes. The errstate
    # suppression is required because the overflow being detected here is
    # precisely the condition numpy would warn about.
    with np.errstate(over="ignore", invalid="ignore", divide="ignore"):
        admittance = np.complex128(1.0) / np.complex128(z)
    return not (np.isfinite(admittance.real) and np.isfinite(admittance.imag))


def dc_substitute_impedance(
    magnitudes: List[float],
    shorted_elements: List[str],
    *,
    context: str,
) -> float:
    """Size and announce the stand-in for impedances that are zero at 0 Hz.

    At DC a purely inductive element -- an earthing conductor modelled as
    ``I*2*pi*f*L``, a bond, a busbar -- has an impedance of exactly zero. That
    is the correct limit and not a modelling mistake, but the nodal formulation
    inverts every impedance it uses, and ``1/0`` is not a number. Dropping the
    element instead turns a short into an open circuit, which is the *opposite*
    of the physics: measured on a three-bus example, treating an ideal bond as
    an open circuit overstates the earth potential rise by a factor between 28
    and 4.5 million depending on the network.

    So the element is kept and its impedance replaced by
    ``_DC_SUBSTITUTE_FACTOR`` times the smallest other impedance in the
    network. See that constant for why the factor is ``sqrt(machine epsilon)``
    and why the reference is the smallest rather than the typical impedance.

    The exact-ideal alternative is to merge the two buses (for a bond) or to
    pin the bus potential to zero (for an ideal electrode). Both change the
    size of the nodal system and the identity of the buses the results are
    reported for, so they are a modelling decision rather than a numerical one
    and are deliberately not taken here.

    One warning is emitted per call, not per element: a hundred branches of the
    same type would otherwise produce a hundred identical lines, and Python's
    default warning filter only collapses messages that are character-for-
    character equal.

    Parameters
    ----------
    magnitudes : list of float
        ``abs(Z)`` of every *usable* impedance in the network at that
        frequency, i.e. finite and non-zero. Non-finite entries are ignored, so
        callers may pass them.
    shorted_elements : list of str
        Names of the elements being substituted, used in the warning. Must not
        be empty.
    context : str
        Where this happened, e.g. ``"steady-state solve"`` or
        ``"transient FFT grid"``.

    Returns
    -------
    float
        The substitute impedance in Ohm, always invertible in double precision.
    """
    pool = [
        magnitude
        for magnitude in magnitudes
        if magnitude > 0.0 and np.isfinite(magnitude)
    ]
    reference = min(pool) if pool else _DC_SUBSTITUTE_FALLBACK_OHM
    substitute = _DC_SUBSTITUTE_FACTOR * reference
    # A network whose smallest impedance is itself near the invertibility bound
    # would otherwise get a substitute that is not invertible, i.e. the very
    # failure this function exists to prevent. ``tiny`` is the smallest normal
    # double; its reciprocal is 4.5e307 and still finite.
    substitute = max(substitute, float(np.finfo(float).tiny))

    shown = ", ".join(shorted_elements[:3])
    if len(shorted_elements) > 3:
        shown += f", ... ({len(shorted_elements)} elements in total)"
    reference_note = (
        f"the smallest other impedance in the network ({reference:g} Ohm)"
        if pool
        else (
            f"{_DC_SUBSTITUTE_FALLBACK_OHM:g} Ohm, because every impedance in "
            "the network is zero at 0 Hz and there is no other scale to "
            "measure against"
        )
    )
    warnings.warn(
        f"Zero impedance at 0 Hz in the {context}: {shown}. At DC a purely "
        "inductive element is an ideal short circuit, which is the correct "
        "limit but has no reciprocal, so the nodal system cannot use it "
        f"directly. The affected elements are modelled with {substitute:g} Ohm "
        f"instead (sqrt(machine epsilon) times {reference_note}). That "
        "reproduces the ideal short to roughly five significant digits and is "
        "far better than dropping the element, which would model a short as "
        "an open circuit. Only the 0 Hz bin is affected. For an exact result, "
        "model the two buses of an ideal bond as a single bus.",
        DCLimitWarning,
        stacklevel=3,
    )
    return substitute


def check_passive_impedance(
    z_dict: Dict[float, Any],
    *,
    element: str,
    formula_str: Optional[str] = None,
    params: Optional[Dict[str, float]] = None,
) -> None:
    """Reject impedance values that cannot become an admittance.

    Every value checked here is destined for the diagonal or an off-diagonal
    entry of ``Y`` as its reciprocal ``1/Z``. Three kinds of value have no
    reciprocal the nodal solve can use, and each of them used to be swallowed
    silently:

    ``Z == 0`` **above 0 Hz**
        Exactly zero. Read as a *limit*, zero is the ideal case -- a perfect
        earth electrode, a perfect bond -- and a sweep towards it converges
        there. The nodal formulation cannot express it: an infinite admittance
        is not a number, so a zero-impedance element used to be dropped from
        the matrix instead, which is the *opposite* of the limit. A bus whose
        grounding impedance is zero then reported the full earth potential
        rise and no current into the soil -- exactly what a bus with *no*
        electrode reports. The two were indistinguishable. Model a near-ideal
        electrode with a small finite value instead: the solution converges
        smoothly as ``Z`` goes to zero, with a relative error of the order of
        the ratio between that value and the other impedances in the network.
        In a network whose impedances are of the order of 1 Ohm, ``1e-6`` Ohm
        reproduces the ideal-earth limit to about seven digits, and every
        further decade buys another digit.

        Above 0 Hz an exact zero is a modelling mistake: no physical element
        has zero impedance at a frequency where its inductance acts.

    ``0 < |Z| < 1/DBL_MAX`` (about ``5.6e-309``) **above 0 Hz**
        Not zero, but so small that ``1/Z`` overflows to infinity. This is the
        same failure one representable step away: the reciprocal is unusable
        and a NaN reaches the result columns.

    **At 0 Hz both of those are accepted**, because there they are not a
    mistake but the correct limit: an inductance is a short circuit at DC. The
    solvers replace such a value with the small finite substitute of
    :func:`dc_substitute_impedance` and say so. Rejecting it here would make
    every ordinary ``R + j*omega*L`` conductor formula unusable in a DC study
    and in every transient study, since the FFT grid always contains a 0 Hz
    bin. The other two rules below still apply at 0 Hz.

    ``Re(Z) < 0``
        A passive element -- an earth electrode, an earthing conductor, a
        cable screen -- cannot have a negative resistance; it would generate
        energy. Such a value is always a formula evaluated outside the range
        it was fitted on (``0.05*rho - 2`` is negative for wet soil) and it
        drives the nodal system towards singularity, where the reported earth
        potential rise grows without bound and without warning.

    Infinity is *not* rejected: it is the documented open-end sentinel
    (formula ``"nan"``) and ``1/inf == 0`` is exactly the right contribution
    for a tower without an electrode. NaN is not rejected either -- it is a
    failed computation rather than an implausible one, and
    :func:`compute_impedance` and
    :meth:`~groundinsight.electrical_network.ElectricalNetwork._assert_finite_system`
    already report it with a message about the formula that produced it.
    Duplicating that here would only make the two messages compete.

    Mutual impedances must **not** be passed to this function. Zero mutual
    coupling is the ordinary case for an uncoupled branch, its sign follows
    the chosen direction convention, and it never becomes an admittance.

    Parameters
    ----------
    z_dict : dict
        Mapping of frequency to :class:`~groundinsight.models.core_models.ComplexNumber`
        (or anything with ``.real`` and ``.imag``).
    element : str
        How to name the offending element in the message, e.g.
        ``"bus 'RMU2' (grounding impedance)"``.
    formula_str : str, optional
        The formula the values came from, quoted back in the message.
    params : dict, optional
        The substitutions used, quoted back in the message.

    Raises
    ------
    ValueError
        If any frequency above 0 Hz carries a zero or non-invertible
        impedance, or if any frequency at all carries a negative real part.
        Every offending frequency is listed, grouped by cause.
    """
    zeros: List[float] = []
    too_small: List[Tuple[float, float]] = []
    negative: List[Tuple[float, float]] = []

    for freq, value in z_dict.items():
        zc = complex(float(value.real), float(value.imag))
        # A failed computation, not an implausible one -- reported elsewhere.
        if np.isnan(zc.real) or np.isnan(zc.imag):
            continue
        # The documented open-end sentinel. 1/inf == 0 is the right answer.
        if np.isinf(zc.real) or np.isinf(zc.imag):
            continue
        if is_short_circuit(zc):
            # At DC this is the physics, not a mistake: the solvers substitute
            # a small finite value and warn. Above DC it is a mistake.
            if freq != 0.0:
                if zc == 0:
                    zeros.append(float(freq))
                else:
                    too_small.append((float(freq), abs(zc)))
            continue
        if zc.real < 0:
            negative.append((float(freq), zc.real))

    if not (zeros or too_small or negative):
        return

    origin = _describe_source(formula_str, params)
    parts: List[str] = [f"Invalid impedance for {element}."]

    if zeros:
        parts.append(
            "It is exactly zero at "
            + _render_frequencies(zeros)
            + ". Zero is not an ideal earth in this model and never was -- an "
            "element with no impedance has no admittance either, so it drops "
            "out of the nodal system and reports the opposite of the "
            "ideal-earth limit: full earth potential rise, no current into "
            "the soil, indistinguishable from an element with no electrode at "
            "all. Use a small finite value instead: the model converges "
            "smoothly as Z goes to zero, and the relative error of the result "
            "is of the order of the ratio between that value and the other "
            "impedances in the network -- 1e-6 Ohm in a network whose "
            "impedances are of the order of 1 Ohm is accurate to about seven "
            "digits. At 0 Hz this would be accepted -- an inductance really is "
            "a short circuit at DC, and the solvers substitute a small finite "
            "value there -- but at the frequencies listed above it is a "
            "modelling error."
        )

    if too_small:
        shown = _render_frequencies([freq for freq, _ in too_small])
        magnitude = min(mag for _, mag in too_small)
        parts.append(
            f"Its magnitude is {magnitude:g} Ohm at {shown}, which is too "
            "small to invert in double precision (1/Z overflows below about "
            "5.6e-309 Ohm). The reciprocal would reach the admittance matrix "
            "as infinity and the bus current would come back as NaN. Use a "
            "small finite value that is still representable, e.g. 1e-6 Ohm."
        )

    if negative:
        shown = _render_frequencies([freq for freq, _ in negative])
        worst = min(real for _, real in negative)
        parts.append(
            f"Its real part is negative ({worst:g} Ohm) at {shown}. An earth "
            "electrode, an earthing conductor and a cable screen are passive: "
            "a negative resistance would generate energy, and it pushes the "
            "nodal system towards singularity, where the earth potential rise "
            "grows without bound. This is almost always a fitted formula "
            "evaluated outside the range it was fitted on -- check the "
            "formula at the lowest rho, the lowest frequency and the shortest "
            "length in the model."
        )

    raise ValueError(" ".join(parts) + origin)


def _render_frequencies(freqs: List[float], limit: int = 5) -> str:
    """Render a frequency list for an error message without flooding it.

    A transient study evaluates the same formula at several thousand FFT bins,
    so an unbounded list would bury the sentence that explains the problem.

    Parameters
    ----------
    freqs : list of float
        Offending frequencies, in the order they were collected.
    limit : int
        Maximum number of frequencies to spell out. Defaults to 5.

    Returns
    -------
    str
        Comma-separated frequencies in Hz, truncated with a total count.
    """
    shown = ", ".join(f"{freq:g} Hz" for freq in freqs[:limit])
    if len(freqs) > limit:
        shown += f", ... ({len(freqs)} frequencies in total)"
    return shown

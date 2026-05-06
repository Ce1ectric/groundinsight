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

import sympy as sp
import numpy as np
from functools import lru_cache
from typing import Any, Dict, List, Tuple


# Symbols used as the imaginary unit in user-supplied formulas. SymPy treats
# ``I`` as the imaginary unit by default; ``j`` is accepted as a free symbol
# and substituted to ``1j`` for engineering compatibility.
_IMAGINARY_UNIT_SUBS: Dict[str, complex] = {"j": 1j, "I": 1j}


@lru_cache(maxsize=512)
def _compile_formula(formula_str: str, param_names: Tuple[str, ...]):
    """
    Parse and compile an impedance formula into a fast NumPy callable.

    The result is cached so that all callers passing the same
    ``formula_str`` and ``param_names`` reuse a single compiled function.
    The cache key intentionally includes the tuple of parameter names
    because the resulting callable depends on the argument order
    ``(f, *param_names)``.

    Args:
        formula_str (str): SymPy-compatible expression, e.g.
            ``"1 + j * f / 50 + rho * l / 1000"``. Recognised free symbols
            are ``f`` and the entries of ``param_names``. ``j`` and ``I``
            are substituted with the imaginary unit.
        param_names (Tuple[str, ...]): Names of the additional parameters
            in the same order they will be passed at evaluation time.

    Returns:
        Callable: A NumPy-friendly function with signature
        ``f_arr, *param_values -> np.ndarray | complex`` that returns the
        impedance for every frequency in ``f_arr``.

    Raises:
        ValueError: If ``formula_str`` cannot be parsed by SymPy.
    """
    try:
        sym_f = sp.Symbol("f")
        sym_params = sp.symbols(param_names) if param_names else ()
        # ``sp.symbols`` returns a single Symbol for a 1-tuple, normalise
        # to an iterable so the lambdify signature is built consistently.
        if param_names and not isinstance(sym_params, tuple):
            sym_params = (sym_params,)

        expr = sp.sympify(formula_str)
        expr = expr.subs(_IMAGINARY_UNIT_SUBS)

        return sp.lambdify((sym_f, *sym_params), expr, modules=["numpy"])
    except Exception as e:  # pragma: no cover - re-raised as ValueError below
        raise ValueError(
            f"Error compiling impedance formula '{formula_str}': {e}"
        ) from e


def clear_formula_cache() -> None:
    """
    Drop all cached compiled formulas.

    Useful for tests that want to measure cold-cache performance and for
    long-running processes that build a large number of ad-hoc formulas
    and want to release the associated SymPy/code-generation memory.
    """
    _compile_formula.cache_clear()


def compute_impedance(
    formula_str: str, frequencies: List[float], params: Dict[str, float]
) -> Dict[float, Any]:
    """
    Compute impedance values for a list of frequencies based on a given formula and parameters.

    This function parses a SymPy-compatible impedance formula string, substitutes the provided
    parameters, and evaluates the impedance across the specified frequencies. If the formula
    contains "NaN" (case-insensitive), it returns infinite impedance values for all frequencies.

    The compiled SymPy callable is cached across calls (see
    :func:`_compile_formula`) and the evaluation across all frequencies is
    vectorised through NumPy. The behaviour and return shape are unchanged
    relative to earlier releases.

    Args:
        formula_str (str): A SymPy-compatible formula string for impedance.
                           Example: "1 + j * f / 50"
        frequencies (List[float]): A list of frequencies (in Hz) at which to compute the impedance.
        params (Dict[str, float]): A dictionary of additional parameters required by the formula.
                                   Example: {"rho": 100.0}

    Returns:
        Dict[float, Any]: A dictionary mapping each frequency to its calculated `ComplexNumber` impedance.

    Raises:
        ValueError: If there is an error in parsing or computing the impedance formula.
        TypeError: If the provided parameters do not match the formula's requirements.
    """
    from groundinsight.models.core_models import ComplexNumber

    # Check if "NaN" is present in the formula string (case-insensitive)
    if "nan" in formula_str.lower():
        # Open-ended branch: infinite impedance at every frequency.
        return {
            float(freq): ComplexNumber(real=np.inf, imag=np.inf)
            for freq in frequencies
        }

    # Stable parameter ordering — used both as the lambdify signature and the
    # cache key. ``tuple(params)`` preserves insertion order in Python 3.7+,
    # which is what callers in core_models rely on.
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
        result = compiled_func(freqs_arr, *param_values)
        # Constant-in-frequency formulas (e.g. "5 + 0j") return a scalar from
        # lambdify regardless of the input shape; broadcast to the requested
        # frequency grid so the output dict has one entry per frequency,
        # matching the legacy behaviour. A formula with an unbound symbol
        # (e.g. ``rho`` not present in ``params``) makes lambdify return a
        # SymPy expression that ``np.asarray(..., dtype=complex)`` rejects;
        # this is the path that turns "missing parameter" into a ValueError.
        result_arr = np.asarray(result, dtype=complex)
        if result_arr.ndim == 0:
            result_arr = np.full(freqs_arr.shape, result_arr.item(), dtype=complex)
        elif result_arr.shape != freqs_arr.shape:
            # Defensive: lambdify with modules="numpy" should always return a
            # matching-shape array, but guard against pathological formulas.
            result_arr = np.broadcast_to(result_arr, freqs_arr.shape)
    except ValueError:
        raise
    except Exception as e:
        raise ValueError(f"Error computing impedance: {e}") from e

    impedance_dict: Dict[float, ComplexNumber] = {}
    for freq, value in zip(freqs_arr.tolist(), result_arr):
        impedance_dict[float(freq)] = ComplexNumber(
            real=float(value.real), imag=float(value.imag)
        )

    return impedance_dict

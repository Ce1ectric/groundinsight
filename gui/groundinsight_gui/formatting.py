# groundinsight_gui/formatting.py
"""
Number formatting and parsing -- the only arithmetic the GUI does.

Rule R2 of the service layer: a front end computes no physics, it formats the
numbers of the DTOs. Formatting means four significant digits (the precision
of the input data of a grounding study, not of the solver) and the decimal
separator of the language; parsing means reading a typed cell back with
either separator, because a German user types ``0,8``. Both are pure
functions, tested on their own.
"""

import math
from typing import Any, Optional, Tuple

__all__ = ["DASH", "format_number", "format_scale", "format_unit", "format_value", "parse_number"]

#: Shown where a DTO carries ``None`` (an undefined or non-finite value).
DASH = "–"

_UNIT_SYMBOLS = {"Ohm*m": "Ω·m", "Ohm": "Ω", "deg": "°"}


def format_number(value: Optional[float], language: str, digits: int = 4) -> str:
    """
    Format a number for display.

    Parameters
    ----------
    value : float or None
        The number; ``None`` and non-finite values show as a dash.
    language : {"de", "en"}
        ``"de"`` uses a decimal comma.
    digits : int, optional
        Significant digits (Python's ``g`` format). Default 4.

    Returns
    -------
    str
        The text.

    Examples
    --------
    >>> format_number(187.54548718780015, "de")
    '187,5'
    >>> format_number(None, "en")
    '–'
    """
    if value is None or isinstance(value, bool):
        return DASH
    number = float(value)
    if not math.isfinite(number):
        return DASH
    text = f"{number:.{digits}g}"
    return text.replace(".", ",") if language == "de" else text


def format_scale(low: float, high: float, language: str) -> Tuple[str, str]:
    """
    The two ends of a colour scale, rounded to the same decimal place.

    Four significant digits of the larger end fix the decimal place of both,
    so a numerical zero of a solve (``5.5e-13`` V next to ``196.5`` V) reads
    as ``0`` and not in scientific notation.

    Parameters
    ----------
    low, high : float
        The ends of the scale.
    language : {"de", "en"}
        ``"de"`` uses a decimal comma.

    Returns
    -------
    tuple of str
        The texts of ``low`` and ``high``.

    Examples
    --------
    >>> format_scale(5.505e-13, 196.5, "de")
    ('0', '196,5')
    """
    largest = max(abs(low), abs(high))
    decimals = max(0, 3 - math.floor(math.log10(largest))) if largest > 0 else 0

    def text(value: float) -> str:
        fixed = f"{round(value, decimals) + 0.0:.{decimals}f}"
        if "." in fixed:
            fixed = fixed.rstrip("0").rstrip(".")
        fixed = "0" if fixed == "-0" else fixed
        return fixed.replace(".", ",") if language == "de" else fixed

    return text(low), text(high)


def format_value(value: Optional[float], largest: float, language: str, digits: int = 4) -> str:
    """
    Format one value of a set, with the largest of the set in view.

    Like :func:`format_number`, but a value a million times smaller than the
    largest one of its set is a numerical zero of the solve and reads as
    ``0`` (``5.9e-15`` V beside ``18.03`` V), not in scientific notation.

    Parameters
    ----------
    value : float or None
        The number.
    largest : float
        The largest magnitude of the set the value belongs to.
    language : {"de", "en"}
        ``"de"`` uses a decimal comma.
    digits : int, optional
        Significant digits. Default 4.

    Returns
    -------
    str
        The text.

    Examples
    --------
    >>> format_value(5.89e-15, 18.03, "de"), format_value(18.03, 18.03, "de")
    ('0', '18,03')
    """
    if value is not None and not isinstance(value, bool) and abs(float(value)) < 1e-6 * abs(largest):
        return "0"
    return format_number(value, language, digits)


def parse_number(value: Any) -> float:
    """
    Read a number typed into a cell, with a decimal comma or point.

    Parameters
    ----------
    value : str, int or float
        The cell value. A string may use ``,`` or ``.`` as decimal separator
        and an exponent (``1e3``); thousands separators are not accepted.

    Returns
    -------
    float
        The finite number.

    Raises
    ------
    ValueError
        For an empty text, text that is no number, a boolean, ``None`` and
        non-finite numbers.

    Examples
    --------
    >>> parse_number("0,8")
    0.8
    """
    if isinstance(value, bool) or value is None:
        raise ValueError(f"not a number: {value!r}")
    if isinstance(value, (int, float)):
        number = float(value)
    else:
        text = str(value).strip()
        if text.count(",") > 1 or ("," in text and "." in text):
            raise ValueError(f"not a number: {value!r}")
        try:
            number = float(text.replace(",", "."))
        except ValueError:
            raise ValueError(f"not a number: {value!r}") from None
    if not math.isfinite(number):
        raise ValueError(f"not a finite number: {value!r}")
    return number


def format_unit(unit: Optional[str]) -> str:
    """
    A unit as it is shown: ``Ohm`` as ``Ω``, ``Ohm*m`` as ``Ω·m``.

    Parameters
    ----------
    unit : str or None
        The unit as the DTOs name it (ASCII).

    Returns
    -------
    str
        The display form; empty for ``None``.
    """
    if not unit:
        return ""
    return _UNIT_SYMBOLS.get(unit, unit)

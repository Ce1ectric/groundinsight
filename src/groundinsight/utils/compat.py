# utils/compat.py

"""
Keyword aliases for renamed public parameters.

Audit pass 19 gave every concept of the public API one parameter name
(finding USE8/STD3). The fault is ``fault`` -- ``run_sweep``,
``run_outage_study``, ``bus_response`` and ``analyze_cuts`` already called it
that, ``run_fault`` and half of the analysis layer said ``fault_name``. An
EPR limit is ``u_limit``, a fault duration ``t_k_s``, the IEC 60909 AC heat
factor ``n_factor`` and a frequency ``frequency``: the names the model fields
(``Fault.t_k_s``, ``Fault.n_factor``) and the newer functions were already
using. A user who learned one call could guess the next one; the mixed names
made every call a lookup.

Renaming a public parameter breaks every call site that passes it by keyword,
so the old names keep working for a while. :func:`renamed_parameters` maps an
old keyword onto the new one and says so with a :class:`DeprecationWarning`
that names both spellings and the removal policy
(:data:`DEPRECATION_POLICY`). Positional calls never warn -- the position did
not change.

A decorator rather than ``**kwargs`` in each signature, because

* the signature stays the real one: :func:`inspect.signature`, IDEs, type
  checkers and the API reference show only the new names;
* the alias handling, including the refusal of a value for both spellings,
  exists exactly once instead of once per function;
* the mapping stays inspectable through ``__deprecated_parameters__``, which
  is what the test that pins the rename inventory walks.
"""

import functools
import inspect
import logging
import warnings
from typing import Any, Callable, Dict, TypeVar

logger = logging.getLogger(__name__)

__all__ = ["DEPRECATION_POLICY", "renamed_parameters"]

F = TypeVar("F", bound=Callable[..., Any])

#: The removal policy quoted in every alias warning. It is the project's
#: deprecation policy (see the head of ``CHANGELOG.md``): calendar versions
#: carry no compatibility promise of their own, so the promise is stated here.
DEPRECATION_POLICY = (
    "The old name keeps working, with this warning, for at least two releases "
    "and six months after the release that deprecated it; it can be removed "
    "after that."
)

_POSITIONAL_KINDS = (
    inspect.Parameter.POSITIONAL_ONLY,
    inspect.Parameter.POSITIONAL_OR_KEYWORD,
)


def _display_name(func: Callable[..., Any]) -> str:
    """
    The name a user calls ``func`` by, for the warning text.

    Parameters
    ----------
    func : callable
        The decorated function or method.

    Returns
    -------
    str
        ``func.__qualname__``, with a trailing ``.__init__`` dropped so a
        constructor reads as ``TransientStudy`` rather than
        ``TransientStudy.__init__``.
    """
    name = func.__qualname__
    if name.endswith(".__init__"):
        name = name[: -len(".__init__")]
    return name


def renamed_parameters(**renames: str) -> Callable[[F], F]:
    """
    Accept the old keyword names of renamed parameters, with a warning.

    Parameters
    ----------
    **renames : str
        ``old_name="new_name"`` pairs. ``new_name`` must be a parameter of the
        decorated function and ``old_name`` must not be.

    Returns
    -------
    callable
        A decorator. The function it returns behaves exactly like the
        decorated one, except that a keyword argument under an old name is
        passed on under its new name after a :class:`DeprecationWarning`. It
        carries the mapping as ``__deprecated_parameters__``.

    Raises
    ------
    TypeError
        At decoration time, if ``renames`` is empty, if a new name is not a
        parameter of the decorated function or if an old name still is one.
        At call time, if a value is passed under the new name -- by keyword or
        by position -- *and* under its old name, which is ambiguous in the
        same way as passing one argument twice.

    Notes
    -----
    The warning is raised with ``stacklevel=2``, so it points at the caller's
    line. :class:`DeprecationWarning` is shown by default for code run as
    ``__main__`` (scripts, notebook cells) and under ``pytest``; library code
    that calls through with an old name stays quiet unless the warning
    filters are opened.

    Examples
    --------
    >>> import warnings
    >>> from groundinsight.utils.compat import renamed_parameters
    >>> @renamed_parameters(fault_name="fault")
    ... def solve_one(network, fault):
    ...     return fault
    >>> solve_one(None, "F1")
    'F1'
    >>> with warnings.catch_warnings(record=True) as caught:
    ...     warnings.simplefilter("always")
    ...     solve_one(None, fault_name="F1")
    'F1'
    >>> caught[0].category.__name__
    'DeprecationWarning'
    >>> solve_one.__deprecated_parameters__
    {'fault_name': 'fault'}
    """
    if not renames:
        raise TypeError("renamed_parameters() needs at least one old='new' pair.")

    def decorate(func: F) -> F:
        parameters = inspect.signature(func).parameters
        display = _display_name(func)
        for old, new in renames.items():
            if new not in parameters:
                raise TypeError(
                    f"{display}() has no parameter {new!r}; cannot declare "
                    f"{old!r} as its deprecated alias."
                )
            if old in parameters:
                raise TypeError(
                    f"{display}() still has a parameter {old!r}; an alias "
                    "cannot shadow a real parameter."
                )
        # Where each new name sits when passed positionally, so that
        # ``f(net, "F", fault_name="G")`` is caught as a double value.
        positions: Dict[str, int] = {
            name: index
            for index, (name, parameter) in enumerate(parameters.items())
            if parameter.kind in _POSITIONAL_KINDS
        }

        @functools.wraps(func)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            for old, new in renames.items():
                if old not in kwargs:
                    continue
                value = kwargs.pop(old)
                position = positions.get(new)
                if new in kwargs or (position is not None and len(args) > position):
                    raise TypeError(
                        f"{display}() got a value for {new!r} and for its "
                        f"deprecated alias {old!r}; pass only {new!r}."
                    )
                warnings.warn(
                    f"{display}(): the keyword {old!r} is deprecated, use "
                    f"{new!r} instead. {DEPRECATION_POLICY}",
                    DeprecationWarning,
                    stacklevel=2,
                )
                kwargs[new] = value
            return func(*args, **kwargs)

        wrapper.__deprecated_parameters__ = dict(renames)
        return wrapper  # type: ignore[return-value]

    return decorate

# diagnostics.py

"""
Structured warnings and the diagnostics that belong to a single run.

``groundinsight`` reports conditions that change how a result must be read --
a heuristic phase split in a ring, a frequency the fault scalings do not
cover, a reactance that has no meaning at 0 Hz -- as warnings rather than as
log lines, because the library's logging default is a ``NullHandler`` and a
log line therefore reaches nobody who has not configured logging. A plain
warning is still not enough for two kinds of consumer:

* a program (a study script, a GUI, a server) needs a stable **code** and the
  **elements** a message is about, not only a sentence to show;
* a message must stay with the **result** it belongs to. The obvious tool,
  :func:`warnings.catch_warnings`, cannot provide that: it swaps process-wide
  state, is documented as not thread-safe, and in a server that solves two
  networks at once it would hand each run the other's messages.

This module provides both without changing how the warnings look to a user
who only reads them:

* :class:`GroundinsightWarning` is the base class of every warning the
  package emits. It carries ``code``, ``severity`` and ``elements`` (tuples
  ``(kind, name)`` such as ``("branch", "L1")``). Constructing one -- which
  :func:`warnings.warn` does *before* it consults the warning filters -- also
  records it in the active collection sink, so a warning that a user filters
  out is still part of the result it belongs to.
* :func:`collecting` opens such a sink for one run. The sink lives in a
  :class:`contextvars.ContextVar`, which is per thread and per asyncio task,
  so concurrent runs never see each other's diagnostics.
* :func:`emit` is the preferred way to raise one: it builds the warning with
  its code and element references and hands it to :func:`warnings.warn`.

:func:`groundinsight.run_fault` collects every package warning raised during
the run and stores them as :class:`Diagnostic` records in
``Result.diagnostics``.
"""

import contextlib
import contextvars
import warnings
from typing import Iterator, List, Literal, Optional, Sequence, Tuple

from pydantic import BaseModel, ConfigDict

ElementRef = Tuple[str, str]

_SEVERITIES = ("info", "warning", "error")


class Diagnostic(BaseModel):
    """One structured message attached to a result.

    Attributes
    ----------
    code : str
        Stable, upper-case identifier of the condition, e.g. ``"PHASE_PROXY"``.
        Programs should branch on the code, never on the message text.
    severity : {"info", "warning", "error"}
        How strongly the condition affects the reading of the result.
    category : str
        Name of the warning class that carried the message.
    message : str
        The human-readable text, identical to the warning's message.
    elements : list of tuple of str
        The network elements the message is about, as ``(kind, name)`` pairs;
        ``kind`` is one of ``"bus"``, ``"branch"``, ``"source"``, ``"fault"``,
        ``"bus_type"``, ``"branch_type"`` or ``"network"``.
    """

    model_config = ConfigDict(frozen=True)

    code: str
    severity: Literal["info", "warning", "error"] = "warning"
    category: str
    message: str
    elements: List[Tuple[str, str]] = []


_SINK: "contextvars.ContextVar[Optional[List[Diagnostic]]]" = contextvars.ContextVar(
    "groundinsight_diagnostics_sink", default=None
)


class GroundinsightWarning(UserWarning):
    """Base class of every warning emitted by ``groundinsight``.

    Parameters
    ----------
    *args
        The message, as for any warning.
    code : str, optional
        Stable identifier of the condition. Defaults to the class attribute
        ``default_code``.
    elements : sequence of tuple of str, optional
        Network elements the warning is about, as ``(kind, name)`` pairs.
    severity : {"info", "warning", "error"}, optional
        Defaults to ``"warning"``.

    Notes
    -----
    The constructor records the warning in the active :func:`collecting`
    sink. :func:`warnings.warn` instantiates the category before it applies
    the filters, so the record is made even when the warning is ignored or
    shown only once -- the result always carries its full set of messages,
    independent of how the caller configured the warning machinery.
    """

    default_code: str = "GROUNDINSIGHT"

    def __init__(
        self,
        *args,
        code: Optional[str] = None,
        elements: Sequence[ElementRef] = (),
        severity: str = "warning",
    ):
        super().__init__(*args)
        if severity not in _SEVERITIES:
            raise ValueError(
                f"severity must be one of {_SEVERITIES}, got {severity!r}"
            )
        self.code = code if code is not None else self.default_code
        self.elements = tuple((str(kind), str(name)) for kind, name in elements)
        self.severity = severity
        sink = _SINK.get()
        if sink is not None:
            sink.append(
                Diagnostic(
                    code=self.code,
                    severity=self.severity,
                    category=type(self).__name__,
                    message=str(self),
                    elements=list(self.elements),
                )
            )

    def __reduce__(self):
        # BaseException pickles (cls, args, __dict__); re-running __init__
        # with the stored attributes keeps code/elements across a pickle and
        # does not record the copy in whatever sink is active at load time
        # (the __dict__ update happens after construction anyway).
        return (
            _rebuild_warning,
            (type(self), self.args, self.code, self.elements, self.severity),
        )


def _rebuild_warning(cls, args, code, elements, severity):
    """Recreate a pickled :class:`GroundinsightWarning` outside any sink."""
    token = _SINK.set(None)
    try:
        return cls(*args, code=code, elements=elements, severity=severity)
    finally:
        _SINK.reset(token)


class ModelAssumptionWarning(GroundinsightWarning):
    """A result rests on a modelling assumption or heuristic the user must know.

    Example: the phase current of a ring is split with a proxy impedance
    because the branch types declare no ``phase_impedance_formula``.
    """

    default_code = "MODEL_ASSUMPTION"


class InputWarning(GroundinsightWarning):
    """The input is incomplete or inconsistent but can still be solved.

    Example: ``Fault.scalings`` does not cover every network frequency, so the
    missing ones are scaled with the documented default of 1.0.
    """

    default_code = "INPUT"


def emit(
    category: type,
    message: str,
    *,
    code: Optional[str] = None,
    elements: Sequence[ElementRef] = (),
    severity: str = "warning",
    stacklevel: int = 2,
) -> None:
    """Emit a structured package warning.

    Parameters
    ----------
    category : type
        A subclass of :class:`GroundinsightWarning`.
    message : str
        The text shown to the user.
    code : str, optional
        Stable identifier; defaults to the category's ``default_code``.
    elements : sequence of tuple of str, optional
        ``(kind, name)`` pairs of the elements concerned.
    severity : {"info", "warning", "error"}, optional
        Defaults to ``"warning"``.
    stacklevel : int, optional
        As for :func:`warnings.warn`, counted from the caller of ``emit``.

    Raises
    ------
    TypeError
        If ``category`` is not a :class:`GroundinsightWarning` subclass.
    """
    if not (isinstance(category, type) and issubclass(category, GroundinsightWarning)):
        raise TypeError(
            "emit() takes a GroundinsightWarning subclass, got "
            f"{category!r}"
        )
    warning = category(message, code=code, elements=elements, severity=severity)
    warnings.warn(warning, stacklevel=stacklevel + 1)


@contextlib.contextmanager
def collecting() -> Iterator[List[Diagnostic]]:
    """Collect the package warnings raised inside the ``with`` block.

    Yields
    ------
    list of Diagnostic
        Filled while the block runs, in the order the warnings were raised.

    Notes
    -----
    Blocks nest: the innermost sink receives the records. The sink is bound
    to the current thread or asyncio task through a context variable, so
    collecting in one thread never sees warnings raised in another.
    """
    sink: List[Diagnostic] = []
    token = _SINK.set(sink)
    try:
        yield sink
    finally:
        _SINK.reset(token)


def unique(diagnostics: Sequence[Diagnostic]) -> List[Diagnostic]:
    """Drop exact repetitions while keeping the first-seen order.

    Parameters
    ----------
    diagnostics : sequence of Diagnostic
        Records as collected; a warning raised once per element or per
        frequency may appear several times with identical content.

    Returns
    -------
    list of Diagnostic
        One record per distinct ``(category, code, message, elements)``.
    """
    seen = set()
    kept: List[Diagnostic] = []
    for record in diagnostics:
        key = (record.category, record.code, record.message, tuple(record.elements))
        if key in seen:
            continue
        seen.add(key)
        kept.append(record)
    return kept

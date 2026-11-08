# progress.py

"""
Progress reports and cooperative cancellation for long-running studies.

A GUI runs a study in a worker thread, draws a progress bar and offers a
cancel button; a server reports job progress and honours a client's abort.
Python cannot stop a thread from outside, so cancellation has to be
*cooperative*: the study asks, between two units of work, whether it should go
on. Until audit pass 19 none of the long-running entry points had such a hook
(finding GUI5), and a thread running a sweep could only be waited for.

The protocol is shared by :func:`~groundinsight.run_sweep`,
:func:`~groundinsight.run_outage_study`,
:func:`~groundinsight.find_max_rho_scaling`,
:func:`~groundinsight.find_max_rho_f_scaling` and
:meth:`TransientStudy.solve <groundinsight.TransientStudy.solve>`, each of
which takes two optional keyword arguments:

``progress(done, total, label)``
    Called after every unit of work: a sweep point, an outage scenario (the
    base case included), one solve of a bisection search, a block of FFT
    bins, a stage of the state-space solver. ``done`` counts the finished
    units from 1, ``label`` names the unit. ``total`` is fixed for sweeps,
    outage studies and transients. A bisection search announces the
    evaluations its bracket and tolerance require; it ends below that when it
    stops early (a fully admissible bracket) and raises it if a
    floating-point boundary case needs one step more. ``done <= total`` holds
    at every call.
``should_cancel()``
    Asked before every unit, the first one included. A true answer ends the
    study with :class:`StudyCancelled`.

On cancellation the network is restored to the state it had when the study was
called -- its serialised form, stored results, active fault, paths and
assembled electrical network -- and nothing is returned. Both callbacks run in
the study's own thread; a GUI hands the values over to its event loop itself.
A callback must not edit the network while the study runs: the study
restores it afterwards anyway, and the inverse searches re-solve their trials
on the assembly of their first solve (:mod:`groundinsight.analysis._session`),
which an edit in between would not reach.
An exception raised *by* a callback propagates unchanged, after the study's
usual clean-up.

Why two plain callables rather than a token or job object: they are what every
caller already has (a ``threading.Event.is_set``, a lambda over a GUI flag, a
``tqdm.update``), they keep the library free of any execution model, and a
service layer can wrap them into whatever job abstraction it uses.
"""

import logging
from typing import Callable, Optional

logger = logging.getLogger(__name__)

__all__ = ["CancelCallback", "ProgressCallback", "StudyCancelled", "StudyHooks"]

#: ``progress(done, total, label)`` -- see the module docstring.
ProgressCallback = Callable[[int, int, str], None]

#: ``should_cancel() -> bool`` -- see the module docstring.
CancelCallback = Callable[[], bool]


class StudyCancelled(RuntimeError):
    """
    A study stopped because its ``should_cancel()`` callback returned true.

    The network the study ran on is back in the state it had when the study
    was called; there is no partial result.

    Parameters
    ----------
    message : str, optional
        Human-readable description.
    done : int, optional
        Units of work completed before the cancellation took effect.
    total : int, optional
        Units of work the study had planned at that moment.

    Attributes
    ----------
    done : int
        As passed.
    total : int
        As passed.

    Examples
    --------
    >>> import groundinsight as gi
    >>> error = gi.StudyCancelled("sweep cancelled", done=3, total=10)
    >>> isinstance(error, RuntimeError), error.done, error.total
    (True, 3, 10)
    """

    def __init__(self, message: str = "study cancelled", *, done: int = 0, total: int = 0):
        super().__init__(message)
        self.done = done
        self.total = total


class StudyHooks:
    """
    The two callbacks of one study run, with the bookkeeping behind them.

    Library-internal: the studies create one per call. A study calls
    :meth:`checkpoint` before and :meth:`advance` after every unit of work.

    Parameters
    ----------
    study : str
        Name of the study, used in the :class:`StudyCancelled` message.
    total : int
        Number of units the study plans.
    progress : callable, optional
        ``progress(done, total, label)``.
    should_cancel : callable, optional
        ``should_cancel() -> bool``.

    Raises
    ------
    TypeError
        If ``progress`` or ``should_cancel`` is neither ``None`` nor callable.
        The check runs before the study starts, so a wrong argument does not
        surface only after the first, possibly long, unit of work.
    """

    def __init__(
        self,
        study: str,
        total: int,
        progress: Optional[ProgressCallback] = None,
        should_cancel: Optional[CancelCallback] = None,
    ) -> None:
        for name, callback in (("progress", progress), ("should_cancel", should_cancel)):
            if callback is not None and not callable(callback):
                raise TypeError(
                    f"{name} must be callable or None, got "
                    f"{type(callback).__name__}."
                )
        self.study = study
        self.done = 0
        self.total = max(int(total), 0)
        self._progress = progress
        self._should_cancel = should_cancel

    def plan(self, total: int) -> None:
        """
        Announce the number of units once the study knows it.

        Parameters
        ----------
        total : int
            The planned number of units; never set below the units already
            done.
        """
        self.total = max(int(total), self.done)

    def checkpoint(self) -> None:
        """
        Ask ``should_cancel()`` and stop the study if it says so.

        Raises
        ------
        StudyCancelled
            If ``should_cancel()`` returns a true value.
        """
        if self._should_cancel is not None and self._should_cancel():
            raise StudyCancelled(
                f"{self.study} was cancelled after {self.done} of {self.total} "
                "steps; the network is back in the state it had when the "
                "study was called.",
                done=self.done,
                total=self.total,
            )

    def advance(self, label: str) -> None:
        """
        Count one finished unit and report it.

        Parameters
        ----------
        label : str
            Name of the unit, passed on to ``progress``.
        """
        self.done += 1
        if self.done > self.total:
            self.total = self.done
        if self._progress is not None:
            self._progress(self.done, self.total, str(label))

# api/jobs.py
"""
Jobs: library work off the caller's thread, with progress, cancel and limits.

A user interface has one rule for long work: the event loop that draws the
window never waits for a solve. A server has a second one: one user's study
must not run forever on a shared machine. :class:`JobRunner` serves both. It
runs a job request (:mod:`groundinsight.api.schemas`, ``task`` =
``"solve"``, ``"solve_all"``, ``"sweep"``, ``"outage"``, ``"max_rho"``) on a
network copy in a worker and hands back a :class:`JobHandle` that a UI timer
or an HTTP handler polls (:meth:`JobHandle.status` is a plain DTO).

Two backends, one computation
-----------------------------
``backend="thread"`` (default) runs the task in a worker thread of this
process: no start-up cost, the right choice for a fault solve that takes
milliseconds to seconds and spends its time in NumPy/SciPy. ``backend="process"``
pickles the request and the network copy into a freshly spawned process: it
costs the interpreter start (about a second) and needs inputs that pickle (a
network does, finding GUI2), and in return the job can be stopped *hard* and
cannot take the caller's process down. Both run the same task function of
:mod:`groundinsight.api.tasks` on equal inputs, and the tests compare their
DTOs field by field.

Progress, cancel, runtime limit
-------------------------------
Progress and cancellation use the library's study hooks (finding GUI5):
``progress(done, total, label)`` is forwarded into the handle (from a child
process through a pipe), and ``should_cancel()`` answers true once
:meth:`JobHandle.cancel` was called or the runtime limit has passed. The study
then raises :class:`groundinsight.StudyCancelled` at its next unit boundary and
the job ends ``"cancelled"`` or ``"timeout"`` -- with no partial result.
Cancellation is cooperative in a thread, because Python cannot stop a thread
from outside: a single solve, which has no hook inside, runs to its end and its
result is then discarded. In a process the runner asks the same way first and
terminates the process if it has not stopped ``cancel_grace_s`` later, so a
process job never runs much beyond its limit. A queued job that is cancelled
never starts.

A job either completes (``"done"``, with its DTO) or it does not
(``"cancelled"``, ``"timeout"``, ``"failed"``) -- a result that arrives after
its cancel request or its limit is not delivered. A failure is data
(:class:`~groundinsight.api.schemas.JobErrorDTO`: exception type and message),
never an exception in the polling thread; :meth:`JobHandle.result` re-raises it
as :class:`JobFailed` for code that waits.

The runner keeps no state outside its instances; a server may share one
runner between sessions (each job carries its own limit) or give each session
its own.
"""

import copy
import itertools
import logging
import multiprocessing
import pickle
import threading
import time
import traceback
import uuid
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Callable, Dict, List, Optional

import groundinsight as gi

from .schemas import JobErrorDTO, JobStatusDTO, ProgressDTO, parse_job_request
from .tasks import TaskOutcome, run_task

logger = logging.getLogger(__name__)

__all__ = ["JobCancelled", "JobFailed", "JobHandle", "JobRunner", "JobTimeout", "snapshot_network"]


class JobCancelled(gi.StudyCancelled):
    """The job was cancelled -- before it started or at a unit boundary.

    A subclass of :class:`groundinsight.StudyCancelled`, so code that handles
    a cancelled study handles a cancelled job too.
    """


class JobTimeout(JobCancelled):
    """The job's runtime limit stopped it."""


class JobFailed(RuntimeError):
    """The job raised; the original exception is the ``__cause__`` where it
    is available (thread backend).

    Attributes
    ----------
    error_type : str
        Class name of the original exception.
    error_message : str
        Its message.
    """

    def __init__(self, message: str, *, error_type: str = "", error_message: str = "") -> None:
        super().__init__(message)
        self.error_type = error_type
        self.error_message = error_message


def snapshot_network(network: gi.Network) -> gi.Network:
    """
    An independent copy of a network's inputs, for a job to compute on.

    ``copy.deepcopy`` with a memo that maps the stored results and the
    enumerated paths to empty dictionaries skips both: they are the bulk of a
    solved network and a solve needs neither (empty paths are rebuilt on
    demand). The copy's elements are attached to the copy, so a study that
    edits it re-derives correctly, and a result computed on it carries the
    same input digest as one computed on the original.

    Parameters
    ----------
    network : Network
        The network.

    Returns
    -------
    Network
        The copy.

    Examples
    --------
    >>> import groundinsight as gi
    >>> net = gi.create_network(name="n", frequencies=[50.0])
    >>> copy_ = snapshot_network(net)
    >>> copy_ is net, copy_.name
    (False, 'n')
    """
    memo = {id(network.results): {}, id(network.paths): {}}
    return copy.deepcopy(network, memo)


#: States in which a job has ended.
FINAL_STATES = ("done", "failed", "cancelled", "timeout")


class JobHandle:
    """
    Observable state of one submitted job.

    Created by :meth:`JobRunner.submit`; thread-safe.

    Attributes
    ----------
    job_id : str
        Identifier, unique within the runner.
    task : str
        The request's task.
    request : BaseModel
        The job request.
    revision : int, optional
        Session revision of the job's input.
    backend : {"thread", "process"}
        Where the job runs.
    """

    def __init__(
        self,
        job_id: str,
        request: Any,
        backend: str,
        revision: Optional[int],
        max_runtime_s: Optional[float],
        label: str,
    ) -> None:
        self.job_id = job_id
        self.request = request
        self.task = request.task
        self.backend = backend
        self.revision = revision
        self.max_runtime_s = max_runtime_s
        self.label = label
        self._lock = threading.Lock()
        self._state = "queued"
        self._progress = ProgressDTO()
        self._started: Optional[float] = None
        self._finished: Optional[float] = None
        self._error: Optional[JobErrorDTO] = None
        self._exception: Optional[BaseException] = None
        self._result: Any = None
        self._cancel = threading.Event()
        self._ended = threading.Event()

    # -- public ---------------------------------------------------------------

    @property
    def state(self) -> str:
        """``"queued"``, ``"running"``, ``"done"``, ``"failed"``, ``"cancelled"`` or ``"timeout"``."""
        with self._lock:
            return self._state

    @property
    def done(self) -> bool:
        """``True`` once the job has ended, in whatever state."""
        return self._ended.is_set()

    def status(self) -> JobStatusDTO:
        """
        A JSON-safe snapshot of the job.

        Returns
        -------
        JobStatusDTO
            State, progress, timing and error.
        """
        with self._lock:
            elapsed = None
            if self._started is not None:
                elapsed = (self._finished or time.monotonic()) - self._started
            return JobStatusDTO(
                job_id=self.job_id,
                task=self.task,
                label=self.label,
                backend=self.backend,
                state=self._state,
                revision=self.revision,
                progress=self._progress,
                elapsed_s=elapsed,
                max_runtime_s=self.max_runtime_s,
                error=self._error,
            )

    def cancel(self) -> bool:
        """
        Ask the job to stop.

        Returns
        -------
        bool
            ``True`` if the job will not complete: a queued job is cancelled
            at once, a running one stops at its next unit boundary (a process
            is terminated if it does not). ``False`` if it has already ended.
        """
        with self._lock:
            if self._state in FINAL_STATES:
                return False
            self._cancel.set()
            if self._state == "queued":
                self._state = "cancelled"
                self._finished = time.monotonic()
                self._ended.set()
            return True

    def wait(self, timeout: Optional[float] = None) -> bool:
        """
        Block until the job has ended.

        Parameters
        ----------
        timeout : float, optional
            Seconds; ``None`` waits indefinitely.

        Returns
        -------
        bool
            ``True`` if the job has ended.
        """
        return self._ended.wait(timeout)

    def result(self, timeout: Optional[float] = None) -> Any:
        """
        Wait for the job and return its DTO.

        Parameters
        ----------
        timeout : float, optional
            Seconds to wait; ``None`` waits indefinitely.

        Returns
        -------
        BaseModel
            The result DTO of a ``"done"`` job.

        Raises
        ------
        TimeoutError
            If the job has not ended within ``timeout``.
        JobTimeout
            If the runtime limit stopped it.
        JobCancelled
            If it was cancelled.
        JobFailed
            If it raised; ``__cause__`` is the original exception where
            available.
        """
        if not self._ended.wait(timeout):
            raise TimeoutError(f"job {self.job_id} ({self.label}) has not ended yet")
        with self._lock:
            state, error, exception, result = self._state, self._error, self._exception, self._result
            progress = self._progress
        if state == "done":
            return result
        if state == "timeout":
            raise JobTimeout(
                f"job {self.job_id} ({self.label}) exceeded its runtime limit of "
                f"{self.max_runtime_s} s",
                done=progress.done,
                total=progress.total,
            )
        if state == "cancelled":
            raise JobCancelled(
                f"job {self.job_id} ({self.label}) was cancelled",
                done=progress.done,
                total=progress.total,
            )
        message = f"job {self.job_id} ({self.label}) failed: {error.type}: {error.message}"
        raise JobFailed(message, error_type=error.type, error_message=error.message) from exception

    # -- runner side ------------------------------------------------------------

    def _start(self) -> bool:
        """Mark the job running; ``False`` if it was cancelled while queued."""
        with self._lock:
            if self._state != "queued":
                return False
            self._state = "running"
            self._started = time.monotonic()
            return True

    def _report(self, done: int, total: int, label: str) -> None:
        """Record one progress report of the library."""
        progress = ProgressDTO(
            done=int(done),
            total=int(total),
            label=str(label),
            fraction=(done / total) if total else None,
        )
        with self._lock:
            self._progress = progress

    def _timed_out(self) -> bool:
        """Whether the runtime limit has passed."""
        with self._lock:
            started = self._started
        return (
            self.max_runtime_s is not None
            and started is not None
            and time.monotonic() - started > self.max_runtime_s
        )

    def _should_stop(self) -> bool:
        """The ``should_cancel`` hook: a cancel request or the limit."""
        return self._cancel.is_set() or self._timed_out()

    def _finish(
        self,
        state: str,
        *,
        result: Any = None,
        error: Optional[JobErrorDTO] = None,
        exception: Optional[BaseException] = None,
    ) -> None:
        """Record the end of the job and wake its waiters."""
        with self._lock:
            self._state = state
            self._result = result
            self._error = error
            self._exception = exception
            self._finished = time.monotonic()
        self._ended.set()

    def _stop_state(self) -> str:
        """``"cancelled"`` after a cancel request, ``"timeout"`` after the limit."""
        return "cancelled" if self._cancel.is_set() else "timeout"


def _process_main(sender: Any, payload: bytes, revision: Optional[int], stop: Any) -> None:
    """
    Entry point of a job process (``spawn``): run one task, report through a pipe.

    Messages: ``("progress", done, total, label)`` while running, then exactly
    one of ``("done", pickled TaskOutcome)``, ``("cancelled", message)`` or
    ``("error", type name, message, traceback)``.

    Parameters
    ----------
    sender : multiprocessing.connection.Connection
        Write end of the pipe.
    payload : bytes
        Pickled ``(request, network)``.
    revision : int, optional
        Session revision for the DTOs.
    stop : multiprocessing.Event
        Set by the parent to cancel.
    """
    try:
        request, network = pickle.loads(payload)
        outcome = run_task(
            request,
            network,
            revision=revision,
            progress=lambda done, total, label: sender.send(("progress", done, total, label)),
            should_cancel=stop.is_set,
        )
        sender.send(("done", pickle.dumps(outcome, protocol=pickle.HIGHEST_PROTOCOL)))
    except gi.StudyCancelled as exc:
        sender.send(("cancelled", str(exc)))
    except BaseException as exc:  # reported to the parent, which decides
        sender.send(("error", type(exc).__name__, str(exc), traceback.format_exc()))
    finally:
        sender.close()


class JobRunner:
    """
    Runs job requests in worker threads or worker processes.

    Parameters
    ----------
    backend : {"thread", "process"}
        Where jobs run (see the module docstring).
    max_workers : int
        Jobs that run at the same time; further jobs wait in the queue.
        Default 1.
    max_runtime_s : float, optional
        Default runtime limit of a job; :meth:`submit` can set one per job.
    cancel_grace_s : float
        Process backend: seconds between the cooperative cancel request and
        terminating the process. Default 5.
    poll_interval_s : float
        Process backend: how often the pipe and the limits are checked.
    keep_finished : int
        Finished jobs kept for :meth:`get`/:meth:`jobs`; older ones are
        forgotten. Default 200.

    Raises
    ------
    ValueError
        For an unknown backend or a non-positive worker count.

    Examples
    --------
    >>> from groundinsight.api import EXAMPLES, JobRunner, SolveRequestDTO
    >>> runner = JobRunner()
    >>> handle = runner.submit(SolveRequestDTO(fault="F1"), EXAMPLES["mv_ring"]())
    >>> handle.result(timeout=60).key_figures.fault_bus
    'b10'
    >>> handle.status().state
    'done'
    >>> runner.shutdown()
    """

    def __init__(
        self,
        backend: str = "thread",
        *,
        max_workers: int = 1,
        max_runtime_s: Optional[float] = None,
        cancel_grace_s: float = 5.0,
        poll_interval_s: float = 0.05,
        keep_finished: int = 200,
    ) -> None:
        if backend not in ("thread", "process"):
            raise ValueError(f"backend must be 'thread' or 'process', got {backend!r}")
        if not isinstance(max_workers, int) or max_workers < 1:
            raise ValueError(f"max_workers must be a positive integer, got {max_workers!r}")
        self.backend = backend
        self.max_runtime_s = max_runtime_s
        self.cancel_grace_s = float(cancel_grace_s)
        self.poll_interval_s = float(poll_interval_s)
        self.keep_finished = int(keep_finished)
        self._executor = ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="gi-job")
        self._jobs: Dict[str, JobHandle] = {}
        self._lock = threading.Lock()
        self._closed = False
        self._counter = itertools.count(1)

    def submit(
        self,
        request: Any,
        network: gi.Network,
        *,
        label: Optional[str] = None,
        revision: Optional[int] = None,
        max_runtime_s: Optional[float] = None,
        on_success: Optional[Callable[[TaskOutcome], Any]] = None,
        copy_network: bool = True,
    ) -> JobHandle:
        """
        Queue a job.

        Parameters
        ----------
        request : job request DTO or dict
            What to compute (:func:`~groundinsight.api.schemas.parse_job_request`).
        network : Network
            The network to compute on.
        label : str, optional
            Description for the status; default ``"<task> <fault>"``.
        revision : int, optional
            Session revision of ``network``, written into the DTOs.
        max_runtime_s : float, optional
            This job's runtime limit; default the runner's.
        on_success : callable, optional
            Called in the worker with the :class:`TaskOutcome` of a completed
            job; its return value becomes the job's result (the session uses
            it to store fault results on its network). Default: the outcome's
            DTO.
        copy_network : bool
            Compute on a snapshot (:func:`snapshot_network`) instead of
            ``network`` itself. Default ``True``; pass ``False`` only for a
            network the caller no longer touches.

        Returns
        -------
        JobHandle
            The job.

        Raises
        ------
        RuntimeError
            If the runner has been shut down.
        ValueError
            If the request is invalid.
        """
        request = parse_job_request(request)
        if copy_network:
            network = snapshot_network(network)
        limit = max_runtime_s if max_runtime_s is not None else self.max_runtime_s
        fault = getattr(request, "fault", None)
        text = label or (f"{request.task} {fault}" if fault else request.task)
        job_id = f"{next(self._counter)}-{uuid.uuid4().hex[:8]}"
        handle = JobHandle(job_id, request, self.backend, revision, limit, text)
        with self._lock:
            if self._closed:
                raise RuntimeError("the job runner has been shut down")
            self._jobs[job_id] = handle
            self._forget_old()
            worker = self._run_in_process if self.backend == "process" else self._run_in_thread
            self._executor.submit(worker, handle, network, on_success)
        return handle

    def get(self, job_id: str) -> JobHandle:
        """
        A job by its identifier.

        Parameters
        ----------
        job_id : str
            The identifier.

        Returns
        -------
        JobHandle
            The job.

        Raises
        ------
        KeyError
            If the runner does not know the job (any more).
        """
        with self._lock:
            return self._jobs[job_id]

    def jobs(self) -> List[JobHandle]:
        """
        The jobs the runner knows, oldest first.

        Returns
        -------
        list of JobHandle
            Queued, running and recently finished jobs.
        """
        with self._lock:
            return list(self._jobs.values())

    def shutdown(self, wait: bool = True) -> None:
        """
        Cancel every job that has not ended and stop the workers.

        Parameters
        ----------
        wait : bool
            Wait until running jobs have stopped.
        """
        with self._lock:
            self._closed = True
            handles = list(self._jobs.values())
        for handle in handles:
            handle.cancel()
        self._executor.shutdown(wait=wait, cancel_futures=True)

    def _forget_old(self) -> None:
        """Drop the oldest finished jobs beyond :attr:`keep_finished` (lock held)."""
        finished = [job_id for job_id, h in self._jobs.items() if h.done]
        for job_id in finished[: max(0, len(finished) - self.keep_finished)]:
            del self._jobs[job_id]

    # -- workers ----------------------------------------------------------------

    @staticmethod
    def _complete(handle: JobHandle, outcome: TaskOutcome, on_success) -> None:
        """Deliver a completed task -- unless a cancel request or the limit came first."""
        if handle._should_stop():
            handle._finish(handle._stop_state())
            return
        try:
            result = on_success(outcome) if on_success is not None else outcome.dto
        except Exception as exc:  # a failing callback is the job's failure
            # The failure reaches the caller as the job's error (status and
            # ``result()``); the traceback is for debugging only.
            logger.debug("result handler of job %s failed", handle.job_id, exc_info=True)
            handle._finish("failed", error=JobErrorDTO(type=type(exc).__name__, message=str(exc)), exception=exc)
            return
        handle._finish("done", result=result)

    def _run_in_thread(self, handle: JobHandle, network: gi.Network, on_success) -> None:
        """Worker of the thread backend."""
        if not handle._start():
            return
        try:
            outcome = run_task(
                handle.request,
                network,
                revision=handle.revision,
                progress=handle._report,
                should_cancel=handle._should_stop,
            )
        except gi.StudyCancelled as exc:
            handle._finish(handle._stop_state(), exception=exc)
            return
        except Exception as exc:
            handle._finish(
                "failed", error=JobErrorDTO(type=type(exc).__name__, message=str(exc)), exception=exc
            )
            return
        self._complete(handle, outcome, on_success)

    def _run_in_process(self, handle: JobHandle, network: gi.Network, on_success) -> None:
        """Worker of the process backend: spawn, relay, enforce, collect."""
        if not handle._start():
            return
        try:
            payload = pickle.dumps((handle.request, network), protocol=pickle.HIGHEST_PROTOCOL)
        except Exception as exc:
            handle._finish(
                "failed",
                error=JobErrorDTO(type=type(exc).__name__, message=f"the job input does not pickle: {exc}"),
                exception=exc,
            )
            return
        context = multiprocessing.get_context("spawn")
        receiver, sender = context.Pipe(duplex=False)
        stop = context.Event()
        process = context.Process(
            target=_process_main,
            args=(sender, payload, handle.revision, stop),
            name=f"gi-job-{handle.job_id}",
            daemon=True,
        )
        process.start()
        sender.close()
        message = None
        stop_requested_at: Optional[float] = None
        try:
            while message is None:
                if receiver.poll(self.poll_interval_s):
                    try:
                        incoming = receiver.recv()
                    except EOFError:
                        break
                    if incoming[0] == "progress":
                        handle._report(*incoming[1:])
                    else:
                        message = incoming
                elif not process.is_alive() and not receiver.poll(0):
                    break
                if message is None and handle._should_stop():
                    if stop_requested_at is None:
                        stop.set()
                        stop_requested_at = time.monotonic()
                    elif time.monotonic() - stop_requested_at > self.cancel_grace_s:
                        logger.info("terminating job process %s", process.name)
                        process.terminate()
                        break
        finally:
            process.join(timeout=10)
            if process.is_alive():  # pragma: no cover - terminate did not work
                process.kill()
                process.join(timeout=10)
            receiver.close()
        if message is None:
            if handle._should_stop():
                handle._finish(handle._stop_state())
            else:
                handle._finish(
                    "failed",
                    error=JobErrorDTO(
                        type="ProcessError",
                        message=f"the job process ended without a result (exit code {process.exitcode})",
                    ),
                )
            return
        if message[0] == "cancelled":
            handle._finish(handle._stop_state())
        elif message[0] == "error":
            _, error_type, error_message, trace = message
            logger.debug("job %s failed in its process:\n%s", handle.job_id, trace)
            handle._finish("failed", error=JobErrorDTO(type=error_type, message=error_message))
        else:
            self._complete(handle, pickle.loads(message[1]), on_success)

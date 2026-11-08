# api/tasks.py
"""
What a job computes: one function per job request.

Each task receives the job's *own* copy of the network (taken by the session
when the job was submitted), the request DTO and a :class:`TaskContext` with
the job's progress and cancellation callbacks, and returns a
:class:`TaskOutcome`: the result DTO and, for fault solves, the library's
:class:`~groundinsight.Result` objects that the session stores on its network.

The tasks call the public library functions with the documented hooks --
:func:`groundinsight.solve`, :func:`~groundinsight.run_sweep`,
:func:`~groundinsight.run_outage_study`,
:func:`~groundinsight.find_max_rho_scaling` -- and convert what comes back
with the builders of :mod:`groundinsight.api.views`. They are module-level
functions registered by name in :data:`TASKS`, so a worker *process* finds the
same function, runs the same code on an unpickled copy of the same network
and returns the same DTO: the thread and the process backend cannot differ in
what they compute, only in where.

A study's own ``progress``/``should_cancel`` hooks (finding GUI5) are passed
straight through. A single solve has no hook inside; it is checked for
cancellation before it starts, and a fault sweep between faults.
"""

import logging
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, Optional

import polars as pl

import groundinsight as gi
from groundinsight.diagnostics import collecting, unique

from .schemas import (
    ComplexDTO,
    FaultResultsDTO,
    MaxRhoRequestDTO,
    OutageStudyRequestDTO,
    SolveAllRequestDTO,
    SolveRequestDTO,
    StudyResultDTO,
    SweepRequestDTO,
)
from .views import diagnostic_dto, fault_result_dto, table_dto

logger = logging.getLogger(__name__)

__all__ = ["TASKS", "TaskContext", "TaskOutcome", "run_task"]


@dataclass
class TaskOutcome:
    """
    What a task returns.

    Attributes
    ----------
    dto : BaseModel
        The result DTO of the job.
    results : dict of str to Result
        Library results by fault, for the session to store on its network
        (empty for studies, which leave the network as found).
    """

    dto: Any
    results: Dict[str, gi.Result] = field(default_factory=dict)


class TaskContext:
    """
    The job's side of the library's study hooks.

    Parameters
    ----------
    revision : int, optional
        Session revision of the job's input; written into the DTOs.
    progress : callable, optional
        ``progress(done, total, label)``.
    should_cancel : callable, optional
        ``should_cancel() -> bool``.
    """

    def __init__(
        self,
        revision: Optional[int] = None,
        progress: Optional[Callable[[int, int, str], None]] = None,
        should_cancel: Optional[Callable[[], bool]] = None,
    ) -> None:
        self.revision = revision
        self.progress = progress
        self.should_cancel = should_cancel

    def checkpoint(self, done: int, total: int, what: str) -> None:
        """
        Stop here if the job is to be cancelled.

        Parameters
        ----------
        done, total : int
            Units finished and planned, for the exception.
        what : str
            The task, for the message.

        Raises
        ------
        groundinsight.StudyCancelled
            If ``should_cancel()`` returns true.
        """
        if self.should_cancel is not None and self.should_cancel():
            raise gi.StudyCancelled(
                f"{what} was cancelled after {done} of {total} steps",
                done=done,
                total=total,
            )

    def report(self, done: int, total: int, label: str) -> None:
        """
        Forward one progress report.

        Parameters
        ----------
        done, total : int
            Units finished and planned.
        label : str
            The unit just finished.
        """
        if self.progress is not None:
            self.progress(done, total, label)


def _solve_one(network: gi.Network, fault: str, mode: str, context: TaskContext):
    """
    Solve one fault on the job's network and build its DTO.

    Parameters
    ----------
    network : Network
        The job's own copy; the result is stored on it, for the views.
    fault : str
        The fault.
    mode : str
        Phase-current mode.
    context : TaskContext
        For the revision.

    Returns
    -------
    tuple
        ``(Result, FaultResultDTO)``.
    """
    started = time.perf_counter()
    result = gi.solve(network, fault, phase_current_mode=mode)
    duration = time.perf_counter() - started
    network.results[fault] = result
    dto = fault_result_dto(
        network,
        fault,
        revision=context.revision,
        phase_current_mode=mode,
        stale=False,
        duration_s=duration,
    )
    return result, dto


def _solve(network: gi.Network, request: SolveRequestDTO, context: TaskContext) -> TaskOutcome:
    """Task ``"solve"``: one fault (:func:`groundinsight.solve`)."""
    context.checkpoint(0, 1, f"solve {request.fault}")
    result, dto = _solve_one(network, request.fault, request.phase_current_mode, context)
    context.report(1, 1, f"solve {request.fault}")
    return TaskOutcome(dto=dto, results={request.fault: result})


def _solve_all(network: gi.Network, request: SolveAllRequestDTO, context: TaskContext) -> TaskOutcome:
    """Task ``"solve_all"``: several faults, cancellable between them."""
    faults = list(request.faults) if request.faults is not None else list(network.faults)
    results: Dict[str, gi.Result] = {}
    dtos = []
    for done, fault in enumerate(faults):
        context.checkpoint(done, len(faults), "fault sweep")
        result, dto = _solve_one(network, fault, request.phase_current_mode, context)
        results[fault] = result
        dtos.append(dto)
        context.report(done + 1, len(faults), f"solve {fault}")
    return TaskOutcome(dto=FaultResultsDTO(results=dtos), results=results)


def _impedance_table(table: Dict[float, Any]) -> Dict[float, complex]:
    """A sweep point's impedance table as ``complex`` values."""
    return {
        float(f): (v.to_complex() if isinstance(v, ComplexDTO) else complex(v))
        for f, v in table.items()
    }


def _study(
    name: str,
    request: Any,
    context: TaskContext,
    run: Callable[[], Any],
    convert: Callable[[Any], Dict[str, Any]],
) -> TaskOutcome:
    """
    Run a library study with the job's hooks and convert its result.

    Parameters
    ----------
    name : str
        Task name, written into the DTO.
    request : job request DTO
        The request (for the fault).
    context : TaskContext
        Revision and hooks.
    run : callable
        Calls the library study.
    convert : callable
        Turns the study's result into ``tables``/``values``/``failures``.

    Returns
    -------
    TaskOutcome
        A :class:`StudyResultDTO` with the library warnings of the study.
    """
    started = time.perf_counter()
    with collecting() as records:
        study = run()
    parts = convert(study)
    dto = StudyResultDTO(
        study=name,
        fault=getattr(request, "fault", None),
        revision=context.revision,
        tables=parts.get("tables", {}),
        values=parts.get("values", {}),
        failures=parts.get("failures", {}),
        diagnostics=[diagnostic_dto(d) for d in unique(records)],
        groundinsight_version=gi.__version__,
        duration_s=time.perf_counter() - started,
    )
    return TaskOutcome(dto=dto)


def _sweep(network: gi.Network, request: SweepRequestDTO, context: TaskContext) -> TaskOutcome:
    """Task ``"sweep"``: :func:`groundinsight.run_sweep`."""
    points = [
        gi.SweepPoint(
            label=point.label,
            bus_rho=dict(point.bus_rho),
            bus_impedance={bus: _impedance_table(t) for bus, t in point.bus_impedance.items()},
            fault=point.fault,
            fault_scalings=dict(point.fault_scalings) if point.fault_scalings is not None else None,
            parameters=dict(point.parameters),
        )
        for point in request.points
    ]

    def run():
        return gi.run_sweep(
            network,
            fault=request.fault,
            points=points,
            phase_current_mode=request.phase_current_mode,
            collect_branches=request.collect_branches,
            progress=context.progress,
            should_cancel=context.should_cancel,
        )

    def convert(study) -> Dict[str, Any]:
        tables = {
            "buses": table_dto("buses", study.buses()),
            "impedances": table_dto("impedances", study.impedances()),
        }
        if request.collect_branches:
            tables["branches"] = table_dto("branches", study.branches())
        return {
            "tables": tables,
            "values": {"points": len(study.labels), "failed": len(study.failures)},
            "failures": dict(study.failures),
        }

    return _study("sweep", request, context, run, convert)


def _outage(network: gi.Network, request: OutageStudyRequestDTO, context: TaskContext) -> TaskOutcome:
    """Task ``"outage"``: :func:`groundinsight.run_outage_study`."""
    scenarios = [
        gi.Outage(
            name=s.name,
            disabled_buses=list(s.disabled_buses),
            disabled_branches=list(s.disabled_branches),
            description=s.description,
        )
        for s in request.scenarios
    ]

    def run():
        return gi.run_outage_study(
            network,
            fault=request.fault,
            scenarios=scenarios,
            include_base=request.include_base,
            phase_current_mode=request.phase_current_mode,
            progress=context.progress,
            should_cancel=context.should_cancel,
        )

    def convert(study) -> Dict[str, Any]:
        return {
            "tables": {
                "buses": table_dto("buses", study.compare_buses()),
                "branches": table_dto("branches", study.compare_branches()),
            },
            "values": {"scenarios": len(study.labels())},
        }

    return _study("outage", request, context, run, convert)


def _max_rho(network: gi.Network, request: MaxRhoRequestDTO, context: TaskContext) -> TaskOutcome:
    """Task ``"max_rho"``: :func:`groundinsight.find_max_rho_scaling`."""

    def run():
        return gi.find_max_rho_scaling(
            network,
            request.fault,
            list(request.bus_names),
            request.u_limit,
            c_bounds=tuple(request.c_bounds),
            tol_rel=request.tol_rel,
            max_iter=request.max_iter,
            monotonicity_grid=request.monotonicity_grid,
            run_fault_kwargs={"phase_current_mode": request.phase_current_mode},
            progress=context.progress,
            should_cancel=context.should_cancel,
        )

    def convert(report: Dict[str, Any]) -> Dict[str, Any]:
        low, high = report.get("c_bracket", (None, None))
        rho_max = report.get("rho_max") or {}
        values = {
            key: report.get(key)
            for key in ("c_max", "u_epr_rms_at_c_max", "iterations", "converged", "status",
                        "bracket_rel_width")
        }
        values.update({"c_bracket_low": low, "c_bracket_high": high})
        frame = pl.DataFrame(
            {"bus_name": list(rho_max), "rho_max": [float(v) for v in rho_max.values()]},
            schema={"bus_name": pl.Utf8, "rho_max": pl.Float64},
        )
        return {"tables": {"rho_max": table_dto("rho_max", frame)}, "values": values}

    return _study("max_rho", request, context, run, convert)


#: Task name (the request's ``task``) to the function that computes it.
TASKS = {
    "solve": _solve,
    "solve_all": _solve_all,
    "sweep": _sweep,
    "outage": _outage,
    "max_rho": _max_rho,
}


def run_task(
    request: Any,
    network: gi.Network,
    *,
    revision: Optional[int] = None,
    progress: Optional[Callable[[int, int, str], None]] = None,
    should_cancel: Optional[Callable[[], bool]] = None,
) -> TaskOutcome:
    """
    Compute a job request in the calling thread.

    Parameters
    ----------
    request : job request DTO
        What to compute; its ``task`` selects the function of :data:`TASKS`.
    network : Network
        The network to compute on. Solves leave it unchanged apart from the
        stored results; studies restore it. Pass a copy if the caller keeps
        using the network concurrently.
    revision : int, optional
        Session revision, written into the result DTOs.
    progress, should_cancel : callable, optional
        The library's study hooks.

    Returns
    -------
    TaskOutcome
        The DTO and the fault results.

    Raises
    ------
    groundinsight.StudyCancelled
        If ``should_cancel()`` returned true.
    ValueError
        If the library rejects the input.
    """
    task = TASKS[request.task]
    context = TaskContext(revision=revision, progress=progress, should_cancel=should_cancel)
    return task(network, request, context)

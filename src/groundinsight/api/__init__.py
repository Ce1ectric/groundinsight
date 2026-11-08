# api/__init__.py
"""
The service layer: what a front end imports.

``groundinsight.api`` sits between the library and every user interface -- the
NiceGUI app of the ``groundinsight-gui`` distribution, the ``groundinsight``
command line, a later REST server. It adds what a front end needs and a
notebook does not, and nothing else:

* :class:`ProjectSession` -- one open project: open/save through the
  library's project file (or a :class:`ProjectStore`), edit commands with
  undo/redo and revisions, the type catalogue, resource limits, results whose
  staleness is the library's, jobs;
* the edit commands (:mod:`~groundinsight.api.commands`) -- atomic, undoable,
  through the library's editing API;
* :class:`JobRunner` -- solves and studies off the caller's thread, in a
  thread or a process, with progress, cancel and a runtime limit;
* the DTOs (:mod:`~groundinsight.api.schemas`) -- the only data shape towards a
  front end: strictly JSON-safe, versioned, with JSON schemas, so they are the
  request and response schemas of a future REST API;
* :data:`EXAMPLES` -- the documented example networks as templates.

Design rules (the service-layer tests hold them): no UI toolkit is imported
here, the package keeps no module state (multi-user), every number comes
from the library's public accessors and is only selected and converted, and
every edit goes through the library. The names are exported here and not from
the top-level ``groundinsight`` namespace, which stays the library's.
"""

from .commands import (
    COMMAND_OPS,
    AddBranch,
    AddBranchType,
    AddBus,
    AddBusType,
    AddFault,
    AddSource,
    Command,
    CommandError,
    RemoveBranch,
    RemoveBranchType,
    RemoveBus,
    RemoveBusType,
    RemoveFault,
    RemoveSource,
    SetFrequencies,
    UpdateBranch,
    UpdateBranchType,
    UpdateBus,
    UpdateBusType,
    UpdateFault,
    UpdateSource,
    parse_command,
)
from .examples import EXAMPLES
from .jobs import JobCancelled, JobFailed, JobHandle, JobRunner, JobTimeout, snapshot_network
from .schemas import (
    DTO_SCHEMA_VERSION,
    CheckReportDTO,
    ColumnDTO,
    CommandResultDTO,
    ComplexDTO,
    DiagnosticDTO,
    ElementRefDTO,
    FaultResultDTO,
    FaultResultsDTO,
    JobErrorDTO,
    JobStatusDTO,
    KeyFigureRowDTO,
    KeyFiguresDTO,
    MaxRhoRequestDTO,
    NetworkSummaryDTO,
    NetworkTablesDTO,
    OutageScenarioDTO,
    OutageStudyRequestDTO,
    ProgressDTO,
    SessionLimitsDTO,
    SolveAllRequestDTO,
    SolveRequestDTO,
    StudyResultDTO,
    SweepPointDTO,
    SweepRequestDTO,
    TableDTO,
    finite_or_none,
    json_schemas,
    parse_job_request,
)
from .session import DatabaseStore, MemoryStore, ProjectError, ProjectSession, ProjectStore

__all__ = [
    # sessions and stores
    "ProjectSession",
    "ProjectError",
    "ProjectStore",
    "MemoryStore",
    "DatabaseStore",
    # commands
    "Command",
    "CommandError",
    "COMMAND_OPS",
    "parse_command",
    "AddBus",
    "UpdateBus",
    "RemoveBus",
    "AddBranch",
    "UpdateBranch",
    "RemoveBranch",
    "AddSource",
    "UpdateSource",
    "RemoveSource",
    "AddFault",
    "UpdateFault",
    "RemoveFault",
    "AddBusType",
    "UpdateBusType",
    "RemoveBusType",
    "AddBranchType",
    "UpdateBranchType",
    "RemoveBranchType",
    "SetFrequencies",
    # jobs
    "JobRunner",
    "JobHandle",
    "JobCancelled",
    "JobTimeout",
    "JobFailed",
    "snapshot_network",
    # DTOs
    "DTO_SCHEMA_VERSION",
    "json_schemas",
    "parse_job_request",
    "finite_or_none",
    "ElementRefDTO",
    "DiagnosticDTO",
    "CheckReportDTO",
    "ColumnDTO",
    "TableDTO",
    "NetworkTablesDTO",
    "SessionLimitsDTO",
    "NetworkSummaryDTO",
    "CommandResultDTO",
    "KeyFigureRowDTO",
    "KeyFiguresDTO",
    "FaultResultDTO",
    "FaultResultsDTO",
    "StudyResultDTO",
    "ProgressDTO",
    "JobErrorDTO",
    "JobStatusDTO",
    "ComplexDTO",
    "SolveRequestDTO",
    "SolveAllRequestDTO",
    "SweepPointDTO",
    "SweepRequestDTO",
    "OutageScenarioDTO",
    "OutageStudyRequestDTO",
    "MaxRhoRequestDTO",
    # templates
    "EXAMPLES",
]

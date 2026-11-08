# cli.py
"""
The ``groundinsight`` command line: solve and check a project file.

::

    groundinsight run projekt.json --fault F1 [--format table|json|csv]
    groundinsight check projekt.json [--fault F1] [--format table|json|csv]
    python -m groundinsight ...            # the same, without the script

Why a command line, and why first: it is the smallest front end that needs
everything a GUI needs -- open a project, check it, solve a fault, report the
key figures and the diagnostics -- so it is the first consumer of the service
layer :mod:`groundinsight.api` and proves its shape before a window does. It is
also what a batch job, a CI pipeline or a colleague without Python
experience runs.

It is built on the service layer only (the same rule the GUI follows): a
:class:`~groundinsight.api.ProjectSession` opens the file, ``check`` prints
the pre-flight check, ``run`` checks, solves through the session and prints
the :class:`~groundinsight.api.FaultResultDTO`. No number is computed here;
the table and CSV outputs only format the DTO's key figures, which are the
rows of :meth:`Network.res_fault <groundinsight.Network.res_fault>`.

Output formats
--------------
``table`` (default)
    A readable report: the key figures per frequency and RMS, then every
    diagnostic with its elements.
``json``
    The DTO as JSON (strict RFC 8259: no ``NaN``/``Infinity``) --
    :class:`~groundinsight.api.FaultResultDTO` for ``run``,
    :class:`~groundinsight.api.CheckReportDTO` for ``check`` and for a ``run``
    the pre-flight check stopped.
``csv``
    ``run``: the key-figure rows with the columns of
    :data:`groundinsight.RES_FAULT_SCHEMA`; ``check``: one row per finding.
    Diagnostics of a ``run`` go to stderr, so stdout stays pure CSV.

Exit codes
----------
0
    Success.
1
    A diagnostic of severity ``error``: the pre-flight check found the network
    unsolvable as described (``run`` then solves nothing), the solve itself
    failed, or the result carries an error record.
2
    A usage error: invalid arguments, a project file that cannot be opened,
    an unknown fault, or ``run`` without ``--fault`` on a network with
    several faults.
"""

import argparse
import csv
import io
import logging
import sys
import warnings
from typing import Any, List, Optional, Sequence, TextIO

from groundinsight import GroundinsightWarning, __version__
from groundinsight.api import (
    CheckReportDTO,
    FaultResultDTO,
    KeyFigureRowDTO,
    ProjectError,
    ProjectSession,
)

logger = logging.getLogger(__name__)

__all__ = ["EXIT_ERRORS", "EXIT_OK", "EXIT_USAGE", "main"]

EXIT_OK = 0
"""Exit code: success."""

EXIT_ERRORS = 1
"""Exit code: a diagnostic of severity ``error``."""

EXIT_USAGE = 2
"""Exit code: a usage error (arguments, project file, fault name)."""

#: The key-figure columns, in the order of ``RES_FAULT_SCHEMA``.
KEY_FIGURE_COLUMNS = [name for name in KeyFigureRowDTO.model_fields if name != "dto_schema_version"]


class _UsageError(Exception):
    """A usage error found after argument parsing (exit code 2)."""


def _write(stream: TextIO, text: str) -> None:
    """
    Write text, degrading characters the stream cannot encode.

    A console with a legacy code page cannot print every element name; the
    report is still printed, with ``?`` for such characters.

    Parameters
    ----------
    stream : TextIO
        ``sys.stdout`` or ``sys.stderr``.
    text : str
        The text.
    """
    try:
        stream.write(text)
    except UnicodeEncodeError:
        encoding = getattr(stream, "encoding", None) or "ascii"
        stream.write(text.encode(encoding, errors="replace").decode(encoding))


def _number(value: Any) -> str:
    """
    Format one number of a report: four significant digits, ``-`` for none.

    Parameters
    ----------
    value : Any
        A number or ``None``.

    Returns
    -------
    str
        The formatted value.
    """
    if value is None:
        return "-"
    if isinstance(value, bool):
        return "yes" if value else "no"
    if isinstance(value, (int, float)):
        return f"{value:.4g}"
    return str(value)


def _columns(rows: List[List[str]]) -> str:
    """
    Lay out rows of text as right-aligned columns.

    Parameters
    ----------
    rows : list of list of str
        The header row first.

    Returns
    -------
    str
        The table, one line per row.
    """
    widths = [max(len(row[i]) for row in rows) for i in range(len(rows[0]))]
    lines = []
    for index, row in enumerate(rows):
        lines.append("  ".join(cell.rjust(width) for cell, width in zip(row, widths, strict=True)))
        if index == 0:
            lines.append("  ".join("-" * width for width in widths))
    return "\n".join(lines) + "\n"


def _diagnostics_text(diagnostics: Sequence[Any]) -> str:
    """
    List diagnostics as ``[severity] CODE (elements): message`` lines.

    Parameters
    ----------
    diagnostics : sequence of DiagnosticDTO
        The records.

    Returns
    -------
    str
        One line per record, or a line saying there are none.
    """
    if not diagnostics:
        return "Diagnostics: none\n"
    lines = [f"Diagnostics ({len(diagnostics)}):"]
    for record in diagnostics:
        elements = ", ".join(f"{e.kind} {e.name}" for e in record.elements)
        where = f" ({elements})" if elements else ""
        lines.append(f"  [{record.severity}] {record.code}{where}: {record.message}")
    return "\n".join(lines) + "\n"


def _result_table(dto: FaultResultDTO, network: str) -> str:
    """
    The readable report of one fault result.

    Parameters
    ----------
    dto : FaultResultDTO
        The result.
    network : str
        Network name, for the title.

    Returns
    -------
    str
        Title, key-figure table, diagnostics.
    """
    header = ["f [Hz]", "I_F [A]", "EPR fault [V]", "EPR max [V]", "at bus", "r_epr",
              "r_current", "I_E [A]", "U_E [V]", "Z_E [Ohm]"]
    rows = [header]
    for row in dto.key_figures.rows:
        rows.append([
            "RMS" if row.is_rms else _number(row.frequency_Hz),
            _number(row.I_F_A),
            _number(row.EPR_fault_V),
            _number(row.EPR_max_V),
            row.EPR_max_bus or "-",
            _number(row.r_epr),
            _number(row.r_current),
            _number(row.I_E_A),
            _number(row.U_E_V),
            _number(row.Z_E_Ohm),
        ])
    title = (
        f"groundinsight {dto.groundinsight_version} -- network '{network}', "
        f"fault '{dto.fault}' at bus '{dto.fault_bus}' "
        f"(phase currents: {dto.phase_current_mode})\n"
        f"input digest {dto.input_digest}\n\n"
    )
    return title + _columns(rows) + "\n" + _diagnostics_text(dto.diagnostics)


def _csv_cell(value: Any) -> str:
    """
    One CSV cell: ``true``/``false``, empty for ``None``, ``repr`` for floats.

    Parameters
    ----------
    value : Any
        The value.

    Returns
    -------
    str
        The cell text; a float round-trips exactly.
    """
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, float):
        return repr(value)
    return str(value)


def _result_csv(dto: FaultResultDTO) -> str:
    """
    The key-figure rows as CSV with the columns of ``RES_FAULT_SCHEMA``.

    Parameters
    ----------
    dto : FaultResultDTO
        The result.

    Returns
    -------
    str
        Header line and one line per row.
    """
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\n")
    writer.writerow(KEY_FIGURE_COLUMNS)
    for row in dto.key_figures.rows:
        data = row.model_dump()
        writer.writerow([_csv_cell(data[column]) for column in KEY_FIGURE_COLUMNS])
    return buffer.getvalue()


def _check_text(report: CheckReportDTO, network: str) -> str:
    """
    The readable report of a pre-flight check.

    Parameters
    ----------
    report : CheckReportDTO
        The check.
    network : str
        Network name, for the title.

    Returns
    -------
    str
        Verdict line and the findings.
    """
    verdict = "ready to solve" if report.ok else "NOT solvable as described"
    scope = f", fault '{report.fault}'" if report.fault else ""
    head = (
        f"Check of network '{network}'{scope}: {verdict} "
        f"({report.n_errors} error(s), {report.n_warnings} warning(s))\n"
    )
    return head + _diagnostics_text(report.diagnostics)


def _check_csv(report: CheckReportDTO) -> str:
    """
    The findings of a check as CSV: severity, code, elements, message.

    Parameters
    ----------
    report : CheckReportDTO
        The check.

    Returns
    -------
    str
        Header line and one line per finding.
    """
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\n")
    writer.writerow(["severity", "code", "elements", "message"])
    for record in report.diagnostics:
        elements = ";".join(f"{e.kind}:{e.name}" for e in record.elements)
        writer.writerow([record.severity, record.code, elements, record.message])
    return buffer.getvalue()


def _open(path: str) -> ProjectSession:
    """
    Open the project file, turning a failure into a usage error.

    Parameters
    ----------
    path : str
        The project file.

    Returns
    -------
    ProjectSession
        The session.

    Raises
    ------
    _UsageError
        If the file cannot be opened as a project.
    """
    try:
        return ProjectSession.from_file(path)
    except ProjectError as exc:
        raise _UsageError(str(exc)) from exc


def _fault(session: ProjectSession, requested: Optional[str], required: bool) -> Optional[str]:
    """
    Resolve the ``--fault`` option against the project.

    Parameters
    ----------
    session : ProjectSession
        The open project.
    requested : str, optional
        The option's value.
    required : bool
        Whether a fault must be determined (``run``). Without the option the
        project's only fault is taken.

    Returns
    -------
    str or None
        The fault name, or ``None`` for an optional, absent option.

    Raises
    ------
    _UsageError
        If the fault is unknown, or required but ambiguous or absent.
    """
    faults = session.fault_names()
    if requested is not None:
        if requested not in faults:
            raise _UsageError(
                f"fault '{requested}' is not in the project (faults: {', '.join(faults) or 'none'})"
            )
        return requested
    if not required:
        return None
    if len(faults) == 1:
        return faults[0]
    raise _UsageError(
        "the project has "
        + (f"{len(faults)} faults ({', '.join(faults)}); choose one with --fault" if faults else "no fault")
    )


def _run(args: argparse.Namespace) -> int:
    """
    The ``run`` command.

    Parameters
    ----------
    args : argparse.Namespace
        Parsed arguments.

    Returns
    -------
    int
        Exit code.
    """
    with _open(args.project) as session:
        fault = _fault(session, args.fault, required=True)
        report = session.check(fault=fault)
        network = session.summary().name
        if not report.ok:
            if args.format == "json":
                _write(sys.stdout, report.model_dump_json(indent=2) + "\n")
            else:
                _write(sys.stdout if args.format == "table" else sys.stderr, _check_text(report, network))
            return EXIT_ERRORS
        try:
            dto = session.solve(fault, phase_current_mode=args.phase_current_mode)
        except ValueError as exc:
            _write(sys.stderr, f"groundinsight: the solve of '{fault}' failed: {exc}\n")
            return EXIT_ERRORS
    if args.format == "json":
        _write(sys.stdout, dto.model_dump_json(indent=2) + "\n")
    elif args.format == "csv":
        _write(sys.stdout, _result_csv(dto))
        _write(sys.stderr, _diagnostics_text(dto.diagnostics))
    else:
        _write(sys.stdout, _result_table(dto, network))
    return EXIT_ERRORS if any(d.severity == "error" for d in dto.diagnostics) else EXIT_OK


def _check(args: argparse.Namespace) -> int:
    """
    The ``check`` command.

    Parameters
    ----------
    args : argparse.Namespace
        Parsed arguments.

    Returns
    -------
    int
        Exit code.
    """
    with _open(args.project) as session:
        fault = _fault(session, args.fault, required=False)
        report = session.check(fault=fault)
        network = session.summary().name
    if args.format == "json":
        _write(sys.stdout, report.model_dump_json(indent=2) + "\n")
    elif args.format == "csv":
        _write(sys.stdout, _check_csv(report))
    else:
        _write(sys.stdout, _check_text(report, network))
    return EXIT_OK if report.ok else EXIT_ERRORS


def _parser() -> argparse.ArgumentParser:
    """
    The argument parser.

    Returns
    -------
    argparse.ArgumentParser
        With the subcommands ``run`` and ``check``.
    """
    parser = argparse.ArgumentParser(
        prog="groundinsight",
        description="Solve and check groundinsight project files.",
        epilog="Exit codes: 0 ok, 1 a diagnostic of severity error, 2 usage error.",
    )
    parser.add_argument("--version", action="version", version=f"groundinsight {__version__}")
    commands = parser.add_subparsers(dest="command", metavar="COMMAND", required=True)

    run = commands.add_parser("run", help="check, solve one fault and report its key figures")
    run.add_argument("project", help="project file (JSON, as saved by groundinsight)")
    run.add_argument("--fault", help="the fault to solve (default: the project's only fault)")
    run.add_argument("--format", choices=("table", "json", "csv"), default="table")
    run.add_argument(
        "--phase-current-mode",
        choices=("auto", "paths"),
        default="auto",
        help="how the phase currents are determined (default: auto)",
    )
    run.set_defaults(handler=_run)

    check = commands.add_parser("check", help="list every problem of the network")
    check.add_argument("project", help="project file (JSON, as saved by groundinsight)")
    check.add_argument("--fault", help="restrict the fault checks to this fault")
    check.add_argument("--format", choices=("table", "json", "csv"), default="table")
    check.set_defaults(handler=_check)
    return parser


def main(argv: Optional[List[str]] = None) -> int:
    """
    Run the command line.

    Parameters
    ----------
    argv : list of str, optional
        The arguments without the program name; default ``sys.argv[1:]``.

    Returns
    -------
    int
        The exit code (see the module docstring). Invalid arguments make
        ``argparse`` exit with code 2 itself.

    Examples
    --------
    >>> main(["--version"])  # doctest: +SKIP
    groundinsight 2026.11.0
    """
    args = _parser().parse_args(argv)
    # The diagnostics are part of every report (the library records them on
    # the result whatever the warning filters say); printing each warning a
    # second time on stderr would only repeat them.
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", GroundinsightWarning)
        try:
            return args.handler(args)
        except _UsageError as exc:
            _write(sys.stderr, f"groundinsight: error: {exc}\n")
            return EXIT_USAGE

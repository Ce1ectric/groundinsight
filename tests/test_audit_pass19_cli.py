# tests/test_audit_pass19_cli.py
"""
The command-line interface, first consumer of the service layer (audit pass
19, GUI phase 0).

``python -m groundinsight`` and the console script ``groundinsight`` solve and
check a project file without a line of Python::

    groundinsight run projekt.json --fault F1 --format json
    groundinsight check projekt.json

It is built on ``groundinsight.api`` only -- a ``ProjectSession`` opens the
file, ``check`` runs the pre-flight check, ``run`` solves through the session
and prints the DTO -- so every number it prints is the service layer's, and
the service layer's numbers are checked against the library elsewhere. Here
they are checked again on their way through the CLI: the JSON output against
the library's ``res_fault`` frame of the same file solved with ``run_fault``,
the CSV output against the same frame column by column.

Exit codes: 0 ok, 1 a diagnostic of severity ``error`` (the network cannot be
solved as described, or the result carries such a record), 2 a usage error
(bad arguments, an unreadable project, an unknown fault).
"""

from __future__ import annotations

import ast
import csv
import io
import json
import math
import os
import pathlib
import subprocess
import sys
import tomllib
import warnings

import pytest

import groundinsight as gi
from groundinsight.models.core_models import RES_FAULT_SCHEMA

ROOT = pathlib.Path(__file__).resolve().parents[1]
CLI_FILE = ROOT / "src" / "groundinsight" / "cli.py"


def _cli():
    from groundinsight import cli

    return cli


def _reject_constant(token):
    raise ValueError(f"non-RFC-8259 token {token}")


def _example(name: str) -> gi.Network:
    from groundinsight.api import EXAMPLES

    return EXAMPLES[name]()


@pytest.fixture
def quickstart_file(tmp_path):
    path = tmp_path / "quickstart.json"
    gi.save_network_to_json(_example("quickstart"), path)
    return path


@pytest.fixture
def ring_file(tmp_path):
    path = tmp_path / "ring.json"
    net = _example("mv_ring")
    gi.create_fault(name="F2", bus="b02", network=net)
    gi.save_network_to_json(net, path)
    return path


def _run_module(*args: str) -> subprocess.CompletedProcess:
    """``python -m groundinsight ...`` with this checkout first on the path."""
    env = dict(os.environ)
    env["PYTHONPATH"] = os.pathsep.join(filter(None, [str(ROOT / "src"), env.get("PYTHONPATH")]))
    env["PYTHONIOENCODING"] = "utf-8"
    return subprocess.run(
        [sys.executable, "-m", "groundinsight", *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        env=env,
        timeout=300,
    )


def _library_rows(path: pathlib.Path, fault: str):
    """The key figures of the file solved with ``run_fault``: the second way."""
    net = gi.load_network_from_json(path)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        gi.run_fault(net, fault)
    rows = []
    for row in net.res_fault(fault).to_dicts():
        rows.append({k: (None if isinstance(v, float) and not math.isfinite(v) else v) for k, v in row.items()})
    return rows


# ---------------------------------------------------------------------------
# run
# ---------------------------------------------------------------------------


def test_run_json_prints_the_fault_result_of_the_library(quickstart_file):
    from groundinsight.api import FaultResultDTO

    completed = _run_module("run", str(quickstart_file), "--fault", "fault_at_remote_bus",
                            "--format", "json")
    assert completed.returncode == 0, completed.stderr
    payload = json.loads(completed.stdout, parse_constant=_reject_constant)
    dto = FaultResultDTO.model_validate(payload)
    rows = [{k: v for k, v in r.model_dump().items() if k != "dto_schema_version"}
            for r in dto.key_figures.rows]
    assert rows == _library_rows(quickstart_file, "fault_at_remote_bus")
    assert "SCALINGS_INCOMPLETE" in {d.code for d in dto.diagnostics}


def test_run_csv_has_the_res_fault_columns_and_values(capsys, quickstart_file):
    cli = _cli()
    assert cli.main(["run", str(quickstart_file), "--fault", "fault_at_remote_bus",
                     "--format", "csv"]) == 0
    out = capsys.readouterr().out
    reader = csv.DictReader(io.StringIO(out))
    assert reader.fieldnames == list(RES_FAULT_SCHEMA)
    parsed = list(reader)
    library = _library_rows(quickstart_file, "fault_at_remote_bus")
    assert len(parsed) == len(library) == 6
    for text_row, row in zip(parsed, library):
        for column, value in row.items():
            if isinstance(value, bool):
                assert text_row[column] == ("true" if value else "false"), column
            elif isinstance(value, (int, float)):
                assert float(text_row[column]) == value, column  # repr round-trips
            elif value is None:
                assert text_row[column] == "", column
            else:
                assert text_row[column] == value, column


def test_run_table_is_a_readable_report(capsys, quickstart_file):
    cli = _cli()
    assert cli.main(["run", str(quickstart_file), "--fault", "fault_at_remote_bus"]) == 0
    out = capsys.readouterr().out
    assert "fault_at_remote_bus" in out and "bus_fault" in out
    library = _library_rows(quickstart_file, "fault_at_remote_bus")
    rms = next(r for r in library if r["is_rms"])
    assert f"{rms['EPR_fault_V']:.4g}" in out
    assert "SCALINGS_INCOMPLETE" in out and "RMS" in out


def test_run_passes_the_phase_current_mode(capsys, ring_file):
    from groundinsight.api import FaultResultDTO

    cli = _cli()
    assert cli.main(["run", str(ring_file), "--fault", "F1", "--format", "json",
                     "--phase-current-mode", "paths"]) == 0
    dto = FaultResultDTO.model_validate_json(capsys.readouterr().out)
    assert dto.phase_current_mode == "paths"


def test_a_single_fault_needs_no_fault_option(capsys, quickstart_file):
    cli = _cli()
    assert cli.main(["run", str(quickstart_file), "--format", "json"]) == 0
    assert json.loads(capsys.readouterr().out)["fault"] == "fault_at_remote_bus"


def test_run_refuses_a_network_with_errors_and_exits_1(tmp_path, capsys):
    """A network without a source: the pre-flight check says why, nothing is solved."""
    net = _example("quickstart")
    net.remove_source("substation_infeed")
    path = tmp_path / "no_source.json"
    gi.save_network_to_json(net, path)
    cli = _cli()
    assert cli.main(["run", str(path), "--fault", "fault_at_remote_bus", "--format", "json"]) == 1
    report = json.loads(capsys.readouterr().out)
    assert report["ok"] is False and "NO_SOURCE" in {d["code"] for d in report["diagnostics"]}


def test_a_result_diagnostic_of_severity_error_exits_1(capsys, monkeypatch, quickstart_file):
    from groundinsight import api

    real_solve = api.ProjectSession.solve

    def solve_with_an_error(self, fault, **kwargs):
        dto = real_solve(self, fault, **kwargs)
        error = api.DiagnosticDTO(code="SOMETHING_BROKE", severity="error", category="InputWarning",
                                  message="simulated", elements=[])
        return dto.model_copy(update={"diagnostics": [*dto.diagnostics, error]})

    monkeypatch.setattr(api.ProjectSession, "solve", solve_with_an_error)
    cli = _cli()
    assert cli.main(["run", str(quickstart_file), "--fault", "fault_at_remote_bus"]) == 1
    assert "SOMETHING_BROKE" in capsys.readouterr().out


# ---------------------------------------------------------------------------
# check
# ---------------------------------------------------------------------------


def test_check_exits_0_for_a_sound_network(capsys, ring_file):
    cli = _cli()
    assert cli.main(["check", str(ring_file), "--format", "json"]) == 0
    report = json.loads(capsys.readouterr().out)
    library = gi.check_network(gi.load_network_from_json(ring_file))
    assert report["ok"] is True
    assert [d["code"] for d in report["diagnostics"]] == [d.code for d in library]


def test_check_exits_1_and_lists_every_error(tmp_path, capsys):
    net = _example("quickstart")
    net.remove_source("substation_infeed")
    gi.create_bus(name="lonely", type=net.buses["bus_fault"].type, network=net)
    path = tmp_path / "broken.json"
    gi.save_network_to_json(net, path)
    cli = _cli()
    assert cli.main(["check", str(path)]) == 1
    out = capsys.readouterr().out
    assert "NO_SOURCE" in out and "ISOLATED_BUS" in out and "lonely" in out


# ---------------------------------------------------------------------------
# usage errors
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "argv",
    [
        [],
        ["frobnicate"],
        ["run"],
        ["run", "{missing}"],
        ["run", "{ring}", "--format", "xml"],
        ["run", "{ring}"],  # two faults and no --fault
        ["run", "{ring}", "--fault", "nope"],
        ["check", "{garbage}"],
    ],
)
def test_usage_errors_exit_2_with_a_message(argv, tmp_path, ring_file, capsys):
    garbage = tmp_path / "garbage.json"
    garbage.write_text('{"name": 1', encoding="utf-8")
    replacements = {"{missing}": str(tmp_path / "missing.json"), "{ring}": str(ring_file),
                    "{garbage}": str(garbage)}
    argv = [replacements.get(a, a) for a in argv]
    cli = _cli()
    with pytest.raises(SystemExit) as caught:
        code = cli.main(argv)
        raise SystemExit(code)
    assert caught.value.code == 2
    err = capsys.readouterr().err
    assert err.strip(), "a usage error explains itself on stderr"


def test_the_module_runs_the_cli_and_reports_its_version():
    completed = _run_module("--version")
    assert completed.returncode == 0
    assert gi.__version__ in completed.stdout
    completed = _run_module()
    assert completed.returncode == 2 and "usage" in completed.stderr.lower()


# ---------------------------------------------------------------------------
# packaging and architecture
# ---------------------------------------------------------------------------


def test_the_console_script_is_declared():
    data = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    assert data["tool"]["poetry"]["scripts"] == {"groundinsight": "groundinsight.cli:main"}


def test_the_cli_is_built_on_the_service_layer_only():
    """Like the GUI: ``groundinsight`` (for the version) and ``groundinsight.api``."""
    tree = ast.parse(CLI_FILE.read_text(encoding="utf-8"))
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            imported.add(node.module)
        elif isinstance(node, ast.ImportFrom) and node.level > 0:
            imported.add("." * node.level + (node.module or ""))
    ours = {name for name in imported if name.startswith(("groundinsight", "."))}
    assert ours <= {"groundinsight", "groundinsight.api", ".api"}, ours
    assert ours & {"groundinsight.api", ".api"}

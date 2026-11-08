# tests/test_audit_pass19_import_time.py

"""
Audit pass 19 -- ``import groundinsight`` must not import ``matplotlib.pyplot``
(GUI8).

``plotting.py`` imported ``matplotlib.pyplot`` at module level, so every
``import groundinsight`` paid for it: about a third of the import time
(measured 0.36 s of 1.1 s), plus pyplot's global state -- backend selection,
the figure registry -- in every process, including a multi-threaded GUI or
server that never draws with matplotlib. pyplot is now imported on the first
call of a plotting helper.

The checks run in fresh interpreters, because the test process itself has
long imported pyplot through other test modules. A static check over every
module keeps a new top-level matplotlib import from creeping back in.
"""

import ast
import json
import os
import pathlib
import subprocess
import sys

import groundinsight as gi

SRC = pathlib.Path(gi.__file__).resolve().parent


def _python(code: str) -> subprocess.CompletedProcess:
    """
    Run ``code`` in a fresh interpreter that imports this very checkout.

    Parameters
    ----------
    code : str
        Source passed to ``python -c``.

    Returns
    -------
    subprocess.CompletedProcess
        The finished process with captured text output.
    """
    env = dict(os.environ)
    env["PYTHONPATH"] = os.pathsep.join(
        part for part in (str(SRC.parent), env.get("PYTHONPATH", "")) if part
    )
    env["PYTHONIOENCODING"] = "utf-8"
    env["MPLBACKEND"] = "Agg"
    return subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        encoding="utf-8",
        env=env,
        timeout=300,
    )


def test_import_does_not_load_pyplot():
    proc = _python(
        "import sys, groundinsight\n"
        "print('matplotlib.pyplot' in sys.modules)\n"
    )
    assert proc.returncode == 0, proc.stderr[-2000:]
    assert proc.stdout.strip() == "False"


def test_plotting_still_works_after_the_lazy_import():
    """First plot call loads pyplot; the bars are the solved EPR values."""
    code = r"""
import json, sys
import groundinsight as gi

before = "matplotlib.pyplot" in sys.modules
bus_type = gi.BusType(name="bt", system_type="Substation", voltage_level=20.0,
                      impedance_formula="rho*0.01 + 1 + I*f*0.002")
branch_type = gi.BranchType(name="kt", grounding_conductor=True,
                            self_impedance_formula="(0.25 + I*f*0.012)*l",
                            mutual_impedance_formula="(0.05 + I*f*0.010)*l",
                            phase_impedance_formula="(0.10 + I*f*0.004)*l")
net = gi.create_network(name="n", frequencies=[50.0])
for name in ("A", "B", "C"):
    gi.create_bus(name=name, type=bus_type, network=net)
gi.create_branch(name="AB", type=branch_type, from_bus="A", to_bus="B", length=1.0, network=net)
gi.create_branch(name="BC", type=branch_type, from_bus="B", to_bus="C", length=1.0, network=net)
gi.create_source(name="s", bus="A", values={50.0: 1000.0}, network=net)
gi.create_fault(name="F", bus="C", scalings={50.0: 1.0}, network=net)
result = gi.run_fault(net, "F")

fig = gi.plot_bus_voltages(result, frequencies=[50.0], close=True)
heights = [patch.get_height() for ax in fig.axes for patch in ax.patches]
frame = net.res_buses("F")
expected = [row["EPR_V"] for row in frame.iter_rows(named=True)
            if row["frequency_Hz"] == 50.0]
print(json.dumps({"before": before,
                  "after": "matplotlib.pyplot" in sys.modules,
                  "figure": type(fig).__name__,
                  "heights": sorted(heights),
                  "expected": sorted(expected)}))
"""
    proc = _python(code)
    assert proc.returncode == 0, proc.stderr[-2000:]
    report = json.loads(proc.stdout.strip().splitlines()[-1])
    assert report["before"] is False
    assert report["after"] is True
    assert report["figure"] == "Figure"
    # Second route to the same numbers: the result frame of the solve.
    assert len(report["heights"]) == len(report["expected"]) == 3
    for height, value in zip(report["heights"], report["expected"], strict=True):
        assert abs(height - value) <= 1e-9 * max(1.0, abs(value))


def _top_level_imports(tree: ast.Module):
    """
    Import statements that run when the module is imported.

    Function and class bodies run later; ``if TYPE_CHECKING:`` blocks never
    run. Everything else at module level -- including ``try`` and plain
    ``if`` blocks -- runs on import.

    Parameters
    ----------
    tree : ast.Module
        Parsed module.

    Yields
    ------
    ast.Import or ast.ImportFrom
        The import nodes executed at import time.
    """
    pending = list(tree.body)
    while pending:
        node = pending.pop()
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            yield node
        elif isinstance(node, ast.If):
            test = ast.unparse(node.test)
            if test in ("TYPE_CHECKING", "typing.TYPE_CHECKING"):
                pending.extend(node.orelse)
            else:
                pending.extend(node.body + node.orelse)
        elif isinstance(node, ast.Try):
            pending.extend(node.body + node.orelse + node.finalbody)
            for handler in node.handlers:
                pending.extend(handler.body)
        elif isinstance(node, (ast.With,)):
            pending.extend(node.body)


def test_no_module_imports_matplotlib_at_import_time():
    offenders = []
    for path in sorted(SRC.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in _top_level_imports(tree):
            names = (
                [alias.name for alias in node.names]
                if isinstance(node, ast.Import)
                else [node.module or ""]
            )
            if any(name == "matplotlib" or name.startswith("matplotlib.") for name in names):
                offenders.append(f"{path.relative_to(SRC)}:{node.lineno}")
    assert offenders == []

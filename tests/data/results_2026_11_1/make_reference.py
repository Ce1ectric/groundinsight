# tests/data/results_2026_11_1/make_reference.py
"""
Freeze the results of groundinsight 2026.11.1 as a regression reference.

Run once, on a checkout whose ``src/`` is release 2026.11.1 (8e9443e), before the earthing
characteristic entered the bus model::

    poetry run python tests/data/results_2026_11_1/make_reference.py

It writes ``reference.json`` next to itself: for every example network of
:mod:`groundinsight.api.examples` and the file written by 0.6.0, every fault
in both phase-current modes, the rows of ``res_buses``, ``res_branches``,
``res_fault`` and ``res_all_impedances``, and the derived impedance of
every bus. Floats are written with ``repr``,
which ``json`` reads back bit for bit. ``test_characteristic_regression.py``
compares a network without any characteristic against it, so the
characteristic code path cannot move a single number of a network that does
not use it. Rerunning the script on a later commit would overwrite the
reference with that commit's numbers; do not.
"""

import json
import platform
import subprocess
import warnings
from pathlib import Path

import numpy
import scipy

import groundinsight as gi
from groundinsight.api import examples

HERE = Path(__file__).resolve().parent


def _networks():
    yield "quickstart", examples.build_quickstart
    yield "minimal", examples.build_minimal
    yield "mv_ring", lambda: examples.build_mv_ring(12)
    yield "written_by_0.6.0", lambda: gi.load_network_from_json(HERE.parent / "network_written_by_0.6.0.json")


def main():
    cases = {}
    for name, build in _networks():
        for mode in ("auto", "paths"):
            with warnings.catch_warnings():
                warnings.simplefilter("ignore")
                network = build()
                for fault in sorted(network.faults):
                    gi.run_fault(network, fault, phase_current_mode=mode)
                frames = {
                    "res_buses": network.res_buses(),
                    "res_branches": network.res_branches(),
                    "res_all_impedances": network.res_all_impedances(),
                }
                for fault in sorted(network.faults):
                    frames[f"res_fault[{fault}]"] = network.res_fault(fault)
            case = {key: frame.rows(named=True) for key, frame in frames.items()}
            case["bus_impedance"] = [
                {"bus": bus.name, "frequency_Hz": f, "real": z.real, "imag": z.imag}
                for bus in network.buses.values() for f, z in bus.impedance.items()
            ]
            cases[f"{name}/{mode}"] = case
    commit = subprocess.run(["git", "log", "-1", "--format=%h", "--", ":/src"], capture_output=True, text=True,
                            cwd=HERE).stdout.strip()
    document = {
        "groundinsight_version": gi.__version__,
        "commit": commit,
        "platform": f"{platform.system()}-{platform.machine()}",
        "python": platform.python_version(),
        "numpy": numpy.__version__,
        "scipy": scipy.__version__,
        "cases": cases,
    }
    (HERE / "reference.json").write_text(json.dumps(document, indent=1, allow_nan=True) + "\n",
                                         encoding="utf-8")


if __name__ == "__main__":
    main()

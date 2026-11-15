# tests/test_characteristic_regression.py
"""
A network without an earthing characteristic computes what 2026.11.1 computed.

``tests/data/results_2026_11_1/reference.json`` holds the result tables and
bus impedances of the example networks as release 2026.11.1 computed them
(see ``make_reference.py`` there). The earthing characteristic adds a branch
to the bus derivation and two columns to the result tables; neither may move
a number of a network that does not use it.

On the platform the reference was written on (and the locked numpy/scipy)
the comparison is exact, bit for bit. Elsewhere sparse LU and BLAS may round
differently in the last place, so it allows a relative 1e-12 there.
"""

import json
import math
import platform
import warnings
from pathlib import Path

import numpy
import pytest
import scipy

import groundinsight as gi
from groundinsight.api import examples

DATA = Path(__file__).resolve().parent / "data"
REFERENCE = json.loads((DATA / "results_2026_11_1" / "reference.json").read_text(encoding="utf-8"))

EXACT = (
    REFERENCE["platform"] == f"{platform.system()}-{platform.machine()}"
    and REFERENCE["numpy"] == numpy.__version__
    and REFERENCE["scipy"] == scipy.__version__
)

BUILDERS = {
    "quickstart": examples.build_quickstart,
    "minimal": examples.build_minimal,
    "mv_ring": lambda: examples.build_mv_ring(12),
    "written_by_0.6.0": lambda: gi.load_network_from_json(DATA / "network_written_by_0.6.0.json"),
}


def _same(actual, expected):
    if isinstance(expected, float) and isinstance(actual, float):
        if math.isnan(expected):
            return math.isnan(actual)
        if EXACT:
            return actual == expected
        return actual == pytest.approx(expected, rel=1e-12, abs=1e-300)
    return actual == expected


def _compute(name, mode):
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        network = BUILDERS[name]()
        for fault in sorted(network.faults):
            gi.run_fault(network, fault, phase_current_mode=mode)
        tables = {
            "res_buses": network.res_buses(),
            "res_branches": network.res_branches(),
            "res_all_impedances": network.res_all_impedances(),
        }
        for fault in sorted(network.faults):
            tables[f"res_fault[{fault}]"] = network.res_fault(fault)
    computed = {key: frame.rows(named=True) for key, frame in tables.items()}
    computed["bus_impedance"] = [
        {"bus": bus.name, "frequency_Hz": f, "real": z.real, "imag": z.imag}
        for bus in network.buses.values() for f, z in bus.impedance.items()
    ]
    return network, computed


@pytest.mark.parametrize("case", sorted(REFERENCE["cases"]))
def test_results_without_characteristic_equal_release_2026_11_1(case):
    name, mode = case.split("/")
    network, computed = _compute(name, mode)
    assert all(bus.characteristic is None and bus.type.characteristic is None
               for bus in network.buses.values())
    expected = REFERENCE["cases"][case]
    for table, rows in expected.items():
        got = computed[table]
        assert len(got) == len(rows), table
        for row_got, row_expected in zip(got, rows, strict=True):
            # Columns added after 2026.11.1 are checked by their own tests;
            # every column of 2026.11.1 must be unchanged.
            for column, value in row_expected.items():
                assert _same(row_got[column], value), (table, column, row_got, row_expected)


#: ``Network.input_digest`` of each fault, computed with ``src/`` of release
#: 2026.11.1. The touch factors enter the digest only where a characteristic
#: tabulates them, so a result stored by 2026.11.1 stays current.
DIGESTS_2026_11_1 = {
    "quickstart": {"fault_at_remote_bus": "gi1:e5e280f741a0164fb380c7a5582efd41"},
    "minimal": {"fault": "gi1:503c74d227ec9b91c209f6ffca05a0d2"},
    "mv_ring": {"F1": "gi1:4b95e8f7c4aad79d0bb4f758f6cd81b8"},
    "written_by_0.6.0": {"F Straße": "gi1:69822d5de419bc2b3c1b9c3c6697bf4b"},
}


@pytest.mark.parametrize("name", sorted(DIGESTS_2026_11_1))
def test_input_digests_without_characteristic_equal_release_2026_11_1(name):
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        network = BUILDERS[name]()
    assert {f: network.input_digest(f) for f in sorted(network.faults)} == DIGESTS_2026_11_1[name]

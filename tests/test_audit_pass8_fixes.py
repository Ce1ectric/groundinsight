# tests/test_audit_pass8_fixes.py

"""
Regression tests for the eighth audit-pass bug-fix batch (2026-07-19).

Pins the three high-severity findings from the eighth review pass:

A1. ``Network.define_paths`` deduplicated paths by branch-name sequence
    only, dropping legitimate paths when two ``(source, fault)`` pairs
    shared the same branch route (two faults on one bus, or two sources
    on one bus). The result was a silently wrong solve — ``EPR = 0`` for
    the shadowed fault, or an ignored source — which *underestimates* the
    earth-potential rise and is therefore safety-relevant. The dedup
    signature now includes the source and fault identity.
A2. ``ComplexNumber`` (and ``Bus`` / ``Branch`` / ``Network``) serialised
    non-finite floats as JSON ``null`` (pydantic default
    ``ser_json_inf_nan='null'``). An open-end impedance ``inf`` (from the
    documented ``"nan"`` formula) round-tripped to ``nan`` and poisoned
    the solve; ``inf`` in a lumped RLC dict even raised on reload. The
    models now use ``ser_json_inf_nan='constants'`` so JSON matches the
    SQLite backend.
A3. Impedance/RLC formula strings flowed straight into ``sympy.sympify``
    (which evaluates as Python), so loading a crafted network JSON or DB
    row was remote code execution. ``assert_safe_formula`` now rejects
    dunder names, dangerous builtins, attribute access and string
    literals before any SymPy parse, while ordinary free symbols and
    numeric literals keep working.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import numpy as np
import pytest

import groundinsight as gi
from groundinsight.utils.validations import (
    assert_safe_formula,
    validate_impedance_formula_value,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _line_network(faults, sources):
    """Build ``b1 - c1 - b2 - c2 - b3`` with a purely resistive ground.

    ``faults`` / ``sources`` are lists of ``(name, bus)`` tuples so a test
    can place several faults or sources on the same bus.
    """
    net = gi.create_network(name="pass8", frequencies=[50.0])
    bus_type = gi.BusType(
        name="b",
        system_type="s",
        voltage_level=20.0,
        impedance_formula="rho*0 + 0.5",
    )
    branch_type = gi.BranchType(
        name="c",
        grounding_conductor=True,
        self_impedance_formula="(rho*0 + 0.25 + j*f*0.012)*l",
        mutual_impedance_formula="(rho*0 + 0.10 + j*f*0.012)*l",
    )
    for name in ("b1", "b2", "b3"):
        gi.create_bus(name=name, type=bus_type, network=net)
    gi.create_branch(
        name="c1", type=branch_type, from_bus="b1", to_bus="b2",
        length=5.0, network=net,
    )
    gi.create_branch(
        name="c2", type=branch_type, from_bus="b2", to_bus="b3",
        length=5.0, network=net,
    )
    for name, bus in sources:
        gi.create_source(name=name, bus=bus, values={50.0: 1000.0}, network=net)
    for name, bus in faults:
        gi.create_fault(name=name, bus=bus, scalings={50.0: 1.0}, network=net)
    return net


def _rms_epr(net, fault, bus):
    df = net.res_buses(fault)
    row = df.filter((df["bus_name"] == bus) & (df["frequency_Hz"] == "RMS"))
    return row["EPR_V"][0]


# ---------------------------------------------------------------------------
# A1 — path deduplication must not shadow (source, fault) pairs
# ---------------------------------------------------------------------------


def test_two_faults_on_same_bus_both_solve():
    """Two faults on the same bus must produce identical, non-zero EPR.

    Before the fix the second fault's path was deduplicated away and its
    EPR came back as exactly ``0`` — a silent, unsafe underestimate.
    """
    base = _line_network([("F1", "b3")], [("src", "b1")])
    gi.run_fault(base, "F1")
    ref = _rms_epr(base, "F1", "b3")
    assert ref > 0.0

    net = _line_network([("F1", "b3"), ("F2", "b3")], [("src", "b1")])
    gi.run_fault(net, "F1")
    gi.run_fault(net, "F2")
    assert _rms_epr(net, "F1", "b3") == pytest.approx(ref)
    assert _rms_epr(net, "F2", "b3") == pytest.approx(ref)
    assert _rms_epr(net, "F2", "b3") > 0.0


def test_two_sources_on_same_bus_both_contribute():
    """Two equal sources on one bus must roughly double the drive.

    Before the fix the second source's path collided with the first and
    was dropped, so only one source was injected.
    """
    base = _line_network([("F1", "b3")], [("src", "b1")])
    gi.run_fault(base, "F1")
    single = _rms_epr(base, "F1", "b3")

    net = _line_network([("F1", "b3")], [("sA", "b1"), ("sB", "b1")])
    gi.run_fault(net, "F1")
    assert _rms_epr(net, "F1", "b3") == pytest.approx(2.0 * single)


def test_distinct_paths_still_deduplicated_within_pair():
    """The single-source / single-fault baseline still yields exactly the
    paths of the reachable topology (no spurious duplication)."""
    net = _line_network([("F1", "b3")], [("src", "b1")])
    gi.run_fault(net, "F1")
    # exactly one simple path b1 -> b2 -> b3
    assert len(net.paths) == 1


# ---------------------------------------------------------------------------
# A2 — non-finite (open-end) values survive the JSON round-trip
# ---------------------------------------------------------------------------


def test_open_end_impedance_survives_json_roundtrip():
    """An ``inf`` grounding impedance must remain ``inf`` after a JSON
    dump/reload, not degrade to ``nan``."""
    net = gi.create_network(name="io", frequencies=[50.0])
    bus_type = gi.BusType(
        name="open", system_type="s", voltage_level=20.0,
        impedance_formula="nan",
    )
    gi.create_bus(name="b1", type=bus_type, network=net)

    z_before = net.buses["b1"].impedance[50.0]
    assert np.isinf(z_before.real)

    payload = net.model_dump_json()
    assert '"real":null' not in payload.replace(" ", "")

    reloaded = gi.Network.model_validate_json(payload)
    z_after = reloaded.buses["b1"].impedance[50.0]
    assert np.isinf(z_after.real)


def test_open_end_rlc_survives_json_roundtrip():
    """An ``inf`` lumped RLC value must reload without raising and stay
    ``inf`` (previously a ``ValidationError`` on reload)."""
    net = gi.create_network(name="io", frequencies=[50.0])
    bus_type = gi.BusType(
        name="open", system_type="s", voltage_level=20.0,
        impedance_formula="rho*0 + 1.0", R_formula="nan",
    )
    gi.create_bus(name="b1", type=bus_type, network=net)
    assert np.isinf(net.buses["b1"].R[50.0])

    reloaded = gi.Network.model_validate_json(net.model_dump_json())
    assert np.isinf(reloaded.buses["b1"].R[50.0])


def test_finite_network_json_roundtrip_unchanged():
    """A finite network must still round-trip bit-for-bit (no regression
    from the config change)."""
    net = gi.create_network(name="io", frequencies=[50.0, 250.0])
    bus_type = gi.BusType(
        name="b", system_type="s", voltage_level=20.0,
        impedance_formula="rho*0 + 0.5 + j*f*0.001",
    )
    gi.create_bus(name="b1", type=bus_type, network=net)
    reloaded = gi.Network.model_validate_json(net.model_dump_json())
    for f in (50.0, 250.0):
        assert reloaded.buses["b1"].impedance[f] == net.buses["b1"].impedance[f]


# ---------------------------------------------------------------------------
# A3 — formula strings must not be an arbitrary-code-execution sink
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "payload",
    [
        "__import__('os').system('touch {canary}')",
        "open('{canary}', 'w')",
        "().__class__.__bases__[0]",
        "eval('1')",
    ],
)
def test_malicious_formula_rejected(payload, tmp_path: Path):
    """A crafted formula must raise ``ValueError`` and must not run."""
    canary = tmp_path / "pwned"
    with pytest.raises(ValueError):
        gi.BusType(
            name="x", system_type="s", voltage_level=1.0,
            impedance_formula=payload.format(canary=canary),
        )
    assert not canary.exists()


def test_malicious_formula_rejected_on_json_load(tmp_path: Path):
    """Loading a network JSON whose formula is a code payload must fail
    safely — the RCE reaches through ``model_validate_json`` too."""
    canary = tmp_path / "pwned"
    payload = {
        "name": "n",
        "frequencies": [50.0],
        "buses": {
            "b": {
                "name": "b",
                "type": {
                    "name": "t", "system_type": "s", "voltage_level": 1.0,
                    "impedance_formula": f"open('{canary}', 'w')",
                },
                "impedance": {},
            }
        },
    }
    with pytest.raises(Exception):
        gi.Network.model_validate_json(json.dumps(payload))
    assert not canary.exists()


def test_assert_safe_formula_blocks_dunder_and_attribute_access():
    with pytest.raises(ValueError):
        assert_safe_formula("x.__class__")
    with pytest.raises(ValueError):
        assert_safe_formula("rho.real")
    with pytest.raises(ValueError):
        assert_safe_formula("'string literal'")


@pytest.mark.parametrize(
    "formula",
    [
        "1+roh+f",                       # arbitrary free symbol (typo) still ok
        "rho*0 + 0.1 + I*f*1/50",
        "(0.25 + j*f*0.012)*l",
        "NaN",                            # open-end sentinel
        "1 + 2*j",
        "R + j*X",
        "rho*0 + 10 + I*2*pi*f*5e-3",     # pi and scientific notation
        "(rho*0 + 600e-9)*l",
    ],
)
def test_legitimate_formulas_still_accepted(formula):
    """No legitimate arithmetic formula may be rejected by the guard."""
    assert validate_impedance_formula_value(formula) == formula
    assert_safe_formula(formula)  # must not raise


def test_legit_formula_solves_end_to_end_after_guard():
    """A guarded formula must still evaluate in a real solve."""
    net = _line_network([("F1", "b3")], [("src", "b1")])
    gi.run_fault(net, "F1")
    assert _rms_epr(net, "F1", "b3") > 0.0

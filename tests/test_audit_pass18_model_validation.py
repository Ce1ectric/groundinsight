# tests/test_audit_pass18_model_validation.py

"""
Regression tests for audit pass 18, findings M4/M19, M5, M6, M8, M9, M22, T2.

M4/M19 -- none of the core models validated on assignment, so a value that
would be rejected at construction slipped in through ``setattr``. The path
that made this concrete is
:func:`groundinsight.io.apply_shortcircuit_characteristics`, whose docstring
invites a hand-edited frame: a ``kappa`` of 42 pushed ``i_p`` up by a factor of
21 in the *unsafe* direction, and the network could not be serialised
afterwards. ``Network.frequencies`` had the same hole (M19).

M5 -- ``ComplexNumber.__eq__`` raised ``TypeError`` for anything it could not
convert, so ``z == None`` crashed on every ``Optional[ComplexNumber]`` field;
and defining ``__eq__`` had removed ``__hash__``.

M6/T2 -- NumPy scalars worked on the left of an operator and raised on the
right, and no test covered the reflected operators at all.

M8/M9 -- ``Fault.scalings`` accepted ``nan``/``inf``/negative values, and the
serialisers wrote the bare ``Infinity`` literal, which is not RFC 8259 JSON:
SQLite's ``json_valid()`` returns 0 for it and no non-Python reader accepts it.
The open-end sentinel of this package *is* an infinite impedance, so dropping
it to ``null`` was not an option either.

M22 -- ``repr(ComplexNumber(real=-1, imag=-2))`` was ``(-1.0+-2.0j)``.
"""

import json
import math
import sqlite3

import numpy as np
import polars as pl
import pytest
from pydantic import ValidationError

import groundinsight as gi
from groundinsight.models.core_models import ComplexNumber, Fault, Network, Source


FREQUENCIES = [50.0]


def _network(name="pass18"):
    """Three buses in a line, one open-ended stub, one source, one fault."""
    bus_type = gi.BusType(
        name="bt",
        system_type="Substation",
        voltage_level=110.0,
        impedance_formula="rho*0 + 1 + I*f*0",
    )
    ordinary = gi.BranchType(
        name="ord",
        grounding_conductor=True,
        self_impedance_formula="(0.25 + I*f*0.012)*l",
        mutual_impedance_formula="(0.05 + I*f*0.010)*l",
    )
    open_end = gi.BranchType(
        name="open",
        grounding_conductor=True,
        self_impedance_formula="nan",
        mutual_impedance_formula="nan",
    )
    net = gi.create_network(name=name, frequencies=FREQUENCIES)
    for label in ("A", "B", "C"):
        gi.create_bus(name=label, type=bus_type, network=net)
    gi.create_branch(
        name="AB", type=ordinary, from_bus="A", to_bus="B", length=1.0, network=net
    )
    gi.create_branch(
        name="BC", type=open_end, from_bus="B", to_bus="C", length=1.0, network=net
    )
    gi.create_source(
        name="s", bus="A", values={50.0: 1000.0 + 0.0j}, network=net
    )
    gi.create_fault(name="F", bus="B", scalings={50.0: 1.0}, network=net)
    return net


# ---------------------------------------------------------------------------
# M4 / M19 -- validate_assignment
# ---------------------------------------------------------------------------


def test_source_rejects_an_out_of_range_kappa_on_assignment():
    net = _network()
    src = net.sources["s"]
    with pytest.raises(ValidationError, match="kappa"):
        src.kappa = 42.0
    assert src.kappa is None


def test_fault_rejects_a_non_positive_clearing_time_on_assignment():
    net = _network()
    fault = net.faults["F"]
    with pytest.raises(ValidationError, match="t_k_s"):
        fault.t_k_s = 0.0
    with pytest.raises(ValidationError, match="n_factor"):
        fault.n_factor = 3.0


def test_bus_and_branch_reject_a_broken_impedance_dict_on_assignment():
    """The validator raises ``TypeError`` per the project's error convention.

    Pydantic lets a ``TypeError`` out of a ``mode="before"`` validator
    unwrapped, which is why this is not a ``ValidationError``.
    """
    net = _network()
    good = dict(net.buses["A"].impedance)
    with pytest.raises((TypeError, ValidationError)):
        net.buses["A"].impedance = "not a dict"
    assert net.buses["A"].impedance == good
    with pytest.raises((TypeError, ValidationError)):
        net.branches["AB"].self_impedance = ["not", "a", "dict"]


def test_network_frequencies_are_validated_on_assignment():
    """M19: assigning a duplicate/negative/empty frequency list must raise."""
    net = _network()
    with pytest.raises(ValidationError):
        net.frequencies = []
    with pytest.raises(ValidationError):
        net.frequencies = [50.0, 50.0]
    with pytest.raises(ValidationError):
        net.frequencies = [-1.0]
    assert net.frequencies == FREQUENCIES


def test_apply_shortcircuit_characteristics_rejects_a_tampered_frame():
    """A hand-edited result frame must not reach the model unchecked.

    The docstring of ``apply_shortcircuit_characteristics`` explicitly allows
    passing a frame produced earlier, so a frame edited by hand is a documented
    input. ``kappa = 42`` multiplies the peak current by 21 in the unsafe
    direction and used to be written straight onto every source.
    """
    from groundinsight.io.pandapower_sc import (
        _SC_SCHEMA,
        apply_shortcircuit_characteristics,
    )

    net = _network()
    row = {name: None for name in _SC_SCHEMA}
    row.update(
        {
            "pp_bus_index": 0,
            "bus_name": "B",
            "vn_kv": 110.0,
            "fault_type": "1ph",
            "case": "max",
            "i_k_a": 1000.0,
            "kappa": 42.0,  # tampered
            "kappa_origin": "manual",
            "n_factor": 1.0,
        }
    )
    frame = pl.DataFrame([row], schema=_SC_SCHEMA)

    with pytest.raises(ValidationError, match="kappa"):
        apply_shortcircuit_characteristics(net, frame, "F", bus_name="B")
    assert net.sources["s"].kappa is None


# ---------------------------------------------------------------------------
# M5 / M6 / T2 -- ComplexNumber comparison, hashing and operator matrix
# ---------------------------------------------------------------------------


def test_equality_with_unconvertible_types_is_false_not_an_error():
    z = ComplexNumber(real=1.0, imag=2.0)
    assert (z == None) is False  # noqa: E711 - the point of the test
    assert (z != None) is True  # noqa: E711
    assert (z == "banana") is False
    assert (z == object()) is False
    assert (None == z) is False  # noqa: E711


def test_complex_number_is_hashable_and_consistent_with_equality():
    z = ComplexNumber(real=1.0, imag=0.0)
    assert hash(z) == hash(1.0)
    assert len({ComplexNumber(real=1.0, imag=2.0), ComplexNumber(real=1.0, imag=2.0)}) == 1
    assert {z: "ok"}[ComplexNumber(real=1.0, imag=0.0)] == "ok"


#: Operands that must work on *either* side of every binary operator.
_OPERANDS = [
    2,
    2.5,
    -3,
    complex(1.0, -2.0),
    np.int64(3),
    np.float64(1.5),
    np.float32(0.5),
    np.complex64(1 + 1j),
    np.complex128(2 - 1j),
    ComplexNumber(real=0.5, imag=-0.25),
]

_BINARY_OPS = [
    ("add", lambda a, b: a + b),
    ("sub", lambda a, b: a - b),
    ("mul", lambda a, b: a * b),
    ("truediv", lambda a, b: a / b),
]


@pytest.mark.parametrize("other", _OPERANDS, ids=lambda o: type(o).__name__)
@pytest.mark.parametrize("name,op", _BINARY_OPS, ids=lambda x: x if isinstance(x, str) else "")
def test_operator_matrix_both_sides(name, op, other):
    """Every operator agrees with plain ``complex`` arithmetic, both ways."""
    z = ComplexNumber(real=3.0, imag=-4.0)
    zc = complex(3.0, -4.0)
    oc = complex(other) if not isinstance(other, ComplexNumber) else complex(other)

    left = op(z, other)
    assert isinstance(left, ComplexNumber)
    assert complex(left) == pytest.approx(op(zc, oc))

    right = op(other, z)
    # NumPy scalars answer first for their own type; the value still has to be
    # the same number, whichever class carries it.
    assert complex(right) == pytest.approx(op(oc, zc))


@pytest.mark.parametrize("other", _OPERANDS, ids=lambda o: type(o).__name__)
def test_power_accepts_the_same_operands(other):
    z = ComplexNumber(real=1.5, imag=0.5)
    assert complex(z**other) == pytest.approx(complex(1.5, 0.5) ** complex(other))


@pytest.mark.parametrize("bad", [None, "banana", object(), [1, 2]])
def test_operators_reject_unconvertible_operands(bad):
    z = ComplexNumber(real=1.0, imag=1.0)
    with pytest.raises(TypeError):
        z + bad
    with pytest.raises(TypeError):
        z * bad


def test_non_finite_operands_propagate_like_complex():
    z = ComplexNumber(real=float("inf"), imag=0.0)
    assert math.isinf((z + 1).real)
    assert math.isnan(ComplexNumber(real=float("nan"), imag=0.0).real)
    # An infinite impedance is the open-end sentinel; it must stay comparable.
    assert ComplexNumber(real=float("inf"), imag=0.0) == complex(float("inf"), 0.0)
    assert (ComplexNumber(real=float("nan"), imag=0.0) == float("nan")) is False


def test_repr_is_readable_back():
    """M22: ``eval(repr(z))`` must reproduce the value."""
    z = ComplexNumber(real=-1.0, imag=-2.0)
    assert repr(z) == "ComplexNumber(real=-1.0, imag=-2.0)"
    assert eval(repr(z), {"ComplexNumber": ComplexNumber}) == z


# ---------------------------------------------------------------------------
# M8 / M9 -- scalings validation and RFC-8259 JSON
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), -1.0])
def test_fault_scalings_reject_non_finite_and_negative_values(bad):
    with pytest.raises(ValidationError):
        Fault(name="F", bus="B", scalings={50.0: bad})


def test_fault_scalings_are_validated_on_assignment():
    fault = Fault(name="F", bus="B", scalings={50.0: 1.0})
    with pytest.raises(ValidationError):
        fault.scalings = {50.0: float("inf")}
    assert fault.scalings == {50.0: 1.0}


def _strict_parse_constant(token):
    raise AssertionError(f"non-RFC-8259 JSON constant in the document: {token}")


def test_open_end_network_serialises_to_rfc_json_and_round_trips():
    """The ``"nan"`` formula produces an infinite impedance -- the sentinel.

    It has to survive the round-trip *and* leave the document readable by
    something other than Python.
    """
    net = _network("pass18_json")
    gi.run_fault(net, "F")
    assert math.isinf(net.branches["BC"].self_impedance[50.0].real)

    payload = net.model_dump_json(indent=2)
    json.loads(payload, parse_constant=_strict_parse_constant)
    assert '"inf"' in payload

    restored = Network.model_validate_json(payload)
    assert math.isinf(restored.branches["BC"].self_impedance[50.0].real)


def test_open_end_network_is_valid_json_in_sqlite(tmp_path):
    net = _network("pass18_sqlite")
    gi.run_fault(net, "F")
    path = str(tmp_path / "rfc.db")
    gi.close_dbsession()
    gi.start_dbsession(path)
    try:
        gi.save_network_to_db(net)
        restored = gi.load_network_from_db("pass18_sqlite")
    finally:
        gi.close_dbsession()
    assert math.isinf(restored.branches["BC"].self_impedance[50.0].real)

    conn = sqlite3.connect(path)
    try:
        invalid = []
        for table in ("buses", "branches", "sources", "faults", "networks"):
            columns = [r[1] for r in conn.execute(f'PRAGMA table_info("{table}")')]
            for column in columns:
                for (value,) in conn.execute(f'SELECT "{column}" FROM "{table}"'):
                    if not isinstance(value, str) or not value.startswith(("{", "[")):
                        continue
                    if not conn.execute(
                        "SELECT json_valid(?)", (value,)
                    ).fetchone()[0]:
                        invalid.append(f"{table}.{column}: {value[:60]}")
    finally:
        conn.close()
    assert invalid == []


def test_source_model_still_validates_its_mode_on_assignment():
    """``validate_assignment`` must not let a Source into an invalid mode."""
    src = Source(name="s", bus="A", values={50.0: ComplexNumber(real=1.0, imag=0.0)})
    with pytest.raises(ValidationError):
        src.source_type = "voltage"
    assert src.source_type == "current"

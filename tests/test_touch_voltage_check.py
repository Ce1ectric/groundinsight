# tests/test_touch_voltage_check.py
"""
The EN 50522 touch-voltage assessment per bus (``gi.check_touch_voltages``).

Second ways: the RMS touch voltage against the RMS row of ``res_buses``; the
limit of a curve against a hand computation on log-log axes; the verdict
against the comparison itself.
"""

import math
import warnings

import polars as pl
import pytest

import groundinsight as gi
from groundinsight.analysis.touch import limit_at

from tests._characteristic_cases import _network, _table

#: A curve shaped like the permissible touch voltage over the fault duration;
#: test data only, not the values of any edition of the standard.
CURVE = [(0.05, 700.0), (0.1, 650.0), (0.5, 220.0), (1.0, 120.0), (10.0, 85.0)]


def test_a_constant_limit():
    net = _network(station_characteristic=_table(), rho=300.0)
    gi.run_fault(net, "F")
    frame = gi.check_touch_voltages(net, "F", u_limit=50.0)
    assert dict(frame.schema) == gi.TOUCH_VOLTAGE_SCHEMA
    rms = net.res_buses("F").filter(pl.col("is_rms"))
    for row in frame.iter_rows(named=True):
        expected = rms.filter(pl.col("bus_name") == row["bus_name"]).row(0, named=True)
        assert row["EPR_V"] == expected["EPR_V"]
        assert row["U_vT_V"] == expected["U_vT_V"]
        if row["U_vT_V"] is None:
            assert row["utilisation"] is None and row["within_limit"] is None
            continue
        assert row["U_limit_V"] == 50.0
        assert row["utilisation"] == row["U_vT_V"] / 50.0
        assert row["within_limit"] is (row["U_vT_V"] <= 50.0)
    assert frame.filter(pl.col("bus_name") == "Q")["U_vT_V"].item() is None


def test_a_curve_is_read_at_the_fault_duration_on_log_log_axes():
    t = 0.3
    weight = math.log(t / 0.1) / math.log(0.5 / 0.1)
    by_hand = math.exp(math.log(650.0) + weight * (math.log(220.0) - math.log(650.0)))
    assert limit_at(CURVE, t) == pytest.approx(by_hand, rel=1e-15)
    assert limit_at(CURVE, 0.5) == 220.0  # a point of the curve: exactly
    net = _network(station_characteristic=_table(), rho=300.0)
    net.faults["F"].t_k_s = t
    gi.run_fault(net, "F")
    frame = gi.check_touch_voltages(net, "F", u_limit=CURVE)
    assert set(frame.drop_nulls("U_limit_V")["U_limit_V"].to_list()) == {limit_at(CURVE, t)}
    assert set(frame.drop_nulls("t_k_s")["t_k_s"].to_list()) == {t}
    # The argument wins over Fault.t_k_s.
    frame = gi.check_touch_voltages(net, "F", u_limit=CURVE, t_k_s=1.0)
    assert set(frame.drop_nulls("U_limit_V")["U_limit_V"].to_list()) == {120.0}


@pytest.mark.parametrize(
    ("u_limit", "t_k_s", "message"),
    [
        (CURVE, None, "pass t_k_s"),
        (CURVE, 20.0, "outside the limit curve"),
        (CURVE, 0.01, "outside the limit curve"),
        ([(0.1, 100.0)], 0.1, "at least two"),
        ([(0.5, 100.0), (0.1, 200.0)], 0.2, "strictly increasing"),
        ([(0.1, -1.0), (0.5, 100.0)], 0.2, "finite and positive"),
        (0.0, None, "finite and positive"),
        (float("nan"), None, "finite and positive"),
    ],
)
def test_invalid_limits_are_refused(u_limit, t_k_s, message):
    net = _network(station_characteristic=_table(), rho=300.0)
    gi.run_fault(net, "F")
    with pytest.raises(ValueError, match=message):
        gi.check_touch_voltages(net, "F", u_limit=u_limit, t_k_s=t_k_s)


def test_errors_for_an_unknown_fault_and_a_missing_result():
    net = _network(station_characteristic=_table())
    with pytest.raises(ValueError, match="does not exist"):
        gi.check_touch_voltages(net, "nope", u_limit=80.0)
    with pytest.raises(RuntimeError, match="no result"):
        gi.check_touch_voltages(net, "F", u_limit=80.0)


def test_without_any_touch_factor_the_check_warns():
    net = _network()
    gi.run_fault(net, "F")
    with pytest.warns(gi.InputWarning, match="tabulated touch factor") as record:
        frame = gi.check_touch_voltages(net, "F", u_limit=80.0)
    assert record[0].message.code == "TOUCH_FACTORS_MISSING"
    assert frame["within_limit"].null_count() == frame.height


def test_the_verdict_flips_at_the_limit():
    net = _network(station_characteristic=_table(), rho=300.0)
    gi.run_fault(net, "F")
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        touch = gi.check_touch_voltages(net, "F", u_limit=1.0)
    top = touch.drop_nulls("U_vT_V")["U_vT_V"].max()
    exact = gi.check_touch_voltages(net, "F", u_limit=top)
    assert exact.drop_nulls("within_limit")["within_limit"].all()
    below = gi.check_touch_voltages(net, "F", u_limit=top * (1 - 1e-12))
    assert not below.drop_nulls("within_limit")["within_limit"].all()

# tests/test_characteristic.py
"""
The earthing characteristic: validation, interpolation, serialisation.

The interpolation is checked against analytic functions on two ways:

* a power law ``|Z| = a rho^p f^q`` with an angle linear in ``ln rho`` and
  ``ln f`` lies in the space the interpolation spans (bilinear in
  ``ln rho``, ``ln f`` for ``ln|Z|`` and the angle), so it must come out
  exact to rounding everywhere -- between the grid points and outside them;
* a function outside that space, ``|Z| = rho (1 + (f/f0)^2)^(1/2)``, must
  be exact at the grid points and between them within the textbook bound of
  bilinear interpolation, ``h^2/8 * max|g''|`` per axis for ``g = ln|Z|``.
"""

import cmath
import json
import math

import numpy as np
import pytest
from pydantic import ValidationError

import groundinsight as gi
from groundinsight.models.characteristic import EarthingCharacteristic

RHO = (30.0, 100.0, 300.0, 1000.0)
FREQ = (50.0, 150.0, 450.0, 1350.0)


def _power_law(rho, f):
    magnitude = 0.05 * rho ** 0.9 * f ** 0.15
    angle = 0.02 * math.log(rho) + 0.1 * math.log(f) - 0.3
    return cmath.rect(magnitude, angle)


def _grid(function, rho=RHO, freq=FREQ):
    return [[function(r, f) for f in freq] for r in rho]


def _characteristic(function=_power_law, **extra):
    return EarthingCharacteristic(
        rho_Ohm_m=RHO, frequency_Hz=FREQ, impedance_Ohm=_grid(function), **extra
    )


def test_grid_points_are_returned_exactly():
    k = [[0.1 + 0.01 * i + 0.001 * j for j in range(4)] for i in range(4)]
    table = _characteristic(k_touch=k, k_transfer=k)
    for i, rho in enumerate(RHO):
        values = table.evaluate(rho, FREQ)
        assert not values.extrapolated
        for j in range(len(FREQ)):
            assert values.impedance[j] == _power_law(rho, FREQ[j])  # bit for bit
            assert values.k_touch[j] == k[i][j]
            assert values.k_transfer[j] == k[i][j]


@pytest.mark.parametrize("rho", [30.0, 47.0, 100.0, 233.3, 999.0, 10.0, 5000.0])
def test_a_power_law_is_reproduced_inside_and_outside_the_grid(rho):
    freqs = [50.0, 77.0, 600.0, 1350.0, 20.0, 5000.0]
    values = _characteristic().evaluate(rho, freqs)
    expected = np.array([_power_law(rho, f) for f in freqs])
    np.testing.assert_allclose(values.impedance, expected, rtol=1e-12)
    assert values.extrapolated  # 20 Hz and 5000 Hz lie outside the grid


def test_between_grid_points_the_error_stays_within_the_bilinear_bound():
    f0 = 400.0

    def smooth(rho, f):
        return complex(rho * math.sqrt(1.0 + (f / f0) ** 2) / 100.0, 0.0)

    table = _characteristic(smooth)
    # g(y) = ln|Z| = ln rho + 0.5 ln(1 + e^(2y)/f0^2) is linear in x = ln rho;
    # g''(y) = 2 s / (1 + s)^2 with s = (f/f0)^2, at most 1/2 (at f = f0).
    h_y = max(math.log(b / a) for a, b in zip(FREQ[:-1], FREQ[1:], strict=True))
    bound = h_y ** 2 / 8.0 * 0.5
    worst = 0.0
    for rho in np.geomspace(30.0, 1000.0, 23):
        freqs = np.geomspace(50.0, 1350.0, 41)
        values = table.evaluate(float(rho), freqs)
        exact = np.array([abs(smooth(rho, f)) for f in freqs])
        error = np.max(np.abs(np.log(np.abs(values.impedance)) - np.log(exact)))
        worst = max(worst, error)
    assert worst <= bound
    assert worst > bound / 20.0  # the bound is not vacuous: the test sees the error


def test_extrapolation_is_flagged_and_continues_the_edge_cell():
    table = _characteristic()
    assert table.is_extrapolated(1000.0, [1350.0]) is False
    assert table.is_extrapolated(1000.1, [50.0]) is True
    assert table.is_extrapolated(100.0, [49.9]) is True
    assert table.evaluate(2000.0, [50.0]).extrapolated is True
    # At or below 0 Hz: the lowest grid frequency.
    dc = table.evaluate(100.0, [0.0])
    assert dc.extrapolated is True
    assert dc.impedance[0] == table.evaluate(100.0, [50.0]).impedance[0]


def test_continued_factors_are_clipped_at_zero():
    k = [[0.4], [0.2]]
    table = EarthingCharacteristic(rho_Ohm_m=(100.0, 200.0), frequency_Hz=(50.0,),
                                   impedance_Ohm=[[1.0], [2.0]], k_touch=k)
    assert table.evaluate(150.0, [50.0]).k_touch[0] == pytest.approx(0.4 - 0.2 * math.log(1.5) / math.log(2.0))
    assert table.evaluate(1e4, [50.0]).k_touch[0] == 0.0


def test_a_single_point_axis_is_constant():
    table = EarthingCharacteristic(rho_Ohm_m=(100.0,), frequency_Hz=(50.0,),
                                   impedance_Ohm=[[2 + 1j]], k_touch=[[0.25]])
    values = table.evaluate(400.0, [50.0, 1000.0])
    assert list(values.impedance) == [2 + 1j, 2 + 1j]
    assert list(values.k_touch) == [0.25, 0.25]
    assert values.extrapolated


@pytest.mark.parametrize(
    ("change", "message"),
    [
        ({"rho_Ohm_m": (100.0, 100.0)}, "strictly increasing"),
        ({"rho_Ohm_m": (0.0, 100.0)}, "> 0"),
        ({"frequency_Hz": (50.0, float("inf"))}, "> 0"),
        ({"frequency_Hz": ()}, "at least one"),
        ({"impedance_Ohm": [[1.0, 1.0]]}, "rows"),
        ({"impedance_Ohm": [[1.0], [-1.0 + 1j]]}, "passive"),
        ({"impedance_Ohm": [[1.0], [0.0]]}, "passive"),
        ({"impedance_Ohm": [[1.0], [float("nan")]]}, "not finite"),
        ({"k_touch": [[0.1], [-0.1]]}, ">= 0"),
        ({"k_transfer": [[0.1, 0.2], [0.1, 0.2]]}, "rows"),
        ({"impedance_Ohm": None}, "needs impedance_Ohm"),
        ({"impedance_formula": "rho * x"}, "x"),
        ({"impedance_formula": "__import__('os')"}, "non-arithmetic"),
    ],
)
def test_invalid_characteristics_are_refused(change, message):
    data = {"rho_Ohm_m": (100.0, 200.0), "frequency_Hz": (50.0,),
            "impedance_Ohm": [[1.0], [2.0]]}
    data.update(change)
    with pytest.raises(ValidationError, match=message):
        EarthingCharacteristic(**data)


def test_formula_source_needs_no_table():
    table = EarthingCharacteristic(rho_Ohm_m=(100.0,), frequency_Hz=(50.0,),
                                   k_touch=[[0.3]], impedance_source="formula",
                                   impedance_formula="rho / 100")
    assert table.evaluate(100.0, [50.0]).impedance is None


def test_impedance_accepts_the_spellings_of_a_complex_number():
    spellings = [[gi.ComplexNumber(real=1.0, imag=2.0)], [{"real": 1.0, "imag": 2.0}],
                 [[1.0, 2.0]], [1 + 2j]]
    table = EarthingCharacteristic(rho_Ohm_m=(1.0, 2.0, 3.0, 4.0), frequency_Hz=(50.0,),
                                   impedance_Ohm=spellings)
    assert {row[0] for row in table.impedance_Ohm} == {1 + 2j}


def test_a_characteristic_is_read_only():
    table = _characteristic()
    with pytest.raises(ValidationError):
        table.k_touch = ((0.1,),)


def test_json_round_trip_is_exact_and_uses_the_complex_number_form():
    provenance = gi.CharacteristicProvenance(source="groundfield", groundfield_version="2026.11.0",
                                             soil={"layers": [[100.0, 2.0], [500.0, None]]})
    k = [[0.1 * (i + 1) / 3.0 for _ in FREQ] for i in range(len(RHO))]
    table = _characteristic(k_touch=k, name="Station A", provenance=provenance,
                            impedance_formula="rho/50 + I*f/1e4")
    text = table.model_dump_json()
    assert json.loads(text)["impedance_Ohm"][0][0].keys() == {"real", "imag"}
    again = EarthingCharacteristic.model_validate_json(text)
    assert again == table
    assert EarthingCharacteristic.model_validate(table.model_dump(mode="json")) == table


def test_to_frame_lists_the_grid():
    frame = _characteristic().to_frame()
    assert frame.height == len(RHO) * len(FREQ)
    first = frame.row(0, named=True)
    z = _power_law(RHO[0], FREQ[0])
    assert first["Z_Ohm"] == abs(z) and first["R_Ohm"] == z.real
    assert first["k_touch"] is None

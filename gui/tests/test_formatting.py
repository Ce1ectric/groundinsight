# gui/tests/test_formatting.py
"""
Number formatting and parsing of the GUI -- the only arithmetic it does.

The GUI shows the numbers of the DTOs with four significant digits and the
decimal separator of the language, and reads a typed cell back with either
separator (a German user types ``0,8``). Rounding is Python's ``g`` format; the
tests compare against it rather than against hand-written strings where
rounding could be ambiguous.
"""


import pytest

from groundinsight_gui.formatting import format_number, format_scale, format_unit, parse_number


@pytest.mark.parametrize(
    "value, language, expected",
    [
        (275.43107335362464, "de", "275,4"),
        (275.43107335362464, "en", "275.4"),
        (0.31000292631538, "de", "0,31"),
        (1000.0, "de", "1000"),
        (1.5e-5, "en", "1.5e-05"),
        (-12.25, "de", "-12,25"),
        (None, "de", "–"),
        (float("nan"), "en", "–"),
        (float("inf"), "de", "–"),
    ],
)
def test_format_number(value, language, expected):
    assert format_number(value, language) == expected


def test_format_number_is_pythons_rounding():
    for value in (187.54548718780015, 0.17825804092290826, 0.6169172832662088):
        assert format_number(value, "en") == f"{value:.4g}"
        assert format_number(value, "de") == f"{value:.4g}".replace(".", ",")


@pytest.mark.parametrize(
    "text, expected",
    [("0,8", 0.8), (" 1.5 ", 1.5), ("1e3", 1000.0), ("-2,5", -2.5), (3, 3.0), (2.25, 2.25)],
)
def test_parse_number(text, expected):
    assert parse_number(text) == expected


@pytest.mark.parametrize("text", ["", "abc", "1,2,3", True, None, "nan", "inf"])
def test_parse_number_rejects(text):
    with pytest.raises(ValueError):
        parse_number(text)


def test_units_are_shown_with_their_symbols():
    assert format_unit("Ohm*m") == "Ω·m"
    assert format_unit("Ohm") == "Ω"
    assert format_unit("km") == "km"
    assert format_unit(None) == ""


@pytest.mark.parametrize(
    "low, high, language, expected",
    [
        (5.505e-13, 196.5, "de", ("0", "196,5")),       # numerical zero of a solve, not 5,505e-13
        (36.62, 934.0, "de", ("36,6", "934")),          # both ends to the same decimal place
        (0.0123, 0.5, "en", ("0.0123", "0.5")),
        (2236.07, 4325.3, "de", ("2236", "4325")),
        (-3.0, 3.0, "en", ("-3", "3")),
    ],
)
def test_the_two_ends_of_a_colour_scale_share_their_decimal_place(low, high, language, expected):
    assert format_scale(low, high, language) == expected


def test_a_numerical_zero_beside_real_values_reads_as_zero():
    from groundinsight_gui.formatting import format_value

    assert format_value(5.89e-15, 18.03, "de") == "0"
    assert format_value(-3e-9, 934.0, "en") == "0"
    # A small value that is a value keeps its digits; so does everything without a larger neighbour.
    assert format_value(0.0366, 934.0, "de") == "0,0366"
    assert format_value(5.89e-15, 5.89e-15, "en") == "5.89e-15"
    assert format_value(None, 1.0, "de") == "–"

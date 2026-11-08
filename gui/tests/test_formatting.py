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

from groundinsight_gui.formatting import format_number, format_unit, parse_number


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

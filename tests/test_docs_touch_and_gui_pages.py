# tests/test_docs_touch_and_gui_pages.py
"""
The pages on the GUI tutorial, earthing characteristics and touch voltages.

* ``getting-started/gui.md`` and ``howto/groundfield-characteristics.md``
  print what their ``text`` blocks say, and the how-to's numbers are the
  closed form of a hemisphere (``Z = rho / (2 pi r)``, ``k_touch = 1/(r+1)``).
* ``explanation/touch-and-transfer.md``: the "network" columns are what the
  example project computes (the field columns come from groundfield and are
  checked against the network columns through the stated differences), the
  parallel connection and the touch factor are those of the project's own
  characteristics.
* ``howto/en50522-check.md`` maps ``U_vT`` and the transferred potential to
  the result fields.
"""

import contextlib
import io
import math
import os
import pathlib
import re

import pytest

import groundinsight as gi

ROOT = pathlib.Path(__file__).resolve().parents[1]
DOCS = ROOT / "docs"
EXAMPLE = DOCS / "examples" / "ortsnetzstation_5_haeuser.json"


def _page(relative: str) -> str:
    return (DOCS / relative).read_text(encoding="utf-8")


def _blocks(text: str, language: str):
    return re.findall(rf"^```{language}[^\n]*\n(.*?)^```", text, re.M | re.S)


def _run_page(relative: str, tmp_path) -> str:
    """Run the python blocks of a page in one namespace; return what they print."""
    namespace, out = {}, io.StringIO()
    cwd = os.getcwd()
    os.chdir(tmp_path)
    try:
        with contextlib.redirect_stdout(out):
            for code in _blocks(_page(relative), "python"):
                if code.lstrip().startswith("# fragment"):
                    continue
                exec(compile(code, relative, "exec"), namespace)
    finally:
        os.chdir(cwd)
    return out.getvalue()


def _stated(relative: str) -> str:
    return "".join(_blocks(_page(relative), "text"))


@pytest.mark.parametrize("page", ["getting-started/gui.md", "howto/groundfield-characteristics.md"])
def test_page_prints_what_it_states(page, tmp_path):
    assert _run_page(page, tmp_path).splitlines() == _stated(page).splitlines()


def test_howto_numbers_are_the_closed_form_of_a_hemisphere():
    z = 100.0 / (2 * math.pi * 2.0)
    epr = 100.0 * z
    stated = _stated("howto/groundfield-characteristics.md")
    assert f"Z_B = {z:.3f} Ohm, EPR = {epr:.1f} V, U_vT = {epr / 3:.1f} V" in stated


def test_tutorial_tiles_are_the_library_numbers(tmp_path):
    """The figures the tutorial quotes from the GUI's tiles are those of its code block."""
    text = _page("getting-started/gui.md")
    printed = _run_page("getting-started/gui.md", tmp_path)
    for quoted in ("`U_E` = 296.5 V", "`Z_E` = 0.7718 Ω", "`I_E` = 323.9 A", "`r` = 0.3239"):
        assert quoted in text
    assert "Z_E = 0.7718 Ohm, I_E = 323.9 A, r = 0.3239" in printed
    assert "station_3   296.5 V" in printed


@pytest.fixture(scope="module")
def example():
    project = gi.load_project(EXAMPLE)
    gi.run_fault(project.network, "F")
    return project.network


def _table(text: str, first_cell: str):
    rows = [line for line in text.splitlines() if line.startswith("| ")]
    for k, row in enumerate(rows):
        if row.startswith(f"| {first_cell} |"):
            return [[c.strip() for c in r.strip("|").split("|")] for r in rows[k:]]
    raise AssertionError(first_cell)


def _number(cell: str) -> float:
    return float(re.match(r"[−-]?[\d.]+", cell).group(0).replace("−", "-"))


def test_explanation_network_columns_are_the_example_project(example):
    text = _page("explanation/touch-and-transfer.md")
    frame = example.res_fault("F")
    rows = {r["frequency_Hz"]: r for r in frame.iter_rows(named=True) if not r["is_rms"]}
    for row in _table(text, "f")[1:4]:
        f = _number(row[0])
        z_field, z_net, dz, u_field, u_net, du = (_number(c.replace("−", "-")) for c in row[1:])
        assert z_net == pytest.approx(rows[f]["Z_E_Ohm"], abs=5e-4)
        assert u_net == pytest.approx(rows[f]["U_vT_max_V"], abs=0.05)
        assert dz == pytest.approx(100 * (z_net - z_field) / z_field, abs=0.06)
        assert du == pytest.approx(100 * (u_net - u_field) / u_field, abs=0.06)


def test_explanation_spacing_differences_follow_from_the_stated_impedances():
    rows = _table(_page("explanation/touch-and-transfer.md"), "House spacing")
    field = [_number(c) for c in rows[1][1:]]
    network = [_number(c) for c in rows[2][1:]]
    stated = [_number(c.replace("−", "-")) for c in rows[3][1:]]
    for f, n, d in zip(field, network, stated, strict=True):
        assert d == pytest.approx(100 * (n - f) / f, abs=0.15)


def test_explanation_parallel_connection_and_touch_factor(example):
    text = _page("explanation/touch-and-transfer.md")
    station, house = example.buses["ONS"], example.buses["H1"]
    z_s = abs(station.resolved_characteristic().evaluate(station.specific_earth_resistance, [50.0]).impedance[0])
    z_h = abs(house.resolved_characteristic().evaluate(house.specific_earth_resistance, [50.0]).impedance[0])
    assert f"station {z_s:.2f} Ω, five houses {z_h:.2f} Ω each, together\n{1 / (1 / z_s + 5 / z_h):.3f} Ω" in text
    assert f"from {station.k_touch[50.0]:.3f} alone" in text


def test_en50522_page_maps_the_touch_quantities():
    text = _page("howto/en50522-check.md")
    assert "| `U_vT` | prospective touch voltage | `ResultBus.u_touch`" in text
    assert "`U_transfer_V` of `res_buses()`" in text
    assert "not computed | – |" in text  # U_T with body resistances stays out
    assert "check_touch_voltages" in text

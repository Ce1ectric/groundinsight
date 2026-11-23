# tests/test_docs_capacitive_currents.py
r"""
The page "Capacitive currents and the reduction factor" shows what the library computes.

The page derives formulas step by step and quotes numbers. Its code blocks
run here as they stand (they print closed form next to solve), the printed
lines are compared, and the numbers of its tables are recomputed. The case
the page derives from the distributed currents -- a tuned, lossless coil: no
fault current, and still an earth potential rise -- is checked a third way,
with the charging current written out piece by piece along the cable.
"""

import contextlib
import io
import math
import pathlib
import re

import pytest

import groundinsight as gi

PAGE = pathlib.Path(gi.__file__).parents[2] / "docs" / "explanation" / "capacitive-currents.md"
F = 50.0


@pytest.fixture(scope="module")
def page():
    """The page text, the namespace of its code blocks and what they print."""
    text = PAGE.read_text(encoding="utf-8")
    namespace: dict = {}
    printed = io.StringIO()
    with contextlib.redirect_stdout(printed):
        for block in re.findall(r"```python\n(.*?)```", text, re.S):
            exec(compile(block, str(PAGE), "exec"), namespace)  # noqa: S102 - the documentation's own code
    return text, namespace, printed.getvalue()


def _pairs(output: str, pattern: str):
    return [(float(a), float(b)) for a, b in re.findall(pattern, output)]


def test_closed_forms_and_solves_print_the_same_numbers(page):
    _, _, output = page
    arrangements = _pairs(output, r"closed form (\d+\.\d+)\s+library (\d+\.\d+)")
    assert len(arrangements) == 4          # three arrangements and Z_E
    detuning = _pairs(output, r"r formula (\d+\.\d+)\s+library (\d+\.\d+)")
    assert len(detuning) == 6              # five detunings and the isolated neutral
    for closed, solved in arrangements + detuning:
        assert solved == pytest.approx(closed, abs=1.5e-6 if closed > 0.5 or closed == solved else 1e-4)


def test_the_table_of_the_three_arrangements(page):
    text, namespace, _ = page
    z_s, z_m, z_a = namespace["z_s"], namespace["z_m"], namespace["z_a"]
    assert f"| $Z_s/(Z_s + Z_A)$ | {abs(z_s / (z_s + z_a)):.3f} |" in text
    assert f"| $(Z_s - Z_m)/(Z_s + Z_A)$ | {abs((z_s - z_m) / (z_s + z_a)):.3f} |" in text


def test_the_detuning_table(page):
    text, namespace, _ = page
    r_ab, d = namespace["r_ab"], 0.03
    assert r_ab == pytest.approx(0.316, abs=5e-4)
    rows = dict(re.findall(r"^\| (−?[\d.]+) \| [^|]+\| ([\d.]+) \| [\d.]+ \|$", text, re.M))
    assert set(rows) == {"1", "0.5", "0.1", "0", "−0.05", "−0.1"}
    for label, shown in rows.items():
        v = float(label.replace("−", "-"))
        ratio = math.sqrt((d**2 + (v - 0.5) ** 2) / (d**2 + v**2))
        assert float(shown) == pytest.approx(ratio, abs=0.006 if ratio < 10 else 0.06), label
    products = dict(re.findall(r"^\| (−?[\d.]+) \| [^|]+\| [\d.]+ \| ([\d.]+) \|$", text, re.M))
    for label, shown in products.items():
        v = float(label.replace("−", "-"))
        value = 0.316 * math.sqrt((d**2 + (v - 0.5) ** 2) / (d**2 + v**2))
        assert float(shown) == pytest.approx(value, abs=0.006), label


def test_the_feeder_table(page):
    text, namespace, _ = page
    cases = namespace["cases"]
    shown = {}
    for name, net in cases.items():
        table = gi.earth_current_contributions(net, "F")
        row = net.res_fault("F").to_dicts()[0]
        shown[name] = (table, row)
    factors = [f"{value:.3f}" for value in shown["isolated"][0]["r_S"]]
    assert factors == ["0.340", "0.315", "0.246", "0.000"]
    for bus, factor in zip(("UW", "S1", "S2"), factors[:3], strict=True):
        assert re.search(rf"^\| {bus} \| {factor} \|", text, re.M)
    currents = {name: [f"{value:.2f}" for value in table["I_S_A"]] for name, (table, _) in shown.items()}
    assert currents["isolated"] == ["4.08", "8.16", "9.52", "5.44"]
    assert currents["resonant"] == ["24.50", "8.16", "9.52", "5.44"]
    assert "| UW | 0.340 | 4.08 A | 24.50 A | 1000.0 A | 1.39 A | 8.33 A | 340.1 A |" in text
    assert "| S1 | 0.315 | 8.16 A | 8.16 A | 8.16 A | 2.57 A | 2.57 A | 2.57 A |" in text
    assert "| S2 | 0.246 | 9.52 A | 9.52 A | 9.52 A | 2.34 A | 2.34 A | 2.34 A |" in text
    assert [f"{shown[n][1]['I_F_A']:.2f}" for n in cases] == ["27.21", "1.59", "1000.37"]
    assert [f"{shown[n][1]['I_E_A']:.2f}" for n in cases] == ["6.28", "3.52", "339.40"]
    assert [f"{shown[n][1]['U_E_V']:.2f}" for n in cases] == ["9.14", "5.12", "493.91"]
    assert [f"{shown[n][1]['r']:.3f}" for n in cases] == ["0.231", "2.218", "0.339"]
    assert "| $r$ | | | | | 0.231 | **2.218** | 0.339 |" in text
    assert "| $U_E$ | | | | | 9.14 V | 5.12 V | 493.9 V |" in text


def test_currents_of_one_phase_stay_below_the_largest_factor(page):
    _, namespace, _ = page
    table = gi.earth_current_contributions(namespace["cases"]["isolated"], "F")
    row = namespace["cases"]["isolated"].res_fault("F").to_dicts()[0]
    assert row["r"] < max(table["r_S"])
    assert len({round(angle, 9) for angle in table["I_S_degree"]}) == 1      # all capacitive


def test_the_fault_station_at_the_coil_sees_the_same_potential_for_every_neutral(page):
    _, namespace, _ = page
    values = []
    for kind in ("isolated", "resonant", "low-impedance"):
        net = namespace["feeder"]()
        net.faults["F"].bus = "UW"
        if kind == "isolated":
            gi.create_isolated_neutral(name="n", bus="UW", voltage_level=20.0, network=net)
        elif kind == "resonant":
            gi.create_resonant_neutral(name="n", bus="UW", voltage_level=20.0, coil_current=28.0, network=net)
        else:
            gi.create_low_impedance_neutral(name="n", bus="UW", voltage_level=20.0, current=1000.0, network=net)
        gi.run_fault(net, "F")
        values.append(net.res_fault("F").to_dicts()[0]["U_E_V"])
    assert values[1] == pytest.approx(values[0], rel=1e-12) and values[2] == pytest.approx(values[0], rel=1e-12)
    assert values[0] == pytest.approx(3.01, abs=5e-3)


class TestTunedCoil:
    """Equation (20): no fault current, and U_E = Z_E * r_AB * I_C / 2 -- three ways."""

    def _cable(self, page):
        return page[1]["cable"]

    def _closed_form(self, page):
        _, namespace, _ = page
        z_s, z_m, z_a, z_b = namespace["z_s"], namespace["z_m"], namespace["z_a"], namespace["z_b"]
        return abs(namespace["I_C"] / 2 * z_b * (z_s - z_m) / (z_s + z_a + z_b))

    def test_the_library_with_a_tuned_lossless_coil(self, page):
        _, namespace, _ = page
        net = namespace["two_stations"](self._cable(page))
        gi.create_resonant_neutral(name="coil", bus="A", voltage_level=20.0, coil_current=namespace["I_C"],
                                   network=net)
        gi.run_fault(net, "F")
        row = net.res_fault("F").to_dicts()[0]
        assert row["I_F_A"] == pytest.approx(0.0, abs=1e-12)
        assert row["U_E_V"] == pytest.approx(self._closed_form(page), rel=1e-12)
        assert row["U_E_V"] == pytest.approx(3.2003, abs=5e-5)

    @pytest.mark.parametrize("pieces", [1, 4, 40])
    def test_the_distributed_currents_written_out_along_the_cable(self, page, pieces):
        """The coil current enters at A; every piece of the cable hands its charging current to the conductors.

        Built without capacitance and without a neutral-point element: the cable in ``pieces`` sections with
        unearthed joints, each section's charging current as two plain sources at its ends, the coil as a
        plain source at A. The result does not depend on the number of pieces.
        """
        _, namespace, _ = page
        plain = self._cable(page).model_copy(update={"name": "plain", "c_shield_nf_per_km": None,
                                                     "voltage_level": None})
        joint = gi.BusType(name="joint", system_type="joint", voltage_level=20.0, impedance_formula="nan")
        net = gi.create_network(name="distributed", frequencies=[F])
        gi.create_bus(name="A", type=namespace["uw"], network=net)
        gi.create_bus(name="B", type=namespace["ons"], network=net)
        nodes = ["A"] + [f"M{k}" for k in range(1, pieces)] + ["B"]
        for name in nodes[1:-1]:
            gi.create_bus(name=name, type=joint, network=net)
        share = 1j * namespace["I_C"] / pieces / 2
        for k, (a, b) in enumerate(zip(nodes[:-1], nodes[1:], strict=True)):
            gi.create_branch(name=f"K{k}", type=plain, from_bus=a, to_bus=b, length=5.0 / pieces, network=net)
            for end in (a, b):
                gi.create_source(name=f"C{k}{end}", bus=end, values={F: share}, network=net)
        gi.create_source(name="coil", bus="A", values={F: -1j * namespace["I_C"]}, network=net)
        gi.create_fault(name="F", bus="B", network=net)
        gi.run_fault(net, "F")
        row = net.res_fault("F").to_dicts()[0]
        assert row["I_F_A"] == pytest.approx(0.0, abs=1e-11)
        assert row["U_E_V"] == pytest.approx(self._closed_form(page), rel=1e-10)

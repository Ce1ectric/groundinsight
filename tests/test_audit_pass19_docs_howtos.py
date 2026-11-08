# tests/test_audit_pass19_docs_howtos.py
"""
Audit pass 19 -- the how-to guides run, print what they say, and are right.

Finding IDs: DOC5/DOC12 (no task-oriented guides), DOC6 (scalings default,
source coverage, DC rules), DOC8 (EPR limit vs. touch voltage), DOC10
(``frequency_Hz`` of the outage comparison), F7/F8 (fault scalings and source
coverage), PH11 (the searches and sweeps scale the bus rho only).

Every guide ``docs/howto/<name>.md`` includes a script ``docs/snippets/<name>.py``
and states its output in a ``text`` block titled "Output of <name>.py". For
each guide the tests

* run the script as ``__main__`` and compare its standard output with that
  block, line by line -- the stated output is the printed one;
* collect the diagnostics the script raises and require the guide to explain
  every code it shows the reader;
* recompute the numbers a second, independent way: closed forms of a two-bus
  line, a hand-merged DC chain, the independent reference solver of the test
  suite (``tests/_reference_mna.py``) with impedances evaluated here from the
  formulas, the known path counts of grid graphs, the IEC 60909/60949 formulas.
"""

import contextlib
import io
import math
import re
import runpy
import sys
import warnings
from pathlib import Path

import polars as pl
import pytest

import groundinsight as gi
from groundinsight.diagnostics import collecting
from tests import _reference_mna as ref

ROOT = Path(__file__).resolve().parent.parent
DOCS = ROOT / "docs"
SNIPPETS = DOCS / "snippets"


def _page(relative):
    """Text of a documentation page."""
    return (DOCS / relative).read_text(encoding="utf-8")


def _flat(text):
    """Collapse whitespace so a sentence wrapped over lines matches."""
    return " ".join(text.split())


def run_snippet(name, monkeypatch=None, tmp_path=None):
    """
    Run ``docs/snippets/<name>`` as a script.

    Returns
    -------
    tuple of (dict, str, list)
        The script's namespace, its standard output and the diagnostics it
        raised (records of every package warning, in order).
    """
    buffer = io.StringIO()
    with collecting() as records, warnings.catch_warnings():
        warnings.simplefilter("ignore")
        with contextlib.redirect_stdout(buffer):
            namespace = runpy.run_path(str(SNIPPETS / name), run_name="__main__")
    # A run (Result.diagnostics) and a collecting() block of the script itself
    # keep their records; gather those too.
    found = list(records)
    values = list(namespace.values())
    values += [v for d in namespace.values() if isinstance(d, dict) for v in d.values()]
    for value in values:
        if isinstance(value, gi.Result):
            found.extend(value.diagnostics)
        elif isinstance(value, list) and value and all(isinstance(v, gi.Diagnostic) for v in value):
            found.extend(value)
    return namespace, buffer.getvalue(), found


def stated_output(page, script):
    """The ``text`` block titled "Output of <script>" on a guide page."""
    text = _page(page)
    match = re.search(
        r'^```text title="Output of ' + re.escape(script) + r'"\n(.*?)^```', text, re.M | re.S
    )
    assert match, f"{page} states no output block for {script}"
    return match.group(1)


def assert_output_matches(page, script, stdout):
    """The printed lines equal the stated ones (trailing blanks ignored)."""
    printed = [line.rstrip() for line in stdout.strip("\n").splitlines()]
    stated = [line.rstrip() for line in stated_output(page, script).strip("\n").splitlines()]
    assert printed == stated


def assert_codes_explained(page, records):
    """Every diagnostic code the script shows is named on the page."""
    text = _page(page)
    codes = {r.code for r in records}
    missing = sorted(c for c in codes if f"`{c}`" not in text)
    assert missing == [], f"{page} does not explain {missing}"


def _phasor(value):
    """A ComplexNumber (or complex) as complex."""
    return complex(getattr(value, "real", value), getattr(value, "imag", 0.0))


def _carson_trefoil(f, rho, length):
    """
    Self and mutual impedance of the trefoil cable type of the data-sheet how-to,
    evaluated here with ``math`` (second evaluation of the formula).
    """
    if f == 0.0:
        return complex(0.727 / 3 * length, 0.0), 0j
    earth = math.pi ** 2 * 1e-4 * f
    x = 4 * math.pi * 1e-4 * f * math.log(658 * math.sqrt(rho / f) / 0.03151272)
    return complex(0.727 / 3 + earth, x) * length, complex(earth, x) * length


def _ring_electrode(rho):
    """Station electrode of the guides: ring around 10 m x 10 m, 2*rho/(3*1.13*sqrt(100))."""
    return 2 * rho / (3 * 1.13 * math.sqrt(100.0))


# ---------------------------------------------------------------------------
# harmonics, fault scalings and DC
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def harmonics():
    return run_snippet("harmonics.py")


def test_harmonics_output_is_the_stated_one(harmonics):
    _ns, stdout, _records = harmonics
    assert_output_matches("howto/harmonics.md", "harmonics.py", stdout)


def test_harmonics_explains_every_code_it_shows(harmonics):
    _ns, _stdout, records = harmonics
    codes = {r.code for r in records}
    assert {"SCALINGS_INCOMPLETE", "SOURCE_FREQUENCY_GAP", "SOURCE_FREQUENCY_OUTSIDE_NETWORK",
            "DC_LIMIT"} <= codes
    assert_codes_explained("howto/harmonics.md", records)


def test_harmonics_two_bus_line_by_closed_form(harmonics):
    """U_E and r of the two-station line: I_E = I*(Z_s - Z_m)/(Z_UW + Z_S + Z_s), U_E = Z_S*I_E."""
    ns, _stdout, _records = harmonics
    figures = ns["figures"]
    rho, length = 100.0, ns["LENGTH_KM"]
    z_uw, z_s = 0.5, _ring_electrode(rho)
    rms = 0.0
    for f, current in ns["SOURCE_VALUES"].items():
        zs, zm = _carson_trefoil(f, rho, length)
        i_earth = current * (zs - zm) / (z_uw + z_s + zs)
        u_e = abs(z_s * i_earth)
        row = figures.filter(pl.col("frequency_Hz") == f).row(0, named=True)
        assert row["U_E_V"] == pytest.approx(u_e, rel=1e-9)
        assert row["r_current"] == pytest.approx(abs(i_earth) / current, rel=1e-9)
        assert row["I_F_A"] == pytest.approx(current, rel=1e-12)
        rms += u_e ** 2
    rms_row = figures.filter(pl.col("is_rms")).row(0, named=True)
    assert rms_row["U_E_V"] == pytest.approx(math.sqrt(rms), rel=1e-9)


def test_harmonics_excluded_frequency_leaves_the_fundamental(harmonics):
    """With scalings 1/0/0 the total RMS is the 50 Hz value alone."""
    ns, _stdout, _records = harmonics
    fifty = ns["figures"].filter(pl.col("frequency_Hz") == 50.0)["U_E_V"][0]
    assert ns["u_e_rms_fundamental_only"] == pytest.approx(fifty, rel=1e-12)


def test_harmonics_dc_chain_by_merging_the_bond(harmonics):
    """The ideal bond A-B is one node: a 2x2 nodal system by hand gives u_A = u_B and u_C."""
    ns, _stdout, _records = harmonics
    z_a, z_b, z_c, r_bc, current = 0.8, 12.0, 3.5, 0.35, 1000.0
    y_ab = 1 / z_a + 1 / z_b
    # earth-side frame of the package: +I at the source bus A, -I at the fault bus C
    a11, a12, a22 = y_ab + 1 / r_bc, -1 / r_bc, 1 / z_c + 1 / r_bc
    det = a11 * a22 - a12 * a12
    u_ab = (current * a22 - a12 * (-current)) / det
    u_c = (a11 * (-current) - a12 * current) / det
    u = ns["dc_potentials"]
    assert u["A"] == pytest.approx(u_ab, rel=1e-5)
    assert u["B"] == pytest.approx(u_ab, rel=1e-5)
    assert u["C"] == pytest.approx(u_c, rel=1e-5)
    assert ns["dc_substitute_ohm"] == pytest.approx(math.sqrt(sys.float_info.epsilon) * r_bc, rel=1e-6)


def test_harmonics_page_states_the_scaling_rules():
    text = _flat(_page("howto/harmonics.md"))
    for phrase in ("scaled with 1.0", "`0.0`", "RMS", "`Fault.scaling_at", "`is_rms`",
                   "0 Hz", "stand-in", "ValueError"):
        assert phrase in text, phrase


# ---------------------------------------------------------------------------
# parameter sweeps and statistics
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def sweeps():
    return run_snippet("sweeps.py")


def test_sweeps_output_is_the_stated_one(sweeps):
    _ns, stdout, _records = sweeps
    assert_output_matches("howto/sweeps.md", "sweeps.py", stdout)
    assert_codes_explained("howto/sweeps.md", sweeps[2])


def _feeder_reference(ns, frequency, station_z=None, fault="F"):
    """Reference-solver input of the feeder, with the station electrodes overridden."""
    data = ref.reference_network(ns["net"], frequency, fault)
    if station_z is not None:
        for name, z in station_z.items():
            data["buses"][name]["z"] = complex(z)
    return data


def test_sweeps_soil_points_by_the_reference_solver(sweeps):
    """EPR of the fault bus per soil point: reference solver with Z_B(rho) evaluated here."""
    ns, _stdout, _records = sweeps
    frame = ns["soil"].buses()
    for rho in ns["RHO_VALUES"]:
        z = _ring_electrode(rho)
        data = _feeder_reference(ns, 50.0, {b: z for b in ns["STATIONS"]})
        expected = abs(ref.solve_oneway(data)["u"][ns["FAULT_BUS"]])
        row = frame.filter(
            (pl.col("rho_Ohm_m") == rho) & (pl.col("bus_name") == ns["FAULT_BUS"])
            & (pl.col("frequency_Hz") == 50.0)
        )
        assert row["EPR_V"][0] == pytest.approx(expected, rel=1e-9)


def test_sweeps_rho_f_points_by_the_reference_solver(sweeps):
    """A rho-f point puts Z = k1*rho + (k2+jk3)*f + (k4+jk5)*rho*f at the station."""
    ns, _stdout, _records = sweeps
    frame = ns["characteristic"].buses()
    for label, (k1, k2, k3, k4, k5) in ns["K_VECTORS"].items():
        for f in ns["net"].frequencies:
            z = k1 * 100.0 + complex(k2, k3) * f + complex(k4, k5) * 100.0 * f
            data = _feeder_reference(ns, f, {ns["FAULT_BUS"]: z})
            expected = abs(ref.solve_oneway(data)["u"][ns["FAULT_BUS"]])
            row = frame.filter(
                (pl.col("point") == label) & (pl.col("bus_name") == ns["FAULT_BUS"])
                & (pl.col("frequency_Hz") == f)
            )
            assert row["EPR_V"][0] == pytest.approx(expected, rel=1e-9)


def test_sweeps_leave_the_network_as_found(sweeps):
    ns, _stdout, _records = sweeps
    assert ns["net"].model_dump_json() == ns["before"]


def test_sweeps_statistics_by_hand(sweeps):
    """summarize/classify against plain Python over the same values."""
    ns, _stdout, _records = sweeps
    values = sorted(ns["fault_bus_rms"]["EPR_V"].to_list())
    summary = ns["summary"].row(0, named=True)
    assert summary["n"] == len(values)
    assert summary["min"] == pytest.approx(values[0]) and summary["max"] == pytest.approx(values[-1])
    assert summary["mean"] == pytest.approx(sum(values) / len(values))
    edges = ns["EDGES_V"]
    counts = ns["classes"]["class"].value_counts()
    expected = {
        ns["LABELS"][0]: sum(v <= edges[0] for v in values),
        ns["LABELS"][1]: sum(edges[0] < v <= edges[1] for v in values),
        ns["LABELS"][2]: sum(v > edges[1] for v in values),
    }
    got = dict(zip(counts["class"], counts["count"], strict=True))
    assert {k: v for k, v in expected.items() if v} == got


def test_sweeps_page_states_the_rules():
    text = _flat(_page("howto/sweeps.md"))
    for phrase in ("`bus_rho`", "`bus_impedance`", "`rho_f_points`", "`summarize`", "`classify`",
                   "`on_error`", "left as it was", "specific_earth_resistance` of the branches"):
        assert phrase in text, phrase


# ---------------------------------------------------------------------------
# outage and what-if studies
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def outage():
    return run_snippet("outage.py")


def test_outage_output_is_the_stated_one(outage):
    _ns, stdout, records = outage
    assert_output_matches("howto/outage.md", "outage.py", stdout)
    assert_codes_explained("howto/outage.md", records)


def test_outage_scenarios_by_the_reference_solver(outage):
    """EPR of the fault bus per scenario: the reference solver with the same elements inactive."""
    ns, _stdout, _records = outage
    frame = ns["compared"].filter(
        (pl.col("bus_name") == ns["FAULT_BUS"]) & (pl.col("frequency_Hz") == "50.0")
    )
    for scenario in ns["SCENARIOS"]:
        data = ref.reference_network(ns["net"], 50.0, "F")
        for name in scenario.disabled_branches:
            data["branches"][name]["active"] = False
        for name in scenario.disabled_buses:
            data["buses"][name]["active"] = False
        solution = ref.solve_oneway(data)
        expected = abs(solution["u"].get(ns["FAULT_BUS"], 0.0))
        value = frame.filter(pl.col("scenario") == scenario.name)["value"][0]
        assert value == pytest.approx(expected, rel=1e-9, abs=1e-9)


def test_outage_failure_is_recorded_and_the_topology_restored(outage):
    """The failing scenario is recorded; every element is back in service."""
    ns, _stdout, _records = outage
    assert set(ns["study"].failures) == {"fault bus out"}
    net = ns["net"]
    assert all(b.active for b in net.buses.values())
    assert all(b.active for b in net.branches.values())
    # second way: the outage_context solve equals the study's scenario value
    k2 = ns["compared"].filter(
        (pl.col("bus_name") == ns["FAULT_BUS"]) & (pl.col("frequency_Hz") == "RMS")
        & (pl.col("scenario") == "K2 out")
    )["value"][0]
    assert ns["k2_out_epr"] == pytest.approx(k2, rel=1e-12)


def test_outage_page_states_the_text_frequency_and_the_null_percentage():
    text = _flat(_page("howto/outage.md"))
    for phrase in ('`"RMS"`', "`delta_pct_vs_base`", "null", '`on_error="record"`',
                   "`outage_context`", "0 V"):
        assert phrase in text, phrase


# ---------------------------------------------------------------------------
# admissible soil resistivity
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def inverse_rho():
    return run_snippet("inverse_rho.py")


def test_inverse_rho_output_is_the_stated_one(inverse_rho):
    _ns, stdout, records = inverse_rho
    assert_output_matches("howto/inverse-rho.md", "inverse_rho.py", stdout)
    assert_codes_explained("howto/inverse-rho.md", records)


def _epr_rms_at(ns, scale):
    """Total RMS EPR of the fault bus with every station's rho scaled, by the reference solver."""
    total = 0.0
    for f in ns["net"].frequencies:
        z = _ring_electrode(100.0 * scale)
        data = _feeder_reference(ns, f, {b: z for b in ns["STATIONS"]})
        total += abs(ref.solve_oneway(data)["u"][ns["FAULT_BUS"]]) ** 2
    return math.sqrt(total)


def test_inverse_rho_bracket_by_the_reference_solver(inverse_rho):
    """At c_max the EPR is admissible, at the upper end of the bracket it is not."""
    ns, _stdout, _records = inverse_rho
    result = ns["result"]
    assert result["converged"] and result["status"] == "converged"
    c_lo, c_hi = result["c_bracket"]
    assert _epr_rms_at(ns, result["c_max"]) == pytest.approx(result["u_epr_rms_at_c_max"], rel=1e-9)
    assert _epr_rms_at(ns, c_lo) <= ns["U_LIMIT_V"] < _epr_rms_at(ns, c_hi)
    assert (c_hi - c_lo) / c_lo <= 1e-3


def test_inverse_rho_f_by_the_reference_solver(inverse_rho):
    """At c_max of the rho-f search no swept fault bus exceeds the limit (second solver)."""
    ns, _stdout, _records = inverse_rho
    result = ns["rho_f"]
    k = result["k_max"]
    worst = 0.0
    for bus in ns["STATIONS"]:
        total = 0.0
        for f in ns["net"].frequencies:
            z = k[0] * 100.0 + complex(k[1], k[2]) * f + complex(k[3], k[4]) * 100.0 * f
            data = _feeder_reference(ns, f, {b: z for b in ns["STATIONS"]})
            data["fault_bus"] = bus
            total += abs(ref.solve_oneway(data)["u"][bus]) ** 2
        worst = max(worst, math.sqrt(total))
    assert worst == pytest.approx(result["max_epr_rms_at_c_max"], rel=1e-9)
    assert worst <= ns["U_LIMIT_V"]


def test_inverse_rho_page_states_how_to_choose_the_limit():
    text = _flat(_page("howto/inverse-rho.md"))
    assert "en50522-check.md" in text
    for status in ("converged", "bracket_within_tol_on_entry", "bracket_fully_admissible",
                   "max_iter_reached", "non_monotone"):
        assert f'`"{status}"`' in text, status
    assert "`c_bracket`" in text and "`converged`" in text
    assert "touch voltage" in text and "not the touch voltage" in text


# ---------------------------------------------------------------------------
# import from pandapower
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def pp_topology():
    pytest.importorskip("pandapower")
    return run_snippet("pandapower_topology.py")


@pytest.fixture(scope="module")
def pp_shortcircuit():
    pytest.importorskip("pandapower")
    return run_snippet("pandapower_shortcircuit.py")


def test_pandapower_topology_output_is_the_stated_one(pp_topology):
    _ns, stdout, records = pp_topology
    assert_output_matches("howto/pandapower.md", "pandapower_topology.py", stdout)
    assert_codes_explained("howto/pandapower.md", records)


def test_pandapower_topology_against_the_pandapower_tables(pp_topology):
    """Second way: the imported buses and lengths read straight from net_pp."""
    ns, _stdout, _records = pp_topology
    net_pp, net = ns["net_pp"], ns["net"]
    mv = set(net_pp.bus[net_pp.bus["vn_kv"] == 20.0]["name"])
    assert set(net.buses) == mv
    for _idx, line in net_pp.line.iterrows():
        if line["name"] in net.branches:
            assert net.branches[line["name"]].length == pytest.approx(line["length_km"])
    # the derived phase formula is (r + j*x)*l with pandapower's r and x
    line = net_pp.line[net_pp.line["name"] == "Line 2"].iloc[0]
    z = net.branches["Line 2"].phase_impedance[50.0]
    assert complex(z.real, z.imag) == pytest.approx(
        complex(line["r_ohm_per_km"], line["x_ohm_per_km"]) * line["length_km"]
    )
    line = net_pp.line[net_pp.line["name"] == "Line 3"].iloc[0]
    assert ns["line_3"].phase_impedance_formula == (
        f"({float(line['r_ohm_per_km'])!r} + I * {float(line['x_ohm_per_km'])!r}) * l"
    )


def test_open_phase_conductor_by_a_nan_phase_formula(pp_topology):
    """The tip of the guide: a 'nan' phase formula opens the phase path, the screen stays."""
    ns, _stdout, _records = pp_topology
    closed, opened = ns["phase_currents_closed"], ns["phase_currents_open"]
    assert abs(closed["Line 3"]) > 1.0
    assert abs(opened["Line 3"]) < 1e-9
    assert abs(opened["Line 4"]) == pytest.approx(1000.0, rel=1e-9)  # the only route left
    # the screen of Line 3 still bonds the stations
    assert ns["net"].branches["Line 3"].active


def test_pandapower_shortcircuit_output_is_the_stated_one(pp_shortcircuit):
    _ns, stdout, records = pp_shortcircuit
    assert_output_matches("howto/pandapower.md", "pandapower_shortcircuit.py", stdout)
    assert_codes_explained("howto/pandapower.md", records)


def test_pandapower_shortcircuit_against_iec60909_by_hand(pp_shortcircuit):
    """kappa, i_p and I_th of the fault bus from pandapower's raw results and IEC 60909-0."""
    ns, _stdout, _records = pp_shortcircuit
    net_pp, frame = ns["net_pp"], ns["sc"]
    t_k, f = 0.5, 50.0
    for idx, raw in net_pp.res_bus_sc.iterrows():
        row = frame.filter(pl.col("pp_bus_index") == idx).row(0, named=True)
        i_k = raw["ikss_ka"] * 1000.0
        r_x = (2 * raw["rk_ohm"] + raw["rk0_ohm"]) / (2 * raw["xk_ohm"] + raw["xk0_ohm"])
        kappa = 1.02 + 0.98 * math.exp(-3.0 * r_x)
        m = (math.exp(4 * f * t_k * math.log(kappa - 1)) - 1) / (2 * f * t_k * math.log(kappa - 1))
        assert row["i_k_a"] == pytest.approx(i_k, rel=1e-12)
        assert row["r_to_x"] == pytest.approx(r_x, rel=1e-12)
        assert row["kappa"] == pytest.approx(kappa, rel=1e-12)
        assert row["i_p_a"] == pytest.approx(kappa * math.sqrt(2) * i_k, rel=1e-12)
        assert row["m"] == pytest.approx(m, rel=1e-9)
        assert row["i_th_a"] == pytest.approx(i_k * math.sqrt(m + 1.0), rel=1e-9)


def test_pandapower_page_states_the_limits_of_the_import():
    text = _flat(_page("howto/pandapower.md"))
    for phrase in ("`switch_branch_type`", "math.isclose", "rel_tol=1e-9", "R₁ + jX₁", "Z_red",
                   "exact only", "s_sc_max_mva", "x0x_max", "r0x0_max", "r0_ohm_per_km",
                   "`derive_phase_impedance`", "line switch"):
        assert phrase in text, phrase


# ---------------------------------------------------------------------------
# thermal limits
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def thermal():
    return run_snippet("thermal.py")


def test_thermal_output_is_the_stated_one(thermal):
    _ns, stdout, records = thermal
    assert_output_matches("howto/thermal.md", "thermal.py", stdout)
    assert_codes_explained("howto/thermal.md", records)
    assert "THERMAL_DC_EXCLUDED" in {r.code for r in records}


def _iec60949_k(material, theta_i, theta_f):
    data = gi.IEC60949_MATERIALS[material]
    return data["K"] * math.sqrt(math.log((theta_f + data["beta"]) / (theta_i + data["beta"])))


def test_thermal_conductor_check_by_hand(thermal):
    """I_adm = k*S/sqrt(t_k) (IEC 60949), I_th = I*sqrt(m + n) (IEC 60909-0)."""
    ns, _stdout, _records = thermal
    t_k, f, r_x = ns["T_K_S"], 50.0, ns["R_TO_X"]
    kappa = 1.02 + 0.98 * math.exp(-3.0 * r_x)
    m = (math.exp(4 * f * t_k * math.log(kappa - 1)) - 1) / (2 * f * t_k * math.log(kappa - 1))
    for row in ns["conductors"].iter_rows(named=True):
        k = _iec60949_k("Cu", 20.0, gi.final_temperature("Cu", "XLPE"))
        assert row["k"] == pytest.approx(k, rel=1e-12)
        assert row["I_admissible_A"] == pytest.approx(k * row["cross_section_mm2"] / math.sqrt(t_k), rel=1e-12)
        assert row["I_th_A"] == pytest.approx(row["I_ac_rms_A"] * math.sqrt(m + 1.0), rel=1e-9)
        assert row["utilization"] == pytest.approx(row["I_th_A"] / row["I_admissible_A"], rel=1e-12)


def test_thermal_node_currents_are_the_result_currents(thermal):
    """Earthing conductor carries I_inj, the electrode I_bus times its split."""
    ns, _stdout, _records = thermal
    net, fault_bus = ns["net"], ns["FAULT_BUS"]
    rms = net.res_buses(fault="F").filter(pl.col("is_rms") & (pl.col("bus_name") == fault_bus)).row(0, named=True)
    nodes = ns["nodes"].filter(pl.col("bus_name") == fault_bus)
    conductor = nodes.filter(pl.col("element") == "earthing_conductor").row(0, named=True)
    electrode = nodes.filter(pl.col("element") == "electrode").row(0, named=True)
    assert conductor["I_rms_A"] == pytest.approx(rms["I_inj_A"], rel=1e-12)
    assert electrode["I_rms_A"] == pytest.approx(rms["I_bus_A"], rel=1e-12)
    assert electrode["I_conductor_A"] == pytest.approx(rms["I_bus_A"] * electrode["current_split"], rel=1e-12)


def test_thermal_page_states_the_tables_and_rules():
    text = _flat(_page("howto/thermal.md"))
    for phrase in ("`t_k_s`", "`IEC60949_MATERIALS`", "`FINAL_TEMPERATURES`", "`CABLE_INSULATION_LIMITS`",
                   "`final_temperature", "`THERMAL_DC_EXCLUDED`", "`THERMAL_DATA_INCOMPLETE`",
                   "`current_split`", "300 °C"):
        assert phrase in text, phrase


# ---------------------------------------------------------------------------
# transient studies
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def transient():
    return run_snippet("transient.py")


def test_transient_output_is_the_stated_one(transient):
    _ns, stdout, records = transient
    assert_output_matches("howto/transient.md", "transient.py", stdout)
    assert_codes_explained("howto/transient.md", records)
    assert "TRANSIENT_MODEL" in {r.code for r in records}


def test_transient_steady_state_by_closed_form(transient):
    """Steady-state RMS of both solvers against the two-bus closed form with and without coupling."""
    ns, _stdout, _records = transient
    w = 2 * math.pi * 50.0
    length = ns["LENGTH_KM"]
    zs = complex(0.2, w * ns["L_SELF_H_PER_KM"]) * length
    zm = complex(0.0, w * ns["L_MUTUAL_H_PER_KM"]) * length
    z_uw, z_s, current = 0.5, 2.0, 1000.0
    with_coupling = abs(z_s * current * (zs - zm) / (z_uw + z_s + zs))
    without = abs(z_s * current * zs / (z_uw + z_s + zs))
    assert ns["stationary_epr"] == pytest.approx(with_coupling, rel=1e-9)
    assert ns["rms_fft"] == pytest.approx(without, rel=1e-3)
    assert ns["rms_state_space"] == pytest.approx(with_coupling, rel=1e-3)


def test_transient_page_states_the_solver_choice():
    text = _flat(_page("howto/transient.md"))
    for phrase in ('`solver="fft"`', '`solver="state_space"`', "`fft_block_bytes`", "√2",
                   "`TRANSIENT_PHASE_SPLIT`", "`TRANSIENT_MODEL`", "`R_self_formula`", "`f_eval`"):
        assert phrase in text, phrase


# ---------------------------------------------------------------------------
# save, load and migrate
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def persistence(tmp_path_factory):
    import os

    cwd = os.getcwd()
    os.chdir(tmp_path_factory.mktemp("persistence"))
    try:
        return run_snippet("persistence.py")
    finally:
        os.chdir(cwd)


def test_persistence_output_is_the_stated_one(persistence):
    _ns, stdout, records = persistence
    assert_output_matches("howto/persistence.md", "persistence.py", stdout)
    assert_codes_explained("howto/persistence.md", records)


def test_persistence_round_trips_are_exact(persistence):
    """Second way: every reloaded network serialises to the same JSON as the original."""
    ns, _stdout, _records = persistence
    original = ns["net"].model_dump_json()
    assert ns["from_json"].model_dump_json() == original
    assert ns["from_database"].model_dump_json() == original
    assert ns["after_migration"].model_dump_json() == original


def test_persistence_migration_keeps_a_backup(persistence):
    ns, _stdout, _records = persistence
    assert ns["kind_before"] == "add_columns" and ns["kind_after"] == "none"
    assert ns["report"].migrated and ns["report"].backup_path.endswith(".bak")


def test_persistence_page_states_the_promise():
    text = _flat(_page("howto/persistence.md"))
    for phrase in ("`schema_version`", "`groundinsight_version`", "`gi.Database`", "`.bak`",
                   "`needs_migration`", "`migrate_database`", "earlier release", "newer",
                   "formula guard", "pickle", "UTF-8"):
        assert phrase in text, phrase


# ---------------------------------------------------------------------------
# large and meshed networks
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def performance():
    return run_snippet("performance.py")


def test_performance_output_is_the_stated_one(performance):
    _ns, stdout, records = performance
    assert_output_matches("howto/performance.md", "performance.py", stdout)
    assert_codes_explained("howto/performance.md", records)


def test_performance_path_counts_are_the_grid_graph_counts(performance):
    """Second way: simple corner-to-corner paths of a k x k grid, OEIS A007764."""
    ns, _stdout, _records = performance
    known = {2: 2, 3: 12, 4: 184, 5: 8512}
    for k, count in ns["path_counts"].items():
        assert count == known[k]


def test_performance_numbers_are_the_changelog_numbers():
    """Every measurement the guide quotes is a measurement of the CHANGELOG."""
    changelog = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    table = re.findall(r"^\|[^|]*\|\s*([\d.]+(?: s)? → [\d.]+ s)\s*\|", _page("howto/performance.md"), re.M)
    assert len(table) >= 5
    for pair in table:
        assert pair in changelog, pair


def test_performance_page_states_what_costs_time():
    text = _flat(_page("howto/performance.md"))
    for phrase in ('`phase_current_mode="paths"`', "`create_paths`", "`PathfinderSettings`",
                   "`max_paths`", "`fft_block_bytes`", "first solve", "result"):
        assert phrase in text, phrase


# ---------------------------------------------------------------------------
# diagnostics, logging and warnings
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def diagnostics_howto():
    return run_snippet("diagnostics.py")


def test_diagnostics_output_is_the_stated_one(diagnostics_howto):
    _ns, stdout, records = diagnostics_howto
    assert_output_matches("howto/diagnostics.md", "diagnostics.py", stdout)
    assert_codes_explained("howto/diagnostics.md", records)


def test_diagnostics_proxy_names_the_ring_branches(diagnostics_howto):
    """Second way: the branches of the only cycle, found here from the topology."""
    ns, _stdout, _records = diagnostics_howto
    net = ns["ring"]
    edges = {name: (b.from_bus, b.to_bus) for name, b in net.branches.items()}
    on_cycle = set()
    for name, (a, b) in edges.items():
        rest = {k: v for k, v in edges.items() if k != name}
        seen, todo = {a}, [a]
        while todo:
            bus = todo.pop()
            for x, y in rest.values():
                for u, v in ((x, y), (y, x)):
                    if u == bus and v not in seen:
                        seen.add(v)
                        todo.append(v)
        if b in seen:
            on_cycle.add(name)
    proxy = next(d for d in ns["result"].diagnostics if d.code == "PHASE_PROXY")
    named = {name for kind, name in proxy.elements if kind == "branch"}
    assert named == on_cycle and on_cycle


def test_diagnostics_check_finds_what_was_broken(diagnostics_howto):
    ns, _stdout, _records = diagnostics_howto
    first = {(d.code, d.severity, tuple(d.elements)) for d in ns["first_check"]}
    assert first == {("ISOLATED_BUS", "warning", (("bus", "X"),))}
    found = {(d.code, d.severity) for d in ns["issues"]}
    assert found == {("SOURCE_UNKNOWN_BUS", "error")}   # topology checks wait for consistency


def test_diagnostics_page_states_the_consumers():
    text = _flat(_page("howto/diagnostics.md"))
    for phrase in ("`Result.diagnostics`", "`gi.check_network`", "`collecting()`", "`set_log_level`",
                   "simplefilter", "-W error::", "`DiagnosticDTO`", "code", "never on the message"):
        assert phrase in text, phrase


# ---------------------------------------------------------------------------
# service layer and command line
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def service(tmp_path_factory):
    import os

    cwd = os.getcwd()
    os.chdir(tmp_path_factory.mktemp("service"))
    try:
        ns, stdout, records = run_snippet("service_layer.py")
        return ns, stdout, records, Path.cwd() / "projekt.json"
    finally:
        os.chdir(cwd)


def test_service_output_is_the_stated_one(service):
    _ns, stdout, records, _path = service
    assert_output_matches("howto/service-layer.md", "service_layer.py", stdout)
    assert_codes_explained("howto/service-layer.md", records)


def test_service_key_figures_are_the_library_figures(service):
    """Second way: the DTO's RMS row against res_fault of a direct run_fault."""
    from groundinsight.api.examples import build_quickstart

    ns, _stdout, _records, _path = service
    net = build_quickstart()
    gi.run_fault(net, "fault_at_remote_bus")
    rms = net.res_fault("fault_at_remote_bus").filter(pl.col("is_rms")).row(0, named=True)
    dto = ns["rms"]
    for column in ("I_F_A", "EPR_fault_V", "I_E_A", "U_E_V"):
        assert getattr(dto, column) == pytest.approx(rms[column], rel=1e-12)


def test_service_command_line_prints_the_stated_table(service):
    """The CLI on the saved project prints the table the guide shows."""
    import subprocess
    import os

    _ns, _stdout, _records, path = service
    env = dict(os.environ, PYTHONPATH=str(ROOT / "src"))
    proc = subprocess.run([sys.executable, "-m", "groundinsight", "run", str(path)],
                          capture_output=True, text=True, encoding="utf-8", errors="replace",
                          env=env, cwd=str(path.parent))
    assert proc.returncode == 0, proc.stderr
    stated = stated_output("howto/service-layer.md", "groundinsight run projekt.json")
    table = [line.rstrip() for line in stated.strip("\n").splitlines()]
    printed = [line.rstrip() for line in proc.stdout.splitlines()]
    start = printed.index(table[0])
    assert printed[start:start + len(table)] == table


def test_service_page_names_the_building_blocks():
    text = _flat(_page("howto/service-layer.md"))
    for phrase in ("`ProjectSession`", "session.apply(", "session.undo()", "`JobRunner`",
                   "session.submit_solve(", "`FaultResultDTO`", "json_schemas()", "groundinsight run",
                   "groundinsight check", "groundinsight-gui", "exit code"):
        assert phrase in text, phrase


# ---------------------------------------------------------------------------
# EN 50522 check -- the key figures of res_fault
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def en50522():
    return run_snippet("en50522_check.py")


def test_en50522_key_figures_are_the_stated_ones(en50522):
    """The last printed lines are the block the guide states for them."""
    _ns, stdout, _records = en50522
    stated = [
        line.rstrip()
        for line in stated_output("howto/en50522-check.md", "en50522_check.py, last lines")
        .strip("\n").splitlines()
    ]
    printed = [line.rstrip() for line in stdout.strip("\n").splitlines()]
    assert printed[-len(stated):] == stated


def test_en50522_page_maps_the_symbols_to_the_res_fault_columns():
    text = _flat(_page("howto/en50522-check.md"))
    for column in ("U_E_V", "Z_E_Ohm", "r_current", "I_E_A", "I_F_A"):
        assert f"`{column}` of `res_fault()`" in text or f"`{column}`, " in text, column
    assert "net.res_fault(fault)" in text


def test_en50522_key_figures_by_the_reference_solver(en50522):
    """
    Every figure of the frame, recomputed with the independent reference solver.

    ``I_F`` is the sum of the source currents, ``U_E`` the potential of the
    fault bus, ``Z_E`` the driving-point impedance of the earthing network at
    the fault bus, ``I_E`` the earth-return group of the electrode currents
    (brute-force half-plane split) and ``r = |I_E| / |I_F|``; the RMS row is
    the root sum of squares, without ratio and impedance.
    """
    ns, _stdout, _records = en50522
    net, fault_bus = ns["net"], ns["FAULT_BUS"]
    frame = ns["KEY_FIGURES"]
    rows = {row["frequency_Hz"]: row for row in frame.iter_rows(named=True) if not row["is_rms"]}
    squares = {"I_F_A": 0.0, "I_E_A": 0.0, "U_E_V": 0.0}
    for f in ns["FREQUENCIES"]:
        rd = ref.reference_network(net, f, "F")
        solution = ref.solve_oneway(rd)
        mapped = ref.to_package_frame(solution)
        i_earth, _group = ref.half_plane_split(mapped["ia"], rd["fault_bus"])
        expected = {
            "I_F_A": abs(solution["I_F"]),
            "U_E_V": abs(mapped["u"][fault_bus]),
            "Z_E_Ohm": abs(ref.driving_point_impedance(rd, fault_bus)),
            "I_E_A": abs(i_earth),
            "r_current": abs(i_earth) / abs(solution["I_F"]),
        }
        for column, value in expected.items():
            assert rows[f][column] == pytest.approx(value, rel=1e-9), (f, column)
        for column in squares:
            squares[column] += expected[column] ** 2
    rms = frame.filter(pl.col("is_rms")).row(0, named=True)
    for column, total in squares.items():
        assert rms[column] == pytest.approx(math.sqrt(total), rel=1e-9), column
    assert rms["r_current"] is None and rms["Z_E_Ohm"] is None
    assert rms["U_E_V"] == pytest.approx(ns["U_E_RMS"], rel=1e-12)

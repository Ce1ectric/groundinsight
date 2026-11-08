# tests/test_audit_pass19_docs_examples.py
"""
Audit pass 19 -- the worked examples of the documentation are computed, not typed.

Finding IDs: D7 (bus and branch types from data sheets, with the cable example
NA2XS(F)2Y 1x300 RM/25 computed two and more ways), DOC13 (example code lives in
``docs/snippets/`` and runs in the suite), PH5 (what ``phase_impedance_formula``
represents), PH4 (the EN 50522 quantities on the how-to page).

``docs/snippets/types_from_datasheets.py`` computes the shield-earth loop
impedance ``Z_s'``, the phase-shield mutual impedance ``Z_m'`` and the reduction
factor ``r = |1 - Z_m/Z_s|`` three ways: (a) the closed form with the equivalent
earth-return depth ``D_E = 658*sqrt(rho/f)``, (b) Pollaczek's earth-return
integral with ``scipy.integrate.quad``, (c) a one-branch network solved with
``run_fault``. The tests hold (a) = (c) to 1e-12, (a) against (b) within the
tolerance the page states, and check (b) itself twice more: against an
independent quadrature (mpmath, tanh-sinh) and against the analytic
low-frequency limit of the integral, Carson's closed form with the exact
constant ``2*exp(1/2 - gamma)/sqrt(omega*mu0/rho)``.

``docs/snippets/en50522_check.py`` reads the EN 50522 quantities of a solved
feeder; its numbers are checked against the fault-bus EPR of ``res_buses`` and
a driving-point impedance assembled here from the model's own impedances.
"""

import math
import runpy
import warnings
from pathlib import Path

import mpmath
import numpy as np
import pytest

from groundinsight.utils.impedance_calculator import DCLimitWarning, compute_impedance

ROOT = Path(__file__).resolve().parent.parent
SNIPPETS = ROOT / "docs" / "snippets"
TYPES_SNIPPET = SNIPPETS / "types_from_datasheets.py"
EN50522_SNIPPET = SNIPPETS / "en50522_check.py"

#: Tolerances the how-to page states for the closed form against the integral
#: (worked example, 50 Hz and 250 Hz, 100 Ohm*m, cable at 0.7 m).
TOL_R_EXAMPLE = 5e-5
#: ... and over the stated range (16.7 Hz ... 2.5 kHz, 10 ... 10 000 Ohm*m). The
#: log grid below contains the worst region (about 400 Hz in 10 Ohm*m soil, where
#: a 40 x 40 grid finds 1.3e-4).
TOL_R_GRID = 1.5e-4
TOL_Z_GRID = 5e-3
GRID_F = tuple(float(f) for f in np.geomspace(16.7, 2500.0, 12))
GRID_RHO = tuple(float(r) for r in np.geomspace(10.0, 10000.0, 7))


@pytest.fixture(scope="module")
def types_ns():
    """Namespace of the data-sheet snippet, run once per module."""
    return runpy.run_path(str(TYPES_SNIPPET), run_name="docs_snippet")


@pytest.fixture(scope="module")
def en50522_ns():
    """Namespace of the EN 50522 snippet, run once per module."""
    return runpy.run_path(str(EN50522_SNIPPET), run_name="docs_snippet")


def _pollaczek_mpmath(radius, depth, rho, f):
    """
    Pollaczek's earth-return impedance, evaluated with mpmath (independent oracle).

    Same integral as the snippet, a different quadrature (tanh-sinh at 30
    digits with the interval split at |m|, 10|m| and 1/(2h)) and a different
    Bessel implementation.

    Parameters
    ----------
    radius, depth : float
        Conductor radius and burial depth in m.
    rho : float
        Soil resistivity in Ohm*m.
    f : float
        Frequency in Hz.

    Returns
    -------
    complex
        The earth-return part of the conductor-earth loop impedance, Ohm/km.
    """
    mpmath.mp.dps = 30
    omega = 2 * mpmath.pi * f
    mu0 = 4e-7 * mpmath.pi
    m = mpmath.sqrt(1j * omega * mu0 / rho)

    def integrand(lam):
        s = mpmath.sqrt(lam**2 + m**2)
        return mpmath.exp(-2 * depth * s) / (lam + s)

    integral = mpmath.quad(integrand, [0, abs(m), 10 * abs(m), 1 / (2 * depth), mpmath.inf])
    bracket = mpmath.besselk(0, m * radius) - mpmath.besselk(0, m * 2 * depth) + 2 * integral
    return complex(1j * omega * mu0 / (2 * mpmath.pi) * bracket * 1000)


def _driving_point(net, bus, f):
    """
    Driving-point impedance of the earthing network at ``bus``.

    Assembled here from ``bus.impedance`` and ``branch.self_impedance`` (no
    coupling, no Thevenin loops) and solved with a dense solver -- shares no
    code with the package's solver.

    Parameters
    ----------
    net : Network
        A network whose impedances are evaluated.
    bus : str
        The bus to inject one ampere at.
    f : float
        Frequency in Hz.

    Returns
    -------
    complex
        ``(Y^-1)[bus, bus]`` in Ohm.
    """
    order = sorted(net.buses)
    index = {name: i for i, name in enumerate(order)}
    y = np.zeros((len(order), len(order)), dtype=complex)
    for name, b in net.buses.items():
        y[index[name], index[name]] += 1 / complex(b.impedance[f].real, b.impedance[f].imag)
    for branch in net.branches.values():
        z = branch.self_impedance[f]
        adm = 1 / complex(z.real, z.imag)
        i, k = index[branch.from_bus], index[branch.to_bus]
        y[i, i] += adm
        y[k, k] += adm
        y[i, k] -= adm
        y[k, i] -= adm
    rhs = np.zeros(len(order), dtype=complex)
    rhs[index[bus]] = 1.0
    return complex(np.linalg.solve(y, rhs)[index[bus]])


# --- (a) = (c): the closed form and the model ---------------------------------


def test_types_snippet_closed_form_equals_model(types_ns):
    """(a) closed form = (c) one-branch run_fault, to rounding, at 50 and 250 Hz.

    ``value`` reproduces ``|1 - Z_m/Z_s|`` exactly for one line, so the only
    difference left is floating point. The branch impedances the model
    evaluated from the formula strings must equal the closed form as well.
    """
    rows = types_ns["ROWS"]
    assert [row["f"] for row in rows] == [50.0, 250.0]
    for row in rows:
        assert abs(row["r_a"] - row["r_c"]) <= 1e-12, row
        for key in ("zs", "zm", "zred"):
            a, c = row[f"{key}_a"], row[f"{key}_c"]
            assert abs(a - c) <= 1e-12 * abs(a), (key, row["f"], a, c)
        # second way to the same number: r = R_s'/|Z_s'| for a concentric screen
        assert row["r_a"] == pytest.approx(types_ns["R_SCREEN"] / abs(row["zs_a"]), rel=1e-12)


def test_types_snippet_numbers_on_the_page(types_ns):
    """The rounded numbers the page prints are the snippet's numbers."""
    by_f = {row["f"]: row for row in types_ns["ROWS"]}
    assert by_f[50.0]["r_a"] == pytest.approx(0.703286, abs=5e-7)
    assert by_f[250.0]["r_a"] == pytest.approx(0.219869, abs=5e-7)
    assert by_f[50.0]["zs_a"] == pytest.approx(complex(0.77635, 0.68254), abs=5e-6)
    assert by_f[50.0]["zm_a"] == pytest.approx(complex(0.04935, 0.68254), abs=5e-6)
    assert by_f[50.0]["zred_a"] == pytest.approx(complex(0.44301, 0.38746), abs=5e-6)
    assert types_ns["R_SCREEN_MEAN"] == pytest.approx(17.825e-3, abs=1e-9)
    assert types_ns["earth_return_depth"](100.0, 50.0) == pytest.approx(930.6, abs=0.05)


# --- (a) against (b): how good is D_E? ----------------------------------------


def test_types_snippet_closed_form_against_pollaczek_example(types_ns):
    """|r(a) - r(b)| within the tolerance the page states for the example."""
    for row in types_ns["ROWS"]:
        assert abs(row["r_a"] - row["r_b"]) <= TOL_R_EXAMPLE, row
        # the reactances agree far better than the earth-return resistance
        assert abs(row["zm_a"].imag - row["zm_b"].imag) <= 1e-3 * abs(row["zm_b"].imag)
        assert abs(row["zred_a"] - row["zred_b"]) <= 1e-3 * abs(row["zred_b"])


def test_types_snippet_closed_form_against_pollaczek_grid(types_ns):
    """Over the stated rho-f grid the closed form stays within the page's bounds."""
    loop = types_ns["loop_impedance"]
    earth = types_ns["pollaczek_earth_impedance"]
    r_s, r_dc, depth = types_ns["R_SCREEN_MEAN"], types_ns["R_SCREEN"], types_ns["DEPTH"]
    worst_r = worst_z = 0.0
    for f in GRID_F:
        for rho in GRID_RHO:
            zs_a, zm_a = loop(r_dc, r_s, rho, f), loop(0.0, r_s, rho, f)
            zm_b = earth(r_s, depth, rho, f)
            zs_b = r_dc + zm_b
            worst_r = max(worst_r, abs(abs(1 - zm_a / zs_a) - abs(1 - zm_b / zs_b)))
            worst_z = max(worst_z, abs(zs_a - zs_b) / abs(zs_b), abs(zm_a - zm_b) / abs(zm_b))
    assert worst_r <= TOL_R_GRID
    assert worst_r > 1.2e-4  # the grid reaches the worst region the page names
    assert worst_z <= TOL_Z_GRID


@pytest.mark.parametrize("rho, f", [(100.0, 50.0), (10.0, 2500.0), (10000.0, 16.7)])
def test_pollaczek_quadrature_against_mpmath(types_ns, rho, f):
    """The snippet's scipy quadrature agrees with an independent mpmath one."""
    r_s, depth = types_ns["R_SCREEN_MEAN"], types_ns["DEPTH"]
    scipy_value = types_ns["pollaczek_earth_impedance"](r_s, depth, rho, f)
    oracle = _pollaczek_mpmath(r_s, depth, rho, f)
    assert abs(scipy_value - oracle) <= 1e-9 * abs(oracle)


def test_pollaczek_tends_to_carson_with_the_exact_constant(types_ns):
    """For a shallow conductor the integral approaches the analytic limit.

    The low-frequency limit of the earth-return integral is Carson's closed
    form with ``D_E = 2*exp(1/2 - gamma)/sqrt(omega*mu0/rho) = 658.87*sqrt(rho/f)``
    (658 on the page is the rounded constant). The remaining difference is of
    the order ``|m|*h`` and has to shrink with the depth.
    """
    exact = 2 * math.exp(0.5 - np.euler_gamma) / math.sqrt(8 * math.pi**2 * 1e-7)
    assert exact == pytest.approx(658.8716, abs=1e-4)
    r_s, rho, f = types_ns["R_SCREEN_MEAN"], 100.0, 50.0
    limit = complex(
        math.pi**2 * 1e-4 * f,
        4 * math.pi * 1e-4 * f * math.log(exact * math.sqrt(rho / f) / r_s),
    )
    gaps = [
        abs(types_ns["pollaczek_earth_impedance"](r_s, h, rho, f) - limit) / abs(limit)
        for h in (0.7, 0.07, 0.02)
    ]
    assert gaps[0] > gaps[1] > gaps[2]
    assert gaps[2] < 2e-5


# --- three-phase system, overhead line, electrodes -----------------------------


def test_trefoil_bundle_closed_form_is_exact(types_ns):
    """GMD closed form = exact screen-bundle reduction = groundinsight, trefoil.

    In trefoil every screen sees the same row sum of the screen impedance
    matrix, so the all-ones vector is an eigenvector and the bundle formula
    with ``R_s/3`` and ``GMR = (r_s*s^2)^(1/3)`` is exact (within the D_E
    model). The other two screens roughly halve r at 50 Hz.
    """
    single = {row["f"]: row["r_a"] for row in types_ns["ROWS"]}
    for row in types_ns["TREFOIL_ROWS"]:
        # identity: bundle formula (unrounded GMR) = exact matrix reduction
        assert abs(row["zs_gmd"] - row["zs_matrix"]) <= 1e-12 * abs(row["zs_matrix"])
        assert abs(row["zm_gmd"] - row["zm_matrix"]) <= 1e-12 * abs(row["zm_matrix"])
        # the formula string (GMR rounded to 0.01 um) = groundinsight, to rounding
        assert abs(row["r_formula"] - row["r_model"]) <= 1e-12
        assert abs(row["r_formula"] - row["r_gmd"]) <= 1e-8
        assert row["r_gmd"] < 0.5 * single[row["f"]]
    by_f = {row["f"]: row for row in types_ns["TREFOIL_ROWS"]}
    assert by_f[50.0]["r_model"] == pytest.approx(0.341570, abs=5e-7)
    assert by_f[250.0]["r_model"] == pytest.approx(0.080223, abs=5e-7)


def test_trefoil_share_of_the_faulted_screen(types_ns):
    """The share of the faulted core's screen that sets the bundle's thermal
    cross-section: 3x3 solve in the snippet against the 2x2 closed form that
    trefoil symmetry allows (I_2 = I_3)."""
    loop = types_ns["loop_impedance"]
    r_s, r_dc, d = types_ns["R_SCREEN_MEAN"], types_ns["R_SCREEN"], types_ns["D_OUTER"]
    for f, expected in ((50.0, 0.359), (250.0, 0.481)):
        a, b = loop(r_dc, r_s, 100.0, f), loop(0.0, d, 100.0, f)
        c1, c2 = loop(0.0, r_s, 100.0, f), loop(0.0, d, 100.0, f)
        det = a * (a + b) - 2 * b * b
        i1 = (-c1 * (a + b) + 2 * b * c2) / det
        i2 = (-a * c2 + b * c1) / det
        closed = abs(i1) / abs(i1 + 2 * i2)
        shares = types_ns["screen_current_shares"](types_ns["TREFOIL"], 0, r_s, r_dc, 100.0, f)
        assert shares[0] == pytest.approx(closed, rel=1e-12)
        assert shares[1] == pytest.approx(shares[2], rel=1e-12)
        assert shares[0] == pytest.approx(expected, abs=5e-4)
    assert types_ns["cable_system"].cross_section_mm2 == pytest.approx(25.0 / types_ns["SHARE_FAULTED_SCREEN"], abs=0.05)
    assert types_ns["cable_system"].cross_section_mm2 == 69.7


def test_flat_formation_needs_the_matrix_reduction(types_ns):
    """In flat formation the GMD formula is only approximate (stated on the page)."""
    loop = types_ns["loop_impedance"]
    bundle = types_ns["bundle_equivalent"]
    r_s, r_dc, d = types_ns["R_SCREEN_MEAN"], types_ns["R_SCREEN"], types_ns["D_OUTER"]
    flat = [(0.0, 0.0), (d, 0.0), (2 * d, 0.0)]
    for f, lower, upper in ((50.0, 1e-4, 1e-2), (250.0, 1e-2, 0.1)):
        zs_m, zm_m = bundle(flat, 1, r_s, r_dc, 100.0, f)
        gmr = (r_s**3 * d**2 * (2 * d) ** 2 * d**2) ** (1 / 9)
        gmd = (r_s * d * d) ** (1 / 3)
        r_gmd = abs(1 - loop(0.0, gmd, 100.0, f) / loop(r_dc / 3, gmr, 100.0, f))
        r_exact = abs(1 - zm_m / zs_m)
        assert lower < abs(r_gmd - r_exact) / r_exact < upper


def test_overhead_line_three_ways(types_ns):
    """Earth-wire line: (a) = (c) to rounding, (a) against Carson's integral (b)."""
    by_f = {row["f"]: row for row in types_ns["OHL_ROWS"]}
    for row in by_f.values():
        assert abs(row["r_a"] - row["r_c"]) <= 1e-12
    assert abs(by_f[50.0]["r_a"] - by_f[50.0]["r_b"]) <= 3e-4
    assert abs(by_f[250.0]["r_a"] - by_f[250.0]["r_b"]) <= 2e-3
    assert by_f[50.0]["r_a"] == pytest.approx(0.712107, abs=5e-7)


def test_carson_overhead_quadrature_against_mpmath(types_ns):
    """The snippet's Carson integral against an independent mpmath evaluation."""
    mpmath.mp.dps = 30
    h_i, h_j, x, d, rho, f = 12.0, 10.0, 0.0, 2.0, 100.0, 250.0
    omega = 2 * mpmath.pi * f
    mu0 = 4e-7 * mpmath.pi
    m = mpmath.sqrt(1j * omega * mu0 / rho)
    integral = mpmath.quad(
        lambda lam: mpmath.exp(-(h_i + h_j) * lam) / (lam + mpmath.sqrt(lam**2 + m**2)),
        [0, abs(m), 10 * abs(m), 1 / (h_i + h_j), mpmath.inf],
    )
    oracle = complex(
        (1j * omega * mu0 / (2 * mpmath.pi) * mpmath.log((h_i + h_j) / d)
         + 1j * omega * mu0 / mpmath.pi * integral) * 1000
    )
    value = types_ns["carson_earth_impedance"](h_i, h_j, x, d, rho, f)
    assert abs(value - oracle) <= 1e-9 * abs(oracle)


def test_electrode_formulas_two_ways(types_ns):
    """BusType formulas: package evaluation = plain math, and two published
    approximations per electrode agree within the spread the page states."""
    rho = types_ns["RHO"]
    rows = {row["name"]: row for row in types_ns["ELECTRODE_ROWS"]}
    rod = rho / (2 * math.pi * 3.0) * (math.log(4 * 3.0 / 0.01) - 1)
    ring = 2 * rho / (3 * 1.13 * math.sqrt(100.0))
    area, length, depth = 1600.0, 400.0, 0.8
    sverak = rho * (1 / length + 1 / math.sqrt(20 * area) * (1 + 1 / (1 + depth * math.sqrt(20 / area))))
    laurent = rho / 4 * math.sqrt(math.pi / area) + rho / length
    dwight_ring = rho / (2 * math.pi**2 * 11.3) * (math.log(8 * 11.3 / 0.01) + math.log(4 * 11.3 / 1.0))
    assert rows["rod 3 m"]["z_model"] == pytest.approx(rod, rel=1e-12)
    assert rows["ring 10 m x 10 m"]["z_model"] == pytest.approx(ring, rel=1e-12)
    assert rows["mesh 40 m x 40 m"]["z_model"] == pytest.approx(sverak, rel=1e-12)
    assert abs(rod / (rho / 3.0) - 1) < 0.04
    assert abs(dwight_ring / ring - 1) < 0.03
    assert abs(laurent / sverak - 1) < 0.04
    assert rod == pytest.approx(32.31, abs=0.005)
    assert ring == pytest.approx(5.90, abs=0.005)
    assert sverak == pytest.approx(1.32, abs=0.005)


# --- f = 0: what the evaluator does with a Carson term ------------------------


def test_carson_formulas_at_dc(types_ns):
    """At 0 Hz: self -> R_s'*l, phase -> R_c'*l and mutual -> ~0, all three
    without a message, as the page documents.

    Second way: the analytic limit of each formula at f -> 0.
    """
    cable = types_ns["cable"]
    params = {"rho": 100.0, "l": 2.0}
    expected = {
        "self_impedance_formula": types_ns["R_SCREEN"] * 2.0,
        "phase_impedance_formula": types_ns["R_CORE"] * 2.0,
    }
    for field, limit in expected.items():
        with warnings.catch_warnings():
            warnings.simplefilter("error", DCLimitWarning)
            value = compute_impedance(getattr(cable, field), [0.0], params)[0.0]
        assert value.imag == 0.0
        assert value.real == pytest.approx(limit, rel=1e-9)
    with warnings.catch_warnings():
        warnings.simplefilter("error", DCLimitWarning)
        mutual = compute_impedance(cable.mutual_impedance_formula, [0.0], params)[0.0]
    assert 0.0 <= mutual.real <= 1e-10 * 2.0
    assert mutual.imag == 0.0


def test_carson_mutual_at_dc_is_quiet_at_every_length(types_ns):
    """The DC note on the page: no length makes the mutual impedance warn.

    Up to this pass it warned from about 11 m on: its real part
    pi^2*1e-4*f*l moves by 8.9e-11*l over the last decade of the approach,
    which an absolute floor of 1e-12 Ohm reported. The step shrinks ten-fold
    per decade, the decrease the evaluator now accepts; second way: that
    ratio, from the real part written out here, and the value it ends on.
    """
    formula = types_ns["cable"].mutual_impedance_formula
    for length in (0.01, 0.012, 1.0, 100.0):
        with warnings.catch_warnings():
            warnings.simplefilter("error", DCLimitWarning)
            value = compute_impedance(formula, [0.0], {"rho": 100.0, "l": length})[0.0]
        assert value.real == pytest.approx(math.pi**2 * 1e-12 * length, rel=1e-9)
    real = [math.pi**2 * 1e-4 * f for f in (1e-6, 1e-7, 1e-8)]
    assert (real[1] - real[2]) / (real[0] - real[1]) == pytest.approx(0.1, rel=1e-9)


# --- the EN 50522 how-to ------------------------------------------------------


def test_en50522_snippet_quantities(en50522_ns):
    """U_E is the fault-bus EPR, Z_E the driving point at the fault bus.

    Second ways: ``res_buses`` for the EPR, a dense nodal solve assembled here
    for the driving point, the source values for ``i_fault``.
    """
    net, rows = en50522_ns["net"], en50522_ns["ROWS"]
    fault_bus = en50522_ns["FAULT_BUS"]
    epr = {
        row["frequency_Hz"]: row["EPR_V"]
        for row in net.res_buses().iter_rows(named=True)
        if row["bus_name"] == fault_bus and not row["is_rms"]
    }
    for row in rows:
        f = row["f"]
        assert row["U_E"] == pytest.approx(epr[f], rel=1e-12)
        assert row["Z_E"] == pytest.approx(abs(_driving_point(net, fault_bus, f)), rel=1e-12)
        assert row["I_F"] == pytest.approx(abs(en50522_ns["SOURCE_VALUES"][f]), rel=1e-12)
        assert row["I_E"] == pytest.approx(row["r"] * row["I_F"], rel=1e-12)
    rms = [
        row["EPR_V"] for row in net.res_buses().iter_rows(named=True)
        if row["bus_name"] == fault_bus and row["is_rms"]
    ]
    assert en50522_ns["U_E_RMS"] == pytest.approx(rms[0], rel=1e-12)


def test_en50522_snippet_numbers_on_the_page(en50522_ns):
    """The table on the how-to page is the snippet's output, rounded."""
    by_f = {row["f"]: row for row in en50522_ns["ROWS"]}
    assert by_f[50.0]["U_E"] == pytest.approx(384.4, abs=0.05)
    assert by_f[50.0]["Z_E"] == pytest.approx(1.189, abs=5e-4)
    assert by_f[50.0]["r"] == pytest.approx(0.2165, abs=5e-5)
    assert by_f[50.0]["chain"] == pytest.approx(257.3, abs=0.05)
    assert by_f[250.0]["U_E"] == pytest.approx(9.10, abs=0.005)
    assert by_f[250.0]["chain"] == pytest.approx(8.56, abs=0.005)
    assert en50522_ns["U_E_RMS"] == pytest.approx(384.6, abs=0.05)

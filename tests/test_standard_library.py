# tests/test_standard_library.py
"""
The standard library of types (``groundinsight.library``), each number a second way.

* MV cable systems: the type of NA2XS(F)2Y 1x300RM/25 is the type the
  documentation page derives (``docs/snippets/types_from_datasheets.py``);
  for every row the bundle formula equals the matrix reduction of the three
  screens (``bundle_equivalent`` of the page), and a one-branch network
  solved with ``run_fault`` gives ``r = |1 - Z_m/Z_s|``.
* LV cables: the coupling over the geometric mean distance is the mean of
  the three phase-PEN couplings; the phase formula is ``Z_s - Z_m²/Z_s``.
* Electrode presets: the rods against Dwight's closed form, every preset
  proportional to rho in homogeneous soil, the stored formula against its
  table.
"""

import cmath
import math
import pathlib
import sys
import warnings

import pytest

import groundinsight as gi
from groundinsight.library import LV_CABLES, MV_CABLES, cable_system_type, lv_cable_type
from groundinsight.library.cables import BEDDING_MM, SCREEN_WIRE_MM, SEMICON_MM

sys.path.insert(0, str(gi.__file__).rsplit("/src/", 1)[0] + "/docs/snippets")
import types_from_datasheets as page  # noqa: E402 -- the documentation page's own derivation


_FIELD = {"self_impedance_formula": "self_impedance", "mutual_impedance_formula": "mutual_impedance",
          "phase_impedance_formula": "phase_impedance"}


def _per_km(cable, formula, rho, f):
    """A formula of ``cable`` as the library evaluates it: a 1 km branch in a network."""
    station = gi.BusType(name="station", system_type="Substation", voltage_level=20.0, impedance_formula="1.0")
    net = gi.create_network(name="km", frequencies=[f])
    for bus in ("A", "B"):
        gi.create_bus(name=bus, type=station, network=net)
    branch = gi.create_branch(name="K", type=cable, from_bus="A", to_bus="B", length=1.0,
                              specific_earth_resistance=rho, network=net)
    return complex(getattr(branch, _FIELD[formula])[f])


def test_the_300_system_is_the_type_of_the_documentation_page():
    cable = gi.standard_branch_types()["NA2XS(F)2Y 1x300RM/25 12/20 kV"]
    reference = page.cable_system
    assert (cable.self_impedance_formula, cable.mutual_impedance_formula, cable.phase_impedance_formula,
            cable.cross_section_mm2) == (reference.self_impedance_formula, reference.mutual_impedance_formula,
                                         reference.phase_impedance_formula, reference.cross_section_mm2)


@pytest.mark.parametrize("row", MV_CABLES, ids=lambda r: r.designation)
@pytest.mark.parametrize("rho, f", [(100.0, 50.0), (100.0, 250.0), (1000.0, 50.0)])
def test_every_mv_system_is_the_matrix_reduction_of_its_three_screens(row, rho, f):
    cable = cable_system_type(row)
    r_screen = round((row.diameter_over_insulation_mm / 2 + SEMICON_MM + BEDDING_MM + SCREEN_WIRE_MM / 2) * 1e-3, 8)
    d = row.outer_diameter_mm * 1e-3
    trefoil = [(0.0, 0.0), (d, 0.0), (d / 2, d * math.sqrt(3) / 2)]
    z_s, z_m = page.bundle_equivalent(trefoil, 0, r_screen, row.screen_resistance, rho, f)
    assert _per_km(cable, "self_impedance_formula", rho, f) == pytest.approx(z_s, rel=1e-6)
    assert _per_km(cable, "mutual_impedance_formula", rho, f) == pytest.approx(z_m, rel=1e-6)
    # Thermal cross-section: the screen over the faulted core's share (page method, 100 Ohm*m, 50 Hz).
    share = page.screen_current_shares(trefoil, 0, r_screen, row.screen_resistance, 100.0, 50.0)[0]
    assert cable.cross_section_mm2 == round(row.screen_cross_section_mm2 / share, 1)


@pytest.mark.parametrize("name", list(gi.standard_branch_types()))
def test_a_network_reproduces_the_reduction_factor_of_every_cable(name):
    cable = gi.standard_branch_types()[name]
    station = gi.BusType(name="station", system_type="Substation", voltage_level=20.0, impedance_formula="2.0")
    net = gi.create_network(name="one", frequencies=[50.0, 250.0])
    for bus in ("A", "B"):
        gi.create_bus(name=bus, type=station, specific_earth_resistance=100.0, network=net)
    gi.create_branch(name="K", type=cable, from_bus="A", to_bus="B", length=1.0, specific_earth_resistance=100.0,
                     network=net)
    gi.create_source(name="I", bus="A", values={50.0: 1000.0, 250.0: 100.0}, network=net)
    gi.create_fault(name="F", bus="B", scalings={50.0: 1.0, 250.0: 1.0}, network=net)
    with warnings.catch_warnings():
        warnings.simplefilter("error")  # a standard type raises no warning in a plain network
        result = gi.run_fault(net, "F")
    for f in (50.0, 250.0):
        z_s, z_m = (_per_km(cable, "self_impedance_formula", 100.0, f), _per_km(cable, "mutual_impedance_formula", 100.0, f))
        # One cable from a source earthed with Z_A = 2 Ohm: r = (Z_s - Z_m) / (Z_s + Z_A).
        assert result.reduction_factor.value[f] == pytest.approx(abs((z_s - z_m) / (z_s + 2.0)), rel=1e-9)


@pytest.mark.parametrize("row", LV_CABLES, ids=lambda r: r.designation)
def test_an_lv_cable_couples_over_the_mean_of_its_three_phase_pen_distances(row):
    """Second way: the mean of the three loop impedances phase-earthing conductor, distance by distance.

    Four cores sit on a square (two neighbours at d, one across at sqrt(2) d);
    five on a regular pentagon in the order PE, N, L1, L2, L3 (one phase next
    to the protective conductor, two at the diagonal, the golden ratio times
    d). A reduced PEN has its own radius, resistance and insulation.
    """
    cable = lv_cable_type(row)
    r = math.sqrt(row.cross_section_mm2 / math.pi) * 1e-3
    pen_area = row.pen_cross_section_mm2 or row.cross_section_mm2
    pen_resistance = row.pen_resistance or row.conductor_resistance
    r_pen = math.sqrt(pen_area / math.pi) * 1e-3
    d = r + r_pen + (row.insulation_thickness_mm + (row.pen_insulation_thickness_mm or row.insulation_thickness_mm)) * 1e-3
    diagonal = 2 * math.cos(math.pi / 5)       # of a regular pentagon with side 1: the golden ratio
    distances = (d, d, math.sqrt(2) * d) if row.cores == 4 else (d, diagonal * d, diagonal * d)
    for f in (50.0, 250.0):
        mean = sum(page.loop_impedance(0.0, x, 100.0, f) for x in distances) / 3
        assert _per_km(cable, "mutual_impedance_formula", 100.0, f) == pytest.approx(mean, rel=1e-6)
        z_s = _per_km(cable, "self_impedance_formula", 100.0, f)
        assert z_s == pytest.approx(page.loop_impedance(pen_resistance, math.exp(-0.25) * r_pen, 100.0, f), rel=1e-6)
        z_pp = page.loop_impedance(row.conductor_resistance, math.exp(-0.25) * r, 100.0, f)
        z_m = _per_km(cable, "mutual_impedance_formula", 100.0, f)
        assert _per_km(cable, "phase_impedance_formula", 100.0, f) == pytest.approx(z_pp - z_m**2 / z_s, rel=1e-6)
    assert (cable.conductor_material, cable.cross_section_mm2) == ("Al", pen_area)
    assert (cable.voltage_level, cable.c_shield_nf_per_km) == (0.4, None)


def test_the_capacitance_of_an_mv_cable_agrees_with_its_geometry():
    """Second way: the coaxial capacitor between conductor screen and insulation screen, XLPE."""
    for row in MV_CABLES:
        cable = cable_system_type(row)
        assert (cable.voltage_level, cable.c_shield_nf_per_km) == (20.0, row.capacitance_nf_per_km)
        outer = row.diameter_over_insulation_mm
        inner = outer - 2 * row.insulation_thickness_mm
        geometry = 2 * math.pi * 8.854e-12 * 2.4 / math.log(outer / inner) * 1e12   # nF/km
        assert row.capacitance_nf_per_km == pytest.approx(geometry, rel=0.06), row.designation


def test_the_existing_types_keep_their_formulas():
    """The sixteen types of the first library are the rows they were: results with them do not move."""
    import hashlib
    import json

    types = gi.standard_branch_types()
    first = [row.designation for row in MV_CABLES[:11]] + [row.designation for row in LV_CABLES[:5]]
    record = {name: [types[name].self_impedance_formula, types[name].mutual_impedance_formula,
                     types[name].phase_impedance_formula, types[name].cross_section_mm2] for name in first}
    assert hashlib.md5(json.dumps(record, sort_keys=True).encode()).hexdigest() == "97b2fba581b41b20ee046f270ad6d3de"


def test_the_library_names_are_unique_readable_and_cite_their_source():
    branch, bus = gi.standard_branch_types(), gi.standard_bus_types()
    assert len(branch) == len(MV_CABLES) + len(LV_CABLES) == 66 and len(MV_CABLES) == 26
    for t in [*branch.values(), *bus.values()]:
        assert "_" not in t.name
    for t in branch.values():
        assert "Source:" in t.description
    assert gi.standard_branch_types() is not gi.standard_branch_types()  # fresh, the caller may change it


def test_the_library_has_ten_electrode_presets():
    assert list(gi.standard_bus_types()) == [
        "Substation ring 2.5 m, 4 rods 2.5 m", "House foundation 10 x 10 m", "Rod 2.5 m", "Rod 5 m",
        "House ring electrode 15 x 5 m", "Cable distribution cabinet, rod 3 m",
        "Transformer station ring 5 x 5 m, rod 6 m", "Transformer station ring 5 x 5 m, 2 rods 12 m at 5 m",
        "Substation mesh 50 x 100 m, meshes 10 x 10 m", "Substation mesh 200 x 200 m, meshes 10 x 10 m"]


@pytest.mark.parametrize("name, length, width, conductor", [
    ("Substation mesh 50 x 100 m, meshes 10 x 10 m", 50.0, 100.0, 6 * 100.0 + 11 * 50.0),
    ("Substation mesh 200 x 200 m, meshes 10 x 10 m", 200.0, 200.0, 2 * 21 * 200.0),
])
def test_the_meshes_agree_with_the_formula_of_laurent_and_niemann(name, length, width, conductor):
    """Second way: R = rho / (2 D) + rho / L, D the diameter of the circle of the same area, L the buried length."""
    preset = gi.standard_bus_types()[name]
    diameter = math.sqrt(4 * length * width / math.pi)
    estimate = 100.0 / (2 * diameter) + 100.0 / conductor
    assert abs(preset.characteristic.evaluate(100.0, [50.0]).impedance[0]) == pytest.approx(estimate, rel=0.12)
    assert (preset.system_type, preset.voltage_level) == ("HV/MV", 110.0)


def test_the_house_ring_lies_between_the_plate_and_the_thin_ring():
    """Second way: bounds. A plate of the same area is the lower one, a ring of the same perimeter in
    full space, rho / (pi^2 D) ln(8 D / d), the upper one (the surface roughly halves it)."""
    preset = gi.standard_bus_types()["House ring electrode 15 x 5 m"]
    z = abs(preset.characteristic.evaluate(100.0, [50.0]).impedance[0])
    plate = 100.0 / (2 * math.sqrt(4 * 75.0 / math.pi))
    ring_diameter = 40.0 / math.pi
    ring = 100.0 / (math.pi**2 * ring_diameter) * math.log(8 * ring_diameter / 0.01)
    assert plate < z < ring


def test_more_electrode_means_less_impedance():
    presets = gi.standard_bus_types()
    z = {name: abs(preset.characteristic.evaluate(100.0, [50.0]).impedance[0]) for name, preset in presets.items()}
    assert (z["Transformer station ring 5 x 5 m, 2 rods 12 m at 5 m"] < z["Transformer station ring 5 x 5 m, rod 6 m"]
            < z["Cable distribution cabinet, rod 3 m"])
    assert z["Substation mesh 200 x 200 m, meshes 10 x 10 m"] < z["Substation mesh 50 x 100 m, meshes 10 x 10 m"]
    assert z["House ring electrode 15 x 5 m"] < z["Rod 5 m"]


@pytest.mark.parametrize("name, length", [("Rod 2.5 m", 2.5), ("Rod 5 m", 5.0),
                                          ("Cable distribution cabinet, rod 3 m", 3.0)])
def test_the_rods_agree_with_dwights_closed_form(name, length):
    rod = gi.standard_bus_types()[name]
    values = rod.characteristic.evaluate(100.0, [50.0])
    dwight = 100.0 / (2 * math.pi * length) * (math.log(4 * length / 0.005) - 1)  # wire radius 5 mm
    assert abs(values.impedance[0]) == pytest.approx(dwight, rel=0.03)


@pytest.mark.parametrize("name", list(gi.standard_bus_types()))
def test_every_preset_scales_with_rho_in_homogeneous_soil(name):
    preset = gi.standard_bus_types()[name].characteristic
    z = [abs(cmath.rect(1, 0) * preset.evaluate(rho, [50.0]).impedance[0]) for rho in preset.rho_Ohm_m]
    for rho, value in zip(preset.rho_Ohm_m, z, strict=True):
        assert value / rho == pytest.approx(z[0] / preset.rho_Ohm_m[0], rel=0.01)
    assert preset.k_touch is not None and preset.impedance_source == "table"
    assert preset.provenance is not None and preset.provenance.solver == "mom"


@pytest.mark.parametrize("name", list(gi.standard_bus_types()))
def test_a_preset_works_as_the_type_of_a_bus(name):
    preset = gi.standard_bus_types()[name]
    net = gi.create_network(name="one", frequencies=[50.0, 250.0])
    bus = gi.create_bus(name="S", type=preset, specific_earth_resistance=100.0, network=net)
    expected = preset.characteristic.evaluate(100.0, [50.0, 250.0]).impedance
    assert [complex(bus.impedance[f]) for f in (50.0, 250.0)] == pytest.approx(list(expected), rel=1e-12)
    # The fitted formula of the type stays close to the table at the grid points.
    formula = complex(gi.create_bus(name="T", type=preset.model_copy(update={"characteristic": None}),
                                    specific_earth_resistance=100.0, network=net).impedance[50.0])
    assert abs(formula) == pytest.approx(abs(expected[0]), rel=0.05)


def _page_rows(title):
    """The table rows under ``## title`` of the reference page, as lists of cells."""
    text = (pathlib.Path(gi.__file__).parents[2] / "docs" / "reference" / "standard-types.md").read_text(encoding="utf-8")
    section = text.split(f"## {title}\n", 1)[1].split("\n## ", 1)[0]
    rows = [line for line in section.splitlines() if line.startswith("| ") and not line.startswith("| Type")]
    return {cells[0]: cells[1:] for cells in ([c.strip() for c in row.strip("|").split("|")] for row in rows)}


def _r(cable, f):
    z_s, z_m = (_per_km(cable, "self_impedance_formula", 100.0, f), _per_km(cable, "mutual_impedance_formula", 100.0, f))
    return f"{abs(1 - z_m / z_s):.4f}"


def test_the_reference_page_shows_the_numbers_of_the_library():
    types = gi.standard_branch_types()
    mv = _page_rows("20 kV cable systems")
    assert list(mv) == [row.designation for row in MV_CABLES]
    for row in MV_CABLES:
        cable = types[row.designation]
        assert mv[row.designation] == [
            f"{row.conductor_resistance:g}", f"{row.screen_cross_section_mm2:g}", f"{row.screen_resistance:g}",
            f"{row.diameter_over_insulation_mm:g}", f"{row.outer_diameter_mm:g}", _r(cable, 50.0), _r(cable, 250.0),
            f"{cable.cross_section_mm2:g}", f"{row.capacitance_nf_per_km:g}"]
    lv = _page_rows("LV cables")
    assert list(lv) == [row.designation for row in LV_CABLES]
    for row in LV_CABLES:
        cable = types[row.designation]
        assert lv[row.designation] == [
            f"{row.cross_section_mm2:g}", f"{cable.cross_section_mm2:g}", f"{row.conductor_resistance:g}",
            f"{row.pen_resistance or row.conductor_resistance:g}", f"{row.insulation_thickness_mm:g}",
            f"{row.outer_diameter_mm:g}", _r(cable, 50.0), _r(cable, 250.0)]
    electrodes = _page_rows("Earth electrodes")
    presets = gi.standard_bus_types()
    assert list(electrodes) == list(presets)
    for name, preset in presets.items():
        values = preset.characteristic.evaluate(100.0, [50.0])
        assert electrodes[name] == [preset.system_type, f"{abs(values.impedance[0]):.4g}", f"{values.k_touch[0]:.3f}"]

# tests/test_groundfield_characterize.py
"""
Earthing characteristics computed with groundfield.

These tests need groundfield, which is not on PyPI before its release
2026.11.0 and is therefore no dependency of groundinsight: they carry the
marker ``groundfield`` and are skipped without it -- also in CI strict mode
(see ``tests/conftest.py``).

Every quantity is checked a second way:

* a rod in homogeneous soil against Dwight's closed form for its resistance,
  ``R = rho / (2 pi L) (ln(4 L / a) - 1)``, and against the surface potential
  of a uniformly loaded line from the surface to depth ``L``,
  ``phi(x) = rho I / (2 pi L) asinh(L / x)``, for ``k_touch`` (feet 1 m from
  the rod) and ``k_transfer`` (target points on the surface);
* ``k_touch`` against groundfield's single-point ``touch_voltage`` in every
  direction of the envelope, ``k_transfer`` against ``FieldResult.potential``;
* the scaling of both soil layers by ``c`` against the linearity of the
  quasi-static field (``Z`` and ``rho_eff`` scale by ``c``, the factors do not
  change);
* the fitted formula against groundfield's own fit and evaluation;
* the touch voltage of a groundinsight solve against groundfield's touch
  voltage for the same current.
"""

import json
import math

import numpy as np
import polars as pl
import pytest

import groundinsight as gi
from groundinsight.integrations import groundfield as gfi

gf = pytest.importorskip("groundfield")

pytestmark = pytest.mark.groundfield

L_ROD, A_ROD = 3.0, 0.01
RHOS = (50.0, 100.0, 200.0, 400.0)
FREQS = (50.0, 250.0, 1000.0)
TARGETS = [(5.0, 0.0, 0.0), (20.0, 0.0, 0.0)]


def _rod_world(soil):
    world = gf.create_world(soil=soil)
    gf.create_electrode(world, "rod", name="rod", position=(0.0, 0.0, 0.0),
                        length=L_ROD, wire_radius=A_ROD)
    return world


def _engine():
    return gf.create_engine(frequencies=list(FREQS), segment_length=0.25)


@pytest.fixture(scope="module")
def rod():
    return gfi.characterize(_rod_world, RHOS, electrode="rod", engine=_engine(),
                            transfer_targets=TARGETS, name="rod 3 m", geometry_id="rod-3m")


def _dwight(rho):
    return rho / (2 * math.pi * L_ROD) * (math.log(4 * L_ROD / A_ROD) - 1)


def _line_share(x):
    """phi(x) / U_E of a uniformly loaded rod, at surface distance x."""
    return math.asinh(L_ROD / x) / (math.log(4 * L_ROD / A_ROD) - 1)


def test_the_rod_table_matches_dwight_and_the_line_source(rod):
    assert rod.rho_Ohm_m == RHOS and rod.frequency_Hz == FREQS
    assert rod.impedance_source == "table" and rod.name == "rod 3 m"
    for i, rho in enumerate(RHOS):
        for j in range(len(FREQS)):
            assert abs(rod.impedance_Ohm[i][j]) == pytest.approx(_dwight(rho), rel=5e-3)
            assert rod.k_touch[i][j] == pytest.approx(1 - _line_share(1.0), rel=1e-3)
            assert rod.k_transfer[i][j] == pytest.approx(1 - _line_share(20.0), rel=5e-3)


def test_homogeneous_soil_scales_the_impedance_and_keeps_the_factors(rod):
    """Second way: the quasi-static field is linear in rho."""
    z_per_rho = [rod.impedance_Ohm[i][0] / rho for i, rho in enumerate(RHOS)]
    assert all(z == pytest.approx(z_per_rho[0], rel=1e-9) for z in z_per_rho)
    for table in (rod.k_touch, rod.k_transfer):
        assert all(k == pytest.approx(table[0][0], rel=1e-9) for row in table for k in row)


def test_k_touch_is_the_largest_single_point_touch_voltage(rod):
    world = _rod_world(gf.HomogeneousSoil(rho=200.0))
    gf.create_source(world, attached_to="rod", magnitude=1.0)
    result = _engine().solve(world)
    i = RHOS.index(200.0)
    for j in range(len(FREQS)):
        u_e = abs(result.electrode_potentials["rod"][j])
        single = max(
            abs(gf.touch_voltage(result, world, electrode="rod", frequency_index=j,
                                 direction=(math.cos(t), math.sin(t), 0.0)))
            for t in np.linspace(0.0, 2 * math.pi, 24, endpoint=False)
        )
        assert rod.k_touch[i][j] == pytest.approx(single / u_e, rel=1e-12)
        phi = result.potential(np.array(TARGETS), frequency_index=j)
        transfer = max(abs(result.electrode_potentials["rod"][j] - p) for p in phi) / u_e
        assert rod.k_transfer[i][j] == pytest.approx(transfer, rel=1e-12)


def test_the_formula_is_groundfields_standard_fit(rod):
    results, rhos = [], []
    for rho in RHOS:
        world = _rod_world(gf.HomogeneousSoil(rho=rho))
        gf.create_source(world, attached_to="rod", magnitude=1.0)
        results.append(_engine().solve(world))
        rhos.append(rho)
    fit = gf.rho_f_standard_from_results(results, rhos, "rod", quantity="cluster")
    document = gf.to_bustype_dict(fit, name="x", system_type="x", voltage_level=0.0)
    assert rod.impedance_formula == document["impedance_formula"]
    # groundinsight evaluates the formula as groundfield does.
    net = gi.create_network(name="n", frequencies=list(FREQS))
    bus_type = gi.BusType(name="rod", system_type="x", voltage_level=0.4,
                          impedance_formula=rod.impedance_formula)
    for rho in (75.0, 300.0):
        bus = gi.create_bus(name=f"b{rho:g}", type=bus_type, specific_earth_resistance=rho,
                            network=net)
        expected = fit.evaluate(np.full(len(FREQS), rho), np.array(FREQS))
        for f, z in zip(FREQS, expected, strict=True):
            assert complex(bus.impedance[f]) == pytest.approx(complex(z), rel=1e-10)


def test_the_touch_voltage_of_a_solve_is_groundfields(rod):
    """End to end: 100 A through the rod into earth, against the field solution.

    The source sits at a remote bus, joined to the rod by a line without an
    earth wire and without coupling, so the whole fault current enters the
    earth through the rod.
    """
    net = gi.create_network(name="rod", frequencies=list(FREQS))
    bus_type = gi.BusType(name="rod", system_type="x", voltage_level=0.4,
                          impedance_formula="rho / 10", characteristic=rod)
    remote = gi.BusType(name="remote", system_type="x", voltage_level=20.0,
                        impedance_formula="0.1 + rho * 0")
    line = gi.BranchType(name="line", grounding_conductor=False,
                         self_impedance_formula="(0.2 + j * 0.4) * l",
                         mutual_impedance_formula="0 * l",
                         phase_impedance_formula="(0.2 + j * 0.4) * l")
    gi.create_bus(name="Q", type=remote, network=net)
    gi.create_bus(name="R", type=bus_type, specific_earth_resistance=200.0, network=net)
    gi.create_branch(name="L", type=line, from_bus="Q", to_bus="R", length=1.0, network=net)
    gi.create_source(name="I", bus="Q", values={f: 100.0 for f in FREQS}, network=net)
    gi.create_fault(name="F", bus="R", scalings={f: 1.0 for f in FREQS}, network=net)
    gi.run_fault(net, "F")
    row = net.res_buses("F").filter((pl.col("frequency_Hz") == 50.0)
                                    & (pl.col("bus_name") == "R")).row(0, named=True)
    world = _rod_world(gf.HomogeneousSoil(rho=200.0))
    gf.create_source(world, attached_to="rod", magnitude=100.0)
    result = _engine().solve(world)
    _, touch = gf.touch_voltage_envelope(result, world, electrode="rod")
    assert row["EPR_V"] == pytest.approx(abs(result.electrode_potentials["rod"][0]), rel=1e-9)
    assert row["U_vT_V"] == pytest.approx(float(np.max(np.abs(touch))), rel=1e-9)


def test_scaling_both_layers_scales_rho_eff_and_the_impedance():
    base = gf.TwoLayerSoil(rho_1=100.0, rho_2=500.0, h_1=2.0)
    factors = (0.5, 1.0, 2.0, 4.0)
    soils = gfi.scaled_soils(base, factors)
    assert [(s.rho_1, s.rho_2, s.h_1) for s in soils] == [(100.0 * c, 500.0 * c, 2.0) for c in factors]
    characteristic = gfi.characterize(_rod_world, soils, electrode="rod", engine=_engine(),
                                      rho_preset="station", fit=False)
    rho_base = gf.effective_resistivity(base, preset="station")
    assert characteristic.rho_Ohm_m == pytest.approx([c * rho_base for c in factors], rel=1e-12)
    z = [row[0] / rho for row, rho in zip(characteristic.impedance_Ohm, characteristic.rho_Ohm_m,
                                         strict=True)]
    assert all(v == pytest.approx(z[0], rel=1e-9) for v in z)
    assert characteristic.impedance_formula is None and characteristic.k_transfer is None
    assert characteristic.provenance.rho_preset == "station"
    assert characteristic.provenance.soil["soils"][0]["kind"] == "two_layer"


def test_provenance_names_the_computation(rod):
    provenance = rod.provenance
    assert provenance.source == "groundfield"
    assert provenance.groundfield_version == gf.__version__
    assert provenance.solver == "image" and provenance.geometry_id == "rod-3m"
    assert provenance.input_hash.startswith("sha256:")
    again = gfi.characterize(_rod_world, RHOS, electrode="rod", engine=_engine(),
                             transfer_targets=TARGETS, name="rod 3 m", geometry_id="rod-3m")
    assert again.provenance.input_hash == provenance.input_hash  # same input, same hash
    other = gfi.characterize(_rod_world, (50.0, 100.0), electrode="rod", engine=_engine(),
                             fit=False)
    assert other.provenance.input_hash != provenance.input_hash


def test_the_document_is_one_groundfield_reads(rod, tmp_path):
    path = tmp_path / "rod.json"
    gfi.write_bustype_json(path, rod, system_type="MV/LV", voltage_level=20.0)
    spec = gf.load_bustype_json(path)  # groundfield's own reader and formula guard
    assert spec.name == "rod 3 m" and spec.impedance_formula == rod.impedance_formula
    z_gf = gf.evaluate_spec(spec, list(FREQS), 150.0)
    net = gi.create_network(name="n", frequencies=list(FREQS))
    bus = gi.create_bus(name="b", type=gi.BusType(name="t", system_type="x", voltage_level=1.0,
                                                  impedance_formula=spec.impedance_formula),
                        specific_earth_resistance=150.0, network=net)
    for f, z in zip(FREQS, z_gf, strict=True):
        assert complex(bus.impedance[f]) == pytest.approx(complex(z), rel=1e-10)
    assert gfi.read_bustype_json(path) == rod  # lossless, factors and provenance included
    assert json.loads(path.read_text(encoding="utf-8"))["schema"] == "groundfield.bustype"


@pytest.mark.parametrize(
    ("kwargs", "error", "message"),
    [
        ({"soils": (100.0, 100.0)}, ValueError, "twice"),
        ({"soils": ()}, ValueError, "at least one"),
        ({"engine": None, "frequencies": (0.0, 50.0)}, ValueError, "positive"),
        ({"electrode": "ghost"}, ValueError, "ghost"),
        ({"touch_distance": -1.0}, ValueError, "touch_distance"),
        ({"engine": None, "frequencies": None}, ValueError, "frequencies"),
    ],
)
def test_invalid_input_is_refused(kwargs, error, message):
    arguments = {"soils": (100.0,), "electrode": "rod", "engine": _engine(), "fit": False}
    arguments.update(kwargs)
    soils = arguments.pop("soils")
    with pytest.raises(error, match=message):
        gfi.characterize(_rod_world, soils, **arguments)


def test_the_characteristic_job_is_characterize_of_the_template():
    import groundinsight.api as api

    session = api.ProjectSession(_station_session_network())
    request = api.CharacterizeRequestDTO(electrode="foundation", dimensions={"size_m": 8.0},
                                         soil="two_layer", rho_1=100.0, rho_2=500.0, h_1=2.0,
                                         factors=[0.5, 1.0, 2.0], frequencies=list(FREQS),
                                         rho_preset="lv_network", transfer_distances=[20.0],
                                         target="bus", owner="R", name="house")
    dto = session.submit(request).result(timeout=300)
    direct = gfi.characterize(
        gfi.electrode_world("foundation", size_m=8.0),
        gfi.scaled_soils(gf.TwoLayerSoil(rho_1=100.0, rho_2=500.0, h_1=2.0), [0.5, 1.0, 2.0]),
        electrode="E", engine=gf.create_engine(frequencies=list(FREQS), segment_length=0.5),
        rho_preset="lv_network", transfer_targets=[(20.0, 0.0, 0.0)], name="house")
    job = gi.EarthingCharacteristic.model_validate(dto.characteristic)
    assert job.model_dump(exclude={"provenance"}) == direct.model_dump(exclude={"provenance"})
    assert job.provenance.input_hash == direct.provenance.input_hash
    assert (dto.target, dto.owner) == ("bus", "R")
    session.apply(api.SetCharacteristic(target="bus", name="R", characteristic=dto.characteristic))
    assert session.network.buses["R"].characteristic == job
    session.close()


def _station_session_network():
    net = gi.create_network(name="r", frequencies=list(FREQS))
    gi.create_bus(name="R", type=gi.BusType(name="t", system_type="x", voltage_level=0.4,
                                            impedance_formula="rho / 10"), network=net)
    return net


def test_the_templates_build_the_same_geometry_alone_and_in_a_larger_world():
    world = gf.create_world(soil=gf.HomogeneousSoil(rho=100.0))
    gfi.add_electrode(world, "ring", name="ONS")
    gfi.add_electrode(world, "foundation", name="H1", position=(25.0, 15.0))
    names = [e.name for e in world.electrodes]
    assert names == ["ONS", "ONS_rod0", "ONS_rod1", "ONS_rod2", "ONS_rod3", "H1"]
    alone = gfi.electrode_world("ring", name="ONS")(gf.HomogeneousSoil(rho=100.0))
    assert [e.model_dump() for e in alone.electrodes] == [e.model_dump() for e in world.electrodes[:5]]
    with pytest.raises(ValueError, match="no dimension"):
        gfi.add_electrode(world, "ring", name="X", size_m=3.0)

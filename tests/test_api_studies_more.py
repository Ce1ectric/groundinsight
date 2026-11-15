# tests/test_api_studies_more.py
"""
The study jobs of the service layer return what the library computes.

Every job runs through ``ProjectSession.submit`` (the thread backend, a copy
of the network) and its tables are compared, row by row, with the frame of
the direct library call on an independently built twin -- the second way for
every number a front end shows of these studies.
"""

import math
import warnings

import pytest

import groundinsight as gi
import groundinsight.api as api
from tests._characteristic_cases import _network, _table
from tests._warning_policy import acknowledge

pytestmark = acknowledge("PHASE_PROXY", "SCALINGS_INCOMPLETE")


def _cell(value):
    """A frame value as a TableDTO cell carries it."""
    if isinstance(value, float) and not math.isfinite(value):
        return None
    if isinstance(value, (list, tuple)):
        return str(value)
    return value


def _rows(frame):
    return [{k: _cell(v) for k, v in row.items()} for row in frame.to_dicts()]


def _run(session, request):
    return session.submit(request).result(timeout=120)


def _solved(network, fault, mode="auto"):
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        gi.run_fault(network, fault, phase_current_mode=mode)
    return network


def test_cuts_are_the_library_analysis():
    session = api.ProjectSession.from_example("mv_ring")
    dto = _run(session, api.CutsRequestDTO(fault="F1", cuts=[
        api.CutDTO(name="left", branches=["L_09_10"]), api.CutDTO(name="right", branches=["L_10_11"])]))
    twin = _solved(api.EXAMPLES["mv_ring"](), "F1")
    frame = gi.analyze_cuts(twin, fault="F1", cuts=[gi.Cut(name="left", branches=["L_09_10"]),
                                                     gi.Cut(name="right", branches=["L_10_11"])]).to_polars()
    assert dto.study == "cuts" and dto.tables["cuts"].rows == _rows(frame)
    session.close()


def test_bus_response_extremes_and_electrode_sweep():
    session = api.ProjectSession.from_example("mv_ring")
    dto = _run(session, api.BusResponseRequestDTO(fault="F1", electrodes={"1 Ohm": 1.0, "5 Ohm": 5.0}))
    twin = _solved(api.EXAMPLES["mv_ring"](), "F1")
    response = gi.bus_response(twin, fault="F1")
    assert dto.tables["extremes"].rows == _rows(response.extremes())
    assert dto.tables["sweep"].rows == _rows(response.sweep([1.0, 5.0], labels=["1 Ohm", "5 Ohm"]))
    assert dto.values["bus"] == "b10"
    session.close()


def _thermal_network():
    net = gi.create_network(name="thermal", frequencies=[50.0])
    bus_type = gi.BusType(name="b", system_type="s", voltage_level=20.0, impedance_formula="rho*0 + 0.5")
    cable = gi.BranchType(name="c", grounding_conductor=True,
                          self_impedance_formula="(rho*0 + 0.25 + j*f*0.012)*l",
                          mutual_impedance_formula="(rho*0 + 0.05 + j*f*0.012)*l",
                          conductor_material="Cu", cross_section_mm2=50.0,
                          theta_initial_C=20.0, theta_final_C=300.0)
    for name in ("b1", "b2"):
        gi.create_bus(name=name, type=bus_type, network=net)
    gi.create_branch(name="c1", type=cable, from_bus="b1", to_bus="b2", length=1.0, network=net)
    gi.create_source(name="src", bus="b1", values={50.0: 5000.0}, network=net)
    gi.create_fault(name="F", bus="b2", scalings={50.0: 1.0}, network=net)
    return net


def test_thermal_check_is_the_library_check():
    session = api.ProjectSession(_thermal_network())
    dto = _run(session, api.ThermalRequestDTO(fault="F", t_k_s=1.0, r_to_x=0.1))
    twin = _solved(_thermal_network(), "F")
    assert dto.tables["limits"].rows == _rows(gi.check_conductor_limits(twin, "F", t_k_s=1.0, r_to_x=0.1))
    status = session.submit(api.ThermalRequestDTO(fault="F", t_k_s=1.0))  # neither kappa nor R/X
    with pytest.raises(api.JobFailed, match="r_to_x"):
        status.result(timeout=60)
    session.close()


def test_touch_check_is_the_library_check():
    session = api.ProjectSession(_network(station_characteristic=_table(), rho=200.0))
    curve = [(0.1, 600.0), (1.0, 100.0)]
    dto = _run(session, api.TouchCheckRequestDTO(fault="F", u_limit=curve, t_k_s=0.5))
    twin = _solved(_network(station_characteristic=_table(), rho=200.0), "F")
    frame = gi.check_touch_voltages(twin, "F", curve, 0.5)
    assert dto.tables["touch"].rows == _rows(frame)
    assert dto.values["assessed"] == 2  # S1 and S2 carry the table; Q does not
    session.close()


def _transient_network():
    net = gi.create_network(name="station", frequencies=[50.0])
    grid = gi.BusType(name="grid", system_type="Substation", voltage_level=20.0,
                      impedance_formula="rho * 0.01", R_formula="rho * 0.01")
    line = gi.BranchType(name="line", grounding_conductor=True, self_impedance_formula="0.25 * l",
                         mutual_impedance_formula="0 * l", R_self_formula="0.25 * l")
    for name in ("B1", "B2"):
        gi.create_bus(name=name, type=grid, network=net)
    gi.create_branch(name="L1", type=line, from_bus="B1", to_bus="B2", length=1.0, network=net)
    gi.create_source(name="S1", bus="B1", values={50.0: 100.0}, network=net)
    gi.create_fault(name="F1", bus="B2", scalings={50.0: 1.0}, network=net)
    return net


@pytest.mark.parametrize("waveform", ["step", "sine"])
def test_transient_is_the_library_study(waveform):
    session = api.ProjectSession(_transient_network())
    # A 20 ms pulse in a resistive network: decayed long before t_end, as the
    # FFT solver needs.
    request = api.TransientRequestDTO(fault="F1", t_end=0.04, dt=1e-3, waveform=waveform, t_off=0.02,
                                      amplitude=100.0, dc_decay_tau=0.01 if waveform == "sine" else None)
    dto = _run(session, request)
    from groundinsight.simulation import waveforms

    study = gi.TransientStudy(_transient_network(), "F1")
    shape = (waveforms.step(100.0, t_off=0.02) if waveform == "step" else
             waveforms.sinusoidal_with_dc_offset(100.0, 50.0, t_off=0.02, dc_amplitude=-100.0,
                                                 dc_decay_tau=0.01))
    study.set_source_waveform("S1", shape)
    study.set_observation(buses=["B1", "B2"])
    frame = study.solve(t_end=0.04, dt=1e-3).to_polars()
    assert dto.tables["series"].rows == _rows(frame)
    # Second way for the resistive network: the fault injects I at B2 and the
    # source takes it back at B1, an antisymmetric pair, so u_B2 = -u_B1 =
    # I / (G_bus + 2 G_line) = 100 / (1 + 2 / 0.25) V during the pulse.
    if waveform == "step":
        b2 = [r["value"] for r in dto.tables["series"].rows if r["name"] == "B2"]
        assert max(abs(v) for v in b2) == pytest.approx(100.0 / 9.0, rel=1e-9)
    session.close()


def test_reference_cases_and_sweep_statistics():
    session = api.ProjectSession.from_example("quickstart")
    dto = _run(session, api.ReferenceCasesRequestDTO())
    assert dto.tables["cases"].rows == _rows(gi.run_reference_cases())
    assert dto.values == {"cases": 6, "agree": 6}
    points = [api.SweepPointDTO(label=f"rho {r:g}", bus_rho={"bus_fault": r}) for r in (50.0, 100.0, 200.0)]
    sweep = _run(session, api.SweepRequestDTO(fault="fault_at_remote_bus", points=points))
    twin = api.EXAMPLES["quickstart"]()
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        study = gi.run_sweep(twin, fault="fault_at_remote_bus", points=[
            gi.SweepPoint(label=f"rho {r:g}", bus_rho={"bus_fault": r}) for r in (50.0, 100.0, 200.0)])
    assert sweep.tables["summary"].rows == _rows(gi.summarize(study.buses(), "EPR_V", by=["bus_name"]))
    session.close()


def test_the_new_requests_round_trip_through_json():
    import json

    for request in (api.CutsRequestDTO(fault="F", cuts=[api.CutDTO(name="a", branches=["K1"])]),
                    api.BusResponseRequestDTO(fault="F", electrodes={"x": {"real": 1.0, "imag": 0.5}}),
                    api.ThermalRequestDTO(fault="F", scope="nodes", kappa=1.8),
                    api.TouchCheckRequestDTO(fault="F", u_limit=80.0),
                    api.TransientRequestDTO(fault="F", t_end=0.1, dt=1e-4),
                    api.ReferenceCasesRequestDTO()):
        assert api.parse_job_request(json.loads(request.model_dump_json())) == request

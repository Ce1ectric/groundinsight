# tests/test_audit_pass19_line_switches.py
"""
Audit pass 19 -- open line switches in the pandapower import.

A pandapower ring operated open at a line switch (a ``net.switch`` row with
``et == "l"`` and ``closed == False``) arrived closed in groundinsight: the
importer evaluated bus-to-bus switches only, so the phase-conductor network
got a mesh that does not exist in operation and the fault current divided
over both halves of the ring.

In pandapower a line switch sits at one end of a line, between the bus
``bus`` and the line ``element``; open, it disconnects the phase conductors of
that line at that end. The cable screen is bonded to the station earth
independently of the switch and stays connected at both ends. The import now
gives such a line an open phase conductor (``phase_impedance_formula =
"nan"``, the open-end sentinel) and keeps its screen, and says so
(``IMPORT_LINE_SWITCH_OPEN``).

Second ways:

* the same pandapower network with the switch **closed** -- the ring is closed
  again and the phase current divides, so that import must differ;
* the same pandapower network with the line **removed** (out of service) -- its
  phase-conductor network is the one of the open switch, so the phase currents
  agree exactly, while the EPR differs by the screen that the open switch
  leaves in place;
* the documented by-hand construction: the closed-switch import with the
  line's phase conductor opened through ``update_branch`` -- identical to the
  import in EPR and branch currents;
* the independent reference solver of :mod:`tests._reference_mna`, fed with
  the data of the closed-switch import and the phase conductor of the line set
  open by hand (and, for the removed line, the line set inactive).
"""

from __future__ import annotations

import copy
import warnings

import polars as pl
import pytest

import groundinsight as gi
from groundinsight.diagnostics import InputWarning, collecting
from tests import _reference_mna as ref

pp = pytest.importorskip("pandapower")

FREQUENCIES = [50.0, 250.0]
TOL = 1e-12


# --------------------------------------------------------------------------
# builders
# --------------------------------------------------------------------------


def _ring(open_at=(("L2", "B"),)):
    """
    A 20 kV ring S-A-B-C-S with a load-break switch at both ends of every line,
    as in a ring main unit; the switches named in ``open_at`` are open.
    """
    net = pp.create_empty_network(name="ring")
    index = {name: pp.create_bus(net, vn_kv=20.0, name=name) for name in "SABC"}
    for name, a, b, length in (
        ("L1", "S", "A", 1.0), ("L2", "A", "B", 1.5),
        ("L3", "B", "C", 2.0), ("L4", "C", "S", 2.5),
    ):
        line = pp.create_line_from_parameters(
            net, from_bus=index[a], to_bus=index[b], length_km=length,
            r_ohm_per_km=0.206, x_ohm_per_km=0.122, c_nf_per_km=0.0, max_i_ka=0.4,
            name=name,
        )
        for end in (a, b):
            pp.create_switch(net, bus=index[end], element=line, et="l", closed=True,
                             name=f"{name}@{end}")
    for line, end in open_at:
        net.switch.loc[net.switch["name"] == f"{line}@{end}", "closed"] = False
    return net


def _defaults(**overrides):
    values = dict(
        default_bus_type=gi.BusType(
            name="station", system_type="MV", voltage_level=20.0,
            impedance_formula="0.5 + 0.01*rho + 0.02*j*f/50",
        ),
        default_branch_type=gi.BranchType(
            name="cable", grounding_conductor=True,
            self_impedance_formula="(0.3+0.6*j*f/50)*l",
            mutual_impedance_formula="(0.05+0.55*j*f/50)*l",
        ),
        frequencies=FREQUENCIES,
        rho=100.0,
        derive_phase_impedance=True,
    )
    values.update(overrides)
    return gi.ImportDefaults(**values)


def _imported(pp_net, **overrides):
    """The import and the records it raised."""
    with collecting() as records, warnings.catch_warnings():
        warnings.simplefilter("ignore")
        net = gi.from_pandapower(pp_net, defaults=_defaults(**overrides),
                                 voltage_level_kV=20.0)
    return net, list(records)


def _solve(net, fault_bus="B"):
    """Source at S, fault at ``fault_bus``; the stored result."""
    gi.create_source("Q", "S", {50.0: 1000.0, 250.0: 50.0}, network=net)
    gi.create_fault("F", fault_bus, {50.0: 1.0, 250.0: 1.0}, network=net)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        return gi.run_fault(net, "F")


def _epr(result, f):
    return {bus.name: complex(bus.uepr_freq[f]) for bus in result.buses}


def _shield(result, f):
    return {branch.name: complex(branch.i_s_freq[f]) for branch in result.branches}


def _phase(net, f):
    """Phase current of every branch (a branch without one carries 0 A)."""
    currents = net.electrical_network.phase_currents[f]
    return {name: complex(currents.get(name, 0j)) for name in net.branches}


def _close_all(pp_net):
    closed = copy.deepcopy(pp_net)
    closed.switch["closed"] = True
    return closed


def _records(records, code):
    return [r for r in records if r.code == code]


def _assert_close(a, b, *, rel=TOL):
    """Equal to ``rel`` of the largest value; a key missing on one side is 0."""
    scale = max(abs(v) for v in b.values()) or 1.0
    for key in set(a) | set(b):
        x, y = a.get(key, 0j), b.get(key, 0j)
        assert abs(x - y) <= rel * scale, (key, x, y)


# --------------------------------------------------------------------------
# the switch opens the phase conductor, the screen stays
# --------------------------------------------------------------------------


def test_an_open_line_switch_opens_the_phase_conductor_and_keeps_the_screen():
    net, records = _imported(_ring())
    branch = net.branches["L2"]
    assert branch.active                                   # the screen is there
    assert branch.type.phase_impedance_formula == "nan"    # the phase conductor is not
    assert branch.type.grounding_conductor
    assert branch.type.self_impedance_formula == "(0.3+0.6*j*f/50)*l"
    assert branch.type.mutual_impedance_formula == "(0.05+0.55*j*f/50)*l"
    for f in FREQUENCIES:
        z = branch.phase_impedance[f]
        assert z.real == float("inf") or z.imag == float("inf")
        assert complex(branch.self_impedance[f].real, branch.self_impedance[f].imag) != 0
    # The other lines keep their derived phase impedance.
    for name in ("L1", "L3", "L4"):
        assert net.branches[name].type.phase_impedance_formula.startswith("(0.206")
    # ... and the import says what it did, with the line and the switch.
    opened = _records(records, "IMPORT_LINE_SWITCH_OPEN")
    assert len(opened) == 1
    assert opened[0].category == "InputWarning"
    assert opened[0].elements == [("branch", "L2"), ("switch", "switch_L2@B")]
    assert "'B'" in opened[0].message and "screen" in opened[0].message


def test_the_fault_current_takes_the_only_closed_route():
    net, _ = _imported(_ring())
    _solve(net)
    for f in FREQUENCIES:
        phase = _phase(net, f)
        assert abs(phase["L1"]) <= TOL and abs(phase["L2"]) <= TOL
        injected = 1000.0 if f == 50.0 else 50.0
        assert abs(phase["L4"]) == pytest.approx(injected, rel=TOL)
        assert abs(phase["L3"]) == pytest.approx(injected, rel=TOL)


def _ratio(branch, f):
    """k = Z_m / Z_s of a branch at ``f``, from its stored impedances."""
    z_m, z_s = branch.mutual_impedance[f], branch.self_impedance[f]
    return complex(z_m.real, z_m.imag) / complex(z_s.real, z_s.imag)


def test_with_the_switch_closed_the_ring_is_closed_again():
    """
    Second way 1: closing the switch in pandapower closes the ring.

    With one cable type on every line the EPR does not depend on how the phase
    current divides: every coupling EMF is ``k * I_p`` with the same
    ``k = Z_m / Z_s``, which amounts to injecting ``(1 - k) * I_F``. The two
    imports share their EPR to rounding, while every shield current moves by
    ``-k`` times the change of its phase current. With an overhead line in the
    ring (another ``k``) the EPR moves as well.
    """
    opened, _ = _imported(_ring())
    closed, records = _imported(_close_all(_ring()))
    assert not _records(records, "IMPORT_LINE_SWITCH_OPEN")
    assert closed.branches["L2"].type.phase_impedance_formula.startswith("(0.206")
    result_open, result_closed = _solve(opened), _solve(closed)
    for f in FREQUENCIES:
        po, pc = _phase(opened, f), _phase(closed, f)
        injected = 1000.0 if f == 50.0 else 50.0
        assert abs(po["L2"]) <= TOL and abs(pc["L2"]) > 0.1 * injected
        _assert_close(_epr(result_open, f), _epr(result_closed, f))
        so, sc = _shield(result_open, f), _shield(result_closed, f)
        scale = max(abs(v) for v in sc.values())
        for name, branch in opened.branches.items():
            k = _ratio(branch, f)
            assert abs((so[name] - sc[name]) + k * (po[name] - pc[name])) <= TOL * scale, name

    overhead = gi.BranchType(
        name="overhead line", grounding_conductor=True,
        self_impedance_formula="(0.65+0.785*j*f/50)*l",
        mutual_impedance_formula="(0.049+0.372*j*f/50)*l",
        phase_impedance_formula="(0.32+0.62*j*f/50)*l",
    )
    mixed = {}
    for label, pp_net in (("open", _ring()), ("closed", _close_all(_ring()))):
        net, _ = _imported(pp_net)
        net.update_branch("L4", type=overhead)
        mixed[label] = _epr(_solve(net), 50.0)
    assert abs(mixed["open"]["B"] - mixed["closed"]["B"]) > 1e-3 * abs(mixed["closed"]["B"])


def test_with_the_line_removed_the_phase_currents_agree_and_the_screen_still_counts():
    """Second way 2: a removed line has the phase network of the open switch."""
    removed_pp = _close_all(_ring())
    removed_pp.line.loc[removed_pp.line["name"] == "L2", "in_service"] = False
    opened, _ = _imported(_ring())
    removed, _ = _imported(removed_pp)
    assert not removed.branches["L2"].active
    result_open, result_removed = _solve(opened), _solve(removed)
    for f in FREQUENCIES:
        _assert_close(_phase(opened, f), _phase(removed, f))
        # The screen of L2 still joins A and B with the switch open: it carries
        # current and changes the potentials, unlike the removed line.
        assert abs(_shield(result_open, f)["L2"]) > 1e-3
        assert abs(_shield(result_removed, f).get("L2", 0j)) == 0.0
        epr_open, epr_removed = _epr(result_open, f), _epr(result_removed, f)
        assert abs(epr_open["A"] - epr_removed["A"]) > 1e-6 * abs(epr_removed["A"])


def test_the_import_equals_the_documented_construction_by_hand():
    """Second way 3: the closed-switch import, phase conductor opened by hand."""
    opened, _ = _imported(_ring())
    by_hand, _ = _imported(_close_all(_ring()))
    line_type = by_hand.branches["L2"].type
    by_hand.update_branch("L2", type=line_type.model_copy(update={
        "name": "cable, phase open", "phase_impedance_formula": "nan"}))
    result_open, result_hand = _solve(opened), _solve(by_hand)
    for f in FREQUENCIES:
        _assert_close(_epr(result_open, f), _epr(result_hand, f))
        _assert_close(_shield(result_open, f), _shield(result_hand, f))
        _assert_close(_phase(opened, f), _phase(by_hand, f))


@pytest.mark.parametrize("variant", ["switch_open", "switch_closed", "line_removed"])
def test_every_potential_and_branch_current_agrees_with_the_reference_solver(variant):
    """Second way 4: the oracle, from the closed-switch data, edited by hand."""
    closed, _ = _imported(_close_all(_ring()))
    if variant == "switch_open":
        imported, _ = _imported(_ring())
    elif variant == "switch_closed":
        imported, _ = _imported(_close_all(_ring()))
    else:
        removed_pp = _close_all(_ring())
        removed_pp.line.loc[removed_pp.line["name"] == "L2", "in_service"] = False
        imported, _ = _imported(removed_pp)
    _solve(closed)          # gives the closed network its source and fault
    result = _solve(imported)
    for f in FREQUENCIES:
        rd = ref.reference_network(closed, f, "F")
        if variant == "switch_open":
            rd["branches"]["L2"]["zp"] = ref.INF        # phase conductor open
        elif variant == "line_removed":
            rd["branches"]["L2"]["active"] = False      # line gone altogether
        solution = ref.solve_oneway(rd)
        mapped = ref.to_package_frame(solution)
        _assert_close(_epr(result, f), mapped["u"])
        _assert_close(_shield(result, f), mapped["i_s"])
        _assert_close(_phase(imported, f), solution["I_p"])


# --------------------------------------------------------------------------
# what the switch state does not change
# --------------------------------------------------------------------------


def test_closed_line_switches_change_nothing():
    """All switches closed equals the same net without a switch table."""
    with_switches, records = _imported(_close_all(_ring()))
    bare_pp = _close_all(_ring())
    bare_pp.switch = bare_pp.switch.iloc[0:0]
    bare, _ = _imported(bare_pp)
    assert not _records(records, "IMPORT_LINE_SWITCH_OPEN")
    assert {n: b.type for n, b in with_switches.branches.items()} == {
        n: b.type for n, b in bare.branches.items()
    }
    result_a, result_b = _solve(with_switches), _solve(bare)
    for f in FREQUENCIES:
        assert _epr(result_a, f) == _epr(result_b, f)


def test_both_ends_open_is_one_record_naming_both_switches():
    net, records = _imported(_ring(open_at=(("L2", "A"), ("L2", "B"))))
    assert net.branches["L2"].type.phase_impedance_formula == "nan"
    opened = _records(records, "IMPORT_LINE_SWITCH_OPEN")
    assert len(opened) == 1
    assert opened[0].elements == [
        ("branch", "L2"), ("switch", "switch_L2@A"), ("switch", "switch_L2@B"),
    ]


def test_an_out_of_service_line_keeps_its_open_switch():
    """Inactive is inactive; the open phase conductor is kept for a later
    re-activation (an outage study flips Branch.active)."""
    pp_net = _ring()
    pp_net.line.loc[pp_net.line["name"] == "L2", "in_service"] = False
    net, records = _imported(pp_net)
    assert not net.branches["L2"].active
    assert net.branches["L2"].type.phase_impedance_formula == "nan"
    assert len(_records(records, "IMPORT_LINE_SWITCH_OPEN")) == 1


def test_switches_of_lines_or_transformers_that_are_not_imported_do_nothing():
    pp_net = _ring(open_at=())
    hv = pp.create_bus(pp_net, vn_kv=110.0, name="HV")
    hv2 = pp.create_bus(pp_net, vn_kv=110.0, name="HV2")
    hv_line = pp.create_line_from_parameters(
        pp_net, from_bus=hv, to_bus=hv2, length_km=10.0, r_ohm_per_km=0.1,
        x_ohm_per_km=0.4, c_nf_per_km=0.0, max_i_ka=1.0, name="HV line")
    pp.create_switch(pp_net, bus=hv, element=hv_line, et="l", closed=False, name="hv_ls")
    trafo = pp.create_transformer_from_parameters(
        pp_net, hv_bus=hv, lv_bus=0, sn_mva=25.0, vn_hv_kv=110.0, vn_lv_kv=20.0,
        vkr_percent=0.5, vk_percent=12.0, pfe_kw=0.0, i0_percent=0.0, name="T1")
    pp.create_switch(pp_net, bus=0, element=trafo, et="t", closed=False, name="t_ls")
    net, records = _imported(pp_net)
    assert not _records(records, "IMPORT_LINE_SWITCH_OPEN")
    assert all(b.type.phase_impedance_formula != "nan" for b in net.branches.values())
    preview = gi.preview_pandapower_import(pp_net, voltage_level_kV=20.0, defaults=_defaults())
    rows = {r["name"]: r for r in preview.filter(pl.col("kind") == "switch").iter_rows(named=True)}
    assert (rows["switch_hv_ls"]["status"], rows["switch_hv_ls"]["reason"]) == (
        "skip", "line_not_imported")
    assert (rows["switch_t_ls"]["status"], rows["switch_t_ls"]["reason"]) == (
        "skip", "switch_not_bus_to_bus")


def test_open_bus_to_bus_switches_stay_out_as_before():
    """An open bus-to-bus switch is the connection itself, not a conductor next
    to one -- it stays no connection (decided in audit pass 18)."""
    pp_net = _ring(open_at=())
    extra = pp.create_bus(pp_net, vn_kv=20.0, name="D")
    pp.create_switch(pp_net, bus=1, element=extra, et="b", closed=False, name="coupler")
    bond = gi.BranchType(name="bond", grounding_conductor=True,
                         self_impedance_formula="(0.05+0.3*j*f/50)*l",
                         mutual_impedance_formula="0")
    net, records = _imported(pp_net, switch_branch_type=bond)
    assert not any("D" in (b.from_bus, b.to_bus) for b in net.branches.values())
    assert not _records(records, "IMPORT_LINE_SWITCH_OPEN")


# --------------------------------------------------------------------------
# the preview shows the switches the way they are imported
# --------------------------------------------------------------------------


def test_the_preview_marks_the_open_switch_and_its_line():
    preview = gi.preview_pandapower_import(_ring(), voltage_level_kV=20.0, defaults=_defaults())
    switches = {r["name"]: r for r in preview.filter(pl.col("kind") == "switch").iter_rows(named=True)}
    assert len(switches) == 8
    assert (switches["switch_L2@B"]["status"], switches["switch_L2@B"]["reason"]) == (
        "keep", "line_switch_open")
    assert switches["switch_L2@B"]["from_bus"] == "B"
    for name, row in switches.items():
        if name != "switch_L2@B":
            assert (row["status"], row["reason"]) == ("skip", "line_switch_closed"), name
    lines = {r["name"]: r for r in preview.filter(pl.col("kind") == "line").iter_rows(named=True)}
    assert {n: (r["status"], r["reason"]) for n, r in lines.items()} == {
        "L1": ("keep", None), "L2": ("keep", "line_switch_open"),
        "L3": ("keep", None), "L4": ("keep", None),
    }
    # The preview needs no defaults for this: the switch state is in pandapower.
    bare = gi.preview_pandapower_import(_ring(), voltage_level_kV=20.0)
    assert bare.filter(pl.col("reason") == "line_switch_open").height == 2

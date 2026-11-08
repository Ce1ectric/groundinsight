# tests/test_audit_pass19_thermal.py
"""The 0 Hz component and the adiabatic check (audit pass 19, PH10).

The thermal checks take the 0 Hz bin out of ``I_th = I_ac * sqrt(m + n)``,
because ``m`` already carries the DC content of an alternating fault current
(audit pass 18, A10). A direct current that genuinely flows during ``T_k`` --
a DC railway, a cathodic-protection rectifier -- is then missing: it adds
about ``I_dc**2`` to ``I_th**2``, plus a cross term with the decaying DC offset
of either sign, so the check is unconservative. The checks now say so with a
ModelAssumptionWarning ``THERMAL_DC_EXCLUDED``.
"""

from __future__ import annotations

import math
import warnings

import numpy as np
import pytest

import groundinsight as gi
from groundinsight.diagnostics import ModelAssumptionWarning
from groundinsight.models.core_models import BranchType, BusType


def _network(frequencies, source_values, scalings=None):
    bus_type = BusType(
        name="bt", system_type="MV", voltage_level=20.0,
        impedance_formula="10",
        earthing_conductor_material="Cu",
        earthing_conductor_cross_section_mm2=50.0,
        electrode_material="Cu",
        electrode_cross_section_mm2=95.0,
    )
    branch_type = BranchType(
        name="brt", grounding_conductor=True,
        self_impedance_formula="(0.1+0.002*f*j)*l",
        mutual_impedance_formula="(0.05+0.001*f*j)*l",
        conductor_material="Cu",
        cross_section_mm2=35.0,
    )
    net = gi.create_network("thermal_dc", frequencies=frequencies)
    for bus in ("S", "A", "F"):
        net.add_bus(gi.create_bus(bus, bus_type, 100.0))
    net.add_branch(gi.create_branch("SA", branch_type, "S", "A", 1.0, 100.0))
    net.add_branch(gi.create_branch("AF", branch_type, "A", "F", 1.0, 100.0))
    gi.create_fault(
        "F", "F", scalings or {f: 1.0 for f in frequencies}, active=True,
        network=net, t_k_s=0.5,
    )
    gi.create_source("SRC", "S", source_values, network=net)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        gi.run_fault(net, "F")
    return net


@pytest.mark.parametrize("check", [gi.check_conductor_limits, gi.check_node_limits])
def test_a_dc_component_excluded_from_the_adiabatic_check_is_announced(check):
    net = _network([0.0, 50.0], {0.0: 700.0, 50.0: 1000.0})
    with pytest.warns(ModelAssumptionWarning, match="unconservative") as record:
        frame = check(net, "F", t_k=0.5, r_to_x=0.1)
    notes = [w.message for w in record if getattr(w.message, "code", None) == "THERMAL_DC_EXCLUDED"]
    assert len(notes) == 1
    assert ("fault", "F") in notes[0].elements
    # Second way for the size the message quotes: recompute the largest
    # increase of I_th if the direct current flowed during T_k from the
    # frame's own columns, I_th' = sqrt(I_ac^2 (m + n) + I_dc^2).
    increase = max(
        math.sqrt(row["I_ac_rms_A"] ** 2 * (row["m"] + row["n"]) + row["I_dc_A"] ** 2)
        / row["I_th_A"] - 1.0
        for row in frame.rows(named=True)
        if row["I_dc_A"]
    )
    assert f"{100 * increase:.1f} %" in str(notes[0])


@pytest.mark.parametrize("check", [gi.check_conductor_limits, gi.check_node_limits])
def test_no_note_without_a_direct_current(check):
    """No 0 Hz bin, or a 0 Hz bin the fault scales to zero: nothing excluded."""
    for net in (
        _network([50.0], {50.0: 1000.0}),
        _network([0.0, 50.0], {0.0: 700.0, 50.0: 1000.0}, scalings={0.0: 0.0, 50.0: 1.0}),
    ):
        with warnings.catch_warnings():
            warnings.simplefilter("error", ModelAssumptionWarning)
            check(net, "F", t_k=0.5, r_to_x=0.1)


def test_the_cross_term_the_note_leaves_out_is_what_the_docstring_says():
    """The Joule integral of I_dc + i_ac(t) with fully offset i_ac, integrated
    numerically, against the closed-form cross term and the quoted formula
    I_th**2 = I_ac**2 (m + n) + I_dc**2 (m from IEC 60909-0, n = 1):
    3.7 % above or 4.4 % below it, depending on the sign of the offset; with
    no offset the formula is exact."""
    f, t_k, i_ac, i_dc, r_to_x = 50.0, 0.5, 1000.0, 700.0, 0.1
    omega = 2 * math.pi * f
    tau = 1.0 / (omega * r_to_x)
    kappa = 1.02 + 0.98 * math.exp(-3 * r_to_x)
    m = (math.exp(4 * f * t_k * math.log(kappa - 1)) - 1) / (2 * f * t_k * math.log(kappa - 1))
    formula = i_ac ** 2 * (m + 1) + i_dc ** 2
    t = np.linspace(0.0, t_k, 400_001)

    integrate = getattr(np, "trapezoid", None) or np.trapz  # NumPy 1.x and 2.x

    def mean_square(i):
        return float(integrate(i * i, t)) / t_k

    no_offset = math.sqrt(2) * i_ac * np.sin(omega * t)
    assert mean_square(no_offset + i_dc) == pytest.approx(i_ac ** 2 + i_dc ** 2, rel=1e-9)
    ratios = {}
    for sign in (+1, -1):
        offset = sign * math.sqrt(2) * i_ac * (np.exp(-t / tau) - np.cos(omega * t))
        cross = mean_square(offset + i_dc) - mean_square(offset) - i_dc ** 2
        closed = sign * 2 * math.sqrt(2) * i_dc * i_ac * tau * (1 - math.exp(-t_k / tau)) / t_k
        assert cross == pytest.approx(closed, rel=1e-6)
        ratios[sign] = math.sqrt(mean_square(offset + i_dc) / formula)
    assert ratios[+1] == pytest.approx(1.037, abs=1e-3)
    assert ratios[-1] == pytest.approx(0.956, abs=1e-3)

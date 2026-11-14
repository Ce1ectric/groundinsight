# tests/test_audit_pass18_reference_cases.py
"""
Audit pass 18 -- two reference cases that could not fail (A7, A8).

A reference case earns its place by *detecting* something. Two of the six did
not:

- **A7** ``en50522_chain`` checked ``U_E = 3*I_0 * Z_E * r`` with all three
  factors read out of one result. The implementation defines ``Z_E = U_E/I_E``
  and ``r = |I_E|/|I_F|``, so the product returns ``U_E`` algebraically. The
  case agreed to 1e-16 whatever the model did -- and multiplying every
  electrode current by three made the reported deviation *smaller*, because
  ``Z_E`` fell by the factor ``r`` rose by.
- **A8** ``ladder_potential_decay`` assembled its nodal matrix from the two
  constants the function had in scope rather than from ``bus.impedance`` and
  ``branch.self_impedance``. Both sides of the comparison were then closed
  forms; the model took no part, and an electrode impedance a hundred times
  too large went unnoticed while ``ladder_input_impedance`` caught it at once.

The tests below are mutation tests: they break the model on purpose and
require the case to notice. A reference case that survives its own mutation is
a row saying "agrees" about nothing.
"""

from __future__ import annotations

import numpy as np
import scipy.sparse
import pytest

import groundinsight as gi
from groundinsight.analysis import reference
from groundinsight.analysis.reference import REFERENCE_CASES
from groundinsight.utils.earth_current import (
    EarthCurrentSplit,
    split_earth_currents as real_split,
)


def _case(name):
    return next(c for c in REFERENCE_CASES if c.name == name)


def _deviation(name):
    return _case(name).evaluate()["rel_deviation"]


# --- A7: the earth-return current -----------------------------------------------


def test_en50522_chain_agrees_on_the_untouched_model():
    row = _case("en50522_chain").evaluate()
    assert row["agrees"]
    assert row["rel_deviation"] < 1e-9


def test_en50522_chain_detects_a_falsified_earth_return_current(monkeypatch):
    """
    The mutation from the review: every electrode current tripled on the way
    into ``I_E``. The old case reported a *smaller* deviation for it; the new
    one has to report a factor of three.
    """
    def tripled(currents, **kwargs):
        split = real_split(currents, **kwargs)
        if split is None:
            return None
        return EarthCurrentSplit(
            i_earth=split.i_earth * 3.0,
            feeding_buses=split.feeding_buses,
            returning_buses=split.returning_buses,
            separation=split.separation,
        )

    monkeypatch.setattr(
        "groundinsight.electrical_network.split_earth_currents", tripled
    )
    row = _case("en50522_chain").evaluate()
    assert not row["agrees"]
    assert row["rel_deviation"] == pytest.approx(2.0, rel=1e-6)


def test_a_uniform_impedance_scaling_is_correctly_not_flagged(monkeypatch):
    """
    The counter-example that keeps the case honest. Scaling *every* impedance
    by the same factor leaves ``I_E`` unchanged -- the earth-return share of a
    linear network does not depend on its overall scale -- so the case must
    stay green. A case that went red here would be reacting to the mutation
    rather than to the physics.
    """
    real_resolved = gi.electrical_network.ElectricalNetwork.resolved_impedance

    def scaled(self, impedance, freq):
        value = real_resolved(self, impedance, freq)
        return None if value is None else value * 1.1

    monkeypatch.setattr(
        gi.electrical_network.ElectricalNetwork, "resolved_impedance", scaled
    )
    # Since audit pass 19 the assembly resolves the impedances as whole arrays
    # (``_NetworkTables.resolve``, the vectorised twin of
    # ``resolved_impedance``), so the uniform scaling has to reach that path
    # as well -- scaling only one of the two would be an inconsistent model,
    # not a uniform one. Real and imaginary parts are scaled separately so an
    # open end stays ``inf + inf*j``.
    tables = gi.electrical_network._NetworkTables
    real_resolve = tables.resolve

    def scaled_tables(self, substitutes):
        real_resolve(self, substitutes)
        for name in ("bus_z", "self_z", "phase_z", "vs_z"):
            values = getattr(self, name).copy()
            values.real *= 1.1
            values.imag *= 1.1
            setattr(self, name, values)

    monkeypatch.setattr(tables, "resolve", scaled_tables)
    assert _case("en50522_chain").evaluate()["agrees"]


# --- A8: the potential decay ----------------------------------------------------


def test_ladder_potential_decay_agrees_on_the_untouched_model():
    row = _case("ladder_potential_decay").evaluate()
    assert row["agrees"]


def test_ladder_potential_decay_detects_a_wrong_electrode_impedance(monkeypatch):
    """
    A hundredfold electrode impedance changes the propagation constant, so it
    changes the decay. The case read its matrix from local constants and saw
    nothing; it now reads it from the model and sees a factor of 200.

    The electrode alone is mutated rather than every impedance: the decay
    depends on the *ratio* ``Z'/(2 Z_e)``, so scaling both would leave a real
    physical quantity unchanged and would not be a defect to detect.
    """
    real_types = reference._types

    def inflated(z_electrode, z_self, z_mutual):
        return real_types(z_electrode * 100.0, z_self, z_mutual)

    monkeypatch.setattr(reference, "_types", inflated)
    row = _case("ladder_potential_decay").evaluate()
    assert not row["agrees"]
    assert row["rel_deviation"] > 1.0


def test_ladder_potential_decay_detects_a_wrong_section_impedance(monkeypatch):
    real_types = reference._types

    def inflated(z_electrode, z_self, z_mutual):
        return real_types(z_electrode, z_self * 20.0, z_mutual)

    monkeypatch.setattr(reference, "_types", inflated)
    assert not _case("ladder_potential_decay").evaluate()["agrees"]


def test_the_two_ladder_cases_now_react_to_the_same_mutation(monkeypatch):
    """
    The asymmetry that gave A8 away: ``ladder_input_impedance`` reads the model
    and reacted, ``ladder_potential_decay`` did not. Both must now.
    """
    real_types = reference._types

    def inflated(z_electrode, z_self, z_mutual):
        return real_types(z_electrode * 100.0, z_self, z_mutual)

    monkeypatch.setattr(reference, "_types", inflated)
    assert not _case("ladder_input_impedance").evaluate()["agrees"]
    assert not _case("ladder_potential_decay").evaluate()["agrees"]


# --- a mutation every case that touches the grid should see ---------------------


def test_a_twenty_percent_bias_on_every_bus_admittance_is_caught(monkeypatch):
    """
    The review's broader probe: a 20 % error on all bus admittances in the
    solver's Y was seen by only two of the six cases. With the two blind cases
    repaired, the chain cases see it too.
    """
    real_construct = gi.electrical_network.ElectricalNetwork._construct_Y_matrices

    def biased(self):
        # The solver factorises ``Y_sparse`` (audit pass 19, P2);
        # ``Y_matrices`` is only a dense view of it, so the bias goes into
        # the sparse matrices.
        real_construct(self)
        for freq in list(self.Y_sparse):
            bias = np.zeros(self.num_buses, dtype=complex)
            for name, idx in self.bus_indices.items():
                z = self.resolved_impedance(
                    self.network.buses[name].impedance.get(freq), freq
                )
                if z is not None and np.isfinite(z) and z != 0:
                    bias[idx] += 0.2 / z
            self.Y_sparse[freq] = (
                self.Y_sparse[freq] + scipy.sparse.diags(bias)
            ).tocsc()

    monkeypatch.setattr(
        gi.electrical_network.ElectricalNetwork,
        "_construct_Y_matrices",
        biased,
    )
    assert not _case("en50522_chain").evaluate()["agrees"]


# --- A12: the dead solve --------------------------------------------------------


def test_the_finite_earthing_case_builds_one_network(monkeypatch):
    """``_line_finite_earthing`` built and solved a three-bus chain it never
    read, next to the two-bus one it did."""
    built = []
    real_chain = reference._chain

    def counting(n_buses, *args, **kwargs):
        built.append(n_buses)
        return real_chain(n_buses, *args, **kwargs)

    monkeypatch.setattr(reference, "_chain", counting)
    reference._line_finite_earthing()
    assert built == [2]

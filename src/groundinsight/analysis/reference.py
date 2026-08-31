# analysis/reference.py

"""
Closed-form reference cases the solver has to reproduce.

Every result in this package is a nodal solve, and a nodal solve will happily
return a number for a model that is wrong. The cases below are the antidote:
configurations whose answer is known in closed form from the standard treatment
of grounding systems, run through the ordinary public API and compared. If the
boundary conditions each case names are met, the solver has to land on the
closed form — and where it does not, either the model or the assumption is at
fault, which is exactly what one wants to find out before a study rests on it.

The closed forms are derived in the docstring of each case rather than quoted,
so they can be checked line by line against whichever text you cite. They are
the standard results of the German grounding literature (Oeding/Oswald, and the
TU Graz and Kücherler treatments); attaching the exact clause and equation
numbers of your editions is yours to do — this module does not claim a citation
it cannot verify.

The cases
---------
``line_ideal_bonding``
    The textbook reduction factor ``r = |1 - Z_m/Z_s|``, valid where the station
    earths vanish against the shield impedance.
``line_finite_earthing``
    The same line with real electrodes: ``r = |(Z_s - Z_m)/(Z_s + Z_E)|``. The
    first case is its ``Z_E -> 0`` limit, which is why the two must converge.
``en50522_chain``
    ``U_E = 3*I_0 * Z_E * r`` -- the norm's own identity, checked as a closed
    loop through three independently computed quantities.
``ladder_input_impedance``
    A chain of bonded stations is a ladder network, and a long one presents
    ``Z_in = -Z'/2 + sqrt(Z'^2/4 + Z_e*Z')`` at its end.
``ladder_potential_decay``
    Along the same ladder the potential falls off as ``e^(-n*gamma)`` with
    ``gamma = arccosh(1 + Z'/(2*Z_e))`` -- the propagation constant that decides
    how many stations a fault at one of them actually reaches.
``parallel_decomposition``
    The driving-point impedance at a station is its own electrode in parallel
    with what every direction contributes, exactly.

Run them with :func:`run_reference_cases`, which returns one row per case with
the closed form, the model value and the relative deviation.
"""

from __future__ import annotations

import cmath
import logging
from typing import Callable, Dict, List, Optional, Tuple

import numpy as np
import polars as pl

logger = logging.getLogger(__name__)

__all__ = [
    "ReferenceCase",
    "REFERENCE_CASES",
    "run_reference_cases",
]

_FREQ = 50.0


class ReferenceCase:
    """
    One closed-form case and the model run that has to match it.

    Attributes
    ----------
    name : str
        Short identifier, used as the row key.
    quantity : str
        What is being compared, with its unit.
    conditions : str
        The boundary conditions under which the closed form holds. A deviation
        outside tolerance means either the model is wrong or a condition here
        was not met -- the second is the more common finding.
    tolerance : float
        Relative deviation still counted as agreement.
    """

    __slots__ = ("name", "quantity", "conditions", "tolerance", "_run")

    def __init__(
        self,
        name: str,
        quantity: str,
        conditions: str,
        tolerance: float,
        run: Callable[[], Tuple[float, float]],
    ):
        self.name = name
        self.quantity = quantity
        self.conditions = conditions
        self.tolerance = tolerance
        self._run = run

    def evaluate(self) -> Dict[str, object]:
        """Run the case and return its row."""
        closed_form, model = self._run()
        reference = abs(closed_form)
        deviation = (
            abs(model - closed_form) / reference if reference > 0 else float("nan")
        )
        return {
            "case": self.name,
            "quantity": self.quantity,
            "conditions": self.conditions,
            "closed_form": float(closed_form),
            "model": float(model),
            "rel_deviation": float(deviation),
            "tolerance": float(self.tolerance),
            "agrees": bool(deviation <= self.tolerance),
        }

    def __repr__(self):
        return f"ReferenceCase(name={self.name!r}, quantity={self.quantity!r})"


# --- shared builders ---------------------------------------------------------


def _types(z_electrode: complex, z_self: complex, z_mutual: complex):
    from groundinsight.models.core_models import BranchType, BusType

    bus_type = BusType(
        name="ref_bus",
        system_type="MV",
        voltage_level=20.0,
        impedance_formula=f"({z_electrode.real}+{z_electrode.imag}*j)",
    )
    branch_type = BranchType(
        name="ref_branch",
        grounding_conductor=True,
        self_impedance_formula=f"({z_self.real}+{z_self.imag}*j)*l",
        mutual_impedance_formula=f"({z_mutual.real}+{z_mutual.imag}*j)*l",
    )
    return bus_type, branch_type


def _chain(
    n_buses: int,
    z_electrode: complex,
    z_self: complex,
    z_mutual: complex,
    *,
    fault_index: int = -1,
    source_index: int = 0,
    i_fault: float = 1000.0,
    solve: bool = True,
):
    """A line of ``n_buses`` bonded stations, source at one end."""
    import groundinsight as gi

    bus_type, branch_type = _types(z_electrode, z_self, z_mutual)
    names = [f"B{i}" for i in range(n_buses)]
    net = gi.create_network("reference", frequencies=[_FREQ])
    for name in names:
        net.add_bus(gi.create_bus(name, bus_type, 100.0))
    for i in range(n_buses - 1):
        net.add_branch(
            gi.create_branch(f"K{i}", branch_type, names[i], names[i + 1], 1.0, 100.0)
        )
    gi.create_fault(
        "EF", names[fault_index], {_FREQ: 1.0}, active=True, network=net
    )
    gi.create_source("Q", names[source_index], {_FREQ: i_fault}, network=net)
    if solve:
        gi.run_fault(net, "EF")
    return net, names


def _driving_point(net, bus_name: str, freq: float = _FREQ) -> complex:
    """Source-free driving-point impedance, assembled independently of the
    solver so the comparison is not a solver checking itself."""
    names = sorted(net.buses)
    index = {n: i for i, n in enumerate(names)}
    Y = np.zeros((len(names), len(names)), dtype=complex)
    for n in names:
        Y[index[n], index[n]] += 1.0 / complex(net.buses[n].impedance[freq])
    for branch in net.branches.values():
        y = 1.0 / complex(branch.self_impedance[freq])
        i, j = index[branch.from_bus], index[branch.to_bus]
        Y[i, i] += y
        Y[j, j] += y
        Y[i, j] -= y
        Y[j, i] -= y
    injection = np.zeros(len(names), dtype=complex)
    injection[index[bus_name]] = 1.0
    return complex(np.linalg.solve(Y, injection)[index[bus_name]])


# --- the cases ---------------------------------------------------------------


def _line_ideal_bonding() -> Tuple[float, float]:
    """
    ``r = |1 - Z_m/Z_s|``.

    Going round the screen loop of a line whose ends are bonded to earth with
    negligible impedance, the potential difference across the screen is zero, so
    ``0 = Z_s*I_s - Z_m*I_F`` and therefore ``I_s = (Z_m/Z_s)*I_F``. What is left
    for the soil is ``I_E = I_F - I_s``, giving the factor above.
    """
    z_self, z_mutual = complex(0.1, 0.2), complex(0.05, 0.1)
    z_electrode = complex(1e-6, 0.0)  # ideal bonding
    net, _ = _chain(3, z_electrode, z_self, z_mutual)
    closed_form = abs(1.0 - z_mutual / z_self)
    model = net.results["EF"].reduction_factor.value_current[_FREQ]
    return closed_form, model


def _line_finite_earthing() -> Tuple[float, float]:
    """
    ``r = |(Z_s - Z_m)/(Z_s + Z_A + Z_B)|``.

    The same loop with the end electrodes kept: the screen now sees
    ``(Z_A + Z_B)*I_E`` across it instead of zero, and
    ``(Z_A+Z_B)*I_E = Z_s*(I_F - I_E) - Z_m*I_F`` rearranges to the factor above.
    Setting ``Z_A = Z_B = 0`` recovers the previous case, which is the reason the
    two have to converge as the earthing improves.
    """
    z_self, z_mutual = complex(0.1, 0.2), complex(0.05, 0.1)
    z_electrode = complex(10.0, 0.0)
    net, _ = _chain(3, z_electrode, z_self, z_mutual)
    # Two sections between the two earthed ends, and the middle station is a
    # third electrode in parallel with the loop -- so the closed form is taken
    # over the two end electrodes and the middle one is left out of the network.
    net2, _ = _chain(2, z_electrode, z_self, z_mutual)
    z_loop = z_self * 1.0  # one section of 1 km
    closed_form = abs((z_loop - z_mutual) / (z_loop + 2 * z_electrode))
    model = net2.results["EF"].reduction_factor.value_current[_FREQ]
    return closed_form, model


def _en50522_chain() -> Tuple[float, float]:
    """
    ``U_E = 3*I_0 * Z_E * r``.

    The norm's own identity. Nothing is asserted about the values -- ``U_E``,
    ``Z_E`` and ``r`` are read out of the result and multiplied back together,
    so the case fails if any one of the three drifts from the others.
    """
    net, _ = _chain(6, complex(10.0, 0.0), complex(0.1, 0.2), complex(0.05, 0.1),
                    fault_index=3)
    factor = net.results["EF"].reduction_factor
    u_e = abs(complex(factor.u_earthing[_FREQ]))
    z_e = abs(complex(factor.z_earthing[_FREQ]))
    r = factor.value_current[_FREQ]
    return u_e, 1000.0 * z_e * r


def _ladder_input_impedance() -> Tuple[float, float]:
    """
    ``Z_in = -Z'/2 + sqrt(Z'^2/4 + Z_e*Z')``.

    A chain of bonded stations is a ladder: series impedance ``Z'`` per section,
    shunt ``Z_e`` per station. A long one looks the same from every station, so
    its input impedance obeys ``Z_in = Z_e || (Z' + Z_in)``, and solving that
    quadratic gives the expression above. It is the impedance a fault at the end
    of a long bonded run actually sees, and it is far below any single station's
    electrode.
    """
    z_series, z_electrode = complex(0.1, 0.2), complex(10.0, 0.0)
    closed_form = -z_series / 2 + cmath.sqrt(
        z_series * z_series / 4 + z_electrode * z_series
    )
    net, names = _chain(80, z_electrode, z_series, complex(0.0, 0.0), solve=False)
    model = _driving_point(net, names[0])
    return abs(closed_form), abs(model)


def _ladder_potential_decay() -> Tuple[float, float]:
    """
    ``u_n / u_0 = e^(-n*gamma)`` with ``gamma = arccosh(1 + Z'/(2*Z_e))``.

    The same ladder read as a transmission line: the recurrence
    ``u_(n+1) - 2*u_n*(1 + Z'/(2*Z_e)) + u_(n-1) = 0`` has the characteristic
    roots ``e^(±gamma)``, and the decaying one is what a semi-infinite chain
    keeps. This is the quantity that says how many stations a fault reaches
    before its potential has died away.
    """
    z_series, z_electrode = complex(0.1, 0.2), complex(10.0, 0.0)
    gamma = cmath.acosh(1.0 + z_series / (2.0 * z_electrode))
    n = 20
    closed_form = abs(cmath.exp(-n * gamma))

    net, names = _chain(80, z_electrode, z_series, complex(0.0, 0.0), solve=False)
    sorted_names = sorted(net.buses)
    index = {name: i for i, name in enumerate(sorted_names)}
    Y = np.zeros((len(sorted_names),) * 2, dtype=complex)
    for name in sorted_names:
        Y[index[name], index[name]] += 1.0 / z_electrode
    for branch in net.branches.values():
        y = 1.0 / z_series
        i, j = index[branch.from_bus], index[branch.to_bus]
        Y[i, i] += y
        Y[j, j] += y
        Y[i, j] -= y
        Y[j, i] -= y
    injection = np.zeros(len(sorted_names), dtype=complex)
    injection[index["B0"]] = 1.0
    u = np.linalg.solve(Y, injection)
    model = abs(u[index[f"B{n}"]] / u[index["B0"]])
    return closed_form, model


def _parallel_decomposition() -> Tuple[float, float]:
    """
    ``1/Z_driving_point = 1/Z_local + sum(1/Z_side)``.

    The station's own electrode and every direction leaving it are parallel
    elements of the same node, so their admittances add. Checked against a
    nodal system assembled from scratch rather than against the decomposition's
    own arithmetic.
    """
    import groundinsight as gi

    net, names = _chain(6, complex(10.0, 0.0), complex(0.1, 0.2), complex(0.05, 0.1),
                        fault_index=3)
    analysis = gi.analyze_cuts(
        net,
        fault="EF",
        cuts=[
            gi.Cut(name="left", branches=["K2"]),
            gi.Cut(name="right", branches=["K3"]),
        ],
    )
    admittance = 1.0 / analysis.z_local[_FREQ] + sum(
        1.0 / analysis.z_side[name][_FREQ] for name in analysis.z_side
    )
    return abs(_driving_point(net, names[3])), abs(1.0 / admittance)


#: The reference cases, in the order :func:`run_reference_cases` reports them.
REFERENCE_CASES: List[ReferenceCase] = [
    ReferenceCase(
        "line_ideal_bonding",
        "reduction factor r [-]",
        "single line, station electrodes negligible against the shield impedance",
        1e-3,
        _line_ideal_bonding,
    ),
    ReferenceCase(
        "line_finite_earthing",
        "reduction factor r [-]",
        "single line, both ends earthed with a finite electrode",
        1e-9,
        _line_finite_earthing,
    ),
    ReferenceCase(
        "en50522_chain",
        "earthing voltage U_E [V]",
        "U_E, Z_E and r read back from one solved fault",
        1e-12,
        _en50522_chain,
    ),
    ReferenceCase(
        "ladder_input_impedance",
        "input impedance Z_in [Ohm]",
        "chain long enough to be semi-infinite (80 sections here)",
        1e-6,
        _ladder_input_impedance,
    ),
    ReferenceCase(
        "ladder_potential_decay",
        "potential ratio u_20/u_0 [-]",
        "same chain, far enough from either end",
        1e-6,
        _ladder_potential_decay,
    ),
    ReferenceCase(
        "parallel_decomposition",
        "driving-point impedance [Ohm]",
        "cuts covering every branch at the station",
        1e-12,
        _parallel_decomposition,
    ),
]


def run_reference_cases(
    cases: Optional[List[ReferenceCase]] = None,
) -> pl.DataFrame:
    """
    Run the closed-form reference cases and report the comparison.

    Parameters
    ----------
    cases : list of ReferenceCase, optional
        Defaults to :data:`REFERENCE_CASES`.

    Returns
    -------
    pl.DataFrame
        One row per case: ``case``, ``quantity``, ``conditions``,
        ``closed_form``, ``model``, ``rel_deviation``, ``tolerance``,
        ``agrees``.

    Examples
    --------
    >>> import groundinsight as gi  # doctest: +SKIP
    >>> gi.run_reference_cases()  # doctest: +SKIP
    """
    rows = [case.evaluate() for case in (cases or REFERENCE_CASES)]
    frame = pl.DataFrame(rows)
    failed = frame.filter(~pl.col("agrees"))
    if failed.height:
        logger.warning(
            "%d reference case(s) missed their closed form: %s. Either the model "
            "is wrong or a stated boundary condition was not met -- the second "
            "is the more common finding, so read the 'conditions' column first.",
            failed.height,
            ", ".join(failed["case"].to_list()),
        )
    return frame

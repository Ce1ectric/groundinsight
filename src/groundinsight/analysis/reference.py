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
the standard results of the German grounding literature (Oeding/Oswald, the
TU Graz treatments and B. Küchler's dissertation, TU Dresden 2024,
urn:nbn:de:bsz:14-qucosa2-901763); attaching the exact clause and equation
numbers of your editions is yours to do — this module does not claim a citation
it cannot verify.

The cases
---------
``line_ideal_bonding``
    The textbook reduction factor ``r = |1 - Z_m/Z_s|``, valid where the station
    earths vanish against the shield impedance.
``line_finite_earthing``
    The same line with a real electrode at the source station:
    ``r = |(Z_s - Z_m)/(Z_s + Z_A)|``. The first case is its ``Z_A -> 0``
    limit, which is why the two must converge.
``en50522_chain``
    The reduction factor of a six-station chain as the current into an ideal
    electrode at the fault bus over ``I_F``, re-derived from a nodal system
    assembled here from the model's own impedances with the fault bus held
    at remote earth. The case does not check ``U_E = r * Z_E * I_F`` with
    all factors read out of one result: ``r`` is defined through that
    relation, and a case built on it would agree to 1e-16 whatever the model
    did.
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

    Parameters
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
    run : callable
        ``run() -> (closed_form, model)``: builds the case's network, solves
        it and returns both numbers.

    Attributes
    ----------
    name, quantity, conditions, tolerance
        The constructor arguments of the same name.

    Examples
    --------
    >>> import groundinsight as gi
    >>> case = gi.REFERENCE_CASES[0]
    >>> isinstance(case, gi.ReferenceCase)
    True
    >>> row = case.evaluate()
    >>> row["case"] == case.name, row["agrees"]
    (True, True)
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
        """
        Run the case and return its row.

        Returns
        -------
        dict
            The row of :func:`run_reference_cases`: ``case``, ``quantity``,
            ``conditions``, ``closed_form``, ``model``, ``rel_deviation``,
            ``tolerance`` and ``agrees``.
        """
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


def _assemble_from_model(net, freq: float = _FREQ):
    """
    Nodal matrix assembled from the **model's own** stored impedances.

    Independent of the solver -- it shares no code with it -- but not
    independent of the model: every value comes from ``bus.impedance`` and
    ``branch.self_impedance``, which is what gives a case built on it the
    power to notice when those values are wrong. A matrix built from the
    constants the case happens to have in scope notices nothing: it is then a
    second copy of the closed form, and the two agree whatever the model does.

    Parameters
    ----------
    net : Network
        The reference network.
    freq : float, optional
        Frequency to read the impedances at. Defaults to 50 Hz.

    Returns
    -------
    tuple
        ``(Y, index, names)`` with ``index`` mapping bus name to row.
    """
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
    return Y, index, names


def _driving_point(net, bus_name: str, freq: float = _FREQ) -> complex:
    """
    Source-free driving-point impedance, assembled independently of the solver.

    The comparison would otherwise be a solver checking itself.
    """
    Y, index, names = _assemble_from_model(net, freq)
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
    model = net.results["EF"].reduction_factor.value[_FREQ]
    return closed_form, model


def _line_finite_earthing() -> Tuple[float, float]:
    """
    ``r = |(Z_s - Z_m)/(Z_s + Z_A)|``.

    One cable from the source station ``A`` (electrode ``Z_A``) to the fault
    station ``B``. The reduction factor is the current into an ideal
    electrode at ``B`` over ``I_F`` (Norton), so ``B`` is held at remote
    earth: the screen sees ``u_A`` across it, ``u_A = Z_s*I_s + Z_m*I_F``
    with the screen current ``I_s`` from ``A`` to ``B``, and the current
    balance at ``A``, where the source takes ``I_F`` out,
    ``u_A/Z_A + I_s = -I_F``, gives ``I_s = -I_F (Z_A + Z_m)/(Z_A + Z_s)``.
    The ideal electrode at ``B`` takes ``I_F + I_s``, which rearranges to the
    factor above. The electrode at ``B`` does not enter; setting ``Z_A = 0``
    recovers the previous case, which is the reason the two have to converge
    as the earthing improves.
    """
    z_self, z_mutual = complex(0.1, 0.2), complex(0.05, 0.1)
    z_electrode = complex(10.0, 0.0)
    net2, _ = _chain(2, z_electrode, z_self, z_mutual)
    z_loop = z_self * 1.0  # one section of 1 km
    closed_form = abs((z_loop - z_mutual) / (z_loop + z_electrode))
    model = net2.results["EF"].reduction_factor.value[_FREQ]
    return closed_form, model


def _en50522_chain() -> Tuple[float, float]:
    """
    ``r = |I_short| / |I_F|`` with the fault bus held at remote earth.

    The reduction factor of a six-station chain, computed twice. The model
    value is ``ResultReductionFactor.value``, which the solver forms as
    ``|U_E / Z_E| / |I_F|``. The reference shorts the fault bus instead: a
    nodal system assembled here from ``bus.impedance`` and
    ``branch.self_impedance``, solved against the injection vector of the
    solved fault with the fault bus's potential held at zero, and the
    current the ideal electrode takes read from the current balance of
    that bus. By Norton's theorem the two agree; the matrix, the solve and
    the short circuit are all done a second way.

    What this case does *not* do: check ``U_E = r * Z_E * I_F`` with all
    three factors read out of one result. ``r`` is defined through that
    relation, so such a case would agree to machine precision whatever the
    model did. A tautology that reports 1e-16 is worse than no case at all,
    because the row it writes says "agrees".

    The mutual injection vector is taken from the solved fault rather than
    rebuilt: ``line_ideal_bonding`` and ``line_finite_earthing`` are the cases
    that test the coupling model, and rebuilding it here would only be a second
    copy of the same assumption.
    """
    import groundinsight as gi  # noqa: F401 -- keeps the public API in the path

    net, names = _chain(
        6, complex(10.0, 0.0), complex(0.1, 0.2), complex(0.05, 0.1),
        fault_index=3,
    )
    factor = net.results["EF"].reduction_factor
    model = factor.value[_FREQ]

    electrical = net.electrical_network
    Y, index, bus_names = _assemble_from_model(net)
    injection = np.array(
        [complex(electrical.i_vectors[_FREQ][electrical.bus_indices[name]]) for name in bus_names]
    )
    fault = index[names[3]]
    keep = [i for i in range(len(bus_names)) if i != fault]
    u = np.zeros(len(bus_names), dtype=complex)
    u[keep] = np.linalg.solve(Y[np.ix_(keep, keep)], injection[keep])
    # KCL of the shorted bus: what the injection leaves over after the
    # branches is the current into the ideal electrode.
    i_short = injection[fault] - Y[fault] @ u
    reference = abs(i_short) / abs(complex(factor.i_fault[_FREQ]))
    return reference, model


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

    The nodal matrix is assembled from ``bus.impedance`` and
    ``branch.self_impedance``, i.e. from what the *model* evaluated out of the
    two type formulas, not from the two local constants below: built from
    those, the case would be blind to the model entirely -- an electrode
    impedance a hundred times too large would leave both sides of the
    comparison untouched.
    """
    z_series, z_electrode = complex(0.1, 0.2), complex(10.0, 0.0)
    gamma = cmath.acosh(1.0 + z_series / (2.0 * z_electrode))
    n = 20
    closed_form = abs(cmath.exp(-n * gamma))

    net, names = _chain(80, z_electrode, z_series, complex(0.0, 0.0), solve=False)
    Y, index, sorted_names = _assemble_from_model(net)
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
        "single line, source station earthed with a finite electrode",
        1e-9,
        _line_finite_earthing,
    ),
    ReferenceCase(
        "en50522_chain",
        "reduction factor r [-]",
        "six-station chain, r re-derived as the current into an ideal fault "
        "electrode of an independently assembled nodal system",
        1e-9,
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
    >>> import groundinsight as gi
    >>> table = gi.run_reference_cases()
    >>> table.height == len(gi.REFERENCE_CASES), table["agrees"].all()
    (True, True)
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

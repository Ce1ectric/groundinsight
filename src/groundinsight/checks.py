# checks.py

"""
Pre-flight check of a network: every problem at once, as data.

``run_fault`` and the ``create_*`` factories report a problem as the *first*
``ValueError`` they meet. That suits a script, which fixes one thing and runs
again; it does not suit a form-based editor, which has to show every problem
of the network before the user presses "solve", each next to the element it
is about. Worse, a few problems are not reported at all: a branch whose end
names a bus that does not exist, or a source on such a bus, is dropped by the
solver without a word and the result reads 0 V. The editing API refuses such
a reference on an element that belongs to a network, but a network loaded
from a hand-edited project file, from a database written by another tool or
from dictionaries a script filled itself is not checked when it is loaded.

:func:`check_network` answers the editor's question. It returns a list of
:class:`~groundinsight.diagnostics.Diagnostic` records -- the same type as
``Result.diagnostics`` -- with a stable ``code`` from :data:`CHECK_CODES`, the
elements concerned and a severity:

``"error"``
    ``run_fault`` would raise, or would return an answer that does not
    describe the network (an element silently dropped).
``"warning"``
    The network solves, but part of the input has no effect or a documented
    default stands in for a missing value.

It never raises: a check that cannot run on a network malformed in some way
nobody anticipated is reported as ``CHECK_FAILED`` and the other checks still
run.

Design
------
Wherever the solver already has a validation path, the check *calls* it and
turns its exception or warning into a record, so the two cannot disagree: the
``Network.frequencies`` validator, the passivity rule
(:func:`~groundinsight.utils.impedance_calculator.check_passive_impedance`),
the formula evaluation of ``Bus``/``Branch.calculate_impedance`` (run on a
copy) and the reachability search ``Network.sources_reaching``. Package
warnings raised along the way are collected with
:func:`~groundinsight.diagnostics.collecting` and keep their own codes. The
selection of elements mirrors the solver's: only what enters the nodal system
is judged (an inactive bus may carry any impedance it likes). New code exists
only for what has no code path yet -- references to buses that do not exist,
the frequency coverage of sources, isolated buses and earthing islands without
any reference to earth.

The check is structural: it neither assembles nor solves. That keeps it cheap
enough to run after every edit (linear in elements x frequencies), and it is
also its limit -- conditions only a solve can see, such as the model
assumption behind a phase-current proxy, arrive with the result in
``Result.diagnostics``.
"""

import logging
import math
from typing import Callable, Dict, List, Mapping, Optional, Sequence, Tuple

from .diagnostics import Diagnostic, collecting, unique
from .models.core_models import (
    Branch,
    Bus,
    Fault,
    Network,
    Source,
    validate_frequencies,
)
from .utils.impedance_calculator import check_passive_impedance

logger = logging.getLogger(__name__)

__all__ = ["CHECK_CODES", "check_network"]

_ERROR = "error"
_WARNING = "warning"

CHECK_CODES: Dict[str, Tuple[str, str]] = {
    "NOT_A_NETWORK": (_ERROR, "The argument is not a Network."),
    "CHECK_FAILED": (
        _ERROR,
        "A check raised unexpectedly; that aspect of the network could not be "
        "judged.",
    ),
    "ELEMENT_INVALID": (
        _ERROR,
        "An entry of buses, branches, sources or faults is not an element of "
        "the expected type.",
    ),
    "FREQUENCIES_INVALID": (
        _ERROR,
        "The frequency list is empty or holds duplicates, non-finite or "
        "negative values.",
    ),
    "NO_ACTIVE_BUS": (_ERROR, "The network has no active bus."),
    "NO_SOURCE": (_ERROR, "The network defines no source."),
    "NO_FAULT": (_ERROR, "The network defines no fault."),
    "FAULT_UNKNOWN": (_ERROR, "The fault passed as fault= does not exist."),
    "BRANCH_UNKNOWN_BUS": (
        _ERROR,
        "A branch end names a bus that does not exist; the solver would drop "
        "the branch.",
    ),
    "SOURCE_UNKNOWN_BUS": (
        _ERROR,
        "A source names a bus that does not exist; the solver would ignore it.",
    ),
    "FAULT_UNKNOWN_BUS": (_ERROR, "A fault names a bus that does not exist."),
    "FAULT_BUS_INACTIVE": (_ERROR, "A fault sits on an inactive bus."),
    "SOURCE_BUS_INACTIVE": (
        _WARNING,
        "A source sits on an inactive bus and injects nothing.",
    ),
    "IMPEDANCE_MISSING": (
        _ERROR,
        "An element of the nodal system has no impedance at a network "
        "frequency.",
    ),
    "FORMULA_ERROR": (
        _ERROR,
        "An element has no stored impedance and its type formula cannot be "
        "evaluated.",
    ),
    "IMPEDANCE_NOT_PASSIVE": (
        _ERROR,
        "An impedance that is inverted is zero, too small to invert or has a "
        "negative real part.",
    ),
    "IMPEDANCE_NOT_FINITE": (_ERROR, "An impedance is NaN at a network frequency."),
    "SOURCE_FREQUENCY_NONE": (
        _ERROR,
        "A source has a value at none of the network frequencies.",
    ),
    "SOURCE_FREQUENCY_GAP": (
        _WARNING,
        "A source has no value at some network frequencies; it injects nothing "
        "there.",
    ),
    "SOURCE_FREQUENCY_OUTSIDE_NETWORK": (
        _WARNING,
        "A source has values at frequencies the network does not solve; they "
        "are ignored.",
    ),
    "SOURCE_VALUE_NOT_FINITE": (
        _ERROR,
        "A source value is NaN or infinite at a network frequency.",
    ),
    "SCALINGS_INCOMPLETE": (
        _WARNING,
        "A fault's scalings miss network frequencies, which are scaled with "
        "1.0.",
    ),
    "FAULT_NOT_REACHED": (
        _WARNING,
        "No source reaches the fault over active elements; every potential "
        "is 0 V.",
    ),
    "ISOLATED_BUS": (
        _WARNING,
        "An active bus has no active branch to any other active bus.",
    ),
    "FLOATING_ISLAND": (
        _ERROR,
        "A group of buses joined by grounding conductors has no reference to "
        "earth; its potential is undefined.",
    ),
}
"""Every code :func:`check_network` produces itself, mapped to its severity
(``"error"`` or ``"warning"``) and meaning. Package warnings collected during
the check (``FREQUENCY_ORDER``, ``DC_LIMIT``) keep their own codes. Programs
branch on the code, never on the message text."""

ElementRef = Tuple[str, str]


class _Report:
    """
    The state one :func:`check_network` call builds up.

    Holds the network, the checked fault names, the cleaned element views the
    individual checks share, and the records found so far.

    Parameters
    ----------
    network : Network
        The network under check.
    """

    def __init__(self, network: Network) -> None:
        self.network = network
        self.name = str(getattr(network, "name", "?"))
        self.records: List[Diagnostic] = []
        self.buses: Dict[str, Bus] = {}
        self.branches: Dict[str, Branch] = {}
        self.sources: Dict[str, Source] = {}
        self.faults: Dict[str, Fault] = {}
        self.frequencies: List[float] = []
        self.checked_faults: List[str] = []
        # Set by the reference checks: a topology with dangling references
        # would make the graph-based checks crash or mislead.
        self.consistent = True

    def add(self, code: str, message: str, elements: Sequence[ElementRef] = ()) -> None:
        """
        Record one problem.

        Parameters
        ----------
        code : str
            A key of :data:`CHECK_CODES`; the severity is taken from there.
        message : str
            The text shown to the user.
        elements : sequence of tuple of str, optional
            ``(kind, name)`` pairs of the elements concerned.
        """
        severity = CHECK_CODES[code][0]
        self.records.append(
            Diagnostic(
                code=code,
                severity=severity,
                category="InputWarning",
                message=message,
                elements=[(str(kind), str(name)) for kind, name in elements],
            )
        )

    def run(self, label: str, check: Callable[["_Report"], None]) -> None:
        """
        Run one check, collecting its package warnings and containing a crash.

        Parameters
        ----------
        label : str
            Human-readable name of the check, used in a ``CHECK_FAILED``
            message.
        check : callable
            ``check(report)``; adds its records to ``report``.
        """
        with collecting() as sink:
            try:
                check(self)
            except Exception as exc:  # noqa: BLE001 -- check_network never raises
                logger.debug("check %s failed", label, exc_info=True)
                self.add(
                    "CHECK_FAILED",
                    f"The {label} check could not run on network '{self.name}': "
                    f"{type(exc).__name__}: {exc}",
                    [("network", self.name)],
                )
        self.records.extend(sink)


def _render(frequencies: Sequence[float]) -> str:
    """Render a frequency list as ``"50, 250 Hz"``."""
    return ", ".join(f"{f:g}" for f in frequencies) + " Hz"


def _is_nan(value) -> bool:
    """``True`` if either part of a stored complex value is NaN."""
    return math.isnan(float(value.real)) or math.isnan(float(value.imag))


def _mapping(report: _Report, field: str, kind: str, cls: type) -> Dict[str, object]:
    """
    The well-formed entries of one element mapping of the network.

    Parameters
    ----------
    report : _Report
        Receives an ``ELEMENT_INVALID`` record per malformed entry.
    field : str
        ``"buses"``, ``"branches"``, ``"sources"`` or ``"faults"``.
    kind : str
        Element kind used in the records.
    cls : type
        The class every entry must be an instance of.

    Returns
    -------
    dict
        The entries that are instances of ``cls``, keyed by name.
    """
    raw = getattr(report.network, field, None)
    if not isinstance(raw, Mapping):
        report.add(
            "ELEMENT_INVALID",
            f"Network '{report.name}'.{field} is {type(raw).__name__}, not a "
            f"mapping of name to {cls.__name__}.",
            [("network", report.name)],
        )
        report.consistent = False
        return {}
    good: Dict[str, object] = {}
    for name, element in raw.items():
        if isinstance(element, cls):
            good[str(name)] = element
        else:
            report.add(
                "ELEMENT_INVALID",
                f"{field}['{name}'] is {type(element).__name__}, not a "
                f"{cls.__name__}.",
                [(kind, str(name))],
            )
            report.consistent = False
    return good


# --------------------------------------------------------------------------
# the checks
# --------------------------------------------------------------------------


def _check_elements(report: _Report) -> None:
    """Collect the well-formed elements; report the malformed ones."""
    report.buses = _mapping(report, "buses", "bus", Bus)
    report.branches = _mapping(report, "branches", "branch", Branch)
    report.sources = _mapping(report, "sources", "source", Source)
    report.faults = _mapping(report, "faults", "fault", Fault)


def _check_frequencies(report: _Report) -> None:
    """
    Re-check the stored list against the rules of ``Network.frequencies``.

    The rules (:func:`~groundinsight.models.core_models.validate_frequencies`)
    guard construction and assignment, but an in-place edit
    (``net.frequencies.append(50.0)``, ``.clear()``) bypasses it. The usable
    part of the list -- finite, non-negative, first occurrence -- is kept for
    the frequency-dependent checks either way.
    """
    raw = list(getattr(report.network, "frequencies", None) or [])
    try:
        validate_frequencies(list(raw))
    except (ValueError, TypeError) as exc:
        report.add("FREQUENCIES_INVALID", str(exc), [("network", report.name)])
    usable: List[float] = []
    for value in raw:
        try:
            value = float(value)
        except (TypeError, ValueError):
            continue
        if math.isfinite(value) and value >= 0 and value not in usable:
            usable.append(value)
    report.frequencies = usable


def _check_excitation(report: _Report) -> None:
    """The network-level conditions ``run_fault`` rejects outright."""
    if not any(bus.active for bus in report.buses.values()):
        report.add(
            "NO_ACTIVE_BUS",
            f"Network '{report.name}' has no active bus, so there is no nodal "
            "system to solve.",
            [("network", report.name)],
        )
    if not report.sources:
        report.add(
            "NO_SOURCE",
            f"Network '{report.name}' defines no source; every potential would "
            "be 0 V. Add one with gi.create_source(...) or "
            "gi.create_voltage_source(...).",
            [("network", report.name)],
        )
    if not report.faults:
        report.add(
            "NO_FAULT",
            f"Network '{report.name}' defines no fault. Add one with "
            "gi.create_fault(...).",
            [("network", report.name)],
        )


def _check_references(report: _Report) -> None:
    """Every bus name an element refers to has to exist."""
    for name, branch in report.branches.items():
        for end in (branch.from_bus, branch.to_bus):
            if end not in report.buses:
                report.add(
                    "BRANCH_UNKNOWN_BUS",
                    f"Branch '{name}' connects to bus '{end}', which is not in "
                    f"network '{report.name}'. The solver would drop the branch "
                    "without a word.",
                    [("branch", name), ("bus", end)],
                )
                report.consistent = False
    for name, source in report.sources.items():
        if source.bus not in report.buses:
            report.add(
                "SOURCE_UNKNOWN_BUS",
                f"Source '{name}' sits on bus '{source.bus}', which is not in "
                f"network '{report.name}'. The solver would ignore the source "
                "and report 0 V.",
                [("source", name), ("bus", source.bus)],
            )
            report.consistent = False
        elif not report.buses[source.bus].active:
            report.add(
                "SOURCE_BUS_INACTIVE",
                f"Source '{name}' sits on the inactive bus '{source.bus}' and "
                "injects nothing.",
                [("source", name), ("bus", source.bus)],
            )


def _impedance_checks(
    report: _Report,
    stored: Optional[Mapping[float, object]],
    label: str,
    elements: Sequence[ElementRef],
    *,
    formula: Optional[str],
    passive: bool,
    evaluate: Optional[Callable[[], None]],
    type_ref: Optional[ElementRef] = None,
) -> None:
    """
    Coverage, NaN and passivity of one stored impedance dictionary.

    Parameters
    ----------
    report : _Report
        Receives the records.
    stored : mapping of float to ComplexNumber or None
        The impedance dictionary as stored on the element.
    label : str
        How the element is named in messages, e.g. ``"bus 'A' (grounding
        impedance)"`` -- the solver's own spelling.
    elements : sequence of tuple of str
        Element references of the records.
    formula : str or None
        The formula the values came from, for the passivity message.
    passive : bool
        Whether the impedance is inverted by the solver (then the passivity
        rule applies; mutual impedances are never inverted).
    evaluate : callable or None
        Re-evaluates the element's formulas on a copy; called only when values
        are missing, to tell "not computed" from "cannot be computed".
    type_ref : tuple of str, optional
        ``("bus_type", name)`` or ``("branch_type", name)``, added to the
        elements of a ``FORMULA_ERROR`` record, where the type is the culprit.
    """
    stored = stored or {}
    missing = [f for f in report.frequencies if f not in stored]
    if missing:
        failure = None
        if evaluate is not None:
            try:
                evaluate()
            except Exception as exc:  # noqa: BLE001 -- reported as a record
                failure = exc
        if failure is not None:
            report.add(
                "FORMULA_ERROR",
                f"The {label} has no value at {_render(missing)}, and its "
                f"formula {formula!r} cannot be evaluated: {failure}",
                list(elements) + ([type_ref] if type_ref is not None else []),
            )
        else:
            report.add(
                "IMPEDANCE_MISSING",
                f"The {label} has no value at {_render(missing)}. Impedances "
                "are evaluated when an element is added, not at solve time; "
                "recompute them with element.calculate_impedance("
                "network.frequencies) or re-add the element.",
                elements,
            )
    present = {f: stored[f] for f in report.frequencies if f in stored}
    nan_at = [f for f, value in present.items() if _is_nan(value)]
    if nan_at:
        report.add(
            "IMPEDANCE_NOT_FINITE",
            f"The {label} is NaN at {_render(nan_at)}: a formula that failed "
            "numerically or a value written by hand. NaN poisons the whole "
            "nodal system of that frequency.",
            elements,
        )
    if passive:
        finite = {f: value for f, value in present.items() if f not in nan_at}
        try:
            check_passive_impedance(finite, element=label, formula_str=formula)
        except ValueError as exc:
            report.add("IMPEDANCE_NOT_PASSIVE", str(exc), elements)


def _check_impedances(report: _Report) -> None:
    """
    Impedances of exactly the elements the solver inverts or reads.

    Same selection as ``ElectricalNetwork._validate_frequency_coverage`` and
    ``_validate_passive_impedances``: active buses; active branches between
    two active buses (self impedance for grounding conductors, mutual impedance
    always); the source impedance of voltage sources on an active bus.
    """
    frequencies = list(report.frequencies)
    for name, bus in report.buses.items():
        if not bus.active:
            continue

        def evaluate(bus=bus) -> None:
            bus.model_copy().calculate_impedance(frequencies)

        _impedance_checks(
            report,
            bus.impedance,
            f"bus '{name}' (grounding impedance)",
            [("bus", name)],
            formula=bus.type.impedance_formula,
            passive=True,
            evaluate=evaluate,
            type_ref=("bus_type", bus.type.name),
        )

    for name, branch in report.branches.items():
        ends = (branch.from_bus, branch.to_bus)
        if not branch.active or any(
            end not in report.buses or not report.buses[end].active for end in ends
        ):
            continue

        def evaluate(branch=branch) -> None:
            branch.model_copy().calculate_impedance(frequencies)

        type_ref = ("branch_type", branch.type.name)
        if branch.type.grounding_conductor:
            _impedance_checks(
                report,
                branch.self_impedance,
                f"branch '{name}' (self impedance)",
                [("branch", name)],
                formula=branch.type.self_impedance_formula,
                passive=True,
                evaluate=evaluate,
                type_ref=type_ref,
            )
        _impedance_checks(
            report,
            branch.mutual_impedance,
            f"branch '{name}' (mutual impedance)",
            [("branch", name)],
            formula=branch.type.mutual_impedance_formula,
            passive=False,
            evaluate=evaluate,
            type_ref=type_ref,
        )

    for name, source in report.sources.items():
        if source.source_type != "voltage":
            continue
        bus = report.buses.get(source.bus)
        if bus is None or not bus.active:
            continue
        _impedance_checks(
            report,
            source.source_impedance,
            f"source '{name}' (source impedance)",
            [("source", name)],
            formula=None,
            passive=True,
            evaluate=None,
        )


def _check_sources(report: _Report) -> None:
    """
    Frequency coverage and finiteness of every source's injection.

    A current source injects ``values[f]``, a Thevenin source ``voltage[f]``
    over ``source_impedance[f]``; a frequency without a value contributes
    nothing at that frequency, and a source covering none of them contributes
    nothing at all. The codes ``SOURCE_FREQUENCY_GAP`` and
    ``SOURCE_FREQUENCY_OUTSIDE_NETWORK`` are the ones the solve raises for the
    same conditions.
    """
    frequencies = report.frequencies
    for name, source in report.sources.items():
        mapping = source.values if source.source_type == "current" else source.voltage
        mapping = mapping or {}
        keys = []
        for key in mapping:
            try:
                keys.append(float(key))
            except (TypeError, ValueError):
                continue
        covered = [f for f in frequencies if f in keys]
        missing = [f for f in frequencies if f not in keys]
        outside = sorted(set(keys) - set(frequencies))
        what = "values" if source.source_type == "current" else "voltage"
        if frequencies and not covered:
            report.add(
                "SOURCE_FREQUENCY_NONE",
                f"Source '{name}' has {what} at {_render(sorted(keys)) if keys else 'no frequency'} "
                f"but the network is solved at {_render(frequencies)}: the "
                "source would inject nothing at all, and run_fault refuses it "
                "when it reaches the fault.",
                [("source", name)],
            )
        elif missing:
            report.add(
                "SOURCE_FREQUENCY_GAP",
                f"Source '{name}' has no {what} at {_render(missing)}; it "
                "injects nothing there.",
                [("source", name)],
            )
        if outside:
            report.add(
                "SOURCE_FREQUENCY_OUTSIDE_NETWORK",
                f"Source '{name}' has {what} at {_render(outside)}, which the "
                "network does not solve; they are ignored.",
                [("source", name)],
            )
        bad = [
            f
            for f in covered
            if not (
                math.isfinite(complex(mapping[f]).real)
                and math.isfinite(complex(mapping[f]).imag)
            )
        ]
        if bad:
            report.add(
                "SOURCE_VALUE_NOT_FINITE",
                f"Source '{name}' has a NaN or infinite {what} at "
                f"{_render(bad)}.",
                [("source", name)],
            )


def _check_faults(report: _Report) -> None:
    """Location, scalings and reachability of each checked fault."""
    frequencies = report.frequencies
    for name in report.checked_faults:
        fault = report.faults[name]
        bus = report.buses.get(fault.bus)
        if bus is None:
            report.add(
                "FAULT_UNKNOWN_BUS",
                f"Fault '{name}' sits on bus '{fault.bus}', which is not in "
                f"network '{report.name}'.",
                [("fault", name), ("bus", fault.bus)],
            )
            continue
        if not bus.active:
            report.add(
                "FAULT_BUS_INACTIVE",
                f"Fault '{name}' sits on the inactive bus '{fault.bus}'; the "
                "solver refuses it.",
                [("fault", name), ("bus", fault.bus)],
            )
            continue
        if fault.scalings:
            missing = [f for f in frequencies if f not in fault.scalings]
            if missing:
                report.add(
                    "SCALINGS_INCOMPLETE",
                    f"Fault '{name}' has no scaling at {_render(missing)}; the "
                    "sources are scaled with 1.0 there. Give every frequency a "
                    "scaling, or none at all (factor 1.0 everywhere).",
                    [("fault", name)],
                )
        if (
            report.sources
            and report.consistent
            and not report.network.sources_reaching(name)
        ):
            report.add(
                "FAULT_NOT_REACHED",
                f"No source reaches fault '{name}' over active buses and "
                "branches; every potential would be 0 V. That is the right "
                "answer for an outage that islands the fault, and a "
                "modelling error otherwise.",
                [("fault", name)],
            )


class _UnionFind:
    """Disjoint sets over bus names, for the island search."""

    def __init__(self, names: Sequence[str]) -> None:
        self.parent = {name: name for name in names}

    def find(self, name: str) -> str:
        """Representative of ``name``'s set (with path halving)."""
        while self.parent[name] != name:
            self.parent[name] = self.parent[self.parent[name]]
            name = self.parent[name]
        return name

    def union(self, a: str, b: str) -> None:
        """Merge the sets of ``a`` and ``b``."""
        root_a, root_b = self.find(a), self.find(b)
        if root_a != root_b:
            self.parent[root_b] = root_a


def _referenced(impedance) -> bool:
    """The solver's test for a bus that ties its island to earth: finite, non-zero, not NaN."""
    if impedance is None:
        return False
    value = complex(float(impedance.real), float(impedance.imag))
    if math.isnan(value.real) or math.isnan(value.imag):
        return False
    return value != 0 and math.isfinite(value.real) and math.isfinite(value.imag)


def _check_topology(report: _Report) -> None:
    """
    Isolated buses and earthing islands without a reference to earth.

    An *earthing island* is a set of active buses joined, at one frequency, by
    active grounding conductors with a finite self impedance -- exactly the
    couplings of the nodal matrix. An island none of whose buses has a finite,
    non-zero grounding impedance at that frequency has no reference to earth:
    its block of the matrix is singular and its potential undefined (the
    solver raises, or returns arbitrary values). An island that holds the bus
    of a Thevenin source is skipped, because the source's loop impedance ties
    it to the fault bus.
    """
    if not report.consistent:
        return
    active = [name for name, bus in report.buses.items() if bus.active]
    active_set = set(active)

    degree: Dict[str, int] = {name: 0 for name in active}
    for branch in report.branches.values():
        if (
            branch.active
            and branch.from_bus in active_set
            and branch.to_bus in active_set
            and branch.from_bus != branch.to_bus
        ):
            degree[branch.from_bus] += 1
            degree[branch.to_bus] += 1
    if len(active) > 1:
        for name in active:
            if degree[name] == 0:
                report.add(
                    "ISOLATED_BUS",
                    f"Bus '{name}' has no active branch to any other bus; it "
                    "takes no part in the calculation.",
                    [("bus", name)],
                )

    thevenin_buses = {
        source.bus
        for source in report.sources.values()
        if source.source_type == "voltage"
    }
    floating: Dict[Tuple[str, ...], List[float]] = {}
    for freq in report.frequencies:
        islands = _UnionFind(active)
        for branch in report.branches.values():
            if not (
                branch.active
                and branch.type.grounding_conductor
                and branch.from_bus in active_set
                and branch.to_bus in active_set
            ):
                continue
            z = (branch.self_impedance or {}).get(freq)
            if z is None:
                continue
            value = complex(float(z.real), float(z.imag))
            if math.isfinite(value.real) and math.isfinite(value.imag):
                islands.union(branch.from_bus, branch.to_bus)
        members: Dict[str, List[str]] = {}
        for name in active:
            members.setdefault(islands.find(name), []).append(name)
        for buses in members.values():
            if thevenin_buses & set(buses):
                continue
            if not any(
                _referenced((report.buses[b].impedance or {}).get(freq)) for b in buses
            ):
                floating.setdefault(tuple(buses), []).append(freq)
    for buses, freqs in floating.items():
        shown = ", ".join(f"'{b}'" for b in buses[:5]) + (
            f" and {len(buses) - 5} more" if len(buses) > 5 else ""
        )
        report.add(
            "FLOATING_ISLAND",
            f"Bus(es) {shown} form a group joined by grounding conductors "
            f"with no reference to earth at {_render(freqs)}: no bus of the "
            "group has a finite, non-zero grounding impedance, so its "
            "potential is undefined and the solve fails. Give one of them an "
            "electrode, or connect the group to the rest of the network.",
            [("bus", b) for b in buses],
        )


def check_network(network: Network, *, fault: Optional[str] = None) -> List[Diagnostic]:
    """
    Every problem of a network, as data, without raising.

    Runs the structural checks listed in :data:`CHECK_CODES` -- elements and
    references, frequencies, excitation, the impedances the solver will invert,
    source coverage, fault location and reachability, isolated buses and
    earthing islands without a reference to earth -- and returns all findings
    at once. Package warnings raised along the way (for example the
    ``FREQUENCY_ORDER`` warning of the frequency validator) are included with
    their own codes.

    Parameters
    ----------
    network : Network
        The network to check. It is not modified; formulas that have to be
        evaluated are evaluated on copies of the elements.
    fault : str, optional
        Restrict the fault-specific checks (location, scalings, reachability)
        to this fault. Defaults to every fault of the network. A name that is
        not a fault of the network is reported as ``FAULT_UNKNOWN``.

    Returns
    -------
    list of Diagnostic
        One record per problem, network-level ones first, then per element in
        the network's order; empty for a network that is ready to solve.
        ``severity`` is ``"error"`` where ``run_fault`` would raise or return
        an answer that does not describe the network, and ``"warning"`` where
        the network solves but part of the input has no effect or a default
        stands in.

    Notes
    -----
    The function never raises. An argument that is not a network yields a
    single ``NOT_A_NETWORK`` record; a check that crashes on an unexpected
    malformation yields ``CHECK_FAILED`` and the remaining checks still run.

    The checks are structural and cheap (no assembly, no solve), so a GUI can
    run them after every edit. Conditions that only the solve can detect --
    model assumptions such as a heuristic phase-current split -- are reported
    with the result in ``Result.diagnostics``.

    Examples
    --------
    >>> import groundinsight as gi
    >>> bus_type = gi.BusType(name="BT", system_type="MV", voltage_level=20.0,
    ...                       impedance_formula="0.01*rho")
    >>> net = gi.create_network(name="demo", frequencies=[50.0])
    >>> _ = gi.create_bus(name="b0", type=bus_type, network=net)
    >>> _ = gi.create_bus(name="b1", type=bus_type, network=net)
    >>> [(d.code, d.severity) for d in gi.check_network(net)]
    [('NO_SOURCE', 'error'), ('NO_FAULT', 'error'), ('ISOLATED_BUS', 'warning'), ('ISOLATED_BUS', 'warning')]
    """
    if not isinstance(network, Network):
        return [
            Diagnostic(
                code="NOT_A_NETWORK",
                severity=CHECK_CODES["NOT_A_NETWORK"][0],
                category="InputWarning",
                message=(
                    "check_network() needs a groundinsight Network, got "
                    f"{type(network).__name__}."
                ),
                elements=[],
            )
        ]

    report = _Report(network)
    report.run("elements", _check_elements)
    report.run("frequency", _check_frequencies)
    report.run("excitation", _check_excitation)
    report.run("reference", _check_references)
    if fault is None:
        report.checked_faults = list(report.faults)
    elif fault in report.faults:
        report.checked_faults = [fault]
    else:
        report.add(
            "FAULT_UNKNOWN",
            f"Fault '{fault}' does not exist in network '{report.name}'. "
            f"Available: {sorted(report.faults)}.",
            [("fault", fault)],
        )
    report.run("impedance", _check_impedances)
    report.run("source", _check_sources)
    report.run("fault", _check_faults)
    report.run("topology", _check_topology)
    return unique(report.records)

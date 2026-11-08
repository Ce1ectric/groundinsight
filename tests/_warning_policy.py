# tests/_warning_policy.py
"""
The package warnings a test may acknowledge instead of asserting.

The suite turns every package warning into an error (``filterwarnings`` in
``pyproject.toml``). A test that provokes one either asserts it
(``pytest.warns``) or acknowledges it here, by name, because its fixture
network raises it by construction and the test is about something else --
a ring always splits its phase current by the proxy, a chain of trial
faults always spreads its electrode currents. An unexpected warning, or an
expected one that changes its wording, then fails the test that meets it
instead of scrolling past in the summary.

Filters can only match the message text, not the ``code`` attribute, so
each entry pairs a code with the start of its message. A ``:`` would end
the message field of a filter, so no pattern contains one.

Examples
--------
A module whose ring fixtures split the phase current by the proxy:

>>> from tests._warning_policy import acknowledge
>>> pytestmark = acknowledge("PHASE_PROXY")
>>> pytestmark.args
('ignore:The phase network around fault .* carries at least one cycle:groundinsight.ModelAssumptionWarning',)
"""

from __future__ import annotations

from typing import Dict, Tuple

import pytest

#: Name -> (warning category, start of the message as a regular expression).
#: The name is the diagnostic code where the code belongs to one message;
#: TransientModelWarning and DCLimitWarning use one code for several
#: messages, so their names say which message is meant.
ACKNOWLEDGEABLE: Dict[str, Tuple[str, str]] = {
    # ModelAssumptionWarning
    "EARTH_GROUP_SPREAD": (
        "ModelAssumptionWarning", r".*the electrode currents are spread in angle"),
    "EARTH_GROUP_UNDEFINED": (
        "ModelAssumptionWarning", r".*no earth-return group exists"),
    "EARTHING_IMPEDANCE_UNDEFINED": (
        "ModelAssumptionWarning", r".*so the earthing impedance Z_E .* is reported as None"),
    "PARALLEL_COEFFICIENT_DEFAULT": (
        "ModelAssumptionWarning", r"Source\(s\) .* over more than one path while parallel_coefficient"),
    "PHASE_PROXY": (
        "ModelAssumptionWarning", r"The phase network around fault .* carries at least one cycle"),
    "REDUCTION_FACTOR_UNDEFINED": (
        "ModelAssumptionWarning", r".*the EPR-based reduction factor"),
    "SOURCE_WITHOUT_PHASE_PATH": (
        "ModelAssumptionWarning", r"Source .* reaches fault .* but has no phase-conductor connection"),
    "SC_KAPPA_MIXED": (
        "ModelAssumptionWarning", r"Fault .* is fed by sources with different R/X"),
    "THERMAL_DC_EXCLUDED": (
        "ModelAssumptionWarning", r".*is excluded from the adiabatic"),
    "THEVENIN_COUPLING": (
        "ModelAssumptionWarning", r".*the Thevenin source\(s\) .* feed the fault while branches with mutual coupling"),
    # InputWarning
    "IMPORT_BUS_SKIPPED": ("InputWarning", r"Bus row #\d+ .* has unparsable vn_kv"),
    "IMPORT_ELEMENT_NOT_IMPORTED": ("InputWarning", r"\d+ trafo element\(s\) were not imported"),
    "IMPORT_EMPTY": ("InputWarning", r"Imported pandapower net into"),
    "IMPORT_LENGTH_DEFAULT": ("InputWarning", r"Line row #\d+ .* has no usable length_km"),
    "IMPORT_LINE_SWITCH_OPEN": (
        "InputWarning", r"Line .* is open at .* imported with an open phase conductor"),
    "IMPORT_LINE_SKIPPED": (
        "InputWarning", r"Line row #\d+ .*(cannot be imported as a branch|is a self-loop)"),
    "IMPORT_SWITCH_NOT_IMPORTED": (
        "InputWarning", r"\d+ closed bus-to-bus switch\(es\) were not imported"),
    "IMPORT_VOLTAGE_LEVEL_MISMATCH": (
        "InputWarning", r"ImportDefaults\.default_bus_type .* declares voltage_level"),
    "SCALINGS_INCOMPLETE": ("InputWarning", r"Fault .* gives no scaling at"),
    "SC_DATA_INCOMPLETE": ("InputWarning", r"Fault .* source\(s\) .* inject .* but carry no IEC"),
    "SC_KAPPA_MISSING": ("InputWarning", r"No kappa could be derived"),
    "SC_KAPPA_OUT_OF_RANGE": ("InputWarning", r"pandapower ip/ikss at bus .* implies kappa"),
    "SC_ROW_AMBIGUOUS": ("InputWarning", r"\d+ short-circuit rows match"),
    "SC_SPLIT_EQUAL": ("InputWarning", r"Sources .* inject nothing at f"),
    "SC_VALUE_REPLACED": ("InputWarning", r"Fault .* replaced by the short-circuit result"),
    "SC_VOLTAGE_SOURCE_SKIPPED": (
        "InputWarning", r"Source .* is a voltage source; its injection was not overwritten"),
    "SOURCE_FREQUENCY_GAP": ("InputWarning", r"Source .* has no value at .* of the network frequencies"),
    "SOURCE_FREQUENCY_OUTSIDE_NETWORK": (
        "InputWarning", r"Source .* carries values at .* which are not among the network frequencies"),
    "THERMAL_DATA_INCOMPLETE": ("InputWarning", r".* carry only half of their thermal data"),
    "TRANSIENT_SOURCE_NOT_CONNECTED": ("InputWarning", r"Transient FFT solve.* have no galvanic connection"),
    # DCLimitWarning (code DC_LIMIT for every message)
    "DC_LIMIT": ("DCLimitWarning", r""),
    # TransientModelWarning (code TRANSIENT_MODEL for every message)
    "TRANSIENT_COUPLING_IGNORED": (
        "TransientModelWarning", r"The FFT transient solver does not evaluate mutual coupling"),
    "TRANSIENT_RINGING": (
        "TransientModelWarning", r"The FFT window ends while the response is still ringing"),
    "TRANSIENT_SCALINGS_IGNORED": ("TransientModelWarning", r"Fault .* carries scalings other than 1"),
    # NetworkFrequencyOrderWarning
    "FREQUENCY_ORDER": ("NetworkFrequencyOrderWarning", r""),
}


def acknowledge(*names: str) -> pytest.MarkDecorator:
    """
    A ``filterwarnings`` mark that lets the named package warnings pass.

    Usable as ``pytestmark = acknowledge(...)`` for a module or as a
    decorator on one test; a comment next to it says why the warnings are
    expected there.

    Parameters
    ----------
    *names : str
        Keys of :data:`ACKNOWLEDGEABLE`.

    Returns
    -------
    pytest.MarkDecorator
        One ``filterwarnings`` mark with one ``ignore`` filter per name.

    Raises
    ------
    KeyError
        If a name is not in :data:`ACKNOWLEDGEABLE`.
    """
    filters = []
    for name in names:
        category, message = ACKNOWLEDGEABLE[name]
        filters.append(f"ignore:{message}:groundinsight.{category}")
    return pytest.mark.filterwarnings(*filters)

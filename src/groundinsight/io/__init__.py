# io/__init__.py

"""
External-network importers.

This sub-package converts existing power-system models from third-party
tools (pandapower today, PowerFactory `.dgs` next) into a
:class:`groundinsight.models.core_models.Network`. Importers do not
build grounding networks from scratch -- they project an existing
distribution-network topology onto the bus/branch primitives that
``groundinsight`` solves for, and let the user supply the impedance
formulas via :class:`ImportDefaults`.

Beyond the topology, the sub-package also imports *results*: a solved
pandapower short-circuit case can be read as IEC 60909 characteristic
quantities and applied to the sources and faults of an existing
groundinsight model, which is what feeds the thermal conductor check.

Public API:

- :class:`ImportDefaults`
- :func:`groundinsight.io.pandapower_import.from_pandapower`
- :func:`groundinsight.io.pandapower_import.preview_pandapower_import`
- :func:`groundinsight.io.pandapower_sc.read_shortcircuit_results`
- :func:`groundinsight.io.pandapower_sc.apply_shortcircuit_characteristics`

The importers themselves live in tool-specific submodules so that the
optional third-party dependency is loaded lazily.
"""

from .defaults import ImportDefaults
from .pandapower_import import from_pandapower, preview_pandapower_import
from .pandapower_sc import (
    apply_shortcircuit_characteristics,
    read_shortcircuit_results,
)

__all__ = [
    "ImportDefaults",
    "from_pandapower",
    "preview_pandapower_import",
    "read_shortcircuit_results",
    "apply_shortcircuit_characteristics",
]

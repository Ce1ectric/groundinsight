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

Public API:

- :class:`ImportDefaults`
- :func:`groundinsight.io.pandapower_import.from_pandapower`
- :func:`groundinsight.io.pandapower_import.preview_pandapower_import`

The importers themselves live in tool-specific submodules so that the
optional third-party dependency is loaded lazily.
"""

from .defaults import ImportDefaults
from .pandapower_import import from_pandapower, preview_pandapower_import

__all__ = [
    "ImportDefaults",
    "from_pandapower",
    "preview_pandapower_import",
]

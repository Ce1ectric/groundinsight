# groundinsight/library/__init__.py
"""
The standard library of types: common cables and typical earth electrodes.

A network is quicker to draw from types that already exist. The library
holds two kinds:

* branch types of common cables, built from data-sheet values with the
  formulas of the documentation page "Types from data sheets"
  (:mod:`~groundinsight.library.cables`): 20 kV single-core cable systems in
  trefoil (aluminium and copper) and four-core LV cables with PEN;
* bus types of typical earth electrodes, precomputed with groundfield
  (:mod:`~groundinsight.library.electrodes`).

Every type is an ordinary :class:`~groundinsight.BranchType` or
:class:`~groundinsight.BusType`: copy it into a network as it is, or change a
copy (``model_copy(update=...)``) for another laying or soil. The builders
:func:`cable_system_type` and :func:`lv_cable_type` make a type of a cable
the library does not list.
"""

from .cables import (
    LV_CABLES,
    MV_CABLES,
    LvCableData,
    MvCableData,
    cable_system_type,
    lv_cable_type,
    standard_branch_types,
)
from .electrodes import standard_bus_types

__all__ = [
    "LV_CABLES",
    "LvCableData",
    "MV_CABLES",
    "MvCableData",
    "cable_system_type",
    "lv_cable_type",
    "standard_branch_types",
    "standard_bus_types",
]

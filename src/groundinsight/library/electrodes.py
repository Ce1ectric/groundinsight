# groundinsight/library/electrodes.py
"""
Bus types of typical earth electrodes, precomputed with groundfield.

The impedance of an earth electrode depends on its geometry and the soil, so
no data sheet gives it; a field calculation does. The presets here were
computed once with groundfield (``scripts/build_standard_bus_types.py``,
backend ``mom``) and are stored as data in ``data/standard_bus_types.json``,
so the library reads them without importing groundfield:

* each electrode alone in **homogeneous** soil of 30 ... 3000 Ohm*m, at
  50 ... 1000 Hz -- the table of the characteristic (impedance and touch
  factor ``k_touch``);
* groundfield's fitted k0...k5 form of the impedance as the type's
  ``impedance_formula``; buses take the table (``impedance_source="table"``),
  interpolated log-log between the points.

The provenance of every characteristic names the groundfield version, the
backend and a hash of the input. Layered soil is site data: compute the
characteristic of the site (``characterize`` or the groundfield job of the
GUI's bus dialog) instead of scaling a preset.
"""

import json
import logging
from importlib import resources
from typing import Dict

from ..models.core_models import BusType

logger = logging.getLogger(__name__)

__all__ = ["standard_bus_types"]

_DATA = "standard_bus_types.json"


def standard_bus_types() -> Dict[str, BusType]:
    """
    The electrode presets of the standard library as bus types, by name.

    Returns
    -------
    dict of str to BusType
        Ten electrodes, each with its groundfield characteristic: ring and
        mesh electrodes of substations and transformer stations (with rods),
        the foundation and the ring electrode of a house, and single driven
        rods; a fresh dictionary on every call.

    Examples
    --------
    >>> import groundinsight as gi
    >>> sorted(gi.standard_bus_types())[:2]
    ['Cable distribution cabinet, rod 3 m', 'House foundation 10 x 10 m']
    """
    text = resources.files(__package__).joinpath("data", _DATA).read_text(encoding="utf-8")
    types = [BusType.model_validate(entry) for entry in json.loads(text)["bus_types"]]
    return {t.name: t for t in types}

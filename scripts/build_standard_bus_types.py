# scripts/build_standard_bus_types.py
"""
Compute the standard bus types (typical earth electrodes) with groundfield.

The standard library of groundinsight (:func:`groundinsight.standard_bus_types`)
reads its electrode presets from ``src/groundinsight/library/data/
standard_bus_types.json``, so the library itself never imports groundfield.
This script writes that file; it needs groundfield and runs outside the test
suite::

    python scripts/build_standard_bus_types.py            # add the presets that are missing
    python scripts/build_standard_bus_types.py --all      # recompute every preset
    python scripts/build_standard_bus_types.py --check    # recompute, compare

Every preset is one electrode alone in **homogeneous** soil, the geometry of
:data:`groundinsight.integrations.groundfield.ELECTRODES` (overridden where
stated), solved with the ``mom`` backend: the touch factor needs field
quality, and ``image`` deviates by 6-11 % near meshes. In homogeneous soil the
impedance scales with rho at every frequency up to the inductive part, so the
table's rho grid (30 ... 3000 Ohm*m) covers the usual soils and the fitted
formula holds between its points. A layered soil is site data: compute the
characteristic of the site with the groundfield job of the bus dialog.

Without ``--all`` a preset that the file already holds is kept as it is: its
table depends on the groundfield version that computed it, and the results of
networks built on a preset must not move because another preset was added.
The segment length is a property of the preset: 0.5 m for the small
electrodes, coarser for the large meshes, whose conductors are far longer
than a segment either way.
"""

import argparse
import json
import logging
import pathlib
import sys
from typing import Any, Dict, List

import groundinsight as gi
from groundinsight.integrations import groundfield as gfi

logger = logging.getLogger(__name__)

TARGET = (pathlib.Path(__file__).resolve().parents[1]
          / "src" / "groundinsight" / "library" / "data" / "standard_bus_types.json")

RHO_GRID = [30.0, 100.0, 300.0, 1000.0, 3000.0]
FREQUENCIES = [50.0, 150.0, 250.0, 550.0, 1000.0]
SEGMENT_LENGTH = 0.5

#: name, electrode kind, dimension overrides, system type, voltage level (kV), description,
#: segment length (m)
PRESETS = [
    ("Substation ring 2.5 m, 4 rods 2.5 m", "ring", {}, "MV/LV", 20.0,
     "MV/LV substation: ring electrode r = 2.5 m at 0.6 m depth, four rods of 2.5 m on a 1.5 m circle",
     SEGMENT_LENGTH),
    ("House foundation 10 x 10 m", "foundation", {}, "LV", 0.4,
     "House: square foundation electrode 10 m x 10 m at 0.8 m depth, 2 x 2 meshes", SEGMENT_LENGTH),
    ("Rod 2.5 m", "rod", {}, "LV", 0.4,
     "Single driven rod, 2.5 m, top at the surface", SEGMENT_LENGTH),
    ("Rod 5 m", "rod", {"length_m": 5.0}, "LV", 0.4,
     "Single driven rod, 5 m, top at the surface", SEGMENT_LENGTH),
    ("House ring electrode 15 x 5 m", "mesh", {"length_m": 15.0, "width_m": 5.0, "mesh_m": 15.0}, "LV", 0.4,
     "House: rectangular ring electrode 15 m x 5 m at 0.8 m depth", SEGMENT_LENGTH),
    ("Cable distribution cabinet, rod 3 m", "rod", {"length_m": 3.0}, "LV", 0.4,
     "Cable distribution cabinet: single driven rod, 3 m, top at the surface", SEGMENT_LENGTH),
    ("Transformer station ring 5 x 5 m, rod 6 m", "mesh",
     {"length_m": 5.0, "width_m": 5.0, "mesh_m": 5.0, "rods": 1, "rod_length_m": 6.0}, "MV/LV", 20.0,
     "Transformer station: square ring electrode 5 m x 5 m at 0.8 m depth, one rod of 6 m at a corner",
     SEGMENT_LENGTH),
    ("Transformer station ring 5 x 5 m, 2 rods 12 m at 5 m", "mesh",
     {"length_m": 5.0, "width_m": 5.0, "mesh_m": 5.0, "rods": 2, "rod_length_m": 12.0, "rod_distance_m": 5.0},
     "MV/LV", 20.0,
     "Transformer station: square ring electrode 5 m x 5 m at 0.8 m depth, two rods of 12 m at 5 m from "
     "the ring on opposite sides, each joined to it by a bare earth conductor at 0.8 m depth", SEGMENT_LENGTH),
    ("Substation mesh 50 x 100 m, meshes 10 x 10 m", "mesh", {"length_m": 50.0, "width_m": 100.0}, "HV/MV", 110.0,
     "Substation: mesh electrode 50 m x 100 m at 0.8 m depth, meshes of 10 m x 10 m", 1.0),
    ("Substation mesh 200 x 200 m, meshes 10 x 10 m", "mesh", {"length_m": 200.0, "width_m": 200.0}, "HV/MV",
     110.0, "Substation: mesh electrode 200 m x 200 m at 0.8 m depth, meshes of 10 m x 10 m", 2.0),
]


def compute(keep: Dict[str, Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    The presets as JSON objects of :class:`groundinsight.BusType`.

    Parameters
    ----------
    keep : dict of str to dict
        Stored presets by name that are taken over instead of computed.

    Returns
    -------
    list of dict
        One object per entry of :data:`PRESETS`, in that order.
    """
    gf = gfi.require_groundfield()
    types = []
    for name, kind, dimensions, system_type, voltage_level, description, segment_length in PRESETS:
        if name in keep:
            types.append(keep[name])
            logger.info("%s: kept", name)
            continue
        engine = gf.create_engine(backend="mom", frequencies=FREQUENCIES, segment_length=segment_length)
        characteristic = gfi.characterize(gfi.electrode_world(kind, **dimensions), RHO_GRID, electrode="E",
                                          engine=engine, name=name, geometry_id=f"{kind} {dimensions or 'default'}")
        bus_type = gi.BusType(name=name, description=description, system_type=system_type,
                              voltage_level=voltage_level, impedance_formula=characteristic.impedance_formula,
                              characteristic=characteristic)
        types.append(json.loads(bus_type.model_dump_json()))
        values = characteristic.evaluate(100.0, [50.0])
        logger.info("%s: |Z|(100 Ohm*m, 50 Hz) = %.4g Ohm, k_touch %.4g", name, abs(values.impedance[0]),
                    values.k_touch[0])
    return types


def main() -> int:
    """Write the presets, or with ``--check`` compare a recomputation with them; the exit status."""
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    parser.add_argument("--check", action="store_true", help="recompute and compare with the stored file")
    parser.add_argument("--all", action="store_true", help="recompute the presets the file already holds as well")
    args = parser.parse_args()
    stored_types = json.loads(TARGET.read_text(encoding="utf-8"))["bus_types"] if TARGET.exists() else []
    keep = {} if args.all or args.check else {entry["name"]: entry for entry in stored_types}
    types = compute(keep)
    if args.check:
        stored = json.loads(TARGET.read_text(encoding="utf-8"))
        same = [a["characteristic"]["impedance_Ohm"] == b["characteristic"]["impedance_Ohm"]
                for a, b in zip(types, stored["bus_types"], strict=True)]
        logger.info("identical tables: %s", same)
        return 0 if all(same) else 1
    TARGET.parent.mkdir(parents=True, exist_ok=True)
    document = {"comment": "written by scripts/build_standard_bus_types.py -- do not edit by hand",
                "bus_types": types}
    TARGET.write_text(json.dumps(document, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    logger.info("written %s", TARGET)
    return 0


if __name__ == "__main__":
    sys.exit(main())

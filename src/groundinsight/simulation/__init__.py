# simulation/__init__.py

"""
Simulation Sub-Package.

Container for higher-level simulation workflows that operate on top of the
core ``groundinsight`` solver. The first inhabitant is
:mod:`groundinsight.simulation.outage`, which provides what-if studies based
on the ``active`` flag on :class:`groundinsight.models.core_models.Bus` and
:class:`groundinsight.models.core_models.Branch`.

Public re-exports are pulled into ``groundinsight`` itself so users can write
``gi.run_outage_study(...)`` instead of importing from this sub-package
directly.
"""

from .outage import (
    Outage,
    OutageStudyResult,
    outage_context,
    run_outage_study,
)
from .sweep import (
    SweepPoint,
    SweepResult,
    rho_f_points,
    run_sweep,
)
from .transient import (
    ResultTransient,
    TransientStudy,
)
from . import waveforms

__all__ = [
    "Outage",
    "OutageStudyResult",
    "outage_context",
    "run_outage_study",
    "SweepPoint",
    "SweepResult",
    "rho_f_points",
    "run_sweep",
    "ResultTransient",
    "TransientStudy",
    "waveforms",
]

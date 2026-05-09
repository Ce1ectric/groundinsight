# io/defaults.py

"""
Common defaults for external-network importers.

Importing a pandapower / PowerFactory model into a groundinsight
:class:`~groundinsight.models.core_models.Network` requires data that
the source tool does not provide: the soil resistivity, the frequencies
to evaluate the network at, and the impedance formulas to use for buses
and branches. :class:`ImportDefaults` bundles these so that a caller can
pass them through every importer with a single keyword argument and so
that future importers (PowerFactory, NEPLAN, PSS/E) reuse the same shape.
"""

from __future__ import annotations

from typing import List

from pydantic import BaseModel, Field

from groundinsight.models.core_models import BranchType, BusType


class ImportDefaults(BaseModel):
    """
    Per-import defaults shared by every external-network importer.

    Attributes:
        rho (float): Specific earth resistance applied to every imported
            ``Bus`` and ``Branch`` (Ohm * m). Source tools do not encode
            soil parameters, so a single project-wide value is taken
            from the user.
        frequencies (List[float]): Frequencies at which impedance values
            are evaluated. Becomes ``Network.frequencies``; bus and
            branch impedance dicts are sized accordingly.
        default_bus_type (BusType): Default ``BusType`` assigned to every
            imported bus on the selected voltage level.
        default_branch_type (BranchType): Default ``BranchType`` assigned
            to every imported line / cable on the selected voltage level.

    Examples:
        >>> from groundinsight.models.core_models import BusType, BranchType
        >>> from groundinsight.io import ImportDefaults
        >>> defaults = ImportDefaults(
        ...     rho=100.0,
        ...     frequencies=[50.0],
        ...     default_bus_type=BusType(
        ...         name="ImportedBus",
        ...         system_type="Grounded",
        ...         voltage_level=20.0,
        ...         impedance_formula="rho * 0 + 1.0 + I * f * 0",
        ...     ),
        ...     default_branch_type=BranchType(
        ...         name="ImportedCable",
        ...         grounding_conductor=True,
        ...         self_impedance_formula="(0.25 + I * 0.6) * l",
        ...         mutual_impedance_formula="(0.0 + I * 0.6) * l",
        ...     ),
        ... )
    """

    rho: float
    frequencies: List[float] = Field(default_factory=list)
    default_bus_type: BusType
    default_branch_type: BranchType

    def __str__(self) -> str:
        return (
            f"ImportDefaults(rho={self.rho}, "
            f"frequencies={self.frequencies}, "
            f"bus_type={self.default_bus_type.name}, "
            f"branch_type={self.default_branch_type.name})"
        )

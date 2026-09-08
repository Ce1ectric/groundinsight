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

import math
from typing import List, Optional

from pydantic import BaseModel, Field, field_validator

from groundinsight.models.core_models import BranchType, BusType


class ImportDefaults(BaseModel):
    """
    Per-import defaults shared by every external-network importer.

    Attributes
    ----------
    rho : float
        Specific earth resistance applied to every imported :class:`Bus`
        and :class:`Branch` (Ohm * m). Source tools do not encode soil
        parameters, so a single project-wide value is taken from the user.
    frequencies : list of float
        Frequencies at which impedance values are evaluated. Becomes
        ``Network.frequencies``; bus and branch impedance dicts are sized
        accordingly.
    default_bus_type : BusType
        Default :class:`BusType` assigned to every imported bus on the
        selected voltage level.
    default_branch_type : BranchType
        Default :class:`BranchType` assigned to every imported line /
        cable on the selected voltage level.
    switch_branch_type : BranchType, optional
        :class:`BranchType` used for a **closed bus-bus switch**. A closed
        switch bonds two grounding systems; dropping it makes the bus behind
        it look galvanically isolated and report 0 V, which is the one answer
        a grounding study must never produce by accident. ``None`` (the
        default) keeps the historical behaviour -- the switch is not imported
        -- but the importer now says so, and lists it in
        :func:`~groundinsight.io.preview_pandapower_import` as a skipped
        ``"switch"`` row.
    switch_length_km : float
        Length assigned to a branch created from a closed switch. A switch
        has no length; a small non-zero value is used because a zero-length
        branch has no series impedance at all and is rejected by the
        importer. Defaults to ``0.001`` km (1 m), which is negligible against
        any real cable section.
    derive_phase_impedance : bool
        Derive a per-line ``phase_impedance_formula`` from the pandapower
        columns ``r_ohm_per_km`` / ``x_ohm_per_km``. The phase-conductor
        network is what ``phase_current_mode="auto"`` solves; without a
        formula every imported mesh falls back to the path-based proxy. The
        earth-return (shield) formulas cannot be derived this way -- they
        depend on the soil and the cable geometry, neither of which
        pandapower carries -- so they stay at ``default_branch_type``.
        Defaults to ``False``, which reproduces the previous behaviour.

    Examples
    --------
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
    switch_branch_type: Optional[BranchType] = None
    switch_length_km: float = 0.001
    derive_phase_impedance: bool = False

    @field_validator("rho")
    @classmethod
    def _validate_rho(cls, value: float) -> float:
        """Reject a soil resistivity that cannot be evaluated.

        ``rho`` is substituted into every impedance formula, so ``nan``
        propagates into every element of the imported network and a
        non-positive value produces a ``sqrt`` domain error deep inside
        :func:`~groundinsight.utils.impedance_calculator.compute_impedance`,
        far from the import that caused it.
        """
        value = float(value)
        if not math.isfinite(value) or value <= 0:
            raise ValueError(
                f"ImportDefaults.rho must be a finite positive resistivity in "
                f"Ohm*m, got {value}."
            )
        return value

    @field_validator("frequencies")
    @classmethod
    def _validate_frequencies(cls, value: List[float]) -> List[float]:
        """Apply the same rules as ``Network.frequencies``, at import time.

        The default used to be an empty list, and the only thing that noticed
        was ``from_pandapower`` -- after the caller had already assembled the
        types. ``Network`` rejects an empty, duplicated, negative or
        non-finite frequency list; an ``ImportDefaults`` that cannot produce a
        valid ``Network`` is not a valid ``ImportDefaults``.
        """
        if not value:
            raise ValueError(
                "ImportDefaults.frequencies must not be empty -- the imported "
                "Network needs at least one frequency to solve at."
            )
        for f in value:
            f = float(f)
            if not math.isfinite(f):
                raise ValueError(
                    f"ImportDefaults.frequencies contains a non-finite value: {f!r}."
                )
            if f < 0:
                raise ValueError(
                    f"ImportDefaults.frequencies must be >= 0; got {f!r} "
                    "(f == 0 for the DC bin is permitted)."
                )
        if len(set(value)) != len(value):
            raise ValueError(
                f"ImportDefaults.frequencies contains duplicates: {value!r}."
            )
        return [float(f) for f in value]

    @field_validator("switch_length_km")
    @classmethod
    def _validate_switch_length(cls, value: float) -> float:
        """A zero-length branch has no series impedance and is rejected later."""
        value = float(value)
        if not math.isfinite(value) or value <= 0:
            raise ValueError(
                "ImportDefaults.switch_length_km must be a finite positive "
                f"length in km, got {value}. A zero-length branch carries no "
                "series impedance at all, which is not what a closed switch "
                "means."
            )
        return value

    def __str__(self) -> str:
        return (
            f"ImportDefaults(rho={self.rho}, "
            f"frequencies={self.frequencies}, "
            f"bus_type={self.default_bus_type.name}, "
            f"branch_type={self.default_branch_type.name})"
        )

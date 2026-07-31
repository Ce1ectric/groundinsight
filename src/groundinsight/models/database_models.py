# models/database_models.py

"""
Database Models Module.

This module defines the SQLAlchemy ORM (Object-Relational Mapping) models corresponding to the core
electrical network components in the GroundInsight package. Each database model facilitates the
storage, retrieval, and manipulation of data related to BusTypes, BranchTypes, Buses, Branches,
Faults, Sources, Paths, and Networks. The models include methods to convert between Pydantic
models and SQLAlchemy database models, ensuring seamless data integration and persistence.

Ownership model
---------------
``BusTypeDB`` and ``BranchTypeDB`` form a *global catalogue*: a type is identified by its
name alone and is deliberately shared between networks, so re-saving an edited type updates
every network that references it.

Every other element -- ``BusDB``, ``BranchDB``, ``FaultDB``, ``SourceDB``, ``PathDB`` and the
``PathSegmentDB`` rows -- belongs to exactly one network. This mirrors the Pydantic
:class:`~groundinsight.models.core_models.Network`, whose element dictionaries only require
uniqueness *within* a network. The primary key of those tables is therefore the composite
``(network_name, name)``; the relationships from ``NetworkDB`` are plain one-to-many
collections with ``delete-orphan`` cascade, so dropping a network (or shrinking one on
overwrite) removes its child rows instead of leaving them behind.

Earlier revisions keyed the child tables by ``name`` alone and linked them to their network
through ``network_buses`` / ``network_branches`` / ``network_faults`` / ``network_sources`` /
``network_paths`` association tables. Two networks that happened to contain an element of the
same name -- the default case for ``create_paths``, which names every path ``path_1``,
``path_2``, ... -- then shared a single row, so saving one network silently rewrote the other.
Databases written by those revisions are *not* readable by this schema; they are detected and
rejected with an actionable error by
:func:`groundinsight.database.crud.ensure_current_schema`.

Collection order is preserved explicitly through ``position`` columns rather than being left to
the database. ``PathDB.segments`` in particular is order-sensitive: the solver walks the
segments from the source bus onwards and fails if consecutive segments do not connect.
"""

from sqlalchemy import (
    Column,
    Integer,
    Float,
    String,
    Text,
    ForeignKey,
    ForeignKeyConstraint,
    JSON,
    Boolean,
    PickleType,
)
from .core_models import (
    ComplexNumber,
    BusType,
    BranchType,
    Bus,
    Branch,
    Fault,
    Source,
    Path,
    Network,
    Result,
)
from sqlalchemy.orm import declarative_base, relationship

Base = declarative_base()


class ComplexNumberDB(Base):
    """
    ComplexNumberDB Model.

    Represents a complex number with real and imaginary parts for storage in the database.
    """

    __tablename__ = "complex_numbers"

    id = Column(Integer, primary_key=True)
    value = Column(JSON, nullable=False)

    def to_pydantic(self):
        return ComplexNumber(**self.value)

    @classmethod
    def from_pydantic(cls, complex_number: ComplexNumber):
        return cls(value={"real": complex_number.real, "imag": complex_number.imag})


class BusTypeDB(Base):
    """
    BusTypeDB Model.

    Represents a BusType in the database, including its properties, the
    mandatory frequency-domain ``impedance_formula``, the optional
    lumped RLC formulas for the transient solvers (``R_formula``,
    ``L_formula``, ``C_formula``) and the optional thermal-limit data of
    the earthing conductor and the earth electrode (EN 50522 /
    IEC 60949), consumed by
    :func:`groundinsight.check_node_limits`.
    """

    __tablename__ = "bus_types"

    name = Column(String, primary_key=True)
    description = Column(Text, nullable=True)
    system_type = Column(String, nullable=False)
    voltage_level = Column(Float, nullable=False)
    impedance_formula = Column(Text, nullable=False)
    R_formula = Column(Text, nullable=True)
    L_formula = Column(Text, nullable=True)
    C_formula = Column(Text, nullable=True)
    earthing_conductor_material = Column(String, nullable=True)
    earthing_conductor_cross_section_mm2 = Column(Float, nullable=True)
    earthing_conductor_theta_initial_C = Column(Float, nullable=True)
    earthing_conductor_theta_final_C = Column(Float, nullable=True)
    earthing_conductor_current_split = Column(Float, nullable=True)
    electrode_material = Column(String, nullable=True)
    electrode_cross_section_mm2 = Column(Float, nullable=True)
    electrode_theta_initial_C = Column(Float, nullable=True)
    electrode_theta_final_C = Column(Float, nullable=True)
    electrode_current_split = Column(Float, nullable=True)

    def to_pydantic(self):
        # The scalar-with-default columns are stored nullable so that rows
        # written before these fields existed still load; ``None`` therefore
        # has to be mapped back onto the pydantic default rather than passed
        # through, which would fail validation.
        return BusType(
            name=self.name,
            description=self.description,
            system_type=self.system_type,
            voltage_level=self.voltage_level,
            impedance_formula=self.impedance_formula,
            R_formula=self.R_formula,
            L_formula=self.L_formula,
            C_formula=self.C_formula,
            earthing_conductor_material=self.earthing_conductor_material,
            earthing_conductor_cross_section_mm2=self.earthing_conductor_cross_section_mm2,
            earthing_conductor_theta_initial_C=(
                self.earthing_conductor_theta_initial_C
                if self.earthing_conductor_theta_initial_C is not None
                else 20.0
            ),
            earthing_conductor_theta_final_C=self.earthing_conductor_theta_final_C,
            earthing_conductor_current_split=(
                self.earthing_conductor_current_split
                if self.earthing_conductor_current_split is not None
                else 1.0
            ),
            electrode_material=self.electrode_material,
            electrode_cross_section_mm2=self.electrode_cross_section_mm2,
            electrode_theta_initial_C=(
                self.electrode_theta_initial_C
                if self.electrode_theta_initial_C is not None
                else 20.0
            ),
            electrode_theta_final_C=self.electrode_theta_final_C,
            electrode_current_split=(
                self.electrode_current_split
                if self.electrode_current_split is not None
                else 1.0
            ),
        )

    @classmethod
    def from_pydantic(cls, bus_type: BusType):
        return cls(
            name=bus_type.name,
            description=bus_type.description,
            system_type=bus_type.system_type,
            voltage_level=bus_type.voltage_level,
            impedance_formula=bus_type.impedance_formula,
            R_formula=bus_type.R_formula,
            L_formula=bus_type.L_formula,
            C_formula=bus_type.C_formula,
            earthing_conductor_material=bus_type.earthing_conductor_material,
            earthing_conductor_cross_section_mm2=bus_type.earthing_conductor_cross_section_mm2,
            earthing_conductor_theta_initial_C=bus_type.earthing_conductor_theta_initial_C,
            earthing_conductor_theta_final_C=bus_type.earthing_conductor_theta_final_C,
            earthing_conductor_current_split=bus_type.earthing_conductor_current_split,
            electrode_material=bus_type.electrode_material,
            electrode_cross_section_mm2=bus_type.electrode_cross_section_mm2,
            electrode_theta_initial_C=bus_type.electrode_theta_initial_C,
            electrode_theta_final_C=bus_type.electrode_theta_final_C,
            electrode_current_split=bus_type.electrode_current_split,
        )


def _real_dict_to_json(values):
    """Serialise a ``Dict[float, float]`` to JSON-compatible form (string
    keys), or ``None`` if the input is ``None``/empty."""
    if not values:
        return None
    return {str(freq): float(val) for freq, val in values.items()}


def _real_dict_from_json(stored):
    """Deserialise a JSON ``{str(freq): float}`` back into
    ``Dict[float, float]``, or ``None`` if missing."""
    if not stored:
        return None
    return {float(freq): float(val) for freq, val in stored.items()}


def _results_to_json(results):
    """Serialise ``Dict[str, Result]`` to a JSON-compatible mapping, or
    ``None`` when there are no results to store. Previously the SQLite backend
    dropped ``Network.results`` entirely while the JSON backend kept them; this
    restores parity so a solved network survives a database round-trip."""
    if not results:
        return None
    return {name: res.model_dump(mode="json") for name, res in results.items()}


def _results_from_json(stored):
    """Deserialise the stored results mapping back into ``Dict[str, Result]``."""
    if not stored:
        return {}
    return {name: Result.model_validate(data) for name, data in stored.items()}


class BusDB(Base):
    """
    BusDB Model.

    Represents a Bus in the database. Carries the frequency-domain
    ``impedance`` dict and -- when the type defines them -- the lumped
    ``R``, ``L`` and ``C`` dicts used by the transient solvers.

    A bus belongs to exactly one network; the primary key is the composite
    ``(network_name, name)`` so two networks may each own a bus of the same
    name. ``position`` records the insertion order of ``Network.buses`` so a
    round-trip through the database returns the dictionary in the order it
    was written.
    """

    __tablename__ = "buses"

    network_name = Column(String, ForeignKey("networks.name"), primary_key=True)
    name = Column(String, primary_key=True)
    position = Column(Integer, nullable=False, default=0)
    description = Column(Text, nullable=True)
    type_name = Column(String, ForeignKey("bus_types.name"))
    specific_earth_resistance = Column(Float, default=100.0)
    impedance = Column(
        JSON
    )  # Store impedance as JSON (frequency: {'real': x, 'imag': y})
    active = Column(Boolean, nullable=False, default=True)
    R = Column(JSON, nullable=True)
    L = Column(JSON, nullable=True)
    C = Column(JSON, nullable=True)

    type = relationship("BusTypeDB", backref="buses")
    network = relationship("NetworkDB", back_populates="buses")

    def to_pydantic(self):
        """Convert the row back into a Pydantic :class:`Bus`.

        Returns
        -------
        Bus
            The reconstructed bus.

        Raises
        ------
        ValueError
            If ``type_name`` does not resolve to a stored ``BusTypeDB`` --
            a bus type deleted from a shared database, or a hand-edited
            row. Without the check the unresolved relationship surfaced as
            ``AttributeError: 'NoneType' object has no attribute
            'to_pydantic'``, which names neither the bus nor the type.
        """
        if self.type is None:
            raise ValueError(
                f"Bus '{self.name}' of network '{self.network_name}' references "
                f"bus type '{self.type_name}', which is not stored in the "
                "database. The database is inconsistent -- re-save that bus "
                "type or point the bus at an existing one."
            )

        # Convert impedance JSON to Dict[float, ComplexNumber]
        impedance = (
            {
                float(freq): ComplexNumber(**value)
                for freq, value in self.impedance.items()
            }
            if self.impedance
            else {}
        )

        return Bus(
            name=self.name,
            description=self.description,
            type=self.type.to_pydantic(),
            impedance=impedance,
            specific_earth_resistance=self.specific_earth_resistance,
            active=True if self.active is None else bool(self.active),
            R=_real_dict_from_json(self.R),
            L=_real_dict_from_json(self.L),
            C=_real_dict_from_json(self.C),
        )

    @classmethod
    def from_pydantic(cls, bus: Bus, network_name: str = None, position: int = 0):
        """Build a ``BusDB`` row from a Pydantic :class:`Bus`.

        Parameters
        ----------
        bus : Bus
            The bus to convert.
        network_name : str, optional
            Name of the owning network. Part of the composite primary key;
            may be left out when the row is appended to
            ``NetworkDB.buses``, in which case SQLAlchemy fills it in.
        position : int, optional
            Zero-based index of the bus inside ``Network.buses``, used to
            restore the dictionary order on load. Defaults to ``0``.

        Returns
        -------
        BusDB
            The unattached database row.
        """
        # Convert impedance to JSON serializable format
        impedance = (
            {
                str(freq): {"real": imp.real, "imag": imp.imag}
                for freq, imp in bus.impedance.items()
            }
            if bus.impedance
            else {}
        )

        return cls(
            network_name=network_name,
            position=position,
            name=bus.name,
            description=bus.description,
            type_name=bus.type.name,
            specific_earth_resistance=bus.specific_earth_resistance,
            impedance=impedance,
            active=bus.active,
            R=_real_dict_to_json(bus.R),
            L=_real_dict_to_json(bus.L),
            C=_real_dict_to_json(bus.C),
        )


class BranchTypeDB(Base):
    """
    BranchTypeDB Model.

    Represents a BranchType in the database, including the mandatory
    ``self_impedance_formula`` / ``mutual_impedance_formula`` and the
    optional lumped RLCM formulas for the transient solvers
    (``R_self_formula``, ``L_self_formula``, ``C_self_formula``,
    ``R_mutual_formula``, ``M_mutual_formula``).
    """

    __tablename__ = "branch_types"

    name = Column(String, primary_key=True)
    description = Column(Text, nullable=True)
    grounding_conductor = Column(Boolean, nullable=False)
    self_impedance_formula = Column(Text, nullable=False)
    mutual_impedance_formula = Column(Text, nullable=False)
    R_self_formula = Column(Text, nullable=True)
    L_self_formula = Column(Text, nullable=True)
    C_self_formula = Column(Text, nullable=True)
    R_mutual_formula = Column(Text, nullable=True)
    M_mutual_formula = Column(Text, nullable=True)
    conductor_material = Column(String, nullable=True)
    cross_section_mm2 = Column(Float, nullable=True)
    theta_initial_C = Column(Float, nullable=True)
    theta_final_C = Column(Float, nullable=True)

    def to_pydantic(self):
        return BranchType(
            name=self.name,
            description=self.description,
            grounding_conductor=self.grounding_conductor,
            self_impedance_formula=self.self_impedance_formula,
            mutual_impedance_formula=self.mutual_impedance_formula,
            R_self_formula=self.R_self_formula,
            L_self_formula=self.L_self_formula,
            C_self_formula=self.C_self_formula,
            R_mutual_formula=self.R_mutual_formula,
            M_mutual_formula=self.M_mutual_formula,
            conductor_material=self.conductor_material,
            cross_section_mm2=self.cross_section_mm2,
            theta_initial_C=(self.theta_initial_C if self.theta_initial_C is not None else 20.0),
            theta_final_C=self.theta_final_C,
        )

    @classmethod
    def from_pydantic(cls, branch_type: BranchType):
        return cls(
            name=branch_type.name,
            description=branch_type.description,
            grounding_conductor=branch_type.grounding_conductor,
            self_impedance_formula=branch_type.self_impedance_formula,
            mutual_impedance_formula=branch_type.mutual_impedance_formula,
            R_self_formula=branch_type.R_self_formula,
            L_self_formula=branch_type.L_self_formula,
            C_self_formula=branch_type.C_self_formula,
            R_mutual_formula=branch_type.R_mutual_formula,
            M_mutual_formula=branch_type.M_mutual_formula,
            conductor_material=branch_type.conductor_material,
            cross_section_mm2=branch_type.cross_section_mm2,
            theta_initial_C=branch_type.theta_initial_C,
            theta_final_C=branch_type.theta_final_C,
        )


class BranchDB(Base):
    """
    BranchDB Model.

    Represents a Branch in the database, including its properties, type,
    connected buses, the frequency-domain impedance dicts and -- when the
    type defines them -- the lumped RLCM dicts used by the transient
    solvers.

    A branch belongs to exactly one network; the primary key is the
    composite ``(network_name, name)``. ``from_bus_name`` / ``to_bus_name``
    reference buses of the *same* network, hence the composite foreign keys.
    """

    __tablename__ = "branches"

    __table_args__ = (
        ForeignKeyConstraint(
            ["network_name", "from_bus_name"], ["buses.network_name", "buses.name"]
        ),
        ForeignKeyConstraint(
            ["network_name", "to_bus_name"], ["buses.network_name", "buses.name"]
        ),
    )

    network_name = Column(String, ForeignKey("networks.name"), primary_key=True)
    name = Column(String, primary_key=True)
    position = Column(Integer, nullable=False, default=0)
    description = Column(Text, nullable=True)
    type_name = Column(String, ForeignKey("branch_types.name"))
    length = Column(Float, nullable=False)
    from_bus_name = Column(String)
    to_bus_name = Column(String)
    self_impedance = Column(JSON)
    mutual_impedance = Column(JSON)
    specific_earth_resistance = Column(Float, default=100.0)
    parallel_coefficient = Column(Float, nullable=True)
    active = Column(Boolean, nullable=False, default=True)
    R_self = Column(JSON, nullable=True)
    L_self = Column(JSON, nullable=True)
    C_self = Column(JSON, nullable=True)
    R_mutual = Column(JSON, nullable=True)
    M_mutual = Column(JSON, nullable=True)

    type = relationship("BranchTypeDB", backref="branches")
    network = relationship("NetworkDB", back_populates="branches")
    # ``from_bus`` / ``to_bus`` object relationships were dropped together
    # with the single-column bus key: ``to_pydantic`` only ever needed the
    # plain names, and mapping them over the composite key would overlap
    # with ``network`` on the shared ``network_name`` column.

    def to_pydantic(self):
        """Convert the row back into a Pydantic :class:`Branch`.

        Returns
        -------
        Branch
            The reconstructed branch.

        Raises
        ------
        ValueError
            If ``type_name`` does not resolve to a stored
            ``BranchTypeDB``. See :meth:`BusDB.to_pydantic` for the
            rationale.
        """
        if self.type is None:
            raise ValueError(
                f"Branch '{self.name}' of network '{self.network_name}' "
                f"references branch type '{self.type_name}', which is not "
                "stored in the database. The database is inconsistent -- "
                "re-save that branch type or point the branch at an existing "
                "one."
            )

        # Convert impedance JSON to Dict[float, ComplexNumber]
        self_impedance = (
            {
                float(freq): ComplexNumber(**value)
                for freq, value in self.self_impedance.items()
            }
            if self.self_impedance
            else {}
        )

        mutual_impedance = (
            {
                float(freq): ComplexNumber(**value)
                for freq, value in self.mutual_impedance.items()
            }
            if self.mutual_impedance
            else {}
        )

        return Branch(
            name=self.name,
            description=self.description,
            type=self.type.to_pydantic(),
            length=self.length,
            from_bus=self.from_bus_name,
            to_bus=self.to_bus_name,
            self_impedance=self_impedance,
            mutual_impedance=mutual_impedance,
            specific_earth_resistance=self.specific_earth_resistance,
            parallel_coefficient=self.parallel_coefficient,
            active=True if self.active is None else bool(self.active),
            R_self=_real_dict_from_json(self.R_self),
            L_self=_real_dict_from_json(self.L_self),
            C_self=_real_dict_from_json(self.C_self),
            R_mutual=_real_dict_from_json(self.R_mutual),
            M_mutual=_real_dict_from_json(self.M_mutual),
        )

    @classmethod
    def from_pydantic(cls, branch: Branch, network_name: str = None, position: int = 0):
        """Build a ``BranchDB`` row from a Pydantic :class:`Branch`.

        Parameters
        ----------
        branch : Branch
            The branch to convert.
        network_name : str, optional
            Name of the owning network. Part of the composite primary key.
        position : int, optional
            Zero-based index of the branch inside ``Network.branches``, used
            to restore the dictionary order on load. Defaults to ``0``.

        Returns
        -------
        BranchDB
            The unattached database row.
        """
        # Convert impedance to JSON serializable format
        self_impedance = (
            {
                str(freq): {"real": imp.real, "imag": imp.imag}
                for freq, imp in branch.self_impedance.items()
            }
            if branch.self_impedance
            else {}
        )

        mutual_impedance = (
            {
                str(freq): {"real": imp.real, "imag": imp.imag}
                for freq, imp in branch.mutual_impedance.items()
            }
            if branch.mutual_impedance
            else {}
        )

        return cls(
            network_name=network_name,
            position=position,
            name=branch.name,
            description=branch.description,
            type_name=branch.type.name,
            length=branch.length,
            from_bus_name=branch.from_bus,
            to_bus_name=branch.to_bus,
            self_impedance=self_impedance,
            mutual_impedance=mutual_impedance,
            specific_earth_resistance=branch.specific_earth_resistance,
            parallel_coefficient=branch.parallel_coefficient,
            active=branch.active,
            R_self=_real_dict_to_json(branch.R_self),
            L_self=_real_dict_to_json(branch.L_self),
            C_self=_real_dict_to_json(branch.C_self),
            R_mutual=_real_dict_to_json(branch.R_mutual),
            M_mutual=_real_dict_to_json(branch.M_mutual),
        )


class FaultDB(Base):
    """
    FaultDB Model.

    Represents a Fault in the database, including its properties and associated bus.

    A fault belongs to exactly one network; the primary key is the composite
    ``(network_name, name)`` and ``bus_name`` references a bus of the same
    network.
    """

    __tablename__ = "faults"

    __table_args__ = (
        ForeignKeyConstraint(
            ["network_name", "bus_name"], ["buses.network_name", "buses.name"]
        ),
    )

    network_name = Column(String, ForeignKey("networks.name"), primary_key=True)
    name = Column(String, primary_key=True)
    position = Column(Integer, nullable=False, default=0)
    description = Column(Text, nullable=True)
    bus_name = Column(String)
    scalings = Column(JSON, nullable=False)
    active = Column(Boolean, default=False)
    # IEC 60909-0 short-circuit duration and AC heat factor
    t_k_s = Column(Float, nullable=True)
    n_factor = Column(Float, nullable=True)

    network = relationship("NetworkDB", back_populates="faults")

    def to_pydantic(self):
        # Convert scalings JSON to Dict[float, float]
        scalings = {float(freq): scale for freq, scale in self.scalings.items()}

        fault = Fault(
            name=self.name,
            description=self.description,
            bus=self.bus_name,
            scalings=scalings,
            t_k_s=self.t_k_s,
            # Legacy rows written before the 60909 columns existed store
            # NULL here; fall back to the far-from-generator default
            # instead of failing validation on None.
            n_factor=1.0 if self.n_factor is None else self.n_factor,
        )
        fault._set_active(self.active)
        return fault

    @classmethod
    def from_pydantic(cls, fault: Fault, network_name: str = None, position: int = 0):
        """Build a ``FaultDB`` row from a Pydantic :class:`Fault`.

        Parameters
        ----------
        fault : Fault
            The fault to convert.
        network_name : str, optional
            Name of the owning network. Part of the composite primary key.
        position : int, optional
            Zero-based index of the fault inside ``Network.faults``, used to
            restore the dictionary order on load. Defaults to ``0``.

        Returns
        -------
        FaultDB
            The unattached database row.
        """
        scalings = {str(freq): scale for freq, scale in fault.scalings.items()}
        return cls(
            network_name=network_name,
            position=position,
            name=fault.name,
            description=fault.description,
            bus_name=fault.bus,
            scalings=scalings,
            active=fault.active,
            t_k_s=fault.t_k_s,
            n_factor=fault.n_factor,
        )


class SourceDB(Base):
    """
    SourceDB Model.

    Represents a Source in the database. Both the legacy current-source mode
    and the Thevenin (voltage-source) mode are persisted in the same row.
    The ``source_type`` column selects which of ``values`` and the pair
    ``(voltage, source_impedance)`` carries the actual data; the other
    fields are stored as ``NULL``.

    A source belongs to exactly one network; the primary key is the
    composite ``(network_name, name)`` and ``bus_name`` references a bus of
    the same network.
    """

    __tablename__ = "sources"

    __table_args__ = (
        ForeignKeyConstraint(
            ["network_name", "bus_name"], ["buses.network_name", "buses.name"]
        ),
    )

    network_name = Column(String, ForeignKey("networks.name"), primary_key=True)
    name = Column(String, primary_key=True)
    position = Column(Integer, nullable=False, default=0)
    description = Column(Text, nullable=True)
    bus_name = Column(String)
    source_type = Column(String, nullable=False, default="current")
    # Current-source mode: frequency -> {real, imag}
    values = Column(JSON, nullable=True)
    # Voltage-source (Thevenin) mode: frequency -> {real, imag}
    voltage = Column(JSON, nullable=True)
    source_impedance = Column(JSON, nullable=True)
    # IEC 60909 characteristic quantities (metadata; not part of the solve)
    i_k_a = Column(Float, nullable=True)
    r_to_x = Column(Float, nullable=True)
    kappa = Column(Float, nullable=True)

    network = relationship("NetworkDB", back_populates="sources")

    @staticmethod
    def _freq_dict_to_pydantic(stored):
        """Convert a JSON-serialised ``{str(freq): {real, imag}}`` mapping
        back into ``Dict[float, ComplexNumber]``. Returns ``None`` if the
        stored value is missing."""
        if stored is None:
            return None
        result = {}
        for freq, value in stored.items():
            if isinstance(value, dict):
                cn = ComplexNumber(**value)
            else:
                cn = ComplexNumber(real=value, imag=0.0)
            result[float(freq)] = cn
        return result

    @staticmethod
    def _freq_dict_to_json(values):
        """Convert ``Dict[float, ComplexNumber]`` (or scalars) into a JSON-
        serialisable mapping with string keys, or ``None`` if the input is
        ``None``."""
        if values is None:
            return None
        out = {}
        for freq, val in values.items():
            if isinstance(val, ComplexNumber):
                out[str(freq)] = {"real": val.real, "imag": val.imag}
            else:
                out[str(freq)] = val  # Assume float or int
        return out

    def to_pydantic(self):
        source_type = self.source_type or "current"
        return Source(
            name=self.name,
            description=self.description,
            bus=self.bus_name,
            source_type=source_type,
            values=self._freq_dict_to_pydantic(self.values),
            voltage=self._freq_dict_to_pydantic(self.voltage),
            source_impedance=self._freq_dict_to_pydantic(self.source_impedance),
            i_k_a=self.i_k_a,
            r_to_x=self.r_to_x,
            kappa=self.kappa,
        )

    @classmethod
    def from_pydantic(cls, source: Source, network_name: str = None, position: int = 0):
        """Build a ``SourceDB`` row from a Pydantic :class:`Source`.

        Parameters
        ----------
        source : Source
            The source to convert.
        network_name : str, optional
            Name of the owning network. Part of the composite primary key.
        position : int, optional
            Zero-based index of the source inside ``Network.sources``, used
            to restore the dictionary order on load. Defaults to ``0``.

        Returns
        -------
        SourceDB
            The unattached database row.
        """
        return cls(
            network_name=network_name,
            position=position,
            name=source.name,
            description=source.description,
            bus_name=source.bus,
            source_type=source.source_type,
            values=cls._freq_dict_to_json(source.values),
            voltage=cls._freq_dict_to_json(source.voltage),
            source_impedance=cls._freq_dict_to_json(source.source_impedance),
            i_k_a=source.i_k_a,
            r_to_x=source.r_to_x,
            kappa=source.kappa,
        )


class PathSegmentDB(Base):
    """
    PathSegmentDB Model.

    One ordered segment of a :class:`PathDB`, pointing at a branch of the same
    network. This used to be a plain association table without an ordering
    column, so a round-trip returned ``Path.segments`` in an unspecified order.
    Segment order is semantically meaningful -- the solver walks the segments
    from the source bus onwards and raises if a segment does not connect to the
    current bus -- hence the explicit ``position`` column, which is part of the
    primary key and drives ``PathDB.segments``' ``order_by``.
    """

    __tablename__ = "path_segments"

    __table_args__ = (
        ForeignKeyConstraint(
            ["network_name", "path_name"], ["paths.network_name", "paths.name"]
        ),
        ForeignKeyConstraint(
            ["network_name", "branch_name"], ["branches.network_name", "branches.name"]
        ),
    )

    network_name = Column(String, primary_key=True)
    path_name = Column(String, primary_key=True)
    position = Column(Integer, primary_key=True)
    branch_name = Column(String, nullable=False)

    #: Read-only handle on the referenced branch. Declared ``viewonly`` so it
    #: does not contend with ``PathDB.segments`` over the shared
    #: ``network_name`` column; the segment rows are written from the plain
    #: ``network_name`` / ``branch_name`` values instead.
    branch = relationship(
        "BranchDB",
        primaryjoin=(
            "and_(PathSegmentDB.network_name == BranchDB.network_name, "
            "PathSegmentDB.branch_name == BranchDB.name)"
        ),
        foreign_keys="[PathSegmentDB.network_name, PathSegmentDB.branch_name]",
        viewonly=True,
    )


class PathDB(Base):
    """
    PathDB Model.

    Represents a Path in the database, including its properties, associated source and fault,
    and connected branches (segments).

    A path belongs to exactly one network; the primary key is the composite
    ``(network_name, name)``. This matters in practice because
    :func:`groundinsight.create_paths` names every path ``path_1``,
    ``path_2``, ..., so path-name collisions between two saved networks are
    the rule rather than the exception.
    """

    __tablename__ = "paths"

    __table_args__ = (
        ForeignKeyConstraint(
            ["network_name", "source_name"], ["sources.network_name", "sources.name"]
        ),
        ForeignKeyConstraint(
            ["network_name", "fault_name"], ["faults.network_name", "faults.name"]
        ),
    )

    network_name = Column(String, ForeignKey("networks.name"), primary_key=True)
    name = Column(String, primary_key=True)
    position = Column(Integer, nullable=False, default=0)
    description = Column(Text, nullable=True)
    source_name = Column(String)
    fault_name = Column(String)

    network = relationship("NetworkDB", back_populates="paths")
    segments = relationship(
        "PathSegmentDB",
        primaryjoin=(
            "and_(PathDB.network_name == PathSegmentDB.network_name, "
            "PathDB.name == PathSegmentDB.path_name)"
        ),
        foreign_keys="[PathSegmentDB.network_name, PathSegmentDB.path_name]",
        order_by="PathSegmentDB.position",
        cascade="all, delete-orphan",
    )

    def to_pydantic(self):
        """Convert the row back into a Pydantic :class:`Path`.

        Returns
        -------
        Path
            The reconstructed path, with ``segments`` in stored order.

        Raises
        ------
        ValueError
            If a stored segment references a branch that is not present in
            the owning network -- a corrupt or hand-edited database.
        """
        segments = []
        for segment in self.segments:
            if segment.branch is None:
                raise ValueError(
                    f"Path '{self.name}' of network '{self.network_name}' references "
                    f"branch '{segment.branch_name}', which is not stored in that "
                    "network. The database is inconsistent."
                )
            segments.append(segment.branch.to_pydantic())

        return Path(
            name=self.name,
            description=self.description,
            source=self.source_name,
            fault=self.fault_name,
            segments=segments,
        )

    @classmethod
    def from_pydantic(cls, path: Path, network_name: str = None, position: int = 0):
        """Build a ``PathDB`` row from a Pydantic :class:`Path`.

        Parameters
        ----------
        path : Path
            The path to convert.
        network_name : str, optional
            Name of the owning network. Part of the composite primary key.
        position : int, optional
            Zero-based index of the path inside ``Network.paths``, used to
            restore the dictionary order on load. Defaults to ``0``.

        Returns
        -------
        PathDB
            The unattached database row. ``segments`` is left empty; the
            caller fills it once the branches of the network are known.
        """
        return cls(
            network_name=network_name,
            position=position,
            name=path.name,
            description=path.description,
            source_name=path.source,
            fault_name=path.fault,
            # Segments will be added separately after the branches are saved
        )


class NetworkDB(Base):
    """
    NetworkDB Model.

    Represents a Network in the database, including its properties and associated components
    such as buses, branches, faults, sources, and paths. It also tracks the active fault within
    the network.

    The element collections are one-to-many with ``delete-orphan`` cascade: a
    bus, branch, fault, source or path row is owned by exactly one network,
    so deleting a network -- or replacing a collection on overwrite -- removes
    the rows it no longer contains instead of orphaning them in a global
    table. Each collection is ordered by the element's ``position`` column so
    the Pydantic dictionaries come back in insertion order.
    """

    __tablename__ = "networks"

    name = Column(String, primary_key=True)
    description = Column(Text, nullable=True)
    frequencies = Column(PickleType)  # Store list of frequencies
    results = Column(JSON, nullable=True)  # {fault_name: Result} serialised as JSON
    # Plain column rather than a foreign key into ``faults``: the faults table
    # now points back at ``networks``, and declaring both directions would make
    # the two tables mutually dependent for schema creation. The value is only
    # ever read back as a name.
    active_fault_name = Column(String, nullable=True)

    # Relationships
    buses = relationship(
        "BusDB",
        back_populates="network",
        cascade="all, delete-orphan",
        order_by="BusDB.position",
    )
    branches = relationship(
        "BranchDB",
        back_populates="network",
        cascade="all, delete-orphan",
        order_by="BranchDB.position",
    )
    faults = relationship(
        "FaultDB",
        back_populates="network",
        cascade="all, delete-orphan",
        order_by="FaultDB.position",
    )
    sources = relationship(
        "SourceDB",
        back_populates="network",
        cascade="all, delete-orphan",
        order_by="SourceDB.position",
    )
    paths = relationship(
        "PathDB",
        back_populates="network",
        cascade="all, delete-orphan",
        order_by="PathDB.position",
    )

    def to_pydantic(self):
        return Network(
            name=self.name,
            description=self.description,
            frequencies=self.frequencies,
            buses={bus.name: bus.to_pydantic() for bus in self.buses},
            branches={branch.name: branch.to_pydantic() for branch in self.branches},
            faults={fault.name: fault.to_pydantic() for fault in self.faults},
            sources={source.name: source.to_pydantic() for source in self.sources},
            paths={path.name: path.to_pydantic() for path in self.paths},
            active_fault=self.active_fault_name,
            results=_results_from_json(self.results),
        )

    @classmethod
    def from_pydantic(cls, network: Network):
        return cls(
            name=network.name,
            description=network.description,
            frequencies=network.frequencies,
            results=_results_to_json(network.results),
            active_fault_name=network.active_fault,
        )

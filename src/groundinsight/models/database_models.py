# models/database_models.py

"""
Database Models Module.

This module defines the SQLAlchemy ORM (Object-Relational Mapping) models corresponding to the core
electrical network components in the GroundInsight package. Each database model facilitates the
storage, retrieval, and manipulation of data related to BusTypes, BranchTypes, Buses, Branches,
Faults, Sources, Paths, and Networks. The models include methods to convert between Pydantic
models and SQLAlchemy database models, ensuring seamless data integration and persistence.
"""

from sqlalchemy import (
    Column,
    Integer,
    Float,
    String,
    Text,
    ForeignKey,
    JSON,
    Boolean,
    Table,
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

    Represents a BusType in the database, including its properties and associated buses.
    """

    __tablename__ = "bus_types"

    name = Column(String, primary_key=True)
    description = Column(Text, nullable=True)
    system_type = Column(String, nullable=False)
    voltage_level = Column(Float, nullable=False)
    impedance_formula = Column(Text, nullable=False)

    def to_pydantic(self):
        return BusType(
            name=self.name,
            description=self.description,
            system_type=self.system_type,
            voltage_level=self.voltage_level,
            impedance_formula=self.impedance_formula,
        )

    @classmethod
    def from_pydantic(cls, bus_type: BusType):
        return cls(
            name=bus_type.name,
            description=bus_type.description,
            system_type=bus_type.system_type,
            voltage_level=bus_type.voltage_level,
            impedance_formula=bus_type.impedance_formula,
        )


class BusDB(Base):
    """
    BusDB Model.

    Represents a Bus in the database, including its properties, type, and associated faults and sources.
    """

    __tablename__ = "buses"

    name = Column(String, primary_key=True)
    description = Column(Text, nullable=True)
    type_name = Column(String, ForeignKey("bus_types.name"))
    specific_earth_resistance = Column(Float, default=100.0)
    impedance = Column(
        JSON
    )  # Store impedance as JSON (frequency: {'real': x, 'imag': y})

    type = relationship("BusTypeDB", backref="buses")

    def to_pydantic(self):
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
        )

    @classmethod
    def from_pydantic(cls, bus: Bus):
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
            name=bus.name,
            description=bus.description,
            type_name=bus.type.name,
            specific_earth_resistance=bus.specific_earth_resistance,
            impedance=impedance,
        )


class BranchTypeDB(Base):
    """
    BranchTypeDB Model.

    Represents a BranchType in the database, including its properties and associated branches.
    """

    __tablename__ = "branch_types"

    name = Column(String, primary_key=True)
    description = Column(Text, nullable=True)
    grounding_conductor = Column(Boolean, nullable=False)
    self_impedance_formula = Column(Text, nullable=False)
    mutual_impedance_formula = Column(Text, nullable=False)

    def to_pydantic(self):
        return BranchType(
            name=self.name,
            description=self.description,
            grounding_conductor=self.grounding_conductor,
            self_impedance_formula=self.self_impedance_formula,
            mutual_impedance_formula=self.mutual_impedance_formula,
        )

    @classmethod
    def from_pydantic(cls, branch_type: BranchType):
        return cls(
            name=branch_type.name,
            description=branch_type.description,
            grounding_conductor=branch_type.grounding_conductor,
            self_impedance_formula=branch_type.self_impedance_formula,
            mutual_impedance_formula=branch_type.mutual_impedance_formula,
        )


class BranchDB(Base):
    """
    BranchDB Model.

    Represents a Branch in the database, including its properties, type, connected buses,
    and associated impedances.
    """

    __tablename__ = "branches"

    name = Column(String, primary_key=True)
    description = Column(Text, nullable=True)
    type_name = Column(String, ForeignKey("branch_types.name"))
    length = Column(Float, nullable=False)
    from_bus_name = Column(String, ForeignKey("buses.name"))
    to_bus_name = Column(String, ForeignKey("buses.name"))
    self_impedance = Column(JSON)
    mutual_impedance = Column(JSON)
    specific_earth_resistance = Column(Float, default=100.0)
    parallel_coefficient = Column(Float, nullable=True)

    type = relationship("BranchTypeDB", backref="branches")
    from_bus = relationship("BusDB", foreign_keys=[from_bus_name])
    to_bus = relationship("BusDB", foreign_keys=[to_bus_name])

    def to_pydantic(self):
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
        )

    @classmethod
    def from_pydantic(cls, branch: Branch):
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
        )


class FaultDB(Base):
    """
    FaultDB Model.

    Represents a Fault in the database, including its properties and associated bus.
    """

    __tablename__ = "faults"

    name = Column(String, primary_key=True)
    description = Column(Text, nullable=True)
    bus_name = Column(String, ForeignKey("buses.name"))
    scalings = Column(JSON, nullable=False)
    active = Column(Boolean, default=False)

    bus = relationship("BusDB", backref="faults")

    def to_pydantic(self):
        # Convert scalings JSON to Dict[float, float]
        scalings = {float(freq): scale for freq, scale in self.scalings.items()}

        fault = Fault(
            name=self.name,
            description=self.description,
            bus=self.bus_name,
            scalings=scalings,
        )
        fault._set_active(self.active)
        return fault

    @classmethod
    def from_pydantic(cls, fault: Fault):
        scalings = {str(freq): scale for freq, scale in fault.scalings.items()}
        return cls(
            name=fault.name,
            description=fault.description,
            bus_name=fault.bus,
            scalings=scalings,
            active=fault.active,
        )


class SourceDB(Base):
    """
    SourceDB Model.

    Represents a Source in the database, including its properties and associated bus.
    """

    __tablename__ = "sources"

    name = Column(String, primary_key=True)
    description = Column(Text, nullable=True)
    bus_name = Column(String, ForeignKey("buses.name"))
    values = Column(JSON, nullable=False)

    bus = relationship("BusDB", backref="sources")

    def to_pydantic(self):
        # Convert values JSON to Dict[float, ComplexNumber]
        values = {
            float(freq): (
                ComplexNumber(**value)
                if isinstance(value, dict)
                else ComplexNumber(real=value, imag=0.0)
            )
            for freq, value in self.values.items()
        }
        return Source(
            name=self.name,
            description=self.description,
            bus=self.bus_name,
            values=values,
        )

    @classmethod
    def from_pydantic(cls, source: Source):
        values = {}
        for freq, val in source.values.items():
            if isinstance(val, ComplexNumber):
                values[str(freq)] = {"real": val.real, "imag": val.imag}
            else:
                values[str(freq)] = val  # Assume float or int
        return cls(
            name=source.name,
            description=source.description,
            bus_name=source.bus,
            values=values,
        )


# Association table for Path segments
path_segments = Table(
    "path_segments",
    Base.metadata,
    Column("path_name", String, ForeignKey("paths.name")),
    Column("branch_name", String, ForeignKey("branches.name")),
)


class PathDB(Base):
    """
    PathDB Model.

    Represents a Path in the database, including its properties, associated source and fault,
    and connected branches (segments).
    """

    __tablename__ = "paths"

    name = Column(String, primary_key=True)
    description = Column(Text, nullable=True)
    source_name = Column(String, ForeignKey("sources.name"))
    fault_name = Column(String, ForeignKey("faults.name"))

    source = relationship("SourceDB", backref="paths")
    fault = relationship("FaultDB", backref="paths")
    segments = relationship("BranchDB", secondary=path_segments)

    def to_pydantic(self):
        return Path(
            name=self.name,
            description=self.description,
            source=self.source_name,
            fault=self.fault_name,
            segments=[branch.to_pydantic() for branch in self.segments],
        )

    @classmethod
    def from_pydantic(cls, path: Path):
        return cls(
            name=path.name,
            description=path.description,
            source_name=path.source,
            fault_name=path.fault,
            # Segments will be added separately after the branches are saved
        )


# Association tables for many-to-many relationships
network_buses = Table(
    "network_buses",
    Base.metadata,
    Column("network_name", String, ForeignKey("networks.name")),
    Column("bus_name", String, ForeignKey("buses.name")),
)

network_branches = Table(
    "network_branches",
    Base.metadata,
    Column("network_name", String, ForeignKey("networks.name")),
    Column("branch_name", String, ForeignKey("branches.name")),
)

network_faults = Table(
    "network_faults",
    Base.metadata,
    Column("network_name", String, ForeignKey("networks.name")),
    Column("fault_name", String, ForeignKey("faults.name")),
)

network_sources = Table(
    "network_sources",
    Base.metadata,
    Column("network_name", String, ForeignKey("networks.name")),
    Column("source_name", String, ForeignKey("sources.name")),
)

network_paths = Table(
    "network_paths",
    Base.metadata,
    Column("network_name", String, ForeignKey("networks.name")),
    Column("path_name", String, ForeignKey("paths.name")),
)


class NetworkDB(Base):
    """
    NetworkDB Model.

    Represents a Network in the database, including its properties and associated components
    such as buses, branches, faults, sources, and paths. It also tracks the active fault within
    the network.
    """

    __tablename__ = "networks"

    name = Column(String, primary_key=True)
    description = Column(Text, nullable=True)
    frequencies = Column(PickleType)  # Store list of frequencies
    active_fault_name = Column(String, ForeignKey("faults.name"), nullable=True)

    # Relationships
    buses = relationship("BusDB", secondary=network_buses, backref="networks")
    branches = relationship("BranchDB", secondary=network_branches, backref="networks")
    faults = relationship("FaultDB", secondary=network_faults, backref="networks")
    sources = relationship("SourceDB", secondary=network_sources, backref="networks")
    paths = relationship("PathDB", secondary=network_paths, backref="networks")
    active_fault = relationship("FaultDB", foreign_keys=[active_fault_name])

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
        )

    @classmethod
    def from_pydantic(cls, network: Network):
        return cls(
            name=network.name,
            description=network.description,
            frequencies=network.frequencies,
            active_fault_name=network.active_fault,
        )

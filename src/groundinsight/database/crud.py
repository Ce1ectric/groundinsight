# database/crud.py


"""
CRUD Operations Module.

This module provides functions for creating, reading, updating, and deleting (CRUD) entities
in the GroundInsight database using SQLAlchemy sessions. It facilitates the management of
core electrical network components such as BusTypes, BranchTypes, Networks, Buses, Branches,
Faults, Sources, and Paths. The functions convert between Pydantic models and SQLAlchemy
database models to ensure seamless data manipulation and persistence.
"""

from groundinsight.models.core_models import Network, BusType, BranchType
from sqlalchemy.orm import Session
from groundinsight.models.database_models import (
    BusTypeDB,
    BusDB,
    BranchTypeDB,
    BranchDB,
    FaultDB,
    SourceDB,
    PathDB,
    NetworkDB,
)
from typing import Dict


def save_bustype(bus_type: BusType, session: Session):
    """
    Save a BusType to the database.

    This function converts a Pydantic `BusType` model to its corresponding SQLAlchemy
    `BusTypeDB` model and saves it to the database. If a BusType with the same name
    already exists, it will be updated.

    Args:
        bus_type (BusType): The BusType instance to be saved.
        session (Session): The SQLAlchemy session used for database operations.

    Raises:
        Exception: If there is an error during the database commit.

    """
    bus_type_db = BusTypeDB.from_pydantic(bus_type)
    session.merge(bus_type_db)
    session.commit()


def load_bustypes(session: Session) -> Dict[str, BusType]:
    """
    Load all BusTypes from the database.

    This function retrieves all BusType entries from the database and converts them
    into a dictionary mapping BusType names to their corresponding Pydantic models.

    Args:
        session (Session): The SQLAlchemy session used for database operations.

    Returns:
        Dict[str, BusType]: A dictionary where keys are BusType names and values are BusType instances.
    """
    bus_types = session.query(BusTypeDB).all()
    return {bt.name: bt.to_pydantic() for bt in bus_types}


def save_branchtype(branch_type: BranchType, session: Session):
    """
    Save a BranchType to the database.

    This function converts a Pydantic `BranchType` model to its corresponding SQLAlchemy
    `BranchTypeDB` model and saves it to the database. If a BranchType with the same name
    already exists, it will be updated.

    Args:
        branch_type (BranchType): The BranchType instance to be saved.
        session (Session): The SQLAlchemy session used for database operations.

    Raises:
        Exception: If there is an error during the database commit.
    """
    branch_type_db = BranchTypeDB.from_pydantic(branch_type)
    session.merge(branch_type_db)
    session.commit()


def load_branchtypes(session: Session) -> Dict[str, BranchType]:
    """
    Load all BranchTypes from the database.

    This function retrieves all BranchType entries from the database and converts them
    into a dictionary mapping BranchType names to their corresponding Pydantic models.

    Args:
        session (Session): The SQLAlchemy session used for database operations.

    Returns:
        Dict[str, BranchType]: A dictionary where keys are BranchType names and values are BranchType instances.
    """
    branch_types = session.query(BranchTypeDB).all()
    return {bt.name: bt.to_pydantic() for bt in branch_types}


def save_network(network: Network, session: Session, overwrite: bool = False):
    """
    Save a Network to the database.

    This function saves a comprehensive `Network` instance to the database, including all
    associated BusTypes, BranchTypes, Buses, Branches, Faults, Sources, and Paths. It handles
    the creation or updating of related entities and ensures referential integrity. If `overwrite`
    is set to `True`, an existing network with the same name will be deleted and replaced.

    Args:
        network (Network): The Network instance to be saved.
        session (Session): The SQLAlchemy session used for database operations.
        overwrite (bool, optional):
            If `True`, existing network data with the same name will be overwritten.
            Defaults to `False`.

    Raises:
        ValueError: If the network already exists and `overwrite` is set to `False`.
        Exception: If there is an error during the database commit.
    """
    # Check for existing network
    existing_network = session.get(NetworkDB, network.name)
    if existing_network and not overwrite:
        raise ValueError(
            f"Network '{network.name}' already exists. Use overwrite=True to overwrite."
        )
    elif existing_network and overwrite:
        session.delete(existing_network)
        session.commit()

    # Save BusTypes
    for bus in network.buses.values():
        bus_type_db = session.get(BusTypeDB, bus.type.name)
        if not bus_type_db:
            bus_type_db = BusTypeDB.from_pydantic(bus.type)
            session.add(bus_type_db)

    # Save Buses
    for bus in network.buses.values():
        bus_db = BusDB.from_pydantic(bus)
        session.merge(bus_db)

    # Save BranchTypes
    for branch in network.branches.values():
        branch_type_db = session.get(BranchTypeDB, branch.type.name)
        if not branch_type_db:
            branch_type_db = BranchTypeDB.from_pydantic(branch.type)
            session.add(branch_type_db)

    # Save Branches
    for branch in network.branches.values():
        branch_db = BranchDB.from_pydantic(branch)
        session.merge(branch_db)

    # Save Faults
    for fault in network.faults.values():
        fault_db = FaultDB.from_pydantic(fault)
        session.merge(fault_db)

    # Save Sources
    for source in network.sources.values():
        source_db = SourceDB.from_pydantic(source)
        session.merge(source_db)

    # Save Paths
    for path in network.paths.values():
        path_db = PathDB.from_pydantic(path)
        # Add segments after branches are saved
        path_db.segments = [
            session.get(BranchDB, branch.name) for branch in path.segments
        ]
        session.merge(path_db)

    # Create the NetworkDB object
    network_db = NetworkDB.from_pydantic(network)

    # **Add or merge the network_db into the session before setting relationships**
    network_db = session.merge(network_db)

    # Now set relationships
    network_db.buses = [
        session.get(BusDB, bus_name) for bus_name in network.buses.keys()
    ]
    network_db.branches = [
        session.get(BranchDB, branch_name) for branch_name in network.branches.keys()
    ]
    network_db.faults = [
        session.get(FaultDB, fault_name) for fault_name in network.faults.keys()
    ]
    network_db.sources = [
        session.get(SourceDB, source_name) for source_name in network.sources.keys()
    ]
    network_db.paths = [
        session.get(PathDB, path_name) for path_name in network.paths.keys()
    ]
    network_db.active_fault_name = network.active_fault

    # Commit the session
    session.commit()


def load_network(name: str, session: Session) -> Network:
    """
    Load a Network from the database.

    This function retrieves a `Network` instance by its name from the database and converts it
    into a Pydantic `Network` model. It ensures that all related entities such as Buses, Branches,
    Faults, Sources, and Paths are properly associated.

    Args:
        name (str): The name of the network to load.
        session (Session): The SQLAlchemy session used for database operations.

    Returns:
        Network: The loaded `Network` instance.

    Raises:
        ValueError: If the specified network does not exist in the database.
    """
    network_db = session.get(NetworkDB, name)
    if not network_db:
        raise ValueError(f"Network '{name}' not found.")
    network = network_db.to_pydantic()
    return network

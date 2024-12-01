# groundinsight/__init__.py

"""
GroundInsight Package Initialization.

This module initializes the GroundInsight package, sets up the database session,
and provides functions to manage bus types, branch types, and networks within
the grounding network analysis. It also includes utilities for saving and loading
data to and from JSON files and integrates plotting functionalities for visualizing
bus voltages and branch currents.
"""

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, scoped_session
from .database.crud import (
    save_bustype as _save_bustype,
    load_bustypes as _load_bustypes,
    save_branchtype as _save_branchtype,
    load_branchtypes as _load_branchtypes,
    save_network as _save_network,
    load_network as _load_network,
)
from typing import Optional
from typing import Dict
from pathlib import Path
from .models.core_models import BusType, BranchType, Network
from .models.database_models import BusTypeDB, BranchTypeDB, NetworkDB
from .network_operations import (
    create_network,
    create_bus,
    create_branch,
    create_fault,
    create_source,
    build_electrical_network,
    run_fault,
    create_network_assistant,
    create_paths,
)
from .plotting import plot_bus_voltages, plot_branch_currents, plot_bus_currents


__all__ = [
    "NetworkManager",
    "db_session",
    "create_network",
    "create_bus",
    "create_branch",
    "create_fault",
    "create_source",
    "build_electrical_network",
    "run_fault",
    "plot_bus_voltages",
    "plot_branch_currents",
    "plot_bus_currents",
    "create_network_assistant",
    "create_paths",
]

# Version
__version__ = "0.1.1"

# These will be initialized by start_dbsession()
engine = None
SessionLocal = None
session: Optional[scoped_session] = None


def start_dbsession(sqlite_path: str = "grounding.db"):
    """
    Initialize the database session.

    This function sets up the SQLAlchemy engine and sessionmaker, creates a scoped session,
    and initializes the database tables based on the defined models. If the database session
    is already active, it notifies the user and does not reinitialize.

    Args:
        sqlite_path (str, optional): The file path for the SQLite database. Defaults to "grounding.db".

    Raises:
        Exception: If there is an error during the database initialization.
    """
    global engine, SessionLocal, session

    if engine is not None:
        print("Database session already started.")
        return

    # Create an engine
    engine = create_engine(f"sqlite:///{sqlite_path}", echo=False)

    # Create a configured "Session" class
    SessionLocal = sessionmaker(bind=engine)

    # Create a thread-safe session
    session = scoped_session(SessionLocal)

    # Import Base from your models and create tables
    from .models.database_models import Base

    Base.metadata.create_all(engine)
    print(f"Database session started with '{sqlite_path}'.")


def close_dbsession():
    """
    Close the database session.

    This function removes the scoped session, disposes of the engine, and resets the session
    variables. If no database session is active, it notifies the user accordingly.
    """
    global session, engine, SessionLocal

    if session is not None:
        session.remove()
        session = None
        engine.dispose()
        engine = None
        SessionLocal = None
        print("Database session closed.")
    else:
        print("No database session to close.")


def save_bustype_to_db(bus_type: BusType, overwrite: bool = False):
    """
    Save a BusType to the database.

    This function saves a BusType instance to the database. If a BusType with the same name
    already exists and overwrite is False, it raises a ValueError. If overwrite is True, it
    updates the existing BusType.

    Args:
        bus_type (BusType): The BusType instance to save.
        overwrite (bool, optional): Whether to overwrite an existing BusType with the same name.
                                    Defaults to False.

    Raises:
        RuntimeError: If the database session is not started.
        ValueError: If the BusType already exists and overwrite is False.
    """
    if session is None:
        raise RuntimeError(
            "Database session is not started. Call gi.start_dbsession() first."
        )
    db_session = session()
    existing = db_session.get(BusTypeDB, bus_type.name)
    if existing and not overwrite:
        db_session.close()
        raise ValueError(
            f"BusType '{bus_type.name}' already exists. Use overwrite=True to overwrite."
        )
    _save_bustype(bus_type, db_session)
    db_session.close()


def load_bustypes_from_db() -> Dict[str, BusType]:
    """
    Load all BusTypes from the database.

    This function retrieves all BusType entries from the database and returns them as a dictionary
    mapping BusType names to BusType instances.

    Returns:
        Dict[str, BusType]: A dictionary of BusType instances keyed by their names.

    Raises:
        RuntimeError: If the database session is not started.
    """
    if session is None:
        raise RuntimeError(
            "Database session is not started. Call gi.start_dbsession() first."
        )
    db_session = session()
    bus_types = _load_bustypes(db_session)
    db_session.close()
    return bus_types


def save_branchtype_to_db(branch_type: BranchType, overwrite: bool = False):
    """
    Save a BranchType to the database.

    This function saves a BranchType instance to the database. If a BranchType with the same name
    already exists and overwrite is False, it raises a ValueError. If overwrite is True, it
    updates the existing BranchType.

    Args:
        branch_type (BranchType): The BranchType instance to save.
        overwrite (bool, optional): Whether to overwrite an existing BranchType with the same name.
                                     Defaults to False.

    Raises:
        RuntimeError: If the database session is not started.
        ValueError: If the BranchType already exists and overwrite is False.
    """
    if session is None:
        raise RuntimeError(
            "Database session is not started. Call gi.start_dbsession() first."
        )
    db_session = session()
    existing = db_session.get(BranchTypeDB, branch_type.name)
    if existing and not overwrite:
        db_session.close()
        raise ValueError(
            f"BranchType '{branch_type.name}' already exists. Use overwrite=True to overwrite."
        )
    _save_branchtype(branch_type, db_session)
    db_session.close()


def load_branchtypes_from_db() -> Dict[str, BranchType]:
    """
    Load all BranchTypes from the database.

    This function retrieves all BranchType entries from the database and returns them as a dictionary
    mapping BranchType names to BranchType instances.

    Returns:
        Dict[str, BranchType]: A dictionary of BranchType instances keyed by their names.

    Raises:
        RuntimeError: If the database session is not started.
    """
    if session is None:
        raise RuntimeError(
            "Database session is not started. Call gi.start_dbsession() first."
        )
    db_session = session()
    branch_types = _load_branchtypes(db_session)
    db_session.close()
    return branch_types


def save_network_to_db(network: Network, overwrite: bool = False):
    """
    Save a Network to the database.

    This function saves a Network instance to the database. If a Network with the same name
    already exists and overwrite is False, it raises a ValueError. If overwrite is True, it
    updates the existing Network.

    Args:
        network (Network): The Network instance to save.
        overwrite (bool, optional): Whether to overwrite an existing Network with the same name.
                                    Defaults to False.

    Raises:
        RuntimeError: If the database session is not started.
        ValueError: If the Network already exists and overwrite is False.
    """
    if session is None:
        raise RuntimeError(
            "Database session is not started. Call gi.start_dbsession() first."
        )
    db_session = session()
    existing = db_session.get(NetworkDB, network.name)
    if existing and not overwrite:
        db_session.close()
        raise ValueError(
            f"Network '{network.name}' already exists. Use overwrite=True to overwrite."
        )
    _save_network(network, db_session, overwrite=overwrite)
    db_session.close()


def load_network_from_db(name: str) -> Network:
    """
    Load a Network from the database by name.

    This function retrieves a Network entry from the database based on its name and returns
    it as a Network instance.

    Args:
        name (str): The name of the Network to load.

    Returns:
        Network: The loaded Network instance.

    Raises:
        RuntimeError: If the database session is not started.
        ValueError: If the Network with the specified name does not exist.
    """
    if session is None:
        raise RuntimeError(
            "Database session is not started. Call gi.start_dbsession() first."
        )
    db_session = session()
    network = _load_network(name, db_session)
    db_session.close()
    return network


def save_network_to_json(network: Network, path: str):
    """
    Save a Network instance to a JSON file.

    This function serializes a Network instance into JSON format and writes it to the specified file path.

    Args:
        network (Network): The Network instance to serialize and save.
        path (str): The file path where the JSON file will be saved.

    Raises:
        IOError: If there is an error writing to the file.
    """
    path = Path(path)
    with path.open("w") as f:
        f.write(network.model_dump_json(indent=4))


def load_network_from_json(path: str) -> Network:
    """
    Load a Network instance from a JSON file.

    This function reads a JSON file from the specified path, deserializes it, and returns
    the corresponding Network instance.

    Args:
        path (str): The file path of the JSON file to load.

    Returns:
        Network: The deserialized Network instance.

    Raises:
        IOError: If there is an error reading the file.
        ValueError: If the JSON content is invalid or does not conform to the Network model.
    """
    path = Path(path)
    with path.open("r") as f:
        json_string = f.read()
        model_instance = Network.model_validate_json(json_string)
    return model_instance

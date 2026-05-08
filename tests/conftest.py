# tests/conftest.py

"""
Test-suite-wide fixtures.

Ensures the on-disk SQLite fixture used by ``test_database.py`` is removed
before the suite runs so schema changes (new columns, dropped columns, etc.)
take effect on the next ``Base.metadata.create_all`` call. SQLAlchemy's
``create_all`` only creates tables that do not yet exist; it never adds
columns to an existing table. Without this fixture, switching to a schema
with a new column on ``buses`` or ``branches`` would fail with
``OperationalError: no such column: ...`` against a stale fixture DB.

The DB file is in ``.gitignore``; it is regenerated on every test run.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest


_FIXTURE_DB = Path(__file__).parent / "test_grounding.db"


@pytest.fixture(scope="session", autouse=True)
def _reset_test_grounding_db():
    """
    Delete ``tests/test_grounding.db`` once at the start of the test session.

    Yields control to the rest of the suite and does not clean up afterwards
    -- that lets developers inspect the produced DB after a failing run.
    """
    try:
        os.remove(_FIXTURE_DB)
    except FileNotFoundError:
        pass
    yield

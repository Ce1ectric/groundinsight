# gui/tests/conftest.py
"""
Shared fixtures of the GUI tests.

The NiceGUI ``user`` fixture is asynchronous; anyio's pytest plugin (installed
with NiceGUI's dependencies) runs the tests marked ``anyio`` on the backend
this fixture names.
"""

import pytest


@pytest.fixture
def anyio_backend():
    """Run the async GUI tests on asyncio, NiceGUI's event loop."""
    return "asyncio"

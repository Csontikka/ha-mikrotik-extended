"""Fixtures for Mikrotik Router tests."""

import pytest


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations):
    """Enable custom integrations for all tests."""
    yield


@pytest.fixture(autouse=True)
def restore_real_socket():
    """Undo the socket guard after every test.

    The Home Assistant test plugin disables sockets before each test by
    subclassing whatever socket.socket currently is, and nothing puts the
    real class back. The guard therefore nests one level deeper per test,
    and on Linux, where the event loop opens a socket pair through that
    chain, the suite hit the recursion limit once it grew past about 950
    tests. Restoring the real class here keeps the chain one level deep.
    """
    yield
    import pytest_socket

    pytest_socket.enable_socket()

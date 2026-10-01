"""Guard against the socket guard piling up across the suite (see conftest)."""

import socket


def test_socket_guard_does_not_nest():
    """Runs late in the suite by name; the guard must still be one level deep."""
    guards = [cls for cls in socket.socket.__mro__ if cls.__name__ == "GuardedSocket"]
    assert len(guards) <= 1, f"socket guard nested {len(guards)} deep"

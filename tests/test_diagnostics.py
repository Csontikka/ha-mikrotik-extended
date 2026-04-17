"""Tests for diagnostics export."""

from types import SimpleNamespace
from unittest.mock import MagicMock

from custom_components.mikrotik_extended.diagnostics import async_get_config_entry_diagnostics


async def test_diagnostics_redacts_and_returns_shape(hass):
    """async_get_config_entry_diagnostics returns redacted data + logs key."""
    entry = MagicMock()
    entry.data = {"host": "192.168.88.1", "password": "secret", "username": "admin"}
    entry.options = {"scan_interval": 10, "password": "shouldberedacted"}

    data_coord = SimpleNamespace(data={"router": {"serial_number": "ABC123", "arp": [{"mac-address": "AA:BB:CC:DD:EE:FF"}]}})
    tracker_coord = SimpleNamespace(data={"host": {"01": {"mac-address": "AA:BB:CC:DD:EE:FF", "host-name": "laptop"}}})
    entry.runtime_data = SimpleNamespace(
        data_coordinator=data_coord,
        tracker_coordinator=tracker_coord,
    )

    result = await async_get_config_entry_diagnostics(hass, entry)

    assert set(result.keys()) == {"entry", "data", "tracker", "logs"}
    assert "data" in result["entry"]
    assert "options" in result["entry"]
    # password in both entry.data and entry.options must be replaced with the
    # HA redaction marker, not just "different from the original"
    assert result["entry"]["data"]["password"] == "**REDACTED**"
    assert result["entry"]["options"]["password"] == "**REDACTED**"
    # logs is a list of formatted string entries (ring-buffer of LogRecord
    # messages formatted via logging.Formatter, not a list of dicts)
    assert isinstance(result["logs"], list)
    for entry_line in result["logs"]:
        assert isinstance(entry_line, str)

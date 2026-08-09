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


async def test_diagnostics_masks_identifiers_used_as_keys(hass):
    """Stores keyed by a MAC must not publish it as a plain object key (issue 25)."""
    entry = MagicMock()
    entry.data = {"host": "192.168.88.1"}
    entry.options = {}
    entry.runtime_data = SimpleNamespace(
        data_coordinator=SimpleNamespace(
            data={
                "host": {
                    "BC:F4:D4:11:22:33": {"source": "arp", "host-name": "laptop"},
                    "E0:98:06:DF:A4:65": {"source": "dhcp", "host-name": "phone"},
                },
                "arp": {"192.168.1.42": {"address": "192.168.1.42"}},
                "resource": {"cpu-load": 5},
            }
        ),
        tracker_coordinator=SimpleNamespace(data={"host": {"BC:F4:D4:11:22:33": {"source": "arp"}}}),
    )

    result = await async_get_config_entry_diagnostics(hass, entry)

    dumped = str(result)
    assert "BC:F4:D4:11:22:33" not in dumped
    assert "E0:98:06:DF:A4:65" not in dumped
    assert "192.168.1.42" not in dumped
    # non-identifier keys stay readable, otherwise the dump is useless
    assert "resource" in result["data"]
    assert result["data"]["resource"]["cpu-load"] == 5
    # the same MAC keeps one stable placeholder across stores, so entries can
    # still be correlated while reading the dump
    assert next(iter(result["data"]["host"])) == next(iter(result["tracker"]["host"]))


async def test_diagnostics_masks_addresses_in_derived_fields(hass):
    """Derived lists spell the field names differently and were never covered."""
    entry = MagicMock()
    entry.data = {}
    entry.options = {}
    entry.runtime_data = SimpleNamespace(
        data_coordinator=SimpleNamespace(
            data={
                "resource": {
                    "wired_clients_list": [{"mac": "BC:F4:D4:11:22:33", "address": "192.168.1.50", "host_name": "laptop"}],
                    "wireless_clients_list": [{"mac": "E0:98:06:DF:A4:65", "address": "192.168.1.51"}],
                },
                "dhcp_leases": {"leases": [{"mac": "AA:BB:CC:DD:EE:FF", "address": "192.168.1.52"}]},
                "cloud": {"public-address": "203.0.113.7"},
                "ip_address": {"iface": {"ip": "192.168.1.1", "network": "192.168.1.0"}},
            }
        ),
        tracker_coordinator=SimpleNamespace(data={}),
    )

    result = await async_get_config_entry_diagnostics(hass, entry)

    dumped = str(result)
    for leaked in (
        "BC:F4:D4:11:22:33",
        "E0:98:06:DF:A4:65",
        "AA:BB:CC:DD:EE:FF",
        "192.168.1.50",
        "192.168.1.52",
        "203.0.113.7",
        "192.168.1.1",
        "192.168.1.0",
    ):
        assert leaked not in dumped, leaked


async def test_diagnostics_masks_the_router_address_and_objects(hass):
    """The router host and non-string objects also reach the file (issue 25)."""
    from ipaddress import IPv4Network

    entry = MagicMock()
    entry.data = {"host": "172.21.52.1", "name": "router"}
    entry.options = {"zone": "home"}
    entry.runtime_data = SimpleNamespace(
        data_coordinator=SimpleNamespace(data={"dhcp-network": {"192.168.1.0/24": {"IPv4Network": IPv4Network("192.168.1.0/24")}}}),
        tracker_coordinator=SimpleNamespace(data={}),
    )

    result = await async_get_config_entry_diagnostics(hass, entry)

    dumped = str(result)
    assert "172.21.52.1" not in dumped
    assert "192.168.1.0" not in dumped
    # unrelated entry fields stay readable
    assert result["entry"]["data"]["name"] == "router"
    assert result["entry"]["options"]["zone"] == "home"


async def test_diagnostics_keeps_non_address_values_readable(hass):
    """Masking must not eat versions, names or states, or the dump is useless."""
    entry = MagicMock()
    entry.data = {}
    entry.options = {}
    entry.runtime_data = SimpleNamespace(
        data_coordinator=SimpleNamespace(
            data={
                "resource": {"version": "7.23.3", "board-name": "RB5009UG+S+", "uptime": "1d2h3m"},
                "interface": {"ether1": {"name": "ether1", "running": True, "type": "ether"}},
            }
        ),
        tracker_coordinator=SimpleNamespace(data={}),
    )

    result = await async_get_config_entry_diagnostics(hass, entry)

    assert result["data"]["resource"]["version"] == "7.23.3"
    assert result["data"]["resource"]["board-name"] == "RB5009UG+S+"
    assert result["data"]["resource"]["uptime"] == "1d2h3m"
    assert result["data"]["interface"]["ether1"]["name"] == "ether1"
    assert result["data"]["interface"]["ether1"]["running"] is True


async def test_diagnostics_keeps_the_host_source_visible(hass):
    """'source' tells restored twins from live entries, so it must not be redacted."""
    entry = MagicMock()
    entry.data = {}
    entry.options = {}
    entry.runtime_data = SimpleNamespace(
        data_coordinator=SimpleNamespace(data={"host": {"AA:BB:CC:DD:EE:FF": {"source": "restored"}}}),
        tracker_coordinator=SimpleNamespace(data={}),
    )

    result = await async_get_config_entry_diagnostics(hass, entry)

    assert next(iter(result["data"]["host"].values()))["source"] == "restored"


async def test_diagnostics_masks_addresses_in_logs(hass):
    """Captured log lines have their network identifiers masked (SEC-02)."""
    from custom_components.mikrotik_extended import _LOG_BUFFER

    entry = MagicMock()
    entry.data = {"host": "192.168.88.1", "password": "secret", "username": "admin"}
    entry.options = {}
    entry.runtime_data = SimpleNamespace(
        data_coordinator=SimpleNamespace(data={}),
        tracker_coordinator=SimpleNamespace(data={}),
    )

    _LOG_BUFFER.clear()
    _LOG_BUFFER.append("API query /ip/arp raw response: [{'address':'192.168.1.42','mac-address':'AA:BB:CC:DD:EE:01'}]")
    try:
        result = await async_get_config_entry_diagnostics(hass, entry)
    finally:
        _LOG_BUFFER.clear()

    joined = "\n".join(result["logs"])
    assert "192.168.1.42" not in joined
    assert "AA:BB:CC:DD:EE:01" not in joined
    # structure preserved: the message text is still there
    assert "API query /ip/arp raw response" in joined

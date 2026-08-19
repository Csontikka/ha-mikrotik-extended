"""Tests for the _skip_sensor filter function in entity.py."""

from __future__ import annotations

from unittest.mock import MagicMock

from custom_components.mikrotik_extended.const import (
    CONF_SENSOR_INTERFACES,
    CONF_SENSOR_NETWATCH_TRACKER,
    CONF_SENSOR_PORT_TRACKER,
    CONF_SENSOR_PORT_TRAFFIC,
    CONF_TRACK_HOSTS,
)
from custom_components.mikrotik_extended.entity import _skip_sensor


def _make_config_entry(**options):
    entry = MagicMock()
    entry.options = options
    return entry


def _make_desc(func="SomeFunc", data_path="interface", data_attribute="enabled"):
    desc = MagicMock()
    desc.func = func
    desc.data_path = data_path
    desc.data_attribute = data_attribute
    return desc


# ---------------------------------------------------------------------------
# MikrotikInterfaceTrafficSensor skips
# ---------------------------------------------------------------------------


class TestSkipTrafficSensor:
    def test_skip_when_traffic_disabled(self):
        entry = _make_config_entry(**{CONF_SENSOR_PORT_TRAFFIC: False})
        desc = _make_desc(func="MikrotikInterfaceTrafficSensor")
        data = {"eth0": {"type": "ether"}}
        assert _skip_sensor(entry, desc, data, "eth0") is True

    def test_no_skip_when_traffic_enabled(self):
        entry = _make_config_entry(**{CONF_SENSOR_PORT_TRAFFIC: True})
        desc = _make_desc(func="MikrotikInterfaceTrafficSensor")
        data = {"eth0": {"type": "ether"}}
        assert _skip_sensor(entry, desc, data, "eth0") is False

    def test_no_skip_bridge_type(self):
        """Bridge interfaces get traffic sensors too (#9): aggregated LAN/DMZ rates."""
        entry = _make_config_entry(**{CONF_SENSOR_PORT_TRAFFIC: True})
        desc = _make_desc(func="MikrotikInterfaceTrafficSensor")
        data = {"br0": {"type": "bridge"}}
        assert _skip_sensor(entry, desc, data, "br0") is False

    def test_no_skip_ether_type(self):
        entry = _make_config_entry(**{CONF_SENSOR_PORT_TRAFFIC: True})
        desc = _make_desc(func="MikrotikInterfaceTrafficSensor")
        data = {"eth0": {"type": "ether"}}
        assert _skip_sensor(entry, desc, data, "eth0") is False


# ---------------------------------------------------------------------------
# sensor_interfaces skips
# ---------------------------------------------------------------------------


class TestSkipInterfaceEntity:
    """With interface entities disabled nothing interface-derived is created."""

    def test_skip_port_switch(self):
        entry = _make_config_entry(**{CONF_SENSOR_INTERFACES: False})
        desc = _make_desc(func="MikrotikPortSwitch")
        data = {"ether1": {"type": "ether"}}
        assert _skip_sensor(entry, desc, data, "ether1") is True

    def test_skip_port_binary_sensor_even_when_tracker_enabled(self):
        entry = _make_config_entry(**{CONF_SENSOR_INTERFACES: False, CONF_SENSOR_PORT_TRACKER: True})
        desc = _make_desc(func="MikrotikPortBinarySensor")
        data = {"ether1": {"type": "ether"}}
        assert _skip_sensor(entry, desc, data, "ether1") is True

    def test_skip_traffic_sensor_even_when_traffic_enabled(self):
        entry = _make_config_entry(**{CONF_SENSOR_INTERFACES: False, CONF_SENSOR_PORT_TRAFFIC: True})
        desc = _make_desc(func="MikrotikInterfaceTrafficSensor")
        data = {"ether1": {"type": "ether"}}
        assert _skip_sensor(entry, desc, data, "ether1") is True

    def test_skip_ip_address_sensor(self):
        entry = _make_config_entry(**{CONF_SENSOR_INTERFACES: False})
        desc = _make_desc(func="MikrotikSensor", data_path="ip_address", data_attribute="ip")
        data = {"lan": {"ip": "192.168.88.1"}}
        assert _skip_sensor(entry, desc, data, "lan") is True

    def test_no_skip_when_interfaces_enabled(self):
        entry = _make_config_entry(**{CONF_SENSOR_INTERFACES: True})
        desc = _make_desc(func="MikrotikPortSwitch")
        data = {"ether1": {"type": "ether"}}
        assert _skip_sensor(entry, desc, data, "ether1") is False

    def test_no_skip_when_option_absent(self):
        """Entries predating the option keep creating interface entities."""
        entry = _make_config_entry()
        desc = _make_desc(func="MikrotikPortSwitch")
        data = {"ether1": {"type": "ether"}}
        assert _skip_sensor(entry, desc, data, "ether1") is False

    def test_unrelated_data_path_untouched(self):
        """Turning interfaces off must not suppress other categories."""
        entry = _make_config_entry(**{CONF_SENSOR_INTERFACES: False})
        desc = _make_desc(func="MikrotikSwitch", data_path="nat")
        data = {"rule1": {"enabled": True}}
        assert _skip_sensor(entry, desc, data, "rule1") is False


# ---------------------------------------------------------------------------
# client_traffic skips
# ---------------------------------------------------------------------------


class TestSkipClientTraffic:
    def test_skip_when_unavailable(self):
        entry = _make_config_entry()
        desc = _make_desc(data_path="client_traffic", data_attribute="tx-byte")
        data = {"client1": {"available": False, "tx-byte": 100}}
        assert _skip_sensor(entry, desc, data, "client1") is True

    def test_skip_when_attribute_missing(self):
        entry = _make_config_entry()
        desc = _make_desc(data_path="client_traffic", data_attribute="tx-byte")
        data = {"client1": {"available": True}}
        assert _skip_sensor(entry, desc, data, "client1") is True

    def test_no_skip_when_available_and_attribute_present(self):
        entry = _make_config_entry()
        desc = _make_desc(data_path="client_traffic", data_attribute="tx-byte")
        data = {"client1": {"available": True, "tx-byte": 100}}
        assert _skip_sensor(entry, desc, data, "client1") is False

    def test_skip_when_available_key_missing(self):
        entry = _make_config_entry()
        desc = _make_desc(data_path="client_traffic", data_attribute="tx-byte")
        data = {"client1": {"tx-byte": 100}}
        assert _skip_sensor(entry, desc, data, "client1") is True


# ---------------------------------------------------------------------------
# MikrotikPortBinarySensor skips
# ---------------------------------------------------------------------------


class TestSkipPortBinarySensor:
    def test_skip_wlan_type(self):
        entry = _make_config_entry(**{CONF_SENSOR_PORT_TRACKER: True})
        desc = _make_desc(func="MikrotikPortBinarySensor")
        data = {"wlan0": {"type": "wlan"}}
        assert _skip_sensor(entry, desc, data, "wlan0") is True

    def test_skip_when_tracker_disabled(self):
        entry = _make_config_entry(**{CONF_SENSOR_PORT_TRACKER: False})
        desc = _make_desc(func="MikrotikPortBinarySensor")
        data = {"eth0": {"type": "ether"}}
        assert _skip_sensor(entry, desc, data, "eth0") is True

    def test_no_skip_ether_with_tracker_enabled(self):
        entry = _make_config_entry(**{CONF_SENSOR_PORT_TRACKER: True})
        desc = _make_desc(func="MikrotikPortBinarySensor")
        data = {"eth0": {"type": "ether"}}
        assert _skip_sensor(entry, desc, data, "eth0") is False


# ---------------------------------------------------------------------------
# Netwatch skips
# ---------------------------------------------------------------------------


class TestSkipNetwatch:
    def test_skip_when_netwatch_disabled(self):
        entry = _make_config_entry(**{CONF_SENSOR_NETWATCH_TRACKER: False})
        desc = _make_desc(data_path="netwatch")
        data = {"nw1": {}}
        assert _skip_sensor(entry, desc, data, "nw1") is True

    def test_no_skip_when_netwatch_enabled(self):
        entry = _make_config_entry(**{CONF_SENSOR_NETWATCH_TRACKER: True})
        desc = _make_desc(data_path="netwatch")
        data = {"nw1": {}}
        assert _skip_sensor(entry, desc, data, "nw1") is False


# ---------------------------------------------------------------------------
# MikrotikHostDeviceTracker skips
# ---------------------------------------------------------------------------


class TestSkipHostTracker:
    def test_skip_when_host_tracking_disabled(self):
        entry = _make_config_entry(**{CONF_TRACK_HOSTS: False})
        desc = _make_desc(func="MikrotikHostDeviceTracker")
        data = {"host1": {}}
        assert _skip_sensor(entry, desc, data, "host1") is True

    def test_no_skip_when_host_tracking_enabled(self):
        entry = _make_config_entry(**{CONF_TRACK_HOSTS: True})
        desc = _make_desc(func="MikrotikHostDeviceTracker")
        data = {"host1": {}}
        assert _skip_sensor(entry, desc, data, "host1") is False

    def test_skip_container_port_host(self):
        """A container endpoint is not a client, so it gets no tracker.

        The client counters have excluded these since issue #6. The tracker
        never applied the same rule, so every container kept a device tracker
        that reported home permanently.
        """
        entry = _make_config_entry(**{CONF_TRACK_HOSTS: True})
        desc = _make_desc(func="MikrotikHostDeviceTracker")
        data = {"host1": {"container-port": True}}
        assert _skip_sensor(entry, desc, data, "host1") is True

    def test_no_skip_for_a_host_that_is_not_on_a_container_port(self):
        entry = _make_config_entry(**{CONF_TRACK_HOSTS: True})
        desc = _make_desc(func="MikrotikHostDeviceTracker")
        data = {"host1": {"container-port": False}}
        assert _skip_sensor(entry, desc, data, "host1") is False

    def test_container_flag_does_not_affect_other_entities(self):
        """Only the tracker is suppressed, nothing else keys off the flag."""
        entry = _make_config_entry(**{CONF_TRACK_HOSTS: True})
        desc = _make_desc(func="MikrotikSensor", data_path="host", data_attribute="address")
        data = {"host1": {"container-port": True}}
        assert _skip_sensor(entry, desc, data, "host1") is False


# ---------------------------------------------------------------------------
# Default — no skip
# ---------------------------------------------------------------------------


class TestNoSkip:
    def test_unknown_func_not_skipped(self):
        entry = _make_config_entry()
        desc = _make_desc(func="SomeOtherSensor", data_path="something")
        data = {"uid1": {"type": "ether"}}
        assert _skip_sensor(entry, desc, data, "uid1") is False


# ---------------------------------------------------------------------------
# PoE selector skips
# ---------------------------------------------------------------------------


class TestSkipNonPoePort:
    """Only a port that can supply power gets a PoE selector."""

    def test_skip_port_without_poe(self):
        entry = _make_config_entry(**{CONF_SENSOR_INTERFACES: True})
        desc = _make_desc(func="MikrotikPoeSelect")
        data = {"ether2": {"type": "ether", "poe-out": "N/A"}}
        assert _skip_sensor(entry, desc, data, "ether2") is True

    def test_skip_port_where_the_field_is_absent(self):
        entry = _make_config_entry(**{CONF_SENSOR_INTERFACES: True})
        desc = _make_desc(func="MikrotikPoeSelect")
        data = {"bridge1": {"type": "bridge"}}
        assert _skip_sensor(entry, desc, data, "bridge1") is True

    def test_no_skip_for_a_poe_capable_port(self):
        entry = _make_config_entry(**{CONF_SENSOR_INTERFACES: True})
        desc = _make_desc(func="MikrotikPoeSelect")
        data = {"ether1": {"type": "ether", "poe-out": "off"}}
        assert _skip_sensor(entry, desc, data, "ether1") is False

    def test_poe_selector_follows_the_interface_option(self):
        """It is an interface entity, so it goes when interfaces go."""
        entry = _make_config_entry(**{CONF_SENSOR_INTERFACES: False})
        desc = _make_desc(func="MikrotikPoeSelect")
        data = {"ether1": {"type": "ether", "poe-out": "off"}}
        assert _skip_sensor(entry, desc, data, "ether1") is True

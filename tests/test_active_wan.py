"""The Active WAN sensor: which interface the active default route of the main table leaves through."""

from unittest.mock import MagicMock, patch

from homeassistant.const import CONF_HOST, CONF_NAME, CONF_PASSWORD, CONF_PORT, CONF_SSL, CONF_USERNAME, CONF_VERIFY_SSL
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.mikrotik_extended.const import DOMAIN
from custom_components.mikrotik_extended.coordinator import _route_interfaces, active_wan


def _route(gateway, hop, distance, active=True, table="main", dst="0.0.0.0/0", dynamic=False, present=True):
    return {"dst-address": dst, "routing-table": table, "gateway": gateway, "immediate-gw": hop, "distance": distance, "active": active, "dynamic": dynamic, "present": present}


# A main connection over PPPoE and a failover over a 5G modem, as in the request.
PPPOE = _route("pppoe-out1", "pppoe-out1", 1, dynamic=True)
MODEM = _route("10.184.226.153", "10.184.226.153%lte1", 10, active=False, dynamic=True)


class TestActiveWan:
    def test_main_connection_carries_the_traffic(self):
        wan = active_wan({"a": PPPOE, "b": MODEM})
        assert wan["interface"] == "pppoe-out1"
        assert wan["gateway"] == "pppoe-out1" and wan["distance"] == 1 and wan["dynamic"] is True
        assert wan["active-routes"] == 1

    def test_failover_took_over(self):
        wan = active_wan({"a": {**PPPOE, "active": False}, "b": {**MODEM, "active": True}})
        assert wan["interface"] == "lte1"
        assert wan["gateway"] == "10.184.226.153" and wan["immediate-gw"] == "10.184.226.153%lte1" and wan["distance"] == 10

    def test_main_link_gone_from_the_table_and_failover_active(self):
        """A PPPoE route leaves the table when the link drops; the kept row is not present."""
        wan = active_wan({"a": {**PPPOE, "active": False, "present": False}, "b": {**MODEM, "active": True}})
        assert wan["interface"] == "lte1"

    def test_no_way_out_reads_none(self):
        wan = active_wan({"a": {**PPPOE, "active": False}, "b": MODEM})
        assert wan["interface"] == "none"
        assert wan["gateway"] is None and wan["active-routes"] == 0
        assert active_wan({})["interface"] == "none"

    def test_not_read_yet_is_unknown_not_none(self):
        assert active_wan(None)["interface"] == "unknown"

    def test_other_tables_and_other_destinations_do_not_count(self):
        routes = {
            "guest": _route("10.0.0.9", "10.0.0.9%wg-guest", 1, table="guest"),
            "lan": _route("10.0.0.2", "10.0.0.2%ether5", 1, dst="10.20.0.0/16"),
        }
        assert active_wan(routes)["interface"] == "none"
        assert active_wan({**routes, "wan": PPPOE})["interface"] == "pppoe-out1"

    def test_a_stale_row_that_still_says_active_is_ignored(self):
        assert active_wan({"a": {**PPPOE, "present": False}})["interface"] == "none"

    def test_two_active_default_routes_are_listed_preferred_first(self):
        first = _route("192.0.2.1", "192.0.2.1%ether1", 1)
        second = _route("198.51.100.1", "198.51.100.1%ether2", 2)
        for routes in ({"a": first, "b": second}, {"b": second, "a": first}):
            wan = active_wan(routes)
            assert wan["interface"] == "ether1, ether2"
            assert wan["active-routes"] == 2 and wan["distance"] == 1

    def test_one_ecmp_route_with_two_hops(self):
        ecmp = _route("192.0.2.1,198.51.100.1", "192.0.2.1%ether1,198.51.100.1%ether2", 1)
        assert active_wan({"a": ecmp})["interface"] == "ether1, ether2"

    def test_same_interface_is_named_once(self):
        a = _route("192.0.2.1", "192.0.2.1%ether1", 1)
        b = _route("192.0.2.2", "192.0.2.2%ether1", 1)
        assert active_wan({"a": a, "b": b})["interface"] == "ether1"

    def test_no_immediate_gateway_falls_back_to_the_gateway(self):
        """RouterOS 6 has no immediate-gw; the gateway is the best there is."""
        assert active_wan({"a": _route("ether1-wan", "", 1)})["interface"] == "ether1-wan"
        assert active_wan({"a": _route("192.0.2.1", None, 1)})["interface"] == "192.0.2.1"


def test_route_interfaces():
    assert _route_interfaces({"immediate-gw": "192.0.2.1%ether1"}) == ["ether1"]
    assert _route_interfaces({"immediate-gw": "pppoe-out1"}) == ["pppoe-out1"]
    assert _route_interfaces({"immediate-gw": "192.0.2.1%ether1, 198.51.100.1%ether2"}) == ["ether1", "ether2"]
    assert _route_interfaces({"immediate-gw": ""}) == [] and _route_interfaces({}) == [] and _route_interfaces({"immediate-gw": "unknown"}) == []


def _coordinator(hass, options=None):
    from custom_components.mikrotik_extended.coordinator import MikrotikCoordinator

    entry = MockConfigEntry(
        domain=DOMAIN,
        data={CONF_HOST: "192.168.88.1", CONF_USERNAME: "admin", CONF_PASSWORD: "x", CONF_PORT: 0, CONF_SSL: False, CONF_VERIFY_SSL: False, CONF_NAME: "Mikrotik"},
        options={"sensor_routes": True} if options is None else options,
        unique_id="192.168.88.1",
    )
    entry.add_to_hass(hass)
    with patch("custom_components.mikrotik_extended.coordinator.MikrotikAPI") as mock_api:
        mock_api.return_value = MagicMock()
        coordinator = MikrotikCoordinator(hass, entry)
    coordinator.api = mock_api.return_value
    coordinator.config_entry = entry
    return coordinator


WAN_ROW = {".id": "*1", "dst-address": "0.0.0.0/0", "gateway": "pppoe-out1", "immediate-gw": "pppoe-out1", "routing-table": "main", "distance": 1, "dynamic": True, "active": True}
LTE_ROW = {".id": "*2", "dst-address": "0.0.0.0/0", "gateway": "10.184.226.153", "immediate-gw": "10.184.226.153%lte1", "routing-table": "main", "distance": 10, "dynamic": True}


class TestActiveWanInTheCoordinator:
    def test_follows_the_routing_table(self, hass):
        coord = _coordinator(hass)
        coord.api.query_where.return_value = [dict(WAN_ROW), dict(LTE_ROW)]
        coord.get_route()
        assert coord.ds["active_wan"]["interface"] == "pppoe-out1"

        # The PPPoE link drops: its route leaves the table, the modem's becomes active.
        coord.api.query_where.return_value = [{**LTE_ROW, "active": True}]
        coord.get_route()
        assert coord.ds["active_wan"]["interface"] == "lte1"

        coord.api.query_where.return_value = [dict(LTE_ROW)]
        coord.get_route()
        assert coord.ds["active_wan"]["interface"] == "none"

    def test_a_failed_read_keeps_the_last_answer(self, hass):
        coord = _coordinator(hass)
        coord.api.query_where.return_value = [dict(WAN_ROW)]
        coord.get_route()
        coord.api.query_where.return_value = None
        coord.get_route()
        assert coord.ds["active_wan"]["interface"] == "pppoe-out1"

    def test_never_read_is_unknown(self, hass):
        coord = _coordinator(hass)
        coord.api.query_where.return_value = None
        coord.get_route()
        assert coord.ds["active_wan"]["interface"] == "unknown"

    def test_nothing_without_the_route_option(self, hass):
        """The store stays empty, so no entity is created."""
        assert _coordinator(hass, options={"scan_interval": 30}).ds["active_wan"] == {}


class TestActiveWanEntity:
    def _sensor(self, hass, wan):
        from custom_components.mikrotik_extended.sensor import MikrotikSensor
        from custom_components.mikrotik_extended.sensor_types import SENSOR_TYPES

        (desc,) = [d for d in SENSOR_TYPES if d.key == "active_wan"]
        entry = MockConfigEntry(domain=DOMAIN, data={CONF_HOST: "192.168.88.1", CONF_NAME: "Mikrotik"}, options={"sensor_routes": True}, unique_id="192.168.88.1")
        entry.add_to_hass(hass)
        coord = MagicMock()
        coord.config_entry = entry
        coord.hass = hass
        coord.data = {"active_wan": wan}
        return MikrotikSensor(coord, desc), desc

    def test_state_name_and_attributes(self, hass):
        sensor, desc = self._sensor(hass, active_wan({"a": PPPOE, "b": MODEM}))
        assert sensor.native_value == "pppoe-out1"
        assert sensor.custom_name == "Active WAN"
        assert sensor.unique_id.endswith("-active_wan")
        attrs = sensor.extra_state_attributes
        assert attrs["gateway"] == "pppoe-out1" and attrs["distance"] == 1 and attrs["active_routes"] == 1
        assert desc.ha_group == "Routes", "on the same device as the route sensors"
        assert not desc.data_reference, "one sensor per router, not one per route"

    def test_none_is_a_state_of_its_own(self, hass):
        sensor, _ = self._sensor(hass, active_wan({}))
        assert sensor.native_value == "none"

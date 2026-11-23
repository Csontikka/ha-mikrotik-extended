"""What happens to an entity when its object is deleted on the router.

A row missing from a read is kept for three reads before it leaves the store.
The entity is unavailable, and a switch writes nothing, from the first of
those reads; when the row leaves the store the entity leaves Home Assistant,
registry entry and empty device included. A read that fails says nothing
about the rows and changes nothing.
"""

from unittest.mock import MagicMock

import pytest
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import entity_platform
from homeassistant.helpers import entity_registry as er

from custom_components.mikrotik_extended.const import DOMAIN
from custom_components.mikrotik_extended.entity import REBIND_DATA_PATHS, ROW_BOUND_DATA_PATHS, MikrotikEntity
from custom_components.mikrotik_extended.switch import MikrotikFilterSwitch, MikrotikKidcontrolPauseSwitch
from custom_components.mikrotik_extended.switch_types import SENSOR_TYPES

from .lifecycle_harness import start

FILTER = "/ip/firewall/filter"
TELNET = {".id": "*1", "chain": "input", "action": "drop", "protocol": "tcp", "dst-port": "23", "comment": "drop telnet", "disabled": False}
LAN = {".id": "*2", "chain": "forward", "action": "accept", "comment": "allow lan", "disabled": False}

# One menu per store that takes part: the path, two rows, and the domains
# that hold an entity for every row.
QUEUE_ROW = {"target": "192.168.88.10/32", "max-limit": "0/0", "rate": "0/0", "limit-at": "0/0", "burst-limit": "0/0", "burst-threshold": "0/0", "burst-time": "0s/0s", "disabled": False}
STORES = {
    "nat": (
        "/ip/firewall/nat",
        [
            {".id": "*1", "chain": "dstnat", "action": "dst-nat", "protocol": "tcp", "dst-port": "80", "to-addresses": "192.168.88.2", "comment": "web", "disabled": False},
            {".id": "*2", "chain": "dstnat", "action": "dst-nat", "protocol": "tcp", "dst-port": "22", "to-addresses": "192.168.88.3", "comment": "ssh", "disabled": False},
        ],
        ("switch",),
    ),
    "mangle": (
        "/ip/firewall/mangle",
        [
            {".id": "*1", "chain": "prerouting", "action": "mark-routing", "comment": "wan2", "disabled": False},
            {".id": "*2", "chain": "forward", "action": "change-mss", "comment": "mss", "disabled": False},
        ],
        ("switch",),
    ),
    "routing_rules": (
        "/routing/rule",
        [
            {".id": "*1", "action": "lookup", "src-address": "10.0.0.0/24", "comment": "guests", "disabled": False},
            {".id": "*2", "action": "lookup", "src-address": "10.0.1.0/24", "comment": "cameras", "disabled": False},
        ],
        ("switch",),
    ),
    "filter": (FILTER, [TELNET, LAN], ("switch",)),
    "raw": (
        "/ip/firewall/raw",
        [
            {".id": "*1", "chain": "prerouting", "action": "drop", "comment": "bogons", "disabled": False},
            {".id": "*2", "chain": "prerouting", "action": "notrack", "comment": "fast", "disabled": False},
        ],
        ("switch",),
    ),
    "ipv6_filter": (
        "/ipv6/firewall/filter",
        [
            {".id": "*1", "chain": "input", "action": "drop", "protocol": "tcp", "dst-port": "23", "comment": "drop telnet", "disabled": False},
            {".id": "*2", "chain": "forward", "action": "accept", "comment": "allow lan", "disabled": False},
        ],
        ("switch",),
    ),
    "ipv6_raw": (
        "/ipv6/firewall/raw",
        [
            {".id": "*1", "chain": "prerouting", "action": "drop", "comment": "bogons", "disabled": False},
            {".id": "*2", "chain": "prerouting", "action": "notrack", "comment": "fast", "disabled": False},
        ],
        ("switch",),
    ),
    "ipv6_nat": (
        "/ipv6/firewall/nat",
        [
            {".id": "*1", "chain": "srcnat", "action": "masquerade", "comment": "out", "disabled": False},
            {".id": "*2", "chain": "dstnat", "action": "dst-nat", "to-address": "fd00::2", "comment": "web", "disabled": False},
        ],
        ("switch",),
    ),
    "ipv6_mangle": (
        "/ipv6/firewall/mangle",
        [
            {".id": "*1", "chain": "prerouting", "action": "mark-routing", "new-routing-mark": "wan2", "comment": "wan2", "disabled": False},
            {".id": "*2", "chain": "forward", "action": "change-mss", "comment": "mss", "disabled": False},
        ],
        ("switch",),
    ),
    "queue": (
        "/queue/simple",
        [{".id": "*1", "name": "kids", **QUEUE_ROW}, {".id": "*2", "name": "guests", **QUEUE_ROW}],
        ("switch",),
    ),
    "netwatch": (
        "/tool/netwatch",
        [
            {".id": "*1", "host": "1.1.1.1", "type": "icmp", "status": "up", "comment": "cloudflare", "disabled": False},
            {".id": "*2", "host": "8.8.8.8", "type": "icmp", "status": "up", "comment": "google", "disabled": False},
        ],
        ("binary_sensor",),
    ),
    "wireguard_peers": (
        "/interface/wireguard/peers",
        [
            {".id": "*1", "public-key": "AAAAkey1=", "interface": "wg0", "name": "phone", "disabled": False, "last-handshake": "30s"},
            {".id": "*2", "public-key": "BBBBkey2=", "interface": "wg0", "name": "laptop", "disabled": False, "last-handshake": "30s"},
        ],
        ("switch", "binary_sensor", "sensor"),
    ),
    "interface_list_member": (
        "/interface/list/member",
        [
            {".id": "*1", "list": "LAN", "interface": "bridge", "dynamic": False, "disabled": False},
            {".id": "*2", "list": "WAN", "interface": "ether1", "dynamic": False, "disabled": False},
        ],
        ("switch",),
    ),
}


def _entity(hass, entity_id):
    """The entity object behind an entity id."""
    for platform in entity_platform.async_get_platforms(hass, DOMAIN):
        if entity_id in platform.entities:
            return platform.entities[entity_id]
    return None


async def _filter_router(hass):
    router = await start(hass, {FILTER: [TELNET, LAN]})
    return router, router.entity_id("switch", "filter", "drop_telnet"), router.entity_id("switch", "filter", "allow_lan")


class TestWhichStores:
    def test_the_stores_whose_entities_leave_with_their_row(self):
        assert set(STORES) == ROW_BOUND_DATA_PATHS
        assert REBIND_DATA_PATHS < ROW_BOUND_DATA_PATHS

    @pytest.mark.parametrize("path", ["interface", "host", "route", "lte", "ip_address", "containers", "kid-control", "ppp_secret", "script", "environment"])
    def test_other_stores_are_not_touched(self, path):
        assert path not in ROW_BOUND_DATA_PATHS

    def test_an_entity_of_another_store_keeps_its_last_state(self, hass):
        """A kid control profile missing from the store: nothing is written, removed or refused."""
        description = next(d for d in SENSOR_TYPES if d.key == "kidcontrol_paused")
        coordinator = MagicMock()
        coordinator.config_entry.data = {"name": "Mikrotik"}
        coordinator.data = {"kid-control": {"kids": {"name": "kids", "paused": False, "comment": ""}}}
        switch = MikrotikKidcontrolPauseSwitch(coordinator, description, "kids")
        switch.hass = hass
        switch.async_write_ha_state = MagicMock()
        switch._async_remove_gone = MagicMock()

        coordinator.data = {"kid-control": {}}
        switch._handle_coordinator_update()

        assert switch.available
        assert switch._row_is_gone() is False
        switch.async_write_ha_state.assert_not_called()
        switch._async_remove_gone.assert_not_called()
        switch._refuse_when_gone("kids")


class TestEveryStore:
    @pytest.mark.parametrize("store", sorted(STORES))
    async def test_an_entity_goes_unavailable_and_is_then_removed(self, hass, store):
        path, rows, domains = STORES[store]
        router = await start(hass, {path: list(rows)}, platforms=domains)
        before = {domain: router.entities(domain) for domain in domains}
        assert all(len(found) >= 2 for found in before.values()), before
        states = {entity_id: router.state(entity_id) for found in before.values() for entity_id in found.values()}
        assert "unavailable" not in states.values()

        router.menus[path] = rows[1:]
        await router.poll()

        # First read without the row: every entity of that row is unavailable, all are still registered.
        after = {domain: router.entities(domain) for domain in domains}
        assert after == before
        unavailable = {entity_id for entity_id in states if router.state(entity_id) == "unavailable"}
        assert unavailable
        assert len(unavailable) * 2 == len(states)

        await router.poll()
        assert {domain: router.entities(domain) for domain in domains} == before

        await router.poll()
        gone = {domain: router.entities(domain) for domain in domains}
        for domain in domains:
            assert set(before[domain].values()) - set(gone[domain].values()) == unavailable & set(before[domain].values())
        for entity_id in unavailable:
            assert router.state(entity_id) is None
            assert router.registry.async_get(entity_id) is None
        # The other row is untouched.
        for entity_id in set(states) - unavailable:
            assert router.state(entity_id) == states[entity_id]


class TestGoneFromTheRouter:
    async def test_unavailable_from_the_first_read_without_the_rule(self, hass):
        router, telnet, lan = await _filter_router(hass)
        assert router.state(telnet) == "on"

        router.menus[FILTER] = [LAN]
        await router.poll()

        assert router.state(telnet) == "unavailable"
        assert router.state(lan) == "on"
        assert router.registry.async_get(telnet) is not None

    async def test_a_switch_writes_nothing_once_its_rule_is_gone(self, hass):
        """The RouterOS id it last saw may be another rule's by now."""
        router, telnet, _ = await _filter_router(hass)
        switch = _entity(hass, telnet)
        router.menus[FILTER] = [LAN]
        await router.poll()

        for call in (switch.async_turn_on, switch.async_turn_off):
            with pytest.raises(HomeAssistantError) as error:
                await call()
            assert error.value.translation_key == "write_failed"
            assert error.value.translation_placeholders["host"] == "192.168.88.1"
        assert router.writes() == []

    async def test_removed_when_the_row_leaves_the_store(self, hass):
        router, telnet, lan = await _filter_router(hass)
        router.menus[FILTER] = [LAN]

        await router.poll(2)
        assert router.registry.async_get(telnet) is not None
        assert "*1" in router.coordinator.ds["filter"]

        await router.poll()
        assert "*1" not in router.coordinator.ds["filter"]
        assert router.registry.async_get(telnet) is None
        assert router.state(telnet) is None
        assert _entity(hass, telnet) is None
        assert router.state(lan) == "on"

    async def test_a_failed_read_neither_hides_nor_removes(self, hass):
        router, telnet, _ = await _filter_router(hass)
        router.menus[FILTER] = None

        await router.poll(6)

        assert router.state(telnet) == "on"
        assert router.registry.async_get(telnet) is not None

    async def test_failed_reads_do_not_count_towards_the_removal(self, hass):
        router, telnet, _ = await _filter_router(hass)
        router.menus[FILTER] = [LAN]
        await router.poll(2)

        router.menus[FILTER] = None
        await router.poll(6)

        assert router.state(telnet) == "unavailable"
        assert router.registry.async_get(telnet) is not None

    async def test_a_rule_that_comes_back_in_time_revives_its_entity(self, hass):
        router, telnet, _ = await _filter_router(hass)
        router.menus[FILTER] = [LAN]
        await router.poll(2)
        assert router.state(telnet) == "unavailable"

        router.menus[FILTER] = [TELNET, LAN]
        await router.poll()

        assert router.state(telnet) == "on"
        assert router.entity_id("switch", "filter", "drop_telnet") == telnet
        await _entity(hass, telnet).async_turn_off()
        assert router.writes() == [(FILTER, ".id", "*1", "disabled", True)]

    async def test_a_recreated_rule_keeps_its_entity(self, hass):
        """Same comment and contents under a new RouterOS id: followed at once, never removed."""
        router, telnet, _ = await _filter_router(hass)
        router.menus[FILTER] = [LAN, {**TELNET, ".id": "*9"}]

        await router.poll()
        assert router.state(telnet) == "on"

        await router.poll(4)
        assert router.state(telnet) == "on"
        assert router.entities() == {"filter-drop_telnet": telnet, "filter-allow_lan": router.entity_id("switch", "filter", "allow_lan")}
        await _entity(hass, telnet).async_turn_off()
        assert router.writes() == [(FILTER, ".id", "*9", "disabled", True)]

    async def test_a_rule_that_returns_later_gets_an_entity_again(self, hass):
        router, _, _ = await _filter_router(hass)
        router.menus[FILTER] = [LAN]
        await router.poll(3)
        assert router.entity_id("switch", "filter", "drop_telnet") is None

        router.menus[FILTER] = [LAN, {**TELNET, ".id": "*7"}]
        await router.poll()

        again = router.entity_id("switch", "filter", "drop_telnet")
        assert again is not None
        assert router.state(again) == "on"
        await _entity(hass, again).async_turn_off()
        assert router.writes() == [(FILTER, ".id", "*7", "disabled", True)]

    async def test_a_peer_added_again_with_its_key_keeps_its_entities(self, hass):
        """A WireGuard peer is not followed to a new id at once, its key is not unique across interfaces.

        Once the old row has left, the new one is the only row the unique id
        can mean, and it is given the same registry entries: what the user
        named them stays.
        """
        path, rows, domains = STORES["wireguard_peers"]
        router = await start(hass, {path: list(rows)}, platforms=domains)
        before = {domain: router.registry_ids(domain) for domain in domains}
        phone = next(entity_id for entity_id in before["switch"] if _entity(hass, entity_id)._uid == "*1")
        router.registry.async_update_entity(phone, name="My phone")

        router.menus[path] = [{**rows[0], ".id": "*8"}, rows[1]]
        await router.poll()
        assert router.state(phone) == "unavailable"
        await router.poll(2)
        assert {domain: router.registry_ids(domain) for domain in domains} == before
        await router.poll()

        assert {domain: router.registry_ids(domain) for domain in domains} == before
        assert router.registry.async_get(phone).name == "My phone"
        assert router.state(phone) == "on"
        assert _entity(hass, phone)._uid == "*8"
        await _entity(hass, phone).async_turn_off()
        assert router.writes() == [(path, ".id", "*8", "disabled", True)]

    async def test_a_rule_recreated_with_other_contents_keeps_its_entity(self, hass):
        """Same comment, another port: not followed while the old row may still be there, then taken over."""
        router, telnet, _ = await _filter_router(hass)
        before = router.registry_ids()
        router.registry.async_update_entity(telnet, name="My telnet", icon="mdi:shield")

        router.menus[FILTER] = [LAN, {**TELNET, ".id": "*9", "dst-port": "2323"}]
        await router.poll()
        assert router.state(telnet) == "unavailable"
        assert _entity(hass, telnet)._uid == "*1"
        with pytest.raises(HomeAssistantError):
            await _entity(hass, telnet).async_turn_off()
        await router.poll(3)

        assert router.registry_ids() == before
        entry = router.registry.async_get(telnet)
        assert (entry.name, entry.icon) == ("My telnet", "mdi:shield")
        assert router.state(telnet) == "on"
        await _entity(hass, telnet).async_turn_off()
        assert router.writes() == [(FILTER, ".id", "*9", "disabled", True)]

    async def test_a_kept_registry_entry_goes_when_the_other_row_leaves_too(self, hass):
        router, telnet, _ = await _filter_router(hass)
        router.menus[FILTER] = [LAN, {**TELNET, ".id": "*9", "dst-port": "2323"}]
        await router.poll(3)
        assert router.registry.async_get(telnet) is not None

        router.menus[FILTER] = [LAN]
        await router.poll(4)

        assert router.registry.async_get(telnet) is None


class TestRegistryAndDevice:
    async def test_the_device_goes_with_its_last_entity(self, hass):
        router, telnet, lan = await _filter_router(hass)
        device_id = router.registry.async_get(telnet).device_id
        assert router.registry.async_get(lan).device_id == device_id

        router.menus[FILTER] = [LAN]
        await router.poll(3)
        assert router.devices.async_get(device_id) is not None

        router.menus[FILTER] = []
        await router.poll(3)
        assert router.registry.async_get(lan) is None
        assert router.devices.async_get(device_id) is None

    async def test_a_device_that_other_entities_use_stays(self, hass):
        """A WireGuard peer has entities on three platforms, all on one device."""
        path, rows, domains = STORES["wireguard_peers"]
        router = await start(hass, {path: list(rows)}, platforms=domains)
        device_ids = {entry.device_id for entry in er.async_entries_for_config_entry(router.registry, router.entry.entry_id)}
        assert len(device_ids) == 1

        router.menus[path] = rows[1:]
        await router.poll(3)

        assert router.devices.async_get(device_ids.pop()) is not None

    async def test_an_entity_the_user_disabled_is_left_alone(self, hass):
        router, telnet, _ = await _filter_router(hass)
        router.registry.async_update_entity(telnet, disabled_by=er.RegistryEntryDisabler.USER)
        await hass.async_block_till_done()
        assert _entity(hass, telnet) is None

        router.menus[FILTER] = [LAN]
        await router.poll(5)

        entry = router.registry.async_get(telnet)
        assert entry is not None
        assert entry.disabled_by is er.RegistryEntryDisabler.USER

    async def test_a_renamed_entity_is_removed_like_any_other(self, hass):
        """What the user changed in Home Assistant does not keep a deleted rule alive."""
        router, telnet, _ = await _filter_router(hass)
        router.registry.async_update_entity(telnet, name="Telnet block", new_entity_id="switch.telnet_block")
        await hass.async_block_till_done()

        router.menus[FILTER] = [LAN]
        await router.poll(3)

        assert router.registry.async_get("switch.telnet_block") is None
        assert router.state("switch.telnet_block") is None


class TestEntityNotInHomeAssistant:
    def test_an_entity_that_was_never_added_only_reads_unavailable(self, hass):
        """Nothing to remove, and nothing that raises."""
        description = next(d for d in SENSOR_TYPES if d.key == "filter")
        coordinator = MagicMock()
        coordinator.config_entry.data = {"name": "Mikrotik"}
        coordinator._get_stale_counters = MagicMock(return_value={})
        coordinator.data = {"filter": {"*1": {".id": "*1", "uniq-id": "drop telnet", "legacy-uniq-id": "sig", "name": "x", "comment": "drop telnet", "enabled": True}}}
        switch = MikrotikFilterSwitch(coordinator, description, "*1")
        switch.hass = hass
        switch.async_write_ha_state = MagicMock()

        coordinator.data = {"filter": {}}
        switch._handle_coordinator_update()

        assert isinstance(switch, MikrotikEntity)
        assert switch.available is False
        switch.async_write_ha_state.assert_called_once()

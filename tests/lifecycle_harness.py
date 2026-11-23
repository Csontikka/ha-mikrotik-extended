"""A running integration for the tests that need one.

The coordinator, the update cycle, the platforms and the registries are the
real ones. The router is a dictionary of menus: a test changes the rows, runs
a poll and looks at what Home Assistant holds afterwards. A menu set to None
is a read that failed.

Only the menus whose entities come and go with their rows are read; every
other read of the cycle is stubbed out.
"""

from __future__ import annotations

import copy
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import CONF_HOST, CONF_NAME, CONF_PASSWORD, CONF_PORT, CONF_SSL, CONF_USERNAME, CONF_VERIFY_SSL
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.setup import async_setup_component
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.mikrotik_extended.const import DOMAIN
from custom_components.mikrotik_extended.coordinator import MikrotikCoordinator

ENTRY_ID = "entry1"
ENTRY_DATA = {
    CONF_HOST: "192.168.88.1",
    CONF_USERNAME: "admin",
    CONF_PASSWORD: "x",
    CONF_PORT: 0,
    CONF_SSL: False,
    CONF_VERIFY_SSL: False,
    CONF_NAME: "Mikrotik",
}
OPTIONS = {
    "sensor_nat": True,
    "sensor_mangle": True,
    "sensor_routing_rules": True,
    "sensor_filter": True,
    "sensor_raw": True,
    "sensor_ipv6_firewall": True,
    "sensor_interface_lists": True,
    "sensor_netwatch_tracker": True,
    "sensor_wireguard": True,
    "sensor_simple_queues": True,
}
READ = (
    "get_nat",
    "get_mangle",
    "get_routing_rules",
    "get_filter",
    "get_raw",
    "get_ipv6_firewall",
    "get_interface_list_members",
    "get_netwatch",
    "get_wireguard_peers",
    "get_queue",
)


class Router:
    """The integration set up against a router made of lists."""

    def __init__(self, hass, entry, coordinator, menus) -> None:
        self.hass = hass
        self.entry = entry
        self.coordinator = coordinator
        self.menus = menus
        self.registry = er.async_get(hass)
        self.devices = dr.async_get(hass)

    async def poll(self, times: int = 1) -> None:
        """Run whole update cycles, each followed by everything they set off."""
        for _ in range(times):
            await self.coordinator.async_refresh()
            await self.hass.async_block_till_done()

    def unique_id(self, key: str, reference: str) -> str:
        return f"{ENTRY_ID}-{key}-{reference}"

    def entity_id(self, domain: str, key: str, reference: str) -> str | None:
        """The entity registered for a row, by the slug of its reference."""
        return self.registry.async_get_entity_id(domain, DOMAIN, self.unique_id(key, reference))

    def entities(self, domain: str = "switch") -> dict[str, str]:
        """Every registered entity of a domain: unique id (without the entry) to entity id."""
        prefix = f"{ENTRY_ID}-"
        return {entry.unique_id.removeprefix(prefix): entry.entity_id for entry in er.async_entries_for_config_entry(self.registry, ENTRY_ID) if entry.domain == domain}

    def state(self, entity_id: str) -> str | None:
        state = self.hass.states.get(entity_id)
        return state.state if state else None

    async def turn(self, entity_id: str, on: bool) -> None:
        await self.hass.services.async_call("switch", "turn_on" if on else "turn_off", {"entity_id": entity_id}, blocking=True)

    def writes(self) -> list[tuple]:
        """What was sent to the router: (menu, key, value, parameter, new value)."""
        return [call.args for call in self.coordinator.api.set_value.call_args_list]


async def start(hass, menus: dict, platforms=("switch",), options: dict | None = None) -> Router:
    """Set the integration up, run the first poll and load the platforms."""
    # Before the entry exists: loading a platform sets the integration up
    # first, and that would try to set up the entry a second time.
    assert await async_setup_component(hass, DOMAIN, {})
    entry = MockConfigEntry(domain=DOMAIN, data=ENTRY_DATA, options={**OPTIONS, **(options or {})}, unique_id="192.168.88.1", entry_id=ENTRY_ID)
    entry.add_to_hass(hass)
    with patch("custom_components.mikrotik_extended.coordinator.MikrotikAPI") as mock_api:
        mock_api.return_value = MagicMock()
        coordinator = MikrotikCoordinator(hass, entry)
    coordinator.config_entry = entry
    api = coordinator.api = mock_api.return_value
    api.query.side_effect = lambda path, *args, **kwargs: copy.deepcopy(menus.get(path, []))
    api.connected.return_value = True
    api.has_reconnected.return_value = False
    api.error = ""
    api.set_value.return_value = True

    for name in dir(MikrotikCoordinator):
        if name.startswith("get_") and name not in READ:
            setattr(coordinator, name, MagicMock(return_value=None))
    for name in ("sync_kid_control_monitoring_profile", "process_kid_control_devices", "process_interface_client"):
        setattr(coordinator, name, MagicMock(return_value=None))
    coordinator.async_get_host_hass = AsyncMock(return_value=None)
    coordinator.async_process_host = AsyncMock(return_value=None)
    coordinator.last_hwinfo_update = datetime.now()
    coordinator.support_wireguard = True
    coordinator.ds["access"] = ["write", "policy", "reboot", "test"]
    coordinator.ds["resource"] = {"board-name": "CHR", "platform": "MikroTik", "version": "7.20"}
    coordinator.ds["routerboard"] = {"serial-number": "SN1", "model": "CHR"}

    await coordinator.async_refresh()
    coordinator.register_core_device()
    entry.runtime_data = SimpleNamespace(data_coordinator=coordinator, tracker_coordinator=MagicMock(async_request_refresh=AsyncMock()))
    entry.mock_state(hass, ConfigEntryState.LOADED)
    await hass.config_entries.async_forward_entry_setups(entry, list(platforms))
    await hass.async_block_till_done()
    return Router(hass, entry, coordinator, menus)

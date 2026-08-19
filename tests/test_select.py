"""Tests for the select platform."""

from unittest.mock import AsyncMock, MagicMock, patch

from homeassistant.const import (
    CONF_HOST,
    CONF_NAME,
    CONF_PASSWORD,
    CONF_PORT,
    CONF_SSL,
    CONF_USERNAME,
    CONF_VERIFY_SSL,
)
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.mikrotik_extended.const import DOMAIN
from custom_components.mikrotik_extended.select import (
    MikrotikPoeSelect,
    async_setup_entry,
)
from custom_components.mikrotik_extended.select_types import POE_OPTIONS, SENSOR_TYPES

ENTRY_DATA = {
    CONF_HOST: "192.168.88.1",
    CONF_USERNAME: "admin",
    CONF_PASSWORD: "test",
    CONF_PORT: 8728,
    CONF_SSL: False,
    CONF_VERIFY_SSL: False,
    CONF_NAME: "Mikrotik",
}


def _make_description(**extra):
    desc = MagicMock()
    desc.key = "poe_out"
    desc.func = "MikrotikPoeSelect"
    desc.data_path = "interface"
    desc.data_attribute = "poe-out"
    desc.data_reference = "default-name"
    desc.data_uid = "name"
    desc.data_name = "default-name"
    desc.data_name_comment = False
    desc.data_attributes_list = []
    desc.data_set_path = "/interface/ethernet/poe"
    desc.data_set_parameter = "poe-out"
    desc.data_set_reference = "name"
    desc.ha_group = "data__default-name"
    desc.ha_connection = None
    desc.ha_connection_value = None
    desc.entity_registry_enabled_default = True
    for k, v in extra.items():
        setattr(desc, k, v)
    return desc


def _make_coordinator(hass, data):
    entry = MockConfigEntry(domain=DOMAIN, data=ENTRY_DATA, options={}, unique_id="192.168.88.1")
    entry.add_to_hass(hass)
    coord = MagicMock()
    coord.config_entry = entry
    coord.data = data
    coord.host = "192.168.88.1"
    coord.async_request_refresh = AsyncMock()
    return coord


def _iface(poe="off"):
    return {"interface": {"ether1": {"default-name": "ether1", "name": "ether1", "poe-out": poe}}, "access": {"write"}}


async def test_async_setup_entry_invokes_add_entities(hass):
    entry = MagicMock()
    with patch("custom_components.mikrotik_extended.select.async_add_entities", new=AsyncMock()) as mock_add:
        await async_setup_entry(hass, entry, MagicMock())
    mock_add.assert_awaited_once()
    _, _, dispatcher = mock_add.await_args.args
    assert set(dispatcher.keys()) == {"MikrotikSelect", "MikrotikPoeSelect"}


def test_the_three_modes_are_offered():
    """RouterOS spells them with a hyphen, Home Assistant needs plain names."""
    desc = next(d for d in SENSOR_TYPES if d.key == "poe_out")
    assert set(desc.options) == {"off", "auto_on", "forced_on"}
    assert POE_OPTIONS == {"off": "off", "auto_on": "auto-on", "forced_on": "forced-on"}


async def test_current_option_maps_the_router_value(hass):
    coord = _make_coordinator(hass, _iface("auto-on"))
    sel = MikrotikPoeSelect(coord, _make_description(), "ether1")
    assert sel.current_option == "auto_on"


async def test_unknown_router_value_reports_nothing(hass):
    """Home Assistant rejects an option it was not told about."""
    coord = _make_coordinator(hass, _iface("N/A"))
    sel = MikrotikPoeSelect(coord, _make_description(), "ether1")
    assert sel.current_option is None


async def test_selecting_a_mode_writes_it_to_the_port(hass):
    coord = _make_coordinator(hass, _iface("off"))
    coord.api.set_value = MagicMock(return_value=True)
    sel = MikrotikPoeSelect(coord, _make_description(), "ether1")
    sel.hass = hass

    await sel.async_select_option("auto_on")

    coord.api.set_value.assert_called_once_with("/interface/ethernet/poe", "name", "ether1", "poe-out", "auto-on")
    coord.async_request_refresh.assert_awaited_once()


async def test_selection_needs_write_access(hass):
    data = _iface("off")
    data["access"] = {"read"}
    coord = _make_coordinator(hass, data)
    coord.api.set_value = MagicMock()
    sel = MikrotikPoeSelect(coord, _make_description(), "ether1")
    sel.hass = hass

    await sel.async_select_option("auto_on")

    coord.api.set_value.assert_not_called()


async def test_an_unknown_option_is_not_sent_to_the_router(hass):
    coord = _make_coordinator(hass, _iface("off"))
    coord.api.set_value = MagicMock()
    sel = MikrotikPoeSelect(coord, _make_description(), "ether1")
    sel.hass = hass

    await sel.async_select_option("turbo")

    coord.api.set_value.assert_not_called()


async def test_a_refused_write_does_not_claim_success(hass):
    coord = _make_coordinator(hass, _iface("off"))
    coord.api.set_value = MagicMock(return_value=False)
    sel = MikrotikPoeSelect(coord, _make_description(), "ether1")
    sel.hass = hass

    await sel.async_select_option("forced_on")

    coord.api.set_value.assert_called_once()
    coord.async_request_refresh.assert_not_awaited()

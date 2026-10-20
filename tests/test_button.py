"""Tests for the button platform."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant.const import (
    CONF_HOST,
    CONF_NAME,
    CONF_PASSWORD,
    CONF_PORT,
    CONF_SSL,
    CONF_USERNAME,
    CONF_VERIFY_SSL,
)
from homeassistant.exceptions import HomeAssistantError
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.mikrotik_extended.button import (
    MikrotikBackupButton,
    MikrotikButton,
    MikrotikPoePowerCycleButton,
    MikrotikRebootButton,
    MikrotikScriptButton,
    async_setup_entry,
)
from custom_components.mikrotik_extended.const import DOMAIN

ENTRY_DATA = {
    CONF_HOST: "192.168.88.1",
    CONF_USERNAME: "admin",
    CONF_PASSWORD: "test",
    CONF_PORT: 0,
    CONF_SSL: False,
    CONF_VERIFY_SSL: False,
    CONF_NAME: "TestRouter",
}


def _make_description(func="MikrotikButton", key="k", name="N", data_path="resource", data_attribute="x", **extra):
    desc = MagicMock()
    desc.func = func
    desc.key = key
    desc.name = name
    desc.data_path = data_path
    desc.data_attribute = data_attribute
    desc.data_reference = None
    desc.data_name = None
    desc.data_name_comment = False
    desc.data_attributes_list = []
    desc.ha_group = None
    desc.ha_connection = None
    desc.ha_connection_value = None
    desc.entity_registry_enabled_default = True
    for k, v in extra.items():
        setattr(desc, k, v)
    return desc


def _make_coordinator(hass, data):
    entry = MockConfigEntry(
        domain=DOMAIN,
        data=ENTRY_DATA,
        options={},
        unique_id="192.168.88.1",
    )
    entry.add_to_hass(hass)

    coord = MagicMock()
    coord.config_entry = entry
    coord.data = data
    coord.hass = hass
    coord.host = "192.168.88.1"
    return coord


async def test_async_setup_entry_invokes_add_entities(hass):
    """async_setup_entry forwards dispatcher dict into async_add_entities."""
    entry = MagicMock()
    add_entities = MagicMock()
    with patch("custom_components.mikrotik_extended.button.async_add_entities", new=AsyncMock()) as mock_add:
        await async_setup_entry(hass, entry, add_entities)
    mock_add.assert_awaited_once()
    _, _, dispatcher = mock_add.await_args.args
    assert set(dispatcher.keys()) == {
        "MikrotikButton",
        "MikrotikPoePowerCycleButton",
        "MikrotikScriptButton",
        "MikrotikRebootButton",
        "MikrotikBackupButton",
    }


async def test_mikrotik_button_press_is_noop(hass):
    """Base MikrotikButton.async_press truly does nothing: no coordinator/API calls."""
    desc = _make_description(func="MikrotikButton")
    coord = _make_coordinator(hass, data={"resource": {"x": "y"}})
    coord.execute = MagicMock()
    coord.api = MagicMock()
    coord.async_refresh = AsyncMock()
    button = MikrotikButton(coord, desc)
    await button.async_update()
    await button.async_press()
    # Base class press must not touch coordinator or API
    coord.execute.assert_not_called()
    coord.api.assert_not_called()
    coord.async_refresh.assert_not_awaited()


async def test_reboot_button_aborts_without_access(hass):
    """MikrotikRebootButton.async_press aborts early if 'reboot' is missing from access."""
    desc = _make_description(func="MikrotikRebootButton")
    coord = _make_coordinator(hass, data={"resource": {"x": "y"}})
    coord.ds = {"access": set()}  # no reboot access
    coord.execute = MagicMock()
    button = MikrotikRebootButton(coord, desc)
    await button.async_press()
    coord.execute.assert_not_called()


async def test_reboot_button_executes_reboot_when_allowed(hass):
    """MikrotikRebootButton.async_press calls the reboot command when permitted."""
    desc = _make_description(func="MikrotikRebootButton")
    coord = _make_coordinator(hass, data={"resource": {"x": "y"}})
    coord.ds = {"access": {"reboot"}}
    coord.execute = MagicMock(return_value=True)
    button = MikrotikRebootButton(coord, desc)
    button.hass = hass
    await button.async_press()
    coord.execute.assert_called_once_with("/system", "reboot", None, None)


async def test_script_button_runs_and_refreshes_on_success(hass):
    """MikrotikScriptButton.async_press runs the script and triggers refreshes."""
    desc = _make_description(
        func="MikrotikScriptButton",
        data_path="script",
        data_name="name",
        data_uid="name",
        data_reference="name",
    )
    script_data = {"myscript": {"name": "myscript", "last-started": "now", "run-count": 1}}
    coord = _make_coordinator(hass, data={"script": script_data})
    coord.api = MagicMock()
    coord.api.run_script = MagicMock(return_value=True)
    coord.async_refresh = AsyncMock()

    tracker = MagicMock()
    tracker.async_request_refresh = AsyncMock()
    coord.config_entry.runtime_data = MagicMock(tracker_coordinator=tracker)

    button = MikrotikScriptButton(coord, desc, uid="myscript")
    button.hass = hass
    await button.async_press()

    coord.api.run_script.assert_called_once_with("myscript")
    coord.async_refresh.assert_awaited_once()
    tracker.async_request_refresh.assert_awaited_once()


async def test_script_button_raises_on_failure(hass):
    """MikrotikScriptButton.async_press raises when run_script returns False, and refreshes nothing."""
    desc = _make_description(
        func="MikrotikScriptButton",
        data_path="script",
        data_name="name",
        data_uid="name",
        data_reference="name",
    )
    script_data = {"myscript": {"name": "myscript"}}
    coord = _make_coordinator(hass, data={"script": script_data})
    coord.api = MagicMock()
    coord.api.run_script = MagicMock(return_value=False)
    coord.api.last_write_failure = "refused"
    coord.async_refresh = AsyncMock()

    tracker = MagicMock()
    tracker.async_request_refresh = AsyncMock()
    coord.config_entry.runtime_data = MagicMock(tracker_coordinator=tracker)

    button = MikrotikScriptButton(coord, desc, uid="myscript")
    button.hass = hass
    with pytest.raises(HomeAssistantError) as err:
        await button.async_press()

    assert err.value.translation_key == "write_refused"
    assert "myscript" in err.value.translation_placeholders["target"]
    coord.async_refresh.assert_not_awaited()
    tracker.async_request_refresh.assert_not_awaited()


async def test_backup_button_aborts_without_write_access(hass):
    """A read only account cannot write a backup, so do not even try.

    Measured on a live router: the save is refused with a permission error
    for an account that may only read.
    """
    desc = _make_description(func="MikrotikBackupButton")
    coord = _make_coordinator(hass, data={"resource": {"x": "y"}})
    coord.ds = {"access": {"read"}}
    coord.execute = MagicMock()
    button = MikrotikBackupButton(coord, desc)
    with pytest.raises(HomeAssistantError) as err:
        await button.async_press()
    assert err.value.translation_key == "no_write_access"
    coord.execute.assert_not_called()


async def test_backup_button_saves_under_a_fixed_name(hass):
    """The name is fixed so repeated presses overwrite one file.

    Without a name RouterOS stamps the date into the file name, so every press
    leaves another copy behind and eventually fills the storage of a small
    device.
    """
    desc = _make_description(func="MikrotikBackupButton")
    coord = _make_coordinator(hass, data={"resource": {"x": "y"}})
    coord.ds = {"access": {"write"}}
    coord.execute = MagicMock(return_value=True)
    button = MikrotikBackupButton(coord, desc)
    button.hass = hass
    await button.async_press()
    coord.execute.assert_called_once_with("/system/backup", "save", None, None, {"name": "homeassistant"})


async def test_backup_button_reports_a_refused_save(hass):
    """A refusal is an error the user sees in the UI, with the router named."""
    desc = _make_description(func="MikrotikBackupButton")
    coord = _make_coordinator(hass, data={"resource": {"x": "y"}})
    coord.ds = {"access": {"write"}}
    coord.execute = MagicMock(return_value=False)
    coord.api.last_write_failure = "refused"
    button = MikrotikBackupButton(coord, desc)
    button.hass = hass
    with pytest.raises(HomeAssistantError) as err:
        await button.async_press()
    coord.execute.assert_called_once()
    assert err.value.translation_key == "write_refused"
    assert err.value.translation_placeholders["host"] == coord.host


def test_backup_button_is_declared():
    """The description has to exist for the entity to be created at all."""
    from custom_components.mikrotik_extended.button_types import SENSOR_TYPES

    keys = {d.key for d in SENSOR_TYPES}
    assert "backup" in keys
    backup = next(d for d in SENSOR_TYPES if d.key == "backup")
    assert backup.func == "MikrotikBackupButton"


# ---------------------------------------------------------------------------
# PoE power cycle
# ---------------------------------------------------------------------------

_PORT = {"name": "ether5", "default-name": "ether5", "poe-out": "auto-on"}


def _poe_button(hass, access, execute_result=True):
    desc = _make_description(func="MikrotikPoePowerCycleButton", key="poe_power_cycle", data_path="interface", data_attribute="poe-out", data_uid="name")
    coord = _make_coordinator(hass, data={"interface": {"ether5": _PORT}})
    coord.ds = {"access": set(access)}
    coord.execute = MagicMock(return_value=execute_result)
    coord.api.last_write_failure = "refused"
    button = MikrotikPoePowerCycleButton(coord, desc)
    button._data = _PORT
    button.hass = hass
    return button, coord


async def test_poe_power_cycle_aborts_without_write_access(hass):
    """Cutting power is a write; a read only account is told so, nothing is sent."""
    button, coord = _poe_button(hass, access={"read"})
    with pytest.raises(HomeAssistantError) as err:
        await button.async_press()
    assert err.value.translation_key == "no_write_access"
    coord.execute.assert_not_called()


async def test_poe_power_cycle_sends_the_command_for_the_port(hass):
    """The PoE menu is addressed by interface name, with RouterOS's default duration.

    The command is the one behind the Power Cycle button of the router's own
    UI; the mode of the port is not touched, so the power returns by itself.
    """
    button, coord = _poe_button(hass, access={"write"})
    await button.async_press()
    coord.execute.assert_called_once_with("/interface/ethernet/poe", "power-cycle", "name", "ether5", {"duration": "5s"})


async def test_poe_power_cycle_reports_a_refused_command(hass):
    """A refusal reaches the UI as an error naming the router."""
    button, coord = _poe_button(hass, access={"write"}, execute_result=False)
    with pytest.raises(HomeAssistantError) as err:
        await button.async_press()
    assert err.value.translation_key == "write_refused"
    assert err.value.translation_placeholders["host"] == coord.host


def test_poe_power_cycle_button_is_declared_next_to_the_selector():
    """Same device and same port row as the PoE out selector."""
    from custom_components.mikrotik_extended.button_types import SENSOR_TYPES
    from custom_components.mikrotik_extended.select_types import SENSOR_TYPES as SELECTS

    button = next(d for d in SENSOR_TYPES if d.key == "poe_power_cycle")
    selector = next(d for d in SELECTS if d.key == "poe_out")
    assert button.func == "MikrotikPoePowerCycleButton"
    for attr in ("ha_group", "ha_connection", "ha_connection_value", "data_path", "data_uid", "data_reference"):
        assert getattr(button, attr) == getattr(selector, attr), attr


def test_poe_power_cycle_button_only_on_poe_ports():
    """A port that cannot supply power gets no button, like it gets no selector."""
    from custom_components.mikrotik_extended.entity import _skip_non_poe_port

    desc = MagicMock(func="MikrotikPoePowerCycleButton")
    assert _skip_non_poe_port(desc, {"poe-out": "N/A"}) is True
    assert _skip_non_poe_port(desc, {"poe-out": ""}) is True
    assert _skip_non_poe_port(desc, {}) is True
    assert _skip_non_poe_port(desc, {"poe-out": "auto-on"}) is False
    assert _skip_non_poe_port(desc, {"poe-out": "off"}) is False

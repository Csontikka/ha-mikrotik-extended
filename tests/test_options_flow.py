"""Tests for Mikrotik Router options flow."""

from homeassistant.const import (
    CONF_HOST,
    CONF_NAME,
    CONF_PASSWORD,
    CONF_PORT,
    CONF_SSL,
    CONF_USERNAME,
    CONF_VERIFY_SSL,
    CONF_ZONE,
    STATE_HOME,
)
from homeassistant.data_entry_flow import FlowResultType
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.mikrotik_extended.const import (
    CONF_SCAN_INTERVAL,
    CONF_SENSOR_CLIENT_CAPTIVE,
    CONF_SENSOR_CLIENT_TRAFFIC,
    CONF_SENSOR_CONTAINERS,
    CONF_SENSOR_ENVIRONMENT,
    CONF_SENSOR_FILTER,
    CONF_SENSOR_KIDCONTROL,
    CONF_SENSOR_MANGLE,
    CONF_SENSOR_NAT,
    CONF_SENSOR_NETWATCH_TRACKER,
    CONF_SENSOR_PORT_TRACKER,
    CONF_SENSOR_PORT_TRAFFIC,
    CONF_SENSOR_PPP,
    CONF_SENSOR_ROUTING_RULES,
    CONF_SENSOR_SCRIPTS,
    CONF_SENSOR_SIMPLE_QUEUES,
    CONF_SENSOR_WIREGUARD,
    CONF_TRACK_HOSTS,
    CONF_TRACK_HOSTS_INTERVAL,
    CONF_TRACK_HOSTS_TIMEOUT,
    DOMAIN,
)

ENTRY_DATA = {
    CONF_HOST: "192.168.88.1",
    CONF_USERNAME: "admin",
    CONF_PASSWORD: "test",
    CONF_PORT: 0,
    CONF_SSL: False,
    CONF_VERIFY_SSL: False,
    CONF_NAME: "Mikrotik",
}

INITIAL_OPTIONS = {
    CONF_SCAN_INTERVAL: 30,
    CONF_TRACK_HOSTS_TIMEOUT: 180,
    CONF_ZONE: STATE_HOME,
    CONF_SENSOR_PORT_TRACKER: True,
    CONF_SENSOR_NAT: True,
    CONF_SENSOR_MANGLE: False,
    CONF_SENSOR_FILTER: True,
    CONF_SENSOR_WIREGUARD: False,
    CONF_SENSOR_CONTAINERS: False,
    CONF_SENSOR_PPP: False,
    CONF_SENSOR_KIDCONTROL: False,
    CONF_SENSOR_SCRIPTS: True,
    CONF_SENSOR_ENVIRONMENT: False,
    CONF_SENSOR_NETWATCH_TRACKER: True,
    CONF_SENSOR_PORT_TRAFFIC: False,
    CONF_SENSOR_CLIENT_TRAFFIC: False,
    CONF_SENSOR_CLIENT_CAPTIVE: False,
    CONF_SENSOR_SIMPLE_QUEUES: False,
    CONF_SENSOR_ROUTING_RULES: False,
    CONF_TRACK_HOSTS: False,
}

SENSOR_SELECT_INPUT = {
    CONF_SENSOR_PORT_TRACKER: True,
    CONF_SENSOR_NAT: True,
    CONF_SENSOR_MANGLE: True,
    CONF_SENSOR_FILTER: True,
    CONF_SENSOR_WIREGUARD: False,
    CONF_SENSOR_CONTAINERS: False,
    CONF_SENSOR_PPP: False,
    CONF_SENSOR_KIDCONTROL: False,
    CONF_SENSOR_SCRIPTS: True,
    CONF_SENSOR_ENVIRONMENT: False,
    CONF_SENSOR_NETWATCH_TRACKER: True,
    CONF_SENSOR_PORT_TRAFFIC: False,
    CONF_SENSOR_CLIENT_TRAFFIC: False,
    CONF_SENSOR_CLIENT_CAPTIVE: False,
    CONF_SENSOR_SIMPLE_QUEUES: False,
    CONF_SENSOR_ROUTING_RULES: False,
    CONF_TRACK_HOSTS: False,
}


async def test_options_flow_updates_scan_interval(hass):
    """Options flow saves updated scan_interval and zone."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        data=ENTRY_DATA,
        options=INITIAL_OPTIONS,
        unique_id="192.168.88.1",
    )
    entry.add_to_hass(hass)

    # Step 1: basic_options
    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "basic_options"

    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {
            CONF_SCAN_INTERVAL: 60,
            CONF_TRACK_HOSTS_TIMEOUT: 300,
            CONF_TRACK_HOSTS_INTERVAL: 45,
            CONF_ZONE: "away",
        },
    )
    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "sensor_mode"

    # Step 2: sensor_mode — choose custom
    result = await hass.config_entries.options.async_configure(result["flow_id"], {"sensor_preset": "custom"})
    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "sensor_select"

    # Step 3: sensor_select — save
    result = await hass.config_entries.options.async_configure(result["flow_id"], SENSOR_SELECT_INPUT)
    assert result["type"] == FlowResultType.CREATE_ENTRY

    assert entry.options[CONF_SCAN_INTERVAL] == 60
    assert entry.options[CONF_TRACK_HOSTS_TIMEOUT] == 300
    assert entry.options[CONF_TRACK_HOSTS_INTERVAL] == 45
    assert entry.options[CONF_ZONE] == "away"
    # Full round-trip: every flag submitted on sensor_select must land verbatim
    # in entry.options (not just CONF_SENSOR_MANGLE).
    for flag, expected in SENSOR_SELECT_INPUT.items():
        assert entry.options[flag] is expected, f"{flag} did not round-trip: got {entry.options.get(flag)!r}, expected {expected!r}"


async def test_options_flow_sensor_toggle(hass):
    """Options flow can disable sensors that were previously enabled."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        data=ENTRY_DATA,
        options=INITIAL_OPTIONS,
        unique_id="192.168.88.1",
    )
    entry.add_to_hass(hass)

    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {
            CONF_SCAN_INTERVAL: 30,
            CONF_TRACK_HOSTS_TIMEOUT: 180,
            CONF_ZONE: STATE_HOME,
        },
    )

    # sensor_mode — choose custom
    result = await hass.config_entries.options.async_configure(result["flow_id"], {"sensor_preset": "custom"})

    disabled_sensors = {k: False for k in SENSOR_SELECT_INPUT}
    disabled_sensors[CONF_SENSOR_PORT_TRACKER] = True  # keep at least one

    result = await hass.config_entries.options.async_configure(result["flow_id"], disabled_sensors)
    assert result["type"] == FlowResultType.CREATE_ENTRY
    # Every flag submitted must round-trip: all False, except CONF_SENSOR_PORT_TRACKER
    for flag, expected in disabled_sensors.items():
        assert entry.options[flag] is expected, f"{flag} did not round-trip: got {entry.options.get(flag)!r}, expected {expected!r}"


async def test_options_flow_core_preset_disables_interfaces(hass):
    """Choosing the Core only preset turns every category off, interfaces included."""
    from custom_components.mikrotik_extended.const import CONF_SENSOR_INTERFACES

    entry = MockConfigEntry(
        domain=DOMAIN,
        data=ENTRY_DATA,
        options=INITIAL_OPTIONS,
        unique_id="192.168.88.1",
    )
    entry.add_to_hass(hass)

    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {
            CONF_SCAN_INTERVAL: 30,
            CONF_TRACK_HOSTS_TIMEOUT: 180,
            CONF_ZONE: STATE_HOME,
        },
    )
    assert result["step_id"] == "sensor_mode"

    result = await hass.config_entries.options.async_configure(result["flow_id"], {"sensor_preset": "core"})
    assert result["type"] == FlowResultType.CREATE_ENTRY

    assert entry.options[CONF_SENSOR_INTERFACES] is False
    assert entry.options[CONF_SENSOR_PORT_TRACKER] is False
    assert entry.options[CONF_SENSOR_NAT] is False
    assert entry.options[CONF_TRACK_HOSTS] is False


async def test_tracker_interval_outside_the_range_is_rejected(hass):
    """The form refuses a tracker interval below 5 or above 300 seconds."""
    import pytest
    from homeassistant.data_entry_flow import InvalidData

    entry = MockConfigEntry(domain=DOMAIN, data=ENTRY_DATA, options=INITIAL_OPTIONS, unique_id="192.168.88.1")
    entry.add_to_hass(hass)
    for bad in (4, 301):
        result = await hass.config_entries.options.async_init(entry.entry_id)
        with pytest.raises(InvalidData):
            await hass.config_entries.options.async_configure(result["flow_id"], {CONF_SCAN_INTERVAL: 30, CONF_TRACK_HOSTS_INTERVAL: bad})


async def test_tracker_interval_form_shows_the_stored_value(hass):
    """Reopening the options shows what was saved, 10 when nothing was."""
    for stored, shown in ((None, 10), (120, 120)):
        options = dict(INITIAL_OPTIONS)
        if stored is not None:
            options[CONF_TRACK_HOSTS_INTERVAL] = stored
        entry = MockConfigEntry(domain=DOMAIN, data=ENTRY_DATA, options=options, unique_id=f"192.168.88.{shown}")
        entry.add_to_hass(hass)
        result = await hass.config_entries.options.async_init(entry.entry_id)
        defaults = {str(key): key.default() for key in result["data_schema"].schema}
        assert defaults[CONF_TRACK_HOSTS_INTERVAL] == shown

"""Tests for Mikrotik Router config flow."""

from unittest.mock import MagicMock, patch

from homeassistant import config_entries
from homeassistant.const import (
    CONF_HOST,
    CONF_NAME,
    CONF_PASSWORD,
    CONF_PORT,
    CONF_SSL,
    CONF_USERNAME,
    CONF_VERIFY_SSL,
)
from homeassistant.data_entry_flow import FlowResultType
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.mikrotik_extended.const import DOMAIN

ENTRY_DATA = {
    CONF_HOST: "192.168.88.1",
    CONF_USERNAME: "admin",
    CONF_PASSWORD: "oldpass",
    CONF_PORT: 0,
    CONF_SSL: False,
    CONF_VERIFY_SSL: False,
    CONF_NAME: "Mikrotik",
}

# User step schema requires: name, host, username, password, port, ssl_mode
USER_INPUT = {
    CONF_NAME: "Mikrotik",
    CONF_HOST: "192.168.88.1",
    CONF_USERNAME: "admin",
    CONF_PASSWORD: "test",
    CONF_PORT: 0,
    "ssl_mode": "none",
}

BASIC_OPTIONS_INPUT = {
    "scan_interval": 30,
    "track_network_hosts_timeout": 180,
    "zone": "home",
}


async def _init_and_skip_discovery(hass):
    """Init flow, pass through discovery step (scan=False), return flow result on 'user' step."""
    with patch(
        "custom_components.mikrotik_extended.config_flow.async_scan_mndp",
        return_value=[],
    ):
        result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": config_entries.SOURCE_USER})
        assert result["type"] == FlowResultType.FORM
        assert result["step_id"] == "discovery"

        result = await hass.config_entries.flow.async_configure(result["flow_id"], {"scan": False})
    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "user"
    return result


async def test_successful_setup_recommended(hass):
    """Test full config flow with recommended preset — entry is created."""
    with patch("custom_components.mikrotik_extended.config_flow.MikrotikAPI") as mock_api_cls:
        mock_api = MagicMock()
        mock_api.connect.return_value = True
        mock_api.error = None
        mock_api_cls.return_value = mock_api

        result = await _init_and_skip_discovery(hass)

        # Step: user — credentials
        result = await hass.config_entries.flow.async_configure(result["flow_id"], USER_INPUT)
        assert result["type"] == FlowResultType.FORM
        assert result["step_id"] == "basic_options"

        # Step: basic_options
        result = await hass.config_entries.flow.async_configure(result["flow_id"], BASIC_OPTIONS_INPUT)
        assert result["type"] == FlowResultType.FORM
        assert result["step_id"] == "sensor_mode"

        # Step: sensor_mode — choose recommended preset
        result = await hass.config_entries.flow.async_configure(result["flow_id"], {"sensor_preset": "recommended"})

        assert result["type"] == FlowResultType.CREATE_ENTRY
        assert result["title"] == "Mikrotik"
        assert result["data"][CONF_HOST] == "192.168.88.1"


async def test_duplicate_entry_aborted(hass):
    """Test config flow aborts when the same host is added a second time."""
    with patch("custom_components.mikrotik_extended.config_flow.MikrotikAPI") as mock_api_cls:
        mock_api = MagicMock()
        mock_api.connect.return_value = True
        mock_api.error = None
        mock_api_cls.return_value = mock_api

        # First entry — full flow
        result = await _init_and_skip_discovery(hass)
        result = await hass.config_entries.flow.async_configure(result["flow_id"], USER_INPUT)
        result = await hass.config_entries.flow.async_configure(result["flow_id"], BASIC_OPTIONS_INPUT)
        result = await hass.config_entries.flow.async_configure(result["flow_id"], {"sensor_preset": "recommended"})
        assert result["type"] == FlowResultType.CREATE_ENTRY

        # Second attempt with the same host — should abort
        result2 = await _init_and_skip_discovery(hass)
        result2 = await hass.config_entries.flow.async_configure(result2["flow_id"], USER_INPUT)
        assert result2["type"] == FlowResultType.ABORT
        assert result2["reason"] == "already_configured"


async def test_connection_failure(hass):
    """Test config flow shows error when router is unreachable."""
    with patch("custom_components.mikrotik_extended.config_flow.MikrotikAPI") as mock_api_cls:
        mock_api = MagicMock()
        mock_api.connect.return_value = False
        mock_api.error = "cannot_connect"
        mock_api_cls.return_value = mock_api

        result = await _init_and_skip_discovery(hass)
        result = await hass.config_entries.flow.async_configure(result["flow_id"], USER_INPUT)

        # Should stay on user step with error
        assert result["type"] == FlowResultType.FORM
        assert result["step_id"] == "user"
        assert result["errors"][CONF_HOST] == "cannot_connect"


async def test_reauth_flow_success(hass):
    """Reauth flow updates credentials and aborts with reauth_successful."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        data=ENTRY_DATA,
        options={},
        unique_id="192.168.88.1",
    )
    entry.add_to_hass(hass)

    with patch("custom_components.mikrotik_extended.config_flow.MikrotikAPI") as mock_api_cls:
        mock_api = MagicMock()
        mock_api.connect.return_value = True
        mock_api.error = None
        mock_api_cls.return_value = mock_api

        result = await hass.config_entries.flow.async_init(
            DOMAIN,
            context={"source": config_entries.SOURCE_REAUTH, "entry_id": entry.entry_id},
            data=entry.data,
        )
        assert result["type"] == FlowResultType.FORM
        assert result["step_id"] == "reauth_confirm"

        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {CONF_USERNAME: "newadmin", CONF_PASSWORD: "newpass"},
        )
        assert result["type"] == FlowResultType.ABORT
        assert result["reason"] == "reauth_successful"
        assert entry.data[CONF_USERNAME] == "newadmin"
        assert entry.data[CONF_PASSWORD] == "newpass"


async def test_reauth_flow_wrong_credentials(hass):
    """Reauth flow stays on form when credentials are rejected by the router."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        data=ENTRY_DATA,
        options={},
        unique_id="192.168.88.1",
    )
    entry.add_to_hass(hass)

    with patch("custom_components.mikrotik_extended.config_flow.MikrotikAPI") as mock_api_cls:
        mock_api = MagicMock()
        mock_api.connect.return_value = False
        mock_api.error = "wrong_login"
        mock_api_cls.return_value = mock_api

        result = await hass.config_entries.flow.async_init(
            DOMAIN,
            context={"source": config_entries.SOURCE_REAUTH, "entry_id": entry.entry_id},
            data=entry.data,
        )
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {CONF_USERNAME: "admin", CONF_PASSWORD: "wrongpass"},
        )
        assert result["type"] == FlowResultType.FORM
        assert result["step_id"] == "reauth_confirm"
        assert result["errors"][CONF_PASSWORD] == "wrong_login"


def test_ssl_mode_from_bools_branches():
    """_ssl_mode_from_bools returns the correct label for every ssl/verify combination."""
    from custom_components.mikrotik_extended.config_flow import _ssl_mode_from_bools

    assert _ssl_mode_from_bools(False, False) == "none"
    assert _ssl_mode_from_bools(False, True) == "none"
    assert _ssl_mode_from_bools(True, False) == "ssl"
    assert _ssl_mode_from_bools(True, True) == "ssl_verify"


async def test_import_step_forwards_to_user(hass):
    """async_step_import should forward to the user step."""
    with (
        patch("custom_components.mikrotik_extended.config_flow.MikrotikAPI") as mock_api_cls,
        patch(
            "custom_components.mikrotik_extended.config_flow.async_scan_mndp",
            return_value=[],
        ),
    ):
        mock_api = MagicMock()
        mock_api.connect.return_value = True
        mock_api.error = None
        mock_api_cls.return_value = mock_api

        result = await hass.config_entries.flow.async_init(
            DOMAIN,
            context={"source": config_entries.SOURCE_IMPORT},
            data=dict(USER_INPUT),
        )
        # Import forwards to user, which with valid data advances to basic_options
        assert result["type"] == FlowResultType.FORM
        assert result["step_id"] == "basic_options"


async def test_sensor_mode_custom_routes_to_sensor_select(hass):
    """Choosing 'custom' on sensor_mode in initial setup routes to sensor_select."""
    with patch("custom_components.mikrotik_extended.config_flow.MikrotikAPI") as mock_api_cls:
        mock_api = MagicMock()
        mock_api.connect.return_value = True
        mock_api.error = None
        mock_api_cls.return_value = mock_api

        result = await _init_and_skip_discovery(hass)
        result = await hass.config_entries.flow.async_configure(result["flow_id"], USER_INPUT)
        result = await hass.config_entries.flow.async_configure(result["flow_id"], BASIC_OPTIONS_INPUT)
        assert result["step_id"] == "sensor_mode"

        result = await hass.config_entries.flow.async_configure(result["flow_id"], {"sensor_preset": "custom"})
        assert result["type"] == FlowResultType.FORM
        assert result["step_id"] == "sensor_select"


async def test_sensor_select_creates_entry_with_custom_sensors(hass):
    """Submitting sensor_select creates the entry with the chosen flags."""
    from custom_components.mikrotik_extended.const import (
        CONF_SENSOR_NAT,
        CONF_SENSOR_PORT_TRACKER,
        CONF_TRACK_HOSTS,
    )

    with patch("custom_components.mikrotik_extended.config_flow.MikrotikAPI") as mock_api_cls:
        mock_api = MagicMock()
        mock_api.connect.return_value = True
        mock_api.error = None
        mock_api_cls.return_value = mock_api

        result = await _init_and_skip_discovery(hass)
        result = await hass.config_entries.flow.async_configure(result["flow_id"], USER_INPUT)
        result = await hass.config_entries.flow.async_configure(result["flow_id"], BASIC_OPTIONS_INPUT)
        result = await hass.config_entries.flow.async_configure(result["flow_id"], {"sensor_preset": "custom"})
        assert result["step_id"] == "sensor_select"

        # Submit sensor_select with only a couple of flags enabled
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {CONF_SENSOR_PORT_TRACKER: True, CONF_SENSOR_NAT: True, CONF_TRACK_HOSTS: False},
        )
        assert result["type"] == FlowResultType.CREATE_ENTRY
        assert result["options"][CONF_SENSOR_PORT_TRACKER] is True
        assert result["options"][CONF_SENSOR_NAT] is True


async def test_successful_setup_core_preset(hass):
    """Core preset creates the entry with every category off, interfaces included."""
    from custom_components.mikrotik_extended.const import (
        CONF_SENSOR_INTERFACES,
        CONF_SENSOR_NAT,
        CONF_SENSOR_PORT_TRACKER,
        CONF_TRACK_HOSTS,
    )

    with patch("custom_components.mikrotik_extended.config_flow.MikrotikAPI") as mock_api_cls:
        mock_api = MagicMock()
        mock_api.connect.return_value = True
        mock_api.error = None
        mock_api_cls.return_value = mock_api

        result = await _init_and_skip_discovery(hass)
        result = await hass.config_entries.flow.async_configure(result["flow_id"], USER_INPUT)
        result = await hass.config_entries.flow.async_configure(result["flow_id"], BASIC_OPTIONS_INPUT)
        result = await hass.config_entries.flow.async_configure(result["flow_id"], {"sensor_preset": "core"})

        assert result["type"] == FlowResultType.CREATE_ENTRY
        assert result["options"][CONF_SENSOR_INTERFACES] is False
        assert result["options"][CONF_SENSOR_PORT_TRACKER] is False
        assert result["options"][CONF_SENSOR_NAT] is False
        assert result["options"][CONF_TRACK_HOSTS] is False


def test_sensor_presets_cover_interfaces_option():
    """Every preset pins the interfaces option, core turns everything off."""
    from custom_components.mikrotik_extended.config_flow import _SENSOR_PRESETS
    from custom_components.mikrotik_extended.const import CONF_SENSOR_INTERFACES

    key_sets = {frozenset(preset) for preset in _SENSOR_PRESETS.values()}
    assert len(key_sets) == 1, "all presets must define the same option keys"
    for name, preset in _SENSOR_PRESETS.items():
        assert preset[CONF_SENSOR_INTERFACES] is (name != "core"), name
    assert all(value is False for value in _SENSOR_PRESETS["core"].values())


def _password_fields(schema):
    """The password field of a form: (key, serialised selector)."""
    import voluptuous_serialize
    from homeassistant.helpers import config_validation as cv

    fields = {f["name"]: f for f in voluptuous_serialize.convert(schema, custom_serializer=cv.custom_serializer)}
    return fields[CONF_PASSWORD]


async def test_password_is_a_password_field_on_every_form(hass):
    """Drawn masked by the frontend, and serialisable, so the forms open."""
    entry = MockConfigEntry(domain=DOMAIN, data=ENTRY_DATA, options={}, unique_id="192.168.88.1")
    entry.add_to_hass(hass)
    forms = {}
    forms["user"] = await _init_and_skip_discovery(hass)
    forms["reauth"] = await entry.start_reauth_flow(hass)
    forms["reconfigure"] = await entry.start_reconfigure_flow(hass)
    for name, result in forms.items():
        field = _password_fields(result["data_schema"])
        assert field["selector"] == {"text": {"type": "password", "multiline": False, "multiple": False}}, name


async def test_reconfigure_form_never_shows_the_stored_password(hass):
    entry = MockConfigEntry(domain=DOMAIN, data={**ENTRY_DATA, CONF_PASSWORD: "s3cret"}, options={}, unique_id="192.168.88.1")
    entry.add_to_hass(hass)
    result = await entry.start_reconfigure_flow(hass)
    field = _password_fields(result["data_schema"])
    assert field.get("default", "") == ""
    assert "s3cret" not in repr(result["data_schema"].schema)

    # Not after a failed attempt either.
    with patch("custom_components.mikrotik_extended.config_flow.MikrotikAPI") as mock_api_cls:
        mock_api_cls.return_value = MagicMock(error="cannot_connect", **{"connect.return_value": False})
        result = await hass.config_entries.flow.async_configure(result["flow_id"], {CONF_NAME: "Mikrotik", CONF_HOST: "192.168.88.1", CONF_USERNAME: "admin", CONF_PASSWORD: "typed", CONF_PORT: 0, "ssl_mode": "none"})
    assert result["type"] == FlowResultType.FORM
    assert "typed" not in repr(result["data_schema"].schema) and "s3cret" not in repr(result["data_schema"].schema)


async def test_reconfigure_with_an_empty_password_keeps_the_stored_one(hass):
    entry = MockConfigEntry(domain=DOMAIN, data={**ENTRY_DATA, CONF_PASSWORD: "s3cret"}, options={}, unique_id="192.168.88.1")
    entry.add_to_hass(hass)
    with patch("custom_components.mikrotik_extended.config_flow.MikrotikAPI") as mock_api_cls:
        mock_api_cls.return_value = MagicMock(error=None, **{"connect.return_value": True})
        result = await entry.start_reconfigure_flow(hass)
        result = await hass.config_entries.flow.async_configure(result["flow_id"], {CONF_NAME: "Mikrotik", CONF_HOST: "192.168.88.1", CONF_USERNAME: "admin", CONF_PORT: 0, "ssl_mode": "none"})
    assert result["type"] == FlowResultType.ABORT
    assert entry.data[CONF_PASSWORD] == "s3cret"
    assert mock_api_cls.call_args.kwargs["password"] == "s3cret", "the connection test ran with the stored password"


async def test_reconfigure_with_a_new_password_stores_it(hass):
    entry = MockConfigEntry(domain=DOMAIN, data={**ENTRY_DATA, CONF_PASSWORD: "s3cret"}, options={}, unique_id="192.168.88.1")
    entry.add_to_hass(hass)
    with patch("custom_components.mikrotik_extended.config_flow.MikrotikAPI") as mock_api_cls:
        mock_api_cls.return_value = MagicMock(error=None, **{"connect.return_value": True})
        result = await entry.start_reconfigure_flow(hass)
        result = await hass.config_entries.flow.async_configure(result["flow_id"], {CONF_NAME: "Mikrotik", CONF_HOST: "192.168.88.1", CONF_USERNAME: "admin", CONF_PASSWORD: "newpass", CONF_PORT: 0, "ssl_mode": "none"})
    assert result["type"] == FlowResultType.ABORT
    assert entry.data[CONF_PASSWORD] == "newpass"
    assert mock_api_cls.call_args.kwargs["password"] == "newpass"


async def test_reconfigure_flow_success(hass):
    """Reconfigure flow updates entry data when credentials still work."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        data=ENTRY_DATA,
        options={},
        unique_id="192.168.88.1",
    )
    entry.add_to_hass(hass)

    with patch("custom_components.mikrotik_extended.config_flow.MikrotikAPI") as mock_api_cls:
        mock_api = MagicMock()
        mock_api.connect.return_value = True
        mock_api.error = None
        mock_api_cls.return_value = mock_api

        result = await entry.start_reconfigure_flow(hass)
        assert result["type"] == FlowResultType.FORM
        assert result["step_id"] == "reconfigure"

        new_input = {
            CONF_NAME: "Mikrotik Renamed",
            CONF_HOST: "192.168.88.1",
            CONF_USERNAME: "admin",
            CONF_PASSWORD: "updatedpass",
            CONF_PORT: 0,
            "ssl_mode": "ssl",
        }
        result = await hass.config_entries.flow.async_configure(result["flow_id"], new_input)
        assert result["type"] == FlowResultType.ABORT
        assert result["reason"] == "reconfigure_successful"
        assert entry.data[CONF_PASSWORD] == "updatedpass"
        assert entry.data[CONF_SSL] is True
        assert entry.data[CONF_VERIFY_SSL] is False


async def test_reconfigure_flow_connection_error(hass):
    """Reconfigure flow stays on form when the new credentials don't work."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        data=ENTRY_DATA,
        options={},
        unique_id="192.168.88.1",
    )
    entry.add_to_hass(hass)

    with patch("custom_components.mikrotik_extended.config_flow.MikrotikAPI") as mock_api_cls:
        mock_api = MagicMock()
        mock_api.connect.return_value = False
        mock_api.error = "cannot_connect"
        mock_api_cls.return_value = mock_api

        result = await entry.start_reconfigure_flow(hass)
        new_input = {
            CONF_NAME: "Mikrotik",
            CONF_HOST: "192.168.88.1",
            CONF_USERNAME: "admin",
            CONF_PASSWORD: "bad",
            CONF_PORT: 0,
            "ssl_mode": "none",
        }
        result = await hass.config_entries.flow.async_configure(result["flow_id"], new_input)
        assert result["type"] == FlowResultType.FORM
        assert result["step_id"] == "reconfigure"
        assert result["errors"][CONF_HOST] == "cannot_connect"


RECONFIGURE_UNCHANGED = {
    CONF_NAME: "Mock Title",
    CONF_HOST: ENTRY_DATA[CONF_HOST],
    CONF_USERNAME: ENTRY_DATA[CONF_USERNAME],
    CONF_PASSWORD: ENTRY_DATA[CONF_PASSWORD],
    CONF_PORT: ENTRY_DATA[CONF_PORT],
    "ssl_mode": "none",
}


async def _reload_counts(hass, *, flow, user_input, with_listener):
    """Run a reauth or reconfigure flow; return (flow reloads, listener calls).

    The listener stands in for the one a loaded entry registers, which
    schedules one reload per call. The first number is what the flow itself
    scheduled; that the real listener reloads is covered by the test of
    async_reload_entry, not here.
    """
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={**ENTRY_DATA, CONF_NAME: "Mock Title"},
        options={},
        unique_id="192.168.88.1",
    )
    entry.add_to_hass(hass)
    listener_calls = []

    async def _listener(hass, config_entry):
        listener_calls.append(config_entry.entry_id)

    if with_listener:
        entry.add_update_listener(_listener)

    with (
        patch("custom_components.mikrotik_extended.config_flow.MikrotikAPI") as mock_api_cls,
        patch.object(hass.config_entries, "async_schedule_reload") as schedule_reload,
    ):
        mock_api_cls.return_value = MagicMock(error=None, **{"connect.return_value": True})
        start = entry.start_reauth_flow if flow == "reauth" else entry.start_reconfigure_flow
        result = await start(hass)
        result = await hass.config_entries.flow.async_configure(result["flow_id"], dict(user_input))
        await hass.async_block_till_done()

    assert result["type"] == FlowResultType.ABORT
    assert result["reason"] == f"{flow}_successful"
    return schedule_reload.call_count, len(listener_calls)


async def test_reconfigure_changed_with_listener_leaves_reload_to_listener(hass):
    """A loaded entry is reloaded by its own listener, and only by it."""
    changed = {**RECONFIGURE_UNCHANGED, CONF_PASSWORD: "updatedpass"}
    assert await _reload_counts(hass, flow="reconfigure", user_input=changed, with_listener=True) == (0, 1)


async def test_reconfigure_title_only_change_leaves_reload_to_listener(hass):
    """A new name alone counts as a change and reaches the listener."""
    renamed = {**RECONFIGURE_UNCHANGED, CONF_NAME: "Renamed"}
    assert await _reload_counts(hass, flow="reconfigure", user_input=renamed, with_listener=True) == (0, 1)


async def test_reconfigure_changed_without_listener_schedules_reload(hass):
    """An entry whose setup failed has no listener, so the flow reloads it."""
    changed = {**RECONFIGURE_UNCHANGED, CONF_PASSWORD: "updatedpass"}
    assert await _reload_counts(hass, flow="reconfigure", user_input=changed, with_listener=False) == (1, 0)


async def test_reconfigure_unchanged_with_listener_schedules_reload(hass):
    """Nothing changed, so the listener is not notified and the flow reloads."""
    assert await _reload_counts(hass, flow="reconfigure", user_input=RECONFIGURE_UNCHANGED, with_listener=True) == (1, 0)


async def test_reconfigure_unchanged_without_listener_schedules_reload(hass):
    """An unchanged entry waiting to retry is brought up at once."""
    assert await _reload_counts(hass, flow="reconfigure", user_input=RECONFIGURE_UNCHANGED, with_listener=False) == (1, 0)


async def test_reauth_with_listener_reloads_once(hass):
    """New credentials on a loaded entry: the listener reloads, the flow does not."""
    creds = {CONF_USERNAME: "admin", CONF_PASSWORD: "newpass"}
    assert await _reload_counts(hass, flow="reauth", user_input=creds, with_listener=True) == (0, 1)


async def test_reauth_without_listener_schedules_reload(hass):
    """New credentials on an entry that failed setup: the flow reloads it."""
    creds = {CONF_USERNAME: "admin", CONF_PASSWORD: "newpass"}
    assert await _reload_counts(hass, flow="reauth", user_input=creds, with_listener=False) == (1, 0)


async def test_reauth_same_credentials_still_reloads(hass):
    """Unchanged credentials, e.g. the router account was fixed instead."""
    creds = {CONF_USERNAME: ENTRY_DATA[CONF_USERNAME], CONF_PASSWORD: ENTRY_DATA[CONF_PASSWORD]}
    assert await _reload_counts(hass, flow="reauth", user_input=creds, with_listener=True) == (1, 0)


async def test_discovery_scan_finds_devices_routes_to_pick_device(hass):
    """When MNDP scan returns devices, flow routes to pick_device step."""
    from custom_components.mikrotik_extended.mndp import MndpDevice

    devices = [
        MndpDevice(ip="192.168.88.2", identity="router-a"),
        MndpDevice(ip="192.168.88.3", identity="router-b"),
    ]
    with patch(
        "custom_components.mikrotik_extended.config_flow.async_scan_mndp",
        return_value=devices,
    ):
        result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": config_entries.SOURCE_USER})
        assert result["step_id"] == "discovery"
        result = await hass.config_entries.flow.async_configure(result["flow_id"], {"scan": True})
        assert result["type"] == FlowResultType.FORM
        assert result["step_id"] == "pick_device"


async def test_discovery_scan_raises_exception_falls_back_to_user(hass):
    """If async_scan_mndp raises, flow falls back and shows the config form with no_devices_found."""
    with patch(
        "custom_components.mikrotik_extended.config_flow.async_scan_mndp",
        side_effect=OSError("scan failed"),
    ):
        result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": config_entries.SOURCE_USER})
        result = await hass.config_entries.flow.async_configure(result["flow_id"], {"scan": True})
        assert result["type"] == FlowResultType.FORM
        assert result["step_id"] == "user"
        assert result["errors"] == {"base": "no_devices_found"}


async def test_discovery_scan_empty_shows_no_devices_found(hass):
    """If async_scan_mndp returns empty list, flow shows config form with no_devices_found."""
    with patch(
        "custom_components.mikrotik_extended.config_flow.async_scan_mndp",
        return_value=[],
    ):
        result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": config_entries.SOURCE_USER})
        result = await hass.config_entries.flow.async_configure(result["flow_id"], {"scan": True})
        assert result["type"] == FlowResultType.FORM
        assert result["step_id"] == "user"
        assert result["errors"] == {"base": "no_devices_found"}


async def test_pick_device_selects_router_shows_config_form(hass):
    """Selecting a specific router from pick_device shows the config form prefilled."""
    from custom_components.mikrotik_extended.mndp import MndpDevice

    devices = [MndpDevice(ip="192.168.88.2", identity="router-a")]
    with patch(
        "custom_components.mikrotik_extended.config_flow.async_scan_mndp",
        return_value=devices,
    ):
        result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": config_entries.SOURCE_USER})
        result = await hass.config_entries.flow.async_configure(result["flow_id"], {"scan": True})
        assert result["step_id"] == "pick_device"

        result = await hass.config_entries.flow.async_configure(result["flow_id"], {"router": "192.168.88.2"})
        assert result["type"] == FlowResultType.FORM
        assert result["step_id"] == "user"


async def test_pick_device_manual_shows_config_form(hass):
    """Choosing 'manual' on pick_device shows the default config form."""
    from custom_components.mikrotik_extended.mndp import MndpDevice

    devices = [MndpDevice(ip="192.168.88.2", identity="router-a")]
    with patch(
        "custom_components.mikrotik_extended.config_flow.async_scan_mndp",
        return_value=devices,
    ):
        result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": config_entries.SOURCE_USER})
        result = await hass.config_entries.flow.async_configure(result["flow_id"], {"scan": True})
        result = await hass.config_entries.flow.async_configure(result["flow_id"], {"router": "manual"})
        assert result["type"] == FlowResultType.FORM
        assert result["step_id"] == "user"


async def test_options_flow_sensor_custom_then_select(hass):
    """Options flow: custom preset -> sensor_select form -> submit creates entry."""
    from custom_components.mikrotik_extended.const import CONF_SENSOR_NAT, CONF_SENSOR_PORT_TRACKER

    entry = MockConfigEntry(
        domain=DOMAIN,
        data=ENTRY_DATA,
        options={CONF_SENSOR_PORT_TRACKER: False, CONF_SENSOR_NAT: False},
        unique_id="192.168.88.1",
    )
    entry.add_to_hass(hass)

    result = await hass.config_entries.options.async_init(entry.entry_id)
    while result["type"] == FlowResultType.FORM and result["step_id"] != "sensor_mode":
        result = await hass.config_entries.options.async_configure(result["flow_id"], {})

    # Pick custom -> sensor_select form (line 564)
    result = await hass.config_entries.options.async_configure(result["flow_id"], {"sensor_preset": "custom"})
    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "sensor_select"

    # Submit sensor_select -> CREATE_ENTRY (lines 590-592)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {CONF_SENSOR_PORT_TRACKER: True, CONF_SENSOR_NAT: True},
    )
    assert result["type"] == FlowResultType.CREATE_ENTRY
    assert result["data"][CONF_SENSOR_PORT_TRACKER] is True
    assert result["data"][CONF_SENSOR_NAT] is True


async def test_options_flow_sensor_mode_preset_creates_entry(hass):
    """Options flow sensor_mode with a non-custom preset writes the preset into options."""
    from custom_components.mikrotik_extended.const import CONF_SENSOR_NAT, CONF_SENSOR_PORT_TRACKER

    entry = MockConfigEntry(
        domain=DOMAIN,
        data=ENTRY_DATA,
        options={CONF_SENSOR_PORT_TRACKER: False, CONF_SENSOR_NAT: False},
        unique_id="192.168.88.1",
    )
    entry.add_to_hass(hass)

    result = await hass.config_entries.options.async_init(entry.entry_id)
    # Navigate: init -> basic -> sensor_mode — skip through early steps using their defaults.
    while result["type"] == FlowResultType.FORM and result["step_id"] != "sensor_mode":
        result = await hass.config_entries.options.async_configure(result["flow_id"], {})

    assert result["step_id"] == "sensor_mode"
    result = await hass.config_entries.options.async_configure(result["flow_id"], {"sensor_preset": "recommended"})
    assert result["type"] == FlowResultType.CREATE_ENTRY
    # Recommended preset enables NAT + port tracker
    assert result["data"][CONF_SENSOR_PORT_TRACKER] is True
    assert result["data"][CONF_SENSOR_NAT] is True


def _make_traffic_entry(hass, traffic_enabled):
    """Config entry with a mocked runtime coordinator for profile cleanup tests."""
    from custom_components.mikrotik_extended.const import CONF_SENSOR_CLIENT_TRAFFIC

    entry = MockConfigEntry(
        domain=DOMAIN,
        data=ENTRY_DATA,
        options={CONF_SENSOR_CLIENT_TRAFFIC: traffic_enabled},
        unique_id="192.168.88.1",
    )
    entry.add_to_hass(hass)
    coordinator = MagicMock()
    entry.runtime_data = MagicMock(data_coordinator=coordinator)
    return entry, coordinator


async def _options_flow_to_sensor_mode(hass, entry):
    result = await hass.config_entries.options.async_init(entry.entry_id)
    while result["type"] == FlowResultType.FORM and result["step_id"] != "sensor_mode":
        result = await hass.config_entries.options.async_configure(result["flow_id"], {})
    return result


async def test_options_flow_turning_traffic_off_removes_profile(hass):
    """Turning the client traffic option off triggers a one-time profile removal."""
    from custom_components.mikrotik_extended.const import CONF_SENSOR_CLIENT_TRAFFIC

    entry, coordinator = _make_traffic_entry(hass, traffic_enabled=True)
    result = await _options_flow_to_sensor_mode(hass, entry)
    result = await hass.config_entries.options.async_configure(result["flow_id"], {"sensor_preset": "custom"})
    result = await hass.config_entries.options.async_configure(result["flow_id"], {CONF_SENSOR_CLIENT_TRAFFIC: False})
    assert result["type"] == FlowResultType.CREATE_ENTRY
    coordinator.remove_kid_control_monitoring_profile.assert_called_once()


async def test_options_flow_preset_turning_traffic_off_removes_profile(hass):
    """A preset that disables client traffic also triggers the removal."""
    entry, coordinator = _make_traffic_entry(hass, traffic_enabled=True)
    result = await _options_flow_to_sensor_mode(hass, entry)
    result = await hass.config_entries.options.async_configure(result["flow_id"], {"sensor_preset": "recommended"})
    assert result["type"] == FlowResultType.CREATE_ENTRY
    coordinator.remove_kid_control_monitoring_profile.assert_called_once()


async def test_options_flow_traffic_stays_off_no_removal(hass):
    """No removal when the option was already off before the flow."""
    from custom_components.mikrotik_extended.const import CONF_SENSOR_CLIENT_TRAFFIC

    entry, coordinator = _make_traffic_entry(hass, traffic_enabled=False)
    result = await _options_flow_to_sensor_mode(hass, entry)
    result = await hass.config_entries.options.async_configure(result["flow_id"], {"sensor_preset": "custom"})
    result = await hass.config_entries.options.async_configure(result["flow_id"], {CONF_SENSOR_CLIENT_TRAFFIC: False})
    assert result["type"] == FlowResultType.CREATE_ENTRY
    coordinator.remove_kid_control_monitoring_profile.assert_not_called()

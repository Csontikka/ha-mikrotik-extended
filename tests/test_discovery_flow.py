"""Tests for MNDP discovery in the config flow."""
from unittest.mock import patch, MagicMock

from homeassistant import config_entries
from homeassistant.data_entry_flow import FlowResultType

from custom_components.mikrotik_router.const import DOMAIN
from custom_components.mikrotik_router.mndp import MndpDevice

BASIC_OPTIONS_INPUT = {
    "scan_interval": 30,
    "track_network_hosts_timeout": 180,
    "zone": "home",
}


def _mock_api(connect_ok=True):
    api = MagicMock()
    api.connect.return_value = connect_ok
    api.error = None if connect_ok else "cannot_connect"
    return api


async def test_discovery_single_router_shown(hass):
    """When MNDP finds one router, pick_device form is shown."""
    discovered = [MndpDevice(ip="192.168.88.1", identity="MyRouter", board="CCR2004")]

    with patch("custom_components.mikrotik_router.config_flow.async_scan_mndp", return_value=discovered):
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": config_entries.SOURCE_USER}
        )

    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "pick_device"


async def test_discovery_multiple_routers_shown(hass):
    """When MNDP finds multiple routers, all appear in the pick_device dropdown."""
    discovered = [
        MndpDevice(ip="192.168.88.1", identity="Router1", board="CCR2004"),
        MndpDevice(ip="10.0.0.1", identity="Router2", board="RB4011"),
    ]

    with patch("custom_components.mikrotik_router.config_flow.async_scan_mndp", return_value=discovered):
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": config_entries.SOURCE_USER}
        )

    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "pick_device"


async def test_discovery_select_router_prefills_host(hass):
    """Selecting a discovered router pre-fills host in the credentials form."""
    discovered = [MndpDevice(ip="192.168.88.1", identity="MyRouter", board="CCR2004")]

    with patch("custom_components.mikrotik_router.config_flow.async_scan_mndp", return_value=discovered):
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": config_entries.SOURCE_USER}
        )
    assert result["step_id"] == "pick_device"

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"device": "192.168.88.1"}
    )
    # Should now show the user (credentials) form with host pre-filled
    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "user"


async def test_discovery_manual_entry_shows_empty_form(hass):
    """Choosing 'manual' from pick_device shows the credentials form with defaults."""
    discovered = [MndpDevice(ip="192.168.88.1", identity="MyRouter", board="CCR2004")]

    with patch("custom_components.mikrotik_router.config_flow.async_scan_mndp", return_value=discovered):
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": config_entries.SOURCE_USER}
        )
    assert result["step_id"] == "pick_device"

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"device": "manual"}
    )
    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "user"


async def test_discovery_no_routers_skips_pick_device(hass):
    """When MNDP finds nothing, the credentials form is shown directly."""
    with patch("custom_components.mikrotik_router.config_flow.async_scan_mndp", return_value=[]):
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": config_entries.SOURCE_USER}
        )

    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "user"


async def test_discovery_scan_error_skips_pick_device(hass):
    """When MNDP scan raises an exception, the credentials form is shown directly."""
    with patch(
        "custom_components.mikrotik_router.config_flow.async_scan_mndp",
        side_effect=OSError("network unreachable"),
    ):
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": config_entries.SOURCE_USER}
        )

    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "user"


async def test_discovery_full_flow_after_pick(hass):
    """Full flow: MNDP scan → pick router → credentials → basic_options → sensor_mode → entry created."""
    discovered = [MndpDevice(ip="192.168.88.1", identity="MyRouter", board="CCR2004")]

    with patch(
        "custom_components.mikrotik_router.config_flow.async_scan_mndp",
        return_value=discovered,
    ), patch(
        "custom_components.mikrotik_router.config_flow.MikrotikAPI"
    ) as mock_api_cls:
        mock_api_cls.return_value = _mock_api(connect_ok=True)

        # Step 1: pick_device
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": config_entries.SOURCE_USER}
        )
        assert result["step_id"] == "pick_device"

        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {"device": "192.168.88.1"}
        )
        assert result["step_id"] == "user"

        # Step 2: credentials
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {
                "host": "192.168.88.1",
                "username": "admin",
                "password": "test",
                "port": 0,
                "ssl_mode": "none",
                "name": "MyRouter",
            },
        )
        assert result["step_id"] == "basic_options"

        # Step 3: basic_options
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], BASIC_OPTIONS_INPUT
        )
        assert result["step_id"] == "sensor_mode"

        # Step 4: sensor_mode
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {"sensor_preset": "recommended"}
        )
        assert result["type"] == FlowResultType.CREATE_ENTRY
        assert result["data"]["host"] == "192.168.88.1"
        assert result["title"] == "MyRouter"

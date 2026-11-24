"""The connection a config flow form makes to test an account is closed again."""

from unittest.mock import MagicMock, patch

import pytest
from homeassistant import config_entries
from homeassistant.const import CONF_HOST, CONF_NAME, CONF_PASSWORD, CONF_PORT, CONF_USERNAME
from homeassistant.data_entry_flow import FlowResultType
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.mikrotik_extended.const import DOMAIN

from .test_config_flow import ENTRY_DATA, USER_INPUT, _init_and_skip_discovery

API = "custom_components.mikrotik_extended.config_flow.MikrotikAPI"


def _router(connects: bool):
    """A router that lets the account in, or turns it away; the calls are kept in order."""
    api = MagicMock()
    api.connect.return_value = connects
    api.error = None if connects else "wrong_login"
    return api


def _entry(hass):
    entry = MockConfigEntry(domain=DOMAIN, data=ENTRY_DATA, options={}, unique_id="192.168.88.1")
    entry.add_to_hass(hass)
    return entry


def _closed_after_connect(api):
    """One connect, one close, in that order."""
    names = [call[0] for call in api.method_calls if call[0] in ("connect", "close")]
    return names == ["connect", "close"]


@pytest.mark.parametrize("connects", [True, False])
async def test_user_step_closes_its_test_connection(hass, connects):
    with patch(API) as api_cls:
        api = api_cls.return_value = _router(connects)
        result = await _init_and_skip_discovery(hass)
        result = await hass.config_entries.flow.async_configure(result["flow_id"], dict(USER_INPUT))
    assert result["step_id"] == ("basic_options" if connects else "user")
    if not connects:
        assert result["errors"][CONF_HOST] == "wrong_login"
    assert _closed_after_connect(api)


@pytest.mark.parametrize("connects", [True, False])
async def test_reauth_step_closes_its_test_connection(hass, connects):
    entry = _entry(hass)
    with patch(API) as api_cls:
        api = api_cls.return_value = _router(connects)
        result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": config_entries.SOURCE_REAUTH, "entry_id": entry.entry_id}, data=entry.data)
        result = await hass.config_entries.flow.async_configure(result["flow_id"], {CONF_USERNAME: "admin", CONF_PASSWORD: "new"})
    if connects:
        assert result["type"] == FlowResultType.ABORT and result["reason"] == "reauth_successful"
    else:
        assert result["type"] == FlowResultType.FORM and result["errors"][CONF_PASSWORD] == "wrong_login"
    assert _closed_after_connect(api)


@pytest.mark.parametrize("connects", [True, False])
async def test_reconfigure_step_closes_its_test_connection(hass, connects):
    entry = _entry(hass)
    with patch(API) as api_cls:
        api = api_cls.return_value = _router(connects)
        result = await entry.start_reconfigure_flow(hass)
        answer = {CONF_NAME: "Mikrotik", CONF_HOST: "192.168.88.1", CONF_USERNAME: "admin", CONF_PASSWORD: "new", CONF_PORT: 0, "ssl_mode": "none"}
        result = await hass.config_entries.flow.async_configure(result["flow_id"], answer)
    if connects:
        assert result["type"] == FlowResultType.ABORT and result["reason"] == "reconfigure_successful"
    else:
        assert result["type"] == FlowResultType.FORM and result["errors"][CONF_HOST] == "wrong_login"
    assert _closed_after_connect(api)


async def test_the_connection_is_closed_when_the_attempt_blows_up(hass):
    """An error nobody expected still must not leave a session on the router."""
    entry = _entry(hass)
    with patch(API) as api_cls:
        api = api_cls.return_value = _router(True)
        api.connect.side_effect = OSError("network is unreachable")
        result = await entry.start_reconfigure_flow(hass)
        answer = {CONF_NAME: "Mikrotik", CONF_HOST: "192.168.88.1", CONF_USERNAME: "admin", CONF_PASSWORD: "new", CONF_PORT: 0, "ssl_mode": "none"}
        with pytest.raises(OSError):
            await hass.config_entries.flow.async_configure(result["flow_id"], answer)
    assert _closed_after_connect(api)

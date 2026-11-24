"""The api_test service answers an administrator, and nobody else who is logged in."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from homeassistant.core import Context
from homeassistant.exceptions import Unauthorized, UnknownUser

from custom_components.mikrotik_extended import async_setup
from custom_components.mikrotik_extended.const import DOMAIN

from .test_init import _make_entry

HOST = "192.168.88.1"
ROWS = [{".id": "*1", "name": "ether1"}, {".id": "*2", "name": "ether2"}]


async def _setup(hass):
    await async_setup(hass, {})
    entry = _make_entry(hass)
    coordinator = MagicMock()
    coordinator.api.query = MagicMock(return_value=list(ROWS))
    coordinator.config_entry = entry
    coordinator.data = {"interface": {"ether1": {"name": "ether1"}}}
    entry.runtime_data = SimpleNamespace(data_coordinator=coordinator, tracker_coordinator=MagicMock())
    return coordinator


async def _call(hass, context, **data):
    call = {"path": "/interface", **data}
    return await hass.services.async_call(DOMAIN, "api_test", call, blocking=True, return_response=True, context=context)


async def test_an_administrator_gets_the_answer(hass, hass_admin_user):
    coordinator = await _setup(hass)
    response = await _call(hass, Context(user_id=hass_admin_user.id))
    assert response["result"][HOST]["total_returned"] == 2
    coordinator.api.query.assert_called_once_with("/interface")


async def test_a_user_who_is_no_administrator_is_turned_away(hass, hass_read_only_user):
    coordinator = await _setup(hass)
    with pytest.raises(Unauthorized):
        await _call(hass, Context(user_id=hass_read_only_user.id))
    # the router is not even asked
    coordinator.api.query.assert_not_called()


async def test_a_user_who_is_no_administrator_gets_no_store_either(hass, hass_read_only_user):
    await _setup(hass)
    with pytest.raises(Unauthorized):
        await _call(hass, Context(user_id=hass_read_only_user.id), path="interface", coordinator_data=True)


async def test_a_user_nobody_knows_is_turned_away(hass):
    coordinator = await _setup(hass)
    with pytest.raises(UnknownUser):
        await _call(hass, Context(user_id="no-such-user"))
    coordinator.api.query.assert_not_called()


async def test_a_call_without_a_user_still_works(hass):
    """An automation or a script runs without a user, and Home Assistant trusts it."""
    coordinator = await _setup(hass)
    response = await _call(hass, Context())
    assert response["result"][HOST]["total_returned"] == 2
    response = await _call(hass, None, path="interface", coordinator_data=True)
    assert response["result"][HOST]["total_keys"] == 1
    assert coordinator.api.query.call_count == 1

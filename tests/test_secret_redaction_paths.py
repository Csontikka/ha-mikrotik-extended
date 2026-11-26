"""The other ways a secret could leave: the parser lines, a store, the api_test service."""

from __future__ import annotations

import logging
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from custom_components.mikrotik_extended import async_setup
from custom_components.mikrotik_extended.apiparser import parse_api
from custom_components.mikrotik_extended.const import DOMAIN
from custom_components.mikrotik_extended.log_redaction import SECRET_MASK, LogRedactor, is_secret_field, mask_secrets, mask_store
from custom_components.mikrotik_extended.mikrotikapi import QueryRows

from .test_init import _make_entry
from .test_secret_redaction import ENV_VALUE, MARKERS, ROWS

SIM_PIN = 4321
LTE = [{".id": "*5", "name": "lte1", "type": "lte", "apn-profiles": "default", "pin": SIM_PIN, "network-mode": "lte", "band": "", "disabled": False}]
HOST = "192.168.88.1"


def _no_marker(text: str) -> bool:
    return not any(marker in text for marker in MARKERS)


def _rows(path):
    rows = QueryRows(ROWS[path])
    rows.path = path
    return rows


# ---------------------------------------------------------------------------
# the SIM PIN of an LTE interface
# ---------------------------------------------------------------------------


def test_the_sim_pin_is_a_secret():
    assert is_secret_field("pin")
    assert is_secret_field("pin", "/interface/lte")
    # a number, as the API library hands an all-digit value over
    assert mask_secrets(LTE, "/interface/lte")[0]["pin"] == SECRET_MASK
    assert LTE[0]["pin"] == SIM_PIN
    out = LogRedactor(b"salt").redact(f"API query /interface/lte raw response: {LTE}")
    assert str(SIM_PIN) not in out
    assert "'name': 'lte1'" in out


@pytest.mark.parametrize("name", ["sim-pin", "wps-pin", "psk", "wpa-psk", "SIM-PIN"])
def test_a_pin_or_a_shared_key_under_a_longer_name(name):
    assert is_secret_field(name)
    assert mask_secrets({name: 1234, "name": "lte1"}) == {name: SECRET_MASK, "name": "lte1"}


@pytest.mark.parametrize("name", ["ping", "pin-count", "mapping", "spin", "pskov", "pins"])
def test_names_that_only_look_like_the_pin(name):
    assert not is_secret_field(name)


# ---------------------------------------------------------------------------
# the parser names the menu in its lines, for whoever masks them once more
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("path", ["/system/script/environment", "/system/script"])
def test_parser_lines_name_the_menu(path, caplog):
    with caplog.at_level(logging.DEBUG, logger="custom_components.mikrotik_extended.apiparser"):
        parse_api(data={}, source=_rows(path), key=".id", vals=[{"name": ".id"}])
    assert f"Processing source from {path}: " in caplog.text
    assert f"Processing entry from {path}: " in caplog.text


def test_parser_lines_of_rows_without_a_menu_read_as_before(caplog):
    with caplog.at_level(logging.DEBUG, logger="custom_components.mikrotik_extended.apiparser"):
        parse_api(data={}, source=[{"name": "a"}], key="name", vals=[{"name": "name"}])
    assert "Processing source [{'name': 'a'}]" in caplog.text
    assert "Processing entry {'name': 'a'}" in caplog.text


@pytest.mark.parametrize("kind", ["source", "entry"])
@pytest.mark.parametrize("path", ["/system/script/environment", "/system/script"])
def test_the_second_net_knows_the_menu_of_a_parser_line(kind, path):
    """A parser line that reached the buffer with its secret still in it is caught by the menu it names."""
    row = ROWS[path][0]
    shown = [row] if kind == "source" else row
    line = f"2026-11-24 17:13:45,512 DEBUG custom_components.mikrotik_extended.apiparser: Processing {kind} from {path}: {shown}"
    out = LogRedactor(b"salt").redact(line)
    assert _no_marker(out)
    assert SECRET_MASK in out
    assert f"Processing {kind} from {path}: " in out


def test_the_second_net_leaves_a_plain_reading_in_a_parser_line():
    line = "Processing entry from /system/health: {'name': 'temperature', 'value': 45}"
    assert LogRedactor(b"salt").redact(line) == line
    line = "Processing entry {'name': 'temperature', 'value': 45, 'source': 'dhcp'}"
    assert LogRedactor(b"salt").redact(line) == line


# ---------------------------------------------------------------------------
# a store of the coordinator
# ---------------------------------------------------------------------------


def test_environment_store_is_handed_out_without_its_values():
    store = {"token": {"name": "token", "value": ENV_VALUE}, "empty": {"name": "empty", "value": ""}}
    masked = mask_store("environment", store)
    assert masked == {"token": {"name": "token", "value": SECRET_MASK}, "empty": {"name": "empty", "value": ""}}
    assert store["token"]["value"] == ENV_VALUE


def test_other_stores_keep_their_readings():
    health = {"temperature": {"name": "temperature", "value": 45}}
    host = {"h1": {"host-name": "laptop", "source": "dhcp"}}
    assert mask_store("health", health) is health
    assert mask_store("host", host) is host
    # not every store is a dict of rows
    assert mask_store("resource", {"cpu-load": 3, "version": "7.25.1"}) == {"cpu-load": 3, "version": "7.25.1"}
    assert mask_store("access", ["read", "write"]) == ["read", "write"]
    assert mask_store("environment", None) is None


def test_a_secret_field_is_masked_in_any_store():
    """Should a store ever keep a password or a key, it does not leave with it."""
    store = {"*1": {"name": "peer", "preshared-key": "abc=", "public-key": "PUB="}}
    assert mask_store("wireguard_peers", store) == {"*1": {"name": "peer", "preshared-key": SECRET_MASK, "public-key": "PUB="}}


# ---------------------------------------------------------------------------
# the api_test service
# ---------------------------------------------------------------------------


async def _call(hass, coordinator_data, path, *, query=None, data=None):
    await async_setup(hass, {})
    entry = _make_entry(hass)
    coordinator = MagicMock()
    coordinator.api = MagicMock()
    coordinator.api.query = MagicMock(return_value=query)
    coordinator.config_entry = entry
    coordinator.data = data
    entry.runtime_data = SimpleNamespace(data_coordinator=coordinator, tracker_coordinator=MagicMock())
    call = {"path": path, "coordinator_data": coordinator_data}
    response = await hass.services.async_call(DOMAIN, "api_test", call, blocking=True, return_response=True)
    return response["result"][HOST], coordinator


@pytest.mark.parametrize("path", sorted(ROWS))
async def test_api_test_answers_a_raw_query_without_secrets(hass, path):
    result, coordinator = await _call(hass, False, path, query=_rows(path))
    coordinator.api.query.assert_called_once_with(path)
    assert result["total_returned"] == 1
    assert _no_marker(str(result))
    assert SECRET_MASK in str(result)
    # the rest of the row is what the user asked for
    assert result["items"][0][".id"] == ROWS[path][0][".id"]
    assert set(result["items"][0]) == set(ROWS[path][0])


@pytest.mark.parametrize("path", ["system/script/environment", "/system/script/environment/", " /system/script/environment "])
async def test_api_test_knows_the_menu_however_the_path_is_written(hass, path):
    result, _ = await _call(hass, False, path, query=list(ROWS["/system/script/environment"]))
    assert result["items"][0]["value"] == SECRET_MASK


async def test_api_test_keeps_plain_readings_of_a_raw_query(hass):
    result, _ = await _call(hass, False, "/system/health", query=[{"name": "temperature", "value": 45}])
    assert result["items"] == [{"name": "temperature", "value": "45"}]


async def test_api_test_answers_the_environment_store_without_its_values(hass):
    data = {"environment": {"token": {"name": "token", "value": ENV_VALUE}}, "health": {"temperature": {"name": "temperature", "value": 45}}}
    result, _ = await _call(hass, True, "environment", data=data)
    assert result == {"total_keys": 1, "items": {"token": {"name": "token", "value": SECRET_MASK}}}
    # the sensor still reads the value
    assert data["environment"]["token"]["value"] == ENV_VALUE


async def test_api_test_keeps_plain_readings_of_a_store(hass):
    data = {"health": {"temperature": {"name": "temperature", "value": 45}}}
    result, _ = await _call(hass, True, "health", data=data)
    assert result["items"] == {"temperature": {"name": "temperature", "value": "45"}}


async def test_api_test_still_reports_a_failed_query(hass):
    result, _ = await _call(hass, False, "/ppp/secret", query=None)
    assert "error" in result


class TestFieldsOfMenusOnlyApiTestReads:
    """The api_test service reads any menu, so the rules cannot stop at the menus the integration polls."""

    @pytest.mark.parametrize(
        "name",
        ["on-up", "on-down", "on-login", "on-logout", "on-master", "on-backup", "auth-key", "enc-key", "tcp-md5-key", "static-key-0", "static-key-3", "management-protection-key", "secrets", "On-Up", "AUTH-KEY"],
    )
    def test_scripts_and_keys_are_secret_in_every_menu(self, name):
        assert is_secret_field(name)

    @pytest.mark.parametrize("name", ["public-key", "client-public-key", "key", "keepalive", "one", "keys", "monkey"])
    def test_what_only_looks_like_one_is_not(self, name):
        assert not is_secret_field(name)

    def test_a_key_of_its_own_menu_only(self):
        assert is_secret_field("key", "/system/ntp/key")
        assert is_secret_field("value", "/container/envs")
        assert not is_secret_field("value", "/system/health")

    @pytest.mark.parametrize(
        ("path", "field"),
        [("/ipv6/dhcp-client", "script"), ("/routing/rip/keys", "key"), ("/snmp/community", "name")],
    )
    def test_fields_read_on_a_router_that_are_secret_in_their_menu_only(self, path, field):
        row = {".id": "*1", field: "CANARY", "comment": "kept"}
        assert mask_secrets([row], path) == [{".id": "*1", field: SECRET_MASK, "comment": "kept"}]
        assert not is_secret_field(field)
        assert not is_secret_field(field, "/interface")
        out = LogRedactor(b"salt").redact(f"API query {path} raw response: [{row}]")
        assert "CANARY" not in out
        assert "'comment': 'kept'" in out

    def test_the_passwords_of_an_snmp_community_go_by_their_name(self):
        row = {"name": "CANARY", "authentication-password": "CANARY", "encryption-password": "CANARY", "addresses": "::/0"}
        assert mask_secrets(row, "/snmp/community") == {"name": SECRET_MASK, "authentication-password": SECRET_MASK, "encryption-password": SECRET_MASK, "addresses": "::/0"}

    @pytest.mark.parametrize(
        "path",
        [
            "/System/Script",
            "/system//script",
            "system/script",
            "/system/script/",
            " /system/script ",
            "/system/script/print",
            "/system script",
            "system script print",
            "/system/script/getall",
            "/system script print where name=x",
        ],
    )
    def test_the_menu_is_known_however_it_is_written(self, path):
        assert mask_secrets([{"name": "s", "source": "CANARY"}], path) == [{"name": "s", "source": SECRET_MASK}]


class TestStoreOfMixedShape:
    def test_a_secret_beside_rows_is_masked_too(self):
        store = {"password": "CANARY", "row": {"name": "n", "private-key": "CANARY"}, "count": 2}

        assert mask_store("x", store) == {"password": SECRET_MASK, "row": {"name": "n", "private-key": SECRET_MASK}, "count": 2}
        assert store["password"] == "CANARY"

    def test_a_store_without_a_secret_is_returned_as_it_is(self):
        store = {"a": {"name": "n"}, "count": 2}

        assert mask_store("x", store) is store

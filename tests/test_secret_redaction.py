"""Secrets stay out of the debug log and out of a diagnostics download.

The rows below are what RouterOS 7.25.1 sends for the menus the integration
reads, taken from a test router with made-up values in the secret fields.
"""

from __future__ import annotations

import logging
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from custom_components.mikrotik_extended.apiparser import parse_api
from custom_components.mikrotik_extended.diagnostics import async_get_config_entry_diagnostics
from custom_components.mikrotik_extended.log_redaction import SECRET_MASK, LogRedactor, is_secret_field, mask_secrets
from custom_components.mikrotik_extended.mikrotikapi import MikrotikAPI, QueryRows

PPP_PASSWORD = "MARKER-PPP-PASSWORD"
PSK = "TUFSS0VSLVBTSy14eHh4eHh4eHh4eHh4eHh4eHh4eHg="
PEER_PRIVATE = "TUFSS0VSLVBSSVYteXl5eXl5eXl5eXl5eXl5eXl5eXk="
SCRIPT_SOURCE = ':global token "MARKER-ENV-VALUE"; :log info "MARKER-SCRIPT-SOURCE"'
ENV_VALUE = "MARKER-ENV-VALUE"
UP_SCRIPT = ':log info "MARKER-NETWATCH-UP"'
DOWN_SCRIPT = ':log info "MARKER-NETWATCH-DOWN"'
ON_EVENT = ':log info "MARKER-SCHEDULER-EVENT"'
MARKERS = (PPP_PASSWORD, PSK, PEER_PRIVATE, "MARKER-SCRIPT-SOURCE", ENV_VALUE, "MARKER-NETWATCH-UP", "MARKER-NETWATCH-DOWN", "MARKER-SCHEDULER-EVENT")

ROWS = {
    "/ppp/secret": [
        {
            ".id": "*1",
            "name": "probe",
            "service": "pptp",
            "caller-id": "",
            "password": PPP_PASSWORD,
            "profile": "default",
            "routes": "",
            "ipv6-routes": "",
            "limit-bytes-in": 0,
            "limit-bytes-out": 0,
            "last-logged-out": "1970-01-01 00:00:00",
            "disabled": False,
            "comment": "probe",
        }
    ],
    "/interface/wireguard/peers": [
        {
            ".id": "*8",
            "interface": "wg-probe",
            "name": "probe-peer",
            "public-key": "eWhzztdXSPqMLQQR2Cq248xKPc+7dDCY9htNS7L/XWU=",
            "private-key": PEER_PRIVATE,
            "endpoint-address": "",
            "endpoint-port": 0,
            "current-endpoint-address": "",
            "current-endpoint-port": 0,
            "allowed-address": "192.0.2.48/32",
            "preshared-key": PSK,
            "client-endpoint": "",
            "client-allowed-address": "",
            "rx": 0,
            "tx": 0,
            "dynamic": False,
            "disabled": False,
            "comment": "probe",
        }
    ],
    "/system/script": [
        {
            ".id": "*3",
            "name": "probe",
            "owner": "admin",
            "policy": "ftp,reboot,read,write,policy,test,password,sniff,sensitive,romon",
            "dont-require-permissions": False,
            "last-started": "2026-11-24 17:02:11",
            "run-count": 1,
            "source": SCRIPT_SOURCE,
            "invalid": False,
            "comment": "probe",
        }
    ],
    "/system/script/environment": [{".id": "*1", "name": "token", "value": ENV_VALUE}],
    "/tool/netwatch": [
        {
            ".id": "*B",
            "name": "",
            "type": "simple",
            "host": "192.0.2.48",
            "interval": "1m",
            "status": "down",
            "since": "2026-11-24 17:01:40",
            "up-script": UP_SCRIPT,
            "down-script": DOWN_SCRIPT,
            "comment": "probe",
        }
    ],
    "/system/scheduler": [{".id": "*2", "name": "probe", "interval": "1d", "on-event": ON_EVENT, "owner": "admin", "disabled": True}],
}


def _no_marker(text: str) -> bool:
    return not any(marker in text for marker in MARKERS)


# ---------------------------------------------------------------------------
# which fields are secrets
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("name", ["password", "preshared-key", "private-key", "on-event", "up-script", "down-script", "security.passphrase", "wpa2-pre-shared-key", "authentication-password", "ipsec-secret", "Password"])
def test_secret_in_every_menu(name):
    assert is_secret_field(name)
    assert is_secret_field(name, "/interface")


@pytest.mark.parametrize("name", ["public-key", "name", "comment", "value", "source", "script", "host", "allowed-address", "key", "ssid"])
def test_not_a_secret_without_its_menu(name):
    assert not is_secret_field(name)
    assert not is_secret_field(name, "/system/health")


def test_secret_in_one_menu_only():
    assert is_secret_field("value", "/system/script/environment")
    assert is_secret_field("source", "/system/script")
    assert is_secret_field("script", "/ip/dhcp-client")
    assert not is_secret_field("source", "/ip/arp")
    assert not is_secret_field("value", "/system/health")


# ---------------------------------------------------------------------------
# the rows as they go to the log
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("path", sorted(ROWS))
def test_masked_rows_hold_no_secret_and_leave_the_original_alone(path):
    rows = ROWS[path]
    before = repr(rows)
    masked = mask_secrets(rows, path)
    assert _no_marker(repr(masked))
    assert SECRET_MASK in repr(masked)
    # everything that is not a secret is still there to debug with
    assert masked[0][".id"] == rows[0][".id"]
    assert set(masked[0]) == set(rows[0])
    assert repr(rows) == before


def test_masking_keeps_what_is_not_secret():
    health = [{".id": "*1", "name": "temperature", "value": 45, "type": "C"}]
    arp = [{"address": "192.0.2.5", "source": "dhcp"}]
    assert mask_secrets(health, "/system/health") == health
    assert mask_secrets(arp, "/ip/arp") == arp
    # a row without a secret is not even copied
    assert mask_secrets(health, "/system/health")[0] is health[0]
    assert mask_secrets(ROWS["/interface/wireguard/peers"], "/interface/wireguard/peers")[0]["public-key"] == ROWS["/interface/wireguard/peers"][0]["public-key"]


def test_an_empty_secret_stays_empty():
    rows = [{"name": "a", "password": "", "preshared-key": None}]
    assert mask_secrets(rows, "/ppp/secret") == rows


def test_the_placeholder_of_an_account_without_sensitive_is_masked_too():
    """Without the sensitive policy the router sends five stars; nothing is told apart."""
    assert mask_secrets([{"name": "a", "password": "*****"}], "/ppp/secret")[0]["password"] == SECRET_MASK


# ---------------------------------------------------------------------------
# the API layer
# ---------------------------------------------------------------------------


def _api():
    return MikrotikAPI("192.0.2.1", "admin", "pass")


@pytest.mark.parametrize("path", sorted(ROWS))
def test_raw_response_is_logged_without_secrets(path, caplog):
    api = _api()
    with caplog.at_level(logging.DEBUG, logger="custom_components.mikrotik_extended.mikrotikapi"):
        rows, missing = api._materialize_list(iter(ROWS[path]), path)
    assert missing is False
    assert f"API query {path} raw response" in caplog.text
    assert _no_marker(caplog.text)
    # what the coordinator works with is untouched
    assert rows == ROWS[path]
    assert isinstance(rows, QueryRows)
    assert rows.path == path


def test_no_masking_work_when_debug_is_off(caplog):
    api = _api()
    with caplog.at_level(logging.INFO, logger="custom_components.mikrotik_extended.mikrotikapi"), patch("custom_components.mikrotik_extended.mikrotikapi.mask_secrets") as mask:
        rows, _ = api._materialize_list(iter(ROWS["/ppp/secret"]), "/ppp/secret")
    mask.assert_not_called()
    assert rows == ROWS["/ppp/secret"]
    assert "raw response" not in caplog.text


def test_an_empty_answer_still_carries_its_menu():
    rows, missing = _api()._materialize_list(iter([]), "/ppp/secret")
    assert rows == [] and missing is False
    assert rows.path == "/ppp/secret"


# ---------------------------------------------------------------------------
# the parser
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("path", ["/system/script/environment", "/system/script", "/ppp/secret", "/interface/wireguard/peers", "/tool/netwatch"])
def test_parser_logs_no_secret(path, caplog):
    source = QueryRows(ROWS[path])
    source.path = path
    with caplog.at_level(logging.DEBUG, logger="custom_components.mikrotik_extended.apiparser"):
        parse_api(data={}, source=source, key=".id", vals=[{"name": ".id"}, {"name": "name"}])
    assert "Processing source" in caplog.text
    assert "Processing entry" in caplog.text
    assert _no_marker(caplog.text)


def test_parser_still_hands_the_value_to_the_sensor(caplog):
    """The environment sensor shows the value; only the log leaves it out."""
    source = QueryRows(ROWS["/system/script/environment"])
    source.path = "/system/script/environment"
    with caplog.at_level(logging.DEBUG, logger="custom_components.mikrotik_extended.apiparser"):
        data = parse_api(data={}, source=source, key="name", vals=[{"name": "name"}, {"name": "value"}])
    assert data == {"token": {"name": "token", "value": ENV_VALUE}}
    assert ENV_VALUE not in caplog.text


# ---------------------------------------------------------------------------
# the second net: a line that reached the buffer unmasked
# ---------------------------------------------------------------------------


def _redactor():
    return LogRedactor(b"fixed-test-salt")


@pytest.mark.parametrize("path", sorted(ROWS))
def test_redactor_masks_secrets_in_a_raw_response_line(path):
    line = f"2026-11-24 17:13:45,512 DEBUG custom_components.mikrotik_extended.mikrotikapi: API query {path} raw response: {ROWS[path]}"
    out = _redactor().redact(line)
    assert _no_marker(out)
    assert SECRET_MASK in out
    assert f"API query {path} raw response" in out
    assert "'.id'" in out


def test_redactor_masks_a_value_with_quotes_and_line_breaks():
    source = ':log info "it\'s MARKER-SCRIPT-SOURCE"\n:local a \'x\'\n:put "done"'
    rows = [{"name": "probe", "source": source, "run-count": 3}]
    line = f"API query /system/script raw response: {rows}"
    # the repr of this value uses escapes and both kinds of quotes
    assert "\\n" in line and "\\'" in line
    out = _redactor().redact(line)
    assert _no_marker(out)
    assert "done" not in out
    assert out.endswith("'source': '**REDACTED**', 'run-count': 3}]")


def test_redactor_masks_a_secret_spread_over_real_lines():
    """A message can hold real line breaks, not only escaped ones."""
    line = "netwatch probe changed: {'host': '192.0.2.48', 'up-script': ':log info \"MARKER-NETWATCH-UP\"\n:delay 1s', 'status': 'up'}"
    out = _redactor().redact(line)
    assert _no_marker(out)
    assert ":delay 1s" not in out
    assert "'status': 'up'" in out


def test_redactor_masks_a_number_and_a_double_quoted_key():
    out = _redactor().redact("""row {'name': 'a', 'password': 12345678, "preshared-key": "MARKER-PPP-PASSWORD"}""")
    assert "12345678" not in out
    assert _no_marker(out)


def test_redactor_leaves_common_names_alone_outside_their_menu():
    line = "API query /system/health raw response: [{'name': 'temperature', 'value': 45}]"
    assert _redactor().redact(line) == line
    line = "API query /ip/arp raw response: [{'interface': 'bridge', 'source': 'dhcp'}]"
    assert _redactor().redact(line) == line
    out = _redactor().redact("API query /interface/wireguard/peers raw response: [{'public-key': 'eWhzztdXSPqMLQQR2Cq248xKPc+7dDCY9htNS7L/XWU='}]")
    assert "eWhzztdXSPqMLQQR2Cq248xKPc+7dDCY9htNS7L/XWU=" in out


def test_redactor_keeps_an_empty_secret_readable():
    line = "API query /ppp/secret raw response: [{'name': 'a', 'password': ''}]"
    assert _redactor().redact(line) == line


# ---------------------------------------------------------------------------
# the download
# ---------------------------------------------------------------------------


def _entry(data):
    entry = MagicMock()
    entry.data = {"host": "192.0.2.1", "password": "secret", "username": "admin"}
    entry.options = {}
    entry.runtime_data = SimpleNamespace(data_coordinator=SimpleNamespace(data=data), tracker_coordinator=SimpleNamespace(data={}))
    return entry


async def test_diagnostics_leaves_out_the_environment_values(hass):
    data = {
        "environment": {"token": {"name": "token", "value": ENV_VALUE}, "empty": {"name": "empty", "value": ""}},
        "health": {"temperature": {"name": "temperature", "value": 45}},
        "host": {"h1": {"host-name": "laptop", "source": "dhcp"}},
    }
    result = await async_get_config_entry_diagnostics(hass, _entry(data))
    assert result["data"]["environment"]["token"] == {"name": "token", "value": SECRET_MASK}
    assert result["data"]["environment"]["empty"]["value"] == ""
    # a field of the same name in another store is not a secret
    assert result["data"]["health"]["temperature"]["value"] == 45
    assert result["data"]["host"]["h1"]["source"] == "dhcp"
    # the sensor reads the store, which must keep the value
    assert data["environment"]["token"]["value"] == ENV_VALUE


async def test_diagnostics_masks_secrets_in_captured_log_lines(hass):
    from custom_components.mikrotik_extended import _LOG_BUFFER

    _LOG_BUFFER.clear()
    for path, rows in ROWS.items():
        _LOG_BUFFER.append(f"2026-11-24 17:13:45,512 DEBUG custom_components.mikrotik_extended.mikrotikapi: API query {path} raw response: {rows}")
    try:
        result = await async_get_config_entry_diagnostics(hass, _entry({}))
    finally:
        _LOG_BUFFER.clear()
    joined = "\n".join(result["logs"])
    assert len(result["logs"]) == len(ROWS)
    assert _no_marker(joined)
    assert joined.count(SECRET_MASK) == 8


async def test_a_debug_cycle_leaves_no_secret_in_the_buffer(hass):
    """End to end: the rows are read with debug on, and nothing of them reaches the download."""
    from custom_components.mikrotik_extended import _LOG_BUFFER

    logger = logging.getLogger("custom_components.mikrotik_extended")
    level = logger.level
    _LOG_BUFFER.clear()
    logger.setLevel(logging.DEBUG)
    try:
        api = _api()
        for path, rows in ROWS.items():
            read, _ = api._materialize_list(iter(rows), path)
            parse_api(data={}, source=read, key=".id", vals=[{"name": ".id"}])
        captured = "\n".join(_LOG_BUFFER)
        result = await async_get_config_entry_diagnostics(hass, _entry({}))
    finally:
        logger.setLevel(level)
        _LOG_BUFFER.clear()
    assert "raw response" in captured and "Processing entry" in captured
    assert _no_marker(captured)
    assert _no_marker("\n".join(result["logs"]))

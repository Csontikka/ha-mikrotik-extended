"""Tests for diagnostics log redaction."""

from __future__ import annotations

from custom_components.mikrotik_extended.log_redaction import LogRedactor

SALT = b"fixed-test-salt"


def _r():
    return LogRedactor(SALT)


def test_ipv4_keeps_first_and_last_octet():
    out = _r().redact("Ping host: 192.168.1.42")
    assert "192.168.1.42" not in out
    assert out.startswith("Ping host: 192.x.x.42#")


def test_mac_keeps_first_and_last_octet_and_separator():
    out = _r().redact("registration mac D8:C8:0C:82:BA:AB done")
    assert "D8:C8:0C:82:BA:AB" not in out
    assert "D8:xx:xx:xx:xx:AB#" in out


def test_dash_separated_mac():
    out = _r().redact("mac AA-BB-CC-DD-EE-01")
    assert "AA-BB-CC-DD-EE-01" not in out
    assert "AA-xx-xx-xx-xx-01#" in out


def test_ipv6_with_double_colon():
    out = _r().redact("neighbor fe80::1ff:fe23:4567:890a up")
    assert "fe80::1ff:fe23:4567:890a" not in out
    assert "890a#" in out


def test_keyed_serial_and_ssid_masked():
    line = "resp: [{'serial-number': 'HGR81234ABC', 'ssid': 'MyHomeWifi'}]"
    out = _r().redact(line)
    assert "HGR81234ABC" not in out
    assert "MyHomeWifi" not in out
    assert "HG...BC#" in out
    assert "My...fi#" in out


def test_no_sensitive_value_survives_full_response():
    resp = "[{'address':'192.168.1.42','mac-address':'AA:BB:CC:DD:EE:01','serial-number':'HGR81234ABC','ssid':'HomeNet'}]"
    out = _r().redact(f"API query /ip/arp raw response: {resp}")
    for leak in ("192.168.1.42", "AA:BB:CC:DD:EE:01", "HGR81234ABC", "HomeNet"):
        assert leak not in out


def test_timestamp_and_logger_name_preserved():
    line = "2026-07-06 00:47:11,038 DEBUG custom_components.mikrotik_extended.coordinator: cycle complete"
    assert _r().redact(line) == line


def test_correlation_stable_within_dump():
    r = _r()
    a = r.redact("192.168.1.42")
    b = r.redact("192.168.1.42")
    assert a == b


def test_different_values_get_different_tags():
    r = _r()
    assert r.redact("192.168.1.42") != r.redact("192.168.1.43")


def test_salt_changes_tag_between_dumps():
    a = LogRedactor(b"salt-a").redact("192.168.1.42")
    b = LogRedactor(b"salt-b").redact("192.168.1.42")
    assert a != b


def test_empty_and_plain_lines_untouched():
    r = _r()
    assert r.redact("") == ""
    assert r.redact("just a message") == "just a message"

"""Partial redaction of captured log lines for diagnostics downloads.

The diagnostics ring buffer may contain raw API responses when the user has
enabled debug logging. Those downloads are frequently attached to public
issues, so network identifiers are masked here before they leave the box.

The masking keeps the log structure and, importantly, correlation intact:
the first and last element of each identifier stay visible, the middle is
replaced, and a short hash that is stable within a single diagnostics dump is
appended. The same address therefore carries the same tag across lines
(you can still tell "this is the same device"), but the value cannot be
recovered and the tag differs between dumps (a fresh salt per download).

Secrets are a different matter. A password, a preshared key or the text of a
script is never shown in part and never tagged: a tag is a hash of the value,
and a weak password can be guessed back from one. Those values are replaced
whole, here and already where the raw API response is written to the log.
"""

from __future__ import annotations

import hashlib
import hmac
import re

# IPv4: keep first and last octet -> 192.x.x.42
_IPV4_RE = re.compile(r"\b(\d{1,3})\.\d{1,3}\.\d{1,3}\.(\d{1,3})\b")

# MAC (colon or dash separated): keep first and last octet -> AA:xx:xx:xx:xx:01
_MAC_RE = re.compile(r"\b([0-9A-Fa-f]{2})([:-])(?:[0-9A-Fa-f]{2}\2){4}([0-9A-Fa-f]{2})\b")

# IPv6 in full form: exactly eight groups, so a clock time ("20:31:53") or a
# MAC can never match it.
_IPV6_FULL_RE = re.compile(r"\b(?:[0-9A-Fa-f]{1,4}:){7}[0-9A-Fa-f]{1,4}\b")

# IPv6 containing "::" (unambiguous). Both sides are optional and matched in
# full, so a bare prefix such as "2001:db8:1234:5678::" is covered as well: a
# delegated prefix is globally unique, which makes it more identifying than
# any private IPv4.
_IPV6_RE = re.compile(r"\b(?:[0-9A-Fa-f]{1,4}:)*[0-9A-Fa-f]{1,4}::(?:[0-9A-Fa-f]{1,4}:)*[0-9A-Fa-f]{0,4}|::[0-9A-Fa-f]{1,4}")

# Free-text sensitive values that appear as 'key': 'value' in raw API reprs.
_KEYED = ("serial-number", "sfp-vendor-serial", "ssid", "caller-id", "imei", "imsi", "iccid", "uicc")
_KEYED_RE = re.compile(r"('(?:" + "|".join(re.escape(k) for k in _KEYED) + r")'\s*:\s*')([^']*)(')")
# The API library turns an all-digit value into a number, so an IMEI or an
# ICCID shows up without quotes in a repr.
_KEYED_BARE_RE = re.compile(r"('(?:" + "|".join(re.escape(k) for k in _KEYED) + r")'\s*:\s*)(\d+)()")


# What takes the place of a secret, in the log and in a diagnostics download.
SECRET_MASK = "**REDACTED**"

# Fields that hold a secret in every menu. Read from RouterOS 7.25.1: a PPP
# secret sends "password", a WireGuard peer "preshared-key" and "private-key",
# and scripts kept inside another object ("on-event" of a scheduler, the up
# and down script of a netwatch probe) are sent in full, to any account that
# may read the menu.
_SECRET_FIELDS = frozenset(
    {
        "password",
        "passphrase",
        "secret",
        "preshared-key",
        "pre-shared-key",
        "private-key",
        # The SIM PIN of an LTE interface.
        "pin",
        # The shared secrets of RoMON.
        "secrets",
        "on-event",
        "up-script",
        "down-script",
        "test-script",
        "lease-script",
    }
)
# The same kind of field under a longer name: "security.passphrase" of a wifi
# interface, "wpa2-pre-shared-key", "authentication-password", "ipsec-secret".
_SECRET_SUFFIXES = ("password", "passphrase", "-secret", "preshared-key", "pre-shared-key", "private-key")
# Any other key: "auth-key" and "enc-key" of an IPsec SA, "tcp-md5-key" of a
# BGP connection, "static-key-0" of a wireless security profile. The
# integration reads none of these menus, the api_test service reads whatever
# it is asked for. A public key is not a secret.
_KEY_FIELD_RE = re.compile(r"(?:^|-)key(?:-\d+)?$")
# Names too common to hide everywhere: "source" also says where a host was
# seen, "value" is a health reading. They are secret in one menu only.
_SECRET_FIELDS_BY_PATH = {
    "/system/script": frozenset({"source"}),
    "/system/script/environment": frozenset({"value"}),
    "/ip/dhcp-client": frozenset({"script"}),
    "/ipv6/dhcp-client": frozenset({"script"}),
    "/container/envs": frozenset({"value"}),
    "/system/ntp/key": frozenset({"key"}),
    "/routing/rip/keys": frozenset({"key"}),
    # A community name is all it takes to read the router over SNMP v1 and v2c.
    "/snmp/community": frozenset({"name"}),
}


def _menu(path: str) -> str:
    """A menu path as the table above spells it, however the caller wrote it."""
    return "/" + "/".join(part for part in path.strip().lower().split("/") if part)


def is_secret_field(name, path: str | None = None) -> bool:
    """Tell whether a field of an API row holds a secret, in the menu it was read from."""
    name = str(name).lower()
    if name in _SECRET_FIELDS or name.endswith(_SECRET_SUFFIXES):
        return True
    # A script kept inside another object: "on-up" of a PPP profile,
    # "on-login" of a hotspot user profile, "on-master" of a VRRP interface.
    if name.startswith("on-"):
        return True
    if name != "key" and not name.endswith("public-key") and _KEY_FIELD_RE.search(name):
        return True
    return path is not None and name in _SECRET_FIELDS_BY_PATH.get(_menu(path), ())


def mask_secrets(rows, path: str | None = None):
    """Return API rows fit for a log line: every secret value replaced.

    The rows themselves are left alone, a row is only copied when it holds a
    secret. An empty value stays empty, so the log still tells a set password
    from none.
    """
    if isinstance(rows, dict):
        if not any(value not in ("", None) and is_secret_field(key, path) for key, value in rows.items()):
            return rows
        return {key: SECRET_MASK if value not in ("", None) and is_secret_field(key, path) else value for key, value in rows.items()}
    if isinstance(rows, (list, tuple)):
        return [mask_secrets(row, path) for row in rows]
    return rows


def mask_store(name: str, rows):
    """Return one store of the coordinator fit to be handed out: its secrets replaced.

    A store is what the entities read, so it holds what the sensors show, and
    for the script environment that is the value of every global variable.
    The owner of the router sees those in Home Assistant; a diagnostics
    download or the answer of the api_test service gets pasted into issues.
    The store is not changed, and a store without a secret is returned as it is.
    """
    if not isinstance(rows, dict):
        return rows
    path = "/system/script/environment" if name == "environment" else None
    # A store is either one row itself, like the resource readings, or rows
    # by key. Both are covered at once: a value that is not a row is judged
    # by the name it is kept under.
    top = mask_secrets(rows, None)

    def masked(key):
        return mask_secrets(rows[key], path) if isinstance(rows[key], dict) else top[key]

    out = {key: masked(key) for key in rows}
    return rows if all(out[key] is rows[key] for key in rows) else out


# A 'key': value pair of a Python repr, the value being a quoted text (with
# either quote, escapes and line breaks included) or a bare number or word.
_PAIR_RE = re.compile(
    r"""(?P<lead>(?P<kq>['"])(?P<key>[^'"\\]{1,64})(?P=kq)\s*:\s*)"""
    r"""(?P<value>'(?:[^'\\]|\\.)*'|"(?:[^"\\]|\\.)*"|[^\s,}\]]+)""",
    re.DOTALL,
)
# The menu the rows of a line were read from, as mikrotikapi words its raw
# response line and the parser the lines of the rows it works through.
_QUERY_PATH_RE = re.compile(r"(?:API query (/\S+) raw response|Processing (?:source|entry) from (/\S+): )")


class LogRedactor:
    """Mask network identifiers in log lines, correlation-stable within a dump."""

    def __init__(self, salt: bytes) -> None:
        self._salt = salt

    def _tag(self, value: str) -> str:
        return hmac.new(self._salt, value.encode(), hashlib.sha256).hexdigest()[:3]

    def _ipv4(self, m: re.Match) -> str:
        # A leading 0 ("any", "this network") or anything from 224 up
        # (multicast, reserved, broadcast, and the 255.x.x.x netmasks) never
        # points at a device, so masking those would only cost readability.
        first = int(m.group(1))
        if first == 0 or first >= 224:
            return m.group(0)
        return f"{m.group(1)}.x.x.{m.group(2)}#{self._tag(m.group(0))}"

    def _mac(self, m: re.Match) -> str:
        sep = m.group(2)
        # Tag the upper-cased address so the same MAC written in either case
        # carries one tag: a dump has to be able to show that they are one
        # device, not two.
        return f"{m.group(1)}{sep}xx{sep}xx{sep}xx{sep}xx{sep}{m.group(3)}#{self._tag(m.group(0).upper())}"

    def _ipv6(self, m: re.Match) -> str:
        # Keep the first and last group, same as for IPv4 and MAC, and drop
        # everything in between whichever form the address was written in.
        text = m.group(0)
        groups = [g for g in text.split(":") if g]
        head = groups[0] if groups else ""
        tail = groups[-1] if len(groups) > 1 else ""
        body = f"{head}:…:{tail}" if tail else f"{head}:…"
        return f"{body}#{self._tag(text.upper())}"

    def _keyed(self, m: re.Match) -> str:
        value = m.group(2)
        masked = "…" if len(value) <= 4 else f"{value[:2]}...{value[-2:]}"
        return f"{m.group(1)}{masked}#{self._tag(value)}{m.group(3)}"

    @staticmethod
    def _secrets(line: str) -> str:
        """Replace the value of every secret field in a line that holds a repr of API rows.

        The raw response is masked before it is logged; this is the second
        net, for a line that reached the buffer some other way.
        """
        if "':" not in line and '":' not in line:
            return line
        found = _QUERY_PATH_RE.search(line)
        path = (found.group(1) or found.group(2)) if found else None

        def swap(m: re.Match) -> str:
            value = m.group("value")
            if value in ("''", '""', "None") or not is_secret_field(m.group("key"), path):
                return m.group(0)
            return f"{m.group('lead')}'{SECRET_MASK}'"

        return _PAIR_RE.sub(swap, line)

    def redact(self, line: str) -> str:
        line = self._secrets(line)
        # MAC before IPv6 (both use colons); the placeholders contain non-hex
        # "x" so already-masked spans are not re-matched afterwards.
        line = _MAC_RE.sub(self._mac, line)
        line = _IPV6_FULL_RE.sub(self._ipv6, line)
        line = _IPV6_RE.sub(self._ipv6, line)
        line = _IPV4_RE.sub(self._ipv4, line)
        line = _KEYED_RE.sub(self._keyed, line)
        line = _KEYED_BARE_RE.sub(self._keyed, line)
        return line

    def redact_data(self, data):
        """Mask addresses anywhere in a data structure, keys included.

        ``async_redact_data`` only rewrites values, and only for the field
        names it is given. That missed two things (issue 25): several stores
        are keyed by a MAC or an IP, so every one of them went out as a plain
        object key, and derived fields that spell the name differently
        ("mac", "host_name", "public-address") were never covered at all.
        Matching on the shape of the value instead of on a field name catches
        both, and keeps catching them when new fields appear. Values that hold
        no address are returned untouched, and the masking is the same
        correlation-stable one used for log lines, so a dump stays readable.
        """
        if isinstance(data, dict):
            return {self.redact(str(key)): self.redact_data(value) for key, value in data.items()}
        if isinstance(data, list):
            return [self.redact_data(item) for item in data]
        if isinstance(data, str):
            return self.redact(data)
        if data is None or isinstance(data, (int, float, bool)):
            return data
        # Anything else (an IPv4Network, for one) reaches the file through its
        # repr, which can carry an address the traversal above never sees.
        # Only swap in the masked text when there was something to mask, so
        # values like timestamps keep their own serialization.
        text = str(data)
        masked = self.redact(text)
        return masked if masked != text else data

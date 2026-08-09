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
_KEYED = ("serial-number", "sfp-vendor-serial", "ssid", "caller-id")
_KEYED_RE = re.compile(r"('(?:" + "|".join(re.escape(k) for k in _KEYED) + r")'\s*:\s*')([^']*)(')")


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

    def redact(self, line: str) -> str:
        # MAC before IPv6 (both use colons); the placeholders contain non-hex
        # "x" so already-masked spans are not re-matched afterwards.
        line = _MAC_RE.sub(self._mac, line)
        line = _IPV6_FULL_RE.sub(self._ipv6, line)
        line = _IPV6_RE.sub(self._ipv6, line)
        line = _IPV4_RE.sub(self._ipv4, line)
        line = _KEYED_RE.sub(self._keyed, line)
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

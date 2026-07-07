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

# IPv6: only match forms containing "::" (unambiguous, never a timestamp or MAC)
_IPV6_RE = re.compile(r"\b([0-9A-Fa-f]{1,4})?::(?:[0-9A-Fa-f]{1,4}:)*([0-9A-Fa-f]{1,4})\b")

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
        return f"{m.group(1)}.x.x.{m.group(2)}#{self._tag(m.group(0))}"

    def _mac(self, m: re.Match) -> str:
        sep = m.group(2)
        return f"{m.group(1)}{sep}xx{sep}xx{sep}xx{sep}xx{sep}{m.group(3)}#{self._tag(m.group(0))}"

    def _ipv6(self, m: re.Match) -> str:
        return f"{m.group(1) or ''}::…:{m.group(2)}#{self._tag(m.group(0))}"

    def _keyed(self, m: re.Match) -> str:
        value = m.group(2)
        masked = "…" if len(value) <= 4 else f"{value[:2]}...{value[-2:]}"
        return f"{m.group(1)}{masked}#{self._tag(value)}{m.group(3)}"

    def redact(self, line: str) -> str:
        # MAC before IPv6 (both use colons); the placeholders contain non-hex
        # "x" so already-masked spans are not re-matched afterwards.
        line = _MAC_RE.sub(self._mac, line)
        line = _IPV6_RE.sub(self._ipv6, line)
        line = _IPV4_RE.sub(self._ipv4, line)
        line = _KEYED_RE.sub(self._keyed, line)
        return line

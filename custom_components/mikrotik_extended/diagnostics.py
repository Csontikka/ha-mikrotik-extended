"""Diagnostics support for MikroTik Extended."""

from __future__ import annotations

import secrets
from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from . import _LOG_BUFFER
from .const import TO_REDACT
from .log_redaction import LogRedactor


async def async_get_config_entry_diagnostics(_hass: HomeAssistant, config_entry: ConfigEntry) -> dict[str, Any]:  # NOSONAR — HA contract requires async
    """Return diagnostics for a config entry."""
    data_coordinator = config_entry.runtime_data.data_coordinator
    tracker_coordinator = config_entry.runtime_data.tracker_coordinator

    # Captured log lines may hold raw API responses (if the user enabled debug
    # logging). Mask network identifiers before they leave the box, keeping the
    # log structure and per-dump correlation intact. A fresh salt per download
    # means the same device is not trackable across separate diagnostics dumps.
    redactor = LogRedactor(secrets.token_bytes(16))

    return {
        "entry": {
            "data": async_redact_data(config_entry.data, TO_REDACT),
            "options": async_redact_data(config_entry.options, TO_REDACT),
        },
        "data": async_redact_data(data_coordinator.data, TO_REDACT),
        "tracker": async_redact_data(tracker_coordinator.data, TO_REDACT),
        "logs": [redactor.redact(line) for line in _LOG_BUFFER],
    }

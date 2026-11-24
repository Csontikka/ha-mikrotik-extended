"""Diagnostics support for MikroTik Extended."""

from __future__ import annotations

import secrets
from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from . import _LOG_BUFFER
from .const import TO_REDACT
from .log_redaction import SECRET_MASK, LogRedactor


def _without_secrets(data: dict[str, Any]) -> dict[str, Any]:
    """Return the coordinator data with the script environment values left out.

    A global variable of the router often holds a token or a password that a
    script needs. The sensor shows it to the owner of the router; a download
    that gets attached to a public issue must not. The store is keyed by the
    variable name, so "value" can be replaced here without touching any other
    store that has a field of that name.
    """
    variables = data.get("environment")
    if not isinstance(variables, dict):
        return data
    masked = {name: {**row, "value": SECRET_MASK} if isinstance(row, dict) and row.get("value") not in ("", None) else row for name, row in variables.items()}
    return {**data, "environment": masked}


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
            # The router address lives here under "host", which no field-name
            # rule covers, so this branch needs the same masking (issue 25).
            "data": redactor.redact_data(async_redact_data(config_entry.data, TO_REDACT)),
            "options": redactor.redact_data(async_redact_data(config_entry.options, TO_REDACT)),
        },
        "data": redactor.redact_data(async_redact_data(_without_secrets(data_coordinator.data), TO_REDACT)),
        "tracker": redactor.redact_data(async_redact_data(tracker_coordinator.data, TO_REDACT)),
        "logs": [redactor.redact(line) for line in _LOG_BUFFER],
    }

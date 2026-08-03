"""Repair flows for the MikroTik Extended integration."""

from __future__ import annotations

from logging import getLogger

import voluptuous as vol
from homeassistant.components.repairs import RepairsFlow
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResult

from .encoding_repair import apply_renames, collect_renames, format_rename_list

_LOGGER = getLogger(__name__)


class EncodingEntityIdRepairFlow(RepairsFlow):
    """Confirm renaming entity ids that were built from misdecoded text."""

    def __init__(self, entry_id: str) -> None:
        """Remember which config entry this repair belongs to."""
        self._entry_id = entry_id

    def _pending_renames(self) -> list[dict]:
        """Recalculate the rename list from the coordinator's current data."""
        entry = self.hass.config_entries.async_get_entry(self._entry_id)
        runtime = getattr(entry, "runtime_data", None) if entry else None
        coordinator = getattr(runtime, "data_coordinator", None)
        if coordinator is None:
            return []
        return collect_renames(self.hass, self._entry_id, coordinator.ds, coordinator._TEXT_FIELDS)

    async def async_step_init(self, user_input: dict | None = None) -> FlowResult:
        """Start the flow."""
        return await self.async_step_confirm()

    async def async_step_confirm(self, user_input: dict | None = None) -> FlowResult:
        """Show what would change, then rename once the user confirms."""
        renames = self._pending_renames()
        if not renames:
            return self.async_create_entry(data={})

        if user_input is not None:
            applied, skipped = apply_renames(self.hass, renames)
            for item in applied:
                _LOGGER.info(
                    "Mikrotik renamed %s to %s after the text encoding fix",
                    item["entity_id"],
                    item["new_entity_id"],
                )
            for item in skipped:
                _LOGGER.warning(
                    "Mikrotik left %s untouched (%s)",
                    item["entity_id"],
                    item["reason"],
                )
            return self.async_create_entry(data={})

        return self.async_show_form(
            step_id="confirm",
            data_schema=vol.Schema({}),
            description_placeholders={
                "count": str(len(renames)),
                "renames": format_rename_list(renames),
            },
        )


async def async_create_fix_flow(hass: HomeAssistant, issue_id: str, data: dict | None) -> RepairsFlow:
    """Create the flow that belongs to an issue raised by this integration."""
    entry_id = (data or {}).get("entry_id", "")
    return EncodingEntityIdRepairFlow(entry_id)

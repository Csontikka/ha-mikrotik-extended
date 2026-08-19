"""Support for the MikroTik Extended selects."""

from __future__ import annotations

PARALLEL_UPDATES = 0

from logging import getLogger
from typing import Any

from homeassistant.components.select import SelectEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .entity import MikrotikEntity, async_add_entities
from .select_types import (
    POE_OPTIONS,
    POE_VALUES,
    SENSOR_SERVICES,  # noqa: F401 — accessed via platform.platform.SENSOR_SERVICES
    SENSOR_TYPES,  # noqa: F401 — accessed via platform.platform.SENSOR_TYPES
)

_LOGGER = getLogger(__name__)


# ---------------------------
#   async_setup_entry
# ---------------------------
async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    _async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up entry for component"""
    dispatcher = {
        "MikrotikSelect": MikrotikSelect,
        "MikrotikPoeSelect": MikrotikPoeSelect,
    }
    await async_add_entities(hass, config_entry, dispatcher)


# ---------------------------
#   MikrotikSelect
# ---------------------------
class MikrotikSelect(MikrotikEntity, SelectEntity):
    """Representation of a select."""

    @property
    def current_option(self) -> str | None:
        """Return the current option."""
        return self._data.get(self.entity_description.data_attribute)

    async def async_select_option(self, option: str) -> None:
        """Change the selected option."""


# ---------------------------
#   MikrotikPoeSelect
# ---------------------------
class MikrotikPoeSelect(MikrotikSelect):
    """Representation of the PoE output mode of a port."""

    @property
    def current_option(self) -> str | None:
        """Return the mode the port is in, or nothing when it is unknown.

        A port that cannot supply power reports a placeholder rather than a
        mode, and Home Assistant rejects an option it was not told about, so
        anything unrecognised is reported as no value.
        """
        return POE_VALUES.get(self._data.get(self.entity_description.data_attribute))

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return the state attributes."""
        return super().extra_state_attributes

    async def async_select_option(self, option: str) -> None:
        """Set the PoE output mode of the port.

        forced_on skips the detection step and puts voltage on the cable
        whatever is plugged in, so it can damage a device that was not built
        for it. That is a decision for whoever is looking at the cabling, not
        something to second guess here, but it is worth the log line.
        """
        if "write" not in self.coordinator.data["access"]:
            _LOGGER.warning(
                "Mikrotik %s user does not have write access rights, cannot set PoE output",
                self.coordinator.host,
            )
            return

        value = POE_OPTIONS.get(option)
        if value is None:
            _LOGGER.error("Unknown PoE output mode: %s", option)
            return

        port = self._data[self.entity_description.data_uid]
        if value == "forced-on":
            _LOGGER.warning(
                "Mikrotik %s forcing PoE output on %s: power is applied without detecting the device first",
                self.coordinator.host,
                port,
            )

        success = await self.hass.async_add_executor_job(
            self.coordinator.api.set_value,
            self.entity_description.data_set_path,
            self.entity_description.data_set_reference,
            port,
            self.entity_description.data_set_parameter,
            value,
        )
        if not success:
            _LOGGER.error(
                "Mikrotik %s refused the PoE output change on %s",
                self.coordinator.host,
                port,
            )
            return

        await self.coordinator.async_request_refresh()

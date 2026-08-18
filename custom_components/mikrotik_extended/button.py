"""Support for the MikroTik Extended buttons."""

from __future__ import annotations

PARALLEL_UPDATES = 0

from logging import getLogger

from homeassistant.components.button import ButtonEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .button_types import (
    SENSOR_SERVICES,  # noqa: F401 — accessed via platform.platform.SENSOR_SERVICES
    SENSOR_TYPES,  # noqa: F401 — accessed via platform.platform.SENSOR_TYPES
)
from .entity import MikrotikEntity, async_add_entities

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
        "MikrotikButton": MikrotikButton,
        "MikrotikScriptButton": MikrotikScriptButton,
        "MikrotikRebootButton": MikrotikRebootButton,
        "MikrotikBackupButton": MikrotikBackupButton,
    }
    await async_add_entities(hass, config_entry, dispatcher)


# ---------------------------
#   MikrotikButton
# ---------------------------
class MikrotikButton(MikrotikEntity, ButtonEntity):
    """Representation of a button."""

    async def async_update(self):
        """Synchronize state with controller."""

    async def async_press(self) -> None:
        pass


# ---------------------------
#   MikrotikRebootButton
# ---------------------------
class MikrotikRebootButton(MikrotikButton):
    """Representation of a reboot button."""

    async def async_press(self) -> None:
        """Reboot the MikroTik device."""
        if "reboot" not in self.coordinator.ds["access"]:
            _LOGGER.warning(
                "Mikrotik %s user does not have reboot access rights",
                self.coordinator.host,
            )
            return
        _LOGGER.info("Rebooting Mikrotik device %s", self.coordinator.host)
        await self.hass.async_add_executor_job(self.coordinator.execute, "/system", "reboot", None, None)


# ---------------------------
#   MikrotikBackupButton
# ---------------------------
class MikrotikBackupButton(MikrotikButton):
    """Representation of a configuration backup button."""

    # RouterOS stamps the date into the file name when none is given, so every
    # press would leave another copy on the router and eventually fill the
    # storage of a small device. A fixed name keeps exactly one backup, always
    # the most recent, and makes it obvious on the router where it came from.
    BACKUP_NAME = "homeassistant"

    async def async_press(self) -> None:
        """Write a configuration backup on the router itself.

        The file stays on the router: its contents are not exposed over the
        API, so there is nothing to bring back here.
        """
        if "write" not in self.coordinator.ds["access"]:
            _LOGGER.warning(
                "Mikrotik %s user does not have write access rights, cannot save a backup",
                self.coordinator.host,
            )
            return

        _LOGGER.info("Saving configuration backup on Mikrotik device %s", self.coordinator.host)
        success = await self.hass.async_add_executor_job(
            self.coordinator.execute,
            "/system/backup",
            "save",
            None,
            None,
            {"name": self.BACKUP_NAME},
        )
        if not success:
            _LOGGER.error(
                "Mikrotik %s refused the configuration backup",
                self.coordinator.host,
            )


# ---------------------------
#   MikrotikScriptButton
# ---------------------------
class MikrotikScriptButton(MikrotikButton):
    """Representation of a script button."""

    async def async_press(self) -> None:
        """Run script using Mikrotik API"""
        _LOGGER.debug("Running script %s on %s", self._data["name"], self.coordinator.host)
        success = await self.hass.async_add_executor_job(self.coordinator.api.run_script, self._data["name"])
        if not success:
            _LOGGER.error("Failed to run script: %s", self._data["name"])
            return
        await self.coordinator.async_refresh()
        await self._config_entry.runtime_data.tracker_coordinator.async_request_refresh()

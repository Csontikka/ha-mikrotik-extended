"""Support for the MikroTik Extended binary sensor service."""

from __future__ import annotations

PARALLEL_UPDATES = 0

from collections.abc import Mapping
from logging import getLogger
from typing import Any

from homeassistant.components.binary_sensor import BinarySensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .binary_sensor_types import (
    DEVICE_ATTRIBUTES_IFACE_ETHER,
    DEVICE_ATTRIBUTES_IFACE_SFP,
    DEVICE_ATTRIBUTES_IFACE_WIRELESS,
    SENSOR_SERVICES,  # noqa: F401 — accessed via platform.platform.SENSOR_SERVICES
    SENSOR_TYPES,  # noqa: F401 — accessed via platform.platform.SENSOR_TYPES
)
from .const import (
    CONF_SENSOR_PORT_TRACKER,
    CONF_SENSOR_PPP,
    DEFAULT_SENSOR_PORT_TRACKER,
    DEFAULT_SENSOR_PPP,
)
from .entity import MikrotikEntity, async_add_entities
from .helper import format_attribute

_LOGGER = getLogger(__name__)


def _collect_iface_attributes(data: Mapping[str, Any]) -> dict[str, Any]:
    """Return formatted iface attributes based on the iface type."""
    collected: dict[str, Any] = {}
    iface_type = data["type"]
    if iface_type == "ether":
        _add_present_attributes(collected, data, DEVICE_ATTRIBUTES_IFACE_ETHER)
        if "sfp-shutdown-temperature" in data:
            _add_present_attributes(collected, data, DEVICE_ATTRIBUTES_IFACE_SFP)
    elif iface_type == "wlan":
        _add_present_attributes(collected, data, DEVICE_ATTRIBUTES_IFACE_WIRELESS)
    return collected


def _add_present_attributes(target: dict[str, Any], data: Mapping[str, Any], variables) -> None:
    """Copy formatted attributes from data into target for each present variable."""
    for variable in variables:
        if variable in data:
            target[format_attribute(variable)] = data[variable]


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
        "MikrotikBinarySensor": MikrotikBinarySensor,
        "MikrotikPPPSecretBinarySensor": MikrotikPPPSecretBinarySensor,
        "MikrotikPortBinarySensor": MikrotikPortBinarySensor,
        "MikrotikWireguardPeerBinarySensor": MikrotikWireguardPeerBinarySensor,
    }
    await async_add_entities(hass, config_entry, dispatcher)


# ---------------------------
#   MikrotikBinarySensor
# ---------------------------
class MikrotikBinarySensor(MikrotikEntity, BinarySensorEntity):
    """Define an Mikrotik Controller Binary Sensor."""

    @property
    def is_on(self) -> bool:
        """Return true if device is on."""
        return self._data[self.entity_description.data_attribute]

    @property
    def icon(self) -> str:
        """Return the icon."""
        if self.entity_description.icon_enabled:
            if self._data[self.entity_description.data_attribute]:
                return self.entity_description.icon_enabled
            else:
                return self.entity_description.icon_disabled


# ---------------------------
#   MikrotikPPPSecretBinarySensor
# ---------------------------
class MikrotikPPPSecretBinarySensor(MikrotikBinarySensor):
    """Representation of a network device."""

    @property
    def option_sensor_ppp(self) -> bool:
        """Config entry option."""
        return self._config_entry.options.get(CONF_SENSOR_PPP, DEFAULT_SENSOR_PPP)

    @property
    def is_on(self) -> bool:
        """Return true if device is on."""
        return self._data[self.entity_description.data_attribute] if self.option_sensor_ppp else False


# ---------------------------
#   MikrotikPortBinarySensor
# ---------------------------
class MikrotikPortBinarySensor(MikrotikBinarySensor):
    """Representation of a network port."""

    @property
    def option_sensor_port_tracker(self) -> bool:
        """Config entry option to not track ARP."""
        return self._config_entry.options.get(CONF_SENSOR_PORT_TRACKER, DEFAULT_SENSOR_PORT_TRACKER)

    @property
    def icon(self) -> str:
        """Return the icon."""
        if self._data[self.entity_description.data_attribute]:
            icon = self.entity_description.icon_enabled
        else:
            icon = self.entity_description.icon_disabled

        if not self._data["enabled"]:
            icon = "mdi:lan-disconnect"

        return icon

    @property
    def extra_state_attributes(self) -> Mapping[str, Any]:
        """Return the state attributes."""
        attributes = super().extra_state_attributes
        attributes.update(_collect_iface_attributes(self._data))
        return attributes


# ---------------------------
#   MikrotikWireguardPeerBinarySensor
# ---------------------------
class MikrotikWireguardPeerBinarySensor(MikrotikBinarySensor):
    """Representation of a WireGuard peer connectivity sensor."""

    @property
    def is_on(self) -> bool:
        """Return true if peer has had a recent handshake."""
        return self._data.get("connected", False)

"""Definitions for MikroTik Extended select entities."""

from __future__ import annotations

from dataclasses import dataclass, field

from homeassistant.components.select import SelectEntityDescription
from homeassistant.helpers.device_registry import CONNECTION_NETWORK_MAC
from homeassistant.helpers.entity import EntityCategory

# RouterOS spells the modes with a hyphen, Home Assistant wants option names it
# can turn into translation keys, so the two are mapped rather than shared.
POE_OPTIONS = {
    "off": "off",
    "auto_on": "auto-on",
    "forced_on": "forced-on",
}
POE_VALUES = {value: key for key, value in POE_OPTIONS.items()}

DEVICE_ATTRIBUTES_POE = [
    "poe-priority",
]


@dataclass
class MikrotikSelectEntityDescription(SelectEntityDescription):
    """Class describing mikrotik entities."""

    ha_group: str | None = None
    ha_connection: str | None = None
    ha_connection_value: str | None = None
    data_path: str | None = None
    data_attribute: str | None = None
    data_set_path: str | None = None
    data_set_parameter: str | None = None
    data_set_reference: str | None = None
    data_name: str | None = None
    data_name_comment: bool = False
    data_uid: str | None = None
    data_reference: str | None = None
    data_attributes_list: list = field(default_factory=lambda: [])
    func: str = "MikrotikSelect"
    enable_on_option: str | None = None


SENSOR_TYPES: tuple[MikrotikSelectEntityDescription, ...] = (
    MikrotikSelectEntityDescription(
        key="poe_out",
        name="PoE out",
        translation_key="poe_out",
        icon="mdi:power-plug-outline",
        entity_category=EntityCategory.CONFIG,
        options=list(POE_OPTIONS),
        ha_group="data__default-name",
        ha_connection=CONNECTION_NETWORK_MAC,
        ha_connection_value="data__port-mac-address",
        data_path="interface",
        data_attribute="poe-out",
        # The PoE menu is a view of the same ethernet ports and is addressed by
        # interface name, so the row is found by name rather than by list id.
        data_set_path="/interface/ethernet/poe",
        data_set_parameter="poe-out",
        data_set_reference="name",
        data_name="default-name",
        data_uid="name",
        data_reference="default-name",
        data_attributes_list=DEVICE_ATTRIBUTES_POE,
        func="MikrotikPoeSelect",
    ),
)

SENSOR_SERVICES = []

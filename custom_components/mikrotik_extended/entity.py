"""Mikrotik HA shared entity model"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from logging import getLogger
from typing import Any, TypeVar

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import ATTR_ATTRIBUTION, CONF_HOST, CONF_NAME
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import (
    device_registry as dr,
)
from homeassistant.helpers import (
    entity_platform as ep,
)
from homeassistant.helpers import (
    entity_registry as er,
)
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity import DeviceInfo, Entity
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.util import slugify

from .const import (
    ATTRIBUTION,
    CONF_SENSOR_INTERFACES,
    CONF_SENSOR_NETWATCH_TRACKER,
    CONF_SENSOR_PORT_ERRORS,
    CONF_SENSOR_PORT_SWITCH,
    CONF_SENSOR_PORT_TRACKER,
    CONF_SENSOR_PORT_TRAFFIC,
    CONF_TRACK_HOSTS,
    DEFAULT_SENSOR_INTERFACES,
    DEFAULT_SENSOR_NETWATCH_TRACKER,
    DEFAULT_SENSOR_PORT_ERRORS,
    DEFAULT_SENSOR_PORT_SWITCH,
    DEFAULT_SENSOR_PORT_TRACKER,
    DEFAULT_SENSOR_PORT_TRAFFIC,
    DEFAULT_TRACK_HOSTS,
    DOMAIN,
)
from .coordinator import MikrotikCoordinator, MikrotikTrackerCoordinator, core_device_kwargs

# Home Assistant 2026.9 links a device to its parent by registry id; earlier
# releases only know the (domain, identifier) tuple and reject the id.
_VIA_DEVICE_BY_ID = "via_device_id" in DeviceInfo.__annotations__

# Values that arrive where a name is expected without being one. A host
# without a resolvable name carries None or "unknown"; a host restored from a
# previous run may carry the string "None", which is what f"{None}" made of
# it before, and that string is also what earlier releases stored as the
# device name.
_NOT_A_NAME = (None, "", "None", "unknown")
from .helper import format_attribute

_LOGGER = getLogger(__name__)

# Stores whose entities may follow a re-created record to its new list id.
# Only the rule stores qualify: they are keyed by the unstable RouterOS id,
# carry a comment-based uniq-id AND a content signature (legacy-uniq-id) to
# confirm the match. WireGuard peers are deliberately absent: the public key
# is not unique across interfaces, so a match there could bind a switch to a
# different peer.
REBIND_DATA_PATHS = frozenset({"nat", "mangle", "routing_rules", "filter", "raw", "queue"})

_FIREWALL_GROUPS = {"NAT", "Mangle", "Filter", "Raw", "Routing Rules"}
_IFACE_TYPE_CATEGORY = {
    "ether": "port",
    "vlan": "vlan",
    "wlan": "wifi",
    "bridge": "bridge",
    "pppoe-out": "ppp",
    "ppp": "ppp",
    "l2tp-out": "vpn",
    "sstp-out": "vpn",
    "ovpn-out": "vpn",
    "wireguard": "vpn",
}


def _same_text(left, right) -> bool:
    """Two names compared the way a MAC needs it: as text, ignoring case."""
    return left is not None and right is not None and str(left).lower() == str(right).lower()


def _skip_non_poe_port(entity_description, item) -> bool:
    """Only a port that can actually supply power gets a PoE selector.

    Ports without the capability report a placeholder instead of a mode, so
    creating a selector for them would offer a control that does nothing.
    """
    if entity_description.func != "MikrotikPoeSelect":
        return False
    return item.get("poe-out") in (None, "", "N/A", "unknown")


def _skip_interface_entity(config_entry, entity_description) -> bool:
    """Skip every interface-derived entity when interface entities are disabled.

    The store itself can still hold data: host tracking reads it to filter
    container veth ports out of the client count and to tell a wifi bridge
    port from a wired one. Entity creation therefore needs its own gate
    instead of relying on an empty store.
    """
    if entity_description.data_path not in ("interface", "ip_address"):
        return False
    return not config_entry.options.get(CONF_SENSOR_INTERFACES, DEFAULT_SENSOR_INTERFACES)


def _skip_port_switch(config_entry, entity_description) -> bool:
    """Leave out the switch that takes a port down when the user does not want it.

    The switch disables the interface on the router. On a port that carries
    the uplink, or the connection to Home Assistant itself, one wrong tap
    cuts the network off, so it can be turned off without giving up the
    other entities of the port.
    """
    if entity_description.func != "MikrotikPortSwitch":
        return False
    return not config_entry.options.get(CONF_SENSOR_PORT_SWITCH, DEFAULT_SENSOR_PORT_SWITCH)


def _skip_interface_traffic_sensor(config_entry, entity_description, item) -> bool:
    if entity_description.func != "MikrotikInterfaceTrafficSensor":
        return False
    return not config_entry.options.get(CONF_SENSOR_PORT_TRAFFIC, DEFAULT_SENSOR_PORT_TRAFFIC)


def _skip_interface_error_sensor(config_entry, entity_description, item) -> bool:
    if entity_description.func != "MikrotikInterfaceErrorSensor":
        return False
    if not config_entry.options.get(CONF_SENSOR_PORT_ERRORS, DEFAULT_SENSOR_PORT_ERRORS):
        return True
    # Not every interface type reports every counter.
    return item.get(entity_description.data_attribute) in (None, "")


def _skip_lte_sensor(entity_description, item) -> bool:
    """The 5G sensors exist only for a modem that is set up for 5G."""
    if entity_description.data_path != "lte":
        return False
    return str(entity_description.data_attribute).startswith("nr-") and not item.get("nr-capable")


def _skip_client_traffic(entity_description, item) -> bool:
    if entity_description.data_path != "client_traffic":
        return False
    if not item.get("available", False):
        return True
    return entity_description.data_attribute not in item


def _skip_port_binary_sensor(config_entry, entity_description, item) -> bool:
    if entity_description.func != "MikrotikPortBinarySensor":
        return False
    if item["type"] == "wlan":
        return True
    return not config_entry.options.get(CONF_SENSOR_PORT_TRACKER, DEFAULT_SENSOR_PORT_TRACKER)


def _skip_netwatch(config_entry, entity_description) -> bool:
    return entity_description.data_path == "netwatch" and not config_entry.options.get(CONF_SENSOR_NETWATCH_TRACKER, DEFAULT_SENSOR_NETWATCH_TRACKER)


def _skip_host_tracker(config_entry, entity_description, item) -> bool:
    if entity_description.func != "MikrotikHostDeviceTracker":
        return False
    if not config_entry.options.get(CONF_TRACK_HOSTS, DEFAULT_TRACK_HOSTS):
        return True
    # A container endpoint is not a client. The client counters have excluded
    # these since the overcounting work, but the tracker never applied the
    # same rule, so every container kept a tracker entity, and a device, that
    # reported home permanently. The mark is set where that check already
    # lives, so the rule stays in one place.
    return bool(item.get("container-port"))


def _skip_sensor(config_entry, entity_description, data, uid) -> bool:
    item = data[uid]
    if _skip_interface_entity(config_entry, entity_description):
        return True
    if _skip_non_poe_port(entity_description, item):
        return True
    if _skip_port_switch(config_entry, entity_description):
        return True
    if _skip_interface_traffic_sensor(config_entry, entity_description, item):
        return True
    if _skip_interface_error_sensor(config_entry, entity_description, item):
        return True
    if _skip_lte_sensor(entity_description, item):
        return True
    if _skip_client_traffic(entity_description, item):
        return True
    if _skip_port_binary_sensor(config_entry, entity_description, item):
        return True
    if _skip_netwatch(config_entry, entity_description):
        return True
    return _skip_host_tracker(config_entry, entity_description, item)


# ---------------------------
#   async_add_entities
# ---------------------------
def _build_unique_id(entry_id, obj, uid) -> str:
    if uid:
        return f"{entry_id}-{obj.entity_description.key}-{slugify(str(obj._data[obj.entity_description.data_reference]).lower())}"
    return f"{entry_id}-{obj.entity_description.key}"


async def _try_re_enable_entity(platform, entity_registry, entity, entity_id, obj, config_entry) -> None:
    """Re-enable a previously disabled integration entity when its option flips on."""
    if entity.disabled_by != er.RegistryEntryDisabler.INTEGRATION:
        return
    enable_on = getattr(obj.entity_description, "enable_on_option", None)
    if enable_on and config_entry.options.get(enable_on, False):
        _LOGGER.debug("Re-enabling entity %s", entity_id)
        entity_registry.async_update_entity(entity_id, disabled_by=None)
        await platform.async_add_entities([obj])


def _cleanup_orphans(hass, platform, config_entry) -> None:
    """Remove orphaned entities and empty devices for this config entry."""
    entity_registry = er.async_get(hass)
    for entry in er.async_entries_for_config_entry(entity_registry, config_entry.entry_id):
        if entry.domain == platform.domain and entry.entity_id not in platform.entities:
            if entry.disabled:
                continue
            _LOGGER.debug("Removing orphaned entity %s", entry.entity_id)
            entity_registry.async_remove(entry.entity_id)

    device_registry = dr.async_get(hass)
    for device_entry in dr.async_entries_for_config_entry(device_registry, config_entry.entry_id):
        device_entities = er.async_entries_for_device(entity_registry, device_entry.id, include_disabled_entities=True)
        if not device_entities:
            _LOGGER.debug("Removing empty device %s", device_entry.name)
            device_registry.async_remove_device(device_entry.id)


async def async_add_entities(hass: HomeAssistant, config_entry: ConfigEntry, dispatcher: dict[str, Callable]):
    """Add entities."""
    platform = ep.async_get_current_platform()
    services = platform.platform.SENSOR_SERVICES
    descriptions = platform.platform.SENSOR_TYPES

    for service in services:
        platform.async_register_entity_service(service[0], service[1], service[2])

    async def async_check_exist(obj, uid: str | None = None) -> None:
        """Check entity exists and add or re-enable as appropriate."""
        entity_registry = er.async_get(hass)
        unique_id = _build_unique_id(config_entry.entry_id, obj, uid)
        entity_id = entity_registry.async_get_entity_id(platform.domain, DOMAIN, unique_id)
        entity = entity_registry.async_get(entity_id)
        if entity is None or ((entity_id not in platform.entities) and (entity.disabled is False)):
            _LOGGER.debug("Add entity %s", entity_id)
            await platform.async_add_entities([obj])
        elif entity is not None:
            await _try_re_enable_entity(platform, entity_registry, entity, entity_id, obj, config_entry)

    async def _process_singleton(coordinator, entity_description, data) -> None:
        if data.get(entity_description.data_attribute) is None:
            return
        func = dispatcher.get(entity_description.func)
        if func is None:
            return
        obj = func(coordinator, entity_description)
        await async_check_exist(obj)

    async def _process_keyed(coordinator, entity_description, data) -> None:
        if not isinstance(data, (dict, list)):
            return
        for uid in data:
            if _skip_sensor(config_entry, entity_description, data, uid):
                continue
            func = dispatcher.get(entity_description.func)
            if func is None:
                continue
            obj = func(coordinator, entity_description, uid)
            await async_check_exist(obj, uid)

    @callback
    async def async_update_controller(coordinator):
        """Update the values of the controller."""
        if coordinator.data is None:
            return

        for entity_description in descriptions:
            data = coordinator.data.get(entity_description.data_path)
            if data is None:
                continue
            if not entity_description.data_reference:
                await _process_singleton(coordinator, entity_description, data)
            else:
                await _process_keyed(coordinator, entity_description, data)

        _cleanup_orphans(hass, platform, config_entry)

    await async_update_controller(config_entry.runtime_data.data_coordinator)

    unsub = async_dispatcher_connect(hass, f"update_sensors_{config_entry.entry_id}", async_update_controller)
    config_entry.async_on_unload(unsub)


_MikrotikCoordinatorT = TypeVar(
    "_MikrotikCoordinatorT",
    bound=MikrotikCoordinator | MikrotikTrackerCoordinator,
)


# ---------------------------
#   MikrotikEntity
# ---------------------------
class MikrotikEntity(CoordinatorEntity[_MikrotikCoordinatorT], Entity):
    """Define entity"""

    _attr_has_entity_name = True
    _unrecorded_attributes = frozenset({"leases", "wired_clients_list", "wireless_clients_list"})

    def __init__(
        self,
        coordinator: MikrotikCoordinator,
        entity_description,
        uid: str | None = None,
    ):
        """Initialize entity"""
        super().__init__(coordinator)
        self.entity_description = entity_description
        self._inst = coordinator.config_entry.data[CONF_NAME]
        self._config_entry = self.coordinator.config_entry
        self._attr_extra_state_attributes = {ATTR_ATTRIBUTION: ATTRIBUTION}
        self._uid = uid
        self._data = coordinator.data[self.entity_description.data_path]
        if self._uid:
            self._data = coordinator.data[self.entity_description.data_path][self._uid]

        self._attr_name = self.custom_name

    @callback
    def _handle_coordinator_update(self) -> None:
        path_data = self.coordinator.data.get(self.entity_description.data_path)
        if path_data is None:
            return
        if self._uid:
            get_counters = getattr(self.coordinator, "_get_stale_counters", None)
            stale = get_counters(self.entity_description.data_path) if get_counters else {}
            if self._uid not in path_data or self._uid in stale:
                # Stores keyed by the RouterOS list id lose their key when a
                # record is re-created (the id is not stable). Follow the row
                # to its new key via the stable uniq-id, otherwise the entity
                # would stay bound to the dead row forever (issue 23 family).
                # A row on the pruning grace is already gone from the router,
                # so a live row with the same reference supersedes it at once.
                new_uid = self._replacement_uid(path_data, stale)
                if new_uid is not None:
                    self._uid = new_uid
                elif self._uid not in path_data:
                    return
            self._data = path_data[self._uid]
        else:
            self._data = path_data
        self._attr_name = self.custom_name
        super()._handle_coordinator_update()

    def _replacement_uid(self, path_data, stale) -> str | None:
        """Find the live store key of a re-created record by its uniq-id."""
        if self.entity_description.data_path not in REBIND_DATA_PATHS:
            return None
        ref = self._data.get("uniq-id")
        if not ref:
            return None
        # The comment alone cannot tell a re-created rule apart from a
        # different rule that happens to share it, and following the wrong
        # one would put this switch in control of an unrelated firewall
        # rule. The stored content signature settles it.
        signature = self._data.get("legacy-uniq-id")
        # Rows on the pruning grace are already gone from the router; binding
        # to one would just trade a dead row for another dead row.
        for uid, vals in path_data.items():
            if uid == self._uid or uid in stale:
                continue
            if vals.get("uniq-id") == ref and vals.get("legacy-uniq-id") == signature:
                return uid
        return None

    @property
    def custom_name(self) -> str:
        """Return the name for this entity"""
        if not self._uid:
            if self.entity_description.data_name_comment and self._data.get("comment"):
                comment = self._data["comment"]
                if self.entity_description.name:
                    return f"{comment} {self.entity_description.name}"
                return comment

            return f"{self.entity_description.name}"

        if self.entity_description.data_name_comment and self._data.get("comment"):
            comment = self._data["comment"]
            if self.entity_description.name:
                return f"{comment} {self.entity_description.name}"
            return comment

        if self.entity_description.name:
            if self._data[self.entity_description.data_reference] == self._data[self.entity_description.data_name]:
                return f"{self.entity_description.name}"

            return f"{self._data[self.entity_description.data_name]} {self.entity_description.name}"

        return f"{self._data[self.entity_description.data_name]}"

    @property
    def unique_id(self) -> str:
        """Return a unique id for this entity"""
        entry_id = self._config_entry.entry_id
        if self._uid:
            return f"{entry_id}-{self.entity_description.key}-{slugify(str(self._data[self.entity_description.data_reference]).lower())}"
        else:
            return f"{entry_id}-{self.entity_description.key}"

    @property
    def entity_registry_enabled_default(self) -> bool:
        """Return if entity should be enabled by default."""
        if self.entity_description.entity_registry_enabled_default:
            return True
        enable_on = getattr(self.entity_description, "enable_on_option", None)
        if enable_on:
            return bool(self._config_entry.options.get(enable_on, False))
        return False

    def _resolve_device_identity(self) -> tuple[str, Any, str]:
        """Resolve (dev_connection, dev_connection_value, dev_group) from entity description."""
        dev_connection = DOMAIN
        dev_connection_value = self.entity_description.data_reference
        dev_group = self.entity_description.ha_group
        if self.entity_description.ha_group == "System":
            dev_group = self.coordinator.data["resource"]["board-name"]
            dev_connection_value = self.coordinator.data["routerboard"]["serial-number"]

        if self.entity_description.ha_group.startswith("data__"):
            dev_group = self.entity_description.ha_group[6:]
            if dev_group in self._data:
                dev_group = self._data[dev_group]
                dev_connection_value = dev_group

        if self.entity_description.ha_connection:
            dev_connection = self.entity_description.ha_connection

        if self.entity_description.ha_connection_value:
            dev_connection_value = self.entity_description.ha_connection_value
            if dev_connection_value.startswith("data__"):
                dev_connection_value = dev_connection_value[6:]
                dev_connection_value = self._data[dev_connection_value]

        return dev_connection, dev_connection_value, dev_group

    def _build_system_device_info(self, entry_id, dev_connection, dev_connection_value) -> DeviceInfo:
        return DeviceInfo(
            **core_device_kwargs(
                entry_id,
                self._inst,
                self.coordinator.config_entry.data[CONF_HOST],
                self.coordinator.data,
            )
        )

    def _fields_for_new_device(self, connection: tuple[str, str], entry_id: str, placeholder: str | None = None, **fields) -> dict:
        """Descriptive fields for a device, set on creation only.

        This is what default_name and its siblings used to do, and the plain
        fields that replaced them in 2026.9 do not: they also overwrite an
        existing device on every start. On releases before 2026.9 a MAC
        connection is shared across integrations, so the plain name would
        rename a Shelly or ESPHome device to whatever the router calls the
        host, on every reload. A live diff of 243 devices also showed our own
        devices renamed wholesale on update. So: an existing device keeps
        what it has, a new one gets the fields, and empty values are never
        sent. A name the user typed in is stored separately either way.
        """
        registry = dr.async_get(self.coordinator.hass)
        lookup = getattr(registry, "async_get_device_by_connection", None)
        if lookup is not None:
            # 2026.8 and later: the lookup is scoped to our config entry, so
            # whatever it returns is ours.
            existing = lookup(connection, entry_id)
            ours = existing is not None
        else:
            # Earlier: one shared device per MAC across integrations.
            existing = registry.async_get_device(connections={connection})
            ours = existing is not None and entry_id in existing.config_entries
        # A device that an earlier release of ours created as "None" (the
        # string, from default_name=f"{None}") is not one to preserve; it gets
        # a name now. Another integration's device is left alone whatever it
        # is called.
        #
        # The same goes for a device of ours that still carries the
        # placeholder it was created with: a host seen before it had a name
        # is called by its MAC, and without this it would keep the MAC for
        # good once the name turns up.
        has_real_name = existing is not None and existing.name not in _NOT_A_NAME and not _same_text(existing.name, placeholder)
        if existing is not None and (not ours or has_real_name):
            return {}
        return {key: value for key, value in fields.items() if value not in _NOT_A_NAME}

    def _via_core(self, entry_id) -> dict:
        """Link a device to the router Core device.

        Home Assistant 2026.9 replaced the via_device tuple with via_device_id
        and removes the tuple in 2027.8; releases before 2026.9 do not accept
        the id. Each release gets the form it understands. The Core device is
        registered at setup, before the platforms load, so its id is known
        here; when it is not (the serial was unknown), the link is left out
        rather than pointed at nothing.
        """
        if _VIA_DEVICE_BY_ID:
            # Host trackers run on the tracker coordinator, which hangs off
            # the main one; the Core device id lives on the main one.
            main = self.coordinator.coordinator if isinstance(self.coordinator, MikrotikTrackerCoordinator) else self.coordinator
            core_id = getattr(main, "core_device_id", None)
            # An id the registry no longer knows (the user deleted the Core
            # device) would make the registry reject the whole device info
            # and the platform drop the entity. No link beats no entity.
            if isinstance(core_id, str) and dr.async_get(self.coordinator.hass).async_get(core_id) is not None:
                return {"via_device_id": core_id}
            return {}
        return {
            "via_device": (
                DOMAIN,
                f"{entry_id}-{self.coordinator.data['routerboard']['serial-number']}",
            )
        }

    def _build_mac_device_info(self, entry_id, dev_connection, dev_connection_value) -> DeviceInfo:
        dev_group = self._data[self.entity_description.data_name]
        dev_manufacturer = ""
        if dev_connection_value in self.coordinator.data["host"]:
            host = self.coordinator.data["host"][dev_connection_value]
            # A host without a resolvable name carries None or "unknown" here.
            # Neither is a name: the old default_name silently stored the
            # string "None" on new devices, and the plain name field would
            # now overwrite a good existing name with it on every restart.
            # Fall back to the interface's own value, which is the MAC.
            if host.get("host-name") not in _NOT_A_NAME:
                dev_group = host["host-name"]
            dev_manufacturer = host.get("manufacturer") or ""
        if dev_group in _NOT_A_NAME:
            # For a host tracker the entity's own record is the host, so the
            # first fallback above is the same None; the MAC is what is left.
            dev_group = dev_connection_value

        connection = (dev_connection, f"{dev_connection_value}")
        return DeviceInfo(
            connections={connection},
            **self._fields_for_new_device(connection, entry_id, placeholder=f"{dev_connection_value}", name=f"{dev_group}", manufacturer=dev_manufacturer),
            **self._via_core(entry_id),
        )

    def _build_generic_device_info(self, entry_id, dev_connection, dev_connection_value, dev_group) -> DeviceInfo:
        orig_ha_group = self.entity_description.ha_group
        if orig_ha_group.startswith("data__"):
            iface_type = self._data.get("type", "")
            category = _IFACE_TYPE_CATEGORY.get(iface_type, "port")
            dev_display_name = f"{self._inst} router {category} {dev_group}"
        elif orig_ha_group in _FIREWALL_GROUPS:
            dev_display_name = f"{self._inst} router firewall {dev_group}"
        else:
            dev_display_name = f"{self._inst} router {dev_group}"
        connection = (dev_connection, f"{entry_id}-{dev_connection_value}")
        return DeviceInfo(
            connections={connection},
            **self._fields_for_new_device(
                connection,
                entry_id,
                name=dev_display_name,
                model=f"{self.coordinator.data['resource']['board-name']}",
                manufacturer=f"{self.coordinator.data['resource']['platform']}",
            ),
            **self._via_core(entry_id),
        )

    @property
    def device_info(self) -> DeviceInfo:
        """Return a description for device registry."""
        dev_connection, dev_connection_value, dev_group = self._resolve_device_identity()
        entry_id = self._config_entry.entry_id
        if self.entity_description.ha_group == "System":
            return self._build_system_device_info(entry_id, dev_connection, dev_connection_value)
        elif "mac-address" in self.entity_description.data_reference:
            return self._build_mac_device_info(entry_id, dev_connection, dev_connection_value)
        else:
            return self._build_generic_device_info(entry_id, dev_connection, dev_connection_value, dev_group)

    @property
    def extra_state_attributes(self) -> Mapping[str, Any]:
        """Return the state attributes."""
        attributes = super().extra_state_attributes
        for variable in self.entity_description.data_attributes_list:
            if variable in self._data:
                attributes[format_attribute(variable)] = self._data[variable]

        return attributes

    async def start(self):
        """Dummy run function"""
        raise NotImplementedError()

    async def stop(self):
        """Dummy stop function"""
        raise NotImplementedError()

    async def restart(self):
        """Dummy restart function"""
        raise NotImplementedError()

    async def reload(self):
        """Dummy reload function"""
        raise NotImplementedError()

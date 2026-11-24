"""State attributes follow the data: one that is gone from the router is not shown on."""

from homeassistant.util.dt import utcnow

from custom_components.mikrotik_extended.binary_sensor import MikrotikPortBinarySensor
from custom_components.mikrotik_extended.const import CONF_SENSOR_PORT_TRACKER
from custom_components.mikrotik_extended.device_tracker import MikrotikHostDeviceTracker

from . import test_binary_sensor as port
from . import test_device_tracker as tracker
from .test_entity import _make_coordinator, _make_entity, _make_entity_description


def test_an_attribute_leaves_with_its_field(hass):
    """The base entity: a field taken out of the row takes its attribute along."""
    desc = _make_entity_description(data_attributes_list=["uptime", "version"], data_path="resource")
    coord = _make_coordinator(hass, data={"resource": {"cpu-load": 10, "uptime": "5d", "version": "7.1"}})
    entity = _make_entity(coord, desc)
    assert entity.extra_state_attributes["version"] == "7.1"

    del entity._data["version"]
    attributes = entity.extra_state_attributes
    assert "version" not in attributes
    assert attributes["uptime"] == "5d"


def test_reading_the_attributes_does_not_grow_what_the_entity_keeps(hass):
    """Each read builds its own answer; the dict the entity was set up with stays as it was."""
    desc = _make_entity_description(data_attributes_list=["uptime"], data_path="resource")
    coord = _make_coordinator(hass, data={"resource": {"cpu-load": 10, "uptime": "5d"}})
    entity = _make_entity(coord, desc)
    kept = dict(entity._attr_extra_state_attributes)
    first = entity.extra_state_attributes
    assert entity._attr_extra_state_attributes == kept
    assert entity.extra_state_attributes is not first
    assert "attribution" in first


async def test_a_host_that_left_the_hotspot_is_no_longer_shown_as_authorized(hass):
    """The coordinator deletes "authorized" and "bypassed" when a host leaves the hotspot table."""
    desc = tracker._make_description(data_attributes_list=["authorized", "bypassed", "last-seen"])
    data = {"mac1": {"mac-address": "mac1", "host-name": "h", "source": "wireless", "available": True, "last-seen": utcnow(), "authorized": True, "bypassed": False}}
    coord = tracker._make_coordinator(hass, data)
    entity = MikrotikHostDeviceTracker(coord, desc, uid="mac1")
    assert entity.extra_state_attributes["authorized"] is True

    # what get_host does for a host that is no longer in /ip/hotspot/host
    del data["mac1"]["authorized"]
    del data["mac1"]["bypassed"]
    attributes = entity.extra_state_attributes
    assert "authorized" not in attributes
    assert "bypassed" not in attributes
    assert attributes["last_seen"] == "Now"


async def test_a_port_that_lost_its_sfp_module_drops_the_sfp_attributes(hass):
    """The interface attributes depend on what the row holds, and are taken from it anew."""
    desc = port._make_description(func="MikrotikPortBinarySensor", data_path="interface", data_reference="name", data_name="name", data_attribute="running")
    row = {"name": "sfp1", "type": "ether", "running": True, "enabled": True, "rate": "1Gbps", "status": "link-ok", "sfp-shutdown-temperature": "80C", "sfp-temperature": "40C"}
    coord = port._make_coordinator(hass, {"interface": {"sfp1": row}}, options={CONF_SENSOR_PORT_TRACKER: True})
    entity = MikrotikPortBinarySensor(coord, desc, uid="sfp1")
    assert any("sfp" in key for key in entity.extra_state_attributes)

    del row["sfp-shutdown-temperature"]
    del row["sfp-temperature"]
    attributes = entity.extra_state_attributes
    assert not any("sfp" in key for key in attributes)
    assert attributes["rate"] == "1Gbps"

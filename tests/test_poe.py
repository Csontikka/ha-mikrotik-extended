"""Per port PoE measurements.

No test router has a port that supplies power, so the sample replies follow
the PoE-Out manual: `/interface/ethernet/poe monitor` adds `poe-out-voltage`,
`poe-out-current` and `poe-out-power` to a port while it measures, in volts,
milliamperes and watts.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant.components.sensor import SensorDeviceClass, SensorStateClass
from homeassistant.const import CONF_HOST, CONF_NAME, CONF_PASSWORD, CONF_PORT, CONF_SSL, CONF_USERNAME, CONF_VERIFY_SSL
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.mikrotik_extended.const import CONF_SENSOR_INTERFACES, DOMAIN
from custom_components.mikrotik_extended.coordinator import POE_READINGS, _poe_readings
from custom_components.mikrotik_extended.entity import _skip_sensor, _waits_for_poe_reading, async_add_entities
from custom_components.mikrotik_extended.sensor_types import SENSOR_TYPES

ENTRY_DATA = {
    CONF_HOST: "192.168.88.1",
    CONF_USERNAME: "admin",
    CONF_PASSWORD: "x",
    CONF_PORT: 0,
    CONF_SSL: False,
    CONF_VERIFY_SSL: False,
    CONF_NAME: "Mikrotik",
}

POWERED_API = {"name": "ether2", "poe-out": "auto-on", "poe-out-status": "powered-on", "poe-out-voltage": "54.2", "poe-out-current": "449", "poe-out-power": "24.3"}
POWERED_TERMINAL = {**POWERED_API, "poe-out-voltage": "54.2V", "poe-out-current": "449mA", "poe-out-power": "24.3W"}
POE_DESCRIPTIONS = [d for d in SENSOR_TYPES if d.func == "MikrotikPoeSensor"]


def _make_coordinator(hass):
    from custom_components.mikrotik_extended.coordinator import MikrotikCoordinator

    entry = MockConfigEntry(domain=DOMAIN, data=ENTRY_DATA, options={}, unique_id="192.168.88.1")
    entry.add_to_hass(hass)
    with patch("custom_components.mikrotik_extended.coordinator.MikrotikAPI") as mock_api:
        mock_api.return_value = MagicMock()
        coordinator = MikrotikCoordinator(hass, entry)
    coordinator.api = mock_api.return_value
    coordinator.config_entry = entry
    return coordinator


class TestReadings:
    @pytest.mark.parametrize("reply", [POWERED_API, POWERED_TERMINAL], ids=["api form", "terminal form"])
    def test_powered_port(self, reply):
        port = dict(reply)
        _poe_readings(port)
        assert (port["poe-out-voltage"], port["poe-out-current"], port["poe-out-power"]) == (54.2, 449, 24.3)
        assert port["poe-metered"] is True

    def test_numbers_the_parser_already_converted(self):
        port = {**POWERED_API, "poe-out-voltage": 54.2, "poe-out-current": 449, "poe-out-power": 24.3}
        _poe_readings(port)
        assert (port["poe-out-voltage"], port["poe-out-current"], port["poe-out-power"]) == (54.2, 449, 24.3)

    @pytest.mark.parametrize("status", ["disabled", "waiting-for-load", "short-circuit", "off"])
    def test_port_without_power_reads_zero_watts_and_nothing_else(self, status):
        port = {"name": "ether2", "poe-out": "off", "poe-out-status": status, **dict.fromkeys(POE_READINGS)}
        _poe_readings(port)
        assert port["poe-out-power"] == 0
        assert port["poe-out-voltage"] is None and port["poe-out-current"] is None
        assert "poe-metered" not in port, "zero is not a reading the router sent"

    def test_powered_port_that_does_not_measure_gets_no_number(self):
        port = {"name": "ether2", "poe-out": "forced-on", "poe-out-status": "powered-on", **dict.fromkeys(POE_READINGS)}
        _poe_readings(port)
        assert [port[field] for field in POE_READINGS] == [None, None, None]
        assert "poe-metered" not in port

    def test_unknown_status_is_not_turned_into_zero(self):
        port = {"name": "ether2", "poe-out": "auto-on", "poe-out-status": "unknown", **dict.fromkeys(POE_READINGS)}
        _poe_readings(port)
        assert port["poe-out-power"] is None

    def test_a_port_stays_metered_after_it_is_switched_off(self):
        port = dict(POWERED_API)
        _poe_readings(port)
        port.update({"poe-out-status": "disabled", **dict.fromkeys(POE_READINGS)})
        _poe_readings(port)
        assert port["poe-metered"] is True
        assert port["poe-out-power"] == 0 and port["poe-out-voltage"] is None

    def test_text_that_is_not_a_number_is_no_reading(self):
        port = {**POWERED_API, "poe-out-power": "n/a"}
        _poe_readings(port)
        assert port["poe-out-power"] is None
        assert port["poe-metered"] is True, "voltage and current did arrive"


class TestFetch:
    def test_readings_come_from_the_status_call(self, hass):
        """No extra query: the monitor reply that carries the status carries these too."""
        coord = _make_coordinator(hass)
        coord.ds["interface"] = {
            "ether1": {"name": "ether1", "poe-out": "off"},
            "ether2": {"name": "ether2", "poe-out": "auto-on"},
            "ether3": {"name": "ether3", "poe-out": "N/A"},
        }
        coord.api.query = MagicMock(return_value=[{"name": "ether1", "poe-out": "off", "poe-out-status": "disabled"}, dict(POWERED_API)])

        coord._fetch_poe_status()

        coord.api.query.assert_called_once()
        ether1, ether2, ether3 = (coord.ds["interface"][name] for name in ("ether1", "ether2", "ether3"))
        assert (ether2["poe-out-voltage"], ether2["poe-out-current"], ether2["poe-out-power"]) == (54.2, 449, 24.3)
        assert ether2["poe-metered"] is True
        assert ether1["poe-out-power"] == 0 and "poe-metered" not in ether1
        assert "poe-out-power" not in ether3 and "poe-metered" not in ether3, "a port without PoE is left alone"

    def test_readings_go_away_when_the_port_is_switched_off(self, hass):
        coord = _make_coordinator(hass)
        coord.ds["interface"] = {"ether2": {"name": "ether2", "poe-out": "auto-on"}}
        coord.api.query = MagicMock(return_value=[dict(POWERED_API)])
        coord._fetch_poe_status()

        coord.api.query = MagicMock(return_value=[{"name": "ether2", "poe-out": "off", "poe-out-status": "disabled"}])
        coord._fetch_poe_status()

        port = coord.ds["interface"]["ether2"]
        assert port["poe-out-power"] == 0 and port["poe-out-voltage"] is None and port["poe-out-current"] is None
        assert port["poe-metered"] is True


class TestDescriptions:
    def test_three_measurement_sensors(self):
        by_attribute = {d.data_attribute: d for d in POE_DESCRIPTIONS}
        assert set(by_attribute) == set(POE_READINGS)
        assert by_attribute["poe-out-power"].device_class == SensorDeviceClass.POWER
        assert by_attribute["poe-out-voltage"].device_class == SensorDeviceClass.VOLTAGE
        assert by_attribute["poe-out-current"].device_class == SensorDeviceClass.CURRENT
        assert by_attribute["poe-out-power"].native_unit_of_measurement == "W"
        assert by_attribute["poe-out-voltage"].native_unit_of_measurement == "V"
        assert by_attribute["poe-out-current"].native_unit_of_measurement == "mA"
        for description in POE_DESCRIPTIONS:
            assert description.state_class == SensorStateClass.MEASUREMENT
            assert description.data_path == "interface" and description.translation_key == description.key

    def test_names_are_translated_everywhere(self):
        import json
        from pathlib import Path

        base = Path(__file__).parent.parent / "custom_components" / "mikrotik_extended"
        for path in [base / "strings.json", *sorted((base / "translations").glob("*.json"))]:
            sensors = json.loads(path.read_text(encoding="utf-8"))["entity"]["sensor"]
            for description in POE_DESCRIPTIONS:
                assert sensors[description.key]["name"], f"{path.name}: {description.key}"


class TestCreation:
    def _entry(self, **options):
        entry = MagicMock()
        entry.options = options
        return entry

    @pytest.mark.parametrize("description", POE_DESCRIPTIONS, ids=lambda d: d.key)
    def test_waits_for_a_port_that_measures(self, description):
        assert _waits_for_poe_reading(description, {"poe-out": "auto-on", "poe-out-power": 0}) is True
        assert _waits_for_poe_reading(description, {"poe-out": "auto-on", "poe-metered": True}) is False

    def test_other_sensors_do_not_wait(self):
        other = next(d for d in SENSOR_TYPES if d.func == "MikrotikInterfaceTrafficSensor")
        assert _waits_for_poe_reading(other, {}) is False

    def test_follows_the_interface_entities_option(self):
        item = {"poe-metered": True, "type": "ether"}
        data = {"ether2": item}
        assert _skip_sensor(self._entry(**{CONF_SENSOR_INTERFACES: False}), POE_DESCRIPTIONS[0], data, "ether2") is True
        assert _skip_sensor(self._entry(**{CONF_SENSOR_INTERFACES: True}), POE_DESCRIPTIONS[0], data, "ether2") is False

    async def _run(self, hass, port, registered):
        entry = MockConfigEntry(domain=DOMAIN, data=ENTRY_DATA, options={}, unique_id="192.168.88.1")
        entry.add_to_hass(hass)
        coord = MagicMock()
        coord.data = {"interface": {"ether2": {"default-name": "ether2", "name": "ether2", "type": "ether", **port}}}
        coord.config_entry = entry
        entry.runtime_data = MagicMock(data_coordinator=coord)

        platform = MagicMock()
        platform.platform.SENSOR_SERVICES = []
        platform.platform.SENSOR_TYPES = tuple(POE_DESCRIPTIONS)
        platform.domain = "sensor"
        platform.entities = {}
        platform.async_add_entities = AsyncMock()

        registry = MagicMock()
        registry.async_get_entity_id = MagicMock(return_value="sensor.ether2_poe" if registered else None)
        known = MagicMock()
        known.disabled = False
        registry.async_get = MagicMock(return_value=known if registered else None)

        class _Fake:
            def __init__(self, coordinator, entity_description, uid):
                self.entity_description = entity_description
                self._data = coordinator.data["interface"][uid]

        with (
            patch("custom_components.mikrotik_extended.entity.ep.async_get_current_platform", return_value=platform),
            patch("custom_components.mikrotik_extended.entity.er.async_get", return_value=registry),
            patch("custom_components.mikrotik_extended.entity.er.async_entries_for_config_entry", return_value=[]),
            patch("custom_components.mikrotik_extended.entity.dr.async_get"),
            patch("custom_components.mikrotik_extended.entity.dr.async_entries_for_config_entry", return_value=[]),
        ):
            await async_add_entities(hass, entry, {"MikrotikPoeSensor": _Fake})
        return platform.async_add_entities.await_count

    async def test_no_sensor_for_a_port_that_never_measured(self, hass):
        assert await self._run(hass, {"poe-out": "off", "poe-out-power": 0}, registered=False) == 0

    async def test_sensors_appear_once_the_port_measures(self, hass):
        assert await self._run(hass, {"poe-out": "auto-on", "poe-metered": True, "poe-out-power": 24.3}, registered=False) == 3

    async def test_known_sensors_survive_a_restart_with_the_port_off(self, hass):
        """Otherwise the orphan cleanup would delete them until the port is powered again."""
        assert await self._run(hass, {"poe-out": "off", "poe-out-power": 0}, registered=True) == 3

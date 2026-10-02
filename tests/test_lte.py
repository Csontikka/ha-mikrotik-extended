"""LTE / 5G modem sensors.

The sample reply is what a user's ATL 5G R16 (modem RG520F-EU) printed for
`/interface/lte/monitor [find] once`, with the operator and the identifiers
replaced. No test router has a modem, so these samples are the ground truth.
"""

from unittest.mock import MagicMock, patch

import pytest
from homeassistant.const import CONF_HOST, CONF_NAME, CONF_PASSWORD, CONF_PORT, CONF_SSL, CONF_USERNAME, CONF_VERIFY_SSL
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.mikrotik_extended.const import DOMAIN
from custom_components.mikrotik_extended.coordinator import _lte_number, _lte_text, lte_row

IMEI, IMSI, ICCID = "860000000000001", "214000000000002", "8934000000000000003"

# As the terminal prints it: values carry their unit, the CA bands span lines.
TERMINAL_REPLY = {
    "status": "running",
    "model": "RG520F-EU",
    "revision": "RG520FEUEAR03A07M4GX",
    "current-operator": "Example Mobile",
    "current-cellid": "215752650",
    "enb-id": "842783",
    "sector-id": "202",
    "phy-cellid": "130",
    "data-class": "LTE",
    "session-uptime": "1h9m49s",
    "imei": IMEI,
    "imsi": IMSI,
    "iccid": ICCID,
    "primary-band": "B3@20Mhz earfcn: 1501 phy-cellid: 130",
    "ca-band": "B20@10Mhz earfcn: 6300 phy-cellid: 130\n                    B28@10Mhz earfcn: 9360 phy-cellid: 426",
    "dl-modulation": "qpsk",
    "cqi": "10",
    "ri": "2",
    "mcs": "0",
    "rssi": "-67dBm",
    "rsrp": "-95dBm",
    "rsrq": "-7dB",
    "sinr": "18dB",
}
# As the API hands it out on releases that send plain numbers.
API_REPLY = {**TERMINAL_REPLY, "current-cellid": 215752650, "enb-id": 842783, "sector-id": 202, "phy-cellid": 130, "cqi": 10, "ri": 2, "mcs": 0, "rssi": -67, "rsrp": -95, "rsrq": -7, "sinr": 18}
MODEM = {".id": "*1", "name": "lte1", "default-name": "lte1", "network-mode": "3g,lte,5g"}


def _make_coordinator(hass, options=None):
    from custom_components.mikrotik_extended.coordinator import MikrotikCoordinator

    entry = MockConfigEntry(
        domain=DOMAIN,
        data={CONF_HOST: "192.168.88.1", CONF_USERNAME: "admin", CONF_PASSWORD: "x", CONF_PORT: 0, CONF_SSL: False, CONF_VERIFY_SSL: False, CONF_NAME: "Mikrotik"},
        options=options or {"sensor_lte": True},
        unique_id="192.168.88.1",
    )
    entry.add_to_hass(hass)
    with patch("custom_components.mikrotik_extended.coordinator.MikrotikAPI") as mock_api:
        mock_api.return_value = MagicMock()
        coordinator = MikrotikCoordinator(hass, entry)
    coordinator.api = mock_api.return_value
    coordinator.config_entry = entry
    return coordinator


def _answers(coord, modems, monitor):
    """Route the two kinds of query: the modem list and the monitor command."""

    def query(path, command=None, args=None, **_):
        assert path == "/interface/lte"
        if command is None:
            return modems
        assert command == "monitor"
        return monitor[args[".id"]] if isinstance(monitor, dict) else monitor

    coord.api.query.side_effect = query


@pytest.mark.parametrize("reply", [TERMINAL_REPLY, API_REPLY], ids=["terminal form", "api form"])
class TestLteRow:
    def test_signal_values_are_numbers(self, reply):
        row = lte_row("lte1", MODEM, reply)
        assert (row["rssi"], row["rsrp"], row["rsrq"], row["sinr"], row["cqi"], row["ri"], row["mcs"]) == (-67, -95, -7, 18, 10, 2, 0)
        assert all(isinstance(row[key], int) for key in ("rssi", "rsrp", "rsrq", "sinr", "cqi"))

    def test_text_values(self, reply):
        row = lte_row("lte1", MODEM, reply)
        assert row["current-operator"] == "Example Mobile"
        assert row["data-class"] == "LTE"
        assert row["primary-band"] == "B3@20Mhz earfcn: 1501 phy-cellid: 130"
        assert row["model"] == "RG520F-EU"
        assert row["session-uptime"] == "1h9m49s"
        assert row["enb-id"] == "842783" and row["current-cellid"] == "215752650"

    def test_carrier_aggregation_bands_end_up_on_one_line(self, reply):
        assert lte_row("lte1", MODEM, reply)["ca-band"] == "B20@10Mhz earfcn: 6300 phy-cellid: 130; B28@10Mhz earfcn: 9360 phy-cellid: 426"

    def test_identifiers_never_reach_the_row(self, reply):
        row = lte_row("lte1", MODEM, reply)
        assert not {"imei", "imsi", "iccid", "uicc"} & set(row)
        flat = repr(row)
        for secret in (IMEI, IMSI, ICCID):
            assert secret not in flat

    def test_connected_and_names(self, reply):
        row = lte_row("lte1", MODEM, reply)
        assert row["connected"] is True
        assert row["name"] == "lte1" and row["uid-ref"] == "lte-lte1"
        renamed = lte_row("lte1", {**MODEM, "name": "wan-lte"}, reply)
        assert renamed["name"] == "wan-lte" and renamed["uid-ref"] == "lte-lte1"


class TestLteRowEdges:
    def test_status_words(self):
        assert lte_row("lte1", MODEM, {"status": "connected"})["connected"] is True
        for status in ("searching", "disabled", "registered", "", None):
            assert lte_row("lte1", MODEM, {"status": status})["connected"] is False, status

    def test_a_data_session_counts_as_connected_whatever_the_status_says(self):
        """Issue 37: an R11e-LTE6 reports "registered" while it carries traffic."""
        reply = {
            "status": "registered",
            "registration-status": "registered",
            "model": "R11e-LTE6",
            "revision": "R11e-LTE6_V039",
            "data-class": "LTE CA",
            "session-uptime": "1d2h49m32s",
            "primary-band": "B1@15Mhz earfcn: 525 phy-cellid: 288",
            "ca-band": "B20@5Mhz earfcn: 6275 phy-cellid: 288",
        }
        row = lte_row("lte1", MODEM, reply)
        assert row["connected"] is True
        assert row["data-class"] == "LTE CA" and row["session-uptime"] == "1d2h49m32s"
        assert lte_row("lte1", MODEM, {"status": "registered", "session-uptime": ""})["connected"] is False, "attached to the network, but no data session"

    def test_an_empty_reply_gives_empty_values_not_an_error(self):
        row = lte_row("lte1", MODEM, {})
        assert row["rssi"] is None and row["current-operator"] is None and row["ca-band"] is None
        assert row["connected"] is False

    def test_5g_capability_comes_from_the_modem_setup(self):
        assert lte_row("lte1", {"network-mode": "3g,lte,5g"}, {})["nr-capable"] is True
        assert lte_row("lte1", {"network-mode": "lte"}, {})["nr-capable"] is False
        assert lte_row("lte1", {}, {})["nr-capable"] is False

    def test_known_5g_values_are_numbers(self):
        row = lte_row("lte1", MODEM, {"nr-rsrp": "-101dBm", "nr-rsrq": "-11dB", "nr-sinr": "9dB"})
        assert (row["nr-rsrp"], row["nr-rsrq"], row["nr-sinr"]) == (-101, -11, 9)

    def test_a_real_5g_nsa_reply(self):
        """What the same modem printed while carrying traffic on 5G NSA."""
        reply = {
            **TERMINAL_REPLY,
            "data-class": "5G NSA",
            "ca-band": "n28@10Mhz earfcn: 154570 phy-cellid: 16\n                    B20@10Mhz earfcn: 6300 phy-cellid: 130",
            "cqi": "12",
            "ri": "1",
            "rssi": "-62dBm",
            "rsrq": "-12dB",
            "sinr": "14dB",
            "nr-dl-modulation": "16qam",
            "nr-rsrp": "-88dBm",
            "nr-rsrq": "-10dB",
            "nr-sinr": "11dB",
        }
        row = lte_row("lte1", MODEM, reply)
        assert row["data-class"] == "5G NSA"
        assert (row["nr-rsrp"], row["nr-rsrq"], row["nr-sinr"]) == (-88, -10, 11)
        assert (row["rssi"], row["rsrp"], row["rsrq"], row["sinr"], row["cqi"]) == (-62, -95, -12, 14, 12)
        assert row["nr-dl-modulation"] == "16qam"
        assert row["nr-other"] is None, "every 5G field this modem sends is known"
        assert row["ca-band"] == "n28@10Mhz earfcn: 154570 phy-cellid: 16; B20@10Mhz earfcn: 6300 phy-cellid: 130"

    def test_a_5g_cqi_would_surface_in_the_other_attribute(self):
        """No modem has shown one yet, so it has no sensor, but it must not vanish."""
        assert lte_row("lte1", MODEM, {"nr-cqi": 12})["nr-other"] == "nr-cqi=12"

    def test_unknown_5g_fields_are_kept_together_for_reporting(self):
        row = lte_row("lte1", MODEM, {"nr-rsrp": -101, "nr-band": "n78@100Mhz", "nr-phy-cellid": 77})
        assert row["nr-other"] == "nr-band=n78@100Mhz, nr-phy-cellid=77"
        assert lte_row("lte1", MODEM, TERMINAL_REPLY)["nr-other"] is None

    def test_5g_values_make_a_modem_5g_capable_even_without_a_network_mode(self):
        assert lte_row("lte1", {}, {"nr-rsrp": -101})["nr-capable"] is True

    def test_a_very_long_band_list_is_cut_to_what_a_state_can_hold(self):
        carriers = "\n".join(f"B{n}@20Mhz earfcn: {1000 + n} phy-cellid: {n}" for n in range(12))
        band = lte_row("lte1", MODEM, {"ca-band": carriers})["ca-band"]
        assert len(band) == 255 and band.endswith("...")

    def test_carrier_aggregation_as_a_list(self):
        assert lte_row("lte1", MODEM, {"ca-band": ["B20@10Mhz", "B28@10Mhz"]})["ca-band"] == "B20@10Mhz; B28@10Mhz"


@pytest.mark.parametrize(
    ("raw", "number"),
    [("-67dBm", -67), ("18dB", 18), ("+5dB", 5), ("10", 10), (10, 10), (-7.5, -7.5), ("-7.5dB", -7.5), (" -95 dBm", -95), ("", None), (None, None), ("n/a", None), (True, None), ("dBm", None)],
)
def test_lte_number(raw, number):
    assert _lte_number(raw) == number
    assert type(_lte_number(raw)) is type(number)


def test_lte_text():
    assert _lte_text("  a   b \n\n  c ") == "a b; c"
    assert _lte_text("") is None and _lte_text(None) is None and _lte_text("   ") is None
    assert _lte_text(130) == "130"


class TestGetLte:
    def test_router_without_a_modem_is_never_asked(self, hass):
        coord = _make_coordinator(hass)
        coord.ds["interface"] = {"ether1": {"type": "ether"}, "bridge": {"type": "bridge"}}
        coord.ds["lte"] = {"stale": {}}
        coord.get_lte()
        coord.api.query.assert_not_called()
        assert coord.ds["lte"] == {}

    def test_modem_is_read_through_the_monitor_command(self, hass):
        coord = _make_coordinator(hass)
        coord.ds["interface"] = {"lte1": {"type": "lte"}}
        _answers(coord, [MODEM], [API_REPLY])
        coord.get_lte()
        assert coord.ds["lte"]["lte1"]["rsrp"] == -95
        coord.api.query.assert_any_call("/interface/lte", command="monitor", args={".id": "*1", "once": True})

    def test_two_modems_are_read_separately(self, hass):
        coord = _make_coordinator(hass)
        coord.ds["interface"] = {"lte1": {"type": "lte"}, "lte2": {"type": "lte"}}
        second = {".id": "*2", "name": "lte2", "network-mode": "lte"}
        _answers(coord, [MODEM, second], {"*1": [API_REPLY], "*2": [{**API_REPLY, "rsrp": -110, "current-operator": "Other Net"}]})
        coord.get_lte()
        assert coord.ds["lte"]["lte1"]["rsrp"] == -95
        assert coord.ds["lte"]["lte2"]["rsrp"] == -110 and coord.ds["lte"]["lte2"]["current-operator"] == "Other Net"

    def test_one_failed_monitor_reply_keeps_the_last_reading(self, hass):
        """A refusal during a cell change must not read as a dropped link."""
        coord = _make_coordinator(hass)
        coord.ds["interface"] = {"lte1": {"type": "lte"}}
        _answers(coord, [MODEM], [API_REPLY])
        coord.get_lte()
        _answers(coord, [MODEM], None)
        coord.get_lte()
        coord.get_lte()
        row = coord.ds["lte"]["lte1"]
        assert row["rsrp"] == -95 and row["connected"] is True

    def test_a_monitor_that_keeps_failing_ends_as_unknown(self, hass):
        coord = _make_coordinator(hass)
        coord.ds["interface"] = {"lte1": {"type": "lte"}}
        _answers(coord, [MODEM], [API_REPLY])
        coord.get_lte()
        _answers(coord, [MODEM], None)
        for _ in range(3):
            coord.get_lte()
        row = coord.ds["lte"]["lte1"]
        assert row["rsrp"] is None and row["connected"] is False

    def test_the_miss_count_starts_over_after_a_good_reply(self, hass):
        coord = _make_coordinator(hass)
        coord.ds["interface"] = {"lte1": {"type": "lte"}}
        for monitor in ([API_REPLY], None, None, [API_REPLY], None, None):
            _answers(coord, [MODEM], monitor)
            coord.get_lte()
        assert coord.ds["lte"]["lte1"]["rsrp"] == -95

    def test_a_modem_never_read_shows_up_as_unknown_at_once(self, hass):
        """The sensors must exist from the first cycle, even without a reply."""
        coord = _make_coordinator(hass)
        coord.ds["interface"] = {"lte1": {"type": "lte"}}
        _answers(coord, [MODEM], None)
        coord.get_lte()
        row = coord.ds["lte"]["lte1"]
        assert row["rsrp"] is None and row["connected"] is False

    def test_renaming_the_modem_keeps_its_key(self, hass):
        coord = _make_coordinator(hass)
        coord.ds["interface"] = {"lte1": {"type": "lte"}}
        _answers(coord, [MODEM], [API_REPLY])
        coord.get_lte()
        _answers(coord, [{**MODEM, "name": "wan-lte"}], [API_REPLY])
        coord.get_lte()
        assert list(coord.ds["lte"]) == ["lte1"]
        row = coord.ds["lte"]["lte1"]
        assert row["name"] == "wan-lte", "the chosen name is what is shown"
        assert row["uid-ref"] == "lte-lte1", "the unique id does not follow the rename"

    def test_failed_list_read_keeps_what_is_known(self, hass):
        coord = _make_coordinator(hass)
        coord.ds["interface"] = {"lte1": {"type": "lte"}}
        _answers(coord, [MODEM], [API_REPLY])
        coord.get_lte()
        _answers(coord, None, [API_REPLY])
        coord.get_lte()
        assert coord.ds["lte"]["lte1"]["rsrp"] == -95

    def test_removed_modem_leaves_the_store(self, hass):
        coord = _make_coordinator(hass)
        coord.ds["interface"] = {"lte1": {"type": "lte"}, "lte2": {"type": "lte"}}
        _answers(coord, [MODEM, {".id": "*2", "name": "lte2"}], [API_REPLY])
        coord.get_lte()
        _answers(coord, [MODEM], [API_REPLY])
        coord.get_lte()
        assert list(coord.ds["lte"]) == ["lte1"]

    def test_identifiers_are_not_in_the_store(self, hass):
        coord = _make_coordinator(hass)
        coord.ds["interface"] = {"lte1": {"type": "lte"}}
        _answers(coord, [MODEM], [TERMINAL_REPLY])
        coord.get_lte()
        for secret in (IMEI, IMSI, ICCID):
            assert secret not in repr(coord.ds["lte"])

    def test_option_is_off_by_default(self, hass):
        assert _make_coordinator(hass, options={"scan_interval": 30}).option_sensor_lte is False
        assert _make_coordinator(hass).option_sensor_lte is True


def test_5g_sensors_only_for_a_5g_capable_modem():
    from custom_components.mikrotik_extended.entity import _skip_lte_sensor

    def desc(attribute, path="lte"):
        d = MagicMock()
        d.data_path = path
        d.data_attribute = attribute
        return d

    assert _skip_lte_sensor(desc("nr-rsrp"), {"nr-capable": False}) is True
    assert _skip_lte_sensor(desc("nr-rsrp"), {"nr-capable": True}) is False
    assert _skip_lte_sensor(desc("rsrp"), {"nr-capable": False}) is False, "LTE sensors always exist, so a restart while offline does not remove them"
    assert _skip_lte_sensor(desc("nr-rsrp", path="interface"), {}) is False


def test_lte_sensor_descriptions():
    from homeassistant.components.sensor import SensorDeviceClass

    from custom_components.mikrotik_extended.binary_sensor_types import SENSOR_TYPES as BINARY
    from custom_components.mikrotik_extended.sensor_types import DEVICE_ATTRIBUTES_LTE, SENSOR_TYPES

    sensors = {d.key: d for d in SENSOR_TYPES if d.data_path == "lte"}
    assert {d.data_attribute for d in sensors.values()} == {"current-operator", "data-class", "rssi", "rsrp", "rsrq", "sinr", "cqi", "primary-band", "ca-band", "nr-rsrp", "nr-rsrq", "nr-sinr"}
    for key in ("lte_rssi", "lte_rsrp", "lte_nr_rsrp"):
        assert sensors[key].native_unit_of_measurement == "dBm" and sensors[key].device_class is SensorDeviceClass.SIGNAL_STRENGTH
    for key in ("lte_rsrq", "lte_sinr", "lte_nr_rsrq", "lte_nr_sinr"):
        assert sensors[key].native_unit_of_measurement == "dB"
    everything = [d.data_attribute for d in sensors.values()] + DEVICE_ATTRIBUTES_LTE
    assert not {"imei", "imsi", "iccid", "uicc"} & set(everything)
    assert [d.data_attribute for d in BINARY if d.data_path == "lte"] == ["connected"]
    assert all(d.data_reference == "uid-ref" for d in sensors.values()), "the name of the modem goes in front, so two modems do not collide"
    assert "nr-dl-modulation" in DEVICE_ATTRIBUTES_LTE
    assert {d.ha_group for d in sensors.values()} == {"LTE modem"} and {d.ha_group for d in BINARY if d.data_path == "lte"} == {"LTE modem"}


def test_log_redaction_covers_the_modem_identifiers():
    """A debug dump of a raw reply must not carry them either."""
    from custom_components.mikrotik_extended.log_redaction import LogRedactor

    redactor = LogRedactor(b"salt")
    quoted = redactor.redact(f"raw response: [{{'imei': '{IMEI}', 'imsi': '{IMSI}', 'iccid': '{ICCID}', 'rsrp': -95}}]")
    # The API library turns all-digit values into numbers, so this is the
    # form a real reply has in a repr.
    numeric = redactor.redact(repr([{"imei": int(IMEI), "imsi": int(IMSI), "iccid": int(ICCID), "rsrp": -95, "enb-id": 842783}]))
    for masked in (quoted, numeric):
        for secret in (IMEI, IMSI, ICCID):
            assert secret not in masked
        assert "-95" in masked
    assert "842783" in numeric, "other numbers are left readable"


class TestLteEntities:
    """The real descriptions on the real entity classes, fed with a parsed row."""

    def _coordinator(self, hass, row):
        entry = MockConfigEntry(domain=DOMAIN, data={CONF_HOST: "192.168.88.1", CONF_NAME: "Mikrotik"}, options={"sensor_lte": True}, unique_id="192.168.88.1")
        entry.add_to_hass(hass)
        coord = MagicMock()
        coord.config_entry = entry
        coord.hass = hass
        coord.data = {"lte": {"lte1": row}, "routerboard": {"serial-number": "X"}, "resource": {"board-name": "B", "platform": "P", "version": "7"}}
        coord.core_device_id = None
        return coord

    def _sensors(self, hass, row):
        from custom_components.mikrotik_extended.sensor import MikrotikSensor
        from custom_components.mikrotik_extended.sensor_types import SENSOR_TYPES

        coord = self._coordinator(hass, row)
        return {d.key: MikrotikSensor(coord, d, uid="lte1") for d in SENSOR_TYPES if d.data_path == "lte"}

    def test_states_units_and_names(self, hass):
        sensors = self._sensors(hass, lte_row("lte1", MODEM, TERMINAL_REPLY))
        assert sensors["lte_rsrp"].native_value == -95 and sensors["lte_rsrp"].native_unit_of_measurement == "dBm"
        assert sensors["lte_sinr"].native_value == 18 and sensors["lte_sinr"].native_unit_of_measurement == "dB"
        assert sensors["lte_cqi"].native_value == 10
        assert sensors["lte_operator"].native_value == "Example Mobile"
        assert sensors["lte_technology"].native_value == "LTE"
        assert sensors["lte_ca_band"].native_value.startswith("B20@10Mhz")
        assert sensors["lte_rsrp"].custom_name == "lte1 RSRP"
        assert sensors["lte_nr_rsrp"].native_value is None, "on LTE the 5G sensor reads unknown"

    def test_unique_ids_are_per_modem_and_per_value(self, hass):
        sensors = self._sensors(hass, lte_row("lte1", MODEM, TERMINAL_REPLY))
        ids = {s.unique_id.split("-", 1)[1] for s in sensors.values()}
        assert len(ids) == len(sensors)
        assert sensors["lte_rsrp"].unique_id.endswith("-lte_rsrp-lte_lte1")

    def test_operator_sensor_carries_the_details_but_no_identifier(self, hass):
        sensors = self._sensors(hass, lte_row("lte1", MODEM, TERMINAL_REPLY))
        attrs = sensors["lte_operator"].extra_state_attributes
        flat = repr(attrs)
        for secret in (IMEI, IMSI, ICCID):
            assert secret not in flat
        assert "842783" in flat and "RG520F-EU" in flat and "1h9m49s" in flat

    def test_disconnected_modem_reads_unknown_not_an_error(self, hass):
        sensors = self._sensors(hass, lte_row("lte1", MODEM, {"status": "searching"}))
        assert all(s.native_value is None for s in sensors.values())

    def test_connection_binary_sensor(self, hass):
        from custom_components.mikrotik_extended.binary_sensor import MikrotikBinarySensor
        from custom_components.mikrotik_extended.binary_sensor_types import SENSOR_TYPES

        (desc,) = [d for d in SENSOR_TYPES if d.data_path == "lte"]
        up = MikrotikBinarySensor(self._coordinator(hass, lte_row("lte1", MODEM, TERMINAL_REPLY)), desc, uid="lte1")
        assert up.is_on is True and up.custom_name == "lte1 Connection"
        down = MikrotikBinarySensor(self._coordinator(hass, lte_row("lte1", MODEM, {"status": "searching"})), desc, uid="lte1")
        assert down.is_on is False

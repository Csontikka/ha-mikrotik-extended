"""IPv6 firewall rules as switches.

The sample rows are what RouterOS 7.25.1 answers on the four menus of
`/ipv6/firewall`: an enabled rule carries no `disabled` key, a port comes as a
number, `invalid` and `dynamic` are always there, and the list ids start again
in every menu.
"""

import glob
import json
import os
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import voluptuous_serialize
from homeassistant import config_entries
from homeassistant.const import CONF_HOST, CONF_NAME, CONF_PASSWORD, CONF_PORT, CONF_SSL, CONF_USERNAME, CONF_VERIFY_SSL
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers import config_validation as cv
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.mikrotik_extended.config_flow import _SENSOR_PRESETS
from custom_components.mikrotik_extended.const import CONF_SENSOR_IPV6_FIREWALL, DOMAIN, TO_REDACT
from custom_components.mikrotik_extended.coordinator import _IPV6_FIREWALL_MENUS, MikrotikCoordinator
from custom_components.mikrotik_extended.entity import _FIREWALL_GROUPS, REBIND_DATA_PATHS
from custom_components.mikrotik_extended.switch import MikrotikFilterSwitch
from custom_components.mikrotik_extended.switch_types import SENSOR_TYPES

ENTRY_DATA = {
    CONF_HOST: "192.168.88.1",
    CONF_USERNAME: "admin",
    CONF_PASSWORD: "x",
    CONF_PORT: 0,
    CONF_SSL: False,
    CONF_VERIFY_SSL: False,
    CONF_NAME: "Mikrotik",
}

FILTER = [
    {".id": "*5", "chain": "forward", "action": "drop", "protocol": "tcp", "src-address": "2001:db8:1::/64", "dst-port": 23, "bytes": 0, "packets": 0, "invalid": False, "dynamic": False, "comment": "drop telnet"},
    {".id": "*6", "chain": "input", "action": "accept", "protocol": "icmpv6", "bytes": 0, "packets": 0, "invalid": True, "dynamic": False},
    {".id": "*7", "chain": "forward", "action": "jump", "jump-target": "guests", "bytes": 0, "packets": 0, "invalid": True, "dynamic": False, "comment": "to guests"},
    {
        ".id": "*8",
        "chain": "forward",
        "action": "drop",
        "connection-state": "invalid",
        "dst-address-list": "blocked",
        "in-interface-list": "all",
        "bytes": 0,
        "packets": 0,
        "invalid": False,
        "dynamic": False,
        "disabled": True,
        "comment": "drop invalid",
    },
    {".id": "*9", "chain": "forward", "action": "accept", "bytes": 0, "packets": 0, "invalid": False, "dynamic": True, "comment": "added by a service"},
]
RAW = [
    {".id": "*1", "chain": "prerouting", "action": "drop", "in-interface": "ether2", "dst-port": 53, "bytes": 0, "packets": 0, "protocol": "udp", "invalid": False, "dynamic": False, "comment": "drop dns from wan"},
    {
        ".id": "*2",
        "chain": "prerouting",
        "action": "add-src-to-address-list",
        "bytes": 0,
        "packets": 0,
        "protocol": "tcp",
        "address-list": "scanners",
        "address-list-timeout": "1h",
        "tcp-flags": "fin,syn",
        "invalid": True,
        "dynamic": False,
    },
]
NAT = [
    {".id": "*1", "chain": "srcnat", "action": "masquerade", "out-interface": "ether2", "bytes": 0, "packets": 0, "invalid": False, "dynamic": False, "comment": "masquerade"},
    {
        ".id": "*2",
        "chain": "dstnat",
        "action": "dst-nat",
        "to-address": "2001:db8:2::10/128",
        "to-ports": 80,
        "protocol": "tcp",
        "in-interface": "ether2",
        "dst-port": 8080,
        "bytes": 0,
        "packets": 0,
        "invalid": True,
        "dynamic": False,
    },
]
MANGLE = [
    {
        ".id": "*1",
        "chain": "prerouting",
        "action": "mark-connection",
        "new-connection-mark": "guests",
        "passthrough": True,
        "src-address": "2001:db8:3::/64",
        "bytes": 0,
        "packets": 0,
        "invalid": False,
        "dynamic": False,
        "comment": "mark guests",
    },
    {".id": "*2", "chain": "forward", "action": "change-mss", "new-mss": "clamp-to-pmtu", "passthrough": False, "protocol": "tcp", "tcp-flags": "syn", "bytes": 0, "packets": 0, "invalid": True, "dynamic": False},
]
MENUS = {
    "/ipv6/firewall/filter": FILTER,
    "/ipv6/firewall/raw": RAW,
    "/ipv6/firewall/nat": NAT,
    "/ipv6/firewall/mangle": MANGLE,
}
STORES = ("ipv6_filter", "ipv6_raw", "ipv6_nat", "ipv6_mangle")
IPV6_DESCRIPTIONS = {d.key: d for d in SENSOR_TYPES if d.key.startswith("ipv6_")}


def _make_coordinator(hass, options=None):
    entry = MockConfigEntry(domain=DOMAIN, data=ENTRY_DATA, options=options or {}, unique_id="192.168.88.1")
    entry.add_to_hass(hass)
    with patch("custom_components.mikrotik_extended.coordinator.MikrotikAPI") as mock_api:
        mock_api.return_value = MagicMock()
        coordinator = MikrotikCoordinator(hass, entry)
    coordinator.api = mock_api.return_value
    coordinator.config_entry = entry
    return coordinator


def _fetch(hass, menus=None, coordinator=None):
    """Run one read of the four menus; a menu left out of ``menus`` answers empty."""
    menus = MENUS if menus is None else menus
    coordinator = coordinator or _make_coordinator(hass)
    coordinator.api.query.side_effect = lambda path: menus.get(path, [])
    coordinator.get_ipv6_firewall()
    return coordinator


# ---------------------------------------------------------------------------
# Reading the menus
# ---------------------------------------------------------------------------
class TestReadingTheMenus:
    def test_every_menu_is_read_once(self, hass):
        coord = _fetch(hass)
        assert [call.args[0] for call in coord.api.query.call_args_list] == list(MENUS)

    def test_each_menu_has_a_store_of_its_own(self, hass):
        """The list ids repeat across menus: "*1" is a raw, a NAT and a mangle rule."""
        coord = _fetch(hass)
        assert coord.ds["ipv6_raw"]["*1"]["action"] == "drop"
        assert coord.ds["ipv6_nat"]["*1"]["action"] == "masquerade"
        assert coord.ds["ipv6_mangle"]["*1"]["action"] == "mark-connection"

    def test_jump_and_dynamic_rules_are_left_out(self, hass):
        coord = _fetch(hass)
        assert set(coord.ds["ipv6_filter"]) == {"*5", "*6", "*8"}

    def test_rule_without_a_disabled_flag_is_enabled(self, hass):
        rules = _fetch(hass).ds["ipv6_filter"]
        assert rules["*5"]["enabled"] is True
        assert rules["*6"]["enabled"] is True
        assert rules["*8"]["enabled"] is False

    @pytest.mark.parametrize("store", STORES)
    def test_every_stored_rule_carries_what_a_switch_needs(self, hass, store):
        rules = _fetch(hass).ds[store]
        assert rules
        for rule in rules.values():
            assert isinstance(rule["enabled"], bool)
            assert rule[".id"].startswith("*")
            assert rule["uniq-id"] and rule["legacy-uniq-id"] and rule["name"]
            assert isinstance(rule["comment"], str)

    def test_unset_matchers_read_as_any(self, hass):
        rule = _fetch(hass).ds["ipv6_filter"]["*6"]
        for matcher in ("src-address", "dst-address", "dst-port", "in-interface", "out-interface-list", "connection-state", "tcp-flags"):
            assert rule[matcher] == "any", matcher
        assert rule["protocol"] == "icmpv6"

    def test_a_numeric_comment_becomes_text(self, hass):
        rows = [{".id": "*1", "chain": "input", "action": "drop", "comment": 2024}]
        rule = _fetch(hass, {"/ipv6/firewall/filter": rows}).ds["ipv6_filter"]["*1"]
        assert rule["comment"] == "2024"
        assert rule["uniq-id"] == "2024"

    def test_a_comment_in_a_legacy_codepage_is_decoded(self, hass):
        rows = [{".id": "*1", "chain": "input", "action": "drop", "comment": "vend\xc3\xa9g"}]
        rule = _fetch(hass, {"/ipv6/firewall/filter": rows}).ds["ipv6_filter"]["*1"]
        assert rule["comment"] == "vendég"
        assert rule["uniq-id"] == "vendég"
        assert rule["comment-raw"] == "vend\xc3\xa9g"


# ---------------------------------------------------------------------------
# What a rule is called
# ---------------------------------------------------------------------------
class TestRuleReference:
    def test_commented_rule_is_keyed_by_its_comment(self, hass):
        rule = _fetch(hass).ds["ipv6_filter"]["*5"]
        assert rule["uniq-id"] == "drop telnet"
        assert rule["name"] == "drop,tcp:23"
        assert "2001:db8:1::/64" in rule["legacy-uniq-id"]
        assert rule["legacy-uniq-id"].startswith("forward,drop,tcp,")

    def test_uncommented_rule_is_keyed_by_its_contents(self, hass):
        rule = _fetch(hass).ds["ipv6_filter"]["*6"]
        assert rule["uniq-id"] == rule["legacy-uniq-id"]
        assert rule["uniq-id"].startswith("input,accept,icmpv6,")

    def test_editing_a_commented_rule_keeps_its_reference(self, hass):
        coord = _fetch(hass)
        edited = [{**FILTER[0], "dst-port": 2323}]
        _fetch(hass, {"/ipv6/firewall/filter": edited}, coord)
        rule = coord.ds["ipv6_filter"]["*5"]
        assert rule["uniq-id"] == "drop telnet"
        assert rule["name"] == "drop,tcp:2323"

    def test_identical_uncommented_rules_both_stay(self, hass, caplog):
        twin = {"chain": "forward", "action": "drop", "protocol": "tcp", "dst-port": 23}
        menus = {"/ipv6/firewall/filter": [{".id": "*1", **twin}, {".id": "*2", **twin}]}
        with caplog.at_level("INFO"):
            coord = _fetch(hass, menus)
            _fetch(hass, menus, coord)
        ids = [rule["uniq-id"] for rule in coord.ds["ipv6_filter"].values()]
        assert len(set(ids)) == 2
        assert ids[0].endswith(" #1") and ids[1].endswith(" #2")
        assert sum("duplicate rule" in record.message for record in caplog.records) == 1

    def test_same_comment_on_two_rules_gets_a_suffix(self, hass):
        menus = {
            "/ipv6/firewall/filter": [
                {".id": "*1", "chain": "forward", "action": "drop", "comment": "guests"},
                {".id": "*2", "chain": "input", "action": "drop", "comment": "guests"},
            ]
        }
        ids = {rule["uniq-id"] for rule in _fetch(hass, menus).ds["ipv6_filter"].values()}
        assert ids == {"guests #1", "guests #2"}

    def test_raw_rules_differing_only_in_address_list_or_flags_stay_apart(self, hass):
        base = {"chain": "prerouting", "action": "add-src-to-address-list", "protocol": "tcp"}
        rows = [
            {".id": "*1", **base, "address-list": "scanners"},
            {".id": "*2", **base, "address-list": "flooders"},
            {".id": "*3", **base, "address-list": "scanners", "tcp-flags": "fin,syn"},
        ]
        coord = _fetch(hass, {"/ipv6/firewall/raw": rows})
        assert len({rule["uniq-id"] for rule in coord.ds["ipv6_raw"].values()}) == 3
        assert coord.ipv6_firewall_removed == set()

    def test_nat_rules_differing_only_in_target_stay_apart(self, hass):
        base = {"chain": "dstnat", "action": "dst-nat", "protocol": "tcp", "dst-port": 443}
        rows = [
            {".id": "*1", **base, "to-address": "2001:db8::10/128"},
            {".id": "*2", **base, "to-address": "2001:db8::11/128"},
            {".id": "*3", **base, "to-address": "2001:db8::10/128", "to-ports": 8443},
        ]
        coord = _fetch(hass, {"/ipv6/firewall/nat": rows})
        assert len({rule["uniq-id"] for rule in coord.ds["ipv6_nat"].values()}) == 3
        assert coord.ipv6_firewall_removed == set()

    def test_mangle_rules_differing_only_in_mark_stay_apart(self, hass):
        base = {"chain": "prerouting", "action": "mark-packet", "protocol": "udp"}
        rows = [
            {".id": "*1", **base, "new-packet-mark": "voice"},
            {".id": "*2", **base, "new-packet-mark": "video"},
        ]
        coord = _fetch(hass, {"/ipv6/firewall/mangle": rows})
        assert len({rule["uniq-id"] for rule in coord.ds["ipv6_mangle"].values()}) == 2
        assert coord.ipv6_firewall_removed == set()


# ---------------------------------------------------------------------------
# The four menus
# ---------------------------------------------------------------------------
class TestMenuSpecifics:
    def test_nat_keeps_every_action(self, hass):
        """Unlike the IPv4 NAT switches, which exist for dst-nat rules only."""
        rules = _fetch(hass).ds["ipv6_nat"]
        assert {rule["action"] for rule in rules.values()} == {"masquerade", "dst-nat"}
        assert rules["*2"]["to-address"] == "2001:db8:2::10/128"
        assert rules["*2"]["to-ports"] == 80
        assert rules["*2"]["name"] == "dst-nat,tcp:8080"

    def test_mangle_reads_passthrough_and_marks(self, hass):
        rules = _fetch(hass).ds["ipv6_mangle"]
        assert rules["*1"]["passthrough"] is True
        assert rules["*1"]["new-connection-mark"] == "guests"
        assert rules["*2"]["passthrough"] is False

    def test_raw_reads_the_address_list(self, hass):
        rule = _fetch(hass).ds["ipv6_raw"]["*2"]
        assert rule["address-list"] == "scanners"
        assert rule["tcp-flags"] == "fin,syn"


# ---------------------------------------------------------------------------
# Rules that come and go
# ---------------------------------------------------------------------------
class TestRulesThatComeAndGo:
    def test_a_failed_read_keeps_the_rules(self, hass):
        coord = _fetch(hass)
        before = json.dumps(coord.ds["ipv6_filter"], sort_keys=True)
        for _ in range(5):
            _fetch(hass, {path: None for path in MENUS}, coord)
        assert json.dumps(coord.ds["ipv6_filter"], sort_keys=True) == before

    def test_a_router_without_the_menu_has_no_rules(self, hass):
        """A missing menu reads as a failed query, on every poll."""
        coord = _make_coordinator(hass)
        for _ in range(4):
            _fetch(hass, {path: None for path in MENUS}, coord)
        assert all(coord.ds[store] == {} for store in STORES)

    def test_a_deleted_rule_leaves_after_three_reads(self, hass):
        coord = _fetch(hass)
        remaining = {**MENUS, "/ipv6/firewall/filter": FILTER[1:]}
        _fetch(hass, remaining, coord)
        _fetch(hass, remaining, coord)
        assert "*5" in coord.ds["ipv6_filter"]
        _fetch(hass, remaining, coord)
        assert "*5" not in coord.ds["ipv6_filter"]

    def test_the_last_rule_deleted_leaves_too(self, hass):
        coord = _fetch(hass)
        for _ in range(3):
            _fetch(hass, {}, coord)
        assert all(coord.ds[store] == {} for store in STORES)

    def test_a_rule_gone_from_one_menu_does_not_take_its_namesake(self, hass):
        """The stale counters are kept per store, like the rules."""
        coord = _fetch(hass)
        without_raw = {**MENUS, "/ipv6/firewall/raw": []}
        for _ in range(3):
            _fetch(hass, without_raw, coord)
        assert coord.ds["ipv6_raw"] == {}
        assert set(coord.ds["ipv6_nat"]) == {"*1", "*2"}
        assert set(coord.ds["ipv6_mangle"]) == {"*1", "*2"}

    def test_a_recreated_rule_does_not_collide_with_its_dead_row(self, hass):
        coord = _fetch(hass)
        recreated = {**MENUS, "/ipv6/firewall/filter": [{**FILTER[0], ".id": "*A"}, *FILTER[1:]]}
        _fetch(hass, recreated, coord)
        assert coord.ds["ipv6_filter"]["*A"]["uniq-id"] == "drop telnet"
        assert coord.ds["ipv6_filter"]["*5"]["uniq-id"] == "drop telnet"
        assert coord.ipv6_firewall_removed == set()


# ---------------------------------------------------------------------------
# The option
# ---------------------------------------------------------------------------
class TestOption:
    def test_option_is_off_by_default(self, hass):
        assert _make_coordinator(hass).option_sensor_ipv6_firewall is False
        assert _make_coordinator(hass, {CONF_SENSOR_IPV6_FIREWALL: True}).option_sensor_ipv6_firewall is True

    def test_only_the_full_preset_turns_it_on(self):
        assert {name: preset[CONF_SENSOR_IPV6_FIREWALL] for name, preset in _SENSOR_PRESETS.items()} == {
            "core": False,
            "minimal": False,
            "recommended": False,
            "full": True,
        }

    @pytest.mark.parametrize(("options", "fetched"), [({}, False), ({CONF_SENSOR_IPV6_FIREWALL: True}, True)])
    async def test_the_menus_are_read_only_with_the_option_on(self, hass, options, fetched):
        coordinator = _make_coordinator(hass, options)
        for attr in dir(coordinator):
            if attr.startswith(("get_", "process_", "sync_")):
                setattr(coordinator, attr, MagicMock(return_value=None))
        coordinator.async_process_host = AsyncMock()
        coordinator.api.has_reconnected.return_value = False
        coordinator.last_hwinfo_update = datetime.now()
        coordinator.api.connected.return_value = True
        coordinator.api.error = ""

        with (
            patch("custom_components.mikrotik_extended.coordinator.async_create_issue", MagicMock()),
            patch("custom_components.mikrotik_extended.coordinator.async_delete_issue", MagicMock()),
            patch("custom_components.mikrotik_extended.coordinator.async_dispatcher_send"),
        ):
            await coordinator._async_update_data()

        assert coordinator.get_ipv6_firewall.called is fetched

    async def test_the_option_is_on_the_setup_form(self, hass):
        with (
            patch("custom_components.mikrotik_extended.config_flow.async_scan_mndp", return_value=[]),
            patch("custom_components.mikrotik_extended.config_flow.MikrotikAPI") as mock_api_cls,
        ):
            mock_api_cls.return_value.connect.return_value = True
            mock_api_cls.return_value.error = None
            result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": config_entries.SOURCE_USER})
            result = await hass.config_entries.flow.async_configure(result["flow_id"], {"scan": False})
            result = await hass.config_entries.flow.async_configure(result["flow_id"], {CONF_NAME: "Mikrotik", CONF_HOST: "192.168.88.1", CONF_USERNAME: "admin", CONF_PASSWORD: "test", CONF_PORT: 0, "ssl_mode": "none"})
            result = await hass.config_entries.flow.async_configure(result["flow_id"], {"scan_interval": 30, "track_network_hosts_timeout": 180, "zone": "home"})
            result = await hass.config_entries.flow.async_configure(result["flow_id"], {"sensor_preset": "custom"})
            assert result["step_id"] == "sensor_select"
            fields = {f["name"]: f for f in voluptuous_serialize.convert(result["data_schema"], custom_serializer=cv.custom_serializer)}
            assert fields[CONF_SENSOR_IPV6_FIREWALL]["default"] is False

            result = await hass.config_entries.flow.async_configure(result["flow_id"], {CONF_SENSOR_IPV6_FIREWALL: True})
        assert result["type"] == FlowResultType.CREATE_ENTRY
        assert result["options"][CONF_SENSOR_IPV6_FIREWALL] is True

    @pytest.mark.parametrize("stored", [True, False])
    async def test_the_options_form_shows_the_stored_value(self, hass, stored):
        entry = MockConfigEntry(domain=DOMAIN, data=ENTRY_DATA, options={CONF_SENSOR_IPV6_FIREWALL: stored}, unique_id="192.168.88.1")
        entry.add_to_hass(hass)
        result = await hass.config_entries.options.async_init(entry.entry_id)
        while result["type"] == FlowResultType.FORM and result["step_id"] != "sensor_mode":
            result = await hass.config_entries.options.async_configure(result["flow_id"], {})
        result = await hass.config_entries.options.async_configure(result["flow_id"], {"sensor_preset": "custom"})
        fields = {f["name"]: f for f in voluptuous_serialize.convert(result["data_schema"], custom_serializer=cv.custom_serializer)}
        assert fields[CONF_SENSOR_IPV6_FIREWALL]["default"] is stored

        result = await hass.config_entries.options.async_configure(result["flow_id"], {CONF_SENSOR_IPV6_FIREWALL: not stored})
        assert result["type"] == FlowResultType.CREATE_ENTRY
        assert result["data"][CONF_SENSOR_IPV6_FIREWALL] is (not stored)

    def test_every_language_names_and_describes_the_option(self):
        base = os.path.join(os.path.dirname(__file__), "..", "custom_components", "mikrotik_extended")
        files = [os.path.join(base, "strings.json"), *glob.glob(os.path.join(base, "translations", "*.json"))]
        assert len(files) == 27
        for path in files:
            with open(path, encoding="utf-8") as handle:
                text = json.load(handle)
            for flow in ("config", "options"):
                step = text[flow]["step"]["sensor_select"]
                label = step["data"][CONF_SENSOR_IPV6_FIREWALL]
                described = step["data_description"][CONF_SENSOR_IPV6_FIREWALL]
                assert "IPv6" in label and "IPv6" in described, path
                assert "—" not in label + described, path
                assert set(step["data"]) == set(step["data_description"]), path


# ---------------------------------------------------------------------------
# The switches
# ---------------------------------------------------------------------------
def _switch(hass, store, uid, data=None):
    coordinator = MagicMock()
    coordinator.config_entry = MockConfigEntry(domain=DOMAIN, data=ENTRY_DATA, options={}, unique_id="192.168.88.1", entry_id="entry1")
    coordinator.config_entry.add_to_hass(hass)
    coordinator.hass = hass
    coordinator.host = "192.168.88.1"
    coordinator.set_value = MagicMock(return_value=True)
    coordinator.async_refresh = AsyncMock()
    coordinator._get_stale_counters = MagicMock(return_value={})
    coordinator.data = data or {**{name: _fetch(hass).ds[name] for name in STORES}, "access": {"write"}}
    switch = MikrotikFilterSwitch(coordinator, IPV6_DESCRIPTIONS[store], uid=uid)
    switch.hass = hass
    return switch


class TestSwitches:
    def test_there_is_a_description_per_menu(self):
        assert set(IPV6_DESCRIPTIONS) == set(STORES) == set(_IPV6_FIREWALL_MENUS)
        for store, description in IPV6_DESCRIPTIONS.items():
            assert description.data_path == store
            assert description.data_switch_path == _IPV6_FIREWALL_MENUS[store][0]
            assert description.data_attribute == "enabled"
            assert description.data_switch_parameter == "disabled"
            assert description.func == "MikrotikFilterSwitch"
            assert description.enable_on_option == CONF_SENSOR_IPV6_FIREWALL
            assert description.entity_registry_enabled_default is False
            assert description.ha_group in _FIREWALL_GROUPS
            assert description.ha_group.startswith("IPv6 ")
            assert description.ha_connection_value == description.ha_group
            assert store in REBIND_DATA_PATHS

    def test_the_rule_stores_migrate_with_the_comment_key(self):
        """The description key is the store name: the migration builds ids from it."""
        assert set(STORES) <= set(MikrotikCoordinator._COMMENT_KEYED_RULES)
        assert set(STORES) <= set(MikrotikCoordinator._TEXT_FIELDS)

    def test_every_attribute_of_a_description_is_read(self, hass):
        coord = _fetch(hass)
        for store, description in IPV6_DESCRIPTIONS.items():
            for rule in coord.ds[store].values():
                missing = [name for name in description.data_attributes_list if name not in rule]
                assert not missing, (store, missing)

    @pytest.mark.parametrize(("store", "uid"), [("ipv6_filter", "*5"), ("ipv6_raw", "*1"), ("ipv6_nat", "*1"), ("ipv6_mangle", "*1")])
    async def test_a_switch_toggles_its_own_rule_on_its_own_menu(self, hass, store, uid):
        switch = _switch(hass, store, uid)
        path = _IPV6_FIREWALL_MENUS[store][0]
        assert switch.is_on is True

        await switch.async_turn_off()
        assert switch.coordinator.set_value.call_args.args == (path, ".id", uid, "disabled", True)
        await switch.async_turn_on()
        assert switch.coordinator.set_value.call_args.args == (path, ".id", uid, "disabled", False)
        assert switch.coordinator.async_refresh.await_count == 2

    def test_a_disabled_rule_reads_off(self, hass):
        assert _switch(hass, "ipv6_filter", "*8").is_on is False

    def test_the_switch_is_named_after_the_comment(self, hass):
        assert _switch(hass, "ipv6_filter", "*5").name == "drop telnet"
        assert _switch(hass, "ipv6_filter", "*6").name == "accept,icmpv6:any"

    def test_the_same_comment_on_an_ipv4_rule_is_another_entity(self, hass):
        ipv4 = next(d for d in SENSOR_TYPES if d.key == "filter")
        ipv6 = _switch(hass, "ipv6_filter", "*5")
        assert ipv6.unique_id == "entry1-ipv6_filter-drop_telnet"
        assert ipv6.unique_id != f"entry1-{ipv4.key}-drop_telnet"

    def test_the_same_comment_in_two_ipv6_menus_gives_two_entities(self, hass):
        rule = {"chain": "prerouting", "action": "drop", "comment": "block"}
        coord = _fetch(hass, {"/ipv6/firewall/filter": [{".id": "*1", **rule, "chain": "input"}], "/ipv6/firewall/raw": [{".id": "*1", **rule}]})
        data = {**{name: coord.ds[name] for name in STORES}, "access": {"write"}}
        ids = {_switch(hass, store, "*1", data).unique_id for store in ("ipv6_filter", "ipv6_raw")}
        assert ids == {"entry1-ipv6_filter-block", "entry1-ipv6_raw-block"}

    def test_attributes_show_the_rule(self, hass):
        attributes = _switch(hass, "ipv6_nat", "*2").extra_state_attributes
        assert attributes["to_address"] == "2001:db8:2::10/128"
        assert attributes["to_ports"] == 80
        assert attributes["chain"] == "dstnat"
        assert _switch(hass, "ipv6_mangle", "*1").extra_state_attributes["passthrough"] is True

    async def test_a_switch_follows_its_recreated_rule(self, hass):
        coord = _fetch(hass)
        data = {**{name: coord.ds[name] for name in STORES}, "access": {"write"}}
        switch = _switch(hass, "ipv6_filter", "*5", data)
        switch.async_write_ha_state = MagicMock()

        _fetch(hass, {**MENUS, "/ipv6/firewall/filter": [{**FILTER[0], ".id": "*A"}, *FILTER[1:]]}, coord)
        switch.coordinator._get_stale_counters = coord._get_stale_counters
        switch._handle_coordinator_update()

        await switch.async_turn_off()
        assert switch.coordinator.set_value.call_args.args == ("/ipv6/firewall/filter", ".id", "*A", "disabled", True)

    async def test_a_switch_does_not_follow_another_rule_with_its_comment(self, hass):
        """The comment alone must not hand the switch a different rule."""
        coord = _fetch(hass)
        data = {**{name: coord.ds[name] for name in STORES}, "access": {"write"}}
        switch = _switch(hass, "ipv6_filter", "*5", data)
        switch.async_write_ha_state = MagicMock()

        other = {".id": "*B", "chain": "input", "action": "accept", "protocol": "udp", "comment": "drop telnet"}
        _fetch(hass, {**MENUS, "/ipv6/firewall/filter": [other, *FILTER[1:]]}, coord)
        switch.coordinator._get_stale_counters = coord._get_stale_counters
        switch._handle_coordinator_update()

        await switch.async_turn_off()
        assert switch.coordinator.set_value.call_args.args[2] == "*5"

    async def test_without_write_access_nothing_is_sent(self, hass):
        coord = _fetch(hass)
        data = {**{name: coord.ds[name] for name in STORES}, "access": set()}
        switch = _switch(hass, "ipv6_filter", "*5", data)
        with pytest.raises(Exception):  # noqa: B017 - the translated error type is asserted elsewhere
            await switch.async_turn_off()
        switch.coordinator.set_value.assert_not_called()

    def test_the_nat_target_address_is_redacted_in_diagnostics(self):
        assert "to-address" in TO_REDACT

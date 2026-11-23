"""Interface list members as switches.

The sample rows are what RouterOS 7.25.1 answers on `/interface/list/member`:
every row carries `disabled` and `dynamic`, also when both are false, and the
router refuses a second member with the same list and interface.
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
from homeassistant.util import slugify
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.mikrotik_extended.config_flow import _SENSOR_PRESETS
from custom_components.mikrotik_extended.const import CONF_SENSOR_INTERFACE_LISTS, DOMAIN
from custom_components.mikrotik_extended.coordinator import MikrotikCoordinator
from custom_components.mikrotik_extended.entity import REBIND_DATA_PATHS
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
PATH = "/interface/list/member"
STORE = "interface_list_member"
MEMBERS = [
    {".id": "*1", "list": "LAN", "interface": "bridge", "dynamic": False, "disabled": False, "comment": "defconf"},
    {".id": "*2", "list": "WAN", "interface": "ether1", "dynamic": False, "disabled": False},
    {".id": "*3", "list": "LAN", "interface": "ether5", "dynamic": False, "disabled": True, "comment": "guest port"},
    {".id": "*4", "list": "WAN", "interface": "pppoe-out1", "dynamic": True, "disabled": False},
]
DESCRIPTION = next(d for d in SENSOR_TYPES if d.key == STORE)


def _make_coordinator(hass, options=None):
    entry = MockConfigEntry(domain=DOMAIN, data=ENTRY_DATA, options=options or {}, unique_id="192.168.88.1")
    entry.add_to_hass(hass)
    with patch("custom_components.mikrotik_extended.coordinator.MikrotikAPI") as mock_api:
        mock_api.return_value = MagicMock()
        coordinator = MikrotikCoordinator(hass, entry)
    coordinator.api = mock_api.return_value
    coordinator.config_entry = entry
    return coordinator


def _fetch(hass, rows=MEMBERS, coordinator=None):
    """Run one read of the member menu; ``None`` is a read that failed."""
    coordinator = coordinator or _make_coordinator(hass)
    coordinator.api.query.side_effect = lambda path: rows if path == PATH else []
    coordinator.get_interface_list_members()
    return coordinator


def _switch(hass, uid, data=None):
    coordinator = MagicMock()
    coordinator.config_entry = MockConfigEntry(domain=DOMAIN, data=ENTRY_DATA, options={}, unique_id="192.168.88.1", entry_id="entry1")
    coordinator.config_entry.add_to_hass(hass)
    coordinator.hass = hass
    coordinator.host = "192.168.88.1"
    coordinator.set_value = MagicMock(return_value=True)
    coordinator.async_refresh = AsyncMock()
    coordinator._get_stale_counters = MagicMock(return_value={})
    coordinator.data = data or {STORE: _fetch(hass).ds[STORE], "access": {"write"}}
    coordinator.data.setdefault("resource", {"board-name": "CHR", "platform": "MikroTik"})
    coordinator.data.setdefault("routerboard", {"serial-number": "SN1"})
    switch = MikrotikFilterSwitch(coordinator, DESCRIPTION, uid=uid)
    switch.hass = hass
    return switch


# ---------------------------------------------------------------------------
# Reading the menu
# ---------------------------------------------------------------------------
class TestReadingTheMembers:
    def test_the_menu_is_read_once(self, hass):
        coord = _fetch(hass)
        assert [call.args[0] for call in coord.api.query.call_args_list] == [PATH]

    def test_dynamic_members_are_left_out(self, hass):
        assert set(_fetch(hass).ds[STORE]) == {"*1", "*2", "*3"}

    def test_a_member_is_enabled_unless_the_router_says_disabled(self, hass):
        members = _fetch(hass).ds[STORE]
        assert [members[uid]["enabled"] for uid in ("*1", "*2", "*3")] == [True, True, False]

    def test_a_member_without_a_disabled_flag_is_enabled(self, hass):
        """RouterOS leaves the flag out of some menus for an enabled row."""
        coord = _fetch(hass, [{".id": "*1", "list": "LAN", "interface": "bridge", "dynamic": False}])
        assert coord.ds[STORE]["*1"]["enabled"] is True

    def test_a_member_is_known_by_its_list_and_interface(self, hass):
        members = _fetch(hass).ds[STORE]
        assert members["*1"]["name"] == "LAN bridge"
        assert members["*1"]["uniq-id"] == "LAN bridge"
        assert members["*3"]["uniq-id"] == "LAN ether5"

    def test_the_comment_does_not_change_the_reference(self, hass):
        coord = _fetch(hass)
        before = coord.ds[STORE]["*3"]["uniq-id"]
        _fetch(hass, [MEMBERS[0], MEMBERS[1], {**MEMBERS[2], "comment": "renamed"}], coord)
        assert coord.ds[STORE]["*3"]["uniq-id"] == before
        assert coord.ds[STORE]["*3"]["comment"] == "renamed"

    def test_every_stored_member_carries_what_a_switch_needs(self, hass):
        for member in _fetch(hass).ds[STORE].values():
            missing = [name for name in (*DESCRIPTION.data_attributes_list, ".id", "name", "uniq-id", "legacy-uniq-id", "enabled") if name not in member]
            assert not missing, missing

    def test_a_numeric_comment_becomes_text(self, hass):
        coord = _fetch(hass, [{**MEMBERS[0], "comment": 42}])
        assert coord.ds[STORE]["*1"]["comment"] == "42"

    def test_the_same_interface_in_two_lists_is_two_members(self, hass):
        rows = [{".id": "*1", "list": "LAN", "interface": "ether5", "dynamic": False, "disabled": False}, {".id": "*2", "list": "trusted", "interface": "ether5", "dynamic": False, "disabled": False}]
        members = _fetch(hass, rows).ds[STORE]
        assert {member["uniq-id"] for member in members.values()} == {"LAN ether5", "trusted ether5"}

    def test_pairs_that_read_alike_as_an_entity_id_stay_apart(self, hass, caplog):
        """A slug drops what separates the list from the interface."""
        rows = [
            {".id": "*1", "list": "LAN guest", "interface": "wifi", "dynamic": False, "disabled": False},
            {".id": "*2", "list": "LAN", "interface": "guest wifi", "dynamic": False, "disabled": False},
            {".id": "*3", "list": "WAN", "interface": "ether1", "dynamic": False, "disabled": False},
        ]
        with caplog.at_level("INFO"):
            coord = _fetch(hass, rows)
            _fetch(hass, rows, coord)
        members = coord.ds[STORE]
        slugs = [slugify(member["uniq-id"].lower()) for member in members.values()]
        assert len(set(slugs)) == 3, slugs
        assert members["*3"]["uniq-id"] == "WAN ether1"
        assert members["*1"]["name"] == "LAN guest wifi"
        assert caplog.text.count("read alike as an entity id") == 1

    def test_the_order_on_the_router_does_not_change_the_references(self, hass):
        rows = [
            {".id": "*1", "list": "LAN guest", "interface": "wifi", "dynamic": False, "disabled": False},
            {".id": "*2", "list": "LAN", "interface": "guest wifi", "dynamic": False, "disabled": False},
        ]
        one = {uid: member["uniq-id"] for uid, member in _fetch(hass, rows).ds[STORE].items()}
        other = {uid: member["uniq-id"] for uid, member in _fetch(hass, rows[::-1]).ds[STORE].items()}
        assert one == other


# ---------------------------------------------------------------------------
# Members that come and go
# ---------------------------------------------------------------------------
class TestMembersThatComeAndGo:
    def test_a_failed_read_keeps_the_members(self, hass):
        coord = _fetch(hass)
        before = json.dumps(coord.ds[STORE], sort_keys=True)
        _fetch(hass, None, coord)
        assert json.dumps(coord.ds[STORE], sort_keys=True) == before

    def test_a_removed_member_leaves_after_three_reads(self, hass):
        coord = _fetch(hass)
        for _ in range(2):
            _fetch(hass, MEMBERS[:2], coord)
            assert "*3" in coord.ds[STORE]
        _fetch(hass, MEMBERS[:2], coord)
        assert "*3" not in coord.ds[STORE]

    def test_the_last_member_removed_leaves_too(self, hass):
        coord = _fetch(hass)
        for _ in range(3):
            _fetch(hass, [], coord)
        assert coord.ds[STORE] == {}

    def test_a_member_that_turns_dynamic_leaves_like_a_removed_one(self, hass):
        coord = _fetch(hass)
        for _ in range(3):
            _fetch(hass, [MEMBERS[0], {**MEMBERS[1], "dynamic": True}, MEMBERS[2]], coord)
        assert set(coord.ds[STORE]) == {"*1", "*3"}

    def test_a_readded_member_does_not_clash_with_its_dead_row(self, hass):
        """The dead row is still around for a few reads and holds the same pair."""
        coord = _fetch(hass)
        _fetch(hass, [MEMBERS[0], MEMBERS[1], {**MEMBERS[2], ".id": "*A"}], coord)
        assert coord.ds[STORE]["*A"]["uniq-id"] == "LAN ether5"
        assert coord.interface_list_clashes == set()


# ---------------------------------------------------------------------------
# The option
# ---------------------------------------------------------------------------
class TestOption:
    def test_option_is_off_by_default(self, hass):
        assert _make_coordinator(hass).option_sensor_interface_lists is False
        assert _make_coordinator(hass, {CONF_SENSOR_INTERFACE_LISTS: True}).option_sensor_interface_lists is True

    def test_only_the_full_preset_turns_it_on(self):
        assert {name: preset[CONF_SENSOR_INTERFACE_LISTS] for name, preset in _SENSOR_PRESETS.items()} == {
            "core": False,
            "minimal": False,
            "recommended": False,
            "full": True,
        }

    @pytest.mark.parametrize(("options", "fetched"), [({}, False), ({CONF_SENSOR_INTERFACE_LISTS: True}, True)])
    async def test_the_menu_is_read_only_with_the_option_on(self, hass, options, fetched):
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

        assert coordinator.get_interface_list_members.called is fetched

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
            assert fields[CONF_SENSOR_INTERFACE_LISTS]["default"] is False

            result = await hass.config_entries.flow.async_configure(result["flow_id"], {CONF_SENSOR_INTERFACE_LISTS: True})
        assert result["type"] == FlowResultType.CREATE_ENTRY
        assert result["options"][CONF_SENSOR_INTERFACE_LISTS] is True

    @pytest.mark.parametrize("stored", [True, False])
    async def test_the_options_form_shows_the_stored_value(self, hass, stored):
        entry = MockConfigEntry(domain=DOMAIN, data=ENTRY_DATA, options={CONF_SENSOR_INTERFACE_LISTS: stored}, unique_id="192.168.88.1")
        entry.add_to_hass(hass)
        result = await hass.config_entries.options.async_init(entry.entry_id)
        while result["type"] == FlowResultType.FORM and result["step_id"] != "sensor_mode":
            result = await hass.config_entries.options.async_configure(result["flow_id"], {})
        result = await hass.config_entries.options.async_configure(result["flow_id"], {"sensor_preset": "custom"})
        fields = {f["name"]: f for f in voluptuous_serialize.convert(result["data_schema"], custom_serializer=cv.custom_serializer)}
        assert fields[CONF_SENSOR_INTERFACE_LISTS]["default"] is stored

        result = await hass.config_entries.options.async_configure(result["flow_id"], {CONF_SENSOR_INTERFACE_LISTS: not stored})
        assert result["type"] == FlowResultType.CREATE_ENTRY
        assert result["data"][CONF_SENSOR_INTERFACE_LISTS] is (not stored)

    def test_every_language_names_and_describes_the_option(self):
        base = os.path.join(os.path.dirname(__file__), "..", "custom_components", "mikrotik_extended")
        files = [os.path.join(base, "strings.json"), *glob.glob(os.path.join(base, "translations", "*.json"))]
        assert len(files) == 27
        english = None
        for path in files:
            with open(path, encoding="utf-8") as handle:
                text = json.load(handle)
            for flow in ("config", "options"):
                step = text[flow]["step"]["sensor_select"]
                label = step["data"][CONF_SENSOR_INTERFACE_LISTS]
                described = step["data_description"][CONF_SENSOR_INTERFACE_LISTS]
                assert label and described and label != described, path
                assert "—" not in label + described, path
                assert set(step["data"]) == set(step["data_description"]), path
            if path.endswith("strings.json"):
                english = (label, described)
        assert english == ("Interface list member switches", "Switch per interface list member to enable/disable the member from HA. Warning: creates one entity per interface list member.")


# ---------------------------------------------------------------------------
# The switches
# ---------------------------------------------------------------------------
class TestSwitches:
    def test_the_description_writes_to_the_member_menu(self):
        assert DESCRIPTION.data_path == STORE
        assert DESCRIPTION.data_switch_path == PATH
        assert DESCRIPTION.func == "MikrotikFilterSwitch"
        assert DESCRIPTION.enable_on_option == CONF_SENSOR_INTERFACE_LISTS
        assert DESCRIPTION.data_reference == "uniq-id"
        assert STORE in REBIND_DATA_PATHS

    def test_the_switch_is_named_after_list_and_interface(self, hass):
        assert _switch(hass, "*1").name == "LAN bridge"
        assert _switch(hass, "*3").name == "LAN ether5"

    def test_the_unique_id_holds_list_and_interface(self, hass):
        assert _switch(hass, "*3").unique_id == "entry1-interface_list_member-lan_ether5"

    def test_a_disabled_member_reads_off(self, hass):
        assert _switch(hass, "*1").is_on is True
        assert _switch(hass, "*3").is_on is False

    def test_attributes_show_the_member(self, hass):
        attributes = _switch(hass, "*3").extra_state_attributes
        assert attributes["list"] == "LAN"
        assert attributes["interface"] == "ether5"
        assert attributes["comment"] == "guest port"

    def test_all_members_share_one_device(self, hass):
        connections = {next(iter(_switch(hass, uid).device_info["connections"])) for uid in ("*1", "*2", "*3")}
        assert connections == {(DOMAIN, "entry1-Interface Lists")}
        others = {f"entry1-{d.ha_connection_value}" for d in SENSOR_TYPES if d.key != STORE}
        assert "entry1-Interface Lists" not in others

    async def test_a_switch_toggles_its_own_member(self, hass):
        switch = _switch(hass, "*3")
        await switch.async_turn_on()
        assert switch.coordinator.set_value.call_args.args == (PATH, ".id", "*3", "disabled", False)
        await switch.async_turn_off()
        assert switch.coordinator.set_value.call_args.args == (PATH, ".id", "*3", "disabled", True)
        assert switch.coordinator.async_refresh.await_count == 2

    async def test_a_switch_follows_its_readded_member(self, hass):
        coord = _fetch(hass)
        data = {STORE: coord.ds[STORE], "access": {"write"}}
        switch = _switch(hass, "*3", data)
        switch.async_write_ha_state = MagicMock()

        _fetch(hass, [MEMBERS[0], MEMBERS[1], {**MEMBERS[2], ".id": "*A", "disabled": False}], coord)
        switch.coordinator._get_stale_counters = coord._get_stale_counters
        switch._handle_coordinator_update()

        assert switch.is_on is True
        assert switch.unique_id == "entry1-interface_list_member-lan_ether5"
        await switch.async_turn_off()
        assert switch.coordinator.set_value.call_args.args == (PATH, ".id", "*A", "disabled", True)

    async def test_a_switch_does_not_follow_the_same_interface_in_another_list(self, hass):
        coord = _fetch(hass)
        data = {STORE: coord.ds[STORE], "access": {"write"}}
        switch = _switch(hass, "*3", data)
        switch.async_write_ha_state = MagicMock()

        other = {".id": "*B", "list": "trusted", "interface": "ether5", "dynamic": False, "disabled": False}
        _fetch(hass, [MEMBERS[0], MEMBERS[1], other], coord)
        switch.coordinator._get_stale_counters = coord._get_stale_counters
        switch._handle_coordinator_update()

        await switch.async_turn_off()
        assert switch.coordinator.set_value.call_args.args[2] == "*3"

    async def test_without_write_access_nothing_is_sent(self, hass):
        coord = _fetch(hass)
        switch = _switch(hass, "*3", {STORE: coord.ds[STORE], "access": set()})
        with pytest.raises(Exception):  # noqa: B017 - the translated error type is asserted elsewhere
            await switch.async_turn_on()
        switch.coordinator.set_value.assert_not_called()

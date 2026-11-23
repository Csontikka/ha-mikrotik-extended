"""Interface list members as switches.

The sample rows are what RouterOS 7.25.1 answers on `/interface/list/member`:
every row carries `disabled` and `dynamic`, also when both are false, and the
router refuses a second member with the same list and interface.

A member is stored under a key made of its list, its interface and a short
hash of the exact pair; `K` below builds it the way the integration does.
"""

import glob
import hashlib
import json
import os
import re
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import voluptuous_serialize
from homeassistant import config_entries
from homeassistant.const import CONF_HOST, CONF_NAME, CONF_PASSWORD, CONF_PORT, CONF_SSL, CONF_USERNAME, CONF_VERIFY_SSL
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import config_validation as cv
from homeassistant.util import slugify
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.mikrotik_extended.config_flow import _SENSOR_PRESETS
from custom_components.mikrotik_extended.const import CONF_SENSOR_INTERFACE_LISTS, DOMAIN
from custom_components.mikrotik_extended.coordinator import MikrotikCoordinator
from custom_components.mikrotik_extended.encoding_repair import RAW_SUFFIX
from custom_components.mikrotik_extended.entity import REBIND_DATA_PATHS
from custom_components.mikrotik_extended.switch import MikrotikInterfaceListMemberSwitch
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


def K(list_name, interface):
    """The store key of a member."""
    pair = hashlib.sha1(f"{list_name}\x00{interface}".encode("utf-8", "surrogateescape"), usedforsecurity=False).hexdigest()[:8]
    return f"{list_name} {interface} {pair}"


BRIDGE, ETHER1, ETHER5 = K("LAN", "bridge"), K("WAN", "ether1"), K("LAN", "ether5")


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
    switch = MikrotikInterfaceListMemberSwitch(coordinator, DESCRIPTION, uid=uid)
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
        assert set(_fetch(hass).ds[STORE]) == {BRIDGE, ETHER1, ETHER5}

    def test_a_member_is_enabled_unless_the_router_says_disabled(self, hass):
        members = _fetch(hass).ds[STORE]
        assert [members[key]["enabled"] for key in (BRIDGE, ETHER1, ETHER5)] == [True, True, False]

    def test_a_member_without_a_disabled_flag_is_enabled(self, hass):
        """RouterOS leaves the flag out of some menus for an enabled row."""
        coord = _fetch(hass, [{".id": "*1", "list": "LAN", "interface": "bridge", "dynamic": False}])
        assert coord.ds[STORE][BRIDGE]["enabled"] is True

    def test_a_member_is_stored_under_its_list_and_interface(self, hass):
        members = _fetch(hass).ds[STORE]
        assert members[BRIDGE][".id"] == "*1"
        assert members[BRIDGE]["name"] == "LAN bridge"
        assert members[BRIDGE]["uniq-id"] == BRIDGE
        assert BRIDGE.startswith("LAN bridge ") and len(BRIDGE) == len("LAN bridge ") + 8

    def test_the_comment_does_not_change_the_reference(self, hass):
        coord = _fetch(hass)
        _fetch(hass, [MEMBERS[0], MEMBERS[1], {**MEMBERS[2], "comment": "renamed"}], coord)
        assert set(coord.ds[STORE]) == {BRIDGE, ETHER1, ETHER5}
        assert coord.ds[STORE][ETHER5]["comment"] == "renamed"

    def test_every_stored_member_carries_what_a_switch_needs(self, hass):
        for member in _fetch(hass).ds[STORE].values():
            missing = [name for name in (*DESCRIPTION.data_attributes_list, ".id", "name", "uniq-id", "enabled") if name not in member]
            assert not missing, missing

    def test_a_numeric_comment_becomes_text(self, hass):
        coord = _fetch(hass, [{**MEMBERS[0], "comment": 42}])
        assert coord.ds[STORE][BRIDGE]["comment"] == "42"

    def test_names_that_look_like_numbers_or_flags_are_text(self, hass):
        """The API library hands such a value over as a number or a flag."""
        coord = _fetch(hass, [{".id": "*1", "list": 10, "interface": True, "dynamic": False, "disabled": False}])
        member = coord.ds[STORE][K("10", "True")]
        assert (member["list"], member["interface"], member["name"]) == ("10", "True", "10 True")

    @pytest.mark.parametrize("row", [{".id": "*1", "interface": "ether1"}, {".id": "*1", "list": "LAN"}, {".id": "*1", "list": "", "interface": "ether1"}])
    def test_a_row_without_a_list_or_an_interface_is_left_out(self, hass, row):
        assert _fetch(hass, [{**row, "dynamic": False, "disabled": False}]).ds[STORE] == {}

    def test_the_same_interface_in_two_lists_is_two_members(self, hass):
        rows = [{".id": "*1", "list": "LAN", "interface": "ether5", "dynamic": False, "disabled": False}, {".id": "*2", "list": "trusted", "interface": "ether5", "dynamic": False, "disabled": False}]
        assert set(_fetch(hass, rows).ds[STORE]) == {K("LAN", "ether5"), K("trusted", "ether5")}

    @pytest.mark.parametrize(
        "pairs",
        [
            [("LAN guest", "wifi"), ("LAN", "guest wifi")],
            [("LAN", "ether1.10"), ("LAN", "ether1-10")],
            [("WAN", "ether1"), ("wan", "ether1")],
            [("A B", "C"), ("A", "B C"), ("A-B", "C")],
            [("LAN", "guest wifi"), ("LAN", "guest wifi in list LAN")],
        ],
    )
    def test_pairs_that_read_alike_as_an_entity_id_stay_apart(self, hass, pairs):
        """A slug drops case and whatever separates the list from the interface."""
        rows = [{".id": f"*{n}", "list": list_name, "interface": interface, "dynamic": False, "disabled": False} for n, (list_name, interface) in enumerate(pairs, 1)]
        members = _fetch(hass, rows).ds[STORE]
        assert len(members) == len(pairs)
        slugs = {slugify(member["uniq-id"].lower()) for member in members.values()}
        assert len(slugs) == len(pairs), slugs

    def test_the_reference_does_not_depend_on_the_other_members(self, hass):
        """Not on a member that reads alike coming or going, and not on the order."""
        alone = {".id": "*2", "list": "LAN", "interface": "guest wifi", "dynamic": False, "disabled": False}
        twin = {".id": "*1", "list": "LAN guest", "interface": "wifi", "dynamic": False, "disabled": False}
        coord = _fetch(hass, [alone])
        before = dict(coord.ds[STORE][K("LAN", "guest wifi")])
        for rows in ([twin, alone], [alone, twin], [alone]):
            _fetch(hass, rows, coord)
            assert coord.ds[STORE][K("LAN", "guest wifi")] == before

    def test_the_text_encoding_option_does_not_change_the_reference(self, hass):
        """The id of an entity must survive a change of the option."""
        raw = "Vendég".encode().decode("latin-1")
        row = {".id": "*1", "list": raw, "interface": "ether5", "dynamic": False, "disabled": False}
        keys = {encoding: next(iter(_fetch(hass, [row], _make_coordinator(hass, {"text_encoding": encoding})).ds[STORE])) for encoding in ("ISO-8859-1", "Windows-1251", "UTF-8")}
        assert set(keys.values()) == {K(raw, "ether5")}

    @pytest.mark.parametrize("encoding", ["ISO-8859-1", "Windows-1251", "KOI8-R", "UTF-8"])
    def test_a_list_name_survives_the_second_decoding_of_the_cycle(self, hass, encoding):
        """The reader decodes the names, and the end of every cycle decodes all text fields again."""
        raw = "Vendég".encode().decode("latin-1")
        coord = _make_coordinator(hass, {"text_encoding": encoding})
        for _ in range(2):
            _fetch(hass, [{".id": "*1", "list": raw, "interface": "ether5", "dynamic": False, "disabled": False, "comment": raw}], coord)
            coord._decode_text_fields()
            member = coord.ds[STORE][K(raw, "ether5")]
            assert (member["list"], member["comment"], member["name"]) == ("Vendég", "Vendég", "Vendég ether5")
            assert member[f"list{RAW_SUFFIX}"] == raw


# ---------------------------------------------------------------------------
# Members that come and go
# ---------------------------------------------------------------------------
class TestMembersThatComeAndGo:
    def test_a_failed_read_keeps_the_members(self, hass):
        coord = _fetch(hass)
        before = json.dumps(coord.ds[STORE], sort_keys=True)
        for _ in range(4):
            _fetch(hass, None, coord)
        assert json.dumps(coord.ds[STORE], sort_keys=True) == before
        # Nor does it count towards a member being gone.
        assert coord._get_stale_counters(STORE) == {}

    def test_a_removed_member_leaves_after_three_reads(self, hass):
        coord = _fetch(hass)
        for _ in range(2):
            _fetch(hass, MEMBERS[:2], coord)
            assert ETHER5 in coord.ds[STORE]
        _fetch(hass, MEMBERS[:2], coord)
        assert ETHER5 not in coord.ds[STORE]

    def test_the_last_member_removed_leaves_too(self, hass):
        coord = _fetch(hass)
        for _ in range(3):
            _fetch(hass, [], coord)
        assert coord.ds[STORE] == {}

    def test_a_member_that_turns_dynamic_leaves_like_a_removed_one(self, hass):
        coord = _fetch(hass)
        for _ in range(3):
            _fetch(hass, [MEMBERS[0], {**MEMBERS[1], "dynamic": True}, MEMBERS[2]], coord)
        assert set(coord.ds[STORE]) == {BRIDGE, ETHER5}

    def test_a_readded_member_is_the_same_entry_with_a_new_id(self, hass):
        coord = _fetch(hass)
        _fetch(hass, MEMBERS[:2], coord)
        _fetch(hass, [MEMBERS[0], MEMBERS[1], {**MEMBERS[2], ".id": "*A"}], coord)
        assert set(coord.ds[STORE]) == {BRIDGE, ETHER1, ETHER5}
        assert coord.ds[STORE][ETHER5][".id"] == "*A"
        assert coord._get_stale_counters(STORE) == {}

    def test_a_member_moved_to_another_list_is_gone_from_the_old_one_at_once(self, hass):
        """It keeps its RouterOS id, which must not stay on the entry of the old pair."""
        coord = _fetch(hass)
        _fetch(hass, [MEMBERS[0], MEMBERS[1], {**MEMBERS[2], "list": "WAN"}], coord)
        assert set(coord.ds[STORE]) == {BRIDGE, ETHER1, K("WAN", "ether5")}
        assert coord.ds[STORE][K("WAN", "ether5")][".id"] == "*3"
        assert coord._get_stale_counters(STORE) == {}

    def test_a_member_moved_and_another_put_in_its_place(self, hass):
        coord = _fetch(hass)
        _fetch(hass, [MEMBERS[0], MEMBERS[1], {**MEMBERS[2], "list": "WAN"}, {".id": "*9", "list": "LAN", "interface": "ether5", "dynamic": False, "disabled": False}], coord)
        assert coord.ds[STORE][ETHER5][".id"] == "*9"
        assert coord.ds[STORE][K("WAN", "ether5")][".id"] == "*3"


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
        assert DESCRIPTION.func == "MikrotikInterfaceListMemberSwitch"
        assert DESCRIPTION.enable_on_option == CONF_SENSOR_INTERFACE_LISTS
        assert DESCRIPTION.data_reference == "uniq-id"
        # Keyed by the pair itself, so there is nothing to follow to a new key.
        assert STORE not in REBIND_DATA_PATHS

    def test_the_switch_is_named_after_list_and_interface(self, hass):
        assert _switch(hass, BRIDGE).name == "LAN bridge"
        assert _switch(hass, ETHER5).name == "LAN ether5"

    def test_the_unique_id_holds_list_interface_and_the_hash_of_the_pair(self, hass):
        assert _switch(hass, ETHER5).unique_id == f"entry1-interface_list_member-{slugify(ETHER5.lower())}"
        assert re.fullmatch(r"entry1-interface_list_member-lan_ether5_[0-9a-f]{8}", _switch(hass, ETHER5).unique_id)

    def test_a_disabled_member_reads_off(self, hass):
        assert _switch(hass, BRIDGE).is_on is True
        assert _switch(hass, ETHER5).is_on is False

    def test_attributes_show_the_member(self, hass):
        attributes = _switch(hass, ETHER5).extra_state_attributes
        assert attributes["list"] == "LAN"
        assert attributes["interface"] == "ether5"
        assert attributes["comment"] == "guest port"

    def test_all_members_share_one_device(self, hass):
        connections = {next(iter(_switch(hass, key).device_info["connections"])) for key in (BRIDGE, ETHER1, ETHER5)}
        assert connections == {(DOMAIN, "entry1-Interface Lists")}
        others = {f"entry1-{d.ha_connection_value}" for d in SENSOR_TYPES if d.key != STORE}
        assert "entry1-Interface Lists" not in others

    async def test_a_switch_toggles_its_own_member(self, hass):
        switch = _switch(hass, ETHER5)
        await switch.async_turn_on()
        assert switch.coordinator.set_value.call_args.args == (PATH, ".id", "*3", "disabled", False)
        await switch.async_turn_off()
        assert switch.coordinator.set_value.call_args.args == (PATH, ".id", "*3", "disabled", True)
        assert switch.coordinator.async_refresh.await_count == 2

    def _live_switch(self, hass):
        coord = _fetch(hass)
        switch = _switch(hass, ETHER5, {STORE: coord.ds[STORE], "access": {"write"}})
        switch.async_write_ha_state = MagicMock()
        switch.coordinator._get_stale_counters = coord._get_stale_counters
        switch.coordinator.last_update_success = True

        def read(rows):
            _fetch(hass, rows, coord)
            switch.coordinator.data[STORE] = coord.ds[STORE]
            switch._handle_coordinator_update()

        return switch, read

    async def test_a_switch_follows_its_readded_member(self, hass):
        switch, read = self._live_switch(hass)
        read([MEMBERS[0], MEMBERS[1], {**MEMBERS[2], ".id": "*A", "disabled": False}])

        assert switch.available is True
        assert switch.is_on is True
        await switch.async_turn_off()
        assert switch.coordinator.set_value.call_args.args == (PATH, ".id", "*A", "disabled", True)

    async def test_a_switch_whose_member_is_gone_is_unavailable_and_writes_nothing(self, hass):
        """From the first read without it, and still after the entry has left the store."""
        switch, read = self._live_switch(hass)
        assert switch.available is True
        for reads in range(1, 5):
            read(MEMBERS[:2])
            assert switch.available is False
            assert switch.async_write_ha_state.call_count == reads
            with pytest.raises(HomeAssistantError) as error:
                await switch.async_turn_on()
            assert error.value.translation_key == "write_failed"
        switch.coordinator.set_value.assert_not_called()

    async def test_a_switch_does_not_write_to_a_member_moved_to_another_list(self, hass):
        """The moved member keeps the RouterOS id this switch last saw."""
        switch, read = self._live_switch(hass)
        read([MEMBERS[0], MEMBERS[1], {**MEMBERS[2], "list": "WAN"}])

        assert switch.available is False
        # The state is written, or the entity would go on showing its last on or off.
        assert switch.async_write_ha_state.call_count == 1
        with pytest.raises(Exception):  # noqa: B017 - the translated error type is asserted elsewhere
            await switch.async_turn_off()
        switch.coordinator.set_value.assert_not_called()
        assert _switch(hass, K("WAN", "ether5"), switch.coordinator.data).unique_id != switch.unique_id

    async def test_a_switch_comes_back_when_its_member_does(self, hass):
        switch, read = self._live_switch(hass)
        for _ in range(4):
            read(MEMBERS[:2])
        read([MEMBERS[0], MEMBERS[1], {**MEMBERS[2], ".id": "*B"}])
        assert switch.available is True
        await switch.async_turn_on()
        assert switch.coordinator.set_value.call_args.args == (PATH, ".id", "*B", "disabled", False)

    def test_a_switch_is_unavailable_when_the_router_cannot_be_read(self, hass):
        switch, _ = self._live_switch(hass)
        switch.coordinator.last_update_success = False
        assert switch.available is False

    async def test_without_write_access_nothing_is_sent(self, hass):
        coord = _fetch(hass)
        switch = _switch(hass, ETHER5, {STORE: coord.ds[STORE], "access": set()})
        with pytest.raises(Exception):  # noqa: B017 - the translated error type is asserted elsewhere
            await switch.async_turn_on()
        switch.coordinator.set_value.assert_not_called()

"""Identical rules, which only a number keeps apart.

Nothing on the router tells two identical rules without a comment of their
own apart. The number a rule gets is therefore arbitrary, but it must not
move: a rule keeps it while it lives, whatever happens to the others, so a
switch never ends up on another rule while Home Assistant runs. A repair
issue tells the user that distinct comments are the real fix.
"""

import glob
import json
import os
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant.const import EVENT_HOMEASSISTANT_FINAL_WRITE
from homeassistant.helpers import entity_platform
from homeassistant.helpers import issue_registry as ir

from custom_components.mikrotik_extended.const import DOMAIN
from custom_components.mikrotik_extended.coordinator import MikrotikCoordinator, _disambiguate_uniq_ids

from .lifecycle_harness import ENTRY_ID, restart, start

COMPONENT = os.path.join(os.path.dirname(__file__), "..", "custom_components", "mikrotik_extended")
MENUS = {"filter": "/ip/firewall/filter", "ipv6_filter": "/ipv6/firewall/filter"}
RULE = {"chain": "forward", "action": "drop", "protocol": "tcp", "dst-port": "23", "disabled": False}
ISSUE = f"duplicate_rules_{ENTRY_ID}"
# The reference of RULE in the IPv4 filter store.
SAME = "forward,drop,tcp,any,any,any:any,any:any-any,any:any,any:23"

both_stores = pytest.mark.parametrize("store", sorted(MENUS))


def _rules(*ids, **extra):
    return [{".id": rule_id, **RULE, **extra} for rule_id in ids]


def _store(*ids, reference="same", comment=""):
    return {uid: {"uniq-id": reference, "comment": comment} for uid in ids}


def _read(store, numbers, stale=(), reference="same"):
    """One read: the reference of every live row starts out plain again, then the numbers are given."""
    for uid, vals in store.items():
        if uid not in stale:
            vals["uniq-id"] = vals.get("own", reference)
    return _disambiguate_uniq_ids(store, stale, numbers)


def _suffixes(store):
    return {uid: vals["uniq-id"] for uid, vals in store.items()}


class TestNumbers:
    def test_the_first_read_numbers_in_store_order(self):
        store, numbers = _store("*1", "*2", "*3"), {}
        assert _read(store, numbers) == ["same"]
        assert _suffixes(store) == {"*1": "same #1", "*2": "same #2", "*3": "same #3"}
        assert numbers == {"same": {"*1": 1, "*2": 2, "*3": 3}}

    def test_without_a_memory_the_rows_are_numbered_in_store_order(self):
        store = _store("*1", "*2")
        assert _disambiguate_uniq_ids(store) == ["same"]
        assert _suffixes(store) == {"*1": "same #1", "*2": "same #2"}

    def test_the_numbers_do_not_follow_the_order_of_the_rows(self):
        store, numbers = _store("*1", "*2", "*3"), {}
        _read(store, numbers)

        store = {uid: store[uid] for uid in ("*3", "*1", "*2")}
        _read(store, numbers)

        assert _suffixes(store) == {"*3": "same #3", "*1": "same #1", "*2": "same #2"}

    def test_a_rule_that_leaves_does_not_renumber_the_others(self):
        store, numbers = _store("*1", "*2", "*3"), {}
        _read(store, numbers)

        del store["*1"]
        _read(store, numbers)

        assert _suffixes(store) == {"*2": "same #2", "*3": "same #3"}
        assert numbers == {"same": {"*2": 2, "*3": 3}}

    def test_a_new_rule_gets_the_lowest_free_number(self):
        store, numbers = _store("*1", "*2", "*3"), {}
        _read(store, numbers)
        del store["*2"]
        _read(store, numbers)

        store["*9"] = {"uniq-id": "same", "comment": ""}
        _read(store, numbers)
        store["*A"] = {"uniq-id": "same", "comment": ""}
        _read(store, numbers)

        assert _suffixes(store) == {"*1": "same #1", "*3": "same #3", "*9": "same #2", "*A": "same #4"}

    def test_the_last_one_left_keeps_its_number(self):
        store, numbers = _store("*1", "*2"), {}
        _read(store, numbers)
        del store["*1"]

        assert _read(store, numbers) == []
        assert _suffixes(store) == {"*2": "same #2"}
        assert numbers == {"same": {"*2": 2}}

    def test_the_last_one_left_keeps_its_number_while_the_other_is_on_the_grace(self):
        """The entity of the missing rule must not find its reference on the one that stayed."""
        store, numbers = _store("*1", "*2"), {}
        _read(store, numbers)

        _read(store, numbers, stale={"*1"})

        assert _suffixes(store) == {"*1": "same #1", "*2": "same #2"}

    def test_a_new_rule_that_stands_alone_takes_the_number_of_the_row_on_the_grace(self):
        """So the entity of the missing rule finds it, as it does for a re-created rule in a larger group."""
        store, numbers = _store("*1", "*2", "*3"), {}
        _read(store, numbers)
        del store["*2"], store["*3"]
        store["*9"] = {"uniq-id": "same", "comment": ""}

        _read(store, numbers, stale={"*1"})

        assert store["*9"]["uniq-id"] == "same #1"

    def test_a_rule_given_a_comment_of_its_own_gives_its_number_back(self):
        store, numbers = _store("*1", "*2", "*3"), {}
        _read(store, numbers)

        store["*2"]["own"] = "mine"
        _read(store, numbers)
        assert _suffixes(store) == {"*1": "same #1", "*2": "mine", "*3": "same #3"}
        assert numbers == {"same": {"*1": 1, "*3": 3}}

        store["*9"] = {"uniq-id": "same", "comment": ""}
        _read(store, numbers)
        assert store["*9"]["uniq-id"] == "same #2"

    def test_a_row_on_the_grace_is_left_as_it_is(self):
        store, numbers = _store("*1", "*2", "*3"), {}
        _read(store, numbers)

        _read(store, numbers, stale={"*1"})

        assert _suffixes(store) == {"*1": "same #1", "*2": "same #2", "*3": "same #3"}

    def test_a_row_back_from_the_grace_has_its_number_back(self):
        store, numbers = _store("*1", "*2"), {}
        _read(store, numbers)
        _read(store, numbers, stale={"*1"})

        _read(store, numbers)

        assert _suffixes(store) == {"*1": "same #1", "*2": "same #2"}

    def test_a_recreated_rule_takes_the_number_of_the_row_on_the_grace(self):
        """That is the reference the entity of the old row looks for."""
        store, numbers = _store("*1", "*2"), {}
        _read(store, numbers)

        store["*9"] = {"uniq-id": "same", "comment": ""}
        _read(store, numbers, stale={"*1"})

        assert _suffixes(store) == {"*1": "same #1", "*2": "same #2", "*9": "same #1"}
        assert numbers == {"same": {"*2": 2, "*9": 1}}

    def test_a_row_that_returns_after_its_number_was_taken_gets_a_free_one(self):
        store, numbers = _store("*1", "*2"), {}
        _read(store, numbers)
        store["*9"] = {"uniq-id": "same", "comment": ""}
        _read(store, numbers, stale={"*1"})

        _read(store, numbers)

        assert _suffixes(store) == {"*1": "same #3", "*2": "same #2", "*9": "same #1"}

    def test_one_new_rule_for_two_on_the_grace_takes_the_first_number(self):
        """One of the two was re-created; which one cannot be known."""
        store, numbers = _store("*1", "*2"), {}
        _read(store, numbers)

        store["*9"] = {"uniq-id": "same", "comment": ""}
        assert _read(store, numbers, stale={"*1", "*2"}) == []
        assert store["*9"]["uniq-id"] == "same #1"

        del store["*1"], store["*2"]
        _read(store, numbers)
        assert store["*9"]["uniq-id"] == "same #1"

    def test_groups_are_numbered_apart(self):
        store = {**_store("*1", "*2"), **_store("*3", "*4", reference="other")}
        numbers = {}
        for uid in ("*3", "*4"):
            store[uid]["own"] = "other"
        assert sorted(_read(store, numbers)) == ["other", "same"]
        assert _suffixes(store) == {"*1": "same #1", "*2": "same #2", "*3": "other #1", "*4": "other #2"}


class TestNumbersBroughtBack:
    """What a start finds saved is given to the numbering as its memory."""

    def test_rows_that_are_there_have_their_numbers_again(self):
        store, numbers = _store("*2", "*3"), {"same": {"*1": 1, "*2": 2, "*3": 3}}
        _read(store, numbers)
        assert _suffixes(store) == {"*2": "same #2", "*3": "same #3"}
        assert numbers == {"same": {"*2": 2, "*3": 3}}

    def test_a_single_row_left_has_its_number_again(self):
        store, numbers = _store("*2"), {"same": {"*1": 1, "*2": 2}}
        _read(store, numbers)
        assert _suffixes(store) == {"*2": "same #2"}

    def test_a_number_saved_for_another_reference_is_dropped(self):
        """The row is there, but it is not the rule the number was given to."""
        store, numbers = _store("*1", "*2"), {"other": {"*1": 5, "*2": 6}}
        _read(store, numbers)
        assert _suffixes(store) == {"*1": "same #1", "*2": "same #2"}
        assert numbers == {"same": {"*1": 1, "*2": 2}}

    def test_a_row_that_is_gone_is_dropped_and_its_number_is_free(self):
        store, numbers = _store("*2", "*9"), {"same": {"*1": 1, "*2": 2}}
        _read(store, numbers)
        assert _suffixes(store) == {"*2": "same #2", "*9": "same #1"}

    def test_a_row_alone_with_nothing_saved_for_it_gets_no_number(self):
        store, numbers = _store("*7"), {"same": {"*1": 1, "*2": 2}}
        _read(store, numbers)
        assert _suffixes(store) == {"*7": "same"}
        assert numbers == {}


def _switches(hass, store):
    """Entity id by the RouterOS id of the rule its switch is bound to."""
    return {entity._uid: entity.entity_id for platform in entity_platform.async_get_platforms(hass, DOMAIN) for entity in platform.entities.values() if entity.entity_description.key == store}


def _switch(hass, entity_id):
    for platform in entity_platform.async_get_platforms(hass, DOMAIN):
        if entity_id in platform.entities:
            return platform.entities[entity_id]
    return None


class TestSwitchesStayWithTheirRule:
    @both_stores
    async def test_removing_one_rule_does_not_move_the_others(self, hass, store):
        path = MENUS[store]
        router = await start(hass, {path: _rules("*1", "*2", "*3")})
        before = _switches(hass, store)
        assert sorted(before) == ["*1", "*2", "*3"]
        registered = router.entities()

        router.menus[path] = _rules("*2", "*3")
        await router.poll()

        # The switch of the removed rule is not handed the next rule.
        assert router.state(before["*1"]) == "unavailable"
        assert _switch(hass, before["*1"])._uid == "*1"
        assert _switches(hass, store) == before

        await router.poll(3)
        after = _switches(hass, store)
        assert after == {"*2": before["*2"], "*3": before["*3"]}
        assert router.entities() == {unique_id: entity_id for unique_id, entity_id in registered.items() if entity_id != before["*1"]}
        for rule_id in ("*2", "*3"):
            await _switch(hass, after[rule_id]).async_turn_off()
        assert router.writes() == [(path, ".id", "*2", "disabled", True), (path, ".id", "*3", "disabled", True)]

    @both_stores
    async def test_the_order_on_the_router_changes_nothing(self, hass, store):
        path = MENUS[store]
        router = await start(hass, {path: _rules("*1", "*2", "*3")})
        before, registered = _switches(hass, store), router.entities()

        router.menus[path] = _rules("*3", "*1", "*2")
        await router.poll(2)

        assert _switches(hass, store) == before
        assert router.entities() == registered

    @both_stores
    async def test_a_recreated_rule_keeps_its_switch(self, hass, store):
        path = MENUS[store]
        router = await start(hass, {path: _rules("*1", "*2", "*3")})
        before, registered = _switches(hass, store), router.entities()

        router.menus[path] = _rules("*1", "*3", "*9")
        await router.poll(4)

        assert _switches(hass, store) == {"*1": before["*1"], "*3": before["*3"], "*9": before["*2"]}
        assert router.entities() == registered
        assert router.state(before["*2"]) == "on"

    @both_stores
    async def test_a_rule_added_later_takes_the_free_number(self, hass, store):
        path = MENUS[store]
        router = await start(hass, {path: _rules("*1", "*2", "*3")})
        before, registered = _switches(hass, store), router.entities()
        router.menus[path] = _rules("*1", "*3")
        await router.poll(3)

        router.menus[path] = _rules("*1", "*3", "*7")
        await router.poll()

        after = _switches(hass, store)
        assert {rule_id: after[rule_id] for rule_id in ("*1", "*3")} == {rule_id: before[rule_id] for rule_id in ("*1", "*3")}
        # The new rule is registered under the unique id the removed one had.
        assert set(router.entities()) == set(registered)

    @both_stores
    async def test_a_second_identical_rule_does_not_cost_the_first_its_entity(self, hass, store):
        """The first rule gains a number, and its entity goes along."""
        path = MENUS[store]
        router = await start(hass, {path: _rules("*1")})
        (first,) = _switches(hass, store).values()

        router.menus[path] = _rules("*1", "*2")
        await router.poll(2)

        after = _switches(hass, store)
        assert after["*1"] == first
        assert len(after) == 2
        assert len(router.entities()) == 2

    @both_stores
    async def test_rules_that_share_a_comment_are_numbered_too(self, hass, store):
        path = MENUS[store]
        router = await start(hass, {path: _rules("*1", "*2", comment="block")})
        assert router.entities() == {f"{store}-block_1": _switches(hass, store)["*1"], f"{store}-block_2": _switches(hass, store)["*2"]}


class TestAcrossARestart:
    @both_stores
    async def test_the_rules_left_after_a_removal_keep_their_switches(self, hass, store):
        """Numbered 2 and 3 before, they must not come back as 1 and 2: 2 would then be the other rule."""
        path = MENUS[store]
        router = await start(hass, {path: _rules("*1", "*2", "*3")})
        before = _switches(hass, store)
        router.menus[path] = _rules("*2", "*3")
        await router.poll(3)
        ids = router.registry_ids()
        assert len(ids) == 2

        router = await restart(router)

        assert _switches(hass, store) == {"*2": before["*2"], "*3": before["*3"]}
        assert router.registry_ids() == ids
        await _switch(hass, before["*3"]).async_turn_off()
        assert router.writes() == [(path, ".id", "*3", "disabled", True)]

    @both_stores
    async def test_a_restart_while_the_removed_rule_is_still_on_its_grace(self, hass, store):
        """The registry still holds all three entities then; only the saved numbers tell who is who."""
        path = MENUS[store]
        router = await start(hass, {path: _rules("*1", "*2", "*3")})
        before = _switches(hass, store)
        ids = router.registry_ids()
        router.menus[path] = _rules("*2", "*3")
        await router.poll()

        router = await restart(router)

        assert _switches(hass, store) == {"*2": before["*2"], "*3": before["*3"]}
        assert {entity_id: router.registry_ids()[entity_id] for entity_id in (before["*2"], before["*3"])} == {entity_id: ids[entity_id] for entity_id in (before["*2"], before["*3"])}
        assert router.registry.async_get(before["*1"]) is None
        for rule_id in ("*2", "*3"):
            await _switch(hass, before[rule_id]).async_turn_off()
        assert router.writes() == [(path, ".id", "*2", "disabled", True), (path, ".id", "*3", "disabled", True)]

    @both_stores
    async def test_a_rule_removed_while_home_assistant_was_down(self, hass, store):
        path = MENUS[store]
        router = await start(hass, {path: _rules("*1", "*2", "*3")})
        before = _switches(hass, store)
        router.menus[path] = _rules("*2", "*3")

        router = await restart(router)

        assert _switches(hass, store) == {"*2": before["*2"], "*3": before["*3"]}

    @both_stores
    async def test_rules_listed_in_another_order_after_the_restart(self, hass, store):
        path = MENUS[store]
        router = await start(hass, {path: _rules("*1", "*2", "*3")})
        before, ids = _switches(hass, store), router.registry_ids()
        router.menus[path] = _rules("*3", "*1", "*2")

        router = await restart(router)

        assert _switches(hass, store) == before
        assert router.registry_ids() == ids

    @both_stores
    async def test_nothing_changes_when_nothing_changed(self, hass, store):
        path = MENUS[store]
        router = await start(hass, {path: _rules("*1", "*2", "*3")})
        before, registered = _switches(hass, store), router.entities()

        ids = router.registry_ids()
        router = await restart(router)
        await router.poll(2)

        assert _switches(hass, store) == before
        assert router.entities() == registered
        # Nothing was registered anew on the way.
        assert router.registry_ids() == ids

    @both_stores
    async def test_the_last_rule_of_a_group_keeps_its_number_and_its_switch(self, hass, store):
        path = MENUS[store]
        router = await start(hass, {path: _rules("*1", "*2")})
        kept = _switches(hass, store)["*2"]
        router.menus[path] = _rules("*2")
        await router.poll(3)
        ids = router.registry_ids()
        assert list(ids) == [kept]

        router = await restart(router)

        assert _switches(hass, store) == {"*2": kept}
        assert router.registry_ids() == ids


class TestWhatIsSaved:
    async def test_the_numbers_are_saved_by_store_reference_and_routeros_id(self, hass, hass_storage):
        router = await start(hass, {MENUS["filter"]: _rules("*1", "*2")})
        reference = router.coordinator.ds["filter"]["*1"]["legacy-uniq-id"]

        router = await restart(router)

        assert hass_storage[f"{DOMAIN}.rule_numbers.{ENTRY_ID}"]["data"] == {"filter": {reference: {"*1": 1, "*2": 2}}}

    async def test_nothing_is_written_without_identical_rules(self, hass, hass_storage):
        router = await start(hass, {MENUS["filter"]: [{".id": "*1", **RULE, "comment": "one"}]})
        await restart(router)
        assert f"{DOMAIN}.rule_numbers.{ENTRY_ID}" not in hass_storage

    @pytest.mark.parametrize(
        "saved",
        [
            None,
            "text",
            [1, 2],
            {"filter": "text"},
            {"filter": {"x": "text"}},
            {"filter": {"x": {"*1": "one"}}},
            {"filter": {"x": {"*1": 0}}},
            {"filter": {"x": {"*1": True}}},
            {"nonsense": {"x": {"*1": 1}}},
            {"filter": {SAME: {"*1": 1, "*2": 1}}},
        ],
    )
    async def test_a_file_that_makes_no_sense_is_ignored(self, hass, hass_storage, saved):
        hass_storage[f"{DOMAIN}.rule_numbers.{ENTRY_ID}"] = {"version": 1, "minor_version": 1, "key": f"{DOMAIN}.rule_numbers.{ENTRY_ID}", "data": saved}

        router = await start(hass, {MENUS["filter"]: _rules("*1", "*2")})

        assert sorted(router.coordinator.ds["filter"][uid]["uniq-id"][-2:] for uid in ("*1", "*2")) == ["#1", "#2"]
        assert router.coordinator.last_update_success

    async def test_a_file_that_cannot_be_read_falls_back_to_the_order_on_the_router(self, hass):
        unreadable = MagicMock()
        unreadable.async_load = AsyncMock(side_effect=OSError("unreadable"))
        with patch.object(MikrotikCoordinator, "rule_numbers_store", return_value=unreadable):
            router = await start(hass, {MENUS["filter"]: _rules("*5", "*6")})

        assert router.coordinator.last_update_success
        assert [router.coordinator.ds["filter"][uid]["uniq-id"][-2:] for uid in ("*5", "*6")] == ["#1", "#2"]

    async def test_numbers_saved_for_rows_that_hold_other_rules_now_are_not_used(self, hass, hass_storage):
        """The ids are there again after a reset of the router, with other rules behind them."""
        router = await start(hass, {MENUS["filter"]: _rules("*1", "*2", "*3")})
        router.menus[MENUS["filter"]] = _rules("*2", "*3")
        await router.poll(3)
        router.menus[MENUS["filter"]] = [{".id": "*2", **RULE, "dst-port": "80"}, {".id": "*3", **RULE, "dst-port": "80"}]

        router = await restart(router)

        assert [router.coordinator.ds["filter"][uid]["uniq-id"][-2:] for uid in ("*2", "*3")] == ["#1", "#2"]

    async def test_a_save_that_is_still_waiting_does_not_bring_the_file_back(self, hass, hass_storage):
        """Unloading writes it out, so nothing is left to be written after the entry was removed."""
        from custom_components.mikrotik_extended import async_remove_entry, async_unload_entry

        router = await start(hass, {MENUS["filter"]: _rules("*1", "*2")})
        key = f"{DOMAIN}.rule_numbers.{ENTRY_ID}"
        assert key not in hass_storage
        router.entry.runtime_data.data_coordinator.api.close = MagicMock()

        assert await async_unload_entry(hass, router.entry)
        assert hass_storage[key]["data"]["filter"]
        api = MagicMock()
        api.connect.return_value = False
        with patch("custom_components.mikrotik_extended.MikrotikAPI", return_value=api):
            await async_remove_entry(hass, router.entry)
        hass.bus.async_fire(EVENT_HOMEASSISTANT_FINAL_WRITE)
        await hass.async_block_till_done()

        assert key not in hass_storage

    async def test_a_read_that_ends_after_the_unload_saves_nothing(self, hass, hass_storage):
        """A cycle under way during the unload must not bring the removed file back."""
        from custom_components.mikrotik_extended import async_remove_entry, async_unload_entry

        router = await start(hass, {MENUS["filter"]: _rules("*1", "*2")})
        key = f"{DOMAIN}.rule_numbers.{ENTRY_ID}"
        coordinator = router.entry.runtime_data.data_coordinator
        coordinator.api.close = MagicMock()
        assert await async_unload_entry(hass, router.entry)
        api = MagicMock()
        api.connect.return_value = False
        with patch("custom_components.mikrotik_extended.MikrotikAPI", return_value=api):
            await async_remove_entry(hass, router.entry)
        assert key not in hass_storage

        coordinator._rule_numbers["filter"]["late"] = {"*8": 1, "*9": 2}
        coordinator._save_rule_numbers()
        hass.bus.async_fire(EVENT_HOMEASSISTANT_FINAL_WRITE)
        await hass.async_block_till_done()

        assert key not in hass_storage

    async def test_a_save_that_fails_does_not_stop_the_unload(self, hass, hass_storage):
        from custom_components.mikrotik_extended import async_unload_entry

        router = await start(hass, {MENUS["filter"]: _rules("*1", "*2")})
        coordinator = router.entry.runtime_data.data_coordinator
        coordinator.api.close = MagicMock()

        with patch.object(coordinator._rule_numbers_file, "async_save", side_effect=OSError("disk full")):
            assert await async_unload_entry(hass, router.entry)

        assert coordinator._rule_numbers_unwritten is True

    async def test_removing_the_entry_removes_the_file(self, hass, hass_storage):
        from custom_components.mikrotik_extended import async_remove_entry

        router = await start(hass, {MENUS["filter"]: _rules("*1", "*2")})
        await restart(router)
        assert f"{DOMAIN}.rule_numbers.{ENTRY_ID}" in hass_storage

        api = MagicMock()
        api.connect.return_value = False
        with patch("custom_components.mikrotik_extended.MikrotikAPI", return_value=api):
            await async_remove_entry(hass, router.entry)

        assert f"{DOMAIN}.rule_numbers.{ENTRY_ID}" not in hass_storage


def _issue(hass):
    return ir.async_get(hass).async_get_issue(DOMAIN, ISSUE)


class TestRepairIssue:
    async def test_no_issue_without_identical_rules(self, hass):
        await start(hass, {MENUS["filter"]: [{".id": "*1", **RULE}, {".id": "*2", **RULE, "comment": "second"}]})
        assert _issue(hass) is None

    async def test_identical_rules_raise_it_for_the_entry(self, hass):
        await start(hass, {MENUS["filter"]: _rules("*1", "*2")})

        issue = _issue(hass)
        assert issue is not None
        assert issue.is_fixable is False
        assert issue.severity is ir.IssueSeverity.WARNING
        assert issue.translation_key == "duplicate_rules"
        assert issue.translation_placeholders == {"host": "192.168.88.1", "menus": "/ip/firewall/filter"}

    async def test_every_menu_with_such_rules_is_named(self, hass):
        await start(hass, {MENUS["filter"]: _rules("*1", "*2"), MENUS["ipv6_filter"]: _rules("*1", "*2"), "/ip/firewall/raw": [{".id": "*1", "chain": "prerouting", "action": "drop", "disabled": False}]})

        assert _issue(hass).translation_placeholders["menus"] == "/ip/firewall/filter, /ipv6/firewall/filter"

    async def test_distinct_comments_clear_it(self, hass):
        router = await start(hass, {MENUS["filter"]: _rules("*1", "*2")})
        before = _switches(hass, "filter")

        router.menus[MENUS["filter"]] = [{".id": "*1", **RULE, "comment": "first"}, {".id": "*2", **RULE, "comment": "second"}]
        await router.poll()

        assert _issue(hass) is None
        # And the switches the user had are the ones that stay.
        assert router.entities() == {"filter-first": before["*1"], "filter-second": before["*2"]}

    async def test_it_stays_while_one_rule_still_carries_a_number(self, hass):
        """The rule keeps its number, so what the issue says still holds for it."""
        router = await start(hass, {MENUS["filter"]: _rules("*1", "*2")})

        router.menus[MENUS["filter"]] = _rules("*2")
        await router.poll(4)
        assert _issue(hass) is not None

        router.menus[MENUS["filter"]] = _rules("*2", comment="only one")
        await router.poll()
        assert _issue(hass) is None

    async def test_numbers_saved_for_a_menu_that_is_not_read_raise_nothing(self, hass):
        router = await start(hass, {MENUS["filter"]: _rules("*1", "*2")})
        router.menus[MENUS["filter"]] = []
        router.hass.config_entries.async_update_entry(router.entry, options={**router.entry.options, "sensor_filter": False})

        await restart(router)

        assert _issue(hass) is None

    async def test_it_goes_when_the_rules_are_gone(self, hass):
        router = await start(hass, {MENUS["filter"]: _rules("*1", "*2")})

        router.menus[MENUS["filter"]] = []
        await router.poll(2)
        assert _issue(hass) is None

    async def test_a_failed_read_keeps_it(self, hass):
        router = await start(hass, {MENUS["filter"]: _rules("*1", "*2")})

        router.menus[MENUS["filter"]] = None
        await router.poll(2)

        assert _issue(hass) is not None


class TestTranslations:
    def _files(self):
        files = sorted(glob.glob(os.path.join(COMPONENT, "translations", "*.json")))
        assert len(files) == 26
        return [os.path.join(COMPONENT, "strings.json"), *files]

    def test_every_language_has_the_issue_with_its_placeholders(self):
        for path in self._files():
            with open(path, encoding="utf-8") as handle:
                issue = json.load(handle)["issues"]["duplicate_rules"]
            assert set(issue) == {"title", "description"}, path
            assert "{host}" in issue["title"], path
            assert "{host}" in issue["description"] and "{menus}" in issue["description"], path
            for text in issue.values():
                assert chr(0x2014) not in text and chr(0x2013) not in text, path

    def test_every_language_has_the_same_issues(self):
        with open(os.path.join(COMPONENT, "strings.json"), encoding="utf-8") as handle:
            expected = set(json.load(handle)["issues"])
        for path in self._files():
            with open(path, encoding="utf-8") as handle:
                assert set(json.load(handle)["issues"]) == expected, path

    def test_english_is_the_source_text(self):
        texts = []
        for name in ("strings.json", os.path.join("translations", "en.json")):
            with open(os.path.join(COMPONENT, name), encoding="utf-8") as handle:
                texts.append(json.load(handle)["issues"]["duplicate_rules"])
        assert texts[0] == texts[1]

    def test_only_the_english_files_are_in_english(self):
        with open(os.path.join(COMPONENT, "strings.json"), encoding="utf-8") as handle:
            english = json.load(handle)["issues"]["duplicate_rules"]
        same = []
        for path in self._files()[1:]:
            with open(path, encoding="utf-8") as handle:
                if json.load(handle)["issues"]["duplicate_rules"] == english:
                    same.append(os.path.basename(path))
        assert same == ["en.json"]

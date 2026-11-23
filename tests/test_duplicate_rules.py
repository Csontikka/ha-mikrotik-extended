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

import pytest
from homeassistant.helpers import entity_platform
from homeassistant.helpers import issue_registry as ir

from custom_components.mikrotik_extended.const import DOMAIN
from custom_components.mikrotik_extended.coordinator import _disambiguate_uniq_ids

from .lifecycle_harness import ENTRY_ID, restart, start

COMPONENT = os.path.join(os.path.dirname(__file__), "..", "custom_components", "mikrotik_extended")
MENUS = {"filter": "/ip/firewall/filter", "ipv6_filter": "/ipv6/firewall/filter"}
RULE = {"chain": "forward", "action": "drop", "protocol": "tcp", "dst-port": "23", "disabled": False}
ISSUE = f"duplicate_rules_{ENTRY_ID}"

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

    def test_the_last_one_left_goes_by_the_plain_reference_again(self):
        """Which is what a restart would call it, so nothing changes then."""
        store, numbers = _store("*1", "*2"), {}
        _read(store, numbers)
        del store["*1"]

        assert _read(store, numbers) == []
        assert _suffixes(store) == {"*2": "same"}
        assert numbers == {}

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
        assert store["*9"]["uniq-id"] == "same"

    def test_groups_are_numbered_apart(self):
        store = {**_store("*1", "*2"), **_store("*3", "*4", reference="other")}
        numbers = {}
        for uid in ("*3", "*4"):
            store[uid]["own"] = "other"
        assert sorted(_read(store, numbers)) == ["other", "same"]
        assert _suffixes(store) == {"*1": "same #1", "*2": "same #2", "*3": "other #1", "*4": "other #2"}


class TestNumbersAfterAStart:
    """Nothing is remembered then; the numbers the registry holds entities for are handed out first."""

    def test_the_registered_numbers_go_first_in_order(self):
        store, numbers = _store("*2", "*3"), {}
        _disambiguate_uniq_ids(store, (), numbers, lambda base: {2, 3})
        assert _suffixes(store) == {"*2": "same #2", "*3": "same #3"}

    def test_a_rule_too_many_gets_the_lowest_number_left(self):
        store, numbers = _store("*2", "*3", "*4"), {}
        _disambiguate_uniq_ids(store, (), numbers, lambda base: {2, 3})
        assert _suffixes(store) == {"*2": "same #2", "*3": "same #3", "*4": "same #1"}

    def test_fewer_rules_than_numbers_take_the_lowest_ones(self):
        store, numbers = _store("*5", "*6"), {}
        _disambiguate_uniq_ids(store, (), numbers, lambda base: {1, 2, 4})
        assert _suffixes(store) == {"*5": "same #1", "*6": "same #2"}

    def test_nothing_registered_numbers_from_one(self):
        store, numbers = _store("*5", "*6"), {}
        _disambiguate_uniq_ids(store, (), numbers, lambda base: set())
        assert _suffixes(store) == {"*5": "same #1", "*6": "same #2"}

    def test_the_registry_is_asked_per_reference(self):
        store = {**_store("*1", "*2"), **_store("*3", "*4", reference="other")}
        _disambiguate_uniq_ids(store, (), {}, lambda base: {"same": {3, 4}, "other": {7}}[base])
        assert _suffixes(store) == {"*1": "same #3", "*2": "same #4", "*3": "other #7", "*4": "other #1"}

    def test_a_group_that_is_remembered_does_not_ask_the_registry(self):
        store, numbers = _store("*1", "*2", "*3"), {}
        _read(store, numbers)
        del store["*2"]
        store["*9"] = {"uniq-id": "same", "comment": ""}
        for uid in ("*1", "*3"):
            store[uid]["uniq-id"] = "same"

        _disambiguate_uniq_ids(store, (), numbers, lambda base: {5})

        assert _suffixes(store) == {"*1": "same #1", "*3": "same #3", "*9": "same #2"}

    def test_a_single_rule_is_never_numbered_from_the_registry(self):
        store = _store("*1")
        assert _disambiguate_uniq_ids(store, (), {}, lambda base: {2}) == []
        assert store["*1"]["uniq-id"] == "same"


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
        registered = router.entities()
        assert len(registered) == 2

        router = await restart(router)

        assert _switches(hass, store) == {"*2": before["*2"], "*3": before["*3"]}
        assert router.entities() == registered
        await _switch(hass, before["*3"]).async_turn_off()
        assert router.writes() == [(path, ".id", "*3", "disabled", True)]

    @both_stores
    async def test_nothing_changes_when_nothing_changed(self, hass, store):
        path = MENUS[store]
        router = await start(hass, {path: _rules("*1", "*2", "*3")})
        before, registered = _switches(hass, store), router.entities()

        router = await restart(router)
        await router.poll(2)

        assert _switches(hass, store) == before
        assert router.entities() == registered

    @both_stores
    async def test_the_last_rule_of_a_group_keeps_its_switch(self, hass, store):
        """It went back to the plain reference while Home Assistant ran, so the restart finds it there."""
        path = MENUS[store]
        router = await start(hass, {path: _rules("*1", "*2")})
        kept = _switches(hass, store)["*2"]
        router.menus[path] = _rules("*2")
        await router.poll(3)

        router = await restart(router)

        assert _switches(hass, store) == {"*2": kept}
        assert list(router.entities().values()) == [kept]

    async def test_only_the_first_read_of_a_store_looks_at_the_registry(self, hass):
        router = await start(hass, {MENUS["filter"]: [{".id": "*1", **RULE}]})
        # A leftover entry that looks like number 5 of this rule.
        reference = router.coordinator.ds["filter"]["*1"]["uniq-id"]
        plain = next(iter(router.entities()))
        router.registry.async_get_or_create("switch", DOMAIN, f"{ENTRY_ID}-{plain}_5", config_entry=router.entry, disabled_by=None)

        router.menus[MENUS["filter"]] = _rules("*1", "*2")
        await router.poll()

        assert router.coordinator.ds["filter"]["*1"]["uniq-id"] == f"{reference} #1"
        assert router.coordinator.ds["filter"]["*2"]["uniq-id"] == f"{reference} #2"


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

    async def test_it_goes_with_the_last_duplicate(self, hass):
        """The rule that is left needs no number, and its entity goes back to the plain id with it."""
        router = await start(hass, {MENUS["filter"]: _rules("*1", "*2")})
        kept = _switches(hass, "filter")["*2"]

        router.menus[MENUS["filter"]] = _rules("*2")
        await router.poll(2)
        assert _issue(hass) is not None

        await router.poll()
        assert _issue(hass) is None
        assert list(router.entities().values()) == [kept]
        assert not next(iter(router.entities())).endswith("_2")
        assert router.state(kept) == "on"

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

"""A rule whose reference changes while Home Assistant runs keeps its entity.

The reference is the comment where the rule has one and its contents
otherwise. Adding, removing or editing the comment therefore changes the
unique id the rule would be registered under. The registry entry follows the
row, so the entity id, the name and the area the user gave stay, and no
second entity is registered for the rule.
"""

import pytest
from homeassistant.helpers import area_registry as ar
from homeassistant.helpers import entity_platform
from homeassistant.helpers import entity_registry as er

from custom_components.mikrotik_extended.const import DOMAIN

from .lifecycle_harness import ENTRY_ID, start

MENUS = {"filter": "/ip/firewall/filter", "ipv6_filter": "/ipv6/firewall/filter"}
BARE = {".id": "*1", "chain": "input", "action": "drop", "protocol": "tcp", "dst-port": "23", "disabled": False}
OTHER = {".id": "*2", "chain": "forward", "action": "accept", "comment": "allow lan", "disabled": False}
QUEUES = "/queue/simple"
QUEUE = {"target": "192.168.88.10/32", "max-limit": "0/0", "rate": "0/0", "limit-at": "0/0", "burst-limit": "0/0", "burst-threshold": "0/0", "burst-time": "0s/0s", "disabled": False}

both_stores = pytest.mark.parametrize("store", sorted(MENUS))


def _entity(hass, entity_id):
    for platform in entity_platform.async_get_platforms(hass, DOMAIN):
        if entity_id in platform.entities:
            return platform.entities[entity_id]
    return None


async def _router(hass, store, *rows):
    """A router with the given rules in one menu, and the entity id of each rule in order."""
    path = MENUS[store]
    router = await start(hass, {path: list(rows)})
    entity_ids = []
    for row in rows:
        switch = next(entity for platform in entity_platform.async_get_platforms(hass, DOMAIN) for entity in platform.entities.values() if entity.entity_description.key == store and entity._uid == row[".id"])
        entity_ids.append(switch.entity_id)
    return router, path, entity_ids


def _customise(hass, router, entity_id):
    """Rename the entity and put it in an area, the way a user would."""
    area = ar.async_get(hass).async_create("Office")
    router.registry.async_update_entity(entity_id, name="My rule", icon="mdi:shield", area_id=area.id)
    return area.id


def _assert_customised(router, entity_id, area_id):
    entry = router.registry.async_get(entity_id)
    assert entry is not None
    assert (entry.name, entry.icon, entry.area_id) == ("My rule", "mdi:shield", area_id)


class TestTheCommentChanges:
    @both_stores
    async def test_a_comment_is_added(self, hass, store):
        router, path, (rule, other) = await _router(hass, store, BARE, OTHER)
        area_id = _customise(hass, router, rule)
        before = router.entities()

        router.menus[path] = [{**BARE, "comment": "drop telnet"}, OTHER]
        await router.poll()

        assert router.entity_id("switch", store, "drop_telnet") == rule
        assert set(router.entities().values()) == set(before.values())
        assert len(router.entities()) == 2
        _assert_customised(router, rule, area_id)
        assert router.state(rule) == "on"
        assert _entity(hass, rule).unique_id == router.registry.async_get(rule).unique_id
        # Later polls leave it alone, and the switch still writes to its rule.
        await router.poll(3)
        assert router.entities() == {f"{store}-drop_telnet": rule, f"{store}-allow_lan": other}
        await _entity(hass, rule).async_turn_off()
        assert router.writes() == [(path, ".id", "*1", "disabled", True)]

    @both_stores
    async def test_a_comment_is_removed(self, hass, store):
        router, path, (rule, other) = await _router(hass, store, {**BARE, "comment": "drop telnet"}, OTHER)
        area_id = _customise(hass, router, rule)

        router.menus[path] = [BARE, OTHER]
        await router.poll()

        assert router.entity_id("switch", store, "drop_telnet") is None
        assert sorted(router.entities().values()) == sorted([rule, other])
        _assert_customised(router, rule, area_id)
        assert router.state(rule) == "on"
        assert _entity(hass, rule).unique_id == router.registry.async_get(rule).unique_id
        await router.poll(3)
        assert sorted(router.entities().values()) == sorted([rule, other])

    @both_stores
    async def test_a_comment_is_changed(self, hass, store):
        router, path, (rule, other) = await _router(hass, store, {**BARE, "comment": "drop telnet"}, OTHER)
        area_id = _customise(hass, router, rule)

        router.menus[path] = [{**BARE, "comment": "no telnet"}, OTHER]
        await router.poll()

        assert router.entities() == {f"{store}-no_telnet": rule, f"{store}-allow_lan": other}
        _assert_customised(router, rule, area_id)
        await router.poll(3)
        assert router.entities() == {f"{store}-no_telnet": rule, f"{store}-allow_lan": other}

    @both_stores
    async def test_a_rule_without_a_comment_is_edited(self, hass, store):
        """Its reference is its contents, so an edit changes it like a new comment would."""
        router, path, (rule, other) = await _router(hass, store, BARE, OTHER)
        area_id = _customise(hass, router, rule)
        before = router.entities()

        router.menus[path] = [{**BARE, "dst-port": "2323"}, OTHER]
        await router.poll(2)

        after = router.entities()
        assert sorted(after.values()) == sorted(before.values())
        assert set(after) != set(before)
        _assert_customised(router, rule, area_id)
        assert router.state(rule) == "on"

    @both_stores
    async def test_a_comment_that_only_changes_case_or_punctuation_moves_nothing(self, hass, store):
        """The unique id is a slug of the reference, and that stays the same."""
        router, path, (rule, other) = await _router(hass, store, {**BARE, "comment": "drop telnet"}, OTHER)

        router.menus[path] = [{**BARE, "comment": "Drop-Telnet"}, OTHER]
        await router.poll(2)

        assert router.entities() == {f"{store}-drop_telnet": rule, f"{store}-allow_lan": other}

    @both_stores
    async def test_a_disabled_entity_follows_too(self, hass, store):
        router, path, (rule, other) = await _router(hass, store, {**BARE, "comment": "drop telnet"}, OTHER)
        router.registry.async_update_entity(rule, disabled_by=er.RegistryEntryDisabler.USER)
        await hass.async_block_till_done()

        router.menus[path] = [{**BARE, "comment": "no telnet"}, OTHER]
        await router.poll(2)

        assert router.entities() == {f"{store}-no_telnet": rule, f"{store}-allow_lan": other}
        assert router.registry.async_get(rule).disabled_by is er.RegistryEntryDisabler.USER

    @both_stores
    async def test_a_failed_read_moves_nothing(self, hass, store):
        router, path, _ = await _router(hass, store, {**BARE, "comment": "drop telnet"}, OTHER)
        before = router.entities()

        router.menus[path] = None
        await router.poll(3)

        assert router.entities() == before

    @both_stores
    async def test_a_rule_back_from_the_grace_with_another_comment(self, hass, store):
        """Missing from one read, then there again under a new comment: still the same row."""
        router, path, (rule, other) = await _router(hass, store, {**BARE, "comment": "drop telnet"}, OTHER)

        router.menus[path] = [OTHER]
        await router.poll()
        assert router.state(rule) == "unavailable"
        router.menus[path] = [{**BARE, "comment": "no telnet"}, OTHER]
        await router.poll()

        assert router.entities() == {f"{store}-no_telnet": rule, f"{store}-allow_lan": other}
        assert router.state(rule) == "on"


class TestTheNewUniqueIdIsTaken:
    @both_stores
    async def test_by_a_rule_that_is_being_removed(self, hass, store):
        """The move waits for the deleted rule's entity to go, without a second entity in the meantime."""
        first = {**BARE, "comment": "telnet"}
        second = {**OTHER, "comment": "lan"}
        router, path, (gone, kept) = await _router(hass, store, first, second)

        router.menus[path] = [{**second, "comment": "telnet"}]
        await router.poll()
        # Nothing moved yet, nothing added.
        assert router.entities() == {f"{store}-telnet": gone, f"{store}-lan": kept}
        assert router.state(gone) == "unavailable"
        assert router.state(kept) == "on"

        await router.poll(2)
        assert router.registry.async_get(gone) is None
        await router.poll()

        assert router.entities() == {f"{store}-telnet": kept}
        assert router.state(kept) == "on"
        await _entity(hass, kept).async_turn_off()
        assert router.writes() == [(path, ".id", "*2", "disabled", True)]

    @both_stores
    async def test_by_an_entity_the_user_disabled(self, hass, store):
        """It stays taken: the rule keeps its entity under the old id, and gets no second one."""
        first = {**BARE, "comment": "telnet"}
        second = {**OTHER, "comment": "lan"}
        router, path, (disabled, kept) = await _router(hass, store, first, second)
        router.registry.async_update_entity(disabled, disabled_by=er.RegistryEntryDisabler.USER)
        await hass.async_block_till_done()

        router.menus[path] = [{**second, "comment": "telnet"}]
        await router.poll(5)

        assert router.entities() == {f"{store}-telnet": disabled, f"{store}-lan": kept}
        assert router.state(kept) == "on"
        await _entity(hass, kept).async_turn_off()
        assert router.writes() == [(path, ".id", "*2", "disabled", True)]

    @both_stores
    async def test_by_a_rule_that_moves_on_in_the_same_read(self, hass, store):
        """One rule takes the comment another gives up: both follow."""
        first = {**BARE, "comment": "telnet"}
        second = {**OTHER, "comment": "lan"}
        router, path, (one, two) = await _router(hass, store, first, second)

        router.menus[path] = [{**first, "comment": "old telnet"}, {**second, "comment": "telnet"}]
        await router.poll()

        assert router.entities() == {f"{store}-old_telnet": one, f"{store}-telnet": two}
        assert _entity(hass, one)._uid == "*1"
        assert _entity(hass, two)._uid == "*2"

    @both_stores
    async def test_two_rules_that_trade_comments_keep_their_entities(self, hass, store):
        """Neither unique id is free, so nothing moves and nothing is added.

        Each switch still controls the rule it always did.
        """
        first = {**BARE, "comment": "telnet"}
        second = {**OTHER, "comment": "lan"}
        router, path, (one, two) = await _router(hass, store, first, second)

        router.menus[path] = [{**first, "comment": "lan"}, {**second, "comment": "telnet"}]
        await router.poll(3)

        assert router.entities() == {f"{store}-telnet": one, f"{store}-lan": two}
        await _entity(hass, one).async_turn_off()
        assert router.writes() == [(path, ".id", "*1", "disabled", True)]


class TestAnEntryOfAnotherRow:
    async def test_two_comments_that_read_alike_as_an_id(self, hass):
        """Only the first of them has an entity; the other one changing does not take it."""
        first = {**BARE, "comment": "drop telnet"}
        second = {**OTHER, "comment": "Drop-Telnet"}
        router = await start(hass, {MENUS["filter"]: [first, second]})
        entity_id = router.entity_id("switch", "filter", "drop_telnet")
        assert len(router.entities()) == 1
        assert _entity(hass, entity_id)._uid == "*1"

        router.menus[MENUS["filter"]] = [first, {**second, "comment": "allow lan"}]
        await router.poll()

        assert router.entity_id("switch", "filter", "drop_telnet") == entity_id
        assert _entity(hass, entity_id)._uid == "*1"
        added = router.entity_id("switch", "filter", "allow_lan")
        assert added is not None
        assert _entity(hass, added)._uid == "*2"

    async def test_a_disabled_entry_two_rows_could_claim_is_left_alone(self, hass):
        first = {**BARE, "comment": "drop telnet"}
        second = {**OTHER, "comment": "Drop-Telnet"}
        router = await start(hass, {MENUS["filter"]: [first, second]})
        entity_id = router.entity_id("switch", "filter", "drop_telnet")
        router.registry.async_update_entity(entity_id, disabled_by=er.RegistryEntryDisabler.USER)
        await hass.async_block_till_done()

        router.menus[MENUS["filter"]] = [first, {**second, "comment": "allow lan"}]
        await router.poll()

        assert router.entity_id("switch", "filter", "drop_telnet") == entity_id
        assert router.entity_id("switch", "filter", "allow_lan") not in (None, entity_id)


class TestQueues:
    async def test_a_renamed_queue_keeps_its_entity(self, hass):
        router = await start(hass, {QUEUES: [{".id": "*1", "name": "kids", **QUEUE}]})
        entity_id = router.entity_id("switch", "queue", "kids")
        area_id = _customise(hass, router, entity_id)

        router.menus[QUEUES] = [{".id": "*1", "name": "children", **QUEUE}]
        await router.poll(2)

        assert router.entities() == {"queue-children": entity_id}
        _assert_customised(router, entity_id, area_id)

    async def test_a_queue_that_loses_its_comment_goes_by_its_name_again(self, hass):
        """Without waiting for a restart, which used to change the id and drop the entity."""
        router = await start(hass, {QUEUES: [{".id": "*1", "name": "kids", "comment": "children", **QUEUE}]})
        entity_id = router.entity_id("switch", "queue", "children")

        router.menus[QUEUES] = [{".id": "*1", "name": "kids", **QUEUE}]
        await router.poll(2)

        assert router.coordinator.ds["queue"]["*1"]["uniq-id"] == "kids"
        assert router.entities() == {"queue-kids": entity_id}

    async def test_a_queue_on_the_grace_keeps_its_reference_through_a_failed_read(self, hass):
        router = await start(hass, {QUEUES: [{".id": "*1", "name": "kids", "comment": "children", **QUEUE}, {".id": "*2", "name": "guests", **QUEUE}]})
        router.menus[QUEUES] = [{".id": "*2", "name": "guests", **QUEUE}]
        await router.poll()
        router.menus[QUEUES] = None
        await router.poll()

        assert router.coordinator.ds["queue"]["*1"]["uniq-id"] == "children"
        assert router.coordinator.ds["queue"]["*2"]["uniq-id"] == "guests"


class TestWhatIsRemembered:
    async def test_the_first_cycle_only_takes_note(self, hass):
        router = await start(hass, {MENUS["filter"]: [{**BARE, "comment": "drop telnet"}, OTHER]})
        assert router.coordinator._rule_references["filter"] == {"*1": "drop telnet", "*2": "allow lan"}

    async def test_a_row_that_left_the_store_is_forgotten(self, hass):
        router = await start(hass, {MENUS["filter"]: [{**BARE, "comment": "drop telnet"}, OTHER]})
        router.menus[MENUS["filter"]] = [OTHER]
        await router.poll(2)
        assert router.coordinator._rule_references["filter"]["*1"] == "drop telnet"
        await router.poll()
        assert router.coordinator._rule_references["filter"] == {"*2": "allow lan"}

    async def test_an_entry_of_another_config_entry_is_never_touched(self, hass):
        router = await start(hass, {MENUS["filter"]: [{**BARE, "comment": "drop telnet"}]})
        foreign = router.registry.async_get_or_create("switch", DOMAIN, "other-filter-no_telnet")

        router.menus[MENUS["filter"]] = [{**BARE, "comment": "no telnet"}]
        await router.poll()

        assert router.registry.async_get(foreign.entity_id).unique_id == "other-filter-no_telnet"
        assert router.entity_id("switch", "filter", "no_telnet") is not None
        assert ENTRY_ID in router.registry.async_get(router.entity_id("switch", "filter", "no_telnet")).unique_id

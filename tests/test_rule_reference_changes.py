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
from custom_components.mikrotik_extended.entity import REBIND_DATA_PATHS

from .lifecycle_harness import ENTRY_ID, start
from .test_entity_lifecycle import STORES

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
    async def test_a_rule_without_a_comment_that_is_edited_is_a_new_rule(self, hass, store):
        """Its contents are all that is known of it. The old entity goes at once, not at the next start."""
        router, path, (rule, other) = await _router(hass, store, BARE, OTHER)
        before = router.registry_ids()

        router.menus[path] = [{**BARE, "dst-port": "2323"}, OTHER]
        await router.poll()

        after = router.registry_ids()
        assert len(after) == 2
        assert after[other] == before[other]
        assert before[rule] not in after.values()
        (new,) = [entity_id for entity_id, entry_id in after.items() if entry_id not in before.values()]
        assert _entity(hass, new)._uid == "*1"
        assert router.state(new) == "on"
        await router.poll(2)
        assert router.registry_ids() == after

    @both_stores
    async def test_a_commented_rule_that_is_edited_stays_what_it_is(self, hass, store):
        router, path, _ = await _router(hass, store, {**BARE, "comment": "drop telnet"}, OTHER)
        before = router.registry_ids()

        router.menus[path] = [{**BARE, "comment": "drop telnet", "dst-port": "2323"}, OTHER]
        await router.poll(2)

        assert router.registry_ids() == before

    @both_stores
    async def test_a_comment_and_the_contents_changed_at_once_is_a_new_rule(self, hass, store):
        """Nothing is left to say it is the same rule; the RouterOS id alone is not trusted."""
        router, path, (rule, other) = await _router(hass, store, {**BARE, "comment": "drop telnet"}, OTHER)
        before = router.registry_ids()

        router.menus[path] = [{**BARE, "comment": "no telnet", "dst-port": "2323"}, OTHER]
        await router.poll()

        assert router.registry.async_get(rule) is None
        assert set(router.entities()) == {f"{store}-no_telnet", f"{store}-allow_lan"}
        assert router.registry_ids()[other] == before[other]

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
        """Each waits for the unique id the other holds; one steps aside so both can move."""
        first = {**BARE, "comment": "telnet"}
        second = {**OTHER, "comment": "lan"}
        router, path, (one, two) = await _router(hass, store, first, second)
        before = router.registry_ids()

        router.menus[path] = [{**first, "comment": "lan"}, {**second, "comment": "telnet"}]
        await router.poll()

        assert router.entities() == {f"{store}-lan": one, f"{store}-telnet": two}
        assert router.registry_ids() == before
        assert (_entity(hass, one)._uid, _entity(hass, two)._uid) == ("*1", "*2")
        await _entity(hass, one).async_turn_off()
        assert router.writes() == [(path, ".id", "*1", "disabled", True)]
        await router.poll(2)
        assert router.entities() == {f"{store}-lan": one, f"{store}-telnet": two}

    @both_stores
    async def test_three_rules_that_pass_their_comments_round(self, hass, store):
        rows = [{**BARE, "comment": "one"}, {**OTHER, "comment": "two"}, {".id": "*3", "chain": "output", "action": "accept", "comment": "three", "disabled": False}]
        router, path, (one, two, three) = await _router(hass, store, *rows)
        before = router.registry_ids()

        router.menus[path] = [{**rows[0], "comment": "two"}, {**rows[1], "comment": "three"}, {**rows[2], "comment": "one"}]
        await router.poll()

        assert router.entities() == {f"{store}-two": one, f"{store}-three": two, f"{store}-one": three}
        assert router.registry_ids() == before

    @both_stores
    async def test_a_chain_that_ends_at_a_taken_id_leaves_nothing_half_moved(self, hass, store):
        rows = [{**BARE, "comment": "one"}, {**OTHER, "comment": "two"}, {".id": "*3", "chain": "output", "action": "accept", "comment": "three", "disabled": False}]
        router, path, (one, two, three) = await _router(hass, store, *rows)
        router.registry.async_update_entity(three, disabled_by=er.RegistryEntryDisabler.USER)
        await hass.async_block_till_done()

        # The first takes the comment of the second, the second that of the third, which is deleted but disabled.
        router.menus[path] = [{**rows[0], "comment": "two"}, {**rows[1], "comment": "three"}]
        await router.poll(4)

        assert router.entities() == {f"{store}-one": one, f"{store}-two": two, f"{store}-three": three}
        assert (_entity(hass, one)._uid, _entity(hass, two)._uid) == ("*1", "*2")


class TestTheSameIdsForOtherRules:
    """A configuration that is reset and loaded again numbers its rules from the start."""

    @both_stores
    async def test_rules_that_come_back_under_each_others_ids(self, hass, store):
        first = {**BARE, "comment": "telnet"}
        second = {**OTHER, "comment": "lan"}
        router, path, (telnet, lan) = await _router(hass, store, first, second)
        area_id = _customise(hass, router, telnet)
        before = router.registry_ids()

        router.menus[path] = [{**second, ".id": "*1"}, {**first, ".id": "*2"}]
        await router.poll()

        # Each entity is still the entity of its rule, which now has the other id.
        assert router.entities() == {f"{store}-telnet": telnet, f"{store}-lan": lan}
        assert router.registry_ids() == before
        _assert_customised(router, telnet, area_id)
        assert (_entity(hass, telnet)._uid, _entity(hass, lan)._uid) == ("*2", "*1")
        assert router.state(telnet) == "on"
        await _entity(hass, telnet).async_turn_off()
        assert router.writes() == [(path, ".id", "*2", "disabled", True)]

    @both_stores
    async def test_rules_pushed_down_by_a_new_one(self, hass, store):
        first = {**BARE, "comment": "telnet"}
        second = {**OTHER, "comment": "lan"}
        router, path, (telnet, lan) = await _router(hass, store, first, second)
        before = router.registry_ids()

        router.menus[path] = [{".id": "*1", "chain": "output", "action": "accept", "comment": "new", "disabled": False}, {**first, ".id": "*2"}, {**second, ".id": "*3"}]
        await router.poll(2)

        after = router.registry_ids()
        assert {entity_id: after[entity_id] for entity_id in before} == before
        assert set(router.entities()) == {f"{store}-new", f"{store}-telnet", f"{store}-lan"}
        assert (_entity(hass, telnet)._uid, _entity(hass, lan)._uid) == ("*2", "*3")
        assert _entity(hass, router.entity_id("switch", store, "new"))._uid == "*1"


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
    async def test_a_renamed_queue_with_a_comment_keeps_its_entity(self, hass):
        router = await start(hass, {QUEUES: [{".id": "*1", "name": "kids", "comment": "children", **QUEUE}]})
        before = router.registry_ids()

        router.menus[QUEUES] = [{".id": "*1", "name": "the kids", "comment": "children", **QUEUE}]
        await router.poll(2)

        assert router.registry_ids() == before
        assert set(router.entities()) == {"queue-children"}

    async def test_a_renamed_queue_without_a_comment_is_a_new_queue(self, hass):
        """The name is all that is known of it. Its old entity goes at once, not at the next start."""
        router = await start(hass, {QUEUES: [{".id": "*1", "name": "kids", **QUEUE}]})
        before = router.registry_ids()

        router.menus[QUEUES] = [{".id": "*1", "name": "children", **QUEUE}]
        await router.poll(2)

        after = router.registry_ids()
        assert set(router.entities()) == {"queue-children"}
        assert not set(after.values()) & set(before.values())

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
        assert {uid: reference for uid, (reference, _contents) in router.coordinator._rule_references["filter"].items()} == {"*1": "drop telnet", "*2": "allow lan"}

    async def test_a_row_that_left_the_store_is_forgotten(self, hass):
        router = await start(hass, {MENUS["filter"]: [{**BARE, "comment": "drop telnet"}, OTHER]})
        router.menus[MENUS["filter"]] = [OTHER]
        await router.poll(2)
        assert router.coordinator._rule_references["filter"]["*1"][0] == "drop telnet"
        await router.poll()
        assert list(router.coordinator._rule_references["filter"]) == ["*2"]

    async def test_an_entry_of_another_config_entry_is_never_touched(self, hass):
        router = await start(hass, {MENUS["filter"]: [{**BARE, "comment": "drop telnet"}]})
        foreign = router.registry.async_get_or_create("switch", DOMAIN, "other-filter-no_telnet")

        router.menus[MENUS["filter"]] = [{**BARE, "comment": "no telnet"}]
        await router.poll()

        assert router.registry.async_get(foreign.entity_id).unique_id == "other-filter-no_telnet"
        assert router.entity_id("switch", "filter", "no_telnet") is not None
        assert ENTRY_ID in router.registry.async_get(router.entity_id("switch", "filter", "no_telnet")).unique_id


class TestEveryRuleStore:
    """What counts as the contents of a rule differs per menu; the comment must be no part of it anywhere."""

    @pytest.mark.parametrize("store", sorted(REBIND_DATA_PATHS))
    async def test_the_comment_can_change_come_and_go(self, hass, store):
        path, rows, _ = STORES[store]
        router = await start(hass, {path: list(rows)})
        before = router.registry_ids()
        assert len(before) == 2
        (rule,) = [entity_id for entity_id in before if _entity(hass, entity_id)._uid == "*1"]
        area_id = _customise(hass, router, rule)

        for comment in ("renamed", None, "back again"):
            first = {name: value for name, value in rows[0].items() if name != "comment"}
            if comment:
                first["comment"] = comment
            router.menus[path] = [first, rows[1]]
            await router.poll(2)

            assert router.registry_ids() == before, comment
            assert _entity(hass, rule)._uid == "*1"
            assert _entity(hass, rule).unique_id == router.registry.async_get(rule).unique_id
            _assert_customised(router, rule, area_id)
        assert router.entity_id("switch", store, "back_again") == rule


class TestTradedAndEditedAtOnce:
    @both_stores
    async def test_one_rule_takes_the_comment_of_a_rule_that_is_edited_too(self, hass, store):
        """The first keeps its entity under the new comment; the second, changed all over, is a new rule."""
        first = {**BARE, "comment": "telnet"}
        second = {**OTHER, "comment": "lan"}
        router, path, (one, two) = await _router(hass, store, first, second)

        router.menus[path] = [{**first, "comment": "lan"}, {**second, "comment": "telnet", "chain": "output"}]
        await router.poll(3)

        bound = {entity._uid: entity.entity_id for platform in entity_platform.async_get_platforms(hass, DOMAIN) for entity in platform.entities.values()}
        assert len(bound) == 2
        assert bound["*1"] == one
        assert router.entities()[f"{store}-lan"] == one
        assert router.registry.async_get(two) is None or bound["*2"] == two
        assert set(router.entities()) == {f"{store}-lan", f"{store}-telnet"}

    async def test_a_ring_next_to_comments_that_read_alike_leaves_the_cycle_running(self, hass):
        rows = [{**BARE, "comment": "one"}, {**OTHER, "comment": "two"}, {".id": "*3", "chain": "output", "action": "accept", "comment": "three", "disabled": False}]
        router = await start(hass, {MENUS["filter"]: rows})

        for _ in range(2):
            router.menus[MENUS["filter"]] = [{**rows[0], "comment": "two"}, {**rows[1], "comment": "one"}, {**rows[2], "comment": "Two!"}]
            await router.poll(2)
            assert router.coordinator.last_update_success
            router.menus[MENUS["filter"]] = rows
            await router.poll(2)
            assert router.coordinator.last_update_success
        assert len({entity._uid for platform in entity_platform.async_get_platforms(hass, DOMAIN) for entity in platform.entities.values()}) == 3

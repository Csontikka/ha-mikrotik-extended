"""A rule whose comment changes while Home Assistant runs keeps its entity.

The reference is the comment where the rule has one and its contents
otherwise. Adding, removing or editing the comment therefore changes the
unique id the rule would be registered under. In the case that leaves no
doubt the registry entry follows the row, so the entity id, the name and the
area the user gave stay, and no second entity is registered for the rule.

Every other case is deliberately left as it always was: nothing is moved,
the old entity stays with its row, and where the new unique id is free the
rule gets a second entity until the next start. What must hold there is that
no switch ends up on another rule.
"""

import pytest
from homeassistant.helpers import area_registry as ar
from homeassistant.helpers import entity_platform
from homeassistant.helpers import entity_registry as er

from custom_components.mikrotik_extended.const import DOMAIN
from custom_components.mikrotik_extended.coordinator import MikrotikCoordinator

from .lifecycle_harness import ENTRY_ID, start
from .test_entity_lifecycle import STORES

RULE_STORES = sorted(MikrotikCoordinator._COMMENT_KEYED_RULES)
MENUS = {"filter": "/ip/firewall/filter", "ipv6_filter": "/ipv6/firewall/filter"}
BARE = {".id": "*1", "chain": "input", "action": "drop", "protocol": "tcp", "dst-port": "23", "disabled": False}
OTHER = {".id": "*2", "chain": "forward", "action": "accept", "comment": "allow lan", "disabled": False}
QUEUES = "/queue/simple"
QUEUE = {"target": "192.168.88.10/32", "max-limit": "0/0", "rate": "0/0", "limit-at": "0/0", "burst-limit": "0/0", "burst-threshold": "0/0", "burst-time": "0s/0s", "disabled": False}

every_store = pytest.mark.parametrize("store", RULE_STORES)
both_stores = pytest.mark.parametrize("store", sorted(MENUS))


def _entity(hass, entity_id):
    for platform in entity_platform.async_get_platforms(hass, DOMAIN):
        if entity_id in platform.entities:
            return platform.entities[entity_id]
    return None


def _bound(hass, store):
    """Which rows the switches of a store stand for: entity id to RouterOS id."""
    return {entity.entity_id: entity._uid for platform in entity_platform.async_get_platforms(hass, DOMAIN) for entity in platform.entities.values() if entity.entity_description.key == store}


def _with_comment(row, comment):
    row = {name: value for name, value in row.items() if name != "comment"}
    if comment:
        row["comment"] = comment
    return row


async def _router(hass, store, *rows):
    """A router with the given rules in one menu, and the entity id of each rule in order."""
    path = MENUS[store]
    router = await start(hass, {path: list(rows)})
    by_row = {uid: entity_id for entity_id, uid in _bound(hass, store).items()}
    return router, path, [by_row[row[".id"]] for row in rows]


def _customise(hass, router, entity_id):
    """Rename the entity and put it in an area, the way a user would."""
    area = ar.async_get(hass).async_get_or_create("Office")
    router.registry.async_update_entity(entity_id, name="My rule", icon="mdi:shield", area_id=area.id)
    return area.id


def _assert_customised(router, entity_id, area_id):
    entry = router.registry.async_get(entity_id)
    assert entry is not None
    assert (entry.name, entry.icon, entry.area_id) == ("My rule", "mdi:shield", area_id)


def test_every_rule_store_has_sample_rows():
    assert set(RULE_STORES) == {"nat", "mangle", "filter", "raw", "routing_rules", "queue", "ipv6_filter", "ipv6_raw", "ipv6_nat", "ipv6_mangle"}
    assert set(RULE_STORES) <= set(STORES)


class TestTheCommentChanges:
    async def _one_change(self, hass, store, before, after):
        """Give the first rule of a store one comment, then another; return what is needed to judge."""
        path, rows, _ = STORES[store]
        router = await start(hass, {path: [_with_comment(rows[0], before), rows[1]]})
        ids = router.registry_ids()
        assert len(ids) == 2
        (rule,) = [entity_id for entity_id, uid in _bound(hass, store).items() if uid == "*1"]
        area_id = _customise(hass, router, rule)
        old_unique_id = router.registry.async_get(rule).unique_id

        router.menus[path] = [_with_comment(rows[0], after), rows[1]]
        await router.poll()

        # The same two registry entries, the rule's own under a new unique id.
        assert router.registry_ids() == ids
        assert router.registry.async_get(rule).unique_id != old_unique_id
        assert _entity(hass, rule).unique_id == router.registry.async_get(rule).unique_id
        assert _bound(hass, store)[rule] == "*1"
        _assert_customised(router, rule, area_id)
        assert router.state(rule) not in (None, "unavailable")
        # Later polls leave it alone.
        await router.poll(3)
        assert router.registry_ids() == ids
        return router, path, rule

    @every_store
    async def test_a_comment_is_added(self, hass, store):
        router, path, rule = await self._one_change(hass, store, None, "brand new")
        assert router.entity_id("switch", store, "brand_new") == rule
        await _entity(hass, rule).async_turn_off()
        assert router.writes() == [(path, ".id", "*1", "disabled", True)]

    @every_store
    async def test_a_comment_is_removed(self, hass, store):
        router, _, _ = await self._one_change(hass, store, "to go", None)
        assert router.entity_id("switch", store, "to_go") is None
        assert len(router.entities()) == 2

    @every_store
    async def test_a_comment_is_changed(self, hass, store):
        router, _, rule = await self._one_change(hass, store, "at first", "later on")
        assert router.entity_id("switch", store, "later_on") == rule
        assert router.entity_id("switch", store, "at_first") is None

    @every_store
    async def test_a_comment_that_only_changes_case_or_punctuation_moves_nothing(self, hass, store):
        """The unique id is a slug of the reference, and that stays the same."""
        path, rows, _ = STORES[store]
        router = await start(hass, {path: [_with_comment(rows[0], "drop telnet"), rows[1]]})
        before = router.entities()

        router.menus[path] = [_with_comment(rows[0], "Drop-Telnet"), rows[1]]
        await router.poll(2)

        assert router.entities() == before

    @every_store
    async def test_a_disabled_entity_follows_too(self, hass, store):
        path, rows, _ = STORES[store]
        router = await start(hass, {path: [_with_comment(rows[0], "at first"), rows[1]]})
        rule = router.entity_id("switch", store, "at_first")
        ids = router.registry_ids()
        router.registry.async_update_entity(rule, disabled_by=er.RegistryEntryDisabler.USER)
        await hass.async_block_till_done()

        router.menus[path] = [_with_comment(rows[0], "later on"), rows[1]]
        await router.poll(2)

        assert router.registry_ids() == ids
        assert router.entity_id("switch", store, "later_on") == rule
        assert router.registry.async_get(rule).disabled_by is er.RegistryEntryDisabler.USER

    @every_store
    async def test_a_failed_read_moves_nothing(self, hass, store):
        path, rows, _ = STORES[store]
        router = await start(hass, {path: list(rows)})
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

    @both_stores
    async def test_a_commented_rule_that_is_edited_stays_what_it_is(self, hass, store):
        router, path, _ = await _router(hass, store, {**BARE, "comment": "drop telnet"}, OTHER)
        before = router.registry_ids()

        router.menus[path] = [{**BARE, "comment": "drop telnet", "dst-port": "2323"}, OTHER]
        await router.poll(2)

        assert router.registry_ids() == before


class TestNothingIsMovedOnAGuess:
    """In each of these the entries stay where they are and every switch stays on its own row."""

    def _assert_untouched(self, hass, router, store, ids, bound):
        now = router.registry_ids()
        assert {entity_id: now.get(entity_id) for entity_id in ids} == ids
        assert not any(unique_id.endswith("-moving") for unique_id in router.entities())
        now_bound = _bound(hass, store)
        assert {entity_id: now_bound.get(entity_id) for entity_id in bound} == bound

    @both_stores
    async def test_the_contents_changed_as_well(self, hass, store):
        """Nothing is left to say it is the same rule; the RouterOS id alone is not trusted."""
        router, path, _ = await _router(hass, store, {**BARE, "comment": "drop telnet"}, OTHER)
        ids, bound, unique_ids = router.registry_ids(), _bound(hass, store), router.entities()

        router.menus[path] = [{**BARE, "comment": "no telnet", "dst-port": "2323"}, OTHER]
        await router.poll(2)

        self._assert_untouched(hass, router, store, ids, bound)
        assert {unique_id: router.entities()[unique_id] for unique_id in unique_ids} == unique_ids

    @both_stores
    async def test_a_rule_without_a_comment_is_edited(self, hass, store):
        router, path, _ = await _router(hass, store, BARE, OTHER)
        ids, bound, unique_ids = router.registry_ids(), _bound(hass, store), router.entities()

        router.menus[path] = [{**BARE, "dst-port": "2323"}, OTHER]
        await router.poll(2)

        self._assert_untouched(hass, router, store, ids, bound)
        assert {unique_id: router.entities()[unique_id] for unique_id in unique_ids} == unique_ids

    @both_stores
    async def test_the_new_unique_id_belongs_to_a_rule_that_is_being_removed(self, hass, store):
        first = {**BARE, "comment": "telnet"}
        second = {**OTHER, "comment": "lan"}
        router, path, (gone, kept) = await _router(hass, store, first, second)

        router.menus[path] = [{**second, "comment": "telnet"}]
        await router.poll()

        assert router.entities() == {f"{store}-telnet": gone, f"{store}-lan": kept}
        assert router.state(gone) == "unavailable"
        assert _bound(hass, store) == {gone: "*1", kept: "*2"}
        await _entity(hass, kept).async_turn_off()
        assert router.writes() == [(path, ".id", "*2", "disabled", True)]
        # Also later, when the removed rule's entity is gone, the kept one stays on its row.
        await router.poll(4)
        assert _bound(hass, store)[kept] == "*2"
        assert router.registry.async_get(kept).unique_id == f"{ENTRY_ID}-{store}-lan"

    @both_stores
    async def test_the_new_unique_id_belongs_to_an_entity_the_user_disabled(self, hass, store):
        first = {**BARE, "comment": "telnet"}
        second = {**OTHER, "comment": "lan"}
        router, path, (disabled, kept) = await _router(hass, store, first, second)
        router.registry.async_update_entity(disabled, disabled_by=er.RegistryEntryDisabler.USER)
        await hass.async_block_till_done()

        router.menus[path] = [{**second, "comment": "telnet"}]
        await router.poll(5)

        assert router.entities() == {f"{store}-telnet": disabled, f"{store}-lan": kept}
        assert _bound(hass, store) == {kept: "*2"}

    @both_stores
    async def test_one_rule_takes_the_comment_another_gives_up(self, hass, store):
        first = {**BARE, "comment": "telnet"}
        second = {**OTHER, "comment": "lan"}
        router, path, _ = await _router(hass, store, first, second)
        ids, bound = router.registry_ids(), _bound(hass, store)

        router.menus[path] = [{**first, "comment": "old telnet"}, {**second, "comment": "telnet"}]
        await router.poll(3)

        self._assert_untouched(hass, router, store, ids, bound)

    @both_stores
    async def test_two_rules_trade_comments(self, hass, store):
        first = {**BARE, "comment": "telnet"}
        second = {**OTHER, "comment": "lan"}
        router, path, (one, _two) = await _router(hass, store, first, second)
        ids, bound = router.registry_ids(), _bound(hass, store)

        router.menus[path] = [{**first, "comment": "lan"}, {**second, "comment": "telnet"}]
        await router.poll(3)

        self._assert_untouched(hass, router, store, ids, bound)
        assert router.registry_ids() == ids
        await _entity(hass, one).async_turn_off()
        assert router.writes() == [(path, ".id", "*1", "disabled", True)]

    @both_stores
    async def test_three_rules_pass_their_comments_round(self, hass, store):
        rows = [{**BARE, "comment": "one"}, {**OTHER, "comment": "two"}, {".id": "*3", "chain": "output", "action": "accept", "comment": "three", "disabled": False}]
        router, path, _ = await _router(hass, store, *rows)
        ids, bound = router.registry_ids(), _bound(hass, store)

        router.menus[path] = [{**rows[0], "comment": "two"}, {**rows[1], "comment": "three"}, {**rows[2], "comment": "one"}]
        await router.poll(3)

        self._assert_untouched(hass, router, store, ids, bound)
        assert router.registry_ids() == ids

    @both_stores
    async def test_a_comment_traded_with_a_rule_that_is_edited_too(self, hass, store):
        first = {**BARE, "comment": "telnet"}
        second = {**OTHER, "comment": "lan"}
        router, path, _ = await _router(hass, store, first, second)
        ids, bound = router.registry_ids(), _bound(hass, store)

        router.menus[path] = [{**first, "comment": "lan"}, {**second, "comment": "telnet", "chain": "output"}]
        await router.poll(3)

        self._assert_untouched(hass, router, store, ids, bound)
        assert router.registry_ids() == ids

    @both_stores
    async def test_two_rules_take_the_same_new_comment(self, hass, store):
        """Both then go by a numbered reference; each keeps its entity under it."""
        first = {**BARE, "comment": "telnet"}
        second = {**OTHER, "comment": "lan"}
        router, path, (one, two) = await _router(hass, store, first, second)
        ids, bound = router.registry_ids(), _bound(hass, store)

        router.menus[path] = [{**first, "comment": "shared"}, {**second, "comment": "shared"}]
        await router.poll(2)

        assert router.registry_ids() == ids
        assert _bound(hass, store) == bound
        assert router.entities() == {f"{store}-shared_1": one, f"{store}-shared_2": two}

    async def test_two_comments_that_read_alike_as_an_id(self, hass):
        """Only the first of them has an entity; the other one changing does not take it."""
        first = {**BARE, "comment": "drop telnet"}
        second = {**OTHER, "comment": "Drop-Telnet"}
        router = await start(hass, {MENUS["filter"]: [first, second]})
        entity_id = router.entity_id("switch", "filter", "drop_telnet")
        assert _bound(hass, "filter") == {entity_id: "*1"}

        router.menus[MENUS["filter"]] = [first, {**second, "comment": "allow lan"}]
        await router.poll()

        assert router.entity_id("switch", "filter", "drop_telnet") == entity_id
        added = router.entity_id("switch", "filter", "allow_lan")
        assert added not in (None, entity_id)
        assert _bound(hass, "filter") == {entity_id: "*1", added: "*2"}

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

    async def test_an_entry_of_another_config_entry_is_never_touched(self, hass):
        router = await start(hass, {MENUS["filter"]: [{**BARE, "comment": "drop telnet"}]})
        foreign = router.registry.async_get_or_create("switch", DOMAIN, "other-filter-no_telnet")

        router.menus[MENUS["filter"]] = [{**BARE, "comment": "no telnet"}]
        await router.poll()

        assert router.registry.async_get(foreign.entity_id).unique_id == "other-filter-no_telnet"
        assert ENTRY_ID in router.registry.async_get(router.entity_id("switch", "filter", "no_telnet")).unique_id


class TestQueues:
    async def test_a_renamed_queue_with_a_comment_keeps_its_entity(self, hass):
        router = await start(hass, {QUEUES: [{".id": "*1", "name": "kids", "comment": "children", **QUEUE}]})
        before = router.registry_ids()

        router.menus[QUEUES] = [{".id": "*1", "name": "the kids", "comment": "children", **QUEUE}]
        await router.poll(2)

        assert router.registry_ids() == before
        assert set(router.entities()) == {"queue-children"}

    async def test_a_queue_that_loses_its_comment_goes_by_its_name_again(self, hass):
        """Without waiting for a restart, which used to change the id and drop the entity."""
        router = await start(hass, {QUEUES: [{".id": "*1", "name": "kids", "comment": "children", **QUEUE}]})
        entity_id = router.entity_id("switch", "queue", "children")

        router.menus[QUEUES] = [{".id": "*1", "name": "kids", **QUEUE}]
        await router.poll(2)

        assert router.coordinator.ds["queue"]["*1"]["uniq-id"] == "kids"
        assert router.entities() == {"queue-kids": entity_id}

    async def test_a_renamed_queue_without_a_comment_keeps_its_old_entity_on_its_row(self, hass):
        """The name is all that is known of it, so nothing is moved."""
        router = await start(hass, {QUEUES: [{".id": "*1", "name": "kids", **QUEUE}]})
        before = router.registry_ids()

        router.menus[QUEUES] = [{".id": "*1", "name": "children", **QUEUE}]
        await router.poll(2)

        after = router.registry_ids()
        assert {entity_id: after[entity_id] for entity_id in before} == before
        assert router.entities()["queue-kids"] in before

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

    def test_the_contents_of_a_routing_rule_leave_the_comment_out(self):
        rule = {"comment": "guests", "action": "lookup", "src-address": "10.0.0.0/24", "dst-address": "any", "routing-mark": "any", "interface": "any", "legacy-uniq-id": "guests,lookup,10.0.0.0/24,any,any,any"}
        assert MikrotikCoordinator._rule_contents("routing_rules", rule) == MikrotikCoordinator._rule_contents(
            "routing_rules", {**rule, "comment": "visitors", "legacy-uniq-id": "visitors,lookup,10.0.0.0/24,any,any,any"}
        )
        assert MikrotikCoordinator._rule_contents("routing_rules", rule) != MikrotikCoordinator._rule_contents("routing_rules", {**rule, "src-address": "10.0.9.0/24"})
        assert MikrotikCoordinator._rule_contents("filter", rule) == rule["legacy-uniq-id"]

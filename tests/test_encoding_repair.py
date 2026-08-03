"""Tests for detecting and fixing entity ids built from misdecoded text."""

from unittest.mock import MagicMock, patch

from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.mikrotik_extended.const import DOMAIN
from custom_components.mikrotik_extended.encoding_repair import (
    apply_renames,
    collect_renames,
    format_rename_list,
)
from custom_components.mikrotik_extended.repairs import (
    EncodingEntityIdRepairFlow,
    async_create_fix_flow,
)

# "Тестовый фильтр" as the router hands it over before decoding
RAW = "Òåñòîâûé ôèëüòð"
DECODED = "Тестовый фильтр"
OLD_SLUG = "oanoiaue_oeeuod"
NEW_SLUG = "testovyi_filtr"

TEXT_FIELDS = {"filter": ("comment",)}


def _entry(hass):
    entry = MockConfigEntry(domain=DOMAIN, data={}, unique_id="192.168.88.1")
    entry.add_to_hass(hass)
    return entry


def _stores():
    return {"filter": {"f1": {"comment": DECODED, "comment-raw": RAW}}}


async def test_collect_finds_untouched_entity(hass):
    """An id ending with the misdecoded slug is offered for renaming."""
    entry = _entry(hass)
    registry = er.async_get(hass)
    created = registry.async_get_or_create("switch", DOMAIN, f"{entry.entry_id}-filter-x", config_entry=entry, suggested_object_id=f"mikrotik_filter_{OLD_SLUG}")

    renames = collect_renames(hass, entry.entry_id, _stores(), TEXT_FIELDS)
    assert len(renames) == 1
    assert renames[0]["entity_id"] == created.entity_id
    assert renames[0]["new_entity_id"].endswith(NEW_SLUG)
    assert renames[0]["has_custom_name"] is False


async def test_collect_skips_user_renamed_entity(hass):
    """An id the user changed no longer matches, so it is left alone."""
    entry = _entry(hass)
    registry = er.async_get(hass)
    registry.async_get_or_create("switch", DOMAIN, f"{entry.entry_id}-filter-x", config_entry=entry, suggested_object_id="my_own_name")
    assert collect_renames(hass, entry.entry_id, _stores(), TEXT_FIELDS) == []


async def test_collect_flags_custom_name(hass):
    """A custom display name is reported so the dialog can mention it."""
    entry = _entry(hass)
    registry = er.async_get(hass)
    created = registry.async_get_or_create("switch", DOMAIN, f"{entry.entry_id}-filter-x", config_entry=entry, suggested_object_id=f"mikrotik_filter_{OLD_SLUG}")
    registry.async_update_entity(created.entity_id, name="My rule")
    renames = collect_renames(hass, entry.entry_id, _stores(), TEXT_FIELDS)
    assert renames[0]["has_custom_name"] is True


async def test_collect_ignores_correctly_decoded_data(hass):
    """Without a stored raw value there is nothing to fix."""
    entry = _entry(hass)
    registry = er.async_get(hass)
    registry.async_get_or_create("switch", DOMAIN, f"{entry.entry_id}-filter-x", config_entry=entry, suggested_object_id=f"mikrotik_filter_{OLD_SLUG}")
    stores = {"filter": {"f1": {"comment": DECODED}}}
    assert collect_renames(hass, entry.entry_id, stores, TEXT_FIELDS) == []


async def test_apply_renames_moves_entity_and_keeps_custom_name(hass):
    """The rename keeps the registry entry, including the user's own name."""
    entry = _entry(hass)
    registry = er.async_get(hass)
    created = registry.async_get_or_create("switch", DOMAIN, f"{entry.entry_id}-filter-x", config_entry=entry, suggested_object_id=f"mikrotik_filter_{OLD_SLUG}")
    registry.async_update_entity(created.entity_id, name="My rule")
    renames = collect_renames(hass, entry.entry_id, _stores(), TEXT_FIELDS)

    applied, skipped = apply_renames(hass, renames)
    assert len(applied) == 1 and skipped == []
    moved = registry.async_get(applied[0]["new_entity_id"])
    assert moved is not None
    assert moved.name == "My rule"
    assert registry.async_get(created.entity_id) is None


async def test_apply_renames_skips_taken_target(hass):
    """An existing entity is never overwritten."""
    entry = _entry(hass)
    registry = er.async_get(hass)
    registry.async_get_or_create("switch", DOMAIN, f"{entry.entry_id}-filter-x", config_entry=entry, suggested_object_id=f"mikrotik_filter_{OLD_SLUG}")
    renames = collect_renames(hass, entry.entry_id, _stores(), TEXT_FIELDS)
    blocker = registry.async_get_or_create(
        "switch",
        DOMAIN,
        f"{entry.entry_id}-filter-other",
        config_entry=entry,
        suggested_object_id=renames[0]["new_entity_id"].split(".", 1)[1],
    )
    applied, skipped = apply_renames(hass, renames)
    assert applied == []
    assert skipped[0]["reason"] == "taken"
    assert registry.async_get(blocker.entity_id) is not None


async def test_apply_renames_skips_missing_entity(hass):
    """An entity removed in the meantime is reported, not recreated."""
    _entry(hass)
    renames = [{"entity_id": "switch.gone", "new_entity_id": "switch.new", "has_custom_name": False}]
    applied, skipped = apply_renames(hass, renames)
    assert applied == []
    assert skipped[0]["reason"] == "missing"


def test_format_rename_list_marks_custom_names_and_truncates():
    items = [{"entity_id": f"switch.a{i}", "new_entity_id": f"switch.b{i}", "has_custom_name": i == 0} for i in range(25)]
    text = format_rename_list(items, limit=3)
    assert "has a custom name" in text
    assert "and 22 more" in text


async def test_repair_flow_shows_form_then_renames(hass):
    """The flow shows the pairs first and only renames after confirmation."""
    entry = _entry(hass)
    registry = er.async_get(hass)
    created = registry.async_get_or_create("switch", DOMAIN, f"{entry.entry_id}-filter-x", config_entry=entry, suggested_object_id=f"mikrotik_filter_{OLD_SLUG}")
    coordinator = MagicMock()
    coordinator.ds = _stores()
    coordinator._TEXT_FIELDS = TEXT_FIELDS
    entry.runtime_data = MagicMock(data_coordinator=coordinator)

    flow = await async_create_fix_flow(hass, "encoding_entity_ids_x", {"entry_id": entry.entry_id})
    assert isinstance(flow, EncodingEntityIdRepairFlow)
    flow.hass = hass

    result = await flow.async_step_init()
    assert result["type"] == "form"
    assert result["description_placeholders"]["count"] == "1"
    assert OLD_SLUG in result["description_placeholders"]["renames"]
    # nothing renamed yet
    assert registry.async_get(created.entity_id) is not None

    result = await flow.async_step_confirm({})
    assert result["type"] == "create_entry"
    assert registry.async_get(created.entity_id) is None


async def test_repair_flow_without_coordinator_is_noop(hass):
    """A missing coordinator ends the flow instead of raising."""
    entry = _entry(hass)
    flow = await async_create_fix_flow(hass, "encoding_entity_ids_x", {"entry_id": entry.entry_id})
    flow.hass = hass
    result = await flow.async_step_init()
    assert result["type"] == "create_entry"


async def test_coordinator_creates_and_clears_the_issue(hass):
    """The coordinator raises the repair only while a mismatch exists."""
    from tests.test_coordinator import _make_coordinator

    coord = _make_coordinator(hass)
    registry = er.async_get(hass)
    registry.async_get_or_create(
        "switch",
        DOMAIN,
        f"{coord.config_entry.entry_id}-filter-x",
        config_entry=coord.config_entry,
        suggested_object_id=f"mikrotik_filter_{OLD_SLUG}",
    )
    coord.ds["filter"] = {"f1": {"comment": DECODED, "comment-raw": RAW}}

    with (
        patch("custom_components.mikrotik_extended.coordinator.async_create_issue") as create,
        patch("custom_components.mikrotik_extended.coordinator.IssueSeverity", MagicMock()),
    ):
        coord._check_encoding_entity_ids()
    create.assert_called_once()
    assert create.call_args.kwargs["is_fixable"] is True
    assert create.call_args.kwargs["translation_placeholders"]["count"] == "1"

    coord.ds["filter"] = {"f1": {"comment": DECODED}}
    with (
        patch("custom_components.mikrotik_extended.coordinator.async_create_issue", MagicMock()),
        patch("custom_components.mikrotik_extended.coordinator.async_delete_issue") as delete,
    ):
        coord._check_encoding_entity_ids()
    delete.assert_called_once()


async def test_collect_matches_slug_before_a_type_suffix(hass):
    """Netwatch style ids carry a type suffix after the text, and must still match."""
    entry = _entry(hass)
    registry = er.async_get(hass)
    created = registry.async_get_or_create(
        "binary_sensor",
        DOMAIN,
        f"{entry.entry_id}-netwatch-x",
        config_entry=entry,
        suggested_object_id=f"mikrotik_router_netwatch_{OLD_SLUG}_netwatch",
    )
    stores = {"netwatch": {"n1": {"comment": DECODED, "comment-raw": RAW}}}

    renames = collect_renames(hass, entry.entry_id, stores, {"netwatch": ("comment",)})
    assert len(renames) == 1
    assert renames[0]["entity_id"] == created.entity_id
    assert renames[0]["new_entity_id"].endswith(f"{NEW_SLUG}_netwatch")


async def test_collect_does_not_match_inside_a_word(hass):
    """A slug embedded in a longer word is not a boundary match."""
    entry = _entry(hass)
    registry = er.async_get(hass)
    registry.async_get_or_create(
        "switch",
        DOMAIN,
        f"{entry.entry_id}-filter-x",
        config_entry=entry,
        suggested_object_id=f"mikrotik_filter_prefix{OLD_SLUG}suffix",
    )
    assert collect_renames(hass, entry.entry_id, _stores(), TEXT_FIELDS) == []

"""Find and fix entity ids that were built from misdecoded router text.

When a router stores comments or host names in a legacy codepage and the
matching Text encoding option is only set later, the data and the entity name
recover on the next update, but the entity id does not: Home Assistant assigns
it once, at registration time, and never revisits it.

The detection below is deliberately narrow. An entity is only offered for
renaming when its id still contains the slug built from the *misdecoded* text,
matched on a word boundary, which means the user has never touched that id.
Anything renamed by the user no longer matches and is therefore left alone.
"""

from __future__ import annotations

from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from homeassistant.util import slugify

# Suffix used by the coordinator to keep the undecoded value next to the
# decoded one, so the previously generated slug can be reconstructed.
RAW_SUFFIX = "-raw"


def _replace_slug(object_id: str, old: str, new: str) -> str | None:
    """Swap ``old`` for ``new`` in an object id, but only on a word boundary.

    The misdecoded text is not always at the end: entity names often carry a
    type suffix, as in ``..._netwatch_<slug>_netwatch``. Matching on boundaries
    keeps the replacement from firing inside an unrelated word.
    """
    if object_id == old:
        return new
    if object_id.startswith(f"{old}_"):
        return f"{new}{object_id[len(old) :]}"
    if object_id.endswith(f"_{old}"):
        return f"{object_id[: -len(old)]}{new}"
    middle = f"_{old}_"
    if middle in object_id:
        return object_id.replace(middle, f"_{new}_", 1)
    return None


def collect_renames(hass: HomeAssistant, entry_id: str, stores: dict, text_fields: dict) -> list[dict]:
    """Return the entities whose id still carries misdecoded text.

    Each item holds the current id, the id it would get, and whether the user
    gave the entity a name of their own, so the confirmation dialog can show it.
    """
    registry = er.async_get(hass)
    entities = er.async_entries_for_config_entry(registry, entry_id)
    renames: list[dict] = []
    handled: set[str] = set()

    for store_name, fields in text_fields.items():
        store = stores.get(store_name) or {}
        if not isinstance(store, dict):
            continue
        for entry in store.values():
            if not isinstance(entry, dict):
                continue
            for field in fields:
                raw = entry.get(f"{field}{RAW_SUFFIX}")
                decoded = entry.get(field)
                if not isinstance(raw, str) or not isinstance(decoded, str) or raw == decoded:
                    continue
                old_slug = slugify(raw.lower())
                new_slug = slugify(decoded.lower())
                if not old_slug or not new_slug or old_slug == new_slug:
                    continue

                for entity in entities:
                    if entity.entity_id in handled:
                        continue
                    domain, _, object_id = entity.entity_id.partition(".")
                    replaced = _replace_slug(object_id, old_slug, new_slug)
                    if replaced is None:
                        continue
                    new_entity_id = f"{domain}.{replaced}"
                    if new_entity_id == entity.entity_id:
                        continue
                    handled.add(entity.entity_id)
                    renames.append(
                        {
                            "entity_id": entity.entity_id,
                            "new_entity_id": new_entity_id,
                            "has_custom_name": bool(entity.name),
                        }
                    )

    return sorted(renames, key=lambda item: item["entity_id"])


def apply_renames(hass: HomeAssistant, renames: list[dict]) -> tuple[list[dict], list[dict]]:
    """Rename the given entities, returning the applied and the skipped ones.

    An entity is skipped when it disappeared in the meantime or when the target
    id is already taken, so an existing entity is never overwritten.
    """
    registry = er.async_get(hass)
    applied: list[dict] = []
    skipped: list[dict] = []

    for item in renames:
        current = registry.async_get(item["entity_id"])
        if current is None:
            skipped.append({**item, "reason": "missing"})
            continue
        if registry.async_get(item["new_entity_id"]) is not None:
            skipped.append({**item, "reason": "taken"})
            continue
        registry.async_update_entity(item["entity_id"], new_entity_id=item["new_entity_id"])
        applied.append(item)

    return applied, skipped


def format_rename_list(renames: list[dict], limit: int = 20) -> str:
    """Render the rename pairs as a markdown list for the confirmation dialog."""
    lines = []
    for item in renames[:limit]:
        marker = " (has a custom name)" if item["has_custom_name"] else ""
        lines.append(f"- `{item['entity_id']}`\n  to `{item['new_entity_id']}`{marker}")
    if len(renames) > limit:
        lines.append(f"- and {len(renames) - limit} more")
    return "\n".join(lines)

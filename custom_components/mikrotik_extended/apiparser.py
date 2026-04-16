"""API parser for JSON APIs."""

from datetime import UTC, datetime
from logging import DEBUG, getLogger

from homeassistant.components.diagnostics import async_redact_data

from .const import TO_REDACT

_LOGGER = getLogger(__name__)

_MISSING = object()


def _resolve_entry_value(entry, param):
    """Return the value at path ``param`` (supports ``a/b/c``), or _MISSING."""
    if "/" in param:
        for tmp_param in param.split("/"):
            if isinstance(entry, dict) and tmp_param in entry:
                entry = entry[tmp_param]
            else:
                return _MISSING
        return entry
    if param in entry:
        return entry[param]
    return _MISSING


def _coerce_typed(ret):
    """Coerce ret to its matching str/int/float form."""
    if isinstance(ret, str):
        return str(ret)
    if isinstance(ret, int):
        return int(ret)
    if isinstance(ret, float):
        return round(float(ret), 2)
    return ret


# ---------------------------
#   utc_from_timestamp
# ---------------------------
def utc_from_timestamp(timestamp: float) -> datetime:
    """Return a UTC time from a timestamp."""
    return datetime.fromtimestamp(timestamp, tz=UTC)


# ---------------------------
#   from_entry
# ---------------------------
def from_entry(entry, param, default="") -> str:
    """Validate and return str value an API dict."""
    ret = _resolve_entry_value(entry, param)
    if ret is _MISSING:
        return default

    if default != "":
        ret = _coerce_typed(ret)

    return ret[:255] if isinstance(ret, str) and len(ret) > 255 else ret


# ---------------------------
#   from_entry_bool
# ---------------------------
def from_entry_bool(entry, param, default=False, reverse=False) -> bool:
    """Validate and return a bool value from an API dict."""
    ret = _resolve_entry_value(entry, param)
    if ret is _MISSING:
        return not default if reverse else default

    if isinstance(ret, str):
        lowered = ret.lower()
        if lowered in ("on", "yes", "up"):
            ret = True
        elif lowered in ("off", "no", "down"):
            ret = False

    if not isinstance(ret, bool):
        ret = default

    return not ret if reverse else ret


# ---------------------------
#   parse_api
# ---------------------------
def parse_api(
    data=None,
    source=None,
    key=None,
    key_secondary=None,
    key_search=None,
    vals=None,
    val_proc=None,
    ensure_vals=None,
    only=None,
    skip=None,
    prune_stale=False,
    stale_counters=None,
) -> dict:
    """Get data from API."""
    if data is None:
        data = {}

    debug = _LOGGER.getEffectiveLevel() == DEBUG
    if isinstance(source, dict):
        source = [source]

    if not source:
        if not key and not key_search:
            data = fill_defaults(data, vals)
        return data

    if debug:
        _LOGGER.debug("Processing source %s", async_redact_data(source, TO_REDACT))

    seen_uids = set()
    keymap = generate_keymap(data, key_search)
    for entry in source:
        if only and not matches_only(entry, only):
            continue

        if skip and can_skip(entry, skip):
            continue

        uid = _resolve_entry_uid(entry, data, seen_uids, key, key_secondary, key_search, keymap)
        if (key or key_search) and not uid:
            continue

        if debug:
            _LOGGER.debug("Processing entry %s", async_redact_data(entry, TO_REDACT))

        _apply_entry_fills(data, entry, uid, vals, ensure_vals, val_proc)

    if prune_stale and stale_counters is not None and (key or key_search):
        _prune_stale_entries(data, seen_uids, stale_counters)

    return data


def _resolve_entry_uid(entry, data, seen_uids, key, key_secondary, key_search, keymap):
    """Get uid for ``entry``, update seen_uids, and ensure data[uid] exists."""
    if not (key or key_search):
        return None
    uid = get_uid(entry, key, key_secondary, key_search, keymap)
    if not uid:
        return None
    seen_uids.add(uid)
    if uid not in data:
        data[uid] = {}
    return uid


def _apply_entry_fills(data, entry, uid, vals, ensure_vals, val_proc) -> None:
    """Apply vals / ensure_vals / val_proc fills for a single entry."""
    if vals:
        fill_vals(data, entry, uid, vals)
    if ensure_vals:
        fill_ensure_vals(data, uid, ensure_vals)
    if val_proc:
        fill_vals_proc(data, uid, val_proc)


def _prune_stale_entries(data, seen_uids, stale_counters) -> None:
    """Drop entries missing from ``seen_uids`` after three strikes."""
    for uid in list(data):
        if uid not in seen_uids:
            stale_counters[uid] = stale_counters.get(uid, 0) + 1
            if stale_counters[uid] >= 3:
                del data[uid]
                del stale_counters[uid]
        else:
            stale_counters.pop(uid, None)


# ---------------------------
#   get_uid
# ---------------------------
def _uid_from_primary_or_secondary(entry, key, key_secondary) -> str | None:
    """Resolve uid from primary key, falling back to the secondary key."""
    key_primary_found = key in entry
    if key_primary_found and not entry[key]:
        return None

    if key_primary_found:
        return entry[key]
    if key_secondary:
        if key_secondary not in entry:
            return None
        if not entry[key_secondary]:
            return None
        return entry[key_secondary]
    return None


def _uid_from_keymap(entry, key_search, keymap) -> str | None:
    """Resolve uid via the pre-computed keymap."""
    if keymap and key_search in entry and entry[key_search] in keymap:
        return keymap[entry[key_search]]
    return None


def get_uid(entry, key, key_secondary, key_search, keymap) -> str | None:
    """Get UID for data list."""
    if not key_search:
        uid = _uid_from_primary_or_secondary(entry, key, key_secondary)
    else:
        uid = _uid_from_keymap(entry, key_search, keymap)
    return uid or None


# ---------------------------
#   generate_keymap
# ---------------------------
def generate_keymap(data, key_search) -> dict | None:
    """Generate keymap."""
    return {data[uid][key_search]: uid for uid in data if key_search in data[uid]} if key_search else None


# ---------------------------
#   matches_only
# ---------------------------
def matches_only(entry, only) -> bool:
    """Return True if all variables are matched."""
    ret = False
    for val in only:
        if val["key"] in entry and entry[val["key"]] == val["value"]:
            ret = True
        else:
            ret = False
            break

    return ret


# ---------------------------
#   can_skip
# ---------------------------
def can_skip(entry, skip) -> bool:
    """Return True if at least one variable matches."""
    ret = False
    for val in skip:
        if val["name"] in entry and entry[val["name"]] == val["value"]:
            ret = True
            break

        if val["value"] == "" and val["name"] not in entry:
            ret = True
            break

    return ret


# ---------------------------
#   fill_defaults
# ---------------------------
def fill_defaults(data, vals) -> dict:
    """Fill defaults if source is not present."""
    for val in vals:
        _name = val["name"]
        _type = val.get("type", "str")
        _source = val.get("source", _name)

        if _type == "str":
            _default = val.get("default", "")
            if "default_val" in val and val["default_val"] in val:
                _default = val[val["default_val"]]

            if _name not in data:
                data[_name] = from_entry([], _source, default=_default)

        elif _type == "bool":
            _default = val.get("default", False)
            _reverse = val.get("reverse", False)
            if _name not in data:
                data[_name] = from_entry_bool([], _source, default=_default, reverse=_reverse)

    return data


# ---------------------------
#   fill_vals
# ---------------------------
def _resolve_str_default(val):
    """Return the effective string default for a val definition."""
    _default = val.get("default", "")
    if "default_val" in val and val["default_val"] in val:
        _default = val[val["default_val"]]
    return _default


def _assign_target(data, uid, name, value) -> None:
    """Write value under data[uid][name] or data[name] based on uid presence."""
    if uid:
        data[uid][name] = value
    else:
        data[name] = value


def _convert_utc_timestamp(data, uid, name) -> None:
    """Convert int timestamp in data[uid][name] / data[name] to a UTC datetime in place."""
    target = data[uid] if uid else data
    raw = target.get(name)
    if not (isinstance(raw, int) and raw > 0):
        return
    if raw > 100000000000:
        raw = raw / 1000
    target[name] = utc_from_timestamp(raw)


def fill_vals(data, entry, uid, vals) -> dict:
    """Fill all data."""
    for val in vals:
        _name = val["name"]
        _type = val.get("type", "str")
        _source = val.get("source", _name)
        _convert = val.get("convert")

        if _type == "str":
            _assign_target(data, uid, _name, from_entry(entry, _source, default=_resolve_str_default(val)))
        elif _type == "bool":
            _default = val.get("default", False)
            _reverse = val.get("reverse", False)
            _assign_target(data, uid, _name, from_entry_bool(entry, _source, default=_default, reverse=_reverse))

        if _convert == "utc_from_timestamp":
            _convert_utc_timestamp(data, uid, _name)

    return data


# ---------------------------
#   fill_ensure_vals
# ---------------------------
def fill_ensure_vals(data, uid, ensure_vals) -> dict:
    """Add required keys which are not available in data."""
    for val in ensure_vals:
        if uid:
            if val["name"] not in data[uid]:
                _default = val.get("default", "")
                data[uid][val["name"]] = _default

        elif val["name"] not in data:
            _default = val.get("default", "")
            data[val["name"]] = _default

    return data


# ---------------------------
#   fill_vals_proc
# ---------------------------
def _combine_value(current, fragment):
    """Append fragment to current string, starting fresh if current is empty."""
    return f"{current}{fragment}" if current else fragment


def _process_val_sub(val_sub, _data) -> tuple[str | None, str | None]:
    """Reduce a single vals_proc sub-list to (_name, _value)."""
    _name = None
    _action = None
    _value = None
    for val in val_sub:
        if "name" in val:
            _name = val["name"]
            continue

        if "action" in val:
            _action = val["action"]
            continue

        if not _name and not _action:
            break

        if _action == "combine":
            if "key" in val:
                _value = _combine_value(_value, _data.get(val["key"], "unknown"))
            if "text" in val:
                _value = _combine_value(_value, val["text"])

    return _name, _value


def fill_vals_proc(data, uid, vals_proc) -> dict:
    """Add custom keys."""
    _data = data[uid] if uid else data
    for val_sub in vals_proc:
        _name, _value = _process_val_sub(val_sub, _data)
        if _name and _value:
            _assign_target(data, uid, _name, _value)

    return data

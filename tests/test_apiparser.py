"""Tests for apiparser functions."""

from datetime import datetime

from custom_components.mikrotik_extended.apiparser import (
    can_skip,
    fill_defaults,
    fill_ensure_vals,
    fill_vals,
    fill_vals_proc,
    from_entry,
    from_entry_bool,
    generate_keymap,
    get_uid,
    matches_only,
    parse_api,
    utc_from_timestamp,
)

# ---- utc_from_timestamp ----


class TestUtcFromTimestamp:
    def test_returns_datetime(self):
        result = utc_from_timestamp(0)
        assert isinstance(result, datetime)
        assert result.tzinfo is not None

    def test_known_timestamp(self):
        # 2021-01-01 00:00:00 UTC = 1609459200
        result = utc_from_timestamp(1609459200)
        assert result.year == 2021
        assert result.month == 1
        assert result.day == 1


# ---- from_entry ----


class TestFromEntry:
    def test_simple_key(self):
        assert from_entry({"name": "router1"}, "name") == "router1"

    def test_missing_key_returns_default(self):
        assert from_entry({"name": "router1"}, "missing") == ""

    def test_missing_key_custom_default(self):
        assert from_entry({"name": "router1"}, "missing", default="N/A") == "N/A"

    def test_nested_key_with_slash(self):
        assert from_entry({"a": {"b": "val"}}, "a/b") == "val"

    def test_nested_key_missing(self):
        assert from_entry({"a": {"c": "val"}}, "a/b") == ""

    def test_nested_key_non_dict_intermediate(self):
        # When intermediate is not a dict, returns default
        assert from_entry({"a": "not_a_dict"}, "a/b", default="x") == "x"

    def test_int_value(self):
        assert from_entry({"cpu": 42}, "cpu", default=0) == 42

    def test_float_value_rounded(self):
        result = from_entry({"temp": 55.678}, "temp", default=0.0)
        assert result == 55.68

    def test_str_value_with_default_coerced(self):
        # Covers line 42 - str branch where ret is already str but default != ""
        result = from_entry({"val": "hello"}, "val", default="x")
        assert result == "hello"

    def test_long_string_truncated_to_255(self):
        long_str = "x" * 300
        result = from_entry({"val": long_str}, "val", default="")
        assert len(result) == 255

    def test_string_exactly_255_not_truncated(self):
        s = "x" * 255
        result = from_entry({"val": s}, "val", default="")
        assert len(result) == 255

    def test_empty_default_no_type_coercion(self):
        """When default is empty string, no type coercion happens."""
        result = from_entry({"val": [1, 2]}, "val")
        assert result == [1, 2]


# ---- from_entry_bool ----


class TestFromEntryBool:
    def test_true_value(self):
        assert from_entry_bool({"enabled": True}, "enabled") is True

    def test_false_value(self):
        assert from_entry_bool({"enabled": False}, "enabled") is False

    def test_string_yes(self):
        assert from_entry_bool({"status": "yes"}, "status") is True

    def test_string_no(self):
        assert from_entry_bool({"status": "no"}, "status") is False

    def test_string_on(self):
        assert from_entry_bool({"status": "on"}, "status") is True

    def test_string_off(self):
        assert from_entry_bool({"status": "off"}, "status") is False

    def test_string_up(self):
        assert from_entry_bool({"status": "up"}, "status") is True

    def test_string_down(self):
        assert from_entry_bool({"status": "down"}, "status") is False

    def test_missing_key_returns_default(self):
        assert from_entry_bool({"x": True}, "missing") is False

    def test_missing_key_custom_default(self):
        assert from_entry_bool({"x": True}, "missing", default=True) is True

    def test_reverse_true_becomes_false(self):
        assert from_entry_bool({"enabled": True}, "enabled", reverse=True) is False

    def test_reverse_false_becomes_true(self):
        assert from_entry_bool({"enabled": False}, "enabled", reverse=True) is True

    def test_reverse_missing_reverses_default(self):
        assert from_entry_bool({}, "missing", default=False, reverse=True) is True

    def test_nested_key_with_slash(self):
        assert from_entry_bool({"a": {"b": True}}, "a/b") is True

    def test_nested_key_missing(self):
        assert from_entry_bool({"a": {"c": True}}, "a/b") is False

    def test_nested_non_dict_intermediate(self):
        assert from_entry_bool({"a": "not_a_dict"}, "a/b") is False

    def test_non_bool_non_string_returns_default(self):
        assert from_entry_bool({"val": 42}, "val") is False


# ---- parse_api ----


class TestParseApi:
    def test_empty_source_returns_data(self):
        result = parse_api(data={"existing": "val"}, source=None, key=".id")
        assert result == {"existing": "val"}

    def test_none_data_creates_empty_dict(self):
        result = parse_api(data=None, source=None, key=".id")
        assert result == {}

    def test_empty_source_no_key_with_vals_fills_defaults(self):
        """Covers line 108: fill_defaults called when no source, no key."""
        result = parse_api(
            data={},
            source=None,
            vals=[{"name": "cpu", "default": "0"}],
        )
        assert result == {"cpu": "0"}

    def test_single_dict_source_wrapped(self):
        """A single dict source is treated as [dict]."""
        source = {"name": "eth0", ".id": "*1"}
        result = parse_api(
            data={},
            source=source,
            key=".id",
            vals=[{"name": "name", "source": "name"}],
        )
        assert "*1" in result
        assert result["*1"]["name"] == "eth0"

    def test_list_source_with_key(self):
        source = [
            {"name": "eth0", ".id": "*1"},
            {"name": "eth1", ".id": "*2"},
        ]
        result = parse_api(
            data={},
            source=source,
            key=".id",
            vals=[{"name": "name", "source": "name"}],
        )
        assert result["*1"]["name"] == "eth0"
        assert result["*2"]["name"] == "eth1"

    def test_source_without_key_fills_flat(self):
        source = [{"cpu": "50", "memory": "75"}]
        result = parse_api(
            data={},
            source=source,
            vals=[
                {"name": "cpu", "source": "cpu"},
                {"name": "memory", "source": "memory"},
            ],
        )
        assert result["cpu"] == "50"
        assert result["memory"] == "75"

    def test_only_filter(self):
        source = [
            {"name": "eth0", ".id": "*1", "type": "ether"},
            {"name": "wlan0", ".id": "*2", "type": "wlan"},
        ]
        result = parse_api(
            data={},
            source=source,
            key=".id",
            vals=[{"name": "name", "source": "name"}],
            only=[{"key": "type", "value": "ether"}],
        )
        assert "*1" in result
        assert "*2" not in result

    def test_skip_filter(self):
        source = [
            {"name": "eth0", ".id": "*1", "type": "ether"},
            {"name": "loopback", ".id": "*2", "type": "loopback"},
        ]
        result = parse_api(
            data={},
            source=source,
            key=".id",
            vals=[{"name": "name", "source": "name"}],
            skip=[{"name": "type", "value": "loopback"}],
        )
        assert "*1" in result
        assert "*2" not in result

    def test_ensure_vals_adds_missing_keys(self):
        source = [{"name": "eth0", ".id": "*1"}]
        result = parse_api(
            data={},
            source=source,
            key=".id",
            vals=[{"name": "name", "source": "name"}],
            ensure_vals=[{"name": "tx_bytes", "default": 0}],
        )
        assert result["*1"]["tx_bytes"] == 0
        assert result["*1"]["name"] == "eth0"

    def test_key_secondary_fallback(self):
        source = [{"alt_id": "backup1", "name": "backup"}]
        result = parse_api(
            data={},
            source=source,
            key=".id",
            key_secondary="alt_id",
            vals=[{"name": "name", "source": "name"}],
        )
        assert "backup1" in result

    def test_empty_source_list(self):
        result = parse_api(data={}, source=[], key=".id")
        assert result == {}

    def test_bool_val_type(self):
        source = [{"name": "eth0", ".id": "*1", "enabled": True}]
        result = parse_api(
            data={},
            source=source,
            key=".id",
            vals=[{"name": "enabled", "source": "enabled", "type": "bool"}],
        )
        assert result["*1"]["enabled"] is True

    def test_val_proc_uses_fill_vals_proc(self):
        """Covers line 144: val_proc triggers fill_vals_proc."""
        source = [{"name": "eth0", ".id": "*1"}]
        result = parse_api(
            data={},
            source=source,
            key=".id",
            vals=[{"name": "name", "source": "name"}],
            val_proc=[
                [
                    {"name": "description"},
                    {"action": "combine"},
                    {"key": "name"},
                    {"text": "_desc"},
                ],
            ],
        )
        assert result["*1"]["description"] == "eth0_desc"

    def test_key_search_finds_existing(self):
        """Covers key_search path in get_uid."""
        data = {"*1": {"name": "eth0"}}
        source = [{"name": "eth0", "mac": "AA:BB"}]
        result = parse_api(
            data=data,
            source=source,
            key_search="name",
            vals=[{"name": "mac", "source": "mac"}],
        )
        assert result["*1"]["mac"] == "AA:BB"

    def test_key_search_no_match_skipped(self):
        """key_search entry not in keymap returns None from get_uid -> continue."""
        data = {"*1": {"name": "eth0"}}
        source = [{"name": "eth99", "mac": "AA:BB"}]
        result = parse_api(
            data=data,
            source=source,
            key_search="name",
            vals=[{"name": "mac", "source": "mac"}],
        )
        # eth99 not in keymap -> skipped; *1 unchanged
        assert result["*1"] == {"name": "eth0"}

    def test_key_empty_value_skipped(self):
        """Covers line 168: entry[key] is falsy returns None."""
        source = [{"name": "eth0", ".id": ""}, {"name": "eth1", ".id": "*2"}]
        result = parse_api(
            data={},
            source=source,
            key=".id",
            vals=[{"name": "name", "source": "name"}],
        )
        assert "*2" in result
        assert len(result) == 1

    def test_key_missing_and_no_secondary_skipped(self):
        """Covers line 127: no uid returned, continue."""
        source = [{"name": "eth0"}]  # no .id
        result = parse_api(
            data={},
            source=source,
            key=".id",
            vals=[{"name": "name", "source": "name"}],
        )
        assert result == {}

    def test_key_secondary_missing_returns_none(self):
        """Covers line 174: key_secondary not in entry."""
        source = [{"name": "eth0"}]  # no .id, no alt_id
        result = parse_api(
            data={},
            source=source,
            key=".id",
            key_secondary="alt_id",
            vals=[{"name": "name", "source": "name"}],
        )
        assert result == {}

    def test_key_secondary_empty_returns_none(self):
        """Covers line 177: key_secondary value is falsy."""
        source = [{"name": "eth0", "alt_id": ""}]
        result = parse_api(
            data={},
            source=source,
            key=".id",
            key_secondary="alt_id",
            vals=[{"name": "name", "source": "name"}],
        )
        assert result == {}


# ---- get_uid direct tests ----


class TestGetUid:
    def test_key_primary_present(self):
        assert get_uid({".id": "*1"}, ".id", None, None, None) == "*1"

    def test_key_primary_empty_returns_none(self):
        assert get_uid({".id": ""}, ".id", None, None, None) is None

    def test_key_secondary_used(self):
        assert get_uid({"alt": "backup"}, ".id", "alt", None, None) == "backup"

    def test_key_secondary_missing(self):
        assert get_uid({}, ".id", "alt", None, None) is None

    def test_key_secondary_empty(self):
        assert get_uid({"alt": ""}, ".id", "alt", None, None) is None

    def test_no_key_primary_no_secondary_returns_none(self):
        """Covers line 180-183 fallthrough: returns None because no match."""
        assert get_uid({"name": "eth0"}, ".id", None, None, None) is None

    def test_key_search_match(self):
        keymap = {"eth0": "*1"}
        assert get_uid({"name": "eth0"}, None, None, "name", keymap) == "*1"

    def test_key_search_no_match_returns_none(self):
        """Covers line 183: else branch returns None."""
        keymap = {"eth0": "*1"}
        assert get_uid({"name": "eth99"}, None, None, "name", keymap) is None

    def test_key_search_empty_keymap(self):
        assert get_uid({"name": "eth0"}, None, None, "name", {}) is None


# ---- prune_stale ----


class TestPruneStale:
    def test_stale_not_pruned_before_3_polls(self):
        """Items missing from source should survive 2 polls."""
        data = {"*1": {"name": "eth0"}, "*2": {"name": "eth1"}}
        counters = {}

        source = [{"name": "eth0", ".id": "*1"}]
        data = parse_api(
            data=data,
            source=source,
            key=".id",
            vals=[{"name": "name", "source": "name"}],
            prune_stale=True,
            stale_counters=counters,
        )
        assert "*2" in data
        assert counters["*2"] == 1

        data = parse_api(
            data=data,
            source=source,
            key=".id",
            vals=[{"name": "name", "source": "name"}],
            prune_stale=True,
            stale_counters=counters,
        )
        assert "*2" in data
        assert counters["*2"] == 2

    def test_stale_pruned_after_3_polls(self):
        """Items missing for 3 consecutive polls should be removed."""
        data = {"*1": {"name": "eth0"}, "*2": {"name": "eth1"}}
        counters = {}
        source = [{"name": "eth0", ".id": "*1"}]

        for _ in range(3):
            data = parse_api(
                data=data,
                source=source,
                key=".id",
                vals=[{"name": "name", "source": "name"}],
                prune_stale=True,
                stale_counters=counters,
            )

        assert "*1" in data
        assert "*2" not in data
        assert "*2" not in counters

    def test_stale_counter_reset_when_seen(self):
        data = {"*1": {"name": "eth0"}, "*2": {"name": "eth1"}}
        counters = {}

        source1 = [{"name": "eth0", ".id": "*1"}]
        data = parse_api(
            data=data,
            source=source1,
            key=".id",
            vals=[{"name": "name", "source": "name"}],
            prune_stale=True,
            stale_counters=counters,
        )
        assert counters["*2"] == 1

        source2 = [
            {"name": "eth0", ".id": "*1"},
            {"name": "eth1", ".id": "*2"},
        ]
        data = parse_api(
            data=data,
            source=source2,
            key=".id",
            vals=[{"name": "name", "source": "name"}],
            prune_stale=True,
            stale_counters=counters,
        )
        assert "*2" not in counters

    def test_prune_disabled_by_default(self):
        data = {"*1": {"name": "eth0"}, "*2": {"name": "eth1"}}
        source = [{"name": "eth0", ".id": "*1"}]

        for _ in range(5):
            data = parse_api(
                data=data,
                source=source,
                key=".id",
                vals=[{"name": "name", "source": "name"}],
            )

        assert "*2" in data

    def test_prune_without_counters_dict_noop(self):
        data = {"*1": {"name": "eth0"}, "*2": {"name": "eth1"}}
        source = [{"name": "eth0", ".id": "*1"}]

        for _ in range(5):
            data = parse_api(
                data=data,
                source=source,
                key=".id",
                vals=[{"name": "name", "source": "name"}],
                prune_stale=True,
                stale_counters=None,
            )

        assert "*2" in data

    def test_prune_without_key_noop(self):
        data = {"cpu": "50"}
        counters = {}
        source = [{"cpu": "60"}]

        data = parse_api(
            data=data,
            source=source,
            vals=[{"name": "cpu", "source": "cpu"}],
            prune_stale=True,
            stale_counters=counters,
        )
        assert "cpu" in data


# ---- matches_only / can_skip ----


class TestFilters:
    def test_matches_only_all_match(self):
        entry = {"type": "ether", "running": "yes"}
        only = [{"key": "type", "value": "ether"}, {"key": "running", "value": "yes"}]
        assert matches_only(entry, only) is True

    def test_matches_only_partial_match(self):
        entry = {"type": "ether", "running": "no"}
        only = [{"key": "type", "value": "ether"}, {"key": "running", "value": "yes"}]
        assert matches_only(entry, only) is False

    def test_matches_only_missing_key(self):
        entry = {"type": "ether"}
        only = [{"key": "type", "value": "ether"}, {"key": "running", "value": "yes"}]
        assert matches_only(entry, only) is False

    def test_can_skip_matching(self):
        entry = {"type": "loopback"}
        skip = [{"name": "type", "value": "loopback"}]
        assert can_skip(entry, skip) is True

    def test_can_skip_no_match(self):
        entry = {"type": "ether"}
        skip = [{"name": "type", "value": "loopback"}]
        assert can_skip(entry, skip) is False

    def test_can_skip_missing_key_empty_value(self):
        """Skip when key is absent and value is empty string."""
        entry = {"name": "eth0"}
        skip = [{"name": "comment", "value": ""}]
        assert can_skip(entry, skip) is True


# ---- generate_keymap ----


class TestGenerateKeymap:
    def test_generates_reverse_map(self):
        data = {
            "*1": {"name": "eth0"},
            "*2": {"name": "eth1"},
        }
        keymap = generate_keymap(data, "name")
        assert keymap == {"eth0": "*1", "eth1": "*2"}

    def test_no_key_search_returns_none(self):
        assert generate_keymap({"*1": {}}, None) is None

    def test_missing_key_in_entry_skipped(self):
        data = {
            "*1": {"name": "eth0"},
            "*2": {},
        }
        keymap = generate_keymap(data, "name")
        assert keymap == {"eth0": "*1"}


# ---- fill_defaults ----


class TestFillDefaults:
    def test_fills_str_default(self):
        data = {}
        vals = [{"name": "cpu", "default": "0"}]
        result = fill_defaults(data, vals)
        assert result["cpu"] == "0"

    def test_fills_str_default_val_override(self):
        """Covers default_val branch in fill_defaults."""
        data = {}
        vals = [{"name": "cpu", "default": "x", "default_val": "fallback", "fallback": "99"}]
        result = fill_defaults(data, vals)
        assert result["cpu"] == "99"

    def test_fills_bool_default(self):
        data = {}
        vals = [{"name": "enabled", "type": "bool", "default": False}]
        result = fill_defaults(data, vals)
        assert result["enabled"] is False

    def test_fills_bool_default_reverse(self):
        data = {}
        vals = [{"name": "enabled", "type": "bool", "default": False, "reverse": True}]
        result = fill_defaults(data, vals)
        assert result["enabled"] is True

    def test_does_not_overwrite_existing(self):
        data = {"cpu": "50"}
        vals = [{"name": "cpu", "default": "0"}]
        result = fill_defaults(data, vals)
        assert result["cpu"] == "50"

    def test_does_not_overwrite_existing_bool(self):
        data = {"enabled": True}
        vals = [{"name": "enabled", "type": "bool", "default": False}]
        result = fill_defaults(data, vals)
        assert result["enabled"] is True


# ---- fill_vals ----


class TestFillVals:
    def test_fill_vals_str_default_val_override(self):
        """Covers line 271: default_val override inside fill_vals."""
        data = {"*1": {}}
        entry = {"name": "eth0", "real_default": "override"}
        vals = [
            {
                "name": "mode",
                "source": "mode",
                "default": "x",
                "default_val": "real_default",
                "real_default": "override",
            }
        ]
        result = fill_vals(data, entry, "*1", vals)
        assert result["*1"]["mode"] == "override"

    def test_fill_vals_bool_no_uid_flat(self):
        """Covers line 285: bool flat (no uid)."""
        data = {}
        entry = {"enabled": True}
        vals = [{"name": "enabled", "source": "enabled", "type": "bool"}]
        result = fill_vals(data, entry, None, vals)
        assert result["enabled"] is True

    def test_fill_vals_utc_from_timestamp_with_uid(self):
        """Covers lines 288-293: utc_from_timestamp convert with uid."""
        data = {"*1": {}}
        entry = {"ts": 1609459200}
        vals = [
            {
                "name": "ts",
                "source": "ts",
                "default": 0,
                "convert": "utc_from_timestamp",
            }
        ]
        result = fill_vals(data, entry, "*1", vals)
        assert isinstance(result["*1"]["ts"], datetime)

    def test_fill_vals_utc_from_timestamp_millis_with_uid(self):
        """Covers line 290-291: millisecond division branch with uid."""
        data = {"*1": {}}
        entry = {"ts": 1609459200000}  # millis
        vals = [
            {
                "name": "ts",
                "source": "ts",
                "default": 0,
                "convert": "utc_from_timestamp",
            }
        ]
        result = fill_vals(data, entry, "*1", vals)
        assert isinstance(result["*1"]["ts"], datetime)
        assert result["*1"]["ts"].year == 2021

    def test_fill_vals_utc_from_timestamp_no_uid(self):
        """Covers lines 294-298: utc_from_timestamp flat (no uid)."""
        data = {}
        entry = {"ts": 1609459200}
        vals = [
            {
                "name": "ts",
                "source": "ts",
                "default": 0,
                "convert": "utc_from_timestamp",
            }
        ]
        result = fill_vals(data, entry, None, vals)
        assert isinstance(result["ts"], datetime)

    def test_fill_vals_utc_from_timestamp_millis_no_uid(self):
        """Covers lines 295-297: millisecond division branch flat."""
        data = {}
        entry = {"ts": 1609459200000}
        vals = [
            {
                "name": "ts",
                "source": "ts",
                "default": 0,
                "convert": "utc_from_timestamp",
            }
        ]
        result = fill_vals(data, entry, None, vals)
        assert isinstance(result["ts"], datetime)
        assert result["ts"].year == 2021

    def test_fill_vals_utc_zero_noop(self):
        """Zero timestamp should not be converted."""
        data = {"*1": {}}
        entry = {"ts": 0}
        vals = [
            {
                "name": "ts",
                "source": "ts",
                "default": 0,
                "convert": "utc_from_timestamp",
            }
        ]
        result = fill_vals(data, entry, "*1", vals)
        assert result["*1"]["ts"] == 0


# ---- fill_ensure_vals ----


class TestFillEnsureVals:
    def test_adds_missing_with_uid(self):
        data = {"*1": {"name": "eth0"}}
        ensure_vals = [{"name": "tx", "default": 0}]
        result = fill_ensure_vals(data, "*1", ensure_vals)
        assert result["*1"]["tx"] == 0

    def test_adds_missing_without_uid(self):
        """Covers lines 314-316: no uid branch."""
        data = {"existing": "val"}
        ensure_vals = [{"name": "new_key", "default": "new_val"}]
        result = fill_ensure_vals(data, None, ensure_vals)
        assert result["new_key"] == "new_val"

    def test_adds_missing_without_uid_default_empty(self):
        """Covers line 315: default not provided branch (empty str)."""
        data = {}
        ensure_vals = [{"name": "new_key"}]
        result = fill_ensure_vals(data, None, ensure_vals)
        assert result["new_key"] == ""

    def test_does_not_overwrite_existing_with_uid(self):
        data = {"*1": {"tx": 100}}
        ensure_vals = [{"name": "tx", "default": 0}]
        result = fill_ensure_vals(data, "*1", ensure_vals)
        assert result["*1"]["tx"] == 100

    def test_does_not_overwrite_existing_without_uid(self):
        data = {"existing": "val"}
        ensure_vals = [{"name": "existing", "default": "new"}]
        result = fill_ensure_vals(data, None, ensure_vals)
        assert result["existing"] == "val"


# ---- fill_vals_proc ----


class TestFillValsProc:
    def test_combine_with_key_and_text_uid(self):
        """Covers lines 326-358: fill_vals_proc with uid."""
        data = {"*1": {"name": "eth0"}}
        vals_proc = [
            [
                {"name": "description"},
                {"action": "combine"},
                {"key": "name"},
                {"text": "_desc"},
            ]
        ]
        result = fill_vals_proc(data, "*1", vals_proc)
        assert result["*1"]["description"] == "eth0_desc"

    def test_combine_without_uid(self):
        """Covers no-uid branch line 326 and 355-356."""
        data = {"name": "eth0"}
        vals_proc = [
            [
                {"name": "description"},
                {"action": "combine"},
                {"key": "name"},
                {"text": "_tag"},
            ]
        ]
        result = fill_vals_proc(data, None, vals_proc)
        assert result["description"] == "eth0_tag"

    def test_combine_missing_key_uses_unknown(self):
        """Key not in data falls back to 'unknown'."""
        data = {"*1": {}}
        vals_proc = [
            [
                {"name": "description"},
                {"action": "combine"},
                {"key": "missing_key"},
            ]
        ]
        result = fill_vals_proc(data, "*1", vals_proc)
        assert result["*1"]["description"] == "unknown"

    def test_skips_early_when_no_name_no_action(self):
        """If neither name nor action set, break out of inner loop."""
        data = {"*1": {}}
        vals_proc = [
            [
                {"key": "anything"},  # no name, no action -> break
            ]
        ]
        result = fill_vals_proc(data, "*1", vals_proc)
        # description is never set, nothing added
        assert result == {"*1": {}}

    def test_no_value_produced_no_assignment(self):
        """If _value remains None, no key is set on data."""
        data = {"*1": {}}
        vals_proc = [
            [
                {"name": "description"},
                {"action": "combine"},
                # no key, no text
            ]
        ]
        result = fill_vals_proc(data, "*1", vals_proc)
        assert "description" not in result["*1"]

    def test_combine_multiple_parts(self):
        data = {"*1": {"first": "hello", "second": "world"}}
        vals_proc = [
            [
                {"name": "full"},
                {"action": "combine"},
                {"key": "first"},
                {"text": "-"},
                {"key": "second"},
            ]
        ]
        result = fill_vals_proc(data, "*1", vals_proc)
        assert result["*1"]["full"] == "hello-world"

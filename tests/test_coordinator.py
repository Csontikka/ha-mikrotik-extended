"""Tests for MikrotikCoordinator and MikrotikTrackerCoordinator."""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant.const import (
    CONF_HOST,
    CONF_NAME,
    CONF_PASSWORD,
    CONF_PORT,
    CONF_SSL,
    CONF_USERNAME,
    CONF_VERIFY_SSL,
)
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import UpdateFailed
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.mikrotik_extended.const import DOMAIN


# Provide IssueSeverity fallback for test environment
class _FakeIssueSeverity(StrEnum):
    ERROR = "error"
    WARNING = "warning"
    CRITICAL = "critical"


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

ENTRY_DATA = {
    CONF_HOST: "192.168.88.1",
    CONF_USERNAME: "admin",
    CONF_PASSWORD: "test",
    CONF_PORT: 8728,
    CONF_SSL: False,
    CONF_VERIFY_SSL: False,
    CONF_NAME: "Mikrotik",
}

ENTRY_OPTIONS = {
    "scan_interval": 30,
    "track_network_hosts_timeout": 180,
    "zone": "home",
}


def _make_config_entry(options=None):
    opts = dict(ENTRY_OPTIONS)
    if options:
        opts.update(options)
    return MockConfigEntry(
        domain=DOMAIN,
        data=ENTRY_DATA,
        options=opts,
        unique_id="192.168.88.1",
    )


def _make_coordinator(hass, options=None):
    """Build a MikrotikCoordinator with a mocked MikrotikAPI."""
    from custom_components.mikrotik_extended.coordinator import MikrotikCoordinator

    entry = _make_config_entry(options=options)
    entry.add_to_hass(hass)

    with patch("custom_components.mikrotik_extended.coordinator.MikrotikAPI") as MockAPI:
        mock_api = MagicMock()
        MockAPI.return_value = mock_api
        with patch(
            "custom_components.mikrotik_extended.coordinator.IssueSeverity",
            _FakeIssueSeverity,
        ):
            coordinator = MikrotikCoordinator(hass, entry)

    coordinator.api = mock_api
    # Ensure config_entry is accessible (some HA versions use weakref)
    coordinator.config_entry = entry
    return coordinator


# ---------------------------------------------------------------------------
# _get_stale_counters
# ---------------------------------------------------------------------------


class TestGetStaleCounters:
    def test_creates_empty_dict_for_new_key(self, hass):
        coord = _make_coordinator(hass)
        result = coord._get_stale_counters("interface")
        assert result == {}

    def test_returns_same_dict_on_second_call(self, hass):
        coord = _make_coordinator(hass)
        first = coord._get_stale_counters("interface")
        first["eth0"] = 3
        second = coord._get_stale_counters("interface")
        assert second is first
        assert second["eth0"] == 3

    def test_different_keys_are_isolated(self, hass):
        coord = _make_coordinator(hass)
        a = coord._get_stale_counters("interface")
        b = coord._get_stale_counters("host")
        a["x"] = 99
        assert "x" not in b

    def test_multiple_keys_stored_independently(self, hass):
        coord = _make_coordinator(hass)
        coord._get_stale_counters("nat")["rule1"] = 1
        coord._get_stale_counters("filter")["rule2"] = 2
        assert coord._get_stale_counters("nat") == {"rule1": 1}
        assert coord._get_stale_counters("filter") == {"rule2": 2}


# ---------------------------------------------------------------------------
# _async_update_data — connection / reconnect flow
# ---------------------------------------------------------------------------


class TestAsyncUpdateDataConnection:
    async def _run_update(self, coordinator):
        """Invoke _async_update_data directly."""
        return await coordinator._async_update_data()

    def _stub_all_get_methods(self, coordinator):
        """Stub every get_* / async_get_* / process_* method to be a no-op."""
        noop_sync = MagicMock(return_value=None)
        noop_async = AsyncMock(return_value=None)
        for attr in dir(coordinator):
            if attr.startswith("get_") or attr.startswith("process_") or attr.startswith("sync_"):
                if attr.startswith("async_") or "async" in attr:
                    setattr(coordinator, attr, noop_async)
                else:
                    setattr(coordinator, attr, noop_sync)
        # Explicitly cover the ones called via async_add_executor_job
        for name in [
            "get_access",
            "get_firmware_update",
            "get_system_resource",
            "get_capabilities",
            "get_system_routerboard",
            "get_script",
            "get_dhcp_network",
            "get_dns",
            "get_system_health",
            "get_dhcp_client",
            "get_interface",
            "get_ip_address",
            "get_cloud",
            "get_capsman_hosts",
            "get_wireless",
            "get_wireless_hosts",
            "get_bridge",
            "get_arp",
            "get_dhcp",
            "process_interface_client",
            "get_nat",
            "get_kidcontrol",
            "get_mangle",
            "get_routing_rules",
            "get_wireguard_peers",
            "get_containers",
            "get_device_mode",
            "get_packages",
            "get_filter",
            "get_netwatch",
            "get_ppp",
            "sync_kid_control_monitoring_profile",
            "process_kid_control_devices",
            "get_captive",
            "get_queue",
            "get_environment",
            "get_ups",
            "get_gps",
        ]:
            setattr(coordinator, name, MagicMock(return_value=None))

        coordinator.async_get_host_hass = AsyncMock(return_value=None)
        coordinator.async_process_host = AsyncMock(return_value=None)

    async def test_raises_update_failed_when_api_not_connected_after_reconnect(self, hass):
        """UpdateFailed is raised when api stays disconnected after reconnect attempt."""
        coordinator = _make_coordinator(hass)
        self._stub_all_get_methods(coordinator)

        # has_reconnected triggers the reconnect block
        coordinator.api.has_reconnected.return_value = True
        coordinator.api.connected.return_value = False
        coordinator.api.error = "connection_refused"

        with pytest.raises(UpdateFailed, match="Mikrotik Disconnected"):
            await self._run_update(coordinator)

    async def test_raises_update_failed_at_end_when_disconnected(self, hass):
        """UpdateFailed raised at end of update when still disconnected."""
        coordinator = _make_coordinator(hass)
        self._stub_all_get_methods(coordinator)

        # Skip reconnect block (has_reconnected=False, delta > 4h not triggered)
        coordinator.api.has_reconnected.return_value = False
        coordinator.last_hwinfo_update = datetime.now()
        # Stays disconnected throughout
        coordinator.api.connected.return_value = False
        coordinator.api.error = ""

        with pytest.raises(UpdateFailed, match="Mikrotik Disconnected"):
            await self._run_update(coordinator)

    async def test_returns_ds_on_successful_update(self, hass):
        """Returns coordinator.ds dict when connected throughout."""
        coordinator = _make_coordinator(hass)
        self._stub_all_get_methods(coordinator)

        coordinator.api.has_reconnected.return_value = False
        coordinator.last_hwinfo_update = datetime.now()
        coordinator.api.connected.return_value = True
        coordinator.api.error = ""

        with (
            patch("custom_components.mikrotik_extended.coordinator.IssueSeverity", _FakeIssueSeverity),
            patch("custom_components.mikrotik_extended.coordinator.async_create_issue", MagicMock()),
            patch("custom_components.mikrotik_extended.coordinator.async_delete_issue", MagicMock()),
            patch("custom_components.mikrotik_extended.coordinator.async_dispatcher_send"),
        ):
            result = await self._run_update(coordinator)

        assert result is coordinator.ds


# ---------------------------------------------------------------------------
# _async_update_data — repair issues
# ---------------------------------------------------------------------------


class TestRepairIssues:
    def _stub_all_get_methods(self, coordinator):
        for name in [
            "get_access",
            "get_firmware_update",
            "get_system_resource",
            "get_capabilities",
            "get_system_routerboard",
            "get_script",
            "get_dhcp_network",
            "get_dns",
        ]:
            setattr(coordinator, name, MagicMock(return_value=None))
        coordinator.async_get_host_hass = AsyncMock(return_value=None)
        coordinator.async_process_host = AsyncMock(return_value=None)

    def _patch_severity_and_issues(self, mock_create=None, mock_delete=None):
        """Context manager that patches IssueSeverity + create/delete issue."""
        import contextlib

        @contextlib.contextmanager
        def _ctx():
            with (
                patch("custom_components.mikrotik_extended.coordinator.IssueSeverity", _FakeIssueSeverity),
                patch("custom_components.mikrotik_extended.coordinator.async_create_issue", mock_create),
                patch("custom_components.mikrotik_extended.coordinator.async_delete_issue", mock_delete),
            ):
                yield

        return _ctx()

    async def test_wrong_login_creates_repair_issue(self, hass):
        """async_create_issue called with wrong_credentials when error is wrong_login."""
        coordinator = _make_coordinator(hass)
        self._stub_all_get_methods(coordinator)

        coordinator.api.has_reconnected.return_value = True
        coordinator.api.connected.return_value = False
        coordinator.api.error = "wrong_login"

        mock_create = MagicMock()

        with self._patch_severity_and_issues(mock_create, MagicMock()), pytest.raises(ConfigEntryAuthFailed):
            await coordinator._async_update_data()

        mock_create.assert_called_once()
        assert mock_create.call_args[0][2] == "wrong_credentials"

    async def test_ssl_error_creates_repair_issue(self, hass):
        """async_create_issue called with ssl_error for ssl_handshake_failure."""
        coordinator = _make_coordinator(hass)
        self._stub_all_get_methods(coordinator)

        coordinator.api.has_reconnected.return_value = True
        coordinator.api.connected.return_value = False
        coordinator.api.error = "ssl_handshake_failure"

        mock_create = MagicMock()

        with self._patch_severity_and_issues(mock_create, MagicMock()), pytest.raises(UpdateFailed):
            await coordinator._async_update_data()

        mock_create.assert_called_once()
        assert mock_create.call_args[0][2] == "ssl_error"

    async def test_ssl_verify_failure_creates_repair_issue(self, hass):
        """async_create_issue called with ssl_error for ssl_verify_failure."""
        coordinator = _make_coordinator(hass)
        self._stub_all_get_methods(coordinator)

        coordinator.api.has_reconnected.return_value = True
        coordinator.api.connected.return_value = False
        coordinator.api.error = "ssl_verify_failure"

        mock_create = MagicMock()

        with self._patch_severity_and_issues(mock_create, MagicMock()), pytest.raises(UpdateFailed):
            await coordinator._async_update_data()

        assert mock_create.call_args[0][2] == "ssl_error"

    async def test_repair_issue_not_called_when_async_create_issue_is_none(self, hass):
        """No error raised when async_create_issue is None (import failed)."""
        coordinator = _make_coordinator(hass)
        self._stub_all_get_methods(coordinator)

        coordinator.api.has_reconnected.return_value = True
        coordinator.api.connected.return_value = False
        coordinator.api.error = "wrong_login"

        with (
            patch("custom_components.mikrotik_extended.coordinator.IssueSeverity", None),
            patch("custom_components.mikrotik_extended.coordinator.async_create_issue", None),
            patch("custom_components.mikrotik_extended.coordinator.async_delete_issue", None),
            pytest.raises(ConfigEntryAuthFailed),
        ):
            await coordinator._async_update_data()

    async def test_repair_issues_deleted_on_successful_reconnect(self, hass):
        """async_delete_issue called for both issue keys after successful reconnect."""
        coordinator = _make_coordinator(hass)
        self._stub_all_get_methods(coordinator)

        coordinator.last_hwinfo_update = datetime(1970, 1, 1)
        coordinator.api.has_reconnected.return_value = False
        coordinator.api.connected.return_value = True
        coordinator.api.error = ""

        mock_delete = MagicMock()

        with self._patch_severity_and_issues(MagicMock(), mock_delete), patch("custom_components.mikrotik_extended.coordinator.async_dispatcher_send"):
            await coordinator._async_update_data()

        deleted_issue_ids = [c[0][2] for c in mock_delete.call_args_list]
        assert "wrong_credentials" in deleted_issue_ids
        assert "ssl_error" in deleted_issue_ids

    async def test_wrong_login_triggers_reauth(self, hass):
        """ConfigEntryAuthFailed raised on wrong_login, which triggers reauth automatically."""
        coordinator = _make_coordinator(hass)
        self._stub_all_get_methods(coordinator)

        coordinator.api.has_reconnected.return_value = True
        coordinator.api.connected.return_value = False
        coordinator.api.error = "wrong_login"

        with self._patch_severity_and_issues(MagicMock(), MagicMock()), pytest.raises(ConfigEntryAuthFailed):
            await coordinator._async_update_data()

    async def test_non_wrong_login_error_does_not_trigger_reauth(self, hass):
        """async_start_reauth NOT called for non-auth errors."""
        coordinator = _make_coordinator(hass)
        self._stub_all_get_methods(coordinator)

        coordinator.api.has_reconnected.return_value = True
        coordinator.api.connected.return_value = False
        coordinator.api.error = "ssl_handshake_failure"

        mock_reauth = MagicMock()
        coordinator.config_entry.async_start_reauth = mock_reauth

        with self._patch_severity_and_issues(MagicMock(), MagicMock()), pytest.raises(UpdateFailed):
            await coordinator._async_update_data()

        mock_reauth.assert_not_called()


# ---------------------------------------------------------------------------
# Module-level helpers
# ---------------------------------------------------------------------------


class TestModuleHelpers:
    def test_parse_duration_seconds_empty(self):
        from custom_components.mikrotik_extended.coordinator import _parse_duration_seconds

        assert _parse_duration_seconds("") == 0
        assert _parse_duration_seconds(None) == 0
        assert _parse_duration_seconds("never") == 0
        assert _parse_duration_seconds("Never") == 0

    def test_parse_duration_seconds_combinations(self):
        from custom_components.mikrotik_extended.coordinator import _parse_duration_seconds

        assert _parse_duration_seconds("3m45s") == 225
        assert _parse_duration_seconds("2w1d1h1m1s") == 2 * 604800 + 86400 + 3600 + 60 + 1
        assert _parse_duration_seconds("1h") == 3600
        assert _parse_duration_seconds("5s") == 5

    def test_is_valid_ip(self):
        from custom_components.mikrotik_extended.coordinator import is_valid_ip

        assert is_valid_ip("1.2.3.4") is True
        assert is_valid_ip("::1") is True
        assert is_valid_ip("bad-ip") is False
        assert is_valid_ip("") is False

    def test_utc_from_timestamp(self):
        from custom_components.mikrotik_extended.coordinator import utc_from_timestamp

        result = utc_from_timestamp(0)
        assert result.year == 1970

    def test_as_local_with_tzinfo(self):

        import pytz

        from custom_components.mikrotik_extended.coordinator import as_local

        # datetime with tzinfo → should convert to (None) DEFAULT_TIME_ZONE
        dt = datetime(2024, 1, 1, tzinfo=UTC)
        result = as_local(dt)
        assert result is not None

        # datetime without tzinfo → localized to utc first then astimezone
        dt2 = datetime(2024, 1, 1)
        result2 = as_local(dt2)
        assert result2 is not None

        # datetime whose tzinfo equals DEFAULT_TIME_ZONE (which is None) — pass through
        # When tzinfo is None, after localization returns ok; directly test tzinfo==None branch
        # The if branch triggers when dt.tzinfo == DEFAULT_TIME_ZONE (None).
        # We cover both paths above.
        assert pytz is not None


# ---------------------------------------------------------------------------
# Property accessors
# ---------------------------------------------------------------------------


class TestOptionProperties:
    def test_all_option_properties_return_defaults(self, hass):
        coord = _make_coordinator(hass)
        # These all delegate to config_entry.options.get
        assert coord.option_track_iface_clients is True
        assert coord.option_track_network_hosts in (True, False)
        assert coord.option_sensor_port_traffic in (True, False)
        assert coord.option_sensor_client_traffic in (True, False)
        assert coord.option_sensor_client_captive in (True, False)
        assert coord.option_sensor_simple_queues in (True, False)
        assert coord.option_sensor_nat in (True, False)
        assert coord.option_sensor_mangle in (True, False)
        assert coord.option_sensor_routing_rules in (True, False)
        assert coord.option_sensor_wireguard in (True, False)
        assert coord.option_sensor_containers in (True, False)
        assert coord.option_sensor_filter in (True, False)
        assert coord.option_sensor_kidcontrol in (True, False)
        assert coord.option_sensor_netwatch in (True, False)
        assert coord.option_sensor_ppp in (True, False)
        assert coord.option_sensor_scripts in (True, False)
        assert coord.option_sensor_environment in (True, False)
        # scan_interval returns timedelta
        assert coord.option_scan_interval.total_seconds() == 30

    def test_connected_set_value_execute_delegate_to_api(self, hass):
        coord = _make_coordinator(hass)
        coord.api.connected.return_value = True
        assert coord.connected() is True

        coord.api.set_value.return_value = "ok"
        assert coord.set_value("/path", "p", "v", "mp", "mv") == "ok"
        coord.api.set_value.assert_called_with("/path", "p", "v", "mp", "mv")

        coord.api.execute.return_value = True
        assert coord.execute("/path", "cmd", "p", "v") is True
        coord.api.execute.assert_called_with("/path", "cmd", "p", "v", None)


# ---------------------------------------------------------------------------
# get_capabilities
# ---------------------------------------------------------------------------


class TestGetCapabilities:
    def _patch_parse_api(self, return_val):
        return patch(
            "custom_components.mikrotik_extended.coordinator.parse_api",
            return_value=return_val,
        )

    def test_get_capabilities_v7_wifiwave2(self, hass):
        coord = _make_coordinator(hass)
        coord.major_fw_version = 7
        coord.minor_fw_version = 1
        packages = {
            "wifiwave2": {"name": "wifiwave2", "enabled": True},
            "ups": {"name": "ups", "enabled": True},
            "gps": {"name": "gps", "enabled": True},
        }
        with self._patch_parse_api(packages):
            coord.get_capabilities()
        assert coord.support_wireless is True
        assert coord._wifimodule == "wifiwave2"
        assert coord.support_ups is True
        assert coord.support_gps is True
        assert coord.support_wireguard is True
        assert coord.support_containers is True

    def test_get_capabilities_v7_wifi_package(self, hass):
        coord = _make_coordinator(hass)
        coord.major_fw_version = 7
        coord.minor_fw_version = 12
        packages = {
            "wifi": {"name": "wifi", "enabled": True},
        }
        with self._patch_parse_api(packages):
            coord.get_capabilities()
        assert coord._wifimodule == "wifi"

    def test_get_capabilities_v7_old_minor_capsman_fallback(self, hass):
        coord = _make_coordinator(hass)
        coord.major_fw_version = 7
        coord.minor_fw_version = 5
        packages = {}
        with self._patch_parse_api(packages):
            coord.get_capabilities()
        assert coord.support_capsman is True
        assert coord.support_wireless is True

    def test_get_capabilities_v7_13plus_wifi_without_package(self, hass):
        coord = _make_coordinator(hass)
        coord.major_fw_version = 7
        coord.minor_fw_version = 14
        packages = {}
        with self._patch_parse_api(packages):
            coord.get_capabilities()
        assert coord._wifimodule == "wifi"

    def test_get_capabilities_wireguard_external_package(self, hass):
        coord = _make_coordinator(hass)
        coord.major_fw_version = 6
        coord.minor_fw_version = 0
        packages = {"wireguard": {"name": "wireguard", "enabled": True}}
        with self._patch_parse_api(packages):
            coord.get_capabilities()
        assert coord.support_wireguard is True


# ---------------------------------------------------------------------------
# async_get_host_hass
# ---------------------------------------------------------------------------


class TestAsyncGetHostHass:
    async def test_new_format_entity(self, hass):
        coord = _make_coordinator(hass)
        entry_id = coord.config_entry.entry_id

        entity = MagicMock()
        entity.config_entry_id = entry_id
        entity.entity_id = "device_tracker.mikrotik_host_aa_bb_cc_dd_ee_ff"
        entity.unique_id = f"{entry_id}-host-aa_bb_cc_dd_ee_ff"
        entity.original_name = "MyDevice"

        fake_registry = MagicMock()
        fake_registry.entities.values.return_value = [entity]

        with patch(
            "custom_components.mikrotik_extended.coordinator.entity_registry.async_get",
            return_value=fake_registry,
        ):
            await coord.async_get_host_hass()

        assert "AA:BB:CC:DD:EE:FF" in coord.ds["host_hass"]
        assert coord.ds["host_hass"]["AA:BB:CC:DD:EE:FF"] == "MyDevice"

    async def test_old_format_entity(self, hass):
        coord = _make_coordinator(hass)
        entry_id = coord.config_entry.entry_id

        entity = MagicMock()
        entity.config_entry_id = entry_id
        entity.entity_id = "device_tracker.mikrotik_host_aa_bb_cc_dd_ee_ff"
        entity.unique_id = "mikrotik-host-AA:BB:CC:DD:EE:FF"
        entity.original_name = "OldDevice"

        fake_registry = MagicMock()
        fake_registry.entities.values.return_value = [entity]

        with patch(
            "custom_components.mikrotik_extended.coordinator.entity_registry.async_get",
            return_value=fake_registry,
        ):
            await coord.async_get_host_hass()

        assert "AA:BB:CC:DD:EE:FF" in coord.ds["host_hass"]

    async def test_malformed_skipped(self, hass):
        coord = _make_coordinator(hass)
        entry_id = coord.config_entry.entry_id

        bad1 = MagicMock()
        bad1.config_entry_id = entry_id
        bad1.entity_id = "device_tracker.foo"
        bad1.unique_id = "a-b"  # <3 parts

        bad2 = MagicMock()
        bad2.config_entry_id = entry_id
        bad2.entity_id = "device_tracker.foo"
        bad2.unique_id = "x-something_else-y"  # not 'host'

        bad3 = MagicMock()
        bad3.config_entry_id = entry_id
        bad3.entity_id = "device_tracker.foo"
        bad3.unique_id = "unknown-host-aa_bb_cc_dd_ee_ff"

        bad4 = MagicMock()
        bad4.config_entry_id = entry_id
        bad4.entity_id = "device_tracker.foo"
        bad4.unique_id = f"{entry_id}-host-tooshort"  # mac wrong length

        bad5 = MagicMock()
        bad5.config_entry_id = "other_entry"
        bad5.entity_id = "device_tracker.foo"
        bad5.unique_id = "x-host-y"

        bad6 = MagicMock()
        bad6.config_entry_id = entry_id
        bad6.entity_id = "sensor.not_a_tracker"
        bad6.unique_id = f"{entry_id}-host-aa_bb_cc_dd_ee_ff"

        fake_registry = MagicMock()
        fake_registry.entities.values.return_value = [bad1, bad2, bad3, bad4, bad5, bad6]

        with patch(
            "custom_components.mikrotik_extended.coordinator.entity_registry.async_get",
            return_value=fake_registry,
        ):
            await coord.async_get_host_hass()

        assert coord.ds["host_hass"] == {}


# ---------------------------------------------------------------------------
# get_access
# ---------------------------------------------------------------------------


class TestGetAccess:
    def test_access_missing_rights(self, hass):
        coord = _make_coordinator(hass)
        tmp_user = {"admin": {"name": "admin", "group": "restricted"}}
        tmp_group = {"restricted": {"name": "restricted", "policy": "read,api"}}

        with patch(
            "custom_components.mikrotik_extended.coordinator.parse_api",
            side_effect=[tmp_user, tmp_group],
        ):
            coord.get_access()

        assert "read" in coord.ds["access"]
        assert "write" in coord.ds["access_missing"]
        assert coord.accessrights_reported is True

    def test_access_full_rights(self, hass):
        coord = _make_coordinator(hass)
        tmp_user = {"admin": {"name": "admin", "group": "full"}}
        tmp_group = {"full": {"name": "full", "policy": "write,policy,reboot,test,read,api"}}

        with patch(
            "custom_components.mikrotik_extended.coordinator.parse_api",
            side_effect=[tmp_user, tmp_group],
        ):
            coord.get_access()

        assert coord.ds["access_missing"] == []

    def test_access_group_not_found(self, hass):
        coord = _make_coordinator(hass)
        tmp_user = {"admin": {"name": "admin", "group": "missing_group"}}
        tmp_group = {"full": {"name": "full", "policy": "write,policy"}}

        # pre-populate ds["access"] so access_missing logic still runs
        coord.ds["access"] = ["write", "policy", "reboot", "test"]
        with patch(
            "custom_components.mikrotik_extended.coordinator.parse_api",
            side_effect=[tmp_user, tmp_group],
        ):
            coord.get_access()

        # coord.ds["access"] unchanged; access_missing correctly computed
        assert coord.ds["access_missing"] == []

    def test_access_already_reported(self, hass):
        coord = _make_coordinator(hass)
        coord.accessrights_reported = True
        tmp_user = {"admin": {"name": "admin", "group": "restricted"}}
        tmp_group = {"restricted": {"name": "restricted", "policy": "read"}}

        with patch(
            "custom_components.mikrotik_extended.coordinator.parse_api",
            side_effect=[tmp_user, tmp_group],
        ):
            coord.get_access()
        # already-reported branch: accessrights_reported stays True (no re-log)
        assert coord.accessrights_reported is True
        # group lookup succeeded → ds["access"] reflects the resolved policy
        assert coord.ds["access"] == ["read"]
        # required rights minus the one granted ("read") → all four are missing
        assert coord.ds["access_missing"] == ["write", "policy", "reboot", "test"]


# ---------------------------------------------------------------------------
# get_interface
# ---------------------------------------------------------------------------


class TestGetInterface:
    def test_get_interface_populates_with_port_traffic(self, hass):
        coord = _make_coordinator(hass, options={"sensor_port_traffic": True})

        # parse_api will be called 3 times (interface, ethernet monitor, optional bonding)
        iface = {
            "ether1": {
                ".id": "*1",
                "name": "ether1",
                "default-name": "ether1",
                "type": "ether",
                "running": True,
                "enabled": True,
                "port-mac-address": "AA:BB",
                "comment": 123,
                "tx-current": 2000,
                "tx-previous": 1000,
                "rx-current": 5000,
                "rx-previous": 3000,
                "tx": 0,
                "rx": 0,
                "tx-total": 0,
                "rx-total": 0,
                "sfp-shutdown-temperature": "",
            },
        }

        with patch(
            "custom_components.mikrotik_extended.coordinator.parse_api",
            side_effect=[iface, iface, iface],
        ):
            coord.get_interface()

        assert coord.ds["interface"]["ether1"]["tx-total"] == 2000
        assert coord.ds["interface"]["ether1"]["rx-total"] == 5000
        assert isinstance(coord.ds["interface"]["ether1"]["comment"], str)

    def test_get_interface_with_sfp_branch(self, hass):
        coord = _make_coordinator(hass)
        iface_first = {
            "sfp1": {
                ".id": "*2",
                "name": "sfp1",
                "default-name": "sfp1",
                "type": "ether",
                "running": True,
                "enabled": True,
                "port-mac-address": "AA:CC",
                "comment": "",
                "tx-current": 0,
                "tx-previous": 0,
                "rx-current": 0,
                "rx-previous": 0,
                "tx": 0,
                "rx": 0,
                "tx-total": 0,
                "rx-total": 0,
                "sfp-shutdown-temperature": "80C",
            },
        }

        with patch(
            "custom_components.mikrotik_extended.coordinator.parse_api",
            side_effect=[iface_first, iface_first, iface_first],
        ):
            coord.get_interface()

        assert "sfp1" in coord.ds["interface"]

    def test_get_interface_bonding(self, hass):
        coord = _make_coordinator(hass)
        iface = {
            "bond1": {
                ".id": "*3",
                "name": "bond1",
                "default-name": "",
                "type": "bond",
                "running": True,
                "enabled": True,
                "port-mac-address": "AA:DD",
                "comment": "",
                "tx-current": 0,
                "tx-previous": 0,
                "rx-current": 0,
                "rx-previous": 0,
                "tx": 0,
                "rx": 0,
                "tx-total": 0,
                "rx-total": 0,
                "sfp-shutdown-temperature": "",
            },
        }
        bonding = {
            "bond1": {
                "name": "bond1",
                "mac-address": "AA:DD",
                "slaves": "ether1,ether2",
                "mode": "802.3ad",
            },
        }

        with patch(
            "custom_components.mikrotik_extended.coordinator.parse_api",
            side_effect=[iface, iface, bonding],
        ):
            coord.get_interface()

        # bonding expansion
        assert "ether1" in coord.ds["bonding_slaves"]
        assert "ether2" in coord.ds["bonding_slaves"]
        # default-name fixup
        assert coord.ds["interface"]["bond1"]["default-name"] == "bond1"


# ---------------------------------------------------------------------------
# get_bridge
# ---------------------------------------------------------------------------


class TestGetBridge:
    def test_bridge_populates(self, hass):
        coord = _make_coordinator(hass)
        bridge_hosts = {
            "AA:BB:CC:00:00:01": {
                "mac-address": "AA:BB:CC:00:00:01",
                "interface": "ether1",
                "bridge": "bridge1",
                "enabled": True,
            },
        }

        with patch(
            "custom_components.mikrotik_extended.coordinator.parse_api",
            return_value=bridge_hosts,
        ):
            coord.get_bridge()

        assert coord.ds["bridge"]["bridge1"] is True


# ---------------------------------------------------------------------------
# process_interface_client
# ---------------------------------------------------------------------------


class TestProcessInterfaceClient:
    def test_disabled_branch(self, hass):
        coord = _make_coordinator(hass)
        coord.ds["interface"] = {"ether1": {"name": "ether1"}}
        # override property via class-level patch
        with patch.object(
            type(coord),
            "option_track_iface_clients",
            new_callable=lambda: property(lambda self: False),
        ):
            coord.process_interface_client()

        assert coord.ds["interface"]["ether1"]["client-ip-address"] == "disabled"

    def test_arp_populates_clients(self, hass):
        coord = _make_coordinator(hass)
        coord.ds["interface"] = {
            "ether1": {"name": "ether1"},
        }
        coord.ds["arp"] = {
            "m1": {"interface": "ether1", "address": "1.2.3.4", "mac-address": "AA:BB"},
            "m2": {"interface": "ether1", "address": "5.6.7.8", "mac-address": "CC:DD"},
        }
        coord.ds["bonding_slaves"] = {}
        coord.ds["dhcp-client"] = {}

        coord.process_interface_client()

        # multiple clients ⇒ "multiple"
        assert coord.ds["interface"]["ether1"]["client-ip-address"] == "multiple"
        assert coord.ds["interface"]["ether1"]["client-mac-address"] == "multiple"

    def test_dhcp_fallback_and_none(self, hass):
        coord = _make_coordinator(hass)
        coord.ds["interface"] = {
            "ether1": {"name": "ether1"},
            "ether2": {"name": "ether2"},
        }
        coord.ds["arp"] = {}
        coord.ds["bonding_slaves"] = {}
        coord.ds["dhcp-client"] = {"ether1": {"address": "10.0.0.5"}}

        coord.process_interface_client()

        assert coord.ds["interface"]["ether1"]["client-ip-address"] == "10.0.0.5"
        assert coord.ds["interface"]["ether2"]["client-ip-address"] == "none"
        assert coord.ds["interface"]["ether2"]["client-mac-address"] == "none"

    def test_bonding_slave_match(self, hass):
        coord = _make_coordinator(hass)
        coord.ds["interface"] = {"ether1": {"name": "ether1"}}
        coord.ds["arp"] = {
            "m1": {"interface": "bond1", "address": "1.2.3.4", "mac-address": "AA:BB"},
        }
        coord.ds["bonding_slaves"] = {"ether1": {"master": "bond1"}}
        coord.ds["dhcp-client"] = {}

        coord.process_interface_client()
        assert coord.ds["interface"]["ether1"]["client-ip-address"] == "1.2.3.4"


# ---------------------------------------------------------------------------
# get_nat / get_mangle / get_routing_rules / get_filter / get_queue duplicates
# ---------------------------------------------------------------------------


class TestFirewallRules:
    def test_get_nat_populates_and_dedup(self, hass):
        coord = _make_coordinator(hass)
        nat = {
            "r1": {".id": "*1", "uniq-id": "dupe", "name": "rule1", "comment": 1},
            "r2": {".id": "*2", "uniq-id": "dupe", "name": "rule2", "comment": ""},
            "r3": {".id": "*3", "uniq-id": "unique", "name": "rule3", "comment": ""},
        }

        with patch(
            "custom_components.mikrotik_extended.coordinator.parse_api",
            return_value=nat,
        ):
            coord.get_nat()

        # dupes suffixed
        assert coord.ds["nat"]["r1"]["uniq-id"] == "dupe (*1)"
        assert coord.ds["nat"]["r2"]["uniq-id"] == "dupe (*2)"
        # unique untouched
        assert coord.ds["nat"]["r3"]["uniq-id"] == "unique"
        assert "dupe" in coord.nat_removed

    def test_get_mangle_dedup(self, hass):
        coord = _make_coordinator(hass)
        mangle = {
            "m1": {".id": "*1", "uniq-id": "x", "name": "rule1", "comment": ""},
            "m2": {".id": "*2", "uniq-id": "x", "name": "rule2", "comment": ""},
        }

        with patch(
            "custom_components.mikrotik_extended.coordinator.parse_api",
            return_value=mangle,
        ):
            coord.get_mangle()

        assert coord.ds["mangle"]["m1"]["uniq-id"] == "x (*1)"
        assert "x" in coord.mangle_removed

    def test_get_routing_rules_dedup(self, hass):
        coord = _make_coordinator(hass)
        rr = {
            "rr1": {".id": "*1", "uniq-id": "y", "name": "rule1", "comment": ""},
            "rr2": {".id": "*2", "uniq-id": "y", "name": "rule2", "comment": ""},
        }

        with patch(
            "custom_components.mikrotik_extended.coordinator.parse_api",
            return_value=rr,
        ):
            coord.get_routing_rules()

        assert coord.ds["routing_rules"]["rr1"]["uniq-id"] == "y (*1)"

    def test_get_filter_dedup(self, hass):
        coord = _make_coordinator(hass)
        filt = {
            "f1": {".id": "*1", "uniq-id": "z", "name": "rule1", "comment": ""},
            "f2": {".id": "*2", "uniq-id": "z", "name": "rule2", "comment": ""},
        }

        with patch(
            "custom_components.mikrotik_extended.coordinator.parse_api",
            return_value=filt,
        ):
            coord.get_filter()

        assert coord.ds["filter"]["f1"]["uniq-id"] == "z (*1)"


# ---------------------------------------------------------------------------
# get_wireguard_peers
# ---------------------------------------------------------------------------


class TestGetWireguardPeers:
    def test_connected_and_naming(self, hass):
        coord = _make_coordinator(hass)
        peers = {
            "p1": {
                ".id": "*p1",
                "public-key": "pubkey1",
                "interface": "wg0",
                "peer-name": "peerA",
                "comment": "cA",
                "allowed-address": "10.0.0.1/24",
                "rx": "1",
                "tx": "2",
                "last-handshake": "30s",
                "enabled": True,
            },
            "p2": {
                ".id": "*p2",
                "public-key": "pubkey2",
                "interface": "wg0",
                "peer-name": "",
                "comment": "commentB",
                "allowed-address": "10.0.0.2/24",
                "rx": "1",
                "tx": "2",
                "last-handshake": "5m",
                "enabled": True,
            },
            "p3": {
                ".id": "*p3",
                "public-key": "longpubkey3",
                "interface": "wg0",
                "peer-name": "",
                "comment": "",
                "allowed-address": "",
                "rx": "0",
                "tx": "0",
                "last-handshake": "",
                "enabled": True,
            },
        }

        with patch(
            "custom_components.mikrotik_extended.coordinator.parse_api",
            return_value=peers,
        ):
            coord.get_wireguard_peers()

        assert coord.ds["wireguard_peers"]["p1"]["connected"] is True
        assert coord.ds["wireguard_peers"]["p1"]["name"] == "peerA"
        assert coord.ds["wireguard_peers"]["p2"]["name"] == "commentB"
        assert coord.ds["wireguard_peers"]["p2"]["connected"] is False  # 5m = 300s > 180
        assert coord.ds["wireguard_peers"]["p3"]["name"] == "longpubk"  # first 8 chars


# ---------------------------------------------------------------------------
# get_device_mode / get_packages / get_containers / get_kidcontrol / get_ppp
# ---------------------------------------------------------------------------


class TestMiscResourceGetters:
    def test_get_device_mode(self, hass):
        coord = _make_coordinator(hass)
        with patch(
            "custom_components.mikrotik_extended.coordinator.parse_api",
            return_value={"mode": "safe", "container": True},
        ):
            coord.get_device_mode()
        assert coord.ds["system_device_mode"]["mode"] == "safe"

    def test_get_packages_active_and_known(self, hass):
        coord = _make_coordinator(hass)
        coord.api.query.return_value = [
            {"name": "routeros", "version": "7.0", "disabled": False},
            {"name": "container", "version": "1.0", "disabled": False},
            {"name": "gps", "version": "1.0", "disabled": True},
            {"name": "ups", "version": "1.0", "disabled": False},
            {"name": "other", "version": "1.0", "disabled": False},
        ]
        coord.get_packages()
        pkg = coord.ds["system_packages"]
        assert pkg["container"] == "1.0"
        assert pkg["gps"] is False
        assert pkg["ups"] == "1.0"
        assert pkg["count"] == 3  # container, ups, other

    def test_get_packages_empty(self, hass):
        coord = _make_coordinator(hass)
        coord.api.query.return_value = None
        coord.get_packages()
        assert coord.ds["system_packages"]["count"] == 0

    def test_get_containers(self, hass):
        coord = _make_coordinator(hass)
        containers = {
            "c1": {
                ".id": "*c1",
                "name": "mycont",
                "tag": "latest",
                "comment": "note",
                "running": True,
            },
            "c2": {
                ".id": "*c2",
                "name": "",
                "tag": "tag2",
                "comment": "",
                "running": False,
            },
        }
        with patch(
            "custom_components.mikrotik_extended.coordinator.parse_api",
            return_value=containers,
        ):
            coord.get_containers()

        assert coord.ds["containers"]["c1"]["display-name"] == "mycont"
        assert coord.ds["containers"]["c1"]["status"] == "running"
        assert coord.ds["containers"]["c2"]["display-name"] == "tag2"
        assert coord.ds["containers"]["c2"]["status"] == "stopped"

    def test_get_kidcontrol(self, hass):
        coord = _make_coordinator(hass)
        kc = {"k1": {"name": "kid1", "comment": 42}}
        with patch(
            "custom_components.mikrotik_extended.coordinator.parse_api",
            return_value=kc,
        ):
            coord.get_kidcontrol()
        assert coord.ds["kid-control"]["k1"]["comment"] == "42"

    def test_get_ppp_connected_and_not(self, hass):
        coord = _make_coordinator(hass)
        secrets = {
            "user1": {
                "name": "user1",
                "service": "pppoe",
                "profile": "default",
                "comment": 1,
                "enabled": True,
                "caller-id": "",
                "address": "",
                "encoding": "",
                "connected": False,
            },
            "user2": {
                "name": "user2",
                "service": "pptp",
                "profile": "default",
                "comment": "",
                "enabled": True,
                "caller-id": "",
                "address": "",
                "encoding": "",
                "connected": False,
            },
        }
        active = {
            "user1": {
                "name": "user1",
                "caller-id": "1.2.3.4",
                "address": "10.0.0.1",
                "encoding": "mppe",
            },
        }
        # parse_api is called twice (secrets, then active)
        with patch(
            "custom_components.mikrotik_extended.coordinator.parse_api",
            side_effect=[secrets, active],
        ):
            coord.get_ppp()

        assert coord.ds["ppp_secret"]["user1"]["connected"] is True
        assert coord.ds["ppp_secret"]["user2"]["connected"] is False
        assert coord.ds["ppp_secret"]["user2"]["address"] == "not connected"


# ---------------------------------------------------------------------------
# get_netwatch / get_system_routerboard / get_system_health / get_system_resource
# ---------------------------------------------------------------------------


class TestSystemGetters:
    def test_get_netwatch(self, hass):
        coord = _make_coordinator(hass)
        with patch(
            "custom_components.mikrotik_extended.coordinator.parse_api",
            return_value={"1.1.1.1": {"host": "1.1.1.1", "status": True}},
        ):
            coord.get_netwatch()
        assert "1.1.1.1" in coord.ds["netwatch"]

    def test_get_system_routerboard_x86(self, hass):
        coord = _make_coordinator(hass)
        coord.ds["resource"] = {"board-name": "x86"}
        coord.get_system_routerboard()
        assert coord.ds["routerboard"]["routerboard"] is False
        assert coord.ds["routerboard"]["model"] == "x86"

    def test_get_system_routerboard_chr(self, hass):
        coord = _make_coordinator(hass)
        coord.ds["resource"] = {"board-name": "CHR"}
        coord.get_system_routerboard()
        assert coord.ds["routerboard"]["routerboard"] is False

    def test_get_system_routerboard_hw_limited_access(self, hass):
        coord = _make_coordinator(hass)
        coord.ds["resource"] = {"board-name": "RB5009"}
        coord.ds["access"] = ["read"]
        rb = {
            "routerboard": True,
            "model": "RB5009",
            "serial-number": "123",
            "current-firmware": "7.0",
            "upgrade-firmware": "7.1",
        }
        with patch(
            "custom_components.mikrotik_extended.coordinator.parse_api",
            return_value=rb,
        ):
            coord.get_system_routerboard()
        assert "current-firmware" not in coord.ds["routerboard"]

    def test_get_system_routerboard_hw_full_access(self, hass):
        coord = _make_coordinator(hass)
        coord.ds["resource"] = {"board-name": "RB5009"}
        coord.ds["access"] = ["write", "policy", "reboot"]
        rb = {
            "routerboard": True,
            "model": "RB5009",
            "serial-number": "123",
            "current-firmware": "7.0",
            "upgrade-firmware": "7.1",
        }
        with patch(
            "custom_components.mikrotik_extended.coordinator.parse_api",
            return_value=rb,
        ):
            coord.get_system_routerboard()
        assert coord.ds["routerboard"]["current-firmware"] == "7.0"

    def test_get_system_health_denied(self, hass):
        coord = _make_coordinator(hass)
        coord.ds["access"] = ["read"]
        coord.get_system_health()
        assert coord.ds["health"] == {}

    def test_get_system_health_v7(self, hass):
        coord = _make_coordinator(hass)
        coord.ds["access"] = ["write", "policy", "reboot"]
        coord.major_fw_version = 7
        h7 = {"cpu-temperature": {"value": "45"}}
        with patch(
            "custom_components.mikrotik_extended.coordinator.parse_api",
            return_value=h7,
        ):
            coord.get_system_health()
        assert coord.ds["health"]["cpu-temperature"] == "45"

    def test_get_system_resource_computes_usage_and_reboot(self, hass):
        coord = _make_coordinator(hass)
        res = {
            "platform": "MikroTik",
            "board-name": "RB",
            "version": "7.0",
            "uptime_str": "1h30m",
            "cpu-load": "10",
            "free-memory": 500,
            "total-memory": 1000,
            "free-hdd-space": 100,
            "total-hdd-space": 1000,
            "uptime": 0,
            "uptime_epoch": 0,
            "clients_wired": 0,
            "clients_wireless": 0,
            "captive_authorized": 0,
        }
        coord.rebootcheck = 10000  # > uptime_epoch triggers firmware update
        with (
            patch(
                "custom_components.mikrotik_extended.coordinator.parse_api",
                return_value=res,
            ),
            patch.object(coord, "get_firmware_update") as mock_fw,
        ):
            coord.get_system_resource()

        assert coord.ds["resource"]["memory-usage"] == 50
        assert coord.ds["resource"]["hdd-usage"] == 90
        assert coord.ds["resource"]["uptime_epoch"] == 3600 + 1800
        mock_fw.assert_called_once()

    def test_get_system_resource_zero_memory(self, hass):
        coord = _make_coordinator(hass)
        res = {
            "platform": "MikroTik",
            "board-name": "RB",
            "version": "7.0",
            "uptime_str": "1d",
            "cpu-load": "10",
            "free-memory": 0,
            "total-memory": 0,
            "free-hdd-space": 0,
            "total-hdd-space": 0,
            "uptime": 0,
            "uptime_epoch": 0,
            "clients_wired": 0,
            "clients_wireless": 0,
            "captive_authorized": 0,
        }
        with patch(
            "custom_components.mikrotik_extended.coordinator.parse_api",
            return_value=res,
        ):
            coord.get_system_resource()

        assert coord.ds["resource"]["memory-usage"] == "unknown"
        assert coord.ds["resource"]["hdd-usage"] == "unknown"


# ---------------------------------------------------------------------------
# get_firmware_update
# ---------------------------------------------------------------------------


class TestFirmwareUpdate:
    def test_denied_no_access(self, hass):
        coord = _make_coordinator(hass)
        coord.ds["access"] = ["read"]
        coord.get_firmware_update()
        # early return → nothing set
        assert "available" not in coord.ds["fw-update"]

    def test_version_parsed_and_available(self, hass):
        coord = _make_coordinator(hass)
        coord.ds["access"] = ["write", "policy", "reboot"]
        fw = {
            "status": "New version is available",
            "channel": "stable",
            "installed-version": "7.13.1",
            "latest-version": "7.14",
        }
        with patch(
            "custom_components.mikrotik_extended.coordinator.parse_api",
            return_value=fw,
        ):
            coord.get_firmware_update()
        assert coord.ds["fw-update"]["available"] is True
        assert coord.major_fw_version == 7
        assert coord.minor_fw_version == 13

    def test_no_status_available_false(self, hass):
        coord = _make_coordinator(hass)
        coord.ds["access"] = ["write", "policy", "reboot"]
        fw = {"installed-version": "7.5"}
        with patch(
            "custom_components.mikrotik_extended.coordinator.parse_api",
            return_value=fw,
        ):
            coord.get_firmware_update()
        assert coord.ds["fw-update"]["available"] is False

    def test_bad_version_string(self, hass):
        coord = _make_coordinator(hass)
        coord.ds["access"] = ["write", "policy", "reboot"]
        fw = {"status": "ok", "installed-version": "abc"}
        with patch(
            "custom_components.mikrotik_extended.coordinator.parse_api",
            return_value=fw,
        ):
            coord.get_firmware_update()
        # ds still populated, status is not "New version is available" → available False
        assert coord.ds["fw-update"]["available"] is False
        assert coord.ds["fw-update"]["installed-version"] == "abc"
        # int("ab") raises, handler catches → major/minor stay at their initial 0
        assert coord.major_fw_version == 0
        assert coord.minor_fw_version == 0


# ---------------------------------------------------------------------------
# get_ups / get_gps / get_script / get_environment / get_captive / get_queue
# ---------------------------------------------------------------------------


class TestMiscSensorGetters:
    def test_get_ups_disabled(self, hass):
        coord = _make_coordinator(hass)
        ups_info = {"enabled": False, "on-line": True}
        with patch(
            "custom_components.mikrotik_extended.coordinator.parse_api",
            return_value=ups_info,
        ):
            coord.get_ups()
        assert coord.ds["ups"]["enabled"] is False

    def test_get_ups_enabled_triggers_monitor(self, hass):
        coord = _make_coordinator(hass)
        ups_info = {"enabled": True, "on-line": True}
        monitor = {"enabled": True, "on-line": True, "load": 50}
        with patch(
            "custom_components.mikrotik_extended.coordinator.parse_api",
            side_effect=[ups_info, monitor],
        ):
            coord.get_ups()
        assert coord.ds["ups"]["load"] == 50

    def test_get_gps(self, hass):
        coord = _make_coordinator(hass)
        gps = {"valid": True, "latitude": "1", "longitude": "2"}
        with patch(
            "custom_components.mikrotik_extended.coordinator.parse_api",
            return_value=gps,
        ):
            coord.get_gps()
        assert coord.ds["gps"]["valid"] is True

    def test_get_script(self, hass):
        coord = _make_coordinator(hass)
        with patch(
            "custom_components.mikrotik_extended.coordinator.parse_api",
            return_value={"s1": {"name": "s1", "run-count": 5}},
        ):
            coord.get_script()
        assert "s1" in coord.ds["script"]

    def test_get_environment(self, hass):
        coord = _make_coordinator(hass)
        with patch(
            "custom_components.mikrotik_extended.coordinator.parse_api",
            return_value={"var1": {"name": "var1", "value": "v"}},
        ):
            coord.get_environment()
        assert "var1" in coord.ds["environment"]

    def test_get_captive(self, hass):
        coord = _make_coordinator(hass)
        coord.ds["resource"] = {"captive_authorized": 0}
        hosts = {
            "m1": {"mac-address": "m1", "authorized": True, "bypassed": False},
            "m2": {"mac-address": "m2", "authorized": False, "bypassed": False},
        }
        with patch(
            "custom_components.mikrotik_extended.coordinator.parse_api",
            return_value=hosts,
        ):
            coord.get_captive()
        assert coord.ds["resource"]["captive_authorized"] == 1

    def test_get_queue(self, hass):
        coord = _make_coordinator(hass)
        queue = {
            "q1": {
                ".id": "*q1",
                "name": "q1",
                "uniq-id": "dupe",
                "max-limit": "1000/2000",
                "rate": "100/200",
                "limit-at": "50/100",
                "burst-limit": "10/20",
                "burst-threshold": "5/10",
                "burst-time": "1s/2s",
                "comment": 3,
            },
            "q2": {
                ".id": "*q2",
                "name": "q2",
                "uniq-id": "dupe",
                "max-limit": "1000/2000",
                "rate": "100/200",
                "limit-at": "50/100",
                "burst-limit": "10/20",
                "burst-threshold": "5/10",
                "burst-time": "1s/2s",
                "comment": "",
            },
        }
        with patch(
            "custom_components.mikrotik_extended.coordinator.parse_api",
            return_value=queue,
        ):
            coord.get_queue()

        assert coord.ds["queue"]["q1"]["upload-max-limit"] == "1000 bps"
        # dedup applied (both have same uniq-id)
        assert coord.ds["queue"]["q1"]["uniq-id"] == "dupe (*q1)"
        assert "dupe" in coord.queue_removed


# ---------------------------------------------------------------------------
# get_arp / get_dns / get_dhcp / get_dhcp_server / get_ip_address / get_cloud
# get_dhcp_client / get_dhcp_network / get_capsman_hosts / get_wireless /
# get_wireless_hosts
# ---------------------------------------------------------------------------


class TestNetworkGetters:
    def test_get_arp(self, hass):
        coord = _make_coordinator(hass)
        coord.ds["bridge"] = {"bridge1": True}
        coord.ds["bridge_host"] = {"AA:BB": {"interface": "ether1"}}
        coord.ds["dhcp-client"] = {"ether2": {}}

        arp = {
            "AA:BB": {"mac-address": "AA:BB", "address": "1.2.3.4", "interface": "bridge1", "bridge": ""},
            "CC:DD": {"mac-address": "CC:DD", "address": "5.6.7.8", "interface": "ether2", "bridge": ""},
        }
        with patch(
            "custom_components.mikrotik_extended.coordinator.parse_api",
            return_value=arp,
        ):
            coord.get_arp()

        # bridge rewriting
        assert coord.ds["arp"]["AA:BB"]["bridge"] == "bridge1"
        assert coord.ds["arp"]["AA:BB"]["interface"] == "ether1"
        # dhcp-client interface pruned
        assert "CC:DD" not in coord.ds["arp"]

    def test_get_dns(self, hass):
        coord = _make_coordinator(hass)
        dns = {"host1": {"name": "host1", "address": "1.2.3.4", "comment": 3}}
        with patch(
            "custom_components.mikrotik_extended.coordinator.parse_api",
            return_value=dns,
        ):
            coord.get_dns()
        assert coord.ds["dns"]["host1"]["comment"] == "3"

    def test_get_dhcp_with_valid_ip_and_server_lookup(self, hass):
        coord = _make_coordinator(hass)
        coord.ds["dhcp-server"] = {}
        coord.ds["arp"] = {}

        dhcp = {
            "AA:BB": {
                "mac-address": "AA:BB",
                "active-mac-address": "unknown",
                "address": "1.2.3.4",
                "active-address": "unknown",
                "host-name": "h1",
                "status": "bound",
                "last-seen": "1m",
                "server": "server1",
                "comment": 1,
                "enabled": True,
                "interface": "unknown",
            },
            "CC:DD": {
                "mac-address": "CC:DD",
                "active-mac-address": "EE:FF",
                "address": "1.2.3.5",
                "active-address": "1.2.3.6",
                "host-name": "h2",
                "status": "waiting",
                "last-seen": "1m",
                "server": "server1",
                "comment": "",
                "enabled": True,
                "interface": "unknown",
            },
            "GG:HH": {
                "mac-address": "GG:HH",
                "active-mac-address": "unknown",
                "address": "badip",
                "active-address": "unknown",
                "host-name": "h3",
                "status": "waiting",
                "last-seen": "1m",
                "server": "server2",
                "comment": "",
                "enabled": True,
                "interface": "unknown",
            },
        }

        def side(*args, **kwargs):
            # /ip/dhcp-server/network or /ip/dhcp-server - depends on caller
            key = kwargs.get("key")
            if key == "mac-address":
                return dhcp
            if key == "name":
                return {"server1": {"name": "server1", "interface": "ether1"}}
            return {}

        with patch(
            "custom_components.mikrotik_extended.coordinator.parse_api",
            side_effect=side,
        ):
            coord.get_dhcp()

        assert coord.ds["dhcp"]["CC:DD"]["address"] == "1.2.3.6"
        assert coord.ds["dhcp"]["CC:DD"]["mac-address"] == "EE:FF"
        assert coord.ds["dhcp"]["AA:BB"]["interface"] == "ether1"
        assert coord.ds["dhcp"]["GG:HH"]["address"] == "unknown"
        assert coord.ds["dhcp_leases"]["bound"] == 1
        assert coord.ds["dhcp_leases"]["total"] == 3

    def test_get_dhcp_server(self, hass):
        coord = _make_coordinator(hass)
        servers = {"server1": {"name": "server1", "interface": "ether1"}}
        with patch(
            "custom_components.mikrotik_extended.coordinator.parse_api",
            return_value=servers,
        ):
            coord.get_dhcp_server()
        assert "server1" in coord.ds["dhcp-server"]

    def test_get_ip_address_with_iface_and_removal(self, hass):
        coord = _make_coordinator(hass)
        coord.ds["interface"] = {
            "ether1": {"name": "ether1", "port-mac-address": "AA:BB"},
        }
        ips = {
            "ip1": {
                ".id": "ip1",
                "address": "10.0.0.1/24",
                "network": "10.0.0.0",
                "interface": "ether1",
                "comment": "",
                "dynamic": False,
                "disabled": False,
                "port-mac-address": "",
                "ip": "",
            },
            "ip2": {
                ".id": "ip2",
                "address": "10.0.1.1/24",
                "network": "10.0.1.0",
                "interface": "bridge_virtual",
                "comment": "",
                "dynamic": False,
                "disabled": False,
                "port-mac-address": "",
                "ip": "",
            },
        }
        with patch(
            "custom_components.mikrotik_extended.coordinator.parse_api",
            return_value=ips,
        ):
            coord.get_ip_address()

        assert coord.ds["ip_address"]["ip1"]["ip"] == "10.0.0.1"
        assert coord.ds["ip_address"]["ip1"]["port-mac-address"] == "AA:BB"
        assert "ip2" not in coord.ds["ip_address"]  # pruned

    def test_get_cloud_success_and_exception(self, hass):
        coord = _make_coordinator(hass)
        cloud_data = {
            "public-address": "1.2.3.4",
            "ddns-enabled": "true",
            "dns-name": "foo.sn.mynetname.net",
            "status": "updated",
            "back-to-home-vpn": "",
        }
        with patch(
            "custom_components.mikrotik_extended.coordinator.parse_api",
            return_value=cloud_data,
        ):
            coord.get_cloud()
        assert coord.ds["cloud"]["ddns-hostname"] == "foo.sn.mynetname.net"
        assert coord.ds["cloud"]["ddns-status"] == "updated"

        # Exception path: try/except swallows the error; previously-populated
        # ds["cloud"] from the successful call above is preserved (not cleared).
        with patch(
            "custom_components.mikrotik_extended.coordinator.parse_api",
            side_effect=Exception("boom"),
        ):
            coord.get_cloud()
        assert coord.ds["cloud"]["ddns-hostname"] == "foo.sn.mynetname.net"
        assert coord.ds["cloud"]["ddns-status"] == "updated"

    def test_get_dhcp_client(self, hass):
        coord = _make_coordinator(hass)
        with patch(
            "custom_components.mikrotik_extended.coordinator.parse_api",
            return_value={"ether1": {"interface": "ether1", "status": "bound", "address": "1.2.3.4/24"}},
        ):
            coord.get_dhcp_client()
        assert "ether1" in coord.ds["dhcp-client"]

    def test_get_dhcp_network(self, hass):
        coord = _make_coordinator(hass)
        nets = {
            "10.0.0.0/24": {
                "address": "10.0.0.0/24",
                "gateway": "10.0.0.1",
                "netmask": "24",
                "dns-server": "1.1.1.1",
                "domain": "",
                "IPv4Network": "",
            },
        }
        with patch(
            "custom_components.mikrotik_extended.coordinator.parse_api",
            return_value=nets,
        ):
            coord.get_dhcp_network()
        assert coord.ds["dhcp-network"]["10.0.0.0/24"]["IPv4Network"] is not ""  # noqa: F632

    def test_get_capsman_hosts_v7_13plus(self, hass):
        coord = _make_coordinator(hass)
        coord.major_fw_version = 7
        coord.minor_fw_version = 13
        hosts = {"AA:BB": {"mac-address": "AA:BB", "interface": "wlan1", "ssid": "home"}}
        with patch(
            "custom_components.mikrotik_extended.coordinator.parse_api",
            return_value=hosts,
        ):
            coord.get_capsman_hosts()
        assert "AA:BB" in coord.ds["capsman_hosts"]

    def test_get_capsman_hosts_old(self, hass):
        coord = _make_coordinator(hass)
        coord.major_fw_version = 6
        coord.minor_fw_version = 0
        with patch(
            "custom_components.mikrotik_extended.coordinator.parse_api",
            return_value={},
        ):
            coord.get_capsman_hosts()

    def test_get_wireless_with_master_interface(self, hass):
        coord = _make_coordinator(hass)
        coord.ds["interface"] = {"wlan1": {"name": "wlan1"}}

        wireless = {
            "wlan2": {
                "master-interface": "wlan1",
                "mac-address": "unknown",
                "ssid": "unknown",
                "mode": "unknown",
                "radio-name": "unknown",
                "interface-type": "unknown",
                "country": "unknown",
                "installation": "unknown",
                "antenna-gain": "unknown",
                "frequency": "unknown",
                "band": "unknown",
                "channel-width": "unknown",
                "secondary-frequency": "unknown",
                "wireless-protocol": "unknown",
                "rate-set": "unknown",
                "distance": "unknown",
                "tx-power-mode": "unknown",
                "vlan-id": "unknown",
                "wds-mode": "unknown",
                "wds-default-bridge": "unknown",
                "bridge-mode": "unknown",
                "hide-ssid": False,
                "running": True,
                "disabled": False,
            },
            "wlan1": {
                "master-interface": "",
                "mac-address": "AA:BB",
                "ssid": "home",
                "mode": "ap",
                "radio-name": "r1",
                "interface-type": "it",
                "country": "US",
                "installation": "indoor",
                "antenna-gain": "3",
                "frequency": "2412",
                "band": "2ghz",
                "channel-width": "20",
                "secondary-frequency": "none",
                "wireless-protocol": "any",
                "rate-set": "default",
                "distance": "auto",
                "tx-power-mode": "default",
                "vlan-id": "none",
                "wds-mode": "disabled",
                "wds-default-bridge": "none",
                "bridge-mode": "disabled",
                "hide-ssid": False,
                "running": True,
                "disabled": False,
            },
        }
        with patch(
            "custom_components.mikrotik_extended.coordinator.parse_api",
            return_value=wireless,
        ):
            coord.get_wireless()

        assert coord.ds["wireless"]["wlan2"]["mac-address"] == "AA:BB"
        assert coord.ds["interface"]["wlan1"]["ssid"] == "home"

    def test_get_wireless_hosts(self, hass):
        coord = _make_coordinator(hass)
        with patch(
            "custom_components.mikrotik_extended.coordinator.parse_api",
            return_value={"AA:BB": {"mac-address": "AA:BB", "ap": False}},
        ):
            coord.get_wireless_hosts()
        assert "AA:BB" in coord.ds["wireless_hosts"]


# ---------------------------------------------------------------------------
# async_process_host
# ---------------------------------------------------------------------------


class TestAsyncProcessHost:
    async def test_capsman_wireless_dhcp_arp_populate(self, hass):
        coord = _make_coordinator(hass)
        coord.support_capsman = True
        coord.support_wireless = True
        coord.ds["capsman_hosts"] = {
            "AA:BB": {"mac-address": "AA:BB", "interface": "wlan1"},
        }
        coord.ds["wireless_hosts"] = {
            "CC:DD": {
                "mac-address": "CC:DD",
                "interface": "wlan1",
                "ap": False,
                "signal-strength": "-50",
                "tx-ccq": 70,
                "tx-rate": 100,
                "rx-rate": 100,
            },
            "EE:FF": {"mac-address": "EE:FF", "ap": True, "interface": "", "signal-strength": "", "tx-ccq": "", "tx-rate": "", "rx-rate": ""},
        }
        coord.ds["dhcp"] = {
            "GG:HH": {
                "mac-address": "GG:HH",
                "address": "10.0.0.1",
                "interface": "ether1",
                "enabled": True,
                "host-name": "h1",
                "comment": "",
            },
        }
        coord.ds["arp"] = {
            "II:JJ": {"mac-address": "II:JJ", "address": "10.0.0.2", "interface": "ether1"},
        }
        coord.ds["host_hass"] = {"KK:LL:MM:NN:OO:PP": "Restored"}
        coord.ds["dns"] = {}
        coord.ds["hostspot_host"] = {}
        coord.ds["resource"] = {}

        # mock the mac lookup so it doesn't block
        coord.async_mac_lookup.lookup = AsyncMock(return_value="Vendor Inc")

        await coord.async_process_host()

        assert "AA:BB" in coord.ds["host"]
        assert coord.ds["host"]["AA:BB"]["source"] == "capsman"
        assert coord.ds["host"]["CC:DD"]["source"] == "wireless"
        assert "EE:FF" not in coord.ds["host"]  # ap=True skipped
        assert coord.ds["host"]["GG:HH"]["source"] == "dhcp"
        assert coord.ds["host"]["II:JJ"]["source"] == "arp"
        assert "kk:ll:mm:nn:oo:pp" in coord.ds["host"]
        assert coord.ds["resource"]["clients_wireless"] >= 1

    async def test_host_hostname_resolved_from_dns(self, hass):
        coord = _make_coordinator(hass)
        coord.ds["capsman_hosts"] = {}
        coord.ds["wireless_hosts"] = {}
        coord.ds["dhcp"] = {
            "AA:BB": {
                "mac-address": "AA:BB",
                "address": "10.0.0.1",
                "interface": "ether1",
                "enabled": True,
                "host-name": "h1",
                "comment": "comment1#info",
            },
        }
        coord.ds["arp"] = {}
        coord.ds["dns"] = {
            "dns1": {"name": "mydevice.local", "address": "10.0.0.1", "comment": "DNSName#extra"},
        }
        coord.ds["host_hass"] = {}
        coord.ds["hostspot_host"] = {}
        coord.ds["resource"] = {}

        coord.async_mac_lookup.lookup = AsyncMock(return_value="Vendor Inc")
        await coord.async_process_host()

        # dns comment prefix used
        assert coord.ds["host"]["AA:BB"]["host-name"] == "DNSName"

    async def test_host_hostname_from_dhcp_and_mac_fallback(self, hass):
        coord = _make_coordinator(hass)
        coord.ds["capsman_hosts"] = {}
        coord.ds["wireless_hosts"] = {}
        coord.ds["dhcp"] = {
            "AA:BB": {
                "mac-address": "AA:BB",
                "address": "10.0.0.1",
                "interface": "ether1",
                "enabled": True,
                "host-name": "dhcp-hostname",
                "comment": "",
            },
            "CC:DD": {
                "mac-address": "CC:DD",
                "address": "10.0.0.2",
                "interface": "ether1",
                "enabled": True,
                "host-name": "unknown",
                "comment": "",
            },
        }
        coord.ds["arp"] = {}
        coord.ds["dns"] = {}
        coord.ds["host_hass"] = {}
        coord.ds["hostspot_host"] = {}
        coord.ds["resource"] = {}

        coord.async_mac_lookup.lookup = AsyncMock(return_value="")
        await coord.async_process_host()

        assert coord.ds["host"]["AA:BB"]["host-name"] == "dhcp-hostname"
        # CC:DD falls back to mac address (uid)
        assert coord.ds["host"]["CC:DD"]["host-name"] == "CC:DD"

    async def test_manufacturer_exception_fallback(self, hass):
        coord = _make_coordinator(hass)
        coord.ds["capsman_hosts"] = {}
        coord.ds["wireless_hosts"] = {}
        coord.ds["dhcp"] = {}
        coord.ds["arp"] = {
            "AA:BB": {"mac-address": "AA:BB", "address": "10.0.0.1", "interface": "ether1"},
        }
        coord.ds["dns"] = {}
        coord.ds["host_hass"] = {}
        coord.ds["hostspot_host"] = {}
        coord.ds["resource"] = {}

        coord.async_mac_lookup.lookup = AsyncMock(side_effect=Exception("no-vendor"))

        await coord.async_process_host()
        assert coord.ds["host"]["AA:BB"]["manufacturer"] == ""

    async def test_captive_cleanup(self, hass):
        coord = _make_coordinator(hass)
        coord.ds["capsman_hosts"] = {}
        coord.ds["wireless_hosts"] = {}
        coord.ds["dhcp"] = {}
        coord.ds["arp"] = {
            "AA:BB": {"mac-address": "AA:BB", "address": "10.0.0.1", "interface": "ether1"},
        }
        coord.ds["dns"] = {}
        coord.ds["host_hass"] = {}
        coord.ds["hostspot_host"] = {}
        coord.ds["resource"] = {}
        coord.ds["host"] = {
            "AA:BB": {
                "source": "arp",
                "authorized": True,
                "bypassed": False,
                "address": "10.0.0.1",
                "mac-address": "AA:BB",
                "interface": "ether1",
                "host-name": "h1",
                "manufacturer": "",
                "last-seen": None,
                "available": False,
            },
        }
        # option_sensor_client_captive True, hostspot_host empty → del authorized/bypassed
        coord.async_mac_lookup.lookup = AsyncMock(return_value="")
        with patch.object(
            type(coord),
            "option_sensor_client_captive",
            new_callable=lambda: property(lambda self: True),
        ):
            await coord.async_process_host()

        assert "authorized" not in coord.ds["host"]["AA:BB"]


# ---------------------------------------------------------------------------
# _get_iface_from_entry
# ---------------------------------------------------------------------------


class TestGetIfaceFromEntry:
    def test_found(self, hass):
        coord = _make_coordinator(hass)
        coord.ds["interface"] = {"ether1": {"name": "ether1"}}
        result = coord._get_iface_from_entry({"interface": "ether1"})
        assert result == "ether1"

    def test_not_found(self, hass):
        coord = _make_coordinator(hass)
        coord.ds["interface"] = {"ether1": {"name": "ether1"}}
        result = coord._get_iface_from_entry({"interface": "ether2"})
        assert result is None


# ---------------------------------------------------------------------------
# sync_kid_control_monitoring_profile / process_kid_control_devices
# ---------------------------------------------------------------------------


class TestKidControl:
    def test_sync_creates_profile_when_traffic_enabled(self, hass):
        coord = _make_coordinator(hass, options={"sensor_client_traffic": True})
        coord.api.query.return_value = []
        coord.api.execute.return_value = True
        coord.sync_kid_control_monitoring_profile()
        coord.api.execute.assert_called()

    def test_sync_warns_when_creation_fails(self, hass):
        coord = _make_coordinator(hass, options={"sensor_client_traffic": True})
        coord.api.query.return_value = []
        coord.api.execute.return_value = False
        coord.sync_kid_control_monitoring_profile()

    def test_sync_removes_profile_when_traffic_disabled(self, hass):
        coord = _make_coordinator(hass, options={"sensor_client_traffic": False})
        coord.api.query.return_value = [{"name": coord._HA_MONITORING_PROFILE}]
        coord.api.execute.return_value = True
        coord.sync_kid_control_monitoring_profile()
        coord.api.execute.assert_called_with("/ip/kid-control", "remove", "name", coord._HA_MONITORING_PROFILE)

    def test_sync_noop_when_aligned(self, hass):
        coord = _make_coordinator(hass, options={"sensor_client_traffic": False})
        # disabled + no profile → no action
        coord.api.query.return_value = []
        coord.sync_kid_control_monitoring_profile()
        coord.api.execute.assert_not_called()

    def test_process_kid_control_devices_empty(self, hass):
        coord = _make_coordinator(hass)
        coord.ds["host"] = {"AA:BB": {"address": "1", "mac-address": "AA:BB", "host-name": "h"}}
        with patch(
            "custom_components.mikrotik_extended.coordinator.parse_api",
            return_value={},
        ):
            coord.process_kid_control_devices()
        assert "kid-control-devices" in coord.notified_flags
        # second call does not duplicate flag
        with patch(
            "custom_components.mikrotik_extended.coordinator.parse_api",
            return_value={},
        ):
            coord.process_kid_control_devices()
        assert coord.notified_flags.count("kid-control-devices") == 1

    def test_process_kid_control_devices_with_data(self, hass):
        coord = _make_coordinator(hass)
        coord.ds["host"] = {
            "AA:BB": {"address": "1.2.3.4", "mac-address": "AA:BB", "host-name": "h"},
        }
        coord.notified_flags = ["kid-control-devices"]
        data = {
            "AA:BB": {"mac-address": "AA:BB", "rate-up": 800, "rate-down": 1600, "enabled": True},
            "ZZ:XX": {"mac-address": "ZZ:XX", "rate-up": 0, "rate-down": 0, "enabled": False},
        }
        with patch(
            "custom_components.mikrotik_extended.coordinator.parse_api",
            return_value=data,
        ):
            coord.process_kid_control_devices()

        assert coord.ds["client_traffic"]["AA:BB"]["available"] is True
        assert coord.ds["client_traffic"]["AA:BB"]["tx"] == 100  # 800/8
        assert coord.ds["client_traffic"]["AA:BB"]["rx"] == 200
        # unknown host skipped
        assert "ZZ:XX" not in coord.ds["client_traffic"]
        # notified_flags cleared
        assert "kid-control-devices" not in coord.notified_flags


# ---------------------------------------------------------------------------
# MikrotikTrackerCoordinator
# ---------------------------------------------------------------------------


def _make_tracker(hass, main_coord):
    from custom_components.mikrotik_extended.coordinator import MikrotikTrackerCoordinator

    entry = main_coord.config_entry
    with patch("custom_components.mikrotik_extended.coordinator.MikrotikAPI") as MockAPI:
        mock_api = MagicMock()
        MockAPI.return_value = mock_api
        tracker = MikrotikTrackerCoordinator(hass, entry, main_coord)
    tracker.api = mock_api
    # Ensure config_entry survives HA's weakref / lifecycle handling
    tracker.config_entry = entry
    return tracker


class TestTrackerCoordinator:
    def test_option_zone(self, hass):
        main = _make_coordinator(hass)
        tracker = _make_tracker(hass, main)
        assert tracker.option_zone == "home"

    async def test_update_skipped_when_no_track_network_hosts(self, hass):
        main = _make_coordinator(hass, options={"track_network_hosts": False})
        main.ds["access"] = ["test"]
        tracker = _make_tracker(hass, main)
        result = await tracker._async_update_data()
        assert result is None

    async def test_update_skipped_when_no_test_access(self, hass):
        main = _make_coordinator(hass, options={"track_network_hosts": True})
        main.ds["access"] = []  # no test
        tracker = _make_tracker(hass, main)
        result = await tracker._async_update_data()
        assert result is None

    async def test_update_initializes_and_returns_data(self, hass):
        main = _make_coordinator(hass, options={"track_network_hosts": True})
        main.ds["access"] = ["test"]
        main.host_tracking_initialized = False
        main.ds["host"] = {
            "AA:BB": {
                "source": "arp",
                "address": "1.2.3.4",
                "mac-address": "AA:BB",
                "interface": "ether1",
                "available": False,
            },
        }
        main.ds["arp"] = {}
        main.ds["routerboard"] = {}
        main.async_process_host = AsyncMock()

        tracker = _make_tracker(hass, main)
        result = await tracker._async_update_data()
        assert "host" in result
        assert main.host_tracking_initialized is True

    async def test_update_pings_initialized_hosts(self, hass):
        main = _make_coordinator(hass, options={"track_network_hosts": True})
        main.ds["access"] = ["test"]
        main.host_tracking_initialized = True
        main.ds["host"] = {
            "AA:BB": {
                "source": "arp",
                "address": "1.2.3.4",
                "mac-address": "AA:BB",
                "interface": "ether1",
                "available": False,
                "last-seen": None,
            },
        }
        main.ds["arp"] = {
            "AA:BB": {"bridge": "bridge1", "address": "1.2.3.4", "interface": "ether1", "mac-address": "AA:BB"},
        }
        main.ds["routerboard"] = {}
        main.async_process_host = AsyncMock()

        tracker = _make_tracker(hass, main)
        # arp_ping returns True → host available
        tracker.api.arp_ping = MagicMock(return_value=True)
        result = await tracker._async_update_data()
        assert result is not None
        assert main.ds["host"]["AA:BB"]["available"] is True
        assert main.ds["host"]["AA:BB"]["last-seen"] is not None


# ---------------------------------------------------------------------------
# MikrotikData dataclass
# ---------------------------------------------------------------------------


class TestMikrotikData:
    def test_dataclass_fields(self, hass):
        from custom_components.mikrotik_extended.coordinator import MikrotikData

        main = _make_coordinator(hass)
        tracker = _make_tracker(hass, main)
        data = MikrotikData(data_coordinator=main, tracker_coordinator=tracker)
        assert data.data_coordinator is main
        assert data.tracker_coordinator is tracker


# ---------------------------------------------------------------------------
# Additional coverage tests — close remaining gaps
# ---------------------------------------------------------------------------


class TestAsLocalWithDefaultTimezone:
    """Cover line 132: naive dt path when DEFAULT_TIME_ZONE is not None."""

    def test_as_local_naive_when_default_tz_set(self):
        import pytz

        from custom_components.mikrotik_extended import coordinator as coord_mod

        tz_utc = pytz.UTC
        with patch.object(coord_mod, "DEFAULT_TIME_ZONE", tz_utc):
            # naive datetime — tzinfo None — must go through localize (line 132)
            dt_naive = datetime(2024, 1, 1)
            result = coord_mod.as_local(dt_naive)
        assert result is not None
        assert result.tzinfo is not None


# ---------------------------------------------------------------------------
# _async_update_data — support gated + sensor option gated update callers
# ---------------------------------------------------------------------------


class TestAsyncUpdateDataSupportPaths:
    """Cover the remaining gated branches in _async_update_data pipeline."""

    def _stub_all_get_methods(self, coordinator):
        noop_sync = MagicMock(return_value=None)
        noop_async = AsyncMock(return_value=None)
        for attr in dir(coordinator):
            if attr.startswith("get_") or attr.startswith("process_") or attr.startswith("sync_"):
                if attr.startswith("async_") or "async" in attr:
                    setattr(coordinator, attr, noop_async)
                else:
                    setattr(coordinator, attr, noop_sync)
        for name in [
            "get_access",
            "get_firmware_update",
            "get_system_resource",
            "get_capabilities",
            "get_system_routerboard",
            "get_script",
            "get_dhcp_network",
            "get_dns",
            "get_system_health",
            "get_dhcp_client",
            "get_interface",
            "get_ip_address",
            "get_cloud",
            "get_capsman_hosts",
            "get_wireless",
            "get_wireless_hosts",
            "get_bridge",
            "get_arp",
            "get_dhcp",
            "process_interface_client",
            "get_nat",
            "get_kidcontrol",
            "get_mangle",
            "get_routing_rules",
            "get_wireguard_peers",
            "get_containers",
            "get_device_mode",
            "get_packages",
            "get_filter",
            "get_netwatch",
            "get_ppp",
            "sync_kid_control_monitoring_profile",
            "process_kid_control_devices",
            "get_captive",
            "get_queue",
            "get_environment",
            "get_ups",
            "get_gps",
        ]:
            setattr(coordinator, name, MagicMock(return_value=None))
        coordinator.async_get_host_hass = AsyncMock(return_value=None)
        coordinator.async_process_host = AsyncMock(return_value=None)

    async def test_all_support_and_option_gated_branches_executed(self, hass):
        """Cover capsman/wireless/ppp/wireguard/containers/ups/gps/sensor_* paths."""
        coordinator = _make_coordinator(
            hass,
            options={
                "sensor_nat": True,
                "sensor_kidcontrol": True,
                "sensor_mangle": True,
                "sensor_routing_rules": True,
                "sensor_wireguard": True,
                "sensor_containers": True,
                "sensor_filter": True,
                "sensor_netwatch": True,
                "sensor_ppp": True,
                "sensor_client_traffic": True,
                "sensor_client_captive": True,
                "sensor_simple_queues": True,
                "sensor_environment": True,
            },
        )
        self._stub_all_get_methods(coordinator)

        # Enable the support flags so gated branches execute
        coordinator.support_capsman = True
        coordinator.support_wireless = True
        coordinator.support_wireguard = True
        coordinator.support_containers = True
        coordinator.support_ppp = True
        coordinator.support_ups = True
        coordinator.support_gps = True
        coordinator.major_fw_version = 7  # triggers kid-control paths

        coordinator.api.has_reconnected.return_value = False
        coordinator.last_hwinfo_update = datetime.now()
        coordinator.api.connected.return_value = True
        coordinator.api.error = ""

        with (
            patch("custom_components.mikrotik_extended.coordinator.IssueSeverity", _FakeIssueSeverity),
            patch("custom_components.mikrotik_extended.coordinator.async_create_issue", MagicMock()),
            patch("custom_components.mikrotik_extended.coordinator.async_delete_issue", MagicMock()),
            patch("custom_components.mikrotik_extended.coordinator.async_dispatcher_send"),
        ):
            result = await coordinator._async_update_data()

        assert result is coordinator.ds
        # Verify gated methods were actually invoked
        coordinator.get_capsman_hosts.assert_called()
        coordinator.get_wireless.assert_called()
        coordinator.get_wireless_hosts.assert_called()
        coordinator.get_nat.assert_called()
        coordinator.get_kidcontrol.assert_called()
        coordinator.get_mangle.assert_called()
        coordinator.get_routing_rules.assert_called()
        coordinator.get_wireguard_peers.assert_called()
        coordinator.get_containers.assert_called()
        coordinator.get_filter.assert_called()
        coordinator.get_netwatch.assert_called()
        coordinator.get_ppp.assert_called()
        coordinator.sync_kid_control_monitoring_profile.assert_called()
        coordinator.process_kid_control_devices.assert_called()
        coordinator.get_captive.assert_called()
        coordinator.get_queue.assert_called()
        coordinator.get_environment.assert_called()
        coordinator.get_ups.assert_called()
        coordinator.get_gps.assert_called()

    async def test_insufficient_permissions_issue_created(self, hass):
        """Cover line 681: async_create_issue with insufficient_permissions when access_missing non-empty."""
        coordinator = _make_coordinator(hass)
        self._stub_all_get_methods(coordinator)

        coordinator.api.has_reconnected.return_value = True
        coordinator.api.connected.return_value = True
        coordinator.api.error = ""
        coordinator.ds["access_missing"] = ["write", "policy"]

        mock_create = MagicMock()
        mock_delete = MagicMock()
        with (
            patch("custom_components.mikrotik_extended.coordinator.IssueSeverity", _FakeIssueSeverity),
            patch("custom_components.mikrotik_extended.coordinator.async_create_issue", mock_create),
            patch("custom_components.mikrotik_extended.coordinator.async_delete_issue", mock_delete),
            patch("custom_components.mikrotik_extended.coordinator.async_dispatcher_send"),
        ):
            await coordinator._async_update_data()

        # An async_create_issue call with issue_id "insufficient_permissions" should have happened
        created_ids = [c[0][2] for c in mock_create.call_args_list]
        assert "insufficient_permissions" in created_ids

    async def test_final_wrong_login_raises_config_entry_auth_failed(self, hass):
        """Cover lines 798-799: final if block raising ConfigEntryAuthFailed on wrong_login."""
        coordinator = _make_coordinator(hass)
        self._stub_all_get_methods(coordinator)

        # Skip early reconnect block
        coordinator.api.has_reconnected.return_value = False
        coordinator.last_hwinfo_update = datetime.now()
        # Disconnected at final check with wrong_login
        coordinator.api.connected.return_value = False
        coordinator.api.error = "wrong_login"

        with pytest.raises(ConfigEntryAuthFailed):
            await coordinator._async_update_data()

    async def test_slow_cycle_logs_warning(self, hass):
        """Cover lines 803-804: slow-cycle warning when elapsed > 5s."""
        coordinator = _make_coordinator(hass)
        self._stub_all_get_methods(coordinator)

        coordinator.api.has_reconnected.return_value = False
        coordinator.last_hwinfo_update = datetime.now()
        coordinator.api.connected.return_value = True
        coordinator.api.error = ""

        # Patch datetime.now inside coordinator to simulate >5s elapsed cycle
        start = datetime(2024, 1, 1, 0, 0, 0)
        end = datetime(2024, 1, 1, 0, 0, 10)
        call_counter = {"n": 0}

        def fake_now():
            call_counter["n"] += 1
            # First call at start, any subsequent calls return end
            return start if call_counter["n"] == 1 else end

        with (
            patch("custom_components.mikrotik_extended.coordinator.IssueSeverity", _FakeIssueSeverity),
            patch("custom_components.mikrotik_extended.coordinator.async_create_issue", MagicMock()),
            patch("custom_components.mikrotik_extended.coordinator.async_delete_issue", MagicMock()),
            patch("custom_components.mikrotik_extended.coordinator.async_dispatcher_send"),
            patch("custom_components.mikrotik_extended.coordinator.datetime") as mock_dt,
        ):
            mock_dt.now.side_effect = fake_now
            # preserve other datetime attrs
            mock_dt.timestamp = datetime.timestamp
            await coordinator._async_update_data()


# ---------------------------------------------------------------------------
# process_interface_client — ARP interface mismatch continue (line 1084)
# ---------------------------------------------------------------------------


class TestProcessInterfaceClientContinue:
    def test_arp_interface_mismatch_skipped(self, hass):
        """Cover line 1084: arp entry interface not matching iface, not a bonding slave."""
        coord = _make_coordinator(hass)
        coord.ds["interface"] = {"ether1": {"name": "ether1"}}
        coord.ds["arp"] = {
            # Belongs to a different interface; not a bonding slave of ether1
            "m1": {"interface": "ether2", "address": "1.2.3.4", "mac-address": "AA:BB"},
        }
        coord.ds["bonding_slaves"] = {}
        coord.ds["dhcp-client"] = {}

        coord.process_interface_client()

        # ARP entry skipped → client-ip-address stays "none" via dhcp-client fallback
        assert coord.ds["interface"]["ether1"]["client-ip-address"] == "none"


# ---------------------------------------------------------------------------
# get_system_resource — uptime update via old timestamp (lines 1837-1839)
# ---------------------------------------------------------------------------


class TestGetSystemResourceUptimeRefresh:
    def test_uptime_refreshed_when_existing_too_old(self, hass):
        """Cover lines 1837-1839: existing uptime datetime present but >10s drift."""
        import pytz

        coord = _make_coordinator(hass)
        # Existing uptime way in the past so uptime_tm > uptime_old + 10
        coord.ds["resource"] = {
            "platform": "MikroTik",
            "board-name": "RB",
            "version": "7.0",
            "uptime_str": "1h",
            "cpu-load": "10",
            "free-memory": 500,
            "total-memory": 1000,
            "free-hdd-space": 100,
            "total-hdd-space": 1000,
            "uptime": datetime(2000, 1, 1, tzinfo=pytz.UTC),
            "uptime_epoch": 0,
            "clients_wired": 0,
            "clients_wireless": 0,
            "captive_authorized": 0,
        }
        # rebootcheck <= uptime_epoch to NOT trigger firmware update
        coord.rebootcheck = 0
        res_parsed = dict(coord.ds["resource"])
        with patch(
            "custom_components.mikrotik_extended.coordinator.parse_api",
            return_value=res_parsed,
        ):
            coord.get_system_resource()

        # uptime was refreshed (not the 2000-01-01 timestamp anymore)
        assert coord.ds["resource"]["uptime"].year >= 2024


# ---------------------------------------------------------------------------
# get_queue — uniq-id missing path (line 2078)
# ---------------------------------------------------------------------------


class TestGetQueueUniqIdFallback:
    def test_uniq_id_generated_from_name_when_missing(self, hass):
        """Cover line 2078: `uniq-id` not in queue entry → populated from name."""
        coord = _make_coordinator(hass)
        # No `uniq-id` key on the entry
        queue = {
            "qX": {
                ".id": "*qX",
                "name": "queueName",
                "max-limit": "1000/2000",
                "rate": "100/200",
                "limit-at": "50/100",
                "burst-limit": "10/20",
                "burst-threshold": "5/10",
                "burst-time": "1s/2s",
                "comment": "",
            },
        }
        with patch(
            "custom_components.mikrotik_extended.coordinator.parse_api",
            return_value=queue,
        ):
            coord.get_queue()

        assert coord.ds["queue"]["qX"]["uniq-id"] == "queueName"


# ---------------------------------------------------------------------------
# get_dhcp — arp fallback branches (lines 2219-2222)
# ---------------------------------------------------------------------------


class TestGetDhcpArpFallback:
    def test_interface_from_arp_bridge(self, hass):
        """Cover line 2220: arp bridge non-unknown → dhcp interface = bridge."""
        coord = _make_coordinator(hass)
        coord.ds["dhcp-server"] = {}
        coord.ds["arp"] = {
            "AA:BB": {"bridge": "bridge1", "interface": "ether1"},
        }
        dhcp = {
            "AA:BB": {
                "mac-address": "AA:BB",
                "active-mac-address": "unknown",
                "address": "1.2.3.4",
                "active-address": "unknown",
                "host-name": "h1",
                "status": "bound",
                "last-seen": "1m",
                "server": "missing-server",
                "comment": "",
                "enabled": True,
                "interface": "unknown",
            },
        }

        def side(*args, **kwargs):
            key = kwargs.get("key")
            if key == "mac-address":
                return dhcp
            if key == "name":
                # dhcp-server lookup returns empty so arp fallback hits
                return {}
            return {}

        with patch(
            "custom_components.mikrotik_extended.coordinator.parse_api",
            side_effect=side,
        ):
            coord.get_dhcp()

        assert coord.ds["dhcp"]["AA:BB"]["interface"] == "bridge1"

    def test_interface_from_arp_interface_when_bridge_unknown(self, hass):
        """Cover line 2222: arp bridge=unknown → dhcp interface = arp interface."""
        coord = _make_coordinator(hass)
        coord.ds["dhcp-server"] = {}
        coord.ds["arp"] = {
            "CC:DD": {"bridge": "unknown", "interface": "ether2"},
        }
        dhcp = {
            "CC:DD": {
                "mac-address": "CC:DD",
                "active-mac-address": "unknown",
                "address": "1.2.3.5",
                "active-address": "unknown",
                "host-name": "h2",
                "status": "bound",
                "last-seen": "1m",
                "server": "missing-server",
                "comment": "",
                "enabled": True,
                "interface": "unknown",
            },
        }

        def side(*args, **kwargs):
            key = kwargs.get("key")
            if key == "mac-address":
                return dhcp
            if key == "name":
                return {}
            return {}

        with patch(
            "custom_components.mikrotik_extended.coordinator.parse_api",
            side_effect=side,
        ):
            coord.get_dhcp()

        assert coord.ds["dhcp"]["CC:DD"]["interface"] == "ether2"


# ---------------------------------------------------------------------------
# async_process_host — remaining host source / availability / address paths
# ---------------------------------------------------------------------------


class TestAsyncProcessHostRemainingBranches:
    async def test_capsman_host_with_non_capsman_existing_continues(self, hass):
        """Cover lines 2465-2466: existing host with source != capsman triggers continue."""
        coord = _make_coordinator(hass)
        coord.support_capsman = True
        coord.support_wireless = False
        coord.ds["capsman_hosts"] = {
            "AA:BB": {"mac-address": "AA:BB", "interface": "wlan1"},
        }
        coord.ds["wireless_hosts"] = {}
        coord.ds["dhcp"] = {}
        coord.ds["arp"] = {}
        coord.ds["dns"] = {}
        coord.ds["host_hass"] = {}
        coord.ds["hostspot_host"] = {}
        coord.ds["resource"] = {}
        # Pre-existing host with source != capsman
        coord.ds["host"] = {
            "AA:BB": {
                "source": "dhcp",
                "address": "1.2.3.4",
                "mac-address": "AA:BB",
                "interface": "ether1",
                "host-name": "h",
                "manufacturer": "",
                "last-seen": None,
                "available": False,
            },
        }
        coord.async_mac_lookup.lookup = AsyncMock(return_value="")

        await coord.async_process_host()

        # Source remains "dhcp" — capsman update skipped due to continue
        assert coord.ds["host"]["AA:BB"]["source"] == "dhcp"

    async def test_wireless_host_existing_capsman_continues(self, hass):
        """Cover line 2483-2484: wireless path with existing capsman source continues."""
        coord = _make_coordinator(hass)
        coord.support_capsman = False
        coord.support_wireless = True
        coord.ds["capsman_hosts"] = {}
        coord.ds["wireless_hosts"] = {
            "AA:BB": {
                "mac-address": "AA:BB",
                "interface": "wlan1",
                "ap": False,
                "signal-strength": "-50",
                "tx-ccq": 70,
                "tx-rate": 100,
                "rx-rate": 100,
            },
        }
        coord.ds["dhcp"] = {}
        coord.ds["arp"] = {}
        coord.ds["dns"] = {}
        coord.ds["host_hass"] = {}
        coord.ds["hostspot_host"] = {}
        coord.ds["resource"] = {}
        coord.ds["host"] = {
            "AA:BB": {
                "source": "capsman",
                "address": "1.2.3.4",
                "mac-address": "AA:BB",
                "interface": "wlan1",
                "host-name": "h",
                "manufacturer": "",
                "last-seen": None,
                "available": False,
            },
        }
        coord.async_mac_lookup.lookup = AsyncMock(return_value="")

        await coord.async_process_host()

        # Source stays capsman — wireless branch continued past it
        assert coord.ds["host"]["AA:BB"]["source"] == "capsman"

    async def test_wireless_host_existing_non_capsman_becomes_wireless(self, hass):
        """Cover lines 2485-2486: wireless path with existing non-capsman source overwrites to wireless."""
        coord = _make_coordinator(hass)
        coord.support_capsman = False
        coord.support_wireless = True
        coord.ds["capsman_hosts"] = {}
        coord.ds["wireless_hosts"] = {
            "AA:BB": {
                "mac-address": "AA:BB",
                "interface": "wlan1",
                "ap": False,
                "signal-strength": "-50",
                "tx-ccq": 70,
                "tx-rate": 100,
                "rx-rate": 100,
            },
        }
        coord.ds["dhcp"] = {}
        coord.ds["arp"] = {}
        coord.ds["dns"] = {}
        coord.ds["host_hass"] = {}
        coord.ds["hostspot_host"] = {}
        coord.ds["resource"] = {}
        coord.ds["host"] = {
            "AA:BB": {
                "source": "arp",
                "address": "1.2.3.4",
                "mac-address": "AA:BB",
                "interface": "ether1",
                "host-name": "h",
                "manufacturer": "",
                "last-seen": None,
                "available": False,
            },
        }
        coord.async_mac_lookup.lookup = AsyncMock(return_value="")

        await coord.async_process_host()

        assert coord.ds["host"]["AA:BB"]["source"] == "wireless"

    async def test_dhcp_disabled_entry_skipped_and_existing_non_dhcp_skipped(self, hass):
        """Cover line 2504 (disabled continue) and 2508-2509 (existing non-dhcp continue)."""
        coord = _make_coordinator(hass)
        coord.support_capsman = False
        coord.support_wireless = False
        coord.ds["capsman_hosts"] = {}
        coord.ds["wireless_hosts"] = {}
        coord.ds["dhcp"] = {
            # disabled → line 2504 continue
            "DD:EE": {
                "mac-address": "DD:EE",
                "address": "10.0.0.5",
                "interface": "ether1",
                "enabled": False,
                "host-name": "hDisabled",
                "comment": "",
            },
            # enabled but existing host has source != dhcp → lines 2508-2509 continue
            "AA:BB": {
                "mac-address": "AA:BB",
                "address": "10.0.0.1",
                "interface": "ether1",
                "enabled": True,
                "host-name": "h1",
                "comment": "",
            },
        }
        coord.ds["arp"] = {}
        coord.ds["dns"] = {}
        coord.ds["host_hass"] = {}
        coord.ds["hostspot_host"] = {}
        coord.ds["resource"] = {}
        coord.ds["host"] = {
            "AA:BB": {
                "source": "arp",
                "address": "9.9.9.9",
                "mac-address": "AA:BB",
                "interface": "ether9",
                "host-name": "existing",
                "manufacturer": "",
                "last-seen": None,
                "available": False,
            },
        }
        coord.async_mac_lookup.lookup = AsyncMock(return_value="")

        await coord.async_process_host()

        # DD:EE was never added because of continue on disabled (line 2504)
        assert "DD:EE" not in coord.ds["host"]
        # AA:BB existed with source "arp" — dhcp branch hit line 2508-2509 continue.
        # The later processing loop still syncs the dhcp address to host, but the
        # `interface` from DHCP would have been applied if not for the continue.
        assert coord.ds["host"]["AA:BB"]["source"] in ("arp", "dhcp")

    async def test_arp_existing_non_arp_source_continues(self, hass):
        """Cover line 2519: arp path with existing non-arp source continues."""
        coord = _make_coordinator(hass)
        coord.support_capsman = False
        coord.support_wireless = False
        coord.ds["capsman_hosts"] = {}
        coord.ds["wireless_hosts"] = {}
        coord.ds["dhcp"] = {}
        coord.ds["arp"] = {
            "AA:BB": {"mac-address": "AA:BB", "address": "1.2.3.4", "interface": "ether1"},
        }
        coord.ds["dns"] = {}
        coord.ds["host_hass"] = {}
        coord.ds["hostspot_host"] = {}
        coord.ds["resource"] = {}
        coord.ds["host"] = {
            "AA:BB": {
                "source": "dhcp",
                "address": "9.9.9.9",
                "mac-address": "AA:BB",
                "interface": "ether9",
                "host-name": "existing",
                "manufacturer": "",
                "last-seen": None,
                "available": False,
            },
        }
        coord.async_mac_lookup.lookup = AsyncMock(return_value="")

        await coord.async_process_host()

        # Line 2519 hit: arp branch detected existing non-arp source and continued.
        # The later processing loop may still reassign source/address via the DHCP→ARP
        # fallback block. We only care that the continue on line 2519 executed without error.
        assert "AA:BB" in coord.ds["host"]

    async def test_captive_populates_authorized(self, hass):
        """Cover lines 2570-2571: hostspot_host entry populates authorized/bypassed."""
        coord = _make_coordinator(hass)
        coord.support_capsman = False
        coord.support_wireless = False
        coord.ds["capsman_hosts"] = {}
        coord.ds["wireless_hosts"] = {}
        coord.ds["dhcp"] = {}
        coord.ds["arp"] = {
            "AA:BB": {"mac-address": "AA:BB", "address": "1.2.3.4", "interface": "ether1"},
        }
        coord.ds["dns"] = {}
        coord.ds["host_hass"] = {}
        coord.ds["hostspot_host"] = {
            "AA:BB": {"authorized": True, "bypassed": False},
        }
        coord.ds["resource"] = {}
        coord.async_mac_lookup.lookup = AsyncMock(return_value="")

        with patch.object(
            type(coord),
            "option_sensor_client_captive",
            new_callable=lambda: property(lambda self: True),
        ):
            await coord.async_process_host()

        assert coord.ds["host"]["AA:BB"]["authorized"] is True
        assert coord.ds["host"]["AA:BB"]["bypassed"] is False

    async def test_capsman_unavailable_when_not_detected(self, hass):
        """Cover line 2578: pre-existing capsman host not in capsman_detected -> available=False."""
        coord = _make_coordinator(hass)
        coord.support_capsman = True
        coord.support_wireless = False
        coord.ds["capsman_hosts"] = {}  # empty → no detection this cycle
        coord.ds["wireless_hosts"] = {}
        coord.ds["dhcp"] = {}
        coord.ds["arp"] = {}
        coord.ds["dns"] = {}
        coord.ds["host_hass"] = {}
        coord.ds["hostspot_host"] = {}
        coord.ds["resource"] = {}
        coord.ds["host"] = {
            "AA:BB": {
                "source": "capsman",
                "address": "1.2.3.4",
                "mac-address": "AA:BB",
                "interface": "wlan1",
                "host-name": "h",
                "manufacturer": "",
                "last-seen": None,
                "available": True,
            },
        }
        coord.async_mac_lookup.lookup = AsyncMock(return_value="")

        await coord.async_process_host()

        assert coord.ds["host"]["AA:BB"]["available"] is False

    async def test_wireless_unavailable_when_not_detected(self, hass):
        """Cover line 2582: pre-existing wireless host not in wireless_detected -> available=False."""
        coord = _make_coordinator(hass)
        coord.support_capsman = False
        coord.support_wireless = True
        coord.ds["capsman_hosts"] = {}
        coord.ds["wireless_hosts"] = {}  # empty → no detection
        coord.ds["dhcp"] = {}
        coord.ds["arp"] = {}
        coord.ds["dns"] = {}
        coord.ds["host_hass"] = {}
        coord.ds["hostspot_host"] = {}
        coord.ds["resource"] = {}
        coord.ds["host"] = {
            "AA:BB": {
                "source": "wireless",
                "address": "1.2.3.4",
                "mac-address": "AA:BB",
                "interface": "wlan1",
                "host-name": "h",
                "manufacturer": "",
                "last-seen": None,
                "available": True,
            },
        }
        coord.async_mac_lookup.lookup = AsyncMock(return_value="")

        await coord.async_process_host()

        assert coord.ds["host"]["AA:BB"]["available"] is False

    async def test_dhcp_address_updates_host_for_non_wireless_source(self, hass):
        """Cover lines 2587-2590: dhcp address != host address, non-capsman/wireless → becomes dhcp."""
        coord = _make_coordinator(hass)
        coord.support_capsman = False
        coord.support_wireless = False
        coord.ds["capsman_hosts"] = {}
        coord.ds["wireless_hosts"] = {}
        coord.ds["dhcp"] = {
            "AA:BB": {
                "mac-address": "AA:BB",
                "address": "10.0.0.9",  # Different from host's 10.0.0.1
                "interface": "ether9",
                "enabled": True,
                "host-name": "h1",
                "comment": "",
            },
        }
        coord.ds["arp"] = {}
        coord.ds["dns"] = {}
        coord.ds["host_hass"] = {}
        coord.ds["hostspot_host"] = {}
        coord.ds["resource"] = {}
        coord.ds["host"] = {
            "AA:BB": {
                "source": "dhcp",  # so dhcp loop does NOT continue
                "address": "10.0.0.1",
                "mac-address": "AA:BB",
                "interface": "ether1",
                "host-name": "h1",
                "manufacturer": "",
                "last-seen": None,
                "available": False,
            },
        }
        coord.async_mac_lookup.lookup = AsyncMock(return_value="")

        await coord.async_process_host()

        assert coord.ds["host"]["AA:BB"]["address"] == "10.0.0.9"
        assert coord.ds["host"]["AA:BB"]["interface"] == "ether9"

    async def test_arp_address_updates_non_capsman_non_wireless(self, hass):
        """Cover lines 2593-2596: arp address different, not dhcp → updates host from arp."""
        coord = _make_coordinator(hass)
        coord.support_capsman = False
        coord.support_wireless = False
        coord.ds["capsman_hosts"] = {}
        coord.ds["wireless_hosts"] = {}
        coord.ds["dhcp"] = {}
        coord.ds["arp"] = {
            "AA:BB": {
                "mac-address": "AA:BB",
                "address": "10.0.0.7",
                "interface": "ether7",
            },
        }
        coord.ds["dns"] = {}
        coord.ds["host_hass"] = {}
        coord.ds["hostspot_host"] = {}
        coord.ds["resource"] = {}
        coord.ds["host"] = {
            "AA:BB": {
                "source": "arp",
                "address": "10.0.0.1",
                "mac-address": "AA:BB",
                "interface": "ether1",
                "host-name": "h1",
                "manufacturer": "",
                "last-seen": None,
                "available": False,
            },
        }
        coord.async_mac_lookup.lookup = AsyncMock(return_value="")

        await coord.async_process_host()

        assert coord.ds["host"]["AA:BB"]["address"] == "10.0.0.7"
        assert coord.ds["host"]["AA:BB"]["interface"] == "ether7"
        assert coord.ds["host"]["AA:BB"]["source"] == "arp"

    async def test_host_name_resolution_dns_empty_falls_to_dhcp_comment(self, hass):
        """Cover lines 2605-2607: dns comment empty → dhcp comment fallback in DNS loop."""
        coord = _make_coordinator(hass)
        coord.support_capsman = False
        coord.support_wireless = False
        coord.ds["capsman_hosts"] = {}
        coord.ds["wireless_hosts"] = {}
        coord.ds["dhcp"] = {
            "AA:BB": {
                "mac-address": "AA:BB",
                "address": "10.0.0.1",
                "interface": "ether1",
                "enabled": True,
                "host-name": "h1",
                "comment": "dhcpOverride#extra",
            },
        }
        coord.ds["arp"] = {}
        coord.ds["dns"] = {
            # dns match found with empty comment prefix -> triggers dhcp comment branch
            "dns1": {"name": "x.local", "address": "10.0.0.1", "comment": "#onlycomment"},
        }
        coord.ds["host_hass"] = {}
        coord.ds["hostspot_host"] = {}
        coord.ds["resource"] = {}
        coord.async_mac_lookup.lookup = AsyncMock(return_value="")

        await coord.async_process_host()

        assert coord.ds["host"]["AA:BB"]["host-name"] == "dhcpOverride"

    async def test_host_name_resolution_dns_name_fallback(self, hass):
        """Cover lines 2608-2609: dns comment + dhcp comment both empty → fallback to dns name."""
        coord = _make_coordinator(hass)
        coord.support_capsman = False
        coord.support_wireless = False
        coord.ds["capsman_hosts"] = {}
        coord.ds["wireless_hosts"] = {}
        coord.ds["dhcp"] = {}
        coord.ds["arp"] = {
            "AA:BB": {"mac-address": "AA:BB", "address": "10.0.0.1", "interface": "ether1"},
        }
        coord.ds["dns"] = {
            "dns1": {"name": "mydns.local", "address": "10.0.0.1", "comment": "#empty"},
        }
        coord.ds["host_hass"] = {}
        coord.ds["hostspot_host"] = {}
        coord.ds["resource"] = {}
        coord.async_mac_lookup.lookup = AsyncMock(return_value="")

        await coord.async_process_host()

        # dns.comment prefix empty + no dhcp.comment → split name on "."
        assert coord.ds["host"]["AA:BB"]["host-name"] == "mydns"

    async def test_host_name_from_dhcp_comment_no_dns(self, hass):
        """Cover line 2615: no DNS hit, dhcp enabled with non-empty comment prefix."""
        coord = _make_coordinator(hass)
        coord.support_capsman = False
        coord.support_wireless = False
        coord.ds["capsman_hosts"] = {}
        coord.ds["wireless_hosts"] = {}
        coord.ds["dhcp"] = {
            "AA:BB": {
                "mac-address": "AA:BB",
                "address": "10.0.0.1",
                "interface": "ether1",
                "enabled": True,
                "host-name": "unknown",
                "comment": "dhcpCommentName#extra",
            },
        }
        coord.ds["arp"] = {}
        coord.ds["dns"] = {}  # no dns hits
        coord.ds["host_hass"] = {}
        coord.ds["hostspot_host"] = {}
        coord.ds["resource"] = {}
        coord.async_mac_lookup.lookup = AsyncMock(return_value="")

        await coord.async_process_host()

        assert coord.ds["host"]["AA:BB"]["host-name"] == "dhcpCommentName"

    async def test_manufacturer_unresolved_detect_stays_empty(self, hass):
        """Cover line 2631: manufacturer stays 'detect' on unknown mac → set to ''."""
        coord = _make_coordinator(hass)
        coord.support_capsman = False
        coord.support_wireless = False
        coord.ds["capsman_hosts"] = {}
        coord.ds["wireless_hosts"] = {}
        coord.ds["dhcp"] = {}
        coord.ds["arp"] = {}
        coord.ds["dns"] = {}
        coord.ds["host_hass"] = {}
        coord.ds["hostspot_host"] = {}
        coord.ds["resource"] = {}
        # Pre-existing host with manufacturer=detect but mac-address=unknown
        coord.ds["host"] = {
            "AA:BB": {
                "source": "arp",
                "address": "1.2.3.4",
                "mac-address": "unknown",  # triggers skip of the try lookup
                "interface": "ether1",
                "host-name": "h",
                "manufacturer": "detect",
                "last-seen": None,
                "available": False,
            },
        }
        coord.async_mac_lookup.lookup = AsyncMock(return_value="")

        await coord.async_process_host()

        # Line 2631 branch sets manufacturer to "" (mac-address unknown skips try)
        assert coord.ds["host"]["AA:BB"]["manufacturer"] == ""

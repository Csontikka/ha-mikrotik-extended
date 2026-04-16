"""Tests for the update platform."""

from unittest.mock import AsyncMock, MagicMock, patch

from homeassistant.const import (
    CONF_HOST,
    CONF_NAME,
    CONF_PASSWORD,
    CONF_PORT,
    CONF_SSL,
    CONF_USERNAME,
    CONF_VERIFY_SSL,
)
from packaging.version import Version
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.mikrotik_extended.const import DOMAIN
from custom_components.mikrotik_extended.update import (
    MikrotikRouterBoardFWUpdate,
    MikrotikRouterOSUpdate,
    async_setup_entry,
    decrement_version,
    fetch_changelog,
    generate_version_list,
)

ENTRY_DATA = {
    CONF_HOST: "192.168.88.1",
    CONF_USERNAME: "admin",
    CONF_PASSWORD: "test",
    CONF_PORT: 0,
    CONF_SSL: False,
    CONF_VERIFY_SSL: False,
    CONF_NAME: "TestRouter",
}


def _make_description(
    func="MikrotikRouterOSUpdate",
    key="k",
    name="N",
    data_path="firmware",
    data_attribute="available",
    title="TestTitle",
    **extra,
):
    desc = MagicMock()
    desc.func = func
    desc.key = key
    desc.name = name
    desc.data_path = data_path
    desc.data_attribute = data_attribute
    desc.data_reference = None
    desc.data_name = None
    desc.data_name_comment = False
    desc.data_attributes_list = []
    desc.ha_group = None
    desc.ha_connection = None
    desc.ha_connection_value = None
    desc.entity_registry_enabled_default = True
    desc.title = title
    for k, v in extra.items():
        setattr(desc, k, v)
    return desc


def _make_coordinator(hass, data):
    entry = MockConfigEntry(
        domain=DOMAIN,
        data=ENTRY_DATA,
        options={},
        unique_id="192.168.88.1",
    )
    entry.add_to_hass(hass)
    coord = MagicMock()
    coord.config_entry = entry
    coord.data = data
    coord.host = "192.168.88.1"
    coord.execute = MagicMock()
    return coord


# --- pure function tests (no hass needed, but module-level imports register the package) ---


def test_decrement_version_micro_minor_major():
    """decrement_version chooses micro→minor→major path."""
    assert decrement_version(Version("7.14.3"), Version("7.14.0")) == Version("7.14.2")
    assert decrement_version(Version("7.10.0"), Version("0.0.0")) == Version("7.9.999")
    assert decrement_version(Version("7.0.0"), Version("0.0.0")) == Version("6.999.999")


def test_generate_version_list_reverse_returns_end():
    """start > end → [end]."""
    result = generate_version_list("7.12", "7.10")
    assert result == ["7.10"]


def test_generate_version_list_patch_range_descending():
    """Normal descent walks from end down to start."""
    result = generate_version_list("7.14.0", "7.14.3")
    assert result[0] == "7.14.3"
    assert result[-1] == "7.14.0"
    assert "7.14.1" in result


def test_generate_version_list_caps_at_50():
    """generate_version_list terminates at 50 entries maximum."""
    result = generate_version_list("6.0.0", "7.0.0")
    assert "7.0.0" in result
    assert len(result) <= 50


async def test_fetch_changelog_success_404_and_exception():
    """fetch_changelog: 200 returns reformatted text, 404/error return empty string."""
    # 200 OK
    response = MagicMock()
    response.status = 200
    response.text = AsyncMock(return_value="*) bug fix\n*) feature")
    response.__aenter__ = AsyncMock(return_value=response)
    response.__aexit__ = AsyncMock(return_value=False)
    session = MagicMock()
    session.get = MagicMock(return_value=response)
    text = await fetch_changelog(session, "7.12")
    assert "- bug fix" in text
    assert "- feature" in text

    # 404
    response.status = 404
    text2 = await fetch_changelog(session, "7.12")
    assert text2 == ""

    # Exception
    session2 = MagicMock()
    session2.get = MagicMock(side_effect=RuntimeError("boom"))
    text3 = await fetch_changelog(session2, "7.12")
    assert text3 == ""


# --- entity tests ---


async def test_async_setup_entry_dispatcher(hass):
    """async_setup_entry forwards the two update classes."""
    entry = MagicMock()
    with patch("custom_components.mikrotik_extended.update.async_add_entities", new=AsyncMock()) as mock_add:
        await async_setup_entry(hass, entry, MagicMock())
    _, _, dispatcher = mock_add.await_args.args
    assert set(dispatcher.keys()) == {"MikrotikRouterOSUpdate", "MikrotikRouterBoardFWUpdate"}


async def test_router_os_update_properties_and_install(hass):
    """MikrotikRouterOSUpdate: properties reflect _data; install hits coordinator.execute (with/without backup)."""
    desc = _make_description(func="MikrotikRouterOSUpdate", data_attribute="available")
    firmware = {"available": True, "installed-version": "7.10", "latest-version": "7.12"}
    coord = _make_coordinator(hass, {"firmware": firmware})
    entity = MikrotikRouterOSUpdate(coord, desc)

    assert entity.is_on is True
    assert entity.installed_version == "7.10"
    assert entity.latest_version == "7.12"
    assert entity.release_url == "https://mikrotik.com/download/changelogs"

    await entity.options_updated()

    # Install without backup
    await entity.async_install("7.12", False)
    coord.execute.assert_called_once_with("/system/package/update", "install", None, None)

    # Install with backup → extra backup call
    coord.execute.reset_mock()
    await entity.async_install("7.12", True)
    assert coord.execute.call_count == 2
    coord.execute.assert_any_call("/system/backup", "save", None, None)


async def test_router_os_release_notes_success_and_failure(hass):
    """async_release_notes: concatenates changelog fetches; returns fallback on error."""
    desc = _make_description(func="MikrotikRouterOSUpdate", data_attribute="available")
    firmware = {"available": True, "installed-version": "7.10.0", "latest-version": "7.10.1"}
    coord = _make_coordinator(hass, {"firmware": firmware})
    entity = MikrotikRouterOSUpdate(coord, desc)
    entity.hass = hass

    with (
        patch(
            "custom_components.mikrotik_extended.update.fetch_changelog",
            new=AsyncMock(return_value="*) line one"),
        ),
        patch(
            "custom_components.mikrotik_extended.update.async_get_clientsession",
            return_value=MagicMock(),
        ),
    ):
        notes = await entity.async_release_notes()
        assert "- line one" in notes

    with patch(
        "custom_components.mikrotik_extended.update.async_get_clientsession",
        side_effect=RuntimeError("boom"),
    ):
        notes_err = await entity.async_release_notes()
        assert "Error fetching" in notes_err


async def test_router_board_fw_update_properties_and_install(hass):
    """MikrotikRouterBoardFWUpdate: is_on, install runs upgrade+reboot, matching versions → False."""
    desc = _make_description(func="MikrotikRouterBoardFWUpdate", data_path="routerboard", data_attribute="enabled")
    rb = {"current-firmware": "7.10", "upgrade-firmware": "7.12"}
    coord = _make_coordinator(hass, {"routerboard": rb})
    entity = MikrotikRouterBoardFWUpdate(coord, desc)

    assert entity.is_on is True
    assert entity.installed_version == "7.10"
    assert entity.latest_version == "7.12"

    await entity.options_updated()

    await entity.async_install("7.12", False)
    assert coord.execute.call_count == 2
    coord.execute.assert_any_call("/system/routerboard", "upgrade", None, None)
    coord.execute.assert_any_call("/system", "reboot", None, None)

    # Matching versions → is_on False
    coord.data["routerboard"] = {"current-firmware": "7.10", "upgrade-firmware": "7.10"}
    assert entity.is_on is False

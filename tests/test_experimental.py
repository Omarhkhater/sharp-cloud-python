"""Experimental FA-frame controls stay hidden until the options toggle is on."""

from __future__ import annotations

import time
from unittest.mock import AsyncMock, patch

import aiohttp
from homeassistant.components.climate import ClimateEntityFeature
from homeassistant.const import PRECISION_HALVES, EntityCategory
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.sharp_cocoro_air.client import SharpAPIError, SharpClient
from custom_components.sharp_cocoro_air.const import CONF_EXPERIMENTAL, DOMAIN, PAIR_BACKOFF_SECONDS
from custom_components.sharp_cocoro_air.coordinator import SharpCoordinator
from custom_components.sharp_cocoro_air.switch import SWITCHES

COOKIES = [
    {
        "name": "JSESSIONID",
        "value": "session-value",
        "domain": "eg-hms.cloudlabs.sharp.co.jp",
        "path": "/",
    }
]


def test_whole_degrees_round_half_up():
    """22.5 becomes 23. Banker's rounding would leave it at 22."""
    from custom_components.sharp_cocoro_air.climate import snap_whole_degree

    assert snap_whole_degree(22.5) == 23
    assert snap_whole_degree(21.4) == 21
    assert snap_whole_degree(21.5) == 22


def test_cocoro_switch_is_config_and_experimental():
    """Cocoro is a config entity and is not created unless the option is on."""
    cocoro = next(item for item in SWITCHES if item.key == "cocoro_air")
    plasma = next(item for item in SWITCHES if item.key == "plasmacluster")
    assert cocoro.entity_category is EntityCategory.CONFIG
    assert cocoro.experimental is True
    assert plasma.experimental is False


async def test_experimental_controls_are_off_until_enabled(hass, boxinfo):
    """Vanes and half degrees are on. Soft stays hidden until the option is on."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id="user-1",
        title="Sharp Air Control",
        data={
            "terminal_app_id": "https://example.invalid/terminal/1",
            "user_id": "user-1",
            "cookies": COOKIES,
        },
    )
    entry.add_to_hass(hass)
    registry = er.async_get(hass)
    registry.async_get_or_create(
        "climate",
        DOMAIN,
        "1|013001",
        suggested_object_id="living_room_ac",
        config_entry=entry,
    )
    registry.async_get_or_create(
        "switch",
        DOMAIN,
        "1|013001_sleep",
        suggested_object_id="living_room_ac_sleep",
        config_entry=entry,
    )
    registry.async_get_or_create(
        "switch",
        DOMAIN,
        "1|013001_speed",
        suggested_object_id="living_room_ac_speed",
        config_entry=entry,
    )
    with (
        patch(
            "custom_components.sharp_cocoro_air.client.SharpClient.box_info",
            new=AsyncMock(return_value=boxinfo),
        ),
        patch(
            "custom_components.sharp_cocoro_air.client.SharpClient.pair",
            new=AsyncMock(),
        ) as pair,
    ):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

        climate = _climate(hass, "1|013001")
        assert climate.entity_id == "climate.living_room_ac"
        assert climate.precision == PRECISION_HALVES
        assert climate.target_temperature_step == PRECISION_HALVES
        assert climate.min_temp == 16
        assert climate.max_temp == 30
        assert ClimateEntityFeature.SWING_MODE in climate.supported_features
        assert ClimateEntityFeature.SWING_HORIZONTAL_MODE in climate.supported_features
        assert climate.swing_modes is not None
        assert "down" in climate.swing_modes
        assert climate.swing_horizontal_modes == ["straight", "left", "right", "swing"]
        assert ClimateEntityFeature.FAN_MODE in climate.supported_features
        assert ClimateEntityFeature.TARGET_TEMPERATURE in climate.supported_features
        assert "rapid" not in climate.fan_modes

        unique_ids = _unique_ids(hass, entry.entry_id)
        assert "1|013001_plasmacluster" in unique_ids
        assert "1|013001_super_jet" in unique_ids
        assert "1|013001_sleep" not in unique_ids
        assert "1|013001_speed" not in unique_ids
        assert "1|013001_soft" not in unique_ids
        super_jet = next(
            entity
            for entity in hass.data["binary_sensor"].entities
            if entity.unique_id == "1|013001_super_jet"
        )
        assert super_jet.entity_category is None
        assert super_jet.entity_registry_enabled_default is True
        assert super_jet.is_on is False
        fault = next(
            entity
            for entity in hass.data["binary_sensor"].entities
            if entity.unique_id == "1|013001_fault"
        )
        assert fault.entity_category is EntityCategory.DIAGNOSTIC

        coordinator = entry.runtime_data
        coordinator.client.restore_cookies(
            [
                *COOKIES,
                {
                    "name": "AWSALBAPP-0",
                    "value": "rotated",
                    "domain": "eg-hms.cloudlabs.sharp.co.jp",
                    "path": "/",
                },
            ]
        )
        coordinator._persist_cookies()
        assert entry.data["cookies"] == COOKIES

        coordinator.client.restore_cookies(
            [
                {
                    "name": "JSESSIONID",
                    "value": "new-session",
                    "domain": "eg-hms.cloudlabs.sharp.co.jp",
                    "path": "/",
                }
            ]
        )
        coordinator._persist_cookies()
        await hass.async_block_till_done()
        stored = {cookie["name"]: cookie["value"] for cookie in entry.data["cookies"]}
        assert stored["JSESSIONID"] == "new-session"
        assert coordinator.tracked_options == {}

        hass.config_entries.async_update_entry(entry, options={CONF_EXPERIMENTAL: True})
        await hass.async_block_till_done()

        climate = _climate(hass, "1|013001")
        assert climate.entity_id == "climate.living_room_ac"
        assert climate.precision == PRECISION_HALVES
        assert "rapid" not in climate.fan_modes
        assert climate.swing_horizontal_modes == ["straight", "left", "right", "swing"]
        unique_ids = _unique_ids(hass, entry.entry_id)
        assert "1|013001_sleep" not in unique_ids
        assert "1|013001_speed" not in unique_ids
        assert "1|013001_soft" not in unique_ids
        assert "1|013001_cocoro_air" not in unique_ids

        hass.config_entries.async_update_entry(entry, options={CONF_EXPERIMENTAL: False})
        await hass.async_block_till_done()
        unique_ids = _unique_ids(hass, entry.entry_id)
        assert "1|013001_sleep" not in unique_ids
        assert "1|013001_speed" not in unique_ids
        assert "1|013001_plasmacluster" in unique_ids
        assert "1|013001_super_jet" in unique_ids
        assert _climate(hass, "1|013001").entity_id == "climate.living_room_ac"
        assert "rapid" not in _climate(hass, "1|013001").fan_modes

    pair.assert_awaited()


async def test_rejected_pairing_is_not_retried_on_later_polls(hass, boxinfo):
    """Four polls of three rejected boxes pair three times, not twelve."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={"terminal_app_id": "https://example.invalid/terminal/1", "cookies": []},
    )
    entry.add_to_hass(hass)
    connector = aiohttp.TCPConnector(resolver=aiohttp.resolver.ThreadedResolver())
    session = aiohttp.ClientSession(connector=connector, cookie_jar=aiohttp.CookieJar(unsafe=True))
    client = SharpClient(session, "https://example.invalid/terminal/1")
    calls: list[str] = []

    async def reject(box_id: str) -> None:
        calls.append(box_id)
        raise SharpAPIError(400)

    client.pair = reject  # type: ignore[method-assign]
    client.box_info = AsyncMock(return_value=boxinfo)  # type: ignore[method-assign]
    coordinator = SharpCoordinator(hass, entry, client)
    try:
        for _ in range(4):
            await coordinator._async_update_data()
        assert len(calls) == 3
        assert len(set(calls)) == 3

        client._pair_attempted = {
            box_id: time.monotonic() - PAIR_BACKOFF_SECONDS - 1 for box_id in set(calls)
        }
        await coordinator._async_update_data()
        assert len(calls) == 6
        await coordinator._async_update_data()
        assert len(calls) == 6
    finally:
        await client.aclose()


def _soft_box() -> dict:
    """One unit whose capability bytes say Soft exists. Fan pattern 0x03 hides Rapid."""
    fa = bytearray(80)
    capabilities = bytearray(32)
    capabilities[4] = 0x21
    capabilities[8] = 0x20
    capabilities[10] = 0x01
    capabilities[11] = 0x02
    capabilities[12] = 0x03
    capabilities[29] = 0x38
    props = (
        (0x80, bytes([0x30])),
        (0xB0, bytes([0x42])),
        (0xF0, bytes(capabilities)),
        (0xFA, bytes(fa)),
    )
    body = bytearray([0x01, 0x30, 0x01, 0x05, 0xFF, 0x01, 0x72, len(props)])
    for epc, edt in props:
        body.append(epc)
        body.append(len(edt))
        body.extend(edt)
    return {
        "devices": [
            {
                "boxId": "box-1",
                "deviceId": 1,
                "echonetNode": "00-11-22-33-44-55",
                "echonetObject": "013001",
                "model": "AY-XP12YHE",
                "labelData": {"deviceType": "AIR_CON", "name": "Living Room"},
                "echonetProperty": body.hex(),
            }
        ]
    }


async def test_untested_switch_keeps_its_entity_id(hass):
    """A Soft switch that was already registered keeps that entity id."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={
            "terminal_app_id": "https://example.invalid/terminal/1",
            "user_id": "user-1",
            "cookies": COOKIES,
        },
        options={CONF_EXPERIMENTAL: True},
    )
    entry.add_to_hass(hass)
    registry = er.async_get(hass)
    registry.async_get_or_create(
        "switch",
        DOMAIN,
        "1|013001_soft",
        suggested_object_id="old_soft_name",
        config_entry=entry,
    )
    registry.async_get_or_create(
        "switch",
        DOMAIN,
        "1|013001_sleep",
        suggested_object_id="old_sleep_name",
        config_entry=entry,
    )
    with (
        patch(
            "custom_components.sharp_cocoro_air.client.SharpClient.box_info",
            new=AsyncMock(return_value=_soft_box()),
        ),
        patch(
            "custom_components.sharp_cocoro_air.client.SharpClient.pair",
            new=AsyncMock(),
        ),
    ):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

    soft = registry.async_get_entity_id("switch", DOMAIN, "1|013001_soft")
    assert soft == "switch.old_soft_name"
    assert registry.async_get_entity_id("switch", DOMAIN, "1|013001_sleep") is None
    assert registry.async_get_entity_id("switch", DOMAIN, "1|013001_speed") is None
    climate = _climate(hass, "1|013001")
    assert "rapid" not in climate.fan_modes
    assert "sleep" not in {item.key for item in SWITCHES}
    assert "speed" not in {item.key for item in SWITCHES}


def _climate(hass, unique_id):
    return next(entity for entity in hass.data["climate"].entities if entity.unique_id == unique_id)


def _unique_ids(hass, entry_id) -> set[str]:
    registry = er.async_get(hass)
    return {
        entity.unique_id
        for entity in er.async_entries_for_config_entry(registry, entry_id)
        if entity.unique_id
    }

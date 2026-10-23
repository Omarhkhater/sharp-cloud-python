"""Experimental FA-frame controls stay hidden until the options toggle is on."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

from homeassistant.components.climate import ClimateEntityFeature
from homeassistant.const import PRECISION_HALVES, PRECISION_WHOLE, EntityCategory
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.sharp_cocoro_air.const import CONF_EXPERIMENTAL, DOMAIN
from custom_components.sharp_cocoro_air.switch import SWITCHES

COOKIES = [
    {
        "name": "JSESSIONID",
        "value": "session-value",
        "domain": "eg-hms.cloudlabs.sharp.co.jp",
        "path": "/",
    }
]


def test_cocoro_switch_is_config_and_experimental():
    """Cocoro is a config entity and is not created unless the option is on."""
    cocoro = next(item for item in SWITCHES if item.key == "cocoro_air")
    plasma = next(item for item in SWITCHES if item.key == "plasmacluster")
    assert cocoro.entity_category is EntityCategory.CONFIG
    assert cocoro.experimental is True
    assert plasma.experimental is False


async def test_experimental_controls_are_off_until_enabled(hass, boxinfo):
    """Default setup is whole degrees and no FA switches. The option reveals them."""
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
        assert climate.precision == PRECISION_WHOLE
        assert climate.target_temperature_step == 1
        assert climate.min_temp == 16
        assert climate.max_temp == 30
        assert ClimateEntityFeature.SWING_MODE not in climate.supported_features
        assert ClimateEntityFeature.SWING_HORIZONTAL_MODE not in climate.supported_features
        assert climate.swing_modes is None
        assert ClimateEntityFeature.FAN_MODE in climate.supported_features
        assert ClimateEntityFeature.TARGET_TEMPERATURE in climate.supported_features

        unique_ids = _unique_ids(hass, entry.entry_id)
        assert "1|013001_plasmacluster" in unique_ids
        assert "1|013001_sleep" not in unique_ids
        assert "1|013001_soft" not in unique_ids
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
        assert climate.precision == PRECISION_HALVES
        assert climate.target_temperature_step == PRECISION_HALVES
        assert ClimateEntityFeature.SWING_MODE in climate.supported_features
        assert ClimateEntityFeature.SWING_HORIZONTAL_MODE in climate.supported_features
        assert climate.swing_modes is not None
        unique_ids = _unique_ids(hass, entry.entry_id)
        assert "1|013001_sleep" in unique_ids
        assert "1|013001_soft" not in unique_ids
        assert "1|013001_speed" not in unique_ids
        assert "1|013001_cocoro_air" not in unique_ids

        hass.config_entries.async_update_entry(entry, options={CONF_EXPERIMENTAL: False})
        await hass.async_block_till_done()
        unique_ids = _unique_ids(hass, entry.entry_id)
        assert "1|013001_sleep" not in unique_ids
        assert "1|013001_plasmacluster" in unique_ids

    pair.assert_awaited()


def _climate(hass, unique_id):
    return next(entity for entity in hass.data["climate"].entities if entity.unique_id == unique_id)


def _unique_ids(hass, entry_id) -> set[str]:
    registry = er.async_get(hass)
    return {
        entity.unique_id
        for entity in er.async_entries_for_config_entry(registry, entry_id)
        if entity.unique_id
    }

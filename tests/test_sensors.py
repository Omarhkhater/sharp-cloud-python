"""Room and humidity sensors, and the outdoor reading held while the unit is off."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

from homeassistant.components.climate import HVACMode
from homeassistant.const import UnitOfTemperature
from homeassistant.core import State
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    mock_restore_cache_with_extra_data,
)

from custom_components.sharp_cocoro_air import coordinator as coordinator_mod
from custom_components.sharp_cocoro_air.const import DOMAIN, OUTDOOR_HOLD_S
from custom_components.sharp_cocoro_air.coordinator import SharpCoordinator
from custom_components.sharp_cocoro_air.protocol import Aircon
from custom_components.sharp_cocoro_air.sensor import SENSORS, SharpOutdoorSensor

_COOKIES = [
    {
        "name": "JSESSIONID",
        "value": "session-value",
        "domain": "eg-hms.cloudlabs.sharp.co.jp",
        "path": "/",
    }
]


class _Clock:
    def __init__(self) -> None:
        self.now = 1_000.0

    def monotonic(self) -> float:
        return self.now


class _Cloud:
    def __init__(self, payload: dict) -> None:
        self.payload = payload

    async def box_info(self) -> dict:
        return self.payload

    async def pair_unlisted(self, payload: dict) -> None:
        return None

    def export_cookies(self) -> list[dict]:
        return []


def _payload(*, power: int, outdoor: int) -> dict:
    """boxInfo with power, a room temperature, humidity, and one outdoor byte."""
    props = ((0x80, power), (0xBB, 23), (0xBA, 40), (0xBE, outdoor))
    body = bytearray([0x01, 0x30, 0x01, 0x05, 0xFF, 0x01, 0x72, len(props)])
    for epc, value in props:
        body.extend((epc, 1, value))
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


def _coordinator(hass, cloud: _Cloud) -> SharpCoordinator:
    entry = MockConfigEntry(domain=DOMAIN, data={})
    entry.add_to_hass(hass)
    return SharpCoordinator(hass, entry, cloud)  # type: ignore[arg-type]


def _outdoor(coordinator: SharpCoordinator) -> SharpOutdoorSensor:
    description = next(item for item in SENSORS if item.key == "outdoor_temperature")
    return SharpOutdoorSensor(coordinator, "1|013001", description)


async def test_off_unit_keeps_room_temperature_and_humidity(hass, boxinfo):
    """An off unit still shows BB and BA on the climate entity and the new sensors."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={
            "terminal_app_id": "https://example.invalid/terminal/1",
            "user_id": "user-1",
            "cookies": _COOKIES,
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
        ),
    ):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

    climate = next(
        entity for entity in hass.data["climate"].entities if entity.unique_id == "2|013001"
    )
    assert climate.hvac_mode is HVACMode.OFF
    assert climate.current_temperature == 25
    assert climate.current_humidity == 55
    sensors = {
        entity.unique_id: entity
        for entity in hass.data["sensor"].entities
        if entity.unique_id and entity.unique_id.startswith("2|013001_")
    }
    assert sensors["2|013001_room_temperature"].native_value == 25
    assert sensors["2|013001_room_temperature"].suggested_display_precision == 0
    assert sensors["2|013001_humidity"].native_value == 55
    assert sensors["2|013001_room_temperature"].available is True
    assert sensors["2|013001_humidity"].available is True
    # The off unit's outdoor byte is 0x7E and there is no earlier reading to hold.
    assert sensors["2|013001_outdoor_temperature"].native_value is None
    assert sensors["2|013001_outdoor_temperature"].available is True


async def test_outdoor_holds_while_off_and_clears_when_it_expires_or_powers_on(hass, monkeypatch):
    """26 °C while on, then 0x7E while off, stays 26 until the hold ends."""
    clock = _Clock()
    monkeypatch.setattr(coordinator_mod, "_monotonic", clock.monotonic)
    cloud = _Cloud(_payload(power=0x30, outdoor=26))
    coordinator = _coordinator(hass, cloud)
    try:
        data = await coordinator._async_update_data()
        coordinator.data = data
        device = data["1|013001"]
        assert device.outdoor_temp == 26
        assert device.outdoor_stale is False

        cloud.payload = _payload(power=0x31, outdoor=0x7E)
        data = await coordinator._async_update_data()
        coordinator.data = data
        device = data["1|013001"]
        assert device.outdoor_temp == 26
        assert device.outdoor_stale is True
        sensor = _outdoor(coordinator)
        assert sensor.native_value == 26
        assert sensor.extra_state_attributes["stale"] is True
        assert sensor.available is True

        clock.now += OUTDOOR_HOLD_S
        data = await coordinator._async_update_data()
        coordinator.data = data
        device = data["1|013001"]
        assert device.outdoor_temp is None
        assert device.outdoor_stale is False
        assert sensor.native_value is None
        assert "stale" not in sensor.extra_state_attributes

        cloud.payload = _payload(power=0x30, outdoor=18)
        data = await coordinator._async_update_data()
        coordinator.data = data
        device = data["1|013001"]
        assert device.outdoor_temp == 18
        assert device.outdoor_stale is False
        assert sensor.extra_state_attributes == {}

        cloud.payload = _payload(power=0x30, outdoor=0x7E)
        data = await coordinator._async_update_data()
        coordinator.data = data
        device = data["1|013001"]
        assert device.outdoor_temp is None
        assert device.outdoor_stale is False
        assert sensor.native_value is None
    finally:
        await coordinator.async_shutdown()


async def test_restart_while_off_restores_the_last_outdoor_value(hass):
    """The first poll after a restart is off and 0x7E, so the saved reading returns."""
    device = Aircon(
        box_id="box-1",
        device_id=1,
        echonet_node="00-11-22-33-44-55",
        echonet_object="013001",
        name="Living Room",
        model="AY-XP12YHE",
        power=False,
        outdoor_temp=None,
    )
    cloud = _Cloud({"devices": []})
    coordinator = _coordinator(hass, cloud)
    coordinator.data = {device.unique_key: device}
    entity_id = "sensor.living_room_outdoor_temperature"
    mock_restore_cache_with_extra_data(
        hass,
        [
            (
                State(entity_id, "26", {"unit_of_measurement": UnitOfTemperature.CELSIUS}),
                {"native_value": 26, "native_unit_of_measurement": UnitOfTemperature.CELSIUS},
            )
        ],
    )
    sensor = _outdoor(coordinator)
    sensor.hass = hass
    sensor.entity_id = entity_id
    try:
        await sensor.async_added_to_hass()
        assert sensor.native_value == 26
        assert sensor.extra_state_attributes["stale"] is True
        assert sensor.available is True
        assert coordinator._last_outdoor[device.unique_key][0] == 26
    finally:
        sensor._call_on_remove_callbacks()
        await coordinator.async_shutdown()

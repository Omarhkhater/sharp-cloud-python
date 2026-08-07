"""Climate platform for Sharp COCORO AIR."""

from __future__ import annotations

from homeassistant.components.climate import (
    ClimateEntity,
    ClimateEntityFeature,
    HVACMode,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import ATTR_TEMPERATURE, UnitOfTemperature
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import SharpCoordinator

HVAC_TO_SHARP = {
    HVACMode.AUTO: 0,
    HVACMode.COOL: 2,
    HVACMode.DRY: 3,
    HVACMode.FAN_ONLY: 4,
    HVACMode.HEAT: 5,
    HVACMode.OFF: 0,
}

SHARP_TO_HVAC = {v: k for k, v in HVAC_TO_SHARP.items()}


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Sharp climate entities."""

    coordinator: SharpCoordinator = hass.data[DOMAIN][entry.entry_id][
        "coordinator"
    ]

    async_add_entities(
        [
            SharpClimateEntity(coordinator, device)
            for device in coordinator.data
        ]
    )


class SharpClimateEntity(
    CoordinatorEntity,
    ClimateEntity,
):
    """Sharp Climate."""

    _attr_has_entity_name = True
    _attr_temperature_unit = UnitOfTemperature.CELSIUS

    _attr_supported_features = (
        ClimateEntityFeature.TARGET_TEMPERATURE
        | ClimateEntityFeature.FAN_MODE
        | ClimateEntityFeature.SWING_MODE
        | ClimateEntityFeature.TURN_ON
        | ClimateEntityFeature.TURN_OFF
    )

    _attr_hvac_modes = list(HVAC_TO_SHARP.keys())

    _attr_fan_modes = [
        "auto",
        "low",
        "medium",
        "high",
    ]

    _attr_swing_modes = [
        "off",
        "vertical",
        "horizontal",
        "both",
    ]

    _attr_min_temp = 16
    _attr_max_temp = 30
    _attr_target_temperature_step = 1

    def __init__(
        self,
        coordinator: SharpCoordinator,
        device: dict,
    ):
        super().__init__(coordinator)

        self._device = device

        self._attr_unique_id = device["boxId"]
        self._attr_name = device.get(
            "deviceName",
            "Sharp Air Conditioner",
        )

    @property
    def _status(self):
        return self._device.get("status", {})

    @property
    def device_info(self):
        return {
            "identifiers": {(DOMAIN, self._device["boxId"])},
            "manufacturer": "Sharp",
            "model": self._device.get("modelName", "COCORO AIR"),
            "name": self._attr_name,
            "sw_version": self._device.get("firmwareVersion"),
        }

    @property
    def extra_state_attributes(self):
        return {
            "box_id": self._device.get("boxId"),
            "terminal_app_id": self._device.get("terminalAppId"),
            "serial_number": self._device.get("serialNumber"),
            "online": self._status.get("online"),
            "properties": self._device.get("properties"),
            "raw_status": self._status,
        }

    @property
    def available(self):
        return bool(self._status)

    @property
    def current_temperature(self):
        return self._status.get("roomTemperature")

    @property
    def target_temperature(self):
        return self._status.get("targetTemperature")

    @property
    def hvac_mode(self):
        return SHARP_TO_HVAC.get(
            self._status.get("operationMode"),
            HVACMode.OFF,
        )

    @property
    def fan_mode(self):
        return self._status.get("fanSpeed")

    @property
    def swing_mode(self):
        return self._status.get("swingMode")

    async def _control(self, payload: dict):
        await self.hass.async_add_executor_job(
            self.coordinator.client.devices.control,
            self._device["terminalAppId"],
            self._device["boxId"],
            payload,
        )

        await self.coordinator.async_request_refresh()

    async def async_set_temperature(self, **kwargs):
        await self._control(
            {
                "power": 1,
                "operationMode": self._status.get("operationMode", 2),
                "temperature": kwargs[ATTR_TEMPERATURE],
            }
        )

    async def async_set_hvac_mode(self, hvac_mode):
        await self._control(
            {
                "power": 0 if hvac_mode == HVACMode.OFF else 1,
                "operationMode": HVAC_TO_SHARP[hvac_mode],
                "temperature": self.target_temperature,
                "fanSpeed": self.fan_mode,
                "swingMode": self.swing_mode,
            }
        )

    async def async_set_fan_mode(self, fan_mode):
        await self._control(
            {
                "power": 1,
                "operationMode": self._status.get("operationMode", 2),
                "temperature": self.target_temperature,
                "fanSpeed": fan_mode,
                "swingMode": self.swing_mode,
            }
        )

    async def async_set_swing_mode(self, swing_mode):
        await self._control(
            {
                "power": 1,
                "operationMode": self._status.get("operationMode", 2),
                "temperature": self.target_temperature,
                "fanSpeed": self.fan_mode,
                "swingMode": swing_mode,
            }
        )

    async def async_turn_on(self):
        await self.async_set_hvac_mode(HVACMode.COOL)

    async def async_turn_off(self):
        await self.async_set_hvac_mode(HVACMode.OFF)
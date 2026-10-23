"""One climate entity per Sharp indoor unit."""

from __future__ import annotations

from typing import ClassVar

from homeassistant.components.climate import (
    ATTR_HVAC_MODE,
    ATTR_TEMPERATURE,
    ClimateEntity,
    ClimateEntityFeature,
    HVACMode,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import PRECISION_HALVES, PRECISION_WHOLE, UnitOfTemperature
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import (
    DOMAIN,
    FAN_MODES,
    MANUFACTURER,
    MODE_AUTO,
    MODE_COOL,
    MODE_DRY,
    MODE_FAN,
    MODE_HEAT,
    TEMP_MAX,
    TEMP_MIN,
    VANE_HORIZONTAL,
    VANE_VERTICAL,
)
from .coordinator import SharpCoordinator
from .protocol import (
    Aircon,
    cmd_fan,
    cmd_mode,
    cmd_power,
    cmd_setpoint,
    cmd_vanes,
    mode_has_setpoint,
)

_HVAC = {
    MODE_AUTO: HVACMode.AUTO,
    MODE_COOL: HVACMode.COOL,
    MODE_HEAT: HVACMode.HEAT,
    MODE_DRY: HVACMode.DRY,
    MODE_FAN: HVACMode.FAN_ONLY,
}
_MODE = {hvac: mode for mode, hvac in _HVAC.items()}


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Create a climate entity for each air conditioner."""
    coordinator: SharpCoordinator = hass.data[DOMAIN][entry.entry_id]
    seen: set[str] = set()

    def _add() -> None:
        if not coordinator.data:
            return
        fresh = []
        for key in coordinator.data:
            if key in seen:
                continue
            seen.add(key)
            fresh.append(SharpClimate(coordinator, key))
        if fresh:
            async_add_entities(fresh)

    entry.async_on_unload(coordinator.async_add_listener(_add))
    _add()


class SharpClimate(CoordinatorEntity[SharpCoordinator], ClimateEntity):
    """Climate controls for one indoor unit."""

    _attr_has_entity_name = True
    _attr_name = None
    _attr_translation_key = "aircon"
    _attr_temperature_unit = UnitOfTemperature.CELSIUS
    _attr_min_temp = TEMP_MIN
    _attr_max_temp = TEMP_MAX
    _attr_hvac_modes: ClassVar[list[HVACMode]] = [
        HVACMode.OFF,
        HVACMode.COOL,
        HVACMode.HEAT,
        HVACMode.DRY,
        HVACMode.FAN_ONLY,
        HVACMode.AUTO,
    ]
    _attr_fan_modes: ClassVar[list[str]] = [name for name, _code in FAN_MODES]
    _attr_swing_modes: ClassVar[list[str]] = [name for name, _code in VANE_VERTICAL]
    _attr_swing_horizontal_modes: ClassVar[list[str]] = [name for name, _code in VANE_HORIZONTAL]

    def __init__(self, coordinator: SharpCoordinator, key: str) -> None:
        """Bind the entity to one unit."""
        super().__init__(coordinator)
        self._key = key
        device = coordinator.data[key]
        self._attr_unique_id = device.unique_key
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, device.unique_key)},
            manufacturer=MANUFACTURER,
            model=device.model,
            name=device.name,
        )

    @property
    def available(self) -> bool:
        """Available while polling succeeds and the unit is still listed."""
        return self.coordinator.last_update_success and self._device is not None

    @property
    def _device(self) -> Aircon | None:
        data = self.coordinator.data
        if not data:
            return None
        return data.get(self._key)

    @property
    def supported_features(self) -> ClimateEntityFeature:
        """Fan and both vanes always; setpoint only in modes that use one."""
        features = (
            ClimateEntityFeature.FAN_MODE
            | ClimateEntityFeature.SWING_MODE
            | ClimateEntityFeature.SWING_HORIZONTAL_MODE
            | ClimateEntityFeature.TURN_ON
            | ClimateEntityFeature.TURN_OFF
        )
        device = self._device
        if device is not None and mode_has_setpoint(device):
            features |= ClimateEntityFeature.TARGET_TEMPERATURE
        return features

    @property
    def precision(self) -> float:
        """Half degrees when the unit's capability blob allows them."""
        device = self._device
        if device is not None and device.caps.half_degree:
            return PRECISION_HALVES
        return PRECISION_WHOLE

    @property
    def target_temperature_step(self) -> float:
        """Same step the precision uses."""
        return self.precision

    @property
    def hvac_mode(self) -> HVACMode | None:
        """Off when power is off, otherwise the ECHONET mode."""
        device = self._device
        if device is None or device.power is None:
            return None
        if not device.power:
            return HVACMode.OFF
        if device.mode is None:
            return None
        return _HVAC.get(device.mode)

    @property
    def current_temperature(self) -> float | None:
        """Room temperature from EPC BB."""
        device = self._device
        return None if device is None else device.room_temp

    @property
    def current_humidity(self) -> float | None:
        """Relative humidity from EPC BA."""
        device = self._device
        return None if device is None else device.humidity

    @property
    def target_temperature(self) -> float | None:
        """Setpoint. Half degrees come from the FA frame when the unit has them."""
        device = self._device
        return None if device is None else device.setpoint

    @property
    def fan_mode(self) -> str | None:
        """Current fan speed."""
        device = self._device
        return None if device is None else device.fan

    @property
    def swing_mode(self) -> str | None:
        """Vertical vane position."""
        device = self._device
        return None if device is None else device.vane_vertical

    @property
    def swing_horizontal_mode(self) -> str | None:
        """Horizontal vane position."""
        device = self._device
        return None if device is None else device.vane_horizontal

    async def async_set_hvac_mode(self, hvac_mode: HVACMode) -> None:
        """Turn the unit off, or on into the selected mode."""
        if hvac_mode == HVACMode.OFF:
            await self.coordinator.async_send(self._key, cmd_power(False), power=False)
            return
        mode = _MODE[hvac_mode]
        await self.coordinator.async_send(self._key, cmd_mode(mode), power=True, mode=mode)

    async def async_set_temperature(self, **kwargs) -> None:
        """Set the target temperature, and the mode when the UI sends both."""
        device = self._device
        if device is None:
            return
        if kwargs.get(ATTR_HVAC_MODE) not in (None, HVACMode.OFF):
            await self.async_set_hvac_mode(kwargs[ATTR_HVAC_MODE])
            device = self._device or device
        temperature = kwargs.get(ATTR_TEMPERATURE)
        if temperature is None:
            return
        status = cmd_setpoint(
            float(temperature),
            half_degree=device.caps.half_degree,
            frame_end=device.frame_end,
        )
        snapped = round(float(temperature) * 2) / 2
        await self.coordinator.async_send(self._key, status, setpoint=snapped)

    async def async_set_fan_mode(self, fan_mode: str) -> None:
        """Set the fan speed."""
        await self.coordinator.async_send(self._key, cmd_fan(fan_mode), fan=fan_mode)

    async def async_set_swing_mode(self, swing_mode: str) -> None:
        """Set the vertical vane."""
        device = self._device
        if device is None:
            return
        status = cmd_vanes(vertical=swing_mode, horizontal=None, frame_end=device.frame_end)
        await self.coordinator.async_send(self._key, status, vane_vertical=swing_mode)

    async def async_set_swing_horizontal_mode(self, swing_horizontal_mode: str) -> None:
        """Set the horizontal vane."""
        device = self._device
        if device is None:
            return
        status = cmd_vanes(
            vertical=None,
            horizontal=swing_horizontal_mode,
            frame_end=device.frame_end,
        )
        await self.coordinator.async_send(self._key, status, vane_horizontal=swing_horizontal_mode)

    async def async_turn_on(self) -> None:
        """Power on without changing the mode."""
        await self.coordinator.async_send(self._key, cmd_power(True), power=True)

    async def async_turn_off(self) -> None:
        """Power off."""
        await self.coordinator.async_send(self._key, cmd_power(False), power=False)

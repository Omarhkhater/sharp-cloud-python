"""One climate entity per Sharp indoor unit."""

from __future__ import annotations

import asyncio
import math
from datetime import datetime
from typing import ClassVar

from homeassistant.components.climate import (
    ATTR_HVAC_MODE,
    ATTR_TEMPERATURE,
    ClimateEntity,
    ClimateEntityFeature,
    HVACMode,
)
from homeassistant.const import PRECISION_HALVES, PRECISION_WHOLE, UnitOfTemperature
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.event import async_call_later
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from . import SharpConfigEntry
from .const import (
    DOMAIN,
    FAN_MODES,
    FAN_RAPID,
    MANUFACTURER,
    MODE_AUTO,
    MODE_COOL,
    MODE_DRY,
    MODE_FAN,
    MODE_HEAT,
    SETPOINT_DEBOUNCE_S,
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


def snap_whole_degree(temperature: float) -> float:
    """Nearest whole degree. Halves round up, including 22.5."""
    return float(math.floor(temperature + 0.5))


async def async_setup_entry(
    hass: HomeAssistant,
    entry: SharpConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Create a climate entity for each air conditioner."""
    coordinator = entry.runtime_data
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
        self._pending_target: float | None = None
        self._debounce_unsub = None
        self._waiters: list[asyncio.Future[None]] = []

    async def async_added_to_hass(self) -> None:
        """Cancel a pending temperature tap if the entity is removed."""
        await super().async_added_to_hass()
        self.async_on_remove(self._cancel_setpoint)

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
        """Fan always. Vanes only when experimental controls are on."""
        features = (
            ClimateEntityFeature.FAN_MODE
            | ClimateEntityFeature.TURN_ON
            | ClimateEntityFeature.TURN_OFF
        )
        if self.coordinator.experimental:
            features |= ClimateEntityFeature.SWING_MODE | ClimateEntityFeature.SWING_HORIZONTAL_MODE
        device = self._device
        if device is not None and mode_has_setpoint(device):
            features |= ClimateEntityFeature.TARGET_TEMPERATURE
        return features

    @property
    def precision(self) -> float:
        """Whole degrees unless experimental half-degree control is enabled."""
        device = self._device
        if self.coordinator.experimental and device is not None and device.caps.half_degree:
            return PRECISION_HALVES
        return PRECISION_WHOLE

    @property
    def target_temperature_step(self) -> float:
        """One degree by default. Half degrees are experimental."""
        return self.precision

    @property
    def swing_modes(self) -> list[str] | None:
        """Vertical vane positions. Hidden until experimental controls are on."""
        if not self.coordinator.experimental:
            return None
        return [name for name, _code in VANE_VERTICAL]

    @property
    def swing_horizontal_modes(self) -> list[str] | None:
        """Horizontal vane positions. Hidden until experimental controls are on."""
        if not self.coordinator.experimental:
            return None
        return [name for name, _code in VANE_HORIZONTAL]

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
        """Setpoint. A burst of taps shows the latest value before it is sent."""
        if self._pending_target is not None:
            return self._pending_target
        device = self._device
        return None if device is None else device.setpoint

    @property
    def fan_modes(self) -> list[str]:
        """Fan speeds. Rapid stays hidden until experimental controls are on."""
        modes = [name for name, _code in FAN_MODES if name != FAN_RAPID]
        if self.coordinator.experimental:
            modes.append(FAN_RAPID)
        return modes

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
        target = float(temperature)
        half_degree = self.coordinator.experimental and device.caps.half_degree
        if not half_degree:
            target = snap_whole_degree(target)
        self._pending_target = target
        self.async_write_ha_state()
        if self._debounce_unsub is not None:
            self._debounce_unsub()
            self._debounce_unsub = None
        self._debounce_unsub = async_call_later(self.hass, SETPOINT_DEBOUNCE_S, self._flush)
        future: asyncio.Future[None] = self.hass.loop.create_future()
        self._waiters.append(future)
        await future

    async def _flush(self, _now: datetime) -> None:
        """Send the last temperature in the burst. Every waiter gets that result."""
        self._debounce_unsub = None
        target = self._pending_target
        waiters = self._waiters
        self._waiters = []
        device = self._device
        if device is None or target is None:
            await self._fail_setpoint(
                waiters, HomeAssistantError("That air conditioner is not available")
            )
            return
        half_degree = self.coordinator.experimental and device.caps.half_degree
        try:
            status = cmd_setpoint(target, half_degree=half_degree, frame_end=device.frame_end)
            await self.coordinator.async_send(self._key, status, setpoint=target)
        except (HomeAssistantError, ValueError) as err:
            await self._fail_setpoint(waiters, err)
            return
        if not self._waiters:
            self._pending_target = None
        for fut in waiters:
            if not fut.done():
                fut.set_result(None)

    async def _fail_setpoint(self, waiters: list[asyncio.Future[None]], err: Exception) -> None:
        """Fail the taps that were just sent. A newer tap stays queued."""
        if not self._waiters:
            self._pending_target = None
            self.async_write_ha_state()
        try:
            await self.coordinator.async_request_refresh()
        finally:
            for fut in waiters:
                if not fut.done():
                    fut.set_exception(HomeAssistantError(str(err)))

    def _cancel_setpoint(self) -> None:
        """Unload: cancel the debounce timer and fail taps that have not been sent."""
        if self._debounce_unsub is not None:
            self._debounce_unsub()
            self._debounce_unsub = None
        waiters = self._waiters
        self._waiters = []
        for fut in waiters:
            if not fut.done():
                fut.set_exception(HomeAssistantError("The air conditioner is not available"))

    async def async_set_fan_mode(self, fan_mode: str) -> None:
        """Set the fan speed. Rapid is experimental and was rejected live."""
        if fan_mode == FAN_RAPID and not self.coordinator.experimental:
            return
        await self.coordinator.async_send(self._key, cmd_fan(fan_mode), fan=fan_mode)

    async def async_set_swing_mode(self, swing_mode: str) -> None:
        """Set the vertical vane. This FA-frame write is experimental."""
        device = self._device
        if device is None or not self.coordinator.experimental:
            return
        status = cmd_vanes(vertical=swing_mode, horizontal=None, frame_end=device.frame_end)
        await self.coordinator.async_send(self._key, status, vane_vertical=swing_mode)

    async def async_set_swing_horizontal_mode(self, swing_horizontal_mode: str) -> None:
        """Set the horizontal vane. This FA-frame write is experimental."""
        device = self._device
        if device is None or not self.coordinator.experimental:
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

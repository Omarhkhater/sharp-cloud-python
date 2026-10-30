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
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.event import async_call_later
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from . import SharpConfigEntry
from .const import (
    DOMAIN,
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
    fa_payload_is_blank,
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

# HomeKit Bridge swing on/off. ON is the lowest vertical vane, OFF the highest.
# The seven position names stay settable. HomeKit reads swing as on only for "on".
_SWING_FROM_HOMEKIT = {"on": "down", "off": "up"}
_SWING_TO_HOMEKIT = {"down": "on"}

# HomeKit's rotation slider is low, middle, medium, high, in that order.
# Auto is a separate toggle. The cloud names stay on the wire.
_FAN_FROM_HOMEKIT = {
    "low": "quiet",
    "middle": "very_weak",
    "medium": "weak",
    "high": "strong",
    "quiet": "quiet",
    "very_weak": "very_weak",
    "weak": "weak",
    "strong": "strong",
}
_FAN_TO_HOMEKIT = {
    "quiet": "low",
    "very_weak": "middle",
    "weak": "medium",
    "strong": "high",
}
_HOMEKIT_FAN_MODES = ("auto", "low", "middle", "medium", "high")
# Same speeds as low, middle, medium, and high. Kept so existing automations still set them.
_LEGACY_FAN_MODES = ("quiet", "very_weak", "weak", "strong")


def _known_vane(name: str | None) -> str | None:
    """A vane name the frame builder can encode. Unknown readings are left out."""
    if name is None:
        return None
    if name in dict(VANE_VERTICAL) or name in dict(VANE_HORIZONTAL):
        return name
    return None


def snap_whole_degree(temperature: float) -> float:
    """Nearest whole degree. Halves round up, including 22.5."""
    return float(math.floor(temperature + 0.5))


def _is_half_step(temperature: float) -> bool:
    """True for 21.5, false for 21 or 22."""
    doubled = round(temperature * 2)
    return abs(temperature * 2 - doubled) < 1e-6 and doubled % 2 == 1


def _vane_frame(device: Aircon, wanted: dict[str, str]) -> list[dict]:
    """FA frame for the vane this send is changing.

    ``wanted`` holds only that vane. The other vane comes from ``device``.
    The coordinator fills that from fresh status, and from an earlier pick
    only while that field's own deadline has not passed.
    """
    vertical = wanted.get("vane_vertical")
    if vertical is None:
        vertical = _known_vane(device.vane_vertical)
    horizontal = wanted.get("vane_horizontal")
    if horizontal is None:
        horizontal = _known_vane(device.vane_horizontal)
    status = cmd_vanes(
        vertical=vertical,
        horizontal=horizontal,
        frame_end=device.frame_end,
        current=device.fa_frame,
    )
    code = str(status[0]["valueBinary"]["code"])
    if fa_payload_is_blank(code):
        raise HomeAssistantError(
            "Sharp would reject that command because its payload is empty. "
            "Wait for a status reading and try again."
        )
    return status


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
        # Unsent vane picks. Cleared when that send succeeds.
        # The card uses these only while the send is running. After that,
        # the coordinator overlay is the only 30 second hold.
        self._pending_vanes: dict[str, str] = {}
        self._vane_send_running = False
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
        """Fan always. Vanes follow the unit's capability bytes."""
        features = (
            ClimateEntityFeature.FAN_MODE
            | ClimateEntityFeature.TURN_ON
            | ClimateEntityFeature.TURN_OFF
        )
        device = self._device
        if device is not None and device.caps.vertical_vanes:
            features |= ClimateEntityFeature.SWING_MODE
        if device is not None and device.caps.horizontal_vanes:
            features |= ClimateEntityFeature.SWING_HORIZONTAL_MODE
        if device is not None and mode_has_setpoint(device):
            features |= ClimateEntityFeature.TARGET_TEMPERATURE
        return features

    @property
    def precision(self) -> float:
        """Half degrees when this unit's capability byte says it has them."""
        device = self._device
        if device is not None and device.caps.half_degree:
            return PRECISION_HALVES
        return PRECISION_WHOLE

    @property
    def target_temperature_step(self) -> float:
        """The same step the capability byte allows."""
        return self.precision

    def _vertical_positions(self, device: Aircon) -> list[str] | None:
        """Vertical positions. Pattern 02 hides Fully down in heat and auto."""
        if not device.caps.vertical_vanes:
            return None
        modes = list(device.caps.vertical_vanes)
        if device.caps.vertical_pattern == 2 and device.mode in {MODE_HEAT, MODE_AUTO}:
            return [mode for mode in modes if mode != "down"]
        return modes

    def _vertical_modes(self, device: Aircon) -> list[str] | None:
        """Positions, plus HomeKit on/off when those vanes are offered."""
        positions = self._vertical_positions(device)
        if positions is None:
            return None
        homekit: list[str] = []
        if "down" in positions:
            homekit.append("on")
        if "up" in positions:
            homekit.append("off")
        return homekit + positions

    @property
    def swing_modes(self) -> list[str] | None:
        """Vertical vane positions from the capability bytes."""
        device = self._device
        if device is None:
            return None
        return self._vertical_modes(device)

    @property
    def swing_horizontal_modes(self) -> list[str] | None:
        """Horizontal positions the capability bytes list, plus swing."""
        device = self._device
        if device is None or not device.caps.horizontal_vanes:
            return None
        return list(device.caps.horizontal_vanes)

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
        """Fan speeds HomeKit can map, plus the older names and Rapid.

        The order is Auto, then low, middle, medium, and high. HomeKit uses
        that order for the slider and treats auto as its own toggle. The older
        names set the same four speeds.
        """
        modes = [*_HOMEKIT_FAN_MODES, *_LEGACY_FAN_MODES]
        device = self._device
        if device is not None and device.caps.rapid:
            modes.append(FAN_RAPID)
        return modes

    @property
    def fan_mode(self) -> str | None:
        """Current fan speed, using the HomeKit name when there is one."""
        device = self._device
        if device is None or device.fan is None:
            return None
        return _FAN_TO_HOMEKIT.get(device.fan, device.fan)

    def _shown_vane(self, key: str, actual: str | None) -> str | None:
        """The pick while its send is in progress. After that, coordinator status.

        The coordinator keeps its own 30 second overlay, so the card does not
        keep a second timer.
        """
        if self._vane_send_running:
            pending = self._pending_vanes.get(key)
            if pending is not None:
                return pending
        return actual

    @property
    def swing_mode(self) -> str | None:
        """Vertical vane. Fully down is reported as on so HomeKit reads swing on.

        Every other position, including Up, is not a HomeKit swing-on name, so
        HomeKit reads swing off. The seven position names remain settable.
        """
        device = self._device
        actual = None if device is None else device.vane_vertical
        shown = self._shown_vane("vane_vertical", actual)
        if shown is None:
            return None
        return _SWING_TO_HOMEKIT.get(shown, shown)

    @property
    def swing_horizontal_mode(self) -> str | None:
        """Horizontal vane position. A recent pick shows before the cloud answers."""
        device = self._device
        actual = None if device is None else device.vane_horizontal
        return self._shown_vane("vane_horizontal", actual)

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
        temperature = kwargs.get(ATTR_TEMPERATURE)
        target: float | None = None
        if temperature is not None:
            # HomeKit sends tenths. Snap before any write so 21.3 is 21.5.
            raw = float(temperature)
            if device.caps.half_degree:
                target = round(raw * 2) / 2
            else:
                target = snap_whole_degree(raw)
        if kwargs.get(ATTR_HVAC_MODE) not in (None, HVACMode.OFF):
            await self.async_set_hvac_mode(kwargs[ATTR_HVAC_MODE])
            device = self._device or device
        if target is None:
            return
        # Ignore a repeat only when nothing is queued. 21→22→21 inside the
        # debounce must replace 22, or the flush sends 22. A newer poll that
        # disagrees still sends, even when the card is showing that value.
        if self._pending_target is None and self.coordinator.already_applied(
            self._key, {"setpoint": target}
        ):
            return
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
        if self.coordinator.already_applied(self._key, {"setpoint": target}):
            if not self._waiters:
                self._pending_target = None
            self.async_write_ha_state()
            for fut in waiters:
                if not fut.done():
                    fut.set_result(None)
            return
        half_degree = device.caps.half_degree
        try:
            if half_degree and _is_half_step(target):
                whole = math.floor(target + 1e-9)
                # The card may still be showing an earlier write. The whole
                # degree has to follow the last cloud reading, or a half step
                # is sent alone and the read-back does not match.
                polled = self.coordinator.polled_device(self._key)
                if polled is not None and polled.setpoint is not None:
                    current = polled.setpoint
                else:
                    current = device.setpoint
                crosses = current is None or math.floor(current + 1e-9) != whole
                if crosses and not self.coordinator.already_applied(
                    self._key, {"setpoint": float(whole)}
                ):
                    # The unit keeps the whole degree in B3. Send that first,
                    # then the half-degree byte, or a lagging FA byte is wrong.
                    # Skip it when this same whole degree was just sent and no
                    # newer reading disagrees.
                    await self.coordinator.async_send(
                        self._key,
                        cmd_setpoint(float(whole), half_degree=True, frame_end=device.frame_end),
                        setpoint=float(whole),
                    )
                    device = self._device or device
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
        """Set the fan speed. Rapid is sent only when the unit lists that speed."""
        device = self._device
        if device is None:
            return
        wire = _FAN_FROM_HOMEKIT.get(fan_mode, fan_mode)
        if wire == FAN_RAPID and not device.caps.rapid:
            raise ServiceValidationError("This air conditioner does not support Rapid")
        await self.coordinator.async_send(self._key, cmd_fan(wire), fan=wire)

    async def async_set_swing_mode(self, swing_mode: str) -> None:
        """Set the vertical vane.

        HomeKit on and off are Fully down and Up. Only that vane is a change.
        The horizontal byte is left as the current status.
        """
        await self._async_set_vanes(vertical=_SWING_FROM_HOMEKIT.get(swing_mode, swing_mode))

    async def async_set_swing_horizontal_mode(self, swing_horizontal_mode: str) -> None:
        """Set the horizontal vane.

        Only that vane is a change. The vertical byte is left as the current
        status and is not sent as its own command.
        """
        await self._async_set_vanes(horizontal=swing_horizontal_mode)

    async def _async_set_vanes(
        self, *, vertical: str | None = None, horizontal: str | None = None
    ) -> None:
        """Show the pick at once. A later pick replaces one that is still sending."""
        device = self._device
        if device is None:
            return
        offered = self._vertical_positions(device)
        if vertical is not None and (offered is None or vertical not in offered):
            raise ServiceValidationError("This air conditioner does not support that vane position")
        if horizontal is not None and horizontal not in device.caps.horizontal_vanes:
            raise ServiceValidationError("This air conditioner does not support that vane position")
        if vertical is not None:
            self._pending_vanes["vane_vertical"] = vertical
        if horizontal is not None:
            self._pending_vanes["vane_horizontal"] = horizontal
        if self._vane_send_running:
            self.async_write_ha_state()
            return
        self._vane_send_running = True
        self.async_write_ha_state()
        try:
            while True:
                wanted = dict(self._pending_vanes)
                try:
                    await self.coordinator.async_send_built(
                        self._key,
                        lambda fresh, picked=wanted: _vane_frame(fresh, picked),
                        **wanted,
                    )
                except HomeAssistantError:
                    if self._pending_vanes == wanted:
                        self._pending_vanes.clear()
                        self.async_write_ha_state()
                        raise
                    continue
                for key, value in wanted.items():
                    if self._pending_vanes.get(key) == value:
                        self._pending_vanes.pop(key, None)
                if not self._pending_vanes:
                    break
        finally:
            self._vane_send_running = False
        self.async_write_ha_state()

    async def async_turn_on(self) -> None:
        """Power on without changing the mode."""
        await self.coordinator.async_send(self._key, cmd_power(True), power=True)

    async def async_turn_off(self) -> None:
        """Power off."""
        await self.coordinator.async_send(self._key, cmd_power(False), power=False)

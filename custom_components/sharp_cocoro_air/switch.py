"""Plasmacluster, sleep, and the capability-gated FA flags."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from homeassistant.components.switch import SwitchEntity, SwitchEntityDescription
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN, MANUFACTURER
from .coordinator import SharpCoordinator
from .protocol import (
    Aircon,
    cmd_cocoro,
    cmd_plasmacluster,
    cmd_sleep,
    cmd_soft,
    cmd_speed,
)


@dataclass(frozen=True, kw_only=True)
class SharpSwitchDescription(SwitchEntityDescription):
    """A boolean feature, created only when ``supported`` says the unit has it."""

    is_on_fn: Callable[[Aircon], bool | None]
    command_fn: Callable[[bool, Aircon], list[dict]]
    field: str
    supported: Callable[[Aircon], bool] = lambda _device: True


def _fa_command(builder: Callable[[bool, int], list[dict]]) -> Callable[[bool, Aircon], list[dict]]:
    return lambda on, device: builder(on, device.frame_end)


SWITCHES: tuple[SharpSwitchDescription, ...] = (
    SharpSwitchDescription(
        key="plasmacluster",
        translation_key="plasmacluster",
        is_on_fn=lambda device: device.plasmacluster,
        command_fn=lambda on, _device: cmd_plasmacluster(on),
        field="plasmacluster",
    ),
    SharpSwitchDescription(
        key="sleep",
        translation_key="sleep",
        is_on_fn=lambda device: device.sleep,
        command_fn=_fa_command(cmd_sleep),
        field="sleep",
    ),
    SharpSwitchDescription(
        key="soft",
        translation_key="soft",
        is_on_fn=lambda device: device.soft,
        command_fn=_fa_command(cmd_soft),
        field="soft",
        supported=lambda device: device.caps.soft,
    ),
    SharpSwitchDescription(
        key="speed",
        translation_key="speed",
        is_on_fn=lambda device: device.speed,
        command_fn=_fa_command(cmd_speed),
        field="speed",
        supported=lambda device: device.caps.speed,
    ),
    SharpSwitchDescription(
        key="cocoro_air",
        translation_key="cocoro_air",
        is_on_fn=lambda device: device.cocoro_air,
        command_fn=_fa_command(cmd_cocoro),
        field="cocoro_air",
        supported=lambda device: device.caps.cocoro,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Create the switches each unit actually supports."""
    coordinator: SharpCoordinator = hass.data[DOMAIN][entry.entry_id]
    seen: set[str] = set()

    def _add() -> None:
        if not coordinator.data:
            return
        fresh = []
        for key, device in coordinator.data.items():
            for description in SWITCHES:
                if not description.supported(device):
                    continue
                ident = f"{key}:{description.key}"
                if ident in seen:
                    continue
                seen.add(ident)
                fresh.append(SharpSwitch(coordinator, key, description))
        if fresh:
            async_add_entities(fresh)

    entry.async_on_unload(coordinator.async_add_listener(_add))
    _add()


class SharpSwitch(CoordinatorEntity[SharpCoordinator], SwitchEntity):
    """One on/off feature."""

    _attr_has_entity_name = True
    entity_description: SharpSwitchDescription

    def __init__(
        self,
        coordinator: SharpCoordinator,
        key: str,
        description: SharpSwitchDescription,
    ) -> None:
        """Bind the switch to one unit and one feature."""
        super().__init__(coordinator)
        self.entity_description = description
        self._key = key
        device = coordinator.data[key]
        self._attr_unique_id = f"{device.unique_key}_{description.key}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, device.unique_key)},
            manufacturer=MANUFACTURER,
            model=device.model,
            name=device.name,
        )

    @property
    def available(self) -> bool:
        """Available while the unit is still in the latest poll."""
        return self.coordinator.last_update_success and self._device is not None

    @property
    def _device(self) -> Aircon | None:
        data = self.coordinator.data
        if not data:
            return None
        return data.get(self._key)

    @property
    def is_on(self) -> bool | None:
        """Current flag, or None when the frame did not include it."""
        device = self._device
        if device is None:
            return None
        return self.entity_description.is_on_fn(device)

    async def async_turn_on(self, **kwargs) -> None:
        """Turn the feature on."""
        await self._async_set(True)

    async def async_turn_off(self, **kwargs) -> None:
        """Turn the feature off."""
        await self._async_set(False)

    async def _async_set(self, on: bool) -> None:
        device = self._device
        if device is None:
            return
        status = self.entity_description.command_fn(on, device)
        await self.coordinator.async_send(self._key, status, **{self.entity_description.field: on})

"""Fault flag from ECHONET EPC 88, and read-only Super Jet from the FA frame."""

from __future__ import annotations

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from . import SharpConfigEntry
from .const import DOMAIN, MANUFACTURER
from .coordinator import SharpCoordinator
from .protocol import Aircon


async def async_setup_entry(
    hass: HomeAssistant,
    entry: SharpConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Create a fault sensor and a Super Jet sensor for each air conditioner."""
    coordinator = entry.runtime_data
    seen: set[str] = set()

    def _add() -> None:
        if not coordinator.data:
            return
        fresh = []
        for key in coordinator.data:
            for cls in (SharpFaultSensor, SharpSuperJetSensor):
                ident = f"{key}:{cls.translation_suffix}"
                if ident in seen:
                    continue
                seen.add(ident)
                fresh.append(cls(coordinator, key))
        if fresh:
            async_add_entities(fresh)

    entry.async_on_unload(coordinator.async_add_listener(_add))
    _add()


class _UnitBinary(CoordinatorEntity[SharpCoordinator], BinarySensorEntity):
    """One on/off reading from an indoor unit. There is no write."""

    _attr_has_entity_name = True
    translation_suffix: str

    def __init__(self, coordinator: SharpCoordinator, key: str) -> None:
        """Bind the sensor to one unit."""
        super().__init__(coordinator)
        self._key = key
        device = coordinator.data[key]
        self._attr_unique_id = f"{device.unique_key}_{self.translation_suffix}"
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


class SharpFaultSensor(_UnitBinary):
    """On when EPC 88 reports a fault."""

    translation_suffix = "fault"
    _attr_translation_key = "fault"
    _attr_device_class = BinarySensorDeviceClass.PROBLEM
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    @property
    def is_on(self) -> bool | None:
        """True when the unit reports a fault."""
        device = self._device
        return None if device is None else device.fault


class SharpSuperJetSensor(_UnitBinary):
    """On when FA byte 50 is 01. The cloud rejected writes of this byte."""

    translation_suffix = "super_jet"
    _attr_translation_key = "super_jet"

    @property
    def is_on(self) -> bool | None:
        """True when Super Jet is on. This sensor does not turn it on."""
        device = self._device
        return None if device is None else device.super_jet

"""Outdoor temperature, instantaneous power, and cumulative energy."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.const import UnitOfEnergy, UnitOfPower, UnitOfTemperature
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from . import SharpConfigEntry
from .const import DOMAIN, MANUFACTURER
from .coordinator import SharpCoordinator
from .protocol import Aircon


@dataclass(frozen=True, kw_only=True)
class SharpSensorDescription(SensorEntityDescription):
    """A sensor read from one decoded unit."""

    value_fn: Callable[[Aircon], float | int | None]


SENSORS: tuple[SharpSensorDescription, ...] = (
    SharpSensorDescription(
        key="outdoor_temperature",
        translation_key="outdoor_temperature",
        device_class=SensorDeviceClass.TEMPERATURE,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        suggested_display_precision=1,
        value_fn=lambda device: device.outdoor_temp,
    ),
    SharpSensorDescription(
        key="power",
        translation_key="power",
        device_class=SensorDeviceClass.POWER,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfPower.WATT,
        suggested_display_precision=0,
        value_fn=lambda device: device.power_w,
    ),
    SharpSensorDescription(
        key="energy",
        translation_key="energy",
        device_class=SensorDeviceClass.ENERGY,
        state_class=SensorStateClass.TOTAL_INCREASING,
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        suggested_display_precision=3,
        value_fn=lambda device: device.energy_kwh,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: SharpConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Create the three sensors for each air conditioner."""
    coordinator = entry.runtime_data
    seen: set[str] = set()

    def _add() -> None:
        if not coordinator.data:
            return
        fresh = []
        for key in coordinator.data:
            for description in SENSORS:
                ident = f"{key}:{description.key}"
                if ident in seen:
                    continue
                seen.add(ident)
                fresh.append(SharpSensor(coordinator, key, description))
        if fresh:
            async_add_entities(fresh)

    entry.async_on_unload(coordinator.async_add_listener(_add))
    _add()


class SharpSensor(CoordinatorEntity[SharpCoordinator], SensorEntity):
    """One reading from an indoor unit."""

    _attr_has_entity_name = True
    entity_description: SharpSensorDescription

    def __init__(
        self,
        coordinator: SharpCoordinator,
        key: str,
        description: SharpSensorDescription,
    ) -> None:
        """Bind the sensor to one unit and one description."""
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
    def native_value(self) -> float | int | None:
        """Latest decoded value, or None when the unit has no reading."""
        device = self._device
        if device is None:
            return None
        return self.entity_description.value_fn(device)

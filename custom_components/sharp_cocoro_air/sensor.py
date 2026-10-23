"""Room and outdoor temperature, humidity, power, and cumulative energy."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime

from homeassistant.components.sensor import (
    RestoreSensor,
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.const import PERCENTAGE, UnitOfEnergy, UnitOfPower, UnitOfTemperature
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
        key="room_temperature",
        translation_key="room_temperature",
        device_class=SensorDeviceClass.TEMPERATURE,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        suggested_display_precision=0,
        value_fn=lambda device: device.room_temp,
    ),
    SharpSensorDescription(
        key="humidity",
        translation_key="humidity",
        device_class=SensorDeviceClass.HUMIDITY,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=PERCENTAGE,
        suggested_display_precision=0,
        value_fn=lambda device: device.humidity,
    ),
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
    """Create the sensors for each air conditioner. None of them depend on power."""
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
                cls = (
                    SharpOutdoorSensor if description.key == "outdoor_temperature" else SharpSensor
                )
                fresh.append(cls(coordinator, key, description))
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


class SharpOutdoorSensor(SharpSensor, RestoreSensor):
    """Outdoor temperature. The last reading is kept while the unit is off.

    A restart has no in-memory history. The restored value is used only when
    the first poll has no outdoor reading and the unit is off. Availability
    still follows the poll: a missing device is unavailable.
    """

    def __init__(
        self,
        coordinator: SharpCoordinator,
        key: str,
        description: SharpSensorDescription,
    ) -> None:
        """Bind the sensor and clear the restore flags."""
        super().__init__(coordinator, key, description)
        self._restored: float | None = None
        self._restored_at: datetime | None = None
        self._use_restore = False
        self._decided = False

    async def async_added_to_hass(self) -> None:
        """Load the previous outdoor reading, then decide if this poll may use it."""
        await super().async_added_to_hass()
        last = await self.async_get_last_sensor_data()
        if (
            last is not None
            and isinstance(last.native_value, (int, float))
            and not isinstance(last.native_value, bool)
        ):
            self._restored = float(last.native_value)
        state = await self.async_get_last_state()
        if state is not None:
            self._restored_at = state.last_updated
        self._accept_restore()

    def _handle_coordinator_update(self) -> None:
        """Use the restored reading for this update, then let the 6 hour hold run.

        Clearing the fallback after the update means a later poll can expire.
        Leaving it set kept the restored number on screen after the hold ended.
        """
        self._accept_restore()
        super()._handle_coordinator_update()
        if self._use_restore:
            self._use_restore = False
            self.async_write_ha_state()

    def _accept_restore(self) -> None:
        """Seed once: first poll is unknown and the unit is off."""
        if self._decided or self._restored is None:
            return
        device = self._device
        if device is None:
            return
        self._decided = True
        if device.outdoor_temp is None and device.power is False:
            self._use_restore = True
            self.coordinator.note_outdoor(self._key, self._restored, self._restored_at)
            return
        self._restored = None

    @property
    def native_value(self) -> float | int | None:
        """Poll value, or the restored reading for that first off poll."""
        device = self._device
        if device is None:
            return None
        if device.outdoor_temp is not None:
            return device.outdoor_temp
        if self._use_restore and device.power is False and self._restored is not None:
            return self._restored
        return None

    @property
    def extra_state_attributes(self) -> dict[str, bool]:
        """``stale`` is set only while the number is a held or restored reading."""
        if self._holding:
            return {"stale": True}
        return {}

    @property
    def _holding(self) -> bool:
        device = self._device
        if device is None:
            return False
        if device.outdoor_stale:
            return True
        return bool(
            device.outdoor_temp is None
            and device.power is False
            and self._use_restore
            and self._restored is not None
        )

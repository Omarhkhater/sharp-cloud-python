"""Sensor platform for Sharp COCORO AIR."""

from __future__ import annotations

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import (
    PERCENTAGE,
    UnitOfTemperature,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import SharpCoordinator


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator: SharpCoordinator = hass.data[DOMAIN][entry.entry_id][
        "coordinator"
    ]

    entities = []

    for device in coordinator.data:
        box_id = device["boxId"]

        entities.extend(
            [
                RoomTemperatureSensor(
                    coordinator,
                    device,
                    box_id,
                ),
                RoomHumiditySensor(
                    coordinator,
                    device,
                    box_id,
                ),
                OutdoorTemperatureSensor(
                    coordinator,
                    device,
                    box_id,
                ),
                OnlineSensor(
                    coordinator,
                    device,
                    box_id,
                ),
            ]
        )

    async_add_entities(entities)


class SharpSensor(
    CoordinatorEntity,
    SensorEntity,
):
    def __init__(
        self,
        coordinator: SharpCoordinator,
        device: dict,
        box_id: str,
        key: str,
        name: str,
    ):
        super().__init__(coordinator)

        self._device = device
        self._key = key

        self._attr_has_entity_name = True
        self._attr_name = name
        self._attr_unique_id = f"{box_id}_{key}"

    @property
    def _status(self):
        return self._device.get("status", {})

    @property
    def native_value(self):
        return self._status.get(self._key)

    @property
    def device_info(self):
        return {
            "identifiers": {
                (
                    DOMAIN,
                    self._device["boxId"],
                )
            }
        }


class RoomTemperatureSensor(
    SharpSensor,
):
    _attr_device_class = SensorDeviceClass.TEMPERATURE
    _attr_native_unit_of_measurement = UnitOfTemperature.CELSIUS

    def __init__(
        self,
        coordinator,
        device,
        box_id,
    ):
        super().__init__(
            coordinator,
            device,
            box_id,
            "roomTemperature",
            "Room Temperature",
        )


class OutdoorTemperatureSensor(
    SharpSensor,
):
    _attr_device_class = SensorDeviceClass.TEMPERATURE
    _attr_native_unit_of_measurement = UnitOfTemperature.CELSIUS

    def __init__(
        self,
        coordinator,
        device,
        box_id,
    ):
        super().__init__(
            coordinator,
            device,
            box_id,
            "outdoorTemperature",
            "Outdoor Temperature",
        )


class RoomHumiditySensor(
    SharpSensor,
):
    _attr_native_unit_of_measurement = PERCENTAGE
    _attr_device_class = SensorDeviceClass.HUMIDITY

    def __init__(
        self,
        coordinator,
        device,
        box_id,
    ):
        super().__init__(
            coordinator,
            device,
            box_id,
            "roomHumidity",
            "Room Humidity",
        )


class OnlineSensor(
    SharpSensor,
):
    def __init__(
        self,
        coordinator,
        device,
        box_id,
    ):
        super().__init__(
            coordinator,
            device,
            box_id,
            "online",
            "Online",
        )
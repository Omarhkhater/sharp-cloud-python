"""Device status models."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(slots=True)
class DeviceStatus:
    """Current device status."""

    power: bool = False
    mode: int | None = None
    temperature: float | None = None
    target_temperature: float | None = None
    fan_speed: int | None = None
    swing: int | None = None
    humidity: int | None = None

    pm25: int | None = None
    co2: int | None = None

    online: bool = False
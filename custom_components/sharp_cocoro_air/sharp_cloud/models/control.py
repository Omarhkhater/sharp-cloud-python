"""Device control models."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(slots=True)
class DeviceCommand:
    """Device command."""

    device_id: str
    property_id: str
    value: object
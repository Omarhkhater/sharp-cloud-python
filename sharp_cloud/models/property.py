"""Device property models."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(slots=True)
class DeviceProperty:
    """Single device property."""

    name: str
    value: object
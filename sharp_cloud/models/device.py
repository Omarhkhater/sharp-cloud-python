"""Sharp device models."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(slots=True)
class Terminal:
    """Registered terminal."""

    terminal_id: str
    app_id: str | None = None
    name: str | None = None


@dataclass(slots=True)
class Device:
    """Sharp device."""

    device_id: str
    terminal_id: str

    name: str | None = None
    model: str | None = None
    firmware: str | None = None
    mac_address: str | None = None

    online: bool = False
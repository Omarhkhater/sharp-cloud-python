"""Sharp Cloud request models."""

from __future__ import annotations

from dataclasses import dataclass, asdict


@dataclass(slots=True)
class LoginRequest:
    """setting/login"""

    id_token: str

    def to_dict(self) -> dict:
        return {
            "idToken": self.id_token,
        }


@dataclass(slots=True)
class TerminalRequest:
    """setting/terminal"""

    service_name: str

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass(slots=True)
class DeviceRequest:
    """Device request."""

    terminal_id: str
    device_id: str

    def to_dict(self) -> dict:
        return {
            "terminalId": self.terminal_id,
            "deviceId": self.device_id,
        }


@dataclass(slots=True)
class DeviceControlRequest:
    """Device control request."""

    terminal_id: str
    device_id: str
    property_id: str
    value: object

    def to_dict(self) -> dict:
        return {
            "terminalId": self.terminal_id,
            "deviceId": self.device_id,
            "propertyId": self.property_id,
            "value": self.value,
        }
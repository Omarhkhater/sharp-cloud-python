"""Model parsers."""

from __future__ import annotations

from .control import DeviceCommand
from .device import Device, Terminal
from .property import DeviceProperty
from .result import ControlResult
from .status import DeviceStatus


class Parser:
    """Converts Sharp Cloud JSON into models."""

    @staticmethod
    def terminal(data: dict) -> Terminal:
        return Terminal(
            terminal_id=data.get("terminalId", ""),
            app_id=data.get("terminalAppId"),
            name=data.get("terminalName"),
        )

    @staticmethod
    def device(data: dict) -> Device:
        return Device(
            device_id=data.get("deviceId", ""),
            terminal_id=data.get("terminalId", ""),
            name=data.get("deviceName"),
            model=data.get("modelName"),
            firmware=data.get("firmwareVersion"),
            mac_address=data.get("macAddress"),
            online=data.get("online", False),
        )

    @staticmethod
    def property(data: dict) -> DeviceProperty:
        return DeviceProperty(
            name=data.get("propertyName", ""),
            value=data.get("value"),
        )

    @staticmethod
    def status(data: dict) -> DeviceStatus:
        return DeviceStatus(
            power=data.get("power", False),
            mode=data.get("mode"),
            temperature=data.get("temperature"),
            target_temperature=data.get("targetTemperature"),
            fan_speed=data.get("fanSpeed"),
            swing=data.get("swing"),
            humidity=data.get("humidity"),
            pm25=data.get("pm25"),
            co2=data.get("co2"),
            online=data.get("online", False),
        )

    @staticmethod
    def control_result(data: dict) -> ControlResult:
        return ControlResult(
            request_id=data.get("requestId", ""),
            completed=data.get("completed", False),
            success=data.get("success", False),
        )
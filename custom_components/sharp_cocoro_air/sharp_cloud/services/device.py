"""Sharp device service."""

from __future__ import annotations

from sharp_cloud.http import HttpClient
from sharp_cloud.protocol import Endpoints


class DeviceService:
    """Handles all Sharp device operations."""

    def __init__(self, http: HttpClient):
        self._http = http

    def properties(
        self,
        terminal_app_id: str,
    ) -> dict:
        response = self._http.get(
            Endpoints.device_property(),
            params={
                "terminalAppId": terminal_app_id,
            },
        )

        response.raise_for_status()

        return response.json()

    def status(
        self,
        terminal_app_id: str,
    ) -> dict:
        response = self._http.get(
            Endpoints.device_status(),
            params={
                "terminalAppId": terminal_app_id,
            },
        )

        response.raise_for_status()

        return response.json()

    def control(
        self,
        terminal_app_id: str,
        box_id: str,
        payload: dict,
    ) -> dict:
        response = self._http.post(
            Endpoints.device_control(),
            params={
                "terminalAppId": terminal_app_id,
                "boxId": box_id,
            },
            json=payload,
        )

        response.raise_for_status()

        return response.json()

    def control_result(
        self,
        terminal_app_id: str,
        control_id: str,
    ) -> dict:
        response = self._http.get(
            Endpoints.control_result(),
            params={
                "terminalAppId": terminal_app_id,
                "controlId": control_id,
            },
        )

        response.raise_for_status()

        return response.json()

    def wait_for_result(
        self,
        terminal_app_id: str,
        control_id: str,
    ) -> dict:
        return self.control_result(
            terminal_app_id=terminal_app_id,
            control_id=control_id,
        )
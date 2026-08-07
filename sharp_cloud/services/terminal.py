"""Sharp terminal service."""

from __future__ import annotations

from sharp_cloud.api.endpoints import Endpoints
from sharp_cloud.api.http import HttpClient
from sharp_cloud.models import Parser, Terminal


class TerminalService:
    """Handles terminal operations."""

    def __init__(self, http: HttpClient):
        self._http = http

    def list(self) -> list[Terminal]:
        response = self._http.post(
            Endpoints.terminal(),
        )

        response.raise_for_status()

        data = response.json()

        return [
            Parser.terminal(item)
            for item in data
        ]

    def app_id(self) -> str:
        response = self._http.post(
            Endpoints.terminal_app_id(),
        )

        response.raise_for_status()

        return response.json().get("terminalAppId", "")
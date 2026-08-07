"""Main Sharp Cloud client."""

from __future__ import annotations

from sharp_cloud.auth.oauth import OAuthClient
from sharp_cloud.config import SharpConfig
from sharp_cloud.http import HttpClient
from sharp_cloud.services import (
    AuthenticationService,
    DeviceService,
)


class SharpCloud:
    """Main entry point for the Sharp Cloud SDK."""

    def __init__(self, config: SharpConfig | None = None):
        self.config = config or SharpConfig()

        self.http = HttpClient(self.config)

        self.auth = OAuthClient(self.config)

        self.authentication = AuthenticationService(
            http=self.http,
            oauth=self.auth,
        )

        self.devices = DeviceService(
            http=self.http,
        )

    @property
    def authenticated(self) -> bool:
        return self.auth.authenticated

    @property
    def access_token(self) -> str:
        return self.auth.access_token

    @property
    def refresh_token(self) -> str:
        return self.auth.refresh_token

    @property
    def id_token(self) -> str:
        return self.auth.id_token

    def login(self) -> None:
        self.auth.open_browser()

    def exchange_code(self, code: str):
        return self.auth.exchange_code(code)

    def refresh(self):
        return self.auth.refresh()

    def logout(self) -> None:
        self.auth.logout()
"""Main Sharp Cloud client."""

from __future__ import annotations

from .auth.oauth import OAuthClient
from .config import SharpConfig
from .http import HttpClient
from .services import (
    AuthenticationService,
    DeviceService,
)


class SharpCloud:
    """Main entry point for the Sharp Cloud SDK."""

    def __init__(
        self,
        config: SharpConfig | None = None,
    ):
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

    @property
    def token(self):
        """Return the current OAuth token."""

        return self.auth.token

    @token.setter
    def token(self, value):
        """Set the current OAuth token."""

        self.auth.token = value

    def authorization_url(self) -> tuple[str, str]:
        """
        Generate an authorization URL.

        Returns
        -------
        tuple[str, str]
            (authorization_url, state)
        """

        return self.auth.build_authorization_url()

    def exchange_code(self, code: str):
        """
        Exchange an authorization code for tokens.
        """

        return self.auth.exchange_code(code)

    def refresh(self):
        """
        Refresh the current access token.
        """

        return self.auth.refresh()

    def logout(self) -> None:
        """
        Clear the current authentication.
        """

        self.auth.logout()
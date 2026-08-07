"""OAuth authentication for Sharp Cloud."""

from __future__ import annotations

import secrets
from urllib.parse import urlencode

import requests

from .token import OAuthToken
from ..config import SharpConfig
from ..constants import (
    B2C_AUTHORIZE_URL,
    B2C_TOKEN_URL,
    CLIENT_ID,
    CLIENT_SECRET,
    REDIRECT_URI,
    SCOPE,
    TOKEN_REDIRECT_URI,
)


class OAuthClient:
    """Handles Azure AD B2C authentication."""

    def __init__(
        self,
        config: SharpConfig,
    ):
        self._config = config
        self.token: OAuthToken | None = None

    @property
    def authenticated(self) -> bool:
        """Return True if authenticated."""

        if self.token is None:
            return False

        if self.token.is_expired:
            try:
                self.refresh()
            except Exception:
                return False

        return True

    @property
    def client_id(self) -> str:
        """Return the Azure client id."""

        return CLIENT_ID

    @property
    def client_secret(self) -> str:
        """Return the Azure client secret."""

        return CLIENT_SECRET

    @property
    def access_token(self) -> str:
        """Return the current access token."""

        if not self.authenticated:
            raise RuntimeError("Not authenticated.")

        return self.token.access_token

    @property
    def refresh_token(self) -> str:
        """Return the current refresh token."""

        if self.token is None:
            raise RuntimeError("Not authenticated.")

        return self.token.refresh_token

    @property
    def id_token(self) -> str:
        """Return the current id token."""

        if self.token is None:
            raise RuntimeError("Not authenticated.")

        return self.token.id_token

    @staticmethod
    def generate_state() -> str:
        """Generate a random OAuth state."""

        return secrets.token_urlsafe(32)

    def build_authorization_url(
        self,
    ) -> tuple[str, str]:
        """
        Build the Sharp OAuth authorization URL.

        Returns
        -------
        tuple[str, str]
            (authorization_url, state)
        """

        state = self.generate_state()

        params = {
            "client_id": CLIENT_ID,
            "response_type": "code",
            "redirect_uri": REDIRECT_URI,
            "scope": SCOPE,
            "response_mode": "query",
            "state": state,
        }

        return (
            f"{B2C_AUTHORIZE_URL}?{urlencode(params)}",
            state,
        )

    def exchange_code(
        self,
        code: str,
    ) -> OAuthToken:
        """Exchange an authorization code for OAuth tokens."""

        response = requests.post(
            B2C_TOKEN_URL,
            data={
                "grant_type": "authorization_code",
                "client_id": CLIENT_ID,
                "client_secret": CLIENT_SECRET,
                "code": code,
                "redirect_uri": TOKEN_REDIRECT_URI,
            },
            timeout=self._config.timeout,
            verify=self._config.verify_ssl,
        )

        response.raise_for_status()

        self.token = OAuthToken.from_dict(
            response.json(),
        )

        return self.token

    def refresh(
        self,
    ) -> OAuthToken:
        """Refresh the OAuth token."""

        if self.token is None:
            raise RuntimeError("Not authenticated.")

        response = requests.post(
            B2C_TOKEN_URL,
            data={
                "grant_type": "refresh_token",
                "client_id": CLIENT_ID,
                "client_secret": CLIENT_SECRET,
                "refresh_token": self.token.refresh_token,
                "redirect_uri": TOKEN_REDIRECT_URI,
            },
            timeout=self._config.timeout,
            verify=self._config.verify_ssl,
        )

        response.raise_for_status()

        self.token = OAuthToken.from_dict(
            response.json(),
        )

        return self.token

    def logout(
        self,
    ) -> None:
        """Clear the current authentication."""

        self.token = None
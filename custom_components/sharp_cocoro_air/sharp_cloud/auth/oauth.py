"""OAuth authentication for Sharp Cloud."""

from __future__ import annotations

import secrets
import webbrowser
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

    def __init__(self, config: SharpConfig):
        self._config = config
        self.token: OAuthToken | None = None

    @property
    def authenticated(self) -> bool:
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
        return CLIENT_ID

    @property
    def client_secret(self) -> str:
        return CLIENT_SECRET

    @property
    def access_token(self) -> str:
        if not self.authenticated:
            raise RuntimeError("Not authenticated.")

        return self.token.access_token

    @property
    def refresh_token(self) -> str:
        if self.token is None:
            raise RuntimeError("Not authenticated.")

        return self.token.refresh_token

    @property
    def id_token(self) -> str:
        if self.token is None:
            raise RuntimeError("Not authenticated.")

        return self.token.id_token

    @staticmethod
    def generate_state() -> str:
        return secrets.token_urlsafe(32)

    def build_authorization_url(self) -> tuple[str, str]:
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

    def open_browser(self) -> str:
        url, state = self.build_authorization_url()
        webbrowser.open(url)
        return state

    def exchange_code(self, code: str) -> OAuthToken:
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

        self.token = OAuthToken.from_dict(response.json())

        return self.token

    def refresh(self) -> OAuthToken:
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

        self.token = OAuthToken.from_dict(response.json())

        return self.token

    def logout(self) -> None:
        self.token = None
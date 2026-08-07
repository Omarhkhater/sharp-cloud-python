"""HTTP client for the Sharp Cloud API."""

from __future__ import annotations

from typing import Any

import requests

from sharp_cloud.config import SharpConfig
from sharp_cloud.exceptions import (
    ApiError,
    AuthenticationError,
    AuthorizationError,
)


class HttpClient:
    """HTTP transport."""

    def __init__(self, config: SharpConfig):
        self._config = config

        self.session = requests.Session()

        self.session.headers.update(
            {
                "User-Agent": config.user_agent,
                "Accept": "application/json",
            }
        )

    def _handle(self, response: requests.Response) -> requests.Response:
        if response.status_code == 401:
            raise AuthenticationError(response.text)

        if response.status_code == 403:
            raise AuthorizationError(response.text)

        if not response.ok:
            raise ApiError(
                f"{response.status_code}: {response.text}"
            )

        return response

    def get(self, url: str, **kwargs: Any):
        kwargs.setdefault("timeout", self._config.timeout)
        kwargs.setdefault("verify", self._config.verify_ssl)

        return self._handle(
            self.session.get(url, **kwargs)
        )

    def post(self, url: str, **kwargs: Any):
        kwargs.setdefault("timeout", self._config.timeout)
        kwargs.setdefault("verify", self._config.verify_ssl)

        return self._handle(
            self.session.post(url, **kwargs)
        )

    def set_bearer_token(self, token: str):
        self.session.headers["Authorization"] = f"Bearer {token}"

    def clear_bearer_token(self):
        self.session.headers.pop("Authorization", None)
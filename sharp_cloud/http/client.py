"""HTTP client for the Sharp Cloud API."""

from __future__ import annotations

from typing import Any

import requests

from sharp_cloud.config import SharpConfig
from sharp_cloud.exceptions import AuthenticationError


class HttpClient:
    """HTTP transport for the Sharp Cloud API."""

    def __init__(self, config: SharpConfig):
        self._config = config

        self.session = requests.Session()

        self.session.headers.update(
            {
                "User-Agent": config.user_agent,
                "Accept": "application/json",
                "Content-Type": "application/json",
            }
        )

    def _prepare(self, kwargs: dict[str, Any]) -> None:
        kwargs.setdefault("timeout", self._config.timeout)
        kwargs.setdefault("verify", self._config.verify_ssl)

    @staticmethod
    def _check(response: requests.Response) -> None:
        if response.status_code == 401:
            raise AuthenticationError("Authentication required.")

        response.raise_for_status()

    def get(self, url: str, **kwargs: Any) -> requests.Response:
        self._prepare(kwargs)
        response = self.session.get(url, **kwargs)
        self._check(response)
        return response

    def post(self, url: str, **kwargs: Any) -> requests.Response:
        self._prepare(kwargs)
        response = self.session.post(url, **kwargs)
        self._check(response)
        return response

    def put(self, url: str, **kwargs: Any) -> requests.Response:
        self._prepare(kwargs)
        response = self.session.put(url, **kwargs)
        self._check(response)
        return response

    def delete(self, url: str, **kwargs: Any) -> requests.Response:
        self._prepare(kwargs)
        response = self.session.delete(url, **kwargs)
        self._check(response)
        return response

    def set_bearer_token(self, token: str) -> None:
        self.session.headers["Authorization"] = f"Bearer {token}"

    def clear_bearer_token(self) -> None:
        self.session.headers.pop("Authorization", None)

    def update_headers(self, headers: dict[str, str]) -> None:
        self.session.headers.update(headers)
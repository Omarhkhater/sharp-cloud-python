"""Sharp authentication service."""

from __future__ import annotations

from sharp_cloud.auth.oauth import OAuthClient
from sharp_cloud.http import HttpClient
from sharp_cloud.protocol import Endpoints


class AuthenticationService:
    """Sharp authentication."""

    def __init__(
        self,
        http: HttpClient,
        oauth: OAuthClient,
    ):
        self._http = http
        self._oauth = oauth

        self.generated_id: str | None = None
        self.app_secret: str | None = None
        self.terminal_app_id: str | None = None

    def generated_id_request(self) -> str:
        response = self._http.get(
            Endpoints.generated_id(),
            headers={
                "Client_Id": self._oauth.client_id,
                "Client_Secret": self._oauth.client_secret,
            },
        )

        response.raise_for_status()

        self.generated_id = response.json()["data"]["Generated ID"]

        return self.generated_id

    def login(
        self,
        payload: dict,
    ) -> dict:
        if self.app_secret is None:
            raise RuntimeError("app_secret is not initialized.")

        response = self._http.post(
            Endpoints.login(self.app_secret),
            json=payload,
        )

        response.raise_for_status()

        return response.json()

    def terminal(
        self,
        payload: dict,
    ) -> dict:
        response = self._http.post(
            Endpoints.terminal(),
            json=payload,
        )

        response.raise_for_status()

        return response.json()

    def terminal_app_id(
        self,
        payload: dict,
    ) -> dict:
        response = self._http.post(
            Endpoints.terminal_app_id(),
            json=payload,
        )

        response.raise_for_status()

        return response.json()

    def user_info(self) -> dict:
        response = self._http.get(
            Endpoints.user_info(),
        )

        response.raise_for_status()

        return response.json()

    def pairing(self) -> dict:
        response = self._http.get(
            Endpoints.pairing(),
        )

        response.raise_for_status()

        return response.json()
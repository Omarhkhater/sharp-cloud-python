"""Sharp Cloud API endpoint builder."""

from __future__ import annotations

from sharp_cloud.constants import (
    API_BASE_URL,
    API_HOST,
    Endpoint,
)


class Endpoints:
    """Builds Sharp Cloud API URLs."""

    @staticmethod
    def url(endpoint: Endpoint) -> str:
        return f"{API_BASE_URL}{endpoint.value}"

    @staticmethod
    def generated_id() -> str:
        return f"{API_HOST}GetGeneratedID"

    @staticmethod
    def login(app_secret: str) -> str:
        return (
            f"{API_BASE_URL}"
            f"{Endpoint.SETTING_LOGIN.value}"
            f"?appSecret={app_secret}&serviceName=sharp-egy"
        )

    @staticmethod
    def terminal() -> str:
        return Endpoints.url(Endpoint.SETTING_TERMINAL)

    @staticmethod
    def terminal_app_id() -> str:
        return Endpoints.url(Endpoint.SETTING_TERMINAL_APP_ID)

    @staticmethod
    def user_info() -> str:
        return Endpoints.url(Endpoint.SETTING_USER_INFO)

    @staticmethod
    def pairing() -> str:
        return Endpoints.url(Endpoint.SETTING_PAIRING)

    @staticmethod
    def device_property() -> str:
        return Endpoints.url(Endpoint.CONTROL_DEVICE_PROPERTY)

    @staticmethod
    def device_status() -> str:
        return Endpoints.url(Endpoint.CONTROL_DEVICE_STATUS)

    @staticmethod
    def device_control() -> str:
        return Endpoints.url(Endpoint.CONTROL_DEVICE_CONTROL)

    @staticmethod
    def control_result() -> str:
        return Endpoints.url(Endpoint.CONTROL_RESULT)
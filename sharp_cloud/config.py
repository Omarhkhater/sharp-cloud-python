"""
Configuration models for the Sharp Cloud SDK.
"""

from __future__ import annotations

from dataclasses import dataclass

from sharp_cloud.constants import (
    API_BASE_URL,
    API_HOST,
    CLIENT_ID,
    REDIRECT_URI,
    SCOPE,
    SERVICE_NAME_EGYPT,
)


@dataclass(slots=True)
class SharpConfig:
    """SDK configuration."""

    api_base_url: str = API_BASE_URL
    api_host: str = API_HOST

    client_id: str = CLIENT_ID
    redirect_uri: str = REDIRECT_URI
    scope: str = SCOPE

    service_name: str = SERVICE_NAME_EGYPT

    timeout: float = 30.0
    verify_ssl: bool = True

    user_agent: str = "sharp-cloud-python/0.1.0"
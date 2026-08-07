"""Token models for Sharp Cloud."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any


@dataclass(slots=True)
class OAuthToken:
    """Represents an Azure AD B2C OAuth token response."""

    access_token: str | None
    refresh_token: str | None
    id_token: str | None
    token_type: str | None
    scope: str | None
    expires_in: int | None

    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))

    @property
    def expires_at(self) -> datetime | None:
        """Return the token expiration time."""
        if self.expires_in is None:
            return None
        return self.created_at + timedelta(seconds=self.expires_in)

    @property
    def is_expired(self) -> bool:
        """Return True if the token has expired."""
        if self.expires_at is None:
            return False
        return datetime.now(UTC) >= self.expires_at

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "OAuthToken":
        """Create an OAuthToken from an OAuth response."""
        return cls(
            access_token=data.get("access_token"),
            refresh_token=data.get("refresh_token"),
            id_token=data.get("id_token"),
            token_type=data.get("token_type"),
            scope=data.get("scope"),
            expires_in=int(data["expires_in"]) if data.get("expires_in") is not None else None,
        )

    def to_dict(self) -> dict[str, Any]:
        """Convert the token to a serializable dictionary."""
        return {
            "access_token": self.access_token,
            "refresh_token": self.refresh_token,
            "id_token": self.id_token,
            "token_type": self.token_type,
            "scope": self.scope,
            "expires_in": self.expires_in,
            "created_at": self.created_at.isoformat(),
        }
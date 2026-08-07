"""Control result models."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(slots=True)
class ControlResult:
    """Command execution result."""

    request_id: str
    completed: bool
    success: bool
"""
Configuration models for the Sharp COCORO AIR SDK.
"""

from dataclasses import dataclass


@dataclass(slots=True)
class SharpConfig:
    """SDK configuration."""

    timeout: float = 30.0
    verify_ssl: bool = True
    user_agent: str = "sharp-cloud-python/0.1.0"
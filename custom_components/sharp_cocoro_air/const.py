"""Constants for the Sharp COCORO AIR integration."""

from __future__ import annotations

DOMAIN = "sharp_cocoro_air"

MANUFACTURER = "Sharp"

DEFAULT_SCAN_INTERVAL = 30

PLATFORMS = [
    "climate",
    "sensor",
]

CONF_REGION = "region"

CONF_USERNAME = "username"

CONF_PASSWORD = "password"

CONF_REFRESH_INTERVAL = "refresh_interval"

DEFAULT_REGION = "egypt"

DEFAULT_REFRESH_INTERVAL = 30
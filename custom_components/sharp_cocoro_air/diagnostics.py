"""Diagnostics support for Sharp COCORO AIR."""

from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .const import DOMAIN


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant,
    entry: ConfigEntry,
) -> dict:
    """Return diagnostics."""

    data = hass.data[DOMAIN][entry.entry_id]

    client = data["client"]
    coordinator = data["coordinator"]

    return {
        "authenticated": client.authenticated,
        "devices": coordinator.data,
        "entry": {
            "title": entry.title,
            "entry_id": entry.entry_id,
        },
    }
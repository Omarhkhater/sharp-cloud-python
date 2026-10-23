"""Diagnostics with account and device identifiers removed."""

from __future__ import annotations

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .const import CONF_COOKIES, CONF_TERMINAL_APP_ID, CONF_USER_ID, DOMAIN
from .coordinator import SharpCoordinator
from .protocol import device_diagnostics

_REDACT = {CONF_TERMINAL_APP_ID, CONF_USER_ID, CONF_COOKIES}


async def async_get_config_entry_diagnostics(hass: HomeAssistant, entry: ConfigEntry) -> dict:
    """Return status without cookies, terminal ids, box ids, or node addresses."""
    coordinator: SharpCoordinator = hass.data[DOMAIN][entry.entry_id]
    devices = list((coordinator.data or {}).values())
    cookies = entry.data.get(CONF_COOKIES) or []
    return async_redact_data(
        {
            "cookie_names": sorted(
                {cookie.get("name") for cookie in cookies if cookie.get("name")}
            ),
            "devices": [device_diagnostics(device) for device in devices],
        },
        _REDACT,
    )

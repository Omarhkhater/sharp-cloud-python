"""Sharp Air Control (Egypt) for Home Assistant."""

from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant

from .client import SharpClient, new_session
from .const import CONF_COOKIES, CONF_TERMINAL_APP_ID, DOMAIN
from .coordinator import SharpCoordinator

PLATFORMS = [
    Platform.CLIMATE,
    Platform.SENSOR,
    Platform.SWITCH,
    Platform.BINARY_SENSOR,
]

type SharpConfigEntry = ConfigEntry


async def async_setup_entry(hass: HomeAssistant, entry: SharpConfigEntry) -> bool:
    """Connect with the stored session and start polling."""
    session = new_session()
    client = SharpClient(session, entry.data.get(CONF_TERMINAL_APP_ID))
    client.restore_cookies(entry.data.get(CONF_COOKIES) or [])
    coordinator = SharpCoordinator(hass, entry, client)
    try:
        await coordinator.async_config_entry_first_refresh()
    except Exception:
        await client.aclose()
        raise
    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = coordinator
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: SharpConfigEntry) -> bool:
    """Stop polling and close the session."""
    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unloaded:
        coordinator: SharpCoordinator = hass.data[DOMAIN].pop(entry.entry_id)
        await coordinator.client.aclose()
    return unloaded

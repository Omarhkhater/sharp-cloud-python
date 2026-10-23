"""Sharp Air Egypt for Home Assistant."""

from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant

from .client import SharpClient, async_get_clientsession
from .const import CONF_COOKIES, CONF_TERMINAL_APP_ID
from .coordinator import SharpCoordinator, clear_auth_failures

PLATFORMS = [
    Platform.CLIMATE,
    Platform.SENSOR,
    Platform.SWITCH,
    Platform.BINARY_SENSOR,
]

type SharpConfigEntry = ConfigEntry[SharpCoordinator]


async def async_setup_entry(hass: HomeAssistant, entry: SharpConfigEntry) -> bool:
    """Connect with the stored session and start polling."""
    session = async_get_clientsession(hass)
    client = SharpClient(session, entry.data.get(CONF_TERMINAL_APP_ID))
    client.restore_cookies(entry.data.get(CONF_COOKIES) or [])
    coordinator = SharpCoordinator(hass, entry, client)
    entry.runtime_data = coordinator
    entry.async_on_unload(entry.add_update_listener(_async_reload_options))
    await coordinator.async_config_entry_first_refresh()
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: SharpConfigEntry) -> bool:
    """Stop polling and forget the sign-in failure count.

    Home Assistant closes the client session. Setup retry does not come through
    here, so a streak of rejected polls still reaches three.
    """
    clear_auth_failures(hass, entry.entry_id)
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)


async def _async_reload_options(hass: HomeAssistant, entry: SharpConfigEntry) -> None:
    """Reload when the experimental toggle changes.

    Cookie writes also update the entry. Those keep the same options, so they
    must not reload.
    """
    if dict(entry.options) == entry.runtime_data.tracked_options:
        return
    await hass.config_entries.async_reload(entry.entry_id)

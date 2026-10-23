"""Polling coordinator. Commands update the cached state once the cloud accepts them."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed, HomeAssistantError
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .client import SharpAuthError, SharpClient, SharpConnectionError, SharpError
from .const import CONF_COOKIES, DOMAIN, SCAN_INTERVAL
from .protocol import Aircon, split_box_info, with_updates

_LOGGER = logging.getLogger(__name__)


class SharpCoordinator(DataUpdateCoordinator[dict[str, Aircon]]):
    """Fetch every indoor unit about once a minute."""

    def __init__(self, hass: HomeAssistant, entry, client: SharpClient) -> None:
        """Store the account entry and the cloud client."""
        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name=DOMAIN,
            update_interval=SCAN_INTERVAL,
        )
        self.entry = entry
        self.client = client
        self._lock = asyncio.Lock()
        self._paired = False

    async def _async_update_data(self) -> dict[str, Aircon]:
        async with self._lock:
            try:
                payload = await self.client.box_info()
            except SharpAuthError as err:
                raise ConfigEntryAuthFailed("Sharp sign-in expired") from err
            except SharpError as err:
                raise UpdateFailed("Could not reach the Sharp cloud") from err
            box_ids, devices = split_box_info(payload)
            if not self._paired:
                for box_id in box_ids:
                    try:
                        await self.client.pair(box_id)
                    except SharpError:
                        _LOGGER.debug("Pairing was not accepted")
                self._paired = True
            self._persist_cookies()
            return {device.unique_key: device for device in devices}

    async def async_send(self, key: str, status: list[dict], **updates: Any) -> None:
        """Run a command and apply the change locally when it succeeds."""
        if not self.data or key not in self.data:
            raise HomeAssistantError("That air conditioner is not available")
        device = self.data[key]
        async with self._lock:
            try:
                await self.client.send_control(device, status)
            except SharpAuthError:
                self.entry.async_start_reauth(self.hass)
                raise HomeAssistantError(
                    "Sharp sign-in expired. Open the integration and sign in again."
                ) from None
            except SharpConnectionError:
                raise HomeAssistantError("Could not reach the Sharp cloud") from None
            except SharpError:
                raise HomeAssistantError("The air conditioner rejected the command") from None
            data = dict(self.data)
            data[key] = with_updates(device, **updates)
            self.async_set_updated_data(data)
            self._persist_cookies()

    def _persist_cookies(self) -> None:
        cookies = self.client.export_cookies()
        if not cookies:
            return
        stored = self.entry.data.get(CONF_COOKIES) or []
        current = sorted((item["name"], item["value"]) for item in cookies)
        previous = sorted((item.get("name"), item.get("value")) for item in stored)
        if current == previous:
            return
        data = dict(self.entry.data)
        data[CONF_COOKIES] = cookies
        self.hass.config_entries.async_update_entry(self.entry, data=data)

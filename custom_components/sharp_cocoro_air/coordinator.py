"""Polling coordinator. Commands update the cached state once the cloud accepts them."""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed, HomeAssistantError
from homeassistant.helpers.event import async_call_later
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .client import (
    SharpAuthError,
    SharpClient,
    SharpCommandRejected,
    SharpConnectionError,
    SharpError,
)
from .const import CONF_COOKIES, CONF_EXPERIMENTAL, DOMAIN, SCAN_INTERVAL
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
        self.tracked_options = dict(entry.options)

    @property
    def experimental(self) -> bool:
        """FA-frame controls the user turned on in the options flow."""
        return bool(self.entry.options.get(CONF_EXPERIMENTAL, False))

    async def _async_update_data(self) -> dict[str, Aircon]:
        async with self._lock:
            try:
                payload = await self.client.box_info()
            except SharpAuthError as err:
                raise ConfigEntryAuthFailed("Sharp sign-in expired") from err
            except SharpError as err:
                raise UpdateFailed("Could not reach the Sharp cloud") from err
            # One attempt per box per entry load, then at most once an hour.
            await self.client.pair_unlisted(payload)
            _box_ids, devices = split_box_info(payload)
            self._persist_cookies()
            return {device.unique_key: device for device in devices}

    async def async_send(self, key: str, status: list[dict], **updates: Any) -> None:
        """Run a command and apply the change locally when it succeeds.

        A control id means the cloud accepted the write. Success and unmatch
        both schedule one status read. The command is not sent again.
        """
        if not self.data or key not in self.data:
            raise HomeAssistantError("That air conditioner is not available")
        device = self.data[key]
        accepted = False
        rejected = False
        async with self._lock:
            try:
                await self.client.send_control(device, status)
            except SharpCommandRejected:
                accepted = True
                rejected = True
            except SharpAuthError:
                self.entry.async_start_reauth(self.hass)
                raise HomeAssistantError(
                    "Sharp sign-in expired. Open the integration and sign in again."
                ) from None
            except SharpConnectionError:
                raise HomeAssistantError("Could not reach the Sharp cloud") from None
            except SharpError:
                raise HomeAssistantError("The air conditioner rejected the command") from None
            else:
                accepted = True
                data = dict(self.data)
                data[key] = with_updates(device, **updates)
                self.async_set_updated_data(data)
                self._persist_cookies()
        # _async_update_data takes this same lock, so the timer is armed out here.
        if accepted:
            self._schedule_status_refresh()
        if rejected:
            raise HomeAssistantError("The air conditioner rejected the command")

    def _schedule_status_refresh(self) -> None:
        """Read status 15 seconds after an accepted write. Do not resend it."""
        unsub = async_call_later(self.hass, 15, self._refresh_after_write)
        self.entry.async_on_unload(unsub)

    async def _refresh_after_write(self, _now: datetime) -> None:
        """Fetch boxInfo. This does not repeat the command."""
        await self.async_request_refresh()

    def _persist_cookies(self) -> None:
        """Store the jar when the session id changes.

        The load balancer rotates AWSALB cookies on their own. Rewriting the
        config entry for those would reload nothing useful and wake listeners.
        """
        cookies = self.client.export_cookies()
        current = _session_id(cookies)
        if not current:
            return
        stored = _session_id(self.entry.data.get(CONF_COOKIES) or [])
        if current == stored:
            return
        data = dict(self.entry.data)
        data[CONF_COOKIES] = cookies
        self.hass.config_entries.async_update_entry(self.entry, data=data)


def _session_id(cookies: list[dict]) -> str | None:
    for item in cookies:
        if item.get("name") == "JSESSIONID":
            return item.get("value")
    return None

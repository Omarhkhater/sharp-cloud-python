"""Polling coordinator.

Writes for one box are queued on that box's lock. Polls use a different lock,
so a status read is not stuck behind controlResult. An unmatch on a single-value
power, mode, fan, setpoint, or Plasmacluster write is checked against a later
status read and sent at most once more. FA frames are never retried.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections import defaultdict
from datetime import datetime
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed, HomeAssistantError
from homeassistant.helpers.event import async_call_later
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from .client import (
    SharpAuthError,
    SharpBusy,
    SharpClient,
    SharpConnectionError,
    SharpError,
    SharpTerminalRefused,
)
from .const import (
    CONF_COOKIES,
    CONF_EXPERIMENTAL,
    DOMAIN,
    OUTDOOR_HOLD_S,
    PENDING_GRACE_S,
    RETRY_EPCS,
    SCAN_INTERVAL,
    VERIFY_DELAY_S,
    WRITE_SETTLE_S,
)
from .protocol import Aircon, split_box_info, with_updates

_LOGGER = logging.getLogger(__name__)
_BUSY_RETRY_S = 10.0
# Whole-degree setpoints are valueRange, not valueSingle. Both are one value.
# valueBinary (FA) is never in this set.
_SINGLE_VALUE_TYPES = {"valueSingle", "valueRange"}
# Setup retries build a new coordinator. The count has to outlive that object
# or a rejected session stays in setup-retry and never asks for reauth.
_AUTH_FAILURES = f"{DOMAIN}_auth_failures"


async def _pause(seconds: float) -> None:
    """Wait out settle, verify, or a busy retry."""
    await asyncio.sleep(seconds)


def _monotonic() -> float:
    """Clock for settle gaps and the pending-target grace period."""
    return time.monotonic()


def _can_retry(status: list[dict]) -> bool:
    """True when every property is one retryable EPC. Timeouts are not passed here."""
    if not status:
        return False
    for prop in status:
        code = str(prop.get("statusCode", "")).lower()
        if code not in RETRY_EPCS or prop.get("valueType") not in _SINGLE_VALUE_TYPES:
            return False
    return True


def _matches(device: Aircon, updates: dict[str, Any]) -> bool:
    """True when the decoded unit already has every optimistic field."""
    return all(getattr(device, name) == value for name, value in updates.items())


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
        self._poll_lock = asyncio.Lock()
        self._box_locks: dict[str, asyncio.Lock] = defaultdict(asyncio.Lock)
        self._last_result_at: dict[str, float] = {}
        self._pending: dict[str, tuple[dict[str, Any], float]] = {}
        self._last_outdoor: dict[str, tuple[float, float]] = {}
        self._refresh_unsub = None
        self._closed = False
        self._auth_failures = self._stored_auth_failures()
        self.tracked_options = dict(entry.options)
        entry.async_on_unload(self._on_unload)

    def _stored_auth_failures(self) -> int:
        """Failures already counted for this entry, including earlier setups."""
        counts: dict[str, int] = self.hass.data.setdefault(_AUTH_FAILURES, {})
        return counts.get(self.entry.entry_id, 0)

    def _set_auth_failures(self, count: int) -> None:
        """Remember the streak on the entry so the next setup retry can see it."""
        self._auth_failures = count
        counts: dict[str, int] = self.hass.data.setdefault(_AUTH_FAILURES, {})
        if count:
            counts[self.entry.entry_id] = count
        else:
            counts.pop(self.entry.entry_id, None)

    @property
    def experimental(self) -> bool:
        """FA-frame controls the user turned on in the options flow."""
        return bool(self.entry.options.get(CONF_EXPERIMENTAL, False))

    async def async_shutdown(self) -> None:
        """Stop the status timer and the poll schedule."""
        self._closed = True
        self._cancel_refresh()
        await super().async_shutdown()

    async def _async_update_data(self) -> dict[str, Aircon]:
        async with self._poll_lock:
            try:
                payload = await self.client.box_info()
            except SharpAuthError as err:
                self._set_auth_failures(self._auth_failures + 1)
                # A refused terminal id will not recover on the next poll.
                # Any other rejection is given two polls before reauth.
                if isinstance(err, SharpTerminalRefused) or self._auth_failures >= 3:
                    raise ConfigEntryAuthFailed("Sharp sign-in expired") from err
                raise UpdateFailed("Sharp sign-in expired") from err
            except SharpError as err:
                raise UpdateFailed("Could not reach the Sharp cloud") from err
            self._set_auth_failures(0)
            # One attempt per box per entry load, then at most once an hour.
            await self.client.pair_unlisted(payload)
            _box_ids, devices = split_box_info(payload)
            devices = self._hold_outdoor(devices)
            self._persist_cookies()
            data = {device.unique_key: device for device in devices}
            self._overlay_pending(data)
            return data

    def note_outdoor(self, key: str, value: float, updated_at: datetime | None = None) -> bool:
        """Remember a restored outdoor reading so a later off poll can hold it.

        ``updated_at`` is the restored state's last report. A reading that is
        already 6 hours old is not seeded.
        """
        seen = _monotonic()
        if updated_at is not None:
            age = (dt_util.utcnow() - dt_util.as_utc(updated_at)).total_seconds()
            if age >= OUTDOOR_HOLD_S:
                return False
            seen -= max(age, 0.0)
        self._last_outdoor.setdefault(key, (value, seen))
        return True

    def _hold_outdoor(self, devices: list[Aircon]) -> list[Aircon]:
        """Keep the last outdoor temperature while the unit is off.

        A fresh reading replaces the memory. 0x7E while the unit is on stays
        unknown and does not count as a reading. The timestamp is not refreshed
        by a held value, so the grace period still ends.
        """
        now = _monotonic()
        held: list[Aircon] = []
        for device in devices:
            if device.outdoor_temp is not None:
                self._last_outdoor[device.unique_key] = (device.outdoor_temp, now)
                held.append(device)
                continue
            stored = self._last_outdoor.get(device.unique_key)
            if device.power is False and stored is not None and now - stored[1] < OUTDOOR_HOLD_S:
                value, _seen = stored
                held.append(with_updates(device, outdoor_temp=value, outdoor_stale=True))
                continue
            held.append(device)
        return held

    def _overlay_pending(self, data: dict[str, Aircon]) -> None:
        """Keep a write on screen until the poll shows it or the grace period ends."""
        now = _monotonic()
        for key in list(self._pending):
            updates, expires = self._pending[key]
            device = data.get(key)
            if device is None:
                if now >= expires:
                    self._pending.pop(key, None)
                continue
            if now >= expires or _matches(device, updates):
                self._pending.pop(key, None)
                continue
            data[key] = with_updates(device, **updates)

    async def async_send(self, key: str, status: list[dict], **updates: Any) -> None:
        """Send a command for one box, then refresh status outside every lock.

        Unmatch on a retryable single-value write is verified after the unit has
        had time to apply it, and sent once more if it did not. The 15-second
        status read is never awaited while a lock is held.
        """
        if not self.data or key not in self.data:
            raise HomeAssistantError("That air conditioner is not available")
        device = self.data[key]
        box_id = device.box_id
        try:
            async with self._box_locks[box_id]:
                try:
                    await self._wait_settle(box_id)
                    try:
                        result = await self.client.send_control(device, status)
                    except SharpBusy:
                        await _pause(_BUSY_RETRY_S)
                        result = await self.client.send_control(device, status)
                    if result == "unmatch" and _can_retry(status):
                        result = await self._verify_or_resend(device, status, updates)
                finally:
                    self._last_result_at[box_id] = _monotonic()
        except SharpTerminalRefused:
            self.entry.async_start_reauth(self.hass)
            raise HomeAssistantError(
                "Sharp sign-in expired. Open the integration and sign in again."
            ) from None
        except SharpAuthError:
            raise HomeAssistantError("Sharp sign-in expired") from None
        except SharpConnectionError:
            raise HomeAssistantError("Could not reach the Sharp cloud") from None
        except SharpError:
            raise HomeAssistantError("The air conditioner rejected the command") from None

        if result in ("success", "verified"):
            self._apply_optimistic(key, updates)
        self._schedule_status_refresh()
        if result not in ("success", "verified"):
            raise HomeAssistantError("The air conditioner rejected the command")

    async def _wait_settle(self, box_id: str) -> None:
        """Let the previous command finish applying before the next one starts."""
        last = self._last_result_at.get(box_id)
        if last is None:
            return
        remaining = WRITE_SETTLE_S - (_monotonic() - last)
        if remaining > 0:
            await _pause(remaining)

    async def _verify_or_resend(
        self,
        device: Aircon,
        status: list[dict],
        updates: dict[str, Any],
    ) -> str:
        """Read boxInfo directly. Resend once when the write is not there yet.

        Caller holds this box's lock. This does not take the poll lock and does
        not call a coordinator refresh.
        """
        await _pause(VERIFY_DELAY_S)
        payload = await self.client.box_info()
        _box_ids, devices = split_box_info(payload)
        current = next((item for item in devices if item.unique_key == device.unique_key), None)
        if current is not None and _matches(current, updates):
            return "verified"
        return await self.client.send_control(device, status)

    def _apply_optimistic(self, key: str, updates: dict[str, Any]) -> None:
        """Show the write now, and keep it across a stale poll for a short while."""
        if not updates:
            return
        self._pending[key] = (dict(updates), _monotonic() + PENDING_GRACE_S)
        if not self.data or key not in self.data:
            return
        data = dict(self.data)
        data[key] = with_updates(self.data[key], **updates)
        self.async_set_updated_data(data)
        self._persist_cookies()

    def _schedule_status_refresh(self) -> None:
        """Read status 15 seconds after a write. One timer, replaced on the next write."""
        if self._closed:
            return
        self._cancel_refresh()
        self._refresh_unsub = async_call_later(self.hass, 15, self._refresh_after_write)

    def _cancel_refresh(self) -> None:
        """Cancel the single status timer, if one is armed."""
        unsub = self._refresh_unsub
        self._refresh_unsub = None
        if unsub is not None:
            unsub()

    def _on_unload(self) -> None:
        """Drop the status timer when the config entry unloads."""
        self._closed = True
        self._cancel_refresh()

    async def _refresh_after_write(self, _now: datetime) -> None:
        """Fetch boxInfo. A fire after unload does nothing. The command is not resent."""
        self._refresh_unsub = None
        if self._closed:
            return
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

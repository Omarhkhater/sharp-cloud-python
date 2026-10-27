"""Polling coordinator.

Writes for one box are queued on that box's lock. Polls use a different lock,
so a status read is not stuck behind controlResult. An unmatch on a single-value
power, mode, fan, setpoint, or Plasmacluster write is checked against a later
status read and sent at most once more. An FA unmatch is confirmed with one
status read and is not sent again.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections import defaultdict
from collections.abc import Callable
from datetime import datetime, timedelta
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed, HomeAssistantError
from homeassistant.helpers.event import async_call_later
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from .client import (
    SharpAPIError,
    SharpAuthError,
    SharpBusy,
    SharpClient,
    SharpConnectionError,
    SharpError,
    SharpRefused,
    SharpTerminalRefused,
)
from .const import (
    CONF_COOKIES,
    CONF_EXPERIMENTAL,
    DOMAIN,
    FA_VERIFY_S,
    OUTDOOR_HOLD_S,
    PENDING_GRACE_S,
    POLL_BACKOFF_SECONDS,
    RETRY_EPCS,
    VERIFY_DELAY_S,
    WRITE_SETTLE_S,
    scan_interval_seconds,
)
from .protocol import Aircon, split_box_info, with_updates

_LOGGER = logging.getLogger(__name__)
_BUSY_RETRY_S = 10.0
# Whole-degree setpoints are valueRange, not valueSingle. Both are one value.
# valueBinary (FA) is never in this set.
_SINGLE_VALUE_TYPES = {"valueSingle", "valueRange"}
# Setup retries build a new coordinator and unload the failed attempt, but they
# do not call async_unload_entry. The count has to outlive that object.
# A real unload, and the moment reauth starts or finishes, clears it so one
# rejected poll after a reload does not go straight back to reauth.
_AUTH_FAILURES = f"{DOMAIN}_auth_failures"
_UNCONFIRMED = "The change couldn't be confirmed"


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


def _is_fa(status: list[dict]) -> bool:
    """True when the command is only an FA frame. Those are confirmed, not resent."""
    if not status:
        return False
    return all(
        str(prop.get("statusCode", "")).lower() == "fa" and prop.get("valueType") == "valueBinary"
        for prop in status
    )


def clear_auth_failures(hass: HomeAssistant, entry_id: str) -> None:
    """Forget the stored sign-in failure count for one config entry."""
    counts: dict[str, int] | None = hass.data.get(_AUTH_FAILURES)
    if counts:
        counts.pop(entry_id, None)


def _matches(device: Aircon, updates: dict[str, Any]) -> bool:
    """True when the decoded unit already has every optimistic field."""
    return all(getattr(device, name) == value for name, value in updates.items())


class SharpCoordinator(DataUpdateCoordinator[dict[str, Aircon]]):
    """Fetch every indoor unit on the configured interval.

    The default is 15 seconds. The Sharp cloud does not push status, so this
    poll is how a change from the remote or the SHARP app can show up.
    """

    def __init__(self, hass: HomeAssistant, entry, client: SharpClient) -> None:
        """Store the account entry and the cloud client."""
        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name=DOMAIN,
            update_interval=timedelta(seconds=scan_interval_seconds(entry.options)),
        )
        self.entry = entry
        self.client = client
        self._poll_lock = asyncio.Lock()
        self._box_locks: dict[str, asyncio.Lock] = defaultdict(asyncio.Lock)
        self._last_result_at: dict[str, float] = {}
        # Device key, then field name, then (value, deadline, set_at).
        # Each field expires alone. set_at is when this write was stored.
        self._pending: dict[str, dict[str, tuple[Any, float, float]]] = {}
        # Last poll, without optimistic fields. Frames copy every other byte from this.
        self._fresh: dict[str, Aircon] = {}
        # Set only when a poll is stored. Copying status for a frame does not.
        self._fresh_at: dict[str, float] = {}
        self._last_outdoor: dict[str, tuple[float, float]] = {}
        self._refresh_unsub = None
        self._closed = False
        self._auth_failures = self._stored_auth_failures()
        # How many polls in a row hit a rate limit, a server error, or the network.
        self._poll_failures = 0
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

    def _poll_backoff(self, err: SharpError) -> float | None:
        """Seconds to wait after a 429, a 5xx, or a network error. Other errors wait as usual."""
        network = isinstance(err, SharpConnectionError)
        server = isinstance(err, SharpAPIError) and (err.status == 429 or err.status >= 500)
        if not network and not server:
            return None
        self._poll_failures += 1
        step = POLL_BACKOFF_SECONDS[min(self._poll_failures - 1, len(POLL_BACKOFF_SECONDS) - 1)]
        return float(max(step, scan_interval_seconds(self.entry.options)))

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
                    self._set_auth_failures(0)
                    raise ConfigEntryAuthFailed("Sharp sign-in expired") from err
                raise UpdateFailed("Sharp sign-in expired") from err
            except SharpError as err:
                delay = self._poll_backoff(err)
                if delay is None:
                    raise UpdateFailed("Could not reach the Sharp cloud") from err
                raise UpdateFailed("Could not reach the Sharp cloud", retry_after=delay) from err
            self._set_auth_failures(0)
            self._poll_failures = 0
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

    def _active_fields(self, key: str, device: Aircon | None) -> dict[str, Any]:
        """Fields still inside their own deadline and not yet shown by this reading.

        A match or an expiry removes only that field. Another field's deadline
        is left alone.
        """
        fields = self._pending.get(key)
        if not fields:
            return {}
        now = _monotonic()
        active: dict[str, Any] = {}
        for name in list(fields):
            value, expires, _set_at = fields[name]
            if now >= expires or (device is not None and getattr(device, name) == value):
                fields.pop(name, None)
                continue
            active[name] = value
        if not fields:
            self._pending.pop(key, None)
        return active

    def _device_for_frame(self, key: str) -> Aircon:
        """Fresh status, plus optimistic values whose own deadlines have not passed."""
        current = self.data[key]
        if key not in self._fresh:
            self._fresh[key] = current
        base = self._fresh[key]
        active = self._active_fields(key, base)
        if not active:
            return base
        return with_updates(base, **active)

    def _overlay_pending(self, data: dict[str, Aircon]) -> None:
        """Keep each unexpired write until a poll shows that field or its deadline passes."""
        now = _monotonic()
        for key, device in data.items():
            self._fresh[key] = device
            self._fresh_at[key] = now
        for key in list(self._pending):
            device = data.get(key)
            if device is None:
                self._active_fields(key, None)
                continue
            active = self._active_fields(key, device)
            if active:
                data[key] = with_updates(device, **active)

    def polled_device(self, key: str) -> Aircon | None:
        """Status copied for frames, without optimistic fields.

        After a poll this is that poll. Before the first poll it is the status
        copied when a write built its frame. It is missing until one of those.
        """
        return self._fresh.get(key)

    def already_applied(self, key: str, updates: dict[str, Any]) -> bool:
        """True when this command should not be sent.

        A newer poll that already has every field clears those overlay fields
        and the card follows the poll. A repeat of our own write is skipped
        until a newer poll disagrees. Status copied before that write does not
        count as the unit already being there.
        """
        if not updates:
            return False
        fresh = self._fresh.get(key)
        fields = self._pending.get(key) or {}
        if fresh is not None and _matches(fresh, updates):
            overlay_differs = any(
                name in fields and fields[name][0] != value for name, value in updates.items()
            )
            stale_snapshot = False
            if overlay_differs:
                fresh_at = self._fresh_at.get(key)
                set_at = max(fields[name][2] for name in updates if name in fields)
                stale_snapshot = fresh_at is None or fresh_at <= set_at
            if not stale_snapshot:
                self._drop_fields(key, set(updates))
                self._publish_device(key)
                return True
        if all(name in fields and fields[name][0] == value for name, value in updates.items()):
            fresh_at = self._fresh_at.get(key)
            set_at = max(fields[name][2] for name in updates)
            contradicted = (
                fresh is not None
                and fresh_at is not None
                and fresh_at > set_at
                and not _matches(fresh, updates)
            )
            if not contradicted:
                return True
        return (
            fresh is None
            and bool(self.data)
            and key in self.data
            and _matches(self.data[key], updates)
        )

    def _drop_fields(self, key: str, names: set[str]) -> None:
        """Remove overlay fields. Other fields keep their own deadlines."""
        fields = self._pending.get(key)
        if not fields:
            return
        for name in names:
            fields.pop(name, None)
        if not fields:
            self._pending.pop(key, None)

    def _publish_device(self, key: str) -> None:
        """Show the last poll plus any overlay fields that are still active."""
        if not self.data or key not in self.data:
            return
        base = self._fresh.get(key)
        if base is None:
            return
        active = self._active_fields(key, base)
        data = dict(self.data)
        data[key] = with_updates(base, **active) if active else base
        self.async_set_updated_data(data)

    async def async_send(self, key: str, status: list[dict], **updates: Any) -> None:
        """Send a command for one box, then refresh status outside every lock.

        Unmatch on a retryable single-value write is verified after the unit has
        had time to apply it, and sent once more if it did not. An FA unmatch is
        confirmed with one status read and is not sent again. The 15-second
        refresh is never awaited while a lock is held.
        """
        await self._async_send(key, lambda _device: status, updates, skip_if_applied=False)

    async def async_send_built(
        self,
        key: str,
        build: Callable[[Aircon], list[dict]],
        **updates: Any,
    ) -> None:
        """Build the frame under the box lock from fresh status.

        A command is not sent when the last poll already has those fields, or
        when it repeats our own write and no newer poll disagrees. Only the
        fields passed in this call are changes. Any other byte comes from the
        last poll, plus an optimistic value only while that field's own
        deadline has not passed.
        """
        await self._async_send(key, build, updates, skip_if_applied=True)

    async def _async_send(
        self,
        key: str,
        build: Callable[[Aircon], list[dict]],
        updates: dict[str, Any],
        *,
        skip_if_applied: bool,
    ) -> None:
        """Send under the box lock. ``build`` runs only after the lock is held."""
        if not self.data or key not in self.data:
            raise HomeAssistantError("That air conditioner is not available")
        box_id = self.data[key].box_id
        confirm_failed = False
        skipped = False
        status: list[dict] = []
        result = "verified"
        try:
            async with self._box_locks[box_id]:
                if not self.data or key not in self.data:
                    raise HomeAssistantError("That air conditioner is not available")
                device = self._device_for_frame(key)
                if skip_if_applied and self.already_applied(key, updates):
                    skipped = True
                else:
                    status = build(device)
                    try:
                        await self._wait_settle(box_id)
                        try:
                            result = await self.client.send_control(device, status)
                        except SharpBusy as busy:
                            await _pause(_BUSY_RETRY_S)
                            try:
                                result = await self.client.send_control(device, status)
                            except SharpBusy as again:
                                _LOGGER.warning("deviceControl refused, errorCode=%s", again.code)
                                raise
                            _LOGGER.debug("deviceControl busy, errorCode=%s", busy.code)
                        if result == "unmatch" and _is_fa(status):
                            try:
                                result = await self._confirm_fa(device, updates)
                            except SharpError:
                                confirm_failed = True
                                raise
                        elif result == "unmatch" and _can_retry(status):
                            result = await self._verify_or_resend(device, status, updates)
                    finally:
                        self._last_result_at[box_id] = _monotonic()
        except SharpTerminalRefused:
            if confirm_failed:
                self._schedule_status_refresh()
                raise HomeAssistantError(_UNCONFIRMED) from None
            self._set_auth_failures(0)
            self.entry.async_start_reauth(self.hass)
            raise HomeAssistantError(
                "Sharp sign-in expired. Open the integration and sign in again."
            ) from None
        except SharpAuthError:
            if confirm_failed:
                self._schedule_status_refresh()
                raise HomeAssistantError(_UNCONFIRMED) from None
            raise HomeAssistantError("Sharp sign-in expired") from None
        except SharpBusy:
            self._schedule_status_refresh()
            if confirm_failed:
                raise HomeAssistantError(_UNCONFIRMED) from None
            raise HomeAssistantError(
                "The air conditioner is busy. Try again in a few seconds."
            ) from None
        except SharpRefused as err:
            self._schedule_status_refresh()
            if confirm_failed:
                raise HomeAssistantError(_UNCONFIRMED) from None
            raise HomeAssistantError(f"Sharp refused the command (code {err.code})") from None
        except SharpAPIError as err:
            self._schedule_status_refresh()
            if confirm_failed:
                raise HomeAssistantError(_UNCONFIRMED) from None
            raise HomeAssistantError(
                f"The Sharp cloud rejected the request (HTTP {err.status})"
            ) from None
        except SharpConnectionError:
            self._schedule_status_refresh()
            if confirm_failed:
                raise HomeAssistantError(_UNCONFIRMED) from None
            raise HomeAssistantError("Could not reach the Sharp cloud") from None
        except SharpError:
            self._schedule_status_refresh()
            if confirm_failed:
                raise HomeAssistantError(_UNCONFIRMED) from None
            raise HomeAssistantError("The air conditioner rejected the command") from None

        if skipped:
            return
        if result in ("success", "verified"):
            self._apply_optimistic(key, updates)
        self._schedule_status_refresh()
        if result not in ("success", "verified"):
            if _is_fa(status):
                raise HomeAssistantError(_UNCONFIRMED)
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

    async def _confirm_fa(self, device: Aircon, updates: dict[str, Any]) -> str:
        """Read status after an FA unmatch. The frame is not sent again.

        Caller holds this box's lock. A reading that does not show the write
        stays ``unmatch`` so the caller raises and leaves the previous state.
        """
        await _pause(FA_VERIFY_S)
        payload = await self.client.box_info()
        _box_ids, devices = split_box_info(payload)
        current = next((item for item in devices if item.unique_key == device.unique_key), None)
        if current is not None and updates and _matches(current, updates):
            return "verified"
        return "unmatch"

    def _apply_optimistic(self, key: str, updates: dict[str, Any]) -> None:
        """Show this write now. Only the fields it changes get a new deadline.

        An earlier field keeps the deadline it already has. A stale poll shows
        both until each one expires or the poll matches that field.
        """
        if not updates:
            return
        now = _monotonic()
        fields = self._pending.setdefault(key, {})
        for name in list(fields):
            if now >= fields[name][1]:
                fields.pop(name, None)
        for name, value in updates.items():
            fields[name] = (value, now + PENDING_GRACE_S, now)
        if not self.data or key not in self.data:
            if not fields:
                self._pending.pop(key, None)
            return
        if key not in self._fresh:
            self._fresh[key] = self.data[key]
        active = {name: stored[0] for name, stored in fields.items()}
        if not fields:
            self._pending.pop(key, None)
        data = dict(self.data)
        base = self._fresh[key]
        data[key] = with_updates(base, **active) if active else base
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

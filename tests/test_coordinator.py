"""An accepted write refreshes status once. It does not send the command again."""

from __future__ import annotations

from datetime import timedelta
from unittest.mock import patch

import pytest
from homeassistant.exceptions import ConfigEntryAuthFailed, HomeAssistantError
from homeassistant.helpers.event import async_call_later as real_call_later
from homeassistant.helpers.update_coordinator import UpdateFailed
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_fire_time_changed,
)

from custom_components.sharp_cocoro_air.client import (
    SharpAPIError,
    SharpAuthError,
    SharpConnectionError,
    SharpTerminalRefused,
)
from custom_components.sharp_cocoro_air.const import CONF_SCAN_INTERVAL, DOMAIN
from custom_components.sharp_cocoro_air.coordinator import (
    _AUTH_FAILURES,
    SharpCoordinator,
    clear_auth_failures,
)
from custom_components.sharp_cocoro_air.protocol import Aircon

_STATUS = [{"statusCode": "a0", "valueType": "valueSingle", "valueSingle": {"code": "41"}}]
_FA = [{"statusCode": "fa", "valueType": "valueBinary", "valueBinary": {"code": "00"}}]


class _Cloud:
    """Count commands and status reads. No network."""

    def __init__(self, result: str) -> None:
        self.result = result
        self.sent: list[list[dict]] = []
        self.box_reads = 0

    async def send_control(self, device: Aircon, status: list[dict]) -> str:
        self.sent.append(status)
        return self.result

    async def box_info(self) -> dict:
        self.box_reads += 1
        return {"devices": []}

    async def pair_unlisted(self, payload: dict) -> None:
        return None

    def export_cookies(self) -> list[dict]:
        return []


def _coordinator(hass, cloud: _Cloud) -> tuple[SharpCoordinator, Aircon]:
    entry = MockConfigEntry(domain=DOMAIN, data={})
    entry.add_to_hass(hass)
    coordinator = SharpCoordinator(hass, entry, cloud)  # type: ignore[arg-type]
    device = Aircon(
        box_id="box-1",
        device_id=1,
        echonet_node="00-11-22-33-44-55",
        echonet_object="013001",
        name="Living Room",
        model="AY-XP12YHE",
        setpoint=21.0,
    )
    coordinator.data = {device.unique_key: device}
    return coordinator, device


def _spy(coordinator: SharpCoordinator, recorded: list[tuple[float, bool]]):
    def spy(hass, delay, action):
        box_locked = any(lock.locked() for lock in coordinator._box_locks.values())
        recorded.append((delay, coordinator._poll_lock.locked() or box_locked))
        return real_call_later(hass, delay, action)

    return spy


async def _advance(hass, seconds: int) -> None:
    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=seconds))
    await hass.async_block_till_done()


async def test_success_schedules_one_status_refresh(hass):
    """A successful controlResult reads status 15 seconds later, outside the lock."""
    cloud = _Cloud("success")
    coordinator, device = _coordinator(hass, cloud)
    recorded: list[tuple[float, bool]] = []
    try:
        with patch(
            "custom_components.sharp_cocoro_air.coordinator.async_call_later",
            side_effect=_spy(coordinator, recorded),
        ):
            await coordinator.async_send(device.unique_key, _STATUS, setpoint=22.0)

        assert coordinator.data[device.unique_key].setpoint == 22.0
        assert recorded == [(15, False)]
        assert len(cloud.sent) == 1
        await _advance(hass, 20)
        assert cloud.box_reads == 1
        assert len(cloud.sent) == 1
    finally:
        await coordinator.async_shutdown()


async def test_unmatch_schedules_one_status_refresh(hass, monkeypatch):
    """An FA unmatch is confirmed by status and is not sent again.

    The confirm read happens during the send. The 15-second refresh still runs
    afterwards, outside the box lock.
    """

    async def pause(_seconds: float) -> None:
        return None

    monkeypatch.setattr(
        "custom_components.sharp_cocoro_air.coordinator._pause",
        pause,
    )
    cloud = _Cloud("unmatch")
    coordinator, device = _coordinator(hass, cloud)
    recorded: list[tuple[float, bool]] = []
    try:
        with (
            patch(
                "custom_components.sharp_cocoro_air.coordinator.async_call_later",
                side_effect=_spy(coordinator, recorded),
            ),
            pytest.raises(HomeAssistantError, match="couldn't be confirmed"),
        ):
            await coordinator.async_send(device.unique_key, _FA, vane_vertical="swing")

        assert recorded == [(15, False)]
        assert len(cloud.sent) == 1
        assert cloud.box_reads == 1
        assert coordinator.data[device.unique_key].vane_vertical is None
        await _advance(hass, 20)
        assert cloud.box_reads == 2
        assert len(cloud.sent) == 1
    finally:
        await coordinator.async_shutdown()


async def test_status_refresh_does_not_resend_the_command(hass):
    """The follow-up read calls boxInfo. It does not call deviceControl again."""
    cloud = _Cloud("timeout")
    coordinator, device = _coordinator(hass, cloud)
    try:
        with pytest.raises(HomeAssistantError):
            await coordinator.async_send(device.unique_key, _STATUS, fan="auto")

        await _advance(hass, 20)
        assert cloud.box_reads == 1
        assert cloud.sent == [_STATUS]
    finally:
        await coordinator.async_shutdown()


class _AuthCloud:
    """boxInfo that fails until ``exc`` is cleared."""

    def __init__(self, exc: Exception | None) -> None:
        self.exc = exc

    async def box_info(self) -> dict:
        if self.exc is not None:
            raise self.exc
        return {"devices": []}

    async def pair_unlisted(self, payload: dict) -> None:
        return None

    def export_cookies(self) -> list[dict]:
        return []


def _auth_coordinator(hass, cloud: _AuthCloud) -> SharpCoordinator:
    entry = MockConfigEntry(domain=DOMAIN, data={})
    entry.add_to_hass(hass)
    return SharpCoordinator(hass, entry, cloud)  # type: ignore[arg-type]


async def test_one_rejected_session_is_update_failed_and_the_third_reauths(hass):
    """The first two sign-in failures stay unavailable. The third starts reauth."""
    cloud = _AuthCloud(SharpAuthError("Sharp sign-in expired"))
    coordinator = _auth_coordinator(hass, cloud)
    try:
        for _attempt in range(2):
            with pytest.raises(UpdateFailed):
                await coordinator._async_update_data()
        assert coordinator._auth_failures == 2
        with pytest.raises(ConfigEntryAuthFailed):
            await coordinator._async_update_data()
    finally:
        await coordinator.async_shutdown()


async def test_successful_poll_resets_the_auth_failure_count(hass):
    """A good poll clears the streak, so two later failures do not reauth."""
    cloud = _AuthCloud(SharpAuthError("Sharp sign-in expired"))
    coordinator = _auth_coordinator(hass, cloud)
    try:
        with pytest.raises(UpdateFailed):
            await coordinator._async_update_data()
        cloud.exc = None
        await coordinator._async_update_data()
        assert coordinator._auth_failures == 0
        cloud.exc = SharpAuthError("Sharp sign-in expired")
        for _attempt in range(2):
            with pytest.raises(UpdateFailed):
                await coordinator._async_update_data()
        assert coordinator._auth_failures == 2
    finally:
        await coordinator.async_shutdown()


async def test_refused_terminal_login_reauths_immediately(hass):
    """A refused terminal id does not wait for three polls."""
    cloud = _AuthCloud(SharpTerminalRefused())
    coordinator = _auth_coordinator(hass, cloud)
    try:
        with pytest.raises(ConfigEntryAuthFailed):
            await coordinator._async_update_data()
        assert coordinator._auth_failures == 0
        assert coordinator.entry.entry_id not in hass.data.get(_AUTH_FAILURES, {})
    finally:
        await coordinator.async_shutdown()


async def test_the_refusal_message_alone_does_not_skip_the_grace(hass):
    """Reauth matches SharpTerminalRefused, not the message text."""
    cloud = _AuthCloud(SharpAuthError("terminal login refused"))
    coordinator = _auth_coordinator(hass, cloud)
    try:
        with pytest.raises(UpdateFailed):
            await coordinator._async_update_data()
        assert coordinator._auth_failures == 1
    finally:
        await coordinator.async_shutdown()


async def test_auth_failures_carry_across_a_new_setup(hass):
    """A setup retry builds a new coordinator and still counts the earlier polls."""
    cloud = _AuthCloud(SharpAuthError("Sharp sign-in expired"))
    entry = MockConfigEntry(domain=DOMAIN, data={})
    entry.add_to_hass(hass)
    first = SharpCoordinator(hass, entry, cloud)  # type: ignore[arg-type]
    try:
        with pytest.raises(UpdateFailed):
            await first._async_update_data()
    finally:
        await first.async_shutdown()

    second = SharpCoordinator(hass, entry, cloud)  # type: ignore[arg-type]
    try:
        with pytest.raises(UpdateFailed):
            await second._async_update_data()
        assert second._auth_failures == 2
    finally:
        await second.async_shutdown()

    third = SharpCoordinator(hass, entry, cloud)  # type: ignore[arg-type]
    try:
        with pytest.raises(ConfigEntryAuthFailed):
            await third._async_update_data()
    finally:
        await third.async_shutdown()


async def test_reauth_clears_the_count_so_one_later_failure_waits(hass):
    """After the third rejection starts reauth, a later rejection waits again."""
    cloud = _AuthCloud(SharpAuthError("Sharp sign-in expired"))
    entry = MockConfigEntry(domain=DOMAIN, data={})
    entry.add_to_hass(hass)
    first = SharpCoordinator(hass, entry, cloud)  # type: ignore[arg-type]
    try:
        for _attempt in range(2):
            with pytest.raises(UpdateFailed):
                await first._async_update_data()
        with pytest.raises(ConfigEntryAuthFailed):
            await first._async_update_data()
        assert first._auth_failures == 0
    finally:
        await first.async_shutdown()

    second = SharpCoordinator(hass, entry, cloud)  # type: ignore[arg-type]
    try:
        with pytest.raises(UpdateFailed):
            await second._async_update_data()
        assert second._auth_failures == 1
    finally:
        await second.async_shutdown()


async def test_unload_clears_a_failure_streak(hass):
    """A reload drops the count. One rejection after that does not start reauth."""
    cloud = _AuthCloud(SharpAuthError("Sharp sign-in expired"))
    entry = MockConfigEntry(domain=DOMAIN, data={})
    entry.add_to_hass(hass)
    first = SharpCoordinator(hass, entry, cloud)  # type: ignore[arg-type]
    try:
        for _attempt in range(2):
            with pytest.raises(UpdateFailed):
                await first._async_update_data()
        assert first._auth_failures == 2
        clear_auth_failures(hass, entry.entry_id)
    finally:
        await first.async_shutdown()

    second = SharpCoordinator(hass, entry, cloud)  # type: ignore[arg-type]
    try:
        with pytest.raises(UpdateFailed):
            await second._async_update_data()
        assert second._auth_failures == 1
    finally:
        await second.async_shutdown()


async def test_failed_fa_read_back_still_schedules_a_refresh(hass, monkeypatch):
    """A status read that errors still arms the refresh, and says it was not confirmed."""

    async def pause(_seconds: float) -> None:
        return None

    monkeypatch.setattr(
        "custom_components.sharp_cocoro_air.coordinator._pause",
        pause,
    )
    cloud = _Cloud("unmatch")

    async def fail_read() -> dict:
        cloud.box_reads += 1
        raise SharpConnectionError("Could not reach the Sharp cloud")

    cloud.box_info = fail_read  # type: ignore[method-assign]
    coordinator, device = _coordinator(hass, cloud)
    try:
        with pytest.raises(HomeAssistantError, match="couldn't be confirmed"):
            await coordinator.async_send(device.unique_key, _FA, vane_vertical="swing")
        assert coordinator._refresh_unsub is not None
        assert len(cloud.sent) == 1
    finally:
        await coordinator.async_shutdown()


async def test_rate_limit_server_and_network_errors_back_off(hass):
    """403, 429, 5xx, and a network error wait longer. A later success starts the wait over."""
    for exc in (
        SharpAPIError(403),
        SharpAPIError(429),
        SharpAPIError(500),
        SharpAPIError(503),
        SharpConnectionError("Could not reach the Sharp cloud"),
    ):
        cloud = _AuthCloud(exc)
        coordinator = _auth_coordinator(hass, cloud)
        try:
            delays = []
            for _attempt in range(5):
                with pytest.raises(UpdateFailed) as caught:
                    await coordinator._async_update_data()
                delays.append(caught.value.retry_after)
            assert delays == [30.0, 60.0, 120.0, 300.0, 300.0]
            assert coordinator.update_interval == timedelta(seconds=15)
            assert coordinator._auth_failures == 0
            cloud.exc = None
            await coordinator._async_update_data()
            cloud.exc = exc
            with pytest.raises(UpdateFailed) as caught:
                await coordinator._async_update_data()
            assert caught.value.retry_after == 30.0
        finally:
            await coordinator.async_shutdown()


async def test_old_home_assistant_stretches_the_poll_interval(hass, monkeypatch):
    """Before 2025.11.2, UpdateFailed rejects retry_after, so the interval itself grows."""

    class _LegacyUpdateFailed(Exception):
        def __init__(self, message: str = "", **kwargs: object) -> None:
            if kwargs:
                raise TypeError("UpdateFailed() takes no keyword arguments")
            super().__init__(message)
            self.retry_after = None

    monkeypatch.setattr(
        "custom_components.sharp_cocoro_air.coordinator.UpdateFailed",
        _LegacyUpdateFailed,
    )
    cloud = _AuthCloud(SharpAPIError(429))
    coordinator = _auth_coordinator(hass, cloud)
    try:
        waits = []
        for _attempt in range(5):
            with pytest.raises(_LegacyUpdateFailed) as caught:
                await coordinator._async_update_data()
            assert caught.value.retry_after is None
            waits.append(coordinator.update_interval)
        assert waits == [timedelta(seconds=seconds) for seconds in (30, 60, 120, 300, 300)]
        cloud.exc = None
        await coordinator._async_update_data()
        assert coordinator.update_interval == timedelta(seconds=15)
    finally:
        await coordinator.async_shutdown()


async def test_other_poll_errors_do_not_back_off(hass):
    """A 400 and a sign-in failure keep the normal poll. They do not stretch the wait."""
    for exc in (SharpAPIError(400), SharpAuthError("Sharp sign-in expired")):
        cloud = _AuthCloud(exc)
        coordinator = _auth_coordinator(hass, cloud)
        try:
            with pytest.raises(UpdateFailed) as caught:
                await coordinator._async_update_data()
            assert caught.value.retry_after is None
            assert coordinator._poll_failures == 0
        finally:
            await coordinator.async_shutdown()


async def test_backoff_does_not_poll_faster_than_the_chosen_interval(hass):
    """A 60 second poll stays at 60 on the first failure, then follows the longer steps."""
    cloud = _AuthCloud(SharpAPIError(429))
    entry = MockConfigEntry(domain=DOMAIN, data={}, options={CONF_SCAN_INTERVAL: 60})
    entry.add_to_hass(hass)
    coordinator = SharpCoordinator(hass, entry, cloud)  # type: ignore[arg-type]
    try:
        delays = []
        for _attempt in range(4):
            with pytest.raises(UpdateFailed) as caught:
                await coordinator._async_update_data()
            delays.append(caught.value.retry_after)
        assert delays == [60.0, 60.0, 120.0, 300.0]
    finally:
        await coordinator.async_shutdown()


async def test_relogin_outage_is_update_failed_and_does_not_count(hass):
    """A 500 or a timeout while refreshing the session is not a reauth."""
    for exc in (
        SharpConnectionError("Could not reach the Sharp cloud"),
        SharpAPIError(503),
    ):
        cloud = _AuthCloud(exc)
        coordinator = _auth_coordinator(hass, cloud)
        try:
            with pytest.raises(UpdateFailed):
                await coordinator._async_update_data()
            assert coordinator._auth_failures == 0
        finally:
            await coordinator.async_shutdown()

"""An accepted write refreshes status once. It does not send the command again."""

from __future__ import annotations

from datetime import timedelta
from unittest.mock import patch

import pytest
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.event import async_call_later as real_call_later
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_fire_time_changed,
)

from custom_components.sharp_cocoro_air.const import DOMAIN
from custom_components.sharp_cocoro_air.coordinator import SharpCoordinator
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


async def test_unmatch_schedules_one_status_refresh(hass):
    """An FA unmatch is still a rejection, and status is read 15 seconds later."""
    cloud = _Cloud("unmatch")
    coordinator, device = _coordinator(hass, cloud)
    recorded: list[tuple[float, bool]] = []
    try:
        with (
            patch(
                "custom_components.sharp_cocoro_air.coordinator.async_call_later",
                side_effect=_spy(coordinator, recorded),
            ),
            pytest.raises(HomeAssistantError, match="rejected the command"),
        ):
            await coordinator.async_send(device.unique_key, _FA, vane_vertical="swing")

        assert recorded == [(15, False)]
        assert len(cloud.sent) == 1
        await _advance(hass, 20)
        assert cloud.box_reads == 1
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

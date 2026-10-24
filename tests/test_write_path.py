"""Rapid taps, per-box settle, and one retry when unmatch is only lag."""

from __future__ import annotations

import asyncio
import time
from datetime import timedelta

import aiohttp
import pytest
import pytest_socket
from aiohttp import web
from homeassistant.components.climate import ATTR_TEMPERATURE, HVACMode
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers.event import async_call_later as real_call_later
from homeassistant.util import dt as dt_util
from homeassistant.util.async_ import get_scheduled_timer_handles
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_fire_time_changed,
)

from custom_components.sharp_cocoro_air import climate as climate_mod
from custom_components.sharp_cocoro_air import coordinator as coordinator_mod
from custom_components.sharp_cocoro_air.client import (
    SharpAPIError,
    SharpAuthError,
    SharpBusy,
    SharpClient,
    SharpError,
    SharpRefused,
    SharpTerminalRefused,
)
from custom_components.sharp_cocoro_air.climate import SharpClimate
from custom_components.sharp_cocoro_air.const import (
    DOMAIN,
    FA_VERIFY_S,
    PENDING_GRACE_S,
    RETRY_EPCS,
    SETPOINT_DEBOUNCE_S,
    WRITE_SETTLE_S,
)
from custom_components.sharp_cocoro_air.coordinator import SharpCoordinator, _can_retry
from custom_components.sharp_cocoro_air.protocol import (
    Aircon,
    Capabilities,
    cmd_setpoint,
    cmd_vanes,
)

_B3 = [{"statusCode": "b3", "valueType": "valueRange", "valueRange": {"code": "22"}}]
_FA = [{"statusCode": "fa", "valueType": "valueBinary", "valueBinary": {"code": "00"}}]


class _Clock:
    """Virtual clock for settle, verify, and busy waits."""

    def __init__(self) -> None:
        self.now = 1_000.0

    def monotonic(self) -> float:
        return self.now

    async def pause(self, seconds: float) -> None:
        self.now += seconds
        await asyncio.sleep(0)


class _Cloud:
    """Scripted control results and boxInfo payloads. No network."""

    def __init__(self, clock: _Clock) -> None:
        self.clock = clock
        self.sent: list[tuple[str, list[dict]]] = []
        self.starts: list[tuple[str, float]] = []
        self.box_reads = 0
        self.results: list[str | Exception] = []
        self.payload: dict | None = {"devices": []}
        self.hold: asyncio.Event | None = None

    async def send_control(self, device: Aircon, status: list[dict]) -> str:
        self.starts.append((device.box_id, self.clock.now))
        self.sent.append((device.box_id, status))
        if self.hold is not None:
            await self.hold.wait()
        if not self.results:
            return "success"
        item = self.results.pop(0)
        if isinstance(item, Exception):
            raise item
        return item

    async def box_info(self) -> dict:
        self.box_reads += 1
        return self.payload or {"devices": []}

    async def pair_unlisted(self, payload: dict) -> None:
        return None

    def export_cookies(self) -> list[dict]:
        return []


def _install_clock(monkeypatch) -> _Clock:
    clock = _Clock()
    monkeypatch.setattr(coordinator_mod, "_monotonic", clock.monotonic)
    monkeypatch.setattr(coordinator_mod, "_pause", clock.pause)
    return clock


def _device(box_id: str = "box-1", device_id: int = 1, setpoint: float = 21.0) -> Aircon:
    return Aircon(
        box_id=box_id,
        device_id=device_id,
        echonet_node="00-11-22-33-44-55",
        echonet_object="013001",
        name="Living Room",
        model="AY-XP12YHE",
        setpoint=setpoint,
        mode="cool",
        power=True,
    )


def _coordinator(hass, cloud: _Cloud, *devices: Aircon) -> SharpCoordinator:
    entry = MockConfigEntry(domain=DOMAIN, data={})
    entry.add_to_hass(hass)
    coordinator = SharpCoordinator(hass, entry, cloud)  # type: ignore[arg-type]
    coordinator.data = {device.unique_key: device for device in devices}
    return coordinator


def _status_payload(temp: int, *, box_id: str = "box-1", device_id: int = 1) -> dict:
    """boxInfo with one B3 setpoint. Enough for the verify read to decode."""
    raw = bytes([0x01, 0x30, 0x01, 0x05, 0xFF, 0x01, 0x72, 0x01, 0xB3, 0x01, temp])
    return {
        "devices": [
            {
                "boxId": box_id,
                "deviceId": device_id,
                "echonetNode": "00-11-22-33-44-55",
                "echonetObject": "013001",
                "model": "AY-XP12YHE",
                "labelData": {"deviceType": "AIR_CON", "name": "Living Room"},
                "echonetProperty": raw.hex(),
            }
        ]
    }


def _live_timers(hass) -> int:
    return sum(1 for handle in get_scheduled_timer_handles(hass.loop) if not handle.cancelled())


async def _climate(hass, coordinator: SharpCoordinator, device: Aircon) -> SharpClimate:
    entity = SharpClimate(coordinator, device.unique_key)
    entity.hass = hass
    entity.entity_id = "climate.living_room"
    await entity.async_added_to_hass()
    entity.async_write_ha_state()
    return entity


async def _advance(hass, seconds: float) -> None:
    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=seconds))
    await hass.async_block_till_done()


async def test_rapid_taps_send_the_last_temperature_once(hass, monkeypatch):
    """Taps at 0/0.4/0.8/1.2 s become one write about 1.5 s after the last tap."""
    clock = _install_clock(monkeypatch)
    cloud = _Cloud(clock)
    device = _device()
    coordinator = _coordinator(hass, cloud, device)
    entity = await _climate(hass, coordinator, device)

    async def tap(delay: float, temperature: float) -> None:
        await asyncio.sleep(delay)
        await entity.async_set_temperature(**{ATTR_TEMPERATURE: temperature})

    tasks = [
        hass.async_create_task(tap(0.0, 21)),
        hass.async_create_task(tap(0.4, 22)),
        hass.async_create_task(tap(0.8, 23)),
        hass.async_create_task(tap(1.2, 24)),
    ]
    try:
        await asyncio.sleep(1.5)
        assert cloud.sent == []
        assert entity.target_temperature == 24
        async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=0.3))
        await asyncio.sleep(0)
        assert cloud.sent == []
        async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=1.8))
        await hass.async_block_till_done()
        assert len(cloud.sent) == 1
        assert cloud.sent[0][1][0]["valueRange"]["code"] == "24"
        for task in tasks:
            assert task.exception() is None
    finally:
        await entity.async_remove()
        await coordinator.async_shutdown()


async def test_same_box_waits_and_different_boxes_run_together(hass, monkeypatch):
    """The next write on a box waits out the settle gap. Two boxes do not."""
    clock = _install_clock(monkeypatch)
    cloud = _Cloud(clock)
    first = _device("box-1", 1)
    second = _device("box-2", 2)
    coordinator = _coordinator(hass, cloud, first, second)
    release = asyncio.Event()

    async def send_control(device: Aircon, status: list[dict]) -> str:
        cloud.starts.append((device.box_id, clock.now))
        cloud.sent.append((device.box_id, status))
        if len(cloud.starts) == 1:
            await release.wait()
        else:
            release.set()
        return "success"

    cloud.send_control = send_control  # type: ignore[method-assign]
    try:
        await asyncio.wait_for(
            asyncio.gather(
                coordinator.async_send(first.unique_key, _B3, setpoint=22.0),
                coordinator.async_send(second.unique_key, _B3, setpoint=22.0),
            ),
            timeout=1,
        )
        assert {item[0] for item in cloud.starts} == {"box-1", "box-2"}

        cloud.starts.clear()

        async def timed(device: Aircon, status: list[dict]) -> str:
            cloud.starts.append((device.box_id, clock.now))
            cloud.sent.append((device.box_id, status))
            return "success"

        cloud.send_control = timed  # type: ignore[method-assign]
        await coordinator.async_send(first.unique_key, _B3, setpoint=23.0)
        await coordinator.async_send(first.unique_key, _B3, setpoint=24.0)
        assert cloud.starts[1][1] - cloud.starts[0][1] >= WRITE_SETTLE_S
    finally:
        await coordinator.async_shutdown()


async def test_poll_does_not_wait_for_a_long_control_result(hass, monkeypatch):
    """A status poll uses its own lock, so a slow controlResult does not block it."""
    clock = _install_clock(monkeypatch)
    cloud = _Cloud(clock)
    device = _device()
    coordinator = _coordinator(hass, cloud, device)
    entered = asyncio.Event()
    release = asyncio.Event()

    async def send_control(device: Aircon, status: list[dict]) -> str:
        entered.set()
        await release.wait()
        return "success"

    cloud.send_control = send_control  # type: ignore[method-assign]
    send_task = hass.async_create_task(
        coordinator.async_send(device.unique_key, _B3, setpoint=22.0)
    )
    try:
        await entered.wait()
        data = await asyncio.wait_for(coordinator._async_update_data(), timeout=1)
        assert data == {}
        assert cloud.box_reads == 1
    finally:
        release.set()
        await send_task
        await coordinator.async_shutdown()


async def test_stale_poll_keeps_the_pending_target_until_it_matches_or_expires(hass, monkeypatch):
    """A poll that still shows the old setpoint does not wipe the write."""
    clock = _install_clock(monkeypatch)
    cloud = _Cloud(clock)
    device = _device()
    coordinator = _coordinator(hass, cloud, device)
    try:
        cloud.payload = _status_payload(21)
        await coordinator.async_send(device.unique_key, _B3, setpoint=22.0)
        assert coordinator.data[device.unique_key].setpoint == 22.0
        data = await coordinator._async_update_data()
        assert data[device.unique_key].setpoint == 22.0
        assert device.unique_key in coordinator._pending

        cloud.payload = _status_payload(22)
        data = await coordinator._async_update_data()
        assert data[device.unique_key].setpoint == 22.0
        assert device.unique_key not in coordinator._pending

        cloud.payload = _status_payload(21)
        await coordinator.async_send(device.unique_key, _B3, setpoint=23.0)
        clock.now += PENDING_GRACE_S
        data = await coordinator._async_update_data()
        assert device.unique_key not in coordinator._pending
        assert data[device.unique_key].setpoint == 21.0
    finally:
        await coordinator.async_shutdown()


async def test_rejected_result_is_not_shown_for_thirty_seconds(hass, monkeypatch):
    """A rejected result does not change the unit. The status read is still scheduled."""
    clock = _install_clock(monkeypatch)
    cloud = _Cloud(clock)
    cloud.results = ["unmatch", "timeout"]
    device = _device()
    coordinator = _coordinator(hass, cloud, device)
    vanes = cmd_vanes(vertical="swing", horizontal=None, frame_end=80)
    try:
        with pytest.raises(HomeAssistantError, match="couldn't be confirmed"):
            await coordinator.async_send(
                device.unique_key, vanes, vane_vertical="swing", setpoint=26.0
            )
        current = coordinator.data[device.unique_key]
        assert current.setpoint == 21.0
        assert current.vane_vertical is None
        assert device.unique_key not in coordinator._pending
        assert coordinator._refresh_unsub is not None

        with pytest.raises(HomeAssistantError, match="rejected the command"):
            await coordinator.async_send(device.unique_key, _B3, setpoint=22.0)
        assert coordinator.data[device.unique_key].setpoint == 21.0
        assert device.unique_key not in coordinator._pending
        assert coordinator._refresh_unsub is not None
    finally:
        await coordinator.async_shutdown()


async def test_failed_setpoint_keeps_a_newer_tap(hass, monkeypatch):
    """A send that fails while the user taps again still sends the newer temperature."""
    clock = _install_clock(monkeypatch)
    cloud = _Cloud(clock)
    cloud.hold = asyncio.Event()
    cloud.results = [SharpError("rejected"), "success"]
    cloud.payload = _status_payload(21)
    device = _device()
    coordinator = _coordinator(hass, cloud, device)
    entity = await _climate(hass, coordinator, device)
    first = hass.async_create_task(entity.async_set_temperature(**{ATTR_TEMPERATURE: 22}))
    second: asyncio.Task | None = None
    try:
        await asyncio.sleep(0)
        async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=SETPOINT_DEBOUNCE_S))
        await asyncio.sleep(0)
        assert len(cloud.sent) == 1
        assert cloud.sent[0][1][0]["valueRange"]["code"] == "22"

        second = hass.async_create_task(entity.async_set_temperature(**{ATTR_TEMPERATURE: 24}))
        await asyncio.sleep(0)
        assert entity._pending_target == 24
        assert entity._waiters
        cloud.hold.set()
        with pytest.raises(HomeAssistantError):
            await first
        assert entity._pending_target == 24
        assert not second.done()

        async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=SETPOINT_DEBOUNCE_S))
        await second
        assert len(cloud.sent) == 2
        assert cloud.sent[1][1][0]["valueRange"]["code"] == "24"
    finally:
        if not first.done():
            first.cancel()
        if second is not None and not second.done():
            second.cancel()
        await entity.async_remove()
        await coordinator.async_shutdown()


async def test_b3_unmatch_that_was_applied_is_not_an_error(hass, monkeypatch):
    """Status already shows the setpoint, so unmatch is not resent and not raised."""
    clock = _install_clock(monkeypatch)
    cloud = _Cloud(clock)
    cloud.results = ["unmatch"]
    cloud.payload = _status_payload(22)
    device = _device()
    coordinator = _coordinator(hass, cloud, device)
    try:
        await coordinator.async_send(device.unique_key, _B3, setpoint=22.0)
        assert len(cloud.sent) == 1
        assert cloud.box_reads == 1
        assert coordinator.data[device.unique_key].setpoint == 22.0
    finally:
        await coordinator.async_shutdown()


async def test_b3_unmatch_is_resent_once(hass, monkeypatch):
    """A setpoint the unit did not apply is sent once more, then reported."""
    clock = _install_clock(monkeypatch)
    cloud = _Cloud(clock)
    cloud.results = ["unmatch", "unmatch"]
    cloud.payload = _status_payload(21)
    device = _device()
    coordinator = _coordinator(hass, cloud, device)
    try:
        with pytest.raises(HomeAssistantError, match="rejected the command"):
            await coordinator.async_send(device.unique_key, _B3, setpoint=22.0)
        assert len(cloud.sent) == 2
        assert cloud.box_reads == 1
    finally:
        await coordinator.async_shutdown()


def _fa_payload(
    *,
    vertical: int = 0x00,
    horizontal: int = 0x01,
    b3: int | None = None,
    half: int | None = None,
    half_degree: bool = False,
) -> dict:
    """boxInfo with vane bytes, and an optional half-degree setpoint."""
    props: list[tuple[int, bytes]] = []
    fa = bytearray(80)
    fa[48] = vertical
    fa[49] = horizontal
    if half is not None:
        fa[26] = half
    props.append((0xFA, bytes(fa)))
    if b3 is not None:
        props.append((0xB3, bytes([b3])))
    if half_degree:
        capabilities = bytearray(32)
        capabilities[8] = 0x10
        props.append((0xF0, bytes(capabilities)))
    body = bytearray([0x01, 0x30, 0x01, 0x05, 0xFF, 0x01, 0x72, len(props)])
    for epc, edt in props:
        body.append(epc)
        body.append(len(edt))
        body.extend(edt)
    return {
        "devices": [
            {
                "boxId": "box-1",
                "deviceId": 1,
                "echonetNode": "00-11-22-33-44-55",
                "echonetObject": "013001",
                "model": "AY-XP12YHE",
                "labelData": {"deviceType": "AIR_CON", "name": "Living Room"},
                "echonetProperty": body.hex(),
            }
        ]
    }


async def test_fa_unmatch_that_applied_is_not_resent(hass, monkeypatch):
    """FA unmatch is confirmed from status after 15 s. The frame is sent once."""
    clock = _install_clock(monkeypatch)
    cloud = _Cloud(clock)
    cloud.results = ["unmatch"]
    cloud.payload = _fa_payload(vertical=0x07, horizontal=0x01)
    device = _device()
    coordinator = _coordinator(hass, cloud, device)
    status = cmd_vanes(vertical="swing", horizontal="straight", frame_end=80)
    try:
        await coordinator.async_send(
            device.unique_key,
            status,
            vane_vertical="swing",
            vane_horizontal="straight",
        )
        assert len(cloud.sent) == 1
        assert cloud.box_reads == 1
        assert clock.now == 1_000.0 + FA_VERIFY_S
        current = coordinator.data[device.unique_key]
        assert current.vane_vertical == "swing"
        assert current.vane_horizontal == "straight"
    finally:
        await coordinator.async_shutdown()


async def test_fa_unmatch_that_did_not_apply_is_an_error(hass, monkeypatch):
    """A status read that still shows the old vane raises and does not change state."""
    clock = _install_clock(monkeypatch)
    cloud = _Cloud(clock)
    cloud.results = ["unmatch"]
    cloud.payload = _fa_payload(vertical=0x00, horizontal=0x01)
    device = _device()
    device.vane_vertical = "auto"
    device.vane_horizontal = "straight"
    coordinator = _coordinator(hass, cloud, device)
    status = cmd_vanes(vertical="swing", horizontal="straight", frame_end=80)
    try:
        with pytest.raises(HomeAssistantError, match="couldn't be confirmed"):
            await coordinator.async_send(
                device.unique_key,
                status,
                vane_vertical="swing",
                vane_horizontal="straight",
            )
        assert coordinator._refresh_unsub is not None
        assert len(cloud.sent) == 1
        assert cloud.box_reads == 1
        current = coordinator.data[device.unique_key]
        assert current.vane_vertical == "auto"
        assert device.unique_key not in coordinator._pending
    finally:
        await coordinator.async_shutdown()


async def test_half_degree_unmatch_that_did_not_apply_is_an_error(hass, monkeypatch):
    """21.5 that is still 21 after the status read is not shown as 21.5."""
    clock = _install_clock(monkeypatch)
    cloud = _Cloud(clock)
    cloud.results = ["unmatch"]
    cloud.payload = _fa_payload(b3=21, half=0x2A, half_degree=True)
    device = _device(setpoint=21.0)
    coordinator = _coordinator(hass, cloud, device)
    status = cmd_setpoint(21.5, half_degree=True, frame_end=80)
    try:
        with pytest.raises(HomeAssistantError, match="couldn't be confirmed"):
            await coordinator.async_send(device.unique_key, status, setpoint=21.5)
        assert coordinator._refresh_unsub is not None
        assert len(cloud.sent) == 1
        assert coordinator.data[device.unique_key].setpoint == 21.0
        assert device.unique_key not in coordinator._pending
        assert clock.now == 1_000.0 + FA_VERIFY_S
    finally:
        await coordinator.async_shutdown()


async def test_vane_write_sends_both_bytes(hass, monkeypatch):
    """Changing one vane includes the other vane's current position."""
    clock = _install_clock(monkeypatch)
    cloud = _Cloud(clock)
    device = _device()
    device.vane_vertical = "auto"
    device.vane_horizontal = "left"
    device.caps = Capabilities(
        vertical_vanes=("auto", "up", "level", "down1", "down2", "down", "swing"),
        horizontal_vanes=("straight", "left", "right", "swing"),
    )
    coordinator = _coordinator(hass, cloud, device)
    entity = await _climate(hass, coordinator, device)
    try:
        await entity.async_set_swing_mode("down")
        raw = bytes.fromhex(cloud.sent[0][1][0]["valueBinary"]["code"])
        assert raw[48] == 0x05
        assert raw[49] == 0x02

        await entity.async_set_swing_horizontal_mode("swing")
        raw = bytes.fromhex(cloud.sent[1][1][0]["valueBinary"]["code"])
        assert raw[48] == 0x05
        assert raw[49] == 0x0F
    finally:
        await entity.async_remove()
        await coordinator.async_shutdown()


async def test_timeout_is_not_resent(hass, monkeypatch):
    """A timeout raises and refreshes. It is not a second deviceControl."""
    clock = _install_clock(monkeypatch)
    cloud = _Cloud(clock)
    cloud.results = ["timeout"]
    device = _device()
    coordinator = _coordinator(hass, cloud, device)
    try:
        with pytest.raises(HomeAssistantError, match="rejected the command"):
            await coordinator.async_send(device.unique_key, _B3, setpoint=22.0)
        assert len(cloud.sent) == 1
        assert cloud.box_reads == 0
        assert coordinator._refresh_unsub is not None
        await _advance(hass, 20)
        assert cloud.box_reads == 1
        assert len(cloud.sent) == 1
    finally:
        await coordinator.async_shutdown()


async def test_busy_is_retried_once_after_ten_seconds(hass, monkeypatch):
    """E1001001 waits 10 s and sends once more. Any other code does not retry.

    A failed write still schedules the status refresh.
    """
    clock = _install_clock(monkeypatch)
    cloud = _Cloud(clock)
    cloud.results = [SharpBusy("E1001001"), "success"]
    device = _device()
    coordinator = _coordinator(hass, cloud, device)
    try:
        await coordinator.async_send(device.unique_key, _B3, setpoint=22.0)
        assert len(cloud.sent) == 2
        assert cloud.starts[1][1] - cloud.starts[0][1] >= 10

        coordinator._cancel_refresh()
        cloud.sent.clear()
        cloud.results = [SharpError("other")]
        before = cloud.box_reads
        with pytest.raises(HomeAssistantError, match="rejected the command"):
            await coordinator.async_send(device.unique_key, _B3, setpoint=23.0)
        assert len(cloud.sent) == 1
        assert coordinator._refresh_unsub is not None
        await _advance(hass, 20)
        assert cloud.box_reads == before + 1
    finally:
        await coordinator.async_shutdown()


async def test_auth_failure_before_accept_does_not_refresh_or_resend(hass, monkeypatch):
    """A 401 that never returns a control id does not arm the status timer.

    A rejection that is not a refused terminal id does not start reauth.
    """
    clock = _install_clock(monkeypatch)
    cloud = _Cloud(clock)
    cloud.results = [SharpAuthError("expired")]
    device = _device()
    coordinator = _coordinator(hass, cloud, device)
    reauth: list[bool] = []
    coordinator.entry.async_start_reauth = lambda *_args, **_kwargs: reauth.append(True)
    try:
        with pytest.raises(HomeAssistantError, match="sign-in expired"):
            await coordinator.async_send(device.unique_key, _B3, setpoint=22.0)
        assert len(cloud.sent) == 1
        assert reauth == []
        assert coordinator._refresh_unsub is None
        await _advance(hass, 20)
        assert cloud.box_reads == 0
        assert len(cloud.sent) == 1
    finally:
        await coordinator.async_shutdown()


async def test_refused_terminal_on_a_write_starts_reauth(hass, monkeypatch):
    """Only a refused terminal id during a write asks the user to sign in again."""
    clock = _install_clock(monkeypatch)
    cloud = _Cloud(clock)
    cloud.results = [SharpTerminalRefused()]
    device = _device()
    coordinator = _coordinator(hass, cloud, device)
    reauth: list[bool] = []
    coordinator.entry.async_start_reauth = lambda *_args, **_kwargs: reauth.append(True)
    try:
        with pytest.raises(HomeAssistantError, match="sign in again"):
            await coordinator.async_send(device.unique_key, _B3, setpoint=22.0)
        assert reauth == [True]
        assert coordinator._refresh_unsub is None
    finally:
        await coordinator.async_shutdown()


async def test_writes_share_one_refresh_timer(hass, monkeypatch):
    """Ten writes leave one timer. Unload cancels it, and a late fire does nothing."""
    clock = _install_clock(monkeypatch)
    cloud = _Cloud(clock)
    device = _device()
    coordinator = _coordinator(hass, cloud, device)
    actions: list = []

    def spy(hass, delay, action):
        actions.append(action)
        return real_call_later(hass, delay, action)

    unload_before = len(coordinator.entry._on_unload)
    timers_before = _live_timers(hass)
    try:
        with pytest.MonkeyPatch.context() as patch:
            patch.setattr(coordinator_mod, "async_call_later", spy)
            for _ in range(10):
                await coordinator.async_send(device.unique_key, _B3, setpoint=22.0)
        assert len(coordinator.entry._on_unload) == unload_before
        assert len(actions) == 10
        assert _live_timers(hass) == timers_before + 1
        await coordinator.entry._async_process_on_unload(hass)
        await hass.async_block_till_done()
        assert _live_timers(hass) == timers_before
        await actions[-1](dt_util.utcnow())
        assert cloud.box_reads == 0
        assert len(cloud.sent) == 10
    finally:
        await coordinator.async_shutdown()


async def test_unload_cancels_a_pending_setpoint_and_fails_waiters(hass, monkeypatch):
    """Removing the entity cancels the debounce timer and wakes the waiting tap."""
    clock = _install_clock(monkeypatch)
    cloud = _Cloud(clock)
    device = _device()
    coordinator = _coordinator(hass, cloud, device)
    entity = await _climate(hass, coordinator, device)
    task = hass.async_create_task(entity.async_set_temperature(**{ATTR_TEMPERATURE: 22}))
    try:
        await asyncio.sleep(0)
        assert entity._debounce_unsub is not None
        await entity.async_remove()
        with pytest.raises(HomeAssistantError, match="not available"):
            await task
        await _advance(hass, 5)
        assert cloud.sent == []
    finally:
        await coordinator.async_shutdown()


async def test_mode_and_temperature_settle_between_b0_and_b3(hass, monkeypatch):
    """Mode is sent immediately. The setpoint follows after the settle gap."""
    clock = _install_clock(monkeypatch)
    cloud = _Cloud(clock)
    device = _device()
    coordinator = _coordinator(hass, cloud, device)
    entity = await _climate(hass, coordinator, device)
    task = hass.async_create_task(
        entity.async_set_temperature(**{ATTR_TEMPERATURE: 22, "hvac_mode": HVACMode.COOL})
    )
    try:
        await asyncio.sleep(0)
        assert len(cloud.sent) == 1
        codes = [prop["statusCode"] for prop in cloud.sent[0][1]]
        assert codes == ["80", "b0"]
        await _advance(hass, 1.8)
        assert len(cloud.sent) == 2
        assert cloud.sent[1][1][0]["statusCode"] == "b3"
        assert cloud.starts[1][1] - cloud.starts[0][1] >= WRITE_SETTLE_S
        assert task.exception() is None
    finally:
        await entity.async_remove()
        await coordinator.async_shutdown()


async def test_concurrent_401s_share_one_terminal_login():
    """Two status calls that both get 401 post setting/login once."""
    pytest_socket.enable_socket()
    logins = 0
    box_hits = 0
    release_login = asyncio.Event()

    async def handle(request: web.Request) -> web.StreamResponse:
        nonlocal logins, box_hits
        if request.path.endswith("/setting/boxInfo"):
            box_hits += 1
            if box_hits <= 2:
                if box_hits == 2:
                    release_login.set()
                return web.Response(status=401, text="")
            return web.json_response({"devices": []})
        if request.path.endswith("/setting/login/"):
            await asyncio.wait_for(release_login.wait(), timeout=2)
            logins += 1
            response = web.json_response({})
            response.set_cookie("JSESSIONID", "session", path="/")
            return response
        return web.Response(status=500, text="")

    app = web.Application()
    app.router.add_route("*", "/{tail:.*}", handle)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    port = site._server.sockets[0].getsockname()[1]
    connector = aiohttp.TCPConnector(resolver=aiohttp.resolver.ThreadedResolver())
    session = aiohttp.ClientSession(connector=connector, cookie_jar=aiohttp.CookieJar(unsafe=True))
    client = SharpClient(
        session, "https://example.invalid/terminal/1", base_url=f"http://127.0.0.1:{port}/"
    )
    try:
        await asyncio.wait_for(asyncio.gather(client.box_info(), client.box_info()), timeout=3)
    finally:
        await client.aclose()
        await runner.cleanup()

    assert logins == 1
    assert box_hits == 4


async def test_fa_frames_stay_out_of_retry_and_pairing_still_backs_off(hass, boxinfo):
    """FA is never retried, and a rejected pair is not attempted on the next poll."""
    from custom_components.sharp_cocoro_air.client import SharpAPIError
    from custom_components.sharp_cocoro_air.const import PAIR_BACKOFF_SECONDS

    assert "fa" not in RETRY_EPCS
    half = cmd_setpoint(22.5, half_degree=True, frame_end=80)
    assert half[0]["statusCode"] == "fa"
    assert _can_retry(half) is False
    assert _can_retry(_B3) is True
    assert _can_retry(_FA) is False

    entry = MockConfigEntry(
        domain=DOMAIN,
        data={"terminal_app_id": "https://example.invalid/terminal/1", "cookies": []},
    )
    entry.add_to_hass(hass)
    connector = aiohttp.TCPConnector(resolver=aiohttp.resolver.ThreadedResolver())
    session = aiohttp.ClientSession(connector=connector, cookie_jar=aiohttp.CookieJar(unsafe=True))
    client = SharpClient(session, "https://example.invalid/terminal/1")
    calls: list[str] = []

    async def reject(box_id: str) -> None:
        calls.append(box_id)
        raise SharpAPIError(400)

    client.pair = reject  # type: ignore[method-assign]
    client.box_info = _box_info(boxinfo)  # type: ignore[method-assign]
    coordinator = SharpCoordinator(hass, entry, client)
    try:
        await coordinator._async_update_data()
        first = len(calls)
        assert first == 3
        await coordinator._async_update_data()
        assert len(calls) == first
        client._pair_attempted = {
            box_id: time.monotonic() - PAIR_BACKOFF_SECONDS - 1 for box_id in set(calls)
        }
        await coordinator._async_update_data()
        assert len(calls) == first * 2
    finally:
        await client.aclose()
        await coordinator.async_shutdown()


async def _flush_temperature(hass, entity, temperature: float) -> None:
    task = hass.async_create_task(entity.async_set_temperature(**{ATTR_TEMPERATURE: temperature}))
    await asyncio.sleep(0)
    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=2))
    await hass.async_block_till_done()
    assert task.done()
    assert task.exception() is None


def _fa_byte(status: list[dict], index: int) -> int:
    return bytes.fromhex(status[0]["valueBinary"]["code"])[index]


async def test_homekit_tenths_snap_to_the_half_degree_step(hass, monkeypatch):
    """21.3 from 20 snaps to 21.5 and sends the whole degree first."""
    _install_clock(monkeypatch)
    cloud = _Cloud(_Clock())
    device = _device(setpoint=20.0)
    device.caps = Capabilities(half_degree=True)
    coordinator = _coordinator(hass, cloud, device)
    entity = await _climate(hass, coordinator, device)
    try:
        await _flush_temperature(hass, entity, 21.3)
        assert len(cloud.sent) == 2
        assert cloud.sent[0][1][0]["statusCode"] == "b3"
        assert cloud.sent[0][1][0]["valueRange"]["code"] == "21"
        assert cloud.sent[1][1][0]["statusCode"] == "fa"
        assert _fa_byte(cloud.sent[1][1], 26) == 43
    finally:
        await entity.async_remove()
        await coordinator.async_shutdown()


async def test_homekit_tenths_that_snap_to_the_current_value_are_not_sent(hass, monkeypatch):
    """21.2 on a half-degree unit snaps to 21, which is already the setpoint."""
    _install_clock(monkeypatch)
    cloud = _Cloud(_Clock())
    device = _device(setpoint=21.0)
    device.caps = Capabilities(half_degree=True)
    coordinator = _coordinator(hass, cloud, device)
    entity = await _climate(hass, coordinator, device)
    try:
        await entity.async_set_temperature(**{ATTR_TEMPERATURE: 21.2})
        assert cloud.sent == []
        assert entity.target_temperature == 21.0
    finally:
        await entity.async_remove()
        await coordinator.async_shutdown()


async def test_setpoint_returns_to_current_inside_the_debounce(hass, monkeypatch):
    """21→22→21 before the debounce ends stays at 21 and does not send 22."""
    _install_clock(monkeypatch)
    cloud = _Cloud(_Clock())
    device = _device(setpoint=21.0)
    coordinator = _coordinator(hass, cloud, device)
    entity = await _climate(hass, coordinator, device)
    first = hass.async_create_task(entity.async_set_temperature(**{ATTR_TEMPERATURE: 22}))
    try:
        await asyncio.sleep(0)
        assert entity.target_temperature == 22
        second = hass.async_create_task(entity.async_set_temperature(**{ATTR_TEMPERATURE: 21}))
        await asyncio.sleep(0)
        assert entity._pending_target == 21
        assert entity.target_temperature == 21
        async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=2))
        await hass.async_block_till_done()
        assert first.exception() is None
        assert second.exception() is None
        assert cloud.sent == []
        assert entity.target_temperature == 21.0
        state = hass.states.get(entity.entity_id)
        assert state is not None
        assert state.attributes["temperature"] == 21.0
    finally:
        if not first.done():
            first.cancel()
        await entity.async_remove()
        await coordinator.async_shutdown()


async def test_homekit_drag_back_to_the_start_sends_nothing(hass, monkeypatch):
    """21.3 then 21.2 on a unit at 21 snaps to 21.5, then back to 21, with no send."""
    _install_clock(monkeypatch)
    cloud = _Cloud(_Clock())
    device = _device(setpoint=21.0)
    device.caps = Capabilities(half_degree=True)
    coordinator = _coordinator(hass, cloud, device)
    entity = await _climate(hass, coordinator, device)
    first = hass.async_create_task(entity.async_set_temperature(**{ATTR_TEMPERATURE: 21.3}))
    try:
        await asyncio.sleep(0)
        assert entity._pending_target == 21.5
        second = hass.async_create_task(entity.async_set_temperature(**{ATTR_TEMPERATURE: 21.2}))
        await asyncio.sleep(0)
        assert entity._pending_target == 21.0
        async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=2))
        await hass.async_block_till_done()
        assert first.exception() is None
        assert second.exception() is None
        assert cloud.sent == []
        assert entity.target_temperature == 21.0
    finally:
        if not first.done():
            first.cancel()
        await entity.async_remove()
        await coordinator.async_shutdown()


async def test_homekit_tenths_on_a_whole_degree_unit_snap_before_sending(hass, monkeypatch):
    """A unit without half degrees snaps 21.4 to 21 (no send) and 21.6 to 22."""
    _install_clock(monkeypatch)
    cloud = _Cloud(_Clock())
    device = _device(setpoint=21.0)
    device.caps = Capabilities(half_degree=False)
    coordinator = _coordinator(hass, cloud, device)
    entity = await _climate(hass, coordinator, device)
    try:
        await entity.async_set_temperature(**{ATTR_TEMPERATURE: 21.4})
        assert cloud.sent == []
        await _flush_temperature(hass, entity, 21.6)
        assert len(cloud.sent) == 1
        assert cloud.sent[0][1][0]["statusCode"] == "b3"
        assert cloud.sent[0][1][0]["valueRange"]["code"] == "22"
    finally:
        await entity.async_remove()
        await coordinator.async_shutdown()


async def test_half_degree_across_a_whole_degree_sends_b3_then_fa(hass, monkeypatch):
    """21 to 22.5 sends the whole degree first, then FA byte 26."""
    _install_clock(monkeypatch)
    cloud = _Cloud(_Clock())
    device = _device(setpoint=21.0)
    device.caps = Capabilities(half_degree=True)
    coordinator = _coordinator(hass, cloud, device)
    entity = await _climate(hass, coordinator, device)
    try:
        await _flush_temperature(hass, entity, 22.5)
        assert len(cloud.sent) == 2
        assert cloud.sent[0][1][0]["statusCode"] == "b3"
        assert cloud.sent[0][1][0]["valueRange"]["code"] == "22"
        assert cloud.sent[1][1][0]["statusCode"] == "fa"
        assert _fa_byte(cloud.sent[1][1], 26) == 45
    finally:
        await entity.async_remove()
        await coordinator.async_shutdown()


async def test_half_degree_down_to_a_whole_degree_is_only_b3(hass, monkeypatch):
    """22.5 to 21 is a whole degree. The lagging FA byte is not sent."""
    _install_clock(monkeypatch)
    cloud = _Cloud(_Clock())
    device = _device(setpoint=22.5)
    device.caps = Capabilities(half_degree=True)
    coordinator = _coordinator(hass, cloud, device)
    entity = await _climate(hass, coordinator, device)
    try:
        await _flush_temperature(hass, entity, 21)
        assert len(cloud.sent) == 1
        assert cloud.sent[0][1][0]["statusCode"] == "b3"
        assert cloud.sent[0][1][0]["valueRange"]["code"] == "21"
    finally:
        await entity.async_remove()
        await coordinator.async_shutdown()


async def test_half_degree_down_across_a_whole_degree_sends_b3_then_fa(hass, monkeypatch):
    """22 to 21.5 sends B3 = 21, then FA byte 26 for 21.5."""
    _install_clock(monkeypatch)
    cloud = _Cloud(_Clock())
    device = _device(setpoint=22.0)
    device.caps = Capabilities(half_degree=True)
    coordinator = _coordinator(hass, cloud, device)
    entity = await _climate(hass, coordinator, device)
    try:
        await _flush_temperature(hass, entity, 21.5)
        assert len(cloud.sent) == 2
        assert cloud.sent[0][1][0]["valueRange"]["code"] == "21"
        assert _fa_byte(cloud.sent[1][1], 26) == 43
    finally:
        await entity.async_remove()
        await coordinator.async_shutdown()


async def test_rapid_on_an_unsupported_unit_is_rejected(hass, monkeypatch):
    """Fan patterns 0x02 and 0x03 hide Rapid. Requesting it is a service error."""
    _install_clock(monkeypatch)
    cloud = _Cloud(_Clock())
    device = _device()
    device.caps = Capabilities(rapid=False)
    coordinator = _coordinator(hass, cloud, device)
    entity = await _climate(hass, coordinator, device)
    try:
        with pytest.raises(ServiceValidationError):
            await entity.async_set_fan_mode("rapid")
        assert cloud.sent == []
    finally:
        await entity.async_remove()
        await coordinator.async_shutdown()


async def test_unsupported_vane_is_rejected_before_a_command(hass, monkeypatch):
    """A vane the capability bytes do not list is a service error, not a no-op."""
    _install_clock(monkeypatch)
    cloud = _Cloud(_Clock())
    device = _device()
    device.caps = Capabilities(
        vertical_vanes=("auto", "up", "level", "down", "swing"),
        horizontal_vanes=("straight", "left", "right", "swing"),
    )
    coordinator = _coordinator(hass, cloud, device)
    entity = await _climate(hass, coordinator, device)
    try:
        with pytest.raises(ServiceValidationError):
            await entity.async_set_swing_mode("down1")
        with pytest.raises(ServiceValidationError):
            await entity.async_set_swing_horizontal_mode("wide")
        assert cloud.sent == []
    finally:
        await entity.async_remove()
        await coordinator.async_shutdown()


async def test_vertical_auto_without_status_is_not_sent(hass, monkeypatch):
    """Vertical auto is byte 00. With no status and no horizontal reading, refuse it."""
    _install_clock(monkeypatch)
    cloud = _Cloud(_Clock())
    device = _device()
    device.vane_horizontal = None
    device.caps = Capabilities(vertical_vanes=("auto", "down", "swing"))
    coordinator = _coordinator(hass, cloud, device)
    entity = await _climate(hass, coordinator, device)
    try:
        with pytest.raises(HomeAssistantError, match="payload is empty"):
            await entity.async_set_swing_mode("auto")
        assert cloud.sent == []

        frame = bytearray(80)
        frame[26] = 0x2A
        device.fa_frame = bytes(frame)
        await entity.async_set_swing_mode("auto")
        assert len(cloud.sent) == 1
        raw = bytes.fromhex(cloud.sent[0][1][0]["valueBinary"]["code"])
        assert raw[48] == 0x00
        assert raw[26] == 0x2A
        assert set(raw[16:]) != {0}
    finally:
        await entity.async_remove()
        await coordinator.async_shutdown()


_ALL_VERTICAL = ("auto", "up", "level", "down1", "down2", "down", "swing")
_FOUR_HORIZONTAL = ("straight", "left", "right", "swing")
_VANE_BYTE = {
    "auto": 0x00,
    "up": 0x01,
    "level": 0x02,
    "down1": 0x03,
    "down2": 0x04,
    "down": 0x05,
    "swing": 0x07,
    "straight": 0x01,
    "left": 0x02,
    "right": 0x03,
}
_VANE_BYTE_SWING_H = 0x0F


def _vane_device(
    *,
    vertical: str = "up",
    horizontal: str = "straight",
    mode: str = "cool",
    pattern: int = 1,
) -> Aircon:
    """A unit with a real FA status frame and the app's vane lists."""
    frame = bytearray(80)
    frame[26] = 0x2A
    frame[48] = _VANE_BYTE[vertical]
    frame[49] = _VANE_BYTE_SWING_H if horizontal == "swing" else _VANE_BYTE[horizontal]
    device = _device()
    device.mode = mode
    device.vane_vertical = vertical
    device.vane_horizontal = horizontal
    device.fa_frame = bytes(frame)
    device.caps = Capabilities(
        vertical_pattern=pattern,
        vertical_vanes=_ALL_VERTICAL,
        horizontal_vanes=_FOUR_HORIZONTAL,
    )
    return device


async def _set_vane(entity: SharpClimate, kind: str, value: str) -> None:
    if kind == "vertical":
        await entity.async_set_swing_mode(value)
    else:
        await entity.async_set_swing_horizontal_mode(value)


def _vane_state(entity: SharpClimate, kind: str) -> str | None:
    if kind == "vertical":
        return entity.swing_mode
    return entity.swing_horizontal_mode


@pytest.mark.parametrize(
    ("kind", "current", "pick"),
    [("vertical", "up", "down"), ("horizontal", "straight", "left")],
)
async def test_vane_pick_shows_while_the_call_is_in_flight(hass, monkeypatch, kind, current, pick):
    """The card shows the pick before the cloud answers, and keeps it after."""
    clock = _install_clock(monkeypatch)
    cloud = _Cloud(clock)
    cloud.hold = asyncio.Event()
    device = _vane_device(**{kind: current})
    coordinator = _coordinator(hass, cloud, device)
    entity = await _climate(hass, coordinator, device)
    task = hass.async_create_task(_set_vane(entity, kind, pick))
    try:
        await asyncio.sleep(0)
        assert _vane_state(entity, kind) == pick
        state = hass.states.get(entity.entity_id)
        assert state is not None
        attribute = "swing_mode" if kind == "vertical" else "swing_horizontal_mode"
        assert state.attributes[attribute] == pick
        assert len(cloud.sent) == 1
        cloud.hold.set()
        await task
        assert _vane_state(entity, kind) == pick
        key = "vane_vertical" if kind == "vertical" else "vane_horizontal"
        assert key not in entity._pending_vanes
        assert entity._shown_vanes.get(key) == pick
        assert entity._pending_vanes_until > 0
    finally:
        if not task.done():
            cloud.hold.set()
            task.cancel()
        await entity.async_remove()
        await coordinator.async_shutdown()


@pytest.mark.parametrize("kind", ["vertical", "horizontal"])
async def test_unmatched_vane_reverts_and_arms_a_refresh(hass, monkeypatch, kind):
    """A read-back that still shows the old position snaps the card back."""
    clock = _install_clock(monkeypatch)
    cloud = _Cloud(clock)
    cloud.results = ["unmatch"]
    cloud.payload = _fa_payload(vertical=0x01, horizontal=0x01)
    device = _vane_device()
    coordinator = _coordinator(hass, cloud, device)
    entity = await _climate(hass, coordinator, device)
    pick = "down" if kind == "vertical" else "left"
    try:
        with pytest.raises(HomeAssistantError, match="couldn't be confirmed"):
            await _set_vane(entity, kind, pick)
        assert _vane_state(entity, kind) == ("up" if kind == "vertical" else "straight")
        assert coordinator._refresh_unsub is not None
        assert len(cloud.sent) == 1
        assert entity._pending_vanes == {}
    finally:
        await entity.async_remove()
        await coordinator.async_shutdown()


@pytest.mark.parametrize("kind", ["vertical", "horizontal"])
async def test_busy_twice_reverts_the_vane_and_arms_a_refresh(hass, monkeypatch, kind):
    """Two busy answers say the unit is busy, and the card returns to the old position."""
    clock = _install_clock(monkeypatch)
    cloud = _Cloud(clock)
    cloud.results = [SharpBusy("E1001001"), SharpBusy("E1001002")]
    device = _vane_device()
    coordinator = _coordinator(hass, cloud, device)
    entity = await _climate(hass, coordinator, device)
    pick = "down" if kind == "vertical" else "left"
    try:
        with pytest.raises(HomeAssistantError, match="The air conditioner is busy"):
            await _set_vane(entity, kind, pick)
        assert _vane_state(entity, kind) == ("up" if kind == "vertical" else "straight")
        assert coordinator._refresh_unsub is not None
        assert len(cloud.sent) == 2
        assert entity._pending_vanes == {}
    finally:
        await entity.async_remove()
        await coordinator.async_shutdown()


@pytest.mark.parametrize(
    ("kind", "picks", "index", "last_byte"),
    [
        ("vertical", ("down", "level", "swing"), 48, 0x07),
        ("horizontal", ("left", "right", "swing"), 49, 0x0F),
    ],
)
async def test_three_quick_vane_picks_send_at_most_twice(
    hass, monkeypatch, kind, picks, index, last_byte
):
    """Picks made while one send is in flight collapse. The last frame is the latest pick."""
    clock = _install_clock(monkeypatch)
    cloud = _Cloud(clock)
    cloud.hold = asyncio.Event()
    device = _vane_device()
    coordinator = _coordinator(hass, cloud, device)
    entity = await _climate(hass, coordinator, device)
    first = hass.async_create_task(_set_vane(entity, kind, picks[0]))
    try:
        await asyncio.sleep(0)
        assert len(cloud.sent) == 1
        await _set_vane(entity, kind, picks[1])
        await _set_vane(entity, kind, picks[2])
        assert len(cloud.sent) == 1
        assert _vane_state(entity, kind) == picks[2]
        cloud.hold.set()
        await first
        assert len(cloud.sent) <= 2
        assert len(cloud.sent) == 2
        first_raw = bytes.fromhex(cloud.sent[0][1][0]["valueBinary"]["code"])
        last_raw = bytes.fromhex(cloud.sent[1][1][0]["valueBinary"]["code"])
        assert first_raw[index] == _VANE_BYTE[picks[0]]
        assert last_raw[index] == last_byte
        other = 49 if index == 48 else 48
        assert last_raw[other] == 0x01
    finally:
        if not first.done():
            cloud.hold.set()
            first.cancel()
        await entity.async_remove()
        await coordinator.async_shutdown()


@pytest.mark.parametrize(
    ("kind", "current"),
    [("vertical", "up"), ("horizontal", "straight")],
)
async def test_picking_the_current_vane_sends_nothing(hass, monkeypatch, kind, current):
    """The unit is already there, so no deviceControl is posted."""
    clock = _install_clock(monkeypatch)
    cloud = _Cloud(clock)
    device = _vane_device()
    coordinator = _coordinator(hass, cloud, device)
    entity = await _climate(hass, coordinator, device)
    try:
        await _set_vane(entity, kind, current)
        assert cloud.sent == []
        assert _vane_state(entity, kind) == current
    finally:
        await entity.async_remove()
        await coordinator.async_shutdown()


async def test_down1_and_down2_copy_the_other_vane_from_fresh_data(hass, monkeypatch):
    """03 and 04 go to the vertical byte. The horizontal byte is the fresh status."""
    _install_clock(monkeypatch)
    cloud = _Cloud(_Clock())
    device = _vane_device(horizontal="right")
    coordinator = _coordinator(hass, cloud, device)
    entity = await _climate(hass, coordinator, device)
    try:
        await entity.async_set_swing_mode("down1")
        raw = bytes.fromhex(cloud.sent[0][1][0]["valueBinary"]["code"])
        assert raw[48] == 0x03
        assert raw[49] == 0x03

        current = coordinator.data[device.unique_key]
        frame = bytearray(current.fa_frame or b"")
        frame[49] = 0x02
        current.fa_frame = bytes(frame)
        current.vane_horizontal = "left"
        await entity.async_set_swing_mode("down2")
        raw = bytes.fromhex(cloud.sent[1][1][0]["valueBinary"]["code"])
        assert raw[48] == 0x04
        assert raw[49] == 0x02
    finally:
        await entity.async_remove()
        await coordinator.async_shutdown()


async def test_later_horizontal_pick_uses_fresh_vertical_status(hass, monkeypatch):
    """A vertical pick is not repeated after the app has changed that vane."""
    clock = _install_clock(monkeypatch)
    monkeypatch.setattr(climate_mod, "_monotonic", clock.monotonic)
    cloud = _Cloud(clock)
    device = _vane_device(vertical="up", horizontal="straight")
    coordinator = _coordinator(hass, cloud, device)
    entity = await _climate(hass, coordinator, device)
    try:
        await entity.async_set_swing_mode("down")
        first = bytes.fromhex(cloud.sent[0][1][0]["valueBinary"]["code"])
        assert first[48] == 0x05
        assert entity._pending_vanes == {}
        clock.now += PENDING_GRACE_S + 1
        current = coordinator.data[device.unique_key]
        frame = bytearray(current.fa_frame or b"")
        frame[48] = 0x01
        current.fa_frame = bytes(frame)
        current.vane_vertical = "up"
        await entity.async_set_swing_horizontal_mode("left")
        last = bytes.fromhex(cloud.sent[-1][1][0]["valueBinary"]["code"])
        assert last[48] == 0x01
        assert last[49] == 0x02
    finally:
        await entity.async_remove()
        await coordinator.async_shutdown()


async def test_app_vane_change_shows_inside_the_grace_window(hass, monkeypatch):
    """A status that differs from the pick replaces it before the 30 s grace ends."""
    clock = _install_clock(monkeypatch)
    monkeypatch.setattr(climate_mod, "_monotonic", clock.monotonic)
    cloud = _Cloud(clock)
    device = _vane_device(vertical="up")
    coordinator = _coordinator(hass, cloud, device)
    entity = await _climate(hass, coordinator, device)
    try:
        await entity.async_set_swing_mode("down")
        assert entity.swing_mode == "down"
        assert entity._pending_vanes == {}
        assert clock.now < entity._pending_vanes_until
        coordinator.data[device.unique_key].vane_vertical = "up"
        assert entity.swing_mode == "up"
    finally:
        await entity.async_remove()
        await coordinator.async_shutdown()


async def test_horizontal_positions_copy_the_vertical_byte_from_fresh_data(hass, monkeypatch):
    """Left and swing use bytes 02 and 0F. The vertical byte is read at send time."""
    _install_clock(monkeypatch)
    cloud = _Cloud(_Clock())
    device = _vane_device(vertical="up")
    coordinator = _coordinator(hass, cloud, device)
    entity = await _climate(hass, coordinator, device)
    try:
        await entity.async_set_swing_horizontal_mode("left")
        raw = bytes.fromhex(cloud.sent[0][1][0]["valueBinary"]["code"])
        assert raw[49] == 0x02
        assert raw[48] == 0x01

        current = coordinator.data[device.unique_key]
        frame = bytearray(current.fa_frame or b"")
        frame[48] = 0x07
        current.fa_frame = bytes(frame)
        current.vane_vertical = "swing"
        await entity.async_set_swing_horizontal_mode("swing")
        raw = bytes.fromhex(cloud.sent[1][1][0]["valueBinary"]["code"])
        assert raw[49] == 0x0F
        assert raw[48] == 0x07
    finally:
        await entity.async_remove()
        await coordinator.async_shutdown()


async def test_pattern_02_hides_fully_down_in_heat_and_auto(hass, monkeypatch):
    """a.java hides portrait_down only for pattern 02 while the mode is heat or auto."""
    _install_clock(monkeypatch)
    cloud = _Cloud(_Clock())
    device = _vane_device(mode="heat", pattern=2)
    coordinator = _coordinator(hass, cloud, device)
    entity = await _climate(hass, coordinator, device)
    try:
        assert entity.swing_modes == ["auto", "up", "level", "down1", "down2", "swing"]
        device.mode = "auto"
        assert entity.swing_modes is not None
        assert "down" not in entity.swing_modes
        with pytest.raises(ServiceValidationError):
            await entity.async_set_swing_mode("down")
        assert cloud.sent == []
        device.mode = "cool"
        assert entity.swing_modes is not None
        assert "down" in entity.swing_modes
        device.mode = "heat"
        device.caps = Capabilities(
            vertical_pattern=1,
            vertical_vanes=_ALL_VERTICAL,
            horizontal_vanes=_FOUR_HORIZONTAL,
        )
        assert entity.swing_modes is not None
        assert "down" in entity.swing_modes
    finally:
        await entity.async_remove()
        await coordinator.async_shutdown()


async def test_pending_vane_expires_after_thirty_seconds(hass, monkeypatch):
    """A matching pick shows during the grace period. After that, status shows."""
    device = _vane_device(vertical="swing", horizontal="left")
    coordinator = _coordinator(hass, _Cloud(_Clock()), device)
    entity = await _climate(hass, coordinator, device)
    entity._shown_vanes = {"vane_vertical": "swing", "vane_horizontal": "left"}
    entity._pending_vanes_until = 50.0
    try:
        monkeypatch.setattr(climate_mod, "_monotonic", lambda: 49.0)
        assert entity.swing_mode == "swing"
        assert entity.swing_horizontal_mode == "left"
        device.vane_vertical = None
        device.vane_horizontal = None
        assert entity.swing_mode == "swing"
        assert entity.swing_horizontal_mode == "left"
        monkeypatch.setattr(climate_mod, "_monotonic", lambda: 50.0)
        assert entity.swing_mode is None
        assert entity.swing_horizontal_mode is None
    finally:
        await entity.async_remove()
        await coordinator.async_shutdown()


async def test_refused_and_http_errors_name_the_cause_and_refresh(hass, monkeypatch):
    """A refusal shows its code and an HTTP error shows its status. Both refresh."""
    _install_clock(monkeypatch)
    cloud = _Cloud(_Clock())
    device = _vane_device()
    coordinator = _coordinator(hass, cloud, device)
    entity = await _climate(hass, coordinator, device)
    try:
        cloud.results = [SharpRefused("E1004001")]
        with pytest.raises(
            HomeAssistantError, match=r"Sharp refused the command \(code E1004001\)"
        ):
            await entity.async_set_swing_mode("down")
        assert entity.swing_mode == "up"
        assert coordinator._refresh_unsub is not None

        coordinator._cancel_refresh()
        cloud.results = [SharpAPIError(400)]
        with pytest.raises(HomeAssistantError, match=r"HTTP 400"):
            await entity.async_set_swing_horizontal_mode("left")
        assert entity.swing_horizontal_mode == "straight"
        assert coordinator._refresh_unsub is not None
    finally:
        await entity.async_remove()
        await coordinator.async_shutdown()


@pytest.mark.parametrize(
    ("failure", "exc"),
    [
        ("http", SharpAPIError(503)),
        ("busy", SharpBusy("E1001001")),
        ("refused", SharpRefused("E1004001")),
    ],
)
async def test_read_back_errors_say_the_change_was_not_confirmed(hass, monkeypatch, failure, exc):
    """An error while reading status back is not a busy, refused, or HTTP message."""
    clock = _install_clock(monkeypatch)
    cloud = _Cloud(clock)
    cloud.results = ["unmatch"]
    device = _device()
    device.vane_vertical = "up"
    coordinator = _coordinator(hass, cloud, device)
    status = cmd_vanes(vertical="down", horizontal=None, frame_end=80)

    async def fail_read() -> dict:
        cloud.box_reads += 1
        raise exc

    cloud.box_info = fail_read  # type: ignore[method-assign]
    try:
        with pytest.raises(HomeAssistantError, match="couldn't be confirmed") as caught:
            await coordinator.async_send(device.unique_key, status, vane_vertical="down")
        text = str(caught.value)
        assert "HTTP" not in text
        assert "busy" not in text.lower()
        assert "E100" not in text
        assert failure
        assert coordinator._refresh_unsub is not None
        assert coordinator.data[device.unique_key].vane_vertical == "up"
        assert len(cloud.sent) == 1
    finally:
        await coordinator.async_shutdown()


async def test_failed_vane_send_keeps_the_newer_pick(hass, monkeypatch):
    """A failed in-flight send does not drop picks whose calls already returned."""
    clock = _install_clock(monkeypatch)
    cloud = _Cloud(clock)
    cloud.hold = asyncio.Event()
    cloud.results = [SharpError("rejected"), "success"]
    device = _vane_device()
    coordinator = _coordinator(hass, cloud, device)
    entity = await _climate(hass, coordinator, device)
    first = hass.async_create_task(_set_vane(entity, "vertical", "down"))
    try:
        await asyncio.sleep(0)
        assert len(cloud.sent) == 1
        await _set_vane(entity, "vertical", "level")
        await _set_vane(entity, "vertical", "swing")
        assert entity.swing_mode == "swing"
        cloud.hold.set()
        await first
        assert len(cloud.sent) == 2
        first_raw = bytes.fromhex(cloud.sent[0][1][0]["valueBinary"]["code"])
        last_raw = bytes.fromhex(cloud.sent[1][1][0]["valueBinary"]["code"])
        assert first_raw[48] == 0x05
        assert last_raw[48] == 0x07
        assert entity.swing_mode == "swing"
    finally:
        if not first.done():
            cloud.hold.set()
            first.cancel()
        await entity.async_remove()
        await coordinator.async_shutdown()


async def test_pending_vane_does_not_expire_while_the_send_is_running(hass, monkeypatch):
    """The 30 s grace starts when the send finishes, not when the user picks."""
    clock = _install_clock(monkeypatch)
    monkeypatch.setattr(climate_mod, "_monotonic", clock.monotonic)
    cloud = _Cloud(clock)
    cloud.hold = asyncio.Event()
    device = _vane_device()
    coordinator = _coordinator(hass, cloud, device)
    entity = await _climate(hass, coordinator, device)
    task = hass.async_create_task(entity.async_set_swing_mode("down"))
    try:
        await asyncio.sleep(0)
        assert entity._vane_send_running is True
        assert entity._pending_vanes_until == 0.0
        assert entity.swing_mode == "down"
        clock.now += PENDING_GRACE_S + 5
        assert entity.swing_mode == "down"
        cloud.hold.set()
        await task
        assert entity._vane_send_running is False
        assert entity._pending_vanes == {}
        assert entity._shown_vanes.get("vane_vertical") == "down"
        assert entity._pending_vanes_until == clock.now + PENDING_GRACE_S
        assert entity.swing_mode == "down"
        coordinator.data[device.unique_key].vane_vertical = "up"
        assert entity.swing_mode == "up"
        clock.now = entity._pending_vanes_until
        assert entity.swing_mode == "up"
    finally:
        if not task.done():
            cloud.hold.set()
            task.cancel()
        await entity.async_remove()
        await coordinator.async_shutdown()


async def test_busy_retry_logs_debug_when_it_succeeds_and_warning_when_it_fails(
    hass, monkeypatch, caplog
):
    """A busy answer that is retried successfully stays at debug. The final failure is a warning."""
    clock = _install_clock(monkeypatch)
    cloud = _Cloud(clock)
    device = _device()
    coordinator = _coordinator(hass, cloud, device)
    caplog.set_level("DEBUG", logger="custom_components.sharp_cocoro_air.coordinator")
    try:
        cloud.results = [SharpBusy("E1001001"), "success"]
        await coordinator.async_send(device.unique_key, _B3, setpoint=22.0)
        logged = [
            (record.levelname, record.getMessage())
            for record in caplog.records
            if record.name == "custom_components.sharp_cocoro_air.coordinator"
        ]
        assert ("DEBUG", "deviceControl busy, errorCode=E1001001") in logged
        assert not any(level == "WARNING" for level, _message in logged)

        caplog.clear()
        cloud.results = [SharpBusy("E1001001"), SharpBusy("E1001002")]
        with pytest.raises(HomeAssistantError, match="busy"):
            await coordinator.async_send(device.unique_key, _B3, setpoint=23.0)
        warnings = [
            record.getMessage()
            for record in caplog.records
            if record.levelname == "WARNING"
            and record.name == "custom_components.sharp_cocoro_air.coordinator"
        ]
        assert warnings == ["deviceControl refused, errorCode=E1001002"]
    finally:
        await coordinator.async_shutdown()


def _box_info(payload: dict):
    async def _read() -> dict:
        return payload

    return _read

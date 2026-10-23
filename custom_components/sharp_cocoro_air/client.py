"""HTTP client for the Sharp Egypt HMS cloud.

The session is a cookie jar (JSESSIONID and the AWSALB cookies). A 401 retries
once by posting the terminal id alone. Query values are percent-encoded because
box ids and terminal ids are URLs.
"""

from __future__ import annotations

import asyncio
import json
import logging
import secrets
import string
import time
import urllib.parse
from dataclasses import dataclass
from http.cookies import SimpleCookie

import aiohttp
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.aiohttp_client import async_create_clientsession
from yarl import URL

from .const import APP_NAME, APP_SECRET, HMS_API, PAIR_BACKOFF_SECONDS, SERVICE_NAME, USER_AGENT
from .protocol import (
    Aircon,
    box_lists_terminal,
    boxes_missing_terminal,
    control_body,
    fa_payload_is_blank,
)

_NONCE_ALPHABET = string.ascii_letters + string.digits
_LOGGER = logging.getLogger(__name__)
_BUSY_CODES = {"E1001001", "E1001002"}


_EMPTY_PAYLOAD = (
    "Sharp would reject that command because its payload is empty. "
    "Wait for a status reading and try again."
)


def _fa_frame_is_blank(status: list[dict]) -> bool:
    """True when an FA payload, ignoring the checksum and change map, is all zeros."""
    for prop in status:
        if prop.get("valueType") != "valueBinary":
            continue
        code = str((prop.get("valueBinary") or {}).get("code") or "")
        if fa_payload_is_blank(code):
            return True
    return False


class SharpError(Exception):
    """Base error. The message never includes cookies, ids, or request URLs."""


class SharpAuthError(SharpError):
    """The session was rejected and terminal-only login did not recover it."""


class SharpTerminalRefused(SharpAuthError):
    """The stored terminal id itself was rejected.

    A later poll cannot repair this. The caller should start reauth.
    """

    def __init__(self) -> None:
        """Use a stable message. Callers match the type, not the text."""
        super().__init__("terminal login refused")


class SharpConnectionError(SharpError):
    """The cloud could not be reached."""


class SharpCommandRejected(SharpError):
    """deviceControl returned an id and controlResult was not success.

    Kept so older callers can still recognise a rejected result. New control
    flow returns the result word instead of raising this.
    """

    def __init__(self, result: str) -> None:
        """Remember the result word without putting it in the message."""
        super().__init__("The air conditioner rejected the command")
        self.result = result


class SharpBusy(SharpError):
    """The unit answered E1001001 or E1001002. One delayed retry is allowed."""

    def __init__(self, code: str) -> None:
        """Remember the busy code. It is not shown to the user."""
        super().__init__("The air conditioner is busy")
        self.code = code


class SharpAPIError(SharpError):
    """The cloud returned an HTTP error."""

    def __init__(self, status: int) -> None:
        """Remember the status code without echoing the response."""
        super().__init__("The Sharp cloud rejected the request")
        self.status = status


@dataclass(frozen=True)
class Account:
    """What the config entry needs after a successful sign-in."""

    user_id: str
    terminal_app_id: str
    cookies: list[dict[str, str]]


def async_get_clientsession(hass: HomeAssistant) -> aiohttp.ClientSession:
    """Home Assistant closes this session. The jar is not shared with other integrations."""
    return async_create_clientsession(
        hass,
        cookie_jar=aiohttp.CookieJar(unsafe=True),
    )


def app_nonce() -> str:
    """32-character login nonce. It is not stored."""
    return "".join(secrets.choice(_NONCE_ALPHABET) for _ in range(32))


def build_url(base: str, path: str, params: dict[str, str] | None = None) -> str:
    """Build an HMS URL. ``appSecret`` and every other value are encoded."""
    query = {"appSecret": APP_SECRET}
    for key, value in (params or {}).items():
        query[key] = value
    encoded = "&".join(f"{urllib_quote(key)}={urllib_quote(value)}" for key, value in query.items())
    return f"{base}{path}?{encoded}"


def urllib_quote(value: str) -> str:
    """Percent-encode a query value, including slashes and spaces."""
    return urllib.parse.quote(str(value), safe="")


class SharpClient:
    """One signed-in terminal."""

    def __init__(
        self,
        session: aiohttp.ClientSession,
        terminal_app_id: str | None = None,
        *,
        base_url: str = HMS_API,
    ) -> None:
        """Bind the client to a session and an API base URL."""
        self._session = session
        self.terminal_app_id = terminal_app_id
        self._user_id: str | None = None
        self._base_url = base_url if base_url.endswith("/") else f"{base_url}/"
        self.poll_interval = 1.0
        self.poll_attempts = 20
        self._pair_attempted: dict[str, float] = {}
        self._auth_lock = asyncio.Lock()
        self._auth_task: asyncio.Task[None] | None = None

    async def aclose(self) -> None:
        """Close a session this client owns. Do not use this for Home Assistant's session."""
        if not self._session.closed:
            await self._session.close()

    def release(self) -> None:
        """Drop a short-lived session without closing Home Assistant's shared connector."""
        if not self._session.closed:
            self._session.detach()

    def restore_cookies(self, cookies: list[dict]) -> None:
        """Load persisted cookies on the path and domain they were saved with.

        A name-only mapping would park JSESSIONID on ``/hems/pfApi/ta``. The
        cloud often sets the new session on ``/`` or ``/hems``, and aiohttp
        then keeps sending the more specific stale cookie.
        """
        jar: SimpleCookie = SimpleCookie()
        for cookie in cookies:
            name = cookie.get("name")
            value = cookie.get("value")
            if not name or value is None:
                continue
            jar[name] = str(value)
            morsel = jar[name]
            morsel["path"] = cookie.get("path") or "/"
            domain = cookie.get("domain")
            if domain:
                morsel["domain"] = domain
        if jar:
            self._session.cookie_jar.update_cookies(jar, response_url=URL(self._base_url))

    def export_cookies(self) -> list[dict[str, str]]:
        """One cookie per name, on the path the server actually set."""
        url = URL(self._base_url)
        host = url.host or ""
        # Later entries win. A Set-Cookie during this session replaces a restored one.
        chosen = {}
        for morsel in self._session.cookie_jar:
            chosen[morsel.key] = morsel
        return [
            {
                "name": name,
                "value": chosen[name].value,
                "domain": chosen[name]["domain"] or host,
                "path": chosen[name]["path"] or "/",
            }
            for name in sorted(chosen)
        ]

    def _drop_session(self) -> None:
        """Drop JSESSIONID and the load-balancer cookies before a login.

        Entries saved before the path was kept still hold a stale session on
        the API prefix. Clearing it lets the next Set-Cookie stand alone.
        """
        self._session.cookie_jar.clear(
            lambda morsel: morsel.key == "JSESSIONID" or morsel.key.startswith("AWSALB")
        )

    def _debug_http(self, status: int) -> None:
        """Log the status and cookie names and paths. Values stay out of the log."""
        cookies = ",".join(
            f"{morsel.key}:{morsel['path'] or '/'}" for morsel in self._session.cookie_jar
        )
        _LOGGER.debug("status=%s cookies=%s", status, cookies)

    async def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, str] | None = None,
        json_body: dict | None = None,
        allow_reauth: bool = True,
        ok: tuple[int, ...] = (200,),
    ) -> tuple[int, dict | list | None]:
        url = URL(build_url(self._base_url, path, params), encoded=True)
        # Home Assistant replaces the session's default User-Agent, so set it here.
        headers = {"User-Agent": USER_AGENT, "Accept": "application/json"}
        kwargs: dict = {"headers": headers, "timeout": aiohttp.ClientTimeout(total=30)}
        if json_body is not None:
            kwargs["json"] = json_body
            headers["Content-Type"] = "application/json; charset=utf-8"
        try:
            async with self._session.request(method, url, **kwargs) as response:
                status = response.status
                text = await response.text()
        except asyncio.CancelledError:
            raise
        except (aiohttp.ClientError, TimeoutError, OSError):
            raise SharpConnectionError("Could not reach the Sharp cloud") from None

        self._debug_http(status)
        if status == 401 and allow_reauth and self.terminal_app_id and path != "setting/login/":
            await self.login_with_terminal()
            try:
                return await self._request(
                    method,
                    path,
                    params=params,
                    json_body=json_body,
                    allow_reauth=False,
                    ok=ok,
                )
            except SharpAuthError:
                # The re-login returned 200 and the retry was still rejected.
                self._drop_session()
                await self.login_with_terminal()
                return await self._request(
                    method,
                    path,
                    params=params,
                    json_body=json_body,
                    allow_reauth=False,
                    ok=ok,
                )
        if status == 401:
            raise SharpAuthError("Sharp sign-in expired")
        if status not in ok:
            raise SharpAPIError(status)
        if not text.strip():
            return status, None
        try:
            return status, json.loads(text)
        except json.JSONDecodeError:
            raise SharpAPIError(status) from None

    async def fetch_terminal_app_id(self) -> str:
        """Ask the cloud for a new terminal id."""
        _status, payload = await self._request("GET", "setting/terminalAppId/", allow_reauth=False)
        terminal_id = (payload or {}).get("terminalAppId") if isinstance(payload, dict) else None
        if not terminal_id:
            raise SharpAuthError("Sharp sign-in expired")
        self.terminal_app_id = str(terminal_id)
        return self.terminal_app_id

    async def login_with_code(self, code: str, nonce: str | None = None) -> None:
        """Exchange a B2C code. There is no call to the B2C token endpoint."""
        self._drop_session()
        if not self.terminal_app_id:
            raise SharpAuthError("Sharp sign-in expired")
        await self._request(
            "POST",
            "setting/login/",
            params={"serviceName": SERVICE_NAME},
            json_body={
                "terminalAppId": self.terminal_app_id,
                "tempAccToken": code,
                "password": nonce or app_nonce(),
            },
            allow_reauth=False,
        )

    async def login_with_terminal(self) -> None:
        """Refresh the session. Concurrent callers share one in-flight login."""
        async with self._auth_lock:
            task = self._auth_task
            if task is None or task.done():
                task = asyncio.create_task(self._login_with_terminal())
                self._auth_task = task
        await task

    async def _login_with_terminal(self) -> None:
        """Post the terminal id. Caller holds the single-flight task, not the lock."""
        self._drop_session()
        if not self.terminal_app_id:
            raise SharpAuthError("Sharp sign-in expired")
        try:
            await self._request(
                "POST",
                "setting/login/",
                params={"serviceName": SERVICE_NAME},
                json_body={"terminalAppId": self.terminal_app_id},
                allow_reauth=False,
            )
        except SharpAuthError:
            raise SharpTerminalRefused() from None

    async def register_terminal(self, name: str = "Home Assistant") -> None:
        """Register this terminal so later logins can use the terminal id alone."""
        await self._request(
            "POST",
            "setting/terminal",
            json_body={
                "name": name,
                "os": "Android",
                "osVersion": "14",
                "pushId": "",
                "appName": APP_NAME,
                "permitTidLogin": 1,
                "expireSecTidLogin": 0,
            },
            ok=(200, 201, 204),
        )

    async def user_info(self) -> dict:
        """Account record. Used for the config entry unique id."""
        if not self.terminal_app_id:
            raise SharpAuthError("Sharp sign-in expired")
        _status, payload = await self._request(
            "GET",
            "setting/userInfo",
            params={"terminalAppId": self.terminal_app_id},
        )
        if not isinstance(payload, dict):
            raise SharpAuthError("Sharp sign-in expired")
        return payload

    async def box_info(self) -> dict:
        """All paired adapters, including the echonetProperty status blob."""
        _status, payload = await self._request("GET", "setting/boxInfo", params={"mode": "other"})
        if not isinstance(payload, dict):
            raise SharpAPIError(200)
        return payload

    async def pair(self, box_id: str) -> None:
        """Pair this terminal to one box. ``houseFlag`` must be false."""
        await self._request(
            "POST",
            "setting/pairing/",
            params={"boxId": box_id, "houseFlag": "false"},
            ok=(200, 201),
        )

    async def exchange_code(self, code: str) -> str:
        """New terminal and code exchange. Does not register or pair.

        The config flow checks the account id before either of those, so a
        second setup does not leave a terminal attached to the boxes.
        """
        await self.fetch_terminal_app_id()
        await self.login_with_code(code)
        return await self._load_user_id()

    async def finish_setup(self, *, register: bool = True) -> Account:
        """Register this terminal when it is new, and pair boxes that lack it."""
        if register:
            await self.register_terminal()
        await self.pair_missing()
        if not self.terminal_app_id or not self._user_id:
            raise SharpAuthError("Sharp sign-in expired")
        return Account(
            user_id=self._user_id,
            terminal_app_id=self.terminal_app_id,
            cookies=self.export_cookies(),
        )

    async def sign_in(self, code: str) -> Account:
        """First-time setup: new terminal, code exchange, register, pair."""
        await self.exchange_code(code)
        return await self.finish_setup()

    async def refresh_with_code(self, code: str) -> str:
        """Re-login and return the user id. Does not pair.

        The config flow compares that id with the existing entry before
        ``finish_setup`` pairs anything.
        """
        if not self.terminal_app_id:
            return await self.exchange_code(code)
        await self.login_with_code(code)
        return await self._load_user_id()

    async def _load_user_id(self) -> str:
        info = await self.user_info()
        user_id = str(info.get("userId") or "")
        if not user_id:
            raise SharpAuthError("Sharp sign-in expired")
        self._user_id = user_id
        return user_id

    async def pair_missing(self) -> None:
        """Pair this terminal only to boxes that do not already list it."""
        await self.pair_unlisted(await self.box_info())

    async def pair_unlisted(self, payload: dict) -> None:
        """Pair boxes that lack this terminal, at most once an hour each.

        A rejected or offline box is recorded immediately, so the 60-second
        poll does not write pairing again until the backoff has passed.
        """
        now = time.monotonic()
        for box_id in boxes_missing_terminal(payload, self.terminal_app_id):
            attempted = self._pair_attempted.get(box_id)
            if attempted is not None and now - attempted < PAIR_BACKOFF_SECONDS:
                continue
            self._pair_attempted[box_id] = now
            try:
                await self.pair(box_id)
            except SharpError:
                continue

    async def terminal_is_paired(self, box_id: str) -> bool:
        """Look at boxInfo before pairing again after a control 400."""
        if not self.terminal_app_id:
            return False
        try:
            payload = await self.box_info()
        except SharpAuthError:
            raise
        except SharpError:
            return False
        return box_lists_terminal(payload, box_id, self.terminal_app_id)

    async def send_control(self, device: Aircon, status: list[dict]) -> str:
        """Send a command and return success, unmatch, error, or timeout.

        A 400 pairs the box only when this terminal is absent. The log line
        has EPC codes and the result, never a device id or box id.
        An empty control is rejected here. The cloud answers that with E1004001.
        """
        if not status:
            raise ValueError("A control needs at least one property")
        if _fa_frame_is_blank(status):
            raise HomeAssistantError(_EMPTY_PAYLOAD)
        started = time.monotonic()
        body = control_body(device.device_id, device.echonet_node, device.echonet_object, status)
        try:
            control_id = await self._device_control(device.box_id, body)
        except SharpAPIError as err:
            if err.status != 400 or await self.terminal_is_paired(device.box_id):
                raise
            await self.pair(device.box_id)
            control_id = await self._device_control(device.box_id, body)
        result = await self._poll_result(device.box_id, control_id)
        elapsed = time.monotonic() - started
        _LOGGER.debug(
            "control epcs=%s result=%s after %.1fs",
            [item.get("statusCode") for item in status],
            result,
            elapsed,
        )
        return result

    async def _device_control(self, box_id: str, body: dict) -> str | int:
        if not self.terminal_app_id:
            raise SharpAuthError("Sharp sign-in expired")
        _status, payload = await self._request(
            "POST",
            "control/deviceControl",
            params={"terminalAppId": self.terminal_app_id, "boxId": box_id},
            json_body=body,
        )
        items = payload.get("controlList") if isinstance(payload, dict) else None
        if not items:
            raise SharpError("The air conditioner did not accept the command")
        item = items[0]
        error_code = item.get("errorCode")
        if error_code not in (None, "null", ""):
            code = str(error_code)
            _LOGGER.debug("deviceControl errorCode=%s", code)
            if code in _BUSY_CODES:
                raise SharpBusy(code)
            raise SharpError("The air conditioner rejected the command")
        if "id" not in item:
            raise SharpError("The air conditioner did not accept the command")
        return item["id"]

    async def _poll_result(self, box_id: str, control_id: str | int) -> str:
        """Wait until controlResult settles. Unmatch is not an exception."""
        for _attempt in range(self.poll_attempts):
            _status, payload = await self._request(
                "POST",
                "control/controlResult",
                params={"boxId": box_id},
                json_body={"resultList": [{"id": control_id}]},
            )
            items = payload.get("resultList") if isinstance(payload, dict) else None
            state = items[0].get("status") if items else None
            if state in (None, "wait", "exec"):
                await asyncio.sleep(self.poll_interval)
                continue
            if state == "success":
                return "success"
            if state == "unmatch":
                return "unmatch"
            if state == "timeout":
                return "timeout"
            return "error"
        return "timeout"

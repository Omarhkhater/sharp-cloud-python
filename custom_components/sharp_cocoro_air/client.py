"""HTTP client for the Sharp Egypt HMS cloud.

The session is a cookie jar (JSESSIONID and the AWSALB cookies). A 401 retries
once by posting the terminal id alone. Query values are percent-encoded because
box ids and terminal ids are URLs.
"""

from __future__ import annotations

import asyncio
import json
import secrets
import string
import urllib.parse
from dataclasses import dataclass

import aiohttp
from yarl import URL

from .const import APP_NAME, APP_SECRET, HMS_API, SERVICE_NAME, USER_AGENT
from .protocol import Aircon, control_body, split_box_info

_NONCE_ALPHABET = string.ascii_letters + string.digits


class SharpError(Exception):
    """Base error. The message never includes cookies, ids, or request URLs."""


class SharpAuthError(SharpError):
    """The session was rejected and terminal-only login did not recover it."""


class SharpConnectionError(SharpError):
    """The cloud could not be reached."""


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


def new_session() -> aiohttp.ClientSession:
    """Session with its own cookie jar. HA's shared session is not used."""
    connector = aiohttp.TCPConnector(resolver=aiohttp.resolver.ThreadedResolver())
    return aiohttp.ClientSession(
        connector=connector,
        cookie_jar=aiohttp.CookieJar(unsafe=True),
        headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
        timeout=aiohttp.ClientTimeout(total=30),
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
        self._base_url = base_url if base_url.endswith("/") else f"{base_url}/"
        self.poll_interval = 1.0
        self.poll_attempts = 20

    async def aclose(self) -> None:
        """Close the HTTP session."""
        if not self._session.closed:
            await self._session.close()

    def restore_cookies(self, cookies: list[dict]) -> None:
        """Load persisted cookies into the jar."""
        mapping = {
            cookie["name"]: cookie["value"]
            for cookie in cookies
            if cookie.get("name") and cookie.get("value") is not None
        }
        if mapping:
            self._session.cookie_jar.update_cookies(mapping, response_url=URL(self._base_url))

    def export_cookies(self) -> list[dict[str, str]]:
        """Cookies the next request to HMS would send."""
        url = URL(self._base_url)
        filtered = self._session.cookie_jar.filter_cookies(url)
        host = url.host or ""
        exported = [
            {
                "name": name,
                "value": morsel.value,
                "domain": morsel["domain"] or host,
                "path": morsel["path"] or "/",
            }
            for name, morsel in filtered.items()
        ]
        exported.sort(key=lambda item: item["name"])
        return exported

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
        kwargs: dict = {}
        if json_body is not None:
            kwargs["json"] = json_body
            kwargs["headers"] = {"Content-Type": "application/json; charset=utf-8"}
        try:
            async with self._session.request(method, url, **kwargs) as response:
                status = response.status
                text = await response.text()
        except asyncio.CancelledError:
            raise
        except (aiohttp.ClientError, TimeoutError, OSError):
            raise SharpConnectionError("Could not reach the Sharp cloud") from None

        if status == 401 and allow_reauth and self.terminal_app_id and path != "setting/login/":
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
        """Refresh the session with the terminal id and the current cookies."""
        if not self.terminal_app_id:
            raise SharpAuthError("Sharp sign-in expired")
        await self._request(
            "POST",
            "setting/login/",
            params={"serviceName": SERVICE_NAME},
            json_body={"terminalAppId": self.terminal_app_id},
            allow_reauth=False,
        )

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

    async def sign_in(self, code: str) -> Account:
        """First-time setup: new terminal, code exchange, register, pair."""
        await self.fetch_terminal_app_id()
        await self.login_with_code(code)
        await self.register_terminal()
        return await self._finish_account()

    async def refresh_with_code(self, code: str) -> Account:
        """Re-auth. Keeps the existing terminal so the boxes stay paired."""
        if not self.terminal_app_id:
            return await self.sign_in(code)
        await self.login_with_code(code)
        return await self._finish_account()

    async def _finish_account(self) -> Account:
        info = await self.user_info()
        user_id = str(info.get("userId") or "")
        if not user_id:
            raise SharpAuthError("Sharp sign-in expired")
        payload = await self.box_info()
        box_ids, _devices = split_box_info(payload)
        for box_id in box_ids:
            try:
                await self.pair(box_id)
            except SharpAPIError:
                # Already paired, or the box rejected it. Commands retry pairing.
                continue
        if not self.terminal_app_id:
            raise SharpAuthError("Sharp sign-in expired")
        return Account(
            user_id=user_id,
            terminal_app_id=self.terminal_app_id,
            cookies=self.export_cookies(),
        )

    async def send_control(self, device: Aircon, status: list[dict]) -> None:
        """Send a command, pairing once if the box has not accepted this terminal."""
        body = control_body(device.device_id, device.echonet_node, device.echonet_object, status)
        try:
            control_id = await self._device_control(device.box_id, body)
        except SharpAPIError as err:
            if err.status != 400:
                raise
            await self.pair(device.box_id)
            control_id = await self._device_control(device.box_id, body)
        await self._poll_result(device.box_id, control_id)

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
            raise SharpError("The air conditioner rejected the command")
        if "id" not in item:
            raise SharpError("The air conditioner did not accept the command")
        return item["id"]

    async def _poll_result(self, box_id: str, control_id: str | int) -> None:
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
                return
            raise SharpError("The air conditioner rejected the command")
        raise SharpError("The air conditioner did not confirm the command")

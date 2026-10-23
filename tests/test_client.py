"""Client behavior against a local fake cloud. No real account is contacted."""

from __future__ import annotations

import asyncio
import json
from unittest.mock import AsyncMock, patch

import aiohttp
import pytest
import pytest_socket
from aiohttp import web
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.sharp_cocoro_air import _async_reload_options
from custom_components.sharp_cocoro_air import client as client_mod
from custom_components.sharp_cocoro_air.client import (
    SharpAPIError,
    SharpAuthError,
    SharpBusy,
    SharpClient,
    SharpConnectionError,
    SharpError,
    SharpTerminalRefused,
    app_nonce,
)
from custom_components.sharp_cocoro_air.const import (
    APP_NAME,
    APP_SECRET,
    CONF_COOKIES,
    CONF_TERMINAL_APP_ID,
    DOMAIN,
)
from custom_components.sharp_cocoro_air.coordinator import SharpCoordinator
from custom_components.sharp_cocoro_air.protocol import Aircon, cmd_setpoint, cmd_vanes


class FakeCloud:
    """Record HMS calls and answer just enough of the protocol."""

    def __init__(self, boxinfo: dict) -> None:
        """Keep the fixture and a list of calls."""
        self.boxinfo = boxinfo
        self.calls: list[dict] = []
        self.control_attempts = 0
        self.result_attempts = 0
        self.box_attempts = 0
        self.fail_control_once = False
        self.fail_login_code = False
        self.unauthorized_once = False
        self.control_error: str | None = None
        self.result_script: list[str] | None = None

    async def handle(self, request: web.Request) -> web.StreamResponse:
        """Dispatch one HMS request."""
        body = await request.read()
        self.calls.append(
            {
                "method": request.method,
                "path": request.path,
                "query": request.query_string,
                "raw_path": request.raw_path,
                "params": dict(request.query),
                "body": body,
                "user_agent": request.headers.get("User-Agent", ""),
            }
        )
        path = request.path
        if path.endswith("/setting/terminalAppId/"):
            return web.json_response({"terminalAppId": "https://example.invalid/terminal/1"})
        if path.endswith("/setting/login/"):
            if self.fail_login_code:
                return web.Response(status=401, text="")
            response = web.json_response({"errorCode": None})
            response.set_cookie("JSESSIONID", "session-1", path="/")
            response.set_cookie("AWSALBAPP-0", "alb-1", path="/")
            return response
        if path.endswith("/setting/terminal") and request.method == "POST":
            return web.Response(status=200, text="")
        if path.endswith("/setting/userInfo"):
            return web.json_response({"userId": "user-1", "ownerFlag": True})
        if path.endswith("/setting/boxInfo"):
            self.box_attempts += 1
            if self.unauthorized_once and self.box_attempts == 1:
                return web.Response(status=401, text="")
            return web.json_response(self.boxinfo)
        if path.endswith("/setting/pairing/"):
            return web.json_response({"errorCode": None, "errorMessage": None}, status=201)
        if path.endswith("/control/deviceControl"):
            self.control_attempts += 1
            if self.fail_control_once and self.control_attempts == 1:
                return web.Response(status=400, text="")
            if self.control_error:
                return web.json_response({"controlList": [{"errorCode": self.control_error}]})
            return web.json_response({"controlList": [{"id": "cmd-1", "errorCode": None}]})
        if path.endswith("/control/controlResult"):
            self.result_attempts += 1
            if self.result_script:
                index = min(self.result_attempts - 1, len(self.result_script) - 1)
                state = self.result_script[index]
            else:
                state = "wait" if self.result_attempts == 1 else "success"
            return web.json_response(
                {"resultList": [{"id": "cmd-1", "status": state, "epc": "b3"}]}
            )
        return web.Response(
            status=500, text=json.dumps({"secret": "https://example.invalid/box/a b"})
        )


@pytest.fixture
async def cloud(boxinfo):
    """Start the fake cloud on localhost."""
    pytest_socket.enable_socket()
    fake = FakeCloud(boxinfo)
    app = web.Application()
    app.router.add_route("*", "/{tail:.*}", fake.handle)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    port = site._server.sockets[0].getsockname()[1]
    base = f"http://127.0.0.1:{port}/hems/pfApi/ta/"
    yield fake, base
    await runner.cleanup()


def _session() -> aiohttp.ClientSession:
    """A session this test owns. Production uses Home Assistant's session helper."""
    connector = aiohttp.TCPConnector(resolver=aiohttp.resolver.ThreadedResolver())
    return aiohttp.ClientSession(
        connector=connector,
        cookie_jar=aiohttp.CookieJar(unsafe=True),
    )


async def _client(base: str, terminal: str | None = None) -> SharpClient:
    client = SharpClient(_session(), terminal, base_url=base)
    client.poll_interval = 0
    return client


async def test_sign_in_registers_and_pairs_with_encoded_queries(cloud):
    """First login posts the code, registers the terminal, and pairs every box."""
    fake, base = cloud
    client = await _client(base)
    try:
        account = await client.sign_in("auth-code-value")
    finally:
        await client.aclose()

    paths = [call["path"].split("/ta/")[-1] for call in fake.calls]
    assert paths[:5] == [
        "setting/terminalAppId/",
        "setting/login/",
        "setting/userInfo",
        "setting/terminal",
        "setting/boxInfo",
    ]
    assert paths[5:] == ["setting/pairing/"] * 3

    login = fake.calls[1]
    login_body = json.loads(login["body"])
    assert login_body["terminalAppId"] == "https://example.invalid/terminal/1"
    assert login_body["tempAccToken"] == "auth-code-value"
    assert len(login_body["password"]) == 32
    assert login_body["password"].isalnum()
    assert set(login_body) == {"terminalAppId", "tempAccToken", "password"}
    assert login["params"]["serviceName"] == "sharp-egy"
    assert login["params"]["appSecret"] == APP_SECRET
    assert "%3D" in login["query"]
    assert login["user_agent"].startswith("smartlink_v200a_eg")

    registered = json.loads(fake.calls[3]["body"])
    assert registered["appName"] == APP_NAME
    assert registered["permitTidLogin"] == 1
    assert registered["expireSecTidLogin"] == 0

    pairing = fake.calls[5]
    assert pairing["body"] == b""
    assert pairing["params"]["houseFlag"] == "false"
    assert "terminalAppId" not in pairing["params"]

    assert account.user_id == "user-1"
    assert account.terminal_app_id == "https://example.invalid/terminal/1"
    names = {cookie["name"] for cookie in account.cookies}
    assert "JSESSIONID" in names
    assert "AWSALBAPP-0" in names


async def test_reauth_keeps_the_terminal_and_posts_only_the_code(cloud):
    """Re-auth does not mint a new terminal id."""
    fake, base = cloud
    client = await _client(base, "https://example.invalid/terminal/existing")
    try:
        user_id = await client.refresh_with_code("new-code")
        paths = [call["path"] for call in fake.calls]
        assert not any(path.rstrip("/").endswith("pairing") for path in paths)
        account = await client.finish_setup(register=False)
    finally:
        await client.aclose()

    paths = [call["path"] for call in fake.calls]
    assert not any(path.endswith("/setting/terminalAppId/") for path in paths)
    assert not any(path.endswith("/setting/terminal") for path in paths)
    body = json.loads(fake.calls[0]["body"])
    assert body["terminalAppId"] == "https://example.invalid/terminal/existing"
    assert body["tempAccToken"] == "new-code"
    assert user_id == "user-1"
    assert account.terminal_app_id == "https://example.invalid/terminal/existing"


async def test_rejected_code_does_not_retry_with_the_terminal(cloud):
    """A bad code is a 401 on the code exchange, not a terminal refresh."""
    fake, base = cloud
    fake.fail_login_code = True
    client = await _client(base, "https://example.invalid/terminal/1")
    try:
        with pytest.raises(SharpAuthError):
            await client.login_with_code("nope")
    finally:
        await client.aclose()
    assert len(fake.calls) == 1


async def test_unauthorized_status_retries_terminal_login(cloud):
    """A 401 posts the terminal id alone, then repeats the original call."""
    fake, base = cloud
    fake.unauthorized_once = True
    client = await _client(base, "https://example.invalid/terminal/1")
    client.restore_cookies(
        [{"name": "JSESSIONID", "value": "old", "domain": "127.0.0.1", "path": "/"}]
    )
    try:
        payload = await client.box_info()
    finally:
        await client.aclose()

    assert payload["devices"]
    login = json.loads(fake.calls[1]["body"])
    assert login == {"terminalAppId": "https://example.invalid/terminal/1"}
    assert [call["path"].endswith("/setting/boxInfo") for call in fake.calls] == [
        True,
        False,
        True,
    ]


async def test_control_encodes_ids_and_polls_until_success(cloud):
    """Setpoint control percent-encodes URL ids and waits out a wait status."""
    fake, base = cloud
    client = await _client(base, "https://example.invalid/terminal/a b")
    device = Aircon(
        box_id="https://example.invalid/box/a b",
        device_id=7,
        echonet_node="00-11-22-33-44-55",
        echonet_object="013001",
        name="Test",
        model="AY-XP12YHE",
    )
    try:
        await client.send_control(device, cmd_setpoint(22, half_degree=True, frame_end=80))
    finally:
        await client.aclose()

    control = next(call for call in fake.calls if call["path"].endswith("deviceControl"))
    assert "boxId=https%3A%2F%2Fexample.invalid%2Fbox%2Fa%20b" in control["raw_path"]
    assert "terminalAppId=https%3A%2F%2Fexample.invalid%2Fterminal%2Fa%20b" in control["raw_path"]
    assert control["params"]["appSecret"] == APP_SECRET
    sent = json.loads(control["body"])
    assert sent["controlList"][0]["status"][0]["statusCode"] == "b3"
    assert sent["controlList"][0]["status"][0]["valueRange"]["code"] == "22"
    assert fake.result_attempts == 2


async def test_unpaired_control_pairs_and_retries(cloud):
    """HTTP 400 on control pairs the box and sends the command again."""
    fake, base = cloud
    fake.fail_control_once = True
    client = await _client(base, "https://example.invalid/terminal/1")
    device = Aircon(
        box_id="box-1",
        device_id=7,
        echonet_node="00-11-22-33-44-55",
        echonet_object="013001",
        name="Test",
        model=None,
    )
    try:
        await client.send_control(device, cmd_setpoint(21, half_degree=False, frame_end=80))
    finally:
        await client.aclose()

    paths = [call["path"].rstrip("/").rsplit("/", 1)[-1] for call in fake.calls]
    assert paths == [
        "deviceControl",
        "boxInfo",
        "pairing",
        "deviceControl",
        "controlResult",
        "controlResult",
    ]
    assert fake.calls[2]["params"]["houseFlag"] == "false"
    assert fake.calls[2]["body"] == b""


async def test_http_errors_do_not_echo_identifiers(cloud):
    """Failures stay generic so logs do not pick up box ids."""
    _fake, base = cloud
    client = await _client(base, "https://example.invalid/terminal/1")
    try:
        with pytest.raises(SharpAPIError) as caught:
            await client._request(
                "GET", "missing", params={"boxId": "https://example.invalid/box/a b"}
            )
    finally:
        await client.aclose()
    message = str(caught.value)
    assert "example.invalid" not in message
    assert "secret" not in message

    closed = SharpClient(_session(), base_url="http://127.0.0.1:1/hems/pfApi/ta/")
    try:
        with pytest.raises(SharpConnectionError) as caught_conn:
            await closed.box_info()
    finally:
        await closed.aclose()
    assert "127.0.0.1" not in str(caught_conn.value)


async def test_sign_in_skips_boxes_that_already_list_the_terminal(cloud):
    """A terminal already in terminalAppInfo is not paired again."""
    fake, base = cloud
    terminal = "https://example.invalid/terminal/1"
    listed = {
        **fake.boxinfo["devices"][0],
        "terminalAppInfo": [{"terminalAppId": terminal, "appName": "spremote_a_eg:1:1.0.4"}],
    }
    fake.boxinfo = {"devices": [listed, fake.boxinfo["devices"][1], fake.boxinfo["devices"][2]]}
    client = await _client(base)
    try:
        await client.sign_in("auth-code-value")
    finally:
        await client.aclose()

    paired = [
        call["params"]["boxId"]
        for call in fake.calls
        if call["path"].rstrip("/").endswith("pairing")
    ]
    assert paired == ["<BOX_ID_1>", "<BOX_ID_2>"]


async def test_control_400_does_not_pair_when_the_terminal_is_listed(cloud):
    """A 400 is a pairing retry only when this terminal is absent from the box."""
    fake, base = cloud
    fake.fail_control_once = True
    terminal = "https://example.invalid/terminal/1"
    fake.boxinfo = {
        "box": [
            {
                "boxId": "box-1",
                "terminalAppInfo": [{"terminalAppId": terminal}],
                "echonetData": [],
            }
        ]
    }
    client = await _client(base, terminal)
    device = Aircon(
        box_id="box-1",
        device_id=7,
        echonet_node="00-11-22-33-44-55",
        echonet_object="013001",
        name="Test",
        model=None,
    )
    try:
        with pytest.raises(SharpAPIError):
            await client.send_control(device, cmd_setpoint(21, half_degree=False, frame_end=80))
    finally:
        await client.aclose()

    paths = [call["path"].rstrip("/").rsplit("/", 1)[-1] for call in fake.calls]
    assert paths == ["deviceControl", "boxInfo"]
    assert not any(path == "pairing" for path in paths)


async def test_unmatch_is_a_result_and_busy_codes_are_distinct(cloud, caplog):
    """unmatch is returned. E1001001 is busy. Any other errorCode is a hard error."""
    fake, base = cloud
    fake.result_script = ["unmatch"]
    client = await _client(base, "https://example.invalid/terminal/1")
    device = Aircon(
        box_id="box-1",
        device_id=7,
        echonet_node="00-11-22-33-44-55",
        echonet_object="013001",
        name="Test",
        model=None,
    )
    caplog.set_level("DEBUG", logger="custom_components.sharp_cocoro_air.client")
    try:
        result = await client.send_control(
            device, cmd_setpoint(22, half_degree=False, frame_end=80)
        )
        logged = " ".join(
            record.getMessage()
            for record in caplog.records
            if record.name == "custom_components.sharp_cocoro_air.client"
        )
        assert result == "unmatch"
        assert "result=unmatch" in logged
        assert "box-1" not in logged
        assert "cmd-1" not in logged

        fake.control_error = "E1001001"
        with pytest.raises(SharpBusy) as busy:
            await client.send_control(device, cmd_setpoint(22, half_degree=False, frame_end=80))
        assert busy.value.code == "E1001001"

        fake.control_error = "E1001999"
        with pytest.raises(SharpError) as rejected:
            await client.send_control(device, cmd_setpoint(22, half_degree=False, frame_end=80))
        assert not isinstance(rejected.value, SharpBusy)
    finally:
        await client.aclose()


async def test_empty_control_is_rejected_before_the_cloud():
    """An empty control never reaches the cloud. That request returns E1004001."""
    client = SharpClient(_session(), "https://example.invalid/terminal/1")
    device = Aircon(
        box_id="box-1",
        device_id=1,
        echonet_node="00-11-22-33-44-55",
        echonet_object="013001",
        name="Test",
        model=None,
    )
    try:
        with pytest.raises(ValueError, match="at least one property"):
            await client.send_control(device, [])
        blank = cmd_vanes(vertical=None, horizontal=None, frame_end=80)
        with pytest.raises(ValueError, match="empty FA"):
            await client.send_control(device, blank)
    finally:
        await client.aclose()


async def test_restore_keeps_the_saved_path_and_domain():
    """A saved cookie is not moved onto the API prefix."""
    client = SharpClient(
        _session(),
        base_url="https://eg-hms.cloudlabs.sharp.co.jp/hems/pfApi/ta/",
    )
    try:
        client.restore_cookies(
            [
                {
                    "name": "JSESSIONID",
                    "value": "kept-session",
                    "domain": "eg-hms.cloudlabs.sharp.co.jp",
                    "path": "/hems",
                },
                {
                    "name": "AWSALB",
                    "value": "kept-lb",
                    "domain": "eg-hms.cloudlabs.sharp.co.jp",
                    "path": "/",
                },
            ]
        )
        assert client.export_cookies() == [
            {
                "name": "AWSALB",
                "value": "kept-lb",
                "domain": "eg-hms.cloudlabs.sharp.co.jp",
                "path": "/",
            },
            {
                "name": "JSESSIONID",
                "value": "kept-session",
                "domain": "eg-hms.cloudlabs.sharp.co.jp",
                "path": "/hems",
            },
        ]
    finally:
        await client.aclose()


@pytest.mark.parametrize("server_path", ["/", "/hems", "/hems/pfApi/ta"])
async def test_expired_cookie_is_replaced_after_one_login(hass, server_path, caplog):
    """A stale session on the API path does not survive the terminal re-login.

    The new JSESSIONID is stored with the path the server set, and the config
    entry is updated without reloading.
    """
    pytest_socket.enable_socket()
    caplog.set_level("DEBUG", logger="custom_components.sharp_cocoro_air.client")
    logins: list[str] = []

    async def handle(request: web.Request) -> web.StreamResponse:
        if request.path.endswith("/setting/login/"):
            logins.append(request.headers.get("Cookie", ""))
            response = web.json_response({})
            response.set_cookie("JSESSIONID", "fresh-session", path=server_path)
            response.set_cookie("AWSALB", "fresh-lb", path=server_path)
            return response
        if request.path.endswith("/setting/boxInfo"):
            if request.cookies.get("JSESSIONID") != "fresh-session":
                return web.Response(status=401, text="")
            return web.json_response({"devices": []})
        return web.Response(status=500, text="")

    runner, base = await _serve(handle)
    session = _session()
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={
            CONF_TERMINAL_APP_ID: "https://example.invalid/terminal/1",
            CONF_COOKIES: [
                {
                    "name": "JSESSIONID",
                    "value": "expired-session",
                    "domain": "127.0.0.1",
                    "path": "/hems/pfApi/ta",
                }
            ],
        },
    )
    entry.add_to_hass(hass)
    client = SharpClient(session, entry.data[CONF_TERMINAL_APP_ID], base_url=base)
    client.restore_cookies(entry.data[CONF_COOKIES])
    coordinator = SharpCoordinator(hass, entry, client)
    entry.runtime_data = coordinator
    entry.add_update_listener(_async_reload_options)
    try:
        with patch.object(hass.config_entries, "async_reload", new=AsyncMock()) as reload:
            await coordinator._async_update_data()
            await hass.async_block_till_done()
        reload.assert_not_called()
    finally:
        await coordinator.async_shutdown()
        await client.aclose()
        await runner.cleanup()

    assert logins == [""]
    saved = next(cookie for cookie in entry.data[CONF_COOKIES] if cookie["name"] == "JSESSIONID")
    assert saved["value"] == "fresh-session"
    assert saved["path"] == server_path
    assert saved["domain"] == "127.0.0.1"
    logged = " ".join(
        record.getMessage()
        for record in caplog.records
        if record.name == "custom_components.sharp_cocoro_air.client"
    )
    assert "expired-session" not in logged
    assert "fresh-session" not in logged
    assert "fresh-lb" not in logged
    assert "status=401" in logged
    assert "status=200" in logged
    assert f"JSESSIONID:{server_path}" in logged


async def test_retry_still_unauthorized_logs_in_once_more():
    """A 401 after a successful re-login drops the session and tries once more."""
    pytest_socket.enable_socket()
    logins = 0

    async def handle(request: web.Request) -> web.StreamResponse:
        nonlocal logins
        if request.path.endswith("/setting/login/"):
            logins += 1
            response = web.json_response({})
            response.set_cookie("JSESSIONID", "fresh-session", path="/")
            return response
        return web.Response(status=401, text="")

    runner, base = await _serve(handle)
    client = await _client(base, "https://example.invalid/terminal/1")
    try:
        with pytest.raises(SharpAuthError, match="Sharp sign-in expired") as caught:
            await client.box_info()
        assert str(caught.value) != "terminal login refused"
        assert logins == 2
    finally:
        await client.aclose()
        await runner.cleanup()


async def test_refused_terminal_login_is_not_retried():
    """A 401 from the terminal re-login itself is terminal login refused."""
    pytest_socket.enable_socket()
    logins = 0
    box_hits = 0

    async def handle(request: web.Request) -> web.StreamResponse:
        nonlocal logins, box_hits
        if request.path.endswith("/setting/login/"):
            logins += 1
            return web.Response(status=401, text="")
        box_hits += 1
        return web.Response(status=401, text="")

    runner, base = await _serve(handle)
    client = await _client(base, "https://example.invalid/terminal/1")
    try:
        with pytest.raises(SharpTerminalRefused, match="terminal login refused"):
            await client.box_info()
        assert logins == 1
        assert box_hits == 1
    finally:
        await client.aclose()
        await runner.cleanup()


async def test_relogin_http_error_is_not_an_auth_failure():
    """A 500 from the terminal re-login is an API error, not a refused login."""
    pytest_socket.enable_socket()
    logins = 0

    async def handle(request: web.Request) -> web.StreamResponse:
        nonlocal logins
        if request.path.endswith("/setting/login/"):
            logins += 1
            return web.Response(status=500, text="")
        return web.Response(status=401, text="")

    runner, base = await _serve(handle)
    client = await _client(base, "https://example.invalid/terminal/1")
    try:
        with pytest.raises(SharpAPIError) as caught:
            await client.box_info()
        assert caught.value.status == 500
        assert logins == 1
    finally:
        await client.aclose()
        await runner.cleanup()


async def test_relogin_timeout_is_a_connection_error(monkeypatch):
    """A terminal re-login that never answers is a connection error."""
    pytest_socket.enable_socket()
    real_timeout = client_mod.aiohttp.ClientTimeout

    def fast_timeout(**kwargs):
        return real_timeout(total=0.2)

    monkeypatch.setattr(client_mod.aiohttp, "ClientTimeout", fast_timeout)

    async def handle(request: web.Request) -> web.StreamResponse:
        if request.path.endswith("/setting/login/"):
            await asyncio.sleep(2)
            return web.json_response({})
        return web.Response(status=401, text="")

    runner, base = await _serve(handle)
    client = await _client(base, "https://example.invalid/terminal/1")
    try:
        with pytest.raises(SharpConnectionError):
            await client.box_info()
    finally:
        await client.aclose()
        await runner.cleanup()


async def _serve(handle) -> tuple[web.AppRunner, str]:
    app = web.Application()
    app.router.add_route("*", "/{tail:.*}", handle)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    port = site._server.sockets[0].getsockname()[1]
    return runner, f"http://127.0.0.1:{port}/hems/pfApi/ta/"


def test_nonce_is_32_alnum():
    """The login nonce matches the app's 32-character alphabet."""
    nonce = app_nonce()
    assert len(nonce) == 32
    assert nonce.isalnum()

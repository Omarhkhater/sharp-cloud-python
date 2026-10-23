"""Config flow against a mocked cloud client."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

from homeassistant.config_entries import SOURCE_REAUTH
from homeassistant.data_entry_flow import FlowResultType
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.sharp_cocoro_air.client import Account, SharpAuthError, SharpConnectionError
from custom_components.sharp_cocoro_air.const import DOMAIN

ACCOUNT = Account(
    user_id="user-1",
    terminal_app_id="https://example.invalid/terminal/1",
    cookies=[
        {
            "name": "JSESSIONID",
            "value": "session-value",
            "domain": "eg-hms.cloudlabs.sharp.co.jp",
            "path": "/",
        }
    ],
)


def _field_names(schema) -> set[str]:
    names = set()
    for key in schema.schema:
        names.add(getattr(key, "schema", key))
    return names


async def test_user_flow_shows_the_authorize_url_and_stores_the_session(hass):
    """The form has the browser link and no password, then stores cookies."""
    with (
        patch(
            "custom_components.sharp_cocoro_air.config_flow.SharpClient.sign_in",
            new=AsyncMock(return_value=ACCOUNT),
        ) as sign_in,
        patch(
            "custom_components.sharp_cocoro_air.client.SharpClient.box_info",
            new=AsyncMock(return_value={"devices": []}),
        ),
    ):
        result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": "user"})
        assert result["type"] is FlowResultType.FORM
        assert "elaiotb2cprd.b2clogin.com" in result["description_placeholders"]["authorize_url"]
        assert "code_challenge" not in result["description_placeholders"]["authorize_url"]
        assert _field_names(result["data_schema"]) == {"redirect_url"}

        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {"redirect_url": "https://sharp-cocoroair-egypt/?code=pasted-code"},
        )

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "Sharp Air Control"
    assert result["data"]["terminal_app_id"] == ACCOUNT.terminal_app_id
    assert result["data"]["cookies"][0]["name"] == "JSESSIONID"
    assert result["data"]["user_id"] == "user-1"
    assert "password" not in result["data"]
    sign_in.assert_awaited_once()
    assert sign_in.await_args.args[0] == "pasted-code"


async def test_invalid_paste_and_rejected_code(hass):
    """A paste with no code, and a code the cloud rejects, stay on the form."""
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": "user"})
    bad = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"redirect_url": "not a code"}
    )
    assert bad["type"] is FlowResultType.FORM
    assert bad["errors"]["base"] == "invalid_code"

    with patch(
        "custom_components.sharp_cocoro_air.config_flow.SharpClient.sign_in",
        new=AsyncMock(side_effect=SharpAuthError("rejected")),
    ):
        rejected = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {"redirect_url": "https://sharp-cocoroair-egypt/?code=nope"},
        )
    assert rejected["errors"]["base"] == "invalid_auth"

    with patch(
        "custom_components.sharp_cocoro_air.config_flow.SharpClient.sign_in",
        new=AsyncMock(side_effect=SharpConnectionError("down")),
    ):
        offline = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {"redirect_url": "https://sharp-cocoroair-egypt/?code=nope"},
        )
    assert offline["errors"]["base"] == "cannot_connect"


async def test_reauth_updates_cookies_without_a_new_account(hass):
    """Re-auth keeps the entry and replaces the session cookies."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id="user-1",
        title="Sharp Air Control",
        data={
            "terminal_app_id": "https://example.invalid/terminal/1",
            "user_id": "user-1",
            "cookies": [
                {
                    "name": "JSESSIONID",
                    "value": "old",
                    "domain": "eg-hms.cloudlabs.sharp.co.jp",
                    "path": "/",
                }
            ],
        },
    )
    entry.add_to_hass(hass)
    refreshed = Account(
        user_id="user-1",
        terminal_app_id="https://example.invalid/terminal/1",
        cookies=[
            {
                "name": "JSESSIONID",
                "value": "new-session",
                "domain": "eg-hms.cloudlabs.sharp.co.jp",
                "path": "/",
            }
        ],
    )
    with (
        patch(
            "custom_components.sharp_cocoro_air.config_flow.SharpClient.refresh_with_code",
            new=AsyncMock(return_value=refreshed),
        ) as refresh,
        patch(
            "custom_components.sharp_cocoro_air.client.SharpClient.box_info",
            new=AsyncMock(return_value={"devices": []}),
        ),
    ):
        result = await hass.config_entries.flow.async_init(
            DOMAIN,
            context={
                "source": SOURCE_REAUTH,
                "entry_id": entry.entry_id,
                "unique_id": entry.unique_id,
            },
            data=dict(entry.data),
        )
        assert result["type"] is FlowResultType.FORM
        assert result["step_id"] == "reauth_confirm"
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {"redirect_url": "https://sharp-cocoroair-egypt/?code=fresh"},
        )

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reauth_successful"
    assert entry.data["cookies"][0]["value"] == "new-session"
    assert entry.data["terminal_app_id"] == "https://example.invalid/terminal/1"
    refresh.assert_awaited_once()
    assert refresh.await_args.args[0] == "fresh"

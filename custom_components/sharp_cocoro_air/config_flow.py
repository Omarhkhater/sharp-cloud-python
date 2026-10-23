"""Config flow. The password never enters Home Assistant.

The user signs in with Azure AD B2C in a browser, lands on an address the
browser cannot open, and pastes that address (or just the code) here.
"""

from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol
from homeassistant.config_entries import ConfigFlow
from homeassistant.data_entry_flow import FlowResult

from .client import (
    SharpAPIError,
    SharpAuthError,
    SharpClient,
    SharpConnectionError,
    new_session,
)
from .const import (
    CONF_COOKIES,
    CONF_REDIRECT_URL,
    CONF_TERMINAL_APP_ID,
    CONF_USER_ID,
    DOMAIN,
)
from .protocol import authorize_url, extract_auth_code

_LOGGER = logging.getLogger(__name__)

_SCHEMA = vol.Schema({vol.Required(CONF_REDIRECT_URL): str})


class SharpConfigFlow(ConfigFlow, domain=DOMAIN):
    """Set up Sharp Air Control, or sign in again when the session dies."""

    VERSION = 1

    def __init__(self) -> None:
        """No account data yet."""
        self._reauth_entry = None

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        """Ask for the browser redirect URL."""
        return await self._async_auth_step("user", user_input)

    async def async_step_reauth(self, entry_data: dict[str, Any]) -> FlowResult:
        """Start a new browser sign-in for an existing entry."""
        self._reauth_entry = self.hass.config_entries.async_get_entry(self.context["entry_id"])
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Collect a fresh code and keep the same terminal."""
        return await self._async_auth_step("reauth_confirm", user_input)

    async def _async_auth_step(self, step_id: str, user_input: dict[str, Any] | None) -> FlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            code = extract_auth_code(user_input[CONF_REDIRECT_URL])
            if not code:
                errors["base"] = "invalid_code"
            else:
                session = new_session()
                client = SharpClient(session)
                try:
                    if self._reauth_entry is not None:
                        client.terminal_app_id = self._reauth_entry.data.get(CONF_TERMINAL_APP_ID)
                        client.restore_cookies(self._reauth_entry.data.get(CONF_COOKIES) or [])
                        account = await client.refresh_with_code(code)
                    else:
                        account = await client.sign_in(code)
                except SharpAuthError:
                    errors["base"] = "invalid_auth"
                except (SharpConnectionError, SharpAPIError):
                    errors["base"] = "cannot_connect"
                except Exception:
                    _LOGGER.exception("Unexpected error during Sharp sign-in")
                    errors["base"] = "unknown"
                else:
                    return await self._async_finish(account)
                finally:
                    await client.aclose()

        return self.async_show_form(
            step_id=step_id,
            data_schema=_SCHEMA,
            description_placeholders={"authorize_url": authorize_url()},
            errors=errors,
        )

    async def _async_finish(self, account) -> FlowResult:
        if self._reauth_entry is not None:
            self.hass.config_entries.async_update_entry(
                self._reauth_entry,
                data={
                    CONF_TERMINAL_APP_ID: account.terminal_app_id,
                    CONF_COOKIES: account.cookies,
                    CONF_USER_ID: self._reauth_entry.data.get(CONF_USER_ID) or account.user_id,
                },
            )
            await self.hass.config_entries.async_reload(self._reauth_entry.entry_id)
            return self.async_abort(reason="reauth_successful")

        await self.async_set_unique_id(account.user_id)
        self._abort_if_unique_id_configured()
        return self.async_create_entry(
            title="Sharp Air Control",
            data={
                CONF_TERMINAL_APP_ID: account.terminal_app_id,
                CONF_COOKIES: account.cookies,
                CONF_USER_ID: account.user_id,
            },
        )

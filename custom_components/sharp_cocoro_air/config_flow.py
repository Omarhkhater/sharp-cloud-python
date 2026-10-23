"""Config flow. The password never enters Home Assistant.

The user signs in with Azure AD B2C in a browser, lands on an address the
browser cannot open, and pastes that address (or just the code) here.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from typing import Any

import voluptuous as vol
from homeassistant.config_entries import SOURCE_REAUTH, ConfigEntry, ConfigFlow, OptionsFlow
from homeassistant.data_entry_flow import AbortFlow, FlowResult

from .client import (
    Account,
    SharpAPIError,
    SharpAuthError,
    SharpClient,
    SharpConnectionError,
    async_get_clientsession,
)
from .const import (
    CONF_COOKIES,
    CONF_EXPERIMENTAL,
    CONF_REDIRECT_URL,
    CONF_TERMINAL_APP_ID,
    CONF_USER_ID,
    DOMAIN,
)
from .coordinator import clear_auth_failures
from .protocol import authorize_url, extract_auth_code

_LOGGER = logging.getLogger(__name__)

_SCHEMA = vol.Schema({vol.Required(CONF_REDIRECT_URL): str})


class SharpConfigFlow(ConfigFlow, domain=DOMAIN):
    """Set up Sharp Air Egypt, or sign in again when the session dies."""

    VERSION = 1

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        """Ask for the browser redirect URL."""
        return await self._async_auth_step(user_input)

    async def async_step_reauth(self, entry_data: Mapping[str, Any]) -> FlowResult:
        """Start a new browser sign-in for an existing entry."""
        clear_auth_failures(self.hass, self._get_reauth_entry().entry_id)
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Collect a fresh code and keep the same terminal."""
        return await self._async_auth_step(user_input)

    async def _async_auth_step(self, user_input: dict[str, Any] | None) -> FlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            code = extract_auth_code(user_input[CONF_REDIRECT_URL])
            if not code:
                errors["base"] = "invalid_code"
            else:
                client = SharpClient(async_get_clientsession(self.hass))
                try:
                    if self.source == SOURCE_REAUTH:
                        return await self._async_reauth(client, code)
                    account = await self._async_new_account(client, code)
                except AbortFlow:
                    raise
                except SharpAuthError:
                    errors["base"] = "invalid_auth"
                except (SharpConnectionError, SharpAPIError):
                    errors["base"] = "cannot_connect"
                except Exception:
                    _LOGGER.exception("Unexpected error during Sharp sign-in")
                    errors["base"] = "unknown"
                else:
                    return self.async_create_entry(
                        title="Sharp Air Egypt",
                        data=_entry_data(account),
                    )
                finally:
                    client.release()

        step_id = "reauth_confirm" if self.source == SOURCE_REAUTH else "user"
        return self.async_show_form(
            step_id=step_id,
            data_schema=_SCHEMA,
            description_placeholders={"authorize_url": authorize_url()},
            errors=errors,
        )

    async def _async_new_account(self, client: SharpClient, code: str) -> Account:
        """Exchange the code, and stop before pairing if this account exists."""
        user_id = await client.exchange_code(code)
        await self.async_set_unique_id(user_id)
        self._abort_if_unique_id_configured()
        return await client.finish_setup()

    async def _async_reauth(self, client: SharpClient, code: str) -> FlowResult:
        """Sign in again on the terminal this entry already registered."""
        entry = self._get_reauth_entry()
        # Fresh jar plus the stored terminal id and the new code. Saved cookies
        # can sit on the wrong path and would be sent ahead of the new session.
        client.terminal_app_id = entry.data.get(CONF_TERMINAL_APP_ID)
        user_id = await client.refresh_with_code(code)
        await self.async_set_unique_id(user_id)
        self._abort_if_unique_id_mismatch()
        account = await client.finish_setup(register=False)
        clear_auth_failures(self.hass, entry.entry_id)
        return self.async_update_reload_and_abort(entry, data_updates=_entry_data(account))

    @staticmethod
    def async_get_options_flow(config_entry: ConfigEntry) -> SharpOptionsFlow:
        """Soft and Cocoro Air stay off until the user enables them."""
        return SharpOptionsFlow()


class SharpOptionsFlow(OptionsFlow):
    """One toggle for Soft and Cocoro Air. The option key is unchanged."""

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        """Ask whether to show the controls that were not confirmed live."""
        if user_input is not None:
            return self.async_create_entry(title="", data=user_input)
        enabled = bool(self.config_entry.options.get(CONF_EXPERIMENTAL, False))
        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema({vol.Required(CONF_EXPERIMENTAL, default=enabled): bool}),
        )


def _entry_data(account: Account) -> dict[str, Any]:
    return {
        CONF_TERMINAL_APP_ID: account.terminal_app_id,
        CONF_COOKIES: account.cookies,
        CONF_USER_ID: account.user_id,
    }

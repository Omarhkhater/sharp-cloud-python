"""Config flow for Sharp COCORO AIR."""

from __future__ import annotations

import voluptuous as vol

from homeassistant import config_entries
from homeassistant.data_entry_flow import FlowResult

from .sharp_cloud import SharpCloud

from .const import (
    CONF_PASSWORD,
    CONF_REFRESH_INTERVAL,
    CONF_REGION,
    CONF_USERNAME,
    DEFAULT_REFRESH_INTERVAL,
    DEFAULT_REGION,
    DOMAIN,
)


class SharpConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow."""

    VERSION = 1

    async def async_step_user(
        self,
        user_input: dict | None = None,
    ) -> FlowResult:
        errors: dict[str, str] = {}

        if user_input is not None:
            try:
                await self.async_set_unique_id(
                    user_input[CONF_USERNAME].lower()
                )
                self._abort_if_unique_id_configured()

                client = SharpCloud()

                await self.hass.async_add_executor_job(
                    client.login,
                )

                return self.async_create_entry(
                    title=user_input[CONF_USERNAME],
                    data=user_input,
                )

            except Exception:
                errors["base"] = "cannot_connect"

        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_USERNAME): str,
                    vol.Required(CONF_PASSWORD): str,
                    vol.Required(
                        CONF_REGION,
                        default=DEFAULT_REGION,
                    ): str,
                    vol.Required(
                        CONF_REFRESH_INTERVAL,
                        default=DEFAULT_REFRESH_INTERVAL,
                    ): int,
                }
            ),
            errors=errors,
        )


class SharpOptionsFlow(config_entries.OptionsFlow):
    """Sharp options."""

    def __init__(self, config_entry):
        self.config_entry = config_entry

    async def async_step_init(
        self,
        user_input=None,
    ):
        if user_input is not None:
            return self.async_create_entry(
                title="",
                data=user_input,
            )

        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        CONF_REFRESH_INTERVAL,
                        default=self.config_entry.options.get(
                            CONF_REFRESH_INTERVAL,
                            DEFAULT_REFRESH_INTERVAL,
                        ),
                    ): int,
                }
            ),
        )


@staticmethod
def async_get_options_flow(
    config_entry,
):
    return SharpOptionsFlow(config_entry)
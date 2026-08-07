"""DataUpdateCoordinator for Sharp COCORO AIR."""

from __future__ import annotations

from datetime import timedelta
import logging

from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import (
    DataUpdateCoordinator,
    UpdateFailed,
)

from .sharp_cloud import SharpCloud

from .const import (
    DEFAULT_REFRESH_INTERVAL,
    DOMAIN,
)

_LOGGER = logging.getLogger(__name__)


class SharpCoordinator(DataUpdateCoordinator):
    """Coordinator for Sharp devices."""

    def __init__(
        self,
        hass: HomeAssistant,
        client: SharpCloud,
    ) -> None:
        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            update_interval=timedelta(
                seconds=DEFAULT_REFRESH_INTERVAL,
            ),
        )

        self.client = client

    async def _async_update_data(self):
        """Fetch latest data."""

        try:
            if not self.client.authenticated:
                return []

            pairing = await self.hass.async_add_executor_job(
                self.client.authentication.pairing,
            )

            devices = pairing.get("data", [])

            for device in devices:
                terminal_app_id = device.get("terminalAppId")

                properties = await self.hass.async_add_executor_job(
                    self.client.devices.properties,
                    terminal_app_id,
                )

                status = await self.hass.async_add_executor_job(
                    self.client.devices.status,
                    terminal_app_id,
                )

                device["properties"] = properties.get(
                    "data",
                    properties,
                )

                device["status"] = status.get(
                    "data",
                    status,
                )

            return devices

        except Exception as err:
            _LOGGER.exception(err)
            raise UpdateFailed(str(err)) from err
"""Setup dialog."""

from __future__ import annotations

from typing import Any

import voluptuous as vol
from homeassistant.config_entries import ConfigFlow, ConfigFlowResult
from homeassistant.helpers import config_validation as cv

from .const import (
    CONF_PORT,
    CONF_UPSTREAM,
    DEFAULT_PORT,
    DEFAULT_UPSTREAM,
    DOMAIN,
)

SCHEMA = vol.Schema(
    {
        vol.Required(CONF_PORT, default=DEFAULT_PORT): cv.port,
        vol.Required(CONF_UPSTREAM, default=DEFAULT_UPSTREAM): cv.url,
    }
)


class PumpspyLocalConfigFlow(ConfigFlow, domain=DOMAIN):
    """Ask for the port to listen on and where to forward."""

    VERSION = 1

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        # One listener, one port: a second entry could only fail to bind.
        if self._async_current_entries():
            return self.async_abort(reason="single_instance_allowed")

        if user_input is not None:
            return self.async_create_entry(title="pumpspy-local", data=user_input)

        return self.async_show_form(step_id="user", data_schema=SCHEMA)

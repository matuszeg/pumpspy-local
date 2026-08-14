"""Setup dialog."""

from __future__ import annotations

from typing import Any

import voluptuous as vol
from homeassistant.config_entries import ConfigFlow, ConfigFlowResult
from homeassistant.helpers import selector

from .const import (
    CONF_PORT,
    CONF_UPSTREAM,
    DEFAULT_PORT,
    DEFAULT_UPSTREAM,
    DOMAIN,
)

# Selectors rather than cv.port / cv.url on purpose. The frontend fetches this
# schema as JSON, and a bare validator function cannot be serialised: the dialog
# then fails with a 500 while a Python-level test of the same flow still passes.
SCHEMA = vol.Schema(
    {
        vol.Required(CONF_PORT, default=DEFAULT_PORT): selector.NumberSelector(
            selector.NumberSelectorConfig(
                min=1, max=65535, mode=selector.NumberSelectorMode.BOX
            )
        ),
        vol.Required(CONF_UPSTREAM, default=DEFAULT_UPSTREAM): selector.TextSelector(
            selector.TextSelectorConfig(type=selector.TextSelectorType.URL)
        ),
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
            # NumberSelector hands back a float; the port has to be an int.
            data = {**user_input, CONF_PORT: int(user_input[CONF_PORT])}
            return self.async_create_entry(title="pumpspy-local", data=data)

        return self.async_show_form(step_id="user", data_schema=SCHEMA)

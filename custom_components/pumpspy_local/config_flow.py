"""Setup dialog."""

from __future__ import annotations

from typing import Any

import voluptuous as vol
from homeassistant.config_entries import ConfigFlow, ConfigFlowResult
from homeassistant.helpers import selector

from .core.gallons import DEFAULT_FLOW_RATE
from .const import (
    CONF_CHECK_INTERVAL_HOURS,
    CONF_FIRMWARE_POLICY,
    CONF_FLOW_RATE,
    CONF_PORT,
    CONF_UPSTREAM,
    DEFAULT_CHECK_INTERVAL_HOURS,
    DEFAULT_FIRMWARE_POLICY,
    DEFAULT_PORT,
    DEFAULT_UPSTREAM,
    DOMAIN,
    POLICY_OBSERVE,
    POLICY_QUARANTINE,
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
        vol.Required(
            CONF_FLOW_RATE, default=DEFAULT_FLOW_RATE
        ): selector.NumberSelector(
            selector.NumberSelectorConfig(
                min=0.1, max=100, step=0.1, mode=selector.NumberSelectorMode.BOX
            )
        ),
        # Observe by default. Quarantine alters what the device receives, which
        # is the one place this project does that, so it is opt-in.
        vol.Required(
            CONF_FIRMWARE_POLICY, default=DEFAULT_FIRMWARE_POLICY
        ): selector.SelectSelector(
            selector.SelectSelectorConfig(
                options=[POLICY_OBSERVE, POLICY_QUARANTINE],
                translation_key=CONF_FIRMWARE_POLICY,
            )
        ),
        vol.Required(
            CONF_CHECK_INTERVAL_HOURS, default=DEFAULT_CHECK_INTERVAL_HOURS
        ): selector.NumberSelector(
            selector.NumberSelectorConfig(
                min=1, max=168, mode=selector.NumberSelectorMode.BOX
            )
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
            data = {
                **user_input,
                CONF_PORT: int(user_input[CONF_PORT]),
                CONF_CHECK_INTERVAL_HOURS: int(
                    user_input[CONF_CHECK_INTERVAL_HOURS]
                ),
            }
            return self.async_create_entry(title="pumpspy-local", data=data)

        return self.async_show_form(step_id="user", data_schema=SCHEMA)

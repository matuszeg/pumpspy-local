"""Setup dialog."""

from __future__ import annotations

import socket
from typing import Any

import voluptuous as vol
from homeassistant.config_entries import ConfigFlow, ConfigFlowResult
from homeassistant.helpers import selector

from .core.gallons import DEFAULT_FLOW_RATE
from .core.upstream import DEFAULT_NAMESERVER
from .const import (
    CONF_CHECK_INTERVAL_HOURS,
    CONF_FIRMWARE_POLICY,
    CONF_FLOW_RATE,
    CONF_NAMESERVER,
    CONF_PORT,
    CONF_UPSTREAM,
    CONF_UPSTREAM_IP,
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
        # How to find the vendor once the redirect is in place. The redirect
        # answers for this host too, so looking the name up the ordinary way
        # returns Home Assistant itself. A resolver that is not carrying the
        # redirect fixes that; an address pins it outright, for networks where
        # outbound DNS is blocked or the resolver cannot be trusted either.
        vol.Required(
            CONF_NAMESERVER, default=DEFAULT_NAMESERVER
        ): selector.TextSelector(),
        vol.Optional(CONF_UPSTREAM_IP, default=""): selector.TextSelector(),
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


def _port_is_free(port: int) -> bool:
    """Whether the listener could bind this port right now.

    Bound the way the listener binds it -- every interface, with the option
    asyncio sets for itself -- because a port that is free on loopback alone is
    not free for us. A bind on a numeric address is a single syscall and does
    no lookup, so this does not block the event loop.
    """
    with socket.socket() as probe:
        probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            probe.bind(("0.0.0.0", port))
        except OSError:
            return False
    return True


def _whole_numbers(user_input: dict[str, Any]) -> dict[str, Any]:
    """NumberSelector hands back a float; a port and an hour count are ints."""
    return {
        **user_input,
        CONF_PORT: int(user_input[CONF_PORT]),
        CONF_CHECK_INTERVAL_HOURS: int(user_input[CONF_CHECK_INTERVAL_HOURS]),
    }


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
            return self.async_create_entry(
                title="pumpspy-local", data=_whole_numbers(user_input)
            )

        return self.async_show_form(step_id="user", data_schema=SCHEMA)

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Edit the settings of the entry that already exists.

        One dialog for all of them rather than the conventional split between a
        reconfigure step for the connection and an options menu for the rest:
        every setting here needs a full reload to take effect, so the split
        would only make a stranger guess which of two menus holds the field
        they typed wrong.
        """
        entry = self._get_reconfigure_entry()

        errors: dict[str, str] = {}

        if user_input is not None:
            settings = _whole_numbers(user_input)
            # Only when it changed. Our own listener is holding the configured
            # port, so probing it unconditionally would report every port taken
            # and quietly make the whole dialog useless.
            moving = settings[CONF_PORT] != entry.data[CONF_PORT]
            if moving and not _port_is_free(settings[CONF_PORT]):
                errors[CONF_PORT] = "port_in_use"
            else:
                # Reload as well as save: every setting is read once, in
                # async_setup_entry, so without this the dialog would report
                # success while the listener carried on with the old values.
                return self.async_update_reload_and_abort(
                    entry, data_updates=settings
                )

        return self.async_show_form(
            step_id="reconfigure",
            # Fall back to what is configured only on the way in. Once the user
            # has typed, show what they typed, or a rejected port is silently
            # replaced by the old one and the error makes no sense.
            data_schema=self.add_suggested_values_to_schema(
                SCHEMA, user_input or entry.data
            ),
            errors=errors,
        )

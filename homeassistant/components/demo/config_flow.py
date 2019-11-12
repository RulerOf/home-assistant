"""Config flow to configure demo component."""

from homeassistant import config_entries
from . import DOMAIN


@config_entries.HANDLERS.register(DOMAIN)
class DemoConfigFlow(config_entries.ConfigFlow):
    """Demo configuration flow."""

    VERSION = 1

    async def async_step_import(self, import_info):
        """Set the config entry up from yaml."""
        return self.async_create_entry(title="Demo", data={})

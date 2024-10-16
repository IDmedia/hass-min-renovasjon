from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from .const import DOMAIN

async def async_setup_entry(hass, entry):
    # Use async_forward_entry_setups instead of async_setup_platforms
    await hass.config_entries.async_forward_entry_setups(entry, ["sensor"])
    return True
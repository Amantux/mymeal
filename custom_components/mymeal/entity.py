from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.helpers.device_registry import DeviceInfo

from .const import DOMAIN


def device_info(entry: ConfigEntry) -> DeviceInfo:
    """One device card grouping all myMeal entities from a config entry."""
    return DeviceInfo(
        identifiers={(DOMAIN, entry.entry_id)},
        name="myMeal",
        manufacturer="myMeal",
        model="Recipes & meal planning",
        configuration_url=f"{entry.data.get('host')}:{entry.data.get('port')}",
    )

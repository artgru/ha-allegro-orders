"""Allegro – zamówienia: lista zamówień sprzedażowych w Home Assistant."""

from __future__ import annotations

from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import AllegroAuth, AllegroClient
from .const import CONF_CLIENT_ID, CONF_CLIENT_SECRET, CONF_TOKEN, CONF_USER_AGENT
from .coordinator import AllegroOrdersCoordinator

PLATFORMS = [Platform.SENSOR]

type AllegroConfigEntry = ConfigEntry[AllegroOrdersCoordinator]


async def async_setup_entry(hass: HomeAssistant, entry: AllegroConfigEntry) -> bool:
    if not entry.data.get(CONF_TOKEN):
        raise ConfigEntryAuthFailed("Brak tokenu – zaloguj się do Allegro.")

    auth = AllegroAuth(
        async_get_clientsession(hass),
        entry.data[CONF_CLIENT_ID],
        entry.data[CONF_CLIENT_SECRET],
        entry.data[CONF_USER_AGENT],
    )

    def _save_token(token: dict[str, Any]) -> None:
        # Allegro rotuje refresh tokeny – nowy trzeba od razu zapisać
        hass.config_entries.async_update_entry(entry, data={**entry.data, CONF_TOKEN: token})

    client = AllegroClient(auth, entry.data[CONF_TOKEN], _save_token)
    coordinator = AllegroOrdersCoordinator(hass, entry, client)
    await coordinator.async_config_entry_first_refresh()

    coordinator._options_snapshot = dict(entry.options)  # noqa: SLF001
    entry.runtime_data = coordinator
    entry.async_on_unload(entry.add_update_listener(_options_updated))
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def _options_updated(hass: HomeAssistant, entry: AllegroConfigEntry) -> None:
    coordinator = entry.runtime_data
    # aktualizacja samego tokenu też wywołuje listener – przeładuj tylko gdy zmieniły się opcje
    if getattr(coordinator, "_options_snapshot", None) == dict(entry.options):
        return
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: AllegroConfigEntry) -> bool:
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)

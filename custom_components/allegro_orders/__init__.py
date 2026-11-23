"""Allegro – zamówienia: lista zamówień sprzedażowych w Home Assistant."""

from __future__ import annotations

from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
import voluptuous as vol

from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.exceptions import ConfigEntryAuthFailed, HomeAssistantError
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.typing import ConfigType

from .api import AllegroAuth, AllegroClient, AllegroError
from .const import (
    ATTR_ORDER_ID,
    ATTR_STATUS,
    CONF_CLIENT_ID,
    CONF_CLIENT_SECRET,
    CONF_TOKEN,
    CONF_USER_AGENT,
    DOMAIN,
    SERVICE_SET_STATUS,
    SETTABLE_STATUSES,
)
from .coordinator import AllegroOrdersCoordinator

PLATFORMS = [Platform.SENSOR, Platform.SELECT]

type AllegroConfigEntry = ConfigEntry[AllegroOrdersCoordinator]

CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)

SET_STATUS_SCHEMA = vol.Schema(
    {
        vol.Required(ATTR_ORDER_ID): cv.string,
        vol.Required(ATTR_STATUS): vol.All(cv.string, vol.Upper, vol.In(SETTABLE_STATUSES)),
    }
)


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    async def _set_status(call: ServiceCall) -> None:
        entries = [
            e for e in hass.config_entries.async_loaded_entries(DOMAIN)
        ]
        if not entries:
            raise HomeAssistantError("Integracja Allegro nie jest załadowana.")
        order_id = call.data[ATTR_ORDER_ID].strip()
        # zamówienie z listy którego konta? domyślnie pierwszego, które je zna
        coordinator = next(
            (e.runtime_data for e in entries if any(r["order_id"] == order_id for r in e.runtime_data.data or [])),
            entries[0].runtime_data,
        )
        await async_change_status(coordinator, order_id, call.data[ATTR_STATUS])

    hass.services.async_register(DOMAIN, SERVICE_SET_STATUS, _set_status, schema=SET_STATUS_SCHEMA)
    return True


async def async_change_status(coordinator: AllegroOrdersCoordinator, order_id: str, status: str) -> None:
    try:
        await coordinator.client.set_fulfillment(order_id, status)
    except AllegroError as err:
        raise HomeAssistantError(str(err)) from err
    await coordinator.async_request_refresh()


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

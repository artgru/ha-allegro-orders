"""Sensor z listą zamówień Allegro."""

from __future__ import annotations

from typing import Any

from homeassistant.components.sensor import SensorEntity, SensorStateClass
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from . import AllegroConfigEntry
from .const import DOMAIN
from .coordinator import AllegroOrdersCoordinator


async def async_setup_entry(
    hass: HomeAssistant,
    entry: AllegroConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    async_add_entities([AllegroOrdersSensor(entry.runtime_data, entry)])


class AllegroOrdersSensor(CoordinatorEntity[AllegroOrdersCoordinator], SensorEntity):
    """Stan = liczba zamówień, atrybut `orders` = lista pozycji."""

    _attr_icon = "mdi:package-variant-closed"
    _attr_native_unit_of_measurement = "zamówień"
    _attr_state_class = SensorStateClass.MEASUREMENT
    # lista bywa długa – nie zapisujemy jej w historii (recorder)
    _unrecorded_attributes = frozenset({"orders", "items"})

    def __init__(self, coordinator: AllegroOrdersCoordinator, entry: AllegroConfigEntry) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{entry.unique_id or entry.entry_id}_orders"
        # stała, przewidywalna nazwa → entity_id: sensor.allegro_zamowienia
        self._attr_name = "Allegro zamówienia"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.unique_id or entry.entry_id)},
            name=f"Allegro {entry.title}",
            manufacturer="Allegro",
            entry_type=DeviceEntryType.SERVICE,
            configuration_url="https://salescenter.allegro.com/orders",
        )

    @property
    def native_value(self) -> int:
        return len({r["order_id"] for r in self.coordinator.data or []})

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        rows = self.coordinator.data or []
        return {
            "orders": rows,
            "items": sum(r["quantity"] for r in rows),
            "total_value": round(sum(r["total"] or 0 for r in rows), 2),
            "currency": rows[0]["currency"] if rows else "PLN",
        }

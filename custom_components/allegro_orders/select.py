"""Wybór zamówienia i zmiana jego statusu z poziomu dashboardu."""

from __future__ import annotations

from homeassistant.components.select import SelectEntity
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from . import AllegroConfigEntry, async_change_status
from .const import DOMAIN, FULFILLMENT_LABELS, SETTABLE_STATUSES
from .coordinator import AllegroOrdersCoordinator

NONE_OPTION = "—"


def _device(entry: AllegroConfigEntry) -> DeviceInfo:
    return DeviceInfo(
        identifiers={(DOMAIN, entry.unique_id or entry.entry_id)},
        name=f"Allegro {entry.title}",
        manufacturer="Allegro",
        entry_type=DeviceEntryType.SERVICE,
    )


async def async_setup_entry(
    hass: HomeAssistant, entry: AllegroConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    coordinator = entry.runtime_data
    order_select = AllegroOrderSelect(coordinator, entry)
    async_add_entities([order_select, AllegroStatusSelect(coordinator, entry, order_select)])


def _orders(coordinator: AllegroOrdersCoordinator) -> dict[str, dict]:
    """Etykieta → dane zamówienia (jedna pozycja na zamówienie, produkty sklejone)."""
    out: dict[str, dict] = {}
    for r in coordinator.data or []:
        oid = r["order_id"]
        existing = next((v for v in out.values() if v["order_id"] == oid), None)
        if existing:
            existing["skus"].append(r["sku"])
            continue
        out[oid] = {"order_id": oid, "status": r["status"], "date": r["date_display"], "skus": [r["sku"]], "product": r["product"]}
    labels: dict[str, dict] = {}
    for o in out.values():
        label = f"{o['date']} · {', '.join(o['skus'])} · {o['product'][:40]}"
        while label in labels:  # identyczne etykiety – dopisz końcówkę numeru
            label += f" #{o['order_id'][:6]}"
        labels[label] = o
    return labels


class AllegroOrderSelect(CoordinatorEntity[AllegroOrdersCoordinator], SelectEntity):
    """Które zamówienie edytujemy."""

    _attr_icon = "mdi:format-list-bulleted"
    _attr_name = "Allegro wybrane zamówienie"

    def __init__(self, coordinator: AllegroOrdersCoordinator, entry: AllegroConfigEntry) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{entry.unique_id or entry.entry_id}_order_select"
        self._attr_device_info = _device(entry)
        self.selected_order_id: str | None = None

    @property
    def options(self) -> list[str]:
        return [NONE_OPTION, *_orders(self.coordinator)]

    @property
    def current_option(self) -> str:
        for label, o in _orders(self.coordinator).items():
            if o["order_id"] == self.selected_order_id:
                return label
        return NONE_OPTION

    @property
    def extra_state_attributes(self) -> dict:
        return {"order_id": self.selected_order_id}

    async def async_select_option(self, option: str) -> None:
        o = _orders(self.coordinator).get(option)
        self.selected_order_id = o["order_id"] if o else None
        self.async_write_ha_state()
        self.hass.bus.async_fire(f"{DOMAIN}_order_selected", {"entity_id": self.entity_id})

    def select_order_id(self, order_id: str | None) -> None:
        self.selected_order_id = order_id
        self.async_write_ha_state()


class AllegroStatusSelect(CoordinatorEntity[AllegroOrdersCoordinator], SelectEntity):
    """Status wybranego zamówienia – zmiana tutaj zmienia status w Allegro."""

    _attr_icon = "mdi:list-status"
    _attr_name = "Allegro status zamówienia"

    def __init__(
        self, coordinator: AllegroOrdersCoordinator, entry: AllegroConfigEntry, order_select: AllegroOrderSelect
    ) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{entry.unique_id or entry.entry_id}_status_select"
        self._attr_device_info = _device(entry)
        self._order_select = order_select
        self._attr_options = [FULFILLMENT_LABELS[s] for s in SETTABLE_STATUSES]

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()

        @callback
        def _on_select(event) -> None:
            if event.data.get("entity_id") == self._order_select.entity_id:
                self.async_write_ha_state()

        self.async_on_remove(self.hass.bus.async_listen(f"{DOMAIN}_order_selected", _on_select))

    def _current(self) -> str | None:
        oid = self._order_select.selected_order_id
        return next((r["status"] for r in self.coordinator.data or [] if r["order_id"] == oid), None)

    @property
    def available(self) -> bool:
        return super().available and self._current() is not None

    @property
    def current_option(self) -> str | None:
        st = self._current()
        return FULFILLMENT_LABELS.get(st) if st else None

    async def async_select_option(self, option: str) -> None:
        status = next(s for s in SETTABLE_STATUSES if FULFILLMENT_LABELS[s] == option)
        oid = self._order_select.selected_order_id
        if not oid:
            return
        await async_change_status(self.coordinator, oid, status)

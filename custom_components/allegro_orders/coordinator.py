"""Koordynator – cykliczne pobieranie zamówień."""

from __future__ import annotations

from datetime import timedelta
import logging
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from .api import AllegroAuthError, AllegroClient, AllegroError
from .const import (
    CONF_FULFILLMENT,
    CONF_MAX_ORDERS,
    CONF_ONLY_PAID,
    CONF_SCAN_MINUTES,
    DEFAULT_FULFILLMENT,
    DEFAULT_MAX_ORDERS,
    DEFAULT_ONLY_PAID,
    DEFAULT_SCAN_MINUTES,
    DOMAIN,
    FULFILLMENT_LABELS,
)

_LOGGER = logging.getLogger(__name__)


class AllegroOrdersCoordinator(DataUpdateCoordinator[list[dict[str, Any]]]):
    """Pobiera zamówienia i spłaszcza je do listy pozycji (produkt + SKU)."""

    config_entry: ConfigEntry

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry, client: AllegroClient) -> None:
        minutes = int(entry.options.get(CONF_SCAN_MINUTES, DEFAULT_SCAN_MINUTES))
        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name=DOMAIN,
            update_interval=timedelta(minutes=max(1, minutes)),
        )
        self.client = client

    async def _async_update_data(self) -> list[dict[str, Any]]:
        opts = self.config_entry.options
        try:
            forms = await self.client.get_orders(
                fulfillment=list(opts.get(CONF_FULFILLMENT, DEFAULT_FULFILLMENT)),
                only_paid=bool(opts.get(CONF_ONLY_PAID, DEFAULT_ONLY_PAID)),
                max_orders=int(opts.get(CONF_MAX_ORDERS, DEFAULT_MAX_ORDERS)),
            )
        except AllegroAuthError as err:
            raise ConfigEntryAuthFailed(str(err)) from err
        except AllegroError as err:
            raise UpdateFailed(str(err)) from err
        return flatten_orders(forms)


def _local(ts: str | None):
    if not ts:
        return None
    parsed = dt_util.parse_datetime(ts)
    return dt_util.as_local(parsed) if parsed else None


def flatten_orders(forms: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Jedna pozycja listy = jeden produkt w zamówieniu."""
    rows: list[dict[str, Any]] = []
    for form in forms:
        delivery = form.get("delivery") or {}
        method = (delivery.get("method") or {}).get("name") or "—"
        pickup = (delivery.get("pickupPoint") or {}).get("name")
        dispatch_to = _local(((delivery.get("time") or {}).get("dispatch") or {}).get("to"))
        status = (form.get("fulfillment") or {}).get("status") or ""
        buyer = (form.get("buyer") or {}).get("login") or ""
        for item in form.get("lineItems") or []:
            offer = item.get("offer") or {}
            sku = ((offer.get("external") or {}).get("id") or "").strip()
            bought = _local(item.get("boughtAt") or form.get("updatedAt"))
            rows.append(
                {
                    "order_id": form.get("id"),
                    "date": bought.isoformat() if bought else None,
                    "date_display": bought.strftime("%d.%m %H:%M") if bought else "",
                    "product": offer.get("name") or "",
                    "offer_id": offer.get("id"),
                    "sku": sku or "BRAK_SKU",
                    "quantity": int(item.get("quantity") or 1),
                    "shipping": method,
                    "pickup_point": pickup,
                    "smart": bool(delivery.get("smart")),
                    "dispatch_by": dispatch_to.strftime("%d.%m %H:%M") if dispatch_to else None,
                    "status": status,
                    "status_label": FULFILLMENT_LABELS.get(status, status),
                    "buyer": buyer,
                }
            )
    rows.sort(key=lambda r: r["date"] or "", reverse=True)
    return rows

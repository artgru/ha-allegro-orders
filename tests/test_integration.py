"""Testy end-to-end z zamockowanym Allegro API."""

from __future__ import annotations

import time
from unittest.mock import patch

import pytest
from homeassistant import config_entries
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.allegro_orders.const import DOMAIN

AUTH = "https://allegro.pl/auth/oauth"
API = "https://api.allegro.pl"

ORDER = {
    "id": "29b6a6e0-aaaa",
    "buyer": {"login": "kupujacy1"},
    "status": "READY_FOR_PROCESSING",
    "fulfillment": {"status": "NEW"},
    "delivery": {
        "method": {"id": "x", "name": "Allegro Paczkomaty InPost"},
        "pickupPoint": {"name": "TOR01M"},
        "smart": True,
        "time": {"dispatch": {"from": "2026-11-15T10:00:00Z", "to": "2026-11-16T14:00:00Z"}},
    },
    "lineItems": [
        {
            "offer": {"id": "18859203327", "name": "Vivanco 47022 adapter SCART", "external": {"id": "P1-0042"}},
            "quantity": 2,
            "boughtAt": "2026-11-15T13:20:00.000Z",
        },
        {
            "offer": {"id": "18859203328", "name": "Kabel bez SKU"},
            "quantity": 1,
            "boughtAt": "2026-11-15T13:20:00.000Z",
        },
    ],
    "updatedAt": "2026-11-15T13:25:00.000Z",
}

CREDS = {"client_id": "cid", "client_secret": "secret", "user_agent": "TPM/1.0 (+test)"}


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations):
    yield


@pytest.fixture(autouse=True)
def fast_sleep():
    async def _no_sleep(_):
        return None

    with patch("custom_components.allegro_orders.api.asyncio.sleep", _no_sleep):
        yield


async def test_full_flow_and_sensor(hass: HomeAssistant, aioclient_mock) -> None:
    import asyncio
    approved = asyncio.Event()

    async def _wait(_):
        await approved.wait()

    sleep_patch = patch("custom_components.allegro_orders.api.asyncio.sleep", _wait)
    sleep_patch.start()
    aioclient_mock.post(
        f"{AUTH}/device?client_id=cid",
        json={
            "device_code": "dev123",
            "user_code": "ABC-DEF",
            "verification_uri": "https://allegro.pl/skojarz-aplikacje",
            "verification_uri_complete": "https://allegro.pl/skojarz-aplikacje?code=ABCDEF",
            "expires_in": 3600,
            "interval": 5,
        },
    )
    aioclient_mock.post(
        f"{AUTH}/token",
        json={"access_token": "acc1", "refresh_token": "ref1", "expires_in": 43199},
    )
    aioclient_mock.get(f"{API}/me", json={"id": "123456", "login": "Outlet_Test"})
    aioclient_mock.get(f"{API}/order/checkout-forms", json={"checkoutForms": [ORDER], "count": 1})

    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": config_entries.SOURCE_USER})
    assert result["type"] is FlowResultType.FORM
    result = await hass.config_entries.flow.async_configure(result["flow_id"], CREDS)
    assert result["type"] is FlowResultType.SHOW_PROGRESS
    assert result["description_placeholders"]["code"] == "ABC-DEF"
    assert "code=ABCDEF" in result["description_placeholders"]["url"]
    approved.set()
    await hass.async_block_till_done()
    sleep_patch.stop()
    result = await hass.config_entries.flow.async_configure(result["flow_id"])
    assert result["type"] is FlowResultType.CREATE_ENTRY, result
    assert result["title"] == "Outlet_Test"
    await hass.async_block_till_done()

    # device_code poll poszedł z basic auth i właściwym grantem
    token_calls = [c for c in aioclient_mock.mock_calls if str(c[1]).endswith("/token")]
    assert "device_code=dev123" in token_calls[0][2]
    assert token_calls[0][3]["Authorization"].startswith("Basic ")

    state = hass.states.get("sensor.allegro_zamowienia")
    assert state is not None, hass.states.async_entity_ids()
    assert state.state == "1"
    orders = state.attributes["orders"]
    assert len(orders) == 2
    first = orders[0]
    assert first["sku"] == "P1-0042"
    assert first["product"] == "Vivanco 47022 adapter SCART"
    assert first["shipping"] == "Allegro Paczkomaty InPost"
    assert first["quantity"] == 2
    assert first["date_display"]  # lokalna data
    assert orders[1]["sku"] == "BRAK_SKU"
    assert state.attributes["items"] == 3

    # zapytanie o zamówienia: filtry + nagłówki
    order_call = [c for c in aioclient_mock.mock_calls if "checkout-forms" in str(c[1])][-1]
    q = str(order_call[1])
    assert "fulfillment.status=NEW" in q and "fulfillment.status=PROCESSING" in q
    assert "status=READY_FOR_PROCESSING" in q
    assert order_call[3]["User-Agent"] == "TPM/1.0 (+test)"
    assert order_call[3]["Authorization"] == "Bearer acc1"


async def test_refresh_and_reauth(hass: HomeAssistant, aioclient_mock) -> None:
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id="123456",
        title="Outlet_Test",
        data={**CREDS, "token": {"access_token": "old", "refresh_token": "r_old", "expires_at": time.time() - 10}},
    )
    entry.add_to_hass(hass)
    aioclient_mock.post(f"{AUTH}/token", json={"access_token": "new", "refresh_token": "r_new", "expires_in": 43199})
    aioclient_mock.get(f"{API}/order/checkout-forms", json={"checkoutForms": []})

    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.data["token"]["refresh_token"] == "r_new"  # rotacja zapisana
    assert hass.states.get("sensor.allegro_zamowienia").state == "0"
    assert entry.state is config_entries.ConfigEntryState.LOADED

    # refresh token unieważniony → reauth
    await hass.config_entries.async_unload(entry.entry_id)
    hass.config_entries.async_update_entry(
        entry, data={**entry.data, "token": {**entry.data["token"], "expires_at": 0}}
    )
    aioclient_mock.clear_requests()
    aioclient_mock.post(f"{AUTH}/token", status=400, json={"error": "invalid_grant"})
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.state is config_entries.ConfigEntryState.SETUP_ERROR
    flows = hass.config_entries.flow.async_progress()
    assert any(f["context"]["source"] == "reauth" for f in flows)


async def test_options(hass: HomeAssistant, aioclient_mock) -> None:
    entry = MockConfigEntry(
        domain=DOMAIN, unique_id="1", title="x",
        data={**CREDS, "token": {"access_token": "a", "refresh_token": "r", "expires_at": time.time() + 9999}},
    )
    entry.add_to_hass(hass)
    aioclient_mock.get(f"{API}/order/checkout-forms", json={"checkoutForms": [ORDER]})
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert result["type"] is FlowResultType.FORM
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {"fulfillment_statuses": ["SENT"], "only_paid": False, "max_orders": 10, "scan_interval_minutes": 15},
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    await hass.async_block_till_done()
    # ORDER ma status NEW → po zmianie filtra na SENT lista pusta (filtr lokalny też działa)
    assert hass.states.get("sensor.allegro_zamowienia").state == "0"
    q = str([c for c in aioclient_mock.mock_calls if "checkout-forms" in str(c[1])][-1][1])
    assert "fulfillment.status=SENT" in q and "READY_FOR_PROCESSING" not in q


async def test_card_template(hass: HomeAssistant, aioclient_mock) -> None:
    import yaml
    from pathlib import Path
    from homeassistant.helpers.template import Template

    entry = MockConfigEntry(
        domain=DOMAIN, unique_id="1", title="x",
        data={**CREDS, "token": {"access_token": "a", "refresh_token": "r", "expires_at": time.time() + 9999}},
    )
    entry.add_to_hass(hass)
    aioclient_mock.get(f"{API}/order/checkout-forms", json={"checkoutForms": [ORDER]})
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    card = yaml.safe_load((Path(__file__).parent.parent / "examples/karta_zamowienia.yaml").read_text())
    out = Template(card["content"], hass).async_render(parse_result=False)
    print(out)
    assert "| **P1-0042** |" in out and "**×2**" in out and "**BRAK_SKU**" in out

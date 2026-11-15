"""Config flow: dane aplikacji Allegro → logowanie Device Flow → wpis."""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
import logging
from typing import Any

import voluptuous as vol

from homeassistant.config_entries import (
    SOURCE_REAUTH,
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlow,
)
from homeassistant.core import callback
from homeassistant.helpers import selector
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import AllegroAuth, AllegroAuthError, AllegroClient, AllegroError
from .const import (
    CONF_CLIENT_ID,
    CONF_CLIENT_SECRET,
    CONF_FULFILLMENT,
    CONF_MAX_ORDERS,
    CONF_ONLY_PAID,
    CONF_SCAN_MINUTES,
    CONF_TOKEN,
    CONF_USER_AGENT,
    DEFAULT_FULFILLMENT,
    DEFAULT_MAX_ORDERS,
    DEFAULT_ONLY_PAID,
    DEFAULT_SCAN_MINUTES,
    DOMAIN,
    FULFILLMENT_STATUSES,
)

_LOGGER = logging.getLogger(__name__)


PLACEHOLDERS = {
    "dev_portal": "https://apps.developer.allegro.pl",
    "ua_example": "TPM/1.0 (+https://allegro.pl/uzytkownik/TwojLogin)",
}


def _creds_schema(defaults: Mapping[str, Any]) -> vol.Schema:
    return vol.Schema(
        {
            vol.Required(CONF_CLIENT_ID, default=defaults.get(CONF_CLIENT_ID, "")): str,
            vol.Required(CONF_CLIENT_SECRET, default=defaults.get(CONF_CLIENT_SECRET, "")): selector.TextSelector(
                selector.TextSelectorConfig(type=selector.TextSelectorType.PASSWORD)
            ),
            vol.Required(CONF_USER_AGENT, default=defaults.get(CONF_USER_AGENT, "")): str,
        }
    )


def _jwt_claims(token: str) -> dict[str, Any]:
    """Odczyt danych z access tokenu (JWT) bez weryfikacji podpisu."""
    import base64
    import json

    try:
        payload = token.split(".")[1]
        payload += "=" * (-len(payload) % 4)
        return json.loads(base64.urlsafe_b64decode(payload))
    except (IndexError, ValueError):
        return {}


class AllegroOrdersConfigFlow(ConfigFlow, domain=DOMAIN):
    VERSION = 1

    def __init__(self) -> None:
        self._creds: dict[str, str] = {}
        self._auth: AllegroAuth | None = None
        self._device: dict[str, Any] | None = None
        self._poll_task: asyncio.Task | None = None
        self._token: dict[str, Any] | None = None
        self._error: str | None = None

    # ---------- krok 1: dane aplikacji ----------
    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            self._creds = {k: str(v).strip() for k, v in user_input.items()}
            err = await self._start_device_flow()
            if err is None:
                return await self.async_step_authorize()
            errors["base"] = err
        return self.async_show_form(
            step_id="user",
            data_schema=_creds_schema(user_input or self._creds),
            errors=errors,
            description_placeholders=PLACEHOLDERS,
        )

    async def _start_device_flow(self) -> str | None:
        self._auth = AllegroAuth(
            async_get_clientsession(self.hass),
            self._creds[CONF_CLIENT_ID],
            self._creds[CONF_CLIENT_SECRET],
            self._creds[CONF_USER_AGENT],
        )
        self._poll_task = None
        try:
            self._device = await self._auth.start_device_flow()
        except AllegroAuthError:
            return "invalid_client"
        except AllegroError as err:
            _LOGGER.warning("Allegro device flow: %s", err)
            return "cannot_connect"
        return None

    # ---------- krok 2: zatwierdzenie w Allegro (polling w tle) ----------
    async def async_step_authorize(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        assert self._auth and self._device
        if self._poll_task is None:
            self._poll_task = self.hass.async_create_task(self._auth.wait_for_device_token(self._device))
        if not self._poll_task.done():
            return self.async_show_progress(
                step_id="authorize",
                progress_action="wait_for_approval",
                description_placeholders={
                    "url": self._device.get("verification_uri_complete")
                    or self._device.get("verification_uri", "https://allegro.pl/skojarz-aplikacje"),
                    "code": self._device.get("user_code", ""),
                },
                progress_task=self._poll_task,
            )
        try:
            self._token = self._poll_task.result()
        except AllegroError as err:
            _LOGGER.warning("Allegro: logowanie nieudane: %s", err)
            self._error = str(err)
            return self.async_show_progress_done(next_step_id="auth_failed")
        return self.async_show_progress_done(next_step_id="finish")

    async def async_step_auth_failed(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        return self.async_abort(reason="auth_failed", description_placeholders={"error": self._error or ""})

    # ---------- krok 3: zapis ----------
    async def async_step_finish(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        assert self._auth and self._token
        client = AllegroClient(self._auth, self._token)
        claims = _jwt_claims(client.token["access_token"])
        user_id = str(claims.get("user_name") or claims.get("sub") or "")
        title = "Allegro"
        try:
            me = await client.get_me()
            user_id = str(me.get("id") or user_id)
            title = me.get("login") or title
        except AllegroError as err:
            # /me wymaga uprawnienia do profilu – nie jest potrzebne do zamówień
            _LOGGER.info("Allegro /me niedostępne (%s) – używam danych z tokenu", err)

        # sprawdzenie, że zamówienia są dostępne
        try:
            await client.get("/order/checkout-forms", [("limit", "1")])
        except AllegroError as err:
            _LOGGER.warning("Allegro: brak dostępu do zamówień: %s", err)
            self._error = str(err)
            return self.async_abort(reason="orders_forbidden", description_placeholders={"error": str(err)[:300]})

        data = {**self._creds, CONF_TOKEN: client.token}
        await self.async_set_unique_id(user_id or client.token["refresh_token"][:16])

        if self.source == SOURCE_REAUTH:
            self._abort_if_unique_id_mismatch(reason="wrong_account")
            return self.async_update_reload_and_abort(self._get_reauth_entry(), data=data)

        self._abort_if_unique_id_configured()
        return self.async_create_entry(title=title, data=data)

    # ---------- ponowne logowanie (refresh token wygasł / unieważniony) ----------
    async def async_step_reauth(self, entry_data: Mapping[str, Any]) -> ConfigFlowResult:
        self._creds = {
            CONF_CLIENT_ID: entry_data[CONF_CLIENT_ID],
            CONF_CLIENT_SECRET: entry_data[CONF_CLIENT_SECRET],
            CONF_USER_AGENT: entry_data[CONF_USER_AGENT],
        }
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            self._creds = {k: str(v).strip() for k, v in user_input.items()}
            err = await self._start_device_flow()
            if err is None:
                return await self.async_step_authorize()
            errors["base"] = err
        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=_creds_schema(self._creds),
            errors=errors,
            description_placeholders=PLACEHOLDERS,
        )

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> OptionsFlow:
        return AllegroOrdersOptionsFlow()


class AllegroOrdersOptionsFlow(OptionsFlow):
    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        if user_input is not None:
            return self.async_create_entry(data=user_input)

        schema = vol.Schema(
            {
                vol.Required(CONF_FULFILLMENT, default=[x.lower() for x in DEFAULT_FULFILLMENT]): selector.SelectSelector(
                    selector.SelectSelectorConfig(
                        options=[x.lower() for x in FULFILLMENT_STATUSES],
                        multiple=True,
                        mode=selector.SelectSelectorMode.LIST,
                        translation_key="fulfillment",
                    )
                ),
                vol.Required(CONF_ONLY_PAID, default=DEFAULT_ONLY_PAID): bool,
                vol.Required(CONF_MAX_ORDERS, default=DEFAULT_MAX_ORDERS): selector.NumberSelector(
                    selector.NumberSelectorConfig(min=1, max=200, step=1, mode=selector.NumberSelectorMode.BOX)
                ),
                vol.Required(CONF_SCAN_MINUTES, default=DEFAULT_SCAN_MINUTES): selector.NumberSelector(
                    selector.NumberSelectorConfig(
                        min=1, max=120, step=1, unit_of_measurement="min", mode=selector.NumberSelectorMode.BOX
                    )
                ),
            }
        )
        return self.async_show_form(
            step_id="init",
            data_schema=self.add_suggested_values_to_schema(
                schema,
                {
                    **self.config_entry.options,
                    CONF_FULFILLMENT: [
                        x.lower() for x in self.config_entry.options.get(CONF_FULFILLMENT, DEFAULT_FULFILLMENT)
                    ],
                },
            ),
        )

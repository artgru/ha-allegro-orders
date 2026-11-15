"""Klient Allegro REST API – OAuth Device Flow + zamówienia.

Logika autoryzacji przeniesiona 1:1 ze skryptu „Allegro – Toolkit (Art)”:
  * POST /auth/oauth/device               – kod urządzenia + link do zatwierdzenia
  * POST /auth/oauth/token (device_code)  – polling aż użytkownik zatwierdzi
  * POST /auth/oauth/token (refresh_token)– odświeżanie, gdy access token wygasa
"""

from __future__ import annotations

import asyncio
import base64
import logging
import time
from collections.abc import Awaitable, Callable
from typing import Any
from urllib.parse import urlencode

import aiohttp

from .const import API_URL, AUTH_URL, MAX_SCANNED_ORDERS, MIME, PAGE_SIZE

_LOGGER = logging.getLogger(__name__)

FORM = "application/x-www-form-urlencoded"
TIMEOUT = aiohttp.ClientTimeout(total=30)


class AllegroError(Exception):
    """Ogólny błąd komunikacji z Allegro."""


class AllegroAuthError(AllegroError):
    """Token nieważny i nie da się go odświeżyć – potrzebne ponowne logowanie."""


class AllegroAuthPending(AllegroError):
    """Użytkownik jeszcze nie zatwierdził dostępu (device flow)."""


class AllegroAuth:
    """Operacje OAuth wspólne dla config flow i klienta API."""

    def __init__(
        self,
        session: aiohttp.ClientSession,
        client_id: str,
        client_secret: str,
        user_agent: str,
    ) -> None:
        self._session = session
        self.client_id = client_id
        self._client_secret = client_secret
        self.user_agent = user_agent

    @property
    def _basic(self) -> str:
        raw = f"{self.client_id}:{self._client_secret}".encode()
        return "Basic " + base64.b64encode(raw).decode()

    async def _post_form(self, url: str, body: dict[str, str]) -> tuple[int, dict[str, Any], str]:
        headers = {
            "Authorization": self._basic,
            "Content-Type": FORM,
            "User-Agent": self.user_agent,
        }
        try:
            async with self._session.post(
                url, data=urlencode(body), headers=headers, timeout=TIMEOUT
            ) as resp:
                text = await resp.text()
                try:
                    data = await resp.json(content_type=None)
                except ValueError:
                    data = {}
                return resp.status, data or {}, text
        except (aiohttp.ClientError, asyncio.TimeoutError) as err:
            raise AllegroError(f"Błąd sieci: {err}") from err

    async def start_device_flow(self) -> dict[str, Any]:
        """Rozpocznij logowanie – zwraca device_code, user_code, verification_uri_complete…"""
        status, data, text = await self._post_form(
            f"{AUTH_URL}/device?client_id={self.client_id}",
            {"client_id": self.client_id},
        )
        if status == 401:
            raise AllegroAuthError("Nieprawidłowy Client ID lub Client Secret.")
        if status != 200:
            raise AllegroError(f"Nie udało się rozpocząć logowania ({status}): {text[:300]}")
        return data

    async def poll_device_token(self, device_code: str) -> dict[str, Any]:
        """Jedno zapytanie o token. AllegroAuthPending = jeszcze nie zatwierdzono."""
        status, data, text = await self._post_form(
            f"{AUTH_URL}/token",
            {
                "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
                "device_code": device_code,
            },
        )
        if status == 200:
            return _normalize_token(data)
        err = data.get("error")
        if err in ("authorization_pending", "slow_down"):
            raise AllegroAuthPending(err)
        raise AllegroAuthError(f"Logowanie odrzucone: {text[:300]}")

    async def wait_for_device_token(self, device: dict[str, Any]) -> dict[str, Any]:
        """Polling aż do zatwierdzenia lub wygaśnięcia kodu."""
        interval = int(device.get("interval") or 5)
        deadline = time.monotonic() + int(device.get("expires_in") or 3600)
        while time.monotonic() < deadline:
            await asyncio.sleep(interval)
            try:
                return await self.poll_device_token(device["device_code"])
            except AllegroAuthPending as err:
                if str(err) == "slow_down":
                    interval += 5
        raise AllegroAuthError("Minął czas na zatwierdzenie logowania.")

    async def refresh(self, refresh_token: str) -> dict[str, Any]:
        status, data, text = await self._post_form(
            f"{AUTH_URL}/token",
            {"grant_type": "refresh_token", "refresh_token": refresh_token},
        )
        if status == 200:
            return _normalize_token(data)
        if status in (400, 401):
            raise AllegroAuthError(f"Nie udało się odświeżyć tokenu: {text[:300]}")
        raise AllegroError(f"Błąd odświeżania tokenu ({status}): {text[:300]}")


def _normalize_token(data: dict[str, Any]) -> dict[str, Any]:
    return {
        "access_token": data["access_token"],
        "refresh_token": data.get("refresh_token"),
        # zapas 120 s – tak jak w skrypcie Tampermonkey
        "expires_at": time.time() + int(data.get("expires_in", 3600)) - 120,
    }


class AllegroClient:
    """Klient REST z automatycznym odświeżaniem tokenu."""

    def __init__(
        self,
        auth: AllegroAuth,
        token: dict[str, Any],
        on_token_update: Callable[[dict[str, Any]], Awaitable[None] | None] | None = None,
    ) -> None:
        self._auth = auth
        self._session = auth._session  # noqa: SLF001
        self.token = dict(token)
        self._on_token_update = on_token_update
        self._lock = asyncio.Lock()

    async def _ensure_token(self, force: bool = False) -> str:
        async with self._lock:
            if not force and self.token.get("expires_at", 0) > time.time():
                return self.token["access_token"]
            refresh = self.token.get("refresh_token")
            if not refresh:
                raise AllegroAuthError("Brak refresh tokenu – zaloguj się ponownie.")
            new = await self._auth.refresh(refresh)
            if not new.get("refresh_token"):
                new["refresh_token"] = refresh
            self.token = new
            if self._on_token_update:
                res = self._on_token_update(new)
                if asyncio.iscoroutine(res):
                    await res
            return new["access_token"]

    async def get(self, path: str, params: list[tuple[str, str]] | None = None) -> dict[str, Any]:
        for attempt in (0, 1):
            token = await self._ensure_token(force=attempt == 1)
            headers = {
                "Authorization": f"Bearer {token}",
                "Accept": MIME,
                "User-Agent": self._auth.user_agent,
            }
            try:
                async with self._session.get(
                    API_URL + path, params=params, headers=headers, timeout=TIMEOUT
                ) as resp:
                    if resp.status == 401 and attempt == 0:
                        continue  # token unieważniony – wymuś odświeżenie i spróbuj raz jeszcze
                    text = await resp.text()
                    if resp.status == 401:
                        raise AllegroAuthError("Allegro odrzuciło token.")
                    if resp.status != 200:
                        raise AllegroError(f"GET {path} → {resp.status}: {text[:300]}")
                    return await resp.json(content_type=None)
            except (aiohttp.ClientError, asyncio.TimeoutError) as err:
                raise AllegroError(f"Błąd sieci: {err}") from err
        raise AllegroAuthError("Allegro odrzuciło token.")

    async def get_me(self) -> dict[str, Any]:
        return await self.get("/me")

    async def get_orders(
        self,
        fulfillment: list[str],
        only_paid: bool,
        max_orders: int,
    ) -> list[dict[str, Any]]:
        """Najnowsze zamówienia spełniające filtry (max `max_orders`)."""
        base: list[tuple[str, str]] = [("sort", "-lineItems.boughtAt")]
        if only_paid:
            base.append(("status", "READY_FOR_PROCESSING"))
        server_filter = [("fulfillment.status", s) for s in fulfillment]
        wanted = set(fulfillment)

        result: list[dict[str, Any]] = []
        offset = 0
        use_server_filter = bool(server_filter)
        while offset < MAX_SCANNED_ORDERS and len(result) < max_orders:
            params = [*base, *(server_filter if use_server_filter else [])]
            params += [("limit", str(PAGE_SIZE)), ("offset", str(offset))]
            try:
                data = await self.get("/order/checkout-forms", params)
            except AllegroError as err:
                # Gdyby API nie przyjęło wielu statusów – filtrujemy sami
                if use_server_filter and " → 4" in str(err):
                    _LOGGER.debug("Filtr fulfillment.status odrzucony, filtruję lokalnie: %s", err)
                    use_server_filter = False
                    offset = 0
                    result = []
                    continue
                raise
            forms = data.get("checkoutForms") or []
            for form in forms:
                st = (form.get("fulfillment") or {}).get("status")
                if not wanted or st in wanted:
                    result.append(form)
            if len(forms) < PAGE_SIZE:
                break
            offset += PAGE_SIZE
        return result[:max_orders]

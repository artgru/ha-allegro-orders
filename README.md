# Allegro – zamówienia (Home Assistant)

Integracja pokazuje w Home Assistant listę zamówień z Allegro: **data, produkt, SKU (sygnatura), metoda wysyłki**.
Logowanie przez OAuth Device Flow – tak samo jak w skrypcie Toolkit do Tampermonkey (ta sama aplikacja Allegro może obsługiwać oba).

## Instalacja (HACS)

1. HACS → ⋮ → **Niestandardowe repozytoria** → URL tego repozytorium, typ **Integracja**.
2. Zainstaluj „Allegro – zamówienia” i zrestartuj Home Assistant.
3. Ustawienia → Urządzenia i usługi → **Dodaj integrację** → „Allegro – zamówienia”.
4. Wpisz **Client ID**, **Client Secret** i zarejestrowany **User-Agent** (z apps.developer.allegro.pl).
5. Kliknij link, który się pojawi, zatwierdź dostęp w Allegro – okno samo przejdzie dalej.

Dane aplikacji podajesz w formularzu, więc **nie ma ich w repozytorium** (repo może być publiczne).

## Co powstaje

`sensor.allegro_zamowienia`
- stan: liczba zamówień spełniających filtr,
- atrybut `orders`: lista pozycji (`date`, `date_display`, `product`, `sku`, `quantity`, `price` (cena za sztukę), `total` (cena × sztuki), `currency`, `shipping`, `pickup_point`, `dispatch_by`, `status_label`, `buyer`, `message` (komentarz kupującego), `order_id`, `offer_id`, `smart`),
- atrybut `items`: łączna liczba sztuk,
- atrybut `total_value`: łączna wartość pozycji na liście.

Lista nie jest zapisywana w historii (recorder), żeby nie puchła baza.

## Zmiana statusu zamówienia

- `select.allegro_wybrane_zamowienie` – wybierasz zamówienie z listy,
- `select.allegro_status_zamowienia` – pokazuje jego status; zmiana tutaj zmienia status w Allegro,
- usługa `allegro_orders.set_status` (`order_id`, `status`) – do automatyzacji i przycisków.

Wymaga uprawnienia aplikacji Allegro do zarządzania zamówieniami (`allegro:api:orders:write`).

## Opcje (Konfiguruj)

- statusy realizacji (domyślnie: Nowe, W realizacji, Do wysłania),
- statusy, które można ustawić z listy wyboru (domyślnie: W realizacji, Do wysłania, Wysłane),
- tylko opłacone (domyślnie tak),
- maks. liczba zamówień (domyślnie 50),
- odświeżanie co N minut (domyślnie 5).

## Karta na dashboard

Wklej [`examples/karta_zamowienia.yaml`](examples/karta_zamowienia.yaml) jako kartę w trybie YAML. SKU jest pogrubione.

## Gdy token wygaśnie

Refresh token Allegro jest ważny ok. 3 miesiące i odnawia się przy każdym użyciu. Jeśli zostanie unieważniony,
Home Assistant pokaże „Wymagane ponowne uwierzytelnienie” – klikasz i zatwierdzasz nowy kod.

Po zmianie uprawnień aplikacji w Allegro zaloguj się ponownie ręcznie: integracja → **Konfiguruj → Zaloguj ponownie do Allegro**.

## Aktualizacje

Zmień `version` w `custom_components/allegro_orders/manifest.json`, zrób commit i utwórz Release na GitHubie
(np. `v1.0.1`) – HACS pokaże aktualizację.

"""Stałe integracji Allegro – zamówienia."""

DOMAIN = "allegro_orders"

AUTH_URL = "https://allegro.pl/auth/oauth"
API_URL = "https://api.allegro.pl"
MIME = "application/vnd.allegro.public.v1+json"

CONF_CLIENT_ID = "client_id"
CONF_CLIENT_SECRET = "client_secret"
CONF_USER_AGENT = "user_agent"
CONF_TOKEN = "token"

# Opcje
CONF_FULFILLMENT = "fulfillment_statuses"
CONF_ONLY_PAID = "only_paid"
CONF_MAX_ORDERS = "max_orders"
CONF_SCAN_MINUTES = "scan_interval_minutes"

# Statusy realizacji zamówienia w Allegro (fulfillment.status)
FULFILLMENT_STATUSES = [
    "NEW",
    "PROCESSING",
    "READY_FOR_SHIPMENT",
    "READY_FOR_PICKUP",
    "SENT",
    "PICKED_UP",
    "CANCELLED",
    "SUSPENDED",
    "RETURNED",
]
FULFILLMENT_LABELS = {
    "NEW": "Nowe",
    "PROCESSING": "W realizacji",
    "READY_FOR_SHIPMENT": "Do wysłania",
    "READY_FOR_PICKUP": "Do odbioru",
    "SENT": "Wysłane",
    "PICKED_UP": "Odebrane",
    "CANCELLED": "Anulowane",
    "SUSPENDED": "Wstrzymane",
    "RETURNED": "Zwrócone",
}

DEFAULT_FULFILLMENT = ["NEW", "PROCESSING", "READY_FOR_SHIPMENT"]
DEFAULT_ONLY_PAID = True
DEFAULT_MAX_ORDERS = 50
DEFAULT_SCAN_MINUTES = 5

# Ile zamówień maksymalnie przejrzeć przy filtrowaniu po stronie integracji
MAX_SCANNED_ORDERS = 500
PAGE_SIZE = 100

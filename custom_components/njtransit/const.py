"""Constants for the NJ Transit integration."""

from __future__ import annotations

from datetime import timedelta
from typing import Final

DOMAIN: Final = "njtransit"

CONF_ORIGIN: Final = "origin"
CONF_ORIGIN_ID: Final = "origin_id"
CONF_DESTINATION: Final = "destination"
CONF_DESTINATION_ID: Final = "destination_id"

CONF_DEPARTURE_INTERVAL: Final = "departure_interval"
CONF_STATUS_INTERVAL: Final = "status_interval"
CONF_DEPARTURE_COUNT: Final = "departure_count"
CONF_DELAY_THRESHOLD: Final = "delay_threshold"
CONF_LOOKAHEAD: Final = "lookahead"

DEFAULT_DEPARTURE_INTERVAL: Final = 60
DEFAULT_STATUS_INTERVAL: Final = 120
DEFAULT_DEPARTURE_COUNT: Final = 3
DEFAULT_DELAY_THRESHOLD: Final = 10
DEFAULT_LOOKAHEAD: Final = 90

MAX_DEPARTURE_COUNT: Final = 10

# Every response carries a `maxAge: 30` cache hint, so 30s is the cadence the
# vendor's own CDN is configured for. Polling faster gains nothing and is
# rude.
MIN_INTERVAL: Final = 30

STATIC_INTERVAL: Final = timedelta(hours=24)
ROUTE_INTERVAL: Final = timedelta(hours=24)

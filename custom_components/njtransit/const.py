"""Constants for the NJ Transit integration."""

from __future__ import annotations

from datetime import timedelta
from typing import Final

DOMAIN: Final = "njtransit"

CONF_ORIGIN: Final = "origin"
CONF_ORIGIN_ID: Final = "origin_id"
CONF_DESTINATION: Final = "destination"
CONF_DESTINATION_ID: Final = "destination_id"

# Which API answers. Stored in the entry's data rather than its options
# because changing it changes the client every coordinator is built on,
# which is a reload, not a live tweak.
CONF_SOURCE: Final = "source"
SOURCE_WEBSITE: Final = "website"
"""The private GraphQL endpoint behind njtransit.com. No account needed."""
SOURCE_RAILDATA: Final = "raildata"
"""NJ Transit's documented RailData API. Needs a developer account, and in
return gives the signalling system's track before the board posts it."""
DEFAULT_SOURCE: Final = SOURCE_WEBSITE

CONF_DEPARTURE_INTERVAL: Final = "departure_interval"
CONF_STATUS_INTERVAL: Final = "status_interval"
CONF_DEPARTURE_COUNT: Final = "departure_count"
CONF_DELAY_THRESHOLD: Final = "delay_threshold"
CONF_LOOKAHEAD: Final = "lookahead"
CONF_FAVORITE_TRAINS: Final = "favorite_trains"

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

# How much track-assignment history to keep. Thirty days is what
# choochootracker.com states it analyses, and it covers four of each weekday,
# which is the shortest window that can tell a weekday pattern from a run of
# coincidences.
TRACK_HISTORY_DAYS: Final = 30

# Writes are coalesced rather than issued per assignment: this file is the
# largest thing the integration owns, and Home Assistant flushes a pending
# delayed save on shutdown, so a long delay costs nothing but a crash.
TRACK_HISTORY_SAVE_DELAY: Final = 600

# Storage key for what the RailData client must not fetch twice: its token
# (ten a day) and each station's schedule (five a day). One file per account.
RAILDATA_STORAGE_KEY: Final = f"{DOMAIN}.raildata"
RAILDATA_STORAGE_VERSION: Final = 1

"""GraphQL operations for the NJ Transit endpoint.

Every operation here is **named** and takes its arguments through `variables`.
That is not a style preference: a WAF sits in front of the endpoint and
rejects inline arguments with a non-GraphQL
``{"status":400,"message":"Malformed request"}``. See SPEC 3.2.

Field selections are pinned deliberately and mirror what njtransit.com itself
sends. Do not widen one without evidence the server populates the new field --
a field that is non-nullable in the schema but null in the data nulls the
*entire* response, not just that field. ``getTrainStations { latitude }``
returns ``data: null`` for exactly this reason. See SPEC 3.1.

``scripts/extract_ops.py`` re-derives these from the site's JS bundles;
introspection is disabled, so that script is the only way to check them
against upstream.
"""

from __future__ import annotations

from dataclasses import dataclass

ENDPOINT = "https://www.njtransit.com/api/graphql/graphql"


@dataclass(frozen=True)
class Operation:
    """A named GraphQL operation ready to POST."""

    name: str
    """The operation name, sent as ``operationName``."""

    document: str
    """The query document."""

    root_field: str
    """The field under ``data`` holding this operation's payload."""


SYSTEM_STATUS = Operation(
    name="SystemStatus",
    root_field="getSystemStatus",
    document="""
query SystemStatus {
  getSystemStatus {
    abbreviation
    message
    msg_richtext
    msg_url
    service
    advisoryAlert
  }
}
""",
)

# `stops` is requested because the site requests it, but the board never
# populates it -- see SPEC 2.2. Keep it so a future upstream change shows up in
# the fixtures rather than needing a query edit to discover.
DEPARTURE_BOARD = Operation(
    name="TrainDepartureScreens",
    root_field="getTrainDepartureScreens",
    document="""
query TrainDepartureScreens($station: String!) {
  getTrainDepartureScreens(station: $station) {
    items {
      background
      color
      departureDate
      destination
      inlineMessage
      line
      lineAbbreviation
      status
      track
      trainID
      stops {
        departed
        dropOff
        name
        status
        time
      }
      capacity {
        sections {
          position
          cars {
            color
            number
          }
        }
      }
    }
    bannerMsg
    fullScreenMsg
  }
}
""",
)

STATIONS = Operation(
    name="TrainScheduleStationsRailForDV",
    root_field="getTrainScheduleStationsRailForDV",
    document="""
query TrainScheduleStationsRailForDV {
  getTrainScheduleStationsRailForDV {
    title
    pentaStationID
    accessible
  }
}
""",
)

TRAIN_LINES = Operation(
    name="TrainLines",
    root_field="getTrainLines",
    document="""
query TrainLines {
  getTrainLines {
    id
    title
    abbreviation
  }
}
""",
)

# The planner needs every one of these arguments in practice. Omitting `date`
# or `time` fails with a generic "unable to find trips" that is
# indistinguishable from a genuine no-service result. See SPEC 2.6.
TRIP_PLANNER = Operation(
    name="TripPlannerSchedule",
    root_field="getTripPlannerSchedule",
    document="""
query TripPlannerSchedule(
  $origin: String!
  $destination: String!
  $timeOption: String
  $date: String
  $time: String
  $accessible: Boolean
  $travelMode: String
  $maxWalkingDistance: String
  $minimizeTime: String
) {
  getTripPlannerSchedule(
    origin: $origin
    destination: $destination
    timeOption: $timeOption
    date: $date
    time: $time
    accessible: $accessible
    travelMode: $travelMode
    maxWalkingDistance: $maxWalkingDistance
    minimizeTime: $minimizeTime
  ) {
    duration
    legs {
      block
      route
      routeType
      onStopDescription
      onStopTime
      offStopDescription
      offStopTime
    }
  }
}
""",
)

# Unused by v1. Kept because it is verified against the live endpoint, so
# per-train stop tracking starts from a known-good query rather than a
# guess -- and because a wrong field selection here nulls the whole
# response (SPEC 3.1), which is exactly the mistake this avoids.
STOP_LIST = Operation(
    name="TrainStopList",
    root_field="getTrainStopList",
    document="""
query TrainStopList($train: String!) {
  getTrainStopList(train: $train) {
    departed
    dropOff
    name
    status
    time
  }
}
""",
)

# Defaults the site itself sends. `accessible` is declared Boolean in the
# schema but njtransit.com sends the strings "true"/"false"; the Boolean form
# is accepted, so use it.
TRIP_PLANNER_DEFAULTS = {
    "timeOption": "D",
    "accessible": False,
    "travelMode": "BCTLXR",
    "maxWalkingDistance": "1.00",
    "minimizeTime": "T",
}

PLANNER_DATE_FORMAT = "%m/%d/%Y"
"""Anything else is rejected -- an ISO date returns HTTP 500. See SPEC 2.6."""

PLANNER_TIME_FORMAT = "%I:%M %p"
"""Twelve-hour, e.g. ``09:30 AM``. A leading zero is accepted; ``%-I`` is
avoided because it is not portable."""

RAIL_ROUTE_TYPE = "C"
"""``routeType`` for commuter rail. Bus is ``B``, PATH ``T``, walking ``W``."""

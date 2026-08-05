"""Diagnostics for the NJ Transit integration.

No redaction is needed. The endpoint takes no credentials and returns no
personal data -- these are public departure boards and service alerts.

The contents are chosen around the failure modes users actually hit. This
integration talks to an undocumented endpoint through four different naming
vocabularies, so "which of them failed to line up" is almost always the
question, and the raw payload alone will not answer it.
"""

from __future__ import annotations

from dataclasses import asdict
from typing import Any

from homeassistant.core import HomeAssistant

from .api.parsing import alert_line_codes, now_local
from .coordinator import NJTransitConfigEntry
from .entity import usable_departures
from .track_history import TrackHistory


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant,
    entry: NJTransitConfigEntry,
) -> dict[str, Any]:
    """Return diagnostics for a commute."""
    runtime = entry.runtime_data
    board = runtime.board.data
    route = runtime.route.data
    static = runtime.static.data
    alerts = runtime.status.data or ()

    usable = usable_departures(board, route, runtime.destination)
    board_lines = {departure.line for departure in board.departures} if board else set()

    return {
        "config": {
            "origin": runtime.origin,
            "destination": runtime.destination,
            "options": dict(entry.options),
        },
        "coordinators": {
            "board": _coordinator_state(runtime.board),
            "status": _coordinator_state(runtime.status),
            "static": _coordinator_state(runtime.static),
            "route": _coordinator_state(runtime.route),
        },
        # The destination filter is the most common source of "why is my
        # sensor empty". Show what it resolved to and what it kept.
        "filter": {
            "resolved_train_ids": sorted(route.train_ids) if route else [],
            "resolution_complete": route.complete if route else None,
            "scheduled_trips": len(route.trips) if route else 0,
            "board_departures": len(board.departures) if board else 0,
            "usable_departures": [departure.train_id for departure in usable],
        },
        # Four vocabularies name these lines differently; a mismatch here is
        # why alerts go missing or arrive for the wrong line.
        "lines": {
            "board_titles": sorted(board_lines),
            "resolved_alert_codes": sorted(
                alert_line_codes(board_lines, static.lines if static else ())
            ),
            "known_lines": (
                [line.abbreviation for line in static.lines] if static else []
            ),
            "alert_codes_seen": sorted({alert.line_abbreviation for alert in alerts}),
        },
        # The whole collected history, not a summary of it. This is the export
        # path for `scripts/analyze_tracks.py`, and a model can only be scored
        # against observations it can see. One station's worth per entry, so
        # downloading both commutes' diagnostics gives both boards.
        "track_history": _track_history(runtime.history, runtime.origin),
        "board": _board(board),
        "alerts": [
            asdict(alert) | {"train_ids": sorted(alert.train_ids)} for alert in alerts
        ],
        "generated_at": now_local().isoformat(),
    }


def _track_history(history: TrackHistory, station: str) -> dict[str, Any]:
    """Return recorded track assignments for a station, with a summary."""
    return {
        "station": station,
        "summary": history.summary(station),
        "days": history.days_for(station),
    }


def _coordinator_state(coordinator: Any) -> dict[str, Any]:
    """Return whether a coordinator is healthy, and how often it polls."""
    interval = coordinator.update_interval
    return {
        "last_update_success": coordinator.last_update_success,
        "has_data": coordinator.data is not None,
        "update_interval_seconds": interval.total_seconds() if interval else None,
    }


def _board(board: Any) -> dict[str, Any] | None:
    """Return the parsed board, with datetimes made serializable."""
    if board is None:
        return None

    return {
        "station": board.station,
        "banner_message": board.banner_message,
        "fullscreen_message": board.fullscreen_message,
        "departures": [
            asdict(departure) | {"scheduled": departure.scheduled.isoformat()}
            for departure in board.departures
        ],
    }

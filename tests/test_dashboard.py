"""The example dashboard's templates, rendered against real states.

Lovelace has no load-and-drive path the way blueprints do, so the cards are
lifted out of the YAML and put through Home Assistant's own template engine.
That is enough: these cards are pure rendering, and every bug they have had was
a rendering bug -- a five-centimetre countdown to a cancelled train, a pill
welded onto the end of a heading, a track called overdue at a station that
posts none.

Cards are found by a signature in their content rather than by index, so
reordering the dashboard cannot silently point a test at a different card.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
import yaml
from freezegun.api import FrozenDateTimeFactory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.template import Template

from custom_components.njtransit.api.parsing import TZ
from custom_components.njtransit.event import TRACK_OVERDUE_LEAD

DASHBOARD = Path(__file__).resolve().parent.parent / "dashboards" / "trains.yaml"

MORNING_PREFIX = "short_hills_station_to_new_york_penn_station"
EVENING_PREFIX = "new_york_penn_station_to_short_hills_station"

PREFIX = f"sensor.{MORNING_PREFIX}"
FAVORITE = f"{PREFIX}_next_favorite"
ROWS = [f"{PREFIX}_next_departure", f"{PREFIX}_departure_2", f"{PREFIX}_departure_3"]
DISRUPTED = f"binary_sensor.{MORNING_PREFIX}_commute_disrupted"

NOW = datetime(2026, 8, 5, 8, 0, tzinfo=TZ)


def views() -> list[dict[str, Any]]:
    """Return the dashboard's views."""
    return yaml.safe_load(DASHBOARD.read_text())["views"]


def card(signature: str, view: int = 0) -> str:
    """Return the markdown card whose template contains ``signature``."""
    contents = [
        c["content"]
        for c in views()[view]["sections"][0]["cards"]
        if c["type"] == "markdown" and signature in c["content"]
    ]
    assert len(contents) == 1, f"{signature!r} matched {len(contents)} cards, want 1"
    return contents[0]


HERO = "Nothing on the board"
TABLE = "| Departs | Train | Trk | |"
NOTICES = "affects_my_trains"


def render(hass: HomeAssistant, content: str) -> str:
    """Render one card the way Home Assistant renders it."""
    return str(Template(content, hass).async_render(parse_result=False))


def departure(
    hass: HomeAssistant,
    entity: str,
    *,
    minutes: int | None,
    train_id: str = "6320",
    track: str | None = "4",
    status: str = "on_time",
    status_text: str = "On time",
    delay: int | None = 0,
    crowding: str = "unknown",
    cars: list[dict[str, str]] | None = None,
    favorites: list[str] | None = None,
) -> None:
    """Publish one departure sensor."""
    when = (
        (NOW + timedelta(minutes=minutes)).isoformat()
        if minutes is not None
        else "unknown"
    )
    hass.states.async_set(
        entity,
        when,
        {
            "train_id": train_id,
            "track": track,
            "status": status,
            "status_text": status_text,
            "delay_minutes": delay,
            "destination": "New York",
            "crowding": crowding,
            "cars": cars or [],
            "alerts": [],
            "favorites": favorites if favorites is not None else ["6320"],
        },
    )


@pytest.fixture(autouse=True)
def at_eight(freezer: FrozenDateTimeFactory) -> None:
    """Pin the clock: every countdown on this dashboard is relative to now."""
    freezer.move_to(NOW)


class TestTheHeroNeverCountsDownToSomethingUncatchable:
    """The bug that reached a screen during a line suspension."""

    async def test_a_wholly_cancelled_board_says_so(self, hass: HomeAssistant) -> None:
        """A five-centimetre "7 MIN" over a train that was not running.

        A cancelled service stays on the board -- that is the point of it
        being there -- so the fallback taking row zero put the largest element
        on the page in service of a train nobody could board, with the word
        Cancelled underneath in eight-point type.
        """
        for index, entity in enumerate(ROWS):
            departure(
                hass,
                entity,
                minutes=12 + index * 60,
                train_id=f"69{index}",
                status="cancelled",
                status_text="Cancelled",
                delay=None,
            )
        departure(hass, FAVORITE, minutes=None)

        output = render(hass, card(HERO))

        assert "Every upcoming departure is cancelled" in output
        # `*min*` is the countdown's unit marker, so its absence is the
        # absence of a countdown -- which is the whole assertion.
        assert "*min*" not in output

    async def test_the_fallback_skips_to_a_boardable_train(
        self, hass: HomeAssistant
    ) -> None:
        """One cancellation is not a suspension."""
        departure(
            hass,
            ROWS[0],
            minutes=12,
            train_id="6918",
            status="cancelled",
            status_text="Cancelled",
            delay=None,
        )
        departure(hass, ROWS[1], minutes=20, train_id="6920")
        departure(hass, ROWS[2], minutes=80, train_id="6922")
        departure(hass, FAVORITE, minutes=None)

        output = render(hass, card(HERO))

        assert "6920" in output
        assert "20 *min*" in output

    async def test_a_cancelled_favourite_is_still_shown(
        self, hass: HomeAssistant
    ) -> None:
        """The filter is about what to fall back *to*, not about hiding news.

        If the train you were going to catch is not running, that is the single
        most important thing this card can tell you.
        """
        departure(hass, ROWS[0], minutes=20, train_id="6920")
        departure(
            hass,
            FAVORITE,
            minutes=12,
            train_id="6320",
            status="cancelled",
            status_text="Cancelled",
            delay=None,
        )

        output = render(hass, card(HERO))

        assert "6320" in output
        assert "**`Cancelled`**" in output


class TestTheTrackPill:
    """Four states, and the one that must not fire at the wrong station."""

    async def test_an_assigned_track_is_the_neutral_pill(
        self, hass: HomeAssistant
    ) -> None:
        departure(hass, ROWS[0], minutes=12, track="4")
        departure(hass, FAVORITE, minutes=12, track="4")

        assert "`Track 4`" in render(hass, card(HERO))

    async def test_a_missing_track_inside_the_threshold_is_overdue(
        self, hass: HomeAssistant
    ) -> None:
        """Only meaningful because the other rows have theirs."""
        departure(hass, ROWS[0], minutes=4, track=None)
        departure(hass, ROWS[1], minutes=40, track="7")
        departure(hass, FAVORITE, minutes=4, track=None)

        assert "Track overdue" in render(hass, card(HERO))

    async def test_a_station_posting_no_tracks_makes_no_claim(
        self, hass: HomeAssistant
    ) -> None:
        """The same test the integration applies before firing `track_overdue`.

        Without it, a station that never publishes -- or a feed that drops the
        field -- reports every train as permanently overdue, which is the
        loudest possible way of saying nothing.
        """
        for entity in ROWS:
            departure(hass, entity, minutes=4, track=None)
        departure(hass, FAVORITE, minutes=4, track=None)

        output = render(hass, card(HERO))

        assert "Track overdue" not in output
        assert "Track not posted" in output

    async def test_a_track_still_due_is_not_yet_a_deviation(
        self, hass: HomeAssistant
    ) -> None:
        departure(hass, ROWS[0], minutes=20, track=None)
        departure(hass, ROWS[1], minutes=40, track="7")
        departure(hass, FAVORITE, minutes=20, track=None)

        output = render(hass, card(HERO))

        assert "Track overdue" not in output
        assert "Track due" in output

    def test_the_overdue_threshold_matches_the_integration(self) -> None:
        """A card contradicting the event it illustrates is worse than neither.

        `TRACK_OVERDUE_LEAD` has moved once already. Nothing links these two
        numbers but this assertion.
        """
        minutes = int(TRACK_OVERDUE_LEAD.total_seconds() // 60)
        assert f"mins <= {minutes} %}}**`⚠️ Track overdue`**" in card(HERO)


class TestCrowding:
    """Where to stand, and only when standing elsewhere would help."""

    async def test_it_names_the_emptier_end(self, hass: HomeAssistant) -> None:
        cars = [
            {"number": "1", "position": "Front", "crowding": "heavy"},
            {"number": "2", "position": "Back", "crowding": "light"},
        ]
        departure(hass, ROWS[0], minutes=12, cars=cars)
        departure(hass, FAVORITE, minutes=12, cars=cars)

        assert "Back cars are emptier" in render(hass, card(HERO))

    async def test_a_uniformly_full_train_says_nothing(
        self, hass: HomeAssistant
    ) -> None:
        """ "Front busy, middle busy, back busy" is three facts and no decision."""
        cars = [
            {"number": "1", "position": "Front", "crowding": "heavy"},
            {"number": "2", "position": "Back", "crowding": "heavy"},
        ]
        departure(hass, ROWS[0], minutes=12, cars=cars)
        departure(hass, FAVORITE, minutes=12, cars=cars)

        assert "emptier" not in render(hass, card(HERO))


class TestTheDeparturesTable:
    """Aligned columns, which is what makes three departures glanceable."""

    async def test_each_row_carries_its_track(self, hass: HomeAssistant) -> None:
        departure(hass, ROWS[0], minutes=12, train_id="6320", track="4")
        departure(hass, ROWS[1], minutes=60, train_id="6322", track="7")
        departure(hass, ROWS[2], minutes=None)

        output = render(hass, card(TABLE))

        assert "| 6320 | `4` |" in output
        assert "| 6322 | `7` |" in output

    async def test_a_missing_track_inside_the_threshold_is_flagged(
        self, hass: HomeAssistant
    ) -> None:
        departure(hass, ROWS[0], minutes=4, train_id="6320", track=None)
        departure(hass, ROWS[1], minutes=60, train_id="6322", track="7")
        departure(hass, ROWS[2], minutes=None)

        assert "**`⚠️`**" in render(hass, card(TABLE))

    async def test_an_empty_board_is_stated_rather_than_left_blank(
        self, hass: HomeAssistant
    ) -> None:
        for entity in ROWS:
            departure(hass, entity, minutes=None)

        assert "Nothing scheduled" in render(hass, card(TABLE))


class TestServiceNotices:
    """Three paragraphs about another line and one about your train."""

    async def test_it_leads_with_whether_a_notice_names_your_train(
        self, hass: HomeAssistant
    ) -> None:
        hass.states.async_set(
            f"{PREFIX}_service_alerts",
            "1",
            {
                "messages": ["Train 6320 is cancelled due to a disabled train"],
                "affects_my_trains": ["6320"],
            },
        )
        hass.states.async_set(f"{PREFIX}_planned_advisories", "0", {"messages": []})

        output = render(hass, card(NOTICES))

        assert "Names your train 6320" in output

    async def test_line_level_noise_is_not_dressed_up_as_yours(
        self, hass: HomeAssistant
    ) -> None:
        hass.states.async_set(
            f"{PREFIX}_service_alerts",
            "1",
            {
                "messages": ["Gladstone Branch is operating close to schedule"],
                "affects_my_trains": [],
            },
        )
        hass.states.async_set(f"{PREFIX}_planned_advisories", "0", {"messages": []})

        output = render(hass, card(NOTICES))

        assert "Names your train" not in output
        assert "Gladstone" in output


class TestTheTwoViewsStayInStep:
    """Morning and evening are one card set with one string changed."""

    def test_they_are_identical_apart_from_the_commute(self) -> None:
        """Every fix has to land in both, and nothing else enforces that.

        The prefix is set on each card's first line precisely so this holds;
        without the check, a fix applied to one view and not the other is
        invisible until someone reads the wrong half of their commute.
        """
        morning, evening = views()

        def normalise(view: dict[str, Any], prefix: str, route: str, icon: str) -> str:
            # `allow_unicode`, or the arrow in each heading dumps as
            # `\u2192` and the substitution below silently misses it -- which
            # reads as the two views differing when they do not.
            text = yaml.safe_dump(view["sections"], sort_keys=True, allow_unicode=True)
            return text.replace(prefix, "P").replace(route, "R").replace(icon, "I")

        assert normalise(
            morning,
            MORNING_PREFIX,
            "Short Hills → New York Penn",
            "mdi:weather-sunset-up",
        ) == normalise(
            evening,
            EVENING_PREFIX,
            "New York Penn → Short Hills",
            "mdi:weather-night",
        )

    def test_each_view_keeps_the_path_the_blueprint_links_to(self) -> None:
        """Renaming a view breaks the Live Activity's tap-through silently."""
        assert [view["path"] for view in views()] == ["morning", "evening"]

"""Replaying and scoring nypenn.live's feed.

The comparison this feeds into decides whether we build track prediction at
all, so its failure mode is the same one issue #35 already caught once in our
own analysis: a number that looks like a result. Two ways that happens here,
and both are tested below.

Their feed carries their prediction *and* the official answer in the same
field. Score the wrong states and you are grading DepartureVision against
itself, which comes back near 100% and is meaningless.

And their predictions are a step function sampled by a poller. Read a gap in
the polling as a gap in their predictions, and a night the collector was down
becomes a site that has nothing to say.

Their paywall is the third way, and it arrived after the first two were
already guarded. A withheld prediction has no track, so everything that reads
`track` sees a train they declined to predict -- and since only the confident
tiers are withheld, the site that comes out of that reading is one that goes
quiet exactly when it is sure. That is a *worse* number for them, which is the
direction that gets believed rather than checked.
"""

from __future__ import annotations

import json
import sys
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

# `scripts/` is a directory of tools rather than a package, so it is not
# importable without this. Kept here rather than in the tool so the tool stays
# a plain script anyone can run.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import collect_nypenn
from analyze_tracks import Observation, nypenn_model, score
from nypenn import TZ, accuracy_at, accuracy_by_tier, head_start, load, lookup

DEPARTURE = datetime(2026, 8, 12, 18, 30, tzinfo=TZ)


def change(
    minutes_before: float,
    track: str | None,
    source: str | None,
    *,
    train_id: str = "6675",
    top3: list[dict[str, Any]] | None = None,
    departure: datetime = DEPARTURE,
) -> dict[str, Any]:
    """Return one line of the change log, `minutes_before` the departure.

    The lock flag is computed by the collector's own function rather than
    written out here, so a test cannot describe a log the collector would
    never produce, and the two halves cannot drift apart silently.
    """
    record = {
        "type": "change",
        "t": int((departure - timedelta(minutes=minutes_before)).timestamp()),
        "train_id": train_id,
        "departure_time": int(departure.timestamp()),
        "line": "M&E",
        "destination": "Dover - SEC",
        "last_seen_on_track": None,
        "track": track,
        "track_source": source,
        "top3": top3,
    }
    return {**record, "withheld": collect_nypenn.withheld(record)}


def poll(minutes_before: float, departure: datetime = DEPARTURE) -> dict[str, Any]:
    """Return a heartbeat, which is what says the collector was awake."""
    return {
        "type": "poll",
        "t": int((departure - timedelta(minutes=minutes_before)).timestamp()),
        "rows": 20,
        "changes": 0,
    }


def write(tmp_path: Path, records: list[dict[str, Any]]) -> Path:
    """Write a change log and return its path."""
    path = tmp_path / "nypenn.jsonl"
    path.write_text(
        "".join(json.dumps(record) + "\n" for record in records), encoding="utf-8"
    )
    return path


def test_a_prediction_stands_until_it_is_replaced(tmp_path: Path) -> None:
    """The log records changes, so most instants have no line of their own.

    Storing every poll would be 57MB a day of near-identical boards. What is
    stored instead is a step function, and reading it as anything else means
    every question lands between two records and gets no answer.
    """
    log = write(
        tmp_path,
        [
            poll(40),
            change(40, "9", "low", top3=[{"track": "9", "pct": 31}]),
            poll(20),
            poll(15),
        ],
    )

    departures, _ = load(log)
    [departure] = departures

    standing = departure.prediction_at(DEPARTURE - timedelta(minutes=15))
    assert standing is not None, "the prediction from T-40 did not stand at T-15"
    assert standing.track == "9"


def test_the_posted_track_is_not_scored_as_a_prediction(tmp_path: Path) -> None:
    """The trap this whole file exists for.

    `track_source: confirmed` is the official board, not their model. Once it
    posts, their row carries the posted track -- so scoring whatever is
    standing would grade DepartureVision against itself and report a site
    that is right essentially always.
    """
    log = write(
        tmp_path,
        [
            poll(30),
            change(30, "9", "low", top3=[{"track": "9", "pct": 31}]),
            poll(8),
            change(8, "12", "confirmed"),
            poll(5),
        ],
    )

    departures, polls = load(log)
    [departure] = departures

    assert departure.truth == "12"
    assert departure.prediction_at(DEPARTURE - timedelta(minutes=5)) is None

    scored = accuracy_at(departures, polls, 5)
    assert scored.answered == 0, "the board's own answer was scored as their guess"

    early = accuracy_at(departures, polls, 30)
    assert early.answered == 1
    assert early.hits == 0, "they said 9, the board posted 12"


def test_a_gap_in_the_polling_is_not_a_silent_site(tmp_path: Path) -> None:
    """Silence has to be theirs, not ours.

    A collector that was stopped overnight and a site with nothing to say
    produce the same empty log. Counting the first as the second is how a
    restart turns into a headline number.
    """
    log = write(
        tmp_path,
        [
            poll(40),
            change(40, "9", "high"),
            poll(38),
            # Confirmed early, only so the departure has a truth to be scored
            # against at all. The collector then stops, and everything from
            # here to the departure is unobserved.
            change(37, "4", "confirmed"),
        ],
    )

    departures, polls = load(log)

    unwatched = accuracy_at(departures, polls, 10)
    assert unwatched.asked == 0, "an instant nobody observed was counted"

    watched = accuracy_at(departures, polls, 38)
    assert watched.asked == 1
    assert watched.answered == 1
    assert watched.hits == 0, "they said 9, the board posted 4"


def test_a_withheld_prediction_is_not_a_silent_site(tmp_path: Path) -> None:
    """Their paywall is not their model going quiet.

    Since September 2026 `high` and `medium` reach a caller without a session
    as the tier with the track stripped out. There is nothing to score, but
    counting it as a departure they declined to answer charges their answer
    rate for our lack of a subscription -- and because only the confident
    tiers are withheld, it is their best work that disappears.
    """
    log = write(
        tmp_path,
        [
            poll(30),
            change(30, None, "high"),
            poll(6),
            change(6, "12", "confirmed"),
            poll(5),
        ],
    )

    departures, polls = load(log)
    [departure] = departures

    assert departure.first_prediction is None, "there was no readable prediction"
    claim = departure.first_claim
    assert claim is not None, "they did predict this train, and said so at T-30"
    assert claim.withheld is True

    scored = accuracy_at(departures, polls, 30)
    assert scored.asked == 0, "a prediction we cannot read was counted as a refusal"
    assert scored.answered == 0
    assert scored.withheld == 1

    by_tier = accuracy_by_tier(departures)
    assert by_tier["high"].answered == 0
    assert by_tier["high"].withheld == 1, "the withheld prediction vanished entirely"


def test_the_lock_survives_a_log_written_before_it_existed(tmp_path: Path) -> None:
    """Logs collected before the paywall have no flag, and needed none.

    Read by the same rule -- a tier with no track -- they say exactly what
    they always said, because that shape did not occur while every tier was
    still sent in full. A reader that treated the missing field as "withheld"
    or refused to read the file at all would rewrite months of collection.
    """
    departure = int(DEPARTURE.timestamp())
    log = write(
        tmp_path,
        [
            poll(30),
            {
                "type": "change",
                "t": departure - 1800,
                "train_id": "6675",
                "departure_time": departure,
                "line": "M&E",
                "destination": "Dover - SEC",
                "last_seen_on_track": None,
                "track": "9",
                "track_source": "high",
                "top3": None,
            },
            poll(4),
            change(4, "9", "confirmed"),
        ],
    )

    departures, polls = load(log)
    [record] = departures

    first = record.first_prediction
    assert first is not None
    assert first.withheld is False

    scored = accuracy_at(departures, polls, 30)
    assert scored == (1, 1, 1, 1, 0), "an old log stopped scoring the way it did"


def test_what_the_collector_saw_outranks_what_the_shape_suggests(
    tmp_path: Path,
) -> None:
    """The reason the flag is written down rather than worked out later.

    A lock is something the site does, not a shape its payload has. Today it
    withholds by sending the tier with no track, so the flag and the shape
    agree and nothing here depends on which is read. The day they withhold by
    sending a decoy track instead, the shape says "a prediction they made" and
    the log still says what the poller actually saw -- and a log that is only
    believed while it agrees with a rule is not evidence of anything.
    """
    departure = int(DEPARTURE.timestamp())
    log = write(
        tmp_path,
        [
            poll(30),
            {
                "type": "change",
                "t": departure - 1800,
                "train_id": "6675",
                "departure_time": departure,
                "line": "M&E",
                "destination": "Dover - SEC",
                "last_seen_on_track": None,
                "track": "1",
                "track_source": "high",
                "top3": None,
                "withheld": True,
            },
            poll(4),
            change(4, "1", "confirmed"),
        ],
    )

    departures, polls = load(log)
    [record] = departures

    assert record.first_prediction is None, "a track they never showed us was scored"
    assert record.prediction_at(DEPARTURE - timedelta(minutes=30)) is None

    scored = accuracy_at(departures, polls, 30)
    assert scored.answered == 0
    assert scored.hits == 0, "they were credited with a prediction we never saw"
    assert scored.withheld == 1


def test_the_paywall_does_not_erase_the_prediction_it_replaced(
    tmp_path: Path,
) -> None:
    """A train can be readable at T-40 and locked by T-20.

    Watched live on 2026-09-01: train 3511 stood at `low` with a full `top3`,
    and a minute later the same train was `high` with no track. Their model
    got more sure, and that is exactly when the answer goes behind the wall.

    So the lock is a state of an instant, not of a departure. What they said
    while they were still showing us counts, and it counts under the tier they
    said it at -- and the same departure must not then be counted a second
    time as one they withheld.
    """
    log = write(
        tmp_path,
        [
            poll(40),
            change(40, "9", "low", top3=[{"track": "9", "pct": 31}]),
            poll(30),
            poll(20),
            change(20, None, "high"),
            poll(15),
            poll(4),
            change(4, "9", "confirmed"),
        ],
    )

    departures, polls = load(log)

    early = accuracy_at(departures, polls, 30)
    assert early.asked == 1
    assert early.answered == 1
    assert early.hits == 1, "the prediction they showed us was thrown away"
    assert early.withheld == 0

    late = accuracy_at(departures, polls, 15)
    assert late.asked == 0, "the locked state was scored as a refusal"
    assert late.withheld == 1

    by_tier = accuracy_by_tier(departures)
    assert by_tier["low"].answered == 1, "their readable guess was not scored"
    assert by_tier["high"].withheld == 0, "the same departure was counted twice"


def test_their_ranking_is_not_padded_out_to_three(tmp_path: Path) -> None:
    """`top3` is only sent on the unsure tiers.

    A confident prediction is one track and no runners-up. Inventing a second
    and third choice would hand them a top-3 number they never claimed, which
    is exactly the kind of flattery that makes a comparison useless.
    """
    log = write(
        tmp_path,
        [poll(30), change(30, "9", "high"), poll(2), change(2, "4", "confirmed")],
    )

    departures, _ = load(log)
    [departure] = departures
    first = departure.first_prediction

    assert first is not None
    assert first.ranked == ["9"]

    by_tier = accuracy_by_tier(departures)
    assert by_tier["high"].answered == 1
    assert by_tier["high"].top3 == 0, "a single guess was credited with a top-3 hit"


def test_the_track_that_stuck_is_the_truth(tmp_path: Path) -> None:
    """Penn moves trains after posting, and the last one is the one you catch."""
    log = write(
        tmp_path,
        [
            poll(20),
            change(20, "7", "confirmed"),
            poll(6),
            change(6, "13", "confirmed"),
        ],
    )

    departures, _ = load(log)
    [departure] = departures

    assert departure.truth == "13"
    assert departure.reassigned is True


def test_the_head_start_is_measured_against_the_official_board(
    tmp_path: Path,
) -> None:
    """The entire case for predicting at a terminal.

    Penn publishes nothing until roughly T-10. A prediction that lands at T-9
    is not worth the code that produced it, however often it is right, so the
    gap is reported next to the accuracy rather than left implied.
    """
    log = write(
        tmp_path,
        [
            poll(38),
            change(38, "9", "low", top3=[{"track": "9", "pct": 31}]),
            poll(9),
            change(9, "9", "confirmed"),
        ],
    )

    departures, _ = load(log)

    assert head_start(departures) == [29]


def test_their_prediction_is_scored_by_our_own_harness(tmp_path: Path) -> None:
    """The comparison is only fair if both sides face the same grader.

    `nypenn_model` puts their answer through the leave-one-day-out loop in
    `analyze_tracks`, so their number and ours come out of the same function
    on the same departures. It also has to behave when they said nothing: an
    unanswered target is not a wrong one, and the `answered` column is where
    staying quiet is supposed to show.
    """
    log = write(
        tmp_path,
        [poll(30), change(30, "9", "high"), poll(2), change(2, "9", "confirmed")],
    )
    departures, _ = load(log)
    answers = lookup(departures, 30)
    assert answers == {(date(2026, 8, 12), "6675"): ["9"]}

    def observation(day: date, train_id: str, track: str) -> Observation:
        return Observation(
            station="New York Penn Station",
            day=day,
            train_id=train_id,
            track=track,
            # Naive local, which is what `analyze_tracks` builds from a
            # recorded "HH:MM" and the day it was recorded on.
            scheduled=datetime(day.year, day.month, day.day, 18, 30),  # noqa: DTZ001
            line="M&E",
            assigned_at=None,
            reassigned=False,
            delay_at_assignment=None,
            final_status=None,
            final_delay=None,
            worst_delay=None,
        )

    observations = [
        observation(date(2026, 8, 12), "6675", "9"),
        observation(date(2026, 8, 11), "6675", "9"),
        # A departure they never spoke about, on a day they did not cover.
        observation(date(2026, 8, 11), "3889", "4"),
    ]

    scores = score(observations, {"n1 nypenn.live": nypenn_model(answers)})

    assert scores is not None
    result = scores["n1 nypenn.live"]
    assert result.total == 3, "every departure should have been put to them"
    assert result.answered == 1, "they only ever predicted one of these"
    assert result.hits == 1

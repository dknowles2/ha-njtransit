import type { CarDetail, Departure } from "./types.js";

/**
 * How a pill reads, not what it says.
 *
 * The YAML dashboard had no way to name these: a markdown card cannot carry a
 * class, so the colour was chosen by how the markdown nested around the
 * backticks -- `**\`Cancelled\`**` was red because `strong > code` was red,
 * and `~~\`12 min late\`~~` was amber with the strikethrough undone in CSS.
 * It worked, it needed a paragraph of the file header to explain, and it
 * needed card-mod to reach into a shadow root to apply any of it.
 */
export type Tone = "accent" | "bad" | "warn" | "muted";

export interface Pill {
  text: string;
  tone: Tone;
}

/**
 * Inside this many minutes, a missing track is a deviation rather than a wait.
 *
 * New York Penn posts NJ Transit tracks a median of 8.8 minutes before
 * departure, interquartile range 1.9 minutes (n=236). This must stay equal to
 * the integration's `TRACK_OVERDUE_LEAD`, which is what fires the event this
 * pill illustrates; `tests/test_card_constants.py` fails if the two drift.
 */
export const TRACK_OVERDUE_MINUTES = 6;

/** Once a track is normally posted, so "not yet" stops being reassuring. */
const TRACK_DUE_MINUTES = 10;

/** Beyond this there is no useful estimate left to give. */
const TRACK_ESTIMATE_HORIZON = 25;

/**
 * What to say about a platform that has not been announced.
 *
 * `posting` is the whole subtlety: without it every train at a quiet station
 * reads as overdue all night.
 */
export function trackPill(
  departure: Departure,
  minutes: number,
  posting: boolean,
): Pill | null {
  if (departure.track) {
    return { text: `Track ${departure.track}`, tone: "accent" };
  }
  // A cancelled train is never getting a platform, so "Track not posted yet"
  // is not a fact about it -- and next to a red `Cancelled` it reads as a
  // second, contradictory piece of news. The YAML card said exactly this.
  if (departure.status === "cancelled") {
    return null;
  }
  if (!posting) {
    return { text: "Track not posted", tone: "muted" };
  }
  if (minutes <= TRACK_OVERDUE_MINUTES) {
    return { text: "⚠️ Track overdue", tone: "bad" };
  }
  if (minutes <= TRACK_DUE_MINUTES) {
    return { text: "Track due any minute", tone: "muted" };
  }
  if (minutes <= TRACK_ESTIMATE_HORIZON) {
    const away = minutes - (TRACK_DUE_MINUTES - 1);
    return { text: `Track due in ~${away} min`, tone: "muted" };
  }
  return { text: "Track not posted yet", tone: "muted" };
}

/** The compact track cell for the board table. */
export function trackCell(
  departure: Departure,
  minutes: number,
  posting: boolean,
): Pill {
  if (departure.track) {
    return { text: departure.track, tone: "accent" };
  }
  if (
    posting &&
    minutes <= TRACK_OVERDUE_MINUTES &&
    departure.status !== "cancelled"
  ) {
    return { text: "⚠️", tone: "bad" };
  }
  return { text: "—", tone: "muted" };
}

/** The operator's own words, coloured by how bad they are. */
export function statusPill(departure: Departure): Pill | null {
  if (!departure.statusText) {
    return null;
  }
  if (departure.status === "cancelled") {
    return { text: departure.statusText, tone: "bad" };
  }
  if (departure.delayMinutes) {
    return { text: departure.statusText, tone: "warn" };
  }
  return { text: departure.statusText, tone: "muted" };
}

/** Only worth a pill when it would change where you stand. */
export function crowdingPill(departure: Departure): Pill | null {
  if (departure.crowding === "heavy") {
    return { text: "Busy", tone: "warn" };
  }
  if (departure.crowding === "moderate") {
    return { text: "Filling up", tone: "muted" };
  }
  return null;
}

const RANK: Record<string, number> = { light: 0, moderate: 1, heavy: 2 };

/**
 * Where to stand, and only when standing somewhere else would help.
 *
 * "Front light · Middle light · Back light" is three facts and no decision,
 * so an even train says nothing at all.
 */
export function emptiestCars(cars: CarDetail[]): string | null {
  let best: string | null = null;
  let low = Infinity;
  let high = -Infinity;

  for (const car of cars) {
    const rank = car.crowding ? RANK[car.crowding] : undefined;
    if (rank === undefined || !car.position) {
      continue;
    }
    if (rank < low) {
      low = rank;
      best = car.position;
    }
    high = Math.max(high, rank);
  }

  return best !== null && high > low ? best : null;
}

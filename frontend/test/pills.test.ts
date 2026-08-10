/**
 * What the card says about a platform, a delay and a crowd.
 *
 * In the YAML dashboard the *colour* of these was chosen by how the markdown
 * nested around the backticks, which meant the decision and its presentation
 * were the same expression and neither could be tested. Here the decision
 * returns a tone and the stylesheet reads it.
 */
import { describe, expect, it } from "vitest";

import {
  TRACK_OVERDUE_MINUTES,
  crowdingPill,
  emptiestCars,
  statusPill,
  trackCell,
  trackPill,
} from "../src/pills.js";
import type { Departure } from "../src/types.js";

function train(overrides: Partial<Departure> = {}): Departure {
  return {
    entityId: "sensor.next_departure",
    scheduled: new Date("2026-08-09T22:31:00.000Z"),
    trainId: "6613",
    favorite: false,
    track: null,
    status: "on_time",
    statusText: null,
    delayMinutes: null,
    crowding: null,
    cars: [],
    alerts: [],
    ...overrides,
  };
}

describe("the track pill", () => {
  it("says the track once there is one", () => {
    expect(trackPill(train({ track: "4" }), 20, true)).toEqual({
      text: "Track 4",
      tone: "accent",
    });
  });

  it("stays quiet when the station is posting no tracks at all", () => {
    // Otherwise every train at a quiet station reads as overdue all evening.
    // A station that posts nothing tells you nothing.
    expect(trackPill(train(), 2, false)).toEqual({
      text: "Track not posted",
      tone: "muted",
    });
  });

  it("calls a missing track overdue once inside the threshold", () => {
    const pill = trackPill(train(), TRACK_OVERDUE_MINUTES, true);

    expect(pill?.text).toContain("overdue");
    expect(pill?.tone).toBe("bad");
  });

  it("is merely informational a minute outside it", () => {
    // Pins the comparison direction, which is the only thing separating
    // "your platform is late" from "wait a moment".
    const pill = trackPill(train(), TRACK_OVERDUE_MINUTES + 1, true);

    expect(pill?.tone).toBe("muted");
    expect(pill?.text).not.toContain("overdue");
  });

  it("estimates against the median posting time", () => {
    // 8.8 minutes median at New York Penn, so a train 20 minutes out has
    // about 11 to wait.
    expect(trackPill(train(), 20, true)?.text).toBe("Track due in ~11 min");
  });

  it("declines to estimate beyond the horizon", () => {
    expect(trackPill(train(), 40, true)?.text).toBe("Track not posted yet");
  });

  it("says nothing about the platform of a cancelled train", () => {
    // Found by looking at it. `Track not posted yet` sat next to a red
    // `Cancelled`, which reads as two pieces of news when the first is not a
    // fact about that train at all -- it is never getting a platform. The
    // YAML card this replaces says exactly the same thing.
    const scrapped = train({ status: "cancelled", statusText: "Cancelled" });

    expect(trackPill(scrapped, 20, true)).toBeNull();
  });

  it("still shows a platform a cancelled train had been given", () => {
    // Cancelled after the track was posted is a different situation: people
    // are already standing on it.
    const scrapped = train({ status: "cancelled", track: "4" });

    expect(trackPill(scrapped, 2, true)?.text).toBe("Track 4");
  });
});

describe("the board's track cell", () => {
  it("carries the bare number", () => {
    expect(trackCell(train({ track: "4" }), 20, true).text).toBe("4");
  });

  it("warns when the track is overdue", () => {
    expect(trackCell(train(), 1, true)).toEqual({ text: "⚠️", tone: "bad" });
  });

  it("shows a dash rather than a warning when nothing is posted", () => {
    expect(trackCell(train(), 1, false).text).toBe("—");
  });

  it("does not call a cancelled train's missing track overdue", () => {
    const scrapped = train({ status: "cancelled" });

    expect(trackCell(scrapped, 1, true).text).toBe("—");
  });
});

describe("the status pill", () => {
  it("says nothing when the operator does not", () => {
    expect(statusPill(train())).toBeNull();
  });

  it("reads a cancellation as bad", () => {
    const pill = statusPill(
      train({ status: "cancelled", statusText: "Cancelled" }),
    );

    expect(pill).toEqual({ text: "Cancelled", tone: "bad" });
  });

  it("reads a delay as degraded rather than broken", () => {
    const pill = statusPill(
      train({ statusText: "12 min late", delayMinutes: 12 }),
    );

    expect(pill).toEqual({ text: "12 min late", tone: "warn" });
  });

  it("carries the real delay, never a rounded one", () => {
    // The disruption blueprint buckets delays to decide whether to speak.
    // That is a comparison device and it must never reach the wording.
    expect(
      statusPill(train({ statusText: "12 min late", delayMinutes: 12 }))?.text,
    ).toBe("12 min late");
  });
});

describe("the crowding pill", () => {
  it("stays silent on a train that is not full", () => {
    expect(crowdingPill(train({ crowding: "light" }))).toBeNull();
    expect(crowdingPill(train({ crowding: null }))).toBeNull();
  });

  it("speaks up once it is worth knowing", () => {
    expect(crowdingPill(train({ crowding: "heavy" }))?.text).toBe("Busy");
    expect(crowdingPill(train({ crowding: "moderate" }))?.text).toBe(
      "Filling up",
    );
  });
});

describe("where to stand", () => {
  it("names the emptier end", () => {
    const cars = [
      { position: "front", crowding: "light" },
      { position: "back", crowding: "heavy" },
    ];

    expect(emptiestCars(cars)).toBe("front");
  });

  it("says nothing about an evenly loaded train", () => {
    // Three cars all reading "light" is three facts and no decision.
    const cars = [
      { position: "front", crowding: "light" },
      { position: "middle", crowding: "light" },
      { position: "back", crowding: "light" },
    ];

    expect(emptiestCars(cars)).toBeNull();
  });

  it("ignores cars with no position to stand at", () => {
    const cars = [
      { position: null, crowding: "light" },
      { position: "back", crowding: "heavy" },
    ];

    expect(emptiestCars(cars)).toBeNull();
  });

  it("says nothing when no crowding was reported", () => {
    expect(emptiestCars([{ position: "front", crowding: null }])).toBeNull();
  });
});

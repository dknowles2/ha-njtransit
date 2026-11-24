/** Clock and countdown wording. */
import { describe, expect, it } from "vitest";

import {
  countdown,
  effectiveDeparture,
  formatClock,
  formatShortClock,
  minutesUntil,
} from "../src/format.js";
import type { Departure } from "../src/types.js";

const NOW = new Date("2026-08-09T22:00:00.000Z");

describe("minutes until", () => {
  it("rounds to the nearest whole minute", () => {
    expect(minutesUntil(new Date("2026-08-09T22:31:29.000Z"), NOW)).toBe(31);
    expect(minutesUntil(new Date("2026-08-09T22:31:31.000Z"), NOW)).toBe(32);
  });

  it("goes negative once the train has gone", () => {
    expect(minutesUntil(new Date("2026-08-09T21:58:00.000Z"), NOW)).toBe(-2);
  });
});

describe("the clock", () => {
  it("writes the time the way the departure board does", () => {
    // Twelve-hour, no leading zero. A card reading `18:31` next to a platform
    // sign reading `6:31 PM` is a small thing to translate while running.
    expect(formatClock(new Date("2026-08-09T22:31:00.000Z"))).toBe("6:31 PM");
  });

  it("drops the meridiem for the board column", () => {
    expect(formatShortClock(new Date("2026-08-09T22:31:00.000Z"))).toBe("6:31");
  });
});

describe("the countdown", () => {
  it("carries a unit only when it is counting", () => {
    expect(countdown(12)).toEqual({ value: "12", unit: true });
    expect(countdown(0)).toEqual({ value: "Now", unit: false });
    expect(countdown(-1)).toEqual({ value: "Departed", unit: false });
  });
});

describe("effective departure time", () => {
  function train(overrides: Partial<Departure> = {}): Departure {
    return {
      entityId: "sensor.next_departure",
      scheduled: new Date("2026-08-09T22:31:00.000Z"),
      trainId: "6613",
      favorite: false,
      track: null,
      signaledTrack: null,
      status: "on_time",
      statusText: null,
      delayMinutes: null,
      crowding: null,
      cars: [],
      alerts: [],
      ...overrides,
    };
  }

  it("shifts the scheduled time forward by the reported delay", () => {
    const delayed = train({ status: "delayed", delayMinutes: 15 });

    expect(effectiveDeparture(delayed)).toEqual(
      new Date("2026-08-09T22:46:00.000Z"),
    );
  });

  it("does not shift once the train has departed", () => {
    const gone = train({ status: "departed", delayMinutes: 15 });

    expect(effectiveDeparture(gone)).toEqual(gone.scheduled);
  });

  it("leaves on-time and zero-delay trains untouched", () => {
    const onTime = train({ delayMinutes: 0 });
    const unknown = train({ delayMinutes: null });

    expect(effectiveDeparture(onTime)).toEqual(onTime.scheduled);
    expect(effectiveDeparture(unknown)).toEqual(unknown.scheduled);
  });
});

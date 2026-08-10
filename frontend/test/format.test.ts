/** Clock and countdown wording. */
import { describe, expect, it } from "vitest";

import { countdown, formatClock, formatShortClock, minutesUntil } from "../src/format.js";

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

/**
 * Finding a commute's entities, and choosing which train the card is about.
 *
 * The YAML dashboard did the first of these by pasting a slugified name into
 * every card, and got the second wrong in the way that mattered: it took the
 * first row on the board without asking whether that train was running, so a
 * line suspension put a five-centimetre countdown to a cancelled service at
 * the top of the page.
 */
import { describe, expect, it } from "vitest";

import {
  isPostingTracks,
  pickHero,
  readBoard,
  readDeparture,
  resolveCommute,
} from "../src/commute.js";
import { PREFIX, departure, empty, fakeHass } from "./fixtures.js";

const AT_631 = "2026-08-09T22:31:00.000Z";
const AT_651 = "2026-08-09T22:51:00.000Z";

describe("finding the rest of the commute", () => {
  it("reads the numbered departures off the registry", () => {
    const hass = fakeHass(
      [
        departure(`${PREFIX}_next_departure`, AT_631),
        departure(`${PREFIX}_departure_2`, AT_651),
        empty(`${PREFIX}_next_favorite`),
        empty(`${PREFIX}_stops_away`),
      ],
      { registry: true },
    );

    const commute = resolveCommute(hass, `${PREFIX}_next_departure`);

    expect(commute.departures).toEqual([
      `${PREFIX}_next_departure`,
      `${PREFIX}_departure_2`,
    ]);
    expect(commute.favorite).toBe(`${PREFIX}_next_favorite`);
    expect(commute.progress).toBe(`${PREFIX}_stops_away`);
  });

  it("survives entities being renamed", () => {
    // The whole reason to ask the registry. Renaming any of these in the UI
    // silently emptied the YAML dashboard, because every card rebuilt the
    // ids from a pasted commute name.
    const hass = fakeHass(
      [
        departure("sensor.my_train", AT_631),
        departure("sensor.the_one_after", AT_651),
        empty("sensor.the_usual"),
      ],
      {
        registry: true,
        keys: {
          "sensor.my_train": "next_departure",
          "sensor.the_one_after": "departure",
          "sensor.the_usual": "next_favorite",
        },
      },
    );

    const commute = resolveCommute(hass, "sensor.my_train");

    expect(commute.departures).toEqual([
      "sensor.my_train",
      "sensor.the_one_after",
    ]);
    expect(commute.favorite).toBe("sensor.the_usual");
  });

  it("orders the tenth departure after the second", () => {
    // Sorted as text, `_departure_10` comes before `_departure_2`, so the
    // board would list the last train of the batch second. The integration
    // allows up to ten.
    const ids = [2, 3, 10].map((n) => `${PREFIX}_departure_${n}`);
    const hass = fakeHass(
      [
        departure(`${PREFIX}_next_departure`, AT_631),
        ...ids.map((id) => departure(id, AT_651)),
      ],
      { registry: true },
    );

    expect(resolveCommute(hass, `${PREFIX}_next_departure`).departures).toEqual([
      `${PREFIX}_next_departure`,
      `${PREFIX}_departure_2`,
      `${PREFIX}_departure_3`,
      `${PREFIX}_departure_10`,
    ]);
  });

  it("falls back to the entity ids when there is no registry entry", () => {
    const hass = fakeHass([
      departure(`${PREFIX}_next_departure`, AT_631),
      departure(`${PREFIX}_departure_2`, AT_651),
      empty(`${PREFIX}_next_favorite`),
    ]);

    const commute = resolveCommute(hass, `${PREFIX}_next_departure`);

    expect(commute.departures).toEqual([
      `${PREFIX}_next_departure`,
      `${PREFIX}_departure_2`,
    ]);
    expect(commute.favorite).toBe(`${PREFIX}_next_favorite`);
  });

  it("prefers the entity ids when the registry can only see one train", () => {
    // A registry entry with no translation key -- an entity customised before
    // the integration set them -- resolves to a device that reports a single
    // departure. Trusting it would quietly drop the rest of the board, so the
    // longer answer wins.
    const hass = fakeHass(
      [
        departure(`${PREFIX}_next_departure`, AT_631),
        departure(`${PREFIX}_departure_2`, AT_651),
      ],
      { registry: true, keys: { [`${PREFIX}_departure_2`]: "" } },
    );

    expect(resolveCommute(hass, `${PREFIX}_next_departure`).departures).toEqual([
      `${PREFIX}_next_departure`,
      `${PREFIX}_departure_2`,
    ]);
  });
});

describe("reading a departure", () => {
  it("reports nothing for a sensor with no train", () => {
    const hass = fakeHass([empty(`${PREFIX}_next_departure`)]);

    expect(readDeparture(hass, `${PREFIX}_next_departure`)).toBeNull();
  });

  it("drops the empty sensors from the board", () => {
    const hass = fakeHass([
      departure(`${PREFIX}_next_departure`, AT_631),
      empty(`${PREFIX}_departure_2`),
    ]);
    const commute = resolveCommute(hass, `${PREFIX}_next_departure`);

    expect(readBoard(hass, commute)).toHaveLength(1);
  });

  it("keeps a track only when it is a real one", () => {
    const hass = fakeHass([
      departure(`${PREFIX}_next_departure`, AT_631, { track: "4" }),
      departure(`${PREFIX}_departure_2`, AT_651, { track: null }),
    ]);
    const board = readBoard(hass, resolveCommute(hass, `${PREFIX}_next_departure`));

    expect(board[0].track).toBe("4");
    expect(board[1].track).toBeNull();
    expect(isPostingTracks(board)).toBe(true);
  });

  it("knows a station posting nothing from one posting some", () => {
    const hass = fakeHass([departure(`${PREFIX}_next_departure`, AT_631)]);
    const board = readBoard(hass, resolveCommute(hass, `${PREFIX}_next_departure`));

    expect(isPostingTracks(board)).toBe(false);
  });
});

describe("choosing the train the card counts down to", () => {
  const cancelled = (id: string, at: string) =>
    departure(id, at, { status: "cancelled", status_text: "Cancelled" });

  it("skips a cancelled train when falling back to the next one out", () => {
    const hass = fakeHass([
      cancelled(`${PREFIX}_next_departure`, AT_631),
      departure(`${PREFIX}_departure_2`, AT_651),
    ]);
    const board = readBoard(hass, resolveCommute(hass, `${PREFIX}_next_departure`));

    const { departure: hero, allCancelled } = pickHero(null, board);

    expect(hero?.entityId).toBe(`${PREFIX}_departure_2`);
    expect(allCancelled).toBe(false);
  });

  it("still shows the favourite when the favourite is cancelled", () => {
    // Deliberately not filtered: if the train you were going to catch is
    // cancelled, that is the single most important thing the card can say.
    const hass = fakeHass([
      cancelled(`${PREFIX}_next_favorite`, AT_631),
      departure(`${PREFIX}_next_departure`, AT_651),
    ]);
    const favorite = readDeparture(hass, `${PREFIX}_next_favorite`);
    const board = readBoard(hass, resolveCommute(hass, `${PREFIX}_next_departure`));

    const { departure: hero, tracking } = pickHero(favorite, board);

    expect(hero?.entityId).toBe(`${PREFIX}_next_favorite`);
    expect(tracking).toBe(true);
  });

  it("says every departure is cancelled rather than nothing is scheduled", () => {
    // Two different pieces of news, and the second reads as "check back
    // later" when the truth is "find another way in".
    const hass = fakeHass([
      cancelled(`${PREFIX}_next_departure`, AT_631),
      cancelled(`${PREFIX}_departure_2`, AT_651),
    ]);
    const board = readBoard(hass, resolveCommute(hass, `${PREFIX}_next_departure`));

    const { departure: hero, allCancelled } = pickHero(null, board);

    expect(hero).toBeNull();
    expect(allCancelled).toBe(true);
  });

  it("does not claim cancellations when the board is simply empty", () => {
    expect(pickHero(null, []).allCancelled).toBe(false);
  });
});

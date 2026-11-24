/**
 * The card itself, rendered into a document.
 *
 * This is the half that had no test at all before. The YAML version could
 * only be checked by lifting its markdown out and running it through Home
 * Assistant's template engine, which said nothing about what the browser did
 * with the result.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import "../src/departures-card.js";
import "../src/editor.js";
import type { NJTransitDeparturesCard } from "../src/departures-card.js";
import type { NJTransitDeparturesEditor } from "../src/editor.js";
import type { HomeAssistant } from "../src/types.js";
import { PREFIX, departure, empty, fakeHass, unavailable } from "./fixtures.js";

const NOW = new Date("2026-08-09T22:00:00.000Z");
const IN_31_MINUTES = "2026-08-09T22:31:00.000Z";
const IN_51_MINUTES = "2026-08-09T22:51:00.000Z";

async function mount(hass: HomeAssistant): Promise<NJTransitDeparturesCard> {
  const card = document.createElement(
    "njtransit-departures",
  ) as NJTransitDeparturesCard;
  card.setConfig({
    type: "custom:njtransit-departures",
    entity: `${PREFIX}_next_departure`,
  });
  card.hass = hass;
  document.body.append(card);
  await card.updateComplete;
  return card;
}

function text(card: NJTransitDeparturesCard, selector: string): string {
  return card.shadowRoot?.querySelector(selector)?.textContent?.trim() ?? "";
}

function pills(card: NJTransitDeparturesCard): string[] {
  return [...(card.shadowRoot?.querySelectorAll(".pills .pill") ?? [])].map(
    (pill) => pill.textContent?.trim() ?? "",
  );
}

beforeEach(() => {
  vi.useFakeTimers();
  vi.setSystemTime(NOW);
});

afterEach(() => {
  vi.useRealTimers();
  document.body.replaceChildren();
});

describe("configuration", () => {
  it("renders a setup prompt instead of throwing when entity is empty", async () => {
    const card = document.createElement(
      "njtransit-departures",
    ) as NJTransitDeparturesCard;
    card.setConfig({ type: "custom:njtransit-departures", entity: "" });
    card.hass = fakeHass([]);
    document.body.append(card);
    await card.updateComplete;

    expect(text(card, ".hero.empty h2")).toBe("Select a commute");
    expect(card.shadowRoot?.querySelector("ha-card")?.className.trim()).toBe(
      "muted",
    );
  });

  it("editor filters entity picker to timestamp sensors", async () => {
    const editor = document.createElement(
      "njtransit-departures-editor",
    ) as NJTransitDeparturesEditor;
    editor.setConfig({ type: "custom:njtransit-departures", entity: "" });
    editor.hass = fakeHass([]);
    document.body.append(editor);
    await editor.updateComplete;

    const form = editor.shadowRoot?.querySelector("ha-form") as unknown as {
      schema: Array<{
        name: string;
        selector: { entity?: Record<string, string> };
      }>;
    };
    const entityField = form.schema.find((field) => field.name === "entity");
    expect(entityField?.selector.entity).toEqual({
      integration: "njtransit",
      domain: "sensor",
      device_class: "timestamp",
    });
  });
});

describe("the hero", () => {
  it("counts down in whole minutes", async () => {
    const card = await mount(
      fakeHass([departure(`${PREFIX}_next_departure`, IN_31_MINUTES)]),
    );

    expect(text(card, ".countdown")).toBe("31min");
    expect(text(card, "h2")).toContain("6:31 PM");
    expect(text(card, "h2")).toContain("Train 6613");
  });

  it("says Now rather than 0 at the departure minute", async () => {
    const card = await mount(
      fakeHass([departure(`${PREFIX}_next_departure`, NOW.toISOString())]),
    );

    expect(text(card, ".countdown")).toBe("Now");
  });

  it("re-counts without any entity changing", async () => {
    // The thing a markdown card could not do. Its content only re-rendered
    // when some entity updated, so the countdown was stale for as long as the
    // integration's poll interval -- up to a couple of minutes wrong on the
    // number the whole card exists to show.
    const card = await mount(
      fakeHass([departure(`${PREFIX}_next_departure`, IN_31_MINUTES)]),
    );
    expect(text(card, ".countdown")).toBe("31min");

    vi.advanceTimersByTime(120_000);
    await card.updateComplete;

    expect(text(card, ".countdown")).toBe("29min");
  });

  it("stops counting once it is off the platform", async () => {
    const card = await mount(
      fakeHass([departure(`${PREFIX}_next_departure`, IN_31_MINUTES)]),
    );

    vi.advanceTimersByTime(35 * 60_000);
    await card.updateComplete;

    expect(text(card, ".countdown")).toBe("Departed");
  });

  it("keeps counting down for a delayed train past its scheduled time", async () => {
    // Scheduled 5 minutes ago, running 15 minutes late -> still 10 minutes out.
    const pastScheduled = "2026-08-09T21:55:00.000Z";
    const card = await mount(
      fakeHass([
        departure(`${PREFIX}_next_departure`, pastScheduled, {
          status: "delayed",
          status_text: "15 min late",
          delay_minutes: 15,
        }),
      ]),
    );

    expect(text(card, ".countdown")).toBe("10min");
    // Subtitle keeps the scheduled clock time so riders match the station sign.
    expect(text(card, "h2 .clock")).toBe("5:55 PM");
    expect(text(card, "tbody tr .relative")).toBe("10m");
  });

  it("shows Departed when a delayed train is marked departed", async () => {
    const pastScheduled = "2026-08-09T21:55:00.000Z";
    const card = await mount(
      fakeHass([
        departure(`${PREFIX}_next_departure`, pastScheduled, {
          status: "departed",
          status_text: "Departed",
          delay_minutes: 15,
        }),
      ]),
    );

    expect(text(card, ".countdown")).toBe("Departed");
  });

  it("shows Cancelled as the hero headline when the favourite is cancelled", async () => {
    const card = await mount(
      fakeHass([
        departure(`${PREFIX}_next_departure`, IN_31_MINUTES, {
          train_id: "6611",
        }),
        departure(`${PREFIX}_next_favorite`, IN_51_MINUTES, {
          train_id: "6615",
          status: "cancelled",
          status_text: "Cancelled",
        }),
      ]),
    );

    expect(text(card, ".countdown")).toBe("Cancelled");
    expect(card.shadowRoot?.querySelector(".countdown .unit")).toBeNull();
  });

  it("never counts down to a cancelled train", async () => {
    // Observed during a line suspension: a five-centimetre countdown to a
    // train that was not running, with "Cancelled" underneath it in
    // eight-point type.
    const card = await mount(
      fakeHass([
        departure(`${PREFIX}_next_departure`, IN_31_MINUTES, {
          status: "cancelled",
          status_text: "Cancelled",
        }),
        departure(`${PREFIX}_departure_2`, IN_51_MINUTES, { train_id: "6615" }),
      ]),
    );

    expect(text(card, ".countdown")).toBe("51min");
    expect(text(card, "h2")).toContain("Train 6615");
  });

  it("distinguishes a suspended line from a quiet one", async () => {
    const card = await mount(
      fakeHass([
        departure(`${PREFIX}_next_departure`, IN_31_MINUTES, {
          status: "cancelled",
        }),
      ]),
    );

    expect(text(card, ".hero.empty")).toContain("Every upcoming departure");
  });

  it("says so when there is simply no train", async () => {
    const card = await mount(fakeHass([empty(`${PREFIX}_next_departure`)]));

    expect(text(card, ".hero.empty h2")).toBe("Nothing on the board");
    expect(card.shadowRoot?.querySelector(".hero.empty h3")).toBeNull();
    expect(text(card, ".hero.empty p")).toContain("next couple of hours");
  });

  it("distinguishes an unavailable feed from an empty board", async () => {
    const card = await mount(
      fakeHass([unavailable(`${PREFIX}_next_departure`)]),
    );

    expect(text(card, ".hero.empty h2")).toBe("Departure board unavailable");
    expect(card.shadowRoot?.querySelector("ha-card")?.className.trim()).toBe(
      "warn",
    );
  });

  it("distinguishes a missing entity from an empty board", async () => {
    const card = await mount(fakeHass([]));

    expect(text(card, ".hero.empty h2")).toBe("Entity not found");
    expect(card.shadowRoot?.querySelector("ha-card")?.className.trim()).toBe(
      "warn",
    );
  });

  it("keeps alerts outside the hero button role and uses role=note", async () => {
    const card = await mount(
      fakeHass([
        departure(`${PREFIX}_next_departure`, IN_31_MINUTES, {
          alerts: ["Switch problem near Newark."],
        }),
      ]),
    );

    const trigger = card.shadowRoot?.querySelector(".hero-main");
    const alert = card.shadowRoot?.querySelector('.alert[role="note"]');
    expect(trigger?.getAttribute("role")).toBe("button");
    expect(trigger?.getAttribute("aria-label")).toContain("Train 6613");
    expect(alert?.textContent?.trim()).toBe("Switch problem near Newark.");
    expect(trigger?.contains(alert ?? null)).toBe(false);
    expect(card.shadowRoot?.querySelector("blockquote")).toBeNull();
  });
});

describe("the pills", () => {
  it("shows the signaled platform as likely until the board posts", async () => {
    const card = await mount(
      fakeHass([
        departure(`${PREFIX}_next_departure`, IN_31_MINUTES, {
          signaled_track: "7",
        }),
      ]),
    );

    expect(pills(card)).toContain("Track 7 (likely)");
  });

  it("shows the track once it is posted", async () => {
    const card = await mount(
      fakeHass([
        departure(`${PREFIX}_next_departure`, IN_31_MINUTES, { track: "4" }),
      ]),
    );

    expect(pills(card)).toContain("Track 4");
  });

  it("colours a cancellation differently from a delay", async () => {
    // The tone is a class now. In the YAML card it was `strong > code` versus
    // `del > code`, and card-mod had to pierce a shadow root to apply either.
    const cancelled = await mount(
      fakeHass([
        departure(`${PREFIX}_next_departure`, IN_31_MINUTES, {
          status: "cancelled",
          status_text: "Cancelled",
        }),
      ]),
    );
    expect(cancelled.shadowRoot?.querySelector(".pill.bad")?.textContent).toBe(
      "Cancelled",
    );

    document.body.replaceChildren();
    const late = await mount(
      fakeHass([
        departure(`${PREFIX}_next_departure`, IN_31_MINUTES, {
          status_text: "12 min late",
          delay_minutes: 12,
        }),
      ]),
    );
    expect(late.shadowRoot?.querySelector(".pill.warn")?.textContent).toBe(
      "12 min late",
    );
  });

  it("warns about an overdue track only when others are posted", async () => {
    const quiet = await mount(
      fakeHass([
        departure(`${PREFIX}_next_departure`, "2026-08-09T22:03:00.000Z"),
      ]),
    );
    expect(pills(quiet)).toEqual(["Track not posted"]);

    document.body.replaceChildren();
    const busy = await mount(
      fakeHass([
        departure(`${PREFIX}_next_departure`, "2026-08-09T22:03:00.000Z"),
        departure(`${PREFIX}_departure_2`, IN_51_MINUTES, { track: "7" }),
      ]),
    );
    expect(pills(busy)).toEqual(["⚠️ Track overdue"]);
  });
});

describe("the card's tint", () => {
  const mood = (card: NJTransitDeparturesCard): string =>
    card.shadowRoot?.querySelector("ha-card")?.className.trim() ?? "";

  it("goes red when the train being shown is cancelled", async () => {
    // The card is read at arm's length on a platform, where a pill is too
    // small to resolve and the surface is the only thing that carries.
    //
    // It has to be the *favourite* that is cancelled. A cancelled train the
    // card has already skipped past is not the reader's problem, and tinting
    // the whole card for it would be crying wolf on the twenty other evenings
    // something on the board is off.
    const card = await mount(
      fakeHass([
        departure(`${PREFIX}_next_departure`, IN_31_MINUTES, {
          train_id: "6611",
        }),
        departure(`${PREFIX}_next_favorite`, IN_51_MINUTES, {
          train_id: "6615",
          status: "cancelled",
          status_text: "Cancelled",
        }),
      ]),
    );

    expect(mood(card)).toBe("bad");
  });

  it("stays calm about a cancellation it has already routed around", async () => {
    const card = await mount(
      fakeHass([
        departure(`${PREFIX}_next_departure`, IN_31_MINUTES, {
          status: "cancelled",
          status_text: "Cancelled",
        }),
        departure(`${PREFIX}_departure_2`, IN_51_MINUTES, {
          train_id: "6615",
          track: "7",
        }),
      ]),
    );

    expect(mood(card)).toBe("accent");
  });

  it("goes amber for a delay", async () => {
    const card = await mount(
      fakeHass([
        departure(`${PREFIX}_next_departure`, IN_31_MINUTES, {
          status_text: "12 min late",
          delay_minutes: 12,
        }),
      ]),
    );

    expect(mood(card)).toBe("warn");
  });

  it("stays calm on an ordinary evening", async () => {
    const card = await mount(
      fakeHass([
        departure(`${PREFIX}_next_departure`, IN_31_MINUTES, { track: "4" }),
      ]),
    );

    expect(mood(card)).toBe("accent");
  });

  it("reads a suspended line as bad, not as quiet", async () => {
    // Nothing left to run and nothing scheduled look identical in the hero
    // text, and one of them is an emergency.
    const suspended = await mount(
      fakeHass([
        departure(`${PREFIX}_next_departure`, IN_31_MINUTES, {
          status: "cancelled",
        }),
      ]),
    );
    expect(mood(suspended)).toBe("bad");

    document.body.replaceChildren();
    const quiet = await mount(fakeHass([empty(`${PREFIX}_next_departure`)]));
    expect(mood(quiet)).toBe("muted");
  });
});

describe("following a favourite", () => {
  const withFavorite = () =>
    fakeHass([
      departure(`${PREFIX}_next_departure`, IN_31_MINUTES),
      departure(`${PREFIX}_next_favorite`, IN_51_MINUTES, {
        train_id: "6615",
        favorite: true,
      }),
      {
        entity_id: `${PREFIX}_stops_away`,
        state: "3",
        attributes: { next_stop: "Millburn" },
        last_changed: "",
        last_updated: "",
      },
    ]);

  it("counts down to the favourite rather than the soonest train", async () => {
    const card = await mount(withFavorite());

    expect(text(card, "h2")).toContain("Train 6615");
  });

  it("shows how far along it is", async () => {
    const card = await mount(withFavorite());

    expect(text(card, ".progress")).toContain("3 stops away");
    expect(text(card, ".progress")).toContain("Millburn");
  });

  it("singularizes 1 stop away", async () => {
    const hass = fakeHass([
      departure(`${PREFIX}_next_departure`, IN_31_MINUTES),
      departure(`${PREFIX}_next_favorite`, IN_51_MINUTES, {
        train_id: "6615",
        favorite: true,
      }),
      {
        entity_id: `${PREFIX}_stops_away`,
        state: "1",
        attributes: { next_stop: "Short Hills" },
        last_changed: "",
        last_updated: "",
      },
    ]);

    const card = await mount(hass);

    expect(text(card, ".progress")).toContain("1 stop away");
    expect(text(card, ".progress")).not.toContain("1 stops away");
  });

  it("explains itself when showing somebody else's train", async () => {
    // Without this the fallback is indistinguishable from the favourite, and
    // the number at the top is for a service the reader was never catching.
    const hass = fakeHass([
      departure(`${PREFIX}_next_departure`, IN_31_MINUTES),
      { ...empty(`${PREFIX}_next_favorite`), attributes: { favorites: ["6615"] } },
    ]);

    const card = await mount(hass);

    expect(text(card, ".hint")).toContain("Waiting for 6615");
  });

  it("stays quiet when no favourites are configured", async () => {
    const hass = fakeHass([
      departure(`${PREFIX}_next_departure`, IN_31_MINUTES),
      { ...empty(`${PREFIX}_next_favorite`), attributes: { favorites: [] } },
    ]);

    const card = await mount(hass);

    expect(card.shadowRoot?.querySelector(".hint")).toBeNull();
  });

  it("does not report progress for a train it is not following", async () => {
    // `stops_away` only tracks the favourite, so it is only ever news when
    // the card is showing one.
    const hass = fakeHass([
      departure(`${PREFIX}_next_departure`, IN_31_MINUTES),
      {
        entity_id: `${PREFIX}_stops_away`,
        state: "3",
        attributes: {},
        last_changed: "",
        last_updated: "",
      },
    ]);

    const card = await mount(hass);

    expect(card.shadowRoot?.querySelector(".progress")).toBeNull();
  });
});

describe("the board", () => {
  it("lists every departure that has a train", async () => {
    const card = await mount(
      fakeHass([
        departure(`${PREFIX}_next_departure`, IN_31_MINUTES, { track: "4" }),
        departure(`${PREFIX}_departure_2`, IN_51_MINUTES, {
          train_id: "6615",
          favorite: true,
        }),
        empty(`${PREFIX}_departure_3`),
      ]),
    );

    const rows = card.shadowRoot?.querySelectorAll("tbody tr") ?? [];
    expect(rows).toHaveLength(2);
    expect(rows[0].textContent).toContain("6:31");
    expect(rows[0].textContent).toContain("31m");
    expect(rows[1].textContent).toContain("⭐");
  });

  it("opens the entity behind a row", async () => {
    // Not possible at all from a markdown card, which is why the YAML
    // dashboard's table was three lines of text you could only read.
    const card = await mount(
      fakeHass([departure(`${PREFIX}_next_departure`, IN_31_MINUTES)]),
    );
    const opened = vi.fn();
    card.addEventListener("hass-more-info", (event) => {
      opened((event as CustomEvent).detail.entityId);
    });

    const row = card.shadowRoot?.querySelector<HTMLElement>("tbody tr");
    row?.click();
    expect(opened).toHaveBeenCalledWith(`${PREFIX}_next_departure`);

    opened.mockClear();
    expect(row?.getAttribute("tabindex")).toBe("0");
    expect(row?.getAttribute("aria-label")).toContain("Train 6613");
    row?.dispatchEvent(
      new KeyboardEvent("keydown", { key: "Enter", bubbles: true }),
    );
    expect(opened).toHaveBeenCalledWith(`${PREFIX}_next_departure`);

    opened.mockClear();
    row?.dispatchEvent(
      new KeyboardEvent("keydown", { key: " ", bubbles: true }),
    );
    expect(opened).toHaveBeenCalledWith(`${PREFIX}_next_departure`);
  });

  it("labels table columns, track symbols, and favourite stars accessibly", async () => {
    const card = await mount(
      fakeHass([
        departure(`${PREFIX}_next_departure`, IN_31_MINUTES, {
          signaled_track: "7",
          favorite: true,
        }),
      ]),
    );

    const headers = [...(card.shadowRoot?.querySelectorAll("thead th") ?? [])];
    expect(headers).toHaveLength(4);
    for (const th of headers) {
      expect(th.getAttribute("scope")).toBe("col");
    }
    expect(headers[2].querySelector("abbr")?.getAttribute("title")).toBe(
      "Track",
    );
    expect(headers[3].querySelector(".sr-only")?.textContent).toBe("Status");

    const row = card.shadowRoot?.querySelector("tbody tr");
    expect(row?.querySelector('[aria-hidden="true"]')?.textContent).toContain(
      "⭐",
    );
    expect(row?.querySelector(".sr-only")?.textContent).toContain("favourite");

    const track = row?.querySelector(".pill");
    expect(track?.getAttribute("aria-label")).toBe("Track 7 (likely)");
    expect(track?.getAttribute("title")).toBe("Track 7 (likely)");
  });

  it("shows dep instead of now for already-departed rows", async () => {
    const card = await mount(
      fakeHass([
        departure(`${PREFIX}_next_departure`, "2026-08-09T21:57:00.000Z"),
        departure(`${PREFIX}_departure_2`, NOW.toISOString(), {
          train_id: "6615",
        }),
      ]),
    );

    const badges = [
      ...(card.shadowRoot?.querySelectorAll("tbody tr .relative") ?? []),
    ].map((el) => el.textContent?.trim());
    expect(badges).toEqual(["dep", "now"]);
  });

  it("stops ticking once it is off the page and refreshes on reconnect", async () => {
    // A dashboard left open on a wall tablet would otherwise accumulate a
    // timer per card rebuild, forever.
    const card = await mount(
      fakeHass([departure(`${PREFIX}_next_departure`, IN_31_MINUTES)]),
    );
    const cleared = vi.spyOn(globalThis, "clearInterval");

    // Calling connectedCallback while already connected clears the prior timer.
    card.connectedCallback();
    expect(cleared).toHaveBeenCalledTimes(1);

    card.remove();
    expect(cleared).toHaveBeenCalledTimes(2);

    // Advancing system time while detached refreshes `_now` on reconnect.
    vi.setSystemTime(new Date("2026-08-09T22:10:00.000Z"));
    document.body.append(card);
    await card.updateComplete;
    expect(text(card, ".countdown")).toBe("21min");
  });
});

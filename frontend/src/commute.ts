import type {
  CarDetail,
  Departure,
  HassEntity,
  HomeAssistant,
} from "./types.js";

/** States that mean "there is no value here", as the YAML dashboard put it. */
const NOTHING = new Set(["unknown", "unavailable", "none", ""]);

/** The most departure sensors the integration will ever create. */
const MAX_DEPARTURES = 10;

export interface Commute {
  /** Departure sensors in board order: next first, then 2, 3, ... */
  departures: string[];
  /** The next *favourite* departure, if the integration created one. */
  favorite: string | null;
  /** How far along the followed train is. */
  progress: string | null;
}

function isNothing(state: string | undefined): boolean {
  return state === undefined || NOTHING.has(state);
}

/** Return the number at the end of `..._departure_7`, for ordering. */
function trailingIndex(entityId: string): number {
  const match = /_(\d+)$/.exec(entityId);
  return match ? Number(match[1]) : 0;
}

/**
 * Find a commute's other entities from any one of them.
 *
 * The YAML dashboard did this by pasting a slugified commute name into every
 * card and rebuilding entity ids from it, which is why renaming an entity
 * broke the dashboard silently. Here the entity registry answers the same
 * question properly: the sensors share a device, and each says what it is
 * through its translation key.
 *
 * The prefix reconstruction survives as a fallback, because the registry is
 * only as good as what it was given -- an entity customised before this
 * integration set translation keys, or a `hass` object in a test, may carry
 * no registry entry at all.
 */
export function resolveCommute(
  hass: HomeAssistant,
  entityId: string,
): Commute {
  const byDevice = fromDevice(hass, entityId);
  const byPrefix = fromPrefix(hass, entityId);

  // Prefer the registry, but not to the point of showing one train where the
  // prefix can see three: a device that reports no numbered departures has
  // told us nothing useful, whatever the reason.
  if (byDevice && byDevice.departures.length >= byPrefix.departures.length) {
    return byDevice;
  }
  return {
    departures: byPrefix.departures,
    favorite: byPrefix.favorite ?? byDevice?.favorite ?? null,
    progress: byPrefix.progress ?? byDevice?.progress ?? null,
  };
}

function fromDevice(hass: HomeAssistant, entityId: string): Commute | null {
  const device = hass.entities?.[entityId]?.device_id;
  if (!device) {
    return null;
  }

  const siblings = Object.values(hass.entities).filter(
    (entry) => entry.device_id === device,
  );
  const withKey = (key: string): string[] =>
    siblings
      .filter((entry) => entry.translation_key === key)
      .map((entry) => entry.entity_id);

  const numbered = withKey("departure").sort(
    (a, b) => trailingIndex(a) - trailingIndex(b),
  );
  const departures = [...withKey("next_departure"), ...numbered];
  if (!departures.length) {
    return null;
  }

  return {
    departures,
    favorite: withKey("next_favorite")[0] ?? null,
    progress: withKey("stops_away")[0] ?? null,
  };
}

function fromPrefix(hass: HomeAssistant, entityId: string): Commute {
  const prefix = entityId.replace(/_next_departure$/, "");
  const exists = (id: string): string | null => (hass.states[id] ? id : null);

  const departures = [entityId];
  for (let index = 2; index <= MAX_DEPARTURES; index++) {
    const sibling = exists(`${prefix}_departure_${index}`);
    if (sibling) {
      departures.push(sibling);
    }
  }

  return {
    departures,
    favorite: exists(`${prefix}_next_favorite`),
    progress: exists(`${prefix}_stops_away`),
  };
}

function text(value: unknown): string | null {
  return typeof value === "string" && value !== "" ? value : null;
}

/** Read one departure sensor, or null when it is not reporting a train. */
export function readDeparture(
  hass: HomeAssistant,
  entityId: string,
): Departure | null {
  const entity: HassEntity | undefined = hass.states[entityId];
  if (!entity || isNothing(entity.state)) {
    return null;
  }

  const scheduled = new Date(entity.state);
  if (Number.isNaN(scheduled.getTime())) {
    return null;
  }

  const attributes = entity.attributes ?? {};
  const delay = attributes.delay_minutes;
  const cars = Array.isArray(attributes.cars)
    ? (attributes.cars as CarDetail[])
    : [];
  const alerts = Array.isArray(attributes.alerts)
    ? (attributes.alerts as unknown[]).filter(
        (alert): alert is string => typeof alert === "string",
      )
    : [];

  return {
    entityId,
    scheduled,
    trainId: text(attributes.train_id),
    favorite: attributes.favorite === true,
    track: text(attributes.track),
    status: text(attributes.status),
    statusText: text(attributes.status_text),
    delayMinutes: typeof delay === "number" ? delay : null,
    crowding: text(attributes.crowding),
    cars,
    alerts,
  };
}

/** Read every departure on the board, dropping the sensors with no train. */
export function readBoard(
  hass: HomeAssistant,
  commute: Commute,
): Departure[] {
  return commute.departures
    .map((entityId) => readDeparture(hass, entityId))
    .filter((departure): departure is Departure => departure !== null);
}

/**
 * Whether this station is posting tracks at all right now.
 *
 * A missing track only means something when the other trains have theirs --
 * the same test the integration applies before it will call a track overdue.
 * A station that posts nothing tells you nothing.
 */
export function isPostingTracks(board: Departure[]): boolean {
  return board.some((departure) => departure.track !== null);
}

/**
 * Pick the train the hero should count down to.
 *
 * The favourite wins whenever it is on the board, *including when it is
 * cancelled* -- that being the single most useful thing the card can say.
 * Falling back, though, a cancelled train is skipped: taking the first row
 * blindly once put a five-centimetre countdown to a train that was not
 * running at the top of the page, during a line suspension, which is exactly
 * when this card is read hardest.
 */
export function pickHero(
  favorite: Departure | null,
  board: Departure[],
): { departure: Departure | null; tracking: boolean; allCancelled: boolean } {
  if (favorite) {
    return { departure: favorite, tracking: true, allCancelled: false };
  }
  const usable = board.find((departure) => departure.status !== "cancelled");
  return {
    departure: usable ?? null,
    tracking: false,
    allCancelled: !usable && board.length > 0,
  };
}

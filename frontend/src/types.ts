// The parts of Home Assistant's frontend objects this card actually reads.
//
// Declared here rather than imported: `home-assistant-js-websocket` and the
// frontend's own `types.ts` are not published as a package a custom card can
// depend on, and vendoring the full definitions would pin us to one release.
// A narrow structural type is both smaller and more forgiving of drift.

export interface HassEntity {
  entity_id: string;
  state: string;
  attributes: Record<string, unknown>;
  last_changed: string;
  last_updated: string;
}

/** An entity registry entry, as the frontend exposes it to cards. */
export interface EntityRegistryDisplayEntry {
  entity_id: string;
  device_id?: string;
  translation_key?: string;
  platform?: string;
}

export interface HomeAssistant {
  states: Record<string, HassEntity>;
  entities: Record<string, EntityRegistryDisplayEntry>;
  locale?: { language: string };
  language?: string;
}

export interface DeparturesCardConfig {
  type: string;
  /** The commute's `next_departure` sensor. Everything else is found from it. */
  entity?: string;
  /** Optional override for the card's own heading. */
  title?: string;
}

/** One car's crowding, as the departure sensors report it. */
export interface CarDetail {
  number?: string | null;
  position?: string | null;
  crowding?: string | null;
}

/** A departure, read off a sensor's state and attributes. */
export interface Departure {
  entityId: string;
  /** The scheduled departure. A sensor with nothing to report reads as null. */
  scheduled: Date;
  trainId: string | null;
  favorite: boolean;
  track: string | null;
  status: string | null;
  statusText: string | null;
  delayMinutes: number | null;
  crowding: string | null;
  cars: CarDetail[];
  alerts: string[];
}

import type { HassEntity, HomeAssistant } from "../src/types.js";

export const PREFIX = "sensor.short_hills_station_to_new_york_penn_station";
export const DEVICE = "device-short-hills";

/** A departure sensor, carrying the attributes the integration reports. */
export function departure(
  entityId: string,
  scheduled: string,
  attributes: Record<string, unknown> = {},
): HassEntity {
  return {
    entity_id: entityId,
    state: scheduled,
    attributes: {
      train_id: "6613",
      favorite: false,
      track: null,
      status: "on_time",
      status_text: null,
      delay_minutes: null,
      crowding: "unknown",
      cars: [],
      alerts: [],
      ...attributes,
    },
    last_changed: scheduled,
    last_updated: scheduled,
  };
}

/** A sensor reporting nothing -- no train, rather than a broken integration. */
export function empty(entityId: string): HassEntity {
  return {
    entity_id: entityId,
    state: "unknown",
    attributes: {},
    last_changed: "",
    last_updated: "",
  };
}

interface Options {
  /** Give the entities a device and translation keys, as the registry would. */
  registry?: boolean;
  /** Translation keys for entities whose id no longer implies one. */
  keys?: Record<string, string>;
}

/**
 * Build a `hass` carrying these entities.
 *
 * Translation keys are assigned the way the integration assigns them, so a
 * test that renames an entity id still exercises the registry path.
 */
export function fakeHass(
  entities: HassEntity[],
  options: Options = {},
): HomeAssistant {
  const states: Record<string, HassEntity> = {};
  const registry: HomeAssistant["entities"] = {};

  for (const entity of entities) {
    states[entity.entity_id] = entity;
    if (options.registry) {
      registry[entity.entity_id] = {
        entity_id: entity.entity_id,
        device_id: DEVICE,
        translation_key:
          options.keys?.[entity.entity_id] ?? translationKey(entity.entity_id),
        platform: "njtransit",
      };
    }
  }

  return { states, entities: registry };
}

function translationKey(entityId: string): string {
  if (entityId.endsWith("_next_departure")) {
    return "next_departure";
  }
  if (entityId.endsWith("_next_favorite")) {
    return "next_favorite";
  }
  if (entityId.endsWith("_stops_away")) {
    return "stops_away";
  }
  return "departure";
}

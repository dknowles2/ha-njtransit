import { LitElement, html, nothing } from "lit";
import type { TemplateResult } from "lit";
import { customElement, property, state } from "lit/decorators.js";

import type { DeparturesCardConfig, HomeAssistant } from "./types.js";

// The picker cannot narrow to "the next departure sensor" -- an entity
// selector filters by domain and integration, not by translation key -- so it
// offers every sensor this integration made and the card sorts out the rest.
// Picking `..._departure_3` still works: the commute is resolved from the
// device, not from which of its sensors was named.
const SCHEMA = [
  {
    name: "entity",
    required: true,
    selector: { entity: { integration: "njtransit", domain: "sensor" } },
  },
  { name: "title", selector: { text: {} } },
];

const LABELS: Record<string, string> = {
  entity: "Commute (any departure sensor)",
  title: "Heading (optional)",
};

@customElement("njtransit-departures-editor")
export class NJTransitDeparturesEditor extends LitElement {
  @property({ attribute: false }) public hass?: HomeAssistant;

  @state() private _config?: DeparturesCardConfig;

  public setConfig(config: DeparturesCardConfig): void {
    this._config = config;
  }

  protected override render(): TemplateResult | typeof nothing {
    if (!this.hass || !this._config) {
      return nothing;
    }

    return html`
      <ha-form
        .hass=${this.hass}
        .data=${this._config}
        .schema=${SCHEMA}
        .computeLabel=${(field: { name: string }) =>
          LABELS[field.name] ?? field.name}
        @value-changed=${this._changed}
      ></ha-form>
    `;
  }

  private _changed(event: CustomEvent): void {
    this.dispatchEvent(
      new CustomEvent("config-changed", {
        detail: { config: event.detail.value },
        bubbles: true,
        composed: true,
      }),
    );
  }
}

declare global {
  interface HTMLElementTagNameMap {
    "njtransit-departures-editor": NJTransitDeparturesEditor;
  }
}

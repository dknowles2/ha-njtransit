import { LitElement, css, html, nothing } from "lit";
import type { TemplateResult } from "lit";
import { customElement, property, state } from "lit/decorators.js";
import { classMap } from "lit/directives/class-map.js";

import {
  isPostingTracks,
  pickHero,
  readBoard,
  readDeparture,
  resolveCommute,
} from "./commute.js";
import { countdown, formatClock, formatShortClock, minutesUntil } from "./format.js";
import {
  crowdingPill,
  emptiestCars,
  statusPill,
  trackCell,
  trackPill,
} from "./pills.js";
import type { Pill } from "./pills.js";
import type { Departure, DeparturesCardConfig, HomeAssistant } from "./types.js";

// How often the countdown recomputes.
//
// This is the thing a markdown card could not do: its content only re-renders
// when some entity changes, so "in 12 min" sat there being wrong for as long
// as the integration's poll interval. Ten seconds is well inside a minute's
// resolution without spinning.
const TICK_MS = 10_000;

@customElement("njtransit-departures")
export class NJTransitDeparturesCard extends LitElement {
  @property({ attribute: false }) public hass?: HomeAssistant;

  @state() private _config?: DeparturesCardConfig;

  @state() private _now = new Date();

  private _timer?: ReturnType<typeof setInterval>;

  public setConfig(config: DeparturesCardConfig): void {
    if (!config?.entity) {
      throw new Error("Set `entity` to the commute's next departure sensor");
    }
    this._config = config;
  }

  public getCardSize(): number {
    return 8;
  }

  public static async getConfigElement(): Promise<HTMLElement> {
    await import("./editor.js");
    return document.createElement("njtransit-departures-editor");
  }

  /** Offer a working card the moment it is added from the picker. */
  public static getStubConfig(hass: HomeAssistant): DeparturesCardConfig {
    const entity = Object.keys(hass.states).find((id) =>
      id.endsWith("_next_departure"),
    );
    return { type: "custom:njtransit-departures", entity: entity ?? "" };
  }

  public override connectedCallback(): void {
    super.connectedCallback();
    this._timer = setInterval(() => {
      this._now = new Date();
    }, TICK_MS);
  }

  public override disconnectedCallback(): void {
    super.disconnectedCallback();
    if (this._timer) {
      clearInterval(this._timer);
      this._timer = undefined;
    }
  }

  protected override render(): TemplateResult | typeof nothing {
    if (!this.hass || !this._config?.entity) {
      return nothing;
    }

    const commute = resolveCommute(this.hass, this._config.entity);
    const board = readBoard(this.hass, commute);
    const favorite = commute.favorite
      ? readDeparture(this.hass, commute.favorite)
      : null;
    const { departure, tracking, allCancelled } = pickHero(favorite, board);
    const posting = isPostingTracks(board);

    return html`
      <ha-card>
        ${this._config.title
          ? html`<h1 class="card-header">${this._config.title}</h1>`
          : nothing}
        ${departure
          ? this._renderHero(departure, {
              tracking,
              posting,
              progress: commute.progress,
              favoriteEntity: commute.favorite,
            })
          : this._renderEmpty(allCancelled)}
        ${board.length ? this._renderBoard(board, posting) : nothing}
      </ha-card>
    `;
  }

  private _renderEmpty(allCancelled: boolean): TemplateResult {
    return html`
      <div class="hero empty">
        <h3>Nothing on the board</h3>
        <p>
          ${allCancelled
            ? "Every upcoming departure is cancelled."
            : "No departures in the next couple of hours."}
        </p>
      </div>
    `;
  }

  private _renderHero(
    departure: Departure,
    context: {
      tracking: boolean;
      posting: boolean;
      progress: string | null;
      favoriteEntity: string | null;
    },
  ): TemplateResult {
    const { tracking, posting, progress, favoriteEntity } = context;
    const minutes = minutesUntil(departure.scheduled, this._now);
    const { value, unit } = countdown(minutes);
    const pills = [
      trackPill(departure, minutes, posting),
      statusPill(departure),
      crowdingPill(departure),
    ].filter((pill): pill is Pill => pill !== null);
    const emptiest = emptiestCars(departure.cars);

    return html`
      <div
        class="hero"
        role="button"
        tabindex="0"
        @click=${() => this._moreInfo(departure.entityId)}
        @keydown=${(event: KeyboardEvent) => {
          if (event.key === "Enter" || event.key === " ") {
            event.preventDefault();
            this._moreInfo(departure.entityId);
          }
        }}
      >
        <div class="countdown">
          ${value}${unit ? html`<span class="unit">min</span>` : nothing}
        </div>
        <h2>
          ${formatClock(departure.scheduled)}
          ${departure.trainId ? html`· Train ${departure.trainId}` : nothing}
        </h2>
        <div class="pills">${pills.map((pill) => this._renderPill(pill))}</div>
        ${emptiest
          ? html`<p class="hint">${title(emptiest)} cars are emptier</p>`
          : nothing}
        ${tracking ? this._renderProgress(progress) : nothing}
        ${departure.alerts.map(
          (message) => html`<blockquote>${message}</blockquote>`,
        )}
        ${tracking ? nothing : this._renderWaiting(favoriteEntity)}
      </div>
    `;
  }

  private _renderProgress(progress: string | null): TemplateResult | typeof nothing {
    const entity = progress ? this.hass!.states[progress] : undefined;
    if (!entity || !/^\d+$/.test(entity.state)) {
      return nothing;
    }

    const stops = Number(entity.state);
    const next = entity.attributes.next_stop;
    return html`
      <hr />
      <p class="progress">
        ${stops === 0 ? "Arriving now" : html`<strong>${stops}</strong> stops away`}
        ${typeof next === "string" && next ? html` · next ${next}` : nothing}
      </p>
    `;
  }

  /**
   * Why the card is showing someone else's train.
   *
   * Without this the fallback is indistinguishable from the favourite, and
   * the countdown at the top is for a service the reader was never going to
   * board.
   */
  private _renderWaiting(
    favoriteEntity: string | null,
  ): TemplateResult | typeof nothing {
    const favorites = favoriteEntity
      ? this.hass!.states[favoriteEntity]?.attributes.favorites
      : undefined;

    if (!Array.isArray(favorites)) {
      return nothing;
    }
    return html`
      <p class="hint">
        ${favorites.length
          ? `Waiting for ${favorites.join(", ")} — not on the board yet.`
          : "No favourite set. Pick one in this commute's options."}
      </p>
    `;
  }

  private _renderBoard(board: Departure[], posting: boolean): TemplateResult {
    return html`
      <div class="board">
        <table>
          <thead>
            <tr>
              <th>Departs</th>
              <th>Train</th>
              <th>Trk</th>
              <th></th>
            </tr>
          </thead>
          <tbody>
            ${board.map((departure) => this._renderRow(departure, posting))}
          </tbody>
        </table>
      </div>
    `;
  }

  private _renderRow(departure: Departure, posting: boolean): TemplateResult {
    const minutes = minutesUntil(departure.scheduled, this._now);
    const status = statusPill(departure);

    return html`
      <tr @click=${() => this._moreInfo(departure.entityId)}>
        <td>
          <strong>${formatShortClock(departure.scheduled)}</strong>
          <span class="relative">${minutes > 0 ? `${minutes}m` : "now"}</span>
        </td>
        <td>${departure.trainId}${departure.favorite ? " ⭐" : ""}</td>
        <td>${this._renderPill(trackCell(departure, minutes, posting))}</td>
        <td>${status ? this._renderPill(status) : nothing}</td>
      </tr>
    `;
  }

  private _renderPill(pill: Pill): TemplateResult {
    return html`<span
      class=${classMap({ pill: true, [pill.tone]: true })}
      >${pill.text}</span
    >`;
  }

  private _moreInfo(entityId: string): void {
    this.dispatchEvent(
      new CustomEvent("hass-more-info", {
        detail: { entityId },
        bubbles: true,
        composed: true,
      }),
    );
  }

  public static override styles = css`
    :host {
      /* The board itself sends #00953b as the Morristown Line's colour. Other
         lines send their own, and the feed also uses that field to mark
         cancellations, so it is not a dependable line identity and the
         integration does not expose it -- override this to match yours. */
      --njtransit-accent: #00953b;
    }

    ha-card {
      border: none;
      border-top: 3px solid var(--njtransit-accent);
      border-radius: 16px;
      overflow: hidden;
      background: linear-gradient(
        168deg,
        color-mix(in srgb, var(--njtransit-accent) 10%, var(--card-background-color))
          0%,
        var(--card-background-color) 62%
      );
    }

    .card-header {
      margin: 0;
      padding: 14px 16px 0;
      font-size: 1.1rem;
      font-weight: 600;
    }

    .hero {
      padding: 16px 16px 18px;
      cursor: pointer;
    }

    .hero:focus-visible {
      outline: 2px solid var(--njtransit-accent);
      outline-offset: -2px;
    }

    .hero.empty {
      cursor: default;
    }

    .countdown {
      font-size: 3.5rem;
      font-weight: 800;
      line-height: 0.95;
      letter-spacing: -0.035em;
      font-variant-numeric: tabular-nums;
      color: var(--primary-text-color);
      margin-bottom: 2px;
    }

    /* The unit, kept small so the number carries the card. */
    .unit {
      font-size: 0.28em;
      font-weight: 600;
      letter-spacing: 0.1em;
      text-transform: uppercase;
      color: var(--secondary-text-color);
      margin-left: 0.3em;
      vertical-align: 0.6em;
    }

    h2 {
      margin: 0 0 14px;
      font-size: 0.78rem;
      font-weight: 600;
      letter-spacing: 0.1em;
      text-transform: uppercase;
      color: var(--secondary-text-color);
    }

    h3 {
      margin: 0 0 6px;
      font-size: 1.15rem;
      font-weight: 600;
    }

    p {
      margin: 0;
      line-height: 1.6;
    }

    .hint {
      margin-top: 10px;
      color: var(--secondary-text-color);
      font-style: italic;
    }

    hr {
      border: none;
      border-top: 1px solid var(--divider-color);
      margin: 14px 0 12px;
    }

    .pills {
      display: flex;
      flex-wrap: wrap;
      gap: 6px;
    }

    .pill {
      font-size: 0.8rem;
      font-weight: 700;
      letter-spacing: 0.015em;
      white-space: nowrap;
      padding: 4px 10px;
      border-radius: 999px;
      border: 1px solid transparent;
      background: var(--njtransit-accent);
      color: #fff;
    }

    .pill.bad {
      background: var(--error-color);
      color: #fff;
    }

    .pill.warn {
      background: var(--warning-color);
      color: #111;
    }

    .pill.muted {
      background: transparent;
      color: var(--secondary-text-color);
      border-color: var(--divider-color);
      font-weight: 600;
    }

    blockquote {
      margin: 12px 0 0;
      padding: 9px 12px;
      border-left: 3px solid var(--error-color);
      border-radius: 0 8px 8px 0;
      background: color-mix(in srgb, var(--error-color) 10%, transparent);
      font-size: 0.9rem;
      line-height: 1.5;
    }

    .board {
      padding: 0 16px 14px;
    }

    table {
      width: 100%;
      border-collapse: collapse;
      font-variant-numeric: tabular-nums;
    }

    th {
      padding: 0 4px 8px 0;
      border-bottom: 1px solid var(--divider-color);
      text-align: left;
      font-size: 0.65rem;
      font-weight: 700;
      letter-spacing: 0.12em;
      text-transform: uppercase;
      color: var(--secondary-text-color);
    }

    td {
      padding: 10px 4px 10px 0;
      border-bottom: 1px solid
        color-mix(in srgb, var(--divider-color) 45%, transparent);
      font-size: 0.98rem;
      white-space: nowrap;
    }

    tbody tr {
      cursor: pointer;
    }

    tbody tr:last-child td {
      border-bottom: none;
    }

    .relative {
      font-size: 0.8rem;
      color: var(--secondary-text-color);
      margin-left: 4px;
    }

    .board .pill {
      font-size: 0.75rem;
      padding: 3px 9px;
    }
  `;
}

function title(value: string): string {
  return value.charAt(0).toUpperCase() + value.slice(1);
}

declare global {
  interface HTMLElementTagNameMap {
    "njtransit-departures": NJTransitDeparturesCard;
  }
}

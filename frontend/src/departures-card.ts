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
  cardMood,
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
    const pills = departure ? this._pillsFor(departure, posting) : [];
    // An empty board is not calm, it is a line suspension.
    const mood = departure ? cardMood(pills) : allCancelled ? "bad" : "muted";

    return html`
      <ha-card class=${classMap({ [mood]: true })}>
        ${this._config.title
          ? html`<h1 class="card-header">${this._config.title}</h1>`
          : nothing}
        ${departure
          ? this._renderHero(departure, pills, {
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

  /** Everything the hero has to say about this train, in reading order. */
  private _pillsFor(departure: Departure, posting: boolean): Pill[] {
    const minutes = minutesUntil(departure.scheduled, this._now);
    return [
      trackPill(departure, minutes, posting),
      statusPill(departure),
      crowdingPill(departure),
    ].filter((pill): pill is Pill => pill !== null);
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
    pills: Pill[],
    context: {
      tracking: boolean;
      posting: boolean;
      progress: string | null;
      favoriteEntity: string | null;
    },
  ): TemplateResult {
    const { tracking, progress, favoriteEntity } = context;
    const minutes = minutesUntil(departure.scheduled, this._now);
    const { value, unit } = countdown(minutes);
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
          <span class="clock">${formatClock(departure.scheduled)}</span>
          ${departure.trainId
            ? html`<span class="train">Train ${departure.trainId}</span>`
            : nothing}
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

      /* Set per card by the worst thing on it, and used for the surface tint,
         the hairline and the row highlight. See cardMood in pills.ts. */
      --mood: var(--njtransit-accent);

      --njt-radius: 18px;
      --njt-gutter: 18px;
      display: block;
      container-type: inline-size;
    }

    /* Every tinted surface in here is these three expressions, mixed in oklab
       so the result keeps its lightness whatever hue it is given. Mixing the
       foreground with the *theme's* text colour is what makes one stylesheet
       work in both: against a light theme it darkens toward black and against
       a dark one it lifts toward white, so a dark green that would be
       unreadable on near-black never has to be special-cased.

       --ink is how much of the tone survives into the text, and it is
       measured rather than chosen. At a straight 70% the amber pill came out
       at 3.33:1 against its own tint in a light theme -- failing WCAG AA for
       text this size -- while green and red sat at 4.2 in a dark one. Amber
       needs far more of the text colour than the others, because a light hue
       can only be darkened by borrowing from it. */
    .pill,
    blockquote {
      --ink: 58%;
      background: color-mix(in oklab, var(--tone) 14%, transparent);
      color: color-mix(
        in oklab,
        var(--tone) var(--ink),
        var(--primary-text-color)
      );
      box-shadow: inset 0 0 0 1px
        color-mix(in oklab, var(--tone) 28%, transparent);
    }

    ha-card {
      position: relative;
      border: none;
      border-radius: var(--njt-radius);
      overflow: hidden;
      background: var(--card-background-color);
    }

    ha-card.warn {
      --mood: var(--warning-color);
    }

    ha-card.bad {
      --mood: var(--error-color);
    }

    ha-card.muted {
      --mood: var(--secondary-text-color);
    }

    /* The tint, as a corner wash rather than a border. A 3px rule across the
       top reads as a status bar on a 2015 dashboard; this reads as the card
       being lit from somewhere. */
    ha-card::before {
      content: "";
      position: absolute;
      inset: 0;
      pointer-events: none;
      background: radial-gradient(
        95% 65% at 0% 0%,
        color-mix(in oklab, var(--mood) 13%, transparent),
        transparent 68%
      );
      transition: background 600ms ease;
    }

    /* What is left of the top border: a hairline that fades out rather than
       stopping, so it reads as an edge lit by the same source. */
    ha-card::after {
      content: "";
      position: absolute;
      inset: 0 0 auto;
      height: 2px;
      pointer-events: none;
      background: linear-gradient(
        90deg,
        var(--mood),
        color-mix(in oklab, var(--mood) 20%, transparent) 55%,
        transparent
      );
    }

    .card-header {
      position: relative;
      margin: 0;
      padding: 16px var(--njt-gutter) 0;
      font-size: 1.05rem;
      font-weight: 600;
      letter-spacing: -0.01em;
    }

    .hero {
      position: relative;
      padding: 18px var(--njt-gutter) 20px;
      cursor: pointer;
      -webkit-tap-highlight-color: transparent;
    }

    .hero.empty {
      cursor: default;
    }

    .hero:focus-visible {
      outline: 2px solid var(--mood);
      outline-offset: -3px;
      border-radius: var(--njt-radius);
    }

    .countdown {
      display: flex;
      align-items: baseline;
      gap: 0.12em;
      font-size: clamp(3rem, 17cqi, 4rem);
      font-weight: 750;
      line-height: 0.92;
      letter-spacing: -0.045em;
      font-variant-numeric: tabular-nums;
      /* Not the mood colour: a delayed train is still the number you are
         reading, and tinting it amber makes the one thing this card exists
         to show harder to read, not easier. */
      color: var(--primary-text-color);
      margin-bottom: 6px;
    }

    /* The unit, kept small so the number carries the card. */
    .unit {
      font-size: 0.26em;
      font-weight: 650;
      letter-spacing: 0.08em;
      text-transform: uppercase;
      color: var(--secondary-text-color);
      transform: translateY(-0.55em);
    }

    h2 {
      display: flex;
      flex-wrap: wrap;
      align-items: baseline;
      gap: 0 8px;
      margin: 0 0 14px;
      font-size: 0.9rem;
      font-weight: 500;
      font-variant-numeric: tabular-nums;
    }

    .clock {
      color: var(--primary-text-color);
      font-weight: 650;
    }

    .train {
      color: var(--secondary-text-color);
    }

    /* Separator between the two, drawn rather than typed, so it never lands
       on its own line when the card is narrow. */
    .train::before {
      content: "";
      display: inline-block;
      width: 3px;
      height: 3px;
      margin-right: 8px;
      border-radius: 50%;
      background: currentColor;
      vertical-align: 0.22em;
      opacity: 0.55;
    }

    h3 {
      margin: 0 0 6px;
      font-size: 1.15rem;
      font-weight: 650;
      letter-spacing: -0.01em;
    }

    p {
      margin: 0;
      line-height: 1.55;
    }

    .hint {
      margin-top: 12px;
      color: var(--secondary-text-color);
      font-size: 0.9rem;
    }

    hr {
      border: none;
      border-top: 1px solid
        color-mix(in oklab, var(--divider-color) 60%, transparent);
      margin: 16px 0 12px;
    }

    .progress {
      font-size: 0.95rem;
    }

    .pills {
      display: flex;
      flex-wrap: wrap;
      gap: 6px;
    }

    .pill {
      --tone: var(--njtransit-accent);
      font-size: 0.78rem;
      font-weight: 650;
      letter-spacing: 0.005em;
      white-space: nowrap;
      padding: 4px 11px;
      border-radius: 999px;
    }

    .pill.bad {
      --tone: var(--error-color);
    }

    .pill.warn {
      --tone: var(--warning-color);
      --ink: 34%;
    }

    .pill.muted {
      --tone: var(--secondary-text-color);
      font-weight: 600;
    }

    blockquote {
      --tone: var(--error-color);
      margin: 14px 0 0;
      padding: 10px 13px;
      border-radius: 10px;
      font-size: 0.9rem;
      line-height: 1.5;
    }

    .board {
      position: relative;
      padding: 0 var(--njt-gutter) 8px;
    }

    table {
      width: 100%;
      border-collapse: collapse;
      font-variant-numeric: tabular-nums;
    }

    th {
      padding: 0 6px 8px 0;
      border-bottom: 1px solid
        color-mix(in oklab, var(--divider-color) 70%, transparent);
      text-align: left;
      font-size: 0.63rem;
      font-weight: 700;
      letter-spacing: 0.14em;
      text-transform: uppercase;
      color: var(--secondary-text-color);
    }

    td {
      padding: 11px 6px 11px 0;
      border-bottom: 1px solid
        color-mix(in oklab, var(--divider-color) 35%, transparent);
      font-size: 0.96rem;
      white-space: nowrap;
    }

    /* The row is the tap target, so the highlight has to reach the card edge
       rather than stopping at the table's padding. */
    tbody tr {
      cursor: pointer;
      -webkit-tap-highlight-color: transparent;
      box-shadow: 0 0 0 0 transparent;
      transition:
        background-color 120ms ease,
        box-shadow 120ms ease;
    }

    tbody tr:hover,
    tbody tr:active {
      background: color-mix(in oklab, var(--mood) 8%, transparent);
      box-shadow:
        calc(var(--njt-gutter) * -1) 0 0 0
          color-mix(in oklab, var(--mood) 8%, transparent),
        var(--njt-gutter) 0 0 0 color-mix(in oklab, var(--mood) 8%, transparent);
    }

    tbody tr:last-child td {
      border-bottom: none;
    }

    .relative {
      font-size: 0.8rem;
      color: var(--secondary-text-color);
      margin-left: 5px;
    }

    .board .pill {
      font-size: 0.74rem;
      padding: 3px 9px;
    }

    /* Anything worth a red pill is worth finding without reading. Slow and
       shallow: this sits next to a number someone is trying to read. */
    @media (prefers-reduced-motion: no-preference) {
      .pills .pill.bad {
        animation: attention 2.6s ease-in-out infinite;
      }
    }

    @keyframes attention {
      0%,
      100% {
        box-shadow: inset 0 0 0 1px
          color-mix(in oklab, var(--tone) 28%, transparent);
      }
      50% {
        box-shadow: inset 0 0 0 1px
          color-mix(in oklab, var(--tone) 70%, transparent);
      }
    }

    /* Narrow columns, and the phone this is really for. */
    @container (max-width: 330px) {
      :host {
        --njt-gutter: 14px;
      }

      td,
      th {
        font-size: 0.9rem;
      }

      .relative {
        display: none;
      }
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

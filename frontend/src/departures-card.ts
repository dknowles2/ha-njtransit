import { LitElement, css, html, nothing } from "lit";
import type { TemplateResult } from "lit";
import { customElement, property, state } from "lit/decorators.js";
import { classMap } from "lit/directives/class-map.js";

import {
  boardStatus,
  isPostingTracks,
  pickHero,
  readBoard,
  readDeparture,
  resolveCommute,
} from "./commute.js";
import {
  countdown,
  effectiveDeparture,
  formatClock,
  formatShortClock,
  minutesUntil,
} from "./format.js";
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
    if (!config) {
      throw new Error("Invalid configuration");
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
    if (this._timer) {
      clearInterval(this._timer);
    }
    this._now = new Date();
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
    if (!this.hass || !this._config) {
      return nothing;
    }

    const heading = this._config.title
      ? html`<h1 class="card-header">${this._config.title}</h1>`
      : nothing;

    if (!this._config.entity) {
      return html`
        <ha-card class="muted">
          ${heading}
          ${this._renderMessage(
            "Select a commute",
            "Choose a departure sensor in the card configuration.",
          )}
        </ha-card>
      `;
    }

    const commute = resolveCommute(this.hass, this._config.entity);
    const availability = boardStatus(this.hass, this._config.entity, commute);
    if (availability === "missing") {
      return html`
        <ha-card class="warn">
          ${heading}
          ${this._renderMessage(
            "Entity not found",
            "Check the card configuration and select a commute departure sensor.",
          )}
        </ha-card>
      `;
    }
    if (availability === "unavailable") {
      return html`
        <ha-card class="warn">
          ${heading}
          ${this._renderMessage(
            "Departure board unavailable",
            "Unable to reach NJ Transit. Check the integration or your connection.",
          )}
        </ha-card>
      `;
    }

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
        ${heading}
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
    const minutes = minutesUntil(effectiveDeparture(departure), this._now);
    return [
      trackPill(departure, minutes, posting),
      statusPill(departure),
      crowdingPill(departure),
    ].filter((pill): pill is Pill => pill !== null);
  }

  private _renderMessage(titleText: string, body: string): TemplateResult {
    return html`
      <div class="hero empty">
        <h2>${titleText}</h2>
        <p>${body}</p>
      </div>
    `;
  }

  private _renderEmpty(allCancelled: boolean): TemplateResult {
    return this._renderMessage(
      "Nothing on the board",
      allCancelled
        ? "Every upcoming departure is cancelled."
        : "No departures in the next couple of hours.",
    );
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
    const minutes = minutesUntil(effectiveDeparture(departure), this._now);
    const { value, unit } =
      departure.status === "cancelled"
        ? { value: "Cancelled", unit: false }
        : countdown(minutes);
    const emptiest = emptiestCars(departure.cars);

    return html`
      <div class="hero">
        <div
          class="hero-main"
          role="button"
          tabindex="0"
          aria-label=${this._departureLabel(departure)}
          @click=${() => this._moreInfo(departure.entityId)}
          @keydown=${(event: KeyboardEvent) =>
            this._activate(event, departure.entityId)}
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
        </div>
        ${departure.alerts.map(
          (message) => html`<div class="alert" role="note">${message}</div>`,
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
        ${stops === 0
          ? "Arriving now"
          : html`<strong>${stops}</strong> ${stops === 1 ? "stop" : "stops"} away`}
        ${typeof next === "string" && next ? html` · next ${next}` : nothing}
      </p>
    `;
  }

  /**
   * Why the card is showing someone else's train.
   *
   * Without this the fallback is indistinguishable from the favourite, and
   * the countdown at the top is for a service the reader was never going to
   * board. Riders who have not configured any favourites are not nagged.
   */
  private _renderWaiting(
    favoriteEntity: string | null,
  ): TemplateResult | typeof nothing {
    const favorites = favoriteEntity
      ? this.hass!.states[favoriteEntity]?.attributes.favorites
      : undefined;

    if (!Array.isArray(favorites) || favorites.length === 0) {
      return nothing;
    }
    return html`
      <p class="hint">
        Waiting for ${favorites.join(", ")} — not on the board yet.
      </p>
    `;
  }

  private _renderBoard(board: Departure[], posting: boolean): TemplateResult {
    return html`
      <div class="board">
        <table>
          <thead>
            <tr>
              <th scope="col">Departs</th>
              <th scope="col">Train</th>
              <th scope="col"><abbr title="Track">Trk</abbr></th>
              <th scope="col"><span class="sr-only">Status</span></th>
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
    const minutes = minutesUntil(effectiveDeparture(departure), this._now);
    const status = statusPill(departure);
    const relative =
      minutes > 0 ? `${minutes}m` : minutes === 0 ? "now" : "dep";

    return html`
      <tr
        tabindex="0"
        aria-label=${this._departureLabel(departure)}
        @click=${() => this._moreInfo(departure.entityId)}
        @keydown=${(event: KeyboardEvent) =>
          this._activate(event, departure.entityId)}
      >
        <td>
          <strong>${formatShortClock(departure.scheduled)}</strong>
          <span class="relative">${relative}</span>
        </td>
        <td>
          ${departure.trainId}${departure.favorite
            ? html`<span aria-hidden="true"> ⭐</span
                ><span class="sr-only"> (favourite)</span>`
            : nothing}
        </td>
        <td>${this._renderPill(trackCell(departure, minutes, posting))}</td>
        <td>${status ? this._renderPill(status) : nothing}</td>
      </tr>
    `;
  }

  private _renderPill(pill: Pill): TemplateResult {
    return html`<span
      class=${classMap({ pill: true, [pill.tone]: true })}
      aria-label=${pill.label ?? nothing}
      title=${pill.label ?? nothing}
      >${pill.text}</span
    >`;
  }

  private _departureLabel(departure: Departure): string {
    const parts: string[] = [];
    if (departure.trainId) {
      parts.push(`Train ${departure.trainId}`);
    }
    parts.push(formatClock(departure.scheduled));
    if (departure.favorite) {
      parts.push("favourite");
    }
    if (departure.track) {
      parts.push(`Track ${departure.track}`);
    } else if (departure.signaledTrack && departure.status !== "cancelled") {
      parts.push(`Track ${departure.signaledTrack} (likely)`);
    }
    if (departure.statusText) {
      parts.push(departure.statusText);
    } else if (departure.status === "cancelled") {
      parts.push("Cancelled");
    }
    return parts.join(", ");
  }

  private _activate(event: KeyboardEvent, entityId: string): void {
    if (event.key === "Enter" || event.key === " ") {
      event.preventDefault();
      this._moreInfo(entityId);
    }
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
      /* Secondary text mixed toward primary so text over the 13% corner wash
         clears WCAG AA (4.5:1) in every mood across both light and dark HA
         themes (lowest is 4.56:1 in dark warn, 8.26:1+ in light). */
      --njt-secondary: color-mix(
        in oklab,
        var(--secondary-text-color) 50%,
        var(--primary-text-color)
      );
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
    .alert {
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
      /* Focus outline mixed with primary text so WCAG 1.4.11 (3:1) clears in
         all moods including light-theme warn (4.66:1 over wash). */
      --njt-focus: color-mix(
        in oklab,
        var(--mood) 55%,
        var(--primary-text-color)
      );
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
    }

    .hero-main {
      cursor: pointer;
      -webkit-tap-highlight-color: transparent;
      border-radius: 10px;
    }

    .hero-main:focus-visible {
      outline: 2px solid var(--njt-focus);
      outline-offset: 4px;
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
      color: var(--njt-secondary);
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

    .hero.empty h2 {
      display: block;
      margin: 0 0 6px;
      font-size: 1.15rem;
      font-weight: 650;
      letter-spacing: -0.01em;
    }

    .clock {
      color: var(--primary-text-color);
      font-weight: 650;
    }

    .train {
      color: var(--njt-secondary);
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

    p {
      margin: 0;
      line-height: 1.55;
    }

    .hint {
      margin-top: 12px;
      color: var(--njt-secondary);
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

    .alert {
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
      overflow-x: auto;
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

    abbr {
      text-decoration: none;
    }

    .sr-only {
      position: absolute;
      width: 1px;
      height: 1px;
      padding: 0;
      margin: -1px;
      overflow: hidden;
      clip: rect(0, 0, 0, 0);
      white-space: nowrap;
      border: 0;
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

    tbody tr:focus-visible {
      outline: 2px solid var(--njt-focus);
      outline-offset: -2px;
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

      th {
        padding-right: 4px;
      }

      td {
        font-size: 0.9rem;
        padding: 9px 4px 9px 0;
      }

      .board .pill {
        font-size: 0.72rem;
        padding: 2px 7px;
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

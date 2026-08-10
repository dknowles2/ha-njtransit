/** Whole minutes from `now` until `when`, negative once it has passed. */
export function minutesUntil(when: Date, now: Date): number {
  return Math.round((when.getTime() - now.getTime()) / 60000);
}

// NJ Transit publishes and announces in 12-hour time, and a board that reads
// `18:31` next to a platform sign reading `6:31 PM` is a small thing to have
// to translate while running for a train. The locale is pinned for that
// reason rather than by omission.
const CLOCK = new Intl.DateTimeFormat("en-US", {
  hour: "numeric",
  minute: "2-digit",
  hour12: true,
});

/** `6:31 PM`, the way the departure board writes it. */
export function formatClock(when: Date): string {
  return CLOCK.format(when);
}

/** `6:31`, for the board table where the column header carries the context. */
export function formatShortClock(when: Date): string {
  return CLOCK.format(when).replace(/\s?[AP]M$/i, "");
}

/** The hero's headline: a bare number, or a word when counting is over. */
export function countdown(minutes: number): { value: string; unit: boolean } {
  if (minutes < 0) {
    return { value: "Departed", unit: false };
  }
  if (minutes === 0) {
    return { value: "Now", unit: false };
  }
  return { value: String(minutes), unit: true };
}

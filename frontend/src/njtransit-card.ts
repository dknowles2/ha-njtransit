import "./departures-card.js";

// What the "Add card" dialog shows. Without this the card exists but can only
// be added by typing `custom:njtransit-departures` into the YAML editor.
interface CustomCardEntry {
  type: string;
  name: string;
  description: string;
  preview?: boolean;
  documentationURL?: string;
}

declare global {
  interface Window {
    customCards?: CustomCardEntry[];
  }
}

window.customCards = window.customCards ?? [];
window.customCards.push({
  type: "njtransit-departures",
  name: "NJ Transit departures",
  description:
    "The next train out, why it might not be the one you want, and the " +
    "board behind it.",
  preview: true,
  documentationURL: "https://github.com/dknowles2/ha-njtransit",
});

// The integration loads this file on every page, so it says so once. A card
// that appears with no explanation of where it came from is worse than a
// line in the console.
// eslint-disable-next-line no-console
console.info("%c NJ TRANSIT %c card loaded ", "font-weight:700", "");

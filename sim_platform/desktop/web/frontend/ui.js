const icons = {
  data: "M4 5c0-3 16-3 16 0s-16 3-16 0Zm0 0v14c0 3 16 3 16 0V5M4 12c0 3 16 3 16 0",
  flask:
    "M9 3h6m-5 0v6l-6 10c-.5 1 .2 2 1.4 2h13.2c1.2 0 1.9-1 1.4-2L14 9V3M7 15h10",
  chart: "M4 3v17h17M7 14l4-5 4 3 5-7",
  agent: "M8 4h8v3H8zM5 7h14v13H5zM2 11v5m20-5v5M9 11v2m6-2v2m-6 4h6",
  plus: "M12 5v14M5 12h14",
  search: "M16 16l5 5M18 10A8 8 0 1 1 2 10a8 8 0 0 1 16 0",
  settings:
    "m9 3 6 0 .7 3 2 1.2 2.8-.8 3 5-2.2 2v2.2l2.2 2-3 5-2.8-.8-2 1.2-.7 3H9l-.7-3-2-1.2-2.8.8-3-5 2.2-2v-2.2l-2.2-2 3-5 2.8.8 2-1.2zM15 13a3 3 0 1 1-6 0 3 3 0 0 1 6 0",
  sun: "M12 8a4 4 0 1 1 0 8 4 4 0 0 1 0-8M12 2v2m0 16v2M2 12h2m16 0h2M5 5l1.5 1.5m11 11L19 19M5 19l1.5-1.5m11-11L19 5",
  chevron: "m9 5 7 7-7 7",
  down: "m6 9 6 6 6-6",
  file: "M6 2h8l5 5v15H6zM14 2v6h5M9 12h7m-7 4h7",
  refresh: "M20 8a8 8 0 1 0 .5 7M20 3v5h-5",
  upload: "M12 16V3m-5 5 5-5 5 5M4 16v5h16v-5",
  check: "m5 12 4 4L19 6",
  clock: "M12 3a9 9 0 1 0 0 18 9 9 0 0 0 0-18M12 7v5l3 2",
  play: "m8 4 12 8-12 8z",
  pause: "M8 5v14M16 5v14",
  stop: "M5 5h14v14H5z",
  arrow: "M4 12h16m-6-6 6 6-6 6",
  back: "M20 12H4m6-6-6 6 6 6",
  info: "M12 3a9 9 0 1 0 0 18 9 9 0 0 0 0-18M12 11v6m0-10v1",
  close: "m6 6 12 12M6 18 18 6",
  panel: "M3 4h18v16H3zM15 4v16",
  download: "M12 3v13m-5-5 5 5 5-5M4 17v4h16v-4",
  folder: "M3 6h7l2 2h9v12H3z",
  dots: "M5 12h.1M12 12h.1M19 12h.1",
  filter: "M4 6h16M7 12h10m-7 6h4",
  keyboard:
    "M2 5h20v14H2zM5 9h1m3 0h1m3 0h1m3 0h1M5 13h1m3 0h1m3 0h1m3 0h1M7 16h10",
  shield: "m12 2 8 4v6c0 5-8 10-8 10S4 17 4 12V6zM8 12l3 3 5-6",
};
export const icon = (name) =>
  `<svg class="icon" viewBox="0 0 24 24" aria-hidden="true"><path d="${icons[name] || icons.file}"/></svg>`;
export const esc = (s) =>
  String(s).replace(
    /[&<>"']/g,
    (c) =>
      ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[
        c
      ],
  );
export const money = (n) =>
  n.toLocaleString("en-US", {
    style: "currency",
    currency: "USD",
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  });

export const button = (label, action, style = "", symbol = "", extra = "") =>
  `<button type="button" class="${style}" data-action="${action}" ${extra}>${symbol ? icon(symbol) : ""}${label}</button>`;
/* Market time counts nanoseconds from midnight of a recording's first day. A recording
   prepared from price bars also knows that first day's date; setCalendar() is called
   with it before a page renders, so times can be shown with their real date. */
const DAY_NS = 86400e9;
let calendar = null;
export function setCalendar(value) {
  calendar = value?.start_date ? value : null;
}
export const isDaily = () => calendar?.timeframe === "1d";
const pad = (n) => String(n).padStart(2, "0");

/* The calendar date of a market time as MM-DD, or "D+n" when no date is known. */
export function day(ns) {
  const index = Math.floor(Number(ns) / DAY_NS);
  if (!calendar) return index ? "D+" + index : "";
  const [y, m, d] = calendar.start_date.split("-").map(Number);
  const date = new Date(Date.UTC(y, m - 1, d + index));
  return pad(date.getUTCMonth() + 1) + "-" + pad(date.getUTCDate());
}
/* Time within its day, as HH:MM:SS. */
export const clock = (ns) => {
  const seconds = Math.floor((Number(ns) % DAY_NS) / 1e9);
  return [Math.floor(seconds / 3600), Math.floor(seconds / 60) % 60, seconds % 60]
    .map(pad)
    .join(":");
};
/* A market time for display: HH:MM:SS, with the date in front when it is known or
   when the recording has run past its first day. */
export const time = (ns) => {
  if (ns == null) return "—";
  const date = day(ns);
  return (date ? date + " " : "") + clock(ns);
};
/* A short chart-axis label: the date for daily bars, otherwise HH:MM. */
export const axisLabel = (ns) => (isDaily() ? day(ns) : clock(ns).slice(0, 5));
/* How a price-bar recording's synthetic book is set up, for any view that names it. */
export function bookLabel(book) {
  return book
    ? `Synthetic book · ${usd(book.spread)} spread · ${book.depth} levels per side`
    : "";
}
export const usd = (value) =>
  value == null
    ? "—"
    : (Number(value) / 10000).toLocaleString("en-US", {
        style: "currency",
        currency: "USD",
        minimumFractionDigits: 2,
        maximumFractionDigits: Number(value) % 100 === 0 ? 2 : 4,
      });
export const size = (bytes) => {
  for (const [unit, scale] of [
    ["TB", 1e12],
    ["GB", 1e9],
    ["MB", 1e6],
    ["KB", 1e3],
  ])
    if (bytes >= scale) return (bytes / scale).toFixed(2) + " " + unit;
  return bytes + " bytes";
};
export const field = (label, name, value = "", type = "text", extra = "") =>
  `<div class="field"><label for="${name}">${label}</label><input id="${name}" name="${name}" type="${type}" value="${esc(value)}" ${extra}></div>`;
export const select = (label, name, options, value, extra = "") =>
  `<div class="field"><label for="${name}">${label}</label><select id="${name}" name="${name}" ${extra}>${options.map(([id, title]) => `<option value="${esc(id)}" ${String(id) === String(value) ? "selected" : ""}>${esc(title)}</option>`).join("")}</select></div>`;
export const head = (title, actions = "") =>
  `<div class="page-head"><h1>${esc(title)}</h1><div class="page-actions">${actions}</div></div>`;
export const table = (headers, rows) =>
  `<div class="table-scroll"><table><thead><tr>${headers.map((h) => `<th>${esc(h)}</th>`).join("")}</tr></thead><tbody>${rows.join("")}</tbody></table></div>`;
export const empty = (title, description, action = "") =>
  `<div class="empty-hint"><h2>${title}</h2><p>${description}</p>${action}</div>`;

export function timeToNs(value) {
  const match = /^(\d{2}):(\d{2})(?::(\d{2})(?:\.(\d{1,9}))?)?$/.exec(
    value || "",
  );
  if (
    !match ||
    Number(match[1]) > 23 ||
    Number(match[2]) > 59 ||
    Number(match[3]) > 59
  )
    throw Error(
      "Enter an Eastern Time in HH:MM:SS format (up to 9 fractional digits).",
    );
  return Number(
    BigInt(
      Number(match[1]) * 3600 + Number(match[2]) * 60 + Number(match[3] || 0),
    ) *
      1000000000n +
      BigInt((match[4] || "").padEnd(9, "0")),
  );
}

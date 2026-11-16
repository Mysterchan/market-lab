/* Market-only replay never creates an experiment or simulated account. */
import { button as b, head, field, select, esc, usd, time } from "./ui.js";
export function preview(s) {
  const d = s.drafts.preview || {},
    v = s.sessions.manager?.view || {},
    m = v.market || {};
  const ready =
    !s.previewLoading &&
    !!m.symbol &&
    !v.run &&
    v.preparation?.status !== "preparing";
  const playing = m.status === "playing";
  return (
    head(
      "Market preview",
      b("Records", "preview-records", "quiet", "file") +
        b("Market data", "data", "quiet", "back"),
    ) +
    `<div class="content page-pad"><form id="preview-form"><div class="field-pair">${select(
      "Recording",
      "path",
      (s.datasets.files || [])
        .filter((f) => f.status === "Available")
        .map((f) => [f.path, f.name]),
      d.path,
    )}${field("Symbol", "symbol", d.symbol || "AAPL", "text", "required")}</div><details class="advanced-section"><summary>Start and additional data</summary>${select(
      "Start at",
      "start_mode",
      [
        ["first_book", "First available bid and ask"],
        ["regular", "Regular session · 09:30 ET"],
        ["custom", "Custom time"],
        ["beginning", "Beginning of recording"],
      ],
      d.start_mode || "first_book",
    )}${field("Custom time · ET", "start_time", d.start_time || "09:30:00", "text")}${field("Announcements file · optional", "news", d.news || "")}<label class="choice-card"><input name="allow_implicit_eof" type="checkbox" ${d.allow_implicit_eof ? "checked" : ""}>Allow complete S/C ending</label></details><div class="form-actions"><button class="primary" ${s.previewLoading ? "disabled" : ""}>${s.previewLoading ? "Preparing…" : "Load market"}</button>${b("Cancel preparation", "preview-cancel", "quiet", "", s.previewLoading ? "" : "hidden")}</div></form><div class="preview-controls">${b(playing ? "Pause" : "Play", playing ? "preview-pause" : "preview-play", "secondary", playing ? "pause" : "play", ready ? "" : "disabled")}${b("Step", "preview-step", "quiet", "chevron", ready ? "" : "disabled")}<span class="muted">No account or experiment is created.</span></div><div class="preview-price"><strong>${esc(m.symbol || "—")} ${usd(m.bids?.[0]?.[0])}</strong><span class="push muted mono">${time(m.time_ns)} ET</span></div><div class="chart-host"><svg class="m-price-chart" role="img" aria-label="Historical market preview"></svg><div class="m-chart-tooltip" hidden></div></div><div class="preview-book">${["bids", "asks"].map((side) => `<section><h3>${side === "bids" ? "Bid" : "Ask"}</h3>${(m[side] || []).map(([price, quantity]) => `<div class="meta-row"><span>${usd(price)}</span><span>${quantity} shares</span></div>`).join("")}</section>`).join("")}</div></div>`
  );
}
export function updatePreview(root, s, chart) {
  const template = document.createElement("template");
  template.innerHTML = preview(s);
  for (const selector of [
    ".preview-controls",
    ".preview-price",
    ".preview-book",
  ]) {
    const old = root.querySelector(selector),
      next = template.content.querySelector(selector);
    if (old && next && old.innerHTML !== next.innerHTML) old.replaceWith(next);
  }
  const button = root.querySelector("#preview-form .primary");
  if (button) {
    button.disabled = !!s.previewLoading;
    button.textContent = s.previewLoading ? "Preparing…" : "Load market";
  }
  const cancel = root.querySelector('[data-action="preview-cancel"]');
  if (cancel) cancel.hidden = !s.previewLoading;
  chart?.setBars(s.sessions.manager?.view.bars || []);
}

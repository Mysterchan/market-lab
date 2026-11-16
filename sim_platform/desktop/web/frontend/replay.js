/* Verification is Python-owned; these controls only select recorded events. */
import {
  esc,
  icon,
  button as b,
  head,
  field,
  table,
  time,
  usd,
  select,
} from "./ui.js";
function eventDescription(event) {
  const d = event.data || {};
  if (d.quantity != null && d.price != null)
    return `${d.quantity} shares · ${usd(d.price)}`;
  if (d.intent)
    return `${esc(d.intent.side)} ${d.intent.quantity} shares · ${usd(d.intent.limit_price)}`;
  return esc(d.reason || d.status || "Recorded financial event");
}
export function replay(s) {
  const events = s.replay?.events || [],
    index = s.replayIndex || 0,
    event = events[index];
  return (
    head("Recorded Replay") +
    `<div class="content"><div class="replay-source"><form id="replay-form"><div class="replay-path">${field("Operation log", "path", s.replayPath || "", "text", "required")}${select("Destination account", "account", [["", "New account · original starting cash"], ...(s.catalog.accounts || []).map((a) => [a.id, a.name])], s.replayAccount || "")}${b("Choose log", "pick:replay", "secondary", "folder")}<button class="primary" ${s.replayBusy ? "disabled" : ""}>${s.replayBusy ? "Verifying…" : "Verify & open"}</button>${s.replayBusy ? b("Cancel verification", "cancel-replay", "quiet") : ""}</div></form></div>${
      s.replay
        ? `<div class="replay-result"><div class="result-banner">${icon("shield")}<span>${s.replay.events_verified ? "Financial events verified" : "Final account matched"}<span class="muted"> · ${s.replay.commands} commands · No model calls</span></span></div><div class="toolbar replay-controls">${b(s.replayPlaying ? "Pause" : "Play", s.replayPlaying ? "replay-pause" : "replay-play", "secondary", s.replayPlaying ? "pause" : "play", !events.length ? "disabled" : "")}${b("Next event", "replay-next", "quiet", "chevron", index >= events.length - 1 ? "disabled" : "")}<select id="replay-speed" aria-label="Replay speed">${[1, 2, 5, 20].map((value) => `<option value="${value}" ${(s.replaySpeed || 1) === value ? "selected" : ""}>${value}×</option>`).join("")}</select><span class="push muted mono">${event ? time(event.time_ns) + " ET" : "No recorded events"}</span></div><input aria-label="Replay event" id="replay-index" type="range" min="0" max="${Math.max(0, events.length - 1)}" value="${index}" ${events.length ? "" : "disabled"}><div class="replay-record-layout"><div>${table(
            ["Time · ET", "Event", "Details"],
            events.map(
              (item, i) =>
                `<tr data-action="replay-select:${i}" class="${i === index ? "selected" : ""}"><td class="mono">${time(item.time_ns)}</td><td>${esc(item.kind.replaceAll("_", " "))}</td><td>${eventDescription(item)}</td></tr>`,
            ),
          )}</div><aside class="inspector"><div class="inspector-top">Selected event</div><div class="inspector-body"><h2>${esc(event?.kind.replaceAll("_", " ") || "No events")}</h2><p>${event ? eventDescription(event) : "The final account was verified without event playback."}</p><details class="advanced-section"><summary>Event data</summary><pre class="record-json">${esc(JSON.stringify(event || {}, null, 2))}</pre></details></div></aside></div></div>`
        : '<div class="empty-hint"><h2>Open a recorded experiment</h2><p>Choose a Live operation log to verify and inspect its financial events.</p></div>'
    }</div>`
  );
}

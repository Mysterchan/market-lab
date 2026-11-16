/* Financial, market and decision records use the same published worker view. */
import { esc, button as b, head, select, table, time, usd } from "./ui.js";
const kinds = [
  ["events", "Account events"],
  ["trades", "Market trades"],
  ["bars", "Minute bars"],
  ["news", "Announcements"],
  ["decisions", "Decisions"],
  ["handoffs", "Policy handoffs"],
  ["work", "Agent work / transcript"],
  ["account", "Account snapshot"],
  ["app", "App activity log"],
];
/* A record as saved: without the display label and details Python adds to events. */
const stored = (record) => {
  if (!record || typeof record !== "object") return record;
  const { label, details, ...rest } = record;
  return label === undefined && details === undefined ? record : rest;
};

export function audit(s) {
  const v = s.sessions[s.active]?.view;
  if (!v)
    return (
      head("Experiment records") +
      '<p class="content page-pad">Open an experiment first.</p>'
    );
  const kind = s.auditKinds[s.active] || "events";
  const records =
    kind === "app"
      ? s.notices
      : kind === "work"
        ? (v.activity || []).concat(v.work ? [v.work] : [])
        : kind === "account"
          ? v.account
            ? [v.account]
            : []
          : v[kind] || [];
  const columns = {
    events: ["Time · ET", "Event", "Details"],
    trades: ["Time · ET", "Price", "Shares", "Type"],
    bars: ["Start · ET", "Open", "High", "Low", "Close", "Volume", "Complete"],
    news: ["Available · ET", "Period", "Headline"],
    decisions: ["Time · ET", "Generation", "Status", "Results"],
    handoffs: [
      "Time · ET",
      "Reason",
      "Generation",
      "State",
      "Inherited positions",
    ],
    work: ["Record", "State", "Details"],
    app: ["Time · local", "Status", "Message"],
    account: ["Cash", "Fees", "Realized P&L", "Positions", "Orders"],
  };
  function cells(item, i) {
    if (kind === "app")
      return [esc(item.time), esc(item.status), esc(item.message)];
    if (kind === "events")
      return [
        time(item.time_ns),
        esc(item.label || item.kind),
        esc(item.details || item.data?.reason || item.data?.status || ""),
      ];
    if (kind === "trades")
      return [
        time(item.timestamp_ns),
        usd(item.price),
        esc(item.quantity),
        esc(item.kind),
      ];
    if (kind === "bars")
      return [
        time(item.start_ns),
        usd(item.open),
        usd(item.high),
        usd(item.low),
        usd(item.close),
        esc(item.volume),
        item.complete ? "Yes" : "Incomplete",
      ];
    if (kind === "news")
      return [
        time(item.available_time_ns),
        esc(item.period),
        esc(item.headline),
      ];
    if (kind === "decisions")
      return [
        time(item.observation?.market?.time_ns),
        esc(item.generation ?? "—"),
        esc(item.status),
        esc(
          item.error ||
            (item.results || []).map((x) => x.reason || x.status).join(" · "),
        ),
      ];
    if (kind === "handoffs")
      return [
        time(item.time_ns),
        esc(item.reason),
        esc(item.generation),
        esc(item.state_mode),
        esc(JSON.stringify(item.account?.positions || [])),
      ];
    if (kind === "account")
      return [
        usd(item.cash),
        usd(item.fees),
        usd(item.realized_pnl),
        item.positions?.length || 0,
        item.orders?.length || 0,
      ];
    return [
      i + 1,
      esc(item.state || item.status || item.work?.state || "Recorded"),
      esc(item.error || item.phase || "Select to inspect"),
    ];
  }
  const selected = s.auditSelection?.[s.active] || 0;
  return (
    head(
      "Experiment records",
      b(
        "Back to " +
          (v.mode === "saved"
            ? "report"
            : v.run
              ? "workspace"
              : "market preview"),
        v.mode === "saved" ? "results" : v.run ? "workspace" : "market-preview",
        "quiet",
        "back",
      ),
    ) +
    `<div class="content page-pad"><div class="audit-toolbar">${select("Record type", "audit-kind", kinds, kind)}<span class="muted">${records.length} published records</span></div>${table(
      columns[kind],
      records.map(
        (item, i) =>
          `<tr data-action="audit-select:${i}" class="${i === selected ? "selected" : ""}">${cells(
            item,
            i,
          )
            .map((cell) => `<td>${cell}</td>`)
            .join("")}</tr>`,
      ),
    )}${records.length ? `<details class="advanced-section" open><summary>Selected record</summary><p class="tiny-note muted">The saved record as stored. Prices and money are integer USD ten-thousandths: 1000100 is $100.01.</p><pre class="record-json">${esc(JSON.stringify(stored(records[Math.min(selected, records.length - 1)]), null, 2))}</pre></details>` : '<p class="muted">No records of this type.</p>'}</div>`
  );
}

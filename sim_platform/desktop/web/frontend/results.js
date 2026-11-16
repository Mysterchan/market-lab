/* Archived results are presented as a report. Python supplies every figure; the
   equity curve is cash plus the position marked at each archived bar's close. */
import { esc, icon, button as b, head, usd, time, table, bookLabel } from "./ui.js";
import { focusedView, symbolsOf } from "./session.js";

const tone = (value) =>
  value == null ? "" : value > 0 ? "positive" : value < 0 ? "negative" : "";
const percent = (ratio) =>
  (ratio > 0 ? "+" : ratio < 0 ? "−" : "") +
  Math.abs(ratio * 100).toFixed(2) +
  "%";
const label = (text) => (text ? text[0].toUpperCase() + text.slice(1) : "—");

function equityMarkup(performance, w, h) {
  const series = performance.series,
    base = performance.initial_cash,
    values = series.map(([, value]) => value);
  const left = 4,
    right = w - 78,
    top = 14,
    bottom = h - 26;
  let low = Math.min(base, ...values),
    high = Math.max(base, ...values);
  const pad = Math.max(5000, (high - low) * 0.2);
  low -= pad;
  high += pad;
  const t0 = series[0][0],
    t1 = series.at(-1)[0];
  const x = (stamp) =>
    left + ((stamp - t0) / Math.max(1, t1 - t0)) * (right - left);
  const y = (value) => bottom - ((value - low) / (high - low)) * (bottom - top);
  const money = (value) =>
    (value / 10000).toLocaleString("en-US", {
      minimumFractionDigits: 2,
      maximumFractionDigits: 2,
    });
  const line = series
    .map(
      ([stamp, value], i) =>
        `${i ? "L" : "M"}${x(stamp).toFixed(1)} ${y(value).toFixed(1)}`,
    )
    .join("");
  const final = values.at(-1),
    kind = final >= base ? "up" : "down";
  const rough = (high - low) / 4,
    magnitude = Math.pow(10, Math.floor(Math.log10(rough))),
    unit =
      [1, 2, 2.5, 5, 10].map((n) => n * magnitude).find((n) => n >= rough) ||
      rough;
  let grid = "";
  for (let value = Math.ceil(low / unit) * unit; value <= high; value += unit) {
    const py = y(value);
    grid += `<line class="m-grid" x1="${left}" y1="${py}" x2="${right}" y2="${py}"/>${Math.abs(py - y(final)) > 13 ? `<text x="${right + 8}" y="${py + 3.5}">${money(value)}</text>` : ""}`;
  }
  let ticks = "";
  const count = Math.max(2, Math.min(6, Math.floor((right - left) / 150)));
  for (let i = 0; i <= count; i++) {
    const stamp = t0 + ((t1 - t0) * i) / count;
    ticks += `<text text-anchor="${i === 0 ? "start" : i === count ? "end" : "middle"}" x="${x(stamp)}" y="${h - 7}">${time(stamp).slice(0, 5)}</text>`;
  }
  const dots = (performance.fills || [])
    .filter((f) => f.time_ns >= t0 && f.time_ns <= t1)
    .map((f) => {
      const after = Math.max(
          1,
          series.findIndex(([stamp]) => stamp >= f.time_ns),
        ),
        [ta, va] = series[after - 1],
        [tb, vb] = series[after],
        value =
          tb === ta ? vb : va + ((vb - va) * (f.time_ns - ta)) / (tb - ta);
      return `<circle class="equity-dot ${f.side === "sell" ? "sell" : "buy"}" cx="${x(f.time_ns).toFixed(1)}" cy="${y(value).toFixed(1)}" r="3.5"><title>${f.side === "sell" ? "Sold" : "Bought"} ${f.quantity} @ ${usd(f.price)} · ${time(f.time_ns)}</title></circle>`;
    })
    .join("");
  return {
    kind,
    markup: `<defs><linearGradient id="equity-fill" x1="0" x2="0" y1="0" y2="1"><stop offset="0" class="equity-stop-a"/><stop offset="1" class="equity-stop-b"/></linearGradient></defs>${grid}<path class="equity-area" d="${line}L${x(t1).toFixed(1)} ${bottom}L${left} ${bottom}Z"/><line class="equity-base" x1="${left}" y1="${y(base)}" x2="${right}" y2="${y(base)}"/><path class="equity-line" d="${line}"/>${dots}<rect class="equity-tag" x="${right + 2}" y="${y(final) - 9}" width="72" height="18" rx="2"/><text class="equity-tag-text" x="${right + 8}" y="${y(final) + 3.5}">${money(final)}</text>${ticks}`,
  };
}

/* Draw at the element's real pixel size so text and markers are never stretched. */
export function mountEquity(root, performance) {
  const svg = root.querySelector(".equity-chart");
  if (!svg || !performance?.series || performance.series.length < 2) return null;
  const draw = () => {
    const box = svg.getBoundingClientRect(),
      w = Math.max(260, box.width),
      h = Math.max(160, box.height),
      drawn = equityMarkup(performance, w, h);
    svg.setAttribute("viewBox", `0 0 ${w} ${h}`);
    svg.classList.toggle("up", drawn.kind === "up");
    svg.classList.toggle("down", drawn.kind === "down");
    svg.innerHTML = drawn.markup;
  };
  const observer = new ResizeObserver(draw);
  observer.observe(svg);
  draw();
  return observer;
}

function equityChart(performance) {
  const series = performance?.series;
  if (!series || series.length < 2)
    return '<div class="equity-empty muted">An equity curve needs archived price bars and an account that started without positions.</div>';
  return '<div class="equity"><svg class="equity-chart" role="img" aria-label="Account equity over the archived market window"></svg></div>';
}

const stat = (title, value, note = "", kind = "") =>
  `<div class="stat"><small>${title}</small><strong class="${kind}">${value}</strong>${note ? `<em class="${kind}">${note}</em>` : ""}</div>`;
const section = (title, note = "") =>
  `<div class="section-head"><h2>${title}</h2><span class="push muted">${note}</span></div>`;
const meta = (name, value) =>
  `<div class="meta-row"><span>${name}</span><span>${value}</span></div>`;

/* A market-time span as days, hours and minutes; the value itself comes from Python. */
function duration(ns) {
  if (ns == null) return "—";
  const minutes = Math.round(ns / 60e9),
    days = Math.floor(minutes / 1440),
    hours = Math.floor((minutes % 1440) / 60);
  if (days) return `${days}d ${hours}h`;
  if (hours) return `${hours}h ${minutes % 60}m`;
  return minutes ? `${minutes}m` : `${Math.round(ns / 1e9)}s`;
}

/* Sharpe ratio, win rate, turnover and holding time (see the results guide). */
function tradeStats(performance) {
  const trips = performance.round_trips || 0;
  return [
    '<div class="result-stats">',
    stat(
      "Sharpe ratio",
      performance.sharpe_ratio == null ? "—" : performance.sharpe_ratio.toFixed(2),
      performance.sharpe_ratio == null
        ? "Needs 2+ daily returns"
        : performance.sharpe_reliable
          ? "Daily, annualised"
          : `Only ${performance.sharpe_returns} daily returns · not reliable`,
    ),
    stat(
      "Sortino ratio",
      performance.sortino_ratio == null ? "—" : performance.sortino_ratio.toFixed(2),
      performance.sortino_ratio != null
        ? "Downside risk only"
        : (performance.sharpe_returns || 0) < 2
          ? "Needs 2+ daily returns"
          : "Needs a losing day",
    ),
    stat(
      "Win rate",
      performance.win_rate == null ? "—" : percent(performance.win_rate).replace("+", ""),
      `${performance.winning_trips || 0} of ${trips} round trips`,
    ),
    stat(
      "Turnover",
      performance.turnover == null ? "—" : performance.turnover.toFixed(2) + "×",
      "Of starting cash",
    ),
    stat("Avg holding time", duration(performance.average_holding_ns), "Market time"),
    "</div>",
  ].join("");
}

function metrics(packet, view, performance) {
  const summary = view.summary || {},
    value = packet.portfolio?.[0],
    pnl = packet.portfolio?.[1],
    startCash = view.run.initial_snapshot?.cash,
    realized = summary.run_realized_pnl,
    drawdown = performance.max_drawdown,
    shares = performance.traded_shares;
  return [
    '<div class="result-stats">',
    stat("Portfolio value", usd(value)),
    stat(
      "Net P&L",
      usd(pnl),
      pnl != null && startCash ? percent(pnl / startCash) : "",
      tone(pnl),
    ),
    stat("Realized P&L", usd(realized), "", tone(realized)),
    stat(
      "Max drawdown",
      drawdown == null ? "—" : usd(drawdown ? -drawdown : 0),
      drawdown ? percent(-performance.max_drawdown_ratio) : "",
      drawdown ? "negative" : "",
    ),
    stat(
      "Fills",
      summary.fill_count ?? (performance.fills || []).length,
      shares ? shares.toLocaleString("en-US") + " shares" : "",
    ),
    stat("Fees", usd(summary.run_fees ?? view.account?.fees)),
    "</div>",
  ].join("");
}

function priceSection(view, shown) {
  const legend =
    '<span class="legend-key"><i class="key buy"></i>Buy<i class="key sell"></i>Sell</span>';
  // Several symbols: choose which symbol's archived bars and executions the chart shows.
  const symbols = symbolsOf(view);
  const choose = symbols.length
    ? `<div class="segmented symbol-switch" role="group" aria-label="Shown symbol">${symbols
        .map((symbol) =>
          b(esc(symbol), "symbol:" + symbol, symbol === shown.market?.symbol ? "active" : ""),
        )
        .join("")}</div>`
    : "";
  view = shown;
  const chart = (view.bars || []).length
    ? '<div class="chart-host result-chart"><svg class="m-price-chart" role="img" aria-label="Archived market price with executions"></svg><div class="m-chart-tooltip" hidden></div></div>'
    : '<div class="equity-empty muted">No price bars were archived for this run.</div>';
  return (
    section("Price & executions", `<span class="price-section-tools">${choose}${legend}</span>`) +
    chart
  );
}

function fillsSection(performance, several = false) {
  const fills = performance.fills || [];
  if (!fills.length)
    return (
      section("Fills") +
      '<p class="muted">No orders were filled in this experiment.</p>'
    );
  const cell = (value) => `<td class="num">${value}</td>`;
  const rows = fills.map((f) => {
    const selling = f.side === "sell";
    return [
      "<tr>",
      cell(time(f.time_ns)),
      several ? `<td><strong>${esc(f.symbol || "")}</strong></td>` : "",
      `<td><span class="side ${selling ? "sell" : "buy"}">${selling ? "Sell" : f.side === "buy" ? "Buy" : "—"}</span></td>`,
      cell(f.quantity),
      cell(usd(f.price)),
      cell(usd(f.quantity * f.price)),
      cell(usd(f.fee)),
      "</tr>",
    ].join("");
  });
  const headers = several
    ? ["Time · ET", "Symbol", "Side", "Shares", "Price", "Value", "Fee"]
    : ["Time · ET", "Side", "Shares", "Price", "Value", "Fee"];
  return (
    section("Fills", usd(performance.traded_notional) + " traded") +
    `<div class="result-table">${table(headers, rows)}</div>`
  );
}

function timelineSection(view) {
  // Market ticks would bury the decisions; show the latest account events only.
  const events = (view.events || [])
    .filter((event) => event.kind !== "execution_tick")
    .slice(-30);
  const items = events
    .map(
      (event) =>
        `<li><time>${time(event.time_ns)}</time><div><strong>${eventTitle(event)}</strong><small>${eventDetail(event)}</small></div></li>`,
    )
    .join("");
  return (
    section("Decision & trade record", `Eastern Time · latest ${events.length}`) +
    (events.length
      ? `<ul class="timeline">${items}</ul>`
      : '<p class="muted">No recorded actions in this experiment.</p>')
  );
}

/* Agent runs: each order with the reads, model replies and notes of its round. */
function decisionsSection(view) {
  const orders = view.agent_decisions;
  if (!orders) return "";
  const rows = orders
    .map((order) => {
      const reads = (order.reads || [])
        .slice(-5)
        .map((read) => `<li>${esc(read.summary)}</li>`)
        .join("");
      const notes = [...(order.notes_before || []), ...(order.notes_after || [])]
        .map((note) => `<blockquote>${esc(note)}</blockquote>`)
        .join("");
      const fills = (order.fills || []).length
        ? `${order.fills.length} fill${order.fills.length > 1 ? "s" : ""}`
        : "no fill";
      return `<li class="decision"><time>${time(order.time_ns)}</time><div><strong>${esc(order.summary)}</strong><small>${order.model_replies} model ${order.model_replies === 1 ? "reply" : "replies"} · ${fills}</small>${reads ? `<ul class="decision-reads">${reads}</ul>` : '<small class="muted">No data read before this order in its round.</small>'}${notes}</div></li>`;
    })
    .join("");
  return (
    section(
      "Agent decisions",
      "Each order with what the Agent read earlier in the same round · order of events, not proof of use",
    ) +
    (orders.length
      ? `<ul class="timeline decisions">${rows}</ul>`
      : '<p class="muted">The Agent placed no orders.</p>')
  );
}

function details(view, account) {
  const run = view.run,
    market = view.market || {},
    summary = view.summary || {};
  // Show the file name; the full path stays available as a tooltip.
  // An experiment on several symbols names each symbol and each recording.
  const universe = run.universe || [],
    paths = view.source?.paths || [view.source?.path || ""];
  const path = paths.map(String).join("\n"),
    file = paths.map((p) => String(p).split(/[\\/]/).at(-1)).join(", ");
  // Several symbols: one ending position per symbol.
  const position = symbolsOf(view).length
    ? (view.account?.positions || [])
        .map((p) => `${esc(p.symbol)} ${p.quantity.toLocaleString("en-US")}`)
        .join(" · ") || "0"
    : (summary.position_quantity ?? 0).toLocaleString("en-US");
  return [
    '<aside class="inspector"><div class="inspector-top">Experiment details</div>',
    '<div class="inspector-body"><h2>Setup</h2><div class="metadata">',
    meta(
      "Market",
      esc(universe.length > 1 ? universe.join(", ") : market.symbol || "—"),
    ),
    meta("Account", esc(account?.name || "—")),
    meta("Starting cash", usd(run.initial_snapshot?.cash)),
    meta("Ending cash", usd(view.account?.cash)),
    meta("Ending position", position + " shares"),
    meta("Ended at · ET", time(market.time_ns || run.time_ns)),
    "</div>",
    `<div class="section-rule"><h3>Recording</h3><p class="recording-file" title="${esc(path)}">${icon("file")}${esc(file || "Not recorded")}</p>${view.source?.prepared?.calendar?.synthetic_book ? `<p>${esc(bookLabel(view.source.prepared.calendar.synthetic_book))}</p>` : ""}</div>`,
    `<div class="section-rule"><h3>Valuation</h3><p>Portfolio value uses the last observed best bid${symbolsOf(view).length ? " of each symbol" : ""}. It is an estimate, not a guaranteed liquidation price.</p></div>`,
    verifySection(run, view),
    "</div></div></aside>",
  ].join("");
}

/* How this run can be checked again: replay of a Controlled run, or of a Live/Agent log. */
function verifySection(run, view) {
  const live = view.execution_mode === "live" || run.config?.agent;
  return live
    ? '<div class="section-rule"><h3>Verify this run</h3><p>Check the saved Live or Agent operation log by executing its recorded operations again, without the Agent or model.</p>' +
        b("Open recorded Replay", "replay", "secondary", "shield")
    : '<div class="section-rule"><h3>Verify this run</h3><p>Repeat the recorded decisions against the same market data and compare every fill and balance with the saved result.</p>' +
        b("Verify run", "verify-run:" + run.id, "secondary", "shield");
}

export function results(s) {
  const packet = s.sessions.manager,
    view = packet?.view;
  if (!view?.run)
    return (
      head("Results") +
      '<div class="empty-hint">Select a completed experiment to inspect its results.</div>'
    );
  const run = view.run,
    market = view.market || {},
    performance = packet.performance || {};
  const account = (s.catalog.accounts || []).find(
    (account) => account.id === run.account_id,
  );
  const name = account?.name || market.symbol + " research session";
  const status = view.summary?.status || run.status;
  const actions =
    b("Inspect records", "audit", "secondary", "file") +
    b("Export report", "export-results", "secondary", "download") +
    b("New from this setup", "reuse-result", "primary", "plus");
  return [
    head(name, actions),
    '<div class="result-subtitle">',
    `<span class="badge">${esc(label(status))}</span>`,
    `<span>${esc((run.universe || []).length > 1 ? run.universe.join(", ") : market.symbol || "")} · read-only result</span>`,
    `<span class="mono">${esc(run.id.slice(0, 8))}</span>`,
    "</div>",
    '<div class="result-grid"><section class="result-body">',
    metrics(packet, view, performance),
    tradeStats(performance),
    section(
      "Equity",
      (symbolsOf(view).length
        ? "Cash plus every position, each at its symbol's latest bar close"
        : "Cash plus position marked at each bar's close") + " · dashed line: starting cash",
    ),
    equityChart(performance),
    priceSection(view, focusedView(s, "manager")),
    fillsSection(performance, symbolsOf(view).length > 0),
    decisionsSection(view),
    timelineSection(view),
    `<details class="advanced-section"><summary>Raw configuration · JSON</summary><pre class="record-json">${esc(JSON.stringify(run.config || {}, null, 2))}</pre></details>`,
    "</section>",
    details(view, account),
    "</div>",
  ].join("");
}

function eventTitle(event) {
  return (
    {
      fill: "Order filled",
      intent: "Order submitted",
      execution_end: "Experiment ended",
      accepted: "Order accepted",
      cancelled: "Order cancelled",
      rejected: "Order rejected",
      handoff: "Decision maker changed",
    }[event.kind] || event.kind.replaceAll("_", " ")
  );
}
function eventDetail(event) {
  const d = event.data || {};
  if (event.kind === "fill")
    return `${d.quantity} shares · ${usd(d.price)} · ${usd(d.fee)} fees`;
  if (d.intent)
    return `${esc(d.intent.side)} ${d.intent.quantity} shares · limit ${usd(d.intent.limit_price)}`;
  return esc(d.reason || "Recorded account event");
}

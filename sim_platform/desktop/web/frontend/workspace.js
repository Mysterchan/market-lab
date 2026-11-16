import {
  policyLabel,
  paceLabel,
  runStatus,
  isPlaying,
  focusedView,
  symbolsOf,
} from "./session.js";
/* Render published market/account snapshots. The browser never advances a clock. */
import {
  esc,
  icon,
  button as b,
  table,
  field,
  select,
  empty,
  usd,
  time,
  isDaily,
  bookLabel,
} from "./ui.js";
const OPEN = ["accepted", "partial"];
const ORDER_STATUS = {
  accepted: "Working",
  partial: "Partially filled",
  filled: "Filled",
  cancelled: "Cancelled",
  rejected: "Rejected",
};
const reference = (o) =>
  (o.intent_id || o.order_id || "").split(":intent:").at(-1);
const sideCell = (side) =>
  `<span class="side ${side === "sell" ? "sell" : "buy"}">${side === "sell" ? "Sell" : side === "buy" ? "Buy" : "—"}</span>`;
const signed = (value) =>
  value == null ? "" : value > 0 ? "positive" : value < 0 ? "negative" : "";
const percent = (ratio) =>
  (ratio > 0 ? "+" : ratio < 0 ? "−" : "") +
  Math.abs(ratio * 100).toFixed(2) +
  "%";

/* Display marks for the chart: published fills and the account's working orders. */
export function chartOverlay(packet, symbol = null) {
  const orders = packet?.view?.account?.orders || [];
  // On several symbols the chart shows one symbol, so only its fills and orders.
  const mine = (item) => !symbol || item.symbol === symbol;
  return {
    fills: (packet?.performance?.fills || []).filter(mine),
    orders:
      packet?.view?.mode === "saved"
        ? []
        : orders
            .filter((o) => OPEN.includes(o.status) && mine(o))
            .map((o) => ({
              price: o.limit_price,
              side: o.side,
              quantity: o.quantity - o.filled,
            })),
  };
}

function depth(market) {
  const asks = (market.asks || []).slice(0, 5),
    bids = (market.bids || []).slice(0, 5);
  if (!asks.length && !bids.length)
    return '<p class="tiny-note muted">No displayed quotes at this time.</p>';
  const most = Math.max(1, ...asks.concat(bids).map(([, qty]) => qty));
  const row = (kind, [price, qty]) =>
    `<button type="button" class="depth-row ${kind}" data-action="price:${price}" title="Use ${usd(price)} as the limit price"><span class="depth-bar" style="width:${Math.max(2, (qty / most) * 100)}%"></span><span class="depth-price">${usd(price)}</span><span class="depth-size">${qty.toLocaleString("en-US")}</span></button>`;
  const spread =
    asks.length && bids.length
      ? `<div class="depth-spread"><span>Spread</span><span>${usd(asks[0][0] - bids[0][0])}</span></div>`
      : "";
  return `<div class="depth-head"><span>Price · USD</span><span>Shares</span></div>${asks
    .slice()
    .reverse()
    .map((level) => row("ask", level))
    .join("")}${spread}${bids.map((level) => row("bid", level)).join("")}`;
}

/* Everything one render needs, read once from the published packet. The section
   functions below only format these values; none of them computes a financial result. */
function context(s) {
  const id = s.active,
    packet = s.sessions[id],
    // On several symbols: the shown symbol's book, bars and trades.
    view = focusedView(s, id),
    run = view.run || {},
    account = view.account || {},
    execution = view.execution || {},
    market = view.market,
    bars = view.bars || [],
    orders = account.orders || [],
    positions = account.positions || [];
  const pipeline = view.pipeline?.state;
  const last =
    (view.trades || []).at(-1)?.price ??
    bars.at(-1)?.close ??
    market.bids?.[0]?.[0];
  return {
    s,
    id,
    packet,
    view,
    run,
    account,
    execution,
    market,
    bars,
    orders,
    positions,
    last,
    events: view.events || [],
    fills: packet.performance?.fills || [],
    ticket: s.tickets[id] || { quantity: 10, limit_price: "", side: "buy" },
    readOnly: view.mode === "saved",
    several: symbolsOf(view).length > 0,
    // The experiment can still accept playback commands and orders.
    running:
      execution.status === "running" ||
      ["running", "paused", "ready"].includes(pipeline),
    playing: isPlaying(view),
    // Live and Agent experiments run at 1x; only controlled playback has a speed.
    fixedSpeed: view.execution_mode === "live" || view.mode === "agent",
  };
}

function toolbar(c) {
  const { s, id, view, market, packet, running, playing, fixedSpeed } = c;
  const status = runStatus(view),
    live = status === "Running";
  const speed = fixedSpeed ? "1" : s.speeds?.[id] || "1";
  const speeds = [
    ["max", "Max"],
    ["1", "1×"],
    ["2", "2×"],
    ["5", "5×"],
  ]
    .map(
      ([value, label]) =>
        `<option value="${value}" ${speed === value ? "selected" : ""}>${label}</option>`,
    )
    .join("");
  const playback = c.readOnly
    ? []
    : [
        `<select id="playback-speed" aria-label="Playback speed" title="Playback speed" ${fixedSpeed ? "disabled" : ""}>${speeds}</select>`,
        b(
          playing ? "Pause" : "Play",
          playing ? "pause" : "play",
          "primary",
          playing ? "pause" : "play",
          running ? "" : "disabled",
        ),
        b(
          "Step",
          "step",
          "quiet step-button",
          "chevron",
          'aria-label="Step market" title="Advance one market event" ' +
            (!running || view.execution_mode === "live" ? "disabled" : ""),
        ),
        b(
          "End",
          "stop-confirm",
          "quiet danger",
          "stop",
          'aria-label="End experiment" title="End this experiment" ' +
            (running ? "" : "disabled"),
        ),
      ];
  return [
    '<div class="run-toolbar">',
    `<h1>${esc(packet.name || market.symbol + " research session")}</h1>`,
    `<span class="badge">${esc(policyLabel(view))} · ${esc(paceLabel(view))}</span>`,
    view.source?.prepared?.calendar?.synthetic_book
      ? `<span class="badge">${esc(bookLabel(view.source.prepared.calendar.synthetic_book))}</span>`
      : "",
    `<span class="push badge run-state ${live ? "live" : ""}">${live ? '<span class="dot"></span>' : ""}${status}</span>`,
    b(
      "",
      "inspector:controls",
      "icon-button",
      "settings",
      'aria-label="Decision controls" title="Decision controls"',
    ),
    b("Records", "audit", "quiet", "file"),
    b("Export", "export", "quiet", "download"),
    ...playback,
    "</div>",
  ].join("");
}

function stats(c) {
  const { packet, account, run, market, positions, orders } = c;
  const pnl = packet.portfolio?.[1],
    startCash = run.initial_snapshot?.cash,
    held = positions.find((p) => p.symbol === market.symbol),
    working = orders.filter((o) => OPEN.includes(o.status)).length;
  // Tests read the first two <strong> values; keep the note outside <strong>.
  const stat = (title, value, note = "", tone = "") =>
    `<div class="stat"><small>${title}</small><strong class="${tone}">${value}</strong>${note ? `<em class="${tone}">${note}</em>` : ""}</div>`;
  return [
    '<div class="stats">',
    stat("Portfolio value", usd(packet.portfolio?.[0])),
    stat(
      "Net P&L",
      usd(pnl),
      pnl != null && startCash ? percent(pnl / startCash) : "",
      signed(pnl),
    ),
    stat("Cash balance", usd(account.cash)),
    stat(
      c.several ? "Position · " + esc(market.symbol) : "Position",
      held ? held.quantity.toLocaleString("en-US") : 0,
      held?.quantity ? "avg " + usd(averageCost(held)) : "shares",
    ),
    stat("Working orders", working),
    "</div>",
  ].join("");
}

/* Average cost per share, rounded to cents for display. */
const averageCost = (position) =>
  Math.round(position.cost / position.quantity / 100) * 100;

function chartSection(c) {
  const { s, id, market, bars, last } = c;
  const period = s.periods[id] || 1;
  // Move since the first visible bar's open: a display comparison, not account P&L.
  const opened = bars[0]?.open,
    move = last != null && opened != null ? last - opened : null;
  const moveLabel =
    move == null
      ? ""
      : `<span class="chart-move ${signed(move) || "muted"}">${move >= 0 ? "+" : "−"}${usd(Math.abs(move)).replace("$", "")} (${percent(move / opened)})</span><span class="muted tiny-note">since first bar</span>`;
  return [
    '<div class="chart-head">',
    // Several symbols: a switch that chooses which one the chart, depth and ticket show.
    symbolsOf(c.packet.view).length
      ? `<div class="segmented symbol-switch" role="group" aria-label="Shown symbol">${symbolsOf(
          c.packet.view,
        )
          .map((symbol) =>
            b(esc(symbol), "symbol:" + symbol, symbol === market.symbol ? "active" : ""),
          )
          .join("")}</div>`
      : `<strong>${esc(market.symbol)}</strong>`,
    '<span class="muted">Historical market · USD</span>',
    // Daily bars have one size; intraday data can be grouped into 1 or 5 minutes.
    isDaily()
      ? '<span class="push badge">1D</span></div>'
      : [
          '<div class="segmented push">',
          b("1m", "period:1", period === 1 ? "active" : ""),
          b("5m", "period:5", period === 5 ? "active" : ""),
          "</div></div>",
        ].join(""),
    '<div class="chart-value">',
    `<strong>${usd(last)}</strong>`,
    moveLabel,
    '<span class="muted">Last observed</span>',
    `<span class="push muted mono">${time(market.time_ns)} ET</span>`,
    "</div>",
    `<div class="table-note chart-empty" ${bars.length ? "hidden" : ""}>Price bars appear as the market advances.</div>`,
    '<div class="chart-host"><svg class="m-price-chart" role="img" aria-label="Historical market price chart"></svg><div class="m-chart-tooltip" hidden></div></div>',
  ].join("");
}

const RECORD_HEADERS = {
  orders: ["Order", "Side", "Filled / qty", "Limit", "Status"],
  positions: ["Symbol", "Shares", "Average cost", "Remaining cost"],
  fills: ["Time · ET", "Side", "Shares", "Price", "Value", "Fee"],
  activity: ["Time · ET", "Event", "Details"],
};
const cell = (value, kind = "num") => `<td class="${kind}">${value}</td>`;

function recordRows(c) {
  const canCancel = !c.readOnly && c.running;
  return {
    orders: c.orders.map((o) => {
      const cancel =
        canCancel && OPEN.includes(o.status)
          ? b("Cancel", "cancel:" + esc(reference(o)), "row-action")
          : "";
      return [
        "<tr>",
        cell(esc(reference(o)), "mono-cell"),
        c.several ? `<td><strong>${esc(o.symbol || "")}</strong></td>` : "",
        `<td>${sideCell(o.side)}</td>`,
        cell(`${o.filled} / ${o.quantity}`),
        cell(usd(o.limit_price)),
        `<td><span class="status ${esc(o.status)}">${esc(ORDER_STATUS[o.status] || o.status)}</span> ${cancel}</td>`,
        "</tr>",
      ].join("");
    }),
    positions: c.positions.map((p) =>
      [
        "<tr>",
        `<td><strong>${esc(p.symbol)}</strong></td>`,
        cell(p.quantity),
        cell(p.quantity ? usd(averageCost(p)) : "—"),
        cell(usd(p.cost)),
        "</tr>",
      ].join(""),
    ),
    // Newest first, so the latest fill stays visible without scrolling.
    fills: c.fills
      .slice()
      .reverse()
      .map((f) =>
        [
          "<tr>",
          cell(time(f.time_ns)),
          c.several ? `<td><strong>${esc(f.symbol || "")}</strong></td>` : "",
          `<td>${sideCell(f.side)}</td>`,
          cell(f.quantity),
          cell(usd(f.price)),
          cell(usd(f.quantity * f.price)),
          cell(usd(f.fee)),
          "</tr>",
        ].join(""),
      ),
    activity: c.events
      .slice()
      .reverse()
      .map((e) =>
        [
          "<tr>",
          cell(time(e.time_ns)),
          // Label and details are written by Python (presentation.event_text).
          `<td>${esc(e.label || e.kind.replaceAll("_", " "))}</td>`,
          `<td>${esc(e.details ?? e.data.reason ?? "")}</td>`,
          "</tr>",
        ].join(""),
      ),
  };
}

function records(c) {
  const rows = recordRows(c),
    tab = c.s.recordTabs[c.id] || "orders";
  const tabs = [
    ["orders", "Orders"],
    ["positions", "Positions"],
    ["fills", "Fills"],
    ["activity", "Activity"],
  ]
    .map(([key, label]) =>
      b(
        `${label} <span class="counter">${rows[key].length}</span>`,
        "records:" + key,
        "record-tab " + (tab === key ? "active" : ""),
      ),
    )
    .join("");
  return [
    '<section class="records">',
    `<div class="record-tabs">${tabs}</div>`,
    table(
      // Several symbols: orders and fills name their symbol after the first column.
      c.several && ["orders", "fills"].includes(tab)
        ? [RECORD_HEADERS[tab][0], "Symbol", ...RECORD_HEADERS[tab].slice(1)]
        : RECORD_HEADERS[tab],
      rows[tab],
    ),
    rows[tab].length
      ? ""
      : `<div class="table-note">No ${tab} in this experiment.</div>`,
    "</section>",
  ].join("");
}

function orderTicket(c) {
  const { ticket, market, execution, run, account, view, playing } = c;
  const fee =
    execution.config?.fee_per_share ?? run.config?.execution?.fee_per_share;
  const selling = ticket.side === "sell";
  const isMarket = ticket.order_type === "market";
  // A market order's estimate uses the quote it would meet first; Python prices it.
  const touch = (selling ? market.bids : market.asks)?.[0]?.[0];
  const estimate = isMarket
    ? touch == null
      ? null
      : Number(ticket.quantity) * touch
    : Number(ticket.quantity) * Number(ticket.limit_price) * 10000;
  const available = execution.available_cash ?? account.cash;
  // A Live order needs a running clock; an Agent experiment places its own orders.
  const waitingForPlay = view.execution_mode === "live" && !playing;
  const blocked =
    c.readOnly || !c.running || view.mode === "agent" || waitingForPlay;
  const row = (label, value) =>
    `<div class="meta-row"><span>${label}</span>${value}</div>`;
  return [
    '<form class="ticket" id="order-form">',
    `<div class="ticket-title"><strong>${isMarket ? "Market" : "Limit"} order</strong><span class="push badge">${esc(market.symbol)}</span></div>`,
    '<div class="trade-side">',
    b("Buy", "side:buy", selling ? "" : "active buy"),
    b("Sell", "side:sell", selling ? "active sell" : ""),
    "</div>",
    '<div class="field-pair">',
    field(
      "Quantity · shares",
      "quantity",
      ticket.quantity,
      "number",
      'min="1" step="1" required',
    ),
    select(
      "Order type",
      "order_type",
      [
        ["limit", "Limit"],
        ["market", "Market"],
      ],
      ticket.order_type || "limit",
    ),
    "</div>",
    isMarket
      ? '<p class="tiny-note muted">Fills at the displayed prices, up to 5% beyond the best quote at acceptance.</p>'
      : field(
          "Limit price · USD",
          "limit_price",
          ticket.limit_price,
          "number",
          'min="0.0001" step="0.0001" required',
        ),
    row("Estimated value", `<span id="order-estimate">${usd(estimate)}</span>`),
    row(
      "Estimated fees",
      `<span id="order-fee">${fee == null ? "—" : usd(Number(ticket.quantity) * fee)}</span>`,
    ),
    row("Available cash", `<span data-live-cash>${usd(available)}</span>`),
    '<details class="advanced-section"><summary>Order reference</summary>',
    field(
      "Client order ID · optional",
      "client_id",
      ticket.client_id || "",
      "text",
      'maxlength="128"',
    ),
    "</details>",
    `<button class="primary ${selling ? "sell" : "buy"}" ${blocked ? "disabled" : ""}>${selling ? "Sell" : "Buy"} ${esc(market.symbol)}</button>`,
    view.mode === "agent"
      ? '<p class="tiny-note muted">The Agent controls orders for this experiment.</p>'
      : "",
    `<p class="tiny-note muted live-order-note" ${waitingForPlay ? "" : "hidden"}>Press Play before submitting a Live order.</p>`,
    `<div class="section-rule"><h3>Market depth</h3><div class="market-depth">${depth(market)}</div></div>`,
    "</form>",
  ].join("");
}

function agentActivity(view) {
  const items = (view.decisions || [])
    .map((item) => {
      const detail =
        item.error ||
        (item.results || [])
          .map(
            (result) => result.reason || result.status || "Recorded decision",
          )
          .join(" · ") ||
        "Decision recorded";
      return `<div class="activity-item"><strong>${esc(item.status || "Decision")}</strong><p>${esc(detail)}</p></div>`;
    })
    .join("");
  return `<div class="ticket agent-activity-list"><h2>Agent activity</h2>${items || '<p class="muted tiny-note">No recorded Agent decisions.</p>'}</div>`;
}

function inspector(c) {
  const { s, view } = c;
  let panel;
  if (s.inspector === "controls")
    panel =
      view.execution_mode === "live"
        ? '<div class="ticket"><h2>Decision maker</h2><p class="tiny-note muted">Live authority is fixed for this experiment. Use controlled playback to schedule policy handoffs.</p></div>'
        : c.several && policyLabel(view) === "Manual"
          ? '<div class="ticket"><h2>Decision maker</h2><p class="tiny-note muted">On several symbols the decision maker can only switch to Manual, which already decides here.</p></div>'
          : decisionControls(s, c.readOnly || !c.running);
  else if (s.inspector === "agent" && view.mode === "agent") panel = agentActivity(view);
  else panel = orderTicket(c);
  return [
    '<aside class="inspector"><div class="inspector-tabs">',
    b("Order ticket", "inspector:order", s.inspector === "order" ? "active" : ""),
    // Only an Agent experiment has Agent decisions to show.
    view.mode === "agent"
      ? b("Agent activity", "inspector:agent", s.inspector === "agent" ? "active" : "")
      : "",
    "</div>",
    panel,
    "</aside>",
  ].join("");
}

/* The page is rebuilt from these sections. updateWorkspace() below swaps individual
   sections by class name on each market snapshot, so keep the class names stable. */
export function workspace(s) {
  if (!s.sessions[s.active]?.view?.market)
    return empty(
      "No experiment open",
      "Choose an experiment or create a new one.",
      b("New experiment", "new", "primary", "plus"),
    );
  const c = context(s);
  return [
    toolbar(c),
    '<div class="workspace"><section class="workspace-main">',
    stats(c),
    chartSection(c),
    records(c),
    "</section>",
    inspector(c),
    "</div>",
  ].join("");
}

/* Which handoff fields the chosen policy and switch condition use. */
export function handoffFieldShown(name, policy, condition) {
  if (name === "quantity") return policy === "rule";
  if (name === "hold_seconds") return policy === "rule" || policy === "hold";
  if (name === "condition_value") return condition !== "now";
  return true;
}

function decisionControls(s, disabled) {
  const d = s.handoffDrafts?.[s.active] || {};
  const policy = d.policy || "human",
    condition = d.condition || "now";
  // Every field stays in the form; the ones the chosen policy and condition do not use are
  // hidden, and forms.js toggles them on change. The rule uses a share quantity; the rule and
  // position management use a hold time; a scheduled switch needs a time or a position.
  const wrap = (name, html) =>
    `<div class="handoff-field" data-field="${name}" ${handoffFieldShown(name, policy, condition) ? "" : "hidden"}>${html}</div>`;
  const position = condition === "position_at_least";
  const quantity = wrap(
    "quantity",
    field("Quantity", "quantity", d.quantity || 10, "number", 'min="1"'),
  );
  const hold = wrap(
    "hold_seconds",
    field("Hold · seconds", "hold_seconds", d.hold_seconds || 2, "number", 'min="0"'),
  );
  const when = wrap(
    "condition_value",
    field(
      position ? "Position quantity · shares" : "Time · HH:MM:SS ET",
      "condition_value",
      d.condition_value || "",
      "text",
      `placeholder="${position ? "100" : "09:30:00.125"}"`,
    ),
  );
  return `<form id="handoff-form" class="ticket"><h2>Decision maker</h2><p class="tiny-note muted" style="margin-bottom:14px">Change the policy for subsequent decisions. Existing fills stay in the account.</p>${select(
    "Policy",
    "policy",
    // The rule and position management trade one symbol: several symbols switch to Manual.
    symbolsOf(s.sessions[s.active]?.view).length
      ? [["human", "Manual"]]
      : [
          ["human", "Manual"],
          ["rule", "Buy → Hold → Sell"],
          ["hold", "Manage positions"],
        ],
    policy,
  )}${quantity}${hold}${select(
    "Policy state",
    "state_mode",
    [
      ["reset", "Reset policy state"],
      ["restore", "Restore saved policy state"],
    ],
    d.state_mode || "reset",
  )}${select(
    "Switch when",
    "condition",
    [
      ["now", "Immediately"],
      ["time", "At an Eastern Time"],
      ["position_at_least", "When position reaches quantity"],
    ],
    condition,
  )}${when}<button class="primary" ${disabled ? "disabled" : ""}>Change decision maker</button></form>`;
}

export function updateWorkspace(root, s, chart) {
  const template = document.createElement("template");
  template.innerHTML = workspace(s);
  for (const selector of [
    ".run-toolbar",
    ".stats",
    ".chart-head",
    ".chart-value",
    ".records",
    ".market-depth",
    ".live-order-note",
    ".agent-activity-list",
  ]) {
    const current = root.querySelector(selector),
      next = template.content.querySelector(selector);
    if (current && next && current.innerHTML !== next.innerHTML) {
      const top = current.scrollTop;
      current.replaceWith(next);
      next.scrollTop = top;
    }
  }
  const liveCash = root.querySelector("[data-live-cash]"),
    nextCash = template.content.querySelector("[data-live-cash]");
  if (liveCash && nextCash) liveCash.textContent = nextCash.textContent;
  const submit = root.querySelector("#order-form .primary"),
    nextSubmit = template.content.querySelector("#order-form .primary");
  if (submit && nextSubmit) submit.disabled = nextSubmit.disabled;
  const liveNote = root.querySelector(".live-order-note"), nextNote = template.content.querySelector(".live-order-note");
  if(liveNote && nextNote) liveNote.hidden = nextNote.hidden;
  const focused = focusedView(s, s.active);
  const bars = focused?.bars || [];
  const hint = root.querySelector(".chart-empty");
  if (hint) hint.hidden = bars.length > 0;
  chart?.setBars(bars);
  chart?.setOverlay(
    chartOverlay(s.sessions[s.active], symbolsOf(focused).length ? focused.market.symbol : null),
  );
  const clock = root.querySelector(".footer .push");
  if (clock)
    clock.textContent =
      time(s.sessions[s.active]?.view.market?.time_ns) + " ET";
}

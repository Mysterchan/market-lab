/* Compare runs: choose saved runs, group them by decision maker and evaluate them.

   Python lists finished runs with their group (Agent revision, rule settings, model
   file, manual) and a market key; only runs sharing the first chosen run's market can
   be chosen together. The statistics come from Platform.evaluation, unchanged. */
import { s } from "./state.js";
import { esc, icon, button as b, head, table, field, select, empty, time, usd } from "./ui.js";

const row = (cells, extra = "") => `<tr ${extra}>${cells.map((c) => `<td>${c}</td>`).join("")}</tr>`;

/* Figures offered as the tested metric, with how each is shown. */
export const METRICS = [
  ["total_return", "Total return", "percent"],
  ["sharpe_ratio", "Sharpe ratio", "ratio"],
  ["sortino_ratio", "Sortino ratio", "ratio"],
  ["max_drawdown_ratio", "Max drawdown", "percent"],
  ["win_rate", "Win rate", "percent"],
  ["turnover", "Turnover", "times"],
  ["exposure", "Exposure", "percent"],
  ["round_trips", "Round trips", "count"],
  ["fees", "Fees", "money"],
  ["model_calls", "Model calls", "count"],
  ["failed_calls", "Failed model calls", "count"],
  ["input_tokens", "Input tokens", "tokens"],
  ["output_tokens", "Output tokens", "tokens"],
  ["mean_latency_seconds", "Model reply time", "seconds"],
  ["model_cost", "Model cost", "dollars"],
];
// Figures that only Agent runs have, shown in their own table.
const MODEL_FIGURES = [
  "model_calls",
  "failed_calls",
  "input_tokens",
  "output_tokens",
  "mean_latency_seconds",
  "model_cost",
];
const FORMAT = Object.fromEntries(METRICS.map(([key, , kind]) => [key, kind]));
const LABEL = Object.fromEntries(METRICS.map(([key, label]) => [key, label]));

export function figure(metric, value, signed = false) {
  if (value == null || Number.isNaN(value)) return "—";
  const sign = signed && value > 0 ? "+" : "";
  switch (FORMAT[metric]) {
    case "percent":
      return sign + (value * 100).toFixed(2) + "%";
    case "times":
      return sign + value.toFixed(2) + "×";
    case "count":
      return sign + value.toFixed(1);
    case "money":
      return (signed && value > 0 ? "+" : "") + usd(Math.round(value));
    case "tokens":
      return sign + Math.round(value).toLocaleString();
    case "seconds":
      return sign + value.toFixed(2) + " s";
    case "dollars":
      return sign + "$" + value.toFixed(value < 1 ? 4 : 2);
    default:
      return sign + value.toFixed(2);
  }
}
const interval = (metric, pair, signed = false) =>
  pair ? `${figure(metric, pair[0], signed)} to ${figure(metric, pair[1], signed)}` : "—";

/* The page's own state, kept between visits. */
export const evaluation = () =>
  (s.evaluation ||= {
    candidates: null,
    selected: {},
    metric: "total_return",
    baseline: "",
    cutoffs: {},
    prices: { input: "", output: "" },
    report: null,
    busy: false,
  });

/* Chosen runs grouped into entries by their decision maker's label. */
export function chosenEntries() {
  const e = evaluation();
  const entries = {};
  for (const run of e.candidates?.runs || [])
    if (e.selected[run.id]) (entries[run.label] ||= []).push(run.id);
  return entries;
}
const chosenMarket = () => {
  const e = evaluation();
  return (e.candidates?.runs || []).find((run) => e.selected[run.id])?.market || null;
};

function candidateTable() {
  const e = evaluation();
  const runs = e.candidates?.runs || [];
  const market = chosenMarket();
  const groups = new Map();
  for (const run of runs) {
    if (!groups.has(run.label)) groups.set(run.label, []);
    groups.get(run.label).push(run);
  }
  const rows = [];
  for (const [label, members] of groups) {
    const usable = members.filter((r) => r.comparable && (!market || r.market === market));
    const chosen = members.filter((r) => e.selected[r.id]).length;
    rows.push(
      `<tr class="eval-group"><td colspan="5"><label class="eval-check"><input type="checkbox" data-eval-group="${esc(label)}" ${chosen && chosen === usable.length ? "checked" : ""} ${usable.length ? "" : "disabled"}><strong>${esc(label)}</strong><span class="muted">${members.length} run${members.length === 1 ? "" : "s"}${chosen ? ` · ${chosen} chosen` : ""}</span></label></td></tr>`,
    );
    for (const run of members) {
      const allowed = run.comparable && (!market || run.market === market);
      const reason = !run.comparable
        ? "Started with shares held"
        : allowed
          ? ""
          : "Different market or settings";
      rows.push(
        row(
          [
            `<label class="eval-check eval-run"><input type="checkbox" data-eval-run="${esc(run.id)}" ${e.selected[run.id] ? "checked" : ""} ${allowed ? "" : "disabled"}>${esc(run.account)}<small class="muted">Run ${esc(run.id.slice(0, 8))}</small></label>`,
            esc(run.market_label),
            `<span class="badge">${esc(run.status)}</span>`,
            usd(run.starting_cash),
            reason ? `<span class="muted">${reason}</span>` : time(run.time_ns),
          ],
          allowed ? "" : 'class="eval-disabled"',
        ),
      );
    }
  }
  return table(["Run", "Market", "Status", "Starting cash", "Ended · ET"], rows);
}

function settingsBlock() {
  const e = evaluation();
  const entries = chosenEntries();
  const names = Object.keys(entries);
  const count = Object.values(entries).flat().length;
  const agents = new Set((e.candidates?.runs || []).filter((r) => r.agent && e.selected[r.id]).map((r) => r.label));
  if (e.baseline && !names.includes(e.baseline)) e.baseline = "";
  const cutoffs = [...agents]
    .map((name) =>
      field(
        `Training cutoff · ${esc(name)}`,
        "cutoff:" + name,
        e.cutoffs[name] || "",
        "date",
        'data-eval-cutoff="' + esc(name) + '"',
      ),
    )
    .join("");
  return `<section class="form-block eval-settings"><h2>Test</h2><div class="eval-fields">${select(
    "Metric",
    "eval-metric",
    METRICS.map(([key, label]) => [key, label]),
    e.metric,
    "data-eval-metric",
  )}${select(
    "Baseline",
    "eval-baseline",
    [["", "None · figures only"], ...names.map((n) => [n, n])],
    e.baseline,
    "data-eval-baseline",
  )}</div>${
    cutoffs
      ? `<p class="inline-hint">An Agent's model may remember real prices. Give its training cutoff to check the data's dates against it; leave it empty if unknown.</p>${cutoffs}`
      : ""
  }${
    agents.size
      ? `<p class="inline-hint">Model price, to show each Agent run's cost. Leave empty to show tokens only.</p><div class="field-pair">${field("Input · USD per 1M tokens", "price-input", e.prices.input, "number", 'min="0" step="0.01" data-eval-price="input"')}${field("Output · USD per 1M tokens", "price-output", e.prices.output, "number", 'min="0" step="0.01" data-eval-price="output"')}</div>`
      : ""
  }<div class="form-actions">${b(
    e.busy ? "Evaluating…" : `Evaluate ${count} run${count === 1 ? "" : "s"} in ${names.length} group${names.length === 1 ? "" : "s"}`,
    "run-evaluation",
    "primary",
    "chart",
    count && !e.busy ? "" : "disabled",
  )}${count ? b("Clear selection", "clear-evaluation", "quiet") : ""}</div></section>`;
}

const FLAG_TEXT = {
  disguised: "Disguised data",
  "after-cutoff": "After training cutoff",
  "possible-memorisation": "Model may remember",
  "cutoff-unknown": "Cutoff unknown",
  "dates-unknown": "Dates unknown",
};
function flagSummary(entry) {
  const counts = {};
  for (const run of entry.runs) for (const flag of run.flags) counts[flag] = (counts[flag] || 0) + 1;
  const parts = Object.entries(counts).map(([flag, n]) => {
    const warn = flag === "possible-memorisation" || flag === "cutoff-unknown";
    return `<span class="badge ${warn ? "eval-warn" : ""}">${esc(FLAG_TEXT[flag] || flag)}${entry.runs.length > 1 ? " · " + n : ""}</span>`;
  });
  return parts.join(" ") || '<span class="muted">—</span>';
}
function verdict(comparison) {
  if (!comparison) return '<span class="badge">Baseline</span>';
  if (comparison.deterministic)
    return '<span class="badge" title="Neither group varies between runs, so the difference is exact and no test applies">Exact · no randomness</span>';
  if (comparison.p_value_holm == null) return '<span class="muted">—</span>';
  if (comparison.insufficient) return '<span class="badge">Too few runs</span>';
  return comparison.p_value_holm < 0.05
    ? `<span class="badge ${comparison.difference > 0 ? "eval-up" : "eval-down"}">Significant</span>`
    : '<span class="badge">Not significant</span>';
}

function reportBlock(report) {
  const metric = report.metric;
  const better = report.better?.[metric];
  const main = report.entries.map((entry) => {
    const stats = entry.statistics[metric];
    const c = entry.comparison;
    return row([
      `<strong>${esc(entry.name)}</strong>`,
      String(stats.n) + (stats.missing ? `<small class="muted"> · ${stats.missing} without a value</small>` : ""),
      figure(metric, stats.mean),
      interval(metric, stats.interval),
      c ? figure(metric, c.difference, true) : "—",
      c ? interval(metric, c.interval, true) : "—",
      c?.p_value_holm != null ? c.p_value_holm.toFixed(3) : "—",
      verdict(c),
      flagSummary(entry),
    ]);
  });
  const shown = METRICS.filter(([key]) => key !== "fees" && !MODEL_FIGURES.includes(key));
  const agentEntries = report.entries.filter((entry) => entry.statistics.model_calls.n);
  const usage = agentEntries.map((entry) =>
    row([
      esc(entry.name),
      ...MODEL_FIGURES.map((key) =>
        key === "model_cost" && !report.token_prices
          ? '<span class="muted">No price</span>'
          : figure(key, entry.statistics[key].mean),
      ),
    ]),
  );
  const all = report.entries.map((entry) =>
    row([
      esc(entry.name),
      ...shown.map(([key]) => figure(key, entry.statistics[key].mean)),
      figure("fees", entry.statistics.fees.mean),
    ]),
  );
  const warnings = (report.warnings || [])
    .map((w) => `<li>${icon("info")}${esc(w)}</li>`)
    .join("");
  return `<section class="form-block eval-report"><div class="section-head"><h2>Result · ${esc(LABEL[metric] || metric)}${
    better ? ` <small class="muted">${better} is better</small>` : ""
  }</h2><span class="push">${b("Export report", "export-evaluation", "secondary", "download")}</span></div>${
    warnings ? `<ul class="eval-warnings">${warnings}</ul>` : ""
  }${table(
    ["Group", "Runs", "Mean", "95% interval", "vs baseline", "Interval of difference", "p (Holm)", "", "Memory check"],
    main,
  )}<p class="inline-hint">Intervals are 95% bootstrap intervals of the mean (${report.resamples.toLocaleString()} resamples, seed ${report.seed}). The p-value is two-sided and adjusted for testing several groups. Groups with fewer than ${report.min_reliable_runs} runs are marked “Too few runs”. Repeated runs on one market measure a method's own randomness, not how it would do on other markets.</p><h3>Every figure · mean of each group</h3>${table(
    ["Group", ...shown.map(([, label]) => label), "Fees"],
    all,
  )}${
    usage.length
      ? `<h3>Model usage · mean per run</h3>${table(
          ["Group", ...MODEL_FIGURES.map((key) => LABEL[key])],
          usage,
        )}<p class="inline-hint">Calls include retries. Reply time is from sending a request to receiving the answer${report.token_prices ? `; cost uses $${report.token_prices.input} / $${report.token_prices.output} per million input / output tokens` : ""}.</p>`
      : ""
  }</section>`;
}

export function comparison(s_) {
  const e = evaluation();
  const quick = quickComparison(s_);
  let body;
  if (e.candidates == null) body = `<p class="inline-hint" role="status">Loading saved runs…</p>`;
  else if (!e.candidates.runs.length)
    body = empty(
      "No finished runs yet",
      "Run an Agent, a rule, a model file or manual trading on the same recording, end the runs, then compare them here.",
      b("New experiment", "new", "primary", "plus"),
    );
  else
    body = `<div class="eval-layout"><section class="form-block"><h2>Choose runs</h2><p class="inline-hint">Runs are grouped by what made their decisions. Choose several runs of an Agent and at least one baseline; only runs on the same market with the same cash and execution settings can be chosen together.</p>${candidateTable()}</section>${settingsBlock()}</div>${
      e.report ? reportBlock(e.report) : ""
    }`;
  return (
    head("Compare runs", b("", "refresh-evaluation", "icon-button", "refresh", 'aria-label="Reload saved runs"')) +
    `<div class="content form-content eval-content">${body}<details class="advanced-section quick-compare"${
      s_.comparison || s_.comparing ? " open" : ""
    }><summary>Quick rule comparison</summary>${quick}</details></div>`
  );
}

/* The earlier rule-only comparison, kept for a fast check of the hold time. */
function quickComparison(s_) {
  const d = s_.drafts.comparison || {};
  const row2 = (cells) => `<tr>${cells.map((c) => `<td>${c}</td>`).join("")}</tr>`;
  return `<p class="inline-hint compare-intro">Runs the Buy → Hold → Sell rule twice on the same recording, once holding 1 second and once 2 seconds, each on its own new account with the same cash and quantity, then compares equity, realized P&amp;L, fees and fills.</p><form id="comparison-form"><section class="form-block">${select(
    "Recording",
    "path",
    (s_.datasets.files || []).filter((f) => f.status === "Available").map((f) => [f.path, f.name]),
    d.path,
  )}<div class="field-pair">${field("Symbol", "symbol", d.symbol || "AAPL", "text", "required")}${field("Starting cash · USD", "cash", d.cash || 10000, "number", 'min="0.01" step="0.01" required')}</div>${field("Quantity · shares", "quantity", d.quantity || 10, "number", 'min="1" required')}</section><div class="form-actions"><button class="primary" ${s_.comparing ? "disabled" : ""}>${s_.comparing ? "Comparing…" : "Compare independent strategies"}</button>${s_.comparing ? b("Cancel", "cancel-comparison", "secondary") : ""}</div></form>${
    s_.comparison
      ? `<section class="form-block"><h2>Comparison results</h2>${b("Export comparison", "export-comparison", "secondary", "download")}${table(
          ["Strategy", "Status", "Equity at bid", "Realized P&L", "Fees", "Fills"],
          (s_.comparison.results || []).map((result) =>
            row2([
              esc(result.name),
              esc(result.summary.status),
              usd(result.summary.estimated_equity_at_bid),
              usd(result.summary.run_realized_pnl),
              usd(result.summary.run_fees),
              result.summary.fill_count,
            ]),
          ),
        )}<details class="advanced-section"><summary>Full comparison record</summary><pre class="record-json">${esc(JSON.stringify(s_.comparison, null, 2))}</pre></details></section>`
      : ""
  }`;
}

/* The reference's staged setup, backed by actual dataset and Agent identities. */
import { esc, icon, button as b, head, field, select, usd, bookLabel } from "./ui.js";
export function setupDefaults(s) {
  const files = (s.datasets.files || []).filter(
    (file) => file.status === "Available",
  );
  return {
    path: files[0]?.path || "",
    agent: s.agents.agents?.[0]
      ? s.agents.agents[0].agent_id + ":" + s.agents.agents[0].revision
      : "",
    symbol: "AAPL",
    start_mode: "first_book",
    start_time: "09:30:00",
    start_date: "",
    policy: "human",
    execution_mode: "controlled",
    account: "new",
    account_name: "Research account",
    cash: "100000",
    fee_per_share: "0.01",
    latency_ms: "0",
    quantity: "10",
    hold_seconds: "2",
    news: "",
    allow_implicit_eof: false,
    allow_short: false,
    end_time: "16:00:00",
    timeout_seconds: "60",
    // An Agent's pacing: "live" (the market keeps moving) or "turn" (it waits).
    agent_pacing: "live",
    model_file: "",
    model_parameters: "{}",
    lookback_bars: "50",
    model_python: "",
    // Further symbols of one experiment on several symbols: [{symbol, path}].
    more: [],
    ...s.drafts.setup,
  };
}

/* Every symbol of the experiment with its recording, the first row first. */
export function setupLegs(d) {
  return [{ symbol: d.symbol, path: d.path }, ...(d.more || [])];
}
/* Methods that can trade several symbols; the others trade one. */
export const SEVERAL_SYMBOL_POLICIES = ["human", "model"];
/* Every choice that changes how the experiment runs, for the review step. */
function reviewRows(s, d) {
  const row = (label, value) =>
    `<div class="meta-row"><span>${label}</span><span>${value}</span></div>`;
  const file = (s.datasets.files || []).find((f) => f.path === d.path);
  const isCsv = /\.csv$/i.test(d.path || "");
  const saved = (s.agents.agents || []).find(
    (agent) => agent.agent_id + ":" + agent.revision === d.agent,
  );
  const maker = {
    human: "Manual",
    rule: `Buy → Hold → Sell · ${esc(d.quantity)} shares, hold ${esc(d.hold_seconds)} s`,
    hold: "Manage existing positions",
    model: `Model · ${esc((d.model_file || "").split(/[\\/]/).at(-1) || "no file chosen")} · ${esc(d.lookback_bars)} bars`,
    agent: esc(saved?.definition.name || "Saved Agent"),
  }[d.policy];
  const start =
    d.start_mode === "custom"
      ? (d.start_date ? esc(d.start_date) + " " : "") + esc(d.start_time) + " ET"
      : d.start_mode === "regular"
        ? "09:30 ET"
        : d.start_mode === "beginning"
          ? "Beginning of file"
          : "First bid and ask";
  const playback =
    d.policy === "agent"
      ? d.agent_pacing === "turn"
        ? "Turn-based · the market waits for each decision"
        : "Live 1×"
      : d.policy !== "model" && d.execution_mode === "live"
        ? "Live 1×"
        : "Controlled";
  return [
    row(
      "Account",
      `<strong>${esc(d.account === "new" ? d.account_name : (s.catalog.accounts || []).find((a) => a.id === d.account)?.name || "Existing account")}</strong>`,
    ),
    row("Starting cash", d.account === "new" ? usd(Number(d.cash) * 10000) : "Existing balance"),
    (d.more || []).length
      ? row(
          "Symbols",
          setupLegs(d)
            .map((leg) => `${esc(leg.symbol.toUpperCase())} · ${esc((s.datasets.files || []).find((f) => f.path === leg.path)?.name || "no recording")}`)
            .join("<br>"),
        )
      : row("Recording", esc(file?.name || "Not selected")),
    isCsv ? row("Synthetic book", esc(bookLabel(file?.book || { spread: 200, depth: 5 }).replace("Synthetic book · ", ""))) : "",
    row("Start", start),
    row("Decision maker", maker),
    row("Playback", playback),
    row("Fee / latency", `${usd(Number(d.fee_per_share) * 10000)} per share / ${esc(d.latency_ms)} ms`),
    d.policy !== "agent" ? row("Short selling", d.allow_short ? "Allowed · 50% initial margin" : "Not allowed") : "",
  ].join("");
}

/* The model method's file, lookback and optional settings. */
function modelFields(d) {
  return `${field("Model file · .py", "model_file", d.model_file, "text", "required")}${b("Choose model file…", "pick:model", "secondary", "folder")}${field("Completed bars per decision", "lookback_bars", d.lookback_bars, "number", 'min="1" max="1000" step="1" required')}<details class="advanced-section"><summary>Model options</summary>${field("Parameters · JSON object", "model_parameters", d.model_parameters)}${field("Python interpreter · optional", "model_python", d.model_python)}<p class="inline-hint">Leave empty to use the App's Python. For packages such as torch, prepare an environment and enter its interpreter.</p></details>`;
}
function summary(s, d) {
  const file = (s.datasets.files || []).find((file) => file.path === d.path);
  const saved = (s.agents.agents || []).find(
    (agent) => agent.agent_id + ":" + agent.revision === d.agent,
  );
  return `<aside class="form-rail"><div class="summary"><h3>Experiment summary</h3><div class="meta-row"><span>Market</span><strong>${esc(setupLegs(d).map((leg) => (leg.symbol || "—").toUpperCase()).join(", "))}</strong></div><div class="meta-row"><span>Recording</span><span>${esc(setupLegs(d).map((leg) => (s.datasets.files || []).find((f) => f.path === leg.path)?.name || "Not selected").join(", "))}</span></div><div class="meta-row"><span>Start</span><span>${d.start_mode === "custom" ? (d.start_date ? esc(d.start_date) + " " : "") + esc(d.start_time) + " ET" : d.start_mode === "regular" ? "09:30 ET" : d.start_mode === "beginning" ? "Beginning of file" : "First bid and ask"}</span></div><div class="meta-row"><span>Decision maker</span><span>${d.policy === "agent" ? esc(saved?.definition.name || "Select an Agent") : d.policy === "rule" ? "Buy → Hold → Sell" : d.policy === "hold" ? "Manage positions" : d.policy === "model" ? esc(d.model_file.split(/[\\/]/).at(-1) || "Choose a model file") : "Manual"}</span></div><div class="meta-row"><span>Starting cash</span><span>${d.account === "new" ? usd(Number(d.cash) * 10000) : "Existing balance"}</span></div>${d.policy === "agent" ? `<div class="meta-row"><span>Pacing</span><span>${d.agent_pacing === "turn" ? "Turn-based" : "Live 1×"}</span></div>` : ""}<p>Opens paused. You choose when the market starts moving.</p><div class="section-rule inline-hint">${icon("shield")}<span>Historical simulation.<br>No connection to a broker.</span></div></div></aside>`;
}
/* Further symbols of the experiment, each with its own recording. */
function symbolLegs(d, files) {
  const rows = (d.more || []).map(
    (leg, index) =>
      `<div class="field-pair symbol-leg">${select(
        "Recording",
        "more_path_" + index,
        files.map((file) => [file.path, file.name]),
        leg.path,
      )}<div class="symbol-leg-symbol">${field("Symbol", "more_symbol_" + index, leg.symbol, "text", 'required maxlength="12"')}<button type="button" class="quiet icon-button" data-remove-leg="${index}" aria-label="Remove ${esc(leg.symbol || "symbol")}">×</button></div></div>`,
  );
  const hint = rows.length
    ? `<p class="inline-hint">One account trades every symbol on one shared timeline. Manual trading or a Model file, in Controlled playback. Choose the same ITCH file on each row when it contains every symbol. The first recording's synthetic book settings apply to every CSV.</p>`
    : "";
  return `${rows.join("")}${hint}<button type="button" class="secondary add-leg">${icon("plus")}Add another symbol</button>`;
}
export function setup(s) {
  const d = setupDefaults(s),
    step = s.setupStep || 1;
  const files = (s.datasets.files || []).filter(
    (file) => file.status === "Available",
  );
  const agents = s.agents.agents || [];
  const several = (d.more || []).length > 0;
  let body = "";
  // A method's own settings sit right under its card, so they appear where it was chosen.
  const policyFields = (value) =>
    value === "model"
      ? modelFields(d)
      : value === "agent"
        ? agents.length
          ? select(
              "Saved revision",
              "agent",
              agents.map((agent) => [
                agent.agent_id + ":" + agent.revision,
                agent.definition.name + " · revision " + agent.revision,
              ]),
              d.agent || agents[0]?.agent_id + ":" + agents[0]?.revision,
            )
          : `<p class="inline-hint">No saved Agents yet.</p>${b("Create an Agent", "agent-new", "secondary", "plus")}`
        : "";
  if (step === 1)
    body = `<section class="form-block"><h2>Choose your market</h2>${
      files.length
        ? select(
            "Recording",
            "path",
            files.map((file) => [file.path, file.name]),
            d.path,
          )
        : `<div class="inline-hint">${icon("file")}Add a recording before creating an experiment.</div><div class="empty-recording-action">${b("Add recording", "import", "secondary", "plus")}</div>`
    }<div class="field-pair">${field("Symbol", "symbol", d.symbol, "text", 'required maxlength="12"')}${select(
      "Start at",
      "start_mode",
      [
        ["first_book", "First available bid and ask"],
        ["regular", "Regular session · 09:30 ET"],
        ["custom", "Custom time"],
        ["beginning", "Beginning of file"],
      ],
      d.start_mode,
    )}</div>${files.length ? symbolLegs(d, files) : ""}${d.start_mode === "custom" ? (/\.csv$/i.test(d.path) ? `<div class="field-pair">${field("Start date · optional", "start_date", d.start_date, "date")}${field("Start time · ET", "start_time", d.start_time, "time", 'step="1" required')}</div><p class="inline-hint">Leave the date empty to start on the recording's first day.</p>` : field("Start time · ET", "start_time", d.start_time, "time", 'step="1" required')) : ""}<details class="advanced-section"><summary>Additional data options</summary>${field("Announcements file · optional", "news", d.news)}${b("Choose announcements…", "pick:news", "secondary", "folder")}<label class="choice-card"><input type="checkbox" name="allow_implicit_eof" ${d.allow_implicit_eof ? "checked" : ""}><span><strong>Allow complete S/C ending</strong><small>Accept a complete S/C message at the end of the recording.</small></span></label></details></section><section class="form-block"><h2>Who makes the decisions?</h2>${[
      [
        "human",
        "Manual trading",
        "Place orders, pause and step through the market.",
      ],
      [
        "rule",
        "Buy → Hold → Sell",
        "Run a baseline without a model or API key.",
      ],
      [
        "hold",
        "Manage existing positions",
        "Use an existing account with the position-management baseline.",
      ],
      [
        "model",
        "Model file",
        "Trade on what your own Python model returns, once per bar.",
      ],
      [
        "agent",
        "Saved Agent",
        "Use a saved revision with its own schedule and tools.",
      ],
    ]
      .filter(([value]) => !several || SEVERAL_SYMBOL_POLICIES.includes(value))
      .map(
        ([value, title, description]) =>
          `<label class="choice-card"><input type="radio" name="policy" value="${value}" ${d.policy === value ? "checked" : ""}><span><strong>${title}</strong><small>${description}</small></span></label>${d.policy === value ? policyFields(value) : ""}`,
      )
      .join("")}</section>`;
  if (step === 2)
    body = `<section class="form-block"><h2>Simulated account</h2>${select("Account", "account", [["new", "New independent account"], ...(s.catalog.accounts || []).map((a) => [a.id, a.name])], d.account)}${d.account === "new" ? `${field("Account name", "account_name", d.account_name, "text", "required")}${field("Starting cash · USD", "cash", d.cash, "number", 'min="0.01" step="0.01" required')}` : '<p class="inline-hint">Existing balances and positions are inherited. An account already used by an open experiment cannot be reused.</p>'}</section><section class="form-block"><h2>Execution assumptions</h2><div class="field-pair">${field("Fee per share · USD", "fee_per_share", d.fee_per_share, "number", 'min="0" step="0.0001" required')}${field("Order latency · ms", "latency_ms", d.latency_ms, "number", 'min="0" step="1" required')}</div>${
      several
        ? '<p class="inline-hint">Experiments on several symbols run in Controlled mode: play, pause and step.</p>'
        : d.policy === "model"
        ? '<p class="inline-hint">Model files run in Controlled mode: play, pause and step.</p>'
        : d.policy === "agent"
        ? `<div class="field"><label>Pacing</label></div>${[
            [
              "live",
              "Live 1×",
              "The market keeps moving while the Agent thinks, as in real trading. Its speed is part of the result.",
            ],
            [
              "turn",
              "Turn-based",
              "The market waits for each decision, then moves to the Agent's next turn. Judges decisions alone, and runs faster than the session.",
            ],
          ]
            .map(
              ([value, title, hint]) =>
                `<label class="choice-card"><input type="radio" name="agent_pacing" value="${value}" ${d.agent_pacing === value ? "checked" : ""}><span><strong>${title}</strong><small>${hint}</small></span></label>`,
            )
            .join("")}<div class="field-pair">${field("Agent session end · ET", "end_time", d.end_time, "time", 'required step="1"')}${field("Model timeout · seconds", "timeout_seconds", d.timeout_seconds, "number", 'required min="1" max="3600" step="1"')}</div>`
        : select(
            "Playback",
            "execution_mode",
            [
              ["controlled", "Controlled · pause and step"],
              ["live", "Live 1×"],
            ],
            d.execution_mode,
          )
    }${d.policy !== "agent" ? `<label class="choice-card"><input type="checkbox" name="allow_short" ${d.allow_short ? "checked" : ""}><span><strong>Allow short selling</strong><small>Sales may exceed the shares held. Opening a short needs 50% of its value in cash; proceeds stay as collateral. No margin calls.</small></span></label>` : ""}${d.policy === "rule" ? `<div class="field-pair">${field("Shares", "quantity", d.quantity, "number", 'min="1" step="1" required')}${field("Hold · seconds", "hold_seconds", d.hold_seconds, "number", 'min="0" step="0.001" required')}</div>` : ""}</section>`;
  if (step === 3)
    body = `<section class="form-block"><h2>Ready to prepare</h2>${reviewRows(s, d)}</section>${s.preparing ? `<div class="preparation-status" role="status">${icon("clock")}<div><strong>Preparing experiment</strong><p>${esc(progressLabel(s.task))}</p></div></div>` : '<p class="inline-hint">Large recordings can take several minutes to prepare.</p>'}`;
  return (
    head("New experiment") +
    `<div class="steps">${["Market", "Account & execution", "Review"].map((title, index) => `<div class="step ${step === index + 1 ? "active" : step > index + 1 ? "done" : ""}" ${step === index + 1 ? 'aria-current="step"' : ""}><span class="step-num">${step > index + 1 ? "✓" : index + 1}</span>${title}</div>`).join("")}</div><div class="content"><div class="form-layout"><form id="setup-form">${body}<div class="form-actions"><button class="primary" ${s.preparing || !files.length || (step === 1 && d.policy === "agent" && !agents.length) ? "disabled" : ""}>${step < 3 ? "Continue " + icon("arrow") : s.preparing ? "Preparing…" : icon("play") + "Prepare experiment"}</button>${step > 1 ? b("Back", "setup-back", "quiet", "back", s.preparing ? "disabled" : "") : ""}${b(s.preparing ? "Cancel preparation" : "Cancel", s.preparing ? "cancel-preparation" : "cancel-setup", "quiet")}</div></form>${summary(s, d)}</div></div>`
  );
}
function progressLabel(task) {
  return (
    {
      market: "Reading market data…",
      initializing_agent: "Starting Agent…",
      creating_experiment: "Creating independent account and run…",
      configuring_model: "Connecting the saved model profile…",
      prepare_experiment: "Preparing market…",
    }[task?.phase] ||
    "Your market, account and decision maker are being prepared."
  );
}
export function setupSummary(s) {
  return summary(s, setupDefaults(s));
}

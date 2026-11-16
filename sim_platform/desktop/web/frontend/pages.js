import { policyLabel, runStatus, sessionName, symbolsOf } from "./session.js";
export { agents, environment } from "./agents.js";
export { setup } from "./setup.js";
export { comparison } from "./compare.js";
/* Management pages consume detached catalogue values; all mutations use commands. */
import {
  esc,
  icon,
  button as b,
  head,
  table,
  field,
  select,
  empty,
  size,
  time,
  usd,
  bookLabel,
} from "./ui.js";
const row = (cells) => `<tr>${cells.map((c) => `<td>${c}</td>`).join("")}</tr>`;
export function experiments(s) {
  const live = Object.entries(s.sessions).filter(
    ([id, p]) => id !== "manager" && p.view.run,
  );
  const openIds = new Set(live.map(([, p]) => p.view.run.id));
  const names = Object.fromEntries(
    (s.catalog.accounts || []).map((a) => [a.id, a.name]),
  );
  // The second line names the account when it differs from the experiment, else the run.
  const subtitle = (title, view) => {
    const account = names[view.run.account_id];
    return account && account !== title
      ? "Account · " + account
      : "Run " + view.run.id.slice(0, 8);
  };
  const rows = live.map(([id, p]) =>
    row([
      `<div class="file-name">${icon("flask")}<div>${esc(sessionName(s, id))}<small class="run-account">${esc(subtitle(sessionName(s, id), p.view))}</small></div></div>`,
      esc(symbolsOf(p.view).join(", ") || p.view.market?.symbol || "—"),
      esc(policyLabel(p.view)),
      `<span class="badge">${runStatus(p.view)}</span>`,
      time(p.view.market?.time_ns),
      ["Stopped", "Completed", "Ended", "Failed", "Read-only result"].includes(
        runStatus(p.view),
      )
        ? b("View results", "saved:" + p.view.run.id, "quiet", "arrow")
        : b("Open workspace", "open:" + id, "secondary", "chart"),
    ]),
  );
  for (const run of s.catalog.runs || [])
    if (!openIds.has(run.id))
      rows.push(
        row([
          esc(names[run.account_id] || run.id.slice(0, 8)),
          "—",
          "Saved revision",
          esc(run.status),
          time(run.time_ns),
          b("View results", "saved:" + run.id, "quiet", "arrow") +
            (run.status === "running"
              ? b(
                  "End interrupted run",
                  "recover:" + run.id,
                  "quiet danger",
                  "stop",
                )
              : ""),
        ]),
      );
  return (
    head("Experiments", b("New experiment", "new", "primary", "plus")) +
    `<div class="content">${rows.length ? table(["Experiment", "Market", "Decision maker", "Status", "Time · ET", ""], rows) : empty("No experiments yet", "Choose market data and create your first experiment.", b("New experiment", "new", "primary", "plus"))}</div>`
  );
}
export function data(s) {
  const files = (s.datasets.files || []).filter(
    (d) =>
      !s.query ||
      (d.name + " " + d.path).toLowerCase().includes(s.query.toLowerCase()),
  );
  const rows = files.map(
    (d) =>
      `<tr data-action="dataset:${esc(d.id)}"><td><button type="button" class="recording-toggle" aria-expanded="${s.dataset === d.id}">${icon(s.dataset === d.id ? "down" : "chevron")}${icon("file")}${esc(d.name)}</button></td><td title="${Number(d.bytes).toLocaleString()} bytes">${size(d.bytes)}</td><td>${esc(d.status)}</td><td>${d.managed ? "App copy" : "Original file"}</td></tr>${s.dataset === d.id ? `<tr class="dataset-detail-row"><td colspan="4"><div class="dataset-details"><div><h3>Recording details</h3><p>${esc(d.name)} · ${size(d.bytes)}</p>${d.kind ? `<p>${esc(d.kind)}${d.dates ? " · " + esc(d.dates) : ""}</p>` : ""}${/\.csv$/i.test(d.name) ? "" : '<p class="muted">Symbols and dates are read when an experiment or preview prepares it.</p>'}<p>${esc(d.status)}</p>${/\.csv$/i.test(d.name) ? `<p>${esc(bookLabel(d.book || DEFAULT_BOOK))}</p>` : ""}<p class="path">${esc(d.path)}</p></div><div><h3>${d.managed ? "App-managed storage" : "Original file"}</h3><div class="inline-actions">${d.status === "Available" ? b("Create experiment", "use:" + d.id, "primary", "plus") + b("Preview market", "preview:" + d.id, "secondary", "chart") : b("Locate file", "locate:" + d.id, "secondary", "folder")}</div></div></div></td></tr>` : ""}`,
  );
  return (
    head("Market data", b("Add recording", "import", "primary", "plus")) +
    `<div class="toolbar"><label class="search-field">${icon("search")}<input id="recording-search" aria-label="Find recording" placeholder="Find a recording…" value="${esc(s.query)}"></label>${b("", "refresh", "icon-button push", "refresh", 'aria-label="Refresh recordings"')}</div><div class="content">${rows.length ? table(["Recording", "Size", "Availability", "Storage"], rows) : empty("No recordings found", "Add an uncompressed ITCH recording or a CSV file of price bars to your library.")}</div>`
  );
}
// The synthetic book a price-bar file gets when none was chosen (money units).
const DEFAULT_BOOK = { spread: 200, depth: 5 };

/* Built-in reference Agents (sim_platform/reference_agents); choosing one fills the source. */
/* Model providers: the hosted OpenAI API, or a server on this machine or network. */
const MODEL_PROVIDERS = [
  ["openai-responses", "OpenAI (hosted)"],
  ["openai-compatible", "Local server: vLLM, Ollama, llama.cpp"],
];
const REFERENCE_AGENTS = [
  ["", "None: use my own source"],
  ["trading_agents", "Multi-role trading firm (TradingAgents)"],
  ["layered_memory", "Layered-memory trader (FinMem)"],
  ["manager_analyst", "Manager and analysts with belief updates (FinCon)"],
];
/* The display name of a chosen reference Agent, used until the user types a name. */
const referenceName = (key) =>
  key ? (REFERENCE_AGENTS.find(([value]) => value === key) || ["", ""])[1] : "";

export { replay } from "./replay.js";
/* Spread and depth of the synthetic book, asked only for a .csv price-bar file. */
function bookFields(s) {
  const d = s.drafts.import || {};
  return `<section class="form-block"><h2>Synthetic order book</h2><p class="inline-hint">A price-bar file has no order book. The platform builds one around each bar's prices; choose how it looks.</p><div class="field-pair">${field("Spread · USD", "spread", d.spread ?? "0.02", "number", 'min="0.01" max="10" step="0.01" required')}${field("Depth · levels per side", "depth", d.depth ?? "5", "number", 'min="1" max="100" step="1" required')}</div></section>`;
}
export function importPage(s) {
  return (
    head("Add a recording") +
    `<div class="content"><div class="form-layout"><form id="import-form"><section class="form-block"><h2>Recording</h2>${s.file ? `<div class="chosen-file">${icon("file")}<div class="chosen-file-copy"><strong>${esc(s.file.split(/[\\/]/).at(-1))}</strong><small class="path">${esc(s.file)}</small></div>${b("Change", "pick:recording", "quiet push", "folder")}</div>` : `<div class="dropzone">${icon("upload")}${b("Choose file…", "pick:recording", "secondary", "folder")}<p>Choose an uncompressed .bin or .itch recording, or a .csv file of OHLCV price bars</p></div>`}</section>${/\.csv$/i.test(s.file || "") ? bookFields(s) : ""}<section class="form-block"><h2>Storage</h2>${[
      [
        "false",
        "Keep in its current location",
        "No extra disk space. Keep the original file accessible.",
      ],
      [
        "true",
        "Copy into this workspace",
        "Creates an independent copy and needs extra disk space.",
      ],
    ]
      .map(
        ([value, title, hint]) =>
          `<label class="choice-card"><input type="radio" name="copy" value="${value}" ${(s.drafts.import?.copy || "false") === value ? "checked" : ""}><span><strong>${title}</strong><small>${hint}</small></span></label>`,
      )
      .join(
        "",
      )}</section><div class="form-actions"><button class="primary" ${s.importing || !s.file ? "disabled" : ""}>${s.importing ? "Adding recording…" : "Add recording"}</button>${s.importing ? `<p class="inline-hint" role="status">${s.task?.total ? Math.round((s.task.completed / s.task.total) * 100) + "% · " : ""}Checking and importing recording…</p>` : ""}${b(s.importing ? "Cancel import" : "Cancel", s.importing ? "cancel-import-job" : "cancel-import", "quiet")}</div></form><aside class="form-rail"><h3>Before you add</h3><p>Use an uncompressed ITCH BinaryFILE, or a CSV file of OHLCV price bars. Extract compressed recordings first.</p><div class="section-rule"><h3>What happens next?</h3><p>The recording appears in your library. Market preparation happens when you create an experiment.</p></div></aside></div></div>`
  );
}
export function editor(s) {
  const d = s.drafts.agent || {};
  return (
    head(d.agent_id ? "Edit Agent" : "New Agent") +
    `<div class="content"><div class="form-layout"><form id="agent-form"><section class="form-block"><h2>Definition</h2>${field("Name", "name", d.name || referenceName(d.reference), "text", "required")}${select(
      "Implementation",
      "integration",
      [
        ["configured", "Configured Agent"],
        ["python_strategy", "Python Agent"],
        ["python_carrier", "Python carrier"],
      ],
      d.integration || "configured",
      d.agent_id ? "disabled" : "",
    )}${d.integration && d.integration !== "configured" ? `${field("Local source path", "source", d.source || "")}${b("Choose source file", "pick:agent", "secondary", "folder")}${b("Choose source folder", "pick:agent-folder", "quiet", "folder")}${d.agent_id ? "" : select("Or start from a reference Agent", "reference", REFERENCE_AGENTS, d.reference || "")}` : ""}<div class="field" style="margin-top:18px"><label for="prompt">Task</label><textarea id="prompt" name="prompt" required>${esc(d.prompt || "")}</textarea></div><div class="field-pair">${select("Model provider", "provider", MODEL_PROVIDERS, d.model?.provider || "openai-responses")}${field("Model identifier", "model", d.model?.model || "")}</div><div class="field-pair">${field("Credential reference", "credential_ref", d.model?.credential_ref ?? "desktop-model")}${(d.model?.provider || "openai-responses") === "openai-compatible" ? field("Local server URL", "base_url", d.model?.base_url || "http://127.0.0.1:11434/v1") : ""}</div></section><details class="advanced-section"><summary>Tools and scheduling</summary><section class="form-block">${field("Granted tools · comma separated", "requires", (d.requires || ["environment.describe", "market.quote", "account.snapshot", "orders.list", "orders.submit_limit", "orders.submit_market", "orders.cancel", "control.finish", "control.wait", "model.request"]).join(", "))}<div class="field-pair">${select(
      "Schedule",
      "schedule_mode",
      [
        ["continuous", "Continuous"],
        ["interval", "After an interval"],
        ["event", "On an event"],
      ],
      d.schedule_mode || "interval",
    )}${field("Schedule interval · seconds", "interval_seconds", d.interval_seconds || 30, "number", 'min="1" required')}</div>${select(
      "Events while busy",
      "busy_policy",
      [
        ["preserve_interval", "Preserve interval"],
        ["wake_on_pending_event", "Wake on pending event"],
      ],
      d.busy_policy || "preserve_interval",
    )}<div class="field"><label for="events">Event conditions · JSON list</label><textarea id="events" name="events">${esc(JSON.stringify(d.events || [], null, 2))}</textarea></div><div class="field"><label for="parameters">Parameters · JSON object</label><textarea id="parameters" name="parameters">${esc(d.parametersText ?? JSON.stringify(d.parameters || {}, null, 2))}</textarea></div></section></details><div class="form-actions"><button class="primary" ${s.saving ? "disabled" : ""}>${s.saving ? "Saving…" : "Save revision"}</button>${b("Cancel", "cancel-agent", "quiet")}</div></form><aside class="form-rail"><h3>Versioned by design</h3><p>Changes create a new revision. Existing experiments keep the revision they started with.</p></aside></div></div>`
  );
}
export function settings(s) {
  return (
    head("Settings") +
    `<div class="content form-content"><section class="form-block"><h2>Appearance</h2><div class="segmented">${b("Light", "light", s.light ? "active" : "")}${b("Dark", "dark", !s.light ? "active" : "")}</div></section><form id="profile-form"><section class="form-block"><h2>Researcher</h2>${select(
      "Current researcher",
      "profile",
      (s.catalog.profiles || []).map((p) => [p.id, p.name]),
      s.catalog.profile,
    )}${b("Switch researcher", "profile-switch", "secondary")}<div style="margin-top:18px">${field("New researcher name", "name", "", "text")}</div><button class="secondary">Create researcher</button></section></form><form id="account-form"><section class="form-block"><h2>Accounts</h2>${
      (s.catalog.accounts || []).length
        ? `<div class="settings-table">${table(
            ["Name", "Account ID"],
            s.catalog.accounts.map((a) => row([esc(a.name), esc(a.id)])),
          )}</div>`
        : '<p class="inline-hint settings-table">No accounts yet. Create one below, or let a new experiment create its own.</p>'
    }${field("New account name", "name", "", "text", "required")}${field("Starting cash · USD", "cash", "100000", "number", 'required min="0.01" step="0.01"')}<button class="secondary">Create account</button></section></form><form id="credential-form"><section class="form-block"><h2>Model credential</h2>${field("Reference", "reference", "desktop-model", "text", "required")}${field("API key", "secret", "", "password", 'required autocomplete="off"')}${select(
      "Storage",
      "persistence",
      [
        ["session", "This session only"],
        ["os", "OS credential store"],
      ],
      "session",
    )}<button class="secondary">Save credential</button>${b("Load saved OS credential", "load-credential", "quiet")}</section></form><section class="form-block"><h2>Dataset storage</h2><p class="path muted">${esc(s.datasets.storage_root || "—")}</p>${b("Choose storage folder…", "dataset-storage", "secondary", "folder")}<p class="muted">Applies to future managed copies. Existing recordings stay in their current locations.</p></section><section class="form-block"><h2>Local workspace</h2><p>Reset removes local experiment records and settings. Original recordings are kept.</p>${b("Reset local workspace…", "reset-workspace", "secondary danger")}</section></div>`
  );
}

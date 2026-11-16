/* What each `data-action` button does.

   Markup names an action as "name" or "name:argument", for example "open:<session>"
   or "period:5". action() looks the name up in two tables: ARGUMENT_ACTIONS for names
   that take an argument, and ACTIONS for the rest. A name in neither table is a route,
   so plain navigation buttons need no entry here. */
import { s, app, hasUnsavedForms } from "./state.js";
import { send, notice } from "./bridge.js";
import { render, go, openSession, openSavedRun } from "./shell.js";
import { setupDefaults } from "./setup.js";
import { showSearch } from "./search.js";
import { playReplay, pauseReplay, nextReplayEvent } from "./replay-player.js";
import { evaluation, chosenEntries } from "./compare.js";

const savedAgent = (id) => s.agents.agents.find((a) => a.agent_id === id);
const recording = (id) => s.datasets.files.find((f) => f.id === id);

/* Start the setup form from its first step, optionally prefilled. */
function startSetup(prefill) {
  if (prefill) s.drafts.setup = prefill;
  s.setupStep = 1;
  go("new");
}

function setAppearance(light) {
  s.light = light;
  localStorage.setItem("market-lab-appearance", light ? "light" : "dark");
  render();
}

/* Leave a form and forget what was typed into it. */
function discardDraft(form, route) {
  s.drafts[form] = {};
  s.dirtyForms[form] = false;
  go(route);
}

/* Copy a saved definition into the editor's draft, in the editor's field names. */
function editAgent(id) {
  const saved = savedAgent(id);
  if (!saved) return;
  const d = saved.definition;
  s.drafts.agent = {
    agent_id: id,
    expected_revision: saved.revision,
    name: d.name,
    integration: d.integration,
    prompt: d.initial_prompt,
    parameters: d.parameters,
    requires: d.requires,
    model: d.parameters?.desktop_model,
    interval_seconds: d.schedule?.interval_seconds || 30,
    schedule_mode: d.schedule?.mode || "continuous",
    busy_policy: d.schedule?.busy_event_policy || "preserve_interval",
    events: d.schedule?.events || [],
  };
  go("agent-edit");
}

/* Play, pause, step or end the active experiment. */
function playback(method) {
  if (method === "stop") s.endingSession = s.active;
  const args = { session: s.active, method };
  if (method === "play") {
    const speed = s.speeds[s.active];
    // "Max" is sent as no speed limit.
    args.speed = speed === "max" ? null : Number(speed || 1);
  }
  send("session", args);
}

/* Play, pause, step or cancel the account-free market preview. */
function previewPlayback(name) {
  const method = name === "preview-cancel" ? "stop" : name.slice("preview-".length);
  send("session", {
    session: "manager",
    method,
    ...(name === "preview-play" ? { speed: 1 } : {}),
  });
}

const ARGUMENT_ACTIONS = {
  // Several symbols: show another symbol's chart, depth and order ticket.
  symbol(symbol) {
    // The results page shows the saved run held by the manager session.
    const id = s.route === "results" ? "manager" : s.active;
    s.symbols[id] = symbol;
    if (id !== "manager") {
      const ask = s.sessions[id]?.view.markets?.[symbol]?.market?.asks?.[0]?.[0];
      s.tickets[id] = {
        ...(s.tickets[id] || { quantity: 10, side: "buy" }),
        limit_price: ask != null ? String(ask / 10000) : "",
      };
    }
    render();
  },
  // Experiments and tabs.
  open: openSession,
  saved: openSavedRun,
  // Python replays the run in the background and reports the outcome as a notice.
  "verify-run"(id) {
    notice("Verifying the run against its recorded decisions…");
    send("verify_run", { run_id: id });
  },
  close(id) {
    s.tabs = s.tabs.filter((tab) => tab !== id);
    if (!s.tabs.length) go("experiments");
    else if (s.active === id) openSession(s.tabs.at(-1));
    else render();
  },
  recover(runId) {
    const agreed = confirm(
      "End this interrupted experiment? Unfilled orders will be cancelled; saved results are kept.",
    );
    if (agreed) {
      s.savedPending = runId;
      send("recover_stop", { run_id: runId });
    }
  },

  // Workspace.
  side(side) {
    s.tickets[s.active] = {
      ...(s.tickets[s.active] || { quantity: 10, limit_price: "" }),
      side,
    };
    render();
  },
  // A click on a depth level copies its price into the ticket without a re-render,
  // so the form keeps focus; the input event updates the estimate.
  price(price) {
    const input = app.querySelector("#order-form #limit_price");
    if (!input) return;
    input.value = (Number(price) / 10000).toFixed(Number(price) % 100 ? 4 : 2);
    input.dispatchEvent(new Event("input", { bubbles: true }));
  },
  period(minutes) {
    s.periods[s.active] = Number(minutes);
    render();
  },
  records(tab) {
    s.recordTabs[s.active] = tab;
    render();
  },
  inspector(panel) {
    s.inspector = panel;
    s.inspectors[s.active] = panel;
    render();
  },
  cancel(clientId) {
    send("session", { session: s.active, method: "cancel", client_id: clientId });
  },

  // Market data.
  dataset(id) {
    s.dataset = s.dataset === id ? null : id;
    render();
    app
      .querySelector(`[data-action="dataset:${CSS.escape(id)}"] .recording-toggle`)
      ?.focus();
  },
  use(id) {
    startSetup({ ...(s.drafts.setup || {}), path: recording(id).path });
  },
  preview(id) {
    s.drafts.preview = {
      path: recording(id).path,
      symbol: s.drafts.setup?.symbol || "AAPL",
    };
    s.previewLoading = true;
    go("market-preview");
    send("load_market", { ...s.drafts.preview, start_mode: "first_book" });
  },
  locate(id) {
    send("locate_dataset", { identity: id });
  },
  pick(target) {
    send("pick_file", { target });
  },

  // Agents.
  "agent-select"(id) {
    s.selectedAgent = id;
    render();
  },
  agent: editAgent,
  "agent-use"(id) {
    const saved = savedAgent(id);
    if (!saved) return;
    startSetup({
      ...(s.drafts.setup || {}),
      policy: "agent",
      agent: id + ":" + saved.revision,
    });
  },
  environment(id) {
    if (!savedAgent(id)) return;
    s.selectedAgent = id;
    s.environmentStatus = "";
    go("agent-environment");
  },

  // Records and Replay.
  "audit-select"(index) {
    s.auditSelection ||= {};
    s.auditSelection[s.active] = Number(index);
    render();
  },
  "replay-select"(index) {
    s.replayIndex = Number(index);
    render();
  },
};

const cancelTask = () => send("cancel_task");

const ACTIONS = {
  search: showSearch,
  refresh: () => send("refresh"),

  // Experiment playback.
  play: () => playback("play"),
  pause: () => playback("pause"),
  step: () => playback("step"),
  stop: () => playback("stop"),
  "stop-confirm"() {
    if (confirm("End this experiment and cancel its unfilled orders?"))
      playback("stop");
  },
  "cancel-preparation": () => send("cancel_preparation"),
  export: () => send("export", { session: s.active || "manager" }),

  // Setup.
  new: () => startSetup(),
  "setup-back"() {
    s.setupStep = Math.max(1, s.setupStep - 1);
    render();
  },
  "cancel-setup"() {
    s.setupStep = 1;
    discardDraft("setup", "experiments");
  },
  "reuse-result"() {
    const view = s.sessions.manager?.view;
    startSetup({
      ...setupDefaults(s),
      path: view?.source?.path || "",
      symbol: view?.market?.symbol || "AAPL",
    });
  },
  "export-results": () => send("export", { session: "manager" }),

  // Market data.
  "preview-records"() {
    s.active = "manager";
    go("audit");
  },
  "cancel-import"() {
    s.file = "";
    discardDraft("import", "data");
  },
  "cancel-import-job": cancelTask,
  "dataset-storage": () => send("dataset_storage"),

  // Agents.
  "agent-new"() {
    if (s.dirtyForms.agent && !confirm("Discard the unfinished Agent definition?"))
      return;
    discardDraft("agent", "agent-edit");
  },
  "cancel-agent": () => discardDraft("agent", "agents"),
  "cancel-environment": cancelTask,
  "load-credential"() {
    send("load_credential", {
      reference: app.querySelector("[name=reference]").value,
    });
  },
  "export-sdk": () => send("export_sdk"),

  // Replay and comparison.
  "replay-play": playReplay,
  "replay-pause": pauseReplay,
  "replay-next": nextReplayEvent,
  "cancel-replay": cancelTask,
  "cancel-comparison": cancelTask,
  "export-comparison": () => send("export_comparison"),
  "run-evaluation"() {
    const e = evaluation();
    const entries = chosenEntries();
    if (!Object.keys(entries).length) return;
    e.busy = true;
    e.report = null;
    const cutoffs = Object.fromEntries(
      Object.entries(e.cutoffs).filter(([name, date]) => date && entries[name]),
    );
    send("evaluate", {
      entries,
      baseline: e.baseline || null,
      metric: e.metric,
      training_cutoffs: cutoffs,
      token_prices:
        e.prices.input !== "" || e.prices.output !== ""
          ? { input: Number(e.prices.input || 0), output: Number(e.prices.output || 0) }
          : null,
    });
    render();
  },
  "clear-evaluation"() {
    const e = evaluation();
    e.selected = {};
    e.report = null;
    render();
  },
  "refresh-evaluation": () => send("evaluation_runs"),
  "export-evaluation": () => send("export_evaluation"),

  // Settings.
  theme: () => setAppearance(!s.light),
  light: () => setAppearance(true),
  dark: () => setAppearance(false),
  "reset-workspace": () => send("reset_local_data"),
  "profile-switch"() {
    if (
      hasUnsavedForms() &&
      !confirm("Switch researcher and discard unfinished forms?")
    )
      return;
    send("select_profile", {
      profile: app.querySelector("[name=profile]").value,
    });
  },
};

export function action(value) {
  const separator = value.indexOf(":");
  const name = separator < 0 ? value : value.slice(0, separator);
  const argument = separator < 0 ? "" : value.slice(separator + 1);
  if (Object.hasOwn(ARGUMENT_ACTIONS, name)) ARGUMENT_ACTIONS[name](argument);
  else if (Object.hasOwn(ACTIONS, value)) ACTIONS[value]();
  else if (name.startsWith("preview-")) previewPlayback(name);
  else go(value);
}

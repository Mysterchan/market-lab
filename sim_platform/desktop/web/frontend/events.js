/* What the page does with each event Python publishes.

   Every handler updates state for one event kind. It returns false when no render
   should follow, either because it already updated the page or because nothing
   visible changed. Otherwise receive() re-renders, unless shouldRender() finds that
   doing so would disturb the user. */
import { s, ENDED } from "./state.js";
import { send, notice } from "./bridge.js";
import {
  render,
  go,
  openSession,
  openSavedRun,
  updateWorkspace,
  refreshPreview,
} from "./shell.js";
import { action } from "./actions.js";
import { isPlaying, runStatus } from "./session.js";
import { evaluation } from "./compare.js";

const FINISHED_TASK = ["ready", "failed", "cancelled"];
// Published lists that refresh in the background while the user works elsewhere.
const CATALOGUES = ["catalog", "datasets", "agents"];

/* Forget everything that belongs to the previous researcher or workspace. */
function clearSessions() {
  s.sessions = {};
  s.tabs = [];
  s.active = null;
  s.agents = {};
  s.drafts = {};
  s.dirtyForms = {};
}

/* A native menu item or shortcut asked for a page action. */
function onMenuAction(name) {
  if (name === "close-tab") action("close:" + s.active);
  else if (name === "play-pause")
    action(isPlaying(s.sessions[s.active]?.view) ? "pause" : "play");
  else if (name === "export" && s.route === "results") action("export-results");
  else action(name);
  return false;
}

function onReset(value) {
  clearSessions();
  s.catalog = value.catalog;
  s.datasets = value.datasets;
  s.tickets = {};
  s.dataset = null;
  go("experiments");
  return false;
}

function onCatalog(value) {
  const switchedResearcher =
    s.catalog.profile && s.catalog.profile !== value.profile;
  if (switchedResearcher) {
    clearSessions();
    s.savedPending = null;
  }
  s.catalog = value;
}

/* A new snapshot for one session. Most snapshots only patch what is on screen. */
function onView(packet) {
  const id = packet.session,
    view = packet.view;
  // A late snapshot from the researcher that was just switched away from.
  if (packet.profile && s.catalog.profile && packet.profile !== s.catalog.profile)
    return false;
  s.sessions[id] = packet;

  if (s.route === "market-preview" && id === "manager") {
    s.previewLoading = view.preparation?.status === "preparing";
    refreshPreview();
    return false;
  }
  if (s.route === "results" && id !== "manager") return false;

  // The user pressed End: close the tab and show the saved result instead.
  if (s.endingSession === id && ENDED.includes(runStatus(view))) {
    s.endingSession = null;
    s.tabs = s.tabs.filter((tab) => tab !== id);
    if (s.route === "workspace" && s.active === id) {
      openSavedRun(view.run.id);
      return false;
    }
  }
  // The saved result that was asked for has arrived.
  const requestedResult =
    id === "manager" && view.mode === "saved" && s.savedPending === view.run?.id;
  if (requestedResult) {
    s.savedPending = null;
    s.active = "manager";
    s.route = "results";
  }

  if (s.route === "workspace") {
    if (s.active !== id) return false;
    updateWorkspace();
    return false;
  }
  if (s.route === "audit" && s.active !== id) return false;
  return ["experiments", "results", "audit"].includes(s.route);
}

function onPrepared(value) {
  s.preparing = false;
  s.dirtyForms.setup = false;
  if (s.route === "new") openSession(value.session);
  else notice("Experiment ready. Open it from Experiments.");
  send("refresh");
  return false;
}

function onPreparing(value) {
  s.preparing = true;
  s.preparingSession = value.session;
}

function environmentStatus(task) {
  if (task.state === "ready") return "Environment ready.";
  if (task.state === "failed")
    return "Preparation failed. Check the error and try again.";
  if (task.state === "cancelled") return "Preparation cancelled.";
  return "Preparing: " + String(task.phase || "environment").replaceAll("_", " ");
}

/* Clear the "busy" flag of whichever page started a task that has now ended. */
function finishTask(task) {
  if (task.session === s.preparingSession) s.preparing = false;
  if (task.phase === "prepare_replay") s.replayBusy = false;
  if (task.phase === "save_agent") s.saving = false;
  if (!["add_dataset", "locate_dataset"].includes(task.phase)) return;
  s.importing = false;
  if (task.phase === "add_dataset" && s.importPending) {
    s.importPending = false;
    if (task.state === "ready") {
      s.dirtyForms.import = false;
      // A finished import returns to the library.
      if (s.route === "import") s.route = "data";
    }
  }
}

/* Progress of a background job: preparation, import, environment, replay. */
function onTask(task) {
  const route = s.route;
  const finished = FINISHED_TASK.includes(task.state);
  s.task = task;
  if (s.environmentBusy && task.session === "manager") {
    s.environmentStatus = environmentStatus(task);
    if (finished) s.environmentBusy = false;
  }
  if (finished) finishTask(task);
  if (s.route !== route) return true;
  // Only pages that display task progress need a refresh.
  return ["new", "import", "agents", "agent-environment"].includes(s.route);
}

function onError(value) {
  if (s.route === "market-preview") s.previewLoading = false;
  if (
    value.session === s.preparingSession ||
    (!value.session && s.route === "new")
  )
    s.preparing = false;
  // An error from the shared worker ends whatever background job a page started.
  if (!value.session || value.session === "manager") {
    s.importing = false;
    s.replayBusy = false;
    s.saving = false;
    s.comparing = false;
    s.environmentBusy = false;
    if (s.evaluation) s.evaluation.busy = false;
  }
  notice(value.message, true);
  render();
  return false;
}

/* A native file picker returned a path for the field that asked for it. */
function onFilePicked({ target, path }) {
  if (target === "news") s.drafts.setup = { ...s.drafts.setup, news: path };
  else if (target === "model")
    s.drafts.setup = { ...s.drafts.setup, model_file: path };
  else if (target === "python-archive")
    s.drafts.environment = { ...s.drafts.environment, python_archive: path };
  else if (target === "wheelhouse")
    s.drafts.environment = { ...s.drafts.environment, wheelhouse: path };
  else if (target === "recording") s.file = path;
  else if (target === "agent")
    s.drafts.agent = { ...s.drafts.agent, source: path };
  else if (target === "replay") s.replayPath = path;
}

const HANDLERS = {
  busy: () => false,
  ui_action: onMenuAction,
  reset: onReset,
  catalog: onCatalog,
  datasets: (value) => void (s.datasets = value),
  agents: (value) => void (s.agents = value),
  view: onView,
  prepared: onPrepared,
  preparing: onPreparing,
  task: onTask,
  notice(value) {
    notice(value);
    return false;
  },
  error: onError,
  file: onFilePicked,
  agent_saved(value) {
    s.dirtyForms.agent = false;
    s.selectedAgent = value.agent_id;
    s.saving = false;
    if (s.route === "agent-edit") s.route = "agents";
  },
  replay(value) {
    s.replay = value;
    s.replayIndex = 0;
    s.replayBusy = false;
  },
  comparison(value) {
    s.comparing = false;
    s.comparison = value;
  },
  evaluation(value) {
    const e = evaluation();
    if (value.candidates) {
      e.candidates = value.candidates;
      // Keep only choices that are still saved runs of this researcher.
      const ids = new Set(value.candidates.runs.map((run) => run.id));
      e.selected = Object.fromEntries(Object.entries(e.selected).filter(([id]) => ids.has(id)));
    }
    if (value.report) {
      e.report = value.report;
      e.busy = false;
    }
    if (s.route !== "comparison") return false;
  },
};

/* Background list refreshes must not redraw a workspace or a field being typed in. */
function shouldRender(kind) {
  if (!CATALOGUES.includes(kind)) return true;
  if (s.route === "workspace") return false;
  return !document.activeElement?.matches("input,textarea,select");
}

export function receive({ kind, value }) {
  const handler = Object.hasOwn(HANDLERS, kind) ? HANDLERS[kind] : null;
  if (handler?.(value) === false) return;
  if (shouldRender(kind)) render();
}

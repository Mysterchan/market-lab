/* The page frame: title bar, sidebar, tabs and the current page, plus navigation.

   render() rebuilds the whole frame from state. It is cheap enough for navigation
   and form steps, but market snapshots arrive many times a second, so an open
   workspace is patched in place by updateWorkspace() instead. */
import { s, app, navigation, ENDED, hasUnsavedForms } from "./state.js";
import { send } from "./bridge.js";
import {
  esc,
  icon,
  button as b,
  time,
  axisLabel,
  isDaily,
  setCalendar,
} from "./ui.js";
import * as pages from "./pages.js";
import {
  workspace,
  updateWorkspace as patchWorkspace,
  chartOverlay,
} from "./workspace.js";
import {
  sessionName,
  runStatus,
  focusedView,
  symbolsOf,
} from "./session.js";
import { results, mountEquity } from "./results.js";
import { audit } from "./audit.js";
import { preview, updatePreview } from "./preview.js";

// The shared chart formats times with the page's formatters.
window.MarketWorkbench = { time, axisLabel };

/* The session whose data the current page shows: saved results and previews come
   from the manager, a workspace from its own experiment. */
function shownPacket() {
  const fromManager =
    ["results", "market-preview"].includes(s.route) ||
    (s.route === "audit" && s.active === "manager");
  return s.sessions[fromManager ? "manager" : s.active];
}

/* Tell the formatters which calendar the shown recording uses, if it has one. */
function useShownCalendar() {
  setCalendar(shownPacket()?.view?.source?.prepared?.calendar);
}

const PAGES = {
  workspace,
  experiments: pages.experiments,
  data: pages.data,
  import: pages.importPage,
  new: pages.setup,
  agents: pages.agents,
  "agent-edit": pages.editor,
  "agent-environment": pages.environment,
  settings: pages.settings,
  replay: pages.replay,
  comparison: pages.comparison,
  results,
  audit,
  "market-preview": preview,
};

/* Sub-pages highlight the sidebar entry they were opened from. */
const PARENT_ROUTE = {
  import: "data",
  "market-preview": "data",
  "agent-edit": "agents",
  "agent-environment": "agents",
  results: "experiments",
};

// Regions that keep their scroll position across a full render.
const SCROLLABLE = ".content,.workspace-main,.inspector,.sidebar,.workbench-tabs";

let chart = null,
  equity = null,
  afterRender = () => {};

/* Register the function that attaches form listeners to freshly rendered markup. */
export function onRender(callback) {
  afterRender = callback;
}

/* Tell Python which page is open, so native menus and Quit behave correctly. */
export function reportState() {
  send("ui_state", {
    route: s.route,
    session: s.active,
    dirty: hasUnsavedForms(),
  });
}

export function go(route) {
  if (s.route === "workspace" && s.active) s.inspectors[s.active] = s.inspector;
  if (route !== "results") s.savedPending = null;
  if (route === "workspace" && (!s.active || s.active === "manager"))
    s.active = s.tabs.at(-1);
  s.route = route;
  // The Compare runs page lists the current saved runs each time it opens.
  if (route === "comparison") send("evaluation_runs");
  render();
}

/* Ask for a saved run's report; the results page opens when its view arrives. */
export function openSavedRun(runId) {
  s.savedPending = runId;
  send("open_saved", { run_id: runId });
}

/* Show an experiment: its workspace while it runs, its saved report once it ended. */
export function openSession(id) {
  const packet = s.sessions[id];
  if (!packet) return;
  const view = packet.view;
  if ([...ENDED, "Read-only result"].includes(runStatus(view))) {
    openSavedRun(view.run.id);
    return;
  }
  s.active = id;
  if (id !== "manager" && !s.tabs.includes(id)) s.tabs.push(id);
  // A new ticket starts at the best ask, the price a buy would need to reach.
  const bestAsk = view.market?.asks?.[0]?.[0];
  s.tickets[id] ??= {
    quantity: 10,
    limit_price: bestAsk != null ? String(bestAsk / 10000) : "",
    side: "buy",
  };
  s.inspector = s.inspectors[id] || (view.mode === "agent" ? "agent" : "order");
  go("workspace");
}

function tabs() {
  const items = s.tabs.map((id) => {
    const view = s.sessions[id]?.view;
    if (!view) return "";
    const title =
      (symbolsOf(view).join(" + ") || view.market?.symbol || "Experiment") +
      " · " +
      (s.sessions[id].name || (view.mode === "agent" ? "Agent" : "Manual"));
    const selected = s.active === id;
    return [
      `<div class="experiment-tab ${selected ? "active" : ""}">`,
      `<button role="tab" aria-label="${esc(title)} ${id.slice(0, 6)}" aria-selected="${selected}" data-action="open:${id}">`,
      icon(view.mode === "agent" ? "agent" : "chart"),
      `<span>${esc(title)}</span></button>`,
      b("", "close:" + id, "tab-close", "close", `aria-label="Close tab ${esc(title)}"`),
      "</div>",
    ].join("");
  });
  return [
    '<div class="tabstrip workbench-tabs" role="tablist" aria-label="Experiment workspaces">',
    ...items,
    b("", "new", "icon-button new-tab", "plus", 'aria-label="New experiment tab"'),
    "</div>",
  ].join("");
}

/* The sidebar entry to highlight. Records keep the entry of the page they belong to. */
function sidebarRoute() {
  if (PARENT_ROUTE[s.route]) return PARENT_ROUTE[s.route];
  if (s.route !== "audit") return s.route;
  const view = s.sessions[s.active]?.view;
  if (view?.mode === "saved") return "experiments";
  return view?.run ? "workspace" : "data";
}

const navButton = (route, title, symbol, active = false) =>
  b(
    `<span class="nav-label">${title}</span>`,
    route,
    "nav " + (active ? "active" : ""),
    symbol,
    `title="${title}"`,
  );

function titlebar() {
  return [
    '<header class="titlebar">',
    '<div class="brand"><span class="mark">m/</span><strong>market lab</strong><span class="project-name">Research</span></div>',
    `<button class="global-search" data-action="search">${icon("search")}Search workspace<kbd>⌘ K</kbd></button>`,
    '<div class="titlebar-end"><span class="muted" style="font-size:11px">Local</span>',
    b("", "theme", "icon-button", "sun", 'aria-label="Switch appearance"'),
    "</div></header>",
  ].join("");
}

function sidebar() {
  const current = sidebarRoute();
  const researcher =
    (s.catalog.profiles || []).find((p) => p.id === s.catalog.profile)?.name ||
    "Local workspace";
  return [
    '<aside class="sidebar"><div class="side-heading">RESEARCH WORKSPACE</div>',
    b('<span class="nav-label">New experiment</span>', "new", "nav", "plus", 'title="New experiment"'),
    '<div class="side-separator"></div>',
    ...navigation.map(([route, title, symbol]) =>
      navButton(route, title, symbol, current === route),
    ),
    '<div class="side-separator"></div><div class="side-label">RECENT EXPERIMENTS</div>',
    ...s.tabs.map((id) =>
      b(esc(sessionName(s, id)), "open:" + id, "session-short", "chart"),
    ),
    '<div class="side-bottom"><div class="utility-nav">',
    navButton("replay", "Recorded Replay", "clock"),
    navButton("comparison", "Compare runs", "chart"),
    "</div>",
    navButton("settings", "Settings", "settings"),
    `<div class="profile"><span class="avatar">${esc((researcher.trim()[0] || "R").toUpperCase())}</span><div><strong>${esc(researcher)}</strong><small>Simulation only</small></div></div>`,
    "</div></aside>",
  ].join("");
}

function footer() {
  const clock =
    s.route === "workspace"
      ? time(s.sessions[s.active]?.view.market?.time_ns) + " ET"
      : "";
  return `<footer class="footer"><span class="dot"></span>Local workspace<span>Simulation only</span><span class="push">${clock}</span></footer>`;
}

function scrollPositions() {
  return [...app.querySelectorAll(SCROLLABLE)].map((node) => ({
    className: node.className,
    top: node.scrollTop,
    left: node.scrollLeft,
  }));
}

function restoreScroll(saved) {
  const nodes = [...app.querySelectorAll(SCROLLABLE)];
  for (const position of saved) {
    const node = nodes.find((node) => node.className === position.className);
    if (node) {
      node.scrollTop = position.top;
      node.scrollLeft = position.left;
    }
  }
}

function unmountCharts() {
  chart?.observer?.disconnect();
  chart = null;
  equity?.disconnect();
  equity = null;
}

/* Attach the price chart, and the equity chart on results, to the new markup. */
function mountCharts() {
  const hasPriceChart = ["workspace", "market-preview", "results"].includes(
    s.route,
  );
  if (hasPriceChart && app.querySelector(".m-price-chart")) {
    const inWorkspace = s.route === "workspace";
    const packet = s.sessions[inWorkspace ? s.active : "manager"];
    chart = new MarketChart(app);
    chart.interval = inWorkspace && !isDaily() ? s.periods[s.active] || 1 : 1;
    // A daily bar arrives as several one-minute bars; draw one candle per day.
    chart.byDay = isDaily();
    // On several symbols the chart draws the shown symbol only.
    const id = inWorkspace ? s.active : "manager";
    const shown = focusedView(s, id);
    const symbol = symbolsOf(shown).length ? shown.market.symbol : null;
    chart.setBars(shown?.bars || []);
    // A market preview has no account, so there are no fills or orders to mark.
    if (s.route !== "market-preview") chart.setOverlay(chartOverlay(packet, symbol));
  }
  if (s.route === "results")
    equity = mountEquity(app, s.sessions.manager?.performance);
}

export function render() {
  const scroll = scrollPositions();
  useShownCalendar();
  document.documentElement.dataset.theme = s.light ? "light" : "dark";
  const page = (PAGES[s.route] || pages.experiments)(s);
  unmountCharts();
  app.innerHTML = [
    '<div class="shell">',
    titlebar(),
    '<div class="body">',
    sidebar(),
    '<main class="main">',
    s.route === "workspace" && s.tabs.length ? tabs() : "",
    page,
    "</main></div>",
    footer(),
    "</div>",
  ].join("");
  afterRender();
  reportState();
  restoreScroll(scroll);
  mountCharts();
}

/* Apply a market snapshot to the open workspace without rebuilding the page. */
export function updateWorkspace() {
  useShownCalendar();
  patchWorkspace(app, s, chart);
  reportState();
}

/* Apply a market snapshot to the open market preview. */
export function refreshPreview() {
  useShownCalendar();
  updatePreview(app, s, chart);
}

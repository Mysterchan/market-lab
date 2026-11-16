/* The Desktop page's single mutable state object and the element it renders into.

   Python publishes detached views; this object holds those views plus what the
   user is doing in the page (current route, open tabs, unsubmitted form values).
   Nothing here is a financial result, and nothing here is saved except the
   light/dark choice. Modules read and write `s` directly, then call render(). */
export const s = {
  // Where the user is.
  route: "experiments",
  active: null, // Session id shown in the workspace; "manager" for saved results.
  tabs: [], // Session ids with an open workspace tab, in tab order.
  light: localStorage.getItem("market-lab-appearance") !== "dark",

  // Published by Python workers.
  catalog: {}, // Profiles, accounts and saved runs for the selected researcher.
  datasets: {}, // Recordings in the data library.
  agents: {}, // Saved Agent definitions.
  sessions: {}, // Latest view packet for each session id.

  // Unsubmitted input, kept so a market update never clears what the user typed.
  drafts: {}, // Form values by form name: setup, import, agent, preview, ...
  dirtyForms: {}, // Which of those forms have unsaved edits.
  tickets: {}, // Order ticket values by session id.
  handoffDrafts: {}, // Decision-maker form values by session id.

  // Per-session view choices.
  inspector: "order", // Right panel of the active workspace.
  inspectors: {},
  recordTabs: {},
  periods: {}, // Chart bar size in minutes.
  symbols: {}, // Shown symbol of an experiment on several symbols.
  speeds: {}, // Playback speed for controlled experiments.
  auditKinds: {},

  // Page-level selections and progress flags.
  dataset: null, // Expanded recording row.
  query: "", // Recording search text.
  file: "", // Recording chosen for import.
  selectedAgent: null,
  setupStep: 1,
  preparing: false,
  notices: [], // Recent notices, shown in the App activity records.
};

export const app = document.querySelector("#app");

/* Main destinations in the sidebar and the search dialog: route, title, icon. */
export const navigation = [
  ["experiments", "Experiments", "flask"],
  ["workspace", "Trading workspace", "chart"],
  ["data", "Market data", "data"],
  ["agents", "Agents", "agent"],
];

/* Statuses after which an experiment is shown as a read-only result. */
export const ENDED = ["Stopped", "Completed", "Ended", "Failed"];

export const formValues = (form) => Object.fromEntries(new FormData(form));

export const hasUnsavedForms = () => Object.values(s.dirtyForms).some(Boolean);

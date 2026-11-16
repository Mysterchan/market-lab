/* Form and input listeners for the page that was just rendered.

   bindForms() runs after every render(). Each function below owns one form: it
   keeps the user's unsubmitted values in `s.drafts`, and on submit turns them into
   one command for Python. Buttons are handled separately, in actions.js. */
import { s, app, formValues } from "./state.js";
import { send, notice } from "./bridge.js";
import { render, reportState } from "./shell.js";
import { action } from "./actions.js";
import {
  setupDefaults,
  setupSummary,
  setupLegs,
  SEVERAL_SYMBOL_POLICIES,
} from "./setup.js";
import { usd, timeToNs } from "./ui.js";
import { handoffFieldShown } from "./workspace.js";
import { focusedSymbol } from "./session.js";
import { evaluation } from "./compare.js";

const SECOND_NS = 1e9,
  MILLISECOND_NS = 1e6;

/* Attach a listener to a form or control, if the current page has it. */
function on(selector, event, handler) {
  app.querySelector(selector)?.addEventListener(event, handler);
}

/* Attach a submit handler that never lets the browser navigate. */
function onSubmit(selector, handler) {
  on(selector, "submit", (event) => {
    event.preventDefault();
    handler(event.target);
  });
}

/* Run a handler and show a validation error as a notice instead of throwing. */
function reporting(handler) {
  return (...args) => {
    try {
      handler(...args);
    } catch (error) {
      notice(error.message, true);
    }
  };
}

const splitList = (text) =>
  text
    .split(",")
    .map((item) => item.trim())
    .filter(Boolean);

// ---------------------------------------------------------------- navigation

/* Arrow keys, Home and End move between experiment tabs. */
function bindTabKeys() {
  const keys = ["ArrowLeft", "ArrowRight", "Home", "End"];
  app.querySelectorAll("[role=tab]").forEach((tab) =>
    tab.addEventListener("keydown", (event) => {
      if (!keys.includes(event.key)) return;
      event.preventDefault();
      const all = [...app.querySelectorAll("[role=tab]")],
        index = all.indexOf(tab);
      let target;
      if (event.key === "Home") target = 0;
      else if (event.key === "End") target = all.length - 1;
      else {
        const step = event.key === "ArrowRight" ? 1 : -1;
        target = (index + step + all.length) % all.length;
      }
      // Opening a tab re-renders the page, so find the new tab by its label to focus it.
      const label = all[target].getAttribute("aria-label");
      all[target].click();
      app
        .querySelector(`[role=tab][aria-label="${CSS.escape(label)}"]`)
        ?.focus();
    }),
  );
}

function bindRecordingSearch() {
  on("#recording-search", "input", (event) => {
    s.query = event.target.value;
    render();
    // The render replaced the input; put the caret back where the user was typing.
    const input = app.querySelector("#recording-search");
    input.focus();
    input.setSelectionRange(s.query.length, s.query.length);
  });
}

// --------------------------------------------------------------- experiment setup

/* The setup form's values over its defaults, including the unchecked checkbox. */
function readSetup(form) {
  const values = formValues(form);
  const draft = { ...setupDefaults(s), ...values };
  // Rows of further symbols arrive as more_symbol_N / more_path_N fields.
  if ("more_symbol_0" in values || form.querySelector(".add-leg")) {
    draft.more = [];
    for (let index = 0; `more_symbol_${index}` in values; index++)
      draft.more.push({
        symbol: values[`more_symbol_${index}`],
        path: values[`more_path_${index}`],
      });
  }
  for (const key of Object.keys(draft))
    if (/^more_(symbol|path)_\d+$/.test(key)) delete draft[key];
  if (form.elements.allow_implicit_eof)
    draft.allow_implicit_eof = form.elements.allow_implicit_eof.checked;
  if (form.elements.allow_short)
    draft.allow_short = form.elements.allow_short.checked;
  return draft;
}

/* Turn the reviewed setup into the arguments of the `prepare` command. */
function prepareArguments(d) {
  const several = (d.more || []).length > 0;
  const args = {
    path: d.path,
    symbol: d.symbol.toUpperCase(),
    ...(several
      ? {
          symbols: setupLegs(d).map((leg) => leg.symbol.toUpperCase()),
          paths: setupLegs(d).map((leg) => leg.path),
        }
      : {}),
    account_name: d.account_name,
    cash: d.cash,
    ...(d.account !== "new" ? { account: d.account } : {}),
    policy: d.policy,
    // An Agent experiment always runs in the background at 1x.
    execution_mode:
      d.policy === "agent"
        ? "live"
        : d.policy === "model" || several
          ? "controlled"
          : d.execution_mode,
    fee_per_share: d.fee_per_share,
    latency_ns: Number(d.latency_ms) * MILLISECOND_NS,
    quantity: Number(d.quantity || 10),
    hold_ns: Number(d.hold_seconds || 2) * SECOND_NS,
    start_mode: d.start_mode,
    news: several ? null : d.news || null,
    allow_implicit_eof: !!d.allow_implicit_eof,
    allow_short: d.policy !== "agent" && !!d.allow_short,
  };
  if (d.start_mode === "custom") args.start_time_ns = timeToNs(d.start_time);
  if (d.start_mode === "custom" && d.start_date) args.start_date = d.start_date;
  const recording = (s.datasets.files || []).find((file) => file.path === d.path);
  if (recording?.book) args.book = recording.book;
  if (d.policy === "model") {
    args.model_file = d.model_file;
    args.model_parameters = d.model_parameters || "{}";
    args.lookback_bars = Number(d.lookback_bars || 50);
    args.model_python = d.model_python || null;
  }
  if (d.policy === "agent") {
    args.end_ns = timeToNs(d.end_time);
    args.timeout_seconds = Number(d.timeout_seconds);
    args.agent_pacing = d.agent_pacing === "turn" ? "turn" : "live";
  }
  if (d.agent) {
    const [id, revision] = d.agent.split(":");
    args.agent_id = id;
    args.revision = Number(revision);
  }
  return args;
}

function bindSetupForm() {
  const form = app.querySelector("#setup-form");
  if (!form) return;
  form.addEventListener("input", () => {
    s.dirtyForms.setup = true;
    reportState();
    s.drafts.setup = readSetup(form);
    // Refresh only the summary rail, so typing never loses focus.
    const rail = app.querySelector(".form-rail");
    if (rail) rail.outerHTML = setupSummary(s);
  });
  // Adding or removing a symbol row keeps what was typed, then redraws the form.
  const changeLegs = (change) => {
    const draft = readSetup(form);
    change(draft.more);
    // Methods that trade one symbol give way to Manual trading.
    if (draft.more.length && !SEVERAL_SYMBOL_POLICIES.includes(draft.policy))
      draft.policy = "human";
    s.drafts.setup = draft;
    s.dirtyForms.setup = true;
    render();
  };
  form.querySelector(".add-leg")?.addEventListener("click", () =>
    changeLegs((more) =>
      more.push({ symbol: "", path: form.elements.path?.value || "" }),
    ),
  );
  form.querySelectorAll("[data-remove-leg]").forEach((button) =>
    button.addEventListener("click", () =>
      changeLegs((more) => more.splice(Number(button.dataset.removeLeg), 1)),
    ),
  );
  // The decision maker and every select change which fields the form shows.
  form
    .querySelectorAll("[name=policy]")
    .forEach((radio) =>
      radio.addEventListener("change", () => {
        render();
        // Bring the chosen method's own settings, just under its card, into view.
        const card = app.querySelector("input[name=policy]:checked")?.closest("label");
        let last = card;
        while (last?.nextElementSibling && !last.nextElementSibling.matches(".choice-card"))
          last = last.nextElementSibling;
        last?.scrollIntoView({ block: "nearest" });
      }),
    );
  form
    .querySelectorAll("select")
    .forEach((select) => select.addEventListener("change", () => render()));
  onSubmit(
    "#setup-form",
    reporting(() => {
      if (s.preparing) return;
      s.drafts.setup = readSetup(form);
      // Steps one and two only move forward; the review step starts preparation.
      if (s.setupStep < 3) {
        s.setupStep++;
        render();
        return;
      }
      const args = prepareArguments(setupDefaults(s));
      s.preparing = true;
      send("prepare", args);
      render();
    }),
  );
}

// ------------------------------------------------------------------ workspace

function bindOrderForm() {
  const form = app.querySelector("#order-form");
  if (!form) return;
  form.addEventListener("input", () => {
    const ticket = {
      ...(s.tickets[s.active] || { side: "buy" }),
      ...formValues(form),
    };
    s.tickets[s.active] = ticket;
    // Update the two estimates in place; a re-render would move the caret.
    const view = s.sessions[s.active]?.view,
      fee =
        view?.execution?.config?.fee_per_share ??
        view?.run?.config?.execution?.fee_per_share;
    const estimate = app.querySelector("#order-estimate"),
      feeNode = app.querySelector("#order-fee");
    if (estimate)
      estimate.textContent = usd(
        Number(ticket.quantity) * Number(ticket.limit_price) * 10000,
      );
    if (feeNode)
      feeNode.textContent =
        fee == null ? "—" : usd(Number(ticket.quantity) * fee);
  });
  // The order type changes which fields the ticket shows.
  form
    .querySelector("[name=order_type]")
    ?.addEventListener("change", () => render());
  onSubmit("#order-form", () => {
    const d = formValues(form);
    const market = d.order_type === "market";
    send("session", {
      session: s.active,
      method: "order",
      client_id: d.client_id?.trim() || "web-" + crypto.randomUUID(),
      side: s.tickets[s.active]?.side || "buy",
      quantity: Number(d.quantity),
      order_type: market ? "market" : "limit",
      // Several symbols: the order is for the symbol the ticket shows.
      ...(focusedSymbol(s, s.active) ? { symbol: focusedSymbol(s, s.active) } : {}),
      // Sent as text; Python converts it to exact integer money.
      ...(market ? {} : { limit_price: d.limit_price }),
    });
  });
}

/* When the new decision maker takes over: now, at a time, or at a position size. */
function handoffCondition(d) {
  if (d.condition === "time")
    return { kind: "time", time_ns: timeToNs(d.condition_value) };
  if (d.condition !== "position_at_least") return null;
  const quantity = Number(d.condition_value);
  if (!Number.isInteger(quantity) || quantity < 1)
    throw Error("Enter a positive whole position quantity.");
  return {
    kind: "position_at_least",
    symbol: focusedSymbol(s, s.active) || s.sessions[s.active].view.market.symbol,
    quantity,
  };
}

/* Show only the fields the chosen policy and switch condition use. */
function showHandoffFields(form) {
  const policy = form.querySelector("[name=policy]").value;
  const condition = form.querySelector("[name=condition]").value;
  form.querySelectorAll(".handoff-field").forEach((wrapper) => {
    wrapper.hidden = !handoffFieldShown(wrapper.dataset.field, policy, condition);
  });
  const position = condition === "position_at_least";
  const label = form.querySelector("label[for=condition_value]");
  if (label) label.textContent = position ? "Position quantity · shares" : "Time · HH:MM:SS ET";
  const input = form.querySelector("[name=condition_value]");
  if (input) input.placeholder = position ? "100" : "09:30:00.125";
}

function bindHandoffForm() {
  const form = app.querySelector("#handoff-form");
  if (!form) return;
  form.addEventListener(
    "input",
    () => (s.handoffDrafts[s.active] = formValues(form)),
  );
  // The policy and the switch condition decide which fields the form shows.
  form
    .querySelectorAll("select[name=policy], select[name=condition]")
    .forEach((select) => select.addEventListener("change", () => showHandoffFields(form)));
  onSubmit(
    "#handoff-form",
    reporting(() => {
      const d = formValues(form);
      send("session", {
        session: s.active,
        method: "handoff",
        policy: d.policy,
        // Hidden fields keep their defaults: 10 shares, a 2-second hold.
        quantity: Number(d.quantity || 10),
        hold_ns: Number(d.hold_seconds || 2) * SECOND_NS,
        state_mode: d.state_mode,
        condition: handoffCondition(d),
      });
    }),
  );
}

// ---------------------------------------------------------------- market data

function bindImportForm() {
  const form = app.querySelector("#import-form");
  if (!form) return;
  form.addEventListener("input", () => {
    s.dirtyForms.import = true;
    reportState();
    s.drafts.import = { ...(s.drafts.import || {}), ...formValues(form) };
  });
  onSubmit("#import-form", () => {
    s.importing = true;
    s.importPending = true;
    const values = formValues(form);
    s.importRequest = {
      path: s.file,
      copy: values.copy === "true",
      // Dollars are converted to integer money in Python.
      ...(values.spread
        ? { book: { spread: values.spread, depth: Number(values.depth) } }
        : {}),
    };
    send("add_dataset", s.importRequest);
    render();
  });
}

function readPreview(form) {
  return {
    ...formValues(form),
    allow_implicit_eof: form.elements.allow_implicit_eof.checked,
  };
}

function bindPreviewForm() {
  on("#preview-form", "input", (event) => {
    s.drafts.preview = readPreview(event.currentTarget);
  });
  onSubmit(
    "#preview-form",
    reporting((form) => {
      const d = readPreview(form);
      s.drafts.preview = d;
      const args = {
        path: d.path,
        symbol: d.symbol,
        news: several ? null : d.news || null,
        allow_implicit_eof: d.allow_implicit_eof,
        start_mode: d.start_mode,
      };
      if (d.start_mode === "custom")
        args.start_time_ns = timeToNs(d.start_time);
      s.previewLoading = true;
      send("load_market", args);
      render();
    }),
  );
}

// --------------------------------------------------------------------- agents

/* The model settings an Agent definition stores from the editor's fields. */
const agentModel = (d) => {
  const provider = d.provider || "openai-responses";
  const model = { provider, model: d.model, credential_ref: d.credential_ref };
  // A local server is reached at its URL and may need no key.
  if (provider === "openai-compatible")
    model.base_url = d.base_url || "http://127.0.0.1:11434/v1";
  return model;
};

function bindAgentForm() {
  const form = app.querySelector("#agent-form");
  if (!form) return;
  form.addEventListener("input", () => {
    s.dirtyForms.agent = true;
    reportState();
    const d = formValues(form);
    const draft = { ...(s.drafts.agent || {}), ...d };
    draft.requires = splitList(d.requires);
    draft.model = agentModel(d);
    draft.parametersText = d.parameters;
    // Parsed when valid. While the user is mid-edit the raw text stays in the draft.
    try {
      draft.parameters = JSON.parse(d.parameters);
      draft.events = JSON.parse(d.events);
    } catch {}
    s.drafts.agent = draft;
  });
  form
    .querySelectorAll("select")
    .forEach((select) => select.addEventListener("change", () => render()));
  onSubmit(
    "#agent-form",
    reporting(() => {
      const d = formValues(form),
        parameters = JSON.parse(d.parameters);
      if (
        !parameters ||
        Array.isArray(parameters) ||
        typeof parameters !== "object"
      )
        throw Error("Parameters must be a JSON object");
      const values = {
        ...(s.drafts.agent || {}),
        ...d,
        parameters,
        requires: splitList(d.requires),
        model: agentModel(d),
        schedule_mode: d.schedule_mode,
        busy_policy: d.busy_policy,
        events: JSON.parse(d.events),
      };
      s.saving = true;
      send("save_agent", { values });
      render();
    }),
  );
}

function readEnvironment(form) {
  return {
    ...formValues(form),
    allow_download: form.elements.allow_download.checked,
    allow_python_download: form.elements.allow_python_download.checked,
  };
}

function bindEnvironmentForm() {
  on("#environment-form", "input", (event) => {
    s.drafts.environment = readEnvironment(event.currentTarget);
  });
  onSubmit("#environment-form", (form) => {
    const draft = readEnvironment(form);
    s.drafts.environment = draft;
    const agent = (s.agents.agents || []).find(
      (a) => a.agent_id === s.selectedAgent,
    );
    s.environmentBusy = true;
    s.environmentStatus = "Preparing Python and dependencies…";
    send("prepare_environment", {
      agent_id: agent.agent_id,
      revision: agent.revision,
      ...draft,
      // Empty offline inputs mean "not provided".
      wheelhouse: draft.wheelhouse || null,
      python_archive: draft.python_archive || null,
    });
    render();
  });
}

// ------------------------------------------------- settings, records and replay

function bindSettingsForms() {
  onSubmit("#account-form", (form) => send("create_account", formValues(form)));
  onSubmit("#profile-form", (form) => {
    const name = formValues(form).name.trim();
    if (name) send("create_profile", { name });
  });
  onSubmit("#credential-form", (form) => {
    send("save_credential", formValues(form));
    // The secret now lives in Python memory only; never keep it in the page.
    form.querySelector("[name=secret]").value = "";
  });
}

function bindRecordControls() {
  on("#audit-kind", "change", (event) => {
    s.auditKinds[s.active] = event.target.value;
    s.auditSelection ||= {};
    s.auditSelection[s.active] = 0;
    render();
  });
  on("#replay-speed", "change", (event) => {
    s.replaySpeed = Number(event.target.value);
    // Restart playback so the new speed applies from the current event.
    if (s.replayPlaying) action("replay-play");
  });
  on("#replay-index", "change", (event) => {
    s.replayIndex = Number(event.target.value);
    render();
  });
}

function bindReplayAndComparisonForms() {
  onSubmit("#comparison-form", (form) => {
    const d = formValues(form);
    s.drafts.comparison = d;
    s.comparing = true;
    send("compare", {
      path: d.path,
      symbol: d.symbol,
      cash: d.cash,
      // The comparison's own variants set the hold time (1 and 2 seconds).
      quantity: Number(d.quantity),
    });
    render();
  });
  onSubmit("#replay-form", (form) => {
    const d = formValues(form);
    s.replayBusy = true;
    s.replayPath = d.path;
    s.replayAccount = d.account;
    send("prepare_replay", { path: d.path, account: d.account || null });
    render();
  });
}

/* Compare runs: run and group choices, the tested metric, baseline and cutoffs. */
function bindEvaluationControls() {
  const e = evaluation();
  app.querySelectorAll("[data-eval-run]").forEach((box) =>
    box.addEventListener("change", () => {
      if (box.checked) e.selected[box.dataset.evalRun] = true;
      else delete e.selected[box.dataset.evalRun];
      render();
    }),
  );
  app.querySelectorAll("[data-eval-group]").forEach((box) =>
    box.addEventListener("change", () => {
      const runs = (e.candidates?.runs || []).filter((r) => r.label === box.dataset.evalGroup);
      const chosen = (e.candidates?.runs || []).find((r) => e.selected[r.id]);
      for (const run of runs) {
        const allowed = run.comparable && (!chosen || run.market === chosen.market);
        if (box.checked && allowed) e.selected[run.id] = true;
        else if (!box.checked) delete e.selected[run.id];
      }
      render();
    }),
  );
  on("[data-eval-metric]", "change", (event) => {
    e.metric = event.target.value;
  });
  on("[data-eval-baseline]", "change", (event) => {
    e.baseline = event.target.value;
  });
  app.querySelectorAll("[data-eval-price]").forEach((input) =>
    input.addEventListener("input", () => {
      e.prices[input.dataset.evalPrice] = input.value;
    }),
  );
  app.querySelectorAll("[data-eval-cutoff]").forEach((input) =>
    input.addEventListener("input", () => {
      e.cutoffs[input.dataset.evalCutoff] = input.value;
    }),
  );
}

export function bindForms() {
  bindTabKeys();
  bindRecordingSearch();
  bindSetupForm();
  bindImportForm();
  bindAgentForm();
  bindOrderForm();
  bindHandoffForm();
  bindEnvironmentForm();
  bindSettingsForms();
  bindRecordControls();
  bindPreviewForm();
  bindReplayAndComparisonForms();
  bindEvaluationControls();
}

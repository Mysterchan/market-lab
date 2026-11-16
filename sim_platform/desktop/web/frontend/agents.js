/* Selection and version context mirror the accepted Agent library layout. */
import { esc, icon, button as b, head, empty, field } from "./ui.js";
const integration = {
  configured: "Template",
  python_strategy: "Python strategy",
  python_carrier: "Python carrier",
};
export function agents(s) {
  const rows = s.agents.agents || [];
  const chosen =
    rows.find((row) => row.agent_id === s.selectedAgent) || rows[0];
  return (
    head("Agents", b("New Agent", "agent-new", "primary", "plus")) +
    `<div class="content"><div class="table-layout agent-library"><section><div class="toolbar library-toolbar"><span class="muted">Saved definitions</span><span class="push badge">${rows.length} ${rows.length === 1 ? "Agent" : "Agents"}</span></div>${rows.length ? rows.map((row) => `<button class="agent-card ${row.agent_id === chosen?.agent_id ? "selected" : ""}" data-action="agent-select:${esc(row.agent_id)}" aria-pressed="${row.agent_id === chosen?.agent_id}"><span class="agent-icon">${icon("agent")}</span><span class="agent-card-copy"><strong>${esc(row.definition.name)}</strong><span>${esc(integration[row.definition.integration] || "Agent")} · ${schedule(row.definition.schedule)}</span></span><span class="badge">Revision ${row.revision}</span></button>`).join("") : empty("No saved Agents", "Create an Agent definition or import a local Python project.", b("New Agent", "agent-new", "secondary", "plus"))}</section>${chosen ? details(chosen) : ""}</div></div>`
  );
}
function schedule(timing) {
  if (!timing) return "Own execution loop";
  return timing.mode === "interval"
    ? "Every " + esc(timing.interval_seconds) + "s"
    : timing.mode === "event"
      ? "Event triggered"
      : "Continuous";
}
function details(row) {
  const d = row.definition;
  return `<aside class="inspector"><div class="inspector-top">Agent definition</div><div class="inspector-body"><div class="agent-icon">${icon("agent")}</div><h2>${esc(d.name)}</h2>${d.initial_prompt ? `<p class="agent-description">${esc(d.initial_prompt)}</p>` : ""}<div class="metadata"><div class="meta-row"><span>Implementation</span><span>${esc(integration[d.integration] || d.integration)}</span></div><div class="meta-row"><span>Revision</span><span>${row.revision}</span></div><div class="meta-row"><span>Environment</span><span>${esc(row.environment)}</span></div><div class="meta-row"><span>Schedule</span><span>${schedule(d.schedule)}</span></div></div><div class="section-rule"><h3>Granted tools</h3><div class="chip-list">${(d.requires || []).map((tool) => `<span class="chip">${esc(tool)}</span>`).join("")}</div></div>${b("Use in experiment", "agent-use:" + row.agent_id, "primary", "plus")}${b("Edit definition", "agent:" + row.agent_id, "link", "settings", 'style="margin-top:12px"')}${d.package ? `<div class="section-rule">${b("Prepare environment", "environment:" + row.agent_id, "secondary", "refresh")}</div>` : ""}</div></aside>`;
}

export function environment(s) {
  const row = (s.agents.agents || []).find(
    (a) => a.agent_id === s.selectedAgent,
  );
  if (!row)
    return (
      head("Agent environment") +
      empty(
        "Select an Agent first",
        "Choose a Python Agent from your library.",
        b("Agents", "agents", "secondary"),
      )
    );
  const d = s.drafts.environment || {};
  return (
    head("Prepare " + row.definition.name) +
    `<div class="content"><div class="form-layout"><form id="environment-form"><section class="form-block"><h2>Python and dependencies</h2><p class="muted">Prepare the interpreter and libraries for this saved revision. Your Agent starts only when you open an experiment.</p><label class="choice-card"><input type="checkbox" name="allow_python_download" ${d.allow_python_download ? "checked" : ""}><span><strong>Download Python if required</strong><small>Use a managed interpreter compatible with this Agent.</small></span></label><label class="choice-card"><input type="checkbox" name="allow_download" ${d.allow_download ? "checked" : ""}><span><strong>Download dependency wheels</strong><small>Allow packages from PyPI. Source builds are not run.</small></span></label></section><details class="advanced-section"><summary>Offline setup</summary>${field("Python runtime archive", "python_archive", d.python_archive || "")}${b("Choose archive…", "pick:python-archive", "secondary", "folder")}${field("Dependency wheels folder", "wheelhouse", d.wheelhouse || "")}${b("Choose folder…", "pick:wheelhouse", "secondary", "folder")}</details><div class="form-actions"><button class="primary" ${s.environmentBusy ? "disabled" : ""}>${s.environmentBusy ? "Preparing…" : "Prepare environment"}</button>${s.environmentBusy ? b("Cancel preparation", "cancel-environment", "quiet") : b("Back to Agents", "agents", "quiet")}</div>${s.environmentStatus ? `<p role="status" class="inline-hint">${esc(s.environmentStatus)}</p>` : ""}</form><aside class="form-rail"><h3>Saved revision ${row.revision}</h3><p>${esc(row.environment)}</p>${row.environment === "Ready" ? b("Use in experiment", "agent-use:" + row.agent_id, "primary", "plus") : ""}</aside></div></div>`
  );
}

/* Entry point: connects the modules below and starts the page.

   state.js          the one mutable UI state object
   bridge.js         commands to Python, events from Python, notices
   shell.js          page frame, navigation, render and in-place workspace updates
   actions.js        what each data-action button does
   forms.js          form drafts and submits
   events.js         what each published event changes
   search.js         the ⌘K dialog
   replay-player.js  browser playback of a verified Replay

   Page modules (workspace.js, results.js, pages.js, ...) only turn state into markup. */
import { s, app } from "./state.js";
import { send, connect } from "./bridge.js";
import { render, onRender } from "./shell.js";
import { action } from "./actions.js";
import { bindForms } from "./forms.js";
import { receive } from "./events.js";
import { initSearch } from "./search.js";
import { isPlaying } from "./session.js";

onRender(bindForms);
initSearch(action);

// One listener serves every button: markup names its action in `data-action`.
app.addEventListener("click", (event) => {
  const target = event.target.closest("[data-action]");
  if (target && app.contains(target) && !target.disabled) {
    event.stopPropagation();
    action(target.dataset.action);
  }
});

// The speed selector lives in the workspace toolbar, which market snapshots replace,
// so it is handled here instead of being bound to one element.
app.addEventListener("change", (event) => {
  if (event.target.id !== "playback-speed") return;
  s.speeds[s.active] = event.target.value;
  // A running experiment restarts playback so the new speed takes effect.
  if (isPlaying(s.sessions[s.active]?.view)) {
    action("pause");
    action("play");
  }
});

connect(receive, () => {
  render();
  send("refresh");
});

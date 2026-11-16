# Desktop frontend

The default Desktop now loads `index.html` inside one local-only WebEngine view.
It uses the accepted 2026-10-31 HTML reference's visual tokens and navigation rules.
It does not load the prototype's sample state, localStorage clock, synthetic chart
or fake trading handlers. All financial values come from Python workers.

| Owner | Responsibility |
| --- | --- |
| `app.js` | Entry point: connects the modules below and installs the page-wide listeners |
| `state.js` | The one mutable UI state object, with each field's purpose |
| `bridge.js` | Commands to Python, events from Python, notices |
| `shell.js` | Title bar, sidebar, tabs, navigation, full render and in-place workspace updates |
| `actions.js` | What each `data-action` button does, as two lookup tables |
| `forms.js` | One binder per form: unsubmitted drafts and the command each submit sends |
| `events.js` | One handler per published event kind, and when a render follows |
| `search.js` | The ⌘K page/experiment search dialog |
| `replay-player.js` | Browser playback cursor over a verified Replay's recorded events |
| `pages.js` | Data library/import, Agent editor and settings |
| `compare.js` | Compare runs: grouped saved runs, test settings and the evaluation report |
| `setup.js` | Market/account/review steps, retained inputs and live summary |
| `agents.js` | Agent cards, revision inspector and Python environment setup |
| `results.js` | Read-only saved report: metrics, equity curve, executions on price, fills and timeline |
| `audit.js` | Published financial/Agent records, transcripts and App activity |
| `preview.js` | Account-free market preparation, playback and depth |
| `replay.js` | Verified operation records, playback and event inspector |
| `session.js` | Shared session names, policy labels and terminal status |
| `workspace.js` | Published chart/account/records, fill and working-order marks, order ticket, depth ladder, decision controls |
| `ui.js` | Escaping, display formatting, fields, buttons and icon paths |
| `tokens.css` | Reference-derived shared component styling |
| `desktop.css` | Integration layouts and real-data display adjustments |
| `../frontend_bridge.py` | Named command channel and bundled-document restrictions |
| `../window.py` | GUI composition, native file selection, command translation |
| `../menus.py` | Native menus, standard edit actions, contextual playback and zoom |
| `../../quit_guard.py` | OS Quit routed through asynchronous worker shutdown |
| `../workers.py` | GUI-thread event relays and one queued worker per experiment |

Management pages replace the central content. Only Trading workspace displays
experiment tabs. Data rows expand and collapse their own details. Closing a tab
hides the view, without ending its Python experiment; Play/Pause/End govern the
actual session. Completed experiments open a read-only report rather than an order
ticket. Setup uses three compact steps with a persistent summary; Agent definitions
use selectable cards and a contextual inspector.

To add a button, give it a `data-action` name and add that name to a table in
`actions.js`; a name in neither table is treated as a route. To add a form, add a
binder to `forms.js`. To react to a new published event, add a handler to `events.js`.
Modules import downwards only: `app.js` → `events.js`/`forms.js` → `actions.js` →
`shell.js` → `bridge.js`/`state.js`.

Market snapshots update changed workspace regions without replacing the chart or
order form. Keep the focused field, unsubmitted values and per-session drafts when
new events arrive. Routine catalogue/task refreshes must not redraw the workspace
or take the user away from another page.

App shutdown cancels background preparation, shuts down every
runtime, and waits for threads before deleting the WebEngine widget tree.

Each experiment owns a worker/runtime against the same workspace storage. Setup
creates an independent account by default, or selects an existing account that is
not already used by an open experiment. Never share a mutable controller merely to implement tabs.
Qt remains the Desktop host; there is no new HTTP service, Node runtime or Electron
packaging requirement. The browser has no direct filesystem, SQL or model access.
Session credentials stay in Python memory and are propagated to experiment workers;
the renderer clears credential inputs after submission. They are not stored in
frontend drafts, localStorage or logs.

The default frontend exposes guarded reset, managed storage selection, interrupted-run
recovery, SDK export, and environment preparation with separate Python/dependency
download choices and offline inputs. Dataset hashing/copying uses cancellable
background jobs, with partial copies removed before returning an error.

There is no alternate classic interface. Source options, existing accounts, timed
and position-based handoffs, policy-state restoration, published record inspection,
market preview and comparison export are exposed in this frontend. See [Desktop release gates](../../../../docs/development/desktop-release.md)
for the local binary candidate and remaining external acceptance requirements.

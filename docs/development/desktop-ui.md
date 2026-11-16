# Desktop UI structure and interaction rules

Desktop uses one bundled HTML frontend hosted by Qt WebEngine. All pages use
browser CSS, not native widget stylesheets. PySide6 remains responsible for the
window, native menu bar, OS pickers and worker transport. There is no `--classic`
entry or retained native page tree.

The accepted visual/navigation baseline remains frozen at
`prototypes/desktop-v2/references/2026-10-31-approved-ui/`.
See the [ownership map](../../sim_platform/desktop/web/frontend/README.md) before editing.

## Interaction rules

- Management destinations replace the central page and keep the sidebar visible.
  Only experiment workspaces use tabs; closing a tab does not end its experiment.
- Add/edit tasks use embedded pages with submit/cancel actions. OS file/folder
  pickers and explicit confirmations are the dialog exceptions.
- Dataset copy/reference is chosen during import. Expand a data row to inspect
  exact size, path and available actions. Preview market prepares data without an
  account or experiment; Create experiment opens the setup form.
- Keep focused inputs, drafts, the chart and selection across market updates.
  Background completion must not steal navigation from another page.
- Conditional handoff and manual orders go through workers. Browser playback of a
  verified Replay selects recorded events; it never reruns models or matches orders.
- Independent experiments cannot share an active account. Account selection and
  preparation validation enforce this before starting a worker's experiment.
- Display unavailable financial values as missing. Never substitute synthetic prices.

## Verify changes

`tools/capture_desktop.py` captures the actual App against temporary fixtures, away
from user storage. `tests/platform/test_desktop*.py` exercises real accounts,
orders, independent clocks, preparation cancellation, Agent revisions and Python
runtimes, Replay, record contrast, error recovery, menu dispatch and shutdown.
Inspect the process exit as well as assertions; workers must drain before WebEngine
is deleted. Include narrow windows, keyboard input and both appearances in review.

[Current user guide](../user-guide/desktop.md) · [Build and release gates](desktop-release.md)

# Market Lab Desktop

Market Lab replays historical market data in a simulated account. It does not
connect to a broker. Manual trading and Agent experiments use the same workspace;
each experiment has its own orders and clock, and cannot share an account with
another open experiment.

## Open the App

A locally built macOS Apple Silicon candidate is `build/desktop-dist/Market Lab.app`.
Double-click it in Finder. It contains Python, Qt, the native market library, the
Agent SDK and the Python manager; running the App does not require Make or a C++
compiler. This is an internal candidate, not a signed/notarized public download.

For source development, install `requirements-desktop.txt`, run `make all`, then:

```sh
.venv/bin/python -m sim_platform desktop
# Temporary synthetic data and accounts, removed after quitting:
.venv/bin/python -m sim_platform desktop --demo
```

For a temporary demo using the local App bundle:

```sh
open -n 'build/desktop-dist/Market Lab.app' --args --demo
```

The normal workspace is stored in `~/Library/Application Support/MarketLab` on
macOS. Restarting the App keeps saved researchers, Agent definitions, data
registrations and experiment results. Experiments do not continue after quitting.

## Complete your first experiment

1. Open **Market data**, then **Add recording**. Choose an uncompressed ITCH
   BinaryFILE (`.bin` or `.itch`). Extract downloaded archives first.
2. Choose **Keep in its current location** or **Copy into this workspace**, then
   **Add recording**. Large files show progress; **Cancel import** leaves the
   original recording intact and removes partial copies.
3. In the library, click the recording row to expand its details. Choose
   **Create experiment**. A missing/changed recording must be located or added again.
4. Choose the symbol, start point and decision maker. For a first trial, use
   **Manual trading** and **First available bid and ask**. **Add another symbol**
   trades [several symbols](several-symbols.md) from one account.
5. Continue to account/execution settings. Create an account with starting cash,
   or select an existing account not used by an open experiment. Set fees and latency.
   **Controlled** supports pause and step; **Live 1×** follows wall-clock time.
6. Review the summary and choose **Prepare experiment**. Preparation is cancellable;
   the workspace opens paused when ready.
7. Use **Play**, **Pause** or **Step market**. Manual orders use quantity and a
   limit price; the ticket shows estimated value, fees and available cash.
8. Inspect **Orders**, **Positions**, **Fills** and **Activity** below the chart.
   Cancel unfilled orders from their rows. Actual prices/fees retain sub-cent
   precision where the engine publishes it.
9. Choose **End experiment** to cancel remaining orders and save the result.
   Completed experiments open a read-only report, with metrics and recorded events.
   **Export report** writes a JSON result to a location you choose. Each figure on the
   result page is defined in [Read a saved result](results.md).

## What each page does

| Page | Purpose |
| --- | --- |
| Experiments | Open existing workspaces and inspect saved results. A run left interrupted by a crash can be ended here. |
| Trading workspace | Chart, controls, account statistics, order ticket/Agent activity and financial records for an open experiment. |
| Market data | Register, copy, locate and inspect recordings; details give the file type and, for price bars, the dates covered. Preview market reads a recording without creating an experiment or account. |
| Agents | Create/import versioned Agent definitions, edit a new revision, prepare Python environments and use a saved revision in an experiment. |
| Recorded Replay | Verify an original Live operation log and inspect its events without calling the Agent/model again. |
| Compare runs | Choose finished runs, grouped by Agent revision, rule, model file or manual trading, and [evaluate](evaluation.md) them against a baseline with intervals and tests. **Quick rule comparison** below runs the Buy → Hold → Sell rule holding 1 and 2 seconds on new accounts. |
| Settings | Appearance, researcher selection/creation, credential references, future managed-copy storage and guarded workspace reset. |

Only Trading workspace has experiment tabs. Opening another page replaces the
central page while keeping the navigation. Closing a tab hides that workspace;
it does not end its experiment. Use End experiment for that. Switching tabs keeps
each order draft, selected records, chart interval and inspector separately.
Market updates do not replace the focused order field or chart element.

## Additional controls

Market setup's **Additional data** accepts an announcement file and controls
whether an implicit recording ending is allowed. Agent setup also exposes an end
time in Eastern Time and a task timeout.

**Preview market** opens an inline page with symbol/start settings, bid/ask depth
and Play/Pause/Step. Cancel preparation stops a pending read. It creates no account.

In controlled workspaces, select playback speed in the toolbar. **Controls** can
handoff decisions immediately, at an exact market time, or when a position reaches
a quantity. Choose reset or restore when returning to a previously used policy.
Manual order **Order reference** accepts an optional client ID.

**Records** opens published account events, trades, minute bars, announcements,
decisions, handoffs, Agent work/transcripts and App activity. Select a row for its
structured details. These are the records published in the current view, not an
unbounded database export. **Compare runs → Export report** saves an evaluation;
**Quick rule comparison → Export comparison** saves that comparison's results.

Recorded Replay lets you select an existing destination account or create one,
cancel verification, and choose playback speed. Its viewer follows the recorded
event times without recreating trades. Settings can create accounts and explicitly
load a saved OS credential reference into the current session.

## Run an Agent

Create an Agent under **Agents → New Agent**. A configured Agent needs a task,
model identifier, credential reference and appropriate tools. Save the API key
under the same reference in **Settings → Model credential**. Keys are kept in
Python memory for the session or the selected OS credential store, not in Agent
configuration or frontend drafts. Changing a definition creates a revision;
existing experiments keep the revision they started with.

For a Python Agent/carrier, select a local source file or project folder. Choose
**Prepare environment** from its details. The environment page offers separate
choices for downloading Python and dependency wheels. Downloads are off until
you choose them. Offline setup accepts a trusted standalone Python archive and a
folder of prebuilt dependency wheels. Preparation can be cancelled and retried.
The packaged App's Python is not used as an Agent development interpreter.

After the environment is ready, choose **Use in experiment**, select the recording
and account settings, then prepare. Under **Pacing**, choose **Live 1×** (the market keeps
moving while the Agent thinks) or **Turn-based** (the market waits for each decision;
see [turn-based Agents](evaluation.md#turn-based-agents)). Play/Pause/End are
contextual, and the manual order ticket is disabled while an Agent owns decisions.
Real model requests use your configured provider account and may incur charges.
The automated acceptance checks use an explicit local model stub.

Use **File → Export Agent SDK** to save the installable wheel for your development
environment. Install the exported file with `python -m pip install /path/to/file.whl`.
This does not imply that the package has been published to PyPI.

## Menus, errors and quitting

The native menu bar provides File, Edit, View, Experiment, Window and Help. On
macOS, About, Settings and Quit appear in the Market Lab application menu. Common
shortcuts use Command on macOS and Control on other platforms:

| Action | Shortcut |
| --- | --- |
| New experiment | Command-N |
| Add recording | Command-O |
| Search workspace | Command-K |
| Settings | Command-comma |
| Play/pause selected experiment | Command-Return |
| Step market | Command-Shift-Right |
| Close experiment tab | Command-W |
| Quit App | Command-Q |

Search closes with Escape, Cancel or clicking outside. View includes zoom controls
for readability. Edit uses the focused browser field for Undo, Cut, Copy and Paste.

A failed task keeps its form editable. If the display renderer fails, Reload
workspace reconnects to the existing Python workers; it does not create a second
trading engine. Quitting while work or unsaved form changes exist asks for
confirmation. Cancel keeps the App running. Confirm ends experiments, cancels
background work, saves results and waits for workers before closing the window.

Reset local workspace is destructive and requires confirmation. It is blocked
while experiments or background work are active. Original recordings remain, but
researchers, accounts, results, definitions and data registrations are removed.
There is no automatic backup. Keep exports separately before using Reset.

[User guide](README.md) · [Build and release validation](../development/desktop-release.md)

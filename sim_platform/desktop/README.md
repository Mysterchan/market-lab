# Desktop application

Desktop has one HTML/CSS/JavaScript frontend. PySide6 hosts its local WebEngine
surface, native menus and OS file pickers. There is no classic interface or
alternate widget implementation.

```sh
.venv/bin/python -m sim_platform desktop
.venv/bin/python -m sim_platform desktop --demo
```

The normal command opens saved local data; the demo uses temporary synthetic
recordings and accounts. The frozen accepted reference is
`prototypes/desktop-v2/references/2026-10-31-approved-ui/`; production changes do not
modify that reference.

| Responsibility | Location |
| --- | --- |
| Application configuration and sole window entry | `app.py` |
| HTML pages, navigation, forms and rendering | `web/frontend/` |
| Window lifecycle, native selections and command translation | `web/window.py` |
| Bounded channel and local-document restrictions | `web/frontend_bridge.py` |
| Native menu actions | `web/menus.py` |
| GUI-thread relays and per-experiment workers | `web/workers.py` |
| Platform ownership and queued execution | `runtime.py`, `worker.py`, `tasks.py` |
| Agent, preparation and Replay workflows | `workflows/` |
| Safe storage replacement | `reset.py` |

```text
HTML input -> bounded channel -> queued worker command -> Platform/controller
  -> detached published view -> HTML rendering
```

Workers own accounts, execution, Agents and financial clocks. Browser code never
queries financial storage, calls a model or matches orders. Market updates retain
the focused field, chart and unsubmitted order. Management tasks replace central
content; only experiment workspaces have tabs. OS pickers and confirmations are
native dialogs, not a second application interface.

`create_application()` configures WebEngine before creating QApplication. Importing
platform/CLI modules does not import Qt. Closing the App cancels preparation,
ends experiments and drains workers before deleting the WebEngine tree.

[Frontend ownership](web/frontend/README.md) · [User guide](../../docs/user-guide/desktop.md) · [Build and release gates](../../docs/development/desktop-release.md)

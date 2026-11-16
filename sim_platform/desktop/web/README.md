# Desktop web presentation

[frontend/](frontend/README.md) is the sole Desktop interface. `window.py` owns the
window and Python integration, `frontend_bridge.py` restricts channel commands to
an explicit list and local bundled content, and `workers.py` relays queued worker
results on the GUI thread. `menus.py` integrates standard native menu actions.

`chart.js` draws published market bars with zoom, pan, a crosshair and marks for
published fills and working orders. Saved-run equity and drawdown are computed in
`../presentation.py` from complete fills and archived bars. HTML renders detached Platform views;
financial calculations, account mutations and model access remain in Python.
Remote navigation, remote scripts and filesystem access from the page are disabled.

The App needs no web server, CDN, Node runtime or separate native widget pages.
Native file pickers and destructive confirmations remain OS dialogs. The bundled
icons retain their license in this directory.

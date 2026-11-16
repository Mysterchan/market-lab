# Historical Desktop workbench reference

This directory preserves the earlier workbench design study and its synthetic
screenshots. It describes the former native Qt shell with an embedded HTML
workspace, rather than the current application structure. The native page,
button, tab and choice-control implementations have been removed.

For current implementation ownership and interaction rules, use
[Desktop UI structure](../desktop-ui.md) and the
[frontend ownership map](../../../sim_platform/desktop/web/frontend/README.md).
The later approved complete-App reference remains frozen in
`prototypes/desktop-v2/references/2026-10-31-approved-ui/`.

| File | Purpose |
| --- | --- |
| [workbench.html](workbench.html) | Standalone historical study with embedded CSS, JavaScript and icons |
| [workbench.fragment.html](workbench.fragment.html) | Original source fragment |
| [reference-metrics.json](reference-metrics.json) | Earlier Qt WebEngine geometry, fonts and colors at 990 × 828 content size |
| [screenshots](screenshots/) | Earlier native Desktop dark/light/order-detail captures |
| [LUCIDE-LICENSE.txt](LUCIDE-LICENSE.txt) | License for the embedded Lucide icons |

## Open the historical study

Open `workbench.html` directly, or serve this directory:

```sh
python3 -m http.server 8000 --bind 127.0.0.1 --directory docs/development/desktop-design
```

Use `http://127.0.0.1:8000/workbench.html`. Scene queries include `?theme=dark`,
`?theme=light`, `?theme=dark&order=entry`, `?theme=dark&table=positions` and
`?theme=dark&tool=quote`. These controls only demonstrate illustrative display
states; they do not send trades, run models or modify application data.

## Interpret the captures

Compare matching content, theme, scale and state. The study's enclosing chrome and
sample values do not establish visual parity with the current App. Historical
metrics include device-pixel ratio; cross-OS font rasterization is not assumed
identical. Preserve these reference assets during unrelated implementation work.

Current captures come from `tools/capture_desktop.py`, using the sole HTML
frontend and temporary synthetic data. Current integration tests live in
`tests/platform/test_desktop*.py`. Neither tool depends on the removed native
widgets or their old screenshot filenames.

[Desktop workflow](../../user-guide/desktop.md) · [Verification](../verification.md)

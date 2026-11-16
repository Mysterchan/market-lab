# Desktop candidate build and release gates

`tools/build_desktop.py` builds the App and its installer for the operating system it
runs on: a macOS `.app` and `.dmg`, a Windows folder App and Inno Setup `.exe`, or a
Linux folder App and `.AppImage`. Installers and `SHA256SUMS` go to `build/installers/`.
The [release workflow](../../.github/workflows/release.yml) builds all three, plus the
wheels and the source distribution, as workflow artifacts. Nothing is published.

| System | Installer | Signing (opt-in environment) |
| --- | --- | --- |
| macOS | `Market-Lab-<version>-macos-<arch>.dmg` | `MARKET_LAB_CODESIGN_IDENTITY`, then `MARKET_LAB_NOTARY_PROFILE` notarises and staples the image |
| Windows | `Market-Lab-<version>-windows-x86_64.exe` (per-user, no administrator rights) | `MARKET_LAB_SIGNTOOL` holds the `signtool sign` arguments; `MARKET_LAB_ISCC` locates Inno Setup |
| Linux | `Market-Lab-<version>-linux-x86_64.AppImage` | none; `MARKET_LAB_APPIMAGETOOL` locates `appimagetool` |

Pass `--app-only` to stop after the frozen App. The Windows and Linux installers are
built by the release workflow; they have not yet been accepted on clean machines.

## Wheels with the native library

`pip wheel .` compiles the native market library with the platform's C++17 compiler
(`tools/native_build.py`) into `sim_platform/market/_native/` and tags the wheel
`py3-none-<platform>`; the library does not use the Python C API, so one wheel serves
Python 3.9–3.12. Without a compiler the wheel stays pure Python; set
`MARKET_LAB_REQUIRE_NATIVE=1` to make that an error. Release wheels are built by
cibuildwheel (settings in `pyproject.toml`) and each is tested by running
`market-lab demo` from the installed wheel.

## Build

Use native Apple Silicon macOS with Python 3.9–3.12 and the Xcode command-line
build tools. Create a dedicated build environment:

```sh
python3 -m venv build/desktop-package-env
build/desktop-package-env/bin/python -m pip install -r requirements-packaging.txt
QT_QPA_PLATFORM=cocoa build/desktop-package-env/bin/python tools/build_desktop.py
```

The build compiles the C++ engine, renders the existing m/ icon, exports the SDK
wheel, records dependency notices, and uses PyInstaller to produce
`build/desktop-dist/Market Lab.app`. The App includes its interpreter, Qt/WebEngine,
native market adapter, Python manager, CA certificates and installable Agent SDK.
No remote script/CDN supplies the frontend.

The default build uses an ad-hoc signature for local testing. The Info.plist uses
Market Lab for the App name, the product version, an application identifier and
macOS 12 minimum; Qt 6.8.3 and the current binary target determine compatibility.
`MARKET_LAB_TARGET_ARCH` (`arm64`, `x86_64` or `universal2`) selects the macOS target.

## Verify the actual bundle

Run from a directory outside the repository to detect accidental source dependencies:

```sh
'/absolute/path/Market Lab.app/Contents/MacOS/Market Lab' \
  --smoke-test-report /absolute/path/app-check.json
codesign --verify --deep --strict '/absolute/path/Market Lab.app'
```

The opt-in smoke check creates temporary fixture data and accounts. It verifies
bundled rendering, native menu dispatch, a market-preparation subprocess, order
submission, a fill, stopping/saving the run, reading its archived result, SDK wheel
integrity and the bundled Python manager. It writes a JSON report and screenshot
and exits. It does not read normal account storage or call a real model.

Run the enabled Desktop suite on a native macOS display, plus the isolated
WebEngine bootstrap check. Linux source tests use the existing Xvfb/xcb CI target.
Also run platform tests, source-export checks, Black/Ruff, frontend syntax checks
and the frozen reference manifest check. Passing assertions alone is insufficient;
check process exit, workers, error/cancel paths and the rendered pages.

Manual acceptance covers first-use import, large-file cancel, manual/rule/Agent
workspaces, multiple tabs, result/replay/export, native menus, keyboard editing,
small-window layout, zoom, renderer recovery, Quit cancellation and reopening saved
results. Paid model accounts, a fresh machine and quarantined downloads require
separate acceptance evidence; local provider stubs cannot establish those claims.

## Before public binary distribution

- Complete the Qt/Chromium binary attribution inventory. The candidate includes
  installed-package notices and Qt/PySide LGPL/GPL license texts; the generated
  QtWebEngine attribution file explicitly records that this review remains open.
  Preserve corresponding source and modification/relinking instructions. The
  [Qt WebEngine licensing page](https://doc.qt.io/qt-6.8/qtwebengine-licensing.html)
  and [Qt third-party index](https://doc.qt.io/qt-6.8/licenses-used-in-qt.html) are
  upstream references, not evidence of a completed distribution audit.
- Build with a maintainer-provided Developer ID identity using
  `MARKET_LAB_CODESIGN_IDENTITY`. Revalidate signatures, the embedded WebEngine
  helper and hardened-runtime behavior. No identity or secrets are stored in Git.
- Archive the signed App, submit it with the maintainer's Apple notarytool profile,
  inspect the result, staple the ticket, and test the quarantined artifact on a
  clean supported Mac. A local ad-hoc signature is not notarization.
- Review the actual source revision, known limitations, installer checksums and
  the App/SDK version separately. Only then approve publication and support claims.

These are release gates, not claims that this candidate is ready for public release.
All Desktop pages use the same HTML frontend. The native page implementation and
`--classic` option have been removed. Regression checks cover migrated source
options, existing accounts, conditional handoffs, policy restoration, record
inspection, market preview, Replay controls and comparison export. External signing,
attribution and clean-machine gates above remain separate from UI migration.

[Release process](releases.md) · [Desktop user guide](../user-guide/desktop.md)

## Maintainer signing and notarization commands

Run these only after the source, attribution and clean-machine review, with a real
Developer ID and a previously configured local notarytool keychain profile:

```sh
MARKET_LAB_CODESIGN_IDENTITY='Developer ID Application: your identity' \
  build/desktop-package-env/bin/python tools/build_desktop.py
codesign --verify --deep --strict 'build/desktop-dist/Market Lab.app'
ditto -c -k --sequesterRsrc --keepParent \
  'build/desktop-dist/Market Lab.app' build/market-lab-notarization.zip
xcrun notarytool submit build/market-lab-notarization.zip \
  --keychain-profile market-lab-notary --wait
xcrun stapler staple 'build/desktop-dist/Market Lab.app'
spctl --assess --type execute --verbose 'build/desktop-dist/Market Lab.app'
```

Inspect the notarization response before stapling. Recreate the distributable ZIP
after stapling, record its checksum, and test that exact download on a clean Mac.
These commands are documented for the maintainer; the local candidate build does
not run them or upload anything.

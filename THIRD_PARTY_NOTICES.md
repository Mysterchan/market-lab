# Third-party components

The Apache-2.0 project license does not replace third-party license terms.

- **Lucide icons**: bundled inline SVGs in the desktop workbench/reference retain the
  [Lucide license text](sim_platform/desktop/web/LUCIDE-LICENSE.txt). The separate
  reference copy is in `docs/development/desktop-design/LUCIDE-LICENSE.txt`.
- **PySide6 / Qt / Qt WebEngine**: installed separately for the optional Desktop.
  Their distribution contains applicable license notices and Chromium third-party
  notices. Any later binary installer must carry the required distribution notices;
  this source release does not bundle the Qt runtime.
- **SQLAlchemy and Alembic**: installed separately from `requirements.txt`; retain
  their upstream package license metadata when redistributing those packages.
- **Python multiprocessing compatibility code**: retains the Python PSF and
  historical upstream notices in `sim_platform/sdk/licenses/Python-PSF.txt`; these
  are also included in exported SDK wheels alongside the project notices.
- **Model SDKs and user dependencies**: optional dependencies installed in selected
  agent environments keep their upstream licenses. They are not relicensed here.

Historical market recordings and user API credentials are not part of the release.
Nasdaq/ITCH names describe compatible input formats, not affiliation or endorsement.

## Local Desktop binary candidate

The macOS build additionally bundles uv (MIT/Apache-2.0), certifi (MPL-2.0), Python,
Qt/PySide and the installed core dependencies. Installed distribution license files,
metadata and exact versions are collected under `Contents/Resources/licenses`;
Qt/PySide license texts are also bundled under `sim_platform/desktop/licenses`.
PyInstaller's bootloader licensing exception is included with its package notices.
The generated QtWebEngine attribution note records the remaining binary attribution
review. This local candidate is not a completed public binary distribution audit.

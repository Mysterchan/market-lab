"""Collect installed dependency notices; record remaining binary attribution review."""

from importlib.metadata import distribution
from pathlib import Path
import json


def collect_notices(destination):
    destination.mkdir(parents=True, exist_ok=True)
    versions = {}
    for name in (
        'SQLAlchemy',
        'alembic',
        'Mako',
        'MarkupSafe',
        'PySide6',
        'PySide6-Essentials',
        'PySide6-Addons',
        'shiboken6',
        'typing-extensions',
        'tomli',
        'PyInstaller',
        'uv',
        'certifi',
    ):
        package = distribution(name)
        versions[name] = package.version
        folder = destination / name
        folder.mkdir(exist_ok=True)
        (folder / 'METADATA.txt').write_text(package.read_text('METADATA') or '')
        for entry in package.files or []:
            if entry.name.lower().startswith(('license', 'copying', 'notice')):
                source = Path(package.locate_file(entry))
                if source.is_file():
                    target = folder / entry.name
                    target.write_bytes(source.read_bytes())
    (destination / 'versions.json').write_text(json.dumps(versions, indent=2) + '\n')
    (destination / 'QtWebEngine-attribution.txt').write_text(
        'Qt WebEngine 6.8.3 contains Chromium and other third-party software.\n'
        'Upstream source and attribution inventory: https://code.qt.io/cgit/qt/qtwebengine.git/tree/?h=v6.8.3\n'
        'License index: https://doc.qt.io/qt-6.8/licenses-used-in-qt.html\n'
        'This local candidate has not completed the binary-distribution attribution review.\n'
    )

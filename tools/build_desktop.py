"""Build the Desktop App and its installer for this operating system.

    python tools/build_desktop.py            # App plus installer
    python tools/build_desktop.py --app-only # the frozen App only

macOS produces ``Market Lab.app`` and a ``.dmg``; Windows a folder App and an Inno
Setup installer (``.exe``); Linux a folder App and an ``.AppImage``. Installers and a
``SHA256SUMS`` file are written to ``build/installers/``. Nothing is uploaded.

Signing is opt-in through the environment, so no identity lives in Git:

- macOS: ``MARKET_LAB_CODESIGN_IDENTITY`` (a Developer ID Application identity) signs
  the App and the disk image; ``MARKET_LAB_NOTARY_PROFILE`` (a ``notarytool`` keychain
  profile) then notarises and staples the disk image.
- Windows: ``MARKET_LAB_SIGNTOOL`` holds the ``signtool sign`` arguments (for example
  ``/fd sha256 /tr http://timestamp.digicert.com /td sha256 /a``) used for the App's
  executable and the installer. ``MARKET_LAB_ISCC`` locates Inno Setup's ``ISCC.exe``.
- Linux: ``MARKET_LAB_APPIMAGETOOL`` locates ``appimagetool`` (default: on PATH).
"""

import argparse
import hashlib
import json
import os
from pathlib import Path
import platform
import shlex
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
ASSETS = ROOT / 'build/desktop-assets'
DIST = ROOT / 'build/desktop-dist'
INSTALLERS = ROOT / 'build/installers'
NAME = 'Market Lab'


def _system() -> str:
    return {'darwin': 'macos', 'win32': 'windows'}.get(sys.platform, 'linux')


def _architecture() -> str:
    machine = platform.machine().lower()
    return {'amd64': 'x86_64', 'aarch64': 'arm64'}.get(machine, machine)


def render_icons() -> None:
    """Render the m/ icon at every size: .icns (macOS), .ico (Windows), .png (Linux)."""
    from PySide6.QtCore import QByteArray, Qt
    from PySide6.QtGui import QImage, QPainter
    from PySide6.QtSvg import QSvgRenderer

    iconset = ASSETS / 'market-lab.iconset'
    iconset.mkdir(parents=True, exist_ok=True)
    renderer = QSvgRenderer(QByteArray((ROOT / 'docs/site/assets/favicon.svg').read_bytes()))

    def image(size: int) -> 'QImage':
        picture = QImage(size, size, QImage.Format.Format_ARGB32)
        picture.fill(Qt.GlobalColor.transparent)
        painter = QPainter(picture)
        renderer.render(painter)
        painter.end()
        return picture

    for size in (16, 32, 128, 256, 512):
        for scale in (1, 2):
            suffix = '@2x' if scale == 2 else ''
            image(size * scale).save(str(iconset / f'icon_{size}x{size}{suffix}.png'))
    image(256).save(str(ASSETS / 'market-lab.png'))
    image(256).save(str(ASSETS / 'market-lab.ico'))
    if sys.platform == 'darwin':
        subprocess.run(
            ['iconutil', '-c', 'icns', str(iconset), '-o', str(ASSETS / 'market-lab.icns')],
            check=True,
        )


def write_assets() -> None:
    """Icons, dependency notices, the Agent SDK wheel and BUILD_INFO.json."""
    from sim_platform.desktop.app import create_application

    application = create_application()
    application.setQuitOnLastWindowClosed(False)
    render_icons()
    from desktop.notices import collect_notices

    collect_notices(ASSETS / 'licenses')
    from sim_platform.persistence.agents.sdk import SDKFiles
    from sim_platform.sdk.distribution import FILENAME

    SDKFiles(ASSETS).export(ASSETS / FILENAME)
    from build_source_release import source_files
    from sim_platform import __version__

    manifest = {
        name.as_posix(): hashlib.sha256((ROOT / name).read_bytes()).hexdigest()
        for name in source_files(ROOT)
    }
    commit = subprocess.run(
        ['git', 'rev-parse', 'HEAD'], cwd=ROOT, capture_output=True, text=True
    ).stdout.strip()
    info = dict(
        product_version=__version__,
        target=f'{_system()}-{_architecture()}',
        source_kind='working-tree-candidate',
        base_commit=commit or None,
        source_sha256=manifest,
    )
    (ASSETS / 'BUILD_INFO.json').write_text(json.dumps(info, indent=2) + '\n')


def build_native() -> None:
    """The native market library in build/, compiled without Make on any system."""
    from native_build import LIBRARY, compile_library

    compile_library(ROOT, ROOT / 'build' / LIBRARY)


def freeze() -> Path:
    """Run PyInstaller with tools/desktop/market_lab.spec; return the App path."""
    environment = dict(
        os.environ,
        PYTHONPATH=os.pathsep.join(filter(None, (str(ROOT), os.environ.get('PYTHONPATH')))),
        PYINSTALLER_CONFIG_DIR=str(ROOT / 'build/pyinstaller-cache'),
    )
    subprocess.run(
        [
            sys.executable,
            '-m',
            'PyInstaller',
            '--noconfirm',
            '--clean',
            '--workpath',
            str(ROOT / 'build/desktop-pyinstaller'),
            '--distpath',
            str(DIST),
            str(ROOT / 'tools/desktop/market_lab.spec'),
        ],
        cwd=ROOT,
        env=environment,
        check=True,
    )
    return DIST / (f'{NAME}.app' if sys.platform == 'darwin' else NAME)


def _installer_name(suffix: str) -> Path:
    from sim_platform import __version__

    INSTALLERS.mkdir(parents=True, exist_ok=True)
    return INSTALLERS / f'Market-Lab-{__version__}-{_system()}-{_architecture()}{suffix}'


def package_macos(app: Path) -> Path:
    """A compressed .dmg with the App and an Applications link; signed and notarised
    when the maintainer's identity and notary profile are set."""
    image = _installer_name('.dmg')
    staging = ROOT / 'build/dmg-staging'
    shutil.rmtree(staging, ignore_errors=True)
    staging.mkdir(parents=True)
    subprocess.run(['ditto', str(app), str(staging / app.name)], check=True)
    (staging / 'Applications').symlink_to('/Applications')
    image.unlink(missing_ok=True)
    subprocess.run(
        [
            'hdiutil',
            'create',
            '-volname',
            NAME,
            '-srcfolder',
            str(staging),
            '-format',
            'UDZO',
            '-ov',
            str(image),
        ],
        check=True,
    )
    identity = os.environ.get('MARKET_LAB_CODESIGN_IDENTITY')
    if identity:
        subprocess.run(['codesign', '--sign', identity, '--timestamp', str(image)], check=True)
    profile = os.environ.get('MARKET_LAB_NOTARY_PROFILE')
    if profile:
        if not identity:
            raise SystemExit('Notarisation needs MARKET_LAB_CODESIGN_IDENTITY as well')
        subprocess.run(
            [
                'xcrun',
                'notarytool',
                'submit',
                str(image),
                '--keychain-profile',
                profile,
                '--wait',
            ],
            check=True,
        )
        subprocess.run(['xcrun', 'stapler', 'staple', str(image)], check=True)
    return image


def _sign_windows(path: Path) -> None:
    arguments = os.environ.get('MARKET_LAB_SIGNTOOL')
    if arguments:
        subprocess.run(['signtool', 'sign', *shlex.split(arguments), str(path)], check=True)


def package_windows(app: Path) -> Path:
    """An Inno Setup installer (per-user, no administrator rights needed)."""
    from sim_platform import __version__

    _sign_windows(app / f'{NAME}.exe')
    compiler = os.environ.get('MARKET_LAB_ISCC') or shutil.which('iscc') or shutil.which('ISCC')
    if not compiler:
        raise SystemExit('Install Inno Setup 6 or set MARKET_LAB_ISCC to ISCC.exe')
    target = _installer_name('.exe')
    subprocess.run(
        [
            compiler,
            f'/DAppVersion={__version__}',
            f'/DSourceDir={app}',
            f'/DIconFile={ASSETS / "market-lab.ico"}',
            f'/DOutputDir={target.parent}',
            f'/DOutputName={target.stem}',
            str(ROOT / 'tools/desktop/market_lab.iss'),
        ],
        check=True,
    )
    _sign_windows(target)
    return target


def package_linux(app: Path) -> Path:
    """An AppImage built from an AppDir around the frozen App."""
    appdir = ROOT / 'build/Market_Lab.AppDir'
    shutil.rmtree(appdir, ignore_errors=True)
    shutil.copytree(app, appdir / 'usr/lib/market-lab')
    (appdir / 'AppRun').write_text(
        '#!/bin/sh\nhere="$(dirname "$(readlink -f "$0")")"\n'
        'exec "$here/usr/lib/market-lab/Market Lab" "$@"\n'
    )
    (appdir / 'AppRun').chmod(0o755)
    shutil.copyfile(ROOT / 'tools/desktop/market-lab.desktop', appdir / 'market-lab.desktop')
    shutil.copyfile(ASSETS / 'market-lab.png', appdir / 'market-lab.png')
    tool = os.environ.get('MARKET_LAB_APPIMAGETOOL') or shutil.which('appimagetool')
    if not tool:
        raise SystemExit('Install appimagetool or set MARKET_LAB_APPIMAGETOOL')
    target = _installer_name('.AppImage')
    subprocess.run(
        [tool, str(appdir), str(target)],
        check=True,
        env=dict(os.environ, ARCH=_architecture()),
    )
    return target


def write_checksums() -> Path:
    """SHA256SUMS for every installer in build/installers/."""
    lines = [
        f'{hashlib.sha256(path.read_bytes()).hexdigest()}  {path.name}'
        for path in sorted(INSTALLERS.iterdir())
        if path.is_file() and path.name != 'SHA256SUMS'
    ]
    target = INSTALLERS / 'SHA256SUMS'
    target.write_text('\n'.join(lines) + '\n')
    return target


def build(app_only: bool = False) -> None:
    write_assets()
    build_native()
    app = freeze()
    print(app)
    if app_only:
        return
    package = dict(macos=package_macos, windows=package_windows, linux=package_linux)
    print(package[_system()](app))
    print(write_checksums())


if __name__ == '__main__':
    sys.path.insert(0, str(ROOT))
    sys.path.insert(0, str(ROOT / 'tools'))
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('--app-only', action='store_true', help='skip the installer')
    build(parser.parse_args().app_only)

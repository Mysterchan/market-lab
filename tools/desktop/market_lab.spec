# Desktop bundle for macOS (.app), Windows and Linux (folder Apps); see build_desktop.py.
# Signing is an explicit build environment choice.
from pathlib import Path
import os
import shutil
import sys
from sim_platform import __version__
from sim_platform.sdk.distribution import FILENAME
from PyInstaller.utils.hooks import collect_data_files, collect_submodules

root = Path(SPECPATH).parents[1]
macos = sys.platform == 'darwin'
windows = sys.platform == 'win32'
identity = os.environ.get('MARKET_LAB_CODESIGN_IDENTITY') or None
suffix = '.dylib' if macos else '.dll' if windows else '.so'
native = root / 'build' / ('libitch_market' + suffix)
uv_name = 'uv.exe' if windows else 'uv'
uv_binary = Path(sys.executable).with_name(uv_name)
if not uv_binary.is_file():
    uv_binary = Path(sys.executable).parent / 'Scripts' / uv_name
if not uv_binary.is_file():
    uv_binary = Path(shutil.which('uv') or '')
if not uv_binary.is_file():
    raise RuntimeError('Install requirements-packaging.txt before building')
analysis = Analysis(
    [str(root / 'tools/desktop/entry.py')],
    pathex=[str(root)],
    binaries=[(str(native), 'build'), (str(uv_binary), 'helpers')],
    # Replay identifies the execution rules by their source text (replay_manifest), so the
    # rule modules ship as source next to the compiled bytecode.
    datas=collect_data_files('sim_platform')
    + [
        (str(root / 'sim_platform/execution' / name), 'sim_platform/execution')
        for name in ('engine.py', 'liquidity.py', 'risk.py', 'models.py')
    ]
    + [
        (str(path), 'sim_platform/domain/ledger')
        for path in sorted((root / 'sim_platform/domain/ledger').glob('*.py'))
    ]
    + [(str(root / 'build/desktop-assets' / FILENAME), 'sim_platform/sdk')]
    + [
        (str(root / 'build/desktop-assets/BUILD_INFO.json'), '.'),
        (str(root / 'build/desktop-assets/licenses'), 'licenses/dependencies'),
        (str(root / 'LICENSE'), 'licenses'),
        (str(root / 'THIRD_PARTY_NOTICES.md'), 'licenses'),
    ],
    hiddenimports=collect_submodules('sim_platform'),
    excludes=[
        'PySide6.QtQml',
        'PySide6.QtQuick',
        'PySide6.QtWebEngineQuick',
        'tkinter',
        'pytest',
        'unittest',
    ],
    noarchive=False,
)
archive = PYZ(analysis.pure)
executable = EXE(
    archive,
    analysis.scripts,
    [],
    exclude_binaries=True,
    name='Market Lab',
    console=False,
    target_arch=os.environ.get('MARKET_LAB_TARGET_ARCH') if macos else None,
    codesign_identity=identity if macos else None,
    entitlements_file=str(root / 'tools/desktop/entitlements.plist') if macos else None,
    icon=str(root / 'build/desktop-assets/market-lab.ico') if windows else None,
)
collection = COLLECT(executable, analysis.binaries, analysis.datas, name='Market Lab')
if macos:
    app = BUNDLE(
        collection,
        name='Market Lab.app',
        icon=str(root / 'build/desktop-assets/market-lab.icns'),
        bundle_identifier='io.github.mysterchan.marketlab',
        info_plist={
            'CFBundleName': 'Market Lab',
            'CFBundleDisplayName': 'Market Lab',
            'CFBundleShortVersionString': __version__,
            'CFBundleVersion': __version__,
            'NSHighResolutionCapable': True,
            'LSMinimumSystemVersion': '12.0',
            'NSHumanReadableCopyright': 'See bundled LICENSE and third-party notices.',
        },
    )

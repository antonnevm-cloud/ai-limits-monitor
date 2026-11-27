import os
import runpy
import sys
from pathlib import Path

root = Path(SPECPATH).parent
metadata = runpy.run_path(str(root / 'claude_usage_manager' / '__init__.py'))
name = metadata['APP_NAME']
version = metadata['APP_VERSION']
icon = str(root / 'assets' / 'icon.ico')
assets = [(icon, 'assets'), (os.environ['AI_LIMITS_BUILD_LICENSES'], 'licenses')]
for filename in ('claude.svg', 'claude.png', 'codex.svg', 'codex.png',
                 'grok.png', 'antigravity.svg', 'antigravity.png'):
    assets.append((str(root / 'assets' / 'providers' / filename), 'assets/providers'))

analysis = Analysis(
    [str(root / 'monitor.py')], pathex=[str(root)], datas=assets,
    hiddenimports=['PySide6.support.deprecated'],
    excludes=['claude_usage_manager.cursor_provider', 'claude_usage_manager.windows_credential',
              'pytest', 'tkinter'],
)
# Виджеты рисуются QPainter. Программный OpenGL из необязательной поставки Qt им не нужен.
if sys.platform == 'win32':
    analysis.binaries = [entry for entry in analysis.binaries if Path(entry[0]).name.lower() != 'opengl32sw.dll']
archive = PYZ(analysis.pure)
exe = EXE(
    archive, analysis.scripts, [], name=name, exclude_binaries=True, icon=icon,
    console=sys.platform == 'win32', hide_console='hide-early' if sys.platform == 'win32' else None,
    upx=False,
)
payload = COLLECT(exe, analysis.binaries, analysis.datas, name=name, upx=False)
if sys.platform == 'darwin':
    app = BUNDLE(
        payload, name=f'{name}.app', icon=icon, version=version,
        bundle_identifier='org.ailimits.monitor',
        info_plist={'CFBundleShortVersionString': version, 'CFBundleVersion': version,
                    'NSHighResolutionCapable': True},
    )

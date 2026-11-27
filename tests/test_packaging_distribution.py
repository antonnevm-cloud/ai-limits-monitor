from pathlib import Path
import runpy
import sys
from types import SimpleNamespace

import pytest

from claude_usage_manager import APP_NAME, APP_VERSION


PACKAGING = Path(__file__).resolve().parents[1] / 'packaging'


@pytest.mark.parametrize('system', ['win32', 'darwin'])
def test_release_bundles_only_the_application_and_public_assets(system, tmp_path, monkeypatch):
    calls = {}

    def pyinstaller_step(step):
        def capture(*args, **kwargs):
            calls[step] = (args, kwargs)
            return SimpleNamespace(pure=[], scripts=[], binaries=[], datas=[])
        return capture

    monkeypatch.setattr(sys, 'platform', system)
    monkeypatch.setenv('AI_LIMITS_BUILD_LICENSES', str(tmp_path))
    namespace = {name: pyinstaller_step(name) for name in ['Analysis', 'PYZ', 'EXE', 'COLLECT', 'BUNDLE']}
    runpy.run_path(str(PACKAGING / 'monitor.spec'), init_globals={**namespace, 'SPECPATH': PACKAGING})
    args, options = calls['Analysis']
    assert args == ([str(PACKAGING.parent / 'monitor.py')],)
    assert {'claude_usage_manager.cursor_provider',
            'claude_usage_manager.windows_credential'} <= set(options['excludes'])
    resources = [(Path(src), dest) for src, dest in options['datas']]
    assert all(p.exists() for p, _ in resources)
    assert {dest for _, dest in resources} == {'assets', 'assets/providers', 'licenses'}
    assert {p.stem for p, dest in resources if dest == 'assets/providers'} == {
        'claude', 'codex', 'grok', 'antigravity',
    }
    assert calls['EXE'][1]['name'] == APP_NAME
    assert calls['EXE'][1]['console'] == (system == 'win32')
    if system == 'darwin':
        assert calls['BUNDLE'][1]['name'] == f'{APP_NAME}.app'
        assert calls['BUNDLE'][1]['info_plist']['CFBundleShortVersionString'] == APP_VERSION
    else:
        assert 'BUNDLE' not in calls


def test_portable_installer_skips_registration_and_all_shortcuts():
    source = (PACKAGING / 'windows.iss').read_text(encoding='utf-8')
    assert 'PrivilegesRequired=lowest' in source
    assert 'Uninstallable=not PortableInstall' in source
    icons = source.split('[Icons]', 1)[1].split('[Code]', 1)[0].strip().splitlines()
    assert len(icons) == 3
    assert all('Filename: "{app}\\{#AppName}.exe"' in line and 'Check: not PortableInstall' in line
               for line in icons)

from pathlib import Path
import re
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
    # Консольный EXE открывает окно терминала при запуске по ярлыку и закрывается вместе с ним.
    assert calls['EXE'][1]['console'] is False
    assert not calls['EXE'][1].get('hide_console')
    if system == 'darwin':
        assert calls['BUNDLE'][1]['name'] == f'{APP_NAME}.app'
        assert calls['BUNDLE'][1]['info_plist']['CFBundleShortVersionString'] == APP_VERSION
    else:
        assert 'BUNDLE' not in calls


def test_portable_installer_skips_registration_and_all_shortcuts():
    source = (PACKAGING / 'windows.iss').read_text(encoding='utf-8')
    assert 'PrivilegesRequired=lowest' in source
    assert 'Uninstallable=not PortableInstall' in source
    parts = re.split(r'^\[(\w+)\]\s*$', source, flags=re.M)
    sections = {name: [line for line in body.strip().splitlines() if line and not line.startswith(';')]
                for name, body in zip(parts[1::2], parts[2::2])}
    icons = sections['Icons']
    assert len(icons) == 2
    assert all('Filename: "{app}\\{#AppExe}"' in line and 'Check: not PortableInstall' in line
               for line in icons)
    # Автозапуск - параметр Run профиля, его же переключает программа; ярлыка в автозагрузке больше нет.
    assert '{userstartup}' not in '\n'.join(icons)
    registry = sections['Registry']
    assert len(registry) == 1 and 'Tasks: autostart' in registry[0] and '--autostart' in registry[0]
    assert 'Check: not PortableInstall' in registry[0]
    # Старые файлы программы удаляются до копирования новой версии.
    assert sections['InstallDelete'] == ['Type: filesandordirs; Name: "{app}\\_internal"',
                                         'Type: files; Name: "{app}\\portable.txt"; Check: not PortableInstall']
    assert any('--uninstall' in line for line in sections['UninstallRun'])
    # Переносимая копия требует своей папки и получает признак хранения данных рядом с программой.
    assert "ExpandConstant('{param:dir|}') = ''" in source
    assert "{app}\\portable.txt" in source


def test_portable_marker_moves_data_next_to_program(tmp_path, monkeypatch):
    from claude_usage_manager import utils

    monkeypatch.delenv('AI_LIMITS_DATA_DIR', raising=False)
    program = tmp_path / 'Копия программы'
    program.mkdir()
    monkeypatch.setattr(utils.sys, 'frozen', True, raising=False)
    monkeypatch.setattr(utils.sys, 'executable', str(program / 'AI Limits Monitor.exe'))
    assert utils.portable_data_dir() is None
    (program / utils.PORTABLE_MARKER).write_text('portable', encoding='utf-8')
    assert utils.data_dir() == program.resolve() / 'data'

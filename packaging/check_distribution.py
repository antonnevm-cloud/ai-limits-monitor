"""Проверка готового установщика или архива на чистом профиле."""
from __future__ import annotations

import argparse
import json
import marshal
import os
import runpy
import subprocess
import sys
import tempfile
from pathlib import Path, PurePosixPath, PureWindowsPath
from types import CodeType


ROOT = Path(__file__).resolve().parents[1]
APP = runpy.run_path(str(ROOT / 'claude_usage_manager' / '__init__.py'))


def check_contents(target: Path, executable: Path, resources: Path) -> None:
    from PyInstaller.archive.readers import CArchiveReader

    icons = {f.stem for f in (resources / 'assets' / 'providers').iterdir()}
    if icons != {'claude', 'codex', 'grok', 'antigravity'}:
        raise ValueError(f'Неверный состав значков в дистрибутиве: {icons}')
    for filename in ('LGPL-3.0-only.txt', 'GPL-3.0-only.txt', 'Qt-third-party.txt', 'Python.txt', 'README.md'):
        if not (resources / 'licenses' / filename).is_file():
            raise FileNotFoundError(filename)
    for member in target.rglob('*'):
        if member.suffix.lower() in ('.db', '.sqlite', '.sqlite3', '.jsonl', '.log') or (
            member.name.lower() in {'auth.json', 'credentials.json', 'settings.json', '.env', '.git'}
        ):
            raise ValueError(f'В дистрибутив попали локальные данные: {member.name}')
    archive = CArchiveReader(str(executable))
    modules = archive.open_embedded_archive('PYZ.pyz')
    removed = {'claude_usage_manager.cursor_provider', 'claude_usage_manager.windows_credential'}
    if removed.intersection(modules.toc):
        raise ValueError('В дистрибутив попал старый источник учетных данных')
    code = [marshal.loads(archive.extract('monitor'))]
    for name in modules.toc:
        if name == 'claude_usage_manager' or name.startswith('claude_usage_manager.'):
            code.append(modules.extract(name))
    while code:
        unit = code.pop()
        filename = unit.co_filename
        if PureWindowsPath(filename).anchor or PurePosixPath(filename).anchor:
            raise ValueError('Код приложения содержит абсолютный путь из среды сборки')
        code.extend(item for item in unit.co_consts if isinstance(item, CodeType))

    native_platform = 'qwindows.dll' if sys.platform == 'win32' else 'libqcocoa.dylib'
    if not any(target.rglob(native_platform)):
        raise FileNotFoundError(native_platform)


def check(package: Path, stage: Path) -> None:
    profile = stage / 'profile'
    profile.mkdir()
    env = os.environ.copy()
    for key in ('PYTHONHOME', 'PYTHONPATH', 'VIRTUAL_ENV', 'CONDA_PREFIX',
                'CLAUDE_CONFIG_DIR', 'CODEX_HOME', 'GROK_HOME', 'ANTIGRAVITY_DATA_DIR'):
        env.pop(key, None)
    env.update(HOME=str(profile), USERPROFILE=str(profile), TMP=str(stage), TEMP=str(stage),
               TMPDIR=str(stage), LOCALAPPDATA=str(profile / 'Local'), APPDATA=str(profile / 'Roaming'),
               AI_LIMITS_DATA_DIR=str(profile / 'Monitor'), QT_QPA_PLATFORM='offscreen')
    if sys.platform == 'win32':
        env['PATH'] = str(Path(os.environ['SystemRoot']) / 'System32')
        target = stage / 'Install'
        subprocess.run([str(package), '/PORTABLE=1', '/NOICONS', '/VERYSILENT', '/SUPPRESSMSGBOXES',
                        '/NORESTART', '/TASKS=', f'/DIR={target}'], cwd=stage, env=env,
                       timeout=180, check=True)
        executable = target / f'{APP["APP_NAME"]}.exe'
        resources = target / '_internal'
    else:
        env['PATH'] = '/usr/bin:/bin:/usr/sbin:/sbin'
        target = stage / 'Install'
        subprocess.run(['ditto', '-x', '-k', str(package), str(target)], check=True)
        app = target / f'{APP["APP_NAME"]}.app'
        subprocess.run(['codesign', '--verify', '--deep', '--strict', str(app)], check=True)
        executable = app / 'Contents' / 'MacOS' / APP['APP_NAME']
        resources = app / 'Contents' / 'Resources'

    check_contents(target, executable, resources)
    for option, expected, stdin in (
        ('--version', f'{APP["APP_NAME"]} {APP["APP_VERSION"]}', None),
        ('--smoke-test', 'smoke OK', None),
        ('--claude-statusline', 'Claude | 5h: 23%',
         json.dumps({'rate_limits': {'five_hour': {'used_percentage': 23}}})),
    ):
        result = subprocess.run([str(executable), option], input=stdin, stdout=subprocess.PIPE,
                                text=True, encoding='utf-8', errors='replace', cwd=stage, env=env,
                                timeout=40, check=True)
        if expected not in result.stdout:
            raise ValueError(f'{option}: отсутствует ожидаемый ответ {expected!r}')
        print(option, result.stdout.strip())


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('package', type=Path)
    parser.add_argument('--work-dir', type=Path, default=ROOT / '.pytest_tmp' / 'packaging')
    args = parser.parse_args()
    args.work_dir.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='check-', dir=args.work_dir.resolve()) as tmp:
        check(args.package.resolve(strict=True), Path(tmp))


if __name__ == '__main__':
    main()

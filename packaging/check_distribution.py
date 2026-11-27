"""Проверка готового установщика или архива на чистом профиле."""
from __future__ import annotations

import argparse
import json
import marshal
import os
import re
import runpy
import subprocess
import sys
import tempfile
import time
from pathlib import Path, PurePosixPath, PureWindowsPath
from types import CodeType


ROOT = Path(__file__).resolve().parents[1]
APP = runpy.run_path(str(ROOT / 'claude_usage_manager' / '__init__.py'))
# Путь в профиле конкретного пользователя любой машины (подстановки вида <имя> и %USERNAME% допустимы).
PRIVATE_PATH = re.compile(r'(?i)(?:\b[a-z]:[\\/]users[\\/]|/users/|/home/)(?!<|%|\{)([\w.-]+)')
# Имена из тестов и документации заведомо вымышлены.
FICTIONAL_USERS = {'public', 'default', 'test', 'demo', 'user', 'example', 'name', 'me'}


def private_path(text: str) -> str | None:
    for found in PRIVATE_PATH.finditer(text):
        name = found.group(1).casefold()
        if len(name) > 1 and name not in FICTIONAL_USERS:
            return found.group(0)
    return None
FOREIGN_BINARY = re.compile(r'(?i)^(?:ucrtbase\.dll|api-ms-win-.*\.dll|lib(?:ssl|crypto)-3-x64\.dll|qtvirtualkeyboard.*)$')
PROFILE_VARIABLES = (
    'PYTHONHOME', 'PYTHONPATH', 'VIRTUAL_ENV', 'CONDA_PREFIX', 'QT_QPA_PLATFORM',
    'AI_LIMITS_DATA_DIR', 'CLAUDE_CONFIG_DIR', 'CODEX_HOME', 'GROK_HOME',
    'ANTIGRAVITY_DATA_DIR', 'NPM_CONFIG_PREFIX',
)


def check_contents(target: Path, executable: Path, resources: Path) -> None:
    from PyInstaller.archive.readers import CArchiveReader

    brand = Path('assets/r1.svg')
    if (resources / brand).read_bytes() != (ROOT / brand).read_bytes():
        raise ValueError('Знак R1 в поставке отличается от ресурса в исходниках')
    icons = {f.stem for f in (resources / 'assets' / 'providers').iterdir()}
    if icons != {'claude', 'codex', 'grok', 'antigravity'}:
        raise ValueError(f'Неверный состав значков в дистрибутиве: {icons}')
    for filename in ('LGPL-3.0-only.txt', 'GPL-3.0-only.txt', 'Qt-third-party.txt', 'Python.txt',
                     'Python-incorporated-software.txt', 'libaom.txt', 'README.md'):
        if not (resources / 'licenses' / filename).is_file():
            raise FileNotFoundError(filename)
    # Посторонние DLL из PATH машины сборки (UCRT из JDK, OpenSSL из Git) и модули Qt только под GPL.
    foreign = sorted(member.name for member in target.rglob('*.dll') if FOREIGN_BINARY.match(member.name))
    if foreign:
        raise ValueError(f'В дистрибутив попали посторонние библиотеки: {", ".join(foreign[:8])}')
    for filename in ('LICENSE', 'NOTICE'):
        packaged = resources / 'licenses' / f'AI-Limits-Monitor-{filename}.txt'
        if packaged.read_bytes() != (ROOT / filename).read_bytes():
            raise ValueError(f'Файл {filename} в поставке отличается от файла в исходниках')
    for member in target.rglob('*'):
        if member.suffix.lower() in ('.db', '.sqlite', '.sqlite3', '.jsonl', '.log') or (
            member.name.lower() in {'auth.json', 'credentials.json', 'settings.json', '.env', '.git'}
        ):
            raise ValueError(f'В дистрибутив попали локальные данные: {member.name}')
    archive = CArchiveReader(str(executable))
    modules = archive.open_embedded_archive('PYZ.pyz')
    code = [marshal.loads(archive.extract('monitor'))]
    for name in modules.toc:
        if name == 'claude_usage_manager' or name.startswith('claude_usage_manager.'):
            code.append(modules.extract(name))
    build_paths = [str(path.resolve()).replace('\\', '/').casefold() + '/'
                   for path in (ROOT, Path.home())]
    while code:
        unit = code.pop()
        filename = unit.co_filename
        if PureWindowsPath(filename).anchor or PurePosixPath(filename).anchor:
            raise ValueError('Код приложения содержит абсолютный путь из среды сборки')
        constants = list(unit.co_consts)
        while constants:
            item = constants.pop()
            if isinstance(item, CodeType):
                code.append(item)
            elif isinstance(item, (tuple, frozenset)):
                constants.extend(item)
            elif isinstance(item, (str, bytes)):
                text = item.decode('utf-8', 'replace') if isinstance(item, bytes) else item
                normalized = text.replace('\\', '/').casefold()
                if (any(path in normalized or normalized == path.rstrip('/') for path in build_paths)
                        or private_path(text)):
                    raise ValueError(f'В модуле {filename} сохранен личный путь: {text[:80]!r}')

    native_platform = 'qwindows.dll' if sys.platform == 'win32' else 'libqcocoa.dylib'
    if not any(target.rglob(native_platform)):
        raise FileNotFoundError(native_platform)


def windows_subsystem(executable: Path) -> int:
    """Подсистема из заголовка PE: 2 - программа без консоли, 3 - консольная."""
    with executable.open('rb') as stream:
        stream.seek(0x3C)
        header = int.from_bytes(stream.read(4), 'little')
        stream.seek(header + 4 + 20 + 68)
        return int.from_bytes(stream.read(2), 'little')


def check(package: Path, stage: Path) -> None:
    profile = stage / 'profile'
    profile.mkdir()
    env = os.environ.copy()
    for key in PROFILE_VARIABLES:
        env.pop(key, None)
    env.update(HOME=str(profile), USERPROFILE=str(profile), TMP=str(stage), TEMP=str(stage),
               TMPDIR=str(stage), LOCALAPPDATA=str(profile / 'Local'), APPDATA=str(profile / 'Roaming'),
               AI_LIMITS_DATA_DIR=str(profile / 'Monitor'), QT_QPA_PLATFORM='offscreen')
    if sys.platform == 'win32':
        env['PATH'] = str(Path(os.environ['SystemRoot']) / 'System32')
        target = stage / 'Установка с пробелами'
        subprocess.run([str(package), '/PORTABLE=1', '/NOICONS', '/VERYSILENT', '/SUPPRESSMSGBOXES',
                        '/NORESTART', '/TASKS=', f'/DIR={target}'], cwd=stage, env=env,
                       timeout=180, check=True)
        executable = target / f'{APP["APP_NAME"]}.exe'
        resources = target / '_internal'
        if windows_subsystem(executable) != 2:
            # Консольный EXE открывает окно терминала при запуске по ярлыку и закрывается вместе с ним.
            raise ValueError('Программа Windows собрана с консолью')
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
    if sys.platform == 'win32':
        # Переносимая копия без AI_LIMITS_DATA_DIR хранит данные в папке data рядом с программой, а не в профиле.
        if not (target / 'portable.txt').is_file():
            raise FileNotFoundError('Переносимая установка без признака portable.txt')
        portable_env = {key: value for key, value in env.items() if key != 'AI_LIMITS_DATA_DIR'}
        # Обработчик строки состояния записывает замер в каталог данных (--smoke-test подменяет каталог сам).
        subprocess.run([str(executable), '--claude-statusline'], stdout=subprocess.PIPE, cwd=stage,
                       env=portable_env, timeout=40, check=True,
                       input=json.dumps({'rate_limits': {'five_hour': {'used_percentage': 23}}}), text=True)
        stored = list((target / 'data').rglob('*.json'))
        if not stored or any((profile / 'Local').rglob('*.json')):
            raise ValueError('Переносимая копия хранит данные не рядом с программой')
        print('portable data', target / 'data')


def _visible_window_titles(process_id: int) -> set[str]:
    import ctypes
    from ctypes import wintypes

    titles = set()
    user = ctypes.windll.user32

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def visit(handle, _):
        hwnd = wintypes.HWND(handle)
        owner = wintypes.DWORD()
        user.GetWindowThreadProcessId(hwnd, ctypes.byref(owner))
        if owner.value == process_id and user.IsWindowVisible(hwnd):
            title = ctypes.create_unicode_buffer(user.GetWindowTextLengthW(hwnd) + 1)
            user.GetWindowTextW(hwnd, title, len(title))
            titles.add(title.value)
        return True

    if not user.EnumWindows(visit, 0):
        raise ctypes.WinError()
    return titles


def check_standard_windows_install(package: Path, stage: Path) -> None:
    """Проверка обычной установки в пустом профиле одноразовой машины Windows."""
    if sys.platform != 'win32':
        raise ValueError('Для проверки обычной установки нужна Windows')
    import winreg

    root = Path(os.environ['LOCALAPPDATA'])
    target = root / 'Programs' / APP['APP_NAME']
    data = root / 'ClaudeUsageManager'
    uninstall_key = r'Software\Microsoft\Windows\CurrentVersion\Uninstall\{C809A50B-4A52-467E-BF77-426185D1FEA9}_is1'
    if target.exists() or data.exists():
        raise ValueError('Для проверки нужен профиль без установки и истории AI Limits Monitor')
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, uninstall_key):
            raise ValueError('В профиле уже зарегистрирована установка AI Limits Monitor')
    except FileNotFoundError:
        pass
    env = {key: value for key, value in os.environ.items() if key not in PROFILE_VARIABLES}
    env['PATH'] = str(Path(os.environ['SystemRoot']) / 'System32')
    subprocess.run([str(package), '/CURRENTUSER', '/VERYSILENT', '/SUPPRESSMSGBOXES',
                    '/NORESTART', '/TASKS='], cwd=stage, env=env, check=True, timeout=180)
    executable = target / f'{APP["APP_NAME"]}.exe'
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, uninstall_key) as key:
            if winreg.QueryValueEx(key, 'DisplayVersion')[0] != APP['APP_VERSION']:
                raise ValueError('В реестре указана другая версия')
        expected = {f'{APP["APP_NAME"]} {APP["APP_VERSION"]}', 'Подключения'}
        with subprocess.Popen([str(executable)], cwd=stage, env=env) as process:
            try:
                deadline = time.monotonic() + 60
                while process.poll() is None:
                    if expected <= _visible_window_titles(process.pid):
                        break
                    if time.monotonic() >= deadline:
                        raise TimeoutError('Виджет и окно первого подключения не появились')
                    time.sleep(0.2)
                else:
                    raise RuntimeError(f'Виджет завершился при запуске с кодом {process.returncode}')
                if not (data / 'usage.db').is_file():
                    raise FileNotFoundError('База истории в новом профиле не создана')
                print('Обычная установка: версия в реестре подтверждена, виджет и окно подключений видны, база истории создана.')
            finally:
                if process.poll() is None:
                    process.terminate()
    finally:
        uninstaller = target / 'unins000.exe'
        if uninstaller.is_file():
            subprocess.run([str(uninstaller), '/VERYSILENT', '/SUPPRESSMSGBOXES', '/NORESTART'],
                           cwd=stage, env=env, check=True, timeout=120)


TEXT_SUFFIXES = {'.py', '.md', '.txt', '.spec', '.iss', '.ps1', '.yml', '.json', '.ini', '.svg', ''}
LOCAL_FILES = re.compile(r'(?i)(?:\.(?:db|sqlite3?|log|jsonl|pem|key|pfx)|(?:^|/)(?:\.env|auth\.json|credentials\.json))$')


def check_source_archive(archive: Path, forbidden: list[str]) -> None:
    """Архив исходников: без локальных данных, личных путей и запрещенных слов (имена клиентов и проектов)."""
    import zipfile

    words = [word.casefold() for word in forbidden if word]
    with zipfile.ZipFile(archive) as bundle:
        for name in bundle.namelist():
            if LOCAL_FILES.search(name):
                raise ValueError(f'В архив исходников попал локальный файл: {name}')
            if PurePosixPath(name).suffix.lower() not in TEXT_SUFFIXES or name.endswith('/'):
                continue
            text = bundle.read(name).decode('utf-8', 'replace')
            found = private_path(text)
            if found:
                raise ValueError(f'{name}: личный путь {found!r}')
            lowered = text.casefold()
            for word in words:
                if word in lowered:
                    raise ValueError(f'{name}: запрещенное слово {word!r}')
    print('Архив исходников проверен:', archive.name)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('package', type=Path)
    parser.add_argument('--work-dir', type=Path, default=ROOT / '.pytest_tmp' / 'packaging')
    parser.add_argument('--standard-windows-install', action='store_true',
                        help='Дополнительно проверить обычную установку на чистой машине Windows')
    parser.add_argument('--source-archive', type=Path, help='Проверить также архив исходников (zip)')
    parser.add_argument('--forbid', action='append', default=[],
                        help='Слово, которого не должно быть в архиве исходников (можно повторять)')
    args = parser.parse_args()
    args.work_dir.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='check-', dir=args.work_dir.resolve()) as tmp:
        package = args.package.resolve(strict=True)
        check(package, Path(tmp))
        if args.standard_windows_install:
            check_standard_windows_install(package, Path(tmp))
    if args.source_archive:
        check_source_archive(args.source_archive.resolve(strict=True), args.forbid)


if __name__ == '__main__':
    main()

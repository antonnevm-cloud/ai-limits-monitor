"""Локальные настройки подключения четырех официальных клиентов."""
from __future__ import annotations

import hashlib
import json
import os
import plistlib
import sys
import tempfile
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Iterator, Mapping
from xml.parsers.expat import ExpatError

from . import utils


PROVIDER_NAMES = {
    "claude": "Claude Code", "codex": "Codex",
    "grok": "Grok Build", "antigravity": "Antigravity",
}
INSTALL_URLS = {
    "claude": "https://code.claude.com/docs/en/setup",
    "codex": "https://developers.openai.com/codex/cli/",
    "grok": "https://docs.x.ai/build/overview",
    "antigravity": "https://antigravity.google/download",
}
PROFILE_VARIABLES = {"claude": "CLAUDE_CONFIG_DIR", "codex": "CODEX_HOME", "grok": "GROK_HOME"}
_PROFILES = {"claude": ".claude", "codex": ".codex", "grok": ".grok", "antigravity": ".gemini/antigravity"}
_BINARY_NAMES = {"claude": "claude", "codex": "codex", "grok": "grok", "antigravity": "agy"}
_PATH_FIELDS = ("executable", "profile", "application", "cli_profile")
_MAX_SETTINGS = 65536


@dataclass(frozen=True)
class Connection:
    enabled: bool = True
    executable: str = ""
    profile: str = ""
    application: str = ""  # Приложение Antigravity для входа
    cli_profile: str = ""  # Каталог настроек agy


def _absolute(value: str) -> Path | None:
    if not value or any(char in value for char in "\0\r\n"):
        return None
    try:
        path = Path(value).expanduser()
        return path if path.is_absolute() else None
    except (OSError, RuntimeError, ValueError):
        return None


def _from_dict(provider: str, row: object) -> Connection:
    if not isinstance(row, dict) or not isinstance(row.get("enabled", True), bool):
        raise ValueError("Некорректные настройки подключения.")
    fields = _PATH_FIELDS if provider == "antigravity" else _PATH_FIELDS[:2]
    paths = {}
    for field in fields:
        value = row.get(field, "")
        if not isinstance(value, str) or (value and _absolute(value) is None):
            raise ValueError(f"Укажи полный путь для {PROVIDER_NAMES[provider]}.")
        paths[field] = value
    return Connection(enabled=row.get("enabled", True), **paths)


def load_connections(*, strict: bool = False) -> dict[str, Connection]:
    try:
        with (utils.data_dir() / "connections.json").open("r", encoding="utf-8-sig") as stream:
            content = stream.read(_MAX_SETTINGS + 1)
        if len(content) > _MAX_SETTINGS:
            raise ValueError("Файл подключений слишком велик.")
        raw = json.loads(content)
        if not isinstance(raw, dict):
            raise ValueError("Некорректный файл подключений.")
        return {key: _from_dict(key, raw.get(key, {})) for key in PROVIDER_NAMES}
    except FileNotFoundError:
        return {key: Connection() for key in PROVIDER_NAMES}
    except (OSError, ValueError, RecursionError) as error:
        if strict:
            raise ValueError("Не удалось прочитать connections.json. Проверь настройки подключения.") from None
        global _DAMAGED_REPORTED
        if not _DAMAGED_REPORTED:
            _DAMAGED_REPORTED = True
            utils.get_logger().warning(
                "Не удалось прочитать %s (%s): все источники выключены до исправления в окне \"Подключения\".",
                utils.data_dir() / "connections.json", error)
        # Поврежденный файл не должен самовольно запускать отключенные клиенты.
        return {key: Connection(enabled=False) for key in PROVIDER_NAMES}


_DAMAGED_REPORTED = False


def validate_connection(provider: str, connection: Connection) -> Connection:
    connection = _from_dict(provider, asdict(connection))
    if connection.executable and not _valid_binary_name(provider, Path(connection.executable)):
        raise ValueError(f"Для {PROVIDER_NAMES[provider]} выбери программу {binary_name(provider)}.")
    if connection.application and Path(connection.application).name.lower() != _app_name().lower():
        raise ValueError(f"Выбери приложение {_app_name()}.")
    return connection


def save_connections(connections: Mapping[str, Connection]) -> None:
    """Записать весь выбор одного пользователя. Читателям доступна только полная версия."""
    rows = {}
    for provider in PROVIDER_NAMES:
        connection = validate_connection(provider, connections[provider])
        rows[provider] = {key: value for key, value in asdict(connection).items() if value != ""}
    content = json.dumps(rows, ensure_ascii=False, indent=2) + "\n"
    if len(content) > _MAX_SETTINGS:
        raise ValueError("Файл подключений слишком велик.")
    target = utils.data_dir() / "connections.json"
    temporary = None
    try:
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=target.parent, delete=False) as output:
            temporary = Path(output.name)
            output.write(content)
            output.flush()
            os.fsync(output.fileno())
        for attempt in range(3):
            try:
                os.replace(temporary, target)
                break
            except PermissionError as error:
                if attempt == 2 or getattr(error, "winerror", None) not in {5, 32, 33}:
                    raise
                # На Windows служба может в этот момент дочитывать предыдущую версию.
                time.sleep(0.02)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def _selection(provider: str, connection: Connection | None) -> Connection:
    return connection if connection is not None else load_connections()[provider]


def provider_enabled(provider: str) -> bool:
    return load_connections()[provider].enabled


def _normalized(path: Path | str) -> str:
    return os.path.normcase(os.path.normpath(os.path.expanduser(str(path))))


def provider_config_key(provider: str, connection: Connection | None = None) -> str:
    """Ключ истории квот: зависит только от каталога данных клиента, а не от пути к программе.

    Выбор той же программы или явное указание стандартного каталога историю не прячет.
    """
    connection = connection or load_connections()[provider]
    try:
        profile = _normalized(provider_profile(provider, connection))
    except ValueError:
        profile = _normalized(connection.profile)
    standard = Path.home() / _PROFILES[provider]
    paths = ["" if profile == _normalized(standard) else profile]
    if provider == "antigravity":
        cli = _normalized(antigravity_cli_profile(connection))
        paths.append("" if cli == _normalized(standard.parent / "antigravity-cli") else cli)
    if not any(paths):
        return ""
    return hashlib.sha256(json.dumps(paths, ensure_ascii=True).encode("ascii")).hexdigest()[:20]


def legacy_provider_config_key(provider: str, connection: Connection | None = None) -> str:
    """Ключ версий до 2.0.6: в него входили пути к программе и приложению."""
    connection = connection or load_connections()[provider]
    profile = connection.profile
    if not profile:
        effective = os.path.normpath(provider_profile(provider, connection))
        standard = os.path.normpath(Path.home() / _PROFILES[provider])
        if os.path.normcase(effective) != os.path.normcase(standard):
            profile = effective
    paths = []
    for value in (connection.executable, profile, connection.application, connection.cli_profile):
        normalized = os.path.normcase(os.path.normpath(os.path.expanduser(value))) if value else ""
        paths.append(normalized)
    if not any(paths):
        return ""
    return hashlib.sha256(json.dumps(paths, ensure_ascii=True).encode("ascii")).hexdigest()[:20]


def provider_profile(provider: str, connection: Connection | None = None) -> Path:
    selected = _selection(provider, connection)
    if selected.profile:
        chosen = _absolute(selected.profile)
        if chosen is None:
            raise ValueError(f"Укажи полный путь к каталогу данных {PROVIDER_NAMES[provider]}.")
        return chosen
    variable = PROFILE_VARIABLES.get(provider)
    override = os.environ.get(variable, "") if variable else ""
    return _absolute(override) or Path.home() / _PROFILES[provider]


def antigravity_cli_profile(connection: Connection | None = None) -> Path:
    selected = _selection("antigravity", connection)
    return _absolute(selected.cli_profile) or provider_profile("antigravity", selected).parent / "antigravity-cli"


def collection_profile_id() -> str:
    """Сборщик и виджет могут получить разные каталоги клиентов из своего окружения."""
    connections = load_connections()
    paths = [utils.data_dir()]
    paths.extend(provider_profile(key, connection) for key, connection in connections.items())
    paths.append(antigravity_cli_profile(connections["antigravity"]))
    normalized = [os.path.normcase(os.path.normpath(path)) for path in paths]
    return hashlib.sha256(json.dumps(normalized, ensure_ascii=True).encode("ascii")).hexdigest()


def provider_environment(provider: str, connection: Connection | None = None) -> dict[str, str]:
    selected = _selection(provider, connection)
    env = os.environ.copy()
    variable = PROFILE_VARIABLES.get(provider)
    if variable and (selected.profile or variable in env):
        env[variable] = str(provider_profile(provider, selected))
    if sys.platform == "darwin":
        # У приложения из Finder путь поиска программ часто короче, чем в Терминале.
        paths = env.get("PATH", os.defpath).split(os.pathsep)
        for path in ("/opt/homebrew/bin", "/usr/local/bin", str(Path.home() / ".local/bin")):
            if path not in paths:
                paths.append(path)
        env["PATH"] = os.pathsep.join(paths)
    return env


def binary_name(provider: str) -> str:
    return _BINARY_NAMES[provider] + (".exe" if sys.platform == "win32" else "")


def _valid_binary_name(provider: str, path: Path) -> bool:
    name = path.name.lower() if sys.platform == "win32" else path.name
    if provider == "codex":
        if sys.platform == "win32" and name in {"codex-x86_64-pc-windows-msvc.exe",
                                                 "codex-aarch64-pc-windows-msvc.exe"}:
            return True
        if sys.platform == "darwin" and name in {"codex-x86_64-apple-darwin", "codex-aarch64-apple-darwin"}:
            return True
    return name == binary_name(provider)


def _can_run(path: Path) -> bool:
    return path.is_absolute() and path.is_file() and (sys.platform == "win32" or os.access(path, os.X_OK))


def _windows_npm_binaries(provider: str, name: str) -> Iterator[Path]:
    # npm кладет файлы запуска в свой prefix, а программы под ним в node_modules.
    # Файл .cmd только указывает расположение пакета. Запускается его штатный .exe.
    prefixes = []
    preset = _absolute(os.environ.get("NPM_CONFIG_PREFIX", ""))
    if preset:
        prefixes.append(preset)
    for entry in os.get_exec_path():
        directory = _absolute(entry)
        if directory is not None and (directory / f"{provider}.cmd").is_file():
            prefixes.append(directory)
    configured = _npmrc_prefix()
    if configured is not None:
        prefixes.append(configured)
    roaming = _absolute(os.environ.get("APPDATA", "")) or Path.home() / "AppData/Roaming"
    prefixes.append(roaming / "npm")
    module_roots = [prefix / "node_modules" for prefix in dict.fromkeys(prefixes)]
    # pnpm хранит глобальные пакеты в PNPM_HOME\global\<версия>\node_modules.
    local = _absolute(os.environ.get("LOCALAPPDATA", "")) or Path.home() / "AppData/Local"
    pnpm = _absolute(os.environ.get("PNPM_HOME", "")) or local / "pnpm"
    try:
        module_roots.extend(sorted((pnpm / "global").glob("*/node_modules"), reverse=True))
    except OSError:
        pass
    for modules in dict.fromkeys(module_roots):
        if provider == "claude":
            yield modules / "@anthropic-ai/claude-code/bin" / name
        else:
            package = modules / "@openai/codex"
            for arch, triple in (("x64", "x86_64-pc-windows-msvc"), ("arm64", "aarch64-pc-windows-msvc")):
                native = package / f"node_modules/@openai/codex-win32-{arch}"
                for root in (native, package, package.parent / f"codex-win32-{arch}"):
                    for layout in ("bin", f"vendor/{triple}/bin", f"vendor/{triple}/codex"):
                        yield root / layout / name


def _npmrc_prefix() -> Path | None:
    """prefix из пользовательского .npmrc: npm ставит туда глобальные пакеты вместо %APPDATA%\\npm."""
    try:
        with (Path.home() / ".npmrc").open(encoding="utf-8", errors="replace") as stream:
            for line in stream:
                key, _, value = line.partition("=")
                if key.strip().casefold() == "prefix":
                    return _absolute(os.path.expandvars(value.strip().strip('"')))
    except OSError:
        pass
    return None


def _binary_candidates(provider: str) -> Iterator[Path]:
    home = Path.home()
    name = binary_name(provider)
    installer_variable = {"codex": "CODEX_INSTALL_DIR", "grok": "GROK_BIN_DIR"}.get(provider)
    if installer_variable:
        directory = _absolute(os.environ.get(installer_variable, ""))
        if directory is not None:
            yield directory / name
    if sys.platform == "win32":
        local = _absolute(os.environ.get("LOCALAPPDATA", "")) or home / "AppData/Local"
        if provider == "codex":
            yield local / "Programs/OpenAI/Codex/bin" / name
            yield home / ".codex/packages/app-server-daemon/current/bin" / name
        if provider == "antigravity":
            yield local / "agy/bin" / name
    if provider == "grok":
        yield home / ".grok/bin" / name
    yield home / ".local/bin" / name
    for directory in os.get_exec_path():
        path = _absolute(directory)
        if path is not None:
            yield path / name
    if sys.platform == "darwin":
        for directory in (Path("/opt/homebrew/bin"), Path("/usr/local/bin")):
            yield directory / name
        if provider == "codex":
            for root in (home / "Applications", Path("/Applications")):
                yield root / "Codex.app/Contents/Resources" / name
    if sys.platform == "win32" and provider in {"claude", "codex"}:
        yield from _windows_npm_binaries(provider, name)


def provider_executable(provider: str, connection: Connection | None = None) -> Path | None:
    selected = _selection(provider, connection)
    if selected.executable:
        path = _absolute(selected.executable)
        return path if path and _valid_binary_name(provider, path) and _can_run(path) else None
    return next((p for p in _binary_candidates(provider) if _can_run(p)), None)


def _app_name() -> str:
    return "Antigravity.exe" if sys.platform == "win32" else "Antigravity.app"


def _is_antigravity_app(path: Path) -> bool:
    if not path.is_absolute() or path.name.lower() != _app_name().lower():
        return False
    if sys.platform == "win32":
        return _can_run(path)
    if sys.platform != "darwin":
        return False
    try:
        info = path / "Contents/Info.plist"
        if info.stat().st_size > 1_000_000:
            return False
        with info.open("rb") as stream:
            executable = plistlib.load(stream)["CFBundleExecutable"]
        return isinstance(executable, str) and Path(executable).name == executable and _can_run(
            path / "Contents/MacOS" / executable
        )
    except (OSError, ValueError, KeyError, TypeError, ExpatError):
        return False


def antigravity_app(connection: Connection | None = None) -> Path | None:
    selected = _selection("antigravity", connection)
    if selected.application:
        path = _absolute(selected.application)
        return path if path and _is_antigravity_app(path) else None
    if sys.platform == "darwin":
        roots = [Path.home() / "Applications", Path("/Applications")]
    elif sys.platform == "win32":
        local = _absolute(os.environ.get("LOCALAPPDATA", "")) or Path.home() / "AppData/Local"
        roots = [local / "Programs/Antigravity"]
        programs = _absolute(os.environ.get("ProgramFiles", ""))
        if programs is not None:
            roots.append(programs / "Antigravity")
    else:
        return None
    return next((root / _app_name() for root in roots if _is_antigravity_app(root / _app_name())), None)

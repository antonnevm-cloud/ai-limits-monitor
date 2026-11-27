"""Квоты Antigravity из штатной команды /usage и входа statusLine."""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
import re
import shlex
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any, Sequence

from . import provider_settings
from .models import QuotaSnapshot, QuotaWindow
from .process_utils import run_hidden
from .statusline_command import statusline_command
from .utils import data_dir, parse_iso


CLI_SOURCE = "antigravity:cli-usage"
STATUSLINE_SOURCE = "antigravity:statusline"
FRESH_FOR = dt.timedelta(minutes=10)
MAX_AGE = dt.timedelta(hours=24)
_MAX_INPUT = 1_000_000
_MAX_SAMPLE = 65_536
_BUCKET = re.compile(r"([a-z0-9]+(?:-[a-z0-9]+)*)-(weekly|5h)")
_PROFILE_KEY = re.compile(r"[a-f0-9]{64}")
_GROUPS = {"gemini": ("gemini", "Gemini Models"), "3p": ("claude-gpt", "Claude and GPT models")}
_EPOCH = dt.datetime(1970, 1, 1, tzinfo=dt.timezone.utc)


def _now() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


def _profile_key(profile: Path, cli_profile: Path) -> str:
    paths = [os.path.normcase(str(path.resolve())) for path in (profile, cli_profile)]
    return hashlib.sha256(json.dumps(paths).encode("ascii")).hexdigest()


def _sample_path(key: str, directory: Path | None = None) -> Path:
    if not _PROFILE_KEY.fullmatch(key):
        raise ValueError("Некорректный ключ профиля Antigravity.")
    return (directory or data_dir()) / "antigravity-statusline" / (key + ".json")


def _read_limited(path: Path, limit: int) -> bytes:
    with path.open("rb") as stream:
        content = stream.read(limit + 1)
    if len(content) > limit:
        raise ValueError("Слишком большой файл Antigravity.")
    return content


def _write_json(path: Path, value: dict, *, original: bytes | None = None, settings: bool = False) -> None:
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    descriptor, name = tempfile.mkstemp(dir=path.parent)
    temporary = Path(name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump(value, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
        if settings:
            try:
                current = _read_limited(path, 2_000_000)
            except FileNotFoundError:
                current = None
            if current != original:
                raise ValueError("Настройки agy изменились во время подключения. Повтори подключение.")
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _window(bucket_id: str, bucket: Any, observed: dt.datetime, now: dt.datetime) -> QuotaWindow | None:
    matched = _BUCKET.fullmatch(bucket_id) if len(bucket_id) <= 64 else None
    if not matched or not isinstance(bucket, dict) or bucket.get("disabled", False) is not False:
        return None
    fraction = bucket.get("remaining_fraction")
    if isinstance(fraction, bool) or not isinstance(fraction, (float, int)) or not 0 <= fraction <= 1:
        return None
    reset = None
    if bucket.get("reset_time"):
        reset = parse_iso(bucket["reset_time"])
        if reset is None or reset.tzinfo is None:
            return None
        # В штатном ответе agy эпоха Unix встречается вместо фактического срока.
        if reset == _EPOCH:
            reset = None
    elif bucket.get("reset_in_seconds") is not None:
        seconds = bucket["reset_in_seconds"]
        if type(seconds) is not int or seconds < 0:
            return None
        try:
            reset = observed + dt.timedelta(seconds=seconds)
        except OverflowError:
            return None
    if reset is not None and reset <= now:
        return None
    group, period = matched.groups()
    group_key, title = _GROUPS.get(group, (group, group))
    return QuotaWindow(
        f"ag-group:{group_key}:{period}", title, (1 - fraction) * 100, reset,
        10_080 if period == "weekly" else 300, "group",
    )


def _snapshot(source: str, buckets: dict, observed: dt.datetime, now: dt.datetime) -> QuotaSnapshot:
    result = QuotaSnapshot(provider="antigravity", source=source, login_method="Antigravity", updated_at=observed)
    for name, data in buckets.items():
        if isinstance(name, str):
            window = _window(name, data, observed, now)
            if window is not None:
                result.windows.append(window)
    if result.windows:
        weekly = [window for window in result.windows if window.window_minutes == 10_080]
        main = max(weekly or result.windows, key=lambda window: window.used_percent)
        result.main_window_id = main.id
        result.main_label = main.title
        if weekly:
            result.weekly = main.used_percent
            result.weekly_resets_at = main.resets_at
        short = result.window(main.id.rsplit(":", 1)[0] + ":5h")
        if short:
            result.five_hour = short.used_percent
            result.five_hour_resets_at = short.resets_at
            result.secondary_label = "Пятичасовая квота"
    return result


def _native_usage(executable: Path, now: dt.datetime | None = None) -> QuotaSnapshot:
    result = QuotaSnapshot(provider="antigravity", source=CLI_SOURCE)
    options = dict(stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, cwd=data_dir())
    try:
        version = run_hidden([str(executable), "--version"], timeout=5, **options)
        matched = re.fullmatch(rb"v?(\d{1,4})\.(\d{1,4})\.(\d{1,4})", version.stdout.strip())
        # До 1.1.11 команда /usage в режиме --print могла уйти модели.
        if version.returncode or not matched or tuple(map(int, matched.groups())) < (1, 1, 11):
            result.error = "Для чтения /usage нужна стабильная версия agy 1.1.11 или новее."
            return result
        report = run_hidden([str(executable), "--print", "/usage", "--output-format", "json"], timeout=15, **options)
        if report.returncode:
            result.error = "agy не вернул квоту. Проверь вход в Antigravity."
            return result
        if len(report.stdout) > _MAX_INPUT:
            raise ValueError
        payload = json.loads(report.stdout)
        if not isinstance(payload, dict) or payload.get("status") != "SUCCESS" or payload.get("num_turns") != 0:
            raise ValueError
        command = payload.get("command")
        if not isinstance(command, dict) or command.get("name") != "usage":
            raise ValueError
        data = command.get("data")
        groups = data.get("groups") if isinstance(data, dict) else None
        if not isinstance(groups, list):
            raise ValueError
        buckets = {}
        for group in groups:
            if isinstance(group, dict) and isinstance(group.get("buckets"), list):
                for bucket in group["buckets"]:
                    if isinstance(bucket, dict) and isinstance(bucket.get("id"), str):
                        buckets[bucket["id"]] = bucket
        observed = now or _now()
        result = _snapshot(CLI_SOURCE, buckets, observed, observed)
        if not result.has_data():
            result.error = "В ответе /usage нет действующих квот с долей остатка."
    except (UnicodeError, ValueError, RecursionError):
        result.error = "agy вернул неизвестный формат /usage."
    except subprocess.TimeoutExpired:
        result.error = "agy не успел вернуть квоту."
    except OSError:
        result.error = "Не удалось запустить agy для чтения квоты."
    return result


def _ingest(payload: Any, key: str, now: dt.datetime, directory: Path | None = None) -> bool:
    if not isinstance(payload, dict) or payload.get("product") != "antigravity":
        return False
    quotas = payload.get("quota")
    if not isinstance(quotas, dict) or len(quotas) > 100:
        return False
    # Из входа statusLine храним только числовой остаток и срок по каждому окну.
    stored = {}
    for name, bucket in quotas.items():
        if isinstance(name, str):
            window = _window(name, bucket, now, now)
            if window is not None:
                stored[name] = {"remaining_fraction": bucket["remaining_fraction"]}
                if window.resets_at is not None:
                    stored[name]["reset_time"] = window.resets_at.isoformat()
    _write_json(_sample_path(key, directory), {"received_at": now.isoformat(), "quota": stored})
    return bool(stored)


def _statusline_sample(key: str, now: dt.datetime) -> QuotaSnapshot:
    result = QuotaSnapshot(provider="antigravity", source=STATUSLINE_SOURCE)
    try:
        payload = json.loads(_read_limited(_sample_path(key), _MAX_SAMPLE))
        if not isinstance(payload, dict) or not isinstance(payload.get("quota"), dict):
            raise ValueError
        stamp = parse_iso(payload.get("received_at"))
        if stamp is None or stamp.tzinfo is None or stamp > now:
            raise ValueError
        if now - stamp > MAX_AGE:
            result.error = "Данные строки состояния Antigravity старше суток."
            return result
        result = _snapshot(STATUSLINE_SOURCE, payload["quota"], stamp, now)
        if not result.has_data():
            result.error = "В строке состояния Antigravity нет действующих квот с долей остатка."
        elif now - stamp > FRESH_FOR:
            result.fallback_source = "Сохраненная строка состояния Antigravity"
    except FileNotFoundError:
        result.error = "Подключи строку состояния Antigravity и открой agy в выбранном профиле."
    except (OSError, ValueError, RecursionError):
        result.error = "Не удалось прочитать снимок строки состояния Antigravity."
    return result


def fetch_antigravity_quota() -> QuotaSnapshot:
    connection = provider_settings.load_connections()["antigravity"]
    if not connection.enabled:
        return QuotaSnapshot(provider="antigravity", source=CLI_SOURCE, error="Источник Antigravity отключен.")
    profile = provider_settings.provider_profile("antigravity", connection)
    cli_profile = provider_settings.antigravity_cli_profile(connection)
    key = _profile_key(profile, cli_profile)
    default = Path.home() / ".gemini"
    # agy --print выбирает учетную запись штатного профиля текущего пользователя.
    if key != _profile_key(default / "antigravity", default / "antigravity-cli"):
        return _statusline_sample(key, _now())
    executable = provider_settings.provider_executable("antigravity", connection)
    if executable is None:
        native = QuotaSnapshot(provider="antigravity", source=CLI_SOURCE, error="Программа agy не найдена.")
    else:
        native = _native_usage(executable)
        if native.has_data():
            return native
    sample = _statusline_sample(key, _now())
    if sample.has_data():
        return sample
    native.error = f"{native.error} {sample.error}"
    return native


def _bridge_command(key: str) -> str:
    arguments = [key]
    if os.environ.get("AI_LIMITS_DATA_DIR"):
        arguments.append(data_dir().as_posix())
    return statusline_command("--antigravity-statusline", *arguments)


def install_antigravity_statusline(*, profile: Path | None = None, cli_profile: Path | None = None) -> str:
    """Подключение по кнопке. Существующая пользовательская команда имеет приоритет."""
    if profile is not None and cli_profile is None:
        cli_profile = profile.parent / "antigravity-cli"
    profile = profile or provider_settings.provider_profile("antigravity")
    cli_profile = cli_profile or provider_settings.antigravity_cli_profile()
    if not cli_profile.is_dir():
        # Каталог настроек создает сам agy при первом запуске; выдуманный каталог он читать не будет.
        return "missing"
    settings = (cli_profile / "settings.json").resolve()
    try:
        original = _read_limited(settings, 2_000_000)
        raw = json.loads(original.decode("utf-8-sig"))
        if not isinstance(raw, dict):
            raise ValueError("Настройки agy должны содержать объект JSON.")
    except FileNotFoundError:
        original, raw = None, {}
    except (UnicodeError, ValueError, RecursionError) as exc:
        raise ValueError("Не удалось прочитать настройки agy. Проверь settings.json.") from exc
    command = _bridge_command(_profile_key(profile, cli_profile))
    current = raw.get("statusLine", {})
    if not isinstance(current, dict) or current.get("enabled", True) is not True or current.get("type", "command") != "command":
        return "conflict"
    # Квитанция с хешем позволяет обновить путь своей команды после переустановки.
    directory_hash = hashlib.sha256(os.fsencode(os.path.normcase(str(settings)))).hexdigest()
    receipt = data_dir() / "antigravity-statusline" / f"installed-{directory_hash}.json"
    command_hash = hashlib.sha256(command.encode("utf-8")).hexdigest()
    existing = current.get("command")
    installed = _read_receipt(receipt)
    previous = installed.get("previous") if "previous" in installed else (None if existing is not None else (current or None))
    if existing == command:
        _write_json(receipt, {"command_hash": command_hash, "settings": str(settings), "previous": previous})
        return "unchanged"
    if existing is not None:
        known = isinstance(existing, str) and hashlib.sha256(existing.encode("utf-8")).hexdigest() == installed.get("command_hash")
        if not (known or _owned_command(existing)):
            return "conflict"
    raw["statusLine"] = {**current, "type": "command", "command": command, "stack_with_default": True}
    if original is not None:
        backup = settings.with_name(settings.name + ".ai-limits-backup")
        backup.write_bytes(original)
    _write_json(settings, raw, original=original, settings=True)
    _write_json(receipt, {"command_hash": command_hash, "settings": str(settings), "previous": previous})
    return "installed"


def _read_receipt(path: Path) -> dict:
    try:
        value = json.loads(_read_limited(path, 65536))
    except (OSError, ValueError, RecursionError):
        return {}
    return value if isinstance(value, dict) else {}


def _owned_command(command: object) -> bool:
    """Своя команда без квитанции: формы cmd (call "...exe" --antigravity-statusline) и PowerShell."""
    if not isinstance(command, str):
        return False
    from .claude_local import _powershell_words

    words = _powershell_words(command)
    if words is None:
        try:
            words = shlex.split(command.replace("\\", "/"), posix=True)
        except ValueError:
            return False
    if "--antigravity-statusline" not in words:
        return False
    at = words.index("--antigravity-statusline")
    program = words[at - 1].replace("\\", "/").rsplit("/", 1)[-1].casefold() if at else ""
    return program in {"ai limits monitor.exe", "ai limits monitor", "monitor.py"}


def remove_antigravity_statusline(*, cli_profile: Path | None = None) -> str:
    """Вернуть строку состояния agy, которая была до подключения монитора."""
    cli_profile = cli_profile or provider_settings.antigravity_cli_profile()
    settings = (cli_profile / "settings.json").resolve()
    try:
        original = _read_limited(settings, 2_000_000)
        raw = json.loads(original.decode("utf-8-sig"))
    except FileNotFoundError:
        return "absent"
    except (UnicodeError, ValueError, RecursionError) as exc:
        raise ValueError("Не удалось прочитать настройки agy. Проверь settings.json.") from exc
    current = raw.get("statusLine") if isinstance(raw, dict) else None
    existing = current.get("command") if isinstance(current, dict) else None
    if not isinstance(existing, str):
        return "absent"
    directory_hash = hashlib.sha256(os.fsencode(os.path.normcase(str(settings)))).hexdigest()
    receipt = data_dir() / "antigravity-statusline" / f"installed-{directory_hash}.json"
    installed = _read_receipt(receipt)
    known = hashlib.sha256(existing.encode("utf-8")).hexdigest() == installed.get("command_hash")
    if not (known or _owned_command(existing)):
        return "foreign"
    previous = installed.get("previous")
    if isinstance(previous, dict):
        raw["statusLine"] = previous
    else:
        raw.pop("statusLine", None)
    _write_json(settings, raw, original=original, settings=True)
    receipt.unlink(missing_ok=True)
    return "removed"


def antigravity_statusline_main(args: Sequence[str]) -> int:
    """Принимает штатный вход agy и оставляет вывод встроенной строки состояния клиенту."""
    if len(args) not in (1, 2) or not _PROFILE_KEY.fullmatch(args[0]):
        return 2
    directory = Path(args[1]) if len(args) == 2 else None
    if directory is not None and not directory.is_absolute():
        return 2
    try:
        stream = getattr(sys.stdin, "buffer", sys.stdin)
        if stream is not None:
            raw = stream.read(_MAX_INPUT + 1)
            if directory is not None:
                os.environ["AI_LIMITS_DATA_DIR"] = str(directory)
            # Выключенный в подключениях источник замеры не сохраняет.
            if len(raw) <= _MAX_INPUT and provider_settings.provider_enabled("antigravity"):
                _ingest(json.loads(raw), args[0], _now(), directory)
    except (UnicodeError, OSError, ValueError, RecursionError):
        pass
    return 0

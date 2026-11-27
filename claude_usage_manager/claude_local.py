"""Local, documented Claude Code status-line quota input. Never contacts Anthropic."""
from __future__ import annotations

import datetime as dt
import json
import hashlib
import math
import os
import pathlib
import re
import shlex
import shutil
import sys
import uuid
from typing import Any

from .models import ManualResetCredits, ManualResetItem, QuotaSnapshot, QuotaWindow
from .utils import claude_root, data_dir, parse_iso

SOURCE = "claude-code:statusline"
MANUAL_SOURCE = "manual:claude-settings-usage"
MANUAL_KEY = "claude_manual_resets_v1"
DATA_FILE = "claude-statusline-v1.json"
FRESH_FOR = dt.timedelta(minutes=10)
MAX_AGE = dt.timedelta(hours=24)
MAX_PAYLOAD = 1_000_000
_WINDOWS = (
    ("five_hour", "primary", "Пятичасовое окно", 300, "primary"),
    ("seven_day", "secondary", "Недельная квота", 10080, "secondary"),
)


def _profile_key(profile: pathlib.Path) -> str:
    path = profile.expanduser().resolve()
    if path == (pathlib.Path.home() / ".claude").resolve():
        return ""
    return hashlib.sha256(os.path.normcase(str(path)).encode("utf-8")).hexdigest()


def _snapshot_path(key: str) -> pathlib.Path:
    return data_dir() / "profiles" / key / DATA_FILE if key else data_dir() / DATA_FILE


def statusline_path() -> pathlib.Path:
    return _snapshot_path(_profile_key(claude_root()))


def _percent(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    number = float(value)
    return number if math.isfinite(number) and 0 <= number <= 100 else None


def _expiry(value: Any) -> dt.datetime | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    try:
        timestamp = float(value)
        if not math.isfinite(timestamp):
            return None
        result = dt.datetime.fromtimestamp(timestamp, tz=dt.timezone.utc)
        if not 2020 <= result.year <= 2100:
            return None
        return result
    except (OverflowError, ValueError, OSError):
        return None


def _atomic_json(path: pathlib.Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + "." + uuid.uuid4().hex + ".tmp")
    try:
        with temporary.open("x", encoding="utf-8") as stream:
            json.dump(value, stream, ensure_ascii=False, sort_keys=True)
            stream.write("\n")
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def ingest_statusline(payload: Any, *, path: pathlib.Path | None = None,
                      now: dt.datetime | None = None) -> bool:
    """Persist only two documented quota windows; discard all session data."""
    if not isinstance(payload, dict):
        return False
    rates = payload.get("rate_limits")
    if not isinstance(rates, dict):
        return False
    windows: dict[str, dict[str, float | None]] = {}
    for key, *_ in _WINDOWS:
        source = rates.get(key)
        if not isinstance(source, dict):
            continue
        used = _percent(source.get("used_percentage"))
        if used is None:
            continue
        reset = _expiry(source.get("resets_at"))
        windows[key] = {
            "used_percentage": used,
            "resets_at": reset.timestamp() if reset is not None else None,
        }
    if not windows:
        return False
    received = (now or dt.datetime.now(dt.timezone.utc)).astimezone(dt.timezone.utc)
    _atomic_json(path or statusline_path(), {
        "schema_version": 1,
        "received_at": received.isoformat(),
        "windows": windows,
    })
    return True


def read_statusline_quota(*, path: pathlib.Path | None = None,
                          now: dt.datetime | None = None) -> QuotaSnapshot:
    """Read a bounded local snapshot; never perform a network request."""
    result = QuotaSnapshot(provider="claude", source=SOURCE,
                           login_method="Claude Code · statusLine")
    moment = (now or dt.datetime.now(dt.timezone.utc)).astimezone(dt.timezone.utc)
    try:
        target = path or statusline_path()
        if not target.is_file() or target.stat().st_size > 65536:
            return result
        raw = json.loads(target.read_text(encoding="utf-8"))
        if not isinstance(raw, dict) or raw.get("schema_version") != 1:
            return result
        stamp = parse_iso(raw.get("received_at"))
        if stamp is None:
            return result
        stamp = stamp.astimezone(dt.timezone.utc)
        age = moment - stamp
        if age < -dt.timedelta(minutes=5) or age > MAX_AGE:
            return result
        sources = raw.get("windows")
        if not isinstance(sources, dict):
            return result
        for name, window_id, title, minutes, role in _WINDOWS:
            source = sources.get(name)
            if not isinstance(source, dict):
                continue
            used = _percent(source.get("used_percentage"))
            if used is None:
                continue
            reset = _expiry(source.get("resets_at"))
            # An expired window is no longer a valid remaining quota.
            if reset is not None and reset <= moment:
                continue
            result.windows.append(QuotaWindow(
                id=window_id, title=title, used_percent=used,
                resets_at=reset, window_minutes=minutes, role=role,
            ))
            if name == "five_hour":
                result.five_hour, result.five_hour_resets_at = used, reset
            else:
                result.weekly, result.weekly_resets_at = used, reset
        if not result.windows:
            return QuotaSnapshot(provider="claude", source=SOURCE,
                                 login_method=result.login_method)
        result.updated_at = stamp
        result.main_window_id = "secondary" if result.weekly is not None else "primary"
        result.main_label = "Недельная квота" if result.weekly is not None else "Пятичасовое окно"
        if age > FRESH_FOR:
            result.fallback_source = "Локальный снимок Claude Code · устарел"
        return result
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        return result


def _manual_date(value: str, now: dt.datetime) -> dt.datetime:
    parsed = dt.datetime.strptime(value.strip(), "%Y-%m-%d %H:%M")
    local_zone = now.astimezone().tzinfo or dt.timezone.utc
    parsed = parsed.replace(tzinfo=local_zone).astimezone(dt.timezone.utc)
    if parsed <= now.astimezone(dt.timezone.utc):
        raise ValueError("Срок сброса уже истёк: " + value)
    return parsed


def parse_manual_resets(text: str, *, now: dt.datetime | None = None) -> list[dt.datetime | None]:
    """One offer per line, ISO local time or '-' when the offer has no expiry."""
    moment = now or dt.datetime.now(dt.timezone.utc)
    values: list[dt.datetime | None] = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        if len(values) >= 30:
            raise ValueError("Можно учесть не более 30 сбросов.")
        if line in ("-", "—", "без срока"):
            values.append(None)
        else:
            try:
                values.append(_manual_date(line, moment))
            except (ValueError, OverflowError) as error:
                raise ValueError("Неверный срок (нужен YYYY-MM-DD HH:MM): " + line) from error
    return values


def save_manual_resets(db: Any, text: str, *, now: dt.datetime | None = None) -> int:
    moment = (now or dt.datetime.now(dt.timezone.utc)).astimezone(dt.timezone.utc)
    expiries = parse_manual_resets(text, now=moment)
    db.set_setting(_manual_key(), json.dumps({
        "schema_version": 1,
        "confirmed_at": moment.isoformat(),
        "items": [stamp.isoformat() if stamp is not None else None for stamp in expiries],
    }, sort_keys=True))
    return len(expiries)


def _manual_key() -> str:
    key = _profile_key(claude_root())
    return f"{MANUAL_KEY}/{key}" if key else MANUAL_KEY


def read_manual_resets(db: Any, *, now: dt.datetime | None = None) -> ManualResetCredits:
    """Manual entries are not live/verified and expire only by their stated dates."""
    result = ManualResetCredits(provider="claude")
    value = db.get_setting(_manual_key(), "")
    if not value:
        return result
    moment = (now or dt.datetime.now(dt.timezone.utc)).astimezone(dt.timezone.utc)
    try:
        raw = json.loads(value)
        if not isinstance(raw, dict) or raw.get("schema_version") != 1:
            return result
        entries = raw.get("items")
        if not isinstance(entries, list) or len(entries) > 30:
            return result
        stamped = parse_iso(raw.get("confirmed_at"))
        items: list[ManualResetItem] = []
        for idx, value in enumerate(entries):
            if value is not None and not isinstance(value, str):
                return result
            expiry = parse_iso(value) if value is not None else None
            if value is not None and expiry is None:
                return result
            if expiry is not None and expiry.astimezone(dt.timezone.utc) <= moment:
                continue
            items.append(ManualResetItem(
                id="manual:" + str(idx + 1), provider="claude",
                expires_at=expiry, reset_type="manual",
                description="Внесено вручную по Settings > Usage",
            ))
        result.state = "available" if items else "none"
        result.available_count = len(items)
        result.items = items
        result.source = MANUAL_SOURCE
        result.updated_at = stamped
        result.freshness = "manual"
        result.details_complete = True
        return result
    except (ValueError, TypeError, json.JSONDecodeError, OverflowError):
        return result


def manual_resets_edit_text(db: Any) -> str:
    credits = read_manual_resets(db)
    return "\n".join(
        stamp.expires_at.astimezone().strftime("%Y-%m-%d %H:%M")
        if stamp.expires_at is not None else "-"
        for stamp in credits.items
    )


def bridge_command(*, profile: pathlib.Path | None = None) -> str:
    from .statusline_command import statusline_command
    key = _profile_key(profile or claude_root())
    args = [key] if key else []
    if os.environ.get("AI_LIMITS_DATA_DIR"):
        args = [key or "default", data_dir().as_posix()]
    return statusline_command("--claude-statusline", *args)


def _owned_command(command: str) -> bool:
    try:
        words = shlex.split(command, posix=True)
        at = words.index("--claude-statusline")
    except ValueError:
        return False
    if at not in (1, 2):
        return False
    target = pathlib.PurePosixPath(words[at - 1].replace(chr(92), "/").casefold())
    if at == 2 and pathlib.PurePosixPath(words[0].casefold()).name not in {"python", "python3", "python.exe"}:
        return False
    ours = target.name in {"ai limits monitor", "ai limits monitor.exe", "claudeusagemanager.exe"}
    ours = ours or (target.name == "monitor.py" and target.parent.name in {"claudeusagemanager", "ai-limits-monitor"})
    options = words[at + 1:]
    if options and (len(options) > 2 or not re.fullmatch(r"default|[a-f0-9]{64}", options[0])):
        return False
    return ours and (len(options) < 2 or pathlib.Path(options[1]).is_absolute())


def install_statusline_command(command: str, *,
                               settings_path: pathlib.Path | None = None) -> str:
    """Explicit opt-in. Never replace an unrelated existing status line."""
    settings = settings_path or claude_root() / "settings.json"
    identity = hashlib.sha256(os.path.normcase(str(settings.resolve())).encode("utf-8")).hexdigest()
    receipt = data_dir() / "claude-hooks" / (identity + ".json")
    def digest(value: str) -> str:
        return hashlib.sha256(value.encode("utf-8")).hexdigest()
    original = None
    if settings.exists():
        if settings.stat().st_size > 2_000_000:
            raise ValueError("Файл настроек Claude неожиданно велик.")
        original = settings.read_bytes()
        raw = json.loads(original.decode("utf-8-sig"))
        if not isinstance(raw, dict):
            raise ValueError("Настройки Claude должны содержать JSON-объект.")
    else:
        raw = {}
    current = raw.get("statusLine")
    if isinstance(current, dict):
        existing = current.get("command")
        if current.get("type") == "command" and existing == command:
            return "already"
        try:
            saved = json.loads(receipt.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            saved = {}
        known = isinstance(saved, dict) and isinstance(existing, str) and saved.get("command_hash") == digest(existing)
        if current.get("type") != "command" or not isinstance(existing, str) or not (known or _owned_command(existing)):
            return "conflict"
    elif current is not None:
        return "conflict"
    raw["statusLine"] = {"type": "command", "command": command}
    settings.parent.mkdir(parents=True, exist_ok=True)
    if settings.exists():
        if settings.read_bytes() != original:
            raise ValueError("Настройки Claude изменились. Открой подключение еще раз.")
        backup = settings.with_name(settings.name + ".ai-limits-backup")
        if not backup.exists():
            shutil.copy2(settings, backup)
    _atomic_json(settings, raw)
    _atomic_json(receipt, {"command_hash": digest(command)})
    return "installed"


def statusline_main(args: list[str] | None = None) -> int:
    """Claude Code invokes this command with JSON on stdin; no Qt, tokens, or HTTP."""
    try:
        raw = sys.stdin.buffer.read(MAX_PAYLOAD + 1)
        if len(raw) > MAX_PAYLOAD:
            print("Claude")
            return 0
        payload = json.loads(raw.decode("utf-8"))
        options = args or []
        if len(options) > 2 or (options and not re.fullmatch(r"default|[a-f0-9]{64}", options[0])):
            return 2
        if len(options) == 2:
            if not pathlib.Path(options[1]).is_absolute():
                return 2
            os.environ["AI_LIMITS_DATA_DIR"] = options[1]
        key = options[0] if options and options[0] != "default" else ""
        valid = ingest_statusline(payload, path=_snapshot_path(key))
        windows = payload.get("rate_limits", {}) if isinstance(payload, dict) else {}
        labels = []
        if valid and isinstance(windows, dict):
            for key, label in (("five_hour", "5h"), ("seven_day", "7d")):
                info = windows.get(key)
                used = _percent(info.get("used_percentage")) if isinstance(info, dict) else None
                if used is not None:
                    labels.append(f"{label}: {used:.0f}%")
        print("Claude | " + " ".join(labels) if labels else "Claude")
    except (OSError, UnicodeError, ValueError, TypeError, json.JSONDecodeError):
        # Status line must never break a Claude session.
        print("Claude")
    return 0

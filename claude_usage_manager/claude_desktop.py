"""Read Claude Desktop's local plan utilization, without HTTP or credentials.

The file is a snapshot maintained by the official Desktop application, not
a documented Anthropic API. Preserve its timestamp and never merge different
account observations or invent reset dates.
"""
from __future__ import annotations

import datetime as dt
import json
import math
import os
import pathlib
import sys

from .models import QuotaSnapshot, QuotaWindow

SOURCE = "claude-desktop:local-history"
HISTORY_FILE = "plan-usage-history.json"
MAX_BYTES = 4 * 1024 * 1024
MAX_AGE = dt.timedelta(hours=2)
FRESH_AGE = dt.timedelta(minutes=30)
_FIVE_HOURS = ("five_hour", "primary", "Пятичасовое окно", 300, "primary", "fh")
_WEEK = ("seven_day", "secondary", "Недельная квота", 10080, "secondary", "sd")


def desktop_history_paths() -> tuple[pathlib.Path, ...]:
    """Discover standard Claude Desktop profiles (never inspect credentials)."""
    home = pathlib.Path.home()
    if sys.platform == "win32":
        roaming = pathlib.Path(os.environ.get("APPDATA") or home / "AppData" / "Roaming")
        local = pathlib.Path(os.environ.get("LOCALAPPDATA") or home / "AppData" / "Local")
        paths = [roaming / "Claude" / HISTORY_FILE]
        packages = local / "Packages"
        if packages.is_dir():
            try:
                paths.extend(
                    folder / "LocalCache" / "Roaming" / "Claude" / HISTORY_FILE
                    for folder in sorted(packages.glob("Claude_*"))
                )
            except OSError:
                pass
        return tuple(paths)
    if sys.platform == "darwin":
        return (home / "Library" / "Application Support" / "Claude" / HISTORY_FILE,)
    xdg = pathlib.Path(os.environ.get("XDG_CONFIG_HOME") or home / ".config")
    return (xdg / "Claude" / HISTORY_FILE,)


def _percent(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    try:
        converted = float(value)
    except (ValueError, TypeError, OverflowError):
        return None
    return converted if math.isfinite(converted) and 0 <= converted <= 100 else None


def _read_history(path: pathlib.Path, now: dt.datetime) -> QuotaSnapshot | None:
    """Choose one most-recent account sample; never fill from older accounts."""
    try:
        if not path.is_file() or path.stat().st_size > MAX_BYTES:
            return None
        with path.open("rb") as stream:
            data = json.load(stream)
    except (OSError, ValueError, UnicodeError, RecursionError, TypeError):
        return None
    if not isinstance(data, dict) or type(data.get("version")) is not int:
        return None
    if data["version"] not in (1, 2) or not isinstance(data.get("samples"), list):
        return None

    latest: dict | None = None
    latest_ms = 0.0
    for sample in data["samples"]:
        if not isinstance(sample, dict):
            continue
        stamp = sample.get("t")
        if isinstance(stamp, bool) or not isinstance(stamp, (int, float)):
            continue
        if not math.isfinite(stamp) or stamp < 0:
            continue
        if stamp >= latest_ms:
            latest_ms, latest = float(stamp), sample
    if latest is None:
        return None
    org = latest.get("org")
    if not isinstance(org, str) or not 0 < len(org.strip()) <= 256:
        return None
    try:
        sampled_at = dt.datetime.fromtimestamp(latest_ms / 1000, dt.timezone.utc)
    except (OverflowError, OSError, ValueError):
        return None
    age = now - sampled_at
    if age < -dt.timedelta(seconds=60) or age > MAX_AGE:
        return None

    usage = latest.get("u")
    if not isinstance(usage, dict):
        usage = latest if data["version"] == 1 else None
    if not isinstance(usage, dict):
        return None

    short = _percent(usage.get("fh"))
    weekly = _percent(usage.get("sd"))
    if short is None and weekly is None:
        return None

    snapshot = QuotaSnapshot(
        provider="claude",
        source=SOURCE,
        login_method="Claude Desktop · локальный снимок",
        updated_at=sampled_at,
        five_hour=short,
        weekly=weekly,
        main_window_id="secondary" if weekly is not None else "primary",
        main_label="Недельная квота" if weekly is not None else "Пятичасовое окно",
        fallback_source=(
            "Claude Desktop · снимок устарел"
            if age > FRESH_AGE else "Claude Desktop · локальные данные"
        ),
    )
    for label, key, title, minutes, role, _ in (_FIVE_HOURS, _WEEK):
        percent = short if label == "five_hour" else weekly
        if percent is None:
            continue
        snapshot.windows.append(
            QuotaWindow(
                id=key, title=title, used_percent=percent,
                window_minutes=minutes, role=role,
            )
        )
    return snapshot


def read_desktop_quota(
    *, paths: tuple[pathlib.Path, ...] | None = None,
    now: dt.datetime | None = None,
) -> QuotaSnapshot:
    """Return current locally recorded percentages, or explicit unavailability."""
    moment = (now or dt.datetime.now(dt.timezone.utc)).astimezone(dt.timezone.utc)
    candidates = [
        sample for path in (paths if paths is not None else desktop_history_paths())
        if (sample := _read_history(path, moment)) is not None
    ]
    if candidates:
        return max(candidates, key=lambda sample: sample.updated_at)
    return QuotaSnapshot(provider="claude", source=SOURCE)


def read_best_local_claude_quota() -> QuotaSnapshot:
    """Newest whole local observation, preferring the Mod on equal timestamps.

    The official Mod writes both windows AND exact resets without a quota
    request; Desktop's plan-usage-history may contain only percentages.
    Never combine two different observations or treat a cache insertion time
    as a quota observation time.
    """
    from .claude_local import read_statusline_quota
    from .claude_mod_bridge import read_mod_quota
    from .utils import claude_root

    if claude_root().resolve() != (pathlib.Path.home() / ".claude").resolve():
        return read_statusline_quota()

    mod = read_mod_quota()
    statusline = read_statusline_quota()
    desktop = read_desktop_quota()

    # Берем один самый свежий снимок целиком, не смешивая разные аккаунты.
    candidates = [item for item in (mod, statusline, desktop)
                  if item.has_data() and item.updated_at is not None]
    if candidates:
        return max(candidates, key=lambda item: item.updated_at)
    return mod

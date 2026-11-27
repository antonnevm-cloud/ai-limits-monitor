"""Shared application utilities: paths, logging, formatting and text helpers."""
from __future__ import annotations

import datetime as dt
import hashlib
import logging
import os
import pathlib
import re
import sys
from logging.handlers import RotatingFileHandler
from typing import Any

APP_DIR_NAME = "ClaudeUsageManager"

UUID_RE = re.compile(r"(?i)([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})")
TAG_RE = re.compile(r"<[^>]+>")
REMINDER_RE = re.compile(r"<system-reminder>.*?</system-reminder>", re.I | re.S)

SKIP_PREFIXES = (
    "caveat:",
    "base directory for this skill:",
    "this session is being continued",
    "stop hook feedback:",
)

MONTHS_RU = (
    "января", "февраля", "марта", "апреля", "мая", "июня",
    "июля", "августа", "сентября", "октября", "ноября", "декабря",
)


# --------------------------------------------------------------------------- #
# Пути приложения
# --------------------------------------------------------------------------- #
def app_root() -> pathlib.Path:
    """Каталог с исходниками программы."""
    return pathlib.Path(__file__).resolve().parent.parent


def data_dir() -> pathlib.Path:
    """Личные настройки и история пользователя, общие для виджета и обработчиков."""
    override = os.environ.get("AI_LIMITS_DATA_DIR")
    if override:
        path = pathlib.Path(override).expanduser()
        if not path.is_absolute():
            raise ValueError("AI_LIMITS_DATA_DIR должен содержать абсолютный путь")
    elif sys.platform == "darwin":
        path = pathlib.Path.home() / "Library" / "Application Support" / APP_DIR_NAME
    elif os.name == "nt":
        base = os.environ.get("LOCALAPPDATA") or pathlib.Path.home() / "AppData" / "Local"
        path = pathlib.Path(base) / APP_DIR_NAME
    else:
        base = os.environ.get("XDG_DATA_HOME") or pathlib.Path.home() / ".local" / "share"
        path = pathlib.Path(base) / APP_DIR_NAME
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    return path


def database_path() -> pathlib.Path:
    return data_dir() / "usage.db"


def data_store_id() -> str:
    path = os.path.normcase(str(data_dir().resolve()))
    return hashlib.sha256(path.encode("utf-8")).hexdigest()


def log_path() -> pathlib.Path:
    return data_dir() / "app.log"


def client_workdir() -> pathlib.Path:
    """Пустой рабочий каталог для запуска клиентов (agy, вход в Codex, Grok, Claude).

    Клиент считает текущий каталог рабочей папкой проекта и не должен видеть историю и настройки монитора.
    """
    path = data_dir() / "client-workdir"
    path.mkdir(parents=True, exist_ok=True)
    return path


def claude_root() -> pathlib.Path:
    """Корень локальных данных Claude Code."""
    from .provider_settings import provider_profile
    return provider_profile("claude")


# --------------------------------------------------------------------------- #
# Логирование
# --------------------------------------------------------------------------- #
_LOGGER_READY = False


def setup_logging(level: int = logging.INFO, *, file_name: str | None = None) -> logging.Logger:
    global _LOGGER_READY
    logger = logging.getLogger("claude_usage_manager")
    if _LOGGER_READY:
        return logger
    logger.setLevel(level)
    handler = RotatingFileHandler(
        data_dir() / file_name if file_name else log_path(), maxBytes=1_000_000, backupCount=3, encoding="utf-8"
    )
    handler.setFormatter(
        logging.Formatter("%(asctime)s %(levelname)s [%(name)s] %(message)s")
    )
    logger.addHandler(handler)
    _LOGGER_READY = True
    return logger


def get_logger() -> logging.Logger:
    return setup_logging()


# --------------------------------------------------------------------------- #
# Время
# --------------------------------------------------------------------------- #
def local_tz() -> dt.tzinfo:
    return dt.datetime.now().astimezone().tzinfo or dt.timezone.utc


def now_local() -> dt.datetime:
    return dt.datetime.now().astimezone()


def parse_iso(value: Any) -> dt.datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        return dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def to_utc(value: dt.datetime) -> dt.datetime:
    if value.tzinfo is None:
        # Наивное местное время переводится по правилам пояса на эту дату, а не по смещению сейчас.
        return value.astimezone(dt.timezone.utc)
    return value.astimezone(dt.timezone.utc)


def local_hm(value: dt.datetime | None, timezone: dt.tzinfo | None = None) -> str:
    if not value:
        return "—"
    # Без явного пояса смещение берется на сам момент: летнее и зимнее время не путаются.
    return (value.astimezone(timezone) if timezone else value.astimezone()).strftime("%H:%M")


def local_dt_text(value: dt.datetime | None, timezone: dt.tzinfo | None = None) -> str:
    if not value:
        return "—"
    return (value.astimezone(timezone) if timezone else value.astimezone()).strftime("%Y-%m-%d %H:%M")


# --------------------------------------------------------------------------- #
# Разбор текста сообщений
# --------------------------------------------------------------------------- #
def content_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(
            str(item.get("text", ""))
            for item in content
            if isinstance(item, dict) and item.get("type") == "text"
        )
    return ""


def clean_prompt(value: str) -> str:
    value = REMINDER_RE.sub(" ", value)
    value = TAG_RE.sub(" ", value)
    value = value.replace("```text", " ").replace("```", " ")
    return re.sub(r"\s+", " ", value).strip()


def meaningful_prompt(value: str) -> bool:
    low = value.casefold()
    return bool(
        len(value) >= 12
        and not any(low.startswith(prefix) for prefix in SKIP_PREFIXES)
        and "task-notification" not in low
        and "local-command-stdout" not in low
    )


def shorten(value: str, limit: int = 110) -> str:
    value = re.sub(r"\s+", " ", value).strip(" \t\r\n-–—:;")
    if len(value) <= limit:
        return value
    cut = value[: limit + 1]
    for separator in (". ", "? ", "! ", "; ", ", "):
        pos = cut.rfind(separator)
        if pos >= limit // 2:
            return cut[: pos + 1].strip()
    return value[: limit - 1].rstrip() + "…"


def human_project(cwd: str | None, project_path: str) -> str:
    source = (cwd or project_path).replace("/", "\\")
    parts = [p for p in source.split("\\") if p and not re.match(r"^[A-Za-z]:$", p)]
    if not parts:
        return project_path
    return parts[-1]


def display_model(value: str) -> str:
    return value.removeprefix("claude-").replace("-", " ")


# --------------------------------------------------------------------------- #
# Форматирование чисел (запятая как десятичный разделитель, RU)
# --------------------------------------------------------------------------- #
def format_tokens(value: int | float) -> str:
    number = float(value or 0)
    if number >= 1_000_000_000:
        return f"{number / 1_000_000_000:.2f}".replace(".", ",") + " млрд"
    if number >= 1_000_000:
        return f"{number / 1_000_000:.2f}".replace(".", ",") + " млн"
    if number >= 1_000:
        return f"{number / 1_000:.1f}".replace(".", ",") + " тыс."
    return f"{int(number):,}".replace(",", " ")


def format_cost(value: float) -> str:
    return f"${value:.2f}".replace(".", ",")


def format_percent(value: float, digits: int = 2) -> str:
    return f"{value:.{digits}f}".replace(".", ",") + "%"


def estimate_project_weekly_pp(
    share_percent: float, weekly_consumed_pp: float | None,
) -> float | None:
    """Распределяет оценку недельного расхода пропорционально доле проекта."""
    if weekly_consumed_pp is None:
        return None
    return weekly_consumed_pp * share_percent / 100.0


def format_pp(value: float | None) -> str:
    return "н/д" if value is None else "≈" + f"{value:.2f}".replace(".", ",") + " п.п."

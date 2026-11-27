"""Таймеры остывания кэша промпта по тредам Claude Code, Codex, Grok Build и Antigravity.

Модуль только читает локальные журналы клиентов (начало и хвост файла, база
разговоров Antigravity только на чтение). Сети, токенов, учётных данных и
запросов к моделям здесь нет, журналы не изменяются и кэш не «подогревается».

Срок жизни кэша:
- Claude Code: время отправки последнего запроса + срок яруса записи кэша
  (`ephemeral_5m` или `ephemeral_1h` в usage.cache_creation).
- Codex: в журнале срок не указан, но он задан документацией OpenAI по модели:
  для GPT-5.6 и новее (в журнале Codex это модель из `turn_context`, например
  `gpt-6.1-sol`) кэш доступен не менее 30 минут после последнего обращения
  (`prompt_cache_options.ttl`, по умолчанию `30m`; OpenAI может хранить дольше).
  Это точное значение, без «~». Для более старых и неопознанных моделей срок
  плавающий, показана оценка 1 час по паузам между запросами в журналах («~»).
- Grok Build: срок не опубликован; оценка 15 минут по паузам в
  `logs/unified.jsonl` (после пауз 7-13 минут кэш сохранился в 4 случаях из 5,
  после 16 минут и дольше не сохранился ни разу из 13), помечается «~».
- Antigravity: в базе разговоров нет данных о кэше, только время последнего
  изменения разговора; оценка 5 минут, помечается «~», проверить её нечем.
  В виджете не показывается (решение владельца 12.11.2026), сканер читает базу
  только если ему явно передали каталог.
"""
from __future__ import annotations

import datetime as dt
import json
import os
import pathlib
import re
import sqlite3
from dataclasses import dataclass, replace
from typing import Any
from urllib.parse import unquote

from . import provider_settings
from .codex_sessions import record_usage, session_metadata, user_text as _codex_user_text
from .history_jsonl import CODEX_MODEL_FIELDS, jsonl_records
from .thread_activity import (
    AliveCheck,
    ClaudeUnreadReader,
    claude_activity,
    claude_turn_blocked,
    codex_activity,
    grok_turn_state,
    newest_database,
    process_alive,
    read_claude_processes,
    read_codex_last_status,
    read_codex_unread,
    read_grok_active,
)
from .utils import (
    clean_prompt,
    content_text,
    get_logger,
    meaningful_prompt,
    parse_iso as _parse_iso,
    shorten,
)


def parse_iso(value: Any) -> dt.datetime | None:
    """Время из журнала всегда с поясом: метка без пояса считается UTC (иначе сравнение с aware-временем роняет скан)."""
    stamp = _parse_iso(value)
    if stamp is not None and stamp.tzinfo is None:
        stamp = stamp.replace(tzinfo=dt.timezone.utc)
    return stamp

CLAUDE_TTL_SHORT = 300
CLAUDE_TTL_LONG = 3600
CODEX_TTL_DEFAULT = 3600
CODEX_TTL_DOCUMENTED = 1800
CODEX_DOCUMENTED_FROM = (5, 6)
CODEX_BASIS_DOCUMENTED = (
    "Срок по документации OpenAI для моделей GPT-5.6 и новее: кэш доступен не менее 30 минут "
    "после последнего обращения, фактически может жить дольше."
)
CODEX_BASIS_ESTIMATE = (
    "Для этой модели OpenAI срок не фиксирует (от 5-10 минут до часа, при расширенном хранении до 24 часов): "
    "показана оценка в 1 час по паузам между запросами в журналах."
)
GROK_BASIS = (
    "xAI срок кэша не публикует и может вытеснить его в любой момент: показана оценка в 15 минут "
    "по паузам между запросами в журнале Grok."
)
GROK_TTL_DEFAULT = 900
ANTIGRAVITY_TTL_DEFAULT = 300
CODEX_LOOKBACK_DAYS = 60
GROK_LOG_TAIL_BYTES = 2 * 1024 * 1024
COOLED_GRACE_SECONDS = 600
COLLAPSED_THREADS = 3  # тредов до ссылки «ещё N» (решение владельца 12.11.2026)
TITLE_LIMIT = 60
HEAD_BYTES = 256 * 1024
TAIL_BYTES = 256 * 1024
TAIL_MAX_BYTES = 4 * 1024 * 1024
MIN_CACHEABLE_TOKENS = 1024
MIN_CODEX_CACHEABLE_TOKENS = MIN_CACHEABLE_TOKENS
GREEN_FRACTION = 0.6
MAX_HUE = 130

DESKTOP_ENVIRONMENT = "Desktop"
TERMINAL_ENVIRONMENT = "терминал"
FORK_MARK = " (fork)"

_CLAUDE_ENVIRONMENTS = {
    "claude-desktop": DESKTOP_ENVIRONMENT,
    "cli": TERMINAL_ENVIRONMENT,
    "claude-vscode": "VS Code",
    "vscode": "VS Code",
}


def is_claude_terminal(thread: "CacheThread") -> bool:
    """Тред Claude Code, запущенный в терминале: интерактивный `claude` (entrypoint cli) или запуск из скрипта (sdk-*)."""
    return thread.agent == "claude" and (thread.environment == TERMINAL_ENVIRONMENT or thread.environment.startswith("sdk"))


@dataclass(frozen=True)
class CacheThread:
    """Один тред с известным моментом, когда его кэш остынет.

    `activity` - состояние треда по локальным данным: работает, ждет ответа
    владельца, завершил ход и не просмотрен; пусто, когда признака нет.
    """

    agent: str
    session_id: str
    title: str
    project: str
    environment: str
    started_at: dt.datetime
    ttl_seconds: int
    estimated: bool = False
    basis: str = ""
    activity: str = ""

    @property
    def expires_at(self) -> dt.datetime:
        return self.started_at + dt.timedelta(seconds=self.ttl_seconds)

    def remaining(self, now: dt.datetime) -> float:
        return max(0.0, (self.expires_at - now).total_seconds())

    def fraction(self, now: dt.datetime) -> float:
        if self.ttl_seconds <= 0:
            return 0.0
        return max(0.0, min(1.0, self.remaining(now) / self.ttl_seconds))

    def cooled(self, now: dt.datetime) -> bool:
        return self.remaining(now) <= 0.0


def cache_hue(fraction: float) -> int:
    """Оттенок заливки: 130 (зелёный) при остатке 60% срока и выше, 0 (красный) при нуле."""
    return round(MAX_HUE * max(0.0, min(1.0, fraction / GREEN_FRACTION)))


def format_remaining(seconds: float, estimated: bool = False) -> str:
    if seconds <= 0:
        return "остыл"
    whole = int(seconds)
    text = f"{whole // 60}:{whole % 60:02d}"
    return f"~{text}" if estimated else text


def select_threads(
    threads: list[CacheThread],
    now: dt.datetime,
    limit: int | None = None,
    grace: int = COOLED_GRACE_SECONDS,
) -> tuple[list[CacheThread], int]:
    """Сверху тред с наименьшей долей остатка; недавно остывшие внизу.

    Остывшие дольше `grace` секунд не показываются. Без `limit` возвращаются все
    треды; с `limit` - первые и число скрытых из-за лимита.
    """
    live = [t for t in threads if not t.cooled(now)]
    cooled = [
        t for t in threads
        if t.cooled(now) and (now - t.expires_at).total_seconds() <= grace
    ]
    live.sort(key=lambda t: (t.fraction(now), t.expires_at))
    cooled.sort(key=lambda t: t.expires_at, reverse=True)
    ordered = live + cooled
    if limit is None:
        return ordered, 0
    return ordered[:limit], max(0, len(ordered) - limit)


# --------------------------------------------------------------------------- #
# Чтение журналов
# --------------------------------------------------------------------------- #
def _read_head_lines(path: pathlib.Path, size: int = HEAD_BYTES) -> list[str]:
    with path.open("rb") as stream:
        data = stream.read(size + 1)
    text = data.decode("utf-8", errors="replace")
    lines = text.split("\n")
    if len(data) > size:
        lines = lines[:-1]
    return [line for line in lines if line.strip()]


def _read_tail_lines(path: pathlib.Path, size: int) -> list[str]:
    with path.open("rb") as stream:
        stream.seek(0, os.SEEK_END)
        length = stream.tell()
        offset = max(0, length - size)
        stream.seek(offset)
        data = stream.read(length - offset)
    lines = data.decode("utf-8", errors="replace").split("\n")
    if offset > 0 and lines:
        lines = lines[1:]
    return [line for line in lines if line.strip()]


def _loads(line: str) -> dict[str, Any] | None:
    try:
        item = json.loads(line)
    except json.JSONDecodeError:
        return None
    return item if isinstance(item, dict) else None


def _project_label(cwd: str | None) -> str:
    if not cwd:
        return "—"
    parts = cwd.replace("\\", "/").rstrip("/").split("/")
    folders = [part.casefold() for part in parts[:-1]]
    if "scratch-workspaces" in folders and {"claude", "claude-data"}.intersection(
        folders[:folders.index("scratch-workspaces")]
    ):
        return "без папки"
    return parts[-1] or cwd


# --------------------------------------------------------------------------- #
# Claude Code
# --------------------------------------------------------------------------- #
def _claude_tier(usage: dict[str, Any]) -> tuple[int, bool] | None:
    """Срок яруса, в который последний раз записывался кэш, или None."""
    created = usage.get("cache_creation")
    if isinstance(created, dict):
        long_tokens = int(created.get("ephemeral_1h_input_tokens") or 0)
        short_tokens = int(created.get("ephemeral_5m_input_tokens") or 0)
        if long_tokens or short_tokens:
            return (CLAUDE_TTL_LONG if long_tokens >= short_tokens else CLAUDE_TTL_SHORT), False
        return None
    if int(usage.get("cache_creation_input_tokens") or 0) > 0:
        return CLAUDE_TTL_SHORT, True
    return None


def _claude_cache_tokens(usage: dict[str, Any]) -> int:
    created = usage.get("cache_creation")
    written = (
        sum(int(v or 0) for v in created.values() if isinstance(v, (int, float)))
        if isinstance(created, dict)
        else int(usage.get("cache_creation_input_tokens") or 0)
    )
    return written + int(usage.get("cache_read_input_tokens") or 0)


def _claude_responses(lines: list[str]) -> tuple[list[dict[str, Any]], list[dt.datetime]]:
    """Ответы основного треда по порядку (куски одного сообщения склеиваются) и времена отправки запросов.

    Строка `api-request` пишется в момент отправки запроса модели: именно тогда
    обновляется срок кэша. Первый кусок ответа появляется позже, при долгом
    размышлении модели - через минуты.
    """
    responses: list[dict[str, Any]] = []
    requests: list[dt.datetime] = []
    for line in lines:
        item = _loads(line)
        if item and item.get("type") == "api-request":
            sent = parse_iso(item.get("timestamp"))
            if sent is not None:
                requests.append(sent)
            continue
        if not item or item.get("type") != "assistant" or item.get("isSidechain"):
            continue
        message = item.get("message")
        if not isinstance(message, dict) or not isinstance(message.get("usage"), dict):
            continue
        message_id = str(message.get("id") or "")
        # Сообщения об ошибке API и служебные ответы клиента: id не настоящий, модель "<synthetic>".
        if message_id.startswith("synthetic") or message.get("model") == "<synthetic>" or item.get("isApiErrorMessage"):
            continue
        stamp = parse_iso(item.get("timestamp"))
        if stamp is None:
            continue
        if responses and message_id and responses[-1]["id"] == message_id:
            responses[-1]["usage"] = message["usage"]
            responses[-1]["last"] = stamp
            continue
        responses.append({"id": message_id, "first": stamp, "last": stamp, "usage": message["usage"]})
    return responses, requests


def _claude_head_meta(path: pathlib.Path) -> dict[str, Any]:
    meta: dict[str, Any] = {"cwd": "", "entrypoint": "", "first_prompt": "", "titles": []}
    for line in _read_head_lines(path):
        item = _loads(line)
        if not item:
            continue
        if not meta["cwd"] and isinstance(item.get("cwd"), str):
            meta["cwd"] = item["cwd"]
        if not meta["entrypoint"] and isinstance(item.get("entrypoint"), str):
            meta["entrypoint"] = item["entrypoint"]
        title = item.get("customTitle")
        if isinstance(title, str) and title.strip():
            meta["titles"].append(title.strip())
        if (
            not meta["first_prompt"]
            and item.get("type") == "user"
            and not item.get("isSidechain")
            and isinstance(item.get("message"), dict)
        ):
            prompt = clean_prompt(content_text(item["message"].get("content")))
            if meaningful_prompt(prompt):
                meta["first_prompt"] = prompt
    return meta


def _parse_claude_file(path: pathlib.Path, head_meta: dict[str, Any]) -> CacheThread | None:
    size = path.stat().st_size
    tail_size = TAIL_BYTES
    while True:
        tail = _read_tail_lines(path, tail_size)
        responses, requests = _claude_responses(tail)
        if responses or tail_size >= TAIL_MAX_BYTES or size <= tail_size:
            break
        tail_size *= 2
    if not responses:
        return None
    last = responses[-1]
    if _claude_cache_tokens(last["usage"]) <= 0:
        return None
    # Срок кэша идёт от отправки последнего запроса: либо запроса, ответ на который
    # уже есть, либо более нового, ответ на который ещё пишется (модель думает).
    started = last["first"]
    sent_before = [r for r in requests if r <= last["first"]]
    if sent_before:
        started = max(sent_before)
    in_flight = [r for r in requests if r > last["last"]]
    if in_flight:
        started = max(in_flight)
    tier: tuple[int, bool] | None = None
    for response in reversed(responses):
        tier = _claude_tier(response["usage"])
        if tier:
            break
    ttl, estimated = tier or (CLAUDE_TTL_SHORT, True)

    titles = list(head_meta["titles"])
    cwd = head_meta["cwd"]
    entrypoint = head_meta["entrypoint"]
    # Имя файла журнала и есть id сессии (так связаны записи Desktop): в журнале форка поле
    # sessionId в скопированной истории принадлежит родителю.
    session_id = path.stem
    for line in tail:
        item = _loads(line)
        if not item:
            continue
        title = item.get("customTitle")
        if isinstance(title, str) and title.strip():
            titles.append(title.strip())
        if not cwd and isinstance(item.get("cwd"), str):
            cwd = item["cwd"]
        if not entrypoint and isinstance(item.get("entrypoint"), str):
            entrypoint = item["entrypoint"]
    if titles:
        title = shorten(titles[-1], TITLE_LIMIT)
    elif head_meta["first_prompt"]:
        title = shorten(head_meta["first_prompt"], TITLE_LIMIT)
    else:
        title = f"Тред {session_id[:8]}"
    return CacheThread(
        agent="claude",
        session_id=session_id,
        title=title,
        project=_project_label(cwd),
        environment=_CLAUDE_ENVIRONMENTS.get(entrypoint, entrypoint or "Claude Code"),
        started_at=started,
        ttl_seconds=ttl,
        estimated=estimated,
    )


# --------------------------------------------------------------------------- #
# Codex
# --------------------------------------------------------------------------- #
def _codex_environment(originator: str, source: str) -> str:
    o, s = originator.casefold(), source.casefold()
    if "desktop" in o:
        return "Desktop"
    if "vscode" in o or "vscode" in s or "extension" in o:
        return "VS Code"
    if s in {"cli", "exec"} or "cli" in o or "exec" in o:
        return "терминал"
    return originator or source or "Codex"


def _codex_last_usage(lines: list[str]) -> tuple[dt.datetime, dict[str, Any]] | None:
    for line in reversed(lines):
        item = _loads(line)
        if not item:
            continue
        payload = item.get("payload")
        usage = record_usage(item.get("type", ""), payload) if isinstance(payload, dict) else None
        stamp = parse_iso(item.get("timestamp"))
        if isinstance(usage, dict) and stamp is not None:
            return stamp, usage
    return None


_CODEX_MODEL = re.compile(r"^gpt-(\d+)(?:\.(\d+))?(?!\d)")


def _scan_codex_model(path: pathlib.Path, offset: int, model: str) -> tuple[int, str]:
    """Читает файл с `offset` построчно до конца и возвращает (новый offset, модель последнего `turn_context`).

    Запись `turn_context` может лежать в мегабайтах от хвоста и начала большого журнала, поэтому
    файл читается один раз целиком, а дальше только дописанное. Неполная последняя строка
    не засчитывается: ее дочитают при следующем сканировании.
    """
    with path.open("rb") as stream:
        stream.seek(offset)
        for offset, item in jsonl_records(stream, (b'"turn_context"',), CODEX_MODEL_FIELDS):
            payload = item.get("payload") if item and item.get("type") == "turn_context" else None
            found = payload.get("model") if isinstance(payload, dict) else None
            if isinstance(found, str) and found:
                model = found
    return offset, model


def codex_documented_ttl(model: str) -> int | None:
    """30 минут для GPT-5.6 и новее (документация OpenAI), для остальных моделей срок не фиксирован."""
    match = _CODEX_MODEL.match(model.casefold())
    if match is None:
        return None
    version = (int(match.group(1)), int(match.group(2) or 0))
    return CODEX_TTL_DOCUMENTED if version >= CODEX_DOCUMENTED_FROM else None


def _parse_codex_file(path: pathlib.Path, ttl_seconds: int, model_of=None, now: dt.datetime | None = None) -> CacheThread | None:
    size = path.stat().st_size
    tail_size = TAIL_BYTES
    while True:
        tail = _read_tail_lines(path, tail_size)
        found = _codex_last_usage(tail)
        if found or tail_size >= TAIL_MAX_BYTES or size <= tail_size:
            break
        tail_size *= 2
    if not found:
        return None
    stamp, usage = found
    if int(usage.get("input_tokens") or 0) < MIN_CODEX_CACHEABLE_TOKENS and not int(usage.get("cached_input_tokens") or 0):
        return None

    session_id = cwd = originator = source = title = ""
    for line in _read_head_lines(path):
        item = _loads(line)
        payload = item.get("payload") if item else None
        if not isinstance(payload, dict):
            continue
        if item.get("type") == "session_meta" or not session_id:
            session_id = session_id or str(payload.get("session_id") or payload.get("id") or "")
            cwd = cwd or (payload["cwd"] if isinstance(payload.get("cwd"), str) else "")
            originator = originator or str(payload.get("originator") or "")
            source = source or (payload["source"] if isinstance(payload.get("source"), str) else "")
        if not cwd and isinstance(payload.get("cwd"), str):
            cwd = payload["cwd"]
        if not title:
            candidate = clean_prompt(_codex_user_text(payload) or "")
            if candidate:
                title = shorten(candidate, TITLE_LIMIT)
        if session_id and cwd and title:
            break
    session_id = session_id or path.stem
    # Модель нужна только живым и недавно остывшим тредам: давно остывшие не показываются, а чтение их
    # журналов целиком (сотни мегабайт на первом скане) ничего не даст.
    live_window = max(CODEX_TTL_DOCUMENTED, ttl_seconds) + COOLED_GRACE_SECONDS
    needs_model = model_of is not None and (now is None or (now - stamp).total_seconds() <= live_window)
    documented = codex_documented_ttl(model_of()) if needs_model else None
    return CacheThread(
        agent="codex",
        session_id=session_id,
        title=title or f"Тред {session_id[-8:]}",
        project=_project_label(cwd),
        environment=_codex_environment(originator, source),
        started_at=stamp,
        ttl_seconds=documented or ttl_seconds,
        estimated=documented is None,
        basis=CODEX_BASIS_DOCUMENTED if documented else CODEX_BASIS_ESTIMATE,
    )


def _recent_codex_files(root: pathlib.Path, now: dt.datetime, days: int = CODEX_LOOKBACK_DAYS) -> list[pathlib.Path]:
    """Файлы rollout за последние `days` дней: каталоги вида ГГГГ/ММ/ДД отсекаются по дате.

    Продолженная сессия дописывается в файл дня своего создания, поэтому окно
    широкое, а живость определяется временем последнего события, не датой папки.
    """
    oldest = now.astimezone().date() - dt.timedelta(days=days)
    found: list[pathlib.Path] = []
    for year in _sub_directories(root):
        for month in _sub_directories(year):
            for day in _sub_directories(month):
                try:
                    folder_date = dt.date(int(year.name), int(month.name), int(day.name))
                except ValueError:
                    continue
                if folder_date < oldest:
                    continue
                found.extend(_files(day, ".jsonl"))
    return found


def _sub_directories(path: pathlib.Path) -> list[pathlib.Path]:
    try:
        return [pathlib.Path(e.path) for e in os.scandir(path) if e.is_dir()]
    except OSError:
        return []


def _files(path: pathlib.Path, suffix: str) -> list[pathlib.Path]:
    try:
        return [pathlib.Path(e.path) for e in os.scandir(path) if e.is_file() and e.name.endswith(suffix)]
    except OSError:
        return []


# --------------------------------------------------------------------------- #
# Grok Build
# --------------------------------------------------------------------------- #
MAX_GROK_META_BYTES = 2 * 1024 * 1024
GROK_REQUEST_EVENT = "shell.turn.inference_done"


@dataclass(frozen=True)
class GrokSession:
    """Метаданные сессии Grok Build из summary.json (журнал запросов общий для всех сессий)."""

    session_id: str
    title: str
    cwd: str
    directory: str = ""


def _as_int(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _grok_last_requests(path: pathlib.Path) -> dict[str, dt.datetime]:
    """Время последнего запроса к модели по каждой сессии: события `shell.turn.inference_done` в хвосте журнала."""
    last: dict[str, dt.datetime] = {}
    try:
        lines = _read_tail_lines(path, GROK_LOG_TAIL_BYTES)
    except OSError:
        return last
    for line in lines:
        if GROK_REQUEST_EVENT not in line:
            continue
        item = _loads(line)
        if not item or item.get("msg") != GROK_REQUEST_EVENT:
            continue
        ctx, sid = item.get("ctx"), item.get("sid")
        if not isinstance(ctx, dict) or not isinstance(sid, str) or not sid:
            continue
        if _as_int(ctx.get("prompt_tokens")) < MIN_CACHEABLE_TOKENS:
            continue
        stamp = parse_iso(item.get("ts"))
        if stamp is not None and (sid not in last or stamp > last[sid]):
            last[sid] = stamp
    return last


def _read_grok_session(path: pathlib.Path) -> GrokSession | None:
    try:
        if path.stat().st_size > MAX_GROK_META_BYTES:
            return None
        data = json.loads(path.read_text(encoding="utf-8", errors="replace"))
    except (OSError, json.JSONDecodeError):
        return None
    info = data.get("info") if isinstance(data, dict) else None
    if not isinstance(info, dict) or not isinstance(info.get("id"), str) or not info["id"]:
        return None
    title = ""
    for field in ("generated_title", "session_summary"):
        if isinstance(data.get(field), str) and data[field].strip():
            title = data[field].strip()
            break
    return GrokSession(
        session_id=info["id"],
        title=shorten(clean_prompt(title), TITLE_LIMIT) if title else "",
        cwd=info["cwd"] if isinstance(info.get("cwd"), str) else "",
        directory=str(path.parent),
    )


def _grok_summary_files(root: pathlib.Path) -> list[pathlib.Path]:
    files: list[pathlib.Path] = []
    for folder in _sub_directories(root):
        for session in _sub_directories(folder):
            summary = session / "summary.json"
            if summary.is_file():
                files.append(summary)
    return files


# --------------------------------------------------------------------------- #
# Antigravity
# --------------------------------------------------------------------------- #
ANTIGRAVITY_DB_NAME = "conversation_summaries.db"
ANTIGRAVITY_MAX_ROWS = 200
_FRACTION = re.compile(r"(\.\d{6})\d+")


def _sql_time(value: Any) -> dt.datetime | None:
    """Время из базы Antigravity: `2026-11-09 17:51:26.8154835+00:00`; нулевая дата 0001-01-01 - «нет значения»."""
    if not isinstance(value, str) or not value:
        return None
    stamp = parse_iso(_FRACTION.sub(r"\1", value.strip()).replace(" ", "T", 1))
    if stamp is None or stamp.year < 2000:
        return None
    return stamp if stamp.tzinfo else stamp.replace(tzinfo=dt.timezone.utc)


def _antigravity_project(workspace_uris: Any) -> str:
    try:
        uris = json.loads(workspace_uris) if isinstance(workspace_uris, str) else []
    except json.JSONDecodeError:
        uris = []
    if not isinstance(uris, list) or not uris or not isinstance(uris[0], str):
        return _project_label(None)
    uri = unquote(uris[0])
    return _project_label(uri[len("file:///"):] if uri.startswith("file:///") else uri)


def _antigravity_environment(source: Any) -> str:
    return "терминал" if isinstance(source, str) and source.endswith("_CLI") else "Antigravity"


def _read_antigravity(db_path: pathlib.Path, ttl_seconds: int, *, limit: int = ANTIGRAVITY_MAX_ROWS) -> list[CacheThread]:
    """Разговоры из базы только на чтение. Данных о кэше в ней нет: берётся время последнего изменения."""
    connection = sqlite3.connect(f"{db_path.resolve().as_uri()}?mode=ro", uri=True, timeout=1)
    try:
        rows = connection.execute(
            "SELECT conversation_id, title, preview, last_modified_time, last_user_input_time,"
            " workspace_uris, source FROM conversation_summaries"
            " ORDER BY last_modified_time DESC LIMIT ?",
            (limit,),
        ).fetchall()
    finally:
        connection.close()
    threads: list[CacheThread] = []
    for conversation_id, title, preview, modified, user_input, workspaces, source in rows:
        stamps = [s for s in (_sql_time(modified), _sql_time(user_input)) if s is not None]
        if not stamps or not isinstance(conversation_id, str):
            continue
        label = title if isinstance(title, str) and title.strip() else preview if isinstance(preview, str) else ""
        threads.append(CacheThread(
            agent="antigravity",
            session_id=conversation_id,
            title=shorten(clean_prompt(label), TITLE_LIMIT) if label.strip() else f"Тред {conversation_id[:8]}",
            project=_antigravity_project(workspaces),
            environment=_antigravity_environment(source),
            started_at=max(stamps),
            ttl_seconds=ttl_seconds,
            estimated=True,
        ))
    return threads


def _signature(path: pathlib.Path) -> tuple[int, int]:
    try:
        stat = path.stat()
    except OSError:
        return (0, 0)
    return (stat.st_mtime_ns, stat.st_size)


# --------------------------------------------------------------------------- #
# Метаданные тредов Claude Desktop (архив, название)
# --------------------------------------------------------------------------- #
DESKTOP_SESSIONS_DIRNAME = "claude-code-sessions"
MAX_DESKTOP_META_BYTES = 2 * 1024 * 1024


@dataclass(frozen=True)
class DesktopSession:
    """Запись приложения Claude Desktop о треде: журнал по cliSessionId живёт отдельно."""

    cli_session_id: str
    archived: bool
    title: str
    forked: bool = False
    host_id: str = ""


def desktop_session_dirs() -> list[pathlib.Path]:
    """Каталоги claude-code-sessions приложения Claude Desktop (учётные данные не читаются)."""
    home = pathlib.Path.home()
    found: list[pathlib.Path] = []
    if os.name == "nt":
        roaming = pathlib.Path(os.environ.get("APPDATA") or home / "AppData" / "Roaming")
        local = pathlib.Path(os.environ.get("LOCALAPPDATA") or home / "AppData" / "Local")
        candidates = [roaming / "Claude" / DESKTOP_SESSIONS_DIRNAME]
        candidates += [folder / DESKTOP_SESSIONS_DIRNAME for folder in _sub_directories(local / "Claude-Data")]
        candidates += [
            folder / "LocalCache" / "Roaming" / "Claude" / DESKTOP_SESSIONS_DIRNAME
            for folder in _sub_directories(local / "Packages")
            if folder.name.startswith("Claude_")
        ]
    else:
        candidates = [
            home / "Library" / "Application Support" / "Claude" / DESKTOP_SESSIONS_DIRNAME,
            pathlib.Path(os.environ.get("XDG_CONFIG_HOME") or home / ".config") / "Claude" / DESKTOP_SESSIONS_DIRNAME,
        ]
    for candidate in candidates:
        if candidate.is_dir() and candidate not in found:
            found.append(candidate)
    return found


def profile_desktop_dirs(profile: pathlib.Path) -> list[pathlib.Path]:
    local = profile / DESKTOP_SESSIONS_DIRNAME
    if local.is_dir():
        return [local]
    if profile == pathlib.Path.home() / ".claude":
        return desktop_session_dirs()
    return []


def _desktop_meta_files(root: pathlib.Path) -> list[pathlib.Path]:
    files: list[pathlib.Path] = []
    for first in _sub_directories(root):
        files.extend(p for p in _files(first, ".json") if p.name.startswith("local_"))
        for second in _sub_directories(first):
            files.extend(p for p in _files(second, ".json") if p.name.startswith("local_"))
    return files


def _read_desktop_session(path: pathlib.Path) -> DesktopSession | None:
    try:
        if path.stat().st_size > MAX_DESKTOP_META_BYTES:
            return None
        data = json.loads(path.read_text(encoding="utf-8", errors="replace"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(data, dict) or not isinstance(data.get("cliSessionId"), str) or not data["cliSessionId"]:
        return None
    title = data.get("title")
    return DesktopSession(
        cli_session_id=data["cliSessionId"],
        archived=data.get("isArchived") is True,
        title=title.strip() if isinstance(title, str) else "",
        forked=bool(data.get("forkedFromSessionId")),
        host_id=path.stem,
    )


class DesktopSessionIndex:
    """Индекс записей Claude Desktop по cliSessionId; файл читается заново при смене размера или времени правки."""

    def __init__(self, directories: list[pathlib.Path]) -> None:
        self._directories = list(directories)
        self._cache: dict[str, tuple[int, int, DesktopSession | None]] = {}

    def refresh(self) -> dict[str, DesktopSession]:
        sessions: dict[str, DesktopSession] = {}
        seen: set[str] = set()
        for root in self._directories:
            for path in _desktop_meta_files(root):
                try:
                    stat = path.stat()
                except OSError:
                    continue
                key = str(path)
                seen.add(key)
                signature = (stat.st_mtime_ns, stat.st_size)
                cached = self._cache.get(key)
                if cached is None or cached[:2] != signature:
                    cached = (*signature, _read_desktop_session(path))
                    self._cache[key] = cached
                record = cached[2]
                if record is None:
                    continue
                known = sessions.get(record.cli_session_id)
                if known is None or (known.archived and not record.archived):
                    sessions[record.cli_session_id] = record
        for key in [k for k in self._cache if k not in seen]:
            del self._cache[key]
        return sessions


# --------------------------------------------------------------------------- #
# Сканер с кэшем по (mtime, size)
# --------------------------------------------------------------------------- #
class ThreadScanner:
    """Находит треды Claude и Codex с живым или недавно остывшим кэшем.

    Файл разбирается заново, только если изменились его размер или время
    правки; начало файла (каталог, среда, первый запрос) читается один раз.

    Для Claude Desktop учитываются записи приложения: заархивированные треды
    не показываются, название берётся из приложения, а журналы Desktop-сессий,
    которых в приложении нет (удалены или заменены новой сессией), скрываются.
    Если записей приложения не нашлось совсем, фильтр не применяется. Без явного
    `claude_dir` каталоги приложения ищутся сами, с явным `claude_dir` (тесты) -
    только переданные в `desktop_dirs`.
    """

    def __init__(
        self,
        claude_dir: pathlib.Path | None = None,
        codex_dir: pathlib.Path | None = None,
        codex_ttl: int = CODEX_TTL_DEFAULT,
        desktop_dirs: list[pathlib.Path] | None = None,
        grok_dir: pathlib.Path | None = None,
        antigravity_dir: pathlib.Path | None = None,
        grok_ttl: int = GROK_TTL_DEFAULT,
        antigravity_ttl: int = ANTIGRAVITY_TTL_DEFAULT,
        alive: AliveCheck = process_alive,
    ) -> None:
        self._alive = alive
        self._unread_reader = ClaudeUnreadReader()
        self._desktop_resolved: list[pathlib.Path] = []
        self._desktop_sessions: dict[str, DesktopSession] = {}
        self._grok_index: dict[str, GrokSession] = {}
        self._grok_events: dict[str, tuple[tuple[int, int], str]] = {}
        self._claude_paths: dict[str, pathlib.Path] = {}
        self._claude_blocked: dict[str, tuple[tuple[int, int], bool]] = {}
        self._activity_failed: set[str] = set()
        self._claude_dir = claude_dir
        self._codex_dir = codex_dir
        self._codex_ttl = codex_ttl
        self._grok_ttl = grok_ttl
        self._antigravity_ttl = antigravity_ttl
        # С явно переданными каталогами Claude или Codex (тесты) личный каталог
        # Grok не читается, пока его не передали тоже.
        defaults = claude_dir is None and codex_dir is None
        self._configured = defaults and grok_dir is None and antigravity_dir is None
        self._grok_dir = grok_dir
        self._antigravity_dir = antigravity_dir  # в виджете не показывается: без явного каталога базу не читаем
        self._desktop_dirs = desktop_dirs
        self._desktop: DesktopSessionIndex | None = None
        self._parsed: dict[str, tuple[tuple[int, int, int, int], CacheThread | None]] = {}
        self._head_meta: dict[str, dict[str, Any]] = {}
        self._codex_models: dict[str, tuple[int, str]] = {}
        self._grok_log: tuple[tuple[int, int], dict[str, dt.datetime]] | None = None
        self._grok_meta: dict[str, tuple[int, int, GrokSession | None]] = {}

    @property
    def codex_ttl(self) -> int:
        return self._codex_ttl

    def set_codex_ttl(self, seconds: int) -> None:
        if seconds != self._codex_ttl:
            self._codex_ttl = seconds
            self._parsed = {k: v for k, v in self._parsed.items() if not k.startswith("codex:")}

    def set_grok_ttl(self, seconds: int) -> None:
        self._grok_ttl = seconds

    def set_antigravity_ttl(self, seconds: int) -> None:
        self._antigravity_ttl = seconds

    def scan(self, now: dt.datetime | None = None) -> dict[str, list[CacheThread]]:
        now = now or dt.datetime.now(dt.timezone.utc)
        if self._configured:
            claude = provider_settings.provider_profile("claude") if provider_settings.provider_enabled("claude") else None
            if claude != self._claude_dir:
                self._desktop = None
                self._desktop_resolved.clear()
                self._desktop_sessions.clear()
                self._claude_paths.clear()
                self._claude_blocked.clear()
                self._claude_dir = claude
            self._codex_dir = (provider_settings.provider_profile("codex") / "sessions"
                               if provider_settings.provider_enabled("codex") else None)
            grok = provider_settings.provider_profile("grok") if provider_settings.provider_enabled("grok") else None
            if grok != self._grok_dir:
                self._grok_dir = grok
                self._grok_log = None
                self._grok_meta.clear()
                self._grok_index.clear()
                self._grok_events.clear()
        seen: set[str] = set()
        result = {
            "claude": self._scan_claude(now, seen) if self._claude_dir else [],
            "codex": self._scan_codex(now, seen) if self._codex_dir else [],
            "grok": self._scan_grok() if self._grok_dir else [],
            "antigravity": self._scan_antigravity() if self._antigravity_dir else [],
        }
        if not result["claude"]:
            self._claude_blocked.clear()
        if not result["grok"]:
            self._grok_events.clear()
        result["claude"] = self._with_activity("claude", result["claude"], lambda threads: self._claude_activity(threads))
        result["codex"] = self._with_activity("codex", result["codex"], lambda threads: self._codex_activity(threads, now))
        result["grok"] = self._with_activity("grok", result["grok"], lambda threads: self._grok_activity(threads))
        for key in [k for k in self._parsed if k not in seen]:
            del self._parsed[key]
        for key in [k for k in self._head_meta if f"claude:{k}" not in seen]:
            del self._head_meta[key]
        for key in [k for k in self._codex_models if f"codex:{k}" not in seen]:
            del self._codex_models[key]
        return result

    def _scan_claude(self, now: dt.datetime, seen: set[str]) -> list[CacheThread]:
        projects = self._claude_dir / "projects"
        horizon = now.timestamp() - (CLAUDE_TTL_LONG + COOLED_GRACE_SECONDS)
        threads: list[CacheThread] = []
        paths: dict[str, pathlib.Path] = {}
        for folder in _sub_directories(projects):
            for path in _files(folder, ".jsonl"):
                thread = self._cached(f"claude:{path}", path, horizon, seen, self._claude_loader)
                if thread:
                    threads.append(thread)
                    paths[thread.session_id] = path
        self._claude_paths = paths
        return self._apply_desktop_index(threads)

    def _apply_desktop_index(self, threads: list[CacheThread]) -> list[CacheThread]:
        if self._desktop is None:
            if self._desktop_dirs is None:
                dirs = profile_desktop_dirs(self._claude_dir) if self._configured else []
            else:
                dirs = self._desktop_dirs
            self._desktop_resolved = list(dirs)
            self._desktop = DesktopSessionIndex(dirs)
        sessions = self._desktop.refresh()
        self._desktop_sessions = sessions
        if not sessions:
            return threads
        result: list[CacheThread] = []
        for thread in threads:
            record = sessions.get(thread.session_id)
            if record is None:
                if thread.environment != DESKTOP_ENVIRONMENT:
                    result.append(thread)
                continue
            if record.archived:
                continue
            title = thread.title
            if record.title:
                title = shorten(record.title, TITLE_LIMIT - len(FORK_MARK) if record.forked else TITLE_LIMIT)
                if record.forked and not title.endswith(FORK_MARK):
                    title += FORK_MARK
            result.append(replace(thread, title=title) if title != thread.title else thread)
        return result

    def _claude_loader(self, path: pathlib.Path) -> CacheThread | None:
        meta = self._head_meta.get(str(path))
        if meta is None:
            meta = _claude_head_meta(path)
            # Начало без названия и осмысленного первого запроса (сессия начата с /model) не запоминаем:
            # при следующей записи в файл оно читается заново, и название появляется.
            if meta["first_prompt"] or meta["titles"]:
                self._head_meta[str(path)] = meta
        return _parse_claude_file(path, meta)

    def _scan_codex(self, now: dt.datetime, seen: set[str]) -> list[CacheThread]:
        """Время правки файлов Codex на Windows отстаёт от записи событий (до часов), поэтому по нему файлы не отсекаются."""
        root = self._codex_dir
        threads: list[CacheThread] = []
        for path in _recent_codex_files(root, now):
            thread = self._cached(
                f"codex:{path}", path, None, seen,
                lambda p: _parse_codex_file(p, self._codex_ttl, lambda: self._codex_model_of(p), now),
            )
            if thread:
                threads.append(thread)
        return self._apply_codex_meta(threads, root)

    def _codex_model_of(self, path: pathlib.Path) -> str:
        offset, model = self._codex_models.get(str(path), (0, ""))
        if offset > path.stat().st_size:
            offset, model = 0, ""
        self._codex_models[str(path)] = _scan_codex_model(path, offset, model)
        return self._codex_models[str(path)][1]

    def _apply_codex_meta(self, threads: list[CacheThread], root: pathlib.Path) -> list[CacheThread]:
        """Название и архив берутся из state_*.sqlite Codex; без базы остаётся название из журнала."""
        meta = session_metadata(root.parent, {thread.session_id for thread in threads})
        result: list[CacheThread] = []
        for thread in threads:
            record = meta.get(thread.session_id)
            if record is None:
                result.append(thread)
            elif not record.archived:
                result.append(replace(thread, title=shorten(record.title, TITLE_LIMIT)) if record.title else thread)
        return result

    def _scan_grok(self) -> list[CacheThread]:
        if self._grok_dir is None:
            return []
        log = self._grok_dir / "logs" / "unified.jsonl"
        signature = _signature(log)
        if self._grok_log is None or self._grok_log[0] != signature:
            self._grok_log = (signature, _grok_last_requests(log))
        requests = self._grok_log[1]
        if not requests:
            return []
        sessions = self._grok_sessions(self._grok_dir / "sessions")
        threads: list[CacheThread] = []
        for session_id, sent in requests.items():
            session = sessions.get(session_id)
            if session is None:
                continue
            threads.append(CacheThread(
                agent="grok",
                session_id=session_id,
                title=session.title or f"Тред {session_id[:8]}",
                project=_project_label(session.cwd),
                environment="терминал",
                started_at=sent,
                ttl_seconds=self._grok_ttl,
                estimated=True,
                basis=GROK_BASIS,
            ))
        return threads

    def _grok_sessions(self, root: pathlib.Path) -> dict[str, GrokSession]:
        sessions: dict[str, GrokSession] = {}
        seen: set[str] = set()
        for path in _grok_summary_files(root):
            key = str(path)
            seen.add(key)
            signature = _signature(path)
            cached = self._grok_meta.get(key)
            if cached is None or cached[:2] != signature:
                cached = (*signature, _read_grok_session(path))
                self._grok_meta[key] = cached
            if cached[2] is not None:
                sessions[cached[2].session_id] = cached[2]
        for key in [k for k in self._grok_meta if k not in seen]:
            del self._grok_meta[key]
        self._grok_index = sessions
        return sessions

    # ------------------------- Состояние тредов ------------------------------ #
    def _with_activity(self, agent: str, threads: list[CacheThread], apply) -> list[CacheThread]:
        """Состояние (работает, ждет ответа, не просмотрен) не должно ломать таймеры: сбой - состояние пустое."""
        if not threads:
            return threads
        try:
            return apply(threads)
        except Exception:
            if agent not in self._activity_failed:
                self._activity_failed.add(agent)
                get_logger().warning("Состояние тредов %s не определено", agent, exc_info=True)
            return threads

    def _claude_activity(self, threads: list[CacheThread]) -> list[CacheThread]:
        processes = read_claude_processes(self._claude_dir / "sessions", self._alive)
        unread = self._unread_reader.read(self._desktop_resolved)
        result: list[CacheThread] = []
        kept: set[str] = set()
        for thread in threads:
            record = self._desktop_sessions.get(thread.session_id)
            is_unread = bool(unread and record is not None and record.host_id in unread)
            kept.add(thread.session_id)
            state = claude_activity(processes.get(thread.session_id), is_unread, self._claude_is_blocked(thread.session_id))
            result.append(replace(thread, activity=state) if state else thread)
        for key in [k for k in self._claude_blocked if k not in kept]:
            del self._claude_blocked[key]
        return result

    def _claude_is_blocked(self, session_id: str) -> bool:
        """Итог последнего хода просит ответа (`blocked: true`); журнал перечитывается, только если он изменился."""
        path = self._claude_paths.get(session_id)
        if path is None:
            return False
        signature = _signature(path)
        cached = self._claude_blocked.get(session_id)
        if cached is None or cached[0] != signature:
            cached = (signature, claude_turn_blocked(path))
            self._claude_blocked[session_id] = cached
        return cached[1]

    def _codex_activity(self, threads: list[CacheThread], now: dt.datetime) -> list[CacheThread]:
        root = self._codex_dir
        ids = [thread.session_id for thread in threads]
        statuses: dict[str, str] = {}
        unread: set[str] = set()
        history = newest_database(root.parent, "thread_history_*.sqlite")
        if history is not None:
            try:
                statuses = read_codex_last_status(history, ids)
            except sqlite3.Error:
                statuses = {}
        state_db = newest_database(root.parent, "state_*.sqlite")
        if state_db is not None:
            try:
                unread = read_codex_unread(state_db, ids)
            except sqlite3.Error:
                unread = set()
        result: list[CacheThread] = []
        for thread in threads:
            idle = (now - thread.started_at).total_seconds()
            state = codex_activity(statuses.get(thread.session_id), thread.session_id in unread, idle)
            result.append(replace(thread, activity=state) if state else thread)
        return result

    def _grok_activity(self, threads: list[CacheThread]) -> list[CacheThread]:
        if self._grok_dir is None:
            return threads
        active = read_grok_active(self._grok_dir, self._alive)
        result: list[CacheThread] = []
        kept: set[str] = set()
        for thread in threads:
            session = self._grok_index.get(thread.session_id)
            if thread.session_id not in active or session is None or not session.directory:
                result.append(thread)
                continue
            kept.add(thread.session_id)
            events = pathlib.Path(session.directory) / "events.jsonl"
            signature = _signature(events)
            cached = self._grok_events.get(thread.session_id)
            if cached is None or cached[0] != signature:
                cached = (signature, grok_turn_state(events))
                self._grok_events[thread.session_id] = cached
            result.append(replace(thread, activity=cached[1]) if cached[1] else thread)
        for key in [k for k in self._grok_events if k not in kept]:
            del self._grok_events[key]
        return result

    def _scan_antigravity(self) -> list[CacheThread]:
        if self._antigravity_dir is None:
            return []
        db = self._antigravity_dir / ANTIGRAVITY_DB_NAME
        if not db.is_file():
            return []
        try:
            return _read_antigravity(db, self._antigravity_ttl)
        except sqlite3.Error:
            return []

    def _cached(self, key, path, horizon, seen, loader):
        try:
            stat = path.stat()
        except OSError:
            return None
        if horizon is not None and stat.st_mtime < horizon:
            return None
        seen.add(key)
        signature = (stat.st_dev, stat.st_ino, stat.st_mtime_ns, stat.st_size)
        cached = self._parsed.get(key)
        if cached:
            previous, thread = cached
            if previous == signature:
                return thread
            if previous[:2] != signature[:2] or signature[3] <= previous[3]:
                self._head_meta.pop(str(path), None)
                self._codex_models.pop(str(path), None)
        try:
            thread = loader(path)
        except OSError:
            return None
        except Exception:
            # Один испорченный журнал не должен ронять скан остальных (и лог каждые 10 секунд).
            get_logger().warning("Журнал %s пропущен при сканировании кэша тредов", path, exc_info=True)
            thread = None
        self._parsed[key] = (signature, thread)
        return thread

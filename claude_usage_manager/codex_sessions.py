"""Чтение имен, признаков архива и счетчиков из выбранного профиля Codex."""
from __future__ import annotations

import datetime as dt
import json
import re
import sqlite3
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from . import pricing
from .thread_activity import newest_database
from .utils import clean_prompt, meaningful_prompt, parse_iso

_INJECTED = ("# agents.md instructions", "<environment_context", "<user_instructions", "<instructions",
             "<recommended_plugins", "here is a list of plugins")
_FILES_HEADER = re.compile(r"^#\s*Files [a-z ]+ by the user:\s*", re.IGNORECASE)


def rollout_files(sessions: Path) -> Iterator[Path]:
    yield from sessions.rglob("*.jsonl")
    if sessions.name == "sessions":
        yield from (sessions.parent / "archived_sessions").rglob("*.jsonl")


def user_text(payload: dict[str, Any]) -> str:
    """Первый запрос пользователя в сообщении или в сохраненной истории."""
    def find(value: Any) -> str:
        if isinstance(value, dict):
            if value.get("type") == "user_message":
                raw = value.get("message")
            elif value.get("role") == "user":
                raw = value.get("content")
            else:
                raw = None
            if isinstance(raw, list):
                raw = "\n".join(str(part.get("text") or part.get("content") or "")
                                for part in raw if isinstance(part, dict))
            if isinstance(raw, str) and not raw.lstrip().casefold().startswith(_INJECTED):
                text = clean_prompt(raw)
                if meaningful_prompt(text) and not text.startswith("<"):
                    return text
            nested = value.values()
        elif isinstance(value, list):
            nested = value
        else:
            return ""
        return next((found for child in nested if (found := find(child))), "")

    return find(payload)


@dataclass(frozen=True)
class SessionMeta:
    title: str
    archived: bool


def session_metadata(profile: Path, ids: set[str]) -> dict[str, SessionMeta]:
    """Имя сохраняется в session_index.jsonl или в таблице threads, архив - в threads."""
    if not ids:
        return {}
    meta: dict[str, SessionMeta] = {}
    try:
        with (profile / "session_index.jsonl").open(encoding="utf-8", errors="replace") as stream:
            for line in stream:
                try:
                    item = json.loads(line)
                except ValueError:
                    continue
                if not isinstance(item, dict):
                    continue
                session_id, name = item.get("id"), item.get("thread_name")
                if isinstance(session_id, str) and session_id in ids and isinstance(name, str) and name.strip():
                    meta[session_id] = SessionMeta(name.strip(), False)
    except OSError:
        pass
    path = newest_database(profile, "state_*.sqlite")
    if path is None:
        return meta
    try:
        # Пока Codex закрыт, файла -wal нет: чтение без блокировок не создает в профиле -shm и -wal.
        # Когда Codex работает, эти файлы уже есть, и обычное чтение видит его последние записи.
        wal = path.with_name(path.name + "-wal")
        query = "mode=ro" if wal.exists() else "mode=ro&immutable=1"
        connection = sqlite3.connect(f"{path.resolve().as_uri()}?{query}", uri=True, timeout=1)
        try:
            columns = {row[1] for row in connection.execute("PRAGMA table_info(threads)")}
            if "id" not in columns:
                return meta
            fields = ", ".join(key if key in columns else "NULL" for key in ("name", "title", "archived"))
            ordered_ids = sorted(ids)
            for start in range(0, len(ordered_ids), 400):
                group = ordered_ids[start:start + 400]
                placeholders = ",".join("?" for _ in group)
                rows = connection.execute(
                    f"SELECT id, {fields} FROM threads WHERE id IN ({placeholders})", group,
                )
                for session_id, name, title, archived in rows:
                    indexed = meta.get(session_id)
                    label = ""
                    for candidate in (name, indexed.title if indexed else "", title):
                        if isinstance(candidate, str) and candidate.strip():
                            label = _FILES_HEADER.sub("", clean_prompt(candidate))
                            if label and not label.casefold().startswith(_INJECTED):
                                break
                            label = ""
                    meta[session_id] = SessionMeta(label, bool(archived))
        finally:
            connection.close()
    except (OSError, sqlite3.Error):
        pass
    return meta


def record_usage(kind: str, payload: dict[str, Any]) -> dict[str, Any] | None:
    if kind == "token_usage_record":
        usage = payload.get("usage")
    else:
        info = payload.get("info")
        usage = info.get("last_token_usage") if isinstance(info, dict) else None
    return usage if isinstance(usage, dict) else None


@dataclass
class UsageRecords:
    """Считает один ответ один раз при повторе снимка или записи в обоих форматах."""
    turn: str = ""
    model: str = ""
    seen_ids: set[str] = field(default_factory=set)
    last_total: dict[str, Any] | None = None
    last_usage: tuple[str, tuple[int, ...], dt.datetime, bool] | None = None
    recent_ids: dict[str, float] = field(default_factory=dict)

    def read(
        self, item: dict[str, Any], payload: dict[str, Any], keep_since: float | None = None,
    ) -> tuple[str, dt.datetime, dict[str, Any]] | None:
        kind = item.get("type", "")
        if isinstance(payload.get("model"), str) and payload["model"]:
            self.model = payload["model"]
        turn = payload.get("turn_id")
        if isinstance(turn, str) and turn:
            self.turn = turn
        usage = record_usage(kind, payload)
        if usage is None:
            return None
        stamp = parse_iso(item.get("timestamp"))
        if stamp is None:
            return None
        if stamp.tzinfo is None:
            stamp = stamp.replace(tzinfo=dt.timezone.utc)
        explicit = kind == "token_usage_record"
        info = payload.get("info")
        total = info.get("total_token_usage") if isinstance(info, dict) else None
        if isinstance(total, dict):
            if self.last_total == total:
                return None
            self.last_total = total
        response_id = payload.get("response_id")
        key = str(response_id or f"{self.turn}:{stamp.isoformat()}")
        if keep_since is None:
            if key in self.seen_ids:
                return None
            self.seen_ids.add(key)
        else:
            if key in self.recent_ids:
                return None
            epoch = stamp.timestamp()
            if epoch >= keep_since:
                self.recent_ids[key] = epoch
        parts = pricing.codex_usage_parts(usage)
        previous = self.last_usage
        if previous and previous[3] != explicit and previous[:2] == (self.turn, parts):
            # Две записи одного ответа идут рядом. Для старого формата без
            # response_id и с одинаковым расходом оставляем разные запросы.
            if self.turn or abs((stamp - previous[2]).total_seconds()) <= 2:
                self.last_usage = None
                return None
        self.last_usage = self.turn, parts, stamp, explicit
        return key, stamp, usage

    def discard_before(self, cutoff: float) -> None:
        if any(stamp < cutoff for stamp in self.recent_ids.values()):
            self.recent_ids = {key: stamp for key, stamp in self.recent_ids.items() if stamp >= cutoff}

"""Расход тредов за неделю лимита по локальным журналам Claude и Codex: токены, доллары по ценам API, доля недели.

Только чтение журналов, без сети. Журналы читаются построчно и дочитываются с места остановки,
поэтому первый проход тяжелый (гигабайты у Claude), а следующие почти бесплатны.
Доля недельного лимита - оценка: недельный процент аккаунта делится между тредами пропорционально
стоимости их токенов (точной доли ни один провайдер не отдает).
У Grok токенов в журнале нет, расход не считается.
"""
from __future__ import annotations

import datetime as dt
import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from . import pricing, provider_settings
from .cache_threads import _project_label, parse_iso
from .codex_sessions import UsageRecords, rollout_files

WEEK = dt.timedelta(days=7)
DAYS_IN_WEEK = 7
MAX_FILE_AGE_DAYS = 9

# Событие расхода: (время, стоимость или None, когда у модели нет цены, токены).
Event = tuple[float, "float | None", int]


def _epoch(value: Any) -> float | None:
    stamp = parse_iso(value)
    return None if stamp is None else stamp.timestamp()


@dataclass
class _FileState:
    offset: int = 0
    session_id: str = ""
    project: str = ""
    codex_usage: UsageRecords = field(default_factory=UsageRecords)
    sidechain_file: bool = False


@dataclass
class _Session:
    project: str = ""
    events: dict[str, Event] = field(default_factory=dict)
    last_time: float = 0.0
    rewrite: float | None = None
    priced_last: bool = False


@dataclass(frozen=True)
class ThreadSpend:
    """Расход одного треда за неделю лимита."""

    cost: float
    tokens: int
    unpriced_tokens: int
    share: float | None
    rewrite: float | None

    @property
    def priced(self) -> bool:
        return self.cost > 0 or self.unpriced_tokens == 0


@dataclass(frozen=True)
class UsageReport:
    """Итог прохода: расход по тредам и расход по проектам и суткам недели (для графика)."""

    threads: dict[tuple[str, str], ThreadSpend]
    daily: dict[str, dict[str, list[float]]]
    totals: dict[str, float]

    def spend(self, agent: str, session_id: str) -> ThreadSpend | None:
        return self.threads.get((agent, session_id))


EMPTY_REPORT = UsageReport({}, {}, {})


class UsageScanner:
    """Накопитель расхода: хранит прочитанное и дочитывает только новые строки журналов."""

    def __init__(self, claude_root: Path | None = None, codex_root: Path | None = None) -> None:
        self._configured = claude_root is None and codex_root is None
        self.claude_root = claude_root
        self.codex_root = codex_root
        self._files: dict[Path, _FileState] = {}
        self._sessions: dict[tuple[str, str], _Session] = {}

    # ------------------------------------------------------------------ #
    def scan(
        self,
        week_start: dict[str, dt.datetime],
        weekly_used: dict[str, float],
        now: dt.datetime | None = None,
    ) -> UsageReport:
        """Читает новое в журналах и считает расход с начала недели каждого агента."""
        now = now or dt.datetime.now(dt.timezone.utc)
        if self._configured:
            claude = (provider_settings.provider_profile("claude") / "projects"
                      if provider_settings.provider_enabled("claude") else None)
            codex = (provider_settings.provider_profile("codex") / "sessions"
                     if provider_settings.provider_enabled("codex") else None)
            if (claude, codex) != (self.claude_root, self.codex_root):
                self.claude_root, self.codex_root = claude, codex
                self._sessions.clear()
                self._files.clear()
        fallback = now - WEEK
        starts = {agent: week_start.get(agent) or fallback for agent in ("claude", "codex")}
        oldest = min(starts.values()) - dt.timedelta(days=1)
        if self.claude_root is not None:
            for path, session_id, sidechain in self._claude_files(oldest):
                self._read_claude(path, session_id, sidechain, starts["claude"].timestamp() - 86400)
        if self.codex_root is not None:
            for path in self._codex_files(oldest):
                self._read_codex(path, starts["codex"].timestamp() - 86400)
        return self._report(starts, weekly_used, now)

    # ------------------------------------------------------------------ #
    # Файлы
    # ------------------------------------------------------------------ #
    def _claude_files(self, oldest: dt.datetime) -> list[tuple[Path, str, bool]]:
        found: list[tuple[Path, str, bool]] = []
        cutoff = oldest.timestamp()
        try:
            projects = [entry for entry in os.scandir(self.claude_root) if entry.is_dir()]
        except OSError:
            return found
        for project in projects:
            try:
                entries = list(os.scandir(project.path))
            except OSError:
                continue
            for entry in entries:
                path = Path(entry.path)
                if entry.is_file() and entry.name.endswith(".jsonl"):
                    if self._fresh(path, cutoff, entry):
                        found.append((path, path.stem, False))
                elif entry.is_dir():
                    sub = Path(entry.path) / "subagents"
                    try:
                        children = list(os.scandir(sub))
                    except OSError:
                        continue
                    for child in children:
                        if child.is_file() and child.name.endswith(".jsonl") and self._fresh(Path(child.path), cutoff, child):
                            found.append((Path(child.path), entry.name, True))
        return found

    def _codex_files(self, oldest: dt.datetime) -> list[Path]:
        cutoff = oldest.timestamp()
        return [path for path in rollout_files(self.codex_root) if self._fresh(path, cutoff)]

    def _fresh(self, path: Path, cutoff: float, entry: "os.DirEntry[str] | None" = None) -> bool:
        if path in self._files:
            return True
        try:
            stamp = entry.stat().st_mtime if entry is not None else path.stat().st_mtime
        except OSError:
            return False
        return stamp >= cutoff

    # ------------------------------------------------------------------ #
    # Чтение журналов
    # ------------------------------------------------------------------ #
    @staticmethod
    def _lines(path: Path, state: _FileState):
        """Строки с места остановки; неполная последняя строка дочитывается в следующий проход."""
        try:
            size = path.stat().st_size
            if size < state.offset:
                state.offset = 0
                state.codex_usage = UsageRecords()
            if size == state.offset:
                return
            stream = path.open("rb")
        except OSError:
            return
        with stream:
            try:
                stream.seek(state.offset)
            except OSError:
                return
            while True:
                line = stream.readline()
                if not line or not line.endswith(b"\n"):
                    return
                state.offset += len(line)
                yield line

    def _session(self, agent: str, session_id: str) -> _Session:
        key = (agent, session_id)
        session = self._sessions.get(key)
        if session is None:
            session = self._sessions[key] = _Session()
        return session

    def _read_claude(self, path: Path, session_id: str, sidechain_file: bool, cutoff: float) -> None:
        state = self._files.setdefault(path, _FileState(session_id=session_id, sidechain_file=sidechain_file))
        session = self._session("claude", state.session_id)
        for line in self._lines(path, state):
            if b'"usage"' not in line or (b'"type":"assistant"' not in line and b'"type": "assistant"' not in line):
                continue
            try:
                item = json.loads(line)
            except ValueError:
                continue
            message = item.get("message") if isinstance(item, dict) else None
            if not isinstance(message, dict) or not isinstance(message.get("usage"), dict):
                continue
            message_id = str(message.get("id") or "")
            if message_id.startswith("synthetic") or message.get("model") == "<synthetic>" or item.get("isApiErrorMessage"):
                continue
            stamp = _epoch(item.get("timestamp"))
            if stamp is None:
                continue
            if not session.project and isinstance(item.get("cwd"), str):
                session.project = _project_label(item["cwd"])
            usage = message["usage"]
            model = str(message.get("model") or "")
            main = not (state.sidechain_file or item.get("isSidechain"))
            if stamp >= cutoff:
                cost = pricing.claude_cost(model, usage)
                tokens = sum(pricing.claude_usage_parts(usage))
                session.events[message_id or f"{path.name}:{state.offset}"] = (stamp, cost, tokens)
            if main and stamp >= session.last_time:
                session.last_time = stamp
                session.rewrite = pricing.claude_rewrite_cost(model, usage)

    def _read_codex(self, path: Path, cutoff: float) -> None:
        state = self._files.setdefault(path, _FileState())
        for line in self._lines(path, state):
            if not any(tag in line for tag in (b'"session_meta"', b'"turn_context"',
                                               b'"token_usage_record"', b'"last_token_usage"')):
                continue
            try:
                item = json.loads(line)
            except ValueError:
                continue
            payload = item.get("payload") if isinstance(item, dict) else None
            if not isinstance(payload, dict):
                continue
            kind = item.get("type")
            if kind == "session_meta":
                state.session_id = state.session_id or str(payload.get("session_id") or payload.get("id") or "")
                if not state.project and isinstance(payload.get("cwd"), str):
                    state.project = _project_label(payload["cwd"])
            reading = state.codex_usage.read(item, payload)
            if reading is None:
                continue
            key, when, usage = reading
            stamp = when.timestamp()
            session_id = str(payload.get("session_id") or state.session_id or path.stem)
            session = self._session("codex", session_id)
            if not session.project:
                session.project = state.project
            model = state.codex_usage.model
            if stamp >= cutoff:
                cost = pricing.codex_cost(model, usage)
                tokens = sum(pricing.codex_usage_parts(usage))
                session.events[key] = (stamp, cost, tokens)
            if stamp >= session.last_time:
                session.last_time = stamp
                session.rewrite = pricing.codex_rewrite_cost(model, usage)

    # ------------------------------------------------------------------ #
    # Итоги
    # ------------------------------------------------------------------ #
    def _report(self, starts: dict[str, dt.datetime], weekly_used: dict[str, float], now: dt.datetime) -> UsageReport:
        sums: dict[tuple[str, str], list[float]] = {}
        totals: dict[str, float] = {}
        daily: dict[str, dict[str, list[float]]] = {}
        end = now.timestamp()
        for (agent, session_id), session in self._sessions.items():
            start = starts[agent].timestamp()
            cost = 0.0
            tokens = unpriced = 0
            for stamp, price, count in session.events.values():
                if stamp < start or stamp > end:
                    continue
                tokens += count
                if price is None:
                    unpriced += count
                    continue
                cost += price
                day = min(DAYS_IN_WEEK - 1, int((stamp - start) // 86400))
                series = daily.setdefault(agent, {}).setdefault(session.project or "без папки", [0.0] * DAYS_IN_WEEK)
                series[day] += price
            if tokens or session.rewrite is not None:
                sums[(agent, session_id)] = [cost, tokens, unpriced]
                totals[agent] = totals.get(agent, 0.0) + cost
        threads: dict[tuple[str, str], ThreadSpend] = {}
        for (agent, session_id), (cost, tokens, unpriced) in sums.items():
            used = weekly_used.get(agent)
            total = totals.get(agent, 0.0)
            share = used * cost / total if used is not None and total > 0 and cost > 0 else None
            session = self._sessions[(agent, session_id)]
            threads[(agent, session_id)] = ThreadSpend(cost, int(tokens), int(unpriced), share, session.rewrite)
        return UsageReport(threads, daily, totals)

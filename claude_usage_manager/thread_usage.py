"""Расход тредов за неделю лимита по локальным журналам Claude и Codex: токены, доллары по ценам API, доля недели.

Только чтение журналов, без сети. Журналы читаются построчно и дочитываются с места остановки,
поэтому первый проход тяжелый (гигабайты у Claude), а следующие почти бесплатны.
Доля недельного лимита - оценка: недельный процент аккаунта делится между тредами пропорционально
стоимости их токенов (точной доли ни один провайдер не отдает).
У Grok токенов в журнале нет, расход не считается.
"""
from __future__ import annotations

import datetime as dt
import os
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from . import pricing, provider_settings
from .cache_threads import _project_label, parse_iso
from .codex_sessions import UsageRecords, rollout_files
from .history_jsonl import CLAUDE_USAGE_FIELDS, CODEX_USAGE_FIELDS, jsonl_records

WEEK = dt.timedelta(days=7)
DAYS_IN_WEEK = 7
FILE_GRACE_SECONDS = 86400
RETENTION_STEP_SECONDS = 3600
_CLAUDE_MARKERS = (b'"usage"',)
_CODEX_MARKERS = (b'"session_meta"', b'"turn_context"', b'"token_usage_record"', b'"last_token_usage"')

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
    codex_usage: UsageRecords | None = None
    sidechain_file: bool = False
    signature: tuple[int, int, int, int] | None = None
    checkpoint: bytes = b""
    last_time: float = 0.0


@dataclass
class _Session:
    project: str = ""
    events: dict[str, Event] = field(default_factory=dict)
    last_time: float = 0.0
    rewrite: float | None = None


@dataclass
class _History:
    files: dict[Path, _FileState] = field(default_factory=dict)
    sessions: dict[str, _Session] = field(default_factory=dict)
    cutoff: float | None = None


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
        self._history = {agent: _History() for agent in ("claude", "codex")}

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
                if claude != self.claude_root:
                    self._history["claude"] = _History()
                if codex != self.codex_root:
                    self._history["codex"] = _History()
                self.claude_root, self.codex_root = claude, codex
        fallback = now - WEEK
        starts = {agent: week_start.get(agent) or fallback for agent in ("claude", "codex")}
        if self.claude_root is not None:
            self._scan_history("claude", self._claude_files(), starts["claude"].timestamp())
        if self.codex_root is not None:
            self._scan_history("codex", ((path, "", False) for path in rollout_files(self.codex_root)),
                               starts["codex"].timestamp())
        return self._report(starts, weekly_used, now)

    # ------------------------------------------------------------------ #
    # Файлы
    # ------------------------------------------------------------------ #
    def _claude_files(self) -> Iterator[tuple[Path, str, bool]]:
        for project in self._entries(self.claude_root):
            if not project.is_dir():
                continue
            for entry in self._entries(project.path):
                if entry.name.endswith(".jsonl") and entry.is_file():
                    path = Path(entry.path)
                    yield path, path.stem, False
                elif entry.is_dir():
                    # Субагенты инструмента Agent лежат прямо в subagents, агенты Workflow глубже:
                    # subagents/workflows/<запуск>/. Все они тратят лимит этого треда.
                    for child in self._jsonl_tree(Path(entry.path) / "subagents"):
                        yield child, entry.name, True

    @classmethod
    def _jsonl_tree(cls, root: Path) -> Iterator[Path]:
        for entry in cls._entries(root):
            if entry.name.endswith(".jsonl") and entry.is_file():
                yield Path(entry.path)
            elif entry.is_dir(follow_symlinks=False):
                yield from cls._jsonl_tree(Path(entry.path))

    @staticmethod
    def _entries(root) -> Iterator[os.DirEntry[str]]:
        try:
            with os.scandir(root) as entries:
                yield from entries
        except OSError:
            return

    @staticmethod
    def _signature(stat: os.stat_result) -> tuple[int, int, int, int]:
        return stat.st_dev, stat.st_ino, stat.st_mtime_ns, stat.st_size

    @staticmethod
    def _replaced(path: Path, state: _FileState, signature: tuple[int, int, int, int]) -> bool:
        previous = state.signature
        if previous is None or previous == signature:
            return False
        if previous[:2] != signature[:2] or signature[3] <= previous[3]:
            return True
        if not state.checkpoint:
            return False
        # После truncate и быстрой дозаписи новый файл уже может быть длиннее старого.
        try:
            with path.open("rb") as stream:
                stream.seek(state.offset - len(state.checkpoint))
                return stream.read(len(state.checkpoint)) != state.checkpoint
        except OSError:
            return False

    def _scan_history(self, agent: str, sources: Iterator[tuple[Path, str, bool]], start: float) -> None:
        history = self._history[agent]
        cutoff = (start - FILE_GRACE_SECONDS) // RETENTION_STEP_SECONDS * RETENTION_STEP_SECONDS
        earlier = history.cutoff is not None and start < history.cutoff
        if history.cutoff is not None and not earlier:
            cutoff = max(cutoff, history.cutoff)
        current = {}
        for path, session_id, sidechain in sources:
            previous = history.files.get(path)
            try:
                stat = path.stat()
            except OSError:
                continue
            if stat.st_mtime >= cutoff or (previous is not None and previous.last_time >= cutoff):
                current[path] = (session_id, sidechain, self._signature(stat))

        if earlier or any(
            self._replaced(path, state, current[path][2]) if path in current else state.last_time >= cutoff
            for path, state in history.files.items()
        ):
            history = self._history[agent] = _History()
        else:
            for path in list(history.files):
                if path not in current:
                    del history.files[path]
            if cutoff != history.cutoff:
                self._discard_before(history, cutoff)
        history.cutoff = cutoff

        for path, (session_id, sidechain, signature) in current.items():
            state = history.files.get(path)
            if state is None:
                state = history.files[path] = _FileState(session_id=session_id, sidechain_file=sidechain)
            if signature == state.signature:
                continue
            try:
                if agent == "claude":
                    self._read_claude(path, state, history.sessions, cutoff)
                else:
                    self._read_codex(path, state, history.sessions, cutoff)
            except OSError:
                continue
            state.signature = signature

    @staticmethod
    def _discard_before(history: _History, cutoff: float) -> None:
        for state in history.files.values():
            if state.codex_usage is not None:
                state.codex_usage.discard_before(cutoff)
        for session_id, session in list(history.sessions.items()):
            if any(event[0] < cutoff for event in session.events.values()):
                session.events = {key: event for key, event in session.events.items() if event[0] >= cutoff}
            if not session.events and session.last_time < cutoff:
                del history.sessions[session_id]

    # ------------------------------------------------------------------ #
    # Чтение журналов
    # ------------------------------------------------------------------ #
    @staticmethod
    def _records(path: Path, state: _FileState, markers, fields):
        """Строки с места остановки; неполная последняя строка дочитывается в следующий проход."""
        with path.open("rb") as stream:
            stream.seek(state.offset)
            state.checkpoint = b""
            for offset, item in jsonl_records(stream, markers, fields):
                state.offset = offset
                if item is not None:
                    yield item
            length = min(64, state.offset)
            stream.seek(state.offset - length)
            state.checkpoint = stream.read(length)

    def _read_claude(self, path: Path, state: _FileState, sessions: dict[str, _Session], cutoff: float) -> None:
        session = sessions.get(state.session_id)
        for item in self._records(path, state, _CLAUDE_MARKERS, CLAUDE_USAGE_FIELDS):
            if item.get("type") != "assistant":
                continue
            message = item.get("message")
            if not isinstance(message, dict) or not isinstance(message.get("usage"), dict):
                continue
            message_id = str(message.get("id") or "")
            if message_id.startswith("synthetic") or message.get("model") == "<synthetic>" or item.get("isApiErrorMessage"):
                continue
            stamp = _epoch(item.get("timestamp"))
            if stamp is None:
                continue
            state.last_time = max(stamp, state.last_time)
            if not state.project and isinstance(item.get("cwd"), str):
                state.project = _project_label(item["cwd"])
            if stamp < cutoff:
                continue
            if session is None:
                session = sessions[state.session_id] = _Session()
            if not session.project:
                session.project = state.project
            usage = message["usage"]
            model = str(message.get("model") or "")
            main = not (state.sidechain_file or item.get("isSidechain"))
            cost = pricing.claude_cost(model, usage)
            tokens = sum(pricing.claude_usage_parts(usage))
            session.events[message_id or f"{path.name}:{state.offset}"] = (stamp, cost, tokens)
            if main and stamp >= session.last_time:
                session.last_time = stamp
                session.rewrite = pricing.claude_rewrite_cost(model, usage)

    def _read_codex(self, path: Path, state: _FileState, sessions: dict[str, _Session], cutoff: float) -> None:
        if state.codex_usage is None:
            state.codex_usage = UsageRecords()
        for item in self._records(path, state, _CODEX_MARKERS, CODEX_USAGE_FIELDS):
            payload = item.get("payload")
            if not isinstance(payload, dict):
                continue
            kind = item.get("type")
            if kind == "session_meta":
                state.session_id = state.session_id or str(payload.get("session_id") or payload.get("id") or "")
                if not state.project and isinstance(payload.get("cwd"), str):
                    state.project = _project_label(payload["cwd"])
            reading = state.codex_usage.read(item, payload, keep_since=cutoff)
            if reading is None:
                continue
            key, when, usage = reading
            stamp = when.timestamp()
            state.last_time = max(stamp, state.last_time)
            if stamp < cutoff:
                continue
            session_id = str(payload.get("session_id") or state.session_id or path.stem)
            session = sessions.get(session_id)
            if session is None:
                session = sessions[session_id] = _Session()
            if not session.project:
                session.project = state.project
            model = state.codex_usage.model
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
        priced_tokens: dict[str, int] = {}
        daily: dict[str, dict[str, list[float]]] = {}
        end = now.timestamp()
        for agent, history in self._history.items():
            start = starts[agent].timestamp()
            owners = _event_owners(history.sessions) if agent == "claude" else {}
            for session_id, session in history.sessions.items():
                cost = 0.0
                tokens = unpriced = 0
                series = None
                for key, (stamp, price, count) in session.events.items():
                    if stamp < start or stamp > end:
                        continue
                    # Ответвление (fork) копирует историю родителя с теми же message.id:
                    # такой ответ считается один раз, у треда-источника.
                    if owners.get(key, session_id) != session_id:
                        continue
                    tokens += count
                    if price is None:
                        unpriced += count
                        continue
                    cost += price
                    if series is None:
                        series = daily.setdefault(agent, {}).setdefault(
                            session.project or "без папки", [0.0] * DAYS_IN_WEEK,
                        )
                    day = min(DAYS_IN_WEEK - 1, int((stamp - start) // FILE_GRACE_SECONDS))
                    series[day] += price
                if tokens or (session.rewrite is not None and session.last_time >= history.cutoff):
                    sums[(agent, session_id)] = [cost, tokens, unpriced]
                    totals[agent] = totals.get(agent, 0.0) + cost
                    priced_tokens[agent] = priced_tokens.get(agent, 0) + tokens - unpriced
        # Доля недели делится по стоимости. Токены моделей без цены оцениваются по средней цене
        # токена этого агента, иначе их расход доставался бы остальным тредам.
        rates = {agent: totals[agent] / count for agent, count in priced_tokens.items()
                 if count > 0 and totals.get(agent, 0.0) > 0}

        def weight(agent: str, cost: float, tokens: float, unpriced: float) -> float:
            rate = rates.get(agent)
            # Без единого ответа с ценой долю не угадываем.
            return cost + unpriced * rate if rate is not None else cost

        weights: dict[str, float] = {}
        for (agent, _session_id), values in sums.items():
            weights[agent] = weights.get(agent, 0.0) + weight(agent, *values)
        threads: dict[tuple[str, str], ThreadSpend] = {}
        for (agent, session_id), (cost, tokens, unpriced) in sums.items():
            used = weekly_used.get(agent)
            mine = weight(agent, cost, tokens, unpriced)
            total = weights.get(agent, 0.0)
            share = used * mine / total if used is not None and total > 0 and mine > 0 else None
            session = self._history[agent].sessions[session_id]
            threads[(agent, session_id)] = ThreadSpend(cost, int(tokens), int(unpriced), share, session.rewrite)
        return UsageReport(threads, daily, totals)


def _event_owners(sessions: dict[str, "_Session"]) -> dict[str, str]:
    """Тред-владелец каждого ответа: самый ранний по первому ответу (родитель раньше ответвления).

    Ответвление начинается с копии истории, поэтому первый ответ у них общий; тогда владельцем
    считается тред с меньшим числом ответов: ответвление - это история родителя плюс продолжение.
    """
    owners: dict[str, str] = {}
    ordered = sorted(sessions.items(),
                     key=lambda item: (min((event[0] for event in item[1].events.values()), default=0.0),
                                       len(item[1].events), item[0]))
    for session_id, session in ordered:
        for key in session.events:
            owners.setdefault(key, session_id)
    return owners

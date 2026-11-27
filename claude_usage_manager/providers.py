"""Провайдеры статистики Claude Code, Codex, Grok Build и Antigravity."""
from __future__ import annotations

import datetime as dt
import json
import os
import pathlib
from collections import defaultdict
from typing import Any

from . import pricing, provider_settings
from .antigravity import read_antigravity
from .analysis import (
    Analyzer as ClaudeAnalyzer,
    aggregate_models,
    aggregate_projects,
    apply_grouping,
    build_timeline,
    local_cost_warning,
    local_sessions,
)
from .codex_sessions import UsageRecords, rollout_files, session_metadata, user_text as _codex_user_text
from .cache_threads import parse_iso
from .database import Database
from .models import AnalysisResult, Period, QuotaSnapshot, ThreadRow
from .quota import fetch_quota
from .utils import (
    get_logger,
    human_project,
    shorten,
)

AGENTS: list[tuple[str, str]] = list(provider_settings.PROVIDER_NAMES.items())
AGENT_LABELS = dict(AGENTS)


class MultiAgentAnalyzer:
    def __init__(self, db: Database | None = None) -> None:
        self.db = db
        self.claude = ClaudeAnalyzer(db)
        self.codex = CodexAnalyzer(db)
        self.grok = GrokAnalyzer(db)
        self.antigravity = AntigravityAnalyzer(db)

    def analyze(
        self,
        agent: str,
        period: Period,
        search: str = "",
        show_hidden: bool = False,
        collect_quota: bool = True,
    ) -> AnalysisResult:
        key = agent.casefold()
        if key == "claude":
            result = self.claude.analyze(period, search, show_hidden, collect_quota)
            result.agent = "claude"
            for row in result.threads:
                row.agent = "claude"
            return result
        if key == "codex":
            return self.codex.analyze(period, search, show_hidden, collect_quota)
        if key == "grok":
            return self.grok.analyze(period, search, show_hidden, collect_quota)
        if key == "antigravity":
            return self.antigravity.analyze(period, search, show_hidden, collect_quota)
        raise ValueError(f"Неизвестный агент: {agent}")


class _BaseProvider:
    agent = ""

    def __init__(self, db: Database | None = None) -> None:
        self.db = db
        self.log = get_logger()

    def _disabled(self, period: Period) -> AnalysisResult:
        return AnalysisResult(period=period, agent=self.agent, quota=QuotaSnapshot(provider=self.agent),
                              warnings=[f"{AGENT_LABELS[self.agent]} отключен в настройках."])

    def _finalize(
        self,
        period: Period,
        rows: list[ThreadRow],
        events: list[dict[str, Any]],
        search: str,
        show_hidden: bool,
        collect_quota: bool,
        warnings: list[str],
    ) -> AnalysisResult:
        rules = self.db.thread_rules() if self.db else {}
        for row in rows:
            rule = rules.get(row.session_id, {})
            override = str(rule.get("title_override") or "").strip()
            if override:
                row.title = shorten(override)
            row.hidden = bool(rule.get("hidden"))
            row.pinned = bool(rule.get("pinned"))
            row.group_name = str(rule.get("group_name") or "")

        raw_scope_rows = [row for row in rows if show_hidden or not row.hidden]
        # Сначала исключаем скрытые элементы, затем группируем. Иначе группа
        # из видимого и скрытого треда снова раскрывает скрытый member_id в
        # итоговых суммах и временной шкале.
        all_rows = apply_grouping(raw_scope_rows)
        scope_rows = all_rows
        total_cost = sum(row.cost for row in scope_rows)
        total_tokens = sum(row.total_tokens for row in scope_rows)

        needle = search.strip().casefold()
        visible = [
            row for row in scope_rows
            if not needle or needle in row.title.casefold() or needle in row.project.casefold()
        ]
        for row in scope_rows:
            basis = total_cost if total_cost > 0 else total_tokens
            value = row.cost if total_cost > 0 else row.total_tokens
            row.share = value / basis * 100 if basis else 0.0
            row.weekly_pp = None
        visible.sort(key=lambda row: (not row.pinned, -row.cost, -row.total_tokens))

        scope_ids = {mid for row in scope_rows for mid in (row.member_ids or [row.session_id])}
        projects = aggregate_projects(scope_rows, total_cost)
        model_rows = aggregate_models(local_sessions(events), scope_ids)

        cost_map: dict[str, float] = {}
        for row in raw_scope_rows:
            members = row.member_ids or [row.session_id]
            per_member = row.cost / len(members) if members else row.cost
            for member_id in members:
                cost_map[member_id] = cost_map.get(member_id, 0.0) + per_member
        timeline = build_timeline(period, events, cost_map, scope_ids)

        if collect_quota:
            quota = fetch_quota(self.agent)
        else:
            quota = QuotaSnapshot(provider=self.agent)
        if not rows:
            warnings.append(f"За выбранный период локальных событий {AGENT_LABELS[self.agent]} не найдено.")

        return AnalysisResult(
            period=period,
            agent=self.agent,
            threads=visible,
            projects=projects,
            model_rows=model_rows,
            timeline=timeline,
            total_tokens=total_tokens,
            total_cost=total_cost,
            work_threads=len(scope_rows),
            quota=quota,
            weekly_consumed_pp=None,
            warnings=list(dict.fromkeys(warnings)),
            generated_at=dt.datetime.now().astimezone(),
            all_threads=scope_rows,
            meta={
                "agent": self.agent,
                "displayed_thread_count": len(visible),
                "scope_thread_count": len(scope_rows),
                "event_count": len(events),
                "events": events,
                "session_costs": cost_map,
            },
        )


class CodexAnalyzer(_BaseProvider):
    agent = "codex"

    def analyze(self, period: Period, search: str = "", show_hidden: bool = False,
                collect_quota: bool = True) -> AnalysisResult:
        if not provider_settings.provider_enabled(self.agent):
            return self._disabled(period)
        profile = provider_settings.provider_profile(self.agent)
        events: list[dict[str, Any]] = []
        rows: dict[str, ThreadRow] = {}
        seen: set[tuple[str, str]] = set()
        for path in rollout_files(profile / "sessions"):
            if not _codex_file_in_period(path, period):
                continue
            parsed = _parse_codex_file(path, period)
            if not parsed:
                continue
            native_id = parsed["internal_id"] or path.stem
            session_id = f"codex:{native_id}"
            cwd = parsed["cwd"]
            row = rows.setdefault(native_id, ThreadRow(
                session_id=session_id, title="", project=human_project(cwd, cwd),
                agent=self.agent, project_path=cwd, member_ids=[session_id],
            ))
            if parsed["title"] and not row.title:
                row.title = parsed["title"]
            if not row.project_path and cwd:
                row.project_path, row.project = cwd, human_project(cwd, cwd)
            for event in parsed["events"]:
                key = (session_id, event.pop("key"))
                if key in seen:
                    continue
                seen.add(key)
                event["session_id"] = session_id
                row.input_tokens += event["inputTokens"]
                row.output_tokens += event["outputTokens"]
                row.cache_read += event["cacheReadTokens"]
                row.cache_creation += event["cacheCreationTokens"]
                row.total_tokens += event["tokens"]
                row.cost += event["cost"] or 0.0
                row.first_activity = _min_dt(row.first_activity, event["timestamp"])
                row.last_activity = _max_dt(row.last_activity, event["timestamp"])
                if event["model"] not in row.models:
                    row.models.append(event["model"])
                events.append(event)

        metadata = session_metadata(profile, set(rows))
        for native_id, row in rows.items():
            meta = metadata.get(native_id)
            if meta and meta.title:
                row.title = shorten(meta.title)
            row.title = row.title or f"Codex {native_id[:8]}"
            row.models.sort()
        warning = local_cost_warning(events)
        return self._finalize(period, list(rows.values()), events, search, show_hidden,
                              collect_quota, [warning] if warning else [])


def _codex_file_in_period(path: pathlib.Path, period: Period) -> bool:
    """Проверяет хвост старого файла: открытый журнал Windows может иметь прежнее время правки."""
    try:
        if path.stat().st_mtime >= period.start_utc.timestamp():
            return True
        with path.open("rb") as stream:
            stream.seek(0, os.SEEK_END)
            stream.seek(max(0, stream.tell() - 16 * 1024))
            for line in reversed(stream.read().splitlines()):
                try:
                    item = json.loads(line)
                except ValueError:
                    continue
                stamp = parse_iso(item.get("timestamp")) if isinstance(item, dict) else None
                if stamp is not None:
                    return stamp >= period.start_utc
    except OSError:
        return False
    return True


class GrokAnalyzer(_BaseProvider):
    """Read metadata from native Grok session summaries without touching chats."""
    agent = "grok"

    def analyze(self, period: Period, search: str = "", show_hidden: bool = False,
                collect_quota: bool = True) -> AnalysisResult:
        if not provider_settings.provider_enabled(self.agent):
            return self._disabled(period)
        root = provider_settings.provider_profile(self.agent) / "sessions"
        rows = []
        warnings = ["Grok Build хранит в описании сессии название и время. Счетчики токенов и стоимость отсутствуют."]
        if root.is_dir():
            for index, path in enumerate(root.rglob("summary.json")):
                if index >= 3000:
                    warnings.append("Grok Build: просмотрено не более 3000 сессий.")
                    break
                item = _read_json(path)
                info = item.get("info") if isinstance(item.get("info"), dict) else {}
                stamp = parse_iso(item.get("last_active_at") or item.get("updated_at"))
                if not stamp or not period.start_utc <= stamp <= period.end_utc:
                    continue
                session_id = str(info.get("id") or path.parent.name)
                cwd = str(info.get("cwd") or "")
                summary = str(item.get("generated_title") or item.get("session_summary") or "").strip()
                rows.append(ThreadRow(
                    session_id=f"grok:{session_id}",
                    title=shorten(summary) if summary else f"Grok {session_id[:8]}",
                    project=human_project(cwd, cwd), project_path=cwd,
                    agent="grok", models=[str(item.get("current_model_id") or "Grok")],
                    first_activity=parse_iso(item.get("created_at")),
                    last_activity=stamp,
                ))
        return self._finalize(period, rows, [], search, show_hidden, collect_quota, warnings)


class AntigravityAnalyzer(_BaseProvider):
    agent = "antigravity"

    def analyze(self, period: Period, search: str = "", show_hidden: bool = False, collect_quota: bool = True) -> AnalysisResult:
        if not provider_settings.provider_enabled(self.agent):
            return self._disabled(period)
        rows, _, events, warnings = read_antigravity(period)
        return self._finalize(period, rows, events, search, show_hidden, collect_quota, warnings)


def _parse_codex_file(path: pathlib.Path, period: Period) -> dict[str, Any] | None:
    internal_id = cwd = title = ""
    totals = defaultdict(int)
    records = UsageRecords()
    events: list[dict[str, Any]] = []
    try:
        with path.open("rb") as stream:
            for line in stream:
                if title and not any(tag in line for tag in (
                    b'"session_meta"', b'"turn_context"', b'"last_token_usage"', b'"token_usage_record"',
                )):
                    continue
                try:
                    item = json.loads(line)
                except ValueError:
                    continue
                payload = item.get("payload") if isinstance(item, dict) else None
                if not isinstance(payload, dict):
                    continue
                if not internal_id:
                    meta_id = payload.get("id") if item.get("type") == "session_meta" or "cwd" in payload else ""
                    internal_id = str(payload.get("session_id") or meta_id or "")
                if not cwd and isinstance(payload.get("cwd"), str):
                    cwd = payload["cwd"]
                if not title:
                    title = shorten(_codex_user_text(payload))
                reading = records.read(item, payload)
                if reading is None:
                    continue
                key, stamp, usage = reading
                if not period.start_utc <= stamp <= period.end_utc:
                    continue
                fresh, cached, written, out = pricing.codex_usage_parts(usage)
                total = fresh + cached + written + out
                if total <= 0:
                    continue
                model = records.model or "codex"
                totals["input"] += fresh
                totals["cache_read"] += cached
                totals["cache_creation"] += written
                totals["output"] += out
                totals["total_tokens"] += total
                events.append({
                    "key": key, "timestamp": stamp, "model": model, "tokens": total,
                    "inputTokens": fresh, "outputTokens": out, "cacheReadTokens": cached,
                    "cacheCreationTokens": written, "cost": pricing.codex_cost(model, usage),
                })
    except OSError:
        return None
    if not events:
        return None
    return {
        "internal_id": internal_id, "cwd": cwd, "title": title,
        "models": {event["model"] for event in events}, "events": events, **totals,
    }


def _read_json(path: pathlib.Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8", errors="replace"))
        return value if isinstance(value, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def _min_dt(left: dt.datetime | None, right: dt.datetime | None) -> dt.datetime | None:
    if left is None:
        return right
    if right is None:
        return left
    return min(left, right)


def _max_dt(left: dt.datetime | None, right: dt.datetime | None) -> dt.datetime | None:
    if left is None:
        return right
    if right is None:
        return left
    return max(left, right)

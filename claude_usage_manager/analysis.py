"""Анализ расхода: сканирование журналов, объединение, агрегации.

Стоимость считается по встроенной таблице цен (pricing.py); внешние утилиты не запускаются.
"""
from __future__ import annotations

import contextlib
import datetime as dt
import json
import os
import pathlib
from typing import Any

from . import pricing, provider_settings
from .cache_threads import DesktopSessionIndex, parse_iso, profile_desktop_dirs
from .database import Database
from .models import (
    AnalysisResult,
    ModelRow,
    Period,
    ProjectRow,
    QuotaSnapshot,
    ThreadRow,
    TimelineBucket,
)
from .quota import fetch_quota, weekly_consumed
from .utils import (
    UUID_RE,
    clean_prompt,
    content_text,
    get_logger,
    human_project,
    meaningful_prompt,
    shorten,
)


# --------------------------------------------------------------------------- #
# Сканирование журналов
# --------------------------------------------------------------------------- #
def _extended_windows_path(path: pathlib.Path) -> pathlib.Path:
    """Возвращает абсолютный extended-length path для файловых операций Windows."""
    if os.name != "nt":
        return path
    absolute = os.path.abspath(path)
    if absolute.startswith("\\\\?\\"):
        return pathlib.Path(absolute)
    if absolute.startswith("\\\\"):
        return pathlib.Path("\\\\?\\UNC\\" + absolute[2:])
    return pathlib.Path("\\\\?\\" + absolute)


def scan_logs(
    claude_root: pathlib.Path,
    start_utc: dt.datetime,
    end_utc: dt.datetime,
) -> tuple[dict[str, dict[str, Any]], list[dict[str, Any]]]:
    """Сканирует JSONL и собирает события расхода assistant в диапазоне.

    Возвращает метаданные сессий и дедуплицированные события для timeline.
    Точная фильтрация по timestamp события, а не по mtime файла. Журналы никуда не копируются.
    """
    projects = claude_root / "projects"
    scan_projects = _extended_windows_path(projects)
    metadata: dict[str, dict[str, Any]] = {}
    events: list[dict[str, Any]] = []
    seen_messages: dict[str, dict[str, Any]] = {}
    min_mtime = start_utc.timestamp()

    for source in scan_projects.rglob("*.jsonl"):
        try:
            if source.stat().st_mtime < min_mtime:
                continue
        except OSError:
            continue

        relative = source.relative_to(scan_projects)
        fallback_id = source.stem
        with contextlib.ExitStack() as files:
            try:
                stream = files.enter_context(source.open("r", encoding="utf-8", errors="replace"))
            except OSError:
                continue
            while True:
                try:
                    line = stream.readline()
                except OSError:
                    break
                if not line:
                    break
                try:
                    item = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if not isinstance(item, dict):
                    continue
                session_id = item.get("sessionId") or fallback_id
                if not isinstance(session_id, str):
                    session_id = fallback_id

                info = metadata.setdefault(session_id, {
                    "custom_titles": [], "first_prompt": None,
                    "period_prompt": None, "cwd": None,
                })
                if not info["cwd"] and isinstance(item.get("cwd"), str):
                    info["cwd"] = item["cwd"]

                title = item.get("customTitle")
                if isinstance(title, str) and title.strip():
                    info["custom_titles"].append(title.strip())

                timestamp = parse_iso(item.get("timestamp"))
                in_range = bool(timestamp and start_utc <= timestamp <= end_utc)

                if item.get("type") == "user" and isinstance(item.get("message"), dict):
                    prompt = clean_prompt(content_text(item["message"].get("content")))
                    if meaningful_prompt(prompt):
                        if not info["first_prompt"]:
                            info["first_prompt"] = prompt
                        if in_range and not info["period_prompt"]:
                            info["period_prompt"] = prompt

                if item.get("type") == "assistant" and in_range:
                    message = item.get("message")
                    if (not isinstance(message, dict) or not isinstance(message.get("usage"), dict)
                            or message.get("model") == "<synthetic>" or item.get("isApiErrorMessage")):
                        continue
                    if not _collect_event(events, seen_messages, session_id, timestamp, message):
                        continue
                    events[-1]["project_path"] = relative.parent.as_posix()
                    if "subagents" in relative.parts[:-1]:
                        events[-1]["source_session"] = source.stem

    return metadata, events


def _collect_event(
    events: list[dict[str, Any]],
    seen_messages: dict[str, dict[str, Any]] | set[str],
    session_id: str,
    timestamp: dt.datetime | None,
    message: dict[str, Any],
) -> bool:
    """Добавляет валидное дедуплицированное событие и сообщает, принято ли оно."""
    message_id = message.get("id")
    if isinstance(message_id, str) and message_id.startswith("synthetic"):
        return False

    usage = message.get("usage") or {}
    inp, write_5m, write_1h, read, out = pricing.claude_usage_parts(usage)
    tokens = inp + write_5m + write_1h + read + out
    if tokens <= 0 or timestamp is None:
        return False
    model = str(message.get("model") or "")

    event = {
        "session_id": session_id,
        "timestamp": timestamp,
        "model": model,
        "tokens": tokens,
        "inputTokens": inp, "outputTokens": out,
        "cacheCreationTokens": write_5m + write_1h, "cacheReadTokens": read,
        "cost": pricing.claude_cost(model, usage),
    }
    if isinstance(message_id, str):
        if isinstance(seen_messages, set):
            if message_id in seen_messages:
                return False
            seen_messages.add(message_id)
        else:
            previous = seen_messages.get(message_id)
            if previous is not None:
                if previous["session_id"] == session_id and timestamp >= previous["timestamp"]:
                    previous.update(event)
                return False
            seen_messages[message_id] = event
    events.append(event)
    return True


def local_sessions(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Собирает из событий строки сессий и разбивку по моделям для общего расчета."""
    sessions: dict[tuple[str, str], dict[str, Any]] = {}
    for event in events:
        key = (event.get("source_session", event["session_id"]), event.get("project_path", ""))
        stamp = event["timestamp"]
        if key not in sessions:
            sessions[key] = {
                "sessionId": key[0], "projectPath": key[1], "first": stamp, "last": stamp,
                "totalCost": 0.0, "totalTokens": 0, "models": {},
            }
        row = sessions[key]
        model = event["model"] or "unknown"
        breakdown = row["models"].setdefault(model, {"modelName": model, "cost": 0.0})
        for part in ("inputTokens", "outputTokens", "cacheCreationTokens", "cacheReadTokens"):
            breakdown[part] = breakdown.get(part, 0) + event[part]
            row[part] = row.get(part, 0) + event[part]
        cost = event["cost"] or 0.0
        breakdown["cost"] += cost
        row["totalCost"] += cost
        row["totalTokens"] += event["tokens"]
        row["first"] = min(row["first"], stamp)
        row["last"] = max(row["last"], stamp)
    for row in sessions.values():
        models = row.pop("models")
        row["modelsUsed"] = list(models)
        row["modelBreakdowns"] = list(models.values())
        row["firstActivity"] = row.pop("first").isoformat()
        row["lastActivity"] = row.pop("last").isoformat()
    return list(sessions.values())


def local_cost_warning(events: list[dict[str, Any]]) -> str:
    unpriced = sorted({event["model"] or "unknown" for event in events if event["cost"] is None})
    if unpriced:
        return "Токены учтены. В оценку стоимости не вошли модели без цены: " + ", ".join(unpriced) + "."
    return ""


# --------------------------------------------------------------------------- #
# Названия тредов
# --------------------------------------------------------------------------- #
def resolve_title(
    session_id: str,
    project_path: str,
    info: dict[str, Any],
    rules: dict[str, dict[str, Any]],
) -> tuple[str, bool]:
    override = (rules.get(session_id, {}).get("title_override") or "").strip()
    if override:
        return shorten(override), False
    custom_titles = info.get("custom_titles") or []
    if custom_titles:
        return shorten(str(custom_titles[-1])), False
    prompt = info.get("period_prompt") or info.get("first_prompt")
    if isinstance(prompt, str) and prompt:
        return shorten(prompt), False
    project = human_project(info.get("cwd"), project_path)
    if project:
        return f"{project} - короткая сессия ({session_id[:8]})", False
    return f"Тред {session_id[:8]}", False


# --------------------------------------------------------------------------- #
# Объединение сессий (субагенты) и построение строк тредов
# --------------------------------------------------------------------------- #
def merge_sessions(
    raw_sessions: list[dict[str, Any]],
    metadata: dict[str, dict[str, Any]],
    rules: dict[str, dict[str, Any]],
) -> list[ThreadRow]:
    """Объединяет части одного sessionId и затем присоединяет субагентов."""
    records: dict[str, dict[str, Any]] = {}
    for raw in raw_sessions:
        session_id = str(raw.get("sessionId") or "")
        if not session_id:
            continue
        incoming_cost = float(raw.get("totalCost") or 0)
        incoming = {
            "session_id": session_id,
            "project_path": str(raw.get("projectPath") or ""),
            "primary_cost": incoming_cost,
            "cost": incoming_cost,
            "total_tokens": int(raw.get("totalTokens") or 0),
            "input_tokens": int(raw.get("inputTokens") or 0),
            "output_tokens": int(raw.get("outputTokens") or 0),
            "cache_creation": int(raw.get("cacheCreationTokens") or 0),
            "cache_read": int(raw.get("cacheReadTokens") or 0),
            "models": set(str(x) for x in (raw.get("modelsUsed") or [])),
            "first_activity": parse_iso(raw.get("firstActivity")),
            "last_activity": parse_iso(raw.get("lastActivity")),
            "subagents": 0,
            "member_ids": [session_id],
        }
        existing = records.get(session_id)
        if existing is None:
            records[session_id] = incoming
            continue

        for field_name in (
            "cost", "total_tokens", "input_tokens", "output_tokens",
            "cache_creation", "cache_read",
        ):
            existing[field_name] += incoming[field_name]
        existing["models"].update(incoming["models"])
        existing["first_activity"] = _min_dt(
            [existing["first_activity"], incoming["first_activity"]]
        )
        existing["last_activity"] = _max_dt(
            [existing["last_activity"], incoming["last_activity"]]
        )
        if _prefer_project_path(
            existing["project_path"], incoming["project_path"],
            existing["primary_cost"], incoming["primary_cost"],
        ):
            existing["project_path"] = incoming["project_path"]
            existing["primary_cost"] = incoming["primary_cost"]

    children: set[str] = set()
    for session_id, record in list(records.items()):
        path = record["project_path"]
        if "subagents" not in path.casefold():
            continue
        matches = UUID_RE.findall(path)
        parent_id = next((x for x in reversed(matches) if x in records), None)
        if not parent_id or parent_id == session_id:
            continue
        parent = records[parent_id]
        for field_name in (
            "cost", "total_tokens", "input_tokens", "output_tokens",
            "cache_creation", "cache_read",
        ):
            parent[field_name] += record[field_name]
        parent["models"].update(record["models"])
        parent["subagents"] += 1 + record["subagents"]
        parent["member_ids"].extend(record["member_ids"])
        parent["first_activity"] = _min_dt(
            [parent["first_activity"], record["first_activity"]]
        )
        parent["last_activity"] = _max_dt(
            [parent["last_activity"], record["last_activity"]]
        )
        children.add(session_id)

    rows: list[ThreadRow] = []
    for session_id, record in records.items():
        if session_id in children:
            continue
        info = metadata.get(session_id, {})
        project_path = record["project_path"]
        if "subagents" in project_path.casefold():
            # Родитель субагента в периоде не работал: расход относится к проекту родителя,
            # а не к служебному каталогу "subagents".
            parent = next((metadata[value] for value in reversed(UUID_RE.findall(project_path))
                           if value in metadata), {})
            if not info.get("cwd") and parent.get("cwd"):
                info = {**info, "cwd": parent["cwd"]}
            project_path = project_path.replace("\\", "/").split("/")[0]
        title, is_service = resolve_title(session_id, project_path, info, rules)
        project = human_project(info.get("cwd"), project_path)
        rule = rules.get(session_id, {})
        rows.append(ThreadRow(
            session_id=session_id,
            title=title,
            project=project,
            project_path=project_path,
            models=sorted(record["models"]),
            cost=record["cost"],
            total_tokens=record["total_tokens"],
            input_tokens=record["input_tokens"],
            output_tokens=record["output_tokens"],
            cache_creation=record["cache_creation"],
            cache_read=record["cache_read"],
            first_activity=record["first_activity"],
            last_activity=record["last_activity"],
            subagents=record["subagents"],
            is_service=is_service,
            hidden=bool(rule.get("hidden")),
            pinned=bool(rule.get("pinned")),
            group_name=str(rule.get("group_name") or ""),
            member_ids=list(dict.fromkeys(record["member_ids"])),
        ))
    return rows


def _prefer_project_path(
    current: str, incoming: str, current_cost: float, incoming_cost: float,
) -> bool:
    """Выбирает основной путь для sessionId, встречающегося в нескольких местах."""
    if not current:
        return bool(incoming)
    current_lower = current.casefold()
    incoming_lower = incoming.casefold()
    current_aux = "worktrees" in current_lower or "subagents" in current_lower
    incoming_aux = "worktrees" in incoming_lower or "subagents" in incoming_lower
    if current_aux != incoming_aux:
        return current_aux and not incoming_aux
    return incoming_cost > current_cost


def apply_grouping(rows: list[ThreadRow]) -> list[ThreadRow]:
    """Объединяет треды с одинаковым group_name в одну строку-группу."""
    groups: dict[str, list[ThreadRow]] = {}
    result: list[ThreadRow] = []
    for row in rows:
        if row.group_name:
            groups.setdefault(row.group_name, []).append(row)
        else:
            result.append(row)

    for name, members in groups.items():
        if len(members) == 1:
            # Группа из одного элемента остаётся обычным тредом с пометкой имени.
            member = members[0]
            member.title = name
            if not member.member_ids:
                member.member_ids = [member.session_id]
            result.append(member)
            continue
        combined = ThreadRow(
            session_id=members[0].session_id,
            title=name,
            project=_common_project(members),
            agent=members[0].agent,
            models=sorted({m for member in members for m in member.models}),
            cost=sum(m.cost for m in members),
            total_tokens=sum(m.total_tokens for m in members),
            input_tokens=sum(m.input_tokens for m in members),
            output_tokens=sum(m.output_tokens for m in members),
            cache_creation=sum(m.cache_creation for m in members),
            cache_read=sum(m.cache_read for m in members),
            first_activity=_min_dt([m.first_activity for m in members]),
            last_activity=_max_dt([m.last_activity for m in members]),
            subagents=sum(m.subagents for m in members),
            is_service=all(m.is_service for m in members),
            hidden=all(m.hidden for m in members),
            pinned=any(m.pinned for m in members),
            group_name=name,
            is_group=True,
            member_ids=[
                mid for member in members
                for mid in (member.member_ids or [member.session_id])
            ],
        )
        result.append(combined)
    return result


def _common_project(rows: list[ThreadRow]) -> str:
    projects = {r.project for r in rows}
    return next(iter(projects)) if len(projects) == 1 else "Несколько проектов"


def _min_dt(values: list[dt.datetime | None]) -> dt.datetime | None:
    present = [v for v in values if v]
    return min(present) if present else None


def _max_dt(values: list[dt.datetime | None]) -> dt.datetime | None:
    present = [v for v in values if v]
    return max(present) if present else None


# --------------------------------------------------------------------------- #
# Агрегации
# --------------------------------------------------------------------------- #
def aggregate_projects(rows: list[ThreadRow], total_cost: float) -> list[ProjectRow]:
    buckets: dict[str, ProjectRow] = {}
    for row in rows:
        bucket = buckets.setdefault(row.project, ProjectRow(project=row.project))
        bucket.threads += 1
        bucket.total_tokens += row.total_tokens
        bucket.cost += row.cost
    total_tokens = sum(bucket.total_tokens for bucket in buckets.values())
    for bucket in buckets.values():
        if total_cost:
            bucket.share = bucket.cost / total_cost * 100
        else:
            bucket.share = bucket.total_tokens / total_tokens * 100 if total_tokens else 0.0
    return sorted(buckets.values(), key=lambda b: (b.cost if total_cost else b.total_tokens), reverse=True)


def aggregate_models(
    raw_sessions: list[dict[str, Any]],
    visible_session_ids: set[str],
) -> list[ModelRow]:
    """Стоимость по моделям из разбивки локальных сессий для видимых тредов."""
    buckets: dict[str, ModelRow] = {}
    for raw in raw_sessions:
        if str(raw.get("sessionId") or "") not in visible_session_ids:
            continue
        for breakdown in raw.get("modelBreakdowns") or []:
            name = str(breakdown.get("modelName") or "unknown")
            bucket = buckets.setdefault(name, ModelRow(model=name))
            bucket.input_tokens += int(breakdown.get("inputTokens") or 0)
            bucket.output_tokens += int(breakdown.get("outputTokens") or 0)
            bucket.cache_creation += int(breakdown.get("cacheCreationTokens") or 0)
            bucket.cache_read += int(breakdown.get("cacheReadTokens") or 0)
            bucket.cost += float(breakdown.get("cost") or 0)
    total_cost = sum(b.cost for b in buckets.values())
    for bucket in buckets.values():
        bucket.total_tokens = (
            bucket.input_tokens + bucket.output_tokens
            + bucket.cache_creation + bucket.cache_read
        )
    total_tokens = sum(b.total_tokens for b in buckets.values())
    for bucket in buckets.values():
        bucket.share = bucket.cost / total_cost * 100 if total_cost else (
            bucket.total_tokens / total_tokens * 100 if total_tokens else 0.0
        )
    return sorted(buckets.values(), key=lambda b: (b.cost, b.total_tokens), reverse=True)



def build_timeline_session_costs(
    raw_sessions: list[dict[str, Any]], events: list[dict[str, Any]],
) -> dict[str, float]:
    costs: dict[str, float] = {}
    for raw in raw_sessions:
        session_id = str(raw.get("sessionId") or "")
        if session_id:
            costs[session_id] = costs.get(session_id, 0.0) + float(raw.get("totalCost") or 0)

    event_ids = {str(event.get("session_id") or "") for event in events}
    for raw in raw_sessions:
        child_id = str(raw.get("sessionId") or "")
        path = str(raw.get("projectPath") or "")
        if not child_id or child_id in event_ids or "subagents" not in path.casefold():
            continue
        parents = [value for value in UUID_RE.findall(path) if value in event_ids]
        if parents:
            parent_id = parents[-1]
            costs[parent_id] = costs.get(parent_id, 0.0) + float(raw.get("totalCost") or 0)
    return costs

def build_timeline(
    period: Period,
    events: list[dict[str, Any]],
    session_costs: dict[str, float],
    visible_session_ids: set[str],
) -> list[TimelineBucket]:
    """Строит корзины по часам (<=48ч) или дням. Токены точные, стоимость оценочная."""
    hourly = period.hourly

    # Токены событий по сессиям — для распределения стоимости.
    session_tokens: dict[str, int] = {}
    for event in events:
        sid = event["session_id"]
        if sid in visible_session_ids:
            session_tokens[sid] = session_tokens.get(sid, 0) + event["tokens"]

    buckets: dict[dt.datetime, TimelineBucket] = {}

    def bucket_key(moment: dt.datetime) -> dt.datetime:
        # Смещение на сам момент: часы до и после перевода времени попадают в свои корзины.
        local = moment.astimezone()
        if hourly:
            return local.replace(minute=0, second=0, microsecond=0)
        return local.replace(hour=0, minute=0, second=0, microsecond=0)

    for event in events:
        sid = event["session_id"]
        if sid not in visible_session_ids:
            continue
        key = bucket_key(event["timestamp"])
        label = key.strftime("%d.%m %H:%M") if hourly else key.strftime("%d.%m.%Y")
        bucket = buckets.setdefault(key, TimelineBucket(label=label, start=key))
        bucket.total_tokens += event["tokens"]
        total_session_tokens = session_tokens.get(sid, 0)
        if total_session_tokens > 0:
            weight = event["tokens"] / total_session_tokens
            bucket.estimated_cost += session_costs.get(sid, 0.0) * weight

    return [buckets[key] for key in sorted(buckets.keys())]


# --------------------------------------------------------------------------- #
# Оркестратор
# --------------------------------------------------------------------------- #
class Analyzer:
    def __init__(self, db: Database | None = None) -> None:
        self._db = db
        self._log = get_logger()

    def analyze(
        self,
        period: Period,
        search: str = "",
        show_hidden: bool = False,
        collect_quota: bool = True,
    ) -> AnalysisResult:
        if not provider_settings.provider_enabled("claude"):
            return AnalysisResult(period=period, warnings=["Claude Code отключен в настройках."])
        rules = self._db.thread_rules() if self._db else {}
        claude = provider_settings.provider_profile("claude")
        warnings: list[str] = []
        # Счетчики читаются из журналов напрямую, стоимость - по встроенной таблице цен.
        metadata, events = scan_logs(claude, period.start_utc, period.end_utc)
        raw_sessions = local_sessions(events)
        for event in events:
            if "source_session" in event:
                event["session_id"] = event.pop("source_session")
        warning = local_cost_warning(events)
        if warning:
            warnings.append(warning)
        if metadata:
            for key, desktop in DesktopSessionIndex(profile_desktop_dirs(claude)).refresh().items():
                if key in metadata and desktop.title:
                    metadata[key]["custom_titles"].append(desktop.title)
        if not raw_sessions:
            warnings.append("За выбранный период локальных событий Claude Code не найдено.")
        timeline_session_costs = build_timeline_session_costs(raw_sessions, events)

        merged_rows = merge_sessions(raw_sessions, metadata, rules)
        scope_rows = [
            row for row in merged_rows
            if show_hidden or not row.hidden
        ]
        # Не даём скрытому member попасть обратно в видимую группу после
        # фильтрации: состав группы должен соответствовать выбранной области.
        scope_rows = apply_grouping(scope_rows)
        total_cost = sum(row.cost for row in scope_rows)
        total_tokens = sum(row.total_tokens for row in scope_rows)

        needle = search.strip().casefold()
        visible = [
            row for row in scope_rows
            if not needle
            or needle in row.title.casefold()
            or needle in row.project.casefold()
        ]

        # Квота и расход.
        quota = fetch_quota() if collect_quota else QuotaSnapshot()
        if collect_quota and self._db is not None and quota.has_data():
            try:
                self._db.record_quota(quota)
            except Exception as error:  # запись квоты не должна ломать анализ
                self._log.warning("Не удалось записать квоту: %s", error)
        if collect_quota and not quota.has_data():
            warnings.append("Квоты недоступны (нет свежих данных локального источника).")

        now_utc = dt.datetime.now(dt.timezone.utc)
        current_weekly = (
            quota.weekly
            if period.start_utc <= now_utc <= period.end_utc
            else None
        )
        weekly_pp = weekly_consumed(period, self._db, current_weekly, current_source=quota.source)

        for row in scope_rows:
            row.share = row.cost / total_cost * 100 if total_cost else (
                row.total_tokens / total_tokens * 100 if total_tokens else 0.0
            )
            row.weekly_pp = weekly_pp * row.share / 100 if weekly_pp is not None else None

        visible.sort(key=lambda r: (not r.pinned, -r.cost, -r.total_tokens))

        scope_session_ids = {mid for row in scope_rows for mid in row.member_ids}
        projects = aggregate_projects(scope_rows, total_cost)
        model_rows = aggregate_models(raw_sessions, scope_session_ids)
        timeline = build_timeline(
            period, events, timeline_session_costs, scope_session_ids
        )

        return AnalysisResult(
            period=period,
            threads=visible,
            projects=projects,
            model_rows=model_rows,
            timeline=timeline,
            total_tokens=total_tokens,
            total_cost=total_cost,
            work_threads=sum(1 for r in scope_rows if not r.is_service),
            quota=quota,
            weekly_consumed_pp=weekly_pp,
            warnings=warnings,
            generated_at=dt.datetime.now().astimezone(),
            all_threads=scope_rows,
            meta={
                "session_count": len(raw_sessions),
                "displayed_thread_count": len(visible),
                "scope_thread_count": len(scope_rows),
                "event_count": len(events),
                "events": events,
                "session_costs": timeline_session_costs,
            },
        )

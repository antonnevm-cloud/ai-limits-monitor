"""Data model and selection logic for AI Limits Monitor."""
from __future__ import annotations

import datetime as dt
from dataclasses import dataclass

from .database import Database
from .models import ManualResetCredits, QuotaSnapshot, QuotaWindow
from .periods import build_period
from .providers import MultiAgentAnalyzer
from .quota import fetch_quota
from .provider_settings import provider_config_key, provider_enabled
from .claude_local import SOURCE as CLAUDE_LOCAL_SOURCE, MAX_AGE as CLAUDE_MAX_AGE, read_manual_resets
from .claude_desktop import read_best_local_claude_quota
from .utils import get_logger, now_local, parse_iso

PROVIDERS = ("claude", "codex", "grok", "antigravity")
PROVIDER_LABELS = {"claude":"Claude","codex":"Codex","grok":"Grok","antigravity":"Antigravity"}
AG_MODES = ("auto_today", "last_used", "fixed", "most_consumed")
AG_FIXED_GROUP_SETTING = "monitor_ag_fixed_group"
# Codex и Grok отвечают не на каждый запрос (процесс не успел подняться, сеть моргнула):
# короткий сбой не должен переключать точку с зеленой на желтую. Последние живые данные
# остаются живыми это время, потом показывается кэш.
LIVE_FAILURE_GRACE = dt.timedelta(minutes=15)
GRACE_PROVIDERS = ("codex", "grok")
_LAST_LIVE: dict[str, tuple[dt.datetime, QuotaSnapshot]] = {}
_LAST_REASON: dict[str, str] = {}
_PROFILE_KEYS: dict[str, str] = {}

@dataclass
class LimitCell:
    label: str
    remaining: float | None
    resets_at: dt.datetime | None = None
    window_id: str = ""

@dataclass
class MonitorRow:
    key: str
    provider: str
    name: str
    subtitle: str = ""
    short: LimitCell | None = None
    long: LimitCell | None = None
    default_visible: bool = True

@dataclass
class ProviderState:
    provider: str
    snapshot: QuotaSnapshot
    live: bool
    error: str = ""

@dataclass
class MonitorData:
    states: dict[str, ProviderState]
    rows: dict[str, list[MonitorRow]]
    compact_rows: list[MonitorRow]
    antigravity_selected_id: str = ""


def remaining(window: QuotaWindow | None) -> float | None:
    if window is None or window.used_percent is None:
        return None
    return max(0.0, min(100.0, 100.0 - float(window.used_percent)))


def manual_reset_urgency(
    credits: ManualResetCredits,
    *,
    now: dt.datetime | None = None,
) -> str:
    current = now or dt.datetime.now(dt.timezone.utc)
    expiry = credits.nearest_expiry(current)
    if credits.state != "available" or not credits.available_count or expiry is None:
        return "normal"
    remaining_time = expiry.astimezone(dt.timezone.utc) - current.astimezone(dt.timezone.utc)
    if remaining_time < dt.timedelta(hours=24):
        return "critical"
    if remaining_time < dt.timedelta(days=3):
        return "warning"
    return "normal"


def _remaining_expiry_text(expiry: dt.datetime, now: dt.datetime) -> str:
    seconds = int((expiry.astimezone(dt.timezone.utc) - now.astimezone(dt.timezone.utc)).total_seconds())
    if seconds <= 0:
        return "истёк"
    days, seconds = divmod(seconds, 86400)
    hours, _ = divmod(seconds, 3600)
    if days:
        return f"через {days} дн. {hours} ч."
    return f"через {hours} ч."


def manual_reset_tooltip(
    credits: ManualResetCredits,
    *,
    now: dt.datetime | None = None,
) -> str:
    if credits.state == "unknown" or credits.available_count is None:
        return "Кредиты ручного сброса: данные недоступны"
    if credits.available_count == 0:
        return ("Вручную указано 0 сбросов; автоматической проверки нет"
                if credits.source.startswith("manual:") else
                "Кредиты ручного сброса: доступных нет")
    current = now or dt.datetime.now(dt.timezone.utc)
    lines = [f"Кредиты ручного сброса: {credits.available_count}"]
    if credits.source.startswith("manual:"):
        lines.append("Введено вручную из Settings > Usage; не подтверждено автоматически")
        if credits.updated_at:
            lines.append("Проверено вручную: " + credits.updated_at.astimezone().strftime("%d.%m.%Y %H:%M"))
    if credits.freshness == "cached":
        stamp = credits.updated_at.astimezone().strftime("%d.%m.%Y %H:%M") if credits.updated_at else "время неизвестно"
        lines.append(f"Кэш от {stamp}")
    for index, item in enumerate(credits.sorted_items(), start=1):
        if item.expires_at is None:
            lines.append(f"{index}. срок действия не указан")
            continue
        expiry = item.expires_at.astimezone()
        lines.append(
            f"{index}. до {expiry:%d.%m.%Y %H:%M} ({_remaining_expiry_text(item.expires_at, current)})"
        )
    missing = max(0, credits.available_count - len(credits.items))
    if missing:
        hint = credits.nearest_expiry_hint
        if hint is not None and not credits.items:
            lines.append(
                f"Ближайший срок: {hint.astimezone():%d.%m.%Y %H:%M} "
                f"({_remaining_expiry_text(hint, current)})"
            )
        lines.append(f"Сроки ещё {missing} кредитов источник не предоставил")
    return "\n".join(lines)


def window_label(window: QuotaWindow) -> str:
    return {300:"5 ч",1440:"День",10080:"Неделя"}.get(
        window.window_minutes,
        "Модель" if window.role == "model" else "Основной" if window.role == "primary" else window.title,
    )


def cell(window: QuotaWindow | None) -> LimitCell | None:
    return None if window is None else LimitCell(window_label(window), remaining(window), window.resets_at, window.id)


def main_windows(snapshot: QuotaSnapshot) -> tuple[QuotaWindow | None, QuotaWindow | None]:
    windows=[w for w in snapshot.windows if w.used_percent is not None and not w.informational]
    short=next((w for w in windows if w.window_minutes and w.window_minutes <= 1440 and w.role != "extra"),None)
    long=next((w for w in windows if w.window_minutes and w.window_minutes >= 10080 and w.role != "extra"),None)
    if snapshot.provider == "grok":
        main=snapshot.main_window() or (windows[0] if windows else None)
        short=None; long=main
    elif short is None and long is None:
        # Окно без длительности в ответе клиента тоже показывается, а не теряется.
        long=next((w for w in windows if w.role != "extra"),None)
    return short,long


def build_rows(states: dict[str,ProviderState]) -> dict[str,list[MonitorRow]]:
    out={p:[] for p in PROVIDERS}
    for provider in ("claude","codex"):
        state=states.get(provider)
        if not state: continue
        snap=state.snapshot; short,long=main_windows(snap)
        name=snap.login_method or PROVIDER_LABELS[provider]
        out[provider].append(MonitorRow(f"{provider}:main",provider,PROVIDER_LABELS[provider],name,cell(short),cell(long),True))
        main_ids={w.id for w in (short,long) if w}
        pools: dict[str, list[QuotaWindow]] = {}
        for w in snap.windows:
            if w.id in main_ids or w.used_percent is None or w.informational or w.role != "extra": continue
            pools.setdefault(w.title, []).append(w)
        # Окна одного дополнительного пула (5 ч и неделя) - одна строка с двумя ячейками.
        for title, pool in pools.items():
            brief=next((w for w in pool if w.window_minutes and w.window_minutes <= 1440),None)
            wide=next((w for w in pool if not w.window_minutes or w.window_minutes > 1440),None)
            out[provider].append(MonitorRow(f"{provider}:{pool[0].id}",provider,title,"Дополнительный пул",cell(brief),cell(wide),False))
    state=states.get("grok")
    if state:
        snap=state.snapshot
        out["grok"].append(MonitorRow(
            "grok:weekly", "grok", "Grok", snap.login_method or "Grok Build",
            None, cell(snap.main_window()), True,
        ))
    state=states.get("antigravity")
    if state:
        group_rows = _antigravity_group_rows(state.snapshot)
        if group_rows:
            out["antigravity"].extend(group_rows)
            return out
        models=[w for w in state.snapshot.windows if w.role == "model" and w.used_percent is not None and not w.informational]
        for w in sorted(models,key=lambda x:x.title.casefold()):
            out["antigravity"].append(MonitorRow(f"antigravity:{w.id}","antigravity",w.title,"Модельный пул",cell(w),None,True))
        if not models:
            fallback = _antigravity_fallback_window(state.snapshot)
            if fallback is not None:
                fallback_cell = cell(fallback)
                if fallback.window_minutes and fallback.window_minutes > 1440:
                    short_cell, long_cell = None, fallback_cell
                else:
                    short_cell, long_cell = fallback_cell, None
                out["antigravity"].append(MonitorRow(
                    "antigravity:console",
                    "antigravity",
                    fallback.title or "Лимит модели",
                    "Консольный лимит",
                    short_cell,
                    long_cell,
                    True,
                ))
    return out


def _antigravity_group_id(window: QuotaWindow) -> str:
    return window.id.rsplit(":", 1)[0] if window.id.startswith("ag-group:") else window.title


def _antigravity_window_groups(snapshot: QuotaSnapshot) -> dict[str, list[QuotaWindow]]:
    grouped: dict[str, list[QuotaWindow]] = {}
    for window in snapshot.windows:
        if (
            window.role != "group"
            or window.used_percent is None
            or window.informational
            or window.window_minutes not in {300, 10080}
        ):
            continue
        grouped.setdefault(_antigravity_group_id(window), []).append(window)

    return dict(sorted(
        grouped.items(),
        key=lambda item: (
            0 if ":gemini" in item[0].casefold() else 1,
            item[1][0].title.casefold(),
            item[0],
        ),
    ))


def _antigravity_group_rows(snapshot: QuotaSnapshot) -> list[MonitorRow]:
    """Combine each Antigravity group's short and weekly windows into one row."""
    rows: list[MonitorRow] = []
    for group_id, windows in _antigravity_window_groups(snapshot).items():
        short_window = next((window for window in windows if window.window_minutes == 300), None)
        weekly_window = next((window for window in windows if window.window_minutes == 10080), None)
        anchor = weekly_window or short_window
        if anchor is None:
            continue
        if short_window and weekly_window:
            subtitle = "Недельная и 5-часовая квота"
        elif weekly_window:
            subtitle = "Недельная квота"
        else:
            subtitle = "Пятичасовая квота"
        rows.append(MonitorRow(
            f"antigravity:{group_id}",
            "antigravity",
            _antigravity_group_title(anchor),
            subtitle,
            cell(short_window),
            cell(weekly_window),
            True,
        ))
    return rows


def antigravity_fixed_group(db: Database) -> str:
    fixed = db.get_setting(AG_FIXED_GROUP_SETTING)
    if fixed is not None:
        return fixed
    if db.get_setting("monitor_ag_mode") == "fixed":
        legacy = db.get_setting("monitor_ag_fixed_model", "") or ""
        group_id, _, period = legacy.rpartition(":")
        if group_id.startswith("ag-group:") and period in {"weekly", "5h"}:
            return group_id
    return ""


def _antigravity_group_title(window: QuotaWindow) -> str:
    return window.title or ("Gemini Models" if ":gemini:" in window.id else "Claude and GPT models")


def _model_windows(snapshot: QuotaSnapshot) -> list[QuotaWindow]:
    return [w for w in snapshot.windows if w.role == "model" and w.used_percent is not None and not w.informational]


def _antigravity_fallback_window(snapshot: QuotaSnapshot) -> QuotaWindow | None:
    """Return a console/legacy Antigravity quota when model groups are absent."""
    main = snapshot.main_window()
    if main is not None:
        return main
    if snapshot.weekly is not None:
        return QuotaWindow(
            "primary", snapshot.main_label or "Лимит модели", snapshot.weekly,
            snapshot.weekly_resets_at, 10080, "primary",
        )
    if snapshot.five_hour is not None:
        return QuotaWindow(
            "primary", snapshot.main_label or "Лимит модели", snapshot.five_hour,
            snapshot.five_hour_resets_at, 300, "primary",
        )
    return None


def _match_model_window(snapshot: QuotaSnapshot, model_name: str) -> QuotaWindow | None:
    key="".join(ch for ch in model_name.casefold() if ch.isalnum())
    windows=_model_windows(snapshot)
    exact=next((w for w in windows if "".join(ch for ch in w.title.casefold() if ch.isalnum()) == key),None)
    if exact: return exact
    return next((w for w in windows if key and (key in "".join(ch for ch in w.title.casefold() if ch.isalnum()) or "".join(ch for ch in w.title.casefold() if ch.isalnum()) in key)),None)


def _scan_antigravity_usage(db: Database) -> tuple[str,str]:
    """Return (most-used-today model, last-used model in recent local history)."""
    analyzer=MultiAgentAnalyzer(db)
    try:
        today=analyzer.analyze("antigravity",build_period("today",now_local()),"",True,False)
    except Exception:
        today=None
    most=""
    if today and today.model_rows:
        most=max(today.model_rows,key=lambda row:row.total_tokens).model
    active=[row for row in (today.threads if today else []) if row.last_activity and row.models]
    if active:
        return most,max(active,key=lambda row:row.last_activity).models[0]
    try:
        recent=analyzer.analyze("antigravity",build_period("last_7d",now_local()),"",True,False)
    except Exception:
        return most,""
    active=[row for row in recent.threads if row.last_activity and row.models]
    last=max(active,key=lambda row:row.last_activity).models[0] if active else ""
    return most,last


def select_antigravity_window(db: Database, snapshot: QuotaSnapshot, *, force_scan: bool=False) -> QuotaWindow | None:
    grouped = _antigravity_window_groups(snapshot)
    if grouped:
        fixed = grouped.get(antigravity_fixed_group(db))
        if fixed:
            return next((window for window in fixed if window.window_minutes == 10080), fixed[0])
        windows = [window for group in grouped.values() for window in group]
        weekly = [window for window in windows if window.window_minutes == 10080]
        return max(weekly or windows, key=lambda window: float(window.used_percent or 0.0))
    windows=_model_windows(snapshot)
    if not windows: return _antigravity_fallback_window(snapshot)
    mode=(db.get_setting("monitor_ag_mode","auto_today") or "auto_today")
    if mode not in AG_MODES: mode="auto_today"
    if mode == "fixed":
        fixed=db.get_setting("monitor_ag_fixed_model","") or ""
        found=next((w for w in windows if w.id == fixed),None)
        if found: return found
    if mode == "most_consumed":
        return max(windows,key=lambda w:float(w.used_percent or 0.0))

    cache_ts=parse_iso(db.get_state("monitor_ag_scan_ts","") or "")
    cache_age=(dt.datetime.now(dt.timezone.utc)-cache_ts).total_seconds() if cache_ts else 10**9
    most=db.get_state("monitor_ag_most_today","") or ""
    last=db.get_state("monitor_ag_last_used","") or ""
    if force_scan or cache_age > 600:
        most,last=_scan_antigravity_usage(db)
        now=dt.datetime.now(dt.timezone.utc).isoformat()
        db.set_state("monitor_ag_scan_ts",now); db.set_state("monitor_ag_most_today",most); db.set_state("monitor_ag_last_used",last)
    candidate=most if mode == "auto_today" else last
    found=_match_model_window(snapshot,candidate) if candidate else None
    if found: return found
    if mode == "auto_today" and last:
        found=_match_model_window(snapshot,last)
        if found: return found
    return max(windows,key=lambda w:float(w.used_percent or 0.0))


def build_compact_rows(db: Database, states: dict[str,ProviderState], *, force_ag_scan: bool=False) -> tuple[list[MonitorRow],str]:
    detailed=build_rows(states); compact=[]
    for provider in ("claude","codex"):
        if detailed.get(provider):
            compact.append(detailed[provider][0])

    if detailed.get("grok"):
        compact.append(detailed["grok"][0])

    ag_state=states.get("antigravity"); selected_id=""
    if ag_state:
        selected = select_antigravity_window(db, ag_state.snapshot, force_scan=force_ag_scan)
        short, long = None, None
        subtitle = "Лимит недоступен"
        if selected:
            selected_id = selected.id
            subtitle = selected.title
            if selected.role == "group" and selected.window_minutes in {300, 10080}:
                group_id = _antigravity_group_id(selected)
                group = next(row for row in detailed["antigravity"] if row.key == f"antigravity:{group_id}")
                short, long = group.short, group.long
                subtitle = group.name
            elif selected.window_minutes and selected.window_minutes > 1440:
                long = cell(selected)
            else:
                short = cell(selected)
        compact.append(MonitorRow("antigravity:selected", "antigravity", "Antigravity", subtitle, short, long))
    return compact,selected_id


def cached_snapshot(db: Database, provider: str) -> QuotaSnapshot:
    if provider in PROVIDERS and not provider_enabled(provider):
        return QuotaSnapshot(provider=provider, error="Источник выключен в настройках")
    default_source = {
        "claude": CLAUDE_LOCAL_SOURCE,
        "codex": "codex-app-server:account/rateLimits/read",
        "grok": "grok:local-billing-log",
        "antigravity": "antigravity:cli-usage",
    }.get(provider, f"unknown:{provider}")
    r, manual_row = db.latest_quota_rows(
        provider, source=CLAUDE_LOCAL_SOURCE if provider == "claude" else None,
        include_manual=provider != "claude",
    )
    if r is None and manual_row is None:
        result = QuotaSnapshot(provider=provider,source=default_source)
        if provider == "claude": result.manual_resets = read_manual_resets(db)
        return result
    base=r or manual_row or {}
    manual=manual_row.get("manual_resets") if manual_row else ManualResetCredits(provider=provider)
    if isinstance(manual,ManualResetCredits) and manual.state != "unknown":
        manual.freshness="cached"
        _drop_expired_credits(manual, dt.datetime.now(dt.timezone.utc))
    q=QuotaSnapshot(five_hour=base.get("five_hour"),weekly=base.get("weekly"),fable_weekly=base.get("fable_weekly"),source=str(base.get("source") or default_source),provider=provider,login_method=str(base.get("login_method") or ""),updated_at=parse_iso(base.get("ts_utc")),windows=list(base.get("windows") or []),main_window_id=str(base.get("main_window_id") or ""),manual_resets=manual)
    if provider == "grok":
        now = dt.datetime.now(dt.timezone.utc)
        weekly = q.main_window()
        reset = weekly.resets_at if weekly is not None else None
        # Never resurrect old Cursor/Grok or expired weekly periods from SQLite.
        if (q.source != default_source or weekly is None or weekly.id != "grok:weekly"
                or weekly.window_minutes != 10080 or reset is None
                or not (now < reset <= now + dt.timedelta(days=8))):
            return QuotaSnapshot(provider="grok", source=default_source,
                                 error="Нет актуального недельного лимита Grok")
        q.windows = [weekly]
        q.weekly = weekly.used_percent
        q.weekly_resets_at = weekly.resets_at
        q.updated_at = parse_iso(weekly.details.get("observed_at"))
    if provider == "claude":
        now = dt.datetime.now(dt.timezone.utc)
        q.manual_resets = read_manual_resets(db, now=now)
        q.windows = [w for w in q.windows if w.resets_at is None or w.resets_at > now]
        if (q.updated_at is None or now - q.updated_at > CLAUDE_MAX_AGE or not q.windows):
            return QuotaSnapshot(provider="claude", source=CLAUDE_LOCAL_SOURCE,
                                 manual_resets=q.manual_resets)
        q.five_hour = next((w.used_percent for w in q.windows if w.window_minutes == 300), None)
        q.weekly = next((w.used_percent for w in q.windows if w.window_minutes == 10080), None)
    if provider == "codex":
        # Окно, срок сброса которого прошел, в кэше уже неверно: после сброса остаток другой.
        now = dt.datetime.now(dt.timezone.utc)
        q.windows = [w for w in q.windows if w.resets_at is None or w.resets_at > now]
        if not any(w.used_percent is not None and not w.informational for w in q.windows):
            return QuotaSnapshot(provider=provider, source=q.source, manual_resets=q.manual_resets,
                                 error="Нет актуального лимита Codex")
        q.five_hour = q.weekly = q.fable_weekly = None
    if provider == "antigravity":
        now = dt.datetime.now(dt.timezone.utc)
        measured = next((w.details["observed_at"] for w in q.windows if w.details.get("observed_at")), None)
        q.updated_at = parse_iso(measured) or q.updated_at
        q.windows = [w for w in q.windows if w.resets_at is None or w.resets_at > now]
        if not q.windows or q.updated_at is None or now - q.updated_at > dt.timedelta(hours=24):
            return QuotaSnapshot(provider=provider, source=q.source, error="Нет свежего измерения Antigravity")
        q.five_hour = None
    main=q.main_window()
    if main: q.weekly=main.used_percent; q.weekly_resets_at=main.resets_at; q.main_label=main.title
    short=next((window for window in q.windows if window.window_minutes == 300 and window.used_percent is not None and not window.informational),None)
    if q.five_hour is None and short is not None:
        q.five_hour=short.used_percent; q.five_hour_resets_at=short.resets_at
    return q


def _drop_expired_credits(credits: ManualResetCredits, now: dt.datetime) -> None:
    """Из кэша убираются кредиты сброса, срок которых уже истек."""
    alive = [item for item in credits.items if item.expires_at is None or item.remaining_seconds(now)]
    expired = [item for item in credits.items if item not in alive]
    if not expired:
        return
    credits.items = alive
    if credits.available_count is not None:
        lost = sum(1 for item in expired if item.status == "available")
        credits.available_count = max(0, credits.available_count - lost)


def _merge_cached_resets(current: QuotaSnapshot, cached: QuotaSnapshot) -> None:
    now = dt.datetime.now(dt.timezone.utc)

    def future(value: dt.datetime | None) -> dt.datetime | None:
        return value if value is not None and value > now else None

    cached_by_id = {window.id: window for window in cached.windows}
    for window in current.windows:
        old = cached_by_id.get(window.id)
        if old is not None and window.resets_at is None:
            window.resets_at = future(old.resets_at)
    if current.five_hour_resets_at is None:
        current.five_hour_resets_at = future(cached.five_hour_resets_at) or future(next(
            (window.resets_at for window in cached.windows if window.window_minutes == 300),
            None,
        ))
    if current.weekly_resets_at is None:
        current.weekly_resets_at = future(cached.weekly_resets_at) or future(next(
            (window.resets_at for window in cached.windows if window.window_minutes == 10080),
            None,
        ))
    if current.manual_resets.state == "unknown" and cached.manual_resets.state != "unknown":
        current.manual_resets = cached.manual_resets
        current.manual_resets.freshness = "cached"


def cached_states(db: Database) -> dict[str,ProviderState]:
    states = {p:ProviderState(p,cached_snapshot(db,p),False,"") for p in PROVIDERS if p != "claude"}
    # Desktop and statusLine both maintain their own original on-disk
    # observations. SQLite recording time is NOT observation time; prefer
    # the actual local sample unconditionally. Never resurrect a quota from
    # SQLite when neither local source remains current.
    if provider_enabled("claude"):
        current = read_best_local_claude_quota()
        current.manual_resets = read_manual_resets(db)
    else:
        current = QuotaSnapshot(provider="claude", error="Источник выключен в настройках")
    states["claude"] = ProviderState(
        "claude", current, False,
        current.fallback_source if current.has_data() else current.error or "Нет текущих локальных данных Claude",
    )
    return states


def _kept_live(provider: str, reason: str) -> ProviderState | None:
    """Последние живые данные Codex/Grok, пока сбой короче LIVE_FAILURE_GRACE."""
    kept = _LAST_LIVE.get(provider) if provider in GRACE_PROVIDERS else None
    if kept is None:
        return None
    stamp, snapshot = kept
    if dt.datetime.now(dt.timezone.utc) - stamp > LIVE_FAILURE_GRACE:
        return None
    note = f"Источник временно не отвечает ({reason}); показаны данные от {stamp.astimezone().strftime('%H:%M:%S')}"
    return ProviderState(provider, snapshot, True, note)


def refresh_provider(db: Database, provider: str, *, excluded_providers=()) -> ProviderState:
    """Обновить одного поставщика, не задерживая остальные."""
    key = provider_config_key(provider)
    if _PROFILE_KEYS.get(provider) != key or not provider_enabled(provider):
        _LAST_LIVE.pop(provider, None)
        _LAST_REASON.pop(provider, None)
        _PROFILE_KEYS[provider] = key
    if not provider_enabled(provider):
        return ProviderState(provider, QuotaSnapshot(provider=provider), False, "Источник выключен в настройках")
    if provider in excluded_providers:
        return ProviderState(provider, cached_snapshot(db, provider), False, "Источник отключен; исторический кеш")
    current = fetch_quota(provider)
    if key != provider_config_key(provider) or not provider_enabled(provider):
        return ProviderState(provider, QuotaSnapshot(provider=provider), False, "Настройки источника изменились")
    if current.source == "antigravity:statusline" and current.updated_at is not None:
        for window in current.windows:
            window.details["observed_at"] = current.updated_at.isoformat()
    if provider == "claude":
        current.manual_resets = read_manual_resets(db)
        if current.has_data():
            fallback = bool(current.fallback_source)
            if not fallback:
                db.record_quota(current, profile_key=key)
            return ProviderState(provider, current, not fallback, current.fallback_source if fallback else "")
        return ProviderState(provider, current, False, "Нет свежих локальных данных Claude")
    cached = cached_snapshot(db, provider)
    if current.has_data() and not current.fallback_source:
        if provider != "antigravity":
            _merge_cached_resets(current, cached)
        db.record_quota(current, profile_key=key)
        _LAST_REASON.pop(provider, None)
        # Срок действия считается от времени измерения, а не повторного чтения.
        if provider in GRACE_PROVIDERS:
            _LAST_LIVE[provider] = (current.updated_at or dt.datetime.now(dt.timezone.utc), current)
        return ProviderState(provider, current, True, "")
    reason = current.fallback_source or current.error or "нет live-данных"
    if _LAST_REASON.get(provider) != reason:
        _LAST_REASON[provider] = reason
        get_logger().warning("Источник %s без live-данных: %s", provider, reason)
    kept = _kept_live(provider, reason)
    if kept is not None:
        return kept
    if current.has_data():
        # Запасной снимок (прошлая запись журнала Grok, прошлый вывод строки состояния agy)
        # не вытесняет более новое измерение из базы и не пишется в историю как свежий замер.
        if (cached.has_data() and cached.updated_at is not None
                and (current.updated_at is None or cached.updated_at >= current.updated_at)):
            return ProviderState(provider, cached, False, current.fallback_source)
        if provider != "antigravity":
            _merge_cached_resets(current, cached)
        db.record_quota(current, profile_key=key, observed_at=current.updated_at)
        return ProviderState(provider, current, False, current.fallback_source)
    if current.manual_resets.state != "unknown":
        db.record_quota(current, profile_key=key)
        cached.manual_resets = current.manual_resets
    return ProviderState(provider, cached, False, current.error or "Нет live-данных")


def refresh_states(db: Database, *, excluded_providers=()) -> dict[str,ProviderState]:
    return {provider: refresh_provider(db, provider, excluded_providers=excluded_providers)
            for provider in PROVIDERS}


def refresh_monitor_data(db: Database) -> MonitorData:
    states=refresh_states(db); rows=build_rows(states); compact,selected=build_compact_rows(db,states)
    return MonitorData(states,rows,compact,selected)

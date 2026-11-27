"""Provider-neutral quota collection and percentage-point history."""
from __future__ import annotations

import datetime as dt
import json
import os
import pathlib
import queue
import re
import subprocess
import threading
import time
from typing import Any

from .database import Database
from .models import ManualResetCredits, ManualResetItem, Period, QuotaSnapshot, QuotaWindow
from .grok_provider import fetch_grok_quota
from .claude_local import SOURCE as CLAUDE_STATUSLINE_SOURCE
from .claude_desktop import SOURCE as CLAUDE_DESKTOP_SOURCE, read_best_local_claude_quota
from .claude_mod_bridge import SOURCE as CLAUDE_MOD_SOURCE
from .antigravity_quota import fetch_antigravity_quota
from .provider_settings import provider_config_key, provider_enabled, provider_environment, provider_executable
from .utils import parse_iso

_CLAUDE_LOCAL_HISTORY_MAX_AGE = dt.timedelta(minutes=30)
_CODEX_APP_SERVER_TIMEOUT = 8.0
_CODEX_APP_SERVER_SOURCE = "codex-app-server:account/rateLimits/read"
_CODEX_RATE_LIMITS_CACHE: dict[str, Any] = {}
_CODEX_RATE_LIMITS_CACHE_TTL = 10.0
_CODEX_RATE_LIMITS_LOCK = threading.Lock()

_CODEX_PLAN_LABELS = {
    "prolite": "Pro Lite",
    "pro": "Pro",
    "plus": "Plus",
    "team": "Team",
    "business": "Business",
    "enterprise": "Enterprise",
    "free": "Free",
}


def codex_cli() -> pathlib.Path | None:
    return provider_executable("codex")


def plan_history_path() -> pathlib.Path | None:
    from .claude_desktop import desktop_history_paths
    from .utils import claude_root
    if claude_root().resolve() != (pathlib.Path.home() / ".claude").resolve():
        return None
    paths = desktop_history_paths()
    return next((path for path in paths if path.is_file()), paths[0] if paths else None)


def _latest_plan_usage_sample() -> tuple[dt.datetime, float | None, float | None] | None:
    """Read the newest native Claude Code usage sample, if it is recent."""
    path = plan_history_path()
    if path is None or not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(data, dict):
        return None

    newest: tuple[dt.datetime, float | None, float | None] | None = None
    for sample in data.get("samples") or []:
        if not isinstance(sample, dict):
            continue
        usage = sample.get("u") or {}
        if not isinstance(usage, dict):
            continue
        try:
            stamp = float(sample.get("t"))
            if stamp < 100_000_000_000:
                stamp *= 1000
            sampled_at = dt.datetime.fromtimestamp(stamp / 1000, tz=dt.timezone.utc)
        except (TypeError, ValueError, OverflowError, OSError):
            continue
        fh = _percent(usage.get("fh"))
        sd = _percent(usage.get("sd"))
        if fh is None and sd is None:
            continue
        candidate = (sampled_at, fh, sd)
        if newest is None or sampled_at > newest[0]:
            newest = candidate
    return newest


def _apply_claude_local_history(snapshot: QuotaSnapshot) -> QuotaSnapshot:
    """Repair Claude's 5-hour value from Claude Code's native local history.

    Claude's Desktop history can be stale or have missing quota windows.
    Claude Code itself keeps
    the current consumed percentage in ``plan-usage-history.json``; prefer
    that sample while it is fresh.
    """
    if snapshot.provider != "claude":
        return snapshot
    had_authoritative_source = snapshot.has_data() and not snapshot.error
    sample = _latest_plan_usage_sample()
    if sample is None or sample[1] is None:
        return snapshot
    sampled_at, five_hour, weekly = sample
    age = dt.datetime.now(dt.timezone.utc) - sampled_at
    if age < dt.timedelta(minutes=-5) or age > _CLAUDE_LOCAL_HISTORY_MAX_AGE:
        return snapshot

    five = next((window for window in snapshot.windows if window.id == "primary"), None)
    if five is None:
        five = QuotaWindow("primary", "Пятичасовое окно", role="primary")
        snapshot.windows.insert(0, five)
    five.title = "Пятичасовое окно"
    five.used_percent = five_hour
    five.window_minutes = 300
    five.role = "primary"
    five.informational = False
    snapshot.five_hour = five_hour
    snapshot.five_hour_resets_at = five.resets_at

    weekly_window = next(
        (
            window
            for window in snapshot.windows
            if window.window_minutes == 10080
            and window.used_percent is not None
            and not window.informational
        ),
        None,
    )
    if weekly_window is None and weekly is not None:
        weekly_window = next((window for window in snapshot.windows if window.id == "secondary"), None)
        if weekly_window is None:
            weekly_window = QuotaWindow("secondary", "Недельная квота", role="secondary")
            snapshot.windows.append(weekly_window)
        weekly_window.title = "Недельная квота"
        weekly_window.used_percent = weekly
        weekly_window.window_minutes = 10080
        weekly_window.role = "secondary"
        weekly_window.informational = False
    if weekly_window is not None:
        snapshot.weekly = weekly_window.used_percent
        snapshot.weekly_resets_at = weekly_window.resets_at
        snapshot.main_window_id = weekly_window.id
        snapshot.main_label = "Недельная квота"
    if snapshot.updated_at is None or snapshot.updated_at < sampled_at:
        snapshot.updated_at = sampled_at
    snapshot.secondary_label = "Пятичасовое окно"
    snapshot.error = ""
    if not had_authoritative_source:
        snapshot.fallback_source = "Локальная история Claude Code"
    if not snapshot.login_method:
        snapshot.login_method = "Claude Code"
    return snapshot


def _percent(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _codex_timestamp(value: Any) -> dt.datetime | None:
    if isinstance(value, dt.datetime):
        return value if value.tzinfo is not None else value.replace(tzinfo=dt.timezone.utc)
    if isinstance(value, str):
        parsed = parse_iso(value)
        if parsed is not None:
            return parsed
        try:
            value = float(value)
        except ValueError:
            return None
    if isinstance(value, (int, float)):
        stamp = float(value)
        if stamp > 100_000_000_000:
            stamp /= 1000.0
        try:
            return dt.datetime.fromtimestamp(stamp, tz=dt.timezone.utc)
        except (OverflowError, OSError, ValueError):
            return None
    return None


def parse_codex_manual_resets(
    payload: Any,
    *,
    now: dt.datetime | None = None,
) -> ManualResetCredits:
    """Normalize ``account/rateLimits/read`` without mixing credits into windows."""
    collected_at = now or dt.datetime.now(dt.timezone.utc)
    source = "codex-app-server:account/rateLimits/read"
    candidates: list[dict[str, Any]] = []
    if isinstance(payload, dict):
        candidates.append(payload)
        result = payload.get("result")
        if isinstance(result, dict):
            candidates.append(result)
    for candidate in list(candidates):
        for key in ("rateLimits", "rate_limits"):
            nested = candidate.get(key)
            if isinstance(nested, dict):
                candidates.append(nested)

    summary: dict[str, Any] | None = None
    for candidate in candidates:
        nested = candidate.get("rateLimitResetCredits")
        if isinstance(nested, dict):
            summary = nested
            break
        if "availableCount" in candidate and ("credits" in candidate or len(candidate) <= 4):
            summary = candidate
            break
    if summary is None:
        return ManualResetCredits(
            provider="codex",
            source=source,
            updated_at=collected_at,
            freshness="live",
            error="Codex не вернул rateLimitResetCredits",
        )

    try:
        count = int(summary.get("availableCount"))
        if count < 0:
            raise ValueError
    except (TypeError, ValueError):
        return ManualResetCredits(
            provider="codex",
            source=source,
            updated_at=collected_at,
            freshness="live",
            error="Codex вернул некорректный availableCount",
        )

    raw_credits = summary.get("credits")
    items: list[ManualResetItem] = []
    if isinstance(raw_credits, list):
        for raw in raw_credits:
            if not isinstance(raw, dict):
                continue
            items.append(ManualResetItem(
                id=str(raw.get("id") or ""),
                provider="codex",
                expires_at=_codex_timestamp(raw.get("expiresAt")),
                granted_at=_codex_timestamp(raw.get("grantedAt")),
                status=str(raw.get("status") or "unknown"),
                reset_type=str(raw.get("resetType") or ""),
                title=str(raw.get("title") or ""),
                description=str(raw.get("description") or ""),
            ))
    credits = ManualResetCredits(
        provider="codex",
        state="none" if count == 0 else "available",
        available_count=count,
        items=items,
        source=source,
        updated_at=collected_at,
        freshness="live",
        details_complete=count == 0 or isinstance(raw_credits, list) and len(items) >= count,
    )
    credits.items = credits.sorted_items()
    return credits


def _codex_message_reader(stream: Any, messages: queue.Queue) -> None:
    try:
        for line in iter(stream.readline, ""):
            try:
                message = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(message, dict):
                messages.put(message)
    finally:
        messages.put(None)


def _codex_response(messages: queue.Queue, request_id: int, timeout: float) -> dict[str, Any]:
    deadline = time.monotonic() + timeout
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError("Codex app-server response timeout")
        try:
            message = messages.get(timeout=remaining)
        except queue.Empty as error:
            raise TimeoutError("Codex app-server response timeout") from error
        if message is None:
            raise RuntimeError("Codex app-server closed the response stream")
        if message.get("id") != request_id:
            continue
        failure = message.get("error")
        if isinstance(failure, dict):
            code = failure.get("code")
            detail = str(failure.get("message") or "unknown error")[:160]
            raise RuntimeError(f"Codex app-server error {code}: {detail}")
        return message


def _read_codex_rate_limits(timeout: float = _CODEX_APP_SERVER_TIMEOUT) -> dict[str, Any]:
    """Call the official local Codex app-server method and return its raw response.

    Кэш живёт несколько секунд и нужен только чтобы один цикл обновления не поднимал
    процесс app-server дважды: окна лимитов и кредиты сброса приходят одним ответом.
    """
    with _CODEX_RATE_LIMITS_LOCK:
        key = provider_config_key("codex")
        cached = _CODEX_RATE_LIMITS_CACHE.get("value") if _CODEX_RATE_LIMITS_CACHE.get("profile") == key else None
        stamp = _CODEX_RATE_LIMITS_CACHE.get("stamp")
        if cached is not None and stamp is not None and (time.monotonic() - stamp) < _CODEX_RATE_LIMITS_CACHE_TTL:
            return cached
    response = _read_codex_rate_limits_uncached(timeout)
    with _CODEX_RATE_LIMITS_LOCK:
        _CODEX_RATE_LIMITS_CACHE["value"] = response
        _CODEX_RATE_LIMITS_CACHE["stamp"] = time.monotonic()
        _CODEX_RATE_LIMITS_CACHE["profile"] = key
    return response


def reset_codex_rate_limits_cache() -> None:
    """Drop the short-lived app-server response cache."""
    with _CODEX_RATE_LIMITS_LOCK:
        _CODEX_RATE_LIMITS_CACHE.clear()


def _read_codex_rate_limits_uncached(timeout: float = _CODEX_APP_SERVER_TIMEOUT) -> dict[str, Any]:
    executable = codex_cli()
    if executable is None:
        raise RuntimeError("Codex CLI не найден")

    creationflags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
    process: subprocess.Popen[str] | None = None
    try:
        process = subprocess.Popen(
            [str(executable), "app-server", "--listen", "stdio://"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
            creationflags=creationflags,
            env=provider_environment("codex"),
        )
        if process.stdin is None or process.stdout is None:
            raise RuntimeError("Codex app-server pipes are unavailable")
        messages: queue.Queue = queue.Queue()
        reader = threading.Thread(
            target=_codex_message_reader,
            args=(process.stdout, messages),
            name="codex-rate-limits-reader",
            daemon=True,
        )
        reader.start()

        def send(message: dict[str, Any]) -> None:
            process.stdin.write(json.dumps(message, ensure_ascii=False, separators=(",", ":")) + "\n")
            process.stdin.flush()

        send({
            "id": 1,
            "method": "initialize",
            "params": {
                "clientInfo": {
                    "name": "ai_limits_monitor",
                    "title": "AI Limits Monitor",
                    "version": "1.8.7",
                },
                "capabilities": {"experimentalApi": False},
            },
        })
        _codex_response(messages, 1, timeout)
        send({"method": "initialized"})
        send({"id": 2, "method": "account/rateLimits/read", "params": {}})
        return _codex_response(messages, 2, timeout)
    finally:
        if process is not None and process.poll() is None:
            try:
                process.terminate()
                process.wait(timeout=2)
            except (OSError, subprocess.SubprocessError, TimeoutError):
                try:
                    process.kill()
                except OSError:
                    pass


def fetch_codex_manual_resets(timeout: float = _CODEX_APP_SERVER_TIMEOUT) -> ManualResetCredits:
    """Read reset credits through the official local Codex app-server protocol."""
    try:
        response = _read_codex_rate_limits(timeout)
    except (OSError, RuntimeError, subprocess.SubprocessError, TimeoutError) as error:
        return ManualResetCredits(provider="codex", source=_CODEX_APP_SERVER_SOURCE, error=str(error)[:240])
    return parse_codex_manual_resets(response)


def _codex_plan_label(plan_type: Any) -> str:
    text = str(plan_type or "").strip()
    if not text:
        return ""
    return _CODEX_PLAN_LABELS.get(text.casefold(), text.replace("_", " ").title())


def _codex_app_server_window(
    window_id: str,
    title: str,
    raw: Any,
    role: str,
) -> QuotaWindow | None:
    if not isinstance(raw, dict):
        return None
    used = _percent(raw.get("usedPercent"))
    if used is None:
        return None
    try:
        minutes = int(raw.get("windowDurationMins")) if raw.get("windowDurationMins") is not None else None
    except (TypeError, ValueError):
        minutes = None
    return QuotaWindow(
        id=window_id,
        title=title,
        used_percent=used,
        resets_at=_codex_timestamp(raw.get("resetsAt")),
        window_minutes=minutes,
        role=role,
    )


def parse_codex_app_server_windows(payload: Any) -> tuple[list[QuotaWindow], str]:
    """Normalize native ``account/rateLimits/read`` quota windows."""
    result = payload.get("result") if isinstance(payload, dict) else None
    if not isinstance(result, dict):
        result = payload if isinstance(payload, dict) else {}
    main_limits = result.get("rateLimits")
    by_id = result.get("rateLimitsByLimitId")
    if not isinstance(by_id, dict):
        by_id = {}
    if not isinstance(main_limits, dict):
        main_limits = by_id.get("codex") if isinstance(by_id.get("codex"), dict) else {}

    windows: list[QuotaWindow] = []
    primary = _codex_app_server_window("primary", "Недельная квота", main_limits.get("primary"), "primary")
    if primary is not None:
        windows.append(primary)
    secondary = _codex_app_server_window("secondary", "Дополнительный лимит", main_limits.get("secondary"), "secondary")
    if secondary is not None:
        windows.append(secondary)

    main_id = str(main_limits.get("limitId") or "codex")
    for limit_id, entry in sorted(by_id.items()):
        if not isinstance(entry, dict) or str(limit_id) == main_id:
            continue
        title = str(entry.get("limitName") or limit_id)
        window_id = re.sub(r"[^a-z0-9]+", "-", title.casefold()).strip("-") or str(limit_id)
        # Из дополнительного лимита берём самое длинное доступное окно.
        candidates = [entry.get("secondary"), entry.get("primary")]
        for raw in candidates:
            extra = _codex_app_server_window(window_id, title, raw, "extra")
            if extra is not None:
                windows.append(extra)
                break
    return windows, _codex_plan_label(main_limits.get("planType"))


def fetch_codex_quota_from_app_server(timeout: float = _CODEX_APP_SERVER_TIMEOUT) -> QuotaSnapshot:
    """Собрать квоту Codex напрямую из штатного локального протокола Codex CLI.

    Данные приходят из штатного локального протокола Codex, без внешних адаптеров.
    """
    snapshot = QuotaSnapshot(source=_CODEX_APP_SERVER_SOURCE, provider="codex")
    try:
        response = _read_codex_rate_limits(timeout)
    except (OSError, RuntimeError, subprocess.SubprocessError, TimeoutError) as error:
        snapshot.error = str(error)[:300]
        return snapshot
    windows, plan = parse_codex_app_server_windows(response)
    if not windows:
        snapshot.error = "Codex app-server не вернул окна лимитов"
        return snapshot
    snapshot.windows = windows
    snapshot.login_method = plan
    snapshot.updated_at = dt.datetime.now(dt.timezone.utc)
    main = _choose_main("codex", windows)
    if main is not None:
        snapshot.main_window_id = main.id
        snapshot.weekly = main.used_percent
        snapshot.weekly_resets_at = main.resets_at
    snapshot.main_label = "Недельная квота"
    spark = next((w for w in windows if "spark" in w.id.casefold()), None)
    if spark is not None:
        snapshot.fable_weekly = spark.used_percent
        snapshot.extra_label = spark.title
    snapshot.secondary_label = snapshot.extra_label or "Доп. квота"
    snapshot.manual_resets = parse_codex_manual_resets(response)
    return snapshot


def _choose_main(provider: str, windows: list[QuotaWindow]) -> QuotaWindow | None:
    usable = [window for window in windows if window.used_percent is not None and not window.informational]
    if not usable:
        return None
    if provider == "claude":
        return next((window for window in usable if window.window_minutes == 10080), usable[0])
    if provider == "codex":
        return next((window for window in usable if window.role == "primary"), usable[0])
    return usable[0]


def fetch_quota(provider: str = "claude") -> QuotaSnapshot:
    """Use each AI client's direct source; never invoke CodexBar."""
    provider = provider.casefold()
    if provider in {"claude", "codex", "grok", "antigravity"} and not provider_enabled(provider):
        return QuotaSnapshot(provider=provider, error="Источник выключен в настройках")
    if provider == "claude":
        return read_best_local_claude_quota()
    if provider == "codex":
        snapshot = fetch_codex_quota_from_app_server()
        if not snapshot.has_data():
            # app-server мог не успеть подняться: одна повторная попытка в этом же цикле.
            reset_codex_rate_limits_cache()
            snapshot = fetch_codex_quota_from_app_server()
        return snapshot
    if provider == "grok":
        return fetch_grok_quota()
    if provider == "antigravity":
        return fetch_antigravity_quota()
    raise ValueError(f"Неизвестный поставщик квоты: {provider}")


def _history_samples() -> list[tuple[int, float]]:
    path = plan_history_path()
    if path is None or not path.exists(): return []
    try: data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError): return []
    if not isinstance(data, dict): return []
    out=[]
    for sample in data.get("samples") or []:
        if not isinstance(sample, dict):
            continue
        usage=sample.get("u") or {}
        if not isinstance(usage, dict) or "sd" not in usage:
            continue
        try:
            stamp = int(sample.get("t", 0))
            if stamp < 100_000_000_000:
                stamp *= 1000
            out.append((stamp, float(usage["sd"])))
        except (TypeError, ValueError):
            pass
    return sorted(out)


def _legacy_window_value(row: dict[str, Any], provider: str, window_id: str) -> float | None:
    if provider == "claude" and window_id == "secondary": return row.get("weekly")
    if provider == "claude" and window_id == "primary": return row.get("five_hour")
    if provider == "codex" and window_id == "primary": return row.get("weekly")
    if provider == "antigravity" and window_id == "primary": return row.get("weekly")
    return None


def _quota_drop_kind(previous: float, value: float) -> str:
    """Classify a downward quota sample without mistaking small jitter for reset."""
    drop = previous - value
    if drop <= 4.0:
        return "jitter"
    if value <= 5.0 and drop >= 5.0:
        return "reset"
    return "unknown"


def _stored_quota_series(
    db: Database, provider: str, window_ids: set[str], start_ms: int, end_ms: int,
) -> dict[str, tuple[str, list[tuple[int, float]]]]:
    by_window: dict[str, dict[str, list[tuple[int, float]]]] = {}
    for row in db.quota_samples(provider=provider):
        ts = parse_iso(row.get("ts_utc"))
        if ts is None:
            continue
        stamp = int(ts.timestamp() * 1000)
        if stamp > end_ms:
            continue
        values = {}
        for window in row.get("windows") or []:
            if window.id in window_ids:
                values.setdefault(window.id, window.used_percent)
        source = str(row.get("source") or "").casefold() if provider == "claude" else ""
        for window_id in window_ids:
            value = values.get(window_id)
            if value is None:
                value = _legacy_window_value(row, provider, window_id)
            if value is not None:
                by_window.setdefault(window_id, {}).setdefault(source, []).append((stamp, float(value)))

    def coverage(item: tuple[str, list[tuple[int, float]]]) -> tuple[bool, bool, int, int]:
        source, values = item
        latest = max(stamp for stamp, _ in values)
        in_period = latest > start_ms
        has_baseline = any(stamp <= start_ms for stamp, _ in values)
        priority = {CLAUDE_MOD_SOURCE: 2, CLAUDE_STATUSLINE_SOURCE: 1}.get(source, 0)
        return in_period, has_baseline, priority if in_period else 0, latest

    return {window_id: max(series.items(), key=coverage) for window_id, series in by_window.items()}


def quota_windows_consumed(
    period: Period, db: Database | None, current_percents: dict[str, float | None], provider: str,
    *, current_source: str | None = None,
) -> dict[str, float]:
    """Рассчитывает расход по окнам, один раз читая историю провайдера из базы."""
    provider = provider.casefold()
    start_ms = int(period.start_utc.timestamp() * 1000)
    end_ms = int(period.end_utc.timestamp() * 1000)
    stored_window_ids = set(current_percents)
    series = {}
    if provider == "claude" and "secondary" in stored_window_ids:
        history = _history_samples()
        if history and (db is None or any(ts <= start_ms or start_ms < ts <= end_ms for ts, _ in history)):
            series["secondary"] = CLAUDE_DESKTOP_SOURCE, history
            stored_window_ids.remove("secondary")
    if db is not None and stored_window_ids:
        series.update(_stored_quota_series(db, provider, stored_window_ids, start_ms, end_ms))

    consumed_by_window = {}
    for window_id, current_percent in current_percents.items():
        if window_id not in series:
            continue
        source, samples = series[window_id]
        # Снимки разных источников Claude могут расходиться по времени и значениям.
        if provider == "claude" and current_source is not None and current_source.casefold() != source:
            current_percent = None
        consumed = _consume_quota_series(samples, start_ms, end_ms, current_percent)
        if consumed is not None:
            consumed_by_window[window_id] = consumed
    return consumed_by_window


def _consume_quota_series(
    samples: list[tuple[int, float]], start_ms: int, end_ms: int, current_percent: float | None,
) -> float | None:
    if not samples: return None
    merged={ts:value for ts,value in sorted(samples)}; ordered=sorted(merged.items())
    before=[s for s in ordered if s[0] <= start_ms]; inside=[s for s in ordered if start_ms < s[0] <= end_ms]
    baseline_known=bool(before)
    if before: previous=before[-1][1]
    elif inside: previous,inside=inside[0][1],inside[1:]
    else: return None
    consumed=0.0; observed=False
    for _,value in inside:
        if value < previous:
            drop_kind = _quota_drop_kind(previous, value)
            if drop_kind == "jitter":
                continue
            if drop_kind == "reset":
                previous=value; observed=True
                continue
            return None
        if value != previous: observed=True
        consumed += value-previous; previous=value
    if current_percent is not None:
        value=float(current_percent)
        if value < previous:
            drop_kind = _quota_drop_kind(previous, value)
            if drop_kind == "jitter":
                value=previous
            elif drop_kind == "reset":
                previous=value; observed=True
            else:
                return None
        if value != previous: observed=True
        consumed += value-previous
    if not baseline_known and not observed: return None
    return max(0.0,consumed)


def quota_window_consumed(
    period: Period, db: Database | None, current_percent: float | None, provider: str, window_id: str,
    *, current_source: str | None = None,
) -> float | None:
    return quota_windows_consumed(
        period, db, {window_id: current_percent}, provider, current_source=current_source,
    ).get(window_id)


def quota_consumed(period: Period, db: Database | None, current_percent: float | None, provider: str, window_id: str | None = None) -> float | None:
    provider=provider.casefold(); window_id=window_id or ("secondary" if provider == "claude" else "primary")
    return quota_window_consumed(period,db,current_percent,provider,window_id)


def weekly_consumed(
    period: Period, db: Database | None, current_weekly: float | None,
    *, current_source: str | None = None,
) -> float | None:
    return quota_window_consumed(period, db, current_weekly, "claude", "secondary", current_source=current_source)

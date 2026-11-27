"""Weekly Grok Build quota from the official Grok CLI.

Primary source: the user's own authenticated `grok agent stdio` process, asked
only for its billing config over the Agent Client Protocol (`initialize`, then
`_x.ai/billing`). No prompt is sent, no model is called; the CLI itself talks to
xAI with its own login, this module never reads credentials or calls any
endpoint. The same method is the first source of the CodexBar Grok provider.

Fallback: the Grok diagnostic log `unified.jsonl`, where the CLI writes the same
billing config only at start-up and after each finished answer. While Grok is
idle that record grows old, so it is only trusted for 20 minutes.

SOURCE keeps its historical name so the stored history stays one series.
"""
from __future__ import annotations

import datetime as dt
import json
import os
import queue
import subprocess
import threading
import time
from pathlib import Path
from typing import Any

from .models import QuotaSnapshot, QuotaWindow
from .provider_settings import provider_config_key, provider_environment, provider_executable, provider_profile

SOURCE = "grok:local-billing-log"
WINDOW_ID = "grok:weekly"
MAX_TAIL_BYTES = 2_097_152
LIVE_MAX_AGE = dt.timedelta(minutes=20)
CLI_INIT_TIMEOUT = 5.0
CLI_BILLING_TIMEOUT = 4.0
CLI_CACHE_SECONDS = 150.0
CLI_FAILURE_SECONDS = 120.0
CLI_INVALID_RETRY_SECONDS = 20.0
BILLING_METHOD = "_x.ai/billing"

_CLI_LOCK = threading.Lock()
_CLI_CACHE: dict[str, Any] = {}


def log_path() -> Path:
    return provider_profile("grok") / "logs" / "unified.jsonl"


def _timestamp(value: Any) -> dt.datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        timestamp = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if timestamp.tzinfo is None:
        return None
    return timestamp.astimezone(dt.timezone.utc)


def _tail(path: Path) -> list[str]:
    try:
        with path.open("rb") as file:
            file.seek(0, os.SEEK_END)
            end = file.tell()
            file.seek(max(0, end - MAX_TAIL_BYTES))
            raw = file.read(MAX_TAIL_BYTES)
    except OSError:
        return []
    if end > MAX_TAIL_BYTES:
        raw = raw.partition(b"\n")[2]
    return raw.decode("utf-8", errors="replace").splitlines()


def parse_billing_event(line: str, now: dt.datetime | None = None) -> tuple[dt.datetime, dt.datetime, float, dt.datetime] | None:
    """Return the valid (period start, reset, percent used, observation time).

    Reject malformed, other-plan, future and already-expired periods instead
    of displaying historical quota as today's value.
    """
    if "billing: fetched credits config" not in line:
        return None
    try:
        obj = json.loads(line)
        context = obj.get("ctx", {})
        if not isinstance(context, dict):
            return None
        return parse_billing_config(context.get("config", {}), _timestamp(obj.get("ts")), now)
    except (ValueError, TypeError, AttributeError, json.JSONDecodeError):
        return None


def parse_billing_config(
    config: Any,
    observed: dt.datetime | None,
    now: dt.datetime | None = None,
) -> tuple[dt.datetime, dt.datetime, float, dt.datetime] | None:
    """Validate the billing `config` object (log record or `_x.ai/billing` answer)."""
    try:
        if not isinstance(config, dict) or config.get("isUnifiedBillingUser") is not True:
            return None
        period = config.get("currentPeriod", {})
        if not isinstance(period, dict) or period.get("type") != "USAGE_PERIOD_TYPE_WEEKLY":
            return None
        start = _timestamp(period.get("start"))
        end = _timestamp(period.get("end"))
        percent = config.get("creditUsagePercent")
        if isinstance(percent, bool) or not isinstance(percent, (int, float)):
            return None
        usage = float(percent)
        if not (0.0 <= usage <= 100.0):
            return None
        current = (now or dt.datetime.now(dt.timezone.utc)).astimezone(dt.timezone.utc)
        if not start or not end or not observed:
            return None
        if not (start <= observed <= current + dt.timedelta(minutes=2)):
            return None
        if end <= current or end <= start:
            return None
        if not (dt.timedelta(days=6) <= end - start <= dt.timedelta(days=8)):
            return None
        return start, end, usage, observed
    except (ValueError, TypeError, AttributeError):
        return None


def _fill_snapshot(snapshot: QuotaSnapshot, event: tuple[dt.datetime, dt.datetime, float, dt.datetime]) -> None:
    start, end, percent, observed = event
    snapshot.windows = [QuotaWindow(
        id=WINDOW_ID,
        title="Grok · общий недельный лимит",
        used_percent=percent,
        resets_at=end,
        window_minutes=10080,
        role="group",
        details={"shared_pool": True, "period_start": start.isoformat(), "observed_at": observed.isoformat()},
    )]
    snapshot.weekly = percent
    snapshot.weekly_resets_at = end
    snapshot.main_window_id = WINDOW_ID
    snapshot.main_label = "Grok · неделя"
    snapshot.updated_at = observed


# --------------------------------------------------------------------------- #
# Grok CLI: `grok agent stdio`, method `_x.ai/billing`
# --------------------------------------------------------------------------- #
def grok_cli() -> Path | None:
    return provider_executable("grok")


def _cli_reader(stream: Any, messages: "queue.Queue[dict[str, Any] | None]") -> None:
    try:
        for line in stream:
            try:
                message = json.loads(line)
            except ValueError:
                continue
            if isinstance(message, dict):
                messages.put(message)
    finally:
        messages.put(None)


def _cli_response(messages: "queue.Queue[dict[str, Any] | None]", request_id: int, timeout: float) -> dict[str, Any]:
    deadline = time.monotonic() + timeout
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError("Grok Build не ответил вовремя")
        try:
            message = messages.get(timeout=remaining)
        except queue.Empty as error:
            raise TimeoutError("Grok Build не ответил вовремя") from error
        if message is None:
            raise RuntimeError("Grok Build завершился без ответа. Проверь установку клиента и вход в Grok.")
        if message.get("id") != request_id:
            continue
        failure = message.get("error")
        if isinstance(failure, dict):
            raise RuntimeError(f"Grok Build отказал в запросе лимита (код {failure.get('code')}): "
                               f"{str(failure.get('message') or 'без описания')[:160]}")
        return message


def read_billing_from_cli(init_timeout: float = CLI_INIT_TIMEOUT, billing_timeout: float = CLI_BILLING_TIMEOUT) -> dict[str, Any]:
    """Ask the local Grok CLI for its billing config. Only these two calls are made."""
    executable = grok_cli()
    if executable is None:
        raise RuntimeError("Grok CLI не найден")
    creationflags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
    process: subprocess.Popen[str] | None = None
    try:
        process = subprocess.Popen(
            [str(executable), "agent", "stdio"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
            creationflags=creationflags,
            env=provider_environment("grok"),
        )
        if process.stdin is None or process.stdout is None:
            raise RuntimeError("Не удалось связаться с запущенным Grok Build")
        messages: "queue.Queue[dict[str, Any] | None]" = queue.Queue()
        threading.Thread(target=_cli_reader, args=(process.stdout, messages), name="grok-billing-reader", daemon=True).start()

        def send(message: dict[str, Any]) -> None:
            process.stdin.write(json.dumps(message, ensure_ascii=False, separators=(",", ":")) + "\n")
            process.stdin.flush()

        send({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {
            "protocolVersion": 1,
            "clientCapabilities": {"fs": {"readTextFile": False, "writeTextFile": False}, "terminal": False},
        }})
        _cli_response(messages, 1, init_timeout)
        send({"jsonrpc": "2.0", "id": 2, "method": BILLING_METHOD, "params": {}})
        result = _cli_response(messages, 2, billing_timeout).get("result")
        if not isinstance(result, dict):
            raise RuntimeError("Grok CLI не вернул данные биллинга")
        return result
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


def _billing_cached(now: dt.datetime) -> tuple[dict[str, Any], dt.datetime]:
    """Сохранить только полный ответ вместе со временем его получения."""
    with _CLI_LOCK:
        monotonic_now = time.monotonic()
        key = provider_config_key("grok")
        if _CLI_CACHE.get("profile") != key:
            _CLI_CACHE.clear()
            _CLI_CACHE["profile"] = key
        cached, stamp = _CLI_CACHE.get("value"), _CLI_CACHE.get("stamp")
        if (cached is not None and stamp is not None and monotonic_now - stamp < CLI_CACHE_SECONDS
                and not _period_ended(cached, now)):
            return cached, _CLI_CACHE["observed"]
        failure = _CLI_CACHE.get("failure")
        if failure is not None and monotonic_now - failure[0] < failure[2]:
            raise RuntimeError(failure[1])
        retry_seconds = CLI_CACHE_SECONDS
        incomplete_streak = 0
        try:
            value = read_billing_from_cli()
            observed = now
            if parse_billing_config(value.get("config"), observed, observed) is None:
                config = value.get("config")
                period = config.get("currentPeriod") if isinstance(config, dict) else None
                start = _timestamp(period.get("start")) if isinstance(period, dict) else None
                end = _timestamp(period.get("end")) if isinstance(period, dict) else None
                incomplete = (isinstance(config, dict) and config.get("isUnifiedBillingUser") is True
                              and isinstance(period, dict) and period.get("type") == "USAGE_PERIOD_TYPE_WEEKLY"
                              and start is not None and end is not None and start <= observed < end
                              and dt.timedelta(days=6) <= end - start <= dt.timedelta(days=8)
                              and config.get("creditUsagePercent") is None)
                if incomplete:
                    # Неделя действует, а процента еще нет: повтор с нарастающей паузой, а не каждые 20 с.
                    incomplete_streak = _CLI_CACHE.get("incomplete", 0) + 1
                    retry_seconds = min(CLI_CACHE_SECONDS * 4,
                                        CLI_INVALID_RETRY_SECONDS * 2 ** (incomplete_streak - 1))
                    raise ValueError("Grok Build пока не сообщил процент недельного лимита")
                raise ValueError("Grok Build вернул недельный лимит в неожиданном виде")
        except ValueError as error:
            _CLI_CACHE.pop("value", None)
            _CLI_CACHE["incomplete"] = incomplete_streak
            _CLI_CACHE["failure"] = (time.monotonic(), str(error)[:240], retry_seconds)
            raise RuntimeError(str(error)) from error
        except (OSError, RuntimeError, subprocess.SubprocessError, TimeoutError) as error:
            _CLI_CACHE.pop("value", None)
            _CLI_CACHE["failure"] = (time.monotonic(), str(error)[:240], CLI_FAILURE_SECONDS)
            raise
        _CLI_CACHE.pop("failure", None)
        _CLI_CACHE.pop("incomplete", None)
        _CLI_CACHE["value"], _CLI_CACHE["stamp"], _CLI_CACHE["observed"] = value, time.monotonic(), observed
        return value, observed


def _period_ended(value: dict[str, Any], now: dt.datetime) -> bool:
    """Ответ CLI о неделе, которая уже закончилась, не показывается как текущий."""
    config = value.get("config") if isinstance(value, dict) else None
    period = config.get("currentPeriod") if isinstance(config, dict) else None
    end = _timestamp(period.get("end")) if isinstance(period, dict) else None
    return end is not None and end <= now


def reset_cli_cache() -> None:
    with _CLI_LOCK:
        _CLI_CACHE.clear()


def fetch_grok_quota_from_cli(now: dt.datetime | None = None) -> QuotaSnapshot:
    current = (now or dt.datetime.now(dt.timezone.utc)).astimezone(dt.timezone.utc)
    snapshot = QuotaSnapshot(provider="grok", source=SOURCE, login_method="Grok Build")
    try:
        result, observed = _billing_cached(current)
    except (OSError, RuntimeError, subprocess.SubprocessError, TimeoutError) as error:
        snapshot.error = str(error)[:240]
        return snapshot
    event = parse_billing_config(result.get("config"), observed, current)
    if event is None:
        snapshot.error = "Grok CLI вернул недельный лимит в неожиданном виде"
        return snapshot
    _fill_snapshot(snapshot, event)
    return snapshot


def fetch_grok_quota(path: Path | None = None, now: dt.datetime | None = None) -> QuotaSnapshot:
    """CLI answer when `path` is not given (production), else only the given log (tests, diagnostics)."""
    current = (now or dt.datetime.now(dt.timezone.utc)).astimezone(dt.timezone.utc)
    cli_error = ""
    live = None
    if path is None:
        live = fetch_grok_quota_from_cli(current)
        cli_error = live.error
    snapshot = QuotaSnapshot(provider="grok", source=SOURCE, login_method="Grok Build")
    latest: tuple[dt.datetime, dt.datetime, float, dt.datetime] | None = None
    for line in _tail(path or log_path()):
        event = parse_billing_event(line, current)
        if event is not None and (latest is None or event[3] >= latest[3]):
            latest = event
    if live is not None and live.has_data() and (latest is None or live.updated_at >= latest[3]):
        return live
    if latest is None:
        snapshot.error = cli_error or "Нет актуального недельного лимита Grok Build в локальном журнале"
        return snapshot
    _fill_snapshot(snapshot, latest)
    observed = latest[3]
    if current - observed > LIVE_MAX_AGE:
        snapshot.fallback_source = f"Запись журнала Grok устарела (от {observed.astimezone().strftime('%d.%m %H:%M')})"
        if cli_error:
            snapshot.fallback_source += f"; Grok CLI не ответил: {cli_error}"
    return snapshot

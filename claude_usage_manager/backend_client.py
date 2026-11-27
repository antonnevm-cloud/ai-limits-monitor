"""Чтение квот для существующего виджета; запуск backend сюда не входит."""
from __future__ import annotations

import json
import datetime as dt
import math
import urllib.error
import urllib.request
from dataclasses import replace

from .local_api import DEFAULT_PORT, profile_api_path
from .models import QuotaWindow
from .quota_monitor_model import MonitorData, build_compact_rows, build_rows, cached_states
from .utils import parse_iso


_DIRECT = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def _open(target, *, timeout):
    """Запрос к сборщику на 127.0.0.1 напрямую: системный прокси для него не нужен и может его сломать."""
    return _DIRECT.open(target, timeout=timeout)


def request_backend_refresh(*, base_url=f"http://127.0.0.1:{DEFAULT_PORT}"):
    try:
        request = urllib.request.Request(base_url + profile_api_path("/v1/refresh"), data=b"", method="POST")
        with _open(request, timeout=2) as response:
            return response.status == 202
    except (OSError, ValueError, urllib.error.URLError):
        return False


def existing_backend_available() -> bool:
    """Совместимость с ранее настроенным отдельным сборщиком."""
    from .backend import read_control
    from .utils import data_dir, data_store_id
    root = data_dir()
    if not read_control(root / "backend-control.json")["desired_running"]:
        return False
    try:
        url = f"http://127.0.0.1:{DEFAULT_PORT}" + profile_api_path("/health")
        with _open(url, timeout=0.6) as response:
            health = json.loads(response.read(4096))
        if not (isinstance(health, dict) and health.get("service") == "ai-limits-monitor"
                and health.get("schema_version") == 1 and health.get("mode") == "backend"
                and health.get("status") == "ok"):
            return False
        return health.get("connections_schema") == 1 and health.get("data_store") == data_store_id()
    except (OSError, ValueError):
        return False


def _grok_from_backend(state, provider_data):
    """Взять проверенный снимок сервиса с настоящим временем измерения."""
    if provider_data.get("source") != "grok:local-billing-log":
        return
    windows = provider_data.get("quota_windows")
    if not isinstance(windows, list) or len(windows) != 1 or not isinstance(windows[0], dict):
        return
    value = windows[0]
    used = value.get("used_percent")
    reset = parse_iso(value.get("resets_at"))
    observed = parse_iso(provider_data.get("updated_at"))
    now = dt.datetime.now(dt.timezone.utc)
    if (value.get("id") != "grok:weekly" or value.get("window_minutes") != 10080
            or isinstance(used, bool) or not isinstance(used, (int, float))
            or not math.isfinite(used) or not 0 <= used <= 100
            or reset is None or observed is None or reset.tzinfo is None or observed.tzinfo is None
            or not now < reset <= now + dt.timedelta(days=8)
            or observed > now + dt.timedelta(minutes=2) or observed >= reset):
        return
    window = QuotaWindow(id="grok:weekly", title=str(value.get("title") or "Grok"),
                         used_percent=used, resets_at=reset, window_minutes=10080,
                         role=str(value.get("role") or "group"),
                         details=value.get("details") if isinstance(value.get("details"), dict) else {})
    fallback = "Локальный снимок Grok устарел" if provider_data.get("source_state") == "local_fallback" else ""
    state.snapshot = replace(state.snapshot, source=provider_data["source"], windows=[window],
                             main_window_id=window.id, weekly=used, weekly_resets_at=reset,
                             updated_at=observed, fallback_source=fallback)


def read_monitor_data(db, *, base_url=f"http://127.0.0.1:{DEFAULT_PORT}"):
    connected = False
    providers = {}
    try:
        with _open(base_url + profile_api_path("/v1/snapshot"), timeout=2) as response:
            payload = json.load(response)
        if isinstance(payload, dict) and payload.get("schema_version") == 1 and isinstance(payload.get("providers"), dict):
            providers = payload["providers"]
            connected = True
    except (OSError, ValueError, urllib.error.URLError):
        pass
    states = cached_states(db)
    for provider, state in states.items():
        provider_data = providers.get(provider, {})
        if not isinstance(provider_data, dict):
            continue
        if provider == "grok":
            _grok_from_backend(state, provider_data)
        source = provider_data.get("source_state")
        # Do not mark a newer local Desktop observation as network-live just
        # because the backend independently saw an older Claude Code sample.
        state.live = (source == "live"
                      and provider_data.get("source") == state.snapshot.source
                      and not state.snapshot.fallback_source)
        if state.live and provider_data.get("degraded") is True:
            state.error = "Источник временно не отвечает, показаны последние живые данные."
    compact, selected = build_compact_rows(db, states)
    return MonitorData(states, build_rows(states), compact, selected), connected

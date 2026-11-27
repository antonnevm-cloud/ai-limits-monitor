"""Read-only loopback JSON API for the running AI Limits Monitor process."""
from __future__ import annotations

import datetime as dt
import json
import threading
from collections.abc import Callable, Mapping
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import urlsplit

from .models import ManualResetCredits, QuotaSnapshot

API_SCHEMA_VERSION = 1
LOOPBACK_HOST = "127.0.0.1"
DEFAULT_PORT = 28765


def _iso(value: dt.datetime | None) -> str | None:
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=dt.timezone.utc)
    return value.astimezone(dt.timezone.utc).isoformat().replace("+00:00", "Z")


def _remaining_seconds(value: dt.datetime | None, now: dt.datetime) -> int | None:
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=dt.timezone.utc)
    return max(0, int((value.astimezone(dt.timezone.utc) - now).total_seconds()))


def manual_resets_payload(
    credits: ManualResetCredits,
    now: dt.datetime,
    provider: str = "",
) -> dict[str, Any]:
    nearest = credits.nearest_expiry()
    owner = credits.provider or provider
    return {
        "state": credits.state,
        "available_count": credits.available_count,
        "provider": owner,
        "source": credits.source,
        "freshness": credits.freshness,
        "updated_at": _iso(credits.updated_at),
        "details_complete": credits.details_complete,
        "nearest_expiry": _iso(nearest),
        "nearest_remaining_seconds": _remaining_seconds(nearest, now),
        "items": [{
            "id": item.id or None,
            "provider": item.provider or owner,
            "status": item.status,
            "reset_type": item.reset_type or None,
            "title": item.title or None,
            "granted_at": _iso(item.granted_at),
            "expires_at": _iso(item.expires_at),
            "remaining_seconds": item.remaining_seconds(now),
        } for item in credits.sorted_items()],
    }


def _source_state(state: Any, snapshot: QuotaSnapshot) -> str:
    if bool(getattr(state, "live", False)):
        return "live"
    if snapshot.fallback_source:
        return "local_fallback"
    if snapshot.has_data() or snapshot.manual_resets.state != "unknown":
        return "cached"
    return "unavailable"


def provider_payload(provider: str, state: Any, now: dt.datetime) -> dict[str, Any]:
    snapshot = getattr(state, "snapshot", state)
    if not isinstance(snapshot, QuotaSnapshot):
        snapshot = QuotaSnapshot(provider=provider)
    source_state = _source_state(state, snapshot)
    return {
        "provider": provider,
        "source": snapshot.source,
        "source_state": source_state,
        # Источник сейчас не отвечает, но последние живые данные еще показываются как онлайн (льгота 15 минут).
        # Причина сбоя в API не передается, она пишется в backend.log.
        "degraded": bool(getattr(state, "live", False) and getattr(state, "error", "")),
        "freshness": source_state,
        "updated_at": _iso(snapshot.updated_at),
        "main_window_id": snapshot.main_window_id or None,
        "quota_windows": [{
            "id": window.id,
            "title": window.title,
            "role": window.role,
            "informational": window.informational,
            "details": window.details,
            "window_minutes": window.window_minutes,
            "used_percent": window.used_percent,
            "remaining_percent": None if window.used_percent is None else max(
                0.0, min(100.0, 100.0 - float(window.used_percent))
            ),
            "resets_at": _iso(window.resets_at),
        } for window in snapshot.windows],
        "manual_resets": manual_resets_payload(snapshot.manual_resets, now, provider),
    }


def snapshot_payload(
    states: Mapping[str, Any],
    *,
    now: dt.datetime | None = None,
) -> dict[str, Any]:
    generated_at = (now or dt.datetime.now(dt.timezone.utc)).astimezone(dt.timezone.utc)
    preferred = ("claude", "codex", "grok", "antigravity")
    ordered = [provider for provider in preferred if provider in states]
    ordered.extend(sorted(provider for provider in states if provider not in preferred))
    return {
        "schema_version": API_SCHEMA_VERSION,
        "generated_at": _iso(generated_at),
        "providers": {
            provider: provider_payload(provider, states[provider], generated_at)
            for provider in ordered
        },
    }


class _ApiServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True


class LocalApiServer:
    """Small in-process HTTP server bound strictly to IPv4 loopback."""

    def __init__(
        self,
        states: Callable[[], Mapping[str, Any]],
        refresh: Callable[[], None] | None = None,
        *,
        host: str = LOOPBACK_HOST,
        port: int = DEFAULT_PORT,
        health_details: Mapping[str, Any] | None = None,
    ) -> None:
        if host != LOOPBACK_HOST:
            raise ValueError("Local API may bind only to 127.0.0.1")
        self._states = states
        self._refresh = refresh
        self._host = host
        self._port = int(port)
        self._health_details = dict(health_details or {})
        self._server: _ApiServer | None = None
        self._thread: threading.Thread | None = None

    @property
    def address(self) -> tuple[str, int]:
        if self._server is None:
            return self._host, self._port
        host, port = self._server.server_address[:2]
        return str(host), int(port)

    @property
    def base_url(self) -> str:
        host, port = self.address
        return f"http://{host}:{port}"

    def start(self) -> None:
        if self._server is not None:
            return
        owner = self

        class Handler(BaseHTTPRequestHandler):
            server_version = "AILimitsMonitor/1"

            def _send(self, status: int, payload: dict[str, Any]) -> None:
                body = json.dumps(
                    payload,
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                ).encode("utf-8")
                self.send_response(status)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Cache-Control", "no-store")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_GET(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
                path = urlsplit(self.path).path
                if path == "/health":
                    self._send(200, {
                        "schema_version": API_SCHEMA_VERSION,
                        "service": "ai-limits-monitor",
                        "status": "ok",
                        **owner._health_details,
                    })
                    return
                if path == "/v1/snapshot":
                    self._send(200, snapshot_payload(dict(owner._states())))
                    return
                self._send(404, {"error": "not_found"})

            def do_POST(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
                path = urlsplit(self.path).path
                if path != "/v1/refresh":
                    self._send(404, {"error": "not_found"})
                    return
                if owner._refresh is None:
                    self._send(503, {"accepted": False})
                    return
                owner._refresh()
                self._send(202, {"accepted": True})

            def log_message(self, _format: str, *args: Any) -> None:
                return

        server = _ApiServer((self._host, self._port), Handler)
        thread = threading.Thread(
            target=server.serve_forever,
            name="ai-limits-loopback-api",
            daemon=True,
        )
        self._server = server
        self._thread = thread
        thread.start()

    def stop(self) -> None:
        server = self._server
        thread = self._thread
        self._server = None
        self._thread = None
        if server is None:
            return
        server.shutdown()
        server.server_close()
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=2)

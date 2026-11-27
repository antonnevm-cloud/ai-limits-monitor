"""Фоновый сбор квот и совместимый локальный API без Qt и окон."""
from __future__ import annotations

import ctypes
import datetime as dt
import json
import os
from pathlib import Path
import queue
import sys
import threading
import time

from .database import Database
from .models import QuotaSnapshot
from .provider_settings import load_connections
from .local_api import DEFAULT_PORT, LocalApiServer
from .quota_monitor_model import GRACE_PROVIDERS, LIVE_FAILURE_GRACE, PROVIDERS, ProviderState, cached_states, refresh_provider
from .utils import data_dir, data_store_id, get_logger, setup_logging


def read_control(path: Path) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8-sig"))
        if isinstance(value, dict) and isinstance(value.get("desired_running"), bool):
            return value
    except (OSError, ValueError):
        pass
    # Планировщик не должен запускать сервис без явного состояния управления.
    return {"desired_running": False, "auto_start": False}


class BackendService:
    def __init__(self, *, port=DEFAULT_PORT, db_factory=Database, refresh=None, provider_refresh=None, serve_http=True):
        self._db_factory = db_factory
        self._refresh = refresh
        self._provider_refresh = provider_refresh or refresh_provider
        db = db_factory()
        try:
            self._states = cached_states(db)
        finally:
            db.close()
        self._lock = threading.Lock()
        self._settings = load_connections()
        self._requested = threading.Event()
        self._provider_requests = {provider: queue.Queue(maxsize=1) for provider in PROVIDERS}
        self._stopped = threading.Event()
        self.api = (LocalApiServer(self.states, self.request_refresh, port=port,
                                   health_details={"mode": "backend", "pid": os.getpid(),
                                                   "connections_schema": 1, "data_store": data_store_id()})
                    if serve_http else None)
        self._thread = None
        self._provider_threads = []

    def states(self):
        settings = load_connections()
        with self._lock:
            return {
                name: state if self._settings[name] == settings[name] else ProviderState(
                    name, QuotaSnapshot(provider=name), False, "Настройки источника изменились",
                )
                for name, state in self._states.items()
            }

    def request_refresh(self):
        # Явное обновление (кнопка, сохранение подключений) спрашивает клиентов заново,
        # а не отдает запомненный ответ или запомненный отказ.
        from .grok_provider import reset_cli_cache
        from .quota import reset_codex_rate_limits_cache

        reset_cli_cache()
        reset_codex_rate_limits_cache()
        self._requested.set()
        for pending in self._provider_requests.values():
            self._wake_provider(pending)

    @staticmethod
    def _wake_provider(pending):
        try:
            pending.put_nowait(None)
        except queue.Full:
            pass

    def start(self):
        if self.api is not None:
            self.api.start()
        if self._refresh is not None:
            self._thread = threading.Thread(target=self._collect, name="ai-limits-collector", daemon=True)
            self._thread.start()
            return
        for provider in PROVIDERS:
            self._wake_provider(self._provider_requests[provider])
            thread = threading.Thread(target=self._collect_provider, args=(provider,),
                                      name=f"ai-limits-{provider}", daemon=True)
            self._provider_threads.append(thread)
            thread.start()

    def _collect_provider(self, provider):
        pending = self._provider_requests[provider]
        interval = 60
        db = None
        try:
            while not self._stopped.is_set():
                try:
                    pending.get(timeout=interval)
                except queue.Empty:
                    pass
                if self._stopped.is_set():
                    break
                try:
                    if db is None:
                        db = self._db_factory()
                    raw = db.get_setting("monitor_refresh_seconds", "60")
                    configured = max(15, min(300, int(raw or "60")))
                    interval = min(configured, {"claude": 5, "grok": 10}.get(provider, configured))
                    setting = load_connections()[provider]
                    state = self._provider_refresh(db, provider)
                    with self._lock:
                        self._states[provider] = state
                        self._settings[provider] = setting
                except Exception:
                    get_logger().exception("Ошибка обновления источника %s", provider)
                    with self._lock:
                        previous = self._states[provider]
                        stamp = previous.snapshot.updated_at
                        live = bool(previous.live and provider in GRACE_PROVIDERS and stamp is not None and stamp.tzinfo is not None
                                    and dt.datetime.now(dt.timezone.utc) - stamp <= LIVE_FAILURE_GRACE)
                        self._states[provider] = ProviderState(provider, previous.snapshot, live,
                                                               "Ошибка обновления источника")
        finally:
            if db is not None:
                db.close()

    def _collect(self):
        while not self._stopped.is_set():
            self._requested.clear()
            db = self._db_factory()
            interval = 60
            try:
                raw = db.get_setting("monitor_refresh_seconds", "60")
                interval = max(15, min(300, int(raw or "60")))
                states = self._refresh(db)
                with self._lock:
                    self._states = states
            except Exception:
                get_logger().exception("Ошибка фонового обновления квот")
            finally:
                db.close()
            self._requested.wait(interval)

    def stop(self):
        self._stopped.set()
        self._requested.set()
        for pending in self._provider_requests.values():
            self._wake_provider(pending)
        if self.api is not None:
            self.api.stop()
        # Клиент, который не отвечает, не держит выход: его процесс снимается сразу.
        from .process_utils import stop_children

        stop_children()
        # Общий срок на все потоки: поток, ждущий ответа клиента, не должен задерживать выход на 5 с каждый.
        deadline = time.monotonic() + 3
        for thread in [self._thread, *self._provider_threads]:
            if thread is not None:
                thread.join(timeout=max(0.0, deadline - time.monotonic()))


def main() -> int:
    """Отдельный сборщик для локальных интеграций. Запускается, только если разрешен в backend-control.json."""
    setup_logging(file_name="backend.log")
    control = data_dir() / "backend-control.json"
    if not read_control(control)["desired_running"]:
        get_logger().warning("Отдельный сборщик не запущен: в %s нет \"desired_running\": true. "
                             "Виджет собирает лимиты сам, отдельный сборщик нужен только локальным интеграциям.",
                             control)
        return 3
    mutex = None
    if sys.platform == "win32":
        kernel = ctypes.windll.kernel32
        kernel.CreateMutexW.restype = ctypes.c_void_p
        mutex = kernel.CreateMutexW(None, False, "Local\\AILimitsBackend28765")
        if not mutex:
            raise ctypes.WinError()
        if kernel.GetLastError() == 183:
            kernel.CloseHandle(ctypes.c_void_p(mutex))
            return 0
    service = None
    try:
        service = BackendService()
        service.start()
        get_logger().info("AI Limits Backend запущен, pid=%s", os.getpid())
        while read_control(control)["desired_running"]:
            service._stopped.wait(0.3)
        return 0
    except Exception:
        get_logger().exception("Ошибка AI Limits Backend")
        return 1
    finally:
        if service is not None:
            service.stop()
        if mutex:
            ctypes.windll.kernel32.CloseHandle(ctypes.c_void_p(mutex))

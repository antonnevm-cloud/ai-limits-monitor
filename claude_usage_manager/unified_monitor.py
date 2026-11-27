"""Существующий мини-виджет с отдельным фоновым сервисом квот."""
from __future__ import annotations

import ctypes
import logging
import sys
import traceback

from PySide6.QtCore import QRunnable, QTimer, Slot
from PySide6.QtGui import QAction, QFont
from PySide6.QtWidgets import QApplication

from .database import Database
from .backend import BackendService
from .integrated_analytics import IntegratedAnalyticsWindow
from .backend_client import existing_backend_available, read_monitor_data, request_backend_refresh
from .qt_gui import apply_windows_dark_frame
from .quota_monitor import MONITOR_NAME, MONITOR_VERSION, STYLE, MonitorWindow, WorkerSignals, _mutex
from .quota_monitor_detailed import MonitorWindow as DetailedLimitsWindow
from .quota_monitor_detailed import STYLE as DETAILED_STYLE
from .quota_monitor_model import MonitorData, build_compact_rows, build_rows
from .qt_theme import build_stylesheet
from .utils import database_path, get_logger, setup_logging
from .windows_ui import enable_per_monitor_dpi_awareness
from .widget_ipc import bring_to_front, open_show_event, request_show, show_requested

DISPLAY_REFRESH_MS = 5000


class BackendRefreshWorker(QRunnable):
    def __init__(self, db, request_update=False, collector=None):
        super().__init__()
        self.db = db
        self.request_update = request_update
        self.collector = collector
        self.signals = WorkerSignals()

    @Slot()
    def run(self):
        try:
            if self.collector is not None:
                if self.request_update:
                    self.collector.request_refresh()
                states = self.collector.states()
                compact, selected = build_compact_rows(self.db, states)
                self.signals.finished.emit(MonitorData(states, build_rows(states), compact, selected))
                return
            if self.request_update:
                request_backend_refresh()
            self.signals.finished.emit(read_monitor_data(self.db))
        except Exception:
            self.signals.failed.emit(traceback.format_exc())


class UnifiedMonitorWindow(MonitorWindow):
    """Compact limits window that owns detailed limits and AI Usage windows."""

    def __init__(self, db: Database, *, auto_refresh: bool = True, collector=None) -> None:
        self.collector = collector
        self._polling = auto_refresh
        self._analytics_window: IntegratedAnalyticsWindow | None = None
        self._limits_window: DetailedLimitsWindow | None = None
        self._limits_render_key = None
        self._shutdown_poll_scheduled = False
        self._refresh_requested = False
        super().__init__(db, auto_refresh=False, cache_scan=auto_refresh)
        self.timer.setInterval(DISPLAY_REFRESH_MS)
        if auto_refresh:
            QTimer.singleShot(250, self.refresh)
            QTimer.singleShot(5000, self.refresh)

    def open_settings(self) -> None:
        super().open_settings()
        self.timer.setInterval(DISPLAY_REFRESH_MS)

    def open_connections(self) -> None:
        super().open_connections()
        # Выбранные пути и отключение источников применяются внутри этого виджета.
        if self._polling and self.collector is None and not existing_backend_available():
            self.collector = BackendService(serve_http=False)
            self.collector.start()
            self.refresh_now()

    def refresh(self) -> None:
        if self.worker or self._quit:
            return
        worker = BackendRefreshWorker(self.db, request_update=self._refresh_requested, collector=self.collector)
        worker.generation = self._connection_generation
        self._refresh_requested = False
        worker.signals.finished.connect(self._done)
        worker.signals.failed.connect(self._failed)
        self.worker = worker
        self.pool.start(worker)

    def refresh_now(self) -> None:
        self._refresh_requested = True
        self.refresh()

    def _build_tray(self) -> None:
        super()._build_tray()
        menu = self.tray.contextMenu()
        if menu is None:
            return
        analytics_action = next(
            (action for action in menu.actions() if action.text() == "Детальная аналитика"),
            None,
        )
        limits_action = QAction("Подробно по лимитам", self)
        limits_action.triggered.connect(self.open_limits_detail)
        if analytics_action is not None:
            menu.insertAction(analytics_action, limits_action)
        else:
            menu.addAction(limits_action)

    def open_detail(self) -> None:
        """Open full usage analytics (threads, projects, tokens and models)."""
        try:
            if self._analytics_window is None:
                self._analytics_window = IntegratedAnalyticsWindow(self.db)
            self._analytics_window.open_and_refresh()
        except Exception:
            get_logger().error(
                "Failed to open detailed analytics: %s",
                traceback.format_exc(),
            )
            self.footer.setText("ошибка открытия аналитики")

    def _reload_limits_window(self, *, force: bool = False) -> None:
        if self._limits_window is None:
            return
        rows = build_rows(self.states)
        key = (rows, tuple((provider, state.live, state.error, state.snapshot.fallback_source,
                            state.snapshot.login_method, state.snapshot.manual_resets)
                           for provider, state in sorted(self.states.items())))
        self._limits_window.states = self.states
        self._limits_window.rows = rows
        if not force and key == self._limits_render_key:
            return
        self._limits_render_key = key
        window = self._limits_window
        scroll = getattr(window, "scroll", None)
        position = scroll.verticalScrollBar().value() if scroll is not None else None
        if hasattr(window, "setUpdatesEnabled"): window.setUpdatesEnabled(False)
        try:
            window._render()
            if position is not None: scroll.verticalScrollBar().setValue(position)
        finally:
            if hasattr(window, "setUpdatesEnabled"): window.setUpdatesEnabled(True)

    def open_limits_detail(self) -> None:
        """Open provider/model quota details without starting another process."""
        try:
            if self._limits_window is None:
                self._limits_window = DetailedLimitsWindow(self.db, auto_refresh=False)
                self._limits_window.timer.stop()
                self._limits_window.setStyleSheet(DETAILED_STYLE)
                apply_windows_dark_frame(self._limits_window)
            self._reload_limits_window(force=True)
            self._limits_window.showNormal()
            self._limits_window.raise_()
            self._limits_window.activateWindow()
        except Exception:
            get_logger().error(
                "Failed to open detailed limits: %s",
                traceback.format_exc(),
            )
            self.footer.setText("ошибка открытия подробных лимитов")

    def _done(self, data) -> None:
        if not self._quit and getattr(self.worker, "generation", self._connection_generation) != self._connection_generation:
            self.worker = None
            self.refresh_now()
            return
        connected = True
        if isinstance(data, tuple):
            data, connected = data
        super()._done(data)
        if self._quit:
            return
        if self._refresh_requested:
            self.refresh()
        if self._analytics_window is not None:
            self._analytics_window.reload_cached_quotas()
        self._reload_limits_window()
        if not connected:
            self.footer.setText("Нет связи · кэш")

    def _has_active_workers(self) -> bool:
        if self.worker is not None or getattr(self, "_cache_worker", None) is not None:
            return True
        for window in (self._analytics_window, self._limits_window):
            if window is None:
                continue
            if getattr(window, "worker", None) is not None:
                return True
            if getattr(window, "_worker", None) is not None:
                return True
            if getattr(window, "_quota_worker", None) is not None:
                return True
            if getattr(window, "_quota_queue", None):
                return True
        return False

    def _finish_quit(self) -> None:
        if self._has_active_workers():
            if not self._shutdown_poll_scheduled:
                self._shutdown_poll_scheduled = True
                QTimer.singleShot(50, self._finish_quit)
            return
        self._shutdown_poll_scheduled = False
        app = QApplication.instance()
        if app is not None:
            app.quit()

    def quit_monitor(self) -> None:
        if self._limits_window is not None:
            self._limits_window.close()
        if self._analytics_window is not None:
            self._analytics_window.close()
        super().quit_monitor()


def run_monitor() -> int:
    setup_logging(logging.INFO)
    dpi_mode = enable_per_monitor_dpi_awareness()
    mutex = _mutex()
    if mutex is None:
        request_show()
        return 0

    first_launch = not database_path().exists()
    db = Database()
    app = QApplication.instance() or QApplication(sys.argv)
    font = app.font()
    font.setHintingPreference(QFont.HintingPreference.PreferFullHinting)
    app.setFont(font)
    app.setApplicationName(MONITOR_NAME)
    app.setApplicationVersion(MONITOR_VERSION)
    app.setQuitOnLastWindowClosed(False)
    app.setStyle("Fusion")
    app.setStyleSheet(build_stylesheet() + STYLE)
    get_logger().info(
        "AI Limits Monitor unified mode, DPI awareness=%s, screen DPR=%.2f",
        dpi_mode,
        float(app.primaryScreen().devicePixelRatio() if app.primaryScreen() else 1.0),
    )
    collector = None if existing_backend_available() else BackendService(serve_http=False)
    window = UnifiedMonitorWindow(db, collector=collector)
    if collector is not None:
        collector.start()
    bring_to_front(window)
    show_event = open_show_event()
    show_timer = QTimer(window)
    show_timer.setInterval(150)
    show_timer.timeout.connect(lambda: bring_to_front(window) if show_requested(show_event) else None)
    if show_event:
        show_timer.start()
    if first_launch:
        QTimer.singleShot(0, window.open_connections)
    try:
        return app.exec()
    finally:
        if window.collector is not None:
            window.collector.stop()
        if show_event:
            ctypes.windll.kernel32.CloseHandle(ctypes.c_void_p(show_event))
        db.close()
        if sys.platform == "win32" and mutex not in (None, True):
            ctypes.windll.kernel32.CloseHandle(ctypes.c_void_p(mutex))


def main() -> int:
    try:
        return run_monitor()
    except Exception:
        get_logger().error(
            "AI Limits Monitor unified fatal: %s",
            traceback.format_exc(),
        )
        return 1

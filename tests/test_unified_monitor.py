import os
import pathlib

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from claude_usage_manager.database import Database
import claude_usage_manager.unified_monitor as unified_monitor


def qapp():
    return QApplication.instance() or QApplication([])


def stop_window(window):
    window.timer.stop()
    window._quit = True
    if hasattr(window, "tray"):
        window.tray.hide()
    window.close()


def test_manual_refresh_requests_backend_and_display_poll_is_fast(tmp_path, monkeypatch):
    app = qapp()
    db = Database(tmp_path / "ui.sqlite")
    calls = []
    monkeypatch.setattr(unified_monitor, "request_backend_refresh", lambda: calls.append("request"))
    monkeypatch.setattr(unified_monitor, "read_monitor_data", lambda _db: calls.append("read") or (None, True))
    worker = unified_monitor.BackendRefreshWorker(db, request_update=True)
    worker.run()
    assert calls == ["request", "read"]
    window = unified_monitor.UnifiedMonitorWindow(db, auto_refresh=False)
    assert window.timer.interval() == unified_monitor.DISPLAY_REFRESH_MS
    stop_window(window)
    app.processEvents()
    db.close()


def test_unified_monitor_reuses_one_analytics_window(tmp_path, monkeypatch):
    app = qapp()
    db = Database(tmp_path / "ui.sqlite")
    calls = {"created": 0, "opened": 0, "reloaded": 0, "closed": 0}

    class FakeAnalytics:
        def __init__(self, incoming_db):
            assert incoming_db is db
            calls["created"] += 1

        def open_and_refresh(self):
            calls["opened"] += 1

        def reload_cached_quotas(self):
            calls["reloaded"] += 1

        def close(self):
            calls["closed"] += 1

    monkeypatch.setattr(unified_monitor, "IntegratedAnalyticsWindow", FakeAnalytics)
    window = unified_monitor.UnifiedMonitorWindow(db, auto_refresh=False)
    window.timer.stop()

    window.open_detail()
    first = window._analytics_window
    window.open_detail()
    assert window._analytics_window is first
    assert calls["created"] == 1
    assert calls["opened"] == 2

    window._done(window.data)
    assert calls["reloaded"] == 1

    window.quit_monitor()
    app.processEvents()
    assert calls["closed"] == 1
    db.close()


def test_tray_has_separate_limits_and_usage_actions(tmp_path):
    app = qapp()
    db = Database(tmp_path / "ui.sqlite")
    window = unified_monitor.UnifiedMonitorWindow(db, auto_refresh=False)
    window.timer.stop()
    texts = [action.text() for action in window.tray.contextMenu().actions()]
    assert "Подробно по лимитам" in texts
    assert "Детальная аналитика" in texts
    assert texts.index("Подробно по лимитам") < texts.index("Детальная аналитика")
    stop_window(window)
    app.processEvents()
    db.close()


def test_unified_monitor_reuses_one_limits_window(tmp_path, monkeypatch):
    app = qapp()
    db = Database(tmp_path / "ui.sqlite")
    calls = {"created": 0, "stopped": 0, "rendered": 0, "shown": 0, "closed": 0}

    class FakeTimer:
        def stop(self):
            calls["stopped"] += 1

    class FakeLimits:
        def __init__(self, incoming_db, *, auto_refresh=True):
            assert incoming_db is db
            assert auto_refresh is False
            calls["created"] += 1
            self.timer = FakeTimer()
            self.states = {}
            self.rows = {}

        def setStyleSheet(self, _style):
            pass

        def _render(self):
            calls["rendered"] += 1

        def showNormal(self):
            calls["shown"] += 1

        def raise_(self):
            pass

        def activateWindow(self):
            pass

        def close(self):
            calls["closed"] += 1

    monkeypatch.setattr(unified_monitor, "DetailedLimitsWindow", FakeLimits)
    monkeypatch.setattr(unified_monitor, "apply_windows_dark_frame", lambda _window: None)
    window = unified_monitor.UnifiedMonitorWindow(db, auto_refresh=False)
    window.timer.stop()

    window.open_limits_detail()
    first = window._limits_window
    assert first.states is window.states
    window.open_limits_detail()
    assert window._limits_window is first
    assert calls["created"] == 1
    assert calls["stopped"] == 1
    assert calls["shown"] == 2
    assert calls["rendered"] >= 2

    window.quit_monitor()
    app.processEvents()
    assert calls["closed"] == 1
    db.close()


def test_unchanged_limits_do_not_rebuild_details_on_each_poll(tmp_path):
    app = qapp()
    db = Database(tmp_path / "ui.sqlite")
    window = unified_monitor.UnifiedMonitorWindow(db, auto_refresh=False)
    window.timer.stop()
    class Details:
        states = None
        rows = None
        calls = 0
        def _render(self): self.calls += 1
        def close(self): pass
    details = Details()
    window._limits_window = details
    window._reload_limits_window()
    window._reload_limits_window()
    window._done(window.data)
    assert details.calls == 1
    window.quit_monitor(); app.processEvents(); db.close()


def test_monitor_entry_point_uses_unified_process():
    source = pathlib.Path("monitor.py").read_text(encoding="utf-8")
    assert "claude_usage_manager.unified_monitor import main" in source


def test_detailed_style_is_scoped_to_details_window():
    source = pathlib.Path("claude_usage_manager/unified_monitor.py").read_text(encoding="utf-8")
    assert "self._limits_window.setStyleSheet(DETAILED_STYLE)" in source
    assert "app.setStyleSheet(build_stylesheet() + STYLE + DETAILED_STYLE)" not in source
    assert "app.setStyleSheet(build_stylesheet() + STYLE)" in source


def test_detailed_limits_window_has_clean_cyrillic():
    source = pathlib.Path("claude_usage_manager/quota_monitor_detailed.py").read_text(encoding="utf-8")
    assert "class MonitorWindow" in source
    for expected in (
        "Обновить",
        "Настройки",
        "Агент / модель",
        "Короткий / день",
        "Долгий / неделя",
        "сброс не указан",
    ):
        assert expected in source
    assert "�" not in source
    assert not any(0x2500 <= ord(char) <= 0x259F for char in source)


def test_installer_creates_only_unified_application_shortcut():
    source = pathlib.Path("install.ps1").read_text(encoding="utf-8")
    assert "$AppScript" not in source
    assert "AI Limits Monitor.lnk') -ScriptPath $MonitorScript" in source
    assert "Claude Usage Manager.lnk') -ScriptPath" not in source

def test_root_launchers_point_to_unified_monitor():
    normal = pathlib.Path("Run AI Limits Monitor.cmd")
    debug = pathlib.Path("Run AI Limits Monitor Debug.cmd")
    assert normal.exists() and debug.exists()
    assert "monitor.py" in normal.read_text(encoding="utf-8-sig")
    assert "monitor.py" in debug.read_text(encoding="utf-8-sig")
    assert not pathlib.Path("Run Claude Usage Manager.cmd").exists()
    assert not pathlib.Path("Run Claude Usage Manager Debug.cmd").exists()

def test_legacy_runtime_surface_is_removed():
    for obsolete in (
        "app.py",
        "monitor_detail.py",
        "legacy_reference.py",
        "DESIGN_REVIEW.md",
        "BUILD_REPORT.md",
        "claude_usage_manager/gui.py",
        "claude_usage_manager/tray.py",
    ):
        assert not pathlib.Path(obsolete).exists()
    requirements = pathlib.Path("requirements.txt").read_text(encoding="utf-8").casefold()
    assert "pystray" not in requirements
    windows_ui = pathlib.Path("claude_usage_manager/windows_ui.py").read_text(encoding="utf-8").casefold()
    assert "tkinter" not in windows_ui

def test_codex_native_app_server_is_live_in_unified_widget(tmp_path, monkeypatch):
    import io
    import json
    from claude_usage_manager import backend_client
    from claude_usage_manager.models import QuotaSnapshot, QuotaWindow

    db = Database(tmp_path / "usage.db")
    snapshot = QuotaSnapshot(
        provider="codex", source="codex-app-server:account/rateLimits/read",
        main_window_id="primary",
        windows=[QuotaWindow("primary", "Weekly", 3.0, window_minutes=10080, role="primary")],
    )
    assert db.record_quota(snapshot)
    response = {"schema_version": 1, "providers": {
        "codex": {"source_state": "live", "source": snapshot.source},
    }}
    monkeypatch.setattr(
        backend_client.urllib.request, "urlopen",
        lambda *_args, **_kwargs: io.BytesIO(json.dumps(response).encode("utf-8")),
    )
    result, connected = backend_client.read_monitor_data(db)
    assert connected
    assert result.states["codex"].live
    assert result.states["codex"].snapshot.source == snapshot.source
    assert result.states["codex"].snapshot.weekly == 3.0
    db.close()

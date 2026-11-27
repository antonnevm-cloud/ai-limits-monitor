"""Виджет и окно подробных лимитов."""
import dataclasses
import json
import os
import pathlib

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QPoint, QRect
from PySide6.QtWidgets import QApplication, QWidget

from claude_usage_manager import APP_VERSION, provider_settings
from claude_usage_manager import quota_monitor_detailed as detailed
from claude_usage_manager.cache_threads_qt import SpinnerClock, StateDot, explicitly_hidden
from claude_usage_manager.database import Database
from claude_usage_manager.quota_columns import remaining_color, remaining_level
from claude_usage_manager.quota_monitor import ANCHOR_MARGIN, ANCHOR_STATE, MonitorWindow
from claude_usage_manager.thread_activity import ACTIVITY_WORKING


def qapp():
    return QApplication.instance() or QApplication([])


def stop(window):
    window.timer.stop()
    window._quit = True
    if hasattr(window, "tray"):
        window.tray.hide()
    window.close()


def test_color_follows_the_shown_number():
    # 19.6 показывается как "20%" и окрашивается как 20, а не как 19.
    assert remaining_level(19.6) == remaining_level(20) == "warning"
    assert remaining_level(19.4) == "danger"
    assert remaining_level(49.7) == "ok"
    assert remaining_color(None) != remaining_color(80)


def test_detailed_gauge_uses_widget_thresholds():
    qapp()
    gauge = detailed.Gauge()
    cell = type("Cell", (), {"remaining": 46.0, "label": "Неделя", "resets_at": None})()
    gauge.set_cell(cell)
    # Раньше окно лимитов считало 46% зеленым (порог 45), а виджет - желтым (порог 50).
    assert detailed.GAUGE_COLORS["warning"] in gauge.bar.styleSheet()


def test_limits_window_keeps_its_own_settings(tmp_path, monkeypatch):
    qapp()
    db = Database(tmp_path / "ui.sqlite")
    db.set_setting("monitor_always_on_top", "1")
    db.set_setting("monitor_refresh_seconds", "120")
    window = detailed.MonitorWindow(db, auto_refresh=False, refresh_handler=lambda: None)
    try:
        assert window.topmost is False  # большое окно по умолчанию не висит поверх всех
        assert window.seconds == 120  # подпись опроса берется у виджета

        class Dialog:
            DialogCode = detailed.QDialog.DialogCode

            def __init__(self, rows, visibility, seconds, topmost, parent):
                assert seconds is None  # интервала виджета в этом окне нет

            def setStyleSheet(self, _style):
                pass

            def exec(self):
                return detailed.QDialog.DialogCode.Accepted

            def result_settings(self):
                return None, True, {}

        monkeypatch.setattr(detailed, "SettingsDialog", Dialog)
        window.open_settings()
        assert db.get_setting(detailed.TOPMOST_SETTING) == "1"
        assert db.get_setting("monitor_always_on_top") == "1"
        assert db.get_setting("monitor_refresh_seconds") == "120"
    finally:
        window.close()
        db.close()


def test_limits_refresh_goes_through_the_widget(tmp_path):
    qapp()
    db = Database(tmp_path / "ui.sqlite")
    calls = []
    window = detailed.MonitorWindow(db, auto_refresh=False, refresh_handler=lambda: calls.append(True))
    try:
        window.refresh()
        assert calls == [True]
        assert window.worker is None  # отдельного опроса мимо сборщика нет
    finally:
        window.close()
        db.close()


def test_disabled_source_is_not_shown_in_limits_window(tmp_path):
    qapp()
    connections = provider_settings.load_connections()
    connections["grok"] = dataclasses.replace(connections["grok"], enabled=False)
    provider_settings.save_connections(connections)
    db = Database(tmp_path / "ui.sqlite")
    window = detailed.MonitorWindow(db, auto_refresh=False)
    try:
        titles = [section.findChild(detailed.QLabel, "ProviderTitle").text()
                  for section in window.content.findChildren(detailed.ProviderSection)]
        assert detailed.PROVIDER_LABELS["grok"] not in titles
        assert "/3" in window.status.text()
        assert window.windowTitle().endswith(APP_VERSION)
    finally:
        window.close()
        db.close()


def test_first_launch_puts_widget_in_bottom_right_corner(tmp_path):
    app = qapp()
    db = Database(tmp_path / "ui.sqlite")
    window = MonitorWindow(db, auto_refresh=False, cache_scan=False)
    try:
        area = app.primaryScreen().availableGeometry()
        geometry = window.geometry()
        assert geometry.right() == area.right() - ANCHOR_MARGIN
        assert geometry.bottom() == area.bottom() - ANCHOR_MARGIN
        window.move(geometry.left() - 40, geometry.top() - 30)
        window._save_geometry()
        saved = json.loads(db.get_state(ANCHOR_STATE))
    finally:
        stop(window)
    again = MonitorWindow(db, auto_refresh=False, cache_scan=False)
    try:
        assert (again.geometry().right(), again.geometry().bottom()) == (saved["right"], saved["bottom"])
    finally:
        stop(again)
        db.close()


def test_anchor_outside_screens_falls_back_to_corner(tmp_path):
    app = qapp()
    db = Database(tmp_path / "ui.sqlite")
    db.set_state(ANCHOR_STATE, json.dumps({"right": -50000, "bottom": -50000}))
    window = MonitorWindow(db, auto_refresh=False, cache_scan=False)
    try:
        area = app.primaryScreen().availableGeometry()
        assert window.geometry().right() == area.right() - ANCHOR_MARGIN
    finally:
        stop(window)
        db.close()


def test_view_panel_of_a_wide_widget_stays_on_screen(tmp_path):
    app = qapp()
    db = Database(tmp_path / "ui.sqlite")
    window = MonitorWindow(db, auto_refresh=False, cache_scan=False)
    try:
        area = app.primaryScreen().availableGeometry()
        window.show()
        window.setGeometry(QRect(area.left(), area.top(), area.width(), 300))
        window.toggle_drawer()
        drawer = window.drawer.frameGeometry()
        assert area.left() <= drawer.left() and drawer.right() <= area.right()
    finally:
        stop(window)
        db.close()


def test_hidden_widget_does_not_scan_or_animate(tmp_path, monkeypatch):
    qapp()
    db = Database(tmp_path / "ui.sqlite")
    window = MonitorWindow(db, auto_refresh=False, cache_scan=False)
    started = []
    monkeypatch.setattr(window.pool, "start", lambda worker: started.append(worker))
    try:
        window._cache_scan = True
        window.show()
        window.hide_to_tray()
        window._start_cache_scan()
        assert started == []
        dot = StateDot(window.centralWidget())
        dot.set_state(ACTIVITY_WORKING)
        assert explicitly_hidden(dot)
        assert not SpinnerClock.instance().has(dot)
        resumed = []
        monkeypatch.setattr(window, "_resume_scans", lambda: resumed.append(True))
        window.show()
        QApplication.processEvents()
        assert resumed == [True]
        assert SpinnerClock.instance().has(dot)
    finally:
        stop(window)
        db.close()


def test_fresh_widget_in_visible_window_still_animates():
    qapp()
    top = QWidget()
    top.show()
    dot = StateDot(top)
    assert not explicitly_hidden(dot)
    top.close()


def test_activation_does_not_inject_input():
    source = pathlib.Path("claude_usage_manager/widget_ipc.py").read_text(encoding="utf-8")
    # Ни нажатия ALT в чужое окно, ни подключения к очереди ввода активной программы.
    assert "SendInput" not in source and "AttachThreadInput" not in source
    assert "AllowSetForegroundWindow" in source
